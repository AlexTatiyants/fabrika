/* The steps of a feature, each addressable: which step a stage lands on, the
   feature bar, the station strip, and the words for each stage. */

'use strict';

/* ------------------------------------------------------------------ steps

   Every step of the process is addressable, always. The stage decides where you
   land; it does not decide what you may look at. Nothing here is reachable
   before its artifact exists, so a link is never a promise the ledger cannot
   keep.
*/

const BUILD_PHASES = ['architect', 'plan_checker', 'workers', 'integrator', 'oracle', 'gates', 'qa',
                      'attribution', 'breaker', 'review', 'arbiter',
                      'repairers', 'simplifier', 'rapporteur'];

/* Phases that run only when there is something for them to do: no findings
   means no arbiter, nothing routed for repair means no repair round. On a run
   that has settled, a pending one of these is not unfinished work. Saying so is
   the difference between "11 of 15 phases" on a finished build and the truth. */
const CONDITIONAL_PHASES = ['attribution', 'breaker', 'arbiter', 'repairers',
                            'simplifier'];

/* The sixteen stations read as one undifferentiated row unless something says
   which four things they are: reading and deciding what to build, writing it,
   checking it stands up, and putting a human's eyes on it. */
const PHASE_GROUPS = [
  { key: 'spec', label: 'spec', names: ['scout', 'interrogator', 'spec_writer', 'spec_checker'] },
  { key: 'build', label: 'build', names: ['architect', 'plan_checker', 'workers', 'integrator'] },
  { key: 'validate', label: 'validate', names: ['oracle', 'gates', 'attribution', 'qa', 'breaker'] },
  { key: 'review', label: 'review',
    names: ['review', 'arbiter', 'repairers', 'simplifier', 'rapporteur'] },
];

const STEPS = [
  { id: 'intent', label: 'intent', has: () => true },
  { id: 'scout', label: 'scout', has: (d) => !!(d.scout || d.digest) },
  { id: 'questions', label: 'questions', has: (d) => !!d.interrogation },
  { id: 'spec', label: 'spec', has: (d) => !!d.spec },
  // Every phase is listed from the start, most of them pending -- so the test is
  // whether one of them actually ran, not whether it is on the list.
  { id: 'build', label: 'build',
    has: (d) => !!d.plan || (d.phases || []).some(
      (p) => BUILD_PHASES.includes(p.name) && p.status !== 'pending') },
  { id: 'review', label: 'review', has: (d) => !!d.packet },
];
const STEP_IDS = STEPS.map((x) => x.id);

/* Which step the stage is sitting on (`STEP_OF_STAGE`, app/vocab.js). Distinct
   from which one you are reading. */
const currentStep = (d) => STEP_OF_STAGE[d.state.stage] || 'intent';

const stepHref = (p, f, id) => `${featureHref(p, f)}?${id}`;

/* True when you are reading a step other than the one the feature is on. Then
   nothing on screen may change anything: you are looking at the past. */
const browsing = () => !!state.view && STEP_IDS.includes(state.view)
  && state.data && state.view !== currentStep(state.data);

