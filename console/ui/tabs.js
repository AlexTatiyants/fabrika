/* The whole of gate 2, on one control.
 *
 * One control rather than two routes that do not know about each other: the
 * requirement screen, and a link at the bottom of it to a single long page
 * holding everything else. That would make reviewing what was *asked for* the
 * landing page and reviewing what was *done* a footnote, which is the wrong
 * way round for a change nobody has read yet.
 *
 * Six tabs, one act. The verdict band sits above all of them because it
 * belongs to none, and every figure on it that something discounts carries a
 * dagger into the tab holding the discount — a caveat behind a closed tab is a
 * caveat nobody reads, and that is the one thing tabs are bad at.
 */

import { html, useEffect, useRef, useState } from '../vendor/preact-htm.module.js';
import { AC_STATE, FocusTools, VERDICTS, blindSpots, callsFor, criteriaRows, discountsFor,
         findingsWithRecords, isRestatement, outcomeOf, packetOf,
         settledBy, usd, manualChecksFor, manualRulings
} from './shared.js';
import { PreviewButton } from './preview.js';
import { dependencyTab } from './dependencies.js';

const VERDICT = Object.fromEntries(
  Object.entries(VERDICTS).map(([id, v]) => [id, `${v.word}.`]));

/* A blocker that is still standing. Repaired and dismissed ones are still in
   the packet -- the record only grows -- but they are not what a dot on a
   closed tab is for. */
const SETTLED = ['repaired', 'no_longer_holds', 'dismissed', 'out_of_spec'];

const standingBlocker = (f) => f.severity === 'blocker' && !isSettled(f);

/* Still true of the branch in front of you, whatever its severity. */
const isSettled = (f) => SETTLED.includes(outcomeOf(f));

/* The computed findings that mean the Evidence tab is missing something to
   watch. Named once, beside the check that uses them. */
const RECORDING_GAPS = ['recordings-uncovered', 'traces-none', 'traces-undeclared'];

export function tabsFor(d, flags, traces) {
  const findings = findingsWithRecords(d).filter((f) => !isRestatement(f));
  const spots = blindSpots(d);
  const packet = packetOf(d);
  const rework = packet.rework || {};
  const calls = callsFor(d);
  const settled = settledBy(calls, flags);
  const checks = manualChecksFor(d);
  const rulings = manualRulings(checks, flags);
  const total = calls.length + checks.length;
  const left = calls.filter((c) => !settled[c.key]).length
    + checks.filter((c) => !rulings[c.criterion_id]).length;
  return [
    /* First, and the only tab whose count goes down. Everything else on this
       control counts what happened; this one counts what is left -- including
       the criteria a person checks by hand. */
    { view: 'calls', label: 'Your calls',
      count: total ? `${left} of ${total}` : 'none',
      alert: calls.some((c) => !settled[c.key] && c.severity === 'blocker') },
    { view: 'map', label: 'Work map',
      count: `${(packet.trace || []).length} → ${(packet.materiality || []).length}`,
      /* A file nothing asked for is the map's fourth state, so it is the
         map's dot -- along with a criterion
         whose blind test failed, which is the other thing on this screen
         worth interrupting a reader for. */
      alert: (packet.unclaimed || []).length > 0
        || criteriaRows(d).some((r) => AC_STATE[r.state].sev === 'blocker') },
    /* The facts, before anybody's reading of them. Every tab after this one
       is an argument made by a model about the run; this one is the run's own
       artifacts, and it sits between the map and the arguments for that
       reason. */
    { view: 'evidence', label: 'Evidence',
      count: `${(traces || []).length + ((d.gates || {}).results || []).length
        + ((d.oracle || {}).tests || []).length
        + ((d.breaker || {}).tests_applied || []).length} items`,
      /* Amber, not red: nothing in here is a thing standing against the
         change. What it can be is missing -- a criterion with no blind test, a
         browser that ran and left no recording, or a criterion a recorded
         browser test checked with no recording titled for it.
         Read off the computed findings rather than off a count, because only
         they know whether a browser ran at all: a feature with nothing to see
         leaves no recording and is missing nothing. */
      amber: findings.some((f) => RECORDING_GAPS.includes(f.id) && !isSettled(f))
        || criteriaRows(d).some((r) => r.state === 'no_test') },
    /* Facts too, like Evidence: what the lockfiles say this change brought in,
       and what is known about it. Red for a package that is malicious,
       vulnerable or under a license the project refused; amber for what nobody
       could look up or a person should rule on. */
    { view: 'dependencies', label: 'Dependencies', ...dependencyTab(d) },
    /* Every objection, security included. A Security tab beside this one
       would be keyed on a category the only prose reader is told not to use,
       so it would draw a zero while the findings it was for sat here. */
    { view: 'objections', label: 'Objections',
      count: findings.length, alert: findings.some(standingBlocker) },
    { view: 'repairs', label: 'Repairs',
      count: rework.rounds ? `${rework.rounds} round${rework.rounds === 1 ? '' : 's'}` : 'none',
      alert: (rework.protected_writes_refused || []).length > 0 },
    { view: 'blindspots', label: 'Blind spots',
      count: spots.length, amber: spots.some((s) => s.hard) },
  ];
}

