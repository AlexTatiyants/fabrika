/* What this run will not decide for itself.
 *
 * On one packet the panel raised 44 objections, the arbiter threw 3 out and
 * the loop repaired 18 -- and the 23 that reached a human would otherwise sit
 * spread across bands and rows on other tabs, with nothing anywhere saying
 * how many there are or how many are left. Figures like `5 blockers open` and
 * `44 findings raised, 31 distinct` do not do that job.
 *
 * So: one screen, one row per decision, and the route at the foot of the same
 * list rather than on a separate page reached from the andon bar.
 *
 * Nothing here is written for the reader. Every line is a field from the
 * packet or a quotation with its author named -- because a screen that
 * summarises a finding is a screen that can be wrong about it, and INV-11 says
 * plainly that a finding reaches the human with its original text intact. A
 * paraphrase can assert, for instance, that the arbiter judged these a call
 * for a person, when on that packet it had never looked at sixteen of them.
 */

import { html, useState } from '../vendor/preact-htm.module.js';
import {
  Composer, DISPOSITIONS, NO_NOTE, Pill, TAG, WORK_STATE, callsFor, manualChecksFor,
  manualRulings, noteOf, packetOf, settledBy, tagOf,
} from './shared.js';
import { PreviewInline, livePreview } from './preview.js';
import { Route } from './rework.js';

/* One labelled line of somebody else's words. `verbatim` is not decoration: it
   is the difference between what an agent said and what this screen thinks it
   means, and the reader is entitled to know which they are reading. `lead` is
   size, not truth -- the arbiter's account is not more verbatim than anyone
   else's above it, only more worth reading first. */
function Said({ who, verbatim = true, lead = false, children }) {
  if (!children) return null;
  return html`
    <div class=${`wl-q ${lead ? 'wl-q-lead' : ''}`}>
      <span class="wl-who">${who}</span>
      <p class=${`wl-said ${verbatim ? 'wl-verbatim' : ''}`}>${children}</p>
    </div>`;
}

