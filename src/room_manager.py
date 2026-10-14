"""
Multiplayer room management.

SeatController  — one per seat; routes choose_action() to AI or a connected
                  human WebSocket.  The game loop runs in a background thread
                  and calls choose_action() synchronously; human moves cross
                  the thread boundary via a thread-safe queue.

LiveRenderer    — subclasses GUIRenderer to broadcast every game event to all
                  connected clients as it happens, in addition to recording
                  the event list for post-game replay.

GameRoom        — holds 6 seats, the game thread, and the broadcast machinery.

RoomManager     — creates and looks up rooms by code.
"""
from __future__ import annotations
import asyncio
import datetime
import json
import os
import queue
import random
import secrets
import string
import sys as _sys
import threading
import time
import traceback as _tb
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

import hosting
from hand import Player
from state import GameState
from moves import Move
from gui_renderer import GUIRenderer, GameEvent
from serializers import ser_event, ser_event_pov, ser_move, ser_card

if TYPE_CHECKING:
    from fastapi import WebSocket

LOGS_DIR = os.path.join(os.path.dirname(__file__), "..", "debug_logs")

MAX_NAME_LEN = 12


class SeatTaken(Exception):
    """The seat belongs to a connected player, or is being held for one who dropped."""


class RoomLimit(Exception):
    """No more tables may be opened right now."""


class RoomClosed(Exception):
    """Raised inside the game thread to end a table that has been closed."""


def clean_name(name: str) -> str:
    """A display name as the table shows it: one line, at most MAX_NAME_LEN characters."""
    return " ".join(str(name).split())[:MAX_NAME_LEN]

# ── AI self-play / battle configuration ─────────────────────────────────────

SPEED_DELAYS = {
    "normal":  (1.0, 3.0),
    "fast":    (0.3, 0.5),
    "instant": (0.0, 0.0),
}


@dataclass
class SeatConfig:
    """Per-seat AI configuration for battle mode."""
    tier: str = "multi-agent"
    personality: Optional[str] = None  # only used when tier == "multi-agent"
    model: Optional[str] = None        # only used when tier == "llm"; None = default model


@dataclass
class BattleConfig:
    """Configuration for an AI-vs-AI spectator battle."""
    seats: list[SeatConfig] = field(default_factory=lambda: [SeatConfig() for _ in range(6)])
    speed: str = "normal"
    num_hands: int = 6


