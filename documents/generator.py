from __future__ import annotations

import json
import re
from typing import Any

from job_agent.ai.service import chain
from job_agent.candidates.models import CandidateFactCollection
from job_agent.database import models as m
from job_agent.core.logging_config import get_logger

log = get_logger(__name__)

SYSTEM = (
    "You generate job application materials. Only use facts provided. "
    "Never invent employment, skills, certifications, degrees, projects, "
    "salary, visa status, work authorization, or notice period. "
    "If a fact is unknown, leave it out. Return valid JSON when asked for JSON."
)


class DocumentGenerator:
    def __init__(self, ai=chain):
        self.ai = ai

    def tailor_resume(
        self,
        facts: CandidateFactCollection,
        job: m.Job,
        original_resume: m.Resume,
    ) -> dict[str, Any]:
        """Produce tailored resume text sections ordered for this job."""
        skills = facts.value("skills") or []
        job_skills = [s for s in (job.skills or []) if str(s).lower() in {str(x).lower() for x in skills}]
        summary = facts.value("summary") or ""
        employers = facts.value("previous_employers") or []
        projects = facts.value("projects") or []
        certs = facts.value("certifications") or []

        prompt = (
            "Tailor the following resume summary for this job. Return ONLY JSON with keys "
            "summary, highlighted_skills (array), headline.\n\n"
            f"JOB: {job.title} at {job.company}\n"
            f"Job skills: {job_skills}\n"
            f"Candidate summary: {summary}\n"
            f"Candidate skills: {skills}\n"
            f"Known employers: {employers}\n"
        )
        try:
            out = self.ai.structured_output(prompt, system=SYSTEM)
        except Exception as exc:
            log.warning("AI resume tailoring failed, using deterministic fallback: %s", exc)
            out = {}

        headline = out.get("headline") or f"{facts.value('current_title') or 'Professional'} applying for {job.title}"
        tailored_summary = out.get("summary") or summary
        highlighted = out.get("highlighted_skills") or job_skills or skills[:10]

        lines = [
            f"# {facts.value('name') or ''}".strip(),
            headline,
            "",
            "## Summary",
            tailored_summary,
            "",
            "## Skills (job-aligned)",
            ", ".join(str(s) for s in highlighted),
            "",
            "## Experience",
        ]
        for emp in employers:
            lines.append(f"- {emp}")
        if projects:
            lines.append("")
            lines.append("## Projects")
            for p in projects:
                lines.append(f"- {p}")
        if certs:
            lines.append("")
            lines.append("## Certifications")
            for c in certs:
                lines.append(f"- {c}")

        return {
            "text": "\n".join(lines),
            "parsed": {
                "headline": headline,
                "highlighted_skills": highlighted,
                "base_resume": original_resume.filename,
                "job": {"title": job.title, "company": job.company},
            },
        }

    def cover_letter(self, facts: CandidateFactCollection, job: m.Job) -> str:
        name = facts.value("name") or "Candidate"
        current_title = facts.value("current_title") or "Professional"
        years = facts.value("years_of_experience")
        skills = facts.value("skills") or []
        matched = [s for s in (job.skills or []) if str(s).lower() in {str(x).lower() for x in skills}]
        exp_line = f" with {int(years)} years of experience" if years else ""
        skills_line = ", ".join(str(s) for s in matched[:6]) or ", ".join(str(s) for s in skills[:6])

        body = f"""Dear {job.company} Hiring Team,

I am writing to apply for the {job.title} position. I am a {current_title}{exp_line}.

Relevant strengths for this role include: {skills_line}.

I would welcome the opportunity to discuss how my background aligns with your requirements.

Regards,
{name}
"""
        if facts.value("email"):
            body += f"{facts.value('email')}\n"
        return body

    def screening_answers(self, facts: CandidateFactCollection, job: m.Job, questions: list[str]) -> list[dict[str, Any]]:
        answers: list[dict[str, Any]] = []
        for q in questions:
            answer = self._answer_question(q, facts, job)
            answers.append({"question": q, "answer": answer["answer"], "source": answer["source"], "confidence": answer["confidence"]})
        return answers

    def _answer_question(self, question: str, facts: CandidateFactCollection, job: m.Job) -> dict[str, Any]:
        q = question.lower()
        known_map = {
            "notice period": "notice_period",
            "expected salary": "expected_ctc",
            "current salary": "current_ctc",
            "current ctc": "current_ctc",
            "work authorization": "work_authorization",
            "visa status": "visa_status",
            "relocate": "relocation_preference",
            "relocation": "relocation_preference",
            "years of experience": "years_of_experience",
            "experience": "years_of_experience",
            "current employer": "previous_employers",
            "current title": "current_title",
            "phone": "phone",
            "email": "email",
        }
        for token, fact_key in known_map.items():
            if token in q:
                value = facts.value(fact_key)
                if value is None or value == "":
                    return {"answer": "UNKNOWN", "source": "unknown", "confidence": 0.0}
                return {"answer": str(value), "source": "candidate_fact", "confidence": 1.0}
        if "why" in q and ("company" in q or "interested" in q or "role" in q):
            return {
                "answer": f"The {job.title} role at {job.company} aligns with my target roles and experience.",
                "source": "generated_from_verified_facts",
                "confidence": 0.8,
            }
        if "skill" in q or "technology" in q:
            skills = facts.value("skills") or []
            return {"answer": ", ".join(str(s) for s in skills[:10]), "source": "resume", "confidence": 0.9}
        # Not answerable from verified facts -> explicit UNKNOWN
        return {"answer": "UNKNOWN", "source": "unknown", "confidence": 0.0}

    def recruiter_message(self, facts: CandidateFactCollection, job: m.Job) -> str:
        name = facts.value("name") or "Candidate"
        title = facts.value("current_title") or ""
        return (
            f"Hello, I recently applied for the {job.title} role at {job.company}. "
            f"I'm {name}{', ' + title if title else ''} and happy to share more detail on my background."
        )


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())[:24] or "job"