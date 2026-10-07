/* One file, and everything that bears on it.
 *
 * Where the work map's right-hand column goes, and the sibling of
 * `criterion.js`: that screen is one requirement with only the code serving
 * it, this one is one file with everything said about it.
 *
 * The diff is the page and the rest is a rail beside it. A reviewer reads code
 * top to bottom and asks "what was this written for" and "who objected to it"
 * *while* reading, so the context has to stay in view and the code has to have
 * the width. With the same material in one column, a four-hundred-line diff
 * would push every objection about it a screen and a half below the thing it
 * objects to.
 */

import { html, useEffect, useMemo, useState } from '../vendor/preact-htm.module.js';
import {
  Composer, FlagsHere, OUTCOME, Pill, SEVERITY_PILL, base, findingsWithRecords,
  isRestatement, outcomeOf, packetOf, request,
} from './shared.js';
import { critIc, fileIc } from './marks.js';

/* The first line the change touched, so an editor opens where the reading
   starts rather than at the top of an eight-hundred-line file. */
function firstChangedLine(text) {
  let n = 0;
  for (const line of String(text || '').split('\n')) {
    const at = /^@@ -\d+(?:,\d+)? \+(\d+)/.exec(line);
    if (at) n = +at[1];
    if (n && /^[+-]/.test(line) && !/^(\+\+\+|---)/.test(line)) return n;
  }
  return n || 1;
}

/* `git diff` is the format, parsed here rather than re-encoded as structure on
   the way out: a second representation is a second place to be wrong. */
function Diff({ text }) {
  const rows = [];
  let oldNo = 0;
  let newNo = 0;
  for (const line of String(text || '').split('\n')) {
    if (/^(diff --git|index |--- |\+\+\+ |new file|deleted file|similarity |rename )/.test(line)) {
      continue;
    }
    if (line.startsWith('@@')) {
      const at = /@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
      if (at) { oldNo = +at[1]; newNo = +at[2]; }
      rows.push({ hunk: line });
      continue;
    }
    const mark = line[0];
    if (mark === '\\') { rows.push({ hunk: line }); continue; }
    if (mark === '+') rows.push({ mark, n: newNo++, text: line });
    else if (mark === '-') rows.push({ mark, o: oldNo++, text: line });
    else rows.push({ mark: ' ', o: oldNo++, n: newNo++, text: line });
  }
  return html`
    <div class="diff-scroll"><div class="diff-rows">
      ${rows.map((r, i) => (r.hunk
        ? html`<div key=${i} class="dl-hunk">${r.hunk}</div>`
        : html`
          <div key=${i} class=${`dl ${r.mark === '+' ? 'dl-add' : r.mark === '-' ? 'dl-rem' : ''}`}>
            <span class="dl-no">${r.o ?? ''}</span>
            <span class="dl-no">${r.n ?? ''}</span>
            <span class="dl-txt">${r.text}</span>
          </div>`))}
    </div></div>`;
}

function Card({ title, count, open, sub, children }) {
  return html`
    <details class="fv-card" open=${open}>
      <summary>
        <span class="fv-card-t">${title}</span>
        <span class="mono fv-card-n">${count}</span>
      </summary>
      <div class="fv-card-b">
        ${sub && html`<p class="fv-card-sub">${sub}</p>`}
        ${children}
      </div>
    </details>`;
}

