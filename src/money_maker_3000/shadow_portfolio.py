"""Private prospective simulations: frozen decisions, later fills, correction ledger."""
from __future__ import annotations

from datetime import timedelta
from . import autonomous_research as A, signal_toolkit as S
from .economic_portfolio import require, simulate


def availability(store, dates, *, available):
    require(type(available) is bool and type(dates) is list and dates == sorted(set(dates)), 'shadow-invalid-availability')
    for date in dates:
        A.L._iso(date)
    with store.locked():
        prior = store.records('availability')
        latest = {}
        for record in prior:
            latest[record['payload']['date']] = record
        result = []
        for date in dates:
            if date in latest and latest[date]['payload']['available'] == available:
                result.append(latest[date]['id']); continue
            result.append(store.append('availability', {'date': date, 'available': available,
                          'supersedes': latest[date]['id'] if date in latest else None})['id'])
        _repair_reference(store)
        return {'eventIds': result, 'state': 'restored' if available else 'withdrawn'}


def unavailable_dates(store):
    latest = {}
    for record in store.records('availability'):
        latest[record['payload']['date']] = record['payload']['available']
    return {date for date, available in latest.items() if not available}


def active_reference(store):
    records = store.records('reference')
    return records[-1] if records else None


def _repair_reference(store):
    active = active_reference(store)
    if active is None or active['payload']['modelId'] is None:
        return
    model_id = active['payload']['modelId']
    scores = [record for record in store.records('score') if record['payload']['modelId'] == model_id]
    dates = unavailable_dates(store)
    checkpoints = [record for record in store.records('checkpoint') if record['payload']['modelId'] == model_id]
    latest_checkpoint = checkpoints[-1] if checkpoints else None
    checkpoint_score = next((score for score in scores if latest_checkpoint and score['id'] == latest_checkpoint['payload']['scoreId']), None)
    material_revision = bool(scores and checkpoint_score and any(scores[-1]['payload'].get(key) != checkpoint_score['payload'].get(key)
                             for key in ('candidate','passive','uncertainty','confirmationWindowDates','genuineProspective')))
    rejected_current = bool(latest_checkpoint and scores and latest_checkpoint['payload']['scoreId'] == scores[-1]['id']
                            and latest_checkpoint['payload']['status'] != 'evidence-qualified-monitoring-candidate')
    relevant_dates = set()
    if scores:
        confirmed_ids = set(scores[-1]['payload']['decisionIds'])
        input_packets = {record['payload']['sha256']: record['payload']['packet'] for record in store.records('input')}
        for decision in store.records('decision'):
            if decision['id'] in confirmed_ids:
                original = input_packets.get(decision['payload']['inputSha256'])
                if original:
                    relevant_dates.update(row['date'] for row in original['observations'])
        relevant_dates.update(date for dates_list in scores[-1]['payload']['outcomeDateBindings'].values() for date in dates_list)
    invalid = bool(dates & relevant_dates) or not scores or scores[-1]['payload']['status'] != 'scored' or material_revision or rejected_current
    if invalid:
        store.append('reference', {'modelId': None, 'status': 'cash-after-evidence-retraction',
                     'supersedes': active['id'], 'checkpointId': active['payload'].get('checkpointId'), 'reason': 'latest-evidence-unavailable'})


