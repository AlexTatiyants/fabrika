/* A feature being built: the timeline on a shared clock, the calls under each
   step, what each turn produced, and the run drawn as a graph. */

'use strict';

/* ---------------------------------------------------------- the timeline

   A phase list tells you what has finished. It cannot show you that the build
   lane and the verify lane are running at the same time, or that nine scouts
   went out at once -- and the concurrency is the part worth seeing while it
   happens. Bars on a shared clock show it.
   ------------------------------------------------------------------------- */

/* Which phase produced a record. Exact, because the kind says so. */
const KIND_PHASE = {
  scout: 'scout', scout_slice: 'scout',
  interrogation: 'interrogator',
  spec: 'spec_writer', spec_draft: 'spec_writer', spec_check: 'spec_checker',
  plan: 'architect', plan_check: 'plan_checker', cut_review: 'plan_checker', cut_ruling: 'plan_checker',
  worker: 'workers',
  integration: 'integrator',
  oracle: 'oracle', oracle_session: 'oracle', verify_lane_failed: 'oracle',
  oracle_rerun: 'oracle', oracle_revision: 'oracle',
  setup: 'gates',
  gates: 'gates',
  attribution: 'attribution',
  qa: 'qa',
  review: 'review', review_sample: 'review', recheck: 'review',
  breaker: 'breaker', breaker_suite: 'breaker', breaker_session: 'breaker',
  arbiter: 'arbiter',
  repair_plan: 'repairers',
  repair: 'repairers',
  packet_raw: 'rapporteur',
};

const BAND_OF_PHASE = {};
BANDS.forEach((b) => b.phases.forEach((p) => { BAND_OF_PHASE[p] = b.id; }));

/* Stretches of the run where nothing was running. Nearly all of them are the
   human: questions sitting unanswered, a spec sitting unapproved. They dominate
   the clock -- a 24m run can be 9m of work -- and on a shared axis they squeeze
   every bar into a sliver. Under a minute is orchestration overhead between
   phases, not somebody being away, so it stays on the axis. */
const WAIT_MIN_MS = 60000;

/* A gap is the human's if a record they wrote lands inside it. That is the
   whole test: the ledger stamps every answer, approval and verdict with
   role `human`, so nothing has to be inferred from the stage machine. */
function idleGaps(spans, log) {
  const busy = [];
  spans.slice().sort((a, b) => a[0] - b[0]).forEach(([s, e]) => {
    const last = busy[busy.length - 1];
    if (last && s <= last[1]) last[1] = Math.max(last[1], e);
    else busy.push([s, e]);
  });

  const humanAt = (log || []).filter((r) => r.role === 'human')
    .map((r) => new Date(r.at).getTime());

  const gaps = [];
  for (let i = 1; i < busy.length; i += 1) {
    const start = busy[i - 1][1];
    const end = busy[i][0];
    if (end - start < WAIT_MIN_MS) continue;
    gaps.push({
      start, end, ms: end - start,
      human: humanAt.some((at) => at >= start && at <= end),
    });
  }
  return gaps;
}

/* WHAT EACH STATION COST.

   A finished run carries `usage.roles` -- calls and dollars per role -- and
   without it every cost column on the timeline would be blank and the caption
   would never name a figure. Per-record costs
   are better when the ledger has them (they place the money in time); this is
   the fallback for a run whose records carry none, which is most of them.

   Two review roles argue over the same packet, so they land on one station. */
const ROLE_PHASE = {
  scout: 'scout', interrogator: 'interrogator', spec_writer: 'spec_writer', planner: 'spec_writer',
  spec_checker: 'spec_checker', architect: 'architect', plan_checker: 'plan_checker',
  worker: 'workers', integrator: 'integrator', oracle: 'oracle', breaker: 'breaker',
  reviewer: 'review', adversary: 'review', arbiter: 'arbiter',
  repairer: 'repairers', rapporteur: 'rapporteur',
};

/* WHICH MODEL RAN WHICH STATION.

   Every agent record stamps the model it called, so this is a fact the run
   already carries. Almost always it is one model for the whole run -- naming
   it sixteen times down a column would be noise, the way a strip of identical
   lamps is -- so it is said once in the caption, and a station only
   names its own when that station used something else. */
function phaseModels(log) {
  const out = {};
  (log || []).forEach((r) => {
    const phase = KIND_PHASE[r.kind];
    if (!phase || !r.model) return;
    (out[phase] || (out[phase] = new Set())).add(r.model);
  });
  return out;
}

/* A provider routes on `vendor/name`; the part after the slash is the model,
   and that is the half people say out loud. Nothing here knows any particular
   one -- it is whatever the record was stamped with. */
const shortModel = (m) => String(m || '').split('/').pop();

/* The model most of this run went to, and whether it went anywhere else.
   Naming a model only when the run used exactly one would give every station a
   badge the moment a second appeared -- the noise worst in the case the badge
   exists for. The baseline is the common one; a station
   speaks up when it is not on it. */
function runModel(log) {
  const n = {};
  (log || []).forEach((r) => { if (r.model) n[r.model] = (n[r.model] || 0) + 1; });
  const names = Object.keys(n).sort((a, b) => n[b] - n[a]);
  return { model: names[0] || '', only: names.length === 1, count: names.length };
}

/* The runs this feature has had, oldest first.

   The ledger only ever grows (INV-11), so one feature id carries every run it
   has had: the first build, and each "Verify again", repair dispatch and
   rebuild after it. Each of those writes a record the moment it starts, and
   that record is where its run begins. Counting only `rebuild` would draw a
   feature verified again five times as one run -- each pass restarting its
   rounds at 0, the next one's reused steps stamped with the last one's round
   -- reading as rounds 2, 0, 1, 2, 0 in a single tree.

   Resuming a run that stopped is not a new run: it carries on the same one. */
const RUN_STARTS = {
  rebuild: () => 'Build again',
  revalidate: () => 'Verify again',
  // Sent back from the review page. The loop reopening itself mid-run
  // (`rework_reopened`) is not a new run; a person sending it back is.
  dispatch: (r) => ({ repair: 'Repair', reopen_spec: 'Spec reopened' })[
    ((r.payload || {}).route)] || 'Sent back',
};

function runsOf(log) {
  const records = log || [];
  const runs = [{ seq: 0, at: (records[0] || {}).at || '', label: 'Build' }];
  records.forEach((r) => {
    if (RUN_STARTS[r.kind]) {
      runs.push({ seq: r.seq, at: r.at || '', label: RUN_STARTS[r.kind](r) });
    }
  });
  runs.forEach((run, i) => {
    run.end = i + 1 < runs.length ? runs[i + 1].seq : Infinity;
    run.endAt = i + 1 < runs.length ? runs[i + 1].at : '';
    run.index = i;
  });
  return runs;
}

