-- PROPOSED: truth-integrity hardening for unit/permit facts.
--
-- This migration is intentionally schema-only.  It does not reconcile or
-- rewrite any existing fact values.  Apply only after the current violations
-- from `python -m btw_engine.audit` have been reviewed.

create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;

do $$ begin
  create type public.unit_basis as enum ('permitted', 'observed', 'reported');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type public.provenance_support as enum ('direct', 'derived');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type public.fact_verification as enum
    ('source_asserted', 'corroborated', 'verified', 'disputed');
exception when duplicate_object then null;
end $$;

-- Unknown equipment attributes are NULL, never truth-looking defaults.
alter table public.unit alter column oem drop not null;
alter table public.unit alter column fuel drop default;
alter table public.unit alter column fuel drop not null;
alter table public.unit add column if not exists basis public.unit_basis;
alter table public.unit add column if not exists total_mw numeric;
alter table public.unit add column if not exists verification_state public.fact_verification;
alter table public.unit add column if not exists logical_id uuid
  not null default extensions.uuid_generate_v4();
alter table public.unit add column if not exists created_at timestamptz not null default now();
alter table public.unit add column if not exists updated_at timestamptz not null default now();
alter table public.unit drop constraint if exists unit_total_mw_nonnegative;
alter table public.unit add constraint unit_total_mw_nonnegative
  check (total_mw is null or total_mw >= 0);

alter table public.permit add column if not exists created_at timestamptz not null default now();
alter table public.permit add column if not exists updated_at timestamptz not null default now();
alter table public.permit add column if not exists verification_state public.fact_verification;
alter table public.permit add column if not exists logical_id uuid
  not null default extensions.uuid_generate_v4();

-- A permit may have a published and a staging version simultaneously.  Old
-- versions remain retracted rather than being deleted.
alter table public.permit
  drop constraint if exists permit_authority_permit_no_key;
create unique index if not exists permit_one_published_version
  on public.permit (authority, permit_no)
  where fact_state = 'published';
create unique index if not exists permit_one_staging_version
  on public.permit (authority, permit_no)
  where fact_state = 'staging';
create unique index if not exists unit_one_published_version
  on public.unit (logical_id) where fact_state = 'published';
create unique index if not exists unit_one_staging_version
  on public.unit (logical_id) where fact_state = 'staging';
create unique index if not exists permit_logical_one_published_version
  on public.permit (logical_id) where fact_state = 'published';
create unique index if not exists permit_logical_one_staging_version
  on public.permit (logical_id) where fact_state = 'staging';

-- A review approves an immutable set of exact row versions.  Nullable typed
-- FKs avoid another polymorphic target with no referential integrity.
alter table public.review add column if not exists manifest_hash text;
alter table public.review add column if not exists merge_commit_sha text;
alter table public.review add column if not exists promoted_at timestamptz;
alter table public.review enable row level security;

create table if not exists public.review_manifest_item (
  id uuid primary key default extensions.uuid_generate_v4(),
  review_id uuid not null references public.review(id) on delete restrict,
  unit_id uuid references public.unit(id) on delete restrict,
  permit_id uuid references public.permit(id) on delete restrict,
  event_id uuid references public.event(id) on delete restrict,
  created_at timestamptz not null default now(),
  constraint review_manifest_exactly_one_target check (
    num_nonnulls(unit_id, permit_id, event_id) = 1
  )
);
alter table public.review_manifest_item enable row level security;
create unique index if not exists review_manifest_unit_once
  on public.review_manifest_item (review_id, unit_id) where unit_id is not null;
create unique index if not exists review_manifest_permit_once
  on public.review_manifest_item (review_id, permit_id) where permit_id is not null;
create unique index if not exists review_manifest_event_once
  on public.review_manifest_item (review_id, event_id) where event_id is not null;
create unique index if not exists review_one_pending_per_day
  on public.review (batch_date) where decision = 'pending';

alter table public.fact_provenance
  add column if not exists support_kind public.provenance_support
  not null default 'direct';
alter table public.fact_provenance
  add column if not exists derivation text;
alter table public.fact_provenance
  add column if not exists created_at timestamptz not null default now();
alter table public.fact_provenance
  drop constraint if exists fact_provenance_derivation_required;
alter table public.fact_provenance
  add constraint fact_provenance_derivation_required check (
    (support_kind = 'direct' and derivation is null)
    or
    (support_kind = 'derived' and nullif(btrim(derivation), '') is not null)
  );

-- Polymorphic provenance cannot have a normal FK to its target.  Enforce the
-- target and semantic field compatibility with a trigger instead.
create or replace function public.btw_validate_fact_provenance()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  claim_field text;
  claim_state public.claim_status;
  target_basis public.unit_basis;
  target_state public.fact_status;
  target_exists boolean := false;
  compatible boolean := false;
