"""Load candidate material from the git-ignored local data directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MAX_PROFILE_BYTES = 1_000_000
ALLOWED_DOCUMENT_KEYS = {
    "standard_cv",
    "bachelor_transcript",
    "master_transcript",
}
AI_FORBIDDEN_ANSWER_KEYS = (
    "authorization",
    "citizenship",
    "consent",
    "disability",
    "ethnicity",
    "gender",
    "nationality",
    "race",
    "sponsorship",
    "veteran",
    "visa",
)


def load_candidate_profile(path: Path) -> dict[str, Any]:
    """Load and minimally validate a local candidate profile."""
    try:
        if path.stat().st_size > MAX_PROFILE_BYTES:
            raise RuntimeError("Kandidatprofilen er større enn tillatt grense på 1 MB.")
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(
            f"Kandidatprofil mangler: {path}. Kopier eksempelprofilen først."
        ) from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Kandidatprofilen er ikke gyldig JSON: {path}") from error
    if not isinstance(value, dict):
        raise RuntimeError("Kandidatprofilen må være et JSON-objekt.")
    for section in ("personal", "education", "documents", "approved_answers"):
        if section in value and not isinstance(value[section], dict):
            raise RuntimeError(f"Kandidatprofilens '{section}' må være et objekt.")
    return value


def profile_values(profile: dict[str, Any]) -> dict[str, str]:
    """Flatten only documented candidate values used by deterministic mapping."""
    personal = profile.get("personal") or {}
    education = profile.get("education") or {}
    experience = profile.get("experience") or []
    approved_answers = profile.get("approved_answers") or {}
    first_name = str(personal.get("first_name") or "").strip()
    last_name = str(personal.get("last_name") or "").strip()
    full_name = str(personal.get("full_name") or "").strip() or " ".join(
        part for part in (first_name, last_name) if part
    )
    date_of_birth = str(personal.get("date_of_birth") or "").strip()
    birth_year = str(personal.get("birth_year") or "").strip()
    if not birth_year and len(date_of_birth) >= 4 and date_of_birth[:4].isdigit():
        birth_year = date_of_birth[:4]

    graduation = str(education.get("expected_graduation") or "").strip()
    if not graduation:
        try:
            start_year = int(education.get("start_year"))
            duration_years = int(education.get("duration_years"))
        except (TypeError, ValueError):
            pass
        else:
            if 1900 <= start_year <= 2200 and 1 <= duration_years <= 10:
                graduation = str(start_year + duration_years)

    experience_lines = []
    if isinstance(experience, list):
        for item in experience:
            if not isinstance(item, dict):
                continue
            parts = [str(item.get(key) or "").strip() for key in ("role", "company", "summary")]
            line = " – ".join(part for part in parts if part)
            if line:
                experience_lines.append(line)

    values = {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "email": str(personal.get("email") or "").strip(),
        "phone": str(personal.get("phone") or "").strip(),
        "gender": str(personal.get("gender") or "").strip(),
        "date_of_birth": date_of_birth,
        "birth_year": birth_year,
        "city": str(personal.get("city") or "").strip(),
        "linkedin": str(personal.get("linkedin") or "").strip(),
        "school": str(education.get("school") or "").strip(),
        "degree": str(education.get("degree") or "").strip(),
        "graduation": graduation,
        "experience": "\n".join(experience_lines),
        "languages": ", ".join(str(item) for item in profile.get("languages") or []),
        "skills": ", ".join(str(item) for item in profile.get("skills") or []),
        "availability": str(profile.get("availability") or "").strip(),
    }
    if isinstance(approved_answers, dict):
        for key, value in approved_answers.items():
            if isinstance(value, (str, int, float, bool)):
                values[f"approved_answers.{key}"] = str(value).strip()
    return values


def cv_filename(profile: dict[str, Any]) -> str:
    """Return only the configured file name, never an absolute local path."""
    documents = profile.get("documents") or {}
    configured = Path(str(documents.get("standard_cv") or "")).name
    return configured if configured not in {"", "."} else ""


def document_filename(profile: dict[str, Any], key: str) -> str:
    """Return one allowlisted document filename without exposing a local path."""
    if key not in ALLOWED_DOCUMENT_KEYS:
        return ""
    documents = profile.get("documents") or {}
    raw_value = str(documents.get(key) or "").strip()
    if not raw_value:
        return ""
    configured = Path(raw_value).name
    return configured if configured not in {"", "."} else ""


def document_filenames(profile: dict[str, Any]) -> dict[str, str]:
    """Return configured, allowlisted document filenames."""
    return {
        key: filename
        for key in ALLOWED_DOCUMENT_KEYS
        if (filename := document_filename(profile, key))
    }


def ai_candidate_context(profile: dict[str, Any]) -> dict[str, Any]:
    """Allowlist non-contact facts that may support review-only AI drafts."""
    education = profile.get("education") or {}
    approved_answers = profile.get("approved_answers") or {}
    if not isinstance(approved_answers, dict):
        approved_answers = {}
    safe_answers = {
        key: value
        for key, value in approved_answers.items()
        if isinstance(key, str)
        and not any(term in key.lower() for term in AI_FORBIDDEN_ANSWER_KEYS)
    }
    return {
        "education": {
            "school": education.get("school", ""),
            "degree": education.get("degree", ""),
            "expected_graduation": education.get("expected_graduation", ""),
        },
        "experience": profile.get("experience") or [],
        "languages": profile.get("languages") or [],
        "skills": profile.get("skills") or [],
        "availability": profile.get("availability", ""),
        "approved_answers": safe_answers,
    }
