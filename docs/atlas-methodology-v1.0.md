A T L A S O P T I M I S A T I O N

Atlas Methodology v1.0

Measurement and scoring standard for AI recommendation visibility in
premium hospitality.

| **Document** | Atlas Methodology v1.0       |
|--------------|------------------------------|
| **Version**  | v1.0 Release Candidate       |
| **Status**   | PRE-CALIBRATION - NOT FROZEN |
| **Date**     | 25 August 2026               |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>STATUS NOTE</strong></p>
<p>This document supersedes the scoring and methodology sections of
Atlas Framework &amp; Action Plan v1.1. v1.0 becomes frozen only after
System Zero passes engineering QA and the first real hospitality
calibration property passes the calibration gate. Until then, every
weight, threshold and formula in this document is a release
candidate.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

**Be part of the answer.**

# Contents

**1.** Proposition, scope and claims boundary

**2.** Methodology governance and freeze gate

**3.** Official score model: AVS + Atlas Readiness Score

**4.** AI Visibility Score specification

**5.** Atlas Readiness Score specification

**6.** Statistical protocol

**7.** Prompt system and anti-manipulation

**8.** Measurement architecture and calibration

**9.** Evidence, comparability and reporting

**10.** Methodology change control

**A.** External platform references

# 1. Proposition, scope and claims boundary

Atlas Optimisation measures how often and how prominently a hospitality
business is recommended by AI assistants under controlled, repeatable
conditions. Atlas then improves the digital signals the business can
control and remeasures observed recommendation behaviour. The commercial
promise remains simple: be part of the answer.

- **Atlas may say:** We measure observed AI recommendation visibility
  under a versioned prompt set and show the evidence behind every
  reported score.

- **Atlas may say:** We identify and improve controllable signals that
  can influence discoverability, comprehension, trust and recommendation
  readiness.

- **Atlas may say:** We rerun the same measurement instrument and report
  meaningful movement, no meaningful movement, regression or
  inconclusive results.

- **Atlas must never say:** We control an AI platform, guarantee a
  ranking position, guarantee score improvement, or have privileged
  access to a platform ranking system.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>STANDARD CLIENT DISCLAIMER</strong></p>
<p>AI assistants are probabilistic systems. Outputs vary across runs,
models, markets, interfaces and updates. Atlas measures observed
behaviour under controlled conditions and improves the signals a
business controls. Atlas does not control any AI platform
output.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

# 2. Methodology governance and freeze gate

Business principles govern the methodology. The methodology governs the
data model. The data model governs technical architecture. Technical
convenience must never silently redefine measurement.

| **State**                | **Meaning**                                                                               | **Permitted use**                                        |
|--------------------------|-------------------------------------------------------------------------------------------|----------------------------------------------------------|
| v1.0 Release Candidate   | Formulas and protocols implemented but hospitality calibration not yet complete           | Internal engineering, System Zero, calibration only      |
| v1.0 Frozen              | Calibration gate passed; formulas, thresholds, eligible platforms and prompt rules locked | Client baselines and client-facing score claims          |
| v1.x Clarification       | No score changes                                                                          | Documentation, implementation detail, non-breaking fixes |
| v2.0+ Methodology change | Any change capable of altering a score                                                    | Applies forward only; never restates history             |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>HARD GATE</strong></p>
<p>System Zero is an engineering test of Atlas itself. It does not
validate hospitality scoring and it produces no publishable AVS or
Readiness claim. The first neighborhood hospitality calibration property
is the freeze gate for v1.0.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

# 3. Official score model: AVS + Atlas Readiness Score

Atlas reports two official numbers. They are intentionally separate so a
controllable readiness improvement can never be mistaken for an observed
recommendation outcome.

