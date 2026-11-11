"""Tests for private local candidate-profile handling."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from application_assistant.local_service.profile import (
    MAX_PROFILE_BYTES,
    ai_candidate_context,
    load_candidate_profile,
    profile_values,
)


class ProfileTests(unittest.TestCase):
    def test_rejects_oversized_profile_before_parsing(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_bytes(b" " * (MAX_PROFILE_BYTES + 1))

            with self.assertRaisesRegex(RuntimeError, "1 MB"):
                load_candidate_profile(path)

    def test_ai_context_excludes_sensitive_approved_answers(self) -> None:
        profile = {
            "approved_answers": {
                "notice_period": "Not applicable",
                "visa_sponsorship": "No",
                "work_authorization": "Yes",
            }
        }

        context = ai_candidate_context(json.loads(json.dumps(profile)))

        self.assertEqual(
            context["approved_answers"],
            {"notice_period": "Not applicable"},
        )

    def test_derives_birth_year_and_graduation_from_documented_dates(self) -> None:
        values = profile_values(
            {
                "personal": {"date_of_birth": "2004-03-29"},
                "education": {"start_year": 2024, "duration_years": 3},
            }
        )

        self.assertEqual(values["birth_year"], "2004")
        self.assertEqual(values["graduation"], "2027")


if __name__ == "__main__":
    unittest.main()
