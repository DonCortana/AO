# Calibration run driver — design

**Status: IMPLEMENTED, NOT RUN (2026-08-28).** §§1-3 are built as
`src/atlas/calibration/driver.py` with the CLI in
`scripts/plan_calibration_run.py`, covered by `tests/test_calibration_driver.py`
against a test double. **Nothing here has been run against live data, and no
`prompt_versions` or `run_plans` row has been written.** §4 has been corrected
against the real `ATLAS_BACKLOG.md`; see §4.1 for two sequencing problems that
need a decision. Registered as D-056; awaiting Doud's review.

Scope: the minimal thing that stands between a finalized Frozen Core prompt
set and a §8.4 calibration gate result for **one** calibration property. It is
deliberately not a general run orchestrator — see "Where this ends and P0-06
begins" at the foot of this document.

---

## 1. What the planner actually is

Read from `src/atlas/planner/run_planner.py`, not from the Execution Plan.
Three things differ from what the plan's prose implies, and all three shape
the driver:

**The callable is `plan_run`, not `run_planner`.** `run_planner` is the
module. The Execution Plan step 3 language ("implement run planner first")
names a component, not a function, and every caller must import
`from atlas.planner.run_planner import plan_run`.

```python
def plan_run(
    run_plan_id: str,
    prompt_version_ids: list[str],
    providers: list[str],
    replicate_count: int,
) -> list[PlannedTask]
```

All four parameters are positional-or-keyword and all four are required.
There is **no `db` parameter** — `plan_run` calls `atlas.db.client.get_db()`
itself. This is a real asymmetry with the rest of the pipeline:
`reconcile_run` and `resume_run` both take an injectable `db`, and their tests
pass a fake. `plan_run` cannot be exercised against a double without patching
the module, which is worth knowing before the driver's tests are written.

**`plan_run` does not write a `run_plans` row.** It receives `run_plan_id` as
a string that must already exist — nothing in the planner inserts into
`run_plans`, and the foreign key on `observations.run_plan_id` means a
fabricated id fails at insert. The only code in the repository that creates a
`run_plans` row today is `scripts/smoke_test.py`, inline:

```python
db.table("run_plans").insert({
    "property_id": ...,
    "run_type": "system_zero",
    "replicate_count": 1,
    "status": "planned",
}).execute().data[0]["id"]
```

So "what `run_planner` expects to receive to write a `run_plans` row" has a
blunt answer: it expects the row to already be there, and creating it is the
caller's job. That gap is most of the driver's reason to exist.

**What it writes.** One `observations` row per (prompt_version × provider ×
replicate), upserted `on_conflict="task_id"`, carrying `task_id`,
`run_plan_id`, `prompt_version_id`, `provider`, `replicate_index`,
`status='planned'` and `model=''`. `model`, `model_snapshot` and
`tool_version` are filled by the adapter at call time, not here. It returns
the `PlannedTask` list it wrote.

**Idempotency is scoped to the run plan.** `deterministic_task_id` is
`sha256(f"{run_plan_id}:{prompt_version_id}:{provider}:{replicate_index}")[:32]`.
Re-running `plan_run` with the *same* `run_plan_id` is a no-op upsert;
re-running it with a *new* `run_plan_id` silently plans a second full set of
200 observations. The idempotency guarantee in Operating System §4 therefore
holds only if the driver reuses the run plan rather than creating a fresh one
on every invocation — which is a constraint on the driver, not something the
planner can enforce.

### 1.1 The planner is Layer A only

`plan_run` sets no `surface_layer`, so every row it writes takes migration
0005's default of `'api'`. Two consequences, both load-bearing for a
calibration driver, which by definition needs both layers:

- Passing `providers=['google_ai']` would insert an `api` row and violate
  `observations_google_ai_is_consumer_only` (D-042, migration 0005). The call
  fails; it does not silently mislabel. Correct behaviour, but it means the
  planner cannot express Google AI at all.
