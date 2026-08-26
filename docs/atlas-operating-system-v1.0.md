A T L A S O P T I M I S A T I O N

Atlas Operating System

How Atlas is delivered reliably, securely and at low operating cost.

| **Document** | Atlas Operating System      |
|--------------|-----------------------------|
| **Version**  | v1.0                        |
| **Status**   | INTERNAL OPERATING STANDARD |
| **Date**     | 25 August 2026              |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>STATUS NOTE</strong></p>
<p>This document contains delivery architecture and operating rules. It
may evolve without changing the Atlas scoring methodology, provided no
implementation change alters a score or movement verdict.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

**Be part of the answer.**

# Contents

**1.** Operating principles

**2.** Client lifecycle and ownership

**3.** System architecture

**4.** Resumable measurement pipeline

**5.** Provider adapter standard

**6.** Data architecture and privacy boundaries

**7.** Evidence Vault and retention

**8.** Human-in-the-loop design

**9.** Cadence and workload control

**10.** Unit economics and budget rails

**11.** QA, reliability and incident handling

**12.** Client deliverable production

**13.** Scale milestones

**A.** External platform references

# 1. Operating principles

- Nothing production-critical depends on the founder workstation being
  online.

- Automate repeatable collection, calculation, validation and report
  assembly before automating judgment-heavy client decisions.

- The database is the source of truth for run state. Schedulers are
  disposable triggers, not the record of whether work happened.

- A technical failure never becomes a zero score.

- Client-visible changes require human approval. Measurement and
  monitoring may run unattended.

- Visibility research data and guest-operational data are separated by
  design.

- Fully loaded human delivery cost is tracked from System Zero onward,
  not reconstructed after scale.

# 2. Client lifecycle and ownership

| **Stage**  | **Exit criterion**                                                                               | **Primary owner**          |
|------------|--------------------------------------------------------------------------------------------------|----------------------------|
| DISCOVER   | Profile, market/language, applicability, prompt version, peers, competitors and access confirmed | Atlas + client             |
| DIAGNOSE   | Required engines complete with \>=90% completeness                                               | Atlas                      |
| PRIORITISE | Every issue has impact, confidence, effort, owner and target date                                | Atlas + client             |
| OPTIMISE   | Approved P0/P1 work implemented or deferred in writing                                           | Atlas / client / developer |
| VALIDATE   | Eligible frozen measurement completed; attribution window stated                                 | Atlas                      |
| EVIDENCE   | Every client claim linked to evidence record                                                     | Atlas                      |
| MAINTAIN   | Report accepted; next cycle and owners agreed                                                    | Atlas + client             |

A signed RACI annex fixes who can implement schema, listings, content,
booking-flow work, PMS changes and approvals. Atlas never assumes
publication rights by default.

# 3. System architecture

| **Layer**              | **MVP**                                                  | **Later trigger**                                                        |
|------------------------|----------------------------------------------------------|--------------------------------------------------------------------------|
| Language               | Python 3.12                                              | TypeScript only when client web application is justified                 |
| Scheduler              | GitHub Actions                                           | Move scheduling when volume/reliability requires; not because of fashion |
| Database               | Supabase managed Postgres with row-level security        | Read replicas / dedicated services at scale                              |
| Evidence storage       | Google Drive for early non-PII evidence + manifests      | Object storage with lifecycle                                            |
| Client output          | Branded Word/PDF + tracker export                        | Read-only web report after repeated client demand                        |
| Observability          | Structured JSON logs, run reconciliation, cost ledger    | Sentry / tracing once operational value exceeds setup cost               |
| Crawling               | httpx/selectolax; Playwright only for JS-dependent pages | Managed crawling only where cheaper than maintaining edge cases          |
| Hospitality Automation | Separate n8n instance and separate data store            | Dedicated integration services as product scales                         |

The Acer workstation is development-only: WSL2 Ubuntu, Docker Desktop,
Python, Git and VS Code. Local small models may be used for parser
experiments and low-risk classification tests, but production
measurements run in managed infrastructure.

# 4. Resumable measurement pipeline

GitHub Actions remains the MVP scheduler, but every run is stateful and
resumable because scheduled Actions may be delayed or dropped under
load.

| **State**  | **Behaviour**                                                      |
|------------|--------------------------------------------------------------------|
| planned    | Run definition created in database before execution                |
| queued     | Observation tasks created with deterministic task IDs              |
| running    | Worker has lease / started timestamp                               |
| complete   | Observation and evidence record stored                             |
| retryable  | Transient failure; retry count \<3                                 |
| failed     | Retry ceiling reached; human or provider incident required         |
| excluded   | Invalid entity, unsupported grounding or methodology exclusion     |
| reconciled | Expected vs completed observations checked; missing tasks requeued |

