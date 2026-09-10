"""M14-01 consumes explicit access truth without persistence or execution."""
import ast
from dataclasses import FrozenInstanceError, asdict, fields, replace
import importlib
import inspect
from itertools import product

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest


@pytest.fixture
def matrix():
    return importlib.import_module("app.generators.bola_matrix")


def fact(matrix, **overrides):
    return matrix.BOLAMatrixAccessFact(**{
        "endpoint_id": 11, "resource_id": 22, "test_identity_id": 33,
        "identity_auth_type": "bearer", "resolution_state": "resolved",
        "relationship": "owner", "expected_access": "allowed",
        "supporting_assertion_ids": (9, 2, 9, 1), **overrides,
    })


def assert_error(matrix, code, call):
    with pytest.raises(matrix.BOLAMatrixPlanningError) as error:
        call()
    assert error.value.code == str(error.value) == code
    assert not hasattr(error.value, "status_code")


def test_no_migration_or_database_dependency(matrix):
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["e9a1c3d5f7b8"]
    assert list(inspect.signature(matrix.plan_bola_matrix).parameters) == ["facts"]
    tree = ast.parse(inspect.getsource(matrix))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imports.add(node.module)
    assert imports <= {"collections.abc", "dataclasses", "typing"}
    # No winner selection, time, side-effect escape hatch, or hidden resolver.
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not names & {"confidence", "provenance", "observed_at", "human_verified", "role",
                        "owner_identity_id", "sorted", "sort", "min", "max", "eval", "exec",
                        "__import__", "open", "getattr", "globals", "locals"}


def test_exact_frozen_input_and_output_fields(matrix):
    source = fact(matrix)
    result = matrix.plan_bola_matrix(facts=[source])
    assert isinstance(result, tuple)
    candidate, = result
    assert isinstance(candidate, matrix.BOLAMatrixCandidate)
    assert {f.name for f in fields(source)} == {
        "endpoint_id", "resource_id", "test_identity_id", "identity_auth_type", "resolution_state",
        "relationship", "expected_access", "supporting_assertion_ids",
    }
    assert {f.name for f in fields(candidate)} == {
        "endpoint_id", "resource_id", "test_identity_id", "subject_kind", "candidate_kind",
        "relationship", "expected_access", "supporting_assertion_ids",
    }
    for value in (source, candidate):
        for field in fields(value):
            with pytest.raises(FrozenInstanceError):
                setattr(value, field.name, "changed")
        with pytest.raises(TypeError):
            value.supporting_assertion_ids[0] = 100
    assert asdict(candidate) == {
        "endpoint_id": 11, "resource_id": 22, "test_identity_id": 33,
        "subject_kind": "authenticated", "candidate_kind": "owner_access",
        "relationship": "owner", "expected_access": "allowed", "supporting_assertion_ids": (9, 2, 9, 1),
    }


@pytest.mark.parametrize(("relationship", "kind"), [
    ("owner", "owner_access"), ("non_owner", "cross_subject_access"), ("shared", "shared_access"),
])
@pytest.mark.parametrize("access", ["allowed", "denied"])
@pytest.mark.parametrize("auth_type", ["bearer", "api_key", "none", "Anonymous", "anonymous ", ""])
def test_authenticated_preserves_independent_relationship_and_access(matrix, relationship, kind, access, auth_type):
    source = fact(matrix, relationship=relationship, expected_access=access, identity_auth_type=auth_type)
    candidate, = matrix.plan_bola_matrix(facts=[source])
    assert candidate.subject_kind == "authenticated"
    assert candidate.candidate_kind == kind
    assert candidate.relationship == relationship
    assert candidate.expected_access == access
    assert candidate.supporting_assertion_ids == source.supporting_assertion_ids


@pytest.mark.parametrize("relationship", ["owner", "shared", "non_owner", "unspecified"])
@pytest.mark.parametrize("access", ["allowed", "denied"])
def test_only_exact_anonymous_auth_type_overrides_candidate_kind(matrix, relationship, access):
    candidate, = matrix.plan_bola_matrix(facts=[fact(matrix, identity_auth_type="anonymous",
        relationship=relationship, expected_access=access)])
    assert (candidate.subject_kind, candidate.candidate_kind) == ("anonymous", "anonymous_access")
    assert candidate.relationship == relationship
    assert candidate.expected_access == access


@pytest.mark.parametrize("auth_type", ["bearer", "anonymous"])
@pytest.mark.parametrize("relationship", ["owner", "shared", "non_owner", "unspecified"])
@pytest.mark.parametrize(("state", "access"), [
    (state, access) for state, access in product(
        ("resolved", "insufficient", "conflict"), ("allowed", "denied", "unspecified"))
    if state != "resolved" or access == "unspecified"
])
def test_unresolved_or_unspecified_access_never_infers_a_winner(matrix, auth_type, relationship, state, access):
    source = fact(matrix, identity_auth_type=auth_type, relationship=relationship,
                  resolution_state=state, expected_access=access)
    assert matrix.plan_bola_matrix(facts=[source]) == ()


