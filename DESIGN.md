---
name: Fabrika
description: The software factory's console -- an andon board hung over a production line of model agents.
colors:
  shop-floor: "#dcdfdb"
  paperwork: "#f4f5f2"
  paperwork-sunk: "#e9ebe7"
  paperwork-lift: "#ffffff"
  ink: "#171a17"
  ink-2: "#4a504a"
  ink-3: "#5c645c"
  line: "#c2c6c1"
  line-strong: "#a2a89f"
  enamel: "#171a17"
  enamel-2: "#21251f"
  enamel-3: "#2d322b"
  rail-steel: "#69736a"
  board-ink-hi: "#ffffff"
  board-ink: "#e7eae6"
  board-ink-2: "#a7afa5"
  board-ink-3: "#8b938a"
  board-ink-4: "#767e75"
  board-faint: "#5d655c"
  board-rule: "#454c45"
  lamp-green: "#2fa360"
  lamp-amber: "#f0a500"
  lamp-red: "#e03127"
  lamp-white: "#e7eae6"
  lamp-off: "#3a403a"
  lamp-green-prior: "#3d5a46"
  accent: "#9a6300"
  accent-ink: "#7d5100"
  accent-wash: "#fdf3dd"
  red: "#c2231b"
  red-wash: "#fae7e5"
  green: "#1d6b46"
  green-wash: "#e4efe8"
  ink-reversed: "#ffffff"
  ink-on-amber: "#241800"
  v-band-ship: "#17693f"
  v-band-rulings: "#b9d27f"
  v-band-rulings-ink: "#1f2a10"
  v-band-stop: "#a3211a"
  lane-plan: "#2f6ea3"
  lane-build: "#2a7d4f"
  lane-verify: "#a8820c"
  lane-review: "#c1560f"
  team-build: "#2a5ea8"
  mat-generated: "#cdd1cc"
  mat-mechanical: "#a9b0a8"
  mat-conventional: "#6d756c"
typography:
  display:
    fontFamily: "Saira Condensed, Helvetica Neue, Arial, sans-serif"
    fontSize: "26px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "0.05em"
  headline:
    fontFamily: "Saira Condensed, Helvetica Neue, Arial, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.011em"
  title:
    fontFamily: "Saira Condensed, Helvetica Neue, Arial, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 600
    lineHeight: 1.25
  body:
    fontFamily: "IBM Plex Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
  body-record:
    fontFamily: "IBM Plex Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.6
  verdict:
    fontFamily: "IBM Plex Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 300
    lineHeight: 1.45
  label:
    fontFamily: "Saira Condensed, Helvetica Neue, Arial, sans-serif"
    fontSize: "0.625rem"
    fontWeight: 600
    letterSpacing: "0.14em"
  button:
    fontFamily: "Saira Condensed, Helvetica Neue, Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "0.05em"
  meta:
    fontFamily: "Roboto Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "0.6875rem"
    fontWeight: 400
  mono-prose:
    fontFamily: "IBM Plex Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  hairline: "2px"
  key: "3px"
  pill: "999px"
  lamp: "50%"
spacing:
  gutter: "24px"
  gutter-narrow: "14px"
  sheet-gap: "22px"
  measure: "1080px"
  measure-wide: "1400px"
  reading: "720px"
