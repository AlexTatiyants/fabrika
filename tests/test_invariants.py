"""One test per invariant.

These are not unit tests of behaviour. Each one asserts a property that makes
the system worth running, and a failure here means the design has been violated
even if every feature still works.

Run them with:  python -m pytest tests -q
"""

from helpers import *  # noqa: F403


# ==========================================================================
# V-1 (INV-1) -- the verify lane cannot see the implementation
# ==========================================================================


def test_v1_verify_context_takes_a_spec_and_three_descriptions_of_the_world():
    """Three parameters now, and the two after the spec are narrow by
    construction -- in two different ways, which is the part worth holding on to.

    It was one, and the docstring said do not add a second -- "not a digest, not
    a file list, not 'just the public interface so the tests compile'". That
    warning is still right, and neither addition is what it was about. Blindness
    turned out to have costs the design had not priced, twice. The oracle wrote
    a conftest importing psycopg2, which was not installed, the suite failed to
    collect, and twelve criteria came back unverified. Later it knew its
    packages but not its environment, invented a live system that had to be
    provisioned for it, and twenty-five criteria came back unverified.

    `runtime` is safe by shape: `installable_names` drops anything not shaped
    like a package name. `where_they_run` is prose and cannot be filtered that
    way, so it is safe by provenance -- it comes from configuration, which a
    human approved and which is not this repository. The call-site test below is
    what enforces that, and it is the load-bearing half.
    """
    signature = inspect.signature(workspace.verify_context)
    parameters = list(signature.parameters.values())

    assert len(parameters) == 5, (
        f"verify_context takes {len(parameters)} parameters: "
        f"{[p.name for p in parameters]}. Exactly five: the spec, the names of "
        "installed packages, the configured description of where the tests run, "
        "the project's own test setup as the survey recorded it, and its approved "
        "testing guides. A sixth is an argument through which implementation "
        "source could arrive."
    )
    spec_param, runtime_param, where_param, testing_param, guides_param = parameters
    assert guides_param.name == "guides" and typing.get_type_hints(
        workspace.verify_context)["guides"] is str, (
        "the testing guides arrive as text rendered by code from approved documents")
    hints = typing.get_type_hints(workspace.verify_context)
    assert hints[spec_param.name] is Spec, \
        f"the first parameter must be a Spec, not {hints[spec_param.name]}"
    assert spec_param.default is inspect.Parameter.empty, "the spec is not optional"
    assert runtime_param.name == "runtime"
    assert where_param.name == "where_they_run"
    assert testing_param.name == "testing"
    assert hints[testing_param.name] is str, (
        "the test setup arrives as prose rendered from the approved survey. A "
        "richer type is a place for something read off the repository to hide."
    )
    assert hints[where_param.name] is str, (
        "the runtime contract is prose from configuration, so it is a str. A "
        "richer type is a place for a structure read off the repository to hide."
    )
    assert hints["return"] is str


def test_v1_nothing_but_a_package_name_survives_the_second_parameter():
    """The filter is what makes the second parameter safe, so test the filter.

    Everything the original docstring forbade is tried here: source, a file
    list, a path, prose. None of it is shaped like a package name and none of it
    reaches the oracle.
    """
    smuggled = [
        "def spin_the_widget():",
        "    return participation.slots_used",
        "backend/app/routers/queue.py",
        "the implementation uses a composite foreign key",
        "../../etc/passwd",
        "import psycopg2; DROP TABLE",
    ]
    assert workspace.installable_names(smuggled) == [], \
        "something that is not a package name reached the verify lane"

    assert workspace.installable_names(
        ["httpx", "psycopg2==2.9.9", "fastapi", "httpx"]) == [
        "fastapi", "httpx", "psycopg2==2.9.9"], "real package names are being dropped"

    spec = Spec(title="t", intent="i", summary="s")
    rendered = workspace.verify_context(spec, smuggled + ["httpx"])
    assert "spin_the_widget" not in rendered and "queue.py" not in rendered, \
        "implementation reached the oracle through the package list"
    assert "httpx" in rendered

    # And with nothing to say, it says nothing -- no empty section.
    assert "What is installed" not in workspace.verify_context(spec, [])


def test_v1_the_runtime_contract_is_configuration_and_says_nothing_when_empty():
    """The third parameter's guarantee is provenance, so test the two things
    provenance leaves open: what gets derived, and what a blank config renders.

    A project that has not written `oracle_runtime` must produce no section at
    all. An empty heading that promises to describe the environment and then
    describes nothing is worse than silence -- it reads as "there is nothing
    here", which is exactly the false belief that made an oracle invent its own
    live system.
    """
    from factory.schemas import ProjectState, command_env as _command_env

    # Shell noise is not an environment variable a test author should read.
    noisy = (
        "cd backend && bash -c 'nohup uvicorn app.main:app & UPID=$!; "
        "for i in $(seq 1 60); do sleep 1; done; RC=$?; "
        "API_BASE_URL=http://127.0.0.1:8000 python -m pytest ../tests/oracle; exit $RC'"
    )
    assert _command_env(noisy) == [("API_BASE_URL", "http://127.0.0.1:8000")], (
        "the derived environment is picking up shell bookkeeping, or dropping the "
        "one variable the suite actually needs"
    )

    blank = ProjectState(project_id="p")
    assert blank.runtime_contract() == "", \
        "a project that configured nothing still renders a runtime contract"

    spec = Spec(title="t", intent="i", summary="s")
    assert "What your tests can reach" not in workspace.verify_context(spec, ["httpx"], ""), \
        "an empty runtime contract still renders its heading"

    told = workspace.verify_context(spec, ["httpx"], "A server is up.")
    assert "What your tests can reach" in told
    assert "Everything not stated here is not there." in told, (
        "the oracle is given an environment without being told it is the whole "
        "of one, which is how a suite comes to assume a fixture nobody supplies"
    )
    assert "`untestable_criteria`" in told, \
        "no escape hatch is offered for a criterion the environment cannot reach"


def test_v1_an_oracle_is_told_about_its_own_project_and_no_other(tmp_path):
    """The runtime contract is read off one project's record, and nothing else.

    It was a method on the factory's configuration once, and the configuration
    held clinic's prose -- its tenants, its database roles, "a FastAPI server
    is already running at `API_BASE_URL`". Every other project's oracle was told
    that as the whole truth about its own environment. Kanban's believed it and
    wrote a fixture requiring `API_BASE_URL`, which nothing in Kanban sets.
    """
    from factory.schemas import (EnvironmentSpec, ProjectState, Service,
                                 TestFileCommand)

    clinic = ProjectState(
        project_id="clinic",
        oracle_runtime="Two tenants are seeded: `mercy` and `coastal`.",
        test_file_commands=[TestFileCommand(
            match="*",
            command="cd backend && API_BASE_URL=http://127.0.0.1:$FACTORY_PORT_API pytest ../{path}")])
    kanban = ProjectState(
        project_id="kanban",
        environment=EnvironmentSpec(kind="compose", services=[
            Service(name="api", command="uvicorn app.main:app --port $FACTORY_PORT_API"),
            Service(name="web", command="npx vite --port $FACTORY_PORT_WEB")]),
        test_file_commands=[
            TestFileCommand(match="web/e2e/*",
                            command="web/node_modules/.bin/playwright test {path}",
                            report="PLAYWRIGHT_JUNIT_OUTPUT_NAME={report} playwright test {path}"),
            TestFileCommand(match="*",
                            command="cd api && KANBAN_DATABASE_URL=postgresql://db/kanban pytest ../{path}")])

    theirs, ours = clinic.runtime_contract(), kanban.runtime_contract()
    assert "mercy" in theirs and "API_BASE_URL" in theirs
    assert "mercy" not in ours and "API_BASE_URL" not in ours, \
        "one project's environment reached another project's oracle"

    # The services are named by the address a test reads.
    assert "`FACTORY_URL_API`" in ours and "`FACTORY_URL_WEB`" in ours
    # A variable a command sets is named against the files that command runs.
    assert "Files matching `*`" in ours and "`KANBAN_DATABASE_URL=postgresql://db/kanban`" in ours
    # The harness's own reporting variable is nothing a test should read.
    assert "PLAYWRIGHT_JUNIT_OUTPUT_NAME" not in ours

    # And no copy of any of it can sit in the factory's own configuration.
    for key in config_module.REPOSITORY_FACTS:
        assert key not in config_module.ReworkConfig.model_fields or key.startswith("oracle_file"), \
            f"rework.{key} is still a setting for every project"
    assert not hasattr(config_module.ReworkConfig, "runtime_contract")

    example = (ROOT / "factory.example.yaml").read_text()
    assert "\nrework:\n" in example
    written = tmp_path / "factory.yaml"
    written.write_text(example.replace(
        "\nrework:\n", "\nrework:\n  oracle_runtime: Two tenants are seeded.\n", 1))
    with pytest.raises(config_module.ConfigError, match="rework.oracle_runtime"):
        config_module.load_config(written)


def test_v1_no_implementation_reaches_the_verify_lane():
    forbidden = (
        "repo_digest", "repo_path", "iter_repo_files", "digest",
        "read_text", "read_bytes", "open(", "Worktree", "safe_join", "glob",
    )
    call_path = {
        "workspace.verify_context": code_without_prose(workspace.verify_context),
        "workspace.spec_text": code_without_prose(workspace.spec_text),
        "Factory._verify_lane": code_without_prose(pipeline.Factory._verify_lane),
    }
    for name, source in call_path.items():
        for term in forbidden:
            assert term not in source, (
                f"{name} references {term!r}. The verify lane's call path must not be able to "
                "reach implementation source, even by mistake -- that is the whole design."
            )


def test_v1_the_lane_is_called_with_the_spec_alone():
    """Checked structurally rather than by pattern: whatever expression reaches
    the oracle as its prompt must resolve to `verify_context(spec, <packages>)`
    and nothing else, whether written inline or bound to a name first.

    The second argument is allowed and pinned: it must be the project's measured
    package list, read off project state. Not a local, not a call, not something
    assembled here -- an expression this test cannot recognise is exactly how a
    digest would arrive, and the whole value of this lane is that it cannot."""
    source = code_without_prose(pipeline.Factory._verify_lane)
    tree = ast.parse(textwrap.dedent(source))

    asks = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "ask"
    ]
    # More than one now: an unusable suite is asked for again. That makes the
    # invariant stronger rather than weaker -- it was "the one call sees only
    # the spec", and it is now "every call does", with no way to add a call that
    # sees anything else.
    assert asks, "the verify lane makes no model call"
    for call in asks:
        assert isinstance(call.args[0], ast.Constant) and call.args[0].value == "oracle", \
            "the verify lane asks a role other than the oracle"
    prompts = [call.args[1] for call in asks]
    assert len({ast.dump(pr) for pr in prompts}) == 1, (
        "the verify lane's calls do not all use the same context; a retry that "
        "sees more than the first call is a hole in the air gap"
    )
    call = asks[0]
    prompt = prompts[0]

    def is_package_list(node) -> bool:
        """`self.project.state.runtime_packages`, and only that.

        Spelled out attribute by attribute so that any other expression --
        a helper call, a local, a slice of something bigger -- fails here.
        """
        names: list[str] = []
        while isinstance(node, ast.Attribute):
            names.append(node.attr)
            node = node.value
        return (
            isinstance(node, ast.Name) and node.id == "self"
            and list(reversed(names)) == ["project", "state", "runtime_packages"]
        )

    def is_runtime_contract(node) -> bool:
        """`self.project.state.runtime_contract()`, and only that.

        The third argument is prose, so no filter on its *contents* can make it
        safe -- what makes it safe is where it comes from. This pins it to a
        no-argument call on this project's own record. It was pinned to the
        factory's configuration once, and that was one project's prose told to
        every project. A helper that read a file, a
        local assembled from anything, a formatted string: none of them are this
        shape, and none of them get to reach the oracle.
        """
        if not (isinstance(node, ast.Call) and not node.args and not node.keywords):
            return False
        names: list[str] = []
        target = node.func
        while isinstance(target, ast.Attribute):
            names.append(target.attr)
            target = target.value
        return (
            isinstance(target, ast.Name) and target.id == "self"
            and list(reversed(names)) == ["project", "state", "runtime_contract"]
        )

    def is_testing_context(node) -> bool:
        """`testing_context(self.project.state.testing)`, and only that.

        Prose again, so shape cannot filter it and provenance must: rendered from
        the survey a human approved at gate 0, never from a walk of the
        repository. A helper that read a file, or a local assembled from one,
        is not this shape and does not reach the oracle.
        """
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "testing_context"
                and len(node.args) == 1 and not node.keywords):
            return False
        names: list[str] = []
        target = node.args[0]
        while isinstance(target, ast.Attribute):
            names.append(target.attr)
            target = target.value
        return (isinstance(target, ast.Name) and target.id == "self"
                and list(reversed(names)) == ["project", "state", "testing"])

    def is_oracle_guides(node) -> bool:
        """`self._oracle_guides(store, state)`, and only that.

        The project's approved testing guides, rendered by code -- documents a
        person approved, never anything a model read in the code. That method's
        own shape is pinned below."""
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_oracle_guides"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
                and [a.id for a in node.args if isinstance(a, ast.Name)] == ["store", "state"]
                and len(node.args) == 2 and not node.keywords)

    def is_verify_context(node) -> bool:
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "verify_context"
                and not node.keywords):
            return False
        if not (node.args and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "spec"):
            return False
        if len(node.args) == 1:
            return True
        if not (len(node.args) in (2, 3, 4, 5) and is_package_list(node.args[1])):
            return False
        if len(node.args) == 2:
            return True
        if not is_runtime_contract(node.args[2]):
            return False
        if len(node.args) == 3:
            return True
        if not is_testing_context(node.args[3]):
            return False
        return len(node.args) == 4 or is_oracle_guides(node.args[4])

    if is_verify_context(prompt):
        return

    # Bound to a name first: that name must have exactly one assignment, and it
    # must be verify_context(spec).
    assert isinstance(prompt, ast.Name), (
        f"the oracle's prompt is neither verify_context(spec) nor a name bound to it: "
        f"{ast.dump(prompt)[:200]}"
    )
    bindings = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == prompt.id for t in node.targets)
    ]
    assert len(bindings) == 1, f"{prompt.id!r} is assigned {len(bindings)} times; it must be one"
    assert is_verify_context(bindings[0]), (
        f"{prompt.id!r} is not "
        "verify_context(spec[, packages[, runtime_contract()[, testing_context()]]]): "
        f"{ast.dump(bindings[0])[:200]}"
    )


