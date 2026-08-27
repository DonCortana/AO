-- 0005: Layer A/Layer B separation on observations, and the §8.4 calibration
-- gate's system of record.
--
-- Forced by decision-register.md D-043 and D-044, both written before this
-- file. Together they unblock the Methodology §8.4 calibration gate, which
-- D-036 named as the missing precondition for producing any AVS at all.
--
-- Apply BEFORE the first Samujana calibration observation is written.

-- ---------------------------------------------------------------------
-- D-043: observations.surface_layer + widened provider vocabulary
-- ---------------------------------------------------------------------
--
-- Two faults in the 0001 schema, both silent:
--
--   1. `provider` was constrained to the four Layer A adapters, so a
--      Google AI consumer capture had no legal value to be written under
--      and the calibration could not store half of its own input.
--
--   2. For the four platforms that have BOTH an API adapter and a consumer
--      surface, a Layer B capture written with the same `provider` value is
--      indistinguishable from an extra replicate of the same platform.
--      atlas.scoring.loader would have read human consumer captures as
--      additional API replicates and folded them straight into a
--      client-facing AVS — inverting §8.3 and destroying the very
--      comparison §8.4 exists to make, with no error and no symptom.
--
-- Default 'api' is deliberate: every pre-existing observation row keeps its
-- current meaning with no data migration, and an adapter that does not know
-- about this column cannot accidentally write a consumer row.

alter table observations
    add column surface_layer text not null default 'api'
    check (surface_layer in ('api', 'consumer'));

comment on column observations.surface_layer is
    'Methodology §8.1 Layer A (api) vs §8.3 Layer B (consumer). Only api rows '
    'enter AVS — see atlas.scoring.loader and decision-register D-043.';

alter table observations drop constraint observations_provider_check;

alter table observations add constraint observations_provider_check check (
    provider in ('openai', 'gemini', 'perplexity', 'anthropic', 'google_ai')
);

-- D-042: Google AI (Search AI Overviews) has no Layer A leg and can never
-- acquire one under this methodology version. Enforced here rather than left
-- to convention, so the structural claim in D-042 cannot be quietly violated
-- by a future writer.
alter table observations add constraint observations_google_ai_is_consumer_only check (
    provider <> 'google_ai' or surface_layer = 'consumer'
);

-- ---------------------------------------------------------------------
-- D-044: calibration_results — the §8.4 gate's system of record
-- ---------------------------------------------------------------------
--
-- D-036 recorded that "no schema column anywhere records per-platform
-- eligibility" and therefore required compute_avs's caller to name the
-- eligible set by hand. This table supplies the missing store, and D-044
-- moves the eligible-platform list from the caller's keyboard to here —
-- narrowing D-036 without retiring it. Eligibility is still an explicit,
-- recorded act, never inferred from which observations happen to exist.
--
-- Append-only by discipline, matching `evidence` (Operating System §7:
-- score-bearing non-personal records are "Append-only; versioned"). The
-- writer in atlas.calibration.store exposes no update or delete path. A
-- re-run writes a NEW row under a new calibration_run_id; eligibility reads
-- the latest run, so a superseded result stays visible rather than being
-- overwritten.

create table calibration_results (
    id uuid primary key default gen_random_uuid(),

    -- Groups every per-platform row produced by one calibration cycle.
    calibration_run_id text not null,

    property_id uuid not null references properties(id),
    market_id uuid not null references markets(id),
    platform text not null check (
        platform in ('openai', 'gemini', 'perplexity', 'anthropic', 'google_ai')
    ),

    -- §9: no score row travels without its prompt-set version, and the gate's
    -- result is only meaningful against the prompt set that produced it.
    prompt_set_version text not null,

    -- §8.4 statistics. D-045 fixes the unit of analysis as the
    -- prompt-platform CELL, not the replicate — n_paired_units counts cells
    -- with a usable majority judgment on both layers.
    n_paired_units int not null check (n_paired_units >= 0),
    raw_agreement numeric(6, 4) check (raw_agreement between 0 and 1),

    -- Nullable: kappa is undefined when one rater's marginal is degenerate
    -- (every cell mention, or every cell absent). That is a real result, not
    -- a missing one, and kappa_prevalence_note records which case applied.
    cohen_kappa numeric(7, 4) check (cohen_kappa between -1 and 1),
    kappa_prevalence_note text not null,

    -- §8.4: rank agreement is reported "where at least 10 co-mentioned
    -- observations exist"; below that it is descriptive and "cannot rescue a
    -- failed mention-agreement gate". spearman_rho is null below the floor.
    co_mention_count int not null check (co_mention_count >= 0),
    spearman_rho numeric(7, 4) check (spearman_rho between -1 and 1),

    -- The gate's output. 'eligible' platforms enter AVS with equal weight
    -- (§4.3); 'evidence_only' platforms are retained and reported separately
    -- but excluded from AVS (§8.4).
    verdict text not null check (verdict in ('eligible', 'evidence_only')),

    -- WHICH ROUTE it passed by. Load-bearing per D-044: a clean kappa pass
    -- and a prevalence-degraded >=85%-plus-manual-review pass are not
    -- equivalent evidence, and a later reader must be able to tell them
    -- apart without re-deriving the statistics. Null when the verdict is
    -- 'evidence_only' — a failing platform passed by no route.
    pass_route text check (pass_route in ('kappa', 'raw_agreement_manual_review')),

    -- §8.4 requires "documented manual review" for the fallback route.
    reviewer text,
    notes text,

    created_at timestamptz not null default now(),

    unique (calibration_run_id, property_id, market_id, platform),

    -- A route is recorded exactly when the platform passed.
    constraint calibration_results_route_matches_verdict check (
        (verdict = 'eligible' and pass_route is not null)
        or (verdict = 'evidence_only' and pass_route is null)
    ),

    -- §8.4's fallback route is ">=85% raw agreement PLUS documented manual
    -- review". A named reviewer is what makes the review documented, so the
    -- route cannot be claimed without one.
    constraint calibration_results_manual_review_has_reviewer check (
        pass_route is distinct from 'raw_agreement_manual_review'
        or (reviewer is not null and length(trim(reviewer)) > 0)
    )
);

create index calibration_results_lookup_idx
    on calibration_results (property_id, market_id, created_at desc);

comment on table calibration_results is
    'Methodology §8.4 calibration gate results, per platform. Append-only. '
    'Source of the eligible-platform list consumed by atlas.scoring.avs — '
    'see decision-register D-036, D-044, D-045.';

-- RLS on every table, no permissive policies (Operating System §6, and the
-- standing rule from migration 0001).
alter table calibration_results enable row level security;
