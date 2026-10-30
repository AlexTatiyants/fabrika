/* Outside work: a look at a branch Fabrika did not build, and the packet it
   hands back. The screen is prototypes/branch-packet.html; the rules for
   the code view are DESIGN.md's, The Code View. */

'use strict';

/* -------------------------------------------------------- outside work */

/* The groups of the code view, in reading order. Novel is open; everything
   after it is folded, and says why it was classed the way it was. Tests are a
   group of their own, outside the ramp. */
const BP_GROUPS = [
  ['novel', 'Novel: read these', 'Real choices a person has to read. Open by default.'],
  ['conventional', 'Conventional', 'The obvious way, following what the repository already does.'],
  ['mechanical', 'Mechanical', 'Forced: there was no choice to make. Each says why.'],
  ['generated', 'Generated', "A tool's output: nobody chose these lines one by one."],
  ['tests', 'Tests', 'What the branch added or changed in its tests. Classed by code, not by a model.'],
];

/* The stations a look runs, in order. Attribution only runs when a check is
   red, so a look with every check green never lights it. The last three are
   the attack: probes written against the branch, a panel reading it with
   their results, and the arbiter routing what they found. */
const LOOK_STATIONS = [
  ['reader', 'Reader'], ['gates', 'Gates'], ['attribution', 'Attribution'], ['rapporteur', 'Rapporteur'],
  ['breaker', 'Breaker'], ['review', 'Review'], ['arbiter', 'Arbiter'],
];

/* A look's line in the shape `stationStrip` reads a feature's: every station,
   in order -- attribution among them, lit only when a check was red -- and a
   stage it understands, so a finished look reads as settled and a station
   with nothing to do as skipped rather than waiting. */
function lookLine(s, d = {}) {
  const has = (name) => (s.phases || []).find((p) => p.name === name);
  const phases = LOOK_STATIONS.map(([name]) => has(name) || { name, status: 'pending', detail: '', error: '' });
  /* Sent back and being fixed, or fixed: the repair's own stations follow the
     arbiter on the same line. A fix recorded before they had names of their
     own ran its repair as `repairers`. */
  const fixing = !!d.fixing;
  if (fixing || d.fix || d.fix_failed || (s.phases || []).some((p) => p.name.startsWith('fix_'))) {
    FIX_STATIONS.forEach(([name, old]) => phases.push(has(name) || (old && has(old) && { ...has(old), name })
      || { name, status: 'pending', detail: '', error: '' }));
  }
  const stage = fixing ? 'building' : { reviewing: 'building', awaiting_ruling: 'awaiting_verdict', ruled: 'accepted',
                                        failed: 'failed' }[s.stage] || s.stage;
  return { phases, state: { ...s, stage }, packet: null };
}

/* Fabrika's fix of a look sent back: repair what goes back, run every check
   that passed and every probe, recheck what no probe shows, and make the
   fix branch. A round that breaks a check that passed is thrown away. */
const FIX_STATIONS = [['fix_repair', 'repairers'], ['fix_fence'], ['fix_recheck'], ['fix_branch']];

function branchHref(projectId, slug) {
  return `#/${encodeURIComponent(projectId)}?branch=${encodeURIComponent(slug)}`;
}

const bpPlural = (n, word, many) => `${n} ${n === 1 ? word : (many || `${word}s`)}`;
const bpSha = (s) => esc((s || '').slice(0, 7));

/* The project's tab: the branches there are, as a list. Pick one, say what it
   was for if you like, and look. Those already looked at are listed first and
   open their packet. */
function branchesSection(p) {
  const b = state.branches && state.branches.project === p.project_id ? state.branches : null;
  const lede = `<p class="sec-lede">Work built outside Fabrika -- in any agent, by anyone -- and handed in as a
    branch. Pick one: it is checked out on its own, run through this project's checks and cut into
    pieces to read. Nothing on the branch, and nothing in your checkout, changes.
    <a href="#" data-help-open="outside-work">How it works</a></p>`;
  if (!b) return `<h2 class="sec-title">Outside work</h2>${lede}<p class="empty">Reading the branches…</p>`;
  const day = (at) => (at ? new Date(at).toLocaleDateString([], { day: 'numeric', month: 'short' }) : '');
  const looked = (b.looked || []).map((x) => `
    <li><a class="bp-br" href="${branchHref(p.project_id, x.slug)}">
      <span class="bp-br-name">${esc(x.branch)}</span>
      <span class="bp-br-meta">round ${x.look} · ${esc(lookWord(x.stage, x.verdict))}</span>
      ${x.headline ? `<span class="bp-br-sub">${esc(x.headline)}</span>`
        : x.error ? `<span class="bp-br-sub bad">${esc(x.error)}</span>` : ''}
    </a></li>`).join('');
  const picked = state.lookPick;
  const candidates = (b.candidates || []).map((c) => {
    const on = picked === c.branch;
    return `
    <li class="${on ? 'on' : ''}">
      <button type="button" class="bp-br" data-look-pick="${esc(c.branch)}" aria-expanded="${on}">
        <span class="bp-br-name">${esc(c.branch)}</span>
        <span class="bp-br-meta">${bpPlural(c.ahead, 'commit')} past ${esc(b.base_ref)} · ${esc(day(c.at))}</span>
        ${c.subject ? `<span class="bp-br-sub">${esc(c.subject)}</span>` : ''}
      </button>
      ${on ? `<div class="bp-br-go">
        <input id="look-intent" class="bp-intent" maxlength="600" autocomplete="off"
          placeholder="What was it for? One sentence, optional." aria-label="What ${esc(c.branch)} was for">
        <button type="button" class="btn btn-primary btn-sm" id="look-start">Look at it</button>
        <button type="button" class="btn btn-sm btn-quiet" data-look-cancel>Cancel</button>
      </div>` : ''}
    </li>`;
  }).join('');
  return `
    <h2 class="sec-title">Outside work</h2>${lede}
    ${looked ? `<h3 class="bp-h">Looked at</h3><ul class="bp-branches">${looked}</ul>` : ''}
    <h3 class="bp-h">Branches with work past ${esc(b.base_ref)}</h3>
    ${candidates ? `<ul class="bp-branches">${candidates}</ul>`
      : `<p class="empty">No branch has commits that ${esc(b.base_ref)} does not.</p>`}`;
}

function lookWord(stage, verdict = '') {
  if (stage === 'ruled' && verdict) return { underwritten: 'underwritten', dropped: 'dropped' }[verdict] || 'sent back';
  return { reviewing: 'looking', awaiting_ruling: 'your call', ruled: 'fixed', failed: 'failed' }[stage] || stage;
}

/* --------------------------------------------------------- the packet */

