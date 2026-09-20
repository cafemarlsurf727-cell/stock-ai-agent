import os
import sys
import time
import json
import re
import random
import argparse
import requests
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

def check_env_vars(require_line=True):
    """GitHub Secrets等から必要な環境変数が渡されているか確認します"""
    required_vars = ["GEMINI_API_KEY"]
    if require_line:
        required_vars += ["LINE_CHANNEL_ACCESS_TOKEN", "LINE_USER_ID"]
    missing = [var for var in required_vars if not os.environ.get(var)]

    if missing:
        print(f"【エラー】以下の環境変数が設定されていません: {', '.join(missing)}")
        sys.exit(1)

def is_market_holiday(date_obj):
    """土日・祝日・年末年始をチェックします"""
    if date_obj.weekday() >= 5:
        return True
    if (date_obj.month == 12 and date_obj.day >= 31) or (date_obj.month == 1 and date_obj.day <= 3):
        return True
    try:
        import jpholiday
        if jpholiday.is_holiday(date_obj.date() if hasattr(date_obj, "date") else date_obj):
            return True
    except ImportError:
        print("【警告】jpholiday が未インストールのため祝日判定をスキップします。")
    return False

# 銘柄名として採用しない汎用ラベル（Yahoo!ファイナンス側の付随リンクのテキスト）
GENERIC_LABELS = {
    "掲示板", "チャート", "ニュース", "時系列", "業績", "会社情報", "適時開示",
    "株主優待", "決算", "IR", "指標", "関連ニュース", "詳細", "取引", "予想",
}

def fetch_new_high_stocks():
    """Yahoo!ファイナンスから年初来高値銘柄データと銘柄名辞書を取得します"""
    url = "https://finance.yahoo.co.jp/stocks/ranking/yearToDateHigh?market=all"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        response.encoding = 'utf-8'
    except Exception as e:
        print(f"【エラー】Yahoo!ファイナンスからのデータ取得に失敗しました: {e}")
        sys.exit(1)

    soup = BeautifulSoup(response.text, "html.parser")

    tables = soup.find_all("table")
    target_table = None
    max_rows = 0
    for t in tables:
        rows_count = len(t.find_all("tr"))
        if rows_count > max_rows:
            max_rows = rows_count
            target_table = t

    if not target_table:
        print("【警告】新高値更新銘柄のテーブル要素が見つかりませんでした。")
        return "本日新高値更新銘柄のデータ取得に失敗しました。", {}, 0

    # 銘柄名辞書は「特定したテーブル内」のリンクだけから構築する。
    # ページ全体を対象にすると、サイドバーや「掲示板」リンクなど
    # 同じ /quote/CODE.T を指す無関係なリンクまで拾って上書きしてしまうため。
    stock_dict = {}
    for a in target_table.find_all("a", href=True):
        m = re.search(r'/quote/([0-9A-Za-z]{4})\.T', a['href'], re.IGNORECASE)
        if not m:
            continue
        code = m.group(1).upper()
        text = a.get_text(strip=True)
        clean_name = re.sub(r'^[0-9A-Za-z]{4}\s*', '', text)
        clean_name = re.sub(r'\s*[0-9A-Za-z]{4}$', '', clean_name)
        clean_name = clean_name.replace('(株)', '').replace('（株）', '').strip()

        if not clean_name or len(clean_name) < 2 or clean_name.isdigit():
            continue
        if clean_name in GENERIC_LABELS:
            # 「掲示板」等のラベルは社名として採用しない
            continue

        existing = stock_dict.get(code)
        # 未登録、既存が汎用ラベル相当、またはより長く情報量の多い文字列の場合のみ採用する
        if existing is None or existing in GENERIC_LABELS or len(clean_name) > len(existing):
            stock_dict[code] = clean_name

    rows = target_table.find_all("tr")
    formatted_data = []

    for row in rows:
        cols = [col.text.strip() for col in row.find_all(["th", "td"])]
        if cols:
            clean_cols = [" ".join(c.split()) for c in cols]
            formatted_data.append(" | ".join(clean_cols))

    if len(formatted_data) <= 1:
        return "本日新高値更新銘柄のデータが見つかりませんでした。", stock_dict, 0

    data_rows = formatted_data[:40]
    scraped_count = max(len(data_rows) - 1, 0)
    return "\n".join(data_rows), stock_dict, scraped_count

