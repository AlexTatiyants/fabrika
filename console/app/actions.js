/* What the buttons do once pressed: saving roles and settings, writing
   scaffolding, rulings and approvals, the confirm dialog and armed buttons. */

'use strict';

function saveRole(name) {
  const d = state.roleDraft[name];
  const role = state.roles.roles.find((r) => r.name === name);
  return withBusy(`Saving ${name}.`, async () => {
    const body = {
      model: d.model, temperature: Number(d.temperature),
      max_tokens: Number(d.max_tokens), reasoning_effort: d.reasoning_effort,
      providers: String(d.providers).split(',').map((x) => x.trim()).filter(Boolean),
      allow_fallbacks: !!d.allow_fallbacks,
      route: d.route || '',
    };
    if (role.kind === 'review') {
      body.samples = Number(d.samples) || 1;
      body.enabled = !!d.enabled;
    }
    await api(`/api/roles/${encodeURIComponent(name)}`, { method: 'PATCH', body: JSON.stringify(body) });
    if (d.prompt !== role.prompt) {
      await api(`/api/roles/${encodeURIComponent(name)}/prompt`, {
        method: 'PUT', body: JSON.stringify({ prompt: d.prompt }),
      });
    }
    state.roleDraft = {};
    await loadRoles();
    render();
    afterSheetSave();
    toast(`${name} saved.`);
  });
}

function saveProvider() {
  const key = (document.getElementById('provider-key') || {}).value || '';
  const base = (document.getElementById('provider-base') || {}).value || '';
  const body = { base_url: base };
  if (key.trim()) body.api_key = key.trim();
  // Saving and testing are one action: a Test that silently reads saved state
  // while the key sits unsaved in the field reports "rejected" for "not saved".
  return withBusy('Saving, then asking the provider.', async () => {
    state.provider = await api('/api/provider', { method: 'PUT', body: JSON.stringify(body) });
    state.providerTest = await api('/api/provider/test', { method: 'POST' });
    state.providerOpen = !state.providerTest.ok;
    render();
    (state.providerTest.ok ? toast : errorToast)(
      state.providerTest.ok ? 'Key accepted.' : 'Saved, but the provider refused it.');
  });
}

function testProvider() {
  const field = document.getElementById('provider-key');
  if (field && field.value.trim()) {
    toast('That key is not saved yet — use Save and test.');
    return;
  }
  return withBusy('Asking the provider.', async () => {
    state.providerTest = await api('/api/provider/test', { method: 'POST' });
    render();
  });
}

function saveEditorUrl() {
  const field = document.getElementById('editor-url-field');
  const value = (field || {}).value || '';
  return withBusy('Saving.', async () => {
    const result = await api('/api/editor', { method: 'PUT', body: JSON.stringify({ editor_url: value }) });
    state.config = { ...state.config, editor_url: result.editor_url };
    render();
    toast(result.editor_url ? 'Saved.'
      : 'Saved — empty hides the "open in editor" link until this has a value again.');
  });
}

function addReviewAgent() {
  const name = (document.getElementById('new-agent-name') || {}).value || '';
  const model = (document.getElementById('new-agent-model') || {}).value || '';
  if (!name.trim() || !model.trim()) { toast('An agent needs a name and a model.'); return; }
  return withBusy('Adding.', async () => {
    await api('/api/roles', {
      method: 'POST',
      body: JSON.stringify({ name: name.trim(), model: model.trim(), samples: 1 }),
    });
    state.roleDraft = {};
    await loadRoles();
    render();
    toast('Added. Write its prompt before the next run.');
  });
}

function deleteReviewAgent(name) {
  return withBusy(`Removing ${name}.`, async () => {
    await api(`/api/roles/${encodeURIComponent(name)}`, { method: 'DELETE' });
    // Removed from under its own sheet. Leaving `openRole` set would leave the
    // page scroll-locked behind a dialog that no longer draws.
    if (state.openRole === name) state.openRole = null;
    state.roleDraft = {};
    await loadRoles();
    render();
  });
}


