/* The repository's own guides -- AGENTS.md, DESIGN.md, skills -- read and
   proposed as commits, and the sheet a guide is edited in. */

'use strict';

/* What the project already depends on, as last read from its lockfiles and
   looked up. The starting point a feature is measured from, so problems here
   are the project's: listed first, and never blamed on a feature. Problems
   only on the face of it; every package one press down. */
/* The repository's own files that say how its code is written: AGENTS.md,
   DESIGN.md, skills. What is committed on the branch features start from is
   what binds; Fabrika approves nothing. It reads these files, hands them to
   the agents that have no harness to read them, and proposes changes to them
   as commits a person edits and approves. */
const GUIDE_LAYERS = [
  { id: 'agent', title: 'How to work here', none: 'No AGENTS.md. Every coding agent reads it first: commands, code style, testing.' },
  { id: 'design', title: 'UX and visual style', none: 'No DESIGN.md.' },
  { id: 'skill', title: 'Skills', none: 'No skills in .claude/skills/ or .agents/skills/.' },
];
const guideBase = () => ((state.project || {}).project || {}).base_ref || 'the base branch';
const guidePlural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
function guideView() {
  const g = (state.project || {}).guides;
  return g && !Array.isArray(g) ? g : { guides: [], skill_dirs: [] };
}
function guideProposals() {
  const p = state.project || {};
  return (p.guide_offers || []).filter((o) => (o.writes || []).length).length + (p.guide_draft ? 1 : 0);
}
function guidesTab() {
  const view = guideView();
  const all = view.guides || [];
  const base = guideBase();
  const has = (layer) => all.some((g) => g.layer === layer);
  const proposals = guideProposals();
  const tally = [all.length ? guidePlural(all.filter((g) => g.layer !== 'linked').length, 'file') : 'none in the repository',
    proposals ? guidePlural(proposals, 'proposal') : ''].filter(Boolean).join(' · ');
  const bars = GUIDE_LAYERS.map((l) => (has(l.id) ? 'g' : ''));
  const intro = `<p class="tab-intro">The rules for how code is written here, in the files every
    coding tool already reads. What is committed on <span class="mono">${esc(base)}</span> is what
    binds &mdash; for Fabrika's agents and for anything else that writes code in this repository.
    Fabrika approves nothing: it reads these files, and proposes changes to them as commits you
    edit first.</p>`;
  /* Guide files written but not committed come before everything: until they
     are on the base branch, no agent follows them. */
  const working = ((state.project || {}).guide_offers || []).filter((o) => o.layer === 'working');
  return `${sectionHead('guides', 'Guides', '', esc(tally), bars)}${intro}${
    working.length ? `<div class="fam guides gd-layer">
      <p class="fam-title"><span class="eyebrow">In your working copy, not committed</span></p>
      ${working.map((o) => guideProposalRow(o.kind)).join('')}</div>` : ''}${
    GUIDE_LAYERS.map((l) => guideLayer(l, all)).join('')}${guideContradictions()}`;
}

function guideLayer(layer, all) {
  const p = state.project || {};
  const mine = all.filter((g) => g.layer === layer.id);
  const linked = layer.id === 'agent' ? all.filter((g) => g.layer === 'linked') : [];
  const offers = (p.guide_offers || []).filter((o) => o.layer === layer.id);
  const lines = (g) => (g.first_lines || []).length ? `<details class="hz-more"><summary>first lines</summary>
    <pre class="hz-pre">${esc(g.first_lines.join('\n'))}</pre></details>` : '';
  const fact = (g) => g.layer === 'skill' ? esc(g.description || 'no description in its front matter')
    : g.layer === 'linked' ? `pointed to by <span class="mono">${esc(g.referenced_by)}</span>`
    : g.scope ? `the rules for <span class="mono">${esc(g.scope)}/</span>`
    : g.layer === 'design' ? 'for anything a person sees'
    : g.path === 'CLAUDE.md' ? 'what Claude Code reads' : 'the whole repository';
  const row = (g) => `<div class="q-row gd-row"><div class="q-line prose">
      <span class="q-lamp g"></span><span class="q-name mono">${esc(g.path)}</span>
      <span class="q-fact">${fact(g)}${lines(g)}</span><span></span></div></div>`;
  return `<div class="fam guides gd-layer">
    <p class="fam-title"><span class="eyebrow">${esc(layer.title)}</span></p>
    ${mine.length || linked.length ? [...mine, ...linked].map(row).join('')
      : `<p class="fam-empty">${esc(layer.none)}</p>`}
    ${layer.id === 'skill' ? skillsDirPick() : ''}
    ${offers.map((o) => (o.kind === 'ux_gap'
      ? `<div class="proposal-note">${icon('l-info', 'ic ic-sm')}<p>${esc(o.why)}</p></div>`
      : guideProposalRow(o.kind))).join('')}
    ${layer.id === 'agent' && p.guide_draft ? guideProposalRow('draft') : ''}
  </div>`;
}

