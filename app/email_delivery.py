"""Minimal SMTP delivery for reader email verification."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

from .settings import Settings


def smtp_configured(settings: Settings) -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def send_verification(settings: Settings, email: str, token: str) -> bool:
    if not smtp_configured(settings):
        return False
    password = ""
    if settings.smtp_password_file:
        password = Path(settings.smtp_password_file).read_text(encoding="utf-8").strip()
    link = f"{settings.public_origin}/account?verify={quote(token, safe='')}"
    message = EmailMessage()
    message["Subject"] = "验证你的 OOH Story 账户"
    message["From"] = settings.smtp_from
    message["To"] = email
    message.set_content(
        "欢迎来到 OOH Story。请在 24 小时内打开以下链接完成邮箱验证：\n\n"
        f"{link}\n\n如果不是你本人注册，请忽略此邮件。"
    )
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
        if settings.smtp_starttls:
            client.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            client.login(settings.smtp_username, password)
        client.send_message(message)
    return True
