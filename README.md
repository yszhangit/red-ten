# Red Ten Poker (红十扑克牌)

A 6-player Chinese card game with three levels of AI, a web UI, and a self-play training pipeline.

---

## What is Red Ten Poker?

红十 (Red Ten) is a trick-taking card game played with two standard decks (108 cards total). Six players split into two teams of three. The goal is for your team to accumulate points by winning tricks that contain scoring cards (5s, 10s, Kings, and the special Red Tens). The Red Ten (红十, the 10 of Hearts or Diamonds) is the most powerful card in the game.

Players: 6 (teams of 3 — alternating seats, so P0/P2/P4 vs P1/P3/P5)
Cards: 2 decks, 108 cards total
Scoring cards: 5=5pts, 10=10pts, K=10pts, Red Ten=10pts (special)
Win condition: outscore the opposing team in each hand

---

## The Three AI Tiers

This project has three levels of AI, each building on the last:

### Tier 1: Rule-Based Player (`rule`)
Simple heuristics — plays high cards when leading, follows suit, avoids giving away scoring cards. Fast and predictable. Good baseline.

### Tier 2: Search Player (`search`)
Smarter — before each move, it imagines 12 possible "worlds" (guesses about what cards opponents hold), simulates the outcome of each legal move in each world, and picks the move that scores best on average. This is called **determinized search** (or "Monte Carlo" sampling). Beats the Rule player roughly 50% of the time.

### Tier 3: ML Player (`ml`)
A small neural network (4 layers: 65→128→64→32→1) trained via **self-play**. It uses the same search loop as Tier 2, but replaces the hand-coded evaluation function with the learned network. Trained entirely by playing games against itself and the Search player.

---

## Project Structure

```
red-ten/
├── src/
│   ├── cards.py          # Card, Rank, Suit definitions
│   ├── moves.py          # Legal move types (singles, pairs, bombs, …)
│   ├── state.py          # GameState, scoring, team assignment
│   ├── hand.py           # One hand of play (deal → tricks → result)
│   ├── session.py        # Multi-hand session, make_players() helper
│   ├── rule_player.py    # Tier 1: heuristic AI
│   ├── search_player.py  # Tier 2: determinized 1-step lookahead
│   ├── features.py       # 65 features extracted per candidate move
│   ├── ml_player.py      # Tier 3: neural network player
│   ├── collect_data.py   # Generate training data (self-play games)
│   ├── train_model.py    # Train the MLP on collected data
│   ├── train_selfplay.py # Full self-play loop (collect → train → benchmark)
│   ├── benchmark.py      # Compare two AI tiers head-to-head
│   ├── web_server.py     # Flask server (game UI + training dashboard)
│   └── gui.py            # pygame desktop UI
├── web/
│   ├── index.html        # Game viewer (web UI)
│   └── dashboard.html    # Training dashboard
├── data/
│   ├── model.pt          # Current best ML model
│   ├── model_r{N}.pt     # Checkpoint from self-play round N
│   ├── sp_r{N}.npz       # Self-play training data from round N
│   └── eval_labels.npz   # Bootstrap data (SearchPlayer distillation)
└── README.md
```

---

## Quickstart

### Prerequisites
```bash
cd /Users/henryliu/dev/red-ten
source .venv/bin/activate   # or: conda activate your-env
pip install flask torch numpy
```

### Watch AI games in the browser

```bash
python src/web_server.py
# Open http://localhost:5173
```

Choose an AI tier in the config dialog:
- **rule** — all six players use Tier 1 (Rule-Based)
- **search** — all six use Tier 2 (Search)
- **ml** — all six use the trained ML model
- **ml-vs-rule** — ML team vs Rule team (benchmark mode)
- **ml-vs-search** — ML team vs Search team

### Run a benchmark (command line)

```bash
# ML vs Rule, 80 games
python src/benchmark.py --tier ml-vs-rule --n 80

# ML vs Search, 80 games
python src/benchmark.py --tier ml-vs-search --n 80

# Search vs Rule (baseline reference)
python src/benchmark.py --tier mixed --n 80

# Use a specific checkpoint
python src/benchmark.py --tier ml-vs-rule --model data/model_r6.pt --n 80
```

Output looks like:
```
Benchmarking: ml-vs-rule  N=80  seed=77  workers=8
  ML         wins:  46  RULE       wins:  31  Ties:   3  (57.5%)
  Time: 38.2s  (0.5s/hand)
```

---

## Training the ML Model