/* The run being looked at: the one a reader picked, or the latest. */
function runShown(log) {
  const runs = runsOf(log);
  return runs.find((r) => r.seq === state.runSeq) || runs[runs.length - 1];
}

/* Where the run being looked at begins, as a seq. 0 for a feature's first. */
function attemptStart(log) {
  return runShown(log).seq;
}

/* When it began, for the phases -- which carry timestamps rather than seqs. */
function attemptAt(log) {
  const run = runShown(log);
  return run.seq ? run.at : '';
}

/* The run's own records. A second argument from a caller is ignored: there is
   no whole-history view -- earlier runs are one link away, each whole on its
   own. */
function attemptScope(log) {
  const run = runShown(log);
  return (log || []).filter((r) => r.seq > run.seq && r.seq < run.end);
}

/* A run named for a reader: what started it, and when. */
function runName(run) {
  return `${run.label} · ${clockDay(run.at)}`;
}

function clockDay(iso) {
  const t = Date.parse(iso || '');
  if (Number.isNaN(t)) return '';
  const d = new Date(t);
  const today = new Date();
  const time = d.toTimeString().slice(0, 5);
  return d.toDateString() === today.toDateString()
    ? time : `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} ${time}`;
}

/* This run, and every other one a link away. Above the timeline and the tree,
   because both show only the run named here. */
function runLinks(log) {
  const runs = runsOf(log);
  if (runs.length < 2) return '';
  const shown = runs.find((r) => r.seq === runShown(log).seq);
  const latest = runs[runs.length - 1];
  const others = runs.filter((r) => r.seq !== shown.seq).reverse();
  return `<nav class="run-links" aria-label="Runs of this feature">
    <span class="run-this">${shown === latest ? 'This run' : 'Showing an earlier run'}:
      <b>${esc(runName(shown))}</b></span>
    ${shown !== latest ? `<button type="button" class="linkish" data-run="${latest.seq}"
      >back to the latest run</button>` : ''}
    <span class="run-others">${shown === latest ? 'Earlier runs' : 'Other runs'}: ${others.map((r) =>
      `<button type="button" class="linkish" data-run="${r.seq}">${esc(runName(r))}</button>`)
      .join('<span class="run-sep"> · </span>')}</span>
  </nav>`;
}

function phaseUsage(d, log, allLog) {
  /* `usage` is written when a packet is assembled. A rebuild started after that
     leaves it describing the *previous* attempt -- so one station can read `6x`
     while the ledger beside it counts 17, both of them wrong about the two
     workers that actually ran. Stale beats absent here: fall through to the
     ledger, which the caller has already scoped.

     The staleness test reads the WHOLE ledger, never the scoped slice. Asking
     the slice is circular -- it begins after the `rebuild` record and so can
     never contain one, the answer is always "not stale", and the stale figure
     is used every time -- exactly the failure this function is here to
     prevent, reintroduced one line lower down. */
  const whole = allLog || log;
  let lastUsage = 0;
  (whole || []).forEach((r) => { if (r.kind === 'usage' && r.seq > lastUsage) lastUsage = r.seq; });
  const stale = attemptStart(whole) > lastUsage;
  const roles = stale ? {} : (((d || {}).usage || {}).roles || {});
  const out = {};
  Object.keys(roles).forEach((role) => {
    const phase = ROLE_PHASE[role] || role;
    const cell = out[phase] || (out[phase] = { calls: 0, cost: 0 });
    cell.calls += roles[role].calls || 0;
    cell.cost += roles[role].cost || 0;
  });
  /* A run still in flight has no `usage` block -- it is written when the run
     ends -- so mid-run the only count is the ledger's. The timeline and the
     list below both fall back to it; if only one did, one would say `1x`
     beside a station the other leaves blank. */
  if (!Object.keys(out).length) {
    (log || []).forEach((r) => {
      const phase = KIND_PHASE[r.kind];
      if (!phase || r.role === 'human') return;
      const cell = out[phase] || (out[phase] = { calls: 0, cost: 0 });
      cell.calls += 1;
      cell.cost += recordCost(r);
    });
  }
  return out;
}

/* Drawn once per gap rather than per row, because a gap is a fact about the
   whole run: by definition no lane was moving. */
const HUMAN_ICON = `<svg viewBox="0 0 12 12" aria-hidden="true">
  <circle cx="6" cy="4.1" r="1.9"/><path d="M2.4 10.6a3.6 3.6 0 0 1 7.2 0"/></svg>`;

