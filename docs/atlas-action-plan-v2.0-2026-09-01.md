# Atlas Action Plan v2.0 — Locked Sequence to v1.0-MVP Freeze

**Date:** 2026-09-01
**Supersedes:** informal Week 1–3 plan (chat, 2026-09-01)
**Inputs:** Framework Advisory v1.1 (2026-08-31) · Team Feedback Review & Final Sequence (2026-09-01) · verified code audit of `atlas_export.zip`
**Status:** Approved direction — Engine safety → provider/methodology stabilization → frozen Samujana calibration → pre-Client-#1 productization.

Two additions beyond the advisor's Final Sequence, agreed 2026-09-01:

1. **Perplexity cutover deadline branch.** If measurement-contract equivalence for the Agent API adapter is not demonstrated by **2026-09-20**, a new decision-register entry explicitly decides Perplexity Layer A's status for the calibration window (evidence-only, or excluded from the Samujana gate). Sonar is never silently run past the retirement date, and the migration is never silently deferred.
2. **Rank-untestable rule is pre-decided.** With a ten-prompt Frozen Core and D-045 cell-level analysis, the Spearman ≥10 co-mention floor means `rank_valid = untestable` is the *expected* Samujana outcome. The scoring consequence (Option A or B, see `draft-decisions-pending.md`) is decided and registered **before Phase C**, not discovered at gate time.

---

## Phase A — Engine safety (target: ~2026-09-06)

One coherent block; root cause is shared (execution state enforced by application convention rather than database infrastructure). Implementation spec: `phase-a-engine-safety-SPEC.md`.

- Migration adding `lease_owner`, `lease_acquired_at`, `lease_expires_at`, `attempt_no`, `max_attempts`, `next_attempt_at`, `provider_request_id` to `observations`.
- One atomic claim path: Postgres function using `FOR UPDATE SKIP LOCKED`, lease expiry and retry ceiling enforced server-side.
- `plan_run` becomes insert-missing-only (no status/model regression on re-plan).
- `reconcile_run` recovers only expired-lease work; never marks a run reconciled while work remains incomplete; respects the retry ceiling.
- `resume.py` evidence writes routed through `vault.store_evidence()` — full D-049 provenance, Drive upload, no direct row writes.
- Budget rail scoped to current billing month and invoked before each batch.
- Scheduled workflows fail closed (placeholder steps `exit 1` or schedules disabled) until real runners exist.
- PR CI: pytest, ruff, migration validation, smoke test — required to merge.
- Concurrent-worker tests for claim/lease behaviour.

**Model:** Claude Code / Opus (schema, claim RPC, planner/reconcile logic). Sonnet for CI workflow wiring and month-scoping the ledger.

## Phase B — Provider and methodology stabilization (target: ~2026-09-13, hard branch 2026-09-20)

- Perplexity Agent API research spike answering the five equivalence questions (native-model pinning, stable recordable model identity, verifiable search invocation, deterministic citation→Evidence Vault mapping, behavioural comparability with the Sonar baseline). Build the adapter behind the provider abstraction. **Cut over only on demonstrated equivalence; apply the deadline branch above otherwise.** Revalidate the 27 Sep retirement against an authoritative Perplexity source; retain it as the internal migration deadline regardless.
- Perplexity consumer access-state conflict resolved via the controlled test matrix (protocol in `draft-decisions-pending.md` §3); superseding decision registered for whichever prior factual record the matrix disproves.
- Consumer Surface Profiles formalized: versioned registry generalizing D-059/D-060/D-066/D-067; every Layer B observation references a profile version (e.g. `perplexity-web/1.1`). Minimum fields per the Final Sequence §3.
- Samujana capture geography defined from guest-origin / booking-origin evidence (source, lookback window, fallback rule; multi-market policy if no single market is representative). Registered before the calibration gate.
- Rank-validity rule decided and registered (Addition 2).
- Current Spec page generated from active decisions; every accepted decision updates impacted governing docs in the same PR from here on.

**Model:** Code/Opus for the adapter rebuild and equivalence harness. Chat/Opus for decision drafting and the geography policy; the access matrix is manual capture work.

## Phase C — Freeze and calibrate Samujana

- Lightweight calibration envelope written before results are interpreted: exactly what this one property can and cannot validate (methodology version + platform + API model + surface-profile version + market + language + property profile).
- Freeze: prompts, scoring rules, provider/model versions, replicate counts, geography, surface-profile versions.
- Complete ChatGPT, Gemini and Claude consumer captures under frozen profiles; Perplexity Layer B enters only under its approved profile version; protocol versions never mixed in one agreement calculation.
- Run and document the calibration as scoped v1.0-MVP validation — not universal validation of every hospitality category and market.
- D-058's single-run-plan deviation stands for this baseline as registered; it is retired for future cycles by the Phase D measurement-cycle abstraction, not patched now.

## Phase D — Before Client #1

- Transferable calibration-scope model (where validation transfers).
- **Separate** `measurement_cycle` abstraction (which run plans constitute one scoreable measurement); `compute_avs` takes a cycle, retiring the single-`run_plan_id` assumption and the six-hour deviation for all future cycles.
- Profile and criterion registries (retire `properties.category` free text).
- Deterministic report renderer + Client Release Pack (report, run IDs, methodology version, calibration scope, evidence manifest, cost/time summary, sign-off).
- Privacy/schema hardening: remove `guest_personal_operational` from the Visibility `data_class` constraint; least-privilege review of service-role use.
- Contract and marketing language: "selected AI platforms," enumerated coverage, no Copilot claim until instrumented and calibrated.
- Final operational acceptance test: clean environment → client-ready release pack.

---

## v1.0-MVP freeze gates (lift into Execution Plan §2 as the G2 checklist)

| Gate | Requirement |
|---|---|
| Execution safety | No duplicate claims, silent retry loops, destructive plan recreation, or placeholder-success workflows. |
| Evidence integrity | All production observations enter through the Evidence Vault path with complete provenance. |
| Provider identity | Every Layer A observation records the actual provider/model/search configuration used. |
| Consumer reproducibility | Every Layer B observation references a versioned Consumer Surface Profile. |
| Geography | Samujana's capture market is evidence-based and documented. |
| Calibration envelope | Atlas states in advance what Samujana can and cannot validate. |
| Rank claims | Mention validity and rank validity have explicit, tested consequences for AVS. |
| Documentation | Active decisions and Current Spec agree with implementation. |

All gates must pass before v1.0-MVP is treated as frozen.
