'use strict';
/* Preferences: the rail's sliders (above the profile), a pane beside the chart as the watchlist is.

     Feed              which feed the chart reads the symbol from: all of them in order, one alone, or
                       an asset's models (app.js renderFeeds fills the options; the user, 2026-10-31, of
                       the toolbar's picker: "lets get rid of this", "at best its a preference")
     All data          bad data included (unfiltered), or the filtered view (app.js unfilteredBtn, hidden)
     Indicators        the chart's indicators on and below the price (app.js MAIN_INDICATORS and
                       SUB_INDICATORS; its hidden menu does the rest). The user, 2026-10-31: "all data
                       should be a preference, indicators should be a preference too"
     Sessions          the sessions an intraday stock chart shows (the switch at the time axis's left end: the
                       same setting, either one changes both)
     Time zone         the zone intraday times are read in (the switch under the price axis: the same
                       setting); with none picked, this computer's own (app.js LOCAL_ZONE)
     Chart type        candles, bars, lines ... or a price-based type (Renko, Kagi ...): app.js typeSelect,
                       hidden, does the rest; a price-based type's settings stay in the toolbar
     Theme             the page's and the chart's colours (here only: the toolbar's picker is gone): the UI's theme,
                       one for every app's page on this machine (app.js pickTheme saves it; the platform's
                       Preferences plugin sets the same one)
     GPU               the chart drawn by the graphics card (WebGL2), or by the CPU (app.js gpuBtn, hidden)
     Candles           the rising and falling candles' colours (app.js setCandleColors: TradingView's
                       #089981 and #f23645 unless picked), from the same colour popup
     Session shading   each session's shade on the chart (timeaxis.js): pre-market (blue), after-hours
                       (grey) and overnight (violet). Its colour from a popup (the swatch: a palette, any
                       colour by the system's picker or its hex code, or back to the standard one), and
                       how strongly it is laid on, 0-200 % of its standard strength (100 %: the strength
                       set 2026-10-28, half the blue and violet before)
     Views             the rail's views, each with a switch (the library's): a view turned off by a right click
                       or a long press on its icon is turned on again here (panels.js Views); Stock and
                       Preferences stay on

   Kept per profile, as the rest of the page's settings are (app.js prefs), but the theme: the machine's.
   On a touch screen (the user, 2026-11-10: "I want to support touch as well as mouse clicking on the interface";
   "Support for virtual keyboard on tablets and phones"): what a mouse reads in a row's tooltip and a finger needs
   (why a control is off, what a chart type or a shading is) is said under it, the controls are a finger's size
   (views-touch.css), and the pane is never drawn again while its colour field is typed in. */