function featureBar(d, opts = {}) {
  const here = bandOf(d);
  const stage = d.state.stage;
  const at = state.view && STEP_IDS.includes(state.view) ? state.view : currentStep(d);
  const gateStep = GATE_STEP[stage];
  const owed = (GATE_OF[stage] || '').split('·').pop().trim();
  const project = d.state.project_id, feature = d.state.feature_id;

  const steps = STEPS.map((step) => {
    const available = step.has(d);
    const isAt = step.id === at;
    const isNow = step.id === currentStep(d);
    const gate = isNow && step.id === gateStep;
    const cls = [
      available ? '' : 'ahead',
      isAt ? 'at' : '',
      isNow && !gate ? 'here' : '',
      gate ? 'gate' : '',
      here.failed && isNow ? 'bad' : '',
    ].filter(Boolean).join(' ');
    const label = `${esc(step.label)}${gate && owed ? ` · ${esc(owed)}` : ''}${
      isNow && here.running && here.phase ? '…' : ''}`;
    return available
      ? `<a class="st ${cls}" href="${stepHref(project, feature, step.id)}">${label}</a>`
      : `<span class="st ${cls}">${label}</span>`;
  }).join('<span class="arr" aria-hidden="true">·</span>');
  /* Every run of this feature -- the build, each verify again, each repair
     round -- as a timeline and a call tree. It is the build step's screen, but
     `build` reads as one step of five and hides that the review and its rounds
     are there too, so it gets a name of its own, set apart from the steps. */
  const runs = STEPS.find((x) => x.id === 'build').has(d)
    ? `<span class="runs-rule" aria-hidden="true"></span><a class="st" href="${stepHref(project, feature, 'build')}"
        title="Every run of this feature: what each agent did, in order, with its calls">runs</a>`
    : '';

  // "sha256:b926159..." spends the whole line on the word sha256.
  const hash = opts.hash !== undefined ? opts.hash
    : (d.state.spec_hash || '').replace(/^sha256:/, '').slice(0, 12);
  const title = opts.title || (state.data && d.state.title) || '';

  const Tag = opts.heading === false ? 'p' : 'h1';

  /* A sub-line has to earn the width of the title. The branch name is the
     feature id with a prefix -- it is in the ledger, in settings and in the
     hash already -- so what is left is the one thing that is not derivable:
     which pass of rework this is. */
  const packetRounds = (d.packet && d.packet.rework && d.packet.rework.rounds) || 0;
  const pass = livePass(d);
  const sub = pass
    ? `<span class="fpass${pass.round ? ' rework' : ''}" title="${esc(pass.why)}">${esc(pass.label)}</span>`
    : packetRounds ? `<span class="fsub">${packetRounds} repair round${packetRounds === 1 ? '' : 's'}</span>` : '';

  return `<header class="board">
    <div class="hall">
      ${boardHead(`<${Tag} class="ftitle" title="${esc(title)}">${esc(title)}</${Tag}>${
        sub}`)}

      ${/* The steps and the actions are one line, and the lamps are the line
           under them -- folded or unfolded. Not the other way round, with the
           buttons beside the strip: unfolded that row is 130px tall and the
           buttons sit in it, but folded it is a row of dots and the bar would
           be mostly empty space to the left of them. What the
           buttons act on is the feature, not the strip, so they belong on the
           row that says which step of the feature you are looking at. */''}
      <div class="proc-row">
        <p class="proc steps" aria-label="Steps of this feature">${steps}${runs}</p>
        ${!browsing() ? `<div class="board-acts">${opts.actions || ''}
        <button class="btn btn-quiet btn-sm act-discard"
          data-project="${esc(project)}" data-feature="${esc(feature)}"
          ${['intake', 'building'].includes(stage) ? 'disabled' : ''}
          title="${['intake', 'building'].includes(stage)
            ? 'Agents are still writing to this feature. It can be discarded once the run finishes or fails.'
            : `Removes this feature's ledger -- every prompt, every response, the packet -- and its checkout. The branch is kept; the log screen is where you can take that too.`}"
          >Discard</button></div>` : ''}
      </div>

      ${(d.phases || []).length ? `<div class="line-row ${stripOpen() ? '' : 'folded'}">
        ${stationStrip(d)}
        ${stripToggle(stripOpen())}
      </div>` : ''}
    </div>
  </header>`;
}

/* PLAN, ACTUAL, DEFECT -- three boxed numerals that do not ride beside the
   feature name.

   Every one of them is already said, in full, by the screen underneath. Plan,
   actual and defect are the four cards across the top of the work map, where
   each number is a word as well as a figure and each one is a filter you can
   press; the header can only ever show the figure. `spent` is in the footer
   of the build screen and of the stop screen, next to what is still spendable
   -- which is the half of it that tells you anything.

   What they would cost is the height of the masthead. A 27px numeral in a
   bordered box is 54px tall, so the row carrying a 26px name would be sized by
   the thing repeating the screen below it. Nothing reads it twice. */

/* THE LINE, FOLDED.

   Sixteen stacked lamps are the signature of this console and they cost about
   130px of every feature screen. Folded, the same sixteen lamps are a row of
   dots with the count and the station beside them, and the actions come up
   onto the line with them -- one 44px row instead of two. Which one you get is
   a preference, kept per browser, and it survives every render because it is
   not a property of the run. */
const STRIP_KEY = 'factory.strip';
function stripOpen() {
  if (state.stripOpen === null) {
    try { state.stripOpen = localStorage.getItem(STRIP_KEY) !== 'off'; }
    catch (e) { state.stripOpen = true; }
  }
  return state.stripOpen;
}
function setStripOpen(open) {
  state.stripOpen = open;
  try { localStorage.setItem(STRIP_KEY, open ? 'on' : 'off'); } catch (e) { /* private window */ }
}

