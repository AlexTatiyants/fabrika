/* What the four gate-2 screens have in common.
 *
 * The vocabulary here is the console's, not a new one: the same pills, the same
 * materiality ramp, the same accent that means "this wants you". What differs
 * is that these screens are built by a reconciler rather than by string
 * concatenation, because you type into all four of them while the page refreshes
 * underneath you.
 */

import { html, useEffect, useState, useRef } from '../vendor/preact-htm.module.js';

/* --------------------------------------------------------- the bar action

   Every screen in this tool right-aligns its primary action in the bar, and the
   bar lives outside the mounted tree -- it has to, because the tree's host is
   cleared on every mount. So the two talk through the document rather than
   through a shared global: the screen says whether its action may fire, the bar
   says when it was pressed.

   One pair of events rather than a pair per screen, because only one of these
   screens is ever mounted at a time. */

export function useBarAction(enabled, fire, deps) {
  useEffect(() => {
    document.dispatchEvent(new CustomEvent('ui:action-state', { detail: !!enabled }));
    const onFire = () => { if (enabled) fire(); };
    document.addEventListener('ui:action', onFire);
    return () => document.removeEventListener('ui:action', onFire);
  }, deps);
}


/* ------------------------------------------------------------------- api */

export const base = (p, f) =>
  `/api/projects/${encodeURIComponent(p)}/features/${encodeURIComponent(f)}`;

/* Every request these screens make goes through `api` in app/util.js, a classic
   script loaded before any module. It notices a server that has stopped
   answering -- the offline bar, and the come-back -- and reads a refusal's
   `detail` one way. A fetch wrapper of these modules' own would do neither,
   and a restart mid-review would read as the request having failed. */
export const request = (url, options) => globalThis.api(url, options);

export const send = (url, body) =>
  request(url, { method: 'POST', body: JSON.stringify(body || {}) });

/* ------------------------------------------------------------ vocabulary */

/* Two choices, and they are verbs, because pressing one is doing something:
   REPAIR or DISMISS. Finer kinds of dismissal -- needing a different spec,
   belonging to a different feature, accepted with the defect present, or
   simply wrong -- do not change where the review goes: only `repair` routes
   anywhere but plain acceptance. As buttons of their own they would cost a
   reader four labels to learn for zero difference in outcome. The reason
   typed alongside a dismissal is where that distinction is made.

   Two is a rule about the ROW, not about where the work can go. Where a
   repair goes is the factory's arithmetic, not a question to hand back: a
   repair whose file only the oracle may write is moved to the oracle by the
   arbiter (`dispose`, pipeline.py), and that shows up here as `TAG.oracle` --
   readable after the fact, never a third button. */
export const DISPOSITIONS = ['repair', 'dismissed'];

export const TAG = {
  repair: { label: 'repair', pill: 'pill-accent',
    consequence: 'the repair loop makes the narrowest change that resolves it' },
  dismissed: { label: 'dismiss', pill: '',
    consequence: 'no work for the repair loop — your reason stays on the record' },
  /* Not choosable: what the factory did with a repair, shown back. */
  oracle: { label: 'sent to the oracle', pill: 'pill-accent',
    consequence: 'the oracle rewrites its own test — the one file a repairer may not touch' },
};

/* A flag's disposition may still be one of the four choices that collapsed
   into `dismissed` above (needs_spec_change, out_of_spec, escalate,
   not_a_defect) -- reads back fine, but `TAG` has no entry for it. Every one
   of those means "not sent to the repair loop", which is exactly what
   `dismissed` means, so that is what it displays as -- not its own label,
   which would point at a button that does not exist. */
export const tagOf = (disposition) => TAG[disposition] || TAG.dismissed;

/* A flag filed without a note. The disposition is the whole of it: you read
   the finding, agreed, and had nothing to add -- so the repair loop works
   from the finding's own words (`flag_findings`, pipeline.py). Said plainly
   wherever a flag's text is shown, rather than papered over with the
   finding's title printed as though you had written it. */
export const NO_NOTE = 'No note of your own.';

/* What the human actually wrote, which may be nothing.
 *
 * `titleOf` resolves a flag's anchor to the finding it hangs off, and exists
 * for flags written by an older console, which filed the finding's own title
 * as your text when a call was ruled without typing anything
 * (`note.trim() || f.title`) -- so those packets carry flags whose words are
 * the machine's, stored as yours. The ledger is append-only and those records
 * say what they say -- so the old shape is recognised here rather than
 * rewritten there.
 *
 * A note that genuinely repeats the finding's title reads as no note. That is
 * the whole cost of the rule, and it is not a lie: those words reach the
 * repair loop either way. */
export const noteOf = (flag, titleOf) => {
  const said = String(flag.text || '').trim();
  if (!said || !titleOf || !flag.anchor) return said;
  return said === String(titleOf(flag.anchor) || '').trim() ? '' : said;
};

/* The four classes, lightest to heaviest. Only `novel` gets the accent: the
   whole point of the ramp is that your eye lands on the code nobody has seen
   before, and four coloured classes would land it nowhere. */
