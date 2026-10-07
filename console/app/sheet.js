/* The sheet: the one editor that is not rebuilt from a string -- markdown
   shown as itself, the outline, the footer -- and its lifecycle. */

'use strict';

/* ------------------------------------------------------------- the sheet

   The sheet is the one part of this console that is NOT rebuilt from a string.
   Everything else can be: a screen redrawn every four seconds is fine when you
   are reading it. An editor cannot be. Rebuilding the subtree under somebody's
   caret is the flicker you see when a poll lands mid-keystroke, and at full
   height it would also throw away their place in six hundred lines.

   So `#sheet-host` sits outside `#main` and is written exactly twice per
   visit: once when a role is opened, once when it is closed. Nothing inside it
   calls `render`. A chip that changes writes the draft and repaints itself;
   the document lives in CodeMirror, which owns its own DOM for as long as the
   sheet is up. */

const sheetHost = document.getElementById('sheet-host');
let sheetView = null;      // the live CodeMirror, or null
let sheetShownFor = null;  // the role `#sheet-host` is currently drawn for
let sheetScrollY = null;   // where the page was when the sheet opened

function closeSheet() {
  if (!state.openRole) return;
  const was = state.openRole;
  const y = sheetScrollY;
  state.openRole = null;
  render();  // tears the sheet down on its way through `syncSheet`
  /* Back where the reader was when it opened -- which is not always the row.
     Opened from the diagram, scrolling to the row would jump the page down to
     a table the reader has not been looking at. Focus still lands on the row, so
     the keyboard picks up from there, but it does not move the page. */
  const row = main.querySelector(`[data-open-role="${CSS.escape(was)}"]`);
  if (row) row.focus({ preventScroll: true });
  if (y !== null) window.scrollTo(0, y);
}

/* --- markdown, shown as what it is ---------------------------------------

   Not a preview pane, and not raw source either. The text stays editable in
   place and is styled where it sits: headings in the console's own condensed
   face at heading sizes, bold actually bold, code and tables in the monospace
   that needs to be monospace and nothing else in it. The markers -- the `##`,
   the `**` -- are hidden on every line except the one the caret is on, which
   is the arrangement Obsidian uses and the reason its documents read like
   documents while staying one keystroke from the source. */

const MD_MARKS = {
  HeaderMark: 1, StrongMark: 1, EmphasisMark: 1, LinkMark: 1, QuoteMark: 1,
};

function mdLivePreview() {
  const { ViewPlugin, Decoration, syntaxTree } = window.CM;
  const hide = Decoration.replace({});
  const codeLine = Decoration.line({ class: 'cm-line-code' });
  const tableLine = Decoration.line({ class: 'cm-line-table' });

  const build = (view) => {
    const marks = [];
    const doc = view.state.doc;

    // Lines the caret is on keep their markers: you cannot edit syntax you
    // cannot see.
    const live = new Set();
    for (const r of view.state.selection.ranges) {
      const from = doc.lineAt(r.from).number;
      const to = doc.lineAt(r.to).number;
      for (let i = from; i <= to; i += 1) live.add(i);
    }

    const blocked = [];  // line ranges that get a whole-line class
    /* YAML front matter -- DESIGN.md's tokens -- is data, not prose. Markdown
       reads its lines as a heading underlined by the closing `---`, so it is
       set as code and nothing in it is styled or hidden. */
    let matterEnd = -1;
    if (doc.lines > 1 && doc.line(1).text.trim() === '---') {
      for (let n = 2; n <= Math.min(doc.lines, 400); n += 1) {
        if (doc.line(n).text.trim() === '---') { matterEnd = doc.line(n).to; break; }
      }
    }
    if (matterEnd >= 0) blocked.push([0, matterEnd, codeLine]);
    for (const { from, to } of view.visibleRanges) {
      syntaxTree(view.state).iterate({
        from,
        to,
        enter: (node) => {
          if (node.name === 'FencedCode' || node.name === 'CodeBlock') {
            blocked.push([node.from, node.to, codeLine]);
            return false;  // leave the fences and their contents alone
          }
          if (node.name === 'Table') { blocked.push([node.from, node.to, tableLine]); return; }
          if (node.from <= matterEnd) return;
          if (!MD_MARKS[node.name]) return;
          const line = doc.lineAt(node.from);
          if (live.has(line.number)) return;
          // A heading's hash takes the space after it with it, or the text
          // starts an em further in than the line below.
          let end = node.to;
          if (node.name === 'HeaderMark' && doc.sliceString(end, end + 1) === ' ') end += 1;
          if (end > node.from) marks.push(hide.range(node.from, end));
        },
      });
    }

    blocked.forEach(([from, to, deco]) => {
      let n = doc.lineAt(from).number;
      const last = doc.lineAt(to).number;
      for (; n <= last; n += 1) marks.push(deco.range(doc.line(n).from));
    });

    return Decoration.set(marks, true);
  };

  return ViewPlugin.fromClass(class {
    constructor(view) { this.decorations = build(view); }
    update(u) {
      if (u.docChanged || u.viewportChanged || u.selectionSet) this.decorations = build(u.view);
    }
  }, { decorations: (v) => v.decorations });
}

