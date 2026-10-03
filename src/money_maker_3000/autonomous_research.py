"""Bounded frozen forecast -> economic research, immutable private trial history.

Existing predefined strategy state forecasts are fitted on development history.
The economic selector cannot inspect the holdout. Known history is retrospective;
only later prospective shadow observations may establish monitoring eligibility.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import fcntl
import math
import os
import random
import re
import stat
import tempfile

from . import learning as L, research_cycle as R, signal_toolkit as S
from .economic_portfolio import EconomicError, require, number, validate_rows, validate_policy, simulate, POLICY
from .market_history import Bar

VERSION = 'autonomous-profit-shadow.v1'
STRATEGY = 'slow-trend-allocation'
KINDS = {'input', 'protocol', 'model', 'trial-start', 'trial-result', 'research-result', 'decision',
         'score', 'availability', 'reference', 'checkpoint', 'snapshot-evidence'}
BOUNDARY = {'simulated': True, 'providerCalls': 'blocked', 'accountData': 'absent',
            'executionRoutes': 'absent', 'profitPromise': 'absent', 'leverage': 1}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def private_directory(path, *, create=False):
    path = Path(path)
    for parent in (path, *path.parents):
        require(not parent.is_symlink(), 'autonomous-unsafe-directory')
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700, 'autonomous-private-directory-required')
    return path


def private_read(path, limit, *, allow_producer_orphan=False):
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as handle:
            info=os.fstat(handle.fileno())
            require(stat.S_ISREG(info.st_mode) and (info.st_nlink==1 or allow_producer_orphan and info.st_nlink==2) and info.st_uid==os.geteuid()
                    and stat.S_IMODE(info.st_mode)==0o600 and info.st_size<=limit,'autonomous-unsafe-private-file')
            raw=handle.read(limit+1); require(len(raw)<=limit,'autonomous-private-file-size-limit')
            if info.st_nlink==2:
                recover_publication_link(path,raw,repair=False)
            return raw
    except OSError:
        raise EconomicError('autonomous-private-file-unavailable') from None


def recover_publication_link(path, expected_raw, *, repair=True):
    """Remove only a validated producer orphan matching the published inode.

    Atomic no-clobber hardlink publication has a two-link crash window. A
    canonical file plus its exact private same-directory pending link can be
    repaired under the writer lock; unrelated hardlinks remain rejected.
    """
    info=path.lstat()
    if info.st_nlink==1:
        return
    require(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o600
            and info.st_nlink==2, 'autonomous-unrecognized-publication-hardlink')
    candidates=[]
    for pending in path.parent.glob('.pending-*'):
        candidate=pending.lstat()
        if candidate.st_ino==info.st_ino and candidate.st_dev==info.st_dev:
            require(re.fullmatch(r'\.pending-[a-z0-9_]{8}',pending.name) is not None,'autonomous-unrecognized-publication-pending-name')
            require(stat.S_ISREG(candidate.st_mode) and candidate.st_uid==os.geteuid()
                    and stat.S_IMODE(candidate.st_mode)==0o600 and candidate.st_nlink==2,
                    'autonomous-unsafe-publication-orphan')
            candidates.append(pending)
    require(len(candidates)==1 and L._read(path,64*1024*1024)==expected_raw, 'autonomous-publication-orphan-identity')
    if not repair:
        return
    os.unlink(candidates[0])
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)


def recover_event_publications(root, *, repair=True):
    for path in sorted(root.glob('event-*.json')):
        if path.lstat().st_nlink==1:
            continue
        raw=L._read(path,64*1024*1024); value=L._json(raw)
        require(set(value)=={'sequence','previous','createdAt','kind','payload','id'}
                and type(value['sequence']) is int and path.name==f"event-{value['sequence']:08d}.json"
                and value['kind'] in KINDS and type(value['payload']) is dict
                and value['id']==S.digest({key:item for key,item in value.items() if key!='id'})
                and S.canonical(value)==raw, 'autonomous-publication-event-identity')
        recover_publication_link(path,raw,repair=repair)


def write_once(path, raw):
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            require(L._read(path, 64*1024*1024) == raw, 'autonomous-immutable-conflict')
            recover_publication_link(path,raw)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            # An identical concurrent publisher may have verified and removed
            # this exact remnant. Prove its canonical bytes rather than hiding
            # other cleanup failures or accepting a missing/corrupt publication.
            require(private_read(path,64*1024*1024)==raw,'autonomous-publication-cleanup-identity')


class Store:
    """Single-host chained append-only store; all compound operations take its lock.

    The clock is owned here. Tests patch now(); operators cannot supply a creation
    timestamp. Read operations still enforce current source-specific retention.
    """
    def __init__(self, root, *, source, retention, create=False):
        R.retention_check(source, retention)
        require(source in ('etoro','fmp-eod','synthetic'),'autonomous-unsupported-store-source')
        self.root = private_directory(root, create=create)
        self.source = source; self.retention = retention
        self._locked = False; self._cache = None; self._read_only = False
        binding = {'version': VERSION, 'source': source}
        path = self.root / '.source.json'
        fd=os.open(self.root/'.lock',(os.O_RDWR|os.O_CREAT if create else os.O_RDONLY)|os.O_NOFOLLOW,0o600)
        try:
            info=os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.geteuid()
                    and stat.S_IMODE(info.st_mode)==0o600,'autonomous-unsafe-lock')
            fcntl.flock(fd,fcntl.LOCK_EX if create else fcntl.LOCK_SH)
            if create:
                write_once(path,S.canonical(binding))
            require(L._json(private_read(path,4096,allow_producer_orphan=not create))==binding,'autonomous-source-mismatch')
        finally:
            os.close(fd)

    @contextmanager
    def locked(self, *, read_only=False):
        R.retention_check(self.source, self.retention)
        fd = os.open(self.root/'.lock', os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600,
                    'autonomous-unsafe-lock')
            fcntl.flock(fd, fcntl.LOCK_SH if read_only else fcntl.LOCK_EX)
            recover_event_publications(self.root,repair=not read_only)
            self._locked = True; self._cache = None; self._read_only = read_only
            yield
        finally:
            self._locked = False; self._cache = None; self._read_only = False
            os.close(fd)

    def records(self, kind=None):
        if not self._locked:
            with self.locked(read_only=True):
                return self.records(kind)
        R.retention_check(self.source, self.retention)
        if self._locked and self._cache is not None:
            return [record for record in self._cache if kind is None or record['kind'] == kind]
        result = []; previous = None; previous_time = None
        for sequence, path in enumerate(sorted(self.root.glob('event-*.json'))):
            value = L._json(private_read(path, 64*1024*1024,allow_producer_orphan=self._read_only))
            require(set(value) == {'sequence', 'previous', 'createdAt', 'kind', 'payload', 'id'}, 'autonomous-invalid-event')
            require(value['sequence'] == sequence and path.name == f'event-{sequence:08d}.json'
                    and value['previous'] == previous and value['kind'] in KINDS and type(value['payload']) is dict,
                    'autonomous-journal-integrity')
            require(value['id'] == S.digest({key: item for key, item in value.items() if key != 'id'}), 'autonomous-journal-digest')
            creation = S.utc_timestamp(value['createdAt'])
            require(previous_time is None or creation >= previous_time, 'autonomous-reversed-clock')
            previous_time = creation; previous = value['id']; result.append(value)
        if self._locked:
            self._cache = result
        return [record for record in result if kind is None or record['kind'] == kind]

    def append(self, kind, payload):
        require(self._locked and not self._read_only, 'autonomous-writer-lock-required')
        require(kind in KINDS and type(payload) is dict, 'autonomous-invalid-event')
        records = self.records()
        existing = [record for record in records if record['kind'] == kind and record['payload'] == payload]
        if existing:
            return existing[0]
        creation = now()
        require(not records or S.utc_timestamp(creation) >= S.utc_timestamp(records[-1]['createdAt']), 'autonomous-reversed-clock')
        record = {'sequence': len(records), 'previous': records[-1]['id'] if records else None,
                  'createdAt': creation, 'kind': kind, 'payload': payload}
        record['id'] = S.digest(record)
        write_once(self.root/f"event-{len(records):08d}.json", S.canonical(record))
        if self._locked:
            self._cache = records+[record]
        return record


def validate_packet(packet, *, allow_synthetic=False):
    require(type(packet) is dict and set(packet) == {'version', 'source', 'symbol', 'classification', 'retention',
            'interpretation', 'observations'}, 'autonomous-invalid-input-contract')
    require(packet['version'] == VERSION and packet['source'] in ('etoro', 'fmp-eod', 'synthetic')
            and packet['symbol'] in ('SPY', 'QQQ', 'VAS'), 'autonomous-invalid-source-or-symbol')
    require(packet['classification'] in ('observed-attested', 'synthetic'), 'autonomous-invalid-classification')
    require(packet['classification'] != 'synthetic' or allow_synthetic, 'autonomous-synthetic-opt-in-required')
    require(packet['source'] != 'synthetic' or packet['classification'] == 'synthetic', 'autonomous-source-classification-mismatch')
    R.retention_check(packet['source'], packet['retention'])
    rows = packet['observations']
    require(type(rows) is list and len(rows) <= 10000, 'autonomous-invalid-history-size')
    prior = None
    for row in rows:
        require(type(row) is dict and set(row) == {'date', 'start', 'end', 'availableAt', 'open', 'high', 'low', 'close', 'volume'}, 'autonomous-invalid-native-observation')
        L._iso(row['date'])
        require(prior is None or row['date'] > prior, 'autonomous-unordered-native-observations')
        require(number(row['close'], 1e-12), 'autonomous-invalid-native-close')
        for key in ('start', 'end', 'availableAt'):
            require(row[key] is None or type(row[key]) is str, 'autonomous-invalid-native-time')
            if row[key] is not None:
                S.utc_timestamp(row[key])
        for field in ('open', 'high', 'low', 'volume'):
            require(row[field] is None or number(row[field], 0 if field == 'volume' else 1e-12), 'autonomous-invalid-native-number')
        prior = row['date']
    interpretation = packet['interpretation']
    require(type(interpretation) is dict and set(interpretation) == {'identity', 'currency', 'sessionConvention',
            'timestampMeaning', 'adjustmentBasis', 'priceBasis', 'evidence'}, 'autonomous-invalid-interpretation')
    require(all(type(interpretation[key]) is str and 1 <= len(interpretation[key]) <= 1024 for key in interpretation if key != 'evidence'), 'autonomous-invalid-interpretation')
    evidence = interpretation['evidence']
    require(type(evidence) is dict and set(evidence) <= {'identity', 'currency', 'sessionConvention', 'timestampMeaning', 'adjustmentBasis', 'priceBasis'}, 'autonomous-invalid-evidence')
    for item in evidence.values():
        require(type(item) is dict and set(item) == {'status', 'reference'} and item['status'] in ('verified', 'unknown')
                and type(item['reference']) is str and len(item['reference']) <= 2048, 'autonomous-invalid-evidence')
    reasons = []
    expected_currency = 'AUD' if packet['symbol'] == 'VAS' else 'USD'
    if interpretation['currency'] != expected_currency:
        reasons.append('listing-currency-unverified')
    for key in ('identity', 'currency', 'sessionConvention', 'timestampMeaning', 'adjustmentBasis', 'priceBasis'):
        if interpretation[key] == 'unknown' or evidence.get(key, {}).get('status') != 'verified' or not evidence.get(key, {}).get('reference'):
            reasons.append(key+'-unverified')
    if not rows:
        reasons.append('no-data')
    elif any(row[key] is None for row in rows for key in ('start', 'end', 'availableAt', 'open', 'high', 'low')):
        reasons.append('completed-ohlc-availability-unverified')
    else:
        validate_rows(rows)
        if any(S.utc_timestamp(row['availableAt']) > S.utc_timestamp(now()) for row in rows):
            reasons.append('future-availability')
    return sorted(set(reasons))


def bars(packet):
    return [Bar(packet['symbol'], row['date'], row['open'], row['high'], row['low'], row['close'], row['volume'], packet['source'])
            for row in packet['observations']]


def freeze(store, packet, *, policy=None, allow_synthetic=False):
    reasons = validate_packet(packet, allow_synthetic=allow_synthetic)
    require(not reasons, 'autonomous-input-ineligible:'+','.join(reasons))
    require(store.source == packet['source'], 'autonomous-source-mismatch')
    policy = dict(POLICY) if policy is None else policy
    validate_policy(policy)
    require(policy['costBps'] <= 500, 'autonomous-double-cost-bound')
    rows = packet['observations']; require(len(rows) >= 420, 'autonomous-insufficient-development-history')
    require(all(S.utc_timestamp(row['availableAt']) <= S.utc_timestamp(now()) for row in rows), 'autonomous-future-input')
    with store.locked():
        input_record = store.append('input', {'packet': packet, 'sha256': S.digest(packet)})
        current = [record for record in store.records('protocol') if record['payload']['inputSha256'] == S.digest(packet)]
        if current:
            require(current[0]['payload']['policy'] == policy, 'autonomous-inspected-holdout-retune-rejected')
            return current[0]
        prior = store.records('protocol')
        # New protocol needs a genuinely longer source history. Merely changing
        # costs/thresholds or correcting inspected history cannot buy a new holdout.
        require(not prior or rows[-1]['date'] > max(record['payload']['knownHistoryEnd'] for record in prior), 'autonomous-inspected-history-reuse-rejected')
        require(9*(len(prior)+1)*(len(prior)+2) <= 300, 'autonomous-frozen-lifetime-comparison-budget-exhausted')
        require(packet['retention'] == store.retention, 'autonomous-retention-attestation-mismatch')
        train_end = int(len(rows)*.6)-1; selection_end = int(len(rows)*.8)-1
        payload = {'version': VERSION, 'inputId': input_record['id'], 'inputSha256': S.digest(packet),
                   'symbol': packet['symbol'], 'currency': packet['interpretation']['currency'], 'source': packet['source'],
                   'classification': packet['classification'], 'policy': policy, 'strategy': STRATEGY,
                   'candidates': L.candidate_grid(STRATEGY), 'trainEnd': train_end, 'selectionEnd': selection_end,
                   'knownHistoryEnd': rows[-1]['date'], 'horizon': 5, 'blockLength': 5, 'minimumBlocks': 20,
                   'minimumRoundTrips': 10, 'alpha': .05, 'comparisonCount': 9*(len(prior)+1)*(len(prior)+2),
                   'selectionRule': 'greatest-development-net-return-minus-matched-passive; tie-lowest-index',
                   'holdoutRule': 'selected-candidate-only; reject-or-insufficient; no-retune',
                   'confirmationRule': 'first-105-prospective-observations-fixed; revisions-only; three-economic-comparisons', 'boundary': BOUNDARY}
        return store.append('protocol', payload)


def model_for(packet, protocol, candidate):
    rows = packet['observations']; history = bars(packet); horizon = protocol['horizon']; end = protocol['trainEnd']
    indices = list(range(L._warmup(candidate)-1, end-horizon+1))
    require(indices, 'autonomous-insufficient-training-support')
    cutoff = S.utc_timestamp(rows[end]['availableAt'])
    require(all(S.utc_timestamp(row['availableAt']) <= cutoff for row in rows[:end+1]), 'autonomous-training-late-availability')
    states = [L._state(history, index, STRATEGY, candidate) for index in indices]
    labels = [int(rows[index+horizon]['close'] > rows[index]['close']) for index in indices]
    return {'version': VERSION, 'strategy': STRATEGY, 'parameters': candidate, 'fit': L._fit(states, labels, STRATEGY),
            'symbol': packet['symbol'], 'source': packet['source'], 'knownHistoryEnd': protocol['knownHistoryEnd'],
            'inputSha256': S.digest(packet), 'semanticsSha256': S.digest({key: packet[key] for key in ('source','symbol','classification','retention','interpretation')}),
            'trainingEnd': rows[end]['date'], 'horizon': horizon}


def forecast(model, packet, index):
    require(model['strategy'] == STRATEGY and model['parameters'] in L.candidate_grid(STRATEGY), 'autonomous-unregistered-model')
    require(model.get('semanticsSha256') == S.digest({key: packet[key] for key in ('source','symbol','classification','retention','interpretation')}), 'autonomous-model-semantics-mismatch')
    L._validate_fit(model['fit'], STRATEGY)
    rows = packet['observations']; warmup = L._warmup(model['parameters'])
    require(index+1 >= warmup, 'autonomous-insufficient-forecast-history')
    decision = S.utc_timestamp(rows[index]['availableAt'])
    require(all(S.utc_timestamp(row['availableAt']) <= decision for row in rows[index+1-warmup:index+1]), 'autonomous-feature-late-availability')
    state = L._state(bars(packet), index, STRATEGY, model['parameters'])
    support = model['fit']['states'][state]
    return {'index': index, 'availableAt': rows[index]['availableAt'], 'probabilityUp': support['probabilityUp']}


def evaluate(model, packet, protocol, start, end, *, double_cost=False):
    rows = packet['observations']; require(0 <= start <= end < len(rows), 'autonomous-invalid-evaluation-window')
    forecasts = []
    for index in range(start, end+1):
        if index+1 >= L._warmup(model['parameters']) and (index-start) % protocol['policy']['cadenceObservations'] == 0:
            value = forecast(model, packet, index)
            value['index'] -= start; forecasts.append(value)
    policy = dict(protocol['policy'])
    if double_cost:
        policy['costBps'] *= 2
    simulation = simulate(rows[start:end+1], forecasts, policy)
    passive = simulate(rows[start:end+1], [], policy, passive=True)
    cash = simulate(rows[start:end+1], [], policy)
    paired = []
    for index in range(protocol['blockLength'], len(simulation['equity']), protocol['blockLength']):
        left, right = simulation['equity'][index-protocol['blockLength']], simulation['equity'][index]
        bleft, bright = passive['equity'][index-protocol['blockLength']], passive['equity'][index]
        paired.append(right['simulatedLiquidationEquity']/left['simulatedLiquidationEquity']
                      - bright['simulatedLiquidationEquity']/bleft['simulatedLiquidationEquity'])
    probabilities = [item['probabilityUp'] for item in forecasts if item['index']+protocol['horizon'] < end-start+1]
    labels = [int(rows[start+item['index']+protocol['horizon']]['close'] > rows[start+item['index']]['close'])
              for item in forecasts if item['index']+protocol['horizon'] < end-start+1]
    return {'candidate': simulation, 'passive': passive, 'cash': cash, 'pairedBlockDifferences': paired,
            'supportingBrier': L._score(probabilities, labels) if labels else None,
            'monetaryEdge': simulation['simulatedNetReturn']-passive['simulatedNetReturn']}


def uncertainty(differences, comparisons):
    """Paired circular moving-block bootstrap at 10/15 observations + Bonferroni.

    Five-observation differences are resampled in contiguous groups of two
    and three to preserve serial dependence; the smaller lower bound wins. At least 20 blocks and 10 closed round trips are
    required; no interval creates evidence from an insufficient sample.
    """
    require(type(comparisons) is int and 1 <= comparisons <= 300, 'autonomous-invalid-comparison-count')
    require(type(differences) is list and all(number(value, -1e12, 1e12) for value in differences), 'autonomous-invalid-paired-differences')
    alpha = .05/comparisons
    if len(differences) < 20:
        return {'status': 'insufficient-evidence', 'blocks': len(differences), 'adjustedAlpha': alpha, 'lowerBound': None}
    count = max(2000, math.ceil(4/alpha)); bounds = []
    for dependence_length in (2, 3):
        randomizer = random.Random(73013+dependence_length); samples = []
        for _ in range(count):
            selected = []
            while len(selected) < len(differences):
                first = randomizer.randrange(len(differences))
                selected.extend(differences[(first+offset) % len(differences)] for offset in range(dependence_length))
            samples.append(math.fsum(selected[:len(differences)])/len(differences))
        samples.sort(); bounds.append(samples[max(0, int(alpha*count)-1)])
    return {'status': 'evaluated', 'blocks': len(differences), 'adjustedAlpha': alpha,
            'lowerBound': min(bounds), 'blockSensitivityLowerBounds': bounds,
            'method': 'paired-circular-moving-10-and-15-observation-block-bootstrap-Bonferroni'}


def run_research(store, protocol_id, *, interrupt_after=None):
    """Resume every frozen trial; results are never discarded or selectively rerun."""
    with store.locked():
        registered = {record['id']: record for record in store.records('protocol')}
        require(protocol_id in registered, 'autonomous-unknown-protocol')
        protocol = registered[protocol_id]['payload']
        inputs = {record['id']: record['payload']['packet'] for record in store.records('input')}
        packet = inputs[protocol['inputId']]
        require(not validate_packet(packet, allow_synthetic=True), 'autonomous-retained-input-ineligible')
        completed = [record for record in store.records('research-result') if record['payload']['protocolId'] == protocol_id]
        if completed:
            return completed[0]
        results = {record['payload']['candidateIndex']: record for record in store.records('trial-result') if record['payload']['protocolId'] == protocol_id}
        for index, candidate in enumerate(protocol['candidates']):
            if index in results:
                continue
            store.append('trial-start', {'protocolId': protocol_id, 'candidateIndex': index})
            try:
                model = model_for(packet, protocol, candidate)
                model_record = store.append('model', {'protocolId': protocol_id, 'candidateIndex': index, 'model': model})
                development = evaluate(model, packet, protocol, protocol['trainEnd']+1, protocol['selectionEnd'])
                payload = {'protocolId': protocol_id, 'candidateIndex': index, 'status': 'evaluated',
                           'modelId': model_record['id'], 'development': development}
            except (EconomicError, L.LearningError, S.SignalError):
                payload = {'protocolId': protocol_id, 'candidateIndex': index, 'status': 'mechanically-rejected', 'reason': 'frozen-trial-invalid'}
            results[index] = store.append('trial-result', payload)
            if interrupt_after is not None and len(results) >= interrupt_after:
                raise EconomicError('autonomous-interrupted-for-recovery-test')
        valid = [record for record in results.values() if record['payload']['status'] == 'evaluated']
        if not valid:
            return store.append('research-result', {'protocolId': protocol_id, 'status': 'mechanically-rejected',
                                'trialIds': [results[index]['id'] for index in sorted(results)], 'selectedModelId': None})
        selected = max(valid, key=lambda record: (record['payload']['development']['monetaryEdge'], -record['payload']['candidateIndex']))
        model_record = next(record for record in store.records('model') if record['id'] == selected['payload']['modelId'])
        holdout = evaluate(model_record['payload']['model'], packet, protocol, protocol['selectionEnd']+1, len(packet['observations'])-1)
        sensitivity = evaluate(model_record['payload']['model'], packet, protocol, protocol['selectionEnd']+1, len(packet['observations'])-1, double_cost=True)
        interval = uncertainty(holdout['pairedBlockDifferences'], protocol['comparisonCount'])
        status = 'rejected' if holdout['monetaryEdge'] <= 0 or holdout['candidate']['simulatedNetReturn'] <= 0 or sensitivity['monetaryEdge'] <= 0 else 'insufficient-evidence'
        # This holdout was present at protocol registration. It is diagnostic,
        # even if never used for selection. It cannot become fresh confirmation.
        payload = {'protocolId': protocol_id, 'status': status, 'trialIds': [results[index]['id'] for index in sorted(results)],
                   'selectedModelId': model_record['id'], 'selectionTrialId': selected['id'], 'holdout': holdout,
                   'doubleCostSensitivity': sensitivity, 'uncertainty': interval, 'monitoringEligible': False,
                   'evidenceType': 'retrospective-known-history', 'reason': 'prospective-independent-confirmation-required', 'boundary': BOUNDARY}
        return store.append('research-result', payload)


def status(store, packet=None):
    reasons = validate_packet(packet, allow_synthetic=True) if packet is not None else ['no-data']
    with store.locked(read_only=True):
        records = store.records()
        from .autonomous_recovery import _replay
        _replay(records)
        results = [record for record in records if record['kind'] == 'research-result']
        checkpoints = [record for record in records if record['kind']=='checkpoint']
        references = [record for record in records if record['kind']=='reference']
        scores = [record for record in records if record['kind']=='score']
        rows = packet['observations'] if packet is not None else []
        available = rows[-1]['availableAt'] if rows else None
        end = rows[-1]['end'] if rows else None
        fresh = bool(end and available and timedelta(0) <= S.utc_timestamp(now())-S.utc_timestamp(end) <= timedelta(days=4))
        return {'version': VERSION, 'softwareHealth': 'healthy', 'feedFreshness': 'fresh-wall-clock-bound' if fresh else 'stale' if available else 'unavailable',
                'lastObservationCompletedAt': end, 'lastAvailabilityAt': available,
                'exchangeCalendarFreshness': 'unverified', 'researchEligibility': 'eligible' if not reasons else 'ineligible', 'eligibilityReasons': reasons,
                'economicEvidence': checkpoints[-1]['payload']['status'] if checkpoints else results[-1]['payload']['status'] if results else 'not-evaluated',
                'retrospectiveEconomicEvidence': results[-1]['payload']['status'] if results else 'not-evaluated',
                'prospectiveEconomicEvidence': checkpoints[-1]['payload']['status'] if checkpoints else 'pending-prospective-confirmation',
                'currentScoreStatus': scores[-1]['payload']['status'] if scores else 'no-prospective-score',
                'activeReference': references[-1]['payload']['status'] if references else 'cash-no-qualified-candidate',
                'eventCount': len(records), 'pendingTrials': len([record for record in records if record['kind'] == 'trial-start'])
                - len([record for record in records if record['kind'] == 'trial-result']), 'boundary': BOUNDARY}
