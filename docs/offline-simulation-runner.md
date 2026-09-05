# Deterministic offline simulation runner

`run-once` only accepts the committed `contracts/offline-simulation-runner-v1.json`. It rechecks and SHA-256-pins the generated dashboard contract, fixture provenance manifest, and selected fixture before acquiring its local fenced lease. It has no provider, credential, account, preview, demo, or live-execution path.

Run a deterministic occurrence from the repository root:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli run-once \
  --manifest contracts/offline-simulation-runner-v1.json \
  --state-path .local/offline-runner-lease.json \
  --ledger-path .local/offline-runner-ledger.jsonl \
  --idempotency-key replay-2026-09-05 \
  --started-at 2026-09-05T00:00:00Z
```

The output separates `preflight.structurallyValid` (manifest, generated-contract, provenance, and parser/risk readiness) from `preflight.strategyAnalysisSufficient` (enough history and sampling for the selected strategy diagnostic). An insufficient analysis remains a safe diagnostics-only, blocked simulation record; it never creates an order intent or execution.

The lease state and ledger are local operational state. Keep them in the ignored `.local/` directory. Use a stable, opaque idempotency key and the same explicit `--started-at` for one scheduled occurrence. Repeating a completed key proves the exact existing redacted record before returning `already-completed`. A crash after an append is recovered only after the lease expires, using that same key and timestamp; it recognizes the exact record and completes without a second append. Corrupt lease state, corrupt ledger state, a clock reversal, a kill switch, or another active key all fail closed and exit non-zero.

The command creates a missing explicitly selected state parent with private permissions. The lease store still rejects a pre-existing group- or world-writable parent.

```sh
install -d -m 700 .local
```

The command initializes a missing lease state itself. Do not edit either state file. To inspect without mutation:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli lease-report .local/offline-runner-lease.json
PYTHONPATH=src python3.13 -m money_maker_3000.cli ledger-report .local/offline-runner-ledger.jsonl
```

`docs/run-once-launchd.plist.template` is a template only. Replace both absolute paths, inspect the command, then install it yourself with `launchctl` if wanted; this repository does not install or load a background service.
