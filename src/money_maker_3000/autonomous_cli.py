"""Bounded coordinator; explicitly configured collection stays in its separate module."""
from pathlib import Path

from . import autonomous_research as A, autonomous_recovery as V, shadow_portfolio as H
from . import learning as L, signal_toolkit as S
from .economic_portfolio import require, EconomicError


def source_native_packet(path, *, retention):
    """Projection preserves unknown candle boundaries rather than guessing sessions."""
    from . import feed_collection as F
    F.check_retention(retention, A.now())
    value = L._json(L._read(path,32*1024*1024))
    require(set(value)=={'schemaVersion','symbol','source','observations','interpretation','retention','unresolvedMissingDates'}
            and value['schemaVersion']=='market-observations.v2' and value['source']=='etoro', 'autonomous-invalid-collector-version')
    F.check_retention(value['retention'], A.now()); require(value['retention']==retention, 'autonomous-source-retention-conflict')
    F.validate_interpretation(value['interpretation'],value['symbol']); rows=F.validate_observations(value['observations'])
    meaning=value['interpretation']; currency=meaning['currency']
    # Collector timestamps declare fromDate only. They do not prove exchange
    # close/start, genuine publication availability or an adjustment convention.
    interpretation={'identity':value['symbol']+' ETF', 'currency':currency, 'sessionConvention':meaning['sessionConvention'],
                    'timestampMeaning':'unknown', 'adjustmentBasis':'unknown', 'priceBasis':meaning['priceBasis'],
                    'evidence':{'identity':{'status':'verified','reference':meaning['interpretationEvidence']},
                                'currency':{'status':'verified','reference':meaning['currencyEvidence']}}}
    return {'version':A.VERSION,'source':'etoro','symbol':value['symbol'],'classification':'observed-attested',
            'retention':retention,'interpretation':interpretation,
            'observations':[{'date':row['date'],'start':None,'end':None,'availableAt':None,'open':row['open'],
                             'high':row['high'],'low':row['low'],'close':row['close'],'volume':row['volume']}
                            for row in rows if row['date'] not in value['unresolvedMissingDates']]}


