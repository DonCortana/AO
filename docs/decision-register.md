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

## Adding a decision

1. Determine version treatment per Methodology §10 (patch/clarification vs.
   new baseline vs. major methodology version vs. operating update).
2. Add a row here with the next sequential D-xxx ID, dated, with the
   decision stated in full — not just a reference to a discussion.
3. Only then implement it in the methodology/operating docs and/or code.
4. If the decision retires an earlier one, mark the earlier row's Status
   column "Retired by D-xxx" — do not delete or rewrite it.