export const MAT = ['generated', 'mechanical', 'conventional', 'novel'];
export const MAT_BLURB = {
  generated: 'machine output, reviewed but not authored',
  mechanical: 'forced by something else — a schema, a signature, a migration',
  conventional: 'ordinary code following a pattern already in the repo',
  novel: 'a choice was made here that nobody has reviewed',
};

/* What is actually known about a criterion.
 *
 * `TraceStatus` alone is not enough for the label: `traced` means only that a
 * file claims to implement it and a blind test is tagged to it. It says
 * nothing about whether that test passed -- so on its own a packet can show
 * twelve criteria reading "traced" beside a headline reading "0 of 21 criteria
 * verified", both correct, and no way to see from the list which twelve were
 * the failures. That is the one question the list exists to answer.
 *
 * So the trace status is joined to the run's own result for the criterion and
 * the pair is stated as one word. Computed here rather than asked for: both
 * halves are the orchestrator's, and neither is a model's opinion. (INV-3)
 */
export const AC_STATE = {
  verified: { label: 'verified', pill: 'pill-green', sev: '',
    blurb: 'A blind test covers it, and the test passed.' },
  failing: { label: 'failing', pill: 'pill-red', sev: 'blocker',
    blurb: 'A blind test covers it, and the test failed.' },
  unsettled: { label: 'not settled', pill: 'pill-amber', sev: 'major',
    blurb: 'A blind test covers it and the run cannot say either way — it never executed, or '
         + 'nothing in the output bears on it. Never a pass.' },
  no_test: { label: 'no test', pill: 'pill-amber', sev: 'major',
    blurb: 'Code implements it and no blind test is tagged to it, so nothing independent '
         + 'confirms it.' },
  no_code: { label: 'no code', pill: 'pill-red', sev: 'blocker',
    blurb: 'Nothing implements it. The criterion was asked for and not built.' },
  manual: { label: 'you check', pill: 'pill-amber', sev: '',
    blurb: "Fabrika didn't test it: its test level can't run cleanly in this project. It's "
         + 'on your calls, to check by hand.' },
};

export function acState(traceStatus, qaStatus) {
  if (traceStatus === 'orphan_requirement') return 'no_code';
  if (traceStatus === 'untested') return 'no_test';
  if (traceStatus === 'manual' || qaStatus === 'manual') return 'manual';
  if (qaStatus === 'passed') return 'verified';
  if (qaStatus === 'failed') return 'failing';
  if (qaStatus === 'no_test') return 'no_test';
  // not_run, not_collected, unknown, and a run that recorded nothing at all.
  return 'unsettled';
}

export const SEVERITY_PILL = {
  blocker: 'pill-red', major: 'pill-amber', minor: '', nit: '',
};

export const esc = (s) => String(s ?? '');
export const usd = (n) => `$${Number(n || 0).toFixed(2)}`;
export const pct = (a, b) => (b ? `${(100 * a / b).toFixed(1)}%` : '0%');

/* ------------------------------------------------- joins over the packet */

/* Everything below is a join, not a judgement. The packet already decided what
   is novel, what is traced and what is unclaimed; these only put the pieces
   next to each other. */

export const packetOf = (d) => d.packet || {};

export function fileIndex(d) {
  /* path -> { klass, lines, reason, contents, unit } */
  const out = {};
  for (const m of packetOf(d).materiality || []) {
    out[m.path] = { path: m.path, klass: m.klass, lines: m.lines, reason: m.reason };
  }
  for (const w of d.workers || []) {
    for (const f of w.files || []) {
      out[f.path] = { ...(out[f.path] || { path: f.path, klass: 'novel', lines: 0, reason: '' }),
                      contents: f.contents, purpose: f.purpose, unit: w.unit_id };
    }
  }
  for (const f of (d.integration || {}).files || []) {
    out[f.path] = { ...(out[f.path] || { path: f.path, klass: 'novel', lines: 0, reason: '' }),
                    contents: f.contents, purpose: f.purpose, unit: 'integrator' };
  }
  return out;
}

export function statementOf(d, id) {
  const ac = ((d.spec || {}).acceptance_criteria || []).find((c) => c.id === id);
  return ac ? ac.statement : '';
}

/* The criterion as a person would say it, falling back to the statement.
   `statement` is written for the oracle, which has never seen the code and
   writes its blind tests from those words alone -- so it is allowed to name a
   path and a literal, and a list of thirteen of them is unreadable. `title` is
   the same criterion said out loud. A spec frozen before the field existed has
   no title, and the statement is the honest thing to show there. */
export function titleOf(d, id) {
  const ac = ((d.spec || {}).acceptance_criteria || []).find((c) => c.id === id);
  return (ac && ac.title) || (ac && ac.statement) || '';
}