begin
  select c.field, c.status
    into claim_field, claim_state
  from public.claim c
  where c.id = new.claim_id;

  if not found or claim_state <> 'validated'::public.claim_status then
    raise exception 'provenance claim % is absent or not validated', new.claim_id;
  end if;

  if new.fact_table = 'unit' then
    select true, u.basis, u.fact_state
      into target_exists, target_basis, target_state
    from public.unit u where u.id = new.fact_id;
  elsif new.fact_table = 'permit' then
    select true, p.fact_state into target_exists, target_state
    from public.permit p where p.id = new.fact_id;
  else
    -- Other fact families keep their current pipeline until their own field
    -- matrix is specified; this migration must not break facility/event work.
    return new;
  end if;

  if not coalesce(target_exists, false) then
    raise exception 'provenance target %.% does not exist',
      new.fact_table, new.fact_id;
  end if;

  if target_state = 'published' then
    raise exception 'attach provenance to a staging fact version, not published %.%',
      new.fact_table, new.fact_id;
  end if;

  if new.support_kind = 'direct' then
    compatible := case
      when new.fact_table = 'unit' and new.fact_field = 'oem'
        then claim_field = 'unit.oem'
      when new.fact_table = 'unit' and new.fact_field = 'model'
        then claim_field = 'unit.model'
      when new.fact_table = 'unit' and new.fact_field = 'unit_count'
        then claim_field = case when target_basis = 'observed'
          then 'observation.unit_count' else 'unit.count' end
      when new.fact_table = 'unit' and new.fact_field = 'mw_each'
        then claim_field = 'unit.mw_each'
      when new.fact_table = 'unit' and new.fact_field = 'total_mw'
        then claim_field in ('unit.mw_total', 'observation.mw')
      when new.fact_table = 'unit' and new.fact_field = 'fuel'
        then claim_field = 'unit.fuel'
      when new.fact_table = 'unit' and new.fact_field = 'hours_permitted'
        then claim_field = 'unit.hours_permitted'
      when new.fact_table = 'permit' and new.fact_field = 'authority'
        then claim_field = 'permit.authority'
      when new.fact_table = 'permit' and new.fact_field = 'permit_no'
        then claim_field = 'permit.no'
      when new.fact_table = 'permit' and new.fact_field = 'permit_type'
        then claim_field = 'permit.type'
      when new.fact_table = 'permit' and new.fact_field = 'status'
        then claim_field = 'permit.status'
      when new.fact_table = 'permit' and new.fact_field = 'filed_at'
        then claim_field = 'permit.filed_at'
      when new.fact_table = 'permit' and new.fact_field = 'issued_at'
        then claim_field = 'permit.issued_at'
      else false
    end;
  else
    compatible := (
      new.fact_table = 'unit'
      and (
        (new.fact_field = 'basis' and case target_basis
          when 'permitted' then claim_field like 'unit.%'
          when 'observed' then claim_field like 'observation.%'
          when 'reported' then claim_field like 'unit.%'
            or claim_field like 'observation.%'
          else false
        end)
        or (new.fact_field = 'verification_state'
            and (claim_field like 'unit.%'
                 or claim_field like 'observation.%'))
        or (new.fact_field in ('unit_count', 'mw_each', 'total_mw')
            and claim_field in ('unit.count', 'unit.mw_each', 'unit.mw_total',
                                'observation.unit_count', 'observation.mw'))
      )
    ) or (
      new.fact_table = 'permit'
      and new.fact_field = 'verification_state'
      and claim_field like 'permit.%'
    );
  end if;

  if not compatible then
    raise exception 'claim field % cannot support %.% as % provenance',
      claim_field, new.fact_table, new.fact_field, new.support_kind;
  end if;
  return new;
end;
$$;

drop trigger if exists fact_provenance_truth_gate on public.fact_provenance;
create trigger fact_provenance_truth_gate
before insert or update on public.fact_provenance
for each row execute function public.btw_validate_fact_provenance();

-- Receipts are append-only, and even appends stop once their target version
-- is sealed into a pending review. This keeps the reviewed evidence bundle
-- stable between PR creation and merge.
create or replace function public.btw_preserve_fact_provenance()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  row_value public.fact_provenance%rowtype;
begin
  if tg_op <> 'INSERT' then
    raise exception 'fact provenance receipts are immutable';
  end if;
  row_value := new;
  if exists (
    select 1
    from public.review_manifest_item mi
    join public.review r on r.id = mi.review_id
    where r.decision = 'pending'
      and ((row_value.fact_table = 'unit' and mi.unit_id = row_value.fact_id)
        or (row_value.fact_table = 'permit' and mi.permit_id = row_value.fact_id)
        or (row_value.fact_table = 'event' and mi.event_id = row_value.fact_id))
  ) then
    raise exception 'cannot add evidence to a fact version in a sealed review';
  end if;
  return new;