/* Fetched rather than rendered, and only when asked for. A diff is the largest
   thing on this page and the only reader who wants it is the one about to
   overwrite a file. */
async function showScaffoldDiff(button, path) {
  const out = main.querySelector(`[data-diff-for="${CSS.escape(path)}"]`);
  if (!out) return;
  if (!out.hidden) { out.hidden = true; button.textContent = 'Show what changes'; return; }
  const done = working(button, 'Reading…');
  let label = '';
  try {
    const d = await api(`${projectUrl(state.projectId)}/scaffold-diff?path=${encodeURIComponent(path)}`);
    out.textContent = d.identical
      ? 'Byte for byte what you already have. Nothing would change.'
      : d.diff || '(no difference)';
    out.hidden = false;
    label = `Hide (${d.changed_lines} line${d.changed_lines === 1 ? '' : 's'} differ)`;
  } catch (e) {
    errorToast(e.message);
  } finally {
    // `working` puts the button's own label back, so a new one has to be set
    // after it, not before -- set first, the count is written and then overwritten.
    done();
    if (label) button.textContent = label;
  }
}

function writeScaffolding(paths, { reread = false, replace = [] } = {}) {
  // Nothing to write is a state a reader can be looking straight at without
  // knowing it -- every file already applied, on a card still offering the
  // button. Silence there reads as a broken button.
  if (!paths.length) { toast('Every one of these is already in your repository.'); return; }
  /* Writing a file and asking whether it changed anything are one intent said
     twice. `reread` sends both as one call: the files land, the repository is
     read again, and the corrected reading comes back for a ruling. What is not
     folded in is applying that reading -- `usable` is a claim on gate 0, and
     the generous direction is the expensive one, so a human still rules with
     `testing_probe` beside it. */
  const label = replace.length
    ? `Replacing ${replace.join(', ')}.`
    : `Writing ${paths.length} file(s).`;
  /* The write is waited on and the reading is not.

     The reading is a model call that can take nearly eight minutes. Holding
     the request open for both would mean a modal overlay for eight minutes,
     which reads as a hang; a reader reloads, and the reload cancels the task
     -- so the one thing guaranteed to make it stuck is the reasonable response
     to it looking stuck. If the files have already been committed by then, the
     money is spent and the answer discarded.

     So the POST returns as soon as the files are on disk, and the reading
     reports itself through the progress stream like the checks do: a strip on
     the page, not a lock over it, and it survives a reload. */
  if (reread) {
    state.projectRun = { phase: 'resurvey', detail: 'reading the repository again' };
    clearTimeout(writeScaffolding._t);
    writeScaffolding._t = setTimeout(() => { state.projectRun = null; render(); }, 20 * 60 * 1000);
  }
  return withBusy(label, async () => {
    const done = await api(
      `${projectUrl(state.projectId)}/${reread ? 'scaffold-and-read' : 'scaffold'}`,
      { method: 'POST', body: JSON.stringify({ paths, replace }) });
    const wrote = reread ? done.scaffold : done;
    await refreshProject();
    const skipped = (wrote.skipped || []).length;
    if (!wrote.written.length) {
      // The reason matters and is shown, not guessed at. "Already exactly this"
      // and "already exists, left alone" are different situations, and one of
      // them can cover for a bug that writes the wrong version of a file.
      state.projectRun = null;
      errorToast(`Nothing written. ${(wrote.skipped || []).join('; ') || 'No reason given.'}`);
      return;
    }
    toast(`Added ${wrote.written.join(', ')}${skipped ? `; ${skipped} already existed` : ''}.`
      + (reread ? ' Re-reading the repository now — several minutes; you can leave this page.'
                : ' Run the checks again.'));
  });
}

