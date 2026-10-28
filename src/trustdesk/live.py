"""Live updates of the Requests page: Postgres `tcn` -> one LISTEN connection -> Server-Sent Events.

`tcn` (a contrib extension on AgentPG) is installed by migration 006 as a trigger on `request` that runs
`NOTIFY request_changes, '"request",U,"id"=''12'''` after every insert, update or delete. The app keeps ONE database connection that
LISTENs and fans each notification out to every connected browser (`GET /api/requests/events`). A burst is collapsed: browsers only
need to know "something changed" and then re-read the list and the counts through the normal API (so the permission checks and
filters are the ordinary ones, and no row data travels through the event).

Everything degrades. Without the extension no notification ever arrives, the stream says `live: false` and the page keeps its poll;
if the LISTEN connection drops it reconnects with a back-off and tells the browsers to refresh (they may have missed a change).
"""
import asyncio
import contextlib
import json
import threading
import time
from collections.abc import AsyncIterator, Callable

import psycopg

CHANNEL = "request_changes"
HEARTBEAT = 15.0          # seconds between keep-alive comments (proxies close idle streams)
MAX_SUBSCRIBERS = 200
TRIGGER = "request_tcn"


def trigger_installed(conn) -> bool:
    row = conn.execute("SELECT 1 FROM pg_trigger WHERE tgname = %s AND tgrelid = to_regclass('public.request')",
                       (TRIGGER,)).fetchone()
    return row is not None


class Hub:
    """Fans notifications out to subscribers (asyncio queues owned by the server's event loop)."""

    def __init__(self) -> None:
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.Lock()
        self.live = False          # the LISTEN connection is up AND the trigger exists
        self.seq = 0

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue | None:
        with self._lock:
            if len(self._subs) >= MAX_SUBSCRIBERS:
                return None
            q: asyncio.Queue = asyncio.Queue(maxsize=1)      # "something changed" is a level, not a log: one pending is enough
            self._subs.add((loop, q))
            return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = {s for s in self._subs if s[1] is not q}

    def count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, payload: str = "") -> None:
        """Thread-safe. Called from the LISTEN thread (or a test)."""
        with self._lock:
            self.seq += 1
            subs = list(self._subs)
        for loop, q in subs:
            def put(q=q) -> None:
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(payload)
            with contextlib.suppress(RuntimeError):          # that browser's loop is gone
                loop.call_soon_threadsafe(put)


hub = Hub()


class Listener(threading.Thread):
    """One background connection that LISTENs and publishes to `hub`. Reconnects forever until `stop()`."""

    def __init__(self, connect: Callable[[], psycopg.Connection], target: Hub = hub, channel: str = CHANNEL) -> None:
        super().__init__(name="request-listener", daemon=True)
        self.connect, self.hub, self.channel = connect, target, channel
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        delay = 1.0
        while not self._halt.is_set():
            try:
                with self.connect() as conn:
                    conn.autocommit = True
                    conn.execute(f"LISTEN {self.channel}")
                    self.hub.live = trigger_installed(conn)
                    if delay > 1.0:
                        self.hub.publish("reconnected")      # changes may have been missed while the connection was down
                    delay = 1.0
                    while not self._halt.is_set():
                        for n in conn.notifies(timeout=1.0):
                            self.hub.publish(n.payload)
                        # notifies() returns after the timeout: a dead connection raises on the next use
                        conn.execute("SELECT 1")
            except Exception:  # noqa: BLE001 - the database may be asleep or restarting: the poll in the browser covers the gap
                self.hub.live = False
                if self._halt.wait(delay):
                    return
                delay = min(delay * 2, 30.0)
        self.hub.live = False


# A comment line of about 2 KB, sent before anything else: proxies that buffer a response until a few KB arrive (some ingresses do, whatever
# X-Accel-Buffering says) then flush the first frame at once. SSE clients ignore comment lines, so it is harmless everywhere else.
PADDING = ": " + " " * 2048 + "\n"


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def stream(target: Hub = hub, is_disconnected: Callable[[], "asyncio.Future | bool"] | None = None,
                 heartbeat: float = HEARTBEAT) -> AsyncIterator[str]:
    """The SSE body. First frame: `hello {live}`; then `changed` on every notification; `: ping` comments as keep-alives.
    Ends when the client disconnects or the server shuts down."""
    q = target.subscribe(asyncio.get_running_loop())
    if q is None:
        yield _frame("busy", {"live": False})
        return
    try:
        yield PADDING + "retry: 5000\n\n" + _frame("hello", {"live": target.live})
        while True:
            if is_disconnected is not None:
                gone = is_disconnected()
                if asyncio.iscoroutine(gone) or isinstance(gone, asyncio.Future):
                    gone = await gone
                if gone:
                    return
            try:
                await asyncio.wait_for(q.get(), timeout=heartbeat)
            except TimeoutError:
                yield f": ping {int(time.time())}\n\n"
                continue
            yield _frame("changed", {"live": target.live})
    finally:
        target.unsubscribe(q)
