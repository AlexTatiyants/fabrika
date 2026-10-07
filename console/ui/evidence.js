/* The evidence, and what it does not prove. At two scopes.
 *
 * Four kinds, each with its provenance stated, because provenance is the whole
 * of their worth. A blind test is worth something *because* the oracle never
 * saw the code. A breaker probe is worth something *because* it failed. A gate
 * is worth something because a process exited zero and no model was involved.
 *
 * Every card also says what it does not prove. A wall of green ticks with no
 * stated limits trains a reader to skim, which is the opposite of the job.
 *
 * TWO SCOPES, ONE WORD. `Evidence` answers "what proves AC-4" and hangs off a
 * criterion; you arrive at it through a row on the map. `RunEvidence` answers
 * "what did this run actually run, and what came back", and is a tab, because
 * that question belongs to nobody's criterion and has nowhere else to be asked.
 * Both are `?evidence` — the id narrows it, the way `?gates=checks` does.
 *
 * The tab exists because every other tab on this packet is somebody's READING
 * of the run: the QA agent's sentence, the rapporteur's verdict, a grid of
 * ticks standing in for ten test files nobody on the screen can see. The
 * artifacts themselves are all in the payload. The one a human can judge
 * without reading any code is a recording of the browser runs, and it leads.
 */

import { html, useEffect, useState } from '../vendor/preact-htm.module.js';
import {
  Composer, FlagsHere, Pill, SEVERITY_PILL, criteriaRows, evidenceFor, packetOf,
  request, statementOf,
} from './shared.js';

const LIMITS = {
  blind: 'Only the cases the oracle thought to write. A criterion can pass every blind test and '
       + 'still be wrong at a boundary nobody named.',
  gates: 'Nothing about whether the feature is right. A gate says a process exited zero or a '
       + 'number cleared a threshold — both facts, and the only facts in the packet.',
  breaker: 'Probes never land on the branch and never enter the matrix. They are an attack '
         + 'record, not an acceptance argument: a passing probe proves nothing at all.',
  findings: 'An argument made by a model reading the change. Every one is worth checking against '
          + 'the code before you act on it.',
};

function Source({ text }) {
  const lines = String(text || '').split('\n');
  return html`
    <div class="ev-code">
      ${lines.map((l, i) => html`
        <div class="ev-line" key=${i}>
          <span class="mono ev-no">${i + 1}</span>
          <span class="mono ev-text">${l || ' '}</span>
        </div>`)}
    </div>`;
}

