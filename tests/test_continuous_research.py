"""Synthetic contract tests; no observed-market or predictive evidence."""
import hashlib
import json
import math
import tempfile
import unittest
from dataclasses import replace
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch

from money_maker_3000 import learning as L
from money_maker_3000 import research_cycle as R
from money_maker_3000 import forward_evaluation as F
from money_maker_3000.market_history import Bar


INTERPRETATION = {'instrumentVerified':True,'trainingPermitted':True,'currency':'USD',
    'session':'synthetic weekday sessions','timestampMeaning':'synthetic date','priceType':'synthetic close',
    'adjustments':'none','costs':'none modeled','evidence':'synthetic contract only'}
RETENTION = {'policy':'source-terms'}


def history(n=600):
    result=[]
    d=date(2020,1,1)
    while len(result)<n:
        if d.weekday()<5:
            value=100+math.sin(len(result)/7)*5+len(result)/100
            result.append(Bar('SPY',d.isoformat(),value,value,value,value,0,'synthetic-research'))
        d+=timedelta(days=1)
    return result


def manifest(bars):
    return {'version':'learning-dataset.v1','symbol':'SPY','classification':'synthetic','source':'synthetic-research',
            'sourceEvidence':'synthetic','licenseEvidence':'synthetic','attribution':'synthetic','rightsEvidence':'synthetic',
            'priceBasis':'unadjusted','sha256':L._digest([b.to_dict() for b in bars]),'rowCount':len(bars)}


class ResearchCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/private/tmp')
        self.store=R.EvidenceStore(Path(self.tmp.name)/'evidence')
        self.bars=history()
        self.manifest=manifest(self.bars)
    def tearDown(self):
        self.tmp.cleanup()
    def protocol(self,bars=None):
        bars=bars or self.bars
        return R.freeze_protocol(bars,manifest(bars),'volatility-band-accumulator',RETENTION,INTERPRETATION,
                                 created_at='2026-09-12T00:00:00Z')
    def experiment(self):
        return R.run_experiment(self.store,self.bars,self.protocol())
    def test_three_windows_and_purge_and_retry_identity(self):
        first=self.experiment()
        self.assertEqual(len(first['payload']['candidates']),9)
        for candidate in first['payload']['candidates']:
            self.assertEqual(len(candidate['windows']),3)
            for window in candidate['windows']:
                self.assertLess(window['trainEnd'],window['evaluationFirst'])
        other=self.protocol();other['createdAt']='2026-09-15T00:00:00Z'
        second=R.run_experiment(self.store,self.bars,other)
        self.assertEqual(first,second)
        self.assertEqual(len(self.store.records('experiment')),1)
    def test_reserved_changes_do_not_change_selection_or_fits(self):
        original=self.experiment()
        changed=[replace(b,close=b.close*1.01,open=b.open*1.01,high=b.high*1.01,low=b.low*1.01)
                 if i>=420 else b for i,b in enumerate(self.bars)]
        second=R.run_experiment(self.store,changed,self.protocol(changed))
        self.assertEqual(original['payload']['selectedCandidate'],second['payload']['selectedCandidate'])
        self.assertEqual(original['payload']['candidates'],second['payload']['candidates'])
        models=self.store.records('model')
        fits={}
        for r in models:
            key=L._digest(r['payload']['parameters'])
            fit=r['payload']['fit']
            if key in fits:self.assertEqual(fits[key],fit)
            fits[key]=fit
    def test_mutated_protocol_and_insufficient_history_rejected(self):
        p=self.protocol();p['runtimeSeconds']=999
        with self.assertRaises(L.LearningError):R.run_experiment(self.store,self.bars,p)
        with self.assertRaises(L.LearningError):self.protocol(self.bars[:80])
    def test_retention_before_any_evidence_access(self):
        with self.assertRaisesRegex(L.LearningError,'fmp-inactive'):
            F.status(self.store,source='fmp-eod',retention={'policy':R.FMP_POLICY,'subscriptionStatus':'expired','terminationDate':None})
        with self.assertRaises(L.LearningError):R.retention_check('fmp-eod',RETENTION)
    def test_interrupted_attempt_blocks_retry(self):
        with patch.object(R,'_fit_at',side_effect=RuntimeError('simulated interruption')):
            with self.assertRaises(RuntimeError):self.experiment()
        with self.assertRaisesRegex(L.LearningError,'interrupted-experiment'):self.experiment()
    def test_corrupt_evidence_and_symlink_are_rejected(self):
        r=self.store.put('reference',{'type':'test'})
        path=self.store.root/f"reference-{r['id']}.json"
        path.write_text('{}')
        with self.assertRaises(L.LearningError):self.store.records('reference')
        link=Path(self.tmp.name)/'link';link.symlink_to(self.store.root)
        with self.assertRaises(L.LearningError):R.EvidenceStore(link)
    def test_orphan_fitted_model_cannot_forecast(self):
        experiment=self.experiment()
        model=dict(self.store.records('model')[0]['payload'])
        model['trainingDatasetSha256']='a'*64
        orphan=self.store.put('model',model)
        with self.assertRaisesRegex(L.LearningError,'model-not-registered'):
            F.record_predictions(self.store,self.bars,self.manifest,[orphan['id']],retention=RETENTION,
                retrieved_at='2026-09-12T12:00:00Z',completed_at=self.bars[-1].date+'T23:00:00Z',replay=True)
        self.assertEqual(self.store.records('forecast'),[])
    def test_runtime_exhaustion_records_attempts_without_selection(self):
        with patch.object(R,'_check_deadline',side_effect=L.LearningError('research-runtime-exhausted')):
            result=self.experiment()
        self.assertEqual(result['payload']['status'],'inconclusive')
        self.assertEqual(len(result['payload']['candidates']),9)
        self.assertEqual(result['payload']['models'],[])


