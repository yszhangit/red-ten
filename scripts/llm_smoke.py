"""
Play headless hands with LLM seats against rule-based seats and report how the
model behaved: decisions, options per decision, latency, retries, fallbacks.

Needs the model server named in config/llm_models.json to be running.

Usage:
    python scripts/llm_smoke.py                       # 1 hand, seat 0 is the LLM
    python scripts/llm_smoke.py --seats 0 3 --hands 2
    python scripts/llm_smoke.py --model gemma4-e4b --seed 5 --verbose
    python scripts/llm_smoke.py --model gemma4-e4b --probe   # one turn, raw output

--probe is the thing to run when a model does not work: it lists the models the
server offers, sends one opening-hand turn, and prints the request and the
response exactly as the server sent it.
"""
from __future__ import annotations
import argparse
import json
import os
import random
import statistics
import sys
import time
import urllib.request

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, _SRC)
sys.path.insert(0, os.path.join(_SRC, "agents"))

from hand import Hand                      # noqa: E402
from logger import GameLogger              # noqa: E402
from rule_player import RuleBasedPlayer    # noqa: E402
from moves import get_legal_moves          # noqa: E402
from llm_client import ModelConfig, get_model, set_debug   # noqa: E402
from llm_player import LLMPlayer           # noqa: E402
from llm_view import describe_move         # noqa: E402


def probe(cfg: ModelConfig, seat: int, seed: int, explain: bool) -> None:
    """One opening-hand turn with everything printed."""
    url = cfg.base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            listing = json.loads(resp.read().decode("utf-8"))
        # OpenAI-style servers answer {"data": [{"id": ...}]}; LM Studio's
        # native API answers {"models": [{"key": ...}]}.
        entries = listing.get("data") or listing.get("models") or []
        ids = [m.get("id") or m.get("key") for m in entries if isinstance(m, dict)]
        if ids:
            print(f"models offered by {url}: {ids}")
            if cfg.model not in ids:
                print(f'!! "{cfg.model}" (from config/llm_models.json) is not in that list')
        else:
            print(f"model list at {url} not understood: {json.dumps(listing)[:300]}")
    except Exception as e:
        print(f"could not list models at {url}: {type(e).__name__}: {e}")

    set_debug(True)
    random.seed(seed)
    players: list = [RuleBasedPlayer(i) for i in range(6)]
    hand = Hand(players, seat, GameLogger(verbose=False))
    hand.deal()
    state = hand.state
    legal = get_legal_moves(state.hands[seat], None)

    player = LLMPlayer(seat, cfg, explain=explain)
    reports: list[dict] = []
    player.on_decision = reports.append
    move = player.choose_action(state, legal)

    d = reports[0]
    print("\n── result ──")
    print(f"played: {describe_move(move)}   options: {d['n_options']}   "
          f"time: {d['latency_ms'] / 1000:.1f}s   model calls: {player.stats['model_calls']}")
    print("FALLBACK: the rule AI played this turn. " + d["error"] if d["fallback"]
          else f"model chose #{d['choice']}: {d['reason']}")
    print(f"thinking tokens received: {len(d['thinking'])} chars")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="name in config/llm_models.json")
    ap.add_argument("--seats", type=int, nargs="+", default=[0], help="LLM seats (0-5)")
    ap.add_argument("--hands", type=int, default=1)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--explain", action="store_true",
                    help="ask for a short analysis with each move (what debug mode does)")
    ap.add_argument("--debug", action="store_true",
                    help="print every request and the raw response from the model server")
    ap.add_argument("--probe", action="store_true",
                    help="send one opening-hand turn, print everything, and stop")
    args = ap.parse_args()

    cfg = get_model(args.model)
    if args.probe:
        print(f"model={cfg.name}  url={cfg.base_url}  id={cfg.model}  "
              f"max_tokens={cfg.max_tokens}  timeout_s={cfg.timeout_s:g}")
        probe(cfg, args.seats[0], args.seed, args.explain)
        return
    if args.debug:
        set_debug(True)
    print(f"model={cfg.name}  url={cfg.base_url}  LLM seats={args.seats}  "
          f"hands={args.hands}  seed={args.seed}")

    reports: list[dict] = []

    def on_decision(d: dict) -> None:
        reports.append(d)
        if args.verbose:
            tag = "FALLBACK" if d["fallback"] else f"#{d['choice']}"
            said = d["reason"] or d["error"] or (d["replies"][-1][:60] if d["replies"] else "")
            print(f"  P{d['seat']} {d['n_options']:3d} options {d['latency_ms'] / 1000:5.1f}s "
                  f"{tag:8s} → {d['move']}   | {said}")

    llm_seats: list[LLMPlayer] = []
    players: list = []
    for i in range(6):
        if i in args.seats:
            p = LLMPlayer(i, cfg, explain=args.explain)
            p.on_decision = on_decision
            llm_seats.append(p)
            players.append(p)
        else:
            players.append(RuleBasedPlayer(i))

    t0 = time.time()
    for h in range(args.hands):
        random.seed(args.seed + h)
        hand = Hand(players, random.randint(0, 5), GameLogger(verbose=False))
        hand.deal()
        result = hand.play()
        red = [f"P{p}" for p in result.red_team]
        print(f"hand {h + 1}: {result.terminal.name}  red={red}  "
              f"finish={result.finish_order}  scores={result.final_scores}")
    elapsed = time.time() - t0

    lat = [d["latency_ms"] / 1000 for d in reports]
    opts = [d["n_options"] for d in reports]
    total = {k: sum(p.stats[k] for p in llm_seats)
             for k in ("decisions", "model_calls", "retries", "fallbacks")}
    print(f"\ndecisions={total['decisions']}  model_calls={total['model_calls']}  "
          f"retries={total['retries']}  fallbacks={total['fallbacks']}")
    if lat:
        print(f"options per decision: median={statistics.median(opts):.0f}  max={max(opts)}")
        print(f"latency per decision: median={statistics.median(lat):.1f}s  "
              f"max={max(lat):.1f}s  total={sum(lat):.0f}s of {elapsed:.0f}s")


if __name__ == "__main__":
    main()
