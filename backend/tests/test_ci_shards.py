"""Scheduling/gate checks only; existing backend tests and fixtures are untouched."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
import ci_shards as ci


def nodes():
    return [f'{path}::test_example[case-{i}]' for i, path in enumerate(sorted(ci.W2_FILES))] + [
        'tests/migrations/test_future.py::test_upgrade',
        'tests/new_directory/test_new.py::test_new[one]',
        'tests/new_directory/test_new.py::test_new[two]',
    ]


def test_every_new_node_and_parameter_is_assigned_in_original_order():
    full = nodes()
    w2, rest = (ci.partition(full, name) for name in ci.SHARDS)
    summary = ci.verify_collections(full, w2, rest)
    assert summary['full_count'] == 6 and summary['intersection_count'] == 0
    assert rest == full[3:] and w2 == full[:3]
    assert ci.partition(full + ['test_new_root.py::test_added'], 'remaining')[-1] == 'test_new_root.py::test_added'


@pytest.mark.parametrize('change', ['missing', 'overlap', 'duplicate', 'swapped', 'order', 'empty', 'missing_w2', 'unknown'])
def test_partition_errors_fail_closed(change):
    full = nodes(); w2 = ci.partition(full, 'w2'); rest = ci.partition(full, 'remaining')
    with pytest.raises(ValueError):
        if change == 'missing':rest.pop()
        elif change == 'overlap':rest.append(w2[0])
        elif change == 'duplicate':w2.append(w2[0])
        elif change == 'swapped':w2[0], rest[0] = rest[0], w2[0]
        elif change == 'order':rest.reverse()
        elif change == 'empty':rest.clear()
        elif change == 'missing_w2':ci.partition(full[1:], 'remaining')
        elif change == 'unknown':ci.partition(full, 'other')
        ci.verify_collections(full, w2, rest)


def success():
    return {key: {'result': 'success', 'outputs': {}} for key in ci.REQUIRED_JOBS}


def test_gate_accepts_only_complete_success():
    ci.require_success(success())


@pytest.mark.parametrize('job', sorted(ci.REQUIRED_JOBS))
@pytest.mark.parametrize('state', ['failure', 'cancelled', 'skipped', '', None])
def test_failed_cancelled_skipped_or_unknown_job_cannot_pass(job, state):
    needs = success(); needs[job]['result'] = state
    with pytest.raises(ValueError):ci.require_success(needs)


@pytest.mark.parametrize('change', ['missing', 'extra', 'list', 'absent_result', 'bad_job'])
def test_malformed_aggregate_dependencies_fail_closed(change):
    needs = success()
    if change == 'missing':del needs['w2']
    elif change == 'extra':needs['unexpected'] = {'result': 'success'}
    elif change == 'list':needs = list(needs)
    elif change == 'absent_result':del needs['remaining']['result']
    else:needs['collection'] = None
    with pytest.raises(ValueError):ci.require_success(needs)


def test_gate_cli_exit_codes_and_malformed_json(monkeypatch):
    for value, code in [(json.dumps(success()), 0), ('{}', 1), ('invalid', 1)]:
        monkeypatch.setenv('NEEDS_JSON', value)
        assert ci.main(['gate']) == code


def test_missing_paths_fail_before_pytest_or_application_imports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert ci.main(['run', 'w2', '--expected-sha256', 'f'*64]) == 1


@pytest.mark.parametrize('broken', [False, True])
def test_actual_collection_handles_added_parameters_and_collection_errors(tmp_path, broken):
    # A wholly synthetic pytest tree. Child helper calls exercise collection,
    # never platform code, HTTP, credentials or any database.
    helper = Path(ci.__file__).resolve()
    for path in ci.W2_FILES:
        file = tmp_path / path; file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('from pathlib import Path\ndef test_owned(): Path("executed").touch()\n')
    (tmp_path / 'test_new.py').write_text('raise RuntimeError("owned collection failure")\n' if broken else
        'import pytest\n@pytest.mark.parametrize("n", [1,2])\ndef test_new(n): pass\n')
    # Different paths share basenames in the real suite, whose packages are
    # explicit; mirror that package structure in this independent tiny tree.
    for directory in ('tests', 'tests/api', 'tests/services'):
        (tmp_path / directory / '__init__.py').touch()
    output = tmp_path / 'collection.json'
    result = subprocess.run([sys.executable, str(helper), 'check', '--output', str(output)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    if broken:
        assert result.returncode != 0 and not output.exists()
        assert 'collection failed' in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        data = json.loads(output.read_text())
        assert data['summary']['full_count'] == 5
        assert data['summary']['w2_count'] == 3 and data['summary']['remaining_count'] == 2
        marker = tmp_path / 'executed'
        assert not marker.exists()
        command = [sys.executable, str(helper), 'run', 'w2', '--expected-sha256', data['summary']['full_sha256']]
        good = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert good.returncode == 0 and marker.exists(), good.stdout + good.stderr
        marker.unlink()
        # An environment-dependent/new test after the check must not silently
        # change either shard, even though all required W2 files remain present.
        (tmp_path / 'test_later.py').write_text('def test_later(): pass\n')
        stale = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert stale.returncode != 0 and not marker.exists()
        assert 'differs from completeness job' in stale.stderr


def test_ambient_selection_flags_are_rejected(monkeypatch):
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k only_some_tests')
    with pytest.raises(ValueError, match='PYTEST_ADDOPTS'):ci.pytest_run('remaining')
