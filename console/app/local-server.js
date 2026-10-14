/* A model server of your own: the form that adds one, on the crew page
   ("Add a local server", the last tile of the accounts grid) and in setup
   ("Use a local server"). One form, drawn the same in both places.

   Connect checks the server from both sides -- from Fabrika, and from a
   throwaway sealed container through the gateway, the path an agent's calls
   take -- and says what it found one row at a time, each warning with what to
   do about it. Nothing is written until Add server, which appears only once
   Connect has run. Which agents run on the server is chosen on Agent
   configuration, not here.

   This is a model server, not a harness: agents on it still work through
   OpenHands. Nothing here names a server or a model; the remedies come from
   the `local_server` block in factory.yaml. */

'use strict';

const LS_FIELDS = ['url', 'name', 'key'];

function lsState() {
  if (!state.ls) state.ls = { open: false, url: '', name: '', key: '', errors: {}, result: null };
  return state.ls;
}

/* What is wrong with the fields, by field. Nothing is sent until this is empty. */
function lsErrors(ls) {
  const errors = {};
  const url = (ls.url || '').trim();
  if (!url) errors.url = 'The server\'s address is needed.';
  else if (!/^https?:\/\/[^\s/]+/i.test(url)) errors.url = 'Start it with http:// or https://.';
  if (!(ls.name || '').trim()) errors.name = 'A name is needed. It titles the account card.';
  return errors;
}

function lsRemedy(r) {
  if (!r) return '';
  return `<div class="ls-remedy">${r.text ? `<p>${esc(r.text)}</p>` : ''}${r.command
    ? `<div class="su-cmd"><span aria-hidden="true">$</span><code>${esc(r.command)}</code>
        <button type="button" class="btn btn-quiet btn-sm" data-copy="${esc(r.command)}">Copy</button></div>`
    : ''}</div>`;
}

function lsCheckRow(c) {
  const mark = c.ok === true ? 'green' : c.ok === false ? 'amber' : '';
  const said = c.ok === true ? 'ok' : c.ok === false ? 'warning' : 'not run';
  return `<li class="ls-check ${c.ok === false ? 'warn' : ''}">
      <span class="su-mark ${mark}" role="img" aria-label="${esc(said)}"></span>
      <div><div class="ls-check-t">${esc(c.title)}</div><div class="ls-check-d">${esc(c.detail)}</div>
        ${c.ok === false ? lsRemedy(c.remedy) : ''}</div>
    </li>`;
}

function lsField(id, label, input, error) {
  return `<label class="ls-field ${error ? 'bad' : ''}" for="ls-${id}"><span class="ls-label">${label}</span>
      ${input}${error ? `<span class="ls-error" id="ls-${id}-error">${esc(error)}</span>` : ''}</label>`;
}

/* The form itself. `where` only changes the words around it. */
function localServerForm(where = 'crew') {
  const ls = lsState();
  const e = ls.errors || {};
  const described = (id) => (e[id] ? `aria-invalid="true" aria-describedby="ls-${id}-error"` : '');
  const r = ls.result;
  return `<div class="ls-form" role="group" aria-label="Add a local server">
      <p class="ls-lede">A model server on this machine or another one on your network, speaking the
        OpenAI-compatible API. Fabrika reaches it from here and agents reach it through Fabrika, so
        <code>localhost</code> and a LAN address both work. Nothing is billed.</p>
      <div class="ls-fields">
        ${lsField('url', 'Address', `<input type="url" id="ls-url" data-ls-field="url" autocomplete="off"
          spellcheck="false" placeholder="http://localhost:8080/v1" value="${esc(ls.url)}" ${described('url')}>`, e.url)}
        ${lsField('name', 'Name', `<input type="text" id="ls-name" data-ls-field="name" autocomplete="off"
          placeholder="${where === 'setup' ? 'Studio Mac' : 'what the account card says'}" value="${esc(ls.name)}" ${described('name')}>`, e.name)}
        ${lsField('key', 'Key', `<input type="password" id="ls-key" data-ls-field="key" autocomplete="off"
          spellcheck="false" placeholder="only if your server asks for one" value="${esc(ls.key)}">`, '')}
      </div>
      ${r ? `<ol class="ls-checks">${(r.checks || []).map(lsCheckRow).join('')}</ol>` : ''}
      <div class="ls-actions">
        <button type="button" class="btn ${r ? 'btn-quiet' : 'btn-primary'}" data-ls="connect">${r ? 'Connect again' : 'Connect'}</button>
        ${r && r.can_add ? `<button type="button" class="btn btn-primary" data-ls="add">Add server</button>` : ''}
        <span class="su-grow"></span>
        ${where === 'crew' ? '<button type="button" class="btn btn-quiet" data-ls="close">Cancel</button>' : ''}
      </div>
    </div>`;
}

/* The dashed tile at the end of the accounts grid. */
function localServerTile() {
  const ls = lsState();
  return `<button type="button" class="acard ls-tile" data-ls="open" aria-expanded="${ls.open}">
      <span class="ls-plus" aria-hidden="true">+</span>
      <span class="ls-tile-t">Add a local server</span>
      <span class="small dim">A model server of your own. No key, no billing.</span>
    </button>`;
}

function lsBody() {
  const ls = lsState();
  return { base_url: (ls.url || '').trim(), name: (ls.name || '').trim(), api_key: ls.key || '' };
}

async function localServerClick(target) {
  const ls = lsState();
  switch (target.dataset.ls) {
    case 'open':
      ls.open = !ls.open;
      return render();
    case 'close':
      state.ls = null;
      return render();
    case 'connect': {
      ls.errors = lsErrors(ls);
      if (Object.keys(ls.errors).length) return render();
      await withBusy('Checking the server from here and from a sealed container.', async () => {
        ls.result = await api('/api/local-servers/connect',
          { method: 'POST', body: JSON.stringify(lsBody()) });
      });
      return render();
    }
    case 'add': {
      ls.errors = lsErrors(ls);
      if (Object.keys(ls.errors).length) return render();
      let added = null;
      await withBusy('Adding the server to factory.yaml.', async () => {
        added = await api('/api/local-servers', { method: 'POST', body: JSON.stringify(lsBody()) });
      });
      if (!added) return undefined;
      const rows = ((state.routes || {}).routes || []).filter((r) => r.name !== added.name);
      state.routes = { ...(state.routes || {}), routes: [...rows, added] };
      state.ls = null;
      toast(`${added.label || added.name} added. Point agents at it on Agent configuration.`);
      if (state.view === 'setup' && typeof suRefresh === 'function') return suRefresh();
      return render();
    }
    default:
      return undefined;
  }
}

/* Typed values survive the repaint a poll makes. */
function localServerInput(target) {
  const field = target.dataset.lsField;
  if (!LS_FIELDS.includes(field)) return false;
  const ls = lsState();
  ls[field] = target.value;
  if (ls.errors && ls.errors[field]) delete ls.errors[field];
  return true;
}