class ForwardTests(unittest.TestCase):
    setUp = ResearchCycleTests.setUp
    tearDown = ResearchCycleTests.tearDown
    protocol = ResearchCycleTests.protocol
    experiment = ResearchCycleTests.experiment
    def prepare(self):
        result=self.experiment()
        ids=[m['id'] for m in result['payload']['models']]
        tail=self.bars[-1].date
        completed=tail+'T23:59:00Z'
        created=(date.fromisoformat(tail)+timedelta(days=1)).isoformat()+'T12:00:00Z'
        with patch.object(F,'now',return_value=created):
            first=F.record_predictions(self.store,self.bars,self.manifest,ids,retention=RETENTION,
                                       retrieved_at=created,completed_at=completed,replay=True)
            again=F.record_predictions(self.store,self.bars,self.manifest,ids,retention=RETENTION,
                                       retrieved_at=created,completed_at=completed,replay=True)
        self.assertEqual(again['created'],0)
        return ids,first
    def test_pending_maturity_replay_and_correction_return(self):
        ids,_=self.prepare()
        later=history(605)
        clock='2026-09-12T00:00:00Z'
        pending=F.score_matured(self.store,history(604),manifest(history(604)),retention=RETENTION,retrieved_at=clock)
        self.assertEqual(pending['matured'],0)
        mature=F.score_matured(self.store,later,manifest(later),retention=RETENTION,retrieved_at=clock)
        self.assertEqual(mature['matured'],len(ids))
        self.assertEqual(F.score_matured(self.store,later,manifest(later),retention=RETENTION,retrieved_at=clock)['matured'],0)
        correction=later[:-1]+[replace(later[-1],close=later[-1].close*1.05)]
        self.assertEqual(F.score_matured(self.store,correction,manifest(correction),retention=RETENTION,retrieved_at=clock)['revised'],len(ids))
        self.assertEqual(F.score_matured(self.store,later,manifest(later),retention=RETENTION,retrieved_at=clock)['revised'],len(ids))
        self.assertTrue(all(r['payload']['evidenceType']=='historical-replay' for r in self.store.records('score')))
    def test_future_outcomes_rejected(self):
        self.prepare()
        with self.assertRaisesRegex(L.LearningError,'future-outcome'):
            F.score_matured(self.store,history(605),manifest(history(605)),retention=RETENTION,
                            retrieved_at=self.bars[-1].date+'T23:59:00Z')
    def test_feature_correction_preserves_prediction(self):
        ids,_=self.prepare()
        changed=self.bars[:-1]+[replace(self.bars[-1],close=self.bars[-1].close*1.01)]
        F.record_predictions(self.store,changed,manifest(changed),ids,retention=RETENTION,
                             retrieved_at='2026-09-12T00:00:00Z',completed_at=self.bars[-1].date+'T23:59:00Z',replay=True)
        self.assertEqual(len(self.store.records('forecast')),len(ids))
        revisions=[r for r in self.store.records('reference') if r['payload'].get('type')=='forecast-feature-revision']
        self.assertEqual(len(revisions),len(ids))
    def test_wrong_source_and_price_basis_cannot_read_or_score(self):
        ids,_=self.prepare()
        with patch.object(self.store,'records',side_effect=AssertionError('derived read before source gate')):
            with self.assertRaisesRegex(L.LearningError,'evidence-store-source-mismatch'):
                F.status(self.store,source='other-source',retention=RETENTION)
        later=history(605);wrong=dict(manifest(later),priceBasis='total-return-adjusted')
        before={p.name:p.read_bytes() for p in self.store.root.iterdir() if p.is_file()}
        with self.assertRaisesRegex(L.LearningError,'forward-source-incompatible'):
            F.score_matured(self.store,later,wrong,retention=RETENTION,retrieved_at='2026-09-12T12:00:00Z')
        after={p.name:p.read_bytes() for p in self.store.root.iterdir() if p.is_file()}
        self.assertEqual(before,after)
    def test_disappeared_matured_observation_and_explicit_restoration(self):
        ids,_=self.prepare()
        full=history(605)
        F.score_matured(self.store,full,manifest(full),retention=RETENTION,retrieved_at='2026-09-12T12:00:00Z')
        F.score_matured(self.store,self.bars,self.manifest,retention=RETENTION,retrieved_at='2026-09-12T13:00:00Z')
        withdrawn=F.status(self.store,source='synthetic-research',retention=RETENTION)
        self.assertEqual(withdrawn['pending'],len(ids));self.assertEqual(withdrawn['validScored'],0)
        self.assertEqual(withdrawn['modelDiagnostics'],[])
        # Retained merged rows with unresolved withdrawal metadata are equally unavailable.
        F.score_matured(self.store,full,manifest(full),retention=RETENTION,retrieved_at='2026-09-12T14:00:00Z',unavailable_dates=[full[-1].date])
        self.assertEqual(F.status(self.store,source='synthetic-research',retention=RETENTION)['validScored'],0)
        restored=F.score_matured(self.store,full,manifest(full),retention=RETENTION,retrieved_at='2026-09-12T15:00:00Z')
        self.assertEqual(restored['matured'],0);self.assertEqual(restored['revised'],0)
        status=F.status(self.store,source='synthetic-research',retention=RETENTION)
        self.assertEqual(status['validScored'],len(ids));self.assertEqual(status['pending'],0)
        self.assertEqual(len(status['modelDiagnostics']),len(ids))
    def test_interrupted_corrected_restoration_never_reactivates_old_score(self):
        ids,_=self.prepare()
        original=history(605)
        low=self.bars[-1].close*.5
        original[-1]=replace(original[-1],open=low,high=low,low=low,close=low)
        F.score_matured(self.store,original,manifest(original),retention=RETENTION,retrieved_at='2026-09-12T12:00:00Z')
        F.score_matured(self.store,self.bars,self.manifest,retention=RETENTION,retrieved_at='2026-09-12T13:00:00Z')
        restored=list(original);high=self.bars[-1].close*2
        restored[-1]=replace(restored[-1],open=high,high=high,low=high,close=high)
        put=self.store.put
        def fail_before_score(kind,payload):
            if kind=='score':raise RuntimeError('interrupted before corrected score')
            return put(kind,payload)
        with patch.object(self.store,'put',side_effect=fail_before_score),self.assertRaises(RuntimeError):
            F.score_matured(self.store,restored,manifest(restored),retention=RETENTION,retrieved_at='2026-09-12T14:00:00Z')
        self.assertEqual(F.status(self.store,source='synthetic-research',retention=RETENTION)['validScored'],0)
        self.assertTrue(all(r['payload']['outcome']==0 for r in self.store.records('score')))
        def fail_after_score(kind,payload):
            if kind=='reference' and payload.get('type')=='outcome-availability' and payload['state']=='available':
                raise RuntimeError('interrupted after score before restoration')
            return put(kind,payload)
        with patch.object(self.store,'put',side_effect=fail_after_score),self.assertRaises(RuntimeError):
            F.score_matured(self.store,restored,manifest(restored),retention=RETENTION,retrieved_at='2026-09-12T14:00:00Z')
        self.assertEqual(F.status(self.store,source='synthetic-research',retention=RETENTION)['validScored'],0)
        F.score_matured(self.store,restored,manifest(restored),retention=RETENTION,retrieved_at='2026-09-12T14:00:00Z')
        self.assertEqual(F.status(self.store,source='synthetic-research',retention=RETENTION)['validScored'],len(ids))
        latest={}
        for r in self.store.records('score'):
            p=r['payload']
            if p['forecastId'] not in latest or latest[p['forecastId']]['revision']<p['revision']:latest[p['forecastId']]=p
        self.assertTrue(all(p['outcome']==1 and p['revision']==1 for p in latest.values()))
    def test_uncertainty_and_checkpoint_wait(self):
        ids,_=self.prepare()
        protocol=self.store.records('protocol')[0]['id']
        if len(ids)>1:
            result=F.evaluate_checkpoint(self.store,protocol,ids[0],ids[1],source='synthetic-research',retention=RETENTION)
            self.assertEqual(result['status'],'inconclusive')
        interval=F.paired_block_interval([.02]*60)
        self.assertAlmostEqual(interval['lower'],.02)
        with self.assertRaises(L.LearningError):F.paired_block_interval([.02]*59)


