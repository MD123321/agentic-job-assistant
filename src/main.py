"""Entry point for the daily job-search agent."""

from dataclasses import replace
from datetime import date, datetime
import os
from pathlib import Path
import tomllib

from src.ai_relevance import (
    AIConfig,
    ai_log_line,
    delivery_decision,
    evaluate_matches,
    summarize_evaluations,
)
from src.email_notification import send_report_email
from src.sent_ads import load_sent_ads, record_sent_ads
from src.search import (
    APPROVED,
    AI_FILTERED,
    funnel_log_lines,
    normalize_url,
    run_search,
    write_diagnostics,
    write_report,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = Path(
    os.getenv("JOB_AGENT_PROFILE", PROJECT_ROOT / "config" / "profile.toml")
)
JOB_LOG_PATH = Path(
    os.getenv("JOB_AGENT_LOG", PROJECT_ROOT / "data" / "job_log.toml")
)
DIAGNOSTICS_PATH = Path(
    os.getenv(
        "JOB_AGENT_DIAGNOSTICS",
        PROJECT_ROOT / "diagnostics" / "search-diagnostics.json",
    )
)
MAX_ADS_PER_REPORT = 20


def apply_ai_relevance(search_run, profile: dict, config: AIConfig) -> None:
    """Attach assessments and apply the strict production gate in active mode."""
    evaluations = evaluate_matches(search_run.matches, profile, config)
    approved_diagnostics = {
        item.normalized_url: item
        for item in search_run.diagnostics
        if item.decision == APPROVED
    }
    deliverable = []
    for match, evaluation in zip(search_run.matches, evaluations):
        diagnostic = approved_diagnostics.get(normalize_url(match.url))
        if diagnostic is None:
            continue
        payload = evaluation.diagnostic_payload()
        diagnostic.ai_status = payload["status"]
        diagnostic.ai_model = payload["model"]
        diagnostic.ai_assessment = payload["assessment"]
        diagnostic.ai_usage = payload["usage"]
        diagnostic.ai_error = payload["error"]
        if config.mode != "active":
            deliverable.append(match)
            continue
        accepted, reason = delivery_decision(evaluation)
        if accepted:
            deliverable.append(
                replace(match, job_type=evaluation.assessment["job_type"])
            )
        else:
            diagnostic.decision = AI_FILTERED
            diagnostic.reason = reason
    if config.mode == "active":
        search_run.matches = deliverable
    search_run.ai_summary = summarize_evaluations(config, evaluations)
    search_run.ai_summary["delivered"] = len(search_run.matches)
    search_run.ai_summary["filtered"] = sum(
        item.decision == AI_FILTERED for item in search_run.diagnostics
    )


def load_profile() -> dict:
    """Load the non-sensitive job-search profile."""
    with PROFILE_PATH.open("rb") as profile_file:
        return tomllib.load(profile_file)


def load_application_log() -> list[dict]:
    """Load confirmed prior applications to prevent duplicate suggestions."""
    if not JOB_LOG_PATH.exists():
        return []
    with JOB_LOG_PATH.open("rb") as log_file:
        return tomllib.load(log_file).get("applications", [])


def selected_search_date(default_date: date) -> date:
    """Use an explicit rotation date only for controlled manual test runs."""
    value = os.getenv("SEARCH_ROTATION_DATE", "").strip()
    if not value:
        return default_date
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise RuntimeError("SEARCH_ROTATION_DATE must use YYYY-MM-DD.") from error


def main() -> None:
    """Run the daily job search workflow."""
    profile = load_profile()
    prior_applications = load_application_log()
    sent_ads = load_sent_ads(JOB_LOG_PATH)
    search = profile["search"]
    run_datetime = datetime.now().astimezone()
    run_time = run_datetime.strftime("%Y-%m-%d %H:%M %Z")
    print(f"Jobbagenten kjørte: {run_time}")
    print(f"Søker etter relevante student- og inngangsstillinger for {search['year']}.")
    search_run = run_search(
        profile,
        prior_applications,
        sent_ads,
        max_ads=MAX_ADS_PER_REPORT,
        run_date=selected_search_date(run_datetime.date()),
    )
    ai_config = AIConfig.from_env()
    apply_ai_relevance(search_run, profile, ai_config)
    print(ai_log_line(search_run.ai_summary))
    report_path = write_report(search_run.matches)
    print(f"Fant {len(search_run.matches)} nye, unike treff. Rapport: {report_path}")
    for line in funnel_log_lines(search_run):
        print(line)

    try:
        email_sent = send_report_email(
            report_path,
            search_run.matches,
            run_time,
            funnel_line=search_run.funnel_line(),
        )
    except Exception:
        search_run.delivery_status = "email_failed"
        diagnostics_path = write_diagnostics(search_run, DIAGNOSTICS_PATH, run_time)
        print(f"E-postsending feilet. Diagnostikk: {diagnostics_path}")
        raise

    if email_sent:
        search_run.delivery_status = "sent"
        search_run.sent_count = len(search_run.matches)
        print("Dagsrapporten ble sendt på e-post.")
        try:
            search_run.logged_count = record_sent_ads(
                JOB_LOG_PATH,
                search_run.matches,
                run_datetime.isoformat(timespec="seconds"),
            )
        except Exception:
            search_run.delivery_status = "sent_logging_failed"
            diagnostics_path = write_diagnostics(search_run, DIAGNOSTICS_PATH, run_time)
            print(f"Loggføring feilet etter utsending. Diagnostikk: {diagnostics_path}")
            raise
        print(f"Loggførte {search_run.logged_count} nye annonser i {JOB_LOG_PATH}.")
    else:
        search_run.delivery_status = "disabled"
        print("E-post er ikke aktivert; rapporten ble bare lagret lokalt.")

    diagnostics_path = write_diagnostics(search_run, DIAGNOSTICS_PATH, run_time)
    print(
        f"Levering: status={search_run.delivery_status}, sendt={search_run.sent_count}, "
        f"loggført={search_run.logged_count}. Diagnostikk: {diagnostics_path}"
    )


if __name__ == "__main__":
    main()
