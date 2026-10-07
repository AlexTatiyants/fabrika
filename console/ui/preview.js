/* The running app, at review.

   A person is asked to rule on criteria nobody tested, and on an argument that
   a page shows something. This lets them look: the feature's own branch,
   started the way the checks start it, on this machine's loopback. Opening it
   is recorded; whether anything works is still theirs to say.

   Drawn in two places from one state: in the heading of "Check these
   yourself", where the looking is for, and as one control on the verdict band.
   The state rides on the feature payload, so the event stream that refreshes
   the screen carries every step of a start. */

import { html, useEffect, useState } from '../vendor/preact-htm.module.js';
import { base, request } from './shared.js';

const ACTIVE = ['starting', 'open'];

export const previewOf = (data) => (data && data.preview) || null;

/* The held preview, when it is starting or open. */
export function livePreview(data) {
  const p = previewOf(data);
  return p && p.held && ACTIVE.includes(p.held.status) ? p.held : null;
}

const minutes = (s) => {
  const m = Math.max(1, Math.round((s || 0) / 60));
  return `about ${m} minute${m === 1 ? '' : 's'}`;
};

const call = (url, method) => request(url, { method });

/* A start asked for and not yet reported. The server's next push to say
   "starting" comes a noticeable moment after the press -- long enough to
   wonder whether it registered, if the spinner waited for it. So a press
   shows the start at once, in every control drawn for this feature, and hands
   over to the server's own state the moment that reports a newer start (its
   `started_at` moves) or the request fails. Shared across the controls
   because the heading and the verdict band are separate components. */
const asked = new Map();          // feature url -> the started_at seen when pressed
const watchers = new Set();
const announce = () => watchers.forEach((w) => w());

function useAsked(url, held) {
  const [, redraw] = useState(0);
  useEffect(() => {
    const w = () => redraw((n) => n + 1);
    watchers.add(w);
    return () => watchers.delete(w);
  }, []);
  // The server has spoken: a newer start, or one already starting or open.
  if (asked.has(url) && ((held && held.started_at || null) !== asked.get(url)
                         || (held && ACTIVE.includes(held.status)))) {
    asked.delete(url);
  }
  return asked.has(url);
}

function usePreviewActions(data) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const st = (data && data.state) || {};
  const url = `${base(st.project_id, st.feature_id)}/preview`;
  const held = (previewOf(data) || {}).held;
  const pending = useAsked(url, held);
  const run = (method) => async () => {
    setBusy(true); setError('');
    if (method === 'POST') { asked.set(url, (held && held.started_at) || null); announce(); }
    try {
      await call(url, method);
    } catch (e) {
      setError(e.message);
      if (asked.delete(url)) announce();
    } finally { setBusy(false); }
  };
  return { busy, error, pending, open: run('POST'), stop: run('DELETE') };
}

const short = (sha) => (sha || '').slice(0, 7);
const bare = (url) => (url || '').replace(/^https?:\/\//, '');

/* In the heading of "Check these yourself": every state of the preview, said
   in one line at the right of the heading. Not a band of its own between the
   heading and the first criterion -- a button, a sentence about what it does,
   a line about the last time it was tried -- since those sentences fit in the
   control's tooltip. Only trouble takes a line of its own: an error, or what
   a failed start printed. */
export function PreviewInline({ data }) {
  const p = previewOf(data);
  const { busy, error, pending, open, stop } = usePreviewActions(data);
  if (!p) return null;
  const held = p.held;
  const live = livePreview(data) || (pending ? { status: 'starting' } : null);
  const help = html`<a href="#" class="pv-help" data-help-open="running-the-app">how it works</a>`;
  const trouble = (failed) => html`
    ${failed && html`<p class="pv-trouble pv-err">It didn't open: ${held.problem}</p>`}
    ${failed && held.log && html`
      <details class="pv-trouble pv-log"><summary>What it printed</summary>
        <pre class="mono">${held.log}</pre></details>`}
    ${error && html`<p class="pv-trouble pv-err">${error}</p>`}`;

  if (!p.available && !live) {
    return html`<span class="pv-inline pv-off" title=${p.reason}>No app to open here · ${help}</span>`;
  }
  if (live && live.status === 'open') {
    return html`
      <span class="pv-inline" role="status" aria-live="polite">
        <a class="btn btn-primary btn-sm" href=${live.url} target="_blank" rel="noopener noreferrer"
           title=${`Running from this branch${live.commit ? ` at ${short(live.commit)}` : ''}. It can't reach anything outside this machine, and closes after ${Math.round((p.idle_s || 3600) / 60)} minutes with this page closed, and when you rule.${p.note ? ` ${p.note}` : ''}`}
           >Open ${bare(live.url)} ↗</a>
        <button type="button" class="wl-act" disabled=${busy} onClick=${stop}>Stop</button>
      </span>${trouble(false)}`;
  }
  if (live) {
    return html`
      <span class="pv-inline" role="status" aria-live="polite">
        <span class="pv-spin" aria-hidden="true"></span>
        <span title=${`It takes ${minutes(p.estimate_s)}, as long as setup took last run.`}>Starting the app${
          live.step ? `: ${live.step}` : ''}…</span>
        <button type="button" class="wl-act" disabled=${busy} onClick=${stop}>Stop</button>
      </span>${trouble(false)}`;
  }
  const failed = held && held.status === 'failed';
  const lastTime = p.probe && !p.probe.ok && !failed;
  return html`
    <span class="pv-inline">
      <button type="button" class="btn btn-sm" disabled=${busy} onClick=${open}
              title=${`Starts this branch the way the checks do, on this machine only. It takes ${minutes(p.estimate_s)}.`}
              >${failed ? 'Try opening the app again' : 'Open the app'}</button>
      ${lastTime && html`<span class="pv-warn" title=${`When the project was last read: ${p.probe.problem}`}
                          >didn't open on main last time</span>`}
      ${help}
    </span>${trouble(failed)}`;
}

/* One control on the verdict band. Nothing when there is nothing to open. */
export function PreviewButton({ data }) {
  const p = previewOf(data);
  const { busy, pending, open } = usePreviewActions(data);
  const live = livePreview(data) || (pending ? { status: 'starting' } : null);
  if (!p || (!p.available && !live)) return null;
  if (live && live.status === 'open') {
    return html`<a class="vs-btn pv-band" href=${live.url} target="_blank"
                   rel="noopener noreferrer" title="The app, running from this branch">
                  App ↗</a>`;
  }
  if (live) {
    return html`<span class="vs-btn pv-band" aria-live="polite">
                  <span class="pv-spin" aria-hidden="true"></span>Starting the app…</span>`;
  }
  return html`<button type="button" class="vs-btn pv-band" disabled=${busy} onClick=${open}
                      title="Start this branch's app, the way the checks do">Open the app</button>`;
}
