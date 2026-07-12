# Market History Fixtures

The short SPY, GLD, and QQQ files are public-test market-bar fixtures used for
parser and batch-contract coverage. `spy-slow-trend-202-daily.csv` is explicitly
synthetic: it is a deterministic rising-price window created only to exercise
the default 50/200/3 slow-trend history contract. It is not observed market
data, a backtest profitability claim, or a recommendation.

Every fixture is offline-only and contains no provider credentials, account
data, holdings, balances, positions, orders, transactions, or raw payloads.
