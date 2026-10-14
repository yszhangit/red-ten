// lobby.js — Lobby screen: room list, create, join
import { get } from '/src/lib/api.js';
import { joinRoom, playerName, setPlayerName } from '/src/lib/session.js';

let _onJoinRoom = null;
let _refreshTimer = null;

const tierName = { rule: 'Rule AI', search: 'Search AI', ml: 'ML AI', llm: 'LLM AI' };

export function initLobby(onJoinRoom) {
  _onJoinRoom = onJoinRoom;

  // Display name, remembered for next time
  const nameEl = document.getElementById('player-name');
  nameEl.value = playerName();
  nameEl.addEventListener('input', () => setPlayerName(nameEl.value.trim()));

  applyServerConfig();

  // Wire up create button
  document.getElementById('create-btn')?.addEventListener('click', createGame);

  // Wire up join button
  document.getElementById('join-btn')?.addEventListener('click', joinGame);

  // Auto-uppercase join code input
  document.getElementById('join-code')?.addEventListener('input', (e) => {
    e.target.value = e.target.value.toUpperCase();
  });

  // Wire up join input Enter key
  document.getElementById('join-code')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') joinGame();
  });

  // Initial load
  refreshRoomList();
  _refreshTimer = setInterval(refreshRoomList, 3000);
}

export function activateLobby() {
  refreshRoomList();
  _refreshTimer = setInterval(refreshRoomList, 3000);
}

export function deactivateLobby() {
  clearInterval(_refreshTimer);
  _refreshTimer = null;
}

// Offer only what this server offers: a hosted server (src/hosting.py) has
// the engine AIs and nothing but the game.
async function applyServerConfig() {
  let cfg;
  try { cfg = await get('/api/lobby'); } catch (_) { return; }
  if (cfg.tiers) {
    const sel = document.getElementById('ai-tier');
    [...sel.options].forEach(o => { if (!cfg.tiers.includes(o.value)) o.remove(); });
    if (!cfg.tiers.includes(sel.value)) sel.value = cfg.tiers[cfg.tiers.length - 1];
  }
  if (!cfg.debug) document.getElementById('ai-debug-field').style.display = 'none';
  if (cfg.hosted) {
    document.getElementById('lobby-footer').style.display = 'none';
    document.getElementById('review-link').style.display = 'none';
  }
}

async function refreshRoomList() {
  try {
    const r = await fetch('/api/rooms');
    if (r.status === 401) { window.location.href = '/login'; return; }
    if (!r.ok) return;
    const list = await r.json();
    const el = document.getElementById('room-list');
    if (!list.length) {
      el.innerHTML = '<div class="no-rooms">还没有牌桌，点上面的按钮开一桌<br>No open tables yet — create one above.</div>';
      return;
    }
    el.innerHTML = list.map(room => {
      const players = room.human_seats ? `${room.human_seats}/6 human` : 'AI only';
      const status  = room.status === 'running' ? 'In game' : 'Waiting';
      const tier    = tierName[room.ai_tier] || room.ai_tier;
      return `<div class="room-row">
        <span class="room-code-tag">${room.code}</span>
        <span class="room-info">${status} · ${players} · ${tier}${room.debug ? ' · debug' : ''}</span>
        <button class="room-join-btn" data-code="${room.code}">Join →</button>
      </div>`;
    }).join('');

    // Wire up room join buttons
    el.querySelectorAll('.room-join-btn').forEach(btn => {
      btn.addEventListener('click', () => quickJoin(btn.dataset.code));
    });
  } catch (_) { /* ignore fetch errors */ }
}

async function quickJoin(code) {
  document.getElementById('lobby-err').textContent = '';
  try {
    await enterRoom(code, -1);
  } catch (e) {
    document.getElementById('lobby-err').textContent = 'Error: ' + e.message;
  }
}

async function createGame() {
  const tier = document.getElementById('ai-tier').value;
  const err  = document.getElementById('lobby-err');
  err.textContent = '';
  try {
    const r = await fetch('/api/room/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ai_tier: tier,
                             debug: document.getElementById('ai-debug').checked }),
    });
    if (r.status === 401) { window.location.href = '/login'; return; }
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    await enterRoom(d.room_code, -1);
  } catch (e) {
    err.textContent = 'Error: ' + e.message;
  }
}

async function joinGame() {
  const code = document.getElementById('join-code').value.trim().toUpperCase();
  const err  = document.getElementById('lobby-err');
  err.textContent = '';
  if (code.length !== 4) { err.textContent = 'Enter a 4-letter code.'; return; }
  try {
    await enterRoom(code, -1);
  } catch (e) {
    err.textContent = 'Error: ' + e.message;
  }
}

async function enterRoom(code, preferSeat) {
  const joined = await joinRoom(code, preferSeat);
  if (_onJoinRoom) _onJoinRoom(joined);
}

export function showLobbyError(msg) {
  document.getElementById('lobby-err').textContent = msg;
}
