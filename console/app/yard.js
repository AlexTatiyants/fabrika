/* The yard: every project on one screen, each lit by its checks, with the
   pill that says whether a person is needed. */

'use strict';

/* --------------------------------------------------------------- 0. yard */

/* THE TWO REGISTERS, ON ONE ROW.

   A project's lamp is lit by its checks and by nothing else -- the same rule
   the board runs on. Whether a *human* is required is a different fact, and it
   is said in ink by the pill beside it. A project can be green and still be
   waiting on you; that is the ordinary case at gate 0, and a design that lit
   one lamp for both would have no way to draw it. */
/* What the baseline says about this project's checks, counted the way the
   project's own nameplate counts them: over the gates, not over the rows of
   the report. `baseline.results` also carries a `setup[n]` row per environment
   step, so a project with five checks files nine results -- and a yard column
   headed CHECKS reading 9/9 beside a project page reading 5 is one word
   standing for two numbers. */
function projectChecks(p) {
  const gates = p.gates || [];
  const results = ((p.baseline || {}).results) || [];
  const byName = {};
  results.forEach((r) => { byName[r.name] = r; });
  const ok = (g) => { const r = byName[g.name]; return !!r && (r.passed || r.skipped); };
  return {
    measured: !!(gates.length && results.length),
    total: gates.length,
    green: gates.filter(ok).length,
  };
}

/* Two stages answer before the checks are consulted, because in both of them
   there is nothing to consult. A table rather than a pair of branches, in the
   idiom the stage pills already use. */
const STAGE_LAMP = {
  failed: ['red lit', 'the checks could not run'],
  surveying: ['reading', 'being read'],
};

function projectLamp(p) {
  if (STAGE_LAMP[p.stage]) return STAGE_LAMP[p.stage];
  const { measured, total, green } = projectChecks(p);
  if (!measured) return ['dark', 'not measured yet'];
  /* Over every row of the report, setup included: an environment that did not
     build stops the checks as surely as a check that will not start. */
  if (unrunnableGates(p.baseline).length) return ['red lit', 'the checks could not run'];
  return green === total
    ? ['green lit', 'checks green']
    : ['amber lit', 'checks ran, not all green'];
}

/* Who is needed, not how the run went. Empty for a project that is doing fine
   without you, which on a healthy floor is most of them. */
function projectFlag(stage) {
  if (stage === 'awaiting_approval') return '<span class="pill pill-accent">awaiting you</span>';
  if (stage === 'failed') return '<span class="pill pill-red">survey failed</span>';
  if (stage === 'surveying') return '<span class="pill">reading the repo</span>';
  return '';
}

/* One row of the ledger. The three facts sit in a wrapper that is
   `display: contents`, so on a wide row each lands in its own column of the
   shared grid and on a narrow one they collapse into a single line under the
   name -- see .prow-facts. */
function yardRow(p) {
  const [lamp, said] = projectLamp(p);
  const { measured, total, green } = projectChecks(p);
  const env = ((p.environment || {}).kind) || '';
  const n = p.feature_count;

  const checks = measured
    ? `<b>${green}</b>/${total}<span class="u"> checks</span>`
    : '<span class="none">—<span class="u"> not measured</span></span>';
  const feats = n
    ? `<b>${n}</b><span class="u"> feature${n === 1 ? '' : 's'}</span>`
    : '<span class="none">none<span class="u"> yet</span></span>';

  return `
    <a class="prow${p.stage === 'awaiting_approval' ? ' needs' : ''}"
       href="#/${encodeURIComponent(p.project_id)}">
      <span class="lamp-row ${lamp}" aria-hidden="true"></span>
      <span class="sr-only">${esc(said)}. </span>
      <span class="prow-id">
        <span class="prow-name">${esc(p.name || p.project_id)}</span>
        <span class="prow-path">${esc(p.repo)}</span>
      </span>
      <span class="prow-facts">
        <span class="prow-fact${measured ? '' : ' blank'}">${checks}</span>
        <span class="prow-fact${env ? '' : ' blank'}">${env
          ? esc(env) : '<span class="none">—</span>'}</span>
        <span class="prow-fact prow-feat">${feats}</span>
      </span>
      <span class="prow-flag">${projectFlag(p.stage)}</span>
    </a>`;
}

/* Said before the first press rather than after it: without a key the survey
   goes out anyway and comes back as the provider's 401. The crew page's own
   reading once it has one, the boot config's until then. A local server that
   takes no key reads as missing too, which is why the line says "unless". */
