-- Review lifecycle hardening.
-- Pending manifests remain immutable, but expected pipeline replays hold
-- instead of failing and rejected reviews can retire their exact staging rows
-- without destroying the audit snapshot.

alter table public.review
  add column if not exists rejected_at timestamptz;
alter table public.review
  add column if not exists rejection_reason text;

-- Exact fact versions are immutable snapshots. Once a version has appeared
-- in any review it is never valid to submit that same version again: promoted
-- versions are published and rejected versions are retracted. These indexes
-- are the race-safe backstop for concurrent manifest creation.
create unique index if not exists review_manifest_unit_version_once
  on public.review_manifest_item (unit_id) where unit_id is not null;
create unique index if not exists review_manifest_permit_version_once
  on public.review_manifest_item (permit_id) where permit_id is not null;
create unique index if not exists review_manifest_event_version_once
  on public.review_manifest_item (event_id) where event_id is not null;

-- A fact version may be named by at most one pending review. The per-review
-- unique indexes from 008 prevent duplicates inside one manifest; this trigger
-- closes the cross-review gap while preserving same-manifest idempotency.
create or replace function public.btw_preserve_review_manifest()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  parent_hash text;
  parent_decision text;
  conflicting_review uuid;
  target_id uuid;
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

  target_id := coalesce(new.unit_id, new.permit_id, new.event_id);
  select r.id into conflicting_review
  from public.review_manifest_item mi
  join public.review r on r.id = mi.review_id
  where r.decision = 'pending'
    and mi.review_id <> new.review_id
    and ((new.unit_id is not null and mi.unit_id = new.unit_id)
      or (new.permit_id is not null and mi.permit_id = new.permit_id)
      or (new.event_id is not null and mi.event_id = new.event_id))
  limit 1;
  if found then
    raise exception 'fact version % already belongs to pending review %',
      target_id, conflicting_review;
  end if;
  return new;
end;
$$;

-- PR URL updates remain legal. Decisions and their audit metadata may change
-- only inside the atomic promotion/rejection capabilities.
create or replace function public.btw_preserve_review_boundary()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  transition boolean :=
    coalesce(current_setting('btw.atomic_promotion', true), '') = 'on'
    or coalesce(current_setting('btw.review_transition', true), '') = 'on';
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
  if new.decision is distinct from old.decision and not transition then
    raise exception 'review decisions change only through btw_promote_review or btw_reject_review';
  end if;
  if not transition and (
       new.merge_commit_sha is distinct from old.merge_commit_sha
       or new.promoted_at is distinct from old.promoted_at
       or new.rejected_at is distinct from old.rejected_at
       or new.rejection_reason is distinct from old.rejection_reason) then
    raise exception 'review transition metadata is immutable';
  end if;
  return new;
end;
$$;

create or replace function public.btw_reject_review(
  p_review_id uuid,
  p_manifest_hash text,
  p_reason text
)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  review_row public.review%rowtype;
  actual_hash text;
  unit_count integer := 0;
  permit_count integer := 0;
  event_count integer := 0;
begin
  if nullif(btrim(p_reason), '') is null then
    raise exception 'rejection reason is required';
  end if;

  select r.* into review_row
  from public.review r
  where r.id = p_review_id
  for update;
  if not found then
    raise exception 'review % not found', p_review_id;
  end if;

  actual_hash := public.btw_review_manifest_hash(p_review_id);
  if actual_hash is distinct from review_row.manifest_hash
     or actual_hash is distinct from p_manifest_hash then
    raise exception 'review manifest hash mismatch';
  end if;

  if review_row.decision = 'rejected' then
    return jsonb_build_object(
      'status', 'already_rejected',
      'review_id', p_review_id,
      'manifest_hash', actual_hash);
  end if;
  if review_row.decision <> 'pending' then
    raise exception 'review % is %, expected pending',
      p_review_id, review_row.decision;
  end if;

  -- The same transaction-local capability used by promotion allows only the
  -- exact guarded transitions below. Direct REST callers cannot set it.
  perform set_config('btw.review_transition', 'on', true);
  perform set_config('btw.atomic_promotion', 'on', true);

  update public.unit u
  set fact_state = 'retracted', updated_at = now()
  from public.review_manifest_item mi
  where mi.review_id = p_review_id
    and mi.unit_id = u.id
    and u.fact_state = 'staging';
  get diagnostics unit_count = row_count;

  update public.permit p
  set fact_state = 'retracted', updated_at = now()
  from public.review_manifest_item mi
  where mi.review_id = p_review_id
    and mi.permit_id = p.id
    and p.fact_state = 'staging';
  get diagnostics permit_count = row_count;

  update public.event e
  set fact_state = 'retracted'
  from public.review_manifest_item mi
  where mi.review_id = p_review_id
    and mi.event_id = e.id
    and e.fact_state = 'staging';
  get diagnostics event_count = row_count;

  update public.review
  set decision = 'rejected',
      rejected_at = now(),
      rejection_reason = btrim(p_reason)
  where id = p_review_id;

  return jsonb_build_object(
    'status', 'rejected',
    'review_id', p_review_id,
    'manifest_hash', actual_hash,
    'units', unit_count,
    'permits', permit_count,
    'events', event_count);
end;
$$;

revoke all on function public.btw_reject_review(uuid, text, text)
  from public, anon, authenticated;
grant execute on function public.btw_reject_review(uuid, text, text)
  to service_role;

comment on function public.btw_reject_review(uuid, text, text) is
  'Atomically rejects a sealed pending review and retracts its exact staging fact versions.';
