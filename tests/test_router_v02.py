import json
import httpx
import pytest

from nexus_byo_router.app import create_app


async def public_resolver(host: str, port: int):
    return ["93.184.216.34"]


@pytest.mark.asyncio
async def test_ephemeral_route_bearer_and_secret_free_usage_readback():
    seen = {}

    async def upstream(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        body = json.loads(request.content)
        seen["model"] = body["model"]
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "routed"}}]})

    app = create_app(
        admin_token="admin",
        service_token="service",
        transport=httpx.MockTransport(upstream),
        resolver=public_resolver,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://router") as client:
        created = await client.post("/internal/routes", headers={"X-Router-Admin-Token": "admin"}, json={
            "base_url": "https://provider.example/v1",
            "api_key": "upstream-secret",
            "model": "model-a",
            "ttl_seconds": 300,
            "supports_tools": True,
            "max_completion_tokens": 256,
        })
        route = created.json()["route_token"]

        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {route}"},
            json={"model": "ignored-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 200
        assert seen == {"authorization": "Bearer upstream-secret", "model": "model-a"}

        usage = await client.get(f"/internal/routes/{route}/usage", headers={"X-Router-Admin-Token": "admin"})
        payload = usage.json()
        assert payload["request_count"] == 1
        assert payload["last_upstream_status"] == 200
        serialized = json.dumps(payload)
        assert "upstream-secret" not in serialized
        assert route not in serialized
