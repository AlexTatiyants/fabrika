"""The one way out of a sealed environment: public addresses, nothing else.

Runs inside its own container (`egress.py` starts it), attached to the default
bridge for its own way out and to each sealed network whose containers are
allowed the internet. Nothing inside those containers is told it exists.

An HTTP proxy announced through the `HTTP(S)_PROXY` variables is not enough,
because a variable is a convention each program decides whether to honour.
npm, pip and curl do. The JVM does not, so a Maven project's first check dies
on `Unknown host repo.maven.apache.org` before compiling a line, and git over
SSH, Gradle, and any client that opens its own socket die the same way. A fix
per stack is a list that never ends.

So the network does the telling, in two parts that live here:

- **Names.** Every sealed container's resolver asks Docker first and this
  process second. Docker answers the names of the container's own stack and
  refuses every outside name, so outside names land here -- and each one is
  answered with a *stand-in*: an address on the container's own network,
  in a block this process owns. Nothing is asked of any real DNS server.
- **Connections.** Whatever connects to a stand-in, on whatever port, is
  redirected to one listener. The kernel says which stand-in and port it was
  meant for; the stand-in says which name that was. From there the name is
  resolved *here*, every address it resolves to is
  checked, and the connection is made to the address that was checked.

It forwards to any *public* address and refuses the rest -- loopback, private
ranges, link-local, and Docker Desktop's range for the host machine. Refusing
by name would not be enough: a name is whatever DNS says it is today.

Every connection it forwards or refuses is one JSON line on stdout, which is
the container's log: `egress.status` reads it back for the console, so what an
agent tried to reach is something a person can look at rather than infer.

Standard library only. The network layout -- which addresses on a sealed
network are Docker's, which one is this process's and which are stand-ins --
is defined here and imported by `egress.py`, so the two cannot disagree.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import struct
import sys
import time
from collections import OrderedDict

#: Docker Desktop reaches the host machine through this range, and it is not
#: only private by the book -- it is the specific hole this proxy exists to
#: close. Named on its own so that a change to what `is_global` means cannot
#: quietly reopen it.
HOST_RANGES = (ipaddress.ip_network("192.168.65.0/24"),)

# -- the layout of a sealed network ------------------------------------------
#
# Every sealed network is a /22 out of one pool, split the same way:
#
#   first /24      Docker's to hand out (`--ip-range`), gateway included
#   .1.1           this process, at a fixed address, so a resolver file can
#                  name it before the container that reads the file exists
#   top /23        stand-ins: addresses this process answers for
#
# A stand-in is on the container's own subnet, so the container reaches it with
# no route and no gateway: nothing has to be granted to what runs inside.

POOL = os.environ.get("EGRESS_POOL", "10.212.0.0/14")
BLOCK_PREFIX = 22
TRANSPARENT_PORT = int(os.environ.get("EGRESS_TRANSPARENT_PORT", "15001"))
DNS_PORT = int(os.environ.get("EGRESS_DNS_PORT", "53"))
#: Short, because a stand-in lives only as long as this process does: a
#: resolver holding one across a restart would connect to an address nobody
#: remembers handing out.
DNS_TTL = 30
CONNECT_TIMEOUT_S = 20.0


def blocks(pool: str = POOL) -> list[ipaddress.IPv4Network]:
    return list(ipaddress.ip_network(pool).subnets(new_prefix=BLOCK_PREFIX))


def block_of(address: str, pool: str = POOL) -> ipaddress.IPv4Network | None:
    """The sealed network an address is on, if it is one of the pool's."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address):
        ip = ip.ipv4_mapped or ip
    net = ipaddress.ip_network(pool)
    if not isinstance(ip, ipaddress.IPv4Address) or ip not in net:
        return None
    return ipaddress.ip_network(f"{ip}/{BLOCK_PREFIX}", strict=False)


def container_range(subnet: str) -> str:
    net = ipaddress.ip_network(subnet)
    return f"{net.network_address}/24"


def proxy_address(subnet: str) -> str:
    return str(ipaddress.ip_network(subnet).network_address + 257)


def stand_in_block(subnet: str) -> str:
    net = ipaddress.ip_network(subnet)
    return f"{net.network_address + 512}/23"


def laid_out(subnet: str, pool: str = POOL) -> bool:
    """Whether a network's subnet is one of the pool's blocks, split as above."""
    try:
        net = ipaddress.ip_network(subnet)
    except ValueError:
        return False
    return net.prefixlen == BLOCK_PREFIX and net in blocks(pool)