Training happens in three stages. You normally only need **Stage 3** unless starting from scratch.

### Stage 1: Bootstrap data (one-time, already done)

Generate 1000 games where every candidate move is evaluated by the Search player. This gives the ML model a starting point.

```bash
python src/collect_data.py --games 1000 --out data/eval_labels.npz --mode eval
```

This takes ~30 minutes and produces `data/eval_labels.npz` (~3.6M samples).

### Stage 2: Train initial model (one-time, already done)

```bash
python src/train_model.py --data data/eval_labels.npz --out data/model.pt --epochs 50
```

The model learns to approximate what the Search player considers a "good position." After this, ML plays at roughly Search-player level (~49% vs Rule).

### Stage 3: Self-play loop (iterative improvement)

This is the main training loop. Each round:
1. Play 500 games: ML (with some random exploration) vs Search opponents
2. Label each decision with **advantage** = how much better this move was vs the alternatives
3. Retrain the neural network from scratch on all data collected so far
4. Benchmark the new model vs Rule and vs Search
5. Save the checkpoint, repeat

```bash
# Recommended: advantage labels, vs Search opponents (currently running)
python src/train_selfplay.py \
  --rounds 10 --games 500 --workers 8 \
  --label-mode advantage --opponent search \
  --start-model data/model.pt

# Resume from a specific checkpoint
python src/train_selfplay.py \
  --start-model data/model_r6.pt --start-round 7 \
  --rounds 5 --label-mode advantage --opponent search

# Quick smoke test (2 rounds, 20 games, 5 epochs)
python src/train_selfplay.py \
  --rounds 2 --games 20 --bench-n 10 --epochs 5 \
  --label-mode advantage --opponent search
```

Key flags:
| Flag | Default | Meaning |
|------|---------|---------|
| `--rounds` | 10 | How many self-play rounds to run |
| `--games` | 500 | Games per round (more = better signal, slower) |
| `--workers` | 8 | Parallel CPU workers |
| `--keep` | 4 | How many rounds of data to keep in replay buffer |
| `--epochs` | 60 | Max training epochs per round |
| `--label-mode` | advantage | `advantage` = relative move quality; `outcome` = game result |
| `--opponent` | search | Who plays against ML: `rule` or `search` |
| `--eps-start` | 0.4 | Starting random-move probability (exploration) |
| `--eps-end` | 0.1 | Final random-move probability |

### Watch training progress

Open the training dashboard:
```
http://localhost:5173/dashboard
```

Or tail the log directly:
```bash
tail -f /tmp/selfplay_adv_search.log
```

---

## Understanding the Results

### Win rate numbers

- **vs Rule**: ML team score vs Rule team score across N games. 50% = tied with Rule. Previous best: 57.5%.
- **vs Search**: ML team vs Search team. 50% = tied with Search (good!). SearchPlayer itself only gets ~49% vs Rule.
- **N=40** benchmarks during training are noisy (±8%). Results crossing 60% vs Search are meaningful.

### Why vs-Rule and vs-Search diverge

The ML model trained with `--opponent search` learns to beat *Search-style* play (careful, lookahead-based). RuleBasedPlayer plays very differently (aggressive, simple heuristics), so the model can win against Search but struggle against Rule's unpredictable style. This is a known tradeoff.

### What "advantage labels" means

