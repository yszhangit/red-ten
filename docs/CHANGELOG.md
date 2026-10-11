# Changelog — Red Ten Poker (红十)

---

## 2026-10-11

### LLM AI 座位（本地大模型出牌）

**文件**: `src/llm_client.py`, `src/llm_view.py`, `src/llm_player.py`, `config/llm_models.json`, `skills/*.zh.md`, `src/session.py`, `src/room_manager.py`, `src/web_server.py`, `web/play.html`, `web/training.html`

**功能**: 新增 AI 层级 `llm`，由本地大模型（OpenAI 兼容接口）决定出牌。
- 程序把所有合法出牌编号列出（只差花色的出法合并，含♥10的出法单独列出），连同牌桌情况发给模型，模型回答编号。
- 模型只能看到自己的手牌和公开信息（已出的牌、各家剩余张数、已亮的红十、得分），看不到别人的手牌和真实阵营。
- 回答无效时重试一次，仍无效或模型服务出错时，该回合改由规则型 AI 出牌，牌局不会卡住。
- 模型地址和名称在 `config/llm_models.json` 中配置（目前有 Qwen3.5-9B 和 gemma4 e4b 两项）。
- 规则摘要和打牌策略写在 `skills/rules_brief.zh.md`、`skills/strategy.zh.md`，用中文书写，可直接修改来调整 AI 打法。
- `/play` 大厅的 AI 选项新增 "LLM AI"；`/training` 每个座位可选 "LLM AI" 并指定模型。
- LLM 座位不再额外加 1-3 秒的"思考"延迟（模型本身已需数秒）。
- 每次决策记录在 `debug_logs/llm/<日期>.jsonl`；`scripts/llm_smoke.py` 可不开界面跑一局并统计耗时。

### LLM 座位：服务端日志和 Gemma 4 兼容

**文件**: `src/llm_client.py`, `src/llm_player.py`, `src/web_server.py`, `scripts/llm_smoke.py`, `config/llm_models.json`, `tests/test_llm_logging.py`

**功能**: 模型出错时可以直接从服务端终端看出原因。
- 每次 LLM 决策在终端输出一行 `[llm]` 日志（座位、模型、选项数、耗时、token 数、所选出牌和理由）。
- 回答无法使用时，输出模型的原始回答、停止原因（finish_reason）和 token 数；请求失败时输出服务器返回的错误内容；改由规则 AI 出牌时输出 `FALLBACK`。
- `python src/web_server.py --llm-debug`（或环境变量 `REDTEN_LLM_DEBUG=1`）会额外输出每次请求和模型服务器的原始响应。
- `python scripts/llm_smoke.py --model <名称> --probe` 只发送一手开局牌并输出全部内容，同时检查配置的模型名是否在服务器的模型列表里。
- 错误信息更具体：空回答会说明是否被"思考"占满；超时会指出 `timeout_s`；HTTP 错误带上服务器的说明（如上下文长度不足、模型名不存在）。
- 识别 Gemma 4 的思考格式（`<|channel>thought … <channel|>`），不会把思考内容里的数字当成出牌编号，并在调试面板中显示。
- `gemma4-e4b` 的默认配置改为 LM Studio（端口 1234）。
- 实测 Gemma 4 E4B 在 LM Studio 上默认开启思考：120 个 token 的回答额度里 117 个用于思考，没有给出答案。LM Studio 的 OpenAI 兼容接口没有关闭思考的参数，因此新增配置项 `"api": "lmstudio"`，改用 LM Studio 自带接口（`/api/v1/chat`）并发送 `"reasoning": "off"`。`gemma4-e4b` 默认使用这种方式，超时放宽到 60 秒。

### 调试模式：显示 AI 手牌和模型思考

**文件**: `src/room_manager.py`, `src/web_server.py`, `src/llm_player.py`, `src/llm_client.py`, `src/llm_view.py`, `web/play.html`, `web/src/pages/{lobby,play,game}.js`, `web/src/styles/*.css`

