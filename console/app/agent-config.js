/* Agent configuration: the crew set by side and by level rather than one
   agent at a time. Reached from the crew page, which it does not replace. */

'use strict';

/* ------------------------------------------------------- 0d2. agent config

   Every agent's model follows from two choices: which provider serves each
   side, and how hard the agent has to think. The screen holds a draft of both
   and of where each agent sits, measures it against what runs now, and writes
   it in one PUT -- the config resolves it, so nothing downstream learns that
   levels exist. */

const AC_HREF = '#/?agent-config';

const AC_LEVELS = [
  ['deep', 'Deep', 'judgment: specs, plans, verdicts'],
  ['standard', 'Standard', 'making: code, repairs, routing'],
  ['light', 'Light', 'reading: scans and lookups'],
];
const AC_SIDES = [
  ['build', 'Blue team', 'builds', 'Makes something: a spec, a plan, code, a repair.'],
  ['neither', 'Neither', 'reads, asks, routes', 'Reads, asks, routes and presents. Follows a side unless one is moved.'],
  ['check', 'Red team', 'checks', 'Gives a verdict on someone else\'s work.'],
];
const AC_EFFORTS = ['', 'none', 'low', 'medium', 'high'];
/* The agents a lean crew does not move down: the four whose output everything
   after them is measured against. */
const AC_KEEP_DEEP = new Set(['spec_writer', 'architect', 'oracle', 'reviewer']);

function acRoute(name) {
  return ((state.ac || {}).routes || []).find((r) => r.name === name) || { name };
}
function acRouteLabel(name) {
  const r = acRoute(name);
  return r.account_label || r.label || name;
}
/* A route is usable for a side when it is switched on. Whether anyone is
   signed in is the crew page's question; this one only refuses what the
   server would refuse. */
function acUsable(name) { return acRoute(name).enabled !== false; }

/* The same identity `model_family` uses: one vendor per CLI route, whatever
   model is picked off its list; on an api route the carrier is a shop, and
   the model id names the mind. */
function acFamily(route, model) {
  const r = acRoute(route);
  if (r.kind === 'api') return String(model || '').split('/')[0] || route;
  return r.account_label || route;
}

function acRole(name) { return state.ac.data.roles.find((r) => r.name === name); }

function acSideOf(name) {
  const role = acRole(name);
  const want = state.ac.draft.roles[name];
  return role.team !== 'neither' ? role.team : (want.side || 'neither');
}

function acRouteOfSide(side) {
  const s = state.ac.draft.staffing;
  if (side === 'build') return s.build;
  if (side === 'check') return s.check;
  if (s.neither === 'build') return s.build;
  if (s.neither === 'check') return s.check;
  return s.neither;
}

function acResolve(name) {
  const want = state.ac.draft.roles[name];
  if (want.pin) {
    return { route: want.pin.route, model: want.pin.model,
             effort: want.pin.reasoning_effort || '', pinned: true };
  }
  const route = acRouteOfSide(acSideOf(name));
  const lv = ((state.ac.draft.levels[route] || {})[want.level]) || {};
  return { route, model: lv.model || '', effort: lv.reasoning_effort || '', pinned: false };
}

/* The most used route among a team's agents now, so a first visit starts from
   where the crew already runs rather than from a guess. */
function acCommonRoute(roles, team) {
  const counts = {};
  roles.filter((r) => r.team === team && r.enabled).forEach((r) => {
    counts[r.route] = (counts[r.route] || 0) + 1;
  });
  return Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0] || '';
}

/* What a level should mean on a route before anything has been chosen: what
   the agents suggested for that level already run on there, most of them
   agreeing. The preset only where none of them says, or to break a tie. */
function acCommonChoice(data, route, lv, placed) {
  const preset = (data.presets[route] || {})[lv];
  const counts = {};
  data.roles.filter((r) => r.enabled && r.route === route && placed[r.name] === route
                           && (data.suggested[r.name] || 'standard') === lv)
    .forEach((r) => {
      const key = `${r.model}|${r.reasoning_effort || ''}`;
      counts[key] = (counts[key] || 0) + 1;
    });
  const top = Math.max(0, ...Object.values(counts));
  if (!top) return preset;
  const best = Object.keys(counts).filter((k) => counts[k] === top);
  const presetKey = preset ? `${preset.model}|${preset.reasoning_effort || ''}` : '';
  const [model, effort] = (best.includes(presetKey) ? presetKey : best[0]).split('|');
  return { model, reasoning_effort: effort };
}

