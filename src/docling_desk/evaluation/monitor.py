"""Sample server RSS. Short peaks between samples can be missed. The sum can double-count shared pages."""

from __future__ import annotations

import threading
import time

import psutil


class RssSampler:
    def __init__(self, pid: int, interval: float = 0.05):
        self.proc = psutil.Process(pid)
        self.interval = interval
        self.phase = "prepare"
        self.samples: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.mark(self.phase)
        self._thread.start()

    def mark(self, phase: str) -> None:
        with self._lock:
            self.phase = phase
            self.samples.append(self._snapshot())

    def _snapshot(self) -> dict:
        processes = [self.proc]
        try:
            processes.extend(self.proc.children(recursive=True))
        except psutil.Error:
            pass
        rss = 0
        alive = 0
        for process in processes:
            try:
                rss += process.memory_info().rss
                alive += 1
            except psutil.Error:
                continue
        return {
            "phase": self.phase,
            "rss_bytes_sum": rss,
            "process_count": alive,
            "monotonic": time.monotonic(),
        }

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            with self._lock:
                self.samples.append(self._snapshot())

    def stop(self) -> dict:
        self._stop.set()
        self._thread.join(timeout=2)
        with self._lock:
            self.samples.append(self._snapshot())
            return self.summary()

    def summary(self) -> dict:
        def peak(phase: str) -> int | None:
            values = [item["rss_bytes_sum"] for item in self.samples if item["phase"] == phase]
            return max(values) if values else None

        return {
            "interval_seconds": self.interval,
            "targets": "evaluation server process and recursive children",
            "client_process_included": False,
            "sample_count": len(self.samples),
            "prepare_max_rss_bytes": peak("prepare"),
            "measure_max_rss_bytes": peak("measure"),
            "max_process_count": max((item["process_count"] for item in self.samples), default=0),
            "limits": [
                "サンプリング間隔より短いピークは取りこぼすことがある。",
                "RSSの合計は共有ページを重複計上し得るため、物理メモリ消費そのものではない。",
                "変換子プロセスがいない段階の観測であり、変換を含む全体のRSSではない。",
            ],
        }
