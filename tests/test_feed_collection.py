"""Synthetic provider-contract tests; no observed prices or credentials."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from money_maker_3000.feed_collection import (
    CollectionError, EtoroReader, check_retention, collect, default_retention,
    parse_candles, persist_version, resolve_instrument, validate_observations, main, preflight_collection,
)

NOW = '2026-09-14T06:00:00Z'
POLICY = {'source':'etoro', 'status':'approved', 'retentionAllowed':True,
          'researchAllowed':True, 'evidence':'synthetic-test-rights-only',
          'expiresAt':'2026-12-31T00:00:00Z', 'writtenModelUseException':True}
MAPPING = {'symbol':'SPY','providerSymbol':'SPY','instrumentId':1,
           'displayName':'SPDR S&P 500','instrumentType':'ETF','exchange':'NYSE',
           'currency':'USD','currencyVerification':'expected-listing-currency-not-returned-by-api',
           'identityStatus':'symbol-name-etf-exchange-matched-currency-pending'}
MEANING = {'source':'etoro', 'currency':'USD', 'currencyVerified':True,
           'priceBasis':'synthetic-test', 'sessionConvention':'synthetic-test',
           'currencyEvidence':'synthetic-test-only', 'interpretationEvidence':'synthetic-test-only',
           'instrumentMapping':MAPPING}


def candle(day='2026-09-10', value=100, **extra):
    return dict(instrumentID=1, fromDate=day+'T00:00:00Z', open=value, high=value+1,
                low=value-1, close=value, **extra)


def payload(*candles):
    return {'interval':'OneDay','candles':[{'instrumentId':1,'candles':list(candles)}]}


def rows():
    return parse_candles(payload(candle()), 1, NOW)['observations']


class FakeReader:
    def __init__(self, failures=None):
        self.failures = failures or {}
        self.calls = []

    def get(self, path, query=None):
        self.calls.append((path,query))
        if query:
            ticker = query['internalSymbolFull']
            if ticker in self.failures:
                raise CollectionError(self.failures[ticker])
            if ticker == 'VAS.ASX':
                return {'items': []}
            return {'items':[{'instrumentId':1, 'internalSymbolFull':ticker,
                             'displayname':'SPDR S&P 500' if ticker=='SPY' else 'Invesco QQQ',
                             'instrumentType':'ETF', 'internalExchangeName':'NYSE' if ticker=='SPY' else 'NASDAQ'}]}
        return payload(candle())


class FeedCollectionTests(unittest.TestCase):
    def test_missing_unused_fields_are_explicit_null(self):
        row = rows()[0]
        self.assertIsNone(row['volume'])
        row['open'] = row['low'] = row['high'] = None
        self.assertEqual(validate_observations([row]), (row,))
        with self.assertRaisesRegex(CollectionError,'required-observation-field-missing'):
            validate_observations([row], required_fields=('volume',))

    def test_missing_close_rejected(self):
        row=rows()[0]; row['close']=None
        with self.assertRaises(CollectionError): validate_observations([row])

    def test_nonfinite_boolean_negative_and_bad_range_rejected(self):
        for field,value in [('close',float('nan')),('volume',True),('volume',-1),('high',50),('low',101)]:
            row=rows()[0]; row[field]=value
            with self.subTest(field=field,value=value), self.assertRaises(CollectionError):
                validate_observations([row])

    def test_unknown_fields_and_duplicate_dates_rejected(self):
        row=rows()[0]
        with self.assertRaises(CollectionError): validate_observations([row,row])
        row['account']='forbidden'
        with self.assertRaises(CollectionError): validate_observations([row])

    def test_incomplete_candles_excluded(self):
        parsed=parse_candles(payload(candle('2026-09-13'),candle('2026-09-14')),1,NOW)
        self.assertEqual(parsed['unfinished'],1)
        self.assertEqual(len(parsed['observations']),1)

    def test_exact_26_hour_completion_and_offset_timestamp_are_normalized(self):
        parsed = parse_candles(payload(candle('2026-09-10')) | {
            'candles': [{'instrumentId': 1, 'candles': [
                {**candle('2026-09-10'), 'fromDate': '2026-09-10T23:00:00-01:00'}
            ]}]
        }, 1, '2026-09-12T02:00:00Z')
        self.assertEqual(parsed['unfinished'], 0)
        self.assertEqual(parsed['observations'][0]['date'], '2026-09-11')
        self.assertEqual(parsed['observations'][0]['timestamp'], '2026-09-11T00:00:00Z')

    def test_extreme_timestamp_completion_does_not_overflow(self):
        parsed = parse_candles(payload({**candle('0001-01-01'), 'fromDate': '0001-01-01T00:00:00Z'}),
                               1, '9999-12-31T23:59:59Z')
        self.assertEqual(parsed['unfinished'], 0)
        self.assertEqual(parsed['observations'][0]['date'], '0001-01-01')

    def test_duplicates_deduplicated_conflicts_fail(self):
        self.assertEqual(parse_candles(payload(candle(),candle()),1,NOW)['duplicates'],1)
        with self.assertRaisesRegex(CollectionError,'conflicting-candles'):
            parse_candles(payload(candle(),candle(value=101)),1,NOW)

    def test_mismatch_and_interval_fail(self):
        for mutation in ('id','interval'):
            p=payload(candle())
            if mutation=='id':p['candles'][0]['instrumentId']=2
            else:p['interval']='OneMinute'
            with self.assertRaises(CollectionError): parse_candles(p,1,NOW)

    def test_boolean_instrument_ids_rejected(self):
        p = payload(candle())
        p['candles'][0]['instrumentId'] = True
        with self.assertRaisesRegex(CollectionError, 'candle-instrument-mismatch'):
            parse_candles(p, 1, NOW)
        p = payload(candle())
        p['candles'][0]['candles'][0]['instrumentID'] = True
        with self.assertRaisesRegex(CollectionError, 'candle-instrument-mismatch'):
            parse_candles(p, 1, NOW)
        with self.assertRaisesRegex(CollectionError, 'candle-instrument-mismatch'):
            parse_candles(payload(candle()), True, NOW)

    def test_missing_weekdays_not_asserted_exchange_sessions(self):
        parsed=parse_candles(payload(candle('2026-09-07'),candle('2026-09-10')),1,NOW)
        self.assertEqual(parsed['potentialMissingWeekdays'],['2026-09-08','2026-09-09'])
        self.assertIn('not-verified',parsed['missingMeaning'])

    def test_naive_timestamp_rejected(self):
        p=payload(candle());p['candles'][0]['candles'][0]['fromDate']='2026-09-10T00:00:00'
        with self.assertRaises(CollectionError):parse_candles(p,1,NOW)

    def test_retention_fails_closed_and_expiry(self):
        for policy in [default_retention(),{**POLICY,'expiresAt':NOW},
                       {**POLICY,'writtenModelUseException':False},
                       {**POLICY,'providerDeletionRequested':True},
                       {**POLICY,'researchAllowed':False}]:
            with self.subTest(policy=policy),self.assertRaises(CollectionError):check_retention(policy,NOW)

    def test_partial_instrument_availability_continues(self):
        reader=FakeReader({'SPY':'instrument-unavailable'})
        result=collect(reader,retrieved_at=NOW,probe_only=True)
        self.assertEqual(result['instruments'][1]['status'],'identity-only')
        self.assertEqual(len(reader.calls),3)
        self.assertFalse(result['instruments'][1]['pricesRetained'])

    def test_authentication_forbidden_and_rate_stop(self):
        for reason in ['authentication-failed','http-forbidden-cause-unresolved','rate-limit-stop']:
            reader=FakeReader({'SPY':reason})
            result=collect(reader,retrieved_at=NOW,probe_only=True)
            self.assertEqual(len(reader.calls),1)
            self.assertEqual(result['instruments'][1]['reason'],'collection-stopped')

    def test_unapproved_collection_never_requests_prices(self):
        reader=FakeReader()
        result=collect(reader,retrieved_at=NOW,symbols=('SPY',))
        self.assertEqual(result['instruments'][0]['reason'],'retention-not-approved')
        self.assertEqual(len(reader.calls),0)

    def test_instrument_currency_is_not_falsely_verified(self):
        mapping=resolve_instrument(FakeReader(),'SPY')
        self.assertIn('not-returned',mapping['currencyVerification'])

    def test_versions_replay_and_revision_preserve_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            first=persist_version(root,symbol='SPY',observations=rows(),interpretation=MEANING,retention=POLICY,retrieved_at=NOW)
            again=persist_version(root,symbol='SPY',observations=rows(),interpretation=MEANING,retention=POLICY,retrieved_at=NOW)
            self.assertEqual(first,again)
            later=persist_version(root,symbol='SPY',observations=rows(),interpretation=MEANING,retention=POLICY,retrieved_at='2026-09-14T07:00:00Z')
            self.assertEqual(first['version'],later['version'])
            self.assertEqual(later['added'],0)
            revised=rows();revised[0]['close']=100.5
            changed=persist_version(root,symbol='SPY',observations=revised,interpretation=MEANING,retention=POLICY,retrieved_at='2026-09-15T07:00:00Z')
            self.assertEqual(changed['revisedDates'],['2026-09-10'])
            old=json.loads((root/('SPY-'+first['version']+'.json')).read_text())
            self.assertEqual(old['observations'][0]['close'],100)

    def test_persist_extreme_timestamp_does_not_overflow_completion_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            observation=rows()[0]
            observation.update({'date':'9999-12-31','timestamp':'9999-12-31T23:00:00Z'})
            with self.assertRaisesRegex(CollectionError,'unfinished-observation'):
                persist_version(Path(tmp),symbol='SPY',observations=[observation],interpretation=MEANING,
                                retention={**POLICY,'expiresAt':'9999-12-31T23:59:59Z'},
                                retrieved_at='9999-12-31T23:59:58Z')

    def test_source_mismatch_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CollectionError,'source-or-symbol'):
                persist_version(Path(tmp),symbol='SPY',observations=rows(),interpretation={**MEANING,'source':'fmp'},retention=POLICY,retrieved_at=NOW)
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_corruption_prevents_new_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            first=persist_version(root,symbol='SPY',observations=rows(),interpretation=MEANING,retention=POLICY,retrieved_at=NOW)
            path=root/('SPY-'+first['version']+'.json');path.write_text('{}')
            with self.assertRaisesRegex(CollectionError,'dataset-integrity'):
                persist_version(root,symbol='SPY',observations=rows(),interpretation=MEANING,retention=POLICY,retrieved_at='2026-09-15T06:00:00Z')

    def test_symlink_store_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'link';root.symlink_to(tmp,target_is_directory=True)
            with self.assertRaises(CollectionError):
                persist_version(root,symbol='SPY',observations=rows(),interpretation=MEANING,retention=POLICY,retrieved_at=NOW)

    def test_interrupted_publication_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('money_maker_3000.feed_collection.os.replace',side_effect=OSError('interruption')):
                with self.assertRaises(OSError):persist_version(root,symbol='SPY',observations=rows(),interpretation=MEANING,retention=POLICY,retrieved_at=NOW)
            self.assertEqual(list(root.glob('*.json')),[])
            self.assertEqual(persist_version(root,symbol='SPY',observations=rows(),interpretation=MEANING,retention=POLICY,retrieved_at=NOW)['added'],1)

    def test_allowlist_and_private_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profile'
            path.write_text('ETORO_API_KEY=synthetic\nETORO_USER_KEY=synthetic\n');path.chmod(0o600)
            reader=EtoroReader(path)
            with self.assertRaisesRegex(CollectionError,'endpoint-not-allowlisted'):reader.get('/trading/info/portfolio')
            self.assertEqual(reader.request_count,0)
            path.chmod(0o644)
            with self.assertRaisesRegex(CollectionError,'private-file-permissions'):EtoroReader(path)

    def test_http_error_body_not_exposed_auth_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profile';path.write_text('ETORO_API_KEY=synthetic\nETORO_USER_KEY=synthetic\n');path.chmod(0o600)
            reader=EtoroReader(path)
            with patch.object(reader._opener,'open',side_effect=urllib.error.HTTPError('private',401,'secret-body',{},None)):
                with self.assertRaisesRegex(CollectionError,'^authentication-failed$'):reader.get('/market-data/search')
            with self.assertRaisesRegex(CollectionError,'collection-stopped'):reader.get('/market-data/search')

    def test_forbidden_signature_is_classified_without_exposing_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profile';path.write_text('ETORO_API_KEY=synthetic\nETORO_USER_KEY=synthetic\n');path.chmod(0o600)
            reader=EtoroReader(path)
            body=json.dumps({'title':'Error 1010: Access denied',
                             'detail':"The site owner has blocked access based on your browser's signature."}).encode()
            error=urllib.error.HTTPError('synthetic',403,'Forbidden',{},io.BytesIO(body))
            with patch.object(reader._opener,'open',side_effect=error):
                with self.assertRaisesRegex(CollectionError,'^cloudflare-browser-signature-block$'):
                    reader.get('/market-data/search')
            self.assertTrue(reader._stopped)

    def test_other_forbidden_envelopes_are_unresolved_and_stop(self):
        cases = [
            b'{"title":"other","detail":"different"}',
            b'{not-json',
            b'{"title":"Error 1010: Access denied","title":"duplicate","detail":"The site owner has blocked access based on your browser\'s signature."}',
            b'{"title":"Error 1010: Access denied","detail":"The site owner has blocked access based on your browser\'s signature.","extra":"synthetic"}',
            b'x' * (16 * 1024 + 1),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profile';path.write_text('ETORO_API_KEY=synthetic\nETORO_USER_KEY=synthetic\n');path.chmod(0o600)
            for body in cases:
                with self.subTest(body_class=len(body)):
                    reader=EtoroReader(path)
                    error=urllib.error.HTTPError('synthetic',403,'Forbidden',{},io.BytesIO(body))
                    with patch.object(reader._opener,'open',side_effect=error):
                        with self.assertRaisesRegex(CollectionError,'^http-forbidden-cause-unresolved$'):
                            reader.get('/market-data/search')
                    with self.assertRaisesRegex(CollectionError,'collection-stopped'):
                        reader.get('/market-data/search')

    def test_forbidden_body_read_error_is_unresolved_and_stops(self):
        class ReadFailingHTTPError(urllib.error.HTTPError):
            def read(self, amount=None):
                raise OSError('synthetic-read-failure')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profile';path.write_text('ETORO_API_KEY=synthetic\nETORO_USER_KEY=synthetic\n');path.chmod(0o600)
            reader=EtoroReader(path)
            error=ReadFailingHTTPError('synthetic',403,'Forbidden',{},io.BytesIO())
            with patch.object(reader._opener,'open',side_effect=error):
                with self.assertRaisesRegex(CollectionError,'^http-forbidden-cause-unresolved$'):
                    reader.get('/market-data/search')
            with self.assertRaisesRegex(CollectionError,'collection-stopped'):
                reader.get('/market-data/search')

    def test_http_close_error_still_returns_controlled_classification(self):
        class CloseFailingHTTPError(urllib.error.HTTPError):
            def close(self):
                raise OSError('synthetic-close-failure')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profile';path.write_text('ETORO_API_KEY=synthetic\nETORO_USER_KEY=synthetic\n');path.chmod(0o600)
            reader=EtoroReader(path)
            error=CloseFailingHTTPError('synthetic',403,'Forbidden',{},io.BytesIO(b'{}'))
            with patch.object(reader._opener,'open',side_effect=error):
                with self.assertRaisesRegex(CollectionError,'^http-forbidden-cause-unresolved$'):
                    reader.get('/market-data/search')

    def test_other_http_status_codes_remain_separate(self):
        cases=[(401,'authentication-failed',True),(404,'instrument-unavailable',False),(429,'rate-limit-stop',True)]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profile';path.write_text('ETORO_API_KEY=synthetic\nETORO_USER_KEY=synthetic\n');path.chmod(0o600)
            for status, expected, stopped in cases:
                with self.subTest(status=status):
                    reader=EtoroReader(path)
                    error=urllib.error.HTTPError('synthetic',status,'synthetic',{},io.BytesIO(b'not-inspected'))
                    with patch.object(reader._opener,'open',side_effect=error):
                        with self.assertRaisesRegex(CollectionError,'^'+expected+'$'):
                            reader.get('/market-data/search')
                    self.assertEqual(reader._stopped,stopped)

    def test_profile_key_families_map_to_correct_headers_without_rewrite(self):
        profiles = (
            'ETORO_API_KEY=synthetic-public\nETORO_USER_KEY=synthetic-user\n',
            '# Existing legacy profile\nexport ETORO_AGENT_PUBLIC_KEY="synthetic-public"\n'
            "ETORO_AGENT_PRIVAT_KEY='synthetic-user'\nUNRELATED=ignored\n",
        )
        for profile in profiles:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'profile';path.write_text(profile);path.chmod(0o600)
                reader=EtoroReader(path)
                with patch.object(reader._opener,'open',side_effect=urllib.error.HTTPError('synthetic',401,'',{},None)) as opened:
                    with self.assertRaisesRegex(CollectionError,'^authentication-failed$'):
                        reader.get('/market-data/search')
                headers=dict(opened.call_args.args[0].header_items())
                self.assertEqual(headers['X-api-key'],'synthetic-public')
                self.assertEqual(headers['X-user-key'],'synthetic-user')
                self.assertEqual(path.read_text(),profile)

    def test_reader_sets_support_confirmed_user_agent_with_required_header_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profile';path.write_text('ETORO_API_KEY=synthetic\nETORO_USER_KEY=synthetic\n');path.chmod(0o600)
            reader=EtoroReader(path)
            with patch.object(reader._opener,'open',side_effect=urllib.error.HTTPError('synthetic',401,'',{},None)) as opened:
                with self.assertRaisesRegex(CollectionError,'^authentication-failed$'):
                    reader.get('/market-data/search')
            headers=dict(opened.call_args.args[0].header_items())
            self.assertEqual(headers['User-agent'],'personal-research-client/1.0')
            self.assertTrue({'X-api-key','X-user-key','X-request-id','Accept','User-agent'}.issubset(headers))
            self.assertNotIn('Authorization',headers)
            self.assertEqual(reader.last_request_id,headers['X-request-id'])

    def test_profile_duplicate_missing_and_mixed_families_fail_before_transport(self):
        canonical=['ETORO_API_KEY=synthetic-public','ETORO_USER_KEY=synthetic-user']
        legacy=['ETORO_AGENT_PUBLIC_KEY=synthetic-public','ETORO_AGENT_PRIVAT_KEY=synthetic-user']
        cases=[([], 'missing-fields')]
        for family in (canonical,legacy):
            cases.extend(([line], 'missing-fields') for line in family)
            for line in family:
                cases.extend((family+[extra], 'invalid') for extra in (line,line+'-conflict'))
        for left in canonical:
            for right in legacy:
                cases.append(([left,right], 'ambiguous-fields'))
        cases.extend((canonical+[line], 'ambiguous-fields') for line in legacy)
        cases.extend((legacy+[line], 'ambiguous-fields') for line in canonical)
        cases.append((canonical+legacy, 'ambiguous-fields'))
        cases.append((canonical+[legacy[0]+'-conflict'], 'ambiguous-fields'))
        for lines,reason in cases:
            with self.subTest(lines=lines), tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'profile';path.write_text('\n'.join(lines));path.chmod(0o600)
                with patch('money_maker_3000.feed_collection.urllib.request.build_opener') as transport:
                    with self.assertRaisesRegex(CollectionError,'^credential-profile-'+reason+'$'):
                        EtoroReader(path)
                    transport.assert_not_called()

    def test_foreign_policy_and_bad_meaning_preflight_before_any_get(self):
        for policy, meaning in [({**POLICY, 'source':'fmp'}, MEANING),
                                ({**POLICY, 'writtenModelUseException':False}, MEANING),
                                (POLICY, {**MEANING, 'currency':None}),
                                (POLICY, {**MEANING, 'priceBasis':True}),
                                (POLICY, {**MEANING, 'account':'forbidden'})]:
            reader=FakeReader()
            result=collect(reader,retrieved_at=NOW,symbols=('SPY',),retention=policy,
                           interpretations={'SPY':meaning},output_root=Path('/unused'))
            self.assertEqual(reader.calls,[])
            self.assertEqual(result['instruments'][0]['status'],'unavailable')

    def test_cli_preflight_does_not_construct_credential_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); policy=root/'policy'; meanings=root/'meanings'
            policy.write_text(json.dumps({**POLICY,'source':'fmp'}));policy.chmod(0o600)
            meanings.write_text(json.dumps({'SPY':MEANING}));meanings.chmod(0o600)
            with patch('money_maker_3000.feed_collection.EtoroReader') as reader, patch('builtins.print'):
                code=main(['--profile','/never-read','--policy',str(policy),'--interpretations',str(meanings),'--output-root',str(root/'output')])
            self.assertEqual(code,1);reader.assert_not_called()

    def test_exact_interpretation_schema_rejects_extras_types_and_identity_drift(self):
        malformed=[]
        for key in MEANING:
            value=deepcopy(MEANING);value.pop(key);malformed.append(value)
        for key in ('priceBasis','sessionConvention','currencyEvidence','interpretationEvidence'):
            value=deepcopy(MEANING);value[key]=True;malformed.append(value)
        for key,val in [('exchange',True),('exchange','NASDAQ'),('instrumentId',True),('symbol','QQQ'),
                        ('providerSymbol','QQQ'),('displayName','Unrelated Fund'),('instrumentType','CFD'),('currency','AUD')]:
            value=deepcopy(MEANING);value['instrumentMapping'][key]=val;malformed.append(value)
        for key in ('accountId','portfolio','arbitrary'):
            value=deepcopy(MEANING);value['instrumentMapping'][key]='forbidden';malformed.append(value)
        with tempfile.TemporaryDirectory() as tmp:
            for meaning in malformed:
                with self.subTest(meaning=meaning), self.assertRaises(CollectionError):
                    persist_version(Path(tmp),symbol='SPY',observations=rows(),interpretation=meaning,retention=POLICY,retrieved_at=NOW)
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_live_mapping_drift_stops_before_candles(self):
        reviewed=deepcopy(MEANING);reviewed['instrumentMapping']['instrumentId']=2
        reader=FakeReader()
        result=collect(reader,retrieved_at=NOW,symbols=('SPY',),retention=POLICY,
                       interpretations={'SPY':reviewed},output_root=Path('/unused'))
        self.assertEqual(len(reader.calls),1)
        self.assertEqual(result['instruments'][0]['reason'],'resolved-instrument-differs-from-reviewed-mapping')

    def test_ambiguous_and_boolean_search_instrument_ids_are_rejected(self):
        reader=FakeReader()
        reader.get=lambda path, query=None: {'items':[
            {'instrumentId':1,'internalSymbolFull':'SPY','displayname':'SPDR S&P 500','instrumentType':'ETF','internalExchangeName':'NYSE'},
            {'instrumentId':2,'internalSymbolFull':'SPY','displayname':'SPDR S&P 500','instrumentType':'ETF','internalExchangeName':'NYSE'},
        ]}
        with self.assertRaisesRegex(CollectionError,'ambiguous-instrument'):
            resolve_instrument(reader,'SPY')
        reader.get=lambda path, query=None: {'items':[
            {'instrumentId':True,'internalSymbolFull':'SPY','displayname':'SPDR S&P 500','instrumentType':'ETF','internalExchangeName':'NYSE'},
        ]}
        with self.assertRaisesRegex(CollectionError,'instrument-identity-unverified'):
            resolve_instrument(reader,'SPY')

    def test_process_crash_after_publication_remains_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            pid=os.fork()
            if pid == 0:
                real_replace=os.replace
                def crash_after_publish(source,destination):
                    real_replace(source,destination)
                    os._exit(17)
                with patch('money_maker_3000.feed_collection.os.replace',side_effect=crash_after_publish):
                    persist_version(root,symbol='SPY',observations=rows(),interpretation=MEANING,retention=POLICY,retrieved_at=NOW)
                os._exit(18)
            _,status=os.waitpid(pid,0)
            self.assertEqual(os.waitstatus_to_exitcode(status),17)
            self.assertEqual(len(list(root.glob('SPY-*.json'))),1)
            self.assertEqual(next(root.glob('SPY-*.json')).stat().st_nlink,1)
            replay=persist_version(root,symbol='SPY',observations=rows(),interpretation=MEANING,retention=POLICY,retrieved_at=NOW)
            self.assertEqual(replay['added'],1)
            self.assertEqual(list(root.glob('.pending-*')),[])

    def test_fifo_profile_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fifo';os.mkfifo(path,0o600)
            with self.assertRaisesRegex(CollectionError,'private-file-permissions'):
                EtoroReader(path)

    def test_cli_spy_only_works_without_vas_interpretation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);policy=root/'policy';meanings=root/'meanings'
            policy.write_text(json.dumps(POLICY));policy.chmod(0o600)
            meanings.write_text(json.dumps({'SPY':MEANING}));meanings.chmod(0o600)
            reader=FakeReader()
            with patch('money_maker_3000.feed_collection.EtoroReader',return_value=reader), patch('builtins.print') as output:
                code=main(['--profile','/never-read','--symbol','SPY','--policy',str(policy),
                           '--interpretations',str(meanings),'--output-root',str(root/'output')])
            self.assertEqual(code,0)
            result=json.loads(output.call_args.args[0])
            self.assertEqual([item['symbol'] for item in result['instruments']],['SPY'])
            self.assertEqual(result['instruments'][0]['status'],'collected')
            self.assertEqual(len(reader.calls),2)

    def test_cli_unknown_or_duplicate_symbol_never_loads_credentials(self):
        with patch('money_maker_3000.feed_collection.EtoroReader') as reader, patch('sys.stderr'):
            with self.assertRaises(SystemExit) as error:
                main(['--profile','/never-read','--probe-only','--symbol','UNKNOWN'])
            self.assertEqual(error.exception.code,2)
            reader.assert_not_called()
        with patch('money_maker_3000.feed_collection.EtoroReader') as reader, patch('builtins.print') as output:
            code=main(['--profile','/never-read','--probe-only','--symbol','SPY','--symbol','SPY'])
            self.assertEqual(code,1)
            self.assertEqual(json.loads(output.call_args.args[0])['reason'],'duplicate-collection-symbols')
            reader.assert_not_called()

    def test_missing_evidence_survives_narrower_windows_until_explicit_return(self):
        def observed(*days):
            return parse_candles(payload(*(candle(day) for day in days)),1,NOW)['observations']
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def save(observations, hour):
                return persist_version(root,symbol='SPY',observations=observations,interpretation=MEANING,
                                       retention=POLICY,retrieved_at=f'2026-09-14T{hour:02d}:00:00Z')
            initial=save(observed('2026-09-07','2026-09-08','2026-09-09'),6)
            missing=save(observed('2026-09-07','2026-09-09'),7)
            self.assertEqual(missing['unresolvedMissingDates'],['2026-09-08'])
            self.assertEqual(missing['missingPreviouslyObservedDates'],['2026-09-08'])
            narrower_rows=observed('2026-09-09','2026-09-10')
            narrow=save(narrower_rows,8)
            self.assertEqual(narrow['missingPreviouslyObservedDates'],[])
            self.assertEqual(narrow['unresolvedMissingDates'],['2026-09-08'])
            self.assertEqual(narrow['restoredDates'],[])
            self.assertEqual(save(narrower_rows,8),narrow)
            snapshot=json.loads((root/('SPY-'+narrow['version']+'.json')).read_text())
            self.assertEqual(snapshot['unresolvedMissingDates'],['2026-09-08'])
            self.assertIn('2026-09-08',[row['date'] for row in snapshot['observations']])
            returned_rows=observed('2026-09-08','2026-09-09','2026-09-10')
            restored=save(returned_rows,9)
            self.assertEqual(restored['unresolvedMissingDates'],[])
            self.assertEqual(restored['restoredDates'],['2026-09-08'])
            self.assertEqual(restored['restorationEvidence'],[{'date':'2026-09-08','observationTimestamp':'2026-09-08T00:00:00Z','retrievedAt':'2026-09-14T09:00:00Z','source':'etoro'}])
            self.assertEqual(save(returned_rows,9),restored)
            # Both the first observed version and withdrawn-evidence version survive.
            self.assertTrue((root/('SPY-'+initial['version']+'.json')).exists())
            self.assertEqual(json.loads((root/('SPY-'+narrow['version']+'.json')).read_text())['unresolvedMissingDates'],['2026-09-08'])

    def test_legacy_retrieval_requires_explicit_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            persist_version(root,symbol='SPY',observations=rows(),interpretation=MEANING,retention=POLICY,retrieved_at=NOW)
            path=next(root.glob('SPY-retrieval-*.json'))
            report=json.loads(path.read_text());report['schemaVersion']='market-retrieval.v1'
            path.write_text(json.dumps(report))
            with self.assertRaisesRegex(CollectionError,'legacy-retrieval-migration-required'):
                persist_version(root,symbol='SPY',observations=rows(),interpretation=MEANING,retention=POLICY,retrieved_at='2026-09-14T07:00:00Z')


if __name__=='__main__':unittest.main()
