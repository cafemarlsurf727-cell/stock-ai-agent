import os
import sys
import time
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
    """Yahoo!ファイナンスから本日年初来高値（新高値）更新銘柄データを取得します"""
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
    table = soup.find("table")
    
    if not table:
        print("【警告】新高値更新銘柄のテーブル要素が見つかりませんでした。")
        return "本日新高値更新銘柄のデータ取得に失敗しました。"
        
    rows = table.find_all("tr")
    formatted_data = []
    
    for row in rows:
        cols = [col.text.strip() for col in row.find_all(["th", "td"])]
        if cols:
            clean_cols = [" ".join(c.split()) for c in cols]
            formatted_data.append(" | ".join(clean_cols))
            
    if len(formatted_data) <= 1:
        return "本日新高値更新銘柄のデータが見つかりませんでした。"
        
    return "\n".join(formatted_data[:35])

def generate_analysis_report(stock_data_text):
    """Gemini APIで新高値銘柄のスクリーニング分析を実施します"""
    client = genai.Client()
    
    system_prompt = """
あなたは「新高値ブレイク投資法」の専門家です。
提供されたYahoo!ファイナンスの年初来高値更新銘柄リストから、業績背景・出来高・上昇モメンタムを考慮し、
「本物の新高値銘柄」をスクリーニングして簡潔なレポートを作成してください。

【出力フォーマット】
📊 本日の新高値精鋭レポート

🏆 最優先注目銘柄
・コード / 銘柄名 / 評価ランク（SまたはA）
・原動力（業績サプライズ・材料）
・テクニカル/出来高評価
・アクションプラン

🔍 その他の注目銘柄
・コード 銘柄名 (評価): 短評

💡 本日の総括・相場感
・市場傾向と観察のワンポイントアドバイス

※読みやすいよう適度に絵文字や改行を活用してください。
"""

    prompt = f"【本日の新高値更新銘柄データ】\n{stock_data_text}"
    
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[system_prompt, prompt]
            )
            return response.text
        except Exception as e:
            print(f"【警告】Gemini APIの試行 ({attempt}/{max_retries}) に失敗しました: {e}")
            if attempt < max_retries:
                time.sleep(10)
            else:
                print("【エラー】規定の再試行回数を超えたため処理を中断します。")
                sys.exit(1)