- Layer B captures for the other four platforms are equally unplannable
  through this path — there is no parameter that would produce
  `surface_layer='consumer'`.

This is not a defect to fix in the driver. Layer B is human-initiated
consumer-surface capture (§8.3); it is not an API task list and does not
belong in a planner whose contract is "expected provider calls, written before
any provider is called". The driver plans Layer A and stops there.

---

## 2. Inputs

| Input | Type | Source | Notes |
|---|---|---|---|
| `property_id` | uuid | `properties.id` | Samujana, `is_calibration_property = true`. The driver reads the flag and refuses a property without it. |
| `prompt_version_ids` | list[uuid] | `prompt_versions.id` | The finalized Frozen Core rows. Passed in, never selected by the driver — see below. |
| `market_id` | uuid | `markets.id` | Not consumed by `plan_run`; carried for the gate handoff and asserted consistent with every prompt row's `market_id`. |
| `layer` | `'api'` | — | Accepted explicitly and rejected unless `'api'`, so the Layer A limit above is stated at the call site rather than discovered at the constraint. |
| `replicate_count` | int, default 5 | Methodology §6.1 | "Frozen Core baseline / validation — n=5 per prompt-platform-market". The default is the methodology's; an override is recorded, not silent. |
| `providers` | list[str], default the four API adapters | — | `openai`, `gemini`, `perplexity`, `anthropic`. `google_ai` is rejected with the D-042 reason, not a constraint error. |

**Prompt rows are passed in as ids, never discovered by query.** A driver that
selected `set_type='frozen_core' and version=...` would quietly pick up a row
someone added after the set was agreed, and §7 makes Frozen Core membership
immutable between baseline and validation. The set is named by the operator
who froze it; the driver's job is to refuse anything that does not match, not
to assemble the set itself.

**Preflight checks, all fail-closed and none silent:**

1. Every `prompt_version_id` exists, has `set_type='frozen_core'`, shares one
   `version` string, and shares one `market_id`.
2. Count is 8–12 (§7 Frozen Core set size).
3. `is_holdout` is false on all of them — hold-out is a §6.3 Benchmark
   concept and has no meaning in a Frozen Core instrument.
4. The property is flagged `is_calibration_property` and is not
   `is_system_zero`.
5. §8.4 property-selection criteria are satisfied. **This check is what D-053
   and migration 0008 exist for** — before 0008, `review_presence` and
   `third_party_reference` had no column and only two of the four criteria
   were checkable. The driver reads all four; a null verification column reads
   as unchecked and blocks, naming the criterion.

At n=5 with 10 prompts and 4 providers this plans **200 observations**. The
driver prints that number and requires confirmation before writing, because
200 planned rows is also 200 provider calls once the resume runner picks them
up.

---

## 3. What it calls, and what it writes

```
  preflight (§2)
    -> insert run_plans row        [driver writes]
    -> plan_run(...)               [planner writes observations]
    -> return run_plan_id
```

**Step 1 — the `run_plans` row.** The driver's own write, since the planner
does not do it:

- `property_id` — as given.
- `run_type='frozen_core'` — from the migration 0002 vocabulary. Not
  `system_zero`: that value is reserved for Atlas's own engineering runs and
  a calibration property is a real hospitality property.
- `replicate_count` — the same 5 handed to `plan_run`, so the plan row and
  the task list cannot disagree.
- `status='planned'`.
- `window_start` / `window_end` — set, not left null. §6.1 requires a
  **minimum six-hour run window** for Frozen Core replicates, and the window
  is unrecoverable after the fact if it is not written when the plan is made.

**Step 2 — `plan_run`,** with the run plan id from step 1, the given prompt
version ids, the four API providers and `replicate_count=5`.

**Step 3 — return the `run_plan_id`.** That is the entire output, and it is
the value `atlas.calibration.run.run_gate` needs as `api_run_plan_ids=[...]`.
The Layer B ids that pair with it come from human capture on a separate path
the driver does not touch.

