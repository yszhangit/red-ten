"""
Tests for hosted mode (REDTEN_HOSTED=1): the passcode gate, which routes are
served, seats that belong to players, the table cap and closing unattended
tables. No network: route functions are called directly and the WebSocket is
a fake.
"""
import asyncio
import json
import os
import threading
import time
import types

import pytest
from fastapi import HTTPException, WebSocketDisconnect

import hosting
import room_manager
import web_server
from hand import Player
from room_manager import GameRoom, RoomManager, SeatTaken

PASSCODE = "hongshi-friends"


def hosted(**kw) -> hosting.Settings:
    base = dict(hosted=True, passcode=PASSCODE, max_rooms=3, idle_room_s=600.0,
                tiers=hosting.HOSTED_TIERS)
    base.update(kw)
    return hosting.Settings(**base)


@pytest.fixture
def hosted_mode(monkeypatch):
    monkeypatch.setattr(hosting, "settings", hosted())
    monkeypatch.setattr(web_server, "rooms", RoomManager())


@pytest.fixture
def fresh_rooms(monkeypatch):
    monkeypatch.setattr(web_server, "rooms", RoomManager())


@pytest.fixture(autouse=True)
def logs_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(room_manager, "LOGS_DIR", str(tmp_path))


def cookie(passcode: str = PASSCODE) -> dict:
    return {hosting.COOKIE: hosting.auth_token(passcode)}


# ── Settings ──────────────────────────────────────────────────────────────────

def test_hosted_mode_is_off_unless_asked_for():
    s = hosting.from_env({})

    assert s.hosted is False and s.max_rooms == 0 and s.idle_room_s == 0
    assert hosting.http_gate("/training", {}, s) == "ok"
    assert hosting.is_authed({}, s) and hosting.tier_allowed("llm", s)


def test_hosted_settings_come_from_the_environment():
    s = hosting.from_env({"REDTEN_HOSTED": "1", "REDTEN_PASSCODE": PASSCODE,
                          "REDTEN_MAX_ROOMS": "2", "REDTEN_IDLE_MINUTES": "5"})

    assert s.hosted and s.passcode == PASSCODE
    assert s.max_rooms == 2 and s.idle_room_s == 300
    assert s.tiers == ("rule", "search")


def test_hosted_server_will_not_start_without_a_passcode():
    assert hosting.startup_error(hosted(passcode="")) is not None
    assert hosting.startup_error(hosted(passcode="abc")) is not None
    assert hosting.startup_error(hosted()) is None
    assert hosting.startup_error(hosting.Settings()) is None


# ── The gate ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/training", "/replay", "/dashboard", "/api/game", "/api/training",
    "/api/battle/create", "/api/tiers", "/docs", "/openapi.json",
])
def test_hosted_serves_nothing_but_the_game(path):
    assert hosting.http_gate(path, cookie(), hosted()) == "not_found"


@pytest.mark.parametrize("path", [
    "/", "/play", "/api/lobby", "/api/rooms", "/api/room/create",
    "/api/room/join/ABCD", "/api/debug-flag/ABCD",
])
def test_game_routes_need_the_passcode_cookie(path):
    s = hosted()

    assert hosting.http_gate(path, cookie(), s) == "ok"
    refused = hosting.http_gate(path, {}, s)
    assert refused == ("unauthorized" if path.startswith("/api/") else "login")
    assert hosting.http_gate(path, cookie("some-other-code"), s) == refused


def test_login_page_and_client_files_are_open():
    s = hosted()

    for path in ("/login", "/api/login", "/src/pages/login.js", "/src/styles/tokens.css"):
        assert hosting.http_gate(path, {}, s) == "ok"


def test_changing_the_passcode_invalidates_old_cookies():
    old = cookie("the-old-passcode")

    assert hosting.is_authed(old, hosted(passcode="the-old-passcode"))
    assert not hosting.is_authed(old, hosted(passcode="the-new-passcode"))


