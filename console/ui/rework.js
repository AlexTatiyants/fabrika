/* Screen 4 — what you found, and where that sends it.
 *
 * The exit is computed, not chosen. Two dispositions, `repair` and
 * `dismissed`: one flag ruled repair is enough to send the whole review to
 * the repair loop, and nothing else changes where it goes. You supply the
 * judgement, one disposition per flag; plain code decides what it means --
 * including which agent a repair belongs to, which is arithmetic and not a
 * question to hand back. (INV-6, at the human boundary.)
 */

import { html } from '../vendor/preact-htm.module.js';
import {
  DISPOSITIONS, NO_NOTE, noteOf, tagOf, usd,
} from './shared.js';

const ROUTE = {
  reopen_spec: { title: 'Gate 1 reopens', cls: 'bad', cta: 'Reopen gate 1 with these notes' },
  repair: { title: 'Repair round details', cls: 'go', cta: 'Send to the repair loop' },
  accept: { title: 'Nothing left to fix', cls: 'ok', cta: 'Accept this feature' },
  /* Not a route: the calls that decide it are not all in. The route is
     computed from your flags, and with none filed it comes out as `accept` --
     "Nothing left to fix. Nothing flagged." -- even under a count of ten
     calls still open. */
  waiting: { title: 'Waiting on your calls', cls: 'wait', cta: 'Rule on every call first' },
};

/* The route's own sentence, except on the one route where the flags below say
   it better. `plan.because` for a repair round reads "3 flags routed to the
   repair loop", printed directly above a list of the same three flags: the
   list with the words taken out of it. Every other route keeps the server's
   sentence, because on those there is no list underneath saying it again. */
function lead(plan, waiting, going) {
  if (waiting) {
    return 'Not decided until every call is in: the route is computed from all of them together.';
  }
  if (plan.route === 'repair') {
    return `You\u2019ve decided to send ${going} issue${going === 1 ? '' : 's'} back `
      + 'with the following comments:';
  }
  return plan.because;
}

/* An anchor that names something on the packet, or nothing. `worklist` is the
   screen a thing was raised on, not an object anybody can go and read, so it
   is not printed as though it were one. */
const RAISED_HERE = new Set(['worklist']);

/* WHAT IS ACTUALLY BEING SENT. Your note is the only thing on this card the
   repair loop reads, so the card shows the note itself rather than a count of
   it -- and something filed from the composer, which answers to no finding at
   all, has nowhere else to appear between filing it and the build resuming.
 *
 * The tag stays a button. This is the last screen before dispatch and so the
 * last place you can change your mind, and the route recomputes under you
 * when you do.
 *
 * Dismissed flags sit under a rule of their own because the sentence above
 * counts only what is going, and without the rule it reads as though it
 * counted these too. */
function Filed({ flags, onRetag, busy, titleOf }) {
  if (!flags.length) return null;
  const going = flags.filter((f) => f.disposition === 'repair');
  const kept = flags.filter((f) => f.disposition !== 'repair');
  const row = (f) => {
    const tag = tagOf(f.disposition);
    const next = () =>
      DISPOSITIONS[(DISPOSITIONS.indexOf(f.disposition) + 1) % DISPOSITIONS.length];
    const named = f.anchor && !RAISED_HERE.has(f.anchor);
    return html`
      <div key=${f.id} class="rw-filed-row">
        <button class=${`pill rw-ftag ${tag.pill}`} type="button" disabled=${busy || !onRetag}
                title="Retag — the route recomputes"
                onClick=${() => onRetag && onRetag(f.id, next())}>${tag.label}</button>
        <p class=${`rw-said ${noteOf(f, titleOf) ? '' : 'rw-said-none'}`}>
          ${noteOf(f, titleOf) || NO_NOTE}
        </p>
        <span class=${`mono rw-on ${named ? '' : 'rw-on-mine'}`}>
          ${named ? `on ${f.anchor}` : 'yours \u00b7 raised here'}
        </span>
      </div>`;
  };
  return html`
    <div class="rw-filed">
      ${going.map(row)}
      ${kept.length > 0 && html`
        <div class="rw-kept-h">
          <b>Dismissed</b> kept in the record, with your reason — no work for the loop
        </div>`}
      ${kept.map(row)}
    </div>`;
}

