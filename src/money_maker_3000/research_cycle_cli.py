"""Short local coordinator; collection is an explicit separate, rights-gated component."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import learning as L
from . import forward_evaluation as F
from .research_cycle import EvidenceStore, freeze_protocol, import_learning_model, now, retention_check, run_experiment, load_observation_dataset


def contained(root: Path, relative: str) -> Path:
    L._require(type(relative) is str and relative and not Path(relative).is_absolute()
               and all(p not in ('.','..') for p in Path(relative).parts), 'invalid-contained-path')
    current = root
    for part in (None,*Path(relative).parts):
        if part is not None:
            current /= part
        L._require(not current.is_symlink(), 'symlink-path-rejected')
    return current


def resolve_source_policies(entries):
    """A source cannot acquire a different entitlement by dictionary overwrite."""
    grouped={}
    for entry in entries:
        L._require(type(entry) is dict and type(entry.get('source')) is str,'invalid-source-policy-entry')
        grouped.setdefault(entry['source'],[]).append(entry.get('retention'))
    resolved,errors={},{}
    for source,policies in grouped.items():
        try:
            for policy in policies:
                retention_check(source,policy)
            L._require(all(L._canonical(policy)==L._canonical(policies[0]) for policy in policies),'conflicting-source-retention-attestations')
            resolved[source]=policies[0]
        except (ValueError,TypeError):
            errors[source]='source-retention-inactive-or-conflicting-attestations'
    return resolved,errors


def coordinate(config_path: str | Path, *, mode: str = 'cycle', allow_synthetic: bool = False) -> dict:
    L._require(mode in ('cycle','status','replay'), 'invalid-cycle-mode')
    config = L._json(L._read(config_path,L.MAX_JSON_BYTES))
    L._keys(config, {'version','dataRoot','storeRoot','inventoryPath','interpretations','existingModels','availability','collection','portability'})
    L._require(config['version'] == 'continuous-research-config.v1', 'invalid-cycle-config')
    data_root, store_root = Path(config['dataRoot']), Path(config['storeRoot'])
    inventory = L._json(L._read(contained(data_root,config['inventoryPath']),L.MAX_JSON_BYTES))
    L._keys(inventory, {'version','datasets'})
    L._require(inventory['version'] == 'money-maker-learning-inventory.v1' and type(inventory['datasets']) is list
               and 1 <= len(inventory['datasets']) <= 20, 'invalid-cycle-inventory')
    source_policies,source_policy_errors=resolve_source_policies(inventory['datasets'])
    collection_report = {'status':'not-configured'}
    if mode == 'cycle' and config['collection'] is not None:
        collection_report = refresh_collection(config['collection'])
    portability_report = {'status':'not-configured'}
    if config['portability'] is not None:
        from .portability import load_report
        try:
            L._keys(config['portability'], {'reportPath','reportSources'})
            sources=config['portability']['reportSources']
            L._require(type(sources) is list and len(sources)==2 and len(set(sources))==2, 'invalid-portability-source-pair')
            L._require(not any(source in source_policy_errors for source in sources),'portability-source-retention-conflict')
            current={source:source_policies[source] for source in sources if source in source_policies}
            L._require(set(current)==set(sources),'portability-current-source-attestation-missing')
            full = load_report(config['portability']['reportPath'],current_retentions=current)
            portability_report = {'strategies':[{'strategy':x['strategy'],'verdict':x['verdict'],'reasons':x['reasons']} for x in full['strategies']], 'limitations':full['limitations']}
        except (ValueError,OSError,KeyError):
            portability_report = {'status':'inconclusive','reason':'portability-report-unavailable-or-retention-blocked'}
    entries = list(inventory['datasets'])
    collection_blocked = []
    if config['collection'] is not None:
        try:
            extra,meaning,availability = collected_entries(config['collection'])
            entries.extend(extra)
            config['interpretations'].update(meaning)
            config['availability'].update(availability)
        except (ValueError,OSError,KeyError,TypeError):
            collection_blocked.append({'source':'etoro','reason':'retained-collector-datasets-unavailable-or-rights-blocked'})
    results, blocked = [], collection_blocked
    seen = set()
    for entry in entries:
        try:
            L._keys(entry, {'observationPath','source','symbol','retention','versionSha256'} if 'observationPath' in entry else {'csvPath','manifestPath','source','symbol','retention'})
            source, symbol = entry['source'],entry['symbol']
            L._require(type(source) is str and source and '/' not in source and source not in ('.','..')
                       and symbol in ('SPY','QQQ','VAS'), 'invalid-source-identity')
            L._require((source,symbol) not in seen, 'duplicate-cycle-dataset')
            seen.add((source,symbol))
            L._require(source not in source_policy_errors,source_policy_errors.get(source,'invalid-source-retention'))
            retention_check(source,entry['retention'])
            interpretation = config['interpretations'].get(source+'/'+symbol,config['interpretations'].get(source))
            for strategy in L.STATES:
                root = store_root/source/symbol/strategy
                if mode == 'status':
                    if not root.exists():
                        results.append({'source':source,'symbol':symbol,'strategy':strategy,'status':'not-started'})
                        continue
                    store = EvidenceStore(root,create=False)
                    results.append({'source':source,'symbol':symbol,'strategy':strategy, **F.status(store,source=source,retention=entry['retention'])})
                    continue
                if 'observationPath' in entry:
                    bars,manifest = load_observation_dataset(entry['observationPath'],retention=entry['retention'],expected_sha256=entry['versionSha256'],allow_synthetic_smoke=allow_synthetic)
                else:
                    bars,manifest = L.load_dataset(contained(data_root,entry['csvPath']),contained(data_root,entry['manifestPath']),allow_synthetic_smoke=allow_synthetic)
                L._require((manifest['source'],manifest['symbol']) == (source,symbol), 'inventory-source-mismatch')
                store = EvidenceStore(root,create=mode != 'replay')
                store.authorize(source,entry['retention'],create=mode=='cycle')
                with store.locked(read_only=mode == 'replay'):
                    experiments = store.records('experiment')
                    protocols = {r['id']:r['payload'] for r in store.records('protocol')}
                    experiments.sort(key=lambda r: protocols[r['payload']['protocolId']]['createdAt'])
                if mode == 'replay':
                    L._require(bool(experiments), 'no-cycle-to-replay')
                    experiment = experiments[-1]
                    protocol = protocols[experiment['payload']['protocolId']]
                    L._require(protocol['datasetSha256'] == manifest['sha256'], 'replay-dataset-version-mismatch')
                    # Reads verify digest envelopes. Model fits and source contracts are independently validated.
                    with store.locked(read_only=True):
                        for model in store.records('model'):
                            F._eligible_model(model['payload'],manifest)
                    results.append({'source':source,'symbol':symbol,'strategy':strategy,'action':'integrity-replay-no-recomputed-historical-metrics',
                                    'experimentId':experiment['id'], **F.status(store,source=source,retention=entry['retention'])})
                    continue
                availability = config['availability'].get(source+'/'+symbol)
                if availability:
                    L._keys(availability, {'retrievedAt','completedAt','datasetSha256'} | ({'missingObservationDates'} if 'missingObservationDates' in availability else set()))
                    L._require(availability['datasetSha256'] == manifest['sha256'], 'availability-dataset-version-mismatch')
                scored = F.score_matured(store,bars,manifest,retention=entry['retention'],retrieved_at=availability['retrievedAt'],unavailable_dates=availability.get('missingObservationDates',[])) if availability else {'matured':0,'revised':0,'pending':'availability-metadata-required'}
                if not experiments:
                    old_path = config['existingModels'].get(source+'/'+symbol+'/'+strategy)
                    incumbent = import_learning_model(old_path,manifest,entry['retention'],interpretation) if old_path else None
                    if incumbent:
                        L._require(incumbent['strategy'] == strategy, 'existing-model-strategy-mismatch')
                    protocol = freeze_protocol(bars,manifest,strategy,entry['retention'],interpretation,incumbent_model=incumbent)
                    experiment = run_experiment(store,bars,protocol,incumbent=incumbent)
                    action = 'research-cycle-completed'
                else:
                    experiment = experiments[-1]
                    action = 'no-new-research-awaiting-forward-checkpoint'
                p = experiment['payload']
                model_ids = [m['id'] for m in p['models']]
                checkpoints = []
                incumbents = [m['id'] for m in p['models'] if m['role'] == 'incumbent']
                if incumbents:
                    for challenger in (m for m in model_ids if m != incumbents[0]):
                        checkpoints.append(F.evaluate_checkpoint(store,p['protocolId'],incumbents[0],challenger,source=source,retention=entry['retention']))
                # A successor may use completed evidence only after the original fixed
                # comparisons finish and sixty genuinely later observations arrive.
                if experiments and checkpoints and all('payload' in c for c in checkpoints):
                    with store.locked():
                        saved_models = {r['id']:r['payload'] for r in store.records('model')}
                        last_known = max(saved_models[i]['knownHistoryEnd'] for i in model_ids)
                        references = [r['payload'] for r in store.records('reference')
                                      if r['payload'].get('protocolId') == p['protocolId'] and 'checkpointId' in r['payload']]
                        checkpoint_by_id = {r['id']:r['payload'] for r in store.records('checkpoint')}
                    if sum(b.date > last_known for b in bars) >= 60:
                        references.sort(key=lambda r:checkpoint_by_id[r['checkpointId']]['createdAt'])
                        current_id = references[-1]['modelId'] if references else incumbents[0]
                        successor_protocol = freeze_protocol(bars,manifest,strategy,entry['retention'],interpretation,incumbent_model=saved_models[current_id])
                        experiment = run_experiment(store,bars,successor_protocol,incumbent=saved_models[current_id])
                        p = experiment['payload']
                        model_ids = [m['id'] for m in p['models']]
                        action = 'successor-research-cycle-completed-from-finished-evidence'
                predictions = F.record_predictions(store,bars,manifest,model_ids,retention=entry['retention'],
                               retrieved_at=availability['retrievedAt'],completed_at=availability['completedAt']) if availability and model_ids else {'created':0,'reason':'availability-metadata-required-or-no-models'}
                results.append({'source':source,'symbol':symbol,'strategy':strategy,'action':action,
                                'newPredictionEndpoint':predictions.get('created',0) > 0,
                                'experimentId':experiment['id'],'scoring':scored,'predictions':predictions,
                                'checkpoints':checkpoints, **F.status(store,source=source,retention=entry['retention'])})
        except (L.LearningError,KeyError,OSError,TypeError,ValueError) as exc:
            blocked.append({'source':entry.get('source','unavailable'),'symbol':entry.get('symbol','unavailable'),
                            'reason':str(exc) if isinstance(exc,L.LearningError) else 'source-cycle-unavailable-check-private-inputs'})
    return {'version':'continuous-research-workflow.v1','mode':mode,
            'status':'partial' if blocked and results else 'blocked' if blocked else 'complete',
            'results':results,'blocked':blocked,'collection':collection_report, 'portability':portability_report,
            'boundary':dict(L.BOUNDARY)}


def collected_entries(config):
    """Bind latest immutable collector versions without rewriting the original inventory."""
    from datetime import timedelta
    from .feed_collection import preflight_collection
    from .research_cycle import timestamp
    preflight_collection(config['retention'],config['interpretations'],tuple(config['symbols']),now())
    entries,availability = [],{}
    root = Path(config['outputRoot'])
    meaning = {}
    for symbol in config['symbols']:
        paths = sorted(root.glob(symbol+'-retrieval-*.json'))
        if not paths:
            continue
        record = L._json(L._read(paths[-1],L.MAX_JSON_BYTES))
        L._require(record['schemaVersion']=='market-retrieval.v2','legacy-collection-availability-unverified')
        L._require(record['symbol'] == symbol and record['source'] == 'etoro' and L._hash(record['version']), 'invalid-collection-version')
        path = contained(root,symbol+'-'+record['version']+'.json')
        bars,manifest = load_observation_dataset(path,retention=config['retention'],expected_sha256=record['version'])
        snapshot=L._json(L._read(path,32*1024*1024))
        L._require(record['unresolvedMissingDates']==snapshot['unresolvedMissingDates'],'collection-availability-version-mismatch')
        m = config['interpretations'][symbol]
        core_meaning = {'instrumentVerified':True,'trainingPermitted':True,'currency':m['currency'],
            'session':m['sessionConvention'],'timestampMeaning':'OneDay candle start; 26-hour completion guard',
            'priceType':'provider daily candle close; see reviewed interpretation', 'adjustments':m['priceBasis'],
            'costs':'undocumented; no transaction costs modeled','evidence':m['interpretationEvidence']}
        meaning['etoro/'+symbol] = core_meaning
        entries.append({'source':'etoro','symbol':symbol,'retention':config['retention'],
                        'observationPath':str(path),'versionSha256':record['version']})
        availability['etoro/'+symbol] = {'datasetSha256':manifest['sha256'],'retrievedAt':record['retrievedAt'],
            'completedAt':(timestamp(bars[-1].date+'T00:00:00Z')+timedelta(days=1)).isoformat(),
            'missingObservationDates':record['unresolvedMissingDates']}
    L._require(bool(entries),'no-completed-collector-datasets')
    return entries,meaning,availability


def refresh_collection(config: dict) -> dict:
    """Fixed read-only collector; no arbitrary executable configuration."""
    from datetime import datetime,timezone
    from .feed_collection import preflight_collection, collect, EtoroReader
    try:
        L._keys(config, {'profilePath','outputRoot','retention','interpretations','symbols'})
        preflight_collection(config['retention'],config['interpretations'],tuple(config['symbols']),now())
        # Construction can access only the explicitly configured profile after rights gate.
        reader = EtoroReader(Path(config['profilePath']))
        return collect(reader,symbols=tuple(config['symbols']),retrieved_at=now(),
                       output_root=Path(config['outputRoot']),retention=config['retention'],
                       interpretations=config['interpretations'])
    except (ValueError,OSError,KeyError,TypeError):
        return {'status':'inconclusive','reason':'collection-unavailable-or-research-rights-blocked'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Configured local research cycle; no trading')
    parser.add_argument('mode',choices=('cycle','status','replay'))
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--allow-synthetic-smoke',action='store_true')
    args = parser.parse_args(argv)
    try:
        result = coordinate(args.config,mode=args.mode,allow_synthetic=args.allow_synthetic_smoke)
    except (L.LearningError,OSError,ValueError,TypeError,KeyError):
        result = {'status':'blocked','reason':'invalid-or-unavailable-cycle-config','boundary':dict(L.BOUNDARY)}
    print(json.dumps(result,sort_keys=True,allow_nan=False))
    return 1 if result['status'] in ('partial','blocked') else 0


if __name__ == '__main__':
    raise SystemExit(main())
