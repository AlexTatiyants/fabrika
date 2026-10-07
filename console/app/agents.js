/* The agents page: the pipeline drawn as a diagram whose every node is a
   control, and the roster of who does each station. */

'use strict';

/* ------------------------------------------------------------- 0d. agents */

const EFFORTS = ['none', 'low', 'medium', 'high'];

/* The pipeline, drawn. It explains the fixed order, the two lanes and the three
   human gates better than four paragraphs would, and every node is a control. */

/* Tall enough for two lines: what the station is, and under it the model that
   answers for it. A gate or a step of code has no model, and its one line sits
   in the middle. */
const NODE_H = 36;

function diaWidth(label, model = '') {
  return Math.max(46, label.length * 6.4 + 20, model.length * 5.3 + 16);
}

/* Blue team and red team, as the server says them (`team_of` in config.py,
   where the reasons are). It decides where an agent runs once the crew is
   staffed by side, so it is said in one place. An agent on neither team keeps
   the plain border rather than being forced onto a side. */
function teamOf(role) {
  const r = ((state.roles || {}).roles || []).find((x) => x.name === role);
  return r && r.team !== 'neither' ? r.team : '';
}

/* `role` is the agent's name in config, which is what a click opens. The label
   is how it reads: `spec writer` is drawn with a space and configured as
   `spec_writer`, and a click keyed on the label opened nothing. */
function diaNode(x, y, label, kind, model = '', role = label.replace(/ /g, '_')) {
  const w = diaWidth(label, model);
  const human = kind === 'gate';
  const yours = kind === 'panel';
  const code = kind === 'step';
  /* An agent switched off in config is drawn, not hidden: the shape of the
     pipeline is the same and what is missing from this run is the point. */
  const off = kind === 'agent-off';
  const fill = human || yours ? 'var(--accent-wash)' : code ? 'var(--paper)' : 'var(--surface)';
  const team = kind === 'agent' ? teamOf(role) : '';
  const stroke = human || yours ? 'var(--accent)' : off ? 'var(--line)'
    : team ? `var(--team-${team})` : 'var(--line-strong)';
  const color = human || yours ? 'var(--accent-ink)'
    : (code || off) ? 'var(--ink-3)' : 'var(--ink-2)';
  const clickable = kind === 'agent' || off || yours;
  const working = kind === 'agent' && asBuiltWorking(role);
  return {
    w,
    svg: `<g ${clickable ? `data-node="${esc(role)}" class="dia-agent ${state.openRole === role ? 'open' : ''} ${working ? 'dia-live' : ''}"` : ''}>
      <rect x="${x}" y="${y}" width="${w}" height="${NODE_H}" rx="3" fill="${fill}"
        stroke="${stroke}" ${code || off ? 'stroke-dasharray="3 2"' : ''} stroke-width="${team ? 1.5 : 1}"/>
      <text x="${x + w / 2}" y="${y + (model ? 15 : NODE_H / 2 + 4)}" text-anchor="middle"
        font-family="var(--mono)" font-size="10" fill="${color}">${esc(label)}</text>
      ${model ? `<text x="${x + w / 2}" y="${y + 28}" text-anchor="middle"
        font-family="var(--mono)" font-size="8" fill="var(--ink-3)">${esc(model)}</text>` : ''}
    </g>`,
  };
}

function diaChain(x, y, items, gap) {
  let cursor = x;
  const parts = [];
  items.forEach((item, i) => {
    const [label, kind, , model, role] = item;
    const n = diaNode(cursor, y, label, kind, model, role);
    parts.push(n.svg);
    cursor += n.w;
    if (i < items.length - 1) {
      const joined = items[i + 1][2];
      if (joined === 'none') {
        cursor += 7;
      } else {
        parts.push(`<line x1="${cursor + 3}" y1="${y + NODE_H / 2}" x2="${cursor + (gap || 18) - 5}"
          y2="${y + NODE_H / 2}" stroke="var(--line-strong)" marker-end="url(#dia-arrow)"/>`);
        cursor += gap || 18;
      }
    }
  });
  return { svg: parts.join(''), end: cursor };
}