/* Exported because the worklist ends with it too: the route is computed from
   the flags, so wherever the flags are made is where it belongs. Two copies of
   this arithmetic is how two screens come to name different destinations. */
/* Exported because the worklist ends with it too: the route is computed from
   the flags, so wherever the flags are made is where it belongs. Two copies of
   this arithmetic is how two screens come to name different destinations.

   `list` is off on the Repairs screen, which lists the flags itself in its own
   left column; `escape` is the other way out of the screen this card is on,
   which the worklist has and Repairs does not. */
export function Route({
  plan, flags, budget, busy, onDispatch, onRetag, titleOf,
  pending = 0, list = true, escape = null,
}) {
  const waiting = pending > 0;
  const shape = waiting ? ROUTE.waiting : (ROUTE[plan.route] || ROUTE.accept);
  const forced = new Set(plan.forced_by || []);
  const forcedFlags = flags.filter((f) => forced.has(f.id));
  const blocked = plan.route === 'repair' && budget?.exhausted;

  return html`
    <aside class=${`rw-route rw-route-${shape.cls}`}>
      <div class="rw-route-head">
        <div class="rw-route-title">${shape.title}</div>
        <p class="rw-because">
          ${lead(plan, waiting,
                 (plan.to_repair || []).length + (plan.to_oracle || []).length)}
        </p>
      </div>

      ${!waiting && forcedFlags.length > 0 && html`
        <div class="rw-forced">
          <div class="eyebrow">Forced by</div>
          ${forcedFlags.map((f) => html`
            <p key=${f.id} class="rw-forced-item"><span class="mono">${f.id}</span> ${f.text}</p>`)}
          <p class="rw-counter">
            Retag ${forcedFlags.length === 1 ? 'it' : 'them'} and this becomes a repair round
            instead — the branch untouched, and the repairer can still come back and tell you it
            needs the spec, which is the cheaper way to find out.
          </p>
        </div>`}

      ${list && html`
        <${Filed} flags=${flags} onRetag=${onRetag} busy=${busy} titleOf=${titleOf} />`}

      ${/* THE BUDGET SAYS NOTHING WHILE THERE IS ENOUGH OF IT. A meter reading
           $8.75 of $8.75 is not information, and a head, a bar and three
           sentences of policy through the middle of the one action on the
           screen, on every run, would say it whether or not it had anything to
           say. The policy sentence is true and worth reading exactly once —
           when the figure is about to refuse you — so it lives in here with
           it. */''}
      ${blocked && html`
        <div class="rw-short">
          <b>Spent</b>
          <span>
            ${`${usd(budget.remaining_usd)} left of ${usd(budget.spendable_usd)}, across `
              + `${budget.dispatches} dispatch${budget.dispatches === 1 ? '' : 'es'}. `}
            Raise ${' '}<span class="mono">rework.budget_usd</span>${' '}
            if another round is worth it${' \u2014 '}
            ${`${usd(budget.reserve_usd)} is held back either way, so a packet can still be `
              + 'produced. This is the whole feature\u2019s figure: sending it back again does '
              + 'not buy another.'}
          </span>
        </div>`}

      <div class="rw-route-foot">
        <button class="btn btn-primary"
                disabled=${busy || waiting || !flags.length || plan.route === 'accept' || blocked}
                onClick=${onDispatch}>${shape.cta}</button>
        <div class="rw-route-n">
          <p class="rw-note">
            Every flag stays in the packet next time, with what happened to it beside it.${' '}
            <a href="#" data-help-open="the-repair-loop">How the repair loop works</a>
          </p>
          ${escape}
        </div>
      </div>
    </aside>`;
}
