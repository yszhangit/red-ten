"""
Red Ten Poker — web server (FastAPI).

Usage:
    python src/web_server.py              # http://localhost:5173
    python src/web_server.py --port 8080

Routes:
    GET  /                      → replay viewer (index.html)
    GET  /dashboard             → training dashboard
    GET  /play                  → multiplayer lobby
    GET  /api/game              → run AI-only game, return event list (replay)
    GET  /api/training          → parse training log, return structured JSON
    POST /api/room/create       → create multiplayer room
    POST /api/room/join/{code}  → join room, claim a seat
    POST /api/debug-flag/{code} → append a debug flag marker to debug_logs/
    WS   /ws/{room}/{seat}      → live game WebSocket
"""
from __future__ import annotations
import sys, os, random, re, glob, asyncio, argparse
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "agents"))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel
from serializers import ser_event, ser_event_pov
from room_manager import RoomManager, SeatConfig, BattleConfig

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

WEB_DIR  = os.path.join(os.path.dirname(__file__), "..", "web")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

rooms = RoomManager()

app.mount("/src", StaticFiles(directory=os.path.join(WEB_DIR, "src")), name="src")


# ── Static pages ──────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


@app.get("/dashboard")
def dashboard():
    return FileResponse(os.path.join(WEB_DIR, "dashboard.html"))


@app.get("/play")
def play():
    return FileResponse(os.path.join(WEB_DIR, "play.html"))


@app.get("/training")
def training():
    return FileResponse(os.path.join(WEB_DIR, "training.html"))


@app.get("/replay")
def replay():
    return FileResponse(os.path.join(WEB_DIR, "replay.html"))


# ── AI replay game ─────────────────────────────────────────────────────────────

@app.get("/api/game")
def new_game(tier: str = "rule", hands: int = 3, seed: str = ""):
    hands = max(1, min(10, hands))
    if seed.isdigit():
        random.seed(int(seed))

    from session import GameSession, make_players
    from gui_renderer import GUIRenderer

    renderer = GUIRenderer()
    try:
        players = make_players(tier)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown tier '{tier}'")

    GameSession(players, renderer).run(num_hands=hands)
    return [ser_event(e) for e in renderer.events]


# ── Training dashboard ─────────────────────────────────────────────────────────

@app.get("/api/training")
def training_status(log: str = "/tmp/selfplay_adv_search.log"):
    import time as _time
    parsed = _parse_training_log(log)
    models = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "model*.pt"))):
        stat = os.stat(path)
        models.append({
            "name":     os.path.basename(path),
            "size_kb":  round(stat.st_size / 1024, 1),
            "modified": _time.strftime("%H:%M:%S", _time.localtime(stat.st_mtime)),
        })
    parsed["models"] = models
    return parsed


# ── Multiplayer room API ───────────────────────────────────────────────────────

class CreateRoomReq(BaseModel):
    ai_tier: str = "search"
    debug: bool = False      # show AI hands and LLM reasoning to the human players

class JoinRoomReq(BaseModel):
    seat: int = -1

@app.get("/api/rooms")
def list_rooms():
    result = []
    for room in rooms._rooms.values():
        if room.status == "done":
            continue
        entry: dict = {
            "code":        room.room_code,
            "status":      room.status,
            "ai_tier":     room.ai_tier,
            "human_seats": sum(1 for s in room.seats if s.is_human),
            "debug":       room.debug,
        }
        if room.config is not None:
            entry["is_battle"] = True
            entry["speed"] = room.config.speed
            entry["spectators"] = room._has_spectators() and 1 or 0
        result.append(entry)
    return result


@app.post("/api/room/create")
def create_room(req: CreateRoomReq):
    room = rooms.create(ai_tier=req.ai_tier, debug=req.debug)
    return {"room_code": room.room_code}


@app.post("/api/room/join/{code}")
def join_room(code: str, req: JoinRoomReq):
    room = rooms.get(code)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.status == "done":
        raise HTTPException(status_code=410, detail="Game already finished")

    seat = req.seat
    if seat == -1:
        free = [i for i, s in enumerate(room.seats) if not s.is_human]
        if not free:
            raise HTTPException(status_code=409, detail="All seats taken")
        seat = free[0]
    elif room.seats[seat].is_human:
        raise HTTPException(status_code=409, detail=f"Seat {seat} already taken")

    return {"room_code": room.room_code, "seat": seat,
            "status": room.status,
            "seats": [{"seat": i, "mode": "human" if s.is_human else "ai"}
                      for i, s in enumerate(room.seats)]}


# ── AI battle / spectator ────────────────────────────────────────────────────

class BattleConfigReq(BaseModel):
    seats: list[dict]         # [{"tier":"multi-agent","personality":"aggressive"}, ...]
    speed: str = "normal"
    num_hands: int = 6