def test_gate_covers_every_page_and_api_route_the_server_has():
    """A route added later is closed in hosted mode until it is listed in hosting.py."""
    s = hosted()
    served = {"/", "/play", "/login", "/api/login", "/api/lobby", "/api/rooms",
              "/api/room/create", "/api/room/join/{code}", "/api/debug-flag/{code}"}

    for route in web_server.app.routes:
        path = getattr(route, "path", "")
        if path.startswith(("/ws/", "/src")):
            continue
        verdict = hosting.http_gate(path, cookie(), s)
        assert (verdict == "ok") == (path in served), path


# ── Login ─────────────────────────────────────────────────────────────────────

def _request(scheme: str = "http"):
    return types.SimpleNamespace(url=types.SimpleNamespace(scheme=scheme))


def test_login_with_the_passcode_sets_the_cookie(hosted_mode):
    resp = asyncio.run(web_server.login(web_server.LoginReq(passcode=PASSCODE), _request()))

    set_cookie = resp.headers["set-cookie"]
    assert f"{hosting.COOKIE}={hosting.auth_token(PASSCODE)}" in set_cookie
    assert "HttpOnly" in set_cookie and "samesite=lax" in set_cookie.lower()
    assert "secure" not in set_cookie.lower()          # plain HTTP on a bare IP


def test_login_cookie_is_secure_over_https(hosted_mode):
    resp = asyncio.run(web_server.login(
        web_server.LoginReq(passcode=PASSCODE), _request("https")))

    assert "secure" in resp.headers["set-cookie"].lower()


def test_login_with_a_wrong_passcode_is_refused_slowly(hosted_mode, monkeypatch):
    naps: list[float] = []

    async def nap(seconds):
        naps.append(seconds)

    monkeypatch.setattr(web_server.asyncio, "sleep", nap)

    with pytest.raises(HTTPException) as err:
        asyncio.run(web_server.login(web_server.LoginReq(passcode="guess"), _request()))

    assert err.value.status_code == 401
    assert naps == [1.0]


# ── Lobby and rooms ───────────────────────────────────────────────────────────

def test_lobby_config_says_what_this_server_offers(hosted_mode):
    assert web_server.lobby_config() == {
        "hosted": True, "tiers": ["rule", "search"], "debug": False, "max_name": 12}


def test_lobby_config_on_a_laptop_offers_everything(fresh_rooms):
    cfg = web_server.lobby_config()

    assert cfg["hosted"] is False and cfg["tiers"] is None and cfg["debug"] is True


def test_hosted_rooms_use_engine_ais_only(hosted_mode):
    for tier in ("ml", "llm", "multi-agent"):
        with pytest.raises(HTTPException) as err:
            web_server.create_room(web_server.CreateRoomReq(ai_tier=tier))
        assert err.value.status_code == 400

    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]
    assert web_server.rooms.get(code) is not None


def test_hosted_rooms_are_never_debug_rooms(hosted_mode):
    code = web_server.create_room(
        web_server.CreateRoomReq(ai_tier="rule", debug=True))["room_code"]

    assert web_server.rooms.get(code).debug is False


def test_table_cap(hosted_mode):
    for _ in range(3):
        web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))

    with pytest.raises(HTTPException) as err:
        web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))
    assert err.value.status_code == 409


def test_no_table_cap_on_a_laptop(fresh_rooms):
    for _ in range(5):
        web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))

    assert len(web_server.rooms.open_rooms()) == 5


def test_finished_tables_do_not_count_towards_the_cap(hosted_mode):
    codes = [web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]
             for _ in range(3)]
    web_server.rooms.get(codes[0]).status = "done"

    web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))   # does not raise
    assert web_server.rooms.get(codes[0]) is None                       # and was forgotten


# ── Seats belong to players ───────────────────────────────────────────────────

def room() -> GameRoom:
    return GameRoom("TEST", ai_tier="rule")


