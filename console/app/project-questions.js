/* The project's two questions -- what blocks approval, and whether anything
   that worked is broken -- answered as folds of rows, family by family. */

'use strict';

/* A section, presenting itself as one line until it is asked for more.

   The title and its one-line answer stay legible whether or not anything is
   open; what folds is the reasoning. `open` is the section's own judgement --
   a red check list opens itself, a green one does not.

   There is no eyebrow. `.step-eyebrow` carries a border-top, so a caption here
   would draw a second rule under the section's own -- and an empty eyebrow
   `<p>` goes on drawing it, which is a line with nothing above it at all. */
function fold({ open, title, line, body }) {
  return `
    <details class="fold sec-fold"${open ? ' open' : ''}>
      <summary>
        <h3 class="step-title">${esc(title)}</h3>
        ${line ? `<p class="fold-line">${line}</p>` : ''}
      </summary>
      ${body}
    </details>`;
}

/* The four answers, in the fewest words that are still true. Each is what its
   section spends a paragraph and a table saying. */
/* The two questions, drawn as headings rather than as tabs.

   `job` is what the section proves, said once. `meter` is the section's answer
   before you read a row of it: a tally and a segmented bar, so health reads at
   a glance and a reader can stop there. */
/* What blocks approval, and what is merely offered. Two counts, because one
   would be vague.

   A red check stops a feature being built. A missing test harness does not --
   it means criteria at that level come back unchecked, which is a cost and not
   a blockage. Merging them into "four things stand between you and your first
   feature" can be wrong about three of the four. */
/* One segment per thing the question is about, coloured by its own state. The
   bar is read before the tally is: three green and one red says where to look
   without anybody counting. */
function gateBars(p) {
  const results = ((p.baseline || {}).results) || [];
  const by = {};
  results.forEach((r) => { by[r.name] = r; });
  return (p.gates || []).map((g) => {
    const r = by[g.name];
    if (!r) return '';
    return isGreen(r) ? 'g' : 'r';
  });
}

/* What stands between this project and approval -- asked once, answered once.

   One rule, because two rules for one question disagree. With the Accept
   button disabling itself on `placement_probe.some(usable)` and this list
   calling a project blocked on `proved < declared`, a project with three
   placements of which two proved carries both at the same time: a live Accept
   button captioned "Lets features start here", and a panel above it reading
   "One thing blocks approval". Neither is a typo and only one can be true.

   So the button asks this, the banner asks this, and a placement that did not
   prove is reported where it is measured -- in the Tests section -- rather than
   being promoted to a blockage it is not. */
function blockingItems(p) {
  const results = ((p.baseline || {}).results) || [];
  const by = {};
  results.forEach((r) => { by[r.name] = r; });
  const out = [];
  if (!(p.gates || []).length) out.push('there are no checks');
  // One line for "never run", not one per check: with no baseline every check
  // is unrun, and listing each restated the first line six more times.
  if (!p.baseline) out.push('The checks have never been run');
  else (p.gates || []).forEach((g) => { if (!by[g.name]) out.push(`${g.name} has never been run`); });
  unrunnableGates(p.baseline).forEach((r) => out.push(`${r.name} reports nothing`));
  if (!(p.placement_probe || []).some((r) => r.usable)) {
    out.push('nowhere proved to put a blind test');
  }
  if (p.baseline && !judgingCount(p)) out.push('no check passes, so nothing would judge a feature');
  return out;
}

function offeredItems(p) {
  return pendingScaffolding(p).length + liveRecommendations().length;
}

function standingBlocks(p) {
  const blocking = blockingItems(p);
  const offered = offeredItems(p);
  return `
    ${!blocking.length ? '' : `
      ${/* No numeral on either of these. Stacked as a big 1 and a big 3 in the
           middle of the page they would read as steps in a sequence -- where
           is 2 -- when they are counts, and counts the sentence beside them
           already opens with. A count repeated in larger type is the page
           saying one fact twice, and here the second saying would be wrong. */''}
      <div class="standing blocks">
        <div>
          <p><b>${blocking.length === 1 ? 'One thing blocks' : `${blocking.length} things block`}
            approval</b></p>
          <ul class="blockers">${blocking.map((b) => `<li>${esc(b)}</li>`).join('')}</ul>
          <p class="blocks-how">Get ${blocking.length === 1 ? 'it' : 'them'} passing in your repo
            and run the checks again, or take ${blocking.length === 1 ? 'it' : 'them'} off the
            list. Nothing here edits code you wrote.</p>
        </div>
      </div>`}
    ${/* No "N suggestions from the reading" panel here. Explaining what a
         suggestion is -- that none of them blocks anything, that each adds
         rather than rewrites -- on every load, for ever, in a box between the
         proposals and the checks, says it once too often: the suggestions are
         on the same screen with their own headers and their own buttons, and a
         reader who has scrolled past four of them does not need to be told a
         fifth time what they are. What such a panel really carries is the
         count, and the section it belongs to already opens with one. */''}`;
}