export function criteriaRows(d) {
  const files = fileIndex(d);
  const packet = packetOf(d);
  const findings = packet.findings || [];
  const qa = {};
  ((d.qa || {}).results || []).forEach((r) => { qa[r.criterion_id] = r; });
  return (packet.trace || []).map((row) => {
    const paths = row.implementing_files || [];
    const mats = paths.map((p) => files[p]).filter(Boolean);
    const total = mats.reduce((s, m) => s + (m.lines || 0), 0);
    const by = (k) => mats.filter((m) => m.klass === k).reduce((s, m) => s + (m.lines || 0), 0);
    return {
      ...row,
      statement: row.statement || statementOf(d, row.criterion_id),
      title: titleOf(d, row.criterion_id) || row.statement,
      /* Only when the two are genuinely different: a spec frozen before
         `title` existed falls back to the statement, and showing the same
         sentence twice reads as a rendering bug. */
      exact: (() => {
        const ac = ((d.spec || {}).acceptance_criteria || [])
          .find((c) => c.id === row.criterion_id);
        return ac && ac.title && ac.statement !== ac.title ? ac.statement : '';
      })(),
      files: mats,
      lines: total,
      novel: by('novel'),
      widths: MAT.map((k) => pct(by(k), total)),
      findings: findings.filter((f) => (f.criterion_ids || []).includes(row.criterion_id)),
      tests: row.test_names || [],
      qa: qa[row.criterion_id] || null,
      state: acState(row.status, (qa[row.criterion_id] || {}).status),
    };
  });
}

export function evidenceFor(d, id) {
  const oracle = d.oracle || {};
  const tests = (oracle.tests || []).filter((t) => (t.criterion_ids || []).includes(id));
  const qa = ((d.qa || {}).results || []).find((r) => r.criterion_id === id);
  return {
    tests,
    qa,
    gates: ((d.gates || {}).results || []),
    findings: (packetOf(d).findings || []).filter(
      (f) => (f.criterion_ids || []).includes(id)),
    notes: oracle.notes || '',
    untestable: (oracle.untestable_criteria || []).includes(id),
  };
}

/* --------------------------------------------------------------- pieces */

export function Pill({ cls, children }) {
  return html`<span class=${`pill ${cls || ''}`}>${children}</span>`;
}

export function MatDot({ klass }) {
  return html`<span class=${`mat-dot mat-${klass}`} title=${MAT_BLURB[klass] || ''} />`;
}

/* One composer, four screens. `anchor` is what the flag will point at forever,
   so it is passed in rather than typed: a comment that has to be told where it
   came from is a comment that will one day be wrong about it. */
export function Composer({ onFile, busy, anchor = '', source = 'comment',
                           placeholder, compact = false,
                           add = '+ flag something here', anchorLabel = true }) {
  const [text, setText] = useState('');
  const [disposition, setDisposition] = useState('repair');
  const [open, setOpen] = useState(!compact);
  const box = useRef(null);

  const file = async () => {
    const body = text.trim();
    if (!body) return;
    await onFile({ text: body, disposition, source, anchor });
    setText('');
    if (compact) setOpen(false); else box.current?.focus();
  };

  if (compact && !open) {
    return html`
      <button class="rw-add" onClick=${() => { setOpen(true); setTimeout(() => box.current?.focus(), 0); }}>
        ${add}
      </button>`;
  }

  return html`
    <div class=${`rw-composer ${compact ? 'rw-composer-inline' : ''}`}>
      ${!compact && html`<div class="eyebrow">Add something you found</div>`}
      ${anchor && anchorLabel && html`<div class="mono rw-composer-anchor">on ${anchor}</div>`}
      <textarea
        ref=${box}
        rows=${compact ? 2 : 3}
        placeholder=${placeholder
          || 'What is wrong, in your words — nothing summarises it. It reaches the repair loop '
             + 'as written, or stays on the record if you dismiss it instead.'}
        value=${text}
        onInput=${(e) => setText(e.target.value)} />
      <div class="rw-composer-foot">
        <select value=${disposition} onChange=${(e) => setDisposition(e.target.value)}>
          ${DISPOSITIONS.map((dd) => html`<option key=${dd} value=${dd}>${TAG[dd].label}</option>`)}
        </select>
        <button class="btn btn-sm" disabled=${busy || !text.trim()} onClick=${file}>File it</button>
        ${compact && html`
          <button class="btn btn-quiet btn-sm" onClick=${() => { setText(''); setOpen(false); }}>
            Cancel
          </button>`}
      </div>
    </div>`;
}

/* Flags already filed against this anchor. Shown wherever you can file one, so
   you are never invited to raise the same thing twice. */
export function FlagsHere({ flags, anchor }) {
  const mine = flags.filter((f) => f.anchor === anchor);
  if (!mine.length) return null;
  return html`
    <div class="rw-here">
      ${mine.map((f) => html`
        <div key=${f.id} class="rw-here-item">
          <${Pill} cls=${TAG[f.disposition]?.pill}>${TAG[f.disposition]?.label}<//>
          <span>${f.text}</span>
        </div>`)}
    </div>`;
}

export function Empty({ children }) {
  return html`<div class="wrap"><p class="empty">${children}</p></div>`;
}

/* ------------------------------------------------------- reading mode

   The same control the frozen spec carries, drawn here through the reconciler
   instead of as a string. It owns no state: app/rail.js holds the preference, hears
   the click through one delegated listener on the document, and hands the
   answer back as a prop -- so the icon can never disagree with the mode.

   A zero-height sticky row, so the screen's own first margin is the one you
   see. Its parent has to be the tall element, not the column: sticky travels
   no further than the box it sits in.

   `bare` returns the button alone, for a screen that has a row of its own to
   put it in. Gate 2 does: it has a second control beside this one, and
   aligning the two by two different rules -- this row's 10px top padding
   against the other's 2px -- makes the kind of eight-pixel disagreement
   nobody can name and everybody can see. */
