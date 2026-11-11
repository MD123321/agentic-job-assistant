"""Tests for filtering search results before they reach the daily report."""

from __future__ import annotations

import unittest
import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src.search import (
    APPROVED,
    DUPLICATE_IN_RUN,
    INVALID_RESULT,
    NOT_DIRECT,
    PREVIOUSLY_SENT,
    PRIOR_APPLICATION,
    REPORT_LIMIT,
    find_matches,
    funnel_log_lines,
    domain_allowed,
    is_direct_job_listing,
    normalize_url,
    resolve_search_plan,
    run_search,
    write_diagnostics,
)


class DirectJobListingTests(unittest.TestCase):
    def assert_direct(self, title: str, url: str) -> None:
        self.assertTrue(is_direct_job_listing({"title": title, "url": url}))

    def assert_not_direct(self, title: str, url: str) -> None:
        self.assertFalse(is_direct_job_listing({"title": title, "url": url}))

    def test_accepts_direct_finn_ad(self) -> None:
        self.assert_direct(
            "Internship Summer 2027 - FINN.no",
            "https://www.finn.no/job/ad/473747565",
        )

    def test_accepts_direct_linkedin_ad(self) -> None:
        self.assert_direct(
            "Project Finance Intern hos Fossefall",
            "https://no.linkedin.com/jobs/view/project-finance-intern-at-fossefall-4437164642",
        )

    def test_accepts_specific_company_job_page(self) -> None:
        self.assert_direct(
            "Summer Finance Intern 2027",
            "https://careers.example.com/job/oslo/summer-finance-intern-2027/12345",
        )

    def test_rejects_finn_search_page(self) -> None:
        self.assert_not_direct(
            "Ledige stillinger - bank, finans og forsikring - internship",
            "https://www.finn.no/job/search?industry=2&q=internship",
        )

    def test_rejects_linkedin_job_list(self) -> None:
        self.assert_not_direct(
            "71 Finance Internship-stillinger i Norge",
            "https://no.linkedin.com/jobs/finance-internship-jobs",
        )

    def test_rejects_indeed_job_list(self) -> None:
        self.assert_not_direct(
            "Internship, finans jobber i Oslo",
            "https://no.indeed.com/q-internship,-finans-l-oslo-jobber.html",
        )

    def test_rejects_iagora_job_list(self) -> None:
        self.assert_not_direct(
            "25 Internships in Oslo, Norway",
            "https://www.iagora.com/work/en/internships/norway/oslo",
        )

    def test_rejects_generic_internship_program_page(self) -> None:
        self.assert_not_direct(
            "Summer internship",
            "https://www.nbim.no/en/about-us/career/summer-internship",
        )

    def test_rejects_generic_company_career_page(self) -> None:
        self.assert_not_direct(
            "Career | Vend",
            "https://www.finn.no/jobbeifinn",
        )

    def test_rejects_missing_url(self) -> None:
        self.assertFalse(is_direct_job_listing({"title": "Summer intern"}))

    def test_rejects_homepages_and_media_files(self) -> None:
        self.assert_not_direct("Investment analyst", "https://analyst.example")
        self.assert_not_direct(
            "FBI Careers: Intelligence Analyst",
            "https://fbi.example/videos/intelligence-analyst.mp4",
        )

    def test_enforces_configured_portal_domain_after_search(self) -> None:
        self.assertTrue(domain_allowed("no.linkedin.com", ["linkedin.com"]))
        self.assertFalse(domain_allowed("youtube.com", ["linkedin.com"]))

    def test_excludes_a_previously_sent_url(self) -> None:
        result = {
            "title": "Summer Finance Intern 2027",
            "url": "https://careers.example.com/job/summer-finance-intern/12345",
            "content": "Finance internship in Oslo in 2027.",
        }
        profile = {
            "search": {
                "primary_roles": [],
                "secondary_roles": [],
            },
            "sources": {
                "queries": [
                    {"name": "Company careers", "query": "internship", "include_domains": []}
                ]
            },
        }
        with patch("src.search.tavily_search", return_value=[result]):
            matches = find_matches(profile, [], [{"url": result["url"]}])

        self.assertEqual(matches, [])


class SearchDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = {
            "search": {
                "primary_roles": ["strategy internship"],
                "secondary_roles": [],
            },
            "sources": {
                "queries": [
                    {
                        "name": "Strategi og analyse",
                        "query": "summer internship 2027 Oslo strategy",
                        "include_domains": [],
                    }
                ]
            },
        }

    def test_normalizes_only_safe_url_variants(self) -> None:
        self.assertEqual(
            normalize_url(
                "HTTPS://www.Example.com:443/jobs/123/?b=2&utm_source=test&a=1#apply"
            ),
            "https://example.com/jobs/123?a=1&b=2",
        )
        self.assertNotEqual(
            normalize_url("https://example.com/jobs/123?id=1"),
            normalize_url("https://example.com/jobs/123?id=2"),
        )

    def test_assigns_one_final_decision_with_defined_priority(self) -> None:
        results = [
            {"title": "Missing URL", "content": "internship"},
            {
                "title": "Open roles",
                "url": "https://example.com/jobs",
                "content": "internship",
            },
            {
                "title": "Already sent",
                "url": "https://example.com/jobs/sent/?utm_source=search",
                "content": "internship",
            },
            {
                "title": "Already sent duplicate",
                "url": "https://www.example.com/jobs/sent",
                "content": "internship",
            },
            {
                "title": "Acme Strategy Intern 2027",
                "url": "https://acme.example/jobs/strategy-2027",
                "content": "Acme strategy internship in Oslo",
            },
            {
                "title": "High score Strategy Intern 2027 Oslo",
                "url": "https://high.example/job/1",
                "content": "strategy internship",
            },
            {
                "title": "Lower score internship",
                "url": "https://low.example/job/2",
                "content": "internship",
            },
        ]
        applications = [
            {
                "company": "Acme",
                "role": "Strategy Internship",
                "verification": "confirmed",
            }
        ]
        with patch("src.search.tavily_search", return_value=results):
            search_run = run_search(
                self.profile,
                applications,
                [{"url": "https://example.com/jobs/sent"}],
                max_ads=1,
            )

        decisions = [item.decision for item in search_run.diagnostics]
        self.assertEqual(
            decisions,
            [
                INVALID_RESULT,
                NOT_DIRECT,
                PREVIOUSLY_SENT,
                DUPLICATE_IN_RUN,
                PRIOR_APPLICATION,
                APPROVED,
                REPORT_LIMIT,
            ],
        )
        self.assertEqual(sum(search_run.decision_counts().values()), len(results))
        self.assertEqual(len(search_run.matches), 1)
        self.assertEqual(search_run.matches[0].source, "high.example")
        self.assertEqual(search_run.matches[0].search_track, "Strategi og analyse")

    def test_diagnostics_excludes_search_snippets(self) -> None:
        result = {
            "title": "Strategy Intern 2027 Oslo",
            "url": "https://careers.example.com/job/strategy-intern/123",
            "content": "SECRET_SNIPPET_TEXT",
        }
        with patch("src.search.tavily_search", return_value=[result]):
            search_run = run_search(self.profile, [], max_ads=20)
        search_run.delivery_status = "sent"
        search_run.sent_count = 1
        search_run.logged_count = 1

        with tempfile.TemporaryDirectory() as directory:
            path = write_diagnostics(
                search_run,
                Path(directory) / "diagnostics.json",
                "2026-09-07 12:00 CEST",
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["summary"]["delivery"]["status"], "sent")
        self.assertEqual(payload["by_search_track"][0]["fetched"], 1)
        self.assertNotIn("SECRET_SNIPPET_TEXT", json.dumps(payload))
        self.assertNotIn("snippet", payload["results"][0])

    def test_reports_configured_search_tracks_with_zero_results(self) -> None:
        self.profile["sources"]["queries"].append(
            {
                "name": "Finans og investering",
                "query": "finance internship",
                "include_domains": [],
            }
        )
        with patch("src.search.tavily_search", side_effect=[[], []]):
            search_run = run_search(self.profile, [])

        lines = funnel_log_lines(search_run)
        self.assertIn("Søkespor Strategi og analyse: hentet=0", lines[1])
        self.assertIn("Søkespor Finans og investering: hentet=0", lines[2])

    def test_rotates_query_variants_without_increasing_daily_searches(self) -> None:
        profile = {
            "search": {
                "primary_roles": [],
                "secondary_roles": [],
                "rotation_start": "2026-09-16",
            },
            "sources": {
                "queries": [
                    {
                        "name": "Finance",
                        "variants": [
                            {"name": "IB", "query": "investment banking", "include_domains": []},
                            {"name": "Markets", "query": "capital markets", "include_domains": []},
                            {"name": "PE", "query": "private equity", "include_domains": []},
                        ],
                    },
                    {
                        "name": "Strategy",
                        "variants": [
                            {"name": "Consulting", "query": "consulting", "include_domains": []},
                            {"name": "Deals", "query": "transaction services", "include_domains": []},
                            {"name": "Analyst", "query": "business analyst", "include_domains": []},
                        ],
                    },
                ]
            },
        }

        with patch("src.search.tavily_search", return_value=[]) as tavily:
            first = run_search(profile, [], run_date=date(2026, 9, 16))
            second = run_search(profile, [], run_date=date(2026, 9, 17))
            fourth = run_search(profile, [], run_date=date(2026, 9, 19))

        self.assertEqual(tavily.call_count, 6)
        self.assertEqual(
            [item["variant"] for item in first.search_plan],
            ["IB", "Consulting"],
        )
        self.assertEqual(
            [item["variant"] for item in second.search_plan],
            ["Markets", "Deals"],
        )
        self.assertEqual(
            [item["variant"] for item in fourth.search_plan],
            ["IB", "Consulting"],
        )

    def test_search_plan_keeps_legacy_single_query_configuration(self) -> None:
        plan = resolve_search_plan(self.profile, date(2026, 9, 16))

        self.assertEqual(plan[0]["variant"], "fixed")
        self.assertEqual(plan[0]["query"], "summer internship 2027 Oslo strategy")

    def test_diagnostics_records_selected_query_variant_even_with_zero_results(self) -> None:
        self.profile["search"]["rotation_start"] = "2026-09-16"
        self.profile["sources"]["queries"][0] = {
            "name": "Strategi og analyse",
            "variants": [
                {"name": "Consulting", "query": "consulting internship", "include_domains": []},
                {"name": "Deals", "query": "deals internship", "include_domains": []},
            ],
        }
        with patch("src.search.tavily_search", return_value=[]):
            search_run = run_search(self.profile, [], run_date=date(2026, 9, 17))

        with tempfile.TemporaryDirectory() as directory:
            path = write_diagnostics(
                search_run,
                Path(directory) / "diagnostics.json",
                "2026-09-17 08:00 CEST",
            )
            track = json.loads(path.read_text(encoding="utf-8"))["by_search_track"][0]

        self.assertEqual(track["variant"], "Deals")
        self.assertEqual(track["query"], "deals internship")
        self.assertEqual(track["fetched"], 0)


if __name__ == "__main__":
    unittest.main()
