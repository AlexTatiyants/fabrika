/* Editing each thing on the tab that shows it, the to-dos a reading suggests,
   what was turned down, and projectScreen, which puts the project page together. */

'use strict';

/* ------------------------------------------------------------------ editing

   Each thing is edited on the tab that shows it, not on a settings page in
   another part of the application, with tabs of its own repeating three of
   the project's, opening on paragraphs nobody came for. Checks, Environment
   and Survey each have an Edit in their heading: the tab becomes
   its form, with one bar saying so -- Cancel, Save, and one sentence on what
   saving costs. A change to what the checks run in, or run, measures them
   again at once, so a broken edit is found in minutes, not by the next
   feature. The timed refresh pauses while a form is open: it would rebuild
   the fields under the person typing in them. */
const EDITABLE = ['checks', 'environment', 'survey'];

function editButton(tab) {
  const p = (state.project || {}).project || {};
  if (state.editing === tab || p.stage === 'surveying') return '';
  return `<button type="button" class="btn btn-sm q-edit" data-edit-tab="${tab}">Edit</button>`;
}

function editBar(tab) {
  const project = tab === 'survey';
  return `<div class="ed-bar"><b>Editing ${project ? 'the project' : `the ${tab}`}</b>
      <span class="grow">${project
        ? 'Changing the repository or the branch measures the checks again before features can start here.'
        : 'Saving measures the checks again before features can start here.'}</span>
      <button type="button" class="btn btn-sm btn-quiet" data-edit-cancel="1">Cancel</button>
      <button type="button" class="btn btn-sm btn-primary" data-edit-save="1">${
        project ? 'Save' : 'Save and run checks'}</button></div>`;
}

function edRow(label, control, hint = '') {
  return `<div class="ed-row"><div class="ed-l">${esc(label)}</div><div class="ed-f">${control}${
    hint ? `<span class="hint">${hint}</span>` : ''}</div></div>`;
}

function edInput(path, value, attrs = '') {
  return `<input class="answer-field" data-draft="${esc(path)}" value="${esc(value ?? '')}" spellcheck="false" ${attrs}>`;
}

function edLines(path, lines, rows) {
  return `<textarea class="answer-field code" rows="${rows}" data-draft="${esc(path)}"
    spellcheck="false" placeholder="one per line">${esc((lines || []).join('\n'))}</textarea>`;
}

function environmentEditor() {
  const env = state.draft.environment;
  const compose = env.kind === 'compose';
  const main = [
    edRow('Runs in', `<select class="answer-field" data-draft="environment.kind" data-rerender="1">${
      ENV_KINDS.map(([k, label]) => `<option value="${k}" ${env.kind === k ? 'selected' : ''}>${
        esc(label)}</option>`).join('')}</select>`),
    compose ? edRow('Compose file', edInput('environment.compose_file', env.compose_file)) : '',
    compose ? edRow('Service', edInput('environment.compose_service', env.compose_service),
      'The service the checks run in.') : '',
    env.kind === 'reuse' ? edRow('Image', edInput('environment.image', env.image)) : '',
    edRow('Installed first', edLines('environment.setup', env.setup, 3),
      'One command per line. Runs with the network before the checks, which run without it.'),
    edRow('Before each check session', edLines('environment.test_prepare', env.test_prepare, 2),
      'Puts data back before the checks, if tests need it.'),
    previewEditor(env),
  ].join('');
  const more = [
    env.kind !== 'reuse' ? edRow('Image tag', edInput('environment.image', env.image)) : '',
    edRow('Working directory', edInput('environment.workdir', env.workdir || '/work')),
  ].join('');
  /* Read-only. A Dockerfile is edited where it lives: in the repository once
     it has been moved there, and by a re-survey until then. */
  const dockerfile = env.dockerfile ? `<details class="ed-more"><summary>The Dockerfile · read-only</summary>
      <div class="ed-ro"><p class="hint">${['repo', 'project'].includes(((state.project || {}).dockerfile || {}).source)
        ? `Edit <span class="mono">${esc(state.project.dockerfile.path)}</span> in your repository and commit it.`
        : 'Fabrika\'s own copy. To edit it, move it into your repository: the button is on this tab, outside Edit.'}</p>
        <pre class="hz-pre">${esc(env.dockerfile)}</pre></div></details>` : '';
  return `${editBar('environment')}
    ${environmentProblems(state.project.environment_problems)}
    <div class="ed-form">${main}
      ${more ? `<details class="ed-more"><summary>More: image tag, working directory</summary>
        <div class="ed-form">${more}</div></details>` : ''}
      ${dockerfile}</div>`;
}

