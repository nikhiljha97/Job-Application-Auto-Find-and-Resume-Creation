from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .models import JobPosting, ScoreResult


def notify_after_run(
    config: dict[str, Any],
    new_jobs: list[JobPosting],
    scores: dict[str, ScoreResult],
    excel_path: str | Path,
) -> None:
    if not new_jobs:
        print("No new matching jobs to notify.")
        return

    notification_config = config.get("notifications", {})
    if not any(channel.get("enabled") for channel in notification_config.values() if isinstance(channel, dict)):
        print(f"Notifications disabled. New matching jobs this run: {len(new_jobs)}")
        return

    top_n = int(config.get("notification_top_n", 10))
    subject = f"LinkedIn scanner: {len(new_jobs)} new matching jobs"
    text = build_summary(new_jobs[:top_n], scores, total_count=len(new_jobs))

    telegram = notification_config.get("telegram", {})
    if telegram.get("enabled"):
        _send_telegram(text, excel_path if telegram.get("send_excel", True) else None)

    email = notification_config.get("email", {})
    if email.get("enabled"):
        _send_email(email, subject, text, excel_path if email.get("send_excel", True) else None)

    whatsapp = notification_config.get("whatsapp_twilio", {})
    if whatsapp.get("enabled"):
        _send_whatsapp_twilio(text)


def build_summary(jobs: list[JobPosting], scores: dict[str, ScoreResult], total_count: int | None = None) -> str:
    count = total_count if total_count is not None else len(jobs)
    lines = [f"LinkedIn scanner found {count} new matching job(s).", ""]
    for idx, job in enumerate(jobs, start=1):
        score = scores[job.key()]
        company = f" - {job.company}" if job.company else ""
        lines.append(f"{idx}. {score.overall_score:.2f}/10 - {job.title}{company}")
        if job.location:
            lines.append(f"   Location: {job.location}")
        if score.resume_ats_score:
            lines.append(f"   Resume ATS: {score.resume_ats_score:.1f}%")
        lines.append(f"   {job.url}")
    if total_count and total_count > len(jobs):
        lines.append("")
        lines.append(f"Showing top {len(jobs)}. See Excel for all new and historical matches.")
    return "\n".join(lines)


def _send_telegram(message: str, excel_path: str | Path | None = None) -> None:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Install requests first: python -m pip install -r requirements.txt") from exc

    token = os.environ.get("JOB_SCANNER_TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("JOB_SCANNER_TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("Telegram notification skipped: set JOB_SCANNER_TELEGRAM_BOT_TOKEN and JOB_SCANNER_TELEGRAM_CHAT_ID.")
        return

    base = f"https://api.telegram.org/bot{token}"
    response = requests.post(f"{base}/sendMessage", data={"chat_id": chat_id, "text": message[:3900]}, timeout=30)
    response.raise_for_status()

    if excel_path and Path(excel_path).exists():
        with Path(excel_path).open("rb") as f:
            response = requests.post(
                f"{base}/sendDocument",
                data={"chat_id": chat_id, "caption": "Latest LinkedIn job scanner Excel"},
                files={"document": (Path(excel_path).name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                timeout=60,
            )
        response.raise_for_status()
    print("Telegram notification sent.")


def _send_email(email_config: dict[str, Any], subject: str, body: str, excel_path: str | Path | None = None) -> None:
    smtp_host = os.environ.get("JOB_SCANNER_SMTP_HOST") or email_config.get("smtp_host", "")
    smtp_port = int(os.environ.get("JOB_SCANNER_SMTP_PORT") or email_config.get("smtp_port", 587))
    username = os.environ.get("JOB_SCANNER_SMTP_USERNAME") or email_config.get("smtp_username", "")
    password = os.environ.get("JOB_SCANNER_SMTP_PASSWORD") or email_config.get("smtp_password", "")
    sender = os.environ.get("JOB_SCANNER_EMAIL_FROM") or email_config.get("from", username)
    recipient = os.environ.get("JOB_SCANNER_EMAIL_TO") or email_config.get("to", "")
    use_tls = bool(email_config.get("use_tls", True))

    if not smtp_host or not sender or not recipient:
        print("Email notification skipped: set SMTP host, sender, and recipient.")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body)

    if excel_path and Path(excel_path).exists():
        data = Path(excel_path).read_bytes()
        msg.add_attachment(
            data,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=Path(excel_path).name,
        )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=60) as server:
        if use_tls:
            server.starttls()
        if username and password:
            server.login(username, password)
        server.send_message(msg)
    print("Email notification sent.")


def _send_whatsapp_twilio(message: str) -> None:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Install requests first: python -m pip install -r requirements.txt") from exc

    account_sid = os.environ.get("JOB_SCANNER_TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("JOB_SCANNER_TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("JOB_SCANNER_TWILIO_FROM", "")
    to_number = os.environ.get("JOB_SCANNER_TWILIO_TO", "")
    if not all([account_sid, auth_token, from_number, to_number]):
        print("WhatsApp notification skipped: set Twilio WhatsApp environment variables.")
        return

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    response = requests.post(
        url,
        data={"From": from_number, "To": to_number, "Body": message[:1500]},
        auth=(account_sid, auth_token),
        timeout=30,
    )
    response.raise_for_status()
    print("WhatsApp notification sent via Twilio.")