function runBaseline() {
  // Backgrounded server-side: the POST is accepted in milliseconds and the run
  // takes half a minute. So the flag is set here rather than released here, and
  // the progress stream clears it -- with a ceiling, because a dropped stream
  // must not leave a button disabled forever.
  state.projectRun = { phase: 'checks', detail: 'on an untouched checkout' };
  clearTimeout(runBaseline._t);
  runBaseline._t = setTimeout(() => { state.projectRun = null; render(); }, 15 * 60 * 1000);
  render();
  return withBusy('Running the checks on an untouched checkout.', async () => {
    try {
      await api(`${projectUrl(state.projectId)}/baseline`, { method: 'POST' });
    } catch (e) {
      state.projectRun = null;
      render();
      throw e;
    }
    await refreshProject();
  });
}

function discardProject() {
  const alsoBranches = !!(document.getElementById('discard-branches') || {}).checked;
  const id = state.projectId;
  return withBusy('Unregistering.', async () => {
    const gone = await api(`${projectUrl(id)}?delete_branches=${alsoBranches}`, { method: 'DELETE' });
    state.discarding = false;
    state.project = null;
    location.hash = '#/';
    await loadProjects();
    render();
    toast(`Unregistered. ${gone.repo} is untouched.`);
  });
}

function discardFeature(project = state.projectId, feature = state.id) {
  // The checkbox only exists on the log screen, and only it can lose code.
  // Reached from the rail or the bar, this always keeps the branch.
  const box = document.getElementById('discard-branch');
  const alsoBranch = !!(box && box.checked && feature === state.id);
  const leaving = feature === state.id;
  return withBusy('Discarding.', async () => {
    const gone = await api(
      `${featureUrl(project, feature)}?delete_branch=${alsoBranch}`, { method: 'DELETE' },
    );
    state.discarding = false;
    state.log = null;
    // Only when the screen you are on is the thing that just went.
    if (leaving) location.hash = `#/${encodeURIComponent(project)}`;
    else await refresh(true);
    toast(gone.branch && !gone.branch_deleted
      ? `Discarded. ${gone.branch} still exists in git.`
      : 'Discarded.');
  });
}

function sendReintake() {
  return withBusy('Reading the repository again.', async () => {
    await api(`${featureUrl(state.projectId, state.id)}/reintake`, { method: 'POST' });
    state.reintaking = false;
    state.answers = {};
    state.openQuestion = null;
    state.intakePlan = null;
    await refresh();
    toast('Reading it again. The old scout report was superseded.');
  });
}

function sendCorrection() {
  const text = (document.getElementById('correction') || {}).value || '';
  if (!text.trim()) { toast('Say what it actually is.'); return; }
  return withBusy('Asking again from your correction.', async () => {
    await api(`${featureUrl(state.projectId, state.id)}/correct`, {
      method: 'POST', body: JSON.stringify({ correction: text.trim() }),
    });
    state.correcting = false;
    state.answers = {};
    state.openQuestion = null;
    await refresh();
    toast('Asking again. The old questions were discarded.');
  });
}

/* Gate 1 with its consequence in front of it.

   Approving starts a build, and a build that reaches a plan's limit does not
   stop at a sensible place -- it stops wherever it happens to be, with every
   agent after that point never running. So before the button does anything the
   gate asks what the plans have left, against what a run has actually drawn
   from them, and offers the answer that costs nothing: the window resets at a
   known second, so "not enough" and "enough in 31 minutes" are the same fact.

   The choice stays the human's. This is a gate, and a gate that quietly
   rescheduled the work would be deciding rather than telling. */
function approveSpec(when) {
  const note = when === 'at_reset'
    ? 'Held. The build starts by itself when the window resets.'
    : 'Approved. The build is running; nothing more is needed from you.';
  return withBusy(note, async () => {
    const url = `${featureUrl(state.projectId, state.id)}/approve-spec${
      when === 'at_reset' ? '?when=at_reset' : ''}`;
    await api(url, { method: 'POST' });
    await refresh();
  });
}

async function loadPlanCheck() {
  if (state.planCheck && state.planCheck.id === state.id) return;
  const body = await api(`${featureUrl(state.projectId, state.id)}/plan-check`)
    .catch(() => null);
  state.planCheck = body ? { ...body, id: state.id } : { verdict: 'go', id: state.id };
  render();
}

