/* The screens this console builds with a reconciler, and the state they share.
 *
 * They share it because a review is one act. A comment you file on a file in
 * screen two has to be on screen four before you decide where the review goes,
 * or the last screen is lying about what you found.
 *
 * app/render.js hands `#main` over for these routes and takes it back on the way out.
 * Everything else in the console is still built from a string, which is fine
 * for reading and impossible for typing.
 */

import { html, render, useEffect, useState } from '../vendor/preact-htm.module.js';
import { Empty, FocusTools, STAGES, base, send } from './shared.js';
import { BlindSpots } from './blindspots.js';
import { WorkMap } from './workmap.js';
import { Worklist } from './worklist.js';
import { FileView } from './file.js';
import { Criterion } from './criterion.js';
import { Evidence, RunEvidence } from './evidence.js';
import { Questions } from './questions.js';
import { Findings } from './findings.js';
import { Repairs } from './repairs.js';
import { Dependencies } from './dependencies.js';
import { Start } from './start.js';
import { Tabs, Verdict, tabsFor } from './tabs.js';

/* A packet being read while a run is measuring the same branch again. Every
   figure on these screens -- gates passing, blockers open, minutes budgeted --
   belongs to the pass that produced the packet, and the running pass exists to
   replace them. Drawn once here rather than in each screen: they all show the
   same superseded numbers, and any one of them left unmarked is the one someone
   reads. */
const SETTLED = Object.keys(STAGES).filter((id) => STAGES[id].settled);

function Superseded({ data, projectId, featureId }) {
  const feature = (data || {}).state;
  if (!feature || SETTLED.includes(feature.stage)) return null;
  /* Not on every unsettled screen: that includes gate 1, where no packet has
     ever existed, and "This packet is being replaced" there would announce the
     replacement of a thing the feature has not produced yet. There has to be
     one to replace.

     `feature` is the state object and carries no phases, so the running station
     is read from the phases on the payload beside it. */
  if (!data.packet) return null;
  const running = (data.phases || []).find((p) => p.status === 'running');

  /* "A run is measuring this branch now" cannot be the else-branch for every
     stage but `failed`, because that never asks whether anything is running.
     A reopened spec sits at `awaiting_answers` with no owner and no phase in
     flight: nothing is measuring anything, the interrogator has asked and
     stopped, and the one thing that moves it is a person answering. A strip
     saying a run is under way, beside a station lamp saying "questions ·
     you", would describe two different situations between them. */
  const WAITS = {
    awaiting_answers: ['questions', 'Answer the questions',
      'The interrogator has asked what it needs and stopped. Nothing is running: this moves when '
      + 'you answer.'],
    awaiting_spec_approval: ['spec', 'Read the spec',
      'A new spec is drafted and frozen as a draft. Nothing is running: this moves when you '
      + 'approve it or send it back.'],
  };
  const waiting = !feature.owner && WAITS[feature.stage];
  const f = (v) => `#/${encodeURIComponent(projectId)}/${encodeURIComponent(featureId)}?${v}`;

  return html`
    <div class=${`wrap ${waiting ? 'is-waiting' : ''}`}>
      <div class="superseded-strip">
        <div>
          <b>${waiting ? 'This packet is being replaced, and it is waiting for you.'
            : 'This packet is being replaced.'}</b>${' '}
          ${waiting ? waiting[2]
            : feature.stage === 'failed'
              ? 'A run was started over this branch and stopped before it produced one.'
              : 'A run is measuring this branch now.'}
          ${!waiting && running && html`${' '}Currently: <span class="mono">${running.name}</span>.`}
          ${' '}Everything below is the previous reading, kept because it is still the only
          packet there is.
        </div>
        <a class=${`btn btn-sm ${waiting ? 'btn-primary' : ''}`}
           href=${f(waiting ? waiting[0] : 'build')}>
          ${waiting ? waiting[1] : 'Watch the run'}
        </a>
      </div>
    </div>`;
}

/* A run in flight over this branch, with a packet already on the page. The
   packet is then a reading of the pass before this one, and the screen it is
   on is not a review screen any more -- it is a screen about a run, which the
   product already has words and a bar for. It wears that bar here rather than
   its own: left at the top in full dress, the last packet would state its
   verdict in the present tense over figures the pass under way exists to
   replace, and a reader who arrived mid-run would read last time's answer as
   this time's.

   Stalled runs are left alone. `En route` is a claim that something is working,
   and when the process that owned the run is gone nothing is -- the strip below
   keeps that case, because the packet is all there is to look at until somebody
   resumes it. */
const buildInFlight = (data) => {
  const feature = (data || {}).state;
  if (!feature || !data.packet) return false;
  return feature.stage === 'building' && !data.orphaned;
};

