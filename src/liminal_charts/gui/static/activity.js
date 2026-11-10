// Fetcher and live-feed activity. One poll of /api/activity feeds the Fetcher view (historical
// data: sessions before the current one, with the Scan button), the Live view (current-session
// feeds and whether they are being recorded), the rail icons and the status bar.
//
// Opening a symbol asks every feed holding it to update it first: with no run going, a run for
// that symbol alone starts (gaps and new data on each feed, within each feed's limits); with one
// going, the running sync takes it next. The status bar at the bottom shows where that stands
// (like a browser's), then the chart picks up the new bars.
(() => {
  'use strict';

  const { symbolIcon } = window.LiminalIcons;
  const POLL_FAST_MS = 1500;   // while the Fetcher or Live view is open
  const POLL_RUN_MS = 5000;    // while a run is going, or the open symbol's fetch waits for it to end (each
                               // answer is work for the server, and the sync runs nearly all the time)
  const POLL_SLOW_MS = 10000;
  const POLL_FAIL_MAX_MS = 60000;  // no answer: each poll waits twice as long as the one before, up to this
  const el = (id) => document.getElementById(id);
  const esc = window.LiminalIcons.esc;
  const safeUrl = window.LiminalIcons.safeUrl;
  const num = (n) => (n == null ? '–' : Number(n).toLocaleString());
  // a feed as the page names it: alpaca (blue ocean), tiingo (forex) (app.js feedLabel)
  const label = (source) => (typeof feedLabel === 'function' ? feedLabel(source) : source);
  // The views' redraws (panels.js Views, loaded after this file: until then drawn at once, as before): each part only
  // when it changed, and none under a finger or while a finger's scroll moves (the user, 2026-11-10: "I want to support
  // touch as well as mouse clicking on the interface"): the Live view and the queue are drawn again every 1.5 to 3 s.
  const redraw = (root, fn) => (window.Views?.redraw ? window.Views.redraw(root, fn) : fn());
  const patch = (node, html, again) => { if (window.Views?.patch) window.Views.patch(node, html, again); else node.innerHTML = html; };
  const fingered = () => ['touch', 'pen'].includes(window.LiminalTouch?.lastInput);

  let data = null;
  let timer = null;
  let wasFetching = new Set();  // the feeds fetching at the last poll
  let scanNote = '';
  // The status bar's job: { ticker, phase: 'checking' | 'waiting' | 'downloading' | 'done' | 'current' | 'error', ... }
  let job = null;
  let openTicker = null;  // the symbol open on the chart (sent with each poll for the live recorder)
  let pollFailed = false;  // the last poll got no answer
  let failedPolls = 0;  // in a row
  let netDrawn = '';  // the status bar's message as drawn

  function dur(s) {
    if (s == null) return '–';
    s = Math.round(s);
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
    return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
  }
  function ago(iso) {
    if (!iso) return 'never';
    const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    return s < 0 ? `in ${dur(-s)}` : `${dur(s)} ago`;
  }
  const when = (iso) => (iso ? new Date(iso).toLocaleString([], { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–');
  // Short form for the status bar: Fri 04:00, or 10-21 19:59 when not this week.
  const short = (iso) => {
    if (!iso) return '–';
    const t = new Date(iso), days = Math.abs(t - Date.now()) / 864e5;
    const o = { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false };
    return days < 6 ? t.toLocaleString('en-US', { ...o, weekday: 'short' }) : t.toLocaleString('en-US', { ...o, month: '2-digit', day: '2-digit' });
  };
  const viewOpen = (name) => document.body.dataset.view === name;

  // The feed the status bar's poll asks about (a page from before the queue may remember another).
  const feed = prefs.get('fetcherFeed', 'massive');
  const RUN_START_GRACE_MS = 15_000;  // a run started for the opened symbol shows within this (its lock and row)

  // One poll at a time, and one timer: a call while one is on its way gets that one's answer (each
  // view switch called poll() and started another loop of polls, never stopped: 9 in 20 s).
  let polling = null;
  function poll() {
    clearTimeout(timer);
    if (!polling) polling = pollOnce().finally(() => { polling = null; });
    return polling;
  }

  async function pollOnce() {
    // A hidden tab asks for nothing: the app is not in use through it (the user, 2026-11-01: no
    // processes or open databases when the app is not in use), so the recorder is let go once no
    // visible chart has its symbol, and the app goes quiet with no visible page at all. Asked
    // again the moment the tab is visible (the visibilitychange listener below).
    if (document.hidden) {
      clearTimeout(timer);
      timer = setTimeout(poll, POLL_SLOW_MS);
      return;
    }
    try {
      // `open`: the chart's symbol, so the live recorder streams it (stocks, from IEX).
      const open = openTicker ? `&open=${encodeURIComponent(openTicker)}` : '';
      const res = await fetch(`/api/activity?lines=20&source=${encodeURIComponent(feed)}${open}`,
        { headers: { 'X-Liminal-Charts': '1' } });  // the app's own page: its open symbol counts
      if (!res.ok) throw new Error(String(res.status));
      data = await res.json();
      window.dispatchEvent(new CustomEvent('liminal:activity', { detail: data }));
      // The feeds run side by side: any feed's run ending may have brought new data, and the opened
      // symbol's own run ends on its own (Massive's pass goes on beside it for hours).
      const going = new Set((data.fetching || []).map((f) => f.source));
      if ([...wasFetching].some((f) => !going.has(f))) window.dispatchEvent(new CustomEvent('liminal:run-finished', { detail: data }));
      wasFetching = going;
      if (job?.phase === 'downloading') {
        // The symbol's run is over once seen going and then ended, or when none is going a while after
        // it was started (a quick run between two polls): not at once, as the poll 400 ms after the
        // start comes before the run has its lock and its row ("fetch failed" while it was starting).
        const mine = data.symbolRun?.ticker === job.ticker;
        if (mine && data.symbolRun.going) job.seen = true;
        if (job.seen ? !(mine && data.symbolRun.going) : !data.running && Date.now() - job.since > RUN_START_GRACE_MS) finishJob();
      }
      advanceJob();
      render();
      pollFailed = false;
      failedPolls = 0;
    } catch {
      data = null;
      pollFailed = true;
      failedPolls += 1;
      render();
    }
    const fast = viewOpen('fetcher') || viewOpen('live');
    const every = fast ? POLL_FAST_MS : data?.running || job?.phase === 'waiting' ? POLL_RUN_MS : POLL_SLOW_MS;
    // The app not answering (stopped, restarting): asked again later each time, back to the usual pace
    // with its next answer. It was asked at the usual pace (every 1.5 s with the Fetcher open) all the while.
    clearTimeout(timer);
    timer = setTimeout(poll, failedPolls ? Math.min(POLL_FAIL_MAX_MS, every * 2 ** failedPolls) : every);
  }

  function render() {
    renderStatusBar();
    if (viewOpen('fetcher')) renderFetcherView();
    if (viewOpen('live')) renderLiveView();
  }

  function statusLine() {
    if (!data) return '<span class="dot"></span><b>Unavailable</b>';
    const last = data.runs.find((r) => r.status !== 'running');
    const d = data.derive, p = data.paused;
    if (data.running) {
      const going = data.fetching?.length ? data.fetching : [data.live];  // the feeds run side by side, one run each
      const one = (r) => `${r.current ? `fetching <b>${esc(r.current)}</b>` : 'starting'} · ${num(r.requests)}${
        r.source === data.source ? ` of ${num(data.plan.maxPerRun)}` : ''} requests · ${dur(r.seconds)}`;
      if (going.length > 1) {
        return `<span class="dot on"></span><b>Running ${going.length} feeds</b> — ${going.map((r) => `<b>${esc(label(r.source))}</b> ${one(r)}`).join('; ')}`;
      }
      const live = going[0];
      return `<span class="dot on"></span><b>Running ${esc(live.command)}</b> <span class="p-muted">(${esc(label(live.source))})</span> — ${one(live)}`;
    }
    if (d) {  // after a run: frames built from what arrived (nothing fetched meanwhile)
      return `<span class="dot on"></span><b>Building derived frames</b> — ${esc(label(d.source))}:${esc(d.ticker)}, `
        + `${esc(d.to.join(', '))} from ${esc(d.from)} (${num(d.n)} of ${num(d.of)}) · nothing is being fetched`;
    }
    if (p?.halt) return '<span class="dot"></span><b>Halted</b> — nothing is fetched until you resume';
    if (p) return `<span class="dot"></span><b>Paused</b> until ${new Date(p.until).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} — the open symbol still updates`;
    return `<span class="dot"></span><b>Idle</b>${last ? ` — last run ${ago(last.finished_at || last.started_at)}
      (${esc(last.status)}, ${num(last.requests)} requests, ${num(last.rows)} rows)` : ''}`;
  }

  // -- automatic check when a symbol is opened, and the status bar ----------------------
  async function checkSymbol(ticker) {
    if (!ticker) return;
    if (!data) await poll();
    job = { ticker, phase: 'checking', since: Date.now() };
    renderStatusBar();
    await askFirst(ticker);
    renderStatusBar();
  }

  // Where the symbol stands on the Fetcher's feed (when nothing is being fetched for it).
  function standing(ticker) {
    const s = data?.symbols?.find((x) => x.ticker === ticker);
    if (!s) return null;
    if (s.state === 'current') return { ticker, phase: 'current', nextDue: s.nextDue, last: s.last, since: Date.now() };
    return { ticker, phase: s.state, progress: s.progress, since: Date.now() };  // backfilling / queued / ...
  }

  const ASK_EVERY_MS = 10_000;
  const asked = { ticker: null, at: 0, busy: null };  // the last priority request, and the one on its way
  async function askFirst(ticker, again = false) {
    if (asked.busy === ticker || again && asked.ticker === ticker && Date.now() - asked.at < ASK_EVERY_MS) return;
    Object.assign(asked, { ticker, at: Date.now(), busy: ticker });
    // a symbol you opened goes in your register once: not the one the page opened by itself, nor a repeat ask
    const visit = state.picked === ticker;
    if (visit) state.picked = null;
    let next = null;
    try {
      const res = await fetch(`/api/priority?ticker=${encodeURIComponent(ticker)}${visit ? '&visit=1' : ''}`, { method: 'POST', headers: WRITE_HEADERS });
      const body = await res.json().catch(() => ({}));
      if (res.status !== 404) {  // 404: not on the watchlist, nothing to fetch
        if (!res.ok) throw new Error(body.error || res.statusText);
        const feeds = body.feeds || [];
        if (body.state === 'started') {
          next = { ticker, phase: 'downloading', feeds, since: Date.now() };
          setTimeout(poll, 400);
        } else if (body.state === 'queued') next = { ticker, phase: 'waiting', feeds, need: body.need || [], since: Date.now() };
        else if (body.state === 'halted') next = { ticker, phase: 'halted', since: Date.now() };
        else if (body.state === 'current') next = { ticker, phase: 'current', nextDue: body.nextDue, last: standing(ticker)?.last, since: Date.now() };
        else next = standing(ticker);  // a run for it went in the last few minutes
      }
    } catch (e) {
      next = { ticker, phase: 'error', message: String(e.message || e), since: Date.now() };
    } finally {
      if (asked.busy === ticker) asked.busy = null;
    }
    if (!job || job.ticker === ticker) job = next;  // a symbol opened meanwhile keeps its own
  }

  function advanceJob() {
    if (!job || !data) return;
    // waiting for the running sync: asked again every ASK_EVERY_MS, so the line ends once the run has
    // taken it (the server then says it is current), not only when the whole run ends
    if (job.phase === 'waiting') askFirst(job.ticker, true).then(renderStatusBar);
  }

  function finishJob() {
    if (!job || job.phase !== 'downloading') return;
    // The symbol's run went over every feed holding it that was not fetching: one run record per feed
    // that took it (others' runs go on beside it).
    const runs = data.runs.filter((r) => String(r.command).startsWith('sync') && r.current === job.ticker
      && r.status !== 'running' && Date.parse(r.started_at) >= job.since - 5000);
    const bad = runs.find((r) => r.status !== 'ok');
    job = !runs.length || bad
      ? { ticker: job.ticker, phase: 'error', message: bad ? `${bad.source}: ${bad.status} ${bad.message || ''}` : 'run ended', since: Date.now() }
      : { ticker: job.ticker, phase: 'done', rows: runs.reduce((n, r) => n + (r.rows || 0), 0),
          requests: runs.reduce((n, r) => n + (r.requests || 0), 0), feedCount: runs.length, since: Date.now() };
    if (job.phase === 'done' && job.rows) window.dispatchEvent(new CustomEvent('liminal:symbol-updated', { detail: job.ticker }));
  }

  function renderStatusBar() {
    const text = el('netText'), bar = el('netBar');
    let msg = '', progress = null, busy = false, cls = '', title = '';
    const running = data?.running, fetching = data?.fetching || [];
    if (job && job.ticker === state.ticker) {
      const t = esc(job.ticker);
      const on = fetching.filter((f) => f.current === job.ticker).map((f) => f.source);  // the feeds fetching it now
      if (job.phase === 'checking') { msg = `${t}: checking…`; busy = true; }
      else if (job.phase === 'waiting' && on.length) {
        msg = `${t}: updating on ${esc(on.join(', '))}${waitNote()}`;
        busy = true;
      } else if (job.phase === 'waiting') {
        // Named: the feeds with something to fetch for it (need), each fetching now, so its run takes
        // it next; "next in line · coinbase is fetching" read as if Coinbase, which has no SPCX, were about to.
        const busyFeeds = fetching.map((f) => f.source), need = job.need || [];
        msg = need.length && need.every((f) => busyFeeds.includes(f)) ? `${t}: next on ${esc(need.join(', '))}${waitNote()}`
          : `${t}: waiting for ${need.length ? esc(need.join(', ')) : 'its feeds'}${busyFeeds.length ? ` · ${esc(busyFeeds.join(', '))} fetching` : ''}${waitNote()}`;
        busy = true;
      } else if (job.phase === 'downloading') {
        msg = on.length ? `${t}: updating on ${esc(on.join(', '))}${waitNote()}` : `${t}: starting…`;
        busy = true;
      } else if (job.phase === 'done') {
        msg = `${t}: +${num(job.rows)} rows from ${job.feedCount} feed${job.feedCount === 1 ? '' : 's'}`;
        cls = 'ok';
      }
      else if (job.phase === 'current') { msg = job.last ? `${t} ✓ to ${short(job.last)} · next ${short(job.nextDue)}` : `${t} ✓ up to date · next data ${short(job.nextDue)}`; }
      else if (job.phase === 'backfilling') { msg = `${t}: backfill ${Math.round((job.progress || 0) * 100)}%`; progress = job.progress || 0; }
      else if (job.phase === 'queued') { msg = `${t}: queued`; }
      else if (job.phase === 'halted') { msg = `${t}: fetching halted (Resume to fetch it)`; }
      else if (job.phase === 'fetching') { msg = `${t}: fetching`; busy = true; }
      else if (job.phase === 'error') { msg = `${t}: fetch failed`; cls = 'bad'; title = job.message; }
    } else if (running) {
      msg = fetching.length > 1 ? `Fetcher: ${num(fetching.length)} feeds · ${num(fetching.reduce((n, f) => n + (f.requests || 0), 0))} req`
        : `Fetcher${data.live.current ? `: ${esc(data.live.current)}` : ''} · ${num(data.live.requests)} req`;
      busy = true;
    }
    // Background fetching paused by the button: the open symbol's own fetches still go.
    const pausedUntil = data?.paused?.until;
    el('netPause').textContent = pausedUntil ? 'Resume fetching' : 'Pause fetching';
    el('netPause').classList.toggle('on', !!pausedUntil);
    el('netHalt').hidden = !!pausedUntil;  // halted or paused: Resume is the one way back
    // The updates pause: the symbols not picked are not updated (history and the picked ones go on).
    const updatesUntil = data?.updatesPause?.until;
    el('netUpdates').textContent = updatesUntil
      ? `Resume updates (paused to ${new Date(updatesUntil).toLocaleDateString([], { month: 'short', day: 'numeric' })})` : 'Pause updates 7 d';
    el('netUpdates').classList.toggle('on', !!updatesUntil);
    if (data?.paused?.halt && !busy) {
      msg = 'Fetching halted: nothing is fetched until you resume (the live streams go on)';
    } else if (pausedUntil && !busy && !msg) {
      msg = `Background fetching paused until ${new Date(pausedUntil).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
        + ' · the open symbol still updates';
    }
    // A copy of the app that fetches nothing by design (LIMINAL_OFFLINE: a test copy) says so.
    if (data?.offline && !busy) {
      msg = 'Offline copy: nothing is fetched here (LIMINAL_OFFLINE)';
      title = 'A copy of the app on a copy of the data, for testing; the real app fetches as usual';
    } else if (data && data.canFetch === false && !busy) {  // no source able to fetch (every one down, limited out or not set up)
      msg = 'Unable to fetch data currently';
      cls = 'bad';
      title = (data.health || []).filter((h) => h.state !== 'off').map((h) => `${h.source}: ${h.state} — ${h.why}`).join('\n')
        || 'No source is set up here';
    } else if (!data && pollFailed) {
      msg = 'Unable to reach the chart app';
      cls = 'bad';
    }
    text.title = title;
    // Frames built from finer bars (derive.py): what, from what, for which series, how far along.
    const d = data?.derive;
    if (d && cls !== 'bad' && (!msg || !busy)) {
      msg = `Building ${esc(d.to.join(', '))} from ${esc(d.from)} bars <span class="net-derived">*derived</span> · `
        + `${esc(label(d.source))}:${esc(d.ticker)} (${num(d.n)} of ${num(d.of)})`;
      progress = d.of ? d.n / d.of : null;
      busy = true;
      cls = '';
    }
    // drawn again only when it changed, and not under a finger (its tap shows the words: below); on a touch screen an
    // ⓘ at its start says there is more to read than the line: why a fetch failed or nothing can be fetched (its
    // tooltip's words), or the line itself cut short on a narrow screen (an ⓘ at its end went with what was cut)
    const html = msg ? `<span class="net-more touch-only" aria-hidden="true"${title ? '' : ' hidden'}>ⓘ</span>${busy ? '<span class="net-spin"></span>' : ''}${msg}` : '';
    redraw(text, () => {
      if (netDrawn !== html) { text.innerHTML = html; netDrawn = html; }
      const more = text.querySelector('.net-more');
      if (more && !title && window.LiminalTouch?.touch) {  // shown while the line does not fit (measured without it)
        more.hidden = true;
        more.hidden = !(text.scrollWidth > text.clientWidth + 1);
      }
    });
    text.className = cls;
    bar.hidden = progress == null && !busy;
    bar.classList.toggle('indeterminate', progress == null && busy);
    bar.firstElementChild.style.width = progress == null ? '' : `${Math.round(progress * 100)}%`;
  }

  // A wait in progress on the running feed: its limit (or the server's Retry-After) and how long.
  function waitNote() {
    const src = data?.live?.source;
    const w = (data?.limits || []).find((l) => l.source === src)?.wait;
    if (!w) return '';
    const left = Math.max(0, Math.round(w.until - Date.now() / 1000));
    return left ? ` · waiting ${left} s (${esc(w.reason)})` : '';
  }

  // The status bar's message whole for a finger: a tap on it shows its words and why (its tooltip's) in a bubble, for
  // a long line is cut short on a narrow screen and the reason a fetch failed is a tooltip no finger can hover (the
  // user, 2026-11-10). A mouse's click does nothing new: its hover shows the tooltip as before.
  el('netText').addEventListener('click', () => {
    if (!fingered() || !window.LiminalTouch?.tip) return;
    const t = el('netText');
    const line = [...t.childNodes].filter((n) => !n.classList?.contains('net-more')).map((n) => n.textContent).join('').trim();
    const words = [line, t.title].filter(Boolean).join('\n');
    if (words) window.LiminalTouch.tip(t, words);
  });

  window.addEventListener('liminal:symbol', (e) => { openTicker = e.detail; checkSymbol(e.detail); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });  // back in view: asked at once

  // Pause: the running background fetch stops before its next request, the hourly one does not
  // start; the open symbol is then fetched at once (priority), with the feeds to itself.
  el('netPause').addEventListener('click', async () => {
    const pause = !data?.paused;  // paused or halted: this resumes (and a sync starts if none is running)
    try {
      await apiWrite('POST', '/api/fetching', { paused: pause });
      await poll();
      if (openTicker) checkSymbol(openTicker);
    } catch (err) { showError(err); }
  });
  el('netUpdates').addEventListener('click', async () => {
    try {
      await apiWrite('POST', '/api/updates-pause', { days: data?.updatesPause ? 0 : 7 });
      await poll();
    } catch (err) { showError(err); }
  });
  el('netHalt').addEventListener('click', async () => {
    // a finger's tap asks first: Halt stops every fetch at once and sits a finger's width from Pause (a mouse's click,
    // and the keyboard's, halt as they did)
    if (fingered() && !window.confirm("Halt all fetching until you resume it? The open symbol's fetches stop too; the live streams go on.")) return;
    try {
      await apiWrite('POST', '/api/fetching', { paused: true, halt: true });
      await poll();
    } catch (err) { showError(err); }
  });

  // -- Fetcher view: the fetch queue (gui/queue.py) ------------------------------------
  // The user, 2026-10-31: "the fetcher will then just show the queue of all resources being downloaded
  // like symbols": every plugin's resources (symbols' bars, news reads, dataset files) waiting, being
  // fetched, held, failed or done, filtered by plugin, kind and state. Each plugin's own status (its
  // feeds' requests and limits, runs, log) is on its page in the Plugins view.
  const Q_STATES = ['running', 'waiting', 'held', 'failed', 'stopped', 'done'];
  const Q_LABEL = { running: 'fetching', waiting: 'waiting', held: 'held', failed: 'failed', stopped: 'stopped', done: 'done' };
  const Q_CLASS = { running: 'fetching', waiting: 'queued', held: 'held', failed: 'run-error', stopped: 'run-abandoned', done: 'current' };
  const Q_DOT = { running: 'fetching', waiting: 'queued', held: 'held', failed: 'blocked', stopped: 'retired', done: 'current' };
  const Q_EVERY_MS = 3000;  // the queue asked at most this often while the Fetcher is open
  const qFilter = { plugin: '', kind: '', state: '' };
  let queue = null, queueAt = 0, queueLoading = null;
  const size = (n) => {
    let v = n, i = 0;
    while (v >= 1024 && i < 4) { v /= 1024; i++; }
    return `${i === 0 || v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${['B', 'KB', 'MB', 'GB', 'TB'][i]}`;
  };

  function loadQueue(force = false) {
    if (queueLoading || (!force && Date.now() - queueAt < Q_EVERY_MS)) return;
    const q = new URLSearchParams(Object.entries(qFilter).filter(([, v]) => v));
    queueLoading = fetch(`/api/queue?${q}`).then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j) { queue = j; queueAt = Date.now(); renderQueue(); } })
      .catch(() => {}).finally(() => { queueLoading = null; });
  }

  function qWhen(r) {
    const next = r.not_before && Date.parse(r.not_before) > Date.now() ? `<div class="sub">next ${when(r.not_before)}</div>` : '';
    if (r.state === 'running') return r.started_at ? `for ${dur((Date.now() - Date.parse(r.started_at)) / 1000)}` : '';
    if (r.state === 'done' || r.state === 'failed' || r.state === 'held') return `${r.finished_at ? ago(r.finished_at) : ''}${next}`;
    if (next) return `not before ${when(r.not_before)}`;
    return r.queued_at ? `queued ${ago(r.queued_at)}` : '';
  }

  function renderQueue() {
    if (!queue) return;
    const t = queue.totals || {};
    const total = Object.values(t).reduce((a, b) => a + b, 0);
    const sum = (o) => Object.values(o || {}).reduce((a, b) => a + b, 0);
    const title = Object.fromEntries(queue.plugins.map((p) => [p.name, p.title]));
    el('qCounts').textContent = total ? Q_STATES.filter((st) => t[st]).map((st) => `${num(t[st])} ${Q_LABEL[st]}`).join(' · ') : '';
    const chips = (key, items) => items.map(([v, text, n]) => `<button type="button" data-q="${key}" data-v="${esc(v)}"
      class="${qFilter[key] === v ? 'active' : ''}">${text}${n != null ? ` <span class="chip">${num(n)}</span>` : ''}</button>`).join('');
    const filters = `<div class="q-group">${chips('plugin', [['', 'Every plugin', null],
        ...queue.plugins.map((p) => [p.name, esc(p.title), sum(queue.counts.plugins[p.name])])])}</div>
      <div class="q-group">${chips('kind', [['', 'Every kind', null], ...queue.kinds.map((k) => [k, esc(k), sum(queue.counts.kinds[k])])])}</div>
      <div class="q-group">${chips('state', [['', 'Every state', null], ...Q_STATES.filter((st) => t[st] || st === qFilter.state)
        .map((st) => [st, `<span class="st-dot ${Q_DOT[st]}"></span>${Q_LABEL[st]}`, t[st] || 0])])}</div>`;
    redraw(el('qFilters'), () => patch(el('qFilters'), filters, renderQueue));
    const rows = queue.rows;
    const body = rows.length ? rows.map((r) => {
      const sym = r.kind === 'bars';
      const name = sym ? `<span class="sym-cell">${symbolIcon({ ticker: r.ticker, asset: r.asset }, 20)}<span><b>${esc(r.title)}</b>
          <div class="sub">${esc(r.asset || 'bars')}</div></span></span>` : `<b>${esc(r.title)}</b><div class="sub">${esc(r.kind)}</div>`;
      return `<tr${sym ? ` class="has-data" data-ticker="${esc(r.ticker)}" title="Open in the chart"` : ''}>
        <td>${name}</td>
        <td>${esc(title[r.plugin] || r.plugin)}${r.partLabel ? `<div class="sub">${esc(r.partLabel)}</div>` : ''}</td>
        <td><span class="state ${Q_CLASS[r.state] || ''}">${esc(Q_LABEL[r.state] || r.state)}</span>${r.attempts > 1 ? `<div class="sub">try ${num(r.attempts)}</div>` : ''}</td>
        <td class="q-prog">${downloaded(r)}</td>
        <td class="sub">${esc(r.error || r.detail || '')}</td>
        <td class="num">${r.requests ? num(r.requests) : '–'}${r.rows ? `<div class="sub">${num(r.rows)} rows</div>` : ''}${r.bytes ? `<div class="sub">${size(r.bytes)}</div>` : ''}</td>
        <td>${qWhen(r)}</td></tr>`;
    }).join('') + (rows.length >= queue.limit ? `<tr><td colspan="7" class="empty">The first ${num(rows.length)} shown: what is being
        fetched, then what waits (in its order), held, failed and the newest done. Filter by plugin, kind or state for the rest.</td></tr>` : '')
      : `<tr><td colspan="7" class="empty">${total ? 'Nothing in this filter.' : 'Nothing on the queue yet: the next run of each plugin puts what it will fetch here.'}</td></tr>`;
    redraw(el('qRows'), () => patch(el('qRows'), body, renderQueue));  // a row that did not change stays: a tap on it opens it
  }

  // How much of a symbol this plugin has downloaded (the user, 2026-11-02): its share of the history
  // it is to hold, as a bar, and the first and last day stored at each resolution it sends.
  const RES_LABEL = { day: 'days', minute: 'minutes', second: 'seconds' };
  const shortDay = (d) => (d || '').slice(2);
  function downloaded(r) {
    const p = r.progress;
    if (!p) return r.kind === 'bars' ? '<span class="p-muted">–</span>' : '';
    const pct = Math.round((p.share || 0) * 100);
    const spans = Object.entries(p.stored || {}).map(([res, [a, b]]) => `${RES_LABEL[res] || res} ${shortDay(a)}→${shortDay(b)}`);
    const note = p.retiredOn ? ` · retired ${shortDay(p.retiredOn)}` : '';
    // how far back it is to hold, in words on a touch screen too (the bar's tooltip is a mouse's)
    return `<div class="q-bar" title="${pct}% of the history this plugin is to hold, back to ${esc(p.target || '')}"><span style="width:${pct}%"></span></div>
      <div class="sub">${pct}%${spans.length ? ' · ' + esc(spans.join(' · ')) : ' · nothing stored here'}${esc(note)}${
        p.target ? `<span class="touch-only"> · back to ${esc(p.target)}</span>` : ''}</div>`;
  }

  // The Fetcher icon's words on a touch screen, which its count carries on the rail (a tooltip no finger can hover):
  // the symbols that should have new data (the user, 2026-11-10: a rail icon's name where it carries state becomes
  // visible text on touch screens)
  const railWords = () => (!data?.running && data?.newData
    ? `<span class="p-muted touch-only">· ${num(data.newData)} symbol${data.newData === 1 ? '' : 's'} should have new data: Scan for new data fetches them</span>` : '');

  function renderFetcherView() {
    el('actStatus').innerHTML = statusLine() + railWords();
    if (data) el('scanBtn').disabled = !!data.running;
    el('scanNote').innerHTML = scanNote ? esc(scanNote) : '';
    loadRequests();
    loadQueue();
  }

  // -- the fetcher's requests: the stack of asks in front of its standing queue -------------
  // (the user, 2026-11-01: "the fetcher can take a queue/stack of requests"; the fetcher assesses
  // the work for each symbol over the plugins and collects: fetcher.py, storage/requests.py)
  const RQ_EVERY_MS = 3000;
  const RQ_OPEN = new Set(['waiting', 'assessing', 'collecting']);
  const RQ_CLASS = { collecting: 'fetching', assessing: 'fetching', waiting: 'queued', done: 'current', failed: 'run-error', cancelled: 'run-abandoned' };
  let requests = null, requestsAt = 0, requestsLoading = null;

  function loadRequests(force = false) {
    if (requestsLoading || (!force && Date.now() - requestsAt < RQ_EVERY_MS)) return;
    requestsLoading = fetch('/api/requests?limit=60').then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j) { requests = j; requestsAt = Date.now(); renderRequests(); } })
      .catch(() => {}).finally(() => { requestsLoading = null; });
  }

  function planText(p) {
    if (!p) return '';
    if (!p.jobs?.length) return p.note ? esc(p.note) : '';
    const by = {};
    for (const j of p.jobs) (by[j.plugin] ||= []).push(`${j.resolution} ${j.start.slice(0, 4)}${j.end ? `–${j.end.slice(0, 4)}` : '→'}`);
    return Object.entries(by).map(([k, v]) => `<div><b>${esc(label(k))}</b> ${esc(v.join(', '))}</div>`).join('')
      + (p.live?.length ? `<div class="sub">live: ${esc(p.live.map((l) => label(l[0])).join(', '))}</div>` : '');
  }

  function rqWhen(r) {
    if (r.state === 'collecting' || r.state === 'assessing') return r.started_at ? `for ${dur((Date.now() - Date.parse(r.started_at)) / 1000)}` : '';
    if (r.state === 'waiting') return `asked ${ago(r.asked_at)}`;
    return r.finished_at ? ago(r.finished_at) : '';
  }

  function renderRequests() {
    if (!requests) return;
    const c = requests.counts || {};
    el('rqCounts').textContent = Object.keys(c).length ? Object.entries(c).map(([k, n]) => `${num(n)} ${k}`).join(' · ') : '';
    const st = requests.status;
    const pl = st?.plugins;  // the plugins it loaded, when told which (--plugins): the names, or none
    const plugins = pl && pl.selection && pl.selection !== 'all'
      ? `; plugins: ${pl.loaded?.length ? esc(pl.loaded.map(label).join(', ')) : 'none'} (${esc(pl.selection)} asked for)`
        + `${pl.leftOut?.length ? `; left out: ${esc(pl.leftOut.map(label).join(', '))}` : ''}`
        + `${pl.unknown?.length ? `; unknown: ${esc(pl.unknown.join(', '))}` : ''}`
      : '';
    el('rqStatus').innerHTML = st
      ? `The fetcher${st.name ? ` ${esc(st.name)}` : ''} is ${st.busy ? 'busy' : 'idle'}${st.feeds?.length ? `, holding ${esc(st.feeds.map(label).join(', '))}` : ''}`
        + `${st.lastPass ? `; last pass ${ago(st.lastPass)}` : '; no pass yet'}${st.nextDue ? `; nothing new before ${when(st.nextDue)}` : ''}`
        + `${st.clients?.length ? `; ${num(st.clients.length)} client${st.clients.length === 1 ? '' : 's'} attached` : ''}${plugins}.`
      : 'No fetcher has run on this database yet.';
    const rows = requests.rows || [];
    const body = rows.length ? rows.map((r) => `<tr>
        <td><span class="sym-cell">${symbolIcon({ ticker: r.symbol, asset: r.plan?.asset, icon: symbolInfo(r.symbol).icon }, 20)}<span><b>${esc(r.symbol)}</b>
          <div class="sub">${esc((r.kinds || []).join(', '))}</div></span></span></td>
        <td>${esc(r.by)}</td>
        <td><span class="state ${RQ_CLASS[r.state] || ''}">${esc(r.state)}</span>${RQ_OPEN.has(r.state)
          ? ` <button type="button" class="small-btn" data-cancel="${r.id}" title="Take the request back">cancel</button>` : ''}</td>
        <td class="sub">${planText(r.plan)}</td>
        <td class="sub">${esc(r.detail || '')}</td>
        <td>${rqWhen(r)}</td></tr>`).join('')
      : '<tr><td colspan="6" class="empty">No requests yet: a symbol opened in the chart, or asked for here, goes on the stack.</td></tr>';
    redraw(el('rqRows'), () => patch(el('rqRows'), body, renderRequests));  // its cancel buttons not replaced under a finger
  }

  async function submitRequest(e) {
    e.preventDefault();
    const symbol = el('rqSymbol').value.trim().toUpperCase();
    if (!symbol) return;
    const kinds = el('rqLive').checked ? ['bars', 'live'] : ['bars'];
    let note = '';
    try {
      const res = await fetch('/api/requests', { method: 'POST', headers: WRITE_HEADERS, body: JSON.stringify({ symbol, kinds, by: 'page' }) });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || res.statusText);
      note = `#${body.id} ${body.symbol}: ${body.state}`;
      el('rqSymbol').value = '';
    } catch (err) {
      note = String(err.message || err);
    }
    el('rqNote').textContent = note;
    loadRequests(true);
  }

  async function cancelRequest(id) {
    try { await fetch(`/api/requests/${encodeURIComponent(id)}`, { method: 'DELETE', headers: WRITE_HEADERS }); } catch { /* the next poll shows it */ }
    loadRequests(true);
  }
  el('rqForm').addEventListener('submit', submitRequest);  // its symbol box's keyboard is a ticker's (index.html)
  el('rqRows').addEventListener('click', (e) => {
    const b = e.target.closest('[data-cancel]');
    if (b) cancelRequest(b.dataset.cancel);
  });

  async function startScan() {
    scanNote = '';
    try {
      const res = await fetch('/api/scan', { method: 'POST', headers: { 'X-Liminal-Charts': '1' } });
      if (!res.ok) scanNote = (await res.json().catch(() => ({}))).error || res.statusText;
      else scanNote = 'Scan started: checking every watched symbol.';
    } catch (e) {
      scanNote = String(e.message || e);
    }
    setTimeout(poll, 500);
  }

  // -- Live view -----------------------------------------------------------------------
  function renderLiveView() {
    const box = el('viewLive');
    if (!data) { box.innerHTML = '<div class="view-head"><h2>Live feeds</h2></div><div class="p-muted">Unavailable.</div>'; return; }
    const live = data.liveFeeds;
    const badge = (f) => f.realtime ? '<span class="state fresh">real time</span>'
      : f.available ? `<span class="state backfilling">${esc(f.delay)}</span>` : '<span class="state">end of day only</span>';
    const names = { stock: 'Stocks', index: 'Indices', fx: 'Forex', future: 'Futures', abroad: 'Stocks abroad' };
    // drawn again every 1.5 s while shown: each part only when it changed (a wide table keeps its sideways scroll, a row
    // its tap), and none under a finger or while a finger's scroll moves
    const html = `
      <div class="view-head"><div><h2>Live feeds</h2>
        <div class="p-muted small">The current session, as it happens. The fetcher handles everything before it.</div></div>
        ${window.LiveIcon ? `<div class="live-effect" role="group" aria-label="Rail icon effect while the open symbol streams">
          <span class="p-muted small">Icon while streaming</span>
          ${Object.entries(window.LiveIcon.EFFECTS).map(([k, label]) => `<button type="button" data-effect="${k}"
            class="${window.LiveIcon.effect === k ? 'active' : ''}">${esc(label)}</button>`).join('')}</div>` : ''}</div>
      <div class="act-status"><span class="rec ${live.recording ? 'on' : ''}"></span>
        <b>${live.recording ? 'Recording' : 'Not recording'}</b>
        <span class="p-muted">${live.recording ? 'current-session data is captured as it arrives'
          : live.available ? 'delayed intraday data is available on your plan; nothing records it live yet, so it arrives with the fetcher\'s runs'
            : 'your plan has no current-session feed'}</span>${(live.liveTickers || []).length
          ? `<span class="touch-only">· streaming now: <b>${esc(live.liveTickers.join(', '))}</b></span>` : ''}</div>
      <div class="table-wrap"><table class="act-table">
        <thead><tr><th>Feed</th><th>Symbols</th><th>Latency on your plan</th><th>Recording</th></tr></thead>
        <tbody>${live.feeds.map((f) => `
          <tr><td><b>${esc(names[f.asset] || f.asset)}</b>${f.note ? `<div class="sub">${esc(f.note)}</div>` : ''}</td>
            <td><span class="sym-cell">${f.symbols.slice(0, 12).map((t) => symbolIcon({ ticker: t, asset: f.asset === 'abroad' ? 'stock' : f.asset, icon: symbolInfo(t).icon }, 18)).join('')}</span>
              <div class="sub">${esc(f.symbols.join(', '))}</div></td>
            <td>${badge(f)}</td>
            <td>${f.recording ? '<span class="rec on"></span> recording' : '<span class="p-muted">idle</span>'}</td></tr>`).join('')}
        </tbody></table></div>
      ${liveStreamsHtml(live)}
      <h4>Exchange checks</h4>
      <div class="table-wrap"><table class="act-table">
        <thead><tr><th>Check</th><th>Found</th><th>Last</th><th>Next</th><th>Cost</th></tr></thead>
        <tbody>${(live.checks || []).map((c) => `
          <tr><td><b>${esc(c.name)}</b></td><td>${esc(c.found)}</td>
            <td>${c.last ? (c.last.length > 10 ? ago(c.last) : esc(c.last)) : '<span class="p-muted">not yet</span>'}</td>
            <td class="sub">${c.next && /^\d{4}-/.test(c.next) ? (c.next.length > 10 ? when(c.next) : esc(c.next)) : esc(c.next || '')}</td>
            <td class="sub">${esc(c.cost)}</td></tr>`).join('')}
        </tbody></table></div>
      <h4>News</h4>
      <div id="liveNews" class="news-list">${liveNewsHtml()}</div>`;
    redraw(box, () => patch(box, html, renderLiveView));
    loadLiveNews();
  }

  // The live recorder's streams (liminal live): connection, and per symbol the
  // trades recorded, the last price and how long ago it traded. Every stream follows the symbols
  // open on a chart (LIVE_STREAMS=selected): with none of its symbols open it is idle, not down.
  function liveStreamsHtml(live) {
    const streams = Object.entries(live.streams || {});
    if (!streams.length) {
      return `<h4>Live streams</h4><div class="empty">No live recorder running. It starts with the hourly sync, or now with
        <code>liminal live start</code>.</div>`;
    }
    const rows = streams.flatMap(([src, s]) => {
      const pairs = Object.entries(s.pairs || {}).sort(([a], [b]) => a.localeCompare(b));
      const head = `<tr><td><b>${esc(label(src))}</b></td><td colspan="4">${s.connected
        ? `<span class="rec on"></span> connected${s.since ? ` · since ${when(s.since)}` : ''}${s.symbols
          ? ` · ${s.symbols.length} symbol${s.symbols.length === 1 ? '' : 's'}` : ''}`
        : s.idle ? `<span class="state">idle</span> <span class="sub">${esc(s.note || 'nothing of it open on a chart: not connected')}</span>`
        : `<span class="state run-error">down</span> <span class="sub">${esc(s.error || '')}</span>`}</td></tr>`;
      return [head, ...pairs.map(([t, p]) => `
        <tr class="has-data" data-ticker="${esc(t)}"><td></td><td><b>${esc(t)}</b>${live.liveTickers?.includes(t) ? ' <span class="state fresh">live</span>' : ''}</td>
          <td class="num">${p.price == null ? '–' : esc(String(p.price))}</td><td class="num">${num(p.trades)}</td>
          <td>${p.last ? ago(new Date(p.last).toISOString()) : '–'}</td></tr>`)];
    });
    return `<h4>Live streams <span class="p-muted small">crypto: public exchange feeds, no key · stocks: IEX's trades
      through Alpaca (your keys; one exchange, a small share of volume) · each feed's trades in its own tick file</span></h4>
      <div class="table-wrap"><table class="act-table">
        <thead><tr><th>Feed</th><th>Symbol</th><th class="num">Last price</th><th class="num">Trades recorded</th><th>Last trade</th></tr></thead>
        <tbody>${rows.join('')}</tbody></table></div>`;
  }

  // The newest newsworthy items (headlines, unscheduled closures, early closes, big moves).
  let liveNews = { items: [], at: 0 };
  async function loadLiveNews() {
    if (Date.now() - liveNews.at < 60000) return;
    liveNews.at = Date.now();
    try { liveNews.items = (await (await fetch('/api/announcements')).json()).items || []; } catch { return; }
    const box = el('liveNews');
    if (box) box.innerHTML = liveNewsHtml();
  }
  function liveNewsHtml() {
    if (!liveNews.items.length) return '<div class="empty">Nothing newsworthy right now. Headlines arrive with the fetcher’s runs.</div>';
    return liveNews.items.slice(0, 15).map((it) => `
      <div class="news-row ${esc(it.kind)}">
        <span class="src">${esc(it.source)}</span>
        <div class="news-main">${safeUrl(it.url) ? `<a href="${esc(safeUrl(it.url))}" target="_blank" rel="noopener noreferrer">${esc(it.text)}</a>` : `<span>${esc(it.text)}</span>`}
          <div class="p-muted small">${it.kind === 'news' ? ago(it.time) : 'now'} · ${esc(it.exchange)}${(it.tickers || []).length ? ` · ${esc(it.tickers.join(' '))}` : ''}</div></div>
      </div>`).join('');
  }

  document.addEventListener('click', (e) => {
    if (e.target.closest('#scanBtn')) { startScan(); return; }
    const fx = e.target.closest('#viewLive .live-effect button[data-effect]');
    if (fx) {
      window.LiveIcon.set(fx.dataset.effect);
      for (const b of fx.parentElement.querySelectorAll('button')) b.classList.toggle('active', b === fx);
      return;
    }
    const item = e.target.closest('#qRows tr.has-data, #viewLive tr.has-data');
    if (item) window.dispatchEvent(new CustomEvent('liminal:open-symbol', { detail: item.dataset.ticker }));
  });
  el('qFilters').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-q]');
    if (!b) return;
    qFilter[b.dataset.q] = b.dataset.v;
    loadQueue(true);
  });
  window.addEventListener('liminal:view', () => { render(); poll(); });
  window.Activity = { poll, get data() { return data; }, get job() { return job; } };  // job: the open symbol's fetch
  poll();
})();