**Re-invocation.** Given a property and a prompt-set version that already have
a `frozen_core` run plan, the driver reuses that plan's id rather than
inserting a second one, and lets `plan_run`'s upsert absorb the repeat. A new
run plan is created only when explicitly asked for — a second baseline is a
deliberate act, not the accidental result of running a command twice.

### What it does not write

- No `prompt_versions` rows. The set is finalized before the driver is
  invoked; freezing prompts is an editorial act with a §7 immutability
  consequence and does not belong behind a run command.
- No `observations` rows directly — only through `plan_run`.
- No `evidence`, `costs`, `recommendations` or `calibration_results` rows.
- Nothing on the four D-053 columns. It **reads** them in preflight; the
  writer for those is a separate decision that has not been made.

### What it does not execute

The driver plans and stops. Execution is `atlas.runners.resume.resume_run`,
already built. One behaviour worth knowing rather than rediscovering:
`plan_run` leaves rows at `status='planned'`, and `resume_run` only claims
`queued`/`retryable` — but it calls `reconcile_run` first, and that requeues
`planned` to `queued`. So the handoff works with no extra step. It is worth
recording, though, that `reconcile_run` also sets `run_plans.status =
'reconciled'` on that same first pass, before any provider has been called.
That is existing behaviour on a shared path; the driver neither depends on it
nor corrects it, and it is noted here so a later reader does not read a
`reconciled` run plan as a finished one.

---

## 4. Where this ends and ATLAS_BACKLOG.md P0-06 begins

**Verified against the real backlog, 2026-08-28.** `ATLAS_BACKLOG.md` is not
in this repository; it lives in Google Drive (`ATLAS_BACKLOG.md`, updated
25 August 2026). Its P0-06 row reads, in full:

| ID | Item | Week | Notes |
|---|---|---|---|
| P0-06 | Run orchestration: prompt sets, replicates, retry, status taxonomy | 3 | |

That is the entire item: four named concerns and a week number, no notes. The
boundary table below was originally written against a one-line gloss
("general production run orchestration") and **overstated P0-06's scope** —
it attributed to P0-06 four things the backlog actually assigns to separate,
later rows. Corrected below, with those four moved to their real owners.

This driver is a **one-property, one-set, one-layer, one-shot** planner. It
turns an already-agreed prompt set into one planned Layer A run plan for one
calibration property, and knows nothing about when to run, how often, at what
cost, or for whom.

| | This driver | P0-06, as actually written |
|---|---|---|
| Prompt sets | Ids handed in, verified, never selected | "prompt sets" — set lifecycle and selection |
| Replicates | Fixed n=5 from §6.1, override recorded | "replicates" — replicate policy across run types |
| Retry / resume | Delegated to `resume_run` unchanged | "retry" — owns the retry policy |
| Status taxonomy | Writes `status='planned'` and stops | "status taxonomy" — defines the taxonomy itself |
| Properties | One, explicitly a calibration property | Not stated in P0-06 |
| Run types | `frozen_core` only | Not stated in P0-06; run types come from Methodology §6.1 |
| Layers | Layer A only | Not stated in P0-06 — see the Layer B row below |

**Four things this table previously assigned to P0-06 that are not P0-06's.**
Each is a distinct backlog row, and three of the four are P1/P2 items dated
well after the calibration study that needs them:

| Previously attributed to P0-06 | Actual owner | When |
|---|---|---|
| GitHub Actions cadence, six-hour window enforced across runs, delayed/dropped-run handling | **P1-02** GitHub Actions scheduling for daily/weekly/monthly cadence | Week 11 |
| Budget ceilings, per-cycle cost projection, refusal above a threshold | **P1-03** Budget rails: 80% alert, 100% halt on non-critical | Week 11 |
| Hold-out sampling within prompt-set lifecycle | **P1-10** Hold-out prompt designation and drift reporting | Week 12 |
| Layer B capture workflow, its own scheduling and provenance | **P2-06** Analyst/VA onboarding: review ingestion + Layer B capture SOP | ~client 15 |

