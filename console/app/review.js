/* The review a feature ends at -- findings by outcome, the verdict -- and the
   screen for a run that failed. */

'use strict';

/* --------------------------------------------------------------- 4. review */

function statRow(stats) {
  const gatesClass = stats.gates_total === 0 ? '' : (stats.gates_passed === stats.gates_total ? 'green' : 'red');
  const critClass = stats.criteria_verified === stats.criteria_total ? 'green' : 'amber';
  const cells = [
    [`${stats.gates_passed}/${stats.gates_total}`, 'checks passed', gatesClass],
    [`${stats.criteria_verified}/${stats.criteria_total}`, 'criteria verified', critClass],
    [stats.orphan_requirements, 'orphan requirements', stats.orphan_requirements ? 'red' : ''],
    [stats.untested_criteria, 'untested criteria', stats.untested_criteria ? 'amber' : ''],
    [stats.blockers, 'blockers', stats.blockers ? 'red' : ''],
  ];
  return `<div class="stat-row">${cells.map(([n, k, cls]) => `
    <div class="stat ${cls}"><span class="n">${esc(n)}</span><span class="k">${esc(k)}</span></div>`).join('')}</div>`;
}

function materialityBand(packet) {
  const items = packet.materiality || [];
  const groups = {};
  MAT_ORDER.forEach((k) => { groups[k] = []; });
  items.forEach((m) => { (groups[m.klass] || (groups[m.klass] = [])).push(m); });

  const totalLines = items.reduce((a, m) => a + (m.lines || 0), 0) || 1;
  const segs = MAT_ORDER.filter((k) => groups[k].length).map((k) => {
    const lines = groups[k].reduce((a, m) => a + (m.lines || 0), 0);
    const pct = (lines / totalLines) * 100;
    const light = k === 'generated' || k === 'mechanical';
    return `<button class="mat-seg" type="button" data-class="${k}"
      aria-expanded="${state.openClass === k}"
      aria-label="${k}: ${lines} lines across ${groups[k].length} files"
      style="width:${pct}%; background:var(--mat-${k})">
      <span class="seg-label" style="color:${light ? 'var(--ink-2)' : '#fff'}">${pct > 11 ? k : ''}</span>
    </button>`;
  }).join('');

  const legend = MAT_ORDER.filter((k) => groups[k].length).map((k) => {
    const lines = groups[k].reduce((a, m) => a + (m.lines || 0), 0);
    return `<span><i class="swatch" style="background:var(--mat-${k})"></i>${k} · ${lines} lines · ${groups[k].length} files</span>`;
  }).join('');

  const open = state.openClass && groups[state.openClass] && groups[state.openClass].length
    ? `<div class="mat-files">
        <p class="small muted" style="margin:.6rem 0 .2rem"><b>${esc(state.openClass)}</b> — ${esc(MAT_BLURB[state.openClass] || '')}</p>
        ${groups[state.openClass].map((m) => `
          <div class="mat-file ${esc(m.klass)}">
            <span class="path">${esc(m.path)}</span>
            <span class="lines">${esc(m.lines)} lines</span>
            <span class="why">${esc(m.reason)}</span>
          </div>`).join('')}
      </div>`
    : '';

  const novelLines = (groups.novel || []).reduce((a, m) => a + (m.lines || 0), 0);
  const stats = packet.stats || {};

  return `
    <section class="materiality">
      <p class="mat-caption">
        <span class="n">${esc(stats.lines_written || totalLines)}</span> lines across
        <span class="n">${esc(stats.files_written || items.length)}</span> files
        <span class="arrow">→</span>
        <span class="n">${esc((packet.decisions || []).length)}</span> decisions
      </p>
      <div class="mat-bar">${segs}</div>
      <div class="mat-legend">${legend}</div>
      <p class="mat-thesis">Graphite is settled: generated, mechanical, or the obvious thing done the
        way this repo already does it. The ${novelLines} accent-coloured lines are the ones where a
        choice was made. Click a band to see which files are in it and why they were classified that
        way — an unaudited claim about what is safe to skip is exactly what this system exists to
        prevent.</p>
      ${open}
    </section>`;
}

