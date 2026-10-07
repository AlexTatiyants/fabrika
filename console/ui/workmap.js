/* What was asked for, and what was built — the first tab of a review.
 *
 * Twenty-one criteria, twenty files and ninety-seven links between them on one
 * real packet. The edge count is the whole design problem: one file answered
 * to thirteen criteria, so a diagram that draws every link at once is a knot
 * that answers nothing.
 *
 * So the gutter always answers a question rather than showing what it knows:
 * one of the four states, one requirement, one file, or every objection. A
 * state's whole sheaf is drawn and faded by its own size, so density reads as
 * density; a single row's links are drawn emphatic. Nothing is ever drawn
 * merely because it exists.
 *
 * Every class here is `wm-` prefixed. `.board`, `.rail`, `.card` and `.ic` are
 * all already taken in this console -- by the andon board, the feature rail,
 * the yard cards and the icon sprites -- and a bare `.board { display: grid }`
 * from this screen would restyle the lamps at the top of every page.
 */

import { html, useEffect, useRef, useState } from '../vendor/preact-htm.module.js';
import { Pill, SEV_RANK, findingsWithRecords, isRestatement, packetOf } from './shared.js';
import { critIc, fileIc } from './marks.js';

const MAT_RANK = { novel: 0, conventional: 1, mechanical: 2, generated: 3 };
/* A path cannot contain a newline and neither can a criterion id, so it is
   the one separator that cannot collide with either half of the key. */
const KEY = (c, f) => `${c}\n${f}`;

/* Everything below is a join. The packet decided what is traced, what is novel
   and what each finding names; this only puts them next to each other. */
function graph(data) {
  const packet = packetOf(data);
  const qa = {};
  ((data.qa || {}).results || []).forEach((r) => { qa[r.criterion_id] = r.status; });

  const criteria = (packet.trace || []).map((t) => ({
    id: t.criterion_id,
    text: t.statement,
    files: t.implementing_files || [],
    tests: (t.test_names || []).length,
    state: t.status === 'orphan_requirement' ? 'no_code'
      : t.status === 'untested' ? 'no_test'
      : ({ passed: 'verified', failed: 'failing', no_test: 'no_test' })[qa[t.criterion_id]]
        || 'unsettled',
  }));

  /* A file list has no order of its own -- the packet's is the rapporteur's
     arrangement -- so it is sorted into the one that answers "what do I have
     to read". Criteria keep their ids, which is an ordering a human holds. */
  const files = (packet.materiality || []).slice().sort((a, b) =>
    (MAT_RANK[a.klass] ?? 9) - (MAT_RANK[b.klass] ?? 9) || a.path.localeCompare(b.path));
  const known = new Set(files.map((f) => f.path));

  const edges = [];
  criteria.forEach((c) => c.files.forEach((p) => {
    if (known.has(p)) edges.push({ c: c.id, f: p, finds: [] });
  }));
  const byKey = {};
  edges.forEach((e) => { byKey[KEY(e.c, e.f)] = e; });

  /* A finding naming both a criterion and a file can be drawn on the link
     between them: the panel saying *this code, written for that requirement,
     is what I am objecting to*. One that names a single end still marks it. */
  const onCriterion = {};
  const onFile = {};
  let unplaceable = 0;
  findingsWithRecords(data).filter((f) => !isRestatement(f)).forEach((f) => {
    (f.criterion_ids || []).forEach((c) => { (onCriterion[c] ||= []).push(f); });
    (f.files || []).forEach((p) => { if (known.has(p)) (onFile[p] ||= []).push(f); });
    (f.criterion_ids || []).forEach((c) => (f.files || []).forEach((p) => {
      const e = byKey[KEY(c, p)];
      if (e) e.finds.push(f);
    }));
    if (!(f.criterion_ids || []).length && !(f.files || []).length) unplaceable += 1;
  });

  /* Kept to the files the right-hand column actually draws, so a state can
     never name a row that is not there to be lit. */
  const unclaimed = (packet.unclaimed || []).map((u) => u.path).filter((path) => known.has(path));

  return { criteria, files, edges, onCriterion, onFile, unplaceable, unclaimed };
}