components:
  button:
    backgroundColor: "{colors.paperwork}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.hairline}"
    padding: "0.45rem 0.85rem"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.shop-floor}"
    typography: "{typography.button}"
    rounded: "{rounded.hairline}"
    padding: "0.45rem 0.85rem"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink-3}"
    typography: "{typography.button}"
    rounded: "{rounded.hairline}"
    padding: "0.45rem 0.85rem"
  button-armed:
    backgroundColor: "{colors.accent-wash}"
    textColor: "{colors.accent-ink}"
    typography: "{typography.button}"
    rounded: "{rounded.hairline}"
  button-board-primary:
    backgroundColor: "{colors.lamp-amber}"
    textColor: "{colors.ink-on-amber}"
    typography: "{typography.button}"
    rounded: "{rounded.hairline}"
  button-cord-stop:
    backgroundColor: "{colors.lamp-red}"
    textColor: "{colors.ink-reversed}"
    typography: "{typography.button}"
    padding: "15px 20px"
  pill:
    backgroundColor: "{colors.paperwork}"
    textColor: "{colors.ink-2}"
    typography: "{typography.meta}"
    rounded: "{rounded.pill}"
    padding: "0.08rem 0.45rem"
  pill-accent:
    backgroundColor: "{colors.accent-wash}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.pill}"
  pill-red:
    backgroundColor: "{colors.red-wash}"
    textColor: "{colors.red}"
    rounded: "{rounded.pill}"
  pill-green:
    backgroundColor: "{colors.green-wash}"
    textColor: "{colors.green}"
    rounded: "{rounded.pill}"
  card:
    backgroundColor: "{colors.paperwork}"
    rounded: "{rounded.hairline}"
    padding: "1rem 1.1rem"
  input:
    backgroundColor: "{colors.paperwork-lift}"
    textColor: "{colors.ink}"
    rounded: "{rounded.hairline}"
    padding: "0.45rem 0.6rem"
  lamp:
    backgroundColor: "{colors.lamp-off}"
    rounded: "{rounded.lamp}"
    size: "22px"
  lamp-mini:
    backgroundColor: "{colors.enamel-3}"
    rounded: "{rounded.lamp}"
    size: "9px"
  verdict-band-ship:
    backgroundColor: "{colors.v-band-ship}"
    textColor: "{colors.ink-reversed}"
    typography: "{typography.verdict}"
  verdict-band-rulings:
    backgroundColor: "{colors.v-band-rulings}"
    textColor: "{colors.v-band-rulings-ink}"
    typography: "{typography.verdict}"
  verdict-band-stop:
    backgroundColor: "{colors.v-band-stop}"
    textColor: "{colors.ink-reversed}"
    typography: "{typography.verdict}"
---

# Design System: Fabrika

## Overview

**Creative North Star: "The Andon Board"**

Fabrika's console is an andon board hung over a production line. Model agents work the stations; the gates are where the line stops; a person decides whether it restarts. The console has two grounds and keeps them apart. **The board** is enamelled steel (near-black in the default scheme) and carries everything above the fold on a feature screen: the title, the step strip, sixteen station lamps, the rework return rail, and the gate actions. **The floor** is painted concrete (a pale green-grey) and carries the record underneath: criteria, decisions, findings, ledgers, prose. The board is for the glance. The floor is for reading, and it is deliberately quieter: shop paperwork, not signage.

Colour is information, never decoration. A lamp is lit by the line's own state and by nothing else. A separate amber ink means "you are required". Graphite is everything the machine has settled. Nothing is styled to look important. Something is important because of what it counts, or because a station said so. The register is a technical instrument: condensed silkscreen lettering for labels and controls, an engineered sans for prose, and a monospace for machine facts.

The console is served as static files with no build step. The token file is the single source of every colour, and each later stylesheet reads names from it rather than stating colours of its own.

The CSS and JS carry long comments explaining why each rule exists. They are part of the design. Read the comment above a rule before changing it.

**Key Characteristics:**
- Two grounds, board and floor, each with its own ink ramp (`--ink*` on the floor, `--board-ink*` on the board).
- Five lamp states (green, amber, red, dark, skipped) plus white for "at work now". They are the only saturated colours, and the line lights them.
- Two registers kept apart. The lamps say where the run is. The step strip says where you are.
- One amber ink, `--accent`, means "you are required".
- Square, ruled surfaces with a 2px radius. Flat except for overlays.
- Three schemes (default, day, night). A scheme may change a lamp's lightness but never its hue or meaning.
- No bundler and no build step: plain CSS in link order, classic scripts, and Preact + htm modules from `console/vendor/`.

## Colors

The palette is green-grey neutrals on two grounds, with a few saturated lamps that only the line may light. Every value lives in `console/css/tokens.css`. The frontmatter above holds the **default** scheme. `day` and `night` are lists of overrides on `:root[data-theme=…]`, set before first paint by the inline script in `console/index.html` from `localStorage['fabrika:scheme']`.