def extract_grounding_urls(response):
    """検索グラウンディングで実際に参照されたURLだけを取り出す"""
    urls = []
    try:
        for cand in response.candidates or []:
            meta = getattr(cand, "grounding_metadata", None)
            for chunk in getattr(meta, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                uri = getattr(web, "uri", None)
                if uri and uri not in urls:
                    urls.append(uri)
    except Exception:
        pass
    return urls

def _is_quota_exhausted(e):
    """時間経過では回復しない、利用枠(クォータ)そのものの枯渇かどうかを判定する。
    通常の一時的なレート制限（分単位）とは異なり、課金設定や日次上限の変更が必要なケース。"""
    msg = str(e)
    return "RESOURCE_EXHAUSTED" in msg and ("quota" in msg.lower() or "billing" in msg.lower())

def call_gemini_with_retry(client, model, contents_list, config=None, max_retries=6):
    """指数バックオフ＋ジッタ付きリトライ。429/5xx系のみ再試行し、それ以外は即座に失敗させる。
    429/503（レート制限・過負荷）は特に長めの指数バックオフで粘るが、
    クォータそのものの枯渇（課金・プラン起因）は待っても無意味なので即座に失敗させる。"""
    for attempt in range(1, max_retries + 1):
        try:
            if config:
                return client.models.generate_content(model=model, contents=contents_list, config=config)
            return client.models.generate_content(model=model, contents=contents_list)
        except genai_errors.APIError as e:
            code = getattr(e, "code", None)

            if code == 429 and _is_quota_exhausted(e):
                print("【エラー】Gemini APIの利用枠（クォータ）を使い切っています。時間経過を待つリトライは意味がないため、ここで処理を中断します。")
                print("　→ https://ai.dev/rate-limit で使用状況を確認し、無料枠の上限か課金設定の要否を確認してください。")
                raise

            retryable = code in (429, 500, 503, 504)
            if not retryable or attempt == max_retries:
                print(f"【エラー】Gemini API 失敗（リトライ対象外、または上限到達）: {e}")
                raise
            if code in (429, 503):
                delay = min(180, 20 * (2 ** (attempt - 1))) + random.uniform(0, 5)
            else:
                delay = attempt * 10
            print(f"【警告】Gemini API 試行 ({attempt}/{max_retries}) 失敗（HTTP {code}）。{delay:.0f}秒待機して再試行します。")
            time.sleep(delay)
        except Exception as e:
            if attempt == max_retries:
                print(f"【エラー】Gemini API 呼び出しで想定外の例外が発生しました: {e}")
                raise
            delay = attempt * 10
            print(f"【警告】Gemini API 試行 ({attempt}/{max_retries}) 失敗: {e}。{delay}秒待機して再試行します。")
            time.sleep(delay)

def analyze_stocks_multi_stage(stock_data_text, scraped_count):
    """Stage 1, 2, 3 を経由してマルチステップで高精度スクリーニングを行います"""
    client = genai.Client()
    model_triage = os.environ.get("MODEL_TRIAGE", "gemini-3.7-flash")
    model_research = os.environ.get("MODEL_RESEARCH", "gemini-3.8-flash")
    model_structure = os.environ.get("MODEL_STRUCTURE", "gemini-3.7-flash")

    print("--> [Stage 1] 検索なしで候補銘柄を8選に絞り込み中...")
    stage1_prompt = f"""
あなたはプロの株式アナリストです。以下の新高値更新銘柄データから、「新高値ブレイク投資法」の観点（上場来高値・2年以上ブレイク、上値の軽さ、出来高急増、業績期待）に基づき、特に有望な8銘柄を選定してください。
銘柄コードは英字混在4桁（例: 130A, 219A, 9A76）の場合があります。数字だけに丸めたり、末尾の英字を省略したりせず、必ず元の表記のまま正確に引用してください。
【データ】
{stock_data_text}
"""
    res1 = call_gemini_with_retry(client, model_triage, [stage1_prompt])
    stage1_candidates = res1.text

    print("--> [Stage 2] Google検索グラウンディングで決算・材料の裏取り中...")
    stage2_prompt = f"""
以下のStage 1で選定された候補銘柄について、Google検索ツールを活用して直近の決算数値（売上・経常利益の前年同期比）、新高値突破の原動力、TOBや非公開化の予定がないか等の事実確認（裏取り）を行ってください。事実が確認できなかった項目は「未確認」と明示してください。
銘柄コードは英字混在4桁（例: 130A）の場合があります。数字だけに丸めたり、末尾の英字を省略したりせず、必ず元の表記のまま正確に引用してください。
本文中にURLを書き出す必要はありません（参照元は別途システム側で取得します）。

【Stage 1 候補データ】
{stage1_candidates}
"""
    config_search = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())]
    )
    res2 = call_gemini_with_retry(client, model_research, [stage2_prompt], config=config_search)
    grounded_research = res2.text
    grounding_urls = extract_grounding_urls(res2)

    print("--> [Stage 3] response_schemaを用いて厳密なJSON構造データに変換中...")
    stage3_prompt = f"""
以下のリサーチ結果をベースに、指定された厳密なJSONスキーマ形式のみで結果を出力してください。
反対材料（bear_case）と撤退条件（invalidation）、信頼度（confidence: High/Medium/Low）を含めてください。
リサーチ結果に書かれていない数値や事実を創作してはいけません。未確認の項目はそのまま「未確認」と書いてください。
codeフィールドは英字混在4桁（例: 130A）の場合があります。数字だけに丸めたり、末尾の英字を省略したりせず、元の表記のまま正確に引用してください。
nameフィールドは会社名を1回だけ記載してください（同じ会社名を2回連結しないこと）。

【リサーチ結果】
{grounded_research}
"""

    json_schema = {
        "type": "OBJECT",
        "properties": {
            "summary": {
                "type": "OBJECT",
                "properties": {
                    "total_scraped": {"type": "INTEGER"},
                    "top_picks_count": {"type": "INTEGER"},
                    "market_trend_comment": {"type": "STRING"}
                },
                "required": ["total_scraped", "top_picks_count", "market_trend_comment"]
            },
            "evaluated_stocks": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "code": {"type": "STRING", "description": "証券コード。英字混在4桁（130A等）の場合は元の表記のまま。"},
                        "name": {"type": "STRING", "description": "会社名。1回だけ記載し、重複連結しないこと。"},
                        "rank": {"type": "STRING", "enum": ["S", "A", "B"]},
                        "breakout_quality": {"type": "STRING"},
                        "confidence": {"type": "STRING", "enum": ["High", "Medium", "Low"]},
                        "fundamentals": {
                            "type": "OBJECT",
                            "properties": {
                                "meets_growth_criteria": {"type": "BOOLEAN"},
                                "revenue_growth": {"type": "STRING"},
                                "profit_growth": {"type": "STRING"},
                                "catalyst": {"type": "STRING"}
                            },
                            "required": ["meets_growth_criteria", "revenue_growth", "profit_growth", "catalyst"]
                        },
                        "technical": {
                            "type": "OBJECT",
                            "properties": {
                                "volume_surge": {"type": "BOOLEAN"},
                                "moving_average_trend": {"type": "STRING"}
                            },
                            "required": ["volume_surge", "moving_average_trend"]
                        },
                        "bear_case": {"type": "STRING"},
                        "invalidation": {"type": "STRING"},
                        "analysis_reason": {"type": "STRING"},
                        "action_plan": {"type": "STRING"}
                    },
                    "required": ["code", "name", "rank", "breakout_quality", "confidence", "fundamentals", "technical", "bear_case", "invalidation", "analysis_reason", "action_plan"]
                }
            }
        },
        "required": ["summary", "evaluated_stocks"]
    }

    config_json = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=json_schema
    )

    res3 = call_gemini_with_retry(client, model_structure, [stage3_prompt], config=config_json)

    try:
        parsed_data = json.loads(res3.text)
    except Exception as e:
        print(f"【エラー】JSONのパースに失敗しました: {e}")
        print(f"レスポンス内容: {res3.text}")
        sys.exit(1)

    parsed_data.setdefault("summary", {})["total_scraped"] = scraped_count
    parsed_data["summary"]["top_picks_count"] = len(parsed_data.get("evaluated_stocks", []))
    parsed_data["source_urls"] = grounding_urls

    return parsed_data

