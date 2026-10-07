/* Help: the topics in console/help/, as a page of their own and as a drawer
   over whatever is open. */

'use strict';

/* ------------------------------------------------------------------ help

   How Fabrika works and what it needs from you, written once, in
   `console/help/` -- a manifest of topics and one plain HTML file each, every
   one opening with a sentence a person can act on. Two ways in, one content:
   a page of its own (`#/?help`, `#/?help=<topic>`), readable with no project
   open and linkable, and a drawer that opens a topic over whatever you are
   looking at -- from a `?` on a section heading, or the `?` key -- and closes
   back to it. A link inside an article (`data-help`) stays in whichever of
   the two it was read in. */
const HELP_START = 'how-fabrika-works';
/* A project tab's `?`, and the `?` key on that tab. */
const HELP_FOR = {
  checks: 'checks', tests: 'test-levels', guides: 'guides', survey: 'survey', environment: 'environment',
  history: 'history',
};
/* A feature's screens: the view in the hash when there is one, and otherwise
   the stage, which is what picks the screen. */
const HELP_FOR_VIEW = {
  intent: 'writing-the-intent', scout: 'writing-the-intent', questions: 'the-questions', spec: 'the-spec',
  build: 'watching-the-build', log: 'watching-the-build', review: 'your-calls', calls: 'your-calls',
  map: 'reading-the-work', criterion: 'reading-the-work', file: 'reading-the-work', evidence: 'evidence',
  dependencies: 'evidence', objections: 'objections', blindspots: 'objections', repairs: 'the-repair-loop',
  rework: 'the-repair-loop', packet: 'ruling-on-a-packet',
};
const HELP_FOR_STAGE = {
  intake: 'writing-the-intent', awaiting_answers: 'the-questions', writing_spec: 'the-spec',
  awaiting_spec_approval: 'the-spec', awaiting_cut_approval: 'plan-review',
  waiting_for_plan: 'waiting-for-a-plan-window', building: 'watching-the-build', failed: 'running-again',
  awaiting_verdict: 'the-review-packet', accepted: 'ruling-on-a-packet', rejected: 'ruling-on-a-packet',
};
const help = { topics: null, articles: {}, query: '', drawer: null };

async function loadHelp(slug) {
  if (!help.topics) {
    help.topics = await fetch('/static/help/topics.json', { cache: 'no-store' })
      .then((r) => r.json()).catch(() => ({ groups: [] }));
  }
  if (slug && !(slug in help.articles)) {
    help.articles[slug] = await fetch(`/static/help/${encodeURIComponent(slug)}.html`,
      { cache: 'no-store' }).then((r) => (r.ok ? r.text() : null)).catch(() => null);
  }
}

function helpTopicList() {
  return ((help.topics || {}).groups || []).flatMap((g) => g.topics.map((t) => ({ ...t, group: g.title })));
}

function helpTitle(slug) {
  return (helpTopicList().find((t) => t.slug === slug) || {}).title || 'Help';
}

/* Titles first; then, once every article has been fetched, their text. Small
   enough to read all of it, and a match in the body is usually the one that
   answers the question. */
function helpMatches(q) {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return null;
  const text = (html) => (html || '').replace(/<[^>]+>/g, ' ').toLowerCase();
  return new Set(helpTopicList().filter((t) => words.every((w) =>
    t.title.toLowerCase().includes(w) || text(help.articles[t.slug]).includes(w))).map((t) => t.slug));
}

function helpNav(current, inDrawer) {
  const hits = helpMatches(help.query);
  const groups = ((help.topics || {}).groups || []).map((g) => {
    const topics = g.topics.filter((t) => !hits || hits.has(t.slug));
    if (!topics.length) return '';
    return `<p>${esc(g.title)}</p>${topics.map((t) => `<a class="${t.slug === current ? 'on' : ''}"
      ${inDrawer ? `href="#" data-help="${esc(t.slug)}"` : `href="${HELP_HREF}=${esc(t.slug)}"`}
      >${esc(t.title)}</a>`).join('')}`;
  }).join('');
  return `<nav class="hp-nav" aria-label="Help topics">${groups
    || (help.topics ? '<p class="hp-none">Nothing in help matches that.</p>' : '')}</nav>`;
}

function helpArticle(slug) {
  const body = help.articles[slug];
  return `<article class="hp-art">
      <h3>${esc(helpTitle(slug))}</h3>
      ${body === undefined ? '<p class="empty">Loading…</p>'
        : body === null ? `<p class="empty">There's no help page called “${esc(slug)}”.
            <a href="${HELP_HREF}">Start here</a>.</p>`
        : body}
    </article>`;
}

function helpScreen() {
  const slug = state.helpTopic || HELP_START;
  return `
    <div class="wrap">
      <section class="roles-top">
        <h1 class="crew-title"><span>Help.</span></h1>
        <p class="crew-sub">How Fabrika works and what it needs from you, in one place. Screens
          link here where it helps, and you don't need a project open to read it.</p>
        <input class="hp-search hp-search-page" type="search" data-help-search="page"
          placeholder="Search help…" value="${esc(help.query)}" aria-label="Search help">
      </section>
      <div class="hp-page">${helpNav(slug, false)}${helpArticle(slug)}</div>
    </div>`;
}

