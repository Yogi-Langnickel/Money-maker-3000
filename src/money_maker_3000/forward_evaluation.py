"""Append-only forward predictions, correction-aware scores and fixed checkpoints."""
from __future__ import annotations

import math
import random
from datetime import timedelta

from . import learning as L
from .research_cycle import EvidenceStore, HORIZON, metrics, now, retention_check, timestamp, validate_history, validate_interpretation, validate_model_schema


def _eligible_model(model, manifest):
    validate_model_schema(model)
    retention_check(manifest['source'], model['retention'])
    L._require(all(model[k] == manifest[k] for k in ('source','symbol','priceBasis','classification')),
               'forward-source-incompatible')
    L._require(model['strategy'] in L.STATES and model['parameters'] in L.candidate_grid(model['strategy'])
               and model['horizonObservations'] == HORIZON, 'invalid-forward-model')
    L._validate_fit(model['fit'],model['strategy'])
    validate_interpretation(model['interpretation'])
    L._require(model['interpretation']['trainingPermitted'] and model['interpretation']['instrumentVerified'], 'unapproved-forward-meaning')


def record_predictions(store: EvidenceStore, bars: list, manifest: dict, model_ids: list[str], *,
                       retention: dict, retrieved_at: str, completed_at: str,
                       replay: bool = False) -> dict:
    """Actual clock owns creation time; caller timestamps can only reduce eligibility.

    Only the latest completed endpoint is forecast. Historical loops use replay=True;
    importing a forecast never creates genuine forward evidence.
    """
    retention_check(manifest['source'],retention)
    validate_history(bars,manifest)
    creation = now()
    created = timestamp(creation)
    retrieved, completed = timestamp(retrieved_at), timestamp(completed_at)
    L._require(completed <= retrieved <= created, 'invalid-observation-availability')
    L._require(0 <= (completed.date()-timestamp(bars[-1].date+'T00:00:00Z').date()).days <= 1, 'completion-before-observation')
    L._require(type(replay) is bool and 1 <= len(model_ids) <= 3 and len(set(model_ids)) == len(model_ids), 'invalid-forward-model-set')
    store.authorize(manifest['source'],retention,create=True)
    with store.locked():
        available = {r['id']:r['payload'] for r in store.records('model')}
        validate_forward_journal(store)
        forecasts = store.records('forecast')
        registered=registered_models(store)
        L._require(all(identity in registered for identity in model_ids),'model-not-registered-by-completed-experiment')
        records, reused = [], 0
        for identity in model_ids:
            L._require(identity in available, 'unknown-frozen-model')
            model = available[identity]
            _eligible_model(model,manifest)
            L._require(bars[-1].date > model['selectionEnd'] and len(bars) >= L._warmup(model['parameters']),
                       'prediction-before-selection-or-warmup')
            previous = [r for r in forecasts if r['payload']['modelId'] == identity
                        and r['payload']['asOfDate'] == bars[-1].date]
            if previous:
                # Revised features never silently replace the original prediction.
                original = previous[0]
                feature_hash = L._digest([b.to_dict() for b in bars])
                if feature_hash != original['payload']['featureRowsSha256']:
                    store.put('reference', {'type':'forecast-feature-revision', 'forecastId':original['id'],
                        'originalFeatureRowsSha256':original['payload']['featureRowsSha256'],
                        'revisedFeatureRowsSha256':feature_hash, 'datasetSha256':manifest['sha256'],
                        'source':manifest['source'], 'retention':retention, 'probabilityUnchanged':True})
                records.append(original['id']); reused += 1; continue
            state = L._state(bars,len(bars)-1,model['strategy'],model['parameters'])
            support = model['fit']['states'][state]
            # Completeness is attested by collector metadata; close freshness is bounded.
            genuine = (not replay and manifest['classification'] == 'observed-attested'
                       and created-completed <= timedelta(days=4)
                       and created.date().isoformat() >= bars[-1].date
                       and timestamp(model['frozenAt']) <= created)
            payload = {'version': 'forward-prediction.v1', 'modelId': identity, 'createdAt': creation,
                       'retrievedAt': retrieved_at, 'completedAt': completed_at, 'source': manifest['source'],
                       'symbol': manifest['symbol'], 'asOfDate': bars[-1].date, 'horizonObservations': HORIZON,
                       'datasetSha256': manifest['sha256'], 'featureRowsSha256': L._digest([b.to_dict() for b in bars]),
                       'originClose': bars[-1].close, 'state': state, 'probabilityUp': support['probabilityUp'],
                       'priorProbabilityUp': model['fit']['priorProbabilityUp'], 'usesPrior': support['usesPrior'],
                       'evidenceType': 'genuine-forward' if genuine else 'historical-replay',
                       'retention': retention, 'boundary': dict(L.BOUNDARY)}
            records.append(store.put('forecast',payload)['id'])
        return {'created': len(records)-reused, 'reused': reused, 'forecastIds': records}