function mdHighlight() {
  const { HighlightStyle, tags: t } = window.CM;
  return HighlightStyle.define([
    { tag: t.heading1, class: 'md-h1' },
    { tag: t.heading2, class: 'md-h2' },
    { tag: t.heading3, class: 'md-h3' },
    { tag: t.heading4, class: 'md-h4' },
    { tag: t.strong, class: 'md-strong' },
    { tag: t.emphasis, class: 'md-em' },
    { tag: t.strikethrough, class: 'md-strike' },
    { tag: t.monospace, class: 'md-code' },
    { tag: t.link, class: 'md-link' },
    { tag: t.url, class: 'md-url' },
    { tag: t.quote, class: 'md-quote' },
    { tag: t.list, class: 'md-list' },
    { tag: t.contentSeparator, class: 'md-rule' },
    { tag: t.processingInstruction, class: 'md-mark' },
  ]);
}

/* The editor every long Markdown document in the console is written in: an
   agent's prompt, a guide Fabrika proposes. Styled where it sits, markers
   shown only on the caret's line, the outline and the counts kept in step. */
function mountMarkdownEditor(host, opts) {
  const C = window.CM;
  const view = new C.EditorView({
    parent: host,
    state: C.EditorState.create({
      doc: opts.doc || '',
      extensions: [
        C.history(),
        C.drawSelection(),
        C.dropCursor(),
        C.highlightActiveLine(),
        C.highlightSelectionMatches(),
        C.bracketMatching(),
        C.EditorView.lineWrapping,
        C.markdown({ base: C.markdownLanguage, addKeymap: true }),
        C.syntaxHighlighting(mdHighlight()),
        mdLivePreview(),
        C.placeholder(opts.placeholder || ''),
        C.keymap.of([
          // Bound here as well as on the document so it wins over anything
          // the editor would do with it.
          ...(opts.onSave ? [{ key: 'Mod-s', preventDefault: true, run: () => { opts.onSave(); return true; } }] : []),
          C.indentWithTab, ...C.defaultKeymap, ...C.historyKeymap, ...C.searchKeymap,
        ]),
        C.EditorView.updateListener.of((u) => {
          if (u.docChanged) {
            opts.onChange(u.state.doc.toString());
            syncPromptCount();
            markSheetDirty();
            drawOutline();
          }
          // `viewportChanged` covers scrolling as well as edits: CodeMirror
          // recomputes what it is showing either way, and asking it is more
          // reliable than a DOM scroll event, which a backgrounded tab does
          // not deliver at all.
          if (u.docChanged || u.viewportChanged || u.geometryChanged) markCurrentSection();
        }),
      ],
    }),
  });
  /* Bound to the scroller itself. `domEventHandlers` listens on the content,
     and a scroll event does not bubble, so bound there the outline would sit on
     whatever section it was last told about while the document moves
     underneath it. */
  view.scrollDOM.addEventListener('scroll', markCurrentSection, { passive: true });
  return view;
}

