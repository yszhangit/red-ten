// game.js — Game play: seat rendering, trick zone, event handling, card selection
import { cardHtml, backCardHtml, sortedHand, RANK_SORT, SUIT_SORT } from '/src/lib/card.js';
import { pLabel } from '/src/lib/seat.js';

// ═══════════════════════════════════════════════════════════════════════════
// Module-level state
// ═══════════════════════════════════════════════════════════════════════════
let mySeat = -1;
let roomSeats = [];
let gameSnap = null;
let myTurnMoves = null;
let lastActor = null;
let showingHandEnd = false;
let pendingResult = null;
let pendingSnap = null;
let evLog = [];
let selectedCardIdxs = new Set();
let _sendWS = null;
let _myRoom = null;
let _showScreen = null;
let debugMode = false;        // debug room: AI hands and LLM reasoning are shown
let thoughts = {};            // seat → latest decision report from an LLM seat
let pinnedThoughtSeat = null; // seat whose thought panel was clicked open

const CCW_ORDER = [0, 5, 4, 3, 2, 1];  // seats in P1→P6 display order

// "P3 老王" when the seat's player gave a name, otherwise "P3". Names are typed
// by players: pass them through esc() wherever HTML is built from strings.
function seatName(p) {
  const name = (roomSeats[p] && roomSeats[p].name) || '';
  return name ? `${pLabel(p)} ${name}` : pLabel(p);
}

// A player sat down, left or changed seats: redraw the names.
export function onSeatsChanged() {
  if (gameSnap) renderGame();
}

// ═══════════════════════════════════════════════════════════════════════════
// Seat positioning constants
// ═══════════════════════════════════════════════════════════════════════════
const FRAC = [
  [0.50, 0.88], [0.02, 0.72], [0.02, 0.35],
  [0.50, 0.09], [0.98, 0.35], [0.98, 0.72],
];
const CCW_SLOT = [0, 5, 4, 3, 2, 1];  // FRAC index for CCW offset 0-5

// ═══════════════════════════════════════════════════════════════════════════
// Sound wrapper (lazy — uses window._sound set by module script)
// ═══════════════════════════════════════════════════════════════════════════
const Sound = {
  card()     { window._sound?.card(); },
  bomb()     { window._sound?.bomb(); },
  yourTurn() { window._sound?.yourTurn(); },
  trickEnd() { window._sound?.trickEnd(); },
  handEnd()  { window._sound?.handEnd(); },
};

// ═══════════════════════════════════════════════════════════════════════════
// Initialization
// ═══════════════════════════════════════════════════════════════════════════
export function init(config) {
  mySeat    = config.mySeat;
  roomSeats = config.roomSeats;
  _sendWS   = config.sendWS;
  _myRoom   = config.roomCode;
  _showScreen = config.showScreen;
  debugMode = !!config.debug;

  // Reset state
  thoughts = {};
  pinnedThoughtSeat = null;
  gameSnap = null;
  myTurnMoves = null;
  lastActor = null;
  showingHandEnd = false;
  pendingResult = null;
  pendingSnap = null;
  evLog = [];
  selectedCardIdxs.clear();

  buildGSeats();
  buildGTrickZone();
  buildDebugUi();
  hideMoGongDisplay();
  hideHandEndOverlay();
  document.getElementById('pre-game-overlay').style.display = 'none';
  document.getElementById('game-over-banner').style.display = 'none';
}

export function setShowScreen(fn) { _showScreen = fn; }
export function getMySeat() { return mySeat; }
export function getMyRoom() { return _myRoom; }

// ═══════════════════════════════════════════════════════════════════════════
// Seat positioning
// ═══════════════════════════════════════════════════════════════════════════
export function positionGSeats() {
  for (let p = 0; p < 6; p++) {
    const seat = document.getElementById('gseat-' + p);
    if (!seat) return;
    const offset = (mySeat - p + 6) % 6;
    const fi = CCW_SLOT[offset];
    seat.style.left = (FRAC[fi][0] * 100) + '%';
    seat.style.top  = (FRAC[fi][1] * 100) + '%';
    seat.classList.remove('anchor-left', 'anchor-right', 'anchor-top');
    if (offset === 4 || offset === 5) seat.classList.add('anchor-left');
    if (offset === 1 || offset === 2) seat.classList.add('anchor-right');
    if (offset === 3)                 seat.classList.add('anchor-top');
  }
}