class CheckpointAndTimingTests(unittest.TestCase):
    setUp=ResearchCycleTests.setUp
    tearDown=ResearchCycleTests.tearDown
    protocol=ResearchCycleTests.protocol
    experiment=ResearchCycleTests.experiment
    def test_genuine_clock_boundary_and_backfill(self):
        # Temporary generated data exercises observed-style attestation inputs;
        # it is a contract test, never retained as market evidence.
        delta=date(2026,9,11)-date.fromisoformat(self.bars[-1].date)
        bars=[replace(b,date=(date.fromisoformat(b.date)+delta).isoformat(),source='contract-data') for b in self.bars]
        m=manifest(bars);m.update(source='contract-data',classification='observed-attested')
        p=R.freeze_protocol(bars,m,'volatility-band-accumulator',RETENTION,INTERPRETATION,created_at='2026-09-12T00:00:00Z')
        e=R.run_experiment(self.store,bars,p)
        ids=[x['id'] for x in e['payload']['models']]
        with patch.object(F,'now',return_value='2026-09-13T10:00:00Z'):
            F.record_predictions(self.store,bars,m,ids,retention=RETENTION,retrieved_at='2026-09-13T09:00:00Z',completed_at='2026-09-12T00:00:00Z')
        self.assertTrue(all(r['payload']['evidenceType']=='genuine-forward' for r in self.store.records('forecast')))
        extra=history(601)[-1]
        older=bars[:-1]
        older_manifest=dict(m,rowCount=len(older),sha256=L._digest([b.to_dict() for b in older]))
        with patch.object(F,'now',return_value='2026-09-25T00:00:00Z'):
            F.record_predictions(self.store,older,older_manifest,ids,retention=RETENTION,retrieved_at='2026-09-13T09:00:00Z',completed_at=older[-1].date+'T23:00:00Z')
        self.assertEqual(sum(r['payload']['evidenceType']=='historical-replay' for r in self.store.records('forecast')),len(ids))
        with patch.object(F,'now',return_value='2026-09-13T10:00:00Z'):
            with self.assertRaises(L.LearningError):
                F.record_predictions(self.store,older,older_manifest,ids,retention=RETENTION,retrieved_at='2026-09-13T09:00:00Z',completed_at='2026-09-13T08:00:00Z')
    def test_fixed_sixty_checkpoint_replacement_and_correction(self):
        # Fully generated temporary forecast/score chain; no retained real-market evidence.
        bars=[replace(b,source='contract-data') for b in history(665)]
        m=manifest(bars);m.update(source='contract-data',classification='observed-attested')
        p=R.freeze_protocol(bars,m,'volatility-band-accumulator',RETENTION,INTERPRETATION,
                            created_at=bars[539].date+'T00:00:00Z')
        parameters=p['candidates'][0]
        state=L.STATES[p['strategy']][0]
        incumbent={'version':R.VERSION,'strategy':p['strategy'],'parameters':parameters,
            'fit':L._fit([state]*20,[1]*18+[0]*2,p['strategy']),
            'symbol':'SPY','source':'contract-data','priceBasis':'unadjusted','classification':'observed-attested',
            'trainingEnd':bars[300].date,'selectionEnd':bars[400].date,'knownHistoryEnd':bars[539].date,
            'horizonObservations':5,'featureVersion':'registry-history-state.v1','trainingDatasetSha256':m['sha256'],
            'retention':RETENTION,'interpretation':INTERPRETATION}
        p=R.freeze_protocol(bars,m,'volatility-band-accumulator',RETENTION,INTERPRETATION,
                            created_at=bars[539].date+'T00:00:00Z',incumbent_model=incumbent)
        experiment=R.run_experiment(self.store,bars,p,incumbent=incumbent)
        frozen={'id':experiment['payload']['protocolId']}
        models=[x['id'] for x in experiment['payload']['models']]
        # A monotonically declining tail means the low-probability challenger is better.
        tail=[replace(b,close=200-(i-540),open=200-(i-540),high=200-(i-540),low=200-(i-540)) if i>=540 else b for i,b in enumerate(bars)]
        for i in range(540,610):
            prefix=tail[:i+1];current=dict(m,rowCount=len(prefix),sha256=L._digest([b.to_dict() for b in prefix]))
            clock=(date.fromisoformat(prefix[-1].date)+timedelta(days=1)).isoformat()+'T12:00:00Z'
            with patch.object(F,'now',return_value=clock):
                F.record_predictions(self.store,prefix,current,models,retention=RETENTION,retrieved_at=clock,completed_at=prefix[-1].date+'T23:00:00Z')
        # Exactly 59 then 60 outcomes mature. Public scoring creates all provenance records.
        first=tail[:604];current=dict(m,rowCount=len(first),sha256=L._digest([b.to_dict() for b in first]))
        F.score_matured(self.store,first,current,retention=RETENTION,retrieved_at='2026-09-12T00:00:00Z')
        waiting=F.evaluate_checkpoint(self.store,frozen['id'],models[0],models[1],source='contract-data',retention=RETENTION)
        self.assertEqual(waiting['completedPairs'],59)
        current=dict(m,sha256=L._digest([b.to_dict() for b in tail]))
        F.score_matured(self.store,tail,current,retention=RETENTION,retrieved_at='2026-09-12T00:00:00Z')
        original_put=self.store.put
        def interrupted_reference(kind,payload):
            if kind=='reference' and payload.get('type')=='incumbent-replacement':
                raise RuntimeError('process interrupted after checkpoint publication')
            return original_put(kind,payload)
        with patch.object(self.store,'put',side_effect=interrupted_reference):
            with self.assertRaises(RuntimeError):
                F.evaluate_checkpoint(self.store,frozen['id'],models[0],models[1],source='contract-data',retention=RETENTION)
        with patch.object(F,'paired_block_interval',side_effect=AssertionError('recovery must not reevaluate')):
            supported=F.evaluate_checkpoint(self.store,frozen['id'],models[0],models[1],source='contract-data',retention=RETENTION)
        self.assertEqual(supported['payload']['status'],'supported')
        self.assertEqual(supported['payload']['completedPairs'],60)
        replay=F.evaluate_checkpoint(self.store,frozen['id'],models[0],models[1],source='contract-data',retention=RETENTION)
        self.assertEqual(supported,replay)
        other=None
        if len(models)>2:
            other=F.evaluate_checkpoint(self.store,frozen['id'],models[0],models[2],source='contract-data',retention=RETENTION)
            self.assertEqual(other['payload']['status'],'supported')
        # Crash halfway through a provider correction with seventy matched dates:
        # later eligible pairs cannot replace an unequal original fixed-date pair.
        partial=list(tail)
        partial[604]=replace(partial[604],close=partial[604].close*1.5,high=partial[604].high*1.5)
        partial_manifest=dict(m,sha256=L._digest([b.to_dict() for b in partial]))
        def interrupted_score(kind,payload):
            result=original_put(kind,payload)
            if kind=='score' and payload['asOfDate']==tail[599].date:
                raise RuntimeError('interrupted one side of paired correction')
            return result
        with patch.object(self.store,'put',side_effect=interrupted_score):
            with self.assertRaises(RuntimeError):
                F.score_matured(self.store,partial,partial_manifest,retention=RETENTION,retrieved_at='2026-09-12T00:00:00Z')
        if other:
            F.evaluate_checkpoint(self.store,frozen['id'],models[0],models[2],source='contract-data',retention=RETENTION)
            self.assertEqual(len([r for r in self.store.records('reference') if r['payload'].get('type')=='replacement-retracted-after-correction']),0)
        mismatch=F.evaluate_checkpoint(self.store,frozen['id'],models[0],models[1],source='contract-data',retention=RETENTION)
        self.assertEqual(mismatch['payload']['status'],'inconclusive')
        self.assertLess(mismatch['payload']['currentEligiblePairs'],60)
        self.assertEqual(mismatch['payload']['fixedDateCount'],60)
        corrected=[replace(b,close=100+(i-540),open=100+(i-540),high=100+(i-540),low=100+(i-540)) if i>=540 else b for i,b in enumerate(tail)]
        current=dict(m,sha256=L._digest([b.to_dict() for b in corrected]))
        F.score_matured(self.store,corrected,current,retention=RETENTION,retrieved_at='2026-09-12T00:00:00Z')
        if other:
            F.evaluate_checkpoint(self.store,frozen['id'],models[0],models[2],source='contract-data',retention=RETENTION)
            self.assertEqual(len([r for r in self.store.records('reference') if r['payload'].get('type')=='replacement-retracted-after-correction']),1)
        reevaluated=F.evaluate_checkpoint(self.store,frozen['id'],models[0],models[1],source='contract-data',retention=RETENTION)
        # Corrected feature histories invalidate conclusive portability of the old probabilities.
        self.assertEqual(reevaluated['payload']['status'],'inconclusive')
        self.assertEqual(reevaluated['payload']['supersedes'],mismatch['id'])
        self.assertEqual(reevaluated['payload']['checkpointDates'],supported['payload']['checkpointDates'])
        self.assertEqual(len([r for r in self.store.records('reference') if r['payload'].get('type')=='incumbent-replacement']),1)
        self.assertEqual(len([r for r in self.store.records('reference') if r['payload'].get('type')=='replacement-retracted-after-correction']),1)


