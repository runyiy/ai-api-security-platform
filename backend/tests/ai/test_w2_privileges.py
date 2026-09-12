"""Actual W2 table/function privileges in an independently owned PG instance."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile

from tests.ai.n1_postgres import OwnedPostgres
from tests.ai.n1_sandbox import Sandbox


def test_sandbox_child_cannot_write_any_w2_table_or_call_administration():
    backend=Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='w2-grants-') as temporary:
        root=Path(temporary)
        pgroot=root/'pg';pgroot.mkdir()
        database=OwnedPostgres(pgroot)
        with database.running():
            identity=database.verify(empty=True)
            assert identity['tables']==identity['other_clients']==0
            # Parent migration process gets a fresh, isolated config directory.
            # The child receives only its generated non-administrative DSN.
            migration=root/'migration'
            migration.mkdir()
            for name in ('alembic','app'):
                shutil.copytree(backend/name,migration/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.env*'))
            shutil.copyfile(backend/'alembic.ini',migration/'alembic.ini')
            env={'PATH':str(Path(sys.executable).parent)+':/usr/bin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1',
                'DATABASE_URL':f'postgresql+psycopg://{database.admin}:{database.admin_password}@127.0.0.1:{database.port}/{database.database}',
                'ALLOWED_TARGET_HOSTS':'localhost,127.0.0.1,::1','EXECUTION_TOPOLOGY':'single_process','CREDENTIAL_ENCRYPTION_KEY_VERSION':'v1'}
            migrated=subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=migration,env=env,
                capture_output=True,timeout=30)
            assert migrated.returncode==0
            tables=json.loads(database.sql("SELECT json_agg(tablename ORDER BY tablename) FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'research_ai_%'"))
            functions=json.loads(database.sql("SELECT json_agg(proname ORDER BY proname) FROM pg_proc WHERE proname LIKE 'research_ai_%'"))
            assert len(tables)==17 and len(functions)==2
            code=root/'code';code.mkdir()
            call=root/'call';call.mkdir()
            (code/'db.json').write_text(json.dumps(database.child_config()))
            (code/'tables.json').write_text(json.dumps(tables))
            (code/'functions.json').write_text(json.dumps(functions))
            (code/'child.py').write_text('''import sys
sys.path[:0]=['/packages']
import json,os
from pathlib import Path
import psycopg
from psycopg import sql
config=json.loads(Path('/code/db.json').read_text())
tables=json.loads(Path('/code/tables.json').read_text())
functions=json.loads(Path('/code/functions.json').read_text())
denied=[]
with psycopg.connect(**config,autocommit=True) as db:
    assert db.execute('SELECT current_user').fetchone()[0]=='n1_adapter'
    for name in tables:
        assert db.execute("SELECT has_table_privilege(current_user,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",(name,)).fetchone()[0] is False
        try: db.execute(sql.SQL('SELECT * FROM {} LIMIT 1').format(sql.Identifier(name)))
        except psycopg.errors.InsufficientPrivilege: denied.append(name)
        else: raise AssertionError('table accessible')
    for name in functions:
        assert db.execute("SELECT has_function_privilege(current_user,%s,'EXECUTE')",(name+'()',)).fetchone()[0] is False
    for statement in ("SET ROLE n1_migration", "ALTER ROLE n1_adapter SUPERUSER", "CREATE TABLE public.forged_permit(id int)", "SELECT pg_read_file('/etc/passwd')"):
        try: db.execute(statement)
        except psycopg.errors.InsufficientPrivilege: pass
        else: raise AssertionError('administration accessible')
assert 'DATABASE_URL' not in os.environ
assert not Path('/home').exists() and not Path('/code/app/ai/w2/fake_authority.py').exists()
print(json.dumps({'denied_tables':denied,'denied_functions':functions,'user':'n1_adapter'}))
''')
            result=json.loads(Sandbox(code,database.socket,call).run(timeout=10))
            assert result==dict(denied_tables=tables,denied_functions=functions,user=database.child)
            assert database.verify()['system_identifier']==identity['system_identifier']
