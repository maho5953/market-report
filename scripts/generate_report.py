#!/usr/bin/env python3
"""東京市場モーニングレポート自動生成スクリプト"""

import datetime
import os
import smtplib
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

import anthropic

try:
    from ddgs import DDGS
    HAS_DDG = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        HAS_DDG = True
    except ImportError:
        HAS_DDG = False
        print("WARNING: ddgs not installed", file=sys.stderr)

try:
    import markdown as md_lib
    from weasyprint import HTML as WeasyprintHTML
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

REPO_ROOT = Path(__file__).parent.parent
MODEL = "claude-opus-4-8"
MAX_TOKENS = 8096


def jst_today() -> datetime.date:
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).date()


def prev_business_day(date: datetime.date) -> datetime.date:
    delta = 1
    while True:
        c = date - datetime.timedelta(days=delta)
        if c.weekday() < 5:
            return c
        delta += 1


WEEKDAY_JP = ['月', '火', '水', '木', '金', '土', '日']


def web_search(query: str, max_results: int = 6) -> str:
    if not HAS_DDG:
        return "検索ツール未インストール"
    for attempt in range(3):
        try:
            time.sleep(1.0 * (attempt + 1))
            with DDGS() as ddgs:
                # ニュース検索を優先（最新市場データに強い）
                results = list(ddgs.news(query, max_results=max_results))
                if not results:
                    # フォールバック: テキスト検索
                    results = list(ddgs.text(query, max_results=max_results))
            if not results:
                return "検索結果なし"
            return "\n\n".join(
                f"【{r['title']}】\n{r.get('url', r.get('href', ''))} \n{r.get('body', r.get('excerpt', ''))}"
                for r in results
            )
        except Exception as e:
            if attempt == 2:
                return f"検索エラー: {e}"
    return "検索失敗"


def run_agent(today: datetime.date, prev_bd: datetime.date) -> str:
    client = anthropic.Anthropic()

    tools = [
        {
            "name": "web_search",
            "description": "最新の株式市場・為替・ニュースをWeb検索します。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "検索クエリ（日本語推奨）"},
                    "max_results": {"type": "integer", "default": 6}
                },
                "required": ["query"]
            }
        }
    ]

    system = (
        "あなたは東京株式市場の専門アナリストです。"
        "web_searchツールで最新データを収集し、日本語のモーニングレポートを作成します。"
        "検索は日本語と英語の両方を試し、kabutan.jp・nikkei.com・bloomberg.co.jp・minkabu.jp・zaikei.co.jpなど"
        "信頼性の高い金融ニュースサイトの結果を優先してください。"
        "1回の検索で結果が不十分な場合は、クエリを変えて複数回検索してください。"
        "調査完了後、Markdownのレポート本文だけを出力してください。説明文・コメント不要。"
    )

    prev_bd_str = f"{prev_bd.month}月{prev_bd.day}日"
    prev_bd_ymd = prev_bd.strftime('%Y年%m月%d日')
    prev_bd_slash = prev_bd.strftime('%Y/%m/%d')
    today_str = f"{today.strftime('%Y年%m月%d日')}({WEEKDAY_JP[today.weekday()]})"

    prompt = f"""今日は{today_str}です。前営業日は{prev_bd_ymd}です。

以下の手順でweb_searchを使って情報を収集し、モーニングレポートを作成してください。

■ 推奨検索クエリ（これを参考に実際の日付で検索してください）
- 「日経平均 大引け {prev_bd_str}」
- 「{prev_bd_slash} 日経平均 終値 寄与度」
- 「{prev_bd_str} 東証 値上がり 値下がり ランキング」
- 「{prev_bd_str} ニューヨーク ダウ S&P500 ナスダック」
- 「SOX指数 半導体 {prev_bd_str}」
- 「シカゴ日経先物 ドル円 {prev_bd_str}」
- 「Nikkei 225 futures {prev_bd.strftime('%B %d %Y')}」（英語クエリも試す）

■ 収集項目
1. 前営業日({prev_bd_str})の東証:
   - 日経平均 終値・前日比・売買代金
   - 値上がり上位銘柄(プライム・売買代金上位中心)と理由 ※理由不明は「材料不明」
   - 値下がり上位銘柄と理由(同条件)
   - 日経平均寄与度ランキング

2. 昨夜の米国市場({prev_bd_str}):
   - ダウ・S&P500・ナスダック 終値と前日比
   - SOX指数(フィラデルフィア半導体株指数)
   - エヌビディアなど主要半導体株の動き
   - 騰落の主な理由

3. シカゴ日経平均先物 清算値(大証日中終値比) とドル円レート

4. 本日の東京市場の見通し

■ 出力フォーマット(Markdownのみ)
- タイトル: `# 東京市場モーニングレポート({today_str})`
- ① 値上がり上位 / ② 値下がり上位 / ③ 昨夜の米国市場 / ④ 本日の見通し
- ③は指数・終値・前日比の表を含める
- スマホで読みやすい長さ（簡潔に）
- 末尾に `### 主な参照元` とURL一覧
- 最後に投資は自己責任の注記"""

    messages = [{"role": "user", "content": prompt}]

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tools,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            return "\n".join(b.text for b in response.content if b.type == "text")

        if response.stop_reason == "tool_use":
            results = []
            for block in response.content:
                if block.type == "tool_use" and block.name == "web_search":
                    res = web_search(
                        block.input["query"],
                        block.input.get("max_results", 6)
                    )
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": res,
                    })
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": results})
        else:
            break

    return ""


