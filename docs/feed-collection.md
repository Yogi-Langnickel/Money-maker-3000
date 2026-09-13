# Separate read-only feed collection

`feed_collection.py` is an opt-in collector separate from the simulation worker
and learning core. It permits only instrument search and daily candle GETs.
Portfolio, identity-of-user, execution and mutation endpoints are absent.
Existing external credentials are read in process without logging, copying or
hashing their values. Profiles require an owner-only regular file with one link;
redirects and proxy forwarding are disabled. Errors contain controlled codes.

The current eToro research rights gate is **closed**. The official
[Builders Economy terms](https://www.etoro.com/wp-content/uploads/2026/03/Master_eToro_Builders_Economy_Terms_17-Feb-2026-clean_R.pdf)
reviewed 2026-09-14 prohibit using Licensed Content to train, fine-tune or ground
models (Part V 1.7), constrain caching outside permitted use (1.8), and require
removal within 24 hours on provider request (Part II 3). General user permission
to retrieve data does not establish an exception to those provider restrictions.
A reviewed written exception covering this research and its retained artifacts
is required before any research collection. No exception has been inferred.

## Endpoint and interpretation evidence

The [official candle schema](https://api-portal.etoro.com/api-reference/market-data/get-instrument-candle-history)
was fetched successfully on 2026-09-14. It documents `OneDay`, `desc` ordering,
a maximum of 1,000 candles and each candle's `fromDate` as its interval start.
The collector requests that bounded available daily window on subsequent refreshes
as well as initial collection, enabling correction detection. The schema has no
date cursor; this implementation does not invent pagination or claim full history.

The shared market-data quota is 120 requests per 60 seconds. This collector adds
a 1.1-second interval and a 12-request run budget; a 429 or exhausted response
quota stops the run without automatic retries. HTTP 401 and 403 stop later
instruments. An absent instrument allows other symbols to continue.

[Instrument search](https://api-portal.etoro.com/api-reference/market-data/search-for-instruments)
resolves exact symbols (`SPY`, `QQQ`, `VAS.ASX`) and checks the expected fund-name
terms, ETF type, and nonempty exchange. VAS aliases are not guessed. Currency is
an expected listing attribute, **not verified by the search response**. Storage
requires a separately reviewed currency interpretation. The immutable version
records the exact resolved instrument mapping alongside interpretation.

The reviewed candle documentation does not settle bid/ask/last-price basis,
corporate-action adjustment, exact trading-session boundaries, exchange closing
auction treatment, or transaction costs. These remain unresolved. A supplied
interpretation must explicitly document price basis and session convention;
unknown values can support investigation but cannot establish cross-provider
compatibility by themselves. No cost adjustment or normalization is performed.

A candle is eligible only after its start plus 26 hours. This conservative
completion policy is not an exchange calendar, and may delay a new observation.
Potential missing weekdays are warnings, not verified missing exchange sessions.
Early closes and holidays need external session evidence for stronger claims.

## Versioned local input

The separate `market-observations.v2` JSON packet contains `symbol`, `source`,
`observations`, `interpretation`, `retention`, and `unresolvedMissingDates`. Every row has ISO `date`, an
aware `timestamp`, required positive finite `close`, and explicit nullable
`open`, `high`, `low`, and `volume`. Available OHLC ranges are validated. A
strategy declaring an absent required field fails closed. Zero volume is retained
when the provider actually supplies zero; missing volume remains null.
`market-history-csv.v1` remains unchanged and strict.

Raw field values represented in the normalized rows remain unchanged. Timestamps
are converted to UTC and dates derive from those timestamps; this convention is
recorded rather than silently claiming equivalence to exchange-session dates.
Unknown extra provider fields are not retained. No raw account-linked payloads
are ever accepted or written.

Snapshots are content-addressed and append-only. A local lock covers comparison,
version publication and retrieval evidence. Publication uses an exclusive
same-directory temporary, fsync and an atomic rename under the exclusive writer lock; interruption before the
retrieval record can be retried. An identical retry returns the previous evidence;
a later unchanged refresh adds no observations or dataset version. Revisions
produce a new snapshot while retaining the old one. Missing previously observed
rows are reported and preserved; their disappearance never silently deletes old
evidence. Conflicting same-date values inside one response fail closed.

Snapshots and their retrieval records belong under ignored `data/private/` with
private directory/file permissions. Source-rights expiry and deletion requests
must block use. eToro approval requires evidence, an expiry and a specific written
model-use exception; current system time is checked as well as retrieval time.
No subscription termination or provider deletion-request detection is automatic.
The operator must update policy when such conditions change and delete all
affected datasets and derived artifacts according to the applicable rights.
FMP's separate deletion obligations are unaffected by this collector.

## Commands and current verification

Metadata-only access verification (no price request or retention):

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.feed_collection \
  --profile /path/to/existing/private/profile --probe-only
```

After an actual reviewed rights exception and verified feed interpretation exist:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.feed_collection \
  --profile /path/to/existing/private/profile \
  --policy /path/to/private/approved-policy.json \
  --interpretations /path/to/private/reviewed-interpretations.json \
  --output-root data/private/market-history/etoro
```

`--ca-file` optionally selects an existing trusted CA bundle. TLS validation
cannot be disabled. No command above authorizes changing provider rights status.

The 2026-09-14 live metadata probe did **not execute**: automatic approval review
rejected authenticated use of the existing profile because the earlier repository
instructions prohibit credential loading/API calls. The user goal authorizes a
scoped separate component, but that conflict needs resolution before retrying the
blocked action. Existing profile metadata was checked without exposing contents:
owner-only regular file, mode 0600, one link. Current authentication/entitlement
and instrument availability remain unverified; an older HTTP 401 is not a
current result. No eToro prices were retained and no eToro model was trained.

Focused synthetic tests cover nullable fields, malformed ranges/numbers,
uncompleted candles, duplicates/conflicts, source mismatch, rights expiry,
revision preservation, replay, interrupted publication, corrupted prior evidence,
private-file checks, blocked endpoints, redacted HTTP failures and stop behavior.

## Reviewed collection preflight contract

`preflight_collection(policy, interpretations, symbols, now)` must run before
constructing `EtoroReader`, loading its profile, or issuing any request. The CLI
and collection function apply this check. Policies must name exactly `etoro`,
contain the written model-use exception, and pass both supplied/current-time
expiry checks. Other-provider policies cannot authorize eToro retrieval.

Each interpretation has exactly `source`, `currency`, `currencyVerified`,
`priceBasis`, `sessionConvention`, `currencyEvidence`, `interpretationEvidence`,
and `instrumentMapping`. Text fields are bounded nonempty strings; verification
is literal true. Currency must match the allowlisted instrument's listing.
The mapping is the exact output shape of `resolve_instrument`, including the
reviewed fund name, ETF type, symbol, identifier, exchange and currency status.
Unknown metadata fields are rejected. Exchanges are bound to reviewed NYSE/Arca
labels for SPY, Nasdaq labels for QQQ, and ASX labels for VAS. A new exchange label
requires review rather than being silently accepted. The resolved live mapping
must equal the supplied reviewed mapping before a candle request can occur.
Evidence identifiers record a review; they do not automatically verify provider
rights or currency documentation.

Atomic rename leaves the canonical file at one link even if the process exits
immediately after publication. A process-crash test verifies this boundary and
successful retry. Nonblocking private-file opens reject FIFOs before reading.

For partial source availability, repeat `--symbol` to select only instruments
with reviewed mappings. With no selector the default remains SPY, QQQ and VAS.
The interpretation file must contain exactly the selected symbols. Unknown and
duplicate selectors fail before credential loading. For example, an approved
SPY-only packet can refresh while VAS remains unavailable:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.feed_collection \
  --profile /path/to/existing/private/profile --symbol SPY \
  --policy /path/to/private/approved-policy.json \
  --interpretations /path/to/private/reviewed-spy-interpretation.json \
  --output-root data/private/market-history/etoro
```

## Withdrawn observations and explicit restoration

`market-retrieval.v2` carries cumulative `unresolvedMissingDates`. An observation
that disappears inside a returned coverage window stays unresolved even when a
later response has a narrower window excluding that date. The immutable snapshot
retains the original bar and the same unresolved-date list. Consumers must mask
those dates when determining available outcome evidence; a preserved bar is not
confirmation that the provider still supplies it.

Only explicit reappearance in a later provider response clears an unresolved
date. The retrieval records `restoredDates` and matching `restorationEvidence`
with the source, original observation timestamp and retrieval time. These records
and the new content-addressed snapshot let scoring record an auditable restoration
without rewriting prior evidence. Identical retries preserve the original report.

A store containing the old `market-retrieval.v1` records fails with
`legacy-retrieval-migration-required`. Such records lack durable withdrawal state;
this implementation does not silently default unknown prior withdrawals to empty.
An explicit reviewed migration is required before reusing such a store.