function traceList(trace) {
  const label = { traced: '', orphan_requirement: 'orphan requirement', untested: 'untested' };
  const rows = trace.map((r) => {
    const pillCls = r.status === 'orphan_requirement' ? 'pill-red'
      : r.status === 'untested' ? 'pill-amber' : '';
    return `<li class="trace-row ${esc(r.status)}">
      <span class="mono dim">${esc(r.criterion_id)}</span>
      <span class="stmt">${esc(r.statement)}</span>
      ${label[r.status] ? `<span class="pill ${pillCls}">${esc(label[r.status])}</span>` : '<span></span>'}
      <span class="meta">
        ${r.implementing_files.length ? `built in ${esc(r.implementing_files.join(', '))}` : 'nothing implements this'}
        · ${r.test_names.length ? `blind tests: ${esc(r.test_names.join(', '))}` : 'no blind test'}
      </span>
    </li>`;
  }).join('');
  return `<ul class="trace-list">${rows}</ul>`;
}

function decisionCard(d, ruling) {
  const ruled = !!ruling;
  const needsYou = !ruled && (
    d.reversibility === 'irreversible' ||
    d.confidence < 0.5 ||
    (d.tags || []).some((t) => ['security', 'data-loss', 'scope'].includes(t))
  );
  const confPct = Math.round((d.confidence || 0) * 100);
  const revPill = d.reversibility === 'irreversible' || d.reversibility === 'hard'
    ? 'pill-accent' : '';

  return `
    <article class="decision ${needsYou ? 'needs-you' : ''} ${ruled ? 'ruled' : ''}" data-decision="${esc(d.id)}">
      <div>
        <span class="rank">${d.rank ? String(d.rank).padStart(2, '0') : '··'}</span>
        <span class="d-title">${esc(d.title)}</span>
      </div>
      <div class="d-pills">
        <span class="pill ${revPill}">${esc(d.reversibility)}</span>
        <span class="pill confidence">confidence
          <span class="conf-bar"><span class="conf-fill ${d.confidence < 0.5 ? 'low' : ''}" style="width:${confPct}%"></span></span>
          ${confPct}%</span>
        ${(d.tags || []).map((t) => `<span class="pill">${esc(t)}</span>`).join('')}
        ${(d.criterion_ids || []).map((c) => `<span class="pill">${esc(c)}</span>`).join('')}
        <span class="pill">${esc(d.id)}</span>
      </div>
      <dl>
        <dt>Rationale</dt><dd>${esc(d.rationale)}</dd>
        ${(d.alternatives_rejected || []).length
          ? `<dt>Rejected</dt><dd><ul>${d.alternatives_rejected.map((a) => `<li>${esc(a)}</li>`).join('')}</ul></dd>`
          : `<dt>Rejected</dt><dd class="dim">Nothing. The worker considered no alternative, which is itself the claim.</dd>`}
        ${d.blast_radius ? `<dt>Blast radius</dt><dd>${esc(d.blast_radius)}</dd>` : ''}
        ${(d.files || []).length ? `<dt>Files</dt><dd class="mono small">${esc(d.files.join(', '))}</dd>` : ''}
      </dl>
      <div class="d-actions">
        ${ruled
          ? `<span class="pill ${ruling.ruling === 'accept' ? 'pill-green' : 'pill-amber'}">${ruling.ruling === 'accept' ? 'accepted' : 'sent back'}</span>
             <span class="ruling-note">ruled ${esc(rel(ruling.at))}</span>
             <button class="btn btn-sm btn-quiet" data-rule="accept" data-id="${esc(d.id)}">re-rule as accepted</button>
             <button class="btn btn-sm btn-quiet" data-rule="send_back" data-id="${esc(d.id)}">re-rule as sent back</button>`
          : `<button class="btn btn-sm" data-rule="accept" data-id="${esc(d.id)}">Accept</button>
             <button class="btn btn-sm" data-rule="send_back" data-id="${esc(d.id)}">Send back</button>`}
      </div>
    </article>`;
}

/* What became of a finding, in the words a reader needs rather than the enum.
   A repaired finding stays in this list: the count never goes down, because a
   packet that shrank as the loop worked would look better and be worth less. */
