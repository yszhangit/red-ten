"""
LLM seat — a local language model picks the play.

The model is shown every distinct legal play as a numbered list plus a text
view of the table (see llm_view.py) and answers with a number. The reply is
validated; a bad reply gets one retry, then the turn falls back to
RuleBasedPlayer so a slow or broken model can never stall a game.

Rules and strategy text come from the files named in the model's config
(skills/*.zh.md), so play can be tuned without touching code.

Every decision is described by a report dict (what the model was shown, what
it answered, any thinking tokens, timing). It is appended to a JSONL log and
handed to `on_decision` if set — debug mode uses that to show it in the UI.

The server log (stdout) gets one line per decision, plus the raw reply and
what the server said about it whenever a reply could not be used.
"""
from __future__ import annotations
import dataclasses
import datetime
import json
import os
import re
import time
from typing import Callable, Optional

from moves import Move
from state import GameState
from hand import Player
from rule_player import RuleBasedPlayer
from llm_client import LLMError, ModelConfig, chat_completion, log
from llm_view import Option, build_options, build_view, describe_move

_REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DEFAULT_LOG_DIR = os.path.join(_REPO, "debug_logs", "llm")

_OUTPUT_RULE = (
    "每次轮到你时，程序会列出所有合法的出牌并编号，你只能从中选一个。\n"
    "严格按照消息末尾给出的 JSON 格式回答，不要输出别的内容。"
)
# Room for the longer answer that explain mode asks for.
_EXPLAIN_MIN_TOKENS = 300

# Inline thinking: Qwen-style <think> blocks and Gemma 4's thought channel.
_THINK_RE = re.compile(r"<think>(.*?)</think>|<\|channel>thought(.*?)<channel\|>", re.S)
_THINK_OPEN_RE = re.compile(r"<think>|<\|channel>thought")
# How much of a reply the server log shows when it could not be used.
_LOG_REPLY_CHARS = 800
_MOVE_RE = re.compile(r'"move"\s*:\s*"?\s*(-?\d+)')
_TEXT_FIELD_RE = r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"'

_prompt_cache: dict[tuple[str, str], str] = {}


def parse_choice(reply: str, n_options: int) -> Optional[int]:
    """Extract the 1-based option number from a model reply, or None."""
    text = _THINK_RE.sub("", reply or "")
    unfinished = _THINK_OPEN_RE.search(text)   # thinking that never ended
    if unfinished:
        text = text[:unfinished.start()]
    m = _MOVE_RE.search(text)
    if m:
        n = int(m.group(1))
    else:
        if '"reason"' in text or '"analysis"' in text:
            return None
        nums = set(re.findall(r"-?\d+", text))
        if len(nums) != 1:
            return None
        n = int(nums.pop())
    return n if 1 <= n <= n_options else None


def _stated_reason(reply: str) -> str:
    """The model's own explanation: the "analysis" or "reason" field."""
    for name in ("analysis", "reason"):
        m = re.search(_TEXT_FIELD_RE % name, reply)
        if m:
            try:
                return str(json.loads('"' + m.group(1) + '"'))
            except ValueError:
                return m.group(1)
    return ""


def _thinking(reply: str) -> str:
    """Thinking tokens: a separate reasoning field, or an inline thinking block."""
    separate = getattr(reply, "reasoning", "") or ""
    if separate:
        return str(separate).strip()
    blocks = _THINK_RE.findall(reply or "")
    return "\n".join((a or b).strip() for a, b in blocks)


def _reply_facts(reply: str) -> str:
    """What the server said about a reply, for the log."""
    parts = []
    finish = getattr(reply, "finish_reason", "")
    if finish:
        parts.append(f"finish_reason={finish}")
    usage = getattr(reply, "usage", None) or {}
    if usage:
        parts.append(f"tokens {usage.get('prompt_tokens', '?')}"
                     f"+{usage.get('completion_tokens', '?')}")
    thinking = _thinking(reply)
    if thinking:
        parts.append(f"thinking {len(thinking)} chars")
    return ", ".join(parts)


def system_prompt(cfg: ModelConfig) -> str:
    """Rules + strategy + output format. Identical for every seat using the
    same model, so the server can cache it as a shared prefix."""
    key = (cfg.rules_file, cfg.skill_file)
    if key not in _prompt_cache:
        parts = []
        for rel in key:
            path = rel if os.path.isabs(rel) else os.path.join(_REPO, rel)
            with open(path, encoding="utf-8") as f:
                parts.append(f.read().strip())
        parts.append(_OUTPUT_RULE)
        _prompt_cache[key] = "\n\n".join(parts)
    return _prompt_cache[key]


