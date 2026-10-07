/* Routes: where an agent's call goes -- the account that serves the model and
   the harness that carries the call -- and the roles laid out by phase. */

'use strict';

/* ------------------------------------------------------------- 0d. routes

   A route is where an agent's call actually goes. Three of them can fail three
   different ways -- not installed, installed and nobody signed in, installed
   and broken -- and each needs its own sentence, because "unavailable" tells a
   human nothing about what to do next.

   The sign-in itself cannot happen here. Every one of these tools authenticates
   through an interactive browser handshake that ends with a paste into a
   terminal; there is no flag for it and no endpoint to post to. So the console
   does the parts it can: says which of the three is wrong, hands over the exact
   line to run, and re-checks on a button. */

const ROUTE_LAMP = {
  ok: 'lit', missing: '', unauthenticated: 'warn', outdated: 'warn',
  error: 'bad', unknown: 'warn',
};

function routeState(name) {
  return ((state.routes || {}).routes || []).find((r) => r.name === name) || null;
}

function routeChip(name) {
  const r = routeState(name || (state.routes || {}).default || 'default');
  if (!r) return '';
  const label = r.label || r.name;
  const bad = r.state !== 'ok' ? ' bad' : '';
  return `<span class="route-chip${bad}" title="${esc(r.detail || '')}">${esc(label)}</span>`;
}

/* What a role's spend column can honestly say. A subscription reports no
   dollars -- booked as $0.00 and nothing else it is indistinguishable from an
   agent that never ran, and a reader takes $0.00 to mean the work was free. */
function meterCell(role) {
  const u = role.usage || {};
  const dollars = u.cost ? '$' + u.cost.toFixed(2) : '';
  const turns = u.turns || u.unbilled_calls || 0;
  // Both, when an agent has run on both kinds of route -- a run that fell back
  // from a subscription onto a metered one has a real dollar figure AND turns
  // that figure does not cover, and showing either alone understates it.
  const said = [dollars, turns ? `${turns} turns` : ''].filter(Boolean).join(' + ');
  if (!said) return '<span class="dim">—</span>';
  return `<span title="${turns
    ? 'Turns are against a subscription, which charges by the month and reports no dollars per call. Only routes metered per token have a price.'
    : 'Metered in dollars by the route that carried it.'}">${said}</span>`;
}

function setupBlock(r) {
  if (r.state === 'ok') return '';
  const steps = (r.setup_steps || []).map((x) => `<li>${esc(x)}</li>`).join('');
  const line = r.state === 'missing' ? (r.install_command || r.setup_command)
    : r.setup_command;
  return `
    <div class="hsetup">
      ${r.setup_note ? `<p class="hnote">${esc(r.setup_note)}</p>` : ''}
      ${line ? `<div class="hcopy">
        <code class="mono">${esc(line)}</code>
        <button class="btn btn-sm btn-quiet" data-copy="${esc(line)}">Copy</button>
      </div>` : ''}
      ${steps ? `<ol class="hsteps">${steps}</ol>` : ''}
      ${r.docs_url ? `<p class="small dim"><a href="${esc(r.docs_url)}"
         target="_blank" rel="noreferrer noopener">Documentation</a></p>` : ''}
    </div>`;
}

/* When a check was made. Shown rather than expired: a sign-in lasts weeks, and
   a lamp that went amber on a timer would teach people to ignore the lamps. */
function checkedAgo(r) {
  if (!r.checked_at) return '';
  const secs = Math.max(0, Date.now() / 1000 - r.checked_at);
  if (secs < 90) return 'checked just now';
  if (secs < 5400) return `checked ${Math.round(secs / 60)} min ago`;
  if (secs < 172800) return `checked ${Math.round(secs / 3600)} h ago`;
  return `checked ${Math.round(secs / 86400)} days ago`;
}

/* What the plan itself says is left. A gauge rather than a number, because the
   thing a person needs from it is "how close to the edge", and because it is
   shared with their own use of the same tool -- this bar moves when they work
   in it, not only when the factory does. */
function windowBars(r) {
  const ws = r.windows || [];
  // Credits without windows is a real state -- a tool can report the balance
  // behind the gauge before it has reported the gauge -- so the bail-out asks
  // about both rather than about the bars alone.
  if (!ws.length && !Object.keys(r.credits || {}).length && !limitReached(r)) return '';
  return `<div class="hwins">${limitLine(r)}${ws.map((w) => {
    const name = esc(String(w.label || w.name).replace(/_/g, ' '));
    /* The window has reset since this was read, so the number is from the
       window before last. Showing it would understate the room left; dropping
       the row would say this plan has one limit when it has two. Neither. */
    if (w.stale) {
      return `<div class="hwin stale">
        <span class="hwin-name">${name}</span>
        <span class="hwin-track"></span>
        <span class="hwin-num">reset since last read</span>
      </div>`;
    }
    const pct = Math.max(0, Math.min(1, Number(w.utilization) || 0));
    const mins = Math.round((Number(w.seconds_to_reset) || 0) / 60);
    const when = mins >= 90 ? `${Math.round(mins / 60)} h` : `${mins} min`;
    const tight = pct >= 0.8 ? ' tight' : pct >= 0.5 ? ' warm' : '';
    return `<div class="hwin${tight}">
      <span class="hwin-name">${name}</span>
      <span class="hwin-track"><span class="hwin-fill" style="width:${(pct * 100).toFixed(1)}%"></span></span>
      <span class="hwin-num">${Math.round(pct * 100)}% · resets in ${esc(when)}</span>
    </div>`;
  }).join('')}${creditLine(r)}${meterAge(r, ws)}</div>`;
}

/* How old the reading is, said once under the bars rather than on every row.
   It matters because the two routes differ: one writes its windows to a session
   log this console can read for free, so its bars follow your own use of the
   plan within seconds; the other only reports them inside a call, so its bars
   are exactly as old as the last thing that ran. A gauge that does not say
   which of those it is invites a six-day-old number to be read as now. */
function meterAge(r, ws) {
  const newest = ws.reduce((n, w) => Math.max(n, Number(w.observed_at) || 0), 0);
  if (!newest) return '';
  const secs = Math.max(0, Date.now() / 1000 - newest);
  const when = secs < 90 ? 'just now'
    : secs < 5400 ? `${Math.round(secs / 60)} min ago`
    : secs < 172800 ? `${Math.round(secs / 3600)} h ago`
    : `${Math.round(secs / 86400)} days ago`;
  /* A note only where there is something left to do. Arriving at this page
     brings every gauge current -- free where the tool writes its windows down,
     and by spending one turn where it only reports them inside a call. So a
     reading that is still stale after all that is one the refresh could not
     get, and Check is the remaining move. When it is current, saying anything
     more would be explaining machinery nobody has to think about. */
  const stale = ws.some((w) => w.stale);
  const how = stale && !r.gauge_self_reads ? ' · Check to refresh' : '';
  return `<p class="hwhen hwhen-meter">plan read ${esc(when)}${esc(how)}</p>`;
}

