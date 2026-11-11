"""Deterministic mapping from visible form labels to approved candidate data."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import FieldDescriptor, FieldPlan, PlanResult
from .profile import cv_filename, document_filenames, profile_values


SENSITIVE_PATTERNS = (
    "work authorization",
    "authorized to work",
    "right to work",
    "work eligibility",
    "visa sponsorship",
    "sponsorship",
    "citizenship",
    "nationality",
    "social security",
    "personnummer",
    "gender",
    "kjonn",
    "ethnicity",
    "race",
    "disability",
    "veteran",
    "criminal",
    "background check",
    "date of birth",
    "year of birth",
    "birth year",
    "birthdate",
    "dob",
    "age",
    "sex",
    "female",
    "male",
    "marital status",
    "sexual orientation",
    "religion",
    "medical",
    "health condition",
    "political affiliation",
    "trade union",
    "samtykke",
    "consent",
    "terms and conditions",
    "privacy policy",
)

CONSENT_PATTERNS = (
    "samtykke",
    "consent",
    "terms and conditions",
    "privacy policy",
    "agree to",
    "i agree",
    "jeg godtar",
    "accept the terms",
)

REVIEW_PATTERNS = {
    "cover_letter": ("cover letter", "application letter", "soknadsbrev"),
    "motivation": ("motivation", "why do you", "why are you", "hvorfor vil"),
    "salary": ("salary", "compensation", "lonn", "lønn"),
    "notice_period": ("notice period", "oppsigelsestid"),
    "availability": ("availability", "available from", "tilgjengelighet"),
    "free_text": ("additional information", "tell us", "describe", "fortell", "beskriv"),
}

SAFE_MAPPINGS = {
    "gender": ("gender", "kjonn", "sex"),
    "date_of_birth": ("date of birth", "birthdate", "dob", "fodselsdato"),
    "birth_year": ("year of birth", "birth year", "fodselsar"),
    "first_name": ("first name", "given name", "fornavn", "given-name"),
    "last_name": ("last name", "family name", "surname", "etternavn", "family-name"),
    "full_name": ("full name", "your name", "candidate name", "legal name", "navn", "name"),
    "email": ("email", "e mail", "e-post", "epost"),
    "phone": ("phone", "telephone", "mobile", "telefon", "tel"),
    "city": ("city", "bosted", "by", "address-level2"),
    "linkedin": ("linkedin",),
    "school": ("school", "university", "college", "universitet", "skole"),
    "degree": ("degree", "qualification", "grad", "utdanning"),
    "graduation": ("graduation", "graduate date", "expected completion", "fullforing"),
    "experience": ("work experience", "employment history", "arbeidserfaring"),
    "languages": ("languages", "sprak"),
    "skills": ("skills", "competencies", "ferdigheter", "kompetanse"),
}

EXPLICIT_LOCAL_SENSITIVE_KEYS = {"gender", "date_of_birth", "birth_year"}


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.lower())
    value = "".join(character for character in value if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def field_context(field: FieldDescriptor) -> str:
    return normalize_text(
        " ".join(
            (
                field.label,
                field.name,
                field.element_id,
                field.autocomplete,
                field.placeholder,
                field.group_label,
                field.help_text,
                field.nearby_text,
            )
        )
    )


def is_sensitive_field(field: FieldDescriptor) -> bool:
    context = field_context(field)
    return any(_contains_phrase(context, pattern) for pattern in SENSITIVE_PATTERNS)


def is_consent_field(field: FieldDescriptor) -> bool:
    context = field_context(field)
    return any(_contains_phrase(context, pattern) for pattern in CONSENT_PATTERNS)


def _contains_phrase(context: str, phrase: str) -> bool:
    normalized = normalize_text(phrase)
    return bool(re.search(rf"(?:^|\s){re.escape(normalized)}(?:$|\s)", context))


def _matched_key(context: str, mappings: dict[str, tuple[str, ...]]) -> str:
    for key, patterns in mappings.items():
        if any(_contains_phrase(context, pattern) for pattern in patterns):
            return key
    return ""


def _approved_answer(context: str, values: dict[str, str]) -> tuple[str, str]:
    for key, value in values.items():
        if not key.startswith("approved_answers.") or not value:
            continue
        answer_name = key.removeprefix("approved_answers.")
        if _contains_phrase(context, answer_name.replace("_", " ")):
            return key, value
    return "", ""


def _option_available(field: FieldDescriptor, value: str) -> bool:
    if field.field_type not in {"select", "radio"}:
        return True
    wanted = normalize_text(value)
    return any(normalize_text(option) == wanted for option in field.options)


def _base_plan(field: FieldDescriptor, category: str, reason: str) -> FieldPlan:
    return FieldPlan(
        field_id=field.field_id,
        dom_index=field.dom_index,
        label=field.label or field.name or field.field_id,
        field_type=field.field_type,
        category=category,
        reason=reason,
        name=field.name,
        element_id=field.element_id,
        tag=field.tag,
        options=list(field.options),
        group_label=field.group_label,
        help_text=field.help_text,
        nearby_text=field.nearby_text,
        input_value=field.input_value,
        accept=field.accept,
    )


def _document_key_for_field(field: FieldDescriptor) -> str:
    context = field_context(field)
    if any(_contains_phrase(context, pattern) for pattern in ("upload cv", "resume", "curriculum vitae")):
        return "standard_cv"
    is_transcript = any(
        _contains_phrase(context, pattern)
        for pattern in ("transcript", "grade transcript", "academic record", "karakterutskrift")
    )
    if is_transcript and any(
        _contains_phrase(context, pattern)
        for pattern in ("master", "m sc", "msc")
    ):
        return "master_transcript"
    if is_transcript and any(
        _contains_phrase(context, pattern)
        for pattern in ("bachelor", "b sc", "bsc", "undergraduate")
    ):
        return "bachelor_transcript"
    return ""


def _auto_profile_plan(
    field: FieldDescriptor,
    key: str,
    value: str,
    *,
    sensitive: bool = False,
) -> FieldPlan:
    plan = _base_plan(field, "auto", "Dokumentert, stabil verdi fra kandidatprofilen.")
    plan.value = value
    plan.profile_key = key
    plan.evidence_keys = [key]
    plan.selected_by_default = True
    plan.fillable = True
    plan.sensitive = sensitive
    return plan


def map_field(
    field: FieldDescriptor,
    values: dict[str, str],
    documents: dict[str, str] | str,
) -> FieldPlan:
    """Map one field without guessing or calling an external service."""
    if isinstance(documents, str):
        documents = {"standard_cv": documents} if documents else {}
    context = field_context(field)
    if field.disabled:
        return _base_plan(field, "unsupported", "Feltet er deaktivert på siden.")
    if field.field_type in {"hidden", "submit", "button", "reset", "image"}:
        return _base_plan(field, "unsupported", "Kontrollfelt fylles aldri av Jobbagent.")
    safe_key = _matched_key(context, SAFE_MAPPINGS)
    if is_sensitive_field(field):
        if safe_key in EXPLICIT_LOCAL_SENSITIVE_KEYS:
            value = values.get(safe_key, "")
            valid_date = field.field_type != "date" or bool(
                re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
            )
            if value and valid_date and _option_available(field, value):
                return _auto_profile_plan(
                    field,
                    safe_key,
                    value,
                    sensitive=True,
                )
        plan = _base_plan(
            field,
            "requires_action",
            "Sensitivt eller juridisk spørsmål krever et aktivt svar fra deg.",
        )
        plan.sensitive = True
        plan.fillable = field.field_type not in {"file", "checkbox"}
        return plan
    if field.field_type == "checkbox":
        plan = _base_plan(
            field,
            "requires_action",
            "Avkrysningsfeltet må forstås og velges aktivt før utfylling.",
        )
        plan.fillable = not is_consent_field(field)
        return plan
    if field.field_type == "file":
        document_key = _document_key_for_field(field)
        filename = documents.get(document_key, "") if document_key else ""
        if filename:
            plan = _base_plan(
                field,
                "auto",
                "Konfigurert lokalt dokument lastes opp når du fyller inn valgte felt.",
            )
            plan.value = filename
            plan.profile_key = f"documents.{document_key}"
            plan.evidence_keys = [plan.profile_key]
            plan.document_key = document_key
            plan.meaning = "document_upload"
            plan.selected_by_default = True
            plan.fillable = True
            plan.sensitive = True
            return plan
        if not field.required:
            return _base_plan(
                field,
                "unsupported",
                "Valgfritt dokumentfelt hoppes over fordi intet lokalt dokument er konfigurert.",
            )
        plan = _base_plan(
            field,
            "requires_action",
            "Et påkrevd dokument mangler i den lokale kandidatprofilen.",
        )
        plan.document_key = document_key
        plan.meaning = "document_upload"
        return plan

    if any(
        _contains_phrase(context, pattern)
        for pattern in ("company name", "employer name", "organization name")
    ):
        return _base_plan(
            field,
            "requires_action",
            "Feltet ser ut til å gjelde arbeidsgiveren og må vurderes manuelt.",
        )

    review_key = _matched_key(context, REVIEW_PATTERNS)
    if review_key:
        plan = _base_plan(
            field,
            "review",
            "Forslaget må kontrolleres og velges aktivt før innsetting.",
        )
        profile_key = (
            "availability"
            if review_key == "availability"
            else f"approved_answers.{review_key}"
        )
        plan.profile_key = profile_key
        plan.value = values.get(profile_key, "")
        plan.evidence_keys = [profile_key] if plan.value else []
        plan.fillable = True
        return plan

    approved_key, approved_value = _approved_answer(context, values)
    if approved_key:
        if not _option_available(field, approved_value):
            return _base_plan(
                field,
                "requires_action",
                "Det godkjente standardsvaret finnes ikke blant sidens alternativer.",
            )
        plan = _base_plan(field, "auto", "Godkjent standardsvar fra kandidatprofilen.")
        plan.value = approved_value
        plan.profile_key = approved_key
        plan.evidence_keys = [approved_key]
        plan.selected_by_default = True
        plan.fillable = True
        return plan

    if not safe_key:
        plan = _base_plan(
            field,
            "requires_action",
            "Feltet er ukjent og må vurderes manuelt.",
        )
        plan.fillable = True
        return plan
    value = values.get(safe_key, "")
    if not value:
        plan = _base_plan(
            field,
            "requires_action",
            "Kandidatprofilen mangler en dokumentert verdi for feltet.",
        )
        plan.profile_key = safe_key
        plan.fillable = True
        return plan
    if not _option_available(field, value):
        plan = _base_plan(
            field,
            "requires_action",
            "Den dokumenterte verdien finnes ikke blant sidens alternativer.",
        )
        plan.profile_key = safe_key
        return plan
    if field.field_type == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        plan = _base_plan(
            field,
            "requires_action",
            "Datofeltet krever en dokumentert verdi i formatet ÅÅÅÅ-MM-DD.",
        )
        plan.profile_key = safe_key
        return plan
    return _auto_profile_plan(field, safe_key, value)


def build_deterministic_plan(
    fields: list[FieldDescriptor], profile: dict[str, Any]
) -> PlanResult:
    values = profile_values(profile)
    documents = document_filenames(profile)
    return PlanResult(
        fields=[map_field(field, values, documents) for field in fields],
        cv_filename=cv_filename(profile),
    )
