"""Tests for the persistent list of emailed job ads."""

from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from src.search import JobMatch
from src.sent_ads import load_sent_ads, record_sent_ads


class SentAdsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "job_log_2027.toml"
        self.match = JobMatch(
            title="Finance internship",
            url="https://example.com/jobs/123",
            snippet="Example role",
            source="Company careers",
            score=18,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_missing_log_starts_empty(self) -> None:
        self.assertEqual(load_sent_ads(self.path), [])

    def test_records_a_successfully_sent_ad(self) -> None:
        added = record_sent_ads(self.path, [self.match], "2026-09-07T12:00:00+08:00")

        self.assertEqual(added, 1)
        self.assertEqual(
            load_sent_ads(self.path),
            [
                {
                    "title": "Finance internship",
                    "url": "https://example.com/jobs/123",
                    "source": "Company careers",
                    "sent_at": "2026-09-07T12:00:00+08:00",
                }
            ],
        )
        with self.path.open("rb") as log_file:
            self.assertEqual(len(tomllib.load(log_file)["sent_ads"]), 1)

    def test_preserves_applications_in_the_shared_log(self) -> None:
        self.path.write_text(
            '[[applications]]\ncompany = "Example"\nrole = "Intern"\n'
            'status = "applied"\nverification = "confirmed"\n',
            encoding="utf-8",
        )

        record_sent_ads(self.path, [self.match], "2026-09-07T12:00:00+08:00")

        with self.path.open("rb") as log_file:
            data = tomllib.load(log_file)
        self.assertEqual(data["applications"][0]["company"], "Example")
        self.assertEqual(len(data["sent_ads"]), 1)

    def test_does_not_record_the_same_url_twice(self) -> None:
        record_sent_ads(self.path, [self.match], "2026-09-07T12:00:00+08:00")
        added = record_sent_ads(self.path, [self.match], "2026-09-08T12:00:00+08:00")

        self.assertEqual(added, 0)
        self.assertEqual(len(load_sent_ads(self.path)), 1)


if __name__ == "__main__":
    unittest.main()
