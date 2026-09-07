# Persistent cloud browsers

Use this workflow to save a profile and reuse it across separate runs. `/cdp/sessions` creates temporary remote CDP sessions and has different limits, billing, and cleanup endpoints.

## Authentication and entry points

Default API origin: `https://api.adscrawl.net`. Send `X-API-Key: <api-key>` for every lifecycle request; the key must be valid and associated with an active user. The authenticated user owns the profile, and the authenticated key pays for the run. Omit `apiKeyId`; if included at start, it must identify the same key. Do not send both API Key and session credentials: `X-API-Key` takes priority, and an invalid key does not fall back to JWT.

| Operation | API Key | Page session / Bearer JWT |
| --- | --- | --- |
| Create, list, detail, start, stop | Supported | Supported |
| Start proxy selection | Valid custom `proxy` in **every start request** | Effective saved/overridden custom proxy or explicit managed `countryCode`; start also selects an owned valid `apiKeyId` |
| PATCH/DELETE profile, Viewer, join-token | Unsupported | Supported |

For the page entry, `GLOBAL` explicitly selects a random available managed country; a two-letter country such as `FR` selects that country. Empty `countryCode` is not a selection. Missing/failed proxy routing must not become a direct connection. Request fields such as `source=web` cannot change the authenticated entry point.

## Endpoints and parameters

### Create a configuration: `POST /cloud-browsers`

Send `Content-Type: application/json` and a JSON object, for example:

```json
{
  "remark": "API lifecycle example",
  "browserSettings": { "viewport": { "width": 1440, "height": 900 } }
}
```

`remark`, when supplied, must be a string of at most 255 characters after trimming. `browserSettings`, when supplied, must be an object. Empty body, `null`, arrays, and scalars are invalid. Response: **201** `{"ok":true,"id":"<browser-id>"}`. Keep this ID for later calls. Creating a configuration consumes a saved-profile slot but does not start or reserve running capacity. Stop retains the configuration; removing it requires page session/Bearer DELETE.

### List: `GET /cloud-browsers?page=1&pageSize=10`

`page` defaults to 1; `pageSize` defaults to and is capped at 10. Response:

```json
{
  "ok": true,
  "data": [{ "id": "<browser-id>", "runtime": { "status": "stopped" } }],
  "pagination": { "page": 1, "pageSize": 10, "total": 1, "totalPages": 1 },
  "limit": 10,
  "runningLimit": 1,
  "runningCount": 0
}
```

This abbreviated example omits profile fields. `runningCount` covers all this user's profiles regardless of pagination or which API key started them. `limit` is saved-profile capacity; it must not be used as the running limit.

### Detail: `GET /cloud-browsers/{id}`

Returns the profile object **directly**, not under `data`. Read `response.runtime.status`. List/detail may include saved cookies and sensitive connection URLs; do not print or log entire responses.

States `starting`, `running`, and `stopping` all occupy running capacity. `starting` does not confirm readiness; `stopping` does not confirm shutdown. Only `stopped` confirms there is no active session. Runtime kind is `neko` or `worker_cdp`. Do not assume persistent browsers expose a CDP WebSocket: `neko` omits `cdpBaseUrl`, and its `connectUrl` leads to the logged-in Viewer page. `worker_cdp` can return a token-bearing `cdpBaseUrl`; keep it secret.

### Start: `POST /cloud-browsers/{id}/start`

Send a JSON object with a top-level `proxy` on **every** API Key start, including a restart of the same saved profile:

```json
{
  "proxy": {
    "server": "http://proxy.example.com:8080",
    "username": "<proxy-username>",
    "password": "<proxy-password>"
  }
}
```

Use `http://host:port` or `socks5://host:port` with an explicit valid port. Do not put credentials in the server URL; `username` and `password` must be non-empty strings provided together, or both omitted for an unauthenticated proxy. `proxy` must be an object. Never send `countryCode` alongside `proxy` in the same start request. A saved `countryCode` is cleared from this run's snapshot when a custom proxy override is provided; this does not change the saved profile.

