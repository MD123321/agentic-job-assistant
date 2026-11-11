"""Tests for the email delivery step."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.email_notification import build_message, send_report_email
from src.search import JobMatch


class FakeSmtp:
    """Record SMTP calls without opening a network connection."""

    instance: "FakeSmtp"

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.login_args: tuple[str, str] | None = None
        self.send_args: tuple[str, tuple[str, ...]] | None = None
        FakeSmtp.instance = self

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, _message: object, from_addr: str, to_addrs: list[str]) -> None:
        self.send_args = (from_addr, tuple(to_addrs))


class EmailNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.report = Path(self.temporary_directory.name) / "2026-09-03.md"
        self.report.write_text("# Daglig jobbsøk\n", encoding="utf-8")
        self.matches = [
            JobMatch("Finance internship", "https://example.com/job", "Example", "Finn", 18)
        ]

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_build_message_includes_top_match_and_attachment(self) -> None:
        message = build_message(
            self.report,
            self.matches,
            "2026-09-03 10:00 CEST",
            "sender@example.com",
            ["recipient@example.com"],
        )

        self.assertIn("1 nye treff", message["Subject"])
        self.assertIn(
            "https://example.com/job",
            message.get_body(preferencelist=("plain",)).get_content(),
        )
        self.assertIn(
            "Jobbtype: Ukjent",
            message.get_body(preferencelist=("plain",)).get_content(),
        )
        attachment = next(message.iter_attachments())
        self.assertEqual(attachment.get_filename(), "2026-09-03.md")

    def test_build_message_accepts_a_custom_subject(self) -> None:
        message = build_message(
            self.report,
            self.matches,
            "2026-09-03 10:00 CEST",
            "sender@example.com",
            ["recipient@example.com"],
            "kvalitetestest",
        )

        self.assertEqual(message["Subject"], "kvalitetestest")

    def test_build_message_includes_one_short_funnel_line(self) -> None:
        funnel_line = "30 søketreff undersøkt · 6 direkte annonser · 1 nye annonser"
        message = build_message(
            self.report,
            self.matches,
            "2026-09-03 10:00 CEST",
            "sender@example.com",
            ["recipient@example.com"],
            funnel_line=funnel_line,
        )

        body = message.get_body(preferencelist=("plain",)).get_content()
        self.assertEqual(body.count(funnel_line), 1)

    def test_disabled_email_does_not_require_credentials(self) -> None:
        with patch.dict(os.environ, {"EMAIL_ENABLED": "false"}, clear=True):
            self.assertFalse(send_report_email(self.report, self.matches, "now"))

    def test_enabled_email_authenticates_and_sends(self) -> None:
        environment = {
            "EMAIL_ENABLED": "true",
            "SMTP_USERNAME": "sender@example.com",
            "SMTP_PASSWORD": "app-password",
            "EMAIL_TO": "one@example.com, two@example.com",
            "EMAIL_SUBJECT": "kvalitetestest",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("src.email_notification.smtplib.SMTP_SSL", FakeSmtp),
        ):
            self.assertTrue(send_report_email(self.report, self.matches, "now"))

        self.assertEqual(FakeSmtp.instance.host, "smtp.gmail.com")
        self.assertEqual(FakeSmtp.instance.port, 465)
        self.assertEqual(
            FakeSmtp.instance.login_args,
            ("sender@example.com", "app-password"),
        )
        self.assertEqual(
            FakeSmtp.instance.send_args,
            ("sender@example.com", ("one@example.com", "two@example.com")),
        )


if __name__ == "__main__":
    unittest.main()
