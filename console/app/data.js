/* Fetching what the screens draw -- the log, roles, projects, a feature --
   and route(), which turns the hash into a screen. */

'use strict';

/* ------------------------------------------------------------------ data */

async function loadIntakePlan() {
  try {
    state.intakePlan = await api(`${featureUrl(state.projectId, state.id)}/intake-plan`);
    render();
  } catch (e) { /* the button still works without a cost estimate */ }
}

async function loadLog() {
  try {
    state.log = state.id
      ? await api(`${featureUrl(state.projectId, state.id)}/log`)
      : await api(`/api/projects/${encodeURIComponent(state.projectId)}/log`);
  } catch (e) { errorToast(e.message); }
}

/* The gauge, kept honest without spending anything.

   A harness card that shows whatever the last call happened to leave behind
   can present a number six days old as the current state of a plan. Pressing
   Check refreshes it and costs a turn, so the screen cannot do it on your
   behalf.

   What it can do is read: the tools write their own windows into their session
   logs, including when you use them yourself at the same terminal, and
   `/api/meters` is that read and nothing else -- no `which`, no `--version`, no
   model call. So the bars follow your own usage of the same plan, which is the
   thing they claim to describe. */
const METER_POLL_MS = 20000;

/* Merge a gauge reading into the cards already on screen. Separate from the
   fetch so the same merge serves both callers: the load, which has the reading
   before it paints, and the timer, which has to fold one into a board that is
   already drawn. */
function applyMeters(gauges) {
  if (!gauges) return false;
  const rows = ((state.routes || {}).routes || []).map((r) => (
    gauges[r.name] ? { ...r, ...gauges[r.name] } : r));
  state.routes = { ...(state.routes || {}), routes: rows };
  return true;
}

async function pollMeters() {
  if (state.view !== 'roles' || document.hidden) return;
  const body = await api('/api/meters').catch(() => null);
  if (applyMeters(body && body.routes)) render();
}

function startMeterPoll() {
  if (state.meterPoller) return;
  state.meterPoller = setInterval(pollMeters, METER_POLL_MS);
  // Coming back to the tab is the moment the number is most likely wrong and
  // most likely to be read, so it is worth one read that the timer has not
  // reached yet.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) pollMeters();
  });
  window.addEventListener('focus', pollMeters);
}

/* Navigating here brings every plan's headroom current. It is not part of the
   same wait as the cards: that would make the first paint carry real numbers,
   but the page would sit on "Loading…" for as long as a probe takes, which
   looks like a hang. The cards paint first, the plan section says it is being
   refreshed, and the numbers land in place when they arrive.

   Mostly free: a tool that writes its windows into a session log is read off
   disk, and the human's own use of that tool keeps the file fresh. Where a tool
   reports its windows only inside a call, and its window has since reset, this
   spends one turn on the shortest prompt in the system -- at most once per
   window, because a reading that survives the visit makes the next one free.

   That is a deliberate position. Opening a screen is not, in general, consent
   to spend a turn, and that rule matters most where a page load would probe
   every route every time. What is spent here is bounded to the one route that
   cannot answer any other way, in the one state where it has no answer at
   all -- and the reason to spend it is that these numbers decide
   whether a run can be scheduled, so "we used to have 42%" is not an answer a
   build can be committed against. The polled refresh below stays free. */
