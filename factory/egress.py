"""Sealed networks, and the proxy that is their only way out.

See `isolation.py` for why, and `egress_proxy.py` for what the proxy allows and
how a sealed network is laid out. This module is the Docker half: it keeps one
proxy container running, makes `--internal` networks out of one address pool,
and connects the proxy to exactly the ones whose containers are allowed the
internet.

A sealed network with the proxy attached reaches public addresses and nothing
else. Without it, it reaches nothing but its own members -- which is what the
checks get, because nothing a check does should need the world.

Nothing inside a container is told the proxy exists. A container on a sealed
network gets one file, its resolver (`resolver_argv`), which asks Docker for
the names of its own stack and the proxy for everything else; the proxy answers
with stand-in addresses it owns, and whatever connects to one is carried out
by the proxy on whatever port it asked for. So a JVM, which ignores the
`HTTP(S)_PROXY` variables, reaches Maven Central the same
way npm reaches its registry -- and so does git over SSH.

Owning addresses on a network takes two kernel changes in the proxy's own
network namespace: a local route for the stand-in block and a redirect from it
to the proxy's listener. The proxy runs with every capability dropped, so a
helper container that shares its namespace makes those changes and exits
(`_install_rules`). Nothing that runs project or agent code gains anything.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Sequence

from . import procs
from .config import DockerConfig
from .egress_proxy import (
    TRANSPARENT_PORT, blocks, container_range, laid_out, proxy_address, stand_in_block,
)

PROXY = "fabrika-egress"
SCRIPT = Path(__file__).with_name("egress_proxy.py")
#: What the proxy's base image gets on top: the two tools the helper needs to
#: give the proxy its stand-in addresses. Either package manager, so a base
#: image named in factory.yaml can be Alpine or Debian.
IMAGE_INSTALL = ("(apk add --no-cache iptables iproute2 || (apt-get update && "
                 "apt-get install -y --no-install-recommends iptables iproute2 && "
                 "rm -rf /var/lib/apt/lists/*))")
#: One resolver file per sealed network, bind-mounted over each container's
#: own. Under the system temp directory because Docker Desktop shares it with
#: its VM by default, and a Linux host needs nothing shared at all.
RESOLV_DIR = Path(tempfile.gettempdir()) / "fabrika-resolv"

_locks: dict[int, asyncio.Lock] = {}


def _lock() -> asyncio.Lock:
    # One per event loop: a lock made on one loop cannot be waited on from
    # another, and the test suite starts a fresh loop per `asyncio.run`.
    loop = asyncio.get_running_loop()
    return _locks.setdefault(id(loop), asyncio.Lock())


async def _docker(argv: Sequence[str], timeout: float = 60.0) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except OSError as exc:
        return 127, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await procs.kill(proc)
        return -9, f"timed out after {timeout:g}s"
    return (proc.returncode if proc.returncode is not None else -1,
            (out or b"").decode("utf-8", errors="replace").strip())


def _image_tag(docker: DockerConfig) -> str:
    digest = hashlib.sha256(f"{docker.egress_image}\n{IMAGE_INSTALL}".encode()).hexdigest()
    return f"{PROXY}:{digest[:12]}"


async def _image(docker: DockerConfig) -> str:
    """The proxy's image: the configured base plus iptables and iproute2, built
    once and kept by content. Its tag is what `_run_argv` names, so a change to
    either is a new version of the proxy."""
    tag = _image_tag(docker)
    code, _ = await _docker([docker.binary, "image", "inspect", "--format", "{{.Id}}", tag])
    if code == 0:
        return tag
    with tempfile.TemporaryDirectory(prefix="fabrika-egress-build-") as tmp:
        (Path(tmp) / "Dockerfile").write_text(
            f"FROM {docker.egress_image}\nUSER root\nRUN {IMAGE_INSTALL}\n", encoding="utf-8")
        await _docker([docker.binary, "build", "-q", "-t", tag, tmp],
                      timeout=docker.build_timeout_s)
    return tag


def _run_argv(docker: DockerConfig, version: str) -> list[str]:
    return [
        docker.binary, "run", "-d", "--name", PROXY,
        "--label", f"fabrika.egress={version}",
        "--restart", "unless-stopped",
        # Its own way out is the default bridge. Nothing is published: the
        # only clients are containers on the sealed networks it joins.
        "--network", "bridge",
        "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        # DNS is port 53, and binding below 1024 is otherwise a capability.
        # This moves the line for this container's network namespace only.
        "--sysctl", "net.ipv4.ip_unprivileged_port_start=53",
        "--memory", "256m", "--pids-limit", "256",
        # A line per connection, for as long as the machine is up: bounded, so
        # the record of what agents reached cannot fill the disk it is kept on.
        "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
        "--volume", f"{SCRIPT}:/egress_proxy.py:ro",
        "--env", f"EGRESS_POOL={docker.sealed_pool}",
        "--env", f"EGRESS_TRANSPARENT_PORT={TRANSPARENT_PORT}",
        _image_tag(docker), "python", "/egress_proxy.py",
    ]


def _version(docker: DockerConfig) -> str:
    """What the running proxy must be: this script, started this way."""
    digest = hashlib.sha256(SCRIPT.read_bytes())
    digest.update("\0".join(_run_argv(docker, "")).encode())
    return digest.hexdigest()[:16]


async def _attached(docker: DockerConfig) -> list[str]:
    code, out = await _docker([
        docker.binary, "inspect", "--format",
        "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", PROXY])
    return [n for n in out.split() if n != "bridge"] if code == 0 else []


async def ensure_proxy(docker: DockerConfig) -> str:
    """Start the proxy if it is not running as this version. "" or a problem.

    Recreated when the script or the way it is started changes, so an edit to
    what it allows reaches the running proxy instead of waiting for someone to
    remember to restart it. A recreated proxy rejoins every network the old one
    was on, at the same address and with its stand-ins: a build in flight when
    the console restarts keeps its way out.
    """
    global _proxy_started
    want = _version(docker)
    async with _lock():
        code, out = await _docker([
            docker.binary, "inspect", "--format",
            '{{.State.Running}} {{index .Config.Labels "fabrika.egress"}} {{.State.StartedAt}}',
            PROXY])
        state = out.split()
        if code == 0 and state[:2] == ["true", want]:
            _proxy_started = state[2] if len(state) > 2 else ""
            return ""
        rejoin = await _attached(docker) if code == 0 else []
        if code == 0:
            await _docker([docker.binary, "rm", "-f", PROXY])
        await _image(docker)
        code, out = await _docker(_run_argv(docker, want), timeout=300)
        if code != 0:
            return f"could not start the egress proxy: {out[-600:]}"
        _, started = await _docker([docker.binary, "inspect", "--format",
                                    "{{.State.StartedAt}}", PROXY])
        _proxy_started = started.strip()
        for network in rejoin:
            subnet = await subnet_of(docker, network)
            if laid_out(subnet, docker.sealed_pool):
                await _join(docker, network, subnet)
        return ""


def _who(name: str) -> dict[str, str]:
    """A container's name, said as what it was doing."""
    short = name.removeprefix("fabrika-")
    if short.startswith("answer-"):
        return {"name": name, "what": "a model call"}
    if short.startswith("setup-"):
        return {"name": name, "what": "setup"}
    return {"name": name, "what": short}


