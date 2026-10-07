/* Setup: commissioning the line. The first screen a new machine sees, and
   reachable from the masthead after that.

   Five stations, each lit from what the server measured (GET /api/setup) and
   never from what was pressed: Docker and the way out, the accounts that pay
   for models, the harness each account brings, the crew, and the limits on
   one feature. Adding a project is the yard's job, not setup's. The lamps say where the machine is; the strip
   under them says which station you are reading. Two registers, kept apart,
   as on a feature's board.

   Everything that names an account, a harness or a model comes from the
   server, which reads it from factory.yaml. Nothing here knows a vendor. */

'use strict';

const SETUP_HREF = '#/?setup';

const SU_STATIONS = [
  ['machine', 'Machine'], ['accounts', 'Accounts'], ['harness', 'Harnesses'],
  ['crew', 'Crew'], ['limits', 'Limits'],
];

async function loadSetup() {
  state.setup = await api('/api/setup');
  if (!state.su) {
    // A first visit opens on the first station that is not working yet.
    const first = SU_STATIONS.findIndex(([id]) => !['green', 'skipped'].includes(suLamp(id)[0]));
    state.su = { at: first === -1 ? SU_STATIONS.length : first, preset: 'suggested',
                 build: null, check: null, said: {} };
  }
}

/* ------------------------------------------------------------ measuring */

const suJob = (name) => ((state.setup || {}).jobs || {})[name] || null;
const suRunning = (name) => (suJob(name) || {}).state === 'running';

/* Who serves the work, by the company that bills it: two routes on one
   company share its blind spots whatever harness carries them. */
function suFamily(route) {
  const d = state.setup;
  if (route === d.provider.route) return d.provider.account_label || route;
  const a = d.accounts.find((x) => x.name === route);
  return (a && (a.account_label || a.label)) || route;
}

function suRouteLabel(route) {
  const d = state.setup;
  if (route === d.provider.route) return `${d.provider.account_label} key`;
  const a = d.accounts.find((x) => x.name === route);
  return (a && (a.harness_label || a.label)) || route;
}

/* [lamp class, one line] for each station. */
function suLamp(id) {
  const d = state.setup;
  if (!d) return ['', 'not checked'];
  switch (id) {
    case 'machine': {
      const m = d.machine;
      if (suRunning('machine') || suRunning('docker')) return ['white blink', 'checking…'];
      if (!m) return ['', 'not checked'];
      if (!m.docker.ok) return ['red', 'Docker is not answering'];
      if (m.egress && !m.egress.ok) return ['red', 'containers cannot reach out'];
      if (m.disk && m.disk.low) return ['amber', `${m.disk.free_gb} GB free`];
      return ['green', `Docker ${m.docker.version}`];
    }
    case 'accounts': {
      if (d.accounts.some((a) => a.job && a.job.state === 'running')) return ['white blink', 'checking…'];
      const n = d.connected.length;
      if (!n) return ['', 'none yet'];
      const families = new Set(d.connected.map(suFamily));
      if (families.size < 2) return ['amber', `${n} connected · one company`];
      return ['green', `${n} connected`];
    }
    case 'harness': {
      const h = d.harnesses;
      if (!h.length) return ['', 'waits on an account'];
      if (h.some((x) => x.state === 'building')) return ['white blink', 'building…'];
      if (h.some((x) => x.state === 'failed')) return ['red', 'a build failed'];
      const ready = h.filter((x) => x.state === 'ready').length;
      if (ready === h.length && d.executor === 'command') return ['green', `${ready} ready`];
      return ['', `${ready} of ${h.length} built`];
    }
    case 'crew': {
      const c = d.crew;
      if (!c.confirmed) return ['', 'not chosen'];
      if (c.independence.length) return ['amber', 'one company both sides'];
      return ['green', `${suFamily(c.staffing.build)} builds · ${suFamily(c.staffing.check)} checks`];
    }
    case 'limits':
      return d.limits.confirmed ? ['green', `$${Number(d.limits.budget_usd).toFixed(2)} a feature`]
        : ['', 'not confirmed'];
    default:
      return ['', ''];
  }
}

/* ------------------------------------------------------------ pieces */

const suMark = (cls, label) => `<span class="su-mark ${cls}" role="img" aria-label="${esc(label)}"></span>`;

const suCmd = (c) => `<div class="su-cmd"><span aria-hidden="true">$</span><code>${esc(c)}</code>
  <button type="button" class="btn btn-quiet btn-sm" data-copy="${esc(c)}">Copy</button></div>`;

/* A fix Fabrika can make for you, said before it is made. */
const suOpt = (title, what, button) => `<div class="su-opt"><span class="su-opt-t">${title}</span>
  <span class="su-opt-c">${what}</span><span class="su-opt-a">${button}</span></div>`;