Optional top-level `cookies` must be an array; `fingerprint` must be an object of supported fingerprint overrides. These apply only to this run. The API accepts optional `apiKeyId` only when it matches the authenticated key's ID. Do not embed `proxy` inside `browserSettings` at start: storing it at create time, omitting it, using a prior run's settings, or sending only `countryCode` does not satisfy the API requirement.

Success: **200** `{"ok":true,"runtime":{"runtimeKind":"neko","status":"running","sessionId":"<session-id>"}}` (abbreviated). The API waits for runtime confirmation before reporting success. It can expose `starting` or `stopping` through concurrent detail/list queries. On start transport failure or timeout, query the saved profile ID before any further action; an instance may exist. Do not automatically repeat start. Stop the same profile if cleanup is needed.

### Stop: `POST /cloud-browsers/{id}/stop`

No request body or proxy is required. Confirmed shutdown, including repeated stop of an inactive profile: **200** `{"ok":true,"runtime":{"status":"stopped"}}` (may include `sessionId`). An already executing stop: **202** `{"ok":true,"runtime":{"status":"stopping","sessionId":"<session-id>"}}`.

After 202, poll detail until `runtime.status == "stopped"`, with a bounded number of attempts and request timeouts. While starting, stop returns `409 CDP_SESSION_STARTING`; query until startup resolves, then retry stop on the same profile. Stop errors/timeouts may leave `stopping` and continue occupying capacity. Report unconfirmed cleanup; retry the idempotent stop operation when the runtime is reachable. Never claim stopped just because a request returned, a deadline elapsed, or a heartbeat disappeared.

## Running quota and billing

- Running capacity is per user, shared across all page/API starts and keys, and counts `starting`, `running`, and `stopping`. Temporary CDP sessions are excluded.
- The backing account setting uses default 1 for `NULL` or negative values, 0 to prohibit new starts, and a positive integer as the upper bound. The list returns the effective `runningLimit`; only authorized operations staff can change the database setting, not this public API.
- The API checks capacity atomically at start. List values are advisory, not a reservation. Reducing capacity leaves existing sessions running and prevents new starts until capacity is available.
- An active paid plan and at least 1 credit are required to start. Persistent cloud browsers consume 1 credit per started minute from successful start until stop, rounding a partial minute up. Repeated stop does not bill twice. Saved-profile limits, user running capacity, and service-wide capacity are separate checks.

## Errors and recovery

Preserve HTTP status and a recognized safe `code`; some legacy errors have only an `error` message. Do not log raw bodies, settings, or token-bearing URLs.

| HTTP / code or legacy message | Action |
| --- | --- |
| 400 `PROXY_REQUIRED` | Provide a complete custom `proxy` in this API start request. |
| 400 `INVALID_PROXY` | Fix scheme, host, explicit port, or paired credentials. |
| 400 `COUNTRY_PROXY_CONFLICT` | Send only `proxy` for API starts; remove `countryCode`. |
| 400 `INVALID_FINGERPRINT_SETTINGS` | Correct the fingerprint override fields/values. |
| 401 | Fix missing, invalid, disabled, or expired authentication; do not switch entry point to bypass validation. |
| 403 | Supplied `apiKeyId` mismatches the authenticated key or belongs to someone else. |
| 404 | Profile does not exist or is not owned by the authenticated user. |
| 402 `PAID_PLAN_REQUIRED` / `INSUFFICIENT_CREDITS` | Resolve the account plan or credit balance before another start. |
| 409 `CLOUD_BROWSER_CONCURRENCY_LIMIT` | User running quota is full or zero. Check `runningLimit`/`runningCount`; a stopping session still counts. |
| 409 `Browser is already running` | Same profile already has an active session. Query it; do not create another run. |
| 409 `Cloud Browser limit reached` | Saved-profile limit reached at create; this is distinct from running quota. |
| 409 `CDP_SESSION_STARTING` on stop | Query until startup resolves, then retry stop on this profile. |
| 503 `CLUSTER_NO_CAPACITY` / `CDP session capacity exhausted` | Service capacity is full; `CLUSTER_NO_CAPACITY` may include nullable `nextAvailableAt` and `retryAfterMs`. Do not hot-loop starts. |
| 502 `CLOUD_RUNTIME_*`, 503 `CLOUD_RUNTIME_CONFIG_INVALID` / `CLOUD_RUNTIME_UNREACHABLE`, 504 `CLOUD_RUNTIME_TIMEOUT` | Inspect profile state and preserve uncertainty; a failed operation need not release quota. Escalate persistent service failures. |
| 503 `DYNAMIC_PROXY_NOT_CONFIGURED` / `MANAGED_PROXY_UNAVAILABLE` | Page managed routing is unavailable. API clients still must supply a custom proxy; never fall back to direct. |

