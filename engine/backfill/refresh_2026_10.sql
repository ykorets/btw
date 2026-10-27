-- Reviewed authoring batch, 2026-10-27. Run only after exact documents are archived.
-- No schema, roles, keys, published unit versions or aggregate changes.
-- If a response is lost, read deterministic IDs and audit rows before any retry.
begin;
do $$ begin
  if exists (select 1 from public.claim where extractor_version='owner-authorized-primary-review-2026-10-27') then
    raise exception 'Refresh already started: reconcile existing rows before retry';
  end if;
  if not exists (select 1 from public.permit where id='08f316a4-b896-4bd8-aad0-6d4db6e2d02f' and fact_state='staging' and verification_state is null) then
    raise exception 'O4721 staging baseline changed';
  end if;
  if exists (select 1 from public.review_manifest_item mi join public.review r on r.id=mi.review_id where r.decision='pending' and mi.permit_id='08f316a4-b896-4bd8-aad0-6d4db6e2d02f') then
    raise exception 'O4721 already sealed by another review';
  end if;
end $$;
do $$ declare doc uuid; begin
  select id into strict doc from public.document where sha256='908e869b003eae12ac20db300a0959f473d4ba5f862ae1d359bb38a1e148cc0f';
  insert into public.claim (id,document_id,entity_hint,field,value,value_num,anchor,quote,page,match_score,numeric_check,confidence,extractor_version,status)
  values ('81b0566b-271b-5f4b-922b-7b8d58973669',doc,'permit O4721','permit.no','O4721',null,'quote','Permit No:  O4721',1,1.0,null,1.0,'owner-authorized-primary-review-2026-10-27','validated');
end $$;
do $$ declare doc uuid; begin
  select id into strict doc from public.document where sha256='908e869b003eae12ac20db300a0959f473d4ba5f862ae1d359bb38a1e148cc0f';
  insert into public.claim (id,document_id,entity_hint,field,value,value_num,anchor,quote,page,match_score,numeric_check,confidence,extractor_version,status)
  values ('732c6285-d6bc-5e6d-9b92-b3a3d5f065a3',doc,'permit O4721','permit.type','Federal Operating Permit',null,'quote','FEDERAL OPERATING PERMIT',1,1.0,null,1.0,'owner-authorized-primary-review-2026-10-27','validated');
end $$;
do $$ declare doc uuid; begin
  select id into strict doc from public.document where sha256='908e869b003eae12ac20db300a0959f473d4ba5f862ae1d359bb38a1e148cc0f';
  insert into public.claim (id,document_id,entity_hint,field,value,value_num,anchor,quote,page,match_score,numeric_check,confidence,extractor_version,status)
  values ('3aad4ab0-62e1-5cd7-a52a-d8e70a66bfa8',doc,'permit O4721','permit.status','issued',null,'quote','A FEDERAL OPERATING PERMIT IS HEREBY ISSUED TO',1,1.0,null,1.0,'owner-authorized-primary-review-2026-10-27','validated');
end $$;
do $$ declare doc uuid; begin
  select id into strict doc from public.document where sha256='908e869b003eae12ac20db300a0959f473d4ba5f862ae1d359bb38a1e148cc0f';
  insert into public.claim (id,document_id,entity_hint,field,value,value_num,anchor,quote,page,match_score,numeric_check,confidence,extractor_version,status)
  values ('4c54ff75-4c13-56a0-b392-c15d8fc2079f',doc,'permit O4721','permit.issued_at','2026-01-16',null,'quote','Issuance Date:  January 16, 2026',1,1.0,null,1.0,'owner-authorized-primary-review-2026-10-27','validated');
end $$;
do $$ declare doc uuid; begin
  select id into strict doc from public.document where sha256='908e869b003eae12ac20db300a0959f473d4ba5f862ae1d359bb38a1e148cc0f';
  insert into public.claim (id,document_id,entity_hint,field,value,value_num,anchor,quote,page,match_score,numeric_check,confidence,extractor_version,status)
  values ('f93ae947-fbe3-5f55-aa43-3397132c752a',doc,'permit O4721','permit.authority','TCEQ',null,'quote','Texas Commission on Environmental Quality (TCEQ)',3,1.0,null,1.0,'owner-authorized-primary-review-2026-10-27','validated');