function branchScreen() {
  const d = state.branchLook;
  const pid = state.projectId;
  if (!d || d.slug !== state.param) return `<div class="wrap"><p class="empty">Loading…</p></div>`;
  const s = d.state;
  const packet = d.packet;
  const look = (d.looks || []).find((l) => l.look === s.look) || {};
  const commits = look.commits || [];
  const authors = [...new Set(commits.map((c) => c.author))];
  const when = commits.length ? new Date(commits[commits.length - 1].at)
    .toLocaleDateString([], { day: 'numeric', month: 'short' }) : '';
  const stat = (packet && packet.stat) || null;
  /* The board a feature's screens are built on: the same header, the same
     station strip and lamps, folding the same way. The look's facts go on the
     paper under it. A plate of its own, and a row of dots for stations, said
     "a line" in words and drew something else. */
  const again = s.stage === 'reviewing' ? '' : `<button type="button" class="btn btn-sm" id="look-again"
    title="Another look at the branch as it is now, kept beside this one. Nothing on the branch changes."
    >Look again</button>`;
  const line = lookLine(s, d);
  const plate = `
    <header class="board">
      <div class="hall">
        ${boardHead(`<h1 class="ftitle" title="${esc(s.branch)}">${esc(s.branch)}</h1><span class="fsub">outside work · no spec</span>`)}
        <div class="proc-row">
          <p class="proc steps" aria-label="Where this look is">
            <a class="st" href="#/${encodeURIComponent(pid)}">${esc((state.project.project || {}).name || pid)}</a><span class="arr" aria-hidden="true">·</span><a class="st" href="#/${encodeURIComponent(pid)}?gates=branches">outside work</a><span class="arr" aria-hidden="true">·</span><span class="st at">look ${s.look}</span>
          </p>
          ${again ? `<div class="board-acts">${again}</div>` : ''}
        </div>
        <div class="line-row bp-line ${stripOpen() ? '' : 'folded'}">
          ${stationStrip(line)}
          ${stripToggle(stripOpen())}
        </div>
      </div>
    </header>
    <div class="wrap">
      <div class="bp-facts bp-facts-line">
        <span><b>${commits.length}</b> ${commits.length === 1 ? 'commit' : 'commits'}${
          authors.length ? ` · ${esc(authors.join(', '))}` : ''}${when ? ` · ${esc(when)}` : ''}</span>
        <span>${esc(s.base_ref)} <b>${bpSha(s.base_sha)}</b> → <b>${bpSha(s.head_sha)}</b></span>
        ${stat ? `<span><b>${stat.files}</b> files · <span class="add">+${stat.added}</span>
          <span class="rem">−${stat.removed}</span></span>` : ''}
        <span>${s.working_branch ? `Fabrika's fixes on <b class="mono">${esc(s.working_branch)}</b>` : 'round 1'}</span>
      </div>
      ${bpRounds(d, pid)}
    </div>`;

  if (s.stage === 'failed' && !d.current) {
    return `${plate}<div class="wrap"><div class="error-box" style="margin-top:1.5rem"><pre>${
      esc(s.error || 'The look stopped.')}</pre></div></div>`;
  }
  /* A round is read whole or not at all. Its packet is written after the
     rapporteur, but the breaker, the review panel and the arbiter are still
     to come, and a packet shown then is a verdict band, tabs and counts for a
     review that has not happened. Until the last station is done, the line
     is the page. A round before this one is still there to open, on the
     strip above. */
  if (!packet || (d.current && s.stage === 'reviewing')) {
    return `${plate}<div class="wrap"><p class="empty" style="margin-top:1.5rem">Fabrika is looking at this
      branch: reading it, running this project's checks on it, cutting its diff into pieces, and then
      attacking it, with the breaker, the review panel and the arbiter. The stations above light as each
      finishes, and the packet opens here when the last one has.</p></div>`;
  }
  const stale = !d.current
    ? `<p class="bp-stale">A newer look is ${s.stage === 'failed' ? 'stopped' : 'under way'}. Below is
        look ${packet.look}, at ${bpSha(packet.head_sha)}.${s.error ? ` ${esc(s.error)}` : ''}</p>` : '';
  const checks = packet.checks || { results: [] };
  const green = checks.results.filter((r) => r.passed).length;
  const lines = packet.lines || {};
  const total = Object.values(lines).reduce((a, n) => a + n, 0);
  const report = (d.system || {}).report || null;
  /* A feature's packet, part for part: the verdict band, the tab strip, one
     tab at a time. The band's word is the one thing this look cannot claim yet
     -- there is no review in it to give a verdict -- so it says that, and the
     rapporteur's headline beside it says what was built. */
  // Findings first, as a feature's review opens on Your calls: what waits on
  // you before what was built.
  const tab = ['findings', 'built', 'code', 'checks'].includes(state.bpTab) ? state.bpTab : 'findings';
  const found = d.findings || null;
  const routes = (found && found.routes) || {};
  const sendBack = ((d.calls || {}).send_back || Object.values(routes).filter((r) => r === 'send_back')).length;
  const verdict = d.verdict;
  const verdictWord = verdict ? ({ underwritten: 'Underwritten', dropped: 'Dropped' }[verdict.verdict] || 'Sent back')
    : found ? (sendBack ? `${bpPlural(sendBack, 'defect')} going back` : 'Nothing going back') : 'Not reviewed';
  const bandCls = verdict ? (verdict.verdict === 'underwritten' ? 'v-band-ship' : 'v-band-stop') : 'v-band-none';
  const href = (t) => `${branchHref(pid, s.slug)}&bp=${t}${state.bpLook ? `&look=${state.bpLook}` : ''}`;
  const red = checks.results.some((r) => !r.passed);
  const tabs = [
    ['findings', 'Findings', found ? `${sendBack} to send back` : 'not attacked', sendBack > 0],
    ['built', 'What was built', bpPlural(stat.files, 'file'), false],
    ['code', 'The code', `${lines.novel || 0} novel of ${total} lines`, false],
    ['checks', 'Checks', checks.results.length ? `${green} of ${checks.results.length}` : 'none', red],
  ].map(([t, label, count, alert]) => `
    <a class="tab" role="tab" href="${href(t)}" aria-selected="${tab === t}">${esc(label)}
      <span class="mono tab-n">${esc(count)}</span>${alert ? `<span class="tab-dot" title="${
        t === 'findings' ? 'a defect goes back to whoever built it' : 'a check is red'}"></span>` : ''}</a>`).join('');
  /* Inside `g2`, as the review is, so the band and the tabs take the window's
     width rather than the column's. The band's ground is no verdict's: green
     ships, light green ships once you rule, red does not, and a look has no
     review to give any of the three. */
  return `${plate}
    <div class="g2">
    <section class="v-band ${bandCls}">
      <div class="wrap v-in">
        <span class="v-word">${verdictWord}</span>
        <p class="verdict-headline">${esc(packet.headline || 'The rapporteur gave no headline.')}</p>
        ${/* The review's reading mode: the rail, the masthead and the board fold
             away and the packet is the window. F and Esc, as there. */''}
        <div class="v-tools">${focusToggle()}</div>
      </div>
    </section>
    <div class="tabstrip"><div class="wrap tabs" role="tablist" aria-label="How to read this packet">${tabs}</div></div>
    <div class="wrap bp-tab">
      ${stale}
      ${tab === 'built' ? bpBuilt(packet, report, commits, href('code'), d) : ''}
      ${tab === 'findings' ? bpFindings(d, packet, href('code')) : ''}
      ${tab === 'code' ? (state.bpFile ? bpWholeFile(state.branchFile, state.bpFile, href('code'))
        : bpCode(packet, total, href('code'))) : ''}
      ${tab === 'checks' ? bpRules(packet, checks) : ''}
    </div>
    </div>`;
}

/* ------------------------------------------------------------ findings */

/* What the attack found, drawn as a feature's Your calls is: one shut row a
   finding, its severity a word in its border's hue, its title, where it goes,
   and what can be done about it on the row itself. Open, it is everyone's own
   words with their names on them -- the arbiter's first -- then the probe
   that showed it, and the files it names, linked into The code. Restatements
   are inside the finding they restate, not beside it. */
const BP_ROUTE_ORDER = ['send_back', 'you', 'cleanup', 'none'];
const BP_ROUTE_WORD = { send_back: 'arbiter: send back', you: 'arbiter: your call', cleanup: 'arbiter: cleanup',
  none: 'arbiter: not a defect' };

function bpSaid(who, text, { lead = false, verbatim = true } = {}) {
  if (!text) return '';
  return `<div class="wl-q${lead ? ' wl-q-lead' : ''}"><span class="wl-who">${who}</span>
    <p class="wl-said${verbatim ? ' wl-verbatim' : ''}">${esc(text)}</p></div>`;
}

function bpFindings(d, packet, codeHref) {
  const found = d.findings;
  if (!found) {
    return '<p class="bp-sub">This look was not attacked: it ran before looks had a breaker and a review panel. Look again to attack it.</p>';
  }
  const ledger = found.ledger || {};
  const records = Object.fromEntries((ledger.records || []).map((r) => [r.finding_id, r]));
  const routeOf = (f) => (found.routes || {})[f.id] || 'you';
  const probeOf = Object.fromEntries((found.probes || []).map((p) => [p.finding, p]));
  const all = ledger.findings || [];
  const restatedBy = {};
  all.forEach((f) => {
    const of = (records[f.id] || {}).duplicate_of;
    if (of) (restatedBy[of] = restatedBy[of] || []).push(f);
  });
  const rows = all.filter((f) => !(records[f.id] || {}).duplicate_of)
    .sort((a, b) => BP_ROUTE_ORDER.indexOf(routeOf(a)) - BP_ROUTE_ORDER.indexOf(routeOf(b))
      || (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9));
  const pieceOf = (path) => (packet.pieces || []).find((p) => p.path === path);
  const tidy = d.tidy || {};
  const onTidy = new Set([...((tidy.first || {}).files || []), ...((tidy.second || {}).files || [])]
    .map((x) => x.split('/').pop()));
  const calls = Object.fromEntries((((d.calls || {}).calls) || []).map((c) => [c.finding_id, c]));
  const compared = d.compared || {};
  const ruledLook = !!d.verdict || (d.state || {}).stage === 'ruled' || (d.shown && d.shown !== (d.state || {}).look);
  const back = ((d.calls || {}).send_back || rows.filter((f) => routeOf(f) === 'send_back')).length;
  const left = ((d.calls || {}).unruled || []).length;

  const call = (f) => {
    const r = records[f.id] || {};
    const route = routeOf(f);
    const probe = probeOf[f.id];
    const also = restatedBy[f.id] || [];
    const files = (f.files || []).map((path) => {
      const p = pieceOf(path);
      return p ? `<a class="mono" href="${esc(codeHref)}#piece-${esc(p.id)}">${esc(path)}</a>` : `<span class="mono">${esc(path)}</span>`;
    }).join('');
    return `<details class="wl-call wl-${esc(f.severity)}${route === 'none' ? ' wl-done' : ''}">
      <summary class="wl-head">
        <span class="wl-chev" aria-hidden="true">›</span>
        <span class="wl-sev">${esc(f.severity)}</span>
        <span class="wl-t">${bpSince(compared, f.id)}${esc(f.title)}${also.length ? `<span class="mono wl-x" title="${
          also.length + 1} findings that say this">${also.length + 1}×</span>` : ''}</span>
        <span class="wl-was" title="where the arbiter sent it">${esc(BP_ROUTE_WORD[route])}</span>
        ${bpRuleActs(calls[f.id], ruledLook)}
      </summary>
      <div class="wl-body">
        ${bpSaid('The arbiter', r.disposition_reason, { lead: true })}
        ${bpSaid(f.id, f.detail)}
        ${bpSaid('To fix it', f.recommendation)}
        ${probe ? `<div class="wl-q"><span class="wl-who">The probe</span><p class="wl-said bp-plain">${
          probe.red ? 'Fails today' : 'Passes'}${probe.claim_ids.length ? `, against ${probe.claim_ids.map(esc).join(', ')}` : ''}: <code>${
          esc(probe.path)}</code>.${onTidy.has(probe.path.split('/').pop())
          ? ` It is on <code>${esc((d.tidy_plan || {}).branch || '')}</code>; add it with the fix.` : ''}</p></div>` : ''}
        ${f.evidence ? `<div class="wl-q"><span class="wl-who">It showed</span><details class="bp-probe">
          <summary>${probe ? 'the probe, and what it printed' : 'the evidence'}</summary>
          <pre class="f-ev">${esc(f.evidence)}</pre></details></div>` : ''}
        ${also.length ? `<div class="wl-q"><span class="wl-who">Also found by</span><ul class="wl-sib">${
          also.map((o) => `<li><span class="mono">${esc(o.id)}</span>${esc(o.title)}</li>`).join('')}</ul></div>` : ''}
      </div>
      <div class="wl-meta"><span>raised by ${esc(r.role || 'unrecorded')}</span>${files}${
        (calls[f.id] ? calls[f.id].goes === 'send_back' : route === 'send_back')
          ? `<button type="button" class="wl-act" data-bp-prompt="${esc(f.id)}">copy a prompt for this one</button>` : ''}</div>
      ${bpRuleNote(f, calls[f.id], ruledLook)}
    </details>`;
  };

  const passing = (found.probes || []).filter((p) => !p.red && !p.timed_out);
  return `
    <div class="wl-stand">
      <p class="tab-lead">What the attack on this branch found, where each finding goes, and your call on it.
        <a href="#" data-help-open="outside-work-findings">How it works</a></p>
      <span class="wl-stand-n">${ruledLook ? '' : left ? `<b>${left}</b> ${left === 1 ? 'call' : 'calls'} waiting on you · ` : ''}${
        back ? `<b>${back}</b> to send back` : '<b>Nothing</b> to send back'}</span>
      ${back ? '<button type="button" class="btn btn-sm" id="look-send-back">Copy all as a prompt</button>' : ''}
    </div>
    ${bpFixedSince(compared)}
    ${rows.length ? `<section class="block wl-list">${rows.map(call).join('')}</section>`
      : `<p class="bp-sub">The breaker and the panel found nothing. ${esc((found.breaker || {}).note || '')}</p>`}
    ${passing.length ? `<p class="bp-sub bp-passing">The breaker also wrote ${bpPlural(passing.length, 'probe')} that
      ${passing.length === 1 ? 'passes' : 'pass'}, which proves nothing about the code${
      passing.some((p) => p.promoted) ? ', and keeps the ones that held as tests worth adding' : ''}.</p>` : ''}
    ${bpTidy(d)}
    ${bpVerdict(d)}`;
}

/* What a finding is, against the round before: new, still there after
   Fabrika's fix, a risk you accepted then (and still accept), or one whose
   code changed since you did. Nothing in the first round. */
function bpSince(compared, fid) {
  const status = ((compared || {}).status || {})[fid];
  const word = { new: 'new', still_there: 'still there', accepted_before: 'accepted before',
                 changed_since_accepted: 'code changed' }[status];
  return word ? `<span class="bp-since s-${esc(status)}" title="${esc(status === 'changed_since_accepted'
    ? 'You accepted this last round; the code it is about has changed since, so it is your call again.'
    : `Against round ${compared.previous}`)}">${word}</span>` : '';
}

/* Since the round before: what Fabrika fixed, and what it could not. */
function bpFixedSince(compared) {
  if (!compared || !compared.previous || !((compared.fixed || []).length || (compared.not_fixed || []).length)) return '';
  const li = (xs, word) => xs.map((t) => `<li><span class="mono">${word}</span>${esc(t)}</li>`).join('');
  return `<details class="bp-fixed-since"><summary>Since round ${compared.previous}: Fabrika fixed
    ${compared.fixed.length}${compared.not_fixed.length ? `, and could not fix ${compared.not_fixed.length}` : ''}</summary>
    <ul class="wl-sib">${li(compared.fixed, 'fixed')}${li(compared.not_fixed, 'not fixed')}</ul></details>`;
}

/* Your call on a finding, in the column Your calls rules in: back to whoever
   built the branch, or a risk you accept and sign for. The one you made is
   lit; until the look is ruled as a whole it can be changed. */
function bpRuleActs(call, ruledLook) {
  if (!call) return '<span class="wl-acts"></span>';
  const mine = (call.ruling || {}).ruling;
  if (ruledLook) {
    return `<span class="wl-mine"><span class="pill ${call.goes === 'send_back' ? 'pill-red' : call.goes === 'accept' ? 'pill-amber' : ''}">${
      call.goes === 'send_back' ? 'fix' : call.goes === 'accept' ? 'risk accepted' : 'not ruled'}</span></span>`;
  }
  const btn = (k, label, why) => `<button type="button" class="wl-act${mine === k ? ' on' : ''}" aria-pressed="${mine === k}"
    data-bp-rule="${esc(call.finding_id)}:${k}" title="${esc(why)}">${label}</button>`;
  return `<span class="wl-acts">${btn('send_back', 'fix', 'It goes back to be fixed: by Fabrika, or by whoever built it')}${
    btn('accept', 'accept', 'A risk you accept and sign for, with your reason')}</span>`;
}

function bpRuleNote(f, call, ruledLook) {
  if (!call) return '';
  const mine = call.ruling;
  if (ruledLook || mine) {
    const word = (mine || {}).carried_from ? `You accepted this as a risk in round ${mine.carried_from}`
      : (mine || {}).ruling === 'accept' ? 'You accepted this as a risk' : (mine || {}).ruling === 'send_back'
        ? 'You sent this back to be fixed' : call.goes === 'send_back' ? 'Going back, as the arbiter routed it' : 'Not ruled on';
    return `<div class="wl-ruled"><span class="wl-who">Your call</span>
      <span class="wl-ruled-t">${esc(word)}${mine && mine.note ? `: ${esc(mine.note)}` : '.'}</span></div>`;
  }
  return `<div class="wl-note-row"><input class="wl-note" id="bp-note-${esc(f.id)}" placeholder="${
    call.route === 'send_back' ? 'Why you accept it, if you do -- it goes on the record you sign'
      : 'What you decide, and why'}" /></div>`;
}