export function FocusTools({ on, bare = false }) {
  const word = on ? 'Leave reading mode' : 'Read this full screen';
  const button = html`
    <button type="button" class="focus-toggle" aria-pressed=${on}
            aria-label=${word} title=${`${word} — ${on ? 'Esc' : 'F'}`}>
      <svg class="ic" aria-hidden="true">
        <use href=${on ? '#i-l-minimize' : '#i-l-maximize'} />
      </svg>
    </button>`;
  return bare ? button : html`<div class="wrap doc-tools">${button}</div>`;
}

/* ---------------------------------------------------------- the review's row

   Findings, criteria and blind spots are the same object on screen: a thing
   with a severity, a one-line claim, and a state it ended up in. Twenty-four
   findings at six lines each is a page nobody finishes, and inside it the
   ordering that matters -- attempted and still there, first -- is invisible.
   Collapsed, each is one scannable line; everything that makes it arguable is
   one click away.

   `<details>` rather than state, so the browser owns which rows are open and a
   poll landing mid-read cannot close one. */

export function Row({ id, sev = '', pill, pillCls = '', pillTitle = '',
                      title, meta, children, open = false }) {
  return html`
    <details class=${`drow ${sev ? `drow-${sev}` : ''}`} open=${open}>
      <summary>
        <span class="drow-chev" aria-hidden="true">›</span>
        <span class="mono drow-id">${id}</span>
        <span class=${`pill ${pillCls}`} title=${pillTitle}>${pill}</span>
        <span class="drow-t">${title}</span>
        <span class="mono drow-meta">${meta}</span>
      </summary>
      <div class="drow-body">${children}</div>
    </details>`;
}

/* What became of a finding, in the words a reader needs rather than the enum.
   The same table `app/review.js` uses for the one-page packet -- two renderings of one
   record must not name the same outcome differently. */
export const OUTCOME = {
  repaired: ['repaired', 'pill-green'],
  /* Raised by one of the factory's own checks, which ran again and no longer
     finds it. Settled, but not a repair: nothing was fixed for it. */
  no_longer_holds: ['no longer holds', 'pill-green'],
  /* A condition of the run, not a defect in the work: which agents the budget
     could not measure, which route carried a role after its own ran out, what
     the run set over the project's own configuration. Read once. There is
     nothing to rule on -- accepting or sending back a branch does not change
     which plan was nearly out of room -- so these stay out of the worklist,
     where they can be seven of a list of seventeen. */
  noted: ['a condition of this run', ''],
  attempted_not_fixed: ['attempted, still standing', 'pill-red'],
  escalated: ['for you to rule on', 'pill-amber'],
  needs_spec_change: ['needs the spec reopened', 'pill-red'],
  out_of_spec: ['beyond the spec', ''],
  dismissed: ['dismissed', ''],
  unattempted: ['never attempted', 'pill-amber'],
  open: ['open', 'pill-amber'],
};

/* Bands by what became of each finding. Attempted-and-still-there first: it is
   the most informative row in the packet, and severity alone would bury it
   under a blocker somebody already fixed. */
/* Four things that can have happened to an objection, named as what happened.
 *
 * Not "Tried, and still there" or "Left for you", which are descriptions of a
 * mood rather than of an event -- and between them do not say who tried, what
 * trying meant, or why the two bands are different. Every title below is a
 * sentence about the repair loop, because the loop
 * is the only thing that acts on a finding between it being raised and you
 * reading it, and what it did is the whole difference between these bands. */
export const BANDS = [
  { key: 'standing', title: 'The loop tried to fix these and could not', open: false,
    sub: 'Routed for repair, attempted, and re-checked as still there. These are worth more than '
       + 'the rest: the objection survived a real attempt to answer it.',
    has: ['attempted_not_fixed', 'needs_spec_change'] },
  { key: 'yours', title: 'The loop never tried these', open: false,
    sub: 'Either the arbiter judged it a call for a person rather than a mechanical fix, or the '
       + 'loop hit a ceiling before reaching it. Nothing here has been attempted, so nothing here '
       + 'has been shown to be hard.',
    has: ['open', 'escalated', 'unattempted'] },
  { key: 'dismissed', title: 'An agent argued these away', open: false,
    sub: 'Judged not a defect, or beyond what the spec asked for. The argument is kept with each '
       + 'one so you can disagree with it.',
    has: ['dismissed', 'out_of_spec'] },
  { key: 'repaired', title: 'The loop fixed these', open: false,
    sub: 'Resolved during the run, and still listed: what was found is as much a fact about this '
       + 'work as what is left.',
    has: ['repaired', 'no_longer_holds'] },
];

/* A finding joined to what the orchestrator recorded about its life. The two
   are separate on the wire -- the text belongs to the agent that raised it and
   nothing may edit it (INV-11) -- and useless apart. */
export function findingsWithRecords(d) {
  const packet = packetOf(d);
  const byId = {};
  (packet.records || []).forEach((r) => { byId[r.finding_id] = r; });
  return (packet.findings || []).map((f) => ({ ...f, rec: byId[f.id] || null }));
}

