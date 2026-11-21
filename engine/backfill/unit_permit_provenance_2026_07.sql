-- HISTORICAL AUDIT RECORD — ALREADY APPLIED TO PROJECT txglwhwnmjtbijbgcpwd
-- on 2026-07-13 before the truth-integrity audit was completed.
--
-- DO NOT RERUN.  This file is retained so the exact prior mutations remain
-- inspectable.  Several links below satisfy row-level coverage but fail the
-- newer semantic field-compatibility gate.  Reconciliation must now happen
-- through staged, reviewed fact versions, not another direct backfill.
--
-- Historical mutation included below: Southaven was changed from 15.6 to
-- 495/27 = 18.333333 MW.  The later audit determined that this is not a valid
-- per-unit fact: the source asserts only a cohort total of at least 495 MW.

do $$
begin
  raise exception 'historical audit record only; do not apply';
end
$$;

begin;

update public.unit
set mw_each = round(495::numeric / 27, 6)
where id = 'f64f2d33-118b-475d-bde7-f863fae0e20f'
  and fact_state = 'published'
  and mw_each = 15.6;

-- Remove assumption-only utilization values. They can return when a permit
-- quote explicitly binds 8,760 hours to these exact models.
update public.unit
set hours_permitted = null
where id in (
  'f9f56928-18c9-4142-8234-a968348fbd9d'::uuid,
  '7a4e004d-915c-4c05-aa9f-848a2b051eff'::uuid
)
  and fact_state = 'published'
  and hours_permitted = 8760
  and not exists (
    select 1 from public.fact_provenance fp
    where fp.fact_table = 'unit'
      and fp.fact_id = public.unit.id
      and fp.fact_field = 'hours_permitted'
  );

-- The permit cover is dated January 15, but both the archived court filing
-- and MDEQ's registry identify March 11 as the issuance date. Keep the
-- published field tied to the explicit issuance claim, not the cover date.
update public.permit
set issued_at = date '2026-03-11',
    status = 'issued'
where id = 'bd5051fc-52b5-4dff-9187-1ee639aee92d'
  and fact_state = 'published'
  and issued_at = date '2026-01-15';

-- Do not publish precision that the archive cannot currently substantiate.
update public.permit
set filed_at = null
where id in (
  'aa81f6c4-6cb3-47c0-8a08-d161c3edb967'::uuid,
  '1252d749-4d30-440b-a6a8-7cdb2b944abf'::uuid
)
  and fact_state = 'published'
  and filed_at is not null
  and not exists (
    select 1 from public.fact_provenance fp
    where fp.fact_table = 'permit'
      and fp.fact_id = public.permit.id
      and fp.fact_field = 'filed_at'
  );

update public.permit
set status = 'issued; under appeal'
where id = '1252d749-4d30-440b-a6a8-7cdb2b944abf'
  and fact_state = 'published'
  and status = 'issued; under appeal; CAA litigation';

-- O4721 is verified in the official TCEQ permit found during this audit, but
-- remains staging until that newly queued PDF is archived and extracted.
update public.permit
set fact_state = 'staging'
where id = '08f316a4-b896-4bd8-aad0-6d4db6e2d02f'
  and fact_state = 'published'
  and not exists (
    select 1 from public.fact_provenance fp
    where fp.fact_table = 'permit'
      and fp.fact_id = public.permit.id
  );

-- Human-reviewed exact sentence from the already archived TCEQ technical
-- review. The automatic extractor captured the permit number and unit facts
-- but skipped this non-numeric status sentence.
insert into public.claim
  (document_id, entity_hint, field, value, value_num, unit, anchor, quote,
   page, match_score, numeric_check, confidence, extractor_version, status)
select
  'f597c26a-4522-4504-ae40-88df73d9d5aa'::uuid,
  'permit 177263', 'permit.status', 'currently permitted', null, null,
  'quote',
  'The electric generating units are currently permitted under Standard Permit Registration 177263.',
  1, 1, null, 1, 'manual-review@2026-07-13', 'validated'
where not exists (
  select 1 from public.claim c
  where c.document_id = 'f597c26a-4522-4504-ae40-88df73d9d5aa'
    and c.field = 'permit.status'
    and c.value = 'currently permitted'
    and c.quote = 'The electric generating units are currently permitted under Standard Permit Registration 177263.'
    and c.status = 'validated'
);

delete from public.fact_provenance
where fact_table = 'permit'
  and fact_id = 'bd5051fc-52b5-4dff-9187-1ee639aee92d'
  and (
    (fact_field = 'issued_at'
     and claim_id = '84682ece-0a47-4df5-bb64-de173272f9f8')
    or
    (fact_field = 'status'
     and claim_id = 'a967aa1e-13f4-48f2-a001-ffa38d9c4461')
  );

