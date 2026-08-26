# Atlas Decision Register

Append-only. A decision is added here, never edited after acceptance — a
reversal is a new decision that references the one it retires. Execution
Plan Technical Lane step 1 requires this file exist in the repo alongside
the methodology, operating system and execution plan.

Per Methodology §10 (Governing Rule): "No methodology version becomes
client-active from convenience, provider deprecation or software limitation
alone. The decision is documented first, then implemented, then applied
forward." Every row below is a decision that was documented before it was
implemented.

| ID | Date | Decision | Status |
|---|---|---|---|
| D-001 | pre-v1.0 | Dual-score AVI-composite + AVS model | **Retired** by D-028 |
| D-028 | 2026-08-25 | Retire AVI before Client #1. Atlas will not combine an observed outcome and controllable levers into one headline composite. Official client metrics are AVS (outcome) and Atlas Readiness Score (controllable), reported separately. Historical compatibility with AVI is unnecessary because no paying client baseline had yet been issued. | Active |
| D-029 | 2026-08-26 | Gemini adapter authenticates via Vertex AI (service account / Application Default Credentials, standard Cloud Billing) instead of an AI Studio authorization key. Reason: AI Studio's prepay-credits billing UI hit an unresolvable bug blocking G0 closure. Forced consequence, not a separate choice: the Interactions API is Gemini Developer API only and is not available on Vertex AI at time of writing, so the adapter's grounding call was also rewritten from `client.interactions.create` to `client.models.generate_content` with the `google_search` tool — the standard endpoint Vertex AI does support today. The grounding contract (grounded vs excluded, retry-once-with-explicit-instruction, Methodology §8.1) is unchanged; only the API surface implementing it changed. Grounding unit cost in `config.py` is carried forward provisionally and flagged for reconfirmation against the live Vertex AI billing export. | Active |

## Adding a decision

1. Determine version treatment per Methodology §10 (patch/clarification vs.
   new baseline vs. major methodology version vs. operating update).
2. Add a row here with the next sequential D-xxx ID, dated, with the
   decision stated in full — not just a reference to a discussion.
3. Only then implement it in the methodology/operating docs and/or code.
4. If the decision retires an earlier one, mark the earlier row's Status
   column "Retired by D-xxx" — do not delete or rewrite it.
