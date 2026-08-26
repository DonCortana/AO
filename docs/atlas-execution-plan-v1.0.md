A T L A S O P T I M I S A T I O N

Atlas Execution Plan

Pre-Client \#1 hardening, System Zero, hospitality calibration and
commercial launch.

| **Document** | Atlas Execution Plan    |
|--------------|-------------------------|
| **Version**  | v1.0                    |
| **Status**   | LIVE EXECUTION DOCUMENT |
| **Date**     | 25 August 2026          |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>STATUS NOTE</strong></p>
<p>This plan changes as execution changes. It does not alter the scoring
methodology. The immediate objective is to reach a frozen, calibrated
Atlas Methodology v1.0 and first paid pilot with the least possible
technical and human overhead.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

**Be part of the answer.**

# Contents

**1.** Changes implemented before Client \#1

**2.** Hard gates

**3.** System Zero protocol

**4.** Hospitality calibration property

**5.** Commercial lane in parallel

**6.** Technical lane

**7.** Google setup actions

**8.** Revised twelve-week roadmap

**9.** Client \#1 readiness checklist

**10.** Operating metrics and go/no-go rules

**11.** Next twenty actions

# 1. Changes implemented before Client \#1

| **Priority** | **v1.1 issue**                                                  | **Implemented change**                                                                                            |
|--------------|-----------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| P0           | AVI mixes outcome and levers                                    | Retire AVI before launch. Official client metrics are AVS and Atlas Readiness Score.                              |
| P0           | Methodology declared frozen before calibration                  | v1.0 remains Release Candidate until System Zero and first hospitality calibration pass.                          |
| P0           | P2-P5 not fully reconstructable                                 | Add deterministic Score Specification for all readiness pillars.                                                  |
| P0           | Statistical change rules conflicted                             | Use paired hierarchical bootstrap, 5-point Minimum Reportable Change, and one verdict taxonomy.                   |
| P0           | Client Zero used as calibration despite non-hospitality profile | Rename to System Zero. It tests engineering only; first real neighborhood venue performs hospitality calibration. |
| P0           | API-only cost model understates delivery economics              | Track fully loaded human + infrastructure cost and require margin gates before \$900 Management is scaled.        |
| P0           | Weekly full benchmark creates avoidable spend                   | Use n=1 Sentinel weekly, n=1 Benchmark monthly, n=5 Frozen Core for official baseline/validation.                 |
| P1           | Provider adapters were too generic                              | Pin current APIs/tool behaviour and store explicit grounding state on every observation.                          |
| P1           | Gemini Maps opportunity absent                                  | Add Gemini Local Discovery / Maps Diagnostic as experimental, non-scored calibration signal.                      |
| P1           | Crawler rules mix search, retrieval and training bots           | Replace with purpose-based Crawler Access Matrix; training controls carry no visibility penalty.                  |
| P1           | GitHub Actions treated as reliable clock                        | Keep GitHub Actions but make runs idempotent, database-state-driven, reconciled and resumable.                    |
| P1           | GBP API assumed available at onboarding                         | Use manual/manager workflow first; API access is separate approval with 60+ day verified-profile prerequisite.    |
| P1           | Gemini auth transition not accounted for                        | Create Gemini authorization key now; do not build on a standard key that will be rejected from September 2026.    |
| P1           | Commercial activity sat behind build                            | Run prospecting and discovery calls in parallel; sell delivery slots, not unvalidated scores.                     |
| P1           | \$400 can anchor value too low                                  | Limit \$400 to max 3 invite-only founding pilot profiles; standard price follows measured unit economics.         |
| P1           | Visibility and guest automation shared data layer               | Separate guest PII and Automation operational data from Visibility research data and credentials.                 |

## 1.1 Additional polish applied

- Equal-weight eligible AI platforms in the standard AVS until Atlas has
  defensible market-specific usage evidence. Platform-level scores
  remain visible.

- Source-only citations no longer raise AVS. They are tracked as
  citation intelligence.

- Hold-out prompts test generalization, not external drift. Competitor
  panels and provider-change logs are the primary drift controls.

- Evidence immutability is separated from personal-data retention so
  GDPR deletion obligations cannot conflict with an absolute no-delete
  rule.