**功能**: `/play` 大厅新增 "Debug mode" 选项。在调试房间里：
- 所有 AI 座位的手牌对真人玩家可见（按点数分组显示，♥10 单独标出）。
- LLM 座位每次出牌会附带两三句分析。出过牌的座位上出现 💭，鼠标悬停可查看，点击可固定面板。
- 面板内容：出的牌、模型的说明、模型返回的思考过程（thinking tokens，如有）、全部可选出牌、模型看到的信息、模型的原始回答。
- 调试房间在房间列表和牌桌上都有标注；普通房间不发送任何 AI 手牌或思考内容。
- 服务端新增 WebSocket 消息 `ai_thought`，`room_state` 新增 `debug` 字段，创建房间接口新增 `debug` 参数。

**说明**: Qwen 自带的"思考"功能保持关闭——打开后，一次只有 5 个选项的出牌用掉 6000 个 token（超过五分钟）仍未给出答案。调试模式下每次 LLM 出牌约需 6-15 秒（普通模式约 4 秒）。

### 修复：首页的"训练"和"回看"按钮打不开

**文件**: `src/web_server.py`

**问题**: 首页按钮跳转到 `/training` 和 `/replay`，但服务器没有这两个路由，返回 404。

**修复**: 新增 `/training`、`/replay` 两个页面路由。

### 修复：轮到自己时看不到 "Pass" 按钮

**文件**: `web/src/pages/game.js`

**问题**: 轮到玩家出牌时 Pass 按钮始终不显示；当只能过牌时，玩家无法操作，牌局卡住。

**原因**: 按钮的隐藏样式从行内样式移到了样式表 (`#pass-btn { display: none; }`)，而显示按钮的代码只是清空行内样式，按钮仍被样式表隐藏。

**修复**: 显示按钮时明确设置 `display: inline-block`。

### 开发环境

- 新增 `requirements.txt`（含 WebSocket 所需的 `websockets`）和 `AGENTS.md`（开发约定）。

---

## 2026-05-09

### 多智能体 AI 决策系统 (Phase 1-7)

**设计文档**: `docs/MULTI_AGENT_AI_DESIGN.md`

实现完整的 5 阶段多智能体决策管线，替代原有的单一规则/搜索/ML 玩家。每个 AI 玩家由多个内部智能体协作决策：

**Phase 1 — 分析层** (`src/agents/analyst_*.py` 5个文件):
- `HandAnalyst` — 分析手牌结构（炸弹数量/等级、顺子潜力、最短完成路径）
- `PositionAnalyst` — 分析座位位置（首出/末位、出牌权归属、被压风险）
- `TeamAnalyst` — 分析队友状态（红十暴露程度、是否需要帮助、团队完成进度）
- `OpponentAnalyst` — 分析对手状态（剩余牌数、威胁等级、身份推测）
- `RiskAnalyst` — 综合风险评估（可见炸弹数、分牌暴露风险、被关概率）
- 分析结果汇总到 `SharedAnalysis` 数据类，供下游消费

**Phase 1.5 — 战略层** (`src/agents/principle_*.py` 2个文件):
- `GuanRenPrinciple` — 关人原则：我方已有 ≥2 人出完时激活，全力阻止对手出完
- `AvoidBeingGuanedPrinciple` — 生存原则：对手已有 ≥1 人出完、我方无人出完时激活，必须尽快破关
- `StrategyDirector` — 根据 `principle_intensities` 合并活跃原则的权重修正
- 每个原则输出 `rule_weight_modifiers`，叠加到 Phase 2 的规则权重

**Phase 2 — 研究员层** (`src/agents/researcher_*.py` 3个文件):
- `BullishResearcher` (激进型) — 关注进攻规则（intercept, score_rush, speed_first）
- `BearishResearcher` (保守型) — 关注防守/团队/时机规则（safe_play, identity_protect, friendly_fire, help_teammate, last_act, delay_bomb）
- `WildcardResearcher` (诡诈型) — 加载全部 9 条规则，加随机扰动 ±0.15，从 top-3 加权随机选择
- `ResearcherBase` — 共享逻辑：人格权重覆盖 + 战略修正量叠加，最终权重 clamp [0, 3.0]

**Phase 3 — 辩论系统** (`src/agents/brain.py` `_run_debate()`):
- 按人格配置的 `debate_rounds` 进行多轮辩论
- 每轮比较各研究员的 `preferred_category`，冲突时降低信心：`confidence *= 0.8^conflicts`

**Phase 4 — 9 条战术规则** (`src/agents/rule_*.py` 9个文件):

