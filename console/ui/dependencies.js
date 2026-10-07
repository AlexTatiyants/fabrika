/* Every package this feature added or moved, read from its lockfiles.
 *
 * Facts computed by code, like Evidence beside it: the lockfile at the base
 * commit and now, then what OSV and each package's registry say about the new
 * versions, held to the project's policy. No model wrote any of it, and the
 * worker's own account of what it added is not in it.
 *
 * One row per direct dependency, with what it brought in folded under it --
 * the forty packages one line in a manifest can pull are the reason this is
 * read from the lockfile at all. Rows that need a ruling come first. "No
 * dependencies changed", "not checked" and "nothing could be looked up" are
 * different states and say so; none of them is a blank.
 */

import { html } from '../vendor/preact-htm.module.js';

const VERDICT_WORDS = {
  denied: 'license not allowed here',
  flagged: 'license flagged',
  'unknown-license': 'license unknown',
  'too-new': 'released in the last few days',
};

const versionOf = (c) => (c.after && c.after.length ? c.after[c.after.length - 1] : '');

/* What needs a person, in words, for one package. */
function troubles(c) {
  const out = [];
  if ((c.malicious || []).length) out.push('published to do harm');
  if ((c.advisories || []).length) out.push(`${c.advisories.length} known vulnerabilit${c.advisories.length === 1 ? 'y' : 'ies'}`);
  (c.verdicts || []).forEach((v) => { if (VERDICT_WORDS[v]) out.push(VERDICT_WORDS[v]); });
  if (!c.checked && !c.private && c.kind !== 'removed') out.push('not checked');
  return out;
}

/* For the tab strip: the count, and whether anything stands. */
export function dependencyTab(d) {
  const rec = d.dependencies;
  if (!rec) return { count: 'not read', amber: true, alert: false };
  const live = (rec.changes || []).filter((c) => c.kind !== 'removed');
  const direct = live.filter((c) => c.direct).length;
  const bad = live.filter((c) => (c.malicious || []).length || (c.advisories || []).length
    || (c.verdicts || []).includes('denied'));
  const unsure = live.filter((c) => troubles(c).length && !bad.includes(c));
  // The direct count only: the strip has no room for both, and what each one
  // brought in is folded under it on the tab. A change that moved nothing
  // direct still has a number.
  return {
    count: !live.length ? 'none' : direct ? `${direct} new` : `${live.length} changed`,
    alert: bad.length > 0,
    amber: unsure.length > 0,
  };
}

/* What OSV holds against one version, said in words before the ids: an id on
   its own reads as a reference, not as malware or a vulnerability, and does
   not say where the link goes. */
function Osv({ ids, words }) {
  if (!ids.length) return '';
  return html`<span class="dep-osv"><span class="dep-flag">${words}</span><span class="dim"> · OSV:</span>
    ${ids.map((id) => html` <a class="dep-adv mono" href=${`https://osv.dev/vulnerability/${encodeURIComponent(id)}`}
      target="_blank" rel="noopener" title=${`${id} on osv.dev`}>${id}</a>`)}</span>`;
}

function Row({ c, nested }) {
  const t = troubles(c);
  const advisories = c.advisories || [];
  const others = t.filter((w) => !/vulnerab|harm/.test(w));
  const from = c.before && c.before.length ? `${c.before.join(', ')} → ` : '';
  return html`
    <tr class=${`dep-row ${nested ? 'dep-nested' : ''} ${t.length ? 'dep-trouble' : ''}`}>
      <td class="dep-name">
        <span class="mono">${c.name}</span>
        <span class="dep-eco">${c.ecosystem}${c.private ? ' · private' : ''}</span>
      </td>
      <td class="mono">${from}${versionOf(c)}${c.kind !== 'added' ? html` <span class="dep-kind">${c.kind}</span>` : ''}</td>
      <td>${c.private ? html`<span class="dim">not looked up</span>` : (c.license || html`<span class="dim">—</span>`)}</td>
      <td class="mono">${(c.published || '').slice(0, 10) || html`<span class="dim">—</span>`}</td>
      <td>
        <${Osv} ids=${c.malicious || []} words="published to do harm" />
        <${Osv} ids=${advisories}
          words=${`${advisories.length} known vulnerabilit${advisories.length === 1 ? 'y' : 'ies'}`} />
        ${others.length ? html`<span class="dep-flag">${others.join(' · ')}</span>` : ''}
      </td>
      <td class="dim">${c.added_by || ''}</td>
    </tr>`;
}

export function Dependencies({ data, hrefs }) {
  const rec = data.dependencies;
  if (!rec) {
    return html`<div class="deps"><p class="deps-lede">Not read yet. The lockfiles are read after
      the first round of checks, and this page fills in then.</p></div>`;
  }
  const changes = rec.changes || [];
  const live = changes.filter((c) => c.kind !== 'removed');
  const removed = changes.filter((c) => c.kind === 'removed');
  if (!changes.length) {
    return html`<div class="deps"><p class="deps-lede">${(rec.lockfiles || []).length
      ? 'No dependencies changed. The lockfiles pin exactly what they did at the base commit.'
      : 'Not checked: this project has no lockfile, so what a feature adds cannot be read.'}
      ${(rec.unreadable || []).length ? html` ${rec.unreadable.join(', ')} could not be read.` : ''}</p></div>`;
  }
  const direct = live.filter((c) => c.direct);
  const under = (name) => live.filter((c) => !c.direct && c.via === name);
  const orphans = live.filter((c) => !c.direct && !direct.some((p) => p.name === c.via));
  const needs = live.filter((c) => troubles(c).length).length;
  const rank = (c) => (troubles(c).length || under(c.name).some((x) => troubles(x).length) ? 0 : 1);
  const ordered = [...direct].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  return html`
    <div class="deps">
      <p class="deps-lede">
        ${`This change adds ${direct.length} package${direct.length === 1 ? '' : 's'} and the `
          + `${live.length - direct.length} ${live.length - direct.length === 1 ? 'one it brings' : 'they bring'} in`}${needs
          ? html`; <b>${needs} need${needs === 1 ? 's' : ''} a ruling</b>, on <a href=${hrefs.calls()}>Your calls</a>.`
          : '. Nothing in them needs a ruling.'}
      </p>
      ${rec.policy_on === false ? html`<p class="deps-note">This project's dependency policy is
        off, so nothing here was looked up or held to it.</p>` : ''}
      <table class="deps-table">
        <thead><tr><th>Package</th><th>Version</th><th>License</th><th>Published</th>
          <th>What is known</th><th>Added by</th></tr></thead>
        <tbody>
          ${ordered.map((c) => html`
            <${Row} c=${c} />
            ${under(c.name).map((x) => html`<${Row} c=${x} nested=${true} />`)}`)}
          ${orphans.length ? html`
            <tr class="dep-group"><td colspan="6">Brought in by packages already here</td></tr>
            ${orphans.map((x) => html`<${Row} c=${x} nested=${true} />`)}` : ''}
        </tbody>
      </table>
      ${removed.length ? html`
        <p class="deps-removed">Removed: ${removed.map((c) => html`<span class="mono">${c.name}</span> `)}</p>` : ''}
    </div>`;
}
