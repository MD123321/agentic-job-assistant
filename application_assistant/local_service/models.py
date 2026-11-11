"""Validated data models shared by the local application-assistant service."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


ALLOWED_FIELD_TYPES = {
    "text",
    "email",
    "tel",
    "url",
    "number",
    "date",
    "textarea",
    "select",
    "radio",
    "checkbox",
    "file",
    "hidden",
    "submit",
    "button",
    "reset",
    "image",
}
ALLOWED_CATEGORIES = {"auto", "review", "requires_action", "unsupported"}


@dataclass(frozen=True)
class FieldDescriptor:
    """A bounded description of one visible form field from the extension."""

    field_id: str
    dom_index: int
    label: str
    name: str = ""
    element_id: str = ""
    tag: str = "input"
    field_type: str = "text"
    autocomplete: str = ""
    placeholder: str = ""
    required: bool = False
    disabled: bool = False
    options: tuple[str, ...] = ()
    group_label: str = ""
    help_text: str = ""
    nearby_text: str = ""
    input_value: str = ""
    accept: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FieldDescriptor":
        if not isinstance(value, dict):
            raise ValueError("Hvert felt må være et objekt.")
        field_id = str(value.get("field_id") or "")[:100]
        if not field_id:
            raise ValueError("Et felt mangler field_id.")
        try:
            dom_index = int(value.get("dom_index"))
        except (TypeError, ValueError) as error:
            raise ValueError("Et felt har ugyldig dom_index.") from error
        if dom_index < 0 or dom_index > 10_000:
            raise ValueError("Et felt har ugyldig dom_index.")
        field_type = str(value.get("field_type") or "text").lower()[:30]
        if field_type not in ALLOWED_FIELD_TYPES:
            field_type = "text"
        raw_options = value.get("options") or []
        if not isinstance(raw_options, list):
            raise ValueError("Feltalternativer må være en liste.")
        return cls(
            field_id=field_id,
            dom_index=dom_index,
            label=str(value.get("label") or "")[:500],
            name=str(value.get("name") or "")[:200],
            element_id=str(value.get("element_id") or "")[:200],
            tag=str(value.get("tag") or "input").lower()[:30],
            field_type=field_type,
            autocomplete=str(value.get("autocomplete") or "")[:100],
            placeholder=str(value.get("placeholder") or "")[:500],
            required=bool(value.get("required")),
            disabled=bool(value.get("disabled")),
            options=tuple(str(option)[:300] for option in raw_options[:100]),
            group_label=str(value.get("group_label") or "")[:500],
            help_text=str(value.get("help_text") or "")[:1_000],
            nearby_text=str(value.get("nearby_text") or "")[:1_500],
            input_value=str(value.get("input_value") or "")[:300],
            accept=str(value.get("accept") or "")[:300],
        )


@dataclass
class FieldPlan:
    """One proposed action shown to the user before form completion."""

    field_id: str
    dom_index: int
    label: str
    field_type: str
    category: str
    value: str = ""
    reason: str = ""
    profile_key: str = ""
    evidence_keys: list[str] = field(default_factory=list)
    selected_by_default: bool = False
    name: str = ""
    element_id: str = ""
    tag: str = "input"
    options: list[str] = field(default_factory=list)
    group_label: str = ""
    help_text: str = ""
    nearby_text: str = ""
    input_value: str = ""
    accept: str = ""
    meaning: str = ""
    confidence: str = ""
    sensitive: bool = False
    fillable: bool = False
    document_key: str = ""

    def __post_init__(self) -> None:
        if self.category not in ALLOWED_CATEGORIES:
            raise ValueError("Ugyldig feltkategori.")

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanResult:
    """A complete preview; it never performs browser actions itself."""

    fields: list[FieldPlan]
    cv_filename: str = ""
    ai_status: str = "disabled"
    ai_message: str = ""

    def payload(self) -> dict[str, Any]:
        counts = {category: 0 for category in ALLOWED_CATEGORIES}
        for item in self.fields:
            counts[item.category] += 1
        return {
            "summary": {
                "total": len(self.fields),
                "auto": counts["auto"],
                "review": counts["review"],
                "requires_action": counts["requires_action"],
                "unsupported": counts["unsupported"],
            },
            "fields": [item.payload() for item in self.fields],
            "cv": {
                "filename": self.cv_filename,
                "upload_mode": "selected_fill" if self.cv_filename else "missing",
            },
            "ai": {"status": self.ai_status, "message": self.ai_message},
            "submission": "manual_only",
        }
