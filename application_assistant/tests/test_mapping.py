"""Tests for deterministic, safety-first field mapping."""

from __future__ import annotations

import unittest

from application_assistant.local_service.mapping import build_deterministic_plan, map_field
from application_assistant.local_service.models import FieldDescriptor
from application_assistant.local_service.profile import profile_values


PROFILE = {
    "personal": {
        "first_name": "Test",
        "last_name": "Candidate",
        "email": "candidate@example.com",
        "phone": "+47 000 00 000",
        "gender": "Male",
        "date_of_birth": "2004-03-29",
        "city": "Oslo",
        "linkedin": "https://linkedin.com/in/example",
    },
    "education": {
        "school": "Example School",
        "degree": "Bachelor",
        "expected_graduation": "2027",
    },
    "experience": [{"role": "Intern", "company": "Example", "summary": "Analysis"}],
    "languages": ["Norwegian", "English"],
    "skills": ["Python", "SQL"],
    "availability": "Summer 2027",
    "approved_answers": {
        "notice_period": "Not applicable",
        "preferred_location": "Oslo",
        "communication_language": "English",
    },
    "documents": {
        "standard_cv": "/private/path/standard-cv.pdf",
        "bachelor_transcript": "bachelor-transcript.pdf",
    },
}


def field(label: str, field_type: str = "text", **kwargs) -> FieldDescriptor:
    return FieldDescriptor(
        field_id=kwargs.pop("field_id", "field-1"),
        dom_index=kwargs.pop("dom_index", 1),
        label=label,
        field_type=field_type,
        **kwargs,
    )