end;
$$;

drop trigger if exists preserve_fact_provenance on public.fact_provenance;
create trigger preserve_fact_provenance
before insert or update or delete on public.fact_provenance
for each row execute function public.btw_preserve_fact_provenance();

-- A validated extraction is a versioned evidence record, not mutable scratch
-- space. Corrections create a new claim and supersede by linkage/workflow.
create or replace function public.btw_preserve_validated_claim()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
  if old.status = 'validated' then
    raise exception 'validated claims are immutable; create a corrected claim';
  end if;
  return case when tg_op = 'DELETE' then old else new end;
end;
$$;

drop trigger if exists preserve_validated_claim on public.claim;
create trigger preserve_validated_claim
before update or delete on public.claim
for each row execute function public.btw_preserve_validated_claim();

create or replace function public.btw_manifest_hash_from_arrays(
  p_unit_ids uuid[] default '{}'::uuid[],
  p_permit_ids uuid[] default '{}'::uuid[],
  p_event_ids uuid[] default '{}'::uuid[]
)
returns text
language sql
immutable
security invoker
set search_path = public
as $$
  select encode(extensions.digest(
    coalesce(string_agg(item, '|' order by item), ''), 'sha256'), 'hex')
  from (
    select 'unit:' || id::text item from unnest(coalesce(p_unit_ids, '{}'::uuid[])) id
    union
    select 'permit:' || id::text from unnest(coalesce(p_permit_ids, '{}'::uuid[])) id
    union
    select 'event:' || id::text from unnest(coalesce(p_event_ids, '{}'::uuid[])) id
  ) manifest;
$$;

create or replace function public.btw_review_manifest_hash(p_review_id uuid)
returns text
language sql
stable
security invoker
set search_path = public
as $$
  select encode(extensions.digest(
    coalesce(string_agg(item, '|' order by item), ''), 'sha256'), 'hex')
  from (
    select 'unit:' || unit_id::text item
    from public.review_manifest_item
    where review_id = p_review_id and unit_id is not null
    union
    select 'permit:' || permit_id::text
    from public.review_manifest_item
    where review_id = p_review_id and permit_id is not null
    union
    select 'event:' || event_id::text
    from public.review_manifest_item
    where review_id = p_review_id and event_id is not null
  ) manifest;
$$;

create or replace function public.btw_create_review_manifest(
  p_batch_date date,
  p_unit_ids uuid[] default '{}'::uuid[],
  p_permit_ids uuid[] default '{}'::uuid[],
  p_event_ids uuid[] default '{}'::uuid[]
)
returns table(review_id uuid, manifest_hash text)
language plpgsql
security invoker
set search_path = public
as $$
declare
  existing_review public.review%rowtype;
  new_review_id uuid;
  requested_hash text;
  stored_hash text;
  violations text;
begin
  -- Serialize same-day creation.  The unique partial index is still the
  -- final constraint; this makes concurrent retries deterministic.
  perform pg_advisory_xact_lock(hashtext(p_batch_date::text));
  perform set_config('btw.manifest_creation', 'on', true);

  p_unit_ids := coalesce(p_unit_ids, '{}'::uuid[]);
  p_permit_ids := coalesce(p_permit_ids, '{}'::uuid[]);
  p_event_ids := coalesce(p_event_ids, '{}'::uuid[]);

  if cardinality(p_unit_ids) <> (select count(distinct id) from unnest(p_unit_ids) id)
     or cardinality(p_permit_ids) <> (select count(distinct id) from unnest(p_permit_ids) id)
     or cardinality(p_event_ids) <> (select count(distinct id) from unnest(p_event_ids) id) then
    raise exception 'review manifest contains duplicate ids';
  end if;

  if cardinality(p_unit_ids) <> (
       select count(*) from public.unit
       where id = any(p_unit_ids) and fact_state = 'staging') then
    raise exception 'review manifest includes absent or non-staging unit ids';
  end if;
  if cardinality(p_permit_ids) <> (
       select count(*) from public.permit
       where id = any(p_permit_ids) and fact_state = 'staging') then
    raise exception 'review manifest includes absent or non-staging permit ids';
  end if;
  if cardinality(p_event_ids) <> (
       select count(*) from public.event
       where id = any(p_event_ids) and fact_state = 'staging') then
    raise exception 'review manifest includes absent or non-staging event ids';
  end if;

  requested_hash := public.btw_manifest_hash_from_arrays(
    p_unit_ids, p_permit_ids, p_event_ids);

  select r.* into existing_review
  from public.review r
  where r.batch_date = p_batch_date and r.decision = 'pending'
  for update;

  if found then
    if existing_review.manifest_hash is distinct from requested_hash then
      raise exception 'pending review % already exists with a different manifest',
        existing_review.id;
    end if;
    return query select existing_review.id, existing_review.manifest_hash;
    return;
  end if;

  insert into public.review (batch_date, decision, corrections)
  values (p_batch_date, 'pending', '{}'::jsonb)
  returning id into new_review_id;

  insert into public.review_manifest_item (review_id, unit_id)
  select new_review_id, id from unnest(p_unit_ids) id;
  insert into public.review_manifest_item (review_id, permit_id)
  select new_review_id, id from unnest(p_permit_ids) id;
  insert into public.review_manifest_item (review_id, event_id)
  select new_review_id, id from unnest(p_event_ids) id;

  select string_agg(v.message, E'\n' order by v.message) into violations
  from public.btw_review_truth_violations(new_review_id) v;
  if violations is not null then
    raise exception 'review manifest truth gate failed:%', E'\n' || violations;
  end if;

  stored_hash := public.btw_review_manifest_hash(new_review_id);
  if stored_hash is distinct from requested_hash then
    raise exception 'stored review manifest hash mismatch';
  end if;
  update public.review set manifest_hash = stored_hash
  where id = new_review_id;
  return query select new_review_id, stored_hash;