function mountPromptEditor(host, role) {
  const d = roleDraft(role);
  if (!window.CM) {
    // The bundle did not load. Say so and give back something that still
    // edits, rather than a blank panel and a silent agent.
    host.innerHTML = `<textarea class="answer-field prompt-fallback" data-role="${esc(role.name)}"
      data-key="prompt" spellcheck="false">${esc(d.prompt)}</textarea>`;
    return null;
  }
  return mountMarkdownEditor(host, {
    doc: d.prompt,
    placeholder: 'This agent has no prompt. Everything it is told to do goes here.',
    onChange: (text) => { d.prompt = text; },
    onSave: () => saveRole(role.name),
  });
}

/* --- the outline --------------------------------------------------------- */

function drawOutline() {
  const list = sheetHost.querySelector('.o-list');
  const cap = sheetHost.querySelector('.o-cap');
  if (!list) return;
  const text = sheetView ? sheetView.state.doc.toString()
    : state.openGuide ? (guideEditFile() || {}).contents || ''
    : (state.roleDraft[state.openRole] || {}).prompt || '';
  const heads = promptOutline(text);
  list.innerHTML = heads.length
    ? heads.map((h) => `<button type="button" data-jump-line="${h.line}"
        class="o-l${h.level}">${esc(h.text)}</button>`).join('')
    : '<p class="o-none">No headings yet.</p>';
  if (cap) cap.textContent = heads.length
    ? `${heads.length} section${heads.length === 1 ? '' : 's'}` : 'Sections';
  markCurrentSection();
}

/* A press on the outline pins that section lit for a moment.

   Otherwise the last few headings can never light at all: the document runs
   out before they reach the top of the scroller, so "what is at the top" is
   still some earlier section and the list answers a press by lighting
   something else. Held briefly rather than latched, so the first real scroll
   afterwards takes the outline back. */
let outlineHeldLine = -1;
let outlineHeldAt = 0;

function jumpToPromptLine(line) {
  if (!sheetView) return;
  const doc = sheetView.state.doc;
  const at = doc.line(Math.min(line + 1, doc.lines)).from;
  outlineHeldLine = line;
  outlineHeldAt = Date.now();
  sheetView.dispatch({
    selection: { anchor: at },
    effects: window.CM.EditorView.scrollIntoView(at, { y: 'start', yMargin: 6 }),
  });
  sheetView.focus();
  markCurrentSection();
}

/* Which heading you are under, lit in the outline. Asked of the editor rather
   than measured by hand: CodeMirror already knows what is at the top of its
   own scroller, and it knows it through wrapping, folds and variable line
   heights that a mirror div would have to reproduce to get wrong. */
function markCurrentSection() {
  const list = sheetHost.querySelector('.o-list');
  if (!list || !sheetView) return;
  const buttons = [...list.querySelectorAll('[data-jump-line]')];
  if (!buttons.length) return;
  /* Asked as a height, not as a point. Hit-testing a coordinate at the top-left
     of the scroller lands in the content's own left margin, where there is no
     line to find and the answer never changes however far the document moves.
     `lineBlockAtHeight` is the question actually being asked: what is at this
     scroll offset, through wrapping and variable line heights.

     Its heights are measured from the start of the document, not from the top
     of the scroller, and those differ by the scroller's own padding. Taking
     the difference from the two boxes is exact and survives any later change
     to that padding; `scrollTop` on its own lands an eighth of an inch high
     and names the section above the one on screen. */
  const h = sheetView.scrollDOM.getBoundingClientRect().top - sheetView.documentTop;
  const block = sheetView.lineBlockAtHeight(Math.max(0, h + 4));
  const topLine = sheetView.state.doc.lineAt(block.from).number - 1;

  let at = 0;
  buttons.forEach((b, i) => { if (Number(b.dataset.jumpLine) <= topLine) at = i; });
  if (outlineHeldLine >= 0) {
    if (Date.now() - outlineHeldAt < 600) {
      const held = buttons.findIndex((b) => Number(b.dataset.jumpLine) === outlineHeldLine);
      if (held >= 0) at = held;
    } else {
      outlineHeldLine = -1;
    }
  }
  buttons.forEach((b, i) => b.classList.toggle('on', i === at));
  const lit = buttons[at];
  if (lit && lit.offsetParent) {
    const nav = list.parentElement;
    const y = lit.offsetTop - nav.scrollTop;
    if (y < 0 || y > nav.clientHeight - lit.offsetHeight) {
      nav.scrollTop = lit.offsetTop - nav.clientHeight / 2;
    }
  }
}

