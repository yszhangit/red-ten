"""
Settings for running the server on the internet for a private group of
players (see docs/hosting.md).

Everything here is off unless REDTEN_HOSTED=1, so running on a laptop is
unchanged. When it is on:

  * only the 6-seat game is served: the lobby at "/", the room API and the
    game WebSocket. Training, replay, battles and spectating answer 404.
  * every page, API call and WebSocket needs the shared passcode, which the
    login page exchanges for a cookie.
  * empty seats are filled by the engine AIs only, the number of open tables
    is capped, and a table nobody is connected to is closed.

Environment:
    REDTEN_HOSTED=1          switch hosted mode on
    REDTEN_PASSCODE=...      the shared passcode (required when hosted)
    REDTEN_MAX_ROOMS=3       open tables allowed at once
    REDTEN_IDLE_MINUTES=10   close a table after this long with nobody connected
"""
from __future__ import annotations
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Mapping

COOKIE = "redten_auth"
COOKIE_MAX_AGE_S = 180 * 24 * 3600

# AI tiers that need neither torch nor a model server.
HOSTED_TIERS = ("rule", "search")

# Reachable without the passcode. /src/ is the client's own JS and CSS.
_OPEN_PATHS = ("/login", "/api/login")
_OPEN_PREFIXES = ("/src/",)
# The 6-seat game. Anything else is not served in hosted mode.
_GAME_PATHS = ("/", "/play", "/api/lobby", "/api/rooms", "/api/room/create")
_GAME_PREFIXES = ("/api/room/join/", "/api/debug-flag/")


@dataclass
class Settings:
    hosted: bool = False
    passcode: str = ""
    max_rooms: int = 0          # 0 = no limit
    idle_room_s: float = 0.0    # 0 = never close an unattended table
    seat_hold_s: float = 60.0   # how long a dropped player's seat is kept for them
    tiers: tuple[str, ...] = ()  # AI tiers a room may use; empty = any


def from_env(env: Mapping[str, str] = os.environ) -> Settings:
    if env.get("REDTEN_HOSTED", "") not in ("1", "true", "yes"):
        return Settings()
    return Settings(
        hosted=True,
        passcode=env.get("REDTEN_PASSCODE", ""),
        max_rooms=int(env.get("REDTEN_MAX_ROOMS", "3")),
        idle_room_s=float(env.get("REDTEN_IDLE_MINUTES", "10")) * 60,
        tiers=HOSTED_TIERS,
    )


settings = from_env()


def startup_error(s: Settings | None = None) -> str | None:
    """Why the server must not start with these settings, or None."""
    s = s or settings
    if s.hosted and len(s.passcode) < 6:
        return ("REDTEN_HOSTED=1 needs REDTEN_PASSCODE set to at least 6 characters; "
                "refusing to serve the game to the internet without one.")
    return None


def auth_token(passcode: str) -> str:
    """The cookie value that proves the passcode was entered. It is derived
    from the passcode, so it survives a restart and stops working when the
    passcode is changed."""
    return hmac.new(passcode.encode("utf-8"), b"redten-auth-v1", hashlib.sha256).hexdigest()


def passcode_ok(given: str, s: Settings | None = None) -> bool:
    s = s or settings
    if not s.hosted:
        return True
    return bool(s.passcode) and hmac.compare_digest(
        given.encode("utf-8"), s.passcode.encode("utf-8"))


def is_authed(cookies: Mapping[str, str], s: Settings | None = None) -> bool:
    s = s or settings
    if not s.hosted:
        return True
    if not s.passcode:
        return False
    return hmac.compare_digest(cookies.get(COOKIE, ""), auth_token(s.passcode))


def http_gate(path: str, cookies: Mapping[str, str], s: Settings | None = None) -> str:
    """What to do with an HTTP request: "ok", "not_found" (not part of the
    hosted game), "login" (send the browser to the login page) or
    "unauthorized" (an API call without the cookie)."""
    s = s or settings
    if not s.hosted:
        return "ok"
    if path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES):
        return "ok"
    if path not in _GAME_PATHS and not path.startswith(_GAME_PREFIXES):
        return "not_found"
    if is_authed(cookies, s):
        return "ok"
    return "unauthorized" if path.startswith("/api/") else "login"


def tier_allowed(tier: str, s: Settings | None = None) -> bool:
    s = s or settings
    return not s.tiers or tier in s.tiers
