"""Background executor for heavy reports / OCR (phase 3 item 19c).

Model — *per-user serialization*:
* the polling thread handles every update inline, as before;
* a heavy step (report build, OCR) is handed to a small ThreadPoolExecutor after a
  «⏳ … در حال ساخت…» notice, so other users are not blocked;
* while a user has a job running, that user's further updates are queued and are
  processed IN ORDER by the same worker after the job — so each user's pending
  state is only ever touched by one thread at a time (thread-safe without
  locking every flow dict). Cross-user shared structures are per-user dict keys
  (GIL-atomic) or have their own locks (frame cache, permission cache);
* SQLite: ``Database.connect()`` opens a new connection per call → per-thread.
Disabled (fully synchronous) unless ``enable()`` is called — smoke tests and
scripts keep their synchronous behaviour; main.py enables it.
"""
from __future__ import annotations

import functools
import logging
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger(__name__)


class BackgroundRunner:
    def __init__(self) -> None:
        self.executor: ThreadPoolExecutor | None = None
        self.lock = threading.RLock()
        self.active: dict[str, deque] = {}
        self._local = threading.local()
        self._label_locks: dict[str, threading.Lock] = {}

    def enable(self, workers: int = 2) -> None:
        if self.executor is None:
            self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="heavy")

    def shutdown(self) -> None:
        if self.executor is not None:
            self.executor.shutdown(wait=True)
            self.executor = None

    def label_lock(self, label: str) -> threading.Lock:
        with self.lock:
            lk = self._label_locks.get(label)
            if lk is None:
                lk = self._label_locks[label] = threading.Lock()
            return lk

    @property
    def in_worker(self) -> bool:
        return bool(getattr(self._local, "in_worker", False))

    def try_queue(self, uid: str, item: Any) -> bool:
        """Main thread: queue ``item`` if ``uid`` has a running job (True = queued)."""
        if not uid or self.in_worker:
            return False
        with self.lock:
            q = self.active.get(uid)
            if q is None:
                return False
            q.append(item)
            return True

    def busy(self, uid: str) -> bool:
        with self.lock:
            return uid in self.active

    def submit(self, uid: str, job: Callable[[], Any], drain: Callable[[Any], None], on_error: Callable[[BaseException], None]) -> bool:
        """Run ``job`` in the pool (False = run it inline: disabled / already in a worker)."""
        if self.executor is None or self.in_worker or not uid:
            return False
        with self.lock:
            if uid in self.active:
                return False
            self.active[uid] = deque()

        def run() -> None:
            self._local.in_worker = True
            try:
                try:
                    job()
                except Exception as exc:  # noqa: BLE001
                    on_error(exc)
                while True:
                    with self.lock:
                        q = self.active.get(uid)
                        if not q:
                            self.active.pop(uid, None)
                            return
                        nxt = q.popleft()
                    try:
                        drain(nxt)
                    except Exception as exc:  # noqa: BLE001
                        on_error(exc)
            finally:
                self._local.in_worker = False
                with self.lock:
                    if uid in self.active and not self.active[uid]:
                        self.active.pop(uid, None)

        self.executor.submit(run)
        return True


def heavy(label: str, *, notice: bool = True, when: Callable[..., bool] | None = None):
    """Method decorator for BotApp: ``label`` is shown in «⏳ در حال ساخت {label}…»."""

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(self, message: dict, *a: Any, **k: Any) -> Any:
            if when is not None and not when(self, message, *a, **k):
                return fn(self, message, *a, **k)
            return self._heavy(message, label, notice, functools.partial(fn, self, message, *a, **k))

        return wrapper

    return deco
