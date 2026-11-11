"""Search public job listings through Tavily and explain every decision."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunsplit
from urllib.request import Request, urlopen


TAVILY_SEARCH_URL = "https://api.tavily.com/search"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIRECTORY = PROJECT_ROOT / "reports" / "reports-v2"

INVALID_RESULT = "invalid_result"
NOT_DIRECT = "not_direct"
DUPLICATE_IN_RUN = "duplicate_in_run"
PREVIOUSLY_SENT = "previously_sent"
PRIOR_APPLICATION = "prior_application"
APPROVED = "approved"
REPORT_LIMIT = "report_limit"
AI_FILTERED = "ai_filtered"

DECISION_LABELS = {
    INVALID_RESULT: "ugyldig treff",
    NOT_DIRECT: "ikke direkte annonse",
    DUPLICATE_IN_RUN: "duplikat i samme kjøring",
    PREVIOUSLY_SENT: "tidligere utsendt",
    PRIOR_APPLICATION: "tidligere søkt",
    APPROVED: "godkjent",
    REPORT_LIMIT: "utelatt av rapportgrensen",
    AI_FILTERED: "filtrert av GPT-kvalitetskontroll",
}

JOB_TYPE_LABELS = {
    "internship": "Internship",
    "summer_job": "Sommerjobb",
    "graduate": "Graduate-stilling",
    "part_time": "Deltidsstilling",
    "student": "Studentstilling",
    "other": "Annen stillingstype",
    "unknown": "Ukjent",
}

TRACKING_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


@dataclass(frozen=True)
class JobMatch:
    """One public job-listing result."""

    title: str
    url: str
    snippet: str
    source: str
    score: int
    search_track: str = ""
    job_type: str = "unknown"


@dataclass
class SearchDiagnostic:
    """The final decision for one result occurrence returned by Tavily."""

    search_track: str
    title: str
    original_url: str
    normalized_url: str
    domain: str
    score: int
    decision: str
    reason: str
    ai_status: str = "not_eligible"
    ai_model: str = ""
    ai_assessment: dict | None = None
    ai_usage: dict | None = None
    ai_error: str = ""


@dataclass
class SearchRun:
    """Selected matches and an auditable record of the complete search funnel."""

    matches: list[JobMatch]
    diagnostics: list[SearchDiagnostic]
    search_tracks: list[str] = field(default_factory=list)
    search_plan: list[dict] = field(default_factory=list)
    delivery_status: str = "not_attempted"
    sent_count: int = 0
    logged_count: int = 0
    ai_summary: dict = field(default_factory=dict)

    def decision_counts(self) -> Counter[str]:
        return Counter(item.decision for item in self.diagnostics)

    def direct_count(self) -> int:
        counts = self.decision_counts()
        return len(self.diagnostics) - counts[INVALID_RESULT] - counts[NOT_DIRECT]

    def funnel_line(self) -> str:
        total = len(self.diagnostics)
        direct = self.direct_count()
        new = len(self.matches)
        if new == 0:
            counts = self.decision_counts()
            handled = counts[PREVIOUSLY_SENT] + counts[PRIOR_APPLICATION]
            filtered = counts[AI_FILTERED]
            return (
                f"{total} søketreff undersøkt · {direct} direkte annonser · "
                f"{handled} allerede behandlet · {filtered} filtrert av GPT · "
                "0 nye annonser"
            )
        filtered = self.decision_counts()[AI_FILTERED]
        filter_text = f" · {filtered} filtrert av GPT" if filtered else ""
        return (
            f"{total} søketreff undersøkt · {direct} direkte annonser"
            f"{filter_text} · {new} nye annonser"
        )


def resolve_search_plan(profile: dict, run_date: date | None = None) -> list[dict]:
    """Select one deterministic query variant per track for the given day."""
    selected_date = run_date or date.today()
    start_value = str(profile.get("search", {}).get("rotation_start") or "2026-09-16")
    try:
        rotation_start = date.fromisoformat(start_value)
    except ValueError as error:
        raise ValueError("search.rotation_start must use YYYY-MM-DD.") from error
    day_index = (selected_date - rotation_start).days
    plan = []
    for source in profile["sources"]["queries"]:
        variants = source.get("variants") or []
        if variants:
            variant_index = day_index % len(variants)
            variant = variants[variant_index]
            query = str(variant.get("query") or "").strip()
            if not query:
                raise ValueError(f"Search track {source['name']} has an empty query variant.")
            plan.append(
                {
                    "search_track": source["name"],
                    "variant": str(variant.get("name") or f"variant {variant_index + 1}"),
                    "variant_index": variant_index,
                    "variant_count": len(variants),
                    "query": query,
                    "include_domains": list(
                        variant.get("include_domains", source.get("include_domains", []))
                    ),
                }
            )
            continue
        query = str(source.get("query") or "").strip()
        if not query:
            raise ValueError(f"Search track {source['name']} is missing a query.")
        plan.append(
            {
                "search_track": source["name"],
                "variant": "fixed",
                "variant_index": 0,
                "variant_count": 1,
                "query": query,
                "include_domains": list(source.get("include_domains", [])),
            }
        )
    return plan


def _usable_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        _ = parsed.port
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


def normalize_url(url: str) -> str:
    """Normalize conservative URL variants while preserving the original URL."""
    url = url.strip()
    if not _usable_url(url):
        return ""

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    port = parsed.port
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in TRACKING_PARAMETERS
        ),
        doseq=True,
    )
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def result_domain(url: str) -> str:
    """Return the actual publishing domain, separate from the configured search."""
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def domain_allowed(domain: str, include_domains: list[str]) -> bool:
    """Enforce portal-specific domain restrictions after Tavily returns results."""
    if not include_domains:
        return True
    return any(
        domain == allowed.lower().removeprefix("www.")
        or domain.endswith(f".{allowed.lower().removeprefix('www.')}")
        for allowed in include_domains
    )


def is_direct_job_listing(result: dict) -> bool:
    """Reject search results that point to job lists or generic career pages."""
    url = str(result.get("url") or "").strip()
    if not _usable_url(url):
        return False

    parsed_url = urlparse(url)
    hostname = (parsed_url.hostname or "").lower().removeprefix("www.")
    path = parsed_url.path.lower().rstrip("/")
    title = str(result.get("title") or "").strip().lower()

    if path in {"", "/"}:
        return False
    if any(
        path.endswith(extension)
        for extension in (".mp4", ".mov", ".pdf", ".jpg", ".png")
    ):
        return False

    if hostname == "finn.no" or hostname.endswith(".finn.no"):
        return re.fullmatch(r"/job/ad/\d+", path) is not None
    if hostname == "linkedin.com" or hostname.endswith(".linkedin.com"):
        return "/jobs/view/" in f"{path}/"
    if hostname == "indeed.com" or hostname.endswith(".indeed.com"):
        return path == "/viewjob"
    if hostname == "iagora.com" or hostname.endswith(".iagora.com"):
        return "/work/en/offer/" in f"{path}/"

    list_page_fragments = (
        "/job/search",
        "/jobs/search",
        "/jobs-and-internships",
        "/jobs/collections",
    )
    if any(fragment in path for fragment in list_page_fragments):
        return False

    generic_endings = (
        "/career",
        "/careers",
        "/internship",
        "/internships",
        "/jobs",
        "/jobbeifinn",
        "/opportunities",
        "/vacancies",
    )
    if path.endswith(generic_endings):
        return False

    list_title_patterns = (
        r"^\d[\d, +]*\s+.*\b(jobs|internships|stillinger)\b",
        r"\b(open roles|ledige stillinger|jobs and internships|jobs in|internships in)\b",
        r"^(career|careers|internships|job search|vacancies)(\s*[|–-].*)?$",
        r"^(summer\s+)?internships?(\s*[|–-].*)?$",
    )
    return not any(re.search(pattern, title) for pattern in list_title_patterns)


def tavily_search(query: str, include_domains: list[str]) -> list[dict]:
    """Run a low-cost public web search through Tavily."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is missing. Add it as a GitHub Actions secret.")

    payload = json.dumps(
        {
            "query": query,
            "search_depth": "basic",
            "max_results": 10,
            "include_domains": include_domains,
            "country": "norway",
        }
    ).encode("utf-8")
    request = Request(
        TAVILY_SEARCH_URL,
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response).get("results", [])
    except HTTPError as error:
        raise RuntimeError(f"Tavily returned HTTP {error.code}.") from error
    except URLError as error:
        raise RuntimeError("Could not reach Tavily.") from error


