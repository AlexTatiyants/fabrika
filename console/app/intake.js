/* A feature before it is built: the bench, the interrogator's questions, the
   plan, the spec and its change map, and the cut a person rules on. */

'use strict';

/* --------------------------------------------------------------- 1. bench */

function driftNote(drift) {
  if (!drift || !drift.checked || !drift.drifted) return '';
  const sample = (drift.sample || []).slice(0, 6);
  const bits = [];
  if (drift.files) bits.push(`${drift.modified} modified and ${drift.untracked} untracked file${drift.untracked === 1 ? '' : 's'}`);
  if (drift.ahead) bits.push(`${drift.ahead} commit${drift.ahead === 1 ? '' : 's'} on <span class="mono">${esc(drift.branch)}</span>`);
  return `<div class="drift-note">
    <p class="who"><span class="drift-mark" aria-hidden="true"></span>The agents will not see ${
      drift.ahead ? 'your branch' : 'your uncommitted work'}</p>
    <p>Every feature is built in a worktree branched from
      <span class="mono">${esc(drift.base_ref)}</span> (<span class="mono">${esc(drift.base_sha.slice(0, 12))}</span>),
      so ${bits.join(' and ')} ${drift.files + drift.ahead === 1 ? 'is' : 'are'} invisible to the
      scout, the interrogator and every worker. They will report on the code they were shown
      truthfully, which is what makes this expensive to notice.
      ${drift.ahead ? `Point this project's base ref at
        <span class="mono">${esc(drift.branch)}</span> in
        <a href="#/${encodeURIComponent(drift.project_id || state.projectId)}?gates=survey">the Survey tab</a>.` : ''}</p>
    <ul class="disclosure-list">
      ${sample.map((f) => `<li><span class="mono">${esc(f.path)}</span>
        <span class="dim">${esc(f.state)}</span></li>`).join('')}
      ${drift.files > sample.length
        ? `<li class="dim">… and ${drift.files - sample.length} more</li>` : ''}
    </ul>
    <p class="small">Or tick below if none of it bears on this feature.</p>
  </div>`;
}

/* -------------------------------------------------------- 2. interrogator */

function intakeRunningScreen() {
  const d = state.data;
  const phases = (d.phases || []).filter((p) => ['scout', 'interrogator'].includes(p.name));
  /* The same question the build screen asks, for the same reason: `stage` is a
     claim and the operating system has the answer. Without it, a reading
     whose process died would sit here saying "worth the wait" forever, while
     the rail beside it already knows the run is gone. */
  const stalled = d.orphaned;
  return `
    ${featureBar(d, { hash: '', actions: stalled
      ? `<button class="btn btn-primary btn-sm" id="reintake-stalled"
          title="Reads the repository again from scratch and asks again. An interrupted reading has no half to keep -- the scout's slices and the questions are one pass."
          >Read it again</button>`
      : liveWord(d) })}
    <div class="wrap wide">
      <div style="padding:1.5rem 0 1rem">
          ${stalled ? `<section class="stopbar disrupted" style="margin:0 0 1.25rem">
            <div class="stopbar-in">
              <span class="stopbar-w">Line stopped</span>
              <div><p class="verdict-headline"><b>Nothing is reading this repository.</b>
                The process that owned the reading is gone. Nothing has been built and nothing
                was asked of you — starting it again costs only the reading.</p></div>
            </div>
          </section>` : ''}
          <h1>${stalled ? 'The reading stopped before it could ask you anything.'
            : 'Reading the repository, then working out what to ask you.'}</h1>
          <p class="muted">${stalled
            ? `An interrupted reading has no half worth keeping: the scout's slices and the
               questions built on them are one pass, so this starts from the repository again.`
            : `The interrogator gets one round of questions and spec review closes behind it,
               so it is worth the wait. Nothing has been built and nothing will be until you freeze a spec.`}</p>
      </div>
      ${timeline(d, state.log, { waitToggle: true })}
      <div>
        <ul class="phase-list">
          ${phases.map((p) => `<li>
            <span class="dot ${esc(p.status)}"></span>
            <span><span class="phase-name ${p.status === 'pending' ? 'pending' : ''}">${esc(stepWord(p.name))}</span>
            ${p.detail ? `<span class="phase-detail"> · ${esc(p.detail)}</span>` : ''}
            ${p.error ? `<span class="phase-detail" style="color:var(--red)"> · ${esc(p.error)}</span>` : ''}</span>
            <span class="phase-time">${p.status === 'pending' ? '' : dur(p.elapsed_s)}</span>
          </li>`).join('')}
        </ul>
        ${d.scout ? `<p class="airgap-note">${esc(d.scout.summary || '')}</p>` : ''}
        ${digestNote(d)}
        ${driftNote(d.drift)}
      </div>
    </div>`;
}

