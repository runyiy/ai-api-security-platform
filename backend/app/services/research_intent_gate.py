"""No new-purpose action may fall back to a legacy consumer before RA-04/W2."""
from sqlalchemy import select, text
from app.db.models.test_case import TestCase
from app.db.models.test_run import TestRun
from app.db.models.plan_action import PlanAction
from app.db.models.research_intent import IntentPlanMember


def _members_exist(db):
    # Older migration compatibility tests deliberately run a legacy-only schema.
    # Do not cache this across transactions, upgrades or rollbacks.
    return db.scalar(text("SELECT to_regclass('research_intent_plan_members')")) is not None


def new_case(db, case):
    if case is None:
        return False
    if case.test_type.startswith('ra_'):
        return True
    return _members_exist(db) and db.scalar(select(IntentPlanMember.id)
        .where(IntentPlanMember.test_case_id==case.id).limit(1)) is not None


def new_plan(db, plan):
    if isinstance(plan.policy_context,dict) and 'research_intent' in plan.policy_context:
        return True
    if _members_exist(db) and db.scalar(select(IntentPlanMember.id)
        .where(IntentPlanMember.plan_id==plan.id).limit(1)) is not None:
        return True
    cases=db.scalars(select(TestCase).join(PlanAction,PlanAction.test_case_id==TestCase.id)
        .where(PlanAction.execution_plan_id==plan.id).limit(101))
    return any(new_case(db,case) for case in cases)


def reject_case(db, case, error):
    if new_case(db,case):
        raise error('intent_w2_execution_closed')


def reject_plan(db, plan, error):
    if new_plan(db,plan):
        raise error('intent_w2_execution_closed')


def reject_baseline(db, finding, error):
    """Legacy report/advisory must not consume a new-purpose baseline either."""
    if finding.baseline_test_run_id is not None:
        run = db.get(TestRun, finding.baseline_test_run_id)
        if run is not None:
            reject_case(db, db.get(TestCase, run.test_case_id), error)
