# Offline simulation operations

These controls operate only on the approved offline runner's private lease state and redacted v2 ledger. They do not load credentials, contact providers, inspect account data, create orders, or enable demo/live execution.

Check freshness without changing the lease or ledger records. On first use the
ledger's shared-reader lock sidecar may be created; this is the only status
command filesystem side effect.

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli operations-status \
  --state-path .local/offline-runner-lease.json \
  --ledger-path .local/offline-runner-ledger.jsonl \
  --max-age-hours 48 --observed-at 2026-09-05T12:00:00Z
```

Create an atomic snapshot and retain only the newest 30 validated snapshots:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli operations-snapshot \
  --state-path .local/offline-runner-lease.json \
  --ledger-path .local/offline-runner-ledger.jsonl \
  --snapshot-root .local/snapshots --retain 30 --created-at 2026-09-05T12:00:00Z
```

Restore verification never overwrites an appliance. It requires a new target directory, validates snapshot digests, then verifies the restored lease and ledger there. The recovery drill is also isolated and proves the crash-after-ledger-append path without a duplicate append.

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli operations-recovery-drill \
  --manifest contracts/offline-simulation-runner-v1.json --started-at 2026-09-05T00:00:00Z
PYTHONPATH=src python3.13 -m money_maker_3000.cli operations-soak-evidence \
  --manifest contracts/offline-simulation-runner-v1.json --days 30 --started-at 2026-09-05T00:00:00Z
```

The soak command is deterministic simulation evidence over at most 30 scheduled days; it is not a claim of 30 wall-clock days of service. A genuine wall-clock soak remains an operator activity, monitored with `operations-status` and snapshots.

No service is installed by this repository or any ordinary runner command. `operations-install-launchd` is the only installation action, requires `--confirm-install`, refuses overwrite, and rolls back its plist if `launchctl bootstrap` fails. Run it only after reviewing the generated schedule and choosing a private LaunchAgents directory; this delivery does not invoke it.
