/* Outside work: a look at a branch Fabrika did not build, and the packet it
   hands back. The screen is prototypes/branch-packet.html; the rules for
   the code view are DESIGN.md's, The Code View. */

'use strict';

/* -------------------------------------------------------- outside work */

/* The groups of the code view, in reading order. Novel is open; everything
   after it is folded, and says why it was classed the way it was. Tests are a
   group of their own, outside the ramp. */
const BP_GROUPS = [
  ['novel', 'Novel: read these', 'Real choices a person has to read. Open by default.'],
  ['conventional', 'Conventional', 'The obvious way, following what the repository already does.'],
  ['mechanical', 'Mechanical', 'Forced: there was no choice to make. Each says why.'],
  ['generated', 'Generated', "A tool's output: nobody chose these lines one by one."],
  ['tests', 'Tests', 'What the branch added or changed in its tests. Classed by code, not by a model.'],
];

/* The stations a look runs, in order. Attribution only runs when a check is
   red, so a look with every check green never lights it. */
const LOOK_STATIONS = [
  ['reader', 'Reader'], ['gates', 'Gates'], ['attribution', 'Attribution'], ['rapporteur', 'Rapporteur'],
];

/* A look's line in the shape `stationStrip` reads a feature's: every station,
   in order -- attribution among them, lit only when a check was red -- and a
   stage it understands, so a finished look reads as settled and a station
   with nothing to do as skipped rather than waiting. */
function lookLine(s) {
  const has = (name) => (s.phases || []).find((p) => p.name === name);
  const phases = LOOK_STATIONS.map(([name]) => has(name) || { name, status: 'pending', detail: '', error: '' });
  const stage = { reviewing: 'building', awaiting_ruling: 'awaiting_verdict', ruled: 'accepted',
                  failed: 'failed' }[s.stage] || s.stage;
  return { phases, state: { ...s, stage }, packet: null };
}

function branchHref(projectId, slug) {
  return `#/${encodeURIComponent(projectId)}?branch=${encodeURIComponent(slug)}`;
}

const bpPlural = (n, word, many) => `${n} ${n === 1 ? word : (many || `${word}s`)}`;
const bpSha = (s) => esc((s || '').slice(0, 7));

/* The project's tab: the branches there are, as a list. Pick one, say what it
   was for if you like, and look. Those already looked at are listed first and
   open their packet. */
function branchesSection(p) {
  const b = state.branches && state.branches.project === p.project_id ? state.branches : null;
  const lede = `<p class="sec-lede">Work built outside Fabrika -- in any agent, by anyone -- and handed in as a
    branch. Pick one: it is checked out on its own, run through this project's checks and cut into
    pieces to read. Nothing on the branch, and nothing in your checkout, changes.</p>`;
  if (!b) return `<h2 class="sec-title">Outside work</h2>${lede}<p class="empty">Reading the branches…</p>`;
  const day = (at) => (at ? new Date(at).toLocaleDateString([], { day: 'numeric', month: 'short' }) : '');
  const looked = (b.looked || []).map((x) => `
    <li><a class="bp-br" href="${branchHref(p.project_id, x.slug)}">
      <span class="bp-br-name">${esc(x.branch)}</span>
      <span class="bp-br-meta">look ${x.look} · ${esc(lookWord(x.stage))}</span>
      ${x.headline ? `<span class="bp-br-sub">${esc(x.headline)}</span>`
        : x.error ? `<span class="bp-br-sub bad">${esc(x.error)}</span>` : ''}
    </a></li>`).join('');
  const picked = state.lookPick;
  const candidates = (b.candidates || []).map((c) => {
    const on = picked === c.branch;
    return `
    <li class="${on ? 'on' : ''}">
      <button type="button" class="bp-br" data-look-pick="${esc(c.branch)}" aria-expanded="${on}">
        <span class="bp-br-name">${esc(c.branch)}</span>
        <span class="bp-br-meta">${bpPlural(c.ahead, 'commit')} past ${esc(b.base_ref)} · ${esc(day(c.at))}</span>
        ${c.subject ? `<span class="bp-br-sub">${esc(c.subject)}</span>` : ''}
      </button>
      ${on ? `<div class="bp-br-go">
        <input id="look-intent" class="bp-intent" maxlength="600" autocomplete="off"
          placeholder="What was it for? One sentence, optional." aria-label="What ${esc(c.branch)} was for">
        <button type="button" class="btn btn-primary btn-sm" id="look-start">Look at it</button>
        <button type="button" class="btn btn-sm btn-quiet" data-look-cancel>Cancel</button>
      </div>` : ''}
    </li>`;
  }).join('');
  return `
    <h2 class="sec-title">Outside work</h2>${lede}
    ${looked ? `<h3 class="bp-h">Looked at</h3><ul class="bp-branches">${looked}</ul>` : ''}
    <h3 class="bp-h">Branches with work past ${esc(b.base_ref)}</h3>
    ${candidates ? `<ul class="bp-branches">${candidates}</ul>`
      : `<p class="empty">No branch has commits that ${esc(b.base_ref)} does not.</p>`}`;
}

function lookWord(stage) {
  return { reviewing: 'looking', awaiting_ruling: 'packet ready', ruled: 'ruled', failed: 'failed' }[stage] || stage;
}

/* --------------------------------------------------------- the packet */

