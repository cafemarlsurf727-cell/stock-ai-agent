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
    """Webサイト（GitHub Pages）用のHTMLダッシュボードと過去ログを作成します"""
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    today_str = now.strftime("%Y-%m-%d")
    today_display = now.strftime("%Y年%m月%d日")
    
    # docsフォルダ作成（GitHub Pagesの公開用ディレクトリ）
    docs_dir = "docs"
    reports_dir = os.path.join(docs_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    
    # 銘柄コード（4桁数字）をYahoo!ファイナンスのチャートリンクに自動変換
    linked_report = re.sub(
        r'\b(\d{4})\b',
        r'<a href="https://finance.yahoo.co.jp/quote/\1.T" target="_blank" class="text-cyan-400 underline font-mono hover:text-cyan-300">\1</a>',
        report_text
    )
    
    # 日別HTML生成
    report_html = f"""<!DOCTYPE html>
<html lang="ja" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{today_display} - 新高値分析レポート</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen p-4 md:p-8 font-sans">
    <div class="max-w-4xl mx-auto">
        <header class="mb-6 flex justify-between items-center border-b border-slate-700 pb-4">
            <div>
                <a href="../index.html" class="text-xs text-cyan-400 hover:underline">← ダッシュボードへ戻る</a>
                <h1 class="text-2xl font-bold mt-1">新高値精鋭レポート</h1>
                <p class="text-xs text-slate-400">{today_display} 17:30 更新</p>
            </div>
        </header>
        <main class="bg-slate-800 rounded-xl p-6 shadow-xl border border-slate-700 whitespace-pre-wrap leading-relaxed text-sm md:text-base">{linked_report}</main>
    </div>
</body>
</html>"""

    # 本日分のHTML保存
    today_file_path = os.path.join(reports_dir, f"{today_str}.html")
    with open(today_file_path, "w", encoding="utf-8") as f:
        f.write(report_html)
        
    # 過去ログファイル一覧を取得して降順ソート
    files = sorted(os.listdir(reports_dir), reverse=True)
    archive_links = ""
    for file in files:
        if file.endswith(".html"):
            date_part = file.replace(".html", "")
            archive_links += f'<li><a href="reports/{file}" class="block p-3 rounded-lg bg-slate-800 hover:bg-slate-700 transition text-slate-200 font-mono flex justify-between items-center"><span>📅 {date_part} のレポート</span><span class="text-xs text-cyan-400">閲覧 →</span></a></li>\n'
            
    # メインダッシュボード（index.html）生成
    index_html = f"""<!DOCTYPE html>
<html lang="ja" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>新高値ブレイク分析ダッシュボード</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen p-4 md:p-8 font-sans">
    <div class="max-w-4xl mx-auto space-y-8">
        <header class="border-b border-slate-700 pb-4">
            <h1 class="text-3xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 to-blue-500">📈 新高値分析ダッシュボード</h1>
            <p class="text-sm text-slate-400 mt-1">Gemini 3.6 Flash による自動スクリーニングアーカイブ</p>
        </header>
        
        <section class="bg-slate-800 rounded-xl p-6 shadow-xl border border-slate-700">
            <div class="flex justify-between items-center mb-4">
                <h2 class="text-xl font-bold text-cyan-400">🔥 最新レポート ({today_display})</h2>
                <a href="reports/{today_str}.html" class="text-xs bg-cyan-600 hover:bg-cyan-500 text-white px-3 py-1.5 rounded-lg transition">全画面で開く</a>
            </div>
            <div class="whitespace-pre-wrap leading-relaxed text-sm md:text-base border-t border-slate-700 pt-4 max-h-[500px] overflow-y-auto">{linked_report}</div>
        </section>
        
        <section class="space-y-4">
            <h2 class="text-xl font-bold text-slate-300">📂 過去ログ一覧</h2>
            <ul class="space-y-2">{archive_links}</ul>
        </section>
    </div>
</body>
</html>"""

    index_file_path = os.path.join(docs_dir, "index.html")
    with open(index_file_path, "w", encoding="utf-8") as f:
        f.write(index_html)
        
    print("【成功】HTMLダッシュボードと過去ログの生成が完了しました。")

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
    
    print("4. HTMLダッシュボード＆過去ログを自動生成中...")
    create_dashboard_html(report)
    
    print("5. LINEへレポートを配信中...")
    send_line_push_message(report)

if __name__ == "__main__":
    main()
