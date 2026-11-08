'use strict';
// The main page's sign-in (the user, 2026-11-06: "i need a login or sign up on the main page"): the
// login app's page held here (sign in, or sign up with your email), and once you are signed in your
// profile is found or made under your account name and the app opens as it. Without a login app the
// profiles alone, as before: the most recently signed in first, a click signs in (no passwords) and
// opens the app; a new one starts with an empty watchlist, no markups and its own preferences. The
// icon: Leonardo's Vitruvian Man (icons.js).
(() => {
  const { icon, esc } = window.LiminalIcons;
  const $ = (id) => document.getElementById(id);
  const WRITE = { 'Content-Type': 'application/json', 'X-Liminal-Charts': '1' };

  $('loginIcon').innerHTML = icon('vitruvian', 140);

  const ago = (iso) => {
    if (!iso) return 'not signed in yet';
    const s = (Date.now() - Date.parse(iso)) / 1000;
    if (s < 3600) return `here ${Math.max(1, Math.round(s / 60))} min ago`;
    if (s < 86400) return `here ${Math.round(s / 3600)} h ago`;
    return `here ${Math.round(s / 86400)} days ago`;
  };

  function failed(err) {
    $('loginError').textContent = String(err.message || err);
  }

  async function post(path, body) {
    const res = await fetch(path, { method: 'POST', headers: WRITE, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  async function signIn(id) {
    await post('/api/profiles/sign-in', { id });
    // the app, as this profile, on the globe (a new #markets alone would not load the page again)
    history.replaceState(null, '', '/#markets');
    location.reload();
  }

  // -- the login app on this page -----------------------------------------------------------------
  async function loginStatus() {
    // the login app bound to this front (/login/), or null when there is none or it does not answer
    try {
      const r = await fetch('/login/api/status');
      return r.ok ? await r.json() : null;
    } catch (e) { return null; }
  }

  async function openAs(name) {
    // your profile, by your account name: found, else made; then the app
    const res = await fetch('/api/profiles');
    const { profiles = [] } = await res.json();
    let me = profiles.find((p) => p.name.toLowerCase() === String(name).toLowerCase());
    if (!me) me = await post('/api/profiles', { name });
    await signIn(me.id);
  }

  function showLoginApp(st) {
    document.querySelector('.login').classList.add('with-app');
    $('picker').hidden = true;
    $('loginApp').hidden = false;
    const f = $('loginFrame');
    if (!f.dataset.loaded) {
      f.dataset.loaded = '1';
      // in this page's theme (the UI's, theme.js; the login app's page honours a `theme` in its address)
      Promise.resolve(window.LiminalTheme?.ready).catch(() => null).then(() => {
        const theme = document.body.dataset.theme;
        f.src = `/login/?embedded=1${theme ? `&theme=${encodeURIComponent(theme)}` : ''}`;
      });
    }
    $('loginNote').textContent = st && st.users === 0
      ? 'No account yet: sign up with your email, and the app opens as you.'
      : 'Sign in, or sign up with your email; the app opens as you.';
  }

  window.addEventListener('message', (e) => {
    // the login page held here says who signed in (login.js of liminal-login, embedded)
    if (e.origin !== location.origin || !e.data || e.data.liminal !== 'login') return;
    if (e.data.signedIn && e.data.name) openAs(e.data.name).catch(failed);
  });

  async function start() {
    const st = await loginStatus();
    if (st) {
      if (st.signedIn && st.name) {
        try { await openAs(st.name); return; } catch (err) { failed(err); }
      }
      showLoginApp(st);
      return;
    }
    $('picker').hidden = false;  // no login app: the profiles
    load();
  }

  // -- the profiles, without a login app ---------------------------------------------------------
  async function load() {
    try {
      const res = await fetch('/api/profiles');
      const { profiles = [], current } = await res.json();
      $('profileList').innerHTML = profiles.map((p) => `
        <button type="button" role="listitem" data-id="${p.id}" class="login-profile${p.id === current ? ' current' : ''}">
          <span class="login-avatar">${esc(p.name.slice(0, 1).toUpperCase())}</span>
          <span class="login-name">${esc(p.name)}</span>
          <span class="login-seen p-muted">${esc(ago(p.seen_at))}</span>
        </button>`).join('');
    } catch (err) { failed(err); }
  }

  $('profileList').addEventListener('click', (e) => {
    const b = e.target.closest('[data-id]');
    if (b) signIn(Number(b.dataset.id)).catch(failed);
  });
  $('newProfile').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = $('newName').value.trim();
    if (!name) return;
    try {
      const made = await post('/api/profiles', { name });
      await signIn(made.id);
    } catch (err) { failed(err); }
  });
  start();
})();
