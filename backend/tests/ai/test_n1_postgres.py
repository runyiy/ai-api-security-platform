"""Real owned-cluster failures; endpoint/process checks are independent of cleanup."""
import os
import select
import subprocess

import pytest

from tests.ai.n1_postgres import OwnedPostgres


def fixture_database(tmp_path, name='postgres'):
    root = tmp_path / name
    root.mkdir()
    return OwnedPostgres(root)


def observe_process(database):
    pid = int((database.data / 'postmaster.pid').read_text().splitlines()[0])
    return os.pidfd_open(pid)


def assert_stopped(database, descriptor):
    assert select.select([descriptor], [], [], 0)[0] == [descriptor]
    assert not (database.data / 'postmaster.pid').exists()
    result = subprocess.run(
        [str(database.bin / 'pg_ctl'), '-D', str(database.data), 'status'],
        env=database.env, capture_output=True, timeout=3)
    assert result.returncode == 3
    control = database.command('pg_controldata', database.data)
    assert 'Database cluster state:               shut down' in control
    assert database.cleanup_failures == []


@pytest.mark.parametrize('stage', ['start_acknowledgement', 'before_createdb'])
def test_setup_failure_stops_owned_server_preserves_original(tmp_path, stage):
    database = fixture_database(tmp_path)
    original = database.command
    failure = RuntimeError('SYNTHETIC_' + stage.upper())
    observed = []

    def command(*args, **kwargs):
        if args[0] == 'createdb' and stage == 'before_createdb':
            observed.append(observe_process(database))
            raise failure
        result = original(*args, **kwargs)
        if args[0] == 'pg_ctl' and args[-1] == 'start' and stage == 'start_acknowledgement':
            observed.append(observe_process(database))
            raise failure  # Start completed; only its acknowledgement is lost.
        return result

    database.command = command
    try:
        with pytest.raises(RuntimeError) as caught:
            with database.running():
                pytest.fail('setup must not yield')
        assert caught.value is failure
        assert str(caught.value) == 'SYNTHETIC_' + stage.upper()
        assert not getattr(caught.value, '__notes__', [])
        assert len(observed) == 1
        assert_stopped(database, observed[0])
        assert database.identity is None  # n1_synthetic was never required for cleanup.
    finally:
        for descriptor in observed:
            os.close(descriptor)


def test_successful_start_use_and_stop(tmp_path):
    database = fixture_database(tmp_path)
    with database.running():
        descriptor = observe_process(database)
        assert database.verify(empty=True)['database'] == 'n1_synthetic'
        database.sql('CREATE TABLE synthetic_cleanup_control (n integer);')
        database.sql('INSERT INTO synthetic_cleanup_control VALUES (17);')
        assert database.sql('SELECT n FROM synthetic_cleanup_control;').strip() == '17'
    try:
        assert_stopped(database, descriptor)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize('primary_error', [False, True])
@pytest.mark.parametrize('mismatch', ['endpoint', 'cluster'])
def test_identity_mismatch_does_not_stop_other_server_or_mask_failure(tmp_path, primary_error, mismatch):
    database = fixture_database(tmp_path, 'subject')
    other = fixture_database(tmp_path, 'independent')
    error = RuntimeError('SYNTHETIC_SETUP_FAILURE')
    original_verify = database.verify
    original_sql = database.sql
    original_cluster = None
    descriptors = []
    with other.running():
        other_identity = other.verify()
        descriptors.append(observe_process(other))
        try:
            with pytest.raises(RuntimeError) as caught:
                with database.running():
                    descriptors.append(observe_process(database))
                    # Substitute the independent endpoint's identity in the actual
                    # maintenance lookup. A real, reachable server is not ownership.
                    original_cluster = database.cluster

                    def substituted(sql, *, database=None):
                        if database == 'postgres':
                            return other.sql(sql, database='postgres')
                        return original_sql(sql, database=database)

                    if mismatch == 'endpoint':
                        database.sql = substituted
                    else:
                        database.cluster = other.cluster
                    if primary_error:
                        raise error
            if primary_error:
                assert caught.value is error
                reports = caught.value.__notes__
            else:
                reports = [str(caught.value)]
            assert reports == database.cleanup_failures
            assert len(reports) == 1
            assert reports[0] == 'N1_OWNED_DATABASE_CLEANUP_UNRESOLVED: ' + str(database.root)
            assert database.admin_password not in str(reports)
            assert select.select(descriptors, [], [], 0)[0] == []
            assert other.verify() == other_identity
            # Restore the genuine ownership path before stopping this test's
            # intentionally unresolved instance. No blind cleanup of either PID.
            database.sql = original_sql
            database.cluster = original_cluster
            assert original_verify()['system_identifier'] == database.identity['system_identifier']
            database.cleanup()
            assert select.select([descriptors[1]], [], [], 0)[0] == [descriptors[1]]
        finally:
            # Preserve the exception if an assertion fails; cleanup still requires
            # independently verified identity, never a forced/unconditional stop.
            if (database.data / 'postmaster.pid').exists():
                database.sql = original_sql
                if original_cluster is not None:
                    database.cluster = original_cluster
                database.cleanup()
    try:
        assert_stopped(other, descriptors[0])
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def test_pre_start_failure_never_signals_an_unrelated_server(tmp_path):
    database = fixture_database(tmp_path, 'not_started')
    other = fixture_database(tmp_path, 'running')
    original = database.command
    failure = RuntimeError('SYNTHETIC_NO_START')
    with other.running():
        descriptor = observe_process(other)

        def command(*args, **kwargs):
            if args[0] == 'pg_ctl' and args[-1] == 'start':
                raise failure
            return original(*args, **kwargs)

        database.command = command
        with pytest.raises(RuntimeError) as caught:
            with database.running():
                pytest.fail('setup must not yield')
        assert caught.value is failure
        assert database.cleanup_failures == []
        assert not (database.data / 'postmaster.pid').exists()
        assert select.select([descriptor], [], [], 0)[0] == []
        assert other.sql('SELECT 1;').strip() == '1'
    try:
        assert_stopped(other, descriptor)
    finally:
        os.close(descriptor)


def test_unverifiable_cleanup_reports_sanitized_resource_preserves_error(tmp_path):
    database = fixture_database(tmp_path)
    verify = database.verify
    failure = RuntimeError('SYNTHETIC_ORIGINAL_FAILURE')
    try:
        with pytest.raises(RuntimeError) as caught:
            with database.running():
                descriptor = observe_process(database)

                def unavailable(**kwargs):
                    raise RuntimeError('raw-response:' + database.admin_password)

                database.verify = unavailable
                raise failure
        assert caught.value is failure
        assert caught.value.__notes__ == database.cleanup_failures
        assert database.cleanup_failures == [
            'N1_OWNED_DATABASE_CLEANUP_UNRESOLVED: ' + str(database.root)]
        assert 'raw-response' not in repr(caught.value.__notes__)
        assert database.admin_password not in repr(caught.value.__notes__)
        assert select.select([descriptor], [], [], 0)[0] == []
    finally:
        database.verify = verify
        if (database.data / 'postmaster.pid').exists():
            database.cleanup()
        os.close(descriptor)