@app.post("/api/battle/create")
def create_battle(req: BattleConfigReq):
    config = BattleConfig(
        seats=[SeatConfig(**s) for s in req.seats],
        speed=req.speed,
        num_hands=req.num_hands,
    )
    room = rooms.create_battle(config)
    return {"room_code": room.room_code}


@app.get("/api/tiers")
def get_tiers():
    from session import AVAILABLE_TIERS, AVAILABLE_PERSONALITIES
    from llm_client import load_models
    try:
        models, default = load_models()
        llm_models = [{"value": name, "label": f"{name} ({m.model})"}
                      for name, m in models.items()]
    except (OSError, ValueError):
        llm_models, default = [], None
    return {"tiers": AVAILABLE_TIERS, "personalities": AVAILABLE_PERSONALITIES,
            "llm_models": llm_models, "llm_default": default}


@app.websocket("/ws/{room_code}/spectate")
async def ws_spectate(websocket: WebSocket, room_code: str):
    await websocket.accept()

    room = rooms.get(room_code)
    if not room:
        await websocket.send_json({"type": "error", "msg": "Room not found"})
        await websocket.close(code=1008)
        return

    loop = asyncio.get_running_loop()
    room.attach_spectator(websocket, loop)

    # Send room state with spectator flag
    state = room.room_state_msg(-1)
    state["is_spectator"] = True
    await websocket.send_json(state)

    # Catch-up if game already running
    if room.status == "running" and room._renderer:
        events = [ser_event(e) for e in room._renderer.events]
        await websocket.send_json({"type": "catch_up", "events": events})

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "start_game":
                num_hands = max(1, min(20, int(data.get("hands", room.config.num_hands if room.config else 6))))
                if room.status == "lobby":
                    room.start(num_hands=num_hands)
                    seats_info = [{"seat": i, "mode": "human" if s.is_human else "ai"}
                                  for i, s in enumerate(room.seats)]
                    room.broadcast({"type": "game_started", "seats": seats_info})
                    room._spectator_send({"type": "game_started", "seats": seats_info})

            elif msg_type == "continue_hand":
                room.signal_continue()

    except WebSocketDisconnect:
        room.detach_spectator(websocket)


@app.post("/api/debug-flag/{code}")
def debug_flag(code: str, note: str = Query(default=None)):
    room = rooms.get(code)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return room.add_flag(note=note)


# ── Multiplayer WebSocket ──────────────────────────────────────────────────────

@app.websocket("/ws/{room_code}/{seat}")
async def ws_game(websocket: WebSocket, room_code: str, seat: int):
    await websocket.accept()

    room = rooms.get(room_code)
    if not room or not (0 <= seat <= 5):
        await websocket.send_json({"type": "error", "msg": "Room not found"})
        await websocket.close(code=1008)
        return

    seat_ctrl = room.seats[seat]
    if seat_ctrl.is_human:
        await websocket.send_json({"type": "error", "msg": "Seat taken"})
        await websocket.close(code=1008)
        return
    loop = asyncio.get_running_loop()
    seat_ctrl.attach(websocket, loop)

    # Push current room state to the new connection.
    await websocket.send_json(room.room_state_msg(seat))

    # Notify all others that a human joined this seat.
    room.broadcast({"type": "seat_update", "seat": seat, "mode": "human"},
                   only_seat=None)

    # If game already running, send personalized replay so joiner can catch up.
    if room.status == "running" and room._renderer:
        reveal = room.reveal_seats(all_humans_done=False)
        events = [ser_event_pov(e, seat, reveal) for e in room._renderer.events]
        await websocket.send_json({"type": "catch_up", "events": events})

    # Debug mode: resend each AI seat's latest reasoning to a (re)joining player.
    for report in list(room.thoughts.values()):
        await websocket.send_json({"type": "ai_thought", **report})

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "start_game":
                num_hands = max(1, min(10, int(data.get("hands", 3))))
                if room.status == "lobby":
                    room.start(num_hands=num_hands)
                    room.broadcast({"type": "game_started",
                                    "seats": [{"seat": i, "mode": "human" if s.is_human else "ai"}
                                              for i, s in enumerate(room.seats)]})

            elif msg_type == "move":
                move_idx = data.get("move_idx")
                if move_idx is not None:
                    seat_ctrl.submit_move(int(move_idx))

            elif msg_type == "continue_hand":
                room.signal_continue()

    except WebSocketDisconnect:
        seat_ctrl.detach()
        room.broadcast({"type": "seat_update", "seat": seat, "mode": "ai"})


# ── Training log parser ────────────────────────────────────────────────────────