/* The look ruled as a whole. To underwrite is to sign for the branch at
   this head: every defect accepted by you as a risk, and every call made.
   Sending it back needs one defect going back, and hands over the prompt. */
function bpVerdict(d) {
  const calls = d.calls || {};
  if (!calls.found) return '';
  const s = d.state || {};
  const round = d.shown || s.look;
  const latest = round === s.look;
  const head = String((d.packet || {}).head_sha || s.head_sha || '').slice(0, 7);
  const v = d.verdict;
  const list = (xs) => `<ul class="wl-sib">${xs.map((x) => `<li><span class="mono">${esc(x.severity)}</span>${
    esc(x.title)}${x.note ? ` <span class="dim">— ${esc(x.note)}</span>` : ''}</li>`).join('')}</ul>`;
  if (v && v.verdict === 'underwritten') {
    return `<section class="block bp-verdict ok"><h3 class="bp-verdict-h">You underwrote this work at round ${round},
        commit <code>${esc(head)}</code>.</h3>
      ${prLine(d, 'id="look-pr"')}
      ${v.accepted.length ? `<p class="bp-sub">The risks you accepted:</p>${list(v.accepted)}` : '<p class="bp-sub">The attack left nothing to accept.</p>'}
      ${v.note ? `<p class="bp-sub">${esc(v.note)}</p>` : ''}</section>`;
  }
  if (v && v.verdict === 'dropped') {
    return `<section class="block bp-verdict back"><h3 class="bp-verdict-h">You dropped this work at round ${round}.</h3>
      ${v.note ? `<p class="bp-sub">${esc(v.note)}</p>` : ''}</section>`;
  }
  // An earlier round: what came of it, and the way to the latest.
  if (!latest) {
    const fix = d.fix;
    return `<section class="block bp-verdict"><h3 class="bp-verdict-h">Round ${round} of ${s.look}</h3>
      <p class="bp-sub">${fix ? `${calls.send_back.length ? '' : ''}Fabrika fixed ${fix.fixed.length} of ${fix.targets.length}
        on <code>${esc(fix.branch)}</code>, and the work moved on to round ${round + 1}.`
        : v && v.verdict === 'sent_back' ? 'Sent back.' : 'The work has moved on since.'}
        <a href="${branchHref(state.projectId, s.slug)}&bp=findings">Round ${s.look} →</a></p></section>`;
  }
  if (s.stage === 'reviewing') return '';
  // Fixed, and the next round not opened yet: an earlier version left work
  // here. The way on is to look at what Fabrika made.
  if (s.stage === 'ruled' && d.fix) {
    return `<section class="block bp-verdict"><h3 class="bp-verdict-h">Fabrika fixed ${d.fix.fixed.length} of
        ${d.fix.targets.length} on <code>${esc(s.working_branch || d.fix.branch)}</code></h3>
      <p class="bp-sub">Round ${s.look + 1} looks at what it made: the checks, the attack, and each finding against this round.</p>
      <div class="bp-verdict-acts"><button type="button" class="btn btn-sm btn-primary" id="look-again">Look at it</button></div>
    </section>`;
  }
  const back = (calls.send_back || []).length;
  const open = (calls.unruled || []).length;
  const can = !back && !open;
  const sentBack = v && v.verdict === 'sent_back';  // an earlier version's ending, read as a round
  return `${back || d.fixing || d.fix_failed || sentBack ? `<section class="block bp-verdict">
      <h3 class="bp-verdict-h">Send back ${bpPlural(back, 'defect')}</h3>
      <div class="bp-routes">${bpFixRoute(d)}
        <div class="bp-route"><p><b>Or hand it back.</b> A prompt with every defect going back, your notes on them,
          and the tests that show them. Fix them on <code>${esc(s.branch)}</code>, then look again.</p>
          <button type="button" class="btn btn-sm" id="look-send-back">Copy the prompt</button></div></div></section>` : ''}
    <section class="block bp-verdict">
      <h3 class="bp-verdict-h">Or end it here, at round ${round}, commit <code>${esc(head)}</code></h3>
      <p class="bp-sub">${can
        ? `Every finding is accepted${(calls.accepted || []).length ? `: ${bpPlural(calls.accepted.length, 'risk')} you sign for` : ''}. You can underwrite it.`
        : `To underwrite it, ${[back ? `accept the ${bpPlural(back, 'defect')} still going back` : '',
            open ? `make the ${bpPlural(open, 'call')} waiting on you` : ''].filter(Boolean).join(', and ')}.`}</p>
      <input class="wl-note bp-verdict-note" id="bp-verdict-note" placeholder="A note for the record, if you have one" />
      <div class="bp-verdict-acts">
        <button type="button" class="btn btn-sm${can ? ' btn-primary' : ''}" data-bp-verdict="underwritten">Underwrite</button>
        <button type="button" class="btn btn-sm" data-bp-verdict="dropped">Drop it</button>
      </div>
    </section>`;
}

/* The rounds of the work, in a row: each round's head and what came of it,
   and between two rounds, what Fabrika fixed. Each opens on its own. */
function bpRounds(d, pid) {
  const rounds = d.rounds || [];
  const s = d.state || {};
  if (rounds.length < 2 && !(rounds[0] || {}).fix) return '';
  const shown = d.shown || s.look;
  const said = (r) => (r.verdict === 'underwritten' ? 'underwritten' : r.verdict === 'dropped' ? 'dropped'
    : !r.found ? (r.look === s.look && s.stage === 'reviewing' ? 'being looked at' : 'not attacked')
    : r.send_back ? `${r.send_back} going back` : r.unruled ? `${r.unruled} waiting on you` : 'all accepted');
  return `<nav class="bp-rounds" aria-label="Rounds of this work">${rounds.map((r) => `
    <a class="bp-round${r.look === shown ? ' on' : ''}" href="${branchHref(pid, s.slug)}&look=${r.look}">
      <b>Round ${r.look}</b><span class="mono">${r.working ? "Fabrika's branch" : 'your branch'} @${esc(r.head_sha.slice(0, 7))}</span>
      <span>${esc(said(r))}</span></a>${r.fix ? `<span class="bp-round-fix">fixed ${r.fix.fixed} of ${r.fix.of} →</span>` : ''}`).join('')}
  </nav>`;
}

/* The tidy branch, in a line: what Fabrika could put on a branch of its own
   for you to take -- the tests its probes became, and any cleanup -- and,
   once it has, where it is. */
function bpTidy(d) {
  const plan = d.tidy_plan || {};
  const made = d.tidy;
  const name = plan.branch || '';
  const tests = (plan.green || 0) + (plan.red || 0);
  if (!tests && !plan.cleanup) return '';
  if (d.tidying) return `<p class="bp-sub bp-tidy-note">Making <code>${esc(name)}</code>…</p>`;
  if (made) {
    const files = [...((made.first || {}).files || []), ...((made.second || {}).files || [])];
    return `<p class="bp-sub bp-tidy-note">Fabrika put ${bpPlural(files.length, 'file')} on <code>${esc(name)}</code>, a branch
      of its own off this one: ${made.kept_cleanup ? 'the cleanup, and ' : ''}${bpPlural(tests, 'test')} from its probes${
      plan.red ? ` (${plan.red} ${plan.red === 1 ? 'fails' : 'fail'} until the fix)` : ''}. Take what you want into
      <code>${esc((d.state || {}).branch || '')}</code> with your fixes.</p>`;
  }
  const what = [plan.cleanup ? bpPlural(plan.cleanup, 'cleanup') : '', tests ? bpPlural(tests, 'test') : ''].filter(Boolean);
  return `<p class="bp-sub bp-tidy-note">Fabrika can put ${esc(what.join(' and '))} on a branch of its own,
    <code>${esc(name)}</code>, for you to take what you want from.
    <button type="button" class="btn btn-sm" id="look-tidy">Make it</button></p>`;
}

/* What became of the pull request for what was underwritten, in one line:
   opened, being opened, failed with a way to try again, or nowhere to open
   it -- and then where to say where. A feature's and a look's alike. */
function prLine(d, retryAttr) {
  const pr = d.pull_request;
  const failed = d.pull_request_failed;
  const target = d.pr_target || {};
  if (d.pr_opening) return '<p class="bp-pr-line">Opening the pull request…</p>';
  if (pr && !failed) {
    return `<p class="bp-pr-line ok">Pull request: <a href="${esc(pr.url)}" target="_blank" rel="noopener">${esc(pr.url)}</a>
      <span class="dim">· <code>${esc(pr.branch)}</code> into <code>${esc(pr.base)}</code> on ${esc(pr.repo)}</span></p>`;
  }
  if (target.missing) {
    return `<p class="bp-pr-line">No pull request: ${esc(target.missing)}
      <a href="#/${encodeURIComponent(state.projectId)}?gates=survey">Project settings</a></p>`;
  }
  return `<p class="bp-pr-line bad">${failed ? `The pull request was not opened: ${esc(failed.error)}` : 'No pull request yet.'}
    <button type="button" class="btn btn-sm" ${retryAttr}>${failed ? 'Try again' : 'Open it'}</button></p>`;
}

/* Sent back, two ways it can go: Fabrika fixes it on a branch of its own,
   and looks at what it made; or the prompt, for whoever built it. */
function bpFixRoute(d) {
  const s = d.state || {};
  const fix = d.fix;
  const failed = d.fix_failed && (!fix || d.fix_failed.tip !== fix.tip) ? d.fix_failed : null;
  const name = s.working_branch || `fabrika/outside/${s.slug}`;
  if (d.fixing) {
    return `<div class="bp-route"><p><b>Fabrika is fixing it</b> on <code>${esc(name)}</code>: repairing what goes back,
      then every check that passed and every probe. The next round of this work starts when it is done.</p></div>`;
  }
  if (fix) {
    const title = (fid) => (((d.calls || {}).calls || []).find((c) => c.finding_id === fid) || {}).title || fid;
    return `<div class="bp-route made"><p><b>Fabrika fixed ${fix.fixed.length} of ${fix.targets.length}</b> on
      <code>${esc(fix.branch)}</code>, in ${bpPlural(fix.rounds.length, 'round')}${
      fix.rounds.some((r) => r.reverted) ? `, ${fix.rounds.filter((r) => r.reverted).length} thrown away for breaking a check` : ''}.</p>
      ${fix.fixed.length ? `<ul class="wl-sib">${fix.fixed.map((f) => `<li><span class="mono">fixed</span>${esc(title(f))}</li>`).join('')}</ul>` : ''}
      ${fix.not_fixed.length ? `<ul class="wl-sib">${fix.not_fixed.map((f) => `<li><span class="mono">not fixed</span>${esc(title(f))}</li>`).join('')}</ul>` : ''}
      ${fix.next_look ? `<p><a href="${branchHref(state.projectId, s.slug)}&look=${fix.next_look}">Round ${fix.next_look} →</a></p>` : ''}</div>`;
  }
  return `<div class="bp-route"><p><b>Have Fabrika fix it.</b> It repairs what goes back on <code>${esc(name)}</code>, its
    branch for this work, with the probes as the fence, then looks at the result as round ${s.look + 1}. Yours is not touched.</p>
    ${failed ? `<p class="bp-q">The last try stopped: ${esc(failed.error)}</p>` : ''}
    <button type="button" class="btn btn-sm btn-primary" id="look-fix">${failed ? 'Try again' : 'Fix it'}</button></div>`;
}

/* One prompt for whoever built the branch, or their agent: every defect the
   arbiter sent back, with where it is, what showed it, and what would fix it.
   Made here from the record, the same every time. */