### Primary (the lamps, lit by the line)
- **Lamp Green** (`--lamp-green`): this station passed its work downstream.
- **Lamp Amber** (`--lamp-amber`): this station called for help. The loop is handling it and you are not needed yet. Lettering on amber is `--ink-on-amber`, because amber is the one lamp light enough to need dark ink.
- **Lamp Red** (`--lamp-red`): this station stopped the line. A disrupted stop bar and the cord's stop button use it at full strength.
- **Lamp White** (`--lamp-white`): the station working right now. In day it is the darkest lamp on the light plate.
- **Lamp Off** (`--lamp-off`, `--lamp-off-paper` on the floor): not reached. The line never got here, so the station has no opinion.
- **Skipped**: has no fill of its own. It is drawn as a transparent lamp with a dashed `--board-rule` ring, meaning the station was reached and had nothing to do.
- **Prior Green** (`--lamp-green-prior`): a pass carried over from an earlier round, dimmed and without a halo.
- **Board lettering in lamp hues** (`--board-red`, `--board-amber`, `--board-green`): the lamp's hue used as words on the enamel. These are separate tokens because a colour that works as a lamp can fail as text (amber words on a light plate measure 2.3:1).

### Secondary (ink that means something)
- **Required Amber** (`--accent`, `--accent-ink`, `--accent-wash`): "you are required", as ink on the floor. Uses: `pill-accent` on stages that need you, the gate step on the strip, the armed button, the repair group head, and the focus ring.
- **Refusal Red** (`--red`, `--red-wash`, plus `--red-deep`, `--red-ink`, `--red-ink-deep`, `--red-edge`, `--red-flag`): errors, failed pills, removed lines, danger buttons, and the novel materiality class.
- **Settled Green** (`--green`, `--green-wash`, plus `--green-ink`, `--green-ok`, `--green-edge`): accepted pills, added lines, and the quiet rule above a stop bar that ships.
- **Amber ink** (`--amber`, `--amber-wash`, `--amber-edge`, `--amber-line`, `--amber-wash-deep`): has the same value as `--accent`. Used for cautions that do not require you, such as untested marks and route trouble.

### Tertiary (places, not states)
- **The four lanes** (`--lane-plan` blue, `--lane-build` green, `--lane-verify` ochre, `--lane-review` orange): a place on the line, not a state. Each lane keeps one hue everywhere it appears, including the timeline, the station list and the rail. `--lane-*-ink` is the darker version for 10px uppercase words on the floor. `--lane-*-lit` is the version for the board.
- **The two crews** (`--team-build` blue, `--team-check` = `--red`): the crew diagram's two sides. Builders are blue and checkers are red, so the check side reads as the side that says no.
- **The verdict grounds** (`--v-band-ship`, `--v-band-rulings`, `--v-band-stop`, each with a `-rule` and an ink): one solid ground for each answer. Ship and stop take white lettering. Rulings is light and takes `--v-band-rulings-ink`.

### Neutral
- **Shop Floor** (`--paper`): the page ground.
- **Paperwork** (`--surface`, `--surface-sunk`, `--surface-lift`): cards, sunk wells, and paper lifted off the paperwork (fields and hovers).
- **Floor Ink** (`--ink`, `--ink-2`, `--ink-3`): lettering, prose, and quiet meta. `--ink-3` is tuned to 4.5:1 on the floor in default and night.
- **Rules** (`--line`, `--line-strong`): hairlines and control edges. These are not text colours.
- **Enamel** (`--panel`, `--panel-2`, `--panel-3`, `--rail`, `--board-edge`, `--board-seam`): the board plate, the side rail's shade, the board's bottom lip, the rework rail's stroke, and the plate's inner and outer edges.
- **Board Ink** (`--board-ink-hi`, `--board-ink`, `--board-ink-2`, `--board-ink-3`, `--board-ink-4`, `--board-faint`, `--board-rule`): a seven-step ramp with nothing between the steps. `hi` marks where you are, a title, or a hovered link. `ink` is lettering. `2` is prose. `3` is quiet. `4` is the quietest text still meant to be read, including a disabled control's label. `faint` means not reached or switched off, and is also a button's edge. `rule` separates words.
- **Materiality** (`--mat-generated`, `--mat-mechanical`, `--mat-conventional`, `--mat-novel` = `--red`): a ramp from light to heavy. Only the novel class gets a hue, so the eye lands on code nobody has reviewed.

### Per scheme