def score_matured(store: EvidenceStore, bars: list, manifest: dict, *, retention: dict,
                  retrieved_at: str, unavailable_dates: list[str] | None = None) -> dict:
    retention_check(manifest['source'],retention)
    validate_history(bars,manifest)
    creation = now()
    L._require(timestamp(retrieved_at) <= timestamp(creation), 'future-retrieval-time')
    L._require(all(bar.date < timestamp(retrieved_at).date().isoformat() for bar in bars), 'unfinished-or-future-outcome-observation')
    unavailable_dates=unavailable_dates or []
    L._require(type(unavailable_dates) is list and all(L._iso(d) for d in unavailable_dates) and len(set(unavailable_dates))==len(unavailable_dates), 'invalid-unavailable-observation-dates')
    indexed = {bar.date:i for i,bar in enumerate(bars)}
    store.authorize(manifest['source'],retention,create=True)
    with store.locked():
        validate_forward_journal(store)
        forecasts = store.records('forecast')
        scores = store.records('score')
        models={r['id']:r['payload'] for r in store.records('model')}
        matching=[r for r in forecasts if (r['payload']['source'],r['payload']['symbol'])==(manifest['source'],manifest['symbol'])]
        for r in matching:
            _eligible_model(models[r['payload']['modelId']],manifest)
        availability=availability_events(store)
        created, revised, pending = 0, 0, 0
        for record in forecasts:
            f = record['payload']
            if (f['source'],f['symbol']) != (manifest['source'],manifest['symbol']):
                continue
            retention_check(f['source'],retention)
            i = indexed.get(f['asOfDate'])
            previous_scores=[r for r in scores if r['payload']['forecastId']==record['id']]
            missing=i is None or i+HORIZON>=len(bars) or any(bars[i].date<=d<=bars[min(i+HORIZON,len(bars)-1)].date for d in unavailable_dates)
            previous_event=availability.get(record['id'])
            state='unavailable' if missing else 'available'
            restoration=None
            if missing and (previous_scores or previous_event) and (previous_event is None or previous_event['payload']['state']!='unavailable'):
                event=store.put('reference',{'type':'outcome-availability','forecastId':record['id'],'state':'unavailable',
                    'recordedAt':creation,'retrievedAt':retrieved_at,'datasetSha256':manifest['sha256'],
                    'source':manifest['source'],'retention':retention})
                availability[record['id']]=event
            elif not missing and previous_event is not None and previous_event['payload']['state']=='unavailable':
                # Restoration is published only after the current score is durable,
                # or after equality with the latest durable score has been verified.
                restoration={'type':'outcome-availability','forecastId':record['id'],'state':'available',
                    'recordedAt':creation,'retrievedAt':retrieved_at,'datasetSha256':manifest['sha256'],
                    'source':manifest['source'],'retention':retention}
            if missing:
                pending+=1;continue
            window = bars[i:i+HORIZON+1]
            outcome_id = L._digest([b.to_dict() for b in window])
            previous = [r for r in scores if r['payload']['forecastId'] == record['id']]
            previous.sort(key=lambda r:r['payload']['revision'])
            L._require([r['payload']['revision'] for r in previous] == list(range(len(previous))), 'invalid-score-revision-chain')
            for j,r in enumerate(previous):
                L._require(r['payload']['supersedes'] == (previous[j-1]['id'] if j else None), 'invalid-score-supersedes')
            feature_revised = L._digest([b.to_dict() for b in bars[:i+1]]) != f['featureRowsSha256']
            if previous and previous[-1]['payload']['outcomeRowsSha256'] == outcome_id and previous[-1]['payload'].get('featureRowsRevised') == feature_revised:
                if restoration is not None:
                    availability[record['id']]=store.put('reference',restoration)
                continue
            outcome = int(window[-1].close > window[0].close)
            eligible = f['evidenceType'] == 'genuine-forward' and timestamp(f['createdAt']).date().isoformat() < window[-1].date
            payload = {'version': 'forward-score.v1', 'forecastId': record['id'], 'modelId': f['modelId'],
                       'createdAt': creation, 'retrievedAt': retrieved_at, 'asOfDate': f['asOfDate'],
                       'outcomeDate': window[-1].date, 'outcomeRowsSha256': outcome_id, 'outcomeWindow':[b.to_dict() for b in window],
                       'datasetSha256': manifest['sha256'], 'outcome': outcome,
                       'brier': (f['probabilityUp']-outcome)**2,
                       'baselineBrier': (f['priorProbabilityUp']-outcome)**2,
                       'revision': len(previous), 'supersedes': previous[-1]['id'] if previous else None,
                       'originPriceRevised': window[0].close != f['originClose'], 'featureRowsRevised':feature_revised,
                       'evidenceType': 'genuine-forward' if eligible else 'historical-replay',
                       'retention': retention, 'source': manifest['source'], 'symbol': manifest['symbol']}
            saved = store.put('score',payload)
            scores.append(saved)
            if restoration is not None:
                availability[record['id']]=store.put('reference',restoration)
            created += not previous; revised += bool(previous)
        return {'matured': created, 'revised': revised, 'pending': pending}