async function loadRoles() {
  /* Painted as its parts arrive rather than all at once. The slowest part is
     the plan refresh, which can spend a turn and take several seconds, and a
     page that waited on "Loading…" for all of it would look stuck. Each part
     fills its own section, and a section still waiting says so. */
  const token = {};
  state.crewLoad = token;
  const step = (key, request, apply) => request.then((value) => {
    if (state.crewLoad !== token) return;
    apply(value);
    token[key] = true;
    if (state.view === 'roles') render();
  });
  const meters = step('meters',
    api('/api/meters/refresh', { method: 'POST' }).catch(() => null),
    (body) => {
      state.lastGauges = (body && body.routes) || state.lastGauges;
      applyMeters(state.lastGauges);
    });
  await Promise.all([
    step('roles', api('/api/roles').catch((e) => { errorToast(e.message); return null; }),
      (v) => { if (v) state.roles = v; }),
    step('provider', api('/api/provider').catch(() => null), (v) => { state.provider = v; }),
    step('routes', api('/api/routes').catch(() => null), (v) => {
      if (v) state.routes = v;
      // The refresh may have landed first; its numbers belong on these cards.
      applyMeters(state.lastGauges);
    }),
    step('egress', api('/api/egress').catch(() => null), (v) => { state.egress = v; }),
  ]);
  startMeterPoll();
  // A reading already under way when the page opened is followed from here.
  if (((state.roles || {}).as_built_running || []).length) asBuiltCrewSoon(5000);
  return meters;
}

/* One real call against a harness, which spends a turn on a live plan. Not run
   on page load for that reason: opening a screen is not consent to spend one. */
function checkRoute(name) {
  return withBusy(`Asking ${name} whether it is connected.`, async () => {
    const status = await api(`/api/routes/${encodeURIComponent(name)}/check`,
      { method: 'POST' });
    const rows = ((state.routes || {}).routes || []).map(
      (r) => (r.name === name ? status : r));
    state.routes = { ...(state.routes || {}), routes: rows };
    render();
    (status.usable ? toast : errorToast)(
      status.usable ? `${status.label} is connected.` : status.detail);
  });
}

async function loadProjects() {
  // The rail is on screen everywhere, so the whole picture is always loaded.
  const [projects, features, version] = await Promise.all([
    api('/api/projects').catch(() => null),
    api('/api/features').catch(() => null),
    // Whether the code answering us is the code on disk. Cheap, and the answer
    // decides whether anything else on the screen can be trusted.
    api('/api/version').catch(() => null),
  ]);
  /* `[]` is not the failure value: with it, a server that does not answer
     empties the rail and every screen built from it -- the yard goes to "no
     projects", and a project page falls through to a loading placeholder that
     never resolves. An empty list is a real answer, meaning this factory has been
     pointed at nothing, and the two must not share a value.

     Keeping what was last known is the honest thing while nothing can be
     asked: it is the last true state of the ledger, the ledger has not
     changed, and the bar above says why the page has stopped moving. */
  if (projects !== null) state.projects = projects;
  if (features !== null) state.allFeatures = features;
  state.version = version;
  if (!state.projectId && features !== null) state.features = features;
}

async function loadProject(quiet) {
  if (!state.projectId) { state.project = null; state.features = []; return; }
  try {
    state.project = await api(`/api/projects/${encodeURIComponent(state.projectId)}`);
    state.projectError = null;
    // The last proposal and whether it was ruled on. Quiet failure: a project
    // that has never been asked simply has none.
    state.resurvey = await api(
      `/api/projects/${encodeURIComponent(state.projectId)}/proposal`).catch(() => null);
    // The overview's features, measured on the server. Quiet on failure: the
    // tiles that do not need it still draw.
    const ov = await api(
      `/api/projects/${encodeURIComponent(state.projectId)}/overview`).catch(() => null);
    if (ov) state.overview = { ...ov, project: state.projectId };
    if (projectSection() === 'as-built') await asBuiltLoad(state.projectId);
    if (projectSection() === 'history') {
      const h = await api(
        `/api/projects/${encodeURIComponent(state.projectId)}/history`).catch(() => null);
      if (h) state.history = { ...h, project: state.projectId };
    }
    state.features = state.project.features || [];
  } catch (e) {
    if (!quiet) errorToast(e.message);
    state.projectError = e.message;
    state.project = null;
    state.features = [];
  }
}


/* Nothing here. A route can be wrong -- a stale link, a discarded feature, a
   typed id -- and the answer to that is a screen that says so and points back
   at something real, rather than a "Loading…" that never resolves and a toast
   that takes the only explanation away after four seconds. */
