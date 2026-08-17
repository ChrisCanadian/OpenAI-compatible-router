# OpenAI-Compatible Router

An original lightweight router for short-lived evaluator-supplied OpenAI-compatible providers.

This is **not** based on Moon's unpublished router. It was designed independently for black-box validation and other short-lived BYO-provider use cases.

## Properties

- evaluator API keys are kept in memory only;
- routes expire automatically and can be revoked explicitly;
- the registered route hard-locks the upstream model ID;
- streaming responses are proxied;
- OpenAI-style tools/tool-calls pass through when the route permits them;
- request body size and completion-token ceilings are enforced;
- hosted mode rejects localhost/private/link-local/reserved upstream addresses to reduce SSRF risk;
- upstream failures do not echo evaluator secrets;
- an admin-only usage readback exposes request count/status for independent routing evidence without exposing the route token or upstream key.

## Endpoints

- `POST /internal/routes` — register a temporary upstream route (admin only).
- `GET /internal/routes/{route_token}/usage` — public-safe route-usage readback (admin only).
- `DELETE /internal/routes/{route_token}` — revoke a route (admin only).
- `POST /v1/chat/completions` — OpenAI-compatible proxy endpoint.
- `GET /v1/models` — return the model locked to the current route.
- `GET /health` — liveness.

## Data-plane authentication

Version `0.2.0` supports two equivalent service-side forms.

A dedicated service can use its own bearer plus the opaque route header:

```text
Authorization: Bearer <router service token>
X-Validation-Route: <opaque temporary route token>
```

A request-scoped private adapter can instead use the temporary route token directly as the bearer:

```text
Authorization: Bearer <opaque temporary route token>
```

The direct bearer form exists so an ordinary OpenAI-compatible client can be pointed at the router without teaching that client a proprietary routing header. In either form, the route token is consumed by the router and is **never** forwarded upstream. The evaluator's provider receives only its own API credential.

## Security note

Treat route tokens as short-lived secrets. Keep the admin interface on a private network, use temporary provider keys with narrow spending limits, and apply edge rate limits when the data plane is Internet reachable.
