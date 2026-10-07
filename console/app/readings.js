/* What a reading proposes and what the checks found: the environment draft,
   proposal cards and their diffs, the parts ruled on per tab, and the first run. */

'use strict';

/* ----------------------------------------------------------- 0c. editing */

const ENV_KINDS = [
  ['compose', 'your compose stack — the checks run in one of its services'],
  ['reuse', 'your existing image, as it is'],
  ['derive', 'built on top of your own image, adding what the checks need'],
  ['generate', 'built for this project from the detected stack'],
];

function startDraft() {
  const p = state.project.project;
  state.draft = {
    name: p.name || '',
    repo: p.repo || '',
    base_ref: p.base_ref || 'HEAD',
    digest_budget: p.digest_budget || 120000,
    gates: JSON.parse(JSON.stringify(p.gates || [])),
    environment: JSON.parse(JSON.stringify(p.environment || {
      kind: 'generate', source: '', dockerfile: '', image: '', setup: [], workdir: '/work', rationale: '',
    })),
  };
  /* What a person opens at review. Edited as fields rather than as an object,
     so an environment that never had one gets an empty one to fill in; saving
     puts it back to none if it is still empty. */
  const env = state.draft.environment;
  env.preview = env.preview || { open: '', path: '/', services: [], note: '' };
  env.preview._lines = (env.preview.services || []).map((v) => `${v.name}: ${v.command}`).join('\n');
}

/* The draft's environment as the server takes it: the preview's service lines
   parsed back, and an untouched empty preview left as none. */
function savedEnvironment(env, before) {
  const out = JSON.parse(JSON.stringify(env));
  const pv = out.preview;
  if (!pv) return out;
  const had = ((before || {}).preview || {}).services || [];
  pv.services = String(pv._lines || '').split('\n').map((l) => l.trim()).filter(Boolean)
    .map((line) => {
      const at = line.indexOf(':');
      const name = (at > 0 ? line.slice(0, at) : line).trim();
      const command = at > 0 ? line.slice(at + 1).trim() : '';
      const old = had.find((v) => v.name === name) || {};
      return { ...old, name, command };
    });
  delete pv._lines;
  if (!(before || {}).preview && !pv.open && !pv.services.length && !pv.note
      && (pv.path || '/') === '/') out.preview = null;
  return out;
}

function setDraft(path, value) {
  const parts = path.split('.');
  let node = state.draft;
  while (parts.length > 1) node = node[parts.shift()];
  node[parts[0]] = value;
}

function environmentProblems(problems) {
  if (!problems || !problems.length) return '';
  return `<div class="objection" style="margin:1rem 0">
    <p class="who">These checks cannot produce a result</p>
    <ul class="disclosure-list">
      ${problems.map((x) => `<li>${esc(x)}</li>`).join('')}
    </ul>
    <p class="small">Fix it in <b>Environment</b> below — a container will not start until you do,
      and every check would be red for a reason that has nothing to do with your code. The same
      goes for a check whose own definition cannot yield an answer.</p>
  </div>`;
}


/* What a testing surface says, laid out to be read rather than parsed.

   Not a `<pre>` of space-padded columns with a paragraph of prose inside each
   row: that is a code block containing sentences, which scrolls sideways and
   wraps nowhere. The verdict is the decision -- `absent` and `inline_only`
   are what cost criteria, `usable` is what pays for them -- so it is a pill,
   the level is a heading, and the reasoning is a paragraph like any other. */
const VERDICT_PILL = {
  usable: 'pill-green', inline_only: 'pill-amber', absent: 'pill-red',
};

/* The test that would prove this tier's claim, shown rather than described.
   It is the difference between "a model says a new test can use this setup" and
   "a test using this setup ran and passed", which is the whole reason the field
   exists. */
function canaryNote(t) {
  if (t.verdict !== 'usable') return '';
  const src = (t.canary || '').trim();
  if (!src) return `<p class="ts-note dim">No canary. This tier says a new test can be written
    against setup that already exists, and supplies no test doing so — so the claim is not
    checkable, and an agent told to use this setup could find nothing there.</p>`;
  return `<p class="ts-note">A test using this tier's own named fixtures is supplied, and repo ready
    runs it where a blind test would actually live. If it does not pass, this verdict is wrong and
    you will see that in seconds rather than in a packet.</p>
    <pre class="hz-pre">${esc(src)}</pre>`;
}

/* A proposal is a replacement set, and rendering it as one has a reader meet
   decisions they have already made, again.

   `testing`, `test_file_commands` and `blind_placements` are replaced whole --
   there is no way for a reading to say "and the other two are fine" except by
   restating them. So a re-read that adds ONE browser placement would draw
   three cards, and the two it did not touch come back with their canary
   comments reworded, which is all a model can help doing when asked to write a
   file twice. A proposal's own prose can admit as much -- "restated only
   because the set is replaced whole" -- while its cards say otherwise.

   What is recorded is right there to compare against, so compare. An item is
   `new` if nothing recorded shares its identity, `restated` if it differs only
   in text that changes nothing about what runs, and `changed` otherwise. Only
   the last two kinds are worth a human's attention, and only they are drawn. */
const DIFF_IDENTITY = {
  test_file_commands: 'match',
  blind_placements: 'directory',
  scaffolding: 'path',
  tiers: 'tier',
};

/* Prose, and regenerated prose at that. A canary asserts nothing about the
   application -- it proves a directory is collected and that a failure there is
   reported -- so two canaries differing in their comments prove the same thing.
   `note` and `purpose` are written for a human to read. Nothing here decides
   what a runner does. */
const DIFF_PROSE = {
  test_file_commands: [],
  blind_placements: ['canary_passes', 'canary_fails', 'kind'],
  scaffolding: ['purpose'],
  tiers: ['note'],
};

/* THE TWO PRIMITIVES EVERY PROPOSAL IS DRAWN WITH.

   Five kinds of proposal with five presentations would mean a hand-written
   pill in a different register each time -- an abstract noun, a sentence
   fragment, a verb phrase -- the model's reasoning as the headline, and the
   thing actually being changed either buried or absent. A reader cannot tell
   what any of them would do without reading a paragraph, and a proposal can be
   proposing nothing at all -- which the paragraph does not say, because the
   model does not know.

   So the change leads and the reasoning follows it. The label is the field being
   replaced, which is a fact; the verb is the action or "replace", which is a
   fact; the summary is counted off the difference, which is a fact. Nothing on
   the row is written per-kind, and the reading's own words are quoted under one
   disclosure with the same label everywhere. */
function diffLines(before, after) {
  const a = before || [];
  const b = after || [];
  const keep = new Set(b);
  const out = [];
  a.forEach((l) => { if (!keep.has(l)) out.push(['del', l]); });
  const had = new Set(a);
  b.forEach((l) => out.push([had.has(l) ? 'same' : 'add', l]));
  if (!out.some(([k]) => k !== 'same')) return '';
  /* Behind a click, like the reasoning is. The header already says how much
     moved -- "1 rule added", "dockerfile, image, setup, services" -- and that is
     what a reader rules on; the lines themselves are what they open when the
     answer is not obvious from the count. Open by default, an environment whose
     whole Dockerfile is being replaced puts seventy-eight red lines between one
     card's header and the next, and the page becomes unreadable at exactly the
     proposal that most needs reading. */
  const moved = out.filter(([k]) => k !== 'same').length;
  return `<details class="p-open">
    <summary>${moved} changed line${moved === 1 ? '' : 's'}</summary>
    <div class="p-diff" tabindex="0">${out.map(([k, l]) =>
      `<div class="dl ${k}">${esc((k === 'del' ? '-' : k === 'add' ? '+' : ' ') + l)}</div>`).join('')}</div>
  </details>`;
}

