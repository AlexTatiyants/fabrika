"""The as-built: the codebase as it was built, read file by file.

Split out of test_invariants.py, which keeps one test per invariant.
"""

from helpers import *  # noqa: F403


def test_a_skipped_blind_test_is_a_known_and_stated_blind_spot():
    """What running one command per file cannot see, recorded rather than hidden.

    A file whose tests were skipped exits zero exactly as a file whose tests
    passed. There is no way to tell them apart from an exit code, and nothing
    reads runner output any more, so this system no longer detects a skipped
    blind test. That is a real loss against the version that scraped for the
    word SKIPPED -- and that version was also the one that reported two erroring
    criteria as passed, because the same scraping is what could not be trusted.

    Two things bound the exposure, and this pins both. The oracle is instructed
    never to skip or `xfail` a criterion. And what a runner does when handed
    nothing is measured against this project's own command at gate 0, so a
    project whose runner exits zero over an empty selection is told so instead
    of being assumed safe.
    """
    oracle = (ROOT / "factory" / "roles" / "oracle.md").read_text(encoding="utf-8")
    assert "Do not soften, skip, or `xfail` a criterion" in " ".join(oracle.split()), \
        "nothing stops the oracle skipping a criterion, and nothing else would catch it"

    findings = pipeline.check_blind_attribution(
        gates.GateReport(results=[_reported("blind-tests", passed=True, total=2,
                                            n_passed=2)]),
        empty_run_detected=False,
    )
    assert [f.id for f in findings] == ["attribution-2"]
    assert "reports success even when there are no tests to run" in findings[0].title
    assert "measured at gate 0" in findings[0].detail.lower(), \
        "the blind spot is asserted rather than measured, which is the habit this replaced"

    # Measured as safe, or a suite run file by file: nothing to report.
    assert pipeline.check_blind_attribution(
        gates.GateReport(results=[_reported("blind-tests", passed=True, total=2,
                                            n_passed=2)]),
        empty_run_detected=True) == []


def test_a_file_that_lost_its_contents_is_a_blocker_not_a_refusal(tmp_path):
    from factory.config import Config

    big = "".join(f"def endpoint_{i}():\n    return {i}\n" for i in range(200))
    sandbox = _branch(
        tmp_path,
        {"routers/queue.py": big, "models/trial.py": "class Trial:\n    pass\n"},
        # Reconstructed: a fraction of the original, and the rest went nowhere.
        {"routers/queue.py": "".join(f"def endpoint_{i}():\n    return {i}\n" for i in range(10)),
         "models/trial.py": "class Trial:\n    slots = 0\n"},
    )
    findings = pipeline.check_destructive_writes(sandbox, Config())

    assert [f.severity for f in findings] == ["blocker"]
    assert findings[0].files == ["routers/queue.py"]
    assert "does not appear anywhere else" in findings[0].title
    assert "380 of 400 lines" in findings[0].title
    assert "git diff" in findings[0].evidence, "the human is not told how to see it"
    assert findings[0].category == "destruction"


def test_a_feature_that_changes_what_a_check_looks_for_is_a_blocker(tmp_path):
    sandbox = _branch(
        tmp_path,
        {"ruff.toml": "line-length = 88\n", "app.py": "x = 1\n"},
        {"ruff.toml": "line-length = 200\n", "app.py": "x = 2\n"},
    )
    findings = pipeline.check_settings_changed(sandbox, _checks_with_settings())

    assert [f.severity for f in findings] == ["blocker"]
    assert findings[0].files == ["ruff.toml"]
    assert "lint" in findings[0].title
    assert "git diff" in findings[0].evidence, "the human is not told how to see it"


def test_a_change_inside_the_named_section_is_reported(tmp_path):
    base = {"pyproject.toml": '[project]\nname = "a"\n\n[tool.ruff]\nselect = ["E", "F"]\n'}
    loosened = {"pyproject.toml": '[project]\nname = "a"\n\n[tool.ruff]\nselect = ["E"]\n'}
    sandbox = _branch(tmp_path, base, loosened)
    findings = pipeline.check_settings_changed(sandbox, _checks_with_settings())
    assert [f.severity for f in findings] == ["blocker"]
    assert "section tool.ruff" in findings[0].evidence