export function positionGTrickZone() {
  const main = document.getElementById('gmain');
  if (!main) return;
  const W = main.clientWidth, H = main.clientHeight;
  const tz = document.getElementById('gtrick-zone');
  tz.style.left = (W * 0.50) + 'px';
  tz.style.top  = (H * 0.50) + 'px';
}

// ═══════════════════════════════════════════════════════════════════════════
// Pre-game overlay
// ═══════════════════════════════════════════════════════════════════════════
export function populatePreGameSeats() {
  for (let p = 0; p < 6; p++) {
    const info = roomSeats[p] || { seat: p, mode: 'ai' };
    const isHuman = info.mode === 'human';
    const isMe = p === mySeat;
    document.getElementById('gsname-' + p).textContent = seatName(p) + (isMe ? ' (You)' : '');
    document.getElementById('gsbadge-' + p).textContent = isHuman ? '\u{1F464}' : '\u{1F916}';
    document.getElementById('gsc-hand-' + p).textContent = 'hand 0';
    document.getElementById('gsc-cum-' + p).textContent = 'total 0';
  }
}

export function showPreGameOverlay() {
  document.getElementById('pgo-title').textContent = 'Red Ten Poker';
  document.getElementById('pgo-teams').innerHTML = '';
  document.getElementById('pgo-da-gong').textContent = '';
  document.getElementById('pgo-mo-gong').textContent = '';

  let html = '';
  for (const p of CCW_ORDER) {
    const info = roomSeats[p] || { seat: p, mode: 'ai' };
    const isHuman = info.mode === 'human';
    const isMe = p === mySeat;
    const icon = isHuman ? '\u{1F464}' : '\u{1F916}';
    const name = esc(seatName(p)) + (isMe ? ' (You)' : '');
    html += '<div style="margin:2px 0;">' + icon + ' ' + name + ' — Score: 0</div>';
  }
  document.getElementById('pgo-players').innerHTML = html;
  document.getElementById('pre-game-overlay').style.display = 'flex';

  // Wire up buttons
  document.getElementById('pgo-start-btn').onclick = startNewHand;
  document.getElementById('pgo-exit-btn').onclick = exitToRoom;
}

// ═══════════════════════════════════════════════════════════════════════════
// Start / exit
// ═══════════════════════════════════════════════════════════════════════════
export function startNewHand() {
  document.getElementById('pre-game-overlay').style.display = 'none';
  hideMoGongDisplay();
  if (showingHandEnd) {
    showingHandEnd = false;
    if (_sendWS) _sendWS({ type: 'continue_hand' });
  } else {
    if (_sendWS) _sendWS({ type: 'start_game', hands: 3 });
  }
}

export function exitToRoom() {
  document.getElementById('pre-game-overlay').style.display = 'none';
  hideMoGongDisplay();
  if (showingHandEnd) {
    showingHandEnd = false;
    if (_sendWS) _sendWS({ type: 'continue_hand' });
  }
  if (_showScreen) _showScreen('room-screen');
}