function branchScreen() {
  const d = state.branchLook;
  const pid = state.projectId;
  if (!d || d.slug !== state.param) return `<div class="wrap"><p class="empty">Loading…</p></div>`;
  const s = d.state;
  const packet = d.packet;
  const look = (d.looks || []).find((l) => l.look === s.look) || {};
  const commits = look.commits || [];
  const authors = [...new Set(commits.map((c) => c.author))];
  const when = commits.length ? new Date(commits[commits.length - 1].at)
    .toLocaleDateString([], { day: 'numeric', month: 'short' }) : '';
  const stat = (packet && packet.stat) || null;
  /* The board a feature's screens are built on: the same header, the same
     station strip and lamps, folding the same way. The look's facts go on the
     paper under it. A plate of its own, and a row of dots for stations, said
     "a line" in words and drew something else. */
  const again = s.stage === 'reviewing' ? '' : `<button type="button" class="btn btn-sm" id="look-again"
    title="Another look at the branch as it is now, kept beside this one. Nothing on the branch changes."
    >Look again</button>`;
  const line = lookLine(s);
  const plate = `
    <header class="board">
      <div class="hall">
        ${boardHead(`<h1 class="ftitle" title="${esc(s.branch)}">${esc(s.branch)}</h1><span class="fsub">outside work · no spec</span>`)}
        <div class="proc-row">
          <p class="proc steps" aria-label="Where this look is">
            <a class="st" href="#/${encodeURIComponent(pid)}">${esc((state.project.project || {}).name || pid)}</a><span class="arr" aria-hidden="true">·</span><a class="st" href="#/${encodeURIComponent(pid)}?gates=branches">outside work</a><span class="arr" aria-hidden="true">·</span><span class="st at">look ${s.look}</span>
          </p>
          ${again ? `<div class="board-acts">${again}</div>` : ''}
        </div>
        <div class="line-row bp-line ${stripOpen() ? '' : 'folded'}">
          ${stationStrip(line)}
          ${stripToggle(stripOpen())}
        </div>
      </div>
    </header>
    <div class="wrap">
      <div class="bp-facts bp-facts-line">
        <span><b>${commits.length}</b> ${commits.length === 1 ? 'commit' : 'commits'}${
          authors.length ? ` · ${esc(authors.join(', '))}` : ''}${when ? ` · ${esc(when)}` : ''}</span>
        <span>${esc(s.base_ref)} <b>${bpSha(s.base_sha)}</b> → <b>${bpSha(s.head_sha)}</b></span>
        ${stat ? `<span><b>${stat.files}</b> files · <span class="add">+${stat.added}</span>
          <span class="rem">−${stat.removed}</span></span>` : ''}
        <span>${s.look === 1 ? 'first look at this branch' : `look ${s.look} at this branch`}</span>
      </div>
    </div>`;

  if (s.stage === 'failed' && !d.current) {
    return `${plate}<div class="wrap"><div class="error-box" style="margin-top:1.5rem"><pre>${
      esc(s.error || 'The look stopped.')}</pre></div></div>`;
  }
  if (!packet) {
    return `${plate}<div class="wrap"><p class="empty" style="margin-top:1.5rem">Fabrika is looking at this
      branch: reading it, running this project's checks on it, and cutting its diff into pieces. The
      stations above light as each finishes.</p></div>`;
  }
  const stale = !d.current
    ? `<p class="bp-stale">A newer look is ${s.stage === 'failed' ? 'stopped' : 'under way'}. Below is
        look ${packet.look}, at ${bpSha(packet.head_sha)}.${s.error ? ` ${esc(s.error)}` : ''}</p>` : '';
  const checks = packet.checks || { results: [] };
  const green = checks.results.filter((r) => r.passed).length;
  const lines = packet.lines || {};
  const total = Object.values(lines).reduce((a, n) => a + n, 0);
  const report = (d.system || {}).report || null;
  /* A feature's packet, part for part: the verdict band, the tab strip, one
     tab at a time. The band's word is the one thing this look cannot claim yet
     -- there is no review in it to give a verdict -- so it says that, and the
     rapporteur's headline beside it says what was built. */
  const tab = ['built', 'code', 'checks'].includes(state.bpTab) ? state.bpTab : 'built';
  const href = (t) => `${branchHref(pid, s.slug)}&bp=${t}`;
  const red = checks.results.some((r) => !r.passed);
  const tabs = [
    ['built', 'What it built', bpPlural(stat.files, 'file'), false],
    ['code', 'The code', `${lines.novel || 0} novel of ${total} lines`, false],
    ['checks', 'Checks', checks.results.length ? `${green} of ${checks.results.length}` : 'none', red],
  ].map(([t, label, count, alert]) => `
    <a class="tab" role="tab" href="${href(t)}" aria-selected="${tab === t}">${esc(label)}
      <span class="mono tab-n">${esc(count)}</span>${alert ? '<span class="tab-dot" title="a check is red"></span>' : ''}</a>`).join('');
  /* Inside `g2`, as the review is, so the band and the tabs take the window's
     width rather than the column's. The band's ground is no verdict's: green
     ships, light green ships once you rule, red does not, and a look has no
     review to give any of the three. */
  return `${plate}
    <div class="g2">
    <section class="v-band v-band-none">
      <div class="wrap v-in">
        <span class="v-word">Not reviewed</span>
        <p class="verdict-headline">${esc(packet.headline || 'The rapporteur gave no headline.')}</p>
        ${/* The review's reading mode: the rail, the masthead and the board fold
             away and the packet is the window. F and Esc, as there. */''}
        <div class="v-tools">${focusToggle()}</div>
      </div>
    </section>
    <div class="tabstrip"><div class="wrap tabs" role="tablist" aria-label="How to read this packet">${tabs}</div></div>
    <div class="wrap bp-tab">
      ${stale}
      ${tab === 'built' ? bpBuilt(packet, report, commits, href('code'), d) : ''}
      ${tab === 'code' ? bpCode(packet, total) : ''}
      ${tab === 'checks' ? bpRules(packet, checks) : ''}
    </div>
    </div>`;
}

/* A model's prose, made readable and nothing more: escaped, its `code` set as
   code, and a blank line a new paragraph. No other markup is honoured. */