function bpSendBackPrompt(d, only = null) {
  const found = d.findings || {};
  const records = Object.fromEntries(((found.ledger || {}).records || []).map((r) => [r.finding_id, r]));
  const probes = Object.fromEntries((found.probes || []).map((p) => [p.finding, p]));
  // What goes back is what your calls send back: the arbiter's route where
  // you made none, and a call of yours sending one back where you did.
  const calls = Object.fromEntries((((d.calls || {}).calls) || []).map((c) => [c.finding_id, c]));
  const goesBack = (f) => (calls[f.id] ? calls[f.id].goes === 'send_back' : (found.routes || {})[f.id] === 'send_back');
  const back = ((found.ledger || {}).findings || []).filter((f) => goesBack(f)
    && !(records[f.id] || {}).duplicate_of && (only === null || f.id === only))
    .sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9));
  // Where a probe is on the tidy branch, the path it has there: the one to add it at.
  const tidy = d.tidy || {};
  const onTidy = [...((tidy.first || {}).files || []), ...((tidy.second || {}).files || [])];
  const tidyName = (d.tidy_plan || {}).branch || '';
  const s = d.state || {};
  const parts = [`# Fix what a review of \`${s.branch}\` found`, '',
    `Fabrika looked at \`${s.branch}\` at ${String(s.head_sha || '').slice(0, 12)} and sends back ${bpPlural(back.length, 'defect')}, `
    + 'the most severe first. Fix each one on the branch. Where a test shows a defect, it fails today and passes '
    + 'once the defect is fixed: add it with the fix, so it keeps the defect from coming back.'];
  back.forEach((f, i) => {
    const p = probes[f.id];
    parts.push('', `## ${i + 1}. ${f.title} (${f.severity})`, '', f.detail);
    if ((f.files || []).length) parts.push('', `Where: ${f.files.map((x) => `\`${x}\``).join(', ')}`);
    if (f.recommendation) parts.push('', `What would fix it: ${f.recommendation}`);
    const note = ((calls[f.id] || {}).ruling || {}).note;
    if (note) parts.push('', `From the person reviewing it: ${note}`);
    if (p) {
      const at = onTidy.find((x) => x.split('/').pop() === p.path.split('/').pop()) || p.path;
      parts.push('', `Add this test at \`${at}\` with your fix. It fails today and passes once this is fixed.${
        onTidy.includes(at) && tidyName ? ` It is also on \`${tidyName}\`, if you would rather take it from there.` : ''}`,
      '', '```', p.contents.trimEnd(), '```');
    } else if (f.evidence) parts.push('', 'What showed it:', '', '```', f.evidence.trimEnd(), '```');
  });
  return parts.join('\n');
}

/* A model's prose, made readable and nothing more: escaped, its `code` set as
   code, and a blank line a new paragraph. No other markup is honoured. */
function bpProse(text) {
  return String(text || '').split(/\n\s*\n/).filter((x) => x.trim())
    .map((para) => `<p>${esc(para.trim()).replace(/`([^`\n]+)`/g, '<code>$1</code>')}</p>`).join('');
}

/* What was built, as the mockup has it: what the work is, what it adds to the
   system, how the new code runs, where it landed, and what its commits claim
   against the tests that show it. Every name links to its piece in The code. */
function bpBuilt(packet, report, commits, codeHref, look = {}) {
  const pieces = packet.pieces || [];
  const byId = Object.fromEntries(pieces.map((p) => [p.id, p]));
  const to = (id, inner) => (id && byId[id] ? `<a href="${codeHref}#piece-${esc(id)}">${inner}</a>` : inner);
  to.href = codeHref;
  const f = packet.facts || {};
  const d = f.data || { tables_added: [], tables_changed: [] };
  const adds = (f.components || []).length + (f.routes || []).length
    + d.tables_added.length + d.tables_changed.length;
  const files = new Set(pieces.filter((p) => p.klass !== 'tests').map((p) => p.path)).size;
  const claims = packet.claims || [];
  /* The spec's document: its sections, headed and iconed as the spec's are,
     and its contents beside them, lit as the reader scrolls. */
  const secs = [
    ['touches', 'What it adds', adds, bpAdds(packet, report, to)],
    ['l-play', 'Key flows', (packet.paths || []).length, bpPaths(packet, codeHref)],
    ['l-layers', 'Where it landed', files, bpLanded(packet, report, codeHref, look)],
    ['mustdo', claims.length ? 'What it claims' : 'What its commits say',
     claims.length || commits.length, bpClaims(packet, commits, codeHref)],
  ].filter(([, , , body]) => body && body.trim());
  return `
    <section class="bp-sec bp-doc" aria-label="What was built">
      ${packet.summary ? `<div class="bp-summary">${bpProse(packet.summary)}</div>` : ''}
      ${packet.intent ? `<p class="bp-sub">Handed in as: <i>${esc(packet.intent)}</i></p>` : ''}
      <div class="doc">
        <div class="doc-body">
          ${secs.map(([id, name, n, body]) => docSection(id, name, n, '', body)).join('')}
        </div>
        <nav class="toc" aria-label="Contents">
          <p class="toc-label">Contents</p>
          ${secs.map(([id, name, n]) => `<a class="toc-item" href="#sec-${id}">
            ${icon(id, 'ic ic-sm')}<span class="toc-name">${esc(name)}</span>
            ${n ? `<span class="toc-count">${n}</span>` : ''}</a>`).join('')}
        </nav>
      </div>
    </section>`;
}

/* What it adds, the way most software is built: what a person sees, the
   endpoints behind it, the data it keeps -- always, since "none" is an answer
   too. Then, only where the branch has one, what is worth stopping on:
   packages, what it reaches outside the repository, secrets, settings. All of
   it found in the code; each name points at the piece it is in. */
function bpAdds(packet, report, to) {
  const f = packet.facts;
  if (!f) return '';
  const code = (x) => to(x.piece, `<code>${esc(x.name)}</code>`);
  const tile = (n, label, items, cls = '') => `
    <div class="bp-add${cls}">
      <div class="bp-add-n">${n}</div>
      <div class="bp-add-l">${esc(label)}</div>
      ${items ? `<ul>${items}</ul>` : ''}
    </div>`;

  const core = bpTouchMap(f, to.href);
  const pk = f.packages || { added: [], removed: [], lockfiles: [] };
  const also = [
    pk.added.length || pk.removed.length ? tile(pk.added.length, pk.added.length === 1 ? 'new package' : 'new packages',
      pk.added.map((n) => `<li><code>${esc(n)}</code></li>`).join('')
      + pk.removed.map((n) => `<li class="bp-q"><code>${esc(n)}</code> removed</li>`).join('')) : '',
    (f.outside || []).length ? tile(f.outside.length, 'outside the repository',
      f.outside.map((x) => `<li>${code(x)}</li>`).join(''), ' bp-add-out') : '',
    (f.secrets || []).length ? tile(f.secrets.length,
      f.secrets.length === 1 ? 'place a secret is read or written' : 'places a secret is read or written',
      f.secrets.map((x) => `<li>${code(x)} <span class="bp-q">${esc(x.how)}</span></li>`).join(''), ' bp-add-out') : '',
    (f.settings || []).length ? tile(f.settings.length, f.settings.length === 1 ? 'new setting' : 'new settings',
      f.settings.map((x) => `<li>${code(x)} <span class="bp-q">in ${esc(x.file)}</span></li>`).join('')) : '',
  ].filter(Boolean).join('');
  const quiet = pk.added.length || pk.removed.length ? ''
    : `<p class="bp-sub bp-adds-note">No new packages${pk.lockfiles.length ? '; a lockfile changed' : ', and the lockfiles are unchanged'}.</p>`;
  return `${core}${bpTouchTabs(f, to)}
    ${also ? `<div class="bp-adds bp-adds-also">${also}</div>` : ''}${quiet}`;
}

/* Under the map, the spec's three tabs: every table and column the branch
   adds or drops, every endpoint, every screen -- each with what its own code
   says it is (a column's type and keys, a docstring, the comment above it). */
function bpTouchTabs(f, to) {
  const d = f.data || { tables_added: [], tables_changed: [], tables_removed: [] };
  const OP = { add: '+', alter: '~', drop: '\u2212' };
  const row = (op, name, x = {}) => {
    const type = [x.type, x.key].filter(Boolean).join(', ');
    const nul = x.nullable === true ? 'NULL' : x.nullable === false ? 'NOT NULL' : '';
    return `<p class="schema-col mono ${op}"><span class="op">${OP[op]}</span>
        <span class="name">${esc(name)}</span><span class="type">${esc(type)}</span>
        <span class="null">${nul}</span></p>${x.note ? `<p class="schema-note">${esc(x.note)}</p>` : ''}`;
  };
  const detail = (t, c) => (t.detail || {})[c] || {};
  const tables = [
    ...d.tables_added.map((t) => [t, 'add', [...t.columns, ...(t.relations || [])].map((c) => ['add', c])]),
    ...d.tables_changed.map((t) => [t, 'alter', [...t.added.map((c) => ['add', c]),
      ...(t.relations || []).map((c) => ['add', c]), ...t.removed.map((c) => ['drop', c])]]),
    ...(d.tables_removed || []).map((name) => [{ name }, 'drop', []]),
  ];
  const dataRows = tables.reduce((n, [, , cols]) => n + 1 + cols.length, 0);
  const dataPanel = tables.map(([t, op, cols]) => `
    <div class="schema">
      <p class="schema-table mono">${t.piece ? to(t.piece, esc(t.name)) : esc(t.name)}</p>
      ${row(op, t.name, { note: t.note })}
      ${cols.map(([cop, c]) => row(cop, c, detail(t, c))).join('')}
    </div>`).join('');
  const cls = (state) => (state === 'added' ? 'new' : 'alter');
  const glyph = (state) => (state === 'added' ? '+' : '~');
  const ifacePanel = (f.routes || []).map((r) => {
    const at = r.name.indexOf(' ');
    const [verb, path] = at > 0 ? [r.name.slice(0, at), r.name.slice(at + 1)] : ['', r.name];
    return `<div class="iface ${cls(r.state)}">
      <p class="mono"><span class="op">${glyph(r.state)}</span><b>${esc(verb)}</b>
        ${r.piece ? to(r.piece, esc(path)) : esc(path)}</p>
      ${r.note ? `<p class="iface-change">${esc(r.note)}</p>` : ''}
    </div>`;
  }).join('');
  const surfacePanel = (f.components || []).map((c) => `
    <div class="iface ${cls(c.state)}">
      <p><span class="op">${glyph(c.state)}</span><b>${c.piece ? to(c.piece, esc(c.name)) : esc(c.name)}</b>${
        c.file ? `<br><span class="where-path">${esc(c.file)}</span>` : ''}</p>
      ${c.note ? `<p class="iface-change">${esc(c.note)}</p>` : ''}
    </div>`).join('');
  return docTabs('bp-touches', [
    ['data', 'Data', dataRows, dataPanel],
    ['iface', 'Interface', (f.routes || []).length, ifacePanel],
    ['surfaces', 'Where you see it', (f.components || []).length, surfacePanel],
  ]);
}

/* What it adds, drawn as the spec's "What it touches": what a person sees,
   the endpoints behind it, and the data those touch, with a line wherever the
   code makes one call the other. Every line was read from the branch's code at
   its head, not from anyone's account of it. */
