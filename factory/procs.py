"""Stopping a subprocess that has run out of time.

`kill()` sends the signal and returns; the process is not gone until something
waits for it. A killed child nobody waits on stays a zombie for as long as the
server runs, and its transport is cleaned up whenever the garbage collector
gets to it -- after the event loop that owned it has closed, in a short-lived
one, which is an error raised into nobody. So a timeout kills and then waits,
briefly: a SIGKILLed process exits at once, and the wait only reaps it.
"""

from __future__ import annotations

import asyncio


async def kill(proc: asyncio.subprocess.Process, wait_s: float = 5.0) -> None:
    """Kill `proc` and wait for it to exit."""
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=wait_s)
    except asyncio.TimeoutError:
        pass
