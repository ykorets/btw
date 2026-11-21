-- Behind the Watt · source health telemetry
--
-- Why: adapters that fail are caught by watch.main() and logged to stdout, but
-- the run still exits 0 and `last_hit_at` simply stops moving. Nothing outside
-- the runner log knows *why*. The only alarm was the SLO staleness check, which
-- fires days later — so on 2026-07-21 three adapters were down while the morning
-- report said 🟢. These columns make each run's outcome durable and queryable.
--
-- last_run_at   — the adapter was attempted (moves on success AND failure)
-- last_error    — describe_error() output from the most recent failure
-- last_error_at — when that failure happened; cleared on the next success
-- last_yield    — rows the adapter returned last run, BEFORE dedupe.
--                 Distinct from "new candidates": mdeq returns 39 rows daily
--                 and stores 0 new ones — healthy. A source whose yield drops
--                 to 0 (FERC pattern) is silently degrading while looking green.

alter table public.source
  add column if not exists last_run_at   timestamptz,
  add column if not exists last_error    text,
  add column if not exists last_error_at timestamptz,
  add column if not exists last_yield    int;

comment on column public.source.last_run_at is
  'Last time the adapter was attempted, regardless of outcome.';
comment on column public.source.last_error is
  'Diagnostic string from the most recent failure; null when last run succeeded.';
comment on column public.source.last_error_at is
  'Timestamp of the most recent failure; cleared on the next success.';
comment on column public.source.last_yield is
  'Rows returned by the adapter on its last successful run, before dedupe.';
