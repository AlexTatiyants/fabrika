/* Starting a feature.
 *
 * No chrome of its own: no breadcrumb -- "projects · <project> · /path ·
 * settings" -- one line beneath a masthead already saying the same thing, and
 * no primary action in the body, since every other screen in the tool
 * right-aligns one in a bar.
 *
 * It is here rather than in app/ so it keeps what you type. A plain textarea
 * with no value binding, inside a screen rebuilt from a string on a
 * four-second poll, loses it: type an intent, pause, and it is gone.
 *
 * This is the one place in this tool where you write rather than rule,
 * so it is drawn as paper: white, full width, ruled like every other surface
 * here, and the prompt in it clears the moment you type.
 */

import { html, useEffect, useRef, useState } from '../vendor/preact-htm.module.js';
import { Pill, STAGES, useBarAction } from './shared.js';

/* A stage's colour and word, from the one table (app/vocab.js); taken as data
   rather than passed as markup, because a screen that renders someone else's
   HTML string is a screen that has to be trusted with it. */
const STAGE = Object.fromEntries(
  Object.entries(STAGES).map(([id, s]) => [id, [s.pill, s.word]]));

const PROMPT = 'What should exist that does not exist now?';

export function Start({ project, features, drift, busy, onCreate }) {
  const [text, setText] = useState('');
  const [title, setTitle] = useState('');
  const [allowDirty, setAllowDirty] = useState(false);
  const box = useRef(null);

  const empty = text.length === 0;
  const ready = text.trim().length > 0;

  const submit = () => {
    if (!ready || busy) return;
    onCreate({ intent: text.trim(), title: title.trim(), allow_dirty: allowDirty });
  };

  useBarAction(ready && !busy, submit, [text, title, allowDirty, ready, busy]);

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); submit(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [text, title, allowDirty, ready, busy]);

  /* The textarea grows with what you write: it is a composer, not a well with a
     scrollbar. */
  const grow = (el) => {
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.max(el.scrollHeight, 100)}px`;
  };
  useEffect(() => grow(box.current), [text]);

  return html`
    <div class="wrap start">
      <div class="compose">
        <p class="eyebrow">Describe the feature</p>

        <div class="field">
          ${empty && html`
            <div class="ghost" aria-hidden="true">
              <span class="prompt">${PROMPT}</span>
            </div>`}
          <textarea
            ref=${box}
            rows="3"
            aria-label=${PROMPT}
            spellcheck="true"
            value=${text}
            disabled=${busy}
            onInput=${(e) => setText(e.target.value)} />
        </div>

        ${ready && html`
          <div class="after">
            <input
              placeholder="Short name (optional)"
              maxlength="70"
              value=${title}
              disabled=${busy}
              onInput=${(e) => setTitle(e.target.value)} />
            <span class="kbd"><kbd>⌘</kbd> <kbd>↵</kbd> to send</span>
          </div>`}

        ${drift && drift.drifted && html`
          <div class="drift">
            <p>
              <b>The checkout has uncommitted changes.</b> A feature branches from
              ${' '}<span class="mono">${drift.base_ref}</span>${' '} as it stands in the
              repository, not as it stands on your disk — so anything you have not committed
              will not be there.
            </p>
            <label>
              <input type="checkbox" checked=${allowDirty} disabled=${busy}
                     onChange=${(e) => setAllowDirty(e.target.checked)} />
              <span>start anyway, from ${drift.base_ref}</span>
            </label>
          </div>`}

        <div class="compose-acts">
          <button class="btn btn-primary" disabled=${!ready || busy} onClick=${submit}>
            Send it to the factory
          </button>
          <p class="after-note">
            Write it the way you would say it to a colleague who knows the codebase.${' '}
            <b>Vagueness here is not a shortcut</b>${' '}— it comes back as questions, and you
            answer them either way. Nothing is built until you freeze the spec.${' '}
            <a href="#" data-help-open="writing-the-intent">How a feature starts</a>
          </p>
        </div>
      </div>

      ${features.length ? html`
        <div class="fl-head">
          <h2>In this project</h2>
          <span class="fl-count">
            ${features.length} feature${features.length === 1 ? '' : 's'}
          </span>
        </div>` : ''}
      ${features.length
        ? features.map((f) => html`
            <a key=${f.feature_id} class="feat-row"
               href=${`#/${encodeURIComponent(f.project_id)}/${encodeURIComponent(f.feature_id)}`}>
              <span>
                <span class="title">${f.title}</span>
                <span class="mono fid">${f.feature_id}</span>
              </span>
              <span class="fstage">
                <${Pill} cls=${(STAGE[f.stage] || ['', f.stage])[0]}>
                  ${(STAGE[f.stage] || ['', f.stage])[1]}
                <//>
              </span>
              <span class="when">${f.when}</span>
            </a>`)
        : html`<p class="start-none">
            Nothing here yet. What you write above becomes the first one.
          </p>`}
    </div>`;
}
