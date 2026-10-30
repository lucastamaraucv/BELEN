from __future__ import annotations

from typing import Callable

from ..config import Settings
from ..db import Databases
from ..services.ordenes_sync import sync_ordenes
from .base_worker import PollingWorker


def build_ordenes_worker(
    dbs: Databases,
    settings: Settings,
    *,
    on_status: Callable[[str], None] | None = None,
    on_cycle: Callable[[str, int, str], None] | None = None,
) -> PollingWorker:
    def poll_fn() -> int:
        with dbs.minsa_session_factory() as session:
            return sync_ordenes(
                session,
                batch_size=settings.ordenes_batch_size,
                dbf_dir=settings.dbf_dir,
                progreso=worker.marcar_progreso,
            )

    worker = PollingWorker(
        "ordenes-sync",
        poll_fn,
        idle_interval=settings.ordenes_poll_interval_idle_seconds,
        busy_interval=settings.ordenes_poll_interval_busy_seconds,
        backoff_initial=settings.backoff_initial_seconds,
        backoff_max=settings.backoff_max_seconds,
        backoff_factor=settings.backoff_factor,
        on_status=on_status,
        on_cycle=on_cycle,
    )
    return worker