class CloseOnlyIntegrationTests(unittest.TestCase):
    def test_collector_version_through_training_forecast_and_score(self):
        from money_maker_3000.feed_collection import persist_version
        from test_feed_collection import POLICY,MEANING
        from copy import deepcopy
        meaning=deepcopy(MEANING);meaning['priceBasis']='unadjusted'
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            root=Path(folder)
            rows=[dict(date=b.date,timestamp=b.date+'T00:00:00Z',open=None,high=None,low=None,close=b.close,volume=None) for b in history()]
            retrieval=persist_version(root/'collection',symbol='SPY',observations=rows,interpretation=meaning,retention=POLICY,retrieved_at='2026-09-12T12:00:00Z')
            bars,m=R.load_observation_dataset(root/'collection'/('SPY-'+retrieval['version']+'.json'),retention=POLICY,expected_sha256=retrieval['version'],allow_synthetic_smoke=True)
            self.assertTrue(all(b.open is None and b.volume is None for b in bars))
            store=R.EvidenceStore(root/'research')
            p=R.freeze_protocol(bars,m,'slow-trend-allocation',POLICY,INTERPRETATION)
            experiment=R.run_experiment(store,bars,p)
            ids=[x['id'] for x in experiment['payload']['models']]
            forecast=F.record_predictions(store,bars,m,ids,retention=POLICY,retrieved_at='2026-09-12T12:00:00Z',completed_at=bars[-1].date+'T23:00:00Z',replay=True)
            self.assertGreater(forecast['created'],0)
            newer=[dict(date=b.date,timestamp=b.date+'T00:00:00Z',open=None,high=None,low=None,close=b.close,volume=None) for b in history(605)]
            retrieval=persist_version(root/'collection',symbol='SPY',observations=newer,interpretation=meaning,retention=POLICY,retrieved_at='2026-09-12T13:00:00Z')
            bars,m=R.load_observation_dataset(root/'collection'/('SPY-'+retrieval['version']+'.json'),retention=POLICY,expected_sha256=retrieval['version'],allow_synthetic_smoke=True)
            scored=F.score_matured(store,bars,m,retention=POLICY,retrieved_at='2026-09-12T13:00:00Z')
            self.assertEqual(scored['matured'],len(ids))
            self.assertEqual(F.score_matured(store,bars,m,retention=POLICY,retrieved_at='2026-09-12T13:00:00Z')['matured'],0)