def paired_block_interval(differences: list[float], *, block_length: int = 5, replicates: int = 2000) -> dict:
    """Deterministic circular moving-block bootstrap of paired Brier improvements."""
    L._require(len(differences) >= 60 and block_length >= HORIZON and replicates == 2000, 'insufficient-block-evidence')
    L._require(all(L._number(x,-1,1) for x in differences), 'invalid-paired-evidence')
    rng = random.Random(0)
    n = len(differences)
    samples = []
    for _ in range(replicates):
        draw = []
        while len(draw) < n:
            start = rng.randrange(n)
            draw.extend(differences[(start+j)%n] for j in range(block_length))
        samples.append(math.fsum(draw[:n])/n)
    samples.sort()
    return {'method': 'paired-circular-moving-block-bootstrap.v1', 'blockLength': block_length,
            'replicates': replicates, 'seed': 0, 'meanImprovement': math.fsum(differences)/n,
            'lower': samples[int(.025*replicates)], 'upper': samples[int(.975*replicates)-1]}


def _repair_reference(store, checkpoint, policy):
    """Idempotently finish checkpoint publication after a process interruption."""
    p=checkpoint['payload']
    events=[r for r in store.records('reference') if r['payload'].get('type') in
            ('incumbent-replacement','replacement-retracted-after-correction') and r['payload'].get('protocolId')==p['protocolId']]
    if any(r['payload']['checkpointId']==checkpoint['id'] for r in events):
        return
    checkpoints={r['id']:r['payload'] for r in store.records('checkpoint')}
    events.sort(key=lambda r:(checkpoints[r['payload']['checkpointId']]['createdAt'],checkpoints[r['payload']['checkpointId']]['revision']))
    active=events[-1]['payload'] if events else None
    replacements=[r for r in events if r['payload']['type']=='incumbent-replacement']
    if p['status']=='supported' and len(replacements)<policy['maxReplacements']:
        store.put('reference',{'type':'incumbent-replacement','protocolId':p['protocolId'],
            'previousModelId':p['incumbentId'],'modelId':p['challengerId'],'checkpointId':checkpoint['id'],
            'source':p['source'],'retention':p['retention']})
    elif p['status']!='supported' and active and active['type']=='incumbent-replacement':
        owner=checkpoints[active['checkpointId']]
        if (owner['incumbentId'],owner['challengerId'])==(p['incumbentId'],p['challengerId']):
            store.put('reference',{'type':'replacement-retracted-after-correction','protocolId':p['protocolId'],
                'modelId':p['incumbentId'],'checkpointId':checkpoint['id'],'source':p['source'],'retention':p['retention']})


