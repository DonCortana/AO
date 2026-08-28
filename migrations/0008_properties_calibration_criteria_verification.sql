-- 0008: the two §8.4 property-selection criteria the `properties` table could
-- not express.
--
-- Forced by decision-register.md D-053, written before this file.
--
-- Methodology §8.4 names four property-selection criteria for a calibration
-- property. Two of them were checkable in code and two were not: migration
-- 0001 gave `properties` a `website_url` and a `google_business_profile_url`,
-- but nothing anywhere in the schema recorded review presence or a
-- third-party reference. So atlas/calibration/gate.py could verify half the
-- criteria and had to take the other half on trust from whoever ran the
-- seed — a selection gate that cannot be re-checked after the fact is not a
-- gate, it is a note.
--
-- Shape follows D-049 rather than inventing a second provenance idiom: each
-- criterion gets a boolean saying a human verified it and a text pointer to
-- the evidence record that shows the verification, with the evidence itself
-- stored through the existing hash-verified pipeline in
-- atlas.evidence.vault.store_evidence. The column pair is deliberate — a bare
-- boolean is an assertion with no artifact behind it, which is precisely the
-- unverifiable state this migration exists to end.
--
-- Nullable by decision, all four. The distinction that matters for a
-- selection gate is between "verified true", "verified false" and "not yet
-- looked at", and a not-null boolean collapses the third into one of the
-- first two. Null means unchecked; the gate reads it as not satisfied and
-- says which criterion is outstanding, rather than silently passing a
-- property nobody has examined.
--
-- No backfill and no application writer in this migration: D-053 adds the
-- columns only. Nothing populates them yet, so every existing row — Samujana
-- included — reads as unchecked until a human verifies and stores the
-- evidence.
--
-- 0001-0007 are already applied to the live project, so this is additive.

alter table properties add column review_presence_verified boolean;
alter table properties add column review_presence_evidence_ref text;
alter table properties add column third_party_reference_verified boolean;
alter table properties add column third_party_reference_evidence_ref text;

comment on column properties.review_presence_verified is
    'Methodology §8.4 property-selection criterion "review presence". True '
    'once a human has confirmed it against a stored artifact; false once '
    'confirmed absent; null when not yet checked (D-053).';

comment on column properties.review_presence_evidence_ref is
    'Pointer to the evidence record backing review_presence_verified, stored '
    'via atlas.evidence.vault.store_evidence (D-049 pattern). A verification '
    'with no artifact behind it is the state D-053 exists to end.';

comment on column properties.third_party_reference_verified is
    'Methodology §8.4 property-selection criterion "third-party reference". '
    'True once a human has confirmed an independent reference against a '
    'stored artifact; false once confirmed absent; null when not yet '
    'checked (D-053).';

comment on column properties.third_party_reference_evidence_ref is
    'Pointer to the evidence record backing third_party_reference_verified, '
    'stored via atlas.evidence.vault.store_evidence (D-049 pattern).';
