from datetime import timedelta
import pytest
from app.schemas import research_intent as s


def test_canonical_domain_vector_and_timestamp_normalization():
    assert s.digest('ra-json-example/1',{'record_id':'7042'})=='d4008f16cf8b2c8de5d5a5f142d2c7fc3f9a2123592bf2c16e7d7d9557ade078'
    assert s.stamp(s.timestamp('2032-04-05T03:01:40-07:00'))=='2032-04-05T10:01:40.000000Z'
    assert s.canonical({'z':False,'a':'é'})=='{"a":"é","z":false}'.encode()
    assert s.digest('x',{})!=s.digest('y',{})


@pytest.mark.parametrize('raw',[b'{"a":1,"a":2}',b'{"a":1.0}',b'{"a":NaN}',b'{"a":Infinity}',b'\xef\xbb\xbf{}',b'"\\ud800"',b'\xff',b'['*1000+b']'*1000])
def test_strict_transport(raw):
    with pytest.raises(s.IntentError):s.parse(raw)


@pytest.mark.parametrize('value',['2032-04-05T00:00:00','2032-04-05T00:00:60Z','2032-04-05T00:00:00.0000001Z','tomorrow'])
def test_strict_time(value):
    with pytest.raises(s.IntentError):s.timestamp(value)


@pytest.mark.parametrize('key,value',[('number',True),('number',0),('number',1025),('version',1.0),('digest','x'),('passed',True)])
def test_reference_strict(key,value):
    p=dict(number=1,version=1,digest='a'*64);p[key]=value
    with pytest.raises(s.IntentError):s.validate(s.Reference,p)


def test_exact_byte_and_node_depth_bounds():
    assert s.parse(b'"'+b'a'*(s.MAX_INPUT-2)+b'"')=='a'*(s.MAX_INPUT-2)
    with pytest.raises(s.IntentError):s.parse(b'"'+b'a'*(s.MAX_INPUT-1)+b'"')
    s.canonical([None]*8191)
    with pytest.raises(s.IntentError):s.canonical([None]*8192)
    s.canonical([[[[[[[[None]]]]]]]])
    with pytest.raises(s.IntentError):s.canonical([[[[[[[[[None]]]]]]]]])


@pytest.mark.parametrize('value',['2032-04-05T00:00:00+00:60','2032-04-05T00:00:00-24:00'])
def test_invalid_rfc3339_offset_is_not_normalized(value):
    with pytest.raises(s.IntentError):s.timestamp(value)


def test_early_year_uses_four_digit_canonical_utc():
    assert s.stamp(s.timestamp('0001-01-01T00:00:00Z'))=='0001-01-01T00:00:00.000000Z'


@pytest.mark.parametrize('value',[True,False,1.0,'1',0,2])
def test_manifest_concurrency_is_a_strict_single_integer(value):
    p=dict(context_version=1,target_id=1,expected_version=0,
        actions=[dict(role=r,subject=dict(number=i,version=1),mapping=dict(number=1,version=1,digest='a'*64)) for i,r in enumerate(('baseline','probe'),1)],
        duration_seconds=300,rate_millirequests_per_second=1000,concurrency=value,
        evidence=dict(kind='synthetic_fixture',fixture_id=3109,version=1))
    with pytest.raises(s.IntentError):s.validate(s.ManifestInput,p)


def test_exact_complete_output_byte_limit():
    value=dict(protocol='ra-w1-receipt/1',reference=dict(number=1,version=1,digest='a'*64),
        kind='mapping',body={'padding':''},eligibility_until='2032-04-05T10:01:40.000000Z',
        audit_id=1,execution_authorized=False,execution_status='w2_dependency_closed')
    remaining=s.MAX_OUTPUT-len(s.output(value))
    value['body']['padding']='x'*remaining
    assert len(s.output(value))==65536
    value['body']['padding']+='x'
    with pytest.raises(s.IntentError,match='response_limit'):s.output(value)


def test_response_envelope_counts_toward_depth_and_nodes():
    value=dict(protocol='ra-w1-receipt/1',reference=dict(number=1,version=1,digest='a'*64),
        kind='mapping',body={'nested':[[[[[[None]]]]]]},eligibility_until='2032-04-05T10:01:40.000000Z',
        audit_id=1,execution_authorized=False,execution_status='w2_dependency_closed')
    s.output(value)  # response body is depth 1; nested value starts at depth 2.
    value['body']['nested']=[value['body']['nested']]
    with pytest.raises(s.IntentError,match='structure_limit'):s.output(value)
    value['body']={'nodes':[None]*8190}
    with pytest.raises(s.IntentError,match='structure_limit'):s.output(value)