def score_result(title: str, snippet: str, profile: dict) -> int:
    """Use transparent keyword scoring without excluding low-scoring ads."""
    text = f"{title} {snippet}".lower()
    search = profile["search"]
    score = 0
    for role in search["primary_roles"]:
        if role.lower().replace(" internship", "") in text:
            score += 8
    for role in search["secondary_roles"]:
        if role.lower().replace(" internship", "") in text:
            score += 4
    if "intern" in text or "summer job" in text or "summerjobb" in text:
        score += 6
    if any(term in text for term in ("graduate", "student", "part-time", "deltid")):
        score += 4
    if "2027" in text:
        score += 5
    if "oslo" in text:
        score += 4
    if "finance" in text or "finans" in text:
        score += 3
    return score


def is_prior_application(result: dict, applications: list[dict]) -> bool:
    """Return true only for a confirmed company-and-role duplicate."""
    text = f"{result.get('title', '')} {result.get('content', '')}".lower()
    generic_role_words = {"summer", "internship", "intern", "program", "2027"}
    for application in applications:
        if application["verification"] != "confirmed":
            continue
        company_key = application["company"].split()[0].lower()
        if company_key not in text:
            continue
        role_words = {
            word.strip("–&,").lower()
            for word in application["role"].split()
            if word.strip("–&,").lower() not in generic_role_words
        }
        if role_words.intersection(text.split()):
            return True
        if "summer internship" in application["role"].lower() and "summer" in text and "intern" in text:
            return True
    return False


