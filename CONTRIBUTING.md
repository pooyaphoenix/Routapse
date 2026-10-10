# Contributing to Routapse

Thanks for helping. Bug reports, docs fixes and features are all welcome.

## Set up

Follow *Run without Docker* in the [README](README.md): router sidecar on `:8001`, backend on `:8002`
(`ROLE=all`), frontend on `:5173`. You can develop everything except Laya without it installed, because the
router falls back to a keyword heuristic.

## Where things live

| Path | What |
|---|---|
| `backend/app/engine.py` | Routing: classify, rules, fallback, execute, log |
| `backend/app/providers.py` | One adapter per provider (OpenAI-compatible, Anthropic, Gemini) |
| `backend/app/api_gateway.py`, `api_admin.py` | Data plane and control plane endpoints |
| `backend/app/reqlog.py` | Daily JSONL request log |
| `router/app/main.py` | Laya and Jev adapters and the keyword fallback |
| `frontend/src/` | GUI: `Routers`, `Studio`, `Logs`, `Connections` |

## Good first contributions

- **More tests.** Suites live in `backend/tests` and `router/tests` (see the README for how to run them).
  Missing coverage: real Laya and Jev responses, and the frontend.
- **Tool calls and image parts** passed through to providers that support them.
- **New provider adapters** in `providers.py`.

## Pull requests

1. Open an issue first for anything large.
2. Keep changes focused. Update the README or `docs/` when behavior changes.
3. Run `python -m compileall -q backend router` and `cd frontend && npm run build`. CI runs both.
4. Never commit `.env`, API keys, logs or prompts.

By contributing you agree your work is released under the [MIT License](LICENSE).
