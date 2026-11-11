"""Build a preview plan without taking any action in the browser."""

from __future__ import annotations

from typing import Any

from .ai import AIConfig, add_ai_suggestions
from .mapping import build_deterministic_plan
from .models import FieldDescriptor, PlanResult


MAX_FIELDS = 200


def build_plan(
    payload: dict[str, Any],
    profile: dict[str, Any],
    ai_config: AIConfig | None = None,
) -> PlanResult:
    """Validate extension input and build a safe preview."""
    if not isinstance(payload, dict):
        raise ValueError("Forespørselen må være et JSON-objekt.")
    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, list):
        raise ValueError("Forespørselen mangler en feltliste.")
    if len(raw_fields) > MAX_FIELDS:
        raise ValueError(f"Maksimalt {MAX_FIELDS} felt kan behandles om gangen.")
    fields = [FieldDescriptor.from_dict(value) for value in raw_fields]
    if len({field.field_id for field in fields}) != len(fields):
        raise ValueError("Felt-ID-er må være unike.")

    result = build_deterministic_plan(fields, profile)
    config = ai_config or AIConfig.from_env()
    status, message = add_ai_suggestions(
        result.fields,
        fields,
        profile,
        payload.get("page") if isinstance(payload.get("page"), dict) else {},
        config,
    )
    result.ai_status = status
    result.ai_message = message
    return result