end;
$$;

-- Once the hash is stored, the manifest is an immutable approval boundary.
-- The constructor inserts items while the parent review still has a NULL
-- hash. Updates and deletes are never valid.
create or replace function public.btw_preserve_review_manifest()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  parent_hash text;
  parent_decision text;
begin
  if tg_op <> 'INSERT' then
    raise exception 'review manifest items are immutable';
  end if;
  if coalesce(current_setting('btw.manifest_creation', true), '') <> 'on' then
    raise exception 'review manifest items must come through btw_create_review_manifest';
  end if;
  select r.manifest_hash, r.decision into parent_hash, parent_decision
  from public.review r where r.id = new.review_id for update;
  if not found or parent_decision <> 'pending' or parent_hash is not null then
    raise exception 'review manifest % is already sealed', new.review_id;
  end if;
  return new;
end;
$$;

drop trigger if exists preserve_review_manifest on public.review_manifest_item;
create trigger preserve_review_manifest
before insert or update or delete on public.review_manifest_item
for each row execute function public.btw_preserve_review_manifest();

-- PR URL updates remain legal, but neither the sealed hash nor the decision
-- can be replaced by a direct REST PATCH.
create or replace function public.btw_preserve_review_boundary()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
  if tg_op = 'INSERT' then
    if coalesce(current_setting('btw.manifest_creation', true), '') <> 'on' then
      raise exception 'reviews must come through btw_create_review_manifest';
    end if;
    return new;
  end if;
  if tg_op = 'DELETE' then
    raise exception 'reviews are immutable audit records';
  end if;
  if old.manifest_hash is not null
     and new.manifest_hash is distinct from old.manifest_hash then
    raise exception 'sealed review manifest hash is immutable';
  end if;
  if new.decision is distinct from old.decision
     and coalesce(current_setting('btw.atomic_promotion', true), '') <> 'on' then
    raise exception 'review decisions change only through btw_promote_review';
  end if;
  return new;
end;
$$;

drop trigger if exists preserve_review_boundary on public.review;
create trigger preserve_review_boundary
before insert or update or delete on public.review
for each row execute function public.btw_preserve_review_boundary();

create or replace function public.btw_parse_claim_date(value text)
returns date
language plpgsql
immutable
security invoker
set search_path = public
as $$
begin
  if value is null or btrim(value) = '' then return null; end if;
  if value ~ '^\d{4}-\d{2}-\d{2}$' then return value::date; end if;
  if value ~ '^\d{1,2}/\d{1,2}/\d{4}$' then
    return to_date(value, 'MM/DD/YYYY');
  end if;
  return to_date(value, 'Month DD, YYYY');
exception when others then
  return null;
end;
$$;

