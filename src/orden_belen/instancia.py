"""Una sola instancia por PC.

Dos copias del programa (doble clic dos veces, o el reinicio automático mientras la anterior sigue viva)
escribirían a la vez en dlhnse2.dbf, que no tiene bloqueo de registros: se perderían o pisarían órdenes.
Se usa un mutex con nombre de Windows; el sistema lo libera solo cuando el proceso termina (aunque se
cierre de golpe), así que nunca queda "trabado".
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_NOMBRE = "OrdenBelenSincronizador"
_ERROR_ALREADY_EXISTS = 183
_mutex = None  # se conserva toda la vida del proceso


def tomar_instancia_unica() -> bool:
    """True si esta es la única instancia (o no se pudo comprobar); False si ya hay otra corriendo."""
    global _mutex
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        # Global\ cubre todas las sesiones de Windows (p. ej. dos usuarios por escritorio remoto).
        for prefijo in ("Global\\", "Local\\"):
            handle = kernel32.CreateMutexW(None, False, prefijo + _NOMBRE)
            if handle:
                if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
                    kernel32.CloseHandle(handle)
                    return False
                _mutex = handle
                if prefijo != "Global\\":
                    logger.warning("Instancia única solo en esta sesión de Windows (sin permiso para el mutex global)")
                return True
    except Exception:
        logger.warning("No se pudo comprobar la instancia única", exc_info=True)
    else:
        logger.warning("No se pudo crear el mutex de instancia única")
    # Se arranca igual: para un servicio 24x7 es peor no arrancar que el riesgo (raro) de dos copias.
    return True
