from datetime import timedelta
import pytest
from sqlalchemy import event, select
from app.db.session import SessionLocal, engine
from app.db.models.research_subject import ResearchSubjectVersion
from app.services import research_subject as service, research_observation as observation
from app.schemas.research_subject import SubjectError
from tests.research_subject_fixtures import subject_pair, two_intake_targets, proposal, assertion, call, NOW, REF, zero_capabilities  # noqa: F401
from tests.research_intake_fixtures import snapshot


def record(g, p=None, number=1, **kwargs):
    return call(service.record,g["ctx"],number,p or proposal(g),project=g["project"],**kwargs)


def read(g, number=1, **kwargs):
    return call(service.read,g["ctx"],number,project=g["project"],**kwargs)


@pytest.mark.parametrize("relationship,access", [("owner","denied"),("non_owner","allowed"),("shared","allowed")])
def test_independent_verified_facts_no_owner_baseline(subject_pair, relationship, access):
    g,_=subject_pair
    aid=assertion(g,relationship,access)
    p=proposal(g);p.update(relationship=relationship,expected_access=access,fact_reference=REF,assertion_ids=[aid])
    before=snapshot(legacy=True)
    result=record(g,p)
    assert result["facts"] == {"state":"resolved","relationship":relationship,"expected_access":access,"supporting_assertion_ids":[aid]}
    assert result["status"]=="NEEDS_INPUT" and not result["execution_authorized"]
    assert "owner_unknown" in result["missing_inputs"] and "membership_unverified" in result["missing_inputs"]
    assert snapshot(legacy=True)==before


def test_proposed_facts_are_not_verified_and_unknown_owner_is_not_created(subject_pair):
    g,_=subject_pair
    p=proposal(g);p.update(resource_id=None,relationship="owner",expected_access="denied",fact_reference=REF)
    before=snapshot(legacy=True)
    result=record(g,p)
    assert result["proposal"]["expected_access"]=="denied" and result["facts"]["expected_access"]=="unspecified"
    assert {"owner_unknown","resource_missing","facts_missing"} <= set(result["missing_inputs"])
    assert snapshot(legacy=True)==before


def test_all_verified_conflicts_resolve_without_selected_id_filter(subject_pair):
    g,_=subject_pair
    a=assertion(g,"owner","allowed");b=assertion(g,"owner","denied")
    p=proposal(g);p["assertion_ids"]=[a]
    result=record(g,p)
    assert result["facts"]["state"]=="conflict" and result["facts"]["supporting_assertion_ids"]==[a,b]
    assert "facts_conflict" in result["missing_inputs"]


@pytest.mark.parametrize("field", ["target_id","test_identity_id","owner_identity_id","resource_id","endpoint_id","binding_id","assertion_ids","sources","credential_binding_id"])
def test_foreign_and_absent_references_are_indistinguishable_and_atomic(subject_pair, field):
    from app.db.models.credential_binding import CredentialBinding
    a,b=subject_pair
    foreign_assertion=assertion(b)
    with SessionLocal() as db:
        binding=CredentialBinding(test_identity_id=b["bearer"],auth_type="bearer",source_type="stored_secret",is_active=True)
        db.add(binding);db.commit();bid=binding.id
    foreign={"target_id":b["target"],"test_identity_id":b["anonymous"],"owner_identity_id":b["bearer"],"resource_id":b["resource"],
        "endpoint_id":b["endpoint"],"binding_id":b["slot"],"assertion_ids":[foreign_assertion],
        "sources":[{"observation_id":b["observation"],"source_entry_index":0}],"credential_binding_id":bid}
    errors=[]
    for exists in (True,False):
        p=proposal(a)
        if field=="credential_binding_id":
            p.update(identity_choice="bearer",test_identity_id=a["bearer"],session_state="unknown",credential_update="needed")
        p[field]=foreign[field] if exists else ([2147483647] if field=="assertion_ids" else
            [{"observation_id":2147483647,"source_entry_index":0}] if field=="sources" else 2147483647)
        before=snapshot()
        with pytest.raises(SubjectError) as exc:
            with SessionLocal() as db:
                try: service.record(db,1,a["ctx"],1,p,now=NOW)
                except SubjectError:
                    db.commit();raise
        errors.append((exc.value.code,exc.value.status))
        assert snapshot()==before
    assert errors==[("research_subject_unavailable",409)]*2