/* What the gate says before it is pressed. Silent when every plan has room,
   because a line that always appears stops being read. */
function planWarning() {
  // Asked for the first time the gate is drawn, and not again: it may spend a
  // probe on a route whose gauge cannot be read for free, and a re-render is
  // not a new question.
  if (!state.planCheck || state.planCheck.id !== state.id) loadPlanCheck();
  const p = state.planCheck;
  if (!p || p.id !== state.id || p.verdict === 'go') return '';
  if (p.verdict === 'unknown') {
    return `<p class="plan-warn plan-warn-soft">${esc(p.reason)}. The build can start;
      whether it finishes is not something this reading can say.</p>`;
  }
  const mins = Math.round((p.seconds_to_start || 0) / 60);
  const when = new Date((p.start_at || 0) * 1000)
    .toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return `<p class="plan-warn">${esc(p.reason)}.
    Its ${esc(p.window)} window resets at ${esc(when)}, in ${mins} min.
    <a href="#" data-help-open="waiting-for-a-plan-window">Why it offers to wait</a></p>`;
}

function planActions() {
  const p = state.planCheck;
  const waiting = p && p.id === state.id && p.verdict === 'wait' && p.start_at;
  if (!waiting) {
    return `<button class="btn btn-primary btn-sm" id="approve-spec"
      title="Approving starts the build. There is no further input until it produces a packet."
      >Approve &amp; build</button>`;
  }
  const when = new Date(p.start_at * 1000)
    .toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return `<button class="btn btn-primary btn-sm" id="approve-at-reset"
      title="The spec is frozen now. The build starts by itself when the window resets."
      >Freeze &amp; start ${esc(when)}</button>
    <button class="btn btn-quiet btn-sm" id="approve-spec"
      title="Starts now, on a subscription that may not have enough left in this window to finish."
      >Start anyway</button>`;
}

function rule(decisionId, ruling) {
  return withBusy('Recording.', async () => {
    await api(`${featureUrl(state.projectId, state.id)}/rulings`, {
      method: 'POST', body: JSON.stringify({ decision_id: decisionId, ruling }),
    });
    // Append-only: re-read rather than patching the local copy.
    await refresh();
    toast(ruling === 'accept' ? 'Accepted.' : 'Sent back.');
  });
}

/* Two presses, because one press buys an hour of gates, containers and review
   agents out of a budget the feature cannot get back. The bar has no room to
   say that next to the label, so the second state says it instead of the label
   -- the consequence appears exactly where the action is.

   The armed state lives on the element rather than in a module variable: this
   button is drawn into two different homes (the packet page's own bar and the
   mounted screen's), and a poll that rebuilds either one drops the arming with
   the element. That failure direction is the safe one -- a rebuilt button is
   disarmed, never armed. */
/* A confirmation that stops, for the controls that cannot be undone by
   pressing the same button again.

   The arm-in-place pattern below is right for most of them: it costs one
   press, stays in the flow, and the label is the warning. It is wrong when
   the warning does not fit on a button -- an armed rebuild runs to sixty
   characters, stretches the bar it lives in and collides with its own
   tooltip, which is a control shouting rather than explaining.

   Native `<dialog>`: focus is trapped, Escape closes, the backdrop is the
   browser's. No library, and nothing to keep in sync with a render.

   Built once and reused, because a dialog appended per press leaves one in the
   DOM per press. */
let confirmBox = null;