function bpProse(text) {
  return String(text || '').split(/\n\s*\n/).filter((x) => x.trim())
    .map((para) => `<p>${esc(para.trim()).replace(/`([^`\n]+)`/g, '<code>$1</code>')}</p>`).join('');
}

/* What it built, as the mockup has it: what the work is, what it adds to the
   system, how the new code runs, where it landed, and what its commits claim
   against the tests that show it. Every name links to its piece in The code. */
function bpBuilt(packet, report, commits, codeHref, look = {}) {
  const pieces = packet.pieces || [];
  const byId = Object.fromEntries(pieces.map((p) => [p.id, p]));
  const to = (id, inner) => (id && byId[id] ? `<a href="${codeHref}#piece-${esc(id)}">${inner}</a>` : inner);
  to.href = codeHref;
  const f = packet.facts || {};
  const d = f.data || { tables_added: [], tables_changed: [] };
  const adds = (f.components || []).length + (f.routes || []).length
    + d.tables_added.length + d.tables_changed.length;
  const files = new Set(pieces.filter((p) => p.klass !== 'tests').map((p) => p.path)).size;
  const claims = packet.claims || [];
  /* The spec's document: its sections, headed and iconed as the spec's are,
     and its contents beside them, lit as the reader scrolls. */
  const secs = [
    ['touches', 'What it adds', adds, bpAdds(packet, report, to)],
    ['l-play', 'Key flows', (packet.paths || []).length, bpPaths(packet, codeHref)],
    ['l-layers', 'Where it landed', files, bpLanded(packet, report, codeHref, look)],
    ['mustdo', claims.length ? 'What it claims' : 'What its commits say',
     claims.length || commits.length, bpClaims(packet, commits)],
  ].filter(([, , , body]) => body && body.trim());
  return `
    <section class="bp-sec bp-doc" aria-label="What it built">
      ${packet.summary ? `<div class="bp-summary">${bpProse(packet.summary)}</div>` : ''}
      ${packet.intent ? `<p class="bp-sub">Handed in as: <i>${esc(packet.intent)}</i></p>` : ''}
      <div class="doc">
        <div class="doc-body">
          ${secs.map(([id, name, n, body]) => docSection(id, name, n, '', body)).join('')}
        </div>
        <nav class="toc" aria-label="Contents">
          <p class="toc-label">Contents</p>
          ${secs.map(([id, name, n]) => `<a class="toc-item" href="#sec-${id}">
            ${icon(id, 'ic ic-sm')}<span class="toc-name">${esc(name)}</span>
            ${n ? `<span class="toc-count">${n}</span>` : ''}</a>`).join('')}
        </nav>
      </div>
    </section>`;
}

/* What it adds, the way most software is built: what a person sees, the
   endpoints behind it, the data it keeps -- always, since "none" is an answer
   too. Then, only where the branch has one, what is worth stopping on:
   packages, what it reaches outside the repository, secrets, settings. All of
   it found in the code; each name points at the piece it is in. */
function bpAdds(packet, report, to) {
  const f = packet.facts;
  if (!f) return '';
  const code = (x) => to(x.piece, `<code>${esc(x.name)}</code>`);
  const tile = (n, label, items, cls = '') => `
    <div class="bp-add${cls}">
      <div class="bp-add-n">${n}</div>
      <div class="bp-add-l">${esc(label)}</div>
      ${items ? `<ul>${items}</ul>` : ''}
    </div>`;

  const core = bpTouchMap(f, to.href);
  const pk = f.packages || { added: [], removed: [], lockfiles: [] };
  const also = [
    pk.added.length || pk.removed.length ? tile(pk.added.length, pk.added.length === 1 ? 'new package' : 'new packages',
      pk.added.map((n) => `<li><code>${esc(n)}</code></li>`).join('')
      + pk.removed.map((n) => `<li class="bp-q"><code>${esc(n)}</code> removed</li>`).join('')) : '',
    (f.outside || []).length ? tile(f.outside.length, 'outside the repository',
      f.outside.map((x) => `<li>${code(x)}</li>`).join(''), ' bp-add-out') : '',
    (f.secrets || []).length ? tile(f.secrets.length,
      f.secrets.length === 1 ? 'place a secret is read or written' : 'places a secret is read or written',
      f.secrets.map((x) => `<li>${code(x)} <span class="bp-q">${esc(x.how)}</span></li>`).join(''), ' bp-add-out') : '',
    (f.settings || []).length ? tile(f.settings.length, f.settings.length === 1 ? 'new setting' : 'new settings',
      f.settings.map((x) => `<li>${code(x)} <span class="bp-q">in ${esc(x.file)}</span></li>`).join('')) : '',
  ].filter(Boolean).join('');
  const quiet = pk.added.length || pk.removed.length ? ''
    : `<p class="bp-sub bp-adds-note">No new packages${pk.lockfiles.length ? '; a lockfile changed' : ', and the lockfiles are unchanged'}.</p>`;
  return `${core}${bpTouchTabs(f, to)}
    ${also ? `<div class="bp-adds bp-adds-also">${also}</div>` : ''}${quiet}`;
}

/* Under the map, the spec's three tabs: every table and column the branch
   adds or drops, every endpoint, every screen -- each with what its own code
   says it is (a column's type and keys, a docstring, the comment above it). */
function bpTouchTabs(f, to) {
  const d = f.data || { tables_added: [], tables_changed: [], tables_removed: [] };
  const OP = { add: '+', alter: '~', drop: '\u2212' };
  const row = (op, name, x = {}) => {
    const type = [x.type, x.key].filter(Boolean).join(', ');
    const nul = x.nullable === true ? 'NULL' : x.nullable === false ? 'NOT NULL' : '';
    return `<p class="schema-col mono ${op}"><span class="op">${OP[op]}</span>
        <span class="name">${esc(name)}</span><span class="type">${esc(type)}</span>
        <span class="null">${nul}</span></p>${x.note ? `<p class="schema-note">${esc(x.note)}</p>` : ''}`;
  };
  const detail = (t, c) => (t.detail || {})[c] || {};
  const tables = [
    ...d.tables_added.map((t) => [t, 'add', [...t.columns, ...(t.relations || [])].map((c) => ['add', c])]),
    ...d.tables_changed.map((t) => [t, 'alter', [...t.added.map((c) => ['add', c]),
      ...(t.relations || []).map((c) => ['add', c]), ...t.removed.map((c) => ['drop', c])]]),
    ...(d.tables_removed || []).map((name) => [{ name }, 'drop', []]),
  ];
  const dataRows = tables.reduce((n, [, , cols]) => n + 1 + cols.length, 0);
  const dataPanel = tables.map(([t, op, cols]) => `
    <div class="schema">
      <p class="schema-table mono">${t.piece ? to(t.piece, esc(t.name)) : esc(t.name)}</p>
      ${row(op, t.name, { note: t.note })}
      ${cols.map(([cop, c]) => row(cop, c, detail(t, c))).join('')}
    </div>`).join('');
  const cls = (state) => (state === 'added' ? 'new' : 'alter');
  const glyph = (state) => (state === 'added' ? '+' : '~');
  const ifacePanel = (f.routes || []).map((r) => {
    const at = r.name.indexOf(' ');
    const [verb, path] = at > 0 ? [r.name.slice(0, at), r.name.slice(at + 1)] : ['', r.name];
    return `<div class="iface ${cls(r.state)}">
      <p class="mono"><span class="op">${glyph(r.state)}</span><b>${esc(verb)}</b>
        ${r.piece ? to(r.piece, esc(path)) : esc(path)}</p>
      ${r.note ? `<p class="iface-change">${esc(r.note)}</p>` : ''}
    </div>`;
  }).join('');
  const surfacePanel = (f.components || []).map((c) => `
    <div class="iface ${cls(c.state)}">
      <p><span class="op">${glyph(c.state)}</span><b>${c.piece ? to(c.piece, esc(c.name)) : esc(c.name)}</b>${
        c.file ? `<br><span class="where-path">${esc(c.file)}</span>` : ''}</p>
      ${c.note ? `<p class="iface-change">${esc(c.note)}</p>` : ''}
    </div>`).join('');
  return docTabs('bp-touches', [
    ['data', 'Data', dataRows, dataPanel],
    ['iface', 'Interface', (f.routes || []).length, ifacePanel],
    ['surfaces', 'Where you see it', (f.components || []).length, surfacePanel],
  ]);
}

/* What it adds, drawn as the spec's "What it touches": what a person sees,
   the endpoints behind it, and the data those touch, with a line wherever the
   code makes one call the other. Every line was read from the branch's code at
   its head, not from anyone's account of it. */
function bpTouchMap(f, codeHref) {
  const ui = f.components || [];
  const eps = f.routes || [];
  const d = f.data || { tables_added: [], tables_changed: [], tables_removed: [] };
  const links = f.links || { ui_endpoint: [], endpoint_data: [], tables_used: [] };
  const cols = (names, op, type = '') => names.map((name) => ({ name, op, type }));
  const tables = [
    ...d.tables_added.map((t) => ({ id: `tb:${t.name}`, name: t.name, piece: t.piece,
      cols: [...cols(t.columns, 'added'), ...cols(t.relations || [], 'added', 'relation')] })),
    ...d.tables_changed.map((t) => ({ id: `tb:${t.name}`, name: t.name, piece: t.piece,
      cols: [...cols(t.added, 'added'), ...cols(t.relations || [], 'added', 'relation'),
             ...cols(t.removed, 'removed')] })),
  ];
  const used = links.tables_used || [];
  const edges = [
    ...links.ui_endpoint.map(([c, r]) => [`ui:${c}`, `ep:${r}`]),
    ...links.endpoint_data.map(([r, t]) => [`ep:${r}`, `tb:${t}`]),
  ];
  if (!ui.length && !eps.length && !tables.length) {
    return '<p class="bp-sub">No screen, endpoint or stored data changed.</p>';
  }

  // The spec review's "What it touches", box for box: the same columns and
  // sizes, a table as its name over a row per column it adds or drops, and
  // the tables it only uses grouped apart, dashed.
  const SW = 190, EW = 268, DW = 290;
  const SX = 0, EX = 280, DX = 668, W = DX + DW;
  const TOP = 34, ROW = 26, NH = 20, EH = 34, GAP = 12;
  const pos = {};
  const rows = [];
  let y = TOP;
  tables.forEach((t) => {
    rows.push({ kind: 'label', text: t.name, y });
    y += 20;
    const first = y;
    t.cols.forEach((c) => { rows.push({ kind: 'col', t, c, y }); y += ROW; });
    // A line ends level with the table's rows: what it touches is the table.
    pos[t.id] = [DX, first, DW, Math.max(NH, y - first - (ROW - NH))];
    y += 10;
  });
  if (used.length) {
    y += 6;
    rows.push({ kind: 'label', text: 'existing · not changed', y, muted: true });
    y += 20;
    used.forEach((name) => { pos[`tb:${name}`] = [DX, y, DW, NH]; rows.push({ kind: 'ext', name, y }); y += ROW; });
  }

  // Barycentre: put a node level with the average of what it connects to.
  const bary = (id, fallback) => {
    const ys = edges.filter(([a, b]) => a === id || b === id)
      .map(([a, b]) => pos[a === id ? b : a]).filter(Boolean).map((q) => q[1] + q[3] / 2 - EH / 2);
    return ys.length ? ys.reduce((x, n) => x + n, 0) / ys.length : fallback;
  };
  const stack = (items, x, w) => {
    let cursor = TOP;
    items.map((it) => ({ it, at: bary(it.id, TOP) }))
      .sort((p, q) => p.at - q.at)
      .forEach(({ it, at }) => {
        const top = Math.max(cursor, at);
        pos[it.id] = [x, top, w, EH];
        cursor = top + EH + GAP;
      });
  };
  const split = (name) => { const at = name.indexOf(' '); return at > 0 ? [name.slice(0, at), name.slice(at + 1)] : ['', name]; };
  const epNodes = eps.map((r) => ({ id: `ep:${r.name}`, name: r.name, state: r.state, piece: r.piece }));
  const uiNodes = ui.map((c) => ({ id: `ui:${c.name}`, name: c.name, file: c.file, state: c.state, piece: c.piece }));
  stack(epNodes, EX, EW);
  stack(uiNodes, SX, SW);
  const H = Math.max(y, ...Object.values(pos).map((q) => q[1] + q[3])) + 20;

  // SVG text neither wraps nor clips: a name too long for its box is squeezed.
  const fit = (text, room, size = 6.3) =>
    text.length * size > room ? ` textLength="${Math.round(room)}" lengthAdjust="spacingAndGlyphs"` : '';
  const curve = ([a, b]) => {
    const [ax, ay, aw, ah] = pos[a], [bx, by, , bh] = pos[b];
    const x1 = ax + aw, y1 = ay + ah / 2, x2 = bx, y2 = by + bh / 2;
    const dx = Math.max(40, (x2 - x1) * 0.45);
    return `M${x1},${y1.toFixed(1)} C${x1 + dx},${y1.toFixed(1)} ${x2 - dx},${y2.toFixed(1)} ${x2},${y2.toFixed(1)}`;
  };
  const OPS = { added: ['+', ''], changed: ['~', 'alter'], removed: ['−', 'gone'] };
  const op = (state, x, yy) => (OPS[state]
    ? `<text class="op ${OPS[state][1]}" x="${x}" y="${yy}">${OPS[state][0]}</text>` : '');
  const linked = (piece, g) => (piece ? `<a class="bp-map-link" href="${esc(codeHref)}#piece-${esc(piece)}">${g}</a>` : g);
  const box = (n, verb, path, title) => {
    const [x, yy, w, h] = pos[n.id];
    return linked(n.piece, `<g class="n" data-id="${esc(n.id)}"><title>${esc(title)}</title>
      <rect x="${x}" y="${yy}" width="${w}" height="${h}" rx="3"/>
      ${op(n.state, x + 10, yy + h / 2 + 4)}
      <text class="verb" x="${x + 24}" y="${yy + 14}">${esc(verb)}</text>
      <text class="path" x="${x + 24}" y="${yy + 26}"${fit(path, w - 34)}>${esc(path)}</text></g>`);
  };
  const drawn = edges.filter(([a, b]) => pos[a] && pos[b]);
  return `
    <p class="oplegend">
      <span><span class="op new">+</span>added</span>
      <span><span class="op alter">~</span>changed</span>
      <span><span class="op gone">−</span>removed</span>
    </p>
    <figure class="cmap-wrap">
      <svg class="cmap" viewBox="0 0 ${W} ${Math.round(H)}" role="img"
        aria-label="Which screens call which endpoints, and which data those endpoints touch">
        <text class="head" x="${SX}" y="16">SEEN AT · ${ui.length}</text>
        <text class="head" x="${EX}" y="16">ENDPOINTS · ${eps.length}</text>
        <text class="head" x="${DX}" y="16">DATA · ${tables.length}</text>
        ${drawn.map(([a, b]) => `<path class="edge" d="${curve([a, b])}"
          data-a="${esc(a)}" data-b="${esc(b)}"/>`).join('')}
        ${uiNodes.map((n) => box(n, 'SCREEN', n.name, n.file || n.name)).join('')}
        ${epNodes.map((n) => { const [verb, path] = split(n.name); return box(n, verb, path, n.name); }).join('')}
        ${rows.map((r) => (r.kind === 'label'
          ? `<text class="tbl ${r.muted ? 'muted' : ''}" x="${DX}" y="${r.y + 11}">${esc(r.text)}</text>`
          : r.kind === 'ext'
          ? `<g class="n ext" data-id="tb:${esc(r.name)}">
              <rect x="${DX}" y="${r.y}" width="${DW}" height="${NH}" rx="3"/>
              <text class="name" x="${DX + 10}" y="${r.y + 14}"${fit(r.name, DW - 20)}>${esc(r.name)}</text></g>`
          : linked(r.t.piece, `<g class="n" data-id="${esc(r.t.id)}">
              <rect x="${DX}" y="${r.y}" width="${DW}" height="${NH}" rx="3"/>
              ${op(r.c.op, DX + 9, r.y + 14)}
              <text class="name" x="${DX + 22}" y="${r.y + 14}">${esc(r.c.name)}</text>
              ${r.c.type ? `<text class="type" x="${DX + DW - 9}" y="${r.y + 14}" text-anchor="end">${esc(r.c.type)}</text>` : ''}</g>`)))
          .join('')}
      </svg>
      <figcaption>Hover a box to isolate what it connects to. Every line was read from the
        branch's code, not from what the model wrote; a box opens its code.${used.length
          ? ` ${used.length} table${used.length === 1 ? '' : 's'} shown dashed
             ${used.length === 1 ? 'is' : 'are'} used by this branch and not altered.` : ''}
      </figcaption>
    </figure>`;
}

/* Key flows: each one a sequence diagram. Who takes part is read from the
   steps' pieces -- one lifeline a file, ordered screen, client, server, data,
   then what lies outside the branch's code -- and each step is a message to
   the file it runs in, from the one before it: a call to itself loops back, a
   step back from the server to the screen is a reply, dashed. The person is
   the first lifeline, and what they do starts the messages it causes. */
const BP_ROUTE = /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|WEBSOCKET)\b/;
const BP_FRONT = /\.(jsx?|tsx?|mjs|vue|svelte|html|css)$/;
const BP_CLIENT = /(^|\/)(api|client|http|fetch|request|requests)\.(m?js|tsx?)$/;
const BP_DATA = /(^|\/)(models?\.py|models\/|migrations?\/|alembic\/)|\.(prisma|sql)$/;
const BP_REQUEST = /\b(GET|POST|PUT|PATCH|DELETE) \/[^\s,;)]*/;
const BP_LANES = ['screen', 'client', 'server', 'data', 'outside'];

function bpFlow(packet, codeHref, at) {
  const pa = (packet.paths || [])[at];
  if (!pa) return { svg: '', html: '', who: [] };
  const byId = Object.fromEntries((packet.pieces || []).map((p) => [p.id, p]));
  const d = (packet.facts && packet.facts.data) || {};
  const tablePieces = new Set([...(d.tables_added || []), ...(d.tables_changed || [])].map((t) => t.piece).filter(Boolean));
  const steps = [...pa.steps, ...pa.then_steps];
  const laneOf = (p) => {
    if (!p) return 4;
    if (BP_FRONT.test(p.path)) return BP_CLIENT.test(p.path) ? 1 : 0;
    return tablePieces.has(p.id) || BP_DATA.test(p.path) ? 3 : 2;
  };

  // One lifeline a file; a step outside the branch's code is one of its own.
  const lines = new Map();
  steps.forEach((st, i) => {
    const p = st.piece && byId[st.piece];
    const key = p ? p.path : `out:${st.label}`;
    if (!lines.has(key)) lines.set(key, { key, path: p ? p.path : '', lane: laneOf(p), first: i, label: st.label });
  });
  const stem = (path) => path.split('/').pop().replace(/\.[^.]+$/, '');
  const stems = {};
  [...lines.values()].forEach((l) => { if (l.path) stems[stem(l.path)] = (stems[stem(l.path)] || 0) + 1; });
  const who = [...lines.values()].sort((x, y) => x.lane - y.lane || x.first - y.first).map((l) => ({
    ...l, name: !l.path ? l.label : stems[stem(l.path)] > 1 ? l.path.split('/').slice(-2).join('/').replace(/\.[^.]+$/, '') : stem(l.path),
  }));
  const SP = 124, X0 = 44, TOP = 64, NOTE = 12 * 0.52, LINE = 15;
  const x = { you: X0 };
  who.forEach((w, i) => { x[w.key] = X0 + SP * (i + 1); });
  let W = X0 + SP * who.length + 60;
  // Monospace at the sizes branch.css draws: a character is 0.6 of the size.
  const reach = (left, text, size) => { W = Math.max(W, Math.ceil(left + String(text).length * size * 0.6) + 12); };
  const keyOf = (st) => (st.piece && byId[st.piece] ? byId[st.piece].path : `out:${st.label}`);
  const laneAt = (i) => lines.get(keyOf(steps[i])).lane;
  const plain = (t) => String(t || '').replace(/`([^`\n]+)`/g, '$1');
  const wrap = (text, width) => {
    const room = Math.max(24, Math.floor(width / NOTE));
    const out = [''];
    plain(text).split(/\s+/).filter(Boolean).forEach((w) => {
      const cur = out[out.length - 1];
      if (!cur || (cur + ' ' + w).length <= room) out[out.length - 1] = cur ? `${cur} ${w}` : w;
      else out.push(w);
    });
    return out[0] ? out : [];
  };

  let y = TOP + 18;
  const drawn = [];
  const active = {};
  const touch = (k, top, bottom) => {
    const a = active[k] || (active[k] = [top, bottom]);
    a[0] = Math.min(a[0], top); a[1] = Math.max(a[1], bottom);
  };
  const mark = { added: ['+', 'add'], changed: ['~', 'chg'] };
  steps.forEach((st, i) => {
    const fromYou = i === 0 || (i === pa.steps.length && pa.then);
    const from = fromYou ? 'you' : keyOf(steps[i - 1]);
    const to = keyOf(st);
    const self = from === to;
    const back = !fromYou && laneAt(i - 1) >= 2 && laneAt(i - 1) <= 3 && laneAt(i) <= 1;
    const crossing = !fromYou && laneAt(i - 1) <= 1 && laneAt(i) >= 2 && laneAt(i) <= 3;
    let request = '';
    if (crossing) {
      // The route's own handler names the request; failing that, the step's note.
      const route = steps.slice(i).map((s2) => byId[s2.piece]).find((p) => p && BP_ROUTE.test(p.symbol));
      const said = BP_REQUEST.exec(`${steps[i - 1].note || ''} ${steps[i - 1].label} ${st.note || ''}`);
      request = route ? route.symbol : said ? said[0] : '';
    }
    const x1 = x[from], x2 = x[to];
    const left = self ? x1 + 10 : Math.min(x1, x2) + 12;
    const width = self ? SP * 3.4 : Math.max(Math.abs(x2 - x1) - 24, SP * 3.4);
    const mid = self ? left : (x1 + x2) / 2;
    // Text runs right from the arrow's start; near the last lifeline, where
    // there is no room for that, it runs left from the arrow's end instead,
    // since running past the lifelines would only scroll.
    const right = X0 + SP * who.length + 40;
    const short = self || Math.abs(x2 - x1) < SP * 1.7;
    const anchor = !short ? 'middle' : right - left >= SP * 3.4 ? 'start' : 'end';
    const tx = anchor === 'start' ? left : anchor === 'end' ? (self ? x1 - 10 : Math.max(x1, x2) - 12) : mid;
    const head = [];
    if (fromYou && i > 0) head.push(['you', pa.then]);
    if (request) head.push(['req', request]);
    const notes = st.note ? wrap(st.note, width) : [];
    const textH = (head.length + 1 + notes.length) * LINE;
    const ay = y + textH + 4;
    const [g, cls] = mark[st.kind] || ['', 'out'];
    let t = y + 11;
    const text = [];
    head.forEach(([k, v]) => { text.push(`<text class="sq-${k}" x="${tx}" y="${t}" text-anchor="${anchor}">${k === 'you' ? '[' : ''}${esc(plain(v))}${k === 'you' ? ']' : ''}</text>`); t += LINE; });
    if (anchor === 'start') {
      reach(tx, `${i + 1}: ${g ? `${g} ` : ''}${plain(st.label)}`, 13);
      head.forEach(([, v]) => reach(tx, v, 12.5));
      notes.forEach((n) => reach(tx, n, 12 * 0.87));
    }
    const label = `<text class="sq-msg ${cls}" x="${tx}" y="${t}" text-anchor="${anchor}"><tspan class="sq-n">${i + 1}:</tspan> ${
      g ? `<tspan class="m">${g}</tspan> ` : ''}${esc(plain(st.label))}</text>`;
    text.push(st.piece && byId[st.piece] ? `<a href="${esc(codeHref)}#piece-${esc(st.piece)}">${label}</a>` : label);
    t += LINE;
    notes.forEach((n) => { text.push(`<text class="sq-note" x="${tx}" y="${t - 1}" text-anchor="${anchor}">${esc(n)}</text>`); t += LINE; });
    const dir = x2 >= x1 ? 1 : -1;
    const line = self
      ? `<path class="sq-line" d="M${x1 + 5},${ay} H${x1 + 36} V${ay + 16} H${x1 + 7}" marker-end="url(#sq-call)"/>`
      : `<path class="sq-line${back ? ' back' : ''}" d="M${x1 + (fromYou ? 0 : 5 * dir)},${ay} H${x2 - 6 * dir}"
          marker-end="url(#${back ? 'sq-back' : 'sq-call'})"/>${fromYou ? `<circle class="sq-dot" cx="${x1}" cy="${ay}" r="4"/>` : ''}`;
    drawn.push(`<g class="sq-step" data-step="${i + 1}"><title>${esc(plain(st.label))}${st.note ? ` -- ${esc(plain(st.note))}` : ''}${
      st.where ? ` (${esc(st.where)})` : ''}</title>${line}${text.join('')}</g>`);
    if (!fromYou) touch(from, ay - 6, ay + 6);
    touch(to, ay - 6, ay + (self ? 24 : 8));
    y = ay + (self ? 30 : 16);
  });
  // A flow that went to the server and ended there still answers the screen.
  const lastLane = steps.length ? laneAt(steps.length - 1) : 0;
  const caller = [...steps.keys()].reverse().find((i) => laneAt(i) <= 1);
  if (steps.length && lastLane >= 2 && lastLane <= 3 && caller !== undefined) {
    const x1 = x[keyOf(steps[steps.length - 1])], x2 = x[keyOf(steps[caller])];
    const ay = y + LINE + 4;
    drawn.push(`<g class="sq-step"><text class="sq-req" x="${(x1 + x2) / 2}" y="${y + 11}" text-anchor="middle">back to the screen</text>
      <path class="sq-line back" d="M${x1 - 5},${ay} H${x2 + 6}" marker-end="url(#sq-back)"/></g>`);
    touch(keyOf(steps[caller]), ay - 6, ay + 6);
    y = ay + 16;
  }
  const H = y + 16;
  const heads = [{ key: 'you', name: 'you', lane: -1 }, ...who].map((w) => {
    const cx = x[w.key];
    const out = w.lane === 4;
    return `<g class="sq-who${w.key === 'you' ? ' you' : ''}${out ? ' out' : ''}"><title>${esc(w.path || (out ? "outside this branch's code" : 'the person using it'))}</title>
      <rect x="${cx - SP / 2 + 8}" y="4" width="${SP - 16}" height="40" rx="3"/>
      <text class="sq-name" x="${cx}" y="22" text-anchor="middle">${esc(w.name.length > 20 ? `${w.name.slice(0, 19)}…` : w.name)}</text>
      <text class="sq-lane" x="${cx}" y="36" text-anchor="middle">${w.key === 'you' ? 'person' : BP_LANES[w.lane]}</text>
      <path class="sq-life" d="M${cx},44 V${H}"/></g>`;
  }).join('');
  const bars = Object.entries(active).map(([k, [top, bottom]]) =>
    `<rect class="sq-act" x="${x[k] - 5}" y="${top}" width="10" height="${bottom - top}"/>`).join('');
  const svg = `<svg class="sq" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(pa.title)}: the steps in the order they run">
    <defs>
      <marker id="sq-call" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path class="sq-head" d="M0,0 L10,5 L0,10 z"/></marker>
      <marker id="sq-back" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path class="sq-head open" d="M0,0 L10,5 L0,10"/></marker>
    </defs>${heads}${bars}${drawn.join('')}</svg>`;
  // Drawn at its own size, and smaller only down to where it still reads:
  // past that it scrolls, rather than turning to fine print.
  return { svg, who: who.map((w) => w.name),
    html: `<div class="bp-seq"><div style="max-width:${W}px;min-width:${Math.round(W * 0.8)}px">${svg}</div></div>` };
}

function bpPaths(packet, codeHref) {
  const paths = packet.paths || [];
  if (!paths.length) return '';
  const at = Math.min(Math.max(Number(state.bpPath) || 0, 0), paths.length - 1);
  const pa = paths[at];
  // One line a flow, numbered, in as many columns as fit: ten flows are four
  // lines, not three rows of cards pushing the diagram down the page.
  const keys = paths.map((x, i) => `
    <li><button type="button" class="bp-fkey${i === at ? ' on' : ''}" data-bp-path="${i}"
      aria-pressed="${i === at}" title="${esc(x.title)}"><span class="bp-fkey-i">${i + 1}</span><span class="bp-fkey-t">${esc(x.title)}</span>
      <span class="bp-fkey-n">${bpPlural(x.steps.length + x.then_steps.length, 'step')}</span></button></li>`).join('');
  const step = (by, glyph, label) => `<button type="button" class="bp-fstep-btn" data-bp-path="${
    (at + by + paths.length) % paths.length}" aria-label="${label}">${glyph}</button>`;
  return `
    <figure class="cmap-wrap bp-map">
      <ol class="bp-flist" aria-label="Key flows">${keys}</ol>
      <div class="bp-pmap">
        <div class="bp-pmap-h">
          <div class="bp-pmap-t"><b>${esc(pa.title)}</b>${paths.length > 1 ? `
            <span class="bp-fnav">${step(-1, '\u2039', 'Previous flow')}<span>Flow ${at + 1} of ${paths.length}</span>${
              step(1, '\u203a', 'Next flow')}</span>` : ''}</div>
          ${pa.said ? `<p>${bpInline(pa.said)}</p>` : ''}</div>
        ${bpFlow(packet, codeHref, at).html}
      </div>
    </figure>`;
}

/* Where it landed: how far into the system the branch reached. The
   repository's own groups when the as-built is read at both ends, its folders
   when it is not -- one line a group, with what it touched of it, and the
   groups it left alone after them. A group opens on its files, each with the
   functions, classes, routes and settings the branch added or changed in it:
   its imports and loose module lines are counted, not named. */
const BP_NOISE = /^(imports|module code)$|, top$/;

function bpUnits(pieces, path) {
  const units = new Map();
  let loose = 0;
  pieces.filter((p) => p.path === path && p.klass !== 'tests').forEach((p) => {
    String(p.symbol).split(' · ').forEach((raw) => {
      if (BP_NOISE.test(raw)) { loose += 1; return; }
      const name = raw.replace(/, fields$/, '');
      const had = units.get(name);
      if (!had || (p.fresh && !had.fresh)) units.set(name, { name, id: p.id, fresh: p.fresh });
    });
  });
  return { units: [...units.values()], loose };
}

function bpLanded(packet, report, codeHref, look = {}) {
  const pieces = packet.pieces || [];
  const tests = pieces.filter((p) => p.klass === 'tests');
  const isTest = (path) => tests.some((t) => t.path === path);
  const counts = (paths) => {
    const mine = pieces.filter((p) => paths.includes(p.path));
    const a = mine.reduce((n, p) => n + p.added.length, 0);
    const r = mine.reduce((n, p) => n + p.removed.length, 0);
    return `<span class="bp-gr-pm"><span class="a">+${a}</span>${r ? ` <span class="r">−${r}</span>` : ''}</span>`;
  };
  const SHOWN = 8;
  const fileRow = (path) => {
    const { units, loose } = bpUnits(pieces, path);
    const chips = units.slice(0, SHOWN).map((u) => `
      <a class="bp-sym ${u.fresh ? 'add' : 'chg'}" href="${codeHref}#piece-${esc(u.id)}"
        title="${u.fresh ? 'added by this branch' : 'changed by this branch'}">${esc(u.name)}</a>`).join('');
    const more = [units.length > SHOWN ? `${units.length - SHOWN} more` : '',
      loose ? (units.length ? 'and its imports' : 'imports and module lines only') : ''].filter(Boolean).join(', ');
    return `<div class="bp-file"><div class="bp-file-p">${esc(path)}${counts([path])}</div>
      <div class="bp-syms">${chips}${more ? `<span class="bp-sym-more">${esc(more)}</span>` : ''}</div></div>`;
  };

  // The groups, from the as-built or else from the folders at the head.
  let groups;
  let left;
  const changed = [...new Set(pieces.filter((p) => p.klass !== 'tests').map((p) => p.path))];
  if (report) {
    const changedIn = (g) => g.files.filter((f) => f.status !== 'same' && !isTest(f.path)).map((f) => f.path);
    groups = report.groups.filter((g) => g.touched && changedIn(g).length)
      .map((g) => ({ name: g.name || g.key, files: changedIn(g), total: g.files.length }));
    if (report.loose.length) groups.push({ name: 'In no group', files: report.loose.map((f) => f.path), total: null });
    left = report.groups.filter((g) => !g.touched).map((g) => g.name || g.key);
  } else {
    const folderOf = (path) => path.split('/').slice(0, -1).slice(0, 2).join('/') || '.';
    const sizes = look.folders || {};
    const by = new Map();
    changed.forEach((path) => {
      const f = folderOf(path);
      if (!by.has(f)) by.set(f, []);
      by.get(f).push(path);
    });
    groups = [...by.entries()].map(([name, files]) => ({ name: name === '.' ? 'the top folder' : `${name}/`, files,
      total: sizes[name] || null }));
    const testFolders = new Set(tests.map((t) => folderOf(t.path)));
    // Not a folder that holds one the branch changed, nor one of its tests'.
    const holds = (f) => [...by.keys(), ...testFolders].some((x) => x.startsWith(`${f}/`));
    left = Object.keys(sizes).filter((f) => !by.has(f) && !testFolders.has(f) && !holds(f) && f !== '.'
                                        && !/(^|\/)\./.test(f))
      .sort().map((f) => `${f}/`);
  }
  const preview = (g) => g.files.flatMap((path) => bpUnits(pieces, path).units.map((u) => u.name));
  const rows = groups.map((g) => {
    const names = preview(g);
    return `
    <details class="bp-gr">
      <summary><span class="bp-gr-name">${esc(g.name)}</span>
        <span class="bp-gr-n">${g.total ? `${g.files.length} of ${g.total} files` : bpPlural(g.files.length, 'file')}</span>
        ${counts(g.files)}
        <span class="bp-gr-what">${esc(names.slice(0, 6).join(' · '))}${names.length > 6 ? ` +${names.length - 6}` : ''}</span></summary>
      <div class="bp-gr-files">${g.files.map(fileRow).join('')}</div>
    </details>`;
  }).join('');

  // What the reading said, when it is not groups: stopped, partial or under way.
  const readings = look.readings || {};
  const off = ['base', 'head'].filter((k) => readings[k] && readings[k].state !== 'read');
  const which = off.length === 2 ? 'both ends' : off.length ? `the ${off[0]}` : '';
  const reading = off.some((k) => readings[k].state === 'reading');
  const again = !reading ? `<button type="button" class="btn btn-sm" id="look-read">Read again</button>` : '';
  let lede;
  if (report) {
    const grouped = report.groups.filter((g) => g.touched).length;
    lede = `This repository's own groups, from the as-built read at both ends of the branch:
      ${grouped} of ${bpPlural(report.groups.length, 'group')} touched${report.loose.length
        ? `, and ${bpPlural(report.loose.length, 'file')} landed in none` : ''}. Open one for its files.`;
  } else if (reading) {
    lede = `The as-built of ${which} is being read now; this is by folder until it finishes.`;
  } else if (off.some((k) => readings[k].state === 'stopped')) {
    const k = off.find((x) => readings[x].state === 'stopped');
    lede = `The as-built reading of ${which} stopped, so this is by folder rather than the repository's own
      groups.<span class="bp-read"><span class="bp-q">${esc(readings[k].error)}</span>${again}</span>`;
  } else if (off.some((k) => readings[k].state === 'partial')) {
    const k = off.find((x) => readings[x].state === 'partial');
    lede = `The as-built of ${which} was read with ${bpPlural(readings[k].unread, 'file')} left, so this is by
      folder rather than the repository's own groups.<span class="bp-read">${again}</span>`;
  } else {
    lede = `The as-built was not read for this look, so this is by folder rather than the repository's own
      groups.<span class="bp-read">${again}</span>`;
  }

  const testFiles = [...new Set(tests.map((p) => p.path))];
  const newTests = tests.filter((p) => p.kind === 'test' && p.fresh).length;
  const testLine = testFiles.length ? `
    <details class="bp-tests-line"><summary><b>Tests:</b> ${bpPlural(testFiles.length, 'file')},
      ${bpPlural(newTests, 'new test')}</summary>
      <p>${testFiles.map((f) => `<code>${esc(f)}</code>`).join(' ')}</p></details>` : '<p class="bp-untouched">No test file changed.</p>';
  return `
    <p class="bp-sub bp-landed-lede">${lede}</p>
    <div class="bp-glist">${rows}</div>
    ${left.length ? `<p class="bp-untouched"><b>Left alone:</b> ${left.slice(0, 12).map(esc).join(' · ')}${
      left.length > 12 ? ` and ${left.length - 12} more` : ''}.</p>` : ''}
    ${testLine}`;
}

/* What the commits claim, each against the tests that show it -- the only
   stated intent a branch with no spec has. */
function bpClaims(packet, commits) {
  const claims = packet.claims || [];
  if (!claims.length) {
    const said = commits.map((c) => `<li><code>${bpSha(c.sha)}</code> ${esc(c.message.split('\n')[0])}</li>`).join('');
    return `<p class="bp-sub">With no spec, the commit messages are the only stated intent this branch has.</p>
      <ul class="bp-looks">${said}</ul>`;
  }
  const shown = claims.filter((c) => c.state === 'shown').length;
  const mark = { shown: ['ok', '✓', 'shown by a test that ran green'],
                 unproved: ['part', '◐', 'a test names it, and the tests did not run green'],
                 untested: ['none', '○', 'no test exercises it'] };
  const rows = claims.map((c) => {
    const [cls, glyph, word] = mark[c.state] || mark.untested;
    const ev = [c.tests.map((t) => `<code>${esc(t)}</code>`).join(' · '),
      c.missing.length ? `named but not in the branch: ${c.missing.map(esc).join(', ')}` : '',
      !c.tests.length && !c.missing.length ? 'no test exercises it' : ''].filter(Boolean).join(' · ');
    return `<div class="bp-claim"><span class="mk ${cls}" role="img" aria-label="${esc(word)}">${glyph}</span>
      <q>${esc(c.quote)}</q><span class="ev">${ev}</span></div>`;
  }).join('');
  return `
    <p class="bp-sub">With no spec, the commit messages are the only stated intent this branch has. Each claim in
      them is matched to the test that exercises it, or marked where none does.</p>
    <div class="bp-claims">
      <div class="bp-claim bp-claim-h">${bpPlural(claims.length, 'claim')} · ${shown} shown by a test</div>
      ${rows}
    </div>`;
}

/* One sentence of a model's prose, inline: escaped, its `code` set as code. */
function bpInline(text) {
  return esc(String(text || '')).replace(/`([^`\n]+)`/g, '<code>$1</code>');
}

function bpRules(packet, checks) {
  const status = checks.status;
  const lede = status === 'none'
    ? 'This project has no checks yet, so nothing here says whether the branch keeps what already worked.'
    : status === 'proposed'
      ? `These are the checks Fabrika proposed for this repository. They have not been approved at
         gate 0, so this says what they found, not that the project agreed to them.`
      : 'The checks this project approved at gate 0, run at the head of the branch in its own environment.';
  const rows = checks.results.map((r) => {
    const word = r.passed ? 'passed' : !r.started ? 'could not run' : 'failed';
    const tone = r.passed ? 'pill-green' : !r.started ? 'pill-amber' : 'pill-red';
    const blame = r.caused_here ? `Green where the branch left ${esc(packet.base_ref)}: the branch broke it.`
      : r.pre_existing ? `Red where the branch left ${esc(packet.base_ref)} too: inherited, not the branch's.`
        : r.at_base === 'could_not_run' ? `Could not be run where the branch left ${esc(packet.base_ref)}, so whose it is is not known.`
          : '';
    const loc = r.located;
    const found = loc ? (loc.report !== 'read'
      ? `Its report was ${esc(loc.report || 'not written')}, so nothing is said by line.`
      : `${loc.added_count} on lines the branch wrote · ${loc.before_count} on lines that were there before`) : '';
    const rewrote = (r.would_rewrite || []).length
      ? `Would rewrite ${bpPlural(r.would_rewrite.length, 'file')}: ${r.would_rewrite.slice(0, 5).map(esc).join(', ')}. Recorded and put back; nothing was committed.`
      : '';
    return `
      <div class="bp-check${status === 'proposed' ? ' proposed' : ''}">
        <div><span class="bp-check-name">${esc(r.name)}</span></div>
        <div>
          <div class="bp-check-t">${[blame, found, rewrote].filter(Boolean).join(' ') || (r.passed ? 'Passed at the head.' : '')}</div>
          <div class="bp-check-c">${esc(r.command)}</div>
          ${!r.passed && r.output_tail ? `<details><summary class="small">output</summary><pre class="small">${esc(r.output_tail)}</pre></details>` : ''}
        </div>
        <div class="bp-check-r"><span class="pill ${tone}">${word}</span>
          ${r.duration_s ? `<div class="bp-check-n">${r.duration_s.toFixed(1)}s</div>` : ''}</div>
      </div>`;
  }).join('');
  return `
    <section class="bp-sec" aria-label="Checks">
      <p class="bp-lead">${lede}</p>
      ${rows ? `<div class="bp-checks">${rows}</div>` : ''}
    </section>`;
}

