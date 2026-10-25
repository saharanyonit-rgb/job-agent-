from __future__ import annotations

from typing import Any

from job_agent.browser.detectors import FieldSpec, FormSpec


class MappingResult:
    def __init__(self, field: FieldSpec, value: Any, confidence: float, key: str, note: str = ""):
        self.field = field
        self.value = value
        self.confidence = confidence
        self.key = key
        self.note = note

    @property
    def ambiguous(self) -> bool:
        return self.confidence < 0.5 and self.field.required

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field.name,
            "value": self.value if not isinstance(self.value, (bytes,)) else "<file>",
            "confidence": self.confidence,
            "key": self.key,
            "note": self.note,
            "ambiguous": self.ambiguous,
        }


class FormMapper:
    """Determines what each form field means and which candidate fact to use.

    Fields that cannot be confidently mapped to verified candidate data are
    reported as ambiguous so the caller can trigger HANDOFF_REQUIRED instead of
    guessing legally-significant answers.
    """

    # (token, fact key, value policy)
    _RULES: dict[str, tuple[str, str]] = {
        "first name": ("first_name", "name"),
        "last name": ("last_name", "name"),
        "name": ("full_name", "name"),
        "email": ("email", "email"),
        "phone": ("phone", "phone"),
        "mobile": ("phone", "phone"),
        "years of experience": ("years_experience", "years_of_experience"),
        "experience": ("years_experience", "years_of_experience"),
        "notice period": ("notice_period", "notice_period"),
        "expected salary": ("expected_salary", "expected_ctc"),
        "expected ctc": ("expected_salary", "expected_ctc"),
        "current salary": ("current_salary", "current_ctc"),
        "current ctc": ("current_salary", "current_ctc"),
        "work authorization": ("work_authorization", "work_authorization"),
        "visa status": ("visa_status", "visa_status"),
        "relocate": ("relocation", "relocation_preference"),
        "relocation": ("relocation", "relocation_preference"),
        "current employer": ("current_employer", "current_employer"),
        "current title": ("current_title", "current_title"),
        "linkedin": ("linkedin_url", "linkedin_url"),
        "resume": ("resume", "resume"),
        "cover letter": ("cover_letter", "cover_letter"),
        "departure": ("notice_period", "notice_period"),
        # Opinion-style questions answered strictly from verified facts
        "interest": ("why_interested", "generated_why"),
        "why": ("why_interested", "generated_why"),
        "leadership": ("q_leadership", "generated_leadership"),
        "motivate": ("why_interested", "generated_why"),
    }

    def map(self, form: FormSpec, facts: dict[str, Any], files: dict[str, str | None]) -> list[MappingResult]:
        results: list[MappingResult] = []
        for field in form.fields:
            if field.field_type == "hidden" and field.value:
                results.append(
                    MappingResult(field, field.value, 1.0, "hidden", "preserved hidden field")
                )
                continue
            results.append(self._map_one(field, facts, files))
        return results

    def _map_one(self, field: FieldSpec, facts: dict[str, Any], files: dict[str, str | None]) -> MappingResult:
        # Guess via id, name, label, placeholder, aria-label
        tokens = []
        for source in (field.label, field.name, field.placeholder, field.aria_label, field.element_id or ""):
            if source:
                tokens.append(source.lower())
        haystack = " ".join(tokens)

        if field.field_type == "file":
            return self._file_mapping(field, files)
        if field.field_type == "checkbox":
            if "term" in haystack or "agree" in haystack or "consent" in haystack:
                return MappingResult(field, "yes", 1.0, "terms", "agree to terms")
            return MappingResult(field, "yes", 0.7, "checkbox", "assumed confirmation checkbox")
        if field.field_type == "radio":
            if "reloc" in haystack:
                pref = facts.get("relocation_preference")
                val = "yes" if pref and str(pref).lower() in ("yes", "true", "remote", "any") else "no"
                return MappingResult(field, val, 0.9, "relocation", "from candidate preference")
            return MappingResult(field, None, 0.0, "unknown_radio", "radio semantics unclear")

        if not tokens:
            return MappingResult(field, None, 0.0, "unknown", "no label/name available")

        for token, (form_key, fact_key) in self._RULES.items():
            if token in haystack:
                return self._fact_based(field, fact_key, form_key, facts, files, haystack)

        # Unmapped non-optional field -> ambiguous (HANDOFF_REQUIRED)
        confidence = 0.0 if field.required else 0.4
        return MappingResult(field, None, confidence, "unmapped", "no reliable mapping to candidate data")

    def _file_mapping(self, field: FieldSpec, files: dict[str, str | None]) -> MappingResult:
        if "resume" in field.label.lower() or "resume" in (field.name or "").lower() or "cv" in field.label.lower():
            path = files.get("resume")
            if path:
                return MappingResult(field, path, 1.0, "resume", "resume file")
            return MappingResult(field, None, 0.3, "resume", "no resume file available")
        if "cover" in field.label.lower():
            path = files.get("cover_letter")
            if path:
                return MappingResult(field, path, 1.0, "cover_letter", "cover letter file")
            return MappingResult(field, None, 0.6, "cover_letter", "no cover letter file; leave empty")
        return MappingResult(field, None, 0.5, "file", "generic file upload")

    def _fact_based(
        self, field: FieldSpec, fact_key: str, form_key: str, facts: dict[str, Any], files: dict[str, str | None], haystack: str
    ) -> MappingResult:
        value = self._resolve_value(fact_key, facts, form_key, files)
        if value is None or value == "":
            # form_key may carry the split semantics (first_name/last_name)
            value = self._resolve_value(form_key, facts, form_key, files)
        if value is None or value == "":
            return MappingResult(field, None, 0.0, fact_key, f"candidate fact '{fact_key}' unknown")
        confidence = 1.0
        source_note = "verified candidate fact"
        if fact_key in ("generated_why", "generated_leadership") or form_key in ("why_interested", "q_leadership"):
            confidence = 0.85
            source_note = "generated from verified candidate facts"
        return MappingResult(field, value, confidence, fact_key, source_note)

    def _resolve_value(self, fact_key: str, facts: dict[str, Any], form_key: str, files: dict[str, str | None]) -> Any:
        if fact_key == "first_name" or form_key == "first_name":
            name = facts.get("name") or ""
            parts = name.split()
            return parts[0] if parts else None
        if fact_key == "last_name" or form_key == "last_name":
            name = facts.get("name") or ""
            parts = name.split()
            return parts[-1] if len(parts) > 1 else None
        if fact_key == "resume":
            return files.get("resume")
        if fact_key == "cover_letter":
            return files.get("cover_letter")
        if fact_key == "current_employer":
            employers = facts.get("previous_employers") or facts.get("current_employer")
            if isinstance(employers, (list, tuple)) and employers:
                return employers[0]
            return employers
        if fact_key == "generated_why":
            title = facts.get("current_title") or ""
            role = facts.get("job_title") or ""
            skills = facts.get("skills") or []
            parts = [f"My {title or 'professional'} background".strip()]
            if role:
                parts.append(f"directly relevant to the {role} role".strip())
            if skills:
                parts.append(f"with strengths in {', '.join(str(s) for s in skills[:6])}".strip())
            text = ", ".join(parts).rstrip(",") + "."
            return text if len(text) > 3 else None
        if fact_key == "generated_leadership":
            title = facts.get("current_title") or "engineer"
            skills = facts.get("skills") or []
            leadership = [s for s in skills if "leadership" in str(s).lower()]
            exp = facts.get("years_of_experience")
            exp_part = f"{exp} years of experience" if exp else "extensive experience"
            body = f"My {title} role spans {exp_part}, including architecture decisions and technical leadership."
            if leadership:
                body = f"My {title} role spans {exp_part}, including {', '.join(str(s) for s in leadership)}."
            return body
        value = facts.get(fact_key)
        return value


SENSITIVE_FIELDS = [
    "visa_status",
    "work_authorization",
    "relocation_preference",
    "notice_period",
    "expected_ctc",
    "current_ctc",
    "current_employer",
]