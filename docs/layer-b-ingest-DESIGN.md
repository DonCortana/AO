# Layer B consumer-surface ingest — design

Status: draft for review. No code written.
Companion to `docs/calibration-run-driver-DESIGN.md` (Layer A).

## 1. What this is, and what it is not

The Layer A driver plans and *executes* API calls. Layer B has nothing to
execute: §8.3 makes consumer-surface checks human-initiated unless a provider
offers an approved automation path, and none currently does for the four API
platforms. So this is not a runner. It is an **ingest and validation path**
that accepts what a human captured in a browser and writes `observations` rows
that `load_cells` will admit.

D-056 flagged "no run_plans creation path exists for consumer-surface
capture". That flag is correct but was carried forward too broadly in session
notes: `run_gate` already accepts `consumer_run_plan_ids` and
`atlas.calibration.loader.load_cells` already reads consumer rows. Nothing in
the calibration package needs changing. The gap is entirely upstream of it.

**No migration is required.** This was the main open question and the schema
answers it: see §3.

## 2. Verified constraints

Everything in this section was read from source or live DDL, not inferred.

### From §8.3 (methodology v1.0, line 559)

- Consumer surfaces are human-initiated. Automation only where a provider
  "explicitly offers an approved automation path for that surface".
- One-time hospitality calibration cadence: full Frozen Core on relevant
  consumer surfaces, "repeated enough to estimate agreement; **target n=3 when
  operationally feasible**".

n=3 is therefore a target, not a floor. The ingest path must accept n=1 and
n=2 cells, record the actual count, and warn rather than reject. `CellJudgment`
already carries `replicate_count`, so the shortfall is representable and
visible downstream without any change.

### From `atlas/calibration/loader.py`

A consumer observation is admitted to the frame only if all of:

- `run_plan_id` in the passed `consumer_run_plan_ids`
- `surface_layer = 'consumer'` exactly
- `status = 'complete'`
- `grounding_status != 'ungrounded_ineligible'` (NULL passes)
- exactly one `recommendations` row with `is_client_entity = true`
- that row has `entity_conflict` falsey and `outcome_type != 'entity_conflict'`

Anything failing these is skipped silently — not errored. A capture that is
ingested but never labeled is indistinguishable from one that was never
captured, from the gate's point of view.

### From `atlas/calibration/agreement.py`

- Pairing is `(prompt_version_id, provider)` set intersection. Replicate
  counts need not match between layers; each layer collapses independently.
  Layer A n=5 against Layer B n=3 is exactly what D-045 intends.
- Unpaired cells are reported, not dropped.

### From live DDL (2026-08-29)

`run_plans` columns: `id, property_id, run_type, replicate_count, planned_at,
status, window_start, window_end`.

There is **no `providers` column and no `market_id` column** on `run_plans`.
Provider is per-observation. This is why no migration is needed: a Layer B run
plan is an ordinary `run_plans` row, and the layer lives entirely on the
observations beneath it.

(Session notes recorded `providers=['perplexity']` and a `market_id` on run
plan `41f71293`. Neither column exists. The market almost certainly came from
`prompt_versions.market_id`. `run_gate`'s `market_id` argument is provenance
metadata for the stored result, not a join key. Worth confirming against
`store.py` before relying on it.)

`observations` constraints that bear on ingest:

| Column | Constraint | Consequence for Layer B |
|---|---|---|
| `task_id` | NOT NULL, UNIQUE | Must be synthesised. See §4. |
| `surface_layer` | NOT NULL, **DEFAULT `'api'`** | Must be set explicitly on every insert. |
| `model` | NOT NULL, DEFAULT `''` | Must carry something meaningful. See §5. |
| `provider` | CHECK in the 5-value list | Consumer capture of Perplexity is `'perplexity'`, never a variant. |
| `grounding_status` | nullable, no default | Leave NULL. |
| `replicate_index` | NOT NULL | 0-based, per cell. |
| `raw_response` | jsonb, nullable | See §5. |

