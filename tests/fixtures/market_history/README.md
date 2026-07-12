# Market History Fixtures

The short SPY, GLD, and QQQ files are public-test market-bar fixtures used for
parser and batch-contract coverage. They do not carry sufficient provenance to
support observed-market claims.

The scenario fixtures below are explicitly synthetic and SHA-256 pinned in CLI
tests:

| Fixture | Diagnostic purpose | SHA-256 |
| --- | --- | --- |
| `spy-slow-trend-202-daily.csv` | Default 50/200/3 slow-trend window | `ecaec707c5bc6dccc05f0ed5b52f1110ba08a0e0431c59e0d9223baf9ae546d9` |
| `spy-volatility-stable-20-daily.csv` | Default stable/no-trigger window | `7e7aa8344d04b62a09d0f1dfb87ba218bad982ec8476e5bcd40666c0859fa56c` |
| `spy-volatility-decline-20-daily.csv` | Default active-decline trigger window | `5621200eb3e8b3d0b87ea7479812c38ad1fa46aeca308ea654043bf0f76c8d5c` |
| `spy-volatility-recovery-20-daily.csv` | Default recovery-after-decline window | `8566f55c59ffd4f0f29c0b228422079d70dc90707345bbf922a87772ffdb076d` |
| `vas-au-etf-synthetic-20-daily.csv` | AU equity/ETF instrument-contract coverage | `6e7c464580d94a33f2f14613e7301af99af7a513e82ee8a71d92c782caf82c4b` |

These files are deterministic contract inputs, not observed market data,
backtest profitability evidence, or recommendations. Do not relabel them as
public-provider data.

Every fixture is offline-only and contains no provider credentials, account
data, holdings, balances, positions, orders, transactions, or raw payloads.
