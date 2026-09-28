from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from job_agent.ai.service import chain
from job_agent.candidates.models import CandidateFactCollection
from job_agent.database import models as m

SENIORITY_KEYWORDS = ["junior", "mid", "senior", "lead", "principal", "staff", "architect", "manager", "director", "vp"]

HARD_REQUIREMENT_WORDS = ["required", "must have", "must-have", "mandatory", "essential", "requirement"]
DEGREE_KEYWORDS = ["bachelor", "master", "degree", "b.tech", "btech", "m.tech", "mtech", "msc", "bsc", "phd", "graduate", "undergraduate"]
AUTHORIZATION_KEYWORDS = [
    "work authorization", "work authorisation", "authorization", 
    "visa sponsorship", "sponsor", "must be authorized",
    "authorized to work", "work permit", "h1b", "h-1b",
    "employment authorization", "work visa", "sponsorship",
]

# Neutral-but-informed scores so unknowns are never mistaken for matches.
UNKNOWN_NEUTRAL = 70.0

FIT_STRONG = 80.0
FIT_POSSIBLE = 60.0

SEMANTIC_MATCH_THRESHOLD = 0.72


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [value]


def _as_lower(value: Any) -> str:
    return str(value or "").lower()


@dataclass
class MatchResult:
    overall_match: float
    role_match: float
    experience_match: float
    skill_match: float
    industry_match: float
    location_match: float
    seniority_match: float
    certification_match: float
    salary_match: float
    remote_match: float
    semantic_match: float = 0.0
    matched_skills: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    # --- Phase 2 structured dimensions ---
    required_skill_match: float = 100.0
    preferred_skill_match: float = 100.0
    education_match: float = 100.0
    work_authorization_match: float = 100.0
    employment_type_match: float = 75.0
    candidate_preference_match: float = 100.0
    evidence_quality: float = 100.0
    hard_requirement_failures: list[str] = field(default_factory=list)
    application_risk: str = "LOW"
    fit_classification: str = "STRONG_MATCH"
    recommendation_reason: str = ""
    breakdown: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "overall_match": self.overall_match,
            "role_match": self.role_match,
            "experience_match": self.experience_match,
            "skill_match": self.skill_match,
            "industry_match": self.industry_match,
            "location_match": self.location_match,
            "seniority_match": self.seniority_match,
            "certification_match": self.certification_match,
            "salary_match": self.salary_match,
            "remote_match": self.remote_match,
            "semantic_match": self.semantic_match,
            "matched_skills": self.matched_skills,
            "missing_requirements": self.missing_requirements,
            "reasons": self.reasons,
        }
        d.update(
            {
                "required_skill_match": self.required_skill_match,
                "preferred_skill_match": self.preferred_skill_match,
                "education_match": self.education_match,
                "work_authorization_match": self.work_authorization_match,
                "employment_type_match": self.employment_type_match,
                "candidate_preference_match": self.candidate_preference_match,
                "evidence_quality": self.evidence_quality,
                "hard_requirement_failures": self.hard_requirement_failures,
                "application_risk": self.application_risk,
                "fit_classification": self.fit_classification,
                "recommendation_reason": self.recommendation_reason,
                "breakdown": self.breakdown,
            }
        )
        return d


