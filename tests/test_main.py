"""Tests for delivery status and sent-ad logging in the complete workflow."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src.main import main, selected_search_date
from src.search import APPROVED, JobMatch, SearchDiagnostic, SearchRun


class MainWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        directory = Path(self.temporary_directory.name)
        self.report_path = directory / "report.md"
        self.report_path.write_text("# Report\n", encoding="utf-8")
        self.diagnostics_path = directory / "diagnostics.json"
        self.match = JobMatch(
            title="Strategy internship",
            url="https://example.com/job/1",
            snippet="Example",
            source="example.com",
            score=20,
            search_track="Strategy",
        )

    def search_run(self) -> SearchRun:
        diagnostic = SearchDiagnostic(
            search_track="Strategy",
            title=self.match.title,
            original_url=self.match.url,
            normalized_url=self.match.url,
            domain=self.match.source,
            score=self.match.score,
            decision=APPROVED,
            reason="Direkte annonse som ikke er behandlet tidligere.",
        )
        return SearchRun(
            matches=[self.match],
            diagnostics=[diagnostic],
            search_tracks=["Strategy"],
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def common_patches(self, search_run: SearchRun):
        return (
            patch("src.main.load_profile", return_value={"search": {"year": 2027}}),
            patch("src.main.load_application_log", return_value=[]),
            patch("src.main.load_sent_ads", return_value=[]),
            patch("src.main.run_search", return_value=search_run),
            patch("src.main.write_report", return_value=self.report_path),
            patch("src.main.DIAGNOSTICS_PATH", self.diagnostics_path),
        )

    def test_logs_ads_only_after_successful_email(self) -> None:
        search_run = self.search_run()
        patches = self.common_patches(search_run)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patch("src.main.send_report_email", return_value=True),
            patch("src.main.record_sent_ads", return_value=1) as record_sent_ads,
        ):
            main()

        record_sent_ads.assert_called_once()
        payload = json.loads(self.diagnostics_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["summary"]["delivery"]["status"], "sent")
        self.assertEqual(payload["summary"]["delivery"]["sent"], 1)
        self.assertEqual(payload["summary"]["delivery"]["logged"], 1)

    def test_email_failure_is_diagnosed_without_logging_ads(self) -> None:
        search_run = self.search_run()
        patches = self.common_patches(search_run)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patch("src.main.send_report_email", side_effect=RuntimeError("SMTP failed")),
            patch("src.main.record_sent_ads") as record_sent_ads,
            self.assertRaises(RuntimeError),
        ):
            main()

        record_sent_ads.assert_not_called()
        payload = json.loads(self.diagnostics_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["summary"]["delivery"]["status"], "email_failed")
        self.assertEqual(payload["summary"]["delivery"]["logged"], 0)

    def test_manual_rotation_date_override_is_explicit_and_validated(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                selected_search_date(date(2026, 9, 16)),
                date(2026, 9, 16),
            )
        with patch.dict("os.environ", {"SEARCH_ROTATION_DATE": "2026-09-18"}):
            self.assertEqual(
                selected_search_date(date(2026, 9, 16)),
                date(2026, 9, 18),
            )
        with patch.dict("os.environ", {"SEARCH_ROTATION_DATE": "day-three"}):
            with self.assertRaises(RuntimeError):
                selected_search_date(date(2026, 9, 16))


if __name__ == "__main__":
    unittest.main()