Cost ceilings and the six-hour window remain out of scope for this driver for
the reasons given below — that judgment is unchanged. What changes is who they
belong to: P1-03 and P1-02, not P0-06.

1. **Cost ceilings.** The driver prints the planned observation count and
   requires `--commit`. It does not price the run or refuse an expensive one.
   A confirmation flag is an operator check, not a budget control, and
   building a partial one here would make P1-03's real ceiling look
   already-handled.
2. **Scheduling and the six-hour window.** The driver writes `window_start`
   and `window_end` on the run plan because the value is unrecoverable
   otherwise. It does not enforce the window, spread calls across it, or know
   what to do when a run overruns it. Recording the window and honouring it
   are different jobs; only the first is in scope, and the second is P1-02's.

### 4.1 Two sequencing problems the real backlog exposes

Both are for Doud to weigh, not for this driver to solve.

**P0-06 is week 3; the calibration study (P0-09) is week 5.** The premise
stated at the head of this document — that the driver exists "so the §8.4 gate
can be reached for Samujana without waiting for general orchestration" — is
weaker than it looked when the backlog could not be read. In the backlog's own
sequencing, orchestration lands two weeks *before* the study that needs it, so
this driver is not routing around a distant dependency; it duplicates a nearer
one and may be superseded within weeks of being written. It is still the
faster path to a gate result today, but "expected to be superseded rather than
grown" should be read as a matter of weeks, not quarters.

**The only Layer B capture item is scheduled after the study that requires
it.** P0-09 ("**Calibration study** Layer A vs Layer B", week 5) is annotated
"Gates every client-facing AVS claim", and the Client #1 readiness checklist
lists "Calibration study complete, agreement rates published" as an unchecked
gate. But the sole backlog row covering Layer B capture is P2-06, triggered at
~client 15 — after Client #1, and ten weeks after the study that cannot
complete without it. This is the D-056 gap stated concretely: Layer B capture
is not merely unowned, it is scheduled behind its own dependent. `run_gate`
requires `consumer_run_plan_ids` alongside `api_run_plan_ids`, so no ordering
of the current backlog reaches a gate result.

One backlog line does corroborate §1.1 rather than contradict it. The
build/buy table records **Screenshots — "Manual, human-initiated,
permanently"**, which confirms that Layer B is not a planner path being
deferred but a human process by design. The missing piece is the `run_plans`
row those manual captures attach to, not an automated capture path.

## 5. Open questions for review

1. ~~**P0-06's actual text** (§4)~~ — **resolved 2026-08-28.** Read from the
   real `ATLAS_BACKLOG.md` in Drive and §4 corrected against it. P0-06 is
   narrower than assumed; four items were reattributed to P1-02, P1-03, P1-10
   and P2-06, and two sequencing problems surfaced (§4.1) that need Doud's
   decision.
2. **Layer B pairing** (D-056). The gate needs `consumer_run_plan_ids`
   alongside the api ids. Consumer captures are not plannable through
   `plan_run` (§1.1) and no path exists yet for creating the run plan they
   attach to. Out of scope here by design, but the gate cannot run until
   someone owns it — and per §4.1 the only backlog row that covers it (P2-06,
   ~client 15) falls *after* the study that depends on it.
3. **`plan_run`'s missing `db` parameter** (§1). Adding one would match
   `reconcile_run` and `resume_run` and make the driver testable without
   patching. That is a change to a shared component and is not proposed as
   part of this driver.
4. ~~**Confirmation prompt vs. `--commit` flag.**~~ — **resolved.** Built with
   the `--commit` idiom from `scripts/seed_calibration_property.py`; dry run is
   the default and writes nothing.