| 规则 | 类别 | 默认权重 | 触发条件 |
|------|------|----------|----------|
| `InterceptRule` | offensive | 0.8 | 对手 ≤2 张牌时出炸弹 |
| `FriendlyFireRule` | teamwork | 1.0 | 当前领先者是队友时不出炸弹 |
| `LastActRule` | timing | 1.0 | 末位出牌：队友领先则 pass，对手领先则出牌 |
| `SafePlayRule` | defensive | 0.7 | 有未见面炸弹时慎出分牌 |
| `IdentityProtectRule` | defensive | 1.0 | 红十未暴露时不打出红十 |
| `HelpTeammateRule` | teamwork | 0.8 | 队友被压制时出炸弹解救 |
| `DelayBombRule` | timing | 0.6 | 手牌多且无紧急威胁时保留炸弹 |
| `ScoreRushRule` | offensive | 0.8 | 优先出分牌（5/10/K）抢分 |
| `SpeedFirstRule` | offensive | 0.7 | 手牌多时优先出多张牌加速清空 |

**Phase 5 — 决策融合** (`src/agents/lead_agent.py`):
- 基线启发式评分：BOMB=0.85, CONSEC_TRIPLES=0.75, TRIPLE_PAIR=0.70, STRAIGHT=0.55, ..., PASS=0.05
- 融合公式：`final = w_base × baseline + w_opinion × Σ(researcher_score × confidence) + w_random × ε`
- 战略紧迫度转移：最多 15% 权重从 baseline 转移到 opinion

**Phase 6 — 6 种人格配置** (`src/agents/personalities.py`):

| 人格 | 研究员 | 辩论轮数 | 融合权重 (B/O/R) | 特征 |
|------|--------|----------|-------------------|------|
| 激进型 | bullish | 0 | 0.30/0.70/0.00 | 强进攻，弱防守，speed_first=1.5 |
| 保守型 | bearish | 0 | 0.30/0.70/0.00 | 强防守，安全优先，identity_protect=1.5 |
| 均衡型 | bull+bear | 2 | 0.40/0.60/0.00 | 全部默认权重，2轮辩论 |
| 诡诈型 | bull+wild | 1 | 0.20/0.60/0.20 | 20% 随机噪声，不可预测 |
| 配合型 | bull+bear | 2 | 0.35/0.50/0.15 | 团队协作权重 15%，friendly_fire=1.3 |
| 自私型 | bullish | 0 | 0.25/0.55/0.20 | 20% 随机，弱团队，强抢分 |

**Phase 7 — 集成** (`src/session.py`):
- 新增 `"multi-agent"` tier：6 个默认人格 MultiAgentPlayer 对战
- 新增 `"personalities"` tier：6 种不同人格各一，用于观察人格差异

**基础设施**:
- `src/agents/rules_base.py` — `StrategyRule` ABC 和 `RuleRegistry` 注册表
- `src/agents/researcher_base.py` — `ResearcherBase` ABC，权重计算与分数聚合
- `src/agents/shared_analysis.py` — `SharedAnalysis`、`StrategicContext`、`ResearcherOpinion` 数据类
- `src/agents/brain.py` — `MultiAgentBrain` 编排完整管线，`MultiAgentPlayer` 集成到游戏引擎
- `pyproject.toml` pythonpath 新增 `"src/agents"`

**Bug 修复 — 生存原则权重错误** (`principle_avoid_being_guaned.py`):
- `intercept: -0.3 → +0.4`：原值在生存模式下反而不鼓励炸弹拦截，导致被关
- `delay_bomb: +0.2 → -0.4`：原值在生存模式下鼓励保留炸弹，与破关目标矛盾
- 修复后炸弹得分从 +0.500 提升到 +1.200（与 pass 的差距从 +0.620 扩大到 +1.320）

**文件变更**: `src/agents/` 目录 25 个新文件, `src/session.py` (新增 2 个 tier), `pyproject.toml` (pythonpath), `src/moves.py` (新增 CONSEC_TRIPLES 牌型 + 顺子验证重构)

---

### MLP 值网络 — 多智能体自学习基础

**设计文档**: `docs/MULTI_AGENT_AI_DESIGN.md` Section 14

**问题**: MultiAgentPlayer 的 LeadAgent 使用硬编码启发式 baseline（BOMB=0.85, SINGLE=0.10...），不随训练提高。MLPlayer 有成熟的自对弈训练管线，但和 MultiAgentPlayer 不互通。

