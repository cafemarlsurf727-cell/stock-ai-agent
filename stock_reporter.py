import os
import sys
import time
import json
import re
import requests
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from google import genai

def check_env_vars():
    """GitHub Secrets等から必要な環境変数が渡されているか事前確認します"""
    required_vars = ["GEMINI_API_KEY", "LINE_CHANNEL_ACCESS_TOKEN", "LINE_USER_ID"]
    missing = [var for var in required_vars if not os.environ.get(var)]
    
    if missing:
        print(f"【エラー】以下の環境変数が設定されていません: {', '.join(missing)}")
        print("GitHub Secretsの設定名を確認してください。")
        sys.exit(1)

def fetch_new_high_stocks():
    """Yahoo!ファイナンスから年初来高値銘柄データと『コード: 銘柄名』の辞書を取得します"""
    url = "https://finance.yahoo.co.jp/stocks/ranking/yearToDateHigh?market=all"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
    except Exception as e:
        print(f"【エラー】Yahoo!ファイナンスからのデータ取得に失敗しました: {e}")
        sys.exit(1)
        
    soup = BeautifulSoup(response.text, "html.parser")
    
    stock_dict = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r'/quote/([0-9A-Za-z]{4})\.T', a['href'], re.IGNORECASE)
        if m:
            code = m.group(1).upper()
            text = a.text.strip()
            clean_name = re.sub(r'^[0-9A-Za-z]{4}\s*', '', text)
            clean_name = re.sub(r'\s*[0-9A-Za-z]{4}$', '', clean_name)
            clean_name = clean_name.replace('(株)', '').replace('（株）', '').strip()
            if clean_name and len(clean_name) >= 2 and not clean_name.isdigit():
                stock_dict[code] = clean_name

    table = soup.find("table")
    if not table:
        print("【警告】新高値更新銘柄のテーブル要素が見つかりませんでした。")
        return "本日新高値更新銘柄のデータ取得に失敗しました。", stock_dict
        
    rows = table.find_all("tr")
    formatted_data = []
    
    for row in rows:
        cols = [col.text.strip() for col in row.find_all(["th", "td"])]
        if cols:
            clean_cols = [" ".join(c.split()) for c in cols]
            formatted_data.append(" | ".join(clean_cols))
            
    if len(formatted_data) <= 1:
        return "本日新高値更新銘柄のデータが見つかりませんでした。", stock_dict
        
    return "\n".join(formatted_data[:35]), stock_dict

