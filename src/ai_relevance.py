"""Structured OpenAI assessments for observing or filtering job candidates."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
import os
from typing import Any, Callable


DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_MAX_CANDIDATES = 20
DEFAULT_MAX_TEXT_CHARS = 2_000
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_OUTPUT_TOKENS = 500

RELEVANCE_VALUES = {"high", "medium", "low", "uncertain"}
TRACK_VALUES = {
    "finance",
    "investment",
    "strategy",
    "consulting",
    "analytics",
    "fintech",
    "product",
    "other",
}
FLAG_VALUES = {"true", "false", "uncertain"}
RED_FLAG_FIELDS = {"wrong_year", "wrong_location", "wrong_experience_level"}
JOB_TYPE_VALUES = {
    "internship",
    "summer_job",
    "graduate",
    "part_time",
    "student",
    "other",
    "unknown",
}
DELIVERABLE_JOB_TYPES = {"internship", "summer_job", "graduate", "part_time", "student"}

RELEVANCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "relevance": {"type": "string", "enum": sorted(RELEVANCE_VALUES)},
        "track": {"type": "string", "enum": sorted(TRACK_VALUES)},
        "job_type": {"type": "string", "enum": sorted(JOB_TYPE_VALUES)},
        "is_direct_job_listing": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 500},
        "red_flags": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                field: {"type": "string", "enum": sorted(FLAG_VALUES)}
                for field in sorted(RED_FLAG_FIELDS)
            },
            "required": sorted(RED_FLAG_FIELDS),
        },
        "insufficient_information": {"type": "boolean"},
    },
    "required": [
        "relevance",
        "track",
        "job_type",
        "is_direct_job_listing",
        "reason",
        "red_flags",
        "insufficient_information",
    ],
}


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


@dataclass(frozen=True)
class AIConfig:
    """Cost and behavior limits for the optional relevance layer."""

    enabled: bool = False
    mode: str = "shadow"
    model: str = DEFAULT_MODEL
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS

    @classmethod
    def from_env(cls) -> "AIConfig":
        return cls(
            enabled=_enabled(os.getenv("AI_ENABLED")),
            mode=(os.getenv("AI_MODE") or "shadow").strip().lower(),
            model=(os.getenv("OPENAI_MODEL") or DEFAULT_MODEL).strip(),
            max_candidates=_bounded_int("AI_MAX_CANDIDATES", 20, 1, 20),
            max_text_chars=_bounded_int("AI_MAX_TEXT_CHARS", 2_000, 200, 5_000),
            timeout_seconds=_bounded_float("AI_TIMEOUT_SECONDS", 20.0, 1.0, 60.0),
            max_output_tokens=_bounded_int("AI_MAX_OUTPUT_TOKENS", 500, 200, 1_000),
        )


@dataclass(frozen=True)
class AIEvaluation:
    """One safe-to-log shadow evaluation result."""

    status: str
    model: str
    assessment: dict[str, Any] | None = None
    usage: dict[str, int] | None = None
    error: str = ""

    def diagnostic_payload(self) -> dict[str, Any]:
        return asdict(self)


def delivery_decision(evaluation: AIEvaluation) -> tuple[bool, str]:
    """Fail closed unless GPT has positively validated every critical condition."""
    if evaluation.status != "success" or not evaluation.assessment:
        return False, f"GPT-kvalitetskontrollen kunne ikke validere annonsen ({evaluation.status})."
    assessment = evaluation.assessment
    if not assessment["is_direct_job_listing"]:
        return False, "GPT vurderte siden som noe annet enn én konkret stillingsannonse."
    if assessment["job_type"] not in DELIVERABLE_JOB_TYPES:
        return False, "GPT fant ingen støttet jobbtype i annonsen."
    if assessment["relevance"] not in {"high", "medium"}:
        return False, f"GPT vurderte relevansen som {assessment['relevance']}."
    if assessment["insufficient_information"]:
        return False, "GPT manglet nok informasjon til å godkjenne annonsen."
    red_flags = assessment["red_flags"]
    for flag in ("wrong_location", "wrong_year", "wrong_experience_level"):
        if red_flags[flag] != "false":
            return False, f"GPT kunne ikke avkrefte {flag}."
    return True, "GPT approved a concrete listing, target location, timing and candidate match."


def candidate_context(profile: dict[str, Any]) -> dict[str, Any]:
    """Allowlist only approved, relevance-related profile fields."""
    candidate = profile.get("candidate", {})
    background = profile.get("background", {})
    search = profile.get("search", {})
    return {
        "degree": candidate.get("degree", ""),
        "school": candidate.get("school", ""),
        "study_stage": candidate.get("study_stage", ""),
        "availability": candidate.get("availability", ""),
        "background_highlights": list(background.get("highlights", [])),
        "skills": list(background.get("skills", [])),
        "target_year": search.get("year"),
        "target_locations": list(search.get("locations", [])),
        "target_job_types": list(search.get("job_types", [])),
        "primary_roles": list(search.get("primary_roles", [])),
        "secondary_roles": list(search.get("secondary_roles", [])),
    }


def _usage_payload(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    values: dict[str, int] = {}
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        if isinstance(value, int):
            values[name] = value
    return values


def _validate_assessment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Modellsvaret var ikke et JSON-objekt.")
    if value.get("relevance") not in RELEVANCE_VALUES:
        raise ValueError("Modellsvaret hadde ugyldig relevans.")
    if value.get("track") not in TRACK_VALUES:
        raise ValueError("Modellsvaret hadde ugyldig fagspor.")
    if value.get("job_type") not in JOB_TYPE_VALUES:
        raise ValueError("Modellsvaret hadde ugyldig jobbtype.")
    if not isinstance(value.get("is_direct_job_listing"), bool):
        raise ValueError("Modellsvaret manglet annonsetype.")
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise ValueError("Modellsvaret manglet en gyldig begrunnelse.")
    red_flags = value.get("red_flags")
    if not isinstance(red_flags, dict) or set(red_flags) != RED_FLAG_FIELDS:
        raise ValueError("Modellsvaret hadde ugyldige røde flagg.")
    if any(flag not in FLAG_VALUES for flag in red_flags.values()):
        raise ValueError("Modellsvaret hadde en ugyldig flaggverdi.")
    if not isinstance(value.get("insufficient_information"), bool):
        raise ValueError("Modellsvaret manglet informasjonsstatus.")
    return value


def _default_client_factory(api_key: str, timeout_seconds: float) -> Any:
    from openai import OpenAI

    return OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=1)


def _evaluate_one(
    client: Any,
    match: Any,
    profile_context: dict[str, Any],
    config: AIConfig,
) -> AIEvaluation:
    input_payload = {
        "candidate_profile": profile_context,
        "job": {
            "title": match.title,
            "search_track": match.search_track,
            "company_or_domain": match.source,
            "location": "",
            "listing_excerpt": match.snippet[: config.max_text_chars],
        },
    }
    response = client.responses.create(
        model=config.model,
        instructions=(
            "Act as a strict quality gate for a job email. Use only the supplied facts. First "
            "decide whether the URL and excerpt describe one concrete job opening that a person "
            "can apply to. Homepages, job lists, investor pages, articles, journals, videos, "
            "courses and general career pages are not direct job listings. Classify the job type. "
            "The location is acceptable only when the supplied listing explicitly supports one "
            "of the candidate's target locations or an immediately adjacent commuting area. Do not "
            "infer a location from the search query or candidate profile. Mark wrong_location "
            "uncertain when the location is absent or ambiguous. Assess personal relevance and "
            "experience level without inventing facts. Keep the reason short and concrete."
        ),
        input=json.dumps(input_payload, ensure_ascii=False),
        text={
            "format": {
                "type": "json_schema",
                "name": "job_relevance",
                "strict": True,
                "schema": RELEVANCE_SCHEMA,
            }
        },
        reasoning={"effort": "low"},
        max_output_tokens=config.max_output_tokens,
        store=False,
    )
    assessment = _validate_assessment(json.loads(response.output_text))
    return AIEvaluation(
        status="success",
        model=config.model,
        assessment=assessment,
        usage=_usage_payload(getattr(response, "usage", None)),
    )


def evaluate_matches(
    matches: list[Any],
    profile: dict[str, Any],
    config: AIConfig,
    client_factory: Callable[[str, float], Any] | None = None,
) -> list[AIEvaluation]:
    """Evaluate matches and return one bounded result for every input match."""
    if not config.enabled:
        return [AIEvaluation(status="disabled", model=config.model) for _ in matches]
    if config.mode not in {"shadow", "active"}:
        return [
            AIEvaluation(
                status="configuration_error",
                model=config.model,
                error="AI_MODE må være shadow eller active.",
            )
            for _ in matches
        ]
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return [
            AIEvaluation(
                status="missing_key",
                model=config.model,
                error="OPENAI_API_KEY mangler.",
            )
            for _ in matches
        ]

    factory = client_factory or _default_client_factory
    try:
        client = factory(api_key, config.timeout_seconds)
    except Exception:
        return [
            AIEvaluation(
                status="client_error",
                model=config.model,
                error="OpenAI-klienten kunne ikke initialiseres.",
            )
            for _ in matches
        ]

    context = candidate_context(profile)
    evaluations: list[AIEvaluation] = []
    for index, match in enumerate(matches):
        if index >= config.max_candidates:
            evaluations.append(AIEvaluation(status="skipped_limit", model=config.model))
            continue
        try:
            evaluations.append(_evaluate_one(client, match, context, config))
        except (json.JSONDecodeError, ValueError):
            evaluations.append(
                AIEvaluation(
                    status="invalid_response",
                    model=config.model,
                    error="OpenAI returnerte et ugyldig strukturert svar.",
                )
            )
        except Exception:
            evaluations.append(
                AIEvaluation(
                    status="api_error",
                    model=config.model,
                    error="OpenAI-kallet feilet eller fikk tidsavbrudd.",
                )
            )
    return evaluations


def summarize_evaluations(config: AIConfig, evaluations: list[AIEvaluation]) -> dict[str, Any]:
    """Build a safe aggregate for logs and diagnostics."""
    statuses = Counter(item.status for item in evaluations)
    usage: Counter[str] = Counter()
    for item in evaluations:
        usage.update(item.usage or {})
    return {
        "enabled": config.enabled,
        "mode": config.mode,
        "model": config.model,
        "max_candidates": config.max_candidates,
        "status_counts": dict(sorted(statuses.items())),
        "usage": dict(sorted(usage.items())),
    }


def ai_log_line(summary: dict[str, Any]) -> str:
    """Return one compact log line without prompts, keys, or candidate details."""
    if not summary["enabled"]:
        return f"AI-relevans ({summary['mode']}): deaktivert."
    statuses = ", ".join(
        f"{status}={count}" for status, count in summary["status_counts"].items()
    ) or "ingen kandidater"
    usage = summary.get("usage", {})
    usage_text = (
        f"; tokens inn={usage.get('input_tokens', 0)}, ut={usage.get('output_tokens', 0)}"
        if usage
        else ""
    )
    selection_text = (
        f"; godkjent={summary.get('delivered', 0)}, filtrert={summary.get('filtered', 0)}"
        if summary.get("mode") == "active"
        else ""
    )
    return (
        f"AI-relevans ({summary['mode']}): modell={summary['model']}; "
        f"{statuses}{selection_text}{usage_text}"
    )