end $$;
do $$ declare doc uuid; begin
  select id into strict doc from public.document where sha256='908e869b003eae12ac20db300a0959f473d4ba5f862ae1d359bb38a1e148cc0f';
  insert into public.claim (id,document_id,entity_hint,field,value,value_num,anchor,quote,page,match_score,numeric_check,confidence,extractor_version,status)
  values ('97c7259d-83bb-543f-8735-b4f97985f8fd',doc,'permit 177263','permit.issued_at','2025-01-22',null,'quote','Authorization No.:  177263 Issuance Date:  01/22/2025',42,1.0,null,1.0,'owner-authorized-primary-review-2026-10-27','validated');
end $$;
do $$ declare doc uuid; begin
  select id into strict doc from public.document where sha256='d137070b957b1a356aba850bac2b863989ab035e62201f8f7196f044fc8eb805';
  insert into public.claim (id,document_id,entity_hint,field,value,value_num,anchor,quote,page,match_score,numeric_check,confidence,extractor_version,status)
  values ('8afa9370-b789-50ff-b885-58a00387adb3',doc,'permit 177263','facility.first_permit_filed','2024-08-15',null,'quote','Project Received DateAugust 15, 2024',1,1.0,null,1.0,'owner-authorized-primary-review-2026-10-27','validated');
end $$;
do $$ declare doc uuid; begin
  select id into strict doc from public.document where sha256='62734b21f553899dfc3abb82bd8b2ef3de34357b29bb09522e24d0f9ba155fe4';
  insert into public.claim (id,document_id,entity_hint,field,value,value_num,anchor,quote,page,match_score,numeric_check,confidence,extractor_version,status)
  values ('1011bfa1-2394-5bbc-934a-95621f4e9960',doc,'permit 0680-00119','permit.no','0680-00119',null,'quote','Permit No.: 0680-00119',1,1.0,null,1.0,'owner-authorized-primary-review-2026-10-27','validated');
end $$;
do $$ declare doc uuid; begin
  select id into strict doc from public.document where sha256='62734b21f553899dfc3abb82bd8b2ef3de34357b29bb09522e24d0f9ba155fe4';
  insert into public.claim (id,document_id,entity_hint,field,value,value_num,anchor,quote,page,match_score,numeric_check,confidence,extractor_version,status)
  values ('90b95705-041e-55c9-8850-b7a46468f2e6',doc,'permit 0680-00119','permit.issued_at','2026-03-11',null,'quote','Issued:____March 11, 2026_____',1,1.0,null,1.0,'owner-authorized-primary-review-2026-10-27','validated');
