// play.js — Orchestrator: wires lobby, room, game screens + WebSocket
import { initLobby, activateLobby, deactivateLobby } from '/src/pages/lobby.js';
import { initRoom, updateSeats, setRoomCode, setStatus } from '/src/pages/room.js';
import * as game from '/src/pages/game.js';

// ═══════════════════════════════════════════════════════════════════════════
// Global state
// ═══════════════════════════════════════════════════════════════════════════
let myRoom = null;
let mySeat = -1;
let roomSeats = [];
let roomDebug = false;   // debug room: AI hands and LLM reasoning are shown

// ═══════════════════════════════════════════════════════════════════════════
// WebSocket
// ═══════════════════════════════════════════════════════════════════════════
let ws = null;

function connectWS() {
  if (ws && ws.readyState !== WebSocket.CLOSED) return;
  if (!myRoom) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws/${myRoom}/${mySeat}`);

  ws.onopen = () => {
    badge('ok');
  };

  ws.onmessage = e => {
    const msg = JSON.parse(e.data);
    handleMsg(msg);
  };

  ws.onclose = () => {
    badge('off');
    if (myRoom !== null) {
      setTimeout(() => {
        if (!ws || ws.readyState === WebSocket.CLOSED) connectWS();
      }, 2000);
    }
  };

  ws.onerror = () => badge('off');
}

function sendWS(data) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(data));
  }
}

function closeWS() {
  myRoom = null; // prevent reconnect
  if (ws) { ws.close(); ws = null; }
}

// ═══════════════════════════════════════════════════════════════════════════
// Connection badge
// ═══════════════════════════════════════════════════════════════════════════
function badge(state) {
  const el = document.getElementById('conn-badge');
  el.className = state === 'ok' ? 'ok' : 'off';
  el.textContent = state === 'ok' ? '⬤ connected' : '⬤ disconnected';
  el.style.display = 'block';
}

// ═══════════════════════════════════════════════════════════════════════════
// Screen management
// ═══════════════════════════════════════════════════════════════════════════
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  const el = document.getElementById(id);
  if (el) el.classList.add('active');

  // Debug button only visible during game
  document.getElementById('debug-btn').style.display =
    (id === 'game-screen' && myRoom) ? 'block' : 'none';

  // Lobby refresh timer management
  if (id === 'lobby-screen') {
    activateLobby();
  } else {
    deactivateLobby();
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// WebSocket message handler
// ═══════════════════════════════════════════════════════════════════════════
function handleMsg(msg) {
  switch (msg.type) {
    case 'room_state':
      roomSeats = msg.seats;
      roomDebug = !!msg.debug;
      updateSeats(roomSeats, mySeat);
      setStatus(msg.status === 'running' ? 'Game in progress — rejoining…' : 'Waiting for players…');
      break;

    case 'seat_update':
      if (roomSeats[msg.seat]) roomSeats[msg.seat].mode = msg.mode;
      updateSeats(roomSeats, mySeat);
      break;

    case 'game_started':
      roomSeats = msg.seats;
      game.init({
        mySeat: mySeat,
        roomSeats: roomSeats,
        sendWS: sendWS,
        roomCode: myRoom,
        showScreen: showScreen,
        debug: roomDebug,
      });
      showScreen('game-screen');
      requestAnimationFrame(() => {
        game.positionGSeats();
        game.positionGTrickZone();
      });
      break;

    case 'catch_up': {
      const events = msg.events;
      if (events.length) {
        const lastEv = events[events.length - 1];
        roomSeats = msg.seats || roomSeats;
        game.init({
          mySeat: mySeat,
          roomSeats: roomSeats,
          sendWS: sendWS,
          roomCode: myRoom,
          showScreen: showScreen,
          debug: roomDebug,
        });
        showScreen('game-screen');
        // Populate event log from descriptions (no animations/sounds on catch-up)
        game.clearEvLog();
        const log = events.map(ev => ev.desc.replace(/\bP([0-5])\b/g, (_, n) => {
          const CCW_NUM = [1, 6, 5, 4, 3, 2];
          return 'P' + CCW_NUM[+n];
        }));
        game.setEvLog(log.slice(-60));
        // Set snapshot and render
        game.handleCatchUp(events, lastEv.snap);
        requestAnimationFrame(() => {
          game.positionGSeats();
          game.positionGTrickZone();
          game.renderGame();
        });
        if (lastEv.kind === 'hand_end' && lastEv.result) {
          // showHandEndOverlay is internal to game.js; the snapshot is already set
        }
      }
      break;
    }

    case 'game_event':
      game.handleEvent(msg.event);
      break;

    case 'your_turn':
      game.handleYourTurn(msg.legal_moves);
      break;

    case 'game_over':
      game.handleGameOver(msg.replay);
      break;

    case 'ai_thought':
      game.handleThought(msg);
      break;

    case 'error':
      console.warn('Server error:', msg.msg);
      break;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Seat claim (called from room.js callback)
// ═══════════════════════════════════════════════════════════════════════════
async function claimSeat(seat) {
  if (ws) {
    ws.close();
    ws = null;
  }
  const r = await fetch(`/api/room/join/${myRoom}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ seat }),
  });
  const d = await r.json();
  if (!r.ok) { alert(d.detail); return; }
  mySeat = d.seat;
  roomSeats = d.seats;
  updateSeats(roomSeats, mySeat);
  connectWS();
}