/* Where a new skill goes: where this project's skills already are, or the
   open standard's folder when there are none. Which runner sees which skill
   is not this -- every worker is bridged to the folder its tool reads. */
function skillsDirPick() {
  const view = guideView();
  const home = view.skills_home || '.agents/skills';
  const opt = (f) => `<button type="button" class="btn btn-sm${home === f ? ' btn-primary' : ''}"
    data-skills-dir="${esc(f)}" aria-pressed="${home === f}">${esc(f)}/</button>`;
  const clashes = (view.clashes || []).map((c) => `<li><b>${esc(c.name)}</b> is in ${
    c.paths.map((p) => `<span class="mono">${esc(p)}</span>`).join(' and ')}</li>`).join('');
  return `<div class="gd-dir"><span class="small dim">New skills go in</span>
    ${opt('.claude/skills')}${opt('.agents/skills')}
    <span class="small dim">${view.skills_home_chosen ? 'Chosen for this project.'
      : esc(view.skills_home_why ? `${view.skills_home_why[0].toUpperCase()}${view.skills_home_why.slice(1)}.` : '')}
      Every worker sees every skill: Fabrika links them, for each session only, into the
      folder its tool reads.</span></div>
    ${clashes ? `<div class="proposal-note">${icon('l-info', 'ic ic-sm')}<p>Two skills share a name, and
      each tool loads only the one in its own folder. Rename one, or remove the copy:</p>
      <ul class="disclosure-list">${clashes}</ul></div>` : ''}`;
}

/* A proposal, as one line: what it would do, and the files it would write.
   Open it for the reasoning and the first lines; Review opens the whole text
   in the editor, where it is changed before anything is committed. */
function guideProposal(kind) {
  const p = state.project || {};
  if (kind === 'draft') {
    const d = p.guide_draft;
    return d && {
      kind, title: `Add ${guidePlural(d.rules.length, 'rule')} ${d.features} features kept following to ${d.path}`,
      why: `Conventions your features kept following that ${d.path} does not state yet. Every rule was counted by code; the sentences are a model's, and yours to edit.`,
      rules: d.rules, files: [{ path: d.path, append: true, contents: d.markdown }],
    };
  }
  const o = (p.guide_offers || []).find((x) => x.kind === kind && (x.writes || []).length);
  return o && { kind, title: o.title || o.path, why: o.why,
    files: o.writes.map((w) => ({ path: w.path, append: !!w.append, link: w.link || '',
      asIs: !!w.as_is, contents: w.contents || '' })) };
}

/* What you have written into a proposal so far, kept until it is committed
   or the proposal itself changes. Closing the editor loses nothing. */
function guideEdits(kind) {
  const prop = guideProposal(kind);
  if (!prop) return null;
  const sig = prop.files.map((f) => `${f.path}\n${f.contents}`).join('\u0000');
  const held = (state.guideEdits || {})[kind];
  if (held && held.sig === sig) return held;
  state.guideEdits = { ...(state.guideEdits || {}),
    [kind]: { sig, at: 0, files: prop.files.map((f) => ({ ...f, proposed: f.contents })) } };
  return state.guideEdits[kind];
}
const guideEdited = (ed) => !!ed && ed.files.some((f) => f.contents !== f.proposed);
function guideEditFile() {
  const ed = state.openGuide && (state.guideEdits || {})[state.openGuide];
  return ed ? ed.files[ed.at] : null;
}
function guideSheetEdited() { return guideEdited((state.guideEdits || {})[state.openGuide]); }

