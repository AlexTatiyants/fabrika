/* The ledger: every record a feature wrote, named in words, with what each
   one cost. */

'use strict';

/* ------------------------------------------------------------ the ledger */

const KIND_WORDS = {
  call: 'model call',
  state: 'state', scout: 'scout', interrogation: 'interrogation', correction: 'correction',
  spec: 'spec', approval: 'approval', plan: 'plan', worker: 'worker',
  spec_testability: 'what this project cannot verify about this spec',
  spec_draft: 'the spec before the spec checker read it',
  spec_check: 'what the spec checker objected to, and what came of it',
  plan_check: 'what the plan checker objected to',
  cut_review: 'whether the cut needed a person, and why',
  cut_ruling: 'what a person ruled at plan review',
  attribution_wiring: 'whether this project can name a failing test',
  blind_unplaceable: 'blind test files this project has nowhere to run',
  regressions: 'acceptance tests from features you already accepted',
  integration: 'integration', oracle: 'oracle', writes: 'writes', gates: 'checks',
  diffstat: 'how much each file actually changed',
  headroom: 'what each plan had left before this run started',
  plan_draw: 'how much of each plan this run actually used',
  deferred: 'held until a plan has room to finish it',
  deferral_cancelled: 'you stopped waiting for the plan',
  unit_env: 'whether this unit had a running system to test against',
  qa: 'qa', trace: 'trace', review: 'review', review_sample: 'review sample',
  packet_raw: 'packet (raw)', packet: 'packet', usage: 'usage', ruling: 'ruling',
  packet_file: 'the packet was written onto the branch',
  as_built: 'the as-built, read at one of this feature\'s commits',
  as_built_failed: 'an as-built reading could not be made',
  as_built_skipped: 'what this changes in the system could not be worked out',
  system_change: 'what this changes in the system',
  spec_file: 'the frozen spec was written onto the branch',
  verdict: 'verdict',
  screens: 'what the suite saw',
  traces: 'recordings of the browser runs',
  trace_too_big: 'a recording was too large to keep',
  unclaimed: 'unaccounted for', flag: 'you flagged', flag_retag: 'you retagged',
  flags_seeded: 'your flags entered the loop', rework_spend: 'rework spend',
  dispatch: 'you dispatched', branch_reset: 'branch reset',
  revalidate: 'you asked for it to be verified again',
  oracle_retry: 'the blind suite named no criterion, so it was asked again',
  verify_lane_failed: 'the verify lane failed; the build was kept',
  oracle_rerun: 'the last blind suite could not load, so it was written again',
  step: 'a step ran',
  check_runs: 'you chose when a check runs',
  check_timing: 'how long each check took in the final pass',
  check_fixer: 'a check was caught rewriting files, so it runs as a fixer',
  repair_unplanned: 'the repair round planned no work for it, so it kept its attempt',
  repair_misrouted: 'the fix belongs in a file a repairer may not write, so it went to the oracle',
  repair_waiting: 'more findings than the round had slots — these keep their attempts',
  panel_skipped: 'the exit review was skipped: work was already queued that this run could not reach',
  fixed: 'a fixer rewrote files, committed on their own',
  oracle_revision: 'the oracle was shown errors in its own files and fixed them',
  rework_rate_limited: 'a plan reached its limit, so the round did not run',
  rework_reopened: 'the panel on the way out found more to repair, so the loop went round again',
  rebuild: 'you asked for it to be built again, over the same spec',
  breaker_suite: 'breaker suite', breaker: 'breaker run',
  breaker_session: 'the breaker wrote its probes',
  oracle_session: 'the oracle wrote its suite',
  breaker_failed: 'breaker failed',
  arbiter: 'dispositions', arbiter_failed: 'arbiter failed',
  arbiter_retry: 'findings came back unruled, so the arbiter was asked again',
  repair_plan: 'repair plan', repair: 'repair', repair_failed: 'repair failed',
  repair_capped: 'repairs deferred', repair_noop: 'harness edited nothing',
  rework_failed: 'repair loop stopped', repair_prompt: 'repair prompt',
  resurvey_unchanged: 'proposed what was already true, and was not shown',
  simplify_prompt: 'simplify prompt', simplify_failed: 'simplify pass failed',
  simplify_skipped: 'nothing to simplify against',
  review_resumed: 'panel finished after an interruption',
  recheck: 're-check', recheck_failed: 're-check failed',
  rework: 'repair loop', rework_state: 'finding ledger',
  rework_reverted: 'round reverted',
  setup: 'environment setup',
  resurvey: 're-survey proposal', resurvey_ruling: 're-survey ruling',
  recommendation_ruling: 'suggestion turned down',
  recommendation_adopted: 'suggestion became a check',
  recommendation_reinstated: 'suggestion allowed again',
  check_ratcheted: 'check held at its reading',
  check_held_on_new_code: 'check held on new code',
  proposal_check_ruling: 'proposed change to a check ruled on',
  proposal_check_undone: 'accepted change to a check undone',
  dependencies: 'dependencies read from the lockfiles',
  dependency_inventory: 'what the project depends on, read again',
  diagnosis: 'why a red check is red, worked out',
  guide_ruling: 'a guide, approved or set aside',
  guides_delivered: 'the guides an agent was handed, or its harness could load',
  guide_contradictions: 'where the code does otherwise than a guide',
  guide_draft: 'a guide drafted from what features observed',
  guide_draft_failed: 'a guide could not be drafted',
  guide_written: 'a guide, written into the repository',
  guide_offer_declined: 'a guide Fabrika offered, turned down',
  guide_assignment_refused: 'a guide the architect named that is not approved',
  skills_dir_set: 'where Fabrika writes skills, chosen',
  diagnosis_fix: 'a fix for a red check, applied',
  family_ruling: 'family of checks gone without',
  family_reinstated: 'family of checks asked about again',
  recommendation_raised: 'a run found something to suggest',
  gate_ruling: 'check turned down', gate_reinstated: 'check put back',
  resurvey_failed: 're-survey failed',
  report_search: 'asked the test runners about reports',
  report_search_failed: 'asking the test runners about reports failed',
  // The project ledger's own kinds, which only the project ledger view
  // renders.
  project: 'project', survey: 'survey', baseline: 'baseline',
  scaffold: 'scaffolding applied', dockerfile: 'Dockerfile committed', scaffold_refused: 'scaffolding refused',
  environment_problems: 'environment problems',
  attribution: 'attribution', attribution_failed: 'attribution unavailable',
  // Without a name here a kind falls through to its raw key. A failure record
  // is exactly when a reader needs the ledger to be legible, so the *_failed
  // kinds matter more than the rest of this list, not less.
  answers: 'answers', digest: 'repo digest', drift: 'working tree drift',
  reintake: 're-read the repository', scout_slice: 'scout slice',
  scout_failed: 'scout failed', review_failed: 'review agent failed',
  worker_prompt: 'worker prompt', integration_prompt: 'integrator prompt',
  sandbox_release_failed: 'sandbox release failed',
  preview: 'the running app',
};