The `surface_layer` default is the sharpest hazard in the whole design. A code
path that forgets the field does not fail — it writes an **API-layer row**,
which pollutes the AVS scoring frame with a human capture. That is precisely
the silent inversion D-043 was written to prevent, reintroduced from the write
side. Mitigation in §6.

`observations_google_ai_is_consumer_only` enforces D-042 at schema level.

### Frame size — no slack

The Frozen Core is exactly 10 prompts (verified: `version =
'frozen-core-samujana-v1'`, 3×A, 3×B, 3×C, 1×D, none holdout). So the paired
frame is at most 10 cells, and `agreement._SMALL_SAMPLE_CELLS = 10` tests
`n < 10`.

- All 10 cells pair → `n = 10` → small-sample check passes.
- **One cell missing → `n = 9` → `UNSTABLE_SMALL_SAMPLE` → `kappa_unusable` →
  the platform routes to the §8.4 fallback and needs ≥85% raw agreement *plus
  a named human reviewer* to reach ELIGIBLE.**

Not fatal — the fallback is reversible by recording the review — but it turns
an automatic pass into a human gate. **The capture protocol must therefore
cover all ten prompts with no abandonment.** This is a protocol rule, not a
code rule, and belongs in the operator instructions.

### Projected result for Perplexity

If the consumer layer reproduces the Layer A pattern (present on A3/B3/C3/D1,
absent on the other six): `both_yes=4, both_no=6`, po=1.0, pe=0.52,
kappa=1.0, prevalence index 0.20 → STABLE → passes on the kappa route with no
reviewer required. Spearman will not compute (4 co-mentions, below the 10
floor) and per §8.4 is descriptive only, which does not block the pass.

Stated here so the expected result is on record *before* the capture, not
fitted to it afterwards.

## 3. Run plan shape

One `run_plans` row per calibration capture campaign:

- `property_id` = Samujana `df2e65c5-190c-4879-88b4-78557176ef4e`
- `run_type` = `'frozen_core'`
- `replicate_count` = 3 (the §8.3 target; actual per-cell counts may be lower
  and are recorded on the observations)
- `window_start` / `window_end` = the human capture window

Note the §6.1 six-hour window (D-058) is an API-run concept. A human capture
campaign will not fit in six hours and should not pretend to. Recommend
registering this explicitly rather than leaving it as a second undocumented
deviation — see §8.

## 4. `task_id` scheme

`task_id` is NOT NULL and UNIQUE, and a human capture has no natural execution
id. It must be deterministic so that re-ingesting a corrected capture updates
rather than duplicates. A human pipeline *will* be re-run after mistakes;
idempotency is not optional.

```
consumer:{run_plan_id}:{prompt_version_id}:{replicate_index}
```

Ingest upserts on `task_id`. Re-running a capture for one cell overwrites that
cell and nothing else.

## 5. Write contract, column by column

| Column | Value |
|---|---|
| `task_id` | per §4 |
| `run_plan_id` | the consumer run plan |
| `prompt_version_id` | **one of the ten verified UUIDs** — reused, never re-seeded |
| `provider` | the platform's canonical value |
| `model` | the surface as observed, e.g. `'perplexity-web'` — see below |
| `model_snapshot`, `tool_version` | NULL |
| `replicate_index` | 0-based within the cell |
| `status` | `'complete'` on successful capture |
| `search_available`, `search_invoked` | NULL |
| `grounding_status` | NULL |
| `raw_response` | `{"capture_text": "..."}` — capture text only; see below |
| `request_time` / `completion_time` | capture start / end — the authoritative record of capture timing; see below |
| cost and token columns | NULL; `is_unknown_cost` = true |
| `surface_layer` | `'consumer'` — explicit, always |

**On `model`:** NOT NULL with a `''` default, and a consumer web UI exposes no
model identifier. Writing `''` is honest but loses the surface identity;
writing a fabricated model name would be worse. Recommend a documented literal
per surface (`'perplexity-web'`, `'chatgpt-web'`, `'gemini-web'`,
`'claude-web'`, `'google-ai-overviews'`) meaning "this consumer surface as
presented on the capture date". This needs a decision entry — §8.