function bpRows(rows) {
  return rows.map(([op, b, h, text]) => {
    if (op === '~') return `<div class="dl-hunk">⋯ ${esc(text)}</div>`;
    const cls = op === '+' ? ' dl-add' : op === '-' ? ' dl-rem' : '';
    return `<div class="dl${cls}"><span class="dl-no">${b || ''}</span><span class="dl-no">${h || ''}</span><span class="dl-txt">${esc(text) || ' '}</span></div>`;
  }).join('');
}

function bpPiece(p) {
  const novel = p.klass === 'novel';
  const where = p.start ? `${p.path}:${p.start}` : `${p.old_path || p.path} · gone at the head`;
  const counts = `<span class="a">+${p.added.length}</span> <span class="r">−${p.removed.length}</span>`;
  return `
    <details class="bp-unit bp-unit-${esc(p.klass)}" id="piece-${esc(p.id)}" data-klass="${esc(p.klass)}"${novel ? ' open' : ''}>
      <summary>
        <span class="bp-umark ${novel ? 'mk-novel' : 'mk-boiler'}" role="img" aria-label="${esc(p.klass)}">${novel ? '◆' : '◇'}</span>
        <span class="bp-uhead"><span class="bp-utitle">${esc(p.symbol)}</span><span class="bp-upath">${esc(where)}${
          p.old_path && p.start ? ` · moved from ${esc(p.old_path)}` : ''}</span></span>
        <span class="bp-ulinks">${p.classed_by === 'default' ? '<span class="bp-ulink">unclassed</span>' : ''}</span>
        <span class="bp-ucount">${counts}</span>
        ${icon('l-chevron-right', 'ic bp-uchev')}
        <span class="bp-uwhy">${esc(p.reason)}</span>
      </summary>
      ${p.rows.length ? `<div class="bp-udiff diff-scroll"><div class="diff-rows">${bpRows(p.rows)}</div></div>`
        : `<div class="bp-udiff"><p class="small" style="padding:.6rem .8rem;margin:0">${
          p.binary ? 'A binary file. There are no lines to show.' : 'No line changed.'}</p></div>`}
    </details>`;
}