- Client-facing reports use neutral visibility and readiness bands. The
  label AI-Optimised is removed because it can overstate outcome
  control.

- OAuth / delegated manager access is the default. Reusable client
  passwords should not enter Atlas systems where delegated access is
  available.

# 2. Hard gates

| **Gate**                   | **Pass condition**                                                                                                        | **What it unlocks**                  |
|----------------------------|---------------------------------------------------------------------------------------------------------------------------|--------------------------------------|
| G0 Accounts and security   | Workspace, GitHub, Supabase and provider accounts secured; 2FA; secrets configured; Gemini auth key used                  | Safe development                     |
| G1 System Zero engineering | End-to-end run, evidence hash, cost row, report generation, retry/resume and reconciliation pass; P0 defects closed/owned | Hospitality calibration              |
| G2 Hospitality calibration | Real venue completes calibration; eligible platforms meet thresholds; scoring hand-check passes                           | Freeze Atlas Methodology v1.0        |
| G3 Commercial readiness    | Agreement, DPA as applicable, RACI, intake, report template, unit-cost model, price floor complete                        | First paid pilot delivery            |
| G4 Management readiness    | At least one validation cycle completed; monthly workload and gross margin meet gate                                      | Offer ongoing Management confidently |

# 3. System Zero protocol

System Zero is Atlas running its own domain through the complete
delivery plumbing. It is not scored as a hospitality client and is never
used to claim methodology validity.

| **Test**          | **Acceptance criterion**                                                                     |
|-------------------|----------------------------------------------------------------------------------------------|
| Provider adapters | One valid grounded observation from every provider against one standard observation contract |
| Grounding state   | search/tool invocation correctly detected; ungrounded responses not silently scored          |
| Database          | Schema migrated; row-level security active; observation IDs idempotent                       |
| Evidence          | Raw response stored, SHA-256 hash verified, manifest built after reconciliation              |
| Resumability      | Intentionally kill a batch mid-run; recovery completes missing tasks without duplicates      |
| Cost ledger       | Every API/tool call creates a cost record; unknown cost is flagged, never treated as zero    |
| Parser            | Assisted manual first; benchmark sample created for later accuracy testing                   |
| Report            | AVS/ARS test report renders from stored data without manual number transcription             |
| Time study        | Wall-clock and active human minutes captured for every stage                                 |
| Defect log        | Every ambiguity, workaround and failure recorded; P0 items closed or assigned                |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>PARALLELISM RULE</strong></p>
<p>System Zero must not delay commercial conversations. Outreach may
begin while System Zero runs. What may not happen is delivery of a
frozen Atlas score before the hospitality calibration gate
passes.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

# 4. Hospitality calibration property

After System Zero passes, Atlas selects one real hospitality business in
the neighborhood as the first calibration property. No venue is selected
in this document; selection happens when the system is ready.

- Prefer a venue with a functioning website, Google Business Profile, at
  least one review platform, third-party mentions and enough public data
  to exercise all four readiness pillars.

- Use one primary market/language for the first calibration. A second
  market may be added only if it materially tests locale behaviour
  without delaying the freeze gate.

- Run the Frozen Core through Layer A at n=5 and consumer-surface
  calibration at the documented calibration cadence.

- Run Gemini Local Discovery / Maps Diagnostic separately and store
  results outside AVS.

- Compute raw mention agreement, Cohen kappa and rank agreement per
  platform. Any failing platform is evidence-only.

- Hand-calculate a representative AVS and each readiness pillar to
  verify engine output.

- Using calibration data, check that the RPV value for an unordered
  positive recommendation (0.30) sits correctly between the Rank 6-10
  (0.25) and Rank 4-5 (0.45) bands. Adjust only via a major methodology
  version with a documented decision, not ad hoc.

- Issue an internal calibration report and defect log. When all P0
  defects are resolved or owned, freeze v1.0.

# 5. Commercial lane in parallel