function guideProposalRow(kind) {
  const prop = guideProposal(kind);
  if (!prop) return '';
  const edited = guideEdited((state.guideEdits || {})[kind]);
  const files = prop.files.map((f) => `<span class="mono">${esc(f.path)}</span>${
    f.append ? ' <span class="dim">addition</span>' : f.link ? ' <span class="dim">link</span>' : ''}`).join(', ');
  /* The text is read in the editor, not previewed here. A link has no text:
     what it is fits on one line, and it is committed from the row. */
  const onlyLinks = prop.files.every((f) => f.link);
  return `<div class="gd-prop">
    <details class="gd-prop-line"><summary>
      <span class="q-lamp a"></span>
      <span class="gd-prop-title">${esc(prop.title)}</span>
      <span class="gd-prop-files">${files}${edited ? ' <span class="chip warn">edited</span>' : ''}</span>
    </summary>
    <div class="gd-prop-more">
      <p>${esc(prop.why)}</p>
      ${prop.rules ? `<ul class="disclosure-list">${prop.rules.map((r) => `<li>${esc(r.rule)} <span class="dim">·
        ${esc(String(r.features))} features · e.g. <span class="mono">${esc((r.files || [])[0] || '')}</span></span></li>`).join('')}</ul>` : ''}
      ${onlyLinks ? `<p class="mono small">${prop.files.map((f) => `${esc(f.path)} → ${esc(f.link)}`).join('<br>')}</p>` : ''}
    </div></details>
    <div class="gd-prop-acts">
      ${onlyLinks
        ? `<button class="btn btn-sm btn-primary" data-guide-commit="${esc(kind)}">Commit it</button>`
        : `<button class="btn btn-sm btn-primary" data-guide-review="${esc(kind)}">Review</button>`}
      <button class="btn btn-sm btn-quiet" data-guide-decline="${esc(kind)}">No thanks</button>
    </div>
  </div>`;
}

/* --- the guide editor ------------------------------------------------------

   The same sheet an agent's prompt is written in: the whole document, its
   outline, its counts. A proposal that writes two files has a tab for each.
   Nothing here touches the repository until Commit; what is committed is
   exactly what is in the editor. */

function guideSheetHtml(kind, ed) {
  const prop = guideProposal(kind);
  const file = ed.files[ed.at];
  const tabs = ed.files.length > 1 ? ed.files.map((f, i) => `<button type="button"
      class="cfg cfg-flag${i === ed.at ? ' on' : ''}" data-guide-file="${i}" aria-pressed="${i === ed.at}">
      <span class="cfg-chip"><span class="k">${f.asIs ? 'yours' : f.append ? 'addition' : 'new'}</span>
      <span class="v">${esc(f.path)}</span></span></button>`).join('') : '';
  return `
    <div class="sheet-scrim"></div>
    <div class="sheet" role="dialog" aria-modal="true" aria-label="${esc(prop.title)}">
      <div class="sheet-top">
        <span class="s-name">${esc(file.path)}</span>
        <span class="s-kind">${file.asIs ? 'in your working copy' : file.append ? 'added to the end' : 'new file'}</span>
        <span class="s-sp"></span>
        <span class="s-path">${esc(prop.title)}</span>
        <button class="btn btn-sm btn-quiet" data-guide-decline="${esc(kind)}">No thanks</button>
        <button class="btn btn-sm btn-quiet" data-guide-close="1">Close</button>
        <button class="btn btn-sm btn-primary" data-guide-commit="${esc(kind)}">Commit</button>
      </div>
      <div class="sheet-rail">
        ${tabs}
        <span class="s-why">${file.asIs
          ? `This is <span class="mono">${esc(file.path)}</span> as it stands in your working copy. A change you make here is written to it when you commit.`
          : file.append
          ? `This text is added to the end of <span class="mono">${esc(file.path)}</span>; what is there now stays as it is.`
          : esc(prop.why)}</span>
      </div>
      <div class="sheet-body">
        <nav class="sheet-outline" aria-label="Sections of this file">
          <p class="o-cap">Sections</p>
          <div class="o-list"></div>
        </nav>
        <div class="sheet-edit"></div>
      </div>
      <div class="sheet-foot">
        <span class="s-count"></span>
        <span class="s-state">as proposed</span>
        <span class="s-sp"></span>
        <span>Commit writes it to your working branch, as edited here</span>
        <span><kbd>Esc</kbd> close</span>
      </div>
    </div>`;
}

/* Drawn by `syncSheet`, into the same host as an agent's prompt and by the
   same rule: written when it opens and when it closes, never on a poll. */