export function Evidence({ data, id, hrefs, flags, onFile, busy }) {
  const ev = evidenceFor(data, id);
  const [sel, setSel] = useState(ev.tests.length ? 'blind' : 'gates');

  const statement = statementOf(data, id);
  const breaker = data.breaker;
  const ran = !!breaker;
  const failing = (breaker || {}).failing || [];
  const gatesPassed = ev.gates.filter((g) => g.passed).length;

  const KINDS = [
    { key: 'blind', mark: ev.tests.length ? '✓' : '—',
      tone: ev.tests.length ? 'ok' : 'amber',
      title: 'Blind acceptance test',
      sub: ev.tests.length
        ? `${ev.tests.length} file${ev.tests.length === 1 ? '' : 's'} · written before the code existed`
        : 'none tagged to this criterion' },
    { key: 'gates', mark: gatesPassed === ev.gates.length ? '✓' : '!',
      tone: gatesPassed === ev.gates.length ? 'ok' : 'amber',
      title: "The project's own gates",
      sub: `${gatesPassed} of ${ev.gates.length} passing · no model involved` },
    { key: 'breaker', mark: !ran ? '?' : (failing.length ? '✗' : '—'),
      tone: !ran ? 'amber' : (failing.length ? 'bad' : ''),
      title: 'Breaker probes',
      sub: !ran ? 'no breaker record for this run'
        : failing.length ? `${failing.length} failing · red is the signal`
        : 'nothing failing · which proves nothing' },
    { key: 'findings', mark: ev.findings.length ? '!' : '✓',
      tone: ev.findings.length ? 'amber' : 'ok',
      title: 'What the review said',
      sub: `${ev.findings.length} finding${ev.findings.length === 1 ? '' : 's'} against ${id}` },
  ];

  const PANES = {
    blind: () => html`
      <div>
        <p class="ev-prov">
          Written by the oracle from the frozen spec alone. It has never seen the implementation,
          and there is no code path by which it could have — <span class="mono">verify_context</span>
          takes a spec and returns a string, with no second parameter.
        </p>
        ${ev.tests.length
          ? ev.tests.map((t) => html`
              <div key=${t.path} class="ev-test">
                <div class="mono ev-test-path">${t.path}</div>
                <${Source} text=${t.contents} />
              </div>`)
          : html`<p class="ev-none">
              No blind test is tagged to ${id}.
              ${ev.untestable
                ? ' The oracle reported it as untestable from the spec alone.'
                : ' Nothing verifies this criterion, and its status in the matrix says so.'}
            </p>`}
        ${ev.notes && html`<p class="ev-prov">${ev.notes}</p>`}
      </div>`,
    gates: () => html`
      <div>
        <p class="ev-prov">
          The commands you approved at gate 0, run in this feature's container on this branch. No
          model is invoked in that module and none should ever be.
        </p>
        <div class="ev-gates">
          ${ev.gates.map((g) => html`
            <div key=${g.name} class="ev-gate">
              <span class=${`mono ev-mark ${g.passed ? 'ev-ok' : 'ev-bad'}`}>
                ${g.passed ? '✓' : '✗'}
              </span>
              <span class="mono ev-gate-name">${g.name}</span>
              <span class="mono dim ev-gate-cmd">${g.command}</span>
              <span class="ev-gate-dur">${Math.round(g.duration_s || 0)}s</span>
              ${g.at_base && g.at_base !== 'not_checked' && html`
                <${Pill} cls=${g.at_base === 'failed_at_base' ? '' : 'pill-amber'}>
                  ${g.at_base === 'failed_at_base' ? 'already red at base' : g.at_base}
                <//>`}
            </div>`)}
        </div>
      </div>`,
    breaker: () => html`
      <div>
        <p class="ev-prov">
          Written after reading the implementation, specifically to break it, then run. Its
          epistemics are the exact inverse of the oracle's: it has read everything, so a passing
          probe proves nothing. Only a failing one carries information.
        </p>
        ${!ran
          ? html`<p class="ev-none">
              No breaker record for this run. That is not the same as nothing failing — it means
              nothing attacked this code, and this panel is reporting an absence rather than a
              result.
            </p>`
          : failing.length
            ? failing.map((name) => html`
                <div key=${name} class="ev-fail">
                  <span class="mono ev-mark ev-bad">✗</span>
                  <span class="mono">${name}</span>
                </div>`)
            : html`<p class="ev-none">
                No probe is failing. That is not evidence the code is correct — it is evidence the
                breaker did not find a way in this round.
              </p>`}
        ${ran && breaker.notes && html`<p class="ev-prov">${breaker.notes}</p>`}
      </div>`,
    findings: () => html`
      <div>
        <p class="ev-prov">
          Raised by the review panel against this criterion. Every agent that ran sees the same
          evidence bundle; findings are de-duplicated by title keeping the harsher reading.
        </p>
        ${ev.findings.length
          ? ev.findings.map((f) => html`
              <div key=${f.id} class="ev-finding">
                <div class="ev-finding-head">
                  <${Pill} cls=${SEVERITY_PILL[f.severity]}>${f.id} ${f.severity}<//>
                  <span class="ev-finding-title">${f.title}</span>
                </div>
                <p class="ev-finding-body">${f.detail}</p>
                ${f.evidence && html`<p class="mono ev-finding-cite">${f.evidence}</p>`}
              </div>`)
          : html`<p class="ev-none">No finding names ${id}.</p>`}
      </div>`,
  };

  const anchor = `${id} · evidence`;

  return html`
    <div class="wrap wide ev">
      <div class="cd-ask">
        <div class="cd-ask-head">
          <span class="mono">${id}</span>
          <span class="eyebrow">what you asked for</span>
        </div>
        <p class="cd-ask-text">${statement}</p>
      </div>

      <div class="ev-cols">
        <div class="ev-nav">
          <div class="eyebrow">Four kinds of evidence</div>
          ${KINDS.map((k) => html`
            <button key=${k.key} class=${`ev-item ${sel === k.key ? 'on' : ''}`}
                    onClick=${() => setSel(k.key)}>
              <span class=${`mono ev-mark ev-${k.tone}`}>${k.mark}</span>
              <span class="ev-item-title">${k.title}</span>
              <span class="ev-item-sub">${k.sub}</span>
            </button>`)}
          <p class="ev-aside">
            Evidence hangs off the criterion, not off the branch. Anything that cannot name a
            criterion shows up under <a href=${hrefs.map()}>asked for, and not built</a>${' '}instead.
          </p>
        </div>

        <div class="ev-pane">
          <div class="ev-pane-body">${PANES[sel]()}</div>
          <div class="ev-limits">
            <div class="eyebrow">What this does not prove</div>
            <p>${LIMITS[sel]}</p>
          </div>
          <div class="ev-file">
            <${FlagsHere} flags=${flags} anchor=${anchor} />
            <${Composer} compact onFile=${onFile} busy=${busy} anchor=${anchor}
              placeholder=${`What is wrong with the evidence for ${id}?`} />
          </div>
        </div>
      </div>
    </div>`;
}

/* --------------------------------------------------------------- run scope */

/* "N test(s) passed" is how the QA agent writes its evidence line, and the
   number in it is the only per-criterion case count there is. Parsed rather
   than recomputed, and absent rather than guessed: a band that invents a
   figure to look complete is the thing this screen exists against. */
const casesIn = (qa) => {
  const m = /^(\d+)\s+test/.exec(String((qa || {}).evidence || ''));
  return m ? Number(m[1]) : null;
};

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/* A band: where this came from, the artifacts, then what it does not prove.
   The contract the per-criterion screen already keeps, kept at run scope. */
