"""One owned validation run; never read operator configuration."""
import json
from hashlib import sha256
import os
from pathlib import Path
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
PYTHON = REPO / 'backend/.venv/bin/python'
root = Path(tempfile.mkdtemp(prefix='rw2-', dir='/tmp'))
root.chmod(0o700)
snapshot = root / 'repo'
snapshot.mkdir()
shutil.copytree(REPO / 'backend', snapshot / 'backend', ignore=shutil.ignore_patterns(
    '.env', '.env.*', '.venv', '.idea', '__pycache__', '.pytest_cache', '*.pyc'))
shutil.copytree(REPO / '.github', snapshot / '.github')
shutil.copytree(REPO / 'docs', snapshot / 'docs')
shutil.copy2(REPO / 'README.md', snapshot / 'README.md')
sources={str(p.relative_to(snapshot)):sha256(p.read_bytes()).hexdigest()
         for p in sorted(snapshot.rglob('*')) if p.is_file()}
source_digest=sha256(json.dumps(sources,sort_keys=True,separators=(',',':')).encode()).hexdigest()
(root/'source.json').write_text(json.dumps({'sha256':source_digest,'files':sources},indent=2))
OwnedPostgres = runpy.run_path(str(REPO / 'backend/tests/ai/n1_postgres.py'))['OwnedPostgres']
database_root = root / 'pg'
database_root.mkdir()
database = OwnedPostgres(database_root)
result = 1
started = time.monotonic()
try:
    with database.running():
        identity = database.verify(empty=True)
        (root/'identity.json').write_text(json.dumps(identity,indent=2))
        print(json.dumps({'run': str(root), 'verified_empty_TEST': identity}), flush=True)
        env = {'PATH': str(PYTHON.parent) + ':/usr/bin:/bin', 'LANG': 'C.UTF-8',
               'PYTHONDONTWRITEBYTECODE': '1', 'PYTEST_ADDOPTS': '',
               'DATABASE_URL': f'postgresql+psycopg://{database.admin}:{database.admin_password}@127.0.0.1:{database.port}/{database.database}',
               'ALLOWED_TARGET_HOSTS': 'localhost,127.0.0.1,::1',
               'EXECUTION_TOPOLOGY': 'single_process',
               'CREDENTIAL_ENCRYPTION_KEY_VERSION': 'v1'}
        commands = [[str(PYTHON), '-m', 'alembic', 'upgrade', 'head']]
        if sys.argv[1:] == ['complete']:
            commands += [[str(PYTHON), '-m', 'ci_shards', 'check', '--output', str(root / 'collection.json')],
                         [str(PYTHON), '-m', 'pytest', '-q', '--tb=short', '--durations=20']]
        else:
            commands += [[str(PYTHON), '-m', 'pytest', '-q', '--tb=short', *sys.argv[1:]]]
        for index, command in enumerate(commands):
            print(json.dumps({'step': index, 'command': command[1:]}), flush=True)
            with (root / f'step-{index}.log').open('w') as output:
                with subprocess.Popen(command,cwd=snapshot/'backend',env=env,
                        stdout=output,stderr=subprocess.STDOUT) as run:
                    end=time.monotonic()+7200
                    while run.poll() is None:
                        if (root/'cancel').exists():
                            # Only this owned child is interrupted. The parent
                            # still checkpoints and verifies its database stop.
                            run.send_signal(signal.SIGINT)
                            try:run.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                run.kill();run.wait(timeout=3)
                            break
                        if time.monotonic()>=end:
                            run.kill();run.wait(timeout=3)
                            break
                        try:run.wait(timeout=1)
                        except subprocess.TimeoutExpired:pass
            print((root / f'step-{index}.log').read_text()[-22000:], flush=True)
            result = run.returncode
            if result:
                break
        # Complete the owned run's dirty-page flush before the unchanged bounded
        # N1 fast-stop check. This does not alter host/CI PostgreSQL settings.
        database.verify()
        database.sql("SET statement_timeout='12s'; CHECKPOINT;")
finally:
    cleanup = not (database.data / 'postmaster.pid').exists() and not database.cleanup_failures
    report = {'exit_code': result, 'owned_postgres_stopped': cleanup,
              'elapsed_seconds': round(time.monotonic() - started, 3), 'evidence': str(root),
              'source_sha256':source_digest}
    (root / 'result.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
sys.exit(result)
