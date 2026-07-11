-- M4: needed by the SLO check to give never-succeeded sources a grace window.
alter table source add column created_at timestamptz not null default now();
