/* Rendering: which screen the stage and the hash choose, the feature bar, the
   mounted review view, and how often to poll. */

'use strict';

/* ---------------------------------------------------------------- render */

/* A step you asked for, or a plain note when its artifact does not exist yet.
   The screens are the same ones the stage would have shown; what changes is that
   nothing on them can be acted on. */
function stepScreen(id) {
  const d = state.data;
  const step = STEPS.find((x) => x.id === id);
  if (step && !step.has(d)) {
    /* The one screen in the console with no heading of any level: a step you
       asked for before the line reached it. It still says where you are. */
    return `${featureBar(d, { heading: false })}
      <div class="wrap doc-wrap"><div class="narrow">
        <h1 class="sec-title" style="margin-top:1.9rem">Nothing at ${esc(step.label)} yet.</h1>
        <p class="empty" style="margin-top:0">It appears here as soon as the factory produces it.
          The line is at <b>${esc((STEPS.find((x) => x.id === currentStep(d)) || {}).label
            || currentStep(d))}</b>.</p>
      </div></div>`;
  }
  switch (id) {
    case 'intent': return intentScreen();
    case 'scout': return scoutScreen();
    // Handled by the module when there is an interrogation to show.
    case 'questions': return intakeRunningScreen();
    case 'spec': return specScreen();
    case 'build': return d.state.stage === 'failed' ? failedScreen() : runningScreen();
    // Unreachable in practice -- `review` is a mounted gate-2 view -- and
    // kept honest rather than deleted: a step id that resolves to nothing is a
    // blank screen the first time someone adds a route.
    case 'review': return reviewScreen();
    default: return intentScreen();
  }
}

/* What you asked for, in your words -- which would otherwise exist only in
   the create form and never be shown again. */
function intentScreen() {
  const d = state.data;
  const corrections = d.corrections || [];
  const report = d.interrogation || {};
  return `
    ${featureBar(d, { hash: '' })}
    <div class="wrap doc-wrap"><div class="narrow">
      <div style="padding:1.5rem 0 1rem">
        <p class="eyebrow">What you asked for</p>
        <h2 class="sec-title" style="margin:0">${esc(d.state.title || 'Untitled')}</h2>
        <p class="restated">${esc(d.state.intent || '')}</p>
        <p class="small dim" style="margin-top:.8rem">Recorded ${esc(rel(d.state.created_at))}.
          Everything downstream was built from these words.</p>
      </div>
      ${report.restated_intent ? `<section class="section">
        <p class="eyebrow">How the interrogator read it</p>
        <p class="restated">${esc(report.restated_intent)}</p>
      </section>` : ''}
      ${corrections.length ? `<section class="section">
        <p class="eyebrow">Corrections you made · ${corrections.length}</p>
        ${corrections.map((c) => `<div class="settled">
          <div class="settled-a">${esc(typeof c === 'string' ? c : (c.text || c.correction || ''))}</div>
          ${(c && c.restated_intent) ? `<div class="settled-q" style="margin-top:.3rem">It had
            said: ${esc(c.restated_intent)}</div>` : ''}
        </div>`).join('')}
      </section>` : ''}
    </div></div>`;
}

/* What the scout found, kept readable after intake has long since finished.

   The summary is nine slice summaries joined by merge_scouts, ~7k characters of
   prose that says "this slice covers X" nine times. It is an archive, not a
   finding, so the structured lists lead and it goes last, collapsed. */
