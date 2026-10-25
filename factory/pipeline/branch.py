"""A look at a branch Fabrika did not build.

Exploratory work -- a new screen, an algorithm nobody could have specified up
front -- is done interactively, in whichever agent the person likes, and ends as
a branch. A look takes that branch, and at most one sentence of intent, and
hands back a packet: what was built, structurally; whether it meets the
repository's own checks; and the diff cut into pieces a person reads by
materiality (`factory.pieces`).

It is a shorter line than a feature's, and it stays off the branch:

    reader (base ∥ head) ∥ gates → attribution → rapporteur

Nothing commits. The branch is checked out detached, at a commit pinned when
the look began, in a worktree of Fabrika's own; a check that rewrites files has
what it would rewrite recorded and put back; and the packet is a record in the
branch's ledger, not a file on the branch. The person's checkout, the branch
ref and the branch's commits are exactly as they were. There is no repair loop
either: what is wrong goes back to whoever built the branch.

Every look at a branch goes into one ledger, appended, so the record of the
earlier ones is never replaced by the later.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Sequence

from .. import branch_facts, git, pieces as cutting
from ..containers import DockerError, runner_for
from ..gates import restore_tracked, run_gate, run_gates, run_setup, test_session, tracked_changes
from ..isolation import IsolationError
from ..projects import ProjectError
from ..sandbox import Sandbox, SandboxError, resolve_ref
from ..schemas import BranchState, GateReport, GateResult, PhaseState, Piece, PieceJudgement
from ..store import EvidenceStore
from .factory import Factory
from .text import _now

#: The phases a look starts with. Attribution joins them when a check is red.
LOOK_PHASES = ("reader", "gates", "rapporteur")
#: How long a look's reader station waits for its readings. A look's packet is
#: built on them -- where it landed, what it adds -- so it waits far longer than
#: a feature does for the same account; past this it goes on without and says so.
LOOK_READ_WAIT_S = 3600
#: Diff rows shown to the rapporteur per piece. It classes from these; the
#: lines are all in the cut whatever is shown.
ROWS_PER_PIECE = 60


def reading_status(store: EvidenceStore, state: BranchState) -> dict[str, dict[str, Any]]:
    """Each end of the look's as-built: read whole, read with files left over,
    stopped (and why), being read now, or not read at all -- from the latest
    record of the reading at that commit."""
    from ..asbuilt import load_file
    from .text import _AS_BUILT_READING

    out: dict[str, dict[str, Any]] = {}
    records = store.of_kinds("as_built", "as_built_failed")
    for label, sha in (("base", state.base_sha), ("head", state.head_sha)):
        mine = [r for r in records if (r.get("payload") or {}).get("label") == label
                and r["payload"].get("commit") == sha]
        running = _AS_BUILT_READING.get((str(store.dir), label, sha))
        entry: dict[str, Any] = {"state": "none", "error": "", "unread": 0, "files": 0}
        if mine:
            last = mine[-1]
            if last["kind"] == "as_built_failed":
                entry.update(state="stopped", error=str(last["payload"].get("error", ""))
                             .removeprefix("AsBuiltError: "))
            else:
                done = load_file(store.dir / last["payload"].get("file", ""))
                unread = len(done.coverage.unread or []) if done is not None else 0
                entry.update(state="partial" if unread else "read", unread=unread,
                             files=last["payload"].get("files", 0))
        if running is not None and not running.done():
            entry.update(state="reading", error="")
        out[label] = entry
    return out


def record_system_change(store: EvidenceStore, state: BranchState) -> bool:
    """The as-built at the base against the head, kept once both are read:
    what Where it landed says in the repository's own groups. A feature
    records it as its build ends; a look had nowhere that did, and its groups
    never showed however whole its readings were. True when it is recorded."""
    from ..asbuilt import feature_reading
    from ..asbuilt_graph import system_change

    base, head = feature_reading(store, "base"), feature_reading(store, "head")
    if base is None or head is None or (base.commit, head.commit) != (state.base_sha, state.head_sha):
        return False
    if any(base.coverage.unread or []) or any(head.coverage.unread or []):
        return False
    kept = store.payload("system_change") or {}
    if (kept.get("base"), kept.get("head")) == (base.commit, head.commit):
        return True
    store.append("system_change", system_change(base, head), role="orchestrator")
    return True


def _said(reading: dict[str, Any]) -> str:
    if reading["state"] == "stopped":
        return f"stopped: {reading['error'].split('. ')[0].removeprefix('Stopped: ')}"
    if reading["state"] == "partial":
        return f"read with {reading['unread']} file(s) left"
    return {"read": "read", "reading": "being read", "none": "not read"}[reading["state"]]


def branch_slug(branch: str) -> str:
    """The branch's name, as a directory: `alex/new-ui` -> `alex-new-ui`."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-.")[:80] or "branch"