# ==========================================================================
# V-2 (INV-2) -- evidence is append-only
# ==========================================================================


def test_v2_store_exposes_no_way_to_change_the_past():
    banned = ("update", "delete", "remove", "set")
    public = [n for n in dir(EvidenceStore) if not n.startswith("_")]
    offenders = [n for n in public for b in banned if b in n.lower()]
    assert not offenders, (
        f"EvidenceStore exposes {offenders}. There must be no update path and no delete path: "
        "a late agent must not be able to launder an early agent's disclosed gap."
    )


def test_v2_both_records_survive_with_increasing_seq(tmp_path):
    evidence = EvidenceStore(tmp_path, "feature-1")
    first = evidence.append("worker", {"disclosed": "I skipped the audit record"}, role="worker")
    second = evidence.append("worker", {"disclosed": "nothing at all"}, role="worker")

    records = evidence.records()
    assert len(records) == 2
    assert first["seq"] < second["seq"]
    assert records[0]["payload"]["disclosed"] == "I skipped the audit record"
    assert records[1]["payload"]["disclosed"] == "nothing at all"

    # And a fresh handle on the same file continues the sequence rather than restarting it.
    reopened = EvidenceStore(tmp_path, "feature-1")
    third = reopened.append("state", {"stage": "building"})
    assert third["seq"] == 3
    assert len(reopened.records()) == 3


def test_v2_reading_one_kind_says_what_reading_the_whole_ledger_says(tmp_path):
    """A kind is answered from an index of where its records sit, not by
    decoding the file -- the console asks for the latest `state` dozens of times
    a poll, of ledgers that run to megabytes. The index is a shortcut, so it is
    held to the slow answer whatever happens to the file underneath it."""
    from factory.pipeline import current_record

    def agrees(s):
        records = s.records()
        for kind in ("state", "spec", "flag", "flag_retag", "never-written"):
            of_kind = [r for r in records if r["kind"] == kind]
            assert s.all(kind) == of_kind
            assert s.latest(kind) == (of_kind[-1] if of_kind else None)
            assert s.has(kind) is bool(of_kind)
            assert s.current(kind) == current_record(records, kind)
        assert s.of_kinds("flag", "flag_retag") == [
            r for r in records if r["kind"] in ("flag", "flag_retag")]

    s = EvidenceStore(tmp_path, "feature-1")
    agrees(s)
    s.append("state", {"stage": "intake"})
    s.append("spec", {"v": 1})
    s.append("flag", {"id": "H-1"})
    agrees(s)

    # Written through another handle on the same file, after this one has read.
    EvidenceStore(tmp_path, "feature-1").append("flag_retag", {"flag_id": "H-1"})
    s.append("correction", {"why": "the reading was wrong"})
    agrees(s)
    assert s.current("spec") is None, "a correction voids the spec before it"
    s.append("spec", {"v": 2})
    agrees(s)
    assert s.current("spec")["payload"] == {"v": 2}

    # A record still being written is not read until it ends -- and a handle
    # opened meanwhile still counts it, so its next record does not reuse a seq.
    with s.file.open("a", encoding="utf-8") as fh:
        fh.write('{"seq": 99, "kind": "state", "payload": {"stage": "buil')
    assert s.latest("state")["payload"] == {"stage": "intake"}
    assert EvidenceStore(tmp_path, "feature-1")._seq == len(s.records()) + 1
    with s.file.open("a", encoding="utf-8") as fh:
        fh.write('ding"}}\n')
    assert s.latest("state")["payload"] == {"stage": "building"}
    agrees(s)

    # What a caller does to what it was handed is not what the next one reads.
    s.latest("spec")["payload"]["v"] = "changed by a caller"
    assert s.latest("spec")["payload"] == {"v": 2}

    # Discarded and started again under the same id: nothing carries over.
    import shutil
    shutil.rmtree(s.dir)
    fresh = EvidenceStore(tmp_path, "feature-1")
    assert not fresh.has("spec") and fresh.latest("state") is None
    fresh.append("state", {"stage": "intake"})
    agrees(fresh)


def test_v3_a_criterion_nothing_implements_is_an_orphan():
    spec = Spec(
        title="t", intent="i", summary="s",
        acceptance_criteria=[AcceptanceCriterion(id="AC-9", statement="the thing happens")],
    )
    worker = _worker("AC-1", ["src/thing.py"])
    suite = OracleSuite(strategy="from the spec", tests=[
        OracleTestFile(path="tests/test_thing.py", contents="def test(): ...", criterion_ids=["AC-9"]),
    ])

    rows = compute_trace(spec, [worker], suite)
    assert [r.criterion_id for r in rows] == ["AC-9"]
    assert rows[0].status == "orphan_requirement"
    assert rows[0].implementing_files == []


def test_v3_implemented_without_a_blind_test_is_untested():
    spec = Spec(
        title="t", intent="i", summary="s",
        acceptance_criteria=[AcceptanceCriterion(id="AC-1", statement="the thing happens")],
    )
    worker = _worker("AC-1", ["src/thing.py"])

    rows = compute_trace(spec, [worker], OracleSuite(strategy="none", tests=[]))
    assert rows[0].status == "untested"
    assert rows[0].implementing_files == ["src/thing.py"]

    with_test = OracleSuite(strategy="from the spec", tests=[
        OracleTestFile(path="tests/test_thing.py", contents="def test(): ...", criterion_ids=["AC-1"]),
    ])
    assert compute_trace(spec, [worker], with_test)[0].status == "traced"


def test_v3_a_model_claimed_trace_is_discarded():
    """A rapporteur that reports everything traced must not be believed."""
    spec = Spec(
        title="t", intent="i", summary="s",
        acceptance_criteria=[AcceptanceCriterion(id="AC-1", statement="the thing happens")],
    )
    computed = compute_trace(spec, [], OracleSuite(strategy="none", tests=[]))
    flattering = Packet(headline="all good", trace=[
        TraceRow(criterion_id="AC-1", statement="the thing happens", status="traced"),
    ])
    restored = restore_packet(flattering, trace=computed)
    assert [r.status for r in restored.trace] == ["orphan_requirement"]


def test_v3_a_file_no_criterion_claims_is_unclaimed():
    """The matrix read backwards. A file every criterion is silent about is the
    shape a scope widening has, and nothing else in the packet says so."""
    claimed = _worker("AC-1", ["src/thing.py"])
    # Same unit, one more file, named by no decision that names a criterion.
    claimed.files.append(FileWrite(path="src/defaults.py", contents="WINDOW = 90\n"))
    claimed.disclosure = SelfDisclosure(assumptions=["a default has to live somewhere"])

    rows = compute_unclaimed([claimed], claimed.files)

    assert [r.path for r in rows] == ["src/defaults.py"]
    assert rows[0].written_by == ["U-1"]
    # Its author's own words are the only account of why it exists (INV-9).
    assert rows[0].disclosures == ["U-1: a default has to live somewhere"]


def test_v3_unclaimed_needs_a_criterion_not_merely_a_decision():
    """A decision that names files but no criterion does not claim them.

    This is the whole point: an agent cannot launder a file into the accounted-for
    column by writing a rationale for it. It has to answer to something you asked
    for."""
    worker = WorkerOutput(
        unit_id="U-1", summary="s",
        files=[FileWrite(path="src/thing.py", contents="x = 1\n")],
        decisions=[Decision(id="D-1", title="a choice", rationale="because",
                            criterion_ids=[], files=["src/thing.py"])],
        disclosure=SelfDisclosure(),
    )
    assert [r.path for r in compute_unclaimed([worker], worker.files)] == ["src/thing.py"]

    worker.decisions[0].criterion_ids = ["AC-1"]
    assert compute_unclaimed([worker], worker.files) == []


def test_v3_a_model_claimed_unclaimed_list_is_discarded():
    """A rapporteur that reports nothing unaccounted for must not be believed."""
    worker = WorkerOutput(
        unit_id="U-1", summary="s",
        files=[FileWrite(path="src/stray.py", contents="x = 1\n")],
        decisions=[], disclosure=SelfDisclosure(),
    )
    computed = compute_unclaimed([worker], worker.files)
    flattering = Packet(headline="all accounted for", unclaimed=[])

    restored = restore_packet(flattering, unclaimed=computed)
    assert [r.path for r in restored.unclaimed] == ["src/stray.py"]


def test_a_comment_added_to_a_long_file_is_not_reported_as_a_long_change(tmp_path):
    """The exact shape the bug had: an 800-line module, six lines of comment."""
    from factory.sandbox import Sandbox

    body = "".join(f"row_{i} = {i}\n" for i in range(800))
    repo = _repo_with_one_commit(tmp_path / "repo", {"seed_cohort.py": body})
    sandbox = Sandbox.create(repo=repo, root=tmp_path / "sandboxes",
                             project_id="demo", feature_id="f1")

    grown = "# a note about why nothing here changed\n" * 6 + body
    sandbox.write("seed_cohort.py", grown)

    stat = sandbox.diffstat(["seed_cohort.py"])
    assert stat["seed_cohort.py"] == (6, 0), "measured against the branch, not the file"

    write = FileWrite(path="seed_cohort.py", contents=grown)
    assert write.line_count == 806, "the file really is that long; that is a different question"

    worker = WorkerOutput(unit_id="R-3", summary="s", files=[write],
                          decisions=[], disclosure=SelfDisclosure())
    rows = compute_unclaimed([worker], [write], diffstat=stat)
    assert [r.lines for r in rows] == [6], "an unasked-for change is six lines, not 806"
    assert (rows[0].added, rows[0].removed) == (6, 0)


def test_the_rapporteurs_line_counts_are_overwritten_by_the_branchs(tmp_path):
    """Materiality is the rapporteur's claim; the size of the diff is not."""
    from factory.schemas import MaterialityItem
    from factory.sandbox import Sandbox

    repo = _repo_with_one_commit(tmp_path / "repo", {"page.tsx": "a\nb\nc\nd\n"})
    sandbox = Sandbox.create(repo=repo, root=tmp_path / "sandboxes",
                             project_id="demo", feature_id="f1")
    sandbox.write("page.tsx", "a\nZ\n")
    sandbox.write("new.ts", "one\ntwo\n")

    written = [FileWrite(path="page.tsx", contents="a\nZ\n"),
               FileWrite(path="new.ts", contents="one\ntwo\n")]
    stat = sandbox.diffstat([f.path for f in written])

    flattering = Packet(headline="small", materiality=[
        MaterialityItem(path="page.tsx", klass="conventional", lines=4000, reason="r"),
    ])
    restored = restore_packet(flattering, files=written, diffstat=stat)

    edited = next(m for m in restored.materiality if m.path == "page.tsx")
    assert (edited.added, edited.removed) == (1, 3), "one line in, three out"
    assert edited.lines == 4, "not the 4000 the model asserted, nor the file's length"

    fresh = next(m for m in restored.materiality if m.path == "new.ts")
    assert (fresh.added, fresh.removed, fresh.lines) == (2, 0, 2), \
        "a file with no earlier version is wholly added"