export const outcomeOf = (f) => (f.rec || {}).outcome || 'open';

/* A restatement is shown inside the finding it corroborates, not beside it.
   Six agents reading the same bundle find the same defect, and the arbiter has
   already said which is which. */
export const isRestatement = (f) => !!(f.rec && f.rec.duplicate_of);

export function bandsOf(findings) {
  const claimed = new Set();
  const out = BANDS.map((band) => {
    const mine = findings.filter((f) => band.has.includes(outcomeOf(f)));
    mine.forEach((f) => claimed.add(f.id));
    return { band, mine };
  });
  const rest = findings.filter((f) => !claimed.has(f.id));
  if (rest.length) {
    out.push({ band: { key: 'other', title: 'Everything else', sub: '', open: false }, mine: rest });
  }
  return out.filter(({ mine }) => mine.length);
}

/* --------------------------------------------------------------- blind spots

   Where the evidence is missing. Every figure on a packet is computed as
   though everything that was supposed to run had run -- and when that is not
   true, nothing on the screen says so, because the thing that failed is
   precisely the thing that cannot report its own failure. An agent that never
   returned files no finding; a suite that died before any probe reported names
   none, and read alone that zero says the adversary attacked this and found
   nothing.

   Every entry names the figure it makes look better than it is, so this is a
   reconciliation rather than a list of complaints. Nothing here is a model's
   opinion: each one is computed from a field that exists for exactly this. */

/* Who wrote a disclosure or a decision, and which screen therefore owns it.
 *
 * A build unit's account of itself belongs beside the file it wrote, and a
 * repair round's belongs with the rest of what that round did. Told apart by
 * whether the unit is in `data.workers` -- a fact -- rather than by whether
 * its id starts with `R-`, which is a convention a model is asked to follow
 * and can quietly not follow. `INT` is the integrator, namespaced by
 * `_renumber_decisions`.
 */
export const buildUnits = (d) =>
  new Set(((d || {}).workers || []).map((w) => w.unit_id).filter(Boolean));

export const disclosureUnit = (line) =>
  (String(line || '').match(/^\s*(\S+)\s+--\s/) || [])[1] || '';

export const decisionUnit = (id) => String(id || '').split('/')[0];

/* What the repair rounds said about their own work: everything not written by
   a build unit or the integrator. It belongs on Repairs, beside what those
   rounds did, not under a tab about code, where it would describe no code
   anybody could open.
 *
 * The fallback direction is deliberate. A disclosure line can only come from
 * `collect_disclosures`, which reads workers -- build units and repair units,
 * never the integrator -- so an unrecognised unit is a repair unit, and the
 * effect of catching everything here is that no line can go homeless. A
 * decision is the other way round: it can name a file, criteria, or neither,
 * so that one is checked for rather than assumed. */
export function repairAccount(d) {
  const build = buildUnits(d);
  const mine = (unit) => unit && unit !== 'INT' && !build.has(unit);
  const packet = packetOf(d);
  return {
    disclosures: (packet.disclosures || []).filter((l) => mine(disclosureUnit(l))),
    decisions: (packet.decisions || []).filter(
      (x) => mine(decisionUnit(x.id)) && !(x.files || []).length),
  };
}

/* A decision no screen can show. Every decision is reached from the file it
   names, the criteria it serves, or -- for a repair round's -- from Repairs.
   One that names none of those is reachable from nothing, and without this a
   reader could only find that out from a tab listing every decision
   regardless. */
export function homelessDecisions(d) {
  const build = buildUnits(d);
  const packet = packetOf(d);
  const known = new Set((packet.materiality || []).map((m) => m.path));
  return (packet.decisions || []).filter((x) => {
    if ((x.files || []).some((f) => known.has(f))) return false;
    if ((x.criterion_ids || []).length) return false;
    const unit = decisionUnit(x.id);
    return unit === 'INT' || build.has(unit);
  });
}