function scoutScreen() {
  const d = state.data;
  const scout = d.scout || {};
  const digest = d.digest || {};
  const slices = (scout.summary || '').split(/\n{2,}/).map((x) => x.trim()).filter(Boolean);

  // Nine slices unioned gives 400 items across six lists. All of it is real and
  // none of it is scannable, so each list shows a readable head and keeps the
  // tail one click away rather than dropping it.
  const SHOWN = 10;
  const li = (x) => `<li>${esc(x)}</li>`;
  const list = (label, items, note) => (items || []).length ? `<section class="scout-list">
      <p class="subhead">${esc(label)} · ${items.length}</p>
      ${note ? `<p class="small dim" style="margin:-.15rem 0 .4rem">${esc(note)}</p>` : ''}
      <ul class="doc-list">${items.slice(0, SHOWN).map(li).join('')}</ul>
      ${items.length > SHOWN ? `<details class="more">
        <summary>${items.length - SHOWN} more</summary>
        <ul class="doc-list">${items.slice(SHOWN).map(li).join('')}</ul>
      </details>` : ''}
    </section>` : '';

  return `
    ${featureBar(d, { hash: '' })}
    <div class="wrap doc-wrap">
      <div class="narrow" style="padding:1.5rem 0 .5rem">
        <p class="eyebrow">What the scout found</p>
        <h2 class="sec-title" style="margin:0">The repository, as it was read</h2>
        ${digest.files ? `<p class="muted">${digest.files_read} of ${digest.files} files,
          ${Math.round((digest.chars_read || 0) / 1000)}k characters, in
          ${digest.slices} slice${digest.slices === 1 ? '' : 's'}${digest.symbols
            ? `, with ${digest.symbols} symbols indexed` : ''}.</p>` : ''}
      </div>
      <div class="narrow">${digestNote(d)}${driftNote(d.drift)}</div>

      <div class="scout-cols">
        ${list('Do not duplicate', scout.do_not_duplicate,
          'What a worker would otherwise reimplement.')}
        ${list('Risks', scout.risks)}
        ${list('Existing capabilities', scout.existing_capabilities)}
        ${list('Contradicts a guide', (scout.contradictions || []).map((c) =>
          `${c.guide}: "${c.rule}" — ${(c.files || []).join(', ')}${c.in_feature_area ? ' (in this feature\'s area)' : ''}`))}
        ${list('Observed conventions', (scout.observed_conventions || []).map((c) =>
          `${c.rule} — ${(c.files || []).slice(0, 4).join(', ')}`),
          'Inferred from the code, only where no guide in the repository speaks.')}
        ${list('Stack', scout.stack)}
        ${list('Relevant files', scout.relevant_files)}
      </div>

      ${slices.length ? `<div class="narrow">
        <details class="decided" style="margin-top:1.5rem">
          <summary>What each slice reported${slices.length > 1
            ? ` · ${slices.length}` : ''} — the scout's own prose, unedited</summary>
          <div class="decided-body">${slices.map((x, i) => `
            <div class="slice-note">
              <p class="subhead">slice ${i + 1}</p>
              <p>${esc(x)}</p>
            </div>`).join('')}</div>
        </details>
      </div>` : ''}
    </div>`;
}

/* Wrapped rather than called from each of the branches below, which return in
   several places -- and a branch that forgot it would let a reader navigate
   to another project and keep the first one's overlay. Painted after the screen
   is written, because the overlay is positioned from the pane's own box. */
/* Nothing can be learned while the server is unreachable, so nothing on the
   page moves. Every refresh path renders at the end regardless of what its
   loads returned, and with the server gone that would mean redrawing screens
   from state which cannot be refreshed -- turning a working project page into
   "Loading…" across a restart and leaving it there until somebody reloads.

   The offline bar is painted on its own, so the page still says why it has
   stopped. `comeBack` re-routes, and that renders properly. */
function render() {
  if (state.offline) { paintOfflineBar(); return; }
  renderScreens();
  paintBusy();
  syncSheet();
}