**On evidence linkage:** a human capture with no screenshot is unverifiable
and must not be ingestible. Ingest should hard-fail on a missing evidence
reference rather than warn. But the link is written on the **evidence** row,
not on the observation. `observations.evidence_id` exists (migration 0001) as a
bare nullable `uuid` with **no foreign key and no writer anywhere in the
codebase** — `vault.store_evidence` populates `evidence.observation_id` and
never touches it, and the adapters' in-memory `evidence_id=""` is never
persisted. Linkage is therefore `evidence.observation_id`, unique per
observation since migration 0003.

**What the evidence row carries.** `store_evidence` copies its
`EvidenceRecord` straight through to the row, so ingest supplies every field:

- `data_class` = `'raw_ai_response'`. It is a parameter on the record, not
  hardcoded in the vault, and must satisfy the migration 0001 check constraint.
- `captured_by` = the operator, from `EvidenceRecord.operator`. This is the
  field Operating System §7 requires for human capture (migration 0001, cited
  in D-043).
- `source_reference` = the consumer surface URL. This is where the surface URL
  lives — not in the `raw_response` envelope. Stored untruncated in the column;
  only the Drive appProperties copy is trimmed at 124 bytes (D-049).
- `payload_hash` = SHA-256 over the screenshot's file bytes. NOT NULL, and not
  computable by `hash_payload`. See §8.

**On the envelope and capture timing.** The envelope holds `capture_text` only.
`captured_at` does not belong in it, because `store_evidence` does not write
`evidence.captured_at` — that column takes its DDL default `now()`, which is
*ingest* time, and `EvidenceRecord.captured_at` reaches only the Drive
appProperties. The observation's `request_time` / `completion_time` are
therefore the authoritative record of when the capture happened, and must be
set from the capture itself, never from the ingest run.

### Write ordering

**Observation row first, then `store_evidence` with that `observation_id`.**

The order is forced, not stylistic. `store_evidence` upserts with
`on_conflict='observation_id'` against the unique constraint from migration
0003. Postgres treats NULLs as distinct under a unique index, so a NULL
`observation_id` never conflicts and **every re-run appends another evidence
row**. A non-NULL `observation_id` is what makes the upsert idempotent, and
§4's re-ingest story depends on that idempotency reaching the evidence ledger,
not just the observation. (`scripts/store_84_evidence.py` passes NULL because a
property-level artifact has no observation to hang from, and documents that it
must guard on `payload_hash` itself as a result. Layer B has an observation, so
it must pass one.)

**Operator constraint: Drive must be reachable at ingest time.**
`store_evidence` uploads to Drive *before* writing the row, deliberately — a
row whose `storage_path` points at nothing falsely asserts that evidence exists
and can be produced on audit. If the upload fails it raises and no row is
written. It also raises outright when no folder is configured
(`GOOGLE_DRIVE_EVIDENCE_FOLDER_ID` unset and no `folder_id` passed). There is
no local-only or deferred-upload mode, so `--commit` cannot be run offline.
This belongs in the operator instructions alongside the ten-prompt protocol
rule from §2.

## 6. Ingest tool

`atlas/tools/consumer_ingest.py`, mirroring the export/validate/import shape of
`rpv_labeling.py` rather than inventing a second idiom.

**Template.** Emits one row per (prompt, replicate) for the campaign, in tier
order, pre-filled with prompt text and the target `prompt_version_id`. The
operator fills capture text and evidence path. Deterministic sort, as in
`rpv_labeling`'s export.

**Validate.** Refuses to write on: any `prompt_version_id` not in the verified
ten; missing evidence reference; missing capture text; a provider outside the
check constraint; a cell with more replicates than the plan's
`replicate_count`. Warns (does not refuse) on: fewer than 3 replicates for a
cell, and any cell entirely absent — the second being the condition that
drops the frame to n=9.