export function blindSpots(d) {
  const packet = packetOf(d);
  const stats = packet.stats || {};
  const gates = (d.gates || {}).results || [];
  const oracle = d.oracle || {};
  const rework = packet.rework || {};
  const out = [];
  const add = (o) => out.push(o);

  if (stats.breaker_suite_failed) {
    add({ id: 'no-probes', hard: true,
      title: 'The adversary suite did not run to completion',
      discounts: `${stats.breaker_failures || 0} adversary failures`,
      what: 'breaker_suite_failed',
      detail: 'The suite exited non-zero before its probes reported, so the count of probes named '
        + 'in the output is low or zero. Read alone that number says the adversary attacked this '
        + 'and found little, which is the opposite of what happened.',
      source: 'BreakerReport.ran / exit_code' });
  }
  if (stats.agent_failures) {
    add({ id: 'no-agent', hard: true,
      title: `${stats.agent_failures} review agent${stats.agent_failures === 1 ? '' : 's'} did not return`,
      discounts: `${stats.findings_total || 0} findings`,
      what: `agent_failures = ${stats.agent_failures}`,
      detail: 'Every number here was computed as though they had run and reported nothing. The '
        + 'findings below are what the agents that came back found, not what the configured panel '
        + 'would have found.',
      source: 'stats.agent_failures' });
  }
  const untested = stats.untested_criteria || 0;
  const orphans = stats.orphan_requirements || 0;
  if (untested || orphans) {
    const bits = [];
    if (orphans) bits.push(`${orphans} with no code at all`);
    if (untested) bits.push(`${untested} implemented with no blind test`);
    add({ id: 'no-tests', hard: true,
      title: `${untested + orphans} of ${stats.criteria_total || 0} criteria have nothing confirming them`,
      discounts: `${stats.criteria_verified || 0} of ${stats.criteria_total || 0} criteria verified`,
      what: bits.join(' · '),
      detail: 'Computed by inverting the traceability matrix rather than reported by an agent, so '
        + 'a criterion cannot reach this list because a model mentioned it and cannot escape it by '
        + 'staying quiet. Nothing independent confirms these.',
      source: 'computed trace · stats.untested_criteria, orphan_requirements' });
  }
  // `optional` is a property of the check, not of the result: it is what the
  // project declared, and the result only says what happened. Joined by name.
  const declared = {};
  (((d.project || {}).gates) || []).forEach((g) => { declared[g.name] = g; });
  const optional = gates.filter((g) => (declared[g.name] || {}).optional);
  const stopped = gates.filter((g) => g.started === false && !g.skipped);
  if (optional.length || stopped.length) {
    const bits = [];
    if (optional.length) bits.push(`${optional.map((g) => g.name).join(', ')} cannot fail the run`);
    if (stopped.length) bits.push(`${stopped.map((g) => g.name).join(', ')} never started`);
    add({ id: 'soft-gates', hard: stopped.length > 0,
      title: `${optional.length + stopped.length} of ${gates.length} checks did not really check`,
      discounts: `${stats.gates_passed || 0} of ${stats.gates_total || 0} checks passing`,
      what: bits.join(' · '),
      detail: 'An optional check is in the denominator and cannot fail the run, so it adds to the '
        + 'passing figure without being able to lower it. A check that never started measured '
        + 'nothing at all — the red beside it is a fact about the machine, not about the code.',
      source: 'Gate.optional · GateResult.started' });
  }
  /* One entry, not one per criterion. A run where the oracle is short five
     capabilities would produce five rows with the same heading, and ten
     untestable criteria ten more -- twenty rows where three facts are being
     stated, which is the wall of repetition this screen exists to avoid. The
     criteria are what differ, so they go inside. */
  const requires = oracle.requires || [];
  if (requires.length) {
    const costs = [...new Set(requires.flatMap((r) => r.criterion_ids || []))];
    add({ id: 'needs', hard: costs.length > 0,
      title: requires.length === 1
        ? 'The oracle needed something the environment did not have'
        : `The oracle needed ${requires.length} things the environment did not have`,
      discounts: costs.join(', ') || 'the blind tests',
      what: requires.map((r) => r.need).filter(Boolean).join(' · '),
      detail: 'It said so rather than writing tests that assume the capability — a suite that '
        + 'stops on a precondition nobody agreed to supply verifies nothing at all, while a '
        + 'requirement stated here costs only the criteria it names. Those criteria are verified '
        + 'lower than they ask for, or not at all. '
        + requires.map((r) => `${r.need}: ${r.detail || 'no detail given'}`).join(' — '),
      source: 'OracleRequirement' });
  }
  const untestable = oracle.untestable_criteria || [];
  if (untestable.length) {
    add({ id: 'untestable', hard: false,
      title: untestable.length === 1
        ? `${untestable[0]} could not be tested from the spec`
        : `${untestable.length} criteria could not be tested from the spec`,
      discounts: untestable.join(', '),
      what: 'oracle.untestable_criteria',
      detail: 'The oracle read these and could not turn them into a test. That is a fact about '
        + 'how the criteria were written rather than about the code, and the matrix may still be '
        + `calling them traced: ${untestable.join(', ')}.`,
      source: 'oracle.untestable_criteria' });
  }
  if (stats.findings_duplicate) {
    add({ id: 'restated',
      title: `${stats.findings_duplicate} of the ${stats.findings_total} findings restate another`,
      discounts: `${stats.findings_total} findings`,
      what: `${stats.findings_distinct} distinct defects`,
      detail: 'Several agents reading the same bundle found the same thing. The restatements are '
        + 'kept and shown inside the finding they corroborate — the corroboration is often why the '
        + 'arbiter routed it for repair rather than escalating it — but the headline count is '
        + 'larger than the number of defects.',
      source: 'stats.findings_duplicate · FindingRecord.duplicate_of' });
  }
  if (rework.rounds && rework.stop_reason && !/converg|nothing left|no findings/i.test(rework.stop_reason)) {
    add({ id: 'stopped-short',
      title: 'The repair loop stopped before it was finished',
      discounts: `${rework.repaired || 0} repaired`,
      what: rework.stop_reason,
      detail: `${rework.unattempted || 0} finding(s) were never attempted. A loop that stops `
        + 'quietly is indistinguishable from one that converged, and those mean opposite things: '
        + 'what is left is what the loop did not reach, not what it could not fix.',
      source: 'rework.stop_reason · rework.unattempted' });
  }
  (rework.unbilled_roles || []).length && add({ id: 'unbilled',
    title: 'The budget could not constrain every agent',
    discounts: `$${Number(rework.cost_usd || 0).toFixed(2)} of $${Number(rework.budget_usd || 0).toFixed(2)}`,
    what: `${rework.unbilled_roles.join(', ')} · ${rework.unbilled_turns || 0} turns`,
    detail: 'Those roles run on routes that report no dollars, so nobody was charged for their '
      + `work and this ceiling could not have stopped it. Notionally $${Number(rework.notional_usd || 0).toFixed(2)}, `
      + "never counted against the budget. Their real ceiling is the plan's rolling window.",
    source: 'rework.unbilled_roles, unbilled_turns, notional_usd' });
  /* A choice this review cannot put in front of anybody. Empty on a healthy
     packet, and saying so is the point: a tab listing every decision
     regardless of whether it was reachable would make this condition
     invisible by never letting it arise. (INV-9) */
  const homeless = homelessDecisions(d);
  if (homeless.length) {
    add({ id: 'unreachable-decisions', hard: false,
      title: `${homeless.length} choice${homeless.length === 1 ? '' : 's'} the review cannot show `
        + 'you next to anything',
      discounts: `${(packet.decisions || []).length} decisions`,
      what: homeless.map((x) => x.id).join(', '),
      detail: 'Every other decision is reached from the file it names or the criteria it serves. '
        + 'These name neither, so nothing on any screen leads to them: '
        + homeless.map((x) => `${x.id} — ${x.title}`).join(' · '),
      source: 'Decision.files, criterion_ids' });
  }
  if ((packet.open_questions || []).length) {
    add({ id: 'unanswered',
      title: `${packet.open_questions.length} question(s) were carried into the build unanswered`,
      discounts: 'the spec this was built against',
      what: 'open at the freeze',
      detail: packet.open_questions.join(' · '),
      source: 'spec.open_questions' });
  }
  return out;
}

