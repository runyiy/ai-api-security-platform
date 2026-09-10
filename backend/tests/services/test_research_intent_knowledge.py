import pytest
from sqlalchemy import select,delete
from app.db.session import SessionLocal
from app.db.models.research_rule_validation import RuleValidation,RuleFeedback,RuleFeedbackReview
from app.db.models.research_knowledge import KnowledgeVersion,KnowledgeEvent,KnowledgeAudit
from app.services import research_intent as service
from app.schemas.research_intent import IntentError
from tests.research_rule_fixtures import rule,validate,publish
from tests.research_knowledge_fixtures import published,content,decision
from tests.research_intent_fixtures import (intent_graph,subject_pair,two_intake_targets,qualified_future,
    call,approve,conversion,NOW)  # noqa: F401
from tests.research_intake_fixtures import snapshot


@pytest.fixture
def intent_rule_graph(intent_graph):
    g=intent_graph
    try:yield g
    finally:
        with SessionLocal() as db:
            ids=select(KnowledgeVersion.id).where(KnowledgeVersion.context_id==g['ctx'])
            db.execute(delete(RuleValidation).where(RuleValidation.version_id.in_(ids)))
            db.execute(delete(KnowledgeEvent).where(KnowledgeEvent.version_id.in_(ids)))
            db.execute(delete(KnowledgeVersion).where(KnowledgeVersion.context_id==g['ctx']))
            db.execute(delete(KnowledgeAudit).where(KnowledgeAudit.context_id==g['ctx']));db.commit()


@pytest.mark.parametrize('kind',['candidate','synthetic','genuine','disabled','wrong_digest','mechanism'])
def test_exact_knowledge_qualification(intent_rule_graph,qualified_future,kind):
    g=intent_rule_graph;approve(g)
    if kind in {'synthetic','mechanism'}:ref=published(g,content(category='mechanism' if kind=='mechanism' else 'rule'))
    else:
        ref=rule(g)
        if kind!='candidate':publish(g,ref,validate(g,ref)['validation_ref'])
        if kind=='disabled':decision(g,ref,'disable',3)
        if kind=='wrong_digest':ref={**ref,'digest':'a'*64}
    before=snapshot()
    if kind=='genuine':
        value=call(service.convert,g,1,conversion(g,knowledge=ref))
        assert value['body']['knowledge']['reference']==ref
        assert value['body']['knowledge']['validation_ref']['validation_id']>0
    else:
        with pytest.raises(Exception):call(service.convert,g,1,conversion(g,knowledge=ref))
        assert snapshot()==before