| Role | default | day | night |
|---|---|---|---|
| `--paper` (floor) | #dcdfdb | #ffffff | #1b1f1a |
| `--surface` | #f4f5f2 | #f6f8fa | #24281f |
| `--surface-sunk` | #e9ebe7 | #eef1f4 | #15180f |
| `--surface-lift` | #ffffff | #ffffff | #2e342c |
| `--ink` / `-2` / `-3` | #171a17 / #4a504a / #5c645c | #1f2328 / #3d444d / #59636e | #e7eae6 / #aeb6ac / #8d958b |
| `--line` / `--line-strong` | #c2c6c1 / #a2a89f | #d1d9e0 / #afb8c1 | #343a32 / #4e564d |
| `--panel` / `-2` / `-3` (board) | #171a17 / #21251f / #2d322b | #ffffff / #f6f8fa / #e3e8ee | #0c0f0b / #14170f / #242920 |
| `--board-ink-hi` → `--board-faint` | #fff → #5d655c | #1f2328 → #656d76 (reversed) | unchanged from default |
| `--lamp-green` / `amber` / `red` | #2fa360 / #f0a500 / #e03127 | #1f8b52 / #d18f00 / #cd291f | unchanged from default |
| `--accent` / `--accent-ink` | #9a6300 / #7d5100 | #8a5800 / #6f4800 | #e3ad44 / #f0c368 |
| `--red` / `--green` | #c2231b / #1d6b46 | #b81f18 / #1a6342 | #f0736a / #52b681 |

**How the schemes relate.** A scheme moves one ground onto the other and leaves the second where it is. It does not invert the console. In **day**, the board joins the floor: painted steel, one light room with cool neutral greys and no tint. The plate is *lighter* than the floor so that the board still reads as hanging in front of it. In **night**, the floor joins the board. The paperwork goes dark and the board goes darker still. On a dark ground a raised card is lighter. The wordmark is a raster image, so day swaps the file (`fabrika-logo-day.png`). That swap is the only rule a scheme may add besides token values.

### Named Rules
**The Lamp Rule.** A lamp is lit only by the line's own state, never by design. The meanings are green passed, amber called for help, red stopped the line, dark not reached, and skipped reached with nothing to do. A scheme may change a lamp's lightness for legibility. It may never change a lamp's hue, its meaning, or which state gets which colour.

**The Required-Is-Ink Rule.** "You are required" is said in `--accent` ink: a pill, a gate step, an armed button. A lamp never says it. Whether a project needs a human is a pill. Whether its checks pass is a lamp.

**The No Private Colours Rule.** No stylesheet or script names a colour of its own. Everything reads a token from `tokens.css`, and a new meaning gets a new token there with a comment explaining the argument for it.

**The Lane Is a Place Rule.** Lane hues mark *where* on the line work happens. A lane hue never marks a state, and a state colour never marks a lane.

## Typography

**Display / Label Font:** Saira Condensed (`--display`, `--cond`; fallback Helvetica Neue, Arial)
**Body Font:** IBM Plex Sans (`--body`; fallback Segoe UI, system-ui)
**Mono Font:** Roboto Mono (`--mono`) for counts, paths, line numbers and meta. IBM Plex Mono (`--mono-prose`) for long machine prose such as role prompts and markdown in the sheet.

**Character:** The labels are condensed silkscreen, the lettering stamped on a plate. Paragraphs use an engineered sans with open apertures, the difference between a face that works on a two-word button and one that works over six lines. Roboto Mono is a UI face, read a word at a time. Plex Mono carries text that is long and also has to be monospaced. The body sets `font-variant-numeric: tabular-nums` globally so counts line up.

Fonts load from Google Fonts in `console/index.html`: Saira Condensed 400–700, IBM Plex Sans 300–600 plus italic 400, IBM Plex Mono 400–600, Roboto Mono 400–500.

### Hierarchy
- **Display** (Saira Condensed 600, 26px, line-height 1, tracked .05em, uppercase; 22px below 860px): the board's title row, which is the feature or project name.
- **Headline** (Saira Condensed 600, 1.5rem, 1.25): `h1` on screens without a board. Page titles such as crew and as-built are set uppercase at .1em.
- **Title** (Saira Condensed 600, 1.0625rem / `--t-sec`): section headings. Record section heads (`.pk-h`) are uppercase at .12em.
- **Verdict** (Plex Sans 300, 18–18.5px, 1.45, max 78ch): the one sentence on a stop bar or verdict band. It is light and large because it is the answer.
- **Body** (Plex Sans 400, 16px, 1.5; 15px below 520px): page prose.
- **Record body** (Plex Sans, `--t-body` .8125rem): the text you are there to read in the review, such as row titles and paragraphs.
- **Button** (Saira Condensed 600, 13px, 1.1, .05em, uppercase): every `.btn`, in every container.
- **Label** (Saira Condensed 600–700, `--t-label` .625rem, tracked .14–.24em, uppercase): pills on paper, the stop-bar word plate, and station names.
- **Meta** (Roboto Mono 400, `--t-meta` .6875rem): counts, paths in a gutter, line numbers, the masthead nav, and breadcrumbs.