**方案**: 将 MLP 值网络嵌入 LeadAgent 替换启发式 baseline。MLP 提供"局面客观评估"，研究员规则提供"风格主观偏好"，融合公式不变。

**架构改动**:
- `lead_agent.py` — 新增 `model`/`n_mlp_samples`/`p_red`/`player_id` 参数；` _mlp_baseline_scores()` 复用 MLPlayer 的 `sample_world → simulate_trick → extract_features → MLP` 管线，min-max 归一化到 [0,1]；原启发式改名为 `_heuristic_baseline()`
- `brain.py` — `MultiAgentBrain.__init__()` 新增 `model_path`/`epsilon`/`device` 参数，自动加载 MLP 模型；`decide()` 新增 ε-greedy 探索；`_fuse_decision()` 传递 model + p_red 给 LeadAgent
- `brain.py` — `MultiAgentPlayer.__init__()` 新增 `model_path`/`epsilon`/`device` 参数
- `session.py` — 新增 `multi-agent-mlp` 和 `multi-agent-mlp-vs-rule` tier
- `benchmark.py` — path 新增 `src/agents`

**向后兼容**: 不传 `model_path` 时行为完全不变（回退到启发式 baseline）。已有 6 种人格配置、已有 benchmark tier 不受影响。

**Benchmark 验证**:
- `multi-agent-vs-rule` (无 MLP): 47.0% vs RuleBased (200 局)
- `multi-agent-mlp-vs-rule` (含 MLP): 38.0% vs RuleBased (50 局)

MLP 版胜率较低是预期的——现有 MLP 模型是用 MLPlayer 搜索数据训练的，尚未在 MultiAgentPlayer 决策管线上训练。**后续需要跑基于人格的自对弈训练**（Section 14.4）来让 MLP 适配多智能体融合框架。

---

### 设计文档更新

- Section 14 新增「MLP 值网络 — 多智能体自学习」完整设计
- Section 13.1 S2 生存原则权重修正表更新：`intercept: −0.3→+0.4`, `delay_bomb: +0.2→−0.4`（与代码修复一致）

---

### CONSEC_TRIPLES 牌型支持

**文件**: `src/moves.py`

**功能**: 新增 `CardType.CONSEC_TRIPLES` (连三条)，支持如 333-444-555 的连续三条出牌。`Move.type_label`、`get_legal_moves`、`identify_move` 均完成适配。

**顺子验证重构**: `_is_valid_straight()` 重写，明确 A 的双重角色（常主牌 or 数字1）和 2 的限制（只能作数字2，不能作高端）。

---

### AI 自对弈观战模式 (AI Self-Play Spectator Mode)

**功能**: 在现有人机对战网页界面基础上，新增 AI vs AI 观战模式。用户可配置 6 个座位的 AI 类型和人格，实时观看全牌面对战。

**设计**: 新增观战 WebSocket 端点 `/ws/{code}/spectate`，与现有 `/ws/{room}/{seat}` 并行。观战者接收非 POV 游戏事件（全部手牌可见），无需出牌操作。

**服务端改动** (`src/room_manager.py`, `src/web_server.py`, `src/session.py`):
- `SeatConfig(tier, personality)` + `BattleConfig(seats, speed, num_hands)` — 按座 AI 配置
- `GameRoom` 新增 spectator 支持：`attach_spectator()`/`detach_spectator()`/`_spectator_send()`
- `broadcast_event()` — 始终向观战者发送非 POV 事件
- `_wait_for_continue()` — 无人类玩家时按速度自动继续（normal 3s / fast 1.5s / instant 1s）
- `room_state_msg()` — 返回座位 tier/personality 信息
- `POST /api/battle/create` — 创建 AI 对战房间
- `GET /api/tiers` — 返回可用 AI tier 和 personality 列表
- `WS /ws/{room_code}/spectate` — 观战 WebSocket
- `SPEED_DELAYS` — 三档速度控制（normal/fast/instant）
- 新增 `multi-agent` tier 到 lobby tier 选项