function acDraftFrom(data) {
  const staffing = data.staffing ? { ...data.staffing } : {
    build: acCommonRoute(data.roles, 'build') || data.default_route,
    check: acCommonRoute(data.roles, 'check') || data.default_route,
    neither: 'build',
  };
  const routeOf = (r) => {
    const side = r.team !== 'neither' ? r.team : (r.side || 'neither');
    const pick = side === 'neither' ? staffing.neither : side;
    return pick === 'build' || pick === 'check' ? staffing[pick] : pick;
  };

  // Which route each agent's side puts it on.
  const placed = Object.fromEntries(data.roles.map((r) => [r.name, routeOf(r)]));
  const levels = {};
  const names = new Set([...Object.keys(data.presets), ...Object.keys(data.levels)]);
  names.forEach((route) => {
    levels[route] = {};
    AC_LEVELS.forEach(([lv]) => {
      const choice = (data.levels[route] || {})[lv]
        || (data.staffing ? (data.presets[route] || {})[lv] : acCommonChoice(data, route, lv, placed));
      if (choice) levels[route][lv] = { model: choice.model, reasoning_effort: choice.reasoning_effort || '' };
    });
  });

  const roles = {};
  data.roles.forEach((r) => {
    const own = { route: r.route, model: r.model, reasoning_effort: r.reasoning_effort };
    if (data.staffing) {
      // Under a staffing block a role with its own model is pinned, and stays so.
      roles[r.name] = { level: r.level || data.suggested[r.name] || 'standard', side: r.side || '',
                        pin: r.pinned.length ? own : null };
      return;
    }
    /* Before anything is chosen, opening this screen must change nothing. An
       agent goes on its suggested level if that level is what it runs now, on
       another level if that one is, and otherwise keeps what it runs, pinned --
       so the first thing on screen is the crew as it is, not a proposal. */
    const route = routeOf(r);
    const same = (lv) => {
      const c = (levels[route] || {})[lv];
      return !!c && r.route === route && c.model === r.model
        && (c.reasoning_effort || '') === (r.reasoning_effort || '');
    };
    const suggested = data.suggested[r.name] || 'standard';
    const level = same(suggested) ? suggested : (AC_LEVELS.map(([lv]) => lv).find(same) || suggested);
    roles[r.name] = { level, side: r.side || '', pin: same(level) ? null : own };
  });
  return { staffing, levels, roles };
}

async function loadAgentConfig() {
  const [data, routes] = await Promise.all([
    api('/api/staffing'),
    api('/api/routes').catch(() => ({ routes: [] })),
  ]);
  const draft = acDraftFrom(data);
  state.ac = { data, routes: routes.routes || [], draft, saved: JSON.stringify(draft),
               sel: [], showAll: false, preset: null };
}

function acDirty() { return JSON.stringify(state.ac.draft) !== state.ac.saved; }

/* ------------------------------------------------------------------ drawing */

function acProvButton(side, route) {
  const on = state.ac.draft.staffing[side] === route.name;
  const off = route.enabled === false;
  return `<button type="button" class="ac-prov" data-ac="side" data-side="${side}"
      data-route="${esc(route.name)}" aria-pressed="${on}" ${off ? 'disabled' : ''}>
      <span class="ac-radio"></span>
      <span><span class="ac-pn">${esc(route.account_label || route.label || route.name)}</span>
        <span class="ac-pv">via ${esc(route.harness_label || route.label || route.name)} · ${
          esc(route.account_kind === 'key' ? 'billed per token' : 'subscription')}</span></span>
      <span class="ac-pstate">${off ? 'switched off' : ''}</span>
    </button>`;
}

