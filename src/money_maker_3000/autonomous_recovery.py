"""Consistent private snapshots, isolated restore and semantic economic replay."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
import fcntl
import hashlib
import os
import stat

from . import autonomous_research as A, shadow_portfolio as H, signal_toolkit as S, learning as L
from .economic_portfolio import require, validate_policy


class PrefixView:
    def __init__(self, records):
        self._records = records
    def records(self, kind=None):
        return [record for record in self._records if kind is None or record['kind'] == kind]


def semantic_replay(store):
    """Recompute models, trial/economic selection, decisions and score revisions.

    Digest validity is necessary but insufficient: replay checks meanings against
    the exact frozen registry and chronological prefix present at each event.
    """
    with store.locked(read_only=True):
        return _replay(store.records())


def _replay(records):
    protocols = {}; inputs = {}; models = {}; trial_results = {}; completed = {}; decisions = {}
    for index, record in enumerate(records):
        kind = record['kind']; value = record['payload']; view = PrefixView(records[:index])
        if kind == 'input':
            require(set(value) == {'packet', 'sha256'} and value['sha256'] == S.digest(value['packet']), 'replay-input-identity')
            A.validate_packet(value['packet'], allow_synthetic=True)
            inputs[record['id']] = value['packet']
        elif kind == 'protocol':
            keys = {'version', 'inputId', 'inputSha256', 'symbol', 'currency', 'source', 'classification', 'policy', 'strategy', 'candidates',
                    'trainEnd', 'selectionEnd', 'knownHistoryEnd', 'horizon', 'blockLength', 'minimumBlocks', 'minimumRoundTrips',
                    'alpha', 'comparisonCount', 'selectionRule', 'holdoutRule', 'confirmationRule', 'boundary'}
            require(set(value) == keys and value['inputId'] in inputs, 'replay-protocol-schema')
            packet = inputs[value['inputId']]; rows = packet['observations']; validate_policy(value['policy'])
            require(value['version'] == A.VERSION and value['strategy'] == A.STRATEGY and value['candidates'] == L.candidate_grid(A.STRATEGY)
                    and value['inputSha256'] == S.digest(packet) and value['symbol'] == packet['symbol'] and value['source'] == packet['source']
                    and value['currency'] == packet['interpretation']['currency'] and value['classification'] == packet['classification']
                    and value['trainEnd'] == int(len(rows)*.6)-1 and value['selectionEnd'] == int(len(rows)*.8)-1
                    and value['knownHistoryEnd'] == rows[-1]['date'] and value['horizon'] == 5 and value['blockLength'] == 5
                    and value['minimumBlocks'] == 20 and value['minimumRoundTrips'] == 10 and value['alpha'] == .05
                    and value['selectionRule'] == 'greatest-development-net-return-minus-matched-passive; tie-lowest-index'
                    and value['holdoutRule'] == 'selected-candidate-only; reject-or-insufficient; no-retune'
                    and value['confirmationRule'] == 'first-105-prospective-observations-fixed; revisions-only; three-economic-comparisons'
                    and value['comparisonCount'] == 9*(len(protocols)+1)*(len(protocols)+2) and value['boundary'] == A.BOUNDARY,
                    'replay-frozen-protocol-drift')
            require(not A.validate_packet(packet, allow_synthetic=True), 'replay-ineligible-protocol')
            protocols[record['id']] = value
        elif kind == 'model':
            require(set(value) == {'protocolId', 'candidateIndex', 'model'} and value['protocolId'] in protocols, 'replay-model-schema')
            protocol = protocols[value['protocolId']]; candidate = value['candidateIndex']
            require(type(candidate) is int and 0 <= candidate < len(protocol['candidates']), 'replay-model-candidate')
            expected = A.model_for(inputs[protocol['inputId']], protocol, protocol['candidates'][candidate])
            require(value['model'] == expected, 'replay-model-semantic-mismatch'); models[record['id']] = value
        elif kind == 'trial-start':
            require(set(value) == {'protocolId', 'candidateIndex'} and value['protocolId'] in protocols
                    and type(value['candidateIndex']) is int and 0 <= value['candidateIndex'] < 3, 'replay-trial-start-schema')
        elif kind == 'trial-result':
            require(value.get('protocolId') in protocols, 'replay-trial-protocol')
            protocol = protocols[value['protocolId']]; key = (value['protocolId'], value.get('candidateIndex'))
            require(key not in trial_results and any(event['kind'] == 'trial-start' and event['payload'] == {'protocolId': key[0], 'candidateIndex': key[1]} for event in records[:index]), 'replay-duplicate-or-unstarted-trial')
            if value.get('status') == 'evaluated':
                require(set(value) == {'protocolId', 'candidateIndex', 'status', 'modelId', 'development'} and value['modelId'] in models, 'replay-trial-schema')
                model = models[value['modelId']]
                require(model['protocolId'] == key[0] and model['candidateIndex'] == key[1], 'replay-trial-model-parity')
                expected = A.evaluate(model['model'], inputs[protocol['inputId']], protocol, protocol['trainEnd']+1, protocol['selectionEnd'])
                require(value['development'] == expected, 'replay-trial-economic-mismatch')
            else:
                require(set(value) == {'protocolId', 'candidateIndex', 'status', 'reason'} and value['status'] == 'mechanically-rejected'
                        and value['reason'] == 'frozen-trial-invalid', 'replay-failed-trial-schema')
                try:
                    model = A.model_for(inputs[protocol['inputId']], protocol, protocol['candidates'][key[1]])
                    A.evaluate(model, inputs[protocol['inputId']], protocol, protocol['trainEnd']+1, protocol['selectionEnd'])
                except (A.EconomicError, L.LearningError, S.SignalError):
                    pass
                else:
                    raise A.EconomicError('replay-fabricated-trial-failure')
            trial_results[key] = record
        elif kind == 'research-result':
            require(value.get('protocolId') in protocols, 'replay-result-protocol')
            protocol_id = value['protocolId']; protocol = protocols[protocol_id]; packet = inputs[protocol['inputId']]
            all_trials = [trial_results.get((protocol_id, candidate)) for candidate in range(3)]
            require(all(all_trials) and value['trialIds'] == [trial['id'] for trial in all_trials], 'replay-incomplete-grid-selection')
            valid = [trial for trial in all_trials if trial['payload']['status'] == 'evaluated']
            if valid:
                selected = max(valid, key=lambda trial: (trial['payload']['development']['monetaryEdge'], -trial['payload']['candidateIndex']))
                model_id = selected['payload']['modelId']; model = models[model_id]['model']
                holdout = A.evaluate(model, packet, protocol, protocol['selectionEnd']+1, len(packet['observations'])-1)
                sensitivity = A.evaluate(model, packet, protocol, protocol['selectionEnd']+1, len(packet['observations'])-1, double_cost=True)
                status = 'rejected' if holdout['monetaryEdge'] <= 0 or holdout['candidate']['simulatedNetReturn'] <= 0 or sensitivity['monetaryEdge'] <= 0 else 'insufficient-evidence'
                expected = {'protocolId': protocol_id, 'status': status, 'trialIds': [trial['id'] for trial in all_trials],
                            'selectedModelId': model_id, 'selectionTrialId': selected['id'], 'holdout': holdout,
                            'doubleCostSensitivity': sensitivity, 'uncertainty': A.uncertainty(holdout['pairedBlockDifferences'], protocol['comparisonCount']),
                            'monitoringEligible': False, 'evidenceType': 'retrospective-known-history',
                            'reason': 'prospective-independent-confirmation-required', 'boundary': A.BOUNDARY}
            else:
                expected = {'protocolId': protocol_id, 'status': 'mechanically-rejected', 'trialIds': [trial['id'] for trial in all_trials], 'selectedModelId': None}
            require(value == expected, 'replay-research-semantic-mismatch'); completed[protocol_id] = record
        elif kind == 'decision':
            keys = {'protocolId', 'modelId', 'symbol', 'currency', 'originDate', 'originEnd', 'originClose', 'probabilityUp', 'inputSha256',
                    'featureRowsSha256', 'decisionAt', 'horizon', 'evidenceType', 'activeReferenceId', 'boundary'}
            require(set(value) == keys and value['modelId'] in models and value['protocolId'] in completed, 'replay-decision-schema')
            packet = next((packet for packet in inputs.values() if S.digest(packet) == value['inputSha256']), None)
            require(packet is not None, 'replay-missing-decision-input')
            rows = packet['observations']; model = models[value['modelId']]['model']; protocol = protocols[value['protocolId']]
            expected = A.forecast(model, packet, len(rows)-1)
            require(value['probabilityUp'] == expected['probabilityUp'] and value['originDate'] == rows[-1]['date']
                    and value['originClose'] == rows[-1]['close'] and value['originEnd'] == rows[-1]['end']
                    and value['featureRowsSha256'] == S.digest(rows) and value['originDate'] > model['knownHistoryEnd']
                    and models[value['modelId']]['protocolId'] == value['protocolId']
                    and any(event['id'] == value['activeReferenceId'] for event in view.records('reference'))
                    and value['symbol'] == packet['symbol'] and value['currency'] == protocol['currency'] and value['horizon'] == 5
                    and S.utc_timestamp(rows[-1]['availableAt']) <= S.utc_timestamp(value['decisionAt']) <= S.utc_timestamp(record['createdAt'])
                    and value['evidenceType'] == ('genuine-prospective' if packet['classification'] == 'observed-attested' else 'synthetic-mechanics')
                    and value['boundary'] == A.BOUNDARY, 'replay-decision-semantic-mismatch')
            require(not any(prior['payload']['modelId'] == value['modelId'] and prior['payload']['originDate'] == value['originDate'] for prior in decisions.values()), 'replay-duplicate-decision')
            decisions[record['id']] = record
        elif kind == 'availability':
            require(set(value) == {'date', 'available', 'supersedes'} and type(value['available']) is bool, 'replay-availability-schema')
            L._iso(value['date']); prior = [event for event in view.records('availability') if event['payload']['date'] == value['date']]
            require(value['supersedes'] == (prior[-1]['id'] if prior else None) and (not prior or prior[-1]['payload']['available'] != value['available']), 'replay-availability-chain')
        elif kind == 'score':
            require(value.get('modelId') in models, 'replay-score-model')
            packet = next((packet for packet in inputs.values() if S.digest(packet) == value['datasetSha256']), None)
            require(packet is not None, 'replay-missing-score-input')
            expected = H._score_payload(view, packet, value['modelId'], observed_at=record['createdAt'])
            prior = [event for event in view.records('score') if event['payload']['modelId'] == value['modelId']]
            expected['supersedes'] = prior[-1]['id'] if prior else None
            require(value == expected, 'replay-score-semantic-mismatch')
        elif kind == 'checkpoint':
            require(set(value) == {'modelId', 'scoreId', 'status', 'reason', 'activeReferenceId', 'supersedes'}, 'replay-checkpoint-schema')
            scores = [event for event in view.records('score') if event['payload']['modelId'] == value['modelId']]
            require(scores and scores[-1]['id'] == value['scoreId'], 'replay-checkpoint-not-latest')
            # Re-run the actual checkpoint producer against an in-memory prefix.
            class CheckView(PrefixView):
                def locked(self, **kwargs):
                    from contextlib import nullcontext
                    return nullcontext()
                def append(self, kind, payload):
                    return {'id': record['id'], 'kind': kind, 'payload': payload}
            expected = H.checkpoint(CheckView(records[:index]), value['modelId'])
            require(value == expected['payload'], 'replay-checkpoint-semantic-mismatch')
        elif kind == 'reference':
            require(set(value) == {'modelId', 'status', 'supersedes', 'checkpointId', 'reason'}, 'replay-reference-schema')
            prior = view.records('reference'); require(value['supersedes'] == (prior[-1]['id'] if prior else None), 'replay-reference-chain')
            if value['modelId'] is not None:
                checkpoints = [event for event in view.records('checkpoint') if event['id'] == value['checkpointId']]
                require(checkpoints and checkpoints[-1]['payload']['status'] == 'evidence-qualified-monitoring-candidate'
                        and checkpoints[-1]['payload']['modelId'] == value['modelId'], 'replay-unqualified-reference')
            else:
                require(value['status'] in ('cash-pending-economic-qualification', 'cash-after-evidence-retraction'), 'replay-cash-reference-schema')
        elif kind == 'snapshot-evidence':
            require(set(value) == {'snapshotSha256', 'eventHead'} and len(value['snapshotSha256']) == 64, 'replay-snapshot-schema')
    return {'version': 'autonomous-semantic-replay.v1', 'status': 'matched', 'eventsVerified': len(records),
            'modelCount': len(models), 'trialCount': len(trial_results), 'decisionCount': len(decisions), 'boundary': A.BOUNDARY}


@contextmanager
def external_lock(root, name):
    fd = os.open(root/name, os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600, 'snapshot-unsafe-external-lock')
        fcntl.flock(fd, fcntl.LOCK_SH); yield
    finally:
        os.close(fd)


def snapshot(store, destination, *, collector_root=None, legacy_root=None, profit_root=None):
    """Freeze the complete research graph under stable locks; never archive credentials.

    External roots are optional only if absent. Every existing supplied root is
    inventoried; unknown file names or source mismatch fail closed. The collector
    root is private normalized market versions/retrieval journals only.
    """
    destination = A.private_directory(destination, create=True)
    files = []; extensions = {}
    with ExitStack() as locks:
        locks.enter_context(store.locked(read_only=True))
        records = store.records(); _replay(records)
        for path in sorted(store.root.glob('event-*.json')):
            raw = L._read(path,64*1024*1024)
            files.append({'family': 'autonomous', 'name': path.name, 'sha256': hashlib.sha256(raw).hexdigest(), 'payload': L._json(raw)})
        if collector_root is not None:
            from . import feed_collection as F
            root = A.private_directory(collector_root)
            locks.enter_context(external_lock(root, '.collector.lock'))
            for path in sorted(root.glob('*.json')):
                raw = L._read(path,32*1024*1024); value = L._json(raw)
                require(value.get('source') == store.source == 'etoro', 'snapshot-collector-source-mismatch')
                if value.get('schemaVersion') == 'market-observations.v2':
                    require(set(value) == {'schemaVersion', 'symbol', 'source', 'observations', 'interpretation', 'retention', 'unresolvedMissingDates'}, 'snapshot-collector-schema')
                    F.check_retention(value['retention'], A.now()); F.validate_interpretation(value['interpretation'], value['symbol']); F.validate_observations(value['observations'])
                else:
                    require(value.get('schemaVersion') == 'market-retrieval.v2' and set(value) == {'schemaVersion', 'symbol', 'retrievedAt', 'version', 'added', 'revisedDates', 'missingPreviouslyObservedDates', 'unresolvedMissingDates', 'restoredDates', 'restorationEvidence', 'unchanged', 'originalObservationsPreserved', 'source', 'inputDigest'}, 'snapshot-collector-retrieval-schema')
                require(path.name.startswith(('SPY-', 'QQQ-', 'VAS-')), 'snapshot-unrecognized-collector-file')
                files.append({'family':'collector', 'name':path.name, 'sha256':hashlib.sha256(raw).hexdigest(), 'payload':value})
            extensions['collector'] = len([item for item in files if item['family']=='collector'])
        if legacy_root is not None:
            legacy = A.R.EvidenceStore(legacy_root, create=False); legacy.authorize(store.source,store.retention)
            locks.enter_context(legacy.locked(read_only=True))
            for kind in ('protocol','experiment','model','forecast','score','checkpoint','reference'):
                for record in legacy.records(kind):
                    path=legacy.root/f"{kind}-{record['id']}.json";raw=L._read(path,8*1024*1024)
                    files.append({'family':'legacy', 'name':path.name,'sha256':hashlib.sha256(raw).hexdigest(),'payload':record})
            extensions['legacy']=len([item for item in files if item['family']=='legacy'])
        if profit_root is not None:
            from . import profit_hypothesis as P
            require(type(profit_root) is dict and set(profit_root)=={'root','configPath'}, 'snapshot-profit-config-binding-required')
            config_raw=L._read(profit_root['configPath'],32*1024*1024); config=L._json(config_raw); config_raw=S.canonical(config)
            require(config.get('instrument',{}).get('source')==store.source, 'snapshot-profit-source-mismatch')
            if config.get('classification')=='observed-attested':
                A.R.retention_check(store.source, config.get('retention'))
            P.run(config,allow_synthetic_smoke=True)  # Exact input schema and safety gate before artifact reads.
            root=A.private_directory(profit_root['root'])
            locks.enter_context(external_lock(root,'.lock'))
            report=L._json(L._read(root/'report.json',32*1024*1024))
            require(report.get('sha256')==root.name, 'snapshot-profit-report-directory-binding')
            P.replay(report,config,evidence_root=root.parent,allow_synthetic_smoke=True)
            files.append({'family':'profit-config','name':'research-config.json','sha256':hashlib.sha256(config_raw).hexdigest(),'payload':config})
            for path in sorted(root.glob('*.json')):
                value=L._json(L._read(path,32*1024*1024))
                require(path.stem in ('frozen-hypothesis','report') or path.stem.startswith('trial-') and path.stem.endswith(('-ledger','-equity')), 'snapshot-unrecognized-profit-artifact')
                raw=L._read(path,32*1024*1024)
                files.append({'family':'profit','name':path.name,'sha256':hashlib.sha256(raw).hexdigest(),'payload':value})
            extensions['profit']={'reportSha256':report['sha256'],'artifactCount':len([item for item in files if item['family']=='profit'])}
        _verify_external_graph(files,store.source,store.retention,records)
        payload={'version':'autonomous-snapshot.v1','source':store.source,'retention':store.retention,
                 'eventHead':records[-1]['id'] if records else None,'files':files,'extensions':extensions,'boundary':A.BOUNDARY}
        identity=S.digest(payload); document={**payload,'sha256':identity}
        A.write_once(destination/(identity+'.json'),S.canonical(document))
    return {'status':'snapshotted','sha256':identity,'fileCount':len(files),'eventHead':payload['eventHead'],'extensions':extensions}


def restore(snapshot_path, destination, *, source, retention):
    A.R.retention_check(source,retention)
    document=L._json(L._read(snapshot_path,128*1024*1024))
    require(set(document)=={'version','source','retention','eventHead','files','extensions','boundary','sha256'}
            and document['version']=='autonomous-snapshot.v1' and document['source']==source
            and document['sha256']==S.digest({key:value for key,value in document.items() if key!='sha256'}), 'restore-snapshot-integrity')
    A.R.retention_check(source,document['retention'])
    root=A.private_directory(destination,create=True)
    # Retry is allowed only for an exact interrupted restore of this snapshot.
    marker=root/'.restore.json'; binding={'snapshotSha256':document['sha256']}
    require(not list(root.iterdir()) or marker.exists() and L._json(L._read(marker,4096))==binding, 'restore-isolated-empty-destination-required')
    A.write_once(marker,S.canonical(binding))
    store=A.Store(root/'autonomous',source=source,retention=retention,create=True)
    identities=set()
    for item in document['files']:
        require(type(item) is dict and set(item)=={'family','name','sha256','payload'} and item['family'] in ('autonomous','collector','legacy','profit','profit-config')
                and type(item['name']) is str and Path(item['name']).name==item['name'] and item['name'].endswith('.json')
                and not item['name'].startswith('.') and (item['family'],item['name']) not in identities, 'restore-unsafe-file-entry')
        raw=S.canonical(item['payload'])
        # Legacy existing stores publish a trailing newline; retain exact hash.
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:
            raw+=b'\n'
        require(hashlib.sha256(raw).hexdigest()==item['sha256'], 'restore-file-checksum')
        identities.add((item['family'],item['name']))
        if item['family']=='autonomous':
            target=store.root
        elif item['family']=='profit':
            target=A.private_directory(root/'profit'/document['extensions']['profit']['reportSha256'],create=True)
        else:
            target=A.private_directory(root/item['family'],create=True)
        A.write_once(target/item['name'],raw)
    with store.locked(read_only=True):
        records=store.records();require((records[-1]['id'] if records else None)==document['eventHead'],'restore-head-mismatch')
    if any(item['family']=='collector' for item in document['files']):
        A.write_once(root/'collector'/'.collector.lock',b'')
    if any(item['family']=='legacy' for item in document['files']):
        A.write_once(root/'legacy'/'.source.json',S.canonical({'version':'research-store-source.v1','source':source}))
        A.write_once(root/'legacy'/'.lock',b'')
    if 'profit' in document['extensions']:
        A.write_once(root/'profit'/document['extensions']['profit']['reportSha256']/'.lock',b'')
    _verify_external_graph(document['files'],source,retention,records,restored_root=root)
    replay=semantic_replay(store)
    return {'status':'restored-and-replayed','sha256':document['sha256'],'fileCount':len(identities),'semanticReplay':replay}


def _verify_external_graph(files,source,retention,autonomous_records,restored_root=None):
    """Validate foreign producer contracts and replay every reachable retained result."""
    A.R.retention_check(source,retention)
    collector={item['name']:item for item in files if item['family']=='collector'}
    candidate_histories=[]
    for record in autonomous_records:
        if record['kind']=='input':
            packet=record['payload']['packet']
            if packet['observations']:
                candidate_histories.append(A.bars(packet))
    if collector:
        from . import feed_collection as F
        from .market_history import Bar
        for name,item in collector.items():
            value=item['payload']
            require(value.get('source')==source=='etoro','snapshot-collector-source-mismatch')
            if value.get('schemaVersion')=='market-observations.v2':
                require(name==value['symbol']+'-'+item['sha256']+'.json','snapshot-collector-version-identity')
                F.check_retention(value['retention'],A.now());F.validate_interpretation(value['interpretation'],value['symbol'])
                observations=F.validate_observations(value['observations'])
                missing=value['unresolvedMissingDates']
                require(type(missing) is list and missing==sorted(set(missing)) and set(missing)<={row['date'] for row in observations},'snapshot-invalid-collector-availability')
                candidate_histories.append([Bar(value['symbol'],row['date'],row['open'],row['high'],row['low'],row['close'],row['volume'],source)
                                            for row in observations if row['date'] not in missing])
            else:
                require(value.get('schemaVersion')=='market-retrieval.v2' and L._hash(value.get('version')), 'snapshot-invalid-collector-retrieval')
                require(value['symbol']+'-'+value['version']+'.json' in collector,'snapshot-orphaned-collector-retrieval')
                target=collector[value['symbol']+'-'+value['version']+'.json']['payload']
                require(value['unresolvedMissingDates']==target['unresolvedMissingDates'], 'snapshot-collector-retrieval-availability-mismatch')
                require(type(value['restorationEvidence']) is list and len(value['restorationEvidence'])==len(value['restoredDates']), 'snapshot-invalid-restoration-evidence')
                for date,evidence in zip(value['restoredDates'],value['restorationEvidence']):
                    require(set(evidence)=={'date','observationTimestamp','retrievedAt','source'} and evidence['date']==date
                            and evidence['source']==source and evidence['retrievedAt']==value['retrievedAt']
                            and any(row['date']==date and row['timestamp']==evidence['observationTimestamp'] for row in target['observations']), 'snapshot-restoration-evidence-mismatch')
    legacy=[item['payload'] for item in files if item['family']=='legacy']
    if legacy:
        from . import forward_evaluation as F
        by_kind={kind:[record for record in legacy if record['kind']==kind] for kind in ('protocol','experiment','model','forecast','score','checkpoint','reference')}
        for records in by_kind.values():
            for record in records:
                require(record['id']==L._digest(record['payload']),'snapshot-legacy-record-identity')
                A.R.validate_record_payload(record['kind'],record['payload'])
        class LegacyView:
            def records(self,kind):return by_kind[kind]
        view=LegacyView();F.validate_forward_journal(view)
        protocols={record['id']:record['payload'] for record in by_kind['protocol']}
        for experiment in by_kind['experiment']:
            require(experiment['payload']['protocolId'] in protocols,'snapshot-legacy-protocol-missing')
            protocol=protocols[experiment['payload']['protocolId']]
            require(protocol['manifest']['source']==source and protocol['retention']==retention,'snapshot-legacy-source-policy-mismatch')
            matching=next((history for history in candidate_histories if L._digest([bar.to_dict() for bar in history])==protocol['rowsSha256']),None)
            require(matching is not None,'snapshot-legacy-exact-input-history-required')
            A.R._semantic_replay_locked(view,matching,protocol['manifest'],experiment,retention)
    profit=[item for item in files if item['family']=='profit']
    if profit:
        from . import profit_hypothesis as P
        configs=[item['payload'] for item in files if item['family']=='profit-config']
        require(len(configs)==1 and configs[0]['instrument']['source']==source,'snapshot-profit-source-config-required')
        config=configs[0]
        if config['classification']=='observed-attested':
            A.R.retention_check(source,config['retention'])
        artifacts={item['name']:item['payload'] for item in profit};report=artifacts.get('report.json')
        require(report is not None and report['sha256']==P._digest({key:value for key,value in report.items() if key!='sha256'}),'snapshot-profit-report-identity')
        for artifact in report['artifactManifest'][:-1]:
            require(artifact['name']+'.json' in artifacts and P._digest(artifacts[artifact['name']+'.json'])==artifact['sha256'],'snapshot-profit-artifact-graph')
        replayed=P.run(config,allow_synthetic_smoke=True,frozen_rules=report['frozen']['hypotheses'],retained_attempt_count=report['frozen']['selectionAccounting']['retainedAttemptCount'])
        require(replayed['frozen']==report['frozen'] and replayed['trials']==report['trials'],'snapshot-profit-semantic-mismatch')
        if restored_root is not None:
            P.replay(report,config,evidence_root=restored_root/'profit',allow_synthetic_smoke=True)