def test_lines_written_is_the_diff_not_the_sum_of_the_files(tmp_path):
    from factory.pipeline import compute_stats
    from factory.sandbox import Sandbox
    from factory.schemas import GateReport, QAReport

    long_file = "".join(f"line {i}\n" for i in range(500))
    repo = _repo_with_one_commit(tmp_path / "repo", {"big.py": long_file})
    sandbox = Sandbox.create(repo=repo, root=tmp_path / "sandboxes",
                             project_id="demo", feature_id="f1")
    touched = long_file + "TAIL = 1\n"
    sandbox.write("big.py", touched)

    written = [FileWrite(path="big.py", contents=touched)]
    stat = sandbox.diffstat(["big.py"])
    spec = Spec(title="t", intent="i", summary="s")
    stats = compute_stats(spec, [], QAReport(summary=""), GateReport(), [], written, [],
                          diffstat=stat)

    assert stats.lines_written == 1, "one line was written; five hundred were already there"
    assert (stats.lines_added, stats.lines_removed) == (1, 0)
    assert stats.files_written == 1, "the file count was never the thing that was wrong"


def test_without_a_branch_to_ask_a_file_counts_as_wholly_new(tmp_path):
    """The fallback is a reading, not a shrug: no before means every line is new.

    It is also what the pipeline's own callers get before a sandbox exists, so
    it has to be a defensible number rather than a zero.
    """
    from factory.pipeline import change_size

    assert change_size("a.py", "x = 1\ny = 2\n", None) == (2, 0)
    assert change_size("a.py", "x = 1\n", {"other.py": (9, 9)}) == (1, 0)
    assert change_size("a.py", "x = 1\n", {"a.py": (0, 0)}) == (0, 0), \
        "a measured no-op is a measurement, not a missing entry"


def test_a_unit_that_ran_without_an_environment_becomes_a_blocker(tmp_path):
    from factory.pipeline import check_unit_environments

    store = EvidenceStore(tmp_path, "feature-1")
    _env_record(store, "U-2", True)
    _env_record(store, "U-1", False,
                problem="preparation step 2 of 2 failed: `python -m app.seed --reset`",
                log="$ python -m app.seed --reset\n[exit 1]\nconnection refused")

    findings = check_unit_environments(store, "sha256:x")

    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "blocker", (
        "the gates that pass afterwards run in a different, working environment, "
        "so this is the premise under every other claim in the packet")
    assert "U-1" in finding.title
    assert "U-2" not in finding.title, "a unit that had its stack is not a finding"
    assert "app.seed --reset" in finding.evidence, "which command failed"
    assert "connection refused" in finding.evidence, (
        "and what it said -- the log was captured and thrown away for a long time")


def test_a_run_where_every_unit_had_its_stack_raises_nothing(tmp_path):
    from factory.pipeline import check_unit_environments

    store = EvidenceStore(tmp_path, "feature-1")
    _env_record(store, "U-1", True)
    _env_record(store, "U-2", True)

    assert check_unit_environments(store, "sha256:x") == []


def test_an_environment_that_came_up_on_a_retry_is_not_reported_blind(tmp_path):
    """A retried run keeps its ledger. Two attempts failed to bring up any
    unit's stack; the third, after a fix, brought up all of them -- and the
    packet still said every agent had written code it could not run."""
    from factory.pipeline import check_unit_environments

    store = EvidenceStore(tmp_path, "feature-1")
    _env_record(store, "U-1", False, problem="putting route 'default' into the image failed")
    _env_record(store, "U-2", False, problem="putting route 'default' into the image failed")
    _env_record(store, "U-1", True)
    _env_record(store, "U-2", True)
    assert check_unit_environments(store, "sha256:x") == []

    # And the other way round: the last attempt is the one that counts.
    _env_record(store, "U-2", False, problem="3 setup command(s) failed")
    findings = check_unit_environments(store, "sha256:x")
    assert [f.id for f in findings] == ["unitenv-1"]
    assert "U-2" in findings[0].title and "U-1" not in findings[0].title


def test_a_failed_environment_tells_the_agent_what_it_printed():
    """The agent was told which command failed and never what it said, which is
    the difference between a broken seed and an env var it could have set."""
    from factory.unitenv import _note

    note = _note(False, (), "preparation step 2 of 2 failed", "$ seed\n[exit 1]\nno such role")

    assert "no such role" in note
    assert "no such database" not in note


def test_every_authoring_session_reports_whether_it_had_an_environment():
    """The structural half. A launcher that takes an environment and does not
    report on it is how this went unseen: `ready` was consumed by merging an
    empty dict of variables, which is not the same as reading it."""
    source = factory_source("pipeline")
    # Each call passing a prepared environment, with the argument list that follows it.
    calls = re.findall(r"environment=self\.unit_env\((?:[^()]|\([^()]*\))*\)(.{0,220})",
                       source, re.S)
    assert calls, "no authoring session takes an environment any more; this test is stale"
    missing = [c for c in calls if "on_environment" not in c]
    assert not missing, (
        f"{len(missing)} authoring session(s) are handed a stack and never say whether "
        "it came up")


def test_a_plan_that_was_nearly_out_at_the_start_reaches_the_packet(tmp_path):
    from factory.pipeline import check_headroom

    store = EvidenceStore(tmp_path, "feature-1")
    _headroom(store, {
        "claude-code": {"windowed": True, "current": True, "windows": [
            {"name": "five_hour", "label": "5h", "utilization": 0.96, "stale": False},
            {"name": "seven_day", "label": "weekly", "utilization": 0.20, "stale": False},
        ]},
        "codex": {"windowed": True, "current": True, "windows": [
            {"name": "primary", "label": "5h", "utilization": 0.10, "stale": False},
        ]},
    })

    findings = check_headroom(store, "sha256:x")
    assert [f.id for f in findings] == ["headroom-1"]
    assert "96%" in findings[0].evidence and "5h" in findings[0].evidence
    assert "codex" not in findings[0].evidence, "a plan with room is not a finding"
    assert findings[0].severity == "major", (
        "a window is shared with the human at the same terminal, so it moves "
        "while the build runs -- a person weighs this, the loop does not refuse on it")


def test_a_plan_nobody_could_read_is_said_too(tmp_path):
    """Different from "it had room", and the run that prompted this could not
    tell the two apart."""
    from factory.pipeline import check_headroom

    store = EvidenceStore(tmp_path, "feature-1")
    _headroom(store, {"claude-code": {"windowed": True, "current": False, "windows": [
        {"name": "five_hour", "utilization": 0.42, "stale": True},
    ]}})

    findings = check_headroom(store, "sha256:x")
    assert [f.id for f in findings] == ["headroom-2"]
    assert "claude-code" in findings[0].evidence


def test_a_route_with_no_window_is_not_an_unknown(tmp_path):
    """A route billed per token has no rolling window and the budget guard
    covers it. Reporting that as unreadable headroom would put a finding on
    every packet for a route that is working exactly as designed."""
    from factory.pipeline import check_headroom

    store = EvidenceStore(tmp_path, "feature-1")
    _headroom(store, {"openrouter": {"windowed": False, "current": True, "windows": []}})
    assert check_headroom(store, "sha256:x") == []


def test_a_plan_without_room_schedules_the_run_instead_of_refusing_it(tmp_path):
    """A rolling window resets at a known second, so "there is not enough left"
    and "there will be in 31 minutes" are the same fact said usefully."""
    import asyncio

    from factory.routes import RouteMonitor

    monitor = RouteMonitor(_sched_cfg(tmp_path), None)
    _seed_gauge(monitor, utilization=0.95, resets_in=1800)
    monitor.draws.record({"claude-code": 0.14})
    monitor.draws.record({"claude-code": 0.11})

    v = asyncio.run(monitor.plan_verdict(["claude-code"]))
    assert v.verdict == "wait"
    assert v.route == "claude-code"
    assert round(v.seconds_to_start / 60) == 30
    assert "5h" in v.reason and "14%" in v.reason


def test_a_plan_with_room_is_not_held_up(tmp_path):
    import asyncio

    from factory.routes import RouteMonitor

    monitor = RouteMonitor(_sched_cfg(tmp_path), None)
    _seed_gauge(monitor, utilization=0.10, resets_in=1800)
    monitor.draws.record({"claude-code": 0.14})
    monitor.draws.record({"claude-code": 0.11})

    assert asyncio.run(monitor.plan_verdict(["claude-code"])).verdict == "go"


def test_one_observation_is_an_anecdote_and_holds_nothing_back(tmp_path):
    """Deferring a build on a single measurement is worse than starting one
    that might not finish."""
    import asyncio

    from factory.routes import RouteMonitor

    monitor = RouteMonitor(_sched_cfg(tmp_path), None)
    _seed_gauge(monitor, utilization=0.99, resets_in=600)
    monitor.draws.record({"claude-code": 0.40})

    assert asyncio.run(monitor.plan_verdict(["claude-code"])).verdict == "go", (
        "one draw on record is not a basis for holding work")


def test_a_reset_too_far_off_is_a_warning_not_a_schedule(tmp_path):
    """Nobody wants a feature that quietly starts on Sunday."""
    import asyncio

    from factory.routes import RouteMonitor

    monitor = RouteMonitor(_sched_cfg(tmp_path, max_wait_s=3600), None)
    _seed_gauge(monitor, utilization=0.97, resets_in=4 * 86400)
    monitor.draws.record({"claude-code": 0.20})
    monitor.draws.record({"claude-code": 0.22})

    v = asyncio.run(monitor.plan_verdict(["claude-code"]))
    assert v.verdict != "wait"
    assert "too far off to wait for" in v.reason


def test_a_scheduled_run_survives_the_process_that_scheduled_it():
    """The sleeping task dies with the server. The promise is on the feature, so
    boot can re-arm it -- a human who was told 14:05 has stopped watching."""
    source = factory_source("server")
    assert "deferred_features()" in source, (
        "nothing re-arms a deferred build at boot")
    assert "start_at" in schemas.FeatureState.model_fields, (
        "the schedule must outlive the task holding it")


def test_a_reading_is_dated_by_its_own_record_not_the_file(tmp_path):
    """A session log is appended to while the tool runs, so its mtime dates the
    newest line and not the newest reading -- which in a long session is hours
    older. That gap is what put "plan read just now" directly above "reset since
    last read" on one card, two true lines that add up to a false one."""
    import json as _json
    import os

    from factory.config import RouteConfig
    from factory.meters import read_from_disk

    wrote_at = time.time() - 6 * 3600
    log = tmp_path / "rollout-abc.jsonl"
    log.write_text("\n".join([
        _json.dumps({"timestamp": "2020-01-01T00:00:00Z", "payload": {"rate_limits": {}}}),
        _json.dumps({
            "timestamp": datetime.datetime.fromtimestamp(
                wrote_at, datetime.timezone.utc).isoformat(),
            "payload": {"rate_limits": {"primary": {
                "used_percent": 40.0, "window_minutes": 300,
                "resets_at": time.time() + 600}}}}),
    ]), encoding="utf-8")
    os.utime(log, (time.time(), time.time()))     # touched now, as a live session is

    route = RouteConfig(
        name="codex", meter_windows_key="payload.rate_limits",
        meter_utilization_key="used_percent", meter_resets_key="resets_at",
        meter_utilization_scale=0.01, meter_window_minutes_key="window_minutes",
        meter_observed_key="timestamp",
        meter_sidecar_glob=str(tmp_path / "rollout-*.jsonl"))

    window = read_from_disk(route)[0][0]
    assert window.observed_at == pytest.approx(wrote_at, abs=2), (
        "dated when the tool wrote it, not when we opened the file")


def test_a_current_gauge_is_never_worth_a_turn(tmp_path):
    """The escalation exists for a reading that does not describe the window
    that is running. One that does is the whole point of reading for free."""
    import asyncio

    from factory.meters import WindowReading
    from factory.routes import RouteMonitor

    monitor = RouteMonitor(_sched_cfg(tmp_path), None)
    monitor._meters.record("claude-code", [WindowReading(
        name="five_hour", utilization=0.3, resets_at=time.time() + 3600,
        observed_at=time.time())])
    probes = []
    monitor.refresh = lambda name: probes.append(name)

    asyncio.run(monitor.gauge_live("claude-code"))
    assert probes == [], "a reading that describes the running window is enough"


