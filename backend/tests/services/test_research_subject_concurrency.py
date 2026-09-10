from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from sqlalchemy import event, select, text
from app.db.session import SessionLocal, engine
from app.db.models.research_subject import ResearchSubjectVersion
from app.services import research_subject as service, research_observation as observation
from app.services.research_context import close_context, create_context
from app.schemas.research_subject import SubjectError
from tests.research_subject_fixtures import subject_pair, two_intake_targets, proposal, call, NOW, REF, zero_capabilities  # noqa: F401
from tests.research_intake_fixtures import intake, snapshot
from tests.services.test_research_context_isolation import wait_for_blocker


@pytest.mark.parametrize("first,second",[("record","close"),("close","record"),("record","hold"),("hold","record"),("correct","correct")])
def test_real_transactions_serialize_context_sources_and_corrections(subject_pair,first,second):
    g,_=subject_pair;p=proposal(g);p["sources"]=[{"observation_id":g["observation"],"source_entry_index":0}]
    if first=="correct":call(service.record,g["ctx"],1,p)
    ready,release,waiting=Event(),Event(),Event();pids={}
    def worker(action,leader):
        with SessionLocal() as db:
            pids[leader]=db.scalar(text("SELECT pg_backend_pid()"))
            if not leader:waiting.set()
            try:
                if action=="close":result=close_context(db,1,g["ctx"],{"expected_version":1,"closure_reference":REF},now=NOW)
                elif action=="hold":
                    from datetime import timedelta
                    result=observation.lifecycle(db,1,g["ctx"],g["observation"],"hold",{"review":REF,"reason":"synthetic_review","until":(NOW+timedelta(days=2)).isoformat()},now=NOW)
                elif action=="correct":result=service.record(db,1,g["ctx"],1,{"expected_version":1,"correction_reference":REF,"proposal":p},correction=True,now=NOW)
                else:result=service.record(db,1,g["ctx"],1,p,now=NOW)
            except SubjectError as exc:result=exc.code
            if leader:ready.set();assert release.wait(10)
            db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(worker,first,True);assert ready.wait(10)
        b=pool.submit(worker,second,False);assert waiting.wait(10)
        try:wait_for_blocker(pids[False],pids[True])
        finally:release.set()
        ra,rb=a.result(15),b.result(15)
    if first=="correct":
        assert isinstance(ra,dict) and rb=="research_subject_version_conflict"
        with SessionLocal() as db:assert len(list(db.scalars(select(ResearchSubjectVersion).where(ResearchSubjectVersion.context_id==g["ctx"]))))==2
    elif first=="record":
        assert isinstance(ra,dict) and isinstance(rb,dict)
        assert call(service.read,g["ctx"],1)["availability"]=="unavailable"
    else:
        assert isinstance(ra,dict) and rb=="research_subject_unavailable"


def test_closed_transferred_history_never_reads_new_project_metadata(subject_pair):
    from app.db.models import Target, TestIdentity
    g,_=subject_pair
    call(service.record,g["ctx"],1,proposal(g))
    call(close_context,g["ctx"],{"expected_version":1,"closure_reference":REF})
    with SessionLocal() as db:
        create_context(db,{"project_number":3,"intake":intake(g)},now=NOW)
        db.get(TestIdentity,g["anonymous"]).auth_type="bearer"
        db.get(Target,g["target"]).base_url="http://127.0.0.1:58125"
        db.commit()
    before=snapshot();statements=[]
    def capture(conn,cursor,statement,*args):statements.append(statement.lower())
    event.listen(engine,"before_cursor_execute",capture)
    try:
        for version in (None,1):
            result=call(service.read,g["ctx"],1,version=version)
            assert result["availability"]=="unavailable" and result["proposal"] is None and result["facts"] is None
    finally:event.remove(engine,"before_cursor_execute",capture)
    assert not any("from "+table in s for s in statements for table in ("targets","test_identities","resources","credential_bindings","scopes","authorization_revisions"))
    assert snapshot()==before


def test_credential_update_waits_for_w3_metadata_transaction(subject_pair):
    from pydantic import SecretStr
    from app.credentials.bearer import BearerCredentialService
    from app.db.models.credential_binding import CredentialBinding
    g,_=subject_pair
    with SessionLocal() as db:
        BearerCredentialService(db=db).update(identity_id=g["bearer"],token=SecretStr("synthetic-w3-before"));db.commit()
        bid=db.scalar(select(CredentialBinding.id).where(CredentialBinding.test_identity_id==g["bearer"]))
    p=proposal(g);p.update(identity_choice="bearer",test_identity_id=g["bearer"],credential_binding_id=bid,
                          session_state="unknown",credential_update="needed")
    ready,release,waiting=Event(),Event(),Event();pids={}
    def reader():
        with SessionLocal() as db:
            pids[True]=db.scalar(text("SELECT pg_backend_pid()"))
            result=service.record(db,1,g["ctx"],1,p,now=NOW)
            ready.set();assert release.wait(10);db.commit();return result
    def updater():
        with SessionLocal() as db:
            pids[False]=db.scalar(text("SELECT pg_backend_pid()"));waiting.set()
            BearerCredentialService(db=db).update(identity_id=g["bearer"],token=SecretStr("synthetic-w3-after"));db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(reader);assert ready.wait(10)
        b=pool.submit(updater);assert waiting.wait(10)
        try:wait_for_blocker(pids[False],pids[True])
        finally:release.set()
        assert a.result(15)["status"]=="NEEDS_INPUT";b.result(15)
    result=call(service.read,g["ctx"],1)
    assert "credential_changed" in result["missing_inputs"]
    assert "synthetic-w3-before" not in str(result) and "synthetic-w3-after" not in str(result)