function timeline(d, allLog, opts = {}) {
  // This attempt only, unless a reader has asked for the history. Scoped here
  // rather than at each call site so every timeline in the console agrees, and
  // so a station's count is a count of the run being looked at.
  const log = attemptScope(allLog);
  const run = runShown(allLog);
  const latest = run.end === Infinity;
  const since = attemptAt(allLog);
  let phases;
  if (latest) {
    phases = (d.phases || []).filter((p) => p.status !== 'pending');
    // A phase that ended before this run began belongs to an earlier one, and
    // so does one this run only reused: it did not run this time.
    if (since) {
      phases = phases.filter((p) => (p.ended_at || p.started_at || '') >= since
        && !/^reused from the last attempt/.test(p.detail || ''));
    }
  } else {
    // `phases` holds each station's latest run only, which for an earlier run
    // is somebody else's. Its own step records say what it ran.
    const last = {};
    log.filter((r) => r.kind === 'step' && r.payload
        && !/^reused from the last attempt/.test(r.payload.detail || ''))
      .forEach((r) => { last[r.payload.name] = r.payload; });
    phases = Object.values(last);
  }
  if (!phases.length) return '';

  const t = (iso) => (iso ? new Date(iso).getTime() : 0);
  const starts = phases.map((p) => t(p.started_at)).filter(Boolean);
  if (!starts.length) return '';
  const t0 = Math.min(...starts);
  const running = phases.some((p) => p.status === 'running');
  const t1 = Math.max(
    ...phases.map((p) => t(p.ended_at) || Date.now()),
    t0 + 1000,
  );

  // Offered on every screen that draws a timeline. Offered on only some, the
  // same graph would behave differently depending on which page you reached it
  // from -- and a timeline like the reading's or the spec writer's, with no way
  // to collapse the waiting, has an axis that is mostly waiting. The caller
  // still decides, so a timeline drawn somewhere the question is "when", not
  // "how long", can still leave the axis wall-clock.
  const gaps = opts.waitToggle
    ? idleGaps(phases.map((p) => [t(p.started_at), t(p.ended_at) || Date.now()]), log)
    : [];
  const collapsed = gaps.length > 0 && state.hideWait;
  const waited = gaps.reduce((a, g) => a + g.ms, 0);

  const span = Math.max(1, (t1 - t0) - (collapsed ? waited : 0));
  // Time with the gaps squeezed out. A moment inside a gap lands on its start,
  // so nothing can be drawn past the point where the run actually stopped.
  const adjust = (ms) => (collapsed
    ? ms - gaps.reduce((a, g) => a + Math.min(Math.max(0, ms - g.start), g.ms), 0)
    : ms);
  const pct = (ms) => Math.max(0, Math.min(100, ((adjust(ms) - t0) / span) * 100));

  // A call belongs to a phase because of what it is, not because of clock
  // arithmetic: a `scout_slice` record is a scout call even if it was written a
  // second after the phase closed. Position comes from the timestamp, clamped
  // into the bar so a skewed clock cannot put a dot somewhere misleading.
  const marks = {};
  (log || []).forEach((r) => {
    const name = KIND_PHASE[r.kind];
    if (!name) return;
    const phase = phases.find((p) => p.name === name);
    if (!phase) return;
    const start = t(phase.started_at);
    const end = t(phase.ended_at) || Date.now();
    const at = t(r.at);
    (marks[name] || (marks[name] = [])).push({
      at: at >= start && at <= end ? at : end,
      role: r.role || r.kind, out: r.payload_chars || 0, seq: r.seq,
      cost: recordCost(r),
    });
  });

  const usage = phaseUsage(d, log, allLog);
  const models = phaseModels(log);
  const { model: mainModel, only: oneModel, count: modelCount } = runModel(log);
  const rows = phases.map((p) => {
    const start = t(p.started_at);
    const end = t(p.ended_at) || Date.now();
    const left = pct(start);
    const width = Math.max(0.6, pct(end) - left);
    const band = BAND_OF_PHASE[p.name] || '';
    const calls = marks[p.name] || [];
    const biggest = Math.max(1, ...calls.map((c) => c.out));
    /* The records first, the run's own total second. */
    const fallback = usage[p.name] || { calls: 0, cost: 0 };
    const cost = calls.reduce((a, c) => a + c.cost, 0) || fallback.cost;
    /* usage.roles is the run's own count; the ledger holds only the records it
       was given. The list below reads the same number, and two counts for one
       station on one screen is how you lose a reader. */
    const nCalls = fallback.calls || calls.length;
    /* Colour it by the same map that writes the label beside it. BANDS and
       PHASE_LANE both group these sixteen stations and they do not agree --
       `gates` is review in one and verify in the other -- so a bar coloured
       from one map under a label written by the other contradicts itself. */
    const lane = band;

    return `<div class="tl-row">
      <span class="tl-name ${p.status === 'running' ? 'live' : ''}">${esc(stepWord(p.name))}</span>
      <span class="tl-band mono lane-${esc(lane)}">${esc(band)}</span>
      <span class="tl-track">
        <span class="tl-bar lane-${esc(lane)} ${esc(p.status)}" style="left:${left}%;width:${width}%"
          title="${esc(stepWord(p.name))} · ${dur(p.elapsed_s)}${p.detail ? ' · ' + esc(p.detail) : ''}${
            (models[p.name] ? [...models[p.name]] : []).map((m) => ` · ${esc(m)}`).join('')
          }"></span>
        ${calls.map((c) => `<span class="tl-mark" style="left:${pct(c.at)}%;
          --size:${(3 + (c.out / biggest) * 5).toFixed(1)}px"
          title="${esc(c.role)} returned ${bytes(c.out)} characters${
            c.cost ? ` for ${usd(c.cost)}` : ''}"></span>`).join('')}
      </span>
      <span class="tl-calls mono" title="agent calls this station made">${
        nCalls ? `${nCalls}\u00d7` : ''}</span>
      <span class="tl-time mono">${p.status === 'running' ? '' : dur(p.elapsed_s)}</span>
      <span class="tl-cost mono" title="what this station spent">${usd2(cost)}</span>
    </div>`;
  }).join('');

  const total = (t1 - t0 - (collapsed ? waited : 0)) / 1000;
  const agents = Object.values(marks).reduce((a, m) => a + m.length, 0)
    || Object.values(usage).reduce((a, u) => a + u.calls, 0);
  // Every record's cost, not just the ones a bar could claim: a call that fell
  // outside every phase still spent the money.
  const spent = logCost(log) || Number(((d.usage || {}).total_cost) || 0);

  const waitMarks = collapsed ? gaps.map((g) => `<span class="tl-wait ${g.human ? 'human' : ''}"
    style="left:${pct(g.start)}%" title="${g.human
      ? `${dur(g.ms / 1000)} waiting for you`
      : `${dur(g.ms / 1000)} with nothing running`}">${g.human ? HUMAN_ICON : ''}</span>`).join('') : '';

  const toggle = gaps.length ? `<button type="button" class="tl-toggle" id="toggle-wait"
    aria-pressed="${collapsed}">${collapsed ? 'show wait time' : 'hide wait time'}</button>` : '';


  return `<section class="timeline ${running ? 'live' : ''}">
    <p class="eyebrow">Timeline${
      runsOf(allLog).length < 2 ? '' : latest ? ' · this run' : ` · ${esc(runName(run))}`} · ${dur(total)}${collapsed ? ' working' : ''}${
      agents ? ` · ${agents} agent calls` : ''}${
      collapsed ? ` · ${dur(waited / 1000)} waiting hidden` : ''}${
      spent ? ` · ${usd2(spent)}` : ''}${
      oneModel ? ` · ${esc(shortModel(mainModel))}`
        : modelCount > 1 ? ` · ${modelCount} models` : ''}${
      running ? ' · running' : ''}${toggle}</p>
    <div class="tl-plot ${collapsed ? 'collapsed' : ''}">
      ${waitMarks ? `<div class="tl-waits"><i></i><i class="tl-w-band"></i>
        <span class="tl-w-track">${waitMarks}</span></div>` : ''}
      <div class="tl-rows">${rows}</div>
    </div>
    <p class="small dim tl-legend">Bars that overlap ran at the same time. The count is what the
      run made; each dot is a call the ledger recorded, placed where it returned and sized by how
      much it wrote.${collapsed
        ? ' The clock skips the stretches where nothing was running: each marker is one of them,'
          + ' and the figure means the run was waiting on you.'
        : ''}</p>
  </section>`;
}

/* -------------------------------------------------------------- 3. running */

/* Which lane each station belongs to. Sixteen ungrouped rows is a list you
   scroll; four lanes of three to five is a line you read. */
const PHASE_LANE = {
  scout: 'plan', interrogator: 'plan', spec_writer: 'plan', planner: 'plan', spec_checker: 'plan',
  architect: 'build', plan_checker: 'build', workers: 'build', integrator: 'build',
  oracle: 'verify', oracle_session: 'verify', verify_lane_failed: 'verify',
  oracle_rerun: 'verify', oracle_revision: 'verify',
  gates: 'verify', attribution: 'verify',
  qa: 'verify', breaker: 'verify', breaker_session: 'verify',
  review: 'review', arbiter: 'review', repairers: 'review',
  rapporteur: 'review',
};