create or replace function public.btw_fact_field_supported(
  p_fact_table text,
  p_fact_id uuid,
  p_fact_field text,
  p_value text,
  p_numeric_value numeric default null
)
returns boolean
language sql
stable
security invoker
set search_path = public
as $$
  select exists (
    select 1
    from public.fact_provenance fp
    join public.claim c on c.id = fp.claim_id
    join public.document d on d.id = c.document_id
    left join public.unit u
      on p_fact_table = 'unit' and u.id = p_fact_id
    where fp.fact_table = p_fact_table
      and fp.fact_id = p_fact_id
      and fp.fact_field = p_fact_field
      and c.status = 'validated'
      and nullif(d.r2_key, '') is not null
      and (
        (c.anchor = 'quote' and c.quote is not null and c.match_score >= 0.92)
        or (c.anchor in ('cell', 'figure') and c.page is not null and c.bbox is not null)
      )
      and case
        when p_fact_table = 'unit' and p_fact_field = 'basis'
          then fp.support_kind = 'derived'
            and nullif(btrim(fp.derivation), '') is not null
            and case u.basis
              when 'permitted' then c.field like 'unit.%'
              when 'observed' then c.field like 'observation.%'
              when 'reported' then c.field in ('unit.count', 'unit.mw_total',
                                                'observation.unit_count', 'observation.mw')
              else false
            end
        when p_fact_table in ('unit', 'permit')
             and p_fact_field = 'verification_state'
          then fp.support_kind = 'derived'
            and nullif(btrim(fp.derivation), '') is not null
        when p_fact_table = 'unit' and p_fact_field = 'oem'
          then fp.support_kind = 'direct' and c.field = 'unit.oem'
        when p_fact_table = 'unit' and p_fact_field = 'model'
          then fp.support_kind = 'direct' and c.field = 'unit.model'
        when p_fact_table = 'unit' and p_fact_field = 'unit_count'
          then fp.support_kind = 'direct'
            and c.field = case when u.basis = 'observed'
              then 'observation.unit_count' else 'unit.count' end
        when p_fact_table = 'unit' and p_fact_field = 'mw_each'
          then fp.support_kind = 'direct' and c.field = 'unit.mw_each'
        when p_fact_table = 'unit' and p_fact_field = 'total_mw'
          then fp.support_kind = 'direct'
            and c.field in ('unit.mw_total', 'observation.mw')
        when p_fact_table = 'unit' and p_fact_field = 'fuel'
          then fp.support_kind = 'direct' and c.field = 'unit.fuel'
        when p_fact_table = 'unit' and p_fact_field = 'hours_permitted'
          then fp.support_kind = 'direct' and c.field = 'unit.hours_permitted'
        when p_fact_table = 'permit' and p_fact_field = 'authority'
          then fp.support_kind = 'direct' and c.field = 'permit.authority'
        when p_fact_table = 'permit' and p_fact_field = 'permit_no'
          then fp.support_kind = 'direct' and c.field = 'permit.no'
        when p_fact_table = 'permit' and p_fact_field = 'permit_type'
          then fp.support_kind = 'direct' and c.field = 'permit.type'
        when p_fact_table = 'permit' and p_fact_field = 'status'
          then fp.support_kind = 'direct' and c.field = 'permit.status'
        when p_fact_table = 'permit' and p_fact_field = 'filed_at'
          then fp.support_kind = 'direct' and c.field = 'permit.filed_at'
        when p_fact_table = 'permit' and p_fact_field = 'issued_at'
          then fp.support_kind = 'direct' and c.field = 'permit.issued_at'
        else false
      end
      and case
        when p_fact_field in ('unit_count', 'mw_each', 'total_mw', 'hours_permitted')
          then c.numeric_check is true and c.value_num = p_numeric_value
        when p_fact_field in ('filed_at', 'issued_at')
          then public.btw_parse_claim_date(c.value) = p_value::date
        when p_fact_field in ('basis', 'verification_state') then true
        else
          nullif(regexp_replace(lower(c.value), '[^0-9a-z]', '', 'g'), '') is not null
          and (
            regexp_replace(lower(p_value), '[^0-9a-z]', '', 'g') like
              '%' || regexp_replace(lower(c.value), '[^0-9a-z]', '', 'g') || '%'
            or regexp_replace(lower(c.value), '[^0-9a-z]', '', 'g') like
              '%' || regexp_replace(lower(p_value), '[^0-9a-z]', '', 'g') || '%'
            or regexp_replace(lower(coalesce(c.quote, '')), '[^0-9a-z]', '', 'g') like
              '%' || regexp_replace(lower(p_value), '[^0-9a-z]', '', 'g') || '%'
          )
      end
  );
$$;

create or replace function public.btw_review_truth_violations(p_review_id uuid)
returns table(message text)
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
  u public.unit%rowtype;
  p public.permit%rowtype;
  component text;
