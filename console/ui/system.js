/* What this branch changed in how the system is put together.
 *
 * Computed by `factory/system_report.py` from two readings of the code -- at
 * the commit the branch started from and at its head -- with every difference
 * put down to the branch or to the second reading. Groups and capabilities are
 * names a model gives the code each time it reads it, so a second reading can
 * merge two groups or rename a capability on code nobody touched. Shown as one
 * list beside the work map, that read as a feature that reorganised the system
 * when it added three routes. So the branch's changes lead, and what moved
 * only because the code was read again is folded away below them, labelled.
 */

import { html } from '../vendor/preact-htm.module.js';

const reportOf = (d) => ((d.system_change || {}).report) || null;

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/* For the tab strip: what the branch added, in the fewest words. */
export function systemTab(d) {
  const change = d.system_change;
  if (!change) return { count: 'not read', amber: false, alert: false };
  const r = reportOf(d);
  if (!r) return { count: `${(change.lines || []).length} changes`, amber: false, alert: false };
  const routes = (r.ways_in_added || []).filter((w) => !w.test).length;
  const caps = (r.capabilities_added || []).length;
  const worse = (r.health || []).some((h) => h.direction === 'worse' || h.direction === 'new');
  const count = caps ? `+${plural(caps, 'capability', 'capabilities')}`
    : routes ? `+${plural(routes, 'way in', 'ways in')}`
    : (r.files_added || []).length || (r.files_changed || []).length ? 'no new parts'
    : 'no change';
  return { count, amber: worse, alert: false };
}

const STATUS = { added: ['+', 'sys-f-add', 'added by this branch'],
                 changed: ['~', 'sys-f-chg', 'changed by this branch'],
                 same: ['·', 'sys-f-same', 'untouched'] };

const label = (name, code) => (code ? `${name} (${code})` : name);

function Adds({ r }) {
  const product = (r.ways_in_added || []).filter((w) => !w.test);
  const stored = (r.entities_added || []).filter((e) => !e.test);
  const packages = r.packages_added;
  return html`
    <div class="sys-adds">
      <div class="sys-add">
        <div class="sys-add-n">${(r.capabilities_added || []).length}</div>
        <div class="sys-add-l">${(r.capabilities_added || []).length === 1 ? 'new capability' : 'new capabilities'}</div>
        <ul>${(r.capabilities_added || []).map((c) => html`
          <li key=${c.name}><b>${c.name}</b>${c.summary ? html` — ${c.summary}` : ''}</li>`)}</ul>
        ${(r.capabilities_removed || []).length > 0 && html`
          <ul>${r.capabilities_removed.map((c) => html`<li key=${c.name}>gone: <b>${c.name}</b></li>`)}</ul>`}
      </div>
      <div class="sys-add">
        <div class="sys-add-n">${product.length}</div>
        <div class="sys-add-l">${product.length === 1 ? 'new way in' : 'new ways in'}</div>
        <ul>${product.map((w) => html`
          <li key=${w.id}><code>${w.kind === 'route' ? `${w.method} ${w.name}`.trim() : w.name}</code></li>`)}</ul>
        ${(r.ways_in_removed || []).length > 0 && html`
          <ul>${r.ways_in_removed.map((w) => html`<li key=${w.id}>removed: <code>${w.name}</code></li>`)}</ul>`}
      </div>
      <div class="sys-add">
        <div class="sys-add-n">${stored.length}</div>
        <div class="sys-add-l">${stored.length === 1 ? 'new data entity' : 'new data entities'}</div>
        <ul>${stored.map((e) => html`<li key=${e.name}><code>${e.name}</code>
          <span class="sys-dim">${e.file}</span></li>`)}</ul>
      </div>
      <div class="sys-add">
        <div class="sys-add-n">${packages === null || packages === undefined ? '—' : packages.length}</div>
        <div class="sys-add-l">${(packages || []).length === 1 ? 'new package' : 'new packages'}</div>
        ${packages === null || packages === undefined
          ? html`<div class="sys-add-note">The lockfiles were not read for this run.</div>`
          : packages.length
            ? html`<ul>${packages.map((p) => html`<li key=${p}><code>${p}</code></li>`)}</ul>`
            : html`<div class="sys-add-ok">The lockfiles changed nothing — the same answer as Dependencies.</div>`}
      </div>
    </div>`;
}

