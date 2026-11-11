'use strict';
/* Plugins view (rail icon: a plug): every data provider plugin, a feed or source (gui/plugins.py).
   On the left the plugins, those with keys and the public ones, each with its state and its plan;
   in the middle the one picked: its plan and where that came from (set by you, found by its scan,
   the default), its limits, what it can do on that plan, its live streams and its last capability
   scan with a Rescan; on the right its part in the delivery scheme (the eras it delivers or stands
   by for), its requests today, the symbols it watches, its keys (set or not, never their values),
   its version, the plugin API it was written for and where it came from (built in, or a package).
   Each plugin refreshes its own plan info: a rescan asks the provider what the key really gets;
   while it runs, each plugin it has still to ask says "scanning" in place of its state.
   A plugin with several feeds (Alpaca: alpaca, and its nights on Blue Ocean, alpaca (blue ocean);
   Tiingo: tiingo and tiingo (forex)) lists them, and names the feed on each capability, scan answer
   and era.
   A plan its scan cannot tell (Tiingo's Power from Commercial) is chosen here: each plan variable
   is a choice, saved like the key script saves it (POST /api/plugins/plan); keys stay in the script.
   Below them the publishers (news and publications: publications.py), each an agency's or a
   vendor's plugin: its feeds with their last and next reads, its release schedule, its scrape
   checks, its limits as last verified with a Recheck, and a Pause. A publisher part of a plugin
   (Massive's headlines, Federal's agencies) is no entry of its own: a section of its plugin's page,
   opened in place (the user, 2026-10-31: "massive headlines should not be separate from massive
   same thing with federal agencies and federal").
   Every plugin is set up here (Setup): each variable it reads, a key or a detail of yours it sends
   (GOV_CONTACT), set or not (never its value), typed and saved as your user environment variable
   (POST /api/plugins/key), or removed. What is typed is sent once and the field cleared.
   The AI assistants (plugin API 2.0; the user, 2026-11-03): each a plugin too, in its own group: Setup
   (its key hidden, its account in the open), Plan (the plan, the model, the caps), the vendor's plans
   and models written in and the date they were verified, Limits, Connect (its capability scan: the key
   tried, the plan read from the server's limits, the tables checked), what it spent today here and on
   the whole machine, its last answers, and a Try it box (POST /api/ai/evaluate).
   Asked again every minute while shown, every few seconds while a rescan or recheck runs. */