/* `body` is what the card is *about*: the tiers, the rules, the files it
   proposes. Without it a card draws a header, a count and a reason with the
   thing itself missing -- a rules card whose one changed rule is nowhere on
   it, under a button that applies it. */
function proposalCard({ field, verb, delta, diff, body, why, evidence, checkbox, act, restated,
                       whyLabel = "the rest of the reading's words", gist = true }) {
  return `
    <article class="p">
      <header class="p-head">
        <label class="p-pick">
          ${checkbox || '<span class="p-nopick" aria-hidden="true"></span>'}
          <span class="p-field">${esc(field)}</span>
        </label>
        <span class="p-verb">${esc(verb)}</span>
        <span class="p-delta">${esc(delta)}</span>
      </header>
      ${diff || ''}
      ${body || ''}
      ${restated || ''}
      ${/* The first sentence on the card, the rest a click away. A reason
            folded away whole leaves the card saying what changes and never
            why, and the paragraph behind the fold opens mid-argument. */''}
      ${!why || !gist ? '' : `<p class="p-gist">${esc(firstSentence(why))}</p>`}
      ${!why || (gist && firstSentence(why).length >= why.trim().length && !evidence) ? '' : `
        <details class="p-why">
          <summary>${esc(whyLabel)}</summary>
          <p>${esc(why)}</p>
          ${evidence ? `<p class="p-ev mono">${esc(evidence)}</p>` : ''}
        </details>`}
      ${act || ''}
    </article>`;
}

function classifyProposed(kind, proposed, recorded) {
  const id = DIFF_IDENTITY[kind];
  const prose = new Set(DIFF_PROSE[kind] || []);
  const have = new Map((recorded || []).map((r) => [r[id], r]));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const fresh = [];
  const restated = [];
  for (const item of proposed || []) {
    const old = have.get(item[id]);
    if (!old) { fresh.push(item); continue; }
    const differs = Object.keys(item)
      .filter((k) => !prose.has(k) && !same(item[k], old[k]));
    if (differs.length) fresh.push(item); else restated.push(item);
  }
  return { fresh, restated, id };
}

/* One line, never a card. The names are there because "2 restated" with no
   names invites the reader to open it to find out whether their thing is in it,
   which is the scrolling this exists to stop. */
function restatedLine(kind, restated, id) {
  if (!restated.length) return '';
  return `<p class="ts-restated small dim">${restated.length} unchanged, restated because the
    set is replaced whole: <span class="mono">${restated.map((r) => esc(r[id])).join(' · ')}</span></p>`;
}

/* How a test at this level undoes what it did -- the project's own convention,
   read off its tests. Rendered in the head as well as in the note, because a
   reading whose whole substance is the cleanup would otherwise show three tiers
   identical to the ones in force, and the change would be a sentence nobody
   can see without opening each "why". `null` is never read, `''` is read as nothing. */
/* Who undoes a test's data, where the reading said -- the one thing about
   cleanup a person may have to act on. "Each test undoes its own" is flagged
   as well as "nothing": nothing catches the test that forgets, which is the
   gap through which a suite can fill a shared demo board with its lists. */
const cleanupBy = (t) => t.cleanup == null ? null
  : (t.cleanup_by || ((t.cleanup || '').trim() ? null : 'nobody'));
const cleanupPill = (t) => ({
  // Settled, not a gap: nothing is reset because no test reads another's data.
  isolated: `<span class="pill pill-green ts-proof">each test uses its own data</span>`,
  each_test: `<span class="pill pill-amber ts-proof">each test must undo its own</span>`,
  nobody: `<span class="pill pill-amber ts-proof">no cleanup</span>`,
}[cleanupBy(t)] || '');

/* The fixes for a tier that needs one, as one choice: pick a fix or leave it.

   Each option is a whole fix, and its parts -- the files it writes, the
   environment step that runs one of them -- are shown inside it, not as cards
   of their own further down the page, where a reader cannot tell they are
   related and can pick half a fix. Applying runs the parts in the order they need
   (see `apply_proposal`). "Leave it" is the default, because unchosen is
   rejected everywhere else on this page too.

   A fix that needs real code is not a choice here at all: nothing applies it.
   It is a prompt for the person's own coding agent, followed by a re-survey --
   never a feature this factory builds, because its own runs need that code to
   exist first. */
function cleanupChoice(t, diff, opts = {}) {
  const by = cleanupBy(t);
  // An isolated tier asks for a choice only when it came with one: the way its
  // tests keep to their own data is not yet somewhere a new test can import.
  if (!['each_test', 'nobody'].includes(by)
      && !(by === 'isolated' && (t.cleanup_options || []).length)) return '';
  const d = diff || {};
  const all = (t.cleanup_options || []).map((o, i) => ({ o, i }));
  const applied = all.filter(({ o }) => (o.files || []).length || o.environment);
  const prompts = all.filter(({ o }) => !((o.files || []).length || o.environment) && o.agent_prompt);
  const st = (state.project || {}).scaffolding_state || {};
  /* On the project page's own Tests row (`opts.row`) rather than a re-survey
     proposal: its own radio group, so a proposal's Apply never collects it,
     and its own button, because nothing else on that page applies it. */
  const cls = opts.row ? 'row-cleanup' : 'rs-cleanup';
  const name = `${opts.row ? 'row-' : ''}cleanup-${t.tier}`;
  const parts = (o) => [
    ...(o.files || []).map((path) => {
      const f = (d.scaffolding || []).find((x) => x.path === path) || {};
      return st[path] === 'differs'
        ? `<li>changes <span class="mono">${esc(path)}</span>
             <button type="button" class="btn btn-sm cf-btn" data-scaffold-diff="${esc(path)}">show what changes</button>
             <pre class="hz-pre diff-out" data-diff-for="${esc(path)}" hidden></pre></li>`
        : `<li>adds <span class="mono">${esc(path)}</span>
             <details class="hz-more cf-read"><summary>read it</summary>
               <pre class="hz-pre">${esc(f.contents || '')}</pre></details></li>`;
    }),
    o.environment && d.environment ? `<li>runs it before each check session
        <details class="hz-more cf-read"><summary>see the command</summary>
        <pre class="hz-pre">${esc((d.environment.test_prepare || []).join('\n'))}</pre></details></li>` : '',
  ].filter(Boolean).join('');
  /* Quiet on purpose: a filled badge beside every title pulls the eye off the
     titles, and the options blur into one another. */
  const tag = (o) => o.recommended ? ' <span class="cf-rec">recommended</span>' : '';
  const row = (i, title, summary, detail, o) => `
    <label class="cf-opt">
      <input type="radio" class="${cls}" name="${esc(name)}" data-tier="${esc(t.tier)}"
             value="${i}" ${i === '' ? 'checked' : ''}>
      <span class="cf-body">
        <span class="cf-title">${esc(title)}${o ? tag(o) : ''}</span>
        ${summary ? `<span class="cf-sum">${esc(summary)}</span>` : ''}
        ${detail ? `<ul class="cf-parts">${detail}</ul>` : ''}
      </span>
    </label>`;
  const promptBlock = prompts.map(({ o }) => `
    <div class="cf-code">
      <span class="cf-title">${esc(o.title)}${tag(o)}</span>
      ${o.summary ? `<span class="cf-sum">${esc(o.summary)}</span>` : ''}
      <span class="cf-sum">Needs code in this repository, so it isn't applied here. Give the prompt
        to your coding agent, then re-survey.</span>
      <span><button type="button" class="btn btn-sm" data-copy-diff-rec="${esc(o.agent_prompt)}">Copy
        the prompt</button></span>
    </div>`).join('');
  if (!applied.length && !prompts.length) return `<div class="cf"><p class="cf-h">No fix was offered
    with this reading. Re-survey to get options, or leave it.</p></div>`;
  return `<div class="cf">
    ${promptBlock ? `<p class="cf-h">Fix it in the code</p>${promptBlock}` : ''}
    <p class="cf-h">${promptBlock ? 'Or, for now' : 'How should Fabrika fix this?'}</p>
    ${applied.map(({ o, i }) => row(i, o.title, o.summary, parts(o), o)).join('')}
    ${/* Leaving a level with no cleanup as it is means it is not tested: a
         test there would fail on another test's leftovers and read as broken
         code. So the choice says that, rather than "leave it". Unit tests
         share no data and are tested either way. */''}
    ${by === 'nobody' && t.tier !== 'unit'
      ? row('', `Don't check criteria at this level`, `Fabrika won't write or run acceptance
          tests here. Criteria at this level are marked on the spec before you freeze it, and at
          review they arrive as a checklist for you to check by hand. Your existing checks still
          run. Re-survey any time to change this.`, '', null)
      : row('', 'Leave it', by === 'isolated'
        ? `The tests here keep to their own data. A new test has to find out how they do it
          by reading one of them.`
        : `Tests at this level rely on each one undoing its own changes. Fabrika's tests do the
          same.`, '', null)}
    ${opts.row ? `<div class="cf-apply">
        <button type="button" class="btn btn-sm btn-primary" data-apply-cleanup="${esc(t.tier)}"
          >Apply</button>
        <span class="dim">A fix is written into your repository and committed. Choosing not to
          check is recorded, and this stops asking.</span></div>` : ''}
  </div>`;
}

