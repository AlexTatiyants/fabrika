/* The small things everything uses: escaping, relative times and durations,
   the toast, and `api`, the one door to the server. */

'use strict';

/* ------------------------------------------------------------------ utils */

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

function rel(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const s = Math.max(0, (Date.now() - then) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function dur(seconds) {
  const s = Math.round(seconds || 0);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
  /* `9298m 34s` is six days said in minutes. Past an hour the seconds stop
     carrying anything and the larger unit is the one being asked about. */
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${String(m % 60).padStart(2, '0')}m`;
  return `${Math.floor(h / 24)}d ${String(h % 24).padStart(2, '0')}h`;
}

/* One dark pill for everything gets three things wrong, and all three cost the
   same press: it looks identical whether it says "Saved." or "That button
   failed", it sits at the bottom of the viewport far from anything just
   clicked, and it clears itself in under four seconds -- so the one message
   worth reading is the one most likely to be missed.

   So: a failure is red, carries the word, and does not leave until it is
   dismissed. Everything else keeps clearing itself, because a confirmation
   nobody reads is not a loss. Both land in the same place, under the masthead,
   because a reader learns one place to look and not two. And they stack rather
   than overwrite -- or two failures in a row would read as one. */
function toast(message, kind = 'info') {
  const note = document.createElement('div');
  note.className = `toast toast-${kind === 'error' ? 'error' : 'info'}`;
  if (kind === 'error') note.setAttribute('role', 'alert');

  const body = document.createElement('div');
  body.className = 'toast-body';
  if (kind === 'error') {
    const tag = document.createElement('b');
    tag.className = 'toast-tag';
    tag.textContent = 'Failed';
    body.appendChild(tag);
  }
  const text = document.createElement('span');
  text.textContent = message;
  body.appendChild(text);
  note.appendChild(body);

  const close = document.createElement('button');
  close.className = 'toast-x';
  close.type = 'button';
  close.setAttribute('aria-label', 'Dismiss');
  close.textContent = '×';
  close.addEventListener('click', () => note.remove());
  note.appendChild(close);

  // Oldest first, so a stack reads top to bottom in the order things happened.
  toastEl.appendChild(note);
  // A failure stays. Anything else is gone in six seconds -- long enough to
  // read a sentence, which under four seconds is not.
  if (kind !== 'error') setTimeout(() => note.remove(), 6000);
  // Not unbounded. A poll failing every two seconds must not build a column
  // that covers the page it is complaining about.
  while (toastEl.children.length > 4) toastEl.firstElementChild.remove();
  return () => note.remove();
}

/* Named rather than a second argument at every call site: the callers that
   should be red are the ones in a `catch`, and they are easier to find and
   harder to get wrong when the name says it. */
function errorToast(message) { return toast(message, 'error'); }

async function api(path, options) {
  /* `fetch` rejects when there was no answer at all -- the server is stopped,
     restarting, or unreachable. That is a different fact from any status it
     could have returned, and treating it as one makes restarting the server
     produce "There is no such project in the yard. It may have been removed":
     a message about the ledger, on a screen whose ledger is untouched.

     Every restart does this, and this app asks for restarts by design -- the
     stale-code bar exists to ask for them. */
  let response;
  try {
    response = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
  } catch (_) {
    goOffline();
    const err = new Error('the server is not answering');
    err.offline = true;
    throw err;
  }
  // Any answer, of any status, means it is back.
  if (state.offline) comeBack();
  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (_) { body = { detail: text }; }
  if (!response.ok) {
    const detail = body && body.detail;
    throw new Error(
      typeof detail === 'string' ? detail
        : (detail && detail.message) || `HTTP ${response.status}`,
    );
  }
  return body;
}