def test_a_self_reading_tool_whose_log_went_stale_is_still_worth_a_turn(tmp_path):
    """A session log holds the reading from the last time the tool *ran*. When
    its window resets and nobody has used it since, the newest thing on disk
    describes a window that has ended -- and probing does add a reading, because
    the probe makes the tool run and running is what writes a new one.

    The card that exposed this said "plan read just now" and "reset since last
    read" on consecutive lines, both true and jointly false.
    """
    import asyncio

    from factory.config import RouteConfig
    from factory.meters import WindowReading
    from factory.routes import RouteMonitor

    cfg = _sched_cfg(tmp_path)
    cfg.routes["codex"] = RouteConfig(
        name="codex", kind="cli", command=["true"],
        meter_windows_key="payload.rate_limits",
        meter_sidecar_glob=str(tmp_path / "rollout-*.jsonl"))
    monitor = RouteMonitor(cfg, None)
    monitor._meters.record("codex", [WindowReading(
        name="primary", utilization=0.0, resets_at=time.time() - 60,
        observed_at=time.time() - 7200)])

    probes = []

    async def fake_refresh(name):
        probes.append(name)

    monitor.refresh = fake_refresh
    asyncio.run(monitor.gauge_live("codex"))
    assert probes == ["codex"], (
        "writing its gauge down does not make a tool's silence a current reading")


def test_a_probe_that_cannot_make_the_reading_current_is_not_repeated(tmp_path, monkeypatch):
    """Every visit to the crew page spent a Codex turn for a reading the turn
    could never reach -- its log stayed in the container it ran in -- and the
    page waited six seconds for it each time. A probe that leaves the reading
    out of date now holds off the next one; one that lands clears the hold."""
    import asyncio

    from factory import routes as routes_mod
    from factory.config import RouteConfig
    from factory.meters import WindowReading
    from factory.routes import RouteMonitor

    cfg = _sched_cfg(tmp_path)
    cfg.routes["codex"] = RouteConfig(
        name="codex", kind="cli", command=["true"], meter_windows_key="payload.rate_limits",
        meter_sidecar_glob=str(tmp_path / "rollout-*.jsonl"))
    monitor = RouteMonitor(cfg, None)
    stale = WindowReading(name="primary", utilization=0.0, resets_at=time.time() - 60,
                          observed_at=time.time() - 7200)
    monitor._meters.record("codex", [stale])
    probes = []
    lands = {"now": False}

    async def fake_refresh(name):
        probes.append(name)
        if lands["now"]:
            monitor._meters.record(name, [WindowReading(
                name="primary", utilization=0.1, resets_at=time.time() + 3600,
                observed_at=time.time())])

    monitor.refresh = fake_refresh
    asyncio.run(monitor.gauge_live("codex"))
    asyncio.run(monitor.gauge_live("codex"))
    assert probes == ["codex"], "a second visit spent a second turn on the same dead end"

    # After the hold, it tries again -- and a probe that lands clears it.
    monkeypatch.setattr(routes_mod, "PROBE_HOLD_S", 0)
    lands["now"] = True
    asyncio.run(monitor.gauge_live("codex"))
    assert probes == ["codex", "codex"] and "codex" not in monitor._probed


def test_a_sealed_tool_s_session_log_is_copied_out_and_read(tmp_path, monkeypatch):
    """Codex writes its plan's windows only into its session log, and it now
    runs sealed: the log was written in the container's home and removed with
    it, so the gauge never moved. The log is copied, before the container goes,
    into a folder of Fabrika's own laid out like the tool's home -- never into
    the person's own `~/.codex` -- and the gauge reads there as well."""
    import inspect as _inspect

    from factory import agentbox, meters

    route = _codex_route(meter_sidecar_glob="~/.codex/sessions/**/rollout-*-{thread_id}.jsonl",
                         meter_window_minutes_key="window_minutes")
    assert meters.sidecar_dir(route) == ".codex/sessions"
    assert meters.sidecar_dir(_codex_route(meter_sidecar_glob="/abs/rollout-*.jsonl")) == ""

    calls = []

    async def fake_docker(argv, timeout=120.0):
        calls.append(list(argv))
        # What `docker cp box:/agent-home/.codex/sessions/. <target>` leaves.
        day = Path(argv[-1]) / "2026" / "09" / "25"
        day.mkdir(parents=True, exist_ok=True)
        (day / "rollout-2026-09-25T00-00-00-thread-7.jsonl").write_text(json.dumps(
            {"payload": {"rate_limits": {"primary": {
                "used_percent": 25.0, "window_minutes": 300,
                "resets_at": time.time() + 3600}}}}), encoding="utf-8")
        return 0, ""

    monkeypatch.setattr(agentbox, "_docker", fake_docker)
    asyncio.run(agentbox.collect_sidecars("docker", "fabrika-answer-x", route,
                                          "/agent-home", tmp_path))
    [cp] = calls
    assert cp[:3] == ["docker", "cp", "fabrika-answer-x:/agent-home/.codex/sessions/."]
    assert Path(cp[3]).is_relative_to(tmp_path / "sidecars"), "copied into the person's own home"

    by_call = meters.read_windows({"thread_id": "thread-7"}, route)
    assert by_call and by_call[0].utilization == pytest.approx(0.25)
    latest, _ = meters.read_from_disk(route)
    assert latest and not latest[0].stale, "the free read between calls does not see the copy"

    # Old copies are pruned; only the newest is ever read.
    for i in range(5):
        (Path(cp[3]) / f"old-{i}.jsonl").write_text("{}")
    meters.prune_mirror(tmp_path / "sidecars", "codex", keep=2)
    assert len([f for f in (tmp_path / "sidecars" / "codex").rglob("*") if f.is_file()]) == 2

    # Both ways a tool runs sealed copy it out, before the container goes.
    box = _inspect.getsource(agentbox.completion_box)
    assert box.index("collect_sidecars(") < box.index('"rm", "-f", name')
    run = factory_source("executors")
    assert run.index("await session.collect_sidecars()") < run.index(
        "await session.close()", run.index("await session.collect_sidecars()"))


def test_a_reading_lifted_off_disk_is_dated_when_the_tool_wrote_it(tmp_path):
    """Not when we happened to open the file. Stamping it with now is what made
    a card claim "plan read just now" over a window that ended hours ago."""
    import json as _json
    import os

    from factory.config import RouteConfig
    from factory.meters import read_windows

    log = tmp_path / "rollout-abc.jsonl"
    log.write_text(_json.dumps({"payload": {"rate_limits": {
        "primary": {"used_percent": 12.0, "window_minutes": 300,
                    "resets_at": time.time() + 3600}}}}), encoding="utf-8")
    wrote_at = time.time() - 7200
    os.utime(log, (wrote_at, wrote_at))

    route = RouteConfig(
        name="codex", meter_windows_key="payload.rate_limits",
        meter_utilization_key="used_percent", meter_resets_key="resets_at",
        meter_utilization_scale=0.01, meter_window_minutes_key="window_minutes",
        meter_sidecar_glob=str(tmp_path / "rollout-*.jsonl"))

    window = read_windows({}, route)[0]
    assert window.observed_at == pytest.approx(wrote_at, abs=2), (
        "the reading is as old as the tool's own record of it")
    assert window.utilization == pytest.approx(0.12)


def test_a_blind_test_a_reviewer_calls_wrong_goes_to_a_person():
    """Six reviewer passes said one failing blind test asserted something the
    framework never exposes, and said it in their summaries -- where nothing
    reads. Filed as a finding it reaches the arbiter, and the arbiter's only
    move with it is a person: a model deciding which assertion to drop is the
    contract weakened by whoever it was meant to judge."""
    from factory.pipeline import SUSPECT_TEST, suspect_tests_to_a_person

    ledger = FindingLedger()
    ledger.add("reviewer", [Finding(
        id="r-1", title="A test checks a header the test tools never see", severity="major",
        category=SUSPECT_TEST, detail="d", evidence="AC-10 says the body; the test reads a header",
        files=["web/src/app/rating.acceptance.spec.ts"], criterion_ids=["AC-10"])], 0)
    ledger.add("reviewer", [Finding(id="r-2", title="A race", severity="major",
                                    detail="d", evidence="e")], 0)
    ledger.add("human", [Finding(
        id="h-1", title="This test asks for what the design cannot show", severity="major",
        category=SUSPECT_TEST, detail="d", evidence="e", files=["web/x.spec.ts"])], 0)
    # And the other kind, which the first version of this rule swallowed: the
    # oracle's own `starState` helper counted every star glyph as filled, the
    # arbiter rightly sent it to the oracle, and forcing it to a person left
    # AC-7 and AC-11 failing on a bug nobody was allowed to fix.
    ledger.add("reviewer", [Finding(
        id="r-3", title="The star helper counts every star as filled", severity="major",
        category="test_code", detail="d", evidence="the helper checks the glyph first",
        files=["web/src/app/star-ratings.acceptance.spec.ts"], criterion_ids=["AC-7"])], 0)

    suspect, race, person, helper = list(ledger.records)
    ruled = suspect_tests_to_a_person([
        FindingDisposition(finding_id=suspect, disposition="oracle", reason="the test is wrong"),
        FindingDisposition(finding_id=race, disposition="repair", reason="real"),
        FindingDisposition(finding_id=person, disposition="oracle", reason="a person said so"),
        FindingDisposition(finding_id=helper, disposition="oracle", reason="its helper is broken"),
    ], ledger)
    by_id = {d.finding_id: d for d in ruled}
    assert by_id[suspect].disposition == "escalate"
    assert by_id[suspect].reason.startswith("the test is wrong"), "the arbiter's words stay first"
    assert by_id[race].disposition == "repair", "nothing else is touched"
    assert by_id[person].disposition == "oracle", \
        "a person's ruling is the one that reaches the oracle"
    assert by_id[helper].disposition == "oracle", \
        "a blind test whose own code is broken is the oracle's to fix"

    # Told, as well as held.
    reviewer = (ROOT / "factory" / "roles" / "reviewer.md").read_text(encoding="utf-8")
    arbiter = (ROOT / "factory" / "roles" / "arbiter.md").read_text(encoding="utf-8")
    assert "`category: suspect_test`" in reviewer and "`category: test_code`" in reviewer
    assert "A `suspect_test` finding" in arbiter and "`escalate`, always" in arbiter
    assert "A `test_code` finding" in arbiter
    assert "suspect_tests_to_a_person(dispositions, ledger)" in inspect.getsource(
        pipeline.Factory._arbitrate)


def test_a_simplify_finding_is_never_also_a_repair_target():
    """The two routes are disjoint, and a repairer must never be sent at one.

    A repairer works to minimal diff, no refactoring, no new abstractions --
    and the whole of a `simplify` finding is that the fix is to remove
    something for being unnecessary, which is the one move those rules forbid.
    Routing one to a repair spends an attempt on a change it is not allowed to
    make, and a spent attempt retires the finding as tried and unfixable.
    """
    ledger = _ledger_with("helper written twice", "boundary check missing")
    dup, defect = list(ledger.records)
    ledger.dispose([
        FindingDisposition(finding_id=dup, disposition="simplify",
                           reason="correct code, written twice"),
        FindingDisposition(finding_id=defect, disposition="repair", reason="off by one"),
    ])

    assert ledger.simplifiable() == [dup]
    assert ledger.repairable(2) == [defect]
    assert not set(ledger.simplifiable()) & set(ledger.repairable(2))


def test_the_simplify_pass_runs_once_and_before_the_panel_that_reads_it():
    """Three properties the convergence block has to hold, all of them cheap to
    get wrong and none of them visible in a passing run.

    It is latched, because the block it sits in is reached again on a forced
    final pass and a second pass would be a second unreviewed edit. It runs
    before the exit panel, because its diff is the last thing to touch the tree
    and the panel is the mechanism for "a late edit broke something nobody was
    looking at". And when it changes a file the gates run again -- without that
    the packet reports checks that ran against code the branch no longer holds,
    and "behaviour did not change" is the one claim in the run that nothing
    measured.
    """
    src = inspect.getsource(pipeline.Factory._converge)

    assert "simplified = False" in src, "the pass has no latch and can run twice"
    call = src.index("await self._simplify_round(")
    assert src.index("if not simplified:") < call

    panel = src.index("await self._review_panel(", call)
    assert call < panel, "the simplification is never read by a panel"

    between = src[call:panel]
    assert "await self._assess(" in between, \
        "the gates are not re-run, so the packet reports checks against a tree that moved"
    assert "compute_trace(" in between and "compute_qa(" in between, \
        "the traceability and qa the packet reports predate the simplification"