def record_decisions(store, packet, protocol_id):
    require(not A.validate_packet(packet, allow_synthetic=True), 'shadow-input-ineligible')
    with store.locked():
        protocols = {record['id']: record['payload'] for record in store.records('protocol')}
        require(protocol_id in protocols, 'shadow-unknown-protocol')
        protocol = protocols[protocol_id]
        require(packet['symbol'] == protocol['symbol'] and packet['source'] == protocol['source'], 'shadow-source-or-currency-mismatch')
        results = [record for record in store.records('research-result') if record['payload']['protocolId'] == protocol_id]
        require(results, 'shadow-research-not-complete')
        creation = A.now(); rows = packet['observations']; latest = rows[-1]
        require(S.utc_timestamp(latest['availableAt']) <= S.utc_timestamp(creation), 'shadow-input-after-creation')
        store.append('input', {'packet': packet, 'sha256': S.digest(packet)})
        if active_reference(store) is None:
            store.append('reference', {'modelId': None, 'status': 'cash-pending-economic-qualification', 'supersedes': None, 'checkpointId': None, 'reason': 'no-qualified-candidate'})
        _repair_reference(store)
        selected_id = results[-1]['payload']['selectedModelId']
        if selected_id is None:
            return {'status': 'cash-only', 'reason': 'no-mechanically-valid-candidate', 'created': 0}
        active = active_reference(store)
        identities = sorted(set([selected_id] + ([active['payload']['modelId']] if active['payload']['modelId'] else [])))
        models = {record['id']: record for record in store.records('model')}
        decisions = store.records('decision'); created = []
        for identity in identities:
            require(identity in models, 'shadow-unknown-model')
            model_record = models[identity]; model = model_record['payload']['model']
            own_protocol_id = model_record['payload']['protocolId']
            own_protocol = protocols[own_protocol_id]
            existing = [record for record in decisions if record['payload']['modelId'] == identity and record['payload']['originDate'] == latest['date']]
            if existing:
                created.append(existing[0]['id']); continue
            previous = [record for record in decisions if record['payload']['modelId'] == identity]
            if latest['date'] <= model['knownHistoryEnd']:
                continue
            if previous:
                previous_index = next((index for index, row in enumerate(rows) if row['date'] == previous[-1]['payload']['originDate']), None)
                if previous_index is None or len(rows)-1-previous_index < own_protocol['policy']['cadenceObservations']:
                    continue
            require(S.utc_timestamp(model_record['createdAt']) <= S.utc_timestamp(creation), 'shadow-model-after-decision')
            require(timedelta(0) <= S.utc_timestamp(creation)-S.utc_timestamp(latest['end']) <= timedelta(days=4), 'shadow-stale-input')
            value = A.forecast(model, packet, len(rows)-1)
            # The durable store clock is also the earliest forecast availability.
            # Future fill must follow it, not just the provider bar timestamp.
            payload = {'protocolId': own_protocol_id, 'modelId': identity, 'symbol': packet['symbol'],
                       'currency': own_protocol['currency'], 'originDate': latest['date'], 'originEnd': latest['end'],
                       'originClose': latest['close'], 'probabilityUp': value['probabilityUp'],
                       'inputSha256': S.digest(packet), 'featureRowsSha256': S.digest(rows), 'decisionAt': creation,
                       'horizon': own_protocol['horizon'], 'evidenceType': 'genuine-prospective' if packet['classification'] == 'observed-attested' else 'synthetic-mechanics',
                       'activeReferenceId': active['id'], 'boundary': A.BOUNDARY}
            record = store.append('decision', payload)
            require(S.utc_timestamp(record['createdAt']) >= S.utc_timestamp(creation), 'shadow-decision-clock-failure')
            created.append(record['id'])
        return {'status': 'recorded' if created else 'pending-later-observation', 'decisionIds': created,
                'activeReference': 'cash' if active['payload']['modelId'] is None else active['payload']['modelId'],
                'reason': 'no-qualified-candidate' if active['payload']['modelId'] is None else 'frozen-active-reference'}


