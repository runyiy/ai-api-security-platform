import json
from urllib.parse import urlsplit

import httpx

from app.network_safety.destination import CanonicalDestination
from app.network_safety.gateway import NetworkGatewayError, NetworkGatewayResult


class HandlerNetworkGateway:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls = 0
        self.target_ids: list[int] = []

    def request(self, *, target_id: int, method: str, url: str, headers: dict[str, str],
                max_response_bytes: int, **kwargs) -> NetworkGatewayResult:
        self.calls += 1
        self.target_ids.append(target_id)
        request = httpx.Request(method, url, headers=headers)
        try:
            response = self.handler(request)
        except Exception as exc:
            raise NetworkGatewayError(
                code="network_request_failed",
                reason="Network request failed.",
            ) from exc
        body = response.content
        if len(body) > max_response_bytes:
            raise NetworkGatewayError(
                code="response_too_large",
                reason="Network response exceeded the allowed size.",
            )
        parsed = urlsplit(url)
        destination = CanonicalDestination(
            scheme=parsed.scheme,
            hostname=parsed.hostname or "localhost",
            port=parsed.port or (443 if parsed.scheme == "https" else 80),
            is_ip_literal=False,
            ip_address=None,
        )
        response_headers = getattr(response, "headers", None)
        content_encoding = (
            ",".join(response_headers.get_list("content-encoding")) or None
            if isinstance(response_headers, httpx.Headers)
            else None
        )
        return NetworkGatewayResult(
            status_code=response.status_code,
            body=body,
            duration_ms=0,
            destination=destination,
            selected_ip="127.0.0.1",
            peer_ip="127.0.0.1",
            content_encoding=content_encoding,
        )


class StaticJSONNetworkGateway(HandlerNetworkGateway):
    def __init__(self, payload: dict | None = None, status_code: int = 200) -> None:
        content = json.dumps(payload or {"paths": {}}).encode()
        super().__init__(lambda request: httpx.Response(status_code, content=content))