class FakeSocket:
    """Stands in for a connected browser."""

    def __init__(self, cookies: dict | None = None, incoming: list | None = None):
        self.cookies = cookies or {}
        self.incoming = list(incoming or [])
        self.sent: list[dict] = []
        self.accepted = False
        self.closed: int | None = None
        self.release = asyncio.Event()

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self.sent.append(json.loads(json.dumps(data)))

    async def close(self, code: int = 1000):
        self.closed = code

    async def receive_json(self):
        if self.incoming:
            return self.incoming.pop(0)
        await self.release.wait()
        raise WebSocketDisconnect()

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


def test_joining_gives_a_seat_a_token_and_a_name():
    r = room()

    seat, token = r.claim_seat(-1, name="  老  王  ")

    assert seat == 0 and token
    assert r.owns(0, token) and not r.owns(0, "guess") and not r.owns(0, None)
    assert r.seat_info(0) == {"seat": 0, "mode": "ai", "name": "老 王"}


def test_names_are_cut_to_twelve_characters():
    r = room()
    r.claim_seat(2, name="a-very-long-display-name")

    assert r.names[2] == "a-very-long-"


def test_two_tabs_get_two_seats():
    """Nothing ties a seat to a browser or an address: each join is a new player."""
    r = room()

    seat_a, token_a = r.claim_seat(-1, name="tab one")
    seat_b, token_b = r.claim_seat(-1, name="tab two")

    assert (seat_a, seat_b) == (0, 1) and token_a != token_b
    assert r.owns(0, token_a) and r.owns(1, token_b)


def test_a_claimed_seat_is_held_for_its_player():
    r = room()
    _, token = r.claim_seat(3, name="老王")

    with pytest.raises(SeatTaken):
        r.claim_seat(3, name="stranger")

    assert r.claim_seat(3, name="老王", token=token) == (3, token)   # the owner may return
    assert r.names[3] == "老王"


def test_a_dropped_players_seat_frees_after_the_hold(monkeypatch):
    r = room()
    _, token = r.claim_seat(3, name="老王")
    r._left_at[3] = time.monotonic() - hosting.settings.seat_hold_s - 1

    seat, new_token = r.claim_seat(3, name="小李")

    assert seat == 3 and new_token != token
    assert r.owns(3, new_token) and not r.owns(3, token)
    assert r.names[3] == "小李"


def test_any_seat_skips_held_ones():
    r = room()
    r.claim_seat(0, name="a")
    r.claim_seat(1, name="b")

    assert r.claim_seat(-1, name="c")[0] == 2


def test_any_seat_returns_the_seat_the_player_already_has():
    r = room()
    r.claim_seat(0, name="a")
    _, token = r.claim_seat(4, name="b")

    assert r.claim_seat(-1, name="b", token=token) == (4, token)


def test_moving_seats_frees_the_old_one():
    r = room()
    _, token = r.claim_seat(1, name="老王")

    assert r.claim_seat(5, name="老王", token=token) == (5, token)

    assert r.owns(5, token) and not r.owns(1, token)
    assert r.names[1] == "" and r.names[5] == "老王"
    assert r.claim_seat(1, name="小李")[0] == 1          # free at once, no hold


def test_a_made_up_token_gets_no_seat():
    r = room()
    r.claim_seat(0, name="老王")

    with pytest.raises(SeatTaken):
        r.claim_seat(0, name="stranger", token="made-up")


def test_a_full_table_turns_players_away():
    r = room()
    for i in range(6):
        r.claim_seat(i)

    with pytest.raises(SeatTaken):
        r.claim_seat(-1)


def test_join_route_validates_the_seat_number(fresh_rooms):
    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]

    for bad in (6, 9, -2):
        with pytest.raises(HTTPException) as err:
            web_server.join_room(code, web_server.JoinRoomReq(seat=bad))
        assert err.value.status_code == 400


def test_join_route_returns_the_token_and_names(fresh_rooms):
    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]

    out = web_server.join_room(code, web_server.JoinRoomReq(seat=2, name="老王"))

    assert out["seat"] == 2 and out["token"]
    assert out["seats"][2] == {"seat": 2, "mode": "ai", "name": "老王"}
    with pytest.raises(HTTPException) as err:
        web_server.join_room(code, web_server.JoinRoomReq(seat=2, name="x"))
    assert err.value.status_code == 409


