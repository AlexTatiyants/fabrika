/* What each round changed.
 *
 * One axis: the round. Everything that belongs on this screen is an attribute
 * of one, and a round opens onto the units it dispatched -- the file, what the
 * diff does, the findings it closed, its own decisions and disclosures.
 *
 * What is deliberately not here, each on an axis of its own: counts of
 * findings by outcome, which is what the Objections bands already group by; a
 * cost line, whose headline figure ("$0.00 of a $10.00 budget") can be
 * contradicted by a note that eleven agents ran unmeasured; a heading and
 * three sentences for one trivial low-confidence decision; and a second copy
 * of the flag list and the Accept button that close "Your calls". A screen
 * holding all of those can still fail to show what a round actually did.
 *
 * The stop reason is the last row rather than a paragraph above the list: the
 * round that did not happen. A loop that converged and a loop that ran out of
 * rounds mean opposite things, and that row is the difference.
 */

import { html } from '../vendor/preact-htm.module.js';
import {
  findingsWithRecords, isRestatement, outcomeOf, packetOf, repairAccount,
} from './shared.js';
import { Decision, Disclosures } from './account.js';

/* Only what a round could not say for itself. A unit reports its own files and
   summary; whether its commit survived the round is the loop's to report. */
function Reverted({ n }) {
  return html`
    <div class="rd-unit">
      <span class="mono rd-unit-id">reverted</span>
      <div>
        <p class="rd-unit-s">The checks came back worse than before this round, so the commit was
          thrown away. What the units below wrote is not on the branch.</p>
      </div>
    </div>`;
}

function Unit({ unit, titleOf, decisions, ruled, onRule, busy }) {
  const mine = decisions.filter((d) => (unit.decision_ids || []).includes(d.id));
  return html`
    <div class="rd-unit">
      <span class="mono rd-unit-id">${unit.unit_id}</span>
      <div>
        ${(unit.files || []).map((f) => html`
          <div class="mono rd-unit-f" key=${f}>${f}</div>`)}
        ${unit.summary && html`<p class="rd-unit-s">${unit.summary}</p>`}
        <div class="mono rd-unit-m">
          ${(unit.finding_ids || []).length
            ? html`closed ${unit.finding_ids.join(', ')}` : 'closed nothing'}
          ${unit.disclosures ? ` · ${unit.disclosures} disclosure line${
            unit.disclosures === 1 ? '' : 's'}` : ''}
        </div>
        ${(unit.finding_ids || []).map((id) => {
          const title = titleOf(id);
          return title ? html`<p class="rd-unit-fin" key=${id}>${title}</p>` : null;
        })}
        ${mine.length > 0 && html`
          <div class="rd-unit-dec">
            ${mine.map((d) => html`
              <${Decision} key=${d.id} d=${d} ruling=${ruled[d.id] || null} open=${false}
                           onRule=${onRule} busy=${busy} />`)}
          </div>`}
      </div>
    </div>`;
}

function Round({ n, units, repaired, reverted, titleOf, decisions, ruled, onRule, busy,
                refused }) {
  /* A packet built before the rounds recorded their units has none, and a row
     reading "0 findings closed" over a round that closed four is worse than
     showing no count at all. The ledger still knows which findings a round
     repaired, so that is what the row counts when the units are missing. */
  const known = units.length > 0;
  const closed = known
    ? units.reduce((a, u) => a + (u.finding_ids || []).length, 0)
    : repaired.length;
  const files = [...new Set(units.flatMap((u) => u.files || []))];
  return html`
    <details class=${`rd ${reverted ? 'rd-rev' : ''}`}>
      <summary>
        <span class="rd-n">Round ${n}${reverted ? ' · reverted' : ''}</span>
        <span class="mono rd-r">${closed} finding${closed === 1 ? '' : 's'} closed</span>
        <span class="rd-w">
          ${known
            ? html`${units.length} unit${units.length === 1 ? '' : 's'}${files.length
                ? ` · ${files.length} file${files.length === 1 ? '' : 's'}` : ''}`
            : 'this round did not record its units'}
        </span>
      </summary>
      <div class="rd-body">
        ${reverted && html`<${Reverted} n=${n} />`}
        ${!known && html`
          <div class="rd-unit">
            <span class="mono rd-unit-id">closed</span>
            <div>
              ${repaired.length
                ? repaired.map((f) => html`
                    <p class="rd-unit-fin" key=${f.id}>
                      <span class="mono dim">${f.id}</span> ${f.title}</p>`)
                : html`<p class="rd-unit-s">This round resolved no finding.</p>`}
              <p class="rd-unit-s">What each unit changed was not recorded on this packet, so
                what the round closed is all there is. A later run records the file, the change
                and the findings each unit was given.</p>
            </div>
          </div>`}
        ${units.map((u) => html`
          <${Unit} key=${u.unit_id} unit=${u} titleOf=${titleOf} decisions=${decisions}
                   ruled=${ruled} onRule=${onRule} busy=${busy} />`)}
        ${refused.length > 0 && html`
          <div class="rd-unit">
            <span class="mono rd-unit-id ev-bad">refused</span>
            <div>
              <p class="rd-unit-s">A repair tried to edit the code that decides whether the checks
                pass, and the writes were refused. This is a finding about the repairer, not a
                nuisance: a repair that can reach the verification surface can make any finding
                disappear without fixing anything. (INV-12)</p>
              ${refused.map((p) => html`<div class="mono rd-unit-f" key=${p}>${p}</div>`)}
            </div>
          </div>`}
      </div>
    </details>`;
}