(() => {
  const { esc, icon } = window.LiminalIcons;
  const SHADES = [
    ['pre', 'Pre-market', '04:00-09:30, blue'],
    ['post', 'After-hours', '16:00-20:00, grey'],
    ['night', 'Overnight', '20:00-04:00 on Blue Ocean, violet'],
  ];
  // The standard colours (timeaxis.js SHADE_BASE's light ones: the dark theme's are deeper), and the palette.
  const STANDARD = { pre: '#143caa', post: '#787b86', night: '#6e3caa' };
  const PALETTE = ['#143caa', '#2962ff', '#00bcd4', '#26a69a', '#4caf50', '#cddc39', '#f0a030', '#ff7043',
                   '#ef5350', '#ec407a', '#ab47bc', '#6e3caa', '#7e57c2', '#787b86', '#9e9e9e', '#ffffff'];
  const HEX = /^#[0-9a-f]{6}$/i;
  const clamp = (v) => (Number.isFinite(v) ? Math.min(200, Math.max(0, Math.round(v))) : 100);
  const saved = prefs.get('shade', {});
  state.shade = Object.fromEntries(SHADES.map(([k]) => [k, clamp(Number(saved?.[k] ?? 100))]));
  const savedColors = prefs.get('shadeColor', {});
  // a colour picked, or null: the standard one (each theme its own)
  state.shadeColor = Object.fromEntries(SHADES.map(([k]) => [k, HEX.test(savedColors?.[k] || '') ? savedColors[k].toLowerCase() : null]));
  // The candles' rising and falling colours (app.js setCandleColors: TradingView's unless picked), and their palette.
  const CANDLES = [['up', 'Rising', 'a candle that closed above its open'], ['down', 'Falling', 'a candle that closed below its open']];
  const CANDLE_PALETTE = ['#089981', '#22ab94', '#26a69a', '#2ebd85', '#2dc08e', '#4caf50', '#00c853', '#787b86',
                          '#f23645', '#f7525f', '#ef5350', '#e53935', '#f92855', '#ff5252', '#ff9800', '#ffffff'];
  const isCandle = (k) => k === 'up' || k === 'down';
  const standardOf = (k) => (isCandle(k) ? window.candleColors()[k === 'up' ? 'standardUp' : 'standardDown'] : STANDARD[k]);
  const colorOf = (k) => (isCandle(k) ? window.candleColors()[k] : state.shadeColor[k] || STANDARD[k]);
  const nameOf = (k) => (isCandle(k) ? `${CANDLES.find(([x]) => x === k)[1]} candles` : SHADES.find(([x]) => x === k)[1]);
  const swatchHtml = (k, what) => `<button type="button" class="pref-swatch" data-color="${k}" style="background:${colorOf(k)}"
    aria-haspopup="dialog" aria-expanded="false" title="${esc(`${nameOf(k)} (${what}): ${colorOf(k)}${colorOf(k) === standardOf(k)
      ? (isCandle(k) ? ", TradingView's" : ', standard') : ''}. ${window.LiminalTouch?.touch ? 'Click or tap' : 'Click'} to pick`)}"></button>`;

  const shown = () => document.body.dataset.pane === 'prefs';
  const typeOption = (o) => `<option value="${esc(o.value)}"${o.value === state.chartType ? ' selected' : ''}${o.disabled ? ' disabled' : ''}`
    + `${o.title ? ` title="${esc(o.title)}"` : ''}>${esc(o.textContent)}</option>`;
  // a row's words a finger needs, under it on a touch screen (its tooltip is a mouse's; an option's never shows at all)
  const why = (words) => (words ? `<div class="p-muted small touch-only pref-why">${esc(words)}</div>` : '');
  // the keyboard a field calls up on a phone or a tablet (the library's touch.js)
  const kb = (kind, more) => window.LiminalTouch?.keyboard(kind, more) || '';
  const whenIdle = (root, fn) => (window.LiminalTouch ? window.LiminalTouch.whenIdle(root, fn) : fn());

  function render() {
    const box = $('pPrefs');
    if (!box) return;
    if (pop) { pop.remove(); pop = null; }  // drawn again: an open popup goes with its row (a click on its swatch opens it)
    const fixed = !!state.meta?.aroundTheClock;  // fx and futures: no sessions to pick
    const feeds = [...$('feedSelect').options];
    const gpu = window.gpuState?.() || { on: false, supported: false, title: '' };
    const type = [...$('typeSelect').querySelectorAll('option')].find((o) => o.value === state.chartType);
    box.innerHTML = `
      <h3>Preferences</h3>
      <div class="p-sub">Data</div>
      <label class="pref-row pref-session" title="The feed the chart reads ${esc(state.ticker || 'the symbol')} from: all of them in order (each span from the first that has it), one alone, or an asset's models"><span>Feed</span>
        <select id="prefFeed"${feeds.length < 2 ? ' disabled' : ''}>${feeds.map((o) =>
          `<option value="${esc(o.value)}"${o.value === state.source ? ' selected' : ''}>${esc(o.textContent)}</option>`).join('')}
        </select></label>
      ${feeds.length < 2 ? why(feeds.length ? `${state.ticker || 'This symbol'} comes from one feed only: nothing to pick` : 'No symbol is open yet') : ''}
      <label class="pref-row pref-gpu" title="${esc($('unfilteredBtn').title)}"><span>All data</span>
        <span><input type="checkbox" id="prefUnfiltered"${state.unfiltered ? ' checked' : ''}> bad data included</span></label>
      <div class="p-sub">Indicators</div>
      <div class="pref-inds">${[['On price', MAIN_INDICATORS], ['Below price', SUB_INDICATORS]].map(([title, items]) =>
        `<div class="pref-ind-group"><div class="pref-ind-head">${esc(title)}</div>${items.map(([name, label]) =>
          `<label class="pref-ind" title="${esc(label)}"><input type="checkbox" data-ind="${esc(name)}"${state.indicators.includes(name) ? ' checked' : ''}>
            <b>${esc(name)}</b><span class="p-muted">${esc(label)}</span></label>`).join('')}</div>`).join('')}</div>
      <div class="p-sub">Sessions and time</div>
      <label class="pref-row pref-session" title="${esc($('sessionSelect').title)}"><span>Sessions shown</span>
        <select id="prefSession"${fixed ? ' disabled' : ''}>${[...$('sessionSelect').options].map((o) =>
          `<option value="${esc(o.value)}"${o.value === state.session ? ' selected' : ''}>${esc(o.textContent)}</option>`).join('')}
        </select></label>
      ${fixed ? `<div class="p-muted small">${esc(state.ticker || 'This symbol')} trades around the clock: no sessions to pick.</div>` : ''}
      <label class="pref-row pref-session" title="The zone the time axis, the crosshair, the current time's clock and the panels read intraday times in (the switch under the price axis: the same setting). Days, weeks and months keep the market's own dates."><span>Time zone</span>
        <select id="prefZone">${TIMEZONES.map((z) =>
          `<option value="${esc(z)}"${z === state.timezone ? ' selected' : ''}>${esc(z === 'exchange'
            ? `Exchange: New York (${gmtLabel(TZ)})` : zoneName(z))}${z === LOCAL_ZONE ? ' · this computer' : ''}</option>`).join('')}
        </select></label>
      <div class="p-sub">Appearance</div>
      <label class="pref-row pref-session" title="${esc($('typeSelect').title)}"><span>Chart type</span>
        <select id="prefType">${[...$('typeSelect').children].map((g) => g.tagName === 'OPTGROUP'
          ? `<optgroup label="${esc(g.label)}">${[...g.children].map(typeOption).join('')}</optgroup>` : typeOption(g)).join('')}
        </select></label>
      ${why(type?.title)}
      <label class="pref-row pref-session" title="The page's and the chart's colours: the UI's theme, the same on every app's page on this machine (the platform's Preferences picks it too)"><span>Theme</span>
        <select id="prefTheme">${Object.entries(THEMES).map(([t, th]) =>
          `<option value="${esc(t)}"${t === state.theme ? ' selected' : ''}>${esc(th.label)} (${th.base === 'light' ? 'light' : 'dark'})</option>`).join('')}
        </select></label>
      <label class="pref-row pref-gpu" title="${esc(gpu.title)}"><span>Drawing</span>
        <span><input type="checkbox" id="prefGpu"${gpu.on && gpu.supported ? ' checked' : ''}${gpu.supported ? '' : ' disabled'}> on the GPU</span></label>
      ${why(gpu.title)}
      <div class="pref-row pref-candles"><span class="pref-candles-head">Candles</span>
        ${CANDLES.map(([k, name, what]) => `<span>${swatchHtml(k, what)}${esc(name)}</span>`).join('')}</div>
      <div class="p-sub">Views <span class="p-muted small">on the rail: a right click or a long press on an icon disables its view, its switch here enables it again</span></div>
      ${(window.Views?.list() || []).map((v) => `
        <div class="pref-row pref-view"${v.fixed ? ` title="${esc(v.name === 'prefs' ? 'Where the views are enabled again: always on the rail' : "The app's own icon (the symbol's): always on the rail")}"` : ''}>
          <span class="pref-view-name">${icon(v.icon, 18)}${esc(v.title)}</span>
          <label class="switch-row"><button type="button" class="switch" role="switch" aria-checked="${v.on}" aria-label="${esc(v.title)}"
            data-view="${esc(v.name)}"${v.fixed ? ' disabled' : ''}></button><span>${v.on ? 'Enabled' : 'Disabled'}</span></label></div>
        ${v.fixed ? why(v.name === 'prefs' ? 'Always on the rail: the views are enabled again here' : "Always on the rail: the app's own icon, the symbol's") : ''}`).join('')}
      <div class="p-sub">Session shading <span class="p-muted small">on intraday stock charts: its colour, and its strength (100 % standard)</span></div>
      ${SHADES.map(([k, name, what]) => `
        <div class="pref-row pref-shade" title="${esc(`${name}: ${what}`)}">
          <span>${swatchHtml(k, what)}${esc(name)}</span>
          <input type="range" min="0" max="200" step="5" data-shade="${k}" value="${state.shade[k]}" aria-label="${esc(name)} shading strength">
          <output>${state.shade[k]} %</output></div>
        ${why(what)}`).join('')}
      <button type="button" id="prefShadeReset" class="pref-reset">Standard colours and strength</button>`;
  }

  // One shade's strength or colour changed: kept, and the chart's sessions drawn again (a layout, no data read).
  function setShade(k, v) {
    state.shade[k] = clamp(v);
    prefs.set('shade', state.shade);
    window.TimeAxis?.redraw();
  }

  function setColor(k, hex) {
    const pick = hex && HEX.test(hex) && hex.toLowerCase() !== standardOf(k) ? hex.toLowerCase() : null;
    if (isCandle(k)) {  // the pair, each candle drawn again (app.js)
      const c = window.candleColors();
      window.setCandleColors(k === 'up' ? pick : c.up, k === 'down' ? pick : c.down);
    } else {
      state.shadeColor[k] = pick;
      prefs.set('shadeColor', state.shadeColor);
      window.TimeAxis?.redraw();
    }
    const sw = $('pPrefs').querySelector(`.pref-swatch[data-color="${k}"]`);
    if (sw) sw.style.background = colorOf(k);
    if (pop?.dataset.color === k) syncPop(k);
  }

  // -- the colour popup: under its swatch, in the side panel ---------------------------------
  let pop = null;
  function openPop(k, swatch) {
    closePop();
    const name = nameOf(k), palette = isCandle(k) ? CANDLE_PALETTE : PALETTE;
    pop = document.createElement('div');
    pop.className = 'pref-pop';
    pop.dataset.color = k;
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', `${name}'s colour`);
    pop.innerHTML = `
      <div class="pref-pop-head">${esc(name)}</div>
      <div class="pref-pop-grid">${palette.map((c) =>
        `<button type="button" data-pick="${c}" style="background:${c}" title="${c}${c === standardOf(k) ? (isCandle(k) ? " (TradingView's)" : ' (standard)') : ''}" aria-label="${c}"></button>`).join('')}</div>
      <div class="pref-pop-row">
        <input type="color" class="pref-pop-any" aria-label="Any colour (the system's picker)" title="Any colour: the system's picker">
        <input type="text" class="pref-pop-hex" maxlength="7" spellcheck="false" aria-label="Colour as #rrggbb" ${kb('plain', { spellcheck: null })}>
      </div>
      <div class="pref-pop-row">
        <button type="button" data-pick="" class="pref-reset">${isCandle(k) ? 'TradingView\'s' : 'Standard'}</button>
        <button type="button" data-done class="pref-reset">Done</button>
      </div>`;
    swatch.closest('.pref-row').append(pop);
    swatch.setAttribute('aria-expanded', 'true');
    syncPop(k);
    (pop.querySelector(`.pref-pop-grid [data-pick="${colorOf(k)}"]`) || pop.querySelector('.pref-pop-grid [data-pick]')).focus();
  }

  function syncPop(k) {
    if (!pop) return;
    const c = colorOf(k);
    pop.querySelector('.pref-pop-any').value = c;
    const hex = pop.querySelector('.pref-pop-hex');
    if (document.activeElement !== hex) hex.value = c;
    for (const b of pop.querySelectorAll('.pref-pop-grid [data-pick]')) b.classList.toggle('on', b.dataset.pick === c);
  }

  function closePop(focusSwatch = false) {
    if (!pop) return;
    const k = pop.dataset.color;
    pop.remove();
    pop = null;
    const sw = $('pPrefs').querySelector(`.pref-swatch[data-color="${k}"]`);
    sw?.setAttribute('aria-expanded', 'false');
    if (focusSwatch) sw?.focus();
  }

  $('pPrefs').addEventListener('input', (e) => {
    if (e.target.classList.contains('pref-pop-any')) { setColor(pop.dataset.color, e.target.value); return; }  // live
    if (e.target.classList.contains('pref-pop-hex')) {
      const v = e.target.value.trim();
      if (HEX.test(v)) setColor(pop.dataset.color, v);
      return;
    }
    const k = e.target.dataset?.shade;
    if (!k) return;
    setShade(k, Number(e.target.value));
    e.target.nextElementSibling.textContent = `${state.shade[k]} %`;
  });
  $('pPrefs').addEventListener('change', (e) => {
    if (e.target.id === 'prefZone') window.setChartTimezone(e.target.value);  // as the switch under the price axis
    if (e.target.id === 'prefFeed') {  // the hidden picker does the rest (app.js): the symbol read from that feed
      $('feedSelect').value = e.target.value;
      $('feedSelect').dispatchEvent(new Event('change'));
      return;
    }
    if (e.target.id === 'prefUnfiltered') { $('unfilteredBtn').click(); return; }  // the hidden toggle does the rest (app.js)
    if (e.target.matches('input[data-ind]')) {  // the hidden menu's checkbox does the rest (app.js): the chart, kept
      const cb = $('indMenu').querySelector(`input[data-ind="${CSS.escape(e.target.dataset.ind)}"]`);
      if (cb) { cb.checked = e.target.checked; cb.dispatchEvent(new Event('change')); }
      return;
    }
    if (e.target.id === 'prefGpu') { $('gpuBtn').click(); return; }
    if (e.target.id === 'prefType') {  // the hidden picker does the rest (app.js): drawn again, kept
      $('typeSelect').value = e.target.value;
      $('typeSelect').dispatchEvent(new Event('change'));
      return;
    }  // the hidden switch does the rest (app.js): kept, drawn again
    if (e.target.id === 'prefTheme') {  // the hidden picker does the rest (app.js): page, chart, kept
      $('themeSelect').value = e.target.value;
      $('themeSelect').dispatchEvent(new Event('change'));
      return;
    }
    if (e.target.id !== 'prefSession') return;
    $('sessionSelect').value = e.target.value;  // the hidden picker does the rest (app.js), as the axis's switch
    $('sessionSelect').dispatchEvent(new Event('change'));
  });
  // a view's switch: its icon back on the rail, or out of it (panels.js Views; the switch follows, liminal:views)
  function syncViews() {
    for (const v of window.Views?.list() || []) {
      const sw = $('pPrefs').querySelector(`button.switch[data-view="${CSS.escape(v.name)}"]`);
      if (!sw) continue;
      sw.setAttribute('aria-checked', String(v.on));
      sw.nextElementSibling.textContent = v.on ? 'Enabled' : 'Disabled';
    }
  }
  $('pPrefs').addEventListener('click', (e) => {
    const sw = e.target.closest('button.switch[data-view]');
    if (sw) {
      if (!sw.disabled) window.Views?.set(sw.dataset.view, sw.getAttribute('aria-checked') !== 'true');
      return;
    }
    const swatch = e.target.closest('.pref-swatch[data-color]');
    if (swatch) {
      if (pop?.dataset.color === swatch.dataset.color) closePop(true);
      else openPop(swatch.dataset.color, swatch);
      return;
    }
    const pick = e.target.closest('[data-pick]');
    if (pick && pop) { setColor(pop.dataset.color, pick.dataset.pick || null); return; }
    if (e.target.closest('[data-done]')) { closePop(true); return; }
    if (e.target.id !== 'prefShadeReset') return;
    for (const [k] of SHADES) { setShade(k, 100); setColor(k, null); }
    closePop();
    render();
  });
  // the popup goes on a click elsewhere, or Escape
  document.addEventListener('pointerdown', (e) => {
    if (pop && !pop.contains(e.target) && !e.target.closest('.pref-swatch')) closePop();
  });
  document.addEventListener('keydown', (e) => { if (pop && e.key === 'Escape') { e.stopPropagation(); closePop(true); } }, true);
  $('pPrefs').addEventListener('keydown', (e) => {  // an on-screen keyboard's Done in the colour's field: put away
    if (e.key === 'Enter' && e.target.classList.contains('pref-pop-hex') && window.Views?.softKeyboard?.()) e.target.blur();
  });
  // the toolbar's picker changed, a symbol opened (around the clock or not), the pane shown; never while the colour's
  // field is typed in (a candle's colour typed whole draws the chart again, which said so): once it is left, and not
  // under a finger (a tap on the popup's palette lands first)
  const again = () => { if (shown()) whenIdle($('pPrefs'), () => window.Views.redraw($('pPrefs'), () => { if (shown()) { closePop(); render(); } })); };
  $('sessionSelect').addEventListener('change', again);
  window.addEventListener('liminal:symbol', again);
  window.addEventListener('liminal:feeds', again);
  window.addEventListener('liminal:gpu', again);
  window.addEventListener('liminal:unfiltered', again);
  window.addEventListener('liminal:indicators', again);  // picked here, or a layout's
  window.addEventListener('liminal:charttype', again);  // a type picked, or set by a model or a layout  // the symbol's feeds listed again (app.js renderFeeds)
  window.addEventListener('liminal:timezone', again);  // picked under the price axis
  window.addEventListener('liminal:theme', () => { again(); window.TimeAxis?.redraw(); });  // picked here, or the UI's changed; the shades' theme
  window.addEventListener('liminal:views', () => { if (shown()) syncViews(); });  // a right click on the rail, or a switch here
  window.addEventListener('liminal:view', (e) => { if (e.detail?.pane === 'prefs') render(); else closePop(); });
  window.Prefs = { render, get shade() { return { ...state.shade }; }, get colors() { return { ...state.shadeColor }; } };
  if (shown()) render();  // the page opened on this pane (its pick kept): shown before this script listened
})();