class MatchingEngine:
    def match(
        self,
        facts: CandidateFactCollection,
        prefs: dict[str, str],
        job: m.Job,
        *,
        verified_skills: set[str] | None = None,
        profile_unavailable: list[str] | None = None,
    ) -> MatchResult:
        target_roles: list[str] = _as_list(prefs.get("target_roles"))
        preferred_companies: list[str] = _as_list(prefs.get("preferred_companies"))
        excluded_companies: list[str] = _as_list(prefs.get("excluded_companies"))
        allowed_locations: list[str] = _as_list(prefs.get("allowed_locations"))
        excluded_roles: list[str] = _as_list(facts.value("excluded_roles"))
        preferred_roles: list[str] = _as_list(facts.value("preferred_roles")) or target_roles

        candidate_skills = {str(s).lower() for s in _as_list(facts.value("skills")) + _as_list(facts.value("technologies"))}
        if verified_skills:
            candidate_skills |= {str(s).lower() for s in verified_skills}
        job_skills = {str(s).lower() for s in (job.skills or [])}
        matched_skills = sorted(candidate_skills & job_skills)
        missing = sorted(job_skills - candidate_skills)
        skill_match = round(100 * len(matched_skills) / max(len(job_skills), 1))

        job_text = (job.description or "").lower()
        required_skills, preferred_skills = self._split_required_preferred(job_skills, job_text)
        missing_required = sorted(set(required_skills) - candidate_skills)
        required_skill_match = round(100 * len(set(required_skills) & candidate_skills) / max(len(required_skills), 1))
        preferred_skill_match = round(
            100 * len(set(preferred_skills) & candidate_skills) / max(len(preferred_skills), 1)
        )

        # --- Role match (unchanged semantics) ---
        current_title = _as_lower(facts.value("current_title"))
        role_match = 40.0
        reasons: list[str] = []
        missing_requirements: list[str] = []
        if job.normalized_title:
            jt = job.normalized_title
            if current_title and (current_title in jt or jt in current_title or any(w in jt for w in current_title.split() if len(w) > 3)):
                role_match = 95.0
                reasons.append(f"Current title '{facts.value('current_title')}' aligns with '{job.title}'")
            elif target_roles:
                for tr in target_roles:
                    tr_low = tr.lower()
                    tr_words = set(tr_low.split())
                    jt_words = set(jt.split())
                    if tr_low in jt or jt in tr_low or len(tr_words & jt_words) >= 1:
                        role_match = 90.0
                        reasons.append(f"Job matches target role '{tr}'")
                        break
                else:
                    role_match = 60.0
                    reasons.append("Job title partially overlaps target roles")
            else:
                role_match = 60.0

        # --- Experience match (unchanged) ---
        cand_exp = facts.value("years_of_experience")
        experience_match = 100.0
        if cand_exp is not None and job.experience_min is not None:
            if float(cand_exp) >= float(job.experience_min):
                experience_match = 100.0
                reasons.append(f"Candidate has {cand_exp} years vs required {job.experience_min}+")
            else:
                gap = float(job.experience_min) - float(cand_exp)
                experience_match = max(0.0, 100 - gap * 20)
                missing_requirements.append(f"Requires {job.experience_min}+ years experience")
        elif job.experience_min is not None and cand_exp is None:
            missing_requirements.append("Candidate years of experience unknown")

        # --- Location match (unchanged) ---
        location_match = 70.0
        cand_location = _as_lower(facts.value("location"))
        job_location = (job.location or "").lower()
        if allowed_locations:
            location_match = 100.0 if any(al.lower() in job_location or job_location in al.lower() for al in allowed_locations) else 30.0
            if location_match == 30.0:
                missing_requirements.append(f"Location '{job.location}' not in allowed locations")
        elif cand_location and job_location:
            if any(tok in job_location for tok in cand_location.split() if len(tok) > 3):
                location_match = 100.0
                reasons.append(f"Location overlap: {job.location}")
            elif job.remote_status == "REMOTE":
                location_match = 95.0
            else:
                location_match = 55.0
                missing_requirements.append(f"Job location '{job.location}' differs from candidate base")

        # --- Remote match (unchanged) ---
        pref_remote = _as_lower(prefs.get("remote_preference") or facts.value("remote_preference"))
        remote_match = 70.0
        if pref_remote:
            if ("remote" in pref_remote and job.remote_status == "REMOTE") or (
                "hybrid" in pref_remote and job.remote_status == "HYBRID"
            ):
                remote_match = 100.0
            elif job.remote_status == "REMOTE":
                remote_match = 90.0
            else:
                remote_match = 50.0
        else:
            remote_match = 75.0

        # --- Seniority match (unchanged) ---
        seniority_match = 80.0
        title_l = (job.title or "").lower()
        if any(k in title_l for k in ["junior", "entry"]):
            if cand_exp is not None and float(cand_exp) <= 3:
                seniority_match = 95.0
            else:
                seniority_match = 50.0
        if any(k in title_l for k in ["architect", "lead", "principal", "staff"]):
            if cand_exp is not None and float(cand_exp) >= 7:
                seniority_match = 95.0
                reasons.append("Seniority level matches experience")
            else:
                seniority_match = 60.0

        # --- Certification match (unchanged) ---
        cand_certs = " ".join(_as_lower(c) for c in _as_list(facts.value("certifications")))
        cert_match = 70.0
        if "azure" in job_text and "azure" in cand_certs:
            cert_match = 100.0
        elif "aws" in job_text and "aws" in cand_certs:
            cert_match = 100.0
        elif not any(k in job_text for k in ["certification", "certified", "azure", "aws"]):
            cert_match = 100.0

        # --- Salary match (unchanged) ---
        min_salary = float(prefs.get("minimum_salary") or 0)
        salary_match = 100.0
        if min_salary and job.salary_max is not None:
            salary_match = 100.0 if job.salary_max >= min_salary else max(0.0, 100 - (min_salary - job.salary_max) / max(min_salary, 1) * 100)
            if salary_match < 100:
                missing_requirements.append(f"Salary max {job.salary_max} below minimum {min_salary}")

        # --- Industry match (unchanged) ---
        industry_match = 75.0
        if any(word in job_text for word in ["private equity", "investment bank", "wealth", "fund"]):
            prev = " ".join(e.lower() for e in _as_list(facts.value("previous_employers")))
            if any(word in prev for word in ["private equity", "investment", "wealth", "fund", "capital"]):
                industry_match = 100.0
            else:
                industry_match = 55.0
                reasons.append("Industry background not explicitly matched")

        # --- Exclusion hard-fail (unchanged) ---
        excluded = False
        if any(ec.lower() == (job.company or "").lower() for ec in excluded_companies):
            role_match = 0.0
            missing_requirements.append(f"Company '{job.company}' is excluded by candidate")
            excluded = True

        # === Phase 2: new structured dimensions ===
        hard_failures: list[str] = []

        if excluded_roles and any(
            er.lower() in (job.normalized_title or job.title or "").lower() for er in excluded_roles
        ):
            hard_failures.append(f"Role '{job.title}' is in candidate excluded_roles")

        for skill in missing_required:
            hard_failures.append(f"Required skill '{skill}' not evidenced")

        education_match, education_note = self._education_dimension(facts, job_text)
        work_auth_match, work_auth_note, auth_unknown = self._work_authorization_dimension(facts, job_text)
        employment_type_match, employment_note = self._employment_type_dimension(facts, prefs, job)
        candidate_pref_match, pref_notes = self._candidate_preference_dimension(
            facts, prefs, job, preferred_roles, preferred_companies
        )

        evidence_quality = self._evidence_quality(facts, profile_unavailable)

        if auth_unknown:
            hard_failures.append("Work authorization status not confirmed")

        if education_note:
            missing_requirements.append(education_note)
        if work_auth_note:
            missing_requirements.append(work_auth_note)
        if employment_note:
            missing_requirements.append(employment_note)
        for n in pref_notes:
            missing_requirements.append(n)

        semantic_score, semantic_reasons = self._semantic_match(facts, job)
        # Defensive clamping: handle NaN, negative, or >1.0 from LLM
        try:
            semantic_score = float(semantic_score)
            if semantic_score != semantic_score:  # NaN check
                semantic_score = 0.0
        except (TypeError, ValueError):
            semantic_score = 0.0
        semantic_score = max(0.0, min(1.0, semantic_score))
        semantic_match = round(semantic_score * 100, 1)
        if semantic_reasons:
            reasons.extend([f"Semantic: {r}" for r in semantic_reasons[:3]])

        weights = {
            "role": 0.25,
            "experience": 0.15,
            "skill": 0.20,
            "location": 0.08,
            "seniority": 0.04,
            "salary": 0.08,
            "semantic": 0.20,
        }
        overall = (
            role_match * weights["role"]
            + experience_match * weights["experience"]
            + skill_match * weights["skill"]
            + location_match * weights["location"]
            + seniority_match * weights["seniority"]
            + salary_match * weights["salary"]
            + semantic_match * weights["semantic"]
        )
        if excluded:
            overall = 0.0
        overall = round(overall, 1)

        if matched_skills:
            reasons.append(f"Matched skills: {', '.join(matched_skills[:8])}")
        if missing:
            missing_requirements.append(f"Skills not evidenced: {', '.join(missing[:8])}")

        application_risk = self._risk(hard_failures, overall, education_match, work_auth_match, evidence_quality)
        fit = self._classify(overall, hard_failures, application_risk)
        recommendation_reason = self._recommendation(fit, hard_failures, matched_skills, missing, overall)

        breakdown = {
            "required_skills": {"matched": sorted(set(required_skills) & candidate_skills), "missing": missing_required, "match": required_skill_match},
            "preferred_skills": {"matched": sorted(set(preferred_skills) & candidate_skills), "missing": sorted(set(preferred_skills) - candidate_skills), "match": preferred_skill_match},
            "experience": {"required_min": job.experience_min, "candidate": cand_exp, "match": round(experience_match, 1)},
            "education": {"match": education_match, "note": education_note},
            "location": {"job": job.location, "candidate": facts.value("location"), "match": round(location_match, 1)},
            "salary": {"max": job.salary_max, "candidate_min": min_salary or None, "match": round(salary_match, 1)},
            "work_authorization": {"judged": "authorization required" if auth_unknown or work_auth_match == 100 and _requires_authorization(job_text) else "not required", "match": work_auth_match},
            "seniority": {"match": round(seniority_match, 1)},
            "employment_type": {"job": job.employment_type, "match": employment_type_match},
            "candidate_preference": {"match": round(candidate_pref_match, 1)},
            "hard_failures": hard_failures,
            "risks": self._risk_reasons(application_risk, hard_failures, evidence_quality),
        }

        result = MatchResult(
            overall_match=overall,
            role_match=role_match,
            experience_match=round(experience_match, 1),
            skill_match=round(skill_match, 1),
            industry_match=round(industry_match, 1),
            location_match=round(location_match, 1),
            seniority_match=round(seniority_match, 1),
            certification_match=round(cert_match, 1),
            salary_match=round(salary_match, 1),
            remote_match=round(remote_match, 1),
            semantic_match=semantic_match,
            matched_skills=matched_skills,
            missing_requirements=missing_requirements,
            reasons=reasons,
            required_skill_match=required_skill_match,
            preferred_skill_match=preferred_skill_match,
            education_match=education_match,
            work_authorization_match=work_auth_match,
            employment_type_match=round(employment_type_match, 1),
            candidate_preference_match=round(candidate_pref_match, 1),
            evidence_quality=evidence_quality,
            hard_requirement_failures=hard_failures,
            application_risk=application_risk,
            fit_classification=fit,
            recommendation_reason=recommendation_reason,
            breakdown=breakdown,
        )
        return result

    # --- helpers ---

    def _split_required_preferred(self, job_skills: set[str], job_text: str) -> tuple[set[str], set[str]]:
        if not job_text:
            return job_skills, set()
        sentences = re.split(r"[.!\n;]", job_text)
        required: set[str] = set()
        job_text_lower = job_text.lower()
        for skill in job_skills:
            skill_lower = skill.lower()
            if any(skill_lower in sent.lower() and any(w in sent.lower() for w in HARD_REQUIREMENT_WORDS) for sent in sentences):
                required.add(skill)
        preferred = job_skills - required
        return required, preferred

    def _education_dimension(self, facts: CandidateFactCollection, job_text: str) -> tuple[float, str | None]:
        mentions = [k for k in DEGREE_KEYWORDS if k in job_text]
        if not mentions:
            return 100.0, None
        education = _as_list(facts.value("education"))
        education_text = " ".join(e.lower() for e in education)
        requires_degree = any("requirement" in s or "required" in s or "must" in s or "degree" in s for s in [job_text])
        if not education:
            note = f"Job mentions {mentions[0]} but candidate education is unknown"
            return 55.0 if requires_degree else 70.0, note
        if any(k in education_text for k in mentions):
            return 100.0, None
        note = f"Job mentions {mentions[0]}; candidate education not obviously aligned"
        return 70.0, note

    def _work_authorization_dimension(self, facts: CandidateFactCollection, job_text: str) -> tuple[float, str | None, bool]:
        requires = _requires_authorization(job_text)
        auth = facts.value("work_authorization")
        if not requires:
            return 100.0, None, False
        if auth:
            return 100.0, None, False
        return 30.0, "Work authorization status unknown; company may require authorization", True

    def _employment_type_dimension(self, facts: CandidateFactCollection, prefs: dict[str, str], job: m.Job) -> tuple[float, str | None]:
        job_type = (job.employment_type or "").upper()
        if not job_type:
            return 75.0, None
        cand_type = str(facts.value("employment_type") or prefs.get("employment_type") or "").upper()
        if not cand_type:
            return 75.0, None
        if cand_type == job_type:
            return 100.0, None
        if job_type == "CONTRACT" and cand_type == "FULL_TIME":
            return 55.0, f"Job is {job_type} but candidate prefers {cand_type}"
        return 70.0, f"Employment type {job_type} differs from candidate preference {cand_type}"

    def _candidate_preference_dimension(
        self,
        facts: CandidateFactCollection,
        prefs: dict[str, str],
        job: m.Job,
        preferred_roles: list[str],
        preferred_companies: list[str],
    ) -> tuple[float, list[str]]:
        notes: list[str] = []
        score = 100.0
        jt = (job.normalized_title or job.title or "").lower()
        if preferred_roles:
            match = any(pr.lower() in jt or jt in pr.lower() for pr in preferred_roles)
            if not match:
                score = min(score, 55.0)
                notes.append(f"Job not in candidate preferred roles: {preferred_roles}")
        if preferred_companies:
            match = any(pc.lower() == (job.company or "").lower() for pc in preferred_companies)
            if not match:
                score = min(score, 80.0)
        industries = _as_list(facts.value("preferred_industries"))
        if industries and job.description:
            matched_ind = [i.lower() for i in industries if i.lower() in job_text_lower(job.description)]
            if not matched_ind:
                score = min(score, 75.0)
                notes.append("Job description does not mention candidate preferred industries")
        return score, notes

    def _evidence_quality(self, facts: CandidateFactCollection, profile_unavailable: list[str] | None) -> float:
        profile_unavailable = profile_unavailable or []
        per = 100.0
        if "years_of_experience" in profile_unavailable:
            per -= 10
        if "location" in profile_unavailable:
            per -= 10
        if "work_authorization" in profile_unavailable:
            per -= 10
        if "skills" in profile_unavailable:
            per -= 20
        if "current_title" in profile_unavailable:
            per -= 10
        total = 0
        count = 0
        for key in ("skills", "technologies", "education", "certifications"):
            val = facts.value(key)
            if val is not None:
                total += 100 if facts.get(key).verified else 70
                count += 1
        if count:
            per = 0.5 * per + 0.5 * (total / count)
        return max(0.0, round(min(per, 100.0), 1))

    def _risk(
        self,
        hard_failures: list[str],
        overall: float,
        education_match: float,
        work_auth_match: float,
        evidence_quality: float,
    ) -> str:
        if hard_failures:
            return "HIGH"
        if evidence_quality < 60 or work_auth_match < 60 or overall < FIT_POSSIBLE:
            return "MEDIUM"
        return "LOW"

    def _classify(self, overall: float, hard_failures: list[str], risk: str) -> str:
        if hard_failures:
            return "HARD_REQUIREMENT_FAILURE"
        if risk == "HIGH":
            return "HIGH_RISK"
        if overall >= FIT_STRONG:
            return "STRONG_MATCH"
        if overall >= FIT_POSSIBLE:
            return "POSSIBLE_MATCH"
        return "WEAK_MATCH"

    def _recommendation(self, fit: str, hard_failures: list[str], matched: list[str], missing: list[str], overall: float) -> str:
        if fit == "HARD_REQUIREMENT_FAILURE":
            return f"Not recommended: {', '.join(hard_failures[:3])}"
        if fit == "HIGH_RISK":
            return "High risk application: key facts unconfirmed; candidate should review before applying."
        if fit == "STRONG_MATCH":
            return f"Strong fit ({overall}). Matches personal strengths; apply."
        if fit == "POSSIBLE_MATCH":
            missing_txt = ", ".join(missing[:3]) or "minor gaps"
            return f"Possible fit ({overall}). Gaps: {missing_txt}."
        return f"Weak fit ({overall}); not recommended."

    def _risk_reasons(self, risk: str, hard_failures: list[str], evidence_quality: float) -> list[str]:
        reasons: list[str] = []
        if hard_failures:
            reasons.extend(hard_failures[:3])
        if evidence_quality < 60:
            reasons.append("Evidence quality is low; several candidate facts are unconfirmed")
        if risk == "LOW":
            reasons.append("No material risks detected")
        return reasons

    def _semantic_match(self, facts: CandidateFactCollection, job: m.Job) -> tuple[float, list[str]]:
        """Use LLM to compute semantic similarity between candidate profile and job."""
        try:
            candidate_summary = (
                f"Title: {facts.value('current_title') or 'N/A'}\n"
                f"Experience: {facts.value('years_of_experience') or 'N/A'} years\n"
                f"Skills: {', '.join(_as_list(facts.value('skills')) + _as_list(facts.value('technologies'))) or 'N/A'}\n"
                f"Certifications: {', '.join(_as_list(facts.value('certifications'))) or 'N/A'}\n"
                f"Summary: {facts.value('summary') or 'N/A'}"
            )
            job_summary = (
                f"Title: {job.title}\n"
                f"Company: {job.company}\n"
                f"Location: {job.location or 'N/A'}\n"
                f"Remote: {job.remote_status or 'N/A'}\n"
                f"Description: {(job.description or '')[:2000]}"
            )
            prompt = (
                "Rate the semantic match between this candidate and job on a scale of 0.0 to 1.0.\n"
                "Consider: role alignment, skill transferability, seniority fit, domain relevance.\n"
                "Return only a JSON object: {\"score\": 0.85, \"reasons\": [\"reason1\", \"reason2\"]}\n\n"
                f"CANDIDATE:\n{candidate_summary}\n\nJOB:\n{job_summary}"
            )
            result = chain.structured_output(prompt)
            score = float(result.get("score", 0.0))
            reasons = result.get("reasons", [])
            return max(0.0, min(1.0, score)), reasons
        except Exception:
            return 0.0, []


def _requires_authorization(job_text: str) -> bool:
    text_lower = job_text.lower()
    return any(k in text_lower for k in AUTHORIZATION_KEYWORDS)


def job_text_lower(description: str | None) -> str:
    return description.lower() if description else ""