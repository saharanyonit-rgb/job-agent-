from __future__ import annotations

import re
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from job_agent.api.deps import DiscreteAgents, JobAgentOrchestrator, get_db
from job_agent.database import models as m

router = APIRouter(prefix="/api/commands", tags=["commands"])

DEFAULT_CANDIDATE_ID = 1

# Intent pattern matching for supported natural-language commands
_INTENTS = [
    (re.compile(r"find jobs|discover jobs", re.I), "find_jobs"),
    (re.compile(r"apply to all eligible|apply to all", re.I), "apply_all"),
    (re.compile(r"pause applications|pause", re.I), "pause"),
    (re.compile(r"resume applications|resume", re.I), "resume"),
    (re.compile(r"show my applications|my applications", re.I), "show_applications"),
    (re.compile(r"what jobs did you apply to today|applied to today", re.I), "applied_today"),
    (re.compile(r"when is my.*interview|next interview", re.I), "next_interview"),
    (re.compile(r"show my interviews", re.I), "show_interviews"),
    (re.compile(r"prepare me for my.*interview|interview prep", re.I), "prepare_interview"),
    (re.compile(r"waiting for action|action required|handoff|hand off", re.I), "waiting_action"),
    (re.compile(r"follow ?up|companies that haven't", re.I), "followup"),
    (re.compile(r"why did you apply|why you applied|explain.*match", re.I), "explain_match"),
    (re.compile(r"daily report", re.I), "daily_report"),
]


class CommandHandler:
    def __init__(self, db: Session):
        self.db = db
        self.orch = JobAgentOrchestrator(db)
        self.agents = DiscreteAgents(db)
        self.state = {"paused": False}

    def run(self, command: str) -> dict:
        for pattern, intent in _INTENTS:
            if pattern.search(command):
                return self.handle(intent, command)
        return {"intent": "unknown", "response": "I don't understand that command yet."}

    def handle(self, intent: str, command: str) -> dict:
        if intent == "find_jobs":
            result = self.orch.cycle(DEFAULT_CANDIDATE_ID)
            return {"intent": intent, "response": "Jobs discovered, verified, matched, and prepared.", "result": result}
        if intent == "apply_all":
            result = self.orch.prepare_and_submit_applications(DEFAULT_CANDIDATE_ID)
            return {"intent": intent, "response": "Application processing complete.", "result": result}
        if intent == "pause":
            self.state["paused"] = True
            return {"intent": intent, "response": "Applications paused."}
        if intent == "resume":
            self.state["paused"] = False
            return {"intent": intent, "response": "Applications resumed."}
        if intent == "show_applications":
            apps = self.db.query(m.Application).order_by(m.Application.id.desc()).limit(50).all()
            return {
                "intent": intent,
                "response": [
                    {"id": a.id, "job_id": a.job_id, "status": a.status, "match": a.match_score, "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None}
                    for a in apps
                ],
            }
        if intent == "applied_today":
            from datetime import datetime

            start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            apps = self.db.query(m.Application).filter(m.Application.submitted_at >= start).all()
            return {"intent": intent, "response": [{"id": a.id, "job_id": a.job_id, "application_id": a.application_id_external} for a in apps]}
        if intent == "next_interview":
            data = self.agents.when_is_my_interview()
            return {"intent": intent, **data}
        if intent == "show_interviews":
            interviews = self.db.query(m.Interview).order_by(m.Interview.interview_date.desc()).all()
            return {
                "intent": intent,
                "response": [
                    {"company": i.company, "role": i.role, "round": i.round, "date": i.interview_date.isoformat() if i.interview_date else None, "status": i.status}
                    for i in interviews
                ],
            }
        if intent == "prepare_interview":
            interview = self.agents.interviews.next_confirmed()
            if not interview:
                return {"intent": intent, "response": "No confirmed interview to prepare for."}
            package = self.agents.prepare_interview(interview.id, DEFAULT_CANDIDATE_ID)
            return {"intent": intent, "interview_id": interview.id, "package": package}
        if intent == "waiting_action":
            handoffs = self.db.query(m.Application).filter(m.Application.status == "HANDOFF_REQUIRED").all()
            return {"intent": intent, "response": [{"id": a.id, "job_id": a.job_id, "reason": a.handoff_reason} for a in handoffs]}
        if intent == "followup":
            from job_agent.followups.engine import FollowupEngine

            engine = FollowupEngine(self.db)
            plans = engine.plan_pending()
            engine.record_plans(plans)
            sent = engine.process_followups()
            return {"intent": intent, "planned": len(plans), "sent": sent}
        if intent == "explain_match":
            match = re.search(r"(\d+)", command)
            app_id = int(match.group(1)) if match else None
            if app_id:
                app = self.db.get(m.Application, app_id)
                if app:
                    return {"intent": intent, "explanation": app.match_explanation, "status": app.status}
            return {"intent": intent, "response": "Pass an application id, e.g. 'show why you applied to job 3'."}
        if intent == "daily_report":
            return {"intent": intent, "response": self._daily_report()}
        return {"intent": intent, "response": "Command not fully handled."}

    def _daily_report(self) -> dict:
        from job_agent.core.observability import ObservabilityService

        obs = ObservabilityService(self.db)
        dash = self.db
        jobs_discovered = dash.query(m.Job).count()
        verified = dash.query(m.Job).filter(m.Job.verification_status == "ACTIVE").count()
        submitted = dash.query(m.Application).filter(m.Application.submitted_at.isnot(None)).count()
        interviews = dash.query(m.Interview).count()
        handoffs = dash.query(m.Application).filter(m.Application.status == "HANDOFF_REQUIRED").all()
        return {
            "jobs_discovered": jobs_discovered,
            "verified": verified,
            "applications_submitted": submitted,
            "interviews_scheduled": interviews,
            "action_required": [{"application_id": h.id, "reason": h.handoff_reason} for h in handoffs],
            "audit_tail": obs.audit_trail(limit=10),
        }


@router.post("")
def run_command(command: str, db: Session = Depends(get_db)) -> dict:
    return CommandHandler(db).run(command)