function notFoundScreen() {
  const project = state.projectId;
  /* A bad project link and a bad feature link are different dead ends. Saying
     "feature" for both would quote the feature id, which on a project URL is
     null -- naming a thing that was never asked for. */
  const feature = !!state.id;
  return `
    <div class="wrap narrow">
      <div class="notfound">
        <p class="nf-word">Nothing on the line</p>
        <h1>${feature
          ? 'This feature is not on the board.'
          : 'There is no such project in the yard.'}</h1>
        <p class="nf-why">${esc((feature ? state.loadError : state.projectError)
          || 'It could not be loaded.')}</p>
        <p class="nf-hint">${feature
          ? `It may have been discarded, or the link may be older than the ledger.`
          : `It may have been removed, or the link may name a project this factory has
             never been pointed at.`} Nothing has been lost by arriving here.</p>
        <div class="nf-acts">
          ${feature && project ? `<a class="btn btn-primary" href="#/${encodeURIComponent(project)}"
            >Back to ${esc(project)}</a>` : ''}
          <a class="btn ${feature ? '' : 'btn-primary'}" href="#/">The yard</a>
        </div>
      </div>
    </div>`;
}


/* What changed, said once, for anyone who is not looking at the screen.

   Only genuine transitions: the first load of a feature already on a stage is
   not news, and a poll that finds nothing new must stay silent or the region
   becomes the thing you turn off. */
function announce(text) {
  if (!liveEl || !text) return;
  // A screen reader will not re-read an identical string, so clear first.
  liveEl.textContent = '';
  setTimeout(() => { liveEl.textContent = text; }, 60);
}

function stageAnnouncement(data) {
  const stage = data.state.stage;
  const title = data.state.title || 'This feature';
  switch (stage) {
    case 'building':
      return `${title}: the line is running. No input is needed until it finishes.`;
    case 'awaiting_verdict': {
      const stats = ((data.packet || {}).stats) || {};
      const blockers = stats.blockers
        ? `, with ${stats.blockers} blocker${stats.blockers === 1 ? '' : 's'} standing` : '';
      return `${title}: the build finished${blockers}. The packet is waiting on your ruling.`;
    }
    case 'awaiting_answers': {
      const n = ((data.interrogation || {}).ambiguities || []).length;
      return `${title}: station 2 called for help. ${n} question${n === 1 ? '' : 's'} waiting on you.`;
    }
    case 'awaiting_spec_approval':
      return `${title}: the work order is ready to release.`;
    case 'failed': {
      const f = (data.phases || []).find((p) => p.status === 'failed');
      return `${title}: the line stopped${f ? ` at ${f.name}` : ''}.`;
    }
    case 'accepted': return `${title}: ruled accepted.`;
    case 'rejected': return `${title}: sent back for revision.`;
    default: return `${title}: ${STAGE_WORDS[stage] || stage}.`;
  }
}

async function refresh(quiet) {
  if (!state.id) return;
  state.lastRefreshAt = Date.now();
  try {
    /* The rail, the feature and its recordings ask about different things, so
       they are asked at once: one after another, a poll would take the sum of
       four round trips rather than the longest. Only the ledger waits, because whether it is
       wanted depends on the stage the feature reports. */
    const [, data, rec] = await Promise.all([
      loadProjects(),
      api(featureUrl(state.projectId, state.id)),
      /* Recordings of the browser runs. Names and sizes only: a trace is a
         download, its frames are fetched when a reader opens it, and nothing is
         paid for until then. A factory that has not been restarted answers 404,
         which is an empty band rather than an error. */
      api(`${featureUrl(state.projectId, state.id)}/traces`).catch(() => null),
    ]);
    // The timeline places a dot per agent call, so a live run needs the ledger
    // as well as the phase list.
    if (['intake', 'writing_spec', 'building'].includes(data.state.stage)
        || state.view === 'log' || state.view === 'build') {
      state.log = await api(`${featureUrl(state.projectId, state.id)}/log`).catch(() => state.log);
    }
    state.traces = (rec || {}).traces || [];
    state.tracesOpenWith = (rec || {}).open_with || '';
    state.loadError = null;
    const known = !!state.data;
    const stageChanged = !state.data || state.data.state.stage !== data.state.stage;
    // Only a real transition on a feature we were already watching.
    if (known && stageChanged) announce(stageAnnouncement(data));
    state.data = data;
    if (stageChanged || !Object.keys(state.answers).length) seedAnswers();
    render();
  } catch (e) {
    // A toast is 3.8 seconds; a placeholder that never resolves is forever.
    // Whatever went wrong, the screen has to say so and offer a way out.
    //
    // Except when nothing answered. Then there is nothing new to say, the page
    // on screen is still the last true thing known, and redrawing it from state
    // that could not be refreshed would let a restart replace a working screen
    // with "Loading…". The bar says the server is gone; the page waits.
    if (e && e.offline) return;
    state.loadError = e.message;
    if (!quiet) errorToast(e.message);
    render();
  }
}

