from copy import deepcopy
import pytest
from app.schemas import research_knowledge as s
from tests.research_knowledge_fixtures import content, query


@pytest.mark.parametrize('field,value',[('top_k',0),('top_k',9),('top_k',True),('top_k',1.0),('context_version',0),('subject_number',1025),('subject_version',False),('keywords',['x']*9),('keywords',['a'*33]),('keywords',['a','a']),('keywords',['<script>']),('selected',[{}]*33),('purpose','execute'),('clock','2030-01-01T00:00:00Z')])
def test_strict_query_rejections(field,value):
    with pytest.raises(s.KnowledgeError):s.validate(s.QueryInput,query(**{field:value}),s.MAX_QUERY)


@pytest.mark.parametrize('size,ok',[(8192,True),(8193,False)])
def test_exact_query_byte_limit(size,ok):
    raw=s.canonical(query());raw+=b' '*(size-len(raw))
    if ok:assert s.validate(s.QueryInput,raw,s.MAX_QUERY).top_k==8
    else:
        with pytest.raises(s.KnowledgeError,match='knowledge_input_limit'):s.validate(s.QueryInput,raw,s.MAX_QUERY)


@pytest.mark.parametrize('raw',[b'\xef\xbb\xbf{}',b'\xff',b'{"top_k":1,"top_k":2}',b'{"value":NaN}',b'{"value":1e9}',b'{"value":"\\ud800"}',b'{"$ref":"file:///synthetic"}',b'[]\n{}'])
def test_malicious_encoding_and_shape(raw):
    with pytest.raises(s.KnowledgeError):s.validate(s.QueryInput,raw,s.MAX_QUERY)


@pytest.mark.parametrize('nodes,ok',[(2048,True),(2049,False)])
def test_independent_node_limit(nodes,ok):
    raw=b'['+b','.join([b'0']*(nodes-1))+b']'
    if ok:assert len(s.KnowledgeJSON(raw).parse())==nodes-1
    else:
        with pytest.raises(s.KnowledgeError):s.KnowledgeJSON(raw).parse()


@pytest.mark.parametrize('depth,ok',[(5,True),(6,False)])
def test_independent_depth_limit(depth,ok):
    raw=b'['*depth+b'0'+b']'*depth
    if ok:s.KnowledgeJSON(raw).parse()
    else:
        with pytest.raises(s.ObservationError):s.KnowledgeJSON(raw).parse()


def test_ref_and_version_card_boundaries():
    c=content();c['source_refs']=[{**c['source_refs'][0],'reference':{'kind':'synthetic_fixture','fixture_id':n,'version':10000}} for n in range(1,17)]
    assert len(s.validate(s.Content,c).source_refs)==16
    c['source_refs'].append({**c['source_refs'][0],'reference':{'kind':'synthetic_fixture','fixture_id':17,'version':1}})
    with pytest.raises(s.KnowledgeError):s.validate(s.Content,c)


@pytest.mark.parametrize('start,end,ok',[('2031-04-03T12:00:00Z','2031-04-03T13:00:00+00:00',True),('2031-04-03T12:00:00','2031-04-03T13:00:00Z',False),('2031-04-03T12:00:00Z','2031-04-03T05:00:00-07:00',False),('2031-04-03T12:00:00-00:00','2031-04-03T13:00:00Z',False)])
def test_decision_timezone_and_half_open_window(start,end,ok):
    ref={'scope':'project','knowledge_id':'knowledge-1','version':1,'digest':'a'*64}
    p={'reference':ref,'expected_sequence':0,'action':'review','review':{'kind':'synthetic_fixture','fixture_id':1,'version':1},'valid_from':start,'valid_until':end}
    if ok:s.validate(s.DecisionInput,p)
    else:
        with pytest.raises(s.KnowledgeError):s.validate(s.DecisionInput,p)
