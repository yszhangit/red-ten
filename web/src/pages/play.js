// play.js — Orchestrator: wires lobby, room, game screens + WebSocket
import { initLobby, activateLobby, deactivateLobby, showLobbyError } from '/src/pages/lobby.js';
import { joinRoom, savedSeat, clearSeat } from '/src/lib/session.js';
import { initRoom, updateSeats, setRoomCode, setStatus } from '/src/pages/room.js';
import * as game from '/src/pages/game.js';

// ═══════════════════════════════════════════════════════════════════════════
// Global state
// ═══════════════════════════════════════════════════════════════════════════
let myRoom = null;
let mySeat = -1;
let myToken = '';        // issued by the server when this tab took its seat
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
  ws = new WebSocket(`${proto}://${location.host}/ws/${myRoom}/${mySeat}`
                     + `?token=${encodeURIComponent(myToken)}`);

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
      if (roomSeats[msg.seat]) {
        roomSeats[msg.seat].mode = msg.mode;
        roomSeats[msg.seat].name = msg.name || '';
      }
      updateSeats(roomSeats, mySeat);
      game.onSeatsChanged();
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
      clearSeat();           // a reload now goes to the lobby, not back to this table
      game.handleGameOver(msg.replay);
      break;

    case 'ai_thought':
      game.handleThought(msg);
      break;

    case 'error':
      console.warn('Server error:', msg.msg);
      if (msg.code === 'not_your_seat' || msg.code === 'no_room') leaveTable(msg.code);
      break;
  }
}

// Leave the table on purpose: give the seat up now, not after the hold for
// dropped connections.
function leaveRoom() {
  sendWS({ type: 'leave' });
  clearSeat();
  closeWS();
  showScreen('lobby-screen');
}

// The seat is gone (held too long by a dropped connection and taken, or the
// server restarted): stop reconnecting and go back to the lobby.
function leaveTable(why) {
  clearSeat();
  closeWS();
  showScreen('lobby-screen');
  showLobbyError(why === 'no_room' ? '牌桌已经结束 This table has ended'
                                   : '座位已被别人接手 Your seat was taken over');
}

// ═══════════════════════════════════════════════════════════════════════════
// Seat claim (called from room.js callback)
// ═══════════════════════════════════════════════════════════════════════════
async function claimSeat(seat) {
  let joined;
  try {
    joined = await joinRoom(myRoom, seat);     // moves this tab's seat
  } catch (e) { alert(e.message); return; }
  if (ws) {
    const old = ws;
    ws = null;
    old.onclose = null;                        // no reconnect to the old seat
    old.close();
  }
  mySeat = joined.seat;
  myToken = joined.token;
  roomSeats = joined.seats;
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
  document.getElementById('leave-btn')?.addEventListener('click', (e) => {
    e.preventDefault();
    leaveRoom();
  });
  document.getElementById('gob-new-btn')?.addEventListener('click', () => {
    clearSeat();
    location.reload();
  });
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

  // Called when this tab has a seat: joined from the lobby, or restored on reload
  const enterRoom = ({ roomCode, seat, token, seats }) => {
    myRoom = roomCode;
    mySeat = seat;
    myToken = token;
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
  };

  initLobby(enterRoom);

  // A reload keeps the seat: rejoin with the token this tab was given.
  const saved = savedSeat();
  if (saved) {
    joinRoom(saved.room, saved.seat).then(enterRoom).catch(() => clearSeat());
  }
}

// Kick off
init();
