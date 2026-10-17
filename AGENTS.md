# Contributor instructions

## Local-first checks and release cost (2026-10-17)

- Iterate locally. Run the smallest meaningful checks after an edit; batch commits and pushes around reviewable changes. Do not push just to execute tests.
- Keep unfinished work in a draft PR. Heavy validation starts on `ready_for_review`; a skipped draft job is not passing release evidence. After any source change, verify the new candidate before publication.
- Before review, run the relevant local build, tests and isolated integration checks. Use `.github/LOCAL-CHECKS.md`. Reuse successful results only for unchanged source and unchanged test/dependency inputs.
- Full-site Lighthouse, broad browser/device matrices and recovery rehearsals belong locally or in an explicitly requested diagnostic run. Preserve independent GitHub checks for the final candidate, security boundaries, tenant isolation, migrations and deployment correctness.
- Never raise the Actions budget, rerun a budget-blocked job, replace required checks with local receipts, or bypass a deployment gate to make a release green. Production deployment authorization remains separate.
- Canonical cross-repository policy: https://github.com/ykorets/infrastructure/blob/main/CI-POLICY.md.
