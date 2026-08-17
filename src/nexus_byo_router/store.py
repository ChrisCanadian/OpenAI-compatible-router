from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class RouteRecord:
    token_hash: str
    base_url: str
    api_key: str
    model: str
    expires_at: datetime
    supports_tools: bool
    max_completion_tokens: int
    request_count: int = 0
    last_upstream_status: int | None = None
    last_request_at: datetime | None = None


class EphemeralRouteStore:
    def __init__(self) -> None:
        self._routes: dict[str, RouteRecord] = {}

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, *, base_url: str, api_key: str, model: str, ttl_seconds: int,
               supports_tools: bool, max_completion_tokens: int) -> tuple[str, RouteRecord]:
        token = secrets.token_urlsafe(32)
        rec = RouteRecord(
            token_hash=self._hash(token),
            base_url=base_url,
            api_key=api_key,
            model=model,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
            supports_tools=supports_tools,
            max_completion_tokens=max_completion_tokens,
        )
        self._routes[rec.token_hash] = rec
        return token, rec

    def get(self, token: str) -> RouteRecord | None:
        key = self._hash(token)
        rec = self._routes.get(key)
        if rec is None:
            return None
        if rec.expires_at <= datetime.now(timezone.utc):
            self._routes.pop(key, None)
            return None
        return rec

    def mark_request(self, token: str, *, upstream_status: int | None = None) -> RouteRecord | None:
        rec = self.get(token)
        if rec is None:
            return None
        rec.request_count += 1
        rec.last_request_at = datetime.now(timezone.utc)
        if upstream_status is not None:
            rec.last_upstream_status = int(upstream_status)
        return rec

    def usage(self, token: str) -> dict[str, object] | None:
        rec = self.get(token)
        if rec is None:
            return None
        return {
            "model": rec.model,
            "request_count": rec.request_count,
            "last_upstream_status": rec.last_upstream_status,
            "last_request_at": rec.last_request_at.isoformat() if rec.last_request_at else None,
            "expires_at": rec.expires_at.isoformat(),
        }

    def delete(self, token: str) -> bool:
        return self._routes.pop(self._hash(token), None) is not None
