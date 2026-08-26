"""Supabase client wrapper.

Operating System §1: "The database is the source of truth for run state.
Schedulers are disposable triggers, not the record of whether work happened."

This module is the only place that constructs a Supabase client. Always uses
the service role key (server-side only) — RLS is enabled on every table from
migration 0001, so anon/authenticated access is denied by default and this
service role is deliberately the narrow, explicit exception.
"""

from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from atlas.config import get_settings


@lru_cache
def get_db() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
