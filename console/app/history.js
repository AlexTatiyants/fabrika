/* History: the project's ledger read as events in words, newest first, a day
   at a time. */

'use strict';

/* ------------------------------------------------------------------ history

   The project's ledger, read. Raw, it is rows of record kinds and byte counts
   -- 132 of them on one project, half of them whole snapshots of the project
   -- and what a person comes to it for -- what Fabrika wrote into their
   repository, what they decided, when a check went red -- is in there and not
   findable. `factory/history.py` turns records into events, in words, with who
   did each; this draws them, newest first, a day at a time. Every event names
   the record it was read from and opens it, and the raw ledger is one link
   away, so reading it this way hides nothing. */
const HISTORY_FILTERS = [
  ['', 'Everything'], ['you', 'Your decisions'], ['repo', 'Written to your repository'],
  ['checks', 'Checks'], ['reading', 'Readings'], ['feature', 'Features'],
];

function historyTags(e) {
  const tags = [e.kind];
  if (e.actor === 'you' && e.kind !== 'feature') tags.push('you');
  return tags;
}

function readingCost(c) {
  if (!c) return '';
  return c.billed ? `${usd(c.billed)} charged`
    : c.notional ? `≈ ${usd(c.notional)} on your subscription` : '';
}

function historyEvent(e, pid) {
  const when = new Date(e.at);
  const time = when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  const link = e.feature_id ? `<a href="${featureHref(pid, e.feature_id)}">${esc(e.feature)}</a>` : '';
  let title = esc(e.title);
  let detail = e.detail || '';
  if (e.kind === 'feature') {
    title = e.actor === 'you' ? `${esc(e.title)} ${link}` : `${link} ${esc(e.title)}`;
    if (e.verdict) detail = `Fabrika says: ${((VERDICT_WORD[e.verdict] || [])[0] || e.verdict).toLowerCase()}`;
  } else if (e.feature_id) {
    title = esc(e.title).replace(esc(e.feature), link);
  }
  if (e.first_at) {
    // The day it started, when that is not the day it is filed under.
    const first = new Date(e.first_at);
    const from = first.toLocaleString([], first.toDateString() === when.toDateString()
      ? { hour: '2-digit', minute: '2-digit', hour12: false }
      : { weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
    detail = `from ${from}${detail ? ` · ${detail}` : ''}`;
  }
  const cost = readingCost(e.cost);
  if (cost) detail = detail ? `${detail} · ${cost}` : cost;
  const lamp = e.lamp === 'ok' || e.lamp === 'bad' ? `<i class="hx-lamp ${e.lamp}"></i>` : '';
  const items = (e.items || []).map((i) => `<span>${esc(i)}</span>`).join('')
    + (e.commit ? `<span class="hx-commit">commit <b>${esc(e.commit.slice(0, 7))}</b></span>` : '');
  const open = e.seq != null && state.historyRecord && state.historyRecord.seq === e.seq;
  return `<div class="hx-ev${e.lamp === 'quiet' ? ' quiet' : ''}">
      <span class="hx-t">${esc(time)}</span>
      <span class="hx-who ${e.actor === 'you' ? 'you' : 'fab'}">${e.actor === 'you' ? 'you' : 'Fabrika'}</span>
      <div class="hx-b"><p class="hx-title">${lamp}${title}</p>${detail ? `<p class="hx-d">${esc(detail)}</p>` : ''}
        ${items ? `<div class="hx-items">${items}</div>` : ''}</div>
      ${e.seq != null ? `<button type="button" class="hx-rec" data-history-record="${e.seq}"
        aria-expanded="${open}" title="The record this line was read from">${
          e.count > 1 && e.first_seq ? `#${e.first_seq}–` : '#'}${e.seq}</button>` : '<span></span>'}
      ${open ? `<pre class="hx-raw">${esc(JSON.stringify(state.historyRecord.payload, null, 1))}</pre>` : ''}
    </div>`;
}

function historySection(p) {
  const h = state.history && state.history.project === p.project_id ? state.history : null;
  const head = sectionHead('history', 'History', '', h && h.since
    ? `since ${esc(new Date(h.since).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' }))}`
    : '', []);
  if (!h) return `${head}<p class="empty">Reading the ledger…</p>`;
  const s = h.summary;
  const f = state.historyFilter || '';
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const cost = s.reading_cost ? readingCost(s.reading_cost) : '';
  const cells = [
    ['repo', plural(s.commits, 'commit'), `written to your repository · ${plural(s.files, 'file')}`],
    ['you', plural(s.decisions, 'decision'), 'made by you'],
    ['checks', plural(s.check_runs, 'check run'), s.last_run
      ? `last: ${s.last_run.title.replace(/^Checks ran: /, '').replace(/^Checks could not run: /, '')}` : 'none yet'],
    ['reading', plural(s.readings, 'reading'), [
      `${plural(s.surveys, 'survey')}, ${plural(s.readings - s.surveys, 're-read')}`,
      s.quiet ? `${s.quiet} found nothing` : '',
      cost ? `${cost}${s.readings_costed < s.readings ? ` for the ${s.readings_costed} costed` : ''}` : '',
    ].filter(Boolean).join(' · ')],
  ].map(([key, n, l]) => `<button type="button" class="hx-cell ${f === key ? 'on' : ''}"
      data-history-filter="${key}"><span class="hx-n">${esc(n)}</span><span class="hx-l">${esc(l)}</span></button>`)
    .join('');
  const chips = HISTORY_FILTERS.map(([key, label]) => `<button type="button" class="${f === key ? 'on' : ''}"
      data-history-filter="${key}" aria-pressed="${f === key}">${esc(label)}</button>`).join('');
  const shown = h.events.filter((e) => !f || historyTags(e).includes(f));
  let day = '';
  const rows = shown.map((e) => {
    const d = new Date(e.at).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
    const header = d !== day ? `<h4 class="hx-day">${esc(d)}</h4>` : '';
    day = d;
    return header + historyEvent(e, p.project_id);
  }).join('');
  return `${head}
    <p class="tab-intro">Everything that has happened to this project, and who did it. Each feature's own
      step-by-step record is on its page. <a href="#" data-help-open="history">More about history</a></p>
    <div class="hx-sum">${cells}</div>
    <div class="hx-chips" role="group" aria-label="Show">${chips}</div>
    <div class="hx-list">${rows || '<p class="empty">Nothing of this kind yet.</p>'}</div>
    <p class="hx-foot">Read from this project's ledger: ${esc(h.records)} records, append-only. Timings are
      left out, and re-readings in a row with nothing of yours between them are one line.
      <a href="#/${encodeURIComponent(p.project_id)}?log">Show every record</a></p>`;
}

function overviewSection(p) {
  const ov = state.overview && state.overview.project === p.project_id ? state.overview : null;
  const pid = p.project_id;
  const tab = (id) => `#/${encodeURIComponent(pid)}?gates=${id}`;
  const proj = state.project || {};
  const results = ((p.baseline || {}).results) || [];
  const scored = results.filter((r) => !/^setup\[/.test(r.name));
  const red = scored.filter((r) => !r.passed && !r.skipped).length;
  const gates = (p.gates || []).length;
  const recs = liveRecommendations();
  const checkRecs = recs.filter((r) => todoSection(r) === 'checks').length;
  const testRecs = recs.filter((r) => todoSection(r) === 'tests').length;
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const skipped = Object.keys(proj.unchecked_levels || {});
  const tiers = ((p.testing || {}).tiers) || [];
  const fixes = undecidedLevels(p).length;
  const waitingProposal = surveyWaiting() || waitingChanges().length > 0;
  const drift = proj.tooling_drift || {};
  const env = p.environment || {};
  const problems = (proj.environment_problems || []).length;
  // Fabrika's own copy, not yet in the repository; or one the checks predate.
  const df = proj.dockerfile || {};
  const w = ov ? ov.waiting : [];
  const b = ov ? ov.building : [];
  const shipReady = w.filter((f) => ['ship', 'ship_with_rulings'].includes(f.verdict)).length;
  const approving = p.stage === 'awaiting_approval';
  const healthy = !red && !fixes && !problems && !waitingProposal && !drift.stale && !approving;

  const lead = !ov ? 'Reading this project…'
    : approving ? (red ? `Before any feature is built here, its checks need you: ${red} of them ${
        red === 1 ? 'is' : 'are'} red on code nobody has changed.`
      : 'Before any feature is built here, accept its checks: they all pass on code nobody has changed.')
      + (movesDockerfile(proj) ? ` Accepting them also commits the Dockerfile they run in to your
        repository, as ${proj.dockerfile.path}.` : '')
    : [w.length ? `${w.length === 1 ? 'One feature is' : `${w.length} features are`} waiting on you${
        shipReady ? `, and ${shipReady === w.length ? (w.length === 1 ? 'it is' : 'all of them are')
          : `${shipReady} of them ${shipReady === 1 ? 'is' : 'are'}`} ready to ship${
          w.some((f) => f.verdict === 'ship_with_rulings') ? ' with rulings' : ''}` : ''}.`
        : b.length ? `Nothing is waiting on you; ${plural(b.length, 'feature')} ${b.length === 1 ? 'is' : 'are'} building.`
          : 'Nothing is waiting on you.',
      healthy ? 'The project itself is in good shape.'
        : 'The project itself needs attention: see the amber tiles.'].join(' ');

  const tiles = [
    overviewTile('Features', ov ? (w.length ? `${w.length} waiting on you` : b.length ? `${b.length} building` : 'Nothing open') : '…',
      ov ? [b.length ? `${b.length} building` : 'nothing building',
            ov.finished_total ? `${ov.finished_total} finished` : 'none finished yet'].join(' · ') : '',
      w.length ? 'warn' : ov ? 'lit' : ''),
    overviewTile('Checks', approving ? 'Waiting for you to accept them'
      : !scored.length ? 'Never run' : red ? `${red} of ${gates} red` : `All ${gates} green`,
      [approving ? (red ? `${red} of ${gates} red` : `all ${gates} green`) : '',
       checkRecs ? plural(checkRecs, 'suggestion') : 'no suggestions',
       `coverage ${coverageState(p).on ? 'on' : 'off'}`,
       p.baseline_at ? `measured ${esc(rel(p.baseline_at))}` : ''].filter(Boolean).join(' · '),
      red ? 'bad' : approving || !scored.length ? 'warn' : 'lit', tab('checks')),
    overviewTile('Tests', tiers.length ? (skipped.length ? `${3 - skipped.length} of 3 levels checked`
      : 'All 3 levels checked') : 'Not read yet',
      [fixes ? plural(fixes, 'fix') + ' to choose' : '', testRecs ? plural(testRecs, 'suggestion') : '']
        .filter(Boolean).join(' · ') || (skipped.length ? `${skipped.join(' and ')} checked by you` : 'nothing to do'),
      fixes ? 'warn' : 'lit', tab('tests')),
    (() => {
      const gs = guideView().guides || [];
      const named = ['AGENTS.md', 'DESIGN.md'].filter((n) => gs.some((g) => g.path === n));
      const skills = gs.filter((g) => g.layer === 'skill').length;
      const proposals = guideProposals();
      return overviewTile('Guides',
        [...named, skills ? plural(skills, 'skill') : ''].filter(Boolean).join(' · ') || 'None in the repository',
        proposals ? plural(proposals, 'proposal') + ' to review' : 'read from the repository',
        proposals ? 'warn' : 'lit', tab('guides'));
    })(),
    overviewTile('Environment', problems ? plural(problems, 'problem')
      : df.unmeasured ? 'Changed since the checks ran'
      : (df.source === 'fabrika' && !df.own) ? 'Dockerfile to move' : 'Ready',
      df.unmeasured ? 'run the checks to measure it'
        : !problems && (df.source === 'fabrika' && !df.own) ? `into your repository, as ${esc(df.path)}`
        : esc({ compose: 'Docker Compose', dockerfile: 'a Docker image built for it', image: 'a Docker image' }[env.kind]
          || env.kind || 'not decided yet'),
      problems ? 'bad' : df.unmeasured || (df.source === 'fabrika' && !df.own) ? 'warn' : 'lit', tab('environment')),
    overviewTile('Survey', waitingProposal ? 'A proposal is waiting' : drift.stale ? 'Behind the code' : 'Up to date',
      waitingProposal ? 'a re-reading proposes changes'
        : drift.stale ? `${(drift.added || []).length + (drift.changed || []).length} tooling file(s) moved`
        : drift.commits ? `main moved ${plural(drift.commits, 'commit')}; nothing it reads changed` : 'nothing moved',
      waitingProposal || drift.stale ? 'warn' : 'lit', tab('survey')),
    costTile(ov),
  ].join('');

  return `
    <p class="db-lead">${esc(lead)}</p>
    <div class="db-tiles">${tiles}</div>
    <section class="db-sec" id="waiting">
      <h3 class="db-h">Waiting on you <span class="db-count">${w.length}</span></h3>
      ${!ov ? '<p class="db-empty">Loading…</p>' : w.length
        ? w.map((f) => waitingRow(f, pid)).join('')
        : '<p class="db-empty">Nothing is waiting on you.</p>'}
    </section>
    <section class="db-sec db-two">
      <div><h3 class="db-h">Building <span class="db-count">${b.length}</span></h3>
        ${b.length ? b.map((f) => `<p class="db-row"><a href="#/${encodeURIComponent(pid)}/${
          encodeURIComponent(f.feature_id)}">${esc(f.title)}</a> <span class="dim">${esc(f.phase || f.stage)}
          · started ${esc(rel(f.created_at))}</span></p>`).join('')
          : `<p class="db-empty">Nothing is building. <a href="#/${encodeURIComponent(pid)}?start">Start a feature</a></p>`}</div>
      <div><h3 class="db-h">Finished <span class="db-count">${ov ? ov.finished_total : 0}</span></h3>
        ${ov && ov.finished.length ? ov.finished.map((f) => `<p class="db-row"><a href="#/${
          encodeURIComponent(pid)}/${encodeURIComponent(f.feature_id)}">${esc(f.title)}</a>
          <span class="dim">${f.stage === 'accepted' ? 'accepted' : 'sent back'} · ${esc(rel(f.since))}</span></p>`).join('')
          : '<p class="db-empty">Nothing ruled on yet.</p>'}</div>
    </section>`;
}

/* The Tests tab: can Fabrika verify what it builds, level by level.

   Not a list of three levels, a separate list of helper files under all of
   them, and suggestions at the foot of the page -- laid out that way, a reader
   cannot see a gap and its fix together. Each level is one card, in one order:
   what it is and whether Fabrika checks it, what the survey found (a runner,
   and how tests clean up), and then everything that would change that -- the
   fix to choose, the prompt for your coding agent, the helper files -- inside
   the same card. Anything a reading did not tie to a level (one from before
   `levels` existed) is listed once, at the end. */
const LEVEL_WHAT = {
  unit: 'logic on its own, no database or browser',
  integration: "your app's own interfaces, with the real database",
  user: 'the whole app, the way a person uses it',
};

function helperRow(f) {
  const also = (f.levels || []).slice(1);
  return `<div class="tl-helper">
      <span class="tl-path">${esc(f.path)}${also.length
        ? `<span class="also">also for ${esc(also.join(' and '))} tests</span>` : ''}</span>
      <span class="tl-sum">${esc(f.summary || '')}</span>
      <span class="acts">${scaffoldBlocked(f)
        ? `<button class="btn btn-sm btn-primary" disabled
             title="A different file is already at this path.">Add to repo</button>`
        : `<button class="btn btn-sm btn-primary" data-write-scaffold="${esc(f.path)}">Add to repo</button>`}
        <button class="btn btn-sm" data-show-file="${esc(f.path)}">Show the file</button></span>
      <pre class="hz-pre file-out tl-file" data-file-for="${esc(f.path)}" hidden>${esc(f.contents || '')}</pre>
    </div>`;
}

function recBlock([r, i]) {
  return `<div class="tl-rec"><p class="tl-rec-t">${esc(r.title || '')}</p>${recTodo(r, i)}</div>`;
}

function testsTab(p) {
  const tiers = ((p.testing || {}).tiers) || [];
  const skipped = (state.project || {}).unchecked_levels || {};
  const applied = p.cleanup_applied || {};
  const probe = {};
  (p.testing_probe || []).forEach((r) => { probe[r.tier] = r; });
  const declared = (p.blind_placements || []).length;
  const proved = (p.placement_probe || []).filter((r) => r.usable).length;
  const placed = declared > 0 && proved === declared;

  /* A level's fix can be picked here when it has options, none has been
     applied, and no re-survey proposal is waiting to offer them instead. */
  const waiting = partsWaiting('tests').includes('testing');
  const declined = new Set(p.cleanup_declined || []);
  const rowChoice = (t) => !waiting && (t.cleanup_options || []).length && !applied[t.tier]
    && !declined.has(t.tier);
  // A suggestion that is already a fix's prompt, or a file that is a fix's
  // part, belongs to that fix and is not shown again on its own.
  const inFix = new Set(tiers.flatMap((t) => (t.cleanup_options || [])
    .map((o) => o.agent_prompt).filter(Boolean)));
  const fixFiles = new Set(tiers.flatMap((t) => (t.cleanup_options || [])
    .flatMap((o) => o.files || [])));
  const recs = liveRecommendations().map((r, i) => [r, i])
    .filter(([r]) => todoSection(r) === 'tests' && !inFix.has(r.title));
  const helpers = pendingScaffolding(p).filter((f) => !fixFiles.has(f.path));
  const levels = ['unit', 'integration', 'user'];

  const card = (lv) => {
    const t = tiers.find((x) => x.tier === lv);
    const why = skipped[lv];
    const runner = t && t.verdict !== 'absent' ? (runnerNames(t.runner) || 'yes') : '';
    const runBy = ((t && t.run_by) || []).map((n) => `<b>${esc(n)}</b>`).join(' and ');
    const by = t ? cleanupBy(t) : null;
    const cleanText = applied[lv] ? `Fixed: ${applied[lv]}.`
      : (t && t.cleanup_summary) || (by ? CLEANUP_WORDS[by] : '')
        || (t && t.cleanup == null ? 'Not read yet: re-survey to find out.' : '');
    const cleanOk = !!applied[lv] || ['harness', 'isolated', 'each_test'].includes(by);
    const r = probe[lv] || {};
    const probeNote = r.passed === false || r.repeatable === false || r.leaves_clean === false
      ? `<p class="tl-conseq">${esc(r.note || '')}</p>` : '';
    const choice = t && rowChoice(t) ? cleanupChoice(t, {
      scaffolding: (p.survey || {}).scaffolding || [], environment: p.environment }, { row: true }) : '';
    const recsHere = recs.filter(([rec]) => (rec.levels || []).includes(lv));
    const helpersHere = helpers.filter((f) => (f.levels || [])[0] === lv);
    return `<section class="tl-level ${why ? 'you' : ''}" id="level-${lv}">
      <header class="tl-h"><b>${lv}</b><span class="tl-what">${esc(LEVEL_WHAT[lv])}</span>
        <span class="tl-state ${why ? 'you' : 'ok'}">${why ? 'you check' : 'checked by Fabrika'}</span></header>
      <dl class="tl-facts">
        <dt>Runs tests</dt><dd>${runner
          ? `<span class="xp-ok">${esc(runner)}</span> <span class="dim">· ${runBy
              ? `your tests here run in ${runBy}` : 'no check runs the tests already here'}</span>`
          : '<span class="xp-no">No test runner at this level.</span>'}</dd>
        <dt>Cleans up</dt><dd>${!runner ? '<span class="dim">Nothing to clean up until there are tests.</span>'
          : `<span class="${cleanOk ? 'xp-ok' : 'xp-no'}">${esc(cleanText)}</span>`}</dd>
      </dl>
      ${why ? `<p class="tl-conseq">Fabrika won't check criteria at this level, because ${esc(why)}.
        They come to you as a checklist at review.
        <a href="#" data-help-open="unchecked-levels">Why, and how to change it</a></p>` : probeNote}
      ${declined.has(lv) && why ? `<div class="tl-do tl-decided">You chose not to have Fabrika check
        this level. <button type="button" class="linkish-q" data-reopen-cleanup="${lv}">Show the fixes
        again</button></div>` : ''}
      ${choice || recsHere.length ? `<div class="tl-do">
        <p class="tl-do-h">${why ? 'To have Fabrika check this level' : 'Make this level more reliable'}</p>
        ${recsHere.map(recBlock).join('')}${choice}</div>` : ''}
      ${helpersHere.length ? `<div class="tl-do"><p class="tl-do-h">Make tests here easier to write</p>
        ${helpersHere.map(helperRow).join('')}</div>` : ''}
    </section>`;
  };

  const looseRecs = recs.filter(([rec]) => !(rec.levels || []).length);
  const looseHelpers = helpers.filter((f) => !(f.levels || []).length);
  const off = levels.filter((lv) => skipped[lv]).length;
  return `
    ${sectionHead('tests', 'Tests', '', `${3 - off} of 3 levels checked by Fabrika`,
                  levels.map((lv) => (skipped[lv] ? 'a' : 'g')))}
    <div class="tl-intro">
      <p class="tl-lead">Fabrika tests everything it builds, at three levels. At each one it needs a
        test runner and a way for tests to clean up after themselves. Where either is missing, it
        doesn't test that level, and hands its criteria to you to check at review.</p>
      <p class="tl-more"><a href="#" data-help-open="checking-the-work">How Fabrika checks its work</a>
        · <a href="#" data-help-open="test-levels">What each level means</a></p>
    </div>
    ${partsBlock(p, 'tests')}
    ${tiers.length ? `<div class="tl-levels">${levels.map(card).join('')}</div>`
      : '<p class="empty">Never read for what a test can be written against.</p>'}
    ${looseRecs.length || looseHelpers.length ? `
      <section class="tl-level tl-general">
        <header class="tl-h"><b>for testing in general</b>
          <span class="tl-what">from a reading that didn't say which level each one serves</span></header>
        ${looseRecs.length ? `<div class="tl-do">${looseRecs.map(recBlock).join('')}</div>` : ''}
        ${looseHelpers.length ? `<div class="tl-do"><p class="tl-do-h">Test helpers</p>
          ${looseHelpers.map(helperRow).join('')}</div>` : ''}
      </section>` : ''}
    ${!placed ? `<p class="small" style="margin-top:.7rem">Nothing here can carry a test written
      by an agent that has not seen this repository. <b>Accept checks stays disabled until one of these
      is proved.</b></p>` : ''}
    ${declinedRecommendations(p, 'tests')}`;
}

/* Which proposed files are actually outstanding, measured against the
   repository rather than remembered from having written them.

   `scaffolding_applied` records what this project wrote once. A re-survey
   replaces the survey and that list goes with it -- so files that have been
   written, committed, and are byte-identical to what is being proposed would
   come back as "ready to write", and pressing the button would answer "already
   exists, left alone". Correct, and indistinguishable from a button that does
   not work.

   `scaffolding_state` is the server comparing the bytes, because the console
   never sees the repository. Every place that offers these same files has to
   read it, or it is wrong in exactly this way. Falls back to the old list only
   for a file the server did not answer for.

   `differs` stays outstanding: something is there under that path and it is not
   this, which is a thing to decide about rather than a thing already done. What
   it is not is writable here -- see `scaffoldBlocked`. */
function pendingScaffolding(p) {
  const files = ((p.survey || {}).scaffolding) || [];
  const st = (state.project || {}).scaffolding_state || {};
  const applied = new Set(p.scaffolding_applied || []);
  return files.filter((f) => (f.path in st ? st[f.path] !== 'same' : !applied.has(f.path)));
}

/* On disk under that path, and not what is being proposed. Scaffolding never
   writes over somebody's file, so the button cannot land it and must not offer
   to: replacing a file is a decision, and the screen that can put a diff in
   front of it is the re-survey proposal. */
function scaffoldBlocked(f) {
  return ((state.project || {}).scaffolding_state || {})[f.path] === 'differs';
}