/* Every model call this attempt made, in the order it made them.

   Nothing here is inferred. Each row is one call: the station it belonged to,
   the role, the model it asked for and the route it went to, and what came
   back. A refusal is its own row, and the substitute that answered in its
   place is the next one, marked as standing in -- so "which model did this
   phase run on" stops being a question the page has to answer for you. */
/* ---- the calls under each step ------------------------------------------
   One list, not two. What each step produced and which model did the work
   are two answers a reader wants about the same step -- so a step's calls
   open underneath it. */

/* A refusal as a person would say it. Older records carry the route's raw
   output here; that is shown when the row is opened, never as the sentence. */
function plainReason(c) {
  const r = String(c.reason || '');
  if (/credit/i.test(r)) return 'out of credits';
  if (/limit/i.test(r)) return 'at its usage limit';
  if (!r || r.length > 80 || r.includes('{')) return c.outcome === 'timed_out' ? 'timed out' : '';
  return r;
}

/* Each call once, in order, with any refusals folded into the call that
   answered in their place: same agent, and the answer names the refusing route
   as the one it stood in for. A refusal nothing replaced stays its own row. */
function foldCalls(calls) {
  const rows = calls.map((r) => ({ r, refused: [] }));
  const used = new Set();
  rows.forEach((row, i) => {
    const c = row.r.payload;
    if (!c.fallback_from || c.outcome === 'refused') return;
    for (let k = i - 1; k >= 0; k -= 1) {
      const p = rows[k].r.payload;
      if (used.has(k)) continue;
      if (p.outcome === 'refused' && p.role === c.role && p.route === c.fallback_from) {
        used.add(k);
        row.refused.unshift(rows[k].r);
      }
    }
  });
  return rows.filter((_, k) => !used.has(k));
}

/* Routes that refused, said once: which, why, since when, and where the work
   went instead. */
function routeTrouble(calls) {
  const out = {};
  calls.forEach((r) => {
    const c = r.payload;
    if (c.outcome === 'refused') {
      const t = out[c.route] || (out[c.route] = { since: r.at, why: '', went: new Set(), moved: 0 });
      t.why = t.why || plainReason(c) || 'refused';
    }
  });
  calls.forEach((r) => {
    const c = r.payload;
    const t = c.fallback_from && out[c.fallback_from];
    if (t && c.outcome === 'answered') {
      t.moved += 1;
      t.went.add(shortModel(c.answered || c.asked || ''));
    }
  });
  return Object.entries(out).map(([route, t]) => `
    <div class="route-trouble">
      <b>${esc(route)} was ${esc(t.why)} from ${esc(clock(t.since))}.</b>
      ${t.moved ? `${t.moved} of its calls went to ${esc([...t.went].join(', '))} instead.`
                : 'Nothing stood in for it.'}
    </div>`).join('');
}

function callRow(row, station) {
  const c = row.r.payload;
  const ok = c.outcome === 'answered';
  const word = ok ? 'answered'
    : c.outcome === 'refused' ? `refused${plainReason(c) ? ` · ${plainReason(c)}` : ''}`
    : `${c.outcome === 'timed_out' ? 'timed out' : 'failed'}`;
  const tokens = (c.prompt_tokens || 0) + (c.completion_tokens || 0);
  const after = row.refused.length
    ? `after ${esc(c.fallback_from)} refused${row.refused.length > 1 ? ` ×${row.refused.length}` : ''}`
    : c.fallback_from && c.reason ? `in place of ${esc(c.reason)}` : '';
  const who = c.role && c.role !== station ? `${esc(c.role)} · ` : '';
  const raw = [...row.refused.map((x) => x.payload.detail || x.payload.reason), c.detail]
    .filter(Boolean).join('\n\n');
  const open = state.openCalls && state.openCalls.has(row.r.seq);
  return `<div class="scall ${ok ? 'ok' : 'bad'}">
      <span class="mono dim">${esc(clock(row.r.at))}</span>
      <span>${who}<span class="mono">${esc(shortModel(c.answered || c.asked || '') || '—')}</span>
        ${after ? `<span class="dim"> · ${after}</span>` : ''}</span>
      <span class="scall-out">${esc(word)}</span>
      <span class="num">${c.duration_s ? dur(c.duration_s) : ''}</span>
      <span class="num">${tokens ? bytes(tokens) : '—'}</span>
      <span class="num">${callSpend(c) ? usd(callSpend(c)) : '—'}</span>
      ${raw ? `<button class="linkish scall-raw-toggle" data-call-raw="${esc(row.r.seq)}"
        >${open ? 'hide' : 'what the route said'}</button>` : ''}
      ${raw && open ? `<pre class="scall-raw">${esc(raw.slice(-4000))}</pre>` : ''}
    </div>`;
}

/* ---- what a turn produced ------------------------------------------------
   The row says how a turn went -- `6/19 passed`, `2 probe(s), 2 failing`,
   `7 blind test file(s)`. This says which six, which probes, which files.

   Plain words first and the list under them: a section leads with the sentence
   its own agent wrote about the whole of it, then the standing caveat when the
   kind of evidence has one, then one row per thing. The caveat is not
   decoration -- a wall of green with no stated limits is what teaches a reader
   to skim, and a passing breaker probe in particular proves nothing at all.

   Nothing here is inferred by the console: `readout` is assembled by the
   server from the station's own records. See `step_readout`. */
function readoutBlock(sections, ledger) {
  if (!sections || !sections.length) return '';
  return sections.map((sec) => `
    <div class="rout">
      <p class="rout-h">${esc(sec.what)}${sec.group ? ` · <b>${esc(sec.group)}</b>` : ''}</p>
      ${sec.note ? `<p class="rout-said">${esc(sec.note)}</p>` : ''}
      ${sec.caveat ? `<p class="rout-limit">${esc(sec.caveat)}</p>` : ''}
      ${(sec.entries || []).map((e) => `
        <div class="rout-e ${e.tone ? `t-${esc(e.tone)}` : ''}">
          <span class="rout-t">${esc(e.text)}</span>
          ${e.outcome ? `<span class="rout-o">${esc(e.outcome)}</span>` : ''}
          ${e.note ? `<span class="rout-n">${esc(e.note)}</span>` : ''}
        </div>`).join('')}
      ${sec.more ? `<p class="rout-more">${sec.more} more, in
        <a href="${ledger}">the ledger</a>.</p>` : ''}
    </div>`).join('');
}

/* What the control under the call column says. The call count when there were
   calls -- that is the column, and it is what the reader has learned to read
   -- and otherwise how many things the readout holds, because a station that
   bought nothing from a model still did the work this row opens onto. */
function stepOpener(row) {
  if (row.calls.length) {
    return `${row.calls.length} call${row.calls.length === 1 ? '' : 's'}`;
  }
  const first = (row.readout || [])[0];
  if (!first) return '';
  const n = (row.readout || []).reduce((a, sec) => a + (sec.entries || []).length
    + (sec.more || 0), 0);
  return `${n} ${esc(first.noun)}`;
}