def to_html(report_md: str, date: datetime.date, for_pdf: bool = False) -> str:
    body = md_lib.markdown(report_md, extensions=["tables"])
    wd = WEEKDAY_JP[date.weekday()]
    title = f"東京市場モーニングレポート {date.strftime('%Y年%m月%d日')}({wd})"

    if for_pdf:
        style_tag = f"<style>{PDF_CSS}</style>"
        back = ""
    else:
        style_tag = '<link rel="stylesheet" href="style.css">'
        back = '<p class="back"><a href="index.html">← レポート一覧へ</a></p>'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{style_tag}
</head>
<body>
<div class="container">
{body}
{back}
</div>
</body>
</html>"""


def update_index(docs_dir: Path):
    reports = sorted(docs_dir.glob("????-??-??.html"), reverse=True)
    items = []
    for p in reports:
        d = datetime.date.fromisoformat(p.stem)
        wd = WEEKDAY_JP[d.weekday()]
        items.append(f'<li><a href="{p.name}">{d.strftime("%Y年%m月%d日")}({wd})</a></li>')

    items_html = "\n".join(items) if items else "<li>レポートなし</li>"

    (docs_dir / "index.html").write_text(f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>東京市場モーニングレポート</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="container">
<h1>東京市場モーニングレポート</h1>
<ul class="report-list">
{items_html}
</ul>
</div>
</body>
</html>""", encoding="utf-8")


def ensure_css(docs_dir: Path):
    css_path = docs_dir / "style.css"
    if not css_path.exists():
        css_path.write_text(WEB_CSS, encoding="utf-8")


def send_email(subject: str, html_body: str, pdf_path: Path = None):
    """Gmail SMTPでメール送信。環境変数未設定の場合はスキップ。"""
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    recipient  = os.environ.get("GMAIL_RECIPIENT") or gmail_user

    if not gmail_user or not gmail_pass:
        print("Gmail credentials not set, skipping email.")
        return

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = gmail_user
    msg["To"]      = recipient

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if pdf_path and pdf_path.exists():
        with open(pdf_path, "rb") as f:
            part = MIMEBase("application", "pdf")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{pdf_path.name}"'
        )
        msg.attach(part)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, recipient, msg.as_bytes())
        print(f"Email sent to {recipient}")
    except Exception as e:
        print(f"Email failed (non-fatal): {e}", file=sys.stderr)