function acSides() {
  const d = state.ac.draft;
  const count = (side) => Object.keys(d.roles).filter((n) => acSideOf(n) === side).length;
  const routes = state.ac.routes;
  return AC_SIDES.map(([k, name, verb, who]) => {
    if (k === 'neither') {
      const opts = [['build', 'Same as the blue team'], ['check', 'Same as the red team'],
        ...routes.filter((r) => r.enabled !== false).map((r) => [r.name, `Its own: ${r.account_label || r.label || r.name}`])];
      return `<div class="ac-side neither">
        <h3>${name} <span class="pill">${count('neither')} agents</span></h3>
        <p class="ac-who">${who}</p>
        <div class="ac-provs">${opts.map(([v, l]) => `
          <button type="button" class="ac-prov" data-ac="neither" data-value="${esc(v)}"
            aria-pressed="${d.staffing.neither === v}">
            <span class="ac-radio"></span><span class="ac-pn">${esc(l)}</span>
            <span class="ac-pstate">${v === 'build' || v === 'check' ? esc(acRouteLabel(d.staffing[v])) : ''}</span>
          </button>`).join('')}</div>
      </div>`;
    }
    return `<div class="ac-side ${k}">
      <h3>${name} <span class="pill">${count(k)} agents · ${verb}</span></h3>
      <p class="ac-who">${who}</p>
      <div class="ac-provs">${routes.map((r) => acProvButton(k, r)).join('')}</div>
    </div>`;
  }).join('');
}

/* What the draft would break, said before the write rather than after it. */
function acChecks() {
  const d = state.ac.draft;
  const names = Object.keys(d.roles).filter((n) => acRole(n).enabled);
  const res = Object.fromEntries(names.map((n) => [n, acResolve(n)]));
  const fam = (n) => acFamily(res[n].route, res[n].model);
  const out = [];

  const families = (team) => new Set(names.filter((n) => acRole(n).team === team).map(fam));
  const blue = families('build');
  const red = families('check');
  const shared = [...blue].filter((f) => red.has(f));
  out.push(shared.length
    ? ['bad', `<b>Blue and red share ${esc(shared.join(', '))}.</b> A checker from the author's
        own family shares its blind spots, and its agreement stops being a check.`]
    : ['lit', `Blue builds on <b>${esc([...blue].join(', ') || '—')}</b>, red checks on
        <b>${esc([...red].join(', ') || '—')}</b>. No vendor is on both sides.`]);

  Object.entries(state.ac.data.independent_of).forEach(([who, others]) => {
    if (!res[who]) return;
    others.forEach((other) => {
      if (!res[other] || fam(who) !== fam(other)) return;
      // A builder and a checker sharing a vendor is the line above.
      const a = acRole(who).team;
      const b = acRole(other).team;
      if (a !== 'neither' && b !== 'neither' && a !== b) return;
      out.push(['bad', `<b>${esc(who)} and ${esc(other)} are both ${esc(fam(who))}.</b>
        ${esc(who)} exists to disagree with ${esc(other)}.`]);
    });
  });

  const { gaps, off } = acGaps();
  gaps.forEach((g) => out.push(['bad', `<b>No model for ${esc(g)}.</b> Choose one under Levels.`]));
  off.forEach((r) => out.push(['bad', `<b>${esc(acRouteLabel(r))} is switched off.</b> Agents on it would fail before their first call.`]));

  const pinned = names.filter((n) => d.roles[n].pin);
  if (pinned.length) {
    out.push(['warn', `${pinned.length} pinned: <span class="mono">${esc(pinned.join(', '))}</span>
      ${pinned.length === 1 ? 'keeps its' : 'keep their'} own model whatever the level says.${
        state.ac.data.staffing ? '' : ` ${pinned.length === 1 ? 'It runs' : 'They run'} something no
        level says yet. Select and unpin to put ${pinned.length === 1 ? 'it' : 'them'} on ${
        pinned.length === 1 ? 'its' : 'their'} level.`}`]);
  }
  return out;
}

/* What would stop the write: an agent with no model to run, or one on a
   route that is switched off. The server refuses both; this says so first. */
function acGaps() {
  const d = state.ac.draft;
  const names = Object.keys(d.roles).filter((n) => acRole(n).enabled);
  const res = names.map((n) => [n, acResolve(n)]);
  const gaps = [...new Set(res.filter(([, r]) => !r.model)
    .map(([n, r]) => `${d.roles[n].level} on ${acRouteLabel(r.route)}`))];
  const off = [...new Set(res.map(([, r]) => r.route))].filter((r) => !acUsable(r));
  return { gaps, off };
}

function acRoutesInUse() {
  const d = state.ac.draft;
  return [...new Set(Object.keys(d.roles).map((n) => acRouteOfSide(acSideOf(n))))];
}

