\set ON_ERROR_STOP on

begin;

grant usage on schema public, extensions to service_role;
grant all on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to service_role;
alter role service_role bypassrls;

insert into public.source (id, kind, url, adapter)
values ('fixture', 'pagehash', 'https://example.test/source', 'fixture');

insert into public.document
  (id, source_id, url, r2_key, sha256, doc_genre)
values
  ('10000000-0000-4000-8000-000000000001', 'fixture',
   'https://example.test/source.pdf', 'docs/fixture.pdf',
   'fixture-sha-256', 'permit');

insert into public.claim
  (id, document_id, field, value, value_num, anchor, quote, page,
   match_score, numeric_check, confidence, extractor_version, status)
values
  ('20000000-0000-4000-8000-000000000001',
   '10000000-0000-4000-8000-000000000001',
   'unit.count', '5', 5, 'quote', 'The permit authorizes five units.', 1,
   1.0, true, 1.0, 'fixture', 'validated');

insert into public.claim
  (id, document_id, field, value, anchor, quote, page, match_score,
   numeric_check, confidence, extractor_version, status)
values
  ('20000000-0000-4000-8000-000000000002',
   '10000000-0000-4000-8000-000000000001',
   'permit.authority', 'TCEQ', 'quote', 'TCEQ issued Permit 177263.', 1,
   1.0, null, 1.0, 'fixture', 'validated'),
  ('20000000-0000-4000-8000-000000000003',
   '10000000-0000-4000-8000-000000000001',
   'permit.no', '177263', 'quote', 'TCEQ issued Permit 177263.', 1,
   1.0, null, 1.0, 'fixture', 'validated'),
  ('20000000-0000-4000-8000-000000000004',
   '10000000-0000-4000-8000-000000000001',
   'permit.status', 'issued', 'quote', 'TCEQ issued Permit 177263.', 1,
   1.0, null, 1.0, 'fixture', 'validated');

insert into public.facility
  (id, slug, name, state, status, fact_state)
values
  ('30000000-0000-4000-8000-000000000001', 'fixture-facility',
   'Fixture Facility', 'TX', 'operating', 'published');

-- Existing production version, seeded with the same capability the migration
-- later reserves for the promotion function.
select set_config('btw.atomic_promotion', 'on', true);
insert into public.unit
  (id, logical_id, facility_id, unit_count, fact_state)
values
  ('40000000-0000-4000-8000-000000000001',
   '41000000-0000-4000-8000-000000000001',
   '30000000-0000-4000-8000-000000000001', 6, 'published');
insert into public.permit
  (id, logical_id, facility_id, authority, permit_no, status, fact_state)
values
  ('50000000-0000-4000-8000-000000000001',
   '51000000-0000-4000-8000-000000000001',
   '30000000-0000-4000-8000-000000000001',
   'TCEQ', '177263', 'filed', 'published');
select set_config('btw.atomic_promotion', 'off', true);

insert into public.unit
  (id, logical_id, facility_id, unit_count, basis, verification_state,
   fact_state)
values
  ('40000000-0000-4000-8000-000000000002',
   '41000000-0000-4000-8000-000000000001',
   '30000000-0000-4000-8000-000000000001', 5, 'permitted',
   'source_asserted', 'staging'),
  -- Created after the manifest below; it must not hitchhike on promotion.
  ('40000000-0000-4000-8000-000000000003',
   '41000000-0000-4000-8000-000000000002',
   '30000000-0000-4000-8000-000000000001', null, null, null, 'staging');

insert into public.permit
  (id, logical_id, facility_id, authority, permit_no, status,
   verification_state, fact_state)
values
  ('50000000-0000-4000-8000-000000000002',
   '51000000-0000-4000-8000-000000000001',
   '30000000-0000-4000-8000-000000000001',
   'TCEQ', '177263', 'issued', 'source_asserted', 'staging');

insert into public.fact_provenance
  (fact_table, fact_id, fact_field, claim_id, support_kind, derivation, note)
