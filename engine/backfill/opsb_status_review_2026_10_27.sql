begin;
insert into public.claim (id,document_id,entity_hint,field,value,value_num,unit,anchor,quote,page,match_score,numeric_check,confidence,extractor_version,status)
values
('30f3ecb7-7417-4301-906a-579ae29a2391','bfa70630-f114-4f61-8baa-57879a5bfa27','Socrates South / 25-0185-EL-BLN','facility.first_power','2026-08-03',null,null,'quote','25-0185-EL-BLN NA Socrates South* 8/3/26 Licking 200',3,1,true,1,'manual-opsb-source-review-2026-10-27','validated'),
('30f3ecb7-7417-4301-906a-579ae29a2392','bfa70630-f114-4f61-8baa-57879a5bfa27','Socrates South / 25-0185-EL-BLN','observation.mw','200 MW gas capacity in OPSB status table',200,'MW','quote','25-0185-EL-BLN NA Socrates South* 8/3/26 Licking 200',3,1,true,1,'manual-opsb-source-review-2026-10-27','validated'),
('30f3ecb7-7417-4301-906a-579ae29a2393','bfa70630-f114-4f61-8baa-57879a5bfa27','STY / 26-0169-EL-BLN','permit.issued_at','2026-06-25',null,null,'quote','26-0169-EL-BLN NA STY Power Generation Facility*¹ 6/25/26 Licking 250',3,1,true,1,'manual-opsb-source-review-2026-10-27','validated'),
('30f3ecb7-7417-4301-906a-579ae29a2394','bfa70630-f114-4f61-8baa-57879a5bfa27','STY / 26-0169-EL-BLN','observation.mw','250 MW gas capacity in OPSB status table',250,'MW','quote','26-0169-EL-BLN NA STY Power Generation Facility*¹ 6/25/26 Licking 250',3,1,true,1,'manual-opsb-source-review-2026-10-27','validated')
on conflict (id) do nothing;
insert into public.event (id,event_date,event_type,headline,source_url,fact_state)
values ('30f3ecb7-7417-4301-906a-579ae29a2395','2026-08-03','operating_status','Socrates South: OPSB September 17, 2026 table lists 200 MW gas generation operational from August 3, 2026. Capacity excludes battery storage; current output is unknown.','https://dam.assets.ohio.gov/image/upload/puco.ohio.gov/empliibrary/files/OPA/Mapping/OPSB/Solar%20Facilities%20Map/Natural_Gas_Map_and_Stats.pdf#page=3','staging')
on conflict(id) do nothing;
commit;