function keyNote() {
  const missing = state.provider ? !state.provider.key_present
    : !!state.config && state.config.api_key_present === false;
  if (!missing) return '';
  return `<p class="intake-key">No provider key is set, and the surveyor needs one to read
    anything. <a href="#/?roles">Set it on the crew page</a>, unless your provider is a local
    server that takes none.</p>`;
}

/* The first screen of the product, and for a while the only one: nothing is
   registered, so the intake is not a line in a header -- it is the page. */
function yardFirstRun() {
  return `
    <div class="wrap yard">
      <section class="yard-first">
        <h1>${icon('l-warehouse', 'page-mark')}<span>The Yard.</span></h1>
        <p class="defn">This is where your projects live, and nothing lives here yet. A project is a
          repository, the checks that say whether it is healthy, and the environment those checks
          run in. Point the factory at one on this machine and it will read it.</p>

        <div class="intake-plate" role="group" aria-labelledby="intake-label">
          <span class="sr-only" id="intake-label">Add a project</span>
          <label class="intake-label" for="project-path">Repository path</label>
          <input id="project-path" class="intake-field" type="text" spellcheck="false"
            placeholder="/Users/you/dev/your-project">
          <div class="intake-row">
            <div>
              <label class="intake-label" for="project-name">Name &mdash; optional</label>
              <input id="project-name" class="intake-field" type="text"
                placeholder="taken from the folder">
            </div>
            <button class="btn btn-primary" id="add-project">Survey it</button>
          </div>
          ${keyNote()}
          <p class="intake-note">New to this machine? <a href="${SETUP_HREF}">Set it up first</a>: Docker,
            an account, the harnesses and the crew, each checked.</p>
          <p class="intake-note">Nothing in your repository is modified: the surveyor reads it, and
            the checks run on an untouched checkout.
            <a href="#" data-help-open="adding-a-project">What happens next</a></p>
        </div>

        <ol class="yard-steps">
          <li><span>A surveyor <b>reads</b> the repository &mdash; what it is, what it is built with,
            and which checks it already runs.</span></li>
          <li><span>Those checks <b>run once</b>, on an untouched checkout of the base branch, inside
            the environment it proposed. That is what settles whether the commands work at all.</span></li>
          <li><span>You <b>approve</b> the checks and the environment. Until you do, nothing is built
            here.</span></li>
        </ol>
      </section>
    </div>`;
}

function yardScreen() {
  if (!state.projects.length) return yardFirstRun();

  /* Projects that need a person come first. Everything else keeps the order
     the registry gave it -- there is no second sort here, because a list that
     reorders itself while you are reading it is worse than one that does not
     rank at all. */
  const needs = (p) => (p.stage === 'awaiting_approval' ? 0 : p.stage === 'failed' ? 1 : 2);
  const projects = state.projects.slice().sort((a, b) => needs(a) - needs(b));

  return `
    <div class="wrap yard">
      <section class="yard-hero">
        <h1>${icon('l-warehouse', 'page-mark')}<span>The Yard.</span></h1>
        <p class="defn">This is where your projects live. A project is a repository, the checks that
          say whether it is healthy, and the environment those checks run in. Every feature is built
          in its own worktree, branched from the project and
          <b>isolated from every other feature</b>.
          <a href="#" data-help-open="how-fabrika-works">How Fabrika works</a></p>
      </section>

      <div class="intake-form" role="group" aria-labelledby="intake-label">
        <span class="intake-label" id="intake-label">Add a project</span>
        <input id="project-path" class="intake-field f-path" type="text" spellcheck="false"
          aria-label="Repository path" placeholder="/Users/you/dev/your-project">
        <input id="project-name" class="intake-field f-name" type="text"
          aria-label="Project name, optional" placeholder="Name &mdash; optional">
        <button class="btn btn-primary" id="add-project">Survey it</button>
      </div>
      ${keyNote()}

      <section class="section" style="margin:0 0 2.5rem">
        <div class="yard-ledger">
          <div class="yard-lh">
            <span></span>
            <span>Registered <span class="lh-count">${projects.length}</span></span>
            <span class="lh-checks">Checks</span>
            <span class="lh-env">Environment</span>
            <span class="lh-feat">Features</span>
            <span></span>
          </div>
          ${projects.map(yardRow).join('')}
        </div>

        <p class="yard-key">
          <span><i class="lamp-row green lit" aria-hidden="true"></i> checks green on an untouched checkout</span>
          <span><i class="lamp-row amber lit" aria-hidden="true"></i> checks ran, not all green</span>
          <span><i class="lamp-row red lit" aria-hidden="true"></i> the checks could not run</span>
          <span><i class="lamp-row dark" aria-hidden="true"></i> not measured yet</span>
        </p>
      </section>
    </div>`;
}