values
  ('unit', '40000000-0000-4000-8000-000000000002', 'unit_count',
   '20000000-0000-4000-8000-000000000001', 'direct', null,
   'exact numeric claim'),
  ('unit', '40000000-0000-4000-8000-000000000002', 'basis',
   '20000000-0000-4000-8000-000000000001', 'derived',
   'unit.* permit claim classifies this row as permitted', 'classification'),
  ('unit', '40000000-0000-4000-8000-000000000002', 'verification_state',
   '20000000-0000-4000-8000-000000000001', 'derived',
   'one direct archived source supports source_asserted', 'classification');

insert into public.fact_provenance
  (fact_table, fact_id, fact_field, claim_id, support_kind, derivation, note)
values
  ('permit', '50000000-0000-4000-8000-000000000002', 'authority',
   '20000000-0000-4000-8000-000000000002', 'direct', null, 'exact authority'),
  ('permit', '50000000-0000-4000-8000-000000000002', 'permit_no',
   '20000000-0000-4000-8000-000000000003', 'direct', null, 'exact number'),
  ('permit', '50000000-0000-4000-8000-000000000002', 'status',
   '20000000-0000-4000-8000-000000000004', 'direct', null, 'exact status'),
  ('permit', '50000000-0000-4000-8000-000000000002',
   'verification_state', '20000000-0000-4000-8000-000000000004',
   'derived', 'one direct archived source supports source_asserted',
   'classification');

set role service_role;

-- The service role cannot manufacture a review or attach receipts directly
-- to a published fact version outside the engine RPCs.
do $$
begin
  begin
    insert into public.review (batch_date, decision)
    values ('2026-07-12', 'pending');
    raise exception 'TEST_EXPECTED_FAILURE_MISSING';
  exception when others then
    if sqlerrm = 'TEST_EXPECTED_FAILURE_MISSING'
       or sqlerrm <> 'reviews must come through btw_create_review_manifest' then
      raise;
    end if;
  end;
end;
$$;

do $$
begin
  begin
    insert into public.fact_provenance
      (fact_table, fact_id, fact_field, claim_id)
    values
      ('unit', '40000000-0000-4000-8000-000000000001', 'unit_count',
       '20000000-0000-4000-8000-000000000001');
    raise exception 'TEST_EXPECTED_FAILURE_MISSING';
  exception when others then
    if sqlerrm = 'TEST_EXPECTED_FAILURE_MISSING'
       or sqlerrm not like 'attach provenance to a staging fact version%' then
      raise;
    end if;
  end;
end;
$$;

-- Direct published-row mutation is blocked even for the service role.
do $$
begin
  begin
    update public.unit set unit_count = 99
    where id = '40000000-0000-4000-8000-000000000001';
    raise exception 'TEST_EXPECTED_FAILURE_MISSING';
  exception when others then
    if sqlerrm = 'TEST_EXPECTED_FAILURE_MISSING'
       or sqlerrm not like 'published unit fact versions are immutable%' then
      raise;
    end if;
  end;
end;
$$;

do $$
begin
  begin
    update public.unit set fact_state = 'published'
    where id = '40000000-0000-4000-8000-000000000003';
    raise exception 'TEST_EXPECTED_FAILURE_MISSING';
  exception when others then
    if sqlerrm = 'TEST_EXPECTED_FAILURE_MISSING'
       or sqlerrm <> 'publish unit rows through btw_promote_review' then
      raise;
    end if;
  end;
end;
$$;

select review_id, manifest_hash as sealed_hash
from public.btw_create_review_manifest(
  '2026-07-13',
  array['40000000-0000-4000-8000-000000000002'::uuid],
  array['50000000-0000-4000-8000-000000000002'::uuid],
  '{}'::uuid[])
\gset manifest_

-- Sealing freezes both the staged row and its evidence bundle.
do $$
begin
  begin
    update public.unit set unit_count = 7
    where id = '40000000-0000-4000-8000-000000000002';
    raise exception 'TEST_EXPECTED_FAILURE_MISSING';
  exception when others then
    if sqlerrm = 'TEST_EXPECTED_FAILURE_MISSING'
       or sqlerrm not like 'fact version % is frozen by a sealed review' then
      raise;
    end if;
  end;
end;
$$;