function syncGuideSheet() {
  const kind = state.view === 'gates' && state.openGuide;
  const ed = kind ? guideEdits(kind) : null;
  if (!ed) {
    state.openGuide = null;
    return false;
  }
  const key = `guide:${kind}:${ed.at}`;
  if (sheetShownFor === key) { positionSheet(); return true; }
  if (sheetShownFor === null) sheetScrollY = window.scrollY;
  if (sheetView) { sheetView.destroy(); sheetView = null; }
  sheetHost.innerHTML = guideSheetHtml(kind, ed);
  sheetShownFor = key;
  document.body.classList.add('sheet-open');
  positionSheet();
  const file = ed.files[ed.at];
  const host = sheetHost.querySelector('.sheet-edit');
  if (window.CM) {
    sheetView = mountMarkdownEditor(host, { doc: file.contents, onChange: (text) => { file.contents = text; } });
  } else {
    host.innerHTML = `<textarea class="answer-field prompt-fallback" data-guide-fallback="1"
      spellcheck="false">${esc(file.contents)}</textarea>`;
  }
  drawOutline();
  syncPromptCount();
  markSheetDirty();
  if (sheetView) sheetView.focus();
  return true;
}

function closeGuideSheet() {
  if (!state.openGuide) return;
  const y = sheetScrollY;
  state.openGuide = null;
  render();
  if (y !== null) window.scrollTo(0, y);
}

function decideGuide(kind, decision) {
  const ed = guideEdits(kind);
  if (!ed) return null;
  return withBusy(decision === 'write' ? 'Committing it.' : 'Recording that.', async () => {
    const out = kind === 'draft'
      ? await api(`${projectUrl(state.projectId)}/guides/draft`, {
        method: 'POST', body: JSON.stringify({ decision, markdown: ed.files[0].contents }),
      })
      : await api(`${projectUrl(state.projectId)}/guides/offer`, {
        method: 'POST',
        body: JSON.stringify({ kind, decision,
          contents: Object.fromEntries(ed.files.map((f) => [f.path, f.contents])) }),
      });
    state.openGuide = null;
    delete state.guideEdits[kind];
    await refreshProject();
    toast(decision === 'decline' ? 'Not offered again.' : guideCommitted(out));
  });
}

function guideContradictions() {
  const notes = (state.project || {}).guide_contradictions || [];
  if (!notes.length) return '';
  return `<div class="fam guides"><details class="fold"><summary><span class="eyebrow">${notes.length} place${
      notes.length === 1 ? '' : 's'} the code does otherwise than a guide</span></summary>
      <ul class="disclosure-list">${notes.map((c) => `<li><span class="mono">${esc(c.guide)}</span>:
        "${esc(c.rule)}" — ${esc((c.files || []).join(', '))} <span class="dim">· seen by ${esc(c.feature)}</span></li>`).join('')}</ul>
      <p class="small dim">Settle each in the guide or in the code.</p></details></div>`;
}

function guideCommitted(out) {
  const all = out.paths || [];
  const paths = all.length > 1 ? `${all.slice(0, -1).join(', ')} and ${all[all.length - 1]}` : all.join('');
  return out.commit_problem ? `Written to ${paths}: ${out.commit_problem}`
    : `Committed ${paths} (${String(out.commit).slice(0, 7)}).`;
}

