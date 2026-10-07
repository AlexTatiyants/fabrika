# Reading a repository

Two readings of a codebase, for two readers. The scout reads for agents: cut to
what one feature needs, and thrown away when the run ends. The as-built reads
for a person: a standing account of the whole codebase, checked by code and
kept in the repository.

## The scout

The scout does not get one truncated pass. A single digest reports on the
alphabetically-first fraction of a codebase and looks exactly like a report on
all of it — on one real repository that was 8% of the source, presented as a
complete survey.

Three things happen instead:

**`.gitignore` decides what exists.** File selection goes through
`git ls-files --cached --others --exclude-standard`, so logs, scraped data and
database dumps are out and work in progress is in. On a 2.3GB repository this is
the difference between walking 750 million characters and reading the 11 million
that are the codebase.

**A symbol index is computed for the whole repo and given to every scout.**
Slicing costs one thing — no scout sees everything, so none can know a name is
defined twice. That was never a judgment; it is a lookup, and code does it
exactly where attention over 200k tokens does it probabilistically. The index is
a few thousand tokens regardless of repo size, and its list of names defined in
more than one file is the strongest possible start on `do_not_duplicate`. Test
functions are excluded: you never accidentally reimplement `test_foo`, and
counting them drowns the signal.

**Slices are chosen by relevance to the intent**, scored on path matches, symbol
matches, and how much each slice *defines* rather than merely mentions. What is
not read is named in the coverage record and shown in the console, so a partial
read never presents as a complete one.

Slice size is a per-model knob. At `scout_slice_chars: 120000` a small local
model reads a 850k-character repo in nine calls; at `900000` a long-context
model does it in one. Same code path.

## The as-built

The scout's reading is for agents, cut to what one feature needs and thrown away
when the run ends. The person answering for code that agents wrote needs the
opposite: a standing account of the whole codebase as it was actually built.
That is the as-built, on the project's **As-built** tab and in the repository
under `.fabrika/as-built/`, readable there without Fabrika.

**A model reads; code checks what it read.** The `reader` is asked what one file
contains — its imports, the files it starts by path, its functions and what each
calls, the routes it serves and calls, its data, the systems outside it talks
to — in whatever language it is written, one call per file, in parallel. Code
then joins the answers and checks every claim it can: an import names a file
that exists and whose name the importer's text carries; a call to the back end
matches a route something serves; a function's line holds its name. What fails
is kept, drawn dashed and counted, never dropped and never promoted.

**Code groups; a model names.** Subsystems are clusters of the confirmed graph,
with files nearly everything imports set aside as shared. A file too big to be
one node is shown in parts — clusters of its own call graph — with the calls
across each boundary counted, which is also where it comes apart. Capabilities
are the ways in (routes, commands, jobs, screens) and what they reach, at two
depths: what a handler runs directly, and what that sets in motion. Only then is
the `cartographer` asked to name the groups and group the ways in; every way in
lands in exactly one capability, or in one a person can see is ungrouped.
Clustering is deterministic and starts from the last reading's groups, so a
refresh moves a part only when the code does.

**It is read at a commit, and only what changed is read again.** Files come
from git's objects at the commit, not the working copy; a file's record is kept
by its content, so a refresh costs about what the change touched. On a person's
press the result is committed under `.fabrika/as-built/`, touching nothing else
they have staged and refusing to overwrite an uncommitted edit there — the rule
`.fabrika/Dockerfile` follows. A feature reads it too, at the commit it branched
from and at its head, and its packet says what it changed in the system: a new
dependency between subsystems, a new way in, a file nothing places. Those
readings stay in the feature's ledger; nothing under `.fabrika/` rides a
feature branch.

The as-built never reaches the verify lane (INV-13, in
[architecture.md](architecture.md#the-invariants)).