## Executable create → start → query → stop example

Use Python 3.9+ and the standard-library helper shipped with this skill. It reads secrets from the environment, does not print settings or connection URLs, and stops polling after 30 detail requests by default. Set `ADSCRAWL_BASE_URL` only for your trusted API origin; HTTPS is required except for loopback mock servers. All credentials below are placeholders. Supply real values through your existing secret mechanism and keep shell tracing (`set -x`) disabled.

```bash
export ADSCRAWL_API_KEY='<api-key>'
export ADSCRAWL_PROXY_SERVER='http://proxy.example.com:8080'
export ADSCRAWL_PROXY_USERNAME='<proxy-username>'
export ADSCRAWL_PROXY_PASSWORD='<proxy-password>'
# For an unauthenticated proxy, omit both credential environment variables.

# Run from the installed skill directory (the directory containing SKILL.md).
set -euo pipefail
ADSCRAWL_BROWSER_ID=$(python3 scripts/cloud_browser.py create --remark 'API lifecycle example' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

# Preserve the saved profile and try to stop even if start/query fails.
adscrawl_cleanup() {
  python3 scripts/cloud_browser.py stop "$ADSCRAWL_BROWSER_ID" || {
    echo 'Cloud browser cleanup is unconfirmed; query status and retry stop.' >&2
    return 1
  }
}
trap adscrawl_cleanup EXIT

# This command reads and sends proxy again on EVERY invocation, including restarts.
python3 scripts/cloud_browser.py start "$ADSCRAWL_BROWSER_ID"
python3 scripts/cloud_browser.py get "$ADSCRAWL_BROWSER_ID"
python3 scripts/cloud_browser.py list --page 1
python3 scripts/cloud_browser.py stop "$ADSCRAWL_BROWSER_ID"
trap - EXIT

# Safe to repeat; confirms idempotent stop and keeps the saved configuration.
python3 scripts/cloud_browser.py stop "$ADSCRAWL_BROWSER_ID"
```

The helper intentionally supports the core lifecycle only. It omits optional cookies/fingerprint overrides, and outputs allowlisted IDs/status/quota fields. Start reads `ADSCRAWL_PROXY_SERVER` afresh and rejects incomplete proxy input before sending any request. `--request-timeout` defaults to 60 seconds; stop additionally accepts `--poll-attempts` (30) and `--poll-interval` (2 seconds). A stop polling deadline or any request error exits nonzero, with a safe HTTP status/code when available. The cleanup trap is best effort; if start is still in progress or stop is unavailable, follow the recovery steps above. Do not silently abandon an active or uncertain run.

## Validation and release scope

Run `python3 -m unittest discover -s tests -v` from the repository root. Tests run the actual CLI against loopback mock HTTP servers with fake credentials, including restart proxy transmission, authentication, error redaction, and stopping polls. They do not prove real browser startup, proxy egress, billing, or production quota enforcement. Real validation requires a test API, paid test account, credits, and a reachable proxy.

This repository's existing source is `skills/SKILL.md` (`name: adscrawl-browser`); it has no version manifest or automated release workflow. Review and merge source changes via a PR. The historical ClawHub listing is `@adscrawl/adscrawl` (0.1.0 at its original publication); updating repository source does not verify a marketplace update. Marketplace packaging must include this reference and `scripts/cloud_browser.py`. Public publishing and marketplace submissions are separate authorized actions.