function stripToggle(open) {
  const word = open ? 'Fold the line' : 'Unfold the line';
  return `<button type="button" class="strip-toggle" id="strip-toggle"
    aria-expanded="${open}" aria-controls="station-strip" title="${esc(word)} -- \\">
    <span class="st-chev" aria-hidden="true">${open ? '\u2303' : '\u2304'}</span>
    <span class="sr-only">${esc(word)}</span>
  </button>`;
}

/* WHICH PASS THE LINE IS ON.

   The same sixteen lamps are lit on the first pass and on every repair round,
   and the round count only reaches the packet once the run is over -- so a
   build deep in its second repair round would read exactly like one that has
   just reached the checks. `rework_round` is the pipeline saying so while it runs;
   a run that predates that field still names its round in the phase details
   ("re-check · round 2"), and the larger of the two is the one that is true. */
function livePass(d) {
  if (!d || !d.state || d.state.stage !== 'building') return null;
  const phases = d.phases || [];
  const said = phases
    .filter((p) => p.status !== 'pending')
    .map((p) => Number((/\bround (\d+)/.exec(p.detail || '') || [])[1] || 0));
  const round = Math.max(Number(d.state.rework_round) || 0, ...said, 0);
  const status = (n) => (phases.find((p) => p.name === n) || {}).status;
  const closing = !!d.state.rework_closing
    || ['running', 'done'].includes(status('simplifier'))
    || status('rapporteur') === 'running';
  const max = Number((d.budget || {}).max_rounds) || 0;
  const of = max ? ` of ${max}` : '';
  let label; let why;
  if (closing) {
    label = round ? `Closing · after ${round} repair round${round === 1 ? '' : 's'}` : 'Closing · first pass';
    why = 'The repair loop is done. What is left is the simplifier and the last full review.';
  } else if (round) {
    label = `Repair round ${round}${of}`;
    why = `Pass ${round + 1}. The code has been repaired ${round} time${round === 1 ? '' : 's'}; `
      + 'the stations lit since the round began are re-checking that repair.';
  } else {
    label = 'First pass';
    why = 'Nothing has been repaired yet. The line is building and checking for the first time.';
  }
  /* When the current round began, so the lamps can say which of them ran in it
     and which are still showing a result from an earlier pass. */
  const rem = phases.find((p) => p.name === 'repairers');
  const since = round && rem && rem.started_at ? rem.started_at : '';
  return { round, closing, max, label, why, since };
}

/* The board. One lamp per station, lit only by the line's own state -- and a
   station that was reached and had nothing to do is not the same as one the
   line never got to, which is why there are five states here and not four. */
/* Whether the line is moving right now, so a station it has not got to yet is
   next rather than missed. "Not reached" on a running line reads as "skipped":
   a person watching the breaker run beneath two stations marked that way asks
   why they were skipped, when they are simply next. A line that stopped
   -- settled, waiting on a person, or orphaned by a server that died under it
   -- has stations it genuinely did not reach, and those keep the word. */
const lineRunning = (d) =>
  ['intake', 'writing_spec', 'building'].includes((d.state || {}).stage) && !d.orphaned;
const pendingWord = (d) => (lineRunning(d) ? 'waiting' : 'not reached');

