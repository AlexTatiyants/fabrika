/* Screen 2 — inside one requirement.
 *
 * The ask is pinned at the top and stays there, because the question on this
 * screen is never "is this code good" but "does this code do what that says".
 *
 * Materiality is a filter here, not a label. The default view folds away
 * everything that is generated, mechanical or conventional and leaves the code
 * where a choice was made. That is the whole mechanism by which a large change
 * becomes reviewable: not a better diff, a smaller one.
 *
 * No diff is drawn. The file is shown as its author wrote it, and the header
 * carries the command that shows the real thing in a real editor -- which is
 * where anyone who genuinely wants a diff should be.
 */

import { html, useState } from '../vendor/preact-htm.module.js';
import {
  Composer, FlagsHere, MatDot, Pill, SEVERITY_PILL,
  criteriaRows, packetOf, statementOf,
} from './shared.js';

function Code({ contents }) {
  const lines = String(contents || '').split('\n');
  return html`
    <div class="cd-code">
      ${lines.map((line, i) => html`
        <div class="cd-line" key=${i}>
          <span class="mono cd-no">${i + 1}</span>
          <span class="mono cd-text">${line || ' '}</span>
        </div>`)}
    </div>`;
}

function File({ file, folded, flags, onFile, busy }) {
  const [open, setOpen] = useState(!folded);
  const hot = file.klass === 'novel';

  return html`
    <div class=${`cd-file ${hot ? 'cd-hot' : ''}`}>
      <button type="button" class="cd-head" onClick=${() => setOpen(!open)}
              aria-expanded=${open ? 'true' : 'false'}>
        <${MatDot} klass=${file.klass} />
        <span class="mono cd-path">${file.path}</span>
        <${Pill} cls=${hot ? 'pill-accent' : ''}>${file.klass}<//>
        <span class="cd-spacer" />
        <span class="cd-lines">${file.lines} lines</span>
        <span class="mono cd-chev" aria-hidden="true">${open ? '−' : '+'}</span>
      </button>

      ${!open && html`<p class="cd-reason">${file.reason}</p>`}

      ${open && html`
        <div>
          ${file.reason && html`<p class="cd-reason cd-reason-open">${file.reason}</p>`}
          ${file.contents
            ? html`<${Code} contents=${file.contents} />`
            : html`<p class="cd-reason">
                No source recorded for this file. It reached the branch from the integrator or a
                repairer, and the packet carries the classification but not the text.
              </p>`}
          <${FlagsHere} flags=${flags} anchor=${file.path} />
          <${Composer} compact onFile=${onFile} busy=${busy} anchor=${file.path}
            placeholder="What is wrong with this file, in your words." />
        </div>`}
    </div>`;
}

function Decisions({ decisions }) {
  if (!decisions.length) return null;
  return html`
    <div class="cd-side-block">
      <div class="eyebrow">The decisions behind it</div>
      ${decisions.map((d) => html`
        <div key=${d.id} class=${`cd-dec ${d.reversibility === 'irreversible' ? 'cd-dec-bad' : ''}`}>
          <div class="cd-dec-head">
            <span class="mono dim">${d.id}</span>
            <${Pill} cls=${d.reversibility === 'irreversible' ? 'pill-red' : ''}>
              ${d.reversibility}
            <//>
            <span class=${`mono cd-conf ${d.confidence < 0.5 ? 'cd-lowconf' : ''}`}>
              confidence ${Number(d.confidence).toFixed(2)}
            </span>
          </div>
          <div class="cd-dec-title">${d.title}</div>
          <p class="cd-dec-body">${d.rationale}</p>
          ${d.blast_radius && html`
            <div class="cd-dec-sub">
              <div class="eyebrow">Blast radius</div>
              <p>${d.blast_radius}</p>
            </div>`}
          ${(d.alternatives_rejected || []).length > 0 && html`
            <div class="cd-dec-sub">
              <div class="eyebrow">Considered and rejected</div>
              ${d.alternatives_rejected.map((a, i) => html`<p key=${i}>${a}</p>`)}
            </div>`}
        </div>`)}
    </div>`;
}

