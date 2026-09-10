import copy
import json
from datetime import timedelta
import pytest
from app.schemas.research_observation import BoundedJSON, ObservationError, canonical, entry_size, parse_observation, PreparationInput, validate
from tests.research_observation_fixtures import observation, preparation, NOW


def parse(v):
    return parse_observation(canonical(v), NOW)


def test_exact_independent_input_entry_depth_node_bounds():
    raw = canonical(observation())
    assert parse_observation(raw+b' '*(262144-len(raw)), NOW).entries[0].actor_ref is None
    with pytest.raises(ObservationError, match="observation_input_limit"):
        BoundedJSON(raw+b' '*(262145-len(raw)))
    # Independent parser budgets: these intentionally are NOT observation schemas.
    for n in (4096, 4097):
        value = "x"*(n-2)
        assert len(canonical(value)) == n
        if n == 4096:
            entry_size(value)
        else:
            with pytest.raises(ObservationError, match="observation_entry_limit"):
                entry_size(value)
    assert BoundedJSON(b'[[[[[0]]]]]').parse() == [[[[[0]]]]]
    with pytest.raises(ObservationError, match="observation_structure_limit"):
        BoundedJSON(b'[[[[[[0]]]]]]').parse()
    raw = b'['+b','.join([b'0']*16383)+b']'
    p = BoundedJSON(raw)
    assert len(p.parse()) == 16383 and p.nodes == 16384
    with pytest.raises(ObservationError, match="observation_structure_limit"):
        BoundedJSON(raw[:-1]+b',0]').parse()


@pytest.mark.parametrize("count", [0, 1, 128, 129])
def test_entry_count_separate_from_bytes(count):
    v = observation()
    e = v["entries"][0]
    v["entries"] = [dict(e, entry_ref=f"entry_{i+1}", source_entry_index=i) for i in range(count)]
    assert len(canonical(v)) < 262144
    if count in (1, 128):
        assert len(parse(v).entries) == count
    else:
        with pytest.raises(ObservationError):
            parse(v)


@pytest.mark.parametrize("raw", [b'', b'\xef\xbb\xbf{}', b'\xff', b'{"x":"\\ud800"}', b'{"\\udfff":0}',
    b'{"x":1,"x":2}', b'{"a":{"x":0,"x":1}}', b'NaN', b'Infinity', b'1e9999', b'1.0', b'01',
    b'9223372036854775808', b'{}\n{}', b'a: 1', b'[1,]', b'{"x":0,}', b'{"x":\x00}', b'\x1f\x8b',
    b'"unclosed', b'[', b'{', b'{"x" 1}', b'null trailing'])
def test_malformed_bytes_no_echo(raw):
    with pytest.raises(ObservationError) as exc:
        BoundedJSON(raw).parse()
    assert str(exc.value).startswith("observation_") and len(str(exc.value)) < 80


@pytest.mark.parametrize("location,field,value", [
    ("root", "$ref", "file:///secret"), ("root", "source_url", "http://untrusted.invalid"),
    ("entry", "headers", {"Authorization": "canary"}), ("response", "body", "<script>canary</script>"),
    ("entry", "entry_ref", "person@example.invalid"), ("entry", "entry_ref", "x\n"),
    ("entry", "source_entry_index", True), ("entry", "source_entry_index", -1),
    ("entry", "source_entry_index", 1000000), ("entry", "source_entry_index", "1"),
    ("entry", "method", "POST"), ("entry", "origin", "http://a:b@127.0.0.1:58123"),
    ("entry", "origin", "http://127.0.0.1:58123/"), ("entry", "origin", "http://127.0.0.1:058123"),
    ("entry", "origin", "http://EXAMPLE:80"), ("entry", "path_template", "/x/../x"),
    ("entry", "path_template", "/x/%2f"), ("entry", "path_template", "/x//y"),
    ("entry", "path_template", "/x/"), ("entry", "path_template", "/{id}/{id}"),
    ("entry", "query_names", ["a", "a"]), ("entry", "query_names", ["x"]*17),
    ("entry", "resource_labels", []), ("response", "status_code", 600),
    ("response", "object_labels", ["resource_2"]), ("response", "capture_state", "missing"),
    ("response", "media_kind", "html"), ("response", "status_code", True),
])
def test_strict_fields_and_values(location, field, value):
    v = observation()
    node = v if location == "root" else v["entries"][0] if location == "entry" else v["entries"][0]["response"]
    node[field] = value
    with pytest.raises(ObservationError):
        parse(v)


@pytest.mark.parametrize("value", ["2031-04-03T12:00:00", "2031-04-03T12:00:00-00:00", "2031-04-03T12:00:60Z",
    "2031-04-03T12:00:00.1234567Z", "2031-04-03T12:00:01Z", "2031-04-03T12:00:00+99:00"])
def test_invalid_times(value):
    v = observation()
    v["prepared_at"] = value
    with pytest.raises(ObservationError):
        parse(v)


def test_observed_after_prepared_and_timezone_equivalence():
    v = observation()
    v["entries"][0]["observed_at"] = "2031-04-03T05:00:00-07:00"
    assert parse(v).entries[0].observed_at.endswith("-07:00")
    v["entries"][0]["observed_at"] = "2031-04-03T12:00:00.000001Z"
    with pytest.raises(ObservationError):
        parse(v)


