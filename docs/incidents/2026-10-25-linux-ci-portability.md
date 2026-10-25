# Incident Review: macOS Temporary Paths Broke Ubuntu CI

Date: 2026-10-25
Status: draft
Severity: high
Type: test-portability and review-gate

## Summary and impact

Continuous research tests created temporary directories beneath `/private/tmp`.
That directory exists on the development Mac but is absent on the Ubuntu CI
runner. Test setup failed before the affected research assertions could run.
Local macOS test passes and verified publication were subsequently reported
without checking the corresponding remote CI result.

Fifteen consecutive CI runs were red between the first failing run and the
latest inspected run. The latest run executed 389 tests and reported 24 errors
in each of the three Python jobs. This establishes a test portability and
completion-reporting failure; it does not establish a production trading,
provider, financial-data or credential incident. Passing unaffected tests does
not validate assertions that failed during setup.

## Evidence and timeline

- 2026-09-07 05:18 AEST: [CI run 9](https://github.com/Yogi-Langnickel/Money-maker-3000/actions/runs/34054497148)
  was the last green run before the regression.
- 2026-09-14 02:35 AEST: [CI run 10](https://github.com/Yogi-Langnickel/Money-maker-3000/actions/runs/34769034124),
  at commit `d2b430ece0606975806b6ff43338955428714aaf`, first failed. Its 299-test
  suite reported 20 errors caused by missing `/private/tmp`. Git blame attributes
  all four current hardcoded temporary-directory calls to this commit.
- 2026-10-25 13:59 AEDT: [CI run 24](https://github.com/Yogi-Langnickel/Money-maker-3000/actions/runs/40156522442),
  at commit `139c67b8c97b8a8549b6a5e71ae630643c8c78fa`, still failed with the same
  missing-directory cause: 24 errors in each Python 3.11/3.12/3.13 job.
- 2026-10-25: the failure was investigated and a portable test setup, behavioral
  regression and mandatory remote CI closeout declaration were prepared.

## Root cause and why it was missed

The tests selected a directory spelling from one operating system instead of
asking Python for its system temporary base. Using plain `/tmp` would also be
insufficient on macOS: the EvidenceStore intentionally rejects symlink ancestors.
The fixture needs a canonical system temporary path and private permissions.

Local validation ran on macOS, where the hardcoded path existed. Reviews accepted
those local results without a behavioral test for a custom or aliased temporary
base. The existing Ubuntu workflow detected the defect, but delivery handoffs
did not require its run URL, exact head SHA or matrix conclusions. Closeout
machine checks established remote publication SHA equality without checking CI.
That gap let local test counts be treated as sufficient completion evidence while
the remote workflow remained red. The problem was not an absent Linux workflow;
it was a platform assumption combined with an unenforced evidence boundary.

## Remediation

- All four affected call sites use one test helper that canonicalizes
  `tempfile.gettempdir()` before creating a standard private TemporaryDirectory.
- A behavioral regression supplies both a custom base and a symlinked alias.
  It creates a real EvidenceStore, locks it, writes and reads an immutable record,
  and verifies directory/file permissions of 0700/0600. Production symlink and
  private-storage checks remain unchanged.
- `.github/closeout-ci.json` declares the required push-to-`develop` workflow and
  its three named Python jobs. Project instructions require remote CI success
  for the exact published SHA and distinguish it from local validation.
- The workspace closeout automatically checks the committed declaration via
  `scripts/ci-status.py`, including linked worktrees. Missing, pending, failed or
  unknown evidence prevents a completed delivery result.

The Ubuntu matrix and its compilation, full tests, generated-contract and fixture
provenance checks are retained. No tests are skipped and no OS-specific directory
is created to conceal the failure. No worker, strategy, collector, credential,
private research input or installed skill behavior changes in this repair.

## Verification and closure

Developer validation passed local compilation, all 390 unittests,
contract-manifest drift, fixture-provenance drift and whitespace checks on the
repair branch. The complete suite took 97 seconds. Validation must also run on
integrated `develop`; these local results remain macOS evidence.

Closure additionally requires the newly published SHA's actual completed,
successful Ubuntu push run, including successful `Python 3.11`, `Python 3.12`
and `Python 3.13` jobs. The final delivery report records that SHA, run URL and
job conclusions; this static review does not predict a future run result or
retroactively change historical runs. A green repair run proves that revision's
checks passed, not that all business behavior is correct.

## Prevention and transferability

- Regression owner: Money Maker developer/reviewers; retain the custom and
  symlinked temporary-base test alongside the private-store tests.
- Delivery owner: orchestrator; run automatic closeout and include separate
  local-validation, published-SHA and remote-CI evidence in the final report.
- Project memory records the distinction; workspace closeout and handoff rules
  carry the general gate so remembering an optional command is unnecessary.
- Category: `workspace-general`. Other repositories can declare their required
  workflow/jobs through the committed marker; no GitHub settings or secrets
  changes are part of this repair.
- Propagation: coordinated in the [workspace incident review](https://github.com/Yogi-Langnickel/codex-workspace-orchestration/blob/develop/docs/incidents/2026-10-25-money-maker-ci-closeout.md)
  and canonical workspace CI closeout/handoff tooling.
