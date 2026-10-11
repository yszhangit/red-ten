"""
Tests for debug mode: a room option that shows AI hands to the human players
and reports what each LLM seat was thinking. No model server is needed.
"""
import io
import json

from moves import get_legal_moves
from llm_client import ChatReply, LLMError, chat_completion
from llm_player import LLMPlayer
from llm_view import build_options
from room_manager import GameRoom

from test_llm_player import FakeChat, cfg, leading_state


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(message: dict, finish: str = "stop"):
    def opener(req, timeout):
        payload = {"choices": [{"message": message, "finish_reason": finish}]}
        return _FakeResponse(json.dumps(payload).encode("utf-8"))
    return opener


# ── Client: reasoning tokens ──────────────────────────────────────────────────

def test_reply_carries_reasoning_tokens():
    out = chat_completion(cfg(), [], opener=_opener(
        {"content": '{"move": 1}', "reasoning": "先看桌面"}))

    assert out == '{"move": 1}'
    assert out.reasoning == "先看桌面"


def test_reply_reads_the_reasoning_content_field_too():
    out = chat_completion(cfg(), [], opener=_opener(
        {"content": '{"move": 1}', "reasoning_content": "另一种字段名"}))

    assert out.reasoning == "另一种字段名"


def test_reply_without_reasoning_has_an_empty_one():
    out = chat_completion(cfg(), [], opener=_opener({"content": '{"move": 1}'}))

    assert out.reasoning == ""


def test_empty_reply_error_keeps_the_reasoning():
    try:
        chat_completion(cfg(), [], opener=_opener(
            {"content": None, "reasoning": "想到一半"}, finish="length"))
    except LLMError as e:
        assert e.reasoning == "想到一半"
    else:
        raise AssertionError("expected LLMError")


# ── Player: decision reports ──────────────────────────────────────────────────

def decide(replies, tmp_path, **kw):
    state, legal = leading_state()
    got: list[dict] = []
    player = LLMPlayer(0, cfg(), chat=FakeChat(replies), log_dir=str(tmp_path), **kw)
    player.on_decision = got.append
    move = player.choose_action(state, legal)
    assert len(got) == 1
    return got[0], move, build_options(legal), player


def test_decision_report_describes_what_the_model_did(tmp_path):
    d, move, options, _ = decide(['{"move": 2, "reason": "先出小牌"}'], tmp_path)

    assert d["seat"] == 0 and d["model"] == "fake"
    assert d["choice"] == 2 and d["move"] == options[1].text
    assert d["reason"] == "先出小牌"
    assert d["thinking"] == ""
    assert d["options"] == [o.text for o in options]
    assert d["fallback"] is False and d["error"] == ""
    assert isinstance(d["latency_ms"], int)
    assert "手牌" in d["view"]
    json.dumps(d)                      # must be sendable as JSON


def test_decision_report_includes_inline_think_block(tmp_path):
    d, *_ = decide(['<think>对手只剩两张</think>{"move": 1}'], tmp_path)

    assert d["thinking"] == "对手只剩两张"
    assert d["choice"] == 1


def test_decision_report_includes_reasoning_tokens(tmp_path):
    d, *_ = decide([ChatReply('{"move": 1}', reasoning="模型的思考")], tmp_path)

    assert d["thinking"] == "模型的思考"


def test_decision_report_marks_a_fallback(tmp_path):
    err = LLMError("fake: empty reply")
    err.reasoning = "想到一半就没了"
    d, move, options, _ = decide([err], tmp_path)

    assert d["fallback"] is True and d["choice"] is None
    assert "empty reply" in d["error"]
    assert d["thinking"] == "想到一半就没了"
    assert d["move"] in [o.text for o in options]


def test_explain_mode_asks_for_an_analysis_and_reports_it(tmp_path):
    d, _, _, player = decide(['{"analysis": "牌多，先走顺子。", "move": 3}'],
                             tmp_path, explain=True)

    last_line = player._chat.calls[0][-1]["content"].splitlines()[-1]
    assert '"analysis"' in last_line
    assert d["reason"] == "牌多，先走顺子。"
    assert d["choice"] == 3


def test_normal_mode_asks_only_for_a_short_reason(tmp_path):
    _, _, _, player = decide(['{"move": 1}'], tmp_path)

    last_line = player._chat.calls[0][-1]["content"].splitlines()[-1]
    assert '"reason"' in last_line and '"analysis"' not in last_line


def test_a_failing_decision_listener_does_not_break_the_move(tmp_path):
    state, legal = leading_state()
    player = LLMPlayer(0, cfg(), chat=FakeChat(['{"move": 1}']), log_dir=str(tmp_path))

    def boom(_):
        raise RuntimeError("listener bug")

    player.on_decision = boom
    assert player.choose_action(state, legal) in legal


# ── Room: what human players are shown ────────────────────────────────────────

def make_room(debug: bool, tier: str = "rule", human_seats=(0,)):
    room = GameRoom("TEST", ai_tier=tier, debug=debug)
    sent: dict[int, list] = {i: [] for i in range(6)}
    for i, seat in enumerate(room.seats):
        if i in human_seats:
            seat._ws = object()                       # marks the seat as human
        seat.send = (lambda data, i=i: sent[i].append(data)) if i in human_seats \
            else (lambda data: None)
    return room, sent


def test_debug_room_shows_ai_hands_from_the_start():
    room, _ = make_room(debug=True)

    assert room.reveal_seats(all_humans_done=False) == {1, 2, 3, 4, 5}


def test_normal_room_shows_ai_hands_only_after_humans_finish():
    room, _ = make_room(debug=False)

    assert room.reveal_seats(all_humans_done=False) is None
    assert room.reveal_seats(all_humans_done=True) == {1, 2, 3, 4, 5}


def test_debug_room_never_reveals_another_humans_hand():
    room, _ = make_room(debug=True, human_seats=(0, 3))

    assert room.reveal_seats(all_humans_done=False) == {1, 2, 4, 5}


def test_room_state_says_whether_debug_is_on():
    assert make_room(debug=True)[0].room_state_msg(0)["debug"] is True
    assert make_room(debug=False)[0].room_state_msg(0)["debug"] is False


def test_debug_room_sends_thoughts_to_humans_and_remembers_them():
    room, sent = make_room(debug=True)
    room.broadcast_thought({"seat": 2, "reason": "试试", "move": "单张 3"})

    assert sent[0] == [{"type": "ai_thought", "seat": 2, "reason": "试试", "move": "单张 3"}]
    assert room.thoughts[2]["reason"] == "试试"


def test_normal_room_never_sends_thoughts():
    room, sent = make_room(debug=False)
    room.broadcast_thought({"seat": 2, "reason": "试试"})

    assert sent[0] == []
    assert room.thoughts == {}


def test_debug_room_listens_to_its_llm_seats():
    room, sent = make_room(debug=True, tier="llm")
    ai = room.seats[4].ai_player

    assert isinstance(ai, LLMPlayer) and ai.explain is True
    ai.on_decision({"seat": 4, "reason": "r"})
    assert sent[0][0]["type"] == "ai_thought" and sent[0][0]["seat"] == 4


def test_normal_room_leaves_llm_seats_alone():
    room, _ = make_room(debug=False, tier="llm")
    ai = room.seats[4].ai_player

    assert ai.explain is False and ai.on_decision is None


def test_create_room_request_can_turn_debug_on():
    import web_server

    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule", debug=True))["room_code"]
    assert web_server.rooms.get(code).debug is True

    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]
    assert web_server.rooms.get(code).debug is False