function Groups({ r }) {
  const touched = r.groups.filter((g) => g.touched).length;
  return html`
    <h4 class="sys-h">Where the new code landed</h4>
    <p class="sys-sub">The system's groups as read at the head, each file marked by what this branch did to it.
      ${' '}${plural((r.files_added || []).length, 'file')} added and ${(r.files_changed || []).length} changed;
      ${' '}${plural(touched, 'group')} written into, highlighted.</p>
    <div class="sys-groups">
      ${r.groups.map((g) => html`
        <div key=${g.key} class=${`sys-g ${g.touched ? 'sys-g-hit' : ''}`}>
          <div class="sys-g-hd">
            <div class="sys-g-code">${g.code}</div>
            <div class="sys-g-name">${g.name || g.key}</div>
            <div class="sys-g-tally">
              ${g.files.filter((f) => f.status === 'added').length} new ·
              ${' '}${g.files.filter((f) => f.status === 'changed').length} changed ·
              ${' '}${g.files.filter((f) => f.status === 'same').length} untouched</div>
          </div>
          <ul>${g.files.map((f) => html`
            <li key=${f.path} class=${`sys-f ${STATUS[f.status][1]}`} title=${`${f.path} — ${STATUS[f.status][2]}`}>
              <i aria-hidden="true">${STATUS[f.status][0]}</i>${f.path}</li>`)}</ul>
        </div>`)}
    </div>
    <div class="sys-legend">
      ${Object.values(STATUS).map(([mark, cls, words]) => html`
        <span key=${cls}><b class=${cls}>${mark}</b>${words}</span>`)}
    </div>
    ${((r.loose || []).length > 0 || r.tests_touched > 0) && html`
      <p class="sys-loose">
        ${(r.loose || []).length > 0 && html`Outside any group: ${r.loose.map((f, i) => html`
          ${i ? ', ' : ''}<code>${f.path}</code> (${f.status})`)}.${' '}`}
        ${r.tests_touched > 0 && `Plus ${plural(r.tests_touched, 'test file')}.`}
      </p>`}`;
}

const DIRECTION = { better: 'pill-green', worse: 'pill-amber', new: 'pill-amber', gone: 'pill-green', same: '' };

function Health({ rows }) {
  const moved = rows.filter((h) => h.direction !== 'same');
  const same = rows.filter((h) => h.direction === 'same');
  if (!rows.length) return null;
  return html`
    <h4 class="sys-h">What it did to the system's health</h4>
    <div class="sys-health">
      ${moved.map((h) => html`
        <div key=${h.kind + h.title} class="sys-hr">
          <span class=${`pill ${DIRECTION[h.direction]}`}>${h.direction}</span>
          <div><div class="sys-hr-t">${h.title}</div>${h.detail && html`<div class="sys-hr-d">${h.detail}</div>`}</div>
          <div class="sys-hr-ba">
            ${h.before && h.after
              ? html`<s>${h.before}</s> → ${h.after}`
              : h.after || h.before}
          </div>
        </div>`)}
      ${same.length > 0 && html`
        <div class="sys-hr sys-hr-quiet">
          <span class="pill">unchanged</span>
          <div><div class="sys-hr-t">${same.map((h) => h.title).join(' · ')}</div></div>
          <div class="sys-hr-ba">same as before</div>
        </div>`}
    </div>`;
}

function Ways({ r }) {
  const product = (r.ways_in_added || []).filter((w) => !w.test);
  const tests = (r.ways_in_added || []).filter((w) => w.test);
  if (!product.length && !tests.length) return null;
  return html`
    <h4 class="sys-h">New ways in</h4>
    <div class="sys-ways">
      <div class="sys-box">
        <h5>For people using it</h5>
        ${product.length
          ? product.map((w) => html`
              <div key=${w.id} class="sys-route">
                <b>${w.kind === 'route' ? w.method : w.kind}</b>${w.name}
                <span class="sys-dim">${w.file}</span></div>`)
          : html`<p>None: nothing a person uses gained a way in.</p>`}
      </div>
      <div class="sys-box">
        <h5>For tests only</h5>
        ${tests.length
          ? html`<div class="sys-chips">${tests.map((w) => html`<code key=${w.id}>${w.name}</code>`)}</div>`
          : html`<p>None.</p>`}
      </div>
    </div>`;
}

