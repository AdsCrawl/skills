---
name: adscrawl-browser
description: Read, capture, or interact with browser-rendered websites through AdsCrawl. Use to extract Markdown, article JSON, or HTML; capture PNG screenshots; control temporary remote CDP sessions; or create, list, inspect, start with a custom proxy, and stop persistent cloud browser profiles.
---

# AdsCrawl Browser

Choose the smallest AdsCrawl workflow that completes the task. Prefer the one-shot `/html` and `/screenshot` endpoints over a CDP session when interaction is unnecessary.

## Prerequisites

Require `ADSCRAWL_API_KEY` in the environment. Use `ADSCRAWL_BASE_URL` when set; otherwise use `https://api.adscrawl.net`.

```bash
test -n "$ADSCRAWL_API_KEY" || { echo "ADSCRAWL_API_KEY is required" >&2; exit 1; }
BASE_URL="${ADSCRAWL_BASE_URL:-https://api.adscrawl.net}"
```

Never place API keys, CDP tokens, cookies, or proxy credentials in code, commits, logs, or final responses.

## Select a workflow

| User intent | Endpoint | Result |
| --- | --- | --- |
| Read, summarize, extract, or inspect rendered content | `POST /html` | Markdown, Readability JSON, or HTML |
| Capture, archive, or visually verify a page | `POST /screenshot` | PNG image |
| Click, type, log in, debug, or run multiple browser steps | `POST /cdp/sessions` | Remote CDP session |
| Save and reuse a cloud browser profile across runs | `/cloud-browsers` | Persistent profile with a separate start/stop lifecycle |

For persistent profiles, read [Cloud browser lifecycle](references/cloud-browsers.md) for the full API contract, errors, quotas, and executable create → start → query → stop example. Temporary `/cdp/sessions` sessions remain a separate workflow; do not use their create/delete endpoints to manage a persistent profile.

For `/html` and `/screenshot`:

- Use `userAgentMode: "random"` with `userAgentOs: "windows"` unless the user requests a custom user agent.
- Add `countryCode` only for a requested country or region. Use uppercase codes such as `US`, `JP`, or `SG`; use `GLOBAL` for automatic managed routing.
- Use a `proxy` object with `server` and optional `username` and `password` for a custom proxy.
- Never send `countryCode` and `proxy` together.

## Extract content

Use `markdown` by default for reading, research, summarization, and RAG. Use `json` for structured article fields and `html` for the complete rendered document.

```bash
curl --fail-with-body -sS -X POST "$BASE_URL/html" \
  -H "content-type: application/json" \
  -H "x-api-key: $ADSCRAWL_API_KEY" \
  -d '{
    "url": "TARGET_URL",
    "contentMode": "markdown",
    "waitUntil": "domcontentloaded",
    "timeoutMs": 60000,
    "userAgentMode": "random",
    "userAgentOs": "windows"
  }' \
  --output page.md
```

- Prefer `domcontentloaded` for ordinary extraction.
- Use `load` when load handlers or assets affect the requested content.
- Use `networkidle` only for asynchronous pages that do not continuously poll.
- If `markdown` or `json` returns `422`, retry once with `contentMode: "html"`.
- Save large responses to a file and verify the result is meaningful content rather than navigation, an error, or a challenge page.

## Capture a screenshot

Default to a 1440 x 900 viewport and full-page capture unless the user requests an exact viewport or first-screen image.

```bash
curl --fail-with-body -sS -X POST "$BASE_URL/screenshot" \
  -H "content-type: application/json" \
  -H "x-api-key: $ADSCRAWL_API_KEY" \
  -d '{
    "url": "TARGET_URL",
    "viewport": { "width": 1440, "height": 900 },
    "fullPage": true,
    "waitUntil": "load",
    "timeoutMs": 60000,
    "userAgentMode": "random",
    "userAgentOs": "windows"
  }' \
  --output page.png
```

- Set `fullPage` to `false` for an exact-viewport capture.
- Add `locale` and `timezoneId` together when checking localization.
- Verify that the response is a non-empty PNG, then inspect it for challenge pages, overlays, missing assets, or incomplete lazy loading.
- Retry at most once with a more appropriate `waitUntil` value.