/* A run's tokens and a run's characters both land in the millions, where
   `3100k` is a number the reader has to divide before it means anything. */
function bytes(n) {
  if (!n) return '—';
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  return n > 9999 ? `${Math.round(n / 1000)}k` : String(n);
}

/* One call can cost a fifth of a cent and a run can cost twenty dollars, and
   both have to be legible in the same column, so the precision follows the
   number. Nothing spent and nothing known both print as an em dash -- the
   caller decides which of those it is, because they are not the same claim. */
/* A column of station totals, where `$0.310` beside `$2.06` is a ragged edge
   rather than precision. Fractions of a cent are for one call, not for a sum. */
function usd2(n) {
  const v = Number(n) || 0;
  return v ? `$${v.toFixed(2)}` : '';
}

function usd(n) {
  const v = Number(n) || 0;
  if (!v) return '—';
  if (v >= 1) return `$${v.toFixed(2)}`;
  if (v >= 0.01) return `$${v.toFixed(3)}`;
  return `$${v.toFixed(4)}`;
}

/* What one exchange cost, as recorded on it. Runs from before the ledger
   carried costs have none: absent, not zero. */
/* "$0.00 of $10.00" over a run that did every unit of work on a subscription is
   correct arithmetic and a sentence meaning the opposite of what it says. The
   budget can only stop work somebody is charged dollars for; an agent on a
   subscription is charged none, so the ceiling never applied to it.

   The dollars stay here, unlike in the call tree's counters: this note exists
   to answer "did that count against my ceiling", which is a question about
   money, and it is already said in the conditional. */
function unbilledNote(rw) {
  const roles = (rw && rw.unbilled_roles) || [];
  if (!roles.length) return '';
  const worth = rw.notional_usd
    ? ` — worth about $${Number(rw.notional_usd).toFixed(2)} had it been billed` : '';
  return `${roles.length} agent${roles.length === 1 ? '' : 's'} ran on a subscription `
    + `(${rw.unbilled_turns || 0} turn${rw.unbilled_turns === 1 ? '' : 's'})`
    + `${worth}, which this budget cannot measure`;
}

