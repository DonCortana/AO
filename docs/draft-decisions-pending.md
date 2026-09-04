# Draft Decisions — Pending Acceptance

These are **candidate texts**, not register entries. Per Methodology §10, a decision is documented in `docs/decision-register.md` (in-repo, committed) *before* implementation. Accept, edit, or reject each here, then register in-repo with the next available IDs. All four sections below are now registered; this file is retained as a pointer to their register IDs.

---

## 1. REGISTERED AS D-068 — see decision-register.md

> Perplexity Layer A migration from Sonar Chat Completions (D-031) to the Agent API is governed by a measurement-contract equivalence gate, not an integration test. The Agent adapter is built immediately behind the provider abstraction, but production cutover requires all of: (a) the resolved model identity is Perplexity-native or explicitly pinned, stable, and recordable in evidence per observation; (b) web/search invocation is verifiable from the response, not inferred; (c) citations and search results map deterministically into the Evidence Vault; (d) repeated behaviour on the Frozen Core prompt set is demonstrated comparable with the current Sonar baseline (side-by-side run, documented). Reason: Atlas measures Perplexity — an endpoint swap that resolves to a third-party routed model changes the instrument even when the migration technically succeeds, invalidating cross-period comparability. Deadline branch: if equivalence is not demonstrated by 2026-09-20, a further decision explicitly sets Perplexity Layer A's status for the Samujana calibration window (evidence-only, or excluded from the §8.4 gate) — Sonar is never silently run past the retirement date and the migration is never silently deferred, per D-031's own instruction that the deadline must not lapse silently. The 2026-09-27 retirement date is revalidated against an authoritative Perplexity source and downgraded from "confirmed shutdown" to "internal migration deadline" if unverifiable; the internal deadline is retained either way.

## 2. REGISTERED AS D-093 — see decision-register.md

## 3. REGISTERED AS D-069 — see decision-register.md

> The conflicting factual records on the perplexity-web anonymous signup wall (D-066-era observations vs. the later roadblock brief) are resolved by a controlled matrix, not a spot check. Conditions: {direct residential connection, IPRoyal residential proxy} × 3 clean sessions each, identical prompt, browser, incognito/session and account state per D-059, executed within one 24-hour window. Recorded per session: UTC timestamp, IP geography and type, account state, browser/session state, wall outcome (gated / not gated), screenshot evidence per Layer B requirements. Outcome rules: wall reproduces in all six → perplexity-web has no anonymous access path; a D-067-pattern scoped logged-in profile (dedicated Atlas test account, memory off) is created as a new surface-profile version. Wall absent on direct but present via proxy → IP-reputation artifact; anonymous capture stands, proxy usage for perplexity-web is re-decided. Mixed/intermittent → anonymous access is treated as unstable and therefore not a reproducible protocol; the logged-in profile path is taken. The resulting decision explicitly names and supersedes whichever prior factual record the matrix disproves.

## 4. REGISTERED AS D-100 — see decision-register.md

The candidate text drafted here (Layer B `provider_scope` for the `frozen-core-samujana-v2` plan) is superseded by D-100 and removed. D-100 scopes the plan at `{openai, gemini, anthropic}` — three surfaces, perplexity-web excluded pending D-068 and D-069 — which is not the four-surface parity candidate this section proposed. Run plan `cb09f157-58d5-4a51-9faa-fba035e00199` was created under D-100 on 2026-09-03.

---

**Registration order:** all five registered 2026-09-01 to 2026-09-04 — §1 as D-068, §2 as D-093 (Option A, presence-only, unblocking Phase C), §3 as D-069, §4 as D-100 (three surfaces, not the four this file proposed), §5 as D-103. §1–§4 followed the same-PR rule from Action Plan Phase B: register entry + impacted governing-doc updates land together. D-103 was registered without accompanying governing-doc updates; the Methodology v1.0 statement of the AVS denominator that D-103 requires is outstanding.

