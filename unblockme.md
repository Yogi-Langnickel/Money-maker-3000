# Money-maker-3000 Unblock Notes

Status: non-blocking for simulation implementation  
Created: 2026-05-15

Simulation work can continue locally. These items block remote PR flow,
provider mode, or any execution capability.

## Git Flow

- No remote is configured for this repository.
- Current implementation work is on `feature/simulation-worker-scaffold`.
- Before a PR/review flow exists, configure a remote and decide whether
  `develop` should be the integration branch.

## Provider And Performance Inputs

- No real market history, portfolio state, or reconciliation input is approved.
- Performance metrics must remain synthetic diagnostics only: run counts, veto
  histograms, warnings, and budget ranges.
- Do not add eToro provider calls, credentials, demo execution, live execution,
  real PnL, win-rate, drawdown, or execution-quality reporting until separate
  review gates approve the inputs and storage boundary.