@pytest.mark.parametrize("action", ["hold","delete","quarantine","expired","revoke","suspend","close"])
def test_source_lifecycle_suppresses_latest_and_history_without_copy(subject_pair,action):
    from app.services.research_context import close_context
    g,_=subject_pair;p=proposal(g)
    p["sources"]=[{"observation_id":g["observation"],"source_entry_index":0}]
    record(g,p)
    correction={"expected_version":1,"correction_reference":REF,"proposal":p}
    record(g,correction,correction=True)
    at=NOW
    if action in {"hold","delete","quarantine"}:
        payload={"review":REF}
        if action=="hold":payload.update(reason="synthetic_review",until=(NOW+timedelta(days=2)).isoformat())
        call(observation.lifecycle,g["ctx"],g["observation"],action,payload)
    elif action=="revoke":call(observation.revoke_preparation,g["ctx"],"preparation_1",{"review":REF})
    elif action=="suspend":call(observation.maintain,g["ctx"],{"action":"suspend","review":REF})
    elif action=="close":call(close_context,g["ctx"],{"expected_version":1,"closure_reference":REF})
    else:at=NOW+timedelta(days=30)
    before=snapshot()
    for version in (None,1):
        result=read(g,version=version,now=at)
        assert result["availability"]=="unavailable" and result["proposal"] is None and result["facts"] is None
    assert snapshot()==before
    with pytest.raises(SubjectError):record(g,p,number=2,now=at)
    correction["expected_version"]=2;correction["proposal"]={**p,"sources":[]}
    with pytest.raises(SubjectError):record(g,correction,correction=True,now=at)
    assert snapshot()==before
    with SessionLocal() as db:
        rows=list(db.scalars(select(ResearchSubjectVersion).where(ResearchSubjectVersion.context_id==g["ctx"])))
        assert len(rows)==2 and all("path_template" not in str(r.proposal) for r in rows)


@pytest.mark.parametrize("session", ["unknown","expired","login_page","mfa_required","authentication_failed","operator_reported_valid"])
def test_bearer_sessions_never_become_anonymous_or_health_proof(subject_pair,session):
    g,_=subject_pair;p=proposal(g)
    p.update(identity_choice="bearer",test_identity_id=g["bearer"],session_state=session,credential_update="needed")
    if session!="unknown":p.update(session_reported_at=NOW.isoformat(),session_reference=REF)
    result=record(g,p)
    assert result["proposal"]["identity_choice"]=="bearer"
    assert {"session_health_unverified","credential_update_needed"} <= set(result["missing_inputs"])
    p["identity_choice"]="anonymous";p["credential_update"]="not_applicable"
    with pytest.raises(SubjectError):record(g,p,number=2)


def test_existing_token_update_boundary_changes_metadata_not_health(subject_pair):
    from app.credentials.bearer import BearerCredentialService
    from app.db.models.credential_binding import CredentialBinding
    from pydantic import SecretStr
    g,_=subject_pair
    with SessionLocal() as db:
        BearerCredentialService(db=db).update(identity_id=g["bearer"],token=SecretStr("synthetic-w3-token-first"));db.commit()
        bid=db.scalar(select(CredentialBinding.id).where(CredentialBinding.test_identity_id==g["bearer"]))
    p=proposal(g);p.update(identity_choice="bearer",test_identity_id=g["bearer"],credential_binding_id=bid,
        session_state="operator_reported_valid",session_reported_at=NOW.isoformat(),session_reference=REF,credential_update="operator_reported_updated")
    statements=[]
    def capture(conn,cursor,statement,*args):statements.append(statement.lower())
    event.listen(engine,"before_cursor_execute",capture)
    try: result=record(g,p);read(g)
    finally:event.remove(engine,"before_cursor_execute",capture)
    assert not any("test_identities.credentials" in s or "encrypted_envelope" in s for s in statements)
    assert "synthetic-w3-token" not in str(result) and "session_health_unverified" in result["missing_inputs"]
    with SessionLocal() as db:
        BearerCredentialService(db=db).update(identity_id=g["bearer"],token=SecretStr("synthetic-w3-token-second"));db.commit()
    assert "credential_changed" in read(g)["missing_inputs"]


