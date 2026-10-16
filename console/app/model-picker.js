/* The model picker: one control for choosing a model on any route.

   A command-line route or a local server offers a handful of models; an
   aggregator offers hundreds, and a dropdown of hundreds is not a choice. So
   the choice is a search, over rows the server reads from each route's own
   model list (`/api/routes/<name>/catalog`), filtered to what the agents
   being placed need of a model: tool calls for the harness agents, a schema
   for all of them, room for their largest call. Those come from the server
   too, not from here. A fact a route does not report is unknown, and an
   unknown never filters a model out -- a short list from a config file says
   nothing about tools, and every model on it stays offered.

   It is its own small piece of DOM, opened over whatever asked for it, and
   it never redraws the screen under it: the role sheet holds an editor that a
   redraw would lose. The caller says what to offer and what a pick does.

     openModelPicker(anchor, {
       routes,           // [{name, label, account_label, default_model_ok}]
       route,            // the route it opens on
       roles,            // the agents being placed, for what they need
       used,             // [{route, model, why}] -- picks already in this crew
       current,          // {route, model} -- what is set now
       onPick(route, model),
     })
*/

const MP_SHOWN = 200;
const mpCatalogs = {};
let mp = null;

function mpRouteLabel(r) { return r.account_label || r.label || r.name; }

function mpK(n) {
  if (!n) return '';
  return n >= 1000000 ? `${Math.round(n / 100000) / 10}M` : `${Math.round(n / 1000)}k`;
}

function mpPrice(r) {
  if (r.price_in == null && r.price_out == null) return '';
  const p = (v) => (v == null ? '?' : v < 0.01 && v > 0 ? '<0.01' : (Math.round(v * 100) / 100).toFixed(2));
  return `$${p(r.price_in)} / $${p(r.price_out)}`;
}

/* What the agents being placed need of a model, from the catalogue's `needs`. */
function mpNeeds(body, roles) {
  const all = (body && body.needs) || {};
  const of = roles.map((n) => all[n]).filter(Boolean);
  return {
    tools: of.some((x) => x.tools),
    structured: of.some((x) => x.structured),
    context: Math.max(0, ...of.map((x) => x.context || 0)),
  };
}

/* Whether a row is shown: every word searched for is somewhere in it -- so
   "vendor v4" finds `vendor/model-v4-pro` -- and no filter that is on
   knows it to be unable. */
function mpMatches(r) {
  const words = mp.q.toLowerCase().split(/[\s/:_-]+/).filter(Boolean);
  const text = `${r.id} ${r.name || ''} ${r.vendor || ''}`.toLowerCase();
  const f = mp.filters;
  return words.every((w) => text.includes(w))
    && (!f.tools || r.tools !== false)
    && (!f.structured || r.structured !== false)
    && (!f.thinking || r.thinking !== false)
    && (!f.fits || r.context == null || r.context >= mp.needs.context);
}

function mpVisible() {
  const body = mpCatalogs[mp.route];
  return ((body && body.models) || []).filter(mpMatches);
}

function mpRow(r, i, why = '') {
  const on = mp.current && mp.current.route === mp.route && mp.current.model === r.id;
  const sub = why || r.name || r.vendor;
  return `<button type="button" class="mp-row ${i === mp.active ? 'active' : ''} ${on ? 'on' : ''}"
      data-mp-pick="${esc(r.id)}" title="${esc(r.id)}" role="option" aria-selected="${on}">
      <span class="mp-id"><span class="mp-m">${esc(modelName(r.id) || 'its own default model')}</span>
        ${sub || r.thinking ? `<span class="mp-sub">${esc(sub)}${r.thinking ? `${sub ? ' · ' : ''}thinking` : ''}</span>` : ''}</span>
      <span class="mp-num">${esc(mpK(r.context))}</span>
      <span class="mp-num mp-price">${esc(mpPrice(r))}</span>
      <span class="mp-num">${r.tools ? 'tools' : ''}</span>
    </button>`;
}

function mpDraw() {
  if (!mp) return;
  const body = mpCatalogs[mp.route];
  const list = mp.el.querySelector('.mp-list');
  const count = mp.el.querySelector('.mp-count');
  if (!body) {
    list.innerHTML = '<p class="mp-note">Reading the models this route offers…</p>';
    count.textContent = '';
    return;
  }
  const visible = mpVisible();
  mp.rows = visible.slice(0, MP_SHOWN);
  const route = mp.routes.find((r) => r.name === mp.route) || {};
  const own = route.default_model_ok ? [{ id: '', name: 'whatever the tool is set to use' }] : [];
  const used = (mp.used || []).filter((u) => u.route === mp.route && u.model);
  const known = new Map((body.models || []).map((r) => [r.id, r]));
  const quick = used.map((u) => ({ ...(known.get(u.model) || { id: u.model }), why: u.why }))
    .filter(mpMatches);
  mp.rows = [...own, ...quick, ...mp.rows.filter((r) => !used.some((u) => u.model === r.id))];
  if (mp.active >= mp.rows.length) mp.active = Math.max(0, mp.rows.length - 1);
  let i = 0;
  const part = (rows, head) => (rows.length
    ? `${head ? `<p class="mp-grp">${esc(head)}</p>` : ''}${rows.map((r) => mpRow(r, i++, r.why || '')).join('')}` : '');
  const rest = mp.rows.slice(own.length + quick.length);
  list.innerHTML = part(own, '') + part(quick, 'Already used in this crew')
    + part(rest, quick.length ? 'Everything else' : '')
    + (body.problem ? `<p class="mp-note bad">${esc(body.problem)}</p>` : '')
    + (!mp.rows.length ? `<p class="mp-note">Nothing matches.${
      Object.values(mp.filters).some(Boolean) ? ' Turn a filter off to see models these agents may not be able to use.' : ''}</p>` : '')
    + (visible.length > MP_SHOWN ? `<p class="mp-note">${visible.length - MP_SHOWN} more — type to narrow.</p>` : '');
  const total = (body.models || []).length;
  count.textContent = total > 12 ? `${visible.length} of ${total}` : '';
  mp.el.querySelectorAll('[data-mp-filter]').forEach((b) => {
    b.classList.toggle('on', !!mp.filters[b.dataset.mpFilter]);
    b.setAttribute('aria-pressed', String(!!mp.filters[b.dataset.mpFilter]));
  });
  const fits = mp.el.querySelector('[data-mp-filter="fits"]');
  if (fits) fits.textContent = mp.needs.context ? `fits these agents (≥ ${mpK(mp.needs.context)})` : 'fits these agents';
}

async function mpLoad(route, refresh = false) {
  mp.route = route;
  mp.active = 0;
  mpDraw();
  if (!mpCatalogs[route] || refresh) {
    try {
      mpCatalogs[route] = await api(`/api/routes/${encodeURIComponent(route)}/catalog${refresh ? '?refresh=1' : ''}`);
    } catch (err) {
      mpCatalogs[route] = { models: [], needs: {}, problem: `The route's models could not be read: ${err.message || err}` };
    }
  }
  if (!mp || mp.route !== route) return;
  mp.needs = mpNeeds(mpCatalogs[route], mp.roles);
  // What these agents need is where the filters start, not where they end.
  if (!mp.touched) mp.filters = { tools: mp.needs.tools, structured: mp.needs.structured, thinking: false, fits: false };
  mpDraw();
}

function mpPlace() {
  if (!mp) return;
  const r = mp.anchor.getBoundingClientRect();
  const w = Math.min(600, window.innerWidth - 32);
  const left = Math.max(16, Math.min(r.left, window.innerWidth - w - 16));
  const below = window.innerHeight - r.bottom - 16;
  const h = Math.min(460, Math.max(below, r.top - 16));
  mp.el.style.width = `${w}px`;
  mp.el.style.left = `${left}px`;
  mp.el.style.maxHeight = `${h}px`;
  if (below >= Math.min(320, h)) {
    mp.el.style.top = `${r.bottom + 6}px`; mp.el.style.bottom = '';
  } else {
    mp.el.style.bottom = `${window.innerHeight - r.top + 6}px`; mp.el.style.top = '';
  }
}

function closeModelPicker() {
  if (!mp) return;
  mp.el.remove();
  document.removeEventListener('mousedown', mpOutside, true);
  window.removeEventListener('resize', mpPlace);
  const anchor = mp.anchor;
  mp = null;
  if (anchor && anchor.isConnected) anchor.focus();
}

function mpOutside(event) {
  if (mp && !mp.el.contains(event.target) && event.target !== mp.anchor) closeModelPicker();
}

function mpChoose(model) {
  const { route, onPick } = mp;
  closeModelPicker();
  onPick(route, model);
}

function openModelPicker(anchor, opts) {
  closeModelPicker();
  const routes = (opts.routes || []).filter((r) => r.enabled !== false);
  const start = opts.route && routes.some((r) => r.name === opts.route) ? opts.route : (routes[0] || {}).name;
  if (!start) return;
  const el = document.createElement('div');
  el.className = 'mp';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-label', 'Choose a model');
  el.innerHTML = `
    <div class="mp-head">
      ${routes.length > 1 ? `<select class="mp-route" aria-label="Provider">${routes.map((r) =>
        `<option value="${esc(r.name)}" ${r.name === start ? 'selected' : ''}>${esc(mpRouteLabel(r))}</option>`).join('')}</select>`
        : `<span class="mp-one">${esc(mpRouteLabel(routes[0]))}</span>`}
      <input class="mp-q" type="search" placeholder="Search by name or vendor" aria-label="Search models"
        autocomplete="off" spellcheck="false">
    </div>
    <div class="mp-filters">
      <button type="button" class="mp-chip" data-mp-filter="tools" title="Harness agents work through tool calls.">tool calls</button>
      <button type="button" class="mp-chip" data-mp-filter="structured" title="Every agent answers to a schema.">structured answers</button>
      <button type="button" class="mp-chip" data-mp-filter="thinking">thinking levels</button>
      <button type="button" class="mp-chip" data-mp-filter="fits">fits these agents</button>
      <span class="mp-count"></span>
    </div>
    <div class="mp-cols"><span>model</span><span>context</span><span>$ in / out per M</span><span></span></div>
    <div class="mp-list" role="listbox"></div>`;
  document.body.appendChild(el);
  mp = { el, anchor, routes, route: start, roles: opts.roles || [], used: opts.used || [],
         current: opts.current || null, onPick: opts.onPick, q: '', filters: {}, needs: { context: 0 },
         rows: [], active: 0, touched: false };
  el.querySelector('.mp-q').addEventListener('input', (e) => { mp.q = e.target.value; mp.active = 0; mpDraw(); });
  const select = el.querySelector('.mp-route');
  if (select) select.addEventListener('change', (e) => mpLoad(e.target.value));
  el.addEventListener('click', (e) => {
    const f = e.target.closest('[data-mp-filter]');
    if (f) { mp.touched = true; mp.filters[f.dataset.mpFilter] = !mp.filters[f.dataset.mpFilter]; mpDraw(); return; }
    const row = e.target.closest('[data-mp-pick]');
    if (row) mpChoose(row.dataset.mpPick);
  });
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); closeModelPicker(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      mp.active = Math.max(0, Math.min(mp.rows.length - 1, mp.active + (e.key === 'ArrowDown' ? 1 : -1)));
      mpDraw();
      const a = mp.el.querySelector('.mp-row.active');
      if (a) a.scrollIntoView({ block: 'nearest' });
      return;
    }
    if (e.key === 'Enter' && e.target.classList.contains('mp-q') && mp.rows[mp.active]) {
      e.preventDefault();
      mpChoose(mp.rows[mp.active].id);
    }
  });
  document.addEventListener('mousedown', mpOutside, true);
  window.addEventListener('resize', mpPlace);
  mpPlace();
  el.querySelector('.mp-q').focus();
  mpLoad(start);
}
