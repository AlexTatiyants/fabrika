/* ------------------------------------------------------------------ as-built

   The project's As-built tab: the codebase as it was actually built, for the
   person who has to answer for code agents wrote. It reads what Fabrika
   committed under `.fabrika/as-built/` at the checked-out commit and draws it
   -- the system, one subsystem, one file and its parts, one capability -- with
   a Refresh that reads again only what changed.

   A plain script beside the ones in app/ rather than more of them. It shares
   their globals (`state`, `api`, `esc`, `render`, `refreshProject`, `errorToast`) and
   is called from exactly two places there: the section switch, and the
   progress stream. Nothing here decides anything; it lays out what the
   server computed. */

const AB = { dir: '.fabrika/as-built' };

function abState(pid) {
  if (!state.asBuilt || state.asBuilt.project !== pid) {
    state.asBuilt = { project: pid, status: null, graph: null, at: { kind: 'system', key: '' } };
  }
  return state.asBuilt;
}

/* Product or support, for a reading made before the engine said -- the same
   rule it uses, so an old reading and a new one draw the same. Product first,
   so they carry the low numbers everywhere. */
/* The engine's rule for three letters, for a reading made before it gave them:
   the initials of the words that carry meaning, filled from the last of them,
   varied until nothing else has it. */
const AB_QUIET = new Set(['a', 'an', 'and', 'the', 'of', 'for', 'to', 'in', 'on', 'with', 'its', 'it', 'by', 'at', 'or', 'from']);
function abDerive(name, taken) {
  const words = ((name || '').replace(/['’]/g, '').match(/[A-Za-z]+/g)) || ['X'];
  const strong = words.filter((w) => !AB_QUIET.has(w.toLowerCase()));
  const use = strong.length ? strong : words;
  let base = use.slice(0, 3).map((w) => w[0]).join('');
  for (const ch of use[use.length - 1].slice(1)) { if (base.length >= 3) break; base += ch; }
  base = `${base}XXX`.slice(0, 3).toUpperCase();
  const free = (c) => !taken.has(c);
  if (free(base)) return base;
  for (const ch of `${use.join('').toUpperCase().slice(2)}ABCDEFGHIJKLMNOPQRSTUVWXYZ`) {
    if (free(base.slice(0, 2) + ch)) return base.slice(0, 2) + ch;
  }
  return base;
}

function abNormalize(g) {
  // A code a group may keep is held for it before any is derived, as the engine does.
  const all = [...g.subsystems, ...g.capabilities];
  const usable = (x) => /^[A-Z]{3}$/.test(x.code || '');
  const asked = all.filter(usable).map((x) => x.code);
  const taken = new Set();
  all.forEach((x) => {
    if (!usable(x) || taken.has(x.code)) x.code = abDerive(x.name, new Set([...taken, ...asked]));
    taken.add(x.code);
  });
  if (g.subsystems.some((s) => s.role)) return g;
  if (!(g.ways_in || []).length) return g;
  const reached = new Set(g.capabilities.flatMap((c) => [...c.direct, ...c.in_motion]).map((r) => r.subsystem).filter(Boolean));
  const files = new Map(g.files.map((f) => [f.path, f]));
  const counted = new Set(g.capabilities.flatMap((c) => c.ways_in));
  g.ways_in.forEach((w) => {
    const sub = (files.get(w.file) || {}).subsystem;
    if (sub && counted.has(w.id)) reached.add(sub);
  });
  g.subsystems.forEach((s) => { s.role = reached.has(s.key) ? 'product' : 'support'; });
  g.subsystems.sort((a, b) => (a.role !== 'product') - (b.role !== 'product') || b.lines - a.lines);
  return g;
}

async function asBuiltLoad(pid) {
  const s = abState(pid);
  const base = `/api/projects/${encodeURIComponent(pid)}/as-built`;
  const status = await api(base).catch(() => null);
  if (status) s.status = status;
  if (status && status.exists && (!s.graph || s.graph.commit !== status.commit)) {
    const graph = await api(`${base}/graph`).catch(() => null);
    if (graph) s.graph = abNormalize(graph);
  }
  if (status && !status.exists) s.graph = null;
}

/* The progress stream reports readings here and nowhere else, so a reading
   never reads as "the checks are running" on the rest of the project page. */
function asBuiltEvent(event) {
  if (event.phase !== 'as-built') return false;
  if (state.view === 'roles') asBuiltCrewSoon();
  if (!state.asBuilt || state.asBuilt.project !== event.project_id) return true;
  const s = state.asBuilt;
  if (s.status) {
    s.status.running = ['running', 'started'].includes(event.status)
      ? { phase: event.step || '', detail: event.detail || '' } : null;
  }
  if (event.status === 'done' || event.status === 'failed') s.graph = s.graph && { ...s.graph, commit: '' };
  return true;
}

const abPlural = (n, one, many) => `${Number(n || 0).toLocaleString()} ${n === 1 ? one : (many || `${one}s`)}`;
const abShort = (sha) => esc(String(sha || '').slice(0, 7));
const abWhen = (iso) => (iso ? new Date(iso).toLocaleDateString([], { day: 'numeric', month: 'short' }) : '');
const abGo = (kind, key) => `data-ab-go="${esc(kind)}" data-ab-key="${esc(key || '')}"`;

function asBuiltSection(p) {
  const s = abState(p.project_id);
  const st = s.status;
  if (!st) return `${abHead(null)}<p class="empty">Reading what is committed…</p>`;
  /* The views sit right under the header, always the second line whatever
     band is up; the introduction is for the overview, or before anything is
     read, not for every page of the map. */
  const body = s.graph ? abBody(s) : null;
  const intro = !body || abWhere().kind === 'overview'
    ? `<p class="tab-intro">What this codebase is, how it fits together and what it does — read from every file at
      the commit you have checked out, grouped by code, named by a model. It lives in your repository under
      <code>${esc(st.dir || AB.dir)}/</code>, so anyone can read it there without Fabrika. <a href="#" data-help-open="as-built">More about the as-built</a></p>` : '';
  return `${abHead(st)}
    ${body ? body.nav : ''}
    ${intro}
    ${abBand(st)}
    ${body ? body.page : ''}`;
}

/* The header, built the way every project tab's is -- icon, title, a muted
   status with its bar on the right, then the tab's own buttons. The reading's
   facts are one line there, the press that reads again sits where Edit sits on
   the others, and the corner icon is the same reading mode as the spec and the
   packet: F and Esc work the same, and it is the preference those keep. */
/* The project's tabs and its certification strip stay on screen above the
   as-built's own views: its pages are long, and the way to any other one
   should not scroll away. The header sticks with its title row scrolled off;
   where its tabs start and how tall what stays is are measured, not guessed,
   so a header that wraps differently still lines up. */
function abStickyChrome() {
  setTimeout(() => {
    const board = document.querySelector('header.board');
    const tabs = board && board.querySelector('.proc.steps');
    if (!board || !tabs) return;
    const b = board.getBoundingClientRect();
    const from = Math.round(tabs.getBoundingClientRect().top - b.top);
    const root = document.documentElement.style;
    root.setProperty('--ab-board-top', `${-from}px`);
    root.setProperty('--ab-board-h', `${Math.round(b.height) - from}px`);
  }, 0);
}

function abHead(st) {
  abStickyChrome();
  const running = st && st.running;
  const last = (st && st.last) || {};
  let tally = '';
  let bar = '';
  if (running) {
    tally = `reading${running.detail ? ` · ${esc(running.detail)}` : ''}`;
    bar = 'a';
  } else if (st && st.error) {
    tally = /^Stopped:/.test(st.error) ? 'paused at the usage limit' : 'the last reading failed';
    bar = 'r';
  } else if (st && st.exists) {
    tally = [`at ${abShort(st.commit)}`, `read ${esc(abWhen(st.built_at))}`, st.fresh ? 'current' : '']
      .filter(Boolean).join(' · ');
    bar = st.fresh ? 'g' : '';
  } else if (st && st.kept) {
    tally = `${abPlural(st.kept, 'file')} of ${Number(st.listed || 0).toLocaleString()} read · not committed yet`;
    bar = 'a';
  } else if (st) {
    tally = 'not read yet';
  }
  const label = !st ? '' : running ? 'Reading…' : !st.exists ? (st.kept ? 'Carry on reading' : 'Read the repository')
    : st.fresh ? 'Read again' : 'Refresh';
  const blocked = !st || running || (st.uncommitted || []).length;
  return `
    <div class="q-head">
      ${SECTION_ICON['as-built']}
      <h3>As-built</h3>
      <button type="button" class="q-help" data-help-open="as-built" title="What is this?"
        aria-label="Help: As-built">?</button>
      <span class="q-meter">
        <span class="q-tally">${tally}</span>
        ${abStalePill(st)}
        <span class="q-bars">${bar ? `<i class="${bar}"></i>` : ''}</span>
      </span>
      ${label ? `<button type="button" class="btn btn-sm q-edit ${running ? 'btn-working' : ''}" data-ab-refresh="1"
        ${blocked ? 'disabled' : ''}>${label}</button>` : ''}
      ${focusToggle()}
    </div>`;
}

/* Below the header only when there is something to say: a reading under way,
   nothing read yet, what went wrong, or which files moved. A reading that is
   current says so in the header and needs no band at all. */
function abBand(st) {
  const running = st.running;
  // A reading that stopped at the plan's limit kept what it read; it is a
  // pause, and saying "failed" sends a person to look for a fault.
  const stopped = /^Stopped:/.test(st.error || '');
  const err = st.error && !running ? `<p class="ab-err${stopped ? ' ab-paused' : ''}">${stopped
    ? `The last reading paused. ${esc(st.error.replace(/^Stopped:\s*/, ''))}` : `The last reading failed: ${esc(st.error)}`}</p>` : '';
  const dirty = (st.uncommitted || []).length
    ? `<p class="ab-err">${esc(AB.dir)}/ has changes that are not committed (${esc(st.uncommitted[0])}). Commit or
        discard them, and the as-built can be read again.</p>` : '';
  if (running) {
    return `<div class="ab-band ab-running"><span class="spin" aria-hidden="true"></span>
      <div class="grow"><b>Reading the repository — ${esc(running.phase || 'starting')}.</b>
        ${running.detail ? `${esc(running.detail)}.` : ''}
        <span class="dim small">Small files ten to a call, large ones alone and in parts, a few calls at a
          time. It runs on the server, so you can leave this page; it pauses itself before your
          plan's window is full, and the as-built appears here when it lands.</span></div>
      </div>`;
  }
  if (!st.exists && st.kept) {
    const left = Math.max(0, (st.listed || 0) - st.kept);
    return `<div class="ab-band">
      <div class="grow"><b>Partly read.</b> ${abPlural(st.kept, 'file')} of ${Number(st.listed || 0).toLocaleString()} are
        read and kept — a reading that paused, or stopped at your plan's limit, keeps what it read and commits
        nothing. Carrying on reads only the ${left.toLocaleString()} left, then commits the as-built.</div></div>${err}${dirty}`;
  }
  if (!st.exists) {
    return `<div class="ab-band">
      <div class="grow"><b>No as-built yet.</b> Fabrika reads each file once — small ones ten to a call, large ones
        alone, on the model the crew gives the reader — checks what it reports against the code, and commits the result.
        After that a refresh reads only the files that changed.</div></div>${err}${dirty}`;
  }
  // What changed since the reading, only when asked for from the header's
  // pill: a list of paths is detail, and the fact that the reading is behind
  // is what the header is for.
  const open = !st.fresh && (st.changed || []).length && state.asBuilt && state.asBuilt.showChanged;
  return `${err}${dirty}
    ${open ? `<div class="ab-changed-box"><p class="ab-cx-l">changed since this reading · ${abPlural(st.changed_count || st.changed.length, 'file')}</p>
      <ul class="ab-changed-list">${st.changed.map((c) => `<li><code>${abBreakable(c)}</code></li>`).join('')}</ul>
      ${(st.changed_count || 0) > st.changed.length ? `<p class="dim small">and ${(st.changed_count - st.changed.length).toLocaleString()} more</p>` : ''}
      <p class="dim small">Refresh reads only these, and keeps every other file's record.</p></div>` : ''}`;
}

/* How far behind the checked-out commit the reading is, where a person
   looks first -- and, pressed, which files. */
function abStalePill(st) {
  if (!st || !st.exists || st.fresh || st.running || st.error) return '';
  const n = st.changed_count || (st.changed || []).length;
  const open = state.asBuilt && state.asBuilt.showChanged;
  return `<button type="button" class="ab-stale${open ? ' on' : ''}" data-ab-stale="1" aria-expanded="${open ? 'true' : 'false'}"
    title="${open ? 'Hide' : 'Show'} the files that changed">${icon('l-circle-help')}Out of date · ${abPlural(n, 'file')} changed</button>`;
}

/* ---- where you are ------------------------------------------------------

   Every page of the as-built has an address, `&ab=` on the tab's own, so the
   browser's back steps back through it and a page can be linked to:
   `system`, `capabilities`, `sub:<key>`, `file:<path>`, `cap:<key>`, and a
   file's source as `src:<path>`, or `src:<path>@<line>` to land on a line. */

function abWhere() {
  const query = (location.hash.split('?')[1] || '');
  const raw = new URLSearchParams(query).get('ab') || 'overview';
  const at = raw.indexOf(':');
  const kind = at === -1 ? raw : raw.slice(0, at);
  let key = at === -1 ? '' : raw.slice(at + 1);
  let line = 0;
  const pin = kind === 'src' ? key.match(/@(\d+)$/) : null;
  if (pin) { line = Number(pin[1]); key = key.slice(0, pin.index); }
  return { kind: { sub: 'subsystem', file: 'file', src: 'source', cap: 'capability', capabilities: 'capabilities',
    overview: 'overview', system: 'system', architecture: 'architecture' }[kind] || 'overview', key, line };
}

function abHref(kind, key, line = 0) {
  const pid = (state.asBuilt || {}).project || state.projectId;
  const code = { system: 'system', overview: 'overview', capabilities: 'capabilities', subsystem: 'sub', file: 'file',
    source: 'src', capability: 'cap', architecture: 'architecture' }[kind];
  const ab = key ? `${code}:${key}${line ? `@${line}` : ''}` : code;
  return `#/${encodeURIComponent(pid)}?gates=as-built${ab === 'overview' ? '' : `&ab=${encodeURIComponent(ab)}`}`;
}

function abBody(s) {
  const g = s.graph;
  const at = abWhere();
  const sub = (key) => g.subsystems.find((x) => x.key === key);
  let tab = 'system';  // a subsystem or a file is a page of the map
  let strip = '';
  let view;
  let fileViews = '';
  if (at.kind === 'subsystem' && sub(at.key)) {
    const here = sub(at.key);
    strip = abPager('subsystem', g.subsystems.map((x) => [x.key, x.name, x.code]), at.key, 'subsystems', true);
    view = abSubsystem(g, here);
  } else if ((at.kind === 'file' || at.kind === 'source') && g.files.find((x) => x.path === at.key)) {
    const f = g.files.find((x) => x.path === at.key);
    const home = sub(f.subsystem);
    if (home) {
      strip = abPager(at.kind, [...home.files].sort((a, b) => a.localeCompare(b))
        .map((p) => [p, p.split('/').pop()]), f.path, home.name, false, abHref('subsystem', home.key));
    }
    view = abFile(g, f, at.kind === 'source' ? at : null);
    fileViews = abFileViews(f, at.kind === 'source' ? at : null);
  } else if (at.kind === 'capability' && g.capabilities.find((x) => x.key === at.key)) {
    const c = g.capabilities.find((x) => x.key === at.key);
    tab = 'capabilities';
    strip = abPager('capability', abCapOrder(g).map((x) => [x.key, x.name, x.code]), c.key, 'capabilities', 'square');
    view = abCapability(g, c);
  } else if (at.kind === 'capabilities') {
    tab = 'capabilities';
    view = abCapabilities(g);
  } else if (at.kind === 'architecture') {
    tab = 'architecture';
    view = abArchitecture(g);
  } else if (at.kind === 'system') {
    view = abSystem(g);
  } else {
    tab = 'overview';
    view = abOverview(g);
  }
  const tabs = [['overview', 'Overview', ''],
    ['architecture', 'Architecture', g.architecture ? abPlural(g.architecture.runtimes.length, 'process', 'processes') : ''],
    ['system', 'System', `${g.subsystems.length} subsystems`],
    ['capabilities', 'Capabilities', `${g.capabilities.length}`]];
  return {
    nav: `<div class="ab-navs"><nav class="ab-nav" aria-label="The as-built">
      <div class="ab-tabs">${tabs.map(([k, label, n]) => `<a href="${abHref(k, '')}" class="${tab === k ? 'on' : ''}"
        ${tab === k ? 'aria-current="page"' : ''}>${label}${n ? `<span>${esc(n)}</span>` : ''}</a>`).join('')}</div>
    </nav>${fileViews}</div>`,
    page: `${strip}${view}`,
  };
}

/* Where this page sits among its siblings, in a form that stays one line
   however many there are: the one before, the one after, and -- for
   subsystems -- the map's own numbers as a row of badges, yours filled, each
   naming itself on hover. A row of names does not survive a repository with
   thirty subsystems. */
function abPager(kind, items, current, within, numbered = false, up = '') {
  if (items.length < 2) return '';
  const at = items.findIndex(([key]) => key === current);
  const prev = at > 0 ? items[at - 1] : null;
  const next = at >= 0 && at < items.length - 1 ? items[at + 1] : null;
  const shape = numbered === 'square' ? 'ab-sq sm' : 'ab-num sm';
  const num = (i) => (numbered ? `<span class="${shape}">${esc(items[i][2] || '')}</span>` : '');
  const side = (item, i, dir) => (item
    ? `<a class="ab-pg-${dir}" href="${abHref(kind, item[0])}" title="${esc(item[1])}">${dir === 'prev' ? '‹' : ''}
        ${num(i)}<span>${esc(item[1])}</span>${dir === 'next' ? '›' : ''}</a>`
    : `<span class="ab-pg-${dir} off"></span>`);
  const middle = numbered
    ? `<span class="ab-pg-dots">${items.map(([key, name, code]) => `<a href="${abHref(kind, key)}"
        class="${shape}${key === current ? ' here' : ''}" title="${esc(code || '')} · ${esc(name)}"
        ${key === current ? 'aria-current="page"' : ''}>${esc(code || '')}</a>`).join('')}</span>`
    : `<span class="ab-pg-at">${at + 1} of ${items.length} in ${up ? `<a href="${up}">${esc(within)}</a>` : esc(within)}</span>`;
  return `<nav class="ab-pager" aria-label="${esc(within)}">${side(prev, at - 1, 'prev')}${middle}${side(next, at + 1, 'next')}</nav>`;
}

/* A strip of small numbers with a meter each -- the board's CHECKS and TESTS
   cells, drawn light: label over value, and under it what the value is a
   share of. Lighter than the overview's tiles, which are for the whole. */
function abPlate(cells) {
  return `<dl class="ab-plate">${cells.map(([label, value, meter, note]) => `<div class="ab-pl">
      <dt>${esc(label)}</dt><dd>${value}</dd>${meter || ''}${note ? `<span class="ab-pl-note">${note}</span>` : ''}</div>`).join('')}</dl>`;
}

function abMeter(n, of) {
  const w = of ? Math.max(n ? 3 : 0, Math.round((100 * n) / of)) : 0;
  return `<span class="ab-meter"><i style="width:${w}%"></i></span>`;
}

function abSegments(n, of) {
  if (of > 24) return abMeter(n, of);
  return `<span class="ab-segs">${Array.from({ length: of }, (_, i) => `<i class="${i < n ? 'on' : ''}"></i>`).join('')}</span>`;
}

/* ---- the system: the map, and the subsystems it numbers ------------------ */

/* The entry point. The map comes first and takes the width; every box carries
   its three letters, and the list under it carries the same ones,
   so a box and its row are found from either side. */
function abSystem(g) {
  if (!g.subsystems.length) {
    return `<p class="empty">No subsystems formed: nothing in this repository links to anything else in it.
      The <a href="${abHref('overview', '')}">overview</a> says what was read.</p>`;
  }
  return `
    <div class="card ab-map">${abMap(g)}
      <p class="ab-legend"><i class="ab-sw"></i>links between files in two subsystems, counted ·
        <i class="ab-sw dash"></i>includes links reported but not confirmed · point at a subsystem to see its links, select it to open it ·
        <a href="#" data-help-open="as-built-system">How to read this</a></p></div>
    ${abSubsList(g, 'product', 'Product', 'what the capabilities reach')}
    ${abSubsList(g, 'support', 'Support', 'build, configuration and environment — nothing a person does reaches these')}
    ${g.subsystems.some((x) => x.role) ? '' : abSubsList(g, '', 'Subsystems', '')}
    ${g.hubs.length ? `<p class="dim small">Shared by nearly everything, and not drawn: ${g.hubs.map((h) =>
      `<a class="ab-link mono" href="${abHref('file', h)}">${esc(h)}</a>`).join(', ')}.</p>` : ''}`;
}

function abSubsList(g, role, title, note) {
  const rows = g.subsystems.map((x, i) => [x, i]).filter(([x]) => (x.role || '') === role);
  if (!rows.length) return '';
  return `<h4 class="ab-h">${esc(title)} · ${rows.length}${note ? ` <span class="ab-h-note">${esc(note)}</span>` : ''}</h4>
    <ol class="ab-subs${role === 'support' ? ' ab-subs-support' : ''}">${rows.map(([x, i]) => `<li><a href="${abHref('subsystem', x.key)}">
        <span class="ab-num">${esc(x.code)}</span>
        <span class="ab-subs-name">${esc(x.name)}</span>
        <span class="ab-subs-size mono">${abPlural(x.files.length, 'file')} · ${x.lines.toLocaleString()} lines</span>
        <span class="ab-subs-what">${esc(x.summary)}</span></a></li>`).join('')}</ol>`;
}

/* ---- the overview: what this is, in numbers and in words -----------------

   Drawn the way the project's own overview is: the summary as its lead, the
   numbers as tiles with a lamp -- the same tiles, so a count here looks like a
   count everywhere else -- then headed sections. A tile that leads somewhere
   is a link to it. */

const AB_FINDING_ICON = {
  concentration: 'layers', mutual: 'arrow-left-right', launched: 'play', no_caller: 'unplug',
  unreached: 'ghost', untested: 'flask', seams: 'scissors', unconfirmed: 'circle-help',
};

function abOverview(g) {
  const code = g.files.filter((f) => !f.is_test && (f.kind || 'code') === 'code');
  const lines = code.reduce((a, f) => a + f.lines, 0);
  const tested = code.filter((f) => (f.tested_by || []).length);
  /* The same measure as the finding under "worth knowing", so the tile and
     the sentence never give two numbers for one fact: a file of 50 lines or
     more that no test imports or calls counts against the code's share. */
  const written = code.filter((f) => !f.generated);  // what a tool made is not what a test must reach
  const writtenLines = written.reduce((a, f) => a + f.lines, 0);
  const untestedLines = written.filter((f) => !(f.tested_by || []).length && f.lines >= 50).reduce((a, f) => a + f.lines, 0);
  const testedShare = writtenLines ? 100 - Math.round((100 * untestedLines) / writtenLines) : 0;
  const tests = g.files.filter((f) => f.is_test).length;
  const cov = g.coverage || {};
  const placed = (cov.placed_graph || 0) + (cov.placed_link || 0);
  const ways = g.ways_in.length;
  const waysTested = g.ways_in.filter((w) => (w.tested_by || []).length).length;
  const unconfirmed = cov.edges_unconfirmed || 0;
  const unread = (cov.unread || []).length;
  const tiles = [
    overviewTile('Subsystems', `${abPlural(g.subsystems.length, 'subsystem')}`,
      g.subsystems.some((x) => x.role)
        ? `${g.subsystems.filter((x) => x.role === 'product').length} product · ${g.subsystems.filter((x) => x.role === 'support').length} support · ${cov.hubs || 0} shared files`
        : `${abPlural(placed, 'file')} placed · ${cov.hubs || 0} shared`, 'lit', abHref('system', '')),
    overviewTile('Capabilities', `${abPlural(g.capabilities.length, 'capability', 'capabilities')}`,
      `${abPlural(ways, 'way in', 'ways in')} · ${waysTested} reached by a test`, 'lit', abHref('capabilities', '')),
    overviewTile('Code', `${lines.toLocaleString()} lines`,
      `${abPlural(code.length, 'file')}${code.filter((f) => f.generated).length ? `, ${code.filter((f) => f.generated).length} generated` : ''} · ${cov.aside || 0} documents and configuration`, 'lit'),
    overviewTile('Tests', tests ? `${testedShare}% of the code reached` : 'No tests',
      `${abPlural(tests, 'test file')} · ${abPlural(tested.length, 'file')} reached`, tests && testedShare >= 50 ? 'lit' : 'warn'),
    overviewTile('Links', `${(cov.edges_confirmed || 0).toLocaleString()} confirmed`,
      unconfirmed ? `${abPlural(unconfirmed, 'link')} reported and not confirmed` : 'every link confirmed by code',
      unconfirmed > (cov.edges_confirmed || 0) / 10 ? 'warn' : 'lit'),
    overviewTile('Coverage', `${(cov.files_read || 0).toLocaleString()} of ${(cov.files_listed || 0).toLocaleString()} files read`,
      [unread ? `${unread} could not be read` : '', (cov.skipped || []).length ? `${cov.skipped.length} skipped` : '']
        .filter(Boolean).join(' · ') || 'everything read', unread ? 'warn' : 'lit'),
  ];
  const file = (path) => `<a class="ab-link mono" href="${abHref('file', path)}">${esc(path)}</a>`;
  const finding = (f) => {
    const pct = /(\d+)% of the code/.exec(f.title);
    return `<li class="ab-find">
      <span class="ab-find-ic">${icon(`l-${AB_FINDING_ICON[f.kind] || 'info'}`)}</span>
      <div><b>${esc(f.title)}</b>
        ${pct ? `<span class="ab-share"><i style="width:${Math.min(100, Number(pct[1]))}%"></i></span>` : ''}
        <p>${esc(f.detail)}</p></div></li>`;
  };
  return `
    ${g.summary ? `<p class="db-lead ab-lead">${esc(g.summary)}</p>` : ''}
    <div class="db-tiles">${tiles.join('')}</div>
    <div class="ab-ov">
      <section class="db-sec">
        <h3 class="db-h">${icon('l-lightbulb')} Worth knowing <span class="db-count">${(g.findings || []).length}</span></h3>
        ${(g.findings || []).length ? `<ul class="ab-finds">${g.findings.map(finding).join('')}</ul>`
          : '<p class="db-empty">Nothing stood out.</p>'}
      </section>
      <div>
        ${(g.start_reading || []).length ? `<section class="db-sec">
          <h3 class="db-h">${icon('l-book-open')} Where to start reading <span class="db-count">${g.start_reading.length}</span></h3>
          <ol class="ab-start">${g.start_reading.map((r, i) => `<li><span class="ab-ord">${i + 1}</span>
            <div>${file(r.path)}<p>${esc(r.why)}</p></div></li>`).join('')}</ol></section>` : ''}
        ${g.hubs.length ? `<section class="db-sec">
          <h3 class="db-h">${icon('l-share')} Shared by nearly everything <span class="db-count">${g.hubs.length}</span></h3>
          <ul class="ab-start">${g.hubs.map((h) => {
            const n = g.edges.filter((e) => e.target === h && e.kind === 'import').length;
            return `<li><span class="ab-hub-n mono">${n}</span><div>${file(h)}<p>${esc((g.files.find((f) => f.path === h) || {}).purpose || '')}</p></div></li>`;
          }).join('')}</ul></section>` : ''}
        <section class="db-sec">
          <h3 class="db-h">${icon('l-eye')} What was read</h3>
          <ul class="ab-list dim small">
            ${(cov.skipped || []).length ? `<li>${cov.skipped.length} not read — dependencies, build output, binaries.</li>` : ''}
            ${unread ? `<li>Could not be read: ${cov.unread.slice(0, 6).map((u) => `<code>${esc(u.path)}</code>`).join(', ')}.</li>` : ''}
            ${(cov.unplaced || []).length ? `<li>Not placed in a subsystem: ${cov.unplaced.slice(0, 6).map((u) => `<code>${esc(u)}</code>`).join(', ')}.</li>` : ''}
            <li>${abPlural(cov.functions_confirmed || 0, 'function')} found at the line the reader gave, ${cov.functions_unconfirmed || 0} not.</li>
            <li>Every number and link is computed from the repository; names and summaries are written by a model
              from what each file contains. The same reading is in <code>${esc(AB.dir)}/README.md</code>.</li>
          </ul>
        </section>
      </div>
    </div>`;
}

/* ---- the capabilities ---------------------------------------------------- */

/* Capabilities, in the order they are shown everywhere -- grouped by area --
   so the grid and the list read in the same order. Each carries its three letters. */
function abCapOrder(g) {
  const areas = [];
  const by = {};
  g.capabilities.forEach((c) => {
    const a = c.area || 'Other';
    if (!by[a]) { by[a] = []; areas.push(a); }
    by[a].push(c);
  });
  return areas.flatMap((a) => by[a]);
}

/* The grounding picture: what each capability reaches in the system. Rows are
   capabilities, columns are the map's own numbered subsystems, and a cell says
   how the capability touches that subsystem -- a solid square where its
   handlers run code there, an outline where that work only sets something in
   motion -- darker for more lines. Everything in it is a link. */
function abCapGrid(g, order) {
  const subs = g.subsystems;
  const reach = (c) => {
    const acc = new Map();
    const add = (items, kind) => items.forEach((r) => {
      const key = r.subsystem || '(shared)';
      const cell = acc.get(key) || { direct: 0, motion: 0 };
      cell[kind] += r.lines;
      acc.set(key, cell);
    });
    add(c.direct, 'direct');
    add(c.in_motion, 'motion');
    return acc;
  };
  const cells = order.map(reach);
  const shared = cells.some((m) => m.has('(shared)'));
  const cols = [...subs.map((x) => ({ key: x.key, label: x.code, name: x.name, href: abHref('subsystem', x.key), role: x.role }))
      .filter((col) => col.role !== 'support'),
    ...(shared ? [{ key: '(shared)', label: '·', name: 'Shared by nearly everything', href: '' }] : [])];
  const most = Math.max(1, ...cells.flatMap((m) => [...m.values()].map((v) => v.direct + v.motion)));
  const tone = (n) => Math.max(0.28, Math.min(1, Math.sqrt(n / most)));
  const rows = order.map((c, i) => {
    const m = cells[i];
    const tds = cols.map((col) => {
      const v = m.get(col.key);
      if (!v) return '<td class="ab-cell"></td>';
      const kind = v.direct ? 'direct' : 'motion';
      const say = `${c.name} → ${col.name}: ${v.direct ? `runs ${v.direct.toLocaleString()} lines there` : ''}${
        v.direct && v.motion ? ', ' : ''}${v.motion ? `sets ${v.motion.toLocaleString()} in motion` : ''}`;
      return `<td class="ab-cell"><a href="${abHref('capability', c.key)}" title="${esc(say)}" aria-label="${esc(say)}">
        <i class="${kind}" style="opacity:${tone(v.direct + v.motion).toFixed(2)}"></i></a></td>`;
    }).join('');
    return `<tr>
      <th class="ab-grid-name"><a href="${abHref('capability', c.key)}"><span class="ab-sq">${esc(c.code)}</span><span>${esc(c.name)}</span></a></th>
      <td class="ab-grid-area-c">${esc(c.area || 'Other')}</td>
      ${tds}
      <td class="ab-grid-ways mono">${c.ways_in.length}</td>
      <td class="ab-grid-test">${abSegments(c.tested, c.ways_in.length)}</td></tr>`;
  }).join('');
  return `<div class="ab-scroll"><table class="ab-grid">
    <thead><tr><th class="ab-grid-name"><span class="ab-grid-l">capability</span></th><th class="ab-grid-l">area</th>
      ${cols.map((col) => `<th class="ab-grid-col">${col.href
        ? `<a href="${col.href}" class="ab-num sm" title="${esc(col.label)} · ${esc(col.name)}">${esc(col.label)}</a>`
        : `<span class="ab-num sm ab-shared" title="${esc(col.name)}">·</span>`}</th>`).join('')}
      <th class="ab-grid-l num">ways in</th><th class="ab-grid-l">tested</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function abCapabilities(g) {
  if (!g.capabilities.length) {
    return '<p class="empty">No ways in were found — no routes, commands, jobs or screens — so there is nothing to group.</p>';
  }
  const order = abCapOrder(g);
  const ways = g.ways_in.length;
  const tested = g.capabilities.reduce((a, c) => a + c.tested, 0);
  return `
    <p class="ab-summary">What the code does: ${abPlural(ways, 'way in', 'ways in')} — routes, commands, jobs and screens —
      grouped into what a person can do with it. ${tested} ${tested === 1 ? 'is' : 'are'} reached by a test.
      <a href="#" data-help-open="as-built-capabilities">How to read this</a></p>
    <div class="card ab-map">${abCapGrid(g, order)}
      <p class="ab-legend"><i class="ab-key direct"></i>its handlers run code there · <i class="ab-key motion"></i>only sets work
        in motion there · darker is more lines · IDs are the system map's</p>
      ${(() => {
        const sup = g.subsystems.map((x, i) => [x, i]).filter(([x]) => x.role === 'support');
        return sup.length ? `<p class="ab-legend">Not drawn: ${abPlural(sup.length, 'support subsystem')}, which no capability reaches —
          ${sup.map(([x]) => `<a class="ab-link" href="${abHref('subsystem', x.key)}"><span class="ab-num sm">${esc(x.code)}</span> ${esc(x.name)}</a>`).join(' · ')}</p>` : '';
      })()}</div>
    <h4 class="ab-h">Capabilities</h4>
    <ol class="ab-subs ab-caps">${order.map((c, i) => `<li><a href="${abHref('capability', c.key)}">
        <span class="ab-sq">${esc(c.code)}</span>
        <span class="ab-subs-name">${esc(c.name)}<span class="ab-cap-area">${esc(c.area || 'Other')}</span></span>
        <span class="ab-subs-size mono">${abPlural(c.ways_in.length, 'way in', 'ways in')} · ${c.tested} tested</span>
        <span class="ab-subs-what">${esc(c.summary)}</span></a></li>`).join('')}</ol>
    ${(g.not_capabilities || []).length ? `<p class="dim small">Not a capability: ${g.not_capabilities.map((w) => `<code>${esc(w)}</code>`).join(' · ')}</p>` : ''}`;
}

/* A layered drawing of the subsystems, left to right: what depends on
   something sits to its left. Cycles are broken where a depth-first walk meets
   them; those links are still drawn, routed underneath and coming back. */
function abLayout(nodes, edges, size) {
  const out = new Map(nodes.map((n) => [n, []]));
  edges.forEach((e) => { if (out.has(e.a) && out.has(e.b)) out.get(e.a).push(e.b); });
  const seen = new Map();
  const back = new Set();
  const order = [];
  const visit = (n) => {
    seen.set(n, 1);
    out.get(n).forEach((m) => {
      if (seen.get(m) === 1) back.add(`${n}>${m}`);
      else if (!seen.get(m)) visit(m);
    });
    seen.set(n, 2);
    order.push(n);
  };
  nodes.forEach((n) => { if (!seen.get(n)) visit(n); });
  order.reverse();
  const rank = new Map(nodes.map((n) => [n, 0]));
  order.forEach((n) => out.get(n).forEach((m) => {
    if (!back.has(`${n}>${m}`)) rank.set(m, Math.max(rank.get(m), rank.get(n) + 1));
  }));
  const cols = [];
  nodes.forEach((n) => { (cols[rank.get(n)] = cols[rank.get(n)] || []).push(n); });
  const pos = new Map();
  const { w, h, gx, gy } = size;
  cols.forEach((col, c) => {
    if (c > 0) {
      const bary = (n) => {
        const ins = edges.filter((e) => e.b === n && pos.has(e.a)).map((e) => pos.get(e.a).y);
        return ins.length ? ins.reduce((a, b) => a + b, 0) / ins.length : 1e9;
      };
      col.sort((a, b) => bary(a) - bary(b));
    }
    col.forEach((n, i) => pos.set(n, { x: 12 + c * (w + gx), y: 12 + i * (h + gy) }));
  });
  const width = cols.length * (w + gx) - gx + 24;
  const tall = Math.max(...cols.map((col) => col.length)) * (h + gy) - gy + 24;
  return { pos, width, height: tall };
}

/* A link forward goes right, side to side. A link back -- the other half of
   a cycle -- drops below both boxes and comes back, one lane per link, so two
   of them never draw on top of each other. */
function abEdgePath(a, b, size, lane, floor) {
  const { w, h } = size;
  if (b.x > a.x) {
    const x1 = a.x + w; const y1 = a.y + h / 2 - 6; const x2 = b.x - 4; const y2 = b.y + h / 2 - 6;
    const mx = (x1 + x2) / 2;
    return { d: `M${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`, mx, my: (y1 + y2) / 2 };
  }
  const x1 = a.x + w / 2 + 14; const x2 = b.x + w / 2 - 14;
  const y1 = a.y + h; const y2 = b.y + h + 4;
  const low = floor + 18 + lane * 22;
  /* The count sits on the curve, at its middle: a cubic whose two handles are
     both at `low` reaches (y1 + 6·low + y2) / 8 there, not `low` itself. */
  return { d: `M${x1} ${y1} C ${x1} ${low}, ${x2} ${low}, ${x2} ${y2}`, mx: (x1 + x2) / 2, my: (y1 + 6 * low + y2) / 8 };
}

/* A name on two lines rather than cut off: SVG text does not wrap, and a
   subsystem called "Account settings and notifica…" is not named at all. */
function abWrap(text, width) {
  if (text.length <= width) return [text, ''];
  const words = text.split(' ');
  let one = '';
  while (words.length && (one + (one ? ' ' : '') + words[0]).length <= width) one += (one ? ' ' : '') + words.shift();
  if (!one) one = words.shift();
  let two = words.join(' ');
  if (two.length > width) two = `${two.slice(0, width - 1)}…`;
  return [one, two];
}

/* Two subsystems that use each other are one link, not two: drawn once, with
   the total, pointing the way most of the links go. `back` is the count going
   the other way. */
function abMerge(edges) {
  const pairs = new Map();
  edges.forEach((e) => {
    const [x, y] = e.a < e.b ? [e.a, e.b] : [e.b, e.a];
    const k = `${x}\u0000${y}`;
    const r = pairs.get(k) || { x, y, xy: 0, yx: 0, shaky: false };
    if (e.a === x) r.xy += e.n; else r.yx += e.n;
    r.shaky = r.shaky || e.shaky;
    pairs.set(k, r);
  });
  return [...pairs.values()].map((r) => (r.xy >= r.yx
    ? { a: r.x, b: r.y, n: r.xy + r.yx, back: r.yx, shaky: r.shaky }
    : { a: r.y, b: r.x, n: r.xy + r.yx, back: r.xy, shaky: r.shaky }));
}

function abMapDense(g, merged, support) {
  const size = { w: 266, h: 78, gx: 70, gy: 70 };
  const { w, h, gx } = size;
  const product = g.subsystems.map((x) => x.key).filter((k) => !support.has(k));
  const inner = merged.filter((e) => !support.has(e.a) && !support.has(e.b));
  /* Dependency order from the layered layout, then poured into rows of four. */
  const ranked = abLayout(product, inner, size).pos;
  const cols = 4;
  const pos = new Map();
  [...product].sort((a, b) => ranked.get(a).x - ranked.get(b).x)
    .forEach((k, i) => pos.set(k, { x: 12 + (i % cols) * (w + gx), y: 12 + Math.floor(i / cols) * (h + size.gy) }));
  const w0 = Math.min(cols, product.length) * (w + gx) - gx + 24;
  const tall = Math.ceil(product.length / cols) * (h + size.gy) - size.gy + 24;
  const bandY = tall + 60;
  [...support].forEach((k, i) => pos.set(k, { x: 12 + i * (w + 24), y: bandY }));
  const width = Math.max(w0, 12 + support.size * (w + 24));
  const height = support.size ? bandY + h + 14 : tall;
  const path = (pa, pb) => {
    if (Math.abs(pa.y - pb.y) < 1) {
      const y = pa.y + h / 2;
      const left = pa.x < pb.x;
      if (Math.abs(pa.x - pb.x) <= w + gx + 1) {
        const x1 = left ? pa.x + w : pa.x - 4; const x2 = left ? pb.x - 4 : pb.x + w;
        return { d: `M${x1} ${y} L ${x2} ${y}`, mx: (x1 + x2) / 2, my: y };
      }
      const x1 = pa.x + w / 2; const x2 = pb.x + w / 2; const top = pa.y - 4;
      const low = pa.y - 46;
      return { d: `M${x1} ${top} C ${x1} ${low}, ${x2} ${low}, ${x2} ${top - 0}`, mx: (x1 + x2) / 2, my: (top + 6 * low + top) / 8 };
    }
    const down = pb.y > pa.y;
    const x1 = pa.x + w / 2; const y1 = down ? pa.y + h : pa.y - 4;
    const x2 = pb.x + w / 2; const y2 = down ? pb.y - 4 : pb.y + h + 4;
    const my = (y1 + y2) / 2;
    return { d: `M${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`, mx: (x1 + x2) / 2, my };
  };
  const lines = merged.map((e) => {
    const p = path(pos.get(e.a), pos.get(e.b));
    const both = e.back > 0;
    return `<g class="ab-link-g${e.n >= 10 ? ' big' : ''}" data-a="${esc(e.a)}" data-b="${esc(e.b)}">
      <title>${e.n} link${e.n === 1 ? '' : 's'}${both ? ` (${e.n - e.back} one way, ${e.back} the other)` : ''}</title>
      <path d="${p.d}" class="ab-edge${e.shaky ? ' dash' : ''}" stroke-width="${Math.min(4, 1 + e.n / 6).toFixed(1)}"
        marker-end="url(#ab-arrow-d)"${both ? ' marker-start="url(#ab-arrow-d)"' : ''}></path>
      <g class="ab-count"><rect x="${p.mx - 12}" y="${p.my - 9}" width="24" height="17" rx="2"></rect>
        <text x="${p.mx}" y="${p.my + 4}" text-anchor="middle">${e.n}</text></g></g>`;
  }).join('');
  const boxes = g.subsystems.map((s) => {
    const { x, y } = pos.get(s.key);
    const [one, two] = abWrap(s.name, 27);
    const tx = x + 56;
    return `<g class="ab-node${support.has(s.key) ? ' ab-support' : ''}" data-node="${esc(s.key)}" ${abGo('subsystem', s.key)} tabindex="0" role="button" aria-label="${esc(s.code)} ${esc(s.name)}">
      <title>${esc(s.code)} · ${esc(s.name)}</title>
      <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="2"></rect>
      <rect x="${x + 10}" y="${y + 11}" width="38" height="20" rx="10" class="ab-badge"></rect>
      <text x="${x + 29}" y="${y + 25}" text-anchor="middle" class="ab-badge-n">${esc(s.code)}</text>
      <text x="${tx}" y="${y + 22}" class="ab-nt">${esc(one)}</text>
      ${two ? `<text x="${tx}" y="${y + 40}" class="ab-nt">${esc(two)}</text>` : ''}
      <text x="${tx}" y="${y + (two ? 62 : 46)}" class="ab-ns">${abPlural(s.files.length, 'file')} · ${s.lines.toLocaleString()} lines</text></g>`;
  }).join('');
  return `<div class="ab-scroll"><svg class="ab-fmap dense" viewBox="0 0 ${width} ${height}" style="width:100%;max-width:${width}px;height:auto"
      role="img" aria-label="The subsystems and the links between them">
    <defs><marker id="ab-arrow-d" viewBox="0 0 8 8" refX="7.5" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M0 0 L8 4 L0 8 z" class="ab-head"></path></marker></defs>
    ${support.size ? `<line x1="12" y1="${bandY - 20}" x2="${width - 12}" y2="${bandY - 20}" class="ab-band-rule"></line>
      <text x="12" y="${bandY - 26}" class="ab-band-l">SUPPORT — NOTHING A PERSON DOES REACHES THESE</text>` : ''}
    ${lines}${boxes}</svg></div>`;
}

function abMap(g) {
  const files = new Map(g.files.map((f) => [f.path, f]));
  const counts = new Map();
  const shaky = new Set();
  g.edges.forEach((e) => {
    const a = (files.get(e.source) || {}).subsystem; const b = (files.get(e.target) || {}).subsystem;
    if (!a || !b || a === b) return;
    const k = `${a}\u0000${b}`;
    counts.set(k, (counts.get(k) || 0) + 1);
    if (!e.confirmed) shaky.add(k);
  });
  const edges = [...counts].map(([k, n]) => { const [a, b] = k.split('\u0000'); return { a, b, n, shaky: shaky.has(k) }; });
  /* Many links between few boxes, most of them running both ways: the layered
     drawing below turns that into one long row and a tangle of arcs underneath.
     A grid, one line for each pair, and the links of whatever is under the
     pointer brought forward, reads where that cannot. */
  const merged = abMerge(edges);
  const supportKeys = new Set(g.subsystems.filter((x) => x.role === 'support').map((x) => x.key));
  if (merged.filter((e) => !supportKeys.has(e.a) && !supportKeys.has(e.b)).length > 12) {
    return abMapDense(g, merged, supportKeys);
  }
  const size = { w: 266, h: 78, gx: 66, gy: 30 };
  /* The product subsystems are laid out by what depends on what; the support
     ones -- nothing a person does reaches them -- sit in a band beneath, drawn
     quieter, so the map's weight is on the part of the system that does the
     work. */
  const support = new Set(g.subsystems.filter((s) => s.role === 'support').map((s) => s.key));
  const product = g.subsystems.map((s) => s.key).filter((k) => !support.has(k));
  const inner = edges.filter((e) => !support.has(e.a) && !support.has(e.b));
  const { pos, width: w0, height: tall } = abLayout(product.length ? product : [...support], product.length ? inner : edges, size);
  const backs = inner.filter((e) => pos.has(e.a) && pos.has(e.b) && pos.get(e.b).x <= pos.get(e.a).x);
  const floor = tall - 12;
  const lanes = backs.length ? 30 + backs.length * 22 : 0;
  const bandY = tall + lanes + 34;
  let width = w0;
  if (product.length && support.size) {
    [...support].forEach((k, i) => pos.set(k, { x: 12 + i * (size.w + 24), y: bandY }));
    width = Math.max(w0, 12 + support.size * (size.w + 24));
  }
  const height = product.length && support.size ? bandY + size.h + 14 : tall + lanes;
  const lines = edges.map((e) => {
    const pa = pos.get(e.a); const pb = pos.get(e.b);
    const crosses = support.has(e.a) !== support.has(e.b) && product.length;
    const p = crosses
      ? (() => {
        const up = pb.y < pa.y;
        const x1 = pa.x + size.w / 2; const y1 = up ? pa.y : pa.y + size.h;
        const x2 = pb.x + size.w / 2; const y2 = up ? pb.y + size.h + 4 : pb.y - 4;
        const my = (y1 + y2) / 2;
        return { d: `M${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`, mx: (x1 + x2) / 2, my };
      })()
      : abEdgePath(pa, pb, size, backs.indexOf(e), floor);
    return `<path d="${p.d}" class="ab-edge${e.shaky ? ' dash' : ''}" stroke-width="${Math.min(4, 1 + e.n / 6).toFixed(1)}"
        marker-end="url(#ab-arrow)"></path>
      <g class="ab-count"><rect x="${p.mx - 12}" y="${p.my - 9}" width="24" height="17" rx="2"></rect>
        <text x="${p.mx}" y="${p.my + 4}" text-anchor="middle">${e.n}</text></g>`;
  }).join('');
  const boxes = g.subsystems.map((s, i) => {
    const { x, y } = pos.get(s.key);
    const [one, two] = abWrap(s.name, 27);
    const tx = x + 56;
    return `<g class="ab-node${support.has(s.key) ? ' ab-support' : ''}" ${abGo('subsystem', s.key)} tabindex="0" role="button" aria-label="${esc(s.code)} ${esc(s.name)}">
      <title>${esc(s.code)} · ${esc(s.name)}</title>
      <rect x="${x}" y="${y}" width="${size.w}" height="${size.h}" rx="2"></rect>
      <rect x="${x + 10}" y="${y + 11}" width="38" height="20" rx="10" class="ab-badge"></rect>
      <text x="${x + 29}" y="${y + 25}" text-anchor="middle" class="ab-badge-n">${esc(s.code)}</text>
      <text x="${tx}" y="${y + 22}" class="ab-nt">${esc(one)}</text>
      ${two ? `<text x="${tx}" y="${y + 40}" class="ab-nt">${esc(two)}</text>` : ''}
      <text x="${tx}" y="${y + (two ? 62 : 46)}" class="ab-ns">${abPlural(s.files.length, 'file')} · ${s.lines.toLocaleString()} lines</text></g>`;
  }).join('');
  return `<div class="ab-scroll"><svg viewBox="0 0 ${width} ${height}" style="width:100%;max-width:${width}px;height:auto"
      role="img" aria-label="The subsystems and the links between them">
    <defs><marker id="ab-arrow" viewBox="0 0 8 8" refX="7.5" refY="4" markerWidth="6" markerHeight="6" orient="auto">
      <path d="M0 0 L8 4 L0 8 z" class="ab-head"></path></marker></defs>
    ${product.length && support.size ? `<line x1="12" y1="${bandY - 20}" x2="${width - 12}" y2="${bandY - 20}" class="ab-band-rule"></line>
      <text x="12" y="${bandY - 26}" class="ab-band-l">SUPPORT — NOTHING A PERSON DOES REACHES THESE</text>` : ''}
    ${lines}${boxes}</svg></div>`;
}

/* ---- one subsystem ------------------------------------------------------- */

function abSubsystem(g, s) {
  const folderModel = abFolderModel(g, s);
  const files = new Map(g.files.map((f) => [f.path, f]));
  const importers = (p) => g.edges.filter((e) => e.target === p && e.kind === 'import').length;
  const index = g.subsystems.findIndex((x) => x.key === s.key);
  const name = new Map(g.subsystems.map((x) => [x.key, [x.code, x.name]]));
  const tally = (dir) => {
    const acc = new Map();
    g.edges.forEach((e) => {
      const a = (files.get(e.source) || {}).subsystem; const b = (files.get(e.target) || {}).subsystem;
      if (!a || !b || a === b) return;
      if (dir === 'out' && a === s.key) acc.set(b, (acc.get(b) || 0) + 1);
      if (dir === 'in' && b === s.key) acc.set(a, (acc.get(a) || 0) + 1);
    });
    return [...acc].sort((x, y) => y[1] - x[1]);
  };
  const uses = tally('out');
  const usedBy = tally('in');
  const through = g.capabilities.filter((c) => [...c.direct, ...c.in_motion].some((r) => r.subsystem === s.key));
  const outside = g.architecture
    ? g.architecture.outside.filter((o) => !o.tooling && o.subsystems.includes(s.key)).map((o) => o.name)
    : [...new Set(s.files.flatMap((p) => (files.get(p) || {}).outside || []))];
  const tested = s.files.filter((p) => ((files.get(p) || {}).tested_by || []).length).length;
  const codeLines = g.files.filter((f) => !f.is_test && (f.kind || 'code') === 'code').reduce((a, f) => a + f.lines, 0) || 1;
  const share = (100 * s.lines) / codeLines;
  const ordered = [...s.files].sort((a, b) => (files.get(a).generated ? 1 : 0) - (files.get(b).generated ? 1 : 0)
    || files.get(b).lines - files.get(a).lines);  // what someone wrote first, then what a tool made
  const biggest = files.get(ordered[0]).lines || 1;

  const plate = abPlate([
    ['files', `${s.files.length}`, abMeter(s.lines, codeLines),
      `${s.lines.toLocaleString()} lines · ${share < 1 ? '<1' : Math.round(share)}% of the code`],
    ['tested', `${tested} of ${s.files.length}`, abSegments(tested, s.files.length), 'files a test reaches'],
    ['links', `${usedBy.reduce((a, [, n]) => a + n, 0)} in · ${uses.reduce((a, [, n]) => a + n, 0)} out`, '',
      `from ${abPlural(usedBy.length, 'subsystem')}, to ${abPlural(uses.length, 'subsystem')}`],
    ['capabilities', `${through.length}`, '', through.length ? 'pass through it' : 'none reach it'],
    ['outside', `${outside.length}`, '', outside.length ? 'systems it talks to' : 'talks to nothing outside'],
  ]);

  const box = (key, n) => {
    const [num, label] = name.get(key) || ['', key];
    return `<a class="ab-cx-box" href="${abHref('subsystem', key)}"><span class="ab-num sm">${num}</span>
      <span>${esc(label)}</span><span class="ab-cx-n mono">${abPlural(n, 'link')}</span></a>`;
  };
  const connects = `
    <div class="ab-cx">
      <div class="ab-cx-col"><p class="ab-cx-l">used by</p>
        ${usedBy.length ? usedBy.map(([k, n]) => box(k, n)).join('') : '<p class="dim small">Nothing depends on it.</p>'}</div>
      <div class="ab-cx-arrow" aria-hidden="true">→</div>
      <div class="ab-cx-here"><span class="ab-num">${esc(s.code)}</span><b>${esc(s.name)}</b></div>
      <div class="ab-cx-arrow" aria-hidden="true">→</div>
      <div class="ab-cx-col"><p class="ab-cx-l">uses</p>
        ${uses.length ? uses.map(([k, n]) => box(k, n)).join('') : '<p class="dim small">It depends on nothing else here.</p>'}</div>
    </div>`;

  const fileRow = (p) => {
    const f = files.get(p);
    const cut = p.lastIndexOf('/');
    const dir = cut === -1 ? '' : p.slice(0, cut + 1);
    const leaf = p.slice(cut + 1);
    return `<tr>
      <td class="ab-file-cell"><a class="ab-fname" href="${abHref('file', p)}"><span class="dir">${abBreakable(dir)}</span><b>${esc(leaf)}</b></a>${
        (f.parts || []).length ? ` <span class="pill">${f.parts.length} parts</span>` : ''}${
        f.generated ? ' <span class="pill ab-gen" title="made by a tool: read for the picture, not for review">generated</span>' : ''}
        <span class="ab-file-size mono">${abPlural(f.lines, 'line')}<span class="ab-meter thin"><i style="width:${Math.round((100 * f.lines) / biggest)}%"></i></span></span></td>
      <td>${esc(f.purpose)}</td>
      <td class="num">${importers(p)}</td>
      <td class="num">${(f.tested_by || []).length ? '<span class="ab-yes" title="a test reaches it">●</span>' : '<span class="dim">—</span>'}</td></tr>`;
  };

  return `
    <div class="ab-subhead"><span class="ab-num">${esc(s.code)}</span>
      <div><h3 class="ab-title">${esc(s.name)}${s.role === 'support' ? ' <span class="pill">support</span>' : ''}</h3>
        <p class="ab-summary">${esc(s.summary)}</p></div></div>
    ${plate}
    <section class="db-sec">
      <h3 class="db-h">${icon('l-arrow-left-right')} How it connects</h3>
      ${connects}
    </section>
    <section class="db-sec">
      <h3 class="db-h">${icon('l-share')} Inside it <span class="db-count">${s.files.length} files${folderModel
        ? ` in ${abPlural([...folderModel.groups.keys()].filter((k) => !k.startsWith('entry:')).length, 'folder')}` : ''}</span></h3>
      ${folderModel ? `<div class="card ab-map">${abFolderMap(g, s, folderModel)}
        <p class="ab-legend"><i class="ab-sw"></i>files in one box import files in the other, the number how many ·
          <i class="ab-sw dash"></i>reported, not confirmed · select a folder to open it · point at a file for its own links,
          select it to open its neighbours' folders</p></div>`
      : `<div class="card ab-map">${abFileMap(g, s)}
        <p class="ab-legend"><i class="ab-sw"></i>a file uses the one it points to · <i class="ab-sw dash"></i>reported, not
          confirmed · boxes with an ID are other subsystems · select a file to open it</p></div>`}
    </section>
    <section class="db-sec">
      <h3 class="db-h">${icon('l-file-text')} Files <span class="db-count">${s.files.length}</span></h3>
      <table class="ab-table ab-files"><thead><tr><th class="ab-file-cell">file</th><th>what it is for</th>
        <th class="num ab-narrow">imported by</th><th class="num ab-narrow">tested</th></tr></thead>
        <tbody>${ordered.map(fileRow).join('')}</tbody></table>
    </section>
    ${abWhereItRuns(g, s)}
    <section class="db-sec">
      <h3 class="db-h">${icon('l-zap')} Capabilities that pass through <span class="db-count">${through.length}</span></h3>
      ${through.length ? `<ul class="ab-list">${through.map((c) => `<li><a class="ab-link" href="${abHref('capability', c.key)}"><span class="ab-sq sm">${esc(c.code)}</span> ${
        esc(c.name)}</a><span>${esc(c.summary)}</span></li>`).join('')}</ul>`
        : '<p class="db-empty">No way in reaches it — configuration, setup, or code nothing calls.</p>'}
    </section>`;
}

/* ---- inside one subsystem: its files, and how they link ------------------

   The same drawing as the system map, one level down: every file of the
   subsystem as a box, laid out by what depends on what, and every link between
   them -- dashed where code could not confirm it. The subsystems it reaches
   outside are drawn too, as numbered boxes where the links leave, so a
   dependency on another subsystem is pinned to the file that carries it. */
/* Top to bottom, a few boxes to a row: a subsystem's files chain far deeper
   than its subsystems do, and laid out left to right the same way the map is,
   eleven ranks shrink the text to nothing. Ranks are the same -- what uses a
   file sits above it -- and a rank wider than a row wraps onto the next. */
function abLayoutRows(nodes, edges, size, cols) {
  const out = new Map(nodes.map((n) => [n, []]));
  edges.forEach((e) => { if (out.has(e.a) && out.has(e.b)) out.get(e.a).push(e.b); });
  const seen = new Map(); const back = new Set(); const order = [];
  const visit = (n) => {
    seen.set(n, 1);
    out.get(n).forEach((m) => { if (seen.get(m) === 1) back.add(`${n}>${m}`); else if (!seen.get(m)) visit(m); });
    seen.set(n, 2); order.push(n);
  };
  nodes.forEach((n) => { if (!seen.get(n)) visit(n); });
  order.reverse();
  const rank = new Map(nodes.map((n) => [n, 0]));
  order.forEach((n) => out.get(n).forEach((m) => {
    if (!back.has(`${n}>${m}`)) rank.set(m, Math.max(rank.get(m), rank.get(n) + 1));
  }));
  const ranks = [];
  nodes.forEach((n) => { (ranks[rank.get(n)] = ranks[rank.get(n)] || []).push(n); });
  const pos = new Map();
  const { w, h, gx, gy } = size;
  let row = 0;
  ranks.forEach((members) => {
    if (!members) return;
    const placed = [...pos.entries()];
    const bary = (n) => {
      const ups = edges.filter((e) => e.b === n && pos.has(e.a)).map((e) => pos.get(e.a).x);
      return ups.length ? ups.reduce((a, b) => a + b, 0) / ups.length : 1e9;
    };
    if (placed.length) members.sort((a, b) => bary(a) - bary(b));
    for (let i = 0; i < members.length; i += cols) {
      members.slice(i, i + cols).forEach((n, j) => pos.set(n, { x: 12 + j * (w + gx), y: 12 + row * (h + gy) }));
      row += 1;
    }
  });
  const width = Math.min(cols, Math.max(...ranks.filter(Boolean).map((m) => m.length))) * (w + gx) - gx + 24 + 60;
  return { pos, width, height: row * (h + gy) - gy + 24 };
}

/* Down to a lower row, bottom to top; to the same row or back up, out along
   the right-hand side, one lane each, so a cycle is still readable. */
function abEdgePathV(a, b, size, lane) {
  const { w, h } = size;
  if (b.y > a.y) {
    const x1 = a.x + w / 2; const y1 = a.y + h; const x2 = b.x + w / 2; const y2 = b.y - 4;
    const my = (y1 + y2) / 2;
    return { d: `M${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`, mx: (x1 + x2) / 2, my };
  }
  const x1 = a.x + w; const y1 = a.y + h / 2 - 5; const x2 = b.x + w + 4; const y2 = b.y + h / 2 + 5;
  const out = Math.max(x1, x2) + 18 + (lane % 4) * 10;
  return { d: `M${x1} ${y1} C ${out} ${y1}, ${out} ${y2}, ${x2} ${y2}`, mx: (x1 + 6 * out + x2) / 8, my: (y1 + y2) / 2 };
}

function abFileMap(g, s) {
  const files = new Map(g.files.map((f) => [f.path, f]));
  const mine = new Set(s.files);
  const number = new Map(g.subsystems.map((x) => [x.key, x.code]));
  const counts = new Map();
  const shaky = new Set();
  const add = (a, b, confirmed) => {
    const k = `${a}\u0000${b}`;
    counts.set(k, (counts.get(k) || 0) + 1);
    if (!confirmed) shaky.add(k);
  };
  g.edges.forEach((e) => {
    const inA = mine.has(e.source); const inB = mine.has(e.target);
    if (inA && inB) add(e.source, e.target, e.confirmed);
    else if (inA) {
      const other = (files.get(e.target) || {}).subsystem;
      if (other && other !== s.key) add(e.source, `sub:${other}`, e.confirmed);
    } else if (inB) {
      const other = (files.get(e.source) || {}).subsystem;
      if (other && other !== s.key) add(`sub:${other}`, e.target, e.confirmed);
    }
  });
  const edges = [...counts].map(([k, n]) => { const [a, b] = k.split('\u0000'); return { a, b, n, shaky: shaky.has(k) }; });
  const outer = [...new Set(edges.flatMap((e) => [e.a, e.b]).filter((n) => n.startsWith('sub:')))];
  const nodes = [...[...s.files].sort((a, b) => files.get(b).lines - files.get(a).lines), ...outer];
  const size = { w: 220, h: 50, gx: 46, gy: 40 };
  const { pos, width, height } = abLayoutRows(nodes, edges, size, 4);
  const backs = edges.filter((e) => pos.get(e.b).y <= pos.get(e.a).y);
  const lines = edges.map((e) => {
    const p = abEdgePathV(pos.get(e.a), pos.get(e.b), size, backs.indexOf(e));
    return `<g class="ab-link-g" data-a="${esc(e.a)}" data-b="${esc(e.b)}"><path d="${p.d}" class="ab-edge${e.shaky ? ' dash' : ''}"
        stroke-width="${Math.min(3, 1 + e.n / 4).toFixed(1)}" marker-end="url(#ab-arrow-f)"></path>${e.n > 1
        ? `<g class="ab-count"><rect x="${p.mx - 10}" y="${p.my - 8}" width="20" height="15" rx="2"></rect>
        <text x="${p.mx}" y="${p.my + 3.5}" text-anchor="middle">${e.n}</text></g>` : ''}</g>`;
  }).join('');
  const boxes = nodes.map((n) => {
    const { x, y } = pos.get(n);
    if (n.startsWith('sub:')) {
      const key = n.slice(4);
      const sub = g.subsystems.find((x2) => x2.key === key) || { name: key };
      const [one, two] = abWrap(sub.name, 22);
      return `<g class="ab-node ab-outer" data-node="${esc(n)}" ${abGo('subsystem', key)} tabindex="0" role="button" aria-label="${esc(sub.name)}">
        <title>${esc(number.get(key) || '')} · ${esc(sub.name)}</title>
        <rect x="${x}" y="${y}" width="${size.w}" height="${size.h}" rx="2"></rect>
        <rect x="${x + 8}" y="${y + size.h / 2 - 9}" width="34" height="18" rx="9" class="ab-badge"></rect>
        <text x="${x + 25}" y="${y + size.h / 2 + 3.5}" text-anchor="middle" class="ab-badge-n">${esc(number.get(key) || '')}</text>
        <text x="${x + 50}" y="${y + (two ? 21 : size.h / 2 + 4)}" class="ab-ns">${esc(one)}</text>
        ${two ? `<text x="${x + 50}" y="${y + 36}" class="ab-ns">${esc(two)}</text>` : ''}</g>`;
    }
    const f = files.get(n);
    const cut = n.lastIndexOf('/');
    const leaf = n.slice(cut + 1);
    const dir = cut === -1 ? '' : n.slice(0, cut + 1);
    const shortDir = dir.length > 30 ? `…${dir.slice(-29)}` : dir;
    return `<g class="ab-node" data-node="${esc(n)}" ${abGo('file', n)} tabindex="0" role="button" aria-label="${esc(n)}">
      <title>${esc(n)} · ${f.lines.toLocaleString()} lines</title>
      <rect x="${x}" y="${y}" width="${size.w}" height="${size.h}" rx="2"></rect>
      <text x="${x + 10}" y="${y + 20}" class="ab-nt mono">${esc(leaf.length > 26 ? `${leaf.slice(0, 25)}…` : leaf)}</text>
      <text x="${x + 10}" y="${y + 37}" class="ab-ns">${esc(shortDir)}${shortDir ? ' · ' : ''}${f.lines.toLocaleString()}</text></g>`;
  }).join('');
  return `<div class="ab-scroll"><svg class="ab-fmap" viewBox="0 0 ${width} ${height}" style="width:100%;max-width:${width}px;height:auto"
      role="img" aria-label="The files of ${esc(s.name)} and the links between them">
    <defs><marker id="ab-arrow-f" viewBox="0 0 8 8" refX="7.5" refY="4" markerWidth="5" markerHeight="5" orient="auto">
      <path d="M0 0 L8 4 L0 8 z" class="ab-head"></path></marker></defs>${lines}${boxes}</svg></div>`;
}

/* ---- one file, and its parts --------------------------------------------- */

/* The file in the reader's own editor, through the template the review uses
   -- and, like the review, only when this console is served from the machine
   the repository is on: an editor url resolves on the browser's filesystem. */
function abEditorHref(path, line) {
  const tmpl = (state.config || {}).editor_url || '';
  const repo = ((state.project || {}).project || {}).repo || '';
  if (!tmpl || !repo || !['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)) return '';
  return resolveEditorUrl(tmpl, `${repo.replace(/\/+$/, '')}/${path}`, line || 1);
}

/* A file's two views, and the way out to your editor: drawn right under the
   as-built's own tabs, and sticking with them, so switching between what the
   file is and its source is never a scroll away. */
function abFileViews(f, source) {
  const at = source && source.line;
  return `<nav class="ab-views" aria-label="This file">
      <a href="${abHref('file', f.path)}" class="${source ? '' : 'on'}" ${source ? '' : 'aria-current="page"'}>${icon('l-book-open')} What it is</a>
      <a href="${abHref('source', f.path)}" class="${source ? 'on' : ''}" ${source ? 'aria-current="page"' : ''}>${icon('l-file-text')} Source
        <span>${abPlural(f.lines, 'line')}</span></a>
      <span class="ab-views-file mono" title="${esc(f.path)}">${esc(f.path.split('/').pop())}</span>
      ${abEditorHref(f.path, at) ? `<a class="btn btn-sm ab-views-open" href="${esc(abEditorHref(f.path, at))}"
        title="the file as it is checked out now, which may have moved on from this reading">Open in your editor →</a>` : ''}</nav>`;
}

function abFile(g, f, source = null) {
  const files = new Map(g.files.map((x) => [x.path, x]));
  const subs = new Map(g.subsystems.map((x) => [x.key, x]));
  const home = subs.get(f.subsystem);
  const links = (dir, kinds) => [...new Set(g.edges
    .filter((e) => (dir === 'out' ? e.source : e.target) === f.path && kinds.includes(e.kind))
    .map((e) => (dir === 'out' ? e.target : e.source)))];
  const importers = links('in', ['import', 'launch']);
  const imports = links('out', ['import']);
  const starts = links('out', ['launch']);
  const calls = links('out', ['route']);
  const called = links('in', ['route']);
  const parts = g.parts.filter((x) => x.file === f.path);
  const ways = g.ways_in.filter((w) => w.file === f.path);
  const capOf = (id) => g.capabilities.find((c) => c.ways_in.includes(id));
  const reaching = g.capabilities.map((c) => {
    const direct = c.direct.filter((r) => r.file === f.path).reduce((a, r) => a + r.lines, 0);
    const motion = c.in_motion.filter((r) => r.file === f.path).reduce((a, r) => a + r.lines, 0);
    return { c, direct, motion };
  }).filter((x) => x.direct || x.motion);
  const fns = f.functions.filter((x) => x.confirmed);
  const homeLines = home ? home.lines : f.lines;
  const shaky = new Set(g.edges.filter((e) => !e.confirmed && (e.source === f.path || e.target === f.path))
    .map((e) => (e.source === f.path ? e.target : e.source)));

  /* A link where it stands alone; plain inside a box that is already a link,
     since a link inside a link is split apart by the browser. */
  const subPill = (key, link = true) => {
    const x = subs.get(key);
    if (!x) return `<span class="ab-num sm ab-shared" title="not in a subsystem">·</span>`;
    return link ? `<a class="ab-num sm" href="${abHref('subsystem', x.key)}" title="${esc(x.code)} · ${esc(x.name)}">${esc(x.code)}</a>`
      : `<span class="ab-num sm" title="${esc(x.code)} · ${esc(x.name)}">${esc(x.code)}</span>`;
  };
  const plate = abPlate([
    ['lines', f.lines.toLocaleString(), abMeter(f.lines, homeLines),
      home ? `${Math.round((100 * f.lines) / (homeLines || 1))}% of ${esc(home.code)}` : esc(f.kind || 'file')],
    ['imported by', `${importers.length}`, '', importers.length ? `from ${abPlural(new Set(importers.map((x) => (files.get(x) || {}).subsystem)).size, 'subsystem')}` : 'nothing imports it'],
    ['imports', `${imports.length + starts.length}`, '', starts.length ? `${starts.length} started by path` : 'files in this repository'],
    ['functions', `${fns.length}`, '', f.functions.length > fns.length ? `${f.functions.length - fns.length} not found at their line` : 'found at their line'],
    ['tested', f.is_test ? 'a test' : `${f.tested_by.length}`, '', f.is_test ? '' : f.tested_by.length ? abPlural(f.tested_by.length, 'test file') : 'no test reaches it'],
    ['ways in', `${ways.length}`, '', ways.length ? `in ${abPlural(new Set(ways.map((w) => (capOf(w.id) || {}).key).filter(Boolean)).size, 'capability', 'capabilities')}` : 'declares none'],
  ]);

  const box = (path, note = '') => {
    const x = files.get(path) || { subsystem: '' };
    const leaf = path.split('/').pop();
    const outside = x.subsystem !== f.subsystem;
    return `<a class="ab-cx-box${outside ? ' ab-cx-away' : ''}${shaky.has(path) ? ' ab-cx-shaky' : ''}" href="${abHref('file', path)}"
        title="${esc(path)}${shaky.has(path) ? ' · reported, not confirmed' : ''}">${subPill(x.subsystem, false)}
        <span class="mono small">${esc(leaf)}</span><span class="ab-cx-n mono">${esc(note || path.slice(0, Math.max(0, path.lastIndexOf('/'))))}</span></a>`;
  };
  const column = (label, paths, empty, note) => `<div class="ab-cx-col"><p class="ab-cx-l">${label}</p>${paths.length
    ? paths.slice(0, 8).map((x) => box(x, note)).join('') + (paths.length > 8 ? `<p class="dim small">and ${paths.length - 8} more</p>` : '')
    : `<p class="dim small">${empty}</p>`}</div>`;
  const connects = `
    <div class="ab-cx">
      ${column('imported by', [...importers, ...called], 'Nothing in this repository imports it.')}
      <div class="ab-cx-arrow" aria-hidden="true">→</div>
      <div class="ab-cx-here">${f.subsystem ? subPill(f.subsystem) : ''}<b class="mono">${esc(f.path.split('/').pop())}</b></div>
      <div class="ab-cx-arrow" aria-hidden="true">→</div>
      <div class="ab-cx-col">
        ${column('imports', imports, 'It imports nothing from this repository.').replace(/^<div class="ab-cx-col">|<\/div>$/g, '')}
        ${starts.length ? `<p class="ab-cx-l ab-cx-then">starts, by path</p>${starts.map((x) => box(x)).join('')}` : ''}
        ${calls.length ? `<p class="ab-cx-l ab-cx-then">calls the back end in</p>${calls.map((x) => box(x)).join('')}` : ''}
      </div>
    </div>`;

  const waysTable = ways.length ? `<table class="ab-table ab-files"><thead><tr><th>way in</th><th>handler</th>
      <th>capability</th><th>called from</th><th class="num">tested</th></tr></thead><tbody>${ways.map((w) => {
      const c = capOf(w.id);
      return `<tr><td class="mono ab-way">${w.method ? `<span class="ab-verb">${esc(w.method)}</span>` : `<span class="ab-verb quiet">${esc(w.kind)}</span>`}${abBreakable(w.name)}</td>
        <td class="mono small">${esc(w.handler || '')}${w.handler && !w.handler_confirmed ? ' <span class="ab-warn">not found</span>' : ''}</td>
        <td>${c ? `<a class="ab-link" href="${abHref('capability', c.key)}"><span class="ab-sq sm">${esc(c.code)}</span> ${esc(c.name)}</a>`
          : '<span class="dim">plumbing</span>'}</td>
        <td>${(w.callers || []).length ? w.callers.slice(0, 3).map((x) => abPath(x)).join('<br>') : '<span class="dim">—</span>'}</td>
        <td class="num">${(w.tested_by || []).length ? '<span class="ab-yes">●</span>' : '<span class="dim">—</span>'}</td></tr>`;
    }).join('')}</tbody></table>` : '';

  const reachList = reaching.length ? `<ul class="ab-list">${reaching.map(({ c, direct, motion }) => `<li>
      <a class="ab-link" href="${abHref('capability', c.key)}"><span class="ab-sq sm">${esc(c.code)}</span> ${esc(c.name)}</a>
      <span>${direct ? `runs ${direct.toLocaleString()} lines here` : ''}${direct && motion ? ' · ' : ''}${motion ? `sets ${motion.toLocaleString()} in motion here` : ''}</span></li>`).join('')}</ul>`
    : `<p class="db-empty">${ways.length ? 'Its ways in are plumbing.' : 'No capability reaches it.'}</p>`;

  const partName = new Map(parts.map((x) => [x.key, x.name || x.key]));
  const fnGroups = parts.length
    ? parts.map((x) => [x.name || x.key, fns.filter((fn) => fn.part === x.key)]).concat([['not in a part', fns.filter((fn) => !fn.part)]])
    : [['', fns]];
  const fnChips = (list) => `<p class="ab-chips">${list.map((fn) => `<a class="ab-chip mono" href="${abHref('source', f.path, fn.line)}"
    title="read it, at line ${fn.line}${fn.part ? ` · ${esc(partName.get(fn.part) || fn.part)}` : ''}">${
    esc(fn.name)}<span class="ab-fn-l">${fn.line}</span></a>`).join('')}</p>`;
  const functions = fns.length ? fnGroups.filter(([, list]) => list.length).map(([label, list]) =>
    `${label ? `<p class="ab-cx-l">${esc(label)} · ${list.length}</p>` : ''}${fnChips(list)}`).join('')
    : '<p class="db-empty">The reader found no functions here.</p>';

  const chips = (items) => `<p class="ab-chips">${items.map((x) => `<span class="ab-chip">${esc(x)}</span>`).join('')}</p>`;
  // What it talks to outside, by the names the architecture gave them; what
  // the account dismissed -- a library, a tool, this repository -- is left out.
  const arch = g.architecture;
  const dismissed = new Set(Object.values((arch && arch.dismissed) || {}).flat());
  const talksTo = arch
    ? [...new Map(f.outside.filter((m) => !dismissed.has(m)).map((m) => {
      const o = arch.outside.find((x) => x.mentions.includes(m));
      return o ? [o.key, o] : [m, { key: '', name: m }];
    })).values()]
    : f.outside.map((m) => ({ key: '', name: m }));
  const data = [['defines', f.entities_defined], ['writes', f.entities_written], ['reads', f.entities_read]]
    .filter(([, list]) => list.length);
  const cut = f.path.lastIndexOf('/');
  const head = `
    <div class="ab-subhead">${f.subsystem ? subPill(f.subsystem).replace('ab-num sm', 'ab-num') : '<span class="ab-num ab-shared">·</span>'}
      <div><h3 class="ab-title ab-file-title"><span class="dir">${esc(cut === -1 ? '' : f.path.slice(0, cut + 1))}</span><b>${esc(f.path.slice(cut + 1))}</b>
          ${(f.kind || 'code') !== 'code' ? ` <span class="pill">${esc(f.kind)}</span>` : ''}
          ${f.placed_by === 'hub' ? ' <span class="pill">shared by nearly everything</span>' : ''}
          ${f.generated ? ' <span class="pill ab-gen">generated</span>' : ''}</h3>
        <p class="ab-summary">${esc(f.purpose)}</p>
        ${f.generated ? `<p class="ab-gen-note">${icon('l-circle-help')} A tool made this file. It is read for what it says about
          the system and drawn like any other; nothing asks you to review it — what to review is what generates it.</p>` : ''}</div></div>`;
  if (source) return `${head}${abSource(g, f, source.line)}`;

  return `${head}
    ${plate}
    <section class="db-sec">
      <h3 class="db-h">${icon('l-arrow-left-right')} How it connects</h3>
      ${connects}
      <p class="ab-legend">boxes carry their subsystem's ID · a box in another subsystem is shaded · dashed where the link was reported, not confirmed</p>
    </section>
    ${parts.length ? `<section class="db-sec">${abParts(g, f, parts)}</section>` : ''}
    ${ways.length ? `<section class="db-sec">
      <h3 class="db-h">${icon('l-zap')} Ways in <span class="db-count">${ways.length}</span></h3>${waysTable}</section>` : ''}
    <div class="ab-two">
      <section class="db-sec">
        <h3 class="db-h">${icon('l-zap')} Capabilities that reach it <span class="db-count">${reaching.length}</span></h3>
        ${reachList}
      </section>
      <section class="db-sec">
        <h3 class="db-h">${icon('l-flask')} Tests <span class="db-count">${f.tested_by.length}</span></h3>
        ${f.is_test ? '<p class="db-empty">This file is a test.</p>' : f.tested_by.length
          ? `<ul class="ab-list">${f.tested_by.map((x) => `<li>${abPath(x)}</li>`).join('')}</ul>`
          : '<p class="db-empty">No test imports or calls this file.</p>'}
      </section>
    </div>
    <section class="db-sec">
      <h3 class="db-h">${icon('l-file-text')} Functions <span class="db-count">${fns.length}</span></h3>
      ${functions}
    </section>
    <div class="ab-two">
      <section class="db-sec">
        <h3 class="db-h">${icon('l-layers')} Data</h3>
        ${data.length ? data.map(([verb, list]) => `<p class="ab-cx-l">${verb}</p>${chips(list)}`).join('')
          : '<p class="db-empty">It defines, reads and writes no data.</p>'}
      </section>
      <section class="db-sec">
        <h3 class="db-h">${icon('l-globe')} Outside the repository <span class="db-count">${talksTo.length}</span></h3>
        ${talksTo.length ? `<p class="ab-chips">${talksTo.map((o) => (o.key
          ? `<a class="ab-chip" href="${abHref('architecture', '')}" title="${esc(o.what || '')}">${esc(o.name)}</a>`
          : `<span class="ab-chip">${esc(o.name)}</span>`)).join('')}</p>` : '<p class="db-empty">It talks to nothing outside.</p>'}
        ${f.domain_terms.length ? `<p class="ab-cx-l ab-cx-then">terms</p>${chips(f.domain_terms)}` : ''}
      </section>
    </div>`;
}

/* ---- a file's source ------------------------------------------------------

   The file as the reading read it -- the very blob, so every note lines up --
   with what the reading knows written beside the lines it is about. Above each
   function, a card: its span, the way in it handles, the capabilities that run
   it, and who else calls it. Beside a line that links to another file or calls
   the back end, where that leads. A call to a function the reading resolved is
   a link to it, in this file or another, so the code can be followed the way
   it runs. The rail beside the numbers marks what something a person does
   runs: solid when a capability runs it, light when one sets it in motion.

   Highlighting is deliberately small -- comments, strings, keywords -- enough
   to read by, in any language, without a library. */

const AB_LANG = (() => {
  const hash = ['py', 'sh', 'bash', 'zsh', 'rb', 'yml', 'yaml', 'toml', 'r', 'pl', 'tf', 'ini', 'cfg', 'conf', 'mk'];
  const slash = ['js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs', 'go', 'rs', 'java', 'kt', 'c', 'h', 'cc', 'cpp', 'hpp', 'cs',
    'swift', 'scala', 'php', 'dart', 'css', 'scss', 'less', 'vue', 'svelte'];
  const markup = ['html', 'htm', 'xml', 'svg', 'vue', 'svelte'];
  const cache = new Map();
  return (path) => {
    const name = path.split('/').pop().toLowerCase();
    const ext = name.includes('.') ? name.split('.').pop() : name;
    if (cache.has(ext)) return cache.get(ext);
    const isHash = hash.includes(ext) || ['dockerfile', 'makefile', 'procfile'].includes(name) || name.startsWith('.env');
    const isSlash = slash.includes(ext);
    const pieces = [];
    const blocks = {};
    if (ext === 'py') { blocks['"""'] = { end: '"""', cls: 't-s' }; blocks["'''"] = { end: "'''", cls: 't-s' }; }
    if (isSlash) blocks['/*'] = { end: '*/', cls: 't-c' };
    if (markup.includes(ext) || ext === 'md') blocks['<!--'] = { end: '-->', cls: 't-c' };
    Object.keys(blocks).forEach((b) => pieces.push(b.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')));
    if (isHash) pieces.push('#.*$');
    if (isSlash && !['css', 'scss', 'less'].includes(ext)) pieces.push('\\/\\/.*$');
    if (ext === 'sql') pieces.push('--.*$');
    const plain = ['md', 'txt', 'rst', 'csv'].includes(ext);
    if (!plain) {
      pieces.push('"(?:[^"\\\\]|\\\\.)*"', "'(?:[^'\\\\]|\\\\.)*'");
      if (isSlash) pieces.push('`(?:[^`\\\\]|\\\\.)*`');
    }
    pieces.push('[A-Za-z_$][\\w$]*');
    const lang = { re: new RegExp(pieces.join('|'), 'g'), blocks, plain };
    cache.set(ext, lang);
    return lang;
  };
})();

const AB_KEYWORDS = new Set(('def class return if elif else for while import from as with try except finally raise '
  + 'yield lambda pass break continue async await not and or is in None True False self function const let var new '
  + 'this export default catch throw typeof instanceof switch case of null undefined true false void interface type '
  + 'enum extends implements public private protected static struct fn func package impl match use mod pub mut '
  + 'where select insert update delete create table values').split(' '));

/* One line, highlighted. `st` carries a comment or string that runs on to
   the next line; `word` decides what an identifier becomes. */
function abHiLine(src, lang, st, word) {
  let out = '';
  let i = 0;
  if (st.end) {
    const j = src.indexOf(st.end);
    if (j === -1) return `<span class="${st.cls}">${esc(src)}</span>`;
    out += `<span class="${st.cls}">${esc(src.slice(0, j + st.end.length))}</span>`;
    i = j + st.end.length;
    st.end = null;
  }
  const re = lang.re;
  re.lastIndex = i;
  let m;
  while ((m = re.exec(src))) {
    out += esc(src.slice(i, m.index));
    const t = m[0];
    const block = lang.blocks[t];
    if (block) {
      const j = src.indexOf(block.end, m.index + t.length);
      if (j === -1) {
        out += `<span class="${block.cls}">${esc(src.slice(m.index))}</span>`;
        st.end = block.end;
        st.cls = block.cls;
        return out;
      }
      out += `<span class="${block.cls}">${esc(src.slice(m.index, j + block.end.length))}</span>`;
      i = j + block.end.length;
      re.lastIndex = i;
      continue;
    }
    if (/^[A-Za-z_$]/.test(t)) out += word(t);
    else if (/^["'`]/.test(t)) out += `<span class="t-s">${esc(t)}</span>`;
    else out += `<span class="t-c">${esc(t)}</span>`;
    i = m.index + t.length;
  }
  return out + esc(src.slice(i));
}

function abSource(g, f, line) {
  const s = state.asBuilt;
  s.sources = s.sources || {};
  const key = `${g.commit}:${f.path}`;
  const got = s.sources[key];
  if (!got) {
    s.sources[key] = 'loading';
    api(`/api/projects/${encodeURIComponent(s.project)}/as-built/source?path=${encodeURIComponent(f.path)}`)
      .then((r) => { s.sources[key] = r; render(); })
      .catch((e) => { s.sources[key] = { error: (e && e.message) || 'it did not open' }; render(); });
  }
  if (!got || got === 'loading') return '<p class="empty">Opening the file…</p>';
  if (got.error) return `<p class="ab-err">The file did not open: ${esc(got.error)}</p>`;
  if (!got.found) return '<p class="db-empty">The blob this reading read is not in the repository any more.</p>';
  if (line) abScrollSoon(f.path, line);

  const files = new Map(g.files.map((x) => [x.path, x]));
  const subs = new Map(g.subsystems.map((x) => [x.key, x]));
  const caps = new Map(g.capabilities.map((c) => [c.key, c]));
  const waysById = new Map(g.ways_in.map((w) => [w.id, w]));
  const capOfWay = (id) => g.capabilities.find((c) => c.ways_in.includes(id));
  const tag = (subKey) => {
    const x = subs.get(subKey);
    return x ? `<span class="ab-num sm" title="${esc(x.code)} · ${esc(x.name)}">${esc(x.code)}</span>` : '';
  };
  const leaf = (p) => p.split('/').pop();
  const lineOf = (path, name) => {
    const fn = ((files.get(path) || {}).functions || []).find((x) => x.confirmed && x.name === name);
    return fn ? fn.line : 0;
  };

  const text = got.text.replace(/\n$/, '');
  const rows = text.split('\n');
  const fns = got.functions.filter((x) => x.line);
  // The innermost function each line belongs to: larger spans first, so a
  // method overwrites its class.
  const owner = new Array(rows.length + 2).fill(null);
  [...fns].sort((a, b) => ((b.end || b.line) - b.line) - ((a.end || a.line) - a.line)).forEach((fn) => {
    for (let i = fn.line; i <= Math.min(fn.end || fn.line, rows.length); i += 1) owner[i] = fn;
  });
  const startsAt = new Map();
  fns.forEach((fn) => startsAt.set(fn.line, [...(startsAt.get(fn.line) || []), fn]));
  const waysAt = new Map();
  const loose = [];
  got.ways_in.forEach((w) => {
    const way = waysById.get(w.id);
    if (!way) return;
    if (w.line) waysAt.set(w.line, [...(waysAt.get(w.line) || []), way]);
    else loose.push(way);
  });
  const linksAt = new Map();
  const unpinned = [];
  got.links.forEach((l) => (l.line ? linksAt.set(l.line, [...(linksAt.get(l.line) || []), l]) : unpinned.push(l)));
  const localLine = new Map();
  fns.forEach((fn) => { if (!localLine.has(fn.name)) localLine.set(fn.name, fn.line); });
  const ent = new Map();
  [['reads', f.entities_read], ['writes', f.entities_written], ['defines', f.entities_defined]]
    .forEach(([verb, list]) => (list || []).forEach((e) => ent.set(e, verb)));

  const depthOf = (fn) => {
    if (!fn) return '';
    const d = fn.capabilities.map((c) => c.depth);
    return d.includes('direct') ? 'd' : d.length ? 'm' : '';
  };
  const lang = AB_LANG(f.path);
  const st = {};
  const card = (fn) => {
    const ways = waysAt.get(fn.line) || [];
    const runs = fn.capabilities.filter((c) => caps.has(c.key));
    const callers = fn.called_by;
    const out = fn.calls.filter((c) => c.file !== f.path);
    const span = fn.end && fn.end > fn.line ? `lines ${fn.line}–${fn.end}` : `line ${fn.line}`;
    const part = fn.part ? (g.parts.find((x) => x.file === f.path && x.key === fn.part) || {}).name || fn.part : '';
    const who = (c) => `<a class="ab-src-to" href="${abHref('source', c.file, c.line)}" title="${esc(c.file)}, line ${c.line}">${
      c.test ? icon('l-flask') : ''}${c.file === f.path ? '' : `${tag(c.subsystem)}<span class="dim">${esc(leaf(c.file))} ·</span> `}${esc(c.name)}</a>`;
    const list = (xs) => xs.slice(0, 6).map(who).join('') + (xs.length > 6 ? `<span class="dim small">and ${xs.length - 6} more</span>` : '');
    return `<div class="ab-src-card${depthOf(fn) ? ` reach-${depthOf(fn)}` : ''}">
      <p class="ab-src-card-h"><b class="mono">${esc(fn.name)}</b><span class="dim small">${span}${part ? ` · ${esc(part)}` : ''}</span></p>
      ${ways.map((w) => {
        const c = capOfWay(w.id);
        return `<p class="ab-src-meta"><span class="ab-src-k">way in</span><span class="mono">${w.method ? `<span class="ab-verb">${esc(w.method)}</span>` : `<span class="ab-verb quiet">${esc(w.kind)}</span>`}${esc(w.name)}</span>
          ${c ? `<a class="ab-link" href="${abHref('capability', c.key)}"><span class="ab-sq sm">${esc(c.code)}</span> ${esc(c.name)}</a>` : '<span class="dim small">plumbing</span>'}
          ${(w.tested_by || []).length ? `<span class="ab-yes small">${icon('l-flask')} tested</span>` : '<span class="dim small">no test calls it</span>'}</p>`;
      }).join('')}
      ${runs.length ? `<p class="ab-src-meta"><span class="ab-src-k">run by</span>${runs.map((r) => {
        const c = caps.get(r.key);
        return `<a class="ab-link${r.depth === 'direct' ? '' : ' ab-src-moved'}" href="${abHref('capability', c.key)}"
          title="${r.depth === 'direct' ? 'runs it' : 'sets it in motion, one call further'}"><span class="ab-sq sm">${esc(c.code)}</span> ${esc(c.name)}</a>`;
      }).join('')}</p>` : ''}
      ${callers.length ? `<p class="ab-src-meta"><span class="ab-src-k">called from</span>${list(callers)}</p>` : ''}
      ${out.length ? `<p class="ab-src-meta"><span class="ab-src-k">calls out to</span>${list(out)}</p>` : ''}
    </div>`;
  };
  const note = (l) => {
    if (l.kind === 'route') {
      const way = waysById.get(l.way_in);
      const at = way ? lineOf(way.file, way.handler) : 0;
      return l.target
        ? `<a class="ab-src-note" href="${abHref('source', l.target, at)}" title="${esc(l.target)}${way && way.handler ? ` · ${esc(way.handler)}` : ''}">→ ${esc(l.route)} ${tag((files.get(l.target) || {}).subsystem)}${esc(leaf(l.target))}${way && way.handler ? ` · ${esc(way.handler)}` : ''}</a>`
        : `<span class="ab-src-note shaky" title="no route this repository serves matches it">→ ${esc(l.route)} · served nowhere here</span>`;
    }
    return `<a class="ab-src-note${l.confirmed ? '' : ' shaky'}" href="${abHref('source', l.target)}"
      title="${l.kind === 'launch' ? 'starts' : 'imports'} ${esc(l.target)}${l.confirmed ? '' : ' · reported, not confirmed'}">${
      l.kind === 'launch' ? '▶' : '→'} ${tag((files.get(l.target) || {}).subsystem)}${esc(leaf(l.target))}</a>`;
  };

  const body = rows.map((src, idx) => {
    const n = idx + 1;
    const fn = owner[n];
    const targets = new Map((fn ? fn.calls : []).map((c) => [c.name, c]));
    const word = (t) => {
      const to = targets.get(t);
      if (to && !(to.file === f.path && to.line === n)) {
        return `<a class="ab-src-call" href="${abHref('source', to.file, to.line)}" title="${esc(to.file)}, line ${to.line}">${esc(t)}</a>`;
      }
      if (localLine.has(t) && localLine.get(t) !== n && !lang.plain) {
        return `<a class="ab-src-call" href="${abHref('source', f.path, localLine.get(t))}" title="line ${localLine.get(t)}">${esc(t)}</a>`;
      }
      if (ent.has(t)) return `<span class="ab-src-ent" title="data this file ${ent.get(t)}">${esc(t)}</span>`;
      if (!lang.plain && AB_KEYWORDS.has(t)) return `<span class="t-k">${esc(t)}</span>`;
      return esc(t);
    };
    const cards = (startsAt.get(n) || []).map(card).join('');
    const depth = depthOf(fn);
    return `${cards}<div class="ab-src-row${depth ? ` reach-${depth}` : ''}${n === line ? ' here' : ''}" id="ab-L${n}">`
      + `<a class="ab-src-n" href="${abHref('source', f.path, n)}">${n}</a>`
      + `<code>${abHiLine(src, lang, st, word) || ' '}${(linksAt.get(n) || []).map(note).join('')}</code></div>`;
  }).join('');

  const partOf = new Map(g.parts.filter((x) => x.file === f.path).map((x) => [x.key, x.name || x.key]));
  const groups = partOf.size
    ? [...partOf].map(([k, name]) => [name, fns.filter((fn) => fn.part === k)]).concat([['not in a part', fns.filter((fn) => !fn.part)]])
    : [['', fns]];
  const outline = `<aside class="ab-src-outline" aria-label="Functions in this file">
      <p class="ab-src-k">functions · ${fns.length}</p>
      ${groups.filter(([, xs]) => xs.length).map(([name, xs]) => `${name ? `<p class="ab-src-part">${esc(name)}</p>` : ''}
        <ul>${xs.map((fn) => `<li><a class="${fn.line === line ? 'on' : ''}${depthOf(fn) ? ` reach-${depthOf(fn)}` : ''}" href="${abHref('source', f.path, fn.line)}">
          <span class="mono">${esc(fn.name)}</span><span class="ab-fn-l">${fn.line}</span></a></li>`).join('')}</ul>`).join('')}
      ${loose.length ? `<p class="ab-src-k ab-cx-then">ways in with no function found</p>${loose.map((w) => `<p class="mono small">${esc(w.method || w.kind)} ${esc(w.name)}</p>`).join('')}` : ''}
      ${unpinned.length ? `<p class="ab-src-k ab-cx-then">links not found on a line</p>${unpinned.map((l) => `<p class="small">${note(l)}</p>`).join('')}` : ''}
    </aside>`;
  return `
    ${got.changed_since ? `<p class="dim small ab-changed">This is the file as the reading read it, at ${abShort(got.commit)}. It has changed since; read the as-built again to see it as it is.</p>` : ''}
    <div class="ab-src-wrap">
      <div>
        <p class="ab-legend">the rail marks what capabilities run: solid runs it · light sets it in motion ·
          an underlined name goes to the function it calls · → where a line links to · dashed where the link was reported, not confirmed</p>
        <div class="ab-src" data-lang="${esc(f.path.split('.').pop())}"><div class="ab-src-in">${body}</div></div>
      </div>
      ${outline}
    </div>`;
}

/* Land on the line an address names, once: the page redraws on every event,
   and a redraw must not pull the reader back to where they arrived. */
function abScrollSoon(path, line) {
  const s = state.asBuilt;
  const want = `${path}@${line}`;
  if (s.scrolled === want) return;
  setTimeout(() => {
    const row = document.getElementById(`ab-L${line}`);
    if (!row) return;
    s.scrolled = want;
    const cardAbove = row.previousElementSibling && row.previousElementSibling.classList.contains('ab-src-card')
      ? row.previousElementSibling : row;
    cardAbove.scrollIntoView({ block: 'start' });
  }, 0);
}

/* ---- how it runs ---------------------------------------------------------

   The Architecture tab: who uses the system, what runs where, what it talks
   to outside this repository, and every way in. Where each subsystem runs and
   what an outside system is are the cartographer's; every arrow and number is
   the graph's. Three strengths of line, the same everywhere: dark where code
   confirmed it, grey where the reader reported it in the files it names,
   dashed where a system is only named -- in configuration or documentation. */

function abArchitecture(g) {
  const a = g.architecture;
  const runtimeOf = abRuntimeOf(g);
  return `
    <section class="db-sec">
      <h3 class="db-h">${icon('l-play')} How it runs</h3>
      ${a && a.runtimes.length ? `<p class="ab-summary ab-arch-lead">Who uses it, what runs where, and what it talks to outside this
          repository. Each box in the middle is a process, carrying the subsystems that run in it.
          <a href="#" data-help-open="as-built-architecture">How to read this</a></p>
        <div class="card ab-map">${abArchMap(g)}
          <p class="ab-legend ab-legend3"><span><i></i>code confirmed it</span><span><i class="said"></i>the reader reported it, in the files it names</span>
            <span><i class="named"></i>only named, in configuration or documentation</span><span>select a box to open it</span></p></div>`
      : `<p class="db-empty">This reading was made before the as-built drew how the system runs. Read it again and it will —
          one more question to the cartographer; files that did not change are not read again.</p>`}
    </section>
    ${a ? abOutsideSystems(g) : ''}
    ${abEndpoints(g, runtimeOf)}`;
}

/* Which process a file's code runs in, by its subsystem. */
function abRuntimeOf(g) {
  const a = g.architecture;
  const files = new Map(g.files.map((f) => [f.path, f]));
  const home = new Map();
  ((a && a.runtimes) || []).forEach((r) => r.subsystems.forEach((k) => home.set(k, r)));
  return (path) => home.get((files.get(path) || {}).subsystem) || null;
}

function abArchMap(g) {
  const a = g.architecture;
  const subs = new Map(g.subsystems.map((s) => [s.key, s]));
  const caps = new Map(g.capabilities.map((c) => [c.key, c]));
  const W = 1080;
  const X = { who: 20, run: 250, out: 760 };
  const WD = { who: 150, run: 380, out: 305 };
  const top = 44;

  // the processes, stacked, each as tall as the subsystems it carries
  let y = top;
  const run = new Map();
  a.runtimes.forEach((r) => {
    const h = 74 + 22 * r.subsystems.length + 18;
    run.set(r.key, { r, y, h });
    y += h + 58;
  });
  const runBottom = y - 58;

  // outside systems, in the order of the process they talk to, so lines cross less.
  // What builds or ships it goes in the band below, with nothing drawn to it.
  const order = (o) => Math.min(...o.runtimes.map((k) => a.runtimes.findIndex((r) => r.key === k)).filter((i) => i >= 0), 99);
  const outs = a.outside.filter((o) => !o.tooling).sort((p, q) => order(p) - order(q));
  const toolOuts = a.outside.filter((o) => o.tooling);
  const out = new Map();
  y = top;
  outs.forEach((o) => {
    const h = o.calls_in.length ? 96 : 80;
    out.set(o.key, { o, y, h });
    y += h + 20;
  });
  const outBottom = y - 20;

  // people, level with the first process they come in through
  const who = [];
  let floor = top;
  a.actors.forEach((x) => {
    const at = x.enters.length ? run.get(x.enters[0]) : null;
    const want = at ? at.y + 10 : floor;
    const yy = Math.max(want, floor);
    who.push({ x, y: yy, h: 62 });
    floor = yy + 62 + 26;
  });

  const bandTop = Math.max(runBottom, outBottom, floor) + 40;
  const tools = a.tooling.map((k) => subs.get(k)).filter(Boolean);
  const pillRows = Math.ceil(tools.length / 3);
  const boxTop = bandTop + 38 + pillRows * 26 + (pillRows ? 8 : 0);
  const boxRows = Math.ceil(toolOuts.length / 4);
  const H = (tools.length || toolOuts.length ? boxTop + boxRows * 66 : bandTop + 70) + 12;

  // ports: where lines meet a process's right edge, spread along it
  const rightPorts = new Map();
  const port = (key, id) => {
    if (!rightPorts.has(key)) rightPorts.set(key, []);
    rightPorts.get(key).push(id);
  };
  outs.forEach((o) => {
    o.runtimes.forEach((k) => port(k, `to:${o.key}`));
    if (o.calls_in.length) abCallsInRuntimes(g, o).forEach((k) => port(k, `from:${o.key}`));
  });
  const portY = (key, id) => {
    const box = run.get(key);
    const list = rightPorts.get(key) || [];
    const i = list.indexOf(id);
    return box.y + 22 + ((box.h - 44) * (i + 1)) / (list.length + 1);
  };

  const sq = (code, x0, y0) => `<g class="sq" transform="translate(${x0},${y0})"><rect width="32" height="16" rx="2"/>
    <text x="16" y="11.5" text-anchor="middle">${esc(code)}</text></g>`;
  const pill = (s, x0, y0, sup) => `<a href="${abHref('subsystem', s.key)}"><g class="pill${sup ? ' sup' : ''}" transform="translate(${x0},${y0})">
    <title>${esc(s.code)} · ${esc(s.name)}</title><rect width="38" height="18" rx="9"/><text x="19" y="12.5" text-anchor="middle">${esc(s.code)}</text></g></a>`;
  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || `${one}s`)}`;
  const parts = [];
  const edges = [];

  parts.push(`<text x="${X.who}" y="22" class="t-col">who uses it</text>
    <text x="${X.run}" y="22" class="t-col">what runs</text>
    <text x="${X.out}" y="22" class="t-col">outside this repository</text>`);

  // where people's lines meet a process's left edge, spread along it
  const leftPorts = new Map();
  who.forEach(({ x }) => x.enters.forEach((k) => leftPorts.set(k, [...(leftPorts.get(k) || []), x.name])));
  const leftY = (key, name) => {
    const box = run.get(key);
    const list = leftPorts.get(key) || [];
    return box.y + 20 + ((box.h - 40) * (list.indexOf(name) + 1)) / (list.length + 1);
  };
  who.forEach(({ x, y: yy, h }) => {
    parts.push(`<g class="who"><title>${esc(x.name)}${x.what ? ` — ${esc(x.what)}` : ''}</title>
      <rect class="box who" x="${X.who}" y="${yy}" width="${WD.who}" height="${h}" rx="6"/>
      <text x="${X.who + 16}" y="${yy + 26}" class="t-name">${esc(abWrap(x.name, 16)[0])}</text>
      <text x="${X.who + 16}" y="${yy + 45}" class="t-sub">${esc(abWrap(x.what || '', 22)[0])}</text></g>`);
    x.enters.forEach((k) => {
      if (!run.get(k)) return;
      const ty = leftY(k, x.name);
      edges.push(`<path class="e code" d="M${X.who + WD.who},${yy + h / 2} C${X.who + WD.who + 40},${yy + h / 2} ${X.run - 40},${ty} ${X.run},${ty}" marker-end="url(#ab-ah-code)"/>`);
    });
    const codes = x.capabilities.map((k) => (caps.get(k) || {}).code).filter(Boolean);
    codes.slice(0, 2).forEach((c, i) => parts.push(sq(c, X.who + WD.who + 6 + i * 35, yy - 2)));
    if (codes.length > 2) parts.push(`<text x="${X.who + 16}" y="${yy + h + 14}" class="t-how">and ${codes.length - 2} more capabilities</text>`);
  });

  a.runtimes.forEach((r) => {
    const { y: yy, h } = run.get(r.key);
    const kinds = Object.entries(r.ways_in).map(([k, n]) => plural(n, k === 'screen' ? 'screen' : k)).join(' · ');
    parts.push(`<rect class="box run" x="${X.run}" y="${yy}" width="${WD.run}" height="${h}" rx="8"/>
      <text x="${X.run + 18}" y="${yy + 28}" class="t-name">${esc(r.name)}</text>
      <text x="${X.run + 18}" y="${yy + 47}" class="t-sub">${esc(abWrap(r.what || '', 58)[0])}</text>`);
    r.subsystems.forEach((k, i) => {
      const s = subs.get(k);
      if (!s) return;
      const py = yy + 60 + i * 22;
      parts.push(`${pill(s, X.run + 18, py, s.role === 'support')}
        <text x="${X.run + 64}" y="${py + 13}" class="t-sub">${esc(abWrap(s.name, 44)[0])}</text>`);
    });
    if (kinds) parts.push(`<text x="${X.run + 18}" y="${yy + h - 12}" class="t-how">${esc(kinds)}</text>`);
  });

  // between processes
  a.links.forEach((l, i) => {
    const s = run.get(l.source);
    const t = run.get(l.target);
    if (!s || !t) return;
    const cls = l.confirmed ? 'code' : 'said';
    const label = `${l.kind === 'http' ? 'HTTP' : 'starts'} · ${plural(l.count, 'link')}`;
    const x0 = X.run + WD.run / 2 + (i % 3) * 24 - 24;
    if (t.y > s.y) {
      edges.push(`<path class="e ${cls}" d="M${x0},${s.y + s.h} L${x0},${t.y}" marker-end="url(#ab-ah-${cls})"/>`);
      parts.push(`<text x="${x0 + 10}" y="${(s.y + s.h + t.y) / 2 + 4}" class="t-edge">${esc(label)}</text>`);
    } else {
      edges.push(`<path class="e ${cls}" d="M${X.run},${s.y + 20} C${X.run - 60},${s.y + 20} ${X.run - 60},${t.y + t.h - 20} ${X.run},${t.y + t.h - 20}" marker-end="url(#ab-ah-${cls})"/>`);
      parts.push(`<text x="${X.run - 58}" y="${(s.y + t.y + t.h) / 2}" class="t-edge">${esc(label)}</text>`);
    }
  });

  outs.forEach((o) => {
    const { y: yy, h } = out.get(o.key);
    const named = o.evidence === 'named';
    const how = named ? `only named · ${plural(o.files.length, 'file')}` : `named by ${plural(o.files.length, 'file')}`
      + (o.entities_written.length || o.entities_read.length ? ` · writes ${o.entities_written.length}, reads ${o.entities_read.length}` : '');
    parts.push(`<g class="ab-arch-out" data-ab-scroll="ab-os-${esc(o.key)}" tabindex="0" role="button">
      <title>${esc(o.name)}${o.what ? ` — ${esc(o.what)}` : ''}</title>
      <rect class="box ${named ? 'named' : 'out'}" x="${X.out}" y="${yy}" width="${WD.out}" height="${h}" rx="6"/>
      <text x="${X.out + 16}" y="${yy + 26}" class="t-name">${esc(abWrap(o.name, 30)[0])}</text>
      <text x="${X.out + 16}" y="${yy + 45}" class="t-sub">${esc(abWrap(o.what || '', 44)[0])}</text>
      <text x="${X.out + 16}" y="${yy + 64}" class="t-how">${esc(how)}</text>
      ${o.calls_in.length ? `<text x="${X.out + 16}" y="${yy + 82}" class="t-how">calls in: ${esc(plural(o.calls_in.length, 'route'))} nothing here calls</text>` : ''}
      </g>`);
    const cls = named ? 'named' : 'said';
    o.runtimes.forEach((k) => {
      if (!run.has(k)) return;
      const py = portY(k, `to:${o.key}`);
      edges.push(`<path class="e ${cls}" d="M${X.run + WD.run},${py} C${X.run + WD.run + 70},${py} ${X.out - 70},${yy + h / 2 - 8} ${X.out},${yy + h / 2 - 8}" marker-end="url(#ab-ah-said)"/>`);
    });
    if (o.calls_in.length) {
      const into = abCallsInRuntimes(g, o);
      const through = g.capabilities.filter((c) => c.ways_in.some((w) => o.calls_in.includes(w)));
      into.forEach((k, i) => {
        if (!run.has(k)) return;
        const py = portY(k, `from:${o.key}`);
        const sy = yy + h / 2 + 12;
        edges.push(`<path class="e said" d="M${X.out},${sy} C${X.out - 70},${sy} ${X.run + WD.run + 70},${py} ${X.run + WD.run},${py}" marker-end="url(#ab-ah-said)"/>`);
        if (i === 0) through.slice(0, 2).forEach((c, j) => parts.push(sq(c.code, (X.run + WD.run + X.out) / 2 - 16 + j * 36, (py + sy) / 2 - 8)));
      });
    }
  });

  {
    parts.push(`<rect class="band" x="20" y="${bandTop}" width="${W - 40}" height="${H - bandTop - 8}" rx="8"/>
      <text x="38" y="${bandTop + 24}" class="t-col">build and tooling · not part of what runs</text>`);
    tools.forEach((s, i) => {
      const cx = 38 + (i % 3) * 340;
      const cy = bandTop + 38 + Math.floor(i / 3) * 26;
      parts.push(`${pill(s, cx, cy, true)}<text x="${cx + 46}" y="${cy + 13}" class="t-sub">${esc(abWrap(s.name, 40)[0])}</text>`);
    });
    toolOuts.forEach((o, i) => {
      const bx = 38 + (i % 4) * 252;
      const by = boxTop + Math.floor(i / 4) * 66;
      const named = o.evidence === 'named';
      parts.push(`<g class="ab-arch-out" data-ab-scroll="ab-os-${esc(o.key)}" tabindex="0" role="button">
        <title>${esc(o.name)}${o.what ? ` — ${esc(o.what)}` : ''}</title>
        <rect class="box ${named ? 'named' : 'out'}" x="${bx}" y="${by}" width="236" height="54" rx="6"/>
        <text x="${bx + 14}" y="${by + 23}" class="t-name">${esc(abWrap(o.name, 24)[0])}</text>
        <text x="${bx + 14}" y="${by + 41}" class="t-how">${esc(named ? `only named · ${plural(o.files.length, 'file')}` : `named by ${plural(o.files.length, 'file')}`)}</text>
        </g>`);
    });
    if (!tools.length && !toolOuts.length) {
      parts.push(`<text x="38" y="${bandTop + 50}" class="t-sub">Nothing — every subsystem runs, and nothing outside is only for building or shipping it.</text>`);
    }
  }

  return `<svg class="ab-arch" viewBox="0 0 ${W} ${H}" role="img" aria-label="How it runs">
    <defs>
      <marker id="ab-ah-code" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="ah-code" d="M0,0 L10,5 L0,10 z"/></marker>
      <marker id="ab-ah-said" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="ah-said" d="M0,0 L10,5 L0,10 z"/></marker>
    </defs>
    ${edges.join('')}${parts.join('')}</svg>`;
}

/* The processes an outside system calls into: where the routes it calls are. */
function abCallsInRuntimes(g, o) {
  const runtimeOf = abRuntimeOf(g);
  const ways = new Map(g.ways_in.map((w) => [w.id, w]));
  return [...new Set(o.calls_in.map((id) => ways.get(id)).filter(Boolean)
    .map((w) => (runtimeOf(w.file) || {}).key).filter(Boolean))];
}

function abOutsideSystems(g) {
  const a = g.architecture;
  const subs = new Map(g.subsystems.map((s) => [s.key, s]));
  const caps = new Map(g.capabilities.map((c) => [c.key, c]));
  const why = { library: 'libraries', tool: 'tools', 'this repository': 'this repository, named as if it were outside',
    'not a system': 'not systems' };
  const dismissed = Object.entries(a.dismissed || {}).filter(([, v]) => v.length);
  return `<section class="db-sec">
    <h3 class="db-h">${icon('l-globe')} Outside systems <span class="db-count">${a.outside.length}</span></h3>
    ${a.outside.length ? `<ul class="ab-osys">${a.outside.map((o) => `<li id="ab-os-${esc(o.key)}">
      <span class="ab-os-n">${esc(o.name)}${o.tooling ? ' <span class="pill">build and tooling</span>' : o.evidence === 'named' ? ' <span class="pill">only named</span>' : ''}</span>
      <span class="ab-os-h mono">${o.evidence === 'named' ? 'named in' : 'named by'} ${abPlural(o.files.length, 'file')}${
        o.entities_written.length || o.entities_read.length ? ` · writes ${o.entities_written.length} · reads ${o.entities_read.length}` : ''}</span>
      <span class="ab-os-w">${esc(o.what)}</span>
      <span class="ab-os-v">${o.subsystems.length ? `through ${o.subsystems.map((k) => subs.get(k)).filter(Boolean)
        .map((s) => `<a class="ab-num sm${a.tooling.includes(s.key) ? ' ab-tool' : ''}" href="${abHref('subsystem', s.key)}"
          title="${esc(s.code)} · ${esc(s.name)}${a.tooling.includes(s.key) ? ' · build and tooling' : ''}">${esc(s.code)}</a>`).join('')}` : ''}
        ${o.capabilities.length ? `<span class="dim">·</span> reached by ${o.capabilities.map((k) => caps.get(k)).filter(Boolean)
          .map((c) => `<a class="ab-sq sm" href="${abHref('capability', c.key)}" title="${esc(c.code)} · ${esc(c.name)}">${esc(c.code)}</a>`).join('')}` : ''}
        ${o.calls_in.length ? `<span class="dim">·</span> calls in: ${o.calls_in.map((id) => `<code>${abBreakable(id.replace(/^route:/, ''))}</code>`).join(', ')}` : ''}
        <span class="dim">·</span> <span class="dim" title="${esc(o.files.join('\n'))}">${esc(o.mentions.slice(0, 3).join(' · '))}${o.mentions.length > 3 ? ` · +${o.mentions.length - 3}` : ''}</span></span>
    </li>`).join('')}</ul>` : '<p class="db-empty">It talks to nothing outside this repository.</p>'}
    ${dismissed.length ? `<p class="ab-os-not"><b>Not outside systems</b>, though files named them:</p>
      <ul class="ab-os-dis">${dismissed.map(([k, v]) => `<li><span class="ab-os-k">${esc(why[k] || k)}</span>
        <span>${v.slice(0, 12).map((m) => esc(m)).join(' · ')}${v.length > 12 ? ` <span class="dim">and ${v.length - 12} more</span>` : ''}</span></li>`).join('')}</ul>` : ''}
    ${(a.unsorted || []).length ? `<p class="ab-os-not"><b>Not sorted</b> by the account: ${a.unsorted.map((m) => `<code>${esc(m)}</code>`).join(' ')}.</p>` : ''}
  </section>`;
}

/* Every way in, where it runs: what it is, what handles it, the capability it
   belongs to, and who calls it -- code here, an outside system, or nothing. */
function abEndpoints(g, runtimeOf) {
  const a = g.architecture;
  const s = state.asBuilt;
  const ep = s.ep || (s.ep = { q: '', kind: '', caller: '', cap: '', untested: false, sort: 'name', dir: 1 });
  const files = new Map(g.files.map((f) => [f.path, f]));
  const subs = new Map(g.subsystems.map((x) => [x.key, x]));
  const capOf = new Map();
  g.capabilities.forEach((c) => c.ways_in.forEach((id) => capOf.set(id, c)));
  const outsideOf = (id) => ((a && a.outside) || []).find((o) => o.calls_in.includes(id));
  const lineOf = (w) => ((files.get(w.file) || {}).functions || []).find((fn) => fn.confirmed && fn.name === w.handler);
  const all = g.ways_in.filter((w) => w.kind !== 'export');
  const exports = g.ways_in.length - all.length;

  // who calls it, as one word the filter and the sort both use
  const callerOf = (w) => (outsideOf(w.id) ? 'outside' : (w.callers || []).length ? 'here'
    : (w.unconfirmed_callers || []).length ? 'here' : w.kind === 'route' ? 'nothing' : 'person');
  const facts = all.map((w) => ({ w, cap: capOf.get(w.id) || null, caller: callerOf(w), o: outsideOf(w.id),
    tested: (w.tested_by || []).length > 0,
    text: `${w.method || ''} ${w.name} ${w.file} ${w.handler || ''}`.toLowerCase() }));
  const count = (pick) => facts.reduce((m, x) => m.set(pick(x), (m.get(pick(x)) || 0) + 1), new Map());
  const kinds = count((x) => x.w.kind);
  const callers = count((x) => x.caller);

  const kept = facts.filter((x) => (!ep.kind || x.w.kind === ep.kind) && (!ep.caller || x.caller === ep.caller)
    && (!ep.cap || (ep.cap === '-' ? !x.cap : x.cap && x.cap.key === ep.cap)) && (!ep.untested || !x.tested));
  const order = { screen: 0, route: 1, command: 2, job: 3, event: 4 };
  const by = {
    name: (x) => `${x.w.name} ${x.w.method || ''}`,
    kind: (x) => `${order[x.w.kind] ?? 9} ${x.w.method || ''} ${x.w.name}`,
    file: (x) => `${x.w.file} ${x.w.handler || ''}`,
    cap: (x) => (x.cap ? `0 ${x.cap.code} ${x.w.name}` : `1 ${x.w.name}`),  // plumbing after every capability
    caller: (x) => `${{ outside: 0, here: 1, person: 2, nothing: 3 }[x.caller]} ${String(9999 - ((x.w.callers || []).length)).padStart(4, '0')} ${x.w.name}`,
    tested: (x) => `${x.tested ? 1 : 0} ${x.w.name}`,
  };
  const key = by[ep.sort] || by.name;
  kept.sort((p, q) => key(p).localeCompare(key(q)) * ep.dir);

  const groups = new Map();
  kept.forEach((x) => {
    const r = runtimeOf(x.w.file);
    const k = r ? r.key : `sub:${(files.get(x.w.file) || {}).subsystem || ''}`;
    if (!groups.has(k)) {
      groups.set(k, { title: r ? r.name : ((subs.get((files.get(x.w.file) || {}).subsystem) || {}).name || 'Not in a subsystem'), list: [] });
    }
    groups.get(k).list.push(x);
  });
  const hide = (x) => (ep.q && !x.text.includes(ep.q.toLowerCase()) ? ' hidden' : '');
  const row = (x) => {
    const { w, cap: c, o } = x;
    const fn = lineOf(w);
    const n = (w.callers || []).length;
    const shaky = (w.unconfirmed_callers || []).length;
    return `<tr data-ab-ep-text="${esc(x.text)}"${hide(x)}><td class="mono ab-way">${w.method ? `<span class="ab-verb">${esc(w.method)}</span>` : `<span class="ab-verb quiet">${esc(w.kind)}</span>`}${abBreakable(w.name)}</td>
      <td>${abPath(w.file, fn ? abHref('source', w.file, fn.line) : abHref('file', w.file))}${w.handler ? `<span class="ab-ep-h mono">${esc(w.handler)}${w.handler_confirmed ? '' : ' <span class="ab-warn">not found</span>'}</span>` : ''}</td>
      <td>${c ? `<a class="ab-link" href="${abHref('capability', c.key)}"><span class="ab-sq sm">${esc(c.code)}</span> ${esc(c.name)}</a>` : '<span class="dim">plumbing</span>'}</td>
      <td>${o ? `<button type="button" class="ab-link" data-ab-scroll="ab-os-${esc(o.key)}">${esc(o.name)}</button>`
        : n ? `<span title="${esc(w.callers.join('\n'))}">${abPlural(n, 'file')}</span>`
        : shaky ? `<span class="dim" title="${esc(w.unconfirmed_callers.join('\n'))}">${abPlural(shaky, 'file')}, not confirmed</span>`
        : x.caller === 'nothing' ? '<span class="ab-warn">nothing here</span>' : '<span class="dim">a person</span>'}</td>
      <td class="num">${x.tested ? '<span class="ab-yes" title="a test reaches it">●</span>' : '<span class="dim">—</span>'}</td></tr>`;
  };
  const th = (k, label, cls = '') => `<th class="${cls}"><button type="button" class="ab-sort${ep.sort === k ? ' on' : ''}" data-ab-ep="sort" data-v="${k}"
      aria-sort="${ep.sort === k ? (ep.dir > 0 ? 'ascending' : 'descending') : 'none'}">${label}${ep.sort === k ? (ep.dir > 0 ? ' ↑' : ' ↓') : ''}</button></th>`;
  const chip = (field, v, label, n) => `<button type="button" class="ab-fchip${ep[field] === v ? ' on' : ''}" data-ab-ep="${field}" data-v="${esc(v)}"
      aria-pressed="${ep[field] === v}">${esc(label)}${n != null ? `<span>${n}</span>` : ''}</button>`;
  const callerWords = { here: 'code here', outside: 'an outside system', person: 'a person', nothing: 'nothing here' };
  const shown = kept.filter((x) => !hide(x)).length;
  const filtered = ep.q || ep.kind || ep.caller || ep.cap || ep.untested;
  const caps = abCapOrder(g).filter((c) => facts.some((x) => x.cap && x.cap.key === c.key));

  if (s.epFocus) {
    setTimeout(() => {
      const box = document.querySelector('[data-ab-ep-q]');
      if (box && document.activeElement !== box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
    }, 0);
  }
  return `<section class="db-sec" id="ab-endpoints">
    <h3 class="db-h">${icon('l-zap')} Endpoints <span class="db-count">${all.length}</span></h3>
    <div class="ab-filters">
      <input type="search" class="ab-fsearch" data-ab-ep-q="1" placeholder="Filter by path, handler or file" value="${esc(ep.q)}" aria-label="Filter endpoints">
      <div class="ab-frow"><span class="ab-flabel">kind</span>${chip('kind', '', 'all', all.length)}${[...kinds]
        .sort((p, q) => (order[p[0]] ?? 9) - (order[q[0]] ?? 9)).map(([k, n]) => chip('kind', k, k, n)).join('')}</div>
      <div class="ab-frow"><span class="ab-flabel">called by</span>${chip('caller', '', 'anyone')}${['here', 'outside', 'person', 'nothing']
        .filter((k) => callers.get(k)).map((k) => chip('caller', k, callerWords[k], callers.get(k))).join('')}</div>
      <div class="ab-frow"><span class="ab-flabel">capability</span>
        <select class="ab-fselect" data-ab-ep-cap="1" aria-label="Capability">
          <option value="">any</option>
          ${caps.map((c) => `<option value="${esc(c.key)}"${ep.cap === c.key ? ' selected' : ''}>${esc(c.code)} · ${esc(c.name)}</option>`).join('')}
          <option value="-"${ep.cap === '-' ? ' selected' : ''}>plumbing — in no capability</option>
        </select>
        <label class="ab-fcheck"><input type="checkbox" data-ab-ep="untested" ${ep.untested ? 'checked' : ''}> untested only</label>
        <span class="ab-fcount" data-ab-ep-count="1">${filtered ? `${shown} of ${all.length}` : ''}</span>
        ${filtered ? '<button type="button" class="ab-link ab-fclear" data-ab-ep="clear">clear</button>' : ''}
      </div>
    </div>
    ${groups.size ? [...groups.values()].map(({ title, list }) => `<div class="ab-ep-grp">
      <p class="ab-ep-g">${esc(title)} <span data-ab-ep-n="1">${list.filter((x) => !hide(x)).length}</span></p>
      <table class="ab-table ab-eps"><thead><tr>${th('kind', 'way in')}${th('file', 'handled in')}${th('cap', 'capability')}${th('caller', 'called from')}${th('tested', 'tested', 'num')}</tr></thead>
        <tbody>${list.map(row).join('')}</tbody></table></div>`).join('')
      : '<p class="db-empty">No endpoint matches.</p>'}
    ${exports ? `<p class="dim small">Not listed: ${abPlural(exports, 'export')} — names files offer one another, not ways in from outside.</p>` : ''}
  </section>`;
}

/* The text filter works on the page as it stands: redrawing on every key
   would rebuild the box being typed in. What it leaves shown is what the next
   redraw draws, because the redraw reads the same filter. */
function abEpFilterRows(q) {
  const box = document.getElementById('ab-endpoints');
  if (!box) return;
  const want = q.toLowerCase();
  let shown = 0;
  box.querySelectorAll('.ab-ep-grp').forEach((grp) => {
    let n = 0;
    grp.querySelectorAll('tbody tr').forEach((tr) => {
      const on = !want || tr.dataset.abEpText.includes(want);
      tr.hidden = !on;
      if (on) n += 1;
    });
    grp.querySelector('[data-ab-ep-n]').textContent = n;
    grp.hidden = n === 0;
    shown += n;
  });
  const total = box.querySelectorAll('tbody tr').length;
  const c = box.querySelector('[data-ab-ep-count]');
  if (c) c.textContent = want || shown !== total ? `${shown} shown` : '';
}

document.addEventListener('input', (e) => {
  if (!e.target.matches || !e.target.matches('[data-ab-ep-q]') || !state.asBuilt) return;
  state.asBuilt.ep.q = e.target.value;
  abEpFilterRows(e.target.value);
});

document.addEventListener('focusin', (e) => {
  if (state.asBuilt && e.target.matches && e.target.matches('[data-ab-ep-q]')) state.asBuilt.epFocus = true;
});
document.addEventListener('focusout', (e) => {
  // a blur the redraw itself causes is not the reader leaving the box
  if (state.asBuilt && e.target.matches && e.target.matches('[data-ab-ep-q]') && e.target.isConnected) state.asBuilt.epFocus = false;
});

document.addEventListener('change', (e) => {
  if (!e.target.matches || !state.asBuilt || !state.asBuilt.ep) return;
  if (e.target.matches('[data-ab-ep-cap]')) {
    state.asBuilt.ep.cap = e.target.value;
    render();
  } else if (e.target.matches('input[data-ab-ep="untested"]')) {
    state.asBuilt.ep.untested = e.target.checked;
    render();
  }
});


/* Where a subsystem runs, and what it talks to there -- the Architecture
   tab's answer, one subsystem at a time. */
function abWhereItRuns(g, s) {
  const a = g.architecture;
  if (!a) return '';
  const run = a.runtimes.find((r) => r.subsystems.includes(s.key));
  const talks = a.outside.filter((o) => !o.tooling && o.subsystems.includes(s.key));
  const tool = a.tooling.includes(s.key);
  return `<section class="db-sec">
    <h3 class="db-h">${icon('l-play')} Where it runs</h3>
    <p class="ab-where">${run ? `Runs in <a class="ab-link" href="${abHref('architecture', '')}"><b>${esc(run.name)}</b></a>${run.what ? ` — ${esc(run.what)}` : ''}.`
      : tool ? 'Build and tooling — nothing of it runs.' : 'The account did not place it.'}</p>
    ${talks.length ? `<p class="ab-chips">${talks.map((o) => `<a class="ab-chip" href="${abHref('architecture', '')}" title="${esc(o.what || '')}">${esc(o.name)}</a>`).join('')}</p>` : ''}
  </section>`;
}

/* ---- a subsystem by its folders ------------------------------------------

   Past a few dozen files, one box a file is taller than a screen and a
   tangle of lines: 42 files and 81 links, on one subsystem. So a large
   subsystem is drawn by its folders -- the grouping its
   authors already chose -- with arrows counting the imports that cross. The
   files nothing here imports are drawn alone at the top: that is where to
   start reading. Files used right across the subsystem sit in a strip rather
   than drawing a line from every user. A folder opens into its files; a file
   pointed at draws its own links, to the file where its folder is open and to
   the folder where it is not; a file selected opens every folder its
   neighbours are in. A small subsystem, or one whose folders do not divide
   it, keeps one box a file. */

const AB_FOLDERS_FROM = 16;      // files in a subsystem before it is drawn by folder
const AB_FOLDER_MIN = 3;         // a folder with fewer is folded in with the other small ones
const AB_SHARED_BY = 5;          // imported by this many, from more than one folder: shared
const AB_OTHER = '(other folders)';

function abFolderModel(g, s) {
  if (s.files.length < AB_FOLDERS_FROM) return null;
  const files = new Map(g.files.map((f) => [f.path, f]));
  const mine = new Set(s.files);
  const dir = (p) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');
  const inside = g.edges.filter((e) => mine.has(e.source) && mine.has(e.target) && e.source !== e.target);
  const importers = new Map();
  inside.forEach((e) => {
    if (!importers.has(e.target)) importers.set(e.target, new Set());
    importers.get(e.target).add(e.source);
  });
  const uses = new Set(inside.map((e) => e.source));
  // Where to start reading: what nothing here imports and something outside
  // calls -- a screen, a route, a command. Failing any, what nothing here
  // imports, but never a package's own `__init__` or `index`, which is its
  // folder speaking, not a start.
  const declares = new Set(g.ways_in.filter((w) => w.kind !== 'export').map((w) => w.file));
  const unimported = s.files.filter((p) => !importers.has(p) && uses.has(p)
    && !/^(__init__|index|mod)\.[a-z]+$/.test(p.split('/').pop()));
  const called = unimported.filter((p) => declares.has(p));
  const entries = (called.length ? called : unimported).sort((a, b) => files.get(b).lines - files.get(a).lines);
  const isEntry = new Set(entries);
  const shared = s.files.filter((p) => !isEntry.has(p) && (importers.get(p) || new Set()).size >= AB_SHARED_BY
    && new Set([...importers.get(p)].map(dir)).size > 1);
  const isShared = new Set(shared);
  const rest = s.files.filter((p) => !isEntry.has(p) && !isShared.has(p));
  const perDir = new Map();
  rest.forEach((p) => perDir.set(dir(p), (perDir.get(dir(p)) || 0) + 1));
  const groupOf = new Map();
  rest.forEach((p) => groupOf.set(p, perDir.get(dir(p)) >= AB_FOLDER_MIN ? dir(p) : AB_OTHER));
  entries.forEach((p) => groupOf.set(p, `entry:${p}`));
  const groups = new Map();
  [...entries, ...rest].forEach((p) => {
    const k = groupOf.get(p);
    if (!groups.has(k)) groups.set(k, { files: [], lines: 0, dirs: new Set() });
    const grp = groups.get(k);
    grp.files.push(p);
    grp.lines += files.get(p).lines;
    grp.dirs.add(dir(p));
  });
  const folders = [...groups.keys()].filter((k) => !k.startsWith('entry:'));
  if (folders.length < 2) return null;           // one folder is no division: one box a file
  const links = new Map();
  const within = new Map();
  const fileLinks = [];
  inside.forEach((e) => {
    if (isShared.has(e.source) || isShared.has(e.target)) return;
    fileLinks.push(e);
    const a = groupOf.get(e.source); const b = groupOf.get(e.target);
    if (a === b) { within.set(a, (within.get(a) || 0) + 1); return; }
    const k = `${a}\u0000${b}`;
    const l = links.get(k) || { a, b, n: 0, shaky: false };
    l.n += 1; l.shaky = l.shaky || !e.confirmed;
    links.set(k, l);
  });
  const external = new Map();
  const fileOut = new Map();
  g.edges.forEach((e) => {
    const inA = mine.has(e.source); const inB = mine.has(e.target);
    if (inA === inB) return;
    const other = (files.get(inA ? e.target : e.source) || {}).subsystem;
    if (!other || other === s.key) return;
    const x = external.get(other) || { in: 0, out: 0 };
    x[inA ? 'out' : 'in'] += 1;
    external.set(other, x);
    const p = inA ? e.source : e.target;
    if (!fileOut.has(p)) fileOut.set(p, []);
    fileOut.get(p).push({ sub: other, dir: inA ? 'out' : 'in' });
  });
  return { files, entries, shared, importers, groups, groupOf, links: [...links.values()], within, fileLinks,
    external, fileOut };
}

/* What the reader has opened and selected on this subsystem's map. Pointing
   is not kept here: it redraws the map alone, never the page. */
function abFolderState(s) {
  const a = state.asBuilt;
  if (!a.fm || a.fm.key !== s.key) a.fm = { key: s.key, open: new Set(), focus: null, hover: null };
  return a.fm;
}

/* A name cut to fit a box, from the middle so its start and its extension
   both survive: `migrate_github_to_…identities.py`. */
function abFit(text, chars) {
  const t = String(text || '');
  if (t.length <= chars) return t;
  const keep = Math.max(4, chars - 1);
  const tail = Math.min(Math.ceil(keep * 0.45), 18);
  return `${t.slice(0, keep - tail)}…${t.slice(-tail)}`;
}

function abFolderLabel(m, k) {
  if (k.startsWith('entry:')) return k.slice(6).split('/').pop();
  if (k === AB_OTHER) {
    const dirs = [...m.groups.get(k).dirs].map((d) => d.split('/').pop() || '(root)');
    return [...new Set(dirs)].sort().join(' · ');
  }
  const all = [...m.groups.keys()].filter((x) => !x.startsWith('entry:') && x !== AB_OTHER);
  // the folders' own names, less what they all share
  let common = all.length ? all[0].split('/') : [];
  all.forEach((d) => { const p = d.split('/'); let i = 0; while (i < common.length && common[i] === p[i]) i += 1; common = common.slice(0, i); });
  const tail = k.split('/').slice(common.length).join('/');
  return tail || k.split('/').pop() || k;
}

function abFolderNeighbours(m, path) {
  return {
    out: [...new Set(m.fileLinks.filter((e) => e.source === path).map((e) => e.target))],
    in: [...new Set(m.fileLinks.filter((e) => e.target === path).map((e) => e.source))],
    outside: m.fileOut.get(path) || [],
  };
}

function abFolderSay(m, fs) {
  const who = fs.focus || fs.hover;
  if (who) {
    const nb = abFolderNeighbours(m, who);
    return `<b class="mono">${esc(who.split('/').pop())}</b> uses ${nb.out.length} here, is used by ${nb.in.length}${
      nb.outside.length ? `, and links to ${abPlural(new Set(nb.outside.map((x) => x.sub)).size, 'other subsystem')}` : ''}
      · <a class="ab-link" href="${abHref('file', who)}">open it →</a>${fs.focus ? ' · <span class="dim">select it again to let go</span>' : ''}`;
  }
  const folders = [...m.groups.keys()].filter((k) => !k.startsWith('entry:')).length;
  return `${abPlural(folders, 'folder')} · ${abPlural(m.entries.length, 'file')} nothing here imports · ${
    abPlural(m.shared.length, 'shared file')} · point at a file to see its own links`;
}

function abFolderSvg(g, s, m, fs) {
  const subs = new Map(g.subsystems.map((x) => [x.key, x]));
  const W = 1080; const BW = 300; const GAP = 36; const COLW = 230;
  const areaW = W - COLW - 40;
  const lines = (p) => m.files.get(p).lines;
  const leaf = (p) => p.split('/').pop();

  // rows: the starting files, then each folder as deep as the heaviest chain of imports into it
  const keys = [...m.groups.keys()];
  const depth = new Map(keys.map((k) => [k, k.startsWith('entry:') ? 0 : null]));
  const heavy = [...m.links].sort((x, y) => y.n - x.n);
  const reaches = (from, to, seen = new Set()) => from === to
    || (!seen.has(from) && (seen.add(from), heavy.some((l) => l.a === from && reaches(l.b, to, seen))));
  for (let pass = 0; pass < keys.length; pass += 1) {
    heavy.forEach(({ a, b }) => {
      if (depth.get(a) == null || b.startsWith('entry:')) return;
      const want = depth.get(a) + 1;
      if (depth.get(b) == null || (want > depth.get(b) && want <= keys.length && !reaches(b, a))) depth.set(b, want);
    });
  }
  keys.forEach((k) => { if (depth.get(k) == null) depth.set(k, 1); });
  const rows = [];
  keys.forEach((k) => { (rows[depth.get(k)] = rows[depth.get(k)] || []).push(k); });

  const pos = new Map();
  let y = 40;
  rows.filter(Boolean).forEach((row) => {
    row.sort((a, b) => m.groups.get(b).lines - m.groups.get(a).lines);
    const boxes = row.map((k) => {
      const opened = fs.open.has(k) && !k.startsWith('entry:');
      const n = m.groups.get(k).files.length;
      return { k, w: opened ? Math.min(areaW, 2 * BW + 40) : BW, h: opened ? 64 + Math.ceil(n / 2) * 28 + 8 : 62 };
    });
    const wrapped = [[]];
    boxes.forEach((b) => {
      const line = wrapped[wrapped.length - 1];
      if (line.length && line.reduce((a, c) => a + c.w + GAP, 0) + b.w > areaW) wrapped.push([b]); else line.push(b);
    });
    wrapped.forEach((line) => {
      const total = line.reduce((a, b) => a + b.w, 0) + GAP * (line.length - 1);
      let x = 20 + Math.max(0, (areaW - total) / 2);
      line.forEach((b) => { pos.set(b.k, { x, y, w: b.w, h: b.h }); x += b.w + GAP; });
      y += Math.max(...line.map((b) => b.h)) + 44;
    });
    y += 14;
  });
  const sharedY = y;
  const exts = [...m.external].sort((a, b) => (b[1].in + b[1].out) - (a[1].in + a[1].out));
  const H = Math.max(sharedY + (m.shared.length ? 76 : 10), 40 + exts.length * 62 + 10);

  const who = fs.focus || fs.hover;
  const nb = who ? abFolderNeighbours(m, who) : { out: [], in: [], outside: [] };
  const near = new Set([...nb.out, ...nb.in]);
  const nearSubs = new Set(nb.outside.map((x) => x.sub));
  const fpos = new Map();
  const back = [];
  const front = [];

  // other subsystems, down the side
  const extPos = new Map();
  back.push(`<text x="${W - COLW + 10}" y="24" class="t-col">other subsystems</text>`);
  exts.forEach(([key, x], i) => {
    const sub = subs.get(key) || { name: key, code: '' };
    const ey = 40 + i * 62;
    extPos.set(key, { x: W - COLW + 10, y: ey, w: COLW - 20, h: 52 });
    front.push(`<g class="ab-fm-ext${nearSubs.has(key) ? ' near' : ''}" ${abGo('subsystem', key)} tabindex="0" role="button"
      aria-label="${esc(sub.code)} ${esc(sub.name)}"><title>${esc(sub.code)} · ${esc(sub.name)}</title>
      <rect x="${W - COLW + 10}" y="${ey}" width="${COLW - 20}" height="52" rx="6"/>
      <g class="pill" transform="translate(${W - COLW + 22},${ey + 10})"><rect width="38" height="18" rx="9"/><text x="19" y="12.5" text-anchor="middle">${esc(sub.code)}</text></g>
      <text x="${W - COLW + 68}" y="${ey + 23}" class="t-how">${x.out ? `uses ${x.out}` : ''}${x.out && x.in ? ' · ' : ''}${x.in ? `used by ${x.in}` : ''}</text>
      <text x="${W - COLW + 22}" y="${ey + 44}" class="t-how">${esc(sub.name.length > 32 ? `${sub.name.slice(0, 31)}…` : sub.name)}</text></g>`);
  });

  // arrows between boxes, counting the imports that cross
  m.links.forEach(({ a, b, n, shaky }) => {
    const p = pos.get(a); const q = pos.get(b);
    if (!p || !q) return;
    const up = q.y <= p.y;
    const x1 = p.x + p.w / 2 + (up ? -p.w / 3 : 0); const y1 = up ? p.y : p.y + p.h;
    const x2 = q.x + q.w / 2 + (up ? q.w / 3 : 0); const y2 = up ? q.y + q.h : q.y;
    const mid = (y1 + y2) / 2;
    back.push(`<path class="e${shaky ? ' shaky' : ''}" style="stroke-width:${(1.2 + Math.min(3.5, Math.log2(n + 1))).toFixed(1)}"
      d="M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2 + (up ? 6 : -6)}" marker-end="url(#ab-fm-ah)"/>
      <g class="n"><rect x="${(x1 + x2) / 2 - 11}" y="${mid - 9}" width="22" height="16" rx="8"/><text x="${(x1 + x2) / 2}" y="${mid + 3}" text-anchor="middle">${n}</text></g>`);
  });

  // the boxes: a starting file, a folded folder, an open one with its files
  pos.forEach((p, k) => {
    const grp = m.groups.get(k);
    const entry = k.startsWith('entry:');
    const opened = fs.open.has(k) && !entry;
    const inner = m.within.get(k) || 0;
    if (entry) fpos.set(grp.files[0], p);
    const mark = entry && grp.files[0] === who ? ' me' : (entry && near.has(grp.files[0])) || (who && !opened && grp.files.some((f) => near.has(f))) ? ' near' : '';
    const ways = entry ? g.ways_in.filter((w) => w.file === grp.files[0] && w.kind !== 'export').length : 0;
    front.push(`<g class="ab-fm-grp${entry ? ' entry' : ''}${mark}" data-fm-k="${esc(k)}" tabindex="0" role="button"
      aria-label="${esc(abFolderLabel(m, k))}"><title>${esc(entry ? grp.files[0] : [...grp.dirs].join('\n'))}</title>
      <rect class="box" x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="6"/>
      ${entry ? '' : `<text x="${p.x + 14}" y="${p.y + 26}" class="twist">${opened ? '▾' : '▸'}</text>`}
      <text x="${p.x + (entry ? 16 : 30)}" y="${p.y + 26}" class="${entry ? 't-file' : 't-name'}">${esc(abFit(abFolderLabel(m, k),
        Math.floor((p.w - (entry ? 32 : 44)) / (entry ? 7.9 : 8.2))))}</text>
      <text x="${p.x + 16}" y="${p.y + 46}" class="t-how">${entry
        ? esc(abFit([grp.files[0].includes('/') ? `${grp.files[0].slice(0, grp.files[0].lastIndexOf('/')).split('/').slice(-2).join('/')}/` : '',
          `${grp.lines.toLocaleString()} lines`, ways ? abPlural(ways, 'way in', 'ways in') : ''].filter(Boolean).join(' · '),
          Math.floor((p.w - 32) / 6.4)))
        : `${grp.files.length} files · ${grp.lines.toLocaleString()} lines${inner ? ` · ${abPlural(inner, 'link')} inside` : ''}`}</text></g>`);
    if (!opened) return;
    [...grp.files].sort((a, b) => lines(b) - lines(a)).forEach((f, i) => {
      const cw = (p.w - 28) / 2 - 8;
      const cx = p.x + 14 + (i % 2) * ((p.w - 28) / 2); const cy = p.y + 60 + Math.floor(i / 2) * 28;
      fpos.set(f, { x: cx, y: cy, w: cw, h: 22 });
      front.push(`<g class="ab-fm-file${f === who ? ' me' : near.has(f) ? ' near' : ''}${m.files.get(f).generated ? ' gen' : ''}" data-fm-p="${esc(f)}"
        tabindex="0" role="button" aria-label="${esc(f)}"><title>${esc(f)}</title>
        <rect x="${cx}" y="${cy}" width="${cw}" height="22" rx="3"/>
        <text x="${cx + 8}" y="${cy + 15}" class="t-file">${esc(abFit(leaf(f), Math.floor((cw - 52) / 6.9)))}</text>
        <text x="${cx + cw - 8}" y="${cy + 15}" class="t-how" text-anchor="end">${lines(f)}</text></g>`);
    });
  });

  // the file in focus: its own links, to the file where it shows, to its folder where not
  if (who && fpos.has(who)) {
    const me = fpos.get(who);
    const hot = [];
    const line = (from, to, cls) => {
      const x1 = from.x + from.w / 2; const y1 = from.y + from.h / 2; const x2 = to.x + (to.side ? 0 : to.w / 2); const y2 = to.y + to.h / 2;
      const bend = Math.abs(y2 - y1) < 4 ? 40 : 0;
      hot.push(`<path class="hot ${cls}" d="M${x1},${y1} C${x1},${(y1 + y2) / 2 - bend} ${x2},${(y1 + y2) / 2 - bend} ${x2},${y2}"/>`);
    };
    const folded = new Map();
    [['out', nb.out], ['in', nb.in]].forEach(([dir, list]) => list.forEach((n) => {
      if (fpos.has(n)) { line(dir === 'out' ? me : fpos.get(n), dir === 'out' ? fpos.get(n) : me, dir); return; }
      const k = m.groupOf.get(n);
      if (!k || !pos.has(k)) return;
      const f = folded.get(k) || { n: 0, dir };
      f.n += 1;
      folded.set(k, f);
    }));
    folded.forEach((f, k) => {
      const b = pos.get(k);
      line(f.dir === 'out' ? me : b, f.dir === 'out' ? b : me, f.dir);
      hot.push(`<g class="badge"><rect x="${b.x + b.w - 64}" y="${b.y - 10}" width="58" height="18" rx="9"/>
        <text x="${b.x + b.w - 35}" y="${b.y + 3}" text-anchor="middle">${abPlural(f.n, 'file')}</text></g>`);
    });
    nearSubs.forEach((key) => {
      const e = extPos.get(key);
      if (e) line(me, { ...e, side: true }, nb.outside.some((x) => x.sub === key && x.dir === 'out') ? 'out ext' : 'in ext');
    });
    // drawn over the lines: the file in focus and the files beside it
    fpos.forEach((q, path) => {
      if ((path !== who && !near.has(path)) || m.groupOf.get(path).startsWith('entry:')) return;
      hot.push(`<g class="ab-fm-file ${path === who ? 'me' : 'near'}" data-fm-p="${esc(path)}"><rect x="${q.x}" y="${q.y}" width="${q.w}" height="22" rx="3"/>
        <text x="${q.x + 8}" y="${q.y + 15}" class="t-file">${esc(abFit(leaf(path), Math.floor((q.w - 52) / 6.9)))}</text></g>`);
    });
    front.push(...hot);
  }

  if (m.shared.length) {
    front.push(`<g class="ab-fm-shared"><rect class="box" x="20" y="${sharedY}" width="${areaW}" height="56" rx="6"/>
      <text x="36" y="${sharedY + 22}" class="t-col">used across this subsystem · not drawn as lines</text>
      ${m.shared.map((p, i) => `<a href="${abHref('file', p)}"><text x="${36 + i * 260}" y="${sharedY + 43}" class="t-file">${esc(leaf(p))}
        <tspan class="t-how"> · ${(m.importers.get(p) || new Set()).size} files</tspan></text></a>`).join('')}</g>`);
  }
  return `<svg class="ab-fm${who ? ' focused' : ''}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(s.name)} by folder">
    <defs><marker id="ab-fm-ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse"
      orient="auto-start-reverse"><path class="ah" d="M0,0 L10,5 L0,10 z"/></marker></defs>
    ${back.join('')}${front.join('')}</svg>`;
}

function abFolderMap(g, s, m) {
  const fs = abFolderState(s);
  const folders = [...m.groups.keys()].filter((k) => !k.startsWith('entry:'));
  const all = folders.every((k) => fs.open.has(k));
  return `<div class="ab-fm-bar"><span class="ab-fm-say" id="ab-fm-say">${abFolderSay(m, fs)}</span>
      <button type="button" class="btn btn-sm" data-ab-fm-all="1" aria-pressed="${all ? 'true' : 'false'}">${all ? 'Collapse all' : 'Expand all'}</button></div>
    <div id="ab-fm" data-sub="${esc(s.key)}">${abFolderSvg(g, s, m, fs)}</div>`;
}

/* Pointing redraws the map and the line above it, and nothing else. */
function abFolderRepaint() {
  const a = state.asBuilt;
  const box = document.getElementById('ab-fm');
  if (!a || !a.graph || !box || !a.fm) return;
  const s = a.graph.subsystems.find((x) => x.key === box.dataset.sub);
  const m = s && abFolderModel(a.graph, s);
  if (!m) return;
  box.innerHTML = abFolderSvg(a.graph, s, m, a.fm);
  const say = document.getElementById('ab-fm-say');
  if (say) say.innerHTML = abFolderSay(m, a.fm);
}

function abFolderPress(e) {
  const a = state.asBuilt;
  const box = e.target.closest && e.target.closest('#ab-fm');
  const all = e.target.closest && e.target.closest('[data-ab-fm-all]');
  if (!a || !a.graph || (!box && !all)) return false;
  const key = (box || document.getElementById('ab-fm') || {}).dataset?.sub;
  const s = a.graph.subsystems.find((x) => x.key === key);
  const m = s && abFolderModel(a.graph, s);
  if (!m) return false;
  const fs = abFolderState(s);
  const folders = [...m.groups.keys()].filter((k) => !k.startsWith('entry:'));
  if (all) {
    if (folders.every((k) => fs.open.has(k))) { fs.open.clear(); fs.focus = null; } else folders.forEach((k) => fs.open.add(k));
    render();
    return true;
  }
  const chip = e.target.closest('[data-fm-p]');
  const grp = e.target.closest('[data-fm-k]');
  const path = chip ? chip.dataset.fmP : grp && grp.dataset.fmK.startsWith('entry:') ? grp.dataset.fmK.slice(6) : null;
  if (path) {
    if (fs.focus === path) { fs.focus = null; } else {
      fs.focus = path;
      const nb = abFolderNeighbours(m, path);
      [path, ...nb.out, ...nb.in].forEach((p) => { const k = m.groupOf.get(p); if (k && !k.startsWith('entry:')) fs.open.add(k); });
    }
    fs.hover = null;
    render();
    return true;
  }
  if (grp) {
    const k = grp.dataset.fmK;
    if (fs.open.has(k)) fs.open.delete(k); else fs.open.add(k);
    render();
    return true;
  }
  return false;
}

document.addEventListener('mouseover', (e) => {
  const a = state.asBuilt;
  if (!a || !a.fm || a.fm.focus || !e.target.closest || !e.target.closest('#ab-fm')) return;
  const chip = e.target.closest('[data-fm-p]');
  const grp = e.target.closest('[data-fm-k^="entry:"]');
  const path = chip ? chip.dataset.fmP : grp ? grp.dataset.fmK.slice(6) : null;
  if (path !== a.fm.hover) { a.fm.hover = path; abFolderRepaint(); }
});
document.addEventListener('mouseout', (e) => {
  const a = state.asBuilt;
  if (!a || !a.fm || !a.fm.hover || !e.target.closest || !e.target.closest('#ab-fm')) return;
  if (e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('#ab-fm')) return;
  a.fm.hover = null;
  abFolderRepaint();
});

function abParts(g, f, parts) {
  const pairs = new Map();
  g.part_links.filter((l) => l.file === f.path).forEach((l) => {
    const k = [l.source, l.target].sort().join('\u0000');
    pairs.set(k, (pairs.get(k) || 0) + l.calls);
  });
  const cols = Math.min(3, parts.length);
  const w = 250; const h = 70; const gx = 44; const gy = 46;
  const pos = new Map(parts.map((p, i) => [p.key, { x: 12 + (i % cols) * (w + gx), y: 12 + Math.floor(i / cols) * (h + gy) }]));
  const width = cols * (w + gx) - gx + 24;
  const height = Math.ceil(parts.length / cols) * (h + gy) - gy + 24;
  const links = [...pairs].filter(([, n]) => n >= 2).map(([k, n]) => {
    const [a, b] = k.split('\u0000');
    const pa = pos.get(a); const pb = pos.get(b);
    if (!pa || !pb) return '';
    const x1 = pa.x + w / 2; const y1 = pa.y + h / 2; const x2 = pb.x + w / 2; const y2 = pb.y + h / 2;
    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="ab-edge" stroke-width="${Math.min(5, 1 + n / 4).toFixed(1)}"></line>
      <g class="ab-count"><rect x="${(x1 + x2) / 2 - 12}" y="${(y1 + y2) / 2 - 9}" width="24" height="17" rx="2"></rect>
        <text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 + 4}" text-anchor="middle">${n}</text></g>`;
  }).join('');
  const boxes = parts.map((p) => {
    const { x, y } = pos.get(p.key);
    const name = (p.name || p.key).length > 32 ? `${(p.name || p.key).slice(0, 31)}…` : (p.name || p.key);
    return `<g class="ab-node static"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="2"></rect>
      <text x="${x + 12}" y="${y + 24}" class="ab-nt">${esc(name)}</text>
      <text x="${x + 12}" y="${y + 43}" class="ab-ns">${abPlural(p.functions.length, 'function')} · ${p.lines.toLocaleString()} lines</text>
      <text x="${x + 12}" y="${y + 59}" class="ab-ns dim">largest: ${esc(p.key)}</text></g>`;
  }).join('');
  const inside = parts.reduce((a, p) => a + p.lines, 0);
  return `<h3 class="db-h">${icon('l-scissors')} Its parts <span class="db-count">${parts.length}</span></h3>
    <p class="dim small">Groups of its functions that call each other more than they call the rest of the file.
      ${parts.length} parts hold ${inside.toLocaleString()} of its ${f.lines.toLocaleString()} lines.</p>
    <div class="card ab-map"><div class="ab-scroll"><svg viewBox="0 0 ${width} ${height}" style="width:100%;max-width:${width}px;height:auto"
      role="img" aria-label="The parts of ${esc(f.path)}">${links}${boxes}</svg></div>
      <p class="ab-legend"><i class="ab-sw"></i>calls between two parts, both ways · pairs with one call not drawn</p></div>
    <table class="ab-table"><thead><tr><th>part</th><th class="num">lines</th><th class="num">functions</th><th>largest members</th>
      <th class="num">calls out · in</th></tr></thead><tbody>${parts.map((p) => `<tr><td><b>${esc(p.name || p.key)}</b><br>
        <span class="dim small">${esc(p.summary)}</span></td><td class="num">${p.lines.toLocaleString()}</td>
        <td class="num">${p.functions.length}</td><td class="mono small">${esc(p.functions.slice(0, 5).join(', '))}</td>
        <td class="num">${p.calls_out} · ${p.calls_in}</td></tr>`).join('')}</tbody></table>`;
}

/* ---- one capability ------------------------------------------------------ */

/* A path as the subsystem page shows one: the folder quiet, the name loud. */
/* A directory that may break after any of its slashes -- never inside a name. */
const abBreakable = (dir) => esc(dir).replace(/\//g, '/<wbr>');

function abPath(p, href = abHref('file', p)) {
  const cut = p.lastIndexOf('/');
  return `<a class="ab-fname" href="${href}"><span class="dir">${abBreakable(cut === -1 ? '' : p.slice(0, cut + 1))}</span><b>${
    esc(p.slice(cut + 1))}</b></a>`;
}

function abCapability(g, c) {
  const number = c.code;
  const ways = new Map(g.ways_in.map((w) => [w.id, w]));
  const subNo = new Map(g.subsystems.map((x) => [x.key, x.code]));
  const subName = new Map(g.subsystems.map((x) => [x.key, x.name]));
  const partName = new Map(g.parts.map((p) => [`${p.file}#${p.key}`, p.name || p.key]));
  const mine = c.ways_in.map((id) => ways.get(id) || { id, name: id, callers: [], tested_by: [] });
  // Who calls it is the product's own code. A test calling it is a test of
  // it; a note that names the route is not calling it at all.
  const prose = /\.(md|mdx|markdown|txt|rst|adoc)$/i;
  const kindOf = new Map(g.files.map((f) => [f.path, prose.test(f.path) ? 'docs' : f.is_test ? 'test' : (f.kind || 'code')]));
  const everyCaller = [...new Set(mine.flatMap((w) => w.callers || []))];
  const callers = everyCaller.filter((p) => ['code', 'config'].includes(kindOf.get(p) || 'code'));
  const testCallers = everyCaller.filter((p) => kindOf.get(p) === 'test');
  const namedIn = everyCaller.filter((p) => !['code', 'config', 'test'].includes(kindOf.get(p) || 'code'));
  const bySub = (items) => {
    const acc = new Map();
    items.forEach((r) => acc.set(r.subsystem || '', (acc.get(r.subsystem || '') || 0) + r.lines));
    return [...acc].sort((a, b) => b[1] - a[1]);
  };
  const runs = bySub(c.direct);
  const moves = bySub(c.in_motion);
  const directLines = c.direct.reduce((a, r) => a + r.lines, 0);
  const motionLines = c.in_motion.reduce((a, r) => a + r.lines, 0);
  const codeLines = g.files.filter((f) => !f.is_test && (f.kind || 'code') === 'code').reduce((a, f) => a + f.lines, 0) || 1;
  const noCaller = mine.filter((w) => w.kind === 'route' && !(w.callers || []).length).length;

  const plate = abPlate([
    ['ways in', `${mine.length}`, '', `${noCaller ? `${noCaller} with no caller found` : 'every one called'}`],
    ['tested', `${c.tested} of ${mine.length}`, abSegments(c.tested, mine.length), 'ways in a test reaches'],
    ['subsystems', `${new Set([...runs, ...moves].map(([k]) => k).filter(Boolean)).size}`, '',
      runs.filter(([k]) => k).map(([k]) => `<span class="ab-num sm">${subNo.get(k) || '·'}</span>`).join(' ') || 'none'],
    ['runs', `${directLines.toLocaleString()} lines`, abMeter(directLines, codeLines), 'what its handlers call directly'],
    ['sets in motion', `${motionLines.toLocaleString()} lines`, abMeter(motionLines, codeLines), 'one call further on'],
  ]);

  const subBox = ([key, lines]) => (key
    ? `<a class="ab-cx-box" href="${abHref('subsystem', key)}"><span class="ab-num sm">${subNo.get(key) || ''}</span>
        <span>${esc(subName.get(key) || key)}</span><span class="ab-cx-n mono">${lines.toLocaleString()} lines</span></a>`
    : `<span class="ab-cx-box ab-cx-quiet"><span class="ab-num sm ab-shared">·</span><span>Shared files</span>
        <span class="ab-cx-n mono">${lines.toLocaleString()} lines</span></span>`);
  const fileBox = (p) => `<a class="ab-cx-box ab-cx-file" href="${abHref('file', p)}" title="${esc(p)}">
      <span class="mono ab-cx-fname">${esc(abFit(p.split('/').pop(), 34))}</span>
      <span class="ab-cx-dir mono">${esc(abFit(p.slice(0, Math.max(0, p.lastIndexOf('/'))), 44))}</span></a>`;
  const flow = `
    <div class="ab-cx ab-flow">
      <div class="ab-cx-col"><p class="ab-cx-l">called from</p>
        ${callers.length ? callers.slice(0, 6).map(fileBox).join('') + (callers.length > 6 ? `<p class="dim small">and ${callers.length - 6} more</p>` : '')
          : '<p class="dim small">Nothing in this repository calls it.</p>'}
        ${testCallers.length || namedIn.length ? `<p class="dim small ab-cx-also">${[
          testCallers.length ? `and ${abPlural(testCallers.length, 'test')}` : '',
          namedIn.length ? `<span title="${esc(namedIn.join('\n'))}">also named in ${abPlural(namedIn.length, 'document')}</span>` : '']
          .filter(Boolean).join(' · ')}</p>` : ''}</div>
      <div class="ab-cx-arrow" aria-hidden="true">→</div>
      <div class="ab-cx-here"><span class="ab-sq">${esc(number)}</span><b>${esc(c.name)}</b></div>
      <div class="ab-cx-arrow" aria-hidden="true">→</div>
      <div class="ab-cx-col"><p class="ab-cx-l">runs</p>
        ${runs.length ? runs.map(subBox).join('') : '<p class="dim small">No handler was found to follow.</p>'}
        ${moves.length ? `<p class="ab-cx-l ab-cx-then">then sets in motion</p>${moves.map(subBox).join('')}` : ''}</div>
    </div>`;

  const waysRows = mine.map((w) => `<tr>
      <td class="mono ab-way">${w.method ? `<span class="ab-verb">${esc(w.method)}</span>` : `<span class="ab-verb quiet">${esc(w.kind || '')}</span>`}${abBreakable(w.name || w.id)}</td>
      <td>${w.file ? abPath(w.file) : ''}${w.handler ? `<span class="ab-handler mono">${esc(w.handler)}</span>` : ''}</td>
      <td>${(w.callers || []).filter((p) => ['code', 'config'].includes(kindOf.get(p) || 'code')).length
        ? w.callers.filter((p) => ['code', 'config'].includes(kindOf.get(p) || 'code')).slice(0, 3).map((p) => abPath(p)).join('<br>')
        : w.kind === 'route' ? '<span class="ab-warn">no caller found</span>' : '<span class="dim">a person</span>'}</td>
      <td class="num">${(w.tested_by || []).length ? `<span class="ab-yes" title="${esc(w.tested_by.join(', '))}">●</span>` : '<span class="dim">—</span>'}</td></tr>`).join('');

  const reach = (items, empty) => items.length ? `<table class="ab-table ab-files"><thead><tr><th>where</th><th>subsystem</th>
      <th class="num">lines</th></tr></thead>
    <tbody>${items.slice(0, 30).map((r) => `<tr><td>${abPath(r.file)}${r.part
      ? `<span class="ab-handler">${esc(partName.get(`${r.file}#${r.part}`) || r.part)}</span>` : ''}</td>
      <td>${r.subsystem ? `<a class="ab-link" href="${abHref('subsystem', r.subsystem)}"><span class="ab-num sm">${subNo.get(r.subsystem) || ''}</span>
        ${esc(subName.get(r.subsystem) || '')}</a>` : '<span class="dim">shared</span>'}</td>
      <td class="num">${r.lines.toLocaleString()}</td></tr>`).join('')}</tbody></table>`
    : `<p class="db-empty">${empty}</p>`;

  return `
    <div class="ab-subhead"><span class="ab-sq">${esc(number)}</span>
      <div><h3 class="ab-title">${esc(c.name)} <span class="pill">${esc(c.area || 'Other')}</span></h3>
        <p class="ab-summary">${esc(c.summary)}</p></div></div>
    ${plate}
    <section class="db-sec">
      <h3 class="db-h">${icon('l-arrow-left-right')} How it flows</h3>
      ${flow}
    </section>
    <section class="db-sec">
      <h3 class="db-h">${icon('l-zap')} Ways in <span class="db-count">${mine.length}</span></h3>
      <table class="ab-table ab-files"><thead><tr><th>way in</th><th>handled in</th><th>called from</th><th class="num">tested</th></tr></thead>
        <tbody>${waysRows}</tbody></table>
    </section>
    <div class="ab-two">
      <section class="db-sec">
        <h3 class="db-h">${icon('l-layers')} What it runs <span class="db-count">${directLines.toLocaleString()} lines</span></h3>
        <p class="dim small">Its handlers, and what they call directly.</p>
        ${reach(c.direct, 'No handler was found to follow.')}
      </section>
      <section class="db-sec">
        <h3 class="db-h">${icon('l-share')} What it sets in motion <span class="db-count">${motionLines.toLocaleString()} lines</span></h3>
        <p class="dim small">One call further on — where the work it starts goes.</p>
        ${reach(c.in_motion, 'Nothing further.')}
      </section>
    </div>`;
}

/* ---- presses ------------------------------------------------------------- */

document.addEventListener('click', async (e) => {
  const go = e.target.closest('[data-ab-go]');
  if (go && state.asBuilt) {
    e.preventDefault();
    location.hash = abHref(go.dataset.abGo, go.dataset.abKey || '').slice(1);
    return;
  }
  if (abFolderPress(e)) {
    e.preventDefault();
    return;
  }
  const epc = e.target.closest('button[data-ab-ep]');
  if (epc && state.asBuilt && state.asBuilt.ep) {
    e.preventDefault();
    const ep = state.asBuilt.ep;
    const { abEp: field, v } = epc.dataset;
    if (field === 'sort') {
      ep.dir = ep.sort === v ? -ep.dir : 1;
      ep.sort = v;
    } else if (field === 'clear') {
      Object.assign(ep, { q: '', kind: '', caller: '', cap: '', untested: false });
    } else {
      ep[field] = ep[field] === v ? '' : v;
    }
    render();
    return;
  }
  const stale = e.target.closest('[data-ab-stale]');
  if (stale && state.asBuilt) {
    e.preventDefault();
    state.asBuilt.showChanged = !state.asBuilt.showChanged;
    render();
    return;
  }
  const jump = e.target.closest('[data-ab-scroll]');
  if (jump && state.asBuilt) {
    e.preventDefault();
    const to = document.getElementById(jump.dataset.abScroll);
    if (to) {
      to.scrollIntoView({ block: 'center', behavior: 'smooth' });
      to.classList.remove('ab-flash');
      void to.offsetWidth;
      to.classList.add('ab-flash');
    }
    return;
  }
  const refresh = e.target.closest('[data-ab-refresh]');
  if (refresh && state.asBuilt) {
    e.preventDefault();
    const pid = state.asBuilt.project;
    try {
      await api(`/api/projects/${encodeURIComponent(pid)}/as-built/refresh`, { method: 'POST' });
      if (state.asBuilt.status) state.asBuilt.status.running = { phase: 'starting', detail: '' };
      render();
    } catch (err) {
      errorToast(err.message);
    }
  }
});

document.addEventListener('keydown', (e) => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('g[data-ab-go], g[data-ab-scroll]')) {
    e.preventDefault();
    e.target.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  }
});

/* ---- the crew screen ----------------------------------------------------- */

/* Which of the as-built's agents is working, on what, right now. The crew
   screen draws the as-built as a row of its own; these light the agent that
   holds the reading and say, under the row, what it is doing. */
function asBuiltRuns(roles) {
  return ((state.roles || {}).as_built_running) || [];
}

function asBuiltWorking(role) {
  return asBuiltRuns().some((r) => r.role === role || (r.inflight || {})[role]);
}

function asBuiltRunLine(r) {
  const where = r.feature ? `${r.project}, feature ${r.feature} (${r.label === 'base' ? 'where it started' : 'its head'})`
    : r.project;
  const flying = (role) => {
    const n = (r.inflight || {})[role] || 0;
    return n ? `${n} call${n === 1 ? '' : 's'} in flight` : '';
  };
  const doing = {
    starting: 'starting',
    listing: 'code is listing the files at the commit',
    reading: ['reader', flying('reader'), r.todo ? `${r.done || 0} of ${r.todo} files read` : ''].filter(Boolean).join(' · '),
    joining: 'code is checking the links and grouping',
    describing: ['cartographer', r.detail, flying('cartographer')].filter(Boolean).join(' · '),
    writing: 'code is writing .fabrika/as-built',
  }[r.phase] || r.phase || 'starting';
  return `${where} — ${doing}`;
}

function asBuiltNow() {
  const runs = asBuiltRuns();
  if (!runs.length) return '';
  return `now: ${runs.map(asBuiltRunLine).join('   ·   ')}`;
}

function asBuiltBandNote(r) {
  const runs = asBuiltRuns();
  const unsplit = (r.as_built_unsplit || {}).calls
    ? ` · ${r.as_built_unsplit.calls} earlier calls not split between the two` : '';
  if (!runs.length) return unsplit ? `<span class="dim">${esc(unsplit.slice(3))}</span>` : '';
  return `<span class="ab-crew-live"><span class="spin" aria-hidden="true"></span>${
    runs.map((x) => esc(asBuiltRunLine(x))).join('<br>')}</span>`;
}

/* While a reading runs, the crew screen follows it: on each progress event,
   and on a short poll for a feature's readings, which report no events. */
let abCrewTimer = null;
function asBuiltCrewSoon(delay = 1500) {
  if (abCrewTimer) return;
  abCrewTimer = setTimeout(async () => {
    abCrewTimer = null;
    if (state.view !== 'roles') return;
    const fresh = await api('/api/roles').catch(() => null);
    if (fresh && state.view === 'roles') {
      state.roles = fresh;
      render();
      if ((fresh.as_built_running || []).length) asBuiltCrewSoon(5000);
    }
  }, delay);
}


/* A new page of the as-built opens at its top, not wherever the last one was
   scrolled to. */
let abLastWhere = '';
window.addEventListener('hashchange', () => {
  if (!location.hash.includes('gates=as-built')) { abLastWhere = ''; return; }
  const now = JSON.stringify(abWhere());
  if (abLastWhere && now !== abLastWhere) {
    requestAnimationFrame(() => {
      const nav = document.querySelector('.ab-nav');
      if (nav) nav.scrollIntoView({ block: 'start' });
    });
  }
  abLastWhere = now;
});

/* Inside a subsystem the links are many, so they are drawn faint and a file
   under the pointer (or the keyboard) brings its own forward: its links drawn
   full, the files at their other ends lit, everything else set back. */
function abFocusFile(node) {
  const svg = node && node.closest('svg.ab-fmap');
  if (!svg) return;
  const key = node.dataset.node;
  svg.classList.add('focusing');
  const near = new Set([key]);
  svg.querySelectorAll('.ab-link-g').forEach((g) => {
    const hot = g.dataset.a === key || g.dataset.b === key;
    g.classList.toggle('hot', hot);
    if (hot) { near.add(g.dataset.a); near.add(g.dataset.b); }
  });
  svg.querySelectorAll('[data-node]').forEach((n) => n.classList.toggle('near', near.has(n.dataset.node)));
}
function abUnfocusFiles(svg) {
  if (!svg) return;
  svg.classList.remove('focusing');
  svg.querySelectorAll('.hot, .near').forEach((n) => n.classList.remove('hot', 'near'));
}
document.addEventListener('mouseover', (e) => {
  const node = e.target.closest && e.target.closest('svg.ab-fmap [data-node]');
  if (node) abFocusFile(node);
});
document.addEventListener('mouseout', (e) => {
  const node = e.target.closest && e.target.closest('svg.ab-fmap [data-node]');
  if (node && !(e.relatedTarget && node.contains(e.relatedTarget))) abUnfocusFiles(node.closest('svg'));
});
document.addEventListener('focusin', (e) => {
  if (e.target.matches && e.target.matches('svg.ab-fmap [data-node]')) abFocusFile(e.target);
});
document.addEventListener('focusout', (e) => {
  if (e.target.matches && e.target.matches('svg.ab-fmap [data-node]')) abUnfocusFiles(e.target.closest('svg'));
});


/* The help that belongs to the page showing, for the `?` key. */
function abHelpHere() {
  const kind = abWhere().kind;
  if (kind === 'architecture') return 'as-built-architecture';
  if (kind === 'capabilities' || kind === 'capability') return 'as-built-capabilities';
  if (['system', 'subsystem', 'file', 'source'].includes(kind)) return 'as-built-system';
  return 'as-built';
}
