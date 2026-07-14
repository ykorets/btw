-- Third-party project pipeline. These rows describe reported/announced
-- capacity and must never be summed into BTW-verified operating capacity.
create table if not exists public.announcement (
  id uuid primary key default extensions.uuid_generate_v4(),
  name text not null,
  state text,
  county text,
  project_type text,
  operating_status text not null,
  expected_operating_year text,
  generating_technology text,
  reported_capacity_mw numeric,
  source_document_id uuid not null references public.document(id),
  source_as_of date not null,
  fact_state fact_status not null default 'staging',
  updated_at timestamptz not null default now(),
  unique (name, source_document_id)
);

alter table public.announcement enable row level security;

comment on table public.announcement is
  'Third-party announced/project pipeline records. These are never equivalent to BTW-verified operating capacity.';
comment on column public.announcement.reported_capacity_mw is
  'Capacity as reported by the cited source; basis may differ by source and is not verified operating MW.';

create index if not exists announcement_fact_state_idx
  on public.announcement (fact_state, operating_status);
create index if not exists announcement_source_document_idx
  on public.announcement (source_document_id);