const OUTCOME = {
  repaired: { label: 'repaired', cls: 'pill-green' },
  /* Raised by one of the factory's own checks, which ran again and no longer
     finds it. Settled, but not a repair: nothing was fixed for it. */
  no_longer_holds: { label: 'no longer holds', cls: 'pill-green' },
  /* A condition of the run rather than a defect in the work. Reported, read
     once, and never a call: no ruling changes which plan was nearly out. */
  noted: { label: 'a condition of this run', cls: '' },
  attempted_not_fixed: { label: 'attempted, still standing', cls: 'pill-red' },
  escalated: { label: 'for you to rule on', cls: 'pill-amber' },
  needs_spec_change: { label: 'needs the spec reopened', cls: 'pill-red' },
  out_of_spec: { label: 'beyond the spec', cls: '' },
  dismissed: { label: 'dismissed', cls: '' },
  unattempted: { label: 'never attempted', cls: 'pill-amber' },
  open: { label: 'open', cls: 'pill-amber' },
};

/* Attempted-and-still-there first: it is the most informative row in the
   packet, and severity alone would bury it under a blocker somebody fixed. */
/* Settled by the run itself: fixed, or no longer found by the check that
   raised it. Neither is waiting on anyone. */
const SETTLED_OUTCOMES = ['repaired', 'no_longer_holds'];

const OUTCOME_ORDER = {
  attempted_not_fixed: 0, needs_spec_change: 1, open: 2, escalated: 2,
  unattempted: 3, dismissed: 4, out_of_spec: 5, repaired: 6, no_longer_holds: 6, noted: 7,
};

/* What the run actually did, in one place.

   A packet that answers only "what did it find" and never "what looked, for how
   long, at what cost" leaves a reader no way to know that three review agents
   ran rather than one, or that the loop stopped on its round cap rather than
   because it was finished. Both are facts about how much the verdict is worth.

   Computed here and handed to the mounted screens, so the two ways of reading a
   packet cannot disagree about arithmetic. */
function runSummary(d) {
  const packet = d.packet || {};
  const stats = packet.stats || {};
  const phases = (d.state && d.state.phases) || [];
  const ran = phases.filter((p) => p.status === 'done' && p.started_at && p.ended_at);
  const started = ran.map((p) => Date.parse(p.started_at)).filter(Number.isFinite);
  const ended = ran.map((p) => Date.parse(p.ended_at)).filter(Number.isFinite);
  // First phase to last, which includes the hours a feature sat waiting on a
  // human at gate 1. Labelled for exactly that rather than called "end to end":
  // a number that silently counts your lunch break is a number nobody can use.
  const wall = started.length && ended.length
    ? Math.max(...ended) - Math.min(...started) : 0;

  // One row per agent, not one per round: the same three agents can run four
  // panels, and listing twelve entries says nothing except that the reader
  // should stop reading.
  const byRole = {};
  ((d.reviews) || []).forEach((r) => {
    const seen = byRole[r.role] || (byRole[r.role] = {
      role: r.role, model: r.model, samples: 0, panels: 0, findings: 0,
    });
    seen.samples = Math.max(seen.samples, r.samples || 1);
    seen.panels += 1;
  });
  ((packet.records) || []).forEach((rec) => {
    if (byRole[rec.role]) byRole[rec.role].findings += 1;
  });

  return {
    wall_ms: wall,
    phases: ran.length,
    calls: (d.usage || {}).calls || 0,
    tokens: (d.usage || {}).total_tokens || 0,
    cost: (d.usage || {}).total_cost || 0,
    agents: Object.values(byRole),
    rework: packet.rework || null,
    gates: { passed: stats.gates_passed || 0, total: stats.gates_total || 0 },
    criteria: { verified: stats.criteria_verified || 0, total: stats.criteria_total || 0 },
    probes: { failing: stats.breaker_failures || 0 },
  };
}

