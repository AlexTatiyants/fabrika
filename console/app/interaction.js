/* Every press in the pane, through one delegated handler; the busy overlay
   a long call puts up; and the calls a project's buttons make. */

'use strict';

/* ----------------------------------------------------------- interaction */

/* Named, because the feature bar lives outside #main and needs the same
   delegated handler: the verdict buttons are in the bar. */
/* Every button in the pane goes through here. Unwrapped, a handler that throws
   does it into an unhandled rejection: the press looks exactly like a press
   that was never wired, and costs a session to diagnose. So the dispatch is
   wrapped rather than each branch guarded -- a branch added later is covered
   without anybody remembering to cover it. */
async function onMainClick(event) {
  try {
    await dispatchMainClick(event);
  } catch (e) {
    console.error('click handler failed', e);
    errorToast(`That button failed: ${e.message}`);
  }
}

async function dispatchMainClick(event) {
  const target = event.target.closest('button');
  if (!target) return;

  // A project group opening or closing is not a request in flight, so it is
  // exempt from the busy guard below -- and it never touches `state`, so a
  // full render() would only cost a rebuild for nothing.
  if (target.dataset.pgToggle) {
    const id = target.dataset.pgToggle;
    const open = !panelOpen.get(id);
    panelOpen.set(id, open);
    // Pressed by hand, so it is worth remembering -- including a close, which
    // is how you tell the rail to stop opening a noisy project for you.
    panelChoice.set(id, open);
    savePanelPrefs();
    const group = target.closest('.panel-group');
    if (group) group.classList.toggle('open', open);
    target.setAttribute('aria-expanded', String(open));
    target.setAttribute('aria-label', `${open ? 'Collapse' : 'Expand'} ${group?.dataset.display || id}`);
    return;
  }
  // A press during a call in flight is dropped on purpose -- two of these
  // running at once write over each other's result. Saying so matters: the
  // overlay is positioned over the pane and a reader scrolled away from it
  // sees an unresponsive page and no reason for it.
  // Scoped the same way the overlay is: a re-survey running on one project must
  // not deaden the buttons on another. Said out loud, because the overlay is
  // over the pane and a reader scrolled past it sees a page that just stopped.
  if (inFlight.has(busyScope())) {
    toast('Something is already running here. Wait for it to finish.');
    return;
  }

  // -- agent configuration
  if (target.dataset.ac && state.ac) return agentConfigClick(target, event);
  // -- setup
  if (target.dataset.su && state.view === 'setup') return setupClick(target);

  // -- yard and project
  if (target.id === 'add-project') return createProject();
  if (target.id === 'add-agent') return addReviewAgent();
  if (target.id === 'toggle-provider') {
    state.providerOpen = !state.providerOpen;
    state.providerTest = null;
    render();
    return;
  }
  // The key behind the api account, reached from that account's own card. The
  // provider card is the one place it can be edited, so this opens it rather
  // than growing a second field that writes the same file.
  if (target.dataset.openProvider) {
    state.providerOpen = true;
    state.providerTest = null;
    render();
    const card = document.getElementById('provider-key');
    if (card) card.scrollIntoView({ block: 'center', behavior: 'smooth' });
    return;
  }
  if (target.id === 'save-provider') return saveProvider();
  if (target.id === 'test-provider') return testProvider();

  // -- control room
  // A preset is a shortcut into the field, not a second control: it writes
  // that editor's template into the input and marks itself, and Save still
  // reads the field itself -- so a hand-typed template is never overruled by
  // whichever chip happened to be lit last.
  if (target.classList.contains('editor-chip')) {
    const field = document.getElementById('editor-url-field');
    if (target.dataset.template && field) field.value = target.dataset.template;
    target.parentElement.querySelectorAll('.editor-chip')
      .forEach((b) => b.classList.toggle('on', b === target));
    return;
  }
  if (target.id === 'save-editor') return saveEditorUrl();
  if (target.dataset.openRole) {
    const name = target.dataset.openRole;
    state.openRole = state.openRole === name ? null : name;
    render();
    return;
  }
  if (target.dataset.closeSheet) return closeSheet();
  /* Reading becomes editing in place. No render: a rebuild here would throw
     away the very thing the press asked for, which is this field, focused. */
  if (target.dataset.cfgOpen) {
    const cell = target.closest('.cfg');
    const field = cell && cell.querySelector('.answer-field');
    if (!field) return;
    /* The chip is sized by its own text. Measured here, at the moment of the
       press, so the field that replaces it is exactly as wide as the thing it
       replaced -- which is how the rail holds still without every chip being
       padded out to some width guessed in advance. A floor, because "0.2" is
       not a usable number field. */
    const w = Math.max(cell.getBoundingClientRect().width, 96);
    cell.style.width = `${Math.round(w)}px`;
    cell.classList.add('editing');
    field.focus();
    if (field.select) { try { field.select(); } catch (e) { /* selects cannot */ } }
    return;
  }
  if (target.dataset.cfgFlag) return toggleFlag(target);
  if (target.dataset.jumpLine) return jumpToPromptLine(Number(target.dataset.jumpLine));
  if (target.dataset.models) {
    const name = target.dataset.models;
    state.openModels = { ...state.openModels, [name]: !state.openModels[name] };
    return render();
  }
  if (target.dataset.checkRoute) return checkRoute(target.dataset.checkRoute);
  if (target.dataset.applyCleanup) return applyCleanup(target.dataset.applyCleanup);
  if (target.dataset.costInfo) { state.costInfo = !state.costInfo; return render(); }
  if (target.dataset.reopenCleanup) return applyCleanup(target.dataset.reopenCleanup, true);
  if (target.dataset.egressStart) return startEgress();
  if (target.dataset.egressRefresh) return refreshEgress();
  if (target.dataset.egressTest) return testEgress();
  if (target.dataset.openRoute) {
    const name = target.dataset.openRoute;
    state.openRoute = state.openRoute === name ? null : name;
    render();
    return;
  }
  if (target.dataset.copy !== undefined) {
    // The one line a human has to run in a terminal, because none of these
    // tools can be signed into from a web page.
    navigator.clipboard.writeText(target.dataset.copy).then(
      () => toast('Copied. Run it in a terminal, then press Check.'),
      () => toast(target.dataset.copy));
    return;
  }
  if (target.dataset.saveRole) return saveRole(target.dataset.saveRole);
  if (target.dataset.deleteRole) return deleteReviewAgent(target.dataset.deleteRole);
  if (target.dataset.editTab) {
    state.editing = target.dataset.editTab;
    startDraft();
    return render();
  }
  if (target.dataset.editCancel) { state.editing = null; state.draft = null; return render(); }
  if (target.dataset.editSave) return saveEdits();
  if (target.dataset.moveDockerfile) return moveDockerfile();
  if (target.dataset.retireDockerfile) return retireDockerfile();
  if (target.dataset.historyFilter !== undefined) {
    state.historyFilter = target.dataset.historyFilter;
    return render();
  }
  if (target.dataset.historyRecord) {
    const seq = Number(target.dataset.historyRecord);
    if (state.historyRecord && state.historyRecord.seq === seq) { state.historyRecord = null; return render(); }
    return api(`${projectUrl(state.projectId)}/log/${seq}`)
      .then((r) => { state.historyRecord = r; render(); }).catch((err) => errorToast(err.message));
  }
  if (target.id === 'add-gate') {
    state.draft.gates.push({ name: '', command: '', timeout_s: 600, parse_metric: '', threshold: null, optional: false });
    render();
    return;
  }
  if (target.dataset.removeGate !== undefined) {
    state.draft.gates.splice(Number(target.dataset.removeGate), 1);
    render();
    return;
  }
  if (target.id === 'run-baseline') return runBaseline();
  if (target.dataset.writeScaffold) return writeScaffolding([target.dataset.writeScaffold]);
  if (target.dataset.scaffoldDiff) return showScaffoldDiff(target, target.dataset.scaffoldDiff);
  if (target.dataset.replaceScaffold) {
    const path = target.dataset.replaceScaffold;
    return writeScaffolding([path], { reread: true, replace: [path] });
  }
  /* A reading's own recommendation, before it is applied: a cleanup fix that
     needs code is offered as this prompt, for the person's own agent. */
  if (target.dataset.copyDiffRec) {
    const r = ((((state.resurvey || {}).diff) || {}).recommendations || [])
      .find((x) => x.title === target.dataset.copyDiffRec);
    if (r && navigator.clipboard) {
      navigator.clipboard.writeText(recPrompt(r)).then(
        () => toast('Prompt copied. Give it to your coding agent in that repository, then re-survey.'),
        () => errorToast('Could not copy.'));
    }
    return;
  }
  if (target.dataset.copyRec) {
    const r = liveRecommendations().find((x) => x.key === target.dataset.copyRec);
    if (r && navigator.clipboard) {
      navigator.clipboard.writeText(recPrompt(r)).then(
        () => toast('Prompt copied. Paste it into a session in that repository.'),
        () => errorToast('Could not copy.'));
    }
    return;
  }
  if (target.dataset.dropGate) { state.droppingGate = target.dataset.dropGate; render(); return; }
  if (target.dataset.checkRuns) return setCheckRuns(target.dataset.checkRuns, target.dataset.runs);
  if (target.dataset.reinstateGate) return reinstateGate(target.dataset.reinstateGate);
  /* One toggle for anything a row can show inline: a check's captured output,
     a file the reading is offering to write. Both are `<pre>` beside the
     buttons, hidden until asked for -- nothing on this page opens a panel or
     moves the reader to another screen to read one thing. */
  if (target.dataset.showOutput || target.dataset.showFile) {
    const key = target.dataset.showOutput ? `out:${target.dataset.showOutput}`
                                          : target.dataset.showFile;
    const pre = main.querySelector(`[data-file-for="${CSS.escape(key)}"]`);
    if (pre) {
      pre.hidden = !pre.hidden;
      target.textContent = pre.hidden
        ? (target.dataset.showOutput ? 'Show the output' : 'Show the file') : 'Hide it';
    }
    return;
  }
  if (target.dataset.adoptRec) {
    state.adoptingRec = target.dataset.adoptRec; state.decliningRec = null; render(); return;
  }
  if (target.id === 'cancel-adopt-rec') { state.adoptingRec = null; render(); return; }
  if (target.dataset.confirmAdoptRec) return adoptRecommendation(target.dataset.confirmAdoptRec);
  if (target.dataset.declineRec) { state.decliningRec = target.dataset.declineRec; render(); return; }
  if (target.id === 'cancel-decline-rec') { state.decliningRec = null; render(); return; }
  if (target.dataset.confirmDeclineRec) return declineRecommendation(target.dataset.confirmDeclineRec);
  if (target.dataset.reinstateRec) return reinstateRecommendation(target.dataset.reinstateRec);
  if (target.dataset.ratchet) return ratchetCheck(target.dataset.ratchet);
  if (target.dataset.guideReview) { state.openGuide = target.dataset.guideReview; render(); return; }
  if (target.dataset.guideClose) { closeGuideSheet(); return; }
  if (target.dataset.guideFile) {
    const ed = (state.guideEdits || {})[state.openGuide];
    if (ed) { ed.at = Number(target.dataset.guideFile); render(); }
    return;
  }
  if (target.dataset.guideCommit) return decideGuide(target.dataset.guideCommit, 'write');
  if (target.dataset.guideDecline) return decideGuide(target.dataset.guideDecline, 'decline');
  if (target.dataset.skillsDir) {
    const folder = target.dataset.skillsDir;
    return withBusy('Recording that.', async () => {
      await api(`${projectUrl(state.projectId)}/guides/skills-dir`, {
        method: 'POST', body: JSON.stringify({ folder }),
      });
      await refreshProject();
      toast(`Fabrika writes skills into ${folder}/.`);
    });
  }
  if (target.dataset.dxApply) return applyDiagnosisFix(target.dataset.dxApply, Number(target.dataset.dxFix));
  if (target.dataset.dxShow) {
    const pre = main.querySelector(`[data-dx-for="${CSS.escape(target.dataset.dxShow)}"]`);
    if (pre) pre.hidden = !pre.hidden;
    return;
  }
  if (target.dataset.dxCopy) {
    const [name, i] = target.dataset.dxCopy.split(/:(?=\d+$)/);
    const fix = ((diagnosisOf({ name }) || {}).fixes || [])[Number(i)] || {};
    return navigator.clipboard.writeText(fix.prompt || '').then(
      () => toast('Copied. Give it to your coding agent, then run the checks again.'),
      () => toast('Could not copy; open it with Show it.'));
  }
  if (target.dataset.ruleCheck) return ruleOnCheck(target.dataset.ruleCheck, target.dataset.accept === '1');
  if (target.dataset.undoCheck) return undoCheck(target.dataset.undoCheck);
  if (target.id === 'edit-policy') { state.editingPolicy = true; render(); return; }
  if (target.id === 'cancel-policy') { state.editingPolicy = false; render(); return; }
  if (target.id === 'save-policy') return savePolicy();
  if (target.dataset.policyOn) return savePolicy(target.dataset.policyOn === '1');
  if (target.dataset.newCode) {
    const from = target.dataset.limitFrom && document.getElementById(target.dataset.limitFrom);
    const limit = from ? from.value.trim() : target.dataset.limit;
    if (from && limit === '') { errorToast('Type the floor, as a percentage.'); return; }
    return holdNewCode(target.dataset.newCode, limit);
  }
  if (target.dataset.declineFamily) {
    state.decliningFamily = target.dataset.declineFamily; render(); return;
  }
  if (target.id === 'cancel-decline-family') { state.decliningFamily = null; render(); return; }
  if (target.dataset.confirmDeclineFamily) return declineFamily(target.dataset.confirmDeclineFamily);
  if (target.dataset.reinstateFamily) return reinstateFamily(target.dataset.reinstateFamily);
  if (target.id === 'cancel-drop-gate') { state.droppingGate = null; render(); return; }
  if (target.id === 'confirm-drop-gate') return dropGate(state.droppingGate);
  if (target.id === 'approve-project') return approveProject();
  if (target.id === 'resurvey') return askResurvey();
  if (target.id === 'propose-changes') return askProposeChanges();
  if (target.dataset.rulePart) return rulePart(target);
  if (target.dataset.undoPart) return undoCheck(target.dataset.undoPart);

  // -- bench
  if (target.id === 'bar-action') {
    document.dispatchEvent(new CustomEvent('ui:action'));
    return;
  }

  // -- interrogator
  if (target.dataset.opt !== undefined && target.dataset.q) {
    state.answers[target.dataset.q] = target.dataset.opt;
    state.openQuestion = null;
    render();
    return;
  }
  if (target.dataset.default) {
    state.answers[target.dataset.default] = target.dataset.opt;
    state.openQuestion = null;
    render();
    return;
  }
  if (target.id === 'toggle-wait') { state.hideWait = !state.hideWait; render(); return; }

  if (target.dataset.run !== undefined && target.closest('.run-links')) {
    const seq = Number(target.dataset.run);
    const runs = runsOf(state.log);
    // The latest is the default rather than a pick, so a new run started while
    // the page is open is the one shown.
    state.runSeq = seq === runs[runs.length - 1].seq ? null : seq;
    render();
    return;
  }
  if (target.id === 'strip-toggle') { setStripOpen(!stripOpen()); render(); return; }
  if (target.dataset.docTab) {
    const [sec, tab] = target.dataset.docTab.split(':');
    state.docTab = { ...state.docTab, [sec]: tab };
    render();
    return;
  }
  if (target.dataset.openRecord) {
    const seq = Number(target.dataset.openRecord);
    if (state.openRecord === seq) { state.openRecord = null; state.record = null; render(); return; }
    state.openRecord = seq;
    state.record = null;
    render();
    api(state.id
      ? `${featureUrl(state.projectId, state.id)}/log/${seq}`
      : `/api/projects/${encodeURIComponent(state.projectId)}/log/${seq}`)
      .then((rec) => { state.record = rec; render(); })
      .catch((e) => errorToast(e.message));
    return;
  }
  if (target.dataset.openCriterion) {
    state.openCriterion = state.openCriterion === target.dataset.openCriterion
      ? null : target.dataset.openCriterion;
    render();
    return;
  }
  if (target.dataset.openQuestion) {
    state.openQuestion = state.openQuestion === target.dataset.openQuestion
      ? null : target.dataset.openQuestion;
    render();
    return;
  }
  if (target.dataset.step) {
    state.openSteps = state.openSteps || new Set();
    const key = target.dataset.step;
    if (state.openSteps.has(key)) state.openSteps.delete(key); else state.openSteps.add(key);
    render();
    return;
  }
  if (target.dataset.callRaw) {
    state.openCalls = state.openCalls || new Set();
    const seq = Number(target.dataset.callRaw);
    if (state.openCalls.has(seq)) state.openCalls.delete(seq); else state.openCalls.add(seq);
    render();
    return;
  }
  if (target.id === 'start-correction') {
    state.correcting = true; state.reintaking = false; render(); return;
  }
  if (target.id === 'start-discard') { state.discarding = true; render(); return; }
  if (target.id === 'cancel-discard') { state.discarding = false; render(); return; }
  if (target.id === 'confirm-discard') return discardFeature();
  if (target.id === 'confirm-discard-project') return discardProject();
  if (target.id === 'start-reintake') {
    state.reintaking = true; state.correcting = false; render();
    if (!state.intakePlan) loadIntakePlan();
    return;
  }
  if (target.id === 'cancel-reintake') { state.reintaking = false; render(); return; }
  if (target.id === 'send-reintake') return sendReintake();
  if (target.id === 'cancel-correction') { state.correcting = false; render(); return; }
  if (target.id === 'send-correction') return sendCorrection();
  if (target.id === 'approve-spec') return approveSpec('now');
  if (target.id === 'approve-at-reset') return approveSpec('at_reset');
  if (target.dataset.cut) return ruleOnCut(target.dataset.cut);

  // -- review
  if (target.dataset.class) {
    state.openClass = state.openClass === target.dataset.class ? null : target.dataset.class;
    render();
    const seg = main.querySelector(`[data-class="${CSS.escape(target.dataset.class)}"]`);
    if (seg) seg.focus();
    return;
  }
  if (target.dataset.rule) return rule(target.dataset.id, target.dataset.rule);
  if (target.id === 'verdict-accept') return showConsequence('accepted');
  if (target.id === 'verdict-reject') return showConsequence('rejected');
  if (target.id === 'verdict-go') return verdict(target.dataset.value);
  if (target.id === 'verdict-cancel') return hideConsequence();
  if (target.id === 'revalidate') return askRevalidate();
  if (target.id === 'rebuild') return askRebuild();
  if (target.id === 'reintake-stalled') {
    return armed(target, 'Press again — reads the repository from scratch and asks again')
      ? sendReintake() : undefined;
  }
  if (target.id && target.id.startsWith('retry-build')) return askRetryBuild();
  if (target.classList.contains('act-discard')) return armDiscard(target);
}