| **Metric**                  | **Question answered**                                                      | **Inputs**                                                     | **Client role**        |
|-----------------------------|----------------------------------------------------------------------------|----------------------------------------------------------------|------------------------|
| AI Visibility Score (AVS)   | Are AI assistants recommending this venue?                                 | Observed recommendation outputs only                           | Outcome                |
| Atlas Readiness Score (ARS) | How strong are the controllable signals that support future AI visibility? | Website, entity consistency, reputation, third-party authority | Controllable readiness |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>CHANGE FROM V1.1</strong></p>
<p>AVI is retired before Client #1. Atlas will not combine an observed
outcome and controllable levers into one headline composite. Historical
compatibility is unnecessary because no paying client baseline has yet
been issued. Recorded as Decision D-028 in the Atlas Decision Register,
formally retiring D-001 (dual-score AVI-composite + AVS model).</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 3.1 Atlas Readiness Score formula

The four readiness pillars keep their original relative weights. They
are normalised from 70 total points to a 0-100 readiness score:

| **Readiness pillar**                | **Original weight** | **Normalized ARS share** |
|-------------------------------------|---------------------|--------------------------|
| P2 Website and Structured Data      | 20                  | 28.57%                   |
| P3 Business Information Consistency | 15                  | 21.43%                   |
| P4 Reputation and Guest Sentiment   | 20                  | 28.57%                   |
| P5 Third-Party Authority            | 15                  | 21.43%                   |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>FORMULA</strong></p>
<p>ARS = (20 x P2 + 15 x P3 + 20 x P4 + 15 x P5) / 70</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **ARS band** | **Range** |
|--------------|-----------|
| Fragile      | 0-39      |
| Developing   | 40-59     |
| Established  | 60-74     |
| Strong       | 75-89     |
| Advanced     | 90-100    |

# 4. AI Visibility Score specification

## 4.1 Recommendation Position Value (RPV)

| **Observed outcome**                                | **RPV**                               |
|-----------------------------------------------------|---------------------------------------|
| Ranked first, or the single positive recommendation | 1.00                                  |
| Rank 2                                              | 0.80                                  |
| Rank 3                                              | 0.65                                  |
| Rank 4-5                                            | 0.45                                  |
| Rank 6-10                                           | 0.25                                  |
| Positive recommendation with no ordinal order       | 0.30                                  |
| Named only as a cited source, not recommended       | 0.00 - tracked as SOURCE_ONLY_MENTION |
| Absent                                              | 0.00                                  |
| Negative mention or counter-example                 | 0.00 - NEGATIVE_MENTION               |
| Wrong entity or name collision                      | Excluded - ENTITY_CONFLICT; feeds P3  |

Only positive recommendations contribute to AVS. Source-only citations
are valuable intelligence but are not recommendations, so they cannot
raise AVS.

## 4.2 Commercial intent weights

| **Tier** | **Intent**                                | **Weight** |
|----------|-------------------------------------------|------------|
| A        | Direct booking intent                     | 1.00       |
| B        | Comparative / alternatives                | 0.80       |
| C        | Amenity, experience or occasion discovery | 0.60       |
| D        | Branded or navigational                   | 0.30       |

Branded prompts remain capped at 15% of total prompt weight. Intent tier
is immutable for the life of a prompt version.

## 4.3 Platform aggregation

The standard AVS uses equal weighting across calibrated eligible
platforms. Equal weighting is intentionally preferred to unsupported
pseudo-precision about market share. Each platform is also reported
separately. A client-specific market-weighted view may be shown as a
secondary, clearly labelled analysis, but it never replaces the standard
AVS.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>FORMULA</strong></p>
<p>PVS(prompt, platform) = mean RPV across valid replicates<br />
PlatformScore = weighted mean of PVS by intent weight<br />
AVS = 100 x mean PlatformScore across eligible calibrated
platforms</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

Markets and languages are separate segments. A blended view is permitted
only when the blend rule is disclosed. Client-supplied revenue mix may
be used for a secondary blended view; otherwise the blend is unweighted
and labelled as such.

## 4.4 Visibility bands