function bpCode(packet, total) {
  const pieces = packet.pieces || [];
  const lines = packet.lines || {};
  const byFile = state.bpOrder === 'file';
  const size = (p) => p.added.length + p.removed.length;
  const legend = BP_GROUPS.filter(([k]) => lines[k]).map(([k, label]) => {
    const n = pieces.filter((p) => p.klass === k).length;
    return `<span><i class="bp-seg-${k === 'tests' ? 'tests' : k}"></i>${esc(label.split(':')[0])} <b>${lines[k]}</b> lines · ${bpPlural(n, 'piece')}</span>`;
  }).join('');
  const bar = BP_GROUPS.filter(([k]) => lines[k]).map(([k, label]) =>
    `<i class="bp-seg bp-seg-${k}" style="flex:${lines[k]}" title="${esc(label.split(':')[0])}: ${lines[k]} lines"></i>`).join('');
  let units;
  if (byFile) {
    const files = [...new Set(pieces.map((p) => p.path))];
    units = files.map((f) => `<h3 class="bp-grp-file">${esc(f)}</h3>${pieces.filter((p) => p.path === f)
      .sort((a, b) => (a.start || 1e9) - (b.start || 1e9)).map(bpPiece).join('')}`).join('');
  } else {
    units = BP_GROUPS.map(([k, label, sub]) => {
      const mine = pieces.filter((p) => p.klass === k);
      if (!mine.length) return '';
      const n = mine.reduce((a, p) => a + size(p), 0);
      return `<h3 class="bp-grp-h" data-klass="${k}">${esc(label)} <span>${bpPlural(mine.length, 'piece')} · ${n} lines</span></h3>
        <p class="bp-grp-sub">${esc(sub)}</p>${mine.map(bpPiece).join('')}`;
    }).join('');
  }
  const refused = (packet.joins_refused || []).length
    ? `<p class="bp-refused">The rapporteur asked to join pieces ${packet.joins_refused.length} time${
      packet.joins_refused.length === 1 ? '' : 's'} where it could not: ${packet.joins_refused
      .map((j) => `${esc(j.ids.join(' + '))} (${esc(j.refused)})`).join('; ')}.</p>` : '';
  return `
    <section class="bp-sec" aria-label="The code">
      <p class="bp-lead">${total} lines changed. <b>${lines.novel || 0} of them are novel, a real choice someone
        has to read</b>, and they're open below. The rest is folded, and each piece says why it was classed the
        way it was. If unsure, a piece was classed novel. Every changed line is in exactly one piece.</p>
      <div class="bp-matbar" role="img" aria-label="${lines.novel || 0} novel lines of ${total}">${bar}</div>
      <div class="bp-matlegend">${legend}</div>
      <div class="bp-codebar">
        <div class="bp-seg-ctl" role="group" aria-label="Order the code">
          <button type="button" class="bp-segbtn" aria-pressed="${!byFile}" data-bp-order="klass">By materiality</button>
          <button type="button" class="bp-segbtn" aria-pressed="${byFile}" data-bp-order="file">By file</button>
        </div>
        <button type="button" class="btn btn-sm btn-quiet" data-bp-fold="open">Open all</button>
        <button type="button" class="btn btn-sm btn-quiet" data-bp-fold="close">Fold all</button>
      </div>
      ${refused}
      <div id="units"${byFile ? ' class="by-file"' : ''}>${units}</div>
    </section>`;
}