def test_a_restated_finding_is_routed_once_and_not_repaired_twice():
    ledger = _ledger_with("actor is not nullable", "audit actor column stays NOT NULL")
    a, b = list(ledger.records)

    ledger.dispose([
        FindingDisposition(finding_id=a, disposition="repair", reason="fix it"),
        FindingDisposition(finding_id=b, disposition="repair", reason="same thing",
                           duplicate_of=a),
    ])

    assert ledger.records[b].duplicate_of == a
    assert ledger.records[a].corroborated_by == [b], "the corroboration lands on the survivor"
    assert ledger.repairable(3) == [a], "one defect, one repair unit"


def test_a_restatement_takes_the_outcome_of_what_it_restates():
    """Never routed means nothing else would ever settle it, and the packet
    would report as unresolved the defect the loop just fixed."""
    ledger = _ledger_with("actor is not nullable", "audit actor column stays NOT NULL")
    a, b = list(ledger.records)
    ledger.dispose([
        FindingDisposition(finding_id=a, disposition="repair", reason="fix it"),
        FindingDisposition(finding_id=b, disposition="repair", reason="same",
                           duplicate_of=a),
    ])
    ledger.apply_verdicts(
        [RepairVerdict(finding_id=a, status="fixed", evidence="migration added")],
        1, "abc123", 3)
    ledger.settle_duplicates()

    assert ledger.records[b].outcome == "repaired"
    assert ledger.records[b].commit == "abc123"
    assert a in ledger.records[b].outcome_evidence, "and says which finding carried the fix"


def test_nothing_is_deleted_by_being_called_a_duplicate():
    """INV-11. A merge that shortened the packet would make the loop look better
    and be worth less -- the duplicate keeps its record, its text and its place."""
    ledger = _ledger_with("actor is not nullable", "audit actor column stays NOT NULL")
    a, b = list(ledger.records)
    ledger.dispose([FindingDisposition(finding_id=b, disposition="repair",
                                       reason="same", duplicate_of=a)])

    assert len(ledger.all_findings()) == 2
    assert ledger.findings[b].title == "audit actor column stays NOT NULL"
    assert {r.finding_id for r in ledger.all_records()} == {a, b}


def test_the_harsher_reading_survives_the_merge():
    ledger = FindingLedger()
    ledger.add("reviewer", [Finding(id="r-1", title="actor nullability", severity="minor",
                                    detail="d", evidence="e")], 0)
    ledger.add("hacker", [Finding(id="h-1", title="actor is not nullable", severity="blocker",
                                  detail="d", evidence="e")], 0)
    a, b = list(ledger.records)
    ledger.dispose([FindingDisposition(finding_id=b, disposition="repair",
                                       reason="same", duplicate_of=a)])

    assert ledger.records[a].severity == "blocker", (
        "a blocker restated as a minor is still a blocker")


def test_a_chain_of_restatements_resolves_to_the_one_that_carries_the_fix():
    """A duplicates B, B duplicates C. Tying A to a record that is itself never
    routed would leave nothing to settle it."""
    ledger = _ledger_with("one", "two", "three")
    a, b, c = list(ledger.records)[:3]
    ledger.dispose([
        FindingDisposition(finding_id=c, disposition="repair", reason="fix it"),
        FindingDisposition(finding_id=b, disposition="repair", reason="s", duplicate_of=c),
        FindingDisposition(finding_id=a, disposition="repair", reason="s", duplicate_of=b),
    ])

    assert ledger.records[a].duplicate_of == c, "followed through to the root"
    assert ledger.repairable(3) == [c]


def test_two_findings_pointing_at_each_other_do_not_hang_the_loop():
    ledger = _ledger_with("one", "two")
    a, b = list(ledger.records)
    ledger.records[a].duplicate_of = b
    ledger.records[b].duplicate_of = a

    assert ledger.repairable(3) == [], "a cycle resolves to nothing rather than spinning"
    ledger.settle_duplicates()          # must return


def test_every_rerun_the_server_offers_has_a_way_to_reach_it():
    """`rebuild` existed as an endpoint, the ledger had a word for it, and six
    places in the console were written to display one -- and nothing ever fired
    it. A control a human cannot reach is a control the product does not have.
    """
    import re

    server = factory_source("server")
    console = app_js()
    reruns = set(re.findall(r'@app\.post\("/api/projects/\{project_id\}/features/'
                            r'\{feature_id\}/(rebuild|revalidate|retry-build)"', server))
    assert reruns, "no rerun endpoints found; this test needs rewriting"
    unreachable = sorted(r for r in reruns if f"/{r}`" not in console and f"/{r}'" not in console)
    assert not unreachable, (
        f"these reruns can only be reached with curl: {unreachable}")


def test_the_control_that_replaces_code_stops_and_asks():
    """Principle 5, and the one case that earns a modal. `Verify again` and
    `Build again` cost about the same hour; only one of them resets a branch.

    Asked in a dialog rather than on the button because what it replaces and
    what it keeps is three sentences, and three sentences do not go on a
    control -- the armed label stretched its own bar and ran under the tooltip
    that was explaining it.
    """
    console = app_js()
    ask = re.search(r"async function askRebuild\(\) \{(.*?)\n\}", console, re.S)
    assert ask, "the rebuild control does not stop to ask"
    body = ask.group(1)
    assert "replaced rather than added to" in body, "it must name what it replaces"
    assert "Spec review is not reopened" in body, "and what survives, beside it"
    assert "danger: true" in body

    # Native `<dialog>`: focus trap, Escape, and a backdrop, none of it hand-rolled.
    assert "showModal()" in console and ".confirm::backdrop" in \
        stylesheet()
    # Cancel holds focus, so the destructive button is not under a reflex Return.
    assert re.search(r"querySelector\(.\[value=\"no\"\].\)\?\.focus\(\)", console)


def test_a_panel_that_all_died_is_a_blocker_not_a_clean_bill(tmp_path):
    from factory.pipeline import check_agent_failures

    store = EvidenceStore(tmp_path, "feature-1")
    for role, error in [
        ("adversary", "route 'codex' reported a failure: Your workspace is out of credits."),
        ("hacker", "route 'codex' reported a failure: {\"findings\":[]}"),
        ("reviewer", "route 'codex' reported a failure: {\"findings\":[]}"),
    ]:
        store.append("review_failed", {"role": role, "error": error},
                     role=role, spec_hash="sha256:x")

    findings = check_agent_failures(store, "sha256:x", expected_reviewers=3)

    assert len(findings) == 1
    assert findings[0].severity == "blocker", (
        "losing one agent leaves a reading; losing all of them leaves none, "
        "while every count downstream still says zero")
    assert "nobody looked" in findings[0].detail
    assert "out of credits" in findings[0].evidence, "the reason has to survive to the packet"


def test_losing_one_review_agent_is_not_a_blocker(tmp_path):
    from factory.pipeline import check_agent_failures

    store = EvidenceStore(tmp_path, "feature-1")
    store.append("review_failed", {"role": "hacker", "error": "boom"},
                 role="hacker", spec_hash="sha256:x")

    findings = check_agent_failures(store, "sha256:x", expected_reviewers=3)
    assert [f.severity for f in findings] == ["major"]


def test_a_dead_arbiter_and_a_dead_scout_reach_the_packet(tmp_path):
    from factory.pipeline import check_agent_failures

    store = EvidenceStore(tmp_path, "feature-1")
    store.append("arbiter_failed", {"error": "ceiling"}, role="arbiter", spec_hash="sha256:x")
    store.append("scout_failed", {"error": "no repo"}, role="scout", spec_hash="sha256:x")

    severities = {f.id: f.severity for f in check_agent_failures(store, "sha256:x")}
    assert severities == {"agent-arbiter-failed": "blocker", "agent-scout-failed": "blocker"}


def test_every_failure_record_the_pipeline_writes_is_read_back():
    """The structural half. Each of these kinds was appended and then read by
    nothing, which is how a whole panel went missing without a trace. A new one
    added without a reading is the same hole reopened."""
    source = factory_source("pipeline")
    written = set(re.findall(r'store\.append\(\s*"([a-z_]*_failed)"', source))
    # `verify_lane_failed` has its own handling upstream and is deliberately out.
    written -= {"verify_lane_failed", "sandbox_release_failed"}
    assert written, "no failure records are written any more; this test is stale"
    unread = sorted(written - set(pipeline.AGENT_FAILURES))
    assert not unread, f"these failures are recorded and never reach a finding: {unread}"


def test_a_breaker_suite_that_failed_without_naming_a_probe_is_not_zero():
    """`failing` is populated by matching probe paths in the output. A suite that
    dies before any probe reports names none -- and the packet said the
    adversarial agent found no problems, four rounds running."""
    from factory.pipeline import breaker_findings
    from factory.schemas import BreakerReport, BreakerSuite

    report = BreakerReport(
        ran=True, exit_code=1, command="pytest tests/breaker/",
        tests_applied=["tests/breaker/test_audit_actor_downgrade.py"],
        failing=[],
        output_tail="ERROR alembic/env.py line 64 in run_migrations_online",
    )
    findings = breaker_findings(BreakerSuite(strategy="", tests=[]), report)

    assert len(findings) == 1
    assert findings[0].severity == "major"
    assert "alembic" in findings[0].evidence, "the output is the whole evidence"
    assert "either a real defect" in findings[0].detail, (
        "a broken probe and a caught defect look identical from outside; "
        "the finding must not pretend to know which")

    passed = BreakerReport(ran=True, exit_code=0, failing=[])
    assert breaker_findings(BreakerSuite(strategy="", tests=[]), passed) == []


def test_the_stats_do_not_report_zero_over_a_failed_suite():
    from factory.pipeline import compute_stats
    from factory.schemas import BreakerReport, GateReport, QAReport

    broken = BreakerReport(ran=True, exit_code=1, failing=[])
    stats = compute_stats(
        Spec(title="t", intent="i", summary="s"), [], QAReport(summary=""), GateReport(),
        [], [], [], breaker=broken)

    assert stats.breaker_failures == 0, "no probe could be named, and that stays true"
    assert stats.breaker_suite_failed, "but the suite failed, and that is now sayable"


def test_a_criterion_nothing_implements_is_never_counted_verified():
    """`criteria_verified` must ask the same three questions the review screen
    asks, or the headline and the row disagree about the same criterion.

    Verified is: something implements it, a blind test is tagged to it, and that
    test passed. Counting the pass alone let a QA row carry a criterion that
    nothing implements -- and the screen, which computes all three, drew "no
    code" on the row the headline had just counted as verified.
    """
    from factory.pipeline import compute_stats
    from factory.schemas import (
        AcceptanceCriterion, CriterionResult, GateReport, QAReport, TraceRow,
    )

    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="implemented, tested, passing"),
        AcceptanceCriterion(id="AC-2", statement="nothing implements this"),
        AcceptanceCriterion(id="AC-3", statement="implemented, no blind test"),
    ])
    trace = [
        TraceRow(criterion_id="AC-1", implementing_files=["a.py"],
                 test_names=["t.py"], status="traced"),
        TraceRow(criterion_id="AC-2", status="orphan_requirement"),
        TraceRow(criterion_id="AC-3", implementing_files=["b.py"], status="untested"),
    ]
    # Every one of them claims to have passed. Only one of them may count.
    qa = QAReport(summary="", results=[
        CriterionResult(criterion_id=cid, status="passed") for cid in ("AC-1", "AC-2", "AC-3")
    ])

    stats = compute_stats(spec, trace, qa, GateReport(), [], [], [])

    assert stats.criteria_verified == 1, (
        "AC-2 has no implementation and AC-3 has no blind test; a QA row saying "
        "passed cannot verify either, and the review screen already says so"
    )
    assert stats.criteria_total == 3


def test_a_window_reported_as_a_percentage_reads_as_a_fraction():
    from factory.meters import read_windows

    limits = json.loads(json.dumps(CODEX_RATE_LIMITS))
    limits["primary"]["resets_at"] = time.time() + 8100
    limits["secondary"]["resets_at"] = time.time() + 400000

    windows = {w.name: w for w in read_windows({"payload": {"rate_limits": limits}},
                                               _codex_route())}

    assert set(windows) == {"primary", "secondary"}, (
        "`credits` is not a window; filing it as one puts a gauge on the screen "
        "reading 0% used for something that is not a gauge")
    assert windows["secondary"].utilization == pytest.approx(0.30), "0-100 is not 0-1"
    assert not windows["secondary"].stale
    assert windows["primary"].seconds_to_reset == pytest.approx(8100, abs=5)