/* The tool said it refused a call, and named why. Drawn first and drawn as an
   alarm, which `creditLine` deliberately is not: `has_credits: false` has been
   seen on accounts whose calls then succeed, but this is not a flag to
   interpret -- it is the refusal itself. Said plainly, then in the tool's own
   words, then how long ago, because a refusal from yesterday is not a refusal
   now and the next call that succeeds clears it. */
const limitReached = (r) => Boolean(((r || {}).limit || {}).reached);

function limitLine(r) {
  if (!limitReached(r)) return '';
  const words = String(r.limit.reached).replace(/_/g, ' ');
  const secs = Math.max(0, Date.now() / 1000 - (Number(r.limit.observed_at) || 0));
  const when = secs < 90 ? 'just now'
    : secs < 5400 ? `${Math.round(secs / 60)} min ago`
    : secs < 172800 ? `${Math.round(secs / 3600)} h ago`
    : `${Math.round(secs / 86400)} days ago`;
  return `<div class="hwin hlimit">
    <span class="hwin-name">refusing calls</span>
    <span class="hwin-num">${esc(words)} · reported ${esc(when)}</span>
  </div>`;
}

/* The pool behind the windows, where a tool keeps one. Shown and not acted on:
   a run can lose every review agent at once to an empty balance while both
   windows above it still have room, so a gauge alone does not tell you whether the next
   call will be answered. Reported exactly as the tool words it -- this console
   does not know what a credit is, and a balance of null is not zero. */
function creditLine(r) {
  const c = r.credits || {};
  if (!Object.keys(c).length) return '';
  if (c.unlimited) return '<div class="hwin"><span class="hwin-name">credits</span>'
    + '<span class="hwin-num">unlimited</span></div>';
  const bal = c.balance === null || c.balance === undefined ? '' : ` · ${esc(String(c.balance))}`;
  /* Deliberately not drawn as an alarm. The tool reports `has_credits: false`
     on an account whose calls then succeed, so the flag may mean "no balance
     configured" rather than "exhausted" -- and nothing in the factory acts on
     it yet. Colouring it red would make a claim the reading does not support,
     which is the same overreach as writing it into a sentence. It is here to be
     watched, in the tool's own words. */
  return `<div class="hwin">
    <span class="hwin-name">credits</span>
    <span class="hwin-num">${c.has_credits === false ? 'none reported' : 'available'}${bal}</span>
  </div>`;
}

/* Which model answers when a role names none. Known from the probe where the
   tool picks for itself, and from config where it does not: a route without
   `default_model_ok` gets `models[0]`, which is the same choice the probe
   makes. Empty when neither is true, because guessing it is worse than saying
   the card does not know. */
function defaultModel(r) {
  return r.resolved_model || (r.default_model_ok ? '' : (r.models || [])[0] || '');
}

/* `2.1.260 (Claude Code)` on a card already titled Claude Code. The tail is the
   tool naming itself, which the nameplate above it has just done. */
const shortVersion = (v) => String(v || '').replace(/\s*\(.*\)\s*$/, '');

/* What this route can carry, in the product's own words rather than the
   implementation's. `completions` and `file sessions` are the two capabilities
   a route has, and most routes have both -- so as a pair of pills they would
   read the same on nearly every card and only ever say anything in the
   negative. The negative is what is kept. */
function crewLine(r, used) {
  const n = used.length;
  const who = n ? `${n} agent${n === 1 ? '' : 's'} on this route`
                : 'no agents on this route';
  const limit = r.completes && r.schema_completions !== false
    ? (r.authors ? '' : ' · cannot carry an agent that writes files')
    : ' · cannot carry any agent here';
  return `<p class="hcrew">${esc(who)}${esc(limit)}</p>`;
}

/* The list is reference, not status: it answers a question asked while
   configuring a role, and the crew table below already shows what each agent
   runs. The card owes you the one model the list cannot name -- the one that
   answers when a role names none -- and keeps the rest behind a disclosure. */
function modelSlot(r) {
  const list = r.models || [];
  const open = state.openModels[r.name];
  if (!list.length) {
    return '<div class="hmodelslot"><p class="hcrew">no models named for this route</p></div>';
  }
  const known = defaultModel(r);
  const rest = known ? list.length - 1 : list.length;
  const pills = list.map((m) => (m === known
    ? `<span class="pill is-default" title="Runs when a role names no model.">${esc(m)}</span>`
    : `<span class="pill">${esc(m)}</span>`)).join('');
  return `<div class="hmodelslot">
    <p class="hcrew">${known ? `default <b class="mono">${esc(known)}</b>`
                             : `${list.length} model${list.length === 1 ? '' : 's'}`}${
      rest > 0 ? ` · <button class="hmore" type="button" data-models="${esc(r.name)}">${
        open ? 'hide' : (known ? `${rest} more` : 'show')}</button>` : ''}</p>
    ${open ? `<div class="hmodels">${pills}</div>` : ''}
  </div>`;
}

/* How long ago, in the words the rest of these cards use. */
function ago(at) {
  const secs = Math.max(0, Date.now() / 1000 - (Number(at) || 0));
  if (secs < 90) return 'just now';
  if (secs < 5400) return `${Math.round(secs / 60)} min ago`;
  if (secs < 172800) return `${Math.round(secs / 3600)} h ago`;
  return `${Math.round(secs / 86400)} days ago`;
}

/* When the account may be asked again, from the hold a run honours. */
function untilRetry(r) {
  const secs = (Number(r.retry_at) || 0) - Date.now() / 1000;
  if (secs <= 0) return '';
  return secs < 5400 ? `${Math.max(1, Math.round(secs / 60))} min`
                     : `${Math.round(secs / 3600)} h`;
}

/* The account's own line: what it last said, and whether that still stands.
 *
 * Kept after it expires, which is the point of it. A refusal is something that
 * happened at a time, not a property of the account -- and the state it set
 * goes back to "nobody has asked" the moment the hold runs out, while this
 * sentence stays, because at 9am "Codex stopped at 10:14 last night for want
 * of credit" is the most useful thing on the card. */
