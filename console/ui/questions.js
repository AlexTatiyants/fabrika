/* Gate 1 — the questions, and what freezing them produces.
 *
 * Stacked as cards -- severity, the question, why it matters, the options one
 * under another, then a text field -- four questions run to about three
 * screens of scroll in a 980px column, beside a rail that stays empty until
 * you have answered something.
 *
 * So the same content runs across instead of down, one row per question, at
 * full width. Two consequences worth naming, because they are the point rather
 * than side effects:
 *
 *   - An answer replaces the options *in the row it answers*. A settled question
 *     stays exactly where it was in the list. Collapsing it into a different
 *     shape somewhere else would move the thing you had just decided away from
 *     where you decided it.
 *
 *   - There is no rail while you are answering. A rail would show what you had
 *     settled, and the rows show that themselves. It appears once the spec is
 *     frozen, where there is finally a contract to put in it.
 */

import { html, useState } from '../vendor/preact-htm.module.js';
import { Pill, base, send, useBarAction } from './shared.js';

/* The chip says which; a coloured edge saying it a second time would need
   hardcoded hex that goes stale the moment the palette moves. Blocking takes
   the one colour that means "you are required"; the rest are named, not
   coloured. */
const SEVERITY = {
  blocking: { pill: 'pill-accent' },
  significant: { pill: '' },
  minor: { pill: '' },
};

/* ------------------------------------------------------------ one question */

function Question({ q, answer, expanded, frozen, busy, onAnswer, onReopen, onToggle }) {
  const [free, setFree] = useState('');
  const done = !!(answer || '').trim();
  const sev = SEVERITY[q.severity] || SEVERITY.minor;

  return html`
    <div class=${`qrow ${done ? 'done' : ''} ${expanded ? 'open' : ''} ${
      q.severity === 'blocking' && !done ? 'blocking' : ''}`}>
      <div class="qmeta">
        <${Pill} cls=${done ? '' : sev.pill}>${done ? 'settled' : q.severity}<//>
        <span class="mono qid">${q.id}</span>
      </div>

      <div class="qbody">
        <div class="qtitle">${q.question}</div>
        <p class=${`qwhy ${expanded ? '' : 'clamp'}`}>${q.why_it_matters}</p>
        ${!done && html`
          <button class="qmore" onClick=${() => onToggle(q.id)}
                  aria-expanded=${expanded ? 'true' : 'false'}
                  aria-label=${expanded
                    ? `Collapse ${q.id}`
                    : `Read why ${q.id} matters, or write your own answer`}>
            ${expanded ? 'less' : 'why this matters · write your own'}
          </button>`}
      </div>

      <div class="qanswer">
        ${done
          ? html`
              <div class="ans">
                <span class="ans-mark">✓</span>
                <span class="ans-text">${answer}</span>
                ${!frozen && html`
                  <button class="ans-change" disabled=${busy}
                          onClick=${() => onReopen(q.id)}>change</button>`}
              </div>`
          : html`
              <div>
                <div class="opts">
                  ${(q.options || []).map((opt) => html`
                    <button key=${opt} class="opt" disabled=${frozen || busy}
                            onClick=${() => onAnswer(q.id, opt)}>
                      ${opt}
                      ${opt === q.proposed_default && html`<span class="def">default</span>`}
                    </button>`)}
                </div>
                ${expanded && html`
                  <div class="qfree">
                    <input
                      placeholder="Or write your own answer"
                      value=${free}
                      disabled=${frozen || busy}
                      onInput=${(e) => setFree(e.target.value)}
                      onKeyDown=${(e) => {
                        if (e.key === 'Enter' && free.trim()) onAnswer(q.id, free.trim());
                      }} />
                    <button class="btn btn-sm" disabled=${frozen || busy || !free.trim()}
                            onClick=${() => onAnswer(q.id, free.trim())}>Use this</button>
                    <button class="btn btn-quiet btn-sm" disabled=${frozen || busy}
                            onClick=${() => onAnswer(q.id, q.proposed_default)}>
                      Use the default
                    </button>
                  </div>`}
              </div>`}
      </div>

    </div>`;
}