def test_a_settings_file_that_no_longer_parses_is_reported_not_skipped(tmp_path):
    base = {"pyproject.toml": '[tool.ruff]\nselect = ["E"]\n'}
    broken = {"pyproject.toml": '[tool.ruff\nselect = ["E"]\n'}
    sandbox = _branch(tmp_path, base, broken)
    findings = pipeline.check_settings_changed(sandbox, _checks_with_settings())
    assert findings and "no longer parses" in findings[0].evidence, \
        "a file nobody can read was taken to mean its settings had not changed"


def test_a_new_suppression_is_reported_beside_the_check_it_silences(tmp_path):
    sandbox = _branch(
        tmp_path,
        {"app.py": "import os  # noqa\nx = 1\n", "web.js": "let a = 1;\n"},
        {"app.py": "import os  # noqa\nx = 1\nimport sys  # noqa\n",
         "web.js": "let a = 1;\n// eslint-disable-next-line\nlet b = 2;\n",
         "notes.py": "y = 2  # type: ignore\n"},
    )
    findings = {f.id: f for f in pipeline.check_new_suppressions(sandbox, _checks_with_settings())}

    assert set(findings) == {"suppressed-lint", "suppressed-js-lint"}, \
        "a marker no approved check declares was reported, or a declared one was missed"
    assert all(f.severity == "major" for f in findings.values())
    lint = findings["suppressed-lint"]
    assert "app.py:3:" in lint.evidence
    assert "app.py:1:" not in lint.evidence, "a suppression already on the base was reported as new"
    assert lint.files == ["app.py"]


def test_the_whole_suite_reaches_disk_and_the_protected_set():
    """`.tests` is the asserting half. Five sites mean the whole suite.

    A suite whose shared setup never lands runs nothing, and one whose setup is
    off the verification surface is INV-12 with a hole in it -- the repair loop
    could rewrite the file that decides whether the tests run, and the suite
    would still pass afterwards. Asserted on the source because both failures
    are silent.
    """
    converge = inspect.getsource(pipeline.Factory._converge)
    assert "protected_paths(" in converge
    assert "extra=[t.path for t in oracle_files(oracle)]" in converge, \
        "the support files are off the verification surface"
    # Written to disk and protected as a whole suite; *invoked* as tests only.
    # The suite needs its conftest to run at all, and a repairer must not be
    # able to edit it -- but running a conftest as though it were a test file
    # would fail for reasons that say nothing about the feature.
    assert "witness=[t.path for t in oracle.tests]" in converge, \
        "support files are being invoked one at a time as though they asserted"

    build = inspect.getsource(pipeline.Factory._build)
    assert "purpose=\"blind acceptance test\"" in build
    assert "for t in oracle_files(oracle)" in build, \
        "the support files never reach disk, so the suite runs without them"
    assert "place_blind_file(" in build
    assert "oracle.tests = [t for t in oracle.tests if _route(t, asserts=True)]" in build \
        and "oracle.support = [f for f in oracle.support if _route(f, asserts=False)]" in build, \
        "a support file escapes confinement while the tests are relocated"


def test_a_run_the_budget_could_not_measure_says_so_in_a_finding():
    """A packet reporting "$0.00 of $10.00" over a run that did every unit of
    work on a plan reads as a cheap run, and a reader deciding whether there is
    room for another attempt draws exactly the wrong conclusion."""
    from factory.config import load_config

    cfg = load_config(str(Path("factory.yaml")))
    findings = pipeline.check_budget_coverage({
        "unbilled_roles": ["worker", "spec_writer"], "unbilled_turns": 42,
        "notional_usd": 3.21, "billed_usd": 0.05, "routes": ["claude-code"],
    }, cfg)
    assert len(findings) == 1
    assert findings[0].category == "budget"
    assert "the budget can't measure" in findings[0].title
    assert "the ceiling never applied to them" in findings[0].detail
    # And it must not read as an alarm: nothing here went wrong.
    assert findings[0].severity in ("minor", "nit")
    assert "Nothing is necessarily wrong" in findings[0].detail