main.addEventListener('click', onMainClick);
if (featureBarEl) featureBarEl.addEventListener('click', onMainClick);
// The rail is anchors, which need no handler -- except that it carries a
// button. Without this the discard on a row arms nothing and does nothing,
// which is the safe failure and still a broken control.
if (panelBody) panelBody.addEventListener('click', onMainClick);

/* The mounted screen owns the state; the bar owns the button. */
document.addEventListener('ui:action-state', (event) => {
  barActionOn = !!event.detail;
  const btn = featureBarEl && featureBarEl.querySelector('#bar-action');
  if (btn) btn.disabled = !barActionOn;
});

/* The router owns the hash, so an in-page anchor would navigate away from the
   feature entirely. The link keeps its href for keyboard and middle-click; the
   scroll is ours. */
main.addEventListener('mouseover', (event) => {
  const map = event.target.closest('.cmap');
  const node = event.target.closest('.n[data-id]');
  if (!map) return;
  map.querySelectorAll('.on').forEach((x) => x.classList.remove('on'));
  if (!node) { map.classList.remove('lit'); return; }
  const id = node.dataset.id;
  const near = new Set([id]);
  map.querySelectorAll('.edge').forEach((p) => {
    const on = p.dataset.a === id || p.dataset.b === id;
    p.classList.toggle('on', on);
    if (on) { near.add(p.dataset.a); near.add(p.dataset.b); }
  });
  map.querySelectorAll('.n').forEach((m) => m.classList.toggle('on', near.has(m.dataset.id)));
  map.classList.add('lit');
});

