"""Tests for the optional OpenAI relevance layer."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.ai_relevance import (
    AIEvaluation,
    AIConfig,
    ai_log_line,
    candidate_context,
    delivery_decision,
    evaluate_matches,
)
from src.main import apply_ai_relevance
from src.search import (
    AI_FILTERED,
    APPROVED,
    JobMatch,
    SearchDiagnostic,
    SearchRun,
    write_diagnostics,
)


VALID_ASSESSMENT = {
    "relevance": "high",
    "track": "strategy",
    "job_type": "internship",
    "is_direct_job_listing": True,
    "reason": "Relevant strategi- og analyseerfaring.",
    "red_flags": {
        "wrong_experience_level": "false",
        "wrong_location": "false",
        "wrong_year": "false",
    },
    "insufficient_information": False,
}


class FakeResponses:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class AIRelevanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.match = JobMatch(
            title="Strategy internship",
            url="https://example.com/job/1",
            snippet="A" * 500,
            source="example.com",
            score=20,
            search_track="Strategy",
        )
        self.profile = {
            "candidate": {
                "degree": "Bachelor of Finance",
                "school": "BI",
                "study_stage": "Final year",
                "availability": "Summer 2027",
                "name": "SHOULD_NOT_BE_SENT",
                "address": "SHOULD_NOT_BE_SENT",
            },
            "background": {"highlights": ["Analytics internship"], "skills": ["Python"]},
            "search": {
                "year": 2027,
                "locations": ["Oslo"],
                "job_types": ["internship", "graduate", "part-time", "student"],
                "primary_roles": ["strategy internship"],
                "secondary_roles": [],
            },
        }

    def client(self, output=VALID_ASSESSMENT, error=None):
        response = SimpleNamespace(
            output_text=json.dumps(output),
            usage=SimpleNamespace(input_tokens=120, output_tokens=40, total_tokens=160),
        )
        responses = FakeResponses(response, error)
        return SimpleNamespace(responses=responses), responses

    def test_returns_valid_structured_assessment_and_usage(self) -> None:
        client, responses = self.client()
        config = AIConfig(enabled=True, max_text_chars=200)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
            evaluations = evaluate_matches(
                [self.match],
                self.profile,
                config,
                client_factory=lambda _key, _timeout: client,
            )

        self.assertEqual(evaluations[0].status, "success")
        self.assertEqual(evaluations[0].assessment, VALID_ASSESSMENT)
        self.assertEqual(evaluations[0].usage["total_tokens"], 160)
        request = responses.calls[0]
        self.assertEqual(request["model"], "gpt-5.6-terra")
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertFalse(request["store"])
        sent_payload = json.loads(request["input"])
        self.assertEqual(len(sent_payload["job"]["listing_excerpt"]), 200)
        self.assertNotIn("SHOULD_NOT_BE_SENT", request["input"])
        self.assertEqual(
            sent_payload["candidate_profile"]["target_job_types"],
            ["internship", "graduate", "part-time", "student"],
        )

    def test_disabled_ai_does_not_create_a_client(self) -> None:
        factory_called = False

        def factory(_key, _timeout):
            nonlocal factory_called
            factory_called = True

        evaluations = evaluate_matches(
            [self.match], self.profile, AIConfig(enabled=False), client_factory=factory
        )

        self.assertFalse(factory_called)
        self.assertEqual(evaluations[0].status, "disabled")

    def test_missing_key_falls_back_safely(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            evaluations = evaluate_matches([self.match], self.profile, AIConfig(enabled=True))

        self.assertEqual(evaluations[0].status, "missing_key")
        self.assertIsNone(evaluations[0].assessment)

    def test_api_error_does_not_expose_exception_or_key(self) -> None:
        secret = "sk-secret-value"
        client, _responses = self.client(error=TimeoutError(f"timeout with {secret}"))
        with patch.dict("os.environ", {"OPENAI_API_KEY": secret}, clear=True):
            evaluations = evaluate_matches(
                [self.match],
                self.profile,
                AIConfig(enabled=True),
                client_factory=lambda _key, _timeout: client,
            )

        serialized = json.dumps(evaluations[0].diagnostic_payload())
        self.assertEqual(evaluations[0].status, "api_error")
        self.assertNotIn(secret, serialized)
        self.assertNotIn("timeout with", serialized)

    def test_invalid_model_response_falls_back_safely(self) -> None:
        client, _responses = self.client(output={"relevance": "high"})
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
            evaluations = evaluate_matches(
                [self.match],
                self.profile,
                AIConfig(enabled=True),
                client_factory=lambda _key, _timeout: client,
            )

        self.assertEqual(evaluations[0].status, "invalid_response")
        self.assertIsNone(evaluations[0].assessment)

    def test_candidate_limit_skips_extra_calls(self) -> None:
        client, responses = self.client()
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
            evaluations = evaluate_matches(
                [self.match, self.match],
                self.profile,
                AIConfig(enabled=True, max_candidates=1),
                client_factory=lambda _key, _timeout: client,
            )

        self.assertEqual(
            [item.status for item in evaluations], ["success", "skipped_limit"]
        )
        self.assertEqual(len(responses.calls), 1)

    def test_shadow_result_never_changes_delivery_selection(self) -> None:
        diagnostic = SearchDiagnostic(
            search_track=self.match.search_track,
            title=self.match.title,
            original_url=self.match.url,
            normalized_url=self.match.url,
            domain=self.match.source,
            score=self.match.score,
            decision=APPROVED,
            reason="Approved",
        )
        search_run = SearchRun(
            matches=[self.match], diagnostics=[diagnostic], search_tracks=["Strategy"]
        )
        original_matches = list(search_run.matches)
        fake_evaluation = SimpleNamespace(
            diagnostic_payload=lambda: {
                "status": "success",
                "model": "test-model",
                "assessment": {**VALID_ASSESSMENT, "relevance": "low"},
                "usage": {"total_tokens": 10},
                "error": "",
            },
            status="success",
            usage={"total_tokens": 10},
        )

        with patch("src.main.evaluate_matches", return_value=[fake_evaluation]):
            apply_ai_relevance(
                search_run, self.profile, AIConfig(enabled=True, mode="shadow")
            )

        self.assertEqual(search_run.matches, original_matches)
        self.assertEqual(search_run.diagnostics[0].decision, APPROVED)
        self.assertEqual(search_run.diagnostics[0].ai_assessment["relevance"], "low")

        with tempfile.TemporaryDirectory() as directory:
            path = write_diagnostics(
                search_run, Path(directory) / "diagnostics.json", "2026-09-07 18:00 HKT"
            )
            diagnostics = path.read_text(encoding="utf-8")
        self.assertIn('"ai_relevance"', diagnostics)
        self.assertIn('"ai_assessment"', diagnostics)
        self.assertNotIn("candidate_profile", diagnostics)
        self.assertNotIn("OPENAI_API_KEY", diagnostics)

    def test_active_mode_delivers_only_fully_validated_matches(self) -> None:
        matches = [self.match, JobMatch(
            title="Analyst journal",
            url="https://example.com/journal/analyst",
            snippet="Research journal",
            source="example.com",
            score=5,
            search_track="Analytics",
        )]
        diagnostics = [
            SearchDiagnostic(
                search_track=match.search_track,
                title=match.title,
                original_url=match.url,
                normalized_url=match.url,
                domain=match.source,
                score=match.score,
                decision=APPROVED,
                reason="Approved",
            )
            for match in matches
        ]
        search_run = SearchRun(matches=matches, diagnostics=diagnostics)
        accepted = AIEvaluation(
            status="success", model="test", assessment=VALID_ASSESSMENT
        )
        rejected = AIEvaluation(
            status="success",
            model="test",
            assessment={
                **VALID_ASSESSMENT,
                "relevance": "low",
                "job_type": "unknown",
                "is_direct_job_listing": False,
            },
        )

        with patch("src.main.evaluate_matches", return_value=[accepted, rejected]):
            apply_ai_relevance(
                search_run,
                self.profile,
                AIConfig(enabled=True, mode="active"),
            )

        self.assertEqual(len(search_run.matches), 1)
        self.assertEqual(search_run.matches[0].job_type, "internship")
        self.assertEqual(search_run.diagnostics[0].decision, APPROVED)
        self.assertEqual(search_run.diagnostics[1].decision, AI_FILTERED)
        self.assertEqual(search_run.ai_summary["delivered"], 1)
        self.assertEqual(search_run.ai_summary["filtered"], 1)

    def test_active_gate_fails_closed_on_location_uncertainty_or_api_error(self) -> None:
        uncertain = AIEvaluation(
            status="success",
            model="test",
            assessment={
                **VALID_ASSESSMENT,
                "red_flags": {**VALID_ASSESSMENT["red_flags"], "wrong_location": "uncertain"},
            },
        )

        self.assertFalse(delivery_decision(uncertain)[0])
        self.assertFalse(delivery_decision(AIEvaluation(status="api_error", model="test"))[0])

    def test_active_gate_accepts_every_requested_job_type(self) -> None:
        for job_type in ("internship", "summer_job", "graduate", "part_time", "student"):
            evaluation = AIEvaluation(
                status="success",
                model="test",
                assessment={**VALID_ASSESSMENT, "job_type": job_type},
            )
            self.assertTrue(delivery_decision(evaluation)[0], job_type)

    def test_candidate_context_is_explicitly_allowlisted(self) -> None:
        context = candidate_context(self.profile)

        self.assertEqual(context["degree"], "Bachelor of Finance")
        self.assertNotIn("name", context)
        self.assertNotIn("address", context)

    def test_log_line_contains_only_aggregate_status(self) -> None:
        line = ai_log_line(
            {
                "enabled": True,
                "model": "gpt-5.6-terra",
                "mode": "active",
                "status_counts": {"api_error": 1},
                "usage": {},
                "delivered": 0,
                "filtered": 1,
            }
        )
        self.assertEqual(
            line,
            "AI-relevans (active): modell=gpt-5.6-terra; api_error=1; "
            "godkjent=0, filtrert=1",
        )


if __name__ == "__main__":
    unittest.main()