def create_dashboard_html(report_text):
    """Webサイト（GitHub Pages）用のサイバーパンク風HTMLダッシュボード（ワンタップ監視機能付き）を作成します"""
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    today_str = now.strftime("%Y-%m-%d")
    today_display = now.strftime("%Y.%m.%d")
    
    docs_dir = "docs"
    reports_dir = os.path.join(docs_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    
    # 銘柄コード（4桁数字）を「Yahoo!ファイナンスリンク ＋ ワンタップ⭐登録ボタン」に自動変換
    linked_report = re.sub(
        r'\b(\d{4})\b',
        r"""<span class="inline-flex items-center gap-1 mx-0.5"><a href="https://finance.yahoo.co.jp/quote/\1.T" target="_blank" class="text-fuchsia-400 font-bold hover:text-fuchsia-300 underline decoration-fuchsia-500 font-mono">[ \1 ]</a><button onclick="toggleInlineStock('\1', event)" class="text-xs hover:scale-125 transition-transform p-0.5 cursor-pointer" title="ワンタップで監視リストに登録/解除">⭐</button></span>""",
        report_text
    )
    
    # 共通JavaScript (監視リスト用)
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

        function toggleInlineStock(code, ev) {
            if (ev) ev.preventDefault();
            const index = watchlist.findIndex(item => item.code === code);
            
            if (index >= 0) {
                const removed = watchlist.splice(index, 1)[0];
                localStorage.setItem('cyber_stock_watchlist', JSON.stringify(watchlist));
                updateWatchlistCount();
                renderWatchlist();
                alert(`[ ${code} ] ${removed.name || ''} を監視リストから解除しました`);
            } else {
                let name = '銘柄 ' + code;
                if (ev && ev.currentTarget) {
                    const parentBlock = ev.currentTarget.closest('p, div, li, span') || ev.currentTarget.parentElement;
                    if (parentBlock) {
                        const text = parentBlock.textContent || '';
                        const match = text.match(new RegExp(code + '[\\\\s/|：:]*([^\\\\s\\\\n/()/（）:：A-Z]+)'));
                        if (match && match[1] && match[1].length > 1) {
                            name = match[1].trim();
                        }
                    }
                }
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
            const code = codeInput.value.trim();
            const name = nameInput.value.trim() || ('銘柄 ' + code);

            if (!code || !/^\d{4}$/.test(code)) {
                alert('4桁の銘柄コードを入力してください（例: 7203）');
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

        function removeStockFromWatchlist(code) {
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
                <li class="flex items-center justify-between p-2.5 bg-slate-900 border border-slate-800 hover:border-cyan-500/50 transition font-mono">
                    <a href="https://finance.yahoo.co.jp/quote/${item.code}.T" target="_blank" class="text-cyan-400 hover:text-fuchsia-400 font-bold flex items-center gap-2">
                        <span class="text-fuchsia-400">[ ${item.code} ]</span>
                        <span class="text-slate-200 text-sm hover:underline">${item.name}</span>
                        <span class="text-xs text-yellow-400">🔗</span>
                    </a>
                    <button onclick="removeStockFromWatchlist('${item.code}')" class="text-xs text-red-400 hover:text-white bg-red-950/60 hover:bg-red-600 border border-red-500/40 px-2.5 py-1 transition font-bold flex items-center gap-1 shadow-[0_0_5px_rgba(239,68,68,0.3)]">
                        🗑️ 監視解除
                    </button>
                </li>
            `).join('');
        }
    </script>
    """

    # 監視リストモーダルの共通HTML
    watchlist_modal_html = """
    <!-- WATCHLIST MODAL -->
    <div id="watchlist-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 hidden justify-center items-center p-4">
        <div class="bg-slate-950 border-2 border-fuchsia-500 shadow-[0_0_25px_rgba(217,70,239,0.4)] w-full max-w-md p-6 space-y-4 font-mono">
            <div class="flex justify-between items-center border-b border-fuchsia-500/40 pb-3">
                <h3 class="text-lg font-bold text-fuchsia-400 flex items-center gap-2">
                    <span>⭐ TARGET WATCHLIST</span>
                </h3>
                <button onclick="toggleWatchlistModal()" class="text-slate-400 hover:text-fuchsia-400 text-xl font-bold">✕</button>
            </div>

            <!-- ADD FORM -->
            <div class="space-y-2 bg-slate-900/80 p-3 border border-slate-800">
                <p class="text-xs text-cyan-400">ADD NEW WATCH TARGET:</p>
                <div class="flex gap-2">
                    <input id="input-code" type="text" placeholder="コード (7203)" maxlength="4" class="bg-black border border-cyan-500/50 text-cyan-400 text-xs p-2 w-24 focus:outline-none focus:border-cyan-400">
                    <input id="input-name" type="text" placeholder="銘柄名 (トヨタ)" class="bg-black border border-cyan-500/50 text-slate-200 text-xs p-2 flex-1 focus:outline-none focus:border-cyan-400">
                    <button onclick="addStockToWatchlist()" class="bg-fuchsia-600 hover:bg-fuchsia-500 text-black font-bold text-xs px-3 py-2 transition shadow-[0_0_10px_rgba(217,70,239,0.5)]">
                        + ADD
                    </button>
                </div>
            </div>

            <!-- WATCHLIST ITEMS -->
            <ul id="watchlist-items" class="space-y-2 max-h-60 overflow-y-auto pr-1"></ul>

            <div class="pt-2 text-right">
                <button onclick="toggleWatchlistModal()" class="text-xs text-slate-400 hover:text-slate-200 border border-slate-700 px-3 py-1">CLOSE</button>
            </div>
        </div>
    </div>
    """

    # 日別レポート用サイバーパンクHTML
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
        <main class="bg-slate-950/90 rounded-none p-6 shadow-[0_0_20px_rgba(217,70,239,0.2)] border border-fuchsia-500/50 whitespace-pre-wrap leading-relaxed text-slate-200 text-sm md:text-base border-l-4 border-l-fuchsia-500">{linked_report}</main>
    </div>
    {watchlist_modal_html}
    {watchlist_js}
</body>
</html>"""

    today_file_path = os.path.join(reports_dir, f"{today_str}.html")
    with open(today_file_path, "w", encoding="utf-8") as f:
        f.write(report_html)
        
    # 過去ログリンクのサイバー調表示
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
            
    # メインダッシュボード（index.html）サイバーパンクデザイン
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
        .neon-glow-fuchsia {{ box-shadow: 0 0 15px rgba(217, 70, 239, 0.3); }}
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
                    <span>🔥 LATEST SCREENING REPORT</span>
                    <span class="text-xs text-fuchsia-400 border border-fuchsia-500/50 px-2 py-0.5">{today_display}</span>
                </h2>
                <a href="reports/{today_str}.html" class="text-xs bg-fuchsia-600 hover:bg-fuchsia-500 text-black font-bold px-3 py-1.5 transition shadow-[0_0_10px_rgba(217,70,239,0.5)]">
                    EXPAND FULL SCREEN ↗
                </a>
            </div>
            <div class="whitespace-pre-wrap leading-relaxed text-sm md:text-base text-slate-200 border-l-2 border-fuchsia-500 pl-4 max-h-[500px] overflow-y-auto font-sans">{linked_report}</div>
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
        
    print("【成功】ワンタップ登録＆監視解除機能付きダッシュボードの生成が完了しました。")

def send_line_push_message(report_text):
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
                "text": report_text[:4500]
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
    stock_data = fetch_new_high_stocks()
    
    print("3. Gemini APIでスクリーニング分析中...")
    report = generate_analysis_report(stock_data)
    
    print("4. ワンタップ登録機能付きダッシュボード＆過去ログを自動生成中...")
    create_dashboard_html(report)
    
    print("5. LINEへレポートを配信中...")
    send_line_push_message(report)

if __name__ == "__main__":
    main()