function bpTouchMap(f, codeHref) {
  const ui = f.components || [];
  const eps = f.routes || [];
  const d = f.data || { tables_added: [], tables_changed: [], tables_removed: [] };
  const links = f.links || { ui_endpoint: [], endpoint_data: [], tables_used: [] };
  const cols = (names, op, type = '') => names.map((name) => ({ name, op, type }));
  const tables = [
    ...d.tables_added.map((t) => ({ id: `tb:${t.name}`, name: t.name, piece: t.piece,
      cols: [...cols(t.columns, 'added'), ...cols(t.relations || [], 'added', 'relation')] })),
    ...d.tables_changed.map((t) => ({ id: `tb:${t.name}`, name: t.name, piece: t.piece,
      cols: [...cols(t.added, 'added'), ...cols(t.relations || [], 'added', 'relation'),
             ...cols(t.removed, 'removed')] })),
  ];
  const used = links.tables_used || [];
  const edges = [
    ...links.ui_endpoint.map(([c, r]) => [`ui:${c}`, `ep:${r}`]),
    ...links.endpoint_data.map(([r, t]) => [`ep:${r}`, `tb:${t}`]),
  ];
  if (!ui.length && !eps.length && !tables.length) {
    return '<p class="bp-sub">No screen, endpoint or stored data changed.</p>';
  }

  // The spec review's "What it touches", box for box: the same columns and
  // sizes, a table as its name over a row per column it adds or drops, and
  // the tables it only uses grouped apart, dashed.
  const SW = 190, EW = 268, DW = 290;
  const SX = 0, EX = 280, DX = 668, W = DX + DW;
  const TOP = 34, ROW = 26, NH = 20, EH = 34, GAP = 12;
  const pos = {};
  const rows = [];
  let y = TOP;
  tables.forEach((t) => {
    rows.push({ kind: 'label', text: t.name, y });
    y += 20;
    const first = y;
    t.cols.forEach((c) => { rows.push({ kind: 'col', t, c, y }); y += ROW; });
    // A line ends level with the table's rows: what it touches is the table.
    pos[t.id] = [DX, first, DW, Math.max(NH, y - first - (ROW - NH))];
    y += 10;
  });
  if (used.length) {
    y += 6;
    rows.push({ kind: 'label', text: 'existing · not changed', y, muted: true });
    y += 20;
    used.forEach((name) => { pos[`tb:${name}`] = [DX, y, DW, NH]; rows.push({ kind: 'ext', name, y }); y += ROW; });
  }

  // Barycentre: put a node level with the average of what it connects to.
  const bary = (id, fallback) => {
    const ys = edges.filter(([a, b]) => a === id || b === id)
      .map(([a, b]) => pos[a === id ? b : a]).filter(Boolean).map((q) => q[1] + q[3] / 2 - EH / 2);
    return ys.length ? ys.reduce((x, n) => x + n, 0) / ys.length : fallback;
  };
  const stack = (items, x, w) => {
    let cursor = TOP;
    items.map((it) => ({ it, at: bary(it.id, TOP) }))
      .sort((p, q) => p.at - q.at)
      .forEach(({ it, at }) => {
        const top = Math.max(cursor, at);
        pos[it.id] = [x, top, w, EH];
        cursor = top + EH + GAP;
      });
  };
  const split = (name) => { const at = name.indexOf(' '); return at > 0 ? [name.slice(0, at), name.slice(at + 1)] : ['', name]; };
  const epNodes = eps.map((r) => ({ id: `ep:${r.name}`, name: r.name, state: r.state, piece: r.piece }));
  const uiNodes = ui.map((c) => ({ id: `ui:${c.name}`, name: c.name, file: c.file, state: c.state, piece: c.piece }));
  stack(epNodes, EX, EW);
  stack(uiNodes, SX, SW);
  const H = Math.max(y, ...Object.values(pos).map((q) => q[1] + q[3])) + 20;

  // SVG text neither wraps nor clips: a name too long for its box is squeezed.
  const fit = (text, room, size = 6.3) =>
    text.length * size > room ? ` textLength="${Math.round(room)}" lengthAdjust="spacingAndGlyphs"` : '';
  const curve = ([a, b]) => {
    const [ax, ay, aw, ah] = pos[a], [bx, by, , bh] = pos[b];
    const x1 = ax + aw, y1 = ay + ah / 2, x2 = bx, y2 = by + bh / 2;
    const dx = Math.max(40, (x2 - x1) * 0.45);
    return `M${x1},${y1.toFixed(1)} C${x1 + dx},${y1.toFixed(1)} ${x2 - dx},${y2.toFixed(1)} ${x2},${y2.toFixed(1)}`;
  };
  const OPS = { added: ['+', ''], changed: ['~', 'alter'], removed: ['−', 'gone'] };
  const op = (state, x, yy) => (OPS[state]
    ? `<text class="op ${OPS[state][1]}" x="${x}" y="${yy}">${OPS[state][0]}</text>` : '');
  const linked = (piece, g) => (piece ? `<a class="bp-map-link" href="${esc(codeHref)}#piece-${esc(piece)}">${g}</a>` : g);
  const box = (n, verb, path, title) => {
    const [x, yy, w, h] = pos[n.id];
    return linked(n.piece, `<g class="n" data-id="${esc(n.id)}"><title>${esc(title)}</title>
      <rect x="${x}" y="${yy}" width="${w}" height="${h}" rx="3"/>
      ${op(n.state, x + 10, yy + h / 2 + 4)}
      <text class="verb" x="${x + 24}" y="${yy + 14}">${esc(verb)}</text>
      <text class="path" x="${x + 24}" y="${yy + 26}"${fit(path, w - 34)}>${esc(path)}</text></g>`);
  };
  const drawn = edges.filter(([a, b]) => pos[a] && pos[b]);
  return `
    <p class="oplegend">
      <span><span class="op new">+</span>added</span>
      <span><span class="op alter">~</span>changed</span>
      <span><span class="op gone">−</span>removed</span>
    </p>
    <figure class="cmap-wrap">
      <svg class="cmap" viewBox="0 0 ${W} ${Math.round(H)}" role="img"
        aria-label="Which screens call which endpoints, and which data those endpoints touch">
        <text class="head" x="${SX}" y="16">SEEN AT · ${ui.length}</text>
        <text class="head" x="${EX}" y="16">ENDPOINTS · ${eps.length}</text>
        <text class="head" x="${DX}" y="16">DATA · ${tables.length}</text>
        ${drawn.map(([a, b]) => `<path class="edge" d="${curve([a, b])}"
          data-a="${esc(a)}" data-b="${esc(b)}"/>`).join('')}
        ${uiNodes.map((n) => box(n, 'SCREEN', n.name, n.file || n.name)).join('')}
        ${epNodes.map((n) => { const [verb, path] = split(n.name); return box(n, verb, path, n.name); }).join('')}
        ${rows.map((r) => (r.kind === 'label'
          ? `<text class="tbl ${r.muted ? 'muted' : ''}" x="${DX}" y="${r.y + 11}">${esc(r.text)}</text>`
          : r.kind === 'ext'
          ? `<g class="n ext" data-id="tb:${esc(r.name)}">
              <rect x="${DX}" y="${r.y}" width="${DW}" height="${NH}" rx="3"/>
              <text class="name" x="${DX + 10}" y="${r.y + 14}"${fit(r.name, DW - 20)}>${esc(r.name)}</text></g>`
          : linked(r.t.piece, `<g class="n" data-id="${esc(r.t.id)}">
              <rect x="${DX}" y="${r.y}" width="${DW}" height="${NH}" rx="3"/>
              ${op(r.c.op, DX + 9, r.y + 14)}
              <text class="name" x="${DX + 22}" y="${r.y + 14}">${esc(r.c.name)}</text>
              ${r.c.type ? `<text class="type" x="${DX + DW - 9}" y="${r.y + 14}" text-anchor="end">${esc(r.c.type)}</text>` : ''}</g>`)))
          .join('')}
      </svg>
      <figcaption>Hover a box to isolate what it connects to. Every line was read from the
        branch's code, not from what the model wrote; a box opens its code.${used.length
          ? ` ${used.length} table${used.length === 1 ? '' : 's'} shown dashed
             ${used.length === 1 ? 'is' : 'are'} used by this branch and not altered.` : ''}
      </figcaption>
    </figure>`;
}

/* Key flows: each one a sequence diagram. Who takes part is read from the
   steps' pieces -- one lifeline a file, ordered screen, client, server, data,
   then what lies outside the branch's code -- and each step is a message to
   the file it runs in, from the one before it: a call to itself loops back, a
   step back from the server to the screen is a reply, dashed. The person is
   the first lifeline, and what they do starts the messages it causes. */
const BP_ROUTE = /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|WEBSOCKET)\b/;
const BP_FRONT = /\.(jsx?|tsx?|mjs|vue|svelte|html|css)$/;
const BP_CLIENT = /(^|\/)(api|client|http|fetch|request|requests)\.(m?js|tsx?)$/;
const BP_DATA = /(^|\/)(models?\.py|models\/|migrations?\/|alembic\/)|\.(prisma|sql)$/;
const BP_REQUEST = /\b(GET|POST|PUT|PATCH|DELETE) \/[^\s,;)]*/;
const BP_LANES = ['screen', 'client', 'server', 'data', 'outside'];

/* A participant's name in lines of at most `room` characters, two at most:
   broken between words where it can be, inside a word where one is longer
   than a line, and cut with an ellipsis past the second. */
function bpFitName(name, room) {
  const words = String(name || '').split(/\s+/).filter(Boolean);
  const out = [];
  let cur = '';
  words.forEach((w) => {
    const next = cur ? `${cur} ${w}` : w;
    if (next.length <= room) { cur = next; return; }
    if (cur) out.push(cur);
    cur = w;
  });
  if (cur) out.push(cur);
  const lines = out.flatMap((l) => (l.length <= room ? [l] : l.match(new RegExp(`.{1,${room}}`, 'g'))));
  if (lines.length > 2) return [lines[0], `${lines[1].slice(0, room - 1)}…`];
  return lines.length ? lines : [''];
}

