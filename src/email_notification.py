"""Send the daily job report through a configurable SMTP account."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, Protocol

from src.search import JOB_TYPE_LABELS


class JobMatchLike(Protocol):
    """Fields needed to include a match in the email summary."""

    title: str
    url: str
    source: str
    score: int
    job_type: str


def _enabled() -> bool:
    return os.getenv("EMAIL_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is missing while EMAIL_ENABLED is true.")
    return value


def _recipients(raw_recipients: str) -> list[str]:
    recipients = [address.strip() for address in raw_recipients.split(",") if address.strip()]
    if not recipients:
        raise RuntimeError("EMAIL_TO must contain at least one email address.")
    return recipients


def build_message(
    report_path: Path,
    matches: Iterable[JobMatchLike],
    run_time: str,
    sender: str,
    recipients: list[str],
    subject: str | None = None,
    funnel_line: str | None = None,
) -> EmailMessage:
    """Build a concise email and attach the complete Markdown report."""
    match_list = list(matches)
    message = EmailMessage()
    message["Subject"] = subject or (
        f"Jobbagent: {len(match_list)} nye treff – {report_path.stem}"
    )
    message["From"] = sender
    message["To"] = ", ".join(recipients)

    lines = [
        f"Jobbagenten kjørte {run_time} og fant {len(match_list)} nye, unike treff.",
        "",
    ]
    if match_list:
        lines.append("De høyest rangerte treffene:")
        for index, match in enumerate(match_list[:10], start=1):
            lines.extend(
                [
                    f"{index}. {match.title}",
                    f"   Jobbtype: {JOB_TYPE_LABELS.get(match.job_type, 'Ukjent')}",
                    f"   Kilde: {match.source} | Match-score: {match.score}",
                    f"   {match.url}",
                ]
            )
    else:
        lines.append("Ingen relevante offentlige treff i dagens søk.")
    if funnel_line:
        lines.extend(["", funnel_line])
    lines.extend(["", "Den komplette rapporten ligger vedlagt."])
    message.set_content("\n".join(lines))

    message.add_attachment(
        report_path.read_text(encoding="utf-8"),
        subtype="markdown",
        filename=report_path.name,
    )
    return message


def send_report_email(
    report_path: Path,
    matches: Iterable[JobMatchLike],
    run_time: str,
    funnel_line: str | None = None,
) -> bool:
    """Send the report when enabled, returning false when email is disabled."""
    if not _enabled():
        return False

    username = _required_environment("SMTP_USERNAME")
    password = _required_environment("SMTP_PASSWORD")
    recipients = _recipients(_required_environment("EMAIL_TO"))
    sender = os.getenv("EMAIL_FROM", username).strip() or username
    subject = os.getenv("EMAIL_SUBJECT", "").strip() or None
    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    try:
        port = int(os.getenv("SMTP_PORT", "465"))
    except ValueError as error:
        raise RuntimeError("SMTP_PORT must be a number.") from error

    message = build_message(
        report_path,
        matches,
        run_time,
        sender,
        recipients,
        subject=subject,
        funnel_line=funnel_line,
    )
    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(username, password)
            smtp.send_message(message, from_addr=sender, to_addrs=recipients)
    except (OSError, smtplib.SMTPException) as error:
        raise RuntimeError(f"Could not send the job report by email: {error}") from error
    return True
