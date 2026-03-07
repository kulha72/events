"""
Gmail delivery — sends the HTML digest via SMTP over SSL.

Requires a Gmail App Password (not your account password).
Set up: Google Account -> Security -> 2-Step Verification -> App passwords.
Set GMAIL_APP_PASSWORD environment variable.
"""

import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_email(html_body: str, config: dict) -> None:
    """Send the digest email via Gmail SMTP."""
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not app_password:
        print("  [gmail] No GMAIL_APP_PASSWORD set — skipping email send.")
        return

    email_cfg = config.get("email", {})
    to_addr = email_cfg.get("to", "")
    from_addr = email_cfg.get("from", "")

    if not to_addr or not from_addr:
        print("  [gmail] email.to / email.from not set in config — skipping.")
        return

    subject = f"Daily Digest \u2014 {date.today().strftime('%a, %b %-d')}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    # Plain-text fallback (minimal)
    plain = f"Daily Digest — {date.today().strftime('%A, %B %-d, %Y')}\n\nOpen in a browser for the full digest."
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    print(f"  [gmail] Sending to {to_addr}...")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(from_addr, app_password)
            server.send_message(msg)
        print("  [gmail] Email sent.")
    except smtplib.SMTPAuthenticationError:
        print("  [gmail] ERROR: Authentication failed. Check GMAIL_APP_PASSWORD and that App Passwords are enabled.")
        raise
    except Exception as e:
        print(f"  [gmail] ERROR sending email: {e}")
        raise