function refusalLine(r) {
  const last = r.last_refusal || {};
  if (!last.at) return '<span class="hslot-empty">no refusal on record</span>';
  const why = esc(String(last.why || 'refused'));
  if (r.state === 'limited') {
    const wait = untilRetry(r);
    return `<p class="hevent live"><b>Refused ${esc(ago(last.at))}</b> — ${why}.${
      wait ? ` Asking again in ${esc(wait)}.` : ' Ready to ask again.'}</p>`;
  }
  return `<p class="hevent"><b>Refused ${esc(ago(last.at))}</b> — ${why}. ${
    r.state === 'ok' ? 'It has answered since.' : 'The hold has run out.'}</p>`;
}

/* ---- the account: who serves the model and charges for it ----------------
 *
 * Everything on this card is a fact about the plan or the key, not about the
 * program that spends it: the windows, the balance, the refusals. On the
 * harness card "8% of five hours left" would read as a property of a CLI, and a
 * workspace out of credit would put a red lamp on a binary that is working
 * perfectly -- sending a human to the install, the login and the config, none
 * of which is wrong. Six slots, aligned across the row by subgrid. */
function accountCard(r) {
  const limited = r.state === 'limited';
  const sub = !r.billed;
  // Dark, not red, where the harness could not answer: nothing was learned
  // about the account, and unknown is not broken.
  const lamp = r.state === 'ok' ? 'lit'
    : limited || r.state === 'unauthenticated' ? 'warn' : '';
  const said = r.state === 'ok'
      ? (sub ? 'Signed in · <b>plan limits</b>' : 'Key stored · <b>billed per token</b>')
    : limited ? `Signed in · <b>${esc(String((r.last_refusal || {}).why || 'refusing calls'))}</b>`
    : r.state === 'unauthenticated' ? (sub ? 'Nobody is signed in' : 'No key stored')
    : `Not known · ${esc(r.harness_label || r.label || r.name)} has not answered`;
  const ws = r.windows || [];
  const plan = ws.length || Object.keys(r.credits || {}).length || limitReached(r)
    ? windowBars(r)
    : `<div class="hwins"><span class="hslot-empty">${
        r.billed ? 'billed per token · no plan window'
        : r.state === 'ok' ? 'no reading yet · press Check'
        : 'no reading yet'}</span></div>`;
  const isDefault = (state.routes || {}).default === r.name;
  return `
    <div class="acard ${r.state === 'ok' ? 'on' : ''} ${limited ? 'limited' : ''}">
      <div class="hhead">
        <span class="lamp ${lamp}"></span>
        <span class="hname">${esc(r.account_label || r.label || r.name)}</span>
        <span class="hver mono">${esc(r.account_kind === 'subscription'
          ? 'subscription' : 'api key')}</span>
      </div>
      <p class="hauth">${said}</p>
      ${plan}
      <div class="hsay">${refusalLine(r)}</div>
      <p class="hcrew">spent by <b>${esc(r.harness_label || r.label || r.name)}</b>${
        isDefault ? ' · the default route' : ''}</p>
      <div class="hact">
        ${limited ? `<button class="btn btn-sm" data-check-route="${esc(r.name)}">Ask now</button>` : ''}
        ${r.kind === 'api' ? '<button class="btn btn-sm btn-quiet" data-open-provider="1">Change key</button>' : ''}
      </div>
    </div>`;
}

/* ---- the harness: the program that carries the call ---------------------
 *
 * Seven slots, always all seven, in the same order on every card. The grid
 * aligns them with `grid-template-rows: subgrid`, so the tallest card sets each
 * row's height and the rest inherit it -- which is what makes the cards on one
 * board read as one board. A slot with nothing in it says which slot it is
 * rather than collapsing, for the reason this console keeps giving: unknown is
 * not absent.
 *
 * `limited` is drawn green here. The account refused; the program is installed,
 * signed in and working, and it is the account's card that says so. */
function harnessCard(r) {
  const open = state.openRoute === r.name;
  const working = r.state === 'ok' || r.state === 'limited';
  const lamp = working ? 'lit' : (ROUTE_LAMP[r.state] === undefined ? 'warn' : ROUTE_LAMP[r.state]);
  const cls = working ? 'on' : (r.state === 'missing' ? 'off' : '');
  const used = ((state.roles || {}).roles || []).filter(
    (x) => (x.route || (state.routes || {}).default) === r.name && x.enabled !== false);
  const accountLamp = r.state === 'ok' ? 'lit'
    : r.state === 'limited' || r.state === 'unauthenticated' ? 'warn' : '';
  return `
    <div class="hcard ${cls} ${open ? 'open' : ''}">
      <div class="hhead">
        <span class="lamp ${lamp}"></span>
        <span class="hname">${esc(r.harness_label || r.label || r.name)}</span>
        <span class="hver mono">${esc(shortVersion(r.version)
          || (r.installed === false ? 'not found'
              : r.kind === 'api' ? 'built in' : ''))}</span>
      </div>
      <p class="hauth">${
        working ? (r.kind === 'cli' ? 'Installed · working' : 'Ready')
        : r.state === 'missing' ? 'Not installed'
        : r.state === 'unauthenticated' ? 'Installed · <b>not signed in</b>'
        : r.state === 'outdated' ? 'Installed · <b>too old</b>'
        : r.state === 'error' ? 'Installed · <b>not working</b>'
        : 'Installed · not checked'}</p>
      <p class="huses"><span class="lamp ${accountLamp}"></span>uses ${
        esc(r.account_label || r.label || r.name)}${
        r.state === 'limited' ? ` · ${esc(String((r.last_refusal || {}).why || 'refusing calls'))}` : ''}</p>
      <div class="hsay">
        ${r.detail && !working ? `<p class="hnote">${esc(r.detail)}</p>` : ''}
        ${r.checked_at ? `<p class="hwhen">${esc(checkedAgo(r))}</p>` : ''}
      </div>
      ${crewLine(r, used)}
      ${modelSlot(r)}
      ${open ? setupBlock(r) : ''}
      <div class="hact">
        ${r.kind === 'cli' ? `<button class="btn btn-sm" data-check-route="${esc(r.name)}">Check</button>` : ''}
        ${!working && (r.setup_steps || []).length
          ? `<button class="btn btn-sm ${open ? 'btn-quiet' : 'btn-primary'}"
               data-open-route="${esc(r.name)}">${open ? 'Hide' : 'How to connect'}</button>` : ''}
      </div>
    </div>`;
}

/* Where each camp lands when its route will not carry it.
 *
 * Shown beside the harnesses because it is the same question asked about a
 * different day: these carry the calls now, and those carry them when one of
 * these runs out. A fallback is chosen at rest and taken at 2am -- the moment a
 * route reaches its ceiling is exactly the moment nobody is reading -- so the
 * one place it can be looked at is here, in advance.
 */