let helpHost = null;

function renderHelpDrawer() {
  if (!help.drawer) {
    if (helpHost) helpHost.innerHTML = '';
    return;
  }
  if (!helpHost) {
    helpHost = document.createElement('div');
    helpHost.id = 'help-host';
    document.body.appendChild(helpHost);
  }
  const slug = help.drawer;
  helpHost.innerHTML = `
    <div class="hp-scrim" data-help-close="1"></div>
    <aside class="hp" role="dialog" aria-modal="true" aria-label="Help">
      <div class="hp-top"><h2>Help</h2>
        <input class="hp-search" type="search" data-help-search="drawer" placeholder="Search help…"
          value="${esc(help.query)}" aria-label="Search help">
        <a class="btn btn-sm btn-quiet" href="${HELP_HREF}=${esc(slug)}" data-help-close="1">Open as a page</a>
        <button type="button" class="hp-x" data-help-close="1" aria-label="Close help">×</button></div>
      <div class="hp-main">${helpNav(slug, true)}${helpArticle(slug)}</div>
    </aside>`;
}

async function openHelp(slug) {
  help.drawer = slug || HELP_START;
  renderHelpDrawer();
  await loadHelp(help.drawer);
  renderHelpDrawer();
}

function closeHelp() {
  help.drawer = null;
  renderHelpDrawer();
}

/* The topic nearest to what is on screen, for the `?` key. */
function helpForHere() {
  if (state.view === 'help') return state.helpTopic || HELP_START;
  if (state.view === 'roles') return 'the-crew';
  if (state.view === 'agent-config') return 'agent-configuration';
  if (state.view === 'control-room') return 'control-room';
  if (!state.projectId) return 'adding-a-project';
  if (state.id && state.data) {
    // A packet opened with no view lands on the calls, so the screen, not the
    // stage, names the topic there.
    const view = state.view || mountedView();
    return HELP_FOR_VIEW[view] || HELP_FOR_STAGE[state.data.state.stage] || HELP_START;
  }
  if (state.view === 'start') return 'writing-the-intent';
  if (state.view === 'log') return 'history';
  const p = (state.project || {}).project;
  if (!p) return HELP_START;
  const section = projectSection();
  if (section === 'as-built') return abHelpHere();
  if (section === 'results') return 'checks';
  if (HELP_FOR[section]) return HELP_FOR[section];
  // The overview: a project still at gate 0 is one being added.
  return p.stage === 'ready' ? 'your-project' : 'adding-a-project';
}

document.addEventListener('click', (event) => {
  const close = event.target.closest('[data-help-close]');
  if (close && help.drawer) {
    if (close.tagName !== 'A') event.preventDefault();
    closeHelp();
    return;
  }
  const link = event.target.closest('[data-help], [data-help-open]');
  if (!link) return;
  event.preventDefault();
  const slug = link.dataset.help || link.dataset.helpOpen;
  if (link.dataset.helpOpen !== undefined || link.closest('#help-host')) {
    openHelp(slug);
  } else {
    location.hash = `${HELP_HREF}=${encodeURIComponent(slug)}`;
  }
});

// The cost panel closes the way a popover does: a click anywhere else, or Escape.
document.addEventListener('click', (event) => {
  if (state.costInfo && !event.target.closest('.db-pop, [data-cost-info]')) {
    state.costInfo = false;
    render();
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && state.costInfo) { state.costInfo = false; render(); }
});

document.addEventListener('input', async (event) => {
  const box = event.target.closest('[data-help-search]');
  if (!box) return;
  help.query = box.value;
  await loadHelp();
  // Every article, once, so a search reaches their text and not only titles.
  await Promise.all(helpTopicList().map((t) => loadHelp(t.slug)));
  const where = box.dataset.helpSearch;
  if (where === 'drawer') renderHelpDrawer(); else render();
  const again = document.querySelector(`[data-help-search="${where}"]`);
  if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && help.drawer) { closeHelp(); return; }
  if (event.key === '?' && !event.metaKey && !event.ctrlKey && !event.altKey
      && !isTyping(event.target)) {
    event.preventDefault();
    if (help.drawer) closeHelp(); else openHelp(helpForHere());
  }
});

function sectionHead(which, title, job, tally, bars) {
  return `
    <div class="q-head">
      ${SECTION_ICON[which]}
      <h3>${esc(title)}</h3>${HELP_FOR[which] ? `<button type="button" class="q-help"
        data-help-open="${HELP_FOR[which]}" title="What is this?" aria-label="Help: ${esc(title)}">?</button>` : ''}
      <span class="q-job">${esc(job)}</span>
      <span class="q-meter">
        <span class="q-tally">${tally}</span>
        <span class="q-bars">${bars.map((b) => `<i class="${b}"></i>`).join('')}</span>
      </span>${EDITABLE.includes(which) ? editButton(which) : ''}
    </div>`;
}