class LLMPlayer(Player):
    """A seat played by a language model behind an OpenAI-compatible API."""

    # SeatController adds an artificial "thinking" pause for AI seats;
    # a model call is slow enough already.
    has_own_latency = True

    def __init__(self, player_id: int, config: ModelConfig,
                 chat: Optional[Callable[[list[dict]], str]] = None,
                 fallback: Optional[Player] = None,
                 log_dir: Optional[str] = None,
                 explain: bool = False):
        super().__init__(player_id)
        self.config = config
        self._chat = chat
        self._fallback = fallback or RuleBasedPlayer(player_id)
        self._log_dir = log_dir or DEFAULT_LOG_DIR
        # Ask the model to write a short analysis with each choice.
        self.explain = explain
        # Called with the decision report after every model-backed decision.
        self.on_decision: Optional[Callable[[dict], None]] = None
        self.stats = {"decisions": 0, "model_calls": 0, "retries": 0,
                      "fallbacks": 0, "latency_s": 0.0}

    def _ask(self, messages: list[dict]) -> str:
        if self._chat is not None:
            return self._chat(messages)
        cfg = self.config
        if self.explain and cfg.max_tokens < _EXPLAIN_MIN_TOKENS:
            cfg = dataclasses.replace(cfg, max_tokens=_EXPLAIN_MIN_TOKENS)
        return chat_completion(cfg, messages)

    def choose_action(self, state: GameState, legal_moves: list[Move]) -> Move:
        if not legal_moves:
            raise RuntimeError(f"P{self.id}: no legal moves")
        if len(legal_moves) == 1:
            return legal_moves[0]

        options = build_options(legal_moves)
        if len(options) == 1:
            return options[0].move

        view = build_view(state, self.id, options, explain=self.explain)
        messages = [{"role": "system", "content": system_prompt(self.config)},
                    {"role": "user", "content": view}]

        self.stats["decisions"] += 1
        replies: list[str] = []
        choice: Optional[int] = None
        error = ""
        thinking = ""
        t0 = time.time()
        try:
            for attempt in range(2):
                self.stats["model_calls"] += 1
                reply = self._ask(messages)
                replies.append(reply)
                thinking = _thinking(reply) or thinking
                choice = parse_choice(reply, len(options))
                if choice is not None:
                    break
                log.warning("P%d %s: reply %d of 2 not usable (%s): %r",
                            self.id, self.config.name, attempt + 1,
                            _reply_facts(reply) or "no details from server",
                            str(reply)[:_LOG_REPLY_CHARS])
                if attempt == 0:
                    self.stats["retries"] += 1
                    messages = messages + [
                        {"role": "assistant", "content": reply},
                        {"role": "user", "content":
                            f'回答无效。请只输出 {{"move": 编号}}，编号必须在 1 到 {len(options)} 之间。'},
                    ]
        except LLMError as e:
            error = str(e)
            thinking = e.reasoning.strip() or thinking
            log.warning("P%d %s: request failed: %s", self.id, self.config.name, error)
            if e.reasoning.strip():
                log.warning("P%d %s: thinking received before the failure: %r",
                            self.id, self.config.name,
                            e.reasoning.strip()[:_LOG_REPLY_CHARS])
        latency = time.time() - t0
        self.stats["latency_s"] += latency

        if choice is not None:
            move = options[choice - 1].move
        else:
            self.stats["fallbacks"] += 1
            move = self._fallback.choose_action(state, legal_moves)
            if move not in legal_moves:
                move = legal_moves[0]
            log.warning("P%d %s: FALLBACK to rule AI after %.1fs (%d options) → %s",
                        self.id, self.config.name, latency, len(options),
                        describe_move(move))

        self._report(view, options, replies, choice, move, latency, error, thinking)
        return move

    def _report(self, view: str, options: list[Option], replies: list[str],
                choice: Optional[int], move: Move, latency: float,
                error: str, thinking: str) -> None:
        record = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "seat": self.id,
            "model": self.config.name,
            "n_options": len(options),
            "choice": choice,
            "move": describe_move(move),
            "reason": _stated_reason(replies[-1]) if replies and choice is not None else "",
            "thinking": thinking,
            "fallback": choice is None,
            "error": error,
            "latency_ms": round(latency * 1000),
            "replies": [str(r) for r in replies],
            "options": [o.text for o in options],
            "view": view,
        }
        if choice is not None:
            facts = _reply_facts(replies[-1])
            log.info("P%d %s: %d options, %.1fs%s → #%d %s | %s",
                     self.id, self.config.name, len(options), latency,
                     f" ({facts})" if facts else "", choice, record["move"],
                     record["reason"])
        try:
            os.makedirs(self._log_dir, exist_ok=True)
            path = os.path.join(self._log_dir, f"{datetime.date.today().isoformat()}.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
        if self.on_decision is not None:
            try:
                self.on_decision(record)
            except Exception:
                pass    # a listener bug must never cost the seat its move