def run_search(
    profile: dict,
    applications: list[dict],
    sent_ads: list[dict] | None = None,
    max_ads: int | None = None,
    run_date: date | None = None,
) -> SearchRun:
    """Search all tracks and assign every fetched occurrence one final decision."""
    candidates: list[tuple[JobMatch, SearchDiagnostic]] = []
    diagnostics: list[SearchDiagnostic] = []
    seen_urls: set[str] = set()
    sent_urls = {
        normalized
        for entry in (sent_ads or [])
        if isinstance(entry, dict) and isinstance(entry.get("url"), str)
        if (normalized := normalize_url(entry["url"]))
    }

    search_plan = resolve_search_plan(profile, run_date)
    for source in search_plan:
        search_track = source["search_track"]
        for result in tavily_search(source["query"], source["include_domains"]):
            original_url = str(result.get("url", "")).strip()
            normalized_url = normalize_url(original_url)
            title = str(result.get("title") or "Untitled listing").strip()
            snippet = str(result.get("content") or "")
            score = score_result(title, snippet, profile)
            domain = result_domain(original_url)

            if not normalized_url:
                decision = INVALID_RESULT
                reason = "Resultatet mangler en gyldig HTTP(S)-URL."
            elif not domain_allowed(domain, source["include_domains"]):
                decision = NOT_DIRECT
                reason = "Resultatet ligger utenfor søkesporets tillatte domener."
            elif not is_direct_job_listing(result):
                decision = NOT_DIRECT
                reason = "URL eller tittel peker på en søkeliste eller generell karriereside."
            elif normalized_url in seen_urls:
                decision = DUPLICATE_IN_RUN
                reason = "Samme normaliserte URL er allerede behandlet i denne kjøringen."
            else:
                seen_urls.add(normalized_url)
                if normalized_url in sent_urls:
                    decision = PREVIOUSLY_SENT
                    reason = "Annonsens normaliserte URL finnes i utsendelsesloggen."
                elif is_prior_application(result, applications):
                    decision = PRIOR_APPLICATION
                    reason = "Selskap og rolle samsvarer med en bekreftet tidligere søknad."
                else:
                    decision = APPROVED
                    reason = "Direkte annonse som ikke er behandlet tidligere."

            diagnostic = SearchDiagnostic(
                search_track=search_track,
                title=title,
                original_url=original_url,
                normalized_url=normalized_url,
                domain=domain,
                score=score,
                decision=decision,
                reason=reason,
            )
            diagnostics.append(diagnostic)
            if decision == APPROVED:
                candidates.append(
                    (
                        JobMatch(
                            title=title,
                            url=original_url,
                            snippet=snippet,
                            source=domain,
                            score=score,
                            search_track=search_track,
                        ),
                        diagnostic,
                    )
                )

    candidates.sort(key=lambda item: item[0].score, reverse=True)
    selected = candidates if max_ads is None else candidates[:max_ads]
    if max_ads is not None:
        for _match, diagnostic in candidates[max_ads:]:
            diagnostic.decision = REPORT_LIMIT
            diagnostic.reason = f"Annonsen falt utenfor rapportgrensen på {max_ads}."
    return SearchRun(
        matches=[match for match, _diagnostic in selected],
        diagnostics=diagnostics,
        search_tracks=[source["search_track"] for source in search_plan],
        search_plan=search_plan,
    )