function dependencyInventory(p) {
  const inv = (state.project || {}).dependencies;
  const policy = p.dependency_policy || {};
  const head = `<h3 class="fam-h">${icon('l-package', 'ic ic-sm fam-ic')}Dependencies
    <span class="fam-asks">What this project already relies on</span></h3>`;
  if (!inv) {
    return `<div class="fam deps-inv">${head}<p class="fam-empty">Not read yet. The lockfiles are
      read with the next run of the checks.</p>${policyLine(policy)}</div>`;
  }
  const all = inv.packages || [];
  const direct = all.filter((x) => x.direct).length;
  const trouble = (x) => (x.malicious || []).length || (x.advisories || []).length
    || (x.verdicts || []).some((v) => v === 'denied' || v === 'unknown-license' || v === 'flagged');
  const bad = all.filter(trouble);
  const words = (x) => [
    (x.malicious || []).length ? 'published to do harm' : '',
    (x.advisories || []).length ? `${x.advisories.join(', ')}` : '',
    (x.verdicts || []).includes('denied') ? `license ${x.license} not allowed here` : '',
    (x.verdicts || []).includes('flagged') ? `license ${x.license} flagged` : '',
    (x.verdicts || []).includes('unknown-license') ? 'license unknown' : '',
    (x.verdicts || []).includes('new-advisory') ? 'new since the last reading' : '',
  ].filter(Boolean).join(' · ');
  const row = (x) => `<li><span class="mono">${esc(x.name)} ${esc((x.after || [])[0] || '')}</span>
    <span class="dim">${esc(x.ecosystem)}${x.direct ? '' : x.via ? ` · through ${esc(x.via)}` : ''}</span>
    ${trouble(x) ? `<span class="dep-flag">${esc(words(x))}</span>` : `<span class="dim">${esc(x.license || (x.private ? 'private, not looked up' : 'license unknown'))}</span>`}</li>`;
  return `<div class="fam deps-inv">${head}
    <p class="fam-empty">${all.length} package${all.length === 1 ? '' : 's'}, ${direct} named directly,
      read ${esc(ago(Date.parse(inv.at) / 1000))}.${bad.length
        ? ` <b>${bad.length} already ${bad.length === 1 ? 'needs' : 'need'} looking at</b> &mdash; the
          project's, not any feature's.` : ' Nothing known against any of them.'}
      ${inv.note ? ` ${esc(inv.note.charAt(0).toUpperCase() + inv.note.slice(1))}.` : ''}</p>
    ${bad.length ? `<ul class="deps-list">${bad.map(row).join('')}</ul>` : ''}
    ${all.length ? `<details class="deps-all"><summary>Every package</summary>
      <ul class="deps-list">${all.map(row).join('')}</ul></details>` : ''}
    ${policyLine(policy)}</div>`;
}

/* The policy a feature's new packages are held to. A person's, so it is
   shown where they can change it, and changing it sends nothing back to gate 0:
   it is not part of what the checks measured. */
function policyLine(policy) {
  if (state.editingPolicy) {
    return `<div class="objection deps-policy">
      ${edRow('Licenses refused', `<input class="answer-field mono" id="policy-deny"
        value="${esc((policy.deny || []).join(', '))}">`, 'SPDX identifiers; * for any version.')}
      ${edRow('Licenses flagged', `<input class="answer-field mono" id="policy-flag"
        value="${esc((policy.flag || []).join(', '))}">`, 'Reported for a ruling, never refused.')}
      ${edRow('Youngest release taken', `<input class="answer-field" id="policy-age" type="number"
        min="0" value="${esc(String(policy.min_age_days ?? 7))}">`, 'Days since it was published.')}
      <div class="d-actions">
        <button class="btn btn-sm btn-quiet" id="cancel-policy">Cancel</button>
        <button class="btn btn-sm btn-primary" id="save-policy">Save the policy</button>
      </div></div>`;
  }
  if (policy.enabled === false) {
    return `<p class="fam-empty">New packages are not looked up or held to anything.
      <button type="button" class="linkish" data-policy-on="1">Hold them to a policy</button></p>`;
  }
  return `<p class="fam-empty">A feature's new packages are looked up, and refused under
    ${esc((policy.deny || []).join(', ') || 'no license')}; flagged under
    ${esc((policy.flag || []).join(', ') || 'none')}; flagged if released in the last
    ${esc(String(policy.min_age_days ?? 7))} days.
    <button type="button" class="linkish" id="edit-policy">Change it</button> &middot;
    <button type="button" class="linkish" data-policy-on="0">Turn it off</button></p>`;
}

/* And everything that answers "is the code being added correct": what a blind
   test can be written at, what is still owed, and what a reading suggests. */
/* How Fabrika checks its work, at the top of the project page.

   The one idea everything under it depends on, said once and plainly: Fabrika
   tests what it builds, so tests here have to be repeatable, and the survey
   looks for the runners and the cleanup that make them so. Below the words,
   this project's own answer, level by level -- whether there is a runner,
   how tests leave things as they found them, and whether Fabrika will check
   criteria there.

   Open until someone folds it. It comes back open, amber, whenever a level is
   not tested, and cannot be folded then: that is the fact a person must not
   lose sight of while they write specs against this project. */
const CLEANUP_WORDS = {
  harness: 'reset for every test',
  isolated: 'each test uses its own data',
  each_test: 'each test undoes its own',
  nobody: 'no cleanup',
};