def test_a_run_starts_outside_any_repair_round():
    """The round counter still held the round the last run ended on, and every
    step a new run reused was recorded as that round's. The closing flag did
    too: a rebuild at `architect` read "Closing · first pass"."""
    build = inspect.getsource(pipeline.Factory._build)
    assert build.index("state.rework_round, state.rework_closing = 0, False") \
        < build.index("async def reused(")
    assert {"dispatch", "rebuild", "revalidate"} <= server.INLINE_LOG_KINDS, \
        "the records that start a run travel with the log, so each run can be named"


def test_as_built_groups_are_the_same_on_every_reading():
    """Fabrika's own call graph came back as ten parts one time and nine the
    next, read in a different order. Groups that move when nothing moved are a
    map nobody can learn. Clustering visits in sorted order, and a refresh
    starts from the last reading's groups."""
    edges = {("a", "b"): 3.0, ("b", "c"): 3.0, ("a", "c"): 2.0, ("d", "e"): 3.0, ("e", "f"): 3.0,
             ("d", "f"): 2.0, ("c", "d"): 0.5}
    first = asbuilt_graph.louvain("fedcba", edges)
    shuffled = asbuilt_graph.louvain("abcdef", dict(reversed(list(edges.items()))))
    assert first == shuffled
    assert first["a"] == first["b"] == first["c"] != first["d"] == first["e"] == first["f"]
    # A seed that already agrees with the graph is kept, label for label.
    assert asbuilt_graph.louvain("abcdef", edges, seed=first) == first


def test_a_big_file_is_shown_in_parts_with_its_seams_counted():
    """A file too big to be one node is split into groups of its functions that
    call each other more than the rest -- and the calls across each boundary
    are counted, because those counts are where the file comes apart."""
    rows, fns = [], []
    for group in ("load", "save"):
        names = [f"{group}_{i}" for i in range(5)]
        for i, n in enumerate(names):
            fns.append(dict(name=n, line=len(rows) + 1, calls=[names[(i + 1) % 5], names[(i + 2) % 5]]))
            rows += [f"def {n}():"] + ["    pass"] * 199
    fns[0]["calls"].append("save_0")
    text = "\n".join(rows) + "\n"
    src = {"big.py": asbuilt_graph.Source(path="big.py", blob="b", text=text)}
    ab = asbuilt_graph.build(src, {"big.py": FileRecord(purpose="big", functions=fns)})
    parts = sorted(ab.parts, key=lambda p: p.key)
    assert [sorted(p.functions) for p in parts] == [[f"load_{i}" for i in range(5)], [f"save_{i}" for i in range(5)]]
    assert ab.file("big.py").parts
    assert [(l.source, l.target, l.calls) for l in ab.part_links] == [(parts[0].key, parts[1].key, 1)]
    assert any(f.kind == "seams" for f in ab.findings)


def test_a_file_s_source_carries_what_the_reading_knows_at_its_lines(tmp_path):
    """The source is the very blob the reading read, so every note lines up.
    A function carries the capabilities that run it and who calls it, tests
    included; a link sits on the line that writes it; a route call on the line
    whose literal it is, and leads to the handler that serves it."""
    root = _ab_repo(tmp_path, _AB_FILES)
    asbuilt.commit_into_repo(root, _ab_read(root, project_name="cards"), project_name="cards")

    view = asbuilt.source_view(root, "api/server.py")
    assert view["found"] and view["text"] == _AB_FILES["api/server.py"] and not view["changed_since"]
    create = next(f for f in view["functions"] if f["name"] == "create_card")
    assert (create["line"], create["end"]) == (4, 5)
    assert create["capabilities"] == [{"key": "work-with-cards", "depth": "direct"}]
    assert [(c["file"], c["name"], c["test"]) for c in create["called_by"]] == [("tests/test_api.py", "test_create", True)]
    assert [(c["file"], c["name"]) for c in create["calls"]] == [("api/store.py", "save_card")]
    assert {"id": "route:POST /api/cards", "line": 4} in view["ways_in"]
    assert [(l["kind"], l["target"], l["line"]) for l in view["links"]] == [("import", "api/store.py", 1)]

    store = asbuilt.source_view(root, "api/store.py")
    depth = {f["name"]: [c["depth"] for c in f["capabilities"]] for f in store["functions"]}
    assert depth == {"save_card": ["direct"], "load_card": ["direct"], "_db": ["in_motion"]}, \
        "one call past what the handler calls is set in motion, not run"

    web = asbuilt.source_view(root, "web/app.js")
    routes = {l["route"]: (l["target"], l["line"]) for l in web["links"] if l["kind"] == "route"}
    assert routes == {"GET /api/cards/{id}": ("api/server.py", 2), "POST /api/cards": ("api/server.py", 6)}

    with pytest.raises(KeyError):
        asbuilt.source_view(root, "../../etc/passwd")
    nested = ["def outer():", "    x = 1", "    def inner():", "        return x", "", "    return inner", "", "def after():"]
    assert asbuilt_graph._span_end(nested, 1, 7) == 6 and asbuilt_graph._span_end(nested, 3, 7) == 4, \
        "a function defined inside another ends where its own body does"