function digestNote(d) {
  const c = d.digest;
  if (!c) return '';
  const pct = Math.round((c.covered || 0) * 100);
  if (c.covered >= 1) {
    return `<p class="small dim" style="margin-top:.6rem">The scout read this repository in
      ${c.slices} slice${c.slices === 1 ? '' : 's'} — ${c.files_read} of ${c.files} files,
      ${(c.chars_read / 1000).toFixed(0)}k characters, complete${c.symbols
        ? `, with all ${c.symbols} symbols indexed (${c.duplicated_symbols} defined more than once)` : ''}.</p>`;
  }
  const unread = (c.dropped_slices || []).slice(0, 8);
  return `<div class="objection" style="margin-top:1rem">
    <p class="who">The scout read ${pct}% of this repository</p>
    <p>${c.files_read} of ${c.files} files across ${c.slices} slices, chosen by relevance to your
      intent. ${c.symbols ? `All ${c.symbols} symbols were indexed, including in the parts not
      read, so "does this already exist" was still answerable.` : ''}
      ${c.slices_dropped} area${c.slices_dropped === 1 ? '' : 's'} went unread:</p>
    <ul class="disclosure-list">
      ${unread.map((d) => `<li><span class="mono">${esc(d.name || d)}</span>${
        d.files ? ` · ${d.files} files` : ''}</li>`).join('')}
      ${c.slices_dropped > unread.length
        ? `<li class="dim">… and ${c.slices_dropped - unread.length} more</li>` : ''}
    </ul>
    <p class="small">Raise <span class="mono">scout_max_slices</span> or
      <span class="mono">scout_slice_chars</span> in <span class="mono">factory.yaml</span> if the
      questions look like they were asked about a different codebase.</p>
  </div>`;
}

function planningScreen() {
  const d = state.data;
  const answered = ((d.answers || {}).resolved || []);
  const phase = (d.phases || []).find((p) => p.name === 'spec_writer') || {};
  return `
    ${featureBar(d, { hash: '', actions: liveWord(d) })}
    <div class="wrap wide">
      <div style="padding:1.5rem 0 1rem">
        <h1>Writing the spec from your answers.</h1>
        <p class="muted">Your ${answered.length} answer${answered.length === 1 ? '' : 's'} are
          recorded. When the spec writer finishes you will read the frozen spec and decide whether to
          approve it — nothing is built before that.</p>
      </div>
      ${timeline(d, state.log, { waitToggle: true })}
      <div>
        <ul class="phase-list">
          <li><span class="dot ${esc(phase.status || 'running')}"></span>
            <span class="phase-name">spec writer</span>
            <span class="phase-time">${phase.status === 'running' ? '' : dur(phase.elapsed_s || 0)}</span></li>
        </ul>
        <section class="section">
          <p class="eyebrow">What you settled</p>
          ${answered.map((a) => `<div class="rail-item">
            <div class="q">${esc(a.question)}</div>
            <div class="a">${esc(a.answer)}${a.source === 'deferred' ? ' <span class="dim">(default)</span>' : ''}</div>
          </div>`).join('') || '<p class="empty">Nothing recorded.</p>'}
        </section>
      </div>
    </div>`;
}

const AREA_ORDER = ['data', 'api', 'ui', 'behaviour', 'behavior', 'ops', ''];
const REV_WORDS = { irreversible: 'cannot be undone', hard: 'hard to undo',
                    moderate: 'undoable with effort', trivial: 'easily changed' };

