-- M4: watcher output. A candidate is "a registry row we haven't judged yet".
-- Idempotent by (source_id, external_id): daily reruns cannot duplicate.
create table candidate (
  id uuid primary key default uuid_generate_v4(),
  source_id text not null references source(id),
  external_id text not null,
  url text,
  title text,
  payload jsonb,
  found_at timestamptz not null default now(),
  status text not null default 'new',  -- new | fetched | dismissed | promoted
  unique (source_id, external_id)
);
create index on candidate (source_id, status);
alter table candidate enable row level security;