const SECTION_ICON = {
  'as-built': `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
      stroke-linecap="square" stroke-linejoin="round" aria-hidden="true">
      <rect x="3" y="3" width="7" height="6"/><rect x="14" y="3" width="7" height="6"/>
      <rect x="8.5" y="15" width="7" height="6"/><path d="M6.5 9v3h11V9M12 12v3"/></svg>`,
  survey: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
      stroke-linecap="square" stroke-linejoin="round" aria-hidden="true">
      <path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4"/><circle cx="11.5" cy="13.5" r="2.8"/>
      <path d="M13.6 15.6l2.4 2.4"/></svg>`,
  history: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
      stroke-linecap="square" stroke-linejoin="round" aria-hidden="true">
      <path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v4h4"/><path d="M12 7.5V12l3 2"/></svg>`,
  environment: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
      stroke-linecap="square" stroke-linejoin="round" aria-hidden="true">
      <path d="M21 7.5l-9-4.5-9 4.5v9l9 4.5 9-4.5z"/><path d="M3 7.5l9 4.5 9-4.5"/><path d="M12 12v9"/></svg>`,
  checks: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
      stroke-linecap="square" aria-hidden="true">
      <path d="M4 5h9M4 12h9M4 19h9"/><path d="M16 4.5l2 2 4-4"/><path d="M16 11.5l2 2 4-4"/>
      <path d="M16.5 17.5l5 5M21.5 17.5l-5 5"/></svg>`,
  guides: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
      stroke-linecap="square" stroke-linejoin="round" aria-hidden="true">
      <path d="M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z"/><path d="M22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"/></svg>`,
  tests: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
      stroke-linecap="square" stroke-linejoin="round" aria-hidden="true">
      <path d="M9.5 3v6.2L4.2 18.4A2 2 0 0 0 5.9 21.5h12.2a2 2 0 0 0 1.7-3.1L14.5 9.2V3"/>
      <path d="M8 3h8"/><path d="M6.9 14.5h10.2"/></svg>`,
};