function EnRoute({ data, href, open, onToggle }) {
  const phases = data.phases || [];
  /* While a run is building nothing is skipped yet -- a conditional station
     that will turn out to have had nothing to do is still owed at this point --
     so the denominator is every station on the line. */
  const done = phases.filter((p) => p.status === 'done').length;
  const now = phases.find((p) => p.status === 'running');

  return html`
    <section class="stopbar normal">
      <div class="stopbar-in">
        <span class="stopbar-w">En route</span>
        <div><p class="verdict-headline">
          <b>No input is needed until this finishes.</b>${' '}
          ${done} of ${phases.length} stations complete${now && html`, now at <b>${now.name}</b>`}.
          ${' '}The next thing you will be asked is whether to release the line.
        </p></div>
      </div>
      <div class="stopbar-foot">
        <a href=${href}>Watch the run</a>
        ${/* The previous packet is kept, because it is still the only reading
             of this branch there is and somebody may want to know what the run
             is being asked to fix. It is one press away and not on the way. */''}
        <button type="button" class="stopbar-link" onClick=${onToggle}>
          ${open ? 'Hide the previous run' : 'Review the previous run'}
        </button>
      </div>
    </section>`;
}

function Screen({ view, param, projectId, featureId, initial, hrefs, onReload, reading,
                 prevOpen, onPrev }) {
  const [flags, setFlags] = useState(initial.flags || []);
  const [plan, setPlan] = useState(initial.plan || { route: 'accept', because: '' });
  const [budget, setBudget] = useState(initial.budget || null);
  const [rulings, setRulings] = useState((initial.data || {}).rulings || []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [sent, setSent] = useState('');

  /* A poll or an SSE tick arrives as new props rather than as fresh innerHTML,
     and the reconciler keeps every node it can reuse -- so whatever you are
     typing into survives the refresh. The stamp is derived from the content, so
     a refresh that changed nothing does not reset what you are in the middle of. */
  useEffect(() => {
    if (initial.flags) setFlags(initial.flags);
    if (initial.plan) setPlan(initial.plan);
    if (initial.budget) setBudget(initial.budget);
    if (initial.data && initial.data.rulings) setRulings(initial.data.rulings);
  }, [initial.stamp]);

  const act = async (fn) => {
    setBusy(true); setError('');
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const onFile = (body) => act(async () => {
    const res = await send(`${base(projectId, featureId)}/flags`, body);
    setFlags((xs) => [...xs, res.flag]);
    setPlan(res.plan);
  });

  /* A ruling is recorded, never applied: it says a human saw this choice and
     let it stand, or did not. Held locally as well as posted so the row settles
     the moment you press it -- the poll that would otherwise carry it back is
     up to five seconds away, and a button that looks inert gets pressed twice. */
  const onRule = (decisionId, ruling) => act(async () => {
    await send(`${base(projectId, featureId)}/rulings`, { decision_id: decisionId, ruling });
    setRulings((xs) => [...xs.filter((r) => r.decision_id !== decisionId),
                        { decision_id: decisionId, ruling, at: new Date().toISOString() }]);
  });

  const onRetag = (id, disposition) => act(async () => {
    setFlags((xs) => xs.map((f) => (f.id === id ? { ...f, disposition } : f)));
    const res = await send(
      `${base(projectId, featureId)}/flags/${encodeURIComponent(id)}`, { disposition });
    setPlan(res.plan);
  });

  /* No route argument: the server recomputes it from the flags and acts on
     that, so this cannot ask for a destination the flags do not support. */
  const onDispatch = () => act(async () => {
    const res = await send(`${base(projectId, featureId)}/dispatch`);
    setBudget(res.budget);
    setSent(res.dispatched);
  });

  const shared = { data: initial.data, hrefs, flags, onFile, busy, run: initial.run };
  /* The screens read rulings from `data`, so the local copy is substituted
     rather than passed beside it -- two sources for one fact is how a row
     comes to disagree with the count above it. */
  const withRulings = initial.data ? { ...initial.data, rulings } : initial.data;
  const packetShared = { ...shared, data: withRulings, projectId, featureId,
                         editor: initial.editor, onRule };

  /* A figure in the verdict band that something discounts opens the tab holding
     the discount, with that entry already open. The band belongs to no tab, so
     it has to be able to reach into one. */
  const [spot, setSpot] = useState(null);
  const toBlindSpot = (id) => { setSpot(id); window.location.hash = hrefs.blindspots().slice(1); };

  const screen = () => {
    if (view === 'start') {
      return html`
        <${Start} project=${initial.project} features=${initial.features}
                  drift=${initial.drift} busy=${busy}
                  onCreate=${(body) => act(async () => {
                    const id = await send(
                      `/api/projects/${encodeURIComponent(projectId)}/features`, body);
                    window.location.hash =
                      `#/${encodeURIComponent(projectId)}/${encodeURIComponent(id.feature_id)}`;
                  })} />`;
    }
    if (view === 'questions') {
      return html`<${Questions} data=${initial.data} plan=${initial.plan_estimate}
                                browsing=${initial.browsing} onReload=${onReload} />`;
    }
    if (!initial.data || !initial.data.packet) {
      return html`<${Empty}>This feature has no packet yet.<//>`;
    }
    switch (view) {
      case 'criterion': return html`<${Criterion} ...${shared} id=${param} />`;
      case 'evidence': return param
        ? html`<${Evidence} ...${shared} id=${param} />`
        : html`<${RunEvidence} ...${shared}
                               traces=${initial.traces} openWith=${initial.tracesOpenWith}
                               projectId=${projectId} featureId=${featureId} />`;
      case 'calls': return html`
        <${Worklist} data=${initial.data} flags=${flags} plan=${plan} budget=${budget}
                     busy=${busy} sent=${sent} onFile=${onFile} onDispatch=${onDispatch}
                     onRetag=${onRetag} hrefs=${hrefs} />`;
      case 'map': return html`<${WorkMap} data=${initial.data} hrefs=${hrefs} />`;
      case 'file': return html`<${FileView} ...${packetShared} path=${param} />`;
      case 'objections': return html`<${Findings} ...${shared} />`;
      case 'dependencies': return html`<${Dependencies} data=${initial.data} hrefs=${hrefs} />`;
      case 'blindspots': return html`
        <${BlindSpots} data=${initial.data} focus=${spot} onFile=${onFile} busy=${busy} />`;
      /* No flags, plan, budget or dispatch: the way out of the packet is on
         Your calls, whose foot is built to hold both of them, and a second
         copy of it here would be one too many. */
      case 'rework':
      case 'repairs': return html`
        <${Repairs} data=${initial.data} busy=${busy} onRule=${onRule} />`;
      /* A packet with no view in the hash lands on the work, not on the
         record of it. */
      default: return html`
        <${Worklist} data=${initial.data} flags=${flags} plan=${plan} budget=${budget}
                     busy=${busy} sent=${sent} onFile=${onFile} onDispatch=${onDispatch}
                     onRetag=${onRetag} hrefs=${hrefs} />`;
    }
  };

  /* A review is one act, and reading mode has to survive walking through it:
     the landing screen, a criterion, its evidence, and where the review goes.
     A screen that drew no button would drop the mode the moment you opened it.
     The two that are forms rather than documents keep their chrome. */
  const canRead = view !== 'start' && view !== 'questions';

  /* Gate 1 is not a packet: it has no verdict to state and nothing to tab
     between. Everything downstream of a packet shares one control. */
  const tabbed = initial.data && initial.data.packet
    && view !== 'start' && view !== 'questions';

  /* The bar takes the top of the screen from the verdict band while a run is in
     flight, and the packet under it is folded away until it is asked for. */
  const flight = tabbed && buildInFlight(initial.data);
  const folded = flight && !prevOpen;

  return html`
    <div class=${tabbed ? 'g2' : ''}>
      ${canRead && !tabbed && html`<${FocusTools} on=${reading} />`}
      ${error && html`<div class="wrap"><div class="rw-error">${error}</div></div>`}
      ${flight
        ? html`<${EnRoute} data=${initial.data} open=${prevOpen} onToggle=${onPrev}
                           href=${`#/${encodeURIComponent(projectId)}/${
                             encodeURIComponent(featureId)}?build`} />`
        : html`<${Superseded} data=${initial.data}
                              projectId=${projectId} featureId=${featureId} />`}
      ${flight && prevOpen && html`
        <div class="wrap"><p class="prev-note">Everything below is the previous run's
          reading — the verdict, the figures, the findings — kept because it is still the
          only packet there is. The pass under way exists to replace it.</p></div>`}
      ${!folded && tabbed && html`
        <${Verdict} data=${initial.data} onDiscount=${toBlindSpot} reading=${reading}
                    run=${initial.run} hrefs=${hrefs} />
        <${Tabs} tabs=${tabsFor(initial.data, flags, initial.traces)} view=${view}
                param=${param} hrefs=${hrefs} />`}
      ${!folded && screen()}
      ${/* `calls` is excluded along with `repairs`/`rework`/`questions`: the
           worklist already ends with its own <Route>, so this banner would sit
           right under a screen already answering "where this review goes" and
           just repeat the question. */''}
      ${!folded && !['repairs', 'rework', 'questions', 'calls'].includes(view)
        && flags.length > 0 && html`
        <div class="wrap">
          <a class="g2-toreview" href=${hrefs.repairs()}>
            ${flags.length} thing${flags.length === 1 ? '' : 's'} flagged —
            see where this review goes →
          </a>
        </div>`}
    </div>`;
}

let host = null;

export function mount(node, props) {
  // Preact appends to a host it did not itself fill, so anything the router
  // left behind -- its "Loading…" placeholder, or a previous screen's markup --
  // has to go before the first render into this node.
  if (node !== host) node.textContent = '';
  render(html`<${Screen} ...${props} />`, node);
  host = node;
}

export function unmount() {
  if (!host) return;
  render(null, host);
  host = null;
}

export const isMounted = () => host !== null;