async function refreshProject(quiet) {
  state.lastRefreshAt = Date.now();
  await Promise.all([loadProjects(), loadProject(quiet)]);
  render();
}

function seedAnswers() {
  const d = state.data || {};
  state.answers = {};
  // The recorded answers are the truth; the spec's copy is derived from them
  // and only exists once the spec writer has finished.
  const recorded = (d.answers || {}).resolved;
  const source = recorded || (d.spec || {}).resolved_answers;
  (source || []).forEach((a) => { state.answers[a.question_id] = a.answer; });
}

/* The fragment to scroll to, consumed by the first render that can find it. */
let pendingScroll = null;

/* What `#main` was last built from. An event can arrive for data nobody on
   screen owns; skip the rebuild when the string it produces is the one
   already there. */
let lastMainHtml = null;

async function route() {
  /* A link may carry a fragment -- `?packet#cord` -- so it can land on the
     region it names. The router owns everything before that second hash; the
     fragment is only a scroll target, and left in the query it made
     `packet#cord` a view nothing matches. */
  const full = location.hash.replace(/^#\/?/, '');
  const cut = full.indexOf('#');
  const raw = cut === -1 ? full : full.slice(0, cut);
  const fragment = cut === -1 ? '' : full.slice(cut + 1);
  const [path, query = ''] = raw.split('?');
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  const flags = query.split('&');
  // Bare tokens name a screen; `key=value` names a screen and its subject. Both
  // shapes, because `?spec` reads better than `?view=spec` and `?criterion=AC-1`
  // cannot be said without a value.
  const params = {};
  flags.filter(Boolean).forEach((bit) => {
    const at = bit.indexOf('=');
    if (at === -1) params[bit] = true;
    else params[bit.slice(0, at)] = decodeURIComponent(bit.slice(at + 1));
  });
  state.fragment = fragment;
  pendingScroll = fragment || null;
  state.helpTopic = typeof params.help === 'string' ? params.help : HELP_START;
  // Settings is folded into the tabs; an old `?settings` link lands on Survey,
  // where the repository's own facts are edited now. `&edit` opens a tab
  // already editing.
  if (params.settings) { params.gates = 'survey'; delete params.settings; }
  const view = params.help ? 'help'
    : params.setup ? 'setup'
    : params.roles ? 'roles'
    : params['agent-config'] ? 'agent-config'
    : params['control-room'] ? 'control-room'
    /* Starting a feature is a screen you go to, not the thing a project is.
       It has a route of its own, so an approved project opens on its checks
       rather than rendering the composer in their place. */
    : params.start ? 'start'
    : params.log ? 'log'
    : params.criterion ? 'criterion'
    : params.evidence ? 'evidence'
    /* `?criteria` was the requirement list. The map is that list with the
       connections put back, so an old link lands on it rather than on
       nothing. */
    : params.criteria ? 'map'
    : params.calls ? 'calls'
    : params.map ? 'map'
    : params.file ? 'file'
    /* `?work` was the code tab: the files, the choices and the disclosures on
       one page. The files are the map's right-hand column now, so that is
       where an old link lands. */
    : params.work ? 'map'
    : params.objections ? 'objections'
    /* `?security` was the second findings tab. Security is one screen's worth
       of angles rather than a screen of its own now, so an old link lands on
       the screen that holds them. */
    : params.security ? 'objections'
    : params.blindspots ? 'blindspots'
    : params.dependencies ? 'dependencies'
    : params.repairs ? 'repairs'
    : params.rework ? 'rework'
    : params.packet ? 'packet'
    : params.gates ? 'gates'
    : STEP_IDS.find((id) => flags.includes(id)) || null;
  /* `?gates=checks` names a screen and which of its four sections is open, the
     same shape as `?criterion=AC-1`. */
  const param = params.criterion
    || (typeof params.evidence === 'string' ? params.evidence : null) || params.file
    || (typeof params.gates === 'string' ? params.gates : null) || null;
  if (view !== state.view || param !== state.param) {
    state.view = view; state.param = param;
    state.draft = null; state.editing = null; state.roleDraft = {}; state.openRole = null;
    // A draft of the crew is the screen's own; leaving it is discarding it.
    state.ac = null;
    state.log = null; state.openRecord = null; state.record = null;
    /* An armed "Drop lint?" must not be waiting on the checks screen because
       someone opened the environment and came back -- and a re-survey that
       found nothing has been read by the time you leave the screen. */
    state.droppingGate = null;
    state.askedProposal = false;
  }
  if (params.edit && view === 'gates' && EDITABLE.includes(param)) {
    state.editing = param;
  }
  const [projectId = null, featureId = null] = parts;
  state.openClass = null;

  if (projectId !== state.projectId) {
    state.projectId = projectId;
    state.project = null;
    state.projectError = null;
    state.features = [];
  }
  if (featureId !== state.id || projectId !== state.projectId) {
    // A picked run is a seq in one feature's ledger and means nothing in another's.
    state.runSeq = null;
  }
  if (featureId !== state.id) {
    state.id = featureId;
    state.data = null;
    state.loadError = null;
    state.answers = {};
  }

  if (!projectId) {
    if (state.view === 'setup') {
      render();
      await Promise.all([loadProjects(), loadSetup().catch((e) => errorToast(e.message))]);
      render();
      return;
    }
    if (state.view === 'help') {
      render();
      await Promise.all([loadProjects(), loadHelp(state.helpTopic)]);
      render();
      return;
    }
    if (state.view === 'agent-config') {
      render();
      await Promise.all([loadProjects(), state.ac ? null : loadAgentConfig().catch((e) => {
        errorToast(e.message);
      })]);
      render();
      return;
    }
    if (state.view === 'roles') {
      // Paint the progress first, then fill it: `loadRoles` renders each part
      // as it lands, and waits only for the fast ones.
      state.crewLoad = state.crewLoad || {};
      render();
      await Promise.all([loadProjects(), loadRoles()]);
    } else {
      await loadProjects();
    }
    render();
    return;
  }

  // Tear down anything mounted into #main before overwriting it. Writing over a
  // reconciler's DOM behind its back leaves it diffing against nodes that are
  // no longer there, and it appends its next render instead of replacing.
  releaseGate2();
  main.innerHTML = '<div class="wrap"><p class="empty">Loading…</p></div>';
  // Written outside the cache render() compares against -- so the render right
  // after this must not mistake a leftover match for nothing having changed
  // and leave the placeholder on screen.
  lastMainHtml = null;
  await Promise.all([loadProjects(), loadProject()]);
  if (featureId) await refresh();
  if (state.view === 'log') await loadLog();
  render();
  window.scrollTo(0, 0);
  if (featureId && !state.view && ['awaiting_answers', 'awaiting_spec_approval']
      .includes((state.data || {}).state?.stage)) {
    loadIntakePlan();
  }
}
