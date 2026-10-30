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
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Sequence

from .. import branch_facts, git, pieces as cutting
from ..containers import DockerError, runner_for
from ..gates import restore_tracked, run_gate, run_gates, run_setup, test_session, tracked_changes
from ..isolation import IsolationError
from ..projects import ProjectError
from ..sandbox import BRANCH_PREFIX, LEGACY_BRANCH_PREFIX, Sandbox, SandboxError, is_own_branch, resolve_ref
from ..schemas import (AcceptanceCriterion, BranchState, BreakerReport, BreakerSuite, GateReport, GateResult,
                       PhaseState, Piece, PieceJudgement, QAReport, Spec, TestFileCommand)
from ..store import EvidenceStore
from .factory import Factory
from .factory.rounds import RoundsMixin
from .factory.context import breaker_section
from .ledger import FindingLedger
from .oracle_suite import _title_key
from .probes import breaker_detail, breaker_findings, probe_finding_id
from .text import _now, tree_section

#: The phases a look starts with. Attribution joins them when a check is red.
#: The last three are the attack: probes, a review panel, and the arbiter that
#: routes what they found -- to whoever built the branch, to the tidy branch,
#: or to the person reading the packet.
LOOK_PHASES = ("reader", "gates", "rapporteur", "breaker", "review", "arbiter")
#: Where a look's tidy branch goes: Fabrika's own name, never the branch under
#: review. One per branch looked at, moved by a later press of the same look.
TIDY_PREFIX = f"{BRANCH_PREFIX}tidy/"
#: Where an earlier version fixed a branch sent back to it: a branch of its own
#: off the look's head, never the one under review, then looked at like any other.
FIX_PREFIX = f"{LEGACY_BRANCH_PREFIX}fix/"
#: Where outside work's fixes go: one branch per item, Fabrika's own, off the
#: person's branch and moved round by round. (`FIX_PREFIX` is where an earlier
#: version put them, one branch per fix; adopted on sight.)
WORK_PREFIX = f"{BRANCH_PREFIX}outside/"
#: The name Fabrika commits under, on a branch of its own. A commit by it is
#: never read as one of the person's claims.
FABRIKA_AUTHOR = "software factory"
#: The stations a fix lights on the look's own line, after the arbiter: the
#: repair loop's steps under names of their own, so a recheck does not relight
#: the look's review station, nor a repair read as the look's.
FIX_PHASES = {"repairers": "fix_repair", "review": "fix_recheck"}
#: The roles that are told, each time they are asked in a look, that there is
#: no spec, no blind suite and no repair loop (`roles/branch_attack.md`).
ATTACK_ROLES = ("reviewer", "breaker", "arbiter", "simplifier", "repairer")
#: Where a look's findings go, by the arbiter's disposition. A look repairs
#: nothing: a defect goes back to whoever built the branch, a cleanup to the
#: tidy branch, and what only a person can decide, to the person.
ROUTES = {"repair": "send_back", "simplify": "cleanup", "escalate": "you", "oracle": "you",
          "needs_spec_change": "you", "out_of_spec": "you", "not_a_defect": "none", "dismissed": "none"}
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


#: What a person can say about a finding a look routed to them or back: send
#: it back to whoever built the branch, or accept it as a risk they sign for.
RULINGS = ("send_back", "accept")


def look_calls(store: EvidenceStore, look: int) -> dict[str, Any]:
    """The findings of look `look` that need a person: every defect routed back
    and every call routed to them, each with what was ruled on it -- the latest
    ruling, or none -- and what that makes it. A defect nobody ruled on still
    goes back: the arbiter's route stands until someone overrules it. A call
    for the person goes nowhere until they make it."""
    found = next((r["payload"] for r in reversed(store.all("look_findings"))
                  if r["payload"].get("look") == look), None) or {}
    records = {r["finding_id"]: r for r in (found.get("ledger") or {}).get("records") or []}
    findings = {f["id"]: f for f in (found.get("ledger") or {}).get("findings") or []}
    ruled: dict[str, dict[str, Any]] = {}
    for r in store.all("look_ruling"):
        if r["payload"].get("look") == look:
            ruled[r["payload"]["finding_id"]] = r["payload"]
    calls = []
    for fid, route in (found.get("routes") or {}).items():
        if route not in ("send_back", "you") or fid not in findings or records.get(fid, {}).get("duplicate_of"):
            continue
        ruling = ruled.get(fid)
        goes = ruling["ruling"] if ruling else ("send_back" if route == "send_back" else "")
        calls.append({"finding_id": fid, "title": findings[fid]["title"], "severity": findings[fid]["severity"],
                      "route": route, "ruling": ruling, "goes": goes})
    return {"calls": calls, "found": bool(found),
            "unruled": [c["finding_id"] for c in calls if not c["goes"]],
            "send_back": [c["finding_id"] for c in calls if c["goes"] == "send_back"],
            "accepted": [c["finding_id"] for c in calls if c["goes"] == "accept"]}


def _round_record(store: EvidenceStore, kind: str, look: int) -> dict[str, Any] | None:
    return next((r["payload"] for r in reversed(store.all(kind)) if r["payload"].get("look") == look), None)


