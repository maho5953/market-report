#!/usr/bin/env python3
"""既存のレポートをメール送信するだけのスクリプト（API不要）"""

import datetime
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

import markdown as md_lib

REPO_ROOT = Path(__file__).parent.parent
WEEKDAY_JP = ['月', '火', '水', '木', '金', '土', '日']


def to_html(report_md: str, date: datetime.date) -> str:
    body = md_lib.markdown(report_md, extensions=["tables"])
    wd = WEEKDAY_JP[date.weekday()]
    title = f"東京市場モーニングレポート {date.strftime('%Y年%m月%d日')}({wd})"
    css_path = REPO_ROOT / "docs" / "style.css"
    style = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{style}</style>
</head>
<body>
<div class="container">
{body}
</div>
</body>
</html>"""


def main():
    # 送信対象の日付（引数で指定、なければ今日）
    if len(sys.argv) > 1:
        date = datetime.date.fromisoformat(sys.argv[1])
    else:
        date = (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).date()

    date_str = date.strftime("%Y-%m-%d")
    md_path = REPO_ROOT / f"{date_str}.md"

    if not md_path.exists():
        print(f"ERROR: {md_path} が見つかりません", file=sys.stderr)
        sys.exit(1)

    report_md = md_path.read_text(encoding="utf-8")
    html_content = to_html(report_md, date)

    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    recipient  = os.environ.get("GMAIL_RECIPIENT") or gmail_user

    if not gmail_user or not gmail_pass:
        print("ERROR: GMAIL_USER / GMAIL_APP_PASSWORD が未設定", file=sys.stderr)
        sys.exit(1)

    wd = WEEKDAY_JP[date.weekday()]
    subject = f"📈 東京市場モーニングレポート {date.strftime('%Y年%m月%d日')}({wd})"

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = gmail_user
    msg["To"]      = recipient
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    # PDF添付（あれば）
    pdf_path = REPO_ROOT / f"{date_str}.pdf"
    if pdf_path.exists():
        with open(pdf_path, "rb") as f:
            part = MIMEBase("application", "pdf")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{pdf_path.name}"')
        msg.attach(part)

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, recipient, msg.as_bytes())

    print(f"Email sent to {recipient} ({date_str})")


if __name__ == "__main__":
    main()