/* What a person opens at review, set by hand when the survey could not say. */
function previewEditor(env) {
  const pv = env.preview || {};
  const extra = String(pv._lines || '').split('\n').map((l) => l.split(':')[0].trim()).filter(Boolean);
  const names = [...new Set([...(env.services || []).map((v) => v.name), ...extra, pv.open]
    .filter(Boolean))];
  return [
    edRow('A person opens', `<select class="answer-field" data-draft="environment.preview.open">
      <option value="" ${pv.open ? '' : 'selected'}>nothing: there is no app to open</option>${
      names.map((n) => `<option value="${esc(n)}" ${pv.open === n ? 'selected' : ''}>${esc(n)}</option>`)
        .join('')}</select>`,
      'The service opened in a browser at review. <a href="#" data-help-open="running-the-app">How it works</a>'),
    edRow('Starting at', edInput('environment.preview.path', pv.path || '/'), 'A path, such as / or /app.'),
    edRow('Also started for a person', `<textarea class="answer-field code" rows="2"
      data-draft="environment.preview._lines" spellcheck="false"
      placeholder="web: npx vite --host 127.0.0.1 --port $FACTORY_PORT_WEB --strictPort">${
      esc(pv._lines || '')}</textarea>`,
      'Anything the tests don\'t need but a person does, one per line as <span class="mono">name: command</span>. '
      + 'Bind 127.0.0.1 and take the port from <span class="mono">$FACTORY_PORT_&lt;NAME&gt;</span>. Save, then '
      + 'close and reopen Edit to choose it above.'),
  ].join('');
}

function checksEditor() {
  const kinds = (state.project || {}).check_kinds || {};
  const rows = state.draft.gates.map((g, i) => {
    const kind = g._runs || kinds[g.name] || 'light';
    return `<div class="gate-ed" id="${esc(checkAnchor(g.name))}">
      <input class="answer-field mono" data-draft="gates.${i}.name" value="${esc(g.name)}"
        placeholder="name" aria-label="Check name" spellcheck="false">
      <input class="answer-field mono" data-draft="gates.${i}.command" value="${esc(g.command)}"
        placeholder="command, from the repository root" aria-label="Command" spellcheck="false">
      <select class="answer-field" data-draft="gates.${i}.family" aria-label="What it asks">
        <option value="" ${g.family ? '' : 'selected'}>not set</option>
        ${['structure', 'quality', 'tests'].map((f) => `<option value="${f}" ${
          g.family === f ? 'selected' : ''}>${f}</option>`).join('')}</select>
      ${kind === 'fixer' ? '<span class="dim small">first, as a fixer</span>'
        : `<select class="answer-field" data-draft="gates.${i}._runs" aria-label="When it runs">
          <option value="light" ${kind !== 'heavy' ? 'selected' : ''}>every round</option>
          <option value="heavy" ${kind === 'heavy' ? 'selected' : ''}>once, at the end</option></select>`}
      <button type="button" class="ed-x" data-remove-gate="${i}" title="Remove ${esc(g.name)}"
        aria-label="Remove ${esc(g.name)}">×</button></div>`;
  }).join('');
  return `${editBar('checks')}
    <div class="gate-ed gate-ed-h"><span>Name</span><span>Command, from the repository root</span>
      <span>Asks</span><span>Runs</span><span></span></div>
    ${rows}
    <button type="button" class="btn btn-sm gate-add" id="add-gate">+ Add a check</button>`;
}

