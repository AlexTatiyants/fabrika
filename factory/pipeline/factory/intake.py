"""Intake and gate 1: scout, interrogator, spec writer and spec checker.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any, Callable, Iterable

from ...projects import ProjectError
from ...sandbox import Sandbox, working_tree_drift
from ...schemas import (
    FeatureState,
    InterrogationReport,
    PhaseState,
    ResolvedAnswer,
    ScoutReport,
    Spec,
    CheckObjection,
    CheckReport,
    SpecRevision,
)
from ...store import EvidenceStore
from ...workspace import (
    unchecked_levels,
    repo_tree,
    slice_coverage,
    symbol_index,
    slice_digest,
    slice_repo,
    spec_hash,
    spec_text,
    fixture_inventory,
    verify_context,
)

from ..text import PHASE_NAMES, INTAKE_PHASES, reset_phases, _now, _slug, _json
from ..records import current_payload
from ..ownership import this_process, owner_is_alive
from ..checks.cut import anchored_objections, settle_objections, objection_block, criteria_changed
from ..checks.blind import check_spec_testability
from ..packet import merge_scouts


class IntakeMixin:
    # -- intake -----------------------------------------------------------

    def working_tree_drift(self) -> dict[str, Any]:
        """Checked before a token is spent, because the alternative is finding
        out from a packet."""
        return working_tree_drift(self.project.repo_path, self.project.base_ref)

    def create_feature(
        self, intent: str, title: str = "", allow_dirty: bool = False,
    ) -> FeatureState:
        """Mint the feature and its sandbox. Fast, and safe to await in a request.

        The stage is `intake` and not `awaiting_answers`: nothing has been asked
        yet, and a feature that says it is waiting on you before the interrogator
        has run is a lie the console will faithfully repeat.
        """
        self.project.require_ready()

        # Refused before the sandbox is made, so a rejected feature leaves
        # nothing behind. Overridable, because sometimes a scratch file really
        # is a scratch file -- but never silent.
        drift = self.working_tree_drift()
        if drift.get("drifted") and not allow_dirty:
            parts = []
            if drift["files"]:
                parts.append(
                    f"{drift['files']} uncommitted change(s) "
                    f"({drift['modified']} modified, {drift['untracked']} untracked)"
                )
            if drift["ahead"]:
                parts.append(
                    f"{drift['ahead']} commit(s) on {drift['branch']!r} that "
                    f"{drift['base_ref']!r} does not have"
                )
            fix = ("Commit them" if drift["files"] and not drift["ahead"]
                   else f"Point this project's base ref at {drift['branch']!r} in its settings"
                   if drift["ahead"] and not drift["files"]
                   else f"Commit them and point the base ref at {drift['branch']!r}")
            raise ProjectError(
                f"{self.project.repo_path} has " + ", and ".join(parts) + ". Features branch from "
                f"{drift['base_ref']} ({drift['base_sha'][:12]}), so every agent would read a "
                "codebase without that work -- and report on it truthfully, which is the part that "
                f"wastes your time. {fix}, or start anyway if none of it bears on this feature."
            )

        title = (title or intent.strip().split("\n")[0][:70] or "untitled").strip()
        feature_id = f"{_slug(title)}-{uuid.uuid4().hex[:6]}"
        store = self.store_for(feature_id)

        # The sandbox is created here rather than at approval, so that the tree
        # the scout digests is the same tree the build writes into. A spec
        # written against one state and built against another is a spec about
        # nothing.
        sandbox = Sandbox.create(
            repo=self.project.repo_path,
            root=self.config.sandbox_path,
            project_id=self.project.id,
            feature_id=feature_id,
            base_ref=self.project.base_ref,
            image=(self.project.environment.image if self.project.environment else ""),
        )
        state = FeatureState(
            feature_id=feature_id, project_id=self.project.id, title=title, intent=intent,
            stage="intake", owner=this_process(), sandbox=sandbox.state,
            phases=[PhaseState(name=n) for n in PHASE_NAMES],
        )
        store.append("drift", drift, role="orchestrator")
        self._save(store, state)
        self._emit(state)
        return state

    async def run_intake(self, feature_id_or_intent: str, title: str = "") -> FeatureState:
        """AC-8.1 -- scout, then interrogator, then stop and wait for a human.

        Accepts either an existing feature id or, for callers that want the whole
        thing in one await, an intent to create first.
        """
        store = self.store_for(feature_id_or_intent)
        if store.has("state"):
            state = self.state_of(store)
        else:
            state = self.create_feature(feature_id_or_intent, title)
            store = self.store_for(state.feature_id)

        try:
            return await self._intake(store, state)
        except Exception as exc:
            state.stage = "failed"
            state.owner = ""   # the run is done with it
            state.error = f"{type(exc).__name__}: {exc}"[:4000]
            self._save(store, state)
            self._emit(state)
            raise

    async def _intake(self, store: EvidenceStore, state: FeatureState) -> FeatureState:
        intent = state.intent
        sandbox = self.open_sandbox(state)
        # The as-built of what this feature starts from, read alongside it so
        # the person who reviews it has the system to read while it runs.
        self._as_built_begin(store, sandbox.state.base_sha, "base")

        async with self._phase(store, state, "scout") as phase:
            scout = await self._scout(store, state, intent, sandbox.path, phase)

        async with self._phase(store, state, "interrogator"):
            await self._interrogate(store, state, intent, scout)

        state.stage = "awaiting_answers"
        state.owner = ""   # the run is done with it
        self._save(store, state)
        self._emit(state)
        return state

    # -- gate 1 -----------------------------------------------------------

    def record_answers(
        self, feature_id: str, answers: dict[str, str], deferred: Iterable[str] = (),
    ) -> list[ResolvedAnswer]:
        """Write down what the human said, before anything is done with it.

        Kept only as a field on the spec, the answers would exist nowhere until
        the spec writer returned -- so a spec writer that takes two minutes,
        fails, or has its request cancelled would take every answer with it.
        What a human typed is evidence in its own right.
        """
        store = self.store_for(feature_id)
        state = self.state_of(store)
        interrogation = InterrogationReport.model_validate(store.payload("interrogation"))
        deferred_ids = set(deferred)

        resolved: list[ResolvedAnswer] = []
        for ambiguity in interrogation.ambiguities:
            answer = (answers.get(ambiguity.id) or "").strip()
            if answer:
                resolved.append(ResolvedAnswer(
                    question_id=ambiguity.id, question=ambiguity.question,
                    answer=answer, source="human",
                ))
            elif ambiguity.id in deferred_ids:
                resolved.append(ResolvedAnswer(
                    question_id=ambiguity.id, question=ambiguity.question,
                    answer=ambiguity.proposed_default, source="deferred",
                ))

        store.append("answers", {
            "resolved": [a.model_dump(mode="json") for a in resolved],
            "asked": len(interrogation.ambiguities),
        }, role="human")
        state.stage = "writing_spec"
        self._save(store, state)
        self._emit(state)
        return resolved

    async def finalize_spec(
        self,
        feature_id: str,
        answers: dict[str, str] | None = None,
        deferred: Iterable[str] = (),
    ) -> Spec:
        """AC-8.2 -- the spec writer writes the spec; the orchestrator, not the model,
        merges the human's answers and carries the unanswered ones as risk.

        `answers` may be omitted when they were already recorded.
        """
        store = self.store_for(feature_id)
        if answers is not None:
            self.record_answers(feature_id, answers, deferred)
        state = self.state_of(store)
        scout = ScoutReport.model_validate(store.payload("scout"))
        interrogation = InterrogationReport.model_validate(store.payload("interrogation"))

        # Answers given before a correction were answers to different questions.
        recorded = current_payload(store.records(), "answers") or {"resolved": []}
        given = {a["question_id"]: a for a in recorded["resolved"]}

        resolved = [ResolvedAnswer.model_validate(a) for a in recorded["resolved"]]
        unanswered: list[str] = [
            f"{a.id} ({a.severity}): {a.question} -- unanswered, carried as accepted risk. "
            f"The interrogator's default was: {a.proposed_default}"
            for a in interrogation.ambiguities
            if a.id not in given or given[a.id]["source"] == "deferred"
        ]

        answers_block = "\n".join(
            f"- {a.question_id}: {a.question}\n    ANSWER ({a.source}): {a.answer}" for a in resolved
        ) or "- (the human answered nothing)"
        "\n".join(f"- {u}" for u in unanswered) or "- (none)"
        # A correction overrode the interrogator's reading. The spec writer has to
        # see it, or the spec quietly reverts to the reading the human rejected.
        corrections = store.payloads("correction")
        corrections_block = (
            "# Corrections the human made to that reading\n\n"
            + "\n".join(f"- {c['correction']}" for c in corrections)
            + "\n\n"
        ) if corrections else ""

        try:
            return await self._write_spec(store, state, scout, interrogation, resolved,
                                    unanswered, corrections_block, answers_block)
        except Exception as exc:
            state.stage = "failed"
            state.owner = ""   # the run is done with it
            state.error = f"{type(exc).__name__}: {exc}"[:4000]
            self._save(store, state)
            self._emit(state)
            raise

    async def _write_spec(self, store, state, scout, interrogation, resolved, unanswered,
                    corrections_block, answers_block) -> Spec:
        unanswered_block = "\n".join(f"- {u}" for u in unanswered) or "- (none)"
        async with self._phase(store, state, "spec_writer"):
            # What a test in this project can already get hold of. The spec writer
            # writes `verified_at` and `setup` for every criterion, and without
            # this it does both blind: the survey records a fixture inventory at
            # gate 0, and this is where intake is shown it. A spec
            # whose criteria need a user nothing can create is a spec written
            # without this list.
            inventory = fixture_inventory(self.project.state.testing)
            fixture_block = (
                inventory
                + "\nName these in each criterion's `setup`, exactly as written above, when "
                "reaching the behaviour needs one. A criterion needing something that is not "
                "here is not a mistake to hide -- say what it needs in plain words and spec "
                "review puts it in front of the human before the freeze.\n\n"
                "If a criterion's whole subject is adding one of those missing affordances -- "
                "'a test can create an operator the server will accept' -- put the fixture it "
                "adds in that criterion's `provides`, named as a later `setup` would name it. "
                "Every other criterion may then name it in `setup` freely: it is a thing this "
                "spec builds, not a gap. Without `provides` the comparison has only the survey "
                "to go on, and a spec whose job is to make this repository testable gets every "
                "criterion that uses its own new fixtures reported as unverifiable.\n\n---\n\n"
            ) if inventory else ""

            writer_prompt = (
                    f"# Intent\n\n{state.intent}\n\n---\n\n"
                    f"# Scout report\n\n{_json(scout)}\n\n---\n\n"
                    + fixture_block
                    + f"# The interrogator's reading of the intent\n\n{interrogation.restated_intent}\n\n"
                    + corrections_block
                    + f"# Answers settled by the human at gate 1\n\n{answers_block}\n\n"
                    f"# Questions the human did not answer\n\n{unanswered_block}\n"
            )
            spec: Spec = await self.llm.ask(
                "spec_writer", writer_prompt, Spec, system=self.role_prompt("spec_writer"),
            )

        def seal(draft: Spec) -> Spec:
            # The model does not get to decide what the human said -- neither
            # the first time nor when it revises at the spec checker's word.
            draft.intent = state.intent
            draft.title = draft.title or state.title
            draft.resolved_answers = resolved
            for item in unanswered:
                if item not in draft.open_questions:
                    draft.open_questions.append(item)
            for i, criterion in enumerate(draft.acceptance_criteria, start=1):
                if not re.fullmatch(r"AC-\d+", criterion.id or ""):
                    criterion.id = f"AC-{i}"
            return draft

        spec = await self._check_spec(store, state, seal(spec), writer_prompt, answers_block,
                                      seal)

        state.spec_hash = spec_hash(spec)
        store.append("spec", spec, role="spec_writer", model=self._answered_by("spec_writer"),
                     spec_hash=state.spec_hash, prompt=writer_prompt)
        # Which of these this project can actually verify, said while the spec is
        # still a draft. Otherwise the same facts arrive in the packet, three
        # hours and a full build later, by which time freezing them has stopped
        # being a decision anybody can revisit.
        untestable = check_spec_testability(spec, self.project.state.testing,
                                            unchecked_levels(self.project.state))
        if untestable:
            store.append("spec_testability", [f.model_dump(mode="json") for f in untestable],
                         role="orchestrator", spec_hash=state.spec_hash)
        state.stage = "awaiting_spec_approval"
        state.owner = ""   # the run is done with it
        self._save(store, state)
        self._emit(state)
        return spec

    async def _check_spec(self, store, state, draft: Spec, writer_prompt: str,
                          answers_block: str, seal: Callable[[Spec], Spec]) -> Spec:
        """The spec checker reads the draft; the spec writer answers once.

        Its audience is the spec writer, not the human. What it settles never
        reaches the spec review screen -- the spec is simply better. What the
        spec writer rebuts becomes an open question, written here rather than by
        either model, because an open question is already where spec review
        shows accepted risk, and a disagreement between two agents is one.

        Advisory throughout. A checker that fails, or a revision that fails,
        leaves the draft exactly as the spec writer wrote it: intake must not be
        lost to the agent whose whole job was to improve it.
        """
        role = self.config.roles.get("spec_checker")
        async with self._phase(store, state, "spec_checker") as phase:
            if role is None or not role.enabled:
                phase.detail = "no spec checker configured"
                return draft
            draft_hash = spec_hash(draft)
            store.append("spec_draft", draft, role="spec_writer",
                         model=self._answered_by("spec_writer"), spec_hash=draft_hash)
            system = self.role_prompt("spec_checker")
            seats = {
                # Exactly what the oracle will be given. Built by the same
                # function, so this seat cannot see more than that one does.
                "oracle": ("# Your seat: the oracle\n\n"
                           "Below is everything the agent that writes the acceptance tests "
                           "will ever see. Read it as that agent.\n\n---\n\n"
                           + verify_context(draft)),
                "asked": ("# Your seat: what was asked for\n\n"
                          f"# Intent\n\n{state.intent}\n\n---\n\n"
                          f"# What the human answered\n\n{answers_block}\n\n---\n\n"
                          f"# The spec\n\n{spec_text(draft)}\n"),
            }
            criteria = [c.id for c in draft.acceptance_criteria]
            questions = [a.question_id for a in draft.resolved_answers]
            objections: list[CheckObjection] = []
            for seat, prompt in seats.items():
                try:
                    report = await self.llm.ask("spec_checker", prompt, CheckReport, system=system)
                except Exception as exc:
                    store.append("spec_check", {"seat": seat, "error": f"{type(exc).__name__}: {exc}"[:600]},
                                 role="orchestrator", spec_hash=draft_hash)
                    continue
                objections += anchored_objections(
                    report.objections, prefix="S", start=len(objections) + 1,
                    criteria=criteria, questions=questions)
            if not objections:
                phase.detail = "no objections"
                store.append("spec_check", {"objections": [], "settled": []},
                             role="spec_checker", spec_hash=draft_hash)
                return draft

            revised, answers = draft, []
            try:
                revision = await self.llm.ask(
                    "spec_writer",
                    writer_prompt
                    + "\n---\n\n# The spec you wrote\n\n" + _json(draft)
                    + "\n\n---\n\n# The spec checker's objections\n\n"
                    + objection_block(objections)
                    + "\n\n---\n\nAnswer every objection once, by id: revise the spec so it no "
                      "longer holds, or rebut it and say why the checker is wrong. Do not add "
                      "scope to satisfy one -- an objection about something missing from the "
                      "intent belongs in `non_goals` or `open_questions`. Return the whole spec.",
                    SpecRevision, system=self.role_prompt("spec_writer"))
                revised, answers = seal(revision.spec), revision.answers
            except Exception as exc:
                store.append("spec_check", {"revision_error": f"{type(exc).__name__}: {exc}"[:600]},
                             role="orchestrator", spec_hash=draft_hash)
            outcome = settle_objections(
                objections, answers, lambda o: criteria_changed(draft, revised, o))
            for item in outcome:
                if item["settled"]:
                    continue
                o, a = item["objection"], item["answer"] or {}
                kept = (f"the spec writer kept it: {a['note']}" if a.get("note")
                        else f"the spec writer {item['why']}")
                line = f"{o['id']} (spec checker): {o['claim']} {o['consequence']} -- {kept}"
                if line not in revised.open_questions:
                    revised.open_questions.append(line)
            store.append("spec_check", {
                "objections": [o.model_dump(mode="json") for o in objections],
                "outcome": outcome,
            }, role="spec_checker", spec_hash=spec_hash(revised))
            open_count = sum(1 for i in outcome if not i["settled"])
            phase.detail = (f"{len(objections)} objection(s), {len(objections) - open_count} "
                            f"settled, {open_count} carried as open questions")
            return revised

    async def _interrogate(self, store, state, intent: str, scout) -> InterrogationReport:
        """One interrogation pass, over the intent, the repo, and any correction
        the human has made to how it was understood."""
        corrections = store.payloads("correction")
        block = ""
        if corrections:
            block = (
                "\n---\n\n# The human has corrected your understanding\n\n"
                "You restated the intent and they said you had it wrong. This is not a hint or a "
                "preference: it overrides your reading, and every question you ask now must follow "
                "from it. Do not re-litigate it, and do not ask a question that only made sense "
                "under the reading they rejected.\n\n"
                + "\n\n".join(
                    f"You said:\n> {c['restated_intent']}\n\nThey said:\n> {c['correction']}"
                    for c in corrections
                )
                + "\n"
            )

        # The one round of questions is the only place a missing affordance can
        # still be cheap. Asked here it is a choice between adding a fixture,
        # narrowing the behaviour, and accepting it unverified; found later it
        # is a finding about code that was built correctly.
        inventory = fixture_inventory(self.project.state.testing)
        prompt = (
            f"# Intent\n\n{intent}\n\n---\n\n"
            f"# What the scout found in this repository\n\n{_json(scout)}\n"
            + (f"\n---\n\n{inventory}" if inventory else "")
            + block
        )
        interrogation: InterrogationReport = await self.llm.ask(
            "interrogator", prompt, InterrogationReport,
            system=self.role_prompt("interrogator"),
        )
        interrogation.ambiguities.sort(
            key=lambda a: {"blocking": 0, "significant": 1, "minor": 2}.get(a.severity, 3)
        )
        store.append(
            "interrogation", interrogation,
            role="interrogator", model=self._answered_by("interrogator"),
            meta={"corrections": len(corrections)}, prompt=prompt,
        )
        return interrogation

    async def _scout(self, store, state, intent: str, root, phase) -> ScoutReport:
        """Read the whole repository, one slice per call, and union the results.

        A single truncated digest reports on the alphabetically-first fraction of
        a codebase and looks exactly like a report on all of it. Slicing trades
        one call for several and gets a complete picture, which matters most on
        the small local models where the truncation bites hardest.
        """
        cfg = self.config.pipeline
        tree = repo_tree(root)

        # Computed once, given to every slice. This is what makes slicing safe:
        # no scout sees the whole repository, but every scout knows what is
        # defined in it and what is defined twice.
        index = symbol_index(root)
        index_text = index.render(min(24_000, max(6_000, cfg.scout_slice_chars // 5)))

        kept, dropped = slice_repo(
            root, cfg.scout_slice_chars, cfg.scout_max_slices, focus=intent, index=index,
        )
        coverage = slice_coverage(kept, dropped, root)
        coverage["dropped_slices"] = [
            {"name": d.name, "files": len(d.paths), "chars": d.chars} for d in dropped
        ]
        coverage["symbols"] = len(index.by_name)
        coverage["duplicated_symbols"] = len(index.duplicates)
        store.append("digest", coverage, role="orchestrator")

        system = self.role_prompt("scout")
        semaphore = asyncio.Semaphore(max(1, cfg.scout_parallel))
        # The repository's guides, as the checkout has them: what the scout
        # reports against -- gaps where none speaks, and code that does
        # otherwise than one says. It infers nothing a guide already states.
        guidance = self._guidance(store, state, "scout", inferred=False, repo=root, ref="HEAD")

        async def one(index: int, sl) -> ScoutReport | None:
            async with semaphore:
                prompt = (
                    f"# Intent\n\n{intent}\n\n---\n\n"
                    f"# You are reading part {index + 1} of {len(kept)} of this repository\n\n"
                    "Other scouts are reading the other parts. Report only on what you can see "
                    "here. Two things are given to all of us: the complete file tree, and a "
                    "symbol index covering the entire repository including the parts you were "
                    "not shown. Use the index to answer 'does this already exist' -- it is "
                    "computed, so it is exact. Do not invent the contents of a file you were "
                    "not given.\n\n---\n\n"
                    + (guidance + "\n\n---\n\n" if guidance else
                       "# How this repository is written\n\nThis repository has no AGENTS.md, "
                       "DESIGN.md or skills, so every convention you report is the only "
                       "statement of it anyone will see.\n\n---\n\n")
                    + index_text
                    + "\n---\n\n"
                    + slice_digest(root, sl, tree)
                )
                try:
                    report: ScoutReport = await self.llm.ask(
                        "scout", prompt, ScoutReport, system=system,
                    )
                except Exception as exc:
                    store.append("scout_failed", {"slice": sl.name, "error": str(exc)},
                                 role="scout")
                    return None
                store.append("scout_slice", report, role="scout",
                             model=self._answered_by("scout"),
                             meta={"slice": sl.name, "files": len(sl.paths), "chars": sl.chars},
                             prompt=prompt)
                return report

        results = await asyncio.gather(*(one(i, sl) for i, sl in enumerate(kept)))
        good = [r for r in results if r is not None]
        if not good:
            raise RuntimeError(f"every scout slice failed across {len(kept)} slice(s)")

        merged = merge_scouts(good)
        store.append("scout", merged, role="scout",
                     model=self._answered_by("scout"),
                     meta={"slices": len(good), "of": len(kept), **coverage})
        # Where the code does otherwise than a guide says, kept on the
        # project as well: one in this feature's area is the interrogator's to
        # ask about, and the rest are a note on the project's health, for a
        # person to settle in the guide or in the code.
        if merged.contradictions:
            self.project.store.append("guide_contradictions", {
                "feature": state.feature_id,
                "contradictions": [c.model_dump(mode="json") for c in merged.contradictions],
            }, role="scout")
        phase.detail = (
            f"{len(good)}/{len(kept)} slices · {coverage['covered'] * 100:.0f}% of "
            f"{coverage['files']} files · {coverage['symbols']} symbols indexed"
            + (f" · {len(dropped)} area(s) unread" if dropped else "")
        )
        return merged

    def intake_plan(self, feature_id: str) -> dict[str, Any]:
        """What re-reading this repository would cost, without spending it.

        File sizes only -- no model call, no file contents. So the button can
        say "nine scout calls" before you press it rather than after.
        """
        store = self.store_for(feature_id)
        state = self.state_of(store)
        sandbox = self.open_sandbox(state)
        cfg = self.config.pipeline
        index = symbol_index(sandbox.path)
        kept, dropped = slice_repo(
            sandbox.path, cfg.scout_slice_chars, cfg.scout_max_slices,
            focus=state.intent, index=index,
        )
        coverage = slice_coverage(kept, dropped, sandbox.path)
        return {
            **coverage,
            "symbols": len(index.by_name),
            "duplicated_symbols": len(index.duplicates),
            "calls": len(kept) + 1,  # the scouts, plus one interrogator
            "unread": [{"name": d.name, "files": len(d.paths)} for d in dropped[:12]],
        }

    async def rerun_intake(self, feature_id: str) -> FeatureState:
        """Read the repository again from scratch, then ask again.

        Distinct from `reinterrogate`, which reuses the stored scout report: this
        is for when what the scout saw was wrong or incomplete, not when the
        interrogator misread a correct report. Everything prior stays in the
        ledger -- `latest` wins, nothing is edited.
        """
        store = self.store_for(feature_id)
        state = self.state_of(store)
        if state.stage in ("building", "awaiting_verdict", "accepted", "rejected"):
            raise ProjectError(
                f"feature {feature_id!r} is {state.stage}: re-reading the repository now would "
                "produce a spec that does not describe what was built. Gate 1 has closed."
            )
        # A reading that is still happening is not one to start again. Asked of
        # the operating system rather than of `stage`, because `intake` is both
        # what a live reading says and what a dead one leaves behind -- and
        # this is the one recovery a dead reading has, so it must stay
        # available the moment the process owning it is gone. (Two readings at
        # once write the same scout slices from two processes.)
        if state.stage == "intake" and owner_is_alive(state.owner):
            raise ProjectError(
                f"feature {feature_id!r} is being read right now by process "
                f"{state.owner.partition(':')[0]}. Wait for the questions, or discard it."
            )

        store.append("reintake", {
            "superseded_scout": bool(store.has("scout")),
            "superseded_spec": bool(store.has("spec")),
        }, role="human")

        reset_phases(state, INTAKE_PHASES)
        state.stage = "intake"
        state.owner = this_process()
        state.error = ""
        self._save(store, state)
        self._emit(state)

        try:
            return await self._intake(store, state)
        except Exception as exc:
            state.stage = "failed"
            state.owner = ""   # the run is done with it
            state.error = f"{type(exc).__name__}: {exc}"[:4000]
            self._save(store, state)
            self._emit(state)
            raise

    async def reinterrogate(self, feature_id: str, correction: str) -> InterrogationReport:
        """The human says the restatement is wrong. Ask again, knowing that.

        Within gate 1, not across it: nothing has been built, no spec is frozen,
        and the old interrogation stays in the ledger beside the new one.
        """
        store = self.store_for(feature_id)
        state = self.state_of(store)
        previous = store.payload("interrogation") or {}
        store.append("correction", {
            "correction": correction,
            "restated_intent": previous.get("restated_intent", ""),
            "superseded_questions": len(previous.get("ambiguities") or []),
        }, role="human")

        scout = ScoutReport.model_validate(store.payload("scout"))
        state.stage = "intake"
        state.owner = this_process()
        self._save(store, state)
        self._emit(state)
        try:
            async with self._phase(store, state, "interrogator"):
                report = await self._interrogate(store, state, state.intent, scout)
        except Exception as exc:
            state.stage = "failed"
            state.owner = ""   # the run is done with it
            state.error = f"{type(exc).__name__}: {exc}"[:4000]
            self._save(store, state)
            self._emit(state)
            raise
        state.stage = "awaiting_answers"
        state.owner = ""   # the run is done with it
        self._save(store, state)
        self._emit(state)
        return report

    def approve_spec(self, feature_id: str) -> FeatureState:
        store = self.store_for(feature_id)
        state = self.state_of(store)
        store.append("approval", {"approved_at": _now(), "spec_hash": state.spec_hash}, role="human")
        state.stage = "building"
        state.owner = this_process()
        self._save(store, state)
        self._emit(state)
        return state