class WorkflowTests(unittest.TestCase):
    def test_cycle_status_replay_no_new_data_and_retention_block(self):
        from money_maker_3000.research_cycle_cli import coordinate
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            root=Path(folder)
            bars=history()
            csv='symbol,date,open,high,low,close,volume,source\n'+''.join(','.join(map(str,(b.symbol,b.date,b.open,b.high,b.low,b.close,b.volume,b.source)))+'\n' for b in bars)
            (root/'history.csv').write_text(csv)
            m=manifest(bars);m['sha256']=hashlib.sha256(csv.encode()).hexdigest()
            (root/'manifest.json').write_text(json.dumps(m))
            entry={'csvPath':'history.csv','manifestPath':'manifest.json','source':m['source'],'symbol':'SPY','retention':RETENTION}
            inventory={'version':'money-maker-learning-inventory.v1','datasets':[entry]}
            (root/'inventory.json').write_text(json.dumps(inventory))
            config={'version':'continuous-research-config.v1','dataRoot':str(root),'storeRoot':str(root/'store'),
                'inventoryPath':'inventory.json','interpretations':{m['source']:INTERPRETATION},'existingModels':{},
                'availability':{m['source']+'/SPY':{'retrievedAt':'2026-09-12T12:00:00Z','completedAt':bars[-1].date+'T23:00:00Z','datasetSha256':m['sha256']}},
                'collection':None,'portability':None}
            path=root/'config.json';path.write_text(json.dumps(config))
            first=coordinate(path,allow_synthetic=True)
            self.assertEqual(first['status'],'complete')
            self.assertEqual(len(first['results']),2)
            second=coordinate(path,allow_synthetic=True)
            self.assertTrue(all(r['predictions']['created']==0 for r in second['results']))
            before={str(p):p.read_bytes() for p in (root/'store').rglob('*') if p.is_file()}
            stat=coordinate(path,mode='status',allow_synthetic=True)
            replay=coordinate(path,mode='replay',allow_synthetic=True)
            after={str(p):p.read_bytes() for p in (root/'store').rglob('*') if p.is_file()}
            self.assertEqual(before,after)
            self.assertEqual(stat['status'],'complete');self.assertEqual(replay['status'],'complete')
            entry.update(source='fmp-eod',retention={'policy':R.FMP_POLICY,'subscriptionStatus':'expired','terminationDate':None})
            (root/'inventory.json').write_text(json.dumps(inventory))
            with patch.object(L,'load_dataset',side_effect=AssertionError('must not read expired source')):
                blocked=coordinate(path,mode='status',allow_synthetic=True)
            self.assertEqual(blocked['status'],'blocked')
            self.assertIn('source-retention',blocked['blocked'][0]['reason'])
    def test_collection_preflight_before_profile_and_offline_modes(self):
        from money_maker_3000.research_cycle_cli import refresh_collection
        from money_maker_3000.feed_collection import default_retention
        config={'profilePath':'/nonexistent/profile','outputRoot':'/nonexistent/output',
                'retention':default_retention(),'interpretations':{},'symbols':['SPY']}
        with patch('money_maker_3000.feed_collection.EtoroReader',side_effect=AssertionError('credentials forbidden')):
            result=refresh_collection(config)
        self.assertEqual(result['status'],'inconclusive')

    def test_conflicting_source_attestations_block_actual_report_before_loader(self):
        from money_maker_3000 import portability as P
        from money_maker_3000.research_cycle_cli import coordinate
        from test_portability import bars as port_bars,descriptor
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            root=Path(folder);left=port_bars('fmp-eod');right=port_bars('kibot')
            active={'policy':R.FMP_POLICY,'subscriptionStatus':'active','terminationDate':None}
            expired=dict(active,subscriptionStatus='expired')
            da=descriptor('fmp-eod');da['retention']=active
            P.freeze_protocol(root/'protocol.json',train_end=left[250].date,selection_end=left[350].date,
                evaluation_start=left[351].date,evaluation_end=left[-1].date,created_at='2026-09-12T00:00:00Z')
            report=P.evaluate_pair(left,right,da,descriptor('kibot'),root/'protocol.json',as_of='2026-09-12')
            P.write_report(report,root/'report.json')
            self.assertEqual(len(P.load_report(root/'report.json',current_retentions={'fmp-eod':active,'kibot':RETENTION})['strategies']),2)
            entries=[{'source':source,'symbol':symbol,'retention':policy,'csvPath':'unused.csv','manifestPath':'unused.json'}
                     for source,symbol,policy in [('fmp-eod','SPY',expired),('fmp-eod','QQQ',active),('kibot','SPY',RETENTION)]]
            (root/'inventory.json').write_text(json.dumps({'version':'money-maker-learning-inventory.v1','datasets':entries}))
            config={'version':'continuous-research-config.v1','dataRoot':str(root),'storeRoot':str(root/'store'),
                'inventoryPath':'inventory.json','interpretations':{},'existingModels':{},'availability':{},'collection':None,
                'portability':{'reportPath':str(root/'report.json'),'reportSources':['fmp-eod','kibot']}}
            (root/'config.json').write_text(json.dumps(config))
            with patch.object(P,'load_report',side_effect=AssertionError('must not read report with conflicting source rights')):
                result=coordinate(root/'config.json',mode='status')
            self.assertEqual(result['portability']['status'],'inconclusive')
            self.assertEqual(len(result['blocked']),2)
            self.assertTrue(all(r['source']=='kibot' for r in result['results']))


if __name__=='__main__':
    unittest.main()