def _score_payload(store, packet, model_id, *, observed_at=None):
    decisions = [record for record in store.records('decision') if record['payload']['modelId'] == model_id]
    require(decisions, 'shadow-no-decisions')
    models = {record['id']: record['payload'] for record in store.records('model')}
    require(model_id in models, 'shadow-unknown-model')
    A.forecast(models[model_id]['model'], packet, len(packet['observations'])-1)
    protocol_id = models[model_id]['protocolId']
    protocol = next(record['payload'] for record in store.records('protocol') if record['id'] == protocol_id)
    rows = packet['observations']; indices = {row['date']: index for index, row in enumerate(rows)}
    unavailable = unavailable_dates(store); selected = []; missing = []; corrections = []
    previous_scores = [record for record in store.records('score') if record['payload']['modelId'] == model_id]
    bindings = {}
    fixed_dates = []; fixed_decisions = None
    for prior_score in previous_scores:
        bindings.update(prior_score['payload'].get('outcomeDateBindings', {}))
        if prior_score['payload'].get('confirmationWindowDates'):
            fixed_dates = prior_score['payload']['confirmationWindowDates']
            fixed_decisions = set(prior_score['payload']['confirmationDecisionIds'])
    if fixed_decisions is not None:
        bindings = {identity: dates for identity, dates in bindings.items() if identity in fixed_decisions}
    inputs = {record['payload']['sha256']: record['payload']['packet'] for record in store.records('input')}
    for decision in decisions:
        if fixed_decisions is not None and decision['id'] not in fixed_decisions:
            continue
        payload = decision['payload']; origin = indices.get(payload['originDate'])
        original = inputs.get(payload['inputSha256'])
        if origin is None or original is None or payload['originDate'] in unavailable:
            missing.append(decision['id']); continue
        prefix = rows[:origin+1]
        if S.digest(prefix) != payload['featureRowsSha256']:
            corrections.append(decision['id']); continue
        frozen_dates = bindings.get(decision['id'], [])
        if any(date not in indices or date in unavailable for date in frozen_dates):
            missing.append(decision['id']); continue
        if len(frozen_dates) == payload['horizon']:
            interval = [rows[indices[date]] for date in frozen_dates]
            target = indices[frozen_dates[-1]]
        else:
            interval = rows[origin+1:min(origin+payload['horizon']+1,len(rows))]
            require([row['date'] for row in interval[:len(frozen_dates)]] == frozen_dates, 'shadow-partial-outcome-order-changed')
            if interval and any(payload['originDate'] < date <= interval[-1]['date'] for date in unavailable):
                missing.append(decision['id']); continue
            bindings[decision['id']] = [row['date'] for row in interval]
            if len(interval) < payload['horizon']:
                continue
            target = origin+payload['horizon']
        if any(payload['originDate'] < date <= rows[target]['date'] for date in unavailable):
            missing.append(decision['id']); continue
        if any(row['date'] in unavailable for row in interval):
            missing.append(decision['id']); continue
        if S.utc_timestamp(interval[0]['start']) <= S.utc_timestamp(decision['createdAt']):
            corrections.append(decision['id']); continue
        if any(S.utc_timestamp(row['availableAt']) <= S.utc_timestamp(decision['createdAt']) for row in interval):
            corrections.append(decision['id']); continue
        if S.utc_timestamp(rows[target]['availableAt']) > S.utc_timestamp(observed_at or A.now()):
            continue
        selected.append((decision, origin, target))
    payload = {'protocolId': protocol_id, 'modelId': model_id, 'datasetSha256': S.digest(packet),
               'availabilityRevisionIds': [record['id'] for record in store.records('availability')
                                            if record['payload']['date'] <= (fixed_dates[-1] if fixed_dates else rows[-1]['date'])],
               'decisionIds': [decision['id'] for decision, _, _ in selected], 'missingDecisionIds': missing,
               'correctedFeatureDecisionIds': corrections, 'outcomeDateBindings': bindings,
               'confirmationWindowDates': fixed_dates, 'confirmationDecisionIds': sorted(fixed_decisions) if fixed_decisions is not None else [], 'status': 'unavailable' if missing or corrections else 'pending',
               'genuineProspective': all(decision['payload']['evidenceType'] == 'genuine-prospective' for decision, _, _ in selected)
                                    and bool(selected) and packet['classification'] == 'observed-attested', 'boundary': A.BOUNDARY}
    if missing or corrections or not selected:
        return payload
    start = min(origin for _, origin, _ in selected)+1; end = max(target for _, _, target in selected)
    if fixed_dates and any(date not in indices or date in unavailable for date in fixed_dates):
        payload['status'] = 'unavailable'; return payload
    if not fixed_dates and end-start+1 >= 105:
        fixed_dates = [row['date'] for row in rows[start:start+105]]
        payload['confirmationWindowDates'] = fixed_dates
    if fixed_dates:
        end = indices[fixed_dates[-1]]
        selected = [(decision, origin, target) for decision, origin, target in selected if target <= end]
        payload['decisionIds'] = [decision['id'] for decision, _, _ in selected]
        payload['outcomeDateBindings'] = {identity: bindings[identity] for identity in payload['decisionIds']}
        payload['confirmationDecisionIds'] = sorted(payload['decisionIds'])
    if any(row['date'] in unavailable for row in rows[start:end+1]):
        payload['status'] = 'unavailable'; return payload
    forecasts = [{'index': origin-start, 'availableAt': decision['createdAt'], 'probabilityUp': decision['payload']['probabilityUp']}
                 for decision, origin, _ in selected]
    policy = protocol['policy']; window = rows[start:end+1]
    simulation = simulate(window, forecasts, policy); passive = simulate(window, [], policy, passive=True)
    cash = simulate(window, [], policy)
    doubled = simulate(window, forecasts, {**policy, 'costBps': policy['costBps']*2})
    doubled_passive = simulate(window, [], {**policy, 'costBps': policy['costBps']*2}, passive=True)
    differences = []
    for index in range(protocol['blockLength'], len(window), protocol['blockLength']):
        left = index-protocol['blockLength']
        differences.append(simulation['equity'][index]['simulatedLiquidationEquity']/simulation['equity'][left]['simulatedLiquidationEquity']
                           - passive['equity'][index]['simulatedLiquidationEquity']/passive['equity'][left]['simulatedLiquidationEquity'])
    probability = [decision['payload']['probabilityUp'] for decision, _, _ in selected]
    labels = [int(rows[target]['close'] > rows[origin]['close']) for _, origin, target in selected]
    payload.update({'status': 'scored', 'candidate': simulation, 'passive': passive, 'cash': cash,
                    'doubleCostCandidate': doubled, 'doubleCostPassive': doubled_passive,
                    'uncertainty': A.uncertainty(differences, protocol['comparisonCount']),
                    'pairedBlockDifferences': differences, 'supportingBrier': A.L._score(probability, labels),
                    'firstDate': window[0]['date'], 'lastDate': window[-1]['date']})
    return payload