function renderScreens() {
  // Above everything, and before the branch below: the bar lives outside `#main`
  // precisely so no screen can decide whether it is drawn.
  paintStaleBar();
  // One family of screens owns its own DOM; everything else on this page is
  // rebuilt from a string on every refresh, which is fine for reading and
  // impossible for typing. Asked first, because otherwise the stage switch
  // below builds a screen that is immediately discarded.
  if (gate2Active()) {
    // Gate 2 owns `#main` directly from here on -- the cached string from the
    // last string-built screen no longer describes what's in it.
    lastMainHtml = null;
    document.body.classList.remove('ab-on');
    renderFeatureBar();
    mountGate2();
    renderPanel();
    renderCrumbs();
    syncHeader();
    managePolling();
    return;
  }
  releaseGate2();
  setBar('');
  if (state.editing && !state.draft && state.project && state.project.project) startDraft();

  let html;
  if (state.view === 'setup') {
    html = setupScreen();
  } else if (state.view === 'roles') {
    html = rolesScreen();
  } else if (state.view === 'agent-config') {
    html = agentConfigScreen();
  } else if (state.view === 'control-room') {
    html = controlRoomScreen();
  } else if (state.view === 'help') {
    html = helpScreen();
  } else if (!state.projectId) {
    html = yardScreen();
  } else if (!state.project) {
    html = (!state.offline && (state.projectError || state.loadError)) ? notFoundScreen()
      : `<div class="wrap"><p class="empty">Loading…</p></div>`;
  } else if (state.view === 'log' && state.data) {
    html = logScreen();
  } else if (state.view === 'log' && !state.id) {
    html = projectLogScreen();
  } else if (state.view === 'gates' && state.project) {
    // The gates, the baseline, the drift and the proposal, at any stage, not
    // only while a project is *not* ready: if the bench took the route once it
    // was approved, everything gate 0 shows -- including the button that re-runs
    // the baseline -- would be unreachable until something broke the project
    // again.
    html = projectScreen();
  } else if (state.view === 'packet' && state.data) {
    // The full packet: the strongest objection, every finding with its outcome,
    // the decisions you rule on, and what the repair loop cost. Reached from
    // the requirement-first screen rather than landed on, because a wall of
    // findings is what you read second.
    html = reviewScreen();
  } else if (state.data && STEP_IDS.includes(state.view)) {
    // You asked for a step, so you get that step -- not the one the stage is on.
    html = stepScreen(state.view);
  } else if (state.project.project.stage !== 'ready') {
    // A project that has not been through gate 0 has no bench.
    html = projectScreen();
  } else if (state.id && !state.data && state.loadError && !state.offline) {
    html = notFoundScreen();
  } else if (!state.id || !state.data) {
    // A ready project with no feature is the start screen, handled above. This
    // is a project mid-gate-0, or a feature whose data has not landed yet.
    html = state.id
      ? `<div class="wrap"><p class="empty">Loading…</p></div>`
      : projectScreen();
  } else {
    // The stage picks the screen. There is no other way to get to one.
    switch (state.data.state.stage) {
      case 'intake': html = intakeRunningScreen(); break;
      case 'writing_spec': html = planningScreen(); break;
      // awaiting_answers with an interrogation is handled above; reaching here
      // means intake has not returned yet.
      case 'awaiting_answers': html = intakeRunningScreen(); break;
      // The spec is written and waiting on you: the document is the screen.
      case 'awaiting_spec_approval': html = specScreen(); break;
      // The architect and the plan checker disagree, or code measured the cut.
      case 'awaiting_cut_approval': html = cutReviewScreen(); break;
      case 'building': html = runningScreen(); break;
      case 'failed': html = failedScreen(); break;
      // A settled or awaiting packet is handled above: it opens
      // requirement-first, with the full packet a step away at ?packet.
      default: html = reviewScreen();
    }
  }
  /* An event anywhere in the project -- another feature entirely, one you are
     not even looking at -- ends here too: `subscribe` cannot tell what changed,
     only that something did, so it refreshes whatever screen is open. Most of
     the time that screen's own data did not move, and the string built from it
     comes out identical. Writing it in anyway would tear down and rebuild the
     whole subtree for nothing -- every scroll position, every open <details>,
     every DevTools selection, lost to a rebuild that changes nothing on screen. */
  /* A control the reader has already pressed once is a question waiting for
     its second press, and the rebuild below answers it for them: the button is
     replaced mid-question and the press lands on nothing. Every armed control
     clears itself after ARM_MS, so this defers a repaint rather than dropping
     one -- `lastMainHtml` is left alone, and the next poll draws it. */
  if (html !== lastMainHtml && !main.querySelector('[data-armed="1"]')) {
    /* What the reader opened survives the rebuild.

       An identical string is already skipped above, but any real change --
       a timestamp ticking over, a check finishing -- rewrites the subtree and
       takes every open <details> with it. That is most of this page: the
       two questions, the recommendations, the harnesses a reading asked for.
       Closing them behind a reader who opened them reads as the page
       refreshing itself, which is exactly what it is doing.

       Keyed by the summary's own text, which is stable across a rebuild and
       does not need an id invented for it. */
    const wasOpen = new Set();
    main.querySelectorAll('details').forEach((d) => {
      if (d.open) wasOpen.add((d.querySelector('summary') || {}).textContent || '');
    });
    /* And what the reader typed. A reason half-written, a draft guide being
       edited: a rebuild for something else entirely -- a feature finishing in
       another tab -- would put the field back as it was drawn. Kept by id, with the
       caret, for every field whose text differs from what the page drew. */
    const typed = new Map();
    main.querySelectorAll('textarea[id], input[id]:not([type=checkbox]):not([type=radio])').forEach((el) => {
      if (el.value !== el.defaultValue) typed.set(el.id, el.value);
    });
    const focused = document.activeElement && main.contains(document.activeElement)
      ? { id: document.activeElement.id, at: document.activeElement.selectionStart } : null;
    main.innerHTML = html;
    typed.forEach((value, id) => {
      const el = document.getElementById(id);
      if (el) el.value = value;
    });
    if (focused && focused.id) {
      const el = document.getElementById(focused.id);
      if (el) {
        el.focus();
        try { if (focused.at != null) el.setSelectionRange(focused.at, focused.at); } catch (e) { /* not a text field */ }
      }
    }
    // The as-built's pages keep the project's tabs on screen; said with a
    // class rather than `:has()`, which not every browser this runs in reads.
    document.body.classList.toggle('ab-on', !!main.querySelector('.ab-nav'));
    if (wasOpen.size) {
      main.querySelectorAll('details').forEach((d) => {
        if (wasOpen.has((d.querySelector('summary') || {}).textContent || '')) d.open = true;
      });
    }
    lastMainHtml = html;
    /* Once, when the fragment arrives -- not on every render. This page
       rebuilds itself on a four-second poll, and scrolling on each rebuild
       would yank the reader back to the anchor they had scrolled away from. */
    if (pendingScroll) {
      const target = document.getElementById(pendingScroll);
      if (target) {
        target.scrollIntoView({ block: 'start' });
        /* A check reached from "Edit the command" is reached to edit the
           command. Scrolling to the row and leaving the caret elsewhere makes
           the reader hunt for the field the link was about. */
        const cmd = pendingScroll.startsWith('check-')
          ? target.querySelector('[data-draft$=".command"]') : null;
        if (cmd) cmd.focus();
        pendingScroll = null;
      }
    }
    watchSections();
  }
  renderPanel();
  renderCrumbs();
  syncHeader();
  managePolling();
}

