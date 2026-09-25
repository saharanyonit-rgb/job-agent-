from __future__ import annotations

from typing import Any

from job_agent.ai.service import chain
from job_agent.candidates.models import CandidateFactCollection
from job_agent.database import models as m
from job_agent.core.logging_config import get_logger

log = get_logger(__name__)


class InterviewPreparationAgent:
    def __init__(self, ai=chain):
        self.ai = ai

    def prepare(self, interview: m.Interview, facts: CandidateFactCollection, job: m.Job | None) -> dict[str, Any]:
        company = interview.company
        role = interview.role
        round_name = interview.round or "General"
        skills = facts.value("skills") or []
        projects = facts.value("projects") or []
        employers = facts.value("previous_employers") or []
        job_desc = (job.description if job else "") or ""

        topics: list[str] = []
        for s in skills:
            if any(k in str(s).lower() for k in ["architect", "design", "azure", "aws", "kubernetes", "microservices", "llm", "genai", "rag", "system"]):
                topics.append(f"Architecture deep-dive: {s}")
        if job_desc:
            for token in ["microservices", "kubernetes", "azure", "aws", "api design", "system design", "cloud"]:
                if token in job_desc.lower():
                    topics.append(f"Review {token} patterns and trade-offs")

        behavioral = [
            "Tell me about a system you designed and the trade-offs you made.",
            "Describe a time you had to align multiple stakeholders.",
            "How do you handle an unrealistic deadline?",
            f"Why are you interested in {company} for a {role} role?",
        ]
        star_stories = [
            {
                "situation": f"Leading architecture at one of your employers ({emp})" if employers else "Architecture decision on a past engagement",
                "task": "Define a scalable, maintainable solution",
                "action": "Gathered requirements, produced design, validated with team, delivered incrementally",
                "result": "Shipped with measurable improvement",
            }
            for emp in (employers[:3] if employers else [""])
        ]
        questions_for_interviewer = [
            "What does a successful first 90 days look like in this role?",
            "Which team will I work most closely with?",
            "How is technical architecture reviewed here?",
        ]
        weak_areas = ["Tailor specifics to the verified interviewer context when available"]

        plan_minutes: list[dict[str, Any]] = [
            {"minutes": "0-10", "task": "Review company and role", "detail": f"Research {company}, product, and the {role} responsibilities"},
            {"minutes": "10-20", "task": "Rehearse STAR stories", "detail": "Focus on the strongest 2 stories"},
            {"minutes": "20-25", "task": "Architecture topics", "detail": ", ".join(topics[:3])},
            {"minutes": "25-30", "task": "Questions for interviewer", "detail": "Pick your best 3 questions"},
        ]

        return {
            "company": company,
            "role": role,
            "round": round_name,
            "company_research_focus": [
                f"Product/sector of {company}",
                "Recent news and funding",
                "Engineering culture signals from the posting",
            ],
            "role_summary": f"Follow-up questions around {role} expectations and {round_name}.",
            "job_requirements": (job.skills or []) if job else [],
            "relevant_projects": projects or [],
            "technical_topics": topics or [],
            "architecture_topics": [t for t in topics if "Architecture" in t] or ["System design", "Cloud infrastructure", "Scalability patterns"],
            "behavioral_questions": behavioral,
            "star_stories": star_stories,
            "questions_for_interviewer": questions_for_interviewer,
            "resume_specific_questions": [f"Walk me through {emp or 'your current role'}." for emp in (employers or ["your background"])],
            "potential_weak_areas": weak_areas,
            "plan_30_minutes": plan_minutes,
            "generated_at_utc": __import__("datetime").datetime.utcnow().isoformat(),
        }