main.addEventListener('mouseout', (event) => {
  const map = event.target.closest('.cmap');
  if (!map || map.contains(event.relatedTarget)) return;
  map.classList.remove('lit');
  map.querySelectorAll('.on').forEach((x) => x.classList.remove('on'));
});

main.addEventListener('click', (event) => {
  const jump = event.target.closest('.toc-item');
  if (jump) {
    const target = main.querySelector(`[id="${jump.getAttribute('href').slice(1)}"]`);
    if (target) {
      event.preventDefault();
      target.scrollIntoView({ block: 'start', behavior: 'smooth' });
      main.querySelectorAll('.toc-item').forEach((x) => x.classList.remove('on'));
      jump.classList.add('on');
    }
    return;
  }

  const node = event.target.closest('[data-node]');
  if (!node || !state.roles) return;
  const name = node.dataset.node;
  if (!state.roles.roles.some((r) => r.name === name)) return;
  // The details open in a sheet over the page, so the page stays where it is:
  // scrolling it to the agent's row would move the diagram out from under the
  // reader, and closing the sheet would then leave them somewhere they never went.
  state.openRole = state.openRole === name ? null : name;
  render();
});

main.addEventListener('input', (event) => {
  // Kept across the repaint a poll makes while the ruling is being written.
  if (event.target.id === 'cut-note') { state.cutNote = event.target.value; return; }
  const answer = event.target.closest('[data-answer]');
  if (answer) {
    state.answers[answer.dataset.answer] = answer.value;
    return;
  }
  const role = event.target.closest('[data-role]');
  if (role && state.roleDraft[role.dataset.role]) {
    const d = state.roleDraft[role.dataset.role];
    if (role.dataset.key === 'model_route') {
      // One control, two fields. A model and the route that carries it are one
      // choice -- "Opus 5 via Claude Code" is not the same thing as "Claude
      // Opus 5 via OpenRouter", and picking them separately invites the pair
      // that does not exist.
      const cut = String(role.value).indexOf('|');
      d.route = String(role.value).slice(0, cut);
      d.model = String(role.value).slice(cut + 1);
      render();
      return;
    }
    d[role.dataset.key] = role.type === 'checkbox' ? role.checked : role.value;
    return;
  }
  const draft = event.target.closest('[data-draft]');
  if (draft && state.draft) {
    // Bound without re-rendering, or every keystroke would lose the caret.
    const path = draft.dataset.draft;
    let value = draft.type === 'checkbox' ? draft.checked : draft.value;
    if (draft.type === 'number') value = value === '' ? null : Number(value);
    if (path === 'environment.setup' || path === 'environment.test_prepare') {
      value = String(value).split('\n').map((x) => x.trim()).filter(Boolean);
    }
    setDraft(path, value);
  }
});

main.addEventListener('change', (event) => {
  if (state.ac && agentConfigChange(event.target)) return;
  const draft = event.target.closest('[data-draft][data-rerender]');
  if (!draft || !state.draft) return;
  setDraft(draft.dataset.draft, draft.value);
  render();
});

main.addEventListener('dragstart', acDragStart);
main.addEventListener('dragover', acDragOver);
main.addEventListener('drop', acDrop);
main.addEventListener('dragend', acDragEnd);

main.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && state.ac && state.ac.sel.length) {
    state.ac.sel = [];
    render();
    return;
  }
  if (event.key === 'Enter' && (event.target.id === 'project-path' || event.target.id === 'project-name')) {
    event.preventDefault();
    createProject();
  }
});



/* A button that is doing something says so, on itself.

   A toast is gone in under four seconds. The re-survey behind "What should
   change?" is a model reading a repository: it runs for the better part of a
   minute, and with only a toast, for fifty of those seconds the page looks
   exactly like a page where nothing has been pressed. So the control
   holds the state instead of a toast: disabled, relabelled, restored in a
   `finally` whether the call worked or threw. */
function working(button, label) {
  if (!button) return () => {};
  const was = button.innerHTML;
  const wasDisabled = button.disabled;
  button.disabled = true;
  button.classList.add('btn-working');
  button.innerHTML = esc(label);
  return () => {
    button.innerHTML = was;
    button.disabled = wasDisabled;
    button.classList.remove('btn-working');
  };
}

