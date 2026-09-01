# CLAUDE.md — Atlas Optimisation

## Read this first, every session

**Before any other action — reading, searching, editing, or running
anything — read `docs/CODE-AUTONOMY-BOUNDARIES.md` in full.**

It defines which actions proceed unattended and which stop for explicit
confirmation. It exists because a `git add -A` swept 421 lines of
unrelated, unreviewed code into an unrelated commit on `main`. Do not
rely on being told to read it; that dependency is the gap it was written
to close.

Its rules are not reproduced here. Read the file.

## Orientation

Atlas Optimisation measures how often and how prominently a hospitality
business is recommended by AI assistants under controlled, repeatable
conditions, then improves the digital signals the business can control and
remeasures observed recommendation behaviour — across multiple providers
(OpenAI, Anthropic, Gemini/Vertex, Perplexity). Atlas reports two official,
intentionally separate scores: AI Visibility Score (AVS) — are AI
assistants recommending this venue, outcome — and Atlas Readiness Score
(ARS) — how strong are the controllable signals supporting future AI
visibility, controllable readiness. Observations run through a
planner/runner pipeline into Supabase Postgres. The governing documents
are `docs/atlas-methodology-v1.0.md`, `docs/atlas-operating-system-v1.0.md`
and `docs/atlas-execution-plan-v1.0.md`.

- **Decision register: `docs/decision-register.md`.** Append-only. A
  decision is added, never edited after acceptance — a reversal is a new
  decision referencing the one it retires. Methodology §10 requires
  decisions be documented *before* implementation. Check it before
  proposing anything that looks like a settled question; many are.
- **Current plan: `docs/atlas-action-plan-v2.0-2026-09-01.md`.** The
  locked sequence to v1.0-MVP freeze, with dated phases.

## This file is governance

`CLAUDE.md` and `docs/CODE-AUTONOMY-BOUNDARIES.md` are both process and
governance documents, not code. Editing either is a bucket-two action
under the boundaries doc: propose the change and wait for confirmation,
don't self-edit.