function Call({ call, data, flag, onFile, busy, hrefs, titleOf }) {
  const [note, setNote] = useState('');
  const packet = packetOf(data);
  const f = call.head;
  const rec = f.rec || {};
  const state = WORK_STATE[call.group] || WORK_STATE.unrouted;
  const mat = (path) => (packet.materiality || []).find((m) => m.path === path);
  const trace = (id) => (packet.trace || []).find((t) => t.criterion_id === id);

  /* A file's reason is the rapporteur reading the diff, which is the one
     account of this code written after the repair rounds had finished with
     it -- and it can be the only place saying that two repairs added and
     removed the same piece of code. */
  const reasons = call.files.map(mat).filter((m) => m && m.reason);

  /* Only the criteria the finding names. Walking the trace forward from the
     files instead -- what else depends on this code -- does not work: one
     objection can name two files that between them serve thirteen
     criteria, and thirteen dotted links is not an answer to "what does this
     cost". A file that serves one criterion makes
     that join useful and a file that serves thirteen makes it noise, and the
     only thing separating those is a threshold, which is a judgement wearing
     arithmetic. The file link goes to the file screen, which answers that
     question properly. */
  const untested = call.criteria.map(trace)
    .filter((t) => t && ['untested', 'orphan_requirement'].includes(t.status));

  /* Empty when you wrote nothing, and empty is what gets sent. Falling back
     to the finding's own title would put the machine's sentence in the record
     as though you had typed it -- so the exit card would list it under "your
     comments", and the ledger would gain a human finding that was a copy of
     one already in it. A ruling with no note is a routing decision on a
     finding that already exists, and `flag_findings` reads the words off that
     finding rather than off you. */
  const file = (disposition) => onFile({
    text: note.trim(),
    disposition,
    source: 'finding',
    anchor: f.id,
  });

  /* Shut, and one line each.
   *
   * Thirteen calls open at once is nine screens of quotation, and a reviewer
   * cannot see the shape of their own work: which are blockers, how many are
   * about the code and how many about the run. Open it when you decide to
   * spend time on it. `<details>` rather than state, so the browser owns which
   * rows are open and a poll landing mid-read cannot shut one. */
  /* Ruling from a shut row. The buttons sit on the summary, so a row you have
     already read does not have to be reopened to rule on it -- and a click on
     one must not also toggle the `<details>` it is inside. The note field
     stays in the body, where the evidence you are writing about is. */
  const rule = (e, disposition) => {
    e.preventDefault();
    e.stopPropagation();
    file(disposition);
  };

  /* FIVE COLUMNS THAT HOLD STILL. Four chips of different widths
     right-aligned as a group would make what YOU ruled and what the ARBITER
     ruled the same kind of object, starting at a different x on every row,
     and no column could be read down. Severity is a word in the hue its border
     already carries -- the border scans, the word names, and neither needs a
     filled box. What the run did is history, so it is quiet and unboxed; your
     call is a decision, so it is the only filled thing on the row, and on a
     row you have not ruled it is the two buttons in that same column. */
  return html`
    <details class=${`wl-call wl-${call.severity} ${flag ? 'wl-done' : ''}`}>
      <summary class="wl-head">
        <span class="wl-chev" aria-hidden="true">›</span>
        <span class="wl-sev">${call.severity}</span>
        <span class="wl-t">
          ${f.title}
          ${call.ids.length > 1 && html`
            <span class="mono wl-x" title=${`${call.ids.length} findings, clustered because `
              + 'they share a file and a criterion'}>${call.ids.length}×</span>`}
        </span>
        <span class=${`wl-was ${call.group === 'standing' ? 'wl-was-stood' : ''}`}
              title=${state[2]}>${state[0]}</span>
        ${flag
          ? html`<span class="wl-mine">
              <${Pill} cls=${tagOf(flag.disposition).pill}>${tagOf(flag.disposition).label}<//>
            </span>`
          : html`<span class="wl-acts">
              ${DISPOSITIONS.map((k) => html`
                <button key=${k} type="button" class="wl-act" disabled=${busy}
                        title=${TAG[k].consequence} onClick=${(e) => rule(e, k)}>
                  ${TAG[k].label}
                </button>`)}
            </span>`}
      </summary>

      <div class="wl-body">
        ${/* The arbiter read every account below before writing this, so it
             goes first: one synthesis ahead of the raw quotes it was
             synthesised from, the way a lead paragraph goes ahead of its
             sources rather than after them. */''}
        ${rec.disposition_reason && html`
          <${Said} who="The arbiter" lead>${rec.disposition_reason}<//>`}
        <${Said} who=${f.id}>${f.detail}<//>
        ${/* Labelled by basename rather than "The file": a call naming two
             files would print the same label twice, and which reason belongs
             to which file is the whole use of having both. */''}
        ${reasons.map((m) => html`
          <${Said} key=${m.path} who=${html`<span class="mono">${m.path.split('/').pop()}</span>`}>
            ${m.reason}<//>`)}
        ${rec.attempts > 0 && html`
          <${Said} who="The loop" verbatim=${false}>
            Attempted ${rec.attempts}×, re-checked as still there.
            ${rec.outcome_evidence ? ` ${rec.outcome_evidence}` : ''}
          <//>`}
        ${untested.length > 0 && html`
          <${Said} who="Nothing confirms" verbatim=${false}>
            ${untested.map((t) => t.criterion_id).join(', ')}${' '}
            ${untested.length === 1 ? 'has' : 'have'} no blind test on this run.
          <//>`}
        ${call.rest.length > 0 && html`
          <div class="wl-q">
            <span class="wl-who">Also found by</span>
            <ul class="wl-sib">
              ${call.rest.map((o) => html`
                <li key=${o.id}><span class="mono">${o.id}</span>${o.title}</li>`)}
            </ul>
          </div>`}
      </div>

      <div class="wl-meta">
        <span class="wl-roles">
          ${call.said.map(([role, k]) => Array.from({ length: k }, (_, i) => html`
            <span key=${`${role}${i}`} class=${`wl-role wl-role-${role}`} title=${role} />`))}
        </span>
        <span>${call.said.map(([role, k]) => `${role} ×${k}`).join(' · ')}</span>
        ${call.files.map((p) => html`
          <a key=${p} class="mono" href=${hrefs.file(p)}>${p}</a>`)}
        ${call.criteria.map((c) => html`
          <a key=${c} class="mono" href=${hrefs.criterion(c)}>${c}</a>`)}
      </div>

      ${/* The tag is on the summary row, so this says the one thing the
           summary cannot: what you wrote. Labelled and in the same gutter as
           the quotations above it, because every other line in this body says
           whose words it is carrying, and an unlabelled one at the foot would
           be the only sentence on the screen that does not. */''}
      ${flag
        ? html`
          <div class="wl-ruled">
            <span class="wl-who">Your note</span>
            <span class=${`wl-ruled-t ${noteOf(flag, titleOf) ? '' : 'rw-said-none'}`}>
              ${noteOf(flag, titleOf) || NO_NOTE}
            </span>
            <a class="wl-ruled-go" href=${hrefs.repairs()}>see where this sends it →</a>
          </div>`
        : html`
          <div class="wl-note-row">
            <input class="wl-note" value=${note} disabled=${busy}
                   onInput=${(e) => setNote(e.target.value)}
                   placeholder="Why — the repair objective, or your reason if dismissed" />
          </div>`}
    </details>`;
}

/* Check these yourself: the criteria Fabrika did not test.

   First on the screen, because nobody else has checked them. Each says how to
   confirm it by hand -- the criterion's own `verification` -- and takes one of
   two answers. "Works" is recorded and asks nothing more; "doesn't" is an
   ordinary call, routed to the repair loop, exactly as a finding would be.

   Led by the running app, because this is where a person needs to look at it.
   A ruling made while it is open says so, with the commit it was running. */
function ManualChecks({ checks, rulings, onFile, busy, data }) {
  const n = checks.length;
  /* One line per criterion. Each "how to check" is a paragraph, and eight of
     them stacked are a screen of reading before a single answer -- so it
     shows as far as one line allows, and the rest is one click away on the
     criterion you are actually checking. */
  const [opened, setOpened] = useState({});
  const toggle = (id) => setOpened((o) => ({ ...o, [id]: !o[id] }));
  const live = livePreview(data);
  const where = live && live.status === 'open'
    ? `Checked by hand in the running app at ${(live.commit || '').slice(0, 7)}`
    : 'Checked by hand';
  const rule = (c, works) => onFile({
    text: works ? `${where}: it works.` : `${where}: it doesn't work.`,
    disposition: works ? 'dismissed' : 'repair', source: 'criterion',
    anchor: c.criterion_id, severity: works ? 'minor' : 'major',
  });
  return html`
    <section class="block mc">
      ${/* A heading, not a card: the title and the one control it needs, on
           the page, over a rule -- the way every section heading here reads.
           Only the criteria are a card, and it is amber because each is
           waiting on you. */''}
      <div class="mc-head">
        <h3 class="mc-title">Check these yourself <span class="db-count">${n}</span></h3>
        <${PreviewInline} data=${data} /></div>
      <p class="mc-why">Criteria Fabrika didn't test: ${
          [...new Set(checks.filter((c) => c.reason)
            .map((c) => `at the ${c.level} level, ${c.reason}`))].join('; ')
          || "their test level can't run cleanly in this project"}.</p>
      <div class="mc-list">
      ${checks.map((c) => {
        const r = rulings[c.criterion_id];
        return html`
          <div class=${`mc-row ${r ? 'mc-done' : ''} ${opened[c.criterion_id] ? 'mc-open' : ''}`}
               key=${c.criterion_id}>
            <span class="mono">${c.criterion_id}</span>
            <button type="button" class="mc-text" aria-expanded=${!!opened[c.criterion_id]}
                    title=${opened[c.criterion_id] ? '' : 'Show how to check it'}
                    onClick=${() => toggle(c.criterion_id)}>
              <span class="st">${c.title || c.statement}</span>
              ${c.how && html`<span class="how">${c.how}</span>`}</button>
            <span class="acts">${r
              ? html`<span class=${`mc-said ${r.disposition === 'repair' ? 'bad' : ''}`}>${
                  r.disposition === 'repair' ? "doesn't work · sent for repair" : 'works'}</span>`
              : html`
                <button type="button" class="wl-act" disabled=${busy}
                        onClick=${() => rule(c, true)}>works</button>
                <button type="button" class="wl-act" disabled=${busy}
                        onClick=${() => rule(c, false)}>doesn't</button>`}</span>
          </div>`;
      })}
      <p class="mc-foot">“Doesn't” goes to the repair loop like any other call.
        ${' '}<a href="#" data-help-open="unchecked-levels">Why these weren't tested</a></p>
      </div>
    </section>`;
}

