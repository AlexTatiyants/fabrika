/* Objections — everything the panel raised about this change, on one screen.
 *
 * Security is not a slice of its own, because nothing would land in it. There
 * is no security agent: the half of that reading that can be proved belongs
 * to the breaker as probes that fail, and the half that cannot belongs to the
 * reviewer as one of the angles it reads in prose. And the reviewer is told
 * to categorise concretely — `authz`, `exposure`, `data-loss`, `injection`,
 * `secrets`, `supply-chain` — so nothing it raises answers to the word
 * "security". A tab keyed on that word could only ever be empty while the
 * findings it was meant for sat under another heading.
 *
 * One screen, then, and the category is printed on the row, so a reader
 * looking for authority or exposure finds it without a click. Sliced on
 * nothing, ordered by what the repair loop did about each.
 *
 * Nothing here is dropped for having been fixed. A packet that got shorter as
 * the loop worked would look better and be worth less. (INV-11)
 */

import { html } from '../vendor/preact-htm.module.js';
import {
  Composer, OUTCOME, Row, SEVERITY_PILL, bandsOf, findingsWithRecords,
  isRestatement, outcomeOf, packetOf,
} from './shared.js';

/* Who raised something, for the line under a restatement. The model is part of
   the name because the role alone does not identify a reader -- two roles on
   one model are two entries on the roster and one reading -- and a person
   deciding what a restatement is worth is asking exactly that. */
const agentOf = (f) => {
  const rec = f.rec || {};
  const role = rec.role || 'unrecorded';
  return rec.model ? `${role} · ${rec.model}` : role;
};

function Finding({ f, restatements }) {
  const [label, cls] = OUTCOME[outcomeOf(f)] || [outcomeOf(f), ''];
  const rec = f.rec || {};
  const history = [];
  if (rec.attempts) history.push(`${rec.attempts} repair attempt${rec.attempts > 1 ? 's' : ''}`);
  if (rec.repaired_in_round) history.push(`fixed in round ${rec.repaired_in_round}`);
  if (rec.commit) history.push(rec.commit.slice(0, 12));

  /* The ledger already decided which of these is evidence, when it tied each
     restatement to this finding. Read its answer rather than recomputing one:
     two answers to the same question drift, and the record is the one that the
     arbiter saw and the packet document prints. A restatement the ledger did
     not classify — from a run before it made the distinction — shows as
     corroboration, because that is what the ledger falls back to and an
     unknown must not be presented as more certain here than there. */
  const echoed = new Set(rec.restated_by || []);
  const crossAgent = restatements.filter((r) => !echoed.has(r.id));
  const sameAgent = restatements.filter((r) => echoed.has(r.id));

  const meta = html`
    ${crossAgent.length > 0
      && html`<span class="drow-corr">+${crossAgent.length} agreed</span> · `}
    ${f.category || 'finding'} · ${label}`;

  return html`
    <${Row} id=${f.id} sev=${f.severity} pill=${f.severity} pillCls=${SEVERITY_PILL[f.severity]}
            title=${f.title} meta=${meta}>
      <p>${f.detail}</p>
      ${f.evidence && html`<div class="f-ev">${f.evidence}</div>`}
      ${/* The rule it holds the code to, and whether code found it there. */''}
      ${f.cites && f.cites.source === 'guide' && html`
        <p class="f-cite"><b>Cites</b> <span class="mono">${f.cites.ref}</span>: “${f.cites.quote}”
          ${f.cite_verified
            ? html` <span class="ev-ok">· found in the guide</span>`
            : html` <span class="ev-bad">· not in the guide — capped at minor</span>`}</p>`}
      ${f.cites && f.cites.source === 'observed' && html`
        <p class="f-cite"><b>Cites</b> an observed convention, <span class="mono">${f.cites.ref}</span>
          <span class="dim"> · inferred by a model, so at most minor</span></p>`}
      ${f.recommendation && html`<p><b>Recommended.</b> ${f.recommendation}</p>`}
      ${rec.disposition_reason && html`
        <p><b>Routed as ${rec.disposition}.</b> ${rec.disposition_reason}</p>`}
      ${rec.outcome_evidence && html`<p><b>On re-check.</b> ${rec.outcome_evidence}</p>`}

      ${/* Two headings, because they are two different facts and one
           heading would say one of them wrongly. "Independently found by 2
           more" is false over the restatements of a single agent sampled
           three times -- which is what the panel is *built* to do, on
           purpose, so one pass catches what another talks itself out of.
           Reading that as three readers agreeing is asking one witness to
           describe the car three times and writing down three witnesses. It
           can cost a packet a blocker. */''}
      ${crossAgent.length > 0 && html`
        <div class="f-restated">
          <div class="eyebrow">Independently found by ${crossAgent.length} other${' '}
            ${crossAgent.length === 1 ? 'agent' : 'agents'}</div>
          ${crossAgent.map((r) => html`
            <p key=${r.id}>
              <span class="mono dim">${r.id}</span> ${r.title}
              <span class="dim"> — ${agentOf(r)}</span>
            </p>`)}
          <p class="dim">Kept, not merged. This is corroboration in the sense that counts —
            another agent reached the same defect — and it is usually why the arbiter routed
            this for repair instead of escalating it.</p>
        </div>`}

      ${sameAgent.length > 0 && html`
        <div class="f-restated f-restated-quiet">
          <div class="eyebrow">Worded ${sameAgent.length} other${' '}
            ${sameAgent.length === 1 ? 'way' : 'ways'} by the same agent</div>
          ${sameAgent.map((r) => html`
            <p key=${r.id}>
              <span class="mono dim">${r.id}</span> ${r.title}
              <span class="dim"> — ${agentOf(r)}</span>
            </p>`)}
          <p class="dim">Not a second opinion. The panel is sampled more than once on purpose,
            so one defect arrives worded several ways — that is the sampling working, and it
            says nothing about whether the defect is real.</p>
        </div>`}

      <div class="f-foot">
        <span>raised by <b>${rec.role || 'unrecorded'}</b></span>
        ${rec.rounds_seen && rec.rounds_seen.length
          ? html`<span>seen in round ${rec.rounds_seen.join(', ')}</span>` : null}
        ${history.length ? html`<span>${history.join(' · ')}</span>` : null}
        ${(f.criterion_ids || []).length
          ? html`<span>affects ${f.criterion_ids.join(', ')}</span>` : null}
        ${(f.files || []).length
          ? html`<span class="mono f-files">${f.files.join('  ')}</span>` : null}
      </div>
    <//>`;
}