# ── The game WebSocket ────────────────────────────────────────────────────────

def _connect(code: str, seat: int, token: str, ws: FakeSocket, during=None):
    """Run the WebSocket handler until the fake browser disconnects."""
    async def run():
        task = asyncio.create_task(web_server.ws_game(ws, code, seat, token))
        for _ in range(20):                 # let the handler reach receive_json
            await asyncio.sleep(0)
        if during:
            await during()
        ws.release.set()
        await task
    asyncio.run(run())


def test_websocket_needs_the_seat_token(fresh_rooms):
    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]
    web_server.join_room(code, web_server.JoinRoomReq(seat=0, name="老王"))

    for token in ("", "guess"):
        ws = FakeSocket()
        _connect(code, 0, token, ws)
        assert ws.closed == 1008 and ws.sent[0]["code"] == "not_your_seat"
        assert not web_server.rooms.get(code).seats[0].is_human


def test_websocket_refuses_a_seat_nobody_joined(fresh_rooms):
    """Before: any connection could sit down at /ws/ROOM/3 mid-game."""
    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]

    ws = FakeSocket()
    _connect(code, 3, "", ws)

    assert ws.closed == 1008 and "room_state" not in ws.types()


def test_websocket_with_the_token_takes_the_seat(fresh_rooms):
    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]
    token = web_server.join_room(code, web_server.JoinRoomReq(seat=2, name="老王"))["token"]
    r = web_server.rooms.get(code)
    seen: dict = {}

    async def during():
        seen["human"] = r.seats[2].is_human

    ws = FakeSocket()
    _connect(code, 2, token, ws, during)

    assert ws.accepted and seen["human"] is True
    state = ws.sent[0]
    assert state["type"] == "room_state" and state["pov_seat"] == 2
    assert state["seats"][2]["name"] == "老王" and state["seats"][2]["you"] is True
    assert not r.seats[2].is_human                       # dropped → the AI plays again
    assert r.owns(2, token)                              # but the seat is still theirs


def test_a_dropped_player_gets_their_seat_back(fresh_rooms):
    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]
    token = web_server.join_room(code, web_server.JoinRoomReq(seat=2, name="老王"))["token"]
    _connect(code, 2, token, FakeSocket())               # connect, then drop

    with pytest.raises(HTTPException):                   # nobody else can take it
        web_server.join_room(code, web_server.JoinRoomReq(seat=2, name="stranger"))

    again = FakeSocket()
    _connect(code, 2, token, again)
    assert again.accepted and again.sent[0]["type"] == "room_state"


def test_leaving_the_table_frees_the_seat_at_once(fresh_rooms):
    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]
    token = web_server.join_room(code, web_server.JoinRoomReq(seat=2, name="老王"))["token"]
    r = web_server.rooms.get(code)

    _connect(code, 2, token, FakeSocket(incoming=[{"type": "leave"}]))

    assert not r.owns(2, token) and r.names[2] == ""
    out = web_server.join_room(code, web_server.JoinRoomReq(seat=2, name="小李"))
    assert out["seat"] == 2 and out["token"] != token


def test_hosted_websocket_needs_the_passcode_cookie(hosted_mode):
    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]
    token = web_server.join_room(code, web_server.JoinRoomReq(seat=0))["token"]

    stranger = FakeSocket()
    _connect(code, 0, token, stranger)
    assert stranger.closed == 1008 and not stranger.accepted and stranger.sent == []

    friend = FakeSocket(cookies=cookie())
    _connect(code, 0, token, friend)
    assert friend.accepted and friend.sent[0]["type"] == "room_state"


def test_hosted_refuses_spectators(hosted_mode):
    """A spectator is sent every hand face up."""
    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]

    ws = FakeSocket(cookies=cookie())
    asyncio.run(web_server.ws_spectate(ws, code))

    assert ws.closed == 1008 and not ws.accepted and ws.sent == []


