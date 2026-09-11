"""First HTTP write follows connect/TLS; bounded deadline applies to every read."""
from contextlib import contextmanager
from unittest.mock import Mock
import pytest
from app.network_safety.gateway import _BoundaryStream,NetworkGatewayError


def test_tls_precedes_exact_boundary_and_only_one_gate():
    events=[];raw=Mock();tls=Mock();raw.start_tls.return_value=tls
    class Boundary:
        @contextmanager
        def sending(self):
            events.append('gate');yield 2;events.append('committed')
    wrapped=_BoundaryStream(raw,Boundary())
    stream=wrapped.start_tls(None,server_hostname='owned.test',timeout=5)
    assert events==[]
    tls.write.side_effect=lambda *a,**k:events.append('write')
    stream.write(b'GET /folders/9462 HTTP/1.1\r\n\r\n',timeout=5)
    stream.write(b'',timeout=5)
    assert events==['gate','write','committed','write']
    assert tls.write.call_args_list[0].kwargs['timeout']<=2


def test_expired_absolute_response_deadline_prevents_additional_read(monkeypatch):
    import app.network_safety.gateway as gateway
    at=[100.0];monkeypatch.setattr(gateway.time,'monotonic',lambda:at[0])
    raw=Mock()
    class Boundary:
        @contextmanager
        def sending(self):yield 0.5
    wrapped=_BoundaryStream(raw,Boundary());wrapped.write(b'GET /owned HTTP/1.1\r\n\r\n',timeout=5)
    at[0]=100.499;wrapped.read(1,timeout=5)
    assert raw.read.call_args.kwargs['timeout']<0.002
    at[0]=100.5
    with pytest.raises(NetworkGatewayError,match='response_deadline'):wrapped.read(1,timeout=5)
    assert raw.read.call_count==1


def test_rejected_gate_never_writes_request():
    raw=Mock()
    class Boundary:
        @contextmanager
        def sending(self):
            raise NetworkGatewayError(code='synthetic_rejected',reason='owned test')
            yield
    wrapped=_BoundaryStream(raw,Boundary())
    with pytest.raises(NetworkGatewayError):wrapped.write(b'GET /owned HTTP/1.1\r\n\r\n')
    raw.write.assert_not_called()


def test_read_returning_after_its_deadline_is_not_a_complete_response(monkeypatch):
    import app.network_safety.gateway as gateway
    at=[100.0];monkeypatch.setattr(gateway.time,'monotonic',lambda:at[0]);raw=Mock()
    class Boundary:
        @contextmanager
        def sending(self):yield 1
    wrapped=_BoundaryStream(raw,Boundary());wrapped.write(b'GET /owned HTTP/1.1\r\n\r\n')
    def read(*args,**kwargs):at[0]=101;return b'late'
    raw.read.side_effect=read
    with pytest.raises(NetworkGatewayError,match='response_deadline'):wrapped.read(4)
