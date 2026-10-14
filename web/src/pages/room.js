// room.js — Waiting room screen: seat grid, start button, room code
import { pLabel } from '/src/lib/seat.js';

const CCW_ORDER = [0, 5, 4, 3, 2, 1];  // seats in P1→P6 display order

let _seats = [];
let _mySeat = -1;
let _onClaimSeat = null;
let _onStartGame = null;

export function initRoom({ seats, mySeat, roomCode, onClaimSeat, onStartGame }) {
  _seats = seats;
  _mySeat = mySeat;
  _onClaimSeat = onClaimSeat;
  _onStartGame = onStartGame;

  document.getElementById('room-code-display').textContent = roomCode;
  renderSeatGrid();

  // Wire up start button
  const startBtn = document.getElementById('start-btn');
  startBtn.replaceWith(startBtn.cloneNode(true)); // remove old listeners
  document.getElementById('start-btn').addEventListener('click', () => {
    if (_onStartGame) _onStartGame();
  });
}

export function updateSeats(seats, mySeat) {
  _seats = seats;
  _mySeat = mySeat;
  renderSeatGrid();
}

export function setRoomCode(code) {
  document.getElementById('room-code-display').textContent = code;
}

export function setStatus(msg) {
  document.getElementById('room-status-msg').textContent = msg;
}

function renderSeatGrid() {
  const grid = document.getElementById('seat-grid');
  grid.innerHTML = '';
  for (const i of CCW_ORDER) {
    const info = _seats[i] || { seat: i, mode: 'ai' };
    const isMe = i === _mySeat;
    const isHuman = info.mode === 'human';
    const div = document.createElement('div');
    div.className = 'seat-slot ' + (isMe ? 'mine' : isHuman ? 'taken' : 'ai');
    div.id = 'slot-' + i;
    // Names are typed by players: set them as text, never as HTML.
    const label = document.createElement('div');
    label.className = 'slot-label';
    label.textContent = pLabel(i);
    const status = document.createElement('div');
    status.className = 'slot-status';
    status.textContent = isMe ? `\u{1F464} ${info.name || 'You'}${info.name ? ' (You)' : ''}`
      : isHuman ? `\u{1F464} ${info.name || 'Human'}`
      : info.name ? `\u{1F916} AI（${info.name} 不在）` : '\u{1F916} AI';
    div.append(label, status);
    if (!isHuman && !isMe) {
      div.addEventListener('click', () => {
        if (_onClaimSeat) _onClaimSeat(i);
      });
    }
    grid.appendChild(div);
  }
}
