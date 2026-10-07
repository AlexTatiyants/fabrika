/* A project being prepared: its steps and sections, the project bar, each
   check's row and fold, and the reference panels for survey and environment. */

'use strict';

/* ------------------------------------------------- 0b. project onboarding */

/* The steps a project goes through, in the words the rest of the console uses.
   Same shape as a feature's, because a project is the same kind of object at a
   different scale: a thing being prepared, with a human gate at the end. */
/* Checks is not in this strip, and that is the point.

   As one of four links of equal weight, sitting between two screens that only
   report what was read, the page a human is actually here to act on would look
   like a tab you might visit, and the informational ones would look like peers
   of it. It is the project's landing page: opening a project opens it, and the
   strip lists what you can go and read *about* it. */
/* Overview first: what the project needs from you, one line per area, and
   the features waiting on you. Checks and Tests answer two questions -- does
   what already works keep working, and can Fabrika verify what it builds --
   so each is a tab of its own; as one long page the testing half would start
   two screens down. `results`, the last run in full, is reached from the
   Checks tab rather than from the strip. */
const PROJECT_STEPS = [
  { id: 'overview', label: 'overview', has: () => true },
  /* Open whenever there is a list: one edited since it was last run has no run
     on record, and the list, and the run it waits for, are what this tab is for. */
  { id: 'checks', label: 'checks', has: (p) => !!p.baseline || !!(p.gates || []).length },
  { id: 'tests', label: 'tests', has: (p) => !!(((p.testing || {}).tiers) || []).length },
  /* Beside checks and tests because they are the third standard a feature is
     held to: how its code is written. Open from the start, so a project with
     none can be told so. */
  { id: 'guides', label: 'guides', has: () => true },
  { id: 'survey', label: 'survey', has: (p) => !!p.survey },
  { id: 'environment', label: 'environment', has: (p) => !!p.environment },
  { id: 'as-built', label: 'as-built', has: () => true },
  { id: 'history', label: 'history', has: () => true },
];

/* The section each step opens, as it is written in the hash. */
const PROJECT_SECTIONS = {
  overview: 'overview', survey: 'survey', environment: 'environment', checks: 'checks',
  tests: 'tests', guides: 'guides', results: 'results', history: 'history', 'as-built': 'as-built',
};
const SECTION_STEP = {
  overview: 'overview', survey: 'survey', environment: 'environment', checks: 'checks',
  tests: 'tests', guides: 'guides', results: 'results', history: 'history', 'as-built': 'as-built',
};

/* A step is a word; a section needs a heading and a sentence. Without them the
   ruling -- "Approved. Features can be started here." -- would be the largest
   line on all four, so every section would open by announcing the same thing
   and none of them would say which section you were reading. */
const SECTION_TITLE = {
  survey: 'What reading the repository found',
  environment: 'Where the checks run',
  checks: 'The checks every feature is judged by',
  results: 'What the run reported',
};
const SECTION_LEDE = {
  survey: `What a model found by reading this repository — what it is, what it is built
    with, and what it thought was worth flagging. Everything below was read, not run.`,
  environment: `Where the checks run. Every feature built here is checked inside this, not on
    your machine, and the setup below is installed before anything is measured.`,
  /* The two questions a build has to answer, said before anything on this page
     tries to answer one. Saying only what the check list is and where it came
     from would be a fact about provenance -- and would leave the reader to work
     out for themselves why half the page is about placements and tiers and a
     disposable database. That half is the second question. */
  checks: `A build has to prove two things, and they are not the same thing: that
    <b>nothing which already worked is broken</b>, and that <b>the code just written is clean,
    tested and correct</b>. Everything on this page serves one of those two. Nothing is added or
    dropped without you.`,
  results: `These checks, run on an untouched checkout of the base branch. What it settles is
    that the commands work at all — a check that cannot run is red for every feature forever
    and reports on nothing. It says nothing about whether the code is good.`,
};
/* The ruling's lamp, and the only coloured thing in the block -- in this world
   a lamp is lit by the line's own state and nothing else is, so the plate does
   not also take a tinted edge for a fact its lamp already carries.

   `circle-alert` and not the rows' `circle-slash`: the row reports that a
   command did not execute, the plate reports that a person is required. Two
   facts, two marks, even though today one always follows the other. */
const RULING_LAMP = {
  ok: 'l-circle-check', act: 'l-circle-alert', wait: 'l-circle',
};

/* Which section `?gates` alone means. The checks are what gate 0 rules on. */
function projectSection() {
  const want = typeof state.view === 'string' && state.view === 'gates'
    ? state.param : null;
  // Every project opens on its overview. One still being set up says there
  // that its checks are waiting to be accepted, and the tile opens them.
  return SECTION_STEP[want] ? want : 'overview';
}

/* Levels Fabrika does not check that still have a fix to choose -- not
   applied, not turned down. The only testing matter that needs you: a level
   you decided about, or one with nothing to choose, is a fact, not a question. */
function undecidedLevels(p) {
  const off = (state.project || {}).unchecked_levels || {};
  const declined = new Set(p.cleanup_declined || []);
  return (((p.testing || {}).tiers) || []).filter((t) => off[t.tier]
    && (t.cleanup_options || []).length && !(p.cleanup_applied || {})[t.tier]
    && !declined.has(t.tier)).map((t) => t.tier);
}