class SeatController(Player):
    """
    Wraps an AI player.  When a human WebSocket is attached the seat switches
    to human mode: choose_action() sends 'your_turn' to the client and blocks
    until the client replies with a move index or disconnects.
    """

    def __init__(self, seat_id: int, ai_player: Player, speed: str = "normal"):
        super().__init__(seat_id)
        self.ai_player = ai_player
        self._speed = speed
        self._min_delay, self._max_delay = SPEED_DELAYS.get(speed, (1.0, 3.0))
        self._ws: Optional["WebSocket"] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._move_queue: queue.Queue[Optional[int]] = queue.Queue()
        self._lock = threading.Lock()
        self._room: Optional["GameRoom"] = None

    # ── connection management ─────────────────────────────────────────────────

    @property
    def is_human(self) -> bool:
        with self._lock:
            return self._ws is not None

    def attach(self, ws: "WebSocket", loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._ws = ws
            self._loop = loop
        if self._room:
            self._room.seat_attended(self.id)

    def detach(self) -> None:
        with self._lock:
            self._ws = None
            self._loop = None
        if self._room:
            self._room.seat_attended(self.id)
        # Wake up any blocked choose_action() so it can fall back to AI.
        self._move_queue.put_nowait(None)

    def send(self, data: dict) -> None:
        """Schedule a send on the event loop from any thread."""
        with self._lock:
            ws, loop = self._ws, self._loop
        if ws and loop:
            asyncio.run_coroutine_threadsafe(ws.send_json(data), loop)

    def submit_move(self, move_idx: int) -> None:
        """Called by the WebSocket handler when the human picks a move."""
        self._move_queue.put_nowait(move_idx)

    # ── Player interface ──────────────────────────────────────────────────────

    def choose_action(self, state: GameState, legal_moves: list[Move]) -> Move:
        if self._room and self._room.should_stop():
            raise RoomClosed()
        if not self.is_human:
            # The pause imitates thinking; a model-backed seat is slow enough already.
            own_latency = getattr(self.ai_player, "has_own_latency", False)
            if not own_latency and (self._min_delay > 0 or self._max_delay > 0):
                time.sleep(random.uniform(self._min_delay, self._max_delay))
            return self.ai_player.choose_action(state, legal_moves)

        # Log the legal-moves set for debugging before asking the human.
        if self._room and self._room._renderer:
            self._room._renderer.log_debug_turn(
                self.id, [{**ser_move(m), "idx": i} for i, m in enumerate(legal_moves)]  # type: ignore[dict-item]
            )

        # Send legal moves to the human client.
        self.send({
            "type": "your_turn",
            "legal_moves": [
                {"idx": i, "desc": str(m) if not m.is_pass() else "Pass",
                 "is_pass": m.is_pass(), "cards": [ser_card(c) for c in m.cards]}
                for i, m in enumerate(legal_moves)
            ],
        })

        # Block until the human replies, a timeout fires, or they disconnect.
        while True:
            try:
                val = self._move_queue.get(timeout=1.0)
            except queue.Empty:
                if self._room and self._room.should_stop():
                    raise RoomClosed()
                if not self.is_human:
                    return self.ai_player.choose_action(state, legal_moves)
                continue

            if val is None:
                # Disconnected sentinel — fall back to AI.
                return self.ai_player.choose_action(state, legal_moves)
            if 0 <= val < len(legal_moves):
                return legal_moves[val]
            # Invalid index — ask again.
            self.send({"type": "error", "msg": "Invalid move — try again."})


class LiveRenderer(GUIRenderer):
    """
    Extends GUIRenderer to broadcast every event to all connected clients
    as it happens, while still building the full event list for replay.
    Also appends every event to a JSONL log file for post-hoc debugging.
    """

    def __init__(self, room: "GameRoom"):
        super().__init__()
        self._room = room
        self._log_path = room._log_path

    def _emit(self, kind: str, desc: str, **kwargs) -> None:
        super()._emit(kind, desc, **kwargs)
        self._room.broadcast_event(self.events[-1])
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ser_event(self.events[-1]), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def log_debug_turn(self, player: int, serialized_moves: list) -> None:
        """Append a your_turn record to the JSONL log without broadcasting it."""
        record = {
            "kind":        "your_turn",
            "desc":        f"P{player}'s turn — {len(serialized_moves)} legal moves",
            "player":      player,
            "timestamp":   datetime.datetime.now().isoformat(timespec="seconds"),
            "legal_moves": serialized_moves,
        }
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass



class GameRoom:
    """
    Manages a single game: 6 SeatControllers, a background game thread,
    and broadcast helpers.
    """

    def __init__(self, room_code: str, ai_tier: str = "search",
                 n_samples: int = 12, config: Optional[BattleConfig] = None,
                 debug: bool = False):
        self.room_code = room_code
        self.config = config
        # Debug mode: human players see AI hands and each LLM seat's reasoning.
        self.debug = debug
        self.thoughts: dict[int, dict] = {}   # latest decision report per seat
        self.ai_tier = ai_tier
        self.status = "lobby"   # lobby | running | done
        self.seats: list[SeatController] = []
        self._renderer: Optional[LiveRenderer] = None
        self._game_thread: Optional[threading.Thread] = None
        self._continue_event = threading.Event()

        # Who sits where. A player who joins gets a token; the seat is theirs
        # while they are connected and is held for them for a while if they drop.
        self.names: list[str] = [""] * 6
        self._owners: list[Optional[str]] = [None] * 6
        self._left_at: list[float] = [0.0] * 6
        self._seat_lock = threading.Lock()
        self._closed = False
        self._last_attended = time.monotonic()

        # Spectator support
        self._spectators: list[tuple] = []  # (WebSocket, event_loop)
        self._spectator_lock = threading.Lock()

        os.makedirs(LOGS_DIR, exist_ok=True)
        self._log_path   = os.path.join(LOGS_DIR, f"{room_code}.jsonl")
        self._flags_path = os.path.join(LOGS_DIR, f"{room_code}_flags.json")

        if config is not None:
            from session import make_players_from_config
            speed = config.speed
            for i, ai in enumerate(make_players_from_config(config)):
                seat = SeatController(i, ai, speed=speed)
                seat._room = self
                self.seats.append(seat)
        else:
            from session import make_players
            for i, ai in enumerate(make_players(ai_tier, n_samples=n_samples)):
                seat = SeatController(i, ai)
                seat._room = self
                self.seats.append(seat)

        if debug:
            for seat in self.seats:
                ai = seat.ai_player
                if hasattr(ai, "on_decision"):       # LLM seats
                    setattr(ai, "on_decision", self.broadcast_thought)
                    setattr(ai, "explain", True)

    # ── broadcasting ──────────────────────────────────────────────────────────

    def broadcast(self, data: dict, only_seat: Optional[int] = None) -> None:
        """Send the same data to all (or one) connected human seat(s)."""
        for i, seat in enumerate(self.seats):
            if only_seat is not None and i != only_seat:
                continue
            seat.send(data)

    def broadcast_event(self, event: "GameEvent") -> None:
        """Send personalized game event — each seat sees only their own hand.
        When all human players have finished, AI hands are revealed so humans
        can observe AI decision-making quality."""
        human_seats = [i for i, s in enumerate(self.seats) if s.is_human]
        if not human_seats:
            pass   # don't return — spectators may still need the event
        else:
            all_humans_done = all(event.snapshot.finished[p] for p in human_seats)
            ai_seats = self.reveal_seats(all_humans_done)
            for i in human_seats:
                self.seats[i].send({"type": "game_event", "event": ser_event_pov(event, i, ai_seats)})

        # Broadcast full (non-POV) event to spectators
        self._spectator_send({"type": "game_event", "event": ser_event(event)})

    def reveal_seats(self, all_humans_done: bool) -> Optional[set[int]]:
        """AI seats whose hands human players may see: all of them in debug
        mode, otherwise only once every human has finished the hand."""
        if self.debug or all_humans_done:
            return {i for i in range(6) if not self.seats[i].is_human}
        return None

    def broadcast_thought(self, report: dict) -> None:
        """Debug mode only: tell human players what an AI seat just decided and why."""
        if not self.debug:
            return
        self.thoughts[report["seat"]] = report
        self.broadcast({"type": "ai_thought", **report})

    # ── seats and their players ───────────────────────────────────────────────

    def seat_info(self, seat: int) -> dict:
        return {"seat": seat, "mode": "human" if self.seats[seat].is_human else "ai",
                "name": self.names[seat]}

    def seats_info(self) -> list[dict]:
        return [self.seat_info(i) for i in range(6)]

    def announce_seat(self, seat: int) -> None:
        """Tell everyone at the table who now sits in a seat."""
        self.broadcast({"type": "seat_update", **self.seat_info(seat)})

    def claim_seat(self, seat: int, name: str = "",
                   token: Optional[str] = None) -> tuple[int, str]:
        """Reserve a seat for a player; seat -1 means any free one. Returns the
        seat and the token the player's WebSocket must present.

        A player who passes the token they already hold keeps their seat (a
        reconnect or a page reload) or moves to another one. Without it, a seat
        is free when no human is connected to it and nobody has claimed it or
        dropped from it in the last `seat_hold_s` seconds. Raises SeatTaken."""
        now = time.monotonic()
        hold = hosting.settings.seat_hold_s
        with self._seat_lock:
            if token is None or token not in self._owners:
                token = None

            def free(i: int) -> bool:
                if self._owners[i] is not None and self._owners[i] == token:
                    return True
                if self.seats[i].is_human:
                    return False
                return self._owners[i] is None or now - self._left_at[i] >= hold

            if seat == -1:
                mine = [i for i in range(6) if token and self._owners[i] == token]
                open_seats = mine or [i for i in range(6) if free(i)]
                if not open_seats:
                    raise SeatTaken("All seats taken")
                seat = open_seats[0]
            elif not free(seat):
                raise SeatTaken(f"Seat {seat} already taken")

            token = token or secrets.token_urlsafe(16)
            changed = [seat]
            for i in range(6):          # one seat per player: moving frees the old one
                if i != seat and self._owners[i] == token:
                    self._owners[i] = None
                    self.names[i] = ""
                    changed.append(i)
            self._owners[seat] = token
            self.names[seat] = clean_name(name)
            self._left_at[seat] = now
        for i in changed:
            self.announce_seat(i)
        return seat, token

    def release_seat(self, seat: int) -> None:
        """The seat's player left the table for good: free it at once."""
        with self._seat_lock:
            self._owners[seat] = None
            self.names[seat] = ""
        self.announce_seat(seat)

    def owns(self, seat: int, token: Optional[str]) -> bool:
        """True if `token` is the one issued for this seat."""
        owner = self._owners[seat]
        return owner is not None and token is not None and secrets.compare_digest(owner, token)

    def seat_attended(self, seat: int) -> None:
        """A human connected to or dropped from a seat."""
        now = time.monotonic()
        self._left_at[seat] = now
        self._last_attended = now

    # ── closing a table nobody is at ──────────────────────────────────────────

    def unattended_for(self) -> float:
        """Seconds since a player or spectator was last connected (0 while one is)."""
        if self._has_human_players() or self._has_spectators():
            return 0.0
        return time.monotonic() - self._last_attended

    def should_stop(self) -> bool:
        """True once the table is closed, or has been unattended for longer
        than the hosted idle limit (which closes it)."""
        limit = hosting.settings.idle_room_s
        if not self._closed and limit and self.unattended_for() > limit:
            self.close()
        return self._closed

    def close(self) -> None:
        """End the game; the game thread stops at its next decision."""
        self._closed = True
        self.status = "done"
        self._continue_event.set()

    def room_state_msg(self, pov_seat: int) -> dict:
        seats_info = []
        for i, s in enumerate(self.seats):
            entry: dict = {**self.seat_info(i), "you": i == pov_seat}
            if self.config is not None:
                entry["tier"] = self.config.seats[i].tier
                entry["personality"] = self.config.seats[i].personality
                entry["model"] = self.config.seats[i].model
            seats_info.append(entry)

        result: dict = {
            "type": "room_state",
            "room_code": self.room_code,
            "pov_seat": pov_seat,
            "seats": seats_info,
            "status": self.status,
            "debug": self.debug,
        }
        if self.config is not None:
            result["is_battle"] = True
            result["speed"] = self.config.speed
            result["num_hands"] = self.config.num_hands
        return result

    # ── spectator support ────────────────────────────────────────────────────

    def attach_spectator(self, ws, loop) -> None:
        """Register a spectator WebSocket connection."""
        with self._spectator_lock:
            self._spectators.append((ws, loop))
        self._last_attended = time.monotonic()

    def detach_spectator(self, ws) -> None:
        """Remove a spectator connection."""
        with self._spectator_lock:
            self._spectators = [(w, l) for w, l in self._spectators if w is not ws]
        self._last_attended = time.monotonic()

    def _spectator_send(self, data: dict) -> None:
        """Send data to all connected spectators (thread-safe)."""
        with self._spectator_lock:
            spectators = list(self._spectators)
        for ws, loop in spectators:
            try:
                asyncio.run_coroutine_threadsafe(ws.send_json(data), loop)
            except Exception:
                pass

    def _has_spectators(self) -> bool:
        with self._spectator_lock:
            return len(self._spectators) > 0

    def _has_human_players(self) -> bool:
        """True if any seat has a human WebSocket attached."""
        return any(s.is_human for s in self.seats)

    # ── game lifecycle ────────────────────────────────────────────────────────

    def add_flag(self, note: str | None = None) -> dict:
        """Write a flag record into the JSONL log and the flags index."""
        import secrets
        flag_id = secrets.token_hex(4)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        record = {"kind": "flag", "id": flag_id, "timestamp": ts, "note": note}
        if self._renderer:
            try:
                with open(self._renderer._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                pass
        try:
            flags = json.loads(open(self._flags_path, encoding="utf-8").read()) \
                if os.path.exists(self._flags_path) else []
        except (OSError, json.JSONDecodeError):
            flags = []
        flags.append(record)
        with open(self._flags_path, "w", encoding="utf-8") as f:
            json.dump(flags, f, ensure_ascii=False, indent=2)
        return {
            "log_file":   os.path.basename(self._renderer._log_path if self._renderer else self._log_path),
            "flags_file": os.path.basename(self._flags_path),
            "flag_id":    flag_id,
            "flag_count": len(flags),
        }

    def signal_continue(self) -> None:
        """Signal the game thread to continue to the next hand."""
        self._continue_event.set()

    def _wait_for_continue(self, timeout: float = 120.0) -> None:
        """Block until a human signals continue, or timeout fires.
        In AI-only (battle/spectator) mode, uses a short auto-continue delay."""
        if not self._has_human_players():
            speed = self.config.speed if self.config else "normal"
            delay = {"normal": 3.0, "fast": 1.5, "instant": 1.0}.get(speed, 3.0)
            self._continue_event.wait(timeout=delay)
        else:
            self._continue_event.wait(timeout=timeout)
        self._continue_event.clear()

    def start(self, num_hands: int = 3) -> None:
        if self.status != "lobby":
            return
        self.status = "running"
        self._renderer = LiveRenderer(self)
        self._game_thread = threading.Thread(
            target=self._run, args=(num_hands,), daemon=True
        )
        self._game_thread.start()

    def _run(self, num_hands: int) -> None:
        from session import GameSession
        try:
            GameSession(self.seats, self._renderer,  # type: ignore[arg-type]
                        continue_cb=self._wait_for_continue).run(num_hands=num_hands)
        except RoomClosed:
            pass
        except Exception:
            exc = _sys.exc_info()
            try:
                exc_type = exc[0]
                crash = {
                    "room_code":   self.room_code,
                    "log_file":    os.path.basename(self._log_path),
                    "timestamp":   datetime.datetime.now().isoformat(timespec="seconds"),
                    "event_count": len(self._renderer.events) if self._renderer else 0,
                    "exc_type":    exc_type.__name__ if exc_type else "Unknown",
                    "exc_message": str(exc[1]),
                    "traceback":   "".join(_tb.format_exception(*exc)),
                }
                crash_path = os.path.join(LOGS_DIR, f"crash_{self.room_code}.json")
                with open(crash_path, "w", encoding="utf-8") as f:
                    json.dump(crash, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
            raise
        finally:
            self.status = "done"
            replay = self._replay()
            self.broadcast({"type": "game_over", "replay": replay})
            self._spectator_send({"type": "game_over", "replay": replay})


    def _replay(self) -> list[dict]:
        """Every event of the game with all hands face up, for the replay
        viewer. That is several MB per player, and a hosted server has no
        replay viewer, so there it is left out."""
        assert self._renderer is not None
        if hosting.settings.hosted:
            return []
        return [ser_event(e) for e in self._renderer.events]


class RoomManager:
    def __init__(self):
        self._rooms: dict[str, GameRoom] = {}

    def sweep(self) -> None:
        """Forget tables that are finished or have sat unattended past the
        hosted idle limit, once nobody is connected to them."""
        for code, room in list(self._rooms.items()):
            if room.should_stop() or room.status == "done":
                if room.unattended_for() > 0:
                    self._rooms.pop(code, None)

    def open_rooms(self) -> list[GameRoom]:
        return [r for r in self._rooms.values() if r.status != "done"]

    def create(self, ai_tier: str = "search", debug: bool = False) -> GameRoom:
        self.sweep()
        limit = hosting.settings.max_rooms
        if limit and len(self.open_rooms()) >= limit:
            raise RoomLimit(f"At most {limit} tables can be open at once")
        code = self._fresh_code()
        room = GameRoom(code, ai_tier=ai_tier, debug=debug)
        self._rooms[code] = room
        return room

    def create_battle(self, config: BattleConfig) -> GameRoom:
        code = self._fresh_code()
        room = GameRoom(code, config=config)
        self._rooms[code] = room
        return room

    def get(self, code: str) -> Optional[GameRoom]:
        return self._rooms.get(code.upper())

    def _fresh_code(self) -> str:
        while True:
            code = "".join(random.choices(string.ascii_uppercase, k=4))
            if code not in self._rooms:
                return code
