# Draft Decisions — Pending Acceptance

These are **candidate texts**, not register entries. Per Methodology §10, a decision is documented in `docs/decision-register.md` (in-repo, committed) *before* implementation. Accept, edit, or reject each here, then register in-repo with the next available IDs. Sections 1-3 are registered; this file is retained as a pointer to their register IDs. Section 4 is open.

---

## 1. REGISTERED AS D-068 — see decision-register.md

> Perplexity Layer A migration from Sonar Chat Completions (D-031) to the Agent API is governed by a measurement-contract equivalence gate, not an integration test. The Agent adapter is built immediately behind the provider abstraction, but production cutover requires all of: (a) the resolved model identity is Perplexity-native or explicitly pinned, stable, and recordable in evidence per observation; (b) web/search invocation is verifiable from the response, not inferred; (c) citations and search results map deterministically into the Evidence Vault; (d) repeated behaviour on the Frozen Core prompt set is demonstrated comparable with the current Sonar baseline (side-by-side run, documented). Reason: Atlas measures Perplexity — an endpoint swap that resolves to a third-party routed model changes the instrument even when the migration technically succeeds, invalidating cross-period comparability. Deadline branch: if equivalence is not demonstrated by 2026-09-20, a further decision explicitly sets Perplexity Layer A's status for the Samujana calibration window (evidence-only, or excluded from the §8.4 gate) — Sonar is never silently run past the retirement date and the migration is never silently deferred, per D-031's own instruction that the deadline must not lapse silently. The 2026-09-27 retirement date is revalidated against an authoritative Perplexity source and downgraded from "confirmed shutdown" to "internal migration deadline" if unverifiable; the internal deadline is retained either way.

## 2. REGISTERED AS D-093 — see decision-register.md

## 3. REGISTERED AS D-069 — see decision-register.md

> The conflicting factual records on the perplexity-web anonymous signup wall (D-066-era observations vs. the later roadblock brief) are resolved by a controlled matrix, not a spot check. Conditions: {direct residential connection, IPRoyal residential proxy} × 3 clean sessions each, identical prompt, browser, incognito/session and account state per D-059, executed within one 24-hour window. Recorded per session: UTC timestamp, IP geography and type, account state, browser/session state, wall outcome (gated / not gated), screenshot evidence per Layer B requirements. Outcome rules: wall reproduces in all six → perplexity-web has no anonymous access path; a D-067-pattern scoped logged-in profile (dedicated Atlas test account, memory off) is created as a new surface-profile version. Wall absent on direct but present via proxy → IP-reputation artifact; anonymous capture stands, proxy usage for perplexity-web is re-decided. Mixed/intermittent → anonymous access is treated as unstable and therefore not a reproducible protocol; the logged-in profile path is taken. The resulting decision explicitly names and supersedes whichever prior factual record the matrix disproves.

---

**Registration order:** all three registered 2026-09-01 to 2026-09-03 — §1 as D-068, §2 as D-093 (Option A, presence-only, unblocking Phase C), §3 as D-069. All three followed the same-PR rule from Action Plan Phase B: register entry + impacted governing-doc updates land together.


---

## 4. OPEN — candidate for the next available ID: Layer B provider scope for the Samujana v2 plan

**Status: not registered. This is the one value the fresh Layer B run plan
cannot be created without, and it is the value nobody has decided.**

D-098 named it explicitly while deleting d2b1c8a3: "d2b1c8a3 is the consumer
plan scoped at four surfaces and its correct `provider_scope` is a live
question D-086 left open, not a fact recoverable from data." Migration 0012
then made the question unavoidable — `run_plans_frozen_core_has_provider_scope`
is a VALIDATED CHECK, so a Layer B `frozen_core` plan cannot be inserted at
all without a scope. Preflight for the v2 plan otherwise passes end to end
against live data; this is the only thing it is waiting on.

### Candidate text

> The Samujana Layer B plan at `frozen-core-samujana-v2` is scoped at
> `{openai, gemini, perplexity, anthropic}` — the four surfaces with an API
> counterpart — and `google_ai` is excluded from this baseline rather than
> deferred within it. Reason: D-090's Scope Parity Gate refuses a §8.4 gate
> run whose Layer A and Layer B plans disagree on any manifest field, and
> D-086 re-plans Layer A across exactly those four adapters, so any other
> Layer B scope makes the two legs of the same calibration disagree by
> construction. `google_ai` publishes no API and therefore has no Layer A leg
> to pair with — structurally the D-042 condition — so including it would
> reproduce, at v2, the exact defect D-086(b) recorded against d2b1c8a3:
> surfaces captured at Layer B with no pairable counterpart, and the gate
> undefined for them. Capturing Google AI Overviews as an unpaired
> consumer-only surface may still be worth doing; it is a separate plan under
> a separate decision, not a fifth element of this one. Cell count: 11 prompts
> x 4 surfaces x 3 replicates = 132, against D-091's ninety, which was
> computed at ten prompts and three surfaces.

### What is NOT established, and why this is a decision rather than an inference

- **Which four surfaces d2b1c8a3 was scoped at is not recorded anywhere.**
  D-086(b) says "four consumer surfaces" and never names them. The row is
  deleted, it held zero observations, and no column recorded the scope — that
  absence is what migration 0012 exists to end. So the candidate above is
  derived from D-090's parity requirement, not recovered from the prior plan;
  if the prior scope included `google_ai`, this candidate changes it, and that
  change should be visible as a decision rather than absorbed as a re-creation.
- **The three unblocked surfaces of D-091 are not identified either.** D-091
  refers to "the ninety Layer B cells on the three unblocked surfaces",
  implying one of four was blocked (D-069's perplexity-web signup wall is the
  obvious candidate, registered but its matrix outcome not recorded here). If
  a surface is still operationally blocked, a four-surface scope is a scope
  that cannot be fully captured, and the honest options are a three-surface
  scope with the Layer A leg matched to it, or a four-surface scope with the
  blocked surface's cells expected to land as an explicit gap. Either is a
  decision; neither is a default.
- **Cost.** 132 cells of human browser capture against 90 is a material
  increase in operator time, and D-097 already accepted a re-baseline cost.
  Recorded as a factor, not as an argument against.

### Blocked on this

`scripts/create_consumer_run_plan.py --commit` for Samujana at
`frozen-core-samujana-v2`. Everything else is ready: property
`df2e65c5-190c-4879-88b4-78557176ef4e`, market
`2d4854b9-5589-44a5-886b-c895e99c7b95` (TH/en, D-088), the eleven v2
`prompt_version_ids` seeded and verified complete under D-083, replicate
target 3 (§8.3). The dry run passes every preflight check.
