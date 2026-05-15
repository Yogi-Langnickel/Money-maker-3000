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

Unblock questions:

1. What Git remote URL should this repository use?
1. Should completed work target `develop` as the integration branch?
1. Who should review simulation contract changes before merge?

## Provider And Performance Inputs

- No real market history, portfolio state, or reconciliation input is approved.
- Performance metrics must remain synthetic diagnostics only: run counts, veto
  histograms, warnings, and budget ranges.
- Do not add eToro provider calls, credentials, demo execution, live execution,
  real PnL, win-rate, drawdown, or execution-quality reporting until separate
  review gates approve the inputs and storage boundary.

Unblock questions:

1. Which provider inputs are approved first: market history, portfolio state, or
   reconciliation records?
1. Where should approved provider-like fixtures live, and what fields must be
   redacted before commit?
1. What private storage boundary should hold any future account-linked
   simulation or reconciliation data?