function surveyEditor() {
  const d = state.draft;
  const live = (state.project || {}).live_features || [];
  return `${editBar('survey')}
    <div class="ed-form">
      ${edRow('Name', edInput('name', d.name))}
      ${edRow('Repository', edInput('repo', d.repo, live.length ? 'disabled' : ''),
        live.length ? `Can't move while ${live.length} feature${live.length === 1 ? ' is' : 's are'} in flight.` : '')}
      ${edRow('Features branch from', edInput('base_ref', d.base_ref))}
      ${edRow('Digest budget', edInput('digest_budget', d.digest_budget, 'type="number"'),
        'Characters of the repository the builders read.')}
    </div>`;
}

function saveEdits() {
  const d = state.draft;
  const p = state.project.project;
  if (d.gates.some((g) => !String(g.name).trim() || !String(g.command).trim())) {
    toast('Every check needs a name and a command.');
    return undefined;
  }
  const kinds = (state.project || {}).check_kinds || {};
  const gates = d.gates.map(({ _runs, ...g }) => g);
  const environment = savedEnvironment(d.environment, p.environment);
  const same = (x, y) => JSON.stringify(x) === JSON.stringify(y);
  const rerun = d.repo !== p.repo || d.base_ref !== p.base_ref
    || !same(gates, p.gates || []) || !same(environment, p.environment || {});
  const runs = d.gates.filter((g) => g._runs && g._runs !== (kinds[g.name] || 'light'));
  return withBusy('Saving.', async () => {
    await api(projectUrl(state.projectId), {
      method: 'PATCH',
      body: JSON.stringify({
        name: d.name, repo: d.repo, base_ref: d.base_ref,
        digest_budget: Number(d.digest_budget) || 120000,
        gates, environment,
      }),
    });
    for (const g of runs) {
      await api(`${projectUrl(state.projectId)}/checks/runs`, {
        method: 'POST', body: JSON.stringify({ name: g.name, runs: g._runs }),
      });
    }
    state.draft = null;
    state.editing = null;
    await refreshProject();
    if (rerun) runBaseline();
    toast(rerun ? 'Saved. Running the checks again.' : 'Saved.');
  });
}

/* A to-do, inside the row or the section it belongs to.

   On another screen -- the survey tab -- each would be three clicks from the
   thing it fixes, and a reader looking at "typecheck cannot run" would never
   meet the reading that says how to make it run. The provenance line is
   there because the sentence below it is a model's words and everything else on
   this page is measured; a reader is entitled to know which is which. */
function todoCard({ src, does, how, note, gain, dx, acts, after }) {
  return `
    <div class="todo">
      ${src ? `<p class="todo-src">${esc(src)}</p>` : ''}
      ${does ? `<p class="todo-does">${does}</p>` : ''}
      ${how ? `<p class="todo-how">${esc(how)}</p>` : ''}
      ${note ? `<p class="todo-note">${note}</p>` : ''}
      ${gain ? `<p class="todo-gain">${TICK}${gain}</p>` : ''}
      ${dx || ''}
      <div class="acts">${acts}</div>
      ${after || ''}
    </div>`;
}

/* Which question a suggestion belongs under. The routing is the `kind` the
   reading already assigns from a closed set -- never the words in its title,
   and never a tool's name. `tests` is the only one about being able to verify
   new work; everything else is about a check. */
function todoSection(rec) {
  /* A suggestion that would make a check reporting what it measured -- coverage
     -- is a check, and sits with the other checks under Tests. The Tests tab is
     for what lets a test be written at all: a runner, a fixture, a way to clean up. */
  return (rec || {}).kind === 'tests' && !(rec || {}).report_format ? 'tests' : 'checks';
}

/* Served ready-made, never re-derived here. Deciding on the page which
   suggestion is which -- and whether it has been turned down -- would be a
   second implementation of a question the server already answers, and the two
   would disagree silently: a card that will not decline, or one that stays
   declined and comes back anyway. */
function liveRecommendations() { return ((state.project || {}).recommendations) || []; }

/* Everything a reading suggested for one question, as to-dos under it. */
/* Naming it is the human's, and so is the command.

   The command is prefilled from `would_gate` and is editable because the
   schema asks for one command and a reading does not always write one: on one
   project two of four were two commands joined by the word "and", and one
   carried a parenthetical aimed at a person. Prefilling it and letting it be
   corrected is honest about which of those it is; adding it unread would put
   prose on the check list. */
function adoptForm(r) {
  return `
    <div class="objection">
      <label class="small dim" for="adopt-name" style="display:block">Call it what the other
        checks are called &mdash; short, and not a sentence.</label>
      <input class="inp" id="adopt-name" type="text" style="width:100%;margin-top:.25rem"
        value="${esc(r.kind || '')}" spellcheck="false">
      <label class="small dim" for="adopt-cmd" style="display:block;margin-top:.6rem">The command.
        The reading wrote this without running it, and sometimes writes two commands joined by
        &ldquo;and&rdquo;, or a note to a person &mdash; <b>read it before you add it</b>.</label>
      <input class="inp mono" id="adopt-cmd" type="text" style="width:100%;margin-top:.25rem"
        value="${esc(r.would_gate || '')}" spellcheck="false">
      ${/* Said here because the order matters and nothing else says it. A check
           is how a repository stays fixed, not how it gets fixed: adopt one
           before the work is done and it is red on untouched code, which is
           the state gate 0 exists to refuse. Four were adopted on one project
           before any of the work behind them, and all four went red -- two
           because the command was prose, two because they were true. */''}
      <p class="small dim" style="margin-top:.6rem"><b>Add this after the work is done, not
        before.</b> A check says the repository still does this; it will be red until it is true.
        Adding one sends the project back to repo ready, where it is measured on untouched code with
        the rest, and nothing is built here until you approve again.</p>
      <div class="d-actions" style="margin-top:.6rem">
        <button class="btn btn-sm btn-quiet" id="cancel-adopt-rec">Cancel</button>
        <button class="btn btn-sm btn-primary" data-confirm-adopt-rec="${esc(r.key)}"
          >Add it</button>
      </div>
    </div>`;
}

function recTodo(r, i) {
  /* Folded, and open when a form inside it is. Two suggestions expanded pushed
     seven green checks off the screen -- and the checks are the answer to the
     question this section asks. What is behind the fold is the case for the
     suggestion; what is on the row is the suggestion. A reader scanning the
     list is doing the second and can ask for the first. */
  const open = state.adoptingRec === r.key || state.decliningRec === r.key;
  return `
    <details class="rec-why"${open ? ' open' : ''}>
      <summary><span class="ts-more">why, and what it would add</span></summary>
      ${recBody(r, i)}
    </details>`;
}

/* The handoff. A suggestion is work somebody has to do, and for the kind this
   reading produces -- a lockfile, an audit step, a coverage reporter -- the
   pipeline is the wrong size of tool: seventeen phases, a spec a human freezes,
   and a verify lane whose share of the work is a blind agent writing a test
   that greps a TOML file. One bundle of six cost 6.4 million tokens.

   So this hands over what a session needs and gets out of the way. What it
   cannot hand over is certainty: `why`, `evidence` and `how` are a model's
   reading of this repository and it ran nothing to get them. One said an audit
   command "needs nothing installed" when that command exits 1 on this tree. The
   prompt says so, because a session that believes a bad premise spends real
   time on it.

   The checks go in because they are the bar and because they are measured --
   the one part of this text that is not a claim. */
function recPrompt(r) {
  const p = state.project.project;
  const gates = p.gates || [];
  const out = [
    `# ${r.title || 'A change to this repository'}`, '',
    `Work in ${p.repo}, on the ${p.base_ref || 'default'} branch.`, '',
    '## What is missing', '', r.why || '(not stated)', '',
  ];
  if (r.evidence) out.push('## What the reading found', '', r.evidence, '');
  if (r.how) out.push('## Where to start', '', r.how, '');
  out.push(
    '## What has to still pass', '',
    gates.length
      ? 'These are the commands this project is checked by. They pass today — run them '
        + 'yourself before you call this done, and get them green:'
      : 'This project has no checks configured, so nothing here will measure your change.',
    '');
  gates.forEach((g) => out.push(`- \`${g.command}\``));
  if (r.would_gate) {
    /* Addressed to whoever does the work, and they are outside this system:
       they have one repository and no idea this tool exists. What becomes of the
       command afterwards -- adopting it, wiring it into what judges a feature --
       is a decision taken here, by a human, and saying so in a brief handed to
       them is describing a machine they cannot see. */
    out.push('', '## Afterwards', '',
      'Once this works, this command should pass:', '',
      `    ${r.would_gate}`, '',
      'Leave it working. Nothing else here depends on you running it again.');
  }
  out.push('', '---', '',
    'The three sections above are a reading of this repository, not a set of verified '
    + 'facts: nothing was run to produce them, and one of them has been wrong before. '
    + 'Check the claims against the code before you act on them, and say so if one does '
    + 'not hold.');
  return out.join('\n');
}

