"""
Model config and a minimal client for OpenAI-compatible chat endpoints
(mlx_lm.server, LM Studio, Ollama, llama.cpp, ...). Standard library only.

Models are named in config/llm_models.json; set REDTEN_LLM_CONFIG to use
another file. API keys are never stored in the file — a model may name an
environment variable to read one from.
"""
from __future__ import annotations
import json
import os
import urllib.request
from dataclasses import dataclass, field, fields
from typing import Any, Callable, Optional

_REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DEFAULT_CONFIG_PATH = os.path.join(_REPO, "config", "llm_models.json")


class LLMError(Exception):
    """The model server could not be reached or returned nothing usable."""
    reasoning: str = ""      # thinking tokens received before the failure, if any


class ChatReply(str):
    """The reply text, plus any separate reasoning ("thinking") tokens the
    server returned alongside it."""
    reasoning: str

    def __new__(cls, content: str, reasoning: str = "") -> "ChatReply":
        obj = super().__new__(cls, content)
        obj.reasoning = reasoning
        return obj


@dataclass
class ModelConfig:
    name: str
    base_url: str                     # e.g. http://localhost:8080/v1
    model: str                        # model id as the server knows it
    temperature: float = 0.3
    max_tokens: int = 120
    timeout_s: float = 30.0
    extra_body: dict[str, Any] = field(default_factory=dict)  # merged into the request
    api_key_env: str = ""             # name of an env var holding the key, if any
    rules_file: str = "skills/rules_brief.zh.md"
    skill_file: str = "skills/strategy.zh.md"
    note: str = ""


def load_models(path: Optional[str] = None) -> tuple[dict[str, ModelConfig], str]:
    """Return ({name: ModelConfig}, default_name)."""
    path = path or os.environ.get("REDTEN_LLM_CONFIG") or DEFAULT_CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    known = {f.name for f in fields(ModelConfig)}
    models: dict[str, ModelConfig] = {}
    for name, entry in raw.get("models", {}).items():
        unknown = set(entry) - known
        if unknown:
            raise ValueError(f"{path}: model '{name}' has unknown keys {sorted(unknown)}")
        models[name] = ModelConfig(name=name, **entry)
    if not models:
        raise ValueError(f"{path}: no models defined")

    default = raw.get("default") or next(iter(models))
    if default not in models:
        raise ValueError(f"{path}: default '{default}' is not a defined model")
    return models, default


def get_model(name: Optional[str] = None, path: Optional[str] = None) -> ModelConfig:
    models, default = load_models(path)
    key = name or default
    if key not in models:
        raise ValueError(f"Unknown LLM model '{key}'. Defined: {sorted(models)}")
    return models[key]


def chat_completion(cfg: ModelConfig, messages: list[dict],
                    opener: Callable[..., Any] = urllib.request.urlopen) -> ChatReply:
    """POST one chat request and return the reply text. Raises LLMError."""
    body: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "stream": False,
    }
    body.update(cfg.extra_body)

    headers = {"Content-Type": "application/json"}
    key = os.environ.get(cfg.api_key_env, "") if cfg.api_key_env else ""
    if key:
        headers["Authorization"] = f"Bearer {key}"

    url = cfg.base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with opener(req, timeout=cfg.timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content")
        reasoning = str(message.get("reasoning") or message.get("reasoning_content") or "")
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
        raise LLMError(f"{cfg.name}: {type(e).__name__}: {e}") from e

    if not content or not content.strip():
        err = LLMError(f"{cfg.name}: empty reply "
                       f"(finish_reason={choice.get('finish_reason')})")
        err.reasoning = reasoning
        raise err
    return ChatReply(str(content), reasoning)
