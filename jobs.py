"""
jobs.py — Background pipeline jobs that outlive WebSocket connections.

A job is an asyncio task running ``run_pipeline`` for one run. Clients (WebSockets) only
*subscribe* to it: closing the browser tab, losing Wi-Fi or switching machine does NOT stop
the job. A client can re-attach later (``attach``) and gets:
    1. a snapshot of the current state (spec, files, progress, token stats), and
    2. the log / agent events it missed (by sequence number).

Every checkpoint is written to SQLite, so even if the server process dies the run can be
continued with ``resume`` (the API key has to be supplied again — it is never stored).
A run stopped because the *server shut down* gets status "interrupted" (clients resume it
automatically); a run stopped by the user gets "stopped".
"""

import asyncio
import json
import logging
import shutil
import tempfile
from collections import deque
from typing import Awaitable, Callable, Deque, Dict, List, Optional, Set

from pipeline import run_pipeline
from store import get_store

logger = logging.getLogger(__name__)

LOG_BUFFER_MAX = 2000
# Events that get a sequence number, are replayed to late clients and stored in SQLite.
PERSISTED_EVENT_TYPES = ("log", "error", "agent")

Push = Callable[[dict], Awaitable[None]]


class Subscriber:
    """One attached client. Events are queued and delivered strictly in order."""

    def __init__(self, push: Push) -> None:
        self.push = push
        self.queue: Deque[dict] = deque()
        self.lock = asyncio.Lock()
        self.dead = False

    async def drain(self) -> None:
        async with self.lock:
            while self.queue and not self.dead:
                event = self.queue.popleft()
                try:
                    await self.push(event)
                except Exception:
                    self.dead = True
                    self.queue.clear()


def build_snapshot(run_id: str, status: str, state: dict) -> List[dict]:
    """Events that bring a (re)connecting client up to date with *state*."""
    events: List[dict] = [{
        "type": "run_info", "run_id": run_id, "status": status,
        "stage": state.get("stage"), "command": state.get("command", ""),
        "ok": state.get("ok", False), "paused": state.get("paused"),
        "round": (state.get("coder") or {}).get("round", 0),
        "files": len(state.get("work") or {}),
    }]
    if state.get("spec"):
        events.append({"type": "spec", "content": state["spec"]})
    if state.get("work"):
        events.append({"type": "code", "files": dict(state["work"])})
    last = state.get("last") or {}
    if last.get("progress"):
        events.append(last["progress"])
    if last.get("tokens"):
        events.append(last["tokens"])
    return events


class Job:
    def __init__(self, run_id: str, user_id: str, state: dict,
                 seq: int = 0, buf: Optional[List[dict]] = None) -> None:
        self.run_id = run_id
        self.user_id = user_id
        self.state = state
        self.task: Optional[asyncio.Task] = None
        self.subs: Set[Subscriber] = set()
        self.seq = seq
        self.buf: List[dict] = buf or []
        self._bg: Set[asyncio.Task] = set()

    # Used as the pipeline's ``send_event``.
    async def emit(self, event: dict) -> None:
        persist = event.get("type") in PERSISTED_EVENT_TYPES
        if persist:
            self.seq += 1
            event = {**event, "seq": self.seq}
            self.buf.append(event)
            if len(self.buf) > LOG_BUFFER_MAX:
                del self.buf[:len(self.buf) - LOG_BUFFER_MAX]

        # Broadcast first, synchronously: attach() can then never see an event twice.
        for sub in list(self.subs):
            if sub.dead:
                self.subs.discard(sub)
                continue
            sub.queue.append(event)
            self._spawn(sub.drain())

        if persist:
            try:
                await asyncio.to_thread(get_store().add_event, self.run_id, self.seq, event)
            except Exception:
                logger.exception("Could not persist event for %s", self.run_id)

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    def attach(self, push: Push, after_seq: int = 0) -> Subscriber:
        """Subscribe a client. Snapshot + missed events are queued before any live event."""
        sub = Subscriber(push)
        sub.queue.extend(build_snapshot(self.run_id, "running", self.state))
        sub.queue.extend(e for e in self.buf if e.get("seq", 0) > after_seq)
        self.subs.add(sub)
        self._spawn(sub.drain())
        return sub

    def detach(self, sub: Optional[Subscriber]) -> None:
        if sub:
            sub.dead = True
            self.subs.discard(sub)

    @property
    def running(self) -> bool:
        return bool(self.task and not self.task.done())


class JobManager:
    def __init__(self) -> None:
        self.jobs: Dict[str, Job] = {}
        self.closing = False        # True while the server shuts down

    def get(self, run_id: Optional[str]) -> Optional[Job]:
        return self.jobs.get(run_id) if run_id else None

    async def start(self, run_id: str, user_id: str, state: dict, api_key: str) -> Job:
        """Start (or continue, if *state* is a checkpoint) a run in the background."""
        existing = self.jobs.get(run_id)
        if existing and existing.running:
            return existing
        store = get_store()
        seq = await asyncio.to_thread(store.max_seq, run_id)
        buf = await asyncio.to_thread(store.get_events, run_id, max(0, seq - LOG_BUFFER_MAX))
        existing = self.jobs.get(run_id)            # re-check: we awaited above
        if existing and existing.running:
            return existing
        job = Job(run_id, user_id, state, seq=seq, buf=buf)
        self.jobs[run_id] = job
        job.task = asyncio.create_task(self._runner(job, api_key))
        return job

    async def stop(self, run_id: str) -> bool:
        job = self.jobs.get(run_id)
        if job and job.running:
            job.task.cancel()
            return True
        return False

    async def shutdown(self) -> None:
        """Server shutting down: stop every job but let each save its checkpoint first."""
        self.closing = True
        tasks = []
        for job in list(self.jobs.values()):
            if job.running:
                job.task.cancel()
                tasks.append(job.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ── the actual runner ───────────────────────────────────────────────────
    async def _runner(self, job: Job, api_key: str) -> None:
        store = get_store()
        state = job.state
        run_id = job.run_id
        workdir = tempfile.mkdtemp(prefix=f"agent_{run_id}_")
        status = "error"

        async def checkpoint(st: dict) -> None:
            # Serialise on the event loop (state is only mutated here), write in a thread.
            data = json.dumps(st, ensure_ascii=False)
            await asyncio.to_thread(store.save_state, run_id, data, "running")

        try:
            await checkpoint(state)
            await run_pipeline(state, api_key, job.emit, checkpoint, workdir)
            if state.get("paused") == "budget":
                status = "paused_budget"
            else:
                status = "done" if state.get("ok") else "failed"

        except asyncio.CancelledError:
            if self.closing:
                status = "interrupted"
                msg = "⏸ Server đang khởi động lại — tiến độ đã được lưu, sẽ tự chạy tiếp."
            else:
                status = "stopped"
                msg = "⏹️ Đã dừng — tiến độ đã được lưu, bạn có thể tiếp tục."
            await job.emit({"type": "log", "role": "system", "message": msg})

        except Exception as exc:  # run_pipeline already reported it via an 'error' event
            logger.error("Run %s failed: %s", run_id, exc)
            status = "error"

        finally:
            try:
                data = json.dumps(state, ensure_ascii=False)
                await asyncio.shield(asyncio.to_thread(store.save_state, run_id, data, status))
            except BaseException:
                logger.exception("Could not save final state of %s", run_id)
            shutil.rmtree(workdir, ignore_errors=True)
            await job.emit({"type": "run_status", "run_id": run_id, "status": status,
                            "ok": bool(state.get("ok"))})
            if self.jobs.get(run_id) is job:
                self.jobs.pop(run_id, None)
