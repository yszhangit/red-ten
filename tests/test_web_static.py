"""
Static checks on the web client. There is no browser test setup, so these pin
regressions by reading the shipped JS and CSS.
"""
import os
import re

WEB = os.path.join(os.path.dirname(__file__), "..", "web")


def _read(*parts: str) -> str:
    with open(os.path.join(WEB, *parts), encoding="utf-8") as f:
        return f.read()


def test_pass_button_is_shown_with_an_explicit_display_value():
    """The stylesheet hides #pass-btn by default, so clearing the inline style
    (display = '') leaves it hidden. Showing it needs a real display value."""
    css = _read("src", "styles", "table.css")
    js = _read("src", "pages", "game.js")

    hidden_by_default = re.search(r"#pass-btn\s*\{[^}]*display:\s*none", css) is not None
    shown = re.search(
        r"getElementById\('pass-btn'\)\.style\.display\s*=\s*hasPass\s*\?\s*'([^']*)'", js)

    assert shown, "game.js no longer toggles #pass-btn the way this test expects"
    if hidden_by_default:
        assert shown.group(1) not in ("", "none"), (
            "pass button can never appear: table.css hides it and game.js only "
            "clears the inline style")


def test_debug_mode_is_wired_through_the_play_page():
    """Lobby checkbox → create request → room flag → thought messages → game view."""
    html = _read("play.html")
    lobby = _read("src", "pages", "lobby.js")
    play = _read("src", "pages", "play.js")
    game = _read("src", "pages", "game.js")

    assert 'id="ai-debug"' in html
    assert re.search(r"debug:\s*document\.getElementById\('ai-debug'\)\.checked", lobby)
    assert "case 'ai_thought':" in play and "game.handleThought(" in play
    assert "debug: roomDebug" in play
    assert "export function handleThought(" in game


def test_client_scripts_parse():
    """A duplicate declaration or a typo in one module breaks the whole page,
    and nothing else here would notice. Needs node; skipped without it."""
    import glob
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    for path in sorted(glob.glob(os.path.join(WEB, "src", "*", "*.js"))):
        out = subprocess.run([node, "--check", path], capture_output=True, text=True)
        assert out.returncode == 0, f"{os.path.basename(path)}: {out.stderr}"


def test_seat_token_and_name_are_wired_through_the_play_page():
    """join sends the name and this tab's token → the token is stored per tab →
    the game WebSocket presents it → a reload rejoins with it."""
    html = _read("play.html")
    session = _read("src", "lib", "session.js")
    lobby = _read("src", "pages", "lobby.js")
    play = _read("src", "pages", "play.js")

    assert 'id="player-name"' in html
    assert re.search(r"body:\s*JSON\.stringify\(\{\s*seat,\s*name:\s*playerName\(\),\s*"
                     r"token:\s*tokenFor\(code\)\s*\}\)", session)
    assert "sessionStorage.setItem(SEAT_KEY" in session     # per tab, not per browser
    assert "joinRoom(code, preferSeat)" in lobby
    assert "?token=${encodeURIComponent(myToken)}" in play
    assert "joinRoom(saved.room, saved.seat).then(enterRoom)" in play
    # a reload keeps the seat, so leaving needs its own way out
    assert 'id="leave-btn"' in html and "sendWS({ type: 'leave' })" in play


def test_player_names_are_never_written_as_raw_html():
    room = _read("src", "pages", "room.js")
    game = _read("src", "pages", "game.js")

    assert "innerHTML" not in room.split("function renderSeatGrid")[1].replace(
        "grid.innerHTML = '';", "")
    # every place game.js builds HTML from a seat name escapes it
    for line in game.splitlines():
        if "seatName(p)" in line and ("html" in line or "<span" in line or "const name" in line):
            assert "esc(seatName(p))" in line, line


def test_login_page_posts_the_passcode():
    html = _read("login.html")
    js = _read("src", "pages", "login.js")

    assert 'id="passcode"' in html and "/src/pages/login.js" in html
    assert "fetch('/api/login'" in js and "passcode: input.value" in js