/* -------------------------------------------------- correcting the reading */

function Restatement({ report, corrections, plan, frozen, busy, onCorrect, onReintake }) {
  const [mode, setMode] = useState('');
  const [text, setText] = useState('');

  return html`
    <div class=${`restate ${mode ? 'restate-open' : ''}`}>
      <div>
        <div class="eyebrow restate-eyebrow">
          What it thinks you asked for${corrections.length
            ? ` · corrected ${corrections.length} time${corrections.length === 1 ? '' : 's'}`
            : ''}
        </div>
        <p class="restate-text">${report.restated_intent || ''}</p>

        ${mode === 'correct' && html`
          <div class="restate-box">
            <textarea rows="4" value=${text} disabled=${busy}
              placeholder="Say what it actually is. This replaces its reading entirely, and every question is asked again from what you write here."
              onInput=${(e) => setText(e.target.value)} />
            <p class="restate-note">
              The ${(report.ambiguities || []).length} question${
                (report.ambiguities || []).length === 1 ? '' : 's'} below were built on the reading
              above and will be discarded.
            </p>
            <div class="restate-acts">
              <button class="btn btn-quiet btn-sm" disabled=${busy}
                      onClick=${() => { setMode(''); setText(''); }}>Cancel</button>
              <button class="btn btn-primary btn-sm" disabled=${busy || !text.trim()}
                      onClick=${() => onCorrect(text.trim())}>Ask again from this</button>
            </div>
          </div>`}

        ${mode === 'reintake' && html`
          <div class="restate-box">
            <p class="restate-note">
              <b>Read this repository again from scratch.</b> The scout's report and everything
              derived from it are replaced. Use this when what it saw was wrong or incomplete — if
              it simply misread a correct report, correcting the wording above is one call instead
              of ${plan ? plan.calls : 'several'}.
            </p>
            ${plan && html`
              <p class="restate-note dim">
                ${`${plan.calls} model call${plan.calls === 1 ? '' : 's'}: ${plan.slices} scout `
                  + `slice${plan.slices === 1 ? '' : 's'} over ${plan.files_read} of ${plan.files} `
                  + `files (${Math.round(plan.covered * 100)}%), ${plan.symbols} symbols indexed, `
                  + `then one interrogator pass.`}
              </p>`}
            <div class="restate-acts">
              <button class="btn btn-quiet btn-sm" disabled=${busy}
                      onClick=${() => setMode('')}>Cancel</button>
              <button class="btn btn-primary btn-sm" disabled=${busy}
                      onClick=${onReintake}>Read it again</button>
            </div>
          </div>`}
      </div>

      ${!mode && html`
        <div class="restate-acts">
          <button class="btn btn-sm" disabled=${frozen || busy}
                  onClick=${() => setMode('correct')}>That's not what I meant</button>
          <button class="btn btn-quiet btn-sm" disabled=${frozen || busy}
                  onClick=${() => setMode('reintake')}>
            Read the codebase again${plan ? ` · ${plan.calls} call${plan.calls === 1 ? '' : 's'}` : ''}
          </button>
        </div>`}
    </div>`;
}

/* ---------------------------------------------------------------- screen */