with desired(fact_table, fact_id, fact_field, claim_id, note) as (
  values
    -- Abilene / GE LM2500
    ('unit', '675f5400-5b08-4a55-8ce4-e82c4288f5dd'::uuid, 'oem',
     'd97e00bb-2885-4740-93f7-f9a75c32268c'::uuid,
     'permit names GE LM2500; supports the published GE Vernova manufacturer label'),
    ('unit', '675f5400-5b08-4a55-8ce4-e82c4288f5dd'::uuid, 'mw_each',
     '4b3634ff-276d-4625-8f1f-bfbcd30f6903'::uuid,
     'direct 34.1 MW-per-turbine quote; entity_hint identifies GE LM2500'),
    ('unit', '675f5400-5b08-4a55-8ce4-e82c4288f5dd'::uuid, 'hours_permitted',
     'cd93d626-557e-4150-b41f-3005058c4390'::uuid,
     'direct 8,760 hours/year quote; entity_hint identifies GE LM2500'),

    -- Memphis / permitted SMT-130 fleet
    ('unit', '7a4e004d-915c-4c05-aa9f-848a2b051eff'::uuid, 'oem',
     'fc1acd47-6223-4e50-a5e8-ed6d773c857f'::uuid,
     'direct Solar SMT-130 quote; Solar is the permit/source manufacturer label'),
    ('unit', '7a4e004d-915c-4c05-aa9f-848a2b051eff'::uuid, 'mw_each',
     '098d86af-ad2c-42d4-8bb4-74e414d97558'::uuid,
     'direct table row: Solar SMT-130, 16.5 MW each'),
    ('unit', '7a4e004d-915c-4c05-aa9f-848a2b051eff'::uuid, 'fuel',
     '9c54ea1e-65e5-4835-b817-a50e6856d140'::uuid,
     'quote explicitly identifies the 15 Solar SMT-130 units as gas turbines'),

    -- Southaven / documented 27-unit cohort
    ('unit', 'f64f2d33-118b-475d-bde7-f863fae0e20f'::uuid, 'unit_count',
     '0c91b20f-9d07-4ed3-8e35-7b396a7d179e'::uuid,
     'aerial photographs confirmed 27 turbines on site on March 3'),
    ('unit', 'f64f2d33-118b-475d-bde7-f863fae0e20f'::uuid, 'mw_each',
     '9213ef68-5486-443f-9e9e-10ee671fddea'::uuid,
     'derived cohort average: at least 495 MW / 27 turbines = 18.333333 MW'),
    ('unit', 'f64f2d33-118b-475d-bde7-f863fae0e20f'::uuid, 'fuel',
     '0c8a82e7-f585-4223-b4e7-3cb3292228b0'::uuid,
     'court filing explicitly identifies the Southaven equipment as gas-fired turbines'),

    -- TCEQ registration 177263. The technical review supports the number,
    -- authority and permit class, but not the published filed/issued state.
    ('permit', 'aa81f6c4-6cb3-47c0-8a08-d161c3edb967'::uuid, 'permit_no',
     'fd62a2fa-8897-48c3-b3a7-c339c002e2f6'::uuid,
     'direct TCEQ registration number'),
    ('permit', 'aa81f6c4-6cb3-47c0-8a08-d161c3edb967'::uuid, 'authority',
     'fd62a2fa-8897-48c3-b3a7-c339c002e2f6'::uuid,
     'official records.tceq.texas.gov registration document'),
    ('permit', 'aa81f6c4-6cb3-47c0-8a08-d161c3edb967'::uuid, 'permit_type',
     '1a8f7f2c-614d-479c-bb64-ded520cc582b'::uuid,
     'document identifies the project as a Standard Permit Application'),

    -- SCHD 01156-01PC
    ('permit', '1252d749-4d30-440b-a6a8-7cdb2b944abf'::uuid, 'permit_no',
     'ccc517a4-72da-4330-998b-a7ca86bcc72b'::uuid,
     'direct Air Permit number'),
    ('permit', '1252d749-4d30-440b-a6a8-7cdb2b944abf'::uuid, 'authority',
     'ad73c005-6fbf-41ec-aa14-9799b767313e'::uuid,
     'direct Shelby County Health Department quote'),
    ('permit', '1252d749-4d30-440b-a6a8-7cdb2b944abf'::uuid, 'permit_type',
     'ccc517a4-72da-4330-998b-a7ca86bcc72b'::uuid,
     'quote explicitly identifies the record as an Air Permit'),
    ('permit', '1252d749-4d30-440b-a6a8-7cdb2b944abf'::uuid, 'issued_at',
     '56cebdf2-8477-44e6-ba27-26f3d9083694'::uuid,
     'direct July 2, 2025 issuance quote'),
    ('permit', '1252d749-4d30-440b-a6a8-7cdb2b944abf'::uuid, 'status',
     'b591e9c6-5c66-4ab4-9162-ecf9722c5949'::uuid,
     'supports the issued component of the compound published status'),
    ('permit', '1252d749-4d30-440b-a6a8-7cdb2b944abf'::uuid, 'status',
     'ccc517a4-72da-4330-998b-a7ca86bcc72b'::uuid,
     'the archived document is the appeal filing for this exact permit; supports under appeal'),

    -- MDEQ 0680-00119
    ('permit', 'bd5051fc-52b5-4dff-9187-1ee639aee92d'::uuid, 'permit_no',
     'f8ad09fd-c6ec-4d99-aa47-a983a8916e89'::uuid,
     'direct permit number on the MDEQ permit cover'),
    ('permit', 'bd5051fc-52b5-4dff-9187-1ee639aee92d'::uuid, 'authority',
     '855cd2fc-4fa2-4065-b988-19286649c4a7'::uuid,
     'direct Mississippi Department of Environmental Quality (MDEQ) quote'),
    ('permit', 'bd5051fc-52b5-4dff-9187-1ee639aee92d'::uuid, 'issued_at',
     '4ef07cd3-8f8d-4b7d-9094-16b8087c34f4'::uuid,
     'direct archived quote: MDEQ issued MZX a permit on March 11, 2026'),
    ('permit', 'bd5051fc-52b5-4dff-9187-1ee639aee92d'::uuid, 'permit_type',
     'a967aa1e-13f4-48f2-a001-ffa38d9c4461'::uuid,
     'permit cover states TO CONSTRUCT AIR EMISSIONS EQUIPMENT'),
    ('permit', 'bd5051fc-52b5-4dff-9187-1ee639aee92d'::uuid, 'permit_type',
     '9ad1e566-4318-44c7-8490-86fd0cfbe528'::uuid,
     '41-turbine envelope component: 17 PGM-130 units'),
    ('permit', 'bd5051fc-52b5-4dff-9187-1ee639aee92d'::uuid, 'permit_type',
     '4d7a3e59-fddc-46f4-b2bd-a803813bf7b0'::uuid,
     '41-turbine envelope component: 16 Titan 350 units'),
    ('permit', 'bd5051fc-52b5-4dff-9187-1ee639aee92d'::uuid, 'permit_type',
     'a6208b5a-8fd2-46c6-8e0f-0447e90c589d'::uuid,
     '41-turbine envelope component: 8 ProEnergy 6000PE units'),
    ('permit', 'bd5051fc-52b5-4dff-9187-1ee639aee92d'::uuid, 'status',
     'c3c9e930-d502-4f23-be7f-22a98c517a5f'::uuid,
     'direct archived quote supports issued status')
), valid_desired as (
  select d.*
  from desired d
  join public.claim c on c.id = d.claim_id and c.status = 'validated'
)
insert into public.fact_provenance
  (fact_table, fact_id, fact_field, claim_id, note)
