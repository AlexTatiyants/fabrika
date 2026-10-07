"""Progress fan-out: every run's events, to every open console stream."""

from __future__ import annotations

import asyncio
import json
from typing import Any






class ProgressHub:
    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue] = set()
        self.recent: list[dict[str, Any]] = []
        self.closing = False
        self.loop: asyncio.AbstractEventLoop | None = None

    def publish(self, event: dict[str, Any]) -> None:
        """Deliver `event` to every open stream, from whichever thread has it.

        Half the callers are not on the event loop: a plain `def` route runs on
        a worker thread, and saving an answer or a project setting publishes
        from there. An asyncio queue fed from another thread neither wakes the
        stream waiting on it nor is safe to touch, so the event sat unseen until
        the stream's 15-second keep-alive happened to wake the loop. Off the
        loop, the delivery is handed to the loop instead.
        """
        loop = self.loop
        if loop is not None and not loop.is_closed():
            try:
                on_loop = asyncio.get_running_loop() is loop
            except RuntimeError:
                on_loop = False
            if not on_loop:
                loop.call_soon_threadsafe(self._deliver, event)
                return
        self._deliver(event)

    def _deliver(self, event: dict[str, Any]) -> None:
        self.recent.append(event)
        del self.recent[:-200]
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def close(self) -> None:
        """End every open stream so the process can actually exit.

        An SSE response is an *active* connection, and uvicorn's graceful
        shutdown waits on those with no timeout -- so a single console tab is
        enough to hang a Ctrl-C, or a --reload restart, until the browser gives
        up first. Without this the reloader kills the worker, the worker waits
        on the stream, and the stream waits on the browser.

        A subscriber that has fallen behind is exactly the one that must not be
        left holding the door, so the oldest event is dropped to make room for
        the sentinel. Dropping an event while shutting down costs nothing.
        """
        self.closing = True
        for q in list(self.subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover -- drained meanwhile
                    pass
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:  # pragma: no cover -- refilled meanwhile
                pass

    async def stream(self):
        if self.closing:
            return              # shutting down; don't hand out a new door to hold
        self.loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.subscribers.add(q)
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event is None:
                    return      # close(); the client's EventSource reconnects
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            self.subscribers.discard(q)