Instead of labeling moves by whether the team won the whole game (noisy — one bad hand shouldn't penalize every decision), we label each move by how much better it was compared to all other legal moves at that moment. Score = `evaluate(this_move) − average(evaluate(all_moves))`. This is zero on average and focuses the model on *relative move quality*, not luck.

### The replay buffer

Training always uses the last `--keep` rounds of data (default: 4 rounds). Older data cycles out. This prevents the model from over-fitting to early, poor-quality games. The eval bootstrap data (`eval_labels.npz`) is seeded into the buffer at round 1 and cycles out after 4 rounds.

---

## Results So Far

### Previous run (outcome labels, vs Rule opponents — 8 rounds)
Best checkpoint: `data/model_r6.pt` — **57.5% vs Rule**, 45% vs Search.
The model learned to exploit Rule's weaknesses but couldn't beat Search.

### Current run (advantage labels, vs Search opponents — 10 rounds)
| Round | vs Rule | vs Search |
|-------|---------|-----------|
| 1 | 47.5% | 52.5% |
| 2 | 40.0% | 45.0% |
| 3 | 47.5% | **65.0%** |
| 4 | 40.0% | 55.0% |
| 5 | **55.0%** | **60.0%** |
| 6 | 52.5% | 45.0% |
| 7 | 37.5% | **60.0%** |

vs-Search average ~56% — consistently beating SearchPlayer. The new training approach works.

---

## Data Files

| File | Size | What it contains |
|------|------|-----------------|
| `data/eval_labels.npz` | 50 MB | 3.6M samples: features + SearchPlayer eval score per candidate move, from 1000 Rule-vs-Rule games. Used to bootstrap training. |
| `data/sp_r{N}.npz` | 4–5 MB | ~160k samples from one round of self-play (500 games, advantage labels). |
| `data/model.pt` | 79 KB | Current active model (updated each round). |
| `data/model_r{N}.pt` | 79 KB | Frozen checkpoint from round N. |

---

## Debugging live games

The multiplayer UI has a **🚩 Flag** button (top-left, visible during a game). Click it whenever something looks wrong — it inserts a timestamped marker into the game log and copies a reference to your clipboard:

```
room NKKK, flag 0a59be0b, check debug_logs/NKKK.jsonl
```

To inspect that flag — as a human or as an AI coding assistant:

```bash
python3 src/debug_inspect.py NKKK 0a59be0b
```

Output shows the legal moves offered at that moment, the human player's full hand, the current trick state, and the 5 preceding events for context. The game log (`debug_logs/<ROOM>.jsonl`) is written continuously during play; crash dumps appear as `debug_logs/crash_<ROOM>.json`. See `docs/debug_snapshot_tool.md` for full details.

---

## LLM AI seat (local language model)

An AI seat can be played by a small local language model. The game shows the
model every legal play as a numbered list plus a description of the table, and
plays the number it answers with. If the model is slow, down, or answers
nonsense, that turn is played by the rule-based AI instead, so a game never
stalls.

**1. Start a model server** that speaks the OpenAI chat API. Any of
`mlx_lm.server`, LM Studio, Ollama or llama.cpp works, for example:

```bash
mlx_lm.server --model mlx-community/Qwen3.5-9B-4bit --port 8080
```

**2. Tell the game where it is** in `config/llm_models.json`: `base_url`, the
`model` name as the server knows it, and optional `extra_body` fields sent with
every request (Qwen needs its "thinking" switched off there). Set the
`REDTEN_LLM_CONFIG` environment variable to use a different file.

Small models that "think" before answering must have thinking switched off, or
the reply limit is used up before any answer appears. How to do that depends on
the server:

| Server | Config entry |
|---|---|
| `mlx_lm.server` | `"extra_body": {"chat_template_kwargs": {"enable_thinking": false}}` |
| LM Studio (0.4 or newer) | `"api": "lmstudio"`, `"base_url": "http://localhost:1234/api/v1"`, `"extra_body": {"reasoning": "off"}` |

`"api": "lmstudio"` makes the game use LM Studio's own API instead of its
OpenAI-style one, because only that one accepts the `reasoning` setting. If your
LM Studio is older, keep the default API (`base_url` ending in `/v1`) and switch
the model's reasoning toggle off inside LM Studio instead.

**3. Pick it in the UI**: on `/play` choose "LLM AI" for the empty seats, or on
`/training` choose "LLM AI" and a model per seat.

**Changing how it plays**: edit `skills/strategy.zh.md` (strategy advice) and
`skills/rules_brief.zh.md` (rules summary). They are plain Chinese text sent to
the model at the start of every turn; restart the server after editing.

**Checking it**: `python scripts/llm_smoke.py --verbose` plays one hand without
the UI and prints each decision, the model's stated reason, and timings. Every
decision is also appended to `debug_logs/llm/<date>.jsonl`.

**When a model does not work** ("模型没有给出有效回答" in the debug panel, or the
AI plays instantly because the rule AI took over):

- The server log (the terminal running `web_server.py`) has one `[llm]` line per
  decision. A failed turn also prints the reason, the model's raw reply, why it
  stopped (`finish_reason`) and the token counts, then a `FALLBACK` line.
- `python src/web_server.py --llm-debug` additionally prints every request and
  the response body exactly as the model server sent it.
- `python scripts/llm_smoke.py --model <name> --probe` does the same for a single
  opening-hand turn without the UI, and first checks that the configured model
  name is one the server offers.

Usual causes, and what the log line looks like:

| Log says | Cause | Fix |
|---|---|---|
| `empty reply (finish_reason=length …) — the model spent its reply on thinking` | Thinking is on and used up `max_tokens` | Switch thinking off in the model server, or raise `max_tokens` |
| `HTTP 400 …` mentioning context length | The model was loaded with a small context window. An opening hand needs about 4,000 tokens | Load the model with a context length of 8192 or more |
| `HTTP 404 …` or a model-not-found message | `model` in the config is not the server's name for it | Copy the id from `<base_url>/models` |
| `TimeoutError … timeout_s=30` | The model is slower than the limit, or was still loading | Raise `timeout_s` |
| `Connection refused` | Wrong `base_url`, or the server is not running | Start it, or fix the port |
| `reply 1 of 2 not usable … '<text>'` | The model answered, but not with a number from the list | Look at the text; the skill or `max_tokens` may need changing |

LM Studio notes: the server listens on port 1234 by default; Gemma 4 has
thinking on by default (the shipped `gemma4-e4b` entry switches it off per
request); and models are often loaded with a 4096-token context unless you
raise it.

### Debug mode: watch how the AI plays

Tick **Debug mode** in the `/play` lobby before creating a game. In that room:

- every AI seat's hand is shown to the human players (one chip per rank, e.g.
  `K×2`; ♥10 is highlighted);
