#!/usr/bin/env python3
"""Configured Money Maker research cycles with preserved legacy offline replay."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

ANCHOR = Path('/Users/yogi/Coding/projects/Money-maker-3000')
DATA_ROOT = Path('data/private/market-history')
INVENTORY = 'learning-inventory.json'
STORE_ROOT = Path('.local/learning/skill-sessions')
STRATEGIES = ('volatility-band-accumulator', 'slow-trend-allocation')
VERSION = 'money-maker-learning-session.v1'


class SessionError(ValueError):
    pass


def require(ok, message='invalid-learning-session-input'):
    if not ok:
        raise SessionError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate-json-key')
            result[key] = value
        return result
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= 131072, 'unsafe-json-input')
        raw = handle.read(131073)
        require(len(raw) <= 131072, 'unsafe-json-input')
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: require(False))
    require(type(result) is dict)
    return result


def write_json(path, value):
    raw = canonical(value) + b'\n'
    require(len(raw) <= 131072, 'session-json-too-large')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def contained(root, relative, *, create_dirs=False):
    require(type(relative) is str and relative and not Path(relative).is_absolute())
    parts = Path(relative).parts
    require(all(part not in ('.', '..') for part in parts), 'path-outside-data-root')
    current = root
    require(current.is_dir() and not current.is_symlink(), 'unsafe-root-directory')
    for index, part in enumerate(parts):
        current /= part
        require(not current.is_symlink(), 'symlink-path-rejected')
        if create_dirs:
            current.mkdir(mode=0o700, exist_ok=True)
        if index < len(parts) - 1 or create_dirs:
            require(current.is_dir(), 'missing-parent-directory')
    return current


def discover_repository():
    output = subprocess.check_output(['git', '-C', str(ANCHOR), 'worktree', 'list', '--porcelain'], text=True, stderr=subprocess.PIPE)
    matches = []
    for block in output.strip().split('\n\n'):
        fields = dict(line.split(' ', 1) for line in block.splitlines() if ' ' in line)
        if fields.get('branch') == 'refs/heads/develop':
            matches.append(Path(fields['worktree']))
    require(len(matches) == 1, 'unique-develop-worktree-required')
    root = matches[0].resolve(strict=True)
    status = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True, stderr=subprocess.PIPE)
    require(not status.strip(), 'develop-worktree-must-be-clean')
    return root


def provider_label(source):
    if source.startswith('fmp') or 'financial-modeling-prep' in source:
        return 'fmp'
    for provider in ('kibot', 'firstrate'):
        if provider in source:
            return provider
    return 'other'


def retention_block(entry):
    retention = entry['retention']
    require(type(retention) is dict)
    fmp_policy = 'fmp-active-subscription-delete-within-30-days'
    if provider_label(entry['source']) == 'fmp':
        require(retention.get('policy') == fmp_policy, 'fmp-retention-policy-required')
    if retention.get('policy') == fmp_policy:
        require(set(retention) == {'policy', 'subscriptionStatus', 'terminationDate'})
        if retention['subscriptionStatus'] != 'active' or retention['terminationDate'] is not None:
            return 'fmp-inactive-or-terminated-delete-all-source-artifacts-within-30-days-of-termination'
    else:
        require(retention == {'policy': 'source-terms'}, 'unknown-retention-policy')
    return None


def load_inventory(repo, learning, allow_synthetic):
    data_root = contained(repo, str(DATA_ROOT))
    inventory = read_json(contained(data_root, INVENTORY))
    require(set(inventory) == {'version', 'datasets'} and inventory['version'] == 'money-maker-learning-inventory.v1')
    entries = inventory['datasets']
    require(type(entries) is list and 1 <= len(entries) <= 20, 'inventory-requires-1-to-20-datasets')
    loaded, blocked, seen = [], [], set()
    for entry in entries:
        require(type(entry) is dict and set(entry) == {'csvPath', 'manifestPath', 'source', 'symbol', 'retention'})
        require(entry['symbol'] in ('SPY', 'QQQ', 'VAS'))
        require(type(entry['source']) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,63}', entry['source']) is not None)
        reason = retention_block(entry)
        if reason:
            blocked.append({'symbol': entry['symbol'], 'provider': provider_label(entry['source']), 'reason': reason})
            continue
        csv_path = contained(data_root, entry['csvPath'])
        manifest_path = contained(data_root, entry['manifestPath'])
        bars, manifest = learning.load_dataset(csv_path, manifest_path, allow_synthetic_smoke=allow_synthetic)
        require(entry['source'] == manifest['source'] and entry['symbol'] == manifest['symbol'], 'inventory-manifest-mismatch')
        identity = (manifest['sha256'], manifest['symbol'])
        require(identity not in seen, 'duplicate-inventory-dataset')
        seen.add(identity)
        loaded.append((csv_path, manifest_path, bars, manifest, entry['retention']))
    return loaded, blocked


def make_plan(learning, bars, manifest, strategy, retention):
    count = len(bars)
    train_index, validation_index = count * 3 // 5 - 1, count * 4 // 5 - 1
    warmup = max(item.get('lookbackDays', item.get('longLookbackDays', 0) + item.get('confirmationBars', 1) - 1)
                 for item in learning.candidate_grid(strategy))
    counts = [train_index - 5 - warmup + 2, validation_index - train_index - 5, count - validation_index - 6]
    if any(actual < minimum for actual, minimum in zip(counts, (20, 10, 10))):
        return None
    return {'version': VERSION, 'pipelineVersion': learning.VERSION, 'datasetSha256': manifest['sha256'],
            'symbol': manifest['symbol'], 'sourceSha256': digest(manifest['source']),
            'priceBasis': manifest['priceBasis'], 'classification': manifest['classification'],
            'strategy': strategy, 'horizonBars': 5, 'trainEnd': bars[train_index].date,
            'validationEnd': bars[validation_index].date, 'datasetRows': count,
            'datasetFirstDate': bars[0].date, 'datasetLastDate': bars[-1].date,
            'provider': provider_label(manifest['source']), 'retention': retention,
            'subscriptionVerification': 'operator-attestation-only'}


def verify_model(learning, artifact, plan):
    learning.validate_artifact(artifact)
    model, report = artifact['model'], artifact['report']
    require(artifact['version'] == plan['pipelineVersion'], 'pipeline-version-mismatch')
    for key in ('symbol', 'sourceSha256', 'priceBasis', 'classification', 'strategy', 'horizonBars'):
        require(model[key] == plan[key], 'cached-model-plan-mismatch')
    for key in ('datasetSha256', 'trainEnd', 'validationEnd', 'datasetRows', 'datasetFirstDate', 'datasetLastDate'):
        require(report[key] == plan[key], 'cached-model-plan-mismatch')


def run_session(repo, learning, *, allow_synthetic=False):
    loaded, blocked = load_inventory(repo, learning, allow_synthetic)
    planned, skipped = [], list(blocked)
    # Freeze every cutoff before fitting any candidate; dates never depend on scores.
    for csv_path, manifest_path, bars, manifest, retention in loaded:
        for strategy in STRATEGIES:
            plan = make_plan(learning, bars, manifest, strategy, retention)
            if plan is None:
                skipped.append({'symbol': manifest['symbol'], 'provider': provider_label(manifest['source']), 'strategy': strategy, 'reason': 'insufficient-learning-splits'})
            else:
                planned.append((csv_path, manifest_path, plan))
    if not planned:
        return {'version': VERSION, 'status': 'blocked', 'boundary': dict(learning.BOUNDARY), 'results': [], 'skipped': skipped}
    store = contained(repo, str(STORE_ROOT), create_dirs=True)
    results = []
    for csv_path, manifest_path, plan in planned:
        model_id = digest(plan)
        folder = contained(store, model_id)
        model_path, plan_path, report_path = folder / 'model.json', folder / 'plan.json', folder / 'report.json'
        if folder.exists():
            require(folder.is_dir(), 'invalid-existing-session')
            require(read_json(plan_path) == plan, 'cached-plan-mismatch')
            artifact = learning.load_artifact(model_path)
            verify_model(learning, artifact, plan)
            saved = read_json(report_path)
            require(saved == {'version': VERSION, 'planSha256': model_id, 'modelSha256': artifact['sha256']}, 'cached-report-mismatch')
            action = 'reused-verified-artifact'
        else:
            folder.mkdir(mode=0o700)
            write_json(plan_path, plan)
            artifact = learning.train(csv_path, manifest_path, strategy=plan['strategy'], horizon_bars=5,
                                      train_end=plan['trainEnd'], validation_end=plan['validationEnd'],
                                      allow_synthetic_smoke=allow_synthetic)
            verify_model(learning, artifact, plan)
            learning.write_artifact(artifact, model_path)
            write_json(report_path, {'version': VERSION, 'planSha256': model_id, 'modelSha256': artifact['sha256']})
            action = 'trained-once'
        report = artifact['report']
        results.append({'symbol': plan['symbol'], 'strategy': plan['strategy'], 'action': action,
                        'artifactPath': str(STORE_ROOT / model_id / 'model.json'),
                        'provider': plan['provider'], 'retention': plan['retention'],
                        'subscriptionVerification': plan['subscriptionVerification'],
                        'modelSha256': artifact['sha256'], 'status': report['status'],
                        'datasetRows': plan['datasetRows'], 'firstDate': plan['datasetFirstDate'],
                        'lastDate': plan['datasetLastDate'], 'splits': report['splits'],
                        'holdoutBrier': report['holdoutBrier'], 'baselineHoldoutBrier': report['baselineHoldoutBrier'],
                        'validationBeatsPrior': report['validationBeatsPrior'],
                        'oneClassTraining': report['oneClassTraining'], 'constantTrainingFeature': report['constantTrainingFeature']})
    return {'version': VERSION, 'status': 'partial' if blocked else 'replay' if all(item['action'] == 'reused-verified-artifact' for item in results) else 'completed',
            'boundary': dict(learning.BOUNDARY), 'results': results, 'skipped': skipped,
            'provenanceVerification': 'operator-attestation-unverified',
            'note': 'Cached results are a replay, not new holdout evidence; smaller Brier error is diagnostic only.'}


def main():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            super().error('invalid-learning-skill-arguments')
    parser = Parser(description='Run configured Money Maker research or legacy offline learning from approved inputs.')
    parser.add_argument('--allow-synthetic-smoke', action='store_true', help='explicit testing only; never observed evidence')
    parser.add_argument('--status',action='store_true',help='read configured cycle status only')
    parser.add_argument('--replay',action='store_true',help='offline configured cycle integrity replay')
    parser.add_argument('--legacy',action='store_true',help='preserve original fixed split learning replay')
    args = parser.parse_args()
    try:
        require(not (args.status and args.replay), 'conflicting-learning-modes')
        repo = discover_repository()
        sys.path.insert(0, str(repo / 'src'))
        learning = importlib.import_module('money_maker_3000.learning')
        cycle_config = repo / 'data/private/market-history/continuous-research-config.json'
        require(not cycle_config.is_symlink(), 'cycle-config-symlink-rejected')
        if cycle_config.exists() and not args.legacy:
            coordinator = importlib.import_module('money_maker_3000.research_cycle_cli')
            result = coordinator.coordinate(cycle_config,mode='status' if args.status else 'replay' if args.replay else 'cycle',allow_synthetic=args.allow_synthetic_smoke)
        else:
            require(not (args.status or args.replay), 'configured-cycle-required-for-mode')
            result = run_session(repo, learning, allow_synthetic=args.allow_synthetic_smoke)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 1 if result['status'] in ('blocked', 'partial') else 0
    except Exception as exc:
        # Never emit input values, parser messages, tracebacks, or private paths.
        message = str(exc) if type(exc) is SessionError else 'learning-session-unavailable-or-invalid'
        print(message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