function stationStrip(d) {
  const phases = d.phases || [];
  if (!phases.length) return '';
  const settled = SETTLED_STAGES.includes(d.state.stage);
  const lampOf = (p) => {
    if (p.status === 'done') return 'lit green';
    if (p.status === 'failed') return 'lit red';
    if (p.status === 'running') return 'lit white blink';
    if (settled && CONDITIONAL_PHASES.includes(p.name)) return 'skipped';
    return '';
  };
  const wordOf = (p) => {
    if (p.status === 'done') return 'passed downstream';
    if (p.status === 'failed') return 'stopped the line';
    if (p.status === 'running') return 'working now';
    if (settled && CONDITIONAL_PHASES.includes(p.name)) return 'nothing for it to do';
    return pendingWord(d);
  };
  /* Sixteen identical lamps in a row say nothing about what kind of work each
     one is. A phase this config doesn't know about still gets a lamp -- it
     just carries no group and starts nothing. The lookup and the boundary
     tracking stay flat, one pass over the same row, so the tuned flex-shrink
     below (see "SIXTEEN STATIONS, NO SCROLLBAR") never has to nest. */
  const groupOf = (name) => (PHASE_GROUPS.find((g) => g.names.includes(name)) || {}).label || '';
  let lastGroup = null;
  const pass = livePass(d);
  /* Green from an earlier pass is not green for this one. Dimmed, so what has
     actually run since the round began is what stands out. */
  const prior = (p) => !!(pass && pass.since && p.status === 'done'
    && p.started_at && p.started_at < pass.since);
  const cells = phases.map((p, i) => {
    const lamp = lampOf(p) + (prior(p) ? ' prior' : '');
    const detail = [wordOf(p), prior(p) ? 'from an earlier pass' : '',
      p.detail || '', p.error || ''].filter(Boolean).join(' · ');
    const grp = groupOf(p.name);
    const starts = grp && grp !== lastGroup;
    if (grp) lastGroup = grp;
    // The first lamp needs no divider from nothing -- it still gets the
    // caption, just not the border and gap that mark every later boundary.
    const cls = starts ? ` grp-start${i > 0 ? ' grp-div' : ''}` : '';
    return `<div class="stn ${lamp ? 'on' : 'off'}${cls}"
      ${starts ? `data-grp="${esc(grp)}"` : ''}
      title="${esc(stepWord(p.name))} -- ${esc(detail)}">
      <span class="lamp ${lamp}"></span>
      <span class="stn-no">${String(i + 1).padStart(2, '0')}</span>
      <span class="stn-nm">${esc(stepWord(p.name))}</span>
    </div>`;
  }).join('');

  /* A line that only runs forwards is not a factory. The rail is part of the
     layout whether or not anything is on it; it says so by how it is drawn. */
  const packetRework = (d.packet && d.packet.rework) || null;
  const rounds = (packetRework && packetRework.rounds) || 0;
  /* The round count only lands once the packet is written, at the very end --
     but the repair-loop stations light up long before that. A run mid-repair
     shouldn't sit under a rail that still claims to be unused. */
  const repairPhases = phases.filter((p) => p.name === 'repairers');
  const repairRunning = repairPhases.some((p) => p.status === 'running');
  const repairDone = repairPhases.some((p) => p.status === 'done');
  const used = rounds > 0 || repairRunning || repairDone;
  const railCls = used ? '' : ' idle';
  /* Rework begins after review, so a line that never got there did not decline
     to use the rail -- it never reached it. Two different facts, and the strip
     must not report the second as the first. */
  const reachedReview = (d.phases || [])
    .some((p) => p.name === 'review' && p.status !== 'pending');
  const railWord = pass && pass.round
    ? `rework · round ${pass.round}${pass.max ? ` of ${pass.max}` : ''}${pass.closing ? ' · closing' : ' · in progress'}`
    : rounds
    ? `rework · ${rounds} pass${rounds === 1 ? '' : 'es'}${
        (d.budget || {}).exhausted ? ' · budget spent' : ''}`
    : repairRunning ? 'rework · in progress'
    : repairDone ? 'rework · used'
    : `rework · ${reachedReview ? 'not used' : 'not reached'}`;
  if (!stripOpen()) {
    const done = phases.filter((p) => p.status === 'done').length;
    const at = phases.find((p) => p.status === 'failed')
      || phases.find((p) => p.status === 'running');
    const no = at ? String(phases.indexOf(at) + 1).padStart(2, '0') : '';
    const word = at
      ? `${at.status === 'failed' ? 'stopped at' : 'now at'} ${no} ${at.name}`
      : (done === phases.length
        ? `all ${phases.length} stations`
        : `${done} of ${phases.length} stations`);
    const count = at ? `${done} of ${phases.length}` : '';
    return `<div class="stations folded" id="station-strip">
      <span class="mini-lamps" aria-hidden="true">${phases.map((p) =>
        `<i class="mlamp ${lampOf(p)}${prior(p) ? " prior" : ""}"></i>`).join('')}</span>
      <span class="mini-word ${at && at.status === 'failed' ? 'bad' : ''}">${
        count ? `${esc(count)} · ` : ''}${esc(word)}</span>
      ${used ? `<span class="mini-rail">${esc(railWord)}</span>` : ''}
    </div>`;
  }

  return `<div class="stations" id="station-strip"><div class="strip-inner">${cells}
    <div class="return-rail${railCls}"><span>${esc(railWord)}</span></div>
  </div></div>`;
}