const bySeverity = (list) => (list || []).slice()
  .sort((a, b) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9));

function Marks({ list }) {
  const l = bySeverity(list);
  if (!l.length) return null;
  return html`
    <span class="wm-marks">
      ${l.slice(0, 6).map((f) => html`
        <span key=${f.id} class=${`wm-mark wm-mark-${f.severity}`}
              title=${`${f.id}: ${f.title}`} />`)}
    </span>`;
}

/* The four ways a requirement and the code can stand to each other, best to
   worst. The first three partition the criteria; the fourth counts files,
   because code nobody asked for has no criterion to be counted under. Colour
   is the *kind*, not the size -- a zero under `best` is not good news. */
function states(g) {
  return [
    { key: 'verified', tone: 'good', tag: 'best', unit: 'criteria',
      w: 'asked for, built, and verified',
      ids: g.criteria.filter((c) => c.files.length && c.state === 'verified').map((c) => c.id),
      why: 'Code was written for each of these and a blind test, written without sight of that '
         + 'code, ran and passed. The only state here that is evidence rather than intent.' },
    { key: 'unconfirmed', tone: 'maybe', tag: 'potentially good', unit: 'criteria',
      w: 'asked for and built, <b>but</b> nothing confirms it',
      ids: g.criteria.filter((c) => c.files.length && c.state !== 'verified').map((c) => c.id),
      why: 'Code was written and nothing independent has confirmed it — the test failed, never '
         + 'ran, or was never written. They may all be correct; this run cannot say so.' },
    { key: 'unbuilt', tone: 'bad', tag: 'bad', unit: 'criteria',
      w: 'asked for, and <b>not</b> built',
      ids: g.criteria.filter((c) => !c.files.length).map((c) => c.id),
      why: 'You asked for these and the branch contains nothing that serves them. Computed by '
         + 'inverting the matrix, so nothing reaches this list because an agent mentioned it '
         + 'and nothing escapes it by staying quiet.' },
    /* `packet.unclaimed` rather than the files no edge here touches. The two
       are meant to agree -- `compute_unclaimed` inverts the same index
       `compute_trace` walks forwards -- and that is exactly why this should
       read the answer rather than compute a second one beside it. This is
       the only screen unclaimed files appear on. */
    { key: 'unasked', tone: 'odd', tag: 'weird', unit: 'files',
      w: 'built, <b>but</b> nothing asked for it',
      ids: g.unclaimed,
      why: 'Written by this run and named by no criterion. Computed by inverting the matrix, so a '
         + 'file cannot reach this list because an agent mentioned it and cannot escape it by '
         + 'staying quiet. Sometimes plumbing the spec did not think to name; sometimes scope '
         + 'nobody agreed to.' },
  ];
}

/* The same change in the system's terms rather than the file's: computed from
   the as-built read at the commit the feature branched from and at its head.
   Absent until both readings exist, and then only when something moved. */
function SystemChange({ change, projectId }) {
  if (!change || !(change.lines || []).length) return null;
  return html`
    <section class="wm-system">
      <h4 class="wm-system-h">What this changes in the system</h4>
      <ul>${change.lines.map((line) => html`<li key=${line}>${line}</li>`)}</ul>
      ${projectId ? html`<a class="wm-system-a" href=${`#/${encodeURIComponent(projectId)}?gates=as-built`}>
        The as-built of this project</a>` : null}
    </section>`;
}

