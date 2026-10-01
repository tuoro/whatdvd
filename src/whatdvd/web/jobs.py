"""进程内任务队列：同时运行的任务数受限，日志通过 SSE 推送给浏览器。

任务在工作线程中执行（处理流程是同步的外部命令调用），日志经 call_soon_threadsafe 回到事件循环。
任务只保存在内存中，服务重启后清空。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

JobStatus = Literal["queued", "running", "done", "failed"]


@dataclass
class Job:
    kind: str
    path: Path
    params: dict[str, Any]
    output_dir: Path
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: JobStatus = "queued"
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    events: list[dict[str, str]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    progress: float | None = None
    """0–1；None 表示无法估计（例如做种时 mktorrent 不报告进度）。"""
    files: frozenset[str] = frozenset()
    """允许通过 API 下载的文件名（位于 output_dir 中）。"""
    _changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def finished(self) -> bool:
        return self.status in ("done", "failed")

    def add_event(self, level: str, message: str) -> None:
        self.events.append({"level": level, "message": message})
        self.touch()

    def set_progress(self, value: float) -> None:
        self.progress = max(0.0, min(1.0, value))
        self.touch()

    def touch(self) -> None:
        """唤醒正在等待的 SSE 连接。"""
        self._changed.set()
        self._changed = asyncio.Event()

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "path": str(self.path),
            "params": self.params,
            "status": self.status,
            "progress": self.progress,
            "ok": None if self.result is None else self.result.get("ok"),
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }

    def detail(self) -> dict[str, Any]:
        return {**self.summary(), "result": self.result, "events": self.events}


class JobReporter:
    """在工作线程中使用，把日志转交给事件循环。"""

    def __init__(self, job: Job, loop: asyncio.AbstractEventLoop) -> None:
        self._job = job
        self._loop = loop

    def info(self, message: str) -> None:
        self._loop.call_soon_threadsafe(self._job.add_event, "info", message)

    def error(self, message: str) -> None:
        self._loop.call_soon_threadsafe(self._job.add_event, "error", message)

    def progress(self, done: int, total: int) -> None:
        self._loop.call_soon_threadsafe(self._job.set_progress, done / total if total else 1.0)


Worker = Callable[[Job, JobReporter], dict[str, Any]]
"""在工作线程中执行任务，返回结果（含 "ok" 和 "files"）；抛出异常表示任务失败。"""


class JobManager:
    def __init__(self, max_jobs: int = 1, keep: int = 50) -> None:
        self._limit = max_jobs
        self._running = 0
        self._slots = asyncio.Condition()
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._keep = keep
        self._tasks: set[asyncio.Task[None]] = set()

    def submit(self, job: Job, worker: Worker) -> Job:
        self._jobs[job.id] = job
        self._prune()
        task = asyncio.get_running_loop().create_task(self._run(job, worker))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def set_limit(self, max_jobs: int) -> None:
        """修改同时运行的任务数；调大时排队的任务立即开始，调小时正在运行的任务不受影响。"""
        async with self._slots:
            self._limit = max_jobs
            self._slots.notify_all()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return list(reversed(self._jobs.values()))

    async def wait_all(self) -> None:
        """测试用：等待所有任务结束。"""
        while self._tasks:
            await asyncio.gather(*list(self._tasks))

    def _prune(self) -> None:
        finished = [job_id for job_id, job in self._jobs.items() if job.finished]
        while len(self._jobs) > self._keep and finished:
            del self._jobs[finished.pop(0)]

    async def _run(self, job: Job, worker: Worker) -> None:
        async with self._slots:
            await self._slots.wait_for(lambda: self._running < self._limit)
            self._running += 1
        try:
            await self._execute(job, worker)
        finally:
            async with self._slots:
                self._running -= 1
                self._slots.notify_all()

    async def _execute(self, job: Job, worker: Worker) -> None:
        job.status = "running"
        job.touch()
        reporter = JobReporter(job, asyncio.get_running_loop())
        try:
            result = await asyncio.to_thread(worker, job, reporter)
        except Exception as error:  # noqa: BLE001 - 任何异常都记为任务失败并显示给用户
            await asyncio.sleep(0)  # 让线程里排队的日志先写入
            job.error = str(error) or type(error).__name__
            job.add_event("error", job.error)
            job.status = "failed"
        else:
            await asyncio.sleep(0)
            job.files = frozenset(result.pop("files", []))
            job.result = result
            job.status = "done"
            job.progress = 1.0
        job.finished_at = time.time()
        job.touch()


async def stream_events(job: Job, start: int = 0, keepalive: float = 15.0) -> AsyncIterator[str]:
    """SSE：先补发 start 之后的日志，再持续推送，任务结束时发送 end 事件。"""
    import json

    index = max(0, start)
    while True:
        changed = job._changed
        while index < len(job.events):
            data = json.dumps(job.events[index], ensure_ascii=False)
            index += 1
            yield f"id: {index}\nevent: log\ndata: {data}\n\n"
        if job.finished:
            yield f"event: end\ndata: {json.dumps(job.summary(), ensure_ascii=False)}\n\n"
            return
        try:
            await asyncio.wait_for(changed.wait(), timeout=keepalive)
        except TimeoutError:
            yield ": keepalive\n\n"