def coordinate(config_path=None, *, mode='cycle', allow_synthetic=False, snapshot_path=None, restore_root=None):
    require(mode in ('cycle','status','replay','snapshot','restore'), 'autonomous-invalid-command')
    if config_path is None:
        return {'version':A.VERSION,'softwareHealth':'healthy','feedFreshness':'unavailable','researchEligibility':'ineligible',
                'eligibilityReasons':['no-data','no-private-configuration'],'economicEvidence':'not-evaluated','status':'no-data','boundary':A.BOUNDARY}
    config=L._json(L._read(config_path,1024*1024))
    required={'version','source','retention','storeRoot','packetPath','collectorVersionPath','collectorRoot','legacyRoot','profitRoot'}
    require(required <= set(config) <= required | {'collection','symbol'}, 'autonomous-invalid-config')
    require(config['version']==A.VERSION and config['source'] in ('etoro','fmp-eod','synthetic'), 'autonomous-invalid-config')
    A.R.retention_check(config['source'],config['retention'])
    if mode=='restore':
        require(snapshot_path is not None and restore_root is not None, 'autonomous-restore-arguments-required')
        return V.restore(snapshot_path,restore_root,source=config['source'],retention=config['retention'])
    if mode=='replay':
        store=A.Store(config['storeRoot'],source=config['source'],retention=config['retention'],create=False)
        return V.semantic_replay(store)
    require(not (config['packetPath'] and config['collectorVersionPath']), 'autonomous-ambiguous-input')
    collection_report={'status':'not-configured'}
    if mode=='cycle' and config.get('collection') is not None:
        from .research_cycle_cli import refresh_collection
        require(config['source']=='etoro' and config['collection']['retention']==config['retention']
                and config['collection']['outputRoot']==config['collectorRoot'], 'autonomous-collector-policy-mismatch')
        collection_report=refresh_collection(config['collection'])
    packet=None; retrieval=None
    if config['collectorRoot'] and not config['packetPath']:
        version_path,retrieval=latest_collector(config['collectorRoot'], config.get('symbol','SPY'))
        if version_path is not None:
            packet=source_native_packet(version_path,retention=config['retention'])
    if config['packetPath']:
        packet=L._json(L._read(config['packetPath'],32*1024*1024))
    elif config['collectorVersionPath'] and packet is None:
        require(config['source']=='etoro', 'autonomous-collector-source-mismatch')
        packet=source_native_packet(config['collectorVersionPath'],retention=config['retention'])
    if packet is not None:
        A.validate_packet(packet,allow_synthetic=allow_synthetic)
        require(packet['source']==config['source'] and packet['retention']==config['retention'], 'autonomous-input-policy-mismatch')
    root=Path(config['storeRoot'])
    if mode=='restore':
        require(snapshot_path is not None and restore_root is not None, 'autonomous-restore-arguments-required')
        return V.restore(snapshot_path,restore_root,source=config['source'],retention=config['retention'])
    if not root.exists() and mode != 'cycle':
        result=A.BOUNDARY.copy()
        return {'version':A.VERSION,'softwareHealth':'uninitialized','feedFreshness':'unavailable',
                'researchEligibility':'ineligible','eligibilityReasons':['no-evidence-store'],
                'economicEvidence':'not-evaluated','status':'no-data','boundary':result}
    store=A.Store(root,source=config['source'],retention=config['retention'],create=mode=='cycle')
    if mode=='replay':
        return V.semantic_replay(store)
    if mode=='snapshot':
        require(snapshot_path is not None,'autonomous-snapshot-destination-required')
        return V.snapshot(store,snapshot_path,collector_root=config['collectorRoot'],legacy_root=config['legacyRoot'],profit_root=config['profitRoot'])
    if mode=='status':
        return A.status(store,packet)
    if packet is None:
        result=A.status(store);result['status']='no-data';return result
    reasons=A.validate_packet(packet,allow_synthetic=allow_synthetic)
    with store.locked():
        input_record=store.append('input',{'packet':packet,'sha256':S.digest(packet)})
    if retrieval is not None:
        H.availability(store,sorted(retrieval['unresolvedMissingDates']),available=False)
        H.availability(store,sorted(retrieval['restoredDates']),available=True)
    if reasons:
        result=A.status(store,packet);result.update({'status':'input-ineligible','sourceNativeObservations':len(packet['observations']),
                            'inputEvidenceId':input_record['id'],'observedEconomicResearch':'not-run-unqualified-input','collection':collection_report});return result
    V.semantic_replay(store)
    protocols=store.records('protocol')
    if protocols:
        protocol=protocols[-1]
        require(protocol['payload']['symbol']==packet['symbol'], 'autonomous-separate-currency-book-required')
    else:
        try:
            protocol=A.freeze(store,packet,allow_synthetic=allow_synthetic)
        except EconomicError as exc:
            if str(exc)!='autonomous-insufficient-development-history':
                raise
            result=A.status(store,packet);result.update({'status':'insufficient-evidence','economicEvidence':'insufficient-development-history'});return result
    research=A.run_research(store,protocol['id'])
    model_id=research['payload']['selectedModelId']
    previous=[record for record in store.records('score') if record['payload']['modelId']==model_id] if model_id else []
    finished=bool(previous and len(previous[-1]['payload'].get('confirmationWindowDates',[]))==105)
    known_end=protocol['payload']['knownHistoryEnd']
    if finished and sum(row['date']>known_end for row in packet['observations']) >= 165:
        protocol=A.freeze(store,packet,allow_synthetic=allow_synthetic)
        research=A.run_research(store,protocol['id'])
    shadow=H.record_decisions(store,packet,protocol['id'])
    scores=H.score_later(store,packet)
    checkpoints=[]
    identities=sorted({record['payload']['modelId'] for record in store.records('score')})
    for identity in identities:
        checkpoints.append(H.checkpoint(store,identity)['id'])
    result=A.status(store,packet);result.update({'status':'cycle-complete','protocolId':protocol['id'],'researchResultId':research['id'],
                        'shadow':shadow,'scoring':scores,'checkpointIds':checkpoints,'collection':collection_report})
    return result


def latest_collector(root, symbol):
    from . import feed_collection as F
    require(symbol in ('SPY','QQQ','VAS'), 'autonomous-invalid-collector-symbol')
    root=A.private_directory(root)
    with F._store_lock(root):
        reports=sorted(root.glob(symbol+'-retrieval-*.json'))
        if not reports:
            return None,None
        report=L._json(L._read(reports[-1],2*1024*1024))
        require(report.get('schemaVersion')=='market-retrieval.v2' and report.get('symbol')==symbol
                and report.get('source')=='etoro' and L._hash(report.get('version')), 'autonomous-invalid-retrieval')
        path=root/(symbol+'-'+report['version']+'.json')
        raw=L._read(path,32*1024*1024)
        import hashlib
        require(hashlib.sha256(raw).hexdigest()==report['version'], 'autonomous-retrieval-version-mismatch')
        return path,report