/* The board is the header on screens that have a line and the masthead is the
   header on screens that do not -- and half the boards on this page are built
   into `main` as a string rather than through setBar, so which one is present
   is read off the DOM after the render rather than tracked at each call site.
   Both carry the rail toggle, and it is synced here. */
function syncHeader() {
  document.body.classList.toggle('has-board', !!document.querySelector('.board'));
  syncToggle();
  /* Read off the DOM for the same reason: whether this screen can be read full
     screen is whether it drew the button, and leaving it takes the mode with
     you without anything having to remember to turn it off. */
  syncFocus();
}

/* The gate-2 screens. Loaded on first visit and never on any other screen: a
   route nobody opens costs nothing. */
const GATE2_VIEWS = ['calls', 'map', 'criterion', 'evidence', 'rework', 'questions', 'review',
                     'objections', 'repairs', 'blindspots', 'file', 'dependencies'];
/* A settled or awaiting packet (`SETTLED_STAGES`) renders these with no view in
   the hash at all, so "is this screen ours" cannot be answered by the view alone. */
/* Which of these screens this route wants, or null for the string-built ones.
   One function rather than a condition plus a default, because the two can
   disagree: a mount guard that says no for a stage whose default screen is
   ours leaves the page on "Loading…" forever. */
function mountedView() {
  if (!state.projectId || !state.project) return null;
  if (['roles', 'agent-config', 'log'].includes(state.view)) return null;

  /* No feature selected: the checks, which is the project.

     Not the composer. `checks` is not a link in the strip -- the checks are
     the landing page -- so handing an approved project to a blank "describe the
     feature" box would leave no route back to the list at all. The one page a
     project is mostly about would become unreachable by accepting it.

     Starting a feature is an action, and it has a button: `?start` is where it
     goes. */
  if (!state.id) {
    return state.view === 'start' && state.project.project.stage === 'ready' ? 'start' : null;
  }
  if (!state.data) return null;

  // Gate 1 has nothing to draw until the interrogator has returned; until then
  // the stage says awaiting_answers and the honest screen is the running one.
  const asked = !!state.data.interrogation;
  if (GATE2_VIEWS.includes(state.view)) {
    return state.view === 'questions' && !asked ? null : state.view;
  }
  if (state.view) return null;

  const stage = state.data.state.stage;
  if (stage === 'awaiting_answers') return asked ? 'questions' : null;
  /* A frozen spec awaiting approval does not default into the gate-1
     component, whose own three-tab summary of the spec would make clicking a
     feature in the rail land on a second, thinner rendering of the document that
     `spec` in the step strip opens properly. One frozen spec, one screen. */
  /* The work waiting, not the record of what was done. A packet opened with
     no view in the hash is a reviewer arriving, and what they are here for is
     the calls. */
  return SETTLED_STAGES.includes(stage) ? 'calls' : null;
}

