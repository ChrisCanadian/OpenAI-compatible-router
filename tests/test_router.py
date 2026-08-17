from __future__ import annotations

import json
import httpx
import pytest

from nexus_byo_router.app import create_app


async def public_resolver(host: str, port: int):
    return ["8.8.8.8"]


class OneChunkStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes):
        self.content = content

    async def __aiter__(self):
        yield self.content


class UpstreamTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.requests = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append((request, body))
        assert request.headers["authorization"] == "Bearer upstream-secret"
        assert "x-validation-route" not in request.headers
        if body.get("stream"):
            content = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=OneChunkStream(content), request=request)
        return httpx.Response(200, json={
            "id": "fake",
            "object": "chat.completion",
            "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
        }, request=request)


@pytest.mark.asyncio
async def test_router_overrides_model_forwards_tools_and_hides_route_header():
    upstream = UpstreamTransport()
    app = create_app(admin_token="admin", service_token="service", transport=upstream, resolver=public_resolver)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://router") as client:
        reg = await client.post("/internal/routes", headers={"X-Router-Admin-Token": "admin"}, json={
            "base_url": "https://provider.example/v1",
            "api_key": "upstream-secret",
            "model": "chosen-model",
            "supports_tools": True,
            "max_completion_tokens": 500,
        })
        assert reg.status_code == 200
        route = reg.json()["route_token"]
        assert "upstream-secret" not in reg.text

        completion = await client.post("/v1/chat/completions", headers={
            "Authorization": "Bearer service",
            "X-Validation-Route": route,
        }, json={
            "model": "caller-tried-to-change-me",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5000,
            "tools": [{"type": "function", "function": {"name": "ping", "parameters": {"type": "object"}}}],
        })
        assert completion.status_code == 200
        assert completion.json()["model"] == "chosen-model"
        _, sent = upstream.requests[-1]
        assert sent["model"] == "chosen-model"
        assert sent["max_tokens"] == 500
        assert sent["tools"][0]["function"]["name"] == "ping"


@pytest.mark.asyncio
async def test_router_streams_sse():
    upstream = UpstreamTransport()
    app = create_app(admin_token="admin", service_token="service", transport=upstream, resolver=public_resolver)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://router") as client:
        reg = await client.post("/internal/routes", headers={"X-Router-Admin-Token": "admin"}, json={
            "base_url": "https://provider.example/v1",
            "api_key": "upstream-secret",
            "model": "chosen-model",
        })
        route = reg.json()["route_token"]
        async with client.stream("POST", "/v1/chat/completions", headers={
            "Authorization": "Bearer service",
            "X-Validation-Route": route,
        }, json={"model": "x", "messages": [{"role": "user", "content": "hi"}], "stream": True}) as resp:
            assert resp.status_code == 200
            text = "".join([chunk async for chunk in resp.aiter_text()])
            assert 'data: {"choices"' in text
            assert "[DONE]" in text


@pytest.mark.asyncio
async def test_ssrf_rejects_private_resolution():
    async def private_resolver(host: str, port: int):
        return ["127.0.0.1"]

    app = create_app(admin_token="admin", service_token="service", transport=UpstreamTransport(), resolver=private_resolver)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://router") as client:
        reg = await client.post("/internal/routes", headers={"X-Router-Admin-Token": "admin"}, json={
            "base_url": "https://evil.example/v1",
            "api_key": "secret",
            "model": "m",
        })
        assert reg.status_code == 400
        assert "private" in reg.json()["detail"]


@pytest.mark.asyncio
async def test_missing_route_and_bad_service_auth_rejected():
    app = create_app(admin_token="admin", service_token="service", transport=UpstreamTransport(), resolver=public_resolver)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://router") as client:
        r = await client.post("/v1/chat/completions", json={"messages": []})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_two_routes_keep_credentials_and_models_isolated():
    class IsolatingTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.seen = []
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            auth = request.headers["authorization"]
            self.seen.append((auth, body["model"]))
            return httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": auth + ":" + body["model"]}}]
            }, request=request)

    upstream = IsolatingTransport()
    app = create_app(admin_token="admin", service_token="service", transport=upstream, resolver=public_resolver)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://router") as client:
        async def reg(key, model):
            r = await client.post("/internal/routes", headers={"X-Router-Admin-Token": "admin"}, json={
                "base_url": "https://provider.example/v1", "api_key": key, "model": model
            })
            return r.json()["route_token"]
        a = await reg("key-a", "model-a")
        b = await reg("key-b", "model-b")
        for route, expected in [(a, "Bearer key-a:model-a"), (b, "Bearer key-b:model-b")]:
            r = await client.post("/v1/chat/completions", headers={
                "Authorization": "Bearer service", "X-Validation-Route": route
            }, json={"model": "ignored", "messages": []})
            assert r.json()["choices"][0]["message"]["content"] == expected
    assert upstream.seen == [("Bearer key-a", "model-a"), ("Bearer key-b", "model-b")]


@pytest.mark.asyncio
async def test_upstream_connection_failure_does_not_echo_api_key():
    class FailingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("synthetic failure", request=request)

    app = create_app(admin_token="admin", service_token="service", transport=FailingTransport(), resolver=public_resolver)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://router") as client:
        reg = await client.post("/internal/routes", headers={"X-Router-Admin-Token": "admin"}, json={
            "base_url": "https://provider.example/v1", "api_key": "super-secret-key", "model": "m"
        })
        route = reg.json()["route_token"]
        r = await client.post("/v1/chat/completions", headers={
            "Authorization": "Bearer service", "X-Validation-Route": route
        }, json={"messages": []})
        assert r.status_code == 502
        assert "super-secret-key" not in r.text
        assert "synthetic failure" not in r.text


@pytest.mark.asyncio
async def test_models_endpoint_exposes_only_registered_model():
    app = create_app(admin_token="admin", service_token="service", transport=UpstreamTransport(), resolver=public_resolver)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://router") as client:
        reg = await client.post("/internal/routes", headers={"X-Router-Admin-Token": "admin"}, json={
            "base_url": "https://provider.example/v1", "api_key": "upstream-secret", "model": "only-model"
        })
        route = reg.json()["route_token"]
        r = await client.get("/v1/models", headers={
            "Authorization": "Bearer service", "X-Validation-Route": route
        })
        assert r.status_code == 200
        assert r.json()["data"] == [{"id": "only-model", "object": "model", "owned_by": "evaluator-provider"}]