function recBody(r, i) {
  return todoCard({
      src: `from the reading · ${r.kind || 'other'}`,
      does: esc(firstSentence(r.why || '')),
      how: r.how || '',
      /* No `gain`. It would read `adds <the command> as a check` directly
         beneath a row whose fact is that same command -- the whole string twice, and for
         the coverage suggestion that is two wrapped lines of it. The row names
         the thing once, the way `fileTodo` names its file once, and the chip
         beside it already says there is no check there yet. */
      /* Three verbs, and the middle one is the one a suggestion was always
         for. `would_gate` is the check this makes possible, and until it is
         on the list nothing measures this repository against it -- not the
         next feature, not the one after. Offered only where the reading named
         a command, because there is otherwise nothing to add. */
      acts: `<button class="btn btn-sm btn-primary" data-copy-rec="${esc(r.key)}"
               >Copy prompt</button>
             ${/* Not gated, and that is a loss worth naming. Disabling it until
                  a feature built from this suggestion is accepted would rest on
                  a fact this project can check -- but the work happens in
                  somebody's session, and nothing here can see it -- so the
                  honest control is an enabled button and a form
                  that says, before you press it, that a check added before its
                  work exists is red on untouched code. */''}
             ${!r.would_gate || state.adoptingRec === r.key ? '' : `
               <button class="btn btn-sm" data-adopt-rec="${esc(r.key)}"
                 >Add it as a check</button>`}
             ${state.decliningRec === r.key ? '' : `
               <button class="btn btn-sm btn-quiet" data-decline-rec="${esc(r.key)}"
                 >Not for this project</button>`}`,
      /* The reason, asked for the same way a check asks: inline, on the card,
         before anything is recorded. It is the only thing that makes the
         declined list readable six months from now. */
      after: state.adoptingRec === r.key ? adoptForm(r) : state.decliningRec !== r.key ? '' : `
        <div class="objection">
          <label class="small dim" for="rec-reason" style="display:block">
            Why not this project? Kept with the decision, and shown to every later reading.</label>
          <input class="inp" id="rec-reason" type="text" style="width:100%;margin-top:.25rem">
          <div class="d-actions" style="margin-top:.6rem">
            <button class="btn btn-sm btn-quiet" id="cancel-decline-rec">Keep it</button>
            <button class="btn btn-sm btn-primary" data-confirm-decline-rec="${esc(r.key)}"
              >Not for this project</button>
          </div>
        </div>`,
  });
}

function firstSentence(text) {
  const whole = String(text || '').trim();
  const cut = whole.search(/[.!?]\s/);
  return cut > 0 ? whole.slice(0, cut + 1) : whole;
}

/* What this project decided against, and the way back.

   Filtered out of every reading, which is what stops the same proposal
   arriving forever -- and therefore invisible unless something draws it. A
   decision you cannot see is one you cannot revisit, and "not now" is the
   common reason a check gets declined. */
/* What was suggested and turned down. Same reasoning as the declined checks:
   filtered out of every reading, and therefore invisible unless something
   draws it -- and "not now" is the usual reason. */