/* ---- the run as a graph ---------------------------------------------------
   One row per agent turn, top to bottom in the order they started. The line on
   the left is the run: parallel work branches off it and joins back where the
   work comes together, the oracle keeps its own line because it works blind,
   and each repair round is its own band. Every run of a step is its own row,
   read from `step` records -- the phase list keeps only the latest of each. */

/* Steps whose work runs in parallel, and what separates the parallel parts. */
const STEP_FANOUT = { workers: 'unit', repairers: 'unit', review: 'role' };
const STEP_WHO = { workers: 'worker', repairers: 'repairer' };

/* A time of day on the reader's clock. Records are stamped in UTC. */
function clock(iso) {
  const t = Date.parse(iso || '');
  return Number.isNaN(t) ? '' : new Date(t).toTimeString().slice(0, 5);
}

/* Stations a run can take from an earlier one rather than run itself: intake,
   and the build a "Verify again" keeps. Anything after them is the run's own. */
const CARRIED_STATIONS = ['scout', 'interrogator', 'spec_writer', 'spec_checker', 'architect',
  'plan_checker', 'workers',
  'integrator', 'oracle'];
const REUSED_WORDS = /^reused from the last attempt(?: · )?/;
/* The record each of those stations leaves behind, which says when it ran. */
const STATION_RECORD = {
  scout: 'scout', interrogation: 'interrogator', spec: 'spec_writer', plan: 'architect',
  worker: 'workers', integration: 'integrator', oracle: 'oracle',
};

function stepRuns(d, scoped, allLog = scoped) {
  const run = runShown(allLog);
  const latest = run.end === Infinity;
  let steps = scoped.filter((r) => r.kind === 'step' && r.payload)
    /* `readout` is not part of the step the pipeline wrote: it is what the
       station's *other* records say it produced, gathered by the server from
       the same window this step declares. See `readouts_for`. */
    .map((r) => ({ ...r.payload, readout: r.readout || [], key: `s${r.seq}`, seq: r.seq }));
  if (!steps.length && latest && !run.seq) {
    /* A run from before steps were recorded: the phase list is all there is. */
    steps = (d.phases || []).filter((p) => p.status !== 'pending' && p.status !== 'running')
      .map((p) => ({ ...p, round: 0, key: `p-${p.name}` }));
  }
  if (latest) {
    (d.phases || []).filter((p) => p.status === 'running').forEach((p) => steps.push({
      ...p, round: CARRIED_STATIONS.includes(p.name) ? 0 : d.state.rework_round || 0,
      key: `run-${p.name}` }));
  }
  steps.sort((a, b) => String(a.started_at).localeCompare(String(b.started_at)));

  /* What this run did not run itself. A "Verify again" keeps the build, and a
     step it merely reused is drawn as the earlier run's, greyed and named as
     such -- not as work done now, and not under a round it had no part in. */
  const reused = steps.filter((s) => REUSED_WORDS.test(s.detail || ''));
  const own = steps.filter((s) => !REUSED_WORDS.test(s.detail || ''));
  // A repair round's oracle fixing its files is not the oracle writing the
  // suite; only a station's own first-pass step means it ran this time.
  const ranHere = new Set(own.filter((s) => !(s.round > 0)).map((s) => s.name));
  const runs = runsOf(allLog);
  const earlier = {};
  (allLog || []).forEach((r) => {
    if (r.kind !== 'step' || !r.payload || r.seq >= run.seq) return;
    if (REUSED_WORDS.test(r.payload.detail || '') || r.payload.round > 0) return;
    earlier[r.payload.name] = { ...r.payload, readout: r.readout || [], seq: r.seq };
  });
  const before = (p) => run.seq && p.started_at && p.started_at < run.at
    && !REUSED_WORDS.test(p.detail || '');
  /* Where a station's work was last actually produced, when no step record
     says so -- steps have only been recorded since the Call Tree existed, and a
     build reused across several runs was made before that. The ledger kept
     what each one produced, stamped, and that is the original. */
  const produced = {};
  (allLog || []).forEach((r) => {
    const name = STATION_RECORD[r.kind];
    if (!name || r.seq >= run.seq || (r.meta || {}).revision) return;
    produced[name] = { name, started_at: r.at, seq: r.seq };
  });
  const fromOf = (step) => {
    const home = step.seq !== undefined
      ? runs.find((r) => step.seq > r.seq && step.seq < r.end)
      : runs.find((r) => (!r.at || step.started_at >= r.at) && (!r.endAt || step.started_at < r.endAt));
    return home ? runName(home) : 'an earlier run';
  };
  const carried = [];
  if (run.seq) {
    CARRIED_STATIONS.forEach((name) => {
      if (ranHere.has(name)) return;
      const kept = reused.find((s) => s.name === name);
      const original = earlier[name]
        || (d.phases || []).find((p) => p.name === name && before(p)) || produced[name];
      if (!original && !kept) return;
      // Some records carry "seams already closed", a fixed label on every
      // reused integrator, true or not. It is not carried forward as though it were a fact.
      const detail = String((original && original.detail) || (kept && kept.detail) || '')
        .replace(REUSED_WORDS, '').replace(/^seams already closed$/, '');
      carried.push({
        ...(original || kept), name, round: 0, detail, error: '',
        key: `c-${name}`, carried: true, known: !!original,
        from: original ? fromOf(original) : 'an earlier run',
      });
    });
  }
  return [...carried, ...own];
}

function callsIn(step, calls) {
  const from = Date.parse(step.started_at) - 1000;
  const to = step.ended_at ? Date.parse(step.ended_at) + 2000 : Infinity;
  return calls.filter((r) => (r.meta || {}).phase === step.name
    && Date.parse(r.at) >= from && Date.parse(r.at) <= to);
}

function turnFacts(calls) {
  const answered = calls.filter((r) => r.payload.outcome === 'answered');
  const refused = calls.filter((r) => r.payload.outcome === 'refused');
  const models = [...new Set(answered.map((r) => shortModel(r.payload.answered || r.payload.asked || '')))]
    .filter(Boolean);
  const routes = [...new Set(refused.map((r) => r.payload.route))];
  const note = refused.length
    ? `${answered.length ? 'after ' : ''}${routes.join(', ')} refused${refused.length > 1 ? ` ×${refused.length}` : ''}`
    : '';
  const starts = calls.map((r) => r.payload.started_at * 1000).filter(Boolean);
  const ends = calls.map((r) => (r.payload.started_at + (r.payload.duration_s || 0)) * 1000).filter(Boolean);
  const cost = calls.reduce((a, r) => a + callSpend(r.payload), 0);
  return { models, note, noAnswer: refused.length && !answered.length, cost,
           span: starts.length ? (Math.max(...ends) - Math.min(...starts)) / 1000 : 0,
           first: starts.length ? Math.min(...starts) : 0 };
}