end $$;
update public.permit set permit_type='Federal Operating Permit', issued_at='2026-01-16', verification_state='source_asserted',updated_at=now()
where id='08f316a4-b896-4bd8-aad0-6d4db6e2d02f' and fact_state='staging';
insert into public.permit (id,facility_id,authority,permit_no,permit_type,status,filed_at,issued_at,fact_state,logical_id,verification_state)
select 'b2259f4a-1485-4b77-ae22-627917b2aa41',facility_id,authority,permit_no,permit_type,status,filed_at,'2025-01-22'::date,'staging',logical_id,verification_state
from public.permit where id='790f1d91-bb3f-4e91-bb08-a2b2104f4237' and fact_state='published';
insert into public.fact_provenance (id,fact_table,fact_id,fact_field,claim_id,note,support_kind,derivation)
select md5('btw-20261027:'||id::text||':b2259f4a-1485-4b77-ae22-627917b2aa41')::uuid,fact_table,'b2259f4a-1485-4b77-ae22-627917b2aa41',fact_field,claim_id,note,support_kind,derivation
from public.fact_provenance where fact_table='permit' and fact_id='790f1d91-bb3f-4e91-bb08-a2b2104f4237';
insert into public.permit (id,facility_id,authority,permit_no,permit_type,status,filed_at,issued_at,fact_state,logical_id,verification_state)
select 'dbd77727-9b0e-49bc-b240-a1fe856c5cbb',facility_id,authority,permit_no,permit_type,status,filed_at,issued_at,'staging',logical_id,verification_state
from public.permit where id='541dbf47-3967-4865-8cd4-55109d1dbbdd' and fact_state='published';
insert into public.fact_provenance (id,fact_table,fact_id,fact_field,claim_id,note,support_kind,derivation)
select md5('btw-20261027:'||id::text||':dbd77727-9b0e-49bc-b240-a1fe856c5cbb')::uuid,fact_table,'dbd77727-9b0e-49bc-b240-a1fe856c5cbb',fact_field,claim_id,note,support_kind,derivation
from public.fact_provenance where fact_table='permit' and fact_id='541dbf47-3967-4865-8cd4-55109d1dbbdd';
insert into public.fact_provenance (fact_table,fact_id,fact_field,claim_id,note,support_kind) values ('permit','08f316a4-b896-4bd8-aad0-6d4db6e2d02f','permit_no','81b0566b-271b-5f4b-922b-7b8d58973669','Primary document rechecked 2026-10-27; exact quote validated locally','direct');
insert into public.fact_provenance (fact_table,fact_id,fact_field,claim_id,note,support_kind) values ('permit','08f316a4-b896-4bd8-aad0-6d4db6e2d02f','permit_type','732c6285-d6bc-5e6d-9b92-b3a3d5f065a3','Primary document rechecked 2026-10-27; exact quote validated locally','direct');
insert into public.fact_provenance (fact_table,fact_id,fact_field,claim_id,note,support_kind) values ('permit','08f316a4-b896-4bd8-aad0-6d4db6e2d02f','status','3aad4ab0-62e1-5cd7-a52a-d8e70a66bfa8','Primary document rechecked 2026-10-27; exact quote validated locally','direct');
insert into public.fact_provenance (fact_table,fact_id,fact_field,claim_id,note,support_kind) values ('permit','08f316a4-b896-4bd8-aad0-6d4db6e2d02f','issued_at','4c54ff75-4c13-56a0-b392-c15d8fc2079f','Primary document rechecked 2026-10-27; exact quote validated locally','direct');
insert into public.fact_provenance (fact_table,fact_id,fact_field,claim_id,note,support_kind) values ('permit','08f316a4-b896-4bd8-aad0-6d4db6e2d02f','authority','f93ae947-fbe3-5f55-aa43-3397132c752a','Primary document rechecked 2026-10-27; exact quote validated locally','direct');
insert into public.fact_provenance (fact_table,fact_id,fact_field,claim_id,note,support_kind) values ('permit','b2259f4a-1485-4b77-ae22-627917b2aa41','issued_at','97c7259d-83bb-543f-8735-b4f97985f8fd','Primary document rechecked 2026-10-27; exact quote validated locally','direct');
insert into public.fact_provenance (fact_table,fact_id,fact_field,claim_id,note,support_kind) values ('facility','bcf8230c-2df9-4383-84df-d8c1c7f80af8','first_permit_filed','8afa9370-b789-50ff-b885-58a00387adb3','Primary document rechecked 2026-10-27; exact quote validated locally','direct');
insert into public.fact_provenance (fact_table,fact_id,fact_field,claim_id,note,support_kind) values ('permit','dbd77727-9b0e-49bc-b240-a1fe856c5cbb','permit_no','1011bfa1-2394-5bbc-934a-95621f4e9960','Primary document rechecked 2026-10-27; exact quote validated locally','direct');
insert into public.fact_provenance (fact_table,fact_id,fact_field,claim_id,note,support_kind) values ('permit','dbd77727-9b0e-49bc-b240-a1fe856c5cbb','issued_at','90b95705-041e-55c9-8850-b7a46468f2e6','Primary document rechecked 2026-10-27; exact quote validated locally','direct');
insert into public.fact_provenance (fact_table,fact_id,fact_field,claim_id,note,support_kind,derivation) values ('permit','08f316a4-b896-4bd8-aad0-6d4db6e2d02f','verification_state','81b0566b-271b-5f4b-922b-7b8d58973669','Primary review 2026-10-27','derived','one archived issued permit supports this version');
-- Facility date fields predate immutable unit/permit versioning. Preserve the complete
-- before/after rows in the existing audit ledger; exact old-value guards stop drift.
do $$ declare before_value jsonb; after_value jsonb; begin
 select to_jsonb(f) into strict before_value from public.facility f where slug='crusoe-stargate-abilene' and first_permit_filed='2024-08-01' and first_power='2025-09-01' for update;
 update public.facility set first_permit_filed='2024-08-15',first_power=null,updated_at=now() where slug='crusoe-stargate-abilene' returning to_jsonb(facility.*) into after_value;
 insert into public.fact_change_log(fact_table,fact_id,operation,before_row,after_row) values ('facility',(before_value->>'id')::uuid,'UPDATE',before_value,after_value);
 select to_jsonb(f) into strict before_value from public.facility f where slug='xai-colossus-1' and first_power='2024-07-01' for update;
 update public.facility set first_power=null,updated_at=now() where slug='xai-colossus-1' returning to_jsonb(facility.*) into after_value;
 insert into public.fact_change_log(fact_table,fact_id,operation,before_row,after_row) values ('facility',(before_value->>'id')::uuid,'UPDATE',before_value,after_value);