def compare_rounds(store: EvidenceStore, look: int) -> dict[str, Any]:
    """Round `look` against the round before it: what Fabrika fixed in
    between and what it could not, and each finding of this round said as new,
    still there, or accepted before -- by you, and carried forward because the
    code it is about did not change. Matched by what a finding says, as the
    ledger matches a finding seen twice; a finding said in new words reads as
    new, and you rule on it again."""
    found = _round_record(store, "look_findings", look) or {}
    before = _round_record(store, "look_findings", look - 1) if look > 1 else None
    if not before:
        return {"previous": None}
    names = {f["id"]: f["title"] for f in (before.get("ledger") or {}).get("findings") or []}
    fix = _round_record(store, "look_fix", look - 1) or {}
    earlier = {_title_key(c["title"]): c for c in look_calls(store, look - 1)["calls"]}
    carried = {r["payload"]["finding_id"] for r in store.all("look_ruling")
               if r["payload"].get("look") == look and r["payload"].get("carried_from")}
    records = {r["finding_id"]: r for r in (found.get("ledger") or {}).get("records") or []}
    status = {}
    for f in (found.get("ledger") or {}).get("findings") or []:
        if records.get(f["id"], {}).get("duplicate_of"):
            continue
        key = _title_key(f["title"])
        status[f["id"]] = ("accepted_before" if f["id"] in carried
                           else "changed_since_accepted" if key in earlier and earlier[key]["goes"] == "accept"
                           else "still_there" if key in earlier
                           else "new")
    return {"previous": look - 1,
            "fixed": [names.get(fid, fid) for fid in fix.get("fixed") or []],
            "not_fixed": [names.get(fid, fid) for fid in fix.get("not_fixed") or []],
            "status": status}


def rounds_of(store: EvidenceStore) -> list[dict[str, Any]]:
    """Every round of the work, oldest first: what it looked at, what it
    found going back, how it was ruled, and what Fabrika fixed after it."""
    out = []
    for r in store.all("look"):
        look = r["payload"]
        n = look.get("look", 0)
        calls = look_calls(store, n)
        verdict = _round_record(store, "look_verdict", n) or {}
        fix = _round_record(store, "look_fix", n)
        out.append({
            "look": n, "head_sha": look.get("head_sha", ""), "at": look.get("at", ""),
            "working": bool(look.get("working_branch")), "found": calls["found"],
            "send_back": len(calls["send_back"]), "accepted": len(calls["accepted"]),
            "unruled": len(calls["unruled"]), "verdict": verdict.get("verdict", ""),
            "fix": {"fixed": len(fix.get("fixed") or []), "of": len(fix.get("targets") or []),
                    "branch": fix.get("branch", "")} if fix else None,
        })
    return out


def look_spec(state: BranchState, packet: dict[str, Any]) -> Spec:
    """What a look holds the branch to, in the shape every attacking role
    already reads: the commits' claims as its criteria, CL-1 onwards, in the
    packet's order. It is a stand-in, said so in `roles/branch_attack.md`:
    nobody froze it, and the branch was not written against it."""
    claims = packet.get("claims") or []
    return Spec(
        title=f"Outside work: {state.branch}",
        intent=state.intent or "None given: what the commits say is all there is.",
        summary=packet.get("summary") or packet.get("headline") or "",
        acceptance_criteria=[
            AcceptanceCriterion(id=f"CL-{n}", title=c["quote"], statement=c["quote"],
                                rationale="said by the branch's commits")
            for n, c in enumerate(claims, 1)])


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
            out.append({"sha": bits[0], "author": bits[1], "at": bits[2], "message": bits[3].strip(),
                        "fabrika": bits[1] == FABRIKA_AUTHOR})
    return out


def _is_ancestor(repo, a: str, b: str) -> bool:
    done = git.run(["merge-base", "--is-ancestor", a, b], repo, timeout=30)
    return done is not None and done.returncode == 0


def check_status(project) -> str:
    """Whether the checks a look runs are ones a person approved."""
    if not project.state.gates:
        return "none"
    return "approved" if project.state.stage == "ready" else "proposed"


