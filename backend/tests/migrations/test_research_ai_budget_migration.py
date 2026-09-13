"""Actual additive W2 schema, immutable history and retained liability."""
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine,inspect,select,text,update
from app.core.config import settings
from app.db.session import engine,SessionLocal
from app.ai.w2 import schema as t
from tests.ai.w2_fixtures import w2,rule_pair,knowledge_pair,subject_pair,two_intake_targets,zero_capabilities  # noqa: F401


def test_empty_round_trip_exact_metadata_and_no_seeded_authority(monkeypatch):
    config=Config('alembic.ini')
    scripts=ScriptDirectory.from_config(config)
    assert scripts.get_heads()==['7ba5dce4a936']
    assert scripts.get_revision('7ba5dce4a936').down_revision=='6a94cbd3f825'
    name='ai_budget_'+uuid4().hex
    with engine.begin() as db:db.execute(text('CREATE SCHEMA '+name))
    url=engine.url.update_query_dict({'options':'-csearch_path='+name})
    isolated=create_engine(url)
    try:
        monkeypatch.setattr(settings,'database_url',url.render_as_string(hide_password=False).replace('%','%%'))
        command.upgrade(config,'6a94cbd3f825')
        before=set(inspect(isolated).get_table_names())
        command.upgrade(config,'head')
        assert set(inspect(isolated).get_table_names())==before|{table.name for table in t.TABLES}
        with isolated.begin() as db:
            assert all(db.scalar(select(text('count(*)')).select_from(table))==0 for table in t.TABLES)
        command.check(config)
        command.downgrade(config,'6a94cbd3f825')
        assert set(inspect(isolated).get_table_names())==before
    finally:
        isolated.dispose()
        with engine.begin() as db:db.execute(text('DROP SCHEMA '+name+' CASCADE'))


def test_populated_downgrade_refused_and_exact_immutable_call_core(w2):
    w2.reopen();rt=w2.runtime()
    with SessionLocal() as db:
        before=bytes(db.scalar(select(t.core.c.core_bytes)))
        with pytest.raises(Exception,match='research_ai_immutable'):
            with db.begin_nested():db.execute(update(t.core).values(core_bytes=b'{}'))
        db.rollback()
    with pytest.raises(Exception,match='research_ai_evidence_retained'):
        command.downgrade(Config('alembic.ini'),'6a94cbd3f825')
    with SessionLocal() as db:
        assert bytes(db.scalar(select(t.core.c.core_bytes)))==before
        assert db.scalar(select(t.reservation.c.held_tokens))==5120
    assert w2.witness.accepted[rt.key.fingerprint()]==0


def test_sql_backstop_rejects_unacknowledged_internal_mutation(w2):
    from app.db.models.research_context import ResearchContext
    with SessionLocal() as db:
        with pytest.raises(Exception,match='research_ai_closure_ack_required'):
            with db.begin_nested():db.execute(update(ResearchContext).where(ResearchContext.id==w2.g['ctx']).values(closed_at=w2.clock.utcnow()))
        db.rollback()
        assert db.get(ResearchContext,w2.g['ctx']).closed_at is None