def find_matches(
    profile: dict,
    applications: list[dict],
    sent_ads: list[dict] | None = None,
) -> list[JobMatch]:
    """Compatibility wrapper returning all approved, ranked matches."""
    return run_search(profile, applications, sent_ads).matches


def funnel_log_lines(search_run: SearchRun) -> list[str]:
    """Build a compact total and per-track funnel for console logs."""
    lines = [f"Søketrakt totalt: {search_run.funnel_line()}"]
    grouped: dict[str, list[SearchDiagnostic]] = defaultdict(list)
    for item in search_run.diagnostics:
        grouped[item.search_track].append(item)
    for track in search_run.search_tracks:
        items = grouped[track]
        plan_item = next(
            (item for item in search_run.search_plan if item["search_track"] == track),
            None,
        )
        variant_text = (
            f" ({plan_item['variant']})"
            if plan_item and plan_item.get("variant") != "fixed"
            else ""
        )
        counts = Counter(item.decision for item in items)
        decision_text = ", ".join(
            f"{DECISION_LABELS[decision]}={counts[decision]}"
            for decision in DECISION_LABELS
            if counts[decision]
        ) or "ingen beslutninger"
        domains = Counter(item.domain or "ukjent" for item in items)
        domain_text = (
            ", ".join(f"{domain}={count}" for domain, count in domains.most_common())
            or "ingen"
        )
        lines.append(
            f"Søkespor {track}{variant_text}: hentet={len(items)}; "
            f"{decision_text}; domener: {domain_text}"
        )
    return lines


def write_diagnostics(search_run: SearchRun, path: Path, run_time: str) -> Path:
    """Write diagnostics without Tavily snippets for use as a workflow artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[SearchDiagnostic]] = defaultdict(list)
    for item in search_run.diagnostics:
        grouped[item.search_track].append(item)

    by_search_track = []
    for track in search_run.search_tracks:
        items = grouped[track]
        plan_item = next(
            (item for item in search_run.search_plan if item["search_track"] == track),
            {},
        )
        by_search_track.append(
            {
                "search_track": track,
                "variant": plan_item.get("variant", ""),
                "query": plan_item.get("query", ""),
                "include_domains": plan_item.get("include_domains", []),
                "fetched": len(items),
                "decisions": dict(sorted(Counter(item.decision for item in items).items())),
                "domains": dict(
                    sorted(Counter(item.domain or "unknown" for item in items).items())
                ),
            }
        )

    payload = {
        "run_time": run_time,
        "summary": {
            "fetched": len(search_run.diagnostics),
            "direct_occurrences": search_run.direct_count(),
            "selected": len(search_run.matches),
            "decisions": dict(sorted(search_run.decision_counts().items())),
            "delivery": {
                "status": search_run.delivery_status,
                "sent": search_run.sent_count,
                "logged": search_run.logged_count,
            },
            "ai_relevance": search_run.ai_summary,
        },
        "by_search_track": by_search_track,
        "results": [asdict(item) for item in search_run.diagnostics],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_report(matches: list[JobMatch]) -> Path:
    """Write one dated report per daily run."""
    REPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIRECTORY / f"{date.today().isoformat()}.md"
    lines = ["# Daglig jobbsøk", "", f"Kjørt: {date.today().isoformat()}", ""]
    if not matches:
        lines.append("Ingen relevante offentlige treff i dagens søk.")
    for index, match in enumerate(matches[:20], start=1):
        source_line = f"**Domene:** {match.source} · **Match-score:** {match.score}"
        source_line += f" · **Jobbtype:** {JOB_TYPE_LABELS.get(match.job_type, 'Ukjent')}"
        if match.search_track:
            source_line += f" · **Søkespor:** {match.search_track}"
        lines.extend(
            [
                f"## {index}. {match.title}",
                source_line,
                f"[Åpne annonsen]({match.url})",
                "",
                match.snippet,
                "",
            ]
        )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