export function WorkMap({ data, hrefs }) {
  const g = graph(data);
  const bands = states(g);
  const novel = g.files.filter((f) => f.klass === 'novel');
  const rest = g.files.filter((f) => f.klass !== 'novel');

  /* Opens on the worst state anything is actually in, computed rather than
     picked: a board drawn empty reads as a board with nothing on it, and
     opening on `verified` when nothing is verified opens on a blank one. */
  const opening = ['unbuilt', 'unasked', 'unconfirmed', 'verified']
    .find((k) => bands.find((b) => b.key === k).ids.length) || 'verified';

  const [pick, setPick] = useState({ kind: 'state', id: opening });
  const [folded, setFolded] = useState(true);
  const board = useRef(null);
  const wires = useRef(null);

  const band = pick.kind === 'state' ? bands.find((b) => b.key === pick.id) : null;

  /* Which links this pick is about. A state is a property of the rows, so it
     draws its whole sheaf; the overlay draws only what an objection names at
     both ends; a single row draws its own. */
  const lit = (() => {
    if (pick.kind === 'criterion') return g.edges.filter((e) => e.c === pick.id);
    if (pick.kind === 'file') return g.edges.filter((e) => e.f === pick.id);
    if (pick.kind === 'objections') return g.edges.filter((e) => e.finds.length);
    if (band.unit !== 'criteria') return [];
    const ids = new Set(band.ids);
    return g.edges.filter((e) => ids.has(e.c));
  })();

  /* A pick that reaches into the folded block opens it: you asked for that one
     thing and want all of it. A sweep does not -- the state this opens on
     holds every criterion, and every criterion together serves every file, so
     unfolding for those would mean the boilerplate was never folded at all. */
  useEffect(() => {
    if (!folded || (pick.kind !== 'criterion' && pick.kind !== 'file')) return;
    const inside = new Set(rest.map((f) => f.path));
    const wants = pick.kind === 'file' ? [pick.id] : lit.map((e) => e.f);
    if (wants.some((p) => inside.has(p))) setFolded(false);
  }, [pick, folded]);

  useEffect(() => {
    const svg = wires.current;
    const host = board.current;
    if (!svg || !host) return undefined;

    const paint = () => {
      const b = host.getBoundingClientRect();
      const w = svg.getBoundingClientRect().width;
      const midY = (node) => {
        const r = node.getBoundingClientRect();
        return r.top - b.top + r.height / 2;
      };
      const one = pick.kind === 'criterion' || pick.kind === 'file';
      const fade = Math.max(0.22, Math.min(0.8, 14 / (lit.length || 1)));
      const parts = [];
      for (const e of lit) {
        const cn = host.querySelector(`[data-c="${CSS.escape(e.c)}"]`);
        const fn = host.querySelector(`[data-f="${CSS.escape(e.f)}"]`);
        /* A hidden row has no box, so `getBoundingClientRect` is all zeros and
           `midY` returns minus the board's own offset -- the line leaves the
           top of the page. That is what happens when a criterion serves a
           file inside the folded block. */
        if (!cn || !fn || !cn.offsetParent || !fn.offsetParent) continue;
        const y1 = midY(cn);
        const y2 = midY(fn);
        const bad = bySeverity(e.finds)[0];
        parts.push(
          `<path class="wm-wire ${bad ? `wm-wire-${bad.severity}`
            : one ? 'wm-wire-selected' : 'wm-wire-plain'}"`
          + ` d="M0,${y1} C${w * 0.45},${y1} ${w * 0.55},${y2} ${w},${y2}"`
          + ` stroke-width="${bad ? 2 : one ? 1.75 : 1.25}"`
          // Density has to read as density: a hundred lines at full strength
          // is a black mass, and the same hundred faded read as a sheaf.
          + ` opacity="${bad ? 0.95 : fade}" />`,
        );
      }
      svg.innerHTML = parts.join('');
    };

    paint();
    const ro = new ResizeObserver(paint);
    ro.observe(host);
    return () => ro.disconnect();
  });

  const litC = new Set(lit.map((e) => e.c));
  const litF = new Set(lit.map((e) => e.f));

  const cls = (side, id) => {
    const selected = (pick.kind === 'criterion' && side === 'c' && pick.id === id)
      || (pick.kind === 'file' && side === 'f' && pick.id === id);
    if (selected) return 'sel';
    if (pick.kind === 'criterion' || pick.kind === 'file' || pick.kind === 'objections') {
      return (side === 'c' ? litC.has(id) : litF.has(id)) ? 'lit' : 'off';
    }
    if (band.unit === 'criteria') {
      return side === 'c'
        ? (band.ids.includes(id) ? 'lit' : 'off')
        : (litF.has(id) ? 'lit' : 'off');
    }
    return side === 'f' && band.ids.includes(id) ? 'lit' : 'off';
  };

  const fileRow = (f) => html`
    <div key=${f.path} data-f=${f.path}
         class=${`wm-node ${cls('f', f.path)}`}
         onClick=${() => setPick({ kind: 'file', id: f.path })}>
      ${fileIc(f.klass)}
      <span class="mono wm-node-t" title=${f.path}>${f.path}</span>
      <span class="wm-node-r">
        <${Marks} list=${g.onFile[f.path]} />
        <span class="mono wm-node-n">
          <span class="add">+${f.added || 0}</span> <span class="rem">−${f.removed || 0}</span>
        </span>
        <span class="mono wm-node-n" title="criteria served">
          ${g.edges.filter((e) => e.f === f.path).length}
        </span>
      </span>
      <a class="wm-open" href=${hrefs.file(f.path)} onClick=${(e) => e.stopPropagation()}
         title=${`Read the diff for ${f.path}`}>↗ open</a>
    </div>`;

  const restLines = rest.reduce((s, f) => s + (f.added || 0) + (f.removed || 0), 0);
  const restKinds = [...new Set(rest.map((f) => f.klass))]
    .map((k) => `${rest.filter((f) => f.klass === k).length} ${k}`).join(' · ');

  return html`
    <div class="wrap">
      <p class="tab-lead">How each acceptance criterion connects to the files this run changed.</p>
      <${SystemChange} change=${data.system_change} projectId=${(data.state || {}).project_id} />

      <div class="wm-gaps">
        ${bands.map((b) => html`
          <button key=${b.key} type="button"
                  class=${`wm-gap wm-gap-${b.tone} ${pick.kind === 'state' && pick.id === b.key ? 'on' : ''}`}
                  onClick=${() => setPick({ kind: 'state', id: b.key })}>
            <span class="wm-gap-top">
              <span class="wm-gap-n">${b.ids.length}</span>
              <span class="mono wm-gap-unit">${b.unit}</span>
              <span class="wm-gap-tag">${b.tag}</span>
            </span>
            <span class="wm-gap-w" dangerouslySetInnerHTML=${{ __html: b.w }} />
          </button>`)}
      </div>

      <div class="wm-overlay-row">
        <button type="button"
                class=${`wm-overlay-btn ${pick.kind === 'objections' ? 'on' : ''}`}
                onClick=${() => setPick({ kind: 'objections' })}>
          Show where objections land
        </button>
        <span class="wm-overlay-note">
          ${g.edges.filter((e) => e.finds.length).length} of ${g.edges.length} links carry one
        </span>
      </div>

      <div class="wm-board" ref=${board}>
        <div class="wm-col">
          <div class="wm-head">
            <h3>Asked for</h3>
            <span class="mono dim">${g.criteria.length} criteria</span>
          </div>
          <div class="wm-key">
            <span>${critIc('verified')}tested</span>
            <span>${critIc('failing')}failed test</span>
            <span>${critIc('no_test')}untested</span>
            <span><span class="wm-marks"><span class="wm-mark wm-mark-blocker" /></span>objections</span>
          </div>
          ${g.criteria.map((c) => html`
            <div key=${c.id} data-c=${c.id}
                 class=${`wm-node ${cls('c', c.id)}`}
                 onClick=${() => setPick({ kind: 'criterion', id: c.id })}>
              ${critIc(c.state)}
              <span class="wm-node-t" title=${c.text}>
                <span class="mono wm-node-id">${c.id}</span> ${c.text}
              </span>
              <span class="wm-node-r">
                <${Marks} list=${g.onCriterion[c.id]} />
                <span class="mono wm-node-n">${c.files.length}</span>
              </span>
              <a class="wm-open" href=${hrefs.criterion(c.id)}
                 onClick=${(e) => e.stopPropagation()}
                 title=${`Read ${c.id} with the code that serves it`}>↗ open</a>
            </div>`)}
        </div>

        <div class="wm-gutter">
          <div class="wm-head wm-gutter-head"><span class="mono dim">links</span></div>
          <svg class="wm-wires" ref=${wires} aria-hidden="true" />
        </div>

        <div class="wm-col">
          <div class="wm-head">
            <h3>Built</h3>
            <span class="mono dim">
              <b class="wm-need">${novel.length}</b> of ${g.files.length} need your eyes
            </span>
          </div>
          <div class="wm-key">
            <span>${fileIc('novel')}<b>novel code, needs human review</b></span>
            <span>${fileIc('conventional')}boilerplate, can skip</span>
          </div>
          ${novel.map(fileRow)}
          ${rest.length > 0 && html`
            <button type="button" class="wm-fold" aria-expanded=${folded ? 'false' : 'true'}
                    onClick=${() => setFolded(!folded)}>
              <span class="wm-fold-w">${rest.length} files you can skip</span>
              <span class="mono wm-fold-n">${restLines} lines · ${restKinds}</span>
              <span class="mono wm-fold-c">${folded ? 'show' : 'hide'}</span>
            </button>`}
          ${!folded && rest.map(fileRow)}
        </div>
      </div>

      <div class="wm-legend">
        <span class="wm-legend-lab">The lines</span>
        <span><span class="wm-swatch wm-swatch-plain" />this file was written for that requirement</span>
        <span><span class="wm-swatch wm-swatch-blocker" />a blocker names both ends</span>
        <span><span class="wm-swatch wm-swatch-major" />a major names both ends</span>
      </div>

      <${Reader} g=${g} pick=${pick} band=${band} lit=${lit} hrefs=${hrefs}
                 folded=${folded} rest=${rest} />

      ${/* The way out of the tabs altogether. This is the landing screen,
           so it is the screen the link belongs at the foot of. */''}
      <p class="wm-tofull">
        <a href=${hrefs.packet()}>The whole packet on one page</a> — everything the tabs hold, in
        the order the rapporteur ranked it, for reading straight through or printing.
      </p>
    </div>`;
}