function suRow(cls, what, say, side = '', fix = '') {
  return `<div class="su-row">${suMark(cls, cls || 'not yet')}
    <div><div class="su-what">${what}</div><div class="su-say">${say}</div></div>
    <div class="su-side">${side}</div>${fix ? `<div class="su-fix">${fix}</div>` : ''}</div>`;
}

function suNav(next, opts = {}) {
  const i = state.su.at;
  return `<div class="su-actions">
    ${i > 0 ? `<button type="button" class="btn btn-quiet" data-su="go" data-to="${i - 1}">Back</button>` : ''}
    <span class="su-grow"></span>
    ${opts.because ? `<span class="su-because">${opts.because}</span>` : ''}
    ${opts.skip === false ? '' : `<button type="button" class="btn btn-quiet" data-su="go" data-to="${i + 1}">Skip for now</button>`}
    <button type="button" class="btn btn-primary" ${opts.act ? `data-su="${opts.act}"` : `data-su="go" data-to="${i + 1}"`}
      ${opts.disabled ? 'disabled' : ''}>${next}</button>
  </div>`;
}

/* ------------------------------------------------------------ 1. machine */

function suMachine() {
  const d = state.setup;
  const m = d.machine;
  const checking = suRunning('machine') || suRunning('docker');
  const failed = suJob('docker') && suJob('docker').state === 'failed' ? suJob('docker').detail : '';
  let rows = '';
  if (!m) {
    rows = suRow('white blink', 'Checking this machine', 'Asking Docker, then sending one container out through the proxy. The first time builds the proxy\'s small image, which can take a minute.');
  } else {
    const dk = m.docker;
    rows += suRow(checking ? 'white blink' : dk.ok ? 'green' : 'red', 'Docker',
      dk.ok ? 'Running. Every agent session and every check gets a sealed container of its own.'
        : `Not answering: ${esc(dk.detail)}`,
      dk.ok ? `engine ${esc(dk.version)}` : '',
      dk.ok || checking ? '' : `<div class="su-remedy red"><p>${failed ? esc(failed) : 'Two ways to fix it:'}</p>
        ${d.can_start_docker ? suOpt('Start Docker Desktop for me',
          'Fabrika runs <code>open -a Docker</code> and waits for the engine to answer, up to two minutes.',
          '<button type="button" class="btn btn-sm" data-su="start-docker">Start it</button>') : ''}
        ${suOpt('I\'ll start it myself', 'Start Docker, wait until it says it is running, then check again.',
          '<button type="button" class="btn btn-sm btn-quiet" data-su="machine">Check again</button>')}</div>`);
    if (dk.ok) {
      const e = m.egress;
      const failedChecks = e ? (e.checks || []).filter((c) => !c.ok) : [];
      rows += suRow(e && e.ok ? 'green' : e ? 'red' : '', 'The way out',
        e && e.ok ? 'A test container on a sealed network reached the internet through the proxy, which is the only way out it has.'
          : e ? `A test container could not get out through the proxy. ${esc(e.detail || failedChecks.map((c) => `${c.what}: ${c.detail}`).join('; '))}`
          : 'Not tested.',
        e && e.ok ? 'public only' : '',
        e && !e.ok ? `<div class="su-remedy red"><p>Usually a VPN, or a firewall that stops Docker reaching the internet.</p>
          ${suOpt('Restart the proxy and test again', 'Recreates the one container every sealed network goes out through.',
            '<button type="button" class="btn btn-sm" data-su="restart-proxy">Restart</button>')}
          ${suOpt('Disconnect the VPN and test again', 'Nothing in Fabrika changes.',
            '<button type="button" class="btn btn-sm btn-quiet" data-su="machine">Test again</button>')}</div>` : '');
      const disk = m.disk;
      rows += suRow(!disk ? '' : disk.low ? 'amber' : 'green', 'Disk space',
        !disk ? 'Measured from inside a container, once one of Fabrika\'s images is on this machine.'
          : disk.low ? `Enough to start, not to keep going. Each harness image is half a gigabyte to a gigabyte and a half, each project's environment is another, and every feature adds a checkout. Fabrika suggests at least ${disk.need_gb} GB.`
          : 'Room for the harness images and a few features at once.',
        disk ? `${disk.free_gb} of ${disk.total_gb} GB free` : '',
        disk && disk.low ? `<div class="su-remedy"><p>Fabrika can carry on as it is. To get room back, run one of these yourself — both delete things that are not Fabrika's too:</p>
          ${suCmd('docker system prune')}<p class="su-small">Stopped containers, unused networks, dangling images.</p>
          ${suCmd('docker builder prune')}<p class="su-small">The build cache.</p></div>` : '');
    }
    rows += suRow(m.git.ok ? 'green' : 'red', 'git',
      m.git.ok ? 'Every feature is built on its own branch, in its own worktree. Your checkout is never written to.'
        : 'Not found. Fabrika cannot make a worktree without it.',
      m.git.ok ? esc(m.git.version) : '');
  }
  const ok = m && m.docker.ok && (!m.egress || m.egress.ok);
  return `<h2 class="su-h">This machine</h2>
    <p class="su-lede">Fabrika builds inside sealed Docker containers, so Docker has to be running here.</p>
    <p class="su-lede-2">Every agent and every check runs in a container with no route to this machine, to other features, or to your own dev servers. The one way out is a proxy to public addresses, which is how the models are reached.</p>
    <div class="su-rows">${rows}</div>
    ${suNav(ok ? 'Continue to accounts' : 'Check again',
      { act: ok ? null : 'machine', disabled: checking, skip: !ok })}`;
}