# ── Closing tables nobody is at ───────────────────────────────────────────────

def _unattended(r: GameRoom, seconds: float) -> None:
    r._last_attended = time.monotonic() - seconds


def test_an_unattended_hosted_table_is_closed_and_forgotten(hosted_mode):
    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]
    r = web_server.rooms.get(code)

    _unattended(r, 599)
    web_server.list_rooms()
    assert web_server.rooms.get(code) is r

    _unattended(r, 601)
    assert web_server.list_rooms() == []
    assert web_server.rooms.get(code) is None and r.status == "done"


def test_a_table_with_a_player_connected_is_never_idle(hosted_mode):
    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]
    r = web_server.rooms.get(code)
    r.seats[0]._ws = object()            # a human is attached
    _unattended(r, 10_000)

    assert r.unattended_for() == 0 and not r.should_stop()
    web_server.list_rooms()
    assert web_server.rooms.get(code) is r


def test_tables_are_not_closed_for_idleness_on_a_laptop(fresh_rooms):
    code = web_server.create_room(web_server.CreateRoomReq(ai_tier="rule"))["room_code"]
    r = web_server.rooms.get(code)
    _unattended(r, 10_000)

    assert not r.should_stop()
    web_server.list_rooms()
    assert web_server.rooms.get(code) is r


def test_closing_a_table_stops_its_game_without_a_crash_report(
        hosted_mode, monkeypatch, tmp_path):
    monkeypatch.setattr(room_manager.time, "sleep", lambda s: None)   # no AI thinking pause
    r = GameRoom("STOP", ai_tier="rule")
    thinking, go_on = threading.Event(), threading.Event()

    class Slow(Player):                  # the first decision waits for the test
        def choose_action(self, state, legal_moves):
            thinking.set()
            go_on.wait(timeout=10)
            return legal_moves[0]

    for seat in r.seats:
        seat.ai_player = Slow(seat.id)

    r.start(num_hands=1)
    assert thinking.wait(timeout=10)
    r.close()
    go_on.set()
    r._game_thread.join(timeout=10)

    assert not r._game_thread.is_alive() and r.status == "done"
    assert not os.path.exists(tmp_path / "crash_STOP.json")
    assert sum(e.kind in ("lead", "action") for e in r._renderer.events) == 1


def test_a_closed_table_cannot_be_started(hosted_mode):
    r = GameRoom("SHUT", ai_tier="rule")
    r.close()

    r.start(num_hands=1)

    assert r._game_thread is None and r.status == "done"


def test_a_running_game_stops_once_its_table_goes_idle(hosted_mode, monkeypatch, tmp_path):
    monkeypatch.setattr(room_manager.time, "sleep", lambda s: None)
    monkeypatch.setattr(hosting, "settings", hosted(idle_room_s=0.0001))
    r = GameRoom("IDLE", ai_tier="rule")
    _unattended(r, 5)

    r.start(num_hands=3)
    r._game_thread.join(timeout=10)

    assert not r._game_thread.is_alive() and r.status == "done"
    assert not os.path.exists(tmp_path / "crash_IDLE.json")


def _played_room(monkeypatch) -> GameRoom:
    monkeypatch.setattr(room_manager.time, "sleep", lambda s: None)
    r = GameRoom("PLAY", ai_tier="rule")
    r.start(num_hands=1)
    r._game_thread.join(timeout=60)
    assert not r._game_thread.is_alive()
    return r


def test_hosted_game_over_does_not_send_the_face_up_replay(hosted_mode, monkeypatch):
    """The replay is every event with all six hands, several MB per player,
    and only the replay viewer (not served when hosted) uses it."""
    assert _played_room(monkeypatch)._replay() == []


def test_game_over_on_a_laptop_still_carries_the_replay(fresh_rooms, monkeypatch):
    r = _played_room(monkeypatch)

    assert len(r._replay()) == len(r._renderer.events) > 10