/* One figure, with the way into whatever discounts it.
 *
 * Not a row of six running across the header under the verdict, in prose
 * order -- `45 min budgeted  5 blockers open · 10 raised  4 of 6 checks
 * passing` -- which is six numbers a reader has to parse as six sentences
 * before learning that none of them is the verdict. In a sheet each is a
 * figure in a column with its name beside it, which is what it is. */
function Fig({ n, of, label, note, bad, spot, go }) {
  return html`
    <div class="vs-row">
      <span class=${`vs-n ${bad ? 'vs-bad' : ''}`}>
        ${n}${of !== undefined && html`<span class="vs-of">${' '}of ${of}</span>`}
      </span>
      <span class="vs-lab">
        ${label}
        ${spot && html`
          <button type="button" class="disc" title="This figure is discounted — see Blind spots"
                  onClick=${() => go(spot)}>†</button>`}
      </span>
      ${note && html`<span class="vs-note">${note}</span>`}
    </div>`;
}

/* Who read this, and how many times each.
 *
 * The one fact about the run that the header does not already say and no tab
 * owns: three agents on different models is a different packet from one, and
 * a reader deciding what the verdict is worth wants it before they start
 * rather than four tabs in. Nothing else about the run needs a strip of its
 * own -- the finding counts are on the tabs, the loop's time and cost are in
 * Repairs, and a figure like "18h 38m intake to packet" mostly measures the
 * feature sitting still waiting for a human.
 */
function Panel({ run, href, close }) {
  if (!run || !run.agents.length) {
    return html`<div class="vs-panel">No review agent is configured — nothing read this but the
      checks.</div>`;
  }
  return html`
    <div class="vs-panel">
      <span class="vs-panel-lab">Read by</span>
      <span class="vs-panel-who">
        ${run.agents.map((a, i) => html`
          <span key=${a.role}>
            ${i ? ' · ' : ''}<b class="mono">${a.role}</b>${a.samples > 1 ? ` ×${a.samples}` : ''}
          </span>`)}
      </span>
      <span class="vs-panel-note">each on the same bundle, none able to see the others</span>
      <a class="vs-panel-more" href=${href} onClick=${close}>what each one found →</a>
    </div>`;
}

/* The figures, behind a press.
 *
 * Not a permanent row under the verdict: a header that says six things at
 * once says none of them. The verdict and its headline are what a reader is
 * there for, and `45 min budgeted` is never the answer to "should this ship".
 * Nothing is left out -- it is one press away and laid out better than a row
 * could lay it out.
 *
 * What a popup must not do is take a caveat with it. Four of these figures are
 * discounted by a blind spot, and each carries a dagger into it; closed, the
 * button carries the dagger for all of them, so "this number is not what it
 * looks like" survives being put away. */
/* The figures this sheet holds, and the only ones its button may speak for.
   `discountsFor` also returns `repairs`, which discounts the repair loop's
   own account of itself and is marked on Repairs -- counting it here would
   make the button promise four daggers over a sheet that draws three. */
const MARKED = ['gates', 'criteria', 'probes', 'findings'];