const TICK = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="square" aria-hidden="true"><path d="M2 8.5l4 4 8-9"/></svg>`;

/* One row: a lamp, a name, one fact, a verdict -- and the to-do that answers
   it, inside it. Every row on this page is this shape, whether it is a check
   that ran, a check that does not exist yet, a level of testing, or a file the
   reading is offering to write. */
function qRow({ lamp, name, fact, chip, tone, todo, prose, metric }) {
  const status = `<span class="chip ${tone}">${esc(chip)}</span>`;
  return `
    <div class="q-row">
      <div class="q-line${prose ? ' prose' : ''}">
        <span class="q-lamp ${lamp}"></span>
        <span class="q-name">${esc(name)}</span>
        <span class="q-fact">${fact}</span>
        ${metric ? `<span class="q-end">${metric}${status}</span>` : status}
      </div>
      ${todo || ''}
    </div>`;
}

/* Everything that answers "is anything that already worked broken": the checks
   that run, and the ones a reading says should. */
/* A check that ran, in the same shape as everything else on the page.

   `gateLine` still draws the results tab, where a row is a measurement being
   read rather than a decision being made -- different job, different row. Here
   the row is one line and the verbs are underneath it. */
function checkRow(g, r, p) {
  const green = r && isGreen(r);
  const cannot = r && gateOutcome(r) === 'could_not_run' && !r.skipped;
  /* "Parked" rather than "red" once the list has been accepted, because by then
     it is a different fact: the check is not judging anything, and a reader who
     sees red in a list they approved reasonably wonders what it is blocking. */
  const parked = p.stage === 'ready' && r && !green;
  /* Never a tests check, whose exit code also says a test failed, and never
     under the bar the project sets for itself, which a hold would contradict. */
  const holdable = r && !green && !cannot && r.metric != null && g.family !== 'tests'
    && r.red_kind !== 'under_own_floor';
  /* Why it is red, when it was worked out: its fixes lead, and the rest of
     the card steps back. */
  const dx = r && !green ? diagnosisOf(g) : null;
  const fixed = !!(dx && !dx.failed && (dx.fixes || []).length);
  const kind = r && !green && !cannot ? redKindSentence(g, r) : '';
  const chip = !r ? ['not measured', 'warn'] : green ? ['green', 'ok']
    : parked ? ['parked', 'warn']
    : cannot ? ["didn't run", 'bad'] : ['red', 'bad'];
  const covers = COVERAGE_REPORTS.includes(g.report_format);
  const facts = [
    r ? `exit ${r.exit_code}` : '',
    r && r.metric != null && !covers ? readingWord(g, r.metric) : '',
  ].filter(Boolean).join(', ');
  const tail = (r && r.output_tail) || '';
  /* A change a reading proposes to this check waits here, in its row. */
  const pending = waitingChanges().find((c) => c.name === g.name && c.action !== 'add');
  const row = qRow({
    lamp: green ? 'g' : r ? 'r' : 'a',
    name: g.name,
    fact: `<code>${esc(g.command)}</code>${facts ? ` &mdash; ${esc(facts)}` : ''}`
      + proposedFor(g, r)
      + checkFold(g, `<span class="q-sub">${runsLine(g, p)}</span>`
        + (guardsText(g) ? `<span class="q-sub">${guardsText(g)}</span>` : '')
        + heldOnNewCode(g)
        /* Otherwise setup quietly takes longer and nobody knows why. */
        + (r && r.warmed ? `<span class="q-sub">Its tool fetches part of what it needs only
            when it runs, so it failed offline at first. Setup now runs it once with the network
            before the checks run without it.</span>` : ''), r),
    chip: chip[0], tone: chip[1],
    metric: covers ? coveragePill(g, r) : '',
    /* Only a check that ran and failed gets the card. One with no result has
       not been measured -- after an edit to the list, every check -- and telling
       it to "get this command to pass" describes a failure nobody has seen. */
    todo: green || !r ? '' : todoCard({
      src: 'measured on untouched code',
      does: cannot
        ? `It never executed &mdash; the command is wrong, or the tool it needs is not in this
           environment. <a class="inline-act" href="${editHref(p.project_id, 'checks', g.name)}"
           >Edit the command</a>`
        : parked
        ? `${kind} Not green when the checks were last run, so it is <b>parked</b>: still on the list,
           still run when you run the checks, and left out of what judges a feature until it
           passes. It is in no packet, so nothing here is red for a reason that is not the
           feature's. Get it passing and the next run puts it back to work.`
        : fixed ? kind
        : `${kind} Red before any feature starts, so nothing built here could be measured against it.
           <b>Get this command to pass, then run the checks again</b> &mdash; or take it off the
           list, which is recorded with your reason.`,
      dx: r && !green ? diagnosisBlock(g, r, dx) : '',
      /* The advice comes before the buttons, and the button it argues for is at
         the end of the sentence that argues for it. Under the row of controls
         as a footnote it would be the wrong order twice: a reader chooses
         after reading, and "put it in the command" three lines above
         "Edit the command" makes them two separate ideas. */
      /* A reading means the honest answer can be a bound instead of a fix:
         held at the number it printed, green now and red on the next one. */
      note: cannot || fixed ? ''
        /* A ceiling in the command is advice for a project with no bar of its
           own. This one has one, and lowering it is not a fix. */
        : r.red_kind === 'under_own_floor'
        ? `Holding it lower here would contradict the project. Find out why it reads low here &mdash;
          a different version, a missing setting &mdash; or change the bar where the project keeps it.`
        : holdable
        ? `It read <b>${esc(String(r.metric))}</b> on untouched code. Hold it there and it is
          green now and red the moment a feature makes it worse &mdash; without fixing what is
          already there first.`
        : `If the honest answer is a ceiling rather than a fix, put it in the
        command &mdash; <span class="mono">--max-warnings</span>,
        <span class="mono">--fail-under</span>, a baseline file. Then the repository and this page agree on
        what passing means. <a class="inline-act" href="${editHref(p.project_id, 'checks', g.name)}"
        >Edit the command</a>`,
      gain: '',
      acts: `${holdable && !fixed ? `<button class="btn btn-sm btn-primary" data-ratchet="${esc(g.name)}"
               >Hold it at ${esc(String(r.metric))}</button>` : ''}
             ${tail ? `<button class="btn btn-sm ${holdable || fixed ? '' : 'btn-primary'}"
               data-show-output="${esc(g.name)}">Show the output</button>` : ''}
             <button class="btn btn-sm" id="run-baseline">Run the checks again</button>
             ${state.droppingGate === g.name ? '' : `
               <button class="btn btn-sm btn-quiet" data-drop-gate="${esc(g.name)}"
                 >Not for this project</button>`}`,
      after: `
        ${tail ? `<pre class="hz-pre file-out" data-file-for="out:${esc(g.name)}" hidden>${
          esc(tail)}</pre>` : ''}
        ${state.droppingGate !== g.name ? '' : `
          <div class="objection">
            <label class="small dim" for="drop-reason" style="display:block">
              Why not this project? Kept with the decision, and shown to every later reading.</label>
            <input class="inp" id="drop-reason" type="text" style="width:100%;margin-top:.25rem"
              placeholder="ten pre-existing errors, not fixing them today">
            <div class="d-actions" style="margin-top:.6rem">
              <button class="btn btn-sm btn-quiet" id="cancel-drop-gate">Keep it</button>
              <button class="btn btn-sm btn-primary" id="confirm-drop-gate"
                >Not for this project</button>
            </div>
          </div>`}
`,
    }),
  });
  return pending ? row.replace(/<\/div>\s*$/, `${proposalTodo(pending)}</div>`) : row;
}