/* ------------------------------------------------------------ 2. accounts */

function suProvider() {
  const p = state.setup.provider;
  const said = state.su.said.provider;
  const lamp = p.connected ? 'green' : said && !said.ok ? 'red' : '';
  const pill = p.connected ? '<span class="pill pill-green">key set</span>' : '<span class="pill">no key</span>';
  let body;
  if (p.key_source === 'environment') {
    body = `Read from the environment Fabrika was started from (ends ${esc(p.key_hint)}). Billed per token; the spend limit on station 5 is a real ceiling on it.`;
  } else {
    body = `${p.connected ? `A key is stored (ends ${esc(p.key_hint)}) in the credentials file beside factory.yaml: owner-only, ignored by git. Paste another to replace it.`
      : 'One key reaches models from many companies, billed per token. The simplest start, and the account OpenHands — the default harness — runs on.'}
      <div class="su-field"><input type="password" id="su-provider-key" autocomplete="off" spellcheck="false"
          placeholder="paste a key" aria-label="Provider API key">
        <button type="button" class="btn" data-su="provider-key">Test and save</button></div>`;
  }
  if (said) body += `<p class="su-said ${said.ok ? 'ok' : 'bad'}">${esc(said.detail)}</p>`;
  return `<div class="su-acct"><div class="su-acct-h">${suMark(lamp, p.connected ? 'connected' : 'no key')}
      <h3>${esc(p.account_label)}</h3><span class="su-who">API key · billed per token</span><span class="su-st">${pill}</span></div>
    <div class="su-body">${body}</div></div>`;
}