/* A long call, said out loud for as long as it lasts.

   A toast clears after under four seconds, which is honest only for a call
   that takes one -- and a re-survey reads the verify lane's whole ledger as
   well as the repository and runs for ten minutes or more. For most of that a
   toast leaves the page saying nothing at all, and a reader with no signal
   reasonably concludes the click did not land and clicks again, which is a
   second model call nobody wanted.

   Elapsed time rather than a percentage. Nothing here knows how long a model
   will take, and a bar that fills at a rate somebody guessed is a worse lie than
   no bar: a counter that keeps moving says the one true thing, which is that
   this is still alive. */
const busyEl = (() => {
  const el = document.createElement('div');
  el.className = 'busy-overlay';
  el.hidden = true;
  el.setAttribute('role', 'status');
  el.setAttribute('aria-live', 'polite');
  el.innerHTML = `
    <div class="busy-card">
      <div class="busy-bar"><i></i></div>
      <p class="busy-what"></p>
      <p class="busy-meta"><span class="busy-clock">0:00</span><span class="busy-note"></span></p>
    </div>`;
  // Kept in the body and *positioned* over the pane, rather than parented to it.
  // Inside `main` it would live exactly until the next render, which replaces
  // that element's contents -- and a poll during a ten-minute call renders.
  document.body.appendChild(el);
  return el;
})();

/* A call belongs to a project, so the overlay does too.

   One boolean for the whole console would be the wrong scope: start a
   ten-minute re-survey on one project, switch to another, and that project's
   pane would come up dimmed and locked with the first one's clock ticking over
   it -- and the reader most likely to look at a second project is exactly the
   one waiting ten minutes on the first.

   Keyed by project id, with the yard and settings under ''. A count rather than
   a flag, because two calls can be in flight at once on different projects
   and the first to finish must not clear the second. */
const inFlight = new Map();

function busyScope() { return state.projectId || ''; }

const busyClock = { timer: null, started: 0 };

/* Over the pane the call belongs to, never the whole window: the floor beside
   it still lists every other project and feature. */
function coverBusy() {
  const box = main.getBoundingClientRect();
  Object.assign(busyEl.style, {
    left: `${Math.max(0, box.left)}px`,
    top: `${Math.max(0, box.top)}px`,
    width: `${box.width}px`,
    height: `${Math.max(0, window.innerHeight - Math.max(0, box.top))}px`,
  });
}

/* Derived, never set. Whether the overlay belongs on screen is a question about
   where the reader is standing, and it is asked again on every render -- so
   navigating away hides it and navigating back brings it up with the clock
   still counting from when the call actually started. */