- Task IDs are idempotent: rerunning a completed task cannot duplicate
  the scored observation.

- A reconciliation job compares expected observation count with
  completed eligible records after each batch and again the following
  morning.

- Cron schedules avoid the start of the hour. Use a non-round minute
  such as :17 or :37.

- A manual workflow_dispatch recovery path exists for every scheduled
  workflow.

- The run manifest is generated only after reconciliation, not simply
  after the scheduler job exits.

# 5. Provider adapter standard

| **Required field group** | **Minimum data**                                                                                      |
|--------------------------|-------------------------------------------------------------------------------------------------------|
| Identity                 | provider, model, model snapshot/version, tool version                                                 |
| Prompt                   | prompt ID/version/text, market, language, intent tier, set                                            |
| Grounding                | search_available, search_invoked, grounding_status, source/search result records                      |
| Outcome                  | raw response, parsed recommendations, rank, source-only mentions, negative mentions, entity conflicts |
| Execution                | request time, completion time, latency, retry number, status, error code                              |
| Cost                     | input tokens, output tokens, search/tool use units, provider-reported or computed cost                |
| Evidence                 | evidence ID, payload hash, manifest ID                                                                |

- OpenAI adapter uses Responses API with web_search and verifies an
  actual web search call occurred.

- Gemini adapter uses current Interactions API with google_search; use
  an authorization key, not a standard key.

- Perplexity adapter uses Sonar for web-grounded answers. search_results
  is the canonical source list.

- Anthropic adapter uses Messages API with a supported web_search server
  tool and handles pause_turn / continuation behaviour.

- Model or response-shape deprecation is isolated inside the adapter and
  triggers a methodology compatibility review. Silent model swaps are
  prohibited.

# 6. Data architecture and privacy boundaries

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>SECURITY BOUNDARY</strong></p>
<p>Hospitality Automation and Atlas Visibility must not share a raw
guest-data store. They may share stable client/property identifiers and
approved aggregate signals only.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **Environment**             | **May contain**                                                                                          | **Must not contain**                                                                                                |
|-----------------------------|----------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| Atlas Visibility / Research | Business facts, prompts, public deidentified review text, AI responses, citations, scores, issue records | Guest messages, guest phone numbers, booking conversations, complaint transcripts, unnecessary personal identifiers |
| Hospitality Automation      | Guest enquiries, WhatsApp/email content, booking/PMS data required for contracted workflow               | Atlas research corpus by default; cross-client intelligence; unrelated research data                                |

- Use separate Supabase projects or equivalently strong logical
  isolation, separate n8n credentials, separate secret scopes and
  separate retention policies.

- OAuth or manager invitations are preferred. Atlas does not request or
  store reusable client passwords where delegated access exists.

- Row-level security is enabled from the first migration. Service roles
  are narrowly scoped.

- Roles under GDPR are assessed per processing activity. The Operating
  System does not assume Atlas is always controller or always processor.

- A Records of Processing Activities register, sub-processor register,
  DPA templates and legitimate-interest assessment are maintained before
  paid operations where applicable.

# 7. Evidence Vault and retention

Measurement evidence and personal data have different immutability
rules. The old rule of no deletes ever is retained for non-personal
score-bearing observations, but it is not allowed to override lawful
deletion or retention obligations.

| **Data class**                          | **Retention / handling**                                                                                  |
|-----------------------------------------|-----------------------------------------------------------------------------------------------------------|
| Score-bearing non-personal observations | Append-only; versioned; retained for reproducibility under documented business retention                  |
| Raw AI responses and screenshots        | Default 36 months unless contract or policy requires shorter; PII scrubbed where feasible                 |
| Public review text                      | Deidentified at ingest; raw third-party content retained only where platform terms and legal basis permit |
| Guest / personal operational data       | Lives in Automation environment; contract-specific retention; deletable                                   |
| Deleted personal-data evidence          | Hash/tombstone may remain only if it cannot reconstruct the deleted personal data and legal basis permits |

Every evidence record carries evidence ID, run ID, prompt version,
provider/model/tool version, market, language, UTC timestamp, source
reference, payload hash and operator where human capture is used.

# 8. Human-in-the-loop design

| **Automate**                                                                                                               | **Require human approval**                                                                                                                                                     |
|----------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| API runs, crawl checks, source capture, deterministic scoring, classification drafts, anomaly detection, report population | Any public listing change, published content, every review response, PR/outreach, price/offer change, complaint handling, booking-flow change, deletion/merging of client data |

## 8.1 Manual workload measurement

- Every manual workflow logs operator, start time, end time, property,
  stage and exception reason.

- The top three manual time sinks are reviewed monthly. Automation
  priority is driven by measured hours saved, not perceived technical
  elegance.