## Control a remote browser

Use temporary CDP for multi-step work that does not require a saved cloud browser profile. CDP `browserSettings` accepts viewport, locale, timezone, geolocation, cookies, a custom `userAgent`, and a custom `proxy`; it does not use the managed HTTP `countryCode` or random User-Agent fields.

Create a session:

```bash
curl --fail-with-body -sS -X POST "$BASE_URL/cdp/sessions" \
  -H "content-type: application/json" \
  -H "x-api-key: $ADSCRAWL_API_KEY" \
  -d '{
    "idleTimeoutMs": 600000,
    "maxSessionMs": 3600000,
    "browserSettings": {
      "viewport": { "width": 1440, "height": 900 }
    }
  }' \
  --output cdp-session.json
```

Read `sessionId` and `webSocketDebuggerUrl` from the response without printing the token-bearing URL. Connect with the browser library already used by the project.

Playwright:

```javascript
const browser = await chromium.connectOverCDP(webSocketDebuggerUrl);
const context = browser.contexts()[0];
const page = context.pages()[0] ?? await context.newPage();
```

Puppeteer:

```javascript
const browser = await puppeteer.connect({ browserWSEndpoint: webSocketDebuggerUrl });
```

Reuse one session for related steps. Prefer DOM or protocol-level readiness checks over fixed sleeps. Always close the remote session in a `finally` block or equivalent cleanup path:

```bash
curl --fail-with-body -sS -X DELETE "$BASE_URL/cdp/sessions/SESSION_ID" \
  -H "x-api-key: $ADSCRAWL_API_KEY"
```

## Manage a persistent cloud browser

- Send `X-API-Key` on create, list, detail, start, and stop requests. The authenticated key is the billing key; omit `apiKeyId` (if supplied, it must match that key's ID).
- Create saves a configuration and returns `201 {ok:true,id}`; it does not start a browser. Use `POST /cloud-browsers/{id}/start` to run it, then `/stop` to stop it while retaining the profile.
- Every API start must explicitly send a valid `proxy` object in that request, including restarts. Saved or historical proxies and `countryCode` cannot replace it. Never fall back to direct or managed routing. Use `http://host:port` or `socks5://host:port`; provide `username` and `password` together or omit both.
- Page session/Bearer authentication can select a custom proxy or an explicit managed `countryCode` (`GLOBAL` for a randomly selected available country). Never send `countryCode` and `proxy` together. PATCH/DELETE profiles and Viewer access remain page session/Bearer only.
- Start requires an active paid plan and credits. Read list fields `runningLimit` and `runningCount` for the user-wide quota shared by page and API; `starting`, `running`, and `stopping` all count. The older `limit` field is the number of saved profiles allowed, not running capacity.
- Start returns `200` only after `running`. Stop can return `202 stopping`; poll detail's top-level `runtime.status` until `stopped`, with a finite deadline. A timeout or failed stop does not confirm cleanup or release capacity. Inspect status before retrying an uncertain start; retry stop on the same profile when needed.
- Use [scripts/cloud_browser.py](scripts/cloud_browser.py) for lifecycle calls with bounded stop polling and output that omits cookies, proxy credentials, and token-bearing URLs. Its commands and environment inputs are documented in the lifecycle reference. Always stop an instance started for the task, including on task failure, and report any unconfirmed cleanup.

## Handle failures

1. Preserve the HTTP status and safe error body.
2. Treat `400` as invalid or conflicting parameters, `401` as authentication failure, `429` as a temporary CDP session limit, `503` as unavailable capacity or routing, and `504` as task timeout. For persistent cloud browsers, use the [lifecycle error table](references/cloud-browsers.md#errors-and-recovery), including `402` billing failures and `409` running-quota or lifecycle conflicts.
3. Retry at most once only when a different navigation strategy can reasonably help.
4. Report the failing workflow, status, and safe error message without exposing secrets.
5. Close any CDP session before returning an error.

Use cookies, authenticated pages, personal data, and external side effects only when the user explicitly authorizes the exact action.