function bpFlow(packet, codeHref, at) {
  const pa = (packet.paths || [])[at];
  if (!pa) return { svg: '', html: '', who: [] };
  const byId = Object.fromEntries((packet.pieces || []).map((p) => [p.id, p]));
  const d = (packet.facts && packet.facts.data) || {};
  const tablePieces = new Set([...(d.tables_added || []), ...(d.tables_changed || [])].map((t) => t.piece).filter(Boolean));
  const steps = [...pa.steps, ...pa.then_steps];
  const laneOf = (p) => {
    if (!p) return 4;
    if (BP_FRONT.test(p.path)) return BP_CLIENT.test(p.path) ? 1 : 0;
    return tablePieces.has(p.id) || BP_DATA.test(p.path) ? 3 : 2;
  };

  // One lifeline a file; a step outside the branch's code is one of its own.
  const lines = new Map();
  steps.forEach((st, i) => {
    const p = st.piece && byId[st.piece];
    const key = p ? p.path : `out:${st.label}`;
    if (!lines.has(key)) lines.set(key, { key, path: p ? p.path : '', lane: laneOf(p), first: i, label: st.label });
  });
  const stem = (path) => path.split('/').pop().replace(/\.[^.]+$/, '');
  const stems = {};
  [...lines.values()].forEach((l) => { if (l.path) stems[stem(l.path)] = (stems[stem(l.path)] || 0) + 1; });
  const who = [...lines.values()].sort((x, y) => x.lane - y.lane || x.first - y.first).map((l) => ({
    ...l, name: !l.path ? l.label : stems[stem(l.path)] > 1 ? l.path.split('/').slice(-2).join('/').replace(/\.[^.]+$/, '') : stem(l.path),
  }));
  const SP = 124, X0 = 44, NOTE = 12 * 0.52, LINE = 15;
  // A name fits its box: as many characters as 13px monospace holds in it, on
  // up to two lines, and the whole name on hover. A step outside the
  // branch's code is named by what it is ("Delete button onDelete"), which is
  // rarely as short as a file. With any name on two lines, every box is drawn
  // tall, so the lifelines start level.
  const ROOM = Math.floor((SP - 26) / (13 * 0.6));
  who.forEach((w) => { w.lines = bpFitName(w.name, ROOM); });
  const tall = who.some((w) => w.lines.length > 1);
  const TOP = tall ? 78 : 64;
  const BOX = tall ? 54 : 40;
  const x = { you: X0 };
  who.forEach((w, i) => { x[w.key] = X0 + SP * (i + 1); });
  let W = X0 + SP * who.length + 60;
  // Monospace at the sizes branch.css draws: a character is 0.6 of the size.
  const reach = (left, text, size) => { W = Math.max(W, Math.ceil(left + String(text).length * size * 0.6) + 12); };
  const keyOf = (st) => (st.piece && byId[st.piece] ? byId[st.piece].path : `out:${st.label}`);
  const laneAt = (i) => lines.get(keyOf(steps[i])).lane;
  const plain = (t) => String(t || '').replace(/`([^`\n]+)`/g, '$1');
  const wrap = (text, width) => {
    const room = Math.max(24, Math.floor(width / NOTE));
    const out = [''];
    plain(text).split(/\s+/).filter(Boolean).forEach((w) => {
      const cur = out[out.length - 1];
      if (!cur || (cur + ' ' + w).length <= room) out[out.length - 1] = cur ? `${cur} ${w}` : w;
      else out.push(w);
    });
    return out[0] ? out : [];
  };

  let y = TOP + 18;
  const drawn = [];
  const active = {};
  const touch = (k, top, bottom) => {
    const a = active[k] || (active[k] = [top, bottom]);
    a[0] = Math.min(a[0], top); a[1] = Math.max(a[1], bottom);
  };
  const mark = { added: ['+', 'add'], changed: ['~', 'chg'] };
  steps.forEach((st, i) => {
    const fromYou = i === 0 || (i === pa.steps.length && pa.then);
    const from = fromYou ? 'you' : keyOf(steps[i - 1]);
    const to = keyOf(st);
    const self = from === to;
    const back = !fromYou && laneAt(i - 1) >= 2 && laneAt(i - 1) <= 3 && laneAt(i) <= 1;
    const crossing = !fromYou && laneAt(i - 1) <= 1 && laneAt(i) >= 2 && laneAt(i) <= 3;
    let request = '';
    if (crossing) {
      // The route's own handler names the request; failing that, the step's note.
      const route = steps.slice(i).map((s2) => byId[s2.piece]).find((p) => p && BP_ROUTE.test(p.symbol));
      const said = BP_REQUEST.exec(`${steps[i - 1].note || ''} ${steps[i - 1].label} ${st.note || ''}`);
      request = route ? route.symbol : said ? said[0] : '';
    }
    const x1 = x[from], x2 = x[to];
    const left = self ? x1 + 10 : Math.min(x1, x2) + 12;
    const width = self ? SP * 3.4 : Math.max(Math.abs(x2 - x1) - 24, SP * 3.4);
    const mid = self ? left : (x1 + x2) / 2;
    // Text runs right from the arrow's start; near the last lifeline, where
    // there is no room for that, it runs left from the arrow's end instead,
    // since running past the lifelines would only scroll.
    const right = X0 + SP * who.length + 40;
    const short = self || Math.abs(x2 - x1) < SP * 1.7;
    const anchor = !short ? 'middle' : right - left >= SP * 3.4 ? 'start' : 'end';
    const tx = anchor === 'start' ? left : anchor === 'end' ? (self ? x1 - 10 : Math.max(x1, x2) - 12) : mid;
    const head = [];
    if (fromYou && i > 0) head.push(['you', pa.then]);
    if (request) head.push(['req', request]);
    const notes = st.note ? wrap(st.note, width) : [];
    const textH = (head.length + 1 + notes.length) * LINE;
    const ay = y + textH + 4;
    const [g, cls] = mark[st.kind] || ['', 'out'];
    let t = y + 11;
    const text = [];
    head.forEach(([k, v]) => { text.push(`<text class="sq-${k}" x="${tx}" y="${t}" text-anchor="${anchor}">${k === 'you' ? '[' : ''}${esc(plain(v))}${k === 'you' ? ']' : ''}</text>`); t += LINE; });
    if (anchor === 'start') {
      reach(tx, `${i + 1}: ${g ? `${g} ` : ''}${plain(st.label)}`, 13);
      head.forEach(([, v]) => reach(tx, v, 12.5));
      notes.forEach((n) => reach(tx, n, 12 * 0.87));
    }
    const label = `<text class="sq-msg ${cls}" x="${tx}" y="${t}" text-anchor="${anchor}"><tspan class="sq-n">${i + 1}:</tspan> ${
      g ? `<tspan class="m">${g}</tspan> ` : ''}${esc(plain(st.label))}</text>`;
    text.push(st.piece && byId[st.piece] ? `<a href="${esc(codeHref)}#piece-${esc(st.piece)}">${label}</a>` : label);
    t += LINE;
    notes.forEach((n) => { text.push(`<text class="sq-note" x="${tx}" y="${t - 1}" text-anchor="${anchor}">${esc(n)}</text>`); t += LINE; });
    const dir = x2 >= x1 ? 1 : -1;
    const line = self
      ? `<path class="sq-line" d="M${x1 + 5},${ay} H${x1 + 36} V${ay + 16} H${x1 + 7}" marker-end="url(#sq-call)"/>`
      : `<path class="sq-line${back ? ' back' : ''}" d="M${x1 + (fromYou ? 0 : 5 * dir)},${ay} H${x2 - 6 * dir}"
          marker-end="url(#${back ? 'sq-back' : 'sq-call'})"/>${fromYou ? `<circle class="sq-dot" cx="${x1}" cy="${ay}" r="4"/>` : ''}`;
    drawn.push(`<g class="sq-step" data-step="${i + 1}"><title>${esc(plain(st.label))}${st.note ? ` -- ${esc(plain(st.note))}` : ''}${
      st.where ? ` (${esc(st.where)})` : ''}</title>${line}${text.join('')}</g>`);
    if (!fromYou) touch(from, ay - 6, ay + 6);
    touch(to, ay - 6, ay + (self ? 24 : 8));
    y = ay + (self ? 30 : 16);
  });
  // A flow that went to the server and ended there still answers the screen.
  const lastLane = steps.length ? laneAt(steps.length - 1) : 0;
  const caller = [...steps.keys()].reverse().find((i) => laneAt(i) <= 1);
  if (steps.length && lastLane >= 2 && lastLane <= 3 && caller !== undefined) {
    const x1 = x[keyOf(steps[steps.length - 1])], x2 = x[keyOf(steps[caller])];
    const ay = y + LINE + 4;
    drawn.push(`<g class="sq-step"><text class="sq-req" x="${(x1 + x2) / 2}" y="${y + 11}" text-anchor="middle">back to the screen</text>
      <path class="sq-line back" d="M${x1 - 5},${ay} H${x2 + 6}" marker-end="url(#sq-back)"/></g>`);
    touch(keyOf(steps[caller]), ay - 6, ay + 6);
    y = ay + 16;
  }
  const H = y + 16;
  const heads = [{ key: 'you', name: 'you', lane: -1 }, ...who].map((w) => {
    const cx = x[w.key];
    const out = w.lane === 4;
    const lines = w.lines || [w.name];
    const first = tall && lines.length === 1 ? 29 : 22;
    return `<g class="sq-who${w.key === 'you' ? ' you' : ''}${out ? ' out' : ''}"><title>${esc(w.path || (out ? `${w.name}: outside this branch's code` : 'the person using it'))}</title>
      <rect x="${cx - SP / 2 + 8}" y="4" width="${SP - 16}" height="${BOX}" rx="3"/>
      ${lines.map((l, n) => `<text class="sq-name" x="${cx}" y="${first + n * 14}" text-anchor="middle">${esc(l)}</text>`).join('')}
      <text class="sq-lane" x="${cx}" y="${BOX - 4}" text-anchor="middle">${w.key === 'you' ? 'person' : BP_LANES[w.lane]}</text>
      <path class="sq-life" d="M${cx},${BOX + 4} V${H}"/></g>`;
  }).join('');
  const bars = Object.entries(active).map(([k, [top, bottom]]) =>
    `<rect class="sq-act" x="${x[k] - 5}" y="${top}" width="10" height="${bottom - top}"/>`).join('');
  const svg = `<svg class="sq" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(pa.title)}: the steps in the order they run">
    <defs>
      <marker id="sq-call" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path class="sq-head" d="M0,0 L10,5 L0,10 z"/></marker>
      <marker id="sq-back" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path class="sq-head open" d="M0,0 L10,5 L0,10"/></marker>
    </defs>${heads}${bars}${drawn.join('')}</svg>`;
  // Drawn at its own size, and smaller only down to where it still reads:
  // past that it scrolls, rather than turning to fine print.
  return { svg, who: who.map((w) => w.name),
    html: `<div class="bp-seq"><div style="max-width:${W}px;min-width:${Math.round(W * 0.8)}px">${svg}</div></div>` };
}

function bpPaths(packet, codeHref) {
  const paths = packet.paths || [];
  if (!paths.length) return '';
  const at = Math.min(Math.max(Number(state.bpPath) || 0, 0), paths.length - 1);
  const pa = paths[at];
  // One line a flow, numbered, in as many columns as fit: ten flows are four
  // lines, not three rows of cards pushing the diagram down the page.
  const keys = paths.map((x, i) => `
    <li><button type="button" class="bp-fkey${i === at ? ' on' : ''}" data-bp-path="${i}"
      aria-pressed="${i === at}" title="${esc(x.title)}"><span class="bp-fkey-i">${i + 1}</span><span class="bp-fkey-t">${esc(x.title)}</span>
      <span class="bp-fkey-n">${bpPlural(x.steps.length + x.then_steps.length, 'step')}</span></button></li>`).join('');
  const step = (by, glyph, label) => `<button type="button" class="bp-fstep-btn" data-bp-path="${
    (at + by + paths.length) % paths.length}" aria-label="${label}">${glyph}</button>`;
  return `
    <figure class="cmap-wrap bp-map">
      <ol class="bp-flist" aria-label="Key flows">${keys}</ol>
      <div class="bp-pmap">
        <div class="bp-pmap-h">
          <div class="bp-pmap-t"><b>${esc(pa.title)}</b>${paths.length > 1 ? `
            <span class="bp-fnav">${step(-1, '\u2039', 'Previous flow')}<span>Flow ${at + 1} of ${paths.length}</span>${
              step(1, '\u203a', 'Next flow')}</span>` : ''}</div>
          ${pa.said ? `<p>${bpInline(pa.said)}</p>` : ''}</div>
        ${bpFlow(packet, codeHref, at).html}
      </div>
    </figure>`;
}

/* Where it landed: how far into the system the branch reached. The
   repository's own groups when the as-built is read at both ends, its folders
   when it is not -- one line a group, with what it touched of it, and the
   groups it left alone after them. A group opens on its files, each with the
   functions, classes, routes and settings the branch added or changed in it:
   its imports and loose module lines are counted, not named. */
const BP_NOISE = /^(imports|module code)$|, top$/;

function bpUnits(pieces, path, tests = false) {
  const units = new Map();
  let loose = 0;
  pieces.filter((p) => p.path === path && (p.klass === 'tests') === tests).forEach((p) => {
    String(p.symbol).split(' · ').forEach((raw) => {
      if (BP_NOISE.test(raw)) { loose += 1; return; }
      const name = raw.replace(/, fields$/, '');
      const had = units.get(name);
      if (!had || (p.fresh && !had.fresh)) units.set(name, { name, id: p.id, fresh: p.fresh });
    });
  });
  return { units: [...units.values()], loose };
}

