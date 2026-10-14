# AGENTS.md — working rules for red-ten (红十)

A 6-player, 3-deck 红十 card game: Python engine and FastAPI server in `src/`, AI players
in `src/` and `src/agents/`, browser UI in `web/`. This is an existing, working project
owned by a non-programmer (Mr. Liu). Change it carefully and in small steps.

`CLAUDE.md` was carried over from the original repo and is partly out of date: its test
count and "zero mypy errors" are old, the Stop hook it describes is not configured, and
it knows nothing about `src/agents/`, the Vite frontend or the LLM seat. Its debug-flag
instructions still hold. Where it disagrees with this file or with the code, trust the
code first, then this file, and check a command before relying on it.

## Commands

Run these from the repo root with the virtual environment active
(`source .venv/bin/activate`).

- Setup: `uv venv --python 3.13 && uv pip install -r requirements.txt`
- Tests: `python -m pytest tests/ -q`
- Scoring invariants over simulated hands (not collected by pytest):
  `PYTHONPATH=src:src/agents python tests/test_integration.py`
- Types: `mypy src/` (one known error in `src/gui.py`; do not add more)
- Run: `python src/web_server.py` → http://localhost:5173
- Run as the hosted server does (game only, passcode login):
  `REDTEN_HOSTED=1 REDTEN_PASSCODE=some-passcode python src/web_server.py`
- Inspect a flagged moment: `python3 src/debug_inspect.py <ROOM> <FLAG_ID>`

## Test first (TDD)

1. Write a failing test that states the behaviour, then the smallest change that passes.
2. Bug fix: reproduce it in a test first, and add an entry to `tests/TEST_LOG.md`.
3. Before changing existing code that has no test, pin its current behaviour with a
   test, then change it.
4. Move generation and comparison: add a property to `tests/test_moves_properties.py`
   rather than more hand-picked examples.
5. Tests never need a network, a model server, or a GPU. Fake the LLM client.
6. Done means: tests and mypy pass, and you ran the thing you changed.

## Game rules

- `docs/红十规则---2026-04-22---v2.txt` is the source of truth. Other 红十 variants differ;
  do not "correct" rules from general knowledge.
- A change to game behaviour needs a matching line in the rules doc or Mr. Liu's say-so.
- Every AI must return one of the `legal_moves` it was given, and must not modify
  `GameState` inside `choose_action`.

## Hidden information

- An AI seat may use only its own hand and what the table has seen: played cards, card
  counts, revealed red tens, scores.
- Never read another seat's `state.hands[p]` or `player_statuses[p].team`. The search and
  ML players do this today; that is known debt, not a pattern to copy.
- A seat knows its own side from its own hand.

## Measuring AI strength

- Teams are decided by who holds ♥10, not by seat. `src/benchmark.py` assumes seats
  0/2/4 are a team, so its win rates are not evidence. Do not quote them.
- Compare AIs by per-seat score over the same deals with seats swapped, with enough
  hands to show a margin. Say how many hands were played.

## LLM seats

- Endpoints and model names live in `config/llm_models.json`, never in code.
  No API keys in the repo.
- Always validate the model's reply against `legal_moves`; retry once, then fall back to
  an engine player. A model outage must never stall a game.
- Strategy lives in `skills/*.zh.md`, in Chinese, so Mr. Liu can edit it.
- Models differ in reply format (thinking tokens, where they appear, stop reasons).
  Handle a difference in `src/llm_client.py` / `src/llm_player.py` with a test built from
  the real raw reply; get that reply with `python scripts/llm_smoke.py --model <name> --probe`.
- LLM activity goes to the `redten.llm` logger (stdout), not `print`.
- A server that needs a different request format gets an entry in `APIS` in
  `src/llm_client.py`, selected by `"api"` in the config. Do not branch on model names.

## Hosted mode

- `REDTEN_HOSTED=1` serves only the 6-seat game, behind a shared passcode, for a group
  of friends on the internet. `src/hosting.py` holds every rule; `docs/hosting.md` is the
  runbook; `deploy/` installs it.
- A new page or API route is closed in hosted mode until it is listed in
  `src/hosting.py`. Keep it that way: list a route only if players need it and it shows
  no one else's cards. `tests/test_hosting.py` fails when the two disagree.
- Hosted mode must stay free of torch, numpy and model files (`requirements-server.txt`).
- A laptop run without `REDTEN_HOSTED` must behave as before.

## Brownfield guardrails

- Keep diffs small. Do not reformat, rename, or reorganise code you are not changing.
- Follow the existing layout: flat modules in `src/`, tests in `tests/`.
- No new dependency without a reason stated in the commit.
- `src/serializers.py` and the `web/` client share a wire format: change both together.
- Do not delete or rewrite `data/` models, `debug_logs/`, or audio assets. Do not commit
  new `*.npz`, `*.pt`, logs, `.venv/`, or `node_modules/`.
- Text players see is Chinese; code and comments are English.
- Record user-visible changes in `docs/CHANGELOG.md`.

## Git

- Work on a feature branch. Never push, force-push, or open a PR unless asked.
- One logical change per commit, with the test in the same commit.