function acLevelsTable() {
  const d = state.ac.draft;
  const used = acRoutesInUse();
  const all = state.ac.routes.map((r) => r.name);
  const shown = state.ac.showAll ? all : all.filter((r) => used.includes(r));
  const presets = state.ac.data.presets;
  const head = shown.map((r) => {
    const route = acRoute(r);
    const sides = ['build', 'check'].filter((s) => d.staffing[s] === r);
    return `<th class="${used.includes(r) ? '' : 'ac-unused'}">${esc(route.account_label || route.label || r)}
      ${sides.map((s) => `<span class="ac-tag ${s}">${s === 'build' ? 'blue' : 'red'}</span>`).join('')}
      <span class="ac-pv">via ${esc(route.harness_label || route.label || r)}</span></th>`;
  }).join('');
  const rows = AC_LEVELS.map(([lv, name, what]) => `<tr>
      <th><span class="ac-ln">${name}</span><span class="ac-ld">${what}</span></th>
      ${shown.map((r) => {
        const cur = (d.levels[r] || {})[lv] || { model: '', reasoning_effort: '' };
        const pre = (presets[r] || {})[lv];
        const changed = pre && (pre.model !== cur.model || (pre.reasoning_effort || '') !== (cur.reasoning_effort || ''));
        const models = [...new Set([...(acRoute(r).models || []), cur.model].filter(Boolean))];
        const n = Object.keys(d.roles).filter((x) => !d.roles[x].pin && d.roles[x].level === lv
          && acRouteOfSide(acSideOf(x)) === r).length;
        const was = acLevelWas(r, lv);
        return `<td class="${used.includes(r) ? '' : 'ac-unused'} ${was ? 'ac-changed' : ''}"><div class="ac-lv">
          <select data-ac-route="${esc(r)}" data-ac-level="${lv}" data-ac-key="model"
            aria-label="${esc(acRouteLabel(r))} ${name} model">
            ${cur.model ? '' : '<option value="">— choose —</option>'}
            ${models.map((m) => `<option ${m === cur.model ? 'selected' : ''}>${esc(m)}</option>`).join('')}
          </select>
          <select data-ac-route="${esc(r)}" data-ac-level="${lv}" data-ac-key="reasoning_effort"
            aria-label="${esc(acRouteLabel(r))} ${name} effort">
            ${AC_EFFORTS.map((e) => `<option value="${e}" ${e === (cur.reasoning_effort || '') ? 'selected' : ''}>${
              e || 'model default'}</option>`).join('')}
          </select>
          ${changed ? `<button type="button" class="ac-reset" data-ac="reset" data-route="${esc(r)}"
            data-level="${lv}" title="${esc(pre.model)}${pre.reasoning_effort ? ' · ' + esc(pre.reasoning_effort) : ''}">preset</button>` : ''}
        </div><span class="ac-count">${n ? `${n} agent${n === 1 ? '' : 's'}` : 'nobody, now'}${
          was ? ` · <span class="ac-was">was ${esc(was)}</span>` : ''}</span></td>`;
      }).join('')}</tr>`).join('');
  const others = all.length - shown.length;
  return `<div class="ac-scroll"><table class="ac-levels"><thead><tr><th></th>${head}</tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${others || state.ac.showAll ? `<button type="button" class="btn btn-quiet btn-sm" data-ac="more">${
      state.ac.showAll ? 'Show only the providers in use'
        : `Also set levels for the other ${others} provider${others === 1 ? '' : 's'}`}</button>` : ''}`;
}

function acChip(name) {
  const want = state.ac.draft.roles[name];
  const role = acRole(name);
  const res = acResolve(name);
  const sel = state.ac.sel.includes(name);
  const was = acWas(name);
  return `<button type="button" class="ac-chip ${role.team} ${sel ? 'sel' : ''} ${was ? 'changed' : ''} ${role.enabled ? '' : 'off'}"
      draggable="true" data-ac="chip" data-name="${esc(name)}" aria-pressed="${sel}"
      title="${esc(name)}: ${esc(res.model || 'no model')}${res.effort ? ' · ' + esc(res.effort) : ''}${role.enabled ? '' : ' (switched off)'}">
      ${esc(name)}${want.pin ? `<span class="ac-pin">pinned · ${esc(want.pin.model)}${
        want.pin.reasoning_effort ? ' · ' + esc(want.pin.reasoning_effort) : ''}</span>` : ''}${
        role.team === 'neither' && want.side ? '<span class="ac-moved">moved here</span>' : ''}${
        was ? `<span class="ac-was">was ${esc(was)}</span>` : ''}
    </button>`;
}

