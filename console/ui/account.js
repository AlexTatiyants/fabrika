/* What a unit said about its own work, and the choices it logged while doing
 * it -- the two things in a packet that are an author's account rather than a
 * reviewer's reading of one.
 *
 * Neither is a tab of its own. A build unit's account belongs beside the file
 * it wrote, on the file screen, and a repair round's belongs with the rest of
 * what that round did, on Repairs -- so the two live here and are drawn in
 * both of those places.
 */

import { html } from '../vendor/preact-htm.module.js';
import { Row } from './shared.js';

const KINDS = [
  { key: 'flag', title: 'Flagged for a reviewer', open: true,
    sub: 'The worker naming where it wants a person to look hard: security, data loss, scope '
       + 'widening, a shortcut it took.' },
  { key: 'deviation from spec', title: 'Did something other than the spec asked', open: false,
    sub: 'Where the work departs from what was frozen, and why.' },
  { key: 'not implemented', title: 'Parts it did not build', open: false,
    sub: 'Named specifically, by the unit that owned them.' },
  { key: 'silence', title: 'Disclosed nothing at all', open: true,
    sub: 'An empty disclosure is a claim, not a clean run: nobody builds a unit from an '
       + 'incomplete spec without assuming something. (INV-9)' },
  { key: 'assumption', title: 'Assumed, because the spec did not say', open: false,
    sub: 'Each one a thing that could be wrong, and worth as much as a criterion to whoever '
       + 'reads this after it turns out to have been.' },
];

const LABELS = ['not implemented', 'assumption', 'deviation from spec', 'flag'];

function parseDisclosure(line) {
  const at = line.indexOf(' -- ');
  if (at < 0) return { unit: '', kind: 'assumption', text: line };
  const unit = line.slice(0, at);
  const rest = line.slice(at + 4);
  for (const label of LABELS) {
    if (rest.startsWith(`${label}: `)) {
      return { unit, kind: label, text: rest.slice(label.length + 2) };
    }
  }
  // The one line `collect_disclosures` writes without a label, and the only
  // one that is about the absence of a disclosure rather than its content.
  return { unit, kind: 'silence', text: rest };
}

export function Disclosures({ lines, title, sub, id = '' }) {
  if (!lines.length) return null;
  const parsed = lines.map(parseDisclosure);
  const bands = KINDS
    .map((k) => ({ k, mine: parsed.filter((p) => p.kind === k.key) }))
    .filter(({ mine }) => mine.length);

  const flags = parsed.filter((p) => p.kind === 'flag').length;
  const silent = parsed.filter((p) => p.kind === 'silence').length;

  /* Shut, and that is the point.
   *
   * These are the panel's input, not the reader's work. Every review agent
   * reads all of them and raises a finding wherever one matters -- so a
   * disclosure that mattered is already in Objections under the agent that
   * objected to it, and the rest are what three independent readings looked
   * at and did not think worth objecting to. A packet can carry seventy-five
   * of them, and presenting those as a wall asks a human to do the panel's
   * job again.
   *
   * Kept in full and one click away, because the record only grows (INV-11)
   * and the panel is not infallible -- the point is where they sit, not
   * whether they are here. */
  return html`
    <section class="block" id=${id}>
      <details class="fg fg-quiet">
        <summary class="fg-head">
          <span class="fg-title">${title}</span>
          <span class="mono fg-count">
            ${lines.length}${flags ? ` · ${flags} flagged for a reviewer` : ''}${
              silent ? ` · ${silent} disclosed nothing` : ''}
          </span>
        </summary>
        <p class="fg-sub">${sub}</p>
        <div class="fg-rows fg-rows-pad">
      ${bands.map(({ k, mine }) => html`
        <details class="fg" key=${k.key} open=${k.open}>
          <summary class="fg-head">
            <span class="fg-title">${k.title}</span>
            <span class="mono fg-count">${mine.length}</span>
          </summary>
          <p class="fg-sub">${k.sub}</p>
          <div class="fg-rows">
            ${mine.map((d, i) => html`
              <div class="disc-row" key=${i}>
                <span class="mono disc-unit">${d.unit || '—'}</span>
                <p class="disc-text">${d.text}</p>
              </div>`)}
          </div>
        </details>`)}
        </div>
      </details>
    </section>`;
}

export function Decision({ d, ruling, open, onRule, busy }) {
  const conf = Math.round((d.confidence || 0) * 100);
  const irreversible = ['irreversible', 'hard'].includes(d.reversibility);
  const meta = html`
    <span class=${conf < 50 ? 'drow-corr' : ''}>${conf}% confident</span>
    ${(d.criterion_ids || []).length ? ` · serves ${d.criterion_ids.join(', ')}` : ''}
    ${(d.files || []).length ? ` · ${d.files.length} file${d.files.length === 1 ? '' : 's'}` : ''}`;

  return html`
    <${Row} id=${d.id} sev=${irreversible ? 'blocker' : conf < 50 ? 'major' : ''}
            pill=${d.reversibility} pillCls=${irreversible ? 'pill-red' : ''}
            pillTitle="How hard this is to undo once merged and in use."
            title=${d.title} meta=${meta} open=${open}>
      <dl class="d-dl">
        <dt>Rationale</dt><dd>${d.rationale}</dd>
        <dt>Rejected</dt>
        <dd>${(d.alternatives_rejected || []).length
          ? html`<ul>${d.alternatives_rejected.map((a, i) => html`<li key=${i}>${a}</li>`)}</ul>`
          : html`<span class="dim">Nothing. The worker considered no alternative, which is
              itself the claim.</span>`}</dd>
        ${d.blast_radius && html`<dt>If it is wrong</dt><dd>${d.blast_radius}</dd>`}
        ${(d.files || []).length
          ? html`<dt>In</dt><dd class="mono">${d.files.join('  ')}</dd>` : null}
        ${(d.tags || []).length ? html`<dt>Tags</dt><dd>${d.tags.join(', ')}</dd>` : null}
        ${ruling && html`<dt>Your ruling</dt>
          <dd>${ruling.ruling === 'accept' ? 'Accepted' : 'Sent back'}${
            ruling.note ? ` — ${ruling.note}` : ''}</dd>`}
      </dl>
      ${/* The ruling is made here, beside the choice: a screen that says
           "needs a ruling from you" and then sends you to another page to
           give one is not a worklist. A worklist you cannot work is a list. */''}
      <div class="d-rule">
        <button class="btn btn-sm" disabled=${busy}
                onClick=${() => onRule(d.id, 'accept')}>
          ${ruling ? 'Re-rule as accepted' : 'Accept'}
        </button>
        <button class="btn btn-sm" disabled=${busy}
                onClick=${() => onRule(d.id, 'send_back')}>
          ${ruling ? 'Re-rule as sent back' : 'Send back'}
        </button>
        <span class="d-rule-note">Accepting records that you saw this choice and let it
          stand. It changes no code.</span>
      </div>
    <//>`;
}