- each LLM seat is asked to write two or three sentences of analysis with its
  move. A 💭 appears on the seat once it has played: hover to peek, click to
  pin the panel. The panel shows the move, the model's explanation, any
  thinking tokens the server returned, the full list of options it chose from,
  exactly what it was shown, and its raw reply.

Debug rooms are marked "debug" in the room list and on the table, since
everyone in the room sees the AI hands. Asking for the analysis makes each LLM
decision slower: roughly 6–15 seconds instead of 4.

About thinking tokens: Qwen's built-in "thinking" is switched off in
`config/llm_models.json`, because with it on the model spent 6,000 tokens
(over five minutes) on a single five-option turn without reaching an answer. The panel
shows thinking tokens whenever a model does return them, so another model or
config entry can use them.

With Qwen3.5-9B on an M4 laptop a decision takes about 4 seconds. The server
answers one request at a time, so one or two LLM seats per game is comfortable.

---

## Testing

### Run tests

```bash
python -m pytest tests/ -v     # 15 tests: 7 legal_moves + 8 hypothesis properties
mypy src/                      # zero type errors
```

### What's in place

| Component | Status | Details |
|-----------|--------|---------|
| **pytest** | ✅ | `pyproject.toml` with `pythonpath = ["src"]`, no `sys.path` hacks |
| **hypothesis** | ✅ | `tests/test_moves_properties.py` — 8 invariant tests covering `get_legal_moves`, `identify_move`, `beats` |
| **mypy** | ✅ | `mypy.ini`, all type errors fixed (zero), 277 annotated functions validated |
| **Stop hook** | ✅ | `.claude/settings.json` auto-runs `pytest -q` before each conversation ends |
| **CI** | ⬜ | GitHub Actions (deferred) |

### Property-based test invariants (`tests/test_moves_properties.py`)

- No legal move contains cards not in the player's hand
- `get_legal_moves()` never returns duplicates
- PASS is always present in legal moves when following
- Following moves always beat the current best play
- `beats()` is asymmetric (if A beats B, B never beats A)
- Bomb hierarchy is transitive
- `identify_move()` roundtrips correctly for all legal moves
- `identify_move()` accepts valid card sets for every CardType

### Bugs found by hypothesis

- **Red Ten `straight_rank(ace_low=True)`**: ♥10 ignored the `ace_low` parameter, producing wrong straight rank (16 instead of 9). Fixed in `src/cards.py`.

### Test coverage

| Tested (8/22) | Untested (14/22) |
|---|---|
| `moves.py`, `cards.py`, `hand.py`, `state.py`, `serializers.py`, `rule_player.py`, `search_player.py`, `room_manager.py` | `evaluator.py`, `features.py`, `identity.py`, `collect_data.py`, `train_model.py`, `train_selfplay.py`, `benchmark.py`, `ml_player.py`, `gui.py`, `gui_renderer.py`, `logger.py`, `session.py`, `web_server.py`, `debug_inspect.py` |

---

## Troubleshooting

**"No module named torch"** — activate your virtual environment first.

**ML player is slow** — it runs on CPU by default (MPS/GPU overhead is too high for tiny batch sizes). Expect ~2–6 seconds per hand.

**Benchmark results are noisy** — N=40 has ±8% statistical error. Use N=80–200 for reliable comparison.

**Training crashed mid-run** — resume with `--start-model data/model_r{last}.pt --start-round {last+1}`.
