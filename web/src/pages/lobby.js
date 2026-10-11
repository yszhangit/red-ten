// lobby.js — Lobby screen: room list, create, join
import { get } from '/src/lib/api.js';

let _onJoinRoom = null;
let _refreshTimer = null;

const tierName = { rule: 'Rule AI', search: 'Search AI', ml: 'ML AI', llm: 'LLM AI' };

export function initLobby(onJoinRoom) {
  _onJoinRoom = onJoinRoom;

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

async function refreshRoomList() {
  try {
    const list = await get('/api/rooms');
    const el = document.getElementById('room-list');
    if (!list.length) {
      el.innerHTML = '<div class="no-rooms">No open rooms yet — create one above.</div>';
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
  const r = await fetch(`/api/room/join/${code}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ seat: preferSeat }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || r.statusText);
  if (_onJoinRoom) {
    _onJoinRoom({
      roomCode: d.room_code,
      seat: d.seat,
      seats: d.seats,
    });
  }
}
