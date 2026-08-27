-- 0007: the Operating System §7 provenance fields the `evidence` table was
-- missing.
--
-- Forced by decision-register.md D-049, written before this file.
--
-- §7: "Every evidence record carries evidence ID, run ID, prompt version,
-- provider/model/tool version, market, language, UTC timestamp, source
-- reference, payload hash and operator where human capture is used."
--
-- Migration 0001 gave the table observation id, run id, payload hash,
-- manifest id, storage path, data class, captured_by and captured_at. The
-- seven columns below had no home in the database at all and travelled only
-- on the Drive file's appProperties — the gap named as
-- atlas.evidence.drive.EVIDENCE_TABLE_GAP in Technical Lane step 8 and
-- recorded there as needing a migration and a decision.
--
-- The Drive copy stays (atlas.evidence.vault._provenance writes the full §7
-- set as appProperties): a downloaded artifact that describes itself is what
-- remains intelligible to an auditor who has the file but not the database.
-- But it is the artifact's self-description, not the system of record — Drive
-- appProperties are not queryable by predicate, are truncated at 124 bytes of
-- key plus value on write, and sit behind a different access boundary from
-- the row that cites them. From here the database copy is the one an audit is
-- answered from.
--
-- Nullable by decision: the table is empty so nothing needs backfilling, and
-- tool_version and source_reference are legitimately absent for some captures
-- (a call with no tool leg; a capture with no consumer URL). A not-null
-- column would force a placeholder and make an absent value
-- indistinguishable from an empty one.
--
-- 0001-0006 are already applied to the live project, so this is additive.

alter table evidence add column prompt_version text;
alter table evidence add column provider text;
alter table evidence add column model text;
alter table evidence add column tool_version text;
alter table evidence add column market text;
alter table evidence add column language text;
alter table evidence add column source_reference text;

comment on column evidence.prompt_version is
    'Operating System §7. The prompt-set version the capture was made under — '
    '§9: no score-bearing record travels without it.';

-- Deliberately NOT constrained to the observations.provider vocabulary
-- (D-049). `evidence` covers data classes with no AI provider at all —
-- public_review_text — so the platform list is not a valid constraint here.
-- Free text matches costs.provider and is intentional.
comment on column evidence.provider is
    'Operating System §7. Free text by D-049, unlike observations.provider: '
    'evidence exists for data classes that have no AI provider.';

comment on column evidence.model is
    'Operating System §7. The pinned model id as called, e.g. gpt-5.6 — the '
    'reproducibility claim in §7 is against a specific model, not a family.';

comment on column evidence.tool_version is
    'Operating System §7 "tool version". Null where the call had no tool leg.';

comment on column evidence.market is
    'Operating System §7. Market code the capture was made for, e.g. TH.';

comment on column evidence.language is
    'Operating System §7. Language of the prompt/response, e.g. en.';

comment on column evidence.source_reference is
    'Operating System §7 "source reference": the provider request/response '
    'identifier for Layer A, the consumer surface URL or capture reference '
    'for Layer B. Stored here in full — the Drive appProperties copy is '
    'truncated at 124 bytes and is not authoritative (D-049).';