def _parse_training_log(log_path: str) -> dict:
    if not os.path.exists(log_path):
        return {"header": {}, "rounds": [], "status": "no_log"}

    with open(log_path) as f:
        text = f.read()

    header: dict = {}
    m = re.search(r"Self-play training loop: (\d+) rounds × (\d+) games", text)
    if m:
        header["total_rounds"] = int(m.group(1))
        header["games_per_round"] = int(m.group(2))
    m = re.search(r"ε: ([\d.]+) → ([\d.]+)\s+\|\s+keep=(\d+)", text)
    if m:
        header["eps_start"] = float(m.group(1))
        header["eps_end"]   = float(m.group(2))
        header["keep"]      = int(m.group(3))
    m = re.search(r"labels=(\w+)\s+opponent=(\w+)", text)
    if m:
        header["label_mode"] = m.group(1)
        header["opponent"]   = m.group(2)
    m = re.search(r"Starting model: (.+)", text)
    if m:
        header["start_model"] = m.group(1).strip()

    round_starts = [(mo.start(), int(mo.group(1)), float(mo.group(2)))
                    for mo in re.finditer(r"── Round (\d+)\s+ε=([\d.]+)", text)]

    rounds = []
    for idx, (start, rnum, eps) in enumerate(round_starts):
        end   = round_starts[idx + 1][0] if idx + 1 < len(round_starts) else len(text)
        chunk = text[start:end]
        rd: dict = {"round": rnum, "eps": eps}

        mo = re.search(r"Saved ([\d,]+) samples.*?\(([\d.]+)s,\s*([\d.]+) samples/s\)", chunk)
        if mo:
            rd["samples"]        = int(mo.group(1).replace(",", ""))
            rd["collect_time_s"] = float(mo.group(2))
            rd["samples_per_s"]  = float(mo.group(3))
        mo = re.search(r"y:.*?mean=([-\d.e]+)\s+std=([\d.e]+)", chunk)
        if mo:
            rd["y_mean"] = float(mo.group(1))
            rd["y_std"]  = float(mo.group(2))
        mo = re.search(r"Training on ([\d,]+) samples", chunk)
        if mo:
            rd["train_samples"] = int(mo.group(1).replace(",", ""))
        mo = re.search(r"Best val loss: ([\d.]+)", chunk)
        if mo:
            rd["best_val_loss"] = float(mo.group(1))
        mo = re.search(r"Training time: ([\d.]+)s", chunk)
        if mo:
            rd["train_time_s"] = float(mo.group(1))
        epochs = []
        for em in re.finditer(r"Epoch\s+(\d+)/\d+\s+train=([\d.]+)\s+val=([\d.]+)", chunk):
            epochs.append({"epoch": int(em.group(1)),
                           "train": float(em.group(2)),
                           "val":   float(em.group(3))})
        if epochs:
            rd["epochs"] = epochs
        mo = re.search(r"Early stop at epoch (\d+)", chunk)
        rd["early_stopped"] = bool(mo)
        if mo:
            rd["stopped_epoch"] = int(mo.group(1))
        mo = re.search(
            r"ml-vs-rule.*?ML\s+wins:\s*(\d+)\s+RULE\s+wins:\s*(\d+)\s+Ties:\s*(\d+)\s+\(([\d.]+)%\)",
            chunk, re.DOTALL)
        if mo:
            rd["vs_rule"] = {"wins": int(mo.group(1)), "losses": int(mo.group(2)),
                             "ties": int(mo.group(3)), "pct": float(mo.group(4))}
        mo = re.search(
            r"ml-vs-search.*?ML\s+wins:\s*(\d+)\s+SEARCH\s+wins:\s*(\d+)\s+Ties:\s*(\d+)\s+\(([\d.]+)%\)",
            chunk, re.DOTALL)
        if mo:
            rd["vs_search"] = {"wins": int(mo.group(1)), "losses": int(mo.group(2)),
                               "ties": int(mo.group(3)), "pct": float(mo.group(4))}
        rd["complete"] = "vs_rule" in rd
        rounds.append(rd)

    if not rounds:
        status = "starting"
    elif not rounds[-1].get("complete"):
        status = "running"
    elif len(rounds) >= header.get("total_rounds", 10):
        status = "done"
    else:
        status = "running"

    return {"header": header, "rounds": rounds, "status": status}


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5173)
    ap.add_argument("--llm-debug", action="store_true",
                    help="log every LLM request and the raw response from the model server")
    args = ap.parse_args()
    if args.llm_debug:
        from llm_client import set_debug
        set_debug(True)
    print(f"Red Ten web UI → http://localhost:{args.port}/")
    print(f"Multiplayer    → http://localhost:{args.port}/play")
    uvicorn.run(app, host="0.0.0.0", port=args.port)