/* Coverage, on its own beside the status: the share of lines the tests ran,
   coloured against the floor the project sets itself -- green with room to
   spare, amber within two points of it, red under it. Grey when no floor is
   known, because then there is nothing to be close to. */
function coveragePill(g, r) {
  if (!r || r.metric == null) return '';
  const floor = r.own_floor_value != null ? r.own_floor_value : g.threshold;
  const tone = floor == null ? '' : r.metric < floor ? 'bad' : r.metric - floor < 2 ? 'warn' : 'ok';
  const title = `${r.metric}% of lines ran${floor == null ? '' : ` · the floor is ${floor}%`}`;
  return `<span class="cov-pill ${tone}" title="${esc(title)}">${esc(String(r.metric))}%
    <small>covered</small></span>`;
}

/* Whether this project measures coverage, and how it came to: a check that
   does, a suggestion waiting to be adopted, or a decision not to. */
function coverageState(p) {
  const checks = (p.gates || []).filter((g) => COVERAGE_REPORTS.includes(g.report_format));
  if (checks.length) return { on: true, checks };
  return {
    on: false,
    rec: liveRecommendations().find((r) => COVERAGE_REPORTS.includes(r.report_format)),
    declined: (p.recommendation_rulings || []).find((r) => COVERAGE_REPORTS.includes(r.report_format)),
  };
}

/* One line at the top of the tests family: coverage on or off, said outright.
   Without it a project that measures nothing looks the same as one nobody
   has asked. Turning it on is adopting the suggestion; the form opens in the
   suggestion's row below. */
function coverageLine(p, by) {
  const c = coverageState(p);
  const head = (word, tone) => `<span class="cov-k">Coverage</span>
    <span class="cov-state ${tone}">${word}</span>`;
  if (c.on) {
    return `<div class="cov-line">${head('on', 'ok')}${c.checks.map((g) => `<span class="cov-by">
      <span class="mono">${esc(g.name)}</span> ${coveragePill(g, by[g.name])
        || '<span class="dim">not measured yet</span>'}</span>`).join('')}</div>`;
  }
  if (c.rec) {
    return `<div class="cov-line">${head('off', 'warn')}
      <span class="cov-why">No check measures it. Suggested: ${esc(c.rec.title)}</span>
      <span class="cov-acts">
        <button class="btn btn-sm btn-primary" data-adopt-rec="${esc(c.rec.key)}">Measure it</button>
        <button class="btn btn-sm btn-quiet" data-decline-rec="${esc(c.rec.key)}">Not for this project</button>
      </span></div>`;
  }
  if (c.declined) {
    return `<div class="cov-line">${head('off', '')}
      <span class="cov-why">Not for this project${c.declined.reason ? ` &mdash; ${esc(c.declined.reason)}` : ''}.</span>
      <span class="cov-acts"><button class="btn btn-sm btn-quiet" data-reinstate-rec="${esc(c.declined.key)}"
        >Ask about it again</button></span></div>`;
  }
  return `<div class="cov-line">${head('off', 'warn')}
    <span class="cov-why">No check measures it, and no reading has suggested one yet. Re-survey to
      have one suggested.</span></div>`;
}

/* The diagnosis that explains this check's red result in the last run, if any. */
function diagnosisOf(g) {
  return ((((state.project || {}).diagnoses) || {})[g.name]) || null;
}

/* A reading with its unit: a coverage figure is a share of lines. */
function readingOf(g, v) {
  if (v == null) return '';
  return COVERAGE_REPORTS.includes(g.report_format) || /%/.test(g.parse_metric || '')
    ? `${v}%` : String(v);
}