| **Timing**                      | **Commercial activity**                                                                 | **Target**                                                |
|---------------------------------|-----------------------------------------------------------------------------------------|-----------------------------------------------------------|
| Now - System Zero               | Build a local prospect list and begin founder-led discovery outreach                    | 30 qualified venues; 10 highly personalized introductions |
| Calibration week                | Invite one suitable venue into calibration; schedule future pilot slots                 | 3-5 discovery calls                                       |
| Post-freeze                     | Offer max 3 founding pilot audits at \$400/profile, invite-only                         | Proof, case-study data and process learning               |
| After 3 pilots                  | Set standard public audit price from fully loaded cost + value                          | \>=65% delivery gross margin; target 75%                  |
| After one full validation cycle | Offer Visibility Management; only then cross-sell Hospitality Automation where relevant | Recurring revenue based on demonstrated process           |

The \$400 founding price remains off the public website. It is a
controlled learning incentive, not the market price of the full Atlas
audit. Any case-study or testimonial permission is requested separately
and never assumed.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>MANAGEMENT PRICE GATE</strong></p>
<p>At a $900 monthly fee, the fully loaded direct delivery cost must be
&lt;=$315 to preserve a 65% delivery gross margin. If it is higher,
either reduce manual workload/cadence or increase price before
scaling.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

# 6. Technical lane

1\. Create one private GitHub repository with protected main branch,
CODEOWNERS later if team expands, Actions secrets and manual dispatch
recovery.

2\. Create Supabase project for Atlas Visibility. Implement clients,
properties, markets, prompt versions, run plans, observations,
recommendations, citations, scores, actions, evidence, costs and audit
logs.

3\. Implement run planner first. It creates the expected task list in
the database before any provider call.

4\. Build OpenAI adapter and prove one observed web-search call, source
record, hash and cost row.

5\. Build Gemini adapter with authorization key and explicit Google
Search grounding-state capture.

6\. Build Perplexity Sonar adapter and treat search_results as canonical
source output.

7\. Build Anthropic Messages + web-search adapter and test pause_turn
continuation.

8\. Add retry, idempotency and reconciliation. Intentionally simulate
provider failures.

9\. Add scoring engine only after raw observations are trustworthy.

10\. Add crawler, entity, reputation and authority engines in the order
of measured manual burden.

# 7. Google setup actions

| **Action**                      | **Decision now**                                                                                                                                                                                                                         |
|---------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Gemini authentication           | Create/use authorization key immediately, before any other Week 1 setup. Standard keys are rejected from September 2026 - do not sequence this behind repo/Supabase/secrets work. Do not start production work on a standard Gemini key. |
| Google Business Profile API     | Do not block launch on API access. Google requires project approval and a verified active profile managed for 60+ days. Use manager access / manual workflow until Atlas qualifies.                                                      |
| Google Search Console           | Use client-delegated access/OAuth during onboarding where available.                                                                                                                                                                     |
| Gemini Maps grounding           | Enable only in calibration diagnostic requests. Store Maps output separately from AVS.                                                                                                                                                   |
| Google-Extended crawler control | Treat as Gemini-use control, not Google Search indexing/ranking. Document client choice.                                                                                                                                                 |

# 8. Revised twelve-week roadmap

| **Week** | **Primary objective**                                                                 | **Acceptance criterion**                                                                                                |
|----------|---------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| 1        | Security, repo, Supabase, run planner, System Zero starts                             | One provider call can create a planned task, completed observation, hash and cost row                                   |
| 2        | All four provider adapters + grounding verification                                   | One comparable grounded observation from all four providers; Gemini auth key already active since action 0 (pre-Week 1) |
| 3        | Resumability, reconciliation, evidence manifest, System Zero defect review            | Killed run resumes without duplicate scored observations; P0 System Zero defects closed/owned                           |
| 4        | Select and onboard neighborhood hospitality calibration property                      | Applicability, peers, prompt set, access and evidence plan locked                                                       |
| 5        | Hospitality calibration + Gemini Maps diagnostic                                      | Per-platform calibration report complete; eligibility decided                                                           |
| 6        | Freeze Methodology v1.0 + generate first calibrated report                            | Hand calculations match engine; v1.0 frozen; client brief activated                                                     |
| 7        | Deliver founding pilot \#1; crawler + entity automation                               | Pilot report delivered; time/cost log complete                                                                          |
| 8        | Founding pilots \#2-3; reputation ingest and authority workflow                       | Pilot delivery repeatable; top manual burdens identified                                                                |
| 9        | Report automation + action engine                                                     | Report populated from DB; no manual number transcription                                                                |
| 10       | Management cycle dry run                                                              | Sentinel, monthly benchmark, readiness refresh and report operate on schedule                                           |
| 11       | Unit economics and pricing review; delegate repeatable evidence work if threshold met | Public audit price and Management floor set from measured cost                                                          |
| 12       | Sell recurring Management; backlog only what saves time or improves evidence          | At least two paid audits or equivalent pipeline; next quarter build plan tied to demand                                 |