def evaluate_checkpoint(store: EvidenceStore, protocol_id: str, incumbent_id: str, challenger_id: str,
                        *, source: str, retention: dict) -> dict:
    retention_check(source,retention)
    store.authorize(source,retention)
    with store.locked():
        protocols={r['id']:r['payload'] for r in store.records('protocol')}
        L._require(protocol_id in protocols,'unknown-checkpoint-protocol')
        protocol=protocols[protocol_id]
        L._require(protocol['manifest']['source']==source,'checkpoint-source-mismatch')
        policy=protocol['checkpointPolicy']
        registered=registered_models(store)
        L._require(incumbent_id!=challenger_id and all(i in registered and registered[i]['protocolId']==protocol_id
                   for i in (incumbent_id,challenger_id)),'checkpoint-model-mismatch')
        prior=[r for r in store.records('checkpoint') if r['payload']['protocolId']==protocol_id]
        validate_forward_journal(store)
        validate_checkpoint_records(store,registered)
        for saved in sorted(prior,key=lambda r:(r['payload']['createdAt'],r['payload']['revision'])):
            _repair_reference(store,saved,policy)
        pair=sorted([r for r in prior if r['payload']['incumbentId']==incumbent_id and r['payload']['challengerId']==challenger_id],key=lambda r:r['payload']['revision'])
        L._require(pair or len({(r['payload']['incumbentId'],r['payload']['challengerId']) for r in prior})<policy['maxComparisons'],'checkpoint-comparison-budget-exhausted')
        validate_forward_journal(store)
        latest={}
        for r in store.records('score'):
            p=r['payload']
            if p['modelId'] in (incumbent_id,challenger_id):
                key=(p['modelId'],p['asOfDate'])
                if key not in latest or latest[key]['payload']['revision']<p['revision']:
                    latest[key]=r
        # Latest revisions are chosen BEFORE eligibility, so an ineligible correction
        # can never fall back to an older favourable score.
        availability=availability_events(store)
        def eligible(d):
            if any((m,d) not in latest for m in (incumbent_id,challenger_id)):
                return False
            scores=[latest[(m,d)]['payload'] for m in (incumbent_id,challenger_id)]
            return (all(x['evidenceType']=='genuine-forward' and not x['featureRowsRevised']
                        and availability.get(x['forecastId'],{}).get('payload',{}).get('state')!='unavailable' for x in scores)
                    and scores[0]['outcomeRowsSha256']==scores[1]['outcomeRowsSha256'])
        common=sorted({d for m,d in latest if m==incumbent_id}&{d for m,d in latest if m==challenger_id})
        usable=[d for d in common if eligible(d)]
        if not pair and len(usable)<policy['checkpointCompleted']:
            return {'status':'inconclusive','reason':'awaiting-fixed-checkpoint','completedPairs':len(usable),'requiredPairs':policy['checkpointCompleted']}
        dates=pair[0]['payload']['checkpointDates'] if pair else usable[:policy['checkpointCompleted']]
        evidence=[latest[(m,d)]['id'] for d in dates for m in (incumbent_id,challenger_id) if (m,d) in latest]
        event_ids=sorted({availability[latest[(m,d)]['payload']['forecastId']]['id'] for d in dates for m in (incumbent_id,challenger_id)
                          if (m,d) in latest and latest[(m,d)]['payload']['forecastId'] in availability})
        complete=all(eligible(d) for d in dates)
        if pair and pair[-1]['payload']['scoreIds']==evidence and pair[-1]['payload']['availabilityEventIds']==event_ids:
            _repair_reference(store,pair[-1],policy)
            return pair[-1]
        interval=None
        verdict='inconclusive'
        if complete:
            differences=[latest[(incumbent_id,d)]['payload']['brier']-latest[(challenger_id,d)]['payload']['brier'] for d in dates]
            interval=paired_block_interval(differences,block_length=policy['blockLength'],replicates=policy['bootstrapReplicates'])
            threshold=policy['minimumUsefulImprovement']
            verdict='supported' if interval['lower']>threshold else 'rejected' if interval['upper']<threshold else 'inconclusive'
        payload={'version':'forward-checkpoint.v1','protocolId':protocol_id,'incumbentId':incumbent_id,'challengerId':challenger_id,
            'createdAt':now(),'completedPairs':sum(eligible(d) for d in dates),'fixedDateCount':len(dates),'currentEligiblePairs':sum(eligible(d) for d in dates),'scoreIds':evidence,'availabilityEventIds':event_ids,'pairingComplete':complete,
            'status':verdict,'interval':interval,'minimumUsefulImprovement':policy['minimumUsefulImprovement'],
            'retention':retention,'source':source,'replacementEnabled':True,'revision':len(pair),'supersedes':pair[-1]['id'] if pair else None,
            'checkpointDates':dates,'reason':'fixed-research-comparison-no-trading' if complete else 'original-checkpoint-evidence-unavailable-or-revised'}
        saved=store.put('checkpoint',payload)
        _repair_reference(store,saved,policy)
        return saved