function suAccount(a) {
  const job = a.job && a.job.state === 'running' ? a.job : null;
  const failed = a.job && a.job.state === 'failed' ? a.job.detail : '';
  const said = state.su.said[a.name];
  const kind = a.takes_key ? 'API key · billed per token' : 'subscription';
  const name = esc(a.label || a.name);
  if (a.declined && !a.connected) {
    return `<div class="su-acct su-declined"><div class="su-acct-h">${suMark('', 'not yours')}<h3>${name}</h3>
      <span class="su-who">${esc(a.account_label)} · not yours</span><span class="su-st">
      <button type="button" class="btn btn-quiet btn-sm" data-su="undecline" data-route="${esc(a.name)}">Show</button></span></div></div>`;
  }
  let lamp = '', pill = '', body = '';
  const install = a.install_command ? suOpt('Install it for me',
    `Fabrika runs <code>${esc(a.install_command)}</code> on this machine. Needs Node.js.`,
    `<button type="button" class="btn btn-sm" data-su="install" data-route="${esc(a.name)}">Install</button>`) : '';
  const decline = suOpt(`I don't use ${esc(a.account_label)}`, 'Leave it. Nothing here needs it.',
    `<button type="button" class="btn btn-sm btn-quiet" data-su="decline" data-route="${esc(a.name)}">Not mine</button>`);
  if (job) {
    lamp = 'white blink'; pill = '<span class="pill">working…</span>';
    body = suJob(`check:${a.name}`) === a.job
      ? 'Making one short call through it, in a sealed container. The first check builds that container\'s image, which can take a few minutes.'
      : `Running <code>${esc(a.install_command)}</code>…`;
  } else if (a.connected) {
    lamp = 'green';
    pill = `<span class="pill pill-green">${a.takes_key ? 'key accepted' : 'signed in'}</span>`;
    body = `${a.takes_key ? `Billed per token${a.key_from_env ? ', on the key in the environment' : ''}, so the spend limit is a real ceiling on it.`
      : `Billed against your ${esc(a.account_label)} plan, not per token.`}${a.version ? ` Version ${esc(a.version)}.` : ''}
      ${a.models && a.models.length ? `<div class="su-models">${a.models.map(esc).join(' · ')}</div>` : ''}`;
  } else if (a.installed === false) {
    pill = '<span class="pill">not installed</span>';
    body = `Not on this machine. Optional: use it if you already ${a.takes_key ? `have a ${esc(a.account_label)} key` : `pay for a ${esc(a.account_label)} plan`}.
      <div class="su-remedy">${install}${decline}</div>`;
  } else if (a.takes_key) {
    lamp = said && !said.ok ? 'red' : '';
    pill = '<span class="pill">no key</span>';
    const steps = (a.setup_steps || []).map((s) => `<li>${esc(s)}</li>`).join('');
    body = `${esc(a.setup_note || '')}${steps ? `<ol class="su-steps">${steps}</ol>` : ''}
      ${a.key_from_env ? '' : `<div class="su-field"><input type="password" id="su-key-${esc(a.name)}" autocomplete="off" spellcheck="false"
          placeholder="paste a key" aria-label="${name} key">
        <button type="button" class="btn" data-su="route-key" data-route="${esc(a.name)}">${a.key_checkable ? 'Test and save' : 'Save'}</button></div>`}
      <div class="su-remedy su-quiet">${decline}</div>`;
  } else if (a.state === 'outdated') {
    lamp = 'red'; pill = '<span class="pill pill-red">too old</span>';
    body = `${esc(a.detail)}<div class="su-remedy red">${a.install_command ? suOpt('Update it for me',
      `Fabrika runs <code>${esc(a.install_command)}</code> again, which installs the newest version. Your sign-in is kept.`,
      `<button type="button" class="btn btn-sm" data-su="install" data-route="${esc(a.name)}">Update</button>`) : ''}</div>`;
  } else if (a.state === 'unauthenticated') {
    pill = '<span class="pill pill-accent">needs you to sign in</span>';
    const steps = (a.setup_steps || []).map((s) => `<li>${esc(s)}</li>`).join('');
    body = `Installed, and nobody has signed in. Signing in happens in a browser from your terminal, so it cannot be done from this page. Once per machine; Fabrika does not keep the token.
      ${steps ? `<ol class="su-steps">${steps}</ol>` : ''}${a.setup_command ? suCmd(a.setup_command) : ''}
      <div class="su-field"><button type="button" class="btn btn-sm" data-su="check" data-route="${esc(a.name)}">Check again</button></div>`;
  } else {
    // Installed, and either never checked or checked and refused for a
    // reason that is not a sign-in.
    lamp = a.state === 'error' || a.state === 'limited' ? 'red' : '';
    pill = `<span class="pill">${a.state === 'unknown' ? 'installed' : esc(a.state)}</span>`;
    body = `${a.state === 'unknown' ? `Installed${a.version ? `, version ${esc(a.version)}` : ''}. Check that it is signed in to your ${esc(a.account_label)} plan.` : esc(a.detail)}
      <div class="su-field"><button type="button" class="btn btn-sm" data-su="check" data-route="${esc(a.name)}">Check</button>
        <button type="button" class="btn btn-sm btn-quiet" data-su="decline" data-route="${esc(a.name)}">Not mine</button></div>`;
  }
  if (failed && !job) body += `<p class="su-said bad">${esc(failed)}</p>`;
  if (said && !job) body += `<p class="su-said ${said.ok ? 'ok' : 'bad'}">${esc(said.detail)}</p>`;
  return `<div class="su-acct"><div class="su-acct-h">${suMark(lamp, a.state || '')}<h3>${name}</h3>
      <span class="su-who">${esc(a.account_label)} · ${kind}</span><span class="su-st">${pill}</span></div>
    <div class="su-body">${body}</div></div>`;
}

function suAccounts() {
  const d = state.setup;
  const keyed = d.accounts.filter((a) => a.takes_key);
  const plans = d.accounts.filter((a) => !a.takes_key);
  const n = d.connected.length;
  const one = new Set(d.connected.map(suFamily)).size < 2;
  return `<h2 class="su-h">Accounts</h2>
    <p class="su-lede">Who pays for the models. You need one; two from different companies is better.</p>
    <p class="su-lede-2">Fabrika puts the agents that build on one company's models and the agents that check on another's, so a reviewer doesn't share the blind spots of the code's author. With one account it still works; the checking is just less independent.</p>
    <p class="su-label">Per token</p>
    ${suProvider()}${keyed.map(suAccount).join('')}
    ${plans.length ? `<p class="su-label su-gap">Plans you already pay for</p>${plans.map(suAccount).join('')}` : ''}
    ${d.switched_off.length ? `<p class="su-foot">${d.switched_off.map(esc).join(', ')} ${d.switched_off.length > 1 ? 'are' : 'is'} switched off in factory.yaml.</p>` : ''}
    ${suNav(n ? 'Continue to harnesses' : 'Continue', { disabled: !n,
      because: !n ? 'Connect at least one account to continue.' : one ? 'One company so far. Fine to continue.' : '' })}`;
}

