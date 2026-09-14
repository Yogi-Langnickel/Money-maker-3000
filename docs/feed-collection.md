# Separate read-only feed collection

`feed_collection.py` is an opt-in collector separate from the simulation worker
and learning core. It permits only instrument search and daily candle GETs.
Portfolio, identity-of-user, execution and mutation endpoints are absent.
Existing external credentials are read in process without logging, copying or
hashing their values. Profiles require an owner-only regular file with one link;
redirects and proxy forwarding are disabled. Errors contain controlled codes.

Profiles accept exactly one complete naming pair: `ETORO_API_KEY` and
`ETORO_USER_KEY`, or the existing legacy names `ETORO_AGENT_PUBLIC_KEY` and
`ETORO_AGENT_PRIVAT_KEY`. The public/application key maps to `x-api-key`; the
user/private key maps to `x-user-key`. Duplicate keys and any mixture of the two
families are rejected before transport construction, even if values agree. The
profile is never rewritten and shell expressions are never evaluated.

The account holder has authorized ordinary read-only API access for this task.
The official [personal-use guidance](https://builders.etoro.com/use-cases/personal-use)
and [app-registration guidance](https://builders.etoro.com/app-registration),
reviewed 2026-09-14, describe personal research and private learning tools using
personal API keys. No additional user approval is required for the scoped reads.

The current model-training research gate remains **closed**. The official
[Builders Economy terms](https://www.etoro.com/wp-content/uploads/2026/03/Master_eToro_Builders_Economy_Terms_17-Feb-2026-clean_R.pdf)
reviewed 2026-09-14 restrict using Licensed Content for model training, fine-tuning
or grounding (Part V 1.7). Applying this restriction to the probability-model
fitting in this workflow is the implementation's conservative interpretation;
the personal-use pages do not document an exception for that fitting. The existing
gate therefore requires reviewed written model-use evidence before collecting
prices for this training workflow. This is not a blanket storage prohibition:
Part V 1.8 restricts databases beyond Permitted Use, and Part II 2.4(c) limits
caching beyond what is reasonably required. Applicable removal obligations,
including removal within 24 hours on provider request (Part II 3), still apply.

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

The initial 2026-09-14 metadata probe was rejected by automatic approval review
under the earlier repository rule. The account holder then clarified access
authorization, and automatic review accepted the subsequent scoped probe. That
attempt stopped locally with `credential-profile-missing-fields` before HTTP
because the existing profile used the legacy names now supported above. No
credentials were exposed or rewritten. Existing profile metadata was checked:
owner-only regular file, mode 0600, one link. Authentication, entitlement and
instrument availability still require a successful request; an older HTTP 401
is not a current result. No eToro prices were retained and no eToro model trained
by that attempt.

After the compatibility patch passed both persona review iterations, the
authorized metadata-only probe on 2026-09-14 made one SPY instrument-search GET.
HTTP 403 produced the controlled `entitlement-failed` code; QQQ and VAS were
`collection-stopped`, with no further requests. The code describes the response
handling, not a verified account or licensing diagnosis. Key scope, IP/access
policy, or an intermediary such as a WAF remain possible unresolved causes.
A bounded follow-up diagnostic found `cloudflare` and `access denied` markers
in the JSON response and a CF-Ray header, with no WWW-Authenticate header. This
supports an edge-access response interpretation; it does not establish why access
was denied. Raw response text and header identifiers were not retained here.
Instrument availability was not verified. No prices or account data were requested
or retained, and the separate model-training gate remains unchanged.

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
