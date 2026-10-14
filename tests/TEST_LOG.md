# Unit Test Log — Red Ten Poker (红十)

Each time a bug is found and fixed, a targeted unit test is added to `tests/test_legal_moves.py`
and documented here with context, root cause, and fix summary.

---

## Test Case 1: Leading player must be able to play all valid combo types

**Date**: 2026-05-02  
**Reported by**: User  
**Source files**: `src/moves.py`, `web/play.html`

### Bug Description

When the player wins a trick (e.g., plays 大王 / big joker and everyone passes), they
become the leader for the next trick. All valid card combinations should be playable.
However, the Play button was grayed out when selecting 3 Queens (a triple), while
single cards worked fine.

### Root Cause (3 interrelated bugs)

1. **3-deck duplicate cards**: `build_deck()` uses 3 standard 54-card decks (162 cards
   total), so there are 3 identical copies of each card (same suit + same rank). `Card`
   is `@dataclass(frozen=True)`, so identical copies are equal and have the same hash.

2. **`frozenset` dedup collision**: The deduplication key was `frozenset(m.cards)`.
   With duplicates, `frozenset({Q♠₁, Q♠₂, Q♠₃})` collapsed to `frozenset({Q♠₁})`,
   which is the same key as the single Q♠ move. The triple was discarded as a "duplicate"
   of the single.

3. **Client exact-card matching**: `findMatchingMove()` compared exact card keys
   (`rl + '|' + suit`). With 4+ cards of the same rank in hand,
   `combinations(cards, 3)` generated multiple triple combos. The user's selection
   might not match any specific combo's exact card identities.

### Fix Summary

| File | Change |
|---|---|
| `src/moves.py:_add_same_rank_combos` | Only generate 1 combo per rank, not all `C(n,k)` combinations |
| `src/moves.py:_add_triple_combos` | Same — 1 combo per rank |
| `src/moves.py` dedup logic | Changed key from `frozenset(m.cards)` to `(m.type, len(m.cards), frozenset(m.cards))` |
| `web/play.html:findMatchingMove` | Changed from exact card-key matching to sorted **rank-label** matching (ignoring suit) |

### Test Coverage

- `test_leading_all_combo_types` — Leading generates singles, pairs, triples, bombs
- `test_3deck_duplicate_rank_matching` — 3-deck duplicates don't break matching
- `test_pair_rank_matching` — Pair dedup works with duplicate cards
- `test_serialization_roundtrip` — Full server→JSON→client matching simulation

---

## Test Case 2: Red ten in pairs/triples must rank as regular 10

**Date**: 2026-05-02  
**Reported by**: User  
**Source files**: `src/moves.py`, `src/cards.py`

### Bug Description

When ♥10 (red ten) is used in a regular pair with another 10 (e.g., ♥10 + ♦10),
it should rank as a regular pair of 10s (rank=8), which cannot beat a pair of 2s (rank=13).
But the game allowed the pair of 10s to beat the pair of 2s because `single_rank()` returns
the elevated red-ten rank (16) for ♥10.

### Root Cause

`Card.single_rank()` returns the elevated rank (16) for red ten via the sentinel value.
This elevation is correct for **single cards** and **special bombs** (2-red-ten bomb,
3-red-ten bomb), but NOT for regular pairs/triples. The move generation functions
(`_add_same_rank_combos`, `_add_triple_combos`, `_add_triple_pair_combos`, `_bomb_key`)
all used `single_rank()` unconditionally.

### Fix Summary

| File | Change |
|---|---|
| `src/moves.py:4 functions` | Replaced `combo[0].single_rank()` with `_SINGLE_RANK_MAP[int(combo[0].rank)]` — uses actual rank value, bypassing red ten elevation |

### Test Coverage

Implicitly covered by `test_leading_all_combo_types` (rank assertion) and regression
check in manual test run.

---

## Test Case 3: `your_turn` message double-nested card array

**Date**: 2026-05-02  
**Reported by**: User  
**Source files**: `src/room_manager.py`, `web/play.html`

### Bug Description

After server fix, the Play button remained grayed out for valid moves. User's selected
cards never matched any legal move.

### Root Cause

The `your_turn` WebSocket message serialized move cards as:
```python
"cards": ser_move(m)  # → {cards: [...], pass: ..., bomb: ..., desc: ...}
```
This double-nested the cards array inside the `ser_move` dict. The client code
`m.cards || []` returned the outer dict (truthy), not the inner array. Later,
`mCards.map(cardKey)` failed silently because dicts don't have `.map()`.

A previous fix had changed `m.cards ? m.cards.cards : []` (correct) to `m.cards || []`
(incorrect), misunderstanding the data structure.