| **Visibility band** | **AVS range** |
|---------------------|---------------|
| Not observed        | 0-9           |
| Detectable          | 10-24         |
| Emerging            | 25-44         |
| Established         | 45-64         |
| Strong              | 65-84         |
| Leading             | 85-100        |

# 5. Atlas Readiness Score specification

Every criterion is applicable, conditional or not applicable.
Applicability is locked before scoring. Only applicable criteria enter
the denominator. Every sub-score is deterministic and reproducible from
stored inputs.

## 5.1 P2 Website and Structured Data

| **Component**                     | **Weight within P2** | **Examples**                                                                                   |
|-----------------------------------|----------------------|------------------------------------------------------------------------------------------------|
| Retrieval and indexability        | 30%                  | HTTP status, noindex, robots, renderability, canonical access                                  |
| Structured data and entity markup | 20%                  | Hotel/Resort/Restaurant/LocalBusiness, Offer, geo, amenityFeature, AggregateRating where valid |
| Answerability                     | 25%                  | Machine-extractable canonical property facts                                                   |
| Architecture and semantics        | 15%                  | Sitemap, internal links, crawl depth, hreflang, headings, breadcrumbs                          |
| Performance and accessibility     | 10%                  | Page speed, mobile usability, image alt coverage, semantic accessibility                       |

Each component is the weighted mean of its applicable binary or banded
criteria. A criterion must have a written scoring rule in the criterion
registry before it can affect a score.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>CRITICAL CAP</strong></p>
<p>A site-wide noindex, sustained 4xx/5xx failure on booking-critical
pages, or content that cannot be retrieved or rendered by the
measurement crawler caps P2 at 20 until resolved. Individual AI crawler
controls do not automatically cap the whole pillar.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

### 5.1.1 Crawler Access Matrix

| **Crawler / control** | **Purpose**                                                                         | **Visibility treatment**                                            |
|-----------------------|-------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| Googlebot             | Google Search discovery and indexing                                                | Critical if booking-critical content is blocked from Google Search  |
| OAI-SearchBot         | ChatGPT search discovery and citation                                               | High-priority visibility lever                                      |
| PerplexityBot         | Perplexity search surfacing and linking                                             | High-priority visibility lever                                      |
| Claude-SearchBot      | Claude search indexing / search relevance                                           | High-priority visibility lever                                      |
| Claude-User           | User-directed retrieval in Claude                                                   | High-priority retrieval check                                       |
| Google-Extended       | Controls certain Gemini model use and grounding; not a Google Search ranking signal | Conditional AI-use lever; never described as Google Search indexing |
| Bingbot               | Bing search discovery                                                               | Secondary search discoverability lever                              |
| GPTBot                | OpenAI model-training control                                                       | Policy choice only; no visibility score penalty                     |
| ClaudeBot             | Anthropic model-training control                                                    | Policy choice only; no visibility score penalty                     |
| Perplexity-User       | User-initiated fetcher; behaviour differs from crawler indexing                     | Observation / WAF accessibility check, not robots score             |

## 5.2 P3 Business Information Consistency

| **Field**            | **Weight** |
|----------------------|------------|
| Name                 | 20         |
| Address              | 15         |
| Coordinates          | 10         |
| Phone                | 10         |
| Website              | 10         |
| Booking URL          | 10         |
| Category             | 8          |
| Hours                | 7          |
| Check-in / check-out | 5          |
| Amenities            | 3          |
| Social profiles      | 2          |

| **Status**                                          | **Factor** |
|-----------------------------------------------------|------------|
| Exact                                               | 1.00       |
| Equivalent                                          | 0.90       |
| Stale or unverified beyond defined freshness window | 0.60       |
| Missing                                             | 0.30       |
| Conflict                                            | 0.00       |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>FORMULA</strong></p>
<p>FieldScore(f) = mean status factor across applicable sources<br />
P3 = sum(FieldWeight x FieldScore) / 100</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

Core and conditional sources are equal-weighted when applicable. Atlas
does not introduce source-weight assumptions until the Source Influence
Graph has enough empirical data to justify a later methodology version.

