---
name: money-maker-learn
description: Coordinate configured Money Maker strategy research, pending forward evaluation, status, or offline replay; preserve legacy fixed-split learning when no cycle is configured. Use for "$money-maker-learn", "run a learning session", or training the Money Maker bot on available local data.
---

# Money Maker learning

Run the helper instead of assembling training commands:

```sh
python3.13 /Users/yogi/.codex/skills/money-maker-learn/scripts/learn.py
```

When `data/private/market-history/continuous-research-config.json` exists, the
helper runs the tracked coordinator: refresh only its explicitly configured,
rights-approved read-only collector; validate local histories; score matured
predictions; run eligible bounded research; save new predictions; report status.
An unavailable source remains explicit while eligible local sources continue.
Use `--status` for read-only status, `--replay` for offline integrity replay of the
configured cycle, or `--legacy` for the original verified fixed-split workflow.
Configured replay verifies immutable records and fitted-model contracts; it does
not recompute historical metrics. The legacy replay retains its existing checks.
Never manufacture eToro data or a rights exception. Its current terms gate must
pass before credential access or retained/model use.

The helper discovers the unique clean `develop` worktree from
`/Users/yogi/Coding/projects/Money-maker-3000`, reads its approved local inventory,
and, when no cycle is configured, trains both supported strategy-state probability models where sample minimums
permit. It uses observed-attested data by default, a fixed five-observed-bar label
horizon, and chronological 60%/20%/20% split cutoffs chosen before scoring. Labels
crossing split boundaries are purged by the underlying pipeline.

Use the named bounded developer agent required by workspace instructions to run
the helper when it will create retained plans/models/reports. A learning request authorizes those local artifacts and the cycle's previously
authorized, explicitly configured read-only refresh. It does not newly authorize
Git publication, another provider, account activity, or strategy-code changes.

## Inputs and repeat sessions

The inventory is `data/private/market-history/learning-inventory.json` in that
worktree. Its exact structure is:

```json
{
  "version": "money-maker-learning-inventory.v1",
  "datasets": [
    {
      "csvPath": "source/spy.csv",
      "manifestPath": "source/spy.dataset.json",
      "source": "source-slug",
      "symbol": "SPY",
      "retention": {"policy": "source-terms"}
    }
  ]
}
```

Paths are relative to the market-history directory and must stay inside it with
no symlink components. The source and symbol must match each dataset's existing
`learning-dataset.v1` manifest. There may be 1–20 entries. For the manifest format,
read the discovered repository's `docs/learning-pipeline.md` when adding or
repairing data. Do not invent source evidence or relabel synthetic fixtures.

Models, immutable plans, and completion records stay under ignored
`.local/learning/skill-sessions/`. Identical data, strategy, and split configuration
reuse an existing validated artifact, including when the inventory changes to
include additional datasets. Reuse is a replay, not another training experiment.
An incomplete/corrupt run blocks instead of automatically repeating its holdout;
inspect the failure before deciding any recovery. Do not delete artifacts or
change dates to seek a better score.

Missing/unapproved data is a concrete input blocker. Report it; do not broadly
search disks or download a replacement automatically. A refresh requires an
explicit request and a source-specific provenance workflow. Legacy learning, configured status, and configured replay use no network. A
normal configured cycle can use only its previously authorized read-only collector. Keep observed data, private manifests, models, and reports ignored.
`--allow-synthetic-smoke` is solely an explicit testing option; never use it to
satisfy a request to train on real history.

## FMP retention

FMP approval permits private research while the subscription is active. Its raw
responses, caches, normalized datasets, hashes/encodings, and this skill's full
models/plans/reports are all subject to deletion within 30 days after subscription
termination. Only separately reviewed, nonreconstructive aggregate statistics
that cannot substitute for FMP data may be retained beyond that; do not assume
these full artifacts qualify.

FMP inventory entries must carry:

```json
"retention": {
  "policy": "fmp-active-subscription-delete-within-30-days",
  "subscriptionStatus": "active",
  "terminationDate": null
}
```

The active state is an operator attestation, not automatic subscription or API
entitlement verification. Any non-active status or non-null termination date
blocks that source before loading its data, including cached models. Other
approved sources can continue; the report is `partial` and exits nonzero when a
source is blocked. No eligible sources returns `blocked`. Do not switch a known
FMP source to another retention policy or relabel it to bypass this boundary.

Subscription termination is not detected automatically. When notified, record it
in the inventory and carry out separately authorized, bounded cleanup of all FMP
source artifacts within the deadline. The helper never deletes data. Non-FMP
entries use `{"policy":"source-terms"}`; this preserves their applicable terms,
and is not a claim of perpetual reuse permission.

## Explain the result

Report which datasets and strategies ran or were skipped, date/row coverage,
training/validation/holdout counts, and holdout Brier error versus the train-only
prior. Lower Brier error means lower squared forecast-probability error; it is
not bot return or evidence of profitability. Call out replay status, one-class
training, constant features, sparse samples, and the unverified provenance
attestation. Keep `researchReady: false` and all execution boundaries intact.

Link the saved model when useful. Configured cycles record immutable diagnostic predictions as authorized by the
cycle configuration. Legacy learning does not automatically invoke prediction.
Neither workflow changes risk policy, accounts, execution, or strategy code.
A supported provider comparison means robustness to source choice, never an
independent confirmation of predictive ability. Genuine confirmation requires
new outcomes after the forecast was recorded.

See the discovered repository's `docs/continuous-research-protocol.md` for the frozen five-observation protocol, current limitations, and private configuration contract.