function confirmDialog({ title, warn = '', body, keeps = '', confirmLabel, danger = false }) {
  if (!confirmBox) {
    confirmBox = document.createElement('dialog');
    confirmBox.className = 'confirm';
    document.body.appendChild(confirmBox);
  }
  confirmBox.innerHTML = `
    <form method="dialog" class="confirm-body">
      <h2 class="confirm-title">${esc(title)}</h2>
      ${warn ? `<p class="confirm-warn">${esc(warn)}</p>` : ''}
      <p class="confirm-what">${esc(body)}</p>
      ${keeps ? `<p class="confirm-keeps">${esc(keeps)}</p>` : ''}
      <div class="confirm-act">
        <button class="btn btn-quiet btn-sm" value="no">Cancel</button>
        <button class="btn ${danger ? 'btn-danger' : 'btn-primary'} btn-sm" value="yes"
          >${esc(confirmLabel)}</button>
      </div>
    </form>`;
  return new Promise((resolve) => {
    confirmBox.addEventListener('close', function done() {
      confirmBox.removeEventListener('close', done);
      resolve(confirmBox.returnValue === 'yes');
    });
    confirmBox.showModal();
    // Cancel holds the focus. The destructive button is one tab away rather
    // than under a reflexive Return.
    confirmBox.querySelector('[value="no"]')?.focus();
  });
}

/* Said twice, on two screens, so it lives once. The pair is a mirror: `Verify
   again` keeps the build and re-measures it; `Build again` replaces the build
   and keeps the spec. Only one of them resets a branch, which is why only one
   of them spells that out. */
const REBUILD_TITLE = 'Builds the units again over the spec you already froze. Spec review is not reopened. The branch is reset first, so the code on it now is replaced rather than added to — the old commits stay reachable in git, and the record only grows. Use it when the last packet judged the harness rather than the code: units whose environment never came up, a plan written before the architect had read the repository.';

const ARM_MS = 8000;

/* Returns true when the press should go through: the second one, inside the
   window. The first swaps the control's own label for the consequence, which is
   the only place a toolbar has to say it. */
function armed(button, question, cls = 'btn-armed') {
  if (button.dataset.armed === '1') {
    button.dataset.armed = '';
    return true;
  }
  button.dataset.armed = '1';
  button.dataset.was = button.innerHTML;
  button.innerHTML = esc(question);
  button.classList.add(cls);
  setTimeout(() => {
    if (button.dataset.armed !== '1') return;
    button.dataset.armed = '';
    button.innerHTML = button.dataset.was;
    button.classList.remove(cls);
  }, ARM_MS);
  return false;
}

/* Asked in a dialog rather than on the button. What this replaces and what it
   keeps is three sentences, and three sentences do not go on a control -- the
   armed label stretched its own bar and ran under the tooltip explaining it. */
async function askRebuild() {
  const yes = await confirmDialog({
    title: 'Build this feature again?',
    body: 'The units are built again over the spec you already froze. The branch is '
        + 'reset first, so the code on it now is replaced rather than added to.',
    keeps: 'Spec review is not reopened, the old commits stay reachable in git, and the '
         + 'record only grows. It takes about as long as the last run.',
    confirmLabel: 'Build again',
    danger: true,
  });
  return yes ? rebuild() : undefined;
}

function rebuild() {
  return withBusy('Building again over the same spec.', async () => {
    await api(`${featureUrl(state.projectId, state.id)}/rebuild`, { method: 'POST' });
    await refresh();
  });
}

/* Asked in a dialog, like building again. An armed label has room for
   something like "a full run, about an hour" -- which is wrong as well as
   cramped: nothing is built again. What happens, and what is kept, needs
   saying in full. */
async function askRevalidate() {
  const yes = await confirmDialog({
    title: 'Verify this feature again?',
    body: 'The code on the branch stays exactly as it is, and everything that judges it '
        + 'runs again: the checks, the blind tests, the probes, the review panel and the '
        + 'repair loop. It spends from the same budget as the last run.',
    confirmLabel: 'Verify again',
  });
  return yes ? revalidate() : undefined;
}

/* Discard is the one control here that destroys a record, so it asks twice
   wherever it appears and its second state says what goes and what survives.
   The branch survives, unless you take it from the log screen -- which is where
   the checkbox that loses code lives, and where it is explained. */
function armDiscard(button) {
  const feature = button.dataset.feature || state.id;
  const project = button.dataset.project || state.projectId;
  // The rail has room for two words and the bar has room for the sentence.
  const question = button.classList.contains('pf-discard')
    ? 'discard?'
    : 'Discard? The ledger and the checkout go; the branch is kept';
  return armed(button, question) ? discardFeature(project, feature) : undefined;
}