function acBoard() {
  const d = state.ac.draft;
  const names = state.ac.data.roles.map((r) => r.name);
  let html = '<div class="ac-corner"></div>' + AC_SIDES.map(([k, name]) => {
    const route = acRouteOfSide(k);
    const follows = k === 'neither' ? (d.staffing.neither === 'build' ? ' · follows blue'
      : d.staffing.neither === 'check' ? ' · follows red' : '') : '';
    return `<div class="ac-colh ${k}"><span class="ac-ct">${name}</span>
      <span class="ac-cs">${esc(acRouteLabel(route))}${follows}</span></div>`;
  }).join('');
  AC_LEVELS.forEach(([lv, name, what]) => {
    html += `<div class="ac-rowh"><span class="ac-ln">${name}</span><span class="ac-ld">${what}</span></div>`;
    AC_SIDES.forEach(([k, sname]) => {
      const route = acRouteOfSide(k);
      const choice = (d.levels[route] || {})[lv] || {};
      const levelWas = acLevelWas(route, lv);
      const here = names.filter((n) => acSideOf(n) === k && d.roles[n].level === lv);
      html += `<div class="ac-cell" data-ac-side="${k}" data-ac-level="${lv}" data-label="${sname} · ${name}">
        <div class="ac-res ${k} ${levelWas ? 'changed' : ''}"><b>${esc(choice.model || 'no model')}</b>${
          choice.reasoning_effort ? ` · ${esc(choice.reasoning_effort)}` : ''}${
          levelWas ? `<span class="ac-was">was ${esc(levelWas)}</span>` : ''}</div>
        <div class="ac-chips">${here.length ? here.map(acChip).join('') : '<span class="ac-empty">nobody</span>'}</div>
      </div>`;
    });
  });
  return `<div class="ac-board">${html}</div>`;
}

function acSelbar() {
  const sel = state.ac.sel;
  if (!sel.length) return '';
  const movable = sel.every((n) => acRole(n).team === 'neither');
  const pinned = sel.some((n) => state.ac.draft.roles[n].pin);
  const route = sel.length === 1 ? acRouteOfSide(acSideOf(sel[0])) : '';
  return `<div class="ac-selbar">
    <span class="ac-count-sel">${sel.length} selected</span>
    <span class="ac-sl">level</span>
    <span class="ac-seg">${AC_LEVELS.map(([lv, name]) =>
      `<button type="button" data-ac="bulk-level" data-level="${lv}">${name}</button>`).join('')}</span>
    <span class="ac-sl">side</span>
    <span class="ac-seg">${AC_SIDES.map(([k, name]) => `<button type="button" data-ac="bulk-side"
        data-side="${k}" ${movable ? '' : 'disabled'}
        title="${movable ? '' : 'A builder stays a builder and a checker a checker. Only agents on neither side move.'}">${name}</button>`).join('')}</span>
    ${route ? `<span class="ac-sl">pin to</span>
      <select id="ac-pin" aria-label="Pin to a model">
        <option value="">— model —</option>
        ${(acRoute(route).models || []).map((m) => `<option>${esc(m)}</option>`).join('')}
      </select>` : ''}
    ${pinned ? '<button type="button" class="btn btn-sm" data-ac="unpin">Unpin</button>' : ''}
    <span class="ac-grow"></span>
    <button type="button" class="btn btn-sm" data-ac="clear">Clear</button>
  </div>`;
}

