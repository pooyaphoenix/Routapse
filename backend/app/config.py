import os


class Settings:
    store_url = os.getenv("STORE_URL", "file:///data/store.json")
    router_url = os.getenv("ROUTER_URL", "http://router:8001").rstrip("/")
    admin_token = os.getenv("ADMIN_TOKEN", "")
    gateway_key = os.getenv("GATEWAY_API_KEY", "")
    role = os.getenv("ROLE", "all")  # all | gateway | admin
    cors = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")]
    ollama_url = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
    log_dir = os.getenv("LOG_DIR", "logs")
    log_bodies = os.getenv("LOG_BODIES", "true").lower() != "false"
    log_retention_days = int(os.getenv("LOG_RETENTION_DAYS", "0") or 0)  # 0 keeps every file


settings = Settings()