/* What kind of red it is, in one sentence -- computed by the server from what
   it read, never by a model. */
function redKindSentence(g, r) {
  const floor = g.own_floor || {};
  const where = floor.where === 'command' ? 'its command'
    : `<span class="mono">${esc(String(floor.where || '').split('#')[0])}</span>`;
  switch (r.red_kind) {
    case 'timed_out':
      return `Stopped at its ${esc(String(Math.round(g.timeout_s || 0)))}s limit before it finished.`;
    case 'no_report':
      return 'It ran, but wrote no report Fabrika could read, so nothing is known about what it found.';
    case 'under_own_floor':
      return `It read <b>${esc(readingOf(g, r.metric))}</b> against the <b>${esc(readingOf(g, r.own_floor_value))}</b>
        ${floor.points === 'ceiling' ? 'ceiling' : 'floor'} this project sets in ${where}. A bar the
        project sets for itself and does not meet on its own code means its own runs are red too,
        or this environment measures differently.`;
    case 'over_bound':
      return `It read <b>${esc(readingOf(g, r.metric))}</b>; it is held at
        <b>${esc(readingOf(g, g.threshold_max != null ? g.threshold_max : g.threshold))}</b>.`;
    case 'findings':
      return `Its report lists ${esc(String(r.metric != null ? r.metric : 'some'))} problem${r.metric === 1 ? '' : 's'}.`;
    case 'flaky':
      return 'It passed when it was run again, so it does not fail every time.';
    case 'failed':
      return `It exited ${esc(String(r.exit_code))}, and nothing Fabrika read says why.`;
    default:
      return '';
  }
}

/* Why it is red, and what would fix it -- each fix with what its try read.
   A fix is offered with its try beside it, so it arrives as "tried: passes",
   not as a guess. */
function diagnosisBlock(g, r, d) {
  if (!d) return '';
  if (d.failed) {
    /* The reason, not the traceback: its first clause says what was missing. */
    const why = String(d.failed).replace(/^[A-Za-z]+Error: /, '').split(/;\s/)[0];
    return `<p class="dx-failed">Fabrika couldn't work out why: ${esc(why)}.</p>`;
  }
  if (d.kind === 'flaky' || !d.cause) return '';
  const tryOf = (i) => (d.tries || []).find((t) => t.fix === i);
  const rank = (i) => (i === d.recommended ? 0 : (tryOf(i) || {}).passed ? 1 : tryOf(i) && !tryOf(i).passed ? 3 : 2);
  const order = (d.fixes || []).map((_, i) => i).sort((a, b) => rank(a) - rank(b));
  const tried = (f, t) => {
    if (f.kind === 'agent_prompt') return ['', 'Not tried: real code has to change.'];
    if (f.kind === 'hold') return ['', `Holds it at ${readingOf(g, r.metric)}.`];
    if (!t) return ['', 'Not tried.'];
    if (!t.ran) return ['bad', `Not tried: ${t.note || 'it could not be applied'}.`];
    return t.passed ? ['ok', `Tried: passes${t.metric != null ? `, ${readingOf(g, t.metric)}` : ''}.`]
      : ['bad', `Tried: still red${t.metric != null ? `, ${readingOf(g, t.metric)}` : ''}.`];
  };
  const VERB = {
    repo_change: ['Apply and commit', 'Show the change'], environment_change: ['Add to setup', 'Show the steps'],
    command_change: ['Use this command', 'Show it'], hold: [`Hold it at ${readingOf(g, r.metric)}`, ''],
    agent_prompt: ['Copy the prompt', 'Show it'],
  };
  const shown = (f) => f.kind === 'repo_change' ? f.contents
    : f.kind === 'environment_change' ? (f.setup || []).join('\n')
    : f.kind === 'command_change' ? f.command : f.kind === 'agent_prompt' ? f.prompt : '';
  const fixes = order.map((i, n) => {
    const f = d.fixes[i];
    const [tone, said] = tried(f, tryOf(i));
    const [yes, show] = VERB[f.kind] || ['Apply', ''];
    const key = `${g.name}:${i}`;
    return `<div class="dx-fix">
      <p class="dx-fix-t">${esc(f.title)}${f.kind === 'repo_change' && f.path
        ? ` <span class="dim mono">${esc(f.path)}</span>` : ''}</p>
      <p class="dx-try ${tone}">${esc(said)}</p>
      <div class="acts">
        ${f.kind === 'agent_prompt'
          ? `<button class="btn btn-sm ${n === 0 ? 'btn-primary' : ''}" data-dx-copy="${esc(key)}">${yes}</button>`
          : `<button class="btn btn-sm ${n === 0 ? 'btn-primary' : ''}" data-dx-apply="${esc(g.name)}"
              data-dx-fix="${i}">${yes}</button>`}
        ${show ? `<button class="btn btn-sm btn-quiet" data-dx-show="${esc(key)}">${show}</button>` : ''}
      </div>
      ${show ? `<pre class="hz-pre" data-dx-for="${esc(key)}" hidden>${esc(shown(f))}</pre>` : ''}
    </div>`;
  }).join('');
  const evidence = (d.evidence || []).length ? `<details class="hz-more"><summary>evidence</summary>
    <ul class="dx-ev">${d.evidence.map((e) => `<li class="mono">${esc(e)}</li>`).join('')}</ul></details>` : '';
  return `<div class="dx">
    <p class="dx-why"><span class="dx-k">Why</span>
      <span class="dx-tag ${d.confirmed ? 'ok' : ''}">${d.confirmed ? 'Confirmed' : 'Likely'}</span>
      ${esc(d.cause)}</p>
    ${evidence}
    ${fixes}
  </div>`;
}