export function Questions({ data, plan, browsing, onReload }) {
  const report = data.interrogation || {};
  const questions = report.ambiguities || [];
  const stage = data.state.stage;
  const frozen = stage !== 'awaiting_answers';

  const [answers, setAnswers] = useState(() => {
    /* One record holding every answer -- `{resolved, asked}` -- not a list.
       Reading it as a list throws "object is not iterable" on every feature
       the pipeline itself creates, and on none of the seeded ones, because
       the fixture writes a different shape. */
    const seed = {};
    for (const a of (data.answers || {}).resolved || []) {
      if (a.question_id) seed[a.question_id] = a.answer;
    }
    return seed;
  });
  const [expanded, setExpanded] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const projectId = data.state.project_id;
  const featureId = data.state.feature_id;
  const act = async (fn) => {
    setBusy(true); setError('');
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const answeredCount = questions.filter((q) => (answers[q.id] || '').trim()).length;
  const openBlocking = questions.filter(
    (q) => q.severity === 'blocking' && !(answers[q.id] || '').trim()).length;
  const unanswered = questions.length - answeredCount;
  /* `?questions` is a request to see the questions step, not the stage the
     feature is on. Browsing it after the spec is frozen shows what was asked
     and answered, read-only -- which is what a step link means everywhere else
     in this console. Without a view, the stage picks the screen, and a frozen
     spec picks the spec document rather than this. */

  const freeze = () => act(async () => {
    const body = {};
    for (const q of questions) {
      const value = (answers[q.id] || '').trim();
      if (value) body[q.id] = value;
    }
    await send(`${base(projectId, featureId)}/answers`, { answers: body, deferred: [] });
    onReload();
  });

  /* One action for the whole screen: freezing. Approving a frozen spec is the
     spec document's own button. */
  useBarAction(
    !busy && !browsing && openBlocking === 0,
    freeze,
    [busy, browsing, openBlocking, answers],
  );

  return html`
    <div class="wrap wide g1">
      ${error && html`<div class="rw-error">${error}</div>`}

      <${Restatement}
        report=${report} corrections=${data.corrections || []} plan=${plan}
        frozen=${frozen || browsing} busy=${busy}
        onCorrect=${(text) => act(async () => {
          await send(`${base(projectId, featureId)}/correct`, { correction: text });
          onReload();
        })}
        onReintake=${() => act(async () => {
          await send(`${base(projectId, featureId)}/reintake`);
          onReload();
        })} />

      <div class="strip">
        <span><b class="strip-n">${answeredCount}</b> of ${questions.length} answered</span>
        <span class="ticks">
          ${questions.map((q) => html`
            <span key=${q.id} class=${`tick ${(answers[q.id] || '').trim() ? 'on' : ''}`} />`)}
        </span>
        <span class="strip-note">
          ${browsing
            ? 'Read as it was. Answers are shown as given; nothing here can be changed.'
            : openBlocking
              ? `${openBlocking} blocking — choosing wrong throws the work away rather than `
                + 'adjusting it.'
              : `${unanswered} unanswered will be carried into the spec as accepted risk, `
                + 'and named there.'}
        </span>
      </div>

      ${questions.length === 0 && html`
        <div class="card qempty">
          <h2>The interrogator raised nothing.</h2>
          <p class="muted">
            Read its understanding above closely. If it captures what you meant, your intent was
            tight enough to build from. If it reads like a paraphrase that dropped something,
            correct it rather than freezing.
          </p>
        </div>`}

      ${questions.map((q) => html`
        <${Question} key=${q.id} q=${q}
          answer=${answers[q.id] || ''} expanded=${expanded === q.id}
          frozen=${frozen || browsing} busy=${busy}
          onAnswer=${(id, value) => { setAnswers({ ...answers, [id]: value }); setExpanded(null); }}
          onReopen=${(id) => {
            const next = { ...answers }; delete next[id];
            setAnswers(next); setExpanded(id);
          }}
          onToggle=${(id) => setExpanded(expanded === id ? null : id)} />`)}

      ${!browsing && html`
        <p class="g1-note">
          ${openBlocking
            ? 'Freezing is blocked until every blocking question has an answer.'
            : 'Freezing closes gate 1. Unanswered questions are carried into the spec as '
              + 'accepted risk, and named there.'}
          ${' '}<a href="#" data-help-open="the-questions">More about the questions</a>
        </p>`}
    </div>`;
}