## 5.3 P4 Reputation and Guest Sentiment

| **Component**             | **Weight** | **Deterministic rule**                                                                                     |
|---------------------------|------------|------------------------------------------------------------------------------------------------------------|
| Rating quality            | 20%        | Normalize rating to platform maximum; score from 0 at 70% of max to 1 at 95% of max, clamped               |
| Peer-normalised volume    | 15%        | min(1, log(1+reviews) / log(1+peer median reviews))                                                        |
| Recency                   | 15%        | 0.5^(median review age in months / 24)                                                                     |
| Velocity                  | 10%        | min(1, trailing 6-month monthly rate / trailing 24-month monthly rate); conditional if history unavailable |
| Management response rate  | 10%        | Responded reviews / review sample                                                                          |
| Response latency          | 5%         | \<24h=1.0; \<72h=0.7; \<7d=0.4; otherwise 0                                                                |
| Sentiment quality         | 15%        | (positive + 0.5 x neutral) / classified reviews                                                            |
| Experience-theme coverage | 5%         | Applicable positive experience themes evidenced / applicable target themes                                 |
| Platform diversity        | 5%         | Applicable review platforms with current usable signal / applicable review platforms                       |

Peer sets are locked before scoring. Target is 8-12 comparable
properties in the same profile, destination and positioning band.
Fallback expansion rules are documented when the local peer set is too
small. Low-confidence review classifications route to human verification
and never directly assign a score.

## 5.4 P5 Third-Party Authority

| **Tier** | **Definition**                                                                  | **Base** |
|----------|---------------------------------------------------------------------------------|----------|
| T1       | National / international editorial, major awards, national tourism authorities  | 1.00     |
| T2       | Specialist travel media, established curated guides, significant regional press | 0.70     |
| T3       | Credible local publications, niche editorial, industry associations             | 0.40     |
| T4       | Directories, aggregators, syndicated listings                                   | 0.10     |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>FORMULA</strong></p>
<p>MentionValue = TierBase x Recency x Independence x Relevance x
AICitation<br />
Recency = 0.5^(age in months / 36)<br />
Independence = 1.0 editorial, 0.3 disclosed sponsored, 0 owned<br />
Relevance = 1.0 destination/category relevant, 0.5 general<br />
AICitation = 1.5 when the source is observed cited in client or peer
recommendation runs, otherwise 1.0</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

Final P5 combines peer-normalised authority depth and source breadth:

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>P5 NORMALIZATION</strong></p>
<p>Depth = min(1, total MentionValue / max(peer median MentionValue,
1.0))<br />
Breadth = min(1, distinct qualifying domains / max(peer median
qualifying domains, 1))<br />
P5 = 100 x (0.80 x Depth + 0.20 x Breadth)</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

# 6. Statistical protocol

## 6.1 Replication and measurement roles

| **Run type**                      | **Replicates**                 | **Purpose**                                          | **May support movement verdict?** |
|-----------------------------------|--------------------------------|------------------------------------------------------|-----------------------------------|
| Frozen Core baseline / validation | n=5 per prompt-platform-market | Official AVS instrument                              | Yes                               |
| Weekly Sentinel                   | n=1                            | Anomaly and direction monitoring only                | No                                |
| Monthly Benchmark                 | n=1                            | Broader diagnostic visibility and competitor context | No                                |
| Quarterly Benchmark validation    | n=3                            | Generalization and market learning                   | Secondary evidence                |
| Discovery Set                     | Sampled quarterly              | Prompt and market discovery                          | No                                |

Frozen Core replicates use fresh sessions, no conversation history and a
minimum six-hour run window. Failed technical calls are never scored as
zero.

## 6.2 Confidence interval and paired change