/* ------------------------------------------------------------ 3. harnesses */

function suHarnesses() {
  const d = state.setup;
  const h = d.harnesses;
  const others = h.filter((x) => !x.default).map((x) => esc(x.label));
  const hasDefault = h.some((x) => x.default);
  const lede = !h.length ? 'Agents work through OpenHands by default. There is nothing to build until an account is connected.'
    : !others.length ? 'Every agent works through OpenHands. Its container image is built once.'
    : `${hasDefault ? 'Agents on the provider key work through OpenHands; the rest work through ' : 'Agents work through '}${others.join(' and ')}. Each image is built once.`;
  const rows = h.map((x) => {
    const lamp = { ready: 'green', building: 'white blink', failed: 'red', unsealed: 'red', none: '' }[x.state];
    const say = x.state === 'ready' ? 'Installed, and started once inside the image to prove it runs.'
      : x.state === 'building' ? 'Installing. Runs in the background; carry on while it builds.'
      : x.state === 'failed' || x.state === 'unsealed' ? esc(x.detail).replace(/\n/g, '<br>')
      : 'Not built yet.';
    return suRow(lamp, `${esc(x.label)} <span class="su-for">${x.default ? 'the default · ' : ''}for the ${esc(x.account_label)} ${x.default ? 'key' : 'account'}</span>`,
      `${say}${x.install.length ? `<details class="su-inside"><summary>What goes in · built in <code>${esc(x.build_image)}</code></summary>
        <pre>${x.install.map(esc).join('\n')}</pre></details>` : ''}`,
      x.state === 'ready' ? 'ready' : '',
      x.state === 'failed' ? `<div class="su-remedy red">${suOpt('Build it again',
        'Most often a network blip during the install. The layers that already built are kept.',
        '<button type="button" class="btn btn-sm" data-su="harnesses">Rebuild</button>')}</div>` : '');
  }).join('');
  const pending = h.filter((x) => ['none', 'failed'].includes(x.state));
  const building = h.some((x) => x.state === 'building');
  const allReady = h.length && h.every((x) => x.state === 'ready');
  const direct = d.executor !== 'command';
  return `<h2 class="su-h">Harnesses</h2>
    <p class="su-lede">${lede}</p>
    <p class="su-lede-2">A harness is the program that lets a model read files, run commands and edit, the way you would in a terminal. OpenHands is the default for every agent on the provider key. The others appear here only if you connected them.</p>
    <details class="su-why"><summary>Why these images need building</summary>
      <dl>
        <dt>Agents never run on this machine.</dt>
        <dd>A harness started here could read any file on your disk and reach any server you run, and some have a shell switched on by default. So every agent session runs in a container with nothing mounted and one way out: the proxy to public addresses. A harness has to be installed in that container to run there.</dd>
        <dt>It has to stand in your project's environment.</dt>
        <dd>An agent runs your project's own tests and linters, which need your project's dependencies. So the harness is added as a layer on top of each project's image rather than living in a box of its own, and what it runs against is what your checks run against.</dd>
        <dt>Your image is left alone.</dt>
        <dd>Each harness is installed in a stock image that has what it needs, and only its own folder, <code>/opt/harness</code>, is copied across. Your project's image only has to be a Linux with a shell; nothing installs packages into it.</dd>
        <dt>A broken harness fails here, not mid-feature.</dt>
        <dd>The last step of every build starts the harness in the finished image. If it cannot run there, the build fails with the reason, instead of every agent session failing an hour into a run.</dd>
        <dt>Built once, rebuilt only when something changes.</dt>
        <dd>Images are cached by their contents. A new harness version, a change to Fabrika's driver or a change to your project's environment rebuilds the layer; otherwise every feature reuses it. What is built here is the install itself, so adding a project only has to put the finished layer on top of that project's image.</dd>
      </dl></details>
    ${h.length ? `<div class="su-rows">${rows}</div>
      ${pending.length || (allReady && direct) ? `<div class="su-field">
        <button type="button" class="btn" data-su="harnesses">${pending.length ? `Build ${pending.length === 1 ? 'the image' : `all ${pending.length} images`}` : 'Use these harnesses'}</button>
        <span class="su-small">${direct ? 'Also switches agents from writing whole files with no tools to working through these harnesses (<code>executor.kind: command</code> in factory.yaml).' : 'Runs in the background.'}</span></div>` : ''}`
      : `<div class="su-remedy"><p>No account is connected yet, so there is no harness to build.
        <button type="button" class="btn btn-quiet btn-sm" data-su="go" data-to="1">Back to accounts</button></p></div>`}
    ${suNav('Continue to crew', { because: building ? 'Builds keep going if you move on.' : '' })}`;
}

