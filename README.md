# usaged

Subscription usage for Claude, ChatGPT/Codex, Google Antigravity (Gemini), OpenCode Go, Kimi Code and Z.ai,
collected by one daemon and shown in a macOS menu bar app.

## usaged.py

Stdlib Python daemon. Polls every 5 min, serves the cached snapshot at `GET /usage` on the machine's
Tailscale IP (port 8787, no auth; the tailnet is the auth).

```json
{"providers": [{"id": "claude", "windows": [{"name": "7d", "used": 0.19, "resets_at": "..."}], "fetched_at": "...", "error": null}]}
```

A provider is `fetch() -> [window]` plus an optional `refresh()`. On a 401 the poller runs `refresh()`
once (shelling out to the vendor CLI, so the CLI stays the only writer of its credentials) and retries.

| provider | source | credential | refresh |
|---|---|---|---|
| claude | `claude -p /usage` (free, no model call; text output parsed) | owned by claude | `claude -p ok --model haiku` |
| codex | one-off `codex app-server` over stdio, `account/rateLimits/read` | `~/.codex/auth.json` (via codex) | built into the RPC |
| google | `POST daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota` | Keychain `gemini`/`antigravity` (agy) | `agy models` |
| opencode | `GET opencode.ai/zen/go/v1/usage` | `OPENCODE_API_KEY` in `~/.env` | — |
| kimi | `GET api.kimi.com/coding/v1/usages` | `KIMI_CODE_API_KEY` in `~/.env` | — |
| zai | `GET api.z.ai/api/monitor/usage/quota/limit` | `ZAI_API_KEY` in `~/.env` | — |

Notes: Antigravity meters on `daily-cloudcode-pa` (prod `cloudcode-pa` never moves) and 403s
non-Antigravity user agents. All `gemini-*` models share one 5h pool.

Run under launchd with `co.morford.usaged.plist`.

## usagebar/

SwiftUI `MenuBarExtra`: per provider, a logo and 1–3 bars showing remaining quota; click for a table.
Set `endpoint` in `app.swift`, then `usagebar/build.sh` (needs `rsvg-convert`) builds, ad-hoc signs and
installs to `~/Applications`.

Logos from [lobe-icons](https://github.com/lobehub/lobe-icons) (MIT).
