from __future__ import annotations

from typing import Callable

from ..config import Settings
from ..db import Databases
from ..services.resultados_sync import sync_resultados
from .base_worker import PollingWorker


def build_resultados_worker(
    dbs: Databases,
    settings: Settings,
    *,
    on_status: Callable[[str], None] | None = None,
    on_cycle: Callable[[str, int, str], None] | None = None,
) -> PollingWorker:
    def poll_fn() -> int:
        # Session por ciclo (unit of work): nunca se comparte una Session entre hilos.
        with dbs.minsa_session_factory() as minsa_session, dbs.resultado_session_factory() as resultado_session:
            return sync_resultados(minsa_session, resultado_session, progreso=worker.marcar_progreso)

    worker = PollingWorker(
        "resultados-sync",
        poll_fn,
        idle_interval=settings.resultados_poll_interval_seconds,
        busy_interval=settings.resultados_poll_interval_seconds,
        backoff_initial=settings.backoff_initial_seconds,
        backoff_max=settings.backoff_max_seconds,
        backoff_factor=settings.backoff_factor,
        on_status=on_status,
        on_cycle=on_cycle,
    )
    return worker
