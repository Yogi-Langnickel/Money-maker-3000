"""Synthetic mechanics only; no empirical economic conclusions."""
import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import math
import tempfile
import unittest
from unittest.mock import patch

from money_maker_3000 import autonomous_research as A, autonomous_recovery as V, shadow_portfolio as H
from money_maker_3000 import autonomous_cli as C, economic_portfolio as E, signal_toolkit as S, profit_hypothesis as P
from money_maker_3000.engine import build_simulation_run


def stamp(value):
    return value.isoformat().replace('+00:00','Z')


def packet(count=420, symbol='SPY'):
    start=datetime(2023,1,1,tzinfo=timezone.utc); rows=[]
    for index in range(count):
        opening=start+timedelta(days=index,hours=9); ending=opening+timedelta(hours=7)
        price=100+index*.03+math.sin(index/7)*3
        rows.append({'date':opening.date().isoformat(),'start':stamp(opening),'end':stamp(ending),'availableAt':stamp(ending+timedelta(minutes=5)),
                     'open':price,'high':price+2,'low':price-2,'close':price+1,'volume':1000.0})
    keys=('identity','currency','sessionConvention','timestampMeaning','adjustmentBasis','priceBasis')
    interpretation=dict(zip(keys,(symbol+' ETF','AUD' if symbol=='VAS' else 'USD','synthetic-session','synthetic-close','unadjusted','synthetic')))
    interpretation['evidence']={key:{'status':'verified','reference':'synthetic-fixture-only'} for key in keys}
    return {'version':A.VERSION,'source':'synthetic','symbol':symbol,'classification':'synthetic','retention':{'policy':'source-terms'},
            'interpretation':interpretation,'observations':rows}


def clock_after(data):
    return stamp(S.utc_timestamp(data['observations'][-1]['availableAt'])+timedelta(hours=1))


class CorrectnessRepairTests(unittest.TestCase):
    def test_late_availability_and_invalid_supplied_times(self):
        rows=[{'date':'2024-01-01','close':100,'availableAt':'2024-01-05T00:00:00Z'},
              {'date':'2024-01-02','close':101,'availableAt':'2024-01-03T00:00:00Z'}]
        with self.assertRaisesRegex(S.SignalError,'row-unavailable'):
            S.evaluate_feature('simple-return',rows,completed_at='2024-01-02T00:00:00Z',available_at='2024-01-03T00:00:00Z')
        for value in ('',False,0):
            rows[0]['availableAt']=value
            with self.assertRaises(S.SignalError):
                S.evaluate_feature('simple-return',rows,completed_at='2024-01-02T00:00:00Z',available_at='2024-01-03T00:00:00Z')

    def test_atr_rejects_inconsistent_ranges_and_open(self):
        data=packet(15)['observations']; data[-1]['high']=data[-1]['close']-1
        with self.assertRaisesRegex(S.SignalError,'inconsistent-ohlc'):
            S.evaluate_feature('normalized-atr',data,completed_at=data[-1]['end'],available_at=data[-1]['availableAt'],ohlc_attested=True,ohlc_basis='synthetic')

    def test_empty_explicit_policies_reject(self):
        for key in ('budget_policy','allocation_policy','risk_policy','schedule_policy'):
            with self.assertRaisesRegex(ValueError,'invalid-.*-policy'):
                build_simulation_run(**{key:{}})
        self.assertEqual(build_simulation_run()['decision'],'skip')

    def test_three_trades_never_strong(self):
        result={'tradeCount':3,'expectancy':100,'netSimulatedReturn':.5}
        self.assertEqual(P._grade(result,.1,[.1,.2],.3)[0],'unclear')

    def test_terminal_cost_and_overflow(self):
        data=packet(17)['observations']
        for row in data:
            row['close']=100.0;row['open']=100.0;row['low']=99.;row['high']=101.
        data[14]['close']=101.;data[14]['high']=102.
        result=P.backtest(P.frozen_hypotheses(100)[0],data)
        self.assertTrue(result['terminalPositionLiquidated']);self.assertFalse(result['openPositionAtEnd'])
        self.assertAlmostEqual(result['finalEquity'],10000*.99/1.01)
        overflow=copy.deepcopy(data); overflow[15]['open']=1e-308;overflow[15]['close']=1e308
        with self.assertRaisesRegex(P.HypothesisError,'arithmetic-overflow'):
            P.backtest(P.frozen_hypotheses(100)[0],overflow)

    def test_stale_higher_gate_is_not_confirmation(self):
        rows=packet(2)['observations']; dataset={'intervals':{'1d':{'rows':rows},'1w':{'rows':[{'end':'2022-01-01T00:00:00Z','availableAt':'2022-01-01T00:00:00Z'}]}}}
        allowed,_=P._aligned_signal_indices(dataset,'1d');self.assertEqual(allowed,set())


