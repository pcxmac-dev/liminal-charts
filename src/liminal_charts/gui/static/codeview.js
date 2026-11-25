'use strict';
/* Codebase view (rail icon: a repository's branch; Monday list item 15: "Codebase in the login's, the integrator's and
   the charts' pages (the platform has it)"; the user, 2026-11-16: "another functional class called codebase ... added
   to all the 'apps' just like connections"; "it will also say how far it is behind the remote"). The library's object
   in every app (liminal_libs.apps.Codebase, GET /api/codebase) drawn by the library's own script (codebase.js), as the
   platform draws it: its data in the middle (each language's share, the repositories, every language), its specifics
   on the right (the whole, or the repository picked), then the rail. On the charts page the charts app's own code base,
   answered by the front (gui/front.py: never passed on to the integrator, whose own it would be); on the integrator's
   page (the fetcher's page) the integrator's, answered by its frame (gui/server.py).
   Asked only while the view is shown (codebase.js asks again every second only while a reading or an asking of the
   remotes is under way); left, nothing more is asked. "Ask the remotes" asks for a yes first (git fetch, nothing checked
   out changes). A library from before 1.59.0 has no codebase.js: the view's icon leaves the rail. */
(() => {
  const box = document.getElementById('viewCodebase');
  const C = window.LiminalCodebase;
  if (!C || !box) {  // no drawing to hold (an older library): no entry on the rail either
    const b = document.getElementById('railCodebase');
    if (b) b.hidden = true;
    return;
  }
  const dataEl = document.getElementById('codebaseData'), sideEl = document.getElementById('codebaseSide');
  let shown = false;
  // the page's own way to ask its app: a JSON answer, an Error when refused (app.js api, WRITE_HEADERS)
  const get = (path) => api(path);
  async function send(path, body) {
    const res = await fetch(path, { method: 'POST', headers: WRITE_HEADERS, body: JSON.stringify(body || {}) });
    let got = {};
    try { got = await res.json(); } catch (err) { got = {}; }  // not JSON: its status says
    if (!res.ok) throw new Error((got && got.error) || `the app answered ${res.status}`);
    return got;
  }
  function show() {
    if (shown) return;
    shown = true;
    C.mount(dataEl, sideEl, { get, send });
  }
  function leave() {
    if (!shown) return;
    shown = false;
    C.unmount();
  }
  window.addEventListener('liminal:view', (ev) => { if (ev.detail.view === 'codebase') show(); else leave(); });
  if (document.body.dataset.view === 'codebase') show();  // the page opened on this view (before this script listened)
})();