/* Not a verdict and not a route: it says nothing about the work. It says this
   packet measured the harness rather than the code, and asks for the same
   branch to be measured and reviewed again from nothing. */
/* Picks a stopped build back up rather than paying for all of it again. The
   endpoint belongs to the build lane; without this, a stopped run is a page
   you can only leave. */
/* Asked in a dialog, like verifying again. An armed label would run across
   the whole bar and still could not say what is kept. */
async function askRetryBuild() {
  const yes = await confirmDialog({
    title: 'Resume this run?',
    body: 'The build picks up where it stopped. Only what stopped runs again, and it '
        + 'spends from the same budget as the last run.',
    keeps: 'Everything already bought is reused: the plan, the blind tests and the '
         + 'units that finished.',
    confirmLabel: 'Resume the run',
  });
  return yes ? retryBuild() : undefined;
}

function retryBuild() {
  return withBusy('Resuming the build.', async () => {
    await api(`${featureUrl(state.projectId, state.id)}/retry-build`, { method: 'POST' });
    await refresh();
  });
}

function revalidate() {
  return withBusy('Verifying again.', async () => {
    await api(`${featureUrl(state.projectId, state.id)}/revalidate`, { method: 'POST' });
    await refresh();
  });
}


/* The stop bar. Colour enters only to say the line is disrupted; a packet that
   ships says so in a quiet bar, because good news does not need a field. */
function stopBar(d, packet, openDecisions) {
  const stats = packet.stats || {};
  const ships = packet.verdict === 'ship';
  const foot = [
    [`${stats.gates_passed}/${stats.gates_total}`, 'checks green'],
    [stats.blockers, 'blockers'],
    [`${(packet.findings || []).length}`, 'findings'],
    [`${stats.orphan_requirements || 0}/${stats.criteria_total}`, 'requirements with no part'],
    [`${packet.attention_budget_minutes} min`, 'of your attention budgeted'],
  ];
  return `
    <section class="stopbar ${ships ? 'good' : 'disrupted'}">
      <div class="stopbar-in">
        <span class="stopbar-w">${esc(ships ? 'Line running' : 'Line stopped')}</span>
        <div>
          <p class="verdict-headline">${esc(packet.headline)}</p>
          ${packet.summary ? `<p class="stopbar-sum">${esc(packet.summary)}</p>` : ''}
        </div>
      </div>
      <div class="stopbar-foot">
        ${foot.map(([n, k]) => `<span><b>${esc(n)}</b> ${esc(k)}</span>`).join('')}
        <span><b>${openDecisions}</b> decision${openDecisions === 1 ? '' : 's'} still need a ruling</span>
      </div>
    </section>`;
}

/* The cord: the operator's right to stop the line, and the only control here
   drawn as an object. It is also the one irreversible click in the product, so
   it does not fire on the first press -- it says what it is about to do first.
   The console spends two thousand lines stating a control's consequence before
   it is pressed, and the one irreversible click is no place to stop. */