def test_a_countdown_is_turned_into_an_instant():
    """Kept because the tool's own binary carries both spellings, and a reading
    off by fifty-six years is not a failure worth guessing at."""
    from factory.meters import read_windows

    route = _codex_route(meter_resets_key="resets_in_seconds", meter_resets_relative=True)
    envelope = {"payload": {"rate_limits": {
        "primary": {"used_percent": 42.5, "resets_in_seconds": 8100}}}}

    window = read_windows(envelope, route)[0]
    assert not window.stale, "a countdown parsed as an epoch second lands in 1970"
    assert window.resets_at == pytest.approx(time.time() + 8100, abs=5)


def test_a_gauge_that_never_reaches_stdout_is_read_from_the_tools_own_log(tmp_path):
    """Measured on codex-cli 0.153.4: `--json` prints four event kinds and none
    of them carries the windows. They go to the session rollout, in a file named
    after the thread id that *is* on stdout."""
    from factory.meters import read_windows

    limits = json.loads(json.dumps(CODEX_RATE_LIMITS))
    limits["secondary"]["resets_at"] = time.time() + 400000
    rollout = tmp_path / "rollout-2026-09-10T14-58-38-abc123.jsonl"
    rollout.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"id": "abc123"}}),
        json.dumps({"type": "event_msg", "payload": {
            "type": "token_count", "rate_limits": limits}}),
        json.dumps({"type": "event_msg", "payload": {"type": "agent_message"}}),
    ]), encoding="utf-8")

    route = _codex_route(meter_sidecar_glob=str(tmp_path / "rollout-*-{thread_id}.jsonl"))
    # What the tool actually prints: an id, an answer, token counts, no windows.
    envelope = {"thread_id": "abc123", "item": {"text": "ok"},
                "usage": {"input_tokens": 15278, "output_tokens": 5}}

    windows = {w.name: w for w in read_windows(envelope, route)}
    assert windows["secondary"].utilization == pytest.approx(0.30)

    # And a call whose id matches no log reports nothing rather than guessing.
    assert read_windows({"thread_id": "nope"}, route) == []


def test_a_route_with_no_gauge_still_reports_none():
    """The one reading worse than none is a confident zero."""
    from factory.config import RouteConfig
    from factory.meters import read_windows

    assert read_windows({"payload": {"rate_limits": CODEX_RATE_LIMITS}},
                        RouteConfig(name="plain")) == []


def test_the_first_shape_still_reads_as_it_did():
    from factory.config import RouteConfig
    from factory.meters import read_windows

    route = RouteConfig(
        name="claude-code",
        meter_windows_key="rate_limit_info.unifiedWindows",
        meter_utilization_key="utilization",
        meter_resets_key="resetsAt",
    )
    envelope = {"rate_limit_info": {"unifiedWindows": {
        "five_hour": {"utilization": 0.21, "resetsAt": time.time() + 3600},
    }}}

    windows = read_windows(envelope, route)
    assert [w.name for w in windows] == ["five_hour"]
    assert windows[0].utilization == pytest.approx(0.21), "no scaling where none was asked for"
    assert windows[0].seconds_to_reset == pytest.approx(3600, abs=5)


def test_a_window_is_named_by_its_length_not_by_its_index():
    """"primary at 30%" answers no question anybody has. The tool's own status
    screen says "5h limit" and "Weekly limit", and the length is in the payload."""
    from factory.meters import WindowReading, read_windows

    limits = json.loads(json.dumps(CODEX_RATE_LIMITS))
    limits["primary"]["resets_at"] = time.time() + 8100
    limits["secondary"]["resets_at"] = time.time() + 400000
    windows = read_windows({"payload": {"rate_limits": limits}},
                           _codex_route(meter_window_minutes_key="window_minutes"))

    assert [w.label for w in windows] == ["5h", "weekly"], (
        "300 and 10080 minutes, as the tool itself renders them")
    assert [w.name for w in windows] == ["primary", "secondary"], "the key is still the key"
    assert windows[0].as_dict()["label"] == "5h", "and it survives to the console"

    # A tool whose windows are already named for themselves keeps its own words.
    assert WindowReading(name="five_hour").label == "five hour"
    assert WindowReading(name="x", window_minutes=1440).label == "daily"


def test_credits_are_read_and_kept_beside_the_windows(tmp_path):
    """A separate pool, and the distinction is not academic: the run that
    prompted this lost every review agent to an empty balance while both of its
    windows still had room."""
    from factory.meters import MeterStore, read_credits, read_windows

    route = _codex_route(meter_credits_key="payload.rate_limits.credits",
                         meter_window_minutes_key="window_minutes")
    limits = json.loads(json.dumps(CODEX_RATE_LIMITS))
    limits["secondary"]["resets_at"] = time.time() + 400000
    envelope = {"payload": {"rate_limits": limits}}

    credits = read_credits(envelope, route)
    assert credits["has_credits"] is False, "carried verbatim; this module knows nothing about credits"
    assert credits["balance"] is None, "and null is not zero"

    store = MeterStore(tmp_path / "meters.json")
    store.record("codex", read_windows(envelope, route), credits)

    assert store.credits("codex")["has_credits"] is False
    assert [w.label for w in store.latest("codex")] == ["5h", "weekly"], (
        "the label survives the round trip the console reads through")
    assert "credits" not in {w.name for w in store.latest("codex")}, (
        "recorded beside the windows, never as one of them")


def test_an_empty_balance_does_not_move_the_gauge():
    """The reserve this feeds must not be built on utilization alone: the pool
    emptied at 0% and 30% used, and a ceiling watching the windows would have
    sailed straight into it."""
    from factory.meters import MeterStore, read_windows

    limits = json.loads(json.dumps(CODEX_RATE_LIMITS))
    limits["primary"]["resets_at"] = time.time() + 8100
    limits["secondary"]["resets_at"] = time.time() + 400000
    windows = read_windows({"payload": {"rate_limits": limits}}, _codex_route())

    worst = max(windows, key=lambda w: w.utilization)
    assert worst.utilization == pytest.approx(0.30), (
        "the tightest window is 70% free while the account cannot spend at all")
    assert MeterStore.worst.__doc__, "and `worst` is still only about windows"


def test_a_window_that_has_reset_is_shown_as_unknown_not_dropped(tmp_path):
    """A reading from before the reset cannot be shown as a number -- it
    understates the room left. But removing the row says the plan has one limit
    where it has two, which is the worse of the two lies. It was the worse one:
    a card showed a seven-day bar alone and read as an account with no five-hour
    window at all."""
    from factory import routes as routes_module
    from factory.meters import MeterStore, WindowReading

    store = MeterStore(tmp_path / "meters.json")
    store.record("claude-code", [
        WindowReading(name="five_hour", utilization=0.42,
                      resets_at=time.time() - 11000, window_minutes=300),
        WindowReading(name="seven_day", utilization=0.35,
                      resets_at=time.time() + 145000, window_minutes=10080),
    ])

    served = {w["name"]: w for w in
              (w.as_dict() for w in store.latest("claude-code"))}
    assert set(served) == {"five_hour", "seven_day"}, "both windows exist and both are said"
    assert served["five_hour"]["stale"] is True, "and the console draws that difference"
    assert served["seven_day"]["stale"] is False

    # Asserted on what the console is served rather than on the source, because
    # filtering stale windows is correct in one place and wrong in another:
    # a decision about whether a run can finish must ignore a reading from the
    # window before last, and the card must still show that the window exists.
    from factory.config import Config, PathsConfig, RouteConfig

    cfg = Config(paths=PathsConfig(evidence=str(tmp_path), sandboxes=str(tmp_path / "s")))
    cfg.routes = {"claude-code": RouteConfig(
        name="claude-code", kind="cli", command=["true"],
        meter_windows_key="rate_limit_info.unifiedWindows")}
    served = routes_module.RouteMonitor(cfg, None).gauges()["claude-code"]["windows"]
    assert {w["name"] for w in served} == {"five_hour", "seven_day"}, (
        "dropping a stale window renders unknown as absent")
    assert [w["stale"] for w in served if w["name"] == "five_hour"] == [True]


def test_the_shipped_codex_route_can_read_its_own_gauge():
    """Config, not code: the route as shipped has to name the fields the tool
    actually emits, or the gauge is a mechanism nothing is plugged into."""
    from factory.config import load_config

    route = load_config("factory.yaml").routes["codex"]
    assert route.meter_windows_key == "payload.rate_limits"
    assert route.meter_sidecar_glob, "codex prints no windows; the log is the only source"
    assert route.meter_utilization_scale == pytest.approx(0.01), "used_percent is 0-100"
    assert route.meter_window_minutes_key == "window_minutes", "or the gauge reads 'primary'"
    assert route.meter_credits_key == "payload.rate_limits.credits", (
        "the pool that emptied and took the panel with it")


# ==========================================================================
# V-6 (INV-6) at the human boundary -- the route is computed, not chosen
# ==========================================================================


def test_the_route_is_computed_from_the_dispositions():
    repair = HumanFlag(id="H-1", text="print() in a codebase with none", disposition="repair")
    withdrawn = HumanFlag(id="H-2", text="checked, not duplication", disposition="not_a_defect")

    # Both accept, and they must not say the same thing: nothing flagged is not
    # the same review as everything flagged and withdrawn.
    assert compute_rework([]).route == "accept"
    assert compute_rework([withdrawn]).route == "accept"
    assert compute_rework([]).because != compute_rework([withdrawn]).because
    assert compute_rework([repair, withdrawn]).route == "repair"
    assert compute_rework([repair]).to_repair == ["H-1"]
    assert compute_rework([withdrawn]).dismissed == ["H-2"]


def test_a_legacy_spec_flag_no_longer_forces_gate_one():
    """`needs_spec_change` was withdrawn from what a human may choose (the
    worklist now offers only `repair` or `dismiss`), because it never changed
    the route to anything but `reopen_spec` -- the one thing this collapse
    gives up, on purpose, in exchange for two labels instead of five. A flag
    still carrying the old value, filed before the collapse, reads back
    exactly as written and folds into `dismissed` like any other non-repair
    reason, rather than forcing gate 1 open the way it used to."""
    repairs = [HumanFlag(id=f"H-{i}", text="t", disposition="repair") for i in range(1, 6)]
    reversal = HumanFlag(id="H-9", text="I answered Q-2 wrong", disposition="needs_spec_change")

    plan = compute_rework(repairs + [reversal])
    assert plan.route == "repair"
    assert plan.forced_by == []
    assert reversal.id in plan.dismissed
    assert plan.to_repair == [f.id for f in repairs]

    # Alone, it settles the review rather than reopening anything.
    assert compute_rework([reversal]).route == "accept"
    assert compute_rework([reversal]).dismissed == ["H-9"]


def test_a_repair_the_oracle_owns_rides_the_repair_route_not_a_third_one():
    """`oracle` is never something a human picked -- the worklist offers two
    buttons and this is not one of them. It is a repair the arbiter moved,
    because the file that has to change is one a repairer may not write. So
    it counts as work on the route it already had: the dispatch is the same
    dispatch, and nothing about it reads as a dismissal."""
    moved = HumanFlag(id="H-1", text="the assertion inverts the contract",
                      disposition="oracle")
    repair = HumanFlag(id="H-2", text="print() left in", disposition="repair")

    alone = compute_rework([moved])
    assert alone.route == "repair"
    assert alone.to_oracle == ["H-1"]
    assert alone.dismissed == []

    both = compute_rework([moved, repair])
    assert both.route == "repair"
    assert (both.to_repair, both.to_oracle) == (["H-2"], ["H-1"])
    # Counted as two flags going to the loop, with the split said afterwards:
    # what a human decided is the headline, who does the work is the detail.
    assert both.because.startswith("2 flags routed to the repair loop")
    assert "only the oracle may write" in both.because


def test_retagging_is_a_new_record_not_an_edit():
    """The store has no update path, so a retag is a second record and the first
    one survives. What you first called something is evidence too. (INV-2)"""
    records = [
        {"kind": "flag", "payload": HumanFlag(
            id="H-1", text="I answered Q-2 wrong", disposition="needs_spec_change",
        ).model_dump(mode="json")},
        {"kind": "flag_retag", "payload": {"flag_id": "H-1", "disposition": "repair"}},
    ]
    folded = human_flags(records)
    assert [f.disposition for f in folded] == ["repair"]
    # The original wording is untouched by the retag.
    assert folded[0].text == "I answered Q-2 wrong"
    # And the first record is still there to read.
    assert records[0]["payload"]["disposition"] == "needs_spec_change"