export function FileView({ data, path, hrefs, editor, flags, onFile, onRule, busy }) {
  const [diff, setDiff] = useState(null);
  const packet = packetOf(data);
  const trackedMat = (packet.materiality || []).find((m) => m.path === path);

  useEffect(() => {
    let alive = true;
    setDiff(null);
    request(`${base(data.state.project_id, data.state.feature_id)}`
            + `/diff?path=${encodeURIComponent(path)}`)
      .then((d) => alive && setDiff(d))
      .catch((e) => alive && setDiff({ problem: e.message }));
    return () => { alive = false; };
  }, [path]);

  /* Lines added/removed off the diff itself, for the one case materiality
     never measured because it never wrote a row: `mat.added`/`removed`
     below are otherwise the rapporteur's count, taken off the branch once
     for every file that got one (pipeline.py, `diffstat`). */
  const diffCounts = useMemo(() => {
    if (!diff || !diff.diff) return { added: 0, removed: 0 };
    let added = 0;
    let removed = 0;
    for (const line of diff.diff.split('\n')) {
      if (/^\+(?!\+\+)/.test(line)) added += 1;
      else if (/^-(?!--)/.test(line)) removed += 1;
    }
    return { added, removed };
  }, [diff]);

  /* Materiality tracks the implementation, not what checks it -- a blind
     test or a seam test lands on the branch and stays there without ever
     getting a row here (see pipeline.py's `written`). That is a reason to
     leave it out of the work map's count and its prev/next walk, not a
     reason to give it a different screen: everything below reads off `mat`,
     so a file with no materiality row gets a stand-in one and the same
     layout, rather than a second, thinner template to keep in sync. */
  const mat = trackedMat || {
    path,
    klass: 'test',
    reason: 'Not on the work map: a test file, not an implementation file, so materiality does '
      + 'not track it. What follows is read straight off the branch instead.',
    added: diffCounts.added,
    removed: diffCounts.removed,
  };

  /* Everything that names this path, joined here rather than asked for. */
  const qa = {};
  ((data.qa || {}).results || []).forEach((r) => { qa[r.criterion_id] = r.status; });
  const criteria = (packet.trace || [])
    .filter((t) => (t.implementing_files || []).includes(path))
    .map((t) => ({
      id: t.criterion_id,
      state: t.status === 'orphan_requirement' ? 'no_code'
        : t.status === 'untested' ? 'no_test'
        : ({ passed: 'verified', failed: 'failing', no_test: 'no_test' })[qa[t.criterion_id]]
          || 'unsettled',
      statement: t.statement,
    }));
  const decisions = (packet.decisions || []).filter((d) => (d.files || []).includes(path));
  const findings = findingsWithRecords(data)
    .filter((f) => !isRestatement(f) && (f.files || []).includes(path));

  /* The units that wrote this file, and only what they disclosed. A worker's
     account of its own unit is the closest thing to an author's note there is,
     and reading it beside the file is the only place it is cheap. */
  const units = [...new Set((data.workers || [])
    .filter((w) => (w.files || []).some((f) => f.path === path))
    .map((w) => w.unit_id))];
  const disclosures = (packet.disclosures || [])
    .filter((line) => units.some((u) => line.startsWith(`${u} -- `)));

  const ruled = {};
  (data.rulings || []).forEach((r) => { ruled[r.decision_id] = r; });

  /* Prev and next walk the files the map says need eyes. Which those are is
     the map's decision and it has already made it; deciding again here is a
     second place for the two to differ. */
  const needing = (packet.materiality || []).filter((m) => m.klass === 'novel').map((m) => m.path);
  const walk = needing.includes(path) ? needing : (packet.materiality || []).map((m) => m.path);
  const at = walk.indexOf(path);

  const href = editor && diff && diff.absolute
    ? editor.replace('{path}', diff.absolute).replace('{line}', firstChangedLine(diff.diff))
    : '';

  return html`
    <div>
      <section class="filebar">
        <div class="wrap filebar-in">
          ${fileIc(mat.klass)}
          <span class="fb-path mono">${path}</span>
          <${Pill} cls=${mat.klass === 'novel' ? 'pill-red' : ''}>${mat.klass}<//>
          <span class="mono fb-n">
            <span class="add">+${mat.added || 0}</span> <span class="rem">−${mat.removed || 0}</span>
          </span>
          <span class="fb-acts">
            ${at >= 0 && html`<span class="mono fb-step">
              ${at + 1} of ${walk.length}${needing.includes(path) ? ' that need eyes' : ''}
            </span>`}
            <a class=${`btn btn-sm ${at > 0 ? '' : 'is-off'}`}
               href=${at > 0 ? hrefs.file(walk[at - 1]) : '#'}
               title=${at > 0 ? walk[at - 1] : ''}>← prev</a>
            <a class=${`btn btn-sm ${at >= 0 && at < walk.length - 1 ? '' : 'is-off'}`}
               href=${at >= 0 && at < walk.length - 1 ? hrefs.file(walk[at + 1]) : '#'}
               title=${at >= 0 && at < walk.length - 1 ? walk[at + 1] : ''}>next →</a>
            ${href && html`<a class="btn btn-primary btn-sm" href=${href}>Open in your editor →</a>`}
          </span>
        </div>
        ${mat.reason && html`<p class="wrap fb-why">${mat.reason}</p>`}
      </section>

      <div class="wrap">
        <div class="fv-body">
          <div>
            <div class="diff">
              <div class="diff-bar">
                <span class="mono">${path}</span>
                ${!href && html`<span class="mono dim diff-note">${editor
                  ? 'the console is not on the factory’s machine, so an editor link would open '
                    + 'the wrong file'
                  : 'no editor is configured'}</span>`}
              </div>
              ${!diff
                ? html`<p class="diff-wait">Asking the repository…</p>`
                : diff.problem
                  ? html`<p class="diff-wait">${diff.problem}</p>`
                  : diff.diff
                    ? html`<${Diff} text=${diff.diff} />`
                    : html`<p class="diff-wait">This branch did not change this file.</p>`}
              ${diff && diff.diff && html`
                <div class="diff-foot">Read-only, and only the hunks. The whole file as it stands
                  is <span class="mono">git show ${(diff.head || '').slice(0, 12)}:${path}</span>
                </div>`}
            </div>

            <${FlagsHere} flags=${flags} anchor=${path} />
            <${Composer} onFile=${onFile} busy=${busy} anchor=${path}
              placeholder="What is wrong with this file, in your words." />
          </div>

          <aside class="fv-rail">
            <${Card} title="Written for" open
                     count=${`${criteria.length} criteri${criteria.length === 1 ? 'on' : 'a'}`}
                     sub="Every criterion a worker building this file was serving. Open one to
                          read it with its evidence.">
              ${criteria.length
                ? html`<div class="fv-chips">
                    ${criteria.map((c) => html`
                      <a key=${c.id} class="fv-chip" href=${hrefs.criterion(c.id)}
                         title=${c.statement}>${critIc(c.state)}${c.id}</a>`)}
                  </div>`
                : html`<p class="fv-card-sub">No criterion accounts for this file. ${trackedMat
                    ? html`It is on the work map under <b>built, but nothing asked for it</b>.`
                    : 'It is a test, not an implementation file, so nothing traces to it.'}</p>`}
            <//>

            <${Card} title="Objections naming it" open count=${findings.length}>
              ${findings.length
                ? findings.map((f) => {
                    const [label] = OUTCOME[outcomeOf(f)] || [outcomeOf(f)];
                    return html`
                      <div key=${f.id} class="fv-item">
                        <div class="fv-item-h">
                          <span class="mono fv-item-id">${f.id}</span>
                          <${Pill} cls=${SEVERITY_PILL[f.severity]}>${f.severity}<//>
                          <span class="mono fv-item-id">
                            ${f.category || 'finding'} · ${(f.rec || {}).role || 'unrecorded'} · ${label}
                          </span>
                          <span class="fv-item-t">${f.title}</span>
                        </div>
                        <p class="fv-item-w">${f.detail}</p>
                      </div>`;
                  })
                : html`<p class="fv-card-sub">Nothing was raised against this file.</p>`}
            <//>

            <${Card} title="Choices made here" count=${decisions.length}
                     sub="Logged by the worker at the moment it made one. Ruling is the only thing
                          on this screen nobody else can do for you.">
              ${decisions.length
                ? decisions.map((d) => html`
                    <div key=${d.id} class="fv-item">
                      <div class="fv-item-h">
                        <span class="mono fv-item-id">${d.id}</span>
                        <${Pill} cls=${['irreversible', 'hard'].includes(d.reversibility)
                          ? 'pill-red' : ''}>${d.reversibility}<//>
                        <${Pill} cls=${d.confidence < 0.5 ? 'pill-amber' : ''}>
                          ${Math.round((d.confidence ?? 0) * 100)}%<//>
                        ${ruled[d.id] && html`<${Pill} cls="pill-green">
                          ${ruled[d.id].ruling === 'accept' ? 'accepted' : 'sent back'}<//>`}
                        <span class="fv-item-t">${d.title}</span>
                      </div>
                      <p class="fv-item-w">${d.rationale}</p>
                      ${d.blast_radius && html`
                        <p class="fv-item-w"><b>If it is wrong.</b> ${d.blast_radius}</p>`}
                      <div class="fv-item-acts">
                        <button class="btn btn-sm" disabled=${busy}
                                onClick=${() => onRule(d.id, 'accept')}>
                          ${ruled[d.id] ? 'Re-rule as accepted' : 'Accept'}
                        </button>
                        <button class="btn btn-sm" disabled=${busy}
                                onClick=${() => onRule(d.id, 'send_back')}>
                          ${ruled[d.id] ? 'Re-rule as sent back' : 'Send back'}
                        </button>
                      </div>
                    </div>`)
                : html`<p class="fv-card-sub">No worker logged a decision in this file.</p>`}
            <//>

            <${Card} title=${`What ${units.join(' and ') || 'the unit'} disclosed`}
                     count=${disclosures.length}
                     sub="Mandatory, and in its own words. Every review agent read these before
                          writing a finding, so anything here that mattered is already an
                          objection above.">
              ${disclosures.length
                ? disclosures.map((line, i) => html`
                    <p key=${i} class="fv-disc">${line.replace(/^\S+ -- /, '')}</p>`)
                : html`<p class="fv-card-sub">Nothing recorded for the unit that wrote this.</p>`}
            <//>
          </aside>
        </div>
      </div>
    </div>`;
}
