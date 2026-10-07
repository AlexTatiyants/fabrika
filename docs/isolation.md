# Isolation

Two boundaries, and a feature needs both. A git worktree keeps one feature's
files apart from every other's. A sealed container keeps everything an agent
does, and everything it wrote, off this machine. The module docstring of
`factory/isolation.py` is the short version of this page.

## Worktrees

Every feature gets a git worktree at `<sandboxes>/<project>/<feature>`, on a
branch `factory/<feature-id>` cut from the project's base ref. Workers, the
integrator, the file writes, the blind tests and the gates all happen in there.
The project's own working tree is never touched.

Because a worktree shares the object database with the repository it came from,
the finished work is already a ref in your normal checkout:

```bash
git diff main...factory/<feature-id>
```

Nothing to export, nothing to copy back. Accepting a feature drops the checkout
and keeps the branch; rejecting one leaves both standing, because the first
thing anyone does after rejecting is go and look at what was actually built.

A worktree isolates files. Ports, databases and dev servers are isolated by
the container: every agent session and every command a check runs happens in
one, on a Docker network of its own (see **Where the gates run** and **The way
out**, below). Nothing an agent does, or anything it wrote, runs on this machine.

## Where the gates run

A project's `EnvironmentSpec` resolves to an image, built at gate 0 and reused by
every feature in that project. Each gate gets its own container with the
feature's worktree mounted at the workdir:

```
docker run --rm --user <you> -v <worktree>:/work -w /work \
  --network fabrika-sealed-<id> --memory 4g --cpus 2 --pids-limit 512 \
  --entrypoint sh <image> -c '<the gate command>'
```

One container per command, so a wedged gate takes nothing else down and no state
carries between gates. Images are content-addressed on the Dockerfile, so an
unchanged environment never rebuilds and an edited one always does. Setup runs
**with** a way out to the internet; gates run **without** one. Both networks are
`--internal`, so neither has a route to this machine; the difference is only
whether the egress proxy is attached.

Some tools fetch part of what they need only when they run. Maven downloads its
test provider the first time a test executes, so a setup that installs
dependencies and skips tests leaves it behind, and the check then fails offline
on `Unknown host repo.maven.apache.org`. Fabrika does not keep a list of which
tools do this. At the baseline, any check that fails offline is run once more
with the network, as part of setup, and then run offline again on a freshly
reset stack. If it stops failing for want of a name, it is recorded in
`warm_checks`, and every setup after that, for features too, runs it once with
the network after its own commands. The check line says "setup runs it online
once first". A new survey clears the list, and the next baseline finds it again.

How often setup runs is a property of the runner, not of the checkout. On the
host it runs once per sandbox: what it installs stays on the machine. In a
container it runs every assessment round, because the toolchain lives in an
image committed from the setup container and removed on the way down, and a
database it migrated lives in a volume removed with it. Skipping it there costs
nothing visible and everything real — every gate after round 0 reports
`ruff: not found`, and the packet then says zero criteria were verified, which
is a fact about the harness wearing the clothes of a fact about the code. The
compose path, on a Postgres-backed project, is where this shows first, because
compose takes the volume as well as the image down between rounds.

**There is no silent fallback.** If a project says its gates run in a container
and no container can be had, the run fails and names the reason. Quietly dropping
to the host would leave you believing results were isolated when they were not,
which is worse than no results. An environment of kind `host` is refused as
soon as an agent would run in it: it would put the agent on this machine, where
it can reach every server running here and every other feature's files. The one
exception is the test suite, which has no Docker (`tests/conftest.py`, and
`factory/isolation.py` for why).

## The way out

Every container Fabrika starts is on a network with no route anywhere: not to
this machine, not to another feature's containers, not to the internet. What
genuinely needs the internet — an agent calling its model provider, setup
installing packages — gets it through one proxy, `fabrika-egress`, which
forwards to **public addresses only**. Loopback, private ranges, link-local and
Docker Desktop's range for the host (`192.168.65.0/24`) are refused. It resolves
each name itself and connects to the address it checked, so a name pointing home
does not get through either.

That is what keeps your own dev servers out of a build. A copy of the project
on `localhost:8300` is the common case, and without the proxy a feature's
tests find it at `192.168.65.254:8300` and pass against the wrong app.

Nothing inside a container is told the proxy is there, so it works for every
stack the same way: npm and pip, but also Maven, Gradle, cargo, go, and git over
SSH. A container's resolver asks Docker for the names of its own stack and the
proxy for everything else. The proxy answers an outside name with a stand-in
address on the container's own network, and whatever connects to that address,
on any port, is carried to the real host if it is public. `HTTP(S)_PROXY` is
not set: a JVM never reads it, which is how a Maven project's first check can
fail with `Unknown host repo.maven.apache.org`.

```
agents, setup ─┐  name → stand-in address  ┌─ public addresses, any port
               ├──── fabrika-egress ───────┤
checks ──── (no way out)                   └─ refused: this machine, your LAN
```

Every sealed network is a /22 from `docker.sealed_pool` (`10.212.0.0/14` by
default, 256 networks). Change it in `factory.yaml` if it overlaps a network
this machine reaches, such as a company VPN. Two things do not get out: a
program that connects to a literal IP address without looking a name up, and
UDP other than DNS.

### Watching it

The crew page has a "Network proxy" card under the harnesses. It says what the
proxy is, then in one line whether it is running and whether it blocked
anything in the last day. Under that are how many connections went out, where
to (the busiest hosts), and each blocked attempt: when it happened, which
container tried, the target and the reason. A public address that did not
answer is listed apart, as "did not answer", because it wasn't refused. The
same reading is at `GET /api/egress`.

The proxy starts with the first build that needs it, and `Start it` on the card
starts it sooner. `Test it` runs a throwaway container with no settings at all
and checks it can reach a registry over HTTPS and GitHub over SSH. It is
recreated whenever `factory/egress_proxy.py` or the way it is started changes,
and it rejoins the networks it was on, so a build in flight keeps its way out.
Its log is one JSON line per connection, capped at 30 MB by Docker:

```bash
docker logs --since 1h fabrika-egress
```

A refusal is almost always a test or an agent reaching for `localhost` or a
host port. The fix belongs in the test: read the service's address from
`FACTORY_URL_<SERVICE>`, never from a default port.
