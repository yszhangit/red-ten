"""
Tests for the LLM seat: option list, seat view, reply parsing, retry/fallback,
model config and the HTTP client. No model server is needed — the chat call
is always faked.
"""
import io
import json
import os
import random

import pytest

from cards import Card, Rank, Suit
from hand import Hand
from logger import GameLogger
from moves import CardType, Move, get_legal_moves
from state import GameState, PlayerStatus

from llm_client import LLMError, ModelConfig, chat_completion, load_models
from llm_player import LLMPlayer, parse_choice
from llm_view import build_options, build_view, option_key

REPO = os.path.join(os.path.dirname(__file__), "..")


def C(rank: Rank, suit: Suit = Suit.SPADES) -> Card:
    return Card(suit, rank)


RED_TEN = Card(Suit.HEARTS, Rank.TEN)


def dealt_state(seed: int = 1) -> GameState:
    random.seed(seed)
    hand = Hand([None] * 6, 0, GameLogger(verbose=False))  # type: ignore[list-item]
    hand.deal()
    return hand.state


def cfg(**kw) -> ModelConfig:
    base = dict(name="fake", base_url="http://localhost:1/v1", model="fake-model")
    base.update(kw)
    return ModelConfig(**base)  # type: ignore[arg-type]


# ── Option list ───────────────────────────────────────────────────────────────

def test_options_merge_suit_only_duplicates():
    hand = [C(Rank.THREE, Suit.SPADES), C(Rank.THREE, Suit.HEARTS),
            C(Rank.THREE, Suit.DIAMONDS), C(Rank.SEVEN, Suit.CLUBS)]
    legal = get_legal_moves(hand, None)
    options = build_options(legal)

    singles = [o for o in options if o.move.type == CardType.SINGLE]
    assert sorted(o.text for o in singles) == ["单张 3", "单张 7"]


def test_every_option_is_legal_and_every_legal_move_is_covered():
    state = dealt_state(seed=3)
    legal = get_legal_moves(state.hands[0], None)
    options = build_options(legal)

    assert all(o.move in legal for o in options)
    assert {option_key(m) for m in legal} == {option_key(o.move) for o in options}
    assert len({option_key(o.move) for o in options}) == len(options)


def test_options_keep_red_ten_separate_from_plain_ten():
    hand = [RED_TEN, C(Rank.TEN, Suit.SPADES), C(Rank.FOUR, Suit.CLUBS)]
    options = build_options(get_legal_moves(hand, None))

    single_texts = [o.text for o in options if o.move.type == CardType.SINGLE]
    assert any("♥10" in t for t in single_texts)
    assert any(t.startswith("单张 10") and "♥" not in t for t in single_texts)


def test_pass_is_listed_first_when_following():
    hand = [C(Rank.NINE), C(Rank.KING)]
    best = Move(CardType.SINGLE, (C(Rank.FIVE),), C(Rank.FIVE).single_rank())
    options = build_options(get_legal_moves(hand, best))

    assert options[0].move.is_pass()
    assert options[0].text == "过"


def test_option_text_shows_points():
    hand = [C(Rank.KING, Suit.SPADES), C(Rank.KING, Suit.CLUBS)]
    options = build_options(get_legal_moves(hand, None))

    pair = next(o for o in options if o.move.type == CardType.PAIR)
    assert "20分" in pair.text


# ── Seat view: only what this seat may know ───────────────────────────────────

class _GuardedHands(list):
    """Raises if any hand other than the allowed seat's is read."""

    def __init__(self, hands, allowed: int):
        super().__init__(hands)
        self._allowed = allowed

    def __getitem__(self, i):
        if i != self._allowed:
            raise AssertionError(f"view read hidden hand of seat {i}")
        return super().__getitem__(i)

    def __iter__(self):
        raise AssertionError("view iterated over all hands")


class _GuardedStatus(PlayerStatus):
    """Raises if the true team assignment is read."""

    def __getattribute__(self, name):
        if name == "team":
            raise AssertionError("view read a hidden team")
        return super().__getattribute__(name)


def guarded(state: GameState, seat: int) -> GameState:
    state.hands = _GuardedHands(state.hands, seat)
    state.player_statuses = [
        _GuardedStatus(cards_remaining=s.cards_remaining, red_ten_count=s.red_ten_count,
                       finished=s.finished, finish_position=s.finish_position,
                       team=s.__dict__["team"])
        for s in state.player_statuses
    ]
    return state


def view_for(state: GameState, seat: int, current_best=None) -> str:
    legal = get_legal_moves(state.hands[seat], current_best)
    return build_view(state, seat, build_options(legal))


