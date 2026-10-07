/* The two headers -- the masthead and the feature bar's place in the process --
   and the colour scheme picker at the end of the masthead. */

'use strict';

/* ------------------------------------------------------------ two headers

   Tier 1 is the masthead: where you are in the tool. It is on every page and
   it is the only navigation that never moves.

   Tier 2 is the feature bar: where this feature is in the process, what it is
   called, and everything you can do to it right now. Position and identity on
   the left, actions on the right. Nothing else earns a row -- a sentence
   explaining a button belongs on the button.
*/

function renderCrumbs() {
  if (crumbsEl) {
    // The board's title says where you are and #mast-nav says where you can
    // go. A breadcrumb here would be the third of three navigations on one screen.
    crumbsEl.innerHTML = '';
    // Narrow enough to scroll means the tail -- where you actually are -- is the
    // end that matters.
    crumbsEl.scrollLeft = crumbsEl.scrollWidth;
  }
  if (!mastNavEl) return;
  mastNavEl.innerHTML = navLinks();
}

function navLinks() {
  /* Every place you can go, once, in the words this world uses. Three
     navigation elements on screen at the same time -- these links, a
     breadcrumb, and the board's own row -- would offer four routes to the home
     screen under three different names. "The" is left off the two words this
     product actually coined: it is not load-bearing, and it is the cheapest
     width to give back with a third global link beside them. */
  const links = [
    `<a href="#/" class="${!state.projectId
      && !['roles', 'agent-config', 'control-room', 'help', 'setup'].includes(state.view) ? 'on' : ''}">yard</a>`,
    `<a href="${ROLES_HREF}" class="${['roles', 'agent-config'].includes(state.view) ? 'on' : ''}">crew</a>`,
    `<a href="${CONTROL_ROOM_HREF}" class="${state.view === 'control-room' ? 'on' : ''}">control room</a>`,
    `<a href="${HELP_HREF}" class="${state.view === 'help' ? 'on' : ''}">help</a>`,
    `<a href="${SETUP_HREF}" class="${state.view === 'setup' ? 'on' : ''}">setup</a>`,
  ];
  let out = links.join('<span class="sep">\u00b7</span>');
  /* These are the project's, never the feature's: the ledger here is the
     project ledger even with a feature open. A feature's own record is on its
     step row, as `runs`, beside the steps it belongs to.

     They sit in the same row as three global links, so something has to mark
     the seam between the two kinds of thing. The rule is the one the scheme
     picker already uses to set itself off from "places you can go"; PROJECT is
     not a link, it is the heading the rule introduces, said once so "ledger"
     doesn't have to say it again. */
  return out + schemePicker();
}

/* ------------------------------------------------------------ the scheme

   Last item in the masthead, after `settings`, set off by a rule -- the four
   words before it are places you can go, and this is not one.

   Three things it deliberately does not do. It does not mark the current
   scheme with the nav's underline, which already means "the page you are
   on"; the two registers this console keeps apart are where the RUN is and
   where YOU are, and a third meaning on the same mark would undo both. It
   does not use a lamp colour, because a lamp is lit by the line. And it does
   not hide behind the settings screen, because a scheme is something you
   change while looking at what it changes.

   Each chip is the scheme it picks -- that scheme's board on the left, its
   floor on the right, in the real values. So `default` reads as a split
   square, and the other two read as what they are: one light room, one dark
   one. Only the chosen scheme says its name; the other two are offered as
   objects, which is how the rail's own filters already work. */
const SCHEMES = [
  ['default', '#171a17', '#dcdfdb'],
  ['day',     '#f8f9f5', '#edefe9'],
  ['night',   '#0c0f0b', '#1b1f1a'],
];

function schemePicker() {
  return `<span class="sw-pick" role="group" aria-label="Colour scheme">` +
    SCHEMES.map(([name, board, floor]) => `<button type="button" data-scheme="${name}"
        aria-pressed="${name === scheme() ? 'true' : 'false'}" title="${name}"
        style="--c-board:${board};--c-floor:${floor}"
      ><i aria-hidden="true"></i><b>${name}</b></button>`).join('') + `</span>`;
}

/* The chosen scheme lives in this browser and nowhere else. It is not a
   property of the factory, the project or the run -- it is a property of the
   person looking, and of the screen they are looking at, so a second machine
   is entitled to a different answer. `SCHEME_KEY` is read before first paint
   by the inline script in index.html; this is the same read, for the picker's
   own state. */
const SCHEME_KEY = 'fabrika:scheme';

function scheme() {
  try {
    const v = localStorage.getItem(SCHEME_KEY);
    return SCHEMES.some(([n]) => n === v) ? v : 'default';
  } catch { return 'default'; }
}

function setScheme(name) {
  if (name === 'default') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.dataset.theme = name;
  try { localStorage.setItem(SCHEME_KEY, name); } catch { /* private window */ }
  for (const b of document.querySelectorAll('.sw-pick button')) {
    b.setAttribute('aria-pressed', String(b.dataset.scheme === name));
  }
}

document.addEventListener('click', (e) => {
  const b = e.target.closest('.sw-pick button');
  if (b) setScheme(b.dataset.scheme);
});

/* THE HEADER IS ONE ROW.

   Where you are on the left, where you can go on the right. Two rows -- a
   masthead carrying the nav over a board carrying the title -- would leave an
   empty middle in the first and nothing but the title in the second. As one,
   the board is the header on any screen that has a line, and the masthead in
   index.html is the same row for the screens that do not. */
/* The name of the thing, and the way out of it. Nothing between them: no
   boxed numerals in the gap, for the reason set out under PLAN, ACTUAL,
   DEFECT in steps.js. */
function boardHead(inner) {
  return `<div class="hdr">
      <div class="hdr-main"><div class="crumbline">
        <button class="panel-toggle" type="button" aria-controls="panel"
          aria-expanded="true" aria-label="Hide active work" title="Active work -- [">
          <span class="pt-chev" aria-hidden="true">\u2039</span>
        </button>
        ${inner}
      </div></div>
      <div class="hdr-right">
        <span class="mast-nav">${navLinks()}</span>
      </div>
    </div>`;
}

/* The live phase, for screens where the answer to "can I do anything?" is no. */
function liveWord(d) {
  const here = bandOf(d);
  if (!here.running || !here.phase) return '';
  const p = (d.phases || []).find((x) => x.name === here.phase) || {};
  return `<span class="liveword"><span class="dotpulse" aria-hidden="true"></span>
    ${esc(here.phase)}${p.detail ? ` · ${esc(p.detail)}` : ''}</span>`;
}