function declinedRecommendations(p, section = null) {
  // On a tab, only that tab's: a turned-down check is not a testing matter.
  const rulings = (p.recommendation_rulings || []).filter((r) => !section
    || todoSection({ kind: r.kind }) === section);
  if (!rulings.length) return '';
  return `
    <details class="fold declined">
      <summary><span class="eyebrow">${rulings.length} suggestion${
        rulings.length === 1 ? '' : 's'} you turned down</span></summary>
      <p class="small dim">No reading will make ${rulings.length === 1 ? 'it' : 'these'} again.
        Letting one back only lifts that &mdash; it reappears when a reading suggests it, which
        it will, from the same facts.</p>
      ${rulings.map((r) => `
        <div class="hz-row">
          <div>
            <span class="hz-path">${esc(r.title || r.key)}</span>
            ${r.would_gate ? `<p class="ask-cost">${esc(r.would_gate)}</p>` : ''}
            <p class="hz-why">${r.reason ? esc(r.reason)
              : '<span class="dim">no reason recorded</span>'}</p>
          </div>
          <button class="btn btn-sm" data-reinstate-rec="${esc(r.key)}">Allow it again</button>
        </div>`).join('')}
    </details>`;
}

function declinedChecks(p) {
  const rulings = p.gate_rulings || [];
  if (!rulings.length) return '';
  return `
    <details class="fold declined">
      <summary><span class="eyebrow">${rulings.length} check${
        rulings.length === 1 ? '' : 's'} you turned down</span></summary>
      <p class="small dim">No reading will propose ${rulings.length === 1 ? 'it' : 'these'} again.
        Putting one back restores the command it had, and clears the baseline.</p>
      ${rulings.map((r) => `
        <div class="hz-row">
          <div>
            <span class="hz-path">${esc(r.name)}</span>
            ${r.command ? `<p class="ask-cost">${esc(r.command)}</p>` : ''}
            <p class="hz-why">${r.reason ? esc(r.reason)
              : '<span class="dim">no reason recorded</span>'}</p>
          </div>
          <button class="btn btn-sm" data-reinstate-gate="${esc(r.name)}">Put it back</button>
        </div>`).join('')}
    </details>`;
}

/* Checks that were measured and did not pass. Still on the list, still run,
   and left out of what judges a feature until they go green. Derived from the
   baseline exactly as the server derives it, so the page and the rule cannot
   drift apart about which ones they are. */
function parkedChecks(p) {
  const results = {};
  (((p.baseline || {}).results) || []).forEach((r) => { results[r.name] = r; });
  return (p.gates || []).filter((g) => results[g.name] && !isGreen(results[g.name]))
    .map((g) => g.name);
}

function judgingCount(p) {
  const results = {};
  (((p.baseline || {}).results) || []).forEach((r) => { results[r.name] = r; });
  return (p.gates || []).filter((g) => results[g.name] && isGreen(results[g.name])).length;
}