export function Worklist({
  data, flags, plan, budget, busy, sent, onFile, onDispatch, onRetag, hrefs,
}) {
  const calls = callsFor(data);
  const settled = settledBy(calls, flags);
  const checks = manualChecksFor(data);
  const rulings = manualRulings(checks, flags);
  const total = calls.length + checks.length;
  const done = calls.filter((c) => settled[c.key]).length
    + checks.filter((c) => rulings[c.criterion_id]).length;
  const left = total - done;
  const packet = packetOf(data);
  const stats = packet.stats || {};
  const untriaged = calls.filter((c) => c.group === 'unrouted').length;
  /* A flag's anchor, resolved to the finding it hangs off -- so a flag whose
     text is that finding's title, filed by an older console in place of a
     note you never wrote, is read for what it is. See `noteOf`. */
  const titleOf = (id) => ((packet.findings || []).find((x) => x.id === id) || {}).title;
  const raised = calls.reduce((n, c) => n + c.ids.length, 0);

  if (!total) {
    return html`
      <div class="wrap">
        <section class="block">
          <p class="sec-sub">Nothing is waiting on you. Every objection this run raised was either
            repaired by the loop or argued away by the arbiter with its reason on the record, and
            the arbiter left nothing unrouted. Read the work map for what was built, or rule on
            the packet below.</p>
          <${Route} plan=${plan} flags=${flags} budget=${budget} busy=${busy}
                    onDispatch=${onDispatch} onRetag=${onRetag}
                    titleOf=${(id) => ((packetOf(data).findings || [])
                      .find((x) => x.id === id) || {}).title} />
        </section>
      </div>`;
  }

  return html`
    <div class="wrap">
      ${/* One line. The clustering rule, the arbiter's default, and the note
           that nothing here is paraphrased would make three boxes above
           thirteen collapsed rows -- more explanation than work, on a screen
           whose whole claim is that it is the work. They are on the rows that
           need them: the count of findings behind a call, and its `never
           triaged` mark. */''}
      ${/* THE STANDING LINE. What the screen is, how much of it is left, and
           what it was budgeted -- one line, not a sub-heading over a bordered
           band holding a 1.6rem numeral, the words `left to rule on`, a
           full-width bar and a note reading `4 of 4 ruled`: four sayings of
           one fact under a tab already reading `0 of 4`. The bar is this
           line's own bottom rule, so it costs no height at all: 36 pixels
           where the band would take 109. */''}
      <div class="wl-stand" style=${{ '--done': `${(100 * done) / total}%` }}>
        <p class="tab-lead">
          The decisions this run left for you to make${untriaged > 0
            ? html`, ${untriaged} of them never triaged by the arbiter` : ''}.
        </p>
        <span class=${`wl-stand-n ${left ? '' : 'wl-stand-done'}`}>
          ${left
            ? html`<b>${left}</b> of ${total} left to rule on`
            : html`<b>All ${total}</b> ruled`}
        </span>
        ${packet.attention_budget_minutes && html`
          <span class="mono wl-stand-bud">${packet.attention_budget_minutes} min budgeted</span>`}
      </div>

      ${sent && html`
        <div class="rw-sent">
          ${sent === 'repair'
            ? 'Sent to the repair loop. The build is resuming against the same spec — your calls '
              + 'are in the ledger, already routed.'
            : 'Gate 1 is reopening. The branch has been reset to where it started, because what '
              + 'was built answers to a spec nobody approved.'}
        </div>`}

      ${checks.length > 0 && html`
        <${ManualChecks} checks=${checks} rulings=${rulings} onFile=${onFile} busy=${busy}
                         data=${data} />`}

      ${calls.length > 0 && html`<section class="block wl-list">
        ${calls.map((c) => html`
          <${Call} key=${c.key} call=${c} data=${data} flag=${settled[c.key]}
                   onFile=${onFile} busy=${busy} hrefs=${hrefs} titleOf=${titleOf} />`)}
      </section>`}

      ${/* The exit, on the screen the work is on, rather than at the bottom
           of Repairs with the actual verdict on a separate page reached from
           the andon bar.

           One heading over it, and it is the card's: an `h2` here, an eyebrow
           repeating it in mono, and a paragraph saying what the card's own
           first sentence says would be four sayings of one thing. The cord
           goes into the card's foot beside the button, because they are the
           two ways out of this screen and a reader looking for one should
           find both in the same place. */''}
      <section class="block wl-exit">
        <${Route} plan=${plan} flags=${flags} budget=${budget} busy=${busy}
                  onDispatch=${onDispatch} onRetag=${onRetag} titleOf=${titleOf}
                  pending=${left}
                  escape=${left === 0 && html`
                    <p class="rw-note">
                      Or rule on the packet as it stands:
                      ${' '}<a href=${hrefs.packet()}>the cord</a>, which accepts
                      ${' '}${stats.blockers ?? 0} blocker${(stats.blockers ?? 0) === 1 ? '' : 's'}
                      ${' '}and ${stats.criteria_total - (stats.criteria_verified ?? 0)} unverified
                      ${' '}criteri${stats.criteria_total - (stats.criteria_verified ?? 0) === 1
                        ? 'on' : 'a'} with it.
                    </p>`} />
      </section>

      ${/* Something the panel missed entirely still has to be sayable, and it
           has no finding to hang off -- but it is the rare case on a screen
           about the things the panel did not miss, and open it would be the
           largest object on it: a textarea three rows deep, under an eyebrow,
           under the anchor `worklist` printed as though it were a packet
           object a reader could go and look at. Shut, like every other
           composer in the console. */''}
      <section class="block">
        <${Composer} onFile=${onFile} busy=${busy} source="comment" anchor="worklist"
          compact anchorLabel=${false} add="+ Something none of these covers"
          placeholder="Something none of these covers, in your words. It reaches the repair loop as written." />
      </section>
    </div>`;
}
