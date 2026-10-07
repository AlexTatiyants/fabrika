/* Two families of mark, and nothing on a review wears both.
 *
 * A criterion carries a result -- it was tested, it failed, nothing looked --
 * and a file carries a material class. Drawn as the same small coloured
 * circles, they would need a key to say which column each belonged to and a
 * reader to remember it. The shape answers that before the colour is read,
 * which also means the distinction survives for a reader who does not have
 * the colour channel at all.
 *
 * Shared because they appear on the work map, on a file screen and in the
 * rail of each; three copies of a vocabulary is how the three come to
 * disagree about what a hollow ring means.
 */

import { html } from '../vendor/preact-htm.module.js';

/* ✓ passed · ✗ failed · ○ nothing looked. A hollow ring is the absence of a
   result, which is exactly what "untested" is -- and `unsettled` wears it too,
   because a run that cannot say either way has not produced one. */
export const CRIT_MARK = {
  verified: ['✓', 'mk-verified', 'a blind test covers it and passed'],
  failing: ['✗', 'mk-failing', 'a blind test covers it and failed'],
  unsettled: ['○', 'mk-untested', 'a test covers it and the run cannot say either way'],
  no_test: ['○', 'mk-untested', 'nothing independent covers it'],
  no_code: ['—', 'mk-failing', 'nothing implements it'],
};

export function critIc(state) {
  const [glyph, cls, why] = CRIT_MARK[state] || CRIT_MARK.unsettled;
  return html`<span class=${`mk ${cls}`} title=${why}>${glyph}</span>`;
}

/* Filled diamond, a real choice; hollow, a pattern that is already here. The
   four materiality classes still sort a column and still name themselves where
   they are counted -- what a reader needs from a single row is only which of
   the two kinds it is, and whether to spend a minute on it. */
export function fileIc(klass) {
  if (klass === 'novel') {
    return html`<span class="mk mk-novel" title="novel — needs human review">◆</span>`;
  }
  if (klass === 'test') {
    return html`<span class="mk mk-boiler" title="test — checks the implementation, is not it">◇</span>`;
  }
  return html`<span class="mk mk-boiler" title="boilerplate — can skip">◇</span>`;
}
