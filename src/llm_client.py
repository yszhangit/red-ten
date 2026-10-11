"""
Model config and a minimal client for OpenAI-compatible chat endpoints
(mlx_lm.server, LM Studio, Ollama, llama.cpp, ...). Standard library only.

A model entry may set "api": "lmstudio" to use LM Studio's native endpoint
(POST <base_url>/chat, base_url ending in /api/v1) instead. That endpoint
takes "reasoning": "off", which its OpenAI-compatible one does not.

Models are named in config/llm_models.json; set REDTEN_LLM_CONFIG to use
another file. API keys are never stored in the file — a model may name an
environment variable to read one from.

Everything an LLM seat does is logged on the "redten.llm" logger, which prints
to stdout: one line per decision, and the raw reply whenever it could not be
used. Set REDTEN_LLM_DEBUG=1 (or call set_debug) to also print every request
and the response body exactly as the server sent it.
"""
from __future__ import annotations
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field, fields
from typing import Any, Callable, Optional

_REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DEFAULT_CONFIG_PATH = os.path.join(_REPO, "config", "llm_models.json")

log = logging.getLogger("redten.llm")


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever sys.stdout is at the time of the message."""

    def __init__(self) -> None:
        logging.Handler.__init__(self)
        self.terminator = "\n"

    @property                                   # type: ignore[override]
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, value) -> None:
        pass


def set_debug(on: bool = True) -> None:
    """Also log every request and raw response (on), or only decisions (off)."""
    log.setLevel(logging.DEBUG if on else logging.INFO)


if not log.handlers:
    _handler = _StdoutHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [llm] %(message)s", "%H:%M:%S"))
    log.addHandler(_handler)
    set_debug(os.environ.get("REDTEN_LLM_DEBUG", "") not in ("", "0"))


class LLMError(Exception):
    """The model server could not be reached or returned nothing usable."""
    reasoning: str = ""      # thinking tokens received before the failure, if any


class ChatReply(str):
    """The reply text, plus what the server said about it: any separate
    reasoning ("thinking") tokens, why it stopped, and token counts."""
    reasoning: str
    finish_reason: str
    usage: dict[str, Any]

    def __new__(cls, content: str, reasoning: str = "", finish_reason: str = "",
                usage: Optional[dict[str, Any]] = None) -> "ChatReply":
        obj = super().__new__(cls, content)
        obj.reasoning = reasoning
        obj.finish_reason = finish_reason
        obj.usage = usage or {}
        return obj


@dataclass
class ModelConfig:
    name: str
    base_url: str                     # e.g. http://localhost:8080/v1
    model: str                        # model id as the server knows it
    api: str = "openai"               # request format: "openai" or "lmstudio"
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
        if models[name].api not in APIS:
            raise ValueError(f"{path}: model '{name}' has unknown api "
                             f"'{models[name].api}'. Known: {sorted(APIS)}")
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


# ── Request formats ───────────────────────────────────────────────────────────
# Each format builds (path, body) from the messages and reads
# (content, reasoning, finish_reason, usage) back out of the response.

def _openai_request(cfg: ModelConfig, messages: list[dict]) -> tuple[str, dict[str, Any]]:
    return "/chat/completions", {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "stream": False,
    }


def _openai_parse(data: dict) -> tuple[Any, str, str, dict[str, Any]]:
    choice = data["choices"][0]
    message = choice["message"]
    reasoning = str(message.get("reasoning") or message.get("reasoning_content") or "")
    return (message.get("content"), reasoning, str(choice.get("finish_reason") or ""),
            data.get("usage") or {})


def _lmstudio_request(cfg: ModelConfig, messages: list[dict]) -> tuple[str, dict[str, Any]]:
    system = "\n\n".join(str(m["content"]) for m in messages if m["role"] == "system")
    # One input text, no roles: a retry carries the question, the rejected
    # answer and the correction together.
    turns = [m for m in messages if m["role"] != "system"]
    text = "\n\n".join(
        ("【你上一次的回答】\n" if m["role"] == "assistant" else "") + str(m["content"])
        for m in turns)
    body: dict[str, Any] = {
        "model": cfg.model,
        "input": text,
        "temperature": cfg.temperature,
        "max_output_tokens": cfg.max_tokens,
        "stream": False,
        "store": False,               # do not save every turn as a chat in LM Studio
    }
    if system:
        body["system_prompt"] = system
    return "/chat", body


def _lmstudio_parse(data: dict) -> tuple[Any, str, str, dict[str, Any]]:
    items = data["output"]
    content = "".join(str(i.get("content") or "") for i in items if i.get("type") == "message")
    reasoning = "\n".join(str(i.get("content") or "") for i in items
                          if i.get("type") == "reasoning")
    stats = data.get("stats") or {}
    usage = {ours: stats[theirs] for ours, theirs in (
        ("prompt_tokens", "input_tokens"),
        ("completion_tokens", "total_output_tokens"),
        ("reasoning_tokens", "reasoning_output_tokens")) if theirs in stats}
    return content, reasoning, "", usage


APIS: dict[str, tuple[Callable, Callable]] = {
    "openai": (_openai_request, _openai_parse),
    "lmstudio": (_lmstudio_request, _lmstudio_parse),
}


def chat_completion(cfg: ModelConfig, messages: list[dict],
                    opener: Callable[..., Any] = urllib.request.urlopen) -> ChatReply:
    """POST one chat request and return the reply text. Raises LLMError."""
    build, parse = APIS[cfg.api]
    path, body = build(cfg, messages)
    body.update(cfg.extra_body)

    headers = {"Content-Type": "application/json"}
    key = os.environ.get(cfg.api_key_env, "") if cfg.api_key_env else ""
    if key:
        headers["Authorization"] = f"Bearer {key}"

    url = cfg.base_url.rstrip("/") + path
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    if log.isEnabledFor(logging.DEBUG):
        settings = {k: v for k, v in body.items()
                    if k not in ("messages", "input", "system_prompt")}
        log.debug("→ POST %s %s", url, json.dumps(settings, ensure_ascii=False))
        for m in messages:
            log.debug("→ %s (%d chars):\n%s", m.get("role"), len(str(m.get("content"))),
                      m.get("content"))
    try:
        with opener(req, timeout=cfg.timeout_s) as resp:
            raw = resp.read().decode("utf-8")
        log.debug("← raw response:\n%s", raw)
        content, reasoning, finish, usage = parse(json.loads(raw))
    except urllib.error.HTTPError as e:
        # The body is where the server says what it did not like
        # (unknown model name, prompt longer than the context window, ...).
        try:
            detail = e.read().decode("utf-8", "replace").strip()[:500]
        except Exception:
            detail = ""
        raise LLMError(f"{cfg.name}: HTTP {e.code} {e.reason}: {detail}") from e
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
        hint = ""
        if isinstance(e, TimeoutError) or "timed out" in str(e):
            hint = (f" — no reply within timeout_s={cfg.timeout_s:g}; "
                    "raise it in config/llm_models.json if the model is just slow")
        raise LLMError(f"{cfg.name}: {type(e).__name__}: {e}{hint}") from e

    if not content or not str(content).strip():
        facts = [f"finish_reason={finish}"] if finish else []
        if "completion_tokens" in usage:
            facts.append(f"completion_tokens={usage['completion_tokens']}")
        msg = f"{cfg.name}: empty reply" + (f" ({', '.join(facts)})" if facts else "")
        if reasoning:
            msg += (f" — the model spent its reply on thinking ({len(reasoning)} chars); "
                    "turn thinking off or raise max_tokens")
        err = LLMError(msg)
        err.reasoning = reasoning
        raise err
    return ChatReply(str(content), reasoning, finish, usage)
