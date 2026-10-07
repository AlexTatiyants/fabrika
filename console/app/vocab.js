'use strict';
/* The words for a stage, a verdict and a severity -- once.

   Kept as a table per fact and per screen -- a stage's word, band, step, gate
   and pill each in its own, with the review screens keeping copies of their
   own -- they drift: one screen names an outcome differently from another, and
   a stage like `waiting_for_plan` has a word on one screen and none on the
   next. Everything here is read by the classic scripts by name and by the
   review modules through `globalThis.fabrikaVocab` (ui/shared.js). Loaded
   before anything else. */

/* Per stage:
     word      what it is called, in the rail and on the board
     band      where in the run it sits, as the diagram names it
     step      which step of the feature's strip it is on
     gate      what the human owes, in the strip's words, when they owe it
     gateStep  the step that gate is on
     active    still moving, or still owed something: shown on the rail
     needsYou  waiting on a person
     settled   a packet exists and the run is over
     pill      the colour a stage pill is drawn in */
const STAGES = Object.freeze({
  intake: { word: 'intake', band: 'spec', step: 'scout', active: true, pill: '' },
  writing_spec: { word: 'writing the spec', band: 'spec', step: 'spec', active: true, pill: '' },
  awaiting_answers: {
    word: 'awaiting answers', band: 'spec', step: 'questions', gate: 'spec review · you',
    gateStep: 'questions', active: true, needsYou: true, pill: 'pill-accent' },
  awaiting_spec_approval: {
    word: 'awaiting freeze', band: 'spec', step: 'spec', gate: 'spec review · freeze',
    gateStep: 'spec', active: true, needsYou: true, pill: 'pill-accent' },
  awaiting_cut_approval: {
    word: 'awaiting plan review', band: 'build', step: 'build', gate: 'plan review · you',
    gateStep: 'build', active: true, needsYou: true, pill: 'pill-accent' },
  waiting_for_plan: { word: 'waiting for the plan', gate: 'frozen · waiting for the plan', pill: '' },
  building: { word: 'building', band: 'build', step: 'build', active: true, pill: '' },
  awaiting_verdict: {
    word: 'awaiting verdict', band: 'review', step: 'review', gate: 'feature review · you',
    gateStep: 'review', active: true, needsYou: true, settled: true, pill: 'pill-accent' },
  accepted: { word: 'accepted', band: 'review', step: 'review', settled: true, pill: 'pill-green' },
  rejected: { word: 'rejected', band: 'review', step: 'review', settled: true, pill: '' },
  failed: { word: 'failed', band: 'review', step: 'build', active: true, pill: 'pill-red' },
});

/* A packet's verdict: its words (`word`), the rail's few characters (`rail`),
   and the tone it is drawn in. */
const VERDICTS = Object.freeze({
  ship: { word: 'Ships', rail: 'ready', tone: 'ship' },
  ship_with_rulings: { word: 'Ships, with rulings', rail: 'rulings', tone: 'rulings' },
  send_back: { word: 'Do not ship', rail: 'do not ship', tone: 'stop' },
  reject: { word: 'Reject', rail: 'reject', tone: 'stop' },
});

const SEVERITY_ORDER = Object.freeze({ blocker: 0, major: 1, minor: 2, nit: 3 });

globalThis.fabrikaVocab = Object.freeze({ STAGES, VERDICTS, SEVERITY_ORDER });

/* The shapes the screens read, derived from the table rather than kept. */
const stagesWhere = (key) => Object.keys(STAGES).filter((s) => STAGES[s][key]);
const stageField = (key) => Object.fromEntries(
  Object.entries(STAGES).filter(([, s]) => s[key]).map(([id, s]) => [id, s[key]]));

const ACTIVE_STAGES = stagesWhere('active');
const NEEDS_YOU = stagesWhere('needsYou');
const SETTLED_STAGES = stagesWhere('settled');
const STAGE_WORDS = stageField('word');
const STAGE_BAND = stageField('band');
const STEP_OF_STAGE = stageField('step');
const GATE_OF = stageField('gate');
const GATE_STEP = stageField('gateStep');
const VERDICT_WORD = Object.fromEntries(
  Object.entries(VERDICTS).map(([id, v]) => [id, [v.word, v.tone]]));
const RAIL_VERDICT = Object.fromEntries(
  Object.entries(VERDICTS).map(([id, v]) => [id, [v.rail, v.tone]]));

/* A step of a run as a person reads it, where the pipeline's own name for it
   is not the word the rest of the console uses. The crew page and the help
   call the project's checks "checks"; the step that runs them is `gates`. */
const STEP_WORD = Object.freeze({ gates: 'checks' });
const stepWord = (name) => STEP_WORD[name] || name;