### Fix Summary

| File | Change |
|---|---|
| `src/room_manager.py:choose_action` | Changed `"cards": ser_move(m)` to `"cards": [ser_card(c) for c in m.cards]` — directly sends the card array |

### Test Coverage

Covered by `test_serialization_roundtrip` — verifies server→client matching with
the corrected flat card array format.

---

## Test Case 4: Red ten in 4+ card normal bomb

**Date**: 2026-05-02
**Reported by**: User
**Source files**: `src/moves.py`

### Bug Description

When leading, user has 4 tens including ♥10 (red ten). Game only allows playing
3 tens (without the red ten), not 4 tens as a bomb. Red ten should count as a
regular 10 in 4+ same-rank bombs, just like in pairs and triples.

### Root Cause

Two places in `src/moves.py` explicitly excluded red tens from normal bomb formation:

1. **`_add_bomb_combos` (line 582)**: `if not c.is_joker() and not c.is_red_ten():`
   filtered out red ten when grouping cards by rank. With 3 regular tens + 1 red ten,
   only 3 cards landed in the rank bucket, and `n < 4` skipped bomb generation.

2. **`_is_valid_bomb` (line 171)**: `if any(c.is_joker() or c.is_red_ten() for c in cards):`
   rejected any bomb candidate containing a red ten.

The comment on line 144 of `_bomb_key` already anticipated this case
("red-ten plays as 10 in normal bomb context") and correctly maps
`_SINGLE_RANK_MAP[int(Rank.TEN)]` → 7 for bomb rank — the rank logic was
already correct; only the card-gathering logic was overly restrictive.

### Fix Summary

| File | Change |
|---|---|
| `src/moves.py:_is_valid_bomb` | Removed `or c.is_red_ten()` — only jokers are excluded from normal bombs |
| `src/moves.py:_add_bomb_combos` | Removed `and not c.is_red_ten()` — red tens participate in rank grouping |

### Test Coverage

- `test_red_ten_in_normal_bomb` — 4-card ten bomb (♥10 + 3 regular tens) is generated when leading with correct rank

---

## Test Case 5: Hand-end condition & scoring card transfer

**Date**: 2026-05-02
**Reported by**: User
**Source files**: `src/hand.py`

### Bug Description

1. Team scores don't add up to 300 per hand (e.g., Red 150 + Non-Red 110 = 260).
2. When one team's players all finish, the hand continues with remaining players battling until all 6 finish, rather than ending when one team is fully done.

### Root Cause

Three interrelated issues in `src/hand.py`:

1. **`_is_hand_over()` returned True at >= 5 finished**: When 5 players finished (e.g., 3 Red + 2 Non-Red), the hand ended, stranding the 6th player's unplayed cards. Any 5/10/K in their hand were never counted.

2. **`_check_terminal()` didn't return NORMAL**: It only returned GUAN_REN or SAN_HONG_SHI (when one team finishes AND the other has zero finishers). When the other team had some finishers, it returned None, allowing the hand to continue — but `_is_hand_over()` at 5 would prematurely end it.

3. **No leftover card scoring**: `_compute_result()` only summed `trick_scores` from actually-played cards. Unplayed scoring cards in mo_gong's hand were never transferred.

### Fix Summary

| File | Change |
|---|---|
| `src/hand.py:_is_hand_over` | Changed `>= 5` to `>= 6` — safety net only |
| `src/hand.py:_check_terminal` | Return `TerminalKind.NORMAL` when one team fully done (even if other team has finishers) |
| `src/hand.py:_compute_result` | Before +/-60 adjustment, scan unfinished players' hands and transfer scoring card values to opposing team |

### Test Coverage

Manual verification: play a hand and confirm Red + Non-Red team scores sum to 300.

---

## Test Case 6: 末贡标签仅在牌局结束时判定

**Date**: 2026-05-02
**Reported by**: User
**Source files**: `web/play.html`

### Bug Description

游戏进行中，`finish_order` 中最后一位已完成的玩家被错误标记为"末贡"。例如第一位玩家出完（大贡），第二位玩家出完后立即显示"末贡"标识，但此时牌局尚未结束，末贡身份尚未确定。

### Root Cause

`renderGame()` 中对 `snap.fo` 中位置的计算：`pos === snap.fo.length-1 ? ' 末贡'` — 将当前已完成玩家的最后一位视为末贡，忽略了牌局仍在进行中的事实。

### Fix Summary

| File | Change |
|---|---|
| `web/play.html:renderGame` | 移除 `pos === snap.fo.length-1 ? ' 末贡'` 分支，游戏进行中仅显示"大贡"和序号 |

