from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from pathlib import Path
from types import FrameType

# Permite ejecutar este archivo directamente (botón ▶ de VS Code / `python gui.py`),
# además de `python -m orden_belen.gui`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "orden_belen"

from .config import get_settings
from .db import Databases
from .diagnostico import conectividad_ok, formatear, verificar
from .instancia import tomar_instancia_unica
from .logging_config import add_file_logging, configure_logging, instalar_capturas, memoria_mb
from .workers.base_worker import PollingWorker
from .workers.ordenes_worker import build_ordenes_worker
from .workers.resultados_worker import build_resultados_worker

logger = logging.getLogger(__name__)

LATIDO_SEGUNDOS = 600.0  # igual que el monitor de la GUI: una línea de estado cada 10 min en el archivo


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    add_file_logging(level=settings.log_level)  # mismo log acotado que la GUI (PYTHON\logs)
    instalar_capturas()
    if not tomar_instancia_unica():
        logger.warning("Ya hay otra instancia de ORDEN BELEN corriendo en esta PC; esta se cierra")
        return
    if not settings.minsa_conn_str or (settings.procesar_resultados and not settings.resultado_conn_str):
        raise SystemExit(
            "Faltan cadenas de conexión (MINSA, y Resultado si ORDEN_PROCESAR_RESULTADOS=1). "
            "Configúrelas en la pestaña Conexiones SQL Server (python -m orden_belen.gui)."
        )

    # Test de verificación de las bases (solo lectura). No impide arrancar: si una base está caída,
    # los workers reintentan con backoff; esto deja constancia clara de qué falla y por qué.
    resultados = verificar(settings)
    informe = formatear(resultados, settings)
    print(informe)
    logger.info("Diagnóstico de bases:\n%s", informe)
    if not conectividad_ok(resultados):
        logger.warning("Hay bases sin conectividad: los workers reintentarán automáticamente.")

    dbs = Databases(settings)

    workers: list[PollingWorker] = [build_ordenes_worker(dbs, settings)]
    if settings.procesar_resultados:  # apagado por defecto (ver config.Settings.procesar_resultados)
        workers.insert(0, build_resultados_worker(dbs, settings))

    stop_requested = threading.Event()

    def _handle_signal(signum: int, frame: FrameType | None) -> None:
        # Equivalente a ON SHUTDOWN DO SALIR.FXP, pero con apagado ordenado de hilos
        # y liberación de pools en vez de un QUIT abrupto.
        logger.info("Señal %s recibida, iniciando apagado ordenado...", signum)
        stop_requested.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    for worker in workers:
        worker.start()

    try:
        proximo_latido = time.monotonic() + LATIDO_SEGUNDOS
        while not stop_requested.is_set():
            stop_requested.wait(1.0)
            if time.monotonic() >= proximo_latido:
                proximo_latido = time.monotonic() + LATIDO_SEGUNDOS
                memoria = memoria_mb()
                logger.info(
                    "LATIDO | hilos: %s | memoria: %s",
                    ", ".join(
                        f"{w.name} {'activo' if w.is_alive() else 'CAIDO'} (último ciclo hace {w.segundos_sin_ciclo():.0f}s)"
                        for w in workers
                    ),
                    "?" if memoria is None else f"{memoria:.0f} MB",
                )
    finally:
        for worker in workers:
            worker.stop()
        for worker in workers:
            worker.join(timeout=10)
        vivos = [w.name for w in workers if w.is_alive()]
        if vivos:
            # No se liberan los pools con un worker aún escribiendo (una consulta bloqueada termina
            # sola por query_timeout_seconds); como los hilos son daemon, el proceso puede salir igual.
            logger.warning("Apagado incompleto: workers aún activos %s", vivos)
        else:
            dbs.dispose()
            logger.info("Apagado completo.")


if __name__ == "__main__":
    main()