# 9. Client \#1 readiness checklist

- Atlas Methodology v1.0 frozen after hospitality calibration, not
  before.

- Eligible platforms and calibration figures documented.

- System Zero and calibration defect logs reviewed; all P0 defects
  closed or owned with date.

- Client Frozen Core, Sentinel and Benchmark sets versioned.

- Applicability Matrix and peer set locked before scoring.

- Client RACI, agreement, privacy terms/DPA where applicable and access
  method confirmed.

- Evidence path, hash, manifest and restore test verified.

- Budget, API cost and human-time ledger enabled for the client.

- Report template renders AVS + ARS from database facts.

- Standard disclaimer and methodology version appear in deliverable.

- Automation product remains commercially and technically separate from
  Visibility data.

# 10. Operating metrics and go/no-go rules

| **Metric**                       | **Target / trigger**                                                                           |
|----------------------------------|------------------------------------------------------------------------------------------------|
| System Zero P0 defects           | 0 unresolved without owner/date before calibration                                             |
| Calibration mention agreement    | \>=80% and kappa \>=0.60 per scored platform, subject to prevalence review                     |
| Parser accuracy                  | \>=95% on labelled recommendation/rank/entity sample before unattended scoring                 |
| Cycle completeness               | \>=90% for score publication                                                                   |
| Manual audit active time         | Measured; if one stage \>45 min/property, prioritise automation/vendor review                  |
| Management delivery gross margin | \>=65% minimum; 75% target after stabilization                                                 |
| Consumer Layer B manual load     | If combined evidence work reaches 8-10 hr/week, delegate/streamline before adding more clients |
| Dashboard demand                 | Build only after repeated client demand or clear retention/revenue evidence                    |

# 11. Next twenty actions

0\. DO IMMEDIATELY, NOT GATED ON WEEK 1: Create or migrate to a Gemini
authorization key. Standard keys are rejected from September 2026, which
lands inside Week 1-2 of this plan. This does not depend on the repo,
Supabase or secrets setup and should not wait for them.

1\. Mark Atlas Methodology v1.0 as Release Candidate, not frozen.

2\. Retire AVI from the tracker output and add Atlas Readiness Score.

3\. Update scoring tables for P2-P5 to the deterministic specification.

4\. Create the GitHub repository and commit methodology, operating
system, execution plan and decision register.

5\. Create the Atlas Visibility Supabase project with row-level security
and run-state tables.

6\. (Superseded by action 0, completed before Week 1.) Confirm the
Gemini authorization key is active before writing the Gemini adapter.

7\. Store all provider secrets in GitHub Actions secrets; enable 2FA
everywhere.

8\. Implement run planner, deterministic task IDs and cost ledger.

9\. Implement OpenAI web-search adapter and grounding-state capture.

10\. Implement Gemini Google Search adapter and grounding-state capture.

11\. Implement Perplexity Sonar adapter.

12\. Implement Anthropic web-search adapter with pause_turn handling.

13\. Implement hash + Evidence Vault manifest and test a round trip.

14\. Simulate an interrupted run and prove resume/reconciliation without
duplicates.

15\. Run System Zero and time every manual stage.

16\. Review System Zero defect log and clear P0 issues.

17\. Select one neighborhood hospitality calibration property only when
G1 has passed.

18\. Run calibration, including the separate Gemini Maps diagnostic.

19\. Freeze Methodology v1.0 only after G2 passes and generate the final
client methodology brief.

20\. Open no more than three \$400 founding pilot slots and continue
commercial outreach while automation replaces measured manual
bottlenecks.
