"""The door into a preview: a TCP forwarder for a list of ports.

A preview's app runs on a sealed network, bound to `127.0.0.1` inside its own
container, and a person's browser is on this machine. Neither can reach the
other. Docker publishes no port for a container on an `--internal` network --
measured: `-p 127.0.0.1:P:P` on one, and curl from this machine could not
connect -- and a server bound to loopback is reachable only from inside its own
network namespace. So there are two hops, and this file is both of them:

- the inner hop shares the app container's network namespace and listens on
  that container's sealed-network address, forwarding to `127.0.0.1`;
- the door sits on a network of its own with the ports published on this
  machine's loopback, is connected to the sealed network, and forwards to the
  app container's address.

The port number is the same at every step, so the address a service was told it
has (`FACTORY_URL_<NAME>`) is the address the browser uses.

Standard library only, and run from Fabrika's own image, like the egress proxy:
no project code ever runs on either hop. It listens on exactly the ports it is
given and carries connections inward; it opens nothing else.

    python doorway.py --listen HOST --to HOST PORT [PORT ...]
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import sys
from typing import Sequence

_CHUNK = 65536


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(_CHUNK)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except (ConnectionError, OSError):  # pragma: no cover -- already gone
            pass


def _handler(to_host: str, port: int):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(to_host, port)
        except OSError:
            # Nothing listening on the far side yet, or any more. The client
            # sees its connection closed, which is what a refused one looks like.
            writer.close()
            return
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
    return handle


async def serve(listen: str, to_host: str, ports: Sequence[int]) -> list[asyncio.base_events.Server]:
    """Start one listener per port. Returned so a caller (a test) can close them.

    A port already taken on `listen` is skipped, not fatal. On the inner hop
    that means a service in this namespace listens on every address -- a
    Spring Boot API does by default -- so it is already reachable at `listen`
    and needs no forwarding. Treated as fatal, it took every other port down
    with it: a web page served on loopback was never forwarded because its
    API was already reachable, and the door closed each connection
    unanswered. Any other failure to listen is still fatal.
    """
    servers = []
    for port in ports:
        try:
            servers.append(await asyncio.start_server(_handler(to_host, port), listen, port))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            print(f"doorway: {listen}:{port} is already served here; not forwarded",
                  flush=True)
    return servers


async def _main(argv: Sequence[str]) -> None:
    parser = argparse.ArgumentParser(prog="doorway")
    parser.add_argument("--listen", required=True)
    parser.add_argument("--to", required=True)
    parser.add_argument("ports", nargs="+", type=int)
    args = parser.parse_args(argv)
    servers = await serve(args.listen, args.to, args.ports)
    forwarded = [s.sockets[0].getsockname()[1] for s in servers]
    print(f"doorway: {args.listen} -> {args.to} on {' '.join(map(str, forwarded)) or 'nothing'}",
          flush=True)
    # Up even with nothing to forward: every port was already served where it
    # is asked for, and a door whose container exits reads as a broken one.
    await asyncio.gather(*(s.serve_forever() for s in servers), asyncio.Event().wait())


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
