# Security Notes

- Use a dedicated temporary provider key with a strict spending limit.
- Never persist evaluator keys to a database, file, trace, or evidence artifact.
- Keep `/internal/routes` inaccessible from the public internet when deployed.
- Set strong values for `ROUTER_ADMIN_TOKEN` and `ROUTER_SERVICE_TOKEN`.
- Hosted mode permits only HTTPS upstreams resolving to public IP space.
- Do not disable the SSRF checks on a public deployment.
- Route tokens expire and should be revoked as soon as a validation sandbox closes.