**客户端改动** (`web/play.html`):
- Lobby 新增 "AI vs AI Battle" 按钮，跳转到战斗配置界面
- 战斗配置界面：6 行座位配置（tier + personality 下拉）、速度选择、局数选择
- Lobby 房间列表显示战斗房间，提供 "Watch" 按钮
- 观战模式：全部手牌正面显示、AI tier/personality 显示在座位名旁
- 底栏替代为 "AI Battle · Speed · Hand N" 状态条
- 牌局结束覆盖层按钮适配（下一手/退出观战）
- Game Over 覆盖层按钮适配（New Battle）

**向后兼容**: `GameRoom.__init__` 同时支持旧签名 `(code, ai_tier)` 和新签名 `(code, config=BattleConfig)`。旧人机对战模式完全不受影响。

**文件变更**: `src/room_manager.py` (spectator + BattleConfig), `src/web_server.py` (3 个新端点 + agents path fix), `src/session.py` (AVAILABLE_TIERS/AVAILABLE_PERSONALITIES/make_players_from_config), `web/play.html` (~200 行新增/修改)

---

## 2026-05-08

### 测试基础设施 (P0 + P1)

**P0.1 — pytest 迁移**: 
- 创建 `pyproject.toml`，配置 `[tool.pytest.ini_options] pythonpath = ["src"]`
- 移除 `tests/test_legal_moves.py` 和 `tests/test_integration.py` 中的 `sys.path.insert` 技巧
- 所有 15 个测试通过（7 个 legal_moves + 8 个 hypothesis 属性测试）

**P0.2 — hypothesis 属性测试**: 
- 新建 `tests/test_moves_properties.py`，8 个不变量测试覆盖 `get_legal_moves`、`identify_move`、`beats`
- 测试不变量：出牌只用手中卡牌、无重复出牌、跟牌时必有 PASS、跟牌必压过当前牌型、beats 反对称、炸弹传递性、identify_move 往返一致性
- 11 个 `@example` 用例覆盖所有牌型（炸弹/顺子/连对/连三条/王牌炸弹）
- 顺带发现并修复了 `cards.py` 中红十 `straight_rank(ace_low=True)` 忽略 ace_low 参数的 bug

**P1.1 — mypy 类型检查**: 
- 创建 `mypy.ini`，配置 `mypy src/` 零错误
- `src/state.py`: `current_trick: list[tuple[int, object]]` → `list[tuple[int, Move]]`，一次性解决 15+ 个派生类型错误
- `src/hand.py`: 添加 `team_of()` 辅助函数处理 `Team | None` 索引
- `src/evaluator.py`: 添加 `_bomb_sort_key()` 辅助函数处理 `bomb_key | None`
- `src/session.py`: 修复 `continue_cb` callable 类型注解
- `src/cards.py`: 修复红十 `straight_rank(ace_low)` bug
- 其余精确 `# type: ignore` 注释（AI/训练模块）

**P1.2 — Claude Code Stop hook**: 
- `.claude/settings.json` 添加 Stop hook，每次对话结束前自动运行 `python -m pytest tests/ -q`

**P1.3 — Git pre-commit hook**: 
- `.git/hooks/pre-commit` — 每次 `git commit` 前自动运行 pytest
- 测试失败则阻止提交，确保坏代码不会进入版本历史
- 与 Stop hook 互补：Stop hook 覆盖 AI 辅助编码，pre-commit 覆盖手动 git 操作

**文件变更**: `pyproject.toml` (新建), `mypy.ini` (新建), `tests/test_moves_properties.py` (新建), `.git/hooks/pre-commit` (新建), `.claude/settings.json` (hook), 16 个源文件类型修复

---

## 2026-05-02

### 1. 单元测试基础设施

**文件**: `tests/test_legal_moves.py`, `tests/TEST_LOG.md`

建立单元测试套件，每次发现 bug 后添加针对性测试。当前包含 6 个测试用例：
- 领牌时所有合法牌型均可出（单张/对子/三条/炸弹）
- 3副牌重复卡牌的 rank 匹配
- 对子去重
- 序列化完整往返测试

详见 [TEST_LOG.md](../tests/TEST_LOG.md)。

---

### 2. 红十在普通对子/三条中按普通10计分

**文件**: `src/moves.py`

**问题**: ♥10 在对子/三条中使用了 `single_rank()` 返回的高位值(16)，导致对10错误地压制对2。

**修复**: `_add_same_rank_combos`、`_add_triple_combos`、`_add_triple_pair_combos`、`_bomb_key` 四个函数改用 `_SINGLE_RANK_MAP[int(combo[0].rank)]` 获取实际 rank 值。