(() => {
  const box = document.getElementById('viewPlugins');
  const { esc, safeUrl } = window.LiminalIcons;
  let data = null, sel = null, note = '', timer = null;
  const folds = new Map();  // a part's section opened or closed by hand (by its name); else open when alone
  // Touch screens and the on-screen keyboard (the user, 2026-11-10: "I want to support touch as well as mouse clicking
  // on the interface"; "Support for virtual keyboard on tablets and phones"): each field's keyboard (the library's
  // touch.js; a key kept out of password managers), and why a control is off in words a finger can read (a disabled
  // control's tooltip is a mouse's, when it shows at all).
  const kb = (kind, more) => window.LiminalTouch?.keyboard(kind, more) || '';
  const whyNot = (why) => (why ? `<div class="p-muted small touch-only pl-why">${esc(why)}</div>` : '');
  // a choice declined, or not saved (refused, the integrator away): the picker back on what is in effect, as it was
  // drawn (drawn again alike, the page leaves the picker as it is, showing what it was last set to)
  const undo = (select) => {
    const was = [...select.options].find((o) => o.defaultSelected) || select.options[0];
    if (was) select.value = was.value;
  };

  const STATE = { ready: 'ready', scanning: 'scanning…', partly: 'keys not set', 'no key': 'no key', killed: 'killed', paused: 'paused',
                  refused: 'refused', failing: 'failing', waiting: 'waiting', off: 'not set up', 'no access': 'no API access' };
  // a state as the reason a button is off, in a sentence (whyNot)
  const STATE_WHY = { scanning: 'It is being scanned', partly: 'Some of its keys are not set', killed: 'It is stopped for now',
                      paused: 'It is paused', refused: 'Its provider refused it', failing: 'Its requests are failing',
                      waiting: 'It is waiting for its limits', off: 'It is not set up here' };
  const FEED_STATE = { ok: 'read', unchanged: 'unchanged', waiting: 'waiting', failed: 'failed', refused: 'refused',
                       'not asked yet': 'not asked yet' };
  const PUB = 'pub:';  // a publisher's id in the list (its name may be a feed's too)
  const ORIGIN = { you: 'set by you', scan: 'found by its scan', default: 'default' };
  const JOBS = { day: 'daily bars', minute: '1-minute', second: '1-second' };
  const num = (n) => (n == null ? '–' : Number(n).toLocaleString('en-US', { maximumFractionDigits: 1 }));
  const ago = (iso) => {
    if (!iso) return 'never';
    const h = (Date.now() - Date.parse(iso)) / 36e5;
    return h < 1 ? `${Math.max(1, Math.round(h * 60))} min ago` : h < 48 ? `${Math.round(h)} h ago` : `${Math.round(h / 24)} days ago`;
  };
  const rate = (r) => {
    if (!r || !r[0]) return 'no limit';
    const [n, s] = r;
    return Math.abs(s - 3600) < 120 ? `${num(n)} an hour` : Math.abs(s - 60) < 5 ? `${num(n)} a minute` : `${num(n)} per ${num(s)} s`;
  };
  const ENGINES = { sqlite: 'SQLite', duckdb: 'DuckDB', parquet: 'Parquet', mongodb: 'MongoDB', files: 'files' };
  const size = (n) => {
    let v = n, i = 0;
    while (v >= 1024 && i < 5) { v /= 1024; i++; }
    return `${i === 0 || v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${['B', 'KB', 'MB', 'GB', 'TB', 'PB'][i]}`;
  };
  const days = (d) => (d < 1 / 24 ? 'under an hour' : d < 1 ? `${(d * 24).toFixed(1)} h` : `${num(d)} days`);
  const chip = (state) => `<span class="ex-state pl-${esc(state.replace(' ', '-'))}">${esc(STATE[state] || state)}</span>`;
  const tile = (title, big, small) => `<div class="tile"><h5>${title}</h5><div class="big">${big}</div><small>${small}</small></div>`;
  const plugin = () => data?.plugins.find((p) => p.name === sel) || null;
  const publisher = () => (sel?.startsWith(PUB) ? data?.publishers?.find((p) => PUB + p.name === sel && !parentOf(p)) || null : null);
  const pubNamed = (name) => (data?.publishers || []).find((q) => q.name === name) || null;
  // a plugin's parts: a feed plugin's publishers (Massive's headlines), a publisher's (Federal's agencies)
  const partsOf = (p) => (p.parts || p.publishers || []).map(pubNamed).filter(Boolean);
  const etTime = (iso) => new Date(iso).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' });
  const etDay = (iso) => new Date(iso).toLocaleDateString('en-US', { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric' });
  const until = (iso) => {
    if (!iso) return '–';
    const m = (Date.parse(iso) - Date.now()) / 6e4;
    return m <= 1 ? 'now' : m < 90 ? `in ${Math.round(m)} min` : m < 48 * 60 ? `in ${(m / 60).toFixed(1)} h` : `in ${Math.round(m / 1440)} days`;
  };
  const every = (m) => (m >= 60 ? `${num(m / 60)} h` : `${num(m)} min`);
  const tierOf = (p) => p.plan.vars.find((v) => v.choices.length) || null;
  // a plugin's version, the plugin API it was written for and where it came from
  const release = (p) => `<div class="p-muted small pl-release">Version ${esc(p.version || 'unversioned')} · plugin API ${esc(p.api)} · ${esc(p.origin)}</div>`;
  // a publisher that puts out datasets only (Ken French's library, Datahub's mirrors; the CFTC)
  const setsOnly = (p) => !p.feeds.length && !p.parts.length && p.datasets.length > 0;
  const SOURCE = { research: "a researcher's own datasets", mirror: 'datasets mirrored from their sources', official: "an agency's datasets" };
  const pubKind = (p) => (p.kind === 'vendor' ? `a vendor's headlines, on ${p.account}'s key`
    : setsOnly(p) ? SOURCE[p.kind] || `${p.kind} datasets` : 'official feeds');
  // what a publisher reads: a plugin of several (Federal) its agencies, a vendor's on its feed's key, its feeds, its datasets
  const pubCount = (p) => (p.parts.length ? `${p.parts.length} agencies`
    : p.kind === 'vendor' ? `on ${p.account}'s key · every ${every(Math.min(...p.feeds.map((f) => f.every)))}`
      : p.feeds.length ? `${p.feeds.length} feed${p.feeds.length === 1 ? '' : 's'} · every ${every(Math.min(...p.feeds.map((f) => f.every)))}`
        : p.datasets.length ? `${p.datasets.length} dataset${p.datasets.length === 1 ? '' : 's'}` : 'nothing to read');

  async function load() {
    clearTimeout(timer);
    if (document.body.dataset.view !== 'plugins' || document.hidden) return;
    try {
      data = await api('/api/plugins');
      const part = sel?.startsWith(PUB) ? pubNamed(sel.slice(PUB.length)) : null;
      const up = part && parentOf(part);  // a part picked: its plugin's page, its section open
      if (up) { folds.set(part.name, true); sel = up.id; }
      if (!sel || !(data.plugins.some((p) => p.name === sel) || publisher())) sel = data.plugins[0]?.name || null;
    } catch (err) { showError(err); }
    render();
    timer = setTimeout(load, data?.running || data?.rechecking ? 4_000 : 60_000);
  }

  async function rescan(name) {
    note = '';
    try {
      const q = name ? `?name=${encodeURIComponent(name)}` : '';
      const res = await fetch(`/api/plugins/rescan${q}`, { method: 'POST', headers: { 'X-Liminal-Charts': '1' } });
      const out = await res.json().catch(() => ({}));
      note = res.ok ? `Rescan started: ${name ? plugin()?.title || name : 'every plugin set up here'} (a few requests each).`
        : out.error || res.statusText;
    } catch (err) { note = String(err.message || err); }
    render();
    setTimeout(load, 1_000);
  }

  async function choosePlan(select) {
    const p = data.plugins.find((x) => x.name === select.dataset.planPlugin);
    const variable = select.dataset.var, value = select.value || null;
    const ask = value
      ? `Use ${variable}=${value}? ${p.title} will keep to that plan's limits, in the app and the sync: choose only the plan you pay for.`
      : `Let ${p.title}'s scan or its default decide ${variable} again?`;
    if (!window.confirm(ask)) { undo(select); render(); return; }
    note = '';
    let saved = false;
    try {
      const res = await fetch('/api/plugins/plan', { method: 'POST', headers: WRITE_HEADERS,
        body: JSON.stringify({ plugin: p.name, variable, value }) });
      const out = await res.json().catch(() => ({}));
      saved = res.ok;
      note = res.ok ? `${variable} ${value ? `set to ${value}` : 'left to its scan or default'}: the app uses it now, the sync from its next run.`
        : out.error || res.statusText;
    } catch (err) { note = String(err.message || err); }
    if (!saved) undo(select);  // not saved: the plan in effect shown, not the one picked
    await load();
  }

  async function post(path, done) {
    note = '';
    try {
      const res = await fetch(path, { method: 'POST', headers: WRITE_HEADERS, body: '{}' });
      const out = await res.json().catch(() => ({}));
      note = res.ok ? done : out.error || res.statusText;
    } catch (err) { note = String(err.message || err); }
    render();
    setTimeout(load, 1_000);
  }

  async function saveKey(owner, variable, value) {
    note = '';
    try {
      const res = await fetch('/api/plugins/key', { method: 'POST', headers: WRITE_HEADERS,
        body: JSON.stringify({ plugin: owner, variable, value }) });
      const out = await res.json().catch(() => ({}));
      const scans = data.plugins.find((x) => x.name === owner)?.scan.has;
      note = res.ok ? (value == null ? `${variable} removed from your user environment variables.`
        : `${variable} saved in your user environment variables: the app uses it now, the sync from its next run${
          scans ? '; Rescan to see what it gets' : ''}.`) : out.error || res.statusText;
    } catch (err) { note = String(err.message || err); }
    await load();
  }

  // -- setup: what each plugin reads that you set ------------------------------------------------
  // each variable set or not (never its value); a key typed hidden, a detail of yours in the open
  function setupHtml(owner, keys, account) {
    const off = data.offline ? ' disabled title="This offline copy of the app saves no settings"' : '';
    const rows = keys.map((k) => `<tr data-key-row="${esc(k.name)}">
        <td><code>${esc(k.name)}</code><div class="p-muted small">${esc(k.label)}</div></td>
        <td>${k.set ? '<span class="up">set</span>' : '<span class="down">not set</span>'}</td>
        <td><input class="pl-key" type="${k.secret ? 'password' : 'text'}" data-var="${esc(k.name)}" autocomplete="off" spellcheck="false"
          ${kb(k.secret ? 'secret' : 'plain', { autocomplete: null, spellcheck: null })}
          maxlength="512" placeholder="${esc(k.hint || (k.set ? 'type a new one to replace it' : `your ${k.label}`))}"${off}></td>
        <td class="pl-key-btns"><button type="button" data-save-key="${esc(k.name)}" data-owner="${esc(owner)}"${off}>${k.set ? 'Replace' : 'Save'}</button>
          ${k.set ? `<button type="button" data-remove-key="${esc(k.name)}" data-owner="${esc(owner)}"${off}>Remove</button>` : ''}</td></tr>`).join('');
    const where = account ? `It reads on ${esc(account.title)}'s key: <button type="button" class="pl-link" data-plugin="${esc(account.id)}">set it up there</button>.` : '';
    if (!rows) return `<h4>Setup</h4><div class="p-muted small">${where || 'Nothing to set: it needs no key and nothing of yours.'}</div>`;
    return `<h4>Setup</h4><table class="act-table ex-mini pl-setup"><tbody>${rows}</tbody></table>
      <div class="p-muted small">Saved as your user environment variables, where the key script saves them: the app uses them at once,
        the sync from its next run. A value is never shown back, only whether it is set.${where ? ` ${where}` : ''}</div>`;
  }

  // -- left: the plugins ---------------------------------------------------------------------
  // -- its fetching (gui/queue.py plugin_status): the user, 2026-10-31: "the plugin status will be
  // available in the plugins:: plugin page". Whether it is fetching (and what), waiting, paused or
  // stopped; its share of the queue; its feeds' requests against their caps, their limits, its runs,
  // its lines of the log, and the audits of its feeds (a second feed's copy compared). Asked every
  // few seconds while its page is open.
  const FETCHING_EVERY_MS = 5000;
  const F_STATE = { fetching: 'fetching', waiting: 'waiting', idle: 'idle', paused: 'paused', halted: 'halted', killed: 'stopped' };
  const Q_LABEL = { running: 'fetching', waiting: 'waiting', held: 'held', failed: 'failed', stopped: 'stopped', done: 'done' };
  const fetchState = { name: null, data: null, timer: null, audits: null, auditsAt: 0 };
  const shown = () => (publisher() || plugin())?.name || null;
  const meter = (f, cls = '') => `<span class="meter ${cls}"><span style="width:${Math.max(0, Math.min(100, (f || 0) * 100)).toFixed(1)}%"></span></span>`;
  const dur = (s) => (s == null ? '–' : s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
  const label = (s) => (typeof feedLabel === 'function' ? feedLabel(s) : s);  // app.js: alpaca (blue ocean)
  const etWhen = (iso) => (iso ? `${etDay(iso)} ${etTime(iso)}` : '–');

  async function loadFetching() {
    clearTimeout(fetchState.timer);
    const name = shown();
    if (!name || document.body.dataset.view !== 'plugins' || document.hidden) return;
    try {
      const got = await api(`/api/plugin-fetching?name=${encodeURIComponent(name)}`);
      if (Date.now() - fetchState.auditsAt > 60_000) {
        fetchState.audits = await api('/api/audits').catch(() => fetchState.audits);
        fetchState.auditsAt = Date.now();
      }
      if (shown() === name) {
        fetchState.name = name;
        fetchState.data = got;
        render();
      }
    } catch { /* the next try: the page keeps what it showed */ }
    fetchState.timer = setTimeout(loadFetching, FETCHING_EVERY_MS);
  }

  function fetchingHtml(name) {
    const f = fetchState.name === name ? fetchState.data : null;
    if (fetchState.name !== name) setTimeout(loadFetching, 0);
    if (!f) return '<h4>Fetching</h4><div class="p-muted small">Asking…</div>';
    const c = f.counts || {};
    const counts = ['running', 'waiting', 'held', 'failed', 'stopped', 'done'].filter((s) => c[s]).map((s) => `${num(c[s])} ${Q_LABEL[s]}`).join(' · ');
    const qRow = (r) => `<tr><td><b>${esc(r.title)}</b><div class="p-muted small">${esc(r.partLabel || r.kind)}</div></td>
      <td><span class="ex-state pl-q-${esc(r.state)}">${esc(Q_LABEL[r.state] || r.state)}</span></td>
      <td class="small">${esc(r.error || r.detail || '')}</td>
      <td class="num">${r.requests ? num(r.requests) : '–'}</td>
      <td class="small">${r.state === 'running' ? `since ${etTime(r.started_at)}` : r.finished_at ? ago(r.finished_at) : r.not_before ? `not before ${etWhen(r.not_before)}` : ''}</td></tr>`;
    const rows = (f.rows || []).filter((r) => r.state !== 'done').slice(0, 15);
    const feeds = (f.feeds || []).map((x) => `<tr><td><b>${esc(x.label)}</b>${x.running ? ' <span class="ex-state pl-q-running">running</span>' : ''}<div class="p-muted small">${esc(x.tier)} plan</div></td>
      <td>${x.perDay ? meter(x.today / x.perDay, x.today / x.perDay > 0.9 ? 'warn' : '') : ''}<div class="small">${num(x.today)} of ${num(x.perDay)} today</div></td>
      <td class="num">${num(x.perRun)}</td><td>${x.rpm ? `${num(x.rpm)} a minute` : 'no rate limit'}</td></tr>`).join('');
    const t = Date.now() / 1000;
    const limits = (f.limits || []).map((l) => {
      const w = l.window || {};
      const full = w.allowed && w.used >= w.allowed;
      const wait = l.wait ? Math.max(0, Math.round(l.wait.until - t)) : 0;
      const next = wait ? `<span class="warn">in ${wait} s</span><div class="p-muted small">${esc(l.wait.reason || '')}</div>`
        : full ? `<span class="warn">in ${Math.ceil(w.freeIn)} s</span><div class="p-muted small">window full</div>` : 'now';
      const m = l.month;
      return `<tr><td><b>${esc(label(l.source))}</b><div class="p-muted small">${esc(l.published)}</div></td><td>${esc(l.keptTo)}</td>
        <td>${w.allowed ? `${meter(w.used / w.allowed, full ? 'warn' : '')}<div class="small">${num(w.used)} of ${num(w.allowed)}</div>` : '<span class="p-muted small">no limit</span>'}</td>
        <td>${next}</td><td class="small">${m && m.allowance ? `${num(m.used)} of ${num(m.allowance)} this month · ${Math.round((m.share ?? 0) * 100)}% by its end` : 'no daily cap'}</td></tr>`;
    }).join('');
    const runs = (f.runs || []).map((r) => `<tr><td>${etWhen(r.started_at)}</td><td>${esc(label(r.source || ''))}<div class="p-muted small">${esc(r.command)}</div></td>
      <td><span class="ex-state pl-run-${esc(r.status)}">${esc(r.status)}</span></td><td class="num">${num(r.requests)}</td><td class="num">${num(r.rows)}</td>
      <td class="num">${dur(r.seconds)}</td><td class="small">${esc(r.message || '')}</td></tr>`).join('');
    const mine = new Set((f.feeds || []).map((x) => x.name));
    const audits = (fetchState.audits?.audits || []).filter((a) => mine.has(a.owner) || mine.has(a.auditor)).slice(0, 10);
    const auditRows = audits.map((a) => `<tr><td>${ago(a.at)}</td><td><b>${esc(a.ticker)}</b></td><td>${esc(a.owner)} → ${esc(a.auditor)}</td>
      <td><span class="ex-state pl-audit-${esc(a.verdict)}">${esc(a.verdict)}</span></td><td class="num">${num(a.matched)}</td>
      <td class="small">${a.report?.box?.c ? `close ${a.report.box.c.median >= 0 ? '+' : ''}${a.report.box.c.median.toFixed(2)} bps (p95 ${a.report.box.c.p95.toFixed(2)})` : ''}</td></tr>`).join('');
    const log = (f.log || []).map((line) => `<div class="${/error|refused|stopped|failed|could not/i.test(line) ? 'bad' : /: [1-9][\d,]* (rows|new)/.test(line) ? 'good' : ''}">${esc(line)}</div>`).join('');
    return `<h4>Fetching</h4>
      <div class="pl-fetching"><span class="ex-state pl-f-${esc(f.state)}">${esc(F_STATE[f.state] || f.state)}</span> <span class="small">${esc(f.why || '')}</span>
        ${counts ? `<div class="p-muted small">Its queue: ${esc(counts)} · <a href="#" data-goto-fetcher="${esc(name)}">all of it in the Fetcher</a></div>` : '<div class="p-muted small">Nothing of it on the queue yet.</div>'}</div>
      ${rows.length ? `<table class="act-table ex-mini pl-queue"><thead><tr><th>Resource</th><th>State</th><th>Detail</th><th class="num">Requests</th><th>When</th></tr></thead><tbody>${rows.map(qRow).join('')}</tbody></table>` : ''}
      ${feeds ? `<table class="act-table ex-mini"><thead><tr><th>Feed</th><th>Requests today</th><th class="num">Per run</th><th>Rate</th></tr></thead><tbody>${feeds}</tbody></table>` : ''}
      ${limits ? `<h4>Request windows now</h4><table class="act-table ex-mini"><thead><tr><th>Server</th><th>Kept to</th><th>Window now</th><th>Next request</th><th>Month</th></tr></thead><tbody>${limits}</tbody></table>` : ''}
      ${runs ? `<h4>Runs</h4><table class="act-table ex-mini"><thead><tr><th>Started (ET)</th><th>Feed</th><th>Status</th><th class="num">Requests</th><th class="num">Rows</th><th class="num">Took</th><th>Note</th></tr></thead><tbody>${runs}</tbody></table>` : ''}
      ${auditRows ? `<h4>Audits of its feeds</h4><table class="act-table ex-mini"><thead><tr><th>When</th><th>Symbol</th><th>Stored → checked by</th><th>Verdict</th><th class="num">Bars</th><th>Difference</th></tr></thead><tbody>${auditRows}</tbody></table>` : ''}
      ${log ? `<h4>Its log</h4><div class="act-log pl-log">${log}</div>` : ''}`;
  }

  function listHtml() {
    // each plugin once: its parts (Massive's headlines, Federal's agencies) are sections of its page
    const pubButton = (p) => `<button type="button" class="ex-venue${PUB + p.name === sel ? ' active' : ''}" data-plugin="${esc(PUB + p.name)}" title="${esc(p.about)}">
          <span class="ex-name">${esc(p.title)}</span>${chip(p.state)}
          <span class="ex-count">${esc(pubCount(p))}</span></button>`;
    const groups = data.groups.map((g) => {
      const ps = data.plugins.filter((p) => p.group === g);
      if (!ps.length) return '';
      return `<h4 class="ex-group">${esc(g)}</h4>` + ps.map((p) => {
        const t = tierOf(p);
        const plan = t ? `${t.value} · ${ORIGIN[t.origin]}` : p.plan.tier;
        const parts = partsOf(p);
        const also = parts.length ? ` · ${parts.every((q) => q.kind === 'vendor') ? 'headlines' : `${parts.length} publishers`}` : '';
        return `<button type="button" class="ex-venue${p.name === sel ? ' active' : ''}" data-plugin="${esc(p.name)}" title="${esc(p.about)}">
          <span class="ex-name">${esc(p.title)}</span>${chip(p.state)}
          <span class="ex-count">${esc(plan)}${p.feeds.length > 1 ? ` · ${p.feeds.length} feeds` : ''}${esc(also)}</span></button>`;
      }).join('');
    }).join('');
    const top = (data.publishers || []).filter((p) => !parentOf(p));
    const pubs = top.filter((p) => !setsOnly(p)).map(pubButton).join('');
    const sets = top.filter(setsOnly).map(pubButton).join('');
    const news = (pubs ? `<h4 class="ex-group">News and publications</h4>${pubs}` : '')
      + (sets ? `<h4 class="ex-group">Datasets</h4>${sets}` : '');
    const left = data.errors.length ? `<h4 class="ex-group">Left out</h4>${data.errors.map((e) => `
      <div class="pl-error"><b>${esc(e.where)}</b><div class="p-muted small">${esc(e.why)}</div></div>`).join('')}` : '';
    return groups + news + left;
  }

  // -- middle: the plugin picked -------------------------------------------------------------
  // a plan variable with choices is a choice: "automatic" leaves it to the scan or the default
  function planCell(p, v) {
    if (!v.choices.length) return `<b>${esc(v.value ?? '–')}</b>`;
    const found = p.plan.found?.[v.name];
    const auto = `automatic: ${found || v.choices[0]} (${found ? 'its scan' : 'the default'})`;
    const mine = v.origin === 'you';
    return `<select class="pl-choose" data-plan-plugin="${esc(p.name)}" data-var="${esc(v.name)}"${data.offline
      ? ' disabled title="This offline copy of the app saves no settings"' : ''}>
        <option value=""${mine ? '' : ' selected'}>${esc(auto)}</option>
        ${v.choices.map((c) => `<option value="${esc(c)}"${mine && v.value === c ? ' selected' : ''}>${esc(c)}</option>`).join('')}
      </select>`;
  }

  function planHtml(p) {
    const name = p.name;
    const rows = p.plan.vars.map((v) => `<tr><td><code>${esc(v.name)}</code></td><td>${planCell(p, v)}</td>
        <td class="pl-origin pl-${esc(v.origin)}">${esc(ORIGIN[v.origin])}</td></tr>`).join('');
    const differs = p.plan.differs.map((d) => `<div class="pl-warn">Your <code>${esc(d.name)}</code> is <b>${esc(d.yours)}</b>,
        but its scan found <b>${esc(d.found)}</b>. Yours is used; change it if the scan is right.</div>`).join('');
    const found = Object.entries(p.plan.found || {});
    return `<h4>Plan</h4>
      ${rows ? `<table class="act-table ex-mini pl-plan"><thead><tr><th>Variable</th><th>Plan</th><th>From</th></tr></thead><tbody>${rows}</tbody></table>`
        : `<div class="p-muted small">One fixed plan: ${esc(p.plan.tier)}.</div>`}
      ${found.length ? `<div class="p-muted small">Its last scan found ${found.map(([k, v]) => `<code>${esc(k)}=${esc(v)}</code>${
        p.plan.alike?.[k]?.length ? ` (or ${p.plan.alike[k].map(esc).join(', ')}: their answers are the same, so the lower limits are used)` : ''}`).join(', ')}:
        used wherever you have not set it yourself.</div>` : ''}
      ${differs}
      ${p.plan.note ? `<div class="pl-note">${esc(p.plan.note)}.</div>` : ''}
      ${rows ? `<div class="p-muted small">Choose a plan above: it is saved as your user environment variable. Keys are set
        in Setup, or with the key script: <code>${esc(data.keyScript)} ${esc(name)}</code></div>` : ''}`;
  }

  // Its other settings (Provider.options; the user, 2026-11-03: the view had no way to turn Tiingo's
  // A-share listings on): chosen here like a plan where the record gives choices, else the key script's.
  function optionCell(p, o) {
    if (!o.choices.length) return `<code>${esc(o.value ?? 'not set')}</code>`;
    const mine = o.origin === 'you';
    return `<select class="pl-option" data-plan-plugin="${esc(p.name)}" data-var="${esc(o.name)}"${data.offline
      ? ' disabled title="This offline copy of the app saves no settings"' : ''}>
        <option value=""${mine ? '' : ' selected'}>${esc(`default: ${o.choices[0]}`)}</option>
        ${o.choices.map((c) => `<option value="${esc(c)}"${mine && o.value === c ? ' selected' : ''}>${esc(c)}</option>`).join('')}
      </select>`;
  }

  function optionsHtml(p) {
    if (!p.options?.length) return '';
    const rows = p.options.map((o) => `<tr><td><code>${esc(o.name)}</code></td><td>${optionCell(p, o)}</td>
        <td class="pl-origin pl-${esc(o.origin)}">${esc(ORIGIN[o.origin])}</td></tr>`).join('');
    return `<h4>Options</h4>
      <table class="act-table ex-mini pl-plan"><thead><tr><th>Variable</th><th>Value</th><th>From</th></tr></thead><tbody>${rows}</tbody></table>
      <div class="p-muted small">Its own settings, saved as your user environment variables like a plan: the app uses them at
        its next look, the sync from its next run${p.options.some((o) => !o.choices.length)
          ? `; one without choices is set with the key script: <code>${esc(data.keyScript)} ${esc(p.name)}</code>` : ''}.</div>`;
  }

  async function chooseOption(select) {
    const p = data.plugins.find((x) => x.name === select.dataset.planPlugin);
    const variable = select.dataset.var, value = select.value || null;
    const ask = value ? `Set ${variable}=${value} for ${p.title}?` : `Leave ${variable} to its default for ${p.title}?`;
    if (!window.confirm(ask)) { undo(select); render(); return; }
    note = '';
    let saved = false;
    try {
      const res = await fetch('/api/plugins/plan', { method: 'POST', headers: WRITE_HEADERS,
        body: JSON.stringify({ plugin: p.name, variable, value }) });
      const out = await res.json().catch(() => ({}));
      saved = res.ok;
      note = res.ok ? `${variable} ${value ? `set to ${value}` : 'left to its default'}: the app uses it now, the sync from its next run.`
        : out.error || res.statusText;
    } catch (err) { note = String(err.message || err); }
    if (!saved) undo(select);  // not saved: the value in effect shown, not the one picked
    await load();
  }

  function limitsHtml(p) {
    const l = p.limits;
    if (l.requests_a_day == null) return `<h4>Limits</h4><div class="p-muted small">${esc(l.published || 'Not set up here.')}</div>`;
    return `<h4>Limits</h4><div class="tiles ex-tiles pl-tiles">
        ${tile('Requests a day', num(l.requests_a_day), l.per_day ? `cap ${num(l.per_day)}` : 'its rate allows')}
        ${tile('Rate kept', rate(l.rate), 'a little inside its server\'s')}
        ${tile('A run', num(l.per_run), 'requests at most')}
        ${tile('Symbols a month', l.symbols_a_month ? num(l.symbols_a_month) : 'any', l.symbols_a_month ? 'different symbols' : 'no monthly cap')}
      </div>${l.published ? `<div class="p-muted small">Its server publishes: ${esc(l.published)}</div>` : ''}`;
  }

  // a plugin with several feeds (alpaca, and alpaca (blue ocean) on its keys): each listed by the
  // plugin's name, with its state ("the source isn't blue ocean its alpaca w/ blue ocean")
  const several = (p) => p.feeds.length > 1;
  function feedsHtml(p) {
    if (!several(p)) return '';
    return `<h4>Feeds</h4><table class="act-table ex-mini pl-feedlist"><tbody>${p.feeds.map((f) => `<tr>
        <td><b>${esc(f.label)}</b>${f.archive ? ' <span class="p-muted small">closed archive</span>' : ''}<div class="p-muted small">${esc(f.about)}</div></td>
        <td>${chip(f.state)}</td></tr>`).join('')}</tbody></table>
      <div class="p-muted small">Each a feed of its own (its own bars, symbols and requests), on this plugin's keys and limits${
        p.feeds.some((f) => f.archive) && !p.archive ? '; a closed archive fills each symbol once, a live feed keeps it current' : ''}.</div>`;
  }

  // the publishers in a feed plugin (Massive's headlines): sections of its page
  function publishersHtml(p) {
    const pubs = partsOf(p);
    if (!pubs.length) return '';
    return `<h4>${pubs.every((q) => q.kind === 'vendor') ? 'Headlines' : 'Publishers'}</h4>${pubs.map((q) => partHtml(q, pubs.length === 1)).join('')}
      <div class="p-muted small">On this plugin's key and limits; each keeps its own scrape checks, pause and recheck.</div>`;
  }

  function capabilitiesHtml(p) {
    if (!p.capabilities.length) return '';
    const feed = (c) => (several(p) ? `<td>${esc(c.feed)}</td>` : '');
    return `<h4>What it can do on this plan${p.capabilitiesWithKey ? ', once its key is set' : ''}</h4>${p.capabilitiesWithKey
      ? '<div class="p-muted small">Its key is not set, so it serves nothing yet: this is what the plan gives with one.</div>' : ''}
      <table class="act-table ex-mini"><thead><tr>${several(p) ? '<th>Feed</th>' : ''}<th>Asset</th><th>Bars</th>
        <th>Market</th><th>From</th><th>Fresh</th><th class="num">A request</th></tr></thead><tbody>
      ${p.capabilities.map((c) => `<tr>${feed(c)}<td>${esc(c.asset)}</td><td>${esc(JOBS[c.resolution] || c.resolution)}</td><td>${esc(c.market)}</td>
        <td>${esc(c.since)}</td><td>${esc(c.fresh)}</td><td class="num">${c.perRequest == null ? 'whole history' : `${num(c.perRequest)} bars`}</td></tr>`).join('')}
      </tbody></table>
      ${p.streams.length ? `<h4>Live</h4>${p.streams.map((s) => `<div class="small"><b>${esc(s.asset)}</b> ${esc(s.what)}</div>`).join('')}` : ''}`;
  }

  function scanHtml(p) {
    if (!p.scan.has) return `<h4>Capability scan</h4><div class="p-muted small">${esc(p.title)} has none: ${p.keys.length
      ? 'one fixed plan on its key, nothing a scan could tell' : 'a public feed with one fixed plan'}.</div>`;
    const can = p.configured && p.state !== 'no key' && !data.offline && !data.running;
    const why = data.offline ? 'This offline copy of the app sends no requests' : !p.configured || p.state === 'no key'
      ? 'Its keys are not set' : data.running ? 'A rescan is running' : 'Ask the provider again what the key gets (a few requests)';
    const rows = p.scan.results.map((r, i) => `${several(p) && r.feed !== p.scan.results[i - 1]?.feed
        ? `<tr class="pl-feedrow"><th colspan="3">${esc(r.feed)}</th></tr>` : ''}<tr><td>${esc(r.what)}</td>
        <td>${r.available ? '<span class="up">yes</span>' : '<span class="down">no</span>'}</td><td class="p-muted">${esc(r.detail)}</td></tr>`).join('');
    return `<h4 class="pl-scan-head"><span>Capability scan</span>
        <button type="button" data-rescan="${esc(p.name)}"${can ? '' : ' disabled'} title="${esc(why)}">${p.scan.running ? 'Scanning…' : 'Rescan'}</button></h4>
      ${can ? '' : whyNot(why)}
      <div class="p-muted small">${p.scan.at ? `Last asked ${ago(p.scan.at)}` : 'Not scanned yet'}: on each start of the app, weekly in the sync, and here${
        several(p) ? ` (its ${p.feeds.length} feeds together)` : ''}.</div>
      ${rows ? `<table class="act-table ex-mini"><tbody>${rows}</tbody></table>` : ''}`;
  }

  // -- a publisher (news and publications) ------------------------------------------------------
  // a plugin of several publishers (Federal): its agencies, sections of its page
  function partsHtml(p) {
    return `<h4>Agencies</h4>${partsOf(p).map((q) => partHtml(q, false)).join('')}
      <div class="p-muted small">Each agency keeps its own scrape checks, limits, pause and recheck: open it for them. Pausing ${esc(p.title)} pauses them all.</div>`;
  }

  // a publisher part of a plugin (Massive's headlines, Federal's agencies): a section of that plugin's
  // page, never an entry of its own; its summary says what it reads and when, opened its feeds,
  // checks, limits, pause and terms (open when it is its plugin's only part, or as you left it)
  function partHtml(q, alone) {
    const last = q.feeds.map((f) => f.at).filter(Boolean).sort().pop();
    const next = q.feeds.map((f) => f.next).filter(Boolean).sort()[0];
    const when = q.feeds.length ? ` · ${last ? `read ${ago(last)}` : 'never read'}${q.state !== 'paused' && next ? ` · next ${until(next)}` : ''}`
      + ` · ${num(q.requestsToday)} requests today` : '';
    const open = folds.has(q.name) ? folds.get(q.name) : alone;
    return `<details class="pl-part" data-part="${esc(q.name)}"${open ? ' open' : ''}>
      <summary><span class="ex-name">${esc(q.title)}</span>${chip(q.state)}<span class="p-muted small">${esc(pubCount(q) + when)}</span></summary>
      <div class="p-muted small">${esc(q.about)}</div>${stopHtml(q)}
      <div class="pl-part-pause">${pauseHtml(q)}</div>
      ${q.feeds.length ? readsHtml(q) : datasetsHtml(q)}${termsHtml(q)}</details>`;
  }

  // the datasets a publisher puts out (the CFTC's Commitments of Traders), as the Datasets view has them
  function datasetsHtml(p) {
    const rows = p.datasetRows || [];
    if (!p.datasets.length) return '';
    return `<h4>Datasets</h4><table class="act-table ex-mini"><thead><tr><th>Dataset</th><th class="num">Values</th><th>Fetched</th><th>Files due</th></tr></thead><tbody>
      ${p.datasets.map((n) => rows.find((r) => r.name === n) || { name: n, title: n }).map((d) => `<tr><td><b>${esc(d.title)}</b><div class="p-muted small">${esc(d.licence || '')}</div></td>
        <td class="num">${d.values != null ? num(d.values) : '–'}</td><td>${d.fetched ? ago(d.fetched) : 'not yet'}</td><td>${d.due != null ? num(d.due) : '–'}</td></tr>`).join('')}</tbody></table>
      <div class="p-muted small">Fetched with the datasets (<code>datasets fetch</code>, then on their own cadence) and shown in the Datasets view;
        their files only from ${p.policy.hosts.map((h) => `<code>${esc(h)}</code>`).join(', ') || 'its hosts'}, over HTTPS.</div>`;
  }

  // the plugin a publisher is part of, as the registry lists it: a publisher (Federal) or a feed
  // plugin (Massive): {id, title}; null for a plugin of its own
  function parentOf(p) {
    if (!p.partOf) return null;
    const pub = (data?.publishers || []).find((q) => (q.parts || []).includes(p.name));
    if (pub) return { id: PUB + pub.name, title: pub.title };
    const feed = (data?.plugins || []).find((q) => (q.publishers || []).includes(p.name));
    return feed ? { id: feed.name, title: feed.title } : null;
  }

  // a publisher's setup: what it reads that you set (its parts' too); a vendor's points at its feed's keys
  function pubSetupHtml(p) {
    const acct = p.kind === 'vendor' ? data.plugins.find((q) => q.name === p.account || q.feeds.some((f) => f.name === p.account)) : null;
    return setupHtml(p.name, p.keys || [], acct ? { id: acct.name, title: acct.title } : null);
  }

  // -- what a plugin attaches to (liminal-libs 1.49.0, plugin API 2.2; the user, 2026-11-11: "each plugin should reflect
  // the the API its attached to, and present links that provide documentation ... it should also expose more of what
  // the API is about"): its APIs and its parts', each once, after what its page already shows, drawn by the library's
  // apis.js (a "more of the API" box opened stays open when the page is drawn again)
  function apisHtml(p, parts = []) {
    // a server older than the cards sends no `apis` (the page's files are read from the checkout at each request): none
    if (!window.LiminalApis || ![p, ...parts].some((e) => Array.isArray(e.apis))) return '';
    const seen = new Map(), facts = {};
    [p, ...parts].forEach((e) => {
      (e.apis || []).forEach((a) => { if (!seen.has(a.name)) seen.set(a.name, a); });
      Object.assign(facts, e.apiFacts || {});
    });
    const older = `Written for plugin API ${p.api || 'before 2.2'}: it does not say yet what it attaches to.`;
    return window.LiminalApis.html([...seen.values()], facts, { owner: p.name, empty: seen.size ? '' : older });
  }
  const pubPartsOf = (p) => (data?.publishers || []).filter((q) => q.partOf === p.name && q.name !== p.name);
  // where a key is made, a link when it is an https address
  const signupHtml = (s) => {
    const u = window.LiminalApis ? window.LiminalApis.safeUrl(s) : '';
    return u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(s)}</a>` : esc(s);
  };

  function pubMainHtml(p) {
    return pubBodyHtml(p) + apisHtml(p, pubPartsOf(p));
  }
  function pubBodyHtml(p) {
    if (p.parts.length) return `${(p.keys || []).length ? pubSetupHtml(p) : ''}${fetchingHtml(p.name)}${partsHtml(p)}${databasesHtml(p)}`;
    if (!p.feeds.length) {
      return `${fetchingHtml(p.name)}${datasetsHtml(p)}${databasesHtml(p)}${p.policy.terms ? `<h4>Terms</h4><div class="small">${esc(p.policy.terms)}</div>` : ''}`;
    }
    return `${pubSetupHtml(p)}${fetchingHtml(p.name)}${readsHtml(p)}${databasesHtml(p)}`;
  }

  // where a plugin's data is (registry.plugin_databases, API 1.8; the user, 2026-10-31: "the plugin
  // will point to the databases it stores the data in"): each database, what it holds of the plugin's,
  // its engine and its size here; a shared one (the main file, gov.db) holds only some rows of it
  function databasesHtml(p) {
    const dbs = p.databases || [];
    if (!dbs.length) return storesHtml(p.name);
    // on a touch screen each one's path is under its name (a finger hovers nothing), and what shared means is said
    const rows = dbs.map((d) => `<tr><td><code title="${esc(d.path || d.where)}">${esc(d.where)}</code>${d.shared
      ? ` <span class="chip" title="The app's and other plugins' too: only this plugin's rows in it are its">shared</span>` : ''}
      <div class="p-muted small touch-only pl-path">${esc(d.path || d.where)}</div></td>
      <td class="small">${esc(d.holds)}</td><td>${esc(ENGINES[d.engine] || d.engine)}</td>
      <td class="num">${d.bytes == null ? `<span class="p-muted">${d.engine === 'mongodb' ? 'a server' : 'none yet'}</span>` : size(d.bytes)}</td></tr>`).join('');
    return `${storesHtml(p.name)}<h4>Databases</h4><table class="act-table ex-mini pl-dbs"><thead><tr><th>Database</th><th>What it holds</th><th>Engine</th><th class="num">Size</th></tr></thead>
      <tbody>${rows}</tbody></table><div class="p-muted small">In the data folder, beside the main file<span class="mouse-only">; hover a name for its path here</span>.${
        dbs.some((d) => d.shared) ? '<span class="touch-only"> Shared: the app\'s and other plugins\' too, only this plugin\'s rows in it are its.</span>' : ''}</div>`;
  }

  // -- its stores (stores.py): the user, 2026-10-31: "each module needs to support sqlite or mongo
  // or duck db based on data type", "lets have a 'store' picker so i can choose manually, but lets
  // try to use the optimal data store for the data type". Each data type it keeps, the store picked
  // (or the optimal, left to the app), where it is now, and how far a move has gone.
  const STORES_EVERY_MS = 60_000;
  const storeState = { name: null, data: null, at: 0, busy: false };
  const gb = (b) => (b == null ? '–' : b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : `${(b / 1e6).toFixed(1)} MB`);

  async function loadStores(name) {
    try {
      const got = await api(`/api/plugin-stores?name=${encodeURIComponent(name)}`);
      Object.assign(storeState, { name, data: got, at: Date.now() });
      if (shown() === name) render();
    } catch { /* the next look */ }
  }

  function storeHeld(r) {
    if (r.type === 'bars') {
      const feeds = Object.entries(r.progress || {});
      if (!feeds.length) return '<span class="p-muted">no bars yet</span>';
      return feeds.map(([f, x]) => `<div><b>${esc(label(f))}</b>: ${x.months ? `${num(x.months)} closed month${x.months === 1 ? '' : 's'} in DuckDB
        (${esc(x.oldest)} to ${esc(x.newest)}), ${num(x.bars)} bars, ${gb(x.bytes)}` : r.effect === 'duckdb' ? 'none in DuckDB yet: the hourly sync copies a few closed months a run, newest first'
        : 'every month in SQLite'}${x.stale ? ` · ${num(x.stale)} written since, read from SQLite until copied again` : ''}</div>`).join('');
    }
    if (r.type === 'documents') {
      const c = r.counts || {};
      return Object.entries(c).map(([e, n]) => `${esc(ENGINES[e] || e)}: ${n == null ? '<span class="p-muted">not running</span>' : num(n)}`).join(' · ');
    }
    return '';
  }

  function storesHtml(name) {
    if (storeState.name !== name || Date.now() - storeState.at > STORES_EVERY_MS) {
      if (!storeState.busy) { storeState.busy = true; setTimeout(() => loadStores(name).finally(() => { storeState.busy = false; }), 0); }
    }
    const got = storeState.name === name ? storeState.data : null;
    if (!got) return '<h4>Stores</h4><div class="p-muted small">Asking…</div>';
    if (!got.stores.length) return '';
    const off = data.offline ? ' disabled title="This offline copy of the app saves no settings"' : '';
    const rows = got.stores.map((r) => {
      const opts = [`<option value=""${r.picked ? '' : ' selected'}>Optimal: ${esc(ENGINES[r.optimal] || r.optimal)}</option>`,
        ...r.engines.map((e) => `<option value="${esc(e.name)}"${r.picked === e.name ? ' selected' : ''}${e.ok ? '' : ' disabled'}>${esc(e.title)}${e.ok ? '' : ` (${esc(e.why)})`}</option>`)];
      const title = (e) => r.engines.find((x) => x.name === e)?.title || ENGINES[e] || e;
      const means = r.engines.find((e) => e.name === r.effect)?.means || '';
      const single = r.engines.length < 2;
      return `<tr><td><b>${esc(r.title)}</b></td>
        <td>${single ? esc(r.engines[0].title) : `<select class="pl-store" data-store-plugin="${esc(name)}" data-store-type="${esc(r.type)}"${off}>${opts.join('')}</select>`}</td>
        <td>${esc(title(r.effect))}${r.wanted !== r.effect ? ` <span class="warn">moving to ${esc(title(r.wanted))}</span>` : ''}
          <div class="p-muted small">${esc(means)}</div></td>
        <td class="small">${storeHeld(r)}</td></tr>`;
    }).join('');
    const m = got.engines.mongodb || {};
    const mongo = !m.installed ? 'MongoDB: not installed' : m.running
      ? `MongoDB ${esc(m.version)}: running on ${esc(m.host)}:${m.port}, ${num(m.residentMB)} MB in memory (cache capped at ${m.cacheGB} GB), the app's documents ${gb(m.storageBytes)}`
      : `MongoDB ${esc(m.version)}: not running (the app starts it when a store uses it)`;
    return `<h4>Stores</h4><table class="act-table ex-mini pl-stores"><thead><tr><th>Data</th><th>Store</th><th>In use</th><th>Held</th></tr></thead><tbody>${rows}</tbody></table>
      <div class="p-muted small">${got.stores.filter((r) => r.engines.length > 1).map((r) => `${esc(r.title)}: ${esc(ENGINES[r.engines[0].name])} is optimal for ${esc(r.why)}.`).join(' ')}
        A store left keeps its copy. SQLite ${esc(got.engines.sqlite)} · DuckDB ${got.engines.duckdb ? esc(got.engines.duckdb) : 'not installed'} · ${mongo}.</div>`;
  }

  async function chooseStore(select) {
    const p = data.plugins.find((x) => x.name === select.dataset.storePlugin) || pubNamed(select.dataset.storePlugin);
    const type = select.dataset.storeType, engine = select.value || null;
    const row = storeState.data?.stores.find((r) => r.type === type);
    const to = ENGINES[engine || row?.optimal] || engine;
    const ask = type === 'bars'
      ? (to === 'DuckDB' ? `Keep ${p.title}'s closed months in DuckDB? The hourly sync copies a few a run, newest first, each checked; SQLite keeps every bar.`
        : `Read ${p.title}'s bars from SQLite alone? The DuckDB files stay until you remove them.`)
      : `Keep ${p.title}'s ${row?.title.toLowerCase() || type} in ${to}? They are copied there now and counted, then read and written there; the store left keeps its copy.`;
    if (!window.confirm(ask)) { undo(select); render(); return; }
    note = '';
    let saved = false;
    try {
      const res = await fetch('/api/plugins/store', { method: 'POST', headers: WRITE_HEADERS,
        body: JSON.stringify({ plugin: select.dataset.storePlugin, type, engine }) });
      const out = await res.json().catch(() => ({}));
      if (res.ok) Object.assign(storeState, { name: select.dataset.storePlugin, data: out, at: Date.now() });
      saved = res.ok && !out.error;
      note = res.ok ? (out.error ? `${row?.title || type} stay where they were: ${out.error}` : `${row?.title || type} kept in ${to} from now.`)
        : out.error || res.statusText;
    } catch (err) { note = String(err.message || err); }
    if (!saved) undo(select);  // they stay where they were: the store in use shown, not the one picked
    render();
  }

  // what a publisher reads: its feeds with their last and next reads, its schedule, its scrape
  // checks and its limits as last verified with a Recheck (its page, or its section in its plugin's)
  function readsHtml(p) {
    const feeds = p.feeds.map((f) => `<tr><td><b>${esc(f.title)}</b><div class="p-muted small">${esc(f.host || f.agency)}</div></td>
        <td>${esc(every(f.every))}</td><td><span class="pl-fstate pl-f-${esc(f.state.replace(/ /g, '-'))}">${esc(FEED_STATE[f.state] || f.state)}</span></td>
        <td>${f.at ? ago(f.at) : 'never'}</td><td>${p.state === 'paused' ? 'paused' : f.next ? until(f.next) : '–'}</td>
        <td class="p-muted pl-msg">${esc(f.message || '')}</td></tr>`).join('');
    const moments = p.moments.length ? `<div class="small">Today's release reads (ET): ${p.moments.map((m) => esc(etTime(m))).join(' · ')}</div>` : '';
    const cal = p.calendar.length ? `<table class="act-table ex-mini"><tbody>${p.calendar.map((c) => `<tr><td>${esc(etDay(c.when))}</td>
        <td>${esc(etTime(c.when))} ET</td><td>${esc(c.what)}</td></tr>`).join('')}</tbody></table>` : '';
    const pol = p.policy;
    const robots = p.robots.map((r) => `<div class="small"><code>${esc(r.host)}</code> robots.txt: ${r.status == null ? 'not read yet'
      : r.status === 200 ? `read ${ago(r.at)}${r.delayMinutes ? `, asks ${num(r.delayMinutes)} min between requests` : ''}`
      : r.answered ? `none published to this reader (HTTP ${r.status}), read as allowing all · ${ago(r.at)}`
      : `did not answer (${r.status ? `HTTP ${r.status}` : 'no answer'}) ${ago(r.at)}: nothing read from the host until it does`}${r.allows === false ? ' · <span class="down">disallows a feed: not read</span>' : ''}</div>`).join('');
    const checks = [
      pol.hosts.length ? `Only ${pol.hosts.map((h) => `<code>${esc(h)}</code>`).join(', ')}, over HTTPS; a redirect elsewhere is not followed` : `Through ${esc(p.account)}'s client: its rate window, daily cap and kills`,
      `Never these sites, whose terms bar programs: ${pol.blocked.map(esc).join(', ')}`,
      'Feeds and APIs only: an answer that is not XML or JSON is refused, never read as a page',
      `A feed at most every ${esc(every(p.floorMinutes))}${p.floorMinutes > pol.minMinutes ? ` (its hosts ask for that; its own is ${esc(every(pol.minMinutes))})` : ''}, whatever its schedule asks`,
      `At most ${num(pol.perDay)} requests a day, robots.txt included: ${num(p.requestsToday)} today`,
      'Conditional requests (an unchanged feed sends nothing back); a refusal (401, 403, 429) holds the whole publisher off 6 h, or the Retry-After if longer; a Retry-After on any answer is honoured',
      'A crawl-delay its robots.txt asks for is kept between any two requests to that host; a feed that keeps failing is asked less and less often, never more than its schedule',
      pol.contact ? (pol.contactSet ? 'Your GOV_CONTACT goes in its User-Agent, as it asks (to it only)' : 'It asks automated readers to identify: waiting for GOV_CONTACT (your name and email)')
        : 'No contact sent: it does not ask for one',
    ].map((c) => `<li>${c}</li>`).join('');
    const rc = p.recheck;
    const seen = rc?.limitsSeen ? Object.entries(rc.limitsSeen) : [];
    const acct = rc?.account ? `<div class="small">${esc(rc.account.title)}'s limits: ${esc(rate(rc.account.rate))}, ${num(rc.account.perDay)} a day${rc.account.published ? ` (its server: ${esc(rc.account.published)})` : ''}</div>` : '';
    const busy = data.rechecking?.name === p.name;
    const waitingContact = p.policy.contact && !p.policy.contactSet;
    const can = !data.offline && !data.rechecking && !['off', 'paused', 'refused'].includes(p.state) && !p.held && !waitingContact;
    const why = data.offline ? 'This offline copy of the app sends no requests'
      : waitingContact ? 'Nothing is sent to it until GOV_CONTACT is set'
      : p.state === 'paused' || p.state === 'refused' || p.held ? 'Nothing is sent to it while it is paused, stopped or held'
      : 'Read its robots.txt again (unless read within the hour) and ask each feed once (never within its floor, refused or held), keeping the rate-limit headers its servers send; pressed again within 15 min it sends nothing';
    const err = p.error ? `<div class="pl-warn">Its plugin failed here: ${esc(p.error)}</div>` : '';
    return `${err}<h4>Feeds</h4><table class="act-table ex-mini pl-feeds"><thead><tr><th>Feed</th><th>Every</th><th>State</th><th>Last read</th><th>Next</th><th></th></tr></thead>
        <tbody>${feeds}</tbody></table>
      ${moments || cal ? `<h4>Schedule</h4>${moments}${cal}` : ''}
      <h4>Scrape checks</h4><ul class="pl-checks small">${checks}</ul>${robots}
      <h4 class="pl-scan-head"><span>Limits, verified</span>
        <button type="button" data-recheck="${esc(p.name)}"${can ? '' : ' disabled'} title="${esc(why)}">${busy ? 'Rechecking…' : 'Recheck'}</button></h4>
      ${can || busy ? '' : whyNot(data.offline || waitingContact || ['paused', 'refused'].includes(p.state) || p.held ? why
        : p.state === 'off' ? 'It is not set up here' : 'Another recheck is running')}
      <div class="p-muted small">${rc ? `Last verified ${ago(rc.at)}` : 'Not verified yet'}: weekly, and here.</div>
      ${acct}${(rc?.notes || []).map((n) => `<div class="small">${esc(n)}</div>`).join('')}
      ${seen.length ? `<table class="act-table ex-mini"><tbody>${seen.map(([k, h]) => `<tr><td>${esc(k)}</td><td class="p-muted">${
        esc(Object.entries(h).filter(([n]) => n !== 'at').map(([n, v]) => `${n}: ${v}`).join(' · '))}</td></tr>`).join('')}</tbody></table>` : ''}`;
  }

  // why nothing is sent for a publisher now: its own pause or kill, its plugin's (Federal's, for an
  // agency), its account's (a vendor's feed refused), fetching paused; or held off after a refusal
  function stopHtml(p) {
    const whose = p.kill?.whose;
    const plugin = parentOf(p);  // Federal, for its agencies; Massive, for its headlines
    const at = (iso) => esc(String(iso || '').slice(0, 16).replace('T', ' '));
    const stop = !p.kill ? ''
      : p.state === 'paused' && whose === 'own' ? 'Paused by you'
      : whose === 'plugin' ? `${p.kill.by === 'user' ? 'Paused' : 'Stopped'} with ${esc(plugin?.title || p.partOf)}: ${p.kill.by === 'user' ? 'Resume all resumes it' : esc(p.kill.why)}`
      : whose === 'fetching' ? `${esc(p.kill.why)}: resume fetching there`
      : whose === 'account' ? `Stopped with ${esc(p.account)}'s account until ${at(p.kill.until)} UTC: ${esc(p.kill.why)}`
      : `Stopped until ${at(p.kill.until)} UTC: ${esc(p.kill.why)}`;
    return `${stop ? `<div class="pl-warn">${stop}</div>` : ''}${p.held ? `<div class="pl-warn">Nothing sent until ${at(p.held.until)} UTC: ${esc(p.held.why)}</div>` : ''}`;
  }

  // its Pause: a plugin of several (Federal) pauses every part; a part (an agency, the headlines) itself
  function pauseHtml(p) {
    const paused = p.state === 'paused' && p.kill?.whose === 'own';  // its own pause: Resume undoes it here
    const all = p.parts.length > 0;
    const sets = setsOnly(p);
    return `<button type="button" data-pause="${esc(p.name)}" data-on="${paused ? '0' : '1'}"${data.offline || (p.kill && !paused) ? ' disabled' : ''}>${paused ? (all ? 'Resume all' : 'Resume') : (all ? 'Pause all' : 'Pause')}</button>
      <div class="p-muted small">${paused ? `Nothing is ${sets ? 'fetched' : 'read'} until you resume it.` : all ? 'Pausing it stops every agency in it until you resume it.'
        : sets ? 'Pausing stops its datasets being fetched until you resume it.' : 'Pausing stops its reads until you resume it.'}</div>`;
  }

  const termsHtml = (p) => `${p.policy.terms ? `<h4>Terms</h4><div class="small">${esc(p.policy.terms)}</div>` : ''}
      ${safeUrl(p.policy.termsUrl) ? `<div class="p-muted small"><a href="${esc(safeUrl(p.policy.termsUrl))}" target="_blank" rel="noopener noreferrer">${esc(p.policy.termsUrl.replace(/^https:\/\//, ''))}</a></div>` : ''}`;

  function pubSideHtml(p) {
    const used = p.policy.perDay ? Math.min(100, (p.requestsToday / p.policy.perDay) * 100) : 0;
    const all = p.parts.length > 0;  // a plugin of several: its pause is theirs too
    const sets = setsOnly(p) ? (p.datasetRows || []) : null;  // a library of datasets: what it holds here
    const fetched = sets && sets.map((d) => d.fetched).filter(Boolean).sort().pop();
    const tiles = sets ? `${tile('Datasets', num(p.datasets.length), 'fetched with datasets fetch')}
        ${tile('Values', num(sets.reduce((n, d) => n + (d.values || 0), 0)), 'kept here')}
        ${tile('Fetched', fetched ? ago(fetched) : 'not yet', 'then on their own cadence')}`
      : `${tile('Requests today', num(p.requestsToday), all ? 'its agencies together' : `of ${num(p.policy.perDay)} a day`)}
        ${all ? tile('Agencies', num(p.parts.length), 'each a part of it') : tile('Floor', every(p.floorMinutes), 'between reads of a feed')}
        ${tile('Feeds', num(all ? partsOf(p).reduce((n, q) => n + q.feeds.length, 0) : p.feeds.length),
          p.kind === 'vendor' ? `on ${esc(p.account)}'s key` : 'official, keyless')}
        ${all ? '' : tile('Verified', p.recheck ? ago(p.recheck.at) : 'never', 'its limits')}`;
    return `<h3>${esc(p.title)} ${chip(p.state)}</h3>
      <div class="p-muted small">${esc(p.about)}</div>${release(p)}
      ${stopHtml(p)}
      <div class="tiles ex-tiles">
        ${tiles}
      </div>
      ${all || sets ? '' : `<div class="pl-usage" title="${num(p.requestsToday)} of ${num(p.policy.perDay)} requests today"><i style="width:${used.toFixed(1)}%"></i></div>`}
      <h4>${sets ? 'Fetching' : 'Reading'}</h4>
      ${pauseHtml(p)}
      ${termsHtml(p)}`;
  }

  // -- an AI assistant (p.ai: its tables, caps, usage and last answers) ---------------------------
  const usd = (c) => (c == null ? '–' : c < 0.01 ? `$${c.toFixed(6)}` : `$${c.toFixed(4)}`);
  // a price per million tokens: $2, $1.25, $0.40, $0.025 (never rounded away)
  const price = (c) => (c == null ? '–' : `$${c >= 1 ? (Number.isInteger(c) ? String(c) : c.toFixed(2)) : Number(c.toFixed(2)) === c ? c.toFixed(2) : String(c)}`);
  const aiTry = { busy: false, answer: null, error: '' };

  function aiTablesHtml(p) {
    const a = p.ai;
    const plans = a.plans.map((x) => `<tr class="${x.name === a.plan ? 'pl-current' : ''}"><td><b>${esc(x.title)}</b><div class="p-muted small">${esc(x.name)}${x.name === a.plan ? ' · chosen' : ''}</div></td>
        <td>${x.apiAccess ? '<span class="up">yes</span>' : '<span class="down">no</span>'}</td>
        <td>${x.rpm ? `${num(x.rpm)} a minute` : '–'}${x.tpm ? `<div class="p-muted small">${num(x.tpm)} tokens a minute</div>` : ''}${x.rpd ? `<div class="p-muted small">${num(x.rpd)} a day</div>` : ''}</td>
        <td class="small">${esc(x.price || '')}${x.spendCap ? `<div class="p-muted">${esc(x.spendCap)}</div>` : ''}</td>
        <td class="small p-muted">${esc(x.note || '')}</td></tr>`).join('');
    const models = a.models.map((m) => `<tr class="${m.id === a.model ? 'pl-current' : ''}"><td><b>${esc(m.title)}</b><div class="p-muted small"><code>${esc(m.id)}</code>${m.id === a.model ? ' · chosen' : ''}</div></td>
        <td class="num">${price(m.inputUsd)}</td><td class="num">${price(m.outputUsd)}</td><td class="num">${price(m.cachedUsd)}</td>
        <td class="num">${m.context ? num(m.context) : '–'}</td><td class="small p-muted">${m.reasoning ? 'thinks first · ' : ''}${esc(m.note || '')}</td></tr>`).join('');
    const sources = a.sources.map((x) => `<div class="small"><a href="${esc(safeUrl(x.url) || '#')}" target="_blank" rel="noopener noreferrer">${esc(x.url.replace(/^https:\/\//, ''))}</a> <span class="p-muted">${esc(x.holds)}</span></div>`).join('');
    return `<h4>${esc(a.vendor)}'s plans, as written in</h4>
      <table class="act-table ex-mini pl-ai-plans"><thead><tr><th>Plan</th><th>API</th><th>Limits published</th><th>Paid as</th><th></th></tr></thead><tbody>${plans}</tbody></table>
      <h4>Its models and prices (per million tokens)</h4>
      <table class="act-table ex-mini pl-ai-models"><thead><tr><th>Model</th><th class="num">In</th><th class="num">Out</th><th class="num">Cached</th><th class="num">Context</th><th></th></tr></thead><tbody>${models}</tbody></table>
      <div class="p-muted small">Verified ${esc(a.verified || 'at release')} against ${a.sources.length ? 'these pages' : 'the vendor'}; the plugin's weekly CI run reads them again and a changed price or limit is a new release of the plugin.</div>${sources}`;
  }

  function aiLimitsHtml(p) {
    const a = p.ai, u = a.usage;
    const today = u.today || {}, machine = u.machine || {}, c = a.caps;
    return `<h4>Caps on this machine</h4><div class="tiles ex-tiles pl-tiles">
        ${tile('Requests today', num(machine.requests), `of ${num(c.requestsPerDay)} a day (this database: ${num(today.requests)})`)}
        ${tile('Tokens today', num(machine.tokens), `of ${num(c.tokensPerDay)} a day (this database: ${num(today.tokens)})`)}
        ${tile('Spent today', usd(today.costUsd), 'this database, by the vendor\'s count or the table')}
        ${tile('An answer', num(c.maxOutputTokens), 'output tokens at most')}
      </div><div class="p-muted small">${esc(p.limits.published || '')}. The caps count every database on this machine on this key (the machine ledgers); raise them in Plan.</div>`;
  }

  function aiConnectHtml(p) {
    const can = p.state === 'ready' && !data.offline && !data.running;
    const why = data.offline ? 'This offline copy of the app sends no requests' : p.state === 'no key' ? 'Its key is not set'
      : p.state === 'no access' ? 'Its plan has no API access: choose the plan your key is on' : data.running ? 'A rescan is running'
      : 'Try the key on the models list and one answer of a few tokens: the plan from the limits its server sends, the tables checked';
    const rows = p.scan.results.map((r) => `<tr><td>${esc(r.what)}</td><td>${r.available ? '<span class="up">yes</span>' : '<span class="down">no</span>'}</td><td class="p-muted">${esc(r.detail)}</td></tr>`).join('');
    return `<h4 class="pl-scan-head"><span>Connect</span>
        <button type="button" data-rescan="${esc(p.name)}"${can ? '' : ' disabled'} title="${esc(why)}">${p.scan.running ? 'Connecting…' : 'Connect'}</button></h4>
      ${can || p.scan.running ? '' : whyNot(data.offline || data.running || ['ready', 'no key', 'no access'].includes(p.state) ? why
        : STATE_WHY[p.state] || `Its state: ${STATE[p.state] || p.state}`)}
      <div class="p-muted small">${p.scan.at ? `Last connected ${ago(p.scan.at)}` : 'Not connected yet'}: on each start of the app and here; a plan it finds is used where you set none.</div>
      ${rows ? `<table class="act-table ex-mini"><tbody>${rows}</tbody></table>` : ''}`;
  }

  function aiRecentHtml(p) {
    const rows = p.ai.recent || [];
    if (!rows.length) return '<h4>Answers</h4><div class="p-muted small">None kept yet: ask below, or from a plugin, the command line (<code>liminal ai ask</code>) or the fetcher\'s page.</div>';
    return `<h4>Last answers</h4><table class="act-table ex-mini pl-ai-recent"><thead><tr><th>When</th><th>Asked</th><th>Answer</th><th>Verdict</th><th class="num">Tokens</th><th class="num">Cost</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${ago(r.at)}<div class="p-muted small">${esc(r.model)} · ${esc(r.kind)}</div></td><td class="small">${esc(r.question)}</td>
        <td class="small${r.stop === 'error' ? ' bad' : ''}">${esc(r.text)}</td><td>${esc(r.verdict || '–')}</td><td class="num">${num(r.tokens)}</td><td class="num">${usd(r.costUsd)}</td></tr>`).join('')}
      </tbody></table>`;
  }

  function aiTryHtml(p) {
    const can = p.state === 'ready' && !data.offline && !aiTry.busy;
    const why = data.offline ? 'This offline copy of the app sends no requests' : p.state !== 'ready' ? 'Set its key and a plan with API access first' : '';
    const models = p.ai.models.map((m) => `<option value="${esc(m.id)}"${m.id === p.ai.model ? ' selected' : ''}>${esc(m.title)}</option>`).join('');
    const out = aiTry.answer && aiTry.answer.assistant === p.name ? aiTry.answer : null;
    const shown = out ? `<div class="pl-ai-answer"><div class="small">${esc(out.text || `(no text: ${out.stop})`)}</div>
        <div class="p-muted small">${esc(out.model)} · ${num(out.usage.inputTokens + out.usage.outputTokens)} tokens · ${usd(out.costUsd)} · ${out.seconds}s${out.verdict ? ` · verdict: ${esc(out.verdict)}` : ''}${out.stop !== 'end_turn' ? ` · ${esc(out.stop)}` : ''}</div></div>` : '';
    return `<h4>Try it</h4><div class="pl-ai-try">
      <textarea class="pl-ai-q" rows="3" maxlength="4000" placeholder="A question for ${esc(p.title)}: it answers from what you give it here, nothing else"${can ? '' : ' disabled'} ${kb('text', 'enter')}></textarea>
      <div class="pl-ai-row"><label>Answer <select class="pl-ai-answer-kind"><option value="text">text</option><option value="verdict">a verdict</option></select></label>
        <label>Scale <input class="pl-ai-scale" type="text" placeholder="yes, no, unclear" size="18" ${kb('plain')}></label>
        <label>Model <select class="pl-ai-model">${models}</select></label>
        <button type="button" data-ai-ask="${esc(p.name)}"${can ? '' : ' disabled'} title="${esc(why || 'One request to the vendor, counted on the caps')}">${aiTry.busy ? 'Asking…' : 'Ask'}</button></div>
      ${can ? '' : whyNot(why)}${aiTry.error ? `<div class="pl-warn">${esc(aiTry.error)}</div>` : ''}${shown}
      <div class="p-muted small">One request, counted on the caps and kept with the answers; the account is sent only as a digest.</div></div>`;
  }

  async function aiAsk(name) {
    // Ask pressed: its fields are let go (a field typed in is never drawn again), so what it says next shows at once
    const typing = box.querySelector('.pl-ai-try')?.contains(document.activeElement) ? document.activeElement : null;
    if (typing && typing.localName !== 'button') typing.blur();
    const q = box.querySelector('textarea.pl-ai-q')?.value.trim();
    if (!q) { aiTry.error = 'Type a question first.'; render(); return; }
    const kind = box.querySelector('select.pl-ai-answer-kind')?.value || 'text';
    const scale = box.querySelector('input.pl-ai-scale')?.value.trim() || '';
    const model = box.querySelector('select.pl-ai-model')?.value || '';
    if (kind === 'verdict' && !scale) { aiTry.error = 'A verdict needs its scale (yes, no, …).'; render(); return; }
    aiTry.busy = true; aiTry.error = ''; render();
    try {
      const res = await fetch('/api/ai/evaluate', { method: 'POST', headers: WRITE_HEADERS,
        body: JSON.stringify({ assistant: name, model, question: q, answer: kind, scale }) });
      const out = await res.json().catch(() => ({}));
      if (res.ok) aiTry.answer = out; else aiTry.error = out.error || res.statusText;
    } catch (err) { aiTry.error = String(err.message || err); }
    aiTry.busy = false;
    render();
    setTimeout(load, 500);
  }

  function aiMainHtml(p) {
    return `${setupHtml(p.name, p.keys)}${p.ai.free ? `<div class="pl-warn">${esc(p.ai.free)}.</div>` : ''}${planHtml(p)}${optionsHtml(p)}${aiTablesHtml(p)}${aiLimitsHtml(p)}${aiConnectHtml(p)}${aiRecentHtml(p)}${aiTryHtml(p)}${databasesHtml(p)}${apisHtml(p)}`;
  }

  function aiSideHtml(p) {
    const a = p.ai, today = a.usage.today || {}, machine = a.usage.machine || {};
    const keys = p.keys.map((k) => `<div class="small"><code>${esc(k.name)}</code> ${k.set ? '<span class="up">set</span>' : '<span class="down">not set</span>'}</div>`).join('');
    return `<h3>${esc(p.title)} ${chip(p.state)}</h3>
      <div class="p-muted small">${esc(p.about)}</div>${release(p)}
      <div class="tiles ex-tiles">
        ${tile('Requests today', num(machine.requests), `of ${num(a.caps.requestsPerDay)} a day`)}
        ${tile('Tokens today', num(machine.tokens), `of ${num(a.caps.tokensPerDay)}`)}
        ${tile('Spent today', usd(today.costUsd), 'this database')}
        ${tile('Model', esc((a.models.find((m) => m.id === a.model) || {}).title || a.model || '–'), `${esc(a.vendor)} · ${esc(a.plan || 'no plan')}`)}
      </div>
      <h4>Keys</h4>${keys}<div class="p-muted small">Set in Setup.</div>
      ${p.signup ? `<div class="p-muted small">From ${signupHtml(p.signup)}</div>` : ''}`;
  }

  function mainHtml() {
    const pub = publisher();
    if (pub) return pubMainHtml(pub);
    const p = plugin();
    if (!p) return '<div class="empty">No plugin found.</div>';
    if (p.ai) return aiMainHtml(p);
    return `${setupHtml(p.name, p.keys)}${fetchingHtml(p.name)}${feedsHtml(p)}${planHtml(p)}${optionsHtml(p)}${limitsHtml(p)}${capabilitiesHtml(p)}${scanHtml(p)}
      ${p.notes.length ? `<h4>Notes</h4>${p.notes.map((n) => `<div class="small">${esc(n)}</div>`).join('')}` : ''}${publishersHtml(p)}${databasesHtml(p)}${apisHtml(p, pubPartsOf(p))}`;
  }

  // -- right: its part in the delivery scheme, its usage, its keys ---------------------------
  function sideHtml() {
    const pub = publisher();
    if (pub) return pubSideHtml(pub);
    const p = plugin();
    if (!p) return '';
    if (p.ai) return aiSideHtml(p);
    const u = p.usage;
    const used = u.perDay ? Math.min(100, (u.today / u.perDay) * 100) : 0;
    const first = p.delivery.filter((e) => e.first), standby = p.delivery.filter((e) => !e.first);
    const era = (e) => `<tr><td>${esc(JOBS[e.job] || e.job)}</td><td>${esc(e.asset)}${several(p) ? `<div class="p-muted">${esc(e.feed)}</div>` : ''}</td><td>${esc(e.start)} → ${esc(e.end || 'now')}</td>
        <td class="p-muted">${e.unique ? 'only source' : e.first ? `then ${esc(e.others.join(', '))}` : `after ${esc(e.others[0] || '')}`}</td></tr>`;
    const keys = p.keys.length ? p.keys.map((k) => `<div class="small"><code>${esc(k.name)}</code> ${k.set ? '<span class="up">set</span>' : '<span class="down">not set</span>'}</div>`).join('')
      + '<div class="p-muted small">Set in Setup.</div>' : '<div class="p-muted small">Needs no key.</div>';
    return `<h3>${esc(p.title)} ${chip(p.state)}</h3>
      <div class="p-muted small">${esc(p.about)}</div>${release(p)}
      ${p.kill ? `<div class="pl-warn">Killed until ${esc(p.kill.until?.slice(0, 16).replace('T', ' '))} UTC: ${esc(p.kill.why)}</div>` : ''}
      <div class="tiles ex-tiles">
        ${tile('Requests today', num(u.today), `of ${num(u.perDay)} a day`)}
        ${tile('Symbols', num(p.watched), 'watched on it')}
        ${tile('Its load', days(p.load), 'of its limits for what it delivers')}
        ${tile('Delivers', num(first.length), `eras first · ${num(standby.length)} standing by`)}
      </div>
      <div class="pl-usage" title="${num(u.today)} of ${num(u.perDay)} requests today"><i style="width:${used.toFixed(1)}%"></i></div>
      ${first.length ? `<h4>What it delivers</h4><table class="act-table ex-mini"><tbody>${first.map(era).join('')}</tbody></table>` : ''}
      ${standby.length ? `<h4>Standing by for</h4><table class="act-table ex-mini"><tbody>${standby.map(era).join('')}</tbody></table>` : ''}
      <h4>Keys</h4>${keys}
      ${p.signup ? `<div class="p-muted small">From ${signupHtml(p.signup)}</div>` : ''}`;
  }

  // Drawn again every 5 s while a plugin's fetching shows (every few seconds in a rescan, every minute else): the three
  // columns stay, each drawn again in its parts, a part only when it changed (panels.js Views.patch), so each column
  // keeps its scroll, a tap on what did not change lands, and the field being typed in (a key in Setup, the question
  // in Try it) is never replaced while it is typed in: an on-screen keyboard is not closed nor its word cut. Nothing
  // is drawn again under a finger or while a finger's scroll moves (the user, 2026-11-10: "I want to support touch as
  // well as mouse clicking on the interface"; "Support for virtual keyboard on tablets and phones").
  function render() {
    if (!data) { box.innerHTML = '<div class="empty">Loading the plugins…</div>'; return; }
    const p = plugin(), pub = publisher();
    const setUp = data.plugins.filter((x) => !x.ai && x.configured && x.state !== 'no key').length;
    const feedCount = data.plugins.filter((x) => !x.ai).length;
    const pubPlugins = (data.publishers || []).filter((q) => !parentOf(q)).length;  // its parts in it
    const head = pub ? `publisher · ${pubKind(pub)} · ${pubCount(pub)}`
      : p?.ai ? `AI assistant · ${p.ai.vendor} · ${p.ai.plan || 'no plan'} · ${p.ai.model}`
      : p ? `${p.archive ? 'closed archive' : p.feeds.some((f) => f.archive) ? 'feed, a closed archive in part' : 'feed'} · ${p.kind} data · ${p.assets.join(', ')}` : '';
    const cols = [
      `<div class="view-head"><div><h2>Plugins</h2>
        <div class="p-muted small">${num(data.plugins.length + pubPlugins)} plugins · ${num(setUp)} of ${num(feedCount)} feeds set up · plugin API ${esc(data.api)}</div></div></div>
        <button type="button" class="pl-all" data-rescan="*"${data.offline || data.running ? ' disabled' : ''}
          title="${data.offline ? 'This offline copy of the app sends no requests' : 'Every plugin set up here asks its provider again (a few requests each)'}">${data.running ? 'Scanning…' : 'Rescan all'}</button>
        ${data.offline ? whyNot('This offline copy of the app sends no requests and saves no settings: its buttons and pickers are off') : ''}
        ${note ? `<div class="p-muted small pl-note-line">${esc(note)}</div>` : ''}
        ${listHtml()}`,
      `<div class="view-head"><div><h2>${esc(pub?.title || p?.title || '')}</h2>
        <div class="p-muted small">${esc(head)}</div></div></div>${mainHtml()}`,
      sideHtml(),
    ];
    window.Views.redraw(box, () => paint(cols));
  }

  function paint([left, middle, side]) {
    const fields = new Set(box.querySelectorAll('input, textarea, select'));  // a field drawn new gets back what it held
    // what is being typed in Setup survives the view's refreshes (every minute, every few seconds in a rescan)
    const typed = [...box.querySelectorAll('input.pl-key')].filter((i) => i.value).map((i) => [i.dataset.var, i.value]);
    // and the question being typed in Try it, its scale, the answer and the model picked
    const asked = ['textarea.pl-ai-q', 'input.pl-ai-scale', 'select.pl-ai-answer-kind', 'select.pl-ai-model']
      .map((q) => [q, box.querySelector(q)?.value]).filter(([, v]) => v);
    const [a, b, c] = box.children;
    if (box.children.length === 3 && a.matches('.ex-venues') && b.matches('.ex-list') && c.matches('.ex-side')) {
      window.Views.patch(a, left, render);
      window.Views.patch(b, middle, render);
      window.Views.patch(c, side, render);
    } else {
      const keep = [...box.querySelectorAll('.ex-venues, .ex-list, .ex-side')].map((el) => el.scrollTop);
      box.innerHTML = `<div class="ex-venues">${left}</div><div class="ex-list">${middle}</div><aside class="ex-side">${side}</aside>`;
      box.querySelectorAll('.ex-venues, .ex-list, .ex-side').forEach((el, i) => { el.scrollTop = keep[i] || 0; });
    }
    const fresh = (f) => !!f && !fields.has(f);
    for (const [v, value] of typed) {
      const input = box.querySelector(`input.pl-key[data-var="${CSS.escape(v)}"]`);
      if (fresh(input)) input.value = value;
    }
    for (const [q, value] of asked) {
      const f = box.querySelector(q);
      if (fresh(f) && (f.localName !== 'select' || [...f.options].some((o) => o.value === value))) f.value = value;
    }
  }

  box.addEventListener('keydown', (e) => {  // Enter in a Setup field saves it
    if (e.key === 'Enter' && e.target.matches('input.pl-key')) {
      e.target.closest('tr')?.querySelector('[data-save-key]')?.click();
    }
  });
  box.addEventListener('change', (e) => {
    if (e.target.matches('select.pl-choose')) choosePlan(e.target);
    if (e.target.matches('select.pl-option')) chooseOption(e.target);
    if (e.target.matches('select.pl-store')) chooseStore(e.target);
  });
  box.addEventListener('toggle', (e) => {  // a part's section opened or closed: kept through the refreshes
    if (e.target.matches?.('details.pl-part')) folds.set(e.target.dataset.part, e.target.open);
  }, true);
  box.addEventListener('click', (e) => {
    const goto = e.target.closest('[data-goto-fetcher]');
    if (goto) {  // its queue in the Fetcher
      e.preventDefault();
      document.getElementById('railFetcher')?.click();
      return;
    }
    const t = e.target.closest('[data-plugin],[data-rescan],[data-recheck],[data-pause],[data-save-key],[data-remove-key],[data-ai-ask]');
    if (!t || t.disabled) return;
    if (t.dataset.aiAsk) {
      aiAsk(t.dataset.aiAsk);
    } else if (t.dataset.saveKey) {
      const input = box.querySelector(`input.pl-key[data-var="${CSS.escape(t.dataset.saveKey)}"]`);
      const value = input?.value.trim();
      if (!value) { note = `Type ${t.dataset.saveKey} first.`; render(); return; }
      input.value = '';  // sent once; never kept in the page
      // saved from its field (Enter, an on-screen keyboard's Done): nothing is typed in it now, so it is let go and
      // drawn again with the key set (a field typed in is never replaced). A hardware keyboard's user gets it back
      // after, as before; an on-screen keyboard is put away (focused again, the field would call it up unasked)
      const back = document.activeElement === input ? t.dataset.saveKey : null;
      const soft = !!window.Views.softKeyboard?.();
      if (back) input.blur();
      saveKey(t.dataset.owner, t.dataset.saveKey, value).then(() => {
        const again = back && !soft && box.querySelector(`input.pl-key[data-var="${CSS.escape(back)}"]`);
        if (again) again.focus();
      });
    } else if (t.dataset.removeKey) {
      if (window.confirm(`Remove ${t.dataset.removeKey} from your user environment variables? What reads it stops until you set it again.`)) {
        saveKey(t.dataset.owner, t.dataset.removeKey, null);
      }
    } else if (t.dataset.plugin && t.dataset.plugin !== sel) {
      // another plugin picked: a field still focused (a key half typed) let go, so its page is all the new plugin's;
      // on a phone (the columns stacked) its page, below the list, brought into view
      window.Views.letGo(box);
      sel = t.dataset.plugin; render();
      window.Views.reveal(box.querySelector('.ex-list'));
    } else if (t.dataset.rescan) {
      rescan(t.dataset.rescan === '*' ? null : t.dataset.rescan);
    } else if (t.dataset.recheck) {
      post(`/api/plugins/recheck?name=${encodeURIComponent(t.dataset.recheck)}`, `Recheck started: ${pubNamed(t.dataset.recheck)?.title || t.dataset.recheck}.`);
    } else if (t.dataset.pause) {
      const on = t.dataset.on === '1';
      post(`/api/plugins/pause?name=${encodeURIComponent(t.dataset.pause)}&on=${on ? 1 : 0}`, on ? 'Paused.' : 'Resumed.');
    }
  });
  window.addEventListener('liminal:view', (e) => {
    if (e.detail.view === 'plugins') load();
    else { clearTimeout(timer); clearTimeout(fetchState.timer); }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  if (document.body.dataset.view === 'plugins') load();  // the page opened on this view (before this script listened)
})();