class BranchReview(RoundsMixin, Factory):
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
            if is_own_branch(state.branch):
                # A look at one of Fabrika's own branches: what an earlier
                # version made of a fix. The work it belongs to carries it.
                continue
            packet = store.payload("branch_packet") or {}
            running = next((ph.name for ph in state.phases if ph.status == "running"), "")
            out.append({
                "project_id": self.project.id, "phase": running,
                "slug": slug, "branch": state.branch, "look": state.look, "stage": state.stage,
                "base_sha": state.base_sha, "head_sha": state.head_sha, "intent": state.intent,
                "headline": packet.get("headline", "") if packet.get("look") == state.look else "",
                "updated_at": state.updated_at, "error": state.error,
                "verdict": next((r["payload"]["verdict"] for r in reversed(store.all("look_verdict"))
                                 if r["payload"].get("look") == state.look), ""),
            })
        out.sort(key=lambda b: b["updated_at"], reverse=True)
        return out

    def candidate_branches(self) -> list[dict[str, Any]]:
        """Local branches with work past the project's base, for the form that
        starts a look. Fabrika's own `fabrika/` branches are its features'."""
        repo = self.project.repo_path
        listed = git.out(["for-each-ref", "--format=%(refname:short)%1f%(objectname)%1f%(committerdate:iso-strict)"
                          "%1f%(contents:subject)", "refs/heads"], repo, timeout=30) or ""
        base = self.project.base_ref
        out = []
        for line in listed.splitlines():
            bits = line.split("\x1f")
            if len(bits) != 4 or is_own_branch(bits[0]):
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
        """The next round of the work on `branch`. Refuses, with a sentence, a
        branch that is not there or has nothing on it. Nothing is checked out
        yet.

        A round looks at the work's head: the person's branch until Fabrika
        has fixed anything, its own working branch after. Commits the person
        added to theirs since the last round are merged into the working branch
        first -- or, where they conflict with Fabrika's fixes, the round does
        not start and says which files."""
        branch = branch.strip()
        repo = self.project.repo_path
        if not branch:
            raise ProjectError("name a branch to look at")
        if is_own_branch(branch):
            raise ProjectError(f"{branch} is one of Fabrika's own branches: look at the branch it was made from")
        try:
            theirs = resolve_ref(repo, branch)
        except SandboxError:
            raise ProjectError(f"there is no branch or commit {branch!r} in {repo}") from None
        slug = branch_slug(branch)
        store = self.branch_store(slug)
        prior = BranchState.model_validate(store.payload("state")) if store.payload("state") else None
        working_branch = prior.working_branch if prior else ""
        working_head = prior.working_head if prior else ""
        if working_branch:
            # Someone may have committed to Fabrika's branch since: it is the
            # head as it stands that is looked at.
            working_head = (git.out(["rev-parse", "--verify", "-q", f"refs/heads/{working_branch}"], repo)
                            or working_head).strip()
            moved = theirs != (prior.theirs_head if prior else "")
            if moved and not _is_ancestor(repo, theirs, working_head):
                working_head = self._take_in(slug, branch, working_branch, working_head, theirs)
        head = working_head or theirs
        base_ref = self.project.base_ref
        base = git.merge_base(repo, base_ref, head)
        if not base:
            raise ProjectError(f"{branch} shares no history with {base_ref}, so there is nothing to compare it to")
        if base == head:
            raise ProjectError(f"{branch} has no commits that {base_ref} does not already have")
        look = prior.look + 1 if prior else 1
        state = BranchState(
            slug=slug, project_id=self.project.id, branch=branch,
            intent=(intent.strip() or (prior.intent if prior else "")), look=look,
            base_ref=base_ref, base_sha=base, head_sha=head,
            working_branch=working_branch, working_head=working_head, theirs_head=theirs,
            phases=[PhaseState(name=n) for n in LOOK_PHASES])
        store.append("look", {
            "look": look, "branch": branch, "intent": state.intent, "base_ref": base_ref,
            "base_sha": base, "head_sha": head, "commits": commits_between(repo, base, head),
            "working_branch": working_branch, "theirs_head": theirs, "at": _now(),
        }, role="human")
        self._save(store, state)
        self._emit(state)
        return state

    def adopt_legacy_fix(self, store: EvidenceStore, state: BranchState) -> BranchState:
        """Work fixed by an earlier version of this, on `factory/fix/<branch>`
        with a second item looking at it, carries on as this work's own
        working branch: the ref is moved to `fabrika/outside/<branch>`, and the
        next round of this work looks at it."""
        if state.working_branch:
            return state
        fix = next((r["payload"] for r in reversed(store.all("look_fix"))
                    if str(r["payload"].get("branch", "")).startswith(FIX_PREFIX) and r["payload"].get("tip")), None)
        if not fix:
            return state
        repo = self.project.repo_path
        name = f"{WORK_PREFIX}{state.slug}"
        if git.create_branch(repo, name, fix["tip"]):
            return state
        old = (git.out(["rev-parse", "--verify", "-q", f"refs/heads/{fix['branch']}"], repo) or "").strip()
        if old == fix["tip"]:
            git.run(["update-ref", "-d", f"refs/heads/{fix['branch']}", old], repo)
        state.working_branch, state.working_head = name, fix["tip"]
        state.theirs_head = state.theirs_head or fix.get("head", "") or state.head_sha
        self._save(store, state)
        return state

    def _take_in(self, slug: str, branch: str, working_branch: str, working_head: str, theirs: str) -> str:
        """The person's new commits, merged into Fabrika's working branch.
        Returns the new head; refuses, naming the files, on a conflict."""
        repo = self.project.repo_path
        sandbox = Sandbox.create_detached(
            repo=repo, root=self.config.sandbox_path, project_id=self.project.id,
            name=f"{slug}-take-in", base_sha=working_head, head_sha=working_head)
        try:
            done = git.run(["-c", f"user.name={FABRIKA_AUTHOR}", "-c", "user.email=factory@localhost",
                            "merge", "--no-ff", "--no-edit", "-m", f"Merge your commits from {branch}", theirs],
                           sandbox.path, timeout=120)
            if done is None or done.returncode != 0:
                files = (git.out(["diff", "--name-only", "--diff-filter=U"], sandbox.path) or "").split()
                git.run(["merge", "--abort"], sandbox.path)
                raise ProjectError(
                    f"Your new commits on {branch} conflict with Fabrika's fixes on {working_branch}"
                    + (f", in {', '.join(files)}" if files else "")
                    + f". Merge {working_branch} into {branch}, settle them there, and look again.")
            tip = sandbox.head()
            refusal = git.create_branch(repo, working_branch, tip, replace=working_head)
            if refusal:
                raise ProjectError(refusal)
            return tip
        finally:
            sandbox.release()

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
            await self._attack(store, state, sandbox, cut, gates, packet)
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
            async with self._project_session(store, state, sandbox) as (runner, prepared, image):
                phase.detail = f"in {image}" if image else "on this machine (environment: host)"
                report = (await self._run_checks(sandbox, runner, checks, rewrote) if prepared.ready
                          else none_ran(prepared.report))
        except (DockerError, IsolationError) as exc:
            # Said in the packet, not lost with it: what was built and how it
            # is cut can still be read with no check run.
            phase.detail = "the checks' environment could not start"
            return none_ran(f"The checks' environment could not start: {exc}")
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

    @asynccontextmanager
    async def _phase(self, store: EvidenceStore, state: BranchState, name: str):
        if getattr(self, "_fixing", False):
            name = FIX_PHASES.get(name, name)
        async with super()._phase(store, state, name) as phase:
            yield phase

    def role_prompt(self, name: str) -> str:
        base = super().role_prompt(name)
        if name in ATTACK_ROLES or name in {r.name for r in self.config.review_roles()}:
            return base + "\n\n" + self.config.role_prompt("branch_attack")
        return base

    @asynccontextmanager
    async def _project_session(self, store: EvidenceStore, state: BranchState,
                               sandbox: Sandbox) -> AsyncIterator[tuple[Any, Any, str]]:
        """The project's own environment, set up and ready to test in, for as
        long as the block runs: `(runner, prepared, image)`. Raises what the
        runner raises when it cannot start."""
        runner, image = await runner_for(self.project, self.config, feature_id=state.feature_id)
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
                yield runner, prepared, image
        finally:
            down = getattr(runner, "down", None)
            if down is not None:
                await down()

    async def _attack(self, store: EvidenceStore, state: BranchState, sandbox: Sandbox,
                      cut: Sequence[Piece], gates: GateReport, packet: dict[str, Any]) -> dict[str, Any]:
        """Where the branch breaks: probes written against it and run, a panel
        reading it with those results, and the arbiter routing every finding.
        Nothing here touches the branch -- the probes are written in this
        look's own worktree and die with it, kept in the ledger for the tidy
        branch -- and nothing is repaired."""
        spec = look_spec(state, packet)
        untested = [f"- CL-{n}: {c['quote']}" for n, c in enumerate(packet.get("claims") or [], 1)
                    if c.get("state") != "shown"]
        focus = ("Claims nothing in the branch tests yet. Attack these first:\n" + "\n".join(untested)
                 if untested else "")
        tests: list = []
        report = BreakerReport()
        async with self._phase(store, state, "breaker") as phase:
            try:
                async with self._project_session(store, state, sandbox) as (runner, prepared, _image):
                    if prepared.ready:
                        tests, report = await self._run_breaker(
                            store, state, spec, sandbox, runner, [], 0, focus)
                    else:
                        report.note = f"the project's environment did not come up: {prepared.report}"
            except (DockerError, IsolationError) as exc:
                report.note = f"the project's environment could not start: {exc}"
            phase.detail = breaker_detail(tests, report, "")

        reviews = await self._review_panel(
            store, state, self._attack_block(state, packet, cut, gates, store, sandbox, report, tests, spec), 0)
        ledger = FindingLedger()
        for role_name, review in reviews:
            ledger.add(role_name, review.findings, 0, model=self._answered_by(role_name))
        # A probe's targets are what it attacks, said as the breaker likes --
        # a component, a claim id, a path. The finding's files are paths, so
        # each is put back to the file the branch has it in.
        paths = {p.path for p in cut}

        def located(target: str) -> list[str]:
            if target in paths:
                return [target]
            return sorted({p.path for p in cut if p.klass != "tests" and target in p.symbol.split(" · ")})

        tests = [t.model_copy(update={"targets": sorted({x for target in t.targets for x in located(target)})
                                      or [t.path]}) for t in tests]
        suite = BreakerSuite(strategy="", tests=tests)
        # A look has no spec to rule a probe out of, so the way out a feature's
        # finding offers is not one here.
        said = [f.model_copy(update={"recommendation": (
                    "Fix the behaviour. The probe is the test of the fix: it fails today and passes "
                    "once this is fixed.")})
                if f.recommendation.startswith("Either fix the behaviour") else f
                for f in breaker_findings(suite, report)]
        ledger.restate("breaker", said, 0, source="breaker", fresh=report.ran, reopen_settled=True)
        open_ids = [fid for fid, r in ledger.records.items() if r.outcome == "open" and not r.duplicate_of]
        qa = QAReport(summary="A look has no acceptance criteria. The commits' claims stand in for them.")
        dispositions = await self._arbitrate(store, state, spec, ledger, open_ids, gates, qa, 0)
        # The routes a look does not have: no oracle, and no spec to change.
        dispositions = [d.model_copy(update={"disposition": "escalate"})
                        if ROUTES.get(d.disposition) == "you" and d.disposition != "escalate" else d
                        for d in dispositions]
        ledger.dispose(dispositions)
        ledger.finalise(stopped_early=False)

        failing, promoted = set(report.failing), set(report.promoted)
        probes = [{
            "path": t.path, "kind": t.kind, "claim_ids": list(t.claim_ids), "hypothesis": t.hypothesis,
            "passes_when": t.passes_when, "severity": t.severity, "targets": list(t.targets),
            "contents": t.contents, "red": t.path in failing, "timed_out": t.path in set(report.timed_out),
            "promoted": t.path in promoted,
            "finding": ledger.id_of("breaker", t.hypothesis) or probe_finding_id(t.path),
        } for t in tests]
        found = {
            "look": state.look, "ledger": ledger.dump(),
            "routes": {fid: ROUTES.get(r.disposition, "you") for fid, r in ledger.records.items()},
            "breaker": report.model_dump(mode="json"), "probes": probes,
            "broken": sorted({c for p in probes if p["red"] for c in p["claim_ids"]}),
        }
        store.append("look_findings", found, role="orchestrator")
        self._carry_forward(store, state, found)
        return found

    def _carry_forward(self, store: EvidenceStore, state: BranchState, found: dict[str, Any]) -> None:
        """A risk you accepted last round is accepted this round too -- unless
        the code it is about changed since, and then it is yours to call
        again. The carried call says where it came from."""
        if state.look < 2:
            return
        earlier = {_title_key(c["title"]): c for c in look_calls(store, state.look - 1)["calls"]
                   if c["goes"] == "accept"}
        if not earlier:
            return
        last = _round_record(store, "look", state.look - 1) or {}
        changed = set((git.out(["diff", "--name-only", last.get("head_sha", ""), state.head_sha],
                               self.project.repo_path) or "").split()) if last.get("head_sha") else set()
        records = {r["finding_id"]: r for r in (found.get("ledger") or {}).get("records") or []}
        for f in (found.get("ledger") or {}).get("findings") or []:
            c = earlier.get(_title_key(f["title"]))
            if c is None or records.get(f["id"], {}).get("duplicate_of") \
                    or (found.get("routes") or {}).get(f["id"]) not in ("send_back", "you"):
                continue
            if changed & set(f.get("files") or []):
                continue
            store.append("look_ruling", {"look": state.look, "finding_id": f["id"], "ruling": "accept",
                                         "note": (c.get("ruling") or {}).get("note", ""),
                                         "carried_from": state.look - 1, "at": _now()}, role="human")

    def rule(self, slug: str, finding_id: str, ruling: str, note: str = "") -> dict[str, Any]:
        """Your call on one finding of the latest look: back to whoever built
        the branch, or a risk you accept. The latest call on a finding is the
        one that stands, until the look is ruled as a whole."""
        store = self.branch_store(slug)
        state = self.look_state(store)
        if state.stage != "awaiting_ruling":
            raise ProjectError("This look is ruled already." if state.stage == "ruled"
                               else "This look is not finished, so there is nothing to rule on yet.")
        if ruling not in RULINGS:
            raise ProjectError(f"A finding is sent back or accepted, not {ruling!r}.")
        if finding_id not in {c["finding_id"] for c in look_calls(store, state.look)["calls"]}:
            raise ProjectError(f"{finding_id} is not one of this look's findings waiting on you.")
        record = {"look": state.look, "finding_id": finding_id, "ruling": ruling, "note": note.strip(),
                  "at": _now()}
        store.append("look_ruling", record, role="human")
        return look_calls(store, state.look)

    def verdict(self, slug: str, verdict: str, note: str = "") -> dict[str, Any]:
        """The work ended, at this round's head: underwritten, or dropped.

        To underwrite is to sign for it as it is: every defect the attack found
        accepted as a risk, by you, and every call made. A defect still going
        back, or a call nobody made, is work you have not yet underwritten, and
        the refusal names them. Sending it back is not an ending: it is a round
        (`fix`, or the prompt and another look)."""
        store = self.branch_store(slug)
        state = self.look_state(store)
        if state.stage != "awaiting_ruling":
            raise ProjectError("This look is ruled already." if state.stage == "ruled"
                               else "This look is not finished, so there is nothing to rule on yet.")
        calls = look_calls(store, state.look)
        if not calls["found"]:
            raise ProjectError("This look was not attacked, so there is nothing to underwrite against. "
                               "Look again.")
        by_id = {c["finding_id"]: c for c in calls["calls"]}
        if verdict == "underwritten":
            if calls["send_back"] or calls["unruled"]:
                left = [*calls["send_back"], *calls["unruled"]]
                raise ProjectError(
                    "Not every finding is accepted yet: " + "; ".join(by_id[f]["title"] for f in left[:5])
                    + (f"; and {len(left) - 5} more" if len(left) > 5 else "")
                    + ". Accept each as a risk you sign for, or send them back to be fixed.")
        elif verdict != "dropped":
            raise ProjectError(f"Work is underwritten or dropped, not {verdict!r}. Sending it back is a round: "
                               "have Fabrika fix it, or copy the prompt and look again.")

        def said(fid: str) -> dict[str, Any]:
            c = by_id[fid]
            return {"finding_id": fid, "title": c["title"], "severity": c["severity"],
                    "note": (c["ruling"] or {}).get("note", "")}

        record = {"look": state.look, "verdict": verdict, "head_sha": state.head_sha, "note": note.strip(),
                  "accepted": [said(f) for f in calls["accepted"]],
                  "sent_back": [said(f) for f in calls["send_back"]],
                  "unruled": [said(f) for f in calls["unruled"]], "at": _now()}
        store.append("look_verdict", record, role="human")
        state.stage = "ruled"
        self._save(store, state)
        self._emit(state)
        return record

    async def open_pull_request(self, slug: str) -> dict[str, Any]:
        """The underwritten branch, pushed at the commit you underwrote and
        opened as a pull request where the project says they go. The body is
        the record: what it is, the checks that passed, the claims tests show,
        and every risk you accepted, with your reason."""
        from .. import pull_requests

        store = self.branch_store(slug)
        state = self.look_state(store)
        verdict = next((r["payload"] for r in reversed(store.all("look_verdict"))
                        if r["payload"].get("look") == state.look), None)
        if not verdict or verdict.get("verdict") != "underwritten":
            raise ProjectError("Only an underwritten look is opened as a pull request.")
        packet = next((r["payload"] for r in reversed(store.all("branch_packet"))
                       if r["payload"].get("look") == state.look), None) or {}
        sha = verdict.get("head_sha") or state.head_sha
        lines = [packet.get("summary") or packet.get("headline") or "", "",
                 f"Underwritten in Fabrika at `{sha[:12]}`, after a look that ran its checks, attacked it "
                 "with probes and a review panel, and routed every finding."]
        if verdict.get("note"):
            lines += ["", f"> {verdict['note']}"]
        passed = [r["name"] for r in (packet.get("checks") or {}).get("results") or [] if r.get("passed")]
        if passed:
            lines += ["", f"**Checks that passed:** {', '.join(passed)}."]
        shown = [c["quote"] for c in packet.get("claims") or [] if c.get("state") == "shown"]
        if shown:
            lines += ["", "## What its commits claim, shown by a test", ""] + [f"- {q}" for q in shown]
        if verdict.get("accepted"):
            lines += ["", "## Risks accepted by the reviewer", ""]
            lines += [f"- **{a['title']}** ({a['severity']})" + (f": {a['note']}" if a.get("note") else "")
                      for a in verdict["accepted"]]
        fixes = [r["payload"] for r in store.all("look_fix")]
        if fixes:
            names = {f["id"]: f["title"] for r in store.all("look_findings")
                     for f in (r["payload"].get("ledger") or {}).get("findings") or []}
            lines += ["", f"## Fixed by Fabrika, on `{state.working_branch}`", ""]
            lines += [f"- Round {f['look']}: {names.get(fid, fid)}" for f in fixes for fid in f.get("fixed") or []]
        tidy = next((r["payload"] for r in reversed(store.all("tidy")) if r["payload"].get("look") == state.look), None)
        if tidy:
            lines += ["", f"Fabrika's tests for this branch are on `{tidy['branch']}`, not in this pull request."]
        try:
            opened = await pull_requests.open_pr(
                self.project, branch=state.working_branch or state.branch, sha=sha,
                title=packet.get("headline") or state.branch, body="\n".join(lines))
        except pull_requests.PullRequestError as exc:
            store.append("pull_request_failed", {"look": state.look, "branch": state.branch, "sha": sha,
                                                 "error": str(exc), "at": _now()}, role="orchestrator")
            self._emit(state)
            raise
        store.append("pull_request", {**opened, "look": state.look}, role="orchestrator")
        self._emit(state)
        return opened

    async def fix(self, slug: str) -> dict[str, Any]:
        """This round's defects, fixed by Fabrika on the work's own branch.

        The repair rounds (`RoundsMixin._repair_rounds`) on Fabrika's working
        branch, `fabrika/outside/<branch>`, from this round's head: the probes
        the look kept are committed first, as the fence, and each fix is
        committed under what it fixes. The working branch is then moved to the
        result -- the person's branch never is -- and the next round of the
        same work is opened, to be looked at, ruled on and underwritten like
        the first. Your note on a finding goes into the repairer's objective.
        """
        from .protection import protected_paths

        store = self.branch_store(slug)
        state = self.look_state(store)
        legacy = (next((r["payload"] for r in reversed(store.all("look_verdict"))
                        if r["payload"].get("look") == state.look), None) or {}).get("verdict") == "sent_back"
        if state.stage != "awaiting_ruling" and not legacy:
            raise ProjectError("This round is not open: there is nothing to fix in it."
                               if state.stage != "reviewing" else "This round is still being looked at.")
        found = next((r["payload"] for r in reversed(store.all("look_findings"))
                      if r["payload"].get("look") == state.look), None) or {}
        calls = {c["finding_id"]: c for c in look_calls(store, state.look)["calls"]}
        targets = [fid for fid, c in calls.items() if c["goes"] == "send_back"]
        if not targets:
            raise ProjectError("Nothing is going back, so there is nothing to fix.")
        packet = next((r["payload"] for r in reversed(store.all("branch_packet"))
                       if r["payload"].get("look") == state.look), None) or {}
        spec = look_spec(state, packet)
        cfg = self.config.rework

        ledger = FindingLedger.load(found.get("ledger") or {})
        for fid in targets:
            rec = ledger.records.get(fid)
            if rec is None:
                continue
            note = ((calls[fid].get("ruling") or {}).get("note") or "").strip()
            ledger.records[fid] = rec.model_copy(update={
                "disposition": "repair", "outcome": "open", "attempts": 0,
                "disposition_reason": (rec.disposition_reason
                                       + (f" The person reviewing it said: {note}" if note else "")).strip()})

        name = state.working_branch or f"{WORK_PREFIX}{slug}"
        sandbox = Sandbox.create_detached(
            repo=self.project.repo_path, root=self.config.sandbox_path, project_id=self.project.id,
            name=f"{slug}-fix-{state.look}", base_sha=state.base_sha, head_sha=state.head_sha)
        record: dict[str, Any] = {"look": state.look, "branch": name, "head": state.head_sha,
                                  "targets": targets, "rounds": []}
        # A fix run again starts its stations again.
        for phase in state.phases:
            if phase.name.startswith("fix_"):
                phase.status, phase.detail, phase.error = "pending", "", ""
        self._save(store, state)
        self._fixing = True
        try:
            probes = [p for p in found.get("probes") or [] if p["kind"] == "regression" and not p["timed_out"]]
            home = {p["path"]: self._probe_home(state, p["path"], "regression") for p in probes}
            for p in probes:
                target = sandbox.path / home[p["path"]]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(p["contents"], encoding="utf-8")
            if probes:
                _sha, why = sandbox.commit_files(
                    list(home.values()),
                    f"Add tests from Fabrika's review of {state.branch}\n\n" + "\n".join(
                        f"- {home[p['path']]}: {p['hypothesis']}" for p in probes))
                if why:
                    raise SandboxError(why)
            tests_of_branch = sorted({p["path"] for p in packet.get("pieces") or [] if p.get("klass") == "tests"})
            protected = protected_paths(self.config, sandbox.path, extra=tests_of_branch + list(home.values()),
                                        own_dirs=self._breaker_own_dirs(state))

            def message(attempted: list[str]) -> str:
                said = [ledger.findings[f] for f in attempted if f in ledger.findings]
                return ("Fix: " + "; ".join(f.title.rstrip(".") for f in said[:3])
                        + (f"; and {len(said) - 3} more" if len(said) > 3 else "")
                        + "\n\n" + "\n\n".join(f"{f.title}\n{f.detail}" for f in said))

            record["rounds"] = await self._repair_rounds(
                store, state, spec, sandbox, ledger, protected=protected,
                fence=lambda: self._fence(store, state, sandbox, list(home.values())),
                shows={p["finding"]: home[p["path"]] for p in probes if p["red"]},
                holds=[home[p["path"]] for p in probes if not p["red"]],
                max_rounds=cfg.max_rounds, max_attempts=cfg.max_attempts_per_finding, message=message)

            outcome = {fid: ledger.records[fid].outcome for fid in targets if fid in ledger.records}
            record["fixed"] = [fid for fid, o in outcome.items() if o == "repaired"]
            record["not_fixed"] = [fid for fid, o in outcome.items() if o != "repaired"]
            async with self._phase(store, state, "fix_branch") as phase:
                tip = sandbox.head()
                if tip == state.head_sha:
                    raise ProjectError("Nothing was committed: there were no tests to add and no fix held.")
                refusal = git.create_branch(self.project.repo_path, name, tip, replace=state.working_head)
                if refusal:
                    raise ProjectError(refusal)
                phase.detail = f"{name}: {len(record['fixed'])} of {len(targets)} fixed"
            record["tip"] = tip
            state.working_branch, state.working_head = name, tip
            state.theirs_head = state.theirs_head or state.head_sha
            state.stage = "ruled"
            self._save(store, state)
            store.append("look_fix", record, role="orchestrator")
        except Exception as exc:
            store.append("look_fix_failed", {**record, "error": f"{type(exc).__name__}: {exc}"[:2000]},
                         role="orchestrator")
            raise
        finally:
            self._fixing = False
            sandbox.release()
        # The next round of the same work, at the fixed head.
        nxt = self.open_look(state.branch)
        record["next_look"] = nxt.look
        record["look_slug"] = slug
        return record

    def tidy_plan(self, store: EvidenceStore, state: BranchState) -> dict[str, Any]:
        """What a tidy branch for this look would hold: the findings routed to
        cleanup, the regression probes that hold today, and those that fail.
        Empty lists all round when the look found nothing to put on one."""
        found = next((r["payload"] for r in reversed(store.all("look_findings"))
                      if r["payload"].get("look") == state.look), None) or {}
        records = {r["finding_id"]: r for r in (found.get("ledger") or {}).get("records") or []}
        findings = {f["id"]: f for f in (found.get("ledger") or {}).get("findings") or []}
        cleanup = [findings[fid] for fid, route in (found.get("routes") or {}).items()
                   if route == "cleanup" and fid in findings and not records.get(fid, {}).get("duplicate_of")]
        regression = [p for p in found.get("probes") or [] if p["kind"] == "regression" and not p["timed_out"]]
        return {"cleanup": cleanup, "green": [p for p in regression if p["promoted"]],
                "red": [p for p in regression if p["red"]], "branch": f"{TIDY_PREFIX}{state.slug}"}

    async def make_tidy(self, slug: str) -> dict[str, Any]:
        """The tidy branch, on a press: the cleanup the arbiter routed and the
        probes worth keeping, on a branch of Fabrika's own off the look's head.

        Two commits. The first holds the cleanup and the probes that pass, and
        is held to a fence first: every check that passed at the head, and
        every probe that passes, must pass on it, or the cleanup is thrown away
        and the probes go on without it. The second holds the probes that fail
        today, alone, so the branch is green until the fix that turns them.
        Nothing else in the repository moves.
        """
        from ..schemas import Finding
        from .protection import protected_paths

        store = self.branch_store(slug)
        state = self.look_state(store)
        plan = self.tidy_plan(store, state)
        if not (plan["cleanup"] or plan["green"] or plan["red"]):
            raise ProjectError("This look found nothing to put on a tidy branch: no cleanup, and no "
                               "regression probe worth keeping.")
        packet = next((r["payload"] for r in reversed(store.all("branch_packet"))
                       if r["payload"].get("look") == state.look), None) or {}
        spec = look_spec(state, packet)
        earlier = next((r["payload"] for r in reversed(store.all("tidy"))
                        if r["payload"].get("branch") == plan["branch"]), None) or {}
        sandbox = Sandbox.create_detached(
            repo=self.project.repo_path, root=self.config.sandbox_path, project_id=self.project.id,
            name=f"{slug}-tidy-{state.look}", base_sha=state.base_sha, head_sha=state.head_sha)
        record: dict[str, Any] = {"look": state.look, "branch": plan["branch"], "head": state.head_sha,
                                  "cleanup_findings": [f["id"] for f in plan["cleanup"]]}
        try:
            home = {p["path"]: self._probe_home(state, p["path"], "regression") for p in plan["green"] + plan["red"]}

            def write(probes) -> list[str]:
                for p in probes:
                    target = sandbox.path / home[p["path"]]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(p["contents"], encoding="utf-8")
                return [home[p["path"]] for p in probes]

            # The cleanup, held to the files its findings name, and never to
            # the branch's own tests or the probes: they are the fence.
            landed: list[str] = []
            refused: list[str] = []
            phase = type("Step", (), {"detail": ""})()
            role = self.config.roles.get("simplifier")
            if plan["cleanup"] and role is not None and role.enabled:
                tests_of_branch = sorted({p["path"] for p in packet.get("pieces") or [] if p.get("klass") == "tests"})
                protected = protected_paths(self.config, sandbox.path, extra=tests_of_branch + list(home.values()),
                                            own_dirs=self._breaker_own_dirs(state))
                done = await self._simplify_writes(
                    store, state, spec, sandbox, [Finding.model_validate(f) for f in plan["cleanup"]],
                    protected, phase, objective=(
                        "Close the findings below and nothing else, on a copy of a branch someone else "
                        "wrote. Its behaviour must not change: the repository's checks and every "
                        "regression probe are run again after you, and a change that moves any of "
                        "them is thrown away."))
                if done is not None:
                    _output, wrote, refused, _applied, _rejected = done
                    landed = [f.path for f in wrote]
            record["cleanup_said"] = phase.detail
            record["refused"] = refused

            first_files = landed + write(plan["green"])
            first, why = sandbox.commit_files(
                first_files, f"Tidy {state.branch}: cleanup and probes that pass\n\n"
                + "\n".join(f"- {f['id']}: {f['title']}" for f in plan["cleanup"] if landed)) if first_files else ("", "")
            if why:
                raise SandboxError(why)

            # The fence, on what was just committed: a fixer among the checks
            # puts back whatever it rewrites, and must not put back the cleanup.
            # Nothing to fence when nothing landed but probes proved at this
            # same head: the tree is the head, plus those.
            fence = (await self._fence(store, state, sandbox, [home[p["path"]] for p in plan["green"]])
                     if landed else {"checks": [], "probes": {}, "moved": [], "skipped":
                                     "no cleanup landed, so the tree is the head and the probes proved there"})
            record["fence"] = fence
            record["kept_cleanup"] = bool(landed) and not fence["moved"]
            if landed and fence["moved"]:
                # Thrown away, and the probes go on without it: they were
                # proved at the head, which is what the tree is again.
                sandbox.reset_to(state.head_sha)
                first_files = write(plan["green"])
                first, why = sandbox.commit_files(
                    first_files, f"Tidy {state.branch}: probes that pass") if first_files else ("", "")
                if why:
                    raise SandboxError(why)
            record["first"] = {"sha": first, "files": first_files}

            red_files = write(plan["red"])
            second, why = sandbox.commit_files(red_files, (
                "Probes that fail until what they found is fixed\n\n"
                + "\n".join(f"- {home[p['path']]}: {p['hypothesis']}" for p in plan["red"]))) if red_files else ("", "")
            if why:
                raise SandboxError(why)
            record["second"] = {"sha": second, "files": red_files}

            tip = second or first
            if not tip:
                raise SandboxError("nothing was committed: every file was already on the branch")
            refusal = git.create_branch(self.project.repo_path, plan["branch"], tip,
                                        replace=earlier.get("tip", ""))
            if refusal:
                raise ProjectError(refusal)
            record["tip"] = tip
            store.append("tidy", record, role="orchestrator")
            return record
        except Exception as exc:
            store.append("tidy_failed", {**record, "error": f"{type(exc).__name__}: {exc}"[:2000]},
                         role="orchestrator")
            raise
        finally:
            sandbox.release()

    async def _fence(self, store: EvidenceStore, state: BranchState, sandbox: Sandbox,
                     probes: Sequence[str]) -> dict[str, Any]:
        """The checks that passed at the look's head and the probes that pass,
        run on the tidy branch's first commit. `moved` names each that does
        not pass there."""
        from ..gates import run_per_file

        before = next((GateReport.model_validate(r["payload"]) for r in reversed(store.all("gates"))), None)
        passed_before = {g.name for g in (before.results if before else []) if g.passed}
        status = check_status(self.project)
        checks = [g for g in (self.project.judging_gates if status == "approved" else list(self.project.state.gates))
                  if g.name in passed_before]
        cfg = self.config.rework
        rules = ([TestFileCommand(match="*", command=cfg.breaker_file_command.strip())]
                 if cfg.breaker_file_command.strip() else list(self.project.state.test_file_commands))
        out: dict[str, Any] = {"checks": [], "probes": {}, "moved": []}
        try:
            async with self._project_session(store, state, sandbox) as (runner, prepared, _image):
                if not prepared.ready:
                    out["moved"].append(f"the environment did not come up: {prepared.report}")
                    return out
                report = await self._run_checks(sandbox, runner, checks, {})
                out["checks"] = [{"name": r.name, "passed": r.passed} for r in report.results]
                out["moved"] += [r.name for r in report.results if not r.passed]
                if probes and rules:
                    _result, exits = await run_per_file(rules, list(probes), sandbox.path, runner,
                                                        timeout_s=cfg.breaker_timeout_s, name="tidy")
                    out["probes"] = {path: code for path, code in exits.items()}
                    out["moved"] += [path for path, code in exits.items() if code != 0]
        except (DockerError, IsolationError) as exc:
            out["moved"].append(f"the environment could not start: {exc}")
        return out

    def _attack_block(self, state: BranchState, packet: dict[str, Any], cut: Sequence[Piece],
                      gates: GateReport, store: EvidenceStore, sandbox: Sandbox,
                      breaker: BreakerReport, probes: Sequence[Any], spec: Spec) -> str:
        """What the review panel reads: the claims it holds the branch to, the
        branch as the packet says it, its code on disk, and the probes run."""
        from ..workspace import spec_text

        look = store.payload("look") or {}
        out = [spec_text(spec), "---",
               self._branch_said(state, look.get("commits") or [], gates, store)]
        if state.look > 1:
            accepted = [c for c in look_calls(store, state.look - 1)["calls"] if c["goes"] == "accept"]
            if accepted:
                out.append("\n# Risks the person accepted last round\n\nThey are on the record already. "
                           "Raise one again only if the code it is about has changed and it is now worse.\n")
                out.extend(f"- {c['title']}" + (f" ({c['ruling']['note']})" if (c.get("ruling") or {}).get("note")
                                                 else "") for c in accepted)
        novel = [p for p in cut if p.klass == "novel"]
        if novel:
            out.append("\n# Where the branch's own decisions are (the novel pieces)\n")
            out.extend(f"- {p.id} `{p.symbol}` in {p.path}:{p.start}" for p in novel)
        changed = sorted({p.path for p in cut if p.start})
        out.append("\n# The branch's files, as they are at its head\n\n" + tree_section(sandbox.path, changed))
        if breaker.ran or breaker.note:
            out.append(breaker_section(breaker))
            red = [t for t in probes if t.path in set(breaker.failing)]
            if red:
                out.append("\n# What each failing probe was written to show\n")
                out.extend(f"- {t.path} ({', '.join(t.claim_ids) or 'no claim'}): {t.hypothesis}" for t in red)
        return "\n".join(out)

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

    def _branch_said(self, state: BranchState, commits: list[dict], gates: GateReport,
                     store: EvidenceStore) -> str:
        """The branch as its commits, the as-built and its checks say it."""
        out = [f"# The branch\n\n{state.branch}, {len(commits)} commit(s), from "
               f"{state.base_ref} at {state.base_sha[:12]} to {state.head_sha[:12]}.",
               "\n# Intent\n\n" + (state.intent or "None given. What the commits say is all there is.")]
        theirs = [c for c in commits if not c.get("fabrika")]
        ours = [c for c in commits if c.get("fabrika")]
        out.append("\n# What its commits say\n")
        for c in theirs:
            out.append(f"## {c['sha'][:12]}\n\n{c['message']}\n")
        if ours:
            # Fabrika's own fixes and tests, from earlier rounds. What they
            # say is Fabrika's account of its own work, never one of the
            # branch's claims.
            out.append("\n# Commits Fabrika made in earlier rounds (not claims: do not quote these)\n")
            out.extend(f"- {c['sha'][:12]} {c['message'].splitlines()[0]}" for c in ours)
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
        return "\n".join(out)

    def _branch_block(self, state: BranchState, commits: list[dict], cut: Sequence[Piece],
                      gates: GateReport, store: EvidenceStore) -> str:
        out = [self._branch_said(state, commits, gates, store)]
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