function pipelineDiagram(roles) {
  const review = roles.filter((r) => r.kind === 'review' && r.enabled !== false);
  const has = (name) => {
    const r = roles.find((x) => x.name === name);
    return r ? (r.enabled === false ? 'agent-off' : 'agent') : 'agent-off';
  };
  const tag = (t, x, y) => `<text x="${x}" y="${y}" font-family="var(--mono)" font-size="9"
    letter-spacing="0.06em" fill="var(--ink-3)">${esc(t)}</text>`;
  /* The model each agent runs on, read from the same roster the table below
     edits -- so the picture changes when the config does. */
  const modelOf = (name) => {
    const r = roles.find((x) => x.name === name);
    return r && r.enabled !== false ? (r.model || '') : '';
  };
  /* A two-word label names its config role outright, so the picture carries
     every agent the orchestrator calls as a literal a reader can search for. */
  const agent = (label, kind, role = label) => [label, kind || has(role), undefined,
                                                 modelOf(role), role];

  const SPINE = 112;
  const LANE = 168;
  const MID = NODE_H / 2;
  /* Every row is placed from the one above it, so a taller node moves the
     whole picture rather than overlapping the next row. */
  const PROJECT_Y = 12;
  const RULE_Y = PROJECT_Y + NODE_H + 8;
  const SPEC_Y = RULE_Y + 16;
  const SPEC_RULE_Y = SPEC_Y + NODE_H + 16;
  const BUILD_Y = SPEC_Y + NODE_H + 32;
  const DIVIDE_Y = BUILD_Y + NODE_H + 10;
  const VERIFY_Y = DIVIDE_Y + 10;
  const ORACLE_RUN = VERIFY_Y + NODE_H + 20;
  const BUILD_RUN = ORACLE_RUN - 10;
  const REVIEW_Y = ORACLE_RUN + 12;
  const REWORK_Y = REVIEW_Y + NODE_H + 42;

  const gate0 = diaChain(88, PROJECT_Y, [
    agent('surveyor', 'agent'), agent('resurvey'), ['repo ready', 'gate'],
  ], 20);
  const intake = diaChain(88, SPEC_Y, [
    agent('scout', 'agent'), agent('interrogator', 'agent'), ['answers', 'gate'],
    agent('spec writer', null, 'spec_writer'), agent('spec checker', null, 'spec_checker'), ['spec review', 'gate'],
  ], 20);
  /* Plan review is drawn as a gate like the others, though most runs never
     stop there: it exists only when the architect and the plan checker leave
     something unsettled, or code measures a problem with the cut. */
  const build = diaChain(LANE, BUILD_Y, [
    agent('architect', 'agent'), agent('plan checker', null, 'plan_checker'), ['plan review', 'gate'],
    agent('worker', 'agent'), agent('integrator', 'agent'),
  ], 20);
  const verify = diaChain(LANE, VERIFY_Y, [agent('oracle', 'agent')], 20);
  const laneEndX = Math.max(build.end, verify.end) + 10;

  /* The converge band. `checks`, `blind suite` and `qa` are code; the breaker is
     an agent with tools. The reviewer after it gets one completion over a text
     bundle and cannot run anything.

     `blind suite` is drawn separately from `checks` rather than folded into it,
     because the two answer different questions and only one of them settles a
     criterion. `checks` is the project's own gates. `blind suite` is the
     oracle's tests, run per file by the orchestrator -- no agent runs them, and
     that is the property the whole verify lane exists to buy. Folded together
     the reader cannot see where an acceptance criterion is actually decided. */
  const pre = diaChain(LANE, REVIEW_Y, [
    ['checks', 'step'], ['blind suite', 'step'], ['qa', 'step'], agent('breaker'),
  ], 18);

  /* Where `blind suite` sits, so the oracle's output can be drawn arriving at
     it. Recomputed from the same widths `diaChain` used rather than returned by
     it: keeping that function's contract to a single `end` is worth more than
     saving these two lines. */
  const blindCx = LANE + diaWidth('checks') + 18 + diaWidth('blind suite') / 2;
  const oracleCx = LANE + diaWidth('oracle', modelOf('oracle')) / 2;

  /* Where the build lane comes down. It leaves to the right of `no shared
     state` rather than dropping straight through it: the line goes from build
     to the review band and never touches verify, and a solid stroke crossing
     that divider would say it did. `buildInX` is off `checks`'s centre because
     the oracle's own line runs along y=208 and the two have to cross
     somewhere -- see the hop below. */
  const buildOutX = laneEndX + 12;
  const buildInX = LANE + 44;
  const groupStart = pre.end + 18;
  const concurrent = review.length
    ? review.map((r) => [r.name, 'agent', undefined, r.model || '', r.name])
    : [['no review agents', 'step']];
  let cx = groupStart;
  const groupNodes = [];
  concurrent.forEach(([lab, kind, , model, role]) => {
    const n = diaNode(cx, REVIEW_Y, lab, kind, model, role);
    groupNodes.push(n.svg);
    cx += n.w + 8;
  });
  const groupEnd = cx - 8;

  /* The simplifier sits between the arbiter and the rapporteur because that is
     when it runs: once, after the repair loop below has converged and before
     the packet is assembled. It is the only agent here that may delete
     something for being unnecessary rather than for being wrong, which is why
     it is not in the loop -- a repairer is forbidden exactly that, and a pass
     that removes code has to happen on a tree whose tests already pass. */
  const tail = diaChain(groupEnd + 18, REVIEW_Y, [
    agent('arbiter'), agent('simplifier'),
    agent('rapporteur', 'agent'), ['feature review', 'gate'],
  ], 18);

  /* The repair loop, drawn as a loop. It reads counter-clockwise -- out of the
     arbiter, back into the gates -- because that is what it does: nothing here
     is a step forward, and drawing it left to right would say it was. */
  const arbiter = diaNode(groupEnd + 18, REVIEW_Y, 'arbiter', has('arbiter'), modelOf('arbiter'));
  const arbCx = groupEnd + 18 + arbiter.w / 2;
  const repW = diaWidth('repairer', modelOf('repairer'));
  const repX = Math.max(LANE, arbCx - repW / 2);
  const repNode = diaNode(repX, REWORK_Y, 'repairer', has('repairer'), modelOf('repairer'));
  const rowMid = REWORK_Y + NODE_H / 2;

  const rework = `
    <path d="M${arbCx} ${REVIEW_Y + NODE_H} L${arbCx} ${REWORK_Y - 6}"
      fill="none" stroke="var(--line-strong)" marker-end="url(#dia-arrow)"/>
    ${repNode.svg}
    <path d="M${repX - 3} ${rowMid} L${LANE - 16} ${rowMid} L${LANE - 16} ${REVIEW_Y + MID + 6}
             L${LANE - 6} ${REVIEW_Y + MID + 6}"
      fill="none" stroke="var(--line-strong)" marker-end="url(#dia-arrow)"/>
    ${tag('bounded by config: rounds, budget, wall clock', repX - 6, REWORK_Y + NODE_H + 16)}`;

  const converge = {
    end: tail.end,
    svg: `${pre.svg}
      <line x1="${pre.end + 3}" y1="${REVIEW_Y + MID}" x2="${groupStart - 5}" y2="${REVIEW_Y + MID}"
        stroke="var(--line-strong)" marker-end="url(#dia-arrow)"/>
      ${groupNodes.join('')}
      <line x1="${groupEnd + 3}" y1="${REVIEW_Y + MID}" x2="${groupEnd + 13}" y2="${REVIEW_Y + MID}"
        stroke="var(--line-strong)" marker-end="url(#dia-arrow)"/>
      ${tail.svg}
      ${rework}`,
  };

  /* The as-built, on a row of its own: it runs beside everything above, on
     your press and at a feature's start and review, and nothing waits on it.
     Its two agents bracket three steps that are code -- the reader's word is
     never drawn until it has been checked, and code, not a model, forms the
     groups the cartographer names. */
  const ASBUILT_Y = REWORK_Y + NODE_H + 40;
  const asBuilt = diaChain(88, ASBUILT_Y, [
    ['files at a commit', 'step'], agent('reader'), ['check the links', 'step'],
    ['group', 'step'], agent('cartographer'), ['.fabrika/as-built', 'step'],
  ], 18);
  const now = asBuiltNow(roles);
  const height = ASBUILT_Y + NODE_H + (now ? 40 : 26);

  return `
    <div class="dia-scroll">
    <svg viewBox="0 0 ${Math.max(900, converge.end + 20)} ${height}" class="dia" role="img"
      aria-label="Agents in a fixed order: two lanes that cannot see each other, a repair loop
        the orchestrator bounds, and three human rulings.">
      <defs>
        <marker id="dia-arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 z" fill="var(--line-strong)"/>
        </marker>
        <marker id="dia-arrow-l" markerWidth="6" markerHeight="6" refX="1" refY="3" orient="auto">
          <path d="M6,0 L0,3 L6,6 z" fill="var(--line-strong)"/>
        </marker>
      </defs>

      ${tag('project', 8, PROJECT_Y + MID + 4)}${gate0.svg}
      <line x1="8" y1="${RULE_Y}" x2="${converge.end + 12}" y2="${RULE_Y}" stroke="var(--line)"/>

      ${tag('spec', 8, SPEC_Y + MID + 4)}${intake.svg}
      <line x1="8" y1="${SPEC_RULE_Y}" x2="${converge.end + 12}" y2="${SPEC_RULE_Y}" stroke="var(--line)"/>

      <path d="M${SPINE} ${SPEC_Y + NODE_H} L${SPINE} ${REVIEW_Y + MID} L${LANE - 6} ${REVIEW_Y + MID}
               M${SPINE} ${BUILD_Y + MID} L${LANE - 6} ${BUILD_Y + MID}
               M${SPINE} ${VERIFY_Y + MID} L${LANE - 6} ${VERIFY_Y + MID}"
        fill="none" stroke="var(--line-strong)"/>

      ${tag('build', 8, BUILD_Y + MID + 4)}${build.svg}
      ${tag('verify', 8, VERIFY_Y + MID + 4)}${verify.svg}
      ${tag('sees the spec and nothing else', verify.end + 12, VERIFY_Y + MID + 4)}

      <!-- Neither lane is a dead end, and drawing them as two was hiding the
           load-bearing fact in this diagram: the review band runs the oracle's
           tests against the build's code, and both of those arrive here.

           The two lines cross, and no arrangement of these nodes avoids it --
           the oracle sits above "checks" but feeds "blind suite", while build
           arrives from the right and feeds "checks". So the oracle's line, the
           one passing through rather than landing, takes a hop over build's. -->
      <path d="M${oracleCx} ${VERIFY_Y + NODE_H} L${oracleCx} ${ORACLE_RUN} L${buildInX - 5} ${ORACLE_RUN}
               A 5 5 0 0 1 ${buildInX + 5} ${ORACLE_RUN} L${blindCx} ${ORACLE_RUN}
               L${blindCx} ${REVIEW_Y - 6}"
        fill="none" stroke="var(--line-strong)" marker-end="url(#dia-arrow)"/>
      <path d="M${build.end} ${BUILD_Y + MID} L${buildOutX} ${BUILD_Y + MID} L${buildOutX} ${BUILD_RUN}
               L${buildInX} ${BUILD_RUN} L${buildInX} ${REVIEW_Y - 6}"
        fill="none" stroke="var(--line-strong)" marker-end="url(#dia-arrow)"/>
      ${tag("the oracle's tests, run per file — no agent runs them",
            blindCx - diaWidth('blind suite') / 2, REVIEW_Y + NODE_H + 14)}
      <line x1="${LANE}" y1="${DIVIDE_Y}" x2="${laneEndX}" y2="${DIVIDE_Y}"
        stroke="var(--line-strong)" stroke-dasharray="2 3"/>
      ${tag('no shared state', laneEndX + 40, DIVIDE_Y + 3)}

      ${tag('review', 8, REVIEW_Y + MID + 4)}${converge.svg}
      ${tag('repair', 8, REWORK_Y + MID + 4)}
      <line x1="8" y1="${ASBUILT_Y - 20}" x2="${converge.end + 12}" y2="${ASBUILT_Y - 20}" stroke="var(--line)"/>
      ${tag('as-built', 8, ASBUILT_Y + MID + 4)}${asBuilt.svg}
      ${tag('on your press, and beside each feature', asBuilt.end + 12, ASBUILT_Y + MID + 4)}
      ${now ? tag(now, 88, ASBUILT_Y + NODE_H + 22) : ''}
    </svg>
    </div>
    <p class="dia-key mono">
      <span><i class="k-build"></i>builds (blue team)</span>
      <span><i class="k-check"></i>checks (red team)</span>
      <span><i class="k-agent"></i>agent that does neither</span>
      <span><i class="k-step"></i>deterministic code</span>
      <span><i class="k-gate"></i>you</span>
    </p>`;
}

/* --------------------------------------------------------- compact roster */
