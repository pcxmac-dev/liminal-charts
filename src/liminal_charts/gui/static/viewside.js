// A three-column view's right-hand menu (0.2.11; Monday list item 31; liminal-libs 1.105.0's docs/PAGES.md and
// side.js): the one picked on the view's left (a plugin, a database, a venue) at the panel's top, drawn as the view
// draws it, and a row picked in the middle told in full under it, alike in Plugins, Databases and Exchanges, and so on
// the integrator's page, which is these views. Loaded after panels.js (Views.patch) and before the views.
//
//   ViewSide.on                                 the library's side.js is here (else each view draws its side whole)
//   ViewSide.paint(aside, html, again, middle, scope)  the side drawn into the panel's top (Views.patch), the middle's
//                                               plain rows keyed and picked, the pick told anew from them
//
// A row is picked when it is a plain row of a table in the middle: not one that is itself a pick of the view's own
// (a product, a database, a venue, a plugin: data-product, data-db, data-venue, data-plugin, data-open) and not one
// holding a field. It is keyed by its table's heading and its first cell's words (what it is, not its counts), a
// second row of the same words by its place among them, and told by its cells under their headings. A library without
// side.js: the side patched whole, as before.
(() => {
  'use strict';
  const S = window.LiminalSide || null;
  const OWN_PICK = '[data-product],[data-db],[data-venue],[data-plugin],[data-open]';
  const FIELD = 'input, select, textarea';
  const text = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');

  function headingOf(table) {  // the table's caption, else the nearest heading before it
    if (table.caption) return text(table.caption);
    for (let n = table; n && !n.matches('.ex-list'); n = n.parentElement) {
      for (let s = n.previousElementSibling; s; s = s.previousElementSibling) {
        if (/^H[2-5]$/.test(s.tagName)) return text(s);
        const h = s.querySelector && s.querySelector('h2, h3, h4, h5');
        if (h && s.matches('.view-head, header')) return text(h);
      }
    }
    return '';
  }

  // the middle's plain rows given their keys (each time it is drawn: a row drawn anew comes without one)
  function keyRows(middle) {
    let i = 0;
    for (const table of middle.querySelectorAll('table')) {
      const head = `${headingOf(table) || `table ${i + 1}`}#${i}`;
      i += 1;
      const seen = new Map();
      for (const tr of table.querySelectorAll('tbody > tr')) {
        if (!tr.cells.length || tr.matches(OWN_PICK) || tr.querySelector(OWN_PICK) || tr.querySelector(FIELD)) {
          if (tr.dataset.pick != null) delete tr.dataset.pick;
          continue;
        }
        const words = text(tr.cells[0]);
        const n = seen.get(words) || 0;
        seen.set(words, n + 1);
        const key = `${head}|${words}${n ? `|${n + 1}` : ''}`;
        if (tr.dataset.pick !== key) tr.dataset.pick = key;
      }
    }
  }

  function rowOf(middle, key) {
    for (const tr of middle.querySelectorAll('tr[data-pick]')) if (tr.dataset.pick === key) return tr;
    return null;
  }

  function paint(aside, html, again, middle, scope) {
    if (!S) { window.Views.patch(aside, html, again); return null; }
    const app = window.FETCHER_MODE ? 'integrator' : 'charts';
    const panel = S.panel(aside, { app });
    for (const child of [...aside.childNodes]) {  // what an aside drawn whole held before the panel: let go
      if (child !== panel.slots.tool && child !== panel.slots.pick) child.remove();
    }
    // the top kept as the view draws it, patched (a tap on a part that did not change lands): the panel's own markup
    // is left empty, so a new scope lets the pick go and never wipes what the patch drew
    const slot = panel.tool(scope, null);
    window.Views.patch(slot, html, again);
    if (middle) {
      keyRows(middle);
      S.rows(middle, { selector: 'tr[data-pick]', panel, describe: (tr) => S.fromRow(tr) });
      panel.refresh((key) => { const tr = rowOf(middle, key); return tr ? S.fromRow(tr) : null; });
    }
    return panel;
  }

  window.ViewSide = { on: !!S, paint, _keyRows: keyRows };
})();