@pytest.mark.parametrize("access", ["allowed", "denied"])
def test_authenticated_unspecified_relationship_skips(matrix, access):
    assert matrix.plan_bola_matrix(facts=[fact(matrix, relationship="unspecified", expected_access=access)]) == ()


@pytest.mark.parametrize("supports", [(), (11,), (100, 2, 100, 1)])
def test_supporting_ids_are_opaque_ordered_provenance(matrix, supports):
    source = fact(matrix, supporting_assertion_ids=supports, relationship="non_owner", expected_access="allowed")
    candidate, = matrix.plan_bola_matrix(facts=[source])
    assert candidate.supporting_assertion_ids == supports
    assert candidate.expected_access == "allowed"


def test_deterministic_order_and_inputs_unchanged(matrix):
    sources = [fact(matrix, test_identity_id=identity_id, supporting_assertion_ids=supports,
                    resolution_state=state)
               for identity_id, supports, state in (
                   (9, (90, 2), "resolved"), (8, (1000,), "conflict"),
                   (3, (3, 1), "resolved"), (1, (), "resolved"))]
    before = tuple(asdict(source) for source in sources)
    expected = matrix.plan_bola_matrix(facts=sources)
    assert [row.test_identity_id for row in expected] == [9, 3, 1]
    for _ in range(3):
        assert matrix.plan_bola_matrix(facts=iter(sources)) == expected
    assert matrix.plan_bola_matrix(facts=reversed(sources)) == tuple(reversed(expected))
    assert tuple(asdict(source) for source in sources) == before


def test_empty_and_exact_512_fact_boundary(matrix):
    assert matrix.MAX_BOLA_MATRIX_FACTS == 512
    assert matrix.plan_bola_matrix(facts=[]) == ()
    sources = [fact(matrix, test_identity_id=i) for i in range(1, 513)]
    assert len(matrix.plan_bola_matrix(facts=sources)) == 512
    assert [row.test_identity_id for row in matrix.plan_bola_matrix(facts=iter(sources))] == list(range(1, 513))


@pytest.mark.parametrize("state", ["resolved", "insufficient"])
def test_513th_fact_fails_without_truncation_partial_result_or_unbounded_consumption(matrix, state):
    consumed = []
    def sources():
        for i in range(1, 515):
            assert i <= 513, "planner consumed beyond the first excess fact"
            consumed.append(i)
            yield fact(matrix, test_identity_id=i, resolution_state=state)
    sentinel = object()
    result = sentinel
    with pytest.raises(matrix.BOLAMatrixPlanningError) as error:
        result = matrix.plan_bola_matrix(facts=sources())
    assert result is sentinel
    assert error.value.code == str(error.value) == "bola_matrix_fact_limit_exceeded"
    assert consumed == list(range(1, 514))


@pytest.mark.parametrize("state", ["resolved", "insufficient", "conflict"])
@pytest.mark.parametrize("identical", [True, False])
def test_exact_duplicate_key_fails_even_if_identical_or_ineligible(matrix, state, identical):
    first = fact(matrix, resolution_state=state)
    second = first if identical else replace(first, expected_access="denied", supporting_assertion_ids=(999,))
    assert_error(matrix, "bola_matrix_duplicate_access_fact",
                 lambda: matrix.plan_bola_matrix(facts=[first, second]))


def test_each_key_component_distinguishes_facts(matrix):
    original = fact(matrix)
    sources = [original, replace(original, endpoint_id=12), replace(original, resource_id=23),
               replace(original, test_identity_id=34)]
    assert len(matrix.plan_bola_matrix(facts=sources)) == 4


@pytest.mark.parametrize("field", ["endpoint_id", "resource_id", "test_identity_id"])
@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.0, "1", None])
def test_input_ids_are_strict_positive_integers(matrix, field, invalid):
    assert_error(matrix, "bola_matrix_invalid_access_fact", lambda: fact(matrix, **{field: invalid}))


@pytest.mark.parametrize(("field", "invalid"), [
    ("identity_auth_type", None), ("identity_auth_type", 1),
    ("resolution_state", "latest"), ("resolution_state", None), ("resolution_state", []),
    ("relationship", "cross_owner"), ("relationship", None), ("relationship", []),
    ("expected_access", "allow"), ("expected_access", None), ("expected_access", []),
    ("supporting_assertion_ids", [1, 2]), ("supporting_assertion_ids", None),
    ("supporting_assertion_ids", (0,)), ("supporting_assertion_ids", (-1,)),
    ("supporting_assertion_ids", (True,)), ("supporting_assertion_ids", (1.0,)),
    ("supporting_assertion_ids", ("1",)),
])
def test_invalid_or_mutable_fact_values_fail_with_pure_error(matrix, field, invalid):
    assert_error(matrix, "bola_matrix_invalid_access_fact", lambda: fact(matrix, **{field: invalid}))


