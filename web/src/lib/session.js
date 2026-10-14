// session.js — what this browser tab remembers about its player
//
// The seat (room, seat number and the token the server issued for it) lives in
// sessionStorage, which is per tab: two tabs are two players with two seats,
// and a reload keeps the seat. The display name is shared by all tabs.
const SEAT_KEY = 'redten-seat';
const NAME_KEY = 'redten-name';

export function savedSeat() {
  try {
    const s = JSON.parse(sessionStorage.getItem(SEAT_KEY) || 'null');
    return s && s.room && s.token ? s : null;
  } catch (_) { return null; }
}

export function saveSeat(room, seat, token) {
  try { sessionStorage.setItem(SEAT_KEY, JSON.stringify({ room, seat, token })); } catch (_) {}
}

export function clearSeat() {
  try { sessionStorage.removeItem(SEAT_KEY); } catch (_) {}
}

export function tokenFor(room) {
  const s = savedSeat();
  return s && s.room === room ? s.token : null;
}

export function playerName() {
  try { return localStorage.getItem(NAME_KEY) || ''; } catch (_) { return ''; }
}

export function setPlayerName(name) {
  try { localStorage.setItem(NAME_KEY, name); } catch (_) {}
}

// Join a room, or keep / move the seat this tab already holds there.
// Resolves to { roomCode, seat, token, seats, status }; rejects with the server's reason.
export async function joinRoom(code, seat) {
  const r = await fetch(`/api/room/join/${code}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ seat, name: playerName(), token: tokenFor(code) }),
  });
  if (r.status === 401) { window.location.href = '/login'; throw new Error('请先登录'); }
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || r.statusText);
  saveSeat(d.room_code, d.seat, d.token);
  return { roomCode: d.room_code, seat: d.seat, token: d.token, seats: d.seats, status: d.status };
}