def test_code_resolves_an_import_however_the_language_writes_it():
    """The reader may write a module the way its language does rather than as a
    path; code turns it into the file it names, or into none. Dots are
    folders, a package is its folder's `__init__` or `index`, and a name two
    files could answer to answers to neither."""
    from collections import defaultdict
    paths = {"backend/app/config.py", "backend/app/models/__init__.py", "backend/app/models/user.py",
             "web/src/types/index.ts", "web/src/lib/api.ts", "a/util.py", "b/util.py"}
    by_tail = defaultdict(list)
    for p in sorted(paths):
        by_tail[p.rsplit("/", 1)[-1]].append(p)
    r = lambda given, origin="backend/app/main.py": asbuilt_graph.resolve_link(given, origin, paths, by_tail)  # noqa: E731
    assert r("app.config") == "backend/app/config.py"
    assert r("app.models") == "backend/app/models/__init__.py"
    assert r("app.models.user") == "backend/app/models/user.py"
    assert r("web/src/types", "web/src/pages/x.tsx") == "web/src/types/index.ts"
    assert r("app/config.py") == "backend/app/config.py"
    assert r("util") == "" and r("config.json") == "", "two files, or none, is not an answer"


def test_small_files_are_read_several_to_a_call_and_each_answered_for(tmp_path):
    """A call's fixed cost -- the harness's prompt, the instructions, the schema
    -- is paid once for a batch of small neighbours, not once per file. Code
    holds the answer to the question: a record goes to the file it names, a path
    nobody asked about is dropped, a file the answer left out is read again on
    its own, and a large file is always read alone. Each file is still cached
    by itself, so a refresh reads only what changed."""
    big = "".join(f"def f{i}():\n    return {i}\n\n" for i in range(1200))
    files = {**_AB_FILES, "api/big.py": big}
    records = {**_AB_RECORDS, "api/big.py": dict(purpose="many small functions")}
    root = _ab_repo(tmp_path, files)
    stub = _AbStub(records=records)
    stub.drop = {"web/view.js"}
    cache = asbuilt.RecordCache(tmp_path / "cache", asbuilt.reader_contract(
        load_config(ROOT / "factory.example.yaml").role_prompt("reader")))
    reading = _ab_read(root, stub, cache=cache)
    small = sorted(set(files) - {"api/big.py"})
    assert stub.batches == [small], "the small files went in one call, in path order"
    alone = sorted({re.search(r"# File\n\n(\S+)", q).group(1) for r, q in stub.asked
                    if r == "reader" and q.startswith("# File\n")})
    assert alone == ["api/big.py", "web/view.js"], "the large file alone, and the one the batch left out"
    assert sorted(reading.records) == sorted(files) and "invented.py" not in reading.records
    assert len(list((tmp_path / "cache").glob("*.json"))) == len(files), "one cached record per file"
    assert asbuilt.chunks_of(big)[0][1] < big.count("\n"), "the large file is read in parts"


