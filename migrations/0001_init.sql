-- Atlas Visibility — initial schema.
-- Execution Plan Technical Lane step 2: clients, properties, markets,
-- prompt versions, run plans, observations, recommendations, citations,
-- scores, actions, evidence, costs, audit logs.
--
-- Operating System §6: "Row-level security is enabled from the first
-- migration. Service roles are narrowly scoped." RLS is enabled on every
-- table below with no permissive policies added — the Supabase service
-- role (used only by the backend, never shipped client-side) bypasses RLS
-- by default, so this blocks anon/authenticated access without needing
-- explicit deny policies.
--
-- This project is Atlas Visibility only. Per Operating System §6 security
-- boundary, Hospitality Automation must use a separate Supabase project —
-- never add guest/booking/PII tables to this schema.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
-- clients / properties / markets
-- ---------------------------------------------------------------------

create table clients (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    status text not null default 'active' check (status in ('active', 'paused', 'churned')),
    created_at timestamptz not null default now()
);

create table properties (
    id uuid primary key default gen_random_uuid(),
    client_id uuid references clients(id),
    name text not null,
    category text, -- Hotel/Resort/Restaurant/LocalBusiness (Methodology §5.1)
    website_url text,
    google_business_profile_url text,
    -- System Zero runs Atlas's own domain and is never a hospitality
    -- calibration property (Execution Plan §3) — this flag keeps that
    -- distinction explicit in the data, not just in prose.
    is_system_zero boolean not null default false,
    is_calibration_property boolean not null default false,
    created_at timestamptz not null default now()
);

create table markets (
    id uuid primary key default gen_random_uuid(),
    property_id uuid references properties(id),
    market_code text not null,
    language_code text not null,
    is_primary boolean not null default true,
    created_at timestamptz not null default now(),
    unique (property_id, market_code, language_code)
);

-- ---------------------------------------------------------------------
-- prompt_versions
-- ---------------------------------------------------------------------