function runBand(sum) {
  const cell = (v, k) => `<div class="rb-cell"><b>${v}</b><span>${esc(k)}</span></div>`;
  const rw = sum.rework;
  return `
    <section class="run-band">
      <div class="rb-row">
        ${cell(dur((sum.wall_ms || 0) / 1000), 'intake to packet')}
        ${cell(sum.phases, 'phases run')}
        ${cell(sum.calls, 'model calls')}
        ${cell(`$${(sum.cost || 0).toFixed(2)}`, 'spent')}
        ${cell(`${sum.gates.passed}<span class="rb-of">/${sum.gates.total}</span>`, 'checks passing')}
        ${cell(`${sum.criteria.verified}<span class="rb-of">/${sum.criteria.total}</span>`,
               'criteria verified')}
      </div>
      <div class="rb-split">
        <div class="rb-half">
          <h3>Who looked</h3>
          ${sum.agents.length ? `<ul class="rb-list">${sum.agents.map((a) => `
            <li><span class="rb-name mono">${esc(a.role)}</span>
              <span class="rb-what">${a.samples > 1 ? `${a.samples} samples` : 'one pass'}
                · ${a.panels} panel${a.panels === 1 ? '' : 's'}</span>
              <span class="rb-count">${a.findings} finding${a.findings === 1 ? '' : 's'}</span>
            </li>`).join('')}</ul>
            <p class="rb-note">Every agent reads the same bundle and cannot see the others.
              An objection raised even once is kept, and the worst verdict wins.</p>`
            : '<p class="rb-note">No review agent is configured.</p>'}
        </div>
        <div class="rb-half">
          <h3>What the loop did</h3>
          ${rw && rw.rounds ? `<ul class="rb-list">
            <li><span class="rb-name">${rw.rounds} round${rw.rounds === 1 ? '' : 's'}</span>
              <span class="rb-what">${dur(rw.elapsed_s || 0)} ·
                $${(rw.cost_usd || 0).toFixed(2)} of $${(rw.budget_usd || 0).toFixed(2)}${
                  unbilledNote(rw) ? ` · ${esc(unbilledNote(rw))}` : ''}</span></li>
            <li><span class="rb-name">${rw.repaired} repaired</span>
              <span class="rb-what">${rw.attempted_not_fixed} attempted and still there ·
                ${rw.escalated} left for you · ${rw.dismissed} dismissed ·
                ${rw.unattempted} never attempted</span></li>
          </ul>
          <p class="rb-note">Stopped because ${esc(rw.stop_reason || 'no reason recorded')}.</p>`
            : '<p class="rb-note">The repair loop did not run.</p>'}
        </div>
      </div>
    </section>`;
}

/* Findings, in bands by what became of them.

   Sixty-odd findings in one column is a list nobody finishes, and the ordering
   that matters -- attempted and still there, first -- is invisible inside it.
   The bands that need a person are open; the ones that are settled are shut and
   say how many they hold, because "13 repaired" is the whole of what a reader
   needs from them until they want one. */
/* Titled and explained in the words of `BANDS` in ui/shared.js, which the review
   screens use: the one-page packet is the same record, read another way. */
const FINDING_BANDS = [
  { key: 'attempted_not_fixed', title: 'The loop tried to fix these and could not',
    sub: 'Routed for repair, attempted, and re-checked as still there. These are worth more than '
       + 'the rest: the objection survived a real attempt to answer it.',
    open: true, outcomes: ['attempted_not_fixed', 'needs_spec_change'] },
  { key: 'open', title: 'The loop never tried these',
    sub: 'Either the arbiter judged it a call for a person rather than a mechanical fix, or the '
       + 'loop hit a ceiling before reaching it. Nothing here has been attempted, so nothing here '
       + 'has been shown to be hard.',
    open: true, outcomes: ['open', 'escalated', 'unattempted'] },
  { key: 'dismissed', title: 'An agent argued these away',
    sub: 'Judged not a defect, or beyond what the spec asked for. The argument is kept with each '
       + 'one so you can disagree with it.',
    open: false, outcomes: ['dismissed', 'out_of_spec'] },
  { key: 'repaired', title: 'The loop fixed these',
    sub: 'Resolved during the run, and still listed: what was found is as much a fact about this '
       + 'work as what is left.',
    open: false, outcomes: ['repaired', 'no_longer_holds'] },
];

function findingGroups(findings, records) {
  const byId = {};
  (records || []).forEach((r) => { byId[r.finding_id] = r; });
  const outcomeOf = (f) => (byId[f.id] || {}).outcome || 'open';
  const claimed = new Set();
  const bands = FINDING_BANDS.map((band) => {
    const mine = findings.filter((f) => band.outcomes.includes(outcomeOf(f)));
    mine.forEach((f) => claimed.add(f.id));
    return { band, mine };
  });
  const rest = findings.filter((f) => !claimed.has(f.id));
  if (rest.length) bands.push({
    band: { key: 'other', title: 'Everything else', sub: '', open: false },
    mine: rest,
  });
  return bands.filter(({ mine }) => mine.length).map(({ band, mine }) => {
    const blockers = mine.filter((f) => f.severity === 'blocker').length;
    return `<details class="fg" ${band.open ? 'open' : ''}>
      <summary class="fg-head">
        <span class="fg-title">${esc(band.title)}</span>
        <span class="fg-count">${mine.length}${blockers ? ` · ${blockers} blocker${blockers === 1 ? '' : 's'}` : ''}</span>
      </summary>
      ${band.sub ? `<p class="pk-sub fg-sub">${esc(band.sub)}</p>` : ''}
      ${findingsList(mine, records)}
    </details>`;
  }).join('') || '<p class="dim">No findings were raised.</p>';
}