function acChanges() {
  const rows = [];
  state.ac.data.roles.forEach((r) => {
    const now = acResolve(r.name);
    const was = [r.route, r.model, r.reasoning_effort || ''];
    if (was[0] === now.route && was[1] === now.model && was[2] === now.effort) return;
    const why = was[0] !== now.route ? `now on ${acRouteLabel(now.route)}`
      : was[1] !== now.model ? `${state.ac.draft.roles[r.name].level} level` : 'the level\'s effort';
    rows.push(`<li><span class="mono">${esc(r.name)}</span><span>
      <span class="ac-from">${esc(r.model)}${was[2] ? ' · ' + esc(was[2]) : ''}</span>
      <span class="ac-arrow">→</span>
      <span class="ac-to">${esc(now.model || 'no model')}${now.effort ? ' · ' + esc(now.effort) : ''}</span>
      <span class="ac-why">${esc(why)}</span></span></li>`);
  });
  const dirty = acDirty();
  const { gaps, off } = acGaps();
  const blocked = gaps.length > 0 || off.length > 0;
  const sum = rows.length
    ? `<b>${rows.length}</b> of ${state.ac.data.roles.length} agents change model or effort.`
    : dirty ? 'No agent changes what it runs, but where they sit does.'
      : 'Nothing to write. This is what runs now.';
  return `<div class="ac-changes">
    <div class="ac-changes-head">${rows.length || dirty ? '<span class="ac-dot warn"></span>' : ''}
      <span>${sum}</span><span class="ac-grow"></span>
      <button type="button" class="btn btn-quiet btn-sm" data-ac="discard" ${acDirty() ? '' : 'disabled'}>Discard</button>
      <button type="button" class="btn btn-primary btn-sm" data-ac="save" ${dirty && !blocked ? '' : 'disabled'}
        title="${blocked ? 'Every agent needs a model on a route that is switched on first.' : ''}">Write to factory.yaml</button>
    </div>
    ${rows.length ? `<ul class="ac-diff">${rows.join('')}</ul>` : ''}
    <p class="ac-foot small dim">Writes to <span class="mono">${esc(state.ac.data.config_path)}</span>.
      The comments in it are kept, and an agent still opens on its own from the crew page.</p>
  </div>`;
}

function agentConfigScreen() {
  if (!state.ac) return '<div class="wrap"><p class="empty">Loading…</p></div>';
  const fresh = !state.ac.data.staffing;
  return `<div class="wrap ac">
    <a class="ac-crumb" href="${ROLES_HREF}">← The Crew</a>
    <h1 class="crew-title"><span>Agent configuration.</span></h1>
    <p class="crew-sub">Every agent's model follows from two choices: which provider serves each side,
      and how hard each agent has to think. Change a side or a level and every agent on it moves
      with it; pin an agent to hold it to one model regardless.${fresh ? ` Nothing here has been
      chosen yet, so this starts from exactly what each agent runs now.` : ''}
      <a href="#" data-help-open="agent-configuration">How this works</a></p>

    <section class="section">
      <p class="eyebrow">1 · Sides · who serves each team</p>
      <div class="ac-sides">${acSides()}</div>
      <ul class="ac-checks">${acChecks().map(([kind, text]) =>
        `<li><span class="ac-dot ${kind}"></span><span>${text}</span></li>`).join('')}</ul>
    </section>

    <section class="section">
      <p class="eyebrow">2 · Levels · what each level means on each provider</p>
      ${acLevelsTable()}
    </section>

    <section class="section">
      <p class="eyebrow">3 · Who sits where · drag an agent to change its level, or select several</p>
      <div class="ac-toolbar">
        <span class="ac-sl">start from</span>
        <span class="ac-seg">${[['current', 'Current'], ['suggested', 'Sensible defaults'], ['lean', 'Lean'],
          ['deep', 'Everything deep']]
          .map(([k, l]) => `<button type="button" data-ac="preset" data-preset="${k}"
            aria-pressed="${k === 'current' ? !acDirty() : state.ac.preset === k}">${l}</button>`).join('')}</span>
        ${state.ac.preset ? '<span class="ac-sl ac-note">levels, pins and moves replaced · sides kept</span>' : ''}
        <span class="ac-grow"></span>
        <button type="button" class="btn btn-quiet btn-sm" data-ac="shift" data-by="-1">All down a level</button>
        <button type="button" class="btn btn-quiet btn-sm" data-ac="shift" data-by="1">All up a level</button>
      </div>
      ${acBoard()}
      <div class="dia-key ac-key">
        <span><i class="k-build"></i>builds (blue team)</span>
        <span><i class="k-check"></i>checks (red team)</span>
        <span><i class="k-agent"></i>neither — can be moved onto a side</span>
        <span><i class="ac-k-changed"></i>runs differently once written</span>
      </div>
      ${acSelbar()}
    </section>

    <section class="section">
      <p class="eyebrow">4 · What changes</p>
      ${acChanges()}
    </section>
  </div>`;
}

