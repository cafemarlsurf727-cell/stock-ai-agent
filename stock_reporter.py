import os
import sys
import time
import requests
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
    """Gemini APIで新高値銘柄のスクリーニング分析を実施します（リトライ処理付き）"""
    client = genai.Client()
    
    system_prompt = """
あなたは「新高値ブレイク投資法」の専門家です。
提供されたYahoo!ファイナンスの年初来高値更新銘柄リストから、業績背景・出来高・上昇モメンタムを考慮し、
「本物の新高値銘柄」をスクリーニングして簡潔なLINE用レポートを作成してください。

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

※LINEメッセージとして読みやすいよう、適度に絵文字や改行を活用し、1,500文字程度に収めてください。
"""

    prompt = f"【本日の新高値更新銘柄データ】\n{stock_data_text}"
    
    # サーバー混雑対策：最大3回まで再試行
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[system_prompt, prompt]
            )
            return response.text
        except Exception as e:
            print(f"【警告】Gemini APIの試行 ({attempt}/{max_retries}) に失敗しました: {e}")
            if attempt < max_retries:
                print("10秒後に再試行します...")
                time.sleep(10)
            else:
                print("【エラー】規定の再試行回数を超えたため処理を中断します。")
                sys.exit(1)

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
    
    print("4. LINEへレポートを配信中...")
    send_line_push_message(report)

if __name__ == "__main__":
    main()