/* A level's fix, picked on the project page. The reading's own options, the
   same rules as a proposal's: applied whole, and the level tested from then on. */
function applyCleanup(tier, reopen = false) {
  const picked = document.querySelector(`input[name="row-cleanup-${tier}"]:checked`);
  // "Don't check criteria at this level" is a decision too, and is recorded as
  // one: the page stops asking. Answering "nothing to apply" would read as a
  // button that does not work.
  const option = reopen || !picked || picked.value === '' ? null : Number(picked.value);
  const Level = tier[0].toUpperCase() + tier.slice(1);
  return withBusy(reopen ? 'Reopening the choice.' : option === null ? 'Recording your choice.'
    : 'Applying the fix.', async () => {
    await api(`/api/projects/${encodeURIComponent(state.projectId)}/cleanup`, {
      method: 'POST', body: JSON.stringify({ tier, option, reopen }),
    });
    await loadProject(true);
    render();
    toast(reopen ? `The fixes for ${tier} tests are back.`
      : option === null ? `Recorded. ${Level} criteria stay yours to check.`
      : `Applied. ${Level} criteria are tested from now on.`);
  });
}

/* The plain sentence the reading wrote for a person, and nothing else. Not
   the first sentence of `cleanup` -- prose written for the agents that write
   tests, which reads to a person as gibberish: "One thing escapes a test here
   and it is the global `fetch`." A reading from before `cleanup_summary`
   existed shows no line here rather than a truncated one; the detail is under
   "why". */
function cleanupGist(t) {
  const plain = (t.cleanup_summary || '').trim();
  if (!plain) return '';
  /* No label beside it: one in the column where field names go reads as a
     stray word, and the sentence says what it is about on its own. */
  return `<p class="ts-cleanup">${esc(plain)}</p>`;
}

function cleanupNote(t) {
  const said = (t.cleanup || '').trim();
  if (!said) return '';
  const ex = (t.cleanup_examples || []).filter((l) => (l || '').trim());
  return `<p class="ts-note"><b>How a test here cleans up after itself.</b> ${esc(said)}</p>
    ${ex.length ? `<pre class="hz-pre">${esc(ex.join('\n'))}</pre>` : ''}`;
}

/* What one level's reading changes, in words a person can check against the
   repository. `lines` are the changes that alter what a test author is told
   or given; `reworded` is true when the prose moved as well, or only the
   prose. A reading is regenerated every time, so wording always drifts, and a
   card that counts drift as change cannot say which proposals matter. */
function tierChanges(before, after) {
  const a = after || {};
  if (!before) return { lines: ['a level not read before'], reworded: false };
  const b = before;
  const list = (x) => x || [];
  const minus = (x, y) => list(x).filter((v) => !list(y).includes(v));
  const lines = [];
  if ((b.verdict || '') !== (a.verdict || '')) lines.push(`${b.verdict || 'none'} → ${a.verdict || 'none'}`);
  if ((b.runner || '') !== (a.runner || '')) lines.push(`runner: ${a.runner || '(none)'}`);

  const paths = (t) => list(t.setup_files).map((f) => f.path);
  const newFiles = minus(paths(a), paths(b));
  const goneFiles = minus(paths(b), paths(a));
  const rewritten = list(a.setup_files).filter((f) => {
    const old = list(b.setup_files).find((x) => x.path === f.path);
    return old && old.contents !== f.contents;
  }).map((f) => f.path);
  if (newFiles.length) lines.push(`setup files a test may use: + ${newFiles.join(', ')}`);
  if (goneFiles.length) lines.push(`setup files dropped: ${goneFiles.join(', ')}`);
  if (rewritten.length) lines.push(`setup files rewritten: ${rewritten.join(', ')}`);

  const newFixtures = minus(a.fixtures, b.fixtures);
  const goneFixtures = minus(b.fixtures, a.fixtures);
  if (newFixtures.length) lines.push(`helpers: + ${newFixtures.join(', ')}`);
  if (goneFixtures.length) lines.push(`helpers dropped: ${goneFixtures.join(', ')}`);

  const newImports = minus(a.import_examples, b.import_examples);
  const goneImports = minus(b.import_examples, a.import_examples);
  newImports.forEach((l) => lines.push(`import line: + ${l}`));
  goneImports.forEach((l) => lines.push(`import line: − ${l}`));

  const had = !!(b.canary || '').trim();
  const has = !!(a.canary || '').trim();
  if (!had && has) lines.push(`a canary test proves it: ${a.canary_filename || 'added'}`);
  else if (had && !has) lines.push('canary test removed');
  else if (had && has && (b.canary !== a.canary || b.canary_filename !== a.canary_filename)) {
    lines.push(`canary test rewritten: ${a.canary_filename || ''}`.trim());
  }

  if ((b.cleanup_by || '') !== (a.cleanup_by || '')) {
    lines.push(`who cleans up: ${b.cleanup_by || 'unknown'} → ${a.cleanup_by || 'unknown'}`);
  }
  const optionShape = (t) => JSON.stringify(list(t.cleanup_options)
    .map((o) => ({ files: o.files || [], environment: !!o.environment, recommended: !!o.recommended })));
  if (optionShape(a) !== optionShape(b)) lines.push('the cleanup fixes on offer changed');
  if (JSON.stringify(list(a.cleanup_examples)) !== JSON.stringify(list(b.cleanup_examples))) {
    lines.push('the cleanup lines it quotes changed');
  }
  if (JSON.stringify(list(a.run_by)) !== JSON.stringify(list(b.run_by))) {
    lines.push(`run by: ${list(a.run_by).join(', ') || '(nothing)'}`);
  }

  const words = (t) => JSON.stringify([t.note || '', t.cleanup || '', t.cleanup_summary || '',
    list(t.cleanup_options).map((o) => [o.title, o.summary, o.agent_prompt || ''])]);
  return { lines, reworded: words(a) !== words(b) };
}