function paintBusy() {
  const call = inFlight.get(busyScope());
  if (!call) {
    clearInterval(busyClock.timer);
    busyClock.timer = null;
    busyEl.hidden = true;
    main.removeAttribute('aria-busy');
    return;
  }
  const tick = () => {
    const secs = Math.round((Date.now() - call.started) / 1000);
    busyEl.querySelector('.busy-clock').textContent =
      `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
  };
  busyEl.querySelector('.busy-what').textContent = call.label;
  busyEl.querySelector('.busy-note').textContent = call.note ? ` · ${call.note}` : '';
  tick();
  coverBusy();
  busyEl.hidden = false;
  main.setAttribute('aria-busy', 'true');
  // Again on the next frame. `coverBusy` reads the pane's box, and immediately
  // after a render that box has not been laid out yet -- so the first paint
  // spans the full width and dims the floor for a moment before the tick below
  // corrects it. One frame later the layout is settled and the first thing
  // anybody sees is already in the right place.
  requestAnimationFrame(coverBusy);
  if (!busyClock.timer) {
    busyClock.timer = setInterval(() => { tick(); coverBusy(); }, 1000);
  }
}

// Registered once. Per-call listeners leaked one handler per re-survey.
window.addEventListener('resize', () => { if (!busyEl.hidden) coverBusy(); });

async function withBusy(label, fn, note = '') {
  const scope = busyScope();
  const call = inFlight.get(scope) || { label, note, started: Date.now(), n: 0 };
  // The newest label wins: if two calls somehow overlap on one project, the
  // thing a reader was told last is the thing they just pressed.
  Object.assign(call, { label, note, n: call.n + 1 });
  inFlight.set(scope, call);
  paintBusy();
  try {
    // What the press produced, for a caller that does something after it --
    // a run of the checks once a fix is in. Undefined when it failed.
    return await fn();
  } catch (e) {
    errorToast(e.message);
    return undefined;
  } finally {
    call.n -= 1;
    if (call.n <= 0) inFlight.delete(scope);
    // From the scope the reader is in *now*, not the one the call started in.
    paintBusy();
  }
}

function createProject() {
  const path = (document.getElementById('project-path') || {}).value || '';
  const name = (document.getElementById('project-name') || {}).value || '';
  if (!path.trim()) { toast('Give it a directory first.'); return; }
  return withBusy('Surveying. Reading the repository, then running its checks on a clean checkout.', async () => {
    const created = await api('/api/projects', {
      method: 'POST', body: JSON.stringify({ path, name }),
    });
    location.hash = `#/${encodeURIComponent(created.project_id)}`;
  });
}

/* Removing a check from the gate 0 screen, without a detour through the
   settings form. The API takes the whole list, so this sends the list minus
   one -- and `gates` is in INVALIDATES_BASELINE, so the server clears the
   baseline and puts the project back to awaiting_approval on its own. The
   confirm above says so before the press. */
/* Not a PATCH of the gate list.

   Dropping a check by writing a shorter list leaves no trace of the decision,
   so the next reading proposes it again from evidence that never leaves the
   repository -- a script in package.json, a step in CI -- and so does every
   reading after that. The endpoint records the name, the command and
   the reason, and nothing may propose it again until it is put back. */
function setCheckRuns(name, runs) {
  return withBusy(`Changing when ${name} runs.`, async () => {
    await api(`${projectUrl(state.projectId)}/checks/runs`, {
      method: 'POST', body: JSON.stringify({ name, runs }),
    });
    await refreshProject();
  });
}

function dropGate(name) {
  if (!name) return undefined;
  const reason = ((document.getElementById('drop-reason') || {}).value || '').trim();
  return withBusy(`Declining ${name}.`, async () => {
    await api(`${projectUrl(state.projectId)}/gates/decline`, {
      method: 'POST', body: JSON.stringify({ name, reason }),
    });
    state.droppingGate = null;
    await refreshProject();
    toast(`${name} is off the list, and no reading will propose it again.`);
  });
}

/* Everything additive, as one feature.

   Doing them one at a time is four branches, four reviews and four baselines,
   three of which measure a repository that is about to change again -- and the
   items interact: adding a test script to CI and adding a browser runner edit
   the same workflow file.

   Deliberately one intent rather than a queue of presses. The files go through
   the pipeline with the rest instead of being written directly, because "as one
   build" has to mean one branch a human reads end to end; writing half of it
   outside the branch would make the review describe less than what landed.

   A red check is never in here. It has more than one right answer and a batch
   cannot pick one. */
/* A build needs an approved project, and something has to say so.

   `Build it` seeds an intent and navigates to the project, whose landing page
   is Checks. On a project that is not approved yet, that is the screen the
   reader is already on, and unless something says why, the intent is dropped
   on the floor -- a button that appears to do nothing. */

function adoptRecommendation(key) {
  const name = ((document.getElementById('adopt-name') || {}).value || '').trim();
  const command = ((document.getElementById('adopt-cmd') || {}).value || '').trim();
  if (!name) { errorToast('Give the check a name.'); return; }
  return withBusy('Adding it to the check list.', async () => {
    await api(`${projectUrl(state.projectId)}/recommendations/adopt`, {
      method: 'POST', body: JSON.stringify({ key, name, command }),
    });
    state.adoptingRec = null;
    await refreshProject();
    toast(`${name} is on the list. Run the checks to measure it.`);
  });
}

/* A reading writes its recommendations fresh on every run, from repository
   facts that do not change -- so the same three arrive at every reading unless
   the decision is recorded. Third time this project has met that loop. */
function declineRecommendation(key) {
  const reason = ((document.getElementById('rec-reason') || {}).value || '').trim();
  return withBusy('Recording that you turned it down.', async () => {
    await api(`${projectUrl(state.projectId)}/recommendations/decline`, {
      method: 'POST', body: JSON.stringify({ key, reason }),
    });
    state.decliningRec = null;
    await refreshProject();
    toast('Turned down. No reading will suggest it again.');
  });
}