function bpLanded(packet, report, codeHref, look = {}) {
  const pieces = packet.pieces || [];
  const tests = pieces.filter((p) => p.klass === 'tests');
  const isTest = (path) => tests.some((t) => t.path === path);
  const counts = (paths) => {
    const mine = pieces.filter((p) => paths.includes(p.path));
    const a = mine.reduce((n, p) => n + p.added.length, 0);
    const r = mine.reduce((n, p) => n + p.removed.length, 0);
    return `<span class="bp-gr-pm"><span class="a">+${a}</span>${r ? ` <span class="r">−${r}</span>` : ''}</span>`;
  };
  const SHOWN = 8;
  const fileRow = (path, test = false) => {
    const { units, loose } = bpUnits(pieces, path, test);
    // Every test of a test file is named: it is the list of what the branch tests.
    const shown = test ? units.length : SHOWN;
    const chips = units.slice(0, shown).map((u) => `
      <a class="bp-sym ${u.fresh ? 'add' : 'chg'}" href="${codeHref}#piece-${esc(u.id)}"
        title="${u.fresh ? 'added by this branch' : 'changed by this branch'}">${esc(u.name)}</a>`).join('');
    const more = [units.length > shown ? `${units.length - shown} more` : '',
      loose ? (units.length ? 'and its imports' : 'imports and module lines only') : ''].filter(Boolean).join(', ');
    const fresh = test ? pieces.filter((p) => p.path === path && p.kind === 'test' && p.fresh).length : 0;
    return `<div class="bp-file"><div class="bp-file-p"><a href="${esc(bpFileHref(codeHref, path))}"
      title="the whole file, as it is at the head">${esc(path)}</a>${
      fresh ? `<span class="bp-file-n">${bpPlural(fresh, 'new test')}</span>` : ''}${counts([path])}</div>
      <div class="bp-syms">${chips}${more ? `<span class="bp-sym-more">${esc(more)}</span>` : ''}</div></div>`;
  };

  // The groups, from the as-built or else from the folders at the head.
  let groups;
  let left;
  const changed = [...new Set(pieces.filter((p) => p.klass !== 'tests').map((p) => p.path))];
  if (report) {
    const changedIn = (g) => g.files.filter((f) => f.status !== 'same' && !isTest(f.path)).map((f) => f.path);
    groups = report.groups.filter((g) => g.touched && changedIn(g).length)
      .map((g) => ({ name: g.name || g.key, files: changedIn(g), total: g.files.length }));
    if (report.loose.length) groups.push({ name: 'In no group', files: report.loose.map((f) => f.path), total: null });
    left = report.groups.filter((g) => !g.touched).map((g) => g.name || g.key);
  } else {
    const folderOf = (path) => path.split('/').slice(0, -1).slice(0, 2).join('/') || '.';
    const sizes = look.folders || {};
    const by = new Map();
    changed.forEach((path) => {
      const f = folderOf(path);
      if (!by.has(f)) by.set(f, []);
      by.get(f).push(path);
    });
    groups = [...by.entries()].map(([name, files]) => ({ name: name === '.' ? 'the top folder' : `${name}/`, files,
      total: sizes[name] || null }));
    const testFolders = new Set(tests.map((t) => folderOf(t.path)));
    // Not a folder that holds one the branch changed, nor one of its tests'.
    const holds = (f) => [...by.keys(), ...testFolders].some((x) => x.startsWith(`${f}/`));
    left = Object.keys(sizes).filter((f) => !by.has(f) && !testFolders.has(f) && !holds(f) && f !== '.'
                                        && !/(^|\/)\./.test(f))
      .sort().map((f) => `${f}/`);
  }
  const preview = (g) => g.files.flatMap((path) => bpUnits(pieces, path).units.map((u) => u.name));
  const rows = groups.map((g) => {
    const names = preview(g);
    return `
    <details class="bp-gr">
      <summary><span class="bp-gr-name">${esc(g.name)}</span>
        <span class="bp-gr-n">${g.total ? `${g.files.length} of ${g.total} files` : bpPlural(g.files.length, 'file')}</span>
        ${counts(g.files)}
        <span class="bp-gr-what">${esc(names.slice(0, 6).join(' · '))}${names.length > 6 ? ` +${names.length - 6}` : ''}</span></summary>
      <div class="bp-gr-files">${g.files.map((f) => fileRow(f)).join('')}</div>
    </details>`;
  }).join('');

  // What the reading said, when it is not groups: stopped, partial or under way.
  const readings = look.readings || {};
  const off = ['base', 'head'].filter((k) => readings[k] && readings[k].state !== 'read');
  const which = off.length === 2 ? 'both ends' : off.length ? `the ${off[0]}` : '';
  const reading = off.some((k) => readings[k].state === 'reading');
  const again = !reading ? `<button type="button" class="btn btn-sm" id="look-read">Read again</button>` : '';
  let lede;
  if (report) {
    const grouped = report.groups.filter((g) => g.touched).length;
    lede = `This repository's own groups, from the as-built read at both ends of the branch:
      ${grouped} of ${bpPlural(report.groups.length, 'group')} touched${report.loose.length
        ? `, and ${bpPlural(report.loose.length, 'file')} landed in none` : ''}. Open one for its files.`;
  } else if (reading) {
    lede = `The as-built of ${which} is being read now; this is by folder until it finishes.`;
  } else if (off.some((k) => readings[k].state === 'stopped')) {
    const k = off.find((x) => readings[x].state === 'stopped');
    lede = `The as-built reading of ${which} stopped, so this is by folder rather than the repository's own
      groups.<span class="bp-read"><span class="bp-q">${esc(readings[k].error)}</span>${again}</span>`;
  } else if (off.some((k) => readings[k].state === 'partial')) {
    const k = off.find((x) => readings[x].state === 'partial');
    lede = `The as-built of ${which} was read with ${bpPlural(readings[k].unread, 'file')} left, so this is by
      folder rather than the repository's own groups.<span class="bp-read">${again}</span>`;
  } else {
    lede = `The as-built was not read for this look, so this is by folder rather than the repository's own
      groups.<span class="bp-read">${again}</span>`;
  }

  const testFiles = [...new Set(tests.map((p) => p.path))];
  const newTests = tests.filter((p) => p.kind === 'test' && p.fresh).length;
  const testLine = testFiles.length ? `
    <details class="bp-tests-line"><summary><b>Tests:</b> ${bpPlural(testFiles.length, 'file')},
      ${bpPlural(newTests, 'new test')}</summary>
      <div class="bp-tests-files">${testFiles.map((f) => fileRow(f, true)).join('')}</div></details>` : '<p class="bp-untouched">No test file changed.</p>';
  return `
    <p class="bp-sub bp-landed-lede">${lede}</p>
    <div class="bp-glist">${rows}</div>
    ${left.length ? `<p class="bp-untouched"><b>Left alone:</b> ${left.slice(0, 12).map(esc).join(' · ')}${
      left.length > 12 ? ` and ${left.length - 12} more` : ''}.</p>` : ''}
    ${testLine}`;
}

/* What the commits claim, each against the tests that show it -- the only
   stated intent a branch with no spec has. */
/* The piece a test a claim names is in, by its name: `test_x`, or a spec's
   title, with a file or suite before it (`a.py::test_x`, `Card > adds`) let
   go. Null for a test the branch did not touch, which has no piece. */
function bpTestPiece(pieces, name) {
  const bare = (x) => String(x).split(/::|\s+[>›]\s+/).pop().trim();
  const want = bare(name);
  return pieces.find((p) => p.klass === 'tests' && p.start
    && String(p.symbol).split(' · ').some((sym) => sym === name || bare(sym) === want)) || null;
}

function bpClaims(packet, commits, codeHref = '') {
  const claims = packet.claims || [];
  const pieces = packet.pieces || [];
  // Each test opens its file, whole, at the test.
  const test = (t) => {
    const p = codeHref && bpTestPiece(pieces, t);
    return p ? `<a href="${esc(bpFileHref(codeHref, p.path, p.start))}" title="${esc(p.path)}:${p.start}"><code>${esc(t)}</code></a>`
      : `<code>${esc(t)}</code>`;
  };
  if (!claims.length) {
    const said = commits.map((c) => `<li><code>${bpSha(c.sha)}</code> ${esc(c.message.split('\n')[0])}</li>`).join('');
    return `<p class="bp-sub">With no spec, the commit messages are the only stated intent this branch has.</p>
      <ul class="bp-looks">${said}</ul>`;
  }
  const mark = { broken: ['bad', '✗', 'a probe written against it fails'],
                 shown: ['ok', '✓', 'shown by a test that ran green'],
                 unproved: ['part', '◐', 'a test names it, and the tests did not run green'],
                 untested: ['none', '○', 'no test exercises it'] };
  const row = (c) => {
    const [cls, glyph, word] = mark[c.state] || mark.untested;
    const ev = [(c.broken_by || []).length ? `<span class="ev-broke">broken by ${c.broken_by.map((b) => `<code>${esc(b)}</code>`).join(', ')}</span>` : '',
      [c.tests.map(test).join(' · '),
       c.missing.length ? `named but not in the branch: ${c.missing.map(esc).join(', ')}` : '',
       !c.tests.length && !c.missing.length ? 'no test exercises it' : ''].filter(Boolean).join(' · ')].filter(Boolean).join('');
    return `<div class="bp-claim"><span class="mk ${cls}" role="img" aria-label="${esc(word)}">${glyph}</span>
      <q>${esc(c.quote)}</q><span class="ev">${ev}</span></div>`;
  };
  /* Grouped by what stands behind each claim, the ones that need you first: a
     claim a probe broke, then one nothing tests, then one whose tests did not
     run green, then what a test shows. Within a group, the commits' order. */
  const groups = [['broken', 'Broken by a probe'], ['untested', 'No test exercises it'],
    ['unproved', 'Named by a test that did not run green'], ['shown', 'Shown by a test']];
  const state = (c) => (mark[c.state] ? c.state : 'untested');
  const body = groups.map(([key, title]) => {
    const these = claims.filter((c) => state(c) === key);
    return these.length ? `<div class="bp-claim bp-claim-h">${title} · ${these.length}</div>${these.map(row).join('')}` : '';
  }).join('');
  return `
    <p class="bp-sub">With no spec, the commit messages are the only stated intent this branch has. Each claim in
      them is matched to the test that exercises it, or marked where none does.</p>
    <div class="bp-claims">${body}</div>`;
}

/* One sentence of a model's prose, inline: escaped, its `code` set as code. */
function bpInline(text) {
  return esc(String(text || '')).replace(/`([^`\n]+)`/g, '<code>$1</code>');
}

function bpRules(packet, checks) {
  const status = checks.status;
  const lede = status === 'none'
    ? 'This project has no checks yet, so nothing here says whether the branch keeps what already worked.'
    : status === 'proposed'
      ? `These are the checks Fabrika proposed for this repository. They have not been approved at
         gate 0, so this says what they found, not that the project agreed to them.`
      : 'The checks this project approved at gate 0, run at the head of the branch in its own environment.';
  const rows = checks.results.map((r) => {
    const word = r.passed ? 'passed' : !r.started ? 'could not run' : 'failed';
    const tone = r.passed ? 'pill-green' : !r.started ? 'pill-amber' : 'pill-red';
    const blame = r.caused_here ? `Green where the branch left ${esc(packet.base_ref)}: the branch broke it.`
      : r.pre_existing ? `Red where the branch left ${esc(packet.base_ref)} too: inherited, not the branch's.`
        : r.at_base === 'could_not_run' ? `Could not be run where the branch left ${esc(packet.base_ref)}, so whose it is is not known.`
          : '';
    const loc = r.located;
    const found = loc ? (loc.report !== 'read'
      ? `Its report was ${esc(loc.report || 'not written')}, so nothing is said by line.`
      : `${loc.added_count} on lines the branch wrote · ${loc.before_count} on lines that were there before`) : '';
    const rewrote = (r.would_rewrite || []).length
      ? `Would rewrite ${bpPlural(r.would_rewrite.length, 'file')}: ${r.would_rewrite.slice(0, 5).map(esc).join(', ')}. Recorded and put back; nothing was committed.`
      : '';
    return `
      <div class="bp-check${status === 'proposed' ? ' proposed' : ''}">
        <div><span class="bp-check-name">${esc(r.name)}</span></div>
        <div>
          <div class="bp-check-t">${[blame, found, rewrote].filter(Boolean).join(' ') || (r.passed ? 'Passed at the head.' : '')}</div>
          <div class="bp-check-c">${esc(r.command)}</div>
          ${!r.passed && r.output_tail ? `<details><summary class="small">output</summary><pre class="small">${esc(r.output_tail)}</pre></details>` : ''}
        </div>
        <div class="bp-check-r"><span class="pill ${tone}">${word}</span>
          ${r.duration_s ? `<div class="bp-check-n">${r.duration_s.toFixed(1)}s</div>` : ''}</div>
      </div>`;
  }).join('');
  return `
    <section class="bp-sec" aria-label="Checks">
      <p class="bp-lead">${lede}</p>
      ${rows ? `<div class="bp-checks">${rows}</div>` : ''}
    </section>`;
}

function bpRows(rows) {
  return rows.map(([op, b, h, text]) => {
    if (op === '~') return `<div class="dl-hunk">⋯ ${esc(text)}</div>`;
    const cls = op === '+' ? ' dl-add' : op === '-' ? ' dl-rem' : '';
    return `<div class="dl${cls}"><span class="dl-no">${b || ''}</span><span class="dl-no">${h || ''}</span><span class="dl-txt">${esc(text) || ' '}</span></div>`;
  }).join('');
}

/* The code tab on one file, whole: `&bpfile=<path>`, and `#L<n>` after it
   for a line. */
function bpFileHref(codeHref, path, line = 0) {
  return `${codeHref}&bpfile=${encodeURIComponent(path)}${line ? `#L${line}` : ''}`;
}