function stepLane(s) {
  if (s.name === 'rapporteur') return 'Packet';
  if (s.round > 0) return `Round ${s.round}`;
  const l = PHASE_LANE[s.name] || 'review';
  return l.charAt(0).toUpperCase() + l.slice(1);
}

function graphRows(steps, calls) {
  const rows = [];
  steps.forEach((s, si) => {
    // A step carried from an earlier run made none of this run's calls.
    const mine = s.carried ? [] : callsIn(s, calls);
    const recheck = s.name === 'review' && /^re-check/.test(s.detail || '');
    const by = !recheck && STEP_FANOUT[s.name];
    const groups = {};
    if (by) {
      mine.forEach((r) => {
        const k = by === 'unit' ? r.payload.unit : r.payload.role;
        if (k) (groups[k] || (groups[k] = [])).push(r);
      });
    }
    /* The oracle runs beside the build, not after it: it takes the band of
       the row it branched beside rather than splitting that band in two. */
    const lane = s.name === 'oracle' && !(s.round > 0) && rows.length
      ? rows[rows.length - 1].lane : stepLane(s);
    /* The work this turn produced, in sections. A station the tree draws as
       parallel rows -- a worker per unit, a reviewer per role -- has a section
       per unit or role, and each row takes its own; a station drawn as one row
       takes all of them. */
    const sections = s.readout || [];
    const base = {
      step: si, lane, oracle: s.name === 'oracle', readout: sections,
      reused: !!s.carried || /reused from the last attempt/.test(s.detail || ''),
      carried: !!s.carried, from: s.from || '', known: !!s.known,
      bad: s.status === 'failed'
        || (() => { const m = /(\d+)\/(\d+) passed/.exec(s.detail || ''); return m && m[1] !== m[2]; })(),
      running: s.status === 'running',
    };
    const keys = Object.keys(groups);
    if (keys.length > 1) {
      keys.map((k) => ({ k, f: turnFacts(groups[k]) }))
        .sort((a, b) => a.f.first - b.f.first)
        .forEach(({ k, f }, i) => rows.push({
          ...base, key: `${s.key}:${k}`, fan: i, calls: groups[k], facts: f,
          at: f.first ? new Date(f.first).toISOString() : s.started_at,
          who: STEP_WHO[s.name] ? `${STEP_WHO[s.name]} · unit ${k}` : k,
          readout: sections.filter((x) => x.group === k),
          what: i === 0 && s.detail ? s.detail : `${groups[k].length} call${groups[k].length === 1 ? '' : 's'}`,
          took: f.span,
        }));
    } else {
      rows.push({
        ...base, key: s.key, fan: 0, calls: mine, facts: turnFacts(mine), at: s.started_at,
        who: recheck ? 're-check' : s.name === 'oracle' && s.round > 0 ? 'oracle · fixes its files' : s.name,
        what: (s.detail || '').replace(/^re-check · /, '') + (s.error ? ` · ${s.error}` : ''),
        took: s.ended_at ? (Date.parse(s.ended_at) - Date.parse(s.started_at)) / 1000 : s.elapsed_s,
      });
    }
  });
  return rows;
}

/* Where each row's node sits, and which rows its line comes from and goes to. */
function graphEdges(rows, steps) {
  const fanWidth = Math.max(1, ...rows.map((r) => r.fan + 1));
  const oracleCol = Math.max(2, fanWidth);
  rows.forEach((r) => { r.col = r.oracle ? oracleCol : r.fan; });
  const slots = [];
  rows.forEach((r, i) => {
    if (r.oracle) return;
    const last = slots[slots.length - 1];
    if (last && last.step === r.step) last.rows.push(i);
    else slots.push({ step: r.step, rows: [i] });
  });
  const edges = [];
  for (let k = 1; k < slots.length; k += 1) {
    const A = slots[k - 1].rows, B = slots[k].rows;
    if (A.length > 1 && B.length > 1) {
      B.forEach((b, i) => edges.push([A[Math.min(i, A.length - 1)], b]));
      A.slice(B.length).forEach((a) => edges.push([a, B[0]]));
    } else {
      A.forEach((a) => B.forEach((b) => edges.push([a, b])));
    }
  }
  /* The oracle leaves the line after the last step that finished before it
     began, and rejoins at the first step that began after it finished. */
  const t = (s) => Date.parse(s);
  rows.forEach((r, i) => {
    if (!r.oracle) return;
    const s = steps[r.step];
    let parent = -1, child = -1;
    rows.forEach((q, j) => {
      if (q.oracle) return;
      const qs = steps[q.step];
      if (qs.ended_at && t(qs.ended_at) <= t(s.started_at) + 1000 && j < i) parent = j;
      if (child < 0 && j > i && s.ended_at && t(qs.started_at) >= t(s.ended_at) - 1000) child = j;
    });
    if (parent >= 0) edges.push([parent, i]);
    if (child >= 0) edges.push([i, child]);
  });
  return { edges, width: oracleCol + 1 };
}

const GX = (c) => 10 + c * 18;
const GROW = 40;

/* One row's slice of the graph: what passes through, what arrives, what leaves. */
function graphCell(i, rows, edges, width, { between = false } = {}) {
  const w = GX(width - 1) + 10;
  const parts = [];
  const colour = (r) => (r.oracle ? 'var(--graph-oracle)' : 'var(--graph-main)');
  edges.forEach(([a, b]) => {
    const A = rows[a], B = rows[b];
    const branch = B.col > A.col;
    const pass = branch ? B.col : A.col;
    const stroke = colour(branch ? B : A);
    const line = (x1, y1, x2, y2) =>
      `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" stroke-width="2"/>`;
    if (between) {
      if (a <= i && i < b) parts.push(line(GX(pass), 0, GX(pass), '100%'));
      return;
    }
    if (a < i && i < b) parts.push(line(GX(pass), 0, GX(pass), GROW));
    if (a === i) {
      if (A.col === B.col || !branch) parts.push(line(GX(A.col), GROW / 2, GX(A.col), GROW));
      else parts.push(`<path d="M${GX(A.col)} ${GROW / 2} C${GX(A.col)} ${GROW * 0.85} ${GX(B.col)} ${GROW * 0.7} ${GX(B.col)} ${GROW}" fill="none" stroke="${stroke}" stroke-width="2"/>`);
    }
    if (b === i) {
      if (A.col === B.col || branch) parts.push(line(GX(B.col), 0, GX(B.col), GROW / 2));
      else parts.push(`<path d="M${GX(A.col)} 0 C${GX(A.col)} ${GROW * 0.3} ${GX(B.col)} ${GROW * 0.15} ${GX(B.col)} ${GROW / 2}" fill="none" stroke="${stroke}" stroke-width="2"/>`);
    }
  });
  if (!between) {
    const r = rows[i];
    const fill = r.bad ? 'var(--red)' : r.reused ? 'var(--paper, #fff)' : colour(r);
    parts.push(`<circle cx="${GX(r.col)}" cy="${GROW / 2}" r="5" fill="${fill}"
      stroke="${r.reused ? colour(r) : 'var(--paper, #fff)'}" stroke-width="2"
      ${r.running ? 'class="graph-now"' : ''}/>`);
  }
  return `<svg class="graph-cell ${between ? 'between' : ''}" width="${w}" height="${between ? '100%' : GROW}"
    ${between ? 'preserveAspectRatio="none"' : ''} aria-hidden="true">${parts.join('')}</svg>`;
}

