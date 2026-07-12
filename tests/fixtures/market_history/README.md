# Market History Fixtures

The short SPY, GLD, and QQQ files are synthetic market-bar fixtures used for
parser and batch-contract coverage. They are not observed-market data.

The scenario fixtures below are explicitly synthetic and pinned in the
canonical fixture-provenance manifest and CLI tests:

| Fixture | Diagnostic purpose |
| --- | --- |
| `spy-slow-trend-202-daily.csv` | Default 50/200/3 slow-trend window |
| `spy-volatility-stable-20-daily.csv` | Default stable/no-trigger window |
| `spy-volatility-decline-20-daily.csv` | Default active-decline trigger window |
| `spy-volatility-recovery-20-daily.csv` | Default recovery-after-decline window |
| `vas-au-etf-synthetic-20-daily.csv` | AU equity/ETF instrument-contract coverage |

Run this from the repository root after any fixture change:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.fixture_provenance --check
```

The generated artifact is
`contracts/market-history-fixture-provenance.json`.

These files are deterministic contract inputs, not observed market data,
backtest profitability evidence, or recommendations. Do not relabel them as
public-provider data.

Every fixture is offline-only and contains no provider credentials, account
data, holdings, balances, positions, orders, transactions, or raw payloads.
