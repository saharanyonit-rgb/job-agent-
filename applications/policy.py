from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from core.database import models as m
from core.core.config import get_settings

MODES = ["DISCOVERY_ONLY", "PREPARE_AND_APPROVE", "AUTONOMOUS"]

# Strategy classes gate applications before the policy decides submission.
# All are "quality gates" rather than match scores alone.
SKIP_UNLESS_APPROVED = {"HARD_REQUIREMENT_FAILURE", "HIGH_RISK", "HANDOFF_REQUIRED"}


@dataclass
class ApplicationPolicy:
    mode: str = "PREPARE_AND_APPROVE"
    minimum_match: float = 80.0
    minimum_salary: float | None = None
    allowed_locations: list[str] = field(default_factory=list)
    allowed_roles: list[str] = field(default_factory=list)
    excluded_companies: list[str] = field(default_factory=list)
    max_daily_applications: int = 20
    require_verified: bool = True
    # Phase 3 / Phase 16 limits: anti-spam + focus.
    max_per_company: int = 3
    max_per_role: int = 5
    company_cooldown_days: int = 30
    min_evidence_confidence: float = 0.8
    max_hard_failures: int = 0
    # Phase hardening: low-confidence application budget (fail-closed gate).
    max_low_confidence_applications: int = 5
    low_confidence_window_hours: int = 24
    low_confidence_window_align_utc_day: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "minimum_match": self.minimum_match,
            "minimum_salary": self.minimum_salary,
            "allowed_locations": self.allowed_locations,
            "allowed_roles": self.allowed_roles,
            "excluded_companies": self.excluded_companies,
            "max_daily_applications": self.max_daily_applications,
            "require_verified": self.require_verified,
            "max_per_company": self.max_per_company,
            "max_per_role": self.max_per_role,
            "company_cooldown_days": self.company_cooldown_days,
            "min_evidence_confidence": self.min_evidence_confidence,
            "max_hard_failures": self.max_hard_failures,
            "max_low_confidence_applications": self.max_low_confidence_applications,
            "low_confidence_window_hours": self.low_confidence_window_hours,
            "low_confidence_window_align_utc_day": self.low_confidence_window_align_utc_day,
        }