/* Decisions split by whether they are still yours to make. A ruled decision is
   a record; an unruled one is work, and mixing the two makes the work invisible
   inside a couple of dozen cards. */
function decisionGroups(decisions, rulingByDecision) {
  const open = decisions.filter((x) => !rulingByDecision[x.id]);
  const ruled = decisions.filter((x) => rulingByDecision[x.id]);
  return `
    ${open.map((x) => decisionCard(x, null)).join('')}
    ${ruled.length ? `<details class="fg">
      <summary class="fg-head">
        <span class="fg-title">Already ruled</span>
        <span class="fg-count">${ruled.length}</span>
      </summary>
      ${ruled.map((x) => decisionCard(x, rulingByDecision[x.id])).join('')}
    </details>` : ''}
    ${decisions.length ? '' : '<p class="dim">No decisions were recorded.</p>'}`;
}

function findingsList(findings, records) {
  const byId = {};
  (records || []).forEach((r) => { byId[r.finding_id] = r; });
  const rank = (f) => {
    const r = byId[f.id];
    return r ? (OUTCOME_ORDER[r.outcome] ?? 9) : 2;
  };
  const sorted = [...findings].sort(
    (a, b) => rank(a) - rank(b)
      || (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9)
  );
  return sorted.map((f) => {
    const cls = f.severity === 'blocker' ? 'pill-red' : f.severity === 'major' ? 'pill-amber' : '';
    const r = byId[f.id];
    const out = r ? (OUTCOME[r.outcome] || { label: r.outcome, cls: '' }) : null;
    const history = r && r.attempts
      ? `${r.attempts} repair attempt${r.attempts > 1 ? 's' : ''}`
        + (r.repaired_in_round ? `, fixed in round ${r.repaired_in_round}` : '')
      : '';
    return `<div class="finding ${r && SETTLED_OUTCOMES.includes(r.outcome) ? 'f-settled' : ''}">
      <div class="f-head">
        <span class="pill ${cls}">${esc(f.severity)}</span>
        ${f.category ? `<span class="pill">${esc(f.category)}</span>` : ''}
        ${out ? `<span class="pill ${out.cls}">${esc(out.label)}</span>` : ''}
        <span class="f-title">${esc(f.title)}</span>
      </div>
      <div class="f-detail">${esc(f.detail)}</div>
      ${f.recommendation ? `<div class="f-detail" style="margin-top:.3rem"><b>Recommendation.</b> ${esc(f.recommendation)}</div>` : ''}
      ${r && r.disposition_reason ? `<div class="f-detail" style="margin-top:.3rem">
        <b>Routed as ${esc(r.disposition)}.</b> ${esc(r.disposition_reason)}</div>` : ''}
      ${r && r.outcome_evidence ? `<div class="f-detail" style="margin-top:.3rem">
        <b>On re-check.</b> ${esc(r.outcome_evidence)}</div>` : ''}
      ${history ? `<div class="f-detail dim" style="margin-top:.3rem">${esc(history)}${
        r.commit ? ` · ${esc(r.commit.slice(0, 12))}` : ''}</div>` : ''}
      ${f.evidence ? `<div class="f-evidence">${esc(f.evidence)}</div>` : ''}
    </div>`;
  }).join('');
}