@pytest.mark.parametrize("invalid", [None, {}, "fact", 1, object()])
def test_planner_rejects_untyped_facts_without_partial_result(matrix, invalid):
    assert_error(matrix, "bola_matrix_invalid_access_fact",
                 lambda: matrix.plan_bola_matrix(facts=[fact(matrix), invalid]))


@pytest.mark.parametrize("invalid", [None, 1])
def test_planner_rejects_noniterable_input_with_pure_error(matrix, invalid):
    assert_error(matrix, "bola_matrix_invalid_access_fact", lambda: matrix.plan_bola_matrix(facts=invalid))


def test_no_legacy_truth_credentials_mutations_or_external_activity(matrix, monkeypatch):
    from app.core.config import settings
    from app.db.models import (Endpoint, Resource, TestIdentity, TestCase, ExecutionPlan, PlanAction,
                               TestRun, Target, Scope, AuthorizationRevision, ResourceAccessAssertion,
                               EndpointResourceBinding, Finding, FindingEvidenceRecord)
    from sqlalchemy.orm import Session
    from sqlalchemy.engine import Connection
    from app.generators import bola
    from app.domain import ownership
    import app.services.resource_access_resolution as resolution
    import app.auth.context as authentication
    from app.credentials.bearer import BearerCredentialService
    from app.network_safety.gateway import NetworkGateway
    from app.executors.http import PolicyEnforcedHTTPExecutor
    from app.services.test_execution import TestExecutionService
    from app.scanners.openapi import OpenAPIScanner
    from app.services.ai_analysis import AIAnalysisService
    from app.ai.mock_provider import MockAIProvider
    import socket
    import httpx
    import httpcore
    import dns.resolver

    sources = [fact(matrix, relationship="owner", expected_access="denied"),
               fact(matrix, test_identity_id=34, relationship="non_owner", expected_access="allowed"),
               fact(matrix, test_identity_id=35, identity_auth_type="anonymous", relationship="unspecified")]
    config_before = settings.model_dump()
    def prohibited(*args, **kwargs):
        pytest.fail("pure matrix planner crossed a forbidden boundary")
    with monkeypatch.context() as guarded:
        for obj, name in (
            (bola, "determine_ownership_relation"), (ownership, "determine_ownership_relation"),
            (bola, "detect_resource_binding"), (bola, "generate_bola_test_cases"),
            (resolution, "resolve_resource_access"), (authentication, "build_authentication_context"),
            (BearerCredentialService, "resolve"), (BearerCredentialService, "resolve_binding"),
            (Session, "__init__"), (Session, "execute"), (Session, "add"), (Session, "delete"),
            (Connection, "execute"), (NetworkGateway, "request"), (PolicyEnforcedHTTPExecutor, "execute"),
            (TestExecutionService, "execute"), (OpenAPIScanner, "scan"),
            (AIAnalysisService, "analyze_finding"), (MockAIProvider, "analyze"),
            (socket, "getaddrinfo"), (socket, "gethostbyname"), (socket, "create_connection"),
            (socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "socket"),
            (httpx.Client, "send"), (httpx.AsyncClient, "send"), (httpcore.ConnectionPool, "stream"),
            (dns.resolver, "resolve"), (dns.resolver.Resolver, "resolve"),
        ):
            guarded.setattr(obj, name, prohibited)
        guarded.setattr(Resource, "owner_identity_id", property(prohibited))
        guarded.setattr(TestIdentity, "credentials", property(prohibited))
        guarded.setattr(TestIdentity, "role", property(prohibited))
        for model in (Endpoint, Resource, TestIdentity, TestCase, ExecutionPlan, PlanAction, TestRun,
                      Target, Scope, AuthorizationRevision, ResourceAccessAssertion, EndpointResourceBinding,
                      Finding, FindingEvidenceRecord):
            guarded.setattr(model, "__init__", prohibited)
            guarded.setattr(model, "__setattr__", prohibited)
        result = matrix.plan_bola_matrix(facts=sources)
        assert [(r.candidate_kind, r.expected_access) for r in result] == [
            ("owner_access", "denied"), ("cross_subject_access", "allowed"), ("anonymous_access", "allowed")]
        assert matrix.plan_bola_matrix(facts=sources) == result
    assert settings.model_dump() == config_before