function NotThisBranch({ r }) {
  const regrouped = r.groups.filter((g) => g.lineage === 'renamed' || g.lineage === 'merged');
  const recounted = [...(r.ways_in_recounted || []), ...(r.ways_in_uncounted || [])];
  const n = (r.moves || []).length + regrouped.length + recounted.length
    + (r.entities_recounted || []).length + (r.outside_named || []).length;
  const renamedCaps = (r.capabilities_before || []).length > 0;
  if (!n && !renamedCaps) return null;
  const tally = [
    regrouped.length && plural(regrouped.length, 'group renamed or merged', 'groups renamed or merged'),
    (r.moves || []).length && plural(r.moves.length, 'file moved', 'files moved'),
    recounted.length && plural(recounted.length, 'way in recounted', 'ways in recounted'),
    (r.outside_named || []).length && plural(r.outside_named.length, 'outside system named', 'outside systems named'),
  ].filter(Boolean).join(' · ');
  return html`
    <details class="sys-noise">
      <summary>
        <span class="pill">not this branch</span>
        <span class="sys-noise-t">What moved because the code was read again, not because this branch changed it</span>
        <span class="sys-noise-m">${tally}</span>
      </summary>
      <div class="sys-noise-b">
        <p>Groups, capabilities and the outside systems the code talks to are names a model gives the code each
          time it reads it. The second reading named some things differently. None of what follows is a change
          the branch made: each was checked against the code itself at both commits.</p>
        ${regrouped.length > 0 && html`
          <table class="sys-moves">
            <thead><tr><th>Group at the head</th><th>Was</th><th>What happened</th></tr></thead>
            <tbody>${regrouped.map((g) => html`
              <tr key=${g.key}><td>${label(g.name || g.key, g.code)}</td><td>${g.was.join(' + ')}</td>
                <td>${g.lineage === 'merged' ? 'merged into one group' : 'renamed'}</td></tr>`)}</tbody>
          </table>`}
        ${(r.moves || []).length > 0 && html`
          <table class="sys-moves">
            <thead><tr><th>File</th><th>Group before</th><th>Group after</th><th>Touched by this branch</th></tr></thead>
            <tbody>${r.moves.map((m) => html`
              <tr key=${m.path}><td class="f">${m.path}</td><td>${m.before}</td><td>${m.after}</td>
                <td class=${m.touched ? 'y' : 'n'}>${m.touched ? 'changed' : 'no'}</td></tr>`)}</tbody>
          </table>`}
        <div class="sys-two">
          ${renamedCaps && html`
            <div class="sys-box">
              <h5>Capabilities, as each reading named them</h5>
              <p>The new ones are above. These are the rest, named once at each commit.</p>
              <div class="sys-cols">
                <div><div class="sys-col-h">Where it started</div>
                  <ul>${r.capabilities_before.map((c) => html`<li key=${c}>${c}</li>`)}</ul></div>
                <div><div class="sys-col-h">At the head</div>
                  <ul>${r.capabilities_after.map((c) => html`<li key=${c}>${c}</li>`)}</ul></div>
              </div>
            </div>`}
          ${recounted.length > 0 && html`
            <div class="sys-box">
              <h5>Ways in counted differently</h5>
              <p>Each is defined in its file at both commits: only the reading's count of it moved.</p>
              <div class="sys-chips">${recounted.map((id) => html`<code key=${id}>${id.replace(/^[a-z]+:/, '')}</code>`)}</div>
            </div>`}
          ${(r.outside_named || []).length > 0 && html`
            <div class="sys-box">
              <h5>Outside systems first named at the head</h5>
              <p>${r.packages_added === null || r.packages_added === undefined
                ? 'The lockfiles were not read, so none of these could be checked against them.'
                : 'The lockfiles show none of these was added by this branch.'}</p>
              <div class="sys-chips">${r.outside_named.map((o) => html`<code key=${o}>${o}</code>`)}</div>
            </div>`}
          ${(r.entities_recounted || []).length > 0 && html`
            <div class="sys-box">
              <h5>Entities counted for the first time</h5>
              <p>Each was already defined in its file at the base.</p>
              <div class="sys-chips">${r.entities_recounted.map((e) => html`<code key=${e}>${e}</code>`)}</div>
            </div>`}
        </div>
      </div>
    </details>`;
}

export function System({ data }) {
  const change = data.system_change;
  const project = (data.state || {}).project_id;
  const asBuilt = project ? `#/${encodeURIComponent(project)}?gates=as-built` : null;
  if (!change) {
    return html`
      <div class="wrap">
        <p class="tab-lead">Not read yet. The code is read where the branch started and again at its head, and
          the two readings are compared; this run has not got both.</p>
      </div>`;
  }
  const r = reportOf(data);
  if (!r) {
    /* The readings this was compared from are gone from the ledger, so only
       the bare comparison is left -- said as that, not as an attribution. */
    return html`
      <div class="wrap">
        <p class="tab-lead">Every difference between the two readings. It could not be split into what this
          branch caused and what moved because the code was read again.</p>
        <ul class="sys-bare">${(change.lines || []).map((l) => html`<li key=${l}>${l}</li>`)}</ul>
      </div>`;
  }
  return html`
    <div class="wrap">
      <p class="tab-lead">What this branch changes about how the system is put together. The code was read where
        the branch started and again at its head, and the two readings compared. What the branch caused comes
        first; what moved only because the code was read again is folded away at the bottom.</p>

      <h4 class="sys-h">What it adds</h4>
      <${Adds} r=${r} />
      <${Groups} r=${r} />
      <${Health} rows=${r.health || []} />
      <${Ways} r=${r} />
      <${NotThisBranch} r=${r} />

      <div class="sys-foot">
        <span>base ${(r.base || '').slice(0, 7)}</span>
        <span>head ${(r.head || '').slice(0, 7)}</span>
        ${asBuilt && html`<a href=${asBuilt}>The as-built of this project →</a>`}
      </div>
    </div>`;
}
