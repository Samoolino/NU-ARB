import asyncio
import logging
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.worker_supervisor import (
    ErrorCategory,
    WorkerRole,
    WorkerSpec,
    WorkerState,
    WorkerSupervisor,
)


async def wait_until(predicate, turns=500):
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition_not_reached")


class WorkerSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_error_retries_and_reports_healthy(self):
        attempts = 0
        recovered = asyncio.Event()

        async def worker(context):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("secret=must-not-be-logged")
            context.report_healthy()
            recovered.set()
            await asyncio.Event().wait()

        supervisor = WorkerSupervisor(sleep=lambda _: asyncio.sleep(0))
        try:
            await supervisor.start([
                WorkerSpec("book-feed", WorkerRole.MARKET_DATA, worker, max_restarts=2)
            ])
            await asyncio.wait_for(recovered.wait(), timeout=1)
            state = supervisor.health["book-feed"]
            self.assertEqual(attempts, 2)
            self.assertEqual(state.state, WorkerState.HEALTHY)
            self.assertEqual(state.failures, 1)
            self.assertEqual(state.restarts, 1)
            self.assertEqual(state.last_error_category, ErrorCategory.TRANSIENT)
        finally:
            await supervisor.shutdown()

    async def test_exponential_backoff_is_capped(self):
        delays = []
        attempts = 0
        running = asyncio.Event()

        async def fake_sleep(delay):
            delays.append(delay)
            await asyncio.sleep(0)

        async def worker(_context):
            nonlocal attempts
            attempts += 1
            if attempts <= 4:
                raise ConnectionError("offline")
            running.set()
            await asyncio.Event().wait()

        supervisor = WorkerSupervisor(sleep=fake_sleep)
        try:
            await supervisor.start([
                WorkerSpec(
                    "capped-feed",
                    WorkerRole.READ_ONLY_SCANNER,
                    worker,
                    base_backoff_s=0.1,
                    max_backoff_s=0.25,
                    max_restarts=4,
                )
            ])
            await asyncio.wait_for(running.wait(), timeout=1)
            self.assertEqual(delays, [0.1, 0.2, 0.25, 0.25])
            self.assertEqual(supervisor.health["capped-feed"].restarts, 4)
        finally:
            await supervisor.shutdown()

    async def test_shutdown_cancels_worker_and_sets_stopped_health(self):
        cancelled = asyncio.Event()

        async def worker(context):
            context.report_healthy()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        supervisor = WorkerSupervisor()
        await supervisor.start([
            WorkerSpec("cancel-feed", WorkerRole.MARKET_DATA, worker)
        ])
        await wait_until(lambda: supervisor.health["cancel-feed"].state is WorkerState.HEALTHY)
        await supervisor.shutdown()
        self.assertTrue(cancelled.is_set())
        self.assertEqual(supervisor.health["cancel-feed"].state, WorkerState.STOPPED)
        with self.assertRaisesRegex(RuntimeError, "worker_supervisor_closed"):
            await supervisor.start([])

    async def test_transient_failure_retries_without_disrupting_sibling(self):
        other_healthy = asyncio.Event()
        recovered = asyncio.Event()
        attempts = 0
        delays = []

        async def fake_sleep(delay):
            delays.append(delay)
            await asyncio.sleep(0)

        async def transient(context):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("temporary failure")
            context.report_healthy()
            recovered.set()
            await asyncio.Event().wait()

        async def healthy(context):
            context.report_healthy()
            other_healthy.set()
            await asyncio.Event().wait()

        supervisor = WorkerSupervisor(sleep=fake_sleep)
        try:
            await supervisor.start([
                WorkerSpec("retry-worker", WorkerRole.READ_ONLY_SCANNER, transient),
                WorkerSpec("good-worker", WorkerRole.MARKET_DATA, healthy),
            ])
            await asyncio.wait_for(other_healthy.wait(), timeout=1)
            await asyncio.wait_for(recovered.wait(), timeout=1)
            self.assertEqual(supervisor.health["retry-worker"].state, WorkerState.HEALTHY)
            self.assertEqual(supervisor.health["retry-worker"].failures, 1)
            self.assertEqual(supervisor.health["retry-worker"].restarts, 1)
            self.assertEqual(supervisor.health["good-worker"].state, WorkerState.HEALTHY)
            self.assertEqual(len(delays), 1)
        finally:
            await supervisor.shutdown()

    async def test_permanent_failure_opens_circuit_without_retry(self):
        attempts = 0

        async def permanent(_context):
            nonlocal attempts
            attempts += 1
            raise ValueError("private response: api_secret=redacted")

        supervisor = WorkerSupervisor(sleep=lambda _: asyncio.sleep(0))
        await supervisor.start([
            WorkerSpec("permanent-failure", WorkerRole.READ_ONLY_SCANNER, permanent)
        ])
        await wait_until(
            lambda: supervisor.health["permanent-failure"].state is WorkerState.CIRCUIT_OPEN
        )
        health = supervisor.health["permanent-failure"]
        self.assertEqual(attempts, 1)
        self.assertEqual(health.failures, 1)
        self.assertEqual(health.restarts, 0)
        self.assertEqual(health.last_error_category, ErrorCategory.PERMANENT)
        await supervisor.shutdown()

    async def test_retry_exhaustion_opens_circuit_and_logs_no_exception_values(self):
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger = logging.Logger("worker-supervisor-test")
        logger.addHandler(Capture())
        attempts = 0

        async def worker(_context):
            nonlocal attempts
            attempts += 1
            raise OSError("credential=do-not-print")

        supervisor = WorkerSupervisor(logger=logger, sleep=lambda _: asyncio.sleep(0))
        await supervisor.start([
            WorkerSpec(
                "exhausted",
                WorkerRole.MARKET_DATA,
                worker,
                max_restarts=2,
                base_backoff_s=0,
                max_backoff_s=0,
            )
        ])
        await wait_until(
            lambda: supervisor.health["exhausted"].state is WorkerState.CIRCUIT_OPEN
        )
        state = supervisor.health["exhausted"]
        self.assertEqual(attempts, 3)
        self.assertEqual(state.restarts, 2)
        self.assertEqual(state.failures, 3)
        self.assertTrue(all("credential=do-not-print" not in record.getMessage() for record in records))
        self.assertTrue(all(record.error_category == "transient" for record in records))
        await supervisor.shutdown()

    async def test_timeout_retries_but_order_execution_role_is_rejected(self):
        attempts = 0
        recovered = asyncio.Event()

        async def worker(context):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                await asyncio.Event().wait()
            context.report_healthy()
            recovered.set()
            await asyncio.Event().wait()

        supervisor = WorkerSupervisor(sleep=lambda _: asyncio.sleep(0))
        with self.assertRaisesRegex(ValueError, "worker_role_unsupported"):
            WorkerSpec("orders", "order_execution", worker)
        try:
            await supervisor.start([
                WorkerSpec(
                    "timeout-feed",
                    WorkerRole.MARKET_DATA,
                    worker,
                    timeout_s=0.01,
                    max_restarts=1,
                    base_backoff_s=0,
                    max_backoff_s=0,
                )
            ])
            await asyncio.wait_for(recovered.wait(), timeout=1)
            health = supervisor.health["timeout-feed"]
            self.assertEqual(health.failures, 1)
            self.assertEqual(health.last_error_category, ErrorCategory.TIMEOUT)
            self.assertEqual(health.state, WorkerState.HEALTHY)
        finally:
            await supervisor.shutdown()


if __name__ == "__main__":
    unittest.main()