function runGraph(d, scoped, calls, note) {
  const steps = stepRuns(d, scoped, state.log);
  if (!steps.length) return '';
  const ledger = logHref(d.state.project_id, d.state.feature_id);
  const rows = graphRows(steps, calls);
  const { edges, width } = graphEdges(rows, steps);
  const open = state.openSteps || new Set();
  let line = 2;
  const sizes = ['28px'];
  const cells = [];
  const lanes = [];
  rows.forEach((r, i) => {
    const f = r.facts;
    /* A station that bought nothing from a model still did work: the gates can
       run nineteen commands and the QA station settle fourteen criteria, both
       without a single call. Counting calls alone, those rows would have
       nothing to open and so say nothing about what they had done. */
    const hasMore = r.calls.length || (r.readout || []).length;
    const isOpen = open.has(r.key) && hasMore;
    const rowLine = line;
    const lane = lanes[lanes.length - 1];
    if (lane && lane.name === r.lane) lane.end = rowLine + (isOpen ? 2 : 1);
    else lanes.push({ name: r.lane, start: rowLine, end: rowLine + (isOpen ? 2 : 1) });
    /* A step this run did not run: the earlier run's, greyed, and saying so in
       the words rather than only in the colour. */
    const cc = r.carried ? ' carried' : '';
    const what = r.carried
      ? `not run this time · from ${r.from}${r.what ? ` · ${r.what}` : ''}` : r.what;
    cells.push(`
      <div class="rg-g${cc}" style="grid-row:${rowLine}">${graphCell(i, rows, edges, width)}</div>
      <div class="rg-c rg-t mono${cc}" style="grid-row:${rowLine}">${esc(
        r.carried && !r.known ? '' : clock(r.at))}</div>
      <div class="rg-c rg-w ${r.running ? 'now' : ''}${cc}" style="grid-row:${rowLine}"
        title="${esc(r.who + (what ? ` · ${what}` : ''))}">
        <span class="rg-who">${esc(r.who)}</span>${what
          ? `<span class="rg-what ${r.bad && !r.carried ? 'bad' : ''}"> · ${esc(what)}</span>` : ''}</div>
      <div class="rg-c rg-model${cc}" style="grid-row:${rowLine}"><span class="mono">${
        esc(f.models.join(', ') || (r.calls.length ? '' : '—'))}</span>${f.note
          ? `<small class="${f.noAnswer ? 'bad' : ''}">${esc(f.note)}</small>` : ''}</div>
      <div class="rg-c rg-n rg-k${cc}" style="grid-row:${rowLine}">${hasMore
        ? `<button class="linkish" data-step="${esc(r.key)}"
            title="What this turn produced${r.calls.length ? ', and every call it made' : ''}"
            >${stepOpener(r)} ${isOpen ? '▾' : '▸'}</button>` : ''}</div>
      <div class="rg-c rg-n rg-cost mono${cc}" style="grid-row:${rowLine}">${f.cost ? usd(f.cost) : ''}</div>
      <div class="rg-c rg-n rg-took mono${cc}" style="grid-row:${rowLine}">${
        r.took && !r.carried ? dur(r.took) : ''}</div>`);
    line += 1;
    sizes.push('40px');
    if (isOpen) {
      cells.push(`
        <div class="rg-g" style="grid-row:${line}">${graphCell(i, rows, edges, width, { between: true })}</div>
        <div class="rg-open" style="grid-row:${line}">
          ${readoutBlock(r.readout, ledger)}
          ${r.calls.length ? `<p class="rout-h">Every call it made</p>
            <div class="scall head"><span>Time</span><span>Model</span><span>Result</span>
              <span class="num">Took</span><span class="num">Tokens</span><span class="num">Cost</span></div>
            ${foldCalls(r.calls).map((row) => callRow(row, steps[r.step].name)).join('')}` : ''}
        </div>`);
      line += 1;
      sizes.push('auto');
    }
  });
  const laneCells = lanes.map((l) => `
    <div class="rg-lane ${/^Round/.test(l.name) ? 'round' : ''}" style="grid-row:${l.start} / ${l.end}">
      <span>${esc(l.name)}</span></div>`).join('');
  const bands = lanes.filter((l) => /^Round/.test(l.name)).map((l) => `
    <div class="rg-band" style="grid-row:${l.start} / ${l.end}"></div>`).join('');
  /* A thick rule where one phase hands over to the next, so round 1 and
     round 2 read as separate blocks rather than one run-on tint. */
  const dividers = lanes.slice(1).map((l) => `
    <div class="rg-divide" style="grid-row:${l.start}"></div>`).join('');
  /* The width of the graph's own column. `.rg-next` indents to it and is not
     inside the grid, so it cannot inherit -- it is given the same value here
     rather than guessing one in the stylesheet. */
  const gw = `--graph-w:${GX(width - 1) + 10}px`;
  return `<div class="rgraph" style="${gw};grid-template-rows:${sizes.join(' ')}">
    <div class="rg-head" style="grid-row:1"><span></span><span></span><span>Time</span>
      <span>Agent · what it did</span><span>Model</span><span></span>
      <span class="rg-n">Cost</span><span class="rg-n">Took</span></div>
    ${bands}${dividers}${laneCells}${cells.join('')}</div>`
    + (note ? `<p class="rg-next" style="${gw}">${note}</p>` : '');
}

