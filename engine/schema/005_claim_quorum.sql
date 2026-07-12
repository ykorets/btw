-- M3.5 · quorum cross-check per decisions D9.
-- Verdict of the independent second-family extraction pass, recorded on the
-- primary claim. NULL = extracted without quorum (single-model run).
--   agree     — checker confirmed field+value → auto-accept
--   solo      — checker silent on the fact; anchor already vouched for it
--   escalated — checker contradicted, escalation model sided with primary
--   disagree  — contradiction survived escalation; claim held at
--               status='extracted' for human review
alter table claim add column if not exists quorum text
  check (quorum in ('agree', 'solo', 'escalated', 'disagree'));

comment on column claim.quorum is
  'D9 quorum verdict: agree|solo|escalated|disagree; null = quorumless run';