class PolicyService:
    KEY = "application_policy"

    def __init__(self, db: Session):
        self.db = db

    def get(self) -> ApplicationPolicy:
        row = self.db.query(m.AgentSetting).filter(m.AgentSetting.key == self.KEY).first()
        if not row:
            settings = get_settings()
            policy = ApplicationPolicy(
                mode=settings.app_mode,
                minimum_match=float(settings.minimum_match),
                max_daily_applications=settings.max_daily_applications,
                max_low_confidence_applications=settings.max_low_confidence_applications,
                low_confidence_window_hours=settings.low_confidence_window_hours,
                low_confidence_window_align_utc_day=settings.low_confidence_window_align_utc_day,
            )
            return policy
        import json

        data = json.loads(row.value)
        return ApplicationPolicy(**{k: data.get(k, getattr(ApplicationPolicy(), k)) for k in ApplicationPolicy.__dataclass_fields__})

    def save(self, policy: ApplicationPolicy) -> None:
        import json

        if policy.mode not in MODES:
            raise ValueError(f"Invalid mode {policy.mode}; must be one of {MODES}")
        payload = json.dumps(policy.to_dict())
        row = self.db.query(m.AgentSetting).filter(m.AgentSetting.key == self.KEY).first()
        if row:
            row.value = payload
        else:
            self.db.add(m.AgentSetting(key=self.KEY, value=payload))
        self.db.commit()

    def update(self, **kwargs: Any) -> ApplicationPolicy:
        policy = self.get()
        for key, value in kwargs.items():
            if value is None:
                continue
            if key in ApplicationPolicy.__dataclass_fields__:
                setattr(policy, key, value)
        self.save(policy)
        return policy

    def evaluate_low_confidence_budget(self, candidate_id: int, *, exclude_application_id: int | None = None, now: datetime | None = None) -> dict[str, Any]:
        """Fail-closed gate: enforce MAX_LOW_CONFIDENCE_APPLICATIONS.

        Counts low-confidence applications that actually reached submission in
        the configured window (current application excluded). Over the limit ->
        HANDOFF_REQUIRED. Do NOT merely log.
        """
        from core.applications.budget import LowConfidenceBudget

        policy = self.get()
        budget = LowConfidenceBudget(self.db)
        return budget.decide(
            candidate_id,
            exclude_application_id=exclude_application_id,
            max_low_confidence_applications=policy.max_low_confidence_applications,
            window_hours=policy.low_confidence_window_hours,
            align_to_utc_day=policy.low_confidence_window_align_utc_day,
            min_evidence_confidence=policy.min_evidence_confidence,
            now=now,
        )

    def decide(
        self,
        match_score: float,
        job: m.Job,
        handoff_reasons: list[str] | None = None,
        *,
        strategy_class: str | None = None,
        hard_failures: list[str] | None = None,
        evidence_confidence: float | None = None,
        company_application_count: int = 0,
        same_role_application_count: int = 0,
        last_company_application_at: datetime | None = None,
        candidate_id: int | None = None,
        exclude_application_id: int | None = None,
    ) -> dict[str, Any]:
        """Decide whether to submit, prepare only, or hand off.

        Combines classic policy checks with the Phase 3 strategy gates and the
        Phase 16 anti-spam/quality limits. Always: skip means skip (recorded via
        ApplicationStrategy), hand-off means a human must act.
        """
        policy = self.get()
        handoff_reasons = list(handoff_reasons or [])
        reasons: list[str] = []

        if handoff_reasons:
            return {"action": "HANDOFF_REQUIRED", "reasons": handoff_reasons, "policy": policy.to_dict()}

        if policy.mode == "DISCOVERY_ONLY":
            return {"action": "DISCOVERY_ONLY", "reasons": ["policy mode is DISCOVERY_ONLY"], "policy": policy.to_dict()}

        if strategy_class in SKIP_UNLESS_APPROVED and strategy_class != "HIGH_RISK":
            reasons.append(f"strategy classification is {strategy_class}")
        if strategy_class == "HIGH_RISK":
            reasons.append("application scored HIGH_RISK")

        hard_failures = hard_failures or []
        if hard_failures and len(hard_failures) > policy.max_hard_failures:
            reasons.append(f"hard requirement failures: {', '.join(hard_failures[:5])}")

        if match_score < policy.minimum_match:
            reasons.append(f"match {match_score} < minimum {policy.minimum_match}")

        if policy.require_verified and job.verification_status != "ACTIVE":
            reasons.append(f"job verification status is {job.verification_status}")

        if policy.excluded_companies and any(
            ec.lower() == (job.company or "").lower() for ec in policy.excluded_companies
        ):
            reasons.append("company is excluded")

        if policy.allowed_roles and not any(
            ar.lower() in (job.normalized_title or job.title or "").lower()
            or (job.normalized_title or "").lower() in ar.lower()
            for ar in policy.allowed_roles
        ):
            reasons.append("role not in allowed_roles")

        if policy.allowed_locations and not any(
            al.lower() in (job.location or "").lower() for al in policy.allowed_locations
        ):
            reasons.append("location not in allowed_locations")

        if policy.minimum_salary and job.salary_max is not None and job.salary_max < policy.minimum_salary:
            reasons.append("salary below minimum_salary")

        if self._daily_application_count() >= policy.max_daily_applications:
            reasons.append("max_daily_applications reached")

        if policy.max_per_company and company_application_count >= policy.max_per_company:
            reasons.append(f"max_per_company reached ({company_application_count})")

        if policy.max_per_role and same_role_application_count >= policy.max_per_role:
            reasons.append(f"max_per_role reached ({same_role_application_count})")

        if (
            policy.company_cooldown_days
            and last_company_application_at
            and (datetime.utcnow() - last_company_application_at) < timedelta(days=policy.company_cooldown_days)
        ):
            reasons.append("company cooldown not elapsed")

        if (
            evidence_confidence is not None
            and policy.min_evidence_confidence
            and evidence_confidence < policy.min_evidence_confidence
        ):
            reasons.append(f"evidence confidence {evidence_confidence} < {policy.min_evidence_confidence}")

        # Fail-closed low-confidence budget gate (enforced, not logged).
        budget_blocked = False
        if candidate_id is not None and policy.max_low_confidence_applications is not None:
            budget = self.evaluate_low_confidence_budget(
                candidate_id,
                exclude_application_id=exclude_application_id,
            )
            if budget["exceeded"]:
                budget_blocked = True
                reasons.extend(budget["reasons"])

        if budget_blocked:
            return {
                "action": "HANDOFF_REQUIRED",
                "decision": "NEED_MORE_EVIDENCE",
                "reasons": reasons,
                "policy": policy.to_dict(),
                "budget": {
                    "count": self._low_confidence_count(candidate_id, exclude_application_id),
                    "limit": policy.max_low_confidence_applications,
                },
            }

        if reasons:
            return {"action": "SKIP", "reasons": reasons, "policy": policy.to_dict()}

        if policy.mode == "AUTONOMOUS":
            return {"action": "SUBMIT", "reasons": ["autonomous mode, all gates passed"], "policy": policy.to_dict()}
        return {"action": "READY_FOR_SUBMISSION", "reasons": ["prepared; awaiting candidate approval"], "policy": policy.to_dict()}

    def _daily_application_count(self) -> int:
        start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            self.db.query(m.Application)
            .filter(m.Application.submitted_at >= start)
            .count()
        )

    def _low_confidence_count(self, candidate_id: int, exclude_application_id: int | None) -> int:
        from core.applications.budget import LowConfidenceBudget

        policy = self.get()
        return LowConfidenceBudget(self.db).count(
            candidate_id,
            exclude_application_id=exclude_application_id,
            window_hours=policy.low_confidence_window_hours,
            align_to_utc_day=policy.low_confidence_window_align_utc_day,
            min_evidence_confidence=policy.min_evidence_confidence,
        )