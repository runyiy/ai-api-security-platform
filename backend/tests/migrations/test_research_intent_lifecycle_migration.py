"""Additive hold provenance, retained legacy history and fail-closed rollback."""
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError,DataError
from app.core.config import settings
from app.db.session import engine,SessionLocal
from app.db.models.research_observation import ObservationRecord
from tests.research_intent_fixtures import subject_pair,two_intake_targets,NOW,REF  # noqa: F401
from tests.research_intake_fixtures import snapshot
from tests.research_intent_fixtures import call
from app.services import research_observation as observation
from datetime import timedelta

REVISION='5f83bac2e714'
PARENT='4e72a9c1d603'
TABLE='research_observation_records'


def test_fresh_schema_and_empty_downgrade(monkeypatch):
    config=Config('alembic.ini');scripts=ScriptDirectory.from_config(config)
    assert scripts.get_heads()==['6a94cbd3f825'] and scripts.get_revision(REVISION).down_revision==PARENT
    name='intent_lifecycle_'+uuid4().hex
    with engine.begin() as db:db.execute(text(f'CREATE SCHEMA "{name}"'))
    url=make_url(settings.database_url).update_query_dict({'options':f'-csearch_path={name}'})
    isolated=create_engine(url)
    try:
        monkeypatch.setattr(settings,'database_url',url.render_as_string(hide_password=False).replace('%','%%'))
        command.upgrade(config,PARENT)
        before=inspect(isolated);tables=set(before.get_table_names());cols=before.get_columns(TABLE)
        command.upgrade(config,REVISION)
        after=inspect(isolated)
        assert set(after.get_table_names())==tables
        added=after.get_columns(TABLE)[-1]
        assert added['name']=='hold_generation' and not added['nullable'] and added['default']=='0'
        assert added['type'].compile(dialect=engine.dialect)=='INTEGER'
        assert 'ck_observation_hold_generation' in {c['name'] for c in after.get_check_constraints(TABLE)}
        command.downgrade(config,PARENT)
        assert [c['name'] for c in inspect(isolated).get_columns(TABLE)]==[c['name'] for c in cols]
        command.upgrade(config,REVISION)
    finally:
        isolated.dispose()
        with engine.begin() as db:db.execute(text(f'DROP SCHEMA "{name}" CASCADE'))


def test_populated_upgrade_preserves_history_and_defaults_existing_records(subject_pair):
    config=Config('alembic.ini');before=snapshot()
    try:
        command.downgrade(config,PARENT)
        with engine.connect() as db:
            # Inspect the previous representation without importing a second model.
            rows=list(db.execute(text('SELECT * FROM research_observation_records ORDER BY id')).mappings())
        assert rows==[{k:v for k,v in r.items() if k!='hold_generation'} for r in before[TABLE]]
        command.upgrade(config,'head')
        assert snapshot()==before
        with SessionLocal() as db:
            row=db.get(ObservationRecord,subject_pair[0]['observation'])
            assert row.hold_generation==0
            for value in (-1,2147483648):
                with pytest.raises((IntegrityError,DataError)):
                    with db.begin_nested():
                        db.execute(text('UPDATE research_observation_records SET hold_generation=:value WHERE id=:id'),dict(value=value,id=row.id))
            db.rollback()
        assert snapshot()==before
    finally:command.upgrade(config,'head')


def test_committed_hold_provenance_cannot_be_downgraded(subject_pair):
    g=subject_pair[0];oid=g['observation']
    call(observation.lifecycle,g,oid,'hold',dict(review=REF,reason='synthetic_review',until=(NOW+timedelta(seconds=20)).isoformat()))
    call(observation.lifecycle,g,oid,'release',dict(review=REF),now=NOW+timedelta(seconds=1))
    before=snapshot()
    with pytest.raises(RuntimeError,match='observation_lifecycle_populated_downgrade_blocked'):
        command.downgrade(Config('alembic.ini'),PARENT)
    assert snapshot()==before
    with engine.connect() as db:assert MigrationContext.configure(db).get_current_revision()=='6a94cbd3f825'