**The review scale** is six steps with nothing between them: `--t-label` .625rem, `--t-meta` .6875rem, `--t-small` .75rem, `--t-body` .8125rem, `--t-head` .9375rem, `--t-sec` 1.0625rem. A size half a pixel off a step reads as carelessness rather than hierarchy. Use the nearest step.

### Named Rules
**The Lettering Lives on the Button Rule.** How a button is lettered belongs to `.btn` itself, never to the container it sits in. A container may set size and colour. It never sets the face.

**The Right Face for the Length Rule.** Saira Condensed letters labels, buttons, stations and counts, and is never used for paragraphs. Plex Sans sets prose. Roboto Mono sets single machine facts. Plex Mono sets long monospaced text.

**The Counted Number Rule.** The only figures set large are counted ones: plan, actual, defect. A number earns size by what it counts, not by being styled.

## Layout

**The shell.** Four parts:
- **The rail** (`.panel`) is fixed on the left. `--panel-w` is 264px in `tokens.css` and overridden to 238px in `reskin-shell.css`. The rail shows status, not navigation: it answers what is in flight and where. It has search, filter chips (All / Needs you / Building / Idle), drag-to-reorder project rows separated by hairlines rather than gaps, and the favicon mark at its foot. It is shaded `--panel-2` so that only the board is the prominent dark element.
- **The masthead** (`.masthead`) is sticky, `--mast-h` (38px). On screens with a board it hides (`body.has-board`), and the board's own header row carries the title on the left and the nav plus scheme picker on the right.
- **`#featurebar`** sits outside `#main`, because `#main` is a mount point that the Preact screens clear.
- **`#main`** is the content column.

**Measures.** `.wrap` is 1080px. `.hall` (the board) and `.mast-in` are `1080px + 2 × --gutter`, so the board's content starts at the same x position as the page content. `.wrap.wide` is 1400px, for working screens such as gate 1. `.wrap.doc-wrap` is 1280px with a `.narrow` reading measure of 720px inside it. Prose otherwise caps at 58–92ch, depending on the paragraph's job.

**Spacing.** `--gutter` (24px; 14px below 520px) is the page and board inset. `--sheet-gap` (22px) is how much board stays visible around a role sheet. Sections stand 2.25–2.5rem apart. Rows inside the record are ruled with 1px `--line` hairlines rather than separated by air.

**The board.** From top to bottom: the header row, then the step strip and actions on one line, then the station strip (sixteen lamps grouped into four lanes with silkscreen captions, and the rework return rail drawn underneath). The board ends in a 5px `--panel-3` lip. The station cells may shrink (`flex: 1 1 58px`) so that all sixteen fit without a scrollbar. The board is a glance, and a board you have to scroll stops being a glance. The strip folds to a row of 9px mini-lamps plus a count, and the actions move up onto that line.

**Reading mode** (`body.reading`) is for documents: the spec and gate 2. It hides the rail, masthead, board and feature bar so the window holds only the document. A single fold control stays sticky at the column's top right, and Esc also exits.

**Breakpoints actually used** (all `max-width` unless noted):
- **1454px / 1216px** (`min-width`): the reading-mode fold control becomes sticky only once there is room beside the column (rail in / rail out).
- **1100px**: the spec's contents rail drops, and two-column requirement and evidence layouts stack.
- **1023px**: the rail stops being a fixture and becomes a drawer over a scrim (`body[data-panel="open"]`). The page takes the full window.
- **900px / 860px**: the board header, stop bar and cord stack vertically, the cord's pull moves under its body, and strip notes un-indent.
- **780px**: the step strip drops its interpuncts.
- **720px / 640px**: tables drop their numeric and band columns.
- **520px**: `--gutter` becomes 14px, body becomes 15px, and the materiality bar hides its segment labels.

Other one-off widths exist (1080, 1000, 760, 700, 560, 40rem). Prefer the set above for new work.