end $$;
insert into public.event(id,facility_id,event_date,event_type,headline,source_url,fact_state) select '0bca12ad-f96e-5cfb-a3e0-b318a08ebc2f',id,'2026-10-27','record_reviewed','Reviewed TCEQ records: original registration 177263 application received August 15, 2024; amendment issued January 22, 2025; Federal Operating Permit O4721 issued January 16, 2026. The 360.5 MW equipment cohort is a permit-based figure, not verified current output. Exact first-power day remains unverified.','https://www.tceq.texas.gov/assets/public/permitting/air/publicnotice/37589sop.pdf','staging' from public.facility where slug='crusoe-stargate-abilene';
insert into public.event(id,facility_id,event_date,event_type,headline,source_url,fact_state) select 'f5dfd151-e7cf-50fb-b19b-dca2aebf1034',id,'2026-10-27','record_reviewed','Checked Shelby County public notices and its October 6, 2026 notice; that notice contains no CTC/xAI update. The published 15-unit, 247.5 MW cohort remains a historical reported configuration; current installed count and delivered power were not established by this check.','https://www.shelbytnhealth.com/DocumentCenter/View/7965','staging' from public.facility where slug='xai-colossus-1';
insert into public.event(id,facility_id,event_date,event_type,headline,source_url,fact_state) select '67e445b3-6c29-5aab-b6c4-2012590c615c',id,'2026-10-27','record_reviewed','Reviewed final MDEQ permit 0680-00119, issued March 11, 2026, and the current registry. The permit lists 41 turbines in three groups (17, 16 and 8). The separate 27-unit, 495 MW cohort comes from a historical court filing; it is not a current full-site operating total.','https://opcgis.deq.state.ms.us/enOnline/get_doc.aspx?dt=finalp&id=1834338','staging' from public.facility where slug='xai-colossus-2';
insert into public.event(id,facility_id,event_date,event_type,headline,source_url,fact_state) select '54b48deb-fee4-5740-a591-9802d635c602',id,'2025-09-30','operator_report','Crusoe reported that the first Abilene phase was running on Oracle Cloud Infrastructure and that its first two buildings had been energized within a year. This establishes operation by the report date, not an exact first day of BTM generation.','https://www.crusoe.ai/resources/newsroom/crusoe-announces-flagship-abilene-data-center-is-live','staging' from public.facility where slug='crusoe-stargate-abilene';
insert into public.event(id,facility_id,event_date,event_type,headline,source_url,fact_state) select '981a3017-ffa8-5564-8451-911ddfc07722',id,'2024-08-15','permit_filed','TCEQ original technical review records August 15, 2024 as the project received date for registration 177263. This corrects the legacy August 1 date; January 11, 2025 relates to the later modification application.','https://records.tceq.texas.gov/cs/idcplg?IdcService=TCEQ_EXTERNAL_SEARCH_GET_FILE&Rendition=Web&dID=8143786','staging' from public.facility where slug='crusoe-stargate-abilene';
commit;