do $$
begin
  begin
    insert into public.fact_provenance
      (fact_table, fact_id, fact_field, claim_id)
    values
      ('unit', '40000000-0000-4000-8000-000000000002', 'unit_count',
       '20000000-0000-4000-8000-000000000001');
    raise exception 'TEST_EXPECTED_FAILURE_MISSING';
  exception when others then
    if sqlerrm = 'TEST_EXPECTED_FAILURE_MISSING'
       or sqlerrm <> 'cannot add evidence to a fact version in a sealed review' then
      raise;
    end if;
  end;
end;
$$;

do $$
begin
  begin
    update public.claim set quote = 'rewritten after review'
    where id = '20000000-0000-4000-8000-000000000001';
    raise exception 'TEST_EXPECTED_FAILURE_MISSING';
  exception when others then
    if sqlerrm = 'TEST_EXPECTED_FAILURE_MISSING'
       or sqlerrm <> 'validated claims are immutable; create a corrected claim' then
      raise;
    end if;
  end;
end;
$$;

-- A wrong hash cannot cause any partial fact transition.
do $$
begin
  begin
    perform public.btw_promote_review(
      (select id from public.review where batch_date = '2026-07-13'),
      repeat('0', 64), repeat('a', 40));
    raise exception 'TEST_EXPECTED_FAILURE_MISSING';
  exception when others then
    if sqlerrm = 'TEST_EXPECTED_FAILURE_MISSING'
       or sqlerrm <> 'review manifest hash mismatch' then
      raise;
    end if;
  end;
end;
$$;

do $$
begin
  if (select fact_state from public.unit
      where id = '40000000-0000-4000-8000-000000000001') <> 'published'
     or (select fact_state from public.unit
         where id = '40000000-0000-4000-8000-000000000002') <> 'staging' then
    raise exception 'wrong-hash attempt changed fact state';
  end if;
  if (select fact_state from public.permit
      where id = '50000000-0000-4000-8000-000000000001') <> 'published'
     or (select fact_state from public.permit
         where id = '50000000-0000-4000-8000-000000000002') <> 'staging' then
    raise exception 'wrong-hash attempt changed permit state';
  end if;
end;
$$;

-- A sealed manifest cannot be edited directly.
do $$
begin
  begin
    update public.review_manifest_item set created_at = now()
    where review_id = (
      select id from public.review where batch_date = '2026-07-13');
    raise exception 'TEST_EXPECTED_FAILURE_MISSING';
  exception when others then
    if sqlerrm = 'TEST_EXPECTED_FAILURE_MISSING'
       or sqlerrm <> 'review manifest items are immutable' then
      raise;
    end if;
  end;
end;
$$;

select public.btw_promote_review(
  :'manifest_review_id'::uuid, :'manifest_sealed_hash', repeat('a', 40));

do $$
begin
  if (select fact_state from public.unit
      where id = '40000000-0000-4000-8000-000000000001') <> 'retracted' then
    raise exception 'prior version was not retracted';
  end if;
  if (select fact_state from public.unit
      where id = '40000000-0000-4000-8000-000000000002') <> 'published' then
    raise exception 'manifest version was not published';
  end if;
  if (select fact_state from public.unit
      where id = '40000000-0000-4000-8000-000000000003') <> 'staging' then
    raise exception 'non-manifest staging row hitchhiked on promotion';
  end if;
  if (select fact_state from public.permit
      where id = '50000000-0000-4000-8000-000000000001') <> 'retracted'
     or (select fact_state from public.permit
         where id = '50000000-0000-4000-8000-000000000002') <> 'published' then
    raise exception 'permit version swap was not atomic';
  end if;
  if (select decision from public.review
      where batch_date = '2026-07-13') <> 'promoted' then
    raise exception 'review was not marked promoted';
  end if;
  if not exists (
    select 1 from public.fact_change_log
    where fact_table = 'unit'
      and fact_id = '40000000-0000-4000-8000-000000000002'
      and operation = 'UPDATE') then
    raise exception 'promotion was not audit logged';
  end if;
end;
$$;

-- Same merge SHA is idempotent.
select public.btw_promote_review(
  :'manifest_review_id'::uuid, :'manifest_sealed_hash', repeat('a', 40));

rollback;
