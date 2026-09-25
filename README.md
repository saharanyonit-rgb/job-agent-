# Job Agent

An AI-assisted job application platform for discovering, verifying, matching, and managing job applications.

> **Status: Active development — not a fully autonomous production job-application bot.**

Job Agent is designed around a simple principle:

**Automate the repetitive parts of job hunting without pretending that unverified data or uncontrolled browser actions are reliable.**

The system separates job discovery, verification, candidate profiling, matching, application planning, evidence tracking, communication, and follow-ups into independent modules.

---

## What It Does

Job Agent is designed to handle the pipeline from:

```text
Candidate Profile
       ↓
Job Discovery
       ↓
Job Normalization
       ↓
Duplicate Detection
       ↓
Job Verification
       ↓
Candidate ↔ Job Matching
       ↓
Application Strategy
       ↓
Application Workflow
       ↓
Evidence / Audit Trail
       ↓
Follow-ups
       ↓
Outcome Tracking
```

The architecture also includes services for:

* Candidate profiles and resume parsing
* Job discovery
* Job verification
* Job normalization
* Duplicate detection
* Job matching
* Application pipelines
* Browser automation
* Email processing
* Interview preparation
* Follow-ups
* Notifications
* Scheduling
* Career planning
* Analytics
* Evidence tracking
* Audit logging
* Security and secret management
* API endpoints

---

## The Problem

Most job automation systems have a dangerous assumption:

> If a website returned a job, the job must be real and actionable.

That's not necessarily true.

Job listings can be:

* duplicated
* expired
* incomplete
* incorrectly parsed
* posted through multiple sources
* missing important requirements
* difficult to verify
* attached to application flows that change without warning

A second problem is automation risk.

Submitting applications automatically can create bad outcomes if:

* the wrong job is selected
* candidate information is incorrect
* an application question is misunderstood
* a website changes its form
* a browser automation step behaves unexpectedly
* credentials are mishandled

Job Agent therefore treats **evidence, verification, and approval** as first-class concepts rather than assuming automation is always desirable.

---

# Architecture

The project is organized as modular Python packages:

```text
job_agent/
│
├── ai/
├── analytics/
├── api/
├── applications/
├── browser/
├── calendar/
├── candidates/
├── career/
├── core/
├── database/
├── discovery/
├── documents/
├── email_service/
├── evidence/
├── followups/
├── integrations/
├── interviews/
├── jobs/
├── matching/
├── notifications/
├── orchestrator/
├── scheduler/
├── security/
│
├── __init__.py
└── __main__.py
```

The goal is to keep the system modular enough that individual components can be tested, replaced, or extended without turning the entire application into one large agent loop.

---

# Core Components

## AI

Responsible for AI-provider abstraction and AI-assisted services.

```text
ai/
├── fallback.py
├── provider.py
├── providers.py
└── service.py
```

The AI layer should remain replaceable rather than coupling the entire application to one model provider.

---

## Job Discovery

```text
discovery/
├── adapters/
│
```