---

### 3. `your_turn` 消息双嵌套修复

**文件**: `src/room_manager.py`

**问题**: `your_turn` WebSocket 消息中 `"cards": ser_move(m)` 将卡片数组嵌套在 dict 内，导致客户端 `m.cards` 拿到 dict 而非数组，Play 按钮始终灰色。

**修复**: 改为 `"cards": [ser_card(c) for c in m.cards]` 直接发送卡片数组。

---

### 4. 3副牌重复卡片导致 frozenset 去重碰撞

**文件**: `src/moves.py`, `web/play.html`

**问题**: 3副牌产生3张完全相同的 Q♠，`frozenset({Q♠₁, Q♠₂, Q♠₃})` 塌缩为 `frozenset({Q♠₁})`，三条的去重 key 与单张相同，三条被丢弃。

**修复**:
- 服务端去重 key 改为 `(m.type, len(m.cards), frozenset(m.cards))`
- 客户端 `findMatchingMove()` 改为按 rank label 排序匹配，忽略花色

---

### 5. 牌局前等待界面

**文件**: `web/play.html`

**功能**: 点击 "Start Game" 后不直接开始牌局，而是进入牌桌界面并在中央显示等待覆盖层：
- 展示所有玩家信息（头像图标、名称、得分）
- "开始新牌局" 按钮 → 发送 `start_game` 开始游戏
- "退出牌局" 按钮 → 返回房间界面

---

### 6. 每局结束后等待界面

**文件**: `src/room_manager.py`, `src/session.py`, `src/web_server.py`, `web/play.html`

**功能**: 每局结束后不再自动进入下一局，而是在牌桌中央显示结果覆盖层：
- 标题：Hand N Result
- 红队/非红队得分对比
- 每位玩家得分，并在得分后标注 大贡 / 末贡
- 玩家列表左对齐显示

**服务端改动**:
- `GameRoom` 新增 `_continue_event` (threading.Event)，提供 `signal_continue()` 和 `_wait_for_continue(timeout=120)`
- `GameSession` 新增 `continue_cb` 回调参数，替代 `time.sleep()` 在每局间等待
- WebSocket 新增 `continue_hand` 消息类型，调用 `room.signal_continue()`

**按钮行为**:
- "开始新牌局" → 发送 `continue_hand`，服务端继续下一局
- "退出牌局" → 发送 `continue_hand`（避免阻塞游戏线程），返回房间界面

**覆盖层复用**: 牌局前等待和每局结束共用 `#pre-game-overlay` div，通过 `showingHandEnd` 状态变量区分上下文。

---

### 7. 出牌区显示重设计

**文件**: `web/play.html`

**问题**: 牌桌中央6个出牌槽位分别显示每位玩家的出牌，多轮出牌后卡片重叠、显示混乱。

**修改**:
- 移除6槽位设计，改为单一中央显示区 `#gtrick-cards` + `#gtrick-name`
- 仅显示最后一手非过牌的出牌（即当前最佳牌型）
- 牌下方显示玩家名称
- 新出牌替代旧出牌，同时更新玩家名

**出牌动画**: 玩家出牌时，卡片从玩家座位区飞入中央显示区（CSS transition + `requestAnimationFrame`）

**过牌提示**: 玩家过牌时，在其座位旁显示 "Pass" 徽章，自动渐隐消失

**清理**:
- 移除 `TRICK_OFF` 常量
- 移除 `.gtrick-slot` CSS
- 简化 `buildGTrickZone()` 和 `positionGTrickZone()`

---

### 8. 红十在4+张普通炸弹中按普通10计分

**文件**: `src/moves.py`

**问题**: 玩家有4张10（含♥10），获得出牌权后无法出4张10的炸弹，只能出3张10（去掉红十）。

**修复**:
- `_is_valid_bomb()`: 移除 `or c.is_red_ten()` 检查，允许红十参与普通炸弹
- `_add_bomb_combos()`: 移除 `and not c.is_red_ten()` 过滤，红十正常计入rank分组

`_bomb_key()` 的rank计算早已正确处理此情况（注释注明 "red-ten plays as 10 in normal bomb context"）。