/* Which figure in the verdict band each blind spot discounts, so the number
   itself can carry the way in. A caveat filed behind a tab nobody opened is a
   caveat nobody read, which is the one thing tabs are bad at. */
export function discountsFor(spots) {
  const map = {};
  for (const s of spots) {
    if (s.id === 'no-tests' || s.id.startsWith('untestable')) map.criteria = s.id;
    else if (s.id === 'soft-gates') map.gates = s.id;
    else if (s.id === 'no-agent' || s.id === 'restated') map.findings = s.id;
    else if (s.id === 'no-probes') map.probes = s.id;
    else if (s.id === 'stopped-short' || s.id === 'unbilled') map.repairs = s.id;
  }
  return map;
}

/* ------------------------------------------------------------- the worklist

   What this run will not decide for itself.

   The same problem can reach the human many times over: on one packet,
   twenty-two findings were six problems. Six agents in three later rounds
   each found the same overflow; five found the same fallback; three found
   the same unprovisioned gate. The arbiter deduplicates within a round --
   thirteen restatements in round 0, correctly folded -- and not across
   rounds, so every re-discovery arrives as a fresh escalation with an empty
   `corroborated_by` and the worklist reads three times longer than the work.

   Clustering here is a stopgap for that, and it is deliberately a join rather
   than a judgement: cross-round `duplicate_of` is the arbiter's to make and to
   record, and a console that guesses at it is a second opinion nobody can
   audit. What a join can prove, it proves; what it cannot reach stays
   unreached. */

/* Everything still waiting on a person, restatements already folded away. */
export function awaitingRuling(d) {
  /* `noted` is deliberately absent: a condition of the run is reported and
     counted, and never queued as a call. */
  const waiting = ['open', 'escalated', 'unattempted', 'attempted_not_fixed', 'needs_spec_change'];
  return findingsWithRecords(d)
    .filter((f) => !isRestatement(f) && waiting.includes(outcomeOf(f)));
}

/* Two findings are the same call when they share a file AND either share a
   criterion or neither names one -- union-find, no threshold, no similarity
   score, nothing a model weighed. On the packet above it reproduces all four
   clusters exactly: six, five, three, two.

   The second clause is what lets two findings from different agents meet
   when both name the same two files and neither names a criterion. Without it
   they stay apart; with it, two findings that merely touch the same file and
   answer to different criteria still stay apart, which is the case it is
   guarding. */
export function clusterFindings(findings) {
  const parent = {};
  findings.forEach((f) => { parent[f.id] = f.id; });
  const find = (x) => (parent[x] === x ? x : (parent[x] = find(parent[x])));
  const union = (a, b) => { parent[find(a)] = find(b); };
  const meets = (a, b) => a.some((x) => b.includes(x));

  for (let i = 0; i < findings.length; i += 1) {
    for (let j = i + 1; j < findings.length; j += 1) {
      const a = findings[i];
      const b = findings[j];
      const af = a.files || [];
      const bf = b.files || [];
      const ac = a.criterion_ids || [];
      const bc = b.criterion_ids || [];
      if (!meets(af, bf)) continue;
      if (meets(ac, bc) || (!ac.length && !bc.length)) union(a.id, b.id);
    }
  }

  const groups = {};
  findings.forEach((f) => { (groups[find(f.id)] || (groups[find(f.id)] = [])).push(f); });
  /* The cluster is keyed on its worst finding, and ties break on id: which
     title heads the row must not depend on the order the packet happens to
     list them in. */
  return Object.values(groups).map((g) => g.slice().sort((x, y) =>
    (SEV_RANK[x.severity] ?? 9) - (SEV_RANK[y.severity] ?? 9) || x.id.localeCompare(y.id)));
}