def commits_between(repo, base: str, head: str) -> list[dict[str, str]]:
    """The branch's commits, oldest first, with their messages whole: with no
    spec, what the commits say is the only stated intent a branch has."""
    text = git.out(["log", "--reverse", "--format=%H%x1f%an%x1f%aI%x1f%B%x1e", f"{base}..{head}"],
                   repo, timeout=60) or ""
    out = []
    for record in text.split("\x1e"):
        bits = record.strip("\n").split("\x1f")
        if len(bits) == 4:
            out.append({"sha": bits[0], "author": bits[1], "at": bits[2], "message": bits[3].strip()})
    return out


def check_status(project) -> str:
    """Whether the checks a look runs are ones a person approved."""
    if not project.state.gates:
        return "none"
    return "approved" if project.state.stage == "ready" else "proposed"


class BranchReview(Factory):
    """The orchestrator, pointed at a branch it did not build.

    A `Factory`, so every step a look shares with a feature's run is the same
    code: phases and their journal, the as-built, the checks' environment and
    attribution at the base commit. What differs is what is handed in -- a
    branch, not a spec -- and that nothing is written back.
    """

    # -- the ledger ---------------------------------------------------------

    def branch_store(self, slug: str) -> EvidenceStore:
        store = self.project.branch_store(slug)
        store.usage_of = getattr(self.llm, "take_usage", None)
        return store

    def look_state(self, store: EvidenceStore) -> BranchState:
        payload = store.payload("state")
        if payload is None:
            raise KeyError(f"no look recorded for branch {store.feature_id!r}")
        return BranchState.model_validate(payload)

    def branches(self) -> list[dict[str, Any]]:
        """Every branch looked at in this project, its latest look first."""
        out = []
        for slug in self.project.branch_slugs():
            store = self.project.branch_store(slug)
            try:
                state = self.look_state(store)
            except (KeyError, ValueError):
                continue
            packet = store.payload("branch_packet") or {}
            running = next((ph.name for ph in state.phases if ph.status == "running"), "")
            out.append({
                "project_id": self.project.id, "phase": running,
                "slug": slug, "branch": state.branch, "look": state.look, "stage": state.stage,
                "base_sha": state.base_sha, "head_sha": state.head_sha, "intent": state.intent,
                "headline": packet.get("headline", "") if packet.get("look") == state.look else "",
                "updated_at": state.updated_at, "error": state.error,
            })
        out.sort(key=lambda b: b["updated_at"], reverse=True)
        return out

    def candidate_branches(self) -> list[dict[str, Any]]:
        """Local branches with work past the project's base, for the form that
        starts a look. Fabrika's own `factory/` branches are its features'."""
        repo = self.project.repo_path
        listed = git.out(["for-each-ref", "--format=%(refname:short)%1f%(objectname)%1f%(committerdate:iso-strict)"
                          "%1f%(contents:subject)", "refs/heads"], repo, timeout=30) or ""
        base = self.project.base_ref
        out = []
        for line in listed.splitlines():
            bits = line.split("\x1f")
            if len(bits) != 4 or bits[0].startswith("factory/"):
                continue
            ahead = git.out(["rev-list", "--count", f"{base}..{bits[1]}"], repo, timeout=30)
            try:
                count = int((ahead or "0").strip())
            except ValueError:
                count = 0
            if count:
                out.append({"branch": bits[0], "head_sha": bits[1], "at": bits[2], "ahead": count,
                            "subject": bits[3]})
        out.sort(key=lambda b: b["at"], reverse=True)
        return out

    # -- the way in ---------------------------------------------------------

    @staticmethod
    def branch_slug_for(branch: str) -> str:
        return branch_slug(branch.strip())

    def open_look(self, branch: str, intent: str = "") -> BranchState:
        """Pin the branch and record the look. Refuses, with a sentence, a branch
        that is not there or has nothing on it. Nothing is checked out yet."""
        branch = branch.strip()
        repo = self.project.repo_path
        if not branch:
            raise ProjectError("name a branch to look at")
        try:
            head = resolve_ref(repo, branch)
        except SandboxError:
            raise ProjectError(f"there is no branch or commit {branch!r} in {repo}") from None
        base_ref = self.project.base_ref
        base = git.merge_base(repo, base_ref, head)
        if not base:
            raise ProjectError(f"{branch} shares no history with {base_ref}, so there is nothing to compare it to")
        if base == head:
            raise ProjectError(f"{branch} has no commits that {base_ref} does not already have")
        slug = branch_slug(branch)
        store = self.branch_store(slug)
        prior = store.payload("state")
        look = BranchState.model_validate(prior).look + 1 if prior else 1
        state = BranchState(
            slug=slug, project_id=self.project.id, branch=branch, intent=intent.strip(), look=look,
            base_ref=base_ref, base_sha=base, head_sha=head,
            phases=[PhaseState(name=n) for n in LOOK_PHASES])
        store.append("look", {
            "look": look, "branch": branch, "intent": state.intent, "base_ref": base_ref,
            "base_sha": base, "head_sha": head, "commits": commits_between(repo, base, head),
            "at": _now(),
        }, role="human")
        self._save(store, state)
        self._emit(state)
        return state

    # -- the line -----------------------------------------------------------

    async def run_look(self, slug: str) -> dict[str, Any]:
        store = self.branch_store(slug)
        state = self.look_state(store)
        sandbox: Sandbox | None = None
        try:
            sandbox = Sandbox.create_detached(
                repo=self.project.repo_path, root=self.config.sandbox_path, project_id=self.project.id,
                name=f"{slug}-{state.look}", base_sha=state.base_sha, head_sha=state.head_sha)
            state.sandbox = sandbox.state
            self._save(store, state)
            # The cut is git and arithmetic, and the checks need it to tell the
            # branch's lines from the ones it inherited.
            cut = cutting.cut(self.project.repo_path, state.base_sha, state.head_sha)
            results = await asyncio.gather(self._read(store, state),
                                           self._look_checks(store, state, sandbox, cut),
                                           return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            gates = results[1]
            await self._as_built_change(store, state, sandbox, None)
            packet = await self._present(store, state, cut, gates)
            state.stage = "awaiting_ruling"
            return packet
        except Exception as exc:
            state.stage = "failed"
            state.error = f"{type(exc).__name__}: {exc}"[:2000]
            raise
        finally:
            if sandbox is not None:
                sandbox.release()
                state.sandbox = sandbox.state
            self._save(store, state)
            self._emit(state)

    async def _read(self, store: EvidenceStore, state: BranchState) -> None:
        """The as-built at both ends of the branch, so what it added to the
        system can be said in the system's terms."""
        async with self._phase(store, state, "reader") as phase:
            tasks = [t for t in (self._as_built_begin(store, state.base_sha, "base"),
                                 self._as_built_begin(store, state.head_sha, "head")) if t is not None]
            if not tasks:
                phase.detail = ("already read" if store.all("as_built")
                                else "the as-built is not read on this factory")
                return
            # Lit for as long as the reading takes. Marked done at a timeout,
            # the station read green while a dozen reader calls were still
            # running and the line looked stalled at the next one.
            phase.detail = f"reading {len(tasks)} commit(s)"
            _done, pending = await asyncio.wait(tasks, timeout=LOOK_READ_WAIT_S)
            if pending:
                phase.detail = (f"{len(pending)} reading(s) still running after {LOOK_READ_WAIT_S // 60} min; "
                                "the packet is made without them")
                return
            # A reading that stopped part way is said so here, not as "done":
            # the station read green over a head with no groups to show.
            status = reading_status(store, state)
            phase.detail = " · ".join(f"{label} {_said(r)}" for label, r in status.items())
            record_system_change(store, state)

    async def _look_checks(self, store: EvidenceStore, state: BranchState, sandbox: Sandbox,
                           cut: Sequence[Piece]) -> GateReport:
        """The project's checks at the branch's head, in the project's own
        environment, every one of them: a look is a final pass."""
        status = check_status(self.project)
        checks = self.project.judging_gates if status == "approved" else list(self.project.state.gates)
        rewrote: dict[str, list[str]] = {}
        async with self._phase(store, state, "gates") as phase:
            report = await self._checks_at_head(store, state, sandbox, checks, rewrote, phase)
            lines = cutting.changed_by_file(cut)
            located = {}
            for result in report.results:
                if result.located or result.report:
                    added, before = cutting.split_located(result.located, lines)
                    located[result.name] = {
                        "report": result.report,
                        "added": [loc.model_dump(mode="json") for loc in added[:60]],
                        "added_count": len(added), "before_count": len(before),
                    }
            store.append("gates", report, role="orchestrator")
            store.append("located", {"checks": located, "would_rewrite": rewrote, "status": status},
                         role="orchestrator")
        # After the runner is down, because it opens its own: a red check is
        # run again where the branch left the base, and the answer is shared
        # with every feature cut from the same commit.
        await self._attribute_failures(store, state, sandbox, report, 0)
        store.append("gates", report, role="orchestrator", meta={"attributed": True})
        return report

    async def _checks_at_head(self, store: EvidenceStore, state: BranchState, sandbox: Sandbox,
                              checks, rewrote: dict[str, list[str]], phase) -> GateReport:
        if not checks:
            phase.detail = "this project has no checks yet"
            return GateReport(results=[])

        def none_ran(why: str) -> GateReport:
            return GateReport(results=[GateResult(
                name=g.name, command=g.command, exit_code=1, passed=False, started=False,
                output_tail=why) for g in checks])

        try:
            runner, image = await runner_for(self.project, self.config, feature_id=state.feature_id)
        except (DockerError, IsolationError) as exc:
            # Said in the packet, not lost with it: what was built and how it
            # is cut can still be read with no check run.
            phase.detail = "the checks' environment could not start"
            return none_ran(f"The checks' environment could not start: {exc}")
        phase.detail = f"in {image}" if image else "on this machine (environment: host)"
        try:
            env = self.project.environment
            if env and env.setup:
                setup = await run_setup(env.setup, sandbox.path, runner, warm=self.project.warm_gates)
                store.append("setup", {
                    "commands": list(env.setup),
                    "results": [r.model_dump(mode="json") for r in setup],
                    "failed": [r.name for r in setup if not r.passed and not r.skipped],
                }, role="orchestrator")
            async with test_session(
                sandbox.path, runner,
                services=list(env.services) if env else (),
                prepare=list(env.test_prepare) if env else (),
                disposable=self.project.state.testing.disposable_db,
            ) as prepared:
                report = (await self._run_checks(sandbox, runner, checks, rewrote) if prepared.ready
                          else none_ran(prepared.report))
        finally:
            down = getattr(runner, "down", None)
            if down is not None:
                await down()
        passed = sum(1 for g in report.results if g.passed)
        phase.detail = f"{passed}/{len(report.results)} passed" + (f" in {image}" if image else "")
        return report

    async def _run_checks(self, sandbox: Sandbox, runner, checks, rewrote: dict[str, list[str]]) -> GateReport:
        """Fixers first, one at a time, and what each would rewrite is written
        down and put back -- never committed. The branch is someone else's; the
        packet can say it is not formatted, and the branch stays as it was."""
        kinds = self.project.check_kinds(self.config.pipeline.heavy_check_after_s)
        fixers = [g for g in checks if kinds.get(g.name) == "fixer"]
        rest = [g for g in checks if kinds.get(g.name) != "fixer"]
        ran: list[GateResult] = []
        for gate in fixers:
            ran.append(await run_gate(gate, sandbox.path, runner))
            changed = tracked_changes(sandbox.path)
            if changed:
                rewrote[gate.name] = changed
                restore_tracked(sandbox.path)
        report = await run_gates(rest, sandbox.path, runner)
        changed = tracked_changes(sandbox.path)
        if changed:
            rewrote["the project's checks"] = changed
            restore_tracked(sandbox.path)
        report.results = ran + report.results
        return report

    async def _present(self, store: EvidenceStore, state: BranchState, cut: list[Piece],
                       gates: GateReport) -> dict[str, Any]:
        """The rapporteur classes the pieces; code holds it to every line once."""
        async with self._phase(store, state, "rapporteur") as phase:
            look = store.payload("look") or {}
            prompt = self._branch_block(state, look.get("commits") or [], cut, gates, store)
            judgement: PieceJudgement = await self.llm.ask(
                "rapporteur", prompt, PieceJudgement,
                system=self.role_prompt("rapporteur") + "\n\n" + self.role_prompt("branch_packet"))
            store.append("pieces_raw", judgement, role="rapporteur",
                         model=self._answered_by("rapporteur"), prompt=prompt)
            classed = cutting.classify([p.model_copy(deep=True) for p in cut], judgement)
            final, refused = cutting.join(classed, judgement)
            counts = {k: sum(len(p.added) + len(p.removed) for p in final if p.klass == k)
                      for k in ("novel", "conventional", "mechanical", "generated", "tests")}
            phase.detail = f"{len(final)} pieces · {counts['novel']} novel lines"
            packet = {
                "look": state.look, "branch": state.branch, "intent": state.intent,
                "base_ref": state.base_ref, "base_sha": state.base_sha, "head_sha": state.head_sha,
                "headline": judgement.headline.strip(), "summary": judgement.summary.strip(),
                "facts": branch_facts.facts(self.project.repo_path, state.base_sha, state.head_sha, final,
                                            self._system_view(store)),
                "paths": cutting.paths_of(judgement, final),
                "claims": cutting.claims_of(judgement, self.project.repo_path, state.head_sha,
                                            self._tests_green(gates)),
                "commits": look.get("commits") or [],
                "stat": {"files": len({p.path for p in final}),
                         "added": sum(len(p.added) for p in final),
                         "removed": sum(len(p.removed) for p in final)},
                "lines": counts,
                "pieces": [p.model_dump(mode="json") for p in final],
                "joins_refused": refused,
                "checks": self._checks_view(gates, store),
            }
            store.append("branch_packet", packet, role="orchestrator")
            return packet

    def _system_view(self, store: EvidenceStore) -> dict[str, Any] | None:
        """What the as-built credits the branch with, when both readings exist."""
        try:
            from ..asbuilt import feature_reading
            from .. import system_report
            base, head = feature_reading(store, "base"), feature_reading(store, "head")
            if base is None or head is None:
                return None
            return system_report.report(base, head, system_report.git_reader(self.project.repo_path),
                                        store.payload("dependencies")).model_dump(mode="json")
        except Exception:  # an account for a reader: never worth the packet
            return None

    def _tests_green(self, gates: GateReport) -> bool | None:
        """Whether the project's test checks passed at the head; None when it has none."""
        names = {g.name for g in self.project.state.gates if g.family == "tests"}
        ran = [r for r in gates.results if r.name in names]
        return all(r.passed for r in ran) if ran else None

    def _checks_view(self, gates: GateReport, store: EvidenceStore) -> dict[str, Any]:
        located = store.payload("located") or {}
        found = located.get("checks") or {}
        rewrote = located.get("would_rewrite") or {}
        rows = []
        for r in gates.results:
            rows.append({
                "name": r.name, "command": r.command, "passed": r.passed, "started": r.started,
                "skipped": r.skipped, "exit_code": r.exit_code, "duration_s": r.duration_s,
                "output_tail": (r.output_tail or "")[-1500:], "at_base": r.at_base,
                "caused_here": r.caused_here, "pre_existing": r.pre_existing,
                "located": found.get(r.name), "would_rewrite": rewrote.get(r.name, []),
            })
        return {"status": located.get("status", check_status(self.project)), "results": rows,
                "would_rewrite": rewrote}

    def _branch_block(self, state: BranchState, commits: list[dict], cut: Sequence[Piece],
                      gates: GateReport, store: EvidenceStore) -> str:
        out = [f"# The branch\n\n{state.branch}, {len(commits)} commit(s), from "
               f"{state.base_ref} at {state.base_sha[:12]} to {state.head_sha[:12]}.",
               "\n# Intent\n\n" + (state.intent or "None given. What the commits say is all there is.")]
        out.append("\n# What its commits say\n")
        for c in commits:
            out.append(f"## {c['sha'][:12]}\n\n{c['message']}\n")
        change = store.payload("system_change")
        if change:
            try:
                from ..asbuilt_graph import SystemChange
                said = SystemChange.model_validate(change).lines()
                if said:
                    out.append("\n# What it changed in the system\n\n" + "\n".join(f"- {s}" for s in said))
            except ValueError:
                pass
        if gates.results:
            out.append("\n# The repository's checks, at the head\n")
            for r in gates.results:
                word = "passed" if r.passed else ("could not run" if not r.started else "failed")
                out.append(f"- {r.name}: {word} (`{r.command}`)")
        ask = [p for p in cut if p.classed_by != "code"]
        fixed = [p for p in cut if p.classed_by == "code"]
        out.append(f"\n# The pieces to class ({len(ask)})\n")
        for p in ask:
            where = f"{p.path}:{p.start}" if p.start else f"{p.path} (gone at the head)"
            out.append(f"## {p.id} · {p.kind} `{p.symbol}` · {where} · +{len(p.added)} −{len(p.removed)}\n")
            rows = [r for r in p.rows if r[0] != "~"][:ROWS_PER_PIECE]
            body = "\n".join(f"{r[0]}{r[3]}" for r in rows)
            more = len([r for r in p.rows if r[0] != "~"]) - len(rows)
            out.append("```\n" + body + ("\n… " + str(more) + " more rows" if more > 0 else "") + "\n```\n")
        tests = [p for p in cut if p.klass == "tests" and p.kind == "test"]
        if tests:
            out.append("\n# Tests the branch added or changed (for `claims`)\n")
            out.extend(f"- {p.symbol} in {p.path}" for p in tests)
        if fixed:
            out.append("\n# Classed by code, not by you\n")
            out.extend(f"- {p.id} `{p.symbol}` in {p.path}: {p.klass} ({p.reason})" for p in fixed)
        return "\n".join(out)