const gate2Active = () => mountedView() !== null;
let gate2 = null;
let gate2Loading = null;

/* Whether the last run's packet has been asked for, on a feature whose branch
   a run is measuring again. Not a place in the review and not a preference: it
   answers "show me the one being replaced", asked on arrival, so it is
   remembered for the feature it was asked about and for no other -- walking to
   another feature is a fresh arrival and folds it back up.

   Held out here rather than in the component that draws it, because the route
   unmounts gate 2 whenever it reloads the feature: state kept inside would
   fold the packet away under a reader who had just opened it and pressed a
   tab. */
let prevPacketFor = null;

function gate2Props() {
  const f = (v) => `${featureHref(state.projectId, state.id)}?${v}`;
  return {
    view: mountedView(),
    param: state.param,
    projectId: state.projectId,
    featureId: state.id,
    reading: focusOn(),
    prevOpen: prevPacketFor === state.id,
    onPrev: () => {
      prevPacketFor = prevPacketFor === state.id ? null : state.id;
      render();
    },
    hrefs: {
      rework: () => f('rework'),
      criterion: (id) => f(`criterion=${encodeURIComponent(id)}`),
      /* With an id, one criterion's evidence; without, the run's. The tab
         strip calls this with no argument, so the bare form is not
         optional. */
      evidence: (id) => f(id ? `evidence=${encodeURIComponent(id)}` : 'evidence'),
      calls: () => f('calls'),
      map: () => f('map'),
      /* A path carries slashes, so it is encoded here and decoded by the hash
         parser -- the same shape as `?criterion=AC-1`, for the same reason. */
      file: (path) => f(`file=${encodeURIComponent(path)}`),
      objections: () => f('objections'),
      repairs: () => f('repairs'),
      blindspots: () => f('blindspots'),
      dependencies: () => f('dependencies'),
      packet: () => f('packet'),
    },
    onReload: () => refresh(),
    initial: {
      data: state.data,
      traces: state.traces || [],
      tracesOpenWith: state.tracesOpenWith || '',
      // Computed once, in one place: the two ways of reading a packet must not
      // disagree about how long it took or who looked at it.
      run: state.data && state.data.packet ? runSummary(state.data) : null,
      project: state.project.project,
      features: state.features.map((f) => ({ ...f, when: rel(f.updated_at) })),
      drift: state.project.drift,
      plan_estimate: state.intakePlan,
      /* Only when this console is served from the machine the factory runs on.
         An editor url is a scheme handler: it resolves on the browser's own
         filesystem, against a path that exists on the factory's. From anywhere
         else the link opens the wrong file, or silently nothing. */
      editor: ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)
        ? (state.config || {}).editor_url || '' : '',
      browsing: browsing(),
      // The start screen has no feature, so none of these exist there.
      flags: (state.data || {}).flags || [],
      plan: (state.data || {}).rework || { route: 'accept', because: '' },
      budget: (state.data || {}).budget || null,
      // Derived from the content, not a counter: a background refresh that
      // changed nothing must not reset what you are in the middle of doing.
      stamp: JSON.stringify([
        state.data && [state.data.flags, state.data.rework, state.data.budget,
                       state.data.state.stage, state.data.corrections,
                       (state.data.rulings || []).length],
        state.features.map((f) => [f.feature_id, f.stage, f.updated_at]),
        state.view, state.param]),
    },
  };
}

/* The bar a mounted screen would otherwise not have.

   Actions only where the screen has no better home for them: the gate-2 landing
   has none, so the verdict lives here. Gate 1 owns its own freeze button next to
   the sentence explaining what freezing does, and the rework screen owns its
   dispatch next to what it costs -- neither should be reachable from a toolbar
   that cannot show the consequence.

   "Verify again" sits here with the verdict for the same reason the verdict
   does. It is not an exit from the review and it says nothing about the
   work, so the rework screen -- which is entirely about what a human flagged in
   the work -- is the wrong home for it, where it would be reachable only from a
   link that appears once you have flagged something, which is the opposite of
   when you want it. What it is about is the packet in front of you, so it belongs on
   the bar of the screen showing that packet. The consequence it cannot show in
   a label is in its title, and it is disabled unless the feature is actually
   sitting on a packet awaiting a ruling. */