/* ------------------------------------------------------------------ acting */

function acMove(names, side, level) {
  names.forEach((n) => {
    const want = state.ac.draft.roles[n];
    if (level) want.level = level;
    if (side && acRole(n).team === 'neither') want.side = side === 'neither' ? '' : side;
  });
  state.ac.preset = null;
}

/* A starting point replaces what is under it: every agent's level, what each
   level means, every pin and every agent moved onto a side. Only the sides
   stay -- who serves the blue team is a decision of its own, made above, and
   no preset knows better. `current` is what factory.yaml says, sides and all. */
function acPreset(kind) {
  const ac = state.ac;
  if (kind === 'current') {
    ac.draft = JSON.parse(ac.saved);
    ac.preset = null;
    return;
  }
  const suggested = ac.data.suggested;
  const down = { deep: 'standard', standard: 'light', light: 'light' };
  Object.entries(ac.draft.roles).forEach(([n, want]) => {
    const base = suggested[n] || 'standard';
    want.level = kind === 'deep' ? 'deep' : kind === 'lean' && !AC_KEEP_DEEP.has(n) ? down[base] : base;
    want.pin = null;
    want.side = '';
  });
  Object.entries(ac.data.presets).forEach(([route, body]) => {
    const row = { ...(ac.draft.levels[route] || {}) };
    AC_LEVELS.forEach(([lv]) => {
      if (body[lv]) row[lv] = { model: body[lv].model, reasoning_effort: body[lv].reasoning_effort || '' };
    });
    ac.draft.levels[route] = row;
  });
  ac.preset = kind;
}

/* What is written now, to mark everything a write would change against it. */
let acSavedCache = { text: null, draft: null };
function acSaved() {
  if (acSavedCache.text !== state.ac.saved) {
    acSavedCache = { text: state.ac.saved, draft: JSON.parse(state.ac.saved) };
  }
  return acSavedCache.draft;
}

const acRun = (model, effort) => `${model || 'no model'}${effort ? ' · ' + effort : ''}`;

/* What this agent runs now, if a write would change it; empty otherwise. */
function acWas(name) {
  const r = acRole(name);
  const now = acResolve(name);
  const same = r.route === now.route && r.model === now.model
    && (r.reasoning_effort || '') === (now.effort || '');
  return same ? '' : `${r.route !== now.route ? acRouteLabel(r.route) + ' ' : ''}${acRun(r.model, r.reasoning_effort)}`;
}

/* What a level meant on a route when it was last written, if it means
   something else in the draft. */
function acLevelWas(route, lv) {
  const was = (acSaved().levels[route] || {})[lv];
  const cur = (state.ac.draft.levels[route] || {})[lv];
  if (!was || !cur) return '';
  return was.model === cur.model && (was.reasoning_effort || '') === (cur.reasoning_effort || '')
    ? '' : acRun(was.model, was.reasoning_effort);
}

function saveAgentConfig() {
  return withBusy('Writing the crew to factory.yaml.', async () => {
    const d = state.ac.draft;
    const keep = new Set([...acRoutesInUse(), ...Object.keys(state.ac.data.levels)]);
    const levels = {};
    keep.forEach((r) => { if (d.levels[r]) levels[r] = d.levels[r]; });
    const roles = {};
    Object.entries(d.roles).forEach(([n, want]) => {
      roles[n] = { level: want.level, side: want.side || '', pin: want.pin };
    });
    const data = await api('/api/staffing', {
      method: 'PUT', body: JSON.stringify({ staffing: d.staffing, levels, roles }),
    });
    const draft = acDraftFrom(data);
    Object.assign(state.ac, { data, draft, saved: JSON.stringify(draft), sel: [], preset: null });
    // The crew page reads the roles; it is stale now.
    state.roles = null;
    render();
    toast('Written to factory.yaml.');
  });
}