- Review ingestion and consumer-surface evidence capture are first
  candidates for analyst/VA delegation when their combined load reaches
  roughly 8-10 hours per week.

# 9. Cadence and workload control

| **Frequency** | **Activity**                                                                                                                                                  |
|---------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Daily         | Automated health, failed-run alerting and material entity/listing anomaly alerts only                                                                         |
| Weekly        | 4-6 prompt Sentinel Set at n=1; Search Console movement; delta crawl; exception review                                                                        |
| Monthly       | Frozen Core n=5 when measurement window is eligible; Benchmark n=1; full readiness refresh; report; 3-5 consumer-surface sentinel checks per relevant surface |
| Quarterly     | Benchmark n=3; full consumer Frozen Core; competitor/peer review; Discovery Set; platform/tool review; methodology watch                                      |

AVS may be observed monthly, but an Improvement/Regression verdict is
issued only when the validation timing and intervention window support
the claim. Continuous implementation is not allowed to create false
attribution precision.

# 10. Unit economics and budget rails

API spend is only one component of delivery cost. Atlas tracks fully
loaded cost from System Zero onward.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>UNIT ECONOMICS FORMULA</strong></p>
<p>Fully Loaded Delivery Cost = API/search costs + infrastructure
allocation + founder delivery hours x internal shadow rate + analyst
hours x internal cost + contractor cost + evidence capture + review
ingestion + report QA + support/admin allocation</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **Metric**              | **Rule**                                                                                                   |
|-------------------------|------------------------------------------------------------------------------------------------------------|
| Delivery gross margin   | (Revenue - fully loaded direct delivery cost) / Revenue                                                    |
| Management minimum gate | Do not scale a monthly package below 65% delivery gross margin; target 75% after stabilization             |
| \$900 management test   | \$900 works only if fully loaded monthly delivery cost is \<=\$315 for 65% margin; \<=\$225 for 75% target |
| Founding \$400 audit    | Invite-only pilot price for maximum 3 profiles. Never used as public reference price                       |
| Standard audit price    | Set after System Zero + first hospitality calibration time logs. Price to margin and value, not API cost   |
| Budget rail             | Per-client monthly budget checked before each batch; alert at 80%, stop non-critical work at 100%          |

- Model routing uses the cheapest capable model for non-score
  classification and extraction.

- Replicates are never served from cache. Sentinel and non-replicate
  diagnostic calls may use cache only when that does not distort
  measurement.

- Incremental crawling is the default: delta weekly, full crawl monthly
  or when change detection requires it.

- Provider pricing is stored as versioned configuration and revalidated
  at build time and quarterly.

# 11. QA, reliability and incident handling

- Cycle completeness below 90% produces Incomplete status and no
  movement verdict.

- Parser confidence below 0.80 routes to human verification. First 10
  clients sample at least 10% of parsed observations; later sampling may
  drop to 3% only after accuracy is demonstrated.

- Parser release acceptance target is \>=95% exact
  recommendation/rank/entity accuracy on a hand-labelled test set.

- Review-theme classifier release target is \>=90% on a stratified
  human-labelled sample before unattended use.

- Every provider incident, methodology exception and manual override is
  written to the audit log.

- Backups and export restoration are tested before Client \#1 and
  quarterly after launch.

# 12. Client deliverable production

- Reports are generated from database facts, not manually retyped.

- Page one contains AVS, ARS, measurement date, market/language,
  movement verdict and a short plain-language interpretation.

- Every score-bearing statement links to an evidence ID or report
  appendix entry.

- The client-facing report omits internal provider implementation detail
  unless the buyer requests the technical appendix.

- A polished Word and PDF report remains the primary deliverable until
  repeated client demand justifies a dashboard.

# 13. Scale milestones

| **Scale**          | **Operating change**                                                                                  |
|--------------------|-------------------------------------------------------------------------------------------------------|
| 1-3 pilot profiles | Founder-led, deliberately assisted manual parsing; time every stage; prove delivery before automating |
| 4-10 clients       | Automate report assembly and routine collection; preserve judgment and QA                             |
| ~15 clients        | Analyst/VA enters when measured manual workload threshold is reached                                  |
| ~25 clients        | Collection mostly automated; review vendor decision; read-only client portal only if demanded         |
| ~100 clients       | Worker queue, portfolio view, second operator, stronger observability                                 |
| 500+               | Productized self-serve audit only after support, data and methodology economics prove viable          |

# External platform references

Platform references (OpenAI, Google/Gemini, Perplexity, Anthropic,
GitHub Actions) are maintained in a single shared file, Atlas External
Platform References, to avoid duplicated appendices drifting out of sync
across documents. See that file for the current list, verification dates
and rechecking rule. It must be rechecked before any methodology version
is frozen or a provider adapter is materially changed.