末贡判定由服务端 `HandResult.mo_gong` 在牌局结束时提供，客户端 `showHandEndOverlay()` 据此显示末贡标签。

### Test Coverage

手动验证：开始一局游戏，观察第二个完成玩家不再显示"末贡"，牌局结束覆盖层正确显示末贡。

---

## Test Case 7: 末贡未出牌展示

**Date**: 2026-05-02
**Reported by**: User
**Source files**: `src/state.py`, `src/hand.py`, `src/serializers.py`, `web/play.html`

### Feature Description

牌局结束后，在显示计分结果前，增加一个展示环节：在牌桌中央将末贡玩家手中未打出的牌面朝上展示，分牌（5/10/K）有金色光晕标记，鼠标悬停时牌面上提。点击"继续"按钮后进入计分结果界面。

### Implementation Summary

| File | Change |
|---|---|
| `src/state.py` | `HandResult` 新增 `mo_gong_hands: dict` 字段 |
| `src/hand.py` | `_compute_result()` 捕获末贡玩家剩余手牌传入 `HandResult` |
| `src/serializers.py` | `ser_result()` 新增 `"mg_hands"` 键，按玩家ID分组序列化 |
| `web/play.html` | 新增 `#mogong-display` 区域、`showMoGongDisplay()` 函数、卡片hover上提效果、"继续"按钮 |

末贡玩家无剩余牌（全员出完的关人情况）时直接进入结果界面。

### Test Coverage

服务端集成测试验证 `mg_hands` 正确包含末贡玩家剩余手牌。UI展示需手动验证。

---

## Test Case 8: Pass button never appears on the human player's turn

**Date**: 2026-10-11  
**Reported by**: User  
**Source files**: `web/src/pages/game.js`, `web/src/styles/table.css`

### Bug Description

On the human player's turn the Pass button was never shown, even when passing was
legal. When pass was the only legal move the player had no way to act and the game
stopped waiting for them.

### Root Cause

Commit `90b51f1` moved the button's inline `style="display:none"` into the stylesheet
as `#pass-btn { display: none; }`. `renderMyHandStrip()` still showed the button by
clearing the inline style (`style.display = ''`), which now falls back to the
stylesheet rule and leaves it hidden.

### Fix

`renderMyHandStrip()` sets `style.display = 'inline-block'` when pass is legal.

### Test Coverage

`tests/test_web_static.py::test_pass_button_is_shown_with_an_explicit_display_value`
reads the shipped CSS and JS and fails if the button is hidden by the stylesheet while
the script only clears the inline style. It is a static check; the button itself needs
checking in a browser.

---

## Test Case 9: Any connection could take any seat

**Date**: 2026-10-13
**Files**: `src/room_manager.py`, `src/web_server.py`, `web/src/lib/session.js`, `web/src/pages/play.js`
**Reported by**: code review before hosting the game online

### Bug Description

The game WebSocket (`/ws/{room}/{seat}`) accepted any connection to any seat that had no
human on it, with no join call, including in the middle of a game. A player whose
connection dropped for a moment lost their seat, and their cards, to whoever connected to
that address next. Separately, `POST /api/room/join/{code}` with a seat number outside 0–5
crashed with a 500, and a negative number silently picked a seat counted from the end.

### Root Cause

A seat was only ever "human connected" or "AI"; nothing recorded who had joined it. The
join route indexed `room.seats[seat]` without checking the number.

### Fix

`GameRoom.claim_seat()` gives the player who joins a seat a random token and remembers
it; the WebSocket must present that token. A dropped player's seat is played by the AI
and held for them for `seat_hold_s` (60 s); after that it is free. The client keeps the
token in `sessionStorage`, so each tab is its own player and a reload keeps the seat.
The join route rejects seat numbers outside −1…5 with a 400.

### Test Coverage

`tests/test_hosting.py`: `test_websocket_refuses_a_seat_nobody_joined`,
`test_websocket_needs_the_seat_token`, `test_a_dropped_player_gets_their_seat_back`,
`test_a_claimed_seat_is_held_for_its_player`, `test_two_tabs_get_two_seats`,
`test_join_route_validates_the_seat_number`. Also checked in a real browser (two tabs at
one table, moving seats, reload); that check is not automated.

---

## Running Tests

```bash
# Run all unit tests
# Windows: set UTF-8 encoding to handle suit symbols in output
PYTHONIOENCODING=utf-8 python tests/test_legal_moves.py

# Or equivalently:
python tests/test_legal_moves.py

# Expected output: ALL TESTS PASSED
```
