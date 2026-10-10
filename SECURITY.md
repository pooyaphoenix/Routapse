# Security policy

Please **do not open a public issue** for a security problem. Use GitHub's private reporting instead:
**Security tab → Report a vulnerability**.

Include what you found, how to reproduce it, and the impact. Routapse is maintained by volunteers, so please allow
time for a reply.

## Deploying safely

- Set `ADMIN_TOKEN` and `GATEWAY_API_KEY`. An empty `ADMIN_TOKEN` disables admin authentication.
- Provider API keys are stored in plaintext in Redis (or the JSON store). Keep it private.
- Put TLS (a reverse proxy) in front of the gateway, admin API and GUI.
- Request logs contain prompts and responses. Set `LOG_BODIES=false` if that is not acceptable, and set `LOG_RETENTION_DAYS` to delete old files.
- Restrict `CORS_ORIGINS` in production.