/* The checks, by the question each one asks. A family with nothing in it is
   shown empty rather than left out: families are optional, and an empty one is
   a fact a person should be able to see and decide about -- not a gap they
   only find by noticing what is missing from a list. */
const FAMILY_ICONS = { structure: 'l-braces', quality: 'l-gauge', tests: 'l-flask' };
const FAMILIES = [
  ['structure', 'Is the code well-formed?'],
  ['quality', 'Is the code healthy?'],
  ['tests', 'Does the code behave?'],
];

/* A check a reading proposes adding: a row in its family that does not exist
   yet, with the decision inside it. */
function proposedAddRow(c) {
  return `<div class="q-row"><div class="q-line prose">
    <span class="q-lamp a"></span><span class="q-name">${esc(c.name)}</span>
    <span class="q-fact"><code>${esc((c.gate || {}).command || '')}</code></span>
    <span class="chip warn">proposed</span></div>${proposalTodo(c)}</div>`;
}

/* Rules a check of another family runs that answer this family's question, as
   a row in this family. Its state is the check's: the same command, run once. */
function alsoRow(g, a, r, p, proposed = false) {
  const green = r && isGreen(r);
  const parked = p.stage === 'ready' && r && !green;
  const chip = proposed ? ['proposed', 'warn'] : !r ? ['not measured', 'warn']
    : green ? ['green', 'ok'] : parked ? ['parked', 'warn'] : ['red', 'bad'];
  const ran = `run by <span class="mono">${esc(g.name)}</span>, a ${esc(g.family || 'other')} check`;
  return qRow({
    lamp: proposed ? 'a' : green ? 'g' : r ? 'r' : 'a',
    name: a.what || '', prose: true, chip: chip[0], tone: chip[1],
    fact: `<code>${esc(g.command || '')}</code>`
      + `<span class="q-sub">${ran}${proposed ? ` &mdash; the re-survey proposes recording it;
          rule on it in <span class="mono">${esc(g.name)}</span>'s row` : ''}</span>`
      + (a.evidence ? `<span class="q-sub dim">${esc(a.evidence)}</span>` : ''),
  });
}

function suggestionRow([r, i]) {
  return qRow({
    lamp: 'a', name: r.title || '', chip: 'suggestion', tone: 'warn',
    fact: `<code>${esc(r.would_gate || '')}</code>`,
    todo: recTodo(r, i), prose: !!r.title,
  });
}

