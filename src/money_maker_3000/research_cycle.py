"""Frozen, bounded local strategy research. All results are retrospective diagnostics."""
from __future__ import annotations

import fcntl
import math
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass
from .market_history import Bar
from pathlib import Path
from typing import Any

from . import learning as L
from . import signal_toolkit as S

VERSION = 'continuous-research.v1'
HORIZON = 5
FMP_POLICY = 'fmp-active-subscription-delete-within-30-days'


def retention_check(source: str, retention: dict) -> None:
    """Check current operator attestation before reading data OR derived evidence."""
    L._require(type(source) is str and type(retention) is dict, 'invalid-retention')
    if source == 'etoro':
        from .feed_collection import check_retention
        check_retention(retention, now())
        return
    if 'fmp' in source.lower() or 'financial-modeling-prep' in source.lower():
        L._require(retention.get('policy') == FMP_POLICY, 'fmp-retention-policy-required')
    if retention.get('policy') == FMP_POLICY:
        L._keys(retention, {'policy', 'subscriptionStatus', 'terminationDate'})
        L._require(retention['subscriptionStatus'] == 'active' and retention['terminationDate'] is None,
                   'fmp-inactive-delete-all-derived-artifacts-within-30-days')
    else:
        L._require(retention == {'policy': 'source-terms'}, 'unknown-retention-policy')


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        L._require(result.tzinfo is not None, 'timezone-required')
        return result.astimezone(timezone.utc)
    except (ValueError, AttributeError):
        raise L.LearningError('invalid-research-timestamp') from None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceStore:
    """Atomic immutable records; flock serializes compound identity checks.

    Evidence is private research material, separate from the simulation audit ledger.
    Crash before atomic link leaves only an unreferenced temporary; retry is safe.
    """
    def __init__(self, root: str | Path, *, create: bool = True):
        self.root = Path(root)
        for parent in (self.root, *self.root.parents):
            L._require(not parent.is_symlink(), 'unsafe-evidence-directory')
        if create:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.stat()
        L._require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                   and stat.S_IMODE(info.st_mode) == 0o700, 'private-evidence-directory-required')

    def authorize(self, source: str, retention: dict, *, create: bool = False) -> None:
        """Read only source-name metadata until the current source policy passes."""
        retention_check(source,retention)
        path=self.root/'.source.json'
        expected={'version':'research-store-source.v1','source':source}
        if not path.exists():
            L._require(create and not any(self.root.glob('*.json')), 'unbound-pre-review-evidence-store')
            fd,temp=tempfile.mkstemp(prefix='.source-pending-',dir=self.root)
            try:
                with os.fdopen(fd,'wb') as handle:
                    handle.write(L._canonical(expected));handle.flush();os.fsync(handle.fileno())
                try:
                    os.link(temp,path,follow_symlinks=False)
                except FileExistsError:
                    pass
                directory=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                os.unlink(temp)
        actual=L._json(L._read(path,1024))
        L._require(actual==expected,'evidence-store-source-mismatch')

    @contextmanager
    def locked(self, *, read_only=False):
        flags = os.O_RDONLY if read_only else os.O_RDWR | os.O_CREAT
        fd = os.open(self.root / '.lock', flags | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            L._require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                       and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe-evidence-lock')
            fcntl.flock(fd, fcntl.LOCK_SH if read_only else fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def records(self, kind: str) -> list[dict]:
        L._require(kind in ('protocol', 'experiment', 'model', 'forecast', 'score', 'checkpoint', 'reference'), 'invalid-record-kind')
        result = []
        for path in sorted(self.root.glob(kind + '-*.json')):
            record = L._json(L._read(path, 8 * 1024 * 1024))
            L._keys(record, {'kind', 'id', 'payload'})
            L._require(type(record['payload']) is dict, 'invalid-evidence-payload')
            L._require(record.get('kind') == kind and record.get('id') == L._digest(record['payload'])
                       and path.name == f"{kind}-{record['id']}.json", 'evidence-integrity-failure')
            validate_record_payload(kind,record['payload'])
            result.append(record)
        return result

    def put(self, kind: str, payload: dict) -> dict:
        L._require(kind in ('protocol', 'experiment', 'model', 'forecast', 'score', 'checkpoint', 'reference'), 'invalid-record-kind')
        source=payload.get('manifest',{}).get('source') if kind=='protocol' else payload.get('source')
        if source is not None and 'retention' in payload:
            self.authorize(source,payload['retention'],create=True)
        identity = L._digest(payload)
        record = {'kind': kind, 'id': identity, 'payload': payload}
        destination = self.root / f'{kind}-{identity}.json'
        raw = L._canonical(record) + b'\n'
        L._require(len(raw) <= 8 * 1024 * 1024, 'evidence-size-limit')
        fd, temp = tempfile.mkstemp(prefix='.pending-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temp, destination, follow_symlinks=False)
            except FileExistsError:
                L._require(L._read(destination, len(raw)) == raw, 'evidence-identity-conflict')
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.unlink(temp)
        return record


def validate_history(bars: list, manifest: dict) -> None:
    L._manifest(manifest)
    L._require(len(bars) == manifest['rowCount'] and 1 <= len(bars) <= L.MAX_ROWS, 'invalid-history-count')
    previous = ''
    for bar in bars:
        L._require(L._iso(bar.date) > previous and bar.source == manifest['source']
                   and bar.symbol == manifest['symbol'] and L._number(bar.close, 1e-8, 1e12), 'invalid-research-history')
        previous = bar.date


def incumbent_provenance(incumbent: dict) -> dict:
    """Return the predecessor model meaning that survives an import cycle.

    Each successor binds its own protocol, freeze time, retention attestation,
    and interpretation.  Those contextual fields are intentionally rewritten
    on import and therefore cannot identify the predecessor model.
    """
    # Optional toolkit bindings describe the successor protocol context, not
    # the predecessor's semantic identity.  Leaving them here makes a legacy
    # predecessor appear to have changed when its successor adds a toolkit.
    return {key: value for key, value in incumbent.items()
            if key not in ('protocolId', 'frozenAt', 'retention', 'interpretation',
                           'signalFeatureBundleSha256', 'signalFeaturePolicy')}


def freeze_protocol(bars: list, manifest: dict, strategy: str, retention: dict,
                    interpretation: dict, *, created_at: str | None = None, incumbent_model: dict | None = None,
                    signal_toolkit: dict | None = None) -> dict:
    retention_check(manifest['source'], retention)
    validate_history(bars, manifest)
    grid = L.candidate_grid(strategy)
    L._require(1 <= len(grid) <= 24, 'candidate-budget-exceeded')
    warmup = max(L._warmup(p) for p in grid)
    n = len(bars)
    cuts = [int(n * fraction) - 1 for fraction in (.4, .5, .6, .7)]
    L._require(cuts[0] - HORIZON - warmup + 2 >= 20 and min(b-a for a,b in zip(cuts,cuts[1:])) >= 15
               and n - cuts[-1] >= 16, 'insufficient-three-window-history')
    validate_interpretation(interpretation)
    L._require(interpretation['trainingPermitted'] is True and interpretation['instrumentVerified'] is True, 'source-not-approved-for-training')
    result = {'version': VERSION, 'createdAt': created_at or now(), 'datasetSha256': manifest['sha256'],
            'rowsSha256': L._digest([bar.to_dict() for bar in bars]), 'manifest': manifest,
            'retention': retention, 'interpretation': interpretation, 'strategy': strategy,
            'featureVersion': 'registry-history-state.v1', 'horizonObservations': HORIZON,
            'incumbentModelSha256':L._digest(incumbent_provenance(incumbent_model)) if incumbent_model is not None else None,
            'candidates': grid, 'maxConfigurationsPerFamily': 24, 'runtimeSeconds': 120,
            'developmentCutoffs': [bars[i].date for i in cuts], 'reservedStart': bars[cuts[-1]+1].date,
            'selectionRule': 'lowest-pooled-development-brier-tie-grid-index',
            'evaluationContext': 'retrospective-known-history',
            'checkpointPolicy': {'minimumCompleted': 60, 'checkpointCompleted': 60,
                'minimumUsefulImprovement': .005, 'blockLength': 5, 'bootstrapReplicates': 2000,
                'confidence': .95, 'maxComparisons': 2, 'maxReplacements': 1, 'maxChallengers': 2},
            'boundary': dict(L.BOUNDARY)}
    if signal_toolkit is not None:
        L._keys(signal_toolkit, {'ohlcAttested', 'ohlcBasis', 'completedAt', 'availableAt'})
        L._require(type(signal_toolkit['ohlcAttested']) is bool and (signal_toolkit['ohlcBasis'] is None or type(signal_toolkit['ohlcBasis']) is str), 'invalid-signal-toolkit-config')
        # Completion and source availability are deliberately distinct full UTC facts.
        L._require(timestamp(signal_toolkit['completedAt']) <= timestamp(signal_toolkit['availableAt']) <= timestamp(result['createdAt']), 'signal-availability-after-protocol-freeze')
        rows = [{**bar.to_dict(), 'end': bar.date + 'T00:00:00Z'} for bar in bars]
        result['signalFeatureBundle'] = S.freeze_feature_bundle(rows, completed_at=signal_toolkit['completedAt'], available_at=signal_toolkit['availableAt'],
                                                                ohlc_attested=signal_toolkit['ohlcAttested'], ohlc_basis=signal_toolkit['ohlcBasis'])
    return result


def validate_interpretation(value: dict) -> None:
    L._keys(value, {'instrumentVerified', 'trainingPermitted', 'currency', 'session', 'timestampMeaning', 'priceType', 'adjustments', 'costs', 'evidence'})
    L._require(type(value['instrumentVerified']) is bool and type(value['trainingPermitted']) is bool, 'invalid-source-permission')
    for key in ('currency', 'session', 'timestampMeaning', 'priceType', 'adjustments', 'costs', 'evidence'):
        L._require(type(value[key]) is str and 1 <= len(value[key]) <= 1024, 'invalid-source-interpretation')


def metrics(probabilities: list[float], labels: list[int], fallbacks: list[bool]) -> dict:
    L._require(len(labels) > 0 and len(probabilities) == len(labels) == len(fallbacks), 'empty-metrics')
    bins = []
    for low in range(5):
        indices = [i for i,p in enumerate(probabilities) if min(int(p*5),4) == low]
        bins.append({'bin': low, 'count': len(indices),
                     'meanProbability': math.fsum(probabilities[i] for i in indices)/len(indices) if indices else None,
                     'observedFrequency': sum(labels[i] for i in indices)/len(indices) if indices else None})
    return {'count': len(labels), 'brier': L._score(probabilities, labels),
            'priorFallbackCount': sum(fallbacks), 'calibration': bins}


def _check_deadline(deadline):
    if deadline is not None:
        L._require(time.monotonic() < deadline, 'research-runtime-exhausted')


def _states(bars, indices, strategy, parameters, deadline=None, cache=None):
    result = []
    for i in indices:
        _check_deadline(deadline)
        key = (strategy, L._digest(parameters), i)
        if cache is None:
            result.append(L._state(bars,i,strategy,parameters))
        else:
            if key not in cache:
                cache[key] = L._state(bars,i,strategy,parameters)
            result.append(cache[key])
    return result


def _fit_at(bars, strategy, parameters, cutoff, warmup, deadline=None, cache=None):
    indices = [i for i in range(warmup-1, len(bars)-HORIZON) if bars[i+HORIZON].date <= cutoff]
    L._require(len(indices) >= 20, 'insufficient-training-evidence')
    return L._fit(_states(bars,indices,strategy,parameters,deadline,cache),
                  [int(bars[i+HORIZON].close > bars[i].close) for i in indices], strategy)


def _evaluate(bars, indices, strategy, parameters, fit, deadline=None, cache=None):
    states = _states(bars,indices,strategy,parameters,deadline,cache)
    probabilities = [fit['states'][s]['probabilityUp'] for s in states]
    labels = [int(bars[i+HORIZON].close > bars[i].close) for i in indices]
    return metrics(probabilities, labels, [fit['states'][s]['usesPrior'] for s in states]), labels


def _frozen_candidate_model(bars, manifest, protocol, index, warmup, reserved, *, deadline=None, cache=None):
    """Rebuild one deterministic candidate model and its sealed reserve diagnostics."""
    strategy = protocol['strategy']
    parameters = protocol['candidates'][index]
    fit = _fit_at(bars, strategy, parameters, protocol['developmentCutoffs'][-1], warmup, deadline, cache)
    result, labels = _evaluate(bars, reserved, strategy, parameters, fit, deadline, cache)
    model = {'version': VERSION, 'strategy': strategy, 'parameters': parameters, 'fit': fit,
             'symbol': manifest['symbol'], 'source': manifest['source'], 'priceBasis': manifest['priceBasis'],
             'classification': manifest['classification'], 'trainingEnd': protocol['developmentCutoffs'][-1],
             'selectionEnd': protocol['developmentCutoffs'][-1], 'knownHistoryEnd': bars[-1].date,
             'horizonObservations': HORIZON, 'protocolId': protocol['_id'], 'retention': protocol['retention'],
             'interpretation': protocol['interpretation'], 'featureVersion': protocol['featureVersion'],
             'frozenAt': protocol['createdAt'], 'trainingDatasetSha256': protocol['datasetSha256']}
    if 'signalFeatureBundle' in protocol:
        model['signalFeatureBundleSha256'] = protocol['signalFeatureBundle']['sha256']
        model['signalFeaturePolicy'] = dict(protocol['signalFeatureBundle']['ohlcPolicy'])
    return model, result, labels


def run_experiment(store: EvidenceStore, bars: list, protocol: dict, *, incumbent: dict | None = None) -> dict:
    """Candidate selection never receives reserved labels. A completed retry reuses evidence."""
    retention_check(protocol['manifest']['source'], protocol['retention'])
    validate_history(bars, protocol['manifest'])
    L._require(L._digest([bar.to_dict() for bar in bars]) == protocol['rowsSha256'], 'protocol-dataset-changed')
    # Reconstruct all fixed rules; only the creation timestamp is caller supplied.
    signal_config = None if 'signalFeatureBundle' not in protocol else {'ohlcAttested': protocol['signalFeatureBundle']['ohlcPolicy']['attested'], 'ohlcBasis': protocol['signalFeatureBundle']['ohlcPolicy']['basis'], 'completedAt': protocol['signalFeatureBundle']['completedAt'], 'availableAt': protocol['signalFeatureBundle']['availableAt']}
    expected = freeze_protocol(bars, protocol['manifest'], protocol['strategy'], protocol['retention'],
                               protocol['interpretation'], created_at=protocol['createdAt'], incumbent_model=incumbent, signal_toolkit=signal_config)
    L._require(protocol == expected, 'protocol-rules-changed')
    timestamp(protocol['createdAt'])
    store.authorize(protocol['manifest']['source'],protocol['retention'],create=True)
    with store.locked():
        semantic = {k:v for k,v in protocol.items() if k != 'createdAt'}
        equivalent = [r for r in store.records('protocol') if {k:v for k,v in r['payload'].items() if k != 'createdAt'} == semantic]
        frozen = equivalent[0] if equivalent else store.put('protocol', protocol)
        protocol = frozen['payload']
        prior = [r for r in store.records('experiment') if r['payload']['protocolId'] == frozen['id']]
        if prior:
            return prior[0]
        attempts = [r for r in store.records('reference') if r['payload'].get('attemptProtocolId') == frozen['id']]
        L._require(not attempts, 'interrupted-experiment-requires-review')
        store.put('reference', {'attemptProtocolId':frozen['id'], 'status':'started'})
        start = time.monotonic()
        deadline = start + protocol['runtimeSeconds']
        strategy, grid = protocol['strategy'], protocol['candidates']
        warmup = max(L._warmup(p) for p in grid)
        cutoffs = protocol['developmentCutoffs']
        candidates = []
        cache = {}
        for index, parameters in enumerate(grid):
            if time.monotonic()-start >= protocol['runtimeSeconds']:
                candidates.append({'index': index, 'parameters': parameters, 'status': 'runtime-budget-not-attempted'})
                continue
            store.put('reference', {'type':'candidate-attempt','protocolId':frozen['id'],'index':index})
            try:
                windows = []
                for train_end, evaluation_end in zip(cutoffs, cutoffs[1:]):
                    fit = _fit_at(bars,strategy,parameters,train_end,warmup,deadline,cache)
                    indices = [i for i in range(warmup-1,len(bars)-HORIZON)
                               if train_end < bars[i].date and bars[i+HORIZON].date <= evaluation_end]
                    result, labels = _evaluate(bars,indices,strategy,parameters,fit,deadline,cache)
                    windows.append({'trainEnd': train_end, 'evaluationFirst': bars[indices[0]].date,
                                    'evaluationLastLabel': bars[indices[-1]+HORIZON].date, **result,
                                    'baselineBrier': L._score([fit['priorProbabilityUp']]*len(labels), labels)})
                candidates.append({'index': index, 'parameters': parameters, 'status': 'completed', 'windows': windows,
                                   'developmentBrier': sum(w['brier']*w['count'] for w in windows)/sum(w['count'] for w in windows)})
            except L.LearningError as exc:
                if str(exc) != 'research-runtime-exhausted':
                    raise
                candidates.append({'index':index, 'parameters':parameters, 'status':'runtime-budget-interrupted', 'windows':windows})
            store.put('reference', {'type':'candidate-result','protocolId':frozen['id'],'candidate':candidates[-1]})
        completed = [c for c in candidates if c['status'] == 'completed']
        if len(completed) != len(grid):
            return store.put('experiment', {'version': VERSION, 'protocolId': frozen['id'], 'candidates': candidates,
                              'status': 'inconclusive', 'reason': 'runtime-budget-exhausted-no-selection', 'models': []})
        ranking = sorted(completed, key=lambda c:(c['developmentBrier'],c['index']))
        reserved = [i for i in range(warmup-1,len(bars)-HORIZON) if bars[i].date >= protocol['reservedStart']]
        protocol = {**protocol, '_id': frozen['id']}
        models, model_ids = [], set()
        existing = None
        if incumbent is not None:
            L._require(incumbent['source'] == protocol['manifest']['source'] and incumbent['symbol'] == protocol['manifest']['symbol']
                       and incumbent['priceBasis'] == protocol['manifest']['priceBasis'], 'incumbent-source-mismatch')
            existing, _ = _evaluate(bars,reserved,strategy,incumbent['parameters'],incumbent['fit'],deadline,cache)
            imported = dict(incumbent, protocolId=frozen['id'], frozenAt=protocol['createdAt'],
                            retention=protocol['retention'], interpretation=protocol['interpretation'])
            if 'signalFeatureBundle' in protocol:
                imported['signalFeatureBundleSha256'] = protocol['signalFeatureBundle']['sha256']
                imported['signalFeaturePolicy'] = dict(protocol['signalFeatureBundle']['ohlcPolicy'])
            imported_record = store.put('model', imported)
            model_ids.add(imported_record['id'])
            models.append({'id':imported_record['id'], 'candidateIndex':None, 'role':'incumbent',
                             'reservedMetrics':existing, 'comparatorContext':'known-retrospective-comparator'})
        # The incumbent comparator is fixed before reserve evaluation.  An imported
        # predecessor can be byte-identical to a current grid candidate; retain one
        # identity, never a duplicate reference masquerading as a challenger.
        selected_indices = list(dict.fromkeys(([0] if incumbent is None else [])+[c['index'] for c in ranking[:2]]))
        for index in selected_indices:
            model, result, labels = _frozen_candidate_model(bars, protocol['manifest'], protocol, index, warmup, reserved,
                                                              deadline=deadline, cache=cache)
            identity = L._digest(model)
            if identity in model_ids:
                continue
            saved = store.put('model', model)
            model_ids.add(saved['id'])
            models.append({'id': saved['id'], 'candidateIndex': index,
                           'role': 'incumbent' if incumbent is None and index == 0 else 'challenger',
                           'reservedMetrics': result,
                           'baselineBrier': L._score([model['fit']['priorProbabilityUp']] * len(labels), labels)})
        return store.put('experiment', {'version': VERSION, 'protocolId': frozen['id'], 'candidates': candidates,
                         'status': 'inconclusive', 'reason': 'historical-selection-awaits-genuine-forward-checkpoint',
                         'selectedCandidate': ranking[0]['index'], 'models': models, 'existingIncumbentMetrics': existing,
                         'existingIncumbentContext': 'known-retrospective-comparator' if incumbent else 'unavailable',
                         'evaluationContext': 'retrospective-known-history', 'boundary': dict(L.BOUNDARY)})


def semantic_replay(store: EvidenceStore, bars: list, manifest: dict, experiment: dict, *, retention: dict) -> dict:
    """Read-only recomputation of a completed local experiment's recorded meaning.

    Integrity envelopes prove stored bytes; this additionally proves that the
    frozen dataset still yields the retained candidate ranking, fitted models,
    and reserved classification diagnostics.  It never writes evidence or
    creates a new selection.
    """
    # Retention must pass before the direct caller can inspect any durable
    # research record.  Replay never creates an unbound store as a side effect.
    source = manifest.get('source') if type(manifest) is dict else None
    retention_check(source, retention)
    store.authorize(source, retention, create=False)
    # A semantic claim must be made from one stable journal view; otherwise a
    # concurrent append could mix a predecessor, experiment, and model set.
    with store.locked(read_only=True):
        return _semantic_replay_locked(store, bars, manifest, experiment, retention)


def _semantic_replay_locked(store: EvidenceStore, bars: list, manifest: dict, experiment: dict, retention: dict) -> dict:
    validate_history(bars, manifest)
    L._require(type(experiment) is dict and experiment.get('kind') == 'experiment'
               and type(experiment.get('payload')) is dict
               and experiment.get('id') == L._digest(experiment['payload']),
               'invalid-semantic-replay-experiment')
    payload = experiment['payload']
    protocols = {record['id']: record['payload'] for record in store.records('protocol')}
    experiments = {record['id']: record for record in store.records('experiment')}
    # Callers may hand us a record obtained before another process appends, but
    # they cannot turn an arbitrary in-memory structure into replay evidence.
    # The exact immutable experiment must be present in this locked snapshot.
    L._require(experiments.get(experiment['id']) == experiment,
               'semantic-replay-experiment-not-durable')
    L._require(payload['protocolId'] in protocols, 'semantic-replay-protocol-missing')
    protocol = protocols[payload['protocolId']]
    L._require([record for record in experiments.values()
                if record['payload']['protocolId'] == payload['protocolId']] == [experiment],
               'semantic-replay-experiment-set-mismatch')
    L._require(protocol['manifest'] == manifest and protocol['datasetSha256'] == manifest['sha256']
               and protocol['rowsSha256'] == L._digest([bar.to_dict() for bar in bars])
               and protocol['retention'] == retention,
               'semantic-replay-dataset-mismatch')
    L._require(payload.get('reason') != 'runtime-budget-exhausted-no-selection',
               'semantic-replay-incomplete-experiment')
    strategy, grid = protocol['strategy'], protocol['candidates']
    warmup = max(L._warmup(parameters) for parameters in grid)
    models = {record['id']: record['payload'] for record in store.records('model')}
    references = payload['models']
    imported = [reference for reference in references if reference['candidateIndex'] is None]
    if imported:
        L._require(len(imported) == 1 and protocol['incumbentModelSha256'] is not None
                   and payload['existingIncumbentContext'] == 'known-retrospective-comparator'
                   and payload['existingIncumbentMetrics'] is not None,
                   'semantic-replay-incumbent-context-mismatch')
    else:
        L._require(protocol['incumbentModelSha256'] is None and payload['existingIncumbentContext'] == 'unavailable'
                   and payload['existingIncumbentMetrics'] is None,
                   'semantic-replay-incumbent-context-mismatch')
    # Stored protocol bytes and their digest are necessary but not sufficient:
    # a coordinated mutation could recompute candidates/models from altered
    # cutoffs or a moved reserve boundary.  Rebuild every deterministic field
    # from the pinned bars, manifest, retention, interpretation, freeze time,
    # strategy registry, and (when present) imported predecessor provenance
    # before using any stored partition date below.
    incumbent = None
    if imported:
        incumbent_id = imported[0]['id']
        L._require(incumbent_id in models, 'semantic-replay-model-missing')
        incumbent = incumbent_provenance(models[incumbent_id])
    signal_config = None if 'signalFeatureBundle' not in protocol else {'ohlcAttested': protocol['signalFeatureBundle']['ohlcPolicy']['attested'], 'ohlcBasis': protocol['signalFeatureBundle']['ohlcPolicy']['basis'], 'completedAt': protocol['signalFeatureBundle']['completedAt'], 'availableAt': protocol['signalFeatureBundle']['availableAt']}
    expected_protocol = freeze_protocol(
        bars, manifest, strategy, retention, protocol['interpretation'],
        created_at=protocol['createdAt'], incumbent_model=incumbent, signal_toolkit=signal_config)
    L._require(protocol == expected_protocol, 'semantic-replay-protocol-mismatch')
    bound_protocol = {**protocol, '_id': payload['protocolId']}
    cache = {}
    candidates = []
    for index, parameters in enumerate(grid):
        windows = []
        for train_end, evaluation_end in zip(protocol['developmentCutoffs'], protocol['developmentCutoffs'][1:]):
            fit = _fit_at(bars, strategy, parameters, train_end, warmup, cache=cache)
            indices = [i for i in range(warmup - 1, len(bars) - HORIZON)
                       if train_end < bars[i].date and bars[i + HORIZON].date <= evaluation_end]
            result, labels = _evaluate(bars, indices, strategy, parameters, fit, cache=cache)
            windows.append({'trainEnd': train_end, 'evaluationFirst': bars[indices[0]].date,
                            'evaluationLastLabel': bars[indices[-1] + HORIZON].date, **result,
                            'baselineBrier': L._score([fit['priorProbabilityUp']] * len(labels), labels)})
        candidates.append({'index': index, 'parameters': parameters, 'status': 'completed', 'windows': windows,
                           'developmentBrier': sum(window['brier'] * window['count'] for window in windows)
                           / sum(window['count'] for window in windows)})
    L._require(candidates == payload['candidates'], 'semantic-replay-candidate-mismatch')
    ranking = sorted(candidates, key=lambda candidate: (candidate['developmentBrier'], candidate['index']))
    L._require(payload['selectedCandidate'] == ranking[0]['index'], 'semantic-replay-selection-mismatch')
    reserved = [i for i in range(warmup - 1, len(bars) - HORIZON) if bars[i].date >= protocol['reservedStart']]
    requested = list(dict.fromkeys(([] if imported else [0]) + [candidate['index'] for candidate in ranking[:2]]))
    seen_model_ids = {reference['id'] for reference in imported}
    selected = []
    for index in requested:
        expected_model, _, _ = _frozen_candidate_model(bars, manifest, bound_protocol, index, warmup, reserved,
                                                        cache=cache)
        if L._digest(expected_model) not in seen_model_ids:
            selected.append(index)
            seen_model_ids.add(L._digest(expected_model))
    expected_indices = ([None] if imported else []) + selected
    L._require(type(references) is list and [reference.get('candidateIndex') for reference in references] == expected_indices
               and len({reference.get('id') for reference in references}) == len(references)
               and all(type(reference.get('id')) is str and L._hash(reference['id']) for reference in references),
               'semantic-replay-model-set-mismatch')
    L._require({record_id for record_id, model in models.items()
                if model['protocolId'] == payload['protocolId']} == {reference['id'] for reference in references},
               'semantic-replay-store-model-membership-mismatch')
    for reference in references:
        expected_keys = ({'id', 'candidateIndex', 'role', 'reservedMetrics', 'comparatorContext'}
                         if reference['candidateIndex'] is None else
                         {'id', 'candidateIndex', 'role', 'reservedMetrics', 'baselineBrier'})
        L._require(set(reference) == expected_keys, 'semantic-replay-model-reference-invalid')
    for reference in references:
        L._require(reference['id'] in models, 'semantic-replay-model-missing')
        model = models[reference['id']]
        if reference['candidateIndex'] is None:
            result, _ = _evaluate(bars, reserved, strategy, model['parameters'], model['fit'], cache=cache)
            original = incumbent_provenance(model)
            expected = {**original, 'protocolId': payload['protocolId'], 'frozenAt': protocol['createdAt'],
                        'retention': protocol['retention'], 'interpretation': protocol['interpretation']}
            if 'signalFeatureBundle' in protocol:
                expected['signalFeatureBundleSha256'] = protocol['signalFeatureBundle']['sha256']
                expected['signalFeaturePolicy'] = dict(protocol['signalFeatureBundle']['ohlcPolicy'])
            L._require(model == expected and L._digest(original) == protocol['incumbentModelSha256']
                       and reference['role'] == 'incumbent' and reference['reservedMetrics'] == result
                       and payload['existingIncumbentMetrics'] == result,
                       'semantic-replay-incumbent-provenance-mismatch')
            continue
        index = reference['candidateIndex']
        expected, result, labels = _frozen_candidate_model(bars, manifest, bound_protocol, index, warmup, reserved,
                                                            cache=cache)
        L._require(model == expected and reference['id'] == L._digest(expected)
                   and reference['role'] == ('incumbent' if not imported and index == 0 else 'challenger')
                   and reference['reservedMetrics'] == result
                   and reference['baselineBrier'] == L._score([expected['fit']['priorProbabilityUp']] * len(labels), labels),
                   'semantic-replay-model-mismatch')
    return {'semanticReplay': 'verified', 'replayedCandidates': len(candidates), 'experimentId': experiment['id']}


def import_learning_model(path: str | Path, manifest: dict, retention: dict, interpretation: dict) -> dict:
    retention_check(manifest['source'],retention)
    artifact = L.load_artifact(path)
    m = artifact['model']
    L._require(m['sourceSha256'] == L._digest(manifest['source']) and all(m[k] == manifest[k] for k in ('symbol','priceBasis','classification')), 'existing-model-source-mismatch')
    L._require(m['horizonBars'] == HORIZON, 'existing-model-horizon-mismatch')
    return {'version':VERSION, 'strategy':m['strategy'], 'parameters':m['parameters'], 'fit':m['fit'],
            'symbol':m['symbol'], 'source':manifest['source'], 'priceBasis':m['priceBasis'],
            'classification':m['classification'], 'trainingEnd':m['lastTrainingLabelDate'],
            'selectionEnd':artifact['report']['validationEnd'], 'knownHistoryEnd':artifact['report']['datasetLastDate'],
            'horizonObservations':HORIZON, 'featureVersion':'registry-history-state.v1',
            'trainingDatasetSha256':artifact['report']['datasetSha256'], 'importedArtifactSha256':artifact['sha256'],
            'retention':retention, 'interpretation':interpretation}


@dataclass(frozen=True)
class ResearchObservation(Bar):
    """market-observations.v2 close-only adapter; absent OHLCV remains None."""
    open: float | None
    high: float | None
    low: float | None
    volume: float | None


def load_observation_dataset(path: str | Path, *, retention: dict, expected_sha256: str,
                             allow_synthetic_smoke: bool = False) -> tuple[list,dict]:
    from .feed_collection import validate_observations, validate_interpretation as check_meaning
    retention_check('etoro',retention)
    raw = L._read(path,32*1024*1024)
    import hashlib
    L._require(hashlib.sha256(raw).hexdigest() == expected_sha256, 'observation-version-checksum-mismatch')
    packet = L._json(raw)
    L._keys(packet, {'schemaVersion','symbol','source','observations','interpretation','retention','unresolvedMissingDates'})
    L._require(packet['schemaVersion'] == 'market-observations.v2' and packet['source'] == 'etoro', 'unsupported-observation-contract')
    retention_check('etoro',packet['retention'])
    check_meaning(packet['interpretation'],packet['symbol'])
    rows = validate_observations(packet['observations'],required_fields=('close',))
    unavailable=packet['unresolvedMissingDates']
    L._require(type(unavailable) is list and unavailable==sorted(set(unavailable)) and all(L._iso(d) for d in unavailable)
               and set(unavailable)<={r['date'] for r in rows}, 'invalid-withdrawn-observation-dates')
    rows=[r for r in rows if r['date'] not in unavailable]
    bars = [ResearchObservation(packet['symbol'],r['date'],r['open'],r['high'],r['low'],r['close'],r['volume'],'etoro') for r in rows]
    manifest = {'version':'learning-dataset.v1', 'symbol':packet['symbol'],'source':'etoro',
        'classification':'synthetic' if allow_synthetic_smoke else 'observed-attested',
        'sourceEvidence':'market-observations.v2 immutable instrument mapping and interpretation',
        'licenseEvidence':'separate written model use and retention exception required',
        'rightsEvidence':'current collector rights gate verified before local read',
        'attribution':'eToro market observations; private research only',
        'priceBasis':packet['interpretation']['priceBasis'],'sha256':expected_sha256,'rowCount':len(bars)}
    validate_history(bars,manifest)
    return bars,manifest


def validate_model_schema(model: dict) -> None:
    keys = {'version','strategy','parameters','fit','symbol','source','priceBasis','classification','trainingEnd',
            'selectionEnd','knownHistoryEnd','horizonObservations','featureVersion','trainingDatasetSha256',
            'retention','interpretation','protocolId','frozenAt'}
    extra = ({'importedArtifactSha256'} if 'importedArtifactSha256' in model else set()) | ({'signalFeatureBundleSha256', 'signalFeaturePolicy'} if 'signalFeatureBundleSha256' in model or 'signalFeaturePolicy' in model else set())
    L._require(('signalFeatureBundleSha256' in model) == ('signalFeaturePolicy' in model), 'incomplete-signal-feature-model-binding')
    L._keys(model,keys | extra)
    L._require(model['version'] == VERSION and model['featureVersion'] == 'registry-history-state.v1'
               and model['horizonObservations'] == HORIZON, 'unsupported-model-contract')
    L._require(model['parameters'] in L.candidate_grid(model['strategy']), 'unfrozen-model-parameters')
    L._validate_fit(model['fit'],model['strategy'])
    L._require(L._iso(model['trainingEnd']) <= L._iso(model['selectionEnd']) < L._iso(model['knownHistoryEnd']), 'model-date-order')
    L._require(L._hash(model['trainingDatasetSha256']) and L._hash(model['protocolId']), 'invalid-model-provenance')
    timestamp(model['frozenAt'])
    validate_interpretation(model['interpretation'])


def validate_record_payload(kind: str, payload: dict) -> None:
    if kind == 'protocol':
        protocol_keys = {'version','createdAt','datasetSha256','rowsSha256','manifest','retention','interpretation',
            'strategy','featureVersion','horizonObservations','incumbentModelSha256','candidates','maxConfigurationsPerFamily',
            'runtimeSeconds','developmentCutoffs','reservedStart','selectionRule','evaluationContext','checkpointPolicy','boundary'}
        L._keys(payload, protocol_keys | ({'signalFeatureBundle'} if 'signalFeatureBundle' in payload else set()))
        L._require(payload['version'] == VERSION and payload['horizonObservations'] == HORIZON
                   and payload['runtimeSeconds'] == 120 and payload['maxConfigurationsPerFamily'] == 24
                   and payload['candidates'] == L.candidate_grid(payload['strategy']), 'protocol-contract-drift')
        L._manifest(payload['manifest'])
        L._require(payload['datasetSha256'] == payload['manifest']['sha256'] and L._hash(payload['rowsSha256']), 'protocol-provenance-mismatch')
        L._require(payload['incumbentModelSha256'] is None or L._hash(payload['incumbentModelSha256']), 'invalid-incumbent-version')
        validate_interpretation(payload['interpretation'])
        L._require(payload['checkpointPolicy'] == {'minimumCompleted':60,'checkpointCompleted':60,'minimumUsefulImprovement':.005,
            'blockLength':5,'bootstrapReplicates':2000,'confidence':.95,'maxComparisons':2,'maxReplacements':1,'maxChallengers':2}, 'checkpoint-policy-drift')
        dates=payload['developmentCutoffs']
        L._require(type(dates) is list and len(dates)==4 and dates==sorted(set(dates)), 'invalid-development-dates')
        L._require(all(L._iso(d) for d in dates) and dates[-1]<L._iso(payload['reservedStart']), 'invalid-reserved-date')
        timestamp(payload['createdAt'])
        if 'signalFeatureBundle' in payload:
            S.validate_feature_bundle(payload['signalFeatureBundle'])
        L._require(payload['boundary']==L.BOUNDARY and payload['evaluationContext']=='retrospective-known-history', 'research-boundary-changed')
    elif kind == 'model':
        validate_model_schema(payload)
    elif kind == 'experiment':
        if payload.get('reason') == 'runtime-budget-exhausted-no-selection':
            L._keys(payload,{'version','protocolId','candidates','status','reason','models'})
            L._require(payload['models']==[], 'partial-search-cannot-select')
        else:
            L._keys(payload,{'version','protocolId','candidates','status','reason','selectedCandidate','models',
                'existingIncumbentMetrics','existingIncumbentContext','evaluationContext','boundary'})
            L._require(type(payload['models']) is list and 1 <= len(payload['models']) <= 3
                       and all(type(reference) is dict for reference in payload['models']),
                       'invalid-incumbent-challenger-set')
            references = payload['models']
            L._require(all(type(reference.get('id')) is str and L._hash(reference['id'])
                               and reference.get('role') in ('incumbent', 'challenger')
                               and (reference.get('candidateIndex') is None
                                    or L._integer(reference.get('candidateIndex'), 0, 23))
                               for reference in references)
                       and len({reference['id'] for reference in references}) == len(references)
                       and sum(reference['role'] == 'incumbent' for reference in references) == 1
                       and references[0]['role'] == 'incumbent', 'invalid-incumbent-challenger-set')
            for reference in references:
                expected_keys = ({'id', 'candidateIndex', 'role', 'reservedMetrics', 'comparatorContext'}
                                 if reference['candidateIndex'] is None else
                                 {'id', 'candidateIndex', 'role', 'reservedMetrics', 'baselineBrier'})
                L._require(set(reference) == expected_keys and type(reference['reservedMetrics']) is dict,
                           'invalid-experiment-model-reference')
                if reference['candidateIndex'] is None:
                    L._require(reference['role'] == 'incumbent'
                               and reference['comparatorContext'] == 'known-retrospective-comparator',
                               'invalid-experiment-model-reference')
                else:
                    L._require(L._number(reference['baselineBrier']), 'invalid-experiment-model-reference')
            L._require(payload['boundary']==L.BOUNDARY, 'research-boundary-changed')
            completed=payload['candidates']
            L._require(type(completed) is list and bool(completed)
                       and all(type(candidate) is dict and set(candidate) == {'index','parameters','status','windows','developmentBrier'}
                               and L._integer(candidate['index'], 0, 23) and type(candidate['parameters']) is dict
                               and candidate['status'] == 'completed' and type(candidate['windows']) is list
                               and L._number(candidate['developmentBrier']) for candidate in completed)
                       and len({candidate['index'] for candidate in completed}) == len(completed), 'incomplete-selection')
            winner=min(completed,key=lambda c:(c['developmentBrier'],c['index']))['index']
            L._require(payload['selectedCandidate']==winner, 'selection-result-mismatch')
        L._require(payload['version']==VERSION and payload['status']=='inconclusive' and L._hash(payload['protocolId']), 'invalid-experiment-contract')