function bpPiece(p, codeHref) {
  const novel = p.klass === 'novel';
  const where = p.start ? `${p.path}:${p.start}` : `${p.old_path || p.path} · gone at the head`;
  const counts = `<span class="a">+${p.added.length}</span> <span class="r">−${p.removed.length}</span>`;
  return `
    <details class="bp-unit bp-unit-${esc(p.klass)}" id="piece-${esc(p.id)}" data-klass="${esc(p.klass)}"${novel ? ' open' : ''}>
      <summary>
        <span class="bp-umark ${novel ? 'mk-novel' : 'mk-boiler'}" role="img" aria-label="${esc(p.klass)}">${novel ? '◆' : '◇'}</span>
        <span class="bp-uhead"><span class="bp-utitle">${esc(p.symbol)}</span><span class="bp-upath">${esc(where)}${
          p.old_path && p.start ? ` · moved from ${esc(p.old_path)}` : ''}</span></span>
        <span class="bp-ulinks">${p.classed_by === 'default' ? '<span class="bp-ulink">unclassed</span>' : ''}${
          p.start && codeHref ? `<a class="bp-ulink" href="${esc(bpFileHref(codeHref, p.path, p.start))}"
            title="${esc(p.path)}, whole, at the head">whole file</a>` : ''}</span>
        <span class="bp-ucount">${counts}</span>
        ${icon('l-chevron-right', 'ic bp-uchev')}
        <span class="bp-uwhy">${esc(p.reason)}</span>
      </summary>
      ${p.rows.length ? `<div class="bp-udiff diff-scroll"><div class="diff-rows">${bpRows(p.rows)}</div></div>`
        : `<div class="bp-udiff"><p class="small" style="padding:.6rem .8rem;margin:0">${
          p.binary ? 'A binary file. There are no lines to show.' : 'No line changed.'}</p></div>`}
    </details>`;
}

/* One file the round changed, whole, as it is at the head: what a piece sits
   in. The lines the branch added are marked as they are in a piece. */
function bpWholeFile(f, path, codeHref) {
  const back = `<a class="bp-file-back" href="${esc(codeHref)}">← All of the code</a>`;
  if (!f || f.path !== path && !f.problem) return `<section class="bp-sec">${back}<p class="empty">Loading ${esc(path)}…</p></section>`;
  const head = `<div class="bp-file-head">${back}<h3 class="bp-file-name">${esc(path)}</h3>`;
  if (f.problem) return `<section class="bp-sec">${head}</div><div class="error-box"><pre>${esc(f.problem)}</pre></div></section>`;
  if (f.gone || f.binary) {
    return `<section class="bp-sec">${head}</div><p class="empty">${f.gone ? 'This branch removes the file: there is nothing of it at the head.'
      : 'A binary file. There are no lines to show.'}</p></section>`;
  }
  const added = new Set(f.added);
  // The router owns the hash, so `:target` never matches: the line a link named is marked here.
  const at = /^L(\d+)$/.exec(state.fragment || '');
  const rows = f.lines.map((text, i) => `<div class="dl${added.has(i + 1) ? ' dl-add' : ''}${
    at && +at[1] === i + 1 ? ' dl-at' : ''}" id="L${i + 1}">
      <span class="dl-no">${i + 1}</span><span class="dl-txt">${esc(text) || ' '}</span></div>`).join('');
  return `
    <section class="bp-sec bp-whole" aria-label="${esc(path)}">
      ${head}<p class="bp-sub">At ${bpSha(f.head_sha)}: ${bpPlural(f.lines.length + (f.cut || 0), 'line')}, ${
        bpPlural(added.size, 'line')} of them added by this branch, marked green.</p></div>
      <div class="bp-whole-rows"><div class="diff-rows">${rows}</div></div>
      ${f.cut ? `<p class="bp-untouched">${bpPlural(f.cut, 'more line')} past here; the pieces in The code hold what the
        branch changed in them.</p>` : ''}
    </section>`;
}

function bpCode(packet, total, codeHref = '') {
  const piece = (p) => bpPiece(p, codeHref);
  const pieces = packet.pieces || [];
  const lines = packet.lines || {};
  const byFile = state.bpOrder === 'file';
  const size = (p) => p.added.length + p.removed.length;
  const legend = BP_GROUPS.filter(([k]) => lines[k]).map(([k, label]) => {
    const n = pieces.filter((p) => p.klass === k).length;
    return `<span><i class="bp-seg-${k === 'tests' ? 'tests' : k}"></i>${esc(label.split(':')[0])} <b>${lines[k]}</b> lines · ${bpPlural(n, 'piece')}</span>`;
  }).join('');
  const bar = BP_GROUPS.filter(([k]) => lines[k]).map(([k, label]) =>
    `<i class="bp-seg bp-seg-${k}" style="flex:${lines[k]}" title="${esc(label.split(':')[0])}: ${lines[k]} lines"></i>`).join('');
  let units;
  if (byFile) {
    const files = [...new Set(pieces.map((p) => p.path))];
    units = files.map((f) => `<h3 class="bp-grp-file">${esc(f)}</h3>${pieces.filter((p) => p.path === f)
      .sort((a, b) => (a.start || 1e9) - (b.start || 1e9)).map(piece).join('')}`).join('');
  } else {
    units = BP_GROUPS.map(([k, label, sub]) => {
      const mine = pieces.filter((p) => p.klass === k);
      if (!mine.length) return '';
      const n = mine.reduce((a, p) => a + size(p), 0);
      return `<h3 class="bp-grp-h" data-klass="${k}">${esc(label)} <span>${bpPlural(mine.length, 'piece')} · ${n} lines</span></h3>
        <p class="bp-grp-sub">${esc(sub)}</p>${mine.map(piece).join('')}`;
    }).join('');
  }
  const refused = (packet.joins_refused || []).length
    ? `<p class="bp-refused">The rapporteur asked to join pieces ${packet.joins_refused.length} time${
      packet.joins_refused.length === 1 ? '' : 's'} where it could not: ${packet.joins_refused
      .map((j) => `${esc(j.ids.join(' + '))} (${esc(j.refused)})`).join('; ')}.</p>` : '';
  return `
    <section class="bp-sec" aria-label="The code">
      <p class="bp-lead">${total} lines changed. <b>${lines.novel || 0} of them are novel, a real choice someone
        has to read</b>, and they're open below. The rest is folded, and each piece says why it was classed the
        way it was. If unsure, a piece was classed novel. Every changed line is in exactly one piece.</p>
      <div class="bp-matbar" role="img" aria-label="${lines.novel || 0} novel lines of ${total}">${bar}</div>
      <div class="bp-matlegend">${legend}</div>
      <div class="bp-codebar">
        <div class="bp-seg-ctl" role="group" aria-label="Order the code">
          <button type="button" class="bp-segbtn" aria-pressed="${!byFile}" data-bp-order="klass">By materiality</button>
          <button type="button" class="bp-segbtn" aria-pressed="${byFile}" data-bp-order="file">By file</button>
        </div>
        <button type="button" class="btn btn-sm btn-quiet" data-bp-fold="open">Open all</button>
        <button type="button" class="btn btn-sm btn-quiet" data-bp-fold="close">Fold all</button>
      </div>
      ${refused}
      <div id="units"${byFile ? ' class="by-file"' : ''}>${units}</div>
    </section>`;
}

/* ---------------------------------------------------------- the presses */

async function startLook() {
  const branch = state.lookPick || '';
  const intent = (document.getElementById('look-intent') || {}).value || '';
  if (!branch) { errorToast('Pick a branch to look at.'); return; }
  const made = await api(`/api/projects/${encodeURIComponent(state.projectId)}/branches`, {
    method: 'POST', body: JSON.stringify({ branch: branch.trim(), intent: intent.trim() }),
  });
  state.lookPick = null;
  location.hash = branchHref(state.projectId, made.slug);
}

async function lookAgain() {
  const s = (state.branchLook || {}).state;
  if (!s) return;
  await api(`/api/projects/${encodeURIComponent(state.projectId)}/branches`, {
    method: 'POST', body: JSON.stringify({ branch: s.branch, intent: s.intent || '' }),
  });
  await refreshProject(true);
}

async function ruleFinding(findingId, ruling, note) {
  const slug = (state.branchLook || {}).slug;
  if (!slug) return;
  await api(`/api/projects/${encodeURIComponent(state.projectId)}/branches/${encodeURIComponent(slug)}/rulings`,
    { method: 'POST', body: JSON.stringify({ finding_id: findingId, ruling, note }) })
    .catch((e) => errorToast(e.message));
  await refreshProject(true);
}

async function ruleLook(verdict, note) {
  const slug = (state.branchLook || {}).slug;
  if (!slug) return;
  await api(`/api/projects/${encodeURIComponent(state.projectId)}/branches/${encodeURIComponent(slug)}/verdict`,
    { method: 'POST', body: JSON.stringify({ verdict, note }) })
    .then(() => toast(verdict === 'underwritten' ? 'Underwritten.' : 'Dropped.'))
    .catch((e) => errorToast(e.message));
  await refreshProject(true);
}

async function lookPost(what, said) {
  const slug = (state.branchLook || {}).slug;
  if (!slug) return;
  await api(`/api/projects/${encodeURIComponent(state.projectId)}/branches/${encodeURIComponent(slug)}/${what}`,
    { method: 'POST' }).then(() => toast(said)).catch((e) => errorToast(e.message));
  await refreshProject(true);
}

async function makeTidy() {
  const slug = (state.branchLook || {}).slug;
  if (!slug) return;
  await api(`/api/projects/${encodeURIComponent(state.projectId)}/branches/${encodeURIComponent(slug)}/tidy`,
    { method: 'POST' });
  await refreshProject(true);
}

async function readAgain() {
  const slug = (state.branchLook || {}).slug;
  if (!slug) return;
  await api(`/api/projects/${encodeURIComponent(state.projectId)}/branches/${encodeURIComponent(slug)}/read`,
    { method: 'POST' });
  await refreshProject(true);
}

function branchClick(target) {
  if (target.id === 'look-start') return startLook().then(() => true);
  if (target.id === 'look-again') return lookAgain().then(() => true);
  if (target.id === 'look-read') return readAgain().then(() => true);
  if (target.id === 'look-tidy') return makeTidy().then(() => true);
  if (target.id === 'look-fix') return lookPost('fix', 'Fabrika is fixing it on a branch of its own.').then(() => true);
  if (target.id === 'look-pr') return lookPost('pull-request', 'Opening the pull request.').then(() => true);
  if (target.dataset.bpRule) {
    const [fid, ruling] = target.dataset.bpRule.split(':');
    const note = ((document.getElementById(`bp-note-${fid}`) || {}).value || '').trim();
    ruleFinding(fid, ruling, note);
    return true;
  }
  if (target.dataset.bpVerdict) {
    const note = ((document.getElementById('bp-verdict-note') || {}).value || '').trim();
    ruleLook(target.dataset.bpVerdict, note);
    return true;
  }
  if (target.dataset.bpPrompt) {
    const text = bpSendBackPrompt(state.branchLook || {}, target.dataset.bpPrompt);
    copyText(text).then(() => toast('Copied: a prompt for this one finding.'), () => toast('Could not copy.'));
    return true;
  }
  if (target.id === 'look-send-back') {
    const text = bpSendBackPrompt(state.branchLook || {});
    copyText(text).then(() => toast('Copied: a prompt for whoever built the branch.'), () => toast('Could not copy.'));
    return true;
  }
  if (target.dataset.lookPick !== undefined) {
    state.lookPick = state.lookPick === target.dataset.lookPick ? null : target.dataset.lookPick;
    render();
    const field = document.getElementById('look-intent');
    if (field) field.focus();
    return true;
  }
  if (target.dataset.lookCancel !== undefined) {
    state.lookPick = null;
    render();
    return true;
  }
  if (target.dataset.bpPath !== undefined) {
    state.bpPath = Number(target.dataset.bpPath);
    render();
    return true;
  }
  if (target.dataset.bpOrder) {
    state.bpOrder = target.dataset.bpOrder;
    render();
    return true;
  }
  if (target.dataset.bpFold) {
    const open = target.dataset.bpFold === 'open';
    main.querySelectorAll('#units details.bp-unit').forEach((el) => { el.open = open; });
    return true;
  }
  return false;
}
