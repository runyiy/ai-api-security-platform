from unittest.mock import Mock
import pytest
from sqlalchemy import select
from app.db.session import SessionLocal
from app.db.models import TestCase, ExecutionPlan, TestRun, Finding, Resource
from app.services import research_intent as service
from app.services.execution_plan import create_execution_plan, PlanActionInput
from app.services.test_case_planning import create_test_case_execution_plan
from app.services.execution_plan_approval import record_plan_decision, is_plan_approved
from app.services.plan_execution import PlanExecutionService
from app.services.test_execution import TestExecutionService
from app.services.finding_analysis import FindingAnalysisService
from app.services.security_report import SecurityReportService
from tests.research_intent_fixtures import (intent_graph,subject_pair,two_intake_targets,qualified_future,
    call,approve,conversion,NOW,REF)  # noqa: F401
from tests.research_intake_fixtures import snapshot


@pytest.mark.parametrize('entry',['direct','planner','creator','approval','approved','exact'])
@pytest.mark.parametrize('tamper',[False,True])
def test_new_records_never_fall_back_to_legacy(intent_graph,qualified_future,entry,tamper):
    g=intent_graph;approve(g);value=call(service.convert,g,1,conversion(g));member=value['body']['link']['members'][1]
    with SessionLocal() as db:
        case=db.get(TestCase,member['test_case_id']);plan=db.get(ExecutionPlan,member['plan_id'])
        if tamper:
            case.test_type='bola_cross_owner'
            # Durable member still identifies a new-purpose TestCase, even without its marker.
            db.commit()
        before=snapshot();executor=Mock()
        def action():
            if entry=='direct':return TestExecutionService(db=db,executor=executor).execute(test_case_id=case.id)
            if entry=='planner':return create_test_case_execution_plan(db,test_case_id=case.id,credential_binding_id=g['credential'])
            if entry=='creator':return create_execution_plan(db,target_id=g['target'],authorization_revision_id=g['revision'],actor_identity_id=g['bearer'],credential_binding_id=g['credential'],actions=[PlanActionInput('GET',plan.actions[0].url,case.id,g['resource'])])
            if entry=='approval':return record_plan_decision(db,execution_plan_id=plan.id,decision='approved')
            if entry=='approved':return is_plan_approved(db,plan.id)
            return PlanExecutionService(db=db,executor=executor).execute(execution_plan_id=plan.id)
        if entry=='approved':assert action() is False
        else:
            with pytest.raises(Exception,match='intent_w2_execution_closed'):action()
        executor.execute.assert_not_called();db.rollback()
    assert snapshot()==before

from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401
from tests.api.test_finding_evidence_fingerprints import old_evidence


@pytest.mark.parametrize('side',['probe_case','baseline_case'])
def test_new_types_rejected_by_legacy_analysis(evidence_pair,side):
    before=snapshot()
    with SessionLocal() as db:
        db.get(TestCase,evidence_pair[side]).test_type='ra_intent_get_v1'
        with pytest.raises(Exception,match='intent_w2_execution_closed'):
            FindingAnalysisService(db=db).analyze_test_run(test_run_id=evidence_pair['probe'],baseline_test_run_id=evidence_pair['baseline'])
        db.rollback()
    assert snapshot()==before


@pytest.mark.parametrize("side",["probe_case","baseline_case"])
def test_new_types_rejected_by_formal_report(evidence_pair,side):
    finding_id,_=old_evidence(evidence_pair);before=snapshot()
    with SessionLocal() as db:
        db.get(TestCase,evidence_pair[side]).test_type='ra_intent_get_v1'
        with pytest.raises(Exception,match='intent_w2_execution_closed'):
            SecurityReportService(db=db).generate(finding_id=finding_id)
        db.rollback()
    assert snapshot()==before


@pytest.mark.parametrize("side",["probe_case","baseline_case"])
def test_new_types_rejected_before_ai_advisory(evidence_pair,side):
    from app.services.ai_analysis import AIAnalysisService
    finding_id,_=old_evidence(evidence_pair);before=snapshot();provider=Mock()
    with SessionLocal() as db:
        db.get(TestCase,evidence_pair[side]).test_type='ra_intent_get_v1'
        with pytest.raises(Exception,match='intent_w2_execution_closed'):
            AIAnalysisService(db=db,provider=provider).analyze_finding(finding_id=finding_id)
        db.rollback()
    assert provider.mock_calls==[] and snapshot()==before


@pytest.mark.parametrize('tamper',[False,True])
def test_observed_access_derivation_rejects_new_members(intent_graph,qualified_future,tamper):
    from app.services.observed_access_assertion import derive_observed_access_assertion
    g=intent_graph;approve(g);value=call(service.convert,g,1,conversion(g));member=value['body']['link']['members'][0]
    before=snapshot()
    with SessionLocal() as db:
        case=db.get(TestCase,member['test_case_id'])
        if tamper:case.test_type='owner_baseline';case.expected_statuses=[200]
        run=TestRun(test_case_id=case.id,request_data={},response_status=200,executed_at=NOW)
        db.add(run);db.flush()
        with pytest.raises(Exception,match='intent_w2_execution_closed'):
            derive_observed_access_assertion(db,run.id)
        db.rollback()
    assert snapshot()==before
