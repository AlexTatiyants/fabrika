/* --------------------------------------------------------------------------
   The console.

   One hash route. The screen is chosen by the feature's stage, never by
   navigation, because the stage is the truth about what the human can do next
   and a nav bar would let them pretend otherwise.
   -------------------------------------------------------------------------- */

'use strict';

/* The page's handles, the one state object every screen reads, the links
   between screens, and the words for stages and bands. Loaded first. */

const main = document.getElementById('main');
const crumbsEl = document.getElementById('crumbs');
const mastNavEl = document.getElementById('mast-nav');
const toastEl = document.getElementById('toast');
const liveEl = document.getElementById('live');
const panelBody = document.getElementById('panel-body');
const panelScrim = document.getElementById('panel-scrim');
const panelStatusEl = document.getElementById('panel-status');
const panelSearchEl = document.getElementById('panel-search');
const panelFiltersEl = document.getElementById('panel-filters');
const featureBarEl = document.getElementById('featurebar');

const state = {
  param: null,
  projectId: null,
  project: null,
  // A project-level run in flight: the checks, or a reading. Held here because
  // nothing else records one -- no stage changes, and no record is written
  // until it is over.
  projectRun: null,
  projects: [],
  allFeatures: [],
  // What the server says about itself: which editor to offer, where the
  // evidence lives. Fetched once at boot; absent until it lands.
  config: null,
  view: null,
  draft: null,
  roles: null,
  roleDraft: {},
  provider: null,
  providerOpen: false,
  providerTest: null,
  openRole: null,
  // The Agent configuration screen: what the server says and the draft of it.
  ac: null,
  openRoute: null,
  // Which route's full model list is expanded. Per route, not global: two
  // cards open at once is a legitimate thing to want.
  openModels: {},
  // The gate-1 reading: what the plans have against what a run draws.
  planCheck: null,
  routes: null,
  correcting: false,
  intakePlan: null,
  allowDirty: false,
  discarding: false,
  /* The name of the check whose removal is being confirmed, if any. Dropping
     one discards the baseline, so it asks first. */
  droppingGate: null,
  decliningRec: null,
  adoptingRec: null,
  /* Whether the last request got no answer at all. Distinct from an error the
     server returned: one means the ledger said no, the other means nothing
     read it. */
  offline: false,
  /* Whether the reader asked for a proposal on this visit. A re-survey that
     found nothing is an answer to a question somebody asked, not a standing
     fact about the project. */
  askedProposal: false,
  reintaking: false,
  resurvey: null,
  projectError: null,
  stripOpen: null,
  docTab: {},
  editing: null,
  history: null,
  historyFilter: '',
  historyRecord: null,
  openQuestion: null,
  openCriterion: null,
  log: null,
  /* The waits are the bigger half of the clock and none of them are the
     factory's: with them on the axis every bar is a sliver of a run that was
     mostly a human being away. The default answers "how long did the work
     take", and the toggle puts the wall clock back. */
  hideWait: true,
  openRecord: null,
  record: null,
  id: null,
  data: null,
  features: [],
  answers: {},
  openClass: null,
  poller: null,
  pollEvery: 0,
  lastRefreshAt: 0,
};

const projectUrl = (p) => `/api/projects/${encodeURIComponent(p)}`;
/* A check's anchor in the checks editor. The router reads the fragment raw,
   so this has to survive being an element id without escaping: a gate name is
   free text and `pytest -q (fast)` is a legal one. */
const checkAnchor = (name) => `check-${String(name).replace(/[^A-Za-z0-9_-]+/g, '-')}`;
/* Where a thing is edited: on the tab that shows it, already in edit mode.
   Naming a check scrolls to it with the caret in its command -- the screen
   that tells you a command is wrong should hand you the field that holds it. */
const editHref = (p, tab, check = '') =>
  `#/${encodeURIComponent(p)}?gates=${tab}&edit${check ? `#${checkAnchor(check)}` : ''}`;
const ROLES_HREF = '#/?roles';
const CONTROL_ROOM_HREF = '#/?control-room';
const HELP_HREF = '#/?help';
const featureUrl = (p, f) => `/api/projects/${encodeURIComponent(p)}/features/${encodeURIComponent(f)}`;
const featureHref = (p, f) => `#/${encodeURIComponent(p)}/${encodeURIComponent(f)}`;
const logHref = (p, f) => `${featureHref(p, f)}?log`;

const MAT_ORDER = ['generated', 'mechanical', 'conventional', 'novel'];
const MAT_BLURB = {
  generated: 'Mechanical output of a tool or template.',
  mechanical: 'Forced by the language or framework. There was no choice to make.',
  conventional: 'The obvious way, following a convention this repo already had.',
  novel: 'A real choice. This is the part a human has to read.',
};

/* The five bands of the diagram, so the rail, the screens and the picture all
   name the same thing. A phase is which agent is running; a band is where that
   sits in the run. */
const BANDS = [
  { id: 'spec', label: 'spec', phases: ['scout', 'interrogator', 'spec_writer', 'spec_checker'], gate: 'spec review' },
  { id: 'build', label: 'build', phases: ['architect', 'plan_checker', 'workers', 'integrator'], gate: 'plan review' },
  { id: 'verify', label: 'verify', phases: ['oracle'] },
  { id: 'review', label: 'review', phases: ['gates', 'attribution', 'qa', 'breaker', 'review',
    'arbiter', 'repairers', 'simplifier', 'rapporteur'], gate: 'feature review' },
];

function bandOf(d) {
  const phases = d.phases || [];
  const running = phases.find((p) => p.status === 'running');
  const failed = phases.find((p) => p.status === 'failed');
  const active = running || failed;
  if (active) {
    const band = BANDS.find((b) => b.phases.includes(active.name));
    return { band: band ? band.id : STAGE_BAND[d.state.stage], phase: active.name,
             running: !!running, failed: !!failed };
  }
  const done = phases.filter((p) => p.status === 'done');
  const last = done.length ? done[done.length - 1].name : '';
  return { band: STAGE_BAND[d.state.stage] || 'spec', phase: '', running: false,
           failed: d.state.stage === 'failed', after: last };
}