/* Every press on this screen. Called from the one delegated click handler. */
function agentConfigClick(target, event) {
  const ac = state.ac;
  const d = ac.draft;
  const t = target.dataset;
  // Anything but choosing a starting point, or looking, departs from it.
  if (!['preset', 'chip', 'clear', 'more', 'save', 'discard'].includes(t.ac)) ac.preset = null;
  switch (t.ac) {
    case 'side': d.staffing[t.side] = t.route; break;
    case 'neither': d.staffing.neither = t.value; break;
    case 'reset': {
      const p = ac.data.presets[t.route][t.level];
      d.levels[t.route] = { ...(d.levels[t.route] || {}),
        [t.level]: { model: p.model, reasoning_effort: p.reasoning_effort || '' } };
      break;
    }
    case 'more': ac.showAll = !ac.showAll; break;
    case 'preset': acPreset(t.preset); ac.sel = []; break;
    case 'shift': {
      const order = AC_LEVELS.map(([lv]) => lv);
      const by = Number(t.by);
      Object.values(d.roles).forEach((want) => {
        want.level = order[Math.max(0, Math.min(2, order.indexOf(want.level) - by))];
      });
      ac.preset = null;
      break;
    }
    case 'chip': {
      const n = t.name;
      const multi = event.shiftKey || event.metaKey || event.ctrlKey || ac.sel.length;
      ac.sel = ac.sel.includes(n) ? ac.sel.filter((x) => x !== n)
        : multi ? [...ac.sel, n] : [n];
      break;
    }
    case 'bulk-level': acMove(ac.sel, null, t.level); break;
    case 'bulk-side': acMove(ac.sel, t.side, null); break;
    case 'unpin': ac.sel.forEach((n) => { d.roles[n].pin = null; }); break;
    case 'clear': ac.sel = []; break;
    case 'discard': {
      ac.draft = JSON.parse(ac.saved);
      ac.sel = [];
      ac.preset = null;
      break;
    }
    case 'save': return saveAgentConfig();
    default: return undefined;
  }
  render();
  return undefined;
}

function agentConfigChange(target) {
  const ac = state.ac;
  if (target.dataset.acRoute) {
    const { acRoute: route, acLevel: level, acKey: key } = target.dataset;
    ac.preset = null;
    const row = ac.draft.levels[route] || (ac.draft.levels[route] = {});
    row[level] = { model: '', reasoning_effort: '', ...(row[level] || {}), [key]: target.value };
    render();
    return true;
  }
  if (target.id === 'ac-pin' && target.value) {
    ac.preset = null;
    ac.sel.forEach((n) => {
      const res = acResolve(n);
      ac.draft.roles[n].pin = { route: res.route, model: target.value, reasoning_effort: res.effort };
    });
    render();
    return true;
  }
  return false;
}

/* Drag: a chip carries the whole selection when it is part of one. A builder
   or a checker moves only up and down its own column; an agent on neither
   side can be moved onto a team. */
let acDragging = [];

function acAllowed(side) {
  return acDragging.every((n) => {
    const team = acRole(n).team;
    return team === 'neither' || team === side;
  });
}

function acDragStart(event) {
  const chip = event.target.closest && event.target.closest('.ac-chip');
  if (!chip || !state.ac) return;
  const n = chip.dataset.name;
  acDragging = state.ac.sel.includes(n) ? [...state.ac.sel] : [n];
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/plain', acDragging.join(','));
}

function acDragOver(event) {
  const cell = event.target.closest && event.target.closest('.ac-cell');
  main.querySelectorAll('.ac-cell.over, .ac-cell.nope').forEach((c) => {
    if (c !== cell) c.classList.remove('over', 'nope');
  });
  if (!cell || !acDragging.length) return;
  event.preventDefault();
  const ok = acAllowed(cell.dataset.acSide);
  cell.classList.toggle('over', ok);
  cell.classList.toggle('nope', !ok);
  event.dataTransfer.dropEffect = ok ? 'move' : 'none';
}

function acDrop(event) {
  const cell = event.target.closest && event.target.closest('.ac-cell');
  if (!cell || !acDragging.length) return;
  event.preventDefault();
  if (acAllowed(cell.dataset.acSide)) acMove(acDragging, cell.dataset.acSide, cell.dataset.acLevel);
  acDragging = [];
  render();
}

function acDragEnd() {
  acDragging = [];
  main.querySelectorAll('.ac-cell.over, .ac-cell.nope').forEach((c) => c.classList.remove('over', 'nope'));
}