// ═══════════════════════════════════════════════════════════════════════════
// Start game (called from room.js callback)
// ═══════════════════════════════════════════════════════════════════════════
function startGame() {
  game.init({
    mySeat: mySeat,
    roomSeats: roomSeats,
    sendWS: sendWS,
    roomCode: myRoom,
    showScreen: showScreen,
    debug: roomDebug,
  });
  showScreen('game-screen');
  requestAnimationFrame(() => {
    game.positionGSeats();
    game.positionGTrickZone();
    game.populatePreGameSeats();
    game.showPreGameOverlay();
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// Mute button
// ═══════════════════════════════════════════════════════════════════════════
function setupMuteButton() {
  const muteBtn = document.getElementById('mute-btn');
  if (!muteBtn) return;

  // Set initial icon
  if (window._sound) {
    muteBtn.textContent = window._sound.muted ? '\u{1F507}' : '\u{1F50A}';
  }

  muteBtn.addEventListener('click', () => {
    const muted = window._sound?.toggle();
    if (window._speech) window._speech._muted = muted;
    muteBtn.textContent = muted ? '\u{1F507}' : '\u{1F50A}';
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// Debug button
// ═══════════════════════════════════════════════════════════════════════════
function setupDebugButton() {
  const btn = document.getElementById('debug-btn');
  if (btn) {
    btn.addEventListener('click', () => game.flagDebugMoment());
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Play / Pass buttons in action bar
// ═══════════════════════════════════════════════════════════════════════════
function setupActionButtons() {
  document.getElementById('pass-btn')?.addEventListener('click', () => game.playPass());
  document.getElementById('play-btn')?.addEventListener('click', () => game.playSelected());
  document.getElementById('gob-new-btn')?.addEventListener('click', () => location.reload());
}

// ═══════════════════════════════════════════════════════════════════════════
// Resize handler
// ═══════════════════════════════════════════════════════════════════════════
window.addEventListener('resize', () => {
  game.onResize();
});

// ═══════════════════════════════════════════════════════════════════════════
// Initialization — called on page load
// ═══════════════════════════════════════════════════════════════════════════
function init() {
  setupMuteButton();
  setupDebugButton();
  setupActionButtons();

  // Initialize lobby with callback that fires when player joins a room
  initLobby(({ roomCode, seat, seats }) => {
    myRoom = roomCode;
    mySeat = seat;
    roomSeats = seats;

    // Initialize room screen
    initRoom({
      seats: seats,
      mySeat: seat,
      roomCode: roomCode,
      onClaimSeat: claimSeat,
      onStartGame: startGame,
    });

    showScreen('room-screen');
    connectWS();
  });
}

// Kick off
init();
