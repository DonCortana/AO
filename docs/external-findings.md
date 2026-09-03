# Atlas External Findings

Append-only. F-series, mirroring the D-series in `docs/decision-register.md`.
Facts about venues, markets and the outside world that Atlas relies on but did
not produce. Distinct from `docs/atlas-external-platform-references-v1.0.md`,
which is scoped to provider and platform documentation cited by the Methodology
and Operating System appendices.

Nothing enters this file without a source URL. Every row carries provenance:

- **Primary** — the venue's or authority's own surface.
- **Reported** — a third party asserting it.
- **Derived** — a conclusion drawn from other rows in this file.

Updating is a ritual, not automation. Rows are drafted, verified against source
and committed manually. Claude Code does not write this file: commits to main
are bucket two under `docs/CODE-AUTONOMY-BOUNDARIES.md` v1.2, and a document
whose entire value is that every line is source-backed cannot admit unverified
model claims.

### F-001 — Koh Samui air arrivals volume
Date: 2026-09-03 | Subject: market/koh-samui | Provenance: Reported
1,127,832 air passengers Jan–Apr 2025 (+9% YoY); 2.78M full-year 2024
(+21%), above 2019 levels. No nationality split published at island level.
Source: travelandtourworld.com (Jun 2025) | Linked: D-088

### F-002 — Samujana Michelin Keys status
Date: 2026-09-03 | Subject: venue/samujana | Provenance: Primary
Presents as the only Three MICHELIN Keys hotel on Koh Samui.
Source: samujana.com/white-lotus/ | Linked: ARS authority-source list

### F-003 — White Lotus S3 visibility halo
Date: 2026-09-03 | Subject: venue/samujana | Provenance: Primary + Reported
Samujana was an HBO White Lotus S3 filming location and runs dedicated
pages on it. A one-off media event driving atypical AI visibility.
Calibration AVS from this venue is not representative of a baseline
luxury-villa AVS, and its ARS picture is contaminated by coverage the
property did not create — no ARS-to-AVS relationship observed here is
transferable.
Source: samujana.com/white-lotus-filming-locations/; cnn.com (Apr 2025)
Linked: D-095

### F-004 — Conflicting public fact, villa identity
Date: 2026-09-03 | Subject: venue/samujana | Provenance: Reported
Samujana's own journal names Villa 27 (Villa Jacinta) as the White Lotus
location; CNN and multiple villa agents name Villa 12. Live ARS test case:
conflicting public facts about a single property.
Source: samujana.com/white-lotus-filming-locations/; cnn.com (Apr 2025)