function runningScreen() {
  const d = state.data;
  const phases = d.phases || [];
  const settled = SETTLED_STAGES.includes(d.state.stage);
  const notNeeded = (p) =>
    settled && p.status === 'pending' && CONDITIONAL_PHASES.includes(p.name);

  const done = phases.filter((p) => p.status === 'done').length;
  const owed = phases.length - phases.filter(notNeeded).length;
  const running = phases.find((p) => p.status === 'running');
  const failedPhase = phases.find((p) => p.status === 'failed');

  /* The same screen is the live one and the record of what the build did: you
     reach it by waiting, and later by clicking `build` on a finished feature.
     Told in the present tense either way, it would say a run that ended hours
     ago is still going and promise a question that has already been asked. */
  const live = d.state.stage === 'building';

  /* `live` is the stage's claim. This is the operating system's answer, and
     when the two disagree the stage is the one that is wrong: the process that
     owned the run is gone, and nothing will ever move it again. It has to be
     said on this screen above all, because this is the screen you wait on --
     without it the band would promise a question that nothing is left to ask. */
  const stalled = live && d.orphaned;

  const word = stalled ? 'Line stopped' : live ? 'En route' : 'What the build did';
  const pass = livePass(d);
  const sinceRound = pass && pass.since
    ? phases.filter((p) => p.status === 'done' && p.started_at >= pass.since).length : 0;
  const sentence = stalled
    ? `<b>Nothing is working on this run.</b> The process that owned it is gone, so it stopped
        where it stood${running ? ` at <b>${esc(running.name)}</b>` : ''} — ${done} of ${owed}
        stations complete. Resuming re-runs only what stopped and keeps everything already bought.`
    : live
    ? `${pass ? `<b class="pass-word">${esc(pass.label)}.</b> ` : ''}<b>No input is needed until this finishes.</b> ${
        pass && pass.since
          ? `${sinceRound} of ${owed} stations have run again since the round began`
          : `${done} of ${owed} stations complete`}${
        running ? `, now at <b>${esc(running.name)}</b>` : ''}. The next thing you will be asked
        is whether to release the line.`
    : `${done} of ${owed} stations complete${
        d.state.stage === 'awaiting_verdict'
          ? `, and the packet is waiting on you — <a href="${featureHref(
              d.state.project_id, d.state.feature_id)}">read it</a>`
          : ''}.`;

  const budget = d.budget || {};
  const spent = typeof budget.spent_usd === 'number' ? budget.spent_usd : null;
  const foot = [
    /* The sentence directly above already says "N of M stations complete". */
    phases.length - owed
      ? `<span><b>${phases.length - owed}</b> had nothing to do</span>` : '',
    spent !== null
      ? `<span><b>$${spent.toFixed(2)}</b>${budget.spendable_usd
          ? ` of $${Number(budget.spendable_usd).toFixed(2)} spendable` : ' spent'}</span>` : '',
    failedPhase ? `<span><b>${esc(failedPhase.name)}</b> failed</span>` : '',
  ].filter(Boolean).join('');

  /* The run as it happened: every agent's turn, where it branched and where
     it joined, and each repair round as its own band. See `runGraph`. */
  const scoped = attemptScope(state.log);
  const calls = scoped.filter((r) => r.kind === 'call' && r.payload);
  const modelSeconds = calls.reduce((a, r) => a + (r.payload.duration_s || 0), 0);
  const callCost = calls.reduce((a, r) => a + callSpend(r.payload), 0);
  /* Counted because a caption naming model time and nothing else, over a run
     that did every turn on a subscription, reads as a run that cost nothing.
     It did cost something: this. A subscription is metered in tokens, so
     tokens are the unit -- the notional dollars the route reports are what the
     same work would have cost on an API, and a `$` in this row reads as money
     somebody has been charged. That figure is still said where the
     question really is money: the budget's own note, and the ledger's Cost
     column against the calls that were actually billed. */
  const callTokens = calls.reduce((a, r) =>
    a + (r.payload.prompt_tokens || 0) + (r.payload.completion_tokens || 0), 0);
  /* Steps not reached yet: still to come while the line runs, not reached once
     it has stopped. The graph draws only what happened. */
  const toCome = settled || runShown(state.log).end !== Infinity ? []
    : phases.filter((p) => p.status === 'pending').map((p) => p.name);

  return `
    ${featureBar(d, { actions: stalled
      ? `<button class="btn btn-primary btn-sm" id="retry-build-3"
          title="Picks the build back up where it stopped. Everything already bought -- the plan, the blind tests, the units that finished -- is reused."
          >Resume the run</button>`
      : liveWord(d) })}

    <section class="stopbar ${stalled ? 'disrupted' : 'normal'}">
      <div class="stopbar-in">
        <span class="stopbar-w">${esc(word)}</span>
        <div><p class="verdict-headline">${sentence}</p></div>
      </div>
      ${foot ? `<div class="stopbar-foot">${foot}</div>` : ''}
    </section>

    <div class="wrap wide">

      ${runLinks(state.log)}
      ${timeline(d, state.log, { waitToggle: true })}

      <section class="section">
        <div class="pk-head">
          <h2 class="pk-h">Call Tree</h2>
          ${calls.length ? `<div class="pk-tally">
            <span class="pk-count"><b>${calls.length}</b> model call${
              calls.length === 1 ? '' : 's'}</span>
            <span class="pk-count"><b>${dur(modelSeconds)}</b> of model time</span>
            ${callTokens ? `<span class="pk-count"
              title="Read and written across every call, which is what a subscription meters."
              ><b>${bytes(callTokens)}</b> tokens</span>` : ''}
            ${/* Only when somebody was actually charged. Every call on a subscription is
                  billed: false, so this counter is absent on a subscription run rather
                  than showing it a $0.00 it would have to explain away. */''}
            ${callCost ? `<span class="pk-count"
              title="Charged by the call, on routes metered per token rather than by subscription."
              ><b>${usd(callCost)}</b> spent</span>` : ''}
          </div>` : ''}
        </div>
        <p class="pk-sub pk-prose">Every agent's turn, in the order it started. Parallel work
          branches off the line and rejoins where it comes together; the oracle keeps its own
          line because it works blind. Every prompt and response, raw, is in
          <a href="${logHref(d.state.project_id, d.state.feature_id)}">the feature's ledger</a>.
          <a href="#" data-help-open="watching-the-build">How the build runs</a></p>
        ${routeTrouble(calls)}
        ${runGraph(d, scoped, calls, toCome.length
          ? `${pendingWord(d) === 'waiting' ? 'Still to come' : 'Not reached'}: ${
              toCome.map(esc).join(' · ')}`
          : '')}
      </section>


      ${/* The same cord the stopped screen carries, for the same reason: a run
           nobody is working on is a page you could otherwise only leave. The
           stage never reached `failed` -- nothing was alive to record that --
           so the screen that offers the way out has to be this one. */''}
      ${stalled ? `<section class="cord">
        <div class="cord-body">
          <h2>Where this goes next</h2>
          <p>Resuming re-runs only what stopped and keeps everything already bought.
            Discarding removes the checkout and this feature's record; the branch is kept.</p>
          <div class="acts">
            <button class="btn btn-stop" id="retry-build-4">Resume the run</button>
            <button class="btn btn-quiet act-discard"
              data-project="${esc(d.state.project_id)}" data-feature="${esc(d.state.feature_id)}"
              >Discard the run</button>
            <a class="btn btn-quiet" href="${logHref(d.state.project_id, d.state.feature_id)}"
              >Read the ledger</a>
          </div>
          <p class="cord-note">Resuming spends from the same budget. The repair reserve is held
            back for fixing a packet that comes out defective, and is not available here.</p>
        </div>
      </section>` : ''}
    </div>`;
}
