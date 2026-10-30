"""Reinicio del proceso completo cuando algo quedó colgado sin remedio (24x7).

Un hilo de Python bloqueado dentro del driver ODBC (un commit/rollback que nunca vuelve tras una caída del servidor)
o en una escritura de disco no se puede matar desde fuera. Tampoco se recupera memoria agotada en Python de 32 bits.
En esos casos lo único que funciona es terminar el proceso y abrirlo de nuevo: lo hace `iniciar_orden_belen.bat`,
que fija ORDEN_SUPERVISADO=1 y vuelve a abrir la app a los 15 s si termina con un código distinto de 0.

Sin el .bat (VS Code, `python -m ...`) no se termina nada: solo se avisa en el log.
Las órdenes a medio procesar quedan con Flag=0 (SQL Server revierte lo no confirmado) y se reprocesan al volver;
una fila repetida en el DBF es aceptable (Nobilis/Datalab no inserta duplicados).
"""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

CODIGO_REINICIO = 3  # el .bat reabre con cualquier código distinto de 0
_VARIABLE = "ORDEN_SUPERVISADO"
_ESPERA_MAX_LOG_SEGUNDOS = 10.0  # si el propio log está colgado, se termina igual

_pedido = False
_lock = threading.Lock()


def supervisado() -> bool:
    """True si la app se abrió con iniciar_orden_belen.bat (que la vuelve a abrir si termina)."""
    return os.environ.get(_VARIABLE) == "1"


def _terminar(codigo: int) -> None:
    """TerminateProcess: no espera a los hilos colgados ni a la descarga de DLLs (os._exit podría esperar)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32")
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.TerminateProcess(kernel32.GetCurrentProcess(), codigo)
        except Exception:
            pass
    os._exit(codigo)


def reiniciar_proceso(motivo: str) -> bool:
    """Termina el proceso para que el .bat lo reabra limpio. False si no está supervisado (solo avisa)."""
    global _pedido
    with _lock:
        if _pedido:
            return True
        if not supervisado():
            logger.error(
                "%s. No se reinicia solo porque no se abrió con iniciar_orden_belen.bat: ciérrelo y vuelva a abrirlo",
                motivo,
            )
            return False
        _pedido = True

    terminar = _terminar  # se fija ahora: el temporizador no debe depender de nada más
    respaldo = threading.Timer(_ESPERA_MAX_LOG_SEGUNDOS, terminar, args=(CODIGO_REINICIO,))
    respaldo.daemon = True
    respaldo.start()

    # Dónde estaba cada hilo (sirve para saber qué se colgó). faulthandler escribe directo, sin locks de logging.
    from . import logging_config

    archivo = logging_config._archivo_fallos
    if archivo is not None:
        try:
            archivo.write(f"=== Reinicio {datetime.now():%Y-%m-%d %H:%M:%S}: {motivo} ===\n")
            archivo.flush()
            faulthandler.dump_traceback(file=archivo, all_threads=True)
        except Exception:
            pass
    logger.critical("%s: se reinicia el programa (iniciar_orden_belen.bat lo vuelve a abrir en 15 s)", motivo)
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            pass
    terminar(CODIGO_REINICIO)
    return True