# -- the policy --------------------------------------------------------------

def public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not any(ip in net for net in HOST_RANGES)


def describe(address: str) -> str:
    """What a refused address is, in words a person reading the log can use."""
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if any(ip in net for net in HOST_RANGES):
        return "Docker's address for this machine"
    if ip.is_loopback:
        return "a loopback address"
    if ip.is_link_local:
        return "a link-local address"
    if ip.is_private:
        return "a private network address"
    return "a reserved address"


async def resolve(host: str, port: int) -> tuple[str, str]:
    """An address to connect to, or why there is none."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return "", f"{host} does not resolve: {exc}"
    addresses = [info[4][0] for info in infos]
    refused = [a for a in addresses if not public(a)]
    if not addresses:
        return "", f"{host} does not resolve"
    if refused:
        # All or nothing: a name that resolves to one public and one private
        # address is the shape of a rebinding trick, and there is no reason a
        # real provider or registry would ever answer that way.
        what = f"{describe(refused[0])}, not a public address"
        if refused[0] == host.strip("[]"):
            return "", f"{host} is {what}"
        return "", f"{host} resolves to {refused[0]}: {what}"
    return addresses[0], ""


# -- stand-ins ---------------------------------------------------------------

class StandIns:
    """Which name each stand-in address was handed out for, per network.

    The same name gets the same stand-in for as long as it is in use, so a
    client that looks a name up twice connects to one address. When a
    network's block is full, the name asked for least recently gives its
    stand-in up -- 510 names is far more than a build reaches, and a stale one
    fails as a refusal in the log rather than as a connection to the wrong
    place.
    """

    def __init__(self) -> None:
        self._names: dict[str, OrderedDict[str, str]] = {}
        self._addresses: dict[str, str] = {}

    def address_for(self, subnet: str, name: str) -> str:
        names = self._names.setdefault(subnet, OrderedDict())
        if name in names:
            names.move_to_end(name)
            return names[name]
        block = ipaddress.ip_network(stand_in_block(subnet))
        usable = block.num_addresses - 2
        if len(names) < usable:
            taken = set(names.values())
            address = next(str(block.network_address + i) for i in range(1, usable + 1)
                           if str(block.network_address + i) not in taken)
        else:
            _, address = names.popitem(last=False)
            self._addresses.pop(address, None)
        names[name] = address
        self._addresses[address] = name
        return address

    def name_for(self, address: str) -> str | None:
        return self._addresses.get(address)


STAND_INS = StandIns()


# -- names -------------------------------------------------------------------

#: Names that are never on the internet, whatever a resolver is asked. Refusing
#: them here costs nothing and keeps a metadata endpoint or a reverse lookup
#: from being turned into a stand-in and then a log line about a failure.
LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".arpa", ".localdomain",
                  ".home", ".lan", ".test", ".invalid")

A, AAAA, ANY = 1, 28, 255
NOERROR, FORMERR, NXDOMAIN, NOTIMP, REFUSED = 0, 1, 3, 4, 5


def parse_query(packet: bytes) -> tuple[int, int, str, int, bytes] | None:
    """(id, flags, name, type, question) out of one DNS query, or None.

    One question, uncompressed -- which is every query a stub resolver sends.
    Anything else is not answered at all rather than guessed at.
    """
    if len(packet) < 12:
        return None
    ident, flags, qdcount = struct.unpack("!HHH", packet[:6])
    if flags & 0x8000 or qdcount != 1:
        return None
    labels: list[str] = []
    at = 12
    while True:
        if at >= len(packet):
            return None
        length = packet[at]
        if length == 0:
            at += 1
            break
        if length & 0xC0 or at + 1 + length > len(packet):
            return None
        labels.append(packet[at + 1:at + 1 + length].decode("ascii", "replace"))
        at += 1 + length
    if at + 4 > len(packet):
        return None
    qtype, _qclass = struct.unpack("!HH", packet[at:at + 4])
    return ident, flags, ".".join(labels).lower().rstrip("."), qtype, packet[12:at + 4]


def answer(packet: bytes, client: str, stand_ins: StandIns = STAND_INS,
           pool: str = POOL) -> bytes | None:
    """The reply to one query from `client`, or None for no reply at all."""
    query = parse_query(packet)
    if query is None:
        return None
    ident, flags, name, qtype, question = query
    opcode = (flags >> 11) & 0xF
    subnet = block_of(client, pool)

    def reply(rcode: int, records: bytes = b"", count: int = 0) -> bytes:
        out_flags = 0x8000 | (opcode << 11) | (flags & 0x0100) | 0x0080 | rcode
        return struct.pack("!HHHHHH", ident, out_flags, 1, count, 0, 0) + question + records

    if subnet is None:
        # Only the pool's networks are served. The default bridge can reach
        # this port too, and nothing on it has any use for a stand-in.
        return reply(REFUSED)
    if opcode != 0:
        return reply(NOTIMP)
    if "." not in name or name.endswith(LOCAL_SUFFIXES) or name in ("localhost",):
        return reply(NXDOMAIN)
    if qtype not in (A, ANY):
        # No AAAA: the sealed networks are IPv4 only, and an empty answer sends
        # a client straight to the A record rather than to a timeout.
        return reply(NOERROR)
    address = stand_ins.address_for(str(subnet), name)
    record = struct.pack("!HHHIH", 0xC00C, A, 1, DNS_TTL, 4) + socket.inet_aton(address)
    return reply(NOERROR, record, 1)


class _Datagrams(asyncio.DatagramProtocol):
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        out = answer(data, addr[0])
        if out is not None:
            self.transport.sendto(out, addr)  # type: ignore[attr-defined]


async def _dns_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """DNS over TCP: the same answers, each prefixed with its length."""
    peer = writer.get_extra_info("peername")
    try:
        while True:
            size = struct.unpack("!H", await reader.readexactly(2))[0]
            out = answer(await reader.readexactly(size), peer[0] if peer else "")
            if out is None:
                break
            writer.write(struct.pack("!H", len(out)) + out)
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        pass
    finally:
        writer.close()


# -- connections -------------------------------------------------------------

#: `SO_ORIGINAL_DST` from linux/netfilter_ipv4.h: where a redirected connection
#: was going before the redirect sent it here.
SO_ORIGINAL_DST = 80


def original_destination(sock: object) -> tuple[str, int] | None:
    try:
        raw = sock.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return None
    port, address = struct.unpack("!2xH4s8x", raw)
    return socket.inet_ntoa(address), port


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


def note(event: str, writer: asyncio.StreamWriter, target: str, why: str = "") -> None:
    """One line of the log `egress.status` reads. The client is an address on a
    sealed network; which container that was is worked out when it is read."""
    peer = writer.get_extra_info("peername")
    line = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event,
            "client": peer[0] if peer else "", "target": target}
    if why:
        line["why"] = why
    print(json.dumps(line), flush=True)


async def carry(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    """One connection a sealed container made to a stand-in, carried out.

    Nothing is read from the connection before it is forwarded, so it does not
    matter what protocol it speaks: TLS goes through as TLS, unopened, with the
    client checking the real server's certificate against the name it asked for.
    """
    dest = original_destination(client_w.get_extra_info("socket"))
    if dest is None:
        note("refused", client_w, "", "reached the listener directly, not through a stand-in")
        client_w.close()
        return
    address, port = dest
    name = STAND_INS.name_for(address)
    if name is None:
        note("refused", client_w, f"{address}:{port}",
             f"{address} is not a stand-in this proxy handed out; a name looked up before "
             "the proxy restarted has to be looked up again")
        client_w.close()
        return
    where = f"{name}:{port}"
    real, why = await resolve(name, port)
    if why:
        note("refused", client_w, where, why)
        client_w.close()
        return
    try:
        up_r, up_w = await asyncio.wait_for(
            asyncio.open_connection(real, port), timeout=CONNECT_TIMEOUT_S)
    except (OSError, asyncio.TimeoutError) as exc:
        # A public address that did not answer is not a refusal: nothing was
        # kept out, the far end was simply not there. Logged apart, so the
        # console's count of refusals means only what the rule stopped.
        note("failed", client_w, where, f"{where}: {exc or 'timed out'}")
        client_w.close()
        return
    note("forwarded", client_w, where)
    await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))


async def main() -> None:
    loop = asyncio.get_running_loop()
    server = await asyncio.start_server(carry, "0.0.0.0", TRANSPARENT_PORT)
    await loop.create_datagram_endpoint(_Datagrams, local_addr=("0.0.0.0", DNS_PORT))
    dns_tcp = await asyncio.start_server(_dns_stream, "0.0.0.0", DNS_PORT)
    print(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "event": "listening", "port": TRANSPARENT_PORT, "dns": DNS_PORT,
                      "pool": POOL}), flush=True)
    async with server, dns_tcp:
        await asyncio.gather(server.serve_forever(), dns_tcp.serve_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
