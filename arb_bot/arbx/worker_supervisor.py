"""Isolated supervision for long-running, read-only scanner workers.

This module deliberately does not supervise order execution. Retrying a worker
that may have submitted an order could duplicate a submission when its state is
unknown; execution recovery remains under the existing execution/reconciliation
controls.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable


class WorkerRole(str, Enum):
    """Roles this supervisor is permitted to run."""

    READ_ONLY_SCANNER = "read_only_scanner"
    MARKET_DATA = "market_data"


class WorkerState(str, Enum):
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CIRCUIT_OPEN = "circuit_open"
    STOPPED = "stopped"


class ErrorCategory(str, Enum):
    TIMEOUT = "timeout"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNEXPECTED_EXIT = "unexpected_exit"


@dataclass(frozen=True)
class WorkerHealth:
    name: str
    role: WorkerRole
    state: WorkerState
    attempts: int
    failures: int
    restarts: int
    last_error_category: ErrorCategory | None


WorkerFactory = Callable[["WorkerContext"], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]
ErrorClassifier = Callable[[BaseException], ErrorCategory]


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    role: WorkerRole
    factory: WorkerFactory
    timeout_s: float = 30.0
    base_backoff_s: float = 0.5
    max_backoff_s: float = 15.0
    max_restarts: int = 5

    def __post_init__(self) -> None:
        if not isinstance(self.role, WorkerRole):
            raise ValueError("worker_role_unsupported")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", self.name):
            raise ValueError("worker_name_invalid")
        if not callable(self.factory):
            raise ValueError("worker_factory_invalid")
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, (int, float))
            or not math.isfinite(self.timeout_s)
            or self.timeout_s <= 0
        ):
            raise ValueError("worker_timeout_invalid")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in (self.base_backoff_s, self.max_backoff_s)
        ):
            raise ValueError("worker_backoff_invalid")
        if (
            isinstance(self.max_restarts, bool)
            or not isinstance(self.max_restarts, int)
            or self.max_restarts < 0
        ):
            raise ValueError("worker_restart_limit_invalid")


@dataclass
class _HealthRecord:
    spec: WorkerSpec
    state: WorkerState = WorkerState.STARTING
    attempts: int = 0
    failures: int = 0
    restarts: int = 0
    last_error_category: ErrorCategory | None = None

    def snapshot(self) -> WorkerHealth:
        return WorkerHealth(
            name=self.spec.name,
            role=self.spec.role,
            state=self.state,
            attempts=self.attempts,
            failures=self.failures,
            restarts=self.restarts,
            last_error_category=self.last_error_category,
        )


class WorkerContext:
    """Narrow health-reporting surface passed to a supervised worker."""

    def __init__(self, record: _HealthRecord) -> None:
        self._record = record

    def report_healthy(self) -> None:
        """Report successful read-only worker activity for its current attempt."""
        if self._record.state not in (WorkerState.CIRCUIT_OPEN, WorkerState.STOPPED):
            self._record.state = WorkerState.HEALTHY


def _default_error_classifier(error: BaseException) -> ErrorCategory:
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return ErrorCategory.TIMEOUT
    if isinstance(error, (ConnectionError, OSError)):
        return ErrorCategory.TRANSIENT
    return ErrorCategory.PERMANENT


class WorkerSupervisor:
    """Own independent, cancellable scanner/market-data workers.

    Failures are isolated per worker. Only timeout and explicitly classified
    transient failures are retried. Health is process-local and intentionally
    contains no exception text, credentials, or exchange response values.
    """

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        sleep: Sleep = asyncio.sleep,
        classify_error: ErrorClassifier = _default_error_classifier,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._sleep = sleep
        self._classify_error = classify_error
        self._records: dict[str, _HealthRecord] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False

    @property
    def health(self) -> dict[str, WorkerHealth]:
        return {name: record.snapshot() for name, record in self._records.items()}

    async def start(self, workers: list[WorkerSpec] | tuple[WorkerSpec, ...]) -> None:
        if self._closed:
            raise RuntimeError("worker_supervisor_closed")
        names = [worker.name for worker in workers]
        if len(names) != len(set(names)):
            raise ValueError("worker_name_duplicate")
        if any(name in self._records for name in names):
            raise ValueError("worker_already_registered")
        for spec in workers:
            record = _HealthRecord(spec=spec)
            self._records[spec.name] = record
            self._tasks[spec.name] = asyncio.create_task(
                self._run(record),
                name=f"scanner-worker:{spec.name}",
            )

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for record in self._records.values():
            if record.state is not WorkerState.CIRCUIT_OPEN:
                record.state = WorkerState.STOPPED

    async def _run(self, record: _HealthRecord) -> None:
        spec = record.spec
        context = WorkerContext(record)
        while not self._closed:
            record.attempts += 1
            try:
                await asyncio.wait_for(spec.factory(context), timeout=spec.timeout_s)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                await self._handle_failure(record, ErrorCategory.TIMEOUT)
            except Exception as error:
                try:
                    category = self._classify_error(error)
                except Exception:
                    category = ErrorCategory.PERMANENT
                if not isinstance(category, ErrorCategory):
                    category = ErrorCategory.PERMANENT
                await self._handle_failure(record, category)
            else:
                # Long-running workers are expected to remain active. A clean
                # return is not treated as evidence that the worker is healthy.
                await self._handle_failure(record, ErrorCategory.UNEXPECTED_EXIT)

            if record.state is WorkerState.CIRCUIT_OPEN or self._closed:
                return

    async def _handle_failure(
        self,
        record: _HealthRecord,
        category: ErrorCategory,
    ) -> None:
        record.failures += 1
        record.last_error_category = category

        retryable = category in (ErrorCategory.TIMEOUT, ErrorCategory.TRANSIENT)
        if not retryable or record.restarts >= record.spec.max_restarts:
            record.state = WorkerState.CIRCUIT_OPEN
            self._logger.error(
                "scanner_worker_circuit_open",
                extra={
                    "worker_role": record.spec.role.value,
                    "error_category": category.value,
                    "attempt": record.attempts,
                    "failure_count": record.failures,
                },
            )
            return

        delay = record.spec.base_backoff_s
        for _ in range(record.restarts):
            if delay >= record.spec.max_backoff_s:
                delay = record.spec.max_backoff_s
                break
            delay = min(record.spec.max_backoff_s, delay * 2)
        record.state = WorkerState.DEGRADED
        self._logger.warning(
            "scanner_worker_retry",
            extra={
                "worker_role": record.spec.role.value,
                "error_category": category.value,
                "attempt": record.attempts,
                "restart_count": record.restarts + 1,
                "backoff_s": delay,
            },
        )
        await self._sleep(delay)
        if not self._closed:
            record.restarts += 1
