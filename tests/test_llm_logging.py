"""
Tests for what the LLM seat writes to the server log, and for the reply
formats of models other than the development one (Gemma 4 thinking channel,
HTTP error bodies). No model server is needed.
"""
import io
import json
import logging
import random
import urllib.error

import pytest

from hand import Hand
from logger import GameLogger
from moves import get_legal_moves

from llm_client import ChatReply, LLMError, ModelConfig, chat_completion
from llm_player import LLMPlayer, parse_choice

LOGGER = "redten.llm"


def cfg(**kw) -> ModelConfig:
    base = dict(name="fake", base_url="http://localhost:1/v1", model="fake-model")
    base.update(kw)
    return ModelConfig(**base)  # type: ignore[arg-type]


def leading_state():
    random.seed(7)
    hand = Hand([None] * 6, 0, GameLogger(verbose=False))  # type: ignore[list-item]
    hand.deal()
    return hand.state, get_legal_moves(hand.state.hands[0], None)


class FakeChat:
    def __init__(self, replies):
        self.replies = list(replies)

    def __call__(self, messages):
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(payload):
    def opener(req, timeout):
        return _FakeResponse(json.dumps(payload).encode("utf-8"))
    return opener


# ── Gemma 4 thinking channel ──────────────────────────────────────────────────

def test_parse_choice_ignores_a_gemma_thought_channel():
    reply = ('<|channel>thought\n出 3 还是 5？也许 {"move": 4}。<channel|>'
             '{"move": 2, "reason": "小牌"}')
    assert parse_choice(reply, n_options=5) == 2


def test_parse_choice_rejects_an_unfinished_gemma_thought_channel():
    assert parse_choice("<|channel>thought\n我觉得应该出 3", n_options=5) is None


def test_report_keeps_inline_gemma_thinking(tmp_path):
    state, legal = leading_state()
    seen = []
    player = LLMPlayer(0, cfg(), log_dir=str(tmp_path), chat=FakeChat(
        ['<|channel>thought\n先出小牌。<channel|>{"move": 2}']))
    player.on_decision = seen.append
    player.choose_action(state, legal)

    assert seen[0]["choice"] == 2
    assert seen[0]["thinking"] == "先出小牌。"


# ── Client: what it keeps and how it explains failures ────────────────────────

def test_client_keeps_finish_reason_and_token_usage():
    payload = {"choices": [{"message": {"content": '{"move": 1}'}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 2800, "completion_tokens": 9}}
    out = chat_completion(cfg(), [{"role": "user", "content": "hi"}], opener=_opener(payload))

    assert out.finish_reason == "stop"
    assert out.usage["prompt_tokens"] == 2800


def test_client_error_includes_the_servers_own_message():
    body = b'{"error": "prompt is greater than the context length (4096)"}'

    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},  # type: ignore[arg-type]
                                     io.BytesIO(body))

    with pytest.raises(LLMError) as e:
        chat_completion(cfg(), [{"role": "user", "content": "hi"}], opener=opener)
    assert "400" in str(e.value)
    assert "context length (4096)" in str(e.value)


def test_client_says_when_the_reply_was_all_thinking():
    payload = {"choices": [{"message": {"content": "", "reasoning_content": "想" * 400},
                            "finish_reason": "length"}],
               "usage": {"prompt_tokens": 2800, "completion_tokens": 120}}

    with pytest.raises(LLMError) as e:
        chat_completion(cfg(), [{"role": "user", "content": "hi"}], opener=_opener(payload))
    msg = str(e.value)
    assert "finish_reason=length" in msg
    assert "completion_tokens=120" in msg
    assert "thinking" in msg
    assert e.value.reasoning == "想" * 400


def test_client_names_the_timeout_setting():
    def opener(req, timeout):
        raise TimeoutError("timed out")

    with pytest.raises(LLMError) as e:
        chat_completion(cfg(timeout_s=30), [{"role": "user", "content": "hi"}], opener=opener)
    assert "timeout_s" in str(e.value)


def test_client_logs_the_raw_response_at_debug_level(caplog):
    payload = {"choices": [{"message": {"content": '{"move": 1}', "odd_field": "x1y2"},
                            "finish_reason": "stop"}]}
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        chat_completion(cfg(), [{"role": "system", "content": "规则"},
                                {"role": "user", "content": "手牌在此"}],
                        opener=_opener(payload))

    text = caplog.text
    assert "http://localhost:1/v1/chat/completions" in text
    assert "手牌在此" in text          # what was asked
    assert "odd_field" in text         # the response exactly as the server sent it


def test_client_is_quiet_about_raw_bodies_at_info_level(caplog):
    payload = {"choices": [{"message": {"content": '{"move": 1}', "odd_field": "x1y2"}}]}
    with caplog.at_level(logging.INFO, logger=LOGGER):
        chat_completion(cfg(), [{"role": "user", "content": "手牌在此"}], opener=_opener(payload))

    assert "odd_field" not in caplog.text


# ── Player: one line per decision, full detail when something goes wrong ──────

def test_player_logs_one_line_for_a_good_decision(tmp_path, caplog):
    state, legal = leading_state()
    player = LLMPlayer(3, cfg(name="gemma4-e4b"), log_dir=str(tmp_path),
                       chat=FakeChat(['{"move": 2, "reason": "先出小牌"}']))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        move = player.choose_action(state, legal)

    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1
    assert "P3" in lines[0] and "gemma4-e4b" in lines[0]
    assert "#2" in lines[0] and "先出小牌" in lines[0]
    assert move in legal


def test_player_logs_the_raw_reply_it_could_not_use(tmp_path, caplog):
    state, legal = leading_state()
    bad = ChatReply("我想出一对三", finish_reason="stop",
                    usage={"prompt_tokens": 2800, "completion_tokens": 7})
    player = LLMPlayer(0, cfg(), log_dir=str(tmp_path), chat=FakeChat([bad, '{"move": 1}']))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        player.choose_action(state, legal)

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "我想出一对三" in warnings[0]
    assert "finish_reason=stop" in warnings[0]
    assert "2800" in warnings[0]


def test_player_logs_the_error_and_the_fallback(tmp_path, caplog):
    state, legal = leading_state()
    err = LLMError("fake: HTTP 400 Bad Request: context length (4096)")
    err.reasoning = "半截思考"
    player = LLMPlayer(0, cfg(), log_dir=str(tmp_path), chat=FakeChat([err]))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        player.choose_action(state, legal)

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "context length (4096)" in text
    assert "半截思考" in text
    assert "FALLBACK" in text