/* Whether the mounted screen's primary action may fire. Held here because the
   bar is rebuilt from a string on every app-level render, and the screen only
   speaks up when its own state changes -- without this the button would come
   back disabled after every poll. */
let barActionOn = false;

/* The one action a mounted screen owns, if it owns one. Its label depends on
   the screen; whether it may be pressed is the screen's business, not ours. */
function barAction(view) {
  const stage = (state.data || {}).state ? state.data.state.stage : '';
  /* The start screen's action sits next to its composer: in the bar it would
     need a sentence explaining why it is disabled, and beside an empty box it needs
     none. The bar keeps the actions that act on the line itself. */
  const label = view === 'start' ? ''
    : view === 'questions' && stage === 'awaiting_answers' ? 'Freeze the spec'
    : view === 'questions' ? 'Approve and build'
    : '';
  if (!label) return '';
  const why = {
    'Send to the factory': 'Intake branches a worktree and runs the scout and the interrogator.',
    'Freeze the spec': 'Freezing closes spec review. Unanswered questions are carried into the spec '
      + 'as accepted risk, and named there.',
    'Approve and build': 'Approving starts the build. There is no further input until it '
      + 'produces a packet.',
  }[label];
  return `<button class="btn btn-primary btn-sm" id="bar-action" title="${esc(why)}"
            ${barActionOn ? '' : 'disabled'}>${esc(label)}</button>`;
}

function setBar(html) {
  if (!featureBarEl) return;
  // Same reason `#main` holds still: the bar carries most of the armed
  // controls, and a poll landing between the two presses would cancel them.
  if (featureBarEl.querySelector('[data-armed="1"]')) return;
  featureBarEl.innerHTML = html;
}

function renderFeatureBar() {
  if (!featureBarEl) return;
  const view = mountedView();
  if (view === 'start') {
    // No feature yet, so the bar belongs to the project -- the same one gate 0
    // draws, with this screen's action and the project's own beside it. The
    // baseline is the one that has to be here: a gate list changes, and the
    // thing that proves the new list on untouched code should not be somewhere
    // you can only reach by breaking the project.
    const p = state.project.project;
    setBar(projectBar(p, { actions: `
      <button class="btn btn-sm" id="run-baseline"
        title="Runs every check on an untouched checkout of ${esc(p.base_ref || 'the base branch')}, in this project's environment. No model calls. Proves the list before a feature is judged by it."
        >Run checks</button>
      ${barAction(view)}` }));
    return;
  }
  if (!state.data) { setBar(''); return; }
  const d = state.data;
  const settled = ['accepted', 'rejected'].includes(d.state.stage);
  const running = d.state.stage !== 'awaiting_verdict';
  const PACKET_VIEWS = ['calls', 'map', 'evidence', 'review', 'objections',
                        'repairs', 'rework', 'blindspots', 'file', 'dependencies'];
  const actions = PACKET_VIEWS.includes(view) && d.packet
    ? `<button class="btn btn-quiet btn-sm" id="revalidate" ${running ? 'disabled' : ''}
         title="Measures this branch again from zero: the checks, the breaker, the panel and the finding ledger start from nothing. The code is not touched and the packet you are reading is kept. It takes as long as the last run and comes out of the same rework budget."
         >Verify again</button>
       <button class="btn btn-quiet btn-sm" id="rebuild" ${d.state.stage === 'building' ? 'disabled' : ''}
         title="${REBUILD_TITLE}"
         >Build again</button>
       ${settled ? '' : `<a class="btn btn-primary btn-sm"
         href="${featureHref(d.state.project_id, d.state.feature_id)}?packet#cord"
         title="The ruling lives with the packet, next to the findings and blockers it accepts."
         >Rule on this packet</a>`}`
    : barAction(view);
  setBar(featureBar(d, { actions }));
}