begin
  for u in
    select fact.* from public.review_manifest_item mi
    join public.unit fact on fact.id = mi.unit_id
    where mi.review_id = p_review_id and mi.unit_id is not null
  loop
    if u.fact_state <> 'staging' then
      return query select format('unit %s is not staging', u.id); continue;
    end if;
    if u.basis is null then
      return query select format('unit %s has no basis', u.id);
    elsif not public.btw_fact_field_supported('unit', u.id, 'basis', u.basis::text) then
      return query select format('unit %s basis has no compatible derived receipt', u.id);
    end if;
    if u.verification_state is null then
      return query select format('unit %s has no verification_state', u.id);
    elsif not public.btw_fact_field_supported(
      'unit', u.id, 'verification_state', u.verification_state::text) then
      return query select format(
        'unit %s verification_state has no compatible derived receipt', u.id);
    end if;
    if u.oem is not null and not public.btw_fact_field_supported('unit', u.id, 'oem', u.oem) then
      return query select format('unit %s oem is unsupported', u.id); end if;
    if u.model is not null and not public.btw_fact_field_supported('unit', u.id, 'model', u.model) then
      return query select format('unit %s model is unsupported', u.id); end if;
    if u.unit_count is not null and not public.btw_fact_field_supported(
      'unit', u.id, 'unit_count', u.unit_count::text, u.unit_count) then
      return query select format('unit %s unit_count is unsupported', u.id); end if;
    if u.mw_each is not null and not public.btw_fact_field_supported(
      'unit', u.id, 'mw_each', u.mw_each::text, u.mw_each) then
      return query select format('unit %s mw_each is unsupported', u.id); end if;
    if u.total_mw is not null and not public.btw_fact_field_supported(
      'unit', u.id, 'total_mw', u.total_mw::text, u.total_mw) then
      return query select format('unit %s total_mw is unsupported', u.id); end if;
    if u.fuel is not null and not public.btw_fact_field_supported('unit', u.id, 'fuel', u.fuel) then
      return query select format('unit %s fuel is unsupported', u.id); end if;
    if u.hours_permitted is not null and not public.btw_fact_field_supported(
      'unit', u.id, 'hours_permitted', u.hours_permitted::text, u.hours_permitted) then
      return query select format('unit %s hours_permitted is unsupported', u.id); end if;
  end loop;

  for p in
    select fact.* from public.review_manifest_item mi
    join public.permit fact on fact.id = mi.permit_id
    where mi.review_id = p_review_id and mi.permit_id is not null
  loop
    if p.fact_state <> 'staging' then
      return query select format('permit %s is not staging', p.id); continue;
    end if;
    if p.verification_state is null then
      return query select format('permit %s has no verification_state', p.id);
    elsif not public.btw_fact_field_supported(
      'permit', p.id, 'verification_state', p.verification_state::text) then
      return query select format(
        'permit %s verification_state has no compatible derived receipt', p.id);
    end if;
    if not public.btw_fact_field_supported('permit', p.id, 'authority', p.authority) then
      return query select format('permit %s authority is unsupported', p.id); end if;
    if not public.btw_fact_field_supported('permit', p.id, 'permit_no', p.permit_no) then
      return query select format('permit %s permit_no is unsupported', p.id); end if;
    if p.permit_type is not null and not public.btw_fact_field_supported(
      'permit', p.id, 'permit_type', p.permit_type) then
      return query select format('permit %s permit_type is unsupported', p.id); end if;
    for component in select btrim(value) from regexp_split_to_table(p.status, '[;,]') value
    loop
      if component <> '' and not public.btw_fact_field_supported(
        'permit', p.id, 'status', component) then
        return query select format('permit %s status component %s is unsupported', p.id, component);
      end if;
    end loop;
    if p.filed_at is not null and not public.btw_fact_field_supported(
      'permit', p.id, 'filed_at', p.filed_at::text) then
      return query select format('permit %s filed_at is unsupported', p.id); end if;
    if p.issued_at is not null and not public.btw_fact_field_supported(
      'permit', p.id, 'issued_at', p.issued_at::text) then
      return query select format('permit %s issued_at is unsupported', p.id); end if;
  end loop;

  if exists (
    select 1 from public.review_manifest_item mi
    join public.event e on e.id = mi.event_id
    where mi.review_id = p_review_id and mi.event_id is not null
      and (e.fact_state <> 'staging' or nullif(btrim(e.source_url), '') is null)
  ) then
    return query select 'one or more events are non-staging or have no source_url';
  end if;
end;
$$;

create or replace function public.btw_promote_review(
  p_review_id uuid,
  p_manifest_hash text,
  p_merge_commit_sha text
)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  review_row public.review%rowtype;
  actual_hash text;
  violations text;
  unit_count int;
  permit_count int;
  event_count int;
  operating_gw numeric;