def test_a_human_flag_enters_the_ledger_already_routed():
    ledger = FindingLedger()
    seed_flags(ledger, [
        HumanFlag(id="H-1", text="Not like this. A downgrade() cannot restore 40k rows.",
                  anchor="0043.py:18", disposition="repair", severity="blocker"),
        HumanFlag(id="H-2", text="Checked it, not duplication.", disposition="not_a_defect"),
    ], 0)

    assert sorted(ledger.records) == ["H-1", "H-2"]
    assert ledger.records["H-1"].disposition == "repair"
    assert ledger.records["H-1"].role == "human"
    # A withdrawn flag is still in the ledger: rework only adds. (INV-11)
    assert ledger.records["H-2"].disposition == "not_a_defect"
    # The title is one line; the paragraph survives whole in the detail.
    assert ledger.findings["H-1"].title == "Not like this."
    assert "40k rows" in ledger.findings["H-1"].detail
    assert ledger.records["H-1"].disposition_reason.startswith("Raised and routed by the human")


def test_the_arbiter_may_move_a_human_repair_to_the_oracle_and_nothing_else():
    """The one thing a model may do to a person's flag.

    Which agent fixes something is arithmetic about who owns the file, and a
    person cannot be asked to know that the blind tests are the one tree a
    repairer may not write in: "this assertion is backwards", filed as a
    repair, went to an agent forbidden to touch the file and came back
    untouched. So `oracle` is accepted over a human's repair -- and nothing
    else is, because every other route is a ruling on whether the thing is a
    defect, which was the person's to make and is made."""
    flags = [
        HumanFlag(id="H-1", text="This asserts the opposite of AC-3.",
                  anchor="test_labels_add.py:31", disposition="repair"),
        HumanFlag(id="H-2", text="The endpoint 500s on a dot-only tag.", disposition="repair"),
        HumanFlag(id="H-3", text="Slow, but fine.", disposition="dismissed"),
    ]
    ledger = FindingLedger()
    seed_flags(ledger, flags, 0)

    ledger.dispose([
        FindingDisposition(finding_id="H-1", disposition="oracle",
                           reason="the blind test asserts what AC-3 never promised"),
        # Every one of these is an attempt to overturn the person.
        FindingDisposition(finding_id="H-2", disposition="not_a_defect", reason="works for me"),
        FindingDisposition(finding_id="H-3", disposition="repair", reason="looks real to me"),
    ])

    assert ledger.records["H-1"].disposition == "oracle"
    assert ledger.records["H-2"].disposition == "repair"
    assert ledger.records["H-3"].disposition == "dismissed"

    # And it lands in the queue the oracle's fix session reads from, not the
    # one the repairers read from.
    assert ledger.for_oracle(2) == ["H-1"]
    assert ledger.repairable(2) == ["H-2"]
    # Said in the packet as what it is, rather than looking like the arbiter
    # ruling on a person's finding.
    assert ledger.records["H-1"].disposition_reason.startswith(
        "The file that has to change is one only the oracle may write")


def test_a_human_finding_is_not_marked_a_duplicate_of_something_else():
    """The move to the oracle is the only thing that may happen to a flag. A
    duplicate is not routed, attempted or re-checked on its own, so allowing
    one here would be a way to retire a person's finding without answering
    it."""
    ledger = FindingLedger()
    seed_flags(ledger, [
        HumanFlag(id="H-1", text="Tags with a dot cannot be removed.", disposition="repair"),
    ], 0)
    ledger.dispose([FindingDisposition(
        finding_id="H-1", disposition="oracle", reason="same as F-4",
        duplicate_of="F-4")])

    assert ledger.records["H-1"].duplicate_of == ""
    assert ledger.for_oracle(2) == ["H-1"]


def test_priority_orders_the_round_and_never_empties_it():
    """A person's flags go first. That is all priority may do.

    For one afternoon it was a handover -- the round got the human's targets
    and their same-file neighbours and nothing else -- and a round with four
    unit-slots ran one, because two of the three human targets could not be
    fixed that round. A NUL byte crashing the API, found independently by
    seven agents, was never offered to anybody. Everything repairable still
    reaches the spec writer; what changes is the order it reads in."""
    ledger = FindingLedger()
    ledger.add("reviewer", [
        Finding(id="r-1", severity="minor", title="a nitpick elsewhere", detail="d"),
        Finding(id="r-2", severity="blocker", title="crashes on NUL", detail="d"),
        Finding(id="r-3", severity="blocker", title="also a blocker", detail="d"),
    ], 0, keep_ids=True)
    seed_flags(ledger, [
        HumanFlag(id="H-1", text="this one matters to me", disposition="repair",
                  severity="minor"),
    ], 0)
    ledger.dispose([
        FindingDisposition(finding_id=f, disposition="repair", reason="fix")
        for f in ("r-1", "r-2", "r-3")
    ])
    # Two readings agreed on r-3; only one raised r-2.
    ledger.records["r-3"].corroborated_by = ["hacker-9"]

    targets = ledger.human_first(ledger.repairable(2))

    # Nothing is dropped. This is the whole point.
    assert sorted(targets) == ["H-1", "r-1", "r-2", "r-3"]
    # The person first, even at `minor`, then severity, then corroboration.
    assert targets == ["H-1", "r-3", "r-2", "r-1"]


def test_ordering_targets_is_stable_and_survives_an_unknown_finding():
    """The order must not depend on what the ledger happened to list first,
    and a target with no record must not throw the round away."""
    ledger = FindingLedger()
    ledger.add("reviewer", [
        Finding(id="r-1", severity="major", title="a", detail="d"),
        Finding(id="r-2", severity="major", title="b", detail="d"),
    ], 0, keep_ids=True)
    ledger.dispose([
        FindingDisposition(finding_id=f, disposition="repair", reason="fix")
        for f in ("r-1", "r-2")
    ])
    assert ledger.human_first(["r-2", "r-1"]) == ["r-1", "r-2"]
    assert ledger.human_first(["r-2", "ghost", "r-1"]) == ["r-1", "r-2", "ghost"]


def test_a_ruling_with_no_note_does_not_put_the_machine_s_words_in_your_mouth():
    """Ruling a call without typing anything used to file the finding's own
    title as the human's text (`note.trim() || f.title`, worklist.js).

    Two things went wrong with that. The review screen listed it back under
    "your comments", so a sentence a model wrote was shown to the person as
    the sentence they had written. And the ledger gained a human finding whose
    title and detail were a copy of one already in it, which is not what
    "rework only adds" is for.

    The flag now records the truth -- nothing -- and the ledger entry borrows
    the anchored finding's words, saying in the disposition reason whose they
    are. Nothing is invented, and the repair loop still has something named to
    work on.
    """
    ledger = FindingLedger()
    seam, tags = ledger.add("adversary", [
        Finding(id="x", title="A check across the seams fails", severity="blocker",
                detail="tests/test_seam.py fails at the boundary between the units."),
        Finding(id="y", title="Removing two tags makes one reappear", severity="major",
                detail="Two DELETEs 40ms apart: the second carries the pre-delete list."),
    ], 0)

    # One ruling per call, which is all the console can file: the row settles
    # the moment it is ruled.
    seed_flags(ledger, [
        HumanFlag(id="H-1", text="", anchor=seam, disposition="repair"),
        HumanFlag(id="H-2", text="   ", anchor=tags, disposition="repair"),
        HumanFlag(id="H-3", text="Serialise the deletes.", anchor=tags, disposition="repair"),
    ], 0)

    # Not blank, and not invented: the anchored finding's own words, whole.
    assert ledger.findings["H-1"].title == "A check across the seams fails"
    assert ledger.findings["H-1"].detail == ledger.findings[seam].detail
    # Whitespace is not a note either.
    assert ledger.findings["H-2"].title == "Removing two tags makes one reappear"
    # And the record says they are not the human's words.
    assert "no note of their own" in ledger.records["H-1"].disposition_reason
    assert seam in ledger.records["H-1"].disposition_reason

    # A flag that does carry words is untouched by any of this.
    assert ledger.findings["H-3"].title == "Serialise the deletes."
    assert ledger.records["H-3"].disposition_reason.startswith("Raised and routed by the human")

    # The console sends the empty string rather than the fallback it used to.
    work = (ROOT / "console" / "ui" / "worklist.js").read_text(encoding="utf-8")
    assert "text: note.trim()," in work and "note.trim() || f.title" not in work, \
        "a ruling with no note still files the finding's title as the human's text"
    # And every screen that prints a flag's words says so when there are none.
    shared = (ROOT / "console" / "ui" / "shared.js").read_text(encoding="utf-8")
    assert "export const NO_NOTE" in shared and "export const noteOf" in shared
    for name in ("rework.js", "worklist.js"):
        src = (ROOT / "console" / "ui" / name).read_text(encoding="utf-8")
        assert "NO_NOTE" in src, f"{name} prints a flag's text without handling an empty one"


def test_a_flag_filed_before_the_fix_is_read_for_what_it_is():
    """Every packet written before the fix above carries flags whose text IS
    the anchored finding's title, because that is what the console filed when
    you ruled a call without typing anything.

    This one is real, off `card-tags-8e21a7`: flag H-3 on `adversary-12`,
    whose stored text is that finding's title character for character. The
    record is append-only and nothing rewrites it -- but nothing should show
    a model's sentence back to the person as the sentence they wrote, so the
    old shape is recognised wherever a flag's words are read.
    """
    title = "Some whitespace-wrapped tags render twice after a successful save"
    ledger = FindingLedger()
    [raised] = ledger.add("adversary", [Finding(
        id="x", title=title, severity="major",
        detail="Two saves 40ms apart leave the second render standing.")], 0)

    seed_flags(ledger, [
        HumanFlag(id="H-3", text=title, anchor=raised, disposition="dismissed"),
    ], 0)

    # Read as the routing decision it was, not as a note.
    assert "no note of their own" in ledger.records["H-3"].disposition_reason
    # And the ledger entry carries the finding's detail, not its title twice.
    assert ledger.findings["H-3"].detail == ledger.findings[raised].detail

    # The same rule, in the console, in one place both screens read through.
    shared = (ROOT / "console" / "ui" / "shared.js").read_text(encoding="utf-8")
    body = shared[shared.index("export const noteOf"):]
    assert "titleOf(flag.anchor)" in body, \
        "the console cannot tell a note from the title it was filed in place of"
    for name in ("rework.js", "worklist.js"):
        src = (ROOT / "console" / "ui" / name).read_text(encoding="utf-8")
        assert "noteOf(" in src and "titleOf" in src, \
            f"{name} reads a flag's words without resolving its anchor"


def test_a_flag_with_no_note_and_no_finding_to_borrow_from_is_still_named():
    """Nothing in the console can file one -- the composer will not submit an
    empty box, and a ruling always carries its finding's id. A packet written
    by an older build can, and a finding with no title is one the repair loop
    cannot act on and no screen can list."""
    ledger = FindingLedger()
    seed_flags(ledger, [HumanFlag(id="H-1", text="", disposition="repair")], 0)

    assert ledger.findings["H-1"].title
    assert ledger.findings["H-1"].detail == ledger.findings["H-1"].title
    assert "no note" in ledger.records["H-1"].disposition_reason


def test_the_arbiter_cannot_re_route_what_a_human_raised():
    """The arbiter routes what models raise. A person's finding arrives already
    decided, and a model that could overturn it would be the tool overruling
    its user."""
    ledger = FindingLedger()
    seed_flags(ledger, [
        HumanFlag(id="H-1", text="fix this", disposition="repair"),
    ], 0)
    ledger.add("reviewer", [Finding(id="x", title="something else", severity="minor",
                                    detail="d")], 0)

    ledger.dispose([
        FindingDisposition(finding_id="H-1", disposition="not_a_defect",
                           reason="I, a model, disagree"),
        FindingDisposition(finding_id="reviewer-1", disposition="not_a_defect",
                           reason="fine on a model's own finding"),
    ])

    assert ledger.records["H-1"].disposition == "repair"
    assert "I, a model, disagree" not in ledger.records["H-1"].disposition_reason
    assert ledger.records["reviewer-1"].disposition == "not_a_defect"


def test_a_retag_moves_an_open_flag_but_not_a_settled_one():
    ledger = FindingLedger()
    seed_flags(ledger, [HumanFlag(id="H-1", text="fix this", disposition="repair")], 0)
    seed_flags(ledger, [HumanFlag(id="H-2", text="and this", disposition="repair")], 0)
    ledger.records["H-2"].outcome = "repaired"

    seed_flags(ledger, [
        HumanFlag(id="H-1", text="fix this", disposition="not_a_defect"),
        HumanFlag(id="H-2", text="and this", disposition="not_a_defect"),
    ], 1)

    assert ledger.records["H-1"].disposition == "not_a_defect"
    # Already repaired. History does not take instructions.
    assert ledger.records["H-2"].disposition == "repair"


