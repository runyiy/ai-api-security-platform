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


@pytest.mark.parametrize('recovery',['release','expiry'])
@pytest.mark.parametrize('intermediate_read',[False,True])
def test_rule_only_source_hold_permanently_invalidates_intent(intent_rule_graph,qualified_future,recovery,intermediate_read):
    from tests.research_knowledge_fixtures import source
    from app.schemas.research_knowledge import KnowledgeError
    from tests.services.test_research_intent_sources import hold_source,recover_source
    from datetime import timedelta
    g=intent_rule_graph;approve(g)
    ref=rule(g,source_refs=[source(g)])
    publish(g,ref,validate(g,ref)['validation_ref'])
    old=call(service.convert,g,1,conversion(g,knowledge=ref))
    hold_source(g,g['observation'])
    if intermediate_read:
        before=snapshot()
        with pytest.raises((IntentError,KnowledgeError)):
            call(service.read,g,'intent',old['reference'],now=NOW+timedelta(seconds=2))
        assert snapshot()==before
    at=recover_source(g,g['observation'],recovery)
    # The manifest has no dependency on this rule-only observation.
    assert call(service.read,g,'manifest',g['manifest'],now=at)['reference']==g['manifest']
    before=snapshot()
    with pytest.raises(IntentError,match='intent_dependency_changed'):
        call(service.read,g,'intent',old['reference'],now=at)
    assert snapshot()==before