def generate_analysis_report(stock_data_text):
    """Gemini APIで新高値銘柄のスクリーニング分析を実施し、JSONデータを返します"""
    client = genai.Client()
    
    system_prompt = """
あなたは株式投資の高度な自動スクリーニングAPIです。
入力された「新高値更新銘柄データ」を「新高値ブレイク投資法」のロジックに基づいて分析し、投資価値の高い注目銘柄を特定して【完全なJSON形式】のみで出力してください。

# 判定・評価ロジック
1. 高値更新の質と上値の軽さ
   - 過去1年（52週）または過去2年以上の高値突破か判断する（2年以上のブレイクは企業変革の可能性が高く加点）。
   - 上値に抵抗帯（シコリ）がなく「売り圧力が少ない上値が軽い状態」かを評価する。
2. 業績・ファンダメンタル（四半期業績の伸び）
   - 直近四半期の「経常利益 前年同期比 +20% 以上」かつ「売上高 前年同期比 +10% 以上」を満たしているかチェックする。
   - 新高値更新の原動力（決算サプライズ、新事業、業界構造の変化等）が存在するか確認する。
3. テクニカル・出来高
   - 突破時に「出来高の急増」が見られるか。
   - 移動平均線が上向きのトレンドを形成しているか。

# 出力ルール
- 出力は必ず以下のJSONスキーマ構造を満たすパース可能なJSONオブジェクト【のみ】で出力してください。
- マークダウンのバッククォート（```json など）や前後の挨拶文は一切含めないでください。

# 出力JSONフォーマット
{
  "summary": {
    "total_scraped": 35,
    "top_picks_count": 5,
    "market_trend_comment": "本日の新高値銘柄群に見られるセクターやテーマの傾向上についての詳細コメント"
  },
  "evaluated_stocks": [
    {
      "code": "7203",
      "name": "トヨタ自動車",
      "rank": "S",
      "breakout_quality": "過去2年高値更新",
      "fundamentals": {
        "meets_growth_criteria": true,
        "revenue_growth": "増収傾向",
        "profit_growth": "経常利益大幅増",
        "catalyst": "新高値突破の原動力・材料説明"
      },
      "technical": {
        "volume_surge": true,
        "moving_average_trend": "上向き"
      },
      "analysis_reason": "上値の軽さや業績変化に関する分析理由",
      "action_plan": "買い検討"
    }
  ]
}
"""

    prompt = f"【本日の新高値更新銘柄データ】\n{stock_data_text}"
    model_name = "gemini-3.6-flash"
    max_retries = 5
    
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[system_prompt, prompt]
            )
            raw_text = response.text.strip()
            # マークダウンのコードブロックがついている場合を除去
            raw_text = re.sub(r'^```json\s*', '', raw_text)
            raw_text = re.sub(r'^```\s*', '', raw_text)
            raw_text = re.sub(r'\s*```$', '', raw_text)
            
            parsed_json = json.loads(raw_text)
            return parsed_json
        except Exception as e:
            err_msg = str(e)
            print(f"【警告】Gemini API分析試行 ({attempt}/{max_retries}) に失敗しました: {e}")
            if attempt < max_retries:
                delay = 65 if ("429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg) else (attempt * 15)
                time.sleep(delay)
            else:
                print("【エラー】規定の再試行回数を超えたため処理を中断します。")
                sys.exit(1)

def create_dashboard_html(data, stock_dict):
    """構造化JSONデータからサイバーパンク風のカード型HTMLダッシュボードを生成します"""
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    today_str = now.strftime("%Y-%m-%d")
    today_display = now.strftime("%Y.%m.%d")
    
    docs_dir = "docs"
    reports_dir = os.path.join(docs_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    
    summary = data.get("summary", {})
    stocks = data.get("evaluated_stocks", [])
    
    # LINE通知用のテキスト生成
    line_text = f"📊 本日の新高値精鋭レポート // {today_display}\n\n"
    market_comment = summary.get("market_trend_comment", "本日の相場感コメントなし")
    line_text += f"💡 総括: {market_comment}\n\n"
    
    cards_html = ""
    for s in stocks:
        code = s.get("code", "").upper()
        name = stock_dict.get(code, s.get("name", f"銘柄 {code}"))
        rank = s.get("rank", "B")
        breakout = s.get("breakout_quality", "")
        fund = s.get("fundamentals", {})
        tech = s.get("technical", {})
        reason = s.get("analysis_reason", "")
        action = s.get("action_plan", "観察継続")
        
        # ランクに応じたネオンカラー設定
        rank_color = "text-fuchsia-400 border-fuchsia-500 bg-fuchsia-950/40" if rank in ["S", "A"] else "text-cyan-400 border-cyan-500 bg-cyan-950/40"
        
        # LINEテキスト用に追加
        line_text += f"▪️ [{code}] {name} (評価:{rank})\n  原動力: {fund.get('catalyst', 'N/A')}\n  アクション: {action}\n\n"
        
        js_safe_name = name.replace("'", "\\'").replace('"', '\\"')
        
        cards_html += f"""
        <div class="bg-slate-900/90 border border-slate-800 hover:border-cyan-500/80 transition p-5 space-y-3 relative group shadow-[0_0_10px_rgba(0,0,0,0.5)]">
            <div class="flex justify-between items-start gap-2">
                <div class="flex items-center gap-2">
                    <span class="px-2.5 py-0.5 text-xs font-bold border {rank_color}">RANK {rank}</span>
                    <a href="https://finance.yahoo.co.jp/quote/{code}.T" target="_blank" class="text-cyan-400 hover:text-fuchsia-400 font-bold text-lg font-mono flex items-center gap-1 underline decoration-cyan-500/50">
                        <span>[ {code} ] {name}</span>
                        <span class="text-xs text-yellow-400">🔗</span>
                    </a>
                </div>
                <button onclick="toggleInlineStock('{code}', '{js_safe_name}', event)" class="bg-slate-800 hover:bg-fuchsia-900 text-yellow-400 border border-fuchsia-500/40 px-2.5 py-1 text-xs font-bold transition flex items-center gap-1 cursor-pointer" title="監視リストに登録/解除">
                    ⭐ WATCH
                </button>
            </div>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs font-mono text-slate-300 bg-black/40 p-3 border border-slate-800/80">
                <div><span class="text-slate-500">ブレイクの質:</span> <span class="text-fuchsia-300">{breakout}</span></div>
                <div><span class="text-slate-500">アクション:</span> <span class="text-yellow-300 font-bold">{action}</span></div>
                <div class="md:col-span-2"><span class="text-slate-500">原動力・材料:</span> <span class="text-slate-200">{fund.get('catalyst', 'N/A')}</span></div>
                <div><span class="text-slate-500">出来高急増:</span> <span class="text-cyan-300">{'あり' if tech.get('volume_surge') else '確認中'}</span></div>
                <div><span class="text-slate-500">トレンド:</span> <span class="text-cyan-300">{tech.get('moving_average_trend', '上向き')}</span></div>
            </div>
            
            <p class="text-xs text-slate-300 leading-relaxed font-sans border-l-2 border-cyan-500/60 pl-3 py-0.5">
                {reason}
            </p>
        </div>
        """

    watchlist_js = """
    <script>
        let watchlist = JSON.parse(localStorage.getItem('cyber_stock_watchlist') || '[]');

        document.addEventListener('DOMContentLoaded', () => {
            updateWatchlistCount();
        });

        function updateWatchlistCount() {
            const el = document.getElementById('watch-count');
            if (el) el.textContent = watchlist.length;
        }

        function toggleWatchlistModal() {
            const modal = document.getElementById('watchlist-modal');
            if (modal.classList.contains('hidden')) {
                renderWatchlist();
                modal.classList.remove('hidden');
                modal.classList.add('flex');
            } else {
                modal.classList.add('hidden');
                modal.classList.remove('flex');
            }
        }

        function toggleInlineStock(code, defaultName, ev) {
            if (ev) ev.preventDefault();
            code = code.toUpperCase();
            const index = watchlist.findIndex(item => item.code === code);
            
            if (index >= 0) {
                if (watchlist[index].name.startsWith('銘柄 ') && defaultName && !defaultName.startsWith('銘柄 ')) {
                    watchlist[index].name = defaultName;
                    localStorage.setItem('cyber_stock_watchlist', JSON.stringify(watchlist));
                    updateWatchlistCount();
                    renderWatchlist();
                    alert(`⭐ [ ${code} ] の銘柄名を 「${defaultName}」 に更新しました！`);
                } else {
                    const removed = watchlist.splice(index, 1)[0];
                    localStorage.setItem('cyber_stock_watchlist', JSON.stringify(watchlist));
                    updateWatchlistCount();
                    renderWatchlist();
                    alert(`[ ${code} ] ${removed.name || ''} を監視リストから解除しました`);
                }
            } else {
                const name = defaultName || ('銘柄 ' + code);
                watchlist.push({ code, name });
                localStorage.setItem('cyber_stock_watchlist', JSON.stringify(watchlist));
                updateWatchlistCount();
                renderWatchlist();
                alert(`⭐ [ ${code} ] ${name} を監視リストに登録しました！`);
            }
        }

        function addStockToWatchlist() {
            const codeInput = document.getElementById('input-code');
            const nameInput = document.getElementById('input-name');
            const code = codeInput.value.trim().toUpperCase();
            const name = nameInput.value.trim() || ('銘柄 ' + code);

            if (!code || !/^\d[0-9A-Z]{3}$/i.test(code)) {
                alert('4桁の銘柄コードを入力してください（例: 7203, 130A）');
                return;
            }

            if (watchlist.some(item => item.code === code)) {
                alert('すでに監視リストに登録されています');
                return;
            }

            watchlist.push({ code, name });
            localStorage.setItem('cyber_stock_watchlist', JSON.stringify(watchlist));
            
            codeInput.value = '';
            nameInput.value = '';
            
            updateWatchlistCount();
            renderWatchlist();
        }

        function editStockName(code) {
            code = code.toUpperCase();
            const item = watchlist.find(i => i.code === code);
            if (!item) return;
            
            const newName = prompt(`[ ${code} ] の新しい銘柄名を入力してください:`, item.name);
            if (newName !== null && newName.trim() !== '') {
                item.name = newName.trim();
                localStorage.setItem('cyber_stock_watchlist', JSON.stringify(watchlist));
                renderWatchlist();
            }
        }

        function removeStockFromWatchlist(code) {
            code = code.toUpperCase();
            watchlist = watchlist.filter(item => item.code !== code);
            localStorage.setItem('cyber_stock_watchlist', JSON.stringify(watchlist));
            updateWatchlistCount();
            renderWatchlist();
        }

        function renderWatchlist() {
            const listEl = document.getElementById('watchlist-items');
            if (!listEl) return;

            if (watchlist.length === 0) {
                listEl.innerHTML = '<li class="text-slate-500 text-xs py-4 text-center font-mono">監視銘柄は登録されていません</li>';
                return;
            }

            listEl.innerHTML = watchlist.map(item => `
                <li class="flex items-center justify-between p-3 bg-slate-900 border border-slate-800 hover:border-cyan-500/50 transition font-mono gap-2">
                    <div class="flex items-center gap-2 overflow-hidden flex-1">
                        <span class="text-fuchsia-400 font-bold shrink-0">[ ${item.code} ]</span>
                        <a href="https://finance.yahoo.co.jp/quote/${item.code}.T" target="_blank" class="text-slate-100 hover:text-cyan-400 font-bold text-sm truncate flex items-center gap-1 hover:underline" title="Yahoo!ファイナンスでチャートを開く">
                            <span>${item.name || ('銘柄 ' + item.code)}</span>
                            <span class="text-xs text-yellow-400 shrink-0">🔗</span>
                        </a>
                    </div>
                    <div class="flex items-center gap-1 shrink-0">
                        <button onclick="editStockName('${item.code}')" class="text-xs text-cyan-400 hover:text-white bg-slate-800 hover:bg-cyan-900 border border-cyan-500/30 px-2 py-1 transition font-bold" title="銘柄名を編集">
                            ✏️ 編集
                        </button>
                        <button onclick="removeStockFromWatchlist('${item.code}')" class="text-xs text-red-400 hover:text-white bg-red-950/60 hover:bg-red-600 border border-red-500/40 px-2 py-1 transition font-bold flex items-center gap-1 shadow-[0_0_5px_rgba(239,68,68,0.3)]">
                            🗑️ 監視解除
                        </button>
                    </div>
                </li>
            `).join('');
        }
    </script>
    """

    watchlist_modal_html = """
    <!-- WATCHLIST MODAL -->
    <div id="watchlist-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 hidden justify-center items-center p-4">
        <div class="bg-slate-950 border-2 border-fuchsia-500 shadow-[0_0_25px_rgba(217,70,239,0.4)] w-full max-w-lg p-6 space-y-4 font-mono">
            <div class="flex justify-between items-center border-b border-fuchsia-500/40 pb-3">
                <h3 class="text-lg font-bold text-fuchsia-400 flex items-center gap-2">
                    <span>⭐ TARGET WATCHLIST</span>
                </h3>
                <button onclick="toggleWatchlistModal()" class="text-slate-400 hover:text-fuchsia-400 text-xl font-bold">✕</button>
            </div>

            <!-- ADD FORM -->
            <div class="space-y-2 bg-slate-900/80 p-3 border border-slate-800">
                <p class="text-xs text-cyan-400">手動で監視対象を追加:</p>
                <div class="flex gap-2">
                    <input id="input-code" type="text" placeholder="コード (7203, 130A)" maxlength="4" class="bg-black border border-cyan-500/50 text-cyan-400 text-xs p-2 w-32 focus:outline-none focus:border-cyan-400">
                    <input id="input-name" type="text" placeholder="銘柄名" class="bg-black border border-cyan-500/50 text-slate-200 text-xs p-2 flex-1 focus:outline-none focus:border-cyan-400">
                    <button onclick="addStockToWatchlist()" class="bg-fuchsia-600 hover:bg-fuchsia-500 text-black font-bold text-xs px-3 py-2 transition shadow-[0_0_10px_rgba(217,70,239,0.5)]">
                        + ADD
                    </button>
                </div>
            </div>

            <!-- WATCHLIST ITEMS -->
            <ul id="watchlist-items" class="space-y-2 max-h-64 overflow-y-auto pr-1"></ul>

            <div class="pt-2 text-right">
                <button onclick="toggleWatchlistModal()" class="text-xs text-slate-400 hover:text-slate-200 border border-slate-700 px-3 py-1">CLOSE</button>
            </div>
        </div>
    </div>
    """

    # レポート個別HTMLページ
    report_html = f"""<!DOCTYPE html>
<html lang="ja" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>[ {today_display} ] CYBER HIGH-BREAK REPORT</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
        body {{ font-family: 'Share Tech Mono', monospace, sans-serif; }}
        .cyber-tile {{
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.9) 0%, rgba(5, 5, 10, 0.95) 100%);
            background-image: radial-gradient(rgba(0, 240, 255, 0.1) 1px, transparent 0);
            background-size: 16px 16px;
        }}
    </style>
</head>
<body class="bg-black text-cyan-400 min-h-screen p-4 md:p-8 cyber-tile selection:bg-fuchsia-500 selection:text-black">
    <div class="max-w-4xl mx-auto space-y-6">
        <header class="border-b-2 border-cyan-500 pb-4 shadow-[0_0_15px_rgba(0,240,255,0.4)] flex justify-between items-end gap-2">
            <div>
                <a href="../index.html" class="text-xs text-fuchsia-400 hover:text-fuchsia-300 font-bold">≪ RETURN TO DASHBOARD</a>
                <h1 class="text-2xl md:text-3xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 via-fuchsia-500 to-yellow-400 tracking-wider mt-1">
                    ⚡ TARGET ANALYSIS // {today_display}
                </h1>
            </div>
            <button onclick="toggleWatchlistModal()" class="text-xs bg-slate-900 border border-fuchsia-500 hover:bg-fuchsia-950 text-fuchsia-400 font-bold px-3 py-1.5 transition flex items-center gap-1 shadow-[0_0_10px_rgba(217,70,239,0.3)]">
                <span>⭐ WATCHLIST</span>
                <span class="text-yellow-400">[ <span id="watch-count">0</span> ]</span>
            </button>
        </header>
        
        <!-- MARKET TREND COMMENT -->
        <section class="bg-slate-950 border border-fuchsia-500/60 p-4 text-xs font-mono text-slate-300 shadow-[0_0_15px_rgba(217,70,239,0.2)]">
            <span class="text-fuchsia-400 font-bold">💡 MARKET OVERVIEW:</span> {market_comment}
        </section>

        <!-- CARDS CONTAINER -->
        <main class="space-y-4">
            {cards_html}
        </main>
    </div>
    {watchlist_modal_html}
    {watchlist_js}
</body>
</html>"""

    today_file_path = os.path.join(reports_dir, f"{today_str}.html")
    with open(today_file_path, "w", encoding="utf-8") as f:
        f.write(report_html)
        
    # アーカイブリンク一覧の更新
    files = sorted(os.listdir(reports_dir), reverse=True)
    archive_links = ""
    for file in files:
        if file.endswith(".html"):
            date_part = file.replace(".html", "")
            archive_links += f'''<li>
            <a href="reports/{file}" class="group block p-3 bg-slate-950 border border-cyan-500/30 hover:border-fuchsia-500 hover:shadow-[0_0_15px_rgba(217,70,239,0.4)] transition duration-200 flex justify-between items-center text-sm font-mono">
                <span class="text-cyan-400 group-hover:text-fuchsia-400 transition">▶ ARCHIVE // {date_part}</span>
                <span class="text-xs text-slate-500 group-hover:text-yellow-400">ACCESS LOG →</span>
            </a>
            </li>\n'''
            
    # メインダッシュボード（index.html）
    index_html = f"""<!DOCTYPE html>
<html lang="ja" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CYBERPUNK // BREAKOUT STOCKS TERMINAL</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
        body {{ font-family: 'Share Tech Mono', monospace, sans-serif; }}
        .cyber-bg {{
            background: linear-gradient(180deg, #05050a 0%, #090d16 100%);
            background-image: linear-gradient(rgba(0, 240, 255, 0.05) 1px, transparent 0), linear-gradient(90deg, rgba(0, 240, 255, 0.05) 1px, transparent 0);
            background-size: 24px 24px;
        }}
        .neon-glow-cyan {{ box-shadow: 0 0 15px rgba(0, 240, 255, 0.3); }}
    </style>
</head>
<body class="bg-black text-slate-100 min-h-screen p-4 md:p-8 cyber-bg selection:bg-fuchsia-500 selection:text-black">
    <div class="max-w-4xl mx-auto space-y-8">
        
        <!-- HEADER -->
        <header class="border-b-2 border-cyan-500 pb-4 flex flex-col md:flex-row justify-between md:items-end gap-3 neon-glow-cyan">
            <div>
                <div class="flex items-center space-x-2">
                    <span class="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-ping"></span>
                    <span class="text-xs text-cyan-400 tracking-widest uppercase">SYSTEM OPERATIONAL // GEMINI 3.6 FLASH</span>
                </div>
                <h1 class="text-3xl md:text-4xl font-black text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 via-fuchsia-400 to-yellow-400 tracking-wider">
                    ⚡ NEW-HIGH TERMINAL
                </h1>
            </div>
            <div class="flex items-center gap-3">
                <button onclick="toggleWatchlistModal()" class="text-xs bg-slate-950 border border-fuchsia-500 hover:bg-fuchsia-950 text-fuchsia-400 font-bold px-3 py-2 transition flex items-center gap-1 shadow-[0_0_10px_rgba(217,70,239,0.4)]">
                    <span>⭐ WATCHLIST</span>
                    <span class="text-yellow-400">[ <span id="watch-count">0</span> ]</span>
                </button>
                <div class="text-xs text-slate-400 font-mono border border-slate-800 p-2 bg-slate-950/80 hidden sm:block">
                    UPDATED: <span class="text-yellow-400 font-bold">{today_display}</span>
                </div>
            </div>
        </header>
        
        <!-- LATEST REPORT -->
        <section class="bg-slate-950/90 border border-cyan-500/60 neon-glow-cyan p-6 space-y-4">
            <div class="flex justify-between items-center border-b border-cyan-500/30 pb-3">
                <h2 class="text-lg md:text-xl font-bold text-cyan-400 tracking-wide flex items-center gap-2">
                    <span>🔥 LATEST SCREENING CARDS</span>
                    <span class="text-xs text-fuchsia-400 border border-fuchsia-500/50 px-2 py-0.5">{today_display}</span>
                </h2>
                <a href="reports/{today_str}.html" class="text-xs bg-fuchsia-600 hover:bg-fuchsia-500 text-black font-bold px-3 py-1.5 transition shadow-[0_0_10px_rgba(217,70,239,0.5)]">
                    EXPAND FULL CARDS ↗
                </a>
            </div>
            <div class="space-y-4 max-h-[600px] overflow-y-auto pr-1">
                {cards_html}
            </div>
        </section>
        
        <!-- ARCHIVE LOGS -->
        <section class="space-y-4">
            <h2 class="text-lg font-bold text-fuchsia-400 tracking-wider flex items-center gap-2">
                <span>📂 SYSTEM ARCHIVES</span>
                <span class="text-xs text-slate-500">// HISTORICAL LOGS</span>
            </h2>
            <ul class="grid grid-cols-1 md:grid-cols-2 gap-3">{archive_links}</ul>
        </section>
        
    </div>
    {watchlist_modal_html}
    {watchlist_js}
</body>
</html>"""

    index_file_path = os.path.join(docs_dir, "index.html")
    with open(index_file_path, "w", encoding="utf-8") as f:
        f.write(index_html)
        
    print("【成功】カード型UI＆JSON出力対応ダッシュボードの生成が完了しました。")
    return line_text

def send_line_push_message(line_text):
    """LINE Messaging API経由で個人アカウントへプッシュ通知を送信します"""
    line_access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    line_user_id = os.environ.get("LINE_USER_ID", "").strip()
    
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {line_access_token}"
    }
    
    payload = {
        "to": line_user_id,
        "messages": [
            {
                "type": "text",
                "text": line_text[:4500]
            }
        ]
    }
    
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=15)
        if res.status_code == 200:
            print("【成功】LINEへのレポート送信が正常に完了しました。")
        else:
            print(f"【エラー】LINE送信エラー (Status {res.status_code}): {res.text}")
            sys.exit(1)
    except Exception as e:
        print(f"【エラー】LINE通信処理中に例外が発生しました: {e}")
        sys.exit(1)

def main():
    print("1. 環境変数のチェック中...")
    check_env_vars()
    
    print("2. Yahoo!ファイナンスから新高値更新銘柄データを取得中...")
    stock_data, stock_dict = fetch_new_high_stocks()
    
    print("3. Gemini APIでJSON構造化スクリーニング分析中...")
    json_data = generate_analysis_report(stock_data)
    
    print("4. カード型サイバーパンク風ダッシュボード＆過去ログを自動生成中...")
    line_message = create_dashboard_html(json_data, stock_dict)
    
    print("5. LINEへレポートを配信中...")
    send_line_push_message(line_message)

if __name__ == "__main__":
    main()
