from typing import Literal

from pydantic import BaseModel, Field


class Provider(BaseModel):
    id: str
    kind: Literal["openai", "openai_compat", "ollama", "anthropic", "gemini"]
    base_url: str = ""
    api_key: str = ""
    timeout: float = Field(120, gt=0, le=600)   # seconds per attempt
    retries: int = Field(2, ge=0, le=5)         # extra attempts on timeouts, connection errors, 408/429/5xx


class ModelDef(BaseModel):
    id: str                      # alias used in routes, e.g. "fast-local"
    provider_id: str
    model_name: str              # provider-side name, e.g. "llama3.2:3b"
    description: str = ""


class Route(BaseModel):
    label: str
    description: str = ""        # tells the router model when this route applies
    examples: list[str] = []
    action: Literal["forward", "respond"] = "forward"
    model_id: str | None = None  # target when action == forward
    fallback_model_id: str | None = None  # tried when model_id fails before producing any output
    system_prompt: str = ""      # agent persona / instructions injected on forward
    response_text: str = ""      # canned reply when action == respond
    color: str = "#2F6FDE"


class Signal(BaseModel):
    """Extra question Laya answers in the same call as the routing choice."""
    name: str
    type: Literal["choice", "score", "noul"]
    instructions: str
    criteria: dict[str, str] | list[str] | None = None


class Rule(BaseModel):
    """If a signal matches, send to this lane before the routing choice is used."""
    signal: str
    op: Literal["is", "gte", "lte"]
    value: str
    route_label: str


class RouterDef(BaseModel):
    id: str
    name: str
    mode: Literal["tier", "agent", "custom"] = "tier"
    router_model: Literal["jev", "laya"] = "laya"
    routes: list[Route] = Field(default_factory=list)
    fallback_label: str | None = None
    min_confidence: float = 0.0
    signals: list[Signal] = Field(default_factory=list)   # Laya only
    rules: list[Rule] = Field(default_factory=list)


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str                   # "router:<id>" or a model alias for direct pass-through
    messages: list[Message]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None


class TestRequest(BaseModel):
    router_id: str | None = None
    router: RouterDef | None = None   # test an unsaved draft from the GUI
    prompt: str
    execute: bool = False


class StudioRequest(BaseModel):
    router_id: str | None = None
    model_id: str | None = None
    system: str = ""
    messages: list[Message]
    temperature: float | None = None
    max_tokens: int | None = None
    force_label: str | None = None    # skip the router and use this lane
    route_only: bool = False


class SavedPrompt(BaseModel):
    id: str
    name: str
    router_id: str | None = None
    model_id: str | None = None
    system: str = ""
    user: str = ""
