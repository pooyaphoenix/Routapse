# Configuration

Routapse is configured through environment variables. Copy [`.env.example`](../.env.example) to `.env`. Docker Compose
reads it for build options and passes it to the containers.

## Services

| Variable | Default | Used by | Purpose |
|---|---|---|---|
| `ROLE` | `all` | backend | `gateway`, `admin` or `all`. Compose sets it per service. |
| `STORE_URL` | `file:///data/store.json` (Compose: `redis://redis:6379/0`) | backend | Config store. Use Redis when running more than one replica. |
| `ROUTER_URL` | `http://router:8001` | backend | Address of the router sidecar. |
| `ADMIN_TOKEN` | empty | backend | Bearer token for `/admin/*`. Empty disables auth (local use only). |
| `GATEWAY_API_KEY` | empty | backend | Require this key on `/v1/*`. |
| `CORS_ORIGINS` | `*` | backend | Comma-separated allowed origins. |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | backend | Default Ollama address for discovery and Ollama providers. |
| `LOG_DIR` | `logs` (Compose: `/logs`) | backend | Directory for `requests-YYYY-MM-DD.jsonl`. |
| `LOG_BODIES` | `true` | backend | `false` logs metadata only, with no prompt or response text. |
| `LOG_RETENTION_DAYS` | `0` | backend | Delete daily log files older than this many days (checked at most hourly per process). `0` keeps every file. |

## Router sidecar

| Variable | Default | Purpose |
|---|---|---|
| `LAYA_PRELOAD` | `0` | `1` loads all three Laya checkpoints at startup. |
| `JEV_KIND` | `ollama` | `ollama` or `openai_compat`. |
| `JEV_URL` | `http://host.docker.internal:11434` | Where Jev is served. |
| `JEV_MODEL` | `jev` | Model name at that server. |
| `JEV_API_KEY` | empty | Key for `openai_compat` servers. |
| `ROUTER_TIMEOUT` | `15` | Seconds to wait for Jev. |

## Docker build options

Read from `.env` by Compose.

| Variable | Default | Purpose |
|---|---|---|
| `INSTALL_LAYA` | `0` | `1` installs the `laya` package into the router image. |
| `PIP_INDEX_URL` | `https://pypi.org/simple` | PyPI mirror for the Python images. |
| `NPM_REGISTRY` | `https://registry.npmjs.org` | npm mirror for the frontend build. |
| `PYTHON_IMAGE` | `python:3.12-slim` | Base image for backend and router. |
| `NODE_IMAGE` | `node:20-alpine` | Base image for the frontend build. |

If containers cannot resolve DNS (errors such as `Temporary failure in name resolution`), set DNS servers in
Docker Desktop (Settings → Docker Engine, `"dns": ["8.8.8.8", "1.1.1.1"]`), or use the mirror options above.

## Ollama and Docker

Containers reach the host through `host.docker.internal`. Start Ollama with `OLLAMA_HOST=0.0.0.0 ollama serve`.
Without Docker, set `OLLAMA_URL=http://localhost:11434`.

## Data and privacy

- **Config store.** Provider API keys are stored in plaintext in Redis (or the JSON file). Keep it private.
- **Logs.** With `LOG_BODIES=true`, logs contain prompts and responses. Authorization headers and API keys are never
  logged. Old daily files are kept unless `LOG_RETENTION_DAYS` is set.
- **Redis keys** use the prefix `routapse:`.