/* The round that did not happen, and why. */
function Stopped({ rework, left, titleOf }) {
  return html`
    <details class="rd rd-stop">
      <summary>
        <span class="rd-n">No round ${(rework.rounds || 0) + 1}</span>
        <span class="mono rd-r">stopped</span>
        <span class="rd-w">${left.length
          ? `${left.length} finding${left.length === 1 ? '' : 's'} routed for repair and never
             attempted` : 'nothing was left to attempt'}</span>
      </summary>
      <div class="rd-body">
        <div class="rd-unit">
          <span class="mono rd-unit-id">why</span>
          <div>
            <p class="rd-unit-s">${rework.stop_reason || 'nothing recorded a reason'}</p>
            ${left.map((f) => html`
              <p class="rd-unit-fin" key=${f.id}>
                <span class="mono dim">${f.id}</span> ${f.title}</p>`)}
          </div>
        </div>
      </div>
    </details>`;
}

export function Repairs({ data, busy, onRule }) {
  const packet = packetOf(data);
  const rework = packet.rework || {};
  const all = findingsWithRecords(data).filter((f) => !isRestatement(f));
  const titleOf = (id) => (all.find((f) => f.id === id) || {}).title || '';
  const left = all.filter((f) => outcomeOf(f) === 'unattempted');

  const ruled = {};
  (data.rulings || []).forEach((r) => { ruled[r.decision_id] = r; });
  const decisions = packet.decisions || [];
  const account = repairAccount(data);
  const claimed = new Set(((rework.units || [])).flatMap((u) => u.decision_ids || []));
  const orphaned = account.decisions.filter((d) => !claimed.has(d.id));

  /* Grouped by the round that dispatched them. A round with no units still
     gets a row: a round that changed nothing is a fact about the loop, and a
     row that is absent cannot say it. */
  const byRound = {};
  ((rework.units || [])).forEach((u) => { (byRound[u.round] || (byRound[u.round] = [])).push(u); });
  const rounds = [];
  for (let n = 1; n <= (rework.rounds || 0); n += 1) rounds.push(n);
  const reverted = new Set(rework.reverted_rounds || []);
  const refused = rework.protected_writes_refused || [];

  if (rework.enabled === false) {
    return html`<div class="wrap"><section class="block">
      <p class="tab-lead">The repair loop is switched off, so nothing tried to fix any
        objection.</p>
    </section></div>`;
  }

  return html`
    <div class="wrap">
      <p class="tab-lead">What the repair loop changed in each round, and what that fixed.</p>
      <section class="block">
        ${rounds.map((n) => html`
          <${Round} key=${n} n=${n} units=${byRound[n] || []} reverted=${reverted.has(n)}
                    repaired=${all.filter((f) => (f.rec || {}).repaired_in_round === n)}
                    titleOf=${titleOf} decisions=${decisions} ruled=${ruled}
                    onRule=${onRule} busy=${busy}
                    refused=${n === (rework.rounds || 0) ? refused : []} />`)}
        <${Stopped} rework=${rework} left=${left} titleOf=${titleOf} />
      </section>

      ${/* Everything a repairer disclosed, once, below the rounds. A unit's
           own count is on its row and its lines are here: the same split the
           build units get, and the reason a disclosure band exists at all is
           that a repairer works under a narrower brief than the unit it is
           correcting, so what it says it could not reach is often the most
           exact account in the packet of why something is still standing.

           `repairAccount` rather than a match on the unit's name. It asks
           whether the line came from something that is not a build unit and
           not the integrator, which stays true however repair units come to
           be named -- and a filter that depends on the naming drops every
           line the day the naming changes, silently, which is the shape of
           bug this screen exists to stop telling. */''}
      ${/* A repair decision no round claimed. On a packet built before the
           rounds recorded their units there are no units to claim any, so
           every one of them lands here -- which is the point: a screen that
           only knew how to draw packets with units would orphan the account
           of every run that came before them, and orphaning an account is the
           failure this screen exists to stop. */''}
      ${orphaned.length > 0 && html`
        <section class="block">
          <h2 class="sec-h">Choices the repair rounds made</h2>
          <p class="sec-sub">Not claimed by any round above — on this packet the rounds recorded
            no units, so their choices are listed together. A repairer decides things too, most
            often that something is not a code defect and needs no fix, which is a claim worth as
            much scrutiny as a change. Ruling on one records that you saw it and let it stand.</p>
          ${orphaned.map((d) => html`
            <${Decision} key=${d.id} d=${d} ruling=${ruled[d.id] || null} open=${false}
                         onRule=${onRule} busy=${busy} />`)}
        </section>`}

      <${Disclosures} lines=${account.disclosures}
        id="rp-disclosed" title="What the repair rounds disclosed"
        sub=${'Mandatory, and verbatim, the same as a build unit\'s. Each line names the unit '
            + 'that filed it, and each unit names its round.'} />
    </div>`;
}