class PortfolioTests(unittest.TestCase):
    def flat(self,count=8):
        data=packet(count)['observations']
        for row in data:
            row.update(open=100.,high=101.,low=99.,close=100.)
        return data

    def test_exact_flat_capital_and_passive_parity(self):
        rows=self.flat();policy={**E.POLICY,'reserveFraction':0.,'maxExposure':1.,'costBps':100.}
        decisions=[{'index':-1,'availableAt':rows[0]['start'],'probabilityUp':1.}]
        result=E.simulate(rows,decisions,policy); passive=E.simulate(rows,[],policy,passive=True)
        self.assertEqual(result['finalCash'],passive['finalCash']);self.assertEqual(result['simulatedCosts'],passive['simulatedCosts'])
        self.assertAlmostEqual(result['finalCash'],10000*.99/1.01)
        self.assertAlmostEqual(result['simulatedCosts'],198.01980198019803)
        self.assertAlmostEqual(result['finalCash']+result['simulatedCosts'],10000)
        cash=E.simulate(rows,[],policy);self.assertEqual(cash['finalCash'],10000)

    def test_reserve_exposure_and_cash_exit_priority(self):
        rows=self.flat();rows[1].update(open=120.,high=131.,low=119.,close=130.)
        decisions=[{'index':-1,'availableAt':rows[0]['start'],'probabilityUp':1.},
                   {'index':1,'availableAt':rows[1]['availableAt'],'probabilityUp':0.}]
        result=E.simulate(rows,decisions)
        self.assertGreaterEqual(result['ledger'][0]['simulatedCash']/ (10000-result['ledger'][0]['cost']), .2-1e-12)
        sale=next(item for item in result['ledger'] if item['type']=='sell' and item['bar']==2)
        self.assertEqual(sale['simulatedQuantity'],0);self.assertTrue(sale['roundTripClosed'])

    def test_gap_loss_stop_uses_later_open(self):
        rows=self.flat();rows[1].update(open=95.,high=96.,low=79.,close=80.)
        rows[2].update(open=70.,high=101.,low=69.,close=100.)
        result=E.simulate(rows,[{'index':-1,'availableAt':rows[0]['start'],'probabilityUp':1.}])
        self.assertTrue(result['riskStopped'])
        self.assertEqual(next(item for item in result['ledger'] if item['type']=='sell')['fill'],70.)

    def test_invalid_prices_ohlc_policy_and_future_fill(self):
        rows=self.flat()
        for value in (1e308,float('inf'),float('nan'),True,10**400):
            bad=copy.deepcopy(rows);bad[1]['close']=value
            with self.assertRaises(E.EconomicError):E.simulate(bad,[])
        bad=copy.deepcopy(rows);bad[1]['low']=110.
        with self.assertRaisesRegex(E.EconomicError,'inconsistent-ohlc'):E.simulate(bad,[])
        with self.assertRaises(E.EconomicError):E.simulate(rows,[],{})
        late=[{'index':0,'availableAt':rows[2]['end'],'probabilityUp':1.}]
        result=E.simulate(rows,late)
        self.assertEqual(result['ledger'][0]['bar'],3)

    def test_passive_is_buy_and_hold_and_ordinary_cadence_is_enforced(self):
        rows=self.flat(30)
        for index,row in enumerate(rows):row.update(open=100+index*10,high=102+index*10,low=99+index*10,close=101+index*10)
        passive=E.simulate(rows,[],passive=True)
        self.assertEqual([item['type'] for item in passive['ledger']],['buy','terminal-sell'])
        forecasts=[{'index':0,'availableAt':rows[0]['availableAt'],'probabilityUp':1.}, {'index':1,'availableAt':rows[1]['availableAt'],'probabilityUp':0.}]
        with self.assertRaisesRegex(E.EconomicError,'cadence'):E.simulate(rows,forecasts)

    def test_partial_rebalances_are_not_roundtrips(self):
        rows=self.flat(30)
        for index,row in enumerate(rows):row.update(open=100+index*10,high=102+index*10,low=99+index*10,close=101+index*10)
        result=E.simulate(rows,[{'index':-1,'availableAt':rows[0]['start'],'probabilityUp':1.}])
        self.assertGreater(sum(item['type']=='sell' for item in result['ledger']),10)
        self.assertEqual(sum(item['type']=='sell' and item['roundTripClosed'] for item in result['ledger']),0)


