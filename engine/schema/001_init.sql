-- Behind the Watt · engine schema v2.0 · maps to docs/architecture.md §3
create extension if not exists "uuid-ossp";

create type source_kind as enum ('search','rss','listing','api','pagehash');
create type anchor_kind as enum ('quote','cell','figure');
create type claim_status as enum ('extracted','validated','rejected','superseded');
create type fact_status as enum ('staging','published','retracted');
create type facility_status as enum
  ('announced','filed','permitted','under_construction','operating','suspended');

create table source (
  id text primary key,
  kind source_kind not null,
  url text not null,
  adapter text not null,
  schedule text not null default 'daily',
  slo_interval interval not null default '14 days',
  last_hit_at timestamptz
);

create table document (
  id uuid primary key default uuid_generate_v4(),
  source_id text not null references source(id),
  url text not null,
  r2_key text not null,
  sha256 text not null unique,
  fetched_at timestamptz not null default now(),
  doc_genre text,
  ocr_quality real,
  pages int
);

create table claim (
  id uuid primary key default uuid_generate_v4(),
  document_id uuid not null references document(id),
  entity_hint text,
  field text not null,
  value text not null,
  value_num numeric,
  unit text,
  anchor anchor_kind not null,
  quote text,
  page int,
  bbox jsonb,
  match_score real,
  numeric_check boolean,
  confidence real,
  extractor_version text not null,
  status claim_status not null default 'extracted',
  created_at timestamptz not null default now(),
  constraint anchored check (
    (anchor = 'quote' and quote is not null and match_score is not null)
    or (anchor in ('cell','figure') and page is not null and bbox is not null)
  )
);

create table facility (
  id uuid primary key default uuid_generate_v4(),
  slug text not null unique,
  name text not null,
  aliases text[] not null default '{}',
  state char(2) not null,
  county text,
  geo point,
  developer text,
  offtaker text,
  status facility_status not null default 'announced',
  flags text[] not null default '{}',
  fact_state fact_status not null default 'staging',
  first_permit_filed date,
  first_power date,
  updated_at timestamptz not null default now()
);

create table unit (
  id uuid primary key default uuid_generate_v4(),
  facility_id uuid not null references facility(id),
  oem text not null,
  model text,
  unit_count int,
  mw_each numeric,
  fuel text not null default 'natural_gas',
  hours_permitted int,
  fact_state fact_status not null default 'staging'
);

create table permit (
  id uuid primary key default uuid_generate_v4(),
  facility_id uuid not null references facility(id),
  authority text not null,
  permit_no text not null,
  permit_type text,
  status text not null,
  filed_at date,
  issued_at date,
  fact_state fact_status not null default 'staging',
  unique (authority, permit_no)
);

create table event (
  id uuid primary key default uuid_generate_v4(),
  facility_id uuid references facility(id),
  event_date date not null,
  event_type text not null,
  headline text not null,
  fact_state fact_status not null default 'staging'
);

create table fact_provenance (
  id uuid primary key default uuid_generate_v4(),
  fact_table text not null,
  fact_id uuid not null,
  fact_field text not null,
  claim_id uuid not null references claim(id),
  note text
);
create index on fact_provenance (fact_table, fact_id);

create table aggregate (
  id uuid primary key default uuid_generate_v4(),
  metric text not null,
  value numeric not null,
  method text not null,
  inputs_note text,
  computed_at timestamptz not null default now()
);

create table review (
  id uuid primary key default uuid_generate_v4(),
  batch_date date not null,
  pr_url text,
  decision text,
  corrections jsonb,
  created_at timestamptz not null default now()
);