function lanesSection() {
  const lanes = (state.roles && state.roles.fallbacks) || {};
  const names = Object.keys(lanes);
  if (!names.length) return '';
  const trouble = (state.roles && state.roles.independence) || [];
  const byLane = {};
  ((state.roles || {}).roles || []).forEach((r) => {
    if (r.fallback) (byLane[r.fallback] = byLane[r.fallback] || []).push(r.name);
  });
  return `
    <section class="section">
      <p class="eyebrow">Fallback lanes · where each camp goes when its route says no</p>
      <div class="harness-grid lane-grid">
        ${names.map((n) => {
          const l = lanes[n] || {};
          const who = (byLane[n] || []).sort();
          return `<div class="lane-card">
            <div class="lane-head"><b>${esc(n)}</b>
              <span class="mono dim">${esc(l.route || '')}</span></div>
            <div class="mono lane-model">${esc(l.model || '')}</div>
            ${l.note ? `<p class="lane-note">${esc(l.note)}</p>` : ''}
            <p class="lane-who">${who.length
              ? `${who.length} agent${who.length === 1 ? '' : 's'}: <span class="mono">${
                  who.map(esc).join(' ')}</span>`
              : '<span class="dim">no agent uses this lane</span>'}</p>
          </div>`;
        }).join('')}
      </div>
      ${trouble.length ? `
        <div class="lane-warn">
          <b>${trouble.length} pair${trouble.length === 1 ? '' : 's'} would stop disagreeing.</b>
          <ul>${trouble.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>
          <p><a href="#" data-help-open="fallbacks-and-independence">Why that matters</a></p>
        </div>`
        : `<p class="lane-ok">Every agent that exists to disagree with another lands on a
           different family from it — on this configuration, and on the day both routes run
           out. <a href="#" data-help-open="fallbacks-and-independence">Why that matters</a></p>`}
    </section>`;
}

/* What this page draws: the routes that are switched on.
 *
 * Drawing every route in `factory.yaml` would turn a shop floor into a
 * catalogue -- a card for a tool deliberately disabled months ago, lamp amber,
 * asking to be connected. A route switched off is a decision already taken, so
 * it is named in one line at the foot and nothing more. */
const liveRoutes = () => ((state.routes || {}).routes || []).filter((r) => r.enabled !== false);

function routesSection() {
  const rows = liveRoutes();
  const loading = state.crewLoad || {};
  if (!rows.length) {
    return state.crewLoad && !loading.routes
      ? `<section class="section">${crewPending('Checking which harnesses are installed…')}</section>` : '';
  }
  const refreshing = state.crewLoad && !loading.meters;
  const off = ((state.routes || {}).routes || []).filter((r) => r.enabled === false);
  const stuck = rows.filter(
    (r) => r.kind === 'cli' && r.state !== 'ok' && r.state !== 'limited').length;
  const short = rows.filter((r) => r.state === 'limited').length;
  return `
    <section class="section">
      <p class="eyebrow">Accounts · who serves the model and bills for it${
        short ? ` · ${short} account${short === 1 ? '' : 's'} refusing calls` : ''}</p>
      ${refreshing ? crewPending('Refreshing how much of each plan is left. This can take a few seconds.') : ''}
      <div class="account-grid">${rows.map(accountCard).join('')}</div>
    </section>

    <section class="section">
      <p class="eyebrow">Harnesses · the programs that run an agent${
        stuck ? ` · ${stuck} not ready` : ''}</p>
      <div class="harness-grid">${rows.map(harnessCard).join('')}</div>
      ${off.length ? `<p class="small dim roles-foot">${
        off.map((r) => esc(r.harness_label || r.label || r.name)).join(', ')} ${
        off.length === 1 ? 'is' : 'are'} switched off in
        <span class="mono">factory.yaml</span> and not shown.</p>` : ''}
    </section>`;
}

/* The way out.

   Every container Fabrika starts sits on a network with no route anywhere, and
   the ones allowed the internet -- agents, setup -- reach it through one proxy
   that forwards to public addresses and refuses the rest: this machine, the
   LAN, Docker's own host range. That is the fence around your dev servers, so
   whether it is up, and what it has stopped, is shown where the harnesses are:
   they are what it carries.

   The plain line comes first. What went out, and what was refused and by whom,
   sit under it. A refusal is usually a test or an agent reaching for
   `localhost:8300` -- the thing this exists to stop -- and the row says which
   container tried. */
function egressWhen(iso) {
  const at = Date.parse(iso || '');
  return Number.isFinite(at) ? ago(at / 1000) : '';
}

function egressRow(e, tone) {
  return `<li class="eg-row">
      <span class="eg-when mono">${esc(egressWhen(e.at))}</span>
      <span class="eg-who">${esc(e.what || 'unknown')}${
        // A stack's container is already named by what it was; a model call
        // or a setup step is not, and its name is how to find its logs.
        e.name && !e.name.endsWith(e.what) ? ` <span class="mono dim">${esc(e.name)}</span>` : ''}</span>
      <span class="eg-target mono ${tone}">${esc(e.target || '?')}</span>
      ${e.why ? `<span class="eg-why">${esc(e.why)}</span>` : ''}
    </li>`;
}

function egressSection() {
  const g = state.egress;
  if (!g) {
    return state.crewLoad && !state.crewLoad.egress
      ? `<section class="section">${crewPending('Reading the network proxy…')}</section>` : '';
  }
  const up = g.state === 'running';
  const blocked = g.refused_count || 0;
  const lamp = !up ? (g.state === 'absent' ? '' : 'bad')
    : blocked || g.current === false ? 'warn' : 'lit';
  const day = g.hours === 24 ? 'the last day' : `the last ${g.hours} hours`;
  const headline = !up
    ? (g.state === 'absent' ? 'Not running yet. It starts with the first build.'
      : g.state === 'stopped' ? 'Stopped. Agents and builds cannot reach the internet until it starts again.'
      : 'Unknown. Docker did not say whether it is running.')
    : blocked
      ? `Running. It blocked ${blocked} attempt${blocked === 1 ? '' : 's'} to reach this machine or your network in ${day}.`
      : `Running. Nothing was blocked in ${day}.`;
  const nets = (g.networks || []).length;
  const facts = up ? [
    `${g.forwarded || 0} connection${g.forwarded === 1 ? '' : 's'} let through`,
    `serving ${nets} network${nets === 1 ? '' : 's'}`,
    g.started_at ? `started ${egressWhen(g.started_at)}` : '',
  ].filter(Boolean) : [];
  const failed = g.failed || [];
  return `
    <section class="section">
      <p class="eyebrow">Network proxy · how agents and builds reach the internet</p>
      <div class="egress card">
        <p class="eg-intro">Fabrika runs every agent and every check in a Docker container that has
          no network of its own. Anything that needs the internet goes through one proxy
          container, <span class="mono">fabrika-egress</span>, which lets public addresses through
          and blocks this machine and your local network. This is its status.</p>
        <div class="eg-head">
          <span class="lamp ${lamp}"></span>
          <p class="eg-line">${esc(headline)}</p>
          <div class="eg-act">
            ${!up || g.current === false
              ? `<button class="btn btn-sm btn-primary" data-egress-start="1">${
                  up ? 'Restart it' : 'Start it'}</button>` : ''}
            <button class="btn btn-sm btn-quiet" data-egress-test="1"
              title="Starts a throwaway container with no network settings of any kind and checks it can look up an outside name, complete an HTTPS handshake with the real certificate, and reach GitHub over SSH.">Test it</button>
            <button class="btn btn-sm btn-quiet" data-egress-refresh="1">Refresh</button>
          </div>
        </div>
        ${g.current === false && up ? `<p class="eg-note">The proxy's code or settings have changed
          since it started. Restart it to pick up the change; builds in progress keep their
          connection. The next build restarts it anyway.</p>` : ''}
        ${!up && g.detail && g.state !== 'absent' ? `<p class="eg-note">${esc(g.detail)}</p>` : ''}
        ${facts.length ? `<p class="eg-facts mono">${facts.map(esc).join(' · ')}</p>` : ''}
        ${egressTestBlock(g.self_test)}
        ${(g.destinations || []).length ? `<div class="eg-dests">${g.destinations.map((d) => `
          <span class="eg-dest mono">${esc(d.host)} <b>${d.count}</b></span>`).join('')}</div>` : ''}
        ${(g.refused || []).length ? `
          <p class="eg-label">Blocked</p>
          <ul class="eg-list">${g.refused.map((e) => egressRow(e, 'eg-bad')).join('')}</ul>` : ''}
        ${failed.length ? `
          <details class="eg-failed"><summary>${g.failed_count} public address${
            g.failed_count === 1 ? '' : 'es'} did not answer</summary>
            <ul class="eg-list">${failed.map((e) => egressRow(e, '')).join('')}</ul></details>` : ''}
      </div>
    </section>`;
}

/* The last "Test it", said as what a container could do. Shown until the next
   one, so a red line stays on the card rather than flashing past in a toast. */
function egressTestBlock(t) {
  if (!t || !t.at) return '';
  return `
    <p class="eg-label">Tested ${esc(egressWhen(t.at))} · ${t.ok ? 'works' : 'broken'}</p>
    ${t.detail ? `<p class="eg-note eg-bad">${esc(t.detail)}</p>` : ''}
    <ul class="eg-list">${(t.checks || []).map((c) => `
      <li class="eg-row eg-check">
        <span class="eg-when mono ${c.ok ? '' : 'eg-bad'}">${c.ok ? 'ok' : 'failed'}</span>
        <span class="eg-who">${esc(c.what)}</span>
        <span class="eg-why mono">${esc(c.detail)}</span>
      </li>`).join('')}</ul>`;
}

function testEgress() {
  return withBusy('Testing the network proxy with a throwaway container.', async () => {
    state.egress = await api('/api/egress/test', { method: 'POST' });
    render();
    const t = state.egress.self_test || {};
    toast(t.ok ? 'The network proxy works.' : 'The network proxy test failed. See the card.');
  });
}

async function refreshEgress() {
  state.egress = await api('/api/egress').catch(() => state.egress);
  render();
}

function startEgress() {
  return withBusy('Starting the network proxy.', async () => {
    state.egress = await api('/api/egress/start', { method: 'POST' });
    render();
    toast('The network proxy is running.');
  });
}

/* The model picker. Every option names its route, because the option text is
   all a closed <select> shows -- and "opus-5" alone cannot tell you whether you
   picked the one metered against a subscription or the one billed per token.
   They are the same weights and not the same thing. */
function modelOptions(role, draft) {
  const rows = (state.routes || {}).routes || [];
  const fallback = (state.routes || {}).default || 'default';
  const current = draft.route || fallback;
  let seen = false;
  const groups = rows.map((r) => {
    const models = r.models || [];
    // "Whatever this tool is configured to use" is a real answer, and the
    // shortest path to a route that works: connect it, point an agent at it,
    // and never look up an id. What ran is read back afterwards, so it does
    // not become a mystery.
    if (r.default_model_ok) models.unshift('');
    if (!models.length) return '';
    const why = r.schema_completions === false ? ' — cannot answer to a schema'
      : r.state === 'ok' ? ''
      : r.state === 'missing' ? ' — not installed'
      : r.state === 'unauthenticated' ? ' — not signed in'
      : r.state === 'unknown' ? ' — not checked' : ' — not working';
    return `<optgroup label="${esc(r.label || r.name)}${esc(why)}">${models.map((m) => {
      const on = current === r.name && draft.model === m;
      if (on) seen = true;
      const shown = m || 'its own default model';
      const off = r.state === 'missing' || r.schema_completions === false;
      return `<option value="${esc(r.name)}|${esc(m)}" ${on ? 'selected' : ''} ${
        off ? 'disabled' : ''}>${esc(shown)} · via ${esc(r.label || r.name)}</option>`;
    }).join('')}</optgroup>`;
  }).join('');
  // Whatever is configured, even when no route offers it. A picker that
  // silently dropped the current value would show the wrong model as selected
  // and save that on the next press of Save.
  const here = `<optgroup label="Configured"><option value="${esc(current)}|${esc(draft.model)}"
      ${seen ? '' : 'selected'}>${esc(draft.model || 'its own default model')} · via ${esc(
        (routeState(current) || {}).label || current)}</option></optgroup>`;
  return (seen ? '' : here) + groups;
}

function roleDraft(role) {
  if (!state.roleDraft[role.name]) {
    state.roleDraft[role.name] = {
      model: role.model, temperature: role.temperature, max_tokens: role.max_tokens,
      reasoning_effort: role.reasoning_effort, providers: (role.providers || []).join(', '),
      allow_fallbacks: role.allow_fallbacks, samples: role.samples,
      enabled: role.enabled, prompt: role.prompt, route: role.route || '',
    };
  }
  return state.roleDraft[role.name];
}

/* How much is being said to this agent, every single call.

   The prompt is an input the run pays for on each invocation, so its size
   belongs on the board beside the temperature and the spend rather than only
   inside the sheet. Words rather than bytes or tokens: bytes answer a question
   nobody asked, and a token count that is not the tokeniser's own would be a
   precise-looking guess. */
function promptWords(role) {
  if (!role.prompt_exists) return '—';
  const n = String(role.prompt || '').trim().split(/\s+/).filter(Boolean).length;
  return n ? n.toLocaleString() : '—';
}

/* What each agent is for, in one sentence a person can read down the column.
   The table says model, temperature and spend, which answers "how is it
   configured" and never answers "what is it". Written for someone who has
   not read the pipeline: no station names, no invariant numbers. */
const ROLE_BLURB = {
  surveyor: 'Reads a repository and works out how it builds, checks and tests itself.',
  resurvey: 'The same reading, run again later, to see what has changed since.',
  diagnose: 'Works out why a check is red before any feature starts, and proposes fixes that code then tries.',
  reporter: 'Finds the command that makes a test runner report each test by name, when the survey did not.',
  guide_writer: 'Drafts a guide from conventions several features found, for you to edit before it is written.',
  reader: 'Reads one file at a time for the as-built and reports what it contains, in any language.',
  cartographer: 'Names the parts of the as-built that code grouped, and says what the system does.',
  scout: 'Reads the code around a new feature and says what already exists, so it is not built twice.',
  interrogator: 'Asks you the questions nobody can answer from the code alone.',
  spec_writer: 'Turns your intent and your answers into the specification you approve.',
  spec_checker: 'Reads the draft specification as the test writer will, and objects where it cannot be tested or misses what you said.',
  architect: 'Cuts the approved specification into units that can be built side by side.',
  plan_checker: 'Takes each builder\'s seat before any starts, and objects wherever one of them would have to guess.',
  oracle: 'Writes the acceptance tests from the specification, before the code exists and without seeing it.',
  worker: 'Builds one unit of the specification, and only that unit.',
  integrator: 'Joins the units together and tests the seams between them, at the API and below.',
  breaker: 'Attacks the finished code to find inputs it does not survive, security included, and proves each one with a test that fails.',
  reviewer: 'Reads the whole change and makes the strongest case against shipping it.',
  arbiter: 'Decides what happens to each finding: real or not, fixable or yours to rule on.',
  repairer: 'Fixes one named defect, and nothing else on the way past.',
  simplifier: 'Removes what is correct but written twice, once the fixing has settled.',
  rapporteur: 'Assembles the packet you read, and the verdict at the top of it.',
  harness: 'Not a station: the coding session the building and fixing agents work inside.',
};

/* The run, in the order it happens. An agent this list has not heard of is
   still shown -- under `elsewhere`, rather than silently dropped. */
const ROLE_PHASES = [
  ['project', 'how this repository builds and tests itself',
   ['surveyor', 'resurvey', 'diagnose', 'reporter', 'guide_writer']],
  ['as-built', 'what the code is, how it fits together, and what it does',
   ['reader', 'cartographer']],
  ['spec', 'what you want, and what is already here',
   ['scout', 'interrogator', 'spec_writer', 'spec_checker']],
  ['build', 'the units, then the seams between them',
   ['architect', 'plan_checker', 'worker', 'integrator']],
  ['verify', 'the exam, written from the spec before the code exists',
   ['oracle']],
  ['review', 'what the finished work does not survive, and what to do about it',
   ['breaker', 'reviewer', 'arbiter', 'rapporteur']],
  ['repair', 'in rounds, until the budget says stop',
   ['repairer', 'simplifier']],
];

function roleRow(role) {
  const open = state.openRole === role.name;
  const u = role.usage || {};
  const n = role.name;
  const d = open ? roleDraft(role) : null;

  const head = `
    <button class="role-row ${open ? 'open' : ''} ${role.enabled === false ? 'off' : ''}"
      data-open-role="${esc(n)}" aria-expanded="${open}" aria-haspopup="dialog">
      <span class="rr-name">${esc(n)}</span>
      <span class="rr-model">${routeChip(role.route)}<span class="m mono">${
        esc(role.model || 'route default')}</span>${role.follows_level
          ? `<span class="rr-level mono" title="Its model comes from its level on the Agent configuration screen.">${esc(role.level)}</span>`
          : role.level && role.pinned && role.pinned.length
            ? '<span class="rr-level mono" title="Its own model, whatever its level says.">pinned</span>' : ''}${ROLE_BLURB[n]
          ? `<span class="rr-does">${esc(ROLE_BLURB[n])}</span>` : ''}</span>
      <span class="rr-num rr-words mono">${esc(promptWords(role))}</span>
      <span class="rr-num mono">${esc(role.temperature)}</span>
      <span class="rr-num mono">${esc(role.reasoning_effort || '—')}</span>
      <span class="rr-num mono">${u.calls || 0}</span>
      <span class="rr-num mono">${meterCell(role)}</span>
      <span class="rr-fb mono" title="${role.fallback_model
        ? `if ${esc(role.route || 'its route')} will not carry it: ${esc(role.fallback_model)} on ${esc(role.fallback_route)}`
        : 'no fallback — this agent stops the run if its route says no'}">${
        role.fallback ? esc(role.fallback) : '<span class="dim">—</span>'}</span>
    </button>`;

  // The row is only ever a row. Its controls and the prompt's textarea open
  // as a sheet over the board, drawn once by `roleSheet` at the end of the
  // screen rather than inside a table.
  /* Three states, not two. A prompt file is missing, present, or present under
     another role's name -- and a role that shares one must not be drawn like a
     role with none: "this agent will fail mid-run", of an agent that runs.
     Worth saying rather than hiding, because editing this agent's prompt edits
     the other one's too. */
  // A missing prompt is a run that will fail, so it is said on the row. That
  // two agents share a prompt is a fact about EDITING one, so it is said on
  // the sheet where the editing happens -- on the row it would be a paragraph
  // between two bands, explaining something nobody on this screen is doing.
  return head + (role.prompt_exists ? '' :
    `<p class="rr-warn">No prompt file at ${esc(role.prompt_path)} — this agent will fail mid-run.</p>`);
}

/* A chip that is its own control.

   Reading, it is a label and a value. Pressing it hides the label and shows
   the field, in the same slot at the same width, so the rail cannot reflow and
   the prompt below cannot jump a line. The field is an ordinary `[data-role]
   [data-key]` control, so the draft binding underneath needs nothing of its
   own.

   The width is taken from the chip as it stands at the moment of the press,
   rather than declared here. A column of numbers set in advance would make
   every chip the width of its widest possible value, and a rail of six short
   facts would end up wider than the window. */
function cfgChip(key, label, shown, control, dirty) {
  return `<span class="cfg ${dirty ? 'dirty' : ''}" data-cfg="${esc(key)}">
      <button type="button" class="cfg-chip" data-cfg-open="${esc(key)}"
        title="${esc(label)}: ${esc(shown)}">
        <span class="k">${esc(label)}</span><span class="v">${esc(shown)}</span></button>
      ${control}
    </span>`;
}

/* A flag has no value a field could show better than the chip itself, so the
   chip is the control: one press toggles the draft. */
function cfgFlag(n, key, label, on, off, value) {
  return `<button type="button" class="cfg cfg-flag ${value ? 'on' : ''}"
      data-cfg-flag="${esc(key)}" data-role="${esc(n)}" aria-pressed="${!!value}">
      <span class="cfg-chip">
        <span class="k">${esc(label)}</span>
        <span class="v" data-on="${esc(on)}" data-off="${esc(off)}">${esc(value ? on : off)}</span>
      </span>
    </button>`;
}

/* The headings of a prompt, which are the only navigation a six-hundred-line
   document needs. */
function promptOutline(text) {
  const out = [];
  let fenced = false;
  String(text == null ? '' : text).split('\n').forEach((line, i) => {
    // A `#` inside a fenced block is a shell comment, not a section.
    if (/^\s*(```|~~~)/.test(line)) { fenced = !fenced; return; }
    if (fenced) return;
    const m = /^(#{1,4})\s+(.*\S)\s*$/.exec(line);
    if (m) out.push({ line: i, level: m[1].length, text: m[2] });
  });
  return out;
}

function roleSheetHtml(role) {
  const n = role.name;
  const d = roleDraft(role);
  const u = role.usage || {};
  const saved = {
    temperature: role.temperature, max_tokens: role.max_tokens,
    reasoning_effort: role.reasoning_effort, providers: (role.providers || []).join(', '),
    samples: role.samples,
  };
  const moved = (k) => String(d[k]) !== String(saved[k]);

  const rail = [
    cfgChip('model_route', 'model',
      `${d.model || 'route default'} · ${(routeState(d.route) || {}).label || d.route || 'default'}`,
      `<select class="answer-field" data-role="${esc(n)}" data-key="model_route">
         ${modelOptions(role, d)}</select>`,
      d.model !== role.model || (d.route || '') !== (role.route || '')),
    cfgChip('temperature', 'temp', String(d.temperature),
      `<input class="answer-field" type="number" step="0.05" data-role="${esc(n)}"
         data-key="temperature" value="${esc(d.temperature)}">`, moved('temperature')),
    cfgChip('max_tokens', 'max', String(d.max_tokens),
      `<input class="answer-field" type="number" data-role="${esc(n)}"
         data-key="max_tokens" value="${esc(d.max_tokens)}">`, moved('max_tokens')),
    cfgChip('reasoning_effort', 'effort', String(d.reasoning_effort || '—'),
      `<select class="answer-field" data-role="${esc(n)}" data-key="reasoning_effort">
         ${EFFORTS.map((e) => `<option ${d.reasoning_effort === e ? 'selected' : ''}>${e}</option>`).join('')}
       </select>`, moved('reasoning_effort')),
    cfgChip('providers', 'providers', d.providers || 'unpinned',
      `<input class="answer-field" data-role="${esc(n)}" data-key="providers"
         value="${esc(d.providers)}" placeholder="pinned host order, comma separated"
         spellcheck="false">`, moved('providers')),
    cfgFlag(n, 'allow_fallbacks', 'fallbacks', 'allowed', 'pinned', d.allow_fallbacks),
  ];
  if (role.kind === 'review') {
    rail.push(cfgChip('samples', 'samples', String(d.samples),
      `<input class="answer-field" type="number" min="1" data-role="${esc(n)}"
         data-key="samples" value="${esc(d.samples)}">`, moved('samples')));
    rail.push(cfgFlag(n, 'enabled', 'agent', 'on', 'off', d.enabled));
  }

  return `
    <div class="sheet-scrim" data-close-sheet="1"></div>
    <div class="sheet" role="dialog" aria-modal="true" aria-label="${esc(n)} — prompt and configuration">
      <div class="sheet-top">
        <span class="s-name">${esc(n)}</span>
        <span class="s-kind">${esc(role.kind === 'review' ? 'review' : role.kind === 'unused' ? 'unused' : 'pipeline')}</span>
        <span class="s-sp"></span>
        ${role.prompt_exists ? '' : '<span class="s-path sheet-warn">no file on disk — saving creates it</span>'}
        <span class="s-path">${esc(role.prompt_path.split('/').slice(-2).join('/'))}</span>
        ${role.prompt_role && role.prompt_role !== n
          ? `<span class="s-path s-shared">shared with <b>${esc(role.prompt_role)}</b> — editing
             it here changes it for both</span>` : ''}
        ${role.kind === 'review' ? `<button class="btn btn-sm btn-quiet" data-delete-role="${esc(n)}">Remove</button>` : ''}
        <button class="btn btn-sm btn-quiet" data-close-sheet="1">Close</button>
        <button class="btn btn-sm btn-primary" data-save-role="${esc(n)}">Save</button>
      </div>

      <div class="sheet-rail">
        ${rail.join('')}
        <span class="s-spend">${u.calls || 0} calls · $${(u.cost || 0).toFixed(2)} metered</span>
      </div>

      <div class="sheet-body">
        <nav class="sheet-outline" aria-label="Sections of this prompt">
          <p class="o-cap">Sections</p>
          <div class="o-list"></div>
        </nav>
        <div class="sheet-edit" data-role="${esc(n)}"></div>
      </div>

      <div class="sheet-foot">
        <span class="s-count"></span>
        <span class="s-state">saved</span>
        <span class="s-sp"></span>
        <span><kbd>${navigator.platform.indexOf('Mac') === 0 ? '⌘' : 'Ctrl'}S</kbd> save</span>
        <span><kbd>Esc</kbd> close</span>
      </div>
    </div>`;
}

function providerCard() {
  const p = state.provider;
  if (!p) return '';
  const fromEnv = p.key_source === 'environment';
  const open = state.providerOpen || !p.key_present;
  const t = state.providerTest;

  if (!open) {
    return `<p class="small dim roles-foot provider-line">
      <span class="mono">${esc(p.base_url)}</span> · key <span class="mono">${esc(p.key_hint)}</span>
      from ${esc(p.key_source)} ·
      <button class="btn btn-sm btn-quiet" id="toggle-provider">change</button>
      <button class="btn btn-sm btn-quiet" id="test-provider">test</button>
      ${t ? `<span class="${t.ok ? 'test-ok' : 'test-bad'}">${esc(t.detail)}</span>` : ''}
    </p>`;
  }

  return `
    <section class="section">
      <p class="eyebrow">${p.key_present ? 'Provider' : 'Provider · no key set, nothing can run'}</p>
      <div class="card ${p.key_present ? '' : 'needs-key'}">
        <div class="role-grid">
          <label class="field-inline grow"><span>base url</span>
            <input class="answer-field mono" id="provider-base" value="${esc(p.base_url)}"
              spellcheck="false"></label>
        </div>
        <div class="role-grid">
          <label class="field-inline grow"><span>api key</span>
            <input class="answer-field mono" id="provider-key" type="password"
              autocomplete="off" spellcheck="false"
              placeholder="${p.key_present ? esc(p.key_hint) + ' — type a new one to replace it' : 'sk-or-v1-...'}"
              ${fromEnv ? 'disabled' : ''}></label>
          <button class="btn btn-sm btn-primary" id="save-provider" ${fromEnv ? 'disabled' : ''}>Save and test</button>
          ${p.key_present ? '<button class="btn btn-sm" id="test-provider">Test saved key</button>' : ''}
          ${p.key_present ? '<button class="btn btn-sm btn-quiet" id="toggle-provider">Done</button>' : ''}
        </div>
        ${t ? `<p class="small ${t.ok ? 'test-ok' : 'test-bad'}">${esc(t.detail)}</p>` : ''}
        <p class="small dim" style="max-width:70ch;margin:.5rem 0 0">
          ${fromEnv
            ? `An environment variable is supplying the key and it wins, so this field is locked.
               Unset it to store one here instead.`
            : `Stored in <span class="mono">${esc(p.credentials_file.split('/').slice(-1)[0])}</span>,
               mode 0600, separate from <span class="mono">factory.yaml</span> — that file is
               rewritten on every role edit and shown in this console. An environment variable of
               the same name always takes precedence.`}
        </p>
      </div>
    </section>`;
}

/* A part of the crew page that has not arrived yet, said as what it is
   waiting on. Pulses, so a slow answer reads as work and not as a hang. */
function crewPending(what) {
  return `<p class="liveword crew-pending"><span class="dotpulse" aria-hidden="true"></span>${esc(what)}</p>`;
}

const CREW_STEPS = [
  ['roles', 'the agents and their models'],
  ['routes', 'which harnesses are installed'],
  ['egress', 'the network proxy'],
  ['meters', 'how much of each plan is left'],
];

function crewLoading() {
  const done = state.crewLoad || {};
  return `<div class="wrap"><div class="crew-loading">
      ${crewPending('Getting the crew page ready')}
      <ul>${CREW_STEPS.map(([key, label]) => `<li class="${done[key] ? 'done' : ''}">${
        done[key] ? '✓' : '·'} ${esc(label)}</li>`).join('')}</ul>
    </div></div>`;
}

function rolesScreen() {
  const r = state.roles;
  if (!r) return crewLoading();
  const pipeline = r.roles.filter((x) => x.kind === 'pipeline');
  const review = r.roles.filter((x) => x.kind === 'review');
  const orphans = r.roles.filter((x) => x.kind === 'unused');
  const spend = r.roles.reduce((a, x) => a + ((x.usage || {}).cost || 0), 0);
  /* Grouped by when it runs, because "which model is the arbiter on" and
     "what is an arbiter" are different questions and a flat list answers
     only the first. An agent no band names still appears, under
     the last one -- a new role reading as an orphan is how you lose it. */
  const placed = new Set(ROLE_PHASES.flatMap(([, , names]) => names));
  // Every agent, review roles included. Not a table of their own under the
  // pipeline's, which would put the one agent that reads the finished work
  // below the one that writes the packet about it.
  const banded = pipeline.concat(review);
  const bands = ROLE_PHASES
    .map(([title, note, names]) => ({
      title, note, rows: names.map((n) => banded.find((x) => x.name === n)).filter(Boolean),
    }))
    .concat([{
      title: 'not in any band', note: 'added here or in the config',
      rows: banded.filter((x) => !placed.has(x.name)),
    }])
    .filter((b) => b.rows.length);
  const band = (b) => `<div class="role-band"><b>${esc(b.title)}</b><span>${esc(b.note)}</span>${
    b.title === 'as-built' ? asBuiltBandNote(r) : ''}</div>`
    + b.rows.map(roleRow).join('');

  const heads = `<div class="role-row head">
      <span>agent</span><span>model</span><span class="rr-num rr-words">words</span>
      <span class="rr-num">temp</span>
      <span class="rr-num">effort</span><span class="rr-num">calls</span>
      <span class="rr-num">spend</span><span class="rr-fb">fallback</span></div>`;

  return `
    <div class="wrap">
      <section class="roles-top">
        <h1 class="crew-title">${icon('l-hard-hat', 'page-mark')}<span>The Crew.</span></h1>
        <p class="crew-sub">Who stands at each station, what they may see, and how much room they
          have to vary. Every agent names a route — the harness that carries its call, whether
          that is one completion or a session with a filesystem.
          <a href="#" data-help-open="the-crew">More about the crew</a> ·
          <a href="${AC_HREF}">Set models for many agents at once</a></p>
        ${pipelineDiagram(r.roles)}
      </section>

      ${routesSection()}

      ${egressSection()}

      ${lanesSection()}

      ${state.provider && (!state.provider.key_present || state.providerOpen) ? providerCard() : ''}

      <section class="section ac-entry">
        <div><b>Agents</b><span>Change models by side and by level instead of one agent at a
          time: one provider for the blue team, one for the red, and three levels of thinking
          on each.</span></div>
        <a class="btn btn-primary btn-sm" href="${AC_HREF}">Agent configuration →</a>
      </section>

      <section class="section">
        ${heads}
        ${bands.map(band).join('')}
        ${orphans.length ? band({ title: 'Configured, never called',
                                  note: 'no station asks for these', rows: orphans }) : ''}
      </section>

      <section class="section">
        <div class="role-band"><b>another reading</b><span>an agent you add here reads the same
          evidence bundle as the reviewer, and the worst verdict is the one that counts — add an
          angle of your own</span></div>
        ${review.length ? '' : '<p class="empty">None configured — the packet will have no findings.</p>'}
        <div class="role-grid add-agent">
          <input class="answer-field tiny" id="new-agent-name" placeholder="name" spellcheck="false">
          <input class="answer-field mono grow" id="new-agent-model" placeholder="vendor/model" spellcheck="false">
          <button class="btn btn-sm btn-primary" id="add-agent">Add</button>
        </div>
      </section>

      ${state.provider && state.provider.key_present && !state.providerOpen ? providerCard() : ''}
      <p class="small dim roles-foot">${r.roles.length} agents · $${spend.toFixed(2)} to date ·
        <span class="mono">${esc(r.config_path.split('/').slice(-1)[0])}</span>, comments intact ·
        prompts in <span class="mono">${esc(r.roles_dir.split('/').slice(-2).join('/'))}</span></p>
    </div>`;
}
