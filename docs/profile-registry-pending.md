---
name: profile-registry-pending
description: Pending decision — properties.category is free text with no registry, but §7 and §5.3 both assume a profile vocabulary exists
metadata:
  type: project
---

`properties.category` is unconstrained free text. There is no profile registry
anywhere in the system: not in the schema (migration 0001 declares
`category text` with no check constraint), not in code, not in any fixture,
and not in the four governing documents. Raised 2026-08-27 while seeding the
Samujana calibration property, and deliberately deferred — it does not block
calibration.

Two places in the methodology assume the registry exists:

- **§7** sizes the Frozen Core at "8-12 **by profile**", so the prompt-set size
  rule is parameterised by a value nothing defines.
- **§5.3** scopes the P4 reputation peer set to "properties in the same
  **profile**, destination and positioning band", so peer comparability is
  keyed on it too.

The only category-shaped vocabulary in the docs is §5.1's
`Hotel/Resort/Restaurant/LocalBusiness`, and that is the *examples* column for
P2 structured-data entity markup — schema.org types to look for on a client's
site — not a registry of Atlas property profiles. The comment on
`properties.category` in migration 0001 copies that string, which conflates the
two and is the likeliest source of a future wrong assumption. That comment
should be corrected whenever this is resolved.

**Why:** Free text holds fine at one property and fails silently at two. Nothing
stops `Villa/Estate`, `villa estate`, `Private Villa` and `Villa` from coexisting
as four distinct profiles, at which point §5.3's peer set quietly splits and
§7's prompt-count rule has no stable key. The failure mode is not an error —
it is a peer comparison that silently narrows, which is exactly the class of
defect the register exists to catch before it reaches a client report.

**How to apply:** Not now, and not as part of the calibration cycle. Samujana is
seeded as `Villa/Estate` by convention (see the verification note in
`scripts/seed_calibration_property.py`), which is sufficient while exactly one
calibration property exists. Resolve **before Client #1** — the first paying
baseline is the point at which a second profile becomes possible and the string
becomes load-bearing.

Resolution needs a decision-register row covering: the profile vocabulary
itself; whether it becomes a check constraint, a lookup table, or an enum;
whether Frozen Core size and composition vary by profile (§7 implies they do);
and how profile relates to the §5.1 schema.org markup types, which is a
different axis and should not be collapsed into it. Per §10 this is likely a
clarification rather than a new baseline, since it documents an assumption the
methodology already makes — but confirm that against §10 at the time, because
if Frozen Core composition turns out to vary by profile it touches the prompt
set and is not a patch.

Related: [[methodology-band-footnote-pending]].
