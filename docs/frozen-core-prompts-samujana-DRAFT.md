# Frozen Core prompt set — Samujana — DRAFT

**Status: DRAFT. Not written to `prompt_versions`. Not versioned. Not frozen.**

Fresh draft — there was no prior draft set in the repo, in git history or in
Drive, so nothing here is a revision of earlier text. D-051's "Replaces draft
set (Six Senses, Cape Fahn, Conrad, W, Four Seasons)" describes a set that
does not exist as an artifact; this file is written to D-051's *constraint*
(Villa/Estate peers, not luxury-resort brands) rather than as a diff against it.

| | |
|---|---|
| Property | Samujana — `df2e65c5-190c-4879-88b4-78557176ef4e` |
| Category | Villa/Estate (free text — see profile-registry gap) |
| Market / language | TH / en — `markets.id = 2d4854b9-5589-44a5-886b-c895e99c7b95` |
| Set type | `frozen_core` |
| Version | `frozen-core-samujana-v1` |
| Prompt count | 10 (§7 allows 8–12 by profile) |
| Competitor set | D-051 |

Once agreed, each row below becomes one `prompt_versions` row:
`set_type='frozen_core'`, `version=<agreed>`, `prompt_text`, `intent_tier`,
`market_id` as above, `is_holdout=false`.

**§7 immutability:** the moment a baseline runs against these, intent, entity,
tier and set membership are locked — changing any of them requires a major
prompt-set version and a new baseline. Edit freely now; not after.

---

## D-051 competitor set

Used verbatim, exactly these five, no others:

1. Kerem Villas
2. Sukkho Samui Estates
3. Magic Suites – Samui Luxury Private Pool Villas
4. Sandalwood Luxury Villas
5. Horizon Villas Samui

**§7: "The client entity is never part of its own competitor set."** Applied
here in the stronger operational form — *no competitor-naming prompt names
Samujana*. Naming the client in a prompt guarantees a mention and destroys the
AVS signal, which measures whether the client surfaces **unprompted**. The one
exception is D1, where naming the client is the entire point of a branded prompt.

---

## Tier A — Direct booking intent (weight 1.00)

### A1 · open-discovery
> Which luxury private pool villas in Koh Samui should I book for a week's stay? I'm looking for somewhere with a dedicated villa team and sea views.

### A2 · open-discovery
> I need to book a private villa in Koh Samui for a family of eight — at least four bedrooms, a private pool, and staff on site. What are the best options?

### A3 · **names competitor** (Kerem Villas)
> I'm about to book Kerem Villas in Koh Samui for a five-night stay. Before I confirm, which comparable luxury villa estates on the island should I be looking at?

---

## Tier B — Comparative / alternatives (weight 0.80)

### B1 · **names competitors** (Sukkho Samui Estates, Sandalwood Luxury Villas, Horizon Villas Samui)
> How do Sukkho Samui Estates, Sandalwood Luxury Villas and Horizon Villas Samui compare for a luxury villa stay in Koh Samui, and are there other estates on the island that outclass them?

### B2 · **names competitors** (Magic Suites – Samui Luxury Private Pool Villas, Horizon Villas Samui)
> I've shortlisted Magic Suites – Samui Luxury Private Pool Villas and Horizon Villas Samui. What are the strongest alternatives in the same price bracket on Koh Samui?

### B3 · open-discovery
> What are the best luxury villa estates in Koh Samui, ranked, and what distinguishes the top three from each other?

---

## Tier C — Amenity, experience or occasion discovery (weight 0.60)

### C1 · open-discovery
> Which villas in Koh Samui have the best infinity pools and sunset views over the Gulf of Thailand?

### C2 · open-discovery
> Where should I stay in Koh Samui for a multi-family villa wedding — somewhere that can host around 40 guests on site?

### C3 · **names competitor** (Sandalwood Luxury Villas)
> Sandalwood Luxury Villas is often recommended for private-chef and in-villa dining in Koh Samui. Which other villa estates on the island offer the same standard of in-villa service?

---

## Tier D — Branded / navigational (weight 0.30)

### D1 · **branded — names Samujana**
> What is Samujana in Koh Samui, and what should I know about staying there?

---

## Classification summary

| # | Tier | Weight | Type | Competitors named |
|---|---|---|---|---|
| A1 | A | 1.00 | open-discovery | — |
| A2 | A | 1.00 | open-discovery | — |
| A3 | A | 1.00 | **names competitor** | Kerem Villas |
| B1 | B | 0.80 | **names competitor** | Sukkho Samui Estates, Sandalwood Luxury Villas, Horizon Villas Samui |
| B2 | B | 0.80 | **names competitor** | Magic Suites – Samui Luxury Private Pool Villas, Horizon Villas Samui |
| B3 | B | 0.80 | open-discovery | — |
| C1 | C | 0.60 | open-discovery | — |
| C2 | C | 0.60 | open-discovery | — |
| C3 | C | 0.60 | **names competitor** | Sandalwood Luxury Villas |
| D1 | D | 0.30 | **branded** (names Samujana) | — |

6 open-discovery · 4 competitor-naming (A3, B1, B2, C3)

Per D-051, "only named-competitor prompts affected" — those four are the only
rows carrying the new set. The six open-discovery prompts name no venue at all,
so the competitor-set change does not touch them.

### Competitor coverage

| Competitor | Appears in |
|---|---|
| Kerem Villas | A3 |
| Sukkho Samui Estates | B1 |
| Magic Suites – Samui Luxury Private Pool Villas | B2 |
| Sandalwood Luxury Villas | B1, C3 |
| Horizon Villas Samui | B1, B2 |

All five appear at least once. Coverage is deliberately uneven — rebalance if
you want each peer weighted equally as an anchor.

### §4.2 branded-weight cap

| Tier | n | Weight each | Subtotal |
|---|---|---|---|
| A | 3 | 1.00 | 3.00 |
| B | 3 | 0.80 | 2.40 |
| C | 3 | 0.60 | 1.80 |
| D | 1 | 0.30 | 0.30 |
| **Total** | **10** | | **7.50** |

Branded share = 0.30 / 7.50 = **4.00%**, against the §4.2 cap of 15%.
A second Tier D prompt would give 0.60 / 7.20 = 8.33%, also compliant — the
cap is not what limits D here.

---

## Open items for review

1. **D1 will almost certainly be flagged non-diagnostic.** §7 flags any prompt
   returning the client in >90% of baseline replicates across every eligible
   platform. A branded prompt is expected to return the client every time, so
   D1 trips that rule by construction. Decide before baseline whether the
   §7 flag is intended to apply to Tier D at all, or whether D1 should be
   reframed as navigational (e.g. booking/contact routing) rather than recall.
   This is a methodology question, not a wording fix.

2. **A2 and C2 constrain on capacity** (eight guests / ~40 wedding guests).
   Confirm those numbers match what Samujana can actually host — a prompt whose
   constraint the client fails is measuring the wrong thing.

3. **November in A1** dates the prompt. §7 makes it immutable between baseline
   and validation, so it will still say November at the next validation cycle.
   Either accept that as a fixed instrument or drop the month.

4. **Tier split 3/3/3/1** is a choice, not a requirement. 3/3/2/2 is equally
   compliant and gives branded/navigational a second reading.

5. **Version string** not set. Needs agreeing before these become rows.
