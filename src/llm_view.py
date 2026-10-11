"""
What an LLM seat is shown: a numbered list of its legal plays and a text
description of the table.

Hidden-information rule: everything here is built from the seat's own hand and
public table state (played cards, card counts, revealed red tens, trick points).
It never reads another seat's hand or the true team assignment.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass

from cards import Card, Rank, RANK_LABELS
from moves import Move, CardType
from state import GameState

# Ranks low → high in single-card order (♥10 is handled separately).
_RANK_ORDER: list[Rank] = [
    Rank.THREE, Rank.FOUR, Rank.FIVE, Rank.SIX, Rank.SEVEN, Rank.EIGHT, Rank.NINE,
    Rank.TEN, Rank.JACK, Rank.QUEEN, Rank.KING, Rank.ACE, Rank.TWO,
    Rank.SMALL_JOKER, Rank.BIG_JOKER,
]
_COPIES_PER_RANK = {r: (3 if r in (Rank.SMALL_JOKER, Rank.BIG_JOKER) else 12)
                    for r in _RANK_ORDER}

_TYPE_ORDER = [
    CardType.PASS, CardType.SINGLE, CardType.PAIR, CardType.TRIPLE,
    CardType.TRIPLE_PAIR, CardType.STRAIGHT, CardType.CONSEC_PAIRS,
    CardType.CONSEC_TRIPLES, CardType.BOMB,
]
_SPECIAL_BOMBS = {1: "三红十", 11: "双红十", 7: "三大王", 12: "双大王",
                  8: "三小王", 13: "双小王"}


@dataclass(frozen=True)
class Option:
    move: Move
    text: str


def _red_tens(cards) -> int:
    return sum(1 for c in cards if c.is_red_ten())


def _label(c: Card) -> str:
    return "♥10" if c.is_red_ten() else RANK_LABELS[c.rank]


def option_key(move: Move) -> tuple:
    """Plays with the same key differ only in suit, which never matters —
    except ♥10, so the number of red tens is part of the key."""
    return (int(move.type), tuple(sorted(int(c.rank) for c in move.cards)),
            _red_tens(move.cards))


def _run_order(move: Move) -> list[Rank]:
    """Distinct ranks of a straight / consecutive group, in run order."""
    low = any(c.rank == Rank.TWO for c in move.cards)

    def key(c: Card) -> int:
        if move.type == CardType.STRAIGHT:
            return c.straight_rank(ace_low=low)
        return c.consec_pair_rank_low() if low else c.consec_pair_rank_high()

    seen: list[Rank] = []
    for c in sorted(move.cards, key=key):
        if c.rank not in seen:
            seen.append(c.rank)
    return seen


def describe_move(move: Move) -> str:
    """Short Chinese description of a play, e.g. '对子 K〔20分〕'."""
    if move.is_pass():
        return "过"
    t = move.type
    n = len(move.cards)
    reds = _red_tens(move.cards)
    first = RANK_LABELS[move.cards[0].rank]
    note_reds = reds > 0

    if t == CardType.SINGLE:
        body = f"单张 {_label(move.cards[0])}"
        note_reds = False
    elif t == CardType.PAIR:
        body = f"对子 {first}"
    elif t == CardType.TRIPLE:
        body = f"三张 {first}"
    elif t == CardType.TRIPLE_PAIR:
        cnt = Counter(c.rank for c in move.cards)
        # The engine also offers five of one rank as triple+pair.
        triple = next(r for r, v in cnt.items() if v >= 3)
        pair = next((r for r, v in cnt.items() if v == 2), triple)
        body = f"三带二 {RANK_LABELS[triple]}×3带{RANK_LABELS[pair]}×2"
    elif t == CardType.STRAIGHT:
        body = "顺子 " + "-".join(RANK_LABELS[r] for r in _run_order(move))
    elif t == CardType.CONSEC_PAIRS:
        body = "连对 " + "-".join(RANK_LABELS[r] * 2 for r in _run_order(move))
    elif t == CardType.CONSEC_TRIPLES:
        body = "连三条 " + "-".join(RANK_LABELS[r] * 3 for r in _run_order(move))
    else:
        level = move.bomb_key[0] if move.bomb_key else 0
        if level in _SPECIAL_BOMBS:
            body = f"炸弹 {_SPECIAL_BOMBS[level]}"
            note_reds = False
        else:
            body = f"炸弹 {n}张{first}"

    if note_reds:
        body += f"（含♥10×{reds}）"
    pts = sum(c.score_value() for c in move.cards)
    if pts:
        body += f"〔{pts}分〕"
    return body


def _sort_key(move: Move) -> tuple:
    t = _TYPE_ORDER.index(move.type)
    if move.is_bomb() and move.bomb_key:
        level, count, rank = move.bomb_key
        return (t, -level, count, rank, (), 0)      # weakest bomb first
    ranks = tuple(sorted(c.single_rank() for c in move.cards))
    return (t, len(move.cards), move.rank, ranks, _red_tens(move.cards))


def build_options(legal_moves: list[Move]) -> list[Option]:
    """Every distinct legal play, pass first, then by type and strength."""
    by_key: dict[tuple, Move] = {}
    for m in legal_moves:
        by_key.setdefault(option_key(m), m)
    moves = sorted(by_key.values(), key=_sort_key)
    return [Option(m, describe_move(m)) for m in moves]


# ── Table description ─────────────────────────────────────────────────────────

def _seat(p: int) -> str:
    return f"座位{p + 1}"


def _hand_text(hand: list[Card]) -> str:
    cnt = Counter(c.rank for c in hand)
    reds = _red_tens(hand)
    parts = []
    for r in _RANK_ORDER:
        n = cnt.get(r, 0)
        if n == 0:
            continue
        s = RANK_LABELS[r] if n == 1 else f"{RANK_LABELS[r]}×{n}"
        if r == Rank.TEN and reds:
            s += f"(其中♥10×{reds})"
        parts.append(s)
    return " ".join(parts)


def _unseen_text(state: GameState, me: int) -> str:
    seen = Counter(c.rank for c in state.hands[me])
    seen.update(c.rank for c in state.played_cards)
    return " ".join(f"{RANK_LABELS[r]}×{_COPIES_PER_RANK[r] - seen.get(r, 0)}"
                    for r in _RANK_ORDER)


def _identity(state: GameState, p: int, i_am_red: bool, unseen_red: int) -> str:
    shown = state.player_statuses[p].red_ten_count
    if shown > 0:
        side = f"已亮♥10×{shown}"
        return f"{side}，是你的队友" if i_am_red else f"{side}，是对手"
    if unseen_red == 0:
        return "非红十方，是对手" if i_am_red else "非红十方，是你的队友"
    return "身份未知"


def build_view(state: GameState, me: int, options: list[Option],
               explain: bool = False) -> str:
    """The per-turn message for seat `me`. With `explain`, the model is asked
    to write a short analysis before its choice (used by debug mode)."""
    hand = state.hands[me]
    my_status = state.player_statuses[me]
    held_red = _red_tens(hand)
    i_am_red = held_red > 0 or my_status.red_ten_count > 0
    shown_total = sum(s.red_ten_count for s in state.player_statuses)
    unseen_red = max(0, 3 - shown_total - held_red)

    out: list[str] = []

    # Me
    if i_am_red:
        if held_red and my_status.red_ten_count == 0:
            side = "你是红十方（♥10还在手里，别人不知道你的身份）"
        elif held_red:
            side = "你是红十方（身份已公开，手里还有♥10）"
        else:
            side = "你是红十方（身份已公开）"
    else:
        side = "你是非红十方（你没有♥10）"
    out.append(f"【你】{_seat(me)}。{side}。已得{state.trick_scores[me]}分。")
    out.append(f"手牌({len(hand)}张): {_hand_text(hand)}")

    # Red tens
    if unseen_red == 0:
        out.append("红十: 3张♥10都已有着落，所有人的身份都确定了。")
    else:
        out.append(f"红十: 还有{unseen_red}张红十没出现（在别人手里）。")

    # Others, in turn order starting with the next to act after me
    out.append("【其他玩家】按出牌顺序，从你的下家开始:")
    for p in state.turn_order_from(me)[1:]:
        st = state.player_statuses[p]
        who = _identity(state, p, i_am_red, unseen_red)
        if st.finished:
            pos = st.finish_position
            tag = f"已出完（第{pos}个" + ("，大贡）" if pos == 1 else "）")
        else:
            tag = f"剩{st.cards_remaining}张"
        out.append(f"{_seat(p)}: {tag}，已得{state.trick_scores[p]}分，{who}")

    # Current trick
    plays = [(p, m) for p, m in state.current_trick]
    best = next(((p, m) for p, m in reversed(plays) if not m.is_pass()), None)
    if best is None:
        out.append("【本轮】轮到你领出，可以出任意牌型。")
    else:
        seq = "；".join(f"{_seat(p)} {'过' if m.is_pass() else '出 ' + describe_move(m)}"
                       for p, m in plays)
        pts = sum(c.score_value() for _, m in plays for c in m.cards)
        bp, bm = best
        who = "你自己" if bp == me else _identity(state, bp, i_am_red, unseen_red)
        out.append(f"【本轮】{seq}")
        out.append(f"目前最大: {_seat(bp)} 的 {describe_move(bm)}（{who}）。桌面分: {pts}")

    out.append(f"外面还没出现的牌（不含你的手牌）: {_unseen_text(state, me)}")

    out.append("【可选出牌】")
    for i, o in enumerate(options, start=1):
        out.append(f"{i}. {o.text}")
    if explain:
        fmt = '{"analysis": "用两三句话说明你怎么看这手牌、为什么这样出", "move": 编号}'
    else:
        fmt = '{"move": 编号, "reason": "不超过20字"}'
    out.append(f'请从 1 到 {len(options)} 中选一个编号，只输出 JSON：{fmt}')
    return "\n".join(out)