begin
  if nullif(btrim(p_merge_commit_sha), '') is null then
    raise exception 'merge commit sha is required';
  end if;

  select r.* into review_row from public.review r
  where r.id = p_review_id for update;
  if not found then raise exception 'review % not found', p_review_id; end if;

  actual_hash := public.btw_review_manifest_hash(p_review_id);
  if actual_hash is distinct from review_row.manifest_hash
     or actual_hash is distinct from p_manifest_hash then
    raise exception 'review manifest hash mismatch';
  end if;

  if review_row.decision = 'promoted' then
    if review_row.merge_commit_sha is distinct from p_merge_commit_sha then
      raise exception 'review was promoted by a different merge commit';
    end if;
    return jsonb_build_object('status', 'already_promoted',
                              'review_id', p_review_id,
                              'manifest_hash', actual_hash);
  end if;
  if review_row.decision <> 'pending' then
    raise exception 'review % is %, expected pending', p_review_id, review_row.decision;
  end if;

  select string_agg(v.message, E'\n' order by v.message) into violations
  from public.btw_review_truth_violations(p_review_id) v;
  if violations is not null then
    raise exception 'promotion truth gate failed:%', E'\n' || violations;
  end if;

  -- Transaction-local capability checked by the defensive triggers below.
  -- REST callers cannot perform the protected state transitions piecemeal.
  perform set_config('btw.atomic_promotion', 'on', true);

  select count(*) filter (where unit_id is not null),
         count(*) filter (where permit_id is not null),
         count(*) filter (where event_id is not null)
    into unit_count, permit_count, event_count
  from public.review_manifest_item where review_id = p_review_id;

  update public.unit old
  set fact_state = 'retracted', updated_at = now()
  where old.fact_state = 'published'
    and exists (
      select 1 from public.review_manifest_item mi
      join public.unit staged on staged.id = mi.unit_id
      where mi.review_id = p_review_id
        and staged.logical_id = old.logical_id and staged.id <> old.id
    );
  update public.permit old
  set fact_state = 'retracted', updated_at = now()
  where old.fact_state = 'published'
    and exists (
      select 1 from public.review_manifest_item mi
      join public.permit staged on staged.id = mi.permit_id
      where mi.review_id = p_review_id
        and staged.logical_id = old.logical_id and staged.id <> old.id
    );

  update public.unit u set fact_state = 'published', updated_at = now()
  from public.review_manifest_item mi
  where mi.review_id = p_review_id and mi.unit_id = u.id;
  update public.permit p set fact_state = 'published', updated_at = now()
  from public.review_manifest_item mi
  where mi.review_id = p_review_id and mi.permit_id = p.id;
  update public.event e set fact_state = 'published'
  from public.review_manifest_item mi
  where mi.review_id = p_review_id and mi.event_id = e.id;

  select round(coalesce(sum(coalesce(
    u.total_mw, u.unit_count * u.mw_each, 0)), 0) / 1000, 2)
  into operating_gw
  from public.unit u join public.facility f on f.id = u.facility_id
  where u.fact_state = 'published' and f.fact_state = 'published'
    and f.status = 'operating';
  insert into public.aggregate (metric, value, method, inputs_note)
  values ('operating_gw', operating_gw,
          'sum(coalesce(unit.total_mw, unit_count*mw_each)) over published operating facilities',
          format('atomic promotion review=%s manifest=%s', p_review_id, actual_hash));

  update public.review set decision = 'promoted', promoted_at = now(),
    merge_commit_sha = p_merge_commit_sha
  where id = p_review_id;

  return jsonb_build_object('status', 'promoted', 'review_id', p_review_id,
    'manifest_hash', actual_hash, 'units', unit_count, 'permits', permit_count,
    'events', event_count, 'operating_gw', operating_gw);
end;
$$;

revoke all on function public.btw_create_review_manifest(date, uuid[], uuid[], uuid[])
  from public, anon, authenticated;
grant execute on function public.btw_create_review_manifest(date, uuid[], uuid[], uuid[])
  to service_role;
revoke all on function public.btw_promote_review(uuid, text, text)
  from public, anon, authenticated;
grant execute on function public.btw_promote_review(uuid, text, text)
  to service_role;

-- Published facts are immutable versions.  Promotion is the sole operation
-- allowed to retract an old version and publish the exact staged rows named
-- by a sealed manifest.
create or replace function public.btw_guard_fact_version()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  atomic boolean := coalesce(
    current_setting('btw.atomic_promotion', true), '') = 'on';
