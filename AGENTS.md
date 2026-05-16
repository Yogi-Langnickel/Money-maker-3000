# Money-maker-3000 Agent Instructions

Money-maker-3000 is a Python-first, simulation-only trading-bot worker core.
Treat all work as financial and security-sensitive.

## Rules

- Do not add live trading, live eToro API calls, account mutation, credential
  loading, order previews, execution routes, or provider mutation endpoints.
- Keep all behavior simulation-first. Demo execution remains disabled until a
  separate review explicitly approves it.
- Never commit API keys, user keys, OAuth tokens, account identifiers,
  portfolio exports, screenshots, balances, statements, holdings, position IDs,
  order IDs, transaction history, raw provider payloads, or production `.env`
  files.
- Internal bot allocation must stay separate from provider/demo balances.
  Provider/demo balances are read-only reconciliation inputs only and must
  never size orders or budgets.
- Risk gates must fail closed before any strategy output can become an order
  intent when allocation, provider state, reconciliation, data freshness,
  strategy version/instrument, or risk policy is missing or invalid.
- Do not implement high-frequency trading, scalping, martingale/grid systems,
  social/news-driven orders, copy trading, shorts, leverage above 1, CFDs,
  options, derivatives, or crypto.
- Strategies must come from the predefined registry. Do not allow arbitrary
  browser-uploaded or operator-provided code.
- Market and news context is context only; it must not directly create orders
  or recommendations.
- Audit and ledger records must be append-only at the application layer,
  redacted, synthetic, and free of account-linked provider data.
- Backtest output is diagnostics only. Do not report real PnL, win rate,
  Sharpe ratio, drawdown, execution quality, or profitability claims.
- Use the Python standard library unless a dependency is already present and
  explicitly justified. Avoid network.

## Validation

Run these after changing worker contracts, strategy logic, risk policy,
backtest behavior, market-history parsing, ledger output, or docs examples:

```sh
PYTHONPYCACHEPREFIX=.pycache python3.13 -m compileall src tests
PYTHONPATH=src python3.13 -m unittest discover tests
```

## Memory

Read `docs/agent-memory.md` for the compact current implementation state,
performance constraints, and context-retrieval notes.