class CheckpointTransitionTests(unittest.TestCase):
    def test_qualified_corrected_rejected_restored_and_interrupted_reference(self):
        # In-memory predicate fixtures prove transitions only. A persisted forged
        # score would fail full semantic replay against its original observations.
        from contextlib import nullcontext
        class MemoryStore:
            def __init__(self):self.events=[]
            def locked(self,**kwargs):return nullcontext()
            def records(self,kind=None):return [record for record in self.events if kind is None or record['kind']==kind]
            def append(self,kind,payload):
                record={'id':S.digest({'kind':kind,'payload':payload,'sequence':len(self.events)}),'kind':kind,'payload':payload}
                self.events.append(record);return record
        store=MemoryStore()
        book={'simulatedNetReturn':.2,'riskStopped':False,'ledger':[{'type':'sell','roundTripClosed':True} for _ in range(10)]}
        score={'modelId':'model','protocolId':'protocol','status':'scored','genuineProspective':True,'candidate':book,
               'passive':{'simulatedNetReturn':.1},'doubleCostCandidate':{'simulatedNetReturn':.15},
               'doubleCostPassive':{'simulatedNetReturn':.1},'uncertainty':{'lowerBound':.001},
               'confirmationWindowDates':['2024-01-01']*105,'availabilityRevisionIds':[], 'decisionIds':[],
               'outcomeDateBindings':{},'supersedes':None}
        original=store.append('score',copy.deepcopy(score));qualified=H.checkpoint(store,'model')
        self.assertEqual(qualified['payload']['status'],'evidence-qualified-monitoring-candidate')
        self.assertEqual(H.active_reference(store)['payload']['modelId'],'model')
        count=len(store.events);self.assertEqual(H.checkpoint(store,'model'),qualified);self.assertEqual(len(store.events),count)
        store.append('availability',{'date':'2030-01-01','available':False,'supersedes':None})
        self.assertEqual(H.checkpoint(store,'model'),qualified);self.assertEqual(H.active_reference(store)['payload']['modelId'],'model')
        corrected=copy.deepcopy(score);corrected['candidate']['simulatedNetReturn']=-.1;corrected['supersedes']=original['id']
        store.append('score',corrected);append=store.append;raised=[]
        def interrupted(kind,payload):
            if kind=='reference' and not raised:
                raised.append(True);raise OSError('synthetic-interruption')
            return append(kind,payload)
        with patch.object(store,'append',side_effect=interrupted):
            with self.assertRaises(OSError):H.checkpoint(store,'model')
        H.checkpoint(store,'model');self.assertIsNone(H.active_reference(store)['payload']['modelId'])
        restored=copy.deepcopy(score);restored['supersedes']=store.records('score')[-1]['id'];store.append('score',restored)
        H.checkpoint(store,'model');self.assertEqual(H.active_reference(store)['payload']['modelId'],'model')
        count=len(store.events);H.checkpoint(store,'model');self.assertEqual(len(store.events),count)


    def test_corrected_challenger_still_compares_frozen_original_reference(self):
        from contextlib import nullcontext
        class MemoryStore:
            def __init__(self):self.events=[]
            def locked(self,**kwargs):return nullcontext()
            def records(self,kind=None):return [record for record in self.events if kind is None or record['kind']==kind]
            def append(self,kind,payload):
                record={'id':S.digest({'kind':kind,'payload':payload,'sequence':len(self.events)}),'kind':kind,'payload':payload}
                self.events.append(record);return record
        store=MemoryStore();equity=[{'simulatedLiquidationEquity':100.}]*105
        store.append('score',{'modelId':'A','status':'scored'})
        reference=store.append('reference',{'modelId':'A','status':'frozen-economic-reference','checkpointId':'old-checkpoint','supersedes':None,'reason':'predicate-fixture'})
        packet_data=packet();store.append('input',{'packet':packet_data,'sha256':S.digest(packet_data)})
        store.append('protocol',{'comparisonCount':18})['id']='protocol'
        score={'modelId':'B','protocolId':'protocol','status':'scored','genuineProspective':True,
               'candidate':{'simulatedNetReturn':.20,'riskStopped':False,'ledger':[{'type':'sell','roundTripClosed':True} for _ in range(10)],'equity':equity},
               'passive':{'simulatedNetReturn':.10},'doubleCostCandidate':{'simulatedNetReturn':.15},'doubleCostPassive':{'simulatedNetReturn':.10},
               'uncertainty':{'lowerBound':.001},'confirmationWindowDates':['2024-01-01']*105,'availabilityRevisionIds':[],
               'decisionIds':[],'outcomeDateBindings':{},'datasetSha256':S.digest(packet_data),'supersedes':None}
        store.append('score',score)
        with patch.object(H,'_matched_reference',return_value={'simulatedNetReturn':.18,'equity':equity}),patch.object(A,'uncertainty',return_value={'lowerBound':.001}),patch.object(H,'_repair_reference'):
            qualified=H.checkpoint(store,'B');self.assertEqual(qualified['payload']['status'],'evidence-qualified-monitoring-candidate')
            self.assertEqual(H.active_reference(store)['payload']['modelId'],'B')
            corrected=copy.deepcopy(score);corrected['candidate']['simulatedNetReturn']=.17
            corrected['supersedes']=store.records('score')[-1]['id'];store.append('score',corrected)
            rejected=H.checkpoint(store,'B');self.assertEqual(rejected['payload']['status'],'rejected')
            self.assertEqual(rejected['payload']['activeReferenceId'],reference['id'])
            self.assertEqual(H.checkpoint(store,'B'),rejected)


class AutonomousTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.store=A.Store(self.root/'store',source='synthetic',retention={'policy':'source-terms'},create=True)
    def tearDown(self):self.temp.cleanup()
    def frozen(self):
        data=packet()
        with patch.object(A,'now',return_value=clock_after(data)):
            protocol=A.freeze(self.store,data,allow_synthetic=True);result=A.run_research(self.store,protocol['id'])
        return data,protocol,result

    def test_interrupted_complete_resume_and_retry_bytes(self):
        data=packet()
        with patch.object(A,'now',return_value=clock_after(data)):
            protocol=A.freeze(self.store,data,allow_synthetic=True)
            with self.assertRaisesRegex(E.EconomicError,'interrupted'):
                A.run_research(self.store,protocol['id'],interrupt_after=1)
            self.assertEqual(len(self.store.records('trial-result')),1)
            result=A.run_research(self.store,protocol['id']);before={path.name:path.read_bytes() for path in self.store.root.glob('*.json')}
            self.assertEqual(A.run_research(self.store,protocol['id']),result)
            self.assertEqual(before,{path.name:path.read_bytes() for path in self.store.root.glob('*.json')})
            self.assertEqual(len(self.store.records('trial-start')),3)
            self.assertFalse(result['payload']['monitoringEligible'])
            self.assertEqual(V.semantic_replay(self.store)['status'],'matched')

    def test_future_perturbation_does_not_change_fitted_forecast(self):
        data,protocol,_=self.frozen();model=A.model_for(data,protocol['payload'],protocol['payload']['candidates'][0])
        original=A.forecast(model,data,300);changed=copy.deepcopy(data)
        for row in changed['observations'][301:]:row.update(open=1000.,high=1002.,low=999.,close=1001.)
        self.assertEqual(A.forecast(model,changed,300),original)
        self.assertEqual(A.model_for(changed,protocol['payload'],protocol['payload']['candidates'][0])['fit'],model['fit'])

    def test_holdout_retune_rejected_and_alpha_spending(self):
        data,protocol,_=self.frozen()
        with self.assertRaisesRegex(E.EconomicError,'retune'):
            A.freeze(self.store,data,policy={**E.POLICY,'costBps':20.},allow_synthetic=True)
        self.assertEqual(protocol['payload']['comparisonCount'],18)
        self.assertLess(sum(.05/(n*(n+1)) for n in range(1,10000)),.05)
        interval=A.uncertainty([.02]*20,18);self.assertGreater(interval['lowerBound'],0)
        self.assertIsNone(A.uncertainty([.02]*19,18)['lowerBound'])

    def test_stale_observation_fresh_retrieval_does_not_qualify(self):
        data=packet();data['observations'][-1]['availableAt']=A.now()
        report=A.status(self.store,data);self.assertEqual(report['feedFreshness'],'stale')
        self.assertEqual(report['exchangeCalendarFreshness'],'unverified')

    def test_native_unknown_no_data_and_separate_currency(self):
        self.assertEqual(C.coordinate()['status'],'no-data')
        data=packet(10,'VAS');data['interpretation']['adjustmentBasis']='unknown'
        self.assertIn('adjustmentBasis-unverified',A.validate_packet(data,allow_synthetic=True))
        self.assertEqual(data['interpretation']['currency'],'AUD')
        data['observations'][0]['accountId']='forbidden'
        with self.assertRaises(E.EconomicError):A.validate_packet(data,allow_synthetic=True)

    def test_prospective_shadow_corrections_withdrawal_restore_and_replay(self):
        _,protocol,_=self.frozen();next_data=packet(421)
        with patch.object(A,'now',return_value=clock_after(next_data)):
            decision=H.record_decisions(self.store,next_data,protocol['id']);repeat=H.record_decisions(self.store,next_data,protocol['id'])
            self.assertEqual(decision['decisionIds'],repeat['decisionIds']);self.assertTrue(decision['decisionIds'])
            score=H.score_later(self.store,next_data);self.assertEqual(self.store.records('score')[-1]['payload']['status'],'pending')
        later=packet(426)
        with patch.object(A,'now',return_value=clock_after(later)):
            H.score_later(self.store,later);scored=self.store.records('score')[-1]
            self.assertEqual(scored['payload']['status'],'scored');self.assertEqual(len(scored['payload']['candidate']['equity']),5)
            model=scored['payload']['modelId'];self.assertEqual(H.checkpoint(self.store,model)['payload']['status'],'insufficient-evidence')
            original_score=scored['id']
            H.availability(self.store,[later['observations'][-1]['date']],available=False);H.score_later(self.store,later)
            self.assertEqual(self.store.records('score')[-1]['payload']['status'],'unavailable')
            H.availability(self.store,[later['observations'][-1]['date']],available=True)
            self.assertEqual(H.checkpoint(self.store,model)['payload']['status'],'unavailable')
            H.score_later(self.store,later);restored=self.store.records('score')[-1]
            self.assertEqual(restored['payload']['status'],'scored');self.assertNotEqual(original_score,restored['id'])
            changed=copy.deepcopy(later);changed['observations'][420]['close']+=.2
            H.score_later(self.store,changed);self.assertEqual(self.store.records('score')[-1]['payload']['status'],'unavailable')
            H.score_later(self.store,later);final=self.store.records('score')[-1]
            self.assertEqual(final['payload']['status'],'scored');self.assertNotEqual(final['id'],restored['id'])
            count=len(self.store.records());H.score_later(self.store,later);self.assertEqual(len(self.store.records()),count)
            self.assertEqual(V.semantic_replay(self.store)['status'],'matched')

    def test_mature_horizon_does_not_shift_when_date_disappears(self):
        _,protocol,_=self.frozen();first=packet(421)
        with patch.object(A,'now',return_value=clock_after(first)):H.record_decisions(self.store,first,protocol['id'])
        later=packet(427)
        with patch.object(A,'now',return_value=clock_after(later)):
            H.score_later(self.store,later);first_score=self.store.records('score')[-1]
            bound=copy.deepcopy(first_score['payload']['outcomeDateBindings'])
            shortened=copy.deepcopy(later);del shortened['observations'][423]
            H.score_later(self.store,shortened);score=self.store.records('score')[-1]
            self.assertEqual(score['payload']['status'],'unavailable');self.assertEqual(score['payload']['outcomeDateBindings'],bound)

    def test_fixed_confirmation_window_does_not_expand(self):
        _,protocol,_=self.frozen()
        for count in range(421,527,5):
            data=packet(count)
            with patch.object(A,'now',return_value=clock_after(data)):
                H.record_decisions(self.store,data,protocol['id']);H.score_later(self.store,data)
        last=packet(541)
        with patch.object(A,'now',return_value=clock_after(last)):
            H.score_later(self.store,last);score=self.store.records('score')[-1]
            self.assertEqual(len(score['payload']['confirmationWindowDates']),105)
            original=score['payload']['confirmationWindowDates'];later=packet(551)
            with patch.object(A,'now',return_value=clock_after(later)):H.score_later(self.store,later)
            self.assertEqual(self.store.records('score')[-1]['payload']['confirmationWindowDates'],original)
            self.assertEqual(len(self.store.records('score')[-1]['payload']['candidate']['equity']),105)

    def test_snapshot_isolated_restore_retry_semantic_replay(self):
        self.frozen();snap=V.snapshot(self.store,self.root/'snapshots')
        result=V.restore(self.root/'snapshots'/(snap['sha256']+'.json'),self.root/'restore',source='synthetic',retention={'policy':'source-terms'})
        self.assertEqual(result['status'],'restored-and-replayed')
        again=V.restore(self.root/'snapshots'/(snap['sha256']+'.json'),self.root/'restore',source='synthetic',retention={'policy':'source-terms'})
        self.assertEqual(result,again)
        dirty=self.root/'dirty';dirty.mkdir(mode=0o700);(dirty/'other').write_text('preserve')
        with self.assertRaisesRegex(E.EconomicError,'isolated-empty'):
            V.restore(self.root/'snapshots'/(snap['sha256']+'.json'),dirty,source='synthetic',retention={'policy':'source-terms'})

    def test_source_semantics_and_classification_cannot_be_laundered(self):
        _,protocol,_=self.frozen();data=packet(421)
        for field,value in (('classification','observed-attested'),('source','fmp-eod')):
            changed=copy.deepcopy(data);changed[field]=value
            if field=='source':changed['retention']={'policy':A.R.FMP_POLICY,'subscriptionStatus':'active','terminationDate':None}
            with patch.object(A,'now',return_value=clock_after(changed)):
                with self.assertRaisesRegex(E.EconomicError,'semantics|source'):
                    H.record_decisions(self.store,changed,protocol['id'])
        changed=copy.deepcopy(data);changed['interpretation']['adjustmentBasis']='split-adjusted'
        with patch.object(A,'now',return_value=clock_after(changed)):
            with self.assertRaisesRegex(E.EconomicError,'semantics'):
                H.record_decisions(self.store,changed,protocol['id'])

    def test_stale_shadow_and_fast_timestamp_spoof_reject(self):
        _,protocol,_=self.frozen();data=packet(421);data['observations'][-1]['availableAt']=A.now()
        with self.assertRaisesRegex(E.EconomicError,'stale-input'):
            H.record_decisions(self.store,data,protocol['id'])
        fast=packet(2);start=S.utc_timestamp(fast['observations'][0]['start'])
        for index,row in enumerate(fast['observations']):
            row['start']=stamp(start+timedelta(seconds=index*2));row['end']=stamp(start+timedelta(seconds=index*2+1));row['availableAt']=row['end']
        with self.assertRaisesRegex(E.EconomicError,'time-order'):
            A.validate_packet(fast,allow_synthetic=True)

    def test_withdrawal_before_maturity_does_not_shift_horizon(self):
        _,protocol,_=self.frozen();data=packet(421)
        with patch.object(A,'now',return_value=clock_after(data)):
            H.record_decisions(self.store,data,protocol['id']);H.score_later(self.store,data)
        later=packet(427);missing=later['observations'][423]['date'];del later['observations'][423]
        with patch.object(A,'now',return_value=clock_after(later)):
            H.availability(self.store,[missing],available=False);H.score_later(self.store,later)
            self.assertEqual(self.store.records('score')[-1]['payload']['status'],'unavailable')
            intact=packet(427);H.availability(self.store,[missing],available=True);H.score_later(self.store,intact)
            restored=self.store.records('score')[-1]['payload'];self.assertEqual(restored['status'],'scored')
            self.assertEqual(len(restored['candidate']['equity']),5)
            self.assertEqual(next(iter(restored['outcomeDateBindings'].values())),[row['date'] for row in intact['observations'][421:426]])

    def test_unavailable_revision_preserves_all_fixed_contributors(self):
        _,protocol,_=self.frozen()
        for count in range(421,527,5):
            data=packet(count)
            with patch.object(A,'now',return_value=clock_after(data)):
                H.record_decisions(self.store,data,protocol['id']);H.score_later(self.store,data)
        later=packet(531)
        with patch.object(A,'now',return_value=clock_after(later)):
            H.score_later(self.store,later);original=self.store.records('score')[-1]['payload']
            self.assertEqual(len(original['confirmationWindowDates']),105)
            fixed=original['confirmationDecisionIds'];date=original['confirmationWindowDates'][10]
            H.availability(self.store,[date],available=False);H.score_later(self.store,later)
            self.assertEqual(self.store.records('score')[-1]['payload']['confirmationDecisionIds'],fixed)
            H.score_later(self.store,later);self.assertEqual(self.store.records('score')[-1]['payload']['status'],'unavailable')
            H.availability(self.store,[date],available=True);H.score_later(self.store,later)
            restored=self.store.records('score')[-1]['payload'];self.assertEqual(restored['status'],'scored')
            self.assertEqual(restored['confirmationDecisionIds'],fixed)
            self.assertEqual(restored['candidate'],original['candidate'])

    def test_incumbent_cadence_stays_with_its_original_protocol(self):
        data=packet()
        with patch.object(A,'now',return_value=clock_after(data)):
            first=A.freeze(self.store,data,policy={**E.POLICY,'cadenceObservations':10},allow_synthetic=True)
            original=A.run_research(self.store,first['id'])['payload']['selectedModelId']
        data=packet(421)
        with patch.object(A,'now',return_value=clock_after(data)):
            H.record_decisions(self.store,data,first['id'])
            second=A.freeze(self.store,data,allow_synthetic=True);A.run_research(self.store,second['id'])
        data=packet(426);reference=copy.deepcopy(H.active_reference(self.store));reference['payload']['modelId']=original
        # Stub only reference eligibility to isolate cadence; synthetic data never
        # qualifies a real active reference. The model/protocol remain genuine fixtures.
        with patch.object(A,'now',return_value=clock_after(data)),patch.object(H,'active_reference',return_value=reference),patch.object(H,'_repair_reference'):
            H.record_decisions(self.store,data,second['id'])
        originals=[record for record in self.store.records('decision') if record['payload']['modelId']==original]
        self.assertEqual(len(originals),1);self.assertEqual(originals[0]['payload']['protocolId'],first['id'])

    def test_publication_interruptions_resume_every_stage(self):
        for kind in ('trial-start','model','trial-result','research-result'):
            store=A.Store(self.root/kind,source='synthetic',retention={'policy':'source-terms'},create=True);data=packet()
            with patch.object(A,'now',return_value=clock_after(data)):
                protocol=A.freeze(store,data,allow_synthetic=True);original=store.append;raised=[]
                def interrupted(current,payload):
                    result=original(current,payload)
                    if current==kind and not raised:
                        raised.append(True);raise OSError('synthetic-interruption')
                    return result
                with patch.object(store,'append',side_effect=interrupted):
                    with self.assertRaises(OSError):A.run_research(store,protocol['id'])
                result=A.run_research(store,protocol['id']);self.assertEqual(len(store.records('trial-result')),3)
                self.assertEqual(A.run_research(store,protocol['id']),result);self.assertEqual(V.semantic_replay(store)['status'],'matched')

    def test_restore_interruption_retry_and_source_retention_gate(self):
        self.frozen();snap=V.snapshot(self.store,self.root/'snapshots');path=self.root/'snapshots'/(snap['sha256']+'.json')
        original=A.write_once;raised=[]
        def interrupted(target,raw):
            original(target,raw)
            if target.name.startswith('event-') and not raised:
                raised.append(True);raise OSError('synthetic-interruption')
        with patch.object(A,'write_once',side_effect=interrupted):
            with self.assertRaises(OSError):V.restore(path,self.root/'restore',source='synthetic',retention={'policy':'source-terms'})
        self.assertEqual(V.restore(path,self.root/'restore',source='synthetic',retention={'policy':'source-terms'})['status'],'restored-and-replayed')
        with self.assertRaises(ValueError):
            A.Store(self.root/'expired',source='fmp-eod',retention={'policy':A.R.FMP_POLICY,'subscriptionStatus':'expired','terminationDate':None},create=True)
        self.assertFalse((self.root/'expired').exists())

    def test_accepted_maximum_cost_has_double_cost_evaluation(self):
        data=packet()
        with patch.object(A,'now',return_value=clock_after(data)):
            protocol=A.freeze(self.store,data,policy={**E.POLICY,'costBps':500.},allow_synthetic=True)
            self.assertIn(A.run_research(self.store,protocol['id'])['payload']['status'],('rejected','insufficient-evidence'))
        different=A.Store(self.root/'high-cost',source='synthetic',retention={'policy':'source-terms'},create=True)
        with self.assertRaisesRegex(E.EconomicError,'double-cost-bound'):
            A.freeze(different,data,policy={**E.POLICY,'costBps':501.},allow_synthetic=True)

    def test_lifetime_boundary_and_recovery_ignore_missing_live_inputs(self):
        for count in range(420,425):
            data=packet(count)
            with patch.object(A,'now',return_value=clock_after(data)):
                protocol=A.freeze(self.store,data,allow_synthetic=True)
        self.assertEqual(protocol['payload']['comparisonCount'],270)
        with self.assertRaisesRegex(E.EconomicError,'budget-exhausted'):
            A.freeze(self.store,packet(425),allow_synthetic=True)
        self.assertEqual(len(self.store.records('protocol')),5)
        # A separate complete graph avoids incomplete protocols in replay.
        complete=A.Store(self.root/'complete',source='synthetic',retention={'policy':'source-terms'},create=True);data=packet()
        with patch.object(A,'now',return_value=clock_after(data)):
            frozen=A.freeze(complete,data,allow_synthetic=True);A.run_research(complete,frozen['id'])
        snap=V.snapshot(complete,self.root/'snapshots')
        config={'version':A.VERSION,'source':'synthetic','retention':{'policy':'source-terms'},'storeRoot':str(complete.root),
                'packetPath':str(self.root/'gone-input.json'),'collectorVersionPath':None,'collectorRoot':str(self.root/'gone-collector'),
                'legacyRoot':None,'profitRoot':None}
        path=self.root/'config.json';path.write_bytes(S.canonical(config))
        self.assertEqual(C.coordinate(path,mode='replay')['status'],'matched')
        self.assertEqual(C.coordinate(path,mode='restore',snapshot_path=self.root/'snapshots'/(snap['sha256']+'.json'),restore_root=self.root/'isolated')['status'],'restored-and-replayed')

    def test_complete_external_graph_snapshot_restore_and_orphan_rejection(self):
        from money_maker_3000 import feed_collection as F, research_cycle as R
        from test_profit_hypothesis import config as profit_config
        policy={'source':'etoro','status':'approved','retentionAllowed':True,'researchAllowed':True,
                'evidence':'synthetic-mechanics-rights-fixture','authorizationBasis':'customer-attestation',
                'activeCustomer':True,'providerDeletionRequested':False}
        data=packet();data.update(source='etoro',retention=policy)
        graph=A.Store(self.root/'graph',source='etoro',retention=policy,create=True)
        with graph.locked():graph.append('input',{'packet':data,'sha256':S.digest(data)})
        mapping={'symbol':'SPY','providerSymbol':'SPY','instrumentId':3000,'displayName':'State Street SPDR S&P 500 ETF',
                 'instrumentType':'ETF','exchange':'NYSE','currency':'USD','currencyVerification':'expected-listing-currency-not-returned-by-api',
                 'identityStatus':'symbol-name-etf-exchange-matched-currency-pending'}
        meaning={'source':'etoro','currency':'USD','currencyVerified':True,'priceBasis':'unknown','sessionConvention':'unknown',
                 'currencyEvidence':'synthetic-mechanics-only','interpretationEvidence':'synthetic-mechanics-only','instrumentMapping':mapping}
        observations=[{'date':row['date'],'timestamp':row['start'],'open':row['open'],'high':row['high'],'low':row['low'],
                       'close':row['close'],'volume':row['volume']} for row in data['observations']]
        collector=self.root/'collector';F.persist_version(collector,symbol='SPY',observations=observations,interpretation=meaning,retention=policy,retrieved_at=A.now())
        history=A.bars(data);manifest={'version':'learning-dataset.v1','symbol':'SPY','source':'etoro','classification':'synthetic',
                    'sourceEvidence':'synthetic-fixture','licenseEvidence':'synthetic-fixture','rightsEvidence':'synthetic-fixture',
                    'attribution':'synthetic-fixture','priceBasis':'unadjusted','sha256':S.digest(data),'rowCount':len(history)}
        interpretation={'instrumentVerified':True,'trainingPermitted':True,'currency':'USD','session':'synthetic-session',
                        'timestampMeaning':'synthetic-close','priceType':'synthetic','adjustments':'unadjusted','costs':'diagnostic-only','evidence':'synthetic-mechanics-only'}
        legacy=R.EvidenceStore(self.root/'legacy');protocol=R.freeze_protocol(history,manifest,'volatility-band-accumulator',policy,interpretation)
        R.run_experiment(legacy,history,protocol)
        config=profit_config();config['instrument']['source']='etoro'
        for interval in config['datasets'].values():
            for row in interval:row['provenance']['source']='etoro'
        report=P.run(config,evidence_root=self.root/'profit',allow_synthetic_smoke=True)
        config_path=self.root/'profit-config.json';config_path.write_bytes(S.canonical(config))
        snapshot=V.snapshot(graph,self.root/'snapshots',collector_root=collector,legacy_root=legacy.root,
                            profit_root={'root':str(self.root/'profit'/report['sha256']),'configPath':str(config_path)})
        restored=V.restore(self.root/'snapshots'/(snapshot['sha256']+'.json'),self.root/'restored-external',source='etoro',retention=policy)
        self.assertEqual(restored['status'],'restored-and-replayed')
        self.assertTrue((self.root/'restored-external/collector/.collector.lock').exists())
        self.assertTrue((self.root/'restored-external/legacy/.source.json').exists())
        self.assertTrue((self.root/'restored-external/legacy/.lock').exists())
        self.assertTrue((self.root/'restored-external/profit'/report['sha256']/'.lock').exists())
        version=next(path for path in collector.glob('SPY-*.json') if '-retrieval-' not in path.name)
        version.unlink()
        with self.assertRaisesRegex(E.EconomicError,'orphaned'):
            V.snapshot(graph,self.root/'orphan',collector_root=collector)

    def test_concurrent_journal_retries_publish_one_exact_record(self):
        from concurrent.futures import ThreadPoolExecutor
        data=packet(1);payload={'packet':data,'sha256':S.digest(data)}
        def worker(_):
            store=A.Store(self.store.root,source='synthetic',retention={'policy':'source-terms'},create=False)
            with store.locked():return store.append('input',payload)['id']
        with ThreadPoolExecutor(max_workers=4) as executor:identities=list(executor.map(worker,range(8)))
        self.assertEqual(len(set(identities)),1);self.assertEqual(len(self.store.records()),1)

    def test_real_publication_hardlink_crash_recovers_without_read_mutation(self):
        original=A.os.unlink;raised=[]
        def crash(path,*args,**kwargs):
            if Path(path).name.startswith('.pending-') and not raised:
                raised.append(True);raise OSError('synthetic-link-window-crash')
            return original(path,*args,**kwargs)
        payload={'packet':packet(1),'sha256':S.digest(packet(1))}
        with patch.object(A.os,'unlink',side_effect=crash):
            with self.assertRaises(OSError):
                with self.store.locked():self.store.append('input',payload)
        canonical=next(self.store.root.glob('event-*.json'));before=canonical.read_bytes()
        self.assertEqual(canonical.stat().st_nlink,2)
        self.assertEqual(len(self.store.records()),1);self.assertEqual(canonical.stat().st_nlink,2)
        with self.store.locked():record=self.store.append('input',payload)
        self.assertEqual(canonical.stat().st_nlink,1);self.assertEqual(canonical.read_bytes(),before)
        self.assertEqual(self.store.records()[0]['id'],record['id'])
        # Unknown extra links have no producer orphan identity and remain blocked.
        A.os.link(canonical,self.store.root/'unrecognized-link')
        with self.assertRaisesRegex(E.EconomicError,'orphan-identity'):
            self.store.records()
        (self.store.root/'unrecognized-link').unlink()
        raised.clear();source_root=self.root/'source-crash'
        with patch.object(A.os,'unlink',side_effect=crash):
            with self.assertRaises(OSError):A.Store(source_root,source='synthetic',retention={'policy':'source-terms'},create=True)
        self.assertEqual((source_root/'.source.json').stat().st_nlink,2)
        A.Store(source_root,source='synthetic',retention={'policy':'source-terms'},create=False)
        self.assertEqual((source_root/'.source.json').stat().st_nlink,2)
        A.Store(source_root,source='synthetic',retention={'policy':'source-terms'},create=True)
        self.assertEqual((source_root/'.source.json').stat().st_nlink,1)
        # The same interrupted publication is recoverable for restore markers.
        snap=V.snapshot(self.store,self.root/'snapshots');raised.clear();path=self.root/'snapshots'/(snap['sha256']+'.json')
        with patch.object(A.os,'unlink',side_effect=crash):
            with self.assertRaises(OSError):V.restore(path,self.root/'restore-link',source='synthetic',retention={'policy':'source-terms'})
        result=V.restore(path,self.root/'restore-link',source='synthetic',retention={'policy':'source-terms'})
        self.assertEqual(result['status'],'restored-and-replayed')

    def test_concurrent_identical_artifact_cleanup_is_idempotent(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event, current_thread
        destination=self.root/'shared-artifact.json';raw=S.canonical({'version':'synthetic-artifact.v1'})
        blocked=Event();resume=Event();original=A.os.unlink
        def coordinated(path,*args,**kwargs):
            if Path(path).name.startswith('.pending-') and current_thread().name=='first-publisher':
                blocked.set();self.assertTrue(resume.wait(10))
            return original(path,*args,**kwargs)
        def first():
            current_thread().name='first-publisher';A.write_once(destination,raw)
        with patch.object(A.os,'unlink',side_effect=coordinated),ThreadPoolExecutor(max_workers=2) as executor:
            pending=executor.submit(first);self.assertTrue(blocked.wait(10))
            other=executor.submit(A.write_once,destination,raw);other.result(timeout=10);resume.set();pending.result(timeout=10)
        self.assertEqual(destination.read_bytes(),raw);self.assertEqual(destination.stat().st_nlink,1)
        self.assertFalse(list(self.root.glob('.pending-*')))

    def test_semantic_tamper_with_rehashed_chain_rejected(self):
        self.frozen();records=self.store.records();trial=next(record for record in records if record['kind']=='trial-result')
        trial['payload']['development']['candidate']['finalCash']+=100
        previous=None
        for record in records:
            record['previous']=previous;record['id']=S.digest({key:value for key,value in record.items() if key!='id'});previous=record['id']
        with self.assertRaisesRegex(E.EconomicError,'semantic|economic|schema|parity'):
            V._replay(records)

if __name__=='__main__':unittest.main()