def escape_html(text):
    """HTMLエスケープ処理"""
    if not text:
        return ""
    return (str(text).replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#39;"))

def normalize_code(raw_code, stock_dict, raw_name=None):
    """Geminiの自然文処理でコードが欠損・改変された場合に、
    スクレイピング原本(stock_dict)と突き合わせて正しいコードへ復元する"""
    code = re.sub(r'[^0-9A-Za-z]', '', str(raw_code or '')).upper()

    # まずスクレイピング原本に実在するコードかを確認（改変されていなければここで確定）
    if code in stock_dict:
        return code

    # 実在しない場合は、銘柄名から原本コードを逆引きして復元する
    if raw_name:
        for c, n in stock_dict.items():
            if n == raw_name or (n and (n in raw_name or raw_name in n)):
                return c

    # 復元できなければ形式だけ整えた値を返す（存在しない可能性が高い）
    return code or "0000"

def dedupe_name(raw_name):
    """『社名+区切り文字(1文字以上)+同じ社名』の完全重複だけを検出して片方に畳む。
    区切りゼロで偶然対称な短い社名（ラクラク、サンサン等）は重複とみなさず保持する"""
    if not raw_name:
        return raw_name
    raw_name = raw_name.strip()
    m = re.match(r'^(.{2,})[\s⭐\-\|/・、,]+\1$', raw_name)
    return m.group(1) if m else raw_name

def build_static_assets():
    """軽量・高速な外部CSSとJSファイルを assets/ に生成します"""
    assets_dir = os.path.join("docs", "assets")
    os.makedirs(assets_dir, exist_ok=True)

    css_content = """
:root {
  --bg-color: #05050a;
  --panel-bg: rgba(10, 15, 30, 0.95);
  --cyan: #00f0ff;
  --fuchsia: #d946ef;
  --yellow: #facc15;
  --text-main: #f1f5f9;
  --text-muted: #94a3b8;
  --border-color: #1e293b;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background-color: var(--bg-color);
  color: var(--text-main);
  font-family: monospace, sans-serif;
  padding: 1rem;
  line-height: 1.5;
}
.container { max-width: 900px; margin: 0 auto; }
header {
  border-bottom: 2px solid var(--cyan);
  padding-bottom: 1rem;
  margin-bottom: 1.5rem;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
}
h1 { font-size: 1.5rem; color: var(--cyan); text-transform: uppercase; }
.btn {
  background: #090d16;
  color: var(--fuchsia);
  border: 1px solid var(--fuchsia);
  padding: 0.4rem 0.8rem;
  cursor: pointer;
  font-family: monospace;
  font-weight: bold;
  font-size: 0.8rem;
  transition: 0.2s;
}
.btn:hover { background: var(--fuchsia); color: #000; }
.card {
  background: var(--panel-bg);
  border: 1px solid var(--border-color);
  padding: 1.2rem;
  margin-bottom: 1rem;
  position: relative;
}
.card:hover { border-color: var(--cyan); }
.badge {
  display: inline-block;
  padding: 0.2rem 0.5rem;
  font-size: 0.75rem;
  font-weight: bold;
  border: 1px solid;
}
.badge-s { color: var(--fuchsia); border-color: var(--fuchsia); background: rgba(217,70,239,0.1); }
.badge-a { color: var(--cyan); border-color: var(--cyan); background: rgba(0,240,255,0.1); }
.badge-b { color: var(--text-muted); border-color: var(--border-color); background: rgba(148,163,184,0.08); }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; font-size: 0.8rem; margin: 0.8rem 0; background: rgba(0,0,0,0.4); padding: 0.6rem; border: 1px solid var(--border-color); }
.reason { font-size: 0.85rem; border-left: 2px solid var(--cyan); padding-left: 0.6rem; margin-top: 0.5rem; color: #cbd5e1; }
.overview-box { background: #090d16; border: 1px solid var(--fuchsia); padding: 1rem; margin-bottom: 1.5rem; font-size: 0.85rem; }
.archive-list { list-style: none; display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }
.archive-item { background: #090d16; border: 1px solid var(--border-color); padding: 0.6rem; display: flex; justify-content: space-between; font-size: 0.85rem; text-decoration: none; color: var(--cyan); }
.archive-item:hover { border-color: var(--fuchsia); color: var(--fuchsia); }
#toast {
  position: fixed; bottom: 20px; right: 20px; background: #0f172a; border: 1px solid var(--cyan);
  color: var(--cyan); padding: 10px 20px; font-size: 0.85rem; z-index: 1000;
  display: none; box-shadow: 0 0 10px rgba(0,240,255,0.3);
}
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.75); z-index: 900;
  display: none; align-items: center; justify-content: center; padding: 1rem;
}
.modal-overlay.open { display: flex; }
.modal-box {
  background: var(--panel-bg); border: 1px solid var(--fuchsia); max-width: 480px; width: 100%;
  max-height: 80vh; display: flex; flex-direction: column; padding: 1.2rem;
}
.modal-box h2 { font-size: 1rem; color: var(--fuchsia); margin-bottom: 0.8rem; display: flex; justify-content: space-between; align-items: center; }
.modal-close { background: none; border: none; color: var(--text-muted); font-size: 1.1rem; cursor: pointer; }
.modal-close:hover { color: var(--fuchsia); }
#watchlist-items { list-style: none; overflow-y: auto; display: flex; flex-direction: column; gap: 0.5rem; }
#watchlist-items li {
  display: flex; justify-content: space-between; align-items: center; gap: 0.5rem;
  background: #090d16; border: 1px solid var(--border-color); padding: 0.5rem 0.7rem; font-size: 0.85rem;
}
#watchlist-items a { color: var(--cyan); text-decoration: none; }
#watchlist-items a:hover { color: var(--fuchsia); }
.remove-btn {
  background: none; border: 1px solid var(--border-color); color: var(--text-muted);
  font-size: 0.75rem; padding: 0.2rem 0.5rem; cursor: pointer; flex-shrink: 0;
}
.remove-btn:hover { border-color: var(--fuchsia); color: var(--fuchsia); }
.watchlist-empty { color: var(--text-muted); font-size: 0.85rem; text-align: center; padding: 1rem 0; }
@media(max-width: 600px) {
  .grid-2 { grid-template-columns: 1fr; }
  .archive-list { grid-template-columns: 1fr; }
}
"""
    with open(os.path.join(assets_dir, "style.css"), "w", encoding="utf-8") as f:
        f.write(css_content.strip())

    js_content = """
let watchlist = JSON.parse(localStorage.getItem('cyber_stock_watchlist') || '[]');

document.addEventListener('DOMContentLoaded', () => {
    updateWatchCount();
    setupEventDelegation();
});

function updateWatchCount() {
    document.querySelectorAll('.watch-count').forEach(el => { el.textContent = watchlist.length; });
}

function showToast(message) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.style.display = 'block';
    setTimeout(() => { toast.style.display = 'none'; }, 3000);
}

function setupEventDelegation() {
    document.body.addEventListener('click', (e) => {
        const watchBtn = e.target.closest('.watch-btn');
        if (watchBtn) {
            toggleWatchlist(watchBtn.dataset.code, watchBtn.dataset.name);
            return;
        }
        const openBtn = e.target.closest('.open-watchlist-btn');
        if (openBtn) {
            renderWatchlistModal();
            document.getElementById('watchlist-modal').classList.add('open');
            return;
        }
        const closeBtn = e.target.closest('.modal-close, .modal-overlay');
        if (closeBtn && (e.target.classList.contains('modal-close') || e.target.classList.contains('modal-overlay'))) {
            document.getElementById('watchlist-modal').classList.remove('open');
            return;
        }
        const removeBtn = e.target.closest('.remove-btn');
        if (removeBtn) {
            removeFromWatchlist(removeBtn.dataset.code);
            return;
        }
    });
}

function toggleWatchlist(code, name) {
    code = code.toUpperCase();
    const index = watchlist.findIndex(item => item.code === code);
    if (index >= 0) {
        watchlist.splice(index, 1);
        showToast(`[ ${code} ] を監視リストから解除しました`);
    } else {
        watchlist.push({ code, name });
        showToast(`⭐ [ ${code} ] ${name} を監視リストに登録しました！`);
    }
    localStorage.setItem('cyber_stock_watchlist', JSON.stringify(watchlist));
    updateWatchCount();
    renderWatchlistModal();
}

function removeFromWatchlist(code) {
    watchlist = watchlist.filter(item => item.code !== code);
    localStorage.setItem('cyber_stock_watchlist', JSON.stringify(watchlist));
    updateWatchCount();
    renderWatchlistModal();
}

function renderWatchlistModal() {
    const listEl = document.getElementById('watchlist-items');
    if (!listEl) return;

    if (watchlist.length === 0) {
        listEl.innerHTML = '<li class="watchlist-empty">監視銘柄はまだ登録されていません</li>';
        return;
    }

    listEl.innerHTML = watchlist.map(item => `
        <li>
            <a href="https://finance.yahoo.co.jp/quote/${item.code}.T" target="_blank">[ ${item.code} ] ${item.name}</a>
            <button class="remove-btn" data-code="${item.code}">解除</button>
        </li>
    `).join('');
}
"""
    with open(os.path.join(assets_dir, "app.js"), "w", encoding="utf-8") as f:
        f.write(js_content.strip())

def build_line_messages(data, today_display, max_len=4500, max_messages=5):
    """LINEの1通あたり上限に収まるよう複数メッセージに分割する"""
    summary = data.get("summary", {})
    stocks = data.get("evaluated_stocks", [])

    blocks = [f"📊 本日の新高値精鋭レポート // {today_display}\n\n💡 総括: {summary.get('market_trend_comment', '')}"]

    for s in stocks:
        fund = s.get("fundamentals", {})
        blocks.append(
            f"▪️ [{s.get('code')}] {s.get('name')} (評価:{s.get('rank')} | 信頼度:{s.get('confidence')})\n"
            f"  原動力: {fund.get('catalyst', 'N/A')}\n"
            f"  反対材料: {s.get('bear_case', 'N/A')}\n"
            f"  撤退条件: {s.get('invalidation', 'N/A')}\n"
            f"  アクション: {s.get('action_plan', '観察継続')}"
        )

    messages = []
    current = ""
    for block in blocks:
        block = block[:max_len]
        if not current:
            current = block
        elif len(current) + len(block) + 2 <= max_len:
            current += "\n\n" + block
        else:
            messages.append(current)
            current = block
    if current:
        messages.append(current)

    return messages[:max_messages]

WATCHLIST_MODAL_HTML = """
    <div id="watchlist-modal" class="modal-overlay">
        <div class="modal-box">
            <h2>⭐ 監視リスト <button class="modal-close" aria-label="閉じる">✕</button></h2>
            <ul id="watchlist-items"></ul>
        </div>
    </div>
"""

def create_dashboard_html(data, stock_dict):
    """軽量CSS/JSを用いた高速HTMLファイルおよびアーカイブを生成します"""
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    today_str = now.strftime("%Y-%m-%d")
    today_display = now.strftime("%Y.%m.%d")

    docs_dir = "docs"
    reports_dir = os.path.join(docs_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    summary = data.get("summary", {})
    stocks = data.get("evaluated_stocks", [])
    market_comment = escape_html(summary.get("market_trend_comment", "本日の相場感コメントなし"))
    source_urls = data.get("source_urls", [])

    cards_html = ""
    for s in stocks:
        raw_code = s.get("code", "")
        raw_name_from_json = s.get("name", "")
        resolved_code = normalize_code(raw_code, stock_dict, raw_name_from_json)
        code = escape_html(resolved_code)

        base_name = stock_dict.get(resolved_code, raw_name_from_json) or f"銘柄 {resolved_code}"
        name = escape_html(dedupe_name(base_name))

        rank = s.get("rank", "B")
        rank = rank if rank in ("S", "A", "B") else "B"
        rank_esc = escape_html(rank)
        breakout = escape_html(s.get("breakout_quality", ""))
        confidence = escape_html(s.get("confidence", "Medium"))
        fund = s.get("fundamentals", {})
        tech = s.get("technical", {})
        bear = escape_html(s.get("bear_case", "特になし"))
        inval = escape_html(s.get("invalidation", "トレンド割れ"))
        reason = escape_html(s.get("analysis_reason", ""))
        action = escape_html(s.get("action_plan", "観察継続"))

        badge_class = {"S": "badge-s", "A": "badge-a", "B": "badge-b"}[rank]

        cards_html += f"""
        <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.6rem;">
                <div style="display:flex; gap:0.5rem; align-items:center;">
                    <span class="badge {badge_class}">RANK {rank_esc}</span>
                    <a href="https://finance.yahoo.co.jp/quote/{code}.T" target="_blank" style="color:var(--cyan); font-weight:bold; font-size:1.1rem; text-decoration:none;">
                        [ {code} ] {name} 🔗
                    </a>
                </div>
                <button class="btn watch-btn" data-code="{code}" data-name="{name}">⭐ WATCH</button>
            </div>

            <div class="grid-2">
                <div><span style="color:var(--text-muted);">ブレイク質:</span> {breakout}</div>
                <div><span style="color:var(--text-muted);">アクション:</span> <span style="color:var(--yellow); font-weight:bold;">{action}</span></div>
                <div><span style="color:var(--text-muted);">増収増益:</span> {'✅ 満たす' if fund.get('meets_growth_criteria') else '⚠️ 要確認'} (信頼度: {confidence})</div>
                <div><span style="color:var(--text-muted);">出来高急増:</span> {'あり' if tech.get('volume_surge') else '確認中'}</div>
                <div style="grid-column: span 2;"><span style="color:var(--text-muted);">原動力:</span> {escape_html(fund.get('catalyst', 'N/A'))}</div>
                <div style="grid-column: span 2;"><span style="color:var(--text-muted);">反対材料 (Bear):</span> {bear}</div>
                <div style="grid-column: span 2;"><span style="color:var(--text-muted);">撤退条件 (Invalidation):</span> {inval}</div>
            </div>

            <p class="reason">{reason}</p>
        </div>
        """

    sources_html = ""
    if source_urls:
        links = "".join(
            f'<li><a href="{escape_html(u)}" target="_blank" style="color:var(--cyan); font-size:0.8rem;">{escape_html(u)}</a></li>'
            for u in source_urls
        )
        sources_html = f"""
        <section style="margin-top:1.5rem;">
            <h2 style="font-size:0.95rem; color:var(--fuchsia); margin-bottom:0.6rem;">参照した情報源</h2>
            <ul style="list-style:none; display:flex; flex-direction:column; gap:0.3rem;">{links}</ul>
        </section>
        """

    disclaimer_html = """
        <p style="font-size:0.7rem; color:var(--text-muted); border-top:1px solid var(--border-color); padding-top:1rem; margin-top:1.5rem;">
            このページは自動生成された機械的なスクリーニング結果で、投資助言ではありません。業績数値は生成AIが検索して抽出したもので、誤りや古い情報を含む可能性があります。売買の判断前に必ず決算短信・適時開示の原文で確認してください。
        </p>
    """

    report_html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="robots" content="noindex">
    <title>[ {today_display} ] HIGH-BREAK REPORT</title>
    <link rel="stylesheet" href="../assets/style.css">
</head>
<body>
    <div class="container">
        <header>
            <div>
                <a href="../index.html" style="color:var(--fuchsia); font-size:0.8rem; text-decoration:none;">≪ DASHBOARD</a>
                <h1>⚡ ANALYSIS // {today_display}</h1>
            </div>
            <button class="btn open-watchlist-btn">⭐ WATCHLIST [ <span class="watch-count">0</span> ]</button>
        </header>

        <div class="overview-box">
            <strong>💡 MARKET OVERVIEW:</strong> {market_comment}
        </div>

        <main>{cards_html}</main>
        {sources_html}
        {disclaimer_html}
    </div>
    {WATCHLIST_MODAL_HTML}
    <div id="toast"></div>
    <script src="../assets/app.js"></script>
</body>
</html>"""

    today_file_path = os.path.join(reports_dir, f"{today_str}.html")
    with open(today_file_path, "w", encoding="utf-8") as f:
        f.write(report_html)

    data_dir = os.path.join(docs_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, f"{today_str}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    files = sorted([f for f in os.listdir(reports_dir) if f.endswith(".html")], reverse=True)
    archive_links = ""
    for file in files:
        date_part = file.replace(".html", "")
        archive_links += f'''<li>
        <a href="reports/{file}" class="archive-item">
            <span>▶ ARCHIVE // {date_part}</span>
            <span>ACCESS →</span>
        </a>
        </li>\n'''

    index_html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="robots" content="noindex">
    <title>CYBERPUNK // BREAKOUT STOCKS TERMINAL</title>
    <link rel="stylesheet" href="assets/style.css">
</head>
<body>
    <div class="container">
        <header>
            <div>
                <span style="color:var(--cyan); font-size:0.75rem;">SYSTEM OPERATIONAL // MULTI-STAGE GROUNDING</span>
                <h1>⚡ NEW-HIGH TERMINAL</h1>
            </div>
            <button class="btn open-watchlist-btn">⭐ WATCHLIST [ <span class="watch-count">0</span> ]</button>
        </header>

        <div class="overview-box">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
                <strong>🔥 最新レポート ({today_display})</strong>
                <a href="reports/{today_str}.html" class="btn">FULL REPORT ↗</a>
            </div>
            <p style="color:var(--text-muted); font-size:0.8rem;">最新のスクリーニング結果とAI裏取りデータは「FULL REPORT」から確認できます。</p>
        </div>

        <section>
            <h2 style="font-size:1rem; color:var(--fuchsia); margin-bottom:0.8rem;">📂 SYSTEM ARCHIVES</h2>
            <ul class="archive-list">{archive_links}</ul>
        </section>
    </div>
    {WATCHLIST_MODAL_HTML}
    <div id="toast"></div>
    <script src="assets/app.js"></script>
</body>
</html>"""

    with open(os.path.join(docs_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)

    with open(os.path.join(docs_dir, ".nojekyll"), "w", encoding="utf-8") as f:
        f.write("")

    print("【成功】軽量CSS/JS及びダッシュボードの生成が完了しました。")
    return build_line_messages(data, today_display)

def send_line_push_messages(messages):
    """LINE Messaging API経由でプッシュ通知を送信します"""
    line_access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    line_user_id = os.environ.get("LINE_USER_ID", "").strip()

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {line_access_token}"
    }

    payload = {
        "to": line_user_id,
        "messages": [{"type": "text", "text": m} for m in messages]
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=15)
        if res.status_code == 200:
            print(f"【成功】LINEへのレポート送信が正常に完了しました（{len(messages)}通）。")
        else:
            print(f"【エラー】LINE送信エラー (Status {res.status_code}): {res.text}")
            sys.exit(1)
    except Exception as e:
        print(f"【エラー】LINE通信処理中に例外が発生しました: {e}")
        sys.exit(1)

def write_job_summary(data):
    """GitHub Actions のジョブサマリーに出力します"""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    stocks = data.get("evaluated_stocks", [])

    lines = "\n".join(
        f"| {s.get('rank')} | {s.get('code')} | {s.get('name')} | {s.get('confidence')} | {s.get('action_plan')} |"
        for s in stocks
    )
    table = (
        "### 📊 新高値スクリーニング結果\n\n"
        f"対象 {data.get('summary', {}).get('total_scraped', 0)} 銘柄 / 抽出 {len(stocks)} 銘柄\n\n"
        "| 評価 | コード | 銘柄名 | 信頼度 | アクション |\n|---|---|---|---|---|\n" + lines
    )

    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(table + "\n")
    else:
        print(table)

def main():
    parser = argparse.ArgumentParser(description="新高値ブレイク 自動スクリーニングAPI")
    parser.add_argument("--dry-run", action="store_true", help="LINEに送信せず、HTML生成とスクリーニングテストのみ行います")
    parser.add_argument("--force", action="store_true", help="営業日判定を無視して強制実行します")
    args = parser.parse_args()

    print("1. 環境変数のチェック中...")
    check_env_vars(require_line=not args.dry_run)

    jst = timezone(timedelta(hours=9))
    today_now = datetime.now(jst)

    if not args.force and is_market_holiday(today_now):
        print(f"本日 ({today_now.strftime('%Y-%m-%d')}) は休日（土日・祝日・年末年始）のため処理をスキップします。")
        sys.exit(0)

    print("2. 外部静的アセット (assets/style.css, app.js) のビルド中...")
    build_static_assets()

    print("3. Yahoo!ファイナンスから新高値更新銘柄データを取得中...")
    stock_data, stock_dict, scraped_count = fetch_new_high_stocks()

    print("4. マルチステージ Gemini API スクリーニング＆裏取り分析を実行中...")
    json_data = analyze_stocks_multi_stage(stock_data, scraped_count)

    print("5. 高速ダッシュボードHTML・JSONアーカイブを生成中...")
    line_messages = create_dashboard_html(json_data, stock_dict)

    write_job_summary(json_data)

    if args.dry_run:
        print("【ドライラン】--dry-run が指定されたため、LINEへの配信をスキップして終了します。")
        sys.exit(0)

    print("6. LINEへレポートを配信中...")
    send_line_push_messages(line_messages)

if __name__ == "__main__":
    main()