/* The bound is the number the check printed on untouched code -- never typed,
   never estimated. Editing a check is editing the list, so the checks run
   again straight away and the bound is proved before anyone approves it. */
function ratchetCheck(name) {
  return withBusy('Holding it at today\'s reading.', async () => {
    await api(`${projectUrl(state.projectId)}/checks/ratchet`, {
      method: 'POST', body: JSON.stringify({ name }),
    });
    await refreshProject();
    runBaseline();
    toast(`${name} is held at today's reading. Running the checks again to prove it.`);
  });
}

/* One proposed change, ruled on in its row. Accepting edits the list, so the
   checks have to run again before approval -- said, not done, so that a
   person ruling on three rows runs them once. */
function ruleOnCheck(item, accept) {
  return withBusy(accept ? 'Applying the change.' : 'Keeping the check as it is.', async () => {
    await api(`/api/projects/${encodeURIComponent(state.projectId)}/proposal/check`, {
      method: 'POST', body: JSON.stringify({ item, accept }),
    });
    await refreshProject();
    toast(accept ? 'Changed. Run the checks to measure it before you approve.'
      : 'Kept as it is. The same change will not be proposed again.');
  });
}

function undoCheck(item) {
  return withBusy('Putting the check back.', async () => {
    await api(`/api/projects/${encodeURIComponent(state.projectId)}/proposal/check/undo`, {
      method: 'POST', body: JSON.stringify({ item }),
    });
    await refreshProject();
    toast('Put back as it was. The proposed change is waiting again.');
  });
}

function applyDiagnosisFix(name, fix) {
  return withBusy('Applying the fix.', async () => {
    const out = await api(`${projectUrl(state.projectId)}/diagnosis/apply`, {
      method: 'POST', body: JSON.stringify({ check: name, fix }),
    });
    await refreshProject();
    toast(out.commit_problem ? `Written to ${out.path}: ${out.commit_problem}`
      : out.commit ? `Committed ${out.path} (${String(out.commit).slice(0, 7)}). Running the checks.`
      : 'Applied. Running the checks.');
    return true;
  }).then((done) => (done && state.project ? runBaseline() : undefined));
}

function holdNewCode(name, limit) {
  const value = limit === '' ? null : Number(limit);
  return withBusy('Changing what a feature is held to.', async () => {
    await api(`${projectUrl(state.projectId)}/checks/new-code`, {
      method: 'POST', body: JSON.stringify({ name, limit: value }),
    });
    await refreshProject();
    runBaseline();
    const gate = ((state.project.project || {}).gates || []).find((x) => x.name === name) || {};
    toast(value == null ? `${name} no longer holds a feature's own lines.`
      : COVERAGE_REPORTS.includes(gate.report_format)
        ? `${name} now requires ${value}% of a feature's changed lines to run.`
        : `${name} now allows ${value} new finding${value === 1 ? '' : 's'} on a feature's lines.`);
  });
}

function savePolicy(enabled) {
  const p = state.project.project;
  const now = { ...(p.dependency_policy || {}) };
  if (enabled !== undefined) {
    now.enabled = enabled;
  } else {
    const list = (id) => ((document.getElementById(id) || {}).value || '')
      .split(',').map((x) => x.trim()).filter(Boolean);
    now.deny = list('policy-deny');
    now.flag = list('policy-flag');
    now.min_age_days = Math.max(0, parseInt((document.getElementById('policy-age') || {}).value, 10) || 0);
    now.enabled = true;
  }
  return withBusy('Saving the dependency policy.', async () => {
    await api(projectUrl(state.projectId), {
      method: 'PATCH', body: JSON.stringify({ dependency_policy: now }),
    });
    state.editingPolicy = false;
    await refreshProject();
    toast('Saved. It applies from the next round of any build.');
  });
}

/* Families are optional. Declining one blocks nothing; it stops the family's
   suggestions and says, where the family would be, that a person chose this. */
function declineFamily(family) {
  const reason = ((document.getElementById('family-reason') || {}).value || '').trim();
  return withBusy('Recording that this project goes without it.', async () => {
    await api(`${projectUrl(state.projectId)}/families/decline`, {
      method: 'POST', body: JSON.stringify({ family, reason }),
    });
    state.decliningFamily = null;
    await refreshProject();
    toast(`No ${family} check here, and none will be suggested.`);
  });
}

function reinstateFamily(family) {
  return withBusy('Letting it be suggested again.', async () => {
    await api(`${projectUrl(state.projectId)}/families/reinstate`, {
      method: 'POST', body: JSON.stringify({ family }),
    });
    await refreshProject();
    toast(`The next reading can suggest a ${family} check again.`);
  });
}

/* Only lifts the ruling. Unlike a check there is nothing to put back -- a
   suggestion is not stored on the project -- so it reappears when a reading
   makes it again, which it will, from the same facts. */
function reinstateRecommendation(key) {
  return withBusy('Letting it be suggested again.', async () => {
    await api(`${projectUrl(state.projectId)}/recommendations/reinstate`, {
      method: 'POST', body: JSON.stringify({ key }),
    });
    await refreshProject();
    toast('It can be suggested again from the next reading.');
  });
}

/* Declined is not deleted. A project turns a check down because it is not ready
   for it, and there has to be somewhere to find it when it is -- with the
   command it had, so nobody reconstructs it from memory. */