function reviewScreen() {
  const d = state.data;
  const packet = d.packet;
  if (!packet) {
    /* A bare sentence on a blank page: no board, no heading, no way back. */
    return `${featureBar(state.data, { heading: false })}
      <div class="wrap doc-wrap"><div class="narrow">
        <h1 class="sec-title" style="margin-top:1.9rem">There is no packet yet.</h1>
        <p class="empty" style="margin-top:0">A packet is what the review lane produces at the
          end of a build. This feature has not reached it.</p>
      </div></div>`;
  }
  const stats = packet.stats || {};
  const rulingByDecision = {};
  (d.rulings || []).forEach((r) => { rulingByDecision[r.decision_id] = r; });
  const openDecisions = (packet.decisions || []).filter((x) => !rulingByDecision[x.id]).length;
  const settled = d.state.stage === 'accepted' || d.state.stage === 'rejected';

  /* A packet on screen while a run is in flight is last time's reading, and
     every number on it -- checks passed, criteria verified, blockers -- is a
     number this run exists to replace. Shown, because it is still the only
     packet there is and a reader may want it. Never shown as current: the whole
     argument for this console is that it does not present a stale measurement
     as a live one. */
  /* There has to be a packet for one to be replaced, and the phases are on the
     payload rather than on `state` -- read from there, the running station is
     never found and the strip never names one. */
  const superseded = !!d.packet && !SETTLED_STAGES.includes(d.state.stage);
  const runningPhase = (d.phases || []).find((p) => p.status === 'running');


  return `
    ${featureBar(d, { actions: `
          <a class="btn btn-sm" href="${featureHref(d.state.project_id, d.state.feature_id)}?criteria"
            title="The same packet, read requirement first: only the code that serves each one, with the boilerplate folded away."
            >Review by requirement</a>
          <button class="btn btn-quiet btn-sm" id="revalidate" ${d.state.stage !== 'awaiting_verdict' ? 'disabled' : ''}
            title="Measures this branch again from zero: the checks, the breaker, the panel and the finding ledger start from nothing. The code is not touched and the packet you are reading is kept. It takes as long as the last run and comes out of the same rework budget."
            >Verify again</button>
          <button class="btn btn-quiet btn-sm" id="rebuild" ${d.state.stage === 'building' ? 'disabled' : ''}
            title="${REBUILD_TITLE}"
            >Build again</button>
`,
      })}
    ${/* The packet is the same review read the long way round, so it folds away
         the same chrome. A sibling of the column rather than a child of it,
         because a sticky box can only travel as far as its own parent -- and
         this one has to travel the whole packet. */''}
    <div class="wrap doc-tools">${focusToggle()}</div>
    <div class="wrap${superseded ? ' is-superseded' : ''}">
      ${superseded ? `
        <div class="superseded-strip">
          <div>
            <b>This packet is being replaced.</b>
            ${esc(d.state.stage === 'failed'
              ? 'A run was started over this branch and stopped before it produced one.'
              : 'A run is measuring this branch now.')}
            ${runningPhase ? `Currently: <span class="mono">${esc(stepWord(runningPhase.name))}</span>.` : ''}
            Everything below — the verdict, the checks, the criteria, the findings — is the
            previous reading, kept because it is still the only packet there is.
          </div>
          <a class="btn btn-sm" href="${featureHref(d.state.project_id, d.state.feature_id)}?build"
            >Watch the run</a>
        </div>` : ''}
      ${stopBar(d, packet, openDecisions)}

      ${statRow(stats)}

      ${runBand(runSummary(d))}

      ${materialityBand(packet)}

      ${packet.strongest_objection ? `
        <section class="section">
          <h2 class="pk-h">The strongest objection raised</h2>
          <div class="objection"><p>${esc(packet.strongest_objection)}</p></div>
        </section>` : ''}


      <section class="section" id="pk-rulings">
        <h2 class="pk-h">${openDecisions ? `${openDecisions} decision${openDecisions === 1 ? '' : 's'} waiting on you`
          : 'Decisions'}</h2>
        <p class="pk-sub">Ranked by how badly each one needs a person. Everything already ruled
          keeps its ruling below.</p>
        ${decisionGroups(packet.decisions || [], rulingByDecision)}
      </section>

      <section class="section" id="pk-findings">
        <h2 class="pk-h">${(packet.findings || []).length} findings</h2>
        <p class="pk-sub">Grouped by what became of each one. Nothing is dropped for having been
          fixed: a repaired finding is still evidence about the run.</p>
        ${findingGroups(packet.findings || [], packet.records || [])}
      </section>

      <section class="section" id="pk-trace">
        <h2 class="pk-h">Traceability</h2>
        <p class="pk-sub">Computed from worker decisions and blind test tags, never asked for.</p>
        ${traceList(packet.trace || [])}
      </section>

      <section class="section" id="pk-gates">
        <h2 class="pk-h">Checks</h2>
        <div class="card">${((d.gates && d.gates.results) || []).map(gateResultRow).join('')}</div>
      </section>

      <section class="section" id="pk-disclosure">
        <h2 class="pk-h">What the workers disclosed</h2>
        <p class="pk-sub">Verbatim, in their words.</p>
        <ul class="disclosure-list">
          ${(packet.disclosures || []).map((line) => `<li>${esc(line)}</li>`).join('') || '<li class="dim">None recorded.</li>'}
        </ul>
      </section>

      ${(packet.open_questions || []).length ? `
        <section class="section">
          <h2 class="pk-h">Carried into the build unanswered</h2>
          <ul class="disclosure-list">${packet.open_questions.map((q) => `<li>${esc(q)}</li>`).join('')}</ul>
        </section>` : ''}

      ${cordSection(d, packet, settled)}

      ${settled ? `<p class="small dim" style="margin-top:1.5rem">Ruled
        <b>${esc(d.state.stage)}</b> ${esc(rel((d.verdict || {}).at))}.</p>` : ''}
    </div>`;
}