**Commit.** `--commit` idiom as per the Layer A driver. Upsert on `task_id`.
`surface_layer='consumer'` passed as a required positional argument in the
insert construction, never defaulted, never optional — the DDL default makes
omission silent and wrong.

Post-commit assertion, before returning success:

```sql
select count(*) from observations
where run_plan_id = :plan and surface_layer <> 'consumer'
```

Non-zero means rows leaked into the API frame. Fail loudly.

## 7. Labeling path

Consumer observations need the same one-client-entity-row-per-observation
treatment as Layer A, via the same `recommendations` boundary — `load_cells`
reads exactly the table the scoring loader reads.

**Labeling upsert key.** Migration 0006 added `UNIQUE (observation_id,
entity_name)` on `recommendations` (constraint
`recommendations_observation_id_entity_name_key`, D-048). That is the conflict
target for the labeling write, and it makes re-labeling a corrected capture an
update rather than a duplicate — the same property §4 gives ingest. Note that
`recommendations.rpv` is `numeric(4,2)` **NOT NULL**: a Methodology §4.1
position value must be computed at import time. There is no writing the row now
with a NULL and scoring it later.

`response_text()` in `atlas/adapters/base.py` currently has one verified
branch (Perplexity) and is adapter-payload-shaped. A consumer capture's
`raw_response` is the §5 envelope, not an adapter payload. Add a branch keyed
on the envelope's `capture_text`, checked *before* provider dispatch — a
consumer Perplexity row carries `provider='perplexity'` and would otherwise
fall into the API branch and fail on a missing `choices` key.

`validate()`'s content-sanity check (entity_name must appear in raw_response)
works unchanged on the envelope.

**On `negative_mention`.** `outcome_type` admits `'negative_mention'`
(migration 0004, D-035, which renamed 0001's `'negative'` and `'source_only'`
to Methodology §4.1's own names). It is absent from
`loader.MENTION_OUTCOMES`, which is `{RANKED, UNORDERED_POSITIVE}` only —
correctly so: a negative mention is not a presence judgment for the gate, for
the same reason a source-only citation is not. Labeling a consumer capture
`'negative_mention'` records the outcome faithfully and counts as a non-mention
on the agreement frame, which is the intended behaviour, not a gap.

## 8. Decisions to register

1. **Layer B ingest requires no schema change.** `run_plans` carries no
   provider or layer; a consumer run plan is an ordinary row. Records that
   D-056's flag was upstream-only and that `run_gate` was never missing an
   argument.
2. **`model` literal for consumer surfaces.** The per-surface value and its
   meaning.
3. **§6.1's run window does not bind Layer B.** A human capture campaign spans
   days by design; §6.1 governs the API instrument. Distinct from D-058, which
   accepted a deviation for an API run.
4. **`task_id` synthesis and upsert idempotency** for human-originated
   observations.
5. **Automation posture (from tonight's sidenote).** Manual capture retained;
   assisted-capture tooling permitted; the automation clause is load-bearing
   for D-042 and any change is a §10 change-control matter.
6. **Promote a `sha256_file` helper into `atlas/evidence/vault.py`.**
   `hash_payload` takes a dict and canonical-JSON-encodes it; it cannot hash a
   screenshot, and `evidence.payload_hash` is NOT NULL.
   `scripts/store_84_evidence.py` already carries a private copy for exactly
   this reason. Layer B evidence is binary by definition, so the helper should
   live in the vault beside `hash_payload` rather than be copied a third time.

## 9. Verify before implementing

One item outstanding.

The `evidence` and `recommendations` contracts above were read from
`migrations/` — 0001 through 0008 — and from `atlas/evidence/vault.py`, not
from live DDL. Every constraint cited is sourced to a migration file, so the
reasoning holds exactly as far as the live database matches the applied
migration set. Confirm it has not drifted before writing code against §5, §6
or §7:

```bash
psql "$PGURI" -c '\d evidence' -c '\d recommendations' -c '\d observations'
```