async def _members(docker: DockerConfig,
                   networks: Sequence[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Address -> container name, and subnet -> network, for the proxy's networks.

    Read when the log is read, not when it was written: the proxy sees only an
    address. A container that has since gone is not guessed at -- addresses on
    a network are reused -- but the network it was on still says something:
    anything from `fabrika-agents` was a model call.
    """
    if not networks:
        return {}, {}
    code, out = await _docker([
        docker.binary, "network", "inspect", "--format",
        "{{.Name}}|{{range .IPAM.Config}}{{.Subnet}} {{end}}|"
        "{{range .Containers}}{{.Name}}={{.IPv4Address}} {{end}}", *networks])
    found: dict[str, str] = {}
    subnets: dict[str, str] = {}
    if code != 0:
        return found, subnets
    for line in out.splitlines():
        network, _, rest = line.partition("|")
        nets, _, pairs = rest.partition("|")
        for subnet in nets.split():
            subnets[subnet] = network
        for pair in pairs.split():
            name, _, address = pair.partition("=")
            if address and name != PROXY:
                found[address.split("/", 1)[0]] = name
    return found, subnets


def _gone(address: str, subnets: dict[str, str]) -> dict[str, str]:
    from .agentbox import AGENTS_NETWORK
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        ip = None
    for subnet, network in subnets.items():
        try:
            inside = ip is not None and ip in ipaddress.ip_network(subnet, strict=False)
        except ValueError:
            continue
        if inside:
            what = ("a model call" if network == AGENTS_NETWORK
                    else f"a container on {network.removeprefix('fabrika-')}")
            return {"name": "", "what": f"{what}, since stopped"}
    return {"name": "", "what": "a container that has since stopped"}


def read_log(text: str) -> list[dict]:
    """The proxy's JSON lines, anything else skipped: an older proxy's plain
    lines, or Python's own complaint if it ever crashed, are not events."""
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("event"):
            events.append(event)
    return events


def summarize(events: Sequence[dict], members: dict[str, str], keep: int = 50,
              subnets: dict[str, str] | None = None) -> dict:
    """What the console shows: how much went out, where, and what was stopped."""
    def attributed(event: dict) -> dict:
        name = members.get(event.get("client", ""))
        who = _who(name) if name else _gone(event.get("client", ""), subnets or {})
        return {"at": event.get("at", ""), "target": event.get("target", ""),
                "why": event.get("why", ""), "client": event.get("client", ""), **who}

    forwarded = [e for e in events if e["event"] == "forwarded"]
    refused = [e for e in events if e["event"] == "refused"]
    failed = [e for e in events if e["event"] == "failed"]
    hosts = Counter(e.get("target", "").rsplit(":", 1)[0] for e in forwarded)
    return {
        "forwarded": len(forwarded),
        "refused_count": len(refused),
        "failed_count": len(failed),
        "destinations": [{"host": h, "count": n} for h, n in hosts.most_common(12) if h],
        "refused": [attributed(e) for e in reversed(refused[-keep:])],
        "failed": [attributed(e) for e in reversed(failed[-keep:])],
    }


async def status(docker: DockerConfig, hours: int = 24) -> dict:
    """Whether the way out is up, and what went through it lately.

    Read-only: it never starts the proxy. Opening a screen should not change
    what runs on the machine; `ensure_proxy` is what a build (or the console's
    Start button) calls.
    """
    base = {"hours": hours, "pool": docker.sealed_pool, "name": PROXY, "networks": [],
            "forwarded": 0, "refused_count": 0, "failed_count": 0,
            "destinations": [], "refused": [], "failed": [], "self_test": _last_test}
    code, out = await _docker([
        docker.binary, "inspect", "--format",
        '{{.State.Running}}|{{.State.StartedAt}}|{{index .Config.Labels "fabrika.egress"}}',
        PROXY])
    if code == 127:
        return {**base, "state": "no_docker", "detail": f"Docker is not reachable: {out}"}
    if code != 0:
        if "no such" in out.lower():
            return {**base, "state": "absent",
                    "detail": "Not started yet. The first build or agent call starts it."}
        return {**base, "state": "unknown", "detail": out[-400:]}
    running, started, version = (out.split("|") + ["", "", ""])[:3]
    networks = await _attached(docker)
    state = "running" if running == "true" else "stopped"
    info = {**base, "state": state, "started_at": started,
            "current": version == _version(docker), "networks": networks}
    code, logs = await _docker([docker.binary, "logs", "--since", f"{hours}h", PROXY])
    if code == 0:
        members, subnets = await _members(docker, networks)
        info.update(summarize(read_log(logs), members, subnets=subnets))
    return info


# -- addresses ---------------------------------------------------------------

#: Blocks this process has promised to a compose stack that has not created its
#: networks yet. Docker cannot see them, so without this two stacks brought up
#: at once would be handed the same one.
_reserved: set[str] = set()
#: Network name -> subnet, for networks made or read here.
_subnets: dict[str, str] = {}
_net_locks: dict[int, asyncio.Lock] = {}


def _net_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    return _net_locks.setdefault(id(loop), asyncio.Lock())


async def _taken(docker: DockerConfig) -> list[ipaddress.IPv4Network]:
    """Every IPv4 subnet any Docker network on this machine already uses."""
    code, out = await _docker([docker.binary, "network", "ls", "-q"])
    ids = out.split() if code == 0 else []
    if not ids:
        return []
    code, out = await _docker([
        docker.binary, "network", "inspect", "--format",
        "{{range .IPAM.Config}}{{.Subnet}} {{end}}", *ids])
    found: list[ipaddress.IPv4Network] = []
    for token in out.split() if code == 0 else []:
        try:
            net = ipaddress.ip_network(token, strict=False)
        except ValueError:
            continue
        if isinstance(net, ipaddress.IPv4Network):
            found.append(net)
    return found


async def _free_block(docker: DockerConfig) -> str:
    taken = await _taken(docker) + [ipaddress.ip_network(r) for r in _reserved]
    for block in blocks(docker.sealed_pool):
        if not any(block.overlaps(t) for t in taken):
            return str(block)
    return ""


async def reserve_blocks(docker: DockerConfig, count: int) -> tuple[list[str], str]:
    """Blocks for networks something else will create -- compose, from an
    override. Held until `release_blocks`, or the process ends."""
    got: list[str] = []
    async with _net_lock():
        for _ in range(count):
            block = await _free_block(docker)
            if not block:
                release_blocks(got)
                return [], _pool_full(docker)
            _reserved.add(block)
            got.append(block)
    return got, ""


def release_blocks(held: Sequence[str]) -> None:
    for block in held:
        _reserved.discard(block)


def _pool_full(docker: DockerConfig) -> str:
    return (f"no free /22 is left in the sealed-network pool {docker.sealed_pool}; "
            "set `docker.sealed_pool` in factory.yaml to a larger range, or remove "
            "networks left behind by runs that were killed (`docker network prune`)")


async def subnet_of(docker: DockerConfig, network: str) -> str:
    if network in _subnets:
        return _subnets[network]
    code, out = await _docker([docker.binary, "network", "inspect", "--format",
                               "{{range .IPAM.Config}}{{.Subnet}} {{end}}", network])
    for token in out.split() if code == 0 else []:
        if "." in token:
            _subnets[network] = token
            return token
    return ""


async def create_sealed(docker: DockerConfig, name: str) -> str:
    """An `--internal` network out of the pool: no route to this machine or
    anywhere else, and laid out so the proxy can serve it if it is let out.

    A network of this name made before the pool existed is not reused: nothing
    could give it stand-ins. It is removed and made again, and if something is
    still on it, that is said rather than worked around.
    """
    async with _net_lock():
        existing = await subnet_of(docker, name)
        if existing and laid_out(existing, docker.sealed_pool):
            return ""
        if existing:
            _subnets.pop(name, None)
            await _docker([docker.binary, "network", "disconnect", "--force", name, PROXY])
            code, out = await _docker([docker.binary, "network", "rm", name])
            if code != 0:
                return (f"sealed network {name} predates the address pool and is still in "
                        f"use, so it cannot be remade: {out[-300:]}")
        last = ""
        for _ in range(8):
            block = await _free_block(docker)
            if not block:
                return _pool_full(docker)
            code, out = await _docker([
                docker.binary, "network", "create", "--internal",
                "--subnet", block, "--ip-range", container_range(block),
                "--label", "fabrika.sealed=1", name])
            if code == 0:
                _subnets[name] = block
                return ""
            if "already exists" in out:
                _subnets.pop(name, None)
                return "" if laid_out(await subnet_of(docker, name), docker.sealed_pool) \
                    else f"sealed network {name} appeared outside the pool: {out[-300:]}"
            last = out
            if "overlap" not in out.lower():
                break
            # Another process took this block between looking and creating.
            # The next look sees its network, so it moves on.
        return f"could not create sealed network {name}: {last[-400:]}"


# -- the way out -----------------------------------------------------------------

#: (proxy start time, subnet) pairs whose kernel rules are in place. A proxy
#: restarted by Docker comes back in a new network namespace with none, and a
#: new start time says so.
_installed: set[tuple[str, str]] = set()
_proxy_started = ""


async def _install_rules(docker: DockerConfig, subnet: str) -> str:
    """Give the proxy the stand-in block of `subnet`: a local route, so the
    kernel answers for every address in it, and a redirect of every TCP
    connection to one of them to the proxy's listener.

    Made by a helper that joins the proxy's network namespace with the one
    capability this needs and exits, so the proxy itself holds none.
    Idempotent: `ip route replace`, and the rule is checked before it is added.
    """
    key = (_proxy_started, subnet)
    if _proxy_started and key in _installed:
        return ""
    block = stand_in_block(subnet)
    rule = f"-t nat {{}} PREROUTING -p tcp -d {block} -j REDIRECT --to-ports {TRANSPARENT_PORT}"
    script = (f"ip route replace local {block} dev lo && "
              f"(iptables {rule.format('-C')} 2>/dev/null || iptables {rule.format('-A')})")
    code, out = await _docker([
        docker.binary, "run", "--rm", "--network", f"container:{PROXY}",
        "--cap-drop", "ALL", "--cap-add", "NET_ADMIN", "--cap-add", "NET_RAW",
        "--entrypoint", "sh", await _image(docker), "-c", script], timeout=120)
    if code != 0:
        return f"could not give the egress proxy the stand-ins for {subnet}: {out[-400:]}"
    _installed.add(key)
    return ""


async def _join(docker: DockerConfig, network: str, subnet: str) -> str:
    """The proxy on `network`, at the address that network's resolver file names."""
    want = proxy_address(subnet)
    code, out = await _docker([docker.binary, "network", "connect", "--ip", want,
                               network, PROXY])
    if code != 0 and ("already exists" in out or "already attached" in out):
        _, have = await _docker([
            docker.binary, "inspect", "--format",
            f'{{{{(index .NetworkSettings.Networks "{network}").IPAddress}}}}', PROXY])
        if have.strip() != want:
            await _docker([docker.binary, "network", "disconnect", "--force", network, PROXY])
            code, out = await _docker([docker.binary, "network", "connect", "--ip", want,
                                       network, PROXY])
        else:
            code = 0
    if code != 0:
        return f"could not connect the egress proxy to {network}: {out[-400:]}"
    return await _install_rules(docker, subnet)


async def open_egress(docker: DockerConfig, network: str) -> str:
    """Let `network`'s containers reach public addresses, through the proxy."""
    problem = await ensure_proxy(docker)
    if problem:
        return problem
    subnet = await subnet_of(docker, network)
    if not laid_out(subnet, docker.sealed_pool):
        return (f"network {network} is not one of the sealed-network pool's "
                f"({subnet or 'no subnet'} is outside {docker.sealed_pool}), so the "
                "proxy cannot serve it; it was made by an older Fabrika and has to be "
                "made again")
    return await _join(docker, network, subnet)


async def close_egress(docker: DockerConfig, network: str) -> None:
    """Take the way out away again. Best effort: the network may be gone.

    The proxy's route and rule for the network's stand-ins stay. With the proxy
    off the network nothing on it can reach them, and a network made later on
    the same block wants exactly the same ones.
    """
    await _docker([docker.binary, "network", "disconnect", "--force", network, PROXY])


async def remove_sealed(docker: DockerConfig, name: str) -> None:
    await close_egress(docker, name)
    await _docker([docker.binary, "network", "rm", name])
    _subnets.pop(name, None)


def resolver_file(subnet: str) -> Path:
    """The resolver a container on `subnet` reads: Docker's first, for the
    names of its own stack, then the proxy's, for everything Docker refuses.

    Docker answers an outside name on an internal network with SERVFAIL, and
    both glibc and musl then ask the next nameserver -- measured, not assumed.
    With the proxy off the network the second one does not answer, and a
    lookup fails after the timeout below, as it should.
    """
    RESOLV_DIR.mkdir(parents=True, exist_ok=True)
    path = RESOLV_DIR / f"resolv-{ipaddress.ip_network(subnet).network_address}.conf"
    text = ("# Written by Fabrika. Docker's resolver answers this container's own\n"
            "# stack; the egress proxy answers the rest while the network is let out.\n"
            "nameserver 127.0.0.11\n"
            f"nameserver {proxy_address(subnet)}\n"
            "options ndots:0 timeout:1 attempts:2\n")
    if not path.exists() or path.read_text() != text:
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(text)
        tmp.replace(path)
    return path


def resolver_argv(subnet: str) -> list[str]:
    """The `docker run` arguments that give a container its resolver."""
    return ["--volume", f"{resolver_file(subnet)}:/etc/resolv.conf:ro"]


# -- proof -------------------------------------------------------------------

#: What `self_test` reaches, and how it knows it got there: a TLS handshake
#: with the certificate checked, which only the real host can complete, and an
#: SSH server's greeting, which no HTTP proxy could ever have carried.
_PROBE = r"""
import json, socket, ssl
def check(what, fn):
    try:
        print(json.dumps({"what": what, "ok": True, "detail": fn()}))
    except Exception as exc:
        print(json.dumps({"what": what, "ok": False, "detail": f"{type(exc).__name__}: {exc}"}))
def https():
    with socket.create_connection(("pypi.org", 443), timeout=15) as raw:
        with ssl.create_default_context().wrap_socket(raw, server_hostname="pypi.org") as tls:
            return f"pypi.org:443, certificate for {dict(x[0] for x in tls.getpeercert()['subject'])['commonName']}"
def ssh():
    with socket.create_connection(("github.com", 22), timeout=15) as s:
        return "github.com:22 said " + s.recv(64).decode().strip()
check("An outside name resolves", lambda: "pypi.org is " + socket.gethostbyname("pypi.org"))
check("HTTPS, with the real certificate", https)
check("SSH, on port 22", ssh)
"""
_last_test: dict = {}


async def self_test(docker: DockerConfig) -> dict:
    """Prove the way out works for a program that knows nothing about it.

    A throwaway sealed network, let out, and a container on it with no setting
    of any kind -- only the resolver every let-out container gets. It resolves
    an outside name, completes a TLS handshake with the real certificate, and
    reads GitHub's SSH greeting. The network is removed afterwards either way.
    """
    import time as _time
    import uuid as _uuid
    name = f"{PROXY}-selftest-{_uuid.uuid4().hex[:8]}"
    checks: list[dict] = []
    detail = ""
    try:
        problem = await create_sealed(docker, name) or await open_egress(docker, name)
        if problem:
            detail = problem
        else:
            code, out = await _docker([
                docker.binary, "run", "--rm", "--network", name,
                *resolver_argv(await subnet_of(docker, name)),
                "--entrypoint", "python", _image_tag(docker), "-c", _PROBE], timeout=120)
            for line in out.splitlines():
                try:
                    checks.append(json.loads(line))
                except ValueError:
                    continue
            if not checks:
                detail = out[-600:] or f"the test container exited {code} and said nothing"
    finally:
        await remove_sealed(docker, name)
    global _last_test
    _last_test = {"ok": bool(checks) and all(c.get("ok") for c in checks) and not detail,
                  "at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
                  "checks": checks, "detail": detail}
    return _last_test


