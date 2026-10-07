# Fabrika's design

The [README](../README.md) says what Fabrika is and how to run it. These pages
say how it works and why it is built the way it is.

## The design

| Page | What it covers |
|---|---|
| [architecture.md](architecture.md) | The review packet, the pipeline, the three layers, the invariants, three things not to improve, the source layout, known gaps and non-goals |
| [gates.md](gates.md) | Gate 0 (approving what a project measures, and keeping it current), gates 1 and 2, and how a check's result is judged and attributed |
| [agents.md](agents.md) | The three kinds of agent, the review panel you declare, the breaker, the repair loop, verifying a packet again, executors and the harness |
| [isolation.md](isolation.md) | Worktrees, the container every check and agent runs in, and the one way out to the internet |
| [repository-reading.md](repository-reading.md) | How the scout reads a repository for agents, and the as-built a person reads |
| [testing.md](testing.md) | Fabrika's own test suite: how to run it, where each area is tested, what it proves and what it cannot see |

The help pages the console opens (`console/help/`) are written for a person
using Fabrika rather than one changing it, and are not repeated here.
