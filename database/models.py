from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database.base import Base


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    work_authorization: Mapped[str | None] = mapped_column(String(255), nullable=True)
    visa_status: Mapped[str | None] = mapped_column(String(255), nullable=True)
    years_of_experience: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notice_period: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_ctc: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expected_ctc: Mapped[str | None] = mapped_column(String(100), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote_preference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    hybrid_preference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    relocation_preference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    languages: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    preferred_industries: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    excluded_roles: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    portfolio_links: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    achievements: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    preferred_roles: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    availability: Mapped[str | None] = mapped_column(String(100), nullable=True)

    facts: Mapped[list["CandidateFact"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    resumes: Mapped[list["Resume"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    preferences: Mapped[list["CandidatePreference"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    skills: Mapped[list["CandidateSkill"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")


class CandidateFact(Base):
    __tablename__ = "candidate_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="unknown")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    candidate: Mapped[Candidate] = relationship(back_populates="facts")


class CandidatePreference(Base):
    __tablename__ = "candidate_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="candidate_confirmed")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    candidate: Mapped[Candidate] = relationship(back_populates="preferences")


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True)
    filename: Mapped[str] = mapped_column(String(255))
    original_name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(512))
    file_type: Mapped[str] = mapped_column(String(20))
    is_original: Mapped[bool] = mapped_column(Boolean, default=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    candidate: Mapped[Candidate | None] = relationship(back_populates="resumes")


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    career_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class JobSource(Base):
    __tablename__ = "job_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    integration_status: Mapped[str] = mapped_column(String(20), default="UNVALIDATED")
    healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    normalized_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str] = mapped_column(String(255), index=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    remote_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    experience_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    experience_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id_external: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(100), default="manual")
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    application_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    posted_date: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verification_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    verification_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    merged_into_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="DISCOVERED")


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"))
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    status: Mapped[str] = mapped_column(String(50), default="DISCOVERED")
    mode: Mapped[str] = mapped_column(String(50), default="PREPARE_AND_APPROVE")
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_explanation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id"), nullable=True)
    cover_letter_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id"), nullable=True)
    screening_answers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    application_id_external: Mapped[str | None] = mapped_column(String(255), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    evidence_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    website_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmation_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    handoff_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    strategy_class: Mapped[str | None] = mapped_column(String(40), nullable=True)
    strategy: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(30), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(30), nullable=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    human_approval_required: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    events: Mapped[list["ApplicationEvent"]] = relationship(back_populates="application", cascade="all, delete-orphan")
    strategies: Mapped[list["ApplicationStrategy"]] = relationship(back_populates="application", cascade="all, delete-orphan")


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    to_status: Mapped[str] = mapped_column(String(50))
    event_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    application: Mapped[Application] = relationship(back_populates="events")


class Recruiter(Base):
    __tablename__ = "recruiters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    application_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EmailMessage(Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification: Mapped[str] = mapped_column(String(50), default="OTHER")
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    linked_application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    link_strength: Mapped[str | None] = mapped_column(String(20), nullable=True)
    link_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    link_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    linked_application: Mapped[Application | None] = relationship()


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    company: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(255))
    round: Mapped[str | None] = mapped_column(String(100), nullable=True)
    interview_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    meeting_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="unknown")
    status: Mapped[str] = mapped_column(String(50), default="SCHEDULED")
    interviewer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    interviewer_role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    interview_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    next_step: Mapped[str | None] = mapped_column(String(50), nullable=True)
    preparation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interview_id: Mapped[int | None] = mapped_column(ForeignKey("interviews.id", ondelete="SET NULL"), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), default="unknown")
    external_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="CONFIRMED")


class Followup(Base):
    __tablename__ = "followups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    channel: Mapped[str] = mapped_column(String(50), default="email")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="PENDING")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"), nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="NORMAL")
    channel: Mapped[str] = mapped_column(String(50), default="inbox")
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    dedup_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    task: Mapped[str] = mapped_column(String(100))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="RUNNING")


class AgentError(Base):
    __tablename__ = "errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    source: Mapped[str] = mapped_column(String(100))
    error_type: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[float] = mapped_column(Float)
    agent: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class AgentSetting(Base):
    __tablename__ = "agent_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CandidateSkill(Base):
    """Per-skill provenance with a lifecycle:
    UNKNOWN -> LEARNING -> PRACTICED -> PROJECT_EVIDENCE -> VERIFIED_SKILL.
    """

    __tablename__ = "candidate_skills"
    __table_args__ = (UniqueConstraint("candidate_id", "name", name="uq_candidate_skill"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"))
    name: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    proficiency: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="unknown")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    candidate: Mapped[Candidate] = relationship(back_populates="skills")


class Evidence(Base):
    """A traceable claim: who said what, from which source, at what confidence."""

    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claim: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(50))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ApplicationStrategy(Base):
    __tablename__ = "application_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    strategy_class: Mapped[str] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(40))
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    risk: Mapped[str] = mapped_column(String(30), default="LOW")
    effort: Mapped[str] = mapped_column(String(30), default="LOW")
    advantages: Mapped[list[str]] = mapped_column(JSON, default=list)
    weaknesses: Mapped[list[str]] = mapped_column(JSON, default=list)
    missing_requirements: Mapped[list[str]] = mapped_column(JSON, default=list)
    resume_customization: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    cover_letter_required: Mapped[bool] = mapped_column(Boolean, default=True)
    human_approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    application: Mapped[Application] = relationship(back_populates="strategies")


class OutcomeAnalysis(Base):
    __tablename__ = "outcome_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"), nullable=True)
    period: Mapped[str] = mapped_column(String(30), default="ALL_TIME")
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SkillGap(Base):
    __tablename__ = "skill_gaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    skill: Mapped[str] = mapped_column(String(100), index=True)
    priority: Mapped[str] = mapped_column(String(20), default="LOW")
    demand_count: Mapped[int] = mapped_column(Integer, default=0)
    appeared_in_rejections: Mapped[int] = mapped_column(Integer, default=0)
    appeared_in_interviews: Mapped[int] = mapped_column(Integer, default=0)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LearningGoal(Base):
    __tablename__ = "learning_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    skill: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    priority: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CareerPlan(Base):
    __tablename__ = "career_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"), nullable=True)
    period: Mapped[str] = mapped_column(String(30), default="CURRENT")
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DailyReport(Base):
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"), nullable=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)