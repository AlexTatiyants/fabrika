/* The rail of projects beside every screen: its filters and search, the
   order a person drags it into, and reading mode, which puts it away. */

'use strict';

/* ------------------------------------------------------------- the rail

   At two or three projects the whole floor fits without scrolling. Past a
   dozen it does not, so two things above the project list carry the weight
   a flat scroll can no longer: `panelStatusEl` says whether anything on the
   floor has stopped before any of it is even in view, and the search + filter
   chrome lets you ask the rail a question instead of scrolling it.
   Individual projects collapse to one line each, open by default only when
   one of their own features is calling.

   `panelOpen` is deliberately not part of `state`: it is which groups are
   open, not data from the server, and it should survive the poll that
   refreshes `state.allFeatures` every few seconds without snapping shut.

   `panelChoice` is the narrower thing: only what the reader themselves opened
   or closed. That is the half worth writing down, because a project the rail
   opened on its own -- because something in it was calling -- should close
   again once it stops, rather than being remembered open forever on the
   strength of a decision nobody made. */

const panelOpen = new Map(); // project_id -> expanded?
const panelChoice = new Map(); // project_id -> expanded?, set by hand only
let panelQuery = '';
let panelFilterMode = 'all'; // all | needs | building | idle
/* The order the reader dragged the projects into. Empty until they touch one,
   and never a complete list: it holds the ids it has heard of, and anything
   else falls in behind them in the order the server gave. */
let panelOrder = [];

const FILTER_MODES = ['all', 'needs', 'building', 'idle'];
/* The rail's shape is a preference, not data: which groups you left open, which
   slice you are looking at, and what you were searching for should all be the
   same after a reload as before it. The query is restored into the box itself
   rather than applied invisibly, so a narrowed floor always has its reason
   sitting above it in plain sight. */
const PANEL_KEY = 'factory.panel';
/* Long enough that a search is stored once rather than once per keystroke. */
const PANEL_SAVE_WAIT = 400;

function loadPanelPrefs() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(PANEL_KEY) || 'null'); } catch (e) { saved = null; }
  if (!saved || typeof saved !== 'object') return;
  if (FILTER_MODES.includes(saved.filter)) panelFilterMode = saved.filter;
  if (typeof saved.query === 'string') panelQuery = saved.query;
  if (Array.isArray(saved.order)) panelOrder = saved.order.filter((id) => typeof id === 'string');
  const groups = saved.groups;
  if (groups && typeof groups === 'object') {
    Object.keys(groups).forEach((id) => {
      if (typeof groups[id] === 'boolean') panelChoice.set(id, groups[id]);
    });
  }
}

function savePanelPrefs() {
  const groups = {};
  panelChoice.forEach((open, id) => { groups[id] = open; });
  try {
    localStorage.setItem(PANEL_KEY,
      JSON.stringify({ filter: panelFilterMode, query: panelQuery, order: panelOrder, groups }));
  } catch (e) { /* private window */ }
}

let panelSaveTimer = null;
function savePanelPrefsSoon() {
  clearTimeout(panelSaveTimer);
  panelSaveTimer = setTimeout(savePanelPrefs, PANEL_SAVE_WAIT);
}

/* The chips and the search box are markup, not state, so a restored mode has to
   be lit and a restored query typed back in by hand once at start -- after that
   the two listeners own them. */
function syncPanelFilters() {
  if (panelFiltersEl) {
    panelFiltersEl.querySelectorAll('.pfilter').forEach((b) => {
      b.classList.toggle('on', b.dataset.filter === panelFilterMode);
    });
  }
  if (panelSearchEl && panelSearchEl.value !== panelQuery) panelSearchEl.value = panelQuery;
}

loadPanelPrefs();
syncPanelFilters();

function isCalling(f) {
  return NEEDS_YOU.includes(f.stage) || f.stage === 'failed' || f.orphaned;
}

/* The rail's order, which is the reader's and nobody else's. A project the
   saved order has never heard of -- registered on another machine, or since
   the last drag -- goes to the back rather than being sorted into a position
   nobody chose for it. Sorting is stable, so those keep the server's order
   among themselves. */
function orderProjects(list) {
  if (!panelOrder.length) return list;
  const rank = new Map(panelOrder.map((id, i) => [id, i]));
  const at = (p) => (rank.has(p.project_id) ? rank.get(p.project_id) : Infinity);
  return list.slice().sort((a, b) => at(a) - at(b));
}

/* Read the rail back out of the DOM after a drag and make that the order.
   Hidden groups are still in the list: a filter narrows what you can see, and
   silently dropping the rest of the floor out of the saved order is not
   something a drag should do. */
function commitPanelOrder() {
  panelOrder = [...panelBody.querySelectorAll('.panel-group')]
    .map((g) => g.dataset.project)
    .filter(Boolean);
  savePanelPrefs();
}

/* What the rail was last built from. A poll lands every few seconds and most
   change nothing on the rail; rebuilding it anyway re-lays out every row and
   drops whatever the pointer is over. */
let lastPanelHtml = null;

function setPanelHtml(html) {
  if (html === lastPanelHtml) return;
  lastPanelHtml = html;
  panelBody.innerHTML = html;
}

function renderPanel() {
  if (!panelBody) return;

  /* A poll lands every few seconds, and rebuilding the list out from under a
     drag would drop the row mid-flight. The redraw is owed, not skipped: the
     drag's end pays it. */
  if (panelDragging) { panelRedrawOwed = true; return; }

  /* Projects that are gone stop being remembered, so a long-lived browser does
     not carry a growing list of choices about work that no longer exists. */
  if (state.projects.length) {
    const live = new Set(state.projects.map((p) => p.project_id));
    let dropped = false;
    panelChoice.forEach((_open, id) => {
      if (!live.has(id)) { panelChoice.delete(id); dropped = true; }
    });
    if (dropped) savePanelPrefs();
  }

  if (!state.projects.length) {
    setPanelHtml('<p class="panel-empty">No projects yet.</p>');
  } else {
    const byProject = {};
    state.allFeatures.forEach((f) => {
      (byProject[f.project_id] || (byProject[f.project_id] = [])).push(f);
    });

    setPanelHtml(orderProjects(state.projects).map((p) => {
      const all = byProject[p.project_id] || [];
      // Recency, not urgency. Ordering work across features is the human's call.
      const active = all.filter((f) => ACTIVE_STAGES.includes(f.stage));
      const settled = all.length - active.length;
      const projectNeeds = p.stage === 'awaiting_approval';
      const isCurrent = p.project_id === state.projectId;
      const isIdle = !active.length && !projectNeeds;

      // Collapsed by default; a project only earns the room to stay open by
      // having something in it that is actually calling. Opening or closing it
      // by hand outranks that, and that choice is the one kept across reloads.
      if (panelChoice.has(p.project_id)) {
        panelOpen.set(p.project_id, panelChoice.get(p.project_id));
      } else if (!panelOpen.has(p.project_id)) {
        panelOpen.set(p.project_id, projectNeeds || active.some(isCalling));
      }
      const open = panelOpen.get(p.project_id);

      const rows = active.map((f) => {
        const needs = NEEDS_YOU.includes(f.stage);
        // The band is always shown, not only while an agent is running: at a
        // glance the rail should answer "where is each of these" in the same
        // words the diagram and the stage strip use.
        const band = STAGE_BAND[f.stage] || '';
        // A packet waiting on a ruling says which way it leans, so the rail can
        // tell "ready to ship" from "do not ship" without opening either.
        const verdict = f.stage === 'awaiting_verdict' && RAIL_VERDICT[f.verdict];
        const detail = f.phase
          ? f.phase + (f.phase_status === 'failed' ? ' failed'
            : f.orphaned ? ' stopped' : '…')
          : f.orphaned ? 'stopped'
            : verdict ? verdict[0]
              : GATE_OF[f.stage] || STAGE_WORDS[f.stage] || f.stage;
        const classes = [
          // `failed` is what the row looks like and what the filters read. The
          // stage never got there, so it is added here rather than read off.
          'panel-feature', f.stage, f.orphaned ? 'failed' : '',
          needs ? 'needs' : '',
          verdict && !f.orphaned ? `v-${verdict[1]}` : '',
          f.feature_id === state.id ? 'current' : '',
        ].filter(Boolean).join(' ');
        // Two targets, so the log is one click from the rail rather than buried
        // in a breadcrumb. A nested anchor is invalid, hence the wrapper.
        return `<div class="${classes} ${f.feature_id === state.id && state.view === 'log' ? 'log-open' : ''}">
          <a class="pf-main" href="${featureHref(f.project_id, f.feature_id)}"
            ${f.feature_id === state.id ? 'aria-current="page"' : ''}>
            <span class="ftitle">${esc(f.title)}</span>
            <span class="fstage"><i class="fdot"></i>
              <b class="fband lane-${esc(band)}">${esc(band)}</b><span class="fsep">·</span><span class="fdetail" title="${esc(verdict ? `Review says: ${VERDICT_WORD[f.verdict][0]}` : detail)}">${esc(detail)}</span>
            </span>
          </a>
          <a class="pf-log" href="${logHref(f.project_id, f.feature_id)}"
            title="Agent log — every exchange, on a timeline"
            aria-label="Agent log for ${esc(f.title)}">
            <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false">
              <rect x="1" y="2" width="10" height="1.4" rx=".5"/>
              <rect x="1" y="5.3" width="7" height="1.4" rx=".5"/>
              <rect x="1" y="8.6" width="9" height="1.4" rx=".5"/>
            </svg>
          </a>
          <button class="pf-log pf-discard act-discard" type="button"
            data-project="${esc(f.project_id)}" data-feature="${esc(f.feature_id)}"
            title="Discard — the ledger and the checkout go; the branch is kept"
            aria-label="Discard ${esc(f.title)}">
            <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false">
              <rect x="2" y="2.4" width="8" height="1.2" rx=".4"/>
              <rect x="4.6" y=".8" width="2.8" height="1.2" rx=".4"/>
              <path d="M3 4.4h6l-.5 6.2a.6.6 0 0 1-.6.6H4.1a.6.6 0 0 1-.6-.6z"/>
            </svg>
          </button>
        </div>`;
      }).join('');

      const meta = projectNeeds
        ? 'repo ready'
        : (active.length ? `${active.length}` : '');
      const displayName = p.name || p.project_id;

      return `<div class="panel-group ${open ? 'open' : ''}"
                   data-project="${esc(p.project_id)}" data-name="${esc(displayName.toLowerCase())}"
                   data-display="${esc(displayName)}" data-idle="${isIdle ? '1' : '0'}">
        <div class="panel-project-row" title="Drag to reorder \u2014 or alt + \u2191 / \u2193">
          <button type="button" class="pg-toggle" data-pg-toggle="${esc(p.project_id)}"
                  aria-expanded="${open ? 'true' : 'false'}"
                  aria-label="${open ? 'Collapse' : 'Expand'} ${esc(displayName)}">
            <span class="pg-chev" aria-hidden="true">›</span>
          </button>
          <a class="panel-project ${isCurrent ? 'current' : ''} ${projectNeeds ? 'needs' : ''}"
             href="#/${encodeURIComponent(p.project_id)}" draggable="false">
            <span class="pname">${esc(displayName)}</span>
            <span class="pmeta">${esc(meta)}</span>
          </a>
        </div>
        <div class="panel-group-body">
          ${rows}
          ${isIdle
            ? `<a class="panel-quiet" href="#/${encodeURIComponent(p.project_id)}">nothing in flight</a>` : ''}
          ${settled
            ? `<a class="panel-quiet" href="#/${encodeURIComponent(p.project_id)}">${settled} settled</a>` : ''}
        </div>
      </div>`;
    }).join(''));
  }

  renderPanelStatus();
  applyPanelFilter();
}

/* The one thing about the whole floor worth saying before the rail is
   scrolled: something has stopped. What needs you is already on the rail and
   behind the NEEDS YOU filter, so a count of it says nothing new. Renders
   nothing when all is well rather than a proud "0 stopped". */
function renderPanelStatus() {
  if (!panelStatusEl) return;
  /* A run whose owner died never reached `failed` -- nothing was alive to
     write that down. It is stopped all the same, and the chip that leaves it
     out is the reason nobody notices for hours. */
  const stopped = state.allFeatures.filter(
    (f) => f.stage === 'failed' || f.orphaned).length;

  if (!stopped) { panelStatusEl.hidden = true; return; }
  panelStatusEl.hidden = false;
  panelStatusEl.innerHTML = `<span class="ps-chip red"><b>${stopped}</b> stopped</span>`;
}

/* Search and the quick filters both narrow the same DOM rather than asking
   the server for anything new, so they apply instantly and survive the next
   poll without re-fetching. */
function applyPanelFilter() {
  const q = panelQuery.trim().toLowerCase();
  /* The hairline between projects is drawn on top of each group, so whichever
     one a filter leaves at the top must not draw it -- otherwise the rail
     opens with a rule under the chips and nothing above it. */
  let firstVisible = true;
  panelBody.querySelectorAll('.panel-group').forEach((group) => {
    const name = group.dataset.name || '';
    const isIdle = group.dataset.idle === '1';
    let anyVisible = false;

    group.querySelectorAll('.panel-feature').forEach((row) => {
      const urgent = row.classList.contains('needs') || row.classList.contains('failed');
      const passesFilter = panelFilterMode === 'all' ? true
        : panelFilterMode === 'needs' ? urgent
        : panelFilterMode === 'building' ? !urgent
        : false; // 'idle' is a project-level state; no individual row qualifies
      const title = (row.querySelector('.ftitle')?.textContent || '').toLowerCase();
      const show = passesFilter && (!q || title.includes(q) || name.includes(q));
      row.hidden = !show;
      if (show) anyVisible = true;
    });
    group.querySelectorAll('.panel-quiet').forEach((el) => {
      el.hidden = panelFilterMode === 'needs' || panelFilterMode === 'building';
    });

    const passesFilter = panelFilterMode === 'all' ? true
      : panelFilterMode === 'idle' ? isIdle
      : anyVisible;
    const visible = passesFilter && (!q || name.includes(q) || anyVisible);
    group.hidden = !visible;
    group.classList.toggle('pg-first', visible && firstVisible);
    if (visible) firstVisible = false;
    if (q && anyVisible) {
      panelOpen.set(group.dataset.project, true);
      group.classList.add('open');
      const toggle = group.querySelector('.pg-toggle');
      if (toggle) {
        toggle.setAttribute('aria-expanded', 'true');
        toggle.setAttribute('aria-label', `Collapse ${group.dataset.display || group.dataset.project}`);
      }
    }
  });
}

/* The rail is two different things at two widths. Wide: a fixture you can put
   away, and it stays away. Narrow: a disclosure over the page. One button, and
   which meaning it carries depends only on how much room there is. */

const RAIL_KEY = 'factory.rail';
const wideEnough = () => window.matchMedia('(min-width: 1024px)').matches;
const railOff = () => document.body.getAttribute('data-rail') === 'off';

function syncToggle() {
  const shown = wideEnough() ? !railOff() : document.body.getAttribute('data-panel') === 'open';
  document.querySelectorAll('.panel-toggle').forEach((btn) => {
    btn.setAttribute('aria-expanded', String(shown));
    btn.setAttribute('aria-label', shown ? 'Hide active work' : 'Show active work');
    const chev = btn.querySelector('.pt-chev');
    if (chev) chev.textContent = shown ? '\u2039' : '\u203a';
  });
}

function setRail(off) {
  document.body.setAttribute('data-rail', off ? 'off' : 'on');
  try { localStorage.setItem(RAIL_KEY, off ? 'off' : 'on'); } catch (e) { /* private window */ }
  syncToggle();
}

function closePanel() {
  document.body.removeAttribute('data-panel');
  if (panelScrim) panelScrim.hidden = true;
  syncToggle();
}

/* Delegated: the board's copy of this button is redrawn on every render. */
document.addEventListener('click', (e) => {
  if (!e.target.closest || !e.target.closest('.panel-toggle')) return;
  if (wideEnough()) return setRail(!railOff());
  if (document.body.getAttribute('data-panel') === 'open') return closePanel();
  document.body.setAttribute('data-panel', 'open');
  if (panelScrim) panelScrim.hidden = false;
  syncToggle();
});
if (panelScrim) panelScrim.addEventListener('click', closePanel);

/* Search and the quick filters live outside `panelBody`, so re-rendering the
   project list on every poll never touches this input's focus or caret. */
if (panelSearchEl) {
  panelSearchEl.addEventListener('input', () => {
    panelQuery = panelSearchEl.value;
    savePanelPrefsSoon();
    applyPanelFilter();
  });
}
if (panelFiltersEl) {
  panelFiltersEl.addEventListener('click', (e) => {
    const btn = e.target.closest('.pfilter');
    if (!btn) return;
    panelFiltersEl.querySelectorAll('.pfilter').forEach((b) => b.classList.toggle('on', b === btn));
    panelFilterMode = btn.dataset.filter;
    savePanelPrefs();
    applyPanelFilter();
  });
}
/* ---------------------------------------------------------- reordering

   The rail's order is the reader's. Nothing on the server has an opinion about
   which project should sit at the top -- but a person with eight of them does,
   and it is usually "the one I am working in", which no amount of sorting by
   name or by activity would ever land on.

   The row is the drag handle, so there is no extra furniture in a rail that is
   264px wide. The project link inside it is marked `draggable="false"`, because
   a browser drags an anchor's own URL by default and that would win over this.

   Dragging is not the only way in: alt + arrow moves the focused project by
   one, which is the same operation for anyone not using a mouse -- and faster
   than a drag when the move is one place. */

const DRAG_SLOP = 4;         // px of travel before a press becomes a drag
let panelDrag = null;        // {group, y0, live, id} while a pointer is down
let panelDragging = null;    // project_id currently in flight, or null
let panelRedrawOwed = false; // a poll landed mid-drag and was turned away
let swallowClick = false;    // eat the click that a finished drag leaves behind

function endPanelDrag() {
  panelDragging = null;
  panelBody.querySelectorAll('.dragging').forEach((el) => el.classList.remove('dragging'));
  document.body.classList.remove('rail-dragging');
  commitPanelOrder();
  // The separator follows visible order, so it has to be recomputed even when
  // no redraw is owed.
  applyPanelFilter();
  if (panelRedrawOwed) { panelRedrawOwed = false; renderPanel(); }
}

/* Move one group by one place among the groups the reader can actually see.
   Stepping over a filtered-out project would look like the row skipped two. */
function nudgeGroup(group, dir) {
  const shown = [...panelBody.querySelectorAll('.panel-group')].filter((g) => !g.hidden);
  const i = shown.indexOf(group);
  const next = shown[i + dir];
  if (i < 0 || !next) return false;
  panelBody.insertBefore(dir > 0 ? next : group, dir > 0 ? group : next);
  commitPanelOrder();
  applyPanelFilter();
  return true;
}

if (panelBody) {
  /* Pointer events rather than HTML5 drag-and-drop. The native API would be
     less code, but it has no touch story at all and it cannot be driven by a
     synthesised pointer -- so the one gesture this whole feature is made of
     would be the one thing never exercised. This path is the same events
     a real hand produces.

     Touch is left alone on purpose: the rail is a scrolling list, and claiming
     a finger's drag here would cost scrolling to buy reordering. */
  panelBody.addEventListener('pointerdown', (e) => {
    swallowClick = false; // a stale one must never outlive a fresh press
    if (e.button !== 0 || e.pointerType === 'touch') return;
    const row = e.target.closest && e.target.closest('.panel-project-row');
    const group = row && row.closest('.panel-group');
    if (!group) return;
    panelDrag = { group, y0: e.clientY, live: false, id: e.pointerId };
  });

  panelBody.addEventListener('pointermove', (e) => {
    if (!panelDrag || e.pointerId !== panelDrag.id) return;
    if (!panelDrag.live) {
      // A press is a click until it travels. Without the threshold, opening a
      // project with a slightly unsteady hand would start a reorder instead.
      if (Math.abs(e.clientY - panelDrag.y0) < DRAG_SLOP) return;
      panelDrag.live = true;
      panelDragging = panelDrag.group.dataset.project;
      panelDrag.group.classList.add('dragging');
      document.body.classList.add('rail-dragging');
      // Captured on the rail, not on the row: the row is inside the element
      // being moved, and capture should not depend on where it ends up.
      try { panelBody.setPointerCapture(e.pointerId); } catch (err) { /* gone */ }
    }
    e.preventDefault();
    /* Reordering live rather than against a placeholder: the row you are
       holding is the preview, so what you see mid-drag is the result. */
    const under = document.elementFromPoint(e.clientX, e.clientY);
    const over = under && under.closest && under.closest('.panel-group');
    if (!over || over === panelDrag.group || over.hidden || over.parentElement !== panelBody) return;
    const box = over.getBoundingClientRect();
    panelBody.insertBefore(panelDrag.group, e.clientY > box.top + box.height / 2 ? over.nextSibling : over);
  });

  const finishDrag = (e) => {
    if (!panelDrag || (e && e.pointerId !== panelDrag.id)) return;
    const live = panelDrag.live;
    if (e) { try { panelBody.releasePointerCapture(e.pointerId); } catch (err) { /* gone */ } }
    panelDrag = null;
    if (!live) return; // a press that never travelled is just a click
    // The pointerup that ends a drag is followed by a click on the link under
    // it, which would navigate away from the project you just moved.
    swallowClick = true;
    endPanelDrag();
  };
  panelBody.addEventListener('pointerup', finishDrag);
  panelBody.addEventListener('pointercancel', finishDrag);

  panelBody.addEventListener('keydown', (e) => {
    if (!e.altKey || e.metaKey || e.ctrlKey) return;
    const dir = e.key === 'ArrowUp' ? -1 : e.key === 'ArrowDown' ? 1 : 0;
    if (!dir) return;
    const group = e.target.closest && e.target.closest('.panel-group');
    if (!group) return;
    e.preventDefault();
    if (!nudgeGroup(group, dir)) return;
    const shown = [...panelBody.querySelectorAll('.panel-group')].filter((g) => !g.hidden);
    announce(`${group.dataset.display || group.dataset.project}, ${shown.indexOf(group) + 1} of ${shown.length}`);
  });
}

document.addEventListener('click', (e) => {
  if (!swallowClick) return;
  swallowClick = false;
  if (e.target.closest && e.target.closest('.panel-project-row')) {
    e.preventDefault();
    e.stopPropagation();
  }
}, true);

const isTyping = (el) => !!el && (el.isContentEditable
  || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName));

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    closePanel();
    /* The way out of reading mode you try first. */
    if (focusOn() && focusable()) setFocus(false);
  }
  const bare = !event.metaKey && !event.ctrlKey && !event.altKey;
  /* The rail is the first thing reading mode folds away, so the key that
     folds the rail is the key that unfolds everything again. */
  if (event.key === '[' && bare && !isTyping(event.target)
      && focusOn() && focusable()) {
    event.preventDefault();
    setFocus(false);
  } else if (event.key === '[' && bare && wideEnough() && !isTyping(event.target)) {
    event.preventDefault();
    setRail(!railOff());
  }
  if ((event.key === 'f' || event.key === 'F') && bare && !isTyping(event.target)
      && focusable()) {
    event.preventDefault();
    setFocus(!focusOn());
  }
  /* The rail folds with [, so the line folds with the key beside it. */
  if (event.key === '\\' && !event.metaKey && !event.ctrlKey && !event.altKey
      && !isTyping(event.target)) {
    event.preventDefault();
    setStripOpen(!stripOpen());
    render();
  }
});

try {
  document.body.setAttribute('data-rail',
    localStorage.getItem(RAIL_KEY) === 'off' ? 'off' : 'on');
} catch (e) { document.body.setAttribute('data-rail', 'on'); }
window.matchMedia('(min-width: 1024px)').addEventListener('change', syncToggle);
syncToggle();

/* ------------------------------------------------------------ reading mode

   The spec is the one screen on this console that is a document rather than a
   control panel: nine sections read top to bottom before a decision. Around it
   sit a rail of other work, a board carrying the title, the sixteen lamps and
   the steps -- all of which answer "where am I", which is not a question you
   have while reading. Folded away, the window is the document and nothing
   else. It is a preference, kept per browser, and it does not survive leaving
   the spec: the screens that are control panels get their chrome back.

   The board goes with the rest, and the approve and send-back buttons go with
   the board -- deliberately. Reading is not deciding; you come back out to
   decide, with the escape key or the same corner you left by. */
const FOCUS_KEY = 'factory.focus';
let focusPref = null;
function focusOn() {
  if (focusPref === null) {
    try { focusPref = localStorage.getItem(FOCUS_KEY) === 'on'; }
    catch (e) { focusPref = false; }
  }
  return focusPref;
}
function setFocus(on) {
  focusPref = on;
  try { localStorage.setItem(FOCUS_KEY, on ? 'on' : 'off'); } catch (e) { /* private window */ }
  /* Through a render rather than by patching the button where it stands. Half
     the screens that carry one are reconciled rather than rebuilt, and reaching
     into their DOM behind the reconciler is how the icon and the mode come to
     disagree on the next poll. */
  render();
}

/* Only on a screen that put one of these buttons on the page. Everywhere else
   the preference is remembered and simply not applied. */
function focusable() { return !!document.querySelector('.focus-toggle'); }

function syncFocus() {
  document.body.classList.toggle('reading', focusOn() && focusable());
}

/* Delegated: every copy of this button is redrawn on each render. */
document.addEventListener('click', (e) => {
  if (!e.target.closest || !e.target.closest('.focus-toggle')) return;
  setFocus(!focusOn());
});

function focusToggle() {
  const on = focusOn();
  const word = on ? 'Leave reading mode' : 'Read this full screen';
  return `<button type="button" class="focus-toggle" aria-pressed="${on}"
    aria-label="${esc(word)}" title="${esc(word)} -- ${on ? 'Esc' : 'F'}">
    <svg class="ic" aria-hidden="true"><use href="#i-l-${on ? 'minimize' : 'maximize'}"/></svg>
  </button>`;
}