function Band({ title, count, weight, warn, prov, limit, children }) {
  return html`
    <section class="ee-band">
      <div class="ee-band-h">
        <h2>${title}</h2>
        <span class="mono ee-band-n">${count}</span>
        <span class=${`ee-band-w ${warn ? 'warn' : ''}`}>${weight}</span>
      </div>
      <p class="ee-prov">${prov}</p>
      ${children}
      <div class="ee-limit">
        <div class="eyebrow">What this does not prove</div>
        <p>${limit}</p>
      </div>
    </section>`;
}

const Fig = ({ n, of, label, bad }) => html`
  <div class="ee-led">
    <span class=${`ee-led-n ${bad ? 'bad' : ''}`}>
      ${n}${of !== undefined && html`<span class="ee-of"> of ${of}</span>`}
    </span>
    <span class="ee-led-l">${label}</span>
  </div>`;

/* A probe's own text, under the row that reports it.
 *
 * Whole, not the first 24 lines the blind tests show. A blind test is read for
 * what it asserts and links out to the criterion's full evidence; a probe has
 * nowhere to link to -- it is not on the branch and will not be -- so a
 * truncation here is a reader who cannot finish the argument. They are small:
 * a typical probe runs to one or two thousand characters.
 */
function ProbeSource({ text, kept }) {
  const lines = String(text || '').split('\n');
  return html`
    <div class="ee-probe-src">
      <p class="ee-prov">
        ${kept
          ? html`This probe is on the branch — it is in the diff with the rest of the change.
              Shown here too, because the claim it settles is on this screen.`
          : html`This file is not on the branch. Probes are removed after they run so the diff a
              human reviews is the feature and not the attack surface, so this is the record of
              what was run.`}
      </p>
      <div class="ee-code">
        ${lines.map((l, i) => html`
          <div class="ee-line" key=${i}>
            <span class="mono ee-no">${i + 1}</span>
            <span class="mono ee-src">${l || ' '}</span>
          </div>`)}
      </div>
    </div>`;
}

/* A recording, walked through.
 *
 * The full viewer is a service-worker application that reads the archive
 * itself, which is why it cannot run inside this page and why, without this,
 * the only way into a recording is a download and a command. Most of the time
 * a reader does not want the viewer -- they want to see what the test did,
 * which is these frames in order with the name of the step each belongs to.
 * Both are in the archive already, on one clock, and are unpacked when it is
 * collected.
 *
 * Stepping, not playing, is the default. Thirteen frames of a five-second
 * test play in about a second and tell nobody anything; the value is in
 * stopping on the frame where the tag appears and reading the label that says
 * the test was filling the box.
 */