def main():
    today = jst_today()
    prev_bd = prev_business_day(today)
    date_str = today.strftime("%Y-%m-%d")
    print(f"Generating report for {date_str} (prev business day: {prev_bd})...")

    report_md = run_agent(today, prev_bd)
    if not report_md.strip():
        print("ERROR: Empty report", file=sys.stderr)
        sys.exit(1)

    # Markdown
    md_path = REPO_ROOT / f"{date_str}.md"
    md_path.write_text(report_md, encoding="utf-8")
    print(f"Saved: {md_path}")

    # HTML (GitHub Pages / mobile)
    docs_dir = REPO_ROOT / "docs"
    docs_dir.mkdir(exist_ok=True)
    ensure_css(docs_dir)

    html_content = to_html(report_md, today)
    html_path = docs_dir / f"{date_str}.html"
    html_path.write_text(html_content, encoding="utf-8")
    print(f"Saved: {html_path}")

    update_index(docs_dir)
    print("Updated: docs/index.html")

    # PDF
    pdf_path = None
    if HAS_PDF:
        try:
            pdf_path = REPO_ROOT / f"{date_str}.pdf"
            WeasyprintHTML(string=to_html(report_md, today, for_pdf=True)).write_pdf(str(pdf_path))
            print(f"Saved: {pdf_path}")
        except Exception as e:
            print(f"PDF generation skipped: {e}", file=sys.stderr)
            pdf_path = None

    # メール送信
    wd = WEEKDAY_JP[today.weekday()]
    subject = f"📈 東京市場モーニングレポート {today.strftime('%Y年%m月%d日')}({wd})"
    send_email(subject, html_content, pdf_path)

    print("Done.")


WEB_CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue",
                 "Hiragino Sans", "IPAGothic", sans-serif;
    font-size: 15px;
    line-height: 1.8;
    color: #1a1a1a;
    background: #f5f5f7;
}
.container {
    max-width: 680px;
    margin: 0 auto;
    padding: 16px;
    background: #fff;
    min-height: 100vh;
}
h1 {
    font-size: 18px;
    border-bottom: 3px solid #1a5276;
    padding-bottom: 8px;
    margin: 16px 0 12px;
    line-height: 1.4;
}
h2 {
    font-size: 14px;
    font-weight: bold;
    background: #eaf2f8;
    border-left: 5px solid #1a5276;
    padding: 6px 10px;
    margin: 20px 0 10px;
}
h3 { font-size: 13px; margin: 14px 0 6px; color: #333; }
p  { margin: 8px 0; font-size: 14px; }
ul, ol { padding-left: 20px; margin: 8px 0; }
li { margin: 5px 0; font-size: 14px; }
table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 13px; }
th, td { border: 1px solid #ccc; padding: 6px 8px; }
th { background: #d6e4f0; font-weight: bold; }
td:nth-child(2), td:nth-child(3) { text-align: right; }
hr  { border: none; border-top: 1px solid #ddd; margin: 16px 0; }
a   { color: #1a5276; }
em  { color: #555; font-style: normal; font-size: 13px; }
.back { margin-top: 24px; font-size: 13px; }
.report-list { list-style: none; padding: 0; }
.report-list li { border-bottom: 1px solid #eee; padding: 14px 0; }
.report-list a  { text-decoration: none; color: #1a5276; font-weight: bold; font-size: 16px; }
@media (max-width: 480px) {
    h1 { font-size: 16px; }
    table { font-size: 12px; }
    th, td { padding: 4px 6px; }
}
"""

PDF_CSS = """\
@page { size: A4; margin: 18mm 15mm; }
body {
    font-family: "IPAGothic", sans-serif;
    font-size: 10.5pt; line-height: 1.7; color: #222;
    background: #fff;
}
.container { max-width: 100%; padding: 0; }
h1 { font-size: 16pt; border-bottom: 3px solid #1a5276; padding-bottom: 6px; }
h2 { font-size: 12.5pt; background: #eaf2f8; border-left: 6px solid #1a5276;
     padding: 4px 8px; margin-top: 18px; }
h3 { font-size: 11pt; margin-top: 14px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; }
th, td { border: 1px solid #999; padding: 4px 8px; font-size: 10pt; }
th { background: #d6e4f0; }
li { margin: 3px 0; }
hr { border: none; border-top: 1px solid #bbb; margin: 14px 0; }
a  { color: #1a5276; text-decoration: none; }
.back { display: none; }
"""

if __name__ == "__main__":
    main()