/* What one call cost in money, which is not what every route reports.

   A route metered in turns is a plan: the figure its CLI hands back is what
   the same work would have cost on an API, and nobody was charged it. Shown in
   a column headed Cost it reads as spend -- and because `claude -p` reports one
   per call and `codex` reports none, the column also reads as though one
   vendor were billing and the other giving it away, when both are turns
   against the same kind of plan.

   The record answers this itself: `call_entry` writes the answer, and the
   ledger endpoint fills it in for rows written before it did. Absent means
   genuinely unanswerable -- a route since renamed or dropped -- and the
   reported figure is all there is. */
function callSpend(c) {
  if (!c) return 0;
  return c.billed === false ? 0 : Number(c.cost_usd) || 0;
}


const recordCost = (r) => Number((r.meta || {}).cost_usd) || 0;
const logCost = (log) => (log || []).reduce((a, r) => a + recordCost(r), 0);

/* The project's own ledger: the survey, the baselines, the approvals and every
   re-survey proposal, in write order. Same rows as a feature's log, without the
   prompt halves -- a project record is a result, not an exchange. */
function projectLogScreen() {
  const p = state.project.project;
  const log = state.log;
  if (!log) return projectBar(p, { at: 'log' }) + `<div class="wrap"><p class="empty">Loading…</p></div>`;

  const rows = log.map((r) => {
    const open = state.openRecord === r.seq;
    return `
      <button class="log-row ${open ? 'open' : ''}" data-open-record="${r.seq}"
        aria-expanded="${open}">
        <span class="lr-seq mono">${String(r.seq).padStart(3, '0')}</span>
        <span class="lr-time mono">${esc(r.at.slice(0, 19).replace('T', ' '))}</span>
        <span class="lr-kind">${esc(KIND_WORDS[r.kind] || r.kind)}</span>
        <span class="lr-role mono">${esc(r.role || '')}</span>
        <span class="lr-model mono">${esc(r.model || '')}</span>
        <span class="lr-num mono">${bytes(r.payload_chars)}</span>
        <span class="rr-chev">${open ? '−' : '+'}</span>
      </button>` + (open && state.record && state.record.seq === r.seq
        ? `<div class="log-detail"><pre>${esc(
            JSON.stringify(state.record.payload, null, 2))}</pre></div>`
        : open ? '<div class="log-detail"><p class="empty">Loading…</p></div>' : '');
  }).join('');

  return projectBar(p, { at: 'log' }) + `
    <div class="wrap">
      <p class="small"><a href="#/${encodeURIComponent(p.project_id)}?gates=history">← History</a></p>
      <p class="p-verdict">${esc(log.length)} records. Append-only: nothing here was edited,
        and a survey superseded by a later one is still on the page.</p>
      <div class="log-rows">${rows}</div>
    </div>`;
}