function Stats({ stats, disc, budget, run, hrefs, onDiscount, rework = {} }) {
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  const discounted = MARKED.filter((k) => disc[k]).length;

  useEffect(() => {
    if (!open) return undefined;
    const away = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === 'Escape') { setOpen(false); e.stopPropagation(); } };
    // `pointerdown`, not `click`: a press that starts outside should dismiss
    // before whatever it landed on gets it, the way a menu does.
    document.addEventListener('pointerdown', away, true);
    document.addEventListener('keydown', esc, true);
    return () => {
      document.removeEventListener('pointerdown', away, true);
      document.removeEventListener('keydown', esc, true);
    };
  }, [open]);

  const go = (spot) => { setOpen(false); onDiscount(spot); };
  const raised = (stats.blockers_raised ?? 0) > (stats.blockers ?? 0)
    ? `${stats.blockers_raised} raised` : '';

  return html`
    <div class="vs" ref=${box}>
      <button type="button" class="vs-btn" aria-expanded=${open ? 'true' : 'false'}
              aria-haspopup="dialog"
              title=${`The figures behind this verdict${discounted
                ? ` — ${discounted} of them discounted` : ''}`}
              onClick=${() => setOpen(!open)}>
        <svg class="ic" aria-hidden="true"><use href="#i-l-chart" /></svg>
        <span class="vs-btn-w">Stats</span>
        ${discounted > 0 && html`<span class="vs-btn-disc" aria-hidden="true">†</span>`}
      </button>

      ${open && html`
        <div class="vs-sheet" role="dialog" aria-label="The figures behind this verdict">
          <${Fig} n=${stats.blockers ?? 0} label="blockers open" note=${raised}
                  bad=${!!stats.blockers} />
          <${Fig} n=${stats.gates_passed ?? 0} of=${stats.gates_total ?? 0}
                  label="checks passing" spot=${disc.gates} go=${go} />
          <${Fig} n=${stats.criteria_verified ?? 0} of=${stats.criteria_total ?? 0}
                  label="criteria verified" spot=${disc.criteria} go=${go} />
          <${Fig} n=${stats.breaker_failures ?? 0} label="probes failing"
                  spot=${disc.probes} go=${go} />
          <${Fig} n=${stats.findings_total ?? 0} label="findings raised"
                  note=${stats.findings_duplicate ? `${stats.findings_distinct} distinct` : ''}
                  spot=${disc.findings} go=${go} />
          <${Fig} n=${budget ?? '—'} label="min budgeted" />
          ${/* What the loop cost, beside the run's other figures rather than
               at the head of the Repairs tab. The measured figure can read
               "$0.00 of a $10.00 budget" while eleven agents ran unmeasured
               for a notional $12.67, more than the whole budget -- correct
               arithmetic and the opposite of the truth. So the notional one
               leads and the budget is described as what it is: a ceiling that
               could not have stopped this run. */''}
          ${rework.rounds > 0 && html`
            <${Fig} n=${usd(rework.notional_usd || rework.cost_usd || 0)} label="the loop cost"
                    note=${(rework.unbilled_roles || []).length
                      ? `${usd(rework.cost_usd || 0)} of it measured`
                      : `of a ${usd(rework.budget_usd || 0)} budget`} />`}
          ${(rework.unbilled_roles || []).length > 0 && html`
            <div class="vs-row vs-wide">
              <span class="vs-lab">${rework.unbilled_roles.length} agents ran on routes that
                report no dollars, spending ${rework.unbilled_turns || 0} turns. The${' '}
                ${usd(rework.budget_usd || 0)} budget could not have stopped them.</span>
            </div>`}
          <${Panel} run=${run} href=${hrefs.objections()} close=${() => setOpen(false)} />
        </div>`}
    </div>`;
}

export function Verdict({ data, onDiscount, reading, run, hrefs }) {
  const packet = packetOf(data);
  const stats = packet.stats || {};
  const rework = packet.rework || {};
  const disc = discountsFor(blindSpots(data));
  const ground = packet.verdict === 'ship' ? 'ship'
    : ['send_back', 'reject'].includes(packet.verdict) ? 'stop' : 'rulings';

  return html`
    ${/* The ground says which of the three this is, and the plate says it
         again in words -- the half that survives a reader the colour is lost on. */''}
    <section class=${`v-band v-band-${ground}`}>
      <div class="wrap v-in">
        ${/* On a plate the verdict is a stamp, not a sentence, and a stamp
             tracked out to a quarter of an em does not carry a full stop. */''}
        <span class="v-word">${(VERDICT[packet.verdict] || packet.verdict).replace(/\.$/, '')}</span>
        <p class="verdict-headline">${packet.headline}</p>
        ${/* One row, one alignment. Left to bring their own vertical
             offsets, these land eight pixels apart. */''}
        <div class="v-tools">
          <${Stats} stats=${stats} disc=${disc} run=${run} hrefs=${hrefs} rework=${rework}
                    budget=${packet.attention_budget_minutes} onDiscount=${onDiscount} />
          <${PreviewButton} data=${data} />
          <${FocusTools} on=${reading} bare />
        </div>
      </div>
    </section>`;
}

export function Tabs({ tabs, view, param, hrefs }) {
  /* `criterion`, `file` and evidence-for-a-criterion are drill-downs from the
     map rather than tabs of their own -- you got there through a row on it,
     and the tab that stays lit is the one that says so. Evidence with no id is
     the run-scope tab, and lights itself. */
  const here = ['criterion', 'file'].includes(view) || (view === 'evidence' && param) ? 'map'
    : view === 'rework' ? 'repairs' : view;
  return html`
    <div class="tabstrip">
      <div class="wrap tabs" role="tablist" aria-label="How to read this packet">
        ${tabs.map((t) => html`
          <a key=${t.view} class="tab" role="tab" href=${hrefs[t.view]()}
             aria-selected=${here === t.view ? 'true' : 'false'}>
            ${t.label}
            <span class="mono tab-n">${t.count}</span>
            ${t.alert && html`<span class="tab-dot" title="something in here is still standing" />`}
            ${t.amber && html`<span class="tab-dot tab-dot-amber"
                                    title="evidence is missing in here" />`}
          </a>`)}
      </div>
    </div>`;
}

export { VERDICT, standingBlocker };
