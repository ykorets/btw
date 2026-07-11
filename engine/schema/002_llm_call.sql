-- M2.5: LLM cost ledger (applied to Supabase 2026-07-11 via MCP)
create table llm_call (
  id uuid primary key default uuid_generate_v4(),
  called_at timestamptz not null default now(),
  role text not null,
  model text not null,
  purpose text not null,
  document_id uuid references document(id),
  prompt_tokens int,
  completion_tokens int,
  cost_usd numeric(12,6),
  meta jsonb
);
create index on llm_call (called_at);
create index on llm_call (model, called_at);
alter table llm_call enable row level security;

create view llm_cost_weekly as
select date_trunc('week', called_at)::date as week,
       model,
       count(*) as calls,
       sum(prompt_tokens) as prompt_tokens,
       sum(completion_tokens) as completion_tokens,
       round(sum(cost_usd), 4) as cost_usd
from llm_call
group by 1, 2
order by 1 desc, 6 desc;