def test_view_never_reads_hidden_hands_or_teams():
    state = guarded(dealt_state(seed=5), seat=2)
    text = view_for(state, 2)
    assert "手牌" in text


def test_view_says_red_side_when_holding_red_ten():
    state = dealt_state(seed=5)
    holder = next(p for p in range(6) if RED_TEN in state.hands[p])
    other = next(p for p in range(6) if RED_TEN not in state.hands[p])

    assert "你是红十方" in view_for(state, holder)
    assert "你是非红十方" in view_for(state, other)


def test_view_still_red_after_playing_own_red_ten():
    state = dealt_state(seed=5)
    holder = next(p for p in range(6) if RED_TEN in state.hands[p])
    n = state.hands[holder].count(RED_TEN)
    for _ in range(n):
        state.hands[holder].remove(RED_TEN)
        state.played_cards.append(RED_TEN)
    state.player_statuses[holder].red_ten_count = n

    assert "你是红十方" in view_for(state, holder)


def test_view_marks_everyone_once_all_red_tens_are_seen():
    state = GameState()
    for p in range(6):
        state.hands[p] = [C(Rank.FOUR), C(Rank.SIX)]
        state.player_statuses[p] = PlayerStatus(cards_remaining=2)
    state.player_statuses[1].red_ten_count = 2
    state.player_statuses[4].red_ten_count = 1
    state.played_cards = [RED_TEN] * 3

    text = view_for(state, 0)
    lines = {p: next(l for l in text.splitlines() if l.startswith(f"座位{p + 1}:"))
             for p in (1, 2, 3, 4, 5)}
    assert "对手" in lines[1] and "对手" in lines[4]
    assert all("队友" in lines[p] for p in (2, 3, 5))


def test_view_keeps_unrevealed_seats_unknown_while_red_tens_are_hidden():
    state = GameState()
    for p in range(6):
        state.hands[p] = [C(Rank.FOUR), C(Rank.SIX)]
        state.player_statuses[p] = PlayerStatus(cards_remaining=2)
    state.player_statuses[1].red_ten_count = 1
    state.played_cards = [RED_TEN]

    text = view_for(state, 0)
    line2 = next(l for l in text.splitlines() if l.startswith("座位3:"))
    assert "身份未知" in line2
    assert "还有2张红十没出现" in text


def test_view_shows_trick_and_current_winner():
    state = GameState()
    for p in range(6):
        state.hands[p] = [C(Rank.NINE), C(Rank.ACE)]
        state.player_statuses[p] = PlayerStatus(cards_remaining=2)
    lead = Move(CardType.SINGLE, (C(Rank.KING),), C(Rank.KING).single_rank())
    state.current_trick = [(1, lead), (0, Move.pass_move())]
    state.played_cards = [C(Rank.KING)]

    text = view_for(state, 5, current_best=lead)
    assert "座位2 出 单张 K" in text
    assert "座位1 过" in text
    assert "桌面分: 10" in text


def test_view_counts_unseen_cards_excluding_own_hand_and_played():
    state = GameState()
    for p in range(6):
        state.hands[p] = [C(Rank.ACE)]
        state.player_statuses[p] = PlayerStatus(cards_remaining=1)
    state.hands[0] = [C(Rank.ACE), C(Rank.ACE, Suit.CLUBS)]
    state.played_cards = [C(Rank.ACE, Suit.DIAMONDS)]

    text = view_for(state, 0)
    unseen = next(l for l in text.splitlines() if l.startswith("外面还没出现的牌"))
    assert "A×9" in unseen          # 12 aces − 2 in hand − 1 played
    assert "大王×3" in unseen


# ── Reply parsing ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("reply, expected", [
    ('{"move": 2, "reason": "压住"}', 2),
    ('{"move": "3"}', 3),
    ('```json\n{"move": 1, "reason": "过"}\n```', 1),
    ('<think>选 4 还是 2 呢</think>\n{"move": 2}', 2),
    ('我选择 {"move":4,"reason":"抢分"} 。', 4),
    ('3', 3),
    ('出牌: 2', 2),
])
def test_parse_choice_accepts(reply, expected):
    assert parse_choice(reply, n_options=5) == expected


@pytest.mark.parametrize("reply", [
    "", "不知道", '{"move": 0}', '{"move": 9}', '{"reason": "没有编号"}',
    "<think>想了很久 3</think>",
])
def test_parse_choice_rejects(reply):
    assert parse_choice(reply, n_options=5) is None


# ── Player: model call, retry, fallback ───────────────────────────────────────

class FakeChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def __call__(self, messages):
        self.calls.append(messages)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def leading_state():
    state = dealt_state(seed=7)
    return state, get_legal_moves(state.hands[0], None)