### Named Rules
**The Two Registers Rule.** The lamps say where the *run* is. The step strip says where *you* are (`at` = what you are reading, `here` = where the feature is, `ahead` = nothing recorded yet, `gate` = waiting on you). Never merge them into one mark or one colour.

**The Return Rail Rule.** The rework return rail is part of the board's layout whether or not anything is on it. It is solid when rework ran and dashed when it did not.

**The Glance Is Not the Record Rule.** The board summarises and the record holds the facts. A change that turns information on the floor into a glance-only simplification is a regression.

## Elevation & Depth

The system is flat. Depth comes from the ground: enamel board over painted floor, paperwork on floor, and a sunk well or a lifted field. Hairlines do the rest. Surfaces at rest have no shadow. Shadows appear only on things that float over the page, and on lamps, where the halo is the light itself.

### Shadow Vocabulary
- **Lamp halo** (`0 0 0 3px` of the lamp's own hue at 20%; red uses 4px at 24%): a lit lamp's glow. Mini-lamps use `0 0 5px -1px` of their hue.
- **Popover** (`0 6px 20px rgba(24,26,22,.16)`): the stats sheet under the verdict band, and toasts.
- **Dialog** (`0 12px 28px rgba(23,26,23,.22)`, backdrop `rgba(23,26,23,.42)`): the confirmation that stops.
- **Sheet** (`0 22px 60px rgba(23,26,23,.34)`): the role or guide editor that rises over the board.
- **Drawer** (`0 0 40px -12px rgba(20,24,26,.5)`, scrim `rgba(20,24,26,.32)`): the rail as a drawer below 1023px. The help drawer uses `-18px 0 40px rgba(0,0,0,.18)`.
- **Inset rules** (`inset 0 -2px 0 var(--ink)` and similar): underline markers on steps and tabs. These are not shadows in the depth sense.

### Named Rules
**The Flat-By-Default Rule.** Nothing on the page casts a shadow. Only overlays (popover, dialog, sheet, drawer) and lit lamps do.

## Shapes

The shapes are square and ruled, like stamped plate and shop paperwork. The global radius is `--radius` (2px), used on cards, buttons, fields, chips and the focus ring. A 3px radius appears on key caps, the reading and stats toggles, and tab underlines. Pills (status pills and the paper step strip's steps) are fully round at 999px. Lamps are circles. The materiality dot is an 8px square at 2px. The composer and other large writing surfaces have no radius at all, so they read as paper laid on the floor rather than a gap in it.

Borders are 1px hairlines in `--line` (structure) or `--line-strong` (control edges). Heavier rules carry meaning. A 4px top rule sits on stop bars and verdict bands. The stop bar's word plate and the verdict word have a 2px `currentColor` box. The capability nameplate uses a `double` border. A skipped lamp gets a dashed ring.

Icons come from one inline SVG sprite in `console/index.html`, stroked in `currentColor`. The console's own `i-*` icons are drawn at 16×16 with stroke 1.4. Lucide icons (`i-l-*`, ISC) are copied verbatim at 24×24 with stroke 2.1, which is the same ratio. Row icons render at 15px (`.ic`), 13px or 11px. A page's mark (`.page-mark`) renders at 2.1rem beside its heading. To add an icon, paste its path data into the sprite. Nothing is fetched at load time.

## Components

The CSS lives in `console/css/`, and the stylesheets load in the order `console/index.html` links them: a later file overrides an earlier one, so **link order is part of the design**. Screens are rendered either by the classic scripts in `console/app/` (string templates, hash router) or by the Preact + htm modules in `console/ui/` (gate 2, gate 1 questions, rework, start). The modules are used where the page refreshes under someone who is typing.

| Component | Styles | Rendered by |
|---|---|---|
| Rail, masthead, buttons, pills, paper step strip, icons, timeline base | `base.css`, re-skinned in `reskin-shell.css` | `app/rail.js`, `app/header.js`, `app/steps.js` |
| Board, station lamps, board step strip, return rail, stop bar, cord, consequence panel | `reskin-board.css`, `reskin-shell.css` | `app/steps.js`, `app/actions.js`, `app/running.js`, `app/review.js`, `app/intake.js` |
| Verdict band, gate-2 tabs, work map, worklist | `review.css`, `gate2.css` | `ui/tabs.js`, `ui/workmap.js`, `ui/worklist.js`, `ui/preview.js` |
| Result and material marks | `review.css` | `ui/marks.js` |
| Capability nameplate, gate 0 | `reskin-shell.css`, `gate0.css`, `gate0-steps.css`, `project.css` | `app/project.js`, `app/project-screen.js`, `app/yard.js` |
| Yard (project ledger with lamps) | `yard.css` | `app/yard.js` |
| Crew diagram, route cards, confirm dialog, sheet | `crew.css`, `runs-crew.css` | `app/agents.js`, `app/routes.js`, `app/actions.js`, `app/sheet.js` |
| Folds, ledger rows, toast | `folds-toast.css` | `app/ledger.js`, `app/util.js` |
| Stage, verdict and severity words | n/a | `app/vocab.js` (also `globalThis.fabrikaVocab`) |

### Buttons
- **Shape:** 2px radius, 1px `--line-strong` edge, padding .45rem .85rem (`.btn-sm` .34rem .8rem).
- **Default:** `--surface` ground, `--ink` lettering, in the button face (Saira Condensed 600, 13px, uppercase, .05em). On hover the ground lifts to `--surface-lift` and the edge darkens to `--ink-3`. Disabled buttons sit at 45% opacity with a not-allowed cursor.
- **Primary:** an `--ink` ground with `--paper` lettering, so the action a screen wants pressed is the darkest thing on it. Hover mixes ink 82% toward paper, which works in every scheme.
- **Quiet:** no border and `--ink-3` lettering. This is the way out, and it does not read as a third choice.
- **Armed:** a button that is asking a question rather than naming an action (`--accent-wash` ground, `--accent` edge). It disarms itself after a few seconds.
- **Danger:** `--red` ground, darkening to `--red-deep` on hover. Inside the cord it uses `--lamp-red` with `--ink-reversed`.
- **On the board:** outline buttons with a `--board-faint` edge and `--board-ink` lettering. The board's primary is filled `--lamp-amber` with `--ink-on-amber`.

### Pills
- **Style:** Roboto Mono .6875rem, .04em, fully round, 1px edge. Neutral pills use `--surface` and `--ink-2`.
- **State:** `pill-accent` (needs you), `pill-red` (failed), `pill-amber`, `pill-green` (accepted). Each uses its wash as the ground, its ink as the text, and an edge mixed to 30%. `app/vocab.js` decides which stage gets which pill. Only stages that need you (`awaiting_answers`, `awaiting_spec_approval`, `awaiting_cut_approval`, `awaiting_verdict`) are accent. `accepted` is green, `failed` is red, and every other stage is neutral.

### Cards / Containers
- **Corner Style:** 2px.
- **Background:** `--surface` on the floor, with a 1px `--line` edge.
- **Shadow Strategy:** none (see Elevation & Depth).
- **Internal Padding:** 1rem 1.1rem.
- **Error box:** `--red-wash` ground and a 3px `--lamp-red` left rule. Machine output inside it gets the full width of the document and keeps its own line breaks.

### Inputs / Fields
- **Style:** `--surface-lift` (paper lifted off the paperwork), a 1px `--line-strong` edge, 2px radius.
- **Focus:** the global ring, or an `--accent` edge on textareas.
- **The composer** (start screen) is the one place where you write rather than rule. It is drawn as paper: full width, square, 1px `--line` rule, Plex Sans at 1.25rem/1.55.

### Navigation
- **The rail:** project names in Saira Condensed 600 .8125rem, counts in mono `--t-meta`. The current project is lettered `--accent-ink`. The hover and current feature row fills `--panel-3`. Filters are buttons.
- **The masthead / header nav:** Roboto Mono 11px in `--board-ink-3`, with `hi` on hover and `ink` for the current page. Separators are `--board-rule` interpuncts. The nav wraps rather than overflowing.
- **The scheme picker** is last in the nav, after a rule. Each chip is a two-band square showing that scheme's board and floor. The chosen scheme gets a ring (not the nav's underline) and is the only one that shows its name.

### The Station Lamp (signature)
A 22px circle with a 2px `--board-seam` ring. Lit lamps get a small specular highlight and a halo in their own hue. A blinking lamp (the station working now) steps hard between full and 34% opacity every 1.6s, like a real lamp rather than a fade. Under each lamp are its number (Roboto Mono) and name (Saira Condensed 600, uppercase). The mini-lamp (9px) is the same lamp in the folded strip and the yard.

### The Stop Bar and Verdict Band (signature)
Colour appears here only when the line is disrupted. `.stopbar.disrupted` is a `--lamp-red` field with white lettering. `.stopbar.good` is transparent with a 4px `--green` top rule, because good news does not need a field. Both carry a word plate (Saira Condensed 700, 13px, .24em, boxed in 2px `currentColor`), the verdict sentence in light Plex, and a foot of mono figures. Links in the foot are underlined, not emphasised. The gate-2 verdict band uses the same layout on three grounds (ship, rulings, stop), and the word plate says the answer in words for a reader who cannot see the colours.

### The Cord (signature)
The operator's right to stop the line, and the only control drawn as an object: an enamel panel with a pull-cord illustration and the caption "any station may stop the line". Pressing it opens the **consequence panel** (`--panel-2`, 3px `--lamp-red` top rule). The panel lists what the ruling will carry, such as unrepaired findings or unbuilt requirements. Only then does a confirm button rule. The **confirmation dialog** (`.confirm`) is reserved for controls that cannot be undone by pressing the same button again.

### Marks (`ui/marks.js`)
Two families of mark, and nothing wears both. **Results** on criteria are glyphs: ✓ verified (green), ✗ failing (red), ○ untested (amber), — no code (red). **Materiality** on files is a diamond: filled ◆ for novel, hollow ◇ for everything else. The two families differ in shape before colour, so the distinction holds for a reader without the colour channel. The four-class materiality ramp still sorts columns and draws the `.mat-mini` bar.

### Toast
Fixed below the masthead and centred over the pane (not the window). Width is min(92vw, 560px), with a 2px radius and the popover shadow. Info toasts are inverted: `--paper` lettering on an `--ink` ground. Errors carry a red mono tag. Toasts rise 4px over 140ms.

## Do's and Don'ts

### Do:
- **Do** read every colour from a `tokens.css` name. A new meaning gets a new token with its argument written beside it, and gets values for all three schemes.
- **Do** light a lamp only from the line's state: green, amber, red, dark, skipped, or white for "at work now".
- **Do** say "you are required" in `--accent` ink as a pill, a gate step or an armed button.
- **Do** keep the board (glance) and the floor (record) as separate grounds with separate ink ramps.
- **Do** use the six-step review scale (`--t-label` … `--t-sec`) and snap other sizes to the nearest step.
- **Do** make anything that opens something a `<button>` (with `aria-expanded` where it discloses).
- **Do** give every animation and transition a `prefers-reduced-motion: reduce` exit. `folds-toast.css` ends with a global one, and components add their own.
- **Do** state a control's consequence before it fires, as the consequence panel and armed buttons do.
- **Do** announce changes that happen without user action through the `#live` region (`role="status"`, `aria-live="polite"`), and transient notes through `#toast`.
- **Do** keep the focus ring visible: 2px `--accent` outline at 2px offset globally, `--lamp-amber` on the enamel, `--vb-ink` on a verdict band, and an inset −2px offset on full-width rows.
- **Do** add icons by pasting path data into the sprite in `index.html` (16px at stroke 1.4, or Lucide at 24px with stroke 2.1).
- **Do** keep link order in `index.html` meaningful: put new rules in the file for their area, after the rules they correct.

### Don't:
- **Don't** add a bundler, a build step, a CSS preprocessor or a runtime dependency fetched at load. The console is served as files.
- **Don't** use a lamp colour as decoration, and don't use a lane hue as a state.
- **Don't** spend `--accent` on running, selection, links, branding or ordinary submit buttons. Ordinary primary actions are `--ink`.
- **Don't** use gradients, purple or blue "AI" palettes, or decoration that outweighs the data. Don't use the literal factory imagery of gears, rivets or hazard stripes.
- **Don't** put a shadow on a resting surface.
- **Don't** set paragraphs in Saira Condensed or label buttons in the body face.
- **Don't** use `--line` or `--line-strong` as a text colour.
- **Don't** add a changed-file list to the review. Code is reached through the criterion that asked for it, or through "Not asked for".
- **Don't** make the board scroll horizontally to show the line. Sixteen stations fit on one row.
- **Don't** collapse the step strip and the lamps into one indicator.