/* --- the footer, and the chips ------------------------------------------- */

function sheetIsDirty() {
  const role = (state.roles || { roles: [] }).roles.find((r) => r.name === state.openRole);
  const d = role && state.roleDraft[role.name];
  if (!role || !d) return false;
  return d.prompt !== role.prompt
    || d.model !== role.model || (d.route || '') !== (role.route || '')
    || d.allow_fallbacks !== role.allow_fallbacks || d.enabled !== role.enabled
    || String(d.temperature) !== String(role.temperature)
    || String(d.max_tokens) !== String(role.max_tokens)
    || String(d.reasoning_effort) !== String(role.reasoning_effort)
    || String(d.providers) !== (role.providers || []).join(', ')
    || String(d.samples) !== String(role.samples);
}

function markSheetDirty() {
  const el = sheetHost.querySelector('.s-state');
  if (!el) return;
  if (state.openGuide) {
    const on = guideSheetEdited();
    el.classList.toggle('s-dirty', on);
    el.textContent = on ? 'edited — not committed' : 'as proposed';
    return;
  }
  const on = sheetIsDirty();
  el.classList.toggle('s-dirty', on);
  el.textContent = on ? 'unsaved' : 'saved';
}

/* The counts are in the footer because the prompt is an input this agent pays
   for on every call it makes. */
function syncPromptCount() {
  const el = sheetHost.querySelector('.s-count');
  if (!el || !sheetView) return;
  const text = sheetView.state.doc.toString();
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  el.textContent = `${sheetView.state.doc.lines} lines · ${words.toLocaleString()} words`;
}

/* The chip and its field show one value, and only the field is bound. A press
   of a key is not a reason to redraw a screen, so the chip is told directly. */
function syncChip(control) {
  const cell = control.closest('.cfg');
  if (!cell) return;
  const out = cell.querySelector('.cfg-chip .v');
  if (!out) return;
  let v = control.tagName === 'SELECT'
    ? control.options[control.selectedIndex].text
    : control.value;
  if (control.dataset.key === 'model_route') {
    // The option reads "opus · via Claude Code"; the chip has no room for the
    // "via" and does not need it.
    v = v.replace(' · via ', ' · ');
  }
  out.textContent = v === '' ? (control.placeholder ? 'unpinned' : '—') : v;
  cell.classList.add('dirty');
  markSheetDirty();
}

function toggleFlag(button) {
  const d = state.roleDraft[button.dataset.role];
  if (!d) return;
  const key = button.dataset.cfgFlag;
  d[key] = !d[key];
  const v = button.querySelector('.v');
  button.classList.toggle('on', !!d[key]);
  button.setAttribute('aria-pressed', String(!!d[key]));
  if (v) v.textContent = d[key] ? v.dataset.on : v.dataset.off;
  markSheetDirty();
}

/* --- the lifecycle -------------------------------------------------------- */

/* Saving leaves the sheet open, which means the sheet is now showing a draft
   that `saveRole` has just thrown away. Re-seed it from what is on disk and
   drop the marks, rather than tearing down an editor somebody is working in. */
function afterSheetSave() {
  if (!sheetShownFor || !state.roles) return;
  const role = state.roles.roles.find((r) => r.name === sheetShownFor);
  if (!role) { state.openRole = null; syncSheet(); return; }
  roleDraft(role);
  sheetHost.querySelectorAll('.cfg.dirty').forEach((c) => c.classList.remove('dirty'));
  markSheetDirty();
}

