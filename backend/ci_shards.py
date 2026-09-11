"""Serial pytest partitions; no application imports outside pytest collection.

Run from backend. Normal pytest remains unchanged unless this helper explicitly
supplies its collection plugin. The aggregate command needs only the stdlib.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time

W2_FILES = frozenset({
    'tests/services/test_research_verification.py',
    'tests/services/test_research_verification_expiry.py',
    'tests/api/test_research_verification.py',
})
SHARDS = ('w2', 'remaining')
REQUIRED_JOBS = frozenset({'collection', *SHARDS})
HELPER_TEST = 'tests/test_ci_shards.py'


def unique_nodes(nodes):
    if not isinstance(nodes, list) or not nodes or any(not isinstance(n, str) or not n for n in nodes):
        raise ValueError('empty or malformed collection')
    if len(set(nodes)) != len(nodes):
        raise ValueError('duplicate node IDs')
    return set(nodes)


def partition(nodes, shard):
    if shard not in SHARDS:
        raise ValueError('unknown shard')
    unique_nodes(nodes)
    files = {n.split('::', 1)[0] for n in nodes}
    if not W2_FILES <= files:
        raise ValueError('required W2 file missing or empty in collection')
    selected = [n for n in nodes if (n.split('::', 1)[0] in W2_FILES) == (shard == 'w2')]
    if not selected:
        raise ValueError('unexpected empty shard')
    return selected


def collection_digest(nodes):
    unique_nodes(nodes)
    return hashlib.sha256(json.dumps(nodes).encode()).hexdigest()


def verify_collections(full, w2, remaining):
    all_ids, w2_ids, rest_ids = map(unique_nodes, (full, w2, remaining))
    if w2_ids & rest_ids or w2_ids | rest_ids != all_ids:
        raise ValueError('shard overlap or incomplete union')
    if w2 != partition(full, 'w2') or remaining != partition(full, 'remaining'):
        raise ValueError('unexpected assignment or collection order')
    return {
        'full_count': len(full), 'w2_count': len(w2), 'remaining_count': len(remaining),
        'helper_count': sum(n.split('::', 1)[0] == HELPER_TEST for n in full),
        'full_sha256': collection_digest(full),
        'intersection_count': 0,
    }


def require_success(needs):
    if not isinstance(needs, dict) or set(needs) != REQUIRED_JOBS:
        raise ValueError('missing or unknown required job')
    for name in sorted(REQUIRED_JOBS):
        job = needs[name]
        if not isinstance(job, dict) or job.get('result') != 'success':
            raise ValueError(f'required job did not succeed: {name}')


def pytest_run(shard=None, *, collect=False, output=None, expected_sha256=None):
    import pytest
    if os.environ.get('PYTEST_ADDOPTS'):
        raise ValueError('PYTEST_ADDOPTS must be empty for complete CI validation')
    if not all(Path(p).is_file() for p in W2_FILES):
        raise ValueError('required W2 path missing; run from backend')
    if shard is not None and shard not in SHARDS:
        raise ValueError('unknown shard')
    if not collect and (not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256)):
        raise ValueError('missing or invalid full-collection digest')

    class Collection:
        nodes = None
        expected = None
        rejected = False

        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(self, config, items):
            if shard is None:
                return
            try:
                all_nodes = [item.nodeid for item in items]
                if not collect and collection_digest(all_nodes) != expected_sha256:
                    raise ValueError('actual collection differs from completeness job')
                self.expected = partition(all_nodes, shard)
            except ValueError as exc:
                self.rejected = True
                raise pytest.UsageError(str(exc)) from exc
            selected = set(self.expected)
            excluded = [item for item in items if item.nodeid not in selected]
            items[:] = [item for item in items if item.nodeid in selected]
            config.hook.pytest_deselected(items=excluded)

        def pytest_collection_finish(self, session):
            self.nodes = [item.nodeid for item in session.items]
            if self.rejected:
                return  # Preserve the original collection/partition error.
            try:
                unique_nodes(self.nodes)
                if shard is not None and self.nodes != self.expected:
                    raise ValueError('collection changed after partition selection')
            except ValueError as exc:
                raise pytest.UsageError(str(exc)) from exc

    plugin = Collection()
    start = time.monotonic()
    args = ['-q', '--collect-only'] if collect else ['-q', '--tb=short', '--durations=20']
    result = int(pytest.main(args, plugins=[plugin]))
    # Never write a success manifest for partial/error/empty collection.
    if result == 0 and collect:
        Path(output).write_text(json.dumps(plugin.nodes) + '\n')
    print(json.dumps({'partition': shard or 'full', 'collected': len(plugin.nodes or []),
                      'elapsed_seconds': round(time.monotonic()-start, 3), 'exit_code': result}), flush=True)
    return result


def completeness(output):
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='ci-collection-') as tmp:
        collections = {}
        # Separate processes preserve normal module/pytest initialization.
        for name in ('full', *SHARDS):
            dest = Path(tmp) / (name + '.json')
            command = [sys.executable, str(Path(__file__).resolve()), '_collect', name, '--output', str(dest)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if result.returncode:
                sys.stdout.write(result.stdout)
                sys.stderr.write(result.stderr)
                raise ValueError(f'collection failed: {name}')
            collections[name] = json.loads(dest.read_text())
        summary = verify_collections(collections['full'], collections['w2'], collections['remaining'])
        summary['elapsed_seconds'] = round(time.monotonic()-start, 3)
        Path(output).write_text(json.dumps({'summary': summary, 'nodes': collections}, indent=2) + '\n')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as github_output:
                github_output.write('full_sha256=' + summary['full_sha256'] + '\n')
        print(json.dumps(summary), flush=True)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check')
    check.add_argument('--output', required=True)
    run = commands.add_parser('run')
    run.add_argument('shard', choices=SHARDS)
    run.add_argument('--expected-sha256', required=True)
    collect = commands.add_parser('_collect')
    collect.add_argument('shard', choices=('full', *SHARDS))
    collect.add_argument('--output', required=True)
    commands.add_parser('gate')
    args = parser.parse_args(argv)
    try:
        if args.command == 'gate':
            require_success(json.loads(os.environ.get('NEEDS_JSON', '{}')))
            print('Complete backend validation succeeded: collection, w2, remaining.')
            return 0
        if args.command == 'check':
            return completeness(args.output)
        if args.command == 'run':
            return pytest_run(args.shard, expected_sha256=args.expected_sha256)
        return pytest_run(None if args.shard == 'full' else args.shard, collect=True, output=args.output)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'CI validation rejected: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