def test_a_batched_answer_that_skips_what_a_file_defines_is_read_again_alone(tmp_path):
    """Ten files to a call, every model tried lists the outer function of a
    dense file and moves on -- 1 of `api.ts`'s 32. Code counts what a file
    plainly defines, by the shape of its lines in any language, and a batched
    answer under half of that is set aside and the file read on its own. A
    callback handed to a call, a type's signature and a local helper are not
    definitions, so a component full of them is not flagged."""
    api = "export const api = {\n  cards: {\n" + "".join(
        f"    op{i}: (id: string) => request(`/api/cards/${{id}}/{i}`),\n" for i in range(8)) + "  },\n}\n"
    component = ("export function Board() {\n  const save = useMutation({\n    mutationFn: () => api.cards.op1('x'),\n"
                 "    onError: (e: Error) => setError(e.message),\n  })\n  const reset = () => save.reset()\n  return null\n}\n"
                 "type Props = {\n  onSelect: (k: string) => void\n}\n")
    assert asbuilt.evident_definitions(api) == 8
    assert asbuilt.evident_definitions(component) == 1
    assert asbuilt.evident_definitions("class A(Base):\n    x: int\n\nclass B(str, Enum):\n    ON = 'on'\n\ndef f():\n    pass\n") == 3

    files = {**_AB_FILES, "web/api.ts": api}
    records = {**_AB_RECORDS, "web/api.ts": dict(purpose="the client", functions=[
        dict(name=f"op{i}", line=3 + i) for i in range(8)])}
    root = _ab_repo(tmp_path, files)
    stub = _AbStub(records=records)
    stub.thin = {"web/api.ts"}
    reading = _ab_read(root, stub)
    alone = {re.search(r"# File\n\n(\S+)", q).group(1) for r, q in stub.asked if r == "reader" and q.startswith("# File\n")}
    assert alone == {"web/api.ts"}, "only the thin answer is read again"
    assert len(reading.records["web/api.ts"].functions) == 8

    # A router or a manifest answered with no way in is thin too: its screens
    # and commands are how a person gets in.
    router = ("export function App() {\n  return (\n    <Routes>\n      <Route path=\"queue\" element={<Q />} />\n"
              "      <Route path=\"patients\" element={<P />} />\n      <Route path=\"patients/:id\" element={<D />} />\n"
              "    </Routes>\n  )\n}\n")
    src = lambda path, text: asbuilt_graph.Source(path=path, blob="b", text=text)  # noqa: E731
    bare = FileRecord(purpose="the app", functions=[dict(name="App", line=1)], ways_in=[dict(kind="export", name="App")])
    assert asbuilt.thin_answer(bare, src("web/App.tsx", router))
    assert asbuilt.thin_answer(FileRecord(purpose="p", kind="config"), src("web/package.json", '{"scripts": {"dev": "vite"}}'))
    assert not asbuilt.thin_answer(FileRecord(purpose="p", kind="config"), src("web/package.json", '{"name": "x"}'))
    assert not asbuilt.thin_answer(FileRecord(purpose="notes", kind="docs"), src("docs/plan.md", router)), \
        "a document's paths are not ways in"


def test_a_reading_at_the_plan_s_limit_stops_keeps_what_it_read_and_commits_nothing(tmp_path):
    """Twice a reading ran out of window a third of the way through, failed
    every file left in seconds, and committed the third it had as the
    picture. At the limit it stops asking; what was read is kept, so the next
    reading carries on; and nothing is committed until the picture is whole."""
    root = _ab_repo(tmp_path, _AB_FILES)
    config = load_config(ROOT / "factory.example.yaml")
    cache = asbuilt.RecordCache(tmp_path / "cache", asbuilt.reader_contract(config.role_prompt("reader")))
    stub = _AbStub()
    stub.limit_after = 0
    with pytest.raises(asbuilt.AsBuiltError, match="Stopped: the reader's route reached its usage limit") as got:
        asyncio.run(asbuilt.AsBuiltReader(config, stub, cache=cache).read(root, "HEAD"))
    assert "nothing was committed" in str(got.value) and "Read again carries on" in str(got.value)
    assert not (root / ".fabrika").exists()

    contract = asbuilt.reader_contract(config.role_prompt("reader"))
    assert asbuilt.cache_coverage(root, tmp_path / "cache", contract) == {"kept": 0, "listed": len(_AB_FILES)}

    stub = _AbStub()                 # the window reopened
    reading = asyncio.run(asbuilt.AsBuiltReader(config, stub, cache=cache).read(root, "HEAD"))
    assert sorted(reading.records) == sorted(_AB_FILES)
    assert asbuilt.cache_coverage(root, tmp_path / "cache", contract) == {"kept": len(_AB_FILES), "listed": len(_AB_FILES)}, \
        "what is read and kept is what the tab says"