/* ---------------------------------------------------------- the presses */

async function startLook() {
  const branch = state.lookPick || '';
  const intent = (document.getElementById('look-intent') || {}).value || '';
  if (!branch) { errorToast('Pick a branch to look at.'); return; }
  const made = await api(`/api/projects/${encodeURIComponent(state.projectId)}/branches`, {
    method: 'POST', body: JSON.stringify({ branch: branch.trim(), intent: intent.trim() }),
  });
  state.lookPick = null;
  location.hash = branchHref(state.projectId, made.slug);
}

async function lookAgain() {
  const s = (state.branchLook || {}).state;
  if (!s) return;
  await api(`/api/projects/${encodeURIComponent(state.projectId)}/branches`, {
    method: 'POST', body: JSON.stringify({ branch: s.branch, intent: s.intent || '' }),
  });
  await refreshProject(true);
}

async function readAgain() {
  const slug = (state.branchLook || {}).slug;
  if (!slug) return;
  await api(`/api/projects/${encodeURIComponent(state.projectId)}/branches/${encodeURIComponent(slug)}/read`,
    { method: 'POST' });
  await refreshProject(true);
}

function branchClick(target) {
  if (target.id === 'look-start') return startLook().then(() => true);
  if (target.id === 'look-again') return lookAgain().then(() => true);
  if (target.id === 'look-read') return readAgain().then(() => true);
  if (target.dataset.lookPick !== undefined) {
    state.lookPick = state.lookPick === target.dataset.lookPick ? null : target.dataset.lookPick;
    render();
    const field = document.getElementById('look-intent');
    if (field) field.focus();
    return true;
  }
  if (target.dataset.lookCancel !== undefined) {
    state.lookPick = null;
    render();
    return true;
  }
  if (target.dataset.bpPath !== undefined) {
    state.bpPath = Number(target.dataset.bpPath);
    render();
    return true;
  }
  if (target.dataset.bpOrder) {
    state.bpOrder = target.dataset.bpOrder;
    render();
    return true;
  }
  if (target.dataset.bpFold) {
    const open = target.dataset.bpFold === 'open';
    main.querySelectorAll('#units details.bp-unit').forEach((el) => { el.open = open; });
    return true;
  }
  return false;
}