function logScreen() {
  const d = state.data;
  const log = state.log;
  if (!log) return `<div class="wrap"><p class="empty">Loading…</p></div>`;

  const agentRecords = log.filter((r) => r.role && r.role !== 'orchestrator' && r.role !== 'human');
  const totalIn = agentRecords.reduce((a, r) => a + (r.prompt_chars || 0), 0);
  const totalOut = agentRecords.reduce((a, r) => a + (r.payload_chars || 0), 0);
  const missing = agentRecords.filter((r) => !r.has_prompt).length;
  // The records add up to what the run has spent so far, which is a number a
  // finished run also carries as one `usage` total. Prefer the records: they
  // exist while the run is still going, and they say which call spent it.
  const spent = logCost(log);
  const summary = Number(((d.usage || {}).total_cost) || 0);
  const priced = agentRecords.filter((r) => recordCost(r) > 0).length;

  const rows = log.map((r) => {
    const open = state.openRecord === r.seq;
    const isAgent = r.role && r.role !== 'orchestrator' && r.role !== 'human';
    const cost = recordCost(r);
    const head = `
      <button class="log-row ${open ? 'open' : ''} ${isAgent ? 'agent' : ''}"
        data-open-record="${r.seq}" aria-expanded="${open}">
        <span class="lr-seq mono">${String(r.seq).padStart(3, '0')}</span>
        <span class="lr-time mono">${esc(r.at.slice(11, 19))}</span>
        <span class="lr-kind">${esc(KIND_WORDS[r.kind] || r.kind)}</span>
        <span class="lr-role mono">${esc(r.role || '')}</span>
        <span class="lr-model mono">${esc(r.model || '')}</span>
        <span class="lr-num mono" title="characters the agent was given">${bytes(r.prompt_chars)}</span>
        <span class="lr-num mono" title="characters it returned">${bytes(r.payload_chars)}</span>
        <span class="lr-num mono lr-cost" title="${cost
          ? 'what this exchange cost'
          : 'not recorded — this ran before costs were, or was bought by another tool'
          }">${cost ? usd(cost) : '—'}</span>
        <span class="rr-chev">${open ? '−' : '+'}</span>
      </button>`;

    if (!open) return head;
    const rec = state.record && state.record.seq === r.seq ? state.record : null;
    if (!rec) return head + `<div class="log-detail"><p class="empty">Loading…</p></div>`;

    return head + `
      <div class="log-detail">
        ${rec.system ? `<details class="raw"><summary>system prompt · roles/${esc(rec.role)}.md · ${bytes(rec.system.length)}</summary>
          <pre>${esc(rec.system)}</pre></details>` : ''}
        ${rec.prompt ? `<details class="raw" open><summary>what it was given · ${bytes(rec.prompt.length)}</summary>
          <pre>${esc(rec.prompt)}</pre></details>`
          : (r.role && r.role !== 'orchestrator' && r.role !== 'human'
             ? `<p class="small dim">No prompt recorded — this ran before prompts were captured.</p>` : '')}
        <details class="raw" open><summary>what it returned · ${bytes(r.payload_chars)}</summary>
          <pre>${esc(JSON.stringify(rec.payload, null, 2))}</pre></details>
        ${Object.keys(rec.meta || {}).length
          ? `<p class="small dim mono">meta: ${esc(JSON.stringify(rec.meta))}</p>` : ''}
      </div>`;
  }).join('');

  return `
    <div class="wrap wide">
      <section class="verdict-strip">
        <p class="eyebrow"><a href="#/${encodeURIComponent(d.state.project_id)}" style="text-decoration:none">${esc(d.state.project_id)}</a>
          · <a href="${featureHref(d.state.project_id, d.state.feature_id)}" style="text-decoration:none">${esc(d.state.title)}</a>
          · log</p>
        <h1 class="verdict-headline">Every exchange, in order.</h1>
        <p class="budget">${log.length} records · ${agentRecords.length} agent calls ·
          <b>${bytes(totalIn)}</b> characters in, <b>${bytes(totalOut)}</b> out${
          spent ? ` · <b>${usd(spent)}</b> spent` : summary ? ` · <b>${usd(summary)}</b> spent` : ''}${
          missing ? ` · <span class="test-bad">${missing} call(s) predate prompt capture</span>` : ''}</p>
        ${spent && priced < agentRecords.length ? `<p class="small dim">${
          agentRecords.length - priced} of ${agentRecords.length} calls carry no cost — they ran
          before costs were recorded, or were bought by the build harness rather than by the
          factory.</p>` : ''}
        ${!spent && summary ? `<p class="small dim">One total for the run: this ledger predates
          per-call costs, so no row can say which call spent it.</p>` : ''}
      </section>

      ${timeline(d, log, { waitToggle: true })}

      <div class="log-row head">
        <span>seq</span><span>time</span><span>record</span><span>agent</span><span>model</span>
        <span class="lr-num">in</span><span class="lr-num">out</span>
        <span class="lr-num">cost</span><span></span>
      </div>
      ${rows}

      <section class="section discard">
        <p class="eyebrow">Discard</p>
        ${state.discarding ? `
          <div class="card needs-key">
            <p class="small">This removes the ledger above — every prompt, every response, the
              packet — and the worktree. <b>The branch
              <span class="mono">${esc((d.sandbox || {}).branch || 'none')}</span> is kept</b>, so
              anything built survives in git.</p>
            <div class="role-grid" style="margin-top:.6rem">
              <label class="field-inline"><input type="checkbox" id="discard-branch">
                <span>delete the branch too — this loses the code</span></label>
              <button class="btn btn-sm btn-quiet" id="cancel-discard">Cancel</button>
              <button class="btn btn-sm btn-primary" id="confirm-discard">Discard it</button>
            </div>
          </div>`
        : `<button class="btn btn-sm btn-quiet" id="start-discard">Discard this feature</button>
           <p class="small dim" style="margin-top:.4rem;max-width:66ch">Removes its evidence and
             its checkout. The branch is kept unless you say otherwise.</p>`}
      </section>
    </div>`;
}