def score_later(store, packet):
    require(not A.validate_packet(packet, allow_synthetic=True), 'shadow-input-ineligible')
    with store.locked():
        store.append('input', {'packet': packet, 'sha256': S.digest(packet)})
        identities = sorted({record['payload']['modelId'] for record in store.records('decision')})
        results = []
        for identity in identities:
            model = next(record['payload']['model'] for record in store.records('model') if record['id'] == identity)
            require(model['symbol'] == packet['symbol'] and model['source'] == packet['source'], 'shadow-source-mismatch')
            payload = _score_payload(store, packet, identity)
            previous = [record for record in store.records('score') if record['payload']['modelId'] == identity]
            latest = previous[-1] if previous else None
            if latest is not None and {key: value for key, value in latest['payload'].items() if key != 'supersedes'} == payload:
                results.append(latest['id'])
            else:
                payload['supersedes'] = latest['id'] if latest else None
                results.append(store.append('score', payload)['id'])
        _repair_reference(store)
        return {'scoreIds': results, 'status': 'scored' if results else 'pending-prospective-decisions'}


def checkpoint(store, model_id):
    """Only economics on current genuine forward evidence may replace cash/reference."""
    with store.locked():
        scores = [record for record in store.records('score') if record['payload']['modelId'] == model_id]
        require(scores, 'shadow-no-score')
        score = scores[-1]; value = score['payload']; active = active_reference(store)
        previous = [record for record in store.records('checkpoint') if record['payload']['modelId'] == model_id]
        comparison_reference = previous[0]['payload']['activeReferenceId'] if previous else active['id'] if active else None
        comparator = next((record for record in store.records('reference') if record['id'] == comparison_reference), None)
        comparator_model_id = comparator['payload']['modelId'] if comparator else None
        status = 'insufficient-evidence'; reason = 'minimum-independent-evidence-not-met'
        if value['status'] != 'scored':
            status = 'unavailable'; reason = 'latest-score-unavailable'
        elif value['availabilityRevisionIds'] != [record['id'] for record in store.records('availability')
                    if record['payload']['date'] <= (value['confirmationWindowDates'][-1] if value['confirmationWindowDates'] else value.get('lastDate','9999-12-31'))]:
            status = 'unavailable'; reason = 'restoration-requires-current-durable-score'
        elif not value['genuineProspective']:
            reason = 'synthetic-or-replay-never-qualifies'
        else:
            candidate = value['candidate']; passive = value['passive']; sensitivity = value['doubleCostCandidate']
            trips = sum(item['type'] == 'sell' and item['roundTripClosed'] for item in candidate['ledger'])
            enough = len(value['confirmationWindowDates']) == 105 and trips >= 10 and value['uncertainty']['lowerBound'] is not None
            if enough:
                qualified = (candidate['simulatedNetReturn'] > max(0, passive['simulatedNetReturn'])
                             and sensitivity['simulatedNetReturn'] > max(0, value['doubleCostPassive']['simulatedNetReturn'])
                             and value['uncertainty']['lowerBound'] > 0 and not candidate['riskStopped'])
                if comparator_model_id not in (None, model_id):
                    reference_scores = [record for record in store.records('score') if record['payload']['modelId'] == comparator_model_id]
                    reference = reference_scores[-1]['payload'] if reference_scores else None
                    packet = next((record['payload']['packet'] for record in store.records('input')
                                   if record['payload']['sha256'] == value['datasetSha256']), None)
                    matched = _matched_reference(store, packet, comparator_model_id, value['confirmationWindowDates']) if packet else None
                    differences = []
                    if matched is not None:
                        for index in range(5, len(candidate['equity']), 5):
                            left = index-5
                            differences.append(candidate['equity'][index]['simulatedLiquidationEquity']/candidate['equity'][left]['simulatedLiquidationEquity']
                                               - matched['equity'][index]['simulatedLiquidationEquity']/matched['equity'][left]['simulatedLiquidationEquity'])
                    comparisons = next(record['payload']['comparisonCount'] for record in store.records('protocol') if record['id'] == value['protocolId'])
                    paired = A.uncertainty(differences, comparisons)
                    qualified = qualified and bool(reference and reference['status'] == 'scored' and matched
                                 and candidate['simulatedNetReturn'] > matched['simulatedNetReturn']
                                 and paired['lowerBound'] is not None and paired['lowerBound'] > 0)
                status = 'evidence-qualified-monitoring-candidate' if qualified else 'rejected'
                reason = 'frozen-economic-and-adjusted-uncertainty-gates' if qualified else 'frozen-economic-gates-not-met'
        payload = {'modelId': model_id, 'scoreId': score['id'], 'status': status,
                   'reason': reason, 'activeReferenceId': comparison_reference}
        if previous and {key: item for key, item in previous[-1]['payload'].items() if key != 'supersedes'} == payload:
            record = previous[-1]
        else:
            payload['supersedes'] = previous[-1]['id'] if previous else None
            record = store.append('checkpoint', payload)
        if status == 'evidence-qualified-monitoring-candidate' and (active is None or active['payload']['modelId'] != model_id):
            store.append('reference', {'modelId': model_id, 'status': 'frozen-economic-reference',
                         'supersedes': active['id'] if active else None, 'checkpointId': record['id'], 'reason': 'prospective-economic-qualification'})
        _repair_reference(store)
        return record