**测试**: `test_red_ten_in_normal_bomb` — 验证4张10（含♥10）可组成炸弹，rank值为普通10(8)

---

### 9. AI出牌延迟

**文件**: `src/room_manager.py`

**功能**: AI玩家出牌前随机延迟1-3秒，模拟真实玩家思考时间。
在 `SeatController.choose_action()` 的AI路径中增加 `time.sleep(random.uniform(1.0, 3.0))`。

---

### 10. 红十红心计数显示

**文件**: `src/state.py`, `src/hand.py`, `src/gui_renderer.py`, `src/serializers.py`, `web/play.html`

**功能**: 玩家每打出一张红桃10，头像旁显示一个♥标记。打出多张红桃10时显示对应数量的♥（最多3个），保留 "Red" 文字。

**服务端改动**:
- `PlayerStatus.identity_revealed` 从 `bool` 改为 `red_ten_count: int`，新增 `@property identity_revealed` 兼容旧代码
- `revealed_red_ten_count()` 改为对 `red_ten_count` 求和
- `_apply_play()` 每张红桃10都递增计数并调用 `log_identity_reveal()`
- `GameSnapshot` 新增 `red_ten_counts` 字段
- 快照序列化新增 `"rtc"` 键

**客户端**: 根据 `snap.rtc[p]` 渲染 `♥.repeat(n) + ' Red'`

---

### 11. 末贡判定时机修正

**文件**: `web/play.html`

**问题**: 游戏进行中，`finish_order` 中最后一位已完成的玩家被错误标记为"末贡"。末贡应在牌局结束时由服务端 `HandResult.mo_gong` 判定。

**修复**: `renderGame()` 中去除 `pos === snap.fo.length-1 ? ' 末贡'` 分支，游戏进行中仅显示"大贡"和序号，末贡标签仅在牌局结束覆盖层显示。

---

### 12. 末贡未出牌展示

**文件**: `src/state.py`, `src/hand.py`, `src/serializers.py`, `web/play.html`

**功能**: 牌局结束后、显示结果前，在牌桌中央展示末贡玩家手中未打出的牌。分牌（5/10/K）有金色光晕标记，鼠标悬停时扑克牌上提。

**服务端改动**:
- `HandResult` 新增 `mo_gong_hands: dict` 字段，记录每位末贡玩家剩余手牌
- `_compute_result()` 在三个返回路径中均捕获 `mg_hands`
- `ser_result()` 新增 `"mg_hands"` 键

**客户端**: 新增 `#mogong-display` 区域，`showMoGongDisplay()` 函数按玩家分组展示牌面，包含"继续"按钮进入结果界面。

---

### 13. 牌局结束条件与末贡分牌转移

**文件**: `src/hand.py`

**问题 1**: 每局双方总分不等于300（例如红方150 + 非红方110 = 260），计分缺失。

**问题 2**: 一方所有玩家出完牌后，另一方剩余玩家继续对战直到全部出完，牌局才结束。正确规则是：任何一方所有玩家出完牌则牌局结束。

**修复**:
- `_is_hand_over()`: `>= 5` 改为 `>= 6`（安全兜底，主要结束条件由 `_check_terminal()` 控制）
- `_check_terminal()`: 当一方全员完成且对方有部分完成时，返回 `TerminalKind.NORMAL`（不再返回 None）
- `_compute_result()`: 在 NORMAL 路径中，扫描未完成玩家手中剩余的分牌（5/10/K），将其分值转移给对方队伍

---

### 14. 出牌区恢复单中心显示 + 分牌点数累加

**文件**: `web/play.html`

**问题**: 上游代码将出牌区改为多槽位布局（`.trick-play` 显示本轮所有出牌），用户要求恢复之前的单中心显示设计。

**修改**:
- 移除 `.trick-play`、`.trick-play-label`、`.trick-play-cards` CSS，恢复 `#gtrick-cards` + `#gtrick-name` + `#gtrick-score-accum` 样式
- `buildGTrickZone()` 创建三个子元素：出牌卡牌区、玩家名称、分牌累加分值
- `renderGame()` 出牌区逻辑改为：仅显示最后一手非过牌的出牌（当前最佳），被压制时自动更新；本轮结束时清空
- 新增分牌点数累加显示：遍历本轮所有出牌的 `sv` 值求和，显示为"分牌 +N"，与出牌内容同步清除
