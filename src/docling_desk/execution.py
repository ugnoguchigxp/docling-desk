"""Admission slots for background work: one owner per slot, released exactly once.

This module owns only the slot. What a feature does when work is interrupted,
resumed or of unknown outcome stays with that feature's manager. It adds no
shared state enum and never retries.

Two events are kept apart on purpose:

* a stop request (``SlotLease.request_stop``) asks the work to end. It never
  returns the slot by itself;
* execution completion returns the slot: the submitted callable has returned or
  raised, or its queued future was cancelled before it started.

A provider that ignores its cancel hook therefore keeps its slot until it stops.

``SlotPool.in_use`` counts this process only. It is for admission limits and
tests; it is never evidence that no other process is writing, so a backup must
not be refused or allowed from it (see the activity contract in the R14 report).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Executor, Future

LOGGER = logging.getLogger(__name__)


class LeaseReleased(RuntimeError):
    """The lease was returned before its work could be submitted."""


class SlotPool:
    """A bounded, non-blocking admission counter."""

    def __init__(self, size: int):
        self.size = size
        self._in_use = 0
        self._lock = threading.Lock()

    @property
    def in_use(self) -> int:
        with self._lock:
            return self._in_use

    def acquire(self, blocking: bool = False) -> bool:
        """Raw slot; the caller must call ``release`` once. Prefer ``claim``."""
        if blocking:
            raise ValueError("SlotPool admission never blocks.")
        with self._lock:
            if self._in_use >= self.size:
                return False
            self._in_use += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._in_use <= 0:
                raise ValueError("SlotPool released too many times")
            self._in_use -= 1

    def claim(self) -> SlotLease | None:
        """Take one slot as a lease that returns it exactly once, or None when full."""
        return SlotLease(self) if self.acquire() else None


class SlotLease:
    """Ownership of one slot, from admission until the work has really ended.

    States: claimed -> submitted -> released, or claimed -> released. Every
    release path goes through ``release``, which acts once.
    """

    def __init__(self, pool: SlotPool):
        self._pool = pool
        self._lock = threading.Lock()
        self._submitted = False
        self._released = False
        self._future: Future | None = None
        self.stop_requested = threading.Event()

    @property
    def released(self) -> bool:
        with self._lock:
            return self._released

    def release(self) -> bool:
        """Return the slot. True only for the call that actually returned it."""
        with self._lock:
            if self._released:
                return False
            self._released = True
        self._pool.release()
        return True

    def abandon(self) -> bool:
        """Return the slot unless the work was handed to an executor.

        Request handlers call this on every exit path. A submitted lease is
        owned by its execution and is left alone, so a handler failing after
        submission cannot free the slot of work that is still queued or running.
        """
        with self._lock:
            if self._submitted or self._released:
                return False
            # Decided and marked in one critical section: a concurrent ``submit``
            # now sees the lease released and refuses, never running without a slot.
            self._released = True
        self._pool.release()
        return True

    def submit(self, executor: Executor, fn: Callable, /, *args, **kwargs) -> Future:
        """Run ``fn`` on the executor; the slot returns when ``fn`` ends or is cancelled.

        If the executor refuses the work (shut down, broken) the slot is returned
        and the error propagates: nothing was accepted. Call this after every
        other step that can fail, because once it returns the work is accepted.
        """
        with self._lock:
            if self._released or self._submitted:
                raise LeaseReleased("The slot is no longer available for this work.")
            self._submitted = True
        try:
            future = executor.submit(self._execute, fn, args, kwargs)
        except BaseException:
            self.release()
            raise
        # Test doubles may run the work inline and return anything.
        if isinstance(future, Future):
            self._future = future
            # Covers work cancelled while queued, which never reaches _execute.
            future.add_done_callback(self._finished)
        return future

    def request_stop(self, cancel: Callable[[], object] | None = None) -> bool:
        """Ask the work to stop; True when it was still queued and will never run.

        Never returns the slot for work that already started: the slot comes
        back when the callable does. A queued future that is cancelled releases
        through its done callback.
        """
        self.stop_requested.set()
        if cancel is not None:
            cancel()
        future = self._future
        return future is not None and future.cancel()

    def _finished(self, future: Future) -> None:
        """Done callback: nobody reads the Future, so log what the work raised.

        The slot is returned even if logging itself fails.
        """
        try:
            if not future.cancelled():
                error = future.exception()
                if error is not None:
                    LOGGER.error(
                        "Background work ended with an unhandled exception",
                        exc_info=(type(error), error, error.__traceback__),
                    )
        except Exception:  # pragma: no cover - logging must never keep the slot
            pass
        finally:
            self.release()

    def _execute(self, fn: Callable, args: tuple, kwargs: dict):
        try:
            return fn(*args, **kwargs)
        finally:
            self.release()