def test_out_of_spec_flags_leave_rather_than_forcing_anything():
    """`out_of_spec` -- real, but not this feature's job -- was one of the four
    choices that collapsed into `dismissed`. It never forced anything even
    before the collapse; a flag still carrying it reads back the same way any
    dismissed flag does."""
    plan = compute_rework([
        HumanFlag(id="H-1", text="a default window is a policy question",
                  disposition="out_of_spec"),
    ])
    assert plan.route == "accept"
    assert plan.dismissed == ["H-1"]


# ==========================================================================
# V-4 (INV-4) -- the rapporteur presents; it cannot omit
# ==========================================================================


def test_v4_dropped_decisions_findings_and_files_are_restored():
    decisions = [
        Decision(id="U-1/D-1", title="kept", rationale="r"),
        Decision(id="U-1/D-2", title="dropped", rationale="r"),
        Decision(id="U-2/D-1", title="also kept", rationale="r"),
        Decision(id="U-2/D-2", title="also dropped", rationale="r"),
    ]
    findings = [
        Finding(id="F-1", severity="blocker", title="kept finding", detail="d"),
        Finding(id="F-2", severity="minor", title="inconvenient finding", detail="d"),
    ]
    files = [
        FileWrite(path="a.py", contents="one\ntwo\n"),
        FileWrite(path="b.py", contents="one\ntwo\nthree\n"),
    ]

    presented = Packet(
        headline="looks fine",
        decisions=[decisions[0].model_copy(update={"rank": 1}),
                   decisions[2].model_copy(update={"rank": 2})],
        findings=[findings[0]],
        materiality=[],
    )
    presented.materiality.append(
        __import__("factory.schemas", fromlist=["MaterialityItem"]).MaterialityItem(
            path="a.py", klass="conventional", lines=2, reason="the obvious way"
        )
    )

    restored = restore_packet(
        presented, decisions=decisions, findings=findings, files=files, trace=[],
    )

    assert {d.id for d in restored.decisions} == {d.id for d in decisions}, "a dropped decision came back"
    assert {f.title for f in restored.findings} == {f.title for f in findings}
    assert {m.path for m in restored.materiality} == {"a.py", "b.py"}

    unclassified = next(m for m in restored.materiality if m.path == "b.py")
    assert unclassified.klass == "novel", "an unclassified file must default to novel, never to safe"
    assert unclassified.lines == 3
    assert unclassified.reason

    # The classification the rapporteur did make is left alone.
    classified = next(m for m in restored.materiality if m.path == "a.py")
    assert classified.klass == "conventional"

    # Restored decisions sort after the ranked ones rather than displacing them.
    assert [d.id for d in restored.decisions][:2] == ["U-1/D-1", "U-2/D-1"]


def test_v5_schema_failure_is_retried_then_succeeds(monkeypatch):
    client = LLM(_stub_config())
    calls: list[dict] = []
    payloads = [
        _response("not json at all, sorry"),
        _response('{"summary": '),
        _response('```json\n{"summary": "ok", "stack": [], "conventions": [], '
                  '"existing_capabilities": [], "do_not_duplicate": [], "relevant_files": [], "risks": []}\n```'),
    ]

    async def fake_post(payload):
        calls.append(payload)
        return payloads[len(calls) - 1]

    monkeypatch.setattr(client, "_post", fake_post)

    from factory.schemas import ScoutReport
    result = asyncio.run(client.ask("scout", "look around", ScoutReport))

    assert isinstance(result, ScoutReport)
    assert result.summary == "ok"
    assert len(calls) == 3, "it must retry, not accept the first malformed body"

    repair = calls[-1]["messages"][-1]["content"]
    assert "did not validate" in repair, "the validation error has to go back to the model"
    assert client.usage["scout"].calls == 3


def test_v5_three_failures_raise_loudly(monkeypatch):
    client = LLM(_stub_config())
    calls = []

    async def fake_post(payload):
        calls.append(payload)
        return _response("still not json")

    monkeypatch.setattr(client, "_post", fake_post)

    from factory.schemas import ScoutReport
    with pytest.raises(LLMError) as caught:
        asyncio.run(client.ask("scout", "look around", ScoutReport))

    assert len(calls) == 3, "exactly three attempts, then fail"
    assert "ScoutReport" in str(caught.value)


def test_v5_fences_and_surrounding_prose_are_stripped():
    extracted = llm.extract_json('Sure! Here you go:\n```json\n{"a": {"b": "}"}}\n```\nHope that helps.')
    assert extracted == '{"a": {"b": "}"}}'


# ==========================================================================
# V-6 (INV-7) -- model identifiers appear in exactly one file
# ==========================================================================


def test_the_example_config_ships_the_routes_it_documents():
    """`factory.yaml` is gitignored, so the example is the only route config a
    clone gets. It had none at all -- the file predated routes -- which meant
    the gauge machinery shipped with nothing to drive it: every meter key lived
    on one laptop.
    """
    config = load_config(ROOT / "factory.example.yaml")
    cli = {name for name, route in config.routes.items() if route.kind == "cli"}
    assert cli, "a clone gets no CLI routes, so no subscription can carry an agent"
    # Naming a route must not switch it on. Every role here uses `default`, so
    # the file behaves as it did before routes existed until somebody opts in.
    assert not [r for r in config.roles.values() if r.route], (
        "the example must not point roles at a tool the reader may not have installed")


def test_every_config_section_is_actually_read_from_the_file():
    """A section `Config` declares but `load_config` never passes through is a
    setting that cannot be set: it documents a knob, accepts a value, and
    silently keeps the default.

    Found the hard way -- `scheduling:` was added to the model and to the
    example, and bending `margin` to 9.5 in the file still produced 1.25.
    """
    import re

    from factory.config import Config

    source = factory_source("config")
    body = re.search(r"cfg = Config\((.*?)\n    \)", source, re.S)
    assert body, "load_config no longer builds Config in one call; this test needs rewriting"
    wired = set(re.findall(r"(\w+)=", body.group(1)))
    # Synthesised rather than read: `default_route` is built from api+executor,
    # and `source_path` is where the file was found.
    declared = set(Config.model_fields) - {"source_path", "default_route"}
    missing = sorted(declared - wired)
    assert not missing, f"these sections are declared and never read from the file: {missing}"


def test_the_example_documents_the_scheduling_knobs():
    """Defaults make it work; the file is what makes it tunable. Every value
    here is reachable and none of them changes behaviour on a fresh checkout."""
    import yaml as _yaml

    raw = _yaml.safe_load((ROOT / "factory.example.yaml").read_text(encoding="utf-8"))
    block = raw.get("scheduling") or {}
    assert set(block) == {"enabled", "margin", "max_wait_s", "min_observations"}, (
        "the example must document every knob, and no knob that does not exist")

    shipped = load_config(ROOT / "factory.example.yaml").scheduling
    assert shipped == config_module.SchedulingConfig(), (
        "the documented values are the defaults: reading this file must not "
        "change what a fresh checkout does")


def test_a_half_declared_gauge_is_not_a_gauge():
    """A route that says where its windows are and not how to read them reports
    nothing, and reports nothing in the way that looks like a plan with no
    limits rather than like a config that is missing a line."""
    config = load_config(ROOT / "factory.example.yaml")
    for name, route in config.routes.items():
        if not route.meter_windows_key:
            continue
        assert route.meter_utilization_key, f"{name} declares windows and no utilization key"
        assert route.meter_resets_key, f"{name} declares windows and no reset key"
        if route.meter_sidecar_glob:
            assert "{" in route.meter_sidecar_glob, (
                f"{name}'s sidecar pattern names no field to fill in from the call")


def test_v6_no_model_names_outside_the_config():
    # sandbox.py, projects.py and onboarding.py are covered by the rglob below.
    needles = ("claude-", "qwen", "gpt-", "glm-", "deepseek", "gemini", "llama", "mistral")
    searched = []
    for path in [*PACKAGE.rglob("*"), ROOT / "seed.py", ROOT / "README.md",
                 *(ROOT / "docs").glob("*.md"), *(ROOT / "console").rglob("*")]:
        if not path.is_file() or path.suffix not in (".py", ".md", ".js", ".css", ".html", ".yaml"):
            continue
        if path.name == "factory.example.yaml":
            continue
        searched.append(path)
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        # A file named for an agent product is not a model identifier: the
        # guides look for GEMINI.md the way they look for AGENTS.md.
        text = text.replace("gemini.md", "")
        for needle in needles:
            assert needle not in text, (
                f"{path.relative_to(ROOT)} contains {needle!r}. Model identifiers belong in the "
                "config file and nowhere else; a name here is a fallback waiting to happen."
            )
    assert len(searched) > 15, "the search found suspiciously few files"


def test_v6_a_missing_role_raises_rather_than_falling_back():
    config = _stub_config()
    with pytest.raises(Exception) as caught:
        config.role("adversary")
    assert "adversary" in str(caught.value)
    assert "fallback" in str(caught.value).lower()


# ==========================================================================
# V-7 (INV-10) -- prompts are versioned files
# ==========================================================================


def test_v7_every_role_the_pipeline_uses_has_a_prompt_file():
    source = "\n".join(
        factory_source(name) for name in ("pipeline", "onboarding", "asbuilt")
    )
    # Two different things wear a role's name, and only one of them needs a
    # file. `role_prompt("x")` names the prompt; `llm.ask("x")` and
    # `config.role("x")` name a model, a route and a budget. They were assumed
    # to be the same name, which is true right up until two readings share one
    # set of rules: `resurvey` bills as itself and is instructed by
    # `surveyor.md`, because the same rule written in two files is the drift
    # this invariant exists to make impossible, not to require.
    prompts, billed = set(), set()
    for pattern in (r'self\.role_prompt\(\s*"([a-z_]+)"',
                    r'self\.config\.role_prompt\(\s*"([a-z_]+)"'):
        prompts.update(re.findall(pattern, source))
    for pattern in (r'self\.llm\.ask\(\s*"([a-z_]+)"',
                    r'self\.config\.role\(\s*"([a-z_]+)"'):
        billed.update(re.findall(pattern, source))

    assert prompts and billed, "no roles found -- the pattern is wrong, not the code"

    # Resolved rather than assumed. A role may work from another's prompt --
    # `roles.<name>.prompt` says so -- and asking `prompt_path` is how every
    # other reader answers this. Rebuilding the convention here is how the crew
    # screen came to report a shared prompt as a missing one.
    from factory.config import load_config, prompt_path
    config = load_config(ROOT / "factory.example.yaml")
    missing = sorted(r for r in prompts if not prompt_path(config, r).exists())
    assert not missing, (
        f"prompts loaded by the pipeline with no file: {missing}. Prompts are versioned "
        "files so that when output quality shifts you can diff the prompt."
    )

    for role in sorted(prompts):
        body = prompt_path(config, role).read_text(encoding="utf-8")
        assert len(body) > 400, f"the prompt for {role!r} is a stub, not an instruction"

    # A name that is billed still has to be a role somebody configured, or the
    # call fails at run time with no model rather than here.
    unconfigured = sorted(r for r in billed if r not in config.roles)
    assert not unconfigured, f"roles billed by the pipeline with no config block: {unconfigured}"

    assert {"oracle", "surveyor", "scout"} <= prompts


def test_v7_no_prompt_text_is_inlined_in_the_pipeline():
    source = factory_source("pipeline")
    assert "You are the" not in source and "you must" not in source.lower(), \
        "prompt prose is drifting into pipeline.py; it belongs in roles/*.md"


def test_a_port_already_served_here_does_not_take_the_door_down():
    """Library's API listens on every address, as Spring Boot does by default, so
    the inner hop could not listen on the API's port -- and treating that as
    fatal took every port down with it, including the web page served on
    loopback. The door accepted each connection and closed it, "Remote end
    closed connection without response", on main and on the branch alike. A
    port already served where the hop listens needs no forwarding; the rest
    still do."""
    import socket

    from factory import doorway

    def free() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    async def go():
        # Something in this namespace already listens where the hop would.
        taken = socket.socket()
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        busy = taken.getsockname()[1]
        wanted = free()
        servers = await doorway.serve("127.0.0.1", "127.0.0.1", [busy, wanted])
        bound = [srv.sockets[0].getsockname()[1] for srv in servers]
        for srv in servers:
            srv.close()
        taken.close()
        return busy, wanted, bound

    busy, wanted, bound = asyncio.run(go())
    assert bound == [wanted], "the port already served is skipped, the other still forwarded"
    assert busy not in bound

    source = Path(doorway.__file__).read_text(encoding="utf-8")
    assert "errno.EADDRINUSE" in source and "asyncio.Event().wait()" in source, \
        "and the hop stays up with nothing to forward, rather than reading as a broken door"