function syncSheet() {
  if (syncGuideSheet()) return;
  const role = state.view === 'roles' && state.openRole && state.roles
    ? state.roles.roles.find((r) => r.name === state.openRole) : null;

  if (!role) {
    if (sheetShownFor !== null) {
      if (sheetView) { sheetView.destroy(); sheetView = null; }
      sheetHost.innerHTML = '';
      sheetShownFor = null;
      sheetScrollY = null;
    }
    document.body.classList.remove('sheet-open');
    return;
  }

  // Already up for this role: leave it alone. This is the whole point of the
  // host -- a poll landing while you type must change nothing here.
  if (sheetShownFor === role.name) { positionSheet(); return; }

  if (sheetShownFor === null) sheetScrollY = window.scrollY;
  if (sheetView) { sheetView.destroy(); sheetView = null; }
  sheetHost.innerHTML = roleSheetHtml(role);
  sheetShownFor = role.name;
  document.body.classList.add('sheet-open');
  positionSheet();

  sheetView = mountPromptEditor(sheetHost.querySelector('.sheet-edit'), role);
  drawOutline();
  syncPromptCount();
  markSheetDirty();
  if (sheetView) sheetView.focus();
}

/* Whatever chrome is pinned to the top of the window today -- one bar most
   days, two when the stale-code strip is up -- the sheet starts under the
   lowest edge of it rather than under a number written down here. */
function positionSheet() {
  const low = ['.masthead', '#stale-bar', '.feature-bar']
    .map((sel) => document.querySelector(sel))
    .filter(Boolean)
    .reduce((y, el) => Math.max(y, el.getBoundingClientRect().bottom), 0);
  document.documentElement.style.setProperty('--sheet-top', `${Math.round(Math.max(low, 0))}px`);
}

sheetHost.addEventListener('click', onMainClick);
/* The scrim is not a button and never reaches the dispatcher above, but it is
   the plainest way out of a sheet and should work like one. */
sheetHost.addEventListener('click', (event) => {
  if (!event.target.classList.contains('sheet-scrim')) return;
  if (state.openGuide) closeGuideSheet(); else closeSheet();
});
sheetHost.addEventListener('input', (event) => {
  if (event.target.dataset.guideFallback) {
    const file = guideEditFile();
    if (file) { file.contents = event.target.value; markSheetDirty(); }
    return;
  }
  const control = event.target.closest('[data-role][data-key]');
  const d = control && state.roleDraft[control.dataset.role];
  if (!d) return;
  if (control.dataset.key === 'model_route') {
    const cut = String(control.value).indexOf('|');
    d.route = String(control.value).slice(0, cut);
    d.model = String(control.value).slice(cut + 1);
  } else {
    d[control.dataset.key] = control.type === 'checkbox' ? control.checked : control.value;
  }
  syncChip(control);
});

/* A chip closes when you are finished with it: Enter or Escape from the field,
   or the focus simply going somewhere else. Escape inside a chip is about the
   chip, not the sheet, so it stops there. */
sheetHost.addEventListener('keydown', (event) => {
  const cell = event.target.closest && event.target.closest('.cfg.editing');
  if (!cell) return;
  if (event.key !== 'Enter' && event.key !== 'Escape') return;
  event.preventDefault();
  event.stopPropagation();
  cell.classList.remove('editing');
  const chip = cell.querySelector('.cfg-chip');
  if (chip) chip.focus();
});

sheetHost.addEventListener('focusout', (event) => {
  const cell = event.target.closest && event.target.closest('.cfg.editing');
  if (!cell) return;
  const to = event.relatedTarget;
  if (to === null || cell.contains(to)) return;
  cell.classList.remove('editing');
});

/* Escape closes the sheet; Save has a shortcut because this is somewhere
   you write for minutes at a time, and reaching for a button after every
   paragraph is how work gets lost. */
document.addEventListener('keydown', (event) => {
  if (state.openGuide && event.key === 'Escape') { event.preventDefault(); closeGuideSheet(); return; }
  if (!state.openRole || state.view !== 'roles') return;
  if (event.key === 'Escape') { event.preventDefault(); closeSheet(); return; }
  if ((event.metaKey || event.ctrlKey) && (event.key === 's' || event.key === 'S')) {
    event.preventDefault();
    if (state.roleDraft[state.openRole]) saveRole(state.openRole);
  }
});

window.addEventListener('resize', () => { if (sheetShownFor) positionSheet(); });