// ═══════════════════════════════════════════════════════════════════════════
// Build seat DOM
// ═══════════════════════════════════════════════════════════════════════════
function buildGSeats() {
  const el = document.getElementById('gseats');
  el.innerHTML = '';
  for (let p = 0; p < 6; p++) {
    const seat = document.createElement('div');
    seat.className = 'gseat' + (p === mySeat ? ' mine' : '');
    seat.id = 'gseat-' + p;
    seat.innerHTML = `
      <div class="gseat-name">
        <span class="sname" id="gsname-${p}">P${p}${p===mySeat?' (You)':''}</span>
        <span>
          <span class="thought-btn" id="gsthought-${p}" title="AI 的思考">💭</span>
          <span id="gsbadge-${p}"></span>
        </span>
      </div>
      <div class="gseat-score">
        <span id="gsc-hand-${p}">hand 0</span>
        <span id="gsc-cum-${p}">total 0</span>
      </div>
      <div class="gseat-cards" id="ghand-${p}"></div>
      <div class="gseat-count" id="gcnt-${p}"></div>`;
    el.appendChild(seat);

    const tb = seat.querySelector('.thought-btn');
    tb.addEventListener('mouseenter', () => showThoughtPanel(p));
    tb.addEventListener('mouseleave', () => {
      if (pinnedThoughtSeat === null) hideThoughtPanel();
      else showThoughtPanel(pinnedThoughtSeat);
    });
    tb.addEventListener('click', () => {
      pinnedThoughtSeat = pinnedThoughtSeat === p ? null : p;
      if (pinnedThoughtSeat === null) hideThoughtPanel(); else showThoughtPanel(p);
    });
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Debug mode: revealed AI hands and the LLM thought panel
// ═══════════════════════════════════════════════════════════════════════════
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, ch =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

function buildDebugUi() {
  const main = document.getElementById('gmain');
  let tag = document.getElementById('ai-debug-tag');
  if (!tag) {
    tag = document.createElement('div');
    tag.id = 'ai-debug-tag';
    tag.textContent = '调试模式 · AI 手牌可见 · 点 💭 看模型思考';
    main.appendChild(tag);
  }
  tag.style.display = debugMode ? 'block' : 'none';

  let panel = document.getElementById('thought-panel');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'thought-panel';
    main.appendChild(panel);
  }
  panel.style.display = 'none';
}

// A revealed hand as one chip per rank, e.g. "K×2"; ♥10 gets its own chip.
function miniHandHtml(hand) {
  const groups = new Map();
  for (const c of hand) {
    const key = c.rt ? '♥10' : c.rl;
    const g = groups.get(key) || { n: 0, c };
    g.n += 1;
    groups.set(key, g);
  }
  const order = k => k === '♥10' ? 10.5 : (RANK_SORT[k] ?? 0);
  return [...groups.entries()]
    .sort((a, b) => order(a[0]) - order(b[0]))
    .map(([k, g]) => {
      let cls = 'mh-chip';
      if (g.c.rt) cls += ' redten';
      else if (g.c.bj || g.c.sj) cls += ' joker';
      else if (g.c.sv > 0) cls += ' scoring';
      return `<span class="${cls}">${esc(k)}${g.n > 1 ? `<small>×${g.n}</small>` : ''}</span>`;
    }).join('');
}

function thoughtPanelHtml(seat, t) {
  const secs = (t.latency_ms / 1000).toFixed(1);
  let h = `<div class="tp-head"><b>${pLabel(seat)}</b> · ${esc(t.model)} · ${secs}s
    <span class="tp-close" id="tp-close">×</span></div>`;
  if (t.fallback) {
    h += `<div class="tp-warn">模型没有给出有效回答，这一手由规则 AI 代出。${esc(t.error)}</div>`;
  }
  const num = t.choice ? `（第 ${t.choice} 项，共 ${t.n_options} 项）` : '';
  h += `<div class="tp-row"><span class="tp-label">出牌</span> ${esc(t.move)} ${num}</div>`;
  if (t.reason) {
    h += `<div class="tp-row"><span class="tp-label">模型的说明</span> ${esc(t.reason)}</div>`;
  }
  if (t.thinking) {
    h += `<div class="tp-label">思考过程（thinking tokens）</div><pre>${esc(t.thinking)}</pre>`;
  }
  const opts = (t.options || []).map((o, i) =>
    `<li${i + 1 === t.choice ? ' class="chosen"' : ''}>${esc(o)}</li>`).join('');
  h += `<details><summary>可选出牌（${t.n_options}）</summary><ol>${opts}</ol></details>`;
  h += `<details><summary>模型看到的信息</summary><pre>${esc(t.view)}</pre></details>`;
  const raw = (t.replies || []).map(esc).join('\n---\n');
  if (raw) h += `<details><summary>模型的原始回答</summary><pre>${raw}</pre></details>`;
  return h;
}

function showThoughtPanel(seat) {
  const t = thoughts[seat];
  const panel = document.getElementById('thought-panel');
  if (!t || !panel) return;
  // Keep any opened <details> sections open while the panel refreshes.
  const open = [...panel.querySelectorAll('details')].map(d => d.open);
  panel.innerHTML = thoughtPanelHtml(seat, t);
  if (panel.dataset.seat === String(seat)) {
    panel.querySelectorAll('details').forEach((d, i) => { d.open = !!open[i]; });
  }
  panel.dataset.seat = String(seat);
  panel.style.display = 'block';
  document.getElementById('tp-close').addEventListener('click', () => {
    pinnedThoughtSeat = null;
    hideThoughtPanel();
  });
}

function hideThoughtPanel() {
  const panel = document.getElementById('thought-panel');
  if (panel) panel.style.display = 'none';
}

export function handleThought(report) {
  thoughts[report.seat] = report;
  const btn = document.getElementById('gsthought-' + report.seat);
  if (btn) btn.style.display = 'inline';
  if (pinnedThoughtSeat === report.seat) showThoughtPanel(report.seat);
}

function buildGTrickZone() {
  document.getElementById('gtrick-zone').innerHTML = '';
}

// ═══════════════════════════════════════════════════════════════════════════
// Main render
// ═══════════════════════════════════════════════════════════════════════════
export function renderGame() {
  if (!gameSnap) return;
  const snap = gameSnap;

  // Seats
  for (let p = 0; p < 6; p++) {
    const seat = document.getElementById('gseat-' + p);
    if (!seat) continue;

    const ident = snap.ids[p];
    const isHuman = roomSeats[p] && roomSeats[p].mode === 'human';

    // Border class
    const offset = (mySeat - p + 6) % 6;
    const anchor = (offset===4||offset===5) ? ' anchor-left' : (offset===1||offset===2) ? ' anchor-right' : (offset===3) ? ' anchor-top' : '';
    let cls = 'gseat' + anchor;
    if (p === mySeat) cls += ' mine';
    else if (ident === 'Red') cls += ' red';
    else if (ident === 'Non-Red') cls += ' nonred';
    if (p === mySeat && myTurnMoves) cls += ' myturn';
    else if (p === lastActor && p !== mySeat) cls += ' acting';
    seat.className = cls;

    // Name
    const nameEl = document.getElementById('gsname-' + p);
    const badgeEl = document.getElementById('gsbadge-' + p);
    let name = p === mySeat ? `${seatName(p)} (You)` : seatName(p);
    if (snap.fin[p]) {
      const pos = snap.fo.indexOf(p);
      name += pos === 0 ? ' 大贡' : ` #${pos+1}`;
    }
    nameEl.textContent = name;
    nameEl.style.color = snap.fin[p] ? '#4bc84b' : ident==='Red' ? '#dc4848' : ident==='Non-Red' ? '#589bdc' : '#e1e4d7';

    if (ident === 'Red') {
      const rtc = (snap.rtc && typeof snap.rtc[p] === 'number') ? snap.rtc[p] : 0;
      let hearts = '';
      if (rtc >= 1) hearts += '♥';
      if (rtc >= 2) hearts += '♥';
      if (rtc >= 3) hearts += '♥';
      badgeEl.textContent = hearts + (hearts ? ' ' : '') + 'Red';
      badgeEl.style.color = '#dc4848';
    } else if (ident === 'Non-Red') {
      badgeEl.textContent = '○';
      badgeEl.style.color = '#589bdc';
    } else {
      badgeEl.textContent = isHuman ? '\u{1F464}' : '';
    }

    // Scores
    document.getElementById('gsc-hand-' + p).textContent = 'hand ' + snap.ts[p];
    document.getElementById('gsc-cum-'  + p).textContent = 'total ' + snap.cs[p];

    // Cards — your own face-up and interactive; others show face-down backs
    const cardsEl = document.getElementById('ghand-' + p);
    const hand = snap.hands[p];
    if (p === mySeat) {
      const sorted = sortedHand(hand);
      let ml = -18;
      if (sorted.length > 1) {
        const vis = Math.max(5, (280 - 36) / (sorted.length - 1));
        ml = Math.min(-6, -(36 - vis));
      }
      cardsEl.innerHTML = sorted.map(({c, i}) =>
        cardHtml(c, { selectable: !!myTurnMoves, selected: selectedCardIdxs.has(i), idx: i })
      ).join('');
      cardsEl.style.setProperty('--cml', ml + 'px');
      if (myTurnMoves) {
        cardsEl.querySelectorAll('.card').forEach(el => {
          el.addEventListener('click', () => toggleCard(parseInt(el.dataset.idx)));
        });
      }
    } else if (hand.length) {
      // The server sent this seat's cards (debug mode, or humans have finished).
      cardsEl.classList.add('revealed');
      cardsEl.innerHTML = miniHandHtml(hand);
    } else {
      cardsEl.classList.remove('revealed');
      const count = snap.hcnt ? snap.hcnt[p] : hand.length;
      const shown = Math.min(count, 10);
      cardsEl.innerHTML = Array(shown).fill(backCardHtml()).join('');
    }
    const trueCount = snap.hcnt ? snap.hcnt[p] : snap.hands[p].length;
    document.getElementById('gcnt-' + p).textContent = trueCount + ' cards';
  }

  // Trick zone
  const tzEl = document.getElementById('gtrick-zone');
  tzEl.innerHTML = '';
  const bestIdx = snap.tp.reduce((bi, [, m], i) => m && !m.pass ? i : bi, -1);
  let gtrickCardsAssigned = false;
  snap.tp.forEach(([p, move], i) => {
    const isPass = !move || move.pass;
    const isBest = i === bestIdx;
    const playDiv = document.createElement('div');
    playDiv.className = 'trick-play' + (isBest ? ' best' : '') + (isPass ? ' pass' : '');
    const label = document.createElement('div');
    label.className = 'trick-play-label';
    label.textContent = pLabel(p) + (isPass ? ' pass' : '');
    playDiv.appendChild(label);
    if (!isPass) {
      const cardsDiv = document.createElement('div');
      cardsDiv.className = 'trick-play-cards';
      if (isBest && !gtrickCardsAssigned) {
        cardsDiv.id = 'gtrick-cards';
        gtrickCardsAssigned = true;
      }
      cardsDiv.innerHTML = move.cards.map(c => cardHtml(c, {})).join('');
      if (p === snap.tw) {
        cardsDiv.querySelectorAll('.card').forEach(c => c.style.outline = '2px solid #ffaf00');
      }
      playDiv.appendChild(cardsDiv);
    }
    tzEl.appendChild(playDiv);
  });

  // Score panel
  const scoreEl = document.getElementById('g-score-table');
  let sh = '<div class="row" style="color:#829672;font-size:10px"><span class="pname">Player</span><span class="pts">Hand</span><span class="pts">Total</span></div>';
  for (const p of CCW_ORDER) {
    const ident = snap.ids[p];
    const col = ident === 'Red' ? '#dc4848' : ident === 'Non-Red' ? '#589bdc' : '#e1e4d7';
    const fin = snap.fin[p] ? ' ✓' : '';
    const you = p === mySeat ? ' (You)' : '';
    sh += `<div class="row" style="color:${col}">
      <span class="pname">${esc(seatName(p))}${fin}${you}</span>
      <span class="pts">${snap.ts[p]}</span>
      <span class="pts">${snap.cs[p]}</span>
    </div>`;
  }
  scoreEl.innerHTML = sh;

  // Event log
  const logEl = document.getElementById('gevlog');
  logEl.innerHTML = evLog.map((d, i) =>
    `<div class="ev${i===evLog.length-1?' cur':''}">${d.slice(0,50)}</div>`
  ).join('');

  // Action bar
  renderMyHandStrip();
}

// ═══════════════════════════════════════════════════════════════════════════
// Animations
// ═══════════════════════════════════════════════════════════════════════════
export function animateCardPlay(fromSeat, cards) {
  const main = document.getElementById('gmain');
  const seatEl = document.getElementById('gseat-' + fromSeat);
  const targetEl = document.getElementById('gtrick-cards');
  if (!main || !seatEl || !targetEl) return;

  const mainRect = main.getBoundingClientRect();
  const seatRect = seatEl.getBoundingClientRect();
  const targetRect = targetEl.getBoundingClientRect();

  const sx = seatRect.left + seatRect.width / 2 - mainRect.left;
  const sy = seatRect.top + seatRect.height / 2 - mainRect.top;
  const tx = targetRect.left + targetRect.width / 2 - mainRect.left;
  const ty = targetRect.top + targetRect.height / 2 - mainRect.top;

  cards.forEach((card, i) => {
    const fly = document.createElement('div');
    fly.className = 'card flying-card';
    fly.innerHTML = cardHtml(card, {});
    fly.style.left = sx + 'px';
    fly.style.top  = sy + 'px';
    main.appendChild(fly);

    requestAnimationFrame(() => {
      fly.style.left = (tx + i * 12) + 'px';
      fly.style.top  = ty + 'px';
    });

    let _flyDone = false;
    const removeFly = () => { if (!_flyDone) { _flyDone = true; fly.remove(); } };
    fly.addEventListener('transitionend', removeFly);
    setTimeout(removeFly, 700);
  });
}

export function shakeScreen() {
  document.body.classList.remove('shake');
  void document.body.offsetWidth; // force reflow
  document.body.classList.add('shake');
  document.body.addEventListener('animationend', () => document.body.classList.remove('shake'), { once: true });
}

export function triggerBombAnimation(container) {
  // Blast circle
  const blast = document.createElement('div');
  blast.className = 'bomb-blast';
  container.appendChild(blast);

  // 8 particle fragments
  for (let i = 0; i < 8; i++) {
    const p = document.createElement('div');
    p.className = 'bomb-particle';
    const angle = (Math.PI * 2 * i) / 8;
    const dist = 40 + Math.random() * 30;
    p.style.setProperty('--px', `${Math.cos(angle) * dist}px`);
    p.style.setProperty('--py', `${Math.sin(angle) * dist}px`);
    container.appendChild(p);
  }

  // White flash overlay
  const flash = document.createElement('div');
  flash.className = 'bomb-overlay';
  container.appendChild(flash);

  // Auto-cleanup after 500ms
  setTimeout(() => {
    blast.remove();
    flash.remove();
    container.querySelectorAll('.bomb-particle').forEach(el => el.remove());
  }, 500);
}

export function showPassBadge(seat) {
  const seatEl = document.getElementById('gseat-' + seat);
  if (!seatEl) return;
  const old = seatEl.querySelector('.pass-badge');
  if (old) old.remove();

  const badge = document.createElement('div');
  badge.className = 'pass-badge';
  badge.textContent = 'Pass';
  seatEl.appendChild(badge);
  badge.addEventListener('animationend', () => badge.remove());
}

// ═══════════════════════════════════════════════════════════════════════════
// Card selection
// ═══════════════════════════════════════════════════════════════════════════
function cardKey(c) { return c.rl + '|' + c.s; }

function findMatchingMove() {
  if (!myTurnMoves || !gameSnap) return null;
  const hand = gameSnap.hands[mySeat] || [];
  const selCards = [...selectedCardIdxs].map(i => hand[i]).filter(Boolean);
  const selRanks = selCards.map(c => c.rl).sort().join(',');

  for (const m of myTurnMoves) {
    if (m.is_pass) continue;
    const mCards = m.cards || [];
    if (mCards.length !== selCards.length) continue;
    const mRanks = mCards.map(c => c.rl).sort().join(',');
    if (mRanks === selRanks) return m;
  }
  return null;
}

function updatePlayBtnState() {
  const btn = document.getElementById('play-btn');
  const match = findMatchingMove();
  btn.disabled = !match;
  btn.textContent = match ? `Play (${match.desc})` : 'Play';
}

function toggleCard(idx) {
  if (selectedCardIdxs.has(idx)) selectedCardIdxs.delete(idx);
  else selectedCardIdxs.add(idx);
  renderGame();
  updatePlayBtnState();
}

function renderMyHandStrip() {
  const msg = document.getElementById('turn-msg');
  if (!myTurnMoves) {
    msg.textContent = 'Waiting for your turn…';
    msg.style.color = '#829672';
    document.getElementById('pass-btn').style.display = 'none';
    document.getElementById('play-btn').disabled = true;
    document.getElementById('play-btn').textContent = 'Play';
  } else {
    msg.textContent = 'Your turn — select cards';
    msg.style.color = '#ffe42d';
    const hasPass = myTurnMoves.some(m => m.is_pass);
    document.getElementById('pass-btn').style.display = hasPass ? 'inline-block' : 'none';
    updatePlayBtnState();
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Turn management
// ═══════════════════════════════════════════════════════════════════════════
export function showMovePicker(moves) {
  myTurnMoves = moves;
  renderGame();
  renderMyHandStrip();
}

export function hideMovePicker() {
  selectedCardIdxs.clear();
  myTurnMoves = null;
  renderMyHandStrip();
}

export function playSelected() {
  const match = findMatchingMove();
  if (!match) return;
  sendMove(match.idx);
}

export function playPass() {
  const passMv = myTurnMoves && myTurnMoves.find(m => m.is_pass);
  if (!passMv) return;
  sendMove(passMv.idx);
}

function sendMove(idx) {
  if (_sendWS) _sendWS({ type: 'move', move_idx: idx });
  hideMovePicker();
}

// ═══════════════════════════════════════════════════════════════════════════
// Game event handling
// ═══════════════════════════════════════════════════════════════════════════
export function handleEvent(ev) {
  if (showingHandEnd) hideHandEndOverlay();
  gameSnap = ev.snap;
  if (ev.player != null) lastActor = ev.player;
  evLog.push(ev.desc.replace(/\bP([0-5])\b/g, (_, n) => pLabel(+n)));
  if (evLog.length > 60) evLog.shift();

  // Trigger card fly / pass animations
  if ((ev.kind === 'lead' || ev.kind === 'action') && ev.move && ev.player != null) {
    if (ev.move.pass) {
      showPassBadge(ev.player);
    } else if (ev.move.cards && ev.move.cards.length) {
      animateCardPlay(ev.player, ev.move.cards);
      if (ev.move.bomb) { Sound.bomb(); shakeScreen(); triggerBombAnimation(document.getElementById('gmain')); } else Sound.card();
    }
  }
  if (ev.kind === 'trick_end') Sound.trickEnd();

  // AI personality speech for pass actions
  if (ev.player != null) {
    if ((ev.kind === 'lead' || ev.kind === 'action') && ev.move?.pass) {
      const gender = (ev.player % 2 === 1) ? 'female' : 'male';
      window._speech?.sayPass(gender);
    }
  }

  renderGame();

  if (ev.kind === 'hand_end' && ev.result) {
    Sound.handEnd();
    pendingResult = ev.result;
    pendingSnap = ev.snap;
    showMoGongDisplay(ev.result);
  }
}

export function handleYourTurn(moves) {
  Sound.yourTurn();
  showMovePicker(moves);
}

export function handleGameOver(replay) {
  myTurnMoves = null;
  hideMovePicker();
  if (replay) {
    try { localStorage.setItem('pendingReplay', JSON.stringify(replay)); } catch (_) {}
  }
  showGameOverBanner();
}

export function handleCatchUp(events, lastSnap) {
  if (events.length && lastSnap) {
    gameSnap = lastSnap;
    renderGame();
    // If the last event was hand_end, show the result overlay
    const lastEv = events[events.length - 1];
    if (lastEv.kind === 'hand_end' && lastEv.result) {
      pendingResult = lastEv.result;
      pendingSnap = lastEv.snap;
      showHandEndOverlay(lastEv.result, lastEv.snap);
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Mo Gong display
// ═══════════════════════════════════════════════════════════════════════════
function showMoGongDisplay(result) {
  const mg = result.mo_gong || [];
  const hands = result.mg_hands || {};

  const hasCards = mg.some(p => hands[String(p)] && hands[String(p)].length > 0);
  if (!hasCards) {
    showHandEndOverlay(result, pendingSnap);
    return;
  }

  const display = document.getElementById('mogong-display');

  let html = '<div class="mg-title">Hand Over — 末贡 Remaining Cards</div>';

  mg.forEach(p => {
    const cards = hands[String(p)] || [];
    if (!cards.length) return;
    const sorted = [...cards].sort((a, b) => {
      const rd = (RANK_SORT[a.rl] ?? 0) - (RANK_SORT[b.rl] ?? 0);
      if (rd !== 0) return rd;
      return (SUIT_SORT[a.s] ?? 0) - (SUIT_SORT[b.s] ?? 0);
    });

    const scoringCount = sorted.filter(c => c.sv > 0).length;
    html += '<div class="mg-player-name">' + pLabel(p) + ' (' + sorted.length + ' cards';
    if (scoringCount > 0) html += ', <span style="color:#ffdc3c">' + scoringCount + ' scoring</span>';
    html += ')</div>';
    html += '<div class="mg-cards">' + sorted.map(c => cardHtml(c, {})).join('') + '</div>';
  });

  html += '<button id="mogong-continue-btn">继续</button>';

  display.innerHTML = html;
  display.style.display = 'flex';
  positionMoGongDisplay();

  // Wire up continue button
  document.getElementById('mogong-continue-btn').addEventListener('click', onMoGongContinue);
}

function hideMoGongDisplay() {
  document.getElementById('mogong-display').style.display = 'none';
}

function onMoGongContinue() {
  hideMoGongDisplay();
  if (pendingResult && pendingSnap) {
    showHandEndOverlay(pendingResult, pendingSnap);
    pendingResult = null;
    pendingSnap = null;
  }
}

function positionMoGongDisplay() {
  const main = document.getElementById('gmain');
  if (!main) return;
  const W = main.clientWidth, H = main.clientHeight;
  const tz = document.getElementById('mogong-display');
  tz.style.left = (W * 0.50) + 'px';
  tz.style.top  = (H * 0.50) + 'px';
}

// ═══════════════════════════════════════════════════════════════════════════
// Hand end overlay
// ═══════════════════════════════════════════════════════════════════════════
function showHandEndOverlay(result, snap) {
  showingHandEnd = true;

  const hn = snap ? snap.hn : '?';
  document.getElementById('pgo-title').textContent = 'Hand ' + hn + ' Result';

  const tp = result.team_pts || {};
  document.getElementById('pgo-teams').innerHTML =
    '<span style="color:#dc4848;">Red: ' + (tp.RED || 0) + ' pts</span>' +
    ' &nbsp;vs&nbsp; ' +
    '<span style="color:#589bdc;">Non-Red: ' + (tp.NON_RED || 0) + ' pts</span>';

  const scores = result.scores || [];
  const dg = result.da_gong;
  const mg = result.mo_gong || [];

  let html = '';
  for (const p of CCW_ORDER) {
    const info = roomSeats[p] || { seat: p, mode: 'ai' };
    const isHuman = info.mode === 'human';
    const isMe = p === mySeat;
    const isRed = result.red && result.red.includes(p);
    const icon = isHuman ? '\u{1F464}' : '\u{1F916}';
    const name = esc(seatName(p)) + (isMe ? ' (You)' : '');
    const col = isRed ? '#dc4848' : '#589bdc';

    let tag = '';
    if (dg === p) tag = ' ← 大贡';
    else if (mg.includes(p)) tag = ' ← 末贡';

    html += '<div style="margin:2px 0;color:' + col + '">' + icon + ' ' + name + ': ' + (scores[p] || 0) + ' pts' + tag + '</div>';
  }
  document.getElementById('pgo-players').innerHTML = html;

  document.getElementById('pgo-da-gong').textContent =
    dg !== null && dg !== undefined ? '大贡: ' + pLabel(dg) : '';

  if (mg && mg.length) {
    document.getElementById('pgo-mo-gong').textContent = '末贡: ' + mg.map(p => pLabel(p)).join(', ');
  } else {
    document.getElementById('pgo-mo-gong').textContent = '';
  }

  document.getElementById('pre-game-overlay').style.display = 'flex';
  // Re-wire buttons
  document.getElementById('pgo-start-btn').onclick = startNewHand;
  document.getElementById('pgo-exit-btn').onclick = exitToRoom;
}

function hideHandEndOverlay() {
  showingHandEnd = false;
  hideMoGongDisplay();
  document.getElementById('pre-game-overlay').style.display = 'none';
}

// ═══════════════════════════════════════════════════════════════════════════
// Game over banner
// ═══════════════════════════════════════════════════════════════════════════
export function showGameOverBanner() {
  const banner = document.getElementById('game-over-banner');
  banner.style.display = 'flex';
}

// ═══════════════════════════════════════════════════════════════════════════
// Debug flag (dev tool)
// ═══════════════════════════════════════════════════════════════════════════
export async function flagDebugMoment() {
  if (!_myRoom) return;
  const btn = document.getElementById('debug-btn');
  btn.disabled = true; btn.textContent = '⏳';
  try {
    const r = await fetch(`/api/debug-flag/${_myRoom}`, { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    const msg = `room ${_myRoom}, flag ${d.flag_id}, check debug_logs/${d.log_file}`;
    // The clipboard is only available over HTTPS or on localhost.
    let copied = false;
    try { await navigator.clipboard.writeText(msg); copied = true; } catch (_) {}
    showDebugToast(copied ? `Flag ${d.flag_id} inserted — copied to clipboard` : msg);
  } catch (e) {
    showDebugToast('Flag failed: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = '\u{1F6A9} Flag';
  }
}

function showDebugToast(msg) {
  const t = document.getElementById('debug-toast');
  t.textContent = msg; t.style.display = 'block';
  clearTimeout(t._tid);
  t._tid = setTimeout(() => { t.style.display = 'none'; }, 6000);
}

// ═══════════════════════════════════════════════════════════════════════════
// Resize handler
// ═══════════════════════════════════════════════════════════════════════════
export function onResize() {
  positionGSeats();
  positionGTrickZone();
  if (document.getElementById('mogong-display').style.display !== 'none') {
    positionMoGongDisplay();
  }
  if (gameSnap) renderGame();
}

// ═══════════════════════════════════════════════════════════════════════════
// Re-export game event list for catch_up
// ═══════════════════════════════════════════════════════════════════════════
export function getEvLog() { return evLog; }
export function setEvLog(log) { evLog = log; }
export function clearEvLog() { evLog = []; }
