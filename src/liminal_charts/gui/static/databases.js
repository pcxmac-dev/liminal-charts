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
   or an estimate from its B-tree (≈), a feed's months the counts its manifest took when each was copied.
   At the top of the list, the engines: each database plugin (GET /api/databases/plugins: liminal-integrator 1.12.0)
   with its instances, the ones it has by itself and the ones you add; the one picked shows them with their figures
   and Test (a read: POST /api/databases/instances {action: test}), then its API card. Since 0.2.8 (Monday list item
   30, step 5; the user, 2026-11-25: the instance forms move to the platform's Databases section, the platform the only
   writer of instance settings) its change forms are links into the platform's page for that plugin, its Main,
   External API and Internal API tabs (/platform/#sqlite, #sqlite/external, #sqlite/internal): no form here posts a
   change. An integrator from before them answers neither: the view is then as it was. */
(() => {
  const box = document.getElementById('viewDatabases');
  const { esc } = window.LiminalIcons;
  const LIST_MS = 10_000, DETAIL_MS = 30_000;
  let data = null, sel = null, detail = null, detailAt = 0, timer = null, failed = null, asking = null;
  let engines = null;  // the database plugins with their instances (null: an integrator from before them, or not read)
  let said = null;  // what the last test's answer said: {plugin, ok, said, lines}
  let busy = false;  // a test is being answered

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
    try {
      engines = await api('/api/databases/plugins');
    } catch (err) {
      if (/not found/i.test(String(err.message || err))) engines = null;  // an integrator from before the database plugins
    }
    if (data && !entry(sel) && !pluginOf(sel)) { sel = data.entries[0]?.id || null; detail = null; }
    render();
    if (data && sel && !pluginOf(sel) && (!detail || detail.id !== sel || Date.now() - detailAt > DETAIL_MS)) await loadDetail(sel);
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
    return enginesHtml() + data.groups.map((g) => {
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

  // -- the engines: each database plugin with its instances (liminal-integrator 1.12.0; the user, 2026-11-15: "for the
  // integrator i need plugins for each type of database ... each instance in the plugin has it's own configuration how
  // it's shared, ports, permissions etc"). A plugin is picked like a database. Since 0.2.8 (Monday list item 30, step 5;
  // the user, 2026-11-25: the instance forms move to the platform's Databases section, the platform the only writer of
  // instance settings, the charts' view keeping its list and linking there): its instances and their figures here, and
  // Test, a read; every change (New database, Connect, Share, Port, Sign-in, Forget) is made on the platform's page for
  // the plugin, which this view links: its Main, External API and Internal API tabs. Nothing here posts a change.
  const pluginOf = (id) => (typeof id === 'string' && id.startsWith('plugin:')
    ? (engines?.plugins || []).find((p) => `plugin:${p.name}` === id) || null : null);
  const ELSEWHERE = "change this on the platform's Databases section";
  // the platform's tabs for a plugin's page (liminal-platform 0.36.0: #name, #name/external, #name/internal)
  const PLATFORM_TABS = [
    ['', 'Main', 'its instances and their forms: New database, Connect, Share, Port, Sign-in, Forget'],
    ['external', 'External API', 'what it attaches to'],
    ['internal', 'Internal API', 'what it shares with the platform and the other apps']];
  // Where the platform's page is: the address the answer names (a page not under the charts front), else the platform
  // the charts front links, under /platform/ (gui/front.py PLATFORM_PREFIX). The integrator's own page links no
  // platform: its words alone, no link.
  function platformBase() {
    const named = engines && typeof engines.platform === 'string' ? engines.platform.trim() : '';
    if (/^(https?:\/\/|\/)[^\s"'<>]*$/.test(named)) return named.endsWith('/') ? named : `${named}/`;
    return window.FETCHER_MODE ? null : '/platform/';
  }
  const platformItem = (p, tab) => (tab ? `${p.name}/${tab}` : p.name);
  function platformHref(p, tab) {
    const base = platformBase();
    return base ? `${base}#${encodeURIComponent(p.name)}${tab ? `/${tab}` : ''}` : null;
  }
  function platformLink(p, tab, label, what) {
    const href = platformHref(p, tab);
    if (!href) return '';
    return `<a class="db-link" href="${esc(href)}" target="_blank" rel="noopener" data-platform="${esc(platformItem(p, tab))}"
      title="${esc(`${p.title || p.name} on the platform, ${label}: ${what}. ${ELSEWHERE[0].toUpperCase()}${ELSEWHERE.slice(1)}.`)}">${esc(label)}</a>`;
  }
  // the links into the platform's page for the plugin, with the words that say why
  function platformHtml(p) {
    const links = PLATFORM_TABS.map(([tab, label, what]) => platformLink(p, tab, label, what)).join('');
    return `<div class="db-links"><span class="db-elsewhere">New database, Connect, Share, Port, Sign-in, Forget: ${esc(ELSEWHERE)}${links ? '.' : ' (the platform\'s page).'}</span>${links}</div>`;
  }

  function enginesHtml() {
    if (!engines || !(engines.plugins || []).length) return '';
    const left = (engines.errors || []).map((why) => `<div class="small warn db-note">${esc(why)}</div>`).join('');
    return `<h4 class="ex-group" title="The database plugins: an engine each, with its instances (its own, and the ones added on the platform)">Engines</h4>${left}`
      + engines.plugins.map((p) => {
        const all = p.instances || [], added = all.filter((i) => !i.builtin).length;
        const state = p.error ? 'not read' : p.driver && !p.driver.ok ? 'no driver' : `${num(all.length)} instance${all.length === 1 ? '' : 's'}`;
        return `<button type="button" class="ex-venue${`plugin:${p.name}` === sel ? ' active' : ''}" data-db="plugin:${esc(p.name)}" title="${esc(p.about || '')}">
          <span class="ex-name">${esc(p.title || p.name)}</span><span class="ex-state">${esc(state)}</span>
          <span class="ex-count">${esc((p.methods || []).map((m) => m.title.toLowerCase()).join(' · '))}${added ? ` · ${num(added)} added` : ''}</span></button>`;
      }).join('');
  }

  function stateHtml(i) {
    const size = i.bytes != null ? ` · ${bytes(i.bytes)}${i.files != null ? ` in ${num(i.files)} file${i.files === 1 ? '' : 's'}` : ''}` : '';
    if (i.state === 'ready') return `ready${size}`;
    if (i.state === 'listening') return `running${i.serverVersion ? ` (${esc(i.serverVersion)})` : ''}${size}`;
    const soft = ['not running', 'not asked', 'empty', 'elsewhere'].includes(i.state);
    return `<span${soft ? '' : ' class="warn"'}>${esc(i.state || 'unknown')}</span>${size}${i.why ? `<div class="sub">${esc(i.why)}</div>` : ''}`;
  }

  function instanceRow(p, i) {
    const kind = i.builtin ? 'its own' : i.made ? 'made here' : 'connected';
    const how = [esc(i.methodTitle || i.method), i.tls ? 'TLS' : '', i.remote ? 'off this machine' : '',
      i.auth ? `signs in${i.role ? ` as ${esc(i.role)}` : ''}${i.signsIn === false ? ' <span class="warn">(its sign-in is missing)</span>' : ''}` : '']
      .filter(Boolean).join(' · ');
    // Test, a read; a change (Share, Port, Sign-in, Forget) on the platform's page for the plugin
    const acts = [`<button type="button" data-do="test" data-inst="${esc(i.name)}" title="Open it for reading and ask what it is">Test</button>`,
      platformLink(p, '', 'Change…', `${i.name}'s share, port, sign-in, or forgetting it`)];
    return `<tr><td><b>${esc(i.title || i.name)}</b><div class="sub">${esc(i.name)} · ${kind}</div></td>
      <td class="small">${(i.holds || []).map((h) => esc(h.name)).join(', ') || '<span class="p-muted">nothing yet</span>'}</td>
      <td class="small"><code>${esc(i.place || i.where || '')}</code><div class="sub">${how}</div></td>
      <td class="small">${esc(i.sharedWords || '')}</td><td class="small">${stateHtml(i)}</td>
      <td class="db-acts">${acts.join('')}</td></tr>`;
  }

  function saidHtml(p) {
    if (!said || said.plugin !== p.name) return '';
    return `<div class="db-said${said.ok ? '' : ' failed'}"><b>${esc(said.said)}</b>${(said.lines || []).length
      ? `<ul>${said.lines.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>` : ''}</div>`;
  }

  // the plugin's own word that a change is made elsewhere (a platform part that is not the writer: liminal-libs 1.90.0)
  // is said once, by the links' words
  const saysElsewhere = (why) => String(why || '').toLowerCase().includes(ELSEWHERE.toLowerCase());

  function pluginMainHtml(p) {
    if (p.error) return `<div class="db-note"><span class="warn">Not read:</span> <span class="small">${esc(p.error)}</span></div>`;
    const lacks = p.driver && !p.driver.ok;
    const notes = [p.problem, lacks ? p.driver.why : '', !lacks && p.can && p.can.why && !saysElsewhere(p.can.why) ? p.can.why : ''].filter(Boolean);
    // Each part has a place of its own, there even when empty: a redraw replaces a part only when its own markup
    // changed (Views.patch), so a test's answer appearing leaves the rest as it is.
    const slot = (html) => html || '<div class="db-slot" hidden></div>';
    return `<div class="db-notes">${notes.map((n) => `<div class="db-note small warn">${esc(n)}</div>`).join('')}</div>
      <h4>Instances</h4><div class="table-wrap"><table class="act-table db-instances"><thead><tr><th>Instance</th><th>Holds</th><th>Where</th>
        <th>Shared with</th><th>State</th><th></th></tr></thead>
      <tbody>${(p.instances || []).map((i) => instanceRow(p, i)).join('')}</tbody></table></div>
      ${platformHtml(p)}${slot(saidHtml(p))}
      <div class="db-apis">${window.LiminalApis ? window.LiminalApis.html(p.apis || [], p.apiFacts || {}, { owner: p.name, fold: true }) : ''}</div>`;
  }

  function pluginSideHtml(p) {
    const pairs = [['Engine', esc(p.engineTitle || p.engine || '')], ['Plugin', `${esc(p.name)}${p.version ? ` ${esc(p.version)}` : ''}`],
      ['From', p.origin ? esc(p.origin) : null], ['Contract', p.api ? `plugin API ${esc(p.api)}` : null],
      ['Driver', p.driver ? (!p.driver.name ? "Python's own" : p.driver.ok ? `${esc(p.driver.name)}, installed` : `<span class="warn">${esc(p.driver.why)}</span>`) : null],
      ['Reached by', (p.methods || []).map((m) => `<b>${esc(m.title)}</b>: ${esc(m.about)}`).join('<br>')],
      ['Can hold', (p.holds || []).map((h) => esc(h.title)).join('; ')],
      ['Shares', p.shares ? 'kept to by the helper: a suite on another system attaches as its share says' : esc(p.sharedNote || '')],
      ['Usual port', p.server && p.defaultPort ? esc(p.defaultPort) : null],
      ['What you add', p.variable ? `kept as <code>${esc(p.variable)}</code>, read here` : null],
      ['Changed', `on the platform's Databases section, the one writer of instance settings`],
      ['Data folder', p.dataFolder ? `<code>${esc(p.dataFolder)}</code>` : null]];
    return `<h3>Specifics</h3>${spec(pairs)}
      <h4>How it is asked</h4><div class="p-muted small">Its instances are listed without opening them: a file's presence and size, and on this
        machine alone whether a server listens. Test opens one for reading and asks what it is. A server is never started to be asked, and another
        machine is asked only by a test you start. Nothing is changed from this page: a new database, a connection, a share, a port, a sign-in or
        an instance forgotten is made on the platform's Databases section, the one writer of instance settings, which the links open.</div>`;
  }

  // Test, the one form left here: a read, sent at once (it changes nothing; the action is always "test")
  async function sendTest(p, name) {
    const res = await fetch('/api/databases/instances', { method: 'POST', headers: WRITE_HEADERS,
      body: JSON.stringify({ plugin: p.name, action: 'test', name }) });
    let got = {};
    try { got = await res.json(); } catch (err) { got = {}; }  // not JSON: its status says
    return { http: res.ok, status: res.status, body: got && typeof got === 'object' ? got : {} };
  }

  // A test's answer: what the plugin said, shown under the instances
  async function test(p, name) {
    busy = true;
    said = null;
    render();
    let got;
    try {
      got = await sendTest(p, name);
    } catch (err) {
      got = { http: false, status: 0, body: { error: `The integrator did not answer: ${err.message || err}` } };
    }
    busy = false;
    said = got.http
      ? { plugin: p.name, ok: got.body.ok !== false, said: got.body.said || 'Done.', lines: got.body.lines || [] }
      : { plugin: p.name, ok: false, said: got.body.error || `Refused (${got.status}).`, lines: [] };
    render();
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
    const p = e ? null : pluginOf(sel);
    const t = data.totals || {};
    const cols = [
      `<div class="view-head"><div><h2>Databases</h2>
        <div class="p-muted small">${num(t.databases)} databases · ${bytes(t.size)} · ${esc(growth(t.growth))}</div></div></div>
        ${failed ? `<div class="small warn">${esc(failed)}</div>` : ''}${listHtml()}`,
      p ? `<div class="view-head"><div><h2>${esc(p.title || p.name)}</h2><div class="p-muted small">${esc(p.about || '')}</div></div></div>${pluginMainHtml(p)}`
        : `<div class="view-head"><div><h2>${esc(e ? e.name : '')}</h2>
        <div class="p-muted small">${e ? `${esc(ENGINE[e.engine] || e.engine)} · ${esc((data.groups.find((g) => g.id === e.group) || {}).about || '')}` : ''}</div></div></div>
        ${e ? mainHtml(e) : '<div class="empty">No database in the data folder.</div>'}`,
      p ? pluginSideHtml(p) : e ? sideHtml(e) : '',
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
    if (b) {
      if (b.dataset.db === sel) return;
      sel = b.dataset.db;
      said = null;
      render();
      window.Views.reveal(box.querySelector('.ex-list'));  // on a phone (the columns stacked) the one picked, below the list
      if (!pluginOf(sel)) loadDetail(sel);
      return;
    }
    // a link into the platform's page: in the bound view when the platform's tray is on the rail (the icon bridge,
    // panels.js Views.bound), else as a link (a new tab); a click with a modifier always as a link
    const link = ev.target.closest('a[data-platform]');
    if (link) {
      const plain = ev.button === 0 && !ev.ctrlKey && !ev.metaKey && !ev.shiftKey && !ev.altKey;
      if (plain && window.Views.bound?.('platform', link.dataset.platform)) ev.preventDefault();
      return;
    }
    const act = ev.target.closest('[data-do="test"]');
    const p = pluginOf(sel);
    if (!act || !p || busy) return;
    test(p, act.dataset.inst || null);
  });
  window.addEventListener('liminal:view', (ev) => {
    if (ev.detail.view === 'databases') load();
    else clearTimeout(timer);
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); else clearTimeout(timer); });
  if (document.body.dataset.view === 'databases') load();  // the page opened on this view (before this script listened)
})();