Atlas uses a hierarchical paired bootstrap with 10,000 resamples. Prompt
IDs are resampled within intent tier; all eligible platforms are
retained; replicate observations are resampled within each
prompt-platform cell. Baseline and validation are recomputed within the
same bootstrap draw, producing a distribution of the paired AVS change.
The 2.5th and 97.5th percentiles form the 95% interval for delta.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>STATISTICAL CHANGE</strong></p>
<p>The previous term Minimum Detectable Change is replaced by Minimum
Reportable Change (MRC). MRC is fixed at 5.0 AVS points for v1.0 RC. It
is a reporting threshold, not a power-analysis claim. Atlas may estimate
an empirical detection threshold after enough real cycles
exist.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **Verdict**            | **Condition**                                                              |
|------------------------|----------------------------------------------------------------------------|
| Improvement            | Delta \>= +5.0 and 95% CI for paired delta excludes 0 on the positive side |
| Regression             | Delta \<= -5.0 and 95% CI for paired delta excludes 0 on the negative side |
| No meaningful movement | Absolute delta \< 5.0                                                      |
| Inconclusive           | Absolute delta \>= 5.0 but paired-delta CI includes 0                      |
| Incomplete             | Observation completeness \<90%; no movement verdict issued                 |

Mention Rate and Top-3 Rate use Wilson intervals. Recommendation
Stability remains the mean proportion of replicates matching the modal
RPV bucket. Stability below 0.60 is explicitly flagged as volatile.

## 6.3 External drift and causal language

- **Competitor panel:** Competitors are scored from the same response
  set. Broad movement across the panel is evidence of platform or market
  drift.

- **Provider change log:** Model ID, tool version, grounding behaviour
  and known provider changes are stamped on each run.

- **Hold-out prompts:** Twenty percent of the Benchmark Set is excluded
  from optimization targeting. Hold-out movement tests generalization;
  it is not treated as a pure external-drift control because site-wide
  interventions can affect it.

- **Staggered intervention:** Where operationally possible, Atlas
  sequences remediation batches. This supports attribution but never
  proves causation.

- **Optional local control property:** For high-value engagements, Atlas
  may track a non-client comparable property as an additional
  descriptive drift control without optimizing it.

# 7. Prompt system and anti-manipulation

| **Set**     | **Typical count** | **Change rule**                           | **Purpose**                                         |
|-------------|-------------------|-------------------------------------------|-----------------------------------------------------|
| Frozen Core | 8-12 by profile   | Immutable between baseline and validation | Official AVS instrument                             |
| Sentinel    | 4-6               | Can change only between monthly cycles    | Cheap weekly anomaly monitoring                     |
| Benchmark   | 16-24             | Versioned; 20% hold-out                   | Broader commercial-intent visibility                |
| Discovery   | 8-12 quarterly    | Dynamic                                   | Learn new prompts, competitors and traveller intent |

- A prompt returning the client in more than 90% of baseline replicates
  across every eligible platform is flagged non-diagnostic.

- The client entity is never part of its own competitor set.

- Prompt changes that alter intent, entity, tier or set membership
  require a major prompt-set version and a new baseline.

- Prompt text, intent family, tier, market and language are stored
  permanently with every observation.

# 8. Measurement architecture and calibration

## 8.1 Layer A: controlled API benchmark

| **Provider**     | **v1.0 RC adapter**                             | **Grounding verification**                                        |
|------------------|-------------------------------------------------|-------------------------------------------------------------------|
| OpenAI           | Responses API + web_search                      | Require observed web_search_call or equivalent source record      |
| Google Gemini    | Interactions API + google_search                | Require observed search call/result or grounding annotations      |
| Perplexity       | Sonar API, not raw Search API                   | Use search_results as canonical source record                     |
| Anthropic Claude | Messages API + supported web_search server tool | Require server tool use / search result blocks; handle pause_turn |

Every observation stores search_available, search_invoked,
citations_present and grounding_status. Where the provider allows
forcing a search tool, Atlas does so. Otherwise, an ungrounded response
is retried once with explicit current-web instruction. If grounding
still does not occur, the observation is marked ineligible rather than
silently scored as grounded.

## 8.2 Gemini Local Discovery / Maps Diagnostic