/* --------------------------------------------------------------- 5. failed */

function failedScreen() {
  const d = state.data;
  const phases = d.phases || [];
  const failed = phases.filter((p) => p.status === 'failed');
  const done = phases.filter((p) => p.status === 'done');
  const never = phases.filter((p) => p.status === 'pending');
  const at = failed[0];
  const stn = at ? String(phases.indexOf(at) + 1).padStart(2, '0') : '--';
  const budget = d.budget || {};
  const spent = typeof budget.spent_usd === 'number' ? budget.spent_usd : null;

  return `
    ${featureBar(d, { actions: `
      <button class="btn btn-primary btn-sm" id="retry-build"
        title="Picks the build back up where it stopped. Everything already bought -- the plan, the blind tests, the units that finished -- is reused."
        >Resume the run</button>` })}

    ${/* The band is the run's headline, so it belongs to the column the board
         belongs to. Inside the 720px measure its -gutter bleed would reach the
         rail on the left only by coincidence and stop short on the right. */''}
    <section class="stopbar disrupted">
        <div class="stopbar-in">
          <span class="stopbar-w">Line stopped</span>
          <div>
            <p class="verdict-headline">Station ${esc(stn)} stopped the line${
              at ? ` at <b>${esc(at.name)}</b>` : ''}.</p>
          </div>
        </div>
        ${/* "N never reached" is the dark lamps three inches above, and the
             untouched reserve is a number nobody can act on here -- the cord
             below says what it is being held for. */''}
        <div class="stopbar-foot">
          <span><b>${done.length} of ${phases.length}</b> stations done</span>
          ${spent !== null ? `<span><b>$${spent.toFixed(2)}</b> spent${
            budget.spendable_usd ? ` of $${Number(budget.spendable_usd).toFixed(2)}` : ''}</span>` : ''}
        </div>
    </section>

    <div class="wrap doc-wrap">

      ${/* The reason the line stopped has a heading of its own and sits outside
           the 720px measure, which it does not fit -- it is machine output, and
           wrapping it early costs the reader the shape of it. */''}
      <div class="error-box">
        <h2 class="eb-h">${esc(failed.map((p) => stepWord(p.name)).join(', ') || 'unknown phase')}</h2>
        <pre>${esc(d.state.error || (failed[0] || {}).error || 'no error recorded')}</pre>
      </div>

      <div class="narrow">

      ${/* No lamp strip in prose here. A block listing how many stations
           finished (the green lamps), which one stopped (the red lamp), and how
           many were never reached (the dark lamps) would open with the sentence
           the error above already ends on, and a table naming every station in
           the same order, most of them "never reached", would spend over half
           the page telling you what the strip tells you at a glance. The ledger
           link is on the cord below, where the other ways out are. */''}
      <section class="cord">
        <div class="cord-body">
          <h2>Where this goes next</h2>
          <p>Resuming re-runs only what stopped and keeps everything already bought.
            Discarding removes the checkout and this feature's record; the branch is kept.</p>
          <div class="acts">
            <button class="btn btn-stop" id="retry-build-2">Resume the run</button>
            <button class="btn btn-quiet act-discard"
              data-project="${esc(d.state.project_id)}" data-feature="${esc(d.state.feature_id)}"
              >Discard the run</button>
            <a class="btn btn-quiet" href="${logHref(d.state.project_id, d.state.feature_id)}"
              >Read the ledger</a>
          </div>
          ${/* The band above carries what has been spent. What it cannot carry
               is why the number beside it is held back. */''}
          <p class="cord-note">Resuming spends from the same budget. The repair reserve is held
            back for fixing a packet that comes out defective, and is not available here.</p>
        </div>
      </section>
    </div></div>`;
}
