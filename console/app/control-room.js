/* The control room: what belongs to the person on this machine rather than to
   any project, which today is how a file opens. */

'use strict';

/* -------------------------------------------------------- 0b2. control room

   Whatever belongs to the person reading a packet on this one machine,
   rather than to any project or any agent; a project's own configuration
   is in its tabs. One setting lives here today: how a file
   opens. The templates are the ones `Config.editor_url`'s own docstring
   names, so a chip and the config comment never drift apart. */
const EDITOR_PRESETS = [
  ['VS Code', 'vscode://file{path}:{line}'],
  ['Cursor', 'cursor://file{path}:{line}'],
  ['Zed', 'zed://file{path}:{line}'],
  ['IntelliJ IDEA', 'idea://open?file={path}&line={line}'],
  ['Sublime Text', 'subl://open?url=file://{path}&line={line}'],
];

function resolveEditorUrl(template, path, line) {
  return template.replace('{path}', path).replace('{line}', String(line));
}

function controlRoomScreen() {
  if (!state.config) return `<div class="wrap"><p class="empty">Loading…</p></div>`;
  const current = state.config.editor_url || '';
  // A real path on this machine, because a placeholder would prove nothing
  // the label above it doesn't already say. `source` is the config file this
  // very save writes to, so it is guaranteed to exist here.
  const examplePath = state.config.source || '/path/to/factory.yaml';
  const preview = current ? resolveEditorUrl(current, examplePath, 1) : '';
  const isKnown = (tmpl) => EDITOR_PRESETS.some(([, t]) => t === tmpl);

  return `
    <div class="wrap">
      <section class="roles-top">
        <h1 class="crew-title">${icon('l-sliders', 'page-mark')}<span>Control Room.</span></h1>
        <p class="crew-sub">Whatever belongs to the person reading a packet on this one machine,
          rather than to any project or any agent — a project's own configuration is in its tabs,
          once one is open. The first thing that qualifies is how a file opens.</p>
      </section>

      <section class="section">
        <p class="eyebrow">How a file opens</p>
        <div class="card">
          <p class="small dim" style="max-width:70ch;margin:0 0 1rem">
            Every review screen can offer to open the real file in your own editor instead of a
            pane on this page. It does that by handing your browser a URL your OS already knows
            how to resolve — <span class="mono">vscode://…</span> and the rest are scheme
            handlers, and they resolve on <b style="color:var(--ink)">this browser's</b>
            filesystem, not the factory's.
          </p>

          <div class="editor-presets" role="group" aria-label="Known editors">
            ${EDITOR_PRESETS.map(([name, tmpl]) => `
              <button type="button" class="editor-chip ${current === tmpl ? 'on' : ''}"
                data-template="${esc(tmpl)}">${esc(name)}</button>`).join('')}
            <button type="button" class="editor-chip ${current && !isKnown(current) ? 'on' : ''}">Custom…</button>
          </div>

          <div class="role-grid">
            <label class="field-inline grow"><span>url template</span>
              <input class="answer-field mono" id="editor-url-field" value="${esc(current)}"
                placeholder="vscode://file{path}:{line}" spellcheck="false"></label>
            <button class="btn btn-sm btn-primary" id="save-editor">Save</button>
          </div>
          <p class="field-hint"><span class="mono">{path}</span> is the absolute path on this
            machine; <span class="mono">{line}</span> is the first line the change touched.
            Leave it empty to hide the link entirely.</p>

          ${preview ? `
          <div class="editor-preview">
            <span class="eyebrow" style="margin:0">resolves to, for example</span>
            <code class="mono">${esc(preview)}</code>
          </div>` : ''}

          <p class="small dim" style="max-width:70ch;margin:.9rem 0 0">
            Only honoured when this console and the factory are the same machine — from anywhere
            else the link would open the wrong file, or nothing. Reviewing over a network, you'll
            see the <span class="mono">git diff</span> command in its place instead.
          </p>
        </div>
      </section>

      <p class="small dim roles-foot">Stored in
        <span class="mono">${esc((state.config.source || 'factory.yaml').split('/').slice(-1)[0])}</span>,
        one value for the whole install — not a browser preference like the colour scheme, and
        not scoped to any one project. Nothing else lives on this page yet.</p>
    </div>`;
}
