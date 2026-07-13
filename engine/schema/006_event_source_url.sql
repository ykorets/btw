-- 006: every event carries its own receipt.
-- Hot-lane inserts must set source_url (the document that triggered the
-- event); backfill moved trailing "[url]" markers out of headline text
-- into the column. Applied to Supabase 2026-07-13.

alter table event add column if not exists source_url text;

update event set
  source_url = (regexp_match(headline, '\[(https?://[^\]]+)\]'))[1],
  headline   = regexp_replace(headline, '\s*\[https?://[^\]]+\]', '')
where headline ~ '\[https?://[^\]]+\]';
