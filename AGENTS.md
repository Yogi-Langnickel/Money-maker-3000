# Money-maker-3000 Agent Instructions

Money-maker-3000 is a simulation-first trading-bot worker scaffold. Treat all
work as financial and security-sensitive.

## Rules

- Do not add live trading, live eToro API calls, account mutation, or secret
  requirements.
- Keep all behavior demo/simulation-first until separate review explicitly
  approves demo execution.
- Never commit API keys, user keys, OAuth tokens, account identifiers,
  portfolio exports, screenshots, balances, statements, or production `.env`
  files.
- Do not implement high-frequency trading, scalping, martingale/grid systems,
  social-feed-driven orders, copy trading, shorts, or leverage above 1.
- Strategies must come from the predefined registry. Do not allow arbitrary
  browser-uploaded or operator-provided code.
- Market and news context is context only; it must not directly create orders
  or recommendations.
- Audit and trade-log records must be redacted and synthetic until a durable
  private store is designed.

## Validation

Run `npm run check` after changing worker contracts or strategy logic.

## Memory

Read `docs/agent-memory.md` for the compact current implementation state,
performance constraints, and context-retrieval notes.