@pytest.mark.parametrize("media,capture,session", [("html", "complete", "login_page"), ("json_array", "truncated", "expired"), ("unknown", "missing", "unknown")])
def test_uncertainty_is_preserved(media, capture, session):
    v = observation()
    v["entries"][0]["response"].update(media_kind=media, capture_state=capture, session_state=session, object_labels=[], status_code=None)
    assert parse(v).entries[0].response.session_state == session


def test_all_required_fields_and_duplicate_entry_identifiers():
    v = observation()
    for level in (v, v["entries"][0], v["entries"][0]["response"]):
        for key in list(level):
            old = level.pop(key)
            with pytest.raises(ObservationError):
                parse(v)
            level[key] = old
    v["entries"].append(copy.deepcopy(v["entries"][0]))
    with pytest.raises(ObservationError):
        parse(v)
    v["entries"][1]["entry_ref"] = "entry_2"
    with pytest.raises(ObservationError):
        parse(v)


@pytest.mark.parametrize("field,value", [("data_eligibility", "reviewed-minimized-private"), ("actor_refs", ["alice"]),
    ("resource_labels", ["secret_token_canary"]), ("path_templates", ["/alice/{resource_id}"]),
    ("query_names", ["password"]), ("retention_seconds", None), ("retention_seconds", True), ("retention_seconds", 2592001)])
def test_registry_cannot_self_qualify_arbitrary_values(field, value):
    p = preparation({"target": 1})
    p[field] = value
    with pytest.raises(ObservationError):
        validate(PreparationInput, p)


@pytest.mark.parametrize("field,ok,bad", [
    ("origin", "http://[::1]:65535", "http://[::1]:65536"),
    ("path_template", "/"+"a"*511, "/"+"a"*512),
    ("entry_ref", "a"*64, "a"*65),
    ("source_entry_index", 999999, 1000000),
    ("query_names", ["q"+str(i) for i in range(16)], ["q"+str(i) for i in range(17)]),
    ("path_template", "/"+"/".join("{s"+str(i)+"}" for i in range(8)), "/"+"/".join("{s"+str(i)+"}" for i in range(9))),
])
def test_independent_field_boundary_pairs(field, ok, bad):
    v = observation()
    v["entries"][0][field] = ok
    parse(v)
    v["entries"][0][field] = bad
    with pytest.raises(ObservationError):
        parse(v)


def test_maximum_aware_time_and_invalid_offset_minutes():
    v = observation()
    v["prepared_at"] = "2031-04-03T11:00:00.123456+01:00"
    v["entries"][0]["observed_at"] = "2031-04-03T10:00:00Z"
    parse(v)
    v["prepared_at"] = "2031-04-03T11:00:00+00:60"
    with pytest.raises(ObservationError):
        parse(v)


def test_remaining_independent_value_limits():
    from app.schemas.research_observation import ResponseFacts
    v = observation()
    e = v["entries"][0]
    e["origin"] = 'http://' + '.'.join(['a'*63]*3+['b'*54])+':80'
    assert len(e["origin"]) == 256
    parse(v)
    e["origin"] = e["origin"].replace('b'*54, 'b'*55)
    with pytest.raises(ObservationError):
        parse(v)
    e["origin"] = observation()["entries"][0]["origin"]
    e["query_names"] = ['q'*64]
    parse(v)
    e["query_names"] = ['q'*65]
    with pytest.raises(ObservationError):
        parse(v)
    e["query_names"] = []
    e["resource_labels"] = [f'resource_{i+1}' for i in range(8)]
    e["response"]["object_labels"] = list(e["resource_labels"])
    parse(v)
    e["response"]["object_labels"].append('resource_9')
    with pytest.raises(ObservationError):
        validate(ResponseFacts, e['response'])
    e["response"]["object_labels"] = []
    e["resource_labels"].append('resource_9')
    with pytest.raises(ObservationError):
        parse(v)
    e["resource_labels"] = ['resource_1']
    for code in (100, 599):
        e['response']['status_code'] = code
        parse(v)
    for code in (99, 600):
        e['response']['status_code'] = code
        with pytest.raises(ObservationError):
            parse(v)


def test_preparation_limits_and_synthetic_coverage_catalog():
    p = preparation({'target': 1})
    p['retention_seconds'] = 1
    p['path_templates'] = ['/', '/'+'/'.join('{slot_'+str(i)+'}' for i in range(1,9))]
    p['query_names'] = [f'query_{i}' for i in range(1,17)]
    for field, prefix in [('batch_refs','batch'), ('entry_refs','entry'), ('actor_refs','actor'), ('resource_labels','resource')]:
        old = p[field]
        p[field] = [f'{prefix}_{i}' for i in range(1,129)]
        validate(PreparationInput, p)
        p[field].append(f'{prefix}_129')
        with pytest.raises(ObservationError):
            validate(PreparationInput, p)
        p[field] = old
    p['retention_seconds'] = 0
    with pytest.raises(ObservationError):
        validate(PreparationInput, p)