def test_player_plays_the_option_the_model_names(tmp_path):
    state, legal = leading_state()
    options = build_options(legal)
    chat = FakeChat(['{"move": 3, "reason": "试试"}'])
    player = LLMPlayer(0, cfg(), chat=chat, log_dir=str(tmp_path))

    assert player.choose_action(state, legal) == options[2].move
    assert len(chat.calls) == 1
    assert chat.calls[0][0]["role"] == "system"


def test_player_retries_once_then_uses_second_answer(tmp_path):
    state, legal = leading_state()
    options = build_options(legal)
    chat = FakeChat(["嗯……", '{"move": 1}'])
    player = LLMPlayer(0, cfg(), chat=chat, log_dir=str(tmp_path))

    assert player.choose_action(state, legal) == options[0].move
    assert len(chat.calls) == 2


def test_player_falls_back_after_two_bad_replies(tmp_path):
    state, legal = leading_state()
    chat = FakeChat(["???", '{"move": 999}'])
    player = LLMPlayer(0, cfg(), chat=chat, log_dir=str(tmp_path))

    move = player.choose_action(state, legal)
    assert move in legal
    assert len(chat.calls) == 2
    assert player.stats["fallbacks"] == 1


def test_player_falls_back_when_the_server_fails(tmp_path):
    state, legal = leading_state()
    chat = FakeChat([LLMError("timed out")])
    player = LLMPlayer(0, cfg(), chat=chat, log_dir=str(tmp_path))

    assert player.choose_action(state, legal) in legal
    assert player.stats["fallbacks"] == 1


def test_player_skips_the_model_when_only_one_move_is_legal(tmp_path):
    state = dealt_state(seed=7)
    chat = FakeChat([])
    player = LLMPlayer(0, cfg(), chat=chat, log_dir=str(tmp_path))

    only = [Move.pass_move()]
    assert player.choose_action(state, only) == only[0]
    assert chat.calls == []


def test_player_does_not_modify_state(tmp_path):
    state, legal = leading_state()
    before = (list(state.hands[0]), list(state.played_cards), list(state.current_trick))
    player = LLMPlayer(0, cfg(), chat=FakeChat(['{"move": 2}']), log_dir=str(tmp_path))
    player.choose_action(state, legal)

    assert (list(state.hands[0]), list(state.played_cards), list(state.current_trick)) == before


def test_player_logs_each_decision(tmp_path):
    state, legal = leading_state()
    player = LLMPlayer(0, cfg(), chat=FakeChat(['{"move": 2, "reason": "r"}']),
                       log_dir=str(tmp_path))
    player.choose_action(state, legal)

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    rec = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert rec["seat"] == 0 and rec["choice"] == 2 and rec["fallback"] is False
    assert rec["n_options"] == len(build_options(legal))


def test_full_hand_of_llm_seats_finishes_with_valid_scores(tmp_path):
    from test_integration import check_result

    class RandomChat:
        """Stands in for a model: picks any listed number."""
        def __call__(self, messages):
            numbered = [l for l in messages[-1]["content"].splitlines()
                        if l[:1].isdigit() and ". " in l[:5]]
            return json.dumps({"move": random.randint(1, len(numbered))})

    random.seed(11)
    players = [LLMPlayer(i, cfg(), chat=RandomChat(), log_dir=str(tmp_path))
               for i in range(6)]
    hand = Hand(players, 0, GameLogger(verbose=False))  # type: ignore[arg-type]
    hand.deal()
    result = hand.play()

    assert check_result(result) == []
    assert sum(p.stats["fallbacks"] for p in players) == 0
    assert sum(p.stats["decisions"] for p in players) > 0


# ── Model config and HTTP client ──────────────────────────────────────────────

def test_shipped_config_lists_both_models_and_no_secrets():
    path = os.path.join(REPO, "config", "llm_models.json")
    models, default = load_models(path)

    assert default in models
    assert {"qwen3.5-9b", "gemma4-e4b"} <= set(models)
    assert models["qwen3.5-9b"].base_url.endswith("/v1")
    raw = open(path, encoding="utf-8").read()
    assert '"api_key"' not in raw


