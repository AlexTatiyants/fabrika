/* Where the evidence is missing.
 *
 * Every figure on this packet was computed as though everything that was meant
 * to run had run. Where that is not true, nothing else on the screen can say
 * so — the thing that failed is precisely the thing that cannot report its own
 * failure. An agent that never returned files no finding. A suite that died
 * before its probes reported names none, and read alone that zero says the
 * adversary attacked this and found nothing.
 *
 * Each entry names the figure it makes look better than it is, which is what
 * makes this a reconciliation rather than a list of complaints — and why the
 * figures in the band above carry a dagger into it.
 */

import { html } from '../vendor/preact-htm.module.js';
import { Composer, Row, blindSpots } from './shared.js';

export function BlindSpots({ data, focus, onFile, busy }) {
  const spots = blindSpots(data);
  const hard = spots.filter((s) => s.hard);

  if (!spots.length) {
    return html`
      <div class="wrap">
        <p class="tab-lead">Nothing is missing: every figure above means what it appears to
          mean.</p>
      </div>`;
  }

  return html`
    <div class="wrap">
      <p class="tab-lead">Where the figures above are worth less than they look, and why.</p>
      <section class="block">

        <div class="files">
          ${spots.map((s) => html`
            <${Row} key=${s.id} id=${s.id === focus ? '▸' : ''} open=${s.id === focus}
                    sev=${s.hard ? 'blocker' : 'major'}
                    pill=${s.hard ? 'nothing measured' : 'discount'}
                    pillCls=${s.hard ? 'pill-red' : 'pill-amber'}
                    title=${s.title}
                    meta=${html`makes <span class="drow-corr">${s.discounts}</span> look better`}>
              <p>${s.detail}</p>
              <div class="f-foot">
                <span>${s.what}</span>
                <span class="cav-disc">computed, not reported</span>
                <span>${s.source}</span>
              </div>
            <//>`)}
        </div>
      </section>

      <${Composer} onFile=${onFile} busy=${busy} source="comment" anchor="blind spots"
        placeholder="Something else this run did not measure, in your words." />
    </div>`;
}