def test_a_generated_file_paints_the_picture_and_is_never_put_up_for_review():
    """A generated file is read and drawn -- the SDK a front end calls through
    is how it reaches the server -- but it is in no finding and no place to
    start reading: what a person reviews is what generates it. It is known by
    a comment saying so, or a generator's name, never by prose or an import."""
    body = "".join(f"export const q{i} = () => fetch('/x/{i}');\n" for i in range(3000))
    texts = {"web/app.ts": "import { q1 } from './sdk';\nexport function main() { return q1(); }\n",
             "web/sdk.ts": "/**\n * DO NOT MODIFY - This file has been generated using oazapfts.\n */\n" + body,
             "web/app.test.ts": "import { main } from './app';\ntest('main', () => main());\n"}
    src = {p: asbuilt_graph.Source(path=p, blob=p, text=t) for p, t in texts.items()}
    recs = {"web/app.ts": FileRecord(purpose="app", imports=["web/sdk.ts"], functions=[dict(name="main", line=2, calls=["q1"])]),
            "web/sdk.ts": FileRecord(purpose="the client", functions=[dict(name="q1", line=5)]),
            "web/app.test.ts": FileRecord(purpose="t", is_test=True, imports=["web/app.ts"])}
    ab = asbuilt_graph.build(src, recs)
    sdk = ab.file("web/sdk.ts")
    assert sdk.generated and not ab.file("web/app.ts").generated
    assert any(e.target == "web/sdk.ts" for e in ab.edges), "it is still drawn"
    flagged = {p for f in asbuilt_graph.findings(ab) for p in f.paths}
    assert "web/sdk.ts" not in flagged, "3,000 untested lines a tool wrote are not a finding"
    asbuilt_graph.apply_account(ab, SystemAccount(summary="s", start_reading=[
        StartHere(path="web/sdk.ts", why="big"), StartHere(path="web/app.ts", why="entry")]))
    assert [x["path"] for x in ab.start_reading] == ["web/app.ts"]
    for text, made in [("// GENERATED CODE - DO NOT MODIFY BY HAND", True), ("# Code generated by protoc-gen-go. DO NOT EDIT.", True),
                       ("Edits do not modify the original file.", False), ("import 'package:app/generated/strings.g.dart';", False)]:
        assert asbuilt_graph.is_generated("a.txt", text) is made, text
    assert asbuilt_graph.is_generated("lib/models/user.freezed.dart", "")


def test_a_refresh_reads_only_what_changed_and_commits_only_the_as_built(tmp_path):
    """A person's press, like `.fabrika/Dockerfile`: the reading is committed,
    and nothing else they have staged is swept in. The next reading keeps every
    record whose file did not change -- a refresh costs what the change touched."""
    root = _ab_repo(tmp_path, _AB_FILES)
    reading = _ab_read(root, project_name="cards")
    (root / "notes.txt").write_text("mine")
    subprocess.run(["git", "add", "notes.txt"], cwd=root, check=True)
    wrote = asbuilt.commit_into_repo(root, reading, project_name="cards")
    assert wrote["commit"] and not wrote["commit_problem"]
    committed = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=root,
                               capture_output=True, text=True).stdout.split()
    assert "notes.txt" not in committed, "somebody's staged work was committed as the as-built"
    assert all(p.startswith(".fabrika/as-built/") for p in committed)
    subprocess.run(["git", "restore", "--staged", "notes.txt"], cwd=root, check=True)
    (root / "notes.txt").unlink()
    assert {".fabrika/as-built/README.md", ".fabrika/as-built/graph.json",
            ".fabrika/as-built/files/api/server.py.md", ".fabrika/as-built/records/api/server.py.json"} <= set(committed)

    status = asbuilt.status(root)
    assert status["fresh"], "committing the as-built must not make it stale"

    (root / "web/view.js").write_text("export function render(card) {\n  document.body.textContent = card.name;\n}\n")
    _ab_commit(root)
    assert asbuilt.status(root)["changed"] == ["web/view.js"]
    previous, known = asbuilt.load_committed(root)
    stub = _AbStub()
    again = _ab_read(root, stub, previous=previous, previous_records=known)
    assert (again.read, again.reused) == (1, 4)
    assert [r for r, _ in stub.asked] == ["reader"], \
        "unchanged names and an unchanged set of ways in are carried, not asked for again"

    (root / ".fabrika/as-built/README.md").write_text("my notes")
    with pytest.raises(asbuilt.AsBuiltError, match="not committed"):
        asbuilt.commit_into_repo(root, again)
    assert (root / ".fabrika/as-built/README.md").read_text() == "my notes"


