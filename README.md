# Atlas Visibility

Measurement and delivery pipeline for **Atlas Optimisation** — AI recommendation
visibility scoring for premium hospitality. Implements the Atlas Execution Plan's
Technical Lane against the Atlas Methodology v1.0 (Release Candidate) and the
Atlas Operating System v1.0.

Governing documents live in [`docs/`](docs/) and are the source of truth. This
code implements them; it does not redefine them. If code and docs disagree,
the docs win and the code has a bug.

- [`docs/atlas-methodology-v1.0.md`](docs/atlas-methodology-v1.0.md) — scoring model, formulas, statistical protocol
- [`docs/atlas-operating-system-v1.0.md`](docs/atlas-operating-system-v1.0.md) — delivery architecture, data/privacy boundaries, unit economics
- [`docs/atlas-execution-plan-v1.0.md`](docs/atlas-execution-plan-v1.0.md) — gates, roadmap, next actions
- [`docs/atlas-external-platform-references-v1.0.md`](docs/atlas-external-platform-references-v1.0.md) — provider API references (recheck before any freeze)
- [`docs/decision-register.md`](docs/decision-register.md) — numbered decisions (D-xxx), append-only

## Status

Methodology v1.0 is a **Release Candidate**, not frozen. Nothing in this repo
may claim a frozen AVS or Atlas Readiness Score until the hospitality
calibration gate (G2) passes. See Execution Plan §2 for all hard gates.

## Structure

```
src/atlas/
  config.py           env + versioned provider pricing config
  db/                 Supabase client wrapper
  planner/            run planner — creates the expected task list before any provider call
  adapters/            one module per provider, all conforming to the Provider Adapter Standard
    base.py            ObservationRecord contract + RunState enum
    openai_adapter.py
    gemini_adapter.py
    perplexity_adapter.py
    anthropic_adapter.py
  evidence/            SHA-256 hashing + Evidence Vault manifest
    vault.py           hashing, manifest, Drive upload + evidence row write (OS §3, §7)
    drive.py           Drive transport — resumable, retried, integrity-checked, idempotent
  tools/               operator surfaces for the human-in-the-loop steps (OS §8)
    sheets.py          thin Google Sheets client (service-account auth)
    rpv_labeling.py    RPV labeling: export -> label -> validate -> import (D-034)
  costs/               cost ledger + budget rail (alert 80%, stop non-critical 100%)
  reconciliation/      expected-vs-completed check, requeue missing tasks
  scoring/             AVS (§4.3) + Atlas Readiness Score (§3.1) + movement verdict (§6.2)
    types.py           ReplicateObservation / PeriodObservations, §4.2 intent weights
    loader.py          the engine's only input — the `recommendations` table (D-034),
                       Layer A rows only (D-043)
    avs.py             PVS -> PlatformScore -> AVS, §4.4 visibility bands
    ars.py             (20*P2 + 15*P3 + 20*P4 + 15*P5) / 70, §3.1 readiness bands
    movement.py        hierarchical paired bootstrap, MRC 5.0, five-way verdict
    intervals.py       Wilson intervals, mention/top-3 rate, recommendation stability
  calibration/         §8.4 calibration gate — Layer A vs Layer B, per platform
    types.py           §8.4 thresholds, cell judgments, verdicts and pass routes
    loader.py          builds cell judgments from both layers (D-043)
    agreement.py       majority collapse (D-045), raw agreement, Cohen kappa, Spearman
    gate.py            applies the §8.4 thresholds; the fallback route needs a human
    store.py           writes `calibration_results`, emits the eligible-platform list
    run.py             one calibration cycle end to end
    scoring.py         the production AVS entry point — eligibility from the gate (D-044)

migrations/            Supabase SQL migrations, RLS enabled from migration 0001
                       0005 is written but NOT yet applied — see D-043, D-044
.github/workflows/     scheduled + manual-dispatch recovery workflows
```

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env   # fill in from Bitwarden — never commit .env
```

Secrets (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `PERPLEXITY_API_KEY`,
`GCP_SERVICE_ACCOUNT_JSON`, `GOOGLE_CLOUD_PROJECT`, Supabase keys) live in
the GitHub `production` environment, restricted to the `main` branch. Gemini
authenticates via Vertex AI service-account credentials, not an API key
(D-029) — see `docs/decision-register.md`. Local `.env` is for dev only and
is gitignored.

## Week 1 acceptance criterion

One provider call can create a planned task, a completed observation, a
payload hash, and a cost row — end to end, from the run planner through to
the evidence vault. Nothing is scored as zero on a technical failure.

## Next

`system-zero.yml` (manual dispatch) runs the full pipeline against Atlas's
own domain as an engineering test. It is never used to claim methodology
validity — see Execution Plan §3.
