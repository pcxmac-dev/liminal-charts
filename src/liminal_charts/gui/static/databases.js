'use strict';
/* Databases view (rail icon: a database), the integrator's (the user, 2026-11-07: "the integrator will have stats for
   the databases themselves, the platform only cares about hardware"). Laid out like every app's page: on the left
   what there is (each SQLite and DuckDB file in the data folder by what it holds, each feed's closed months in
   DuckDB, Liminal's MongoDB and its databases while it runs); in the middle the one picked, its data (its size on
   disk, growth an hour and last write; its tables and their rows, a feed's months, a MongoDB database's
   collections); on the right its specifics (where it is, its files, its engine's own figures, how its rows were
   counted); then the rail.
   Answered by the integrator (GET /api/databases and /api/databases/detail: liminal-integrator's databases.py):
   the folder listed while this view is shown (every 10 s: sizes and times, no database opened), the database
   picked opened read-only for the moment its figures are read (at most once a minute); nothing is asked while
   the page is hidden or another view is shown. A large table is never counted: its rows are SQLite's statistics
   or an estimate from its B-tree (≈), a feed's months the counts its manifest took when each was copied. */
(() => {
  const box = document.getElementById('viewDatabases');
  const { esc } = window.LiminalIcons;
  const LIST_MS = 10_000, DETAIL_MS = 30_000;
  let data = null, sel = null, detail = null, detailAt = 0, timer = null, failed = null, asking = null;

  const UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  function bytes(n) {
    if (n == null || !Number.isFinite(n)) return '–';
    let v = Math.abs(n), i = 0;
    while (v >= 1024 && i < UNITS.length - 1) { v /= 1024; i++; }
    return `${n < 0 ? '−' : ''}${i === 0 || v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${UNITS[i]}`;
  }
  const num = (n) => (n == null ? '–' : Number(n).toLocaleString('en-US'));
  function big(n) {  // a count too large to read digit by digit: three figures and its word
    if (n == null) return '–';
    if (n >= 1e12) return `${(n / 1e12).toPrecision(3)} trillion`;
    if (n >= 1e9) return `${(n / 1e9).toPrecision(3)} billion`;
    if (n >= 1e6) return `${(n / 1e6).toPrecision(3)} million`;
    return num(n);
  }
  const exact = (how) => how === 'counted' || how === 'manifest' || how === 'metadata';
  const rows = (r, how) => (r == null ? '–' : exact(how) ? num(r) : `≈ ${big(r)}`);
  function growth(g) {
    if (g == null) return 'measuring…';
    if (Math.abs(g) < 1) return 'none';
    return `${g > 0 ? '+' : '−'}${bytes(Math.abs(g))}/h`;
  }
  function span(s) {
    if (s == null) return 'shown once this page has watched for a minute';
    const m = s / 60;
    return m < 90 ? `over the last ${Math.round(m)} min` : `over the last ${(m / 60).toFixed(1)} h (this page away meanwhile)`;
  }
  const when = (iso) => (iso ? `${new Date(iso).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })} ET` : '–');
  function ago(iso) {
    if (!iso) return '–';
    const s = (Date.now() - Date.parse(iso)) / 1000;
    if (s < 90) return 'just now';
    if (s < 5400) return `${Math.round(s / 60)} min ago`;
    if (s < 172800) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} days ago`;
  }
  const ENGINE = { sqlite: 'SQLite', duckdb: 'DuckDB', mongodb: 'MongoDB' };
  const HOW = {
    counted: 'counted', statistics: "SQLite's statistics (its last ANALYZE)", estimated: 'estimated from its B-tree',
    manifest: 'counted when each month was copied (the manifest)', metadata: "the server's own count", virtual: 'a virtual table',
    unknown: 'not counted (too large to count, its tree not in the file yet)' };
  const tile = (title, value, small) => `<div class="tile"><h5>${title}</h5><div class="big">${value}</div><small>${small}</small></div>`;
  const spec = (pairs) => `<table class="act-table ex-mini db-spec"><tbody>${pairs.filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${v}</td></tr>`).join('')}</tbody></table>`;

  function entry(id) { return data?.entries.find((e) => e.id === id) || null; }

  async function load() {
    clearTimeout(timer);
    if (document.body.dataset.view !== 'databases' || document.hidden) return;
    try {
      data = await api('/api/databases');
      failed = null;
    } catch (err) {
      failed = /not found/i.test(String(err.message || err))
        ? 'The integrator answering this page keeps no database stats yet: it runs code from before them. Restarted (the LiminalIntegrator task), it shows them here.'
        : `The integrator did not answer: ${err.message || err}`;
    }
    if (data && !entry(sel)) { sel = data.entries[0]?.id || null; detail = null; }
    render();
    if (data && sel && (!detail || detail.id !== sel || Date.now() - detailAt > DETAIL_MS)) await loadDetail(sel);
    clearTimeout(timer);  // one loop, however many loads overlapped
    if (document.body.dataset.view === 'databases' && !document.hidden) timer = setTimeout(load, LIST_MS);
  }

  async function loadDetail(id) {
    if (asking === id) return;
    asking = id;
    try {
      const got = await api('/api/databases/detail', { id });
      if (got.id === sel) { detail = got; detailAt = Date.now(); }
    } catch (err) {
      if (id === sel) { detail = { id, figures: { error: String(err.message || err) } }; detailAt = Date.now(); }
    } finally {
      asking = null;
    }
    render();
  }

  // -- left: what there is -------------------------------------------------------------------
  function listHtml() {
    const m = data.mongo || {};
    return data.groups.map((g) => {
      const items = data.entries.filter((e) => e.group === g.id);
      const head = g.id === 'mongodb'
        ? `MongoDB <span class="p-muted">${m.running ? `${esc(m.version || '')} · running` : '· not running'}</span>` : esc(g.title);
      return `<h4 class="ex-group" title="${esc(g.about)}">${head}</h4>` + items.map((e) => {
        const what = e.kind === 'history' ? `${num(e.files)} month${e.files === 1 ? '' : 's'}${e.copying ? ' · copying' : ''}`
          : e.kind === 'mongodb' ? `${num(e.collections)} collections` : e.state || '';
        return `<button type="button" class="ex-venue${e.id === sel ? ' active' : ''}" data-db="${esc(e.id)}" title="${esc(e.path || e.id)}">
          <span class="ex-name">${esc(e.name)}</span><span class="ex-state">${esc(ENGINE[e.engine] || e.engine)}</span>
          <span class="ex-count">${bytes(e.size)} · ${esc(growth(e.growth))}${what ? ` · ${esc(what)}` : ''}</span></button>`;
      }).join('');
    }).join('');
  }

  // -- middle: the one picked ----------------------------------------------------------------
  function tilesHtml(e, f) {
    const side = e.side && (e.side.wal || e.side.shm || e.side.journal)
      ? `the file ${bytes(e.file)}${e.side.wal != null ? `, its -wal ${bytes(e.side.wal)}` : ''}${e.side.shm != null ? `, -shm ${bytes(e.side.shm)}` : ''}${e.side.journal != null ? `, -journal ${bytes(e.side.journal)}` : ''}`
      : e.kind === 'history' ? `${num(e.files)} month files${e.copying ? `, ${bytes(e.copying)} of a copy going on` : ''}`
        : e.kind === 'mongodb-server' ? 'its folder, mongo/ in the data folder' : e.engine === 'mongodb' ? 'its storage on the server' : 'one file';
    const tables = (f && f.tables) || [];
    const top = tables[0];
    const total = tables.reduce((s, t) => s + (t.rows || 0), 0);
    const estimated = tables.some((t) => t.rows != null && !exact(t.how));
    const rowsTile = e.kind === 'mongodb-server' ? tile('Documents', '–', 'asked only while it runs')
      : f && !f.error && tables.length
      ? tile(e.engine === 'mongodb' ? 'Documents' : 'Rows', `${estimated ? '≈ ' : ''}${big(total)}`,
             `in ${num(tables.length)} ${e.engine === 'mongodb' ? 'collection' : 'table'}${tables.length === 1 ? '' : 's'}; the largest ${esc(top.name)}`)
      : tile('Rows', '–', f?.error ? 'not read' : detail?.id === e.id ? 'none' : 'reading…');
    return `<div class="tiles">${tile('Size on disk', bytes(e.size), side)}${tile('Growth an hour', esc(growth(e.growth)), esc(span(e.growthSpan)))}
      ${tile('Last written', esc(ago(e.modified)), esc(e.modified ? when(e.modified) : e.engine === 'mongodb' ? 'the server writes its own files' : '–'))}${rowsTile}</div>`;
  }

  // What a tag in a table means, said under it on a touch screen: its tooltip is a mouse's (the user, 2026-11-10: what
  // a tag means becomes visible text on touch screens)
  const tagNote = (words) => (words.length ? `<div class="p-muted small touch-only db-note">${words.map(esc).join('<br>')}</div>` : '');

  function tablesHtml(f) {
    const ts = f.tables || [];
    if (!ts.length) return '<div class="p-muted small db-note">No tables.</div>';
    return `<h4>Tables</h4><div class="table-wrap"><table class="act-table db-tables"><thead><tr><th>Table</th><th class="num">Rows</th><th>Counted</th><th class="num">Indexes</th></tr></thead>
      <tbody>${ts.map((t) => `<tr><td><b>${esc(t.name)}</b>${t.withoutRowid ? ' <span class="chip" title="Its rows in its primary key\'s B-tree (WITHOUT ROWID)">without rowid</span>' : ''}</td>
        <td class="num">${rows(t.rows, t.how)}</td><td class="small">${esc(HOW[t.how] || t.how)}</td><td class="num">${t.indexes == null ? '–' : num(t.indexes)}</td></tr>`).join('')}</tbody></table></div>
      ${tagNote(ts.some((t) => t.withoutRowid) ? ["without rowid: its rows in its primary key's B-tree (WITHOUT ROWID)"] : [])}`;
  }

  function monthsHtml(f) {
    const ms = f.months || [];
    const head = f.manifest ? '' : '<div class="p-muted small db-note">No manifest in the feed\'s SQLite file: its month files are listed as they are.</div>';
    const table = ms.length ? `<h4>Months <span class="p-muted small">newest first</span></h4><div class="table-wrap"><table class="act-table db-months">
      <thead><tr><th>Month</th><th class="num">Bars</th><th class="num">Built</th><th class="num">Size</th><th>Copied</th><th>State</th></tr></thead>
      <tbody>${ms.map((m) => `<tr><td><b>${esc(m.month)}</b><div class="sub">${esc(m.file)}</div></td><td class="num">${num(m.bars)}</td>
        <td class="num">${num(m.built)}</td><td class="num">${bytes(m.onDisk ?? m.bytes)}</td><td class="small">${esc(when(m.copiedAt))}</td>
        <td class="small">${m.onDisk == null ? '<span class="warn">its file is missing</span>' : m.stale ? '<span class="warn" title="Written in SQLite since its copy: read from SQLite until it is copied again">written since</span>' : 'current'}</td></tr>`).join('')}</tbody></table></div>
      ${tagNote(ms.some((m) => m.onDisk != null && m.stale) ? ['written since: written in SQLite since its copy, so read from SQLite until it is copied again'] : [])}` : '';
    const un = (f.unlisted || []).length ? `<h4>Not in the manifest</h4><table class="act-table ex-mini"><tbody>${f.unlisted.map((u) => `<tr><td>${esc(u.file)}</td>
      <td class="num">${bytes(u.size)}</td><td class="small">${esc(u.state === 'copying' ? 'a copy going on' : u.state === 'superseded' ? 'superseded: removed by the next run' : 'unlisted')}</td></tr>`).join('')}</tbody></table>` : '';
    return head + table + un;
  }

  function collectionsHtml(f) {
    const cs = f.tables || [];
    if (!cs.length) return '<div class="p-muted small db-note">No collections.</div>';
    return `<h4>Collections</h4><div class="table-wrap"><table class="act-table db-colls"><thead><tr><th>Collection</th><th class="num">Documents</th><th class="num">Data</th>
      <th class="num">Storage</th><th class="num">Indexes</th></tr></thead>
      <tbody>${cs.map((c) => `<tr><td><b>${esc(c.name)}</b>${c.error ? `<div class="sub warn">${esc(c.error)}</div>` : ''}</td><td class="num">${num(c.count)}</td>
        <td class="num">${bytes(c.size)}</td><td class="num">${bytes(c.storageSize)}</td><td class="num">${c.indexes == null ? '–' : `${num(c.indexes)} · ${bytes(c.indexSize)}`}</td></tr>`).join('')}</tbody></table></div>`;
  }

  function mainHtml(e) {
    const d = detail && detail.id === e.id ? detail : null;
    const f = d?.figures;
    let body;
    if (e.kind === 'mongodb-server') {
      body = `<div class="p-muted small db-note">MongoDB is not running. The integrator never starts it to read its figures: they show here while it runs
        (it starts when a plugin's documents are kept there). Its folder holds ${bytes(e.size)} on disk.</div>`;
    } else if (!f) {
      body = '<div class="p-muted small db-note">Reading it…</div>';
    } else if (f.error) {
      body = `<div class="db-note"><span class="warn">Not read:</span> <span class="small">${esc(f.error)}</span></div>`;
    } else if (e.kind === 'history') {
      body = monthsHtml(f);
    } else if (e.engine === 'mongodb') {
      body = collectionsHtml(f);
    } else {
      body = tablesHtml(f);
    }
    return tilesHtml(e, f) + body;
  }

  // -- right: its specifics ------------------------------------------------------------------
  function sideHtml(e) {
    const d = detail && detail.id === e.id ? detail : null;
    const f = (d && d.figures) || {};
    const read = d && d.readAt ? `${esc(when(d.readAt))}${f.took != null ? ` <span class="p-muted">(${num(Math.round(f.took * 1000))} ms)</span>` : ''}` : null;
    let pairs;
    if (e.engine === 'mongodb') {
      const s = (d && d.server) || data.mongo || {};
      pairs = [['Server', `${esc(s.host || '')}:${esc(s.port ?? '')}`], ['State', s.running ? 'running' : esc(s.why || 'not running')],
               ['Version', s.version ? esc(s.version) : null], ['Up', s.uptime != null ? `${num(Math.round(s.uptime / 3600))} h` : null],
               ['Process', s.pid != null ? `pid ${esc(s.pid)}${s.residentMB != null ? `, ${num(s.residentMB)} MB in memory` : ''}` : null],
               ['Its folder', s.diskPath ? `<code>${esc(s.diskPath)}</code>` : null], ['On disk', s.disk != null ? `${bytes(s.disk)} in ${num(s.diskFiles)} files` : null]];
      if (e.kind === 'mongodb' && !f.error) {
        pairs.push(['Data', bytes(f.dataSize)], ['Storage', bytes(f.storageSize)], ['Indexes', f.indexes != null ? `${num(f.indexes)} · ${bytes(f.indexSize)}` : null],
                   ['Total', bytes(f.totalSize)], ['Average document', bytes(f.avgObjSize)],
                   ['Its disk', f.fsTotalSize ? `${bytes(f.fsUsedSize)} used of ${bytes(f.fsTotalSize)}` : null],
                   ["MongoDB's own", (s.system || []).length ? esc(s.system.join(', ')) : null], ['Read', read]);
      }
    } else {
      pairs = [['Where', `<code>${esc(e.path)}</code>`], ['Engine', esc(ENGINE[e.engine] || e.engine)], ['Last written', esc(when(e.modified))]];
      if (e.kind === 'history') {
        pairs.push(['Manifest', f.manifest ? `<code>${esc(e.manifest)}</code>, its history table` : `none (<code>${esc(e.manifest)}</code>)`],
                   ['Months', f.months ? `${num(f.months.length)}${f.stale ? `, ${num(f.stale)} written since` : ''}${f.missing ? `, ${num(f.missing)} missing` : ''}` : null],
                   ['Rows', f.tables ? `${num(f.bars)} bars, ${num(f.built)} built` : null]);
      } else if (e.engine === 'sqlite') {
        pairs.push(['Pages', f.pages != null ? `${num(f.pages)} of ${bytes(f.pageSize)}` : null],
                   ['Free pages', f.freePages != null ? `${num(f.freePages)} (${bytes(f.freePages * f.pageSize)})` : null],
                   ['Journal', f.journal ? esc(f.journal) : null], ['User version', f.userVersion != null ? num(f.userVersion) : null],
                   ['Indexes', f.indexes != null ? num(f.indexes) : null],
                   ['Statistics', f.tables ? (f.statistics ? 'analysed (sqlite_stat1)' : 'never analysed') : null],
                   ['SQLite', f.sqlite ? esc(f.sqlite) : null]);
      } else if (f.duckdb) {
        pairs.push(['DuckDB', esc(f.duckdb)]);
      }
      pairs.push(['Read', read]);
    }
    const lim = data.limits || {};
    return `<h3>Specifics</h3>${spec(pairs)}
      <h4>How it is read</h4><div class="p-muted small">Opened read-only for a moment when picked, at most once a minute; the folder listed every
        ${num((data.cadence || {}).list || 5)} s while this view is shown, nothing while it is not. A table is counted outright only when cheap
        (a file under ${bytes(lim.exactBytes)}, or under ${num(lim.exactRows)} rows by its B-tree); a larger one shows SQLite's statistics, or ≈ an estimate from
        ${num(lim.probes)} paths through its B-tree; a feed's months, what the manifest counted when each was copied. MongoDB is asked only while it runs.</div>`;
  }

  // Drawn again every 10 s: the three columns stay, each drawn again in its parts, a part only when it changed, and
  // nothing under a finger (its tap lands) or while a finger's scroll moves (panels.js Views; the user, 2026-11-10: "I
  // want to support touch as well as mouse clicking on the interface").
  function render() {
    if (document.body.dataset.view !== 'databases') return;
    if (!data) {
      box.innerHTML = `<div class="empty">${failed ? esc(failed) : 'Listing the databases…'}</div>`;
      return;
    }
    const e = entry(sel);
    const t = data.totals || {};
    const cols = [
      `<div class="view-head"><div><h2>Databases</h2>
        <div class="p-muted small">${num(t.databases)} databases · ${bytes(t.size)} · ${esc(growth(t.growth))}</div></div></div>
        ${failed ? `<div class="small warn">${esc(failed)}</div>` : ''}${listHtml()}`,
      `<div class="view-head"><div><h2>${esc(e ? e.name : '')}</h2>
        <div class="p-muted small">${e ? `${esc(ENGINE[e.engine] || e.engine)} · ${esc((data.groups.find((g) => g.id === e.group) || {}).about || '')}` : ''}</div></div></div>
        ${e ? mainHtml(e) : '<div class="empty">No database in the data folder.</div>'}`,
      e ? sideHtml(e) : '',
    ];
    window.Views.redraw(box, () => paint(cols));
  }

  function paint([left, middle, side]) {
    const [a, b, c] = box.children;
    if (box.children.length === 3 && a.matches('.ex-venues') && b.matches('.ex-list') && c.matches('.ex-side')) {
      window.Views.patch(a, left, render);
      window.Views.patch(b, middle, render);
      window.Views.patch(c, side, render);
      return;
    }
    const keep = [...box.querySelectorAll('.ex-venues, .ex-list, .ex-side')].map((el) => el.scrollTop);
    box.innerHTML = `<div class="ex-venues">${left}</div><div class="ex-list">${middle}</div><aside class="ex-side">${side}</aside>`;
    box.querySelectorAll('.ex-venues, .ex-list, .ex-side').forEach((el, i) => { el.scrollTop = keep[i] || 0; });
  }

  box.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-db]');
    if (!b || b.dataset.db === sel) return;
    sel = b.dataset.db;
    render();
    window.Views.reveal(box.querySelector('.ex-list'));  // on a phone (the columns stacked) the one picked, below the list
    loadDetail(sel);
  });
  window.addEventListener('liminal:view', (ev) => {
    if (ev.detail.view === 'databases') load();
    else clearTimeout(timer);
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); else clearTimeout(timer); });
  if (document.body.dataset.view === 'databases') load();  // the page opened on this view (before this script listened)
})();