select d.fact_table, d.fact_id, d.fact_field, d.claim_id, d.note
from valid_desired d
where not exists (
  select 1
  from public.fact_provenance fp
  where fp.fact_table = d.fact_table
    and fp.fact_id = d.fact_id
    and fp.fact_field = d.fact_field
    and fp.claim_id = d.claim_id
);

insert into public.fact_provenance
  (fact_table, fact_id, fact_field, claim_id, note)
select 'permit',
       'aa81f6c4-6cb3-47c0-8a08-d161c3edb967'::uuid,
       'status', c.id,
       'archived technical review explicitly says registration 177263 is currently permitted'
from public.claim c
where c.document_id = 'f597c26a-4522-4504-ae40-88df73d9d5aa'
  and c.field = 'permit.status'
  and c.value = 'currently permitted'
  and c.status = 'validated'
  and not exists (
    select 1 from public.fact_provenance fp
    where fp.fact_table = 'permit'
      and fp.fact_id = 'aa81f6c4-6cb3-47c0-8a08-d161c3edb967'
      and fp.fact_field = 'status'
      and fp.claim_id = c.id
  );

insert into public.aggregate (metric, value, method, inputs_note)
select 'operating_gw',
       round(sum(u.unit_count * u.mw_each) / 1000, 2),
       'sum(unit_count*mw_each) over published operating facilities; recomputed after provenance correction',
       'Southaven documented cohort corrected to >=495 MW (27 turbines)'
from public.unit u
join public.facility f on f.id = u.facility_id
where u.fact_state = 'published'
  and f.fact_state = 'published'
  and f.status = 'operating'
having not exists (
  select 1
  from public.aggregate a
  where a.metric = 'operating_gw'
    and a.inputs_note = 'Southaven documented cohort corrected to >=495 MW (27 turbines)'
);

commit;