/* What the pick is, in full, under the board. Every selection also carries its
   way out on the row itself -- this board is six hundred pixels tall, so a
   reader who picks the third row would never see a panel down here. */
function Reader({ g, pick, band, lit, hrefs, folded, rest }) {
  if (pick.kind === 'criterion') {
    const c = g.criteria.find((x) => x.id === pick.id);
    const finds = g.onCriterion[pick.id] || [];
    return html`
      <div class="wm-reader">
        <div class="wm-reader-head">
          <h3>${c.id} · ${c.state.replace('_', ' ')}</h3>
          <div class="wm-reader-acts">
            <a class="btn btn-sm btn-primary" href=${hrefs.criterion(c.id)}>Review ${c.id} →</a>
          </div>
        </div>
        <p class="wm-reader-sub">${c.text}</p>
        <div class="wm-reader-cols">
          <div>
            <div class="eyebrow">${lit.length} file(s) written for it</div>
            ${lit.map((e) => {
              const f = g.files.find((x) => x.path === e.f);
              return html`
                <div key=${e.f} class="wm-rrow">
                  ${fileIc(f.klass)}
                  <a class="mono" href=${hrefs.file(f.path)}>${f.path}</a>
                  <span class="dim">${f.reason}</span>
                </div>`;
            })}
            ${!lit.length && html`<p class="empty">Nothing implements this.</p>`}
          </div>
          <div>
            <div class="eyebrow">
              ${finds.length} objection(s) naming it${c.tests ? '' : ' · no blind test'}
            </div>
            ${bySeverity(finds).map((f) => html`
              <div key=${f.id} class="wm-rrow">
                <${Pill} cls=${f.severity === 'blocker' ? 'pill-red'
                  : f.severity === 'major' ? 'pill-amber' : ''}>${f.severity}<//>
                <span>${f.title}</span>
              </div>`)}
            ${!finds.length && html`<p class="empty">Nothing was raised against it.</p>`}
          </div>
        </div>
      </div>`;
  }

  if (pick.kind === 'file') {
    const f = g.files.find((x) => x.path === pick.id);
    const finds = g.onFile[pick.id] || [];
    return html`
      <div class="wm-reader">
        <div class="wm-reader-head">
          <h3 class="mono wm-reader-path">${f.path}</h3>
          <div class="wm-reader-acts">
            <a class="btn btn-sm btn-primary" href=${hrefs.file(f.path)}>Read the diff →</a>
          </div>
        </div>
        <p class="wm-reader-sub">${f.reason}</p>
        <div class="wm-reader-cols">
          <div>
            <div class="eyebrow">${lit.length} criteri${lit.length === 1 ? 'on' : 'a'} it serves</div>
            ${lit.map((e) => {
              const c = g.criteria.find((x) => x.id === e.c);
              return html`
                <div key=${e.c} class="wm-rrow">
                  ${critIc(c.state)}
                  <a class="mono" href=${hrefs.criterion(c.id)}>${c.id}</a>
                  <span class="dim">${c.text}</span>
                </div>`;
            })}
            ${!lit.length && html`<p class="empty">No criterion accounts for this file.</p>`}
          </div>
          <div>
            <div class="eyebrow">${finds.length} objection(s) naming it</div>
            ${bySeverity(finds).map((x) => html`
              <div key=${x.id} class="wm-rrow">
                <${Pill} cls=${x.severity === 'blocker' ? 'pill-red'
                  : x.severity === 'major' ? 'pill-amber' : ''}>${x.severity}<//>
                <span>${x.title}</span>
              </div>`)}
            ${!finds.length && html`<p class="empty">Nothing was raised against it.</p>`}
          </div>
        </div>
      </div>`;
  }

  if (pick.kind === 'objections') {
    const inFold = new Set(rest.map((f) => f.path));
    const hid = folded ? lit.filter((e) => inFold.has(e.f)).length : 0;
    return html`
      <div class="wm-reader">
        <h3>Where the objections land</h3>
        <p class="wm-reader-sub">
          ${lit.length} of ${g.edges.length} links carry an objection naming both the requirement
          and the file — the panel saying <i>this code, written for that requirement, is what I am
          objecting to</i>. Colour is the worst severity on the link.
          ${hid > 0 && html`${' '}${hid} of them ${hid === 1 ? 'reaches a file' : 'reach files'}${' '}
            inside the fold and ${hid === 1 ? 'is' : 'are'} not drawn.`}
          ${g.unplaceable > 0 && html`${' '}${g.unplaceable} finding(s) name neither and cannot be
            drawn anywhere; they are in Objections.`}
        </p>
      </div>`;
  }

  const inside = new Set(rest.map((f) => f.path));
  const hidden = folded ? lit.filter((e) => inside.has(e.f)).length : 0;
  return html`
    <div class="wm-reader">
      <h3>
        ${band.ids.length} ${band.unit} —${' '}<span dangerouslySetInnerHTML=${{ __html: band.w }} />
      </h3>
      <p class="wm-reader-sub">
        ${band.why}
        ${lit.length > 0 && html`${' '}<span class="dim">${lit.length - hidden} link(s) drawn${
          hidden ? `; ${hidden} more reach files inside the fold` : ''}.</span>`}
      </p>
      ${band.ids.length
        ? html`<p class="mono wm-idlist">${band.ids.join('  ')}</p>`
        : html`<p class="empty">Nothing is in this state${band.key === 'verified'
            ? ', which on this run is the whole of the problem: every criterion was built and '
              + 'not one of them was confirmed.' : '.'}</p>`}
    </div>`;
}
