import random

def call_gemini_with_retry(client, model, contents_list, config=None, max_retries=6):
    """指数バックオフ＋ジッタ付きリトライ。429/5xx系のみ再試行し、それ以外は即座に失敗させる。"""
    for attempt in range(1, max_retries + 1):
        try:
            if config:
                return client.models.generate_content(model=model, contents=contents_list, config=config)
            return client.models.generate_content(model=model, contents=contents_list)
        except genai_errors.APIError as e:
            code = getattr(e, "code", None)
            retryable = code in (429, 500, 503, 504)
            if not retryable or attempt == max_retries:
                print(f"【エラー】Gemini API 失敗（リトライ対象外、または上限到達）: {e}")
                raise
            # 429/503（レート制限・過負荷）は長めの指数バックオフ、それ以外は短めの線形
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