function familyGroups(p, by, recs) {
  const gates = p.gates || [];
  const ruled = {};
  (p.family_rulings || []).forEach((f) => { ruled[f.family] = f; });
  const group = (title, asks, body, mark = '') => `
    <div class="fam">
      <h3 class="fam-h">${mark ? icon(mark, 'ic ic-sm fam-ic') : ''}${esc(title)}${asks
        ? ` <span class="fam-asks">${esc(asks)}</span>` : ''}</h3>
      ${body}
    </div>`;
  const out = FAMILIES.map(([fam, asks]) => {
    const rows = gates.filter((g) => g.family === fam).map((g) => checkRow(g, by[g.name], p)).join('')
      + waitingChanges().filter((c) => c.action === 'add' && ((c.gate || {}).family || '') === fam)
        .map(proposedAddRow).join('');
    const sugg = recs.filter(([r]) => r.family === fam).map(suggestionRow).join('');
    const covered = fam === 'tests' ? coverageLine(p, by) : '';
    /* Rules another family's check runs that answer this family's question --
       security rules in the linter's config. Each is a row of its own here,
       where a reader looks for them: one line under the family would read as
       a footnote, and the family would look empty. */
    const also = gates.flatMap((g) => (g.also || []).filter((a) => a.family === fam)
      .map((a) => alsoRow(g, a, by[g.name], p)));
    /* And what a reading proposes to record, ruled on in the check's own row. */
    const alsoProposed = waitingChanges().filter((c) => c.action === 'change').flatMap((c) => {
      const now = gates.find((g) => g.name === c.name) || {};
      const had = new Set((now.also || []).map((a) => `${a.family}:${a.what}`));
      return ((c.gate || {}).also || [])
        .filter((a) => a.family === fam && !had.has(`${a.family}:${a.what}`))
        .map((a) => alsoRow({ ...now, ...c.gate }, a, null, p, true));
    });
    const alsoLine = also.join('') + alsoProposed.join('');
    const declined = ruled[fam];
    let empty = '';
    if (alsoLine) {
      empty = '';
    } else if (declined) {
      empty = `<p class="fam-empty">Not for this project${declined.reason
        ? ` &mdash; ${esc(declined.reason)}` : ''}. Nothing here is measured, and nothing in it is
        suggested. <button type="button" class="linkish" data-reinstate-family="${fam}">Ask about it again</button></p>`;
    } else if (!rows) {
      empty = state.decliningFamily === fam ? `
        <div class="objection">
          <label class="small dim" for="family-reason" style="display:block">
            Why does this project go without it? Kept with the decision, and shown here.</label>
          <input class="inp" id="family-reason" type="text" style="width:100%;margin-top:.25rem"
            placeholder="a prototype; not worth it yet">
          <div class="d-actions" style="margin-top:.6rem">
            <button class="btn btn-sm btn-quiet" id="cancel-decline-family">Keep asking</button>
            <button class="btn btn-sm btn-primary" data-confirm-decline-family="${fam}"
              >Not for this project</button>
          </div>
        </div>`
        : `<p class="fam-empty">No check asks this, so a feature built here is not measured on
          it. That is allowed. <button type="button" class="linkish" data-decline-family="${fam}"
          >Not for this project</button></p>`;
    }
    return group(fam, asks, `${covered}${rows}${alsoLine}${sugg}${empty}`, FAMILY_ICONS[fam]);
  });
  /* Recorded before there were families. Shown, never sorted by guessing:
     the next reading proposes a family for each, and a person approves it. */
  const unset = gates.filter((g) => !FAMILY_ASKS[g.family]);
  const unsetAdds = waitingChanges().filter((c) => c.action === 'add' && !FAMILY_ASKS[(c.gate || {}).family]);
  if (unset.length || unsetAdds.length) {
    out.push(group('Family not set', 'The next reading proposes one for each.',
      unset.map((g) => checkRow(g, by[g.name], p)).join('') + unsetAdds.map(proposedAddRow).join('')));
  }
  const other = recs.filter(([r]) => !FAMILY_ASKS[r.family]);
  if (other.length) out.push(group('Other suggestions', '', other.map(suggestionRow).join('')));
  return out.join('');
}

/* A re-survey's proposal is ruled on on the Survey tab, but what it proposes
   is a change to this list -- so this list says so, at the top and on each row
   it would change. Without it a reading that found exactly what was asked for
   looks, from here, as if it had found nothing. */
function waitingChanges() {
  const rs = state.resurvey;
  if (!rs || !rs.diff) return [];
  const pending = new Set(rs.pending_checks || []);
  return (rs.diff.gate_changes || []).filter((c) => pending.has(`${c.action}:${c.name}`));
}

/* Whether the rest of a reading -- its testing surface, rules, placements,
   files and environment -- is waiting on the Survey tab. Its checks are not
   part of this: they wait in their rows on the Checks page. */
function surveyWaiting() {
  return partsWaiting().length > 0;
}

/* What was decided about one proposed check, if anything. */
function checkRuling(key) { return (((state.resurvey || {}).checks) || {})[key] || null; }