create table prompt_versions (
    id uuid primary key default gen_random_uuid(),
    set_type text not null check (set_type in ('frozen_core', 'sentinel', 'benchmark', 'discovery')),
    version text not null,
    prompt_text text not null,
    -- Methodology §4.2 commercial intent tiers. Immutable for the life of a
    -- prompt version — a tier change requires a new prompt version, never
    -- an update to this row (Methodology §7).
    intent_tier text not null check (intent_tier in ('A', 'B', 'C', 'D')),
    market_id uuid references markets(id),
    is_holdout boolean not null default false, -- Methodology §6.3, 20% of Benchmark Set
    created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- run_plans / observations
-- ---------------------------------------------------------------------

create table run_plans (
    id uuid primary key default gen_random_uuid(),
    property_id uuid references properties(id),
    run_type text not null check (
        run_type in ('frozen_core', 'sentinel', 'benchmark_monthly', 'benchmark_quarterly', 'discovery')
    ),
    replicate_count int not null,
    planned_at timestamptz not null default now(),
    status text not null default 'planned' check (status in ('planned', 'queued', 'running', 'complete', 'reconciled')),
    window_start timestamptz,
    window_end timestamptz
);

create table observations (
    id uuid primary key default gen_random_uuid(),
    -- Deterministic + idempotent: see atlas.planner.run_planner.deterministic_task_id.
    -- Rerunning a completed task must never duplicate a scored observation
    -- (Operating System §4).
    task_id text not null unique,
    run_plan_id uuid references run_plans(id),
    prompt_version_id uuid references prompt_versions(id),
    provider text not null check (provider in ('openai', 'gemini', 'perplexity', 'anthropic')),
    model text not null default '',
    model_snapshot text,
    tool_version text,
    replicate_index int not null,

    -- Resumable Measurement Pipeline states — Operating System §4.
    status text not null default 'planned' check (
        status in ('planned', 'queued', 'running', 'complete', 'retryable', 'failed', 'excluded', 'reconciled')
    ),

    -- Grounding — every observation stores these (Methodology §8.1).
    search_available boolean,
    search_invoked boolean,
    grounding_status text,

    raw_response jsonb,

    request_time timestamptz,
    completion_time timestamptz,
    latency_ms int,
    retry_number int not null default 0,
    http_status int,
    error_code text,

    input_tokens int,
    output_tokens int,
    search_tool_units int,
    cost_usd numeric(10, 4),
    -- A technical failure never becomes a zero score (Operating System §1)
    -- and unknown cost is flagged, never treated as zero (Execution Plan §3).
    is_unknown_cost boolean not null default false,

    evidence_id uuid,
    created_at timestamptz not null default now()
);

create index observations_run_plan_id_idx on observations (run_plan_id);
create index observations_status_idx on observations (status);

-- ---------------------------------------------------------------------
-- recommendations / citations
-- ---------------------------------------------------------------------

create table recommendations (
    id uuid primary key default gen_random_uuid(),
    observation_id uuid references observations(id),
    entity_name text not null,
    is_client_entity boolean not null default false,
    rank int, -- null for unordered positive recommendations
    -- Methodology §4.1 Recommendation Position Value, 0.00-1.00.
    rpv numeric(4, 2) not null,
    outcome_type text not null check (
        outcome_type in ('ranked', 'unordered_positive', 'source_only', 'absent', 'negative', 'entity_conflict')
    ),
    entity_conflict boolean not null default false,
    created_at timestamptz not null default now()
);

create table citations (
    id uuid primary key default gen_random_uuid(),
    observation_id uuid references observations(id),
    source_url text,
    source_domain text,
    -- Methodology §5.4 P5 Third-Party Authority tiers.
    tier text check (tier in ('T1', 'T2', 'T3', 'T4')),
    is_ai_cited boolean not null default false,
    independence text check (independence in ('editorial', 'disclosed_sponsored', 'owned')),
    relevance text check (relevance in ('destination_category', 'general')),
    published_at date,
    created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- scores
-- ---------------------------------------------------------------------

create table scores (
    id uuid primary key default gen_random_uuid(),
    property_id uuid references properties(id),
    -- Historical scores are never recalculated under a later methodology
    -- (Methodology §9) — this version stamp is what makes that enforceable.
    score_model_version text not null,
    prompt_set_version text not null,
    market_id uuid references markets(id),
    run_plan_id uuid references run_plans(id),
    measurement_date date not null,

    avs numeric(5, 2),
    visibility_band text check (
        visibility_band in ('not_observed', 'detectable', 'emerging', 'established', 'strong', 'leading')
    ),

    ars numeric(5, 2),
    readiness_band text check (
        readiness_band in ('fragile', 'developing', 'established', 'strong', 'advanced')
    ),
    p2_score numeric(5, 2),
    p3_score numeric(5, 2),
    p4_score numeric(5, 2),
    p5_score numeric(5, 2),

    -- < 90% => Incomplete, no movement verdict issued (Methodology §6.2).
    completeness_pct numeric(5, 2) not null,
    movement_verdict text check (
        movement_verdict in ('improvement', 'regression', 'no_meaningful_movement', 'inconclusive', 'incomplete')
    ),
    ci_lower numeric(6, 2),
    ci_upper numeric(6, 2),

    created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- actions (PRIORITISE / OPTIMISE stages)
-- ---------------------------------------------------------------------

create table actions (
    id uuid primary key default gen_random_uuid(),
    property_id uuid references properties(id),
    pillar text check (pillar in ('P2', 'P3', 'P4', 'P5')),
    description text not null,
    impact text check (impact in ('P0', 'P1', 'P2', 'P3')),
    confidence text,
    effort text,
    owner text,
    target_date date,
    status text not null default 'open' check (status in ('open', 'approved', 'implemented', 'deferred')),
    created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- evidence / costs / audit_logs
-- ---------------------------------------------------------------------

create table evidence (
    id uuid primary key default gen_random_uuid(),
    observation_id uuid references observations(id),
    run_id text,
    payload_hash text not null, -- SHA-256, see atlas.evidence.vault.hash_payload
    manifest_id text,
    storage_path text, -- Google Drive path (MVP) — Operating System §3
    data_class text check (
        data_class in ('score_bearing_non_personal', 'raw_ai_response', 'public_review_text', 'guest_personal_operational')
    ),
    captured_by text, -- operator, set only for human-captured (Layer B) evidence
    captured_at timestamptz not null default now()
);

create table costs (
    id uuid primary key default gen_random_uuid(),
    observation_id uuid references observations(id),
    property_id uuid references properties(id),
    provider text not null,
    input_tokens int,
    output_tokens int,
    search_units int,
    unit_cost_usd numeric(10, 6),
    total_cost_usd numeric(10, 4) not null,
    is_unknown_cost boolean not null default false,
    created_at timestamptz not null default now()
);

create index costs_property_id_idx on costs (property_id);

create table audit_logs (
    id uuid primary key default gen_random_uuid(),
    event_type text not null check (
        event_type in ('provider_incident', 'methodology_exception', 'manual_override', 'decision')
    ),
    entity_table text,
    entity_id uuid,
    description text not null,
    actor text,
    created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Row-level security — enabled on every table, no policies added.
-- Anon/authenticated roles get no access by default; the backend's
-- service-role key (server-side only, see atlas.db.client) bypasses RLS.
-- ---------------------------------------------------------------------

alter table clients enable row level security;
alter table properties enable row level security;
alter table markets enable row level security;
alter table prompt_versions enable row level security;
alter table run_plans enable row level security;
alter table observations enable row level security;
alter table recommendations enable row level security;
alter table citations enable row level security;
alter table scores enable row level security;
alter table actions enable row level security;
alter table evidence enable row level security;
alter table costs enable row level security;
alter table audit_logs enable row level security;