begin
  if tg_op = 'INSERT' then
    if new.fact_state = 'published' and not atomic then
      raise exception 'published % rows must come through btw_promote_review',
        tg_table_name;
    end if;
    return new;
  end if;

  if old.fact_state = 'published' and not atomic then
    raise exception 'published % fact versions are immutable', tg_table_name;
  end if;
  if not atomic and exists (
    select 1
    from public.review_manifest_item mi
    join public.review r on r.id = mi.review_id
    where r.decision = 'pending'
      and ((tg_table_name = 'unit' and mi.unit_id = old.id)
        or (tg_table_name = 'permit' and mi.permit_id = old.id)
        or (tg_table_name = 'event' and mi.event_id = old.id))
  ) then
    raise exception 'fact version % is frozen by a sealed review', old.id;
  end if;
  if new.fact_state = 'published' and old.fact_state <> 'published'
     and not atomic then
    raise exception 'publish % rows through btw_promote_review', tg_table_name;
  end if;
  return new;
end;
$$;

drop trigger if exists guard_unit_fact_version on public.unit;
create trigger guard_unit_fact_version before insert or update on public.unit
for each row execute function public.btw_guard_fact_version();
drop trigger if exists guard_permit_fact_version on public.permit;
create trigger guard_permit_fact_version before insert or update on public.permit
for each row execute function public.btw_guard_fact_version();
drop trigger if exists guard_event_fact_version on public.event;
create trigger guard_event_fact_version before insert or update on public.event
for each row execute function public.btw_guard_fact_version();

-- Never allow promotion or maintenance to destroy the row a receipt names.
create or replace function public.btw_preserve_sourced_fact()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
  if exists (
    select 1 from public.fact_provenance fp
    where fp.fact_table = tg_table_name and fp.fact_id = old.id
  ) then
    raise exception 'cannot delete sourced % fact %; retract it instead',
      tg_table_name, old.id;
  end if;
  return old;
end;
$$;

drop trigger if exists preserve_sourced_unit on public.unit;
create trigger preserve_sourced_unit before delete on public.unit
for each row execute function public.btw_preserve_sourced_fact();
drop trigger if exists preserve_sourced_permit on public.permit;
create trigger preserve_sourced_permit before delete on public.permit
for each row execute function public.btw_preserve_sourced_fact();

-- Database-level history makes direct service-key changes discoverable even
-- when they did not come through the normalizer/review workflow.
create table if not exists public.fact_change_log (
  id uuid primary key default extensions.uuid_generate_v4(),
  fact_table text not null,
  fact_id uuid not null,
  operation text not null check (operation in ('INSERT', 'UPDATE', 'DELETE')),
  before_row jsonb,
  after_row jsonb,
  changed_at timestamptz not null default now(),
  database_role text not null default current_user,
  jwt_subject text,
  transaction_id bigint not null default txid_current()
);
alter table public.fact_change_log enable row level security;
create index if not exists fact_change_log_target_idx
  on public.fact_change_log (fact_table, fact_id, changed_at desc);

create or replace function public.btw_audit_fact_change()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  old_row jsonb := case when tg_op in ('UPDATE', 'DELETE') then to_jsonb(old) end;
  new_row jsonb := case when tg_op in ('INSERT', 'UPDATE') then to_jsonb(new) end;
  row_id uuid := coalesce((new_row ->> 'id')::uuid, (old_row ->> 'id')::uuid);
begin
  insert into public.fact_change_log
    (fact_table, fact_id, operation, before_row, after_row, jwt_subject)
  values
    (tg_table_name, row_id, tg_op, old_row, new_row,
     nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub');
  return case when tg_op = 'DELETE' then old else new end;
end;
$$;

drop trigger if exists audit_unit_changes on public.unit;
create trigger audit_unit_changes after insert or update or delete on public.unit
for each row execute function public.btw_audit_fact_change();
drop trigger if exists audit_permit_changes on public.permit;
create trigger audit_permit_changes after insert or update or delete on public.permit
for each row execute function public.btw_audit_fact_change();
drop trigger if exists audit_provenance_changes on public.fact_provenance;
create trigger audit_provenance_changes
after insert or update or delete on public.fact_provenance
for each row execute function public.btw_audit_fact_change();

comment on column public.unit.total_mw is
  'Directly supported cohort total; do not manufacture mw_each by division.';
comment on column public.unit.basis is
  'Whether the row describes permitted equipment, observed equipment, or third-party reporting.';
comment on column public.unit.verification_state is
  'Fact-level epistemic state. Claim validated means extraction/anchor validated, not that the assertion is true.';
comment on column public.permit.verification_state is
  'Fact-level epistemic state, separate from staging/published workflow state.';
comment on column public.review.manifest_hash is
  'SHA-256 of the exact typed fact-version ids approved by this review.';
comment on table public.review_manifest_item is
  'Immutable typed row-version set sealed before the human review PR is opened.';
comment on table public.fact_change_log is
  'Append-only audit trail for all unit, permit, and provenance mutations.';