def test_append_correction_retains_history_and_conflict_rollback(subject_pair):
    g,_=subject_pair;p=proposal(g);old=record(g,p)
    p.update(expected_access="denied",fact_reference=REF)
    record(g,{"expected_version":1,"correction_reference":REF,"proposal":p},correction=True)
    assert read(g,version=1)["proposal"]==old["proposal"]
    assert read(g)["version_number"]==2
    before=snapshot()
    with pytest.raises(SubjectError,match="version_conflict"):
        record(g,{"expected_version":1,"correction_reference":REF,"proposal":p},correction=True)
    assert snapshot()==before


def test_missing_identity_is_explicit_gap(subject_pair):
    g,_=subject_pair;p=proposal(g);p.update(identity_choice="missing",test_identity_id=None,session_state="unknown")
    result=record(g,p)
    assert "identity_missing" in result["missing_inputs"] and result["proposal"]["identity_choice"]=="missing"


@pytest.mark.parametrize("count",[256,257])
def test_resolver_exact_and_excess_fact_limit(subject_pair,count):
    from app.db.models.resource_access_assertion import ResourceAccessAssertion
    g,_=subject_pair
    with SessionLocal() as db:
        db.execute(ResourceAccessAssertion.__table__.insert(),[dict(resource_id=g["resource"],test_identity_id=g["anonymous"],
            relationship="shared",expected_access="allowed",provenance="target_fixture",confidence=80,
            verification_state="verified",asserted_at=NOW) for _ in range(count)]);db.commit()
    before=snapshot()
    if count==256:
        assert len(record(g)["facts"]["supporting_assertion_ids"])==256
    else:
        with pytest.raises(SubjectError,match="facts_limit"):record(g)
        assert snapshot()==before


def test_context_capacity_exact_and_over_boundary(subject_pair):
    g,_=subject_pair;p=proposal(g)
    with SessionLocal() as db:
        db.execute(ResearchSubjectVersion.__table__.insert(),[dict(context_id=g["ctx"],target_id=g["target"],context_version=1,
            proposal_number=i,version_number=1,proposal=p,provenance="operator_proposed_unverified",recorded_at=NOW) for i in range(1,1024)])
        db.commit()
    assert record(g,number=1024)["proposal_number"]==1024
    before=snapshot()
    with pytest.raises(SubjectError,match="capacity_exceeded"):
        record(g,{"expected_version":1,"correction_reference":REF,"proposal":p},correction=True)
    assert snapshot()==before


@pytest.mark.parametrize("shape",["query","nested","body","mutation"])
def test_unsupported_shapes_remain_visible(subject_pair,shape):
    from app.db.models import Endpoint
    from app.db.models.endpoint_resource_binding import EndpointResourceBinding
    g,_=subject_pair
    with SessionLocal() as db:
        endpoint=db.get(Endpoint,g["endpoint"]);slot=db.get(EndpointResourceBinding,g["slot"])
        if shape=="query":slot.location="query";endpoint.parameters=[{"name":"resource_id","in":"query"}]
        elif shape=="body":slot.location="body";slot.selector="/resource_id"
        elif shape=="nested":endpoint.path="/projects/{project_id}/folders/{resource_id}"
        else:endpoint.method="POST"
        db.commit()
    result=record(g)
    assert ("slot_unavailable" if shape=="body" else "preview_only_shape") in result["missing_inputs"]
    assert not result["execution_preparation_allowed"]


def test_saturated_source_audit_does_not_report_successful_read(subject_pair):
    from tests.services.test_research_observation_lifecycle import fill_audit
    g,_=subject_pair;p=proposal(g);p["sources"]=[{"observation_id":g["observation"],"source_entry_index":0}]
    record(g,p);fill_audit(g["ctx"],g["observation"]);before=snapshot()
    with pytest.raises(SubjectError,match="audit_unavailable"):read(g)
    assert snapshot()==before


def test_w2_tombstone_cleanup_not_blocked_by_proposal_history(subject_pair):
    g,_=subject_pair;p=proposal(g);p["sources"]=[{"observation_id":g["observation"],"source_entry_index":0}]
    record(g,p)
    call(observation.lifecycle,g["ctx"],g["observation"],"delete",{"review":REF})
    result=call(observation.maintain,g["ctx"],{"action":"reconcile","review":REF},now=NOW+timedelta(days=90))
    assert result["removed_tombstones"]==1
    assert read(g,now=NOW+timedelta(days=90))["availability"]=="unavailable"
    with SessionLocal() as db:assert db.scalar(select(ResearchSubjectVersion.id).where(ResearchSubjectVersion.context_id==g["ctx"]))