function gateCount(p) {
  const results = ((p.baseline || {}).results) || [];
  const scored = results.filter((r) => !/^setup\[/.test(r.name));
  const green = scored.filter((r) => r.passed).length;
  const n = (p.gates || []).length;
  if (!scored.length) return `${n} check${n === 1 ? '' : 's'} · never measured`;
  const when = p.baseline_at ? ` · measured ${esc(rel(p.baseline_at))}` : '';
  return green === scored.length
    ? `${n} check${n === 1 ? '' : 's'} · all green${when}`
    : `${n} check${n === 1 ? '' : 's'} · <b>${scored.length - green} red</b>${when}`;
}


/* One line for the whole second question, in the order a reader cares: what is
   still owed from them, then what the lane can actually do, then what it has
   asked for and not been given. */
/* A red row read three hours after the file that fixes it landed is correct,
   stale, and indistinguishable from broken. */
function staleBaselineNote(p) {
  const at = p.baseline_at;
  if (!at || !p.baseline) return '';
  const applied = p.scaffolding_applied || [];
  if (!applied.length) return '';
  /* Only while a red row could be explained by it. A green board needs no
     warning, and a note that is always up is a note nobody reads. */
  const red = ((p.baseline.results) || []).filter((r) => !r.passed && !r.skipped);
  if (!red.length) return '';
  return `
    <p class="small stale-note">These rows were measured
      ${esc(rel(at))}, and ${applied.length === 1 ? 'a harness file has' : `${applied.length} harness files have`}
      been added to the repository since this project was last surveyed.
      <b>Run the checks</b> before reading a red row as a defect — the last one that looked like a
      failure was a check waiting on a file that had since been written.</p>`;
}

function projectScreen() {
  const p = state.project.project;
  const survey = p.survey;
  const baseline = p.baseline;
  const env = p.environment;
  const results = (baseline && baseline.results) || [];
  const byNameEarly = {};
  results.forEach((r) => { byNameEarly[r.name] = r; });
  const broken = baseline ? unrunnableGates(baseline) : [];
  const notGreen = (p.gates || [])
    .filter((g) => byNameEarly[g.name] && !isGreen(byNameEarly[g.name]));
  const failing = results.filter((r) => !r.passed && !r.skipped);
  /* Whether a section may present itself as a line. Measured from what is
     already on this screen: a baseline exists, nothing in it failed, and no
     check is unrunnable. Anything short of that and the section opens. */
  const allGreen = !!baseline && !failing.length && !broken.length && !notGreen.length;
  const sec = projectSection();

  if (p.stage === 'surveying') {
    /* Under the project's own header and in the page's own column, like every
       other state of this page. A 720px column centred on its own with no
       header would make the one screen a new project opens on look like a
       different application. */
    return `
      ${projectBar(p)}
      <div class="wrap">
        <div style="padding:2.5rem 0 1rem">
          <h1>Surveying ${esc(p.name)}.</h1>
          <p class="muted">Working out how this project is tested and what those tests need to run
            in, then running those checks on an untouched checkout.</p>
        </div>
        <ul class="phase-list">
          <li><span class="dot running"></span><span class="phase-name">survey</span><span></span></li>
          <li><span class="dot"></span><span class="phase-name pending">baseline</span><span></span></li>
        </ul>
        <p class="airgap-note">What you approve is that every check on the list passes on
          untouched code, and that a blind test can live here. A check that is red before any work
          starts cannot say whether later work broke something — so it is fixed, ratcheted at
          today's number, or declined with a reason.</p>
      </div>`;
  }

  if (p.stage === 'failed') {
    /* A reading that succeeded and a baseline that did not are the same stage,
       and only one of them needs paying for again.

       A survey can read a repository correctly, propose gates, an environment,
       tiers and placements -- and the baseline after it refuse the environment
       over a false positive. "Survey from scratch" alone would buy a
       byte-identical reading, and the error itself says "fix them in settings".

       So: when the reading survived, offer to run the checks again, and let the
       settings it points at be reached. */
    const read = !!p.survey;
    const problems = (state.project || {}).environment_problems || [];
    return `
      ${projectBar(p, { actions: `
        ${read ? `<button class="btn btn-primary" id="run-baseline"
          >Try the checks again</button>` : ''}
        <button class="btn" id="resurvey">Survey from scratch</button>` })}
      <div class="wrap">
        <div class="error-box" style="margin-top:1.5rem"><pre>${esc(p.error)}</pre></div>
        ${!read ? '' : `
          <p class="small" style="margin-top:1rem">The reading itself survived — this project's
            checks, environment, testing surface and placements are all recorded. What failed is
            what came after it, so <b>surveying again would pay a model to say the same thing</b>.
            ${problems.length
              ? `The environment still has ${problems.length} problem${
                  problems.length === 1 ? '' : 's'}; edit it below and try again.`
              : `Nothing is wrong with it now — try the checks again.`}</p>
          ${projectPanels(p, env, survey, 'environment')}`}
      </div>`;
  }

  // Setup is plumbing. Three rows of `pip install` between the human and the
  // gates they are ruling on is three rows too many; the summary carries the
  // only thing that matters, which is whether it worked.
  const byName = {};
  results.forEach((r) => { byName[r.name] = r; });
  const gateLines = (p.gates || []).map((g) => gateLine(g, byName[g.name], p)).join('');
  const orphaned = results
    .filter((r) => !r.name.startsWith('setup[') && !(p.gates || []).some((g) => g.name === r.name))
    .map((r) => gateLine(null, r)).join('');
  /* Gates this baseline says nothing about. They draw as "not run", and a
     verdict computed from the results alone would count them toward nothing --
     a gate with no result is neither broken nor failing -- and could draw four
     "not run" rows under a heading reading "every check ran and passed", which
     is the page contradicting itself in the one place it is asked to be
     plain. */
  const unmeasured = (p.gates || []).filter((g) => !byName[g.name]);

  /* Measured at gate 0, in the sandbox. `approve` refuses without it, so the
     button has to agree with the API rather than let a human press it and read
     the refusal as a bug. */
  const blindProved = (p.placement_probe || []).some((r) => r.usable);

  const actions = `
    <button class="btn btn-sm" id="propose-changes"
      title="A model reads the repository — its CI, its scripts, its config — and says what the check list should be. It proposes; nothing changes until you accept it, item by item."
      >Resurvey</button>
    <button class="btn btn-sm ${state.projectRun ? 'btn-working' : ''}" id="run-baseline"
      ${state.projectRun ? 'disabled' : ''}
      title="Runs every check above on an untouched checkout of ${esc(p.base_ref || 'the base branch')}, in this project's environment. No model calls. What it settles is that these commands work at all."
      >${state.projectRun ? 'Running…' : 'Run checks'}</button>
    ${/* Once a project is approved, approving again is the one thing you cannot
         do here and starting a feature is the thing you came for, so the
         primary action becomes what this screen is now for. */''}
    ${p.stage === 'ready'
      ? `<a class="btn btn-primary btn-sm"
           href="#/${encodeURIComponent(p.project_id)}?start"
           title="Opens the composer. A feature branches from ${esc(p.base_ref || 'the base branch')} and is judged by the checks on this screen."
           >Start a feature</a>`
      : `${/* "Approve" reads as approving the project. What a human is ruling
             on is the check list, and the button says so -- and it does not
             wait for every check to be green, because a red one is parked
             rather than blocking. Why it is disabled is the "blocks
             approval" banner's to say, not a caption's. */''}
         <button class="btn btn-primary btn-sm" id="approve-project"
           ${movesDockerfile(state.project) ? `title="Also commits the Dockerfile these checks run in to your repository, as ${
             esc(state.project.dockerfile.path)}."` : ''}
           ${blockingItems(p).length ? 'disabled' : ''}>Accept checks</button>`}`;

  /* THE RULING.

     One object, three parts: a lamp lit by the line's own state, a legend that
     is the ruling itself, and one sentence of reason. Not two stacked
     paragraphs -- a display-face headline shrunk to body size, and a grey
     paragraph welding the standing explanation of gate 0 to the
     state-dependent instruction -- which would put the reader's answer,
     "nothing to fix", on line three of four.

     The legend never counts. The nameplate above already says `5 · 2 red`, and
     a count repeated in larger type is the page saying one fact twice. What the
     nameplate cannot say is what the count *means* for the decision, which is
     the only thing this object is for.

     Approval is not tested first: if it were, an approved project would report
     "Approved. Features can be started here." however the last run went --
     including a check that no longer runs at all. What the checks say outranks
     whether you once said yes to them. */
  /* Named, not counted -- the nameplate counts. A comma-joined pair reads as a
     truncated list ("tests, lint fail"), so two get a conjunction and three or
     more get the serial comma. */
  const names = (list) => {
    const n = list.map((g) => esc(g.name));
    return n.length < 3 ? n.join(' and ') : `${n.slice(0, -1).join(', ')} and ${n[n.length - 1]}`;
  };
  const ruling = !baseline
    ? { tone: 'wait', legend: 'Not proved yet',
        why: `These checks have never been run, and a check nobody has run is a check nobody
              knows works. Run them, then approve.` }
    : unmeasured.length === (p.gates || []).length
      ? { tone: 'wait', legend: 'Not proved yet',
          why: `None of these checks has been run since the list changed. Every result below was
                measured against a list that no longer exists.` }
      : unmeasured.length
        ? { tone: 'wait', legend: 'Not proved yet',
            why: `${names(unmeasured)} ${unmeasured.length > 1 ? 'have' : 'has'} never been
                  run. Whatever the rest say, ${unmeasured.length > 1 ? 'those are' : 'that is'}
                  unproved.` }
      : broken.length
        ? { tone: 'act', legend: 'You are needed',
            why: `${names(broken)} could not run at all, so ${broken.length > 1 ? 'they report' : 'it reports'}
                  nothing — on this run and on every feature after it.
                  ${p.stage === 'ready'
                    ? `Red for every feature built here until ${broken.length > 1 ? 'they are' : 'it is'} fixed or dropped.`
                    : `The output and the three things you can do about
                       ${broken.length > 1 ? 'them are in the amber rows' : 'it are in the amber row'}
                       below. <b>Accept checks stays disabled while
                       ${broken.length > 1 ? 'any of them is' : 'it is'} on the list.</b>`}` }
        : !blindProved
          ? { tone: 'act', legend: 'You are needed',
              why: `Nothing here can carry a test written by an agent that has never seen this
                    repository — the measurement is below. Until one place is proved, every
                    acceptance criterion would rest on tests written by the same agents that
                    wrote the feature, which is the one thing this pipeline exists to avoid.
                    <b>Accept checks stays disabled until then.</b>` }
        : notGreen.length
          ? { tone: 'act', legend: 'You are needed',
              why: `${names(notGreen)} ${notGreen.length > 1 ? 'are' : 'is'} red on code nobody
                    has changed. Nothing is built here until every check passes, because a check
                    that is red before any work starts cannot tell you whether the work broke
                    something — and a row that is red in every packet teaches people to stop
                    reading red at all.
                    <b>Three ways out, and each is recorded:</b> fix it; ratchet it, by capturing
                    the count with a metric pattern and setting the ceiling to today's number, so
                    it is green now and red on the next regression; or decline it with a reason,
                    which takes it off the list and leaves the reason readable. Accepting the
                    checks with one still red parks it: it stays here as a job, and is left out
                    of what judges each feature. At least one check has to pass.` }
          : { tone: 'ok', legend: 'Ready for your ruling',
              why: 'Every check ran and passed on code nobody has changed.' };

  return `
    ${projectBar(p, { actions })}
    <div class="wrap">
      ${/* The survey's own news, for the tabs about checks and tests. The
           as-built is a reading of its own and has nothing to rule on here. */''}
      ${sec === 'as-built' ? '' : readingBehindStrip()}
      ${/* Not on the checks screen. "The checks every feature is judged by"
           would stand above a heading reading "Checks", and a paragraph naming
           the two questions would stand above the two headings that name them
           -- three statements of the same thing before a single row. The other sections
           keep theirs; they have no headings of their own. */''}
      ${['checks', 'tests', 'guides', 'overview', 'history', 'as-built'].includes(sec) ? ''
        : ['survey', 'environment'].includes(sec) ? tabHead(sec, p) : `
        <h2 class="sec-title">${esc(SECTION_TITLE[sec])}</h2>
        <p class="sec-lede">${SECTION_LEDE[sec]}</p>`}

      ${/* The ruling belongs with the list you rule on -- and only while there
           is one to make. Once a project is approved and every check is green,
           "Approved. Features can be started here." and a paragraph explaining
           that you approved it are the nameplate's CERTIFIED / 5 · all green
           said twice more, in more words, further down the page. What survives
           being approved is a problem: a check that could not run, or one that
           fails on untouched code. */''}
      ${/* Only while something is in your way. "Nothing to fix" and "Ready for
           your ruling" would be verdicts on the repository, and one of them is
           routinely wrong: a check red for want of a harness file that the same
           survey offered gets reported as a defect in your code, with the
           advice to go and fix it. What this page can honestly say about a healthy
           project is section 4 -- what it can verify -- and that is not a
           verdict at all. */''}
      ${/* No ruling plate on this screen. "YOU ARE NEEDED -- lint is red on
           code nobody has changed. Nothing is built here until every check
           passes" would stand directly above a block saying "One thing blocks
           approval: lint. Get it passing in your repo..." -- the same fact,
           twice, in two voices, and the second one is the one with the way out
           in it. It still stands on the other sections. */''}
      ${/* Not while the checks are running. "Not proved yet -- run them, then
           approve" would stand directly above "Running the checks", telling a
           person to do the thing already happening; and any other verdict here is
           about the run this one is replacing. The strip below is the state. */''}
      ${['checks', 'tests', 'overview', 'history', 'as-built'].includes(sec) || !['act', 'wait'].includes(ruling.tone)
        || (state.projectRun && state.projectRun.phase !== 'resurvey') ? '' : `
      <div class="ruling ${esc(ruling.tone)}">
        <span class="r-lamp" aria-hidden="true">${icon(RULING_LAMP[ruling.tone], 'ic')}</span>
        <p class="r-legend">${esc(ruling.legend)}</p>
        <p class="r-why">${ruling.why}</p>
      </div>
      ${/* No standing paragraph explaining gate 0 here. The consequence is a
           caption under Approve -- "Lets features start here. Nothing is built
           until you do." -- so a paragraph would say the same thing a second
           time, further from the button and in smaller type, and its other half
           ("whether this is the right list to hold every feature to") is what
           the lede above already says. The vocabulary is still explained: the
           strip names gate 0, and the caption says what approving costs at the
           moment it can be spent. */''}`}

      ${/* The detector runs and the API serves its answer, so the page says
           it. A gate list that has stopped describing the repository is exactly
           the thing a human is here to rule on, and it belongs above the gates
           it is about. */''}
      ${!['checks', 'tests', 'guides', 'overview', 'survey', 'history'].includes(sec) ? '' : `
      ${/* The drift card asks "has the gate list stopped describing this repo?"
           and names the files that moved. A proposal answers it and names the
           same files. Two cards, one fact. So the question stands only while it
           is unanswered: the moment a proposal is on screen and unruled, the
           answer speaks for both. */''}
      ${/* Two kinds of project-level run report through the same stream, and
           the strip describes each -- otherwise a re-reading that takes eight
           minutes announces itself as "Running the checks · half a minute or
           so", which is worse than saying nothing. */''}
      ${!state.projectRun ? '' : state.projectRun.phase === 'resurvey' ? `
        <div class="running-strip">
          <span class="spin" aria-hidden="true"></span>
          <div>
            <b>Reading this repository again.</b>
            A model is reading the files that moved and everything the verify lane has asked for.
            Several minutes — nearer ten than one. It runs on the server, so you can leave this
            page or close the tab; the proposal appears here when it lands.
          </div>
        </div>` : `
        <div class="running-strip">
          <span class="spin" aria-hidden="true"></span>
          <div>
            <b>Running the checks${esc(state.projectRun.detail ? ' ' + state.projectRun.detail : '')}.</b>
            Every check above, on code nobody has touched, in this project's environment. Half a
            minute or so; the rows fill in as they finish.
          </div>
        </div>`}

      ${/* A re-reading, and what it proposes, are the Survey tab's: the
           overview says one is waiting, and the tab's dot marks it. */''}
      ${sec !== 'survey' || (state.resurvey && state.resurvey.diff)
        ? '' : toolingDriftBlock(p, (state.project || {}).tooling_drift, { brief: true })}
      ${sec === 'survey' ? resurveyBlock(p) : ''}

      ${/* The page as drawn: two questions, and nothing on it that is not a row
           of one of them or a to-do inside a row.

           What is not on it: a title and a lede restating the two questions
           the headings already name, a verdict plate saying what the counts
           say, a placement block, a prose paragraph explaining what
           "verifiable" means, and a disclosure about how it was measured. Every
           one of them would be true and none of them is a decision. */''}
      ${sec === 'overview' ? overviewSection(p) : ''}
      ${sec === 'checks' ? `${standingBlocks(p)}${checksQuestion(p)}` : ''}
      ${sec === 'tests' ? testsTab(p) : ''}
      ${sec === 'guides' ? guidesTab() : ''}
      ${sec === 'history' ? historySection(p) : ''}


      `}

      ${sec === 'results' ? firstRunSection(p, baseline, env) : ''}
      ${/* Outside the block above: the drift card and the checks' running
           strip are about the gate list, and the as-built is not. */''}
      ${sec === 'as-built' ? asBuiltSection(p) : ''}
      ${sec === 'survey' || sec === 'environment'
        ? (state.editing === sec ? (sec === 'survey' ? surveyEditor() : environmentEditor())
          : projectPanels(p, env, survey, sec)) : ''}
    </div>`;
}