def _matched_reference(store, packet, model_id, dates):
    """Start both comparison books in cash on the challenger's frozen dates."""
    model_record = next(record['payload'] for record in store.records('model') if record['id']==model_id)
    A.forecast(model_record['model'], packet, len(packet['observations'])-1)
    rows = packet['observations']; index = {row['date']: number for number, row in enumerate(rows)}
    if not dates or any(date not in index for date in dates):
        return None
    all_decisions = [record for record in store.records('decision') if record['payload']['modelId'] == model_id]
    prior = []; inside = []; first = index[dates[0]]; last = index[dates[-1]]
    for decision in all_decisions:
        origin = index.get(decision['payload']['originDate'])
        if origin is None:
            continue
        if origin < first:
            prior.append((decision, origin))
        elif origin < last:
            inside.append((decision, origin))
    if not prior:
        return None
    selected = [max(prior, key=lambda item:item[1])] + inside
    forecasts = []
    for decision, origin in selected:
        if S.digest(rows[:origin+1]) != decision['payload']['featureRowsSha256']:
            return None
        relative = max(-1, origin-first)
        if relative == -1 and S.utc_timestamp(decision['createdAt']) >= S.utc_timestamp(rows[first]['start']):
            return None
        forecasts.append({'index':relative,'availableAt':decision['createdAt'],'probabilityUp':decision['payload']['probabilityUp']})
    model = next(record['payload'] for record in store.records('model') if record['id']==model_id)
    policy = next(record['payload']['policy'] for record in store.records('protocol') if record['id']==model['protocolId'])
    return simulate([rows[index[date]] for date in dates],forecasts,policy)