function Band({ band, mine, restatedBy }) {
  const blockers = mine.filter((f) => f.severity === 'blocker').length;
  return html`
    <details class="fg" open=${band.open}>
      <summary class="fg-head">
        <span class="fg-title">${band.title}</span>
        <span class="mono fg-count">
          ${mine.length}${blockers ? ` · ${blockers} blocker${blockers === 1 ? '' : 's'}` : ''}
        </span>
      </summary>
      ${band.sub && html`<p class="fg-sub">${band.sub}</p>`}
      <div class="fg-rows">
        ${mine.map((f) => html`
          <${Finding} key=${f.id} f=${f} restatements=${restatedBy[f.id] || []} />`)}
      </div>
    </details>`;
}

/* What each agent attacked and could not break.
 *
 * The panel runs again after every repair round, so a role has one reading per
 * round -- and listing all of them gives four blocks headed "hacker", four
 * headed "adversary", ninety concessions in total, not one of them repeated
 * verbatim because each pass words its own. Only each role's last is shown.
 *
 * Only the last reading is about the tree that is shipping. A concession from
 * the first pass was made against code that three repair rounds then changed,
 * so printing it beside the current one states, as a present fact, something
 * that was true of a branch that no longer exists -- the same error as showing
 * a superseded packet as current. The earlier readings stay in the ledger.
 *
 * Kept at all because they are what make the findings above credible: an agent
 * that objects to everything carries no information, and a panel that conceded
 * nothing is a panel nobody should believe. (ReviewReport.conceded)
 */
/* Ledger order, not the round number.
 *
 * `round` counts within one dispatch and starts again at zero when a packet is
 * sent back, so a feature's readings can run 0,0,0 · 1,1,1 · 0,0,0 · 3,3,3 --
 * and picking the highest round would choose a panel from the previous
 * dispatch whenever the latest one stopped earlier than its predecessor. The
 * store appends in order, so the last record for a role is its last reading.
 * The round survives only as a label on the block. */
