/* The progress stream and the wait for a server that went away, then the
   boot: the first route, the subscription, and the server's config. Loaded last. */

'use strict';

/* -------------------------------------------------------- progress stream */

/* Waiting for the server to come back, and putting the page back together when
   it does.

   `EventSource` reconnects on its own while the connection merely drops, and
   gives up when there is nothing listening -- which is exactly the case a
   restart produces. Even where it recovers it only delivers what happens next:
   the data that failed to load while the server was down stays missing, so the
   screen keeps whatever it was showing when it broke until somebody reloads.

   So the stream is torn down and rebuilt with the rest, once the server answers
   a request again. */
let offlineTimer = null;
let stream = null;

function goOffline() {
  if (state.offline) return;
  state.offline = true;
  if (stream) { try { stream.close(); } catch (_) { /* already gone */ } stream = null; }
  paintOfflineBar();
  waitForServer();
}

function waitForServer() {
  clearTimeout(offlineTimer);
  offlineTimer = setTimeout(async () => {
    try {
      const answer = await fetch('/api/version', { cache: 'no-store' });
      if (!answer.ok) throw new Error('not ready');
    } catch (_) {
      waitForServer();
      return;
    }
    comeBack();
    subscribe();
    route();
  }, 1500);
}

function comeBack() {
  clearTimeout(offlineTimer);
  if (!state.offline) return;
  state.offline = false;
  state.loadError = null;
  state.projectError = null;
  paintOfflineBar();
}

function paintOfflineBar() {
  const el = document.getElementById('offline-bar');
  if (!el) return;
  el.hidden = !state.offline;
  el.innerHTML = state.offline ? `
    <div class="stale-strip">
      <span class="ss-lamp" aria-hidden="true">${icon('l-circle-alert', 'ic')}</span>
      <div>
        <b>The server is not answering.</b> Nothing has been lost &mdash; the ledger is a file
        on disk and this page is the only thing that cannot reach it. Trying again every
        second; the screen fills itself back in when it comes back.
      </div>
    </div>` : '';
}

function subscribe() {
  if (!window.EventSource) return;
  if (stream) { try { stream.close(); } catch (_) { /* already gone */ } }
  const source = new EventSource('/api/events');
  stream = source;
  let pending = null;
  source.onmessage = (message) => {
    let event;
    try { event = JSON.parse(message.data); } catch (_) { return; }
    // A project-level run -- the checks, a reading -- reports itself here and
    // nowhere else. It changes no stage, writes no record until it finishes,
    // and the POST that starts it returns in milliseconds, so without this the
    // page has nothing to show for the half-minute it takes and looks idle.
    // A reading of the as-built is its own tab's business, and never makes the
    // rest of the project page say its checks are running.
    const asBuilt = asBuiltEvent(event);
    if (!asBuilt && !event.feature_id && event.project_id === state.projectId) {
      state.projectRun = ['running', 'started'].includes(event.status)
        ? { phase: event.phase, detail: event.detail || '' }
        : null;
    }
    // Anything, anywhere, changes what the rail should say.
    const mine = state.id && event.feature_id === state.id;
    clearTimeout(pending);
    pending = setTimeout(() => (mine ? refresh(true) : refreshProject(true)), 250);
  };
  /* The browser retries a dropped connection by itself, and stops once there is
     nothing to connect to. `readyState === CLOSED` is that second case, and is
     the one a restart causes. */
  source.onerror = () => { if (source.readyState === EventSource.CLOSED) goOffline(); };
}

/* A move inside the as-built -- `&ab=` and nothing else -- is a page of one
   tab, not a new screen: it redraws in place. Routed like any other change it
   reloaded the whole project and blanked the page to "Loading…" on every
   click through the map. */
const abBare = (hash) => hash.replace(/&ab=[^&]*/, '');
let routedHash = abBare(location.hash);
window.addEventListener('hashchange', () => {
  const bare = abBare(location.hash);
  if (bare === routedHash && state.asBuilt && state.project) { render(); return; }
  routedHash = bare;
  closePanel();
  route();
});
route();
subscribe();

/* A machine nobody has set up, with nothing in the yard, opens on setup rather
   than on an empty yard -- once. Finishing or skipping setup records that, and
   a link to any other page is followed as given. */
api('/api/setup/brief').then((b) => {
  if (!b.finished && !b.projects && !location.hash.replace(/^#\/?/, '')) location.hash = SETUP_HREF;
}).catch(() => {});

/* The editor template the review opens files with. Once, at boot, and never
   awaited by anything: the screens that want it render fine without it and
   pick it up on the next render, which is what a refresh already triggers. */
api('/api/config').then((c) => { state.config = c; render(); }).catch(() => {});
