// login.js — shared-passcode login for a hosted server (see src/hosting.py)

const form = document.getElementById('lobby-box');
const input = document.getElementById('passcode');
const btn = document.getElementById('login-btn');
const err = document.getElementById('lobby-err');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  err.textContent = '';
  btn.disabled = true;
  try {
    const r = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passcode: input.value }),
    });
    if (r.ok) { window.location.href = '/'; return; }
    err.textContent = r.status === 401 ? '口令不对 Wrong passcode' : '出错了 Error ' + r.status;
    input.select();
  } catch (_) {
    err.textContent = '连不上服务器 Cannot reach the server';
  } finally {
    btn.disabled = false;
  }
});