function runnerNames(runner) {
  // "vitest 2 with jsdom (web); pytest for the API's pure functions" -> "vitest, pytest"
  return [...new Set((runner || '').split(';').map((part) => part.trim().split(/\s+/)[0])
    .map((w) => w.replace(/[`,.:;()]/g, '')).filter(Boolean))].join(', ');
}

/* The Overview: what the project needs from you, and nothing that needs
   reading to find out.

   One sentence first. Then one tile per area, each a single line of status --
   "All 8 green · 1 suggestion" -- never the items themselves, and each opening
   its tab. Then every feature waiting on a person, ship-ready first, with the
   packet's own one-line gist and what ruling on it involves. Everything here is
   read off the record: counts, the packet's stored fields, and what the server
   computes in `factory/overview.py`. No model is asked anything to draw it. */

function tokenCount(n) {
  const v = Number(n) || 0;
  return v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}K` : `${v}`;
}

/* A route's name as a person knows it: the tool, or the key it bills. */
function routeWord(name) {
  if (name === 'default') return 'API key';
  return String(name || 'unknown').split('-').map((w) => w[0].toUpperCase() + w.slice(1)).join(' ');
}

/* How the work was paid for, from the overview's per-route token split: the
   share that went through a subscription, and any charged per token. */
function costSplit(spend) {
  const tokens = (spend || {}).tokens || {};
  let sub = 0; let charged = 0;
  Object.values(tokens).forEach((t) => { sub += t.subscription || 0; charged += t.charged || 0; });
  const total = sub + charged;
  return { tokens, sub, charged, total, pct: total ? Math.round((100 * sub) / total) : 100 };
}

function money(n) {
  const v = Number(n) || 0;
  return v >= 100 ? `$${Math.round(v)}` : `$${v.toFixed(2)}`;
}

/* A tile that has a tab opens it; one that only reports -- Features, whose
   list is right below, and Spend -- is not a link. */
function overviewTile(area, status, sub, lamp, href = null) {
  const inner = `<span class="lamp ${lamp}"></span>
      <span class="db-area">${esc(area)}</span>
      <span class="db-status">${status}</span><span class="db-sub">${sub}</span>`;
  const needs = lamp === 'warn' ? ' db-needs' : '';
  return href ? `<a class="db-tile${needs}" href="${href}">${inner}</a>`
    : `<div class="db-tile db-still${needs}">${inner}</div>`;
}

function waitingRow(f, pid) {
  const href = `#/${encodeURIComponent(pid)}/${encodeURIComponent(f.feature_id)}`;
  const v = VERDICT_WORD[f.verdict];
  const facts = [];
  if (f.criteria_total) facts.push(`${f.criteria_verified} of ${f.criteria_total} criteria verified`);
  if (f.calls != null) {
    facts.push(f.calls_left ? `${f.calls_left} of ${f.calls} call${f.calls === 1 ? '' : 's'} left`
      : f.calls ? 'every call ruled on' : 'no calls');
  }
  if (f.checks) facts.push(`${f.checks_left} of ${f.checks} to check by hand`);
  if (f.rounds) facts.push(`${f.rounds} repair round${f.rounds === 1 ? '' : 's'}`);
  /* Tokens, and how they were paid for -- never a dollar figure for work on a
     plan. Dollars appear only where something was actually charged. */
  const split = costSplit(f.spend);
  const charged = (f.spend || {}).billed || 0;
  if (split.total) {
    facts.push(`${tokenCount(split.total)} tokens, ${split.charged ? `${split.pct}% on plans` : 'all on plans'}`);
  }
  if (charged) facts.push(`${money(charged)} charged`);
  /* Stale is measured, not guessed from a date: how far main has moved since
     this feature's branch was cut. Every commit is one it was never checked
     against. */
  const behind = f.behind_main;
  return `<div class="db-need">
      <div class="db-need-main"><a class="db-need-t" href="${href}">${esc(f.title)}</a>
        ${v ? `<span class="db-verdict ${v[1]}">${esc(v[0])}</span>`
          : `<span class="db-need-what">${esc(f.needs)}</span>`}
        ${f.gist || f.headline ? `<p class="db-need-h">${esc(f.gist || f.headline)}</p>` : ''}
        ${f.error ? `<p class="db-need-h">${esc(f.error)}</p>` : ''}
        ${facts.length ? `<p class="db-need-f">${facts.map(esc).join(' · ')}</p>` : ''}</div>
      <div class="db-need-side"><span class="db-age">waiting ${esc(rel(f.since))}${behind
          ? ` · <span class="${behind >= 5 ? 'db-stale' : ''}">main is ${behind} commit${behind === 1 ? '' : 's'} ahead</span>`
          : ''}</span>
        <a class="btn btn-sm ${v && v[1] !== 'stop' ? 'btn-primary' : ''}" href="${href}">${
          f.stage === 'awaiting_verdict' ? 'Rule on it' : 'Open it'}</a></div>
    </div>`;
}

/* Cost: how the work was paid for, not a dollar figure. A subscription bills
   nothing per call -- what its tools report is what the work would have cost at
   API prices, and adding that up reads as spend when nothing was spent. So the
   headline says whether anything was charged; the bar splits the tokens by
   route, subscriptions in green and anything charged per token in amber; and
   dollars appear only for what a route actually billed. */
function costTile(ov) {
  if (!ov) return overviewTile('Cost', '…', '', '');
  const spend = ov.spend || {};
  const split = costSplit(spend);
  const charged = spend.billed || 0;
  const status = !split.total && !charged ? 'No work yet'
    : !split.charged && !charged ? 'All on subscriptions'
    : charged ? `${money(charged)} charged` : `${split.pct}% on subscriptions`;
  /* The same three lines as every other tile, so the row stays even: the
     sub line is the bar's key -- each route and its tokens -- and the bar sits
     thin at the foot. "Nothing charged" is the headline's to say; sessions
     from before tokens were recorded are in the tile's hover text. */
  const routes = Object.entries(split.tokens).filter(([, t]) => (t.subscription || 0) + (t.charged || 0));
  const key = routes.map(([name, t], i) => `<span class="db-key"><i class="${
    t.charged && !t.subscription ? 'chg' : i % 2 ? 'sub-b' : 'sub-a'}"></i>${esc(routeWord(name))} ${
    tokenCount((t.subscription || 0) + (t.charged || 0))}</span>`);
  if (split.charged) key.push(`<span class="db-key"><i class="chg"></i>${tokenCount(split.charged)} charged</span>`);
  const segs = [];
  routes.forEach(([name, t], i) => {
    if (t.subscription) segs.push([i % 2 ? 'sub-b' : 'sub-a', name, t.subscription]);
    if (t.charged) segs.push(['chg', name, t.charged]);
  });
  const bar = split.total ? `<span class="db-bar" role="img" aria-label="${split.pct}% on subscriptions">${
    segs.map(([cls, name, n]) => `<i class="${cls}" style="width:${(100 * n) / split.total}%"></i>`).join('')}</span>` : '';
  /* The detail is one click away, behind an (i) in the corner: per route,
     what went through a subscription and what was charged, and what the
     that means. Not hover text, which nobody finds. */
  const open = !!state.costInfo;
  const rows = routes.map(([name, t]) => `<tr><td>${esc(routeWord(name))}</td>
      <td>${tokenCount(t.subscription || 0)}</td><td>${tokenCount(t.charged || 0)}</td></tr>`).join('');
  const panel = !open ? '' : `<div class="db-pop" role="dialog" aria-label="How the work was paid for">
      <p class="db-pop-h">How the work was paid for</p>
      ${routes.length ? `<table class="db-pop-t"><tr><th>Route</th><th>On a subscription</th><th>Charged</th></tr>
        ${rows}<tr class="tot"><td>All</td><td>${tokenCount(split.sub)}</td><td>${tokenCount(split.charged)}</td></tr></table>`
        : '<p>No tokens recorded yet.</p>'}
      <p>${charged ? `${money(charged)} was charged by routes billed per token.`
        : 'Nothing was charged: every token went through a subscription plan.'}
        A subscription bills nothing per call, so no dollar figure is shown for it.</p>
    </div>`;
  return `<div class="db-tile db-still db-cost"><span class="lamp ${charged ? 'warn' : ''}"></span>
      <span class="db-area">Cost</span>
      <button type="button" class="db-info" data-cost-info="1" aria-expanded="${open}"
        aria-label="How the work was paid for" title="How the work was paid for">${icon('l-info')}</button>
      <span class="db-status">${esc(status)}</span>
      <span class="db-sub">${key.join(' · ') || 'no tokens recorded'}</span>${bar}${panel}</div>`;
}
