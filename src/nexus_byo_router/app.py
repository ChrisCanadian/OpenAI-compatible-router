from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from .models import RouteCreated, RouteRegistration
from .security import Resolver, chat_completions_url, default_resolver, validate_upstream_base_url
from .store import EphemeralRouteStore


def create_app(*, admin_token: str | None = None, service_token: str | None = None,
               transport: httpx.AsyncBaseTransport | None = None,
               resolver: Resolver = default_resolver) -> FastAPI:
    store = EphemeralRouteStore()
    admin_token = admin_token or os.environ.get("ROUTER_ADMIN_TOKEN", "dev-admin-change-me")
    service_token = service_token or os.environ.get("ROUTER_SERVICE_TOKEN", "dev-service-change-me")
    client = httpx.AsyncClient(timeout=httpx.Timeout(900, connect=30), transport=transport)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(title="OpenAI-Compatible Router", version="0.1.0", lifespan=lifespan)

    async def require_admin(value: str | None):
        if not value or value != admin_token:
            raise HTTPException(status_code=401, detail="unauthorized")

    def require_service(authorization: str | None):
        expected = f"Bearer {service_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "openai-compatible-router"}

    @app.post("/internal/routes", response_model=RouteCreated)
    async def create_route(body: RouteRegistration, x_router_admin_token: str | None = Header(default=None)):
        await require_admin(x_router_admin_token)
        try:
            safe_base = await validate_upstream_base_url(body.base_url, resolver=resolver)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        token, rec = store.create(
            base_url=safe_base,
            api_key=body.api_key.get_secret_value(),
            model=body.model,
            ttl_seconds=body.ttl_seconds,
            supports_tools=body.supports_tools,
            max_completion_tokens=body.max_completion_tokens,
        )
        return RouteCreated(route_token=token, expires_at=rec.expires_at.isoformat())

    @app.delete("/internal/routes/{route_token}", status_code=204)
    async def delete_route(route_token: str, x_router_admin_token: str | None = Header(default=None)):
        await require_admin(x_router_admin_token)
        store.delete(route_token)
        return None

    @app.get("/v1/models")
    async def list_models(
        authorization: str | None = Header(default=None),
        x_validation_route: str | None = Header(default=None),
    ):
        require_service(authorization)
        if not x_validation_route:
            raise HTTPException(status_code=400, detail="missing validation route")
        route = store.get(x_validation_route)
        if route is None:
            raise HTTPException(status_code=401, detail="validation route is invalid or expired")
        return {
            "object": "list",
            "data": [{"id": route.model, "object": "model", "owned_by": "evaluator-provider"}],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        authorization: str | None = Header(default=None),
        x_validation_route: str | None = Header(default=None),
    ):
        require_service(authorization)
        if not x_validation_route:
            raise HTTPException(status_code=400, detail="missing validation route")
        route = store.get(x_validation_route)
        if route is None:
            raise HTTPException(status_code=401, detail="validation route is invalid or expired")

        raw = await request.body()
        if len(raw) > 1_000_000:
            raise HTTPException(status_code=413, detail="request too large")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid json") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="request body must be an object")

        body["model"] = route.model
        if not route.supports_tools:
            body.pop("tools", None)
            body.pop("tool_choice", None)
        if "max_tokens" in body:
            try:
                body["max_tokens"] = min(int(body["max_tokens"]), route.max_completion_tokens)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="max_tokens must be an integer")

        upstream_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {route.api_key}",
        }
        upstream_url = chat_completions_url(route.base_url)
        wants_stream = bool(body.get("stream", False))

        try:
            req = client.build_request("POST", upstream_url, json=body, headers=upstream_headers)
            resp = await client.send(req, stream=wants_stream)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="upstream connection failed") from exc

        if wants_stream:
            media_type = resp.headers.get("content-type", "text/event-stream").split(";", 1)[0]
            return StreamingResponse(
                resp.aiter_raw(),
                status_code=resp.status_code,
                media_type=media_type,
                background=BackgroundTask(resp.aclose),
            )

        data = await resp.aread()
        await resp.aclose()
        content_type = resp.headers.get("content-type", "application/json")
        if "json" in content_type:
            try:
                content = json.loads(data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                content = {"upstream_text": data.decode("utf-8", "replace")}
        else:
            content = {"upstream_text": data.decode("utf-8", "replace")}
        return JSONResponse(status_code=resp.status_code, content=content)

    return app


app = create_app()