function cordSection(d, packet, settled) {
  const stats = packet.stats || {};
  const records = packet.records || [];
  const openFindings = records.filter((r) => !SETTLED_OUTCOMES.includes(r.outcome)).length
    || (packet.findings || []).length;
  const branch = (d.sandbox && d.sandbox.branch) || 'this branch';
  const budget = d.budget || {};
  const spent = typeof budget.spent_usd === 'number' ? `$${budget.spent_usd.toFixed(2)}` : null;

  if (settled) {
    return `<section class="cord cord-settled">
      <div class="cord-body">
        <h2>The line has been ruled on</h2>
        <p>This packet was ruled <b>${esc(d.state.stage)}</b> ${esc(rel((d.verdict || {}).at))}.
          Everything above is kept as it was read.</p>
      </div>
    </section>`;
  }

  return `<section class="cord" id="cord">
    <div class="cord-in">
      <div class="cord-body">
        <h2>Restart the line, or send it back</h2>
        <p>${esc(stats.criteria_verified)} of ${esc(stats.criteria_total)} requirements were made,
          ${esc(stats.orphan_requirements || 0)} have no part at all, and
          ${esc(stats.blockers)} blocker${stats.blockers === 1 ? '' : 's'} stand.
          <a href="#" data-help-open="ruling-on-a-packet">What each one does</a></p>
        <div class="acts">
          <button class="btn btn-stop" id="verdict-reject">Pull the cord — send it back</button>
          <button class="btn" id="verdict-accept">Release the line — accept</button>
        </div>

        <div class="consequence-panel" id="cq-rejected" hidden>
          <p class="cq-h">Sending it back records your ruling: the feature is rejected.</p>
          <ul>
            <li>Nothing runs. The branch <span class="mono">${esc(branch)}</span> and its checkout
              are left standing, so you can look at what was built.</li>
            <li>To have the work repaired instead, flag what is wrong and use
              <b>Send to the repair loop</b> on the worklist; repairs come out of the rework
              budget${spent ? `, of which ${esc(spent)} is already spent` : ''}.</li>
            <li>To start over, <b>Build again</b>.</li>
          </ul>
          <div class="acts">
            <button class="btn btn-stop" id="verdict-go" data-value="rejected">Send it back</button>
            <button class="btn btn-quiet" id="verdict-cancel">Not yet</button>
          </div>
        </div>

        <div class="consequence-panel" id="cq-accepted" hidden>
          <p class="cq-h">Releasing the line cannot be taken back.</p>
          <ul>
            <li><span class="mono">${esc(branch)}</span> is accepted as it stands.</li>
            <li><b>${esc(stats.blockers)}</b> blocker${stats.blockers === 1 ? '' : 's'} and
                <b>${esc(openFindings)}</b> unrepaired finding${openFindings === 1 ? '' : 's'}
                are accepted with it, and stay on the record with your reason beside them.</li>
            <li>${esc(stats.orphan_requirements || 0)} requirement${(stats.orphan_requirements || 0) === 1 ? '' : 's'}
                that nothing on disk satisfies ${(stats.orphan_requirements || 0) === 1 ? 'is' : 'are'}
                accepted unbuilt.</li>
          </ul>
          <div class="acts">
            <button class="btn btn-danger" id="verdict-go" data-value="accepted">Accept, with all of that standing</button>
            <button class="btn btn-quiet" id="verdict-cancel">Not yet</button>
          </div>
        </div>
      </div>
      <div class="cord-pull" aria-hidden="true">
        <svg width="34" height="150" viewBox="0 0 34 150">
          <line x1="17" y1="0" x2="17" y2="104" stroke="#e03127" stroke-width="2.5"/>
          <path d="M17 104 a11 11 0 0 1 11 11 v16 a11 11 0 0 1 -11 11 a11 11 0 0 1 -11 -11 v-16 a11 11 0 0 1 11 -11 z"
                fill="none" stroke="#e03127" stroke-width="2.5"/>
          <line x1="6" y1="121" x2="28" y2="121" stroke="#e03127" stroke-width="2.5"/>
        </svg>
        <span>Any station<br>may stop<br>the line</span>
      </div>
    </div>
  </section>`;
}

/* Showing a consequence is not a verdict. Only #verdict-go rules. */
function showConsequence(which) {
  ['accepted', 'rejected'].forEach((k) => {
    const el = document.getElementById(`cq-${k}`);
    if (el) el.hidden = k !== which;
  });
  const panel = document.getElementById(`cq-${which}`);
  if (panel) {
    const go = panel.querySelector('#verdict-go');
    if (go) go.focus();
  }
}

function hideConsequence() {
  ['accepted', 'rejected'].forEach((k) => {
    const el = document.getElementById(`cq-${k}`);
    if (el) el.hidden = true;
  });
}

function verdict(value) {
  return withBusy('Recording the verdict.', async () => {
    await api(`${featureUrl(state.projectId, state.id)}/verdict`, {
      method: 'POST', body: JSON.stringify({ verdict: value }),
    });
    await refresh();
  });
}
