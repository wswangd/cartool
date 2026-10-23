"""收发工作线程：接收泵 + 定时发送调度。

所有 CAN 收发都在这个线程里进行，界面线程只投递请求，避免卡顿和并发调用 DLL。
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from .bus import BusBackend
from .frame import RX, TX, CanFrame, TraceRecord
from .zlgcan_api import ZlgError

PROGRESS_INTERVAL = 0.1
STATUS_INTERVAL = 0.5
FAILURE_INTERVAL = 1.0


@dataclass
class PeriodicTask:
    uid: int
    frame: CanFrame
    period_ms: int
    remaining: int          # <=0 表示无限
    next_due: float
    sent: int = 0


@dataclass
class SequenceTask:
    entries: list[tuple[int, CanFrame, int]]
    repeats: int
    next_due: float
    interval_ms: int
    cycle_interval_ms: int
    index: int = 0
    cycles: int = 0


class BusWorker(QThread):
    records = Signal(object)    # list[TraceRecord]
    progress = Signal(object)   # dict[uid, sent]
    task_done = Signal(int)     # uid，有限次数发完
    sequence_done = Signal()
    sequence_progress = Signal(int, int)
    failure = Signal(str)
    status = Signal(object)     # dict | None
    counters = Signal(int, int, int)   # 累计发送、累计接收、错误帧

    def __init__(self, bus: BusBackend, parent=None) -> None:
        super().__init__(parent)
        self._bus = bus
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._immediate: deque[CanFrame] = deque()
        self._periodic: dict[int, PeriodicTask] = {}
        self._sequence: SequenceTask | None = None
        self._seq = itertools.count(1)
        self._tx_total = 0
        self._rx_total = 0
        self._err_total = 0
        self._last_progress = 0.0
        self._last_status = 0.0
        self._last_failure = 0.0
        self._pending_progress: dict[int, int] = {}
        self._next_immediate_due: float = 0.0  # 即时发送间隔控制

    # ------------------------------------------------------------ 界面侧接口

    def send_now(self, frames: list[CanFrame]) -> None:
        with self._lock:
            self._immediate.extend(frame.copy() for frame in frames)

    def start_periodic(self, uid: int, frame: CanFrame, period_ms: int,
                       repeat: int = 0) -> None:
        period_ms = max(1, int(period_ms))
        with self._lock:
            self._periodic[uid] = PeriodicTask(
                uid=uid, frame=frame.copy(), period_ms=period_ms,
                remaining=int(repeat), next_due=time.perf_counter())

    def stop_periodic(self, uid: int) -> None:
        with self._lock:
            self._periodic.pop(uid, None)

    def stop_all_periodic(self) -> None:
        with self._lock:
            self._periodic.clear()
            self._sequence = None

    def start_sequence(self, tasks: list[tuple], repeats: int = 0,
                       interval_ms: int = 100, cycle_interval_ms: int = 100) -> None:
        entries = [(uid, frame.copy(), max(1, int(period)))
                   for uid, frame, period, _ in tasks]
        if not entries:
            return
        with self._lock:
            self._sequence = SequenceTask(entries, max(0, int(repeats)),
                                          time.perf_counter(), max(1, int(interval_ms)),
                                          max(1, int(cycle_interval_ms)))

    def stop_sequence(self) -> None:
        with self._lock:
            self._sequence = None

    def update_sequence_entries(self, tasks) -> None:
        with self._lock:
            sequence = self._sequence
            if sequence is None:
                return
            upcoming = [entry[0] for entry in sequence.entries[sequence.index:]]
            sequence.entries = [(uid, frame.copy(), max(1, int(period)))
                                for uid, frame, period, *_ in tasks]
            positions = {entry[0]: index for index, entry in enumerate(sequence.entries)}
            sequence.index = next((positions[uid] for uid in upcoming if uid in positions), 0)

    def active_uids(self) -> list[int]:
        with self._lock:
            return list(self._periodic)

    def update_frame(self, uid: int, frame: CanFrame) -> None:
        with self._lock:
            task = self._periodic.get(uid)
            if task:
                task.frame = frame.copy()

    def shutdown(self) -> None:
        self._stop.set()
        self.wait(2000)

    # ------------------------------------------------------------------ 主循环

    def run(self) -> None:
        while not self._stop.is_set():
            idle = True
            now = time.perf_counter()
            try:
                idle &= self._flush_immediate()
                idle &= self._run_periodic(now)
                idle &= self._run_sequence(time.perf_counter())
                idle &= self._pump_rx()
                self._emit_progress(now)
                self._emit_status(now)
            except ZlgError as exc:
                self._report_failure(str(exc))
            self._stop.wait(0.002 if not idle else 0.005)
        with self._lock:
            self._immediate.clear()
            self._periodic.clear()
            self._sequence = None

    def _run_sequence(self, now: float) -> bool:
        with self._lock:
            task = self._sequence
            if task is None:
                return True
            if not task.entries:
                return True
            if now < task.next_due:
                return False
            uid, frame, period = task.entries[task.index]
            self._transmit([frame])
            self.sequence_progress.emit(uid, task.cycles + 1)
            task.index += 1
            if task.index == len(task.entries):
                task.index = 0
                task.cycles += 1
                if task.repeats and task.cycles >= task.repeats:
                    self._sequence = None
                    self.sequence_done.emit()
                    return False
            delay = task.cycle_interval_ms if task.index == 0 else task.interval_ms
            task.next_due = time.perf_counter() + delay / 1000.0
        return False

    def _flush_immediate(self) -> bool:
        with self._lock:
            if not self._immediate:
                self._next_immediate_due = 0.0
                return True
            now = time.perf_counter()
            if now < self._next_immediate_due:
                return False
            frame = self._immediate.popleft()
        self._transmit([frame])
        self._next_immediate_due = time.perf_counter() + 0.010  # 10ms 间隔
        return bool(self._immediate)

    def _run_periodic(self, now: float) -> bool:
        with self._lock:
            due = [task for task in self._periodic.values() if task.next_due <= now]
            finished = [uid for uid, task in self._periodic.items()
                        if task.remaining > 0 and task.sent >= task.remaining]
            for uid in finished:
                del self._periodic[uid]
            active = bool(self._periodic) or bool(due)
        for uid in finished:
            self.task_done.emit(uid)
        if not due:
            return not active
        for task in due:
            self._transmit([task.frame])
            with self._lock:
                live = self._periodic.get(task.uid)
                if live is not task:
                    continue
                live.sent += 1
                self._pending_progress[live.uid] = live.sent
                # 错过的周期直接跳过，避免积压后爆发式补发
                step = max(1, int((now - live.next_due) // (live.period_ms / 1000.0)) + 1)
                live.next_due += step * live.period_ms / 1000.0
                if live.remaining > 0 and live.sent >= live.remaining:
                    del self._periodic[live.uid]
                    self.task_done.emit(live.uid)
        return False

    def _pump_rx(self) -> bool:
        received = self._bus.receive(limit=1000, wait_ms=0)
        if not received:
            return True
        records = []
        for frame, timestamp, is_error in received:
            records.append(TraceRecord.make(RX, frame, next(self._seq), timestamp,
                                            is_error))
            self._rx_total += 1
            self._err_total += 1 if is_error else 0
        self.records.emit(records)
        self.counters.emit(self._tx_total, self._rx_total, self._err_total)
        return False

    def _transmit(self, frames: list[CanFrame]) -> None:
        if not frames:
            return
        sent = self._bus.transmit(frames)
        self._tx_total += sent
        records = [TraceRecord.make(TX, frame.copy(), next(self._seq))
                   for frame in frames[:sent]]
        self.records.emit(records)
        self.counters.emit(self._tx_total, self._rx_total, self._err_total)

    def _emit_progress(self, now: float) -> None:
        if now - self._last_progress < PROGRESS_INTERVAL:
            return
        self._last_progress = now
        with self._lock:
            if not self._pending_progress:
                return
            batch = dict(self._pending_progress)
            self._pending_progress.clear()
        self.progress.emit(batch)

    def _emit_status(self, now: float) -> None:
        if now - self._last_status < STATUS_INTERVAL:
            return
        self._last_status = now
        self.status.emit(self._bus.status())

    def _report_failure(self, message: str) -> None:
        now = time.perf_counter()
        if now - self._last_failure < FAILURE_INTERVAL:
            return
        self._last_failure = now
        self.failure.emit(message)