function mountGate2() {
  if (gate2) { gate2.mount(main, gate2Props()); return; }
  if (gate2Loading) return;
  main.innerHTML = '<div class="wrap"><p class="empty">Loading…</p></div>';
  gate2Loading = import('/static/ui/index.js').then((mod) => {
    gate2 = mod;
    gate2Loading = null;
    if (!gate2Active()) return;
    mod.mount(main, gate2Props());
    // The screen that carries the reading-mode button only exists now.
    syncHeader();
  }).catch((e) => {
    gate2Loading = null;
    main.innerHTML = `<div class="wrap"><p class="empty">${esc(e.message)}</p></div>`;
  });
}

function releaseGate2() {
  if (gate2 && gate2.isMounted()) gate2.unmount();
}

let sectionSpy = null;

function watchSections() {
  if (sectionSpy) { sectionSpy.disconnect(); sectionSpy = null; }
  const items = [...main.querySelectorAll('.toc-item')];
  if (!items.length) return;
  const heads = items.map((a) => main.querySelector(`[id="${a.getAttribute('href').slice(1)}"]`));
  sectionSpy = new IntersectionObserver((entries) => {
    entries.forEach((en) => {
      if (!en.isIntersecting) return;
      const i = heads.indexOf(en.target);
      if (i < 0) return;
      items.forEach((x) => x.classList.remove('on'));
      items[i].classList.add('on');
    });
  }, { rootMargin: '-120px 0px -70% 0px' });
  heads.forEach((h) => h && sectionSpy.observe(h));
}

/* Two rates, because the fast one is an optimisation and the slow one is the
   safety net.

   The event stream is what normally keeps this page current, and the 4s poll
   rides alongside it while something is known to be running. Neither survives
   the stream dropping -- a server restart, a laptop waking, a proxy timing out
   -- and when it drops on a stage the client believes is terminal, nothing ever
   starts again: the page sits on `failed` while the feature builds. So there is
   always a slow poll whenever a feature is open, whatever the client thinks its
   stage is. */
const FAST_POLL_MS = 4000;
const SLOW_POLL_MS = 15000;

function managePolling() {
  const midIntake = state.data && state.data.state.stage === 'awaiting_answers'
    && !state.data.interrogation;
  /* Scoped to what is on screen. Asking the whole floor would poll every
     project's page every four seconds whenever any one of them builds -- and a
     build that died days ago, still recorded as `building` because nothing
     closes a run the process did not survive, would do it forever. Every one
     of those polls rebuilds `#main`, and a rebuild closes any section the
     reader has opened.

     Nothing is lost by narrowing it: the progress stream pushes every event
     from everywhere already, and this interval is the fallback for when that
     stream is not connected. The yard is the exception -- looking at no project
     in particular means looking at all of them. */
  const here = (f) => !state.projectId || f.project_id === state.projectId;
  const running = midIntake
    || (state.data && state.data.state.stage === 'writing_spec')
    || state.allFeatures.some((f) => here(f) && !f.orphaned
                                     && ['building', 'intake', 'writing_spec'].includes(f.stage))
    || state.projects.some((p) => (!state.projectId || p.project_id === state.projectId)
                                  && p.stage === 'surveying');
  /* Setup measures things no event announces -- a build, an install, Docker
     coming up -- so it polls fast while one of its jobs runs, and slowly
     otherwise to notice a sign-in done in a terminal. */
  const setupOpen = state.view === 'setup' && !state.projectId;
  const wanted = state.editing ? 0 : (running || (setupOpen && state.setup && state.setup.busy)) ? FAST_POLL_MS
    : (state.projectId && !state.projectError) || setupOpen ? SLOW_POLL_MS : 0;

  if (state.pollEvery === wanted) return;
  if (state.poller) { clearInterval(state.poller); state.poller = null; }
  state.pollEvery = wanted;
  if (!wanted) return;
  state.poller = setInterval(pollTick, wanted);
}

/* The poll is the fallback for what the progress stream does not say -- an
   agent call inside a phase, a change made in another tab -- so a tick that
   lands just after the stream already refreshed the page asks for nothing it
   does not have. Nor does a tab nobody is looking at: it catches up the moment
   it is shown again. */
function pollTick() {
  if (document.hidden) return;
  if (Date.now() - (state.lastRefreshAt || 0) < state.pollEvery * 0.75) return;
  if (state.view === 'setup' && !state.projectId) { suRefresh(); return; }
  state.id ? refresh(true) : refreshProject(true);
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && state.pollEvery) {
    state.id ? refresh(true) : refreshProject(true);
  }
});