/* Those changes, open on the card: every level that changes, and one line for
   the levels that only reworded. */
function tierChangeList(moved, reworded) {
  if (!moved.length && !reworded.length) return '';
  return `<div class="ts-changes">
    ${moved.map((c) => `
      <div class="ts-change">
        <span class="ts-name">${esc(c.tier)}</span>
        <ul>${c.lines.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>
      </div>`).join('')}
    ${reworded.length ? `<p class="ts-reworded small dim">${esc(reworded.map((c) => c.tier).join(', '))}:
      reworded only -- nothing a test author is told or given changes.</p>` : ''}
  </div>`;
}

function testingSummary(testing, diff) {
  /* A `usable` verdict is a claim about what an agent that has never seen this
     repository may write against, and the canary is the only thing that makes
     it more than a claim. Rendering the verdict alone would make a reading
     whose entire difference is carrying canaries display identically to one
     without them -- the card saying "same as before" about the only change in
     it. */
  const proof = (t) => {
    if (t.verdict !== 'usable') return '';
    return (t.canary || '').trim()
      ? `<span class="pill pill-green ts-proof">provable</span>`
      : `<span class="pill pill-amber ts-proof">unproved</span>`;
  };
  /* And the capability beside the tiers, for the same reason as the canaries
     above. A reading whose entire substance is a disposable database would
     otherwise render as three tiers identical to the ones already in force --
     the card saying nothing has changed about the only thing that has. */
  const db = (testing || {}).disposable_db;
  const dbRow = db && (db.env || '').trim() ? `
    <div class="ts-tier">
      <div class="ts-head">
        <span class="ts-name">test db</span>
        <span class="pill pill-green ts-proof">new</span>
        <span class="mono ts-runner">${esc(db.env)}</span>
      </div>
      <p class="ts-rule ts-sub mono"><span class="ts-match">url</span>${esc(db.url || '')}</p>
      ${(db.prepare || []).map((c) => `
        <p class="ts-rule ts-sub mono"><span class="ts-match">prepare</span>${esc(c)}</p>`).join('')}
      ${db.migrate ? `<p class="ts-rule ts-sub mono"><span class="ts-match">migrate</span>${esc(db.migrate)}</p>` : ''}
    </div>` : '';

  return dbRow + ((testing && testing.tiers) || []).map((t) => `
    <div class="ts-tier">
      ${/* The three heads are the reading. Each note is a paragraph of evidence
           for one of them, and three paragraphs stacked is a wall nobody reads
           to decide a checkbox -- so the verdicts stay, and the reasoning is
           one click away for the one you doubt. */''}
      <details class="ts-why">
        <summary class="ts-head">
          <span class="ts-name">${esc(t.tier)}</span>
          <span class="pill ${VERDICT_PILL[t.verdict] || ''}">${esc(t.verdict)}</span>
          ${proof(t)}
          ${cleanupPill(t)}
          <span class="ts-runner mono">${esc(t.runner || 'no runner')}</span>
          <span class="ts-more">why</span>
        </summary>
        ${cleanupNote(t)}
        ${t.note ? `<p class="ts-note">${esc(t.note)}</p>` : ''}
        ${canaryNote(t)}
      </details>
      ${cleanupGist(t)}
      ${cleanupChoice(t, diff)}
    </div>`).join('');
}

function resurveyBlock(p) {
  const rs = state.resurvey;
  if (!rs || !rs.diff) return '';
  const diff = rs.diff;
  const ruled = rs.ruling;
  /* Nothing a reading proposes is ruled on here. A change to a check waits in
     its row on Checks, a change to how this project is tested on Tests, and a
     change to where it runs on Environment -- each on the page it would
     change. This says where, and what the reading found, in one place. */
  const pid = encodeURIComponent(state.projectId);
  const where = [
    [waitingChanges().length, 'checks', 'Checks'],
    [partsWaiting('tests').length, 'tests', 'Tests'],
    [partsWaiting('environment').length, 'environment', 'Environment'],
  ].filter(([n]) => n);
  if (where.length) {
    const total = where.reduce((n, [k]) => n + k, 0);
    const links = where.map(([n, sec, label]) => `${n} on <a class="inline-act"
      href="#/${pid}?gates=${sec}">${label}</a>`);
    return `<div class="proposal-note">${icon('l-circle-alert', 'ic ic-sm')}
      <p><b>The re-survey proposes ${total} change${total === 1 ? '' : 's'}</b>, each on the page it
        would change: ${links.join(', ')}. ${esc(diff.summary || '')}</p></div>`;
  }

  /* Once you have ruled, the list below IS the ruling: a gate you accepted is a
     live row in it, and one you turned down is absent from it. The ledger
     keeps the record; this screen keeps only what the list cannot show, which
     is a check that was accepted and has never been run. A whole field
     accepted by name -- `testing`, `environment` -- is not a check and is
     never run, so it is never "accepted and not run yet"; a removed check is
     gone, and will never be run again. */
  if (ruled) {
    const ran = new Set((((p.baseline || {}).results) || []).map((r) => r.name));
    const unrun = (ruled.applied || [])
      .filter((a) => a.includes(':'))
      .filter((a) => !a.startsWith('remove:') && !a.startsWith('drop:'))
      .map((a) => a.slice(a.indexOf(':') + 1))
      .filter((name) => !ran.has(name));
    if (!unrun.length) return '';
    return `<p class="rs-ruled small dim">
      <span class="mono">${unrun.map(esc).join(' · ')}</span>
      ${unrun.length > 1 ? 'were accepted and have' : 'was accepted and has'} not been run yet:
      run the checks, then approve.
    </p>`;
  }

  /* "It read the repo and proposes nothing" is an answer, and an answer is
     owed only to whoever asked the question. `state.resurvey` is the last
     proposal *ever* run, so keyed on that alone this card would stand on the
     screen forever after any past re-survey that found nothing -- a transient
     reply pinned above a list it has nothing to say about. It appears only for
     the reader who just pressed the button, and is gone the next time they open
     the screen. */
  if (!state.askedProposal) return '';
  return `<div class="card rs-none">
    <p class="small"><b>Nothing proposed.</b>
      ${esc(diff.summary || 'The check list still describes this repository.')}</p>
  </div>`;
}

/* The tab each part of a reading is ruled on. */
const PART_TAB = {
  testing: 'tests', test_file_commands: 'tests', trace_dirs: 'tests', blind_placements: 'tests',
  scaffolding: 'tests', preview: 'environment', environment: 'environment',
};

/* What each part is called where a person rules on it. */
const PART_NAMES = {
  testing: 'what each level can test', test_file_commands: 'how one test file runs',
  trace_dirs: 'where runs are recorded', blind_placements: 'where blind tests go',
  scaffolding: 'files this project needs', preview: 'what opens at review', environment: 'environment',
};

/* The parts of a reading waiting on a ruling, on one tab or on any. */
function partsWaiting(tab) {
  const rs = state.resurvey;
  if (!rs || !rs.diff) return [];
  return (rs.pending_parts || []).filter((part) => !tab || PART_TAB[part] === tab);
}

/* Each part of a reading, as the card it is ruled on. Built from the diff and
   what is recorded; the card says what would change, and why. */
function readingParts(p, diff) {
  let scaffoldNote = '';
  const extras = [
    diff.testing ? (() => {
      const { fresh, restated, id } = classifyProposed(
        'tiers', diff.testing.tiers, ((p.testing || {}).tiers) || []);
      // The capability is not a tier, and a reading whose whole substance is a
      // disposable database can have three tiers identical to the ones in force.
      const dbMoved = JSON.stringify(diff.testing.disposable_db || null)
                   !== JSON.stringify((p.testing || {}).disposable_db || null);
      if (!fresh.length && !dbMoved) return null;
      /* What each level's reading changes, field by field, and open. Not a
         folded diff of one line per level -- `unit  usable` -- under which a
         reading that adds two helper files, eight fixtures, a canary and new
         import lines to the unit level shows "2 changed lines" and nothing a
         person can recognise, and levels whose only change is wording count
         as changed too. */
      const before = new Map(((p.testing || {}).tiers || []).map((t) => [t.tier, t]));
      const changes = fresh.map((t) => ({ tier: t.tier, ...tierChanges(before.get(t.tier), t) }));
      const moved = changes.filter((c) => c.lines.length);
      const reworded = changes.filter((c) => !c.lines.length && c.reworded);
      return { key: 'testing', field: 'testing', verb: 'replace',
               delta: [moved.length ? `${moved.map((c) => c.tier).join(', ')} changes` : '',
                       reworded.length ? `${reworded.map((c) => c.tier).join(', ')} reworded` : '',
                       dbMoved ? 'disposable_db' : ''].filter((part) => part).join(' · ') || 'no change',
               diff: tierChangeList(moved, reworded),
               reason: diff.testing_reason,
               restated: restatedLine('tiers', restated, id),
               // The capability travels with the surface, not with a tier, so it
               // rides along whichever tiers survived the comparison.
               body: testingSummary({ ...diff.testing, tiers: fresh }, diff) }; })() : null,
    /* A plain list of directories, so no identity field and nothing restated:
       two strings are the same string or they are not. Drawn at all because a
       project with no recording directory has no recordings, and the run says
       so every time until somebody answers. */
    (diff.trace_dirs || []).length
      ? {
        key: 'trace_dirs', field: 'trace_dirs', verb: 'replace',
        delta: `${(diff.trace_dirs || []).length} director${
          (diff.trace_dirs || []).length === 1 ? 'y' : 'ies'}`,
        diff: diffLines(p.trace_dirs || [], diff.trace_dirs || []),
        reason: diff.trace_dirs_reason,
        /* A body, and not only because every card has one: `extras` drops a
           card that has nothing to show, which is the right rule -- a tick
           under a button that applies something, with nothing visible to read,
           is how a person agrees to what they could not see. Without a body
           this card would be silently filtered for exactly that reason, and a
           re-survey could propose the directory while the screen said nothing
           was proposed. */
        body: (diff.trace_dirs || []).map((d) => `
          <p class="ts-rule mono"><span class="ts-match">records into</span>
            ${esc(d)}</p>`).join(''),
      }
      : null,
    (diff.test_file_commands || []).length
      ? (() => {
        const { fresh, restated, id } = classifyProposed(
          'test_file_commands', diff.test_file_commands, p.test_file_commands);
        /* Every part of a rule, not the command alone: a proposal can change
           one rule's `report` and nothing else, and a diff built from match and
           command would come back empty, showing no change at all under a
           button that applies one. */
        const line = (r) => [`${r.match}   ${r.command}`,
                             `${r.match}   report: ${r.report || '(none)'}`,
                             `${r.match}   load: ${r.collect || '(none)'}`];
        const a = (p.test_file_commands || []).flatMap(line);
        const b = (diff.test_file_commands || []).flatMap(line);
        return { key: 'test_file_commands', field: 'test_file_commands', verb: 'replace',
          delta: b.length > a.length ? `${(b.length - a.length) / 3} rule added`
                 : b.length < a.length ? `${(a.length - b.length) / 3} rule removed`
                 : fresh.length ? `${fresh.map((r) => r.match).join(', ')} changed` : 'rules',
          diff: diffLines(a, b),
          reason: diff.test_file_commands_reason,
          restated: restatedLine('test_file_commands', restated, id),
          rules: fresh,
              /* `collect` is rendered too, and its absence is named. Without
                 it a proposal that changes only the load command would draw
                 two rows identical to the two already recorded -- a diff with
                 nothing visibly different in it, under a button that applies
                 it. */
              body: fresh.map((r) => `
            <p class="ts-rule mono"><span class="ts-match">${esc(r.match)}</span>
              ${esc(r.command)}</p>
            <p class="ts-rule ts-sub mono"><span class="ts-match">report</span>
              ${r.report ? esc(r.report)
                         : '<span class="dim">no per-test report; a file is the smallest verdict</span>'}</p>
            <p class="ts-rule ts-sub mono"><span class="ts-match">load</span>
              ${r.collect ? esc(r.collect)
                          : '<span class="dim">no load-only mode for this runner</span>'}</p>`)
              .join('') }; })()
      : null,
    (() => {
      // `apply_survey_diff` merges by path and `apply_scaffolding` refuses to
      // overwrite, so a file already offered -- or already on disk -- is not a
      // change a tickbox can make. Drawing it asks a reader to rule a second
      // time on a card that does nothing.
      //
      // Saying so rather than dropping it, because the two cases differ and one
      // of them is a dead end: a reading that wants to ADD THREE LINES to an
      // existing conftest can only express that as the whole file, and a
      // tickbox never writes a whole file over one. That proposal cannot land through this
      // mechanism at all, and a human who is not told will keep accepting it.
      /* Three states, of which `scaffolding_applied` can tell two apart: it
         says a file was written once; it cannot say whether what is there now
         is what is being proposed. Read alone, a file already replaced would
         draw the same card as one still needing it, and pressing it would
         write nothing -- which is correct, and indistinguishable from a button
         that does not work. The server compares the bytes, because the
         console never sees the repo. */
      const st = (state.project || {}).scaffolding_state || {};
      /* A file that belongs to a cleanup fix is shown inside that fix, and
         written when the fix is chosen -- never also here, as a card that looks
         unrelated to it. */
      const inFix = new Set(((diff.testing || {}).tiers || [])
        .flatMap((t) => (t.cleanup_options || []).flatMap((o) => o.files || [])));
      const loose = (diff.scaffolding || []).filter((f) => !inFix.has(f.path));
      const files = loose.filter((f) => st[f.path] === 'missing');
      const blocked = loose.filter((f) => st[f.path] === 'differs');
      const settled = loose.filter((f) => st[f.path] === 'same');
      /* Naming the file and shrugging -- "already in your repository, and
         scaffolding never writes over what is there" -- would be a message
         with nothing in it a reader can do. The reading is not wrong to want
         it: a proposal that adds one line to an existing conftest can be the
         only thing standing between a project's factories and any test being
         able to name one, and as a shrug it could be accepted forever and
         never land.

         So it is a change like any other, with the two things that make
         replacing somebody's file a decision rather than a leap: the diff, and
         a button that says what it does. */
      scaffoldNote = [
        blocked.map((f) => proposalCard({
          field: `scaffolding · ${f.path}`,
          verb: 'replace file',
          delta: `${String(f.contents || '').split('\n').length} lines`,
          diff: '',
          body: (f.summary || '').trim() ? `<div class="sc-file"><p class="sc-summary">${
            esc(f.summary)}</p></div>` : '',
          why: f.purpose || '',
          checkbox: '',
          act: `
          <p class="p-cost">Scaffolding normally only adds files. This one is already in your
            repository, so accepting it writes over what is there — committed, so the version you
            have now stays in your history.</p>
          <div class="rec-act">
            <button class="btn btn-sm" data-scaffold-diff="${esc(f.path)}">Show what changes</button>
            <button class="btn btn-sm btn-primary" data-replace-scaffold="${esc(f.path)}"
              >Replace the file</button>
          </div>
          <pre class="hz-pre diff-out" data-diff-for="${esc(f.path)}" hidden></pre>`,
        })).join(''),
        settled.length ? `<p class="ts-restated small dim">${settled.length} proposed file${
          settled.length === 1 ? ' is' : 's are'} already exactly as proposed:
          <span class="mono">${settled.map((f) => esc(f.path)).join(' · ')}</span>. Nothing to
          decide about ${settled.length === 1 ? 'it' : 'them'}.</p>` : '',
      ].join('');
      /* A title, an action and a count, like every other card: without them the
         header draws a bare checkbox and an empty badge. And the files already in
         the repository are named here too, because they are drawn as their own
         cards below and the reason above speaks for all of them. */
      return files.length
      ? { key: 'scaffolding', field: 'files this project needs', verb: 'add',
          delta: `${files.length} file${files.length === 1 ? '' : 's'}`
                 + (blocked.length ? ` · ${blocked.length} to replace, below` : ''),
          reason: diff.scaffolding_reason,
          /* Never the purpose cut at a character count, which lands mid-word
             and reads as a rendering fault rather than a summary -- and this is
             the text a human weighs to decide whether a file belongs in their
             repository. */
          /* Nor the whole purpose, which the card already carries behind a
             disclosure: printed here too it is a hundred-odd words twice, on a
             page that is already long. First sentence, then ask. */
          /* The plain sentence written for a person; the purpose, written for
             whoever maintains the file, one click down. A slice of the purpose
             is never shown in its place -- it opens with a category label
             ("a test at the user level cannot...") and reads as noise. */
          body: files.map((f) => `
            <div class="sc-file">
              <p class="mono sc-path">${esc(f.path)}</p>
              ${(f.summary || '').trim() ? `<p class="sc-summary">${esc(f.summary)}</p>` : ''}
              ${(f.purpose || '').trim() ? `<details class="hz-more"><summary>what this file is for</summary>
                <p class="hz-why">${esc(f.purpose)}</p></details>` : ''}
            </div>`).join('') }
      : null; })(),
    (diff.blind_placements || []).length
      ? (() => {
        const { fresh, restated, id } = classifyProposed(
          'blind_placements', diff.blind_placements, p.blind_placements);
        return { key: 'blind_placements', field: 'where blind tests go', verb: 'replace',
          delta: `${fresh.length} place${fresh.length === 1 ? '' : 's'}`,
          reason: diff.blind_placements_reason,
          restated: restatedLine('blind_placements', restated, id),
          /* The canary's case name is on the row for the same reason a tier's
             canary is: a reading whose entire difference is that field would
             otherwise draw three placements identical to the three in force,
             and ask a human to re-approve the project over a change the card
             does not show. */
          body: fresh.map((b) => `
            <p class="ts-rule mono"><span class="ts-match">${esc(b.directory)}/</span>
              ${esc(b.filename)}${b.kind ? ` <span class="dim">${esc(b.kind)}</span>` : ''}</p>
            ${b.canary_case ? `<p class="ts-rule ts-sub mono"><span class="ts-match">its test is called</span>${esc(b.canary_case)}</p>` : ''}`).join('') }; })()
      : null,
    /* What a person opens at review. Its own card, so taking it never rebuilds
       the image the checks run in.

       Said as a sentence about the button, not as fields. As a one-line diff
       behind a fold, `opens web /` in code, with the note running past the
       card's edge, it would be the one card on the page about something a
       person does, and the only one they cannot read. */
    diff.preview ? (() => {
      const pv = diff.preview;
      const was = (p.environment || {}).preview;
      const where = (x) => `<b>${esc(x.open)}</b> service, at <span class="mono">${esc(x.path || '/')}</span>`;
      const lead = !pv.open
        ? `<p>Says there is nothing to open at review${pv.note ? `: ${esc(pv.note)}` : '.'}</p>`
        : `<p>At review, <b>Open the app</b> will open ${esc(p.name || 'this project')} in your
            browser: the ${where(pv)}.</p>`;
      const before = was && was.open ? `<p class="dim">Today it opens the ${where(was)}.</p>` : '';
      const starts = (pv.services || []).length ? `
          <p class="dim">To do that it also starts ${pv.services.length === 1 ? 'one thing' : 'these'}
            the tests don't need:</p>
          <ul class="p-plain-list">${pv.services.map((v) => `<li><b>${esc(v.name)}</b>
            <span class="mono">${esc(v.command)}</span></li>`).join('')}</ul>`
        : pv.open ? '<p class="dim">Nothing new is started: that service already runs for the checks.</p>' : '';
      const note = pv.open && pv.note ? `<p><span class="p-plain-k">What you'll see</span> ${ticks(pv.note)}</p>` : '';
      return { key: 'preview', field: 'preview', verb: was ? 'replace' : 'add',
        delta: pv.open ? 'the app at review' : 'nothing to open',
        diff: '',
        reason: diff.preview_reason,
        /* The lead already says what it does; the reading's reason is the
           evidence for it, one click down rather than restated above it. */
        whyLabel: 'how the survey worked this out',
        body: `<div class="p-plain">${lead}${before}${starts}${note}</div>`,
        act: `<p class="p-cost">Accepting changes nothing your checks run in. The check run that
          follows opens the app once on ${esc(p.base_ref || 'the main branch')}, and the Environment
          tab says whether it opened.</p>` };
    })() : null,
  ].filter(Boolean)
    /* A set with nothing new in it is not a change, and counting it as one is
       how "5 changes proposed" comes to stand over two cards a human has
       already ruled on. `testing` is exempt from the rule because its substance is not only
       its tiers -- a disposable database can be the entire point of a reading
       whose three tiers are word-for-word the ones in force. */
    .filter((x) => x.key === 'testing' || x.key === 'scaffolding'
                   || (x.body || '').trim() !== '');

  const environment = !diff.environment ? null : (() => {
    /* Which fields moved, counted rather than described. "environment ·
       compose" says only what kind it already is; what a reader is ruling
       on is that the image, the setup commands and the declared services
       are being replaced, and those are comparable. */
    const now = p.environment || {};
    const next = diff.environment || {};
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    const moved = Object.keys(next).filter((k) => !same(next[k], now[k]));
    /* An environment whose only change is a cleanup fix's reset step is
       part of that fix, applied when the fix is chosen. */
    const inFix = ((diff.testing || {}).tiers || [])
      .some((t) => (t.cleanup_options || []).some((o) => o.environment));
    if (inFix && moved.every((k) => k === 'test_prepare')) return null;
    const flat = (o) => moved.flatMap((k) => {
      const v = o[k];
      if (v === undefined || v === null || v === '') return [];
      if (Array.isArray(v)) return v.map((x) => `${k}: ${typeof x === 'string' ? x : JSON.stringify(x)}`);
      if (typeof v === 'string') return v.split('\n').map((l) => `${k}: ${l}`);
      return [`${k}: ${JSON.stringify(v)}`];
    });
    return {
      key: 'environment', field: 'environment', verb: 'replace',
      delta: moved.join(', ') || 'nothing differs',
      diff: diffLines(flat(now), flat(next)),
      why: diff.environment_reason || diff.environment.rationale || '',
      /* Which versions to ask for, and which files pin them, describe the
         environment: recording only those rebuilds nothing. */
      act: moved.every((k) => ['version_commands', 'version_files'].includes(k))
        ? '<p class="p-cost">Recording these rebuilds nothing and costs no run of the checks.</p>'
        : '<p class="p-cost">Accepting this rebuilds the image and costs a new baseline.</p>',
    };
  })();
  return { extras, scaffoldNote, environment };
}

/* What a reading proposes for one tab, each part a card with its own ruling,
   as each proposed check is in its row. A part whose card would show nothing
   different -- a restatement in other words -- still gets one, because the
   reading cannot be called current until it is ruled on. */
function partsBlock(p, tab) {
  const rs = state.resurvey;
  const waiting = partsWaiting(tab);
  const done = partsDone(p, tab);
  if (!waiting.length) return done;
  const { extras, scaffoldNote, environment } = readingParts(p, rs.diff);
  const built = new Map([...extras, ...(environment ? [environment] : [])].map((x) => [x.key, x]));
  const NAMES = PART_NAMES;
  const cards = waiting.map((key) => {
    const x = built.get(key) || {
      key, field: NAMES[key] || key, verb: 'restate', delta: 'nothing visible differs',
      reason: rs.diff[`${key}_reason`] || '',
      body: '<p class="small dim">Proposed again in other words; what it does is what is recorded.</p>',
    };
    const [yes, no] = key === 'environment' ? ['Use this environment', 'Keep the current one']
      : key === 'scaffolding' ? ['Offer these files', 'Leave them'] : ['Record it', 'Leave it'];
    return `<div class="rs-part" data-part="${esc(key)}">${proposalCard({
      field: NAMES[key] || x.field, verb: x.verb, delta: x.delta,
      ...(x.whyLabel ? { whyLabel: x.whyLabel, gist: false } : {}),
      diff: x.diff, body: x.body, why: x.reason, restated: x.restated,
      act: `${x.act || ''}
        <div class="rec-act">
          <button class="btn btn-sm btn-primary" data-rule-part="${esc(key)}" data-accept="1">${yes}</button>
          <button class="btn btn-sm btn-quiet" data-rule-part="${esc(key)}" data-accept="0">${no}</button>
          <span class="small dim">Either way, nothing else in the proposal changes.</span>
        </div>`,
    })}</div>`;
  }).join('');
  return `${done}<div class="proposal-note">${icon('l-circle-alert', 'ic ic-sm')}
    <p><b>The re-survey proposes ${waiting.length} change${waiting.length === 1 ? '' : 's'}
      here.</b> Nothing changes until you rule on each, one at a time.</p></div>
    <section class="rs-block">${cards}${tab === 'tests' && waiting.includes('scaffolding') ? scaffoldNote : ''}</section>`;
}

/* A part just accepted from the reading, until the checks have run on it:
   what it was, and the way back. */
const UNDOABLE_PARTS = ['testing', 'test_file_commands', 'trace_dirs', 'blind_placements'];
function partsDone(p, tab) {
  const ruled = ((state.resurvey || {}).checks) || {};
  const since = p.baseline_at || '';
  const fresh = Object.entries(ruled).filter(([key, r]) => PART_TAB[key] === tab
    && r.decision === 'applied' && r.at && (!since || r.at > since));
  if (!fresh.length) return '';
  return fresh.map(([key]) => `<p class="q-sub q-proposed">Recorded from the re-survey just now:
    <b>${esc(PART_NAMES[key] || key)}</b>. Run the checks to measure it.${UNDOABLE_PARTS.includes(key)
      ? ` <button type="button" class="linkish" data-undo-part="${esc(key)}">Undo</button>` : ''}</p>`).join('');
}

/* Gates come from what a repository already runs, so they are only as current
   as the reading that produced them. This says when that reading has gone
   stale. It is a fact and a link, never an automatic re-survey: what the
   project measures is a human's to change. */
function toolingDriftBlock(p, drift, opts = {}) {
  if (!drift) return '';
  // The quiet states are one line in the Survey column already. Only the state
  // that asks something of the reader earns a block of its own.
  if (!drift.stale) return opts.brief ? '' : (
    !drift.checked
      ? (drift.reason ? `<p class="small dim">Staleness unknown — ${esc(drift.reason)}.
          Survey it again to start tracking.</p>` : '')
      : `<p class="small dim">Tooling unchanged in the ${esc(drift.commits)} commit(s)
          since this survey. The check list still describes this repository.</p>`);
  const line = (label, items) => items.length
    ? `<p class="small"><b>${label}</b> <span class="mono">${items.slice(0, 12).map(esc).join(' · ')}</span>${
        items.length > 12 ? ` and ${items.length - 12} more` : ''}</p>` : '';
  return `
    <div class="card" style="margin-top:1rem">
      <p class="who">The check list may have stopped describing this repository</p>
      <p class="small">${esc(drift.commits)} commit(s) since this project was last read have
        touched files that decide what the checks should be. Nothing is failing, and that is the
        point: a tool added here gains a surface no check covers.</p>
      ${line('added', drift.added)}
      ${line('changed', drift.changed)}
      ${line('never read', drift.unseen)}
    </div>`;
}

/* Mirrors `gate_outcome` in gates.py. The server is what refuses an approval;
   this exists so the page says the same thing the server will, rather than
   offering a button that is about to be rejected. */
const GATE_ABSENT = [
  'command not found', 'no such file or directory',
  'is not recognized as an internal or external command',
  'modulenotfounderror', 'no module named', 'cannot find module',
  'executable file not found', 'permission denied',
];

function gateOutcome(r) {
  if (r.passed && !r.skipped) return 'ran';
  if (r.started === false) return 'could_not_run';
  if (r.timed_out) return 'could_not_run';
  if (r.exit_code === 126 || r.exit_code === 127) return 'could_not_run';
  const tail = (r.output_tail || '').toLowerCase();
  if (GATE_ABSENT.some((m) => tail.includes(m))) return 'could_not_run';
  if (r.exit_code !== 0 && !tail.trim()) return 'unknown';
  return 'ran';
}

/* Whether this check passed on its own terms. Mirrors `is_green` in gates.py,
   which is what the server refuses an approval over -- so this has to agree
   with it, or the button offers a press the API turns down. `optional` buys a
   check nothing here, on purpose. */
function isGreen(r) {
  return Boolean(r && r.started !== false && r.passed && !r.skipped);
}

function unrunnableGates(baseline) {
  return ((baseline && baseline.results) || [])
    .filter((r) => !r.skipped && gateOutcome(r) === 'could_not_run');
}

/* One baseline row. The output tail is 4,000 characters in the store and is
   drawn whole, not clipped to a few hundred -- which is the difference
   between "backend-lint failed" and knowing it found 129 errors and where. The
   summary line stays short; the whole thing is one click away. */
function gateResultRow(r) {
  const tail = r.output_tail || '';
  const head = tail.split('\n').filter((l) => l.trim())[0] || '';
  const outcome = r.skipped ? 'skipped' : r.passed ? 'passed' : 'failed';
  /* What the same gate did at the commit this work branched from. A red gate on
     its own says nothing on a repository that was already red; this is the
     sentence that makes it readable. */
  const base = {
    blind_tests: ['pill', 'only the blind tests fail'],
    oracle_files: ['pill pill-amber', 'the oracle’s files fail it'],
  }[r.failed_on] || {
    failed: ['pill', 'already failing before this'],
    passed: ['pill pill-red', 'this feature broke it'],
    could_not_run: ['pill pill-amber', 'base could not be measured'],
  }[r.at_base];
  return `<div class="mat-file gate-result ${outcome}${r.at_base === 'failed' ? ' inherited' : ''}">
    <span class="path">${r.passed || r.skipped ? '' : '<span class="test-bad">✕</span> '}${esc(r.name)}
      ${base ? `<span class="${base[0]}">${esc(base[1])}</span>` : ''}</span>
    <span class="lines">${outcome} · ${dur(r.duration_s)}${
      r.metric != null ? ` · ${esc(r.metric)}` : ''}${
      r.timed_out ? ' · timed out' : ''}${
      r.exit_code ? ` · exit ${esc(r.exit_code)}` : ''}${
      r.base_sha ? ` · compared with ${esc(r.base_sha.slice(0, 12))}` : ''}</span>
    <span class="why mono">${esc(head.slice(0, 160))}</span>
    ${tail ? `<details class="raw gate-out"><summary>output · ${bytes(tail.length)}</summary>
      <pre>${esc(tail)}</pre>
      <p class="small dim mono">$ ${esc(r.command || '')}</p></details>` : ''}
  </div>`;
}

/* What a red baseline means, which depends on whether the gate ran.

   A gate that runs and reports 129 lint errors is a working gate: every feature
   re-runs it at the commit that feature branched from, so the packet says
   whether the change caused the failure or inherited it. That is informative
   from the first feature, on a repository that has never been clean.

   A gate that cannot run is different in kind. It is red forever, it reports on
   nothing, and it looks like coverage. The server refuses to approve around one. */
/* THE FIRST RUN.

   Drawn whether or not anything failed: a block that renders nothing at all
   unless something failed is, on a healthy project, an empty region.

   What belongs here is the run rather than the list. `checks` is what reading
   the repository proposed; this is what happened when those commands were
   executed on a checkout nobody had touched -- how many started, how long they
   took, what was installed first, and what could not run. */
function firstRunSection(p, baseline, env) {
  if (!baseline) return '<p class="empty">These checks have never been run.</p>';
  const results = baseline.results || [];
  const setup = results.filter((r) => r.name.startsWith('setup['));
  // A warm-up (`setup[warm:…]`) is allowed to fail: it runs a check with the
  // network only so its tool can fetch, and how it did is not the point.
  const setupFailed = setup.filter((r) => !r.passed && !r.skipped);
  const checks = results.filter((r) => !r.name.startsWith('setup['));
  const broken = unrunnableGates(baseline);
  const passed = checks.filter((r) => r.passed).length;
  const skipped = checks.filter((r) => r.skipped).length;
  const clock = results.reduce((a, r) => a + (r.duration_s || 0), 0);

  return `
    <dl class="facts-list">
      ${factRow('checks executed', `<b>${esc(checks.length - skipped)}</b> of ${
        esc(checks.length)}${skipped ? ` · ${esc(skipped)} skipped` : ''}`)}
      ${factRow('passed', `<b>${esc(passed)}</b> of ${esc(checks.length)}`)}
      ${factRow('could not run', broken.length
        ? `<span class="warn">${esc(broken.length)}</span>
           <p class="mono dim small">${broken.map((r) => esc(r.name)).join(' · ')}</p>
           <p class="dim small">A check that never started reports on nothing, and is red for
             every feature built here. Fix where it runs, or drop it.</p>`
        : 'none — every check started and reported')}
      ${factRow('on the clock', esc(dur(clock)))}
      ${factRow('installed first', setup.length
        ? `<ol class="cmd-list">${setup.map((r) => `<li class="mono">${esc(r.command)}${
            r.name.startsWith('setup[warm:') ? ' <span class="dim">(run once with the network, so its tool could fetch what it fetches only when it runs)</span>' : ''}</li>`).join('')}</ol>
           <p class="dim small">${setupFailed.length
             ? `<span class="warn">${esc(setupFailed.length)} of these failed.</span> Anything
                a check needed from them was missing when it ran.`
             : 'All of these worked. The checks ran with the network off.'}</p>`
        : 'nothing — the checks run against the environment as it is')}
    </dl>

    ${baselineVerdict(p, baseline)}

    ${setup.length ? `<details class="gline ${setupFailed.length ? 'failed' : 'passed'} muted-row"
        style="margin-top:1rem">
      <summary>
        <span class="g-mark">${icon(setupFailed.length ? 'l-x' : 'l-check', 'ic ic-sm')}</span>
        <span class="g-name">setup output</span>
        <span class="g-cmd mono">${esc(((env && env.setup) || []).join(' ; '))}</span>
        <span class="g-meta mono">${setupFailed.length
          ? `${esc(setupFailed.length)} of ${esc(setup.length)} failed`
          : `${esc(setup.length)} command${setup.length > 1 ? 's' : ''} ran`} ·
          ${esc(dur(setup.reduce((a, r) => a + r.duration_s, 0)))}</span>
      </summary>
      <div class="g-nest">${setup.map((r) => gateLine(null, r)).join('')}</div>
    </details>` : ''}`;
}

function baselineVerdict(p, baseline, opts = {}) {
  if (!baseline) return '';
  const broken = unrunnableGates(baseline);
  const failing = (baseline.results || []).filter((r) => !r.passed && !r.skipped);
  const inherited = failing.filter((r) => gateOutcome(r) !== 'could_not_run');
  if (!failing.length) return '';

  // The headline already carries the count and the action, and the rows carry
  // the names. What is left for this block is the part neither can hold: why an
  // absence is worse than a failure.
  const blocked = broken.length ? `
    <div class="objection" style="margin-top:1rem">
      <p class="who">${icon('l-circle-alert', 'ic ic-sm')}Why this blocks</p>
      <p>Nothing executed — the tool is not in this environment, or the command never started.
        A check like this is red for every feature ever built here and never reports on
        anything, which is worse than having no check at all, because a red row looks like
        coverage.</p>
    </div>` : '';

  // On a page that already opens with a one-line verdict, repeating "2 gates are
  // red and it does not block" at length is the same sentence twice. The
  // blocking objection still earns its space; this does not.
  const known = inherited.length && !opts.brief ? `
    <div class="card" style="margin-top:1rem">
      <p class="who">${esc(inherited.length)} check(s) fail on code nobody has touched</p>
      <p class="small">These block approval. They ran and reported, which is what this step asks
        of them mechanically — but a check that is already red cannot tell you whether a feature
        made things worse, because it was failing to catch that class of problem before anyone
        started. Fix it, ratchet it at today's number so a regression still shows, or decline it
        with a reason.</p>
      <p class="small mono dim">${inherited.map((r) => esc(r.name)).join(' · ')}</p>
    </div>` : '';
  return blocked + known;
}