def test_a_long_file_is_read_in_numbered_pieces_that_keep_its_own_line_numbers():
    text = "".join(f"def f{i}():\n    return {i}\n\n" for i in range(900))
    pieces = asbuilt.chunks_of(text)
    assert len(pieces) > 1
    assert pieces[0][0] == 1 and pieces[1][0] == pieces[0][1] + 1
    assert sum(last - first + 1 for first, last, _ in pieces) == len(text.splitlines())
    assert all(b[0] == a[1] + 1 for a, b in zip(pieces, pieces[1:])), "a line fell between two pieces"
    for first, _, body in pieces[1:]:
        assert body.startswith("def "), "a cut landed inside a definition"
    assert asbuilt.numbered(41, "x\ny").splitlines()[1].strip().startswith("42")


def test_what_a_feature_changed_is_said_in_the_system_s_terms():
    def reading(files, edges, subs):
        return asbuilt_graph.AsBuilt(
            commit="c", files=[asbuilt_graph.FileNode(path=p, subsystem=s, blob=p, placed_by="graph") for p, s in files],
            edges=[asbuilt_graph.Edge(source=a, target=b, kind="import") for a, b in edges],
            subsystems=[asbuilt_graph.Subsystem(key=k, name=n, files=[p for p, s in files if s == k]) for k, n in subs])
    base = reading([("web/a.js", "web/a.js"), ("jobs/j.py", "jobs/j.py")], [], [("web/a.js", "Web"), ("jobs/j.py", "Jobs")])
    head = reading([("web/a.js", "web/a.js"), ("web/b.js", "web/a.js"), ("jobs/j.py", "jobs/j.py")],
                   [("web/b.js", "jobs/j.py")], [("web/a.js", "Web"), ("jobs/j.py", "Jobs")])
    head.ways_in = [asbuilt_graph.WayInNode(id="route:GET /x", kind="route", name="/x", file="web/b.js")]
    change = asbuilt_graph.system_change(base, head)
    assert change.dependencies_added == [{"from": "Web", "to": "Jobs"}]
    lines = change.lines()
    assert "Adds a dependency from Web to Jobs." in lines
    assert any(line.startswith("One new file, all placed") for line in lines)
    assert any("route:GET /x" in line for line in lines)


def test_the_as_built_pages_link_down_and_back_up(tmp_path):
    root = _ab_repo(tmp_path, _AB_FILES)
    reading = _ab_read(root, project_name="cards")
    pages = asbuilt_pages.render(reading.as_built, reading.records, project_name="cards")
    readme = pages["README.md"]
    assert "```mermaid" in readme and "## Coverage of this reading" in readme and "## Capabilities" in readme
    for target in re.findall(r"\]\(([^)]+\.md)\)", readme):
        assert target in pages, f"README links to {target}, which was not written"
    sub = next(p for p in pages if p.startswith("subsystems/"))
    assert "](../README.md)" in pages[sub]
    assert "](../../README.md)" in pages["files/api/server.py.md"]
    assert json.loads(pages["records/api/server.py.json"])["blob"]
