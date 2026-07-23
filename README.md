# WhatsApp Dementia Assistant — Implementation Blueprint

**Scope:** Hackathon demo. Bilingual (English / Simplified Chinese) WhatsApp assistant for a
dementia patient, backed by Gmail + Google Calendar, hosted on Railway.

**Status:** Design complete, no code written. This directory is the input to Claude Code.

---

## How to use this with Claude Code

1. Copy `CLAUDE.md` to your repo root. Claude Code reads it automatically every session.
2. Copy `docs/` to `docs/` in your repo.
3. Work milestone by milestone from `docs/13-roadmap.md`. Do **not** ask Claude Code to
   "build the app" — give it one milestone at a time and let it read the relevant doc.
4. `CLAUDE.md` contains the non-negotiable invariants. If a generated design conflicts with
   `CLAUDE.md`, `CLAUDE.md` wins.

Suggested first prompt to Claude Code:

> Read `CLAUDE.md` and `docs/13-roadmap.md`. Implement Milestone 0 only. Stop and show me
> the repo tree and the health check before writing any feature code.

---

## The three constraints that shape everything

Read these before anything else. They are not preferences; they are hard limits of the
platforms chosen, and most of the architecture exists to work around them.

| # | Constraint | Consequence |
|---|---|---|
| 1 | Twilio's WhatsApp **sandbox cannot use custom templates** — only three fixed pre-approved ones — and outside the 24-hour window a template is the *only* thing you may send | Proactive medication reminders are not free-form. Everything routes through a window-aware outbound gateway. See `docs/03-whatsapp-twilio.md`. |
| 2 | The sandbox **join expires 3 days after joining** | If your judge joins on Monday and demos on Friday, the bot is silently dead. Pre-demo checklist in `docs/13-roadmap.md`. |
| 3 | WhatsApp gives you a **static location pin on request**, not a live GPS stream | Continuous geofencing is not buildable. Replaced with a pull-based location check. See `docs/07-features.md`. |

---

## Document map

Every numbered section from the original brief is covered. Mapping:

| Brief § | Topic | Document |
|---|---|---|
| 1, 2 | Executive summary, architecture, request lifecycle, data flow | `docs/01-summary-and-architecture.md` |
| 3 | Technology stack, alternatives, limitations | `docs/02-tech-stack.md` |
| 5 | WhatsApp / Twilio flow, all six input types | `docs/03-whatsapp-twilio.md` |
| 6, 7, 8 | Google OAuth, Gmail, Calendar | `docs/04-google-integration.md` |
| 9 | AI processing pipelines (text, voice, image, PDF, email, calendar) | `docs/05-ai-pipelines.md` |
| 10, 11, 12, 13, 15 | Speech-to-text, TTS, OCR, vision, multilingual | `docs/06-speech-vision-multilingual.md` |
| 4 | Feature-by-feature implementation, dementia features | `docs/07-features.md` |
| 17 | PostgreSQL schema, ER diagram, indexes, constraints | `docs/08-database.md` |
| 19, 20 | API endpoints, background jobs | `docs/09-api-and-jobs.md` |
| 21, 22, 23 | Security, logging, error handling | `docs/10-security-observability.md` |
| 24 | Testing strategy | `docs/11-testing.md` |
| 16, 18 | Railway deployment, folder structure | `docs/12-deployment-and-structure.md` |
| 25 | Development roadmap and milestones | `docs/13-roadmap.md` |
| 26, 27, 28, 29 | Cost analysis, scalability, risks, future work | `docs/14-cost-scale-risks.md` |
| 30 | All Mermaid diagrams | `docs/15-diagrams.md` |

---

## Scope discipline

You chose **hackathon demo only**. That decision has been applied throughout: sections that
would matter for a production system are marked `DEFERRED` with a one-line note on what
you'd do instead, rather than padded out. Building the deferred items will cost you the demo.

What is genuinely in scope for demo day is listed in `docs/13-roadmap.md` under
**Demo Day Cut Line**. Everything below that line is optional.

---

## Honest caveats

- **Free-tier figures move.** Gemini's free quotas were cut in December 2025 and again in
  April 2026; Railway has no permanent free tier. Numbers in `docs/14-cost-scale-risks.md`
  were checked in July 2026 — re-verify against the live pricing pages before you rely on them.
- **`edge-tts` is unofficial.** It reverse-engineers a Microsoft Edge endpoint. It is free and
  the Mandarin voices are the best you can get for $0, but it can break without notice.
  A fallback path is specified in `docs/06-speech-vision-multilingual.md`.
- **Use synthetic patient data only.** Gemini's free tier may use your prompts for training.
  Do not put a real person's medical information through this demo.