export function Criterion({ data, id, hrefs, flags, onFile, busy }) {
  const [showAll, setShowAll] = useState(false);
  const rows = criteriaRows(data);
  const row = rows.find((r) => r.criterion_id === id);
  const packet = packetOf(data);

  if (!row) {
    return html`<div class="wrap"><p class="empty">No criterion ${id} in this packet.</p></div>`;
  }

  const novel = row.files.filter((f) => f.klass === 'novel');
  const shown = showAll ? row.files : novel;
  const folded = row.files.filter((f) => f.klass !== 'novel');
  const foldedLines = folded.reduce((s, f) => s + (f.lines || 0), 0);
  const decisions = (packet.decisions || []).filter(
    (d) => (d.criterion_ids || []).includes(id));
  /* There is deliberately no gate-1 answer shown beside the criterion. A
     ResolvedAnswer records the question it answers, not the criteria the
     spec writer then wrote from it, so there is no link to follow -- and inventing
     one by matching text would be a guess presented as a citation. */

  return html`
    <div class="wrap wide cd">
      <div class="cd-cols">
        <div>
          <div class="cd-ask">
            <div class="cd-ask-head">
              <span class="mono">${id}</span>
              <span class="eyebrow">what you asked for</span>
            </div>
            <p class="cd-ask-text">${row.statement || statementOf(data, id)}</p>
          </div>

          <div class="cd-filter">
            <div class="cd-chips">
              <button class=${`cd-chip ${showAll ? '' : 'on'}`} onClick=${() => setShowAll(false)}>
                Needs review · ${novel.reduce((s, f) => s + (f.lines || 0), 0)} lines
              </button>
              <button class=${`cd-chip ${showAll ? 'on' : ''}`} onClick=${() => setShowAll(true)}>
                Everything · ${row.lines} lines
              </button>
            </div>
            <span class="cd-folded">
              ${showAll
                ? `${row.files.length} files · nothing folded`
                : `${foldedLines} lines folded away across ${folded.length} file${
                    folded.length === 1 ? '' : 's'}`}
            </span>
          </div>

          ${shown.length
            ? shown.map((f) => html`
                <${File} key=${f.path} file=${f} folded=${f.klass !== 'novel'}
                  flags=${flags} onFile=${onFile} busy=${busy} />`)
            : html`<p class="cd-none">
                Nothing novel here. Everything serving ${id} is generated, mechanical or
                conventional — switch to Everything if you want to read it anyway.
              </p>`}
        </div>

        <aside class="cd-side">
          <${Decisions} decisions=${decisions} />

          ${row.findings.length > 0 && html`
            <div class="cd-side-block">
              <div class="eyebrow">Findings against ${id}</div>
              ${row.findings.map((f) => html`
                <div key=${f.id} class="cd-finding">
                  <div class="cd-dec-head">
                    <${Pill} cls=${SEVERITY_PILL[f.severity]}>${f.id} ${f.severity}<//>
                  </div>
                  <div class="cd-dec-title">${f.title}</div>
                  <p class="cd-dec-body">${f.detail}</p>
                </div>`)}
            </div>`}

          <div class="cd-side-block">
            <div class="eyebrow">Evidence</div>
            <a class="cd-evlink" href=${hrefs.evidence(id)}>
              ${row.tests.length
                ? `${row.tests.length} blind test${row.tests.length === 1 ? '' : 's'}, and what they do not prove`
                : 'No blind test covers this — see why'} →
            </a>
          </div>

          <div class="cd-side-block">
            <div class="eyebrow">If you want the real diff</div>
            <p class="cd-side-note">
              This shows each file as its author wrote it, not what is on the branch after repairs.
              For the actual change, in your own editor:
            </p>
            <code class="cd-cmd">${data.diff_command || 'git diff'}</code>
          </div>
        </aside>
      </div>
    </div>`;
}