/* ------------------------------------------------------------ 4. crew */

function suCrew() {
  const d = state.setup;
  const su = state.su;
  const conn = d.connected;
  const staffed = d.crew.staffing;
  if (!su.build || !conn.includes(su.build)) {
    su.build = staffed && conn.includes(staffed.build) ? staffed.build : conn[0] || null;
  }
  if (!su.check || !conn.includes(su.check)) {
    su.check = staffed && conn.includes(staffed.check) ? staffed.check
      : conn.find((r) => suFamily(r) !== suFamily(su.build)) || conn[0] || null;
  }
  const routesAll = [d.provider.route, ...d.accounts.map((a) => a.name)];
  const provs = (side) => routesAll.map((r) => {
    const on = conn.includes(r);
    return `<button type="button" class="su-prov" ${on ? '' : 'disabled'} aria-pressed="${su[side] === r}"
        data-su="side" data-side="${side}" data-route="${esc(r)}">
      <span class="su-pn">${esc(suRouteLabel(r))}</span><span class="su-pv">${on ? esc(suFamily(r)) : 'not connected'}</span></button>`;
  }).join('');
  const same = su.build && suFamily(su.build) === suFamily(su.check);
  const verdict = !su.build ? '' : same
    ? `<div class="su-verdict warn">${suMark('amber', 'warning')}<div>Both sides on ${esc(suFamily(su.build))}. It works, but a reviewer from the same company as the author tends to miss the same things. ${new Set(conn.map(suFamily)).size > 1 ? 'Pick a different account for the checkers.' : 'Connect a second account to fix it.'}</div></div>`
    : `<div class="su-verdict good">${suMark('green', 'independent')}<div>${esc(suFamily(su.build))} builds and ${esc(suFamily(su.check))} checks, so a reviewer won't share the author's blind spots.</div></div>`;
  const presets = [['suggested', 'Sensible defaults', 'Judgment work (specs, plans, verdicts) on the strongest model; making and reading on cheaper ones. A starting guess, not a measurement.'],
    ['lean', 'Lean', 'Only the spec, the plan, the blind tests and the review stay deep. Cheaper, and worse at catching subtle mistakes.'],
    ['deep', 'Everything deep', 'Every agent on the strongest model. Slowest and most expensive; useful as a ceiling to compare against.']];
  const now = presets.find(([k]) => k === su.preset);
  return `<h2 class="su-h">Crew</h2>
    <p class="su-lede">Who does the work. Pick who builds and who checks; Fabrika fills in every agent from that.</p>
    <p class="su-lede-2">Each agent sits on a side and at a level of thinking, so two choices staff all of them.${staffed ? ' A crew is already written to factory.yaml; choosing here replaces it.' : ''} You can still change any single agent later.</p>
    ${conn.length ? `<div class="su-teams">
        <div class="su-team build"><h3>Blue team <span>builds</span></h3><p>Writes the spec, the plan, the code and the repairs.</p><div class="su-provs">${provs('build')}</div></div>
        <div class="su-team check"><h3>Red team <span>checks</span></h3><p>Writes blind tests, attacks the result, reviews it.</p><div class="su-provs">${provs('check')}</div></div>
      </div>${verdict}
      <div class="su-levels"><p class="su-label">How hard they think</p>
        <div class="su-seg" role="group" aria-label="Starting point">${presets.map(([k, l]) =>
          `<button type="button" data-su="preset" data-preset="${k}" aria-pressed="${su.preset === k}">${l}</button>`).join('')}</div>
        <p class="su-small">${now[2]}</p></div>
      <p class="su-foot">Every agent's model, side and level is on <a href="${AC_HREF}">Agent configuration</a>, once this is written.</p>`
      : `<div class="su-remedy"><p>The crew is staffed from your accounts, and none is connected yet.
        <button type="button" class="btn btn-quiet btn-sm" data-su="go" data-to="1">Back to accounts</button></p></div>`}
    ${suNav('Use this crew', { act: 'crew', disabled: !conn.length })}`;
}

/* ------------------------------------------------------------ 5. limits */

function suLimits() {
  const L = state.setup.limits;
  return `<h2 class="su-h">Limits</h2>
    <p class="su-lede">How far one feature may go on its own before it stops and hands back what it has.</p>
    <p class="su-lede-2">After review, Fabrika tries to fix what the reviewers found. These three numbers bound that loop. When one runs out the run stops and you get the packet as it stands, with every unfixed finding still in it.</p>
    <div class="su-rows">
      <div class="su-limit"><label for="su-budget">Spend per feature</label>
        <span>The run stops before the next call would cross it. Only per-token accounts count against it; a plan's limit is its own usage window.</span>
        <span class="su-in">$ <input type="number" id="su-budget" min="0.5" step="0.5" value="${Number(L.budget_usd).toFixed(2)}"></span></div>
      <div class="su-limit"><label for="su-rounds">Repair rounds</label>
        <span>How many times the fix-and-review loop may go round. Each round re-runs your checks and the review.</span>
        <span class="su-in"><input type="number" id="su-rounds" min="0" max="10" step="1" value="${L.max_rounds}"></span></div>
      <div class="su-limit"><label for="su-minutes">Time per feature</label>
        <span>Wall clock from the spec being frozen to the packet.</span>
        <span class="su-in"><input type="number" id="su-minutes" min="15" step="15" value="${Number(L.wall_clock_minutes)}"> min</span></div>
    </div>
    <details class="su-why"><summary>What this writes to factory.yaml</summary>
      <pre>rework:\n  budget_usd: …\n  max_rounds: …\n  wall_clock_minutes: …   # everything else in rework is left as it is</pre></details>
    ${suNav('Keep these limits', { act: 'limits' })}`;
}

/* ------------------------------------------------------------ summary */

function suDone() {
  const open = SU_STATIONS.map(([id, name], i) => [i, name, suLamp(id)])
    .filter(([, , [cls]]) => !['green', 'skipped'].includes(cls));
  return `<div class="su-done"><span class="su-plate">${open.length ? 'Running' : 'Commissioned'}</span>
      <p>${open.length ? `The line runs, with ${open.length} station${open.length > 1 ? 's' : ''} still to look at.` : 'Every station works. The line is ready for its first feature.'}</p></div>
    ${open.length ? `<div class="su-rows">${open.map(([i, name, [cls, st]]) => suRow(cls, esc(name), esc(st),
      `<button type="button" class="btn btn-quiet btn-sm" data-su="go" data-to="${i}">Open</button>`)).join('')}</div>` : ''}
    <p class="su-lede-2 su-gap">This board stays under <b>setup</b> in the masthead. Anything that stops working later lights here too.</p>
    <div class="su-actions"><span class="su-grow"></span><button type="button" class="btn btn-primary" data-su="finish">Go to the yard</button></div>`;
}

/* ------------------------------------------------------------ the screen */

const SU_WHY = [
  ['Why Docker', 'An agent that runs tests can also run anything else. The container is what makes that safe, and what stops a build passing against your own app on localhost instead of its own.'],
  ['Why two companies', 'Models from one company share training and priors. Splitting the builders from the checkers is the cheapest way to make a review mean something.'],
  ['Why a harness', 'Without tools a model guesses at whether its code runs. With one, it runs the tests and reads what failed, and that log is part of the evidence you review.'],
  ['Why sides and levels', 'Judgment — what to build, whether it is right — is where a stronger model pays for itself. Typing out a plan it already made is not.'],
  ['Why limits', 'The loop can always find one more thing to fix. A limit turns "it kept going" into a packet you can rule on, with what is left said plainly.'],
];

function setupScreen() {
  const d = state.setup;
  if (!d || !state.su) return '<div class="wrap"><p class="empty">Measuring this machine…</p></div>';
  const at = state.su.at;
  const lamps = SU_STATIONS.map(([id]) => suLamp(id));
  const working = lamps.filter(([c]) => c === 'green' || c === 'skipped').length;
  const stopped = lamps.filter(([c]) => c === 'red').length;
  const board = `<section class="su-board" aria-labelledby="su-title"><div class="su-board-in">
      <div class="su-board-head">
        <div><h1 id="su-title">Commissioning the line</h1>
          <p>Five things have to work before Fabrika can build anything. Each lamp lights from what Fabrika measured on this machine, not from what you clicked.</p></div>
        <div class="su-counts">
          <div><b>${SU_STATIONS.length}</b><span>plan</span></div><div><b>${working}</b><span>working</span></div>
          <div class="${stopped ? 'stop' : ''}"><b>${stopped}</b><span>stopped</span></div></div>
      </div>
      <ol class="su-line">${SU_STATIONS.map(([, name], i) => {
        const [cls, said] = lamps[i];
        const lit = cls && cls !== 'skipped' ? 'lit' : '';
        const tone = cls.startsWith('red') ? 'red' : cls.startsWith('amber') ? 'amber' : '';
        return `<li><button type="button" class="su-stn" data-su="go" data-to="${i}" aria-current="${at === i ? 'step' : 'false'}"
            aria-label="${i + 1}. ${esc(name)}: ${esc(said)}">
          <span class="lamp ${cls.split(' ').join(' ')} ${lit}"></span>
          <span class="stn-no">${i + 1}</span><span class="su-nm">${esc(name)}</span><span class="su-st ${tone}">${esc(said)}</span>
        </button></li>`;
      }).join('')}</ol>
      <div class="su-strip" aria-hidden="true">${SU_STATIONS.map(([, name], i) =>
        `<span class="${at === i ? 'here' : ''}">${at === i ? 'you are here' : esc(name)}</span>`).join('')}</div>
    </div></section>`;
  const done = at >= SU_STATIONS.length;
  const sheet = done ? suDone() : [suMachine, suAccounts, suHarnesses, suCrew, suLimits][at]();
  const why = SU_WHY[Math.min(at, SU_WHY.length - 1)];
  const aside = done ? `<h4>Changing things later</h4><p>Accounts and harnesses are on the crew page, every agent on Agent configuration. Limits and everything else live in factory.yaml, which this screen only edits when you press a button that says so.</p>`
    : `<h4>${why[0]}</h4><p>${why[1]}</p>
      <div class="su-later"><h4>Done this before?</h4><p>Everything here is also in <code>factory.yaml</code>.
        <button type="button" class="btn btn-quiet btn-sm" data-su="finish">Skip setup</button></p></div>`;
  return `${board}<div class="wrap su-floor"><div class="su-sheet">${sheet}</div><aside class="su-aside">${aside}</aside></div>`;
}

/* ------------------------------------------------------------ acting */

function suRefresh() {
  return loadSetup().then(render).catch((e) => errorToast(e.message));
}

async function suPost(url, body, method = 'POST') {
  return api(url, { method, body: body === undefined ? undefined : JSON.stringify(body) });
}

const suValue = (id) => ((document.getElementById(id) || {}).value || '').trim();

async function setupClick(target) {
  const t = target.dataset;
  const su = state.su;
  switch (t.su) {
    case 'go':
      su.at = Math.max(0, Math.min(SU_STATIONS.length, Number(t.to)));
      render();
      window.scrollTo(0, 0);
      return;
    case 'machine':
      await suPost('/api/setup/machine');
      return suRefresh();
    case 'start-docker':
      await suPost('/api/setup/docker/start');
      return suRefresh();
    case 'restart-proxy':
      await withBusy('Restarting the proxy.', () => suPost('/api/egress/start'));
      await suPost('/api/setup/machine');
      return suRefresh();
    case 'provider-key': {
      const key = suValue('su-provider-key');
      if (!key) { toast('Paste a key first.'); return; }
      await withBusy('Saving the key and asking the provider about it.', async () => {
        await api('/api/provider', { method: 'PUT', body: JSON.stringify({ api_key: key }) });
        su.said.provider = await suPost('/api/provider/test');
      });
      return suRefresh();
    }
    case 'route-key': {
      const key = suValue(`su-key-${t.route}`);
      if (!key) { toast('Paste a key first.'); return; }
      await withBusy('Testing the key.', async () => {
        su.said[t.route] = await suPost(`/api/setup/routes/${encodeURIComponent(t.route)}/key`, { api_key: key }, 'PUT');
      });
      return suRefresh();
    }
    case 'install':
      await suPost(`/api/setup/routes/${encodeURIComponent(t.route)}/install`);
      return suRefresh();
    case 'check':
      delete su.said[t.route];
      await suPost(`/api/setup/routes/${encodeURIComponent(t.route)}/check`);
      return suRefresh();
    case 'decline':
    case 'undecline':
      await suPost(`/api/setup/routes/${encodeURIComponent(t.route)}/decline`, { declined: t.su === 'decline' });
      return suRefresh();
    case 'harnesses':
      await withBusy('Starting the builds.', () => suPost('/api/setup/harnesses'));
      return suRefresh();
    case 'side':
      su[t.side] = t.route;
      render();
      return;
    case 'preset':
      su.preset = t.preset;
      render();
      return;
    case 'crew':
      await withBusy('Writing the crew to factory.yaml.', async () => {
        await suPost('/api/setup/crew', { build: su.build, check: su.check, preset: su.preset }, 'PUT');
        state.roles = null;
        state.ac = null;
        su.at = 4;
        toast('Crew written to factory.yaml.');
      });
      return suRefresh();
    case 'limits':
      await withBusy('Writing the limits to factory.yaml.', async () => {
        await suPost('/api/setup/limits', {
          budget_usd: Number(suValue('su-budget')), max_rounds: Number(suValue('su-rounds')),
          wall_clock_minutes: Number(suValue('su-minutes')),
        }, 'PUT');
        su.at = SU_STATIONS.length;
        toast('Limits written to factory.yaml.');
      });
      return suRefresh();
    case 'finish':
      // Finished or skipped, setup stops opening by itself; the yard is where
      // a project is added.
      await suPost('/api/setup/finish');
      location.hash = '#/';
      return undefined;
    default:
  }
}
