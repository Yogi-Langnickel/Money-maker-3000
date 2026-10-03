"""Frozen long-only simulation accounting; no provider/account dependencies."""
from __future__ import annotations

from decimal import Decimal, localcontext, DecimalException
import math
from .signal_toolkit import utc_timestamp

VERSION = 'economic-portfolio.v1'
POLICY = {'initialCash': 10000.0, 'reserveFraction': .2, 'maxExposure': .8,
          'lossStop': .1, 'drawdownStop': .15, 'costBps': 10.0,
          'probabilityThreshold': .55, 'cadenceObservations': 5}


class EconomicError(ValueError):
    """Controlled, value-blind failure."""


def require(condition, code):
    if not condition:
        raise EconomicError(code)


def number(value, low=0, high=1e12):
    try:
        return type(value) in (int, float) and math.isfinite(value) and low <= value <= high
    except OverflowError:
        return False


def checked(value):
    require(value.is_finite() and abs(value) <= Decimal('1e100'), 'economic-arithmetic-overflow')
    result = float(value)
    require(math.isfinite(result), 'economic-arithmetic-overflow')
    return result


def validate_policy(policy):
    require(type(policy) is dict and set(policy) == set(POLICY), 'economic-invalid-policy')
    require(number(policy['initialCash'], 1, 1e12), 'economic-invalid-capital')
    require(all(number(policy[key], 0, 1) for key in ('reserveFraction', 'maxExposure', 'lossStop', 'drawdownStop', 'probabilityThreshold')), 'economic-invalid-limit')
    require(0 < policy['lossStop'] < 1 and 0 < policy['drawdownStop'] < 1
            and 0 < policy['maxExposure'] <= 1 - policy['reserveFraction']
            and .5 < policy['probabilityThreshold'] < 1, 'economic-invalid-limit')
    require(number(policy['costBps'], 0, 1000) and type(policy['cadenceObservations']) is int
            and 5 <= policy['cadenceObservations'] <= 20, 'economic-invalid-cost-or-cadence')