function latestByRole(reviews) {
  const last = {};
  const seen = {};
  (reviews || []).forEach((r) => {
    seen[r.role] = (seen[r.role] || 0) + 1;
    last[r.role] = r;
  });
  return Object.values(last)
    .map((r) => ({ role: r.role, round: r.round ?? 0, readings: seen[r.role],
                   items: ((r.report || {}).conceded || []) }))
    .filter((r) => r.items.length);
}

function Conceded({ reviews }) {
  const rows = latestByRole(reviews);
  if (!rows.length) return null;
  const total = rows.reduce((n, r) => n + r.items.length, 0);
  const readings = Math.max(...rows.map((r) => r.readings), 1);
  const dropped = (reviews || [])
    .reduce((n, r) => n + ((r.report || {}).conceded || []).length, 0) - total;
  const named = rows.map((r) => r.role);
  const who = named.length === 1 ? `the ${named[0]}`
    : `${named.slice(0, -1).map((n) => `the ${n}`).join(', ')} and the ${named[named.length - 1]}`;

  return html`
    <section class="block">
      <details class="fg fg-quiet">
        <summary class="fg-head">
          <span class="fg-title">What ${who} examined and found sound</span>
          <span class="mono fg-count">
            ${total} from ${rows.length} ${rows.length === 1 ? 'agent' : 'agents'}
          </span>
        </summary>
        <p class="fg-sub">What the panel attacked and could not break, in its own words.
          Concessions are what make the findings above worth reading — an agent that objects to
          everything has told you nothing.${readings > 1
            ? html` Each agent read this ${readings} times — the panel runs again after every
                repair round — and only its last reading is about the tree in front of you, so
                that is the one shown. The other ${dropped} concession(s) were made about code the
                loop has since changed, and are in the ledger.` : ''}</p>
        <div class="fg-rows fg-rows-pad">
          ${rows.map((r) => html`
            <div key=${r.role} class="conceded-block">
              <div class="eyebrow">${r.role}${r.readings > 1
                ? html` <span class="dim">· its ${r.readings}th reading, after round ${r.round}
                    </span>` : ''}</div>
              ${r.items.map((text, i) => html`
                <p key=${i} class="conceded-line"><span class="mono ok">✓</span> ${text}</p>`)}
            </div>`)}
        </div>
      </details>
    </section>`;
}

export function Findings({ data, flags, onFile, busy }) {
  const all = findingsWithRecords(data);

  /* A restatement lives inside the finding it restates, not beside it. */
  const restatedBy = {};
  all.filter(isRestatement).forEach((f) => {
    const of = f.rec.duplicate_of;
    (restatedBy[of] || (restatedBy[of] = [])).push(f);
  });

  const mine = all.filter((f) => !isRestatement(f));
  const bands = bandsOf(mine);

  return html`
    <div class="wrap">
      ${/* Security is named here because it has no tab to name it. A
           reader who came looking for "who can now do what" has to learn on
           arrival that this is the screen holding it. */''}
      <p class="tab-lead">
        Every objection raised about this change, security included, grouped by what the
        repair loop did about it.
      </p>
      <section class="block">

        ${/* The adversary's best case against shipping, in its own words.
             This is where it belongs: it is the strongest objection, and
             these are the objections. The role brief calls it "the one
             line a human will read under time pressure", which is an argument
             for putting it above the bands rather than behind a seventh
             tab. */''}
        ${packetOf(data).strongest_objection && html`
          <div class="fd-worst">
            <div class="eyebrow">The strongest case against shipping this</div>
            <p>${packetOf(data).strongest_objection}</p>
          </div>`}

        ${/* No "start here" band above the bands. One would name a handful
             of ids and send the reader to the second group down, past the
             first -- a table of contents for four bands that already carry
             their own counts and their own blocker counts. The bands are the
             ordering. */''}
        ${bands.length
          ? bands.map(({ band, mine: rows }) => html`
              <${Band} key=${band.key} band=${band} mine=${rows} restatedBy=${restatedBy} />`)
          : html`<p class="empty">
              Nothing was raised. The panel read this change, attacked it, and objected to
              nothing in it.</p>`}

      </section>

      ${/* Concessions carry no category to split them on the way findings are
           split, so the only axis they could be divided on is the agent. With
           one screen there is nothing to divide: every agent that read this
           conceded on it here, unfiltered by role. */''}
      <${Conceded} reviews=${data.reviews} />

      <${Composer} onFile=${onFile} busy=${busy} source="finding" anchor="objections"
        placeholder="Something wrong with this work, in your words." />
    </div>`;
}