def status(store: EvidenceStore, *, source: str, retention: dict) -> dict:
    retention_check(source,retention)
    store.authorize(source,retention)
    with store.locked(read_only=True):
        store.records('protocol')
        store.records('experiment')
        validate_forward_journal(store)
        forecasts = [r for r in store.records('forecast') if r['payload']['source'] == source]
        ids = {r['id'] for r in forecasts}
        scores = [r for r in store.records('score') if r['payload']['forecastId'] in ids]
        scored = {r['payload']['forecastId'] for r in scores}
        unavailable={key for key,value in availability_events(store).items() if value['payload']['state']=='unavailable'}
        return {'version':'continuous-research-status.v1','forecasts':len(forecasts), 'pending':len(ids-scored)+len(scored & unavailable),
                'scored':len(scored),'scoredHistoricalTotal':len(scored),'validScored':len(scored-unavailable),'scoreRevisions':len(scores)-len(scored), 'currentlyUnavailable':len(scored & unavailable),
                'modelDiagnostics':journal_diagnostics(forecasts,scores,unavailable),
                'genuineForward':sum(r['payload']['evidenceType']=='genuine-forward' for r in forecasts),
                'boundary':dict(L.BOUNDARY)}


def journal_diagnostics(forecasts, scores, unavailable):
    by_forecast={r['id']:r['payload'] for r in forecasts}
    latest={}
    for r in scores:
        p=r['payload']
        if p['forecastId'] not in latest or p['revision']>latest[p['forecastId']]['revision']:
            latest[p['forecastId']]=p
    groups={}
    for identity,p in latest.items():
        if identity in unavailable or p['featureRowsRevised']:
            continue
        groups.setdefault((p['modelId'],p['evidenceType']),[]).append((by_forecast[identity],p))
    result=[]
    for (model,evidence_type),rows in sorted(groups.items()):
        rows.sort(key=lambda row:row[1]['asOfDate'])
        probabilities=[f['probabilityUp'] for f,p in rows]
        labels=[p['outcome'] for f,p in rows]
        report=metrics(probabilities,labels,[f['usesPrior'] for f,p in rows])
        periods=[]
        for i in range(3):
            part=rows[len(rows)*i//3:len(rows)*(i+1)//3]
            if part:
                periods.append({'period':i,'count':len(part),'firstDate':part[0][1]['asOfDate'],'lastDate':part[-1][1]['asOfDate'],
                    'brier':math.fsum(p['brier'] for f,p in part)/len(part),
                    'baselineBrier':math.fsum(p['baselineBrier'] for f,p in part)/len(part)})
        result.append({'modelId':model,'evidenceType':evidence_type,**report,
                       'baselineBrier':L._score([f['priorProbabilityUp'] for f,p in rows],labels),'periods':periods})
    return result


def validate_checkpoint_records(store, registered):
    pairs={}
    score_ids={r['id'] for r in store.records('score')}
    event_ids={r['id'] for r in store.records('reference') if r['payload'].get('type')=='outcome-availability'}
    for r in store.records('checkpoint'):
        p=r['payload']
        L._keys(p,{'version','protocolId','incumbentId','challengerId','createdAt','completedPairs','fixedDateCount',
            'currentEligiblePairs','scoreIds','availabilityEventIds','pairingComplete','status','interval','minimumUsefulImprovement',
            'retention','source','replacementEnabled','revision','supersedes','checkpointDates','reason'})
        L._require(p['version']=='forward-checkpoint.v1' and p['status'] in ('supported','inconclusive','rejected')
                   and p['minimumUsefulImprovement']==.005 and p['replacementEnabled'] is True,'invalid-checkpoint-contract')
        L._require(all(i in registered and registered[i]['protocolId']==p['protocolId'] and registered[i]['source']==p['source']
                       for i in (p['incumbentId'],p['challengerId'])) and p['incumbentId']!=p['challengerId'],'checkpoint-registration-mismatch')
        L._require(type(p['checkpointDates']) is list and p['checkpointDates']==sorted(set(p['checkpointDates']))
                   and len(p['checkpointDates'])==p['fixedDateCount']==60 and all(L._iso(d) for d in p['checkpointDates']), 'invalid-fixed-checkpoint-dates')
        L._require(L._integer(p['currentEligiblePairs'],0,60) and p['completedPairs']==p['currentEligiblePairs']
                   and type(p['pairingComplete']) is bool and p['pairingComplete']==(p['currentEligiblePairs']==60), 'checkpoint-pair-count-mismatch')
        L._require(type(p['scoreIds']) is list and len(set(p['scoreIds']))==len(p['scoreIds']) and set(p['scoreIds'])<=score_ids
                   and type(p['availabilityEventIds']) is list and set(p['availabilityEventIds'])<=event_ids,'checkpoint-evidence-unavailable')
        if not p['pairingComplete']:
            L._require(p['status']=='inconclusive' and p['interval'] is None,'incomplete-checkpoint-conclusion')
        else:
            interval=p['interval']
            L._keys(interval,{'method','blockLength','replicates','seed','meanImprovement','lower','upper'})
            L._require(interval['method']=='paired-circular-moving-block-bootstrap.v1' and interval['blockLength']==5
                       and interval['replicates']==2000 and interval['seed']==0 and all(L._number(interval[k],-1,1) for k in ('meanImprovement','lower','upper'))
                       and interval['lower']<=interval['upper'],'invalid-checkpoint-interval')
            verdict='supported' if interval['lower']>.005 else 'rejected' if interval['upper']<.005 else 'inconclusive'
            L._require(p['status']==verdict,'checkpoint-verdict-mismatch')
        timestamp(p['createdAt'])
        pairs.setdefault((p['protocolId'],p['incumbentId'],p['challengerId']),[]).append(r)
    for chain in pairs.values():
        chain.sort(key=lambda r:r['payload']['revision'])
        L._require([r['payload']['revision'] for r in chain]==list(range(len(chain))),'checkpoint-revision-chain-invalid')
        for i,r in enumerate(chain):
            L._require(r['payload']['supersedes']==(chain[i-1]['id'] if i else None)
                       and r['payload']['checkpointDates']==chain[0]['payload']['checkpointDates'],'checkpoint-revision-provenance-invalid')


def registered_models(store):
    protocols={r['id']:r['payload'] for r in store.records('protocol')}
    models={r['id']:r['payload'] for r in store.records('model')}
    registered={}
    seen=set()
    for experiment in store.records('experiment'):
        e=experiment['payload']
        L._require(e['protocolId'] in protocols and e['protocolId'] not in seen,'invalid-or-duplicate-experiment-protocol')
        seen.add(e['protocolId'])
        protocol=protocols[e['protocolId']]
        for reference in e['models']:
            L._require(reference['id'] in models,'experiment-model-unavailable')
            m=models[reference['id']]
            L._require(m['protocolId']==e['protocolId'] and m['strategy']==protocol['strategy']
                       and all(m[k]==protocol['manifest'][k] for k in ('source','symbol','priceBasis','classification'))
                       and m['frozenAt']==protocol['createdAt'],'experiment-model-protocol-mismatch')
            registered[reference['id']]=m
    return registered


def availability_events(store):
    latest={}
    for r in store.records('reference'):
        p=r['payload']
        if p.get('type')!='outcome-availability':
            continue
        L._keys(p,{'type','forecastId','state','recordedAt','retrievedAt','datasetSha256','source','retention'})
        L._require(p['state'] in ('available','unavailable') and L._hash(p['forecastId']) and L._hash(p['datasetSha256']),'invalid-outcome-availability')
        L._require(timestamp(p['retrievedAt'])<=timestamp(p['recordedAt']),'availability-clock-reversed')
        if p['forecastId'] not in latest or timestamp(latest[p['forecastId']]['payload']['recordedAt'])<timestamp(p['recordedAt']):
            latest[p['forecastId']]=r
    return latest


def validate_forward_journal(store):
    models=registered_models(store)
    forecasts={r['id']:r['payload'] for r in store.records('forecast')}
    for f in forecasts.values():
        L._keys(f, {'version','modelId','createdAt','retrievedAt','completedAt','source','symbol','asOfDate',
            'horizonObservations','datasetSha256','featureRowsSha256','originClose','state','probabilityUp',
            'priorProbabilityUp','usesPrior','evidenceType','retention','boundary'})
        L._require(f['modelId'] in models and f['version']=='forward-prediction.v1' and f['horizonObservations']==HORIZON, 'orphan-or-invalid-forecast')
        m=models[f['modelId']]
        L._require(f['source']==m['source'] and f['symbol']==m['symbol'] and f['asOfDate']>m['selectionEnd'], 'forecast-model-mismatch')
        fit=m['fit']['states'].get(f['state'])
        L._require(fit is not None and f['probabilityUp']==fit['probabilityUp'] and f['usesPrior']==fit['usesPrior']
                   and f['priorProbabilityUp']==m['fit']['priorProbabilityUp'], 'forecast-probability-mismatch')
        L._require(L._number(f['originClose'],1e-8,1e12) and L._hash(f['datasetSha256']) and L._hash(f['featureRowsSha256']), 'invalid-forecast-provenance')
        L._require(timestamp(f['completedAt'])<=timestamp(f['retrievedAt'])<=timestamp(f['createdAt']), 'forecast-availability-order')
        if f['evidenceType']=='genuine-forward':
            L._require(m['classification']=='observed-attested' and timestamp(m['frozenAt'])<=timestamp(f['createdAt'])
                       and timestamp(f['createdAt'])-timestamp(f['completedAt'])<=timedelta(days=4), 'invalid-genuine-forward-claim')
        else:
            L._require(f['evidenceType']=='historical-replay','invalid-evidence-type')
        L._require(f['boundary']==L.BOUNDARY,'forecast-boundary-changed')
    chains={}
    for record in store.records('score'):
        s=record['payload']
        L._keys(s,{'version','forecastId','modelId','createdAt','retrievedAt','asOfDate','outcomeDate','outcomeRowsSha256',
            'outcomeWindow','datasetSha256','outcome','brier','baselineBrier','revision','supersedes','originPriceRevised',
            'featureRowsRevised','evidenceType','retention','source','symbol'})
        L._require(s['forecastId'] in forecasts and s['version']=='forward-score.v1','orphan-or-invalid-score')
        f=forecasts[s['forecastId']]
        L._require(all(s[k]==f[k] for k in ('modelId','asOfDate','source','symbol')),'score-forecast-mismatch')
        window=s['outcomeWindow']
        L._require(type(window) is list and len(window)==HORIZON+1 and L._digest(window)==s['outcomeRowsSha256'],'score-outcome-window-mismatch')
        previous=''
        for row in window:
            L._keys(row,{'symbol','date','open','high','low','close','volume','source'})
            L._require(row['source']==s['source'] and row['symbol']==s['symbol'] and L._iso(row['date'])>previous
                       and L._number(row['close'],1e-8,1e12),'invalid-score-observation')
            previous=row['date']
        L._require(window[0]['date']==s['asOfDate'] and window[-1]['date']==s['outcomeDate']
                   and s['outcomeDate']<timestamp(s['retrievedAt']).date().isoformat(),'score-outcome-date-mismatch')
        outcome=int(window[-1]['close']>window[0]['close'])
        L._require(type(s['outcome']) is int and s['outcome']==outcome
                   and s['brier']==(f['probabilityUp']-outcome)**2
                   and s['baselineBrier']==(f['priorProbabilityUp']-outcome)**2,'score-metric-mismatch')
        L._require(type(s['featureRowsRevised']) is bool and s['originPriceRevised']==(window[0]['close']!=f['originClose']), 'score-revision-flags-mismatch')
        eligible=f['evidenceType']=='genuine-forward' and timestamp(f['createdAt']).date().isoformat()<s['outcomeDate']
        L._require(s['evidenceType']==('genuine-forward' if eligible else 'historical-replay'),'invalid-score-forward-claim')
        L._require(timestamp(f['createdAt'])<=timestamp(s['createdAt']) and timestamp(s['retrievedAt'])<=timestamp(s['createdAt']),'score-clock-reversed')
        chains.setdefault(s['forecastId'],[]).append(record)
    for chain in chains.values():
        chain.sort(key=lambda r:r['payload']['revision'])
        L._require([r['payload']['revision'] for r in chain]==list(range(len(chain))),'invalid-score-revision-chain')
        for i,r in enumerate(chain):
            L._require(r['payload']['supersedes']==(chain[i-1]['id'] if i else None),'invalid-score-supersedes')