/* A tab that needs you says so, the way the review's tabs do. */
function sectionDot(id, p) {
  const proj = state.project || {};
  if (id === 'tests') {
    if (partsWaiting('tests').length) {
      return '<span class="st-dot amber" title="a proposed change to how this is tested is waiting"></span>';
    }
    return undecidedLevels(p).length
      ? '<span class="st-dot amber" title="a level is waiting on your choice"></span>' : '';
  }
  if (id === 'environment') {
    return partsWaiting('environment').length
      ? '<span class="st-dot amber" title="a proposed change to the environment is waiting"></span>' : '';
  }
  if (id === 'checks') {
    const results = ((p.baseline || {}).results) || [];
    const red = results.filter((r) => !/^setup\[/.test(r.name) && !r.passed && !r.skipped).length;
    if (red) return '<span class="st-dot red" title="a check is failing"></span>';
    return waitingChanges().length
      ? '<span class="st-dot amber" title="a proposed change to a check is waiting"></span>' : '';
  }
  /* A reading's proposals are marked on the tabs they would change. */
  if (id === 'guides') {
    return guideProposals()
      ? '<span class="st-dot amber" title="Fabrika proposes a change to a guide"></span>' : '';
  }
  if (id === 'survey') {
    return (proj.tooling_drift || {}).stale
      ? '<span class="st-dot amber" title="tooling moved since the last reading"></span>' : '';
  }
  return '';
}

/* These ids must exist in PROJECT_STEPS. An id missing from it never makes
   isNow true, and then no project step is marked as where you are. */
const PROJECT_STAGE_STEP = {
  surveying: 'survey', awaiting_approval: 'checks', ready: 'overview', failed: 'survey',
};

function projectBar(p, opts = {}) {
  /* On the start screen no section is open, so nothing in the strip is marked
     as where you are reading. */
  const at = state.view === 'gates' ? projectSection()
    : state.view === 'log' && !state.id ? 'history' : '';
  // The tab you are reading: on the landing page too, where the name is not a
  // link because it would lead where you already are.
  const showing = at || (!state.view ? projectSection() : '');
  const now = PROJECT_STAGE_STEP[p.stage] || 'survey';
  const steps = PROJECT_STEPS.map((step) => {
    const available = step.has(p);
    const isNow = step.id === now;
    const cls = [
      available ? '' : 'ahead',
      isNow && p.stage !== 'awaiting_approval' ? 'here' : '',
      p.stage === 'failed' && isNow ? 'bad' : '',
    ].filter(Boolean).join(' ');
    /* Links, not spans -- a row that looks like navigation should navigate --
       and each to a section of gate 0, shown one at a time, rather than an
       anchor into one long page that re-scrolls on every poll. Following one
       moves the page nowhere. */
    const sec = PROJECT_SECTIONS[step.id];
    return available
      ? `<a class="st ${cls}${showing === sec ? ' at' : ''}"
           href="#/${encodeURIComponent(p.project_id)}?gates=${sec}"
           ${showing === sec ? 'aria-current="page"' : ''}>${esc(step.label)}${sectionDot(step.id, p)}</a>`
      : `<span class="st ${cls}">${esc(step.label)}</span>`;
  }).join('<span class="arr" aria-hidden="true">·</span>');

  /* The strip is a row of screens you can open. Whether gate 0 is approved is
     a status, and the plate below stamps it. */
  const gate = p.stage === 'ready' ? ''
    : `<span class="st gate">repo ready${p.stage === 'awaiting_approval' ? ' · you' : ''}</span>`;

  const results = ((p.baseline || {}).results) || [];
  const byName = {};
  results.forEach((r) => { byName[r.name] = r; });
  const green = (p.gates || []).filter((g) => (byName[g.name] || {}).passed).length;

  return `<header class="board">
    <div class="hall">
      ${/* The name is the way home, since checks is not in the strip. Every
           screen below it is something you went to read; this is the one you
           came to act on, and it opens by default. */''}
      ${boardHead(`<h1 class="ftitle" title="${esc(p.repo || p.name)}">${
        at ? `<a href="#/${encodeURIComponent(p.project_id)}">${esc(p.name || p.project_id)}</a>`
           : esc(p.name || p.project_id)}</h1>`)}

      <p class="proc steps" aria-label="Steps of this project">${steps}</p>

      <div class="line-row">
        ${capabilityPlate(p, byName)}
        ${opts.actions && !browsing() ? `<div class="board-acts">${opts.actions}</div>` : ''}
      </div>
    </div>
  </header>`;
}

/* THE CAPABILITY NAMEPLATE.

   A lamp says something temporal about a run: this station passed its work
   downstream, called for help, stopped the line, was never reached. A gate 0
   check is not that. It is a tool installed on the line, and its green means
   the command ran and reported truthfully against untouched code -- durable
   capability, not live state. Using a lamp for both would teach that green
   means two different things depending on the screen.

   The plate is also invariant. A project's checks are its own: five here, two
   on a small service, a dozen on a monorepo, all named differently. A strip of
   lamps changes shape with every project; the plate is the same object with
   the same fields, and the per-check detail lives in the table below where
   there is room for it. */
function capabilityPlate(p, byName) {
  const gates = p.gates || [];
  const baseline = p.baseline;
  const results = (baseline && baseline.results) || [];
  const ran = gates.filter((g) => byName[g.name]);
  const green = gates.filter((g) => isGreen(byName[g.name])).length;
  const unrunnable = baseline ? unrunnableGates(baseline) : [];
  const red = ran.length - green - unrunnable.length;

  let stamp = 'certified';
  let cls = '';
  if (!baseline || !ran.length) { stamp = 'unproven'; cls = 'unproven'; }
  else if (unrunnable.length) { stamp = 'out of calibration'; cls = 'hold'; }
  else if (red > 0) { stamp = 'not certified'; cls = 'red'; }

  const count = !baseline || !ran.length
    ? `${gates.length} · never run`
    : unrunnable.length
      ? `${gates.length} · ${unrunnable.length} cannot run`
      : red > 0
        ? `${gates.length} · ${red} red`
        : `${gates.length} · all green`;

  /* Both halves of what the project can check, whichever tab is open. */
  const levels = ((p.testing || {}).tiers) || [];
  const off = Object.keys((state.project || {}).unchecked_levels || {}).length;

  /* No "proven on" field: it reads `main` on nearly every project, and a field
     that never differs tells the reader nothing. The branch is still in the
     Run checks button's own title. No "runs in" either: it is the Environment
     tab's and the overview's, and the plate has to leave the buttons room. */
  return `<dl class="nameplate">
    <div class="np-stamp"><span class="np-mark ${cls}">${esc(stamp)}</span></div>
    <div class="np-field"><dt>checks</dt>
      <dd class="${cls && cls !== 'unproven' ? 'bad' : ''}">${esc(count)}</dd></div>
    ${levels.length ? `<div class="np-field"><dt>tests</dt>
      <dd class="${off ? 'amber' : ''}">${3 - off} of 3 levels</dd></div>` : ''}
  </dl>`;
}

/* One row per gate: what it is, what it did, and what it said.

   One list, not two -- the gate definitions, then the same gates again as
   baseline results with different columns. Reading a list twice to assemble
   one fact about each row is the page's whole problem in miniature, and two
   lists cost more vertical space than everything else combined. */
/* When a check runs, and a person's say over it. A fixer has no choice to
   make: it runs first because it rewrites files. */
function runsLine(gate, p) {
  const kind = ((state.project || {}).check_kinds || {})[gate.name] || '';
  const chosen = (p.check_runs || {})[gate.name];
  if (kind === 'fixer') {
    return 'A fixer: it rewrites files, so it runs first every round and what it changes is '
      + 'committed on its own.';
  }
  return `${kind === 'heavy' ? 'Runs once, in the final pass after repairs'
    : 'Runs every round'}${chosen ? ' &mdash; your choice' : ' &mdash; from how long it takes'}.
    <button class="linkish" data-check-runs="${esc(gate.name)}"
      data-runs="${kind === 'heavy' ? 'light' : 'heavy'}">${kind === 'heavy'
        ? 'Run it every round instead' : 'Run it once at the end instead'}</button>
    ${chosen ? `<button class="linkish" data-check-runs="${esc(gate.name)}" data-runs=""
      >Let its timing decide</button>` : ''}`;
}

function gateLine(gate, result, p) {
  const outcome = !result ? 'never' : result.skipped ? 'skipped' : result.passed ? 'passed' : 'failed';
  const cannot = result && gateOutcome(result) === 'could_not_run' && !result.skipped;
  /* One word per row, and "could not run" replaces "failed" rather than being
     appended to it -- a check that never executed did not fail, and saying both
     is the row contradicting itself.

     The mark carries the same three states the whole page turns on: it ran and
     reported (`·` / `✕`), or nothing executed (`–`). */
  const outcomeWord = cannot ? 'could not run'
    : { passed: 'passed', failed: 'failed', never: 'not run',
        skipped: result && !result.passed ? 'failed, allowed' : 'skipped' }[outcome];
  /* Drawn, not typed. Typed, these would be `✕ · ○ –` -- Unicode of four
     different weights standing in for an icon set, which is the one thing a
     glyph should never be asked to do. The mark is decorative: `outcomeWord`
     beside it carries the meaning for anything that is not looking at the
     screen. */
  const mark = icon(cannot ? 'l-circle-slash'
    : { passed: 'l-check', failed: 'l-x', skipped: 'l-circle',
        never: 'l-circle-slash' }[outcome], 'ic ic-sm');
  const tail = (result && result.output_tail) || '';
  const first = tail.split('\n').filter((l) => l.trim())[0] || '';
  /* A result with no gate is a result for a check that is no longer on the
     list. Rendered like a live one -- same tick, same command, "passed · 3s"
     -- superseded checks would read as current passing ones, which is the
     opposite of what the row is reporting. Say what it is,
     and say it first: the word before the outcome is the one that decides how
     the outcome should be read. */
  const gone = !gate;
  /* When it runs. Light is the default and says nothing; the two that behave
     differently say so where the timing is. */
  const kind = gate ? (((state.project || {}).check_kinds || {})[gate.name] || '') : '';
  const meta = [
    gone ? 'no longer a check' : '',
    gate ? familyWord(gate.family) : '',
    kind === 'fixer' ? 'fixer · runs first' : kind === 'heavy' ? 'runs once, at the end' : '',
    outcomeWord,
    result ? dur(result.duration_s) : '',
    result && result.metric != null ? String(result.metric) : '',
    gate && gate.optional ? 'optional' : '',
    result && result.timed_out ? 'timed out' : '',
    /* Its tool fetches part of what it needs only when it runs, so setup
       runs it once with the network before the offline checks. Said, because
       otherwise setup quietly takes longer and nobody knows why. */
    result && result.warmed ? 'setup runs it online once first' : '',
  ].filter(Boolean).join(' · ');

  /* A check that never executed blocks approval, will report nothing on every
     feature forever, and the reader has its output right here: it needs a verb
     to go with it. Three exits, because there are exactly three things wrong it
     can be: the command, where the command runs, or the check itself. */
  /* Every check that is not green gets the verbs, not only one that could not
     run. In a feature build, a check which ran and failed on untouched code
     "needs nothing done to it -- it is red for everyone": the base comparison
     already knows it was red and no feature is blamed for it. At gate 0 it is
     the opposite: this is the row that blocks approval, and the reader is here
     to settle it.

     Two outcomes, whatever the check is: green, or gone. Nothing offers to edit
     code somebody wrote. */
  const wrong = gate && p && result && !isGreen(result);
  const actions = !wrong ? '' : `
    <div class="g-act">
      <p class="small">${cannot
        ? 'It never executed — the command is wrong, or the tool it needs is not in this environment.'
        : 'Get this command to pass, then run the checks again — or take it off the list, which is recorded with your reason.'}</p>
      <div class="acts">
        <button class="btn btn-sm btn-primary" id="run-baseline">Run the checks again</button>
        <a class="btn btn-sm" href="${editHref(p.project_id, 'checks', gate.name)}">Edit the command</a>
        ${cannot ? `<a class="btn btn-sm btn-quiet"
          href="#/${encodeURIComponent(p.project_id)}?gates=environment"
          >Where checks run</a>` : ''}
        <button class="btn btn-sm btn-quiet" data-drop-gate="${esc(gate.name)}"
          >Not for this project</button>
      </div>
      ${cannot ? '' : `<p class="small dim">If the honest answer is a ceiling rather than a fix,
        put it in the command — <span class="mono">--max-warnings</span>,
        <span class="mono">--fail-under</span>, a baseline file. Then the repository and this page agree on
        what passing means.</p>`}
    </div>`;

  /* Consequence before the press, and this one is expensive: the gate list is
     what the baseline was measured against, so removing a check discards every
     result on this screen. */
  const dropping = gate && p && state.droppingGate === gate.name ? `
    <div class="objection g-drop">
      <p class="who">${icon('l-circle-alert', 'ic ic-sm')}Drop ${esc(gate.name)}?</p>
      <p>No feature built here is ever checked by it again. This also clears the run below —
        the results were measured against a list that is about to change — so the checks have
        to be run once more before you can approve.
        ${(p.gates || []).length === 1
          ? ' <b>It is the only check on the list.</b> Dropping it leaves nothing judging a feature.'
          : ''}</p>
      ${/* This page tells people in three places that declining a check
           "leaves the reason readable", and this is where the reason is
           collected -- without it the record says a check was dropped and never
           why, which is the question somebody asks six months later. */''}
      <label class="small dim" for="drop-reason" style="display:block;margin-top:.7rem">
        Why not this project? Kept with the decision, and shown to every later reading.</label>
      <input class="inp" id="drop-reason" type="text" style="width:100%;margin-top:.25rem"
        placeholder="ten pre-existing errors, not fixing them today">
      <div class="d-actions" style="margin-top:.7rem">
        <button class="btn btn-sm btn-quiet" id="cancel-drop-gate">Keep it</button>
        <button class="btn btn-sm btn-primary" id="confirm-drop-gate">Not for this project</button>
      </div>
    </div>` : '';

  const runs = !gate || !p || gone ? '' : `<div class="g-runs small">${runsLine(gate, p)}</div>`;
  const guards = gate && !gone && guardsText(gate)
    ? `<div class="g-runs small">${guardsText(gate)}</div>` : '';
  const body = `${tail ? `<pre class="g-out">${esc(tail)}</pre>` : ''}${runs}${guards}${actions}${dropping}`;
  /* `data-empty` makes the summary un-clickable, and a row carrying controls
     has something to open even when the command printed nothing. And a check
     that blocks approval opens itself: the one row a person is here to settle
     is not worth hiding behind a disclosure. */
  return `<details class="gline ${outcome}${cannot ? ' unrunnable' : ''}${gone ? ' superseded' : ''}"${body ? '' : ' data-empty'}${actions ? ' open' : ''}>
    <summary>
      <span class="g-mark">${mark}</span>
      <span class="g-name">${esc(gate ? gate.name : result.name)}</span>
      <span class="g-cmd mono">${esc((gate && gate.command) || (result && result.command) || '')}</span>
      <span class="g-meta mono">${esc(meta)}</span>
    </summary>
    ${body}
  </details>`;
}

/* The question a check asks. A check recorded before families existed says
   so, rather than having one guessed for it from its name: the next reading
   proposes one, and a person approves it with the check. */
const FAMILY_ASKS = { structure: 'is well-formed', quality: 'is healthy', tests: 'behaves' };
function familyWord(family) { return FAMILY_ASKS[family] ? family : 'family not set'; }

/* On the row a waiting proposal would change: what it would become. */
function proposedFor(g, r) {
  /* Accepted from the proposal and not yet measured: the row is the new
     check, and says what it was, until the checks have run on it. */
  const done = checkRuling(`change:${g.name}`) || checkRuling(`add:${g.name}`);
  if (!done || done.decision !== 'applied' || r) return '';
  /* "Changed from" the command it still runs says nothing; a change that kept
     the command changed how it is measured or what it watches. */
  const same = done.before && done.before.command === g.command;
  const before = done.before && !same ? ` from <code>${esc(done.before.command)}</code>` : '';
  const key = checkRuling(`change:${g.name}`) ? `change:${g.name}` : `add:${g.name}`;
  return `<span class="q-sub q-proposed">${!done.before ? 'Added' : same ? 'Updated' : 'Changed'} just now${before}${
    same ? ', with its command as it was' : ''}.
    Run the checks to measure it. <button type="button" class="linkish"
    data-undo-check="${esc(key)}">Undo</button></span>`;
}

/* A check row is one line: its name, its command, what it read. How it runs,
   what it watches and how it holds new code are one press down, under a
   summary that already says each in a word -- so the list reads as a list,
   and the sentences are there for whoever is deciding about that one check. */
function checkFold(g, body, r) {
  const kind = ((state.project || {}).check_kinds || {})[g.name] || 'light';
  const files = (g.config_files || []).length;
  const marks = (g.suppressions || []).length;
  const held = g.patch_max != null ? `${g.patch_max} new finding${g.patch_max === 1 ? '' : 's'} allowed`
    : g.patch_min != null ? `new lines ${g.patch_min}% covered`
    : g.report_format ? 'can be held on new code' : '';
  const digest = [
    kind === 'fixer' ? 'runs first, as a fixer' : kind === 'heavy' ? 'runs once, at the end' : 'every round',
    files ? 'settings watched' : '',
    marks ? `${marks} marker${marks === 1 ? '' : 's'} watched` : '',
    held,
    (g.also || []).length ? `also ${[...new Set(g.also.map((a) => a.family))].join(', ')}` : '',
    r && r.warmed ? 'fetches once online' : '',
  ].filter(Boolean).join(' · ');
  const key = `check:${g.name}`;
  return `<details class="q-more" data-fold="${esc(key)}" ${openFolds.has(key) ? 'open' : ''}>
    <summary>${esc(digest)}</summary>${body}</details>`;
}

/* Which folds a person opened. The page is drawn again whenever the server
   says anything, and a fold that snapped shut under the reader each time would
   be worse than no fold. */
const openFolds = new Set();
document.addEventListener('toggle', (event) => {
  const fold = event.target && event.target.dataset && event.target.dataset.fold;
  if (!fold) return;
  if (event.target.open) openFolds.add(fold); else openFolds.delete(fold);
}, true);

/* What this check stops a feature from changing quietly. Shown on the row it
   belongs to, because approving the check is approving these too. */
function guardsText(gate) {
  const files = gate.config_files || [];
  const marks = gate.suppressions || [];
  const where = (entry) => {
    const [file, section] = String(entry).split('#');
    return section ? `${esc(file)}, section ${esc(section)}` : esc(file);
  };
  return [
    files.length ? `A feature that changes its settings is reported: <span class="mono">${
      files.map(where).join('</span>; <span class="mono">')}</span>.` : '',
    marks.length ? `A new <span class="mono">${
      marks.map(esc).join('</span>, <span class="mono">')}</span> in a feature's lines is reported.` : '',
  ].filter(Boolean).join(' ');
}

/* Whether a check is held on the lines a feature changes. Offered at zero,
   because that is what almost everyone means: no new finding, whatever the
   repository already had. Only for a check that writes where each finding is. */
const COVERAGE_REPORTS = ['cobertura', 'lcov', 'coverage-json'];

/* What a check's number is. Not always "errors": a coverage run would read
   "61.4 errors" -- a share of lines that ran, described as a count of faults. */
function readingWord(g, m) {
  if (COVERAGE_REPORTS.includes(g.report_format)) return `${m}% of lines ran`;
  if (g.report_format === 'sarif') return `${m} finding${m === 1 ? '' : 's'}`;
  if (g.threshold != null && g.threshold_max == null) return `read ${m}`;
  return `${m} ${m === 1 ? 'error' : 'errors'}`;
}

function heldOnNewCode(g) {
  if (!g.report_format) return '';
  /* A coverage floor is left blank on purpose: unlike "no new findings",
     there is no number almost everyone means, so the person types theirs. */
  if (COVERAGE_REPORTS.includes(g.report_format)) {
    const id = `patch-min-${checkAnchor(g.name)}`;
    return g.patch_min == null
      ? `<span class="q-sub">It can hold the lines a feature changes to a floor of its own,
          whatever the repository's total. <input class="answer-field inp-pct" id="${esc(id)}" type="number"
          min="0" max="100" step="1" aria-label="Coverage floor for new lines, percent"> %
          <button type="button" class="linkish" data-new-code="${esc(g.name)}"
          data-limit-from="${esc(id)}">Require it</button></span>`
      : `<span class="q-sub">Lines a feature changes must be at least
          <b>${esc(String(g.patch_min))}%</b> run by its tests. <button type="button"
          class="linkish" data-new-code="${esc(g.name)}" data-limit="">Stop requiring it</button></span>`;
  }
  const limit = g.patch_max;
  return limit == null
    ? `<span class="q-sub">It can be held on a feature's own lines, whatever the repository
        already has. <button type="button" class="linkish" data-new-code="${esc(g.name)}"
        data-limit="0">Allow no new findings</button></span>`
    : `<span class="q-sub">On a feature's own lines it allows <b>${esc(String(limit))}</b> new
        finding${limit === 1 ? '' : 's'}. <button type="button" class="linkish"
        data-new-code="${esc(g.name)}" data-limit="">Stop holding it there</button></span>`;
}

/* The reference material, one panel at a time.

   Not columns, which keep the page short by keeping every entry short -- a
   rationale behind a disclosure, seven concerns behind a count. A
   panel has the whole width, so it can show the thing itself instead of a
   summary of it, and only one is open at a time so the page stays one screen. */
/* Named for what a reader wants to know, not for the pipeline stage that
   produced the information.

   Not `survey / environment / repository`, which is provenance -- the surveyor
   produces the stack, the image choice and the concerns, so all three would
   land under "survey" regardless of what they are about. Provenance is the
   system's concern. Subject is the reader's. */
/* `kind` is an enum the orchestrator branches on. Nobody outside the code has
   any reason to know the word `generate`. */
const ENV_KIND_WORDS = {
  generate: 'built for this project',
  derive: 'built on top of your own image',
  reuse: 'your existing image, as it is',
  compose: 'your compose stack',
  host: 'this machine — no longer allowed: every check runs in a container',
};

function factRow(term, value, cls = '') {
  if (!value) return '';
  return `<div class="fr"><dt>${esc(term)}</dt><dd class="${cls}">${value}</dd></div>`;
}

/* THE SURVEY AND THE ENVIRONMENT.

   Not three tabs of their own -- the code, where checks run, worth knowing --
   inside a screen whose step strip already names the same material two rows
   above. That would be two navigations for one page, free to disagree: a strip
   link that scrolls to a tab bar without selecting a tab lands you on a panel
   that has nothing to do with the word you clicked. The strip is the one
   navigation. */
/* The Survey and Environment tabs open the way Checks and Tests do: an icon,
   the tab's name, a ? that opens its help, one line of status on the right,
   and one sentence saying what the tab is for. */
function tabHead(sec, p) {
  const proj = state.project || {};
  if (sec === 'survey') {
    const waiting = surveyWaiting() || waitingChanges().length > 0;
    const drift = proj.tooling_drift || {};
    const status = waiting ? 'its proposals wait on the tabs they change'
      : drift.stale ? 'tooling moved since it was read' : 'up to date';
    return `${sectionHead('survey', 'Survey', '', esc(status), [waiting || drift.stale ? 'a' : 'g'])}
      <p class="tab-intro">What Fabrika found by reading this repository: what it is, what it is built
        with, and anything worth flagging. Everything here was read, not run. Re-survey after your
        tooling changes. <a href="#" data-help-open="survey">More about the survey</a></p>`;
  }
  const env = p.environment || {};
  const problems = (proj.environment_problems || []).length;
  const where = { compose: 'Docker Compose', dockerfile: 'a Docker image built for it',
                  image: 'a Docker image' }[env.kind] || 'not decided yet';
  return `${sectionHead('environment', 'Environment', '',
                        esc(problems ? `${problems} problem${problems === 1 ? '' : 's'}` : where),
                        [problems ? 'r' : 'g'])}
    <p class="tab-intro">Where every check and every agent runs: a container built for this project,
      sealed off from this machine and from other features. What is set up below is installed before
      anything is measured. <a href="#" data-help-open="environment">More about the environment</a></p>`;
}

function projectPanels(p, env, survey, which) {
  const drift = state.project && state.project.tooling_drift;
  const paths = (state.project && state.project.paths) || {};
  const dirty = state.project && state.project.drift && state.project.drift.dirty;
  const live = (state.project && state.project.live_features) || [];
  const concerns = (survey && survey.concerns) || [];
  const fold = (summary, body) => `<details class="fold-fact"><summary>${summary}</summary>${body}</details>`;

  const panels = {
    survey: `
      <dl class="facts-list">
        ${factRow('name', `<b>${esc(p.name || p.project_id)}</b>`)}
        ${factRow('repository', `<span class="mono">${esc(p.repo)}</span>`)}
        ${factRow('features branch from', `<b class="mono">${esc(p.base_ref)}</b>`)}
        ${dirty ? factRow('uncommitted changes', `<span class="warn">yes — a feature branches from the
          last commit, so these would not be in it</span>`) : ''}
        ${factRow('what it is', survey && survey.summary ? `<p>${esc(survey.summary)}</p>` : '')}
        ${factRow('built with', (survey && survey.stack || []).length
          ? `<div class="chipset">${survey.stack.map((t) => `<span>${esc(t)}</span>`).join('')}</div>` : '')}
        ${factRow('flagged', concerns.length
          ? fold(`${concerns.length} thing${concerns.length === 1 ? '' : 's'} worth knowing about this repository`,
            `<ul class="disclosure-list">${concerns.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>`)
          : 'nothing')}
        ${factRow('still current?', driftFact(drift, p))}
        ${factRow('advanced', fold('Digest budget, and where Fabrika keeps its files', `
          <p class="small">Builders read up to <b>${esc(Number(p.digest_budget || 120000).toLocaleString())}</b>
            characters of the repository.</p>
          ${paths.sandboxes ? `<p class="small dim">Features are built in <span class="mono">${
            esc(paths.sandboxes)}</span>${paths.evidence ? `; runs are recorded in <span class="mono">${
            esc(paths.evidence)}</span>` : ''}. Your own working copy is never written to.</p>` : ''}`))}
      </dl>
      ${/* What the project depends on is part of what the reading found, not a
           check: read from the lockfiles, judged by nothing a feature is held
           to. Above the foot, which stays the last thing on the page. */''}
      <div class="deps-survey">${dependencyInventory(p)}</div>
      ${removeProject(live)}`,

    environment: partsBlock(p, 'environment') + (env ? `
      <dl class="facts-list">
        ${factRow('runs in', `<b>${esc(ENV_KIND_WORDS[env.kind] || env.kind)}</b>${
          env.kind === 'compose' && env.compose_file
            ? ` <span class="dim">· ${esc(env.compose_file)}${env.compose_service
              ? `, service ${esc(env.compose_service)}` : ''}</span>`
            : env.source && env.source.toLowerCase() !== 'generated'
              ? ` <span class="dim">· from ${esc(env.source)}</span>` : ''}`)}
        ${factRow('installed first', (env.setup || []).length
          ? `<ol class="cmd-list">${env.setup.map((c) => `<li class="mono">${esc(c)}</li>`).join('')}</ol>` : '')}
        ${factRow('before each check session', (env.test_prepare || []).length
          ? `<ol class="cmd-list">${env.test_prepare.map((c) => `<li class="mono">${esc(c)}</li>`).join('')}</ol>` : '')}
        ${factRow('services', (env.services || []).length
          ? `<ul class="cmd-list bare">${env.services.map((v) => `<li><b>${esc(v.name)}</b>${v.ready_when
            ? ` <span class="dim">· ready when <span class="mono">${esc(v.ready_when)}</span></span>` : ''}</li>`).join('')}</ul>` : '')}
        ${factRow('a person opens', previewFact(env))}
        ${factRow('versions', versionsFact(p, env))}
        ${factRow('why this setup', env.rationale ? fold(esc(firstClause(env.rationale)),
          `<p class="small">${esc(env.rationale)}</p>`) : '')}
        ${factRow('dockerfile', env.dockerfile ? dockerfileFact(env) : '')}
      </dl>`
      : '<p class="empty">Nowhere to run the checks yet.</p>'),
  };

  return `<section class="psection">${panels[which] || ''}</section>`;
}

/* Which toolchain versions the checks ran with, beside the files that say what
   the project's own runs use. A fact, not a warning: when a check is red here
   and green on a developer's machine, this is where the difference shows. */
function versionsFact(p, env) {
  const seen = Object.entries(p.toolchain_versions || {});
  const files = env.version_files || [];
  if (!seen.length && !files.length) {
    return '<span class="dim">not said yet &mdash; survey again to have them read.</span>';
  }
  return `${seen.length ? `<ul class="cmd-list bare">${seen.map(([cmd, v]) =>
      `<li><b>${esc(v)}</b> <span class="dim mono">${esc(cmd)}</span></li>`).join('')}</ul>`
    : '<span class="dim">measured at the next run of the checks</span>'}
    ${files.length ? `<p class="small dim">The project's own runs: <span class="mono">${
      files.map(esc).join(' · ')}</span></p>` : ''}`;
}

/* What a person opens at review, and whether it opened when last measured. */
function previewFact(env) {
  const pv = env.preview;
  const probe = (state.project && state.project.project || {}).preview_probe;
  const help = ' <a href="#" data-help-open="running-the-app">How it works</a>';
  if (!pv) {
    return `<span class="dim">not said yet — survey again, or set it under Edit.</span>${help}`;
  }
  if (!pv.open) return `<span class="dim">nothing: ${esc(pv.note || 'there is no app to open')}</span>`;
  const also = (pv.services || []).length
    ? ` <span class="dim">· also starts ${pv.services.map((v) => `<b>${esc(v.name)}</b>`).join(', ')}</span>` : '';
  const measured = !probe ? '<span class="dim">Not measured yet: the next check run opens it once.</span>'
    : probe.ok ? `<span class="pv-ok">Opened on ${esc((probe.sha || '').slice(0, 7) || 'the main branch')}
        in ${Math.round(probe.seconds || 0)}s.</span>`
      : `<span class="warn">Didn't open when last measured: ${esc(probe.problem)}</span>`;
  return `<b>${esc(pv.open)}</b> <span class="mono dim">${esc(pv.path || '/')}</span>${also}
    <p class="small">${measured}${pv.note ? ` ${esc(pv.note)}` : ''}${help}</p>`;
}

/* Where the Dockerfile lives, and whether the checks have seen this one.

   In the repository, at `.fabrika/Dockerfile`, read from the branch features
   start from each time Fabrika builds -- edited, reviewed and versioned like
   any file. Until it is moved there it is the survey's proposal, kept on
   Fabrika's record, and this is where it is moved in: one press, which writes
   the file and commits it, as accepting scaffolding does. Never edited here in
   either case: the repository is where a Dockerfile is edited. */
function dockerfileFact(env) {
  const d = (state.project && state.project.dockerfile) || {};
  const path = d.path || '.fabrika/Dockerfile';
  const ref = d.ref || 'main';
  const fromRepo = d.source === 'repo' || d.source === 'project';
  const stage = d.target ? `, stage <span class="mono">${esc(d.target)}</span>` : '';
  const notes = [
    d.unmeasured ? `<p class="small warn">Changed since the checks last ran, so their results are about
      the one before. <b>Run checks</b> to measure this one.</p>` : '',
    d.uncommitted ? `<p class="small warn">Your working copy's <span class="mono">${esc(path)}</span> differs
      from what is committed on <b>${esc(ref)}</b>. Fabrika builds from the committed one; commit it to use
      yours.</p>` : '',
  ].join('');
  const summary = fromRepo
    ? `<span class="mono">${esc(path)}</span>${stage} on ${esc(ref)} (show)`
    : 'Fabrika\'s own copy (show)';
  const body = d.source === 'project'
    ? `<p class="small dim">Your project's own Dockerfile${d.target ? ', built to that stage' : ''}. Edit it
        there and commit to ${esc(ref)}; Fabrika builds from ${esc(ref)} each time.</p>`
    : fromRepo
    ? `<p class="small dim">In your repository. Edit it there and commit to ${esc(ref)}; Fabrika reads it from
        ${esc(ref)} each time it builds. Agents can't change it.</p>`
    : `<p class="small dim">The survey's proposal, kept by Fabrika rather than in your repository, so it can
        only change by surveying again.</p>`;
  const onAccept = movesDockerfile(state.project);
  const retire = d.fabrika_unused ? `<div class="df-move">
      <button type="button" class="btn btn-sm" data-retire-dockerfile="1">Remove .fabrika/Dockerfile</button>
      <span class="small dim">No longer used: Fabrika builds from your own Dockerfile now. Removes the file
        and commits that.</span></div>` : '';
  const move = fromRepo ? retire : d.own ? '' : `<div class="df-move">
      <button type="button" class="btn btn-sm" data-move-dockerfile="1">Move it into your repository</button>
      <span class="small dim">${onAccept ? 'Accepting this project\'s checks does this for you, or do it now. '
        : ''}Writes <span class="mono">${esc(path)}</span> and commits it on
        ${esc(d.branch || 'the current branch')}${d.branch && d.branch !== ref
          ? `, not ${esc(ref)} — merge it there for Fabrika to use it` : ''}. After that you edit it like
        any file.</span></div>`;
  return `${notes}<details class="fold-fact"><summary>${summary}</summary>${body}
      <pre class="hz-pre">${esc(env.dockerfile)}</pre></details>${move}`;
}

/* Whether "Accept checks" will move the Dockerfile in -- a new project,
   on Fabrika's copy, with nothing at that path yet. Said before the press. */
function movesDockerfile(proj) {
  const d = (proj || {}).dockerfile;
  return !!d && d.source === 'fabrika' && !d.own && !d.on_disk
    && ((proj || {}).project || {}).stage === 'awaiting_approval';
}

function retireDockerfile() {
  return withBusy('Removing .fabrika/Dockerfile.', async () => {
    const out = await api(`${projectUrl(state.projectId)}/dockerfile/retire`, { method: 'POST' });
    await refreshProject();
    toast(out.commit_problem ? `Removed ${out.path}: ${out.commit_problem}`
      : `Removed ${out.path} and committed it (${String(out.commit).slice(0, 7)}).`);
  });
}

function moveDockerfile() {
  return withBusy('Moving the Dockerfile into your repository.', async () => {
    const out = await api(`${projectUrl(state.projectId)}/dockerfile/move`, { method: 'POST' });
    await refreshProject();
    toast(out.commit_problem ? `Written to ${out.path}: ${out.commit_problem}`
      : `Committed ${out.path} (${String(out.commit).slice(0, 7)}).`);
  });
}

/* The line a folded reading shows before it is opened: its own first clause,
   whole, never a sentence cut off mid-word. */
function firstClause(text) {
  const t = String(text || '').trim();
  const m = t.match(/^(.+?)(?:\s--\s|\s—\s|;\s|\.\s|\.$)/);
  return (m ? m[1] : t).replace(/[,:]$/, '');
}

/* Apart from everything else, at the foot of the Survey tab, in red. */
function removeProject(live) {
  if (state.discarding) {
    return `<div class="danger"><p><b>Remove this project from Fabrika?</b> Your repository is
        untouched; Fabrika forgets the project and its runs.</p>
      <label class="field-inline"><input type="checkbox" id="discard-branches">
        <span>also delete the feature branches it made — this loses their code</span></label>
      <div class="danger-acts"><button class="btn btn-sm btn-quiet" id="cancel-discard">Cancel</button>
        <button class="btn btn-sm btn-danger" id="confirm-discard-project">Remove project</button></div></div>`;
  }
  return `<div class="danger"><p><b>Remove this project from Fabrika.</b> Your repository is untouched;
      Fabrika forgets the project and its runs.${live.length
        ? ' Not possible while features are in flight.' : ''}</p>
    <button class="btn btn-sm" id="start-discard" ${live.length ? 'disabled' : ''}>Remove project</button></div>`;
}

/* Whether the check list still describes this repository, in the place someone
   would look for it rather than as a banner further down. */
function driftFact(drift, p) {
  if (!drift) return '';
  if (!drift.checked) {
    return `<span class="dim">unknown — this survey did not record which commit it read.
      Survey again to start tracking.</span>`;
  }
  if (!drift.stale) {
    return `yes. Nothing that decides what to check has changed in the
      ${esc(drift.commits)} commit(s) since.`;
  }
  const bits = [
    drift.added.length ? `${drift.added.length} appeared` : '',
    drift.changed.length ? `${drift.changed.length} changed` : '',
    drift.unseen.length ? `${drift.unseen.length} never read` : '',
  ].filter(Boolean).join(', ');
  const names = [...drift.added, ...drift.changed, ...drift.unseen].slice(0, 10);
  return `<span class="warn">maybe not.</span> ${esc(bits)} — files that decide what gets
    checked have moved in the ${esc(drift.commits)} commit(s) since this reading.
    <p class="mono dim small">${names.map(esc).join(' · ')}</p>
    <p class="dim small">A tool added here gains a surface no check covers, and the existing
      checks stay green throughout because none of them was ever asked about it.
      <b>What should change?</b> proposes a diff for you to accept or reject.</p>`;
}

/* The one thing no screen could say: that the code answering you is not the
   code on disk.

   Each way it happens costs real money. A console started before a field
   existed round-trips projects through its own older shape and silently deletes
   the newer fields from the stored record. A wedged reloader leaves a process
   holding the port and answering nothing. And a fix to what counts as test
   setup can sit on disk for hours while a human pays for a re-survey whose
   answer is byte-identical to the one before it, with nothing anywhere saying
   why.

   Quiet, and only when true. An indicator that is on when nothing is wrong
   stops being read, which is why role prompts and the console's own files are
   excluded server-side -- both are loaded per call, so editing them is live. */
/* Painted above the masthead, on every screen, rather than inside one page's
   body. Rendered by `projectScreen` alone, it would tell a reader on the yard,
   on a feature, on the ledger or in settings nothing, and the actions that
   spend money are reachable from all of them. A warning that appears only where
   somebody happens to be standing is a warning about the wrong thing. */
function paintStaleBar() {
  paintOfflineBar();
  const el = document.getElementById('stale-bar');
  if (!el) return;
  const html = staleServerStrip();
  el.innerHTML = html;
  el.hidden = !html;
}

function staleServerStrip() {
  const v = state.version;
  if (!v || !v.stale) return '';
  const mins = Math.max(1, Math.round(v.stale_by_s / 60));
  return `
    <div class="stale-strip">
      <span class="ss-lamp" aria-hidden="true">${icon('l-circle-alert', 'ic')}</span>
      <div>
        <b>This server is running older code than what is on disk.</b>
        <span class="mono">${esc(v.newest_source || 'a module')}</span> was edited
        ${mins === 1 ? 'about a minute' : `about ${mins} minutes`} after it started, and Python is
        imported once — so anything you run now uses the older version. Restart it before
        surveying, running checks or building: those cost money and would be answered by code you
        have already changed.
      </div>
    </div>`;
}

/* `like this` -> <code>like this</code>, escaped before anything is marked up
   so a field name can never carry markup of its own. */
function ticks(text) {
  return esc(text).replace(/`([^`]+)`/g, '<code class="mono">$1</code>');
}

/* The other direction of the same idea as `staleServerStrip`.

   That one says the code answering you is older than the code on disk. This
   says the *record* is older than the code -- a field was added here, every
   existing project's reading predates it, and nothing else notices: the drift
   detector watches the repository, and the repository did not move.

   Without this the first sign is a re-survey proposing a change to a project
   that has not changed, which reads as drift, is not, and costs a model call to
   discover. Said before the button that spends money rather than after. */
function readingBehindStrip() {
  const behind = (state.project && state.project.reading_behind) || [];
  if (!behind.length) return '';
  /* The stamp moves when the project is next saved, and ruling on a waiting
     proposal is that save, whichever way it goes. Telling someone to re-survey
     then would send them to pay for the same proposal again, over a reading
     that has already answered the question and is waiting on a ruling. */
  const waiting = surveyWaiting();
  const survey = `#/${encodeURIComponent(state.projectId)}?gates=survey`;
  return `
    <div class="stale-strip">
      <span class="ss-lamp" aria-hidden="true">${icon('l-circle-alert', 'ic')}</span>
      <div>
        <b>This reading predates ${behind.length === 1 ? 'a question' : `${behind.length} questions`}
        this tool now asks.</b>
        Nothing about your repository has changed — these fields were added here, so they are
        empty because nobody was ever asked, not because the answer is none:
        <ul class="sb-list">${behind.map((b) => `<li>${ticks(b)}</li>`).join('')}</ul>
        ${waiting
          ? `The re-survey <a href="${survey}">waiting on the Survey tab</a> was asked
            ${behind.length === 1 ? 'this' : 'these'}. Rule on its proposal, either way, and this
            goes away; there is no need to re-survey again.`
          : 'Re-survey to fill them. Until you do, a blind test author works without them.'}
      </div>
    </div>`;
}