def test_shipped_skill_files_exist():
    models, default = load_models(os.path.join(REPO, "config", "llm_models.json"))
    for m in models.values():
        for rel in (m.rules_file, m.skill_file):
            assert os.path.isfile(os.path.join(REPO, rel)), rel


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_client_posts_to_chat_completions_and_merges_extra_body():
    seen = {}

    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode("utf-8"))
        seen["timeout"] = timeout
        payload = {"choices": [{"message": {"content": '{"move": 1}'}}]}
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    c = cfg(base_url="http://localhost:8080/v1/", temperature=0.2, max_tokens=64,
            timeout_s=9, extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    out = chat_completion(c, [{"role": "user", "content": "hi"}], opener=opener)

    assert out == '{"move": 1}'
    assert seen["url"] == "http://localhost:8080/v1/chat/completions"
    assert seen["body"]["model"] == "fake-model"
    assert seen["body"]["max_tokens"] == 64
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen["timeout"] == 9


def test_client_raises_when_the_reply_has_no_content():
    def opener(req, timeout):
        payload = {"choices": [{"message": {"content": None, "reasoning": "想……"},
                                "finish_reason": "length"}]}
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    with pytest.raises(LLMError):
        chat_completion(cfg(), [{"role": "user", "content": "hi"}], opener=opener)


def test_client_wraps_connection_errors():
    def opener(req, timeout):
        raise OSError("connection refused")

    with pytest.raises(LLMError):
        chat_completion(cfg(), [{"role": "user", "content": "hi"}], opener=opener)


# ── LM Studio's native API (the only documented way to switch thinking off) ───

def _lmstudio_cfg(**kw):
    return cfg(api="lmstudio", base_url="http://localhost:1234/api/v1/",
               extra_body={"reasoning": "off"}, **kw)


def test_lmstudio_request_uses_the_native_chat_endpoint_and_fields():
    seen = {}

    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode("utf-8"))
        payload = {"output": [{"type": "message", "content": '{"move": 1}'}], "stats": {}}
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    c = _lmstudio_cfg(temperature=0.2, max_tokens=64)
    chat_completion(c, [{"role": "system", "content": "规则"},
                        {"role": "user", "content": "手牌"}], opener=opener)

    body = seen["body"]
    assert seen["url"] == "http://localhost:1234/api/v1/chat"
    assert body["model"] == "fake-model"
    assert body["system_prompt"] == "规则"
    assert body["input"] == "手牌"
    assert body["max_output_tokens"] == 64
    assert body["temperature"] == 0.2
    assert body["reasoning"] == "off"
    assert body["store"] is False and body["stream"] is False
    assert "messages" not in body and "max_tokens" not in body


def test_lmstudio_reply_separates_message_reasoning_and_token_counts():
    payload = {"output": [{"type": "reasoning", "content": "先想一想"},
                          {"type": "message", "content": '{"move": 2}'}],
               "stats": {"input_tokens": 2796, "total_output_tokens": 40,
                         "reasoning_output_tokens": 25}}

    def opener(req, timeout):
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    out = chat_completion(_lmstudio_cfg(), [{"role": "user", "content": "手牌"}], opener=opener)

    assert out == '{"move": 2}'
    assert out.reasoning == "先想一想"
    assert out.usage["prompt_tokens"] == 2796
    assert out.usage["completion_tokens"] == 40


def test_lmstudio_reply_with_only_reasoning_is_an_error_that_keeps_it():
    payload = {"output": [{"type": "reasoning", "content": "想了又想"}],
               "stats": {"input_tokens": 2796, "total_output_tokens": 120}}

    def opener(req, timeout):
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    with pytest.raises(LLMError) as e:
        chat_completion(_lmstudio_cfg(), [{"role": "user", "content": "手牌"}], opener=opener)
    assert e.value.reasoning == "想了又想"
    assert "completion_tokens=120" in str(e.value)


def test_lmstudio_retry_keeps_the_original_question_in_the_input():
    """The native API takes one input text, so a retry must carry the table
    view, the rejected answer and the correction together."""
    seen = {}

    def opener(req, timeout):
        seen["body"] = json.loads(req.data.decode("utf-8"))
        payload = {"output": [{"type": "message", "content": '{"move": 1}'}]}
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    chat_completion(_lmstudio_cfg(), [
        {"role": "system", "content": "规则"},
        {"role": "user", "content": "手牌在此"},
        {"role": "assistant", "content": "我出对三"},
        {"role": "user", "content": "回答无效"},
    ], opener=opener)

    text = seen["body"]["input"]
    assert text.index("手牌在此") < text.index("我出对三") < text.index("回答无效")


def test_config_rejects_an_unknown_api(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"models": {"m": {
        "base_url": "http://x/v1", "model": "m", "api": "telepathy"}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_models(str(path))


def test_shipped_gemma_entry_switches_thinking_off():
    models, _ = load_models(os.path.join(REPO, "config", "llm_models.json"))
    gemma = models["gemma4-e4b"]
    assert gemma.api == "lmstudio"
    assert gemma.extra_body.get("reasoning") == "off"