def validate_rows(rows):
    require(type(rows) is list and 1 <= len(rows) <= 10000, 'economic-invalid-history-size')
    prior = None
    for row in rows:
        require(type(row) is dict and {'date', 'start', 'end', 'availableAt', 'open', 'high', 'low', 'close'} <= set(row), 'economic-missing-ohlc-or-time')
        start, end, available = [utc_timestamp(row[key]) for key in ('start', 'end', 'availableAt')]
        require(start < end <= available and (end-start).total_seconds() >= 240*60 and (prior is None or start >= prior), 'economic-invalid-time-order')
        require(all(number(row[field], 1e-12) for field in ('open', 'high', 'low', 'close')), 'economic-invalid-price')
        require(row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high'], 'economic-inconsistent-ohlc')
        prior = end


def simulate(rows, forecasts, policy=None, *, passive=False):
    """Fill frozen forecasts at the first *later* bar open after availability.

    Decision index -1 is allowed only for the passive comparison, whose first
    open and final close match the candidate window. Stops observe close and
    sell at the next executable open; the stop threshold is never a fill price.
    Equity is liquidation value throughout, so exit cost is consistently marked.
    """
    policy = dict(POLICY) if policy is None else policy
    validate_policy(policy); validate_rows(rows)
    require(type(passive) is bool and type(forecasts) is list, 'economic-invalid-forecasts')
    identities = set()
    for forecast in forecasts:
        require(type(forecast) is dict and set(forecast) == {'index', 'availableAt', 'probabilityUp'}, 'economic-invalid-forecast')
        index = forecast['index']
        require(type(index) is int and -1 <= index < len(rows) and index not in identities
                and number(forecast['probabilityUp'], 0, 1), 'economic-invalid-forecast')
        require(index >= 0 or utc_timestamp(forecast['availableAt']) <= utc_timestamp(rows[0]['start']), 'economic-forecast-after-first-open')
        require(index < 0 or utc_timestamp(forecast['availableAt']) >= utc_timestamp(rows[index]['availableAt']), 'economic-forecast-before-input')
        identities.add(index)
    ordered = sorted(forecasts, key=lambda item:item['index'])
    for previous, current in zip(ordered, ordered[1:]):
        require((previous['index'] == -1 or current['index']-previous['index'] >= policy['cadenceObservations'])
                and (utc_timestamp(current['availableAt'])-utc_timestamp(previous['availableAt'])).total_seconds() >= 240*60,
                'economic-forecast-cadence-rejected')
    try:
        with localcontext() as context:
            context.prec = 40
            D = lambda value: Decimal(str(value))
            initial = D(policy['initialCash']); cash = initial; quantity = D(0)
            cost = D(policy['costBps']) / D(10000); target = min(D(policy['maxExposure']), 1-D(policy['reserveFraction']))
            fees = D(0); turnover = D(0); peak = initial; max_dd = D(0)
            stopped = False; pending = None; ledger = []; curve = []; exposure = D(0)
            lookup = {forecast['index']: forecast for forecast in forecasts}
            if -1 in lookup:
                first = lookup[-1]
                pending = (target if first['probabilityUp'] >= policy['probabilityThreshold'] else D(0), first['availableAt'])
            for index, row in enumerate(rows):
                price = D(row['open'])
                if passive and index == 0:
                    pending = (target, None)
                if pending is not None and (pending[1] is None or utc_timestamp(pending[1]) <= utc_timestamp(row['start'])):
                    weight = D(0) if stopped else pending[0]
                    equity = cash + quantity * price
                    position = quantity * price
                    # Exact fee-aware target weight; buys preserve the cash reserve.
                    if weight * equity > position:
                        gross = (weight * equity - position) / (1 + weight * cost)
                        gross = min(gross, cash / (1 + cost))
                        cash -= gross * (1 + cost); quantity += gross / price; side = 'buy'
                    else:
                        gross = (position - weight * equity) / (1 - weight * cost)
                        gross = min(gross, position)
                        cash += gross * (1-cost); quantity = D(0) if gross == position else quantity-gross/price; side = 'sell'
                    checked(cash); checked(quantity)
                    require(cash >= 0 and quantity >= 0, 'economic-capital-conservation-failure')
                    if gross:
                        fees += gross * cost; turnover += gross
                        ledger.append({'type': side, 'bar': index, 'time': row['start'], 'fill': row['open'],
                                       'notional': checked(gross), 'cost': checked(gross*cost), 'simulatedCash': checked(cash),
                                       'simulatedQuantity': checked(quantity), 'roundTripClosed': side == 'sell' and quantity == 0})
                    pending = None
                mark = quantity * D(row['close'])
                liquidation = cash + mark * (1-cost)
                checked(liquidation); checked(fees); checked(turnover)
                peak = max(peak, liquidation); dd = (peak-liquidation)/peak; max_dd = max(max_dd, dd)
                exposure += mark/(cash+mark) if mark else 0
                if not passive and (liquidation <= initial*(1-D(policy['lossStop'])) or dd >= D(policy['drawdownStop'])):
                    stopped = True
                    pending = (D(0), row['availableAt'])
                elif not passive and index in lookup:
                    forecast = lookup[index]
                    pending = (target if forecast['probabilityUp'] >= policy['probabilityThreshold'] else D(0), forecast['availableAt'])
                if not passive and mark and mark/(cash+mark) > target and (pending is None or pending[0] > target):
                    # Price drift can exceed the target intrabar. Rebalance only
                    # at the next causal open; never rewrite the observed mark.
                    pending = (target if not stopped else D(0), row['availableAt'])
                curve.append({'time': row['end'], 'simulatedLiquidationEquity': checked(liquidation),
                              'simulatedCash': checked(cash), 'simulatedQuantity': checked(quantity)})
            terminal = quantity > 0
            if terminal:
                gross = quantity * D(rows[-1]['close']); fees += gross*cost; turnover += gross
                cash += gross*(1-cost); quantity = D(0)
                ledger.append({'type': 'terminal-sell', 'bar': len(rows)-1, 'time': rows[-1]['end'],
                               'fill': rows[-1]['close'], 'notional': checked(gross), 'cost': checked(gross*cost),
                               'simulatedCash': checked(cash), 'simulatedQuantity': 0.0, 'roundTripClosed': True})
            final = checked(cash)
            return {'version': VERSION, 'simulated': True, 'initialCash': float(initial), 'finalCash': final,
                    'simulatedNetReturn': checked((cash-initial)/initial), 'simulatedMaxDrawdown': checked(max_dd),
                    'simulatedTurnover': checked(turnover/initial), 'simulatedMeanExposure': checked(exposure/D(len(rows))),
                    'simulatedCosts': checked(fees), 'terminalLiquidated': terminal, 'riskStopped': stopped,
                    'ledger': ledger, 'equity': curve, 'accountingPolicy': 'passive-buy-and-hold-initial-limits-drift-disclosed' if passive else 'causal-rebalance-and-close-observed-stops',
                    'currencyConversion': 'absent', 'providerBalances': 'absent'}
    except (DecimalException, OverflowError, ZeroDivisionError):
        raise EconomicError('economic-arithmetic-overflow') from None
