# Draft Decisions — Pending Acceptance

These are **candidate texts**, not register entries. Per Methodology §10, a decision is documented in `docs/decision-register.md` (in-repo, committed) *before* implementation. Accept, edit, or reject each here, then register in-repo with the next available IDs. §2 requires a founder choice before it can be registered.

---

## 1. REGISTERED AS D-068 — see decision-register.md

> Perplexity Layer A migration from Sonar Chat Completions (D-031) to the Agent API is governed by a measurement-contract equivalence gate, not an integration test. The Agent adapter is built immediately behind the provider abstraction, but production cutover requires all of: (a) the resolved model identity is Perplexity-native or explicitly pinned, stable, and recordable in evidence per observation; (b) web/search invocation is verifiable from the response, not inferred; (c) citations and search results map deterministically into the Evidence Vault; (d) repeated behaviour on the Frozen Core prompt set is demonstrated comparable with the current Sonar baseline (side-by-side run, documented). Reason: Atlas measures Perplexity — an endpoint swap that resolves to a third-party routed model changes the instrument even when the migration technically succeeds, invalidating cross-period comparability. Deadline branch: if equivalence is not demonstrated by 2026-09-20, a further decision explicitly sets Perplexity Layer A's status for the Samujana calibration window (evidence-only, or excluded from the §8.4 gate) — Sonar is never silently run past the retirement date and the migration is never silently deferred, per D-031's own instruction that the deadline must not lapse silently. The 2026-09-27 retirement date is revalidated against an authoritative Perplexity source and downgraded from "confirmed shutdown" to "internal migration deadline" if unverifiable; the internal deadline is retained either way.

## 2. Candidate — Rank-validity rule (FOUNDER CHOICE REQUIRED: Option A or B)

Shared preamble for either option:

> The §8.4 gate is split into two validity levels recorded per platform in `calibration_results`: **mention_valid** (existing kappa / fallback routes, unchanged) and **rank_valid** (Spearman rho ≥ 0.50 over ≥ 10 co-mentioned prompt-platform cells, per the existing §8.4 floor and D-045's cell-level unit). Structural fact forcing this decision now: the Samujana Frozen Core is ten prompts, so the ≥10 co-mention floor requires every cell co-mentioned — `rank_valid = untestable` is therefore the expected v1.0-MVP outcome, not an edge case, and the scoring consequence must exist before the gate runs so the rule is not chosen after seeing which reading passes (D-045 precedent).

**Option A — presence-only scoring for non-rank-validated platforms.**

> Where `mention_valid = true` and `rank_valid` is false or untestable, that platform's PlatformScore is computed presence-only: a mentioned cell contributes RPV 1.00 regardless of rank; absent/negative/source-only handling unchanged. Rank-weighted scoring for a platform activates only once rank_valid is established for its calibration scope. Consequence accepted: v1.0-MVP AVS is a presence-weighted score and is described as such in client claims; rank behaviour is reported descriptively alongside. Cleaner claim, materially different AVS number.

**Option B — rank-weighted scoring retained, with a mandatory rank-validation caveat.**

> Where `mention_valid = true` and `rank_valid` is false or untestable, rank-weighted RPV scoring proceeds, and every score row, report and client claim derived from it carries a mandatory caveat that rank agreement between API and consumer surface is not yet validated for that platform, with the co-mention count and rho (where computable) stated. Consequence accepted: the AVS number retains §4.1's full rank sensitivity, but the calibration claim behind it is explicitly weaker until co-mention volume grows. Stronger number, weaker claim.

Either option: the chosen rule is encoded in `gate.py` + scoring and covered by tests before Samujana becomes the reference calibration (freeze gate "Rank claims").

**Recommendation:** Option A. Atlas's differentiator is claims integrity; a presence-weighted v1.0-MVP score whose every component is calibrated beats a rank-weighted score carrying a permanent asterisk, and rank weighting becomes a documented v1.x upgrade with its own validation story. Option B is defensible if early client conversations demand rank sensitivity — but the caveat must then survive into marketing, not just the report appendix.

## 3. REGISTERED AS D-069 — see decision-register.md

> The conflicting factual records on the perplexity-web anonymous signup wall (D-066-era observations vs. the later roadblock brief) are resolved by a controlled matrix, not a spot check. Conditions: {direct residential connection, IPRoyal residential proxy} × 3 clean sessions each, identical prompt, browser, incognito/session and account state per D-059, executed within one 24-hour window. Recorded per session: UTC timestamp, IP geography and type, account state, browser/session state, wall outcome (gated / not gated), screenshot evidence per Layer B requirements. Outcome rules: wall reproduces in all six → perplexity-web has no anonymous access path; a D-067-pattern scoped logged-in profile (dedicated Atlas test account, memory off) is created as a new surface-profile version. Wall absent on direct but present via proxy → IP-reputation artifact; anonymous capture stands, proxy usage for perplexity-web is re-decided. Mixed/intermittent → anonymous access is treated as unstable and therefore not a reproducible protocol; the logged-in profile path is taken. The resulting decision explicitly names and supersedes whichever prior factual record the matrix disproves.

---

**Registration order:** accept §1 and §3 now (no dependency); decide §2 before Phase C begins. All three follow the same-PR rule from Action Plan Phase B: register entry + impacted governing-doc updates land together.