function reinstateGate(name) {
  return withBusy(`Putting ${name} back.`, async () => {
    await api(`${projectUrl(state.projectId)}/gates/reinstate`, {
      method: 'POST', body: JSON.stringify({ name }),
    });
    await refreshProject();
    toast(`${name} is back on the list. Run the checks again before approving.`);
  });
}

function approveProject() {
  return withBusy('Approving.', async () => {
    const out = await api(`/api/projects/${encodeURIComponent(state.projectId)}/approve`, { method: 'POST' });
    await refreshProject();
    const moved = out && out.dockerfile_moved;
    if (moved) {
      toast(moved.commit_problem ? `Accepted. ${moved.path}: ${moved.commit_problem}`
        : `Accepted, and committed ${moved.path} to your repository.`);
      return;
    }
    /* Stays on the checks, which is where the reader was and what they just
       ruled on. Replacing the screen with a blank composer would leave the
       list with no route at all, since `checks` is not a link in the strip. */
    const parked = parkedChecks(state.project.project);
    toast(parked.length
      ? `Accepted. ${parked.join(', ')} stays parked until it passes; everything else judges every feature.`
      : 'Accepted. These checks judge every feature built here.');
  });
}

/* Both readings are taken from the last commit, and uncommitted work is not
   stale to them but invisible -- so it leads the dialog rather than trailing
   its paragraph, where it would be the sentence most likely to go unread.

   Only when there is some. Said on every press, to a working copy with nothing
   uncommitted in it, it reads as exactly what it looks like: a finding about
   your repository, and a false one. */
function uncommittedUnseen() {
  const d = (state.project || {}).drift || {};
  const n = Number(d.files) || 0;
  return n ? `${n === 1 ? 'One uncommitted change' : `${n} uncommitted changes`} in your working copy will
    not be seen: the reading is of the last commit. Commit first.` : '';
}

/* Asked in a dialog, because both survey actions call a model for several
   minutes, and they differ in what they are allowed to change: one proposes,
   the other replaces. That difference is the thing to read before pressing. */
async function askResurvey() {
  const yes = await confirmDialog({
    title: 'Survey this project from scratch?',
    warn: uncommittedUnseen(),
    body: 'A model reads your repository again and replaces the current reading: the checks, '
        + 'the environment and the test levels. Then every check runs on untouched code, and you '
        + 'approve the project again before anything is built.',
    confirmLabel: 'Survey from scratch',
    danger: true,
  });
  return yes ? resurvey() : undefined;
}

async function askProposeChanges() {
  const yes = await confirmDialog({
    title: 'Resurvey this project?',
    warn: uncommittedUnseen(),
    body: 'A model reads your repository\'s last commit, including its scripts, CI and '
        + 'configuration, and proposes changes to the checks, the test levels and the '
        + 'environment. Nothing changes until you accept each proposal. It usually takes '
        + 'several minutes.',
    confirmLabel: 'Resurvey',
  });
  return yes ? proposeChanges() : undefined;
}

function resurvey() {
  const done = working(document.getElementById('resurvey'), 'Surveying…');
  return withBusy('Surveying again, from scratch.', async () => {
    try {
      await api(`/api/projects/${encodeURIComponent(state.projectId)}/resurvey`, { method: 'POST' });
      await refreshProject();
    } finally {
      done();
    }
  });
}

/* Ask what should change, rather than re-deriving everything. One bounded call
   over the files that moved; the answer is a diff nobody has accepted yet. */
function proposeChanges() {
  const done = working(document.getElementById('propose-changes'), 'Reading the repository…');
  return withBusy(
    'Reading this project again.',
    async () => {
      try {
        await api(`/api/projects/${encodeURIComponent(state.projectId)}/proposal`, { method: 'POST' });
        state.askedProposal = true;
        await refreshProject();
      } finally {
        done();
      }
    },
    'a model is reading the repository and everything the verify lane has asked for; '
    + 'ten minutes is normal');
}

/* One part of a reading, accepted or left where it is shown. A testing card
   can carry a cleanup fix to choose; choosing one is applied with it, whole,
   and its files written first -- which is the apply endpoint's job. */
function rulePart(button) {
  const part = button.dataset.rulePart;
  const accept = button.dataset.accept === '1';
  const card = button.closest('.rs-part');
  const cleanup = {};
  for (const el of (card ? card.querySelectorAll('.rs-cleanup:checked') : [])) {
    cleanup[el.dataset.tier] = el.value === '' ? null : Number(el.value);
  }
  const fixing = Object.values(cleanup).some((v) => v !== null);
  const pid = encodeURIComponent(state.projectId);
  return withBusy(accept ? 'Applying the change.' : 'Leaving it as it is.', async () => {
    if (accept && fixing) {
      await api(`/api/projects/${pid}/proposal/apply`, {
        method: 'POST', body: JSON.stringify({ part, cleanup, checks: false }),
      });
    } else {
      await api(`/api/projects/${pid}/proposal/check`, {
        method: 'POST', body: JSON.stringify({ item: part, accept }),
      });
    }
    await refreshProject();
    /* Measured against what was just recorded, once nothing else in the
       reading waits: a testing reading keeps the baseline, so without a run
       the page goes on showing results taken before it. While something
       still waits, a run now would be measuring a list about to change. */
    const last = !partsWaiting().length && !waitingChanges().length;
    toast(!accept ? 'Left as it is. The rest of the proposal still waits.'
      : last ? 'Recorded. Running the checks against it.'
      : 'Recorded. The checks run once the rest of the proposal is ruled on.');
    return accept && last ? 'run' : '';
  }).then((next) => (next === 'run' && state.project ? runBaseline() : undefined));
}