/* The words for stages, verdicts and severities, from app/vocab.js -- a
   classic script loaded before any module, so it is always there. */
export const { STAGES, VERDICTS, SEVERITY_ORDER } = globalThis.fabrikaVocab;
export const SEV_RANK = SEVERITY_ORDER;

/* The exact sentence the orchestrator writes when the arbiter returned no
   disposition for a finding. On the packet above, sixteen of the twenty-two
   carried it, all raised in the last round -- so most of that worklist was in
   front of a human by default rather than by judgement, and a heading saying
   "the arbiter judged these a call for a person" would be false about most of
   it. */
export const UNROUTED = 'The arbiter did not route this finding.';

/* What has been done to a finding before it reached you. A mark on the row,
   not the shape of the worklist: the fact is worth carrying, and sorting by it
   can put a minor above five blockers. */
export const WORK_STATE = {
  standing: ['tried, still there', 'pill-red',
    'Routed for repair, attempted, and re-checked as still there — the objection survived a '
    + 'real attempt to answer it.'],
  ruled: ['the arbiter\u2019s call', 'pill-amber',
    'Triaged and deliberately not sent to the loop. Its reason is on the row.'],
  unrouted: ['never triaged', '',
    'The arbiter returned no disposition for this, so it defaulted to you \u2014 never to '
    + 'silence. Nothing has judged it and nothing has attempted it.'],
};

const groupOf = (f) => {
  if (['attempted_not_fixed', 'needs_spec_change'].includes(outcomeOf(f))) return 'standing';
  return String((f.rec || {}).disposition_reason || '').startsWith(UNROUTED)
    ? 'unrouted' : 'ruled';
};

/* One row per call: the cluster, the group it belongs to, and who found it.
   Ordered worst-first within a group, because the group is already the
   ordering between them. */
export function callsFor(d) {
  const rows = clusterFindings(awaitingRuling(d)).map((group) => {
    const head = group[0];
    const roles = {};
    group.forEach((f) => { const r = (f.rec || {}).role || 'unknown'; roles[r] = (roles[r] || 0) + 1; });
    return {
      key: head.id,
      head,
      rest: group.slice(1),
      ids: group.map((f) => f.id),
      group: groupOf(head),
      severity: head.severity,
      said: Object.entries(roles).sort((a, b) => b[1] - a[1]),
      files: [...new Set(group.flatMap((f) => f.files || []))],
      criteria: [...new Set(group.flatMap((f) => f.criterion_ids || []))].sort(),
    };
  });
  /* Worst first, and every tie broken by a field.
   *
   * Not grouped by how much process has been applied to them -- attempted,
   * then judged, then never triaged -- with severity only inside each group.
   * There a lone `minor` in the first group would lead a list containing five
   * blockers. That ordering is an account of how a finding got here, which is
   * worth knowing and is not what a reviewer opens a worklist to find out.
   *
   * After severity: a finding the loop attempted and could not fix outranks
   * one nothing has tried, because it has survived a real attempt. Then the
   * number of independent readers, because agreement is evidence. Then the id,
   * so the order never depends on what the packet happened to list first. */
  const attempted = (c) => (['attempted_not_fixed', 'needs_spec_change']
    .includes(outcomeOf(c.head)) ? 0 : 1);
  const readers = (c) => -c.ids.length;
  return rows.sort((a, b) =>
    (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9)
    || attempted(a) - attempted(b)
    || readers(a) - readers(b)
    || a.key.localeCompare(b.key));
}

/* A call is settled when a flag points at one of its findings. The flag is the
   record -- held by the server, in the ledger, surviving a reload -- so
   "have I dealt with this" is never a thing this screen remembers on its own. */
/* The criteria Fabrika did not test -- their level can't run cleanly in this
   project -- and what a person said about each, by hand. A criterion flag is
   the ruling: `dismissed` means it works, `repair` sends it to the loop like
   any other call. Counted with the calls, because nobody else has checked
   them and "all ruled" must not be true while one is still open. */
export const manualChecksFor = (d) => packetOf(d).manual_checks || [];

export const manualRulings = (checks, flags) => {
  const ids = new Set(checks.map((c) => c.criterion_id));
  const out = {};
  (flags || []).forEach((fl) => {
    if (fl.source === 'criterion' && ids.has(fl.anchor)) out[fl.anchor] = fl;
  });
  return out;
};

export const settledBy = (calls, flags) => {
  const byAnchor = {};
  (flags || []).forEach((fl) => { if (fl.anchor) (byAnchor[fl.anchor] ||= []).push(fl); });
  const out = {};
  calls.forEach((c) => {
    const hit = c.ids.flatMap((id) => byAnchor[id] || []);
    if (hit.length) out[c.key] = hit[hit.length - 1];
  });
  return out;
};