/* A proposed change, as a to-do in the row it would change. */
function proposalTodo(c) {
  const key = `${c.action}:${c.name}`;
  const now = ((state.project || {}).project || {}).gates || [];
  const was = now.find((g) => g.name === c.name) || {};
  const g = c.gate || {};
  const more = [];
  const added = (a, b) => (b || []).filter((x) => !(a || []).includes(x));
  const files = added(was.config_files, g.config_files);
  const marks = added(was.suppressions, g.suppressions);
  const where = (entry) => {
    const [file, section] = String(entry).split('#');
    return section ? `${esc(file)}, section ${esc(section)}` : esc(file);
  };
  if (files.length) more.push(`watch <span class="mono">${files.map(where).join('</span>; <span class="mono">')}</span>`);
  if (marks.length) more.push(`report a new <span class="mono">${marks.map(esc).join('</span> or <span class="mono">')}</span>`);
  if (g.report_format && g.report_format !== was.report_format) more.push(`read its ${esc(g.report_format)} report`);
  if (g.files_command && !was.files_command) more.push('measure a worker’s own tests during its turn');
  /* A change that leaves the command as it is only describes the check
     better -- what its rules also cover, where its settings live. Said as
     that, not as "run it as" the command it already runs. */
  const sameCommand = c.action === 'change' && g.command === was.command;
  const newAlso = (g.also || []).filter((a) => !(was.also || []).some((b) => b.what === a.what));
  if (newAlso.length) more.unshift(`count as ${newAlso.map((a) => `${esc(a.family)}: ${esc(a.what)}`).join(', ')}`);
  if (g.own_floor && !was.own_floor) {
    more.push(`know the ${g.own_floor.points === 'ceiling' ? 'ceiling' : 'floor'} this project sets itself, in
      <span class="mono">${esc(g.own_floor.where)}</span>`);
  }
  const does = c.action === 'remove'
    ? `Remove this check. ${esc(firstSentence(c.reason))}`
    : c.action === 'add'
      ? `A new check. ${esc(firstSentence(c.reason))}`
      : sameCommand
        ? `Record what this check covers; the command stays as it is. ${esc(firstSentence(c.reason))}`
        : `Run it as <code>${esc(g.command || '')}</code>. ${esc(firstSentence(c.reason))}`;
  const [yes, no] = c.action === 'remove' ? ['Remove it', 'Keep it']
    : c.action === 'add' ? ['Add it', 'Not for this project']
    : sameCommand ? ['Record it', 'Leave it'] : ['Use the new command', 'Keep this one'];
  return `<div class="todo prop">
    <p class="todo-src">proposed by the re-survey</p>
    <p class="todo-does">${does}</p>
    ${more.length && c.action !== 'remove' ? `<p class="todo-note">It would also ${more.join(', ')}.</p>` : ''}
    ${(c.reason || '').length > firstSentence(c.reason).length || c.evidence ? `<details class="hz-more">
      <summary>the reading’s reason</summary><p class="hz-why">${esc(c.reason || '')}</p>
      ${c.evidence ? `<p class="hz-why dim">${esc(c.evidence)}</p>` : ''}</details>` : ''}
    <div class="acts">
      <button class="btn btn-sm btn-primary" data-rule-check="${esc(key)}" data-accept="1">${yes}</button>
      <button class="btn btn-sm btn-quiet" data-rule-check="${esc(key)}" data-accept="0">${no}</button>
      ${c.action === 'remove' ? '' : '<span class="small dim">Either way, nothing else in the proposal changes.</span>'}
    </div></div>`;
}

function proposalNotice() {
  const changes = waitingChanges();
  if (!changes.length) return '';
  return `<div class="proposal-note">${icon('l-circle-alert', 'ic ic-sm')}
    <p><b>The re-survey proposes ${changes.length} change${changes.length === 1 ? '' : 's'} to these
      checks</b>, each in the row it would change. Nothing changes until you rule on it, one at a
      time.</p></div>`;
}

function checksQuestion(p) {
  const by = {};
  (((p.baseline || {}).results) || []).forEach((r) => { by[r.name] = r; });
  const recs = liveRecommendations().map((r, i) => [r, i]).filter(([r]) => todoSection(r) === 'checks');
  return `
    ${sectionHead('checks', 'Checks', '',
                  gateCount(p), gateBars(p))}
    ${state.editing === 'checks' ? checksEditor() : `${staleBaselineNote(p)}
    <div class="q-rows">
    <p class="tab-intro">The commands your repository already runs against its code. Fabrika runs
      them on every feature it builds, and a feature that breaks one that passed is held to it.
      <a href="#" data-help-open="checks">More about checks</a> ·
      <a href="#/${encodeURIComponent(p.project_id)}?gates=results">The last run in full</a></p>
      ${proposalNotice()}
      ${(p.gates || []).length || recs.length ? familyGroups(p, by, recs)
        : '<p class="empty">No checks proposed.</p>'}
    </div>
    ${declinedChecks(p)}
    ${declinedRecommendations(p, 'checks')}`}`;
}