Gemini Grounding with Google Maps is run as an experimental hospitality
diagnostic during calibration. It is location-aware and can return
Maps-grounded place recommendations. It does not enter AVS or ARS in
v1.0. The diagnostic stores prompt, coordinates or locality, place IDs,
returned sources, recommendation position and cost. Promotion into a
scored metric would require a later methodology version and a separate
calibration decision.

## 8.3 Layer B: consumer-surface evidence

Atlas does not automate consumer chat interfaces by default.
Consumer-surface checks are human-initiated unless a provider explicitly
offers an approved automation path for that surface. This avoids relying
on brittle browser automation and reduces terms, account and
reproducibility risk.

| **Stage**                        | **Consumer evidence cadence**                                                                                                 |
|----------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| One-time hospitality calibration | Full Frozen Core on relevant consumer surfaces, repeated enough to estimate agreement; target n=3 when operationally feasible |
| Client baseline                  | Full or representative Frozen Core capture                                                                                    |
| Monthly management               | 3-5 sentinel prompts per relevant surface                                                                                     |
| Validation / quarterly           | Full Frozen Core capture                                                                                                      |

## 8.4 Calibration gate

- System Zero passes all engineering acceptance criteria first, but does
  not count toward methodology calibration.

- Atlas then selects one real neighborhood hospitality property with a
  live website, Google Business Profile, review presence and public
  third-party references.

- Per platform, API benchmark results are compared with consumer-surface
  results on recommendation presence and rank behaviour.

- Platform eligibility requires raw mention agreement \>=80% and Cohen
  kappa \>=0.60. If prevalence makes kappa unstable, \>=85% raw
  agreement plus documented manual review is required.

- Rank agreement is reported with Spearman correlation where at least 10
  co-mentioned observations exist; rho \>=0.50 is the working acceptance
  threshold. If sample size is lower, rank agreement is descriptive and
  cannot rescue a failed mention-agreement gate.

- A platform failing the gate remains evidence-only and is excluded from
  AVS. Eligible platform weights are equal.

- v1.0 is frozen only after P0 defects are resolved or formally owned,
  the score engine reproduces hand calculations, and calibration results
  are documented.

# 9. Evidence, comparability and reporting

- Every score row carries score-model version, prompt-set version,
  market, language, run ID, completeness, interval bounds and evidence
  references.

- Raw provider responses, source records and hashes are stored under the
  evidence policy defined in the Atlas Operating System.

- No number appears in a client deliverable without a measurement date.
  Outcome metrics include confidence or stability information where
  applicable.

- Historical scores are never recalculated under a later methodology.
  Cross-version comparisons are clearly marked as non-like-for-like
  unless a bridge study exists.

- Share of Voice uses positive recommended venues only: sum RPV(client)
  / sum RPV(all positively recommended venues). Source-only citations do
  not enter the denominator.

# 10. Methodology change control

| **Change**                                                                                           | **Version treatment**                     |
|------------------------------------------------------------------------------------------------------|-------------------------------------------|
| Copy edit, clearer wording, implementation note with no score effect                                 | Patch / clarification                     |
| Provider model swap, platform eligibility change, prompt intent change                               | New baseline and methodology decision     |
| Any formula, criterion weight, RPV, threshold, platform weighting or statistical verdict rule change | Major methodology version                 |
| New experimental diagnostic kept outside scores                                                      | Operating update; no score version change |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>GOVERNING RULE</strong></p>
<p>No methodology version becomes client-active from convenience,
provider deprecation or software limitation alone. The decision is
documented first, then implemented, then applied forward.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

# External platform references

Platform references (OpenAI, Google/Gemini, Perplexity, Anthropic,
GitHub Actions) are maintained in a single shared file, Atlas External
Platform References, to avoid duplicated appendices drifting out of sync
across documents. See that file for the current list, verification dates
and rechecking rule. It must be rechecked before any methodology version
is frozen or a provider adapter is materially changed.