class MappingTests(unittest.TestCase):
    def test_maps_common_safe_fields_from_documented_profile(self) -> None:
        labels = [
            "First name",
            "Last name",
            "Email",
            "Phone",
            "City",
            "LinkedIn",
            "University",
            "Degree",
            "Expected graduation",
            "Languages",
            "Skills",
            "Work experience",
        ]
        fields = [
            field(label, field_id=f"field-{index}", dom_index=index)
            for index, label in enumerate(labels)
        ]

        result = build_deterministic_plan(fields, PROFILE)

        self.assertEqual(len(result.fields), 12)
        self.assertTrue(all(item.category == "auto" for item in result.fields))
        self.assertTrue(all(item.value for item in result.fields))
        self.assertTrue(all(item.selected_by_default for item in result.fields))

    def test_unknown_field_requires_user_action(self) -> None:
        plan = map_field(field("Favorite interview snack"), profile_values(PROFILE), "cv.pdf")

        self.assertEqual(plan.category, "requires_action")
        self.assertEqual(plan.value, "")

    def test_company_name_is_not_mistaken_for_candidate_name(self) -> None:
        plan = map_field(field("Company name"), profile_values(PROFILE), "cv.pdf")

        self.assertEqual(plan.category, "requires_action")
        self.assertEqual(plan.value, "")

    def test_sensitive_field_is_never_answered(self) -> None:
        plan = map_field(
            field("Are you legally authorized to work in Norway?", "radio"),
            profile_values(PROFILE),
            "cv.pdf",
        )

        self.assertEqual(plan.category, "requires_action")
        self.assertEqual(plan.value, "")
        self.assertTrue(plan.sensitive)
        self.assertTrue(plan.fillable)

    def test_explicit_local_demographic_value_can_fill_radio(self) -> None:
        plan = map_field(
            field(
                "Female",
                "radio",
                group_label="Gender",
                options=("Female", "Male"),
            ),
            profile_values(PROFILE),
            "cv.pdf",
        )

        self.assertTrue(plan.sensitive)
        self.assertEqual((plan.category, plan.value), ("auto", "Male"))
        self.assertTrue(plan.selected_by_default)

    def test_birth_year_is_derived_from_documented_birth_date(self) -> None:
        plan = map_field(field("Year of birth"), profile_values(PROFILE), "cv.pdf")

        self.assertEqual((plan.category, plan.value), ("auto", "2004"))
        self.assertTrue(plan.sensitive)

    def test_missing_candidate_data_requires_user_action(self) -> None:
        profile = {**PROFILE, "personal": {**PROFILE["personal"], "phone": ""}}
        plan = map_field(field("Phone"), profile_values(profile), "cv.pdf")

        self.assertEqual(plan.category, "requires_action")
        self.assertIn("mangler", plan.reason)

    def test_review_fields_are_not_selected_by_default(self) -> None:
        plan = map_field(field("Notice period"), profile_values(PROFILE), "cv.pdf")

        self.assertEqual(plan.category, "review")
        self.assertEqual(plan.value, "Not applicable")
        self.assertFalse(plan.selected_by_default)

    def test_approved_standard_answer_can_fill_select_or_radio(self) -> None:
        select = map_field(
            field("Preferred location", "select", options=("Oslo", "Bergen")),
            profile_values(PROFILE),
            "cv.pdf",
        )
        radio = map_field(
            field("Communication language", "radio", options=("English", "Norwegian")),
            profile_values(PROFILE),
            "cv.pdf",
        )

        self.assertEqual((select.category, select.value), ("auto", "Oslo"))
        self.assertEqual((radio.category, radio.value), ("auto", "English"))

    def test_standard_answer_not_present_in_options_requires_action(self) -> None:
        plan = map_field(
            field("Preferred location", "select", options=("Bergen", "Trondheim")),
            profile_values(PROFILE),
            "cv.pdf",
        )

        self.assertEqual(plan.category, "requires_action")
        self.assertEqual(plan.value, "")

    def test_configured_cv_and_bachelor_transcript_are_ready_for_upload(self) -> None:
        result = build_deterministic_plan(
            [
                field("Upload CV", "file", field_id="field-1"),
                field(
                    "Please upload a transcript of your B.Sc. degree",
                    "file",
                    field_id="field-2",
                ),
            ],
            PROFILE,
        )

        self.assertEqual(result.cv_filename, "standard-cv.pdf")
        self.assertEqual(
            [(item.category, item.value, item.document_key) for item in result.fields],
            [
                ("auto", "standard-cv.pdf", "standard_cv"),
                ("auto", "bachelor-transcript.pdf", "bachelor_transcript"),
            ],
        )
        self.assertTrue(all(item.selected_by_default for item in result.fields))

    def test_optional_unconfigured_master_transcript_is_not_an_action(self) -> None:
        plan = build_deterministic_plan(
            [field("Upload your M.Sc. transcript if applicable", "file", required=False)],
            PROFILE,
        ).fields[0]

        self.assertEqual(plan.category, "unsupported")

    def test_teamtailor_known_data_reduces_required_actions(self) -> None:
        teamtailor_fields = [
            field("Please state your gender", "radio", field_id="field-1", options=("Female", "Male")),
            field("Year of birth", field_id="field-2"),
            field("Are you fluent in both Swedish and English?", "radio", field_id="field-3", options=("Yes", "No")),
            field("Please upload a transcript of your B.Sc. degree", "file", field_id="field-4", required=True),
            field("Please upload a transcript of your M.Sc. degree if applicable", "file", field_id="field-5"),
            field("Year of graduation", field_id="field-6"),
            field("19 January - 26 March", "checkbox", field_id="field-7"),
            field("30 March - 4 June", "checkbox", field_id="field-8"),
            field("8 June - 13 August", "checkbox", field_id="field-9"),
            field("ABGSC employees you have contacted, if any", "textarea", field_id="field-10"),
            field("Email", "email", field_id="field-11"),
            field("Phone", "tel", field_id="field-12"),
            field("Upload CV", "file", field_id="field-13", required=True),
            field("Additional files", "file", field_id="field-14"),
            field("I agree to the privacy policy", "checkbox", field_id="field-15"),
            field("Contact me about future jobs", "checkbox", field_id="field-16"),
        ]

        result = build_deterministic_plan(teamtailor_fields, PROFILE)
        by_id = {item.field_id: item for item in result.fields}

        self.assertEqual(
            {item.field_id for item in result.fields if item.category == "auto"},
            {"field-1", "field-2", "field-4", "field-6", "field-11", "field-12", "field-13"},
        )
        self.assertEqual(by_id["field-5"].category, "unsupported")
        self.assertEqual(by_id["field-14"].category, "unsupported")
        self.assertEqual(
            {item.field_id for item in result.fields if item.category == "requires_action"},
            {"field-3", "field-7", "field-8", "field-9", "field-10", "field-15", "field-16"},
        )

    def test_submit_and_consent_controls_are_never_fillable(self) -> None:
        submit = map_field(field("Submit application", "submit"), profile_values(PROFILE), "")
        consent = map_field(field("I agree", "checkbox"), profile_values(PROFILE), "")

        self.assertEqual(submit.category, "unsupported")
        self.assertEqual(consent.category, "requires_action")
        self.assertFalse(consent.fillable)


if __name__ == "__main__":
    unittest.main()
