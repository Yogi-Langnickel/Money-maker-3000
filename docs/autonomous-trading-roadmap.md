# Autonomous trading roadmap

Status: planning only, 2026-09-22.

Money Maker is currently simulation-only. No demo or live execution, account
access, provider mutation, or capital allocation is authorized or implemented.
The eventual product direction is autonomous operation within a dedicated,
bounded allocation, but every move into an execution stage requires explicit
authorization at that stage.

## Evidence standard

A 70–80% hit rate is an aspirational research target, not a release criterion,
profitability claim, or authorization to trade. A release decision must instead
consider all of the following:

- prospective, out-of-sample evidence on genuinely later observations;
- expected net result after documented costs and slippage assumptions;
- enforced loss and drawdown caps appropriate to the approved allocation; and
- a reproducible decision and evidence ledger that permits independent review.

Offline refinement may generate candidate strategies. The active execution
version remains frozen throughout an evaluation period, and a candidate can be
promoted only through an auditable reviewed gate that records the evidence,
parameters, allocation limit, risk policy, and approval.

## Staged path

1. **Data and rights gates.** Verify instrument identity and feed meaning,
   including currency, sessions, timestamps, price basis, adjustments, and
   costs. Obtain rights for retention and model use before retaining provider
   observations or fitting a model from them.
2. **Forward and shadow decisions.** Freeze a strategy version and risk policy;
   record prospective decisions and outcomes without execution. Score only when
   genuinely later approved observations mature.
3. **Approved demo pilot.** After explicit authorization, operate only against
   a designated demo environment with a bounded allocation, documented costs,
   loss/drawdown caps, reconciliation, and a reviewed rollback plan.
4. **Explicit real-capital pilot.** After a separate explicit authorization,
   use a small dedicated real allocation with fixed strategy and risk versions,
   predeclared loss limits, reconciliation, monitoring, and immediate rollback
   criteria.
5. **Monitored autonomous operation.** Expand only after pilot evidence and a
   new reviewed authorization. Keep allocation bounds, data freshness checks,
   reconciliation, an append-only evidence ledger, alerts, and an operator kill
   switch active. A kill switch must stop new execution immediately and require
   an explicit reviewed re-enable decision.

No stage may claim that historical or cross-provider agreement proves
predictive ability. Provider agreement measures robustness to a data source;
forward evidence on new observations remains the relevant test.
