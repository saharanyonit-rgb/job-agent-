# 👋 Hi, I'm Yonit Saharan — and this is my AI Job Agent

<div align="center">

🏗️ **Civil Engineer (6 yrs)** · 🤖 **AI-Automation Builder** · 📍 Las Vegas, NV

`Python` `FastAPI` `SQLite` `Playwright` `LLM Pipelines` `GitHub Actions`

</div>

---

## 🤖 About This Repo — Meet **Job Agent**

I built an **autonomous job-hunting agent** that runs a full pipeline so humans don't have to scroll listings all day:

```
Resume → Discovery → Normalization → Dedupe → Live Verification (HTTP 200)
      → Match Scoring vs. Resume → Policy Gates → Application Prep → Submit → Follow-up
```

### What makes it different
- 🔍 **Real jobs only** — fake/mock sources are disabled by default; every listing is URL-verified live before you ever see it
- 🌐 **Multi-source adapters** — Greenhouse, Lever, We Work Remotely, Remotive (+ LinkedIn/Indeed with cookies)
- 🧠 **Resume-aware matching** — parses my CV and scores every job 0–100 against actual experience (civil eng, stormwater, roadway, PM)
- 🎛️ **Three safety modes** — `MOCK` (demo) · `COPILOT` (human approves each apply) · `AUTONOMOUS` (auto-submit within budget/cooldown rules)
- 🗂️ **Full audit trail** — SQLite state machine tracks every job & application from discovery to outcome
- ⏰ **Self-hosted on GitHub Actions** — discovers & matches new jobs daily via cron, files match alerts as issues

### Three-mode design
| Mode | Who clicks "Apply" | Use case |
|------|-------------------|----------|
| `MOCK` | Nobody (sandbox ATS) | Testing the pipeline |
| `COPILOT` | **Me**, after review | Default — safe |
| `AUTONOMOUS` | The agent | Fully hands-off within policy limits |

---

## 🛠️ Toolbox

<img src="https://skillicons.dev/icons?i=python,fastapi,sqlite,playwright,git,githubactions,vscode" alt="skills" />

Also: async pipelines, adapter/plugin architecture, prompt engineering for resume→job matching, HTTP verification & anti-scrape handling.

---

## 📊 By the numbers (from real runs)

- **2,800+** live jobs discovered in a single run across multiple sources
- **~1,000** scored matches against my resume
- **95** professional civil-engineering roles auto-shortlisted from one board alone
- **0** fake/stale listings — expired & closed postings get purged automatically

---

## 🚀 Quick Start

```bash
pip install -r requirements.txt
cp resume/my_resume.pdf resume/          # agent auto-imports it
APP_MODE=COPILOT python -m core run       # discover + match real jobs
python -m core report                     # see your top matches & links
```

---

## 🌱 Currently building

- [ ] Construction-industry ATS coverage (AECOM/Jacobs/Kiewit need browser automation)
- [ ] Auto-drafted cover letters per posting
- [ ] Interview-prep briefs generated from each job description
- [ ] Email follow-up automation with reply detection

💬 Ask me about **how the verification layer filters out dead postings** — that's the part most "AI job bots" skip.