const icon = (name, cls = 'ic') =>
  `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

function workedBlock(spec) {
  const before = spec.worked_before || '';
  const after = spec.worked_after || '';
  if (!before && !after) return '';
  return `<div class="worked"><div class="worked-pair">
    ${before ? `<div class="worked-side before">
      <p class="worked-when">Today</p>
      <p class="worked-text">${esc(before)}</p>
    </div>` : ''}
    ${after ? `<div class="worked-side then">
      <p class="worked-when">Once this ships</p>
      <p class="worked-text">${esc(after)}</p>
    </div>` : ''}
  </div></div>`;
}

/* The change map: one relation only, and it is the structural one.

   A screen calls an endpoint; an endpoint touches a column. Both edges are
   declared by the spec writer from names it already wrote, so the picture cannot
   claim a topology the spec never stated. Criteria are counts on a node, never
   edges -- drawing "this promise mentions that column" left-to-right alongside
   real data flow reads as the UI reaching past the API, which is a lie the
   layout tells on its own. */

const QUALIFIED = /^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$/;

/* One vocabulary across all three columns: added, changed, taken away. */
const OP_GLYPH = { add: '+', new_table: '+', index: '+', drop: '−', remove: '−', alter: '~' };
const glyph = (op) => OP_GLYPH[op] || '~';
const opClass = (op) => (op === 'drop' || op === 'remove' ? 'gone'
  : op === 'alter' || !op ? 'alter' : 'new');

/* Derived, never asked for: a dropped column is irreversible by definition and
   a model's opinion on that adds nothing. Mirrors irreversible_changes(). */
function irreversibleChanges(spec) {
  const c = spec.changes;
  if (!c) return [];
  return [
    ...(c.data || []).filter((d) => d.operation === 'drop')
      .map((d) => (d.column ? `${d.table}.${d.column}` : d.table)),
    ...(c.interface || []).filter((i) => i.operation === 'remove')
      .map((i) => `${i.method} ${i.path}`),
    ...(c.surfaces || []).filter((x) => x.operation === 'remove').map((x) => x.where),
  ];
}

/* Two kinds of target are legitimate. A column in `changes.data` is one this
   change creates (col:). A `table.column` outside it already exists and is
   merely used (ext:) -- an endpoint writing audit_logs.action is telling you
   the blast radius, which is worth more than the silence dropping it buys.
   Only a bare name that was never declared is unusable: there is no table to
   attach it to, and inventing one is the thing this scheme exists to avoid. */
function specLinks(spec) {
  const c = spec.changes;
  if (!c) return { edges: [], unresolved: [] };
  const declared = {}, paths = {};
  (c.data || []).forEach((d) => {
    if (!d.column) return;
    const target = `col:${d.table}.${d.column}`;
    declared[d.column] = target;
    declared[`${d.table}.${d.column}`] = target;
  });
  (c.interface || []).forEach((i) => { paths[i.path] = `ep:${i.method} ${i.path}`; });

  const edges = [], unresolved = [];
  (c.interface || []).forEach((i) => (i.touches || []).forEach((raw) => {
    const name = String(raw).trim();
    if (declared[name]) edges.push([`ep:${i.method} ${i.path}`, declared[name]]);
    else if (QUALIFIED.test(name)) edges.push([`ep:${i.method} ${i.path}`, `ext:${name}`]);
    else unresolved.push(`${i.method} ${i.path} → ${name}`);
  }));
  (c.surfaces || []).forEach((x) => (x.calls || []).forEach((raw) => {
    const path = String(raw).trim();
    if (paths[path]) edges.push([`sf:${x.where}`, paths[path]]);
    else unresolved.push(`${x.where.split(' (')[0]} → ${path}`);
  }));
  return { edges, unresolved };
}

/* The one inference kept, and only because an endpoint path is a long unique
   string rather than a word: how many promises are about each endpoint. It is
   a number on a node, so it cannot imply a wrong shape. */
function promiseCounts(spec) {
  const out = {};
  ((spec.changes || {}).interface || []).forEach((i) => {
    if (!i.path || i.path.length < 8 || !i.path.includes('/')) return;
    out[`ep:${i.method} ${i.path}`] = (spec.acceptance_criteria || [])
      .filter((c) => (c.statement || '').includes(i.path)).length;
  });
  return out;
}

function changeMap(spec) {
  const { edges, unresolved } = specLinks(spec);
  if (!edges.length) return '';

  const c = spec.changes;
  const surfaces = c.surfaces || [];
  const iface = c.interface || [];
  const data = (c.data || []).filter((d) => d.column);
  const counts = promiseCounts(spec);

  const SW = 190, EW = 268, DW = 290;
  const SX = 0, EX = 280, DX = 668, W = DX + DW;
  const TOP = 34, ROW = 26, NH = 20, EH = 34, GAP = 12;

  const pos = {};
  const rows = [];
  let y = TOP;
  const tables = [];
  data.forEach((d) => {
    let t = tables.find((x) => x.table === d.table);
    if (!t) { t = { table: d.table, cols: [] }; tables.push(t); }
    t.cols.push(d);
  });
  tables.forEach(({ table, cols }) => {
    rows.push({ kind: 'label', text: table, y });
    y += 20;
    cols.forEach((d) => {
      const id = `col:${d.table}.${d.column}`;
      pos[id] = [DX, y, DW, NH];
      rows.push({ kind: 'col', id, d, y });
      y += ROW;
    });
    y += 10;
  });

  // Columns on tables this change does not alter, grouped apart so nothing on
  // the right reads as new when it is not.
  const existing = [...new Set(edges.map(([, b]) => b).filter((b) => b.startsWith('ext:')))].sort();
  if (existing.length) {
    y += 6;
    rows.push({ kind: 'label', text: 'existing · not changed', y, muted: true });
    y += 20;
    existing.forEach((id) => {
      pos[id] = [DX, y, DW, NH];
      rows.push({ kind: 'ext', id, y });
      y += ROW;
    });
  }

  // Barycentre: put a node level with the average of what it connects to.
  const bary = (id, fallback) => {
    const ys = edges.filter(([a, b]) => a === id || b === id)
      .map(([a, b]) => pos[a === id ? b : a]).filter(Boolean).map((p) => p[1]);
    return ys.length ? ys.reduce((x, n) => x + n, 0) / ys.length : fallback;
  };
  const stack = (items, x, w, h) => {
    let cursor = TOP;
    items.map((it) => ({ it, at: bary(it.id, TOP) }))
      .sort((a, b) => a.at - b.at)
      .forEach(({ it, at }) => {
        const top = Math.max(cursor, at - h / 2);
        pos[it.id] = [x, top, w, h];
        cursor = top + h + GAP;
      });
  };
  stack(iface.map((i) => ({ id: `ep:${i.method} ${i.path}`, i })), EX, EW, EH);
  stack(surfaces.map((x) => ({ id: `sf:${x.where}`, x })), SX, SW, EH);

  const H = Math.max(y, ...Object.values(pos).map((p) => p[1] + p[3])) + 20;
  const existingCount = existing.length;

  /* SVG text neither wraps nor clips, and a route is the one thing here that
     must not be truncated -- /trials/{id}/slots and /participations/{id}/slots
     differ only in the part an ellipsis would eat. Squeeze it to fit instead. */
  const fit = (text, room, size = 6.3) =>
    text.length * size > room
      ? ` textLength="${Math.round(room)}" lengthAdjust="spacingAndGlyphs"` : '';

  const curve = ([a, b]) => {
    const [ax, ay, aw, ah] = pos[a], [bx, by, bw, bh] = pos[b];
    const x1 = ax + aw, y1 = ay + ah / 2, x2 = bx, y2 = by + bh / 2;
    const dx = Math.max(40, (x2 - x1) * 0.45);
    return `M${x1},${y1.toFixed(1)} C${x1 + dx},${y1.toFixed(1)} ${x2 - dx},${y2.toFixed(1)} ${x2},${y2.toFixed(1)}`;
  };

  const drawn = edges.filter(([a, b]) => pos[a] && pos[b]);
  const box = (id, inner) => {
    const [x, yy, w, h] = pos[id];
    return `<g class="n" data-id="${esc(id)}">
      <rect x="${x}" y="${yy}" width="${w}" height="${h}" rx="3"/>${inner(x, yy, w, h)}</g>`;
  };

  return `
    <figure class="cmap-wrap">
      <svg class="cmap" viewBox="0 0 ${W} ${Math.round(H)}" role="img"
        aria-label="Which screens call which endpoints, and which columns those endpoints touch">
        <text class="head" x="${SX}" y="16">SEEN AT · ${surfaces.length}</text>
        <text class="head" x="${EX}" y="16">ENDPOINTS · ${iface.length}</text>
        <text class="head" x="${DX}" y="16">DATA · ${data.length}</text>
        ${drawn.map(([a, b]) => `<path class="edge" d="${curve([a, b])}"
          data-a="${esc(a)}" data-b="${esc(b)}"/>`).join('')}
        ${surfaces.map((x) => box(`sf:${x.where}`, (px, py, w, h) => `
          <text class="op ${opClass(x.operation)}" x="${px + 10}"
            y="${py + h / 2 + 4}">${glyph(x.operation)}</text>
          <text class="verb" x="${px + 24}" y="${py + 14}">SCREEN</text>
          <text class="path" x="${px + 24}" y="${py + 26}"${
            fit(x.where.split(' (')[0], w - 34)}>${esc(x.where.split(' (')[0])}</text>`)).join('')}
        ${iface.map((i) => {
          const id = `ep:${i.method} ${i.path}`;
          const n = counts[id] || 0;
          return box(id, (px, py, w, h) => `
            <text class="op ${opClass(i.operation)}" x="${px + 10}"
              y="${py + h / 2 + 4}">${glyph(i.operation)}</text>
            <text class="verb" x="${px + 24}" y="${py + 14}">${esc(i.method)}</text>
            ${n ? `<text class="tally" x="${px + w - 10}" y="${py + 14}"
              text-anchor="end">${n} promise${n === 1 ? '' : 's'}</text>` : ''}
            <text class="path" x="${px + 24}" y="${py + 26}"${
              fit(i.path, w - 34)}>${esc(i.path)}</text>`);
        }).join('')}
        ${rows.map((r) => r.kind === 'label'
          ? `<text class="tbl ${r.muted ? 'muted' : ''}" x="${DX}"
              y="${r.y + 11}">${esc(r.text)}</text>`
          : r.kind === 'ext'
          ? `<g class="n ext" data-id="${esc(r.id)}">
              <rect x="${pos[r.id][0]}" y="${r.y}" width="${DW}" height="${NH}" rx="3"/>
              <text class="name" x="${pos[r.id][0] + 10}" y="${r.y + 14}"${
                fit(r.id.slice(4), DW - 20)}>${esc(r.id.slice(4))}</text></g>`
          : box(r.id, (px, py, w) => {
              // SVG text neither wraps nor clips, so the type yields to the name.
              // The detail column below carries it either way.
              const room = (w - 40) / 6.1 - r.d.column.length;
              const type = room > r.d.type.length ? r.d.type : '';
              return `
              <text class="op ${opClass(r.d.operation)}" x="${px + 9}" y="${py + 14}">${
                glyph(r.d.operation)}</text>
              <text class="name" x="${px + 22}" y="${py + 14}">${esc(r.d.column)}</text>
              ${type ? `<text class="type" x="${px + w - 9}" y="${py + 14}"
                text-anchor="end">${esc(type)}</text>` : ''}`;
            })).join('')}
      </svg>
      <figcaption>Hover a box to isolate what it connects to. Every line was declared by the
        spec writer, not inferred from its prose.${existingCount
          ? ` ${existingCount} column${existingCount === 1 ? '' : 's'} shown dashed
             ${existingCount === 1 ? 'is' : 'are'} on tables this change does not alter.` : ''}
        ${unresolved.length ? `<details class="dropped"><summary>${unresolved.length}
          reference${unresolved.length === 1 ? '' : 's'} could not be placed and
          ${unresolved.length === 1 ? 'was' : 'were'} dropped</summary>
          <ul>${unresolved.map((u) => `<li>${esc(u)}</li>`).join('')}</ul></details>` : ''}
      </figcaption>
    </figure>`;
}

function changeCols(spec) {
  const c = spec.changes;
  const data = (c && c.data) || [];
  const iface = (c && c.interface) || [];
  const surfaces = (c && c.surfaces) || [];
  if (!data.length && !iface.length && !surfaces.length) return '';

  // The marks are used by the map and all three panels, so the key belongs to
  // the section rather than to any one of them.
  const dataPanel = data.length ? `
    ${Object.entries(data.reduce((acc, d) => {
      (acc[d.table] || (acc[d.table] = [])).push(d); return acc;
    }, {})).map(([table, cols]) => `
      <div class="schema">
        <p class="schema-table mono">${esc(table)}</p>
        ${cols.map((d) => `<p class="schema-col mono ${esc(d.operation)}">
          <span class="op">${glyph(d.operation)}</span>
          <span class="name">${esc(d.column || d.table)}</span>
          <span class="type">${esc(d.type)}</span>
          <span class="null">${d.nullable ? 'NULL' : 'NOT NULL'}</span>
        </p>${d.note ? `<p class="schema-note">${esc(d.note)}</p>` : ''}`).join('')}
      </div>`).join('')}` : '';

  const ifacePanel = iface.length ? iface.map((i) => `
    <div class="iface ${opClass(i.operation)}">
      <p class="mono"><span class="op">${glyph(i.operation)}</span><b>${esc(i.method)}</b>
        ${esc(i.path)}</p>
      <p class="iface-change">${esc(i.change)}</p>
    </div>`).join('') : '';

  const surfacePanel = surfaces.length ? surfaces.map((x) => {
    // "TrialDetail page (frontend/src/pages/TrialDetail.tsx)" -- the name is
    // what you read, the path is what you open.
    const cut = x.where.indexOf(' (');
    const name = cut > 0 ? x.where.slice(0, cut) : x.where;
    const path = cut > 0 ? x.where.slice(cut + 1) : '';
    return `<div class="iface ${opClass(x.operation)}">
      <p><span class="op">${glyph(x.operation)}</span><b>${esc(name)}</b>${
        path ? `<br><span class="where-path">${esc(path)}</span>` : ''}</p>
      ${x.change ? `<p class="iface-change">${esc(x.change)}</p>` : ''}
    </div>`;
  }).join('') : '';

  return `
    <p class="oplegend">
      <span><span class="op new">+</span>new</span>
      <span><span class="op alter">~</span>changed</span>
      <span><span class="op gone">\u2212</span>removed</span>
    </p>
    ${changeMap(spec)}
    ${docTabs('touches', [
      ['data', 'Data', data.length, dataPanel],
      ['iface', 'Interface', iface.length, ifacePanel],
      ['surfaces', 'Where you see it', surfaces.length, surfacePanel],
    ])}`;
}

function criteriaByArea(criteria, skipped = {}) {
  const groups = {};
  criteria.forEach((c) => { (groups[c.area || ''] || (groups[c.area || ''] = [])).push(c); });
  const keys = Object.keys(groups).sort(
    (a, b) => (AREA_ORDER.indexOf(a) + 1 || 99) - (AREA_ORDER.indexOf(b) + 1 || 99));

  return keys.map((area) => {
    const rows = groups[area].map((c) => {
      const open = state.openCriterion === c.id;
      const rev = c.reversibility;
      const heavy = rev === 'irreversible' || rev === 'hard';
      return `
        <button class="ac-row ${open ? 'open' : ''} ${heavy ? 'heavy' : ''}"
          data-open-criterion="${esc(c.id)}" aria-expanded="${open}">
          <span class="ac-code mono">${esc(c.id)}</span>
          ${/* The title is the criterion as a person would say it, and the
               schema says in as many words that it is "the only part most
               readers see". The statement is the test author's register --
               file paths and index definitions -- and rendering it here would
               leave the title written by the spec writer and read by nobody. The
               statement is one click away, where the schema puts it:
               what you open when you want to know exactly what was promised.
               A spec from before titles existed falls back to it. */''}
          <span class="ac-statement">${esc(c.title || c.statement)}${skipped[c.verified_at]
            ? `<span class="ac-you" title="Fabrika won't test this: ${esc(skipped[c.verified_at])}."
                >you check</span>` : ''}</span>
          ${rev ? `<span class="ac-rev ${esc(rev)}">${
            heavy ? icon('lock', 'ic ic-xs') : ''}${esc(REV_WORDS[rev] || rev)}</span>`
            : '<span></span>'}
          <span class="rr-chev">${open ? '−' : '+'}</span>
        </button>
        ${open ? `<div class="ac-detail">
          ${c.title && c.statement ? `<p><b>Exactly.</b> ${esc(c.statement)}</p>` : ''}
          ${c.rationale ? `<p><b>Why.</b> ${esc(c.rationale)}</p>` : ''}
          ${c.verification ? `<p><b>Verified by.</b> ${esc(c.verification)}</p>` : ''}
          ${!c.rationale && !c.verification ? '<p class="dim">No rationale or verification given.</p>' : ''}
        </div>` : ''}`;
    }).join('');
    return `<div class="ac-group">
      ${keys.length > 1 ? `<p class="eyebrow ac-group-label">${esc(area || 'other')} · ${groups[area].length}</p>` : ''}
      ${rows}
    </div>`;
  }).join('');
}

/* Gate 1 is one document read in one order, because it serves one decision.
   Each section answers a different question a person has before approving; the
   rail is the contents, and the counts on it are the shape of the spec. */

/* A doc section whose body is two or three lists of different kinds. Stacked
   as columns, the widest one would set the height and the narrow ones would sit
   in a lot of white; and side by side, three of them in a 720px measure gives
   each 220px for prose. One at a time, whichever you are reading gets the width
   -- the same move the frozen spec already makes. A single panel is not a
   choice, so it renders bare. */
function docTabs(secId, panels) {
  const live = panels.filter(([, , , body]) => body);
  if (!live.length) return '';
  if (live.length === 1) return live[0][3];
  const at = live.some(([id]) => id === state.docTab[secId]) ? state.docTab[secId] : live[0][0];
  return `
    <div class="ptabs doc-tabs" role="tablist">
      ${live.map(([id, label, n]) => `
        <button type="button" role="tab" aria-selected="${id === at}"
          class="ptab ${id === at ? 'on' : ''}" data-doc-tab="${esc(secId)}:${esc(id)}">
          ${esc(label)}${n ? ` <span class="fz-count">${esc(n)}</span>` : ''}</button>`).join('')}
    </div>
    <div class="ppanel" role="tabpanel">${live.find(([id]) => id === at)[3]}</div>`;
}

function docSection(id, name, count, sub, body) {
  return `
    <section class="doc-sec">
      <div class="sec-head" id="sec-${id}">${icon(id)}<h2>${esc(name)}</h2>
        ${count ? `<span class="sec-count mono">${count}</span>` : ''}</div>
      ${sub ? `<p class="sec-sub">${esc(sub)}</p>` : ''}
      ${body}
    </section>`;
}

const docList = (items) =>
  `<ul class="doc-list">${items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;


/* Plan review. The run stopped before any worker started, because the architect
   and the plan checker left something unsettled, or code measured a problem
   with the cut that nobody can argue away.

   Drawn like gate 2 rather than like a document: the person rules on
   disagreements, one position beside the other, and the plan itself is kept
   underneath for reference. What is being decided is said once, in plain
   words, before anything that needs a unit id to read. */
function cutReviewScreen() {
  const d = state.data;
  const review = d.cut_review || {};
  const plan = d.plan || {};
  const units = plan.units || [];
  const open = review.open || [];
  const facts = review.facts || [];
  const pieces = units.length === 1 ? 'one piece' : `${units.length} pieces in parallel`;
  const lede = open.length
    ? `The factory wants to build this as <b>${esc(pieces)}</b>. The architect and the plan
       checker disagree about ${open.length === 1 ? 'one thing' : `${open.length} things`}.`
    : facts.length
      ? `The factory wants to build this as <b>${esc(pieces)}</b>, and measured a problem with
         that cut that neither agent can argue away.`
      : `The factory wants to build this as <b>${esc(pieces)}</b>, and is waiting for your
         ruling on the cut.`;

  const call = (item) => {
    const o = item.objection || {};
    const a = item.answer || {};
    const architect = a.note
      ? esc(a.note)
      : `<span class="dim">${esc(item.why === 'not answered' ? 'Did not answer.'
          : 'Said it revised the plan, but nothing it names changed.')}</span>`;
    const where = [...(o.unit_ids || []), ...(o.criterion_ids || [])].join(', ');
    return `<div class="cut-call">
      <p class="cut-q">${esc(o.claim || '')}</p>
      <div class="cut-sides">
        <div><p class="cut-who">Architect</p><p>${architect}</p></div>
        <div><p class="cut-who">Plan checker</p><p>${esc(o.consequence || '')}${
          o.settled_by ? ` <span class="dim">Settled by: ${esc(o.settled_by)}</span>` : ''}</p></div>
      </div>
      ${where ? `<p class="cut-where">${esc(where)}</p>` : ''}
    </div>`;
  };

  const cut = units.map((u) => `<li><b>${esc(u.id)}</b> ${esc(u.title || '')}${
    (u.criterion_ids || []).length ? ` <span class="dim">· ${esc(u.criterion_ids.join(', '))}</span>` : ''}</li>`).join('');

  return `
    ${featureBar(d)}
    <div class="wrap cut">
      <div class="cut-head">
        <h1>${lede}</h1>
        <p class="muted">Nothing has been built. Your ruling starts the build; until then no
          worker runs. <a href="#" data-help-open="plan-review">More about plan review</a></p>
      </div>
      ${open.length ? `<section class="section">
        <p class="eyebrow">Where they disagree · ${open.length}</p>
        ${open.map(call).join('')}
      </section>` : ''}
      ${facts.length ? `<section class="section">
        <p class="eyebrow">What code measured · ${facts.length}</p>
        <ul class="doc-list">${facts.map((f) => `<li>${
          esc(f).replace(/`([^`]+)`/g, '<code>$1</code>')}</li>`).join('')}</ul>
        <p class="muted cut-note">Facts about the cut, not opinions: a ruling to keep it is
          allowed, and is carried into the packet as a risk you accepted.</p>
      </section>` : ''}
      <section class="section">
        <p class="eyebrow">Your ruling</p>
        <div class="cut-acts">
          <button class="btn btn-sm" data-cut="keep"
            title="Build the cut as it stands. What the checker objected to goes into the packet as a risk you accepted."
            >Agree with architect</button>
          ${open.length ? `<button class="btn btn-sm" data-cut="revise"
            title="The architect re-cuts once, told the plan checker is right. If code still measures a problem, you are asked again."
            >Agree with checker</button>` : `<button class="btn btn-sm" data-cut="revise"
            title="The architect re-cuts once, told to fix what was measured. If code still measures a problem, you are asked again."
            >Re-cut to fix it</button>`}
        </div>
        <textarea id="cut-note" class="cut-note-input" rows="3"
          placeholder="Or neither: say how you would cut it, and the architect re-cuts that way."
          >${esc(state.cutNote || '')}</textarea>
        <div class="cut-acts">
          <button class="btn btn-sm" data-cut="alternative"
            title="Sends what you wrote above to the architect as a ruling. If code still measures a problem with the new cut, you are asked again."
            >Propose alternative</button>
        </div>
      </section>
      <details class="section cut-plan">
        <summary>The cut: ${units.length} unit${units.length === 1 ? '' : 's'}, ${
          (plan.seams || []).length} seam${(plan.seams || []).length === 1 ? '' : 's'}</summary>
        ${plan.summary ? `<p>${esc(plan.summary)}</p>` : ''}
        <ul class="doc-list">${cut}</ul>
        ${(plan.seams || []).length ? `<p class="subhead">Seams</p>
          <ul class="doc-list">${plan.seams.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
      </details>
    </div>`;
}

const CUT_WORDS = {
  keep: 'Building the cut as it stands.',
  revise: 'Sent back to the architect on the checker\'s side. The build continues from there.',
  alternative: 'Sent your cut to the architect. The build continues from there.',
};

function ruleOnCut(choice) {
  const box = main.querySelector('#cut-note');
  const note = box ? box.value : (state.cutNote || '');
  if (choice === 'alternative' && !note.trim()) {
    toast('Write the cut you want first; that is what goes to the architect.');
    if (box) box.focus();
    return undefined;
  }
  return withBusy(CUT_WORDS[choice] || 'Recorded.', async () => {
    await api(`${featureUrl(state.projectId, state.id)}/cut-ruling`, {
      method: 'POST', body: JSON.stringify({ choice, note }),
    });
    state.cutNote = '';
    await refresh();
  });
}

function specScreen() {
  const d = state.data;
  const spec = d.spec || {};
  const criteria = spec.acceptance_criteria || [];
  const answers = ((d.answers || {}).resolved) || spec.resolved_answers || [];
  const corrections = d.corrections || [];
  const nonGoals = spec.non_goals || [];
  const constraints = spec.constraints || [];
  const assumptions = spec.assumptions || [];
  const open = spec.open_questions || [];

  const secs = [];
  const worked = workedBlock(spec);
  if (worked) secs.push(['practice', 'In practice', 0, '', worked]);

  const cols = changeCols(spec);
  if (cols) {
    const c = spec.changes || {};
    secs.push(['touches', 'What it touches',
      (c.data || []).length + (c.interface || []).length + (c.surfaces || []).length,
      'Declared by the spec writer and checked against what the workers actually build.', cols]);
  }
  if (criteria.length) {
    /* The criteria Fabrika won't test, said once above the list and tagged
       where each is listed, so nobody freezes a spec without seeing which of
       its promises they are signing up to check by hand. */
    const skipped = (state.data || {}).unchecked_levels || {};
    const byHand = criteria.filter((c) => skipped[c.verified_at]);
    const reasons = [...new Set(byHand.map((c) => `the ${c.verified_at} level, where ${skipped[c.verified_at]}`))];
    // e.g. "the user level, where tests can't clean up after themselves"
    const unfrozen = ((state.data || {}).state || {}).stage === 'awaiting_spec_approval';
    const banner = byHand.length ? `<div class="sk-banner">
        <p class="l"><b>${byHand.length} of these ${criteria.length} criteria won't be checked by
          Fabrika.</b> They're at ${esc(reasons.join('; and '))}.</p>
        <p>You'll get them as a checklist when you review.${unfrozen
          ? ' Freezing the spec agrees to that.' : ''}
          <a href="#" data-help-open="unchecked-levels">Why, and how to change it</a></p></div>` : '';
    secs.push(['mustdo', 'What it must do', criteria.length,
      'The complete promise. Anything not here is out of scope.',
      banner + criteriaByArea(criteria, skipped)]);
  }
  if (nonGoals.length || constraints.length) {
    secs.push(['stops', 'Where it stops', nonGoals.length + constraints.length, '',
      docTabs('stops', [
        ['not', 'Deliberately not doing', nonGoals.length, nonGoals.length ? docList(nonGoals) : ''],
        ['respect', 'Must respect', constraints.length, constraints.length ? docList(constraints) : ''],
      ])]);
  }
  const gone = irreversibleChanges(spec);
  if (assumptions.length || open.length || gone.length) {
    secs.push(['wrong', 'What could be wrong', assumptions.length + open.length + gone.length,
      'The reasons to send this back. Each one is something nobody has verified.',
      `${gone.length ? `<div class="cannot">
        <p class="subhead">${icon('lock', 'ic ic-xs')}Cannot be undone · ${gone.length}</p>
        <ul class="doc-list">${gone.map((g) => `<li>${esc(g)}</li>`).join('')}</ul>
        <p class="cannot-note">Derived from the operation, not asked for: a dropped column or a
          removed endpoint cannot be put back by editing code afterwards.</p>
      </div>` : ''}
      ${docTabs('wrong', [
        ['assuming', 'Assuming', assumptions.length, assumptions.length ? docList(assumptions) : ''],
        ['nobody', 'Nobody decided', open.length, open.length ? docList(open) : ''],
      ])}`]);
  }
  if (answers.length || corrections.length) {
    const parts = [];
    if (answers.length) parts.push(`${answers.length} answer${answers.length === 1 ? '' : 's'} you gave`);
    if (corrections.length) parts.push(`${corrections.length} correction${corrections.length === 1 ? '' : 's'}`);
    secs.push(['decided', 'How this was decided', answers.length + corrections.length, '',
      `<details class="decided">
        <summary>${esc(parts.join(', and '))} — open if something above looks wrong</summary>
        <div class="decided-body">
          ${answers.map((a) => `<div class="settled">
            <div class="settled-q">${esc(a.question)}</div>
            <div class="settled-a">${esc(a.answer)}${
              a.source === 'deferred' ? ' <span class="pill">default</span>' : ''}</div>
          </div>`).join('')}
          ${corrections.map((c) => `<div class="settled">
            <div class="settled-q">You corrected how this was read</div>
            <div class="settled-a">${esc(typeof c === 'string' ? c : (c.text || c.correction || ''))}</div>
          </div>`).join('')}
        </div>
      </details>`]);
  }

  return `
    ${featureBar(d, { heading: true,
        actions: `
          <button class="btn btn-quiet btn-sm" id="start-correction">Send it back</button>
          ${planActions()}`,
      })}
      ${planWarning()}
    <div class="wrap doc-wrap">
      ${/* Inside the document, because it is the document's control and not the
           console's: what it folds away is everything that is not this spec.
           It sticks to the top of the column so a reader forty criteria down
           is still one click from the way out. */''}
      <div class="doc-tools">${focusToggle()}</div>

      ${/* The board names the feature, the same as every other screen; the
           spec's own headline is this document's title and belongs on it. In
           the board it would truncate at 40 characters and displace the only
           name that is stable across the six screens. */''}
      ${spec.title && spec.title !== d.state.title
        ? `<h2 class="spec-title">${esc(spec.title)}</h2>` : ''}
      ${d.spec_model ? `<p class="small dim" style="margin:-.2rem 0 .6rem">Planned by
        ${esc(shortModel(d.spec_model))}</p>` : ''}
      <p class="spec-summary">${esc(spec.summary || '')}</p>
      ${corrections.length ? `<p class="small dim" style="margin-top:.6rem">Your reading was corrected
        ${corrections.length} time${corrections.length === 1 ? '' : 's'} before this was written.</p>` : ''}

      ${state.correcting && !browsing() ? `
        <div class="card needs-key sendback" style="margin:1.25rem 0 0">
          <p class="small"><b>Send it back.</b> Say what this spec has wrong. The interrogator asks
            again from your correction and the spec is rewritten — nothing has been built yet.</p>
          <textarea id="correction" class="answer-field" rows="6"
            placeholder="What this gets wrong."></textarea>
          <div class="d-actions">
            <button class="btn btn-sm btn-quiet" id="cancel-correction">Cancel</button>
            <button class="btn btn-sm btn-primary" id="send-correction">Ask again from this</button>
          </div>
        </div>` : ''}

      <div class="doc">
        <div class="doc-body">
          ${secs.length
            ? secs.map(([id, name, n, sub, body]) => docSection(id, name, n, sub, body)).join('')
            : '<p class="empty">This spec is empty.</p>'}
        </div>
        <nav class="toc" aria-label="Contents">
          <p class="toc-label">Contents</p>
          ${secs.map(([id, name, n]) => `<a class="toc-item" href="#sec-${id}">
            ${icon(id, 'ic ic-sm')}<span class="toc-name">${esc(name)}</span>
            ${n ? `<span class="toc-count">${n}</span>` : ''}</a>`).join('')}
        </nav>
      </div>
    </div>`;
}