function TracePlayer({ url, frames, criteria = [], onClose }) {
  const [at, setAt] = useState(0);
  const [playing, setPlaying] = useState(false);
  const shots = frames.frames || [];

  useEffect(() => {
    if (!playing) return undefined;
    const tick = setInterval(() => setAt((i) => {
      if (i + 1 >= shots.length) { setPlaying(false); return i; }
      return i + 1;
    }), 600);
    return () => clearInterval(tick);
  }, [playing, shots.length]);

  useEffect(() => {
    const key = (e) => {
      if (e.key === 'ArrowRight') setAt((i) => Math.min(i + 1, shots.length - 1));
      if (e.key === 'ArrowLeft') setAt((i) => Math.max(i - 1, 0));
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [shots.length, onClose]);

  if (!shots.length) return null;
  const now = shots[Math.min(at, shots.length - 1)];

  return html`
    <div class="tp-wrap" onClick=${onClose}>
      <div class="tp" onClick=${(e) => e.stopPropagation()}>
        <div class="tp-head">
          <span class="tp-t">${criteria.length
            ? criteria.map((c) => c.id).join(', ')
            : (testNamed(frames.title) || 'a browser run')}</span>
          <button type="button" class="tp-x" onClick=${onClose}>close</button>
        </div>
        ${/* What was promised, above the thing that is supposed to show it --
             so a reader steps through the frames with the claim in front of
             them rather than in another tab. */''}
        ${criteria.filter((c) => c.title).map((c) => html`
          <p class="tp-ac" key=${c.id}>${c.title}</p>`)}
        ${criteria.length > 0 && testNamed(frames.title) && html`
          <p class="tp-test">${testNamed(frames.title)}</p>`}
        <img class="tp-img" src=${`${url}/frames/${now.file}`}
             alt=${`frame ${at + 1}: ${now.step}`} />
        <div class="tp-bar">
          <button type="button" class="tp-b" onClick=${() => setPlaying(!playing)}>
            ${playing ? 'pause' : 'play'}</button>
          <button type="button" class="tp-b"
                  onClick=${() => { setPlaying(false); setAt(Math.max(at - 1, 0)); }}>‹</button>
          <input class="tp-slide" type="range" min="0" max=${shots.length - 1} value=${at}
                 onInput=${(e) => { setPlaying(false); setAt(Number(e.target.value)); }} />
          <button type="button" class="tp-b"
                  onClick=${() => { setPlaying(false);
                                    setAt(Math.min(at + 1, shots.length - 1)); }}>›</button>
          <span class="mono tp-n">${at + 1}/${shots.length}</span>
        </div>
        ${/* What the test was doing when this frame was taken -- the step in
             flight, since paint lags the action that caused it. */''}
        <p class="tp-step"><b>${now.step || 'between steps'}</b></p>
        <p class="tp-note">Arrow keys step. ${' '}
          The frames are what the browser drew; for the page's own structure,
          the network and the console at each step, download the recording and
          open it with${' '}<span class="mono">npx playwright show-trace</span>.</p>
      </div>
    </div>`;
}

/* The criteria a test names itself with, kept to the ones this feature has.
   A Playwright title carries them -- `[AC-10, AC-11, AC-14, AC-16] a person
   can create and remove a tag` -- and intersecting with the spec is what stops
   a bracketed id from another project's convention being read as one of
   these. */
const criteriaIn = (title, ids) => {
  const said = String(title || '').toUpperCase();
  /* On a boundary, not as a substring: `AC-1` is inside `AC-13`, so a plain
     `includes` would label a recording of AC-13 with both. Sorting longest-first
     fixes the single-match case and cannot fix this one, which collects all
     of them. */
  return ids.filter((id) => new RegExp(`\\b${id.toUpperCase()}\\b(?!\\d)`).test(said));
};

/* The same criteria, each with the plain-language title the spec gave it.
   `criteriaIn` answers which ones a test named; an id on its own is a
   reference a reader has to go and resolve, and the title is the sentence
   they were going to resolve it into. Numerically ordered, because `AC-2`
   after `AC-10` is only how the id list happens to be sorted. */
const criteriaNamed = (title, ids, said) => criteriaIn(title, ids)
  .slice()
  .sort((a, b) => (parseInt(a.replace(/\D+/g, ''), 10) || 0)
                - (parseInt(b.replace(/\D+/g, ''), 10) || 0))
  .map((id) => ({ id, title: said[id.toUpperCase()] || '' }));

/* A Playwright title is addressed to a runner:
   `acceptance/tags.spec.ts:44 › [AC-13] the real Add tag control ...`.
   The path says which file to re-run, which is not a question anybody has
   while looking at the frames it produced, and the bracketed ids are the line
   directly above this one. What is left is the sentence the test's author
   wrote about what it does. */
const testNamed = (title) => {
  const said = String(title || '');
  const cut = said.lastIndexOf(' \u203a ');
  return (cut < 0 ? said : said.slice(cut + 3)).replace(/^\s*\[[^\]]*\]\s*/, '').trim();
};

export function RunEvidence({ data, traces, openWith, hrefs, flags, onFile, busy,
                              projectId, featureId }) {
  const d = data || {};
  const packet = packetOf(d);
  const gates = (d.gates || {}).results || [];
  const oracle = d.oracle || {};
  const tests = oracle.tests || [];
  const qa = (d.qa || {}).results || [];
  const breaker = d.breaker || null;
  const suite = d.breaker_suite || {};
  const qaFor = (ids) => qa.filter((r) => (ids || []).includes(r.criterion_id));
  const gatesPassed = gates.filter((g) => g.passed).length;
  const gateSeconds = gates.reduce((a, g) => a + (g.duration_s || 0), 0);
  const applied = (breaker || {}).tests_applied || [];
  const failing = (breaker || {}).failing || [];
  /* Longest id first, so `AC-11` is never read as `AC-1`. */
  const acIds = ((d.spec || {}).acceptance_criteria || [])
    .map((c) => c.id).filter(Boolean)
    .sort((a, b) => b.length - a.length);
  /* `title` and not `statement`: the spec keeps a plain-language name for a
     reader and a precise one for a test author, and this is a reader. */
  const acSaid = {};
  for (const c of (d.spec || {}).acceptance_criteria || []) {
    if (c.id) acSaid[String(c.id).toUpperCase()] = c.title || '';
  }

  /* The source is already here -- it arrives with the suite, so drawing it
     costs no request. Keyed by path, which is how the report names a probe. */
  const probeSource = {};
  ((suite || {}).tests || []).forEach((t) => { probeSource[t.path] = t.contents; });
  const noBase = gates.filter((g) => !g.at_base || g.at_base === 'not_checked').length;

  /* Absent rather than wrong: one criterion whose line the parser did not
     recognise takes the total out, instead of reporting a sum of the rest as
     if it were the whole. */
  const counts = qa.map(casesIn);
  const cases = counts.some((c) => c === null)
    ? null : counts.reduce((a, c) => a + c, 0);

  const recordings = traces || [];
  const items = recordings.length + gates.length + tests.length + applied.length;
  const traceUrl = (n) =>
    `/api/projects/${encodeURIComponent(projectId)}/features/${
      encodeURIComponent(featureId)}/traces/${encodeURIComponent(n)}`;
  /* The recording being walked through, and its frames. Fetched on the press
     rather than with the packet: a run with ten recordings should cost nothing
     until somebody opens one. */
  const [walk, setWalk] = useState(null);
  const openTrace = async (name) => {
    try {
      const frames = await request(`${traceUrl(name)}/player`);
      setWalk({ url: traceUrl(name), frames });
    } catch {
      setWalk(null);
    }
  };
  /* The absences. Every one is read off the same payload the bands above are,
     because a gap a reader has to infer is a gap nobody finds. */
  const rows = criteriaRows(d);
  const untested = rows.filter((r) => r.state === 'no_test');
  const seamless = !((d.integration || {}).seam_checks || []).length;
  const unmeasured = gates.every((g) => g.metric === null || g.metric === undefined);
  const unclaimed = (packet.unclaimed || []).length;
  const gaps = [
    seamless && ['No check at the seam', html`
      Units were built separately and integrated, and no check was left for the boundary between
      them. Nothing on this tab covers it.`, true],
    noBase > 0 && ['No gate read at the base commit', html`
      ${noBase === gates.length ? 'None of the' : `${noBase} of the`} ${gates.length} gates also
      ran at the base. ${gatesPassed === gates.length
        ? 'All are green, so nothing is hidden by that today — but a red one would be unreadable '
          + 'without it.'
        : 'A red gate cannot be told from one that was already red before this branch.'}`, true],
    untested.length > 0 && [`${plural(untested.length, 'criterion has', 'criteria have')} no blind test`,
      html`${untested.map((r) => r.criterion_id).join(', ')} — nothing independent verifies ${
        untested.length === 1 ? 'it' : 'them'}.`, true],
    !recordings.length && ['Nothing to watch', html`
      No browser test was recorded on this run, so nothing here can be checked by watching. For a
      change with nothing a person sees, that is expected.`, false],
    unmeasured && ['Nothing measured for speed or size', html`
      No gate on this project carries a metric or a threshold, so every figure here is pass or
      fail and none of them is a number.`, false],
    unclaimed > 0 && [`${plural(unclaimed, 'file', 'files')} nothing asked for`, html`
      Written and claimed by no criterion. ${' '}
      <a href=${hrefs.map()}>They are on the work map</a>, not here.`, false],
  ].filter(Boolean);

  return html`
    <div class="wrap wide ee">
      <p class="tab-lead">Everything this run produced that you can check for yourself.</p>

      <div class="ee-ledger">
        <${Fig} n=${recordings.length} label="recordings" />
        <${Fig} n=${gatesPassed} of=${gates.length} label="gates passed"
                bad=${gatesPassed < gates.length} />
        <${Fig} n=${tests.length} label="blind test files" />
        <${Fig} n=${cases === null ? '—' : cases} label="blind cases passed" />
        <${Fig} n=${failing.length} of=${applied.length} label="probes failing"
                bad=${failing.length > 0} />
        <${Fig} n=${`${Math.round(gateSeconds)}s`} label="spent measuring" />
        <${Fig} n=${gates.length - noBase} of=${gates.length} label="gates read at base"
                bad=${noBase === gates.length} />
      </div>

      ${/* ------------------------------------------------------ on screen */''}
      <${Band} title="On screen" weight="Fastest check · weakest proof" warn
        count=${recordings.length ? plural(recordings.length, 'recording', 'recordings') : 'none'}
        prov=${html`
          <b>The only evidence here a person can judge without reading code.</b> Left behind by
          the tests as they ran, in this feature's container, on this branch.`}
        limit=${html`
          A label says which criterion a test was <i>for</i>, taken from the name the test gave
          itself. Nothing checks that the frame shows it, and a criterion usually describes more
          than one state — so read the criterion, then look. One browser, one seeded database. A
          criterion with nothing to draw has nothing here, and so does one whose visible half is
          asserted below the browser; the blind tests band says which.`}>
        ${recordings.length > 0 ? html`
            <div class="ee-sheet">
              ${recordings.map((t) => html`
                <button key=${t.name} class="ee-shot" disabled=${t.frames === false}
                        onClick=${() => t.frames !== false && openTrace(t.name)}>
                  <span class="ee-shot-img">
                    ${t.poster
                      ? html`<img src=${`${traceUrl(t.name)}/frames/${t.poster}`}
                                  alt="last frame" loading="lazy" />`
                      : html`<span class="ee-shot-none">no frames</span>`}
                    <span class="ee-play" aria-hidden="true">▶</span>
                  </span>
                  <span class="ee-shot-meta">
                    ${/* The ids, and then what each of them promised. An id
                         alone tells a reader which criterion to go and look
                         up; the point of a recording is that they should not
                         have to leave it to find out what they are watching. */''}
                    <span class="ee-shot-t">${criteriaIn(t.title, acIds).join(', ')
                      || 'no criterion named'}</span>
                    ${criteriaNamed(t.title, acIds, acSaid)
                      .filter((c) => c.title)
                      .map((c) => html`
                        <span class="ee-shot-ac" key=${c.id}>${c.title}</span>`)}
                    ${/* Why the picture is blank, when it is. A test that died
                         in a fixture leaves one white frame, and a row of white
                         cards says nothing while the reason sits in each archive. */''}
                    ${t.error && html`
                      <span class="mono ee-shot-err">Stopped: ${t.error}</span>`}
                    <span class="mono ee-shot-src">${Math.round((t.bytes || 0) / 1024)} KB${' '}
                      · <a href=${traceUrl(t.name)} download
                           onClick=${(e) => e.stopPropagation()}>download</a></span>
                  </span>
                </button>`)}
            </div>`
          : html`<p class="ee-none">
              No browser test was recorded on this run. That is an absence, not a pass: nothing
              here can be checked by watching, and every other band asks you to read code or
              trust a model.
            </p>`}
      <//>

      ${/* ---------------------------------------------------------- gates */''}
      <${Band} title="The project's gates" weight="A fact"
        count=${`${plural(gates.length, 'command', 'commands')} · ${
          Math.round(gateSeconds)}s · ${gatesPassed === gates.length
            ? 'all passed' : `${gates.length - gatesPassed} failing`}`}
        warn=${gatesPassed < gates.length}
        prov=${html`
          The commands you approved at gate 0, run in this feature's container on this branch.
          ${' '}<b>No model is involved in that module and none should ever be</b> — a gate says a
          process exited zero, or that a number cleared a threshold. Open a row for the output
          that was kept.`}
        limit=${html`
          Nothing about whether the feature is right — only that a process exited zero.
          ${noBase > 0 && html`${' '}And ${noBase === gates.length ? 'none' : `${noBase}`} of these
            ${' '}${noBase === gates.length ? 'of them was' : 'were'} also run at the base commit,
            so a failure could not be told from one this branch inherited.`}`}>
        ${gates.length ? gates.map((g) => html`
          <details class="ee-gate" key=${g.name}>
            <summary>
              <span class=${`mono ee-mark ${g.passed ? 'ev-ok' : 'ev-bad'}`}>
                ${g.passed ? '✓' : '✗'}
              </span>
              <span class="mono ee-gname">${g.name}</span>
              <span class="mono ee-gcmd">${g.command}</span>
              <span class="mono ee-gnum">${Math.round(g.duration_s || 0)}s</span>
              <span class="ee-gbase">
                ${!g.at_base || g.at_base === 'not_checked' ? 'no base'
                  : g.at_base === 'failed_at_base' ? 'red at base' : g.at_base}
              </span>
            </summary>
            <div class="ee-out">
              <pre>${(g.output_tail || '').trim() || 'This gate kept no output.'}</pre>
            </div>
          </details>`)
          : html`<p class="ee-none">This project has no gates configured, so nothing here was
              measured by anything but a model.</p>`}
      <//>

      ${/* --------------------------------------------------------- guides */''}
      <${Band} title="Guides this feature was held to" weight="Written by people, read by agents"
        count=${(packetOf(data).guides || []).length
          ? plural(packetOf(data).guides.length, 'guide', 'guides') : 'none'}
        warn=${(packetOf(data).guides || []).some((g) => g.partial)}
        prov=${html`
          The repository's own AGENTS.md, DESIGN.md and skills, as committed where this feature
          branched from. Agents with no harness were handed them by code; a harness worker's own
          loader had them available. A reviewer who cited one quoted it, and code checked the quote.`}
        limit=${html`
          Being handed a guide is not following it. Whether the code does is what the findings
          that cite it say${(packetOf(data).guides || []).some((g) => g.partial)
            ? html` — and an agent marked <i>partial</i> saw only that guide's headings` : ''}.`}>
        ${(packetOf(data).guides || []).length ? html`
          <ul class="ee-guides">${packetOf(data).guides.map((g) => html`
            <li key=${g.path}><span class="mono">${g.path}</span>
              <span class="dim"> · ${[...(g.roles || []),
                ...(g.loader || []).map((r) => `${r} (its harness loads it)`)].join(', ')}</span>
              ${g.partial && html` <span class="ev-bad">· partial</span>`}
              ${g.changed && html` <span class="dim">· edited since approved</span>`}</li>`)}</ul>`
          : html`<p class="ee-none">This repository has no AGENTS.md, DESIGN.md or skills, so how
              the code is written was held to nothing written down — only to what a model saw.</p>`}
      <//>

      ${/* ---------------------------------------------------- blind tests */''}
      <${Band} title="Blind acceptance tests" weight="Strong, and narrow"
        count=${`${plural(tests.length, 'file', 'files')}${cases === null ? '' : ` · ${cases} cases`
          } · ${rows.length - untested.length} of ${rows.length} criteria`}
        warn=${untested.length > 0}
        prov=${html`
          Written by the oracle from the frozen spec alone, <b>before this code existed</b>, and it
          has never seen the implementation — ${' '}<span class="mono">verify_context</span> takes a
          spec and returns a string, with no second parameter.
          ${oracle.strategy && html`${' '}Strategy for this run: ${' '}
            <span class="mono">${oracle.strategy}</span>.`}
          ${' '}Each row names the criterion it was written for and opens that criterion's own
          evidence.`}
        limit=${html`
          Only the cases the oracle thought to write. A criterion can pass every blind test and
          still be wrong at a boundary nobody named — which is what a failing probe below is.`}>
        ${tests.length ? tests.map((t) => {
          const ids = t.criterion_ids || [];
          const mine = qaFor(ids);
          const n = mine.map(casesIn);
          const ran = n.some((x) => x === null) ? null : n.reduce((a, x) => a + x, 0);
          const ok = mine.length > 0 && mine.every((r) => r.status === 'passed');
          const shared = ids.filter((id) => tests.filter(
            (o) => (o.criterion_ids || []).includes(id)).length > 1);
          return html`
            <details class="ee-test" key=${t.path}>
              <summary>
                <span class=${`mono ee-mark ${ok ? 'ev-ok' : 'ev-amber'}`}>${ok ? '✓' : '!'}</span>
                <span class="mono ee-ac">${ids.join(' ') || '—'}</span>
                <span class="mono ee-path">${t.path}</span>
                <span class="mono ee-cases">
                  ${ran === null || shared.length
                    ? (mine[0] || {}).status || 'not run' : `${ran} passed`}
                </span>
              </summary>
              <div class="ee-body">
                ${shared.length > 0 && html`
                  <p class="ee-stmt ee-shared">
                    ${shared.join(', ')} ${shared.length === 1 ? 'is' : 'are'} covered by more than
                    one file${ran === null ? '' : html`, and the ${ran} passing cases are recorded
                      against the criterion rather than split between them`} — this file is one
                    tier of it, not the whole.
                  </p>`}
                ${ids.map((id) => html`
                  <p class="ee-stmt" key=${id}>
                    <b>${id}</b> ${statementOf(d, id)}${' '}
                    <a href=${hrefs.evidence(id)}>Everything that proves it →</a>
                  </p>`)}
                <div class="ee-code">
                  ${String(t.contents || '').split('\n').slice(0, 24).map((l, i) => html`
                    <div class="ee-line" key=${i}>
                      <span class="mono ee-no">${i + 1}</span>
                      <span class="mono ee-src">${l || ' '}</span>
                    </div>`)}
                </div>
                ${String(t.contents || '').split('\n').length > 24 && html`
                  <a class="ee-more" href=${hrefs.evidence(ids[0])}>
                    The whole file, beside everything else that proves ${ids[0]} →
                  </a>`}
              </div>
            </details>`;
        }) : html`<p class="ee-none">The oracle wrote no blind tests for this run. Nothing on this
            branch was checked against the spec by anything that had not already read the code.</p>`}
        ${(oracle.notes || '').trim() && html`
          <details class="ee-gate ee-lone">
            <summary>
              <span class="mono ee-mark">›</span>
              <span class="ee-gname">How the oracle says these fit together</span>
              <span class="ee-gcmd">which tier asserts what, and what it could not reach</span>
              <span class="ee-gnum"></span>
              <span class="ee-gbase">its words</span>
            </summary>
            <div class="ee-out"><p class="ee-notes">${oracle.notes.trim()}</p></div>
          </details>`}
      <//>

      ${/* --------------------------------------------------------- probes */''}
      <${Band} title="Breaker probes" weight="Only failures carry information"
        warn=${failing.length > 0}
        count=${breaker ? `${applied.length} applied · ${failing.length} failing` : 'none'}
        prov=${html`
          Written${' '}<b>after</b>${' '}reading the implementation, specifically to break it, then run. Its
          epistemics are the exact inverse of the oracle's: it has read everything, so a passing
          probe proves nothing at all.`}
        limit=${html`
          Probes never land on the branch and never enter the matrix. They are an attack record,
          not an acceptance argument — and a concession is one agent finding nothing in one round,
          not a guarantee that there is nothing to find.`}>
        ${!breaker
          ? html`<p class="ee-none">
              No breaker record for this run. That is not the same as nothing failing — it means
              nothing attacked this code, and this band is reporting an absence rather than a
              result.
            </p>`
          : html`
            ${(breaker.note || '').trim() && html`
              <p class="ee-prov ee-note">
                ${breaker.note.trim().replace(/^./, (c) => c.toUpperCase())}
              </p>`}
            ${suite.strategy && html`<p class="ee-prov ee-said">“${suite.strategy}”</p>`}
            ${/* A probe that stayed is the one thing on this screen a reader
                 will also meet in the diff, so it has to say so here. "Passing
                 proves nothing" is still true of it as evidence about the code
                 -- kept is not a claim that it found something, it is a claim
                 that it will go red again if the defect comes back. */''}
            ${/* Three states, not two. A probe killed on the clock is neither
                 failing nor passing: it reported on nothing, so it is not
                 evidence that the code is broken and not evidence that it is
                 sound. Shown amber for the same reason the Evidence tab's own
                 dot is amber -- nothing here stands against the change, but
                 something that was supposed to measure it did not. */''}
            ${/* The probe opens. A breaker finding is a claim that the code
                 fails a specific attack, and the attack is this file -- so the
                 two ways to judge the claim are to read it or to run it.
                 Running it is not on offer: the probe is deleted from the
                 branch on purpose (the diff a human reviews is the feature, not
                 the attack surface). So this screen shows the source it already
                 holds, the way the blind tests beside this band show theirs,
                 rather than only a path and a mark.

                 It is worth most on exactly the probes that are hardest to
                 believe. A probe that hangs, or one that fails for a reason
                 nobody can reconstruct, is where a reader most needs to see
                 what was actually run -- and a demonstration runs once and is
                 deleted, so this is the only record there will be. */''}
            ${applied.map((path) => {
              const red = failing.includes(path);
              const stuck = (breaker.timed_out || []).includes(path);
              const kept = (breaker.promoted || []).includes(path);
              const src = probeSource[path];
              const mark = html`
                <span class=${`mono ee-mark ${red ? 'ev-bad' : ''}`}>
                  ${red ? '✗' : stuck ? '⋯' : '—'}</span>
                <span class="mono ee-probe-t">${path}</span>
                <span class="mono ee-cases">
                  ${red ? 'failing — this is the signal'
                        : stuck ? 'never finished — reports nothing either way'
                        : kept ? 'passing — kept in this project\u2019s suite'
                               : 'passing — which proves nothing'}
                </span>`;
              const cls = `ee-probe ${red ? 'fail' : stuck ? 'stuck' : 'pass'}`;
              if (!src) {
                return html`<div class=${cls} key=${path}><div class="ee-probe-h">${mark}</div></div>`;
              }
              return html`
                <details class=${cls} key=${path}>
                  <summary class="ee-probe-h">${mark}</summary>
                  <${ProbeSource} text=${src} kept=${kept} />
                <//>`;
            })}
            ${(breaker.timed_out || []).length > 0 && html`
              <p class="ee-prov">
                ${breaker.timed_out.length} probe${breaker.timed_out.length === 1 ? '' : 's'}
                ${' '}${breaker.timed_out.length === 1 ? 'was' : 'were'} killed on the clock,
                so${' '}${breaker.timed_out.length === 1 ? 'it tested' : 'they tested'} nothing —
                neither the hypothesis nor its opposite. A probe that stops finishing is usually
                reporting about itself, so ${breaker.timed_out.length === 1 ? 'it is' : 'they are'}
                ${' '}not run again.
              </p>`}
            ${(breaker.quarantined || []).length > 0 && html`
              <p class="ee-prov">
                ${breaker.quarantined.length} probe${breaker.quarantined.length === 1 ? '' : 's'}
                ${' '}${breaker.quarantined.length === 1 ? 'was' : 'were'} not run here after
                being killed on the clock in an earlier round.
              </p>`}
            ${(breaker.promoted || []).length > 0 && html`
              <p class="ee-prov">
                ${breaker.promoted.length} probe${breaker.promoted.length === 1 ? '' : 's'}
                ${' '}passed the repaired code every time ${breaker.promoted.length === 1
                  ? 'it was' : 'they were'} asked to, and ${breaker.promoted.length === 1
                  ? 'is' : 'are'} kept in the project's own test suite — so ${
                  breaker.promoted.length === 1 ? 'it is' : 'they are'} in the diff below this
                packet, the same as any other test this feature added. Every other probe was
                deleted.
              </p>`}
            ${(breaker.retired || []).length > 0 && html`
              <p class="ee-prov">
                ${breaker.retired.length} probe${breaker.retired.length === 1 ? '' : 's'}
                ${' '}${breaker.retired.length === 1 ? 'was' : 'were'} not re-run here: a probe
                that proves a defect is present now stops meaning anything once the defect is
                gone. What ${breaker.retired.length === 1 ? 'it' : 'they'} found is above, and
                in the ledger.
              </p>`}
            ${(suite.conceded || []).length > 0 && html`
              <div class="ee-conceded">
                <div class="eyebrow">What the breaker tried and could not break</div>
                <ul>${suite.conceded.map((c, i) => html`<li key=${i}>${c}</li>`)}</ul>
              </div>`}
            ${(breaker.output_tail || '').trim() && html`
              <details class="ee-gate ee-lone">
                <summary>
                  <span class="mono ee-mark">›</span>
                  <span class="mono ee-gname">output</span>
                  <span class="mono ee-gcmd">${breaker.command || ''}</span>
                  <span class="mono ee-gnum">${Math.round(breaker.duration_s || 0)}s</span>
                  <span class="ee-gbase">exit ${breaker.exit_code}</span>
                </summary>
                <div class="ee-out"><pre>${breaker.output_tail.trim()}</pre></div>
              </details>`}`}
      <//>

      ${/* ------------------------------------------------------ the gaps */''}
      <section class="ee-band">
        <div class="ee-band-h">
          <h2>What this run did not produce</h2>
          <span class="mono ee-band-n">${plural(gaps.length, 'absence', 'absences')}</span>
          <span class="ee-band-w warn">Read this before you rule</span>
        </div>
        <p class="ee-prov">
          The bands above are everything that exists. These are the checks a reader might
          reasonably assume happened and that did not, each read off the same record. An absence is
          not a failure — it is a thing the packet cannot speak to.
        </p>
        ${gaps.length
          ? html`<div class="ee-gaps">
              ${gaps.map(([title, body, hard]) => html`
                <div class=${`ee-gap ${hard ? 'hard' : ''}`} key=${title}>
                  <b>${title}</b><p>${body}</p>
                </div>`)}
            </div>`
          : html`<p class="ee-none">Nothing this screen knows how to look for is missing. That is
              a statement about this screen's list, not about the run.</p>`}
      </section>

      <div class="ee-file">
        <${FlagsHere} flags=${flags} anchor="evidence" />
        <${Composer} compact onFile=${onFile} busy=${busy} anchor="evidence"
          placeholder="What is missing from the evidence for this run?" />
      </div>

      ${walk && html`
        <${TracePlayer} url=${walk.url} frames=${walk.frames}
                        criteria=${criteriaNamed(walk.frames.title, acIds, acSaid)}
                        onClose=${() => setWalk(null)} />`}

    </div>`;
}
