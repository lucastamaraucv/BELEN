"""Lanzador 24x7: abre la aplicación (ventana) en un proceso hijo y la vuelve a abrir si se cierra por un fallo.

Es el punto de entrada de OrdenBelen.exe y el que usa iniciar_orden_belen.bat (`pythonw -m orden_belen.lanzador`).

* El hijo corre con ORDEN_SUPERVISADO=1: así, si queda colgado 10 min o sin memoria, se cierra solo con código 3
  (ver reinicio.py) y este lanzador lo reabre a los 15 s.
* Cierre normal (Detener + X, o ya había otra copia abierta) = código 0: el lanzador también termina.
* Cualquier otro código (caída, Administrador de tareas, error al arrancar) = se reabre. Cada reapertura queda en
  logs\\reinicios.log (máx. 1 MB más una copia .1).
* El lanzador no abre bases de datos ni ventana: casi no usa memoria y no tiene nada que se le cuelgue.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

# Permite ejecutar este archivo directamente (botón ▶ de VS Code), además de `python -m orden_belen.lanzador`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "orden_belen"

from .logging_config import DIRECTORIO_LOGS  # noqa: E402

VARIABLE_HIJO = "ORDEN_BELEN_HIJO"  # marca el proceso hijo (la aplicación de verdad)
ESPERA_REINICIO_SEGUNDOS = 15.0
MAX_BYTES_REINICIOS = 1024 * 1024
TEMPORAL_HUERFANO_SEGUNDOS = 600.0  # una carpeta que se está descomprimiendo ahora mismo nunca es tan vieja


def comando_hijo() -> "list[str]":
    if getattr(sys, "frozen", False):
        return [sys.executable]  # el mismo .exe; la variable de entorno lo pone en modo aplicación
    return [sys.executable, "-m", "orden_belen.gui"]


def anotar_reinicio(texto: str, carpeta: Path = DIRECTORIO_LOGS) -> None:
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = carpeta / "reinicios.log"
        if ruta.exists() and ruta.stat().st_size >= MAX_BYTES_REINICIOS:
            os.replace(ruta, carpeta / "reinicios.log.1")
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {texto}\n")
    except OSError:
        pass  # anotar es lo de menos: lo importante es reabrir


def _tiene_conexiones(ruta: Path) -> bool:
    from .config_store import ConfigStore

    try:
        guardada = ConfigStore(ruta)
        return bool(guardada.get("MINSA") or guardada.get("Resultado"))
    except Exception:
        return False


def origenes_de_configuracion(incluida: "Path | None") -> "list[Path]":
    """Dónde buscar conexiones la primera vez, en orden: la instalación anterior desde el código fuente
    (Escritorio\\PYTHON\\config, p. ej. C:\\Users\\Administrador\\Desktop\\PYTHON en la PC servidor) y, si no hay,
    las que vienen dentro del .exe."""
    perfil = Path(os.environ.get("USERPROFILE") or Path.home())
    escritorios = [perfil / "Desktop", perfil / "Escritorio", perfil / "OneDrive" / "Desktop", perfil / "OneDrive" / "Escritorio"]
    origenes = [e / "PYTHON" / "config" / "orden_belen.db" for e in escritorios]
    if incluida is not None:
        origenes.append(incluida)
    return origenes


def sembrar_configuracion(destino: Path, origenes: "list[Path]") -> "Path | None":
    """Primera vez en una PC: si junto al .exe no hay conexiones guardadas, copia las del primer origen que tenga
    (ver origenes_de_configuracion). Así abre directo en PRODUCCIÓN, con las cadenas propias de esa PC.
    Si ya hay conexiones guardadas (p. ej. cambiadas desde la pestaña Conexiones) no se tocan. Devuelve el origen usado."""
    if destino.is_file() and _tiene_conexiones(destino):
        return None
    for origen in origenes:
        try:
            if not origen.is_file() or origen.resolve() == destino.resolve() or not _tiene_conexiones(origen):
                continue
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origen, destino)
            return origen
        except OSError:
            continue
    return None


def limpiar_temporales_huerfanos(carpeta: "Path | None", propia: "Path | None") -> int:
    """Borra las carpetas _MEIxxxx que dejó un .exe cerrado a la fuerza (corte de luz, Administrador de tareas).

    Cada una pesa ~25 MB. Solo se borra una carpeta que ningún proceso usa: primero se intenta renombrarla y Windows
    no lo permite mientras una instancia viva tenga cargada su python39.dll. Nunca se toca la propia ni las recientes.
    """
    if carpeta is None or not carpeta.is_dir():
        return 0
    borradas = 0
    limite = time.time() - TEMPORAL_HUERFANO_SEGUNDOS
    for d in carpeta.glob("_MEI*"):
        try:
            if not d.is_dir() or (propia is not None and d.resolve() == propia.resolve()) or d.stat().st_mtime > limite:
                continue
            apartada = d.with_name(d.name + "_borrar")
            os.rename(d, apartada)  # falla si la está usando otra instancia
        except OSError:
            continue
        shutil.rmtree(apartada, ignore_errors=True)
        borradas += 1
    return borradas


def supervisar(
    comando: "list[str]",
    *,
    ejecutar: "Callable[..., int]" = subprocess.call,
    esperar: "Callable[[float], None]" = time.sleep,
    carpeta: Path = DIRECTORIO_LOGS,
) -> int:
    """Ejecuta `comando` y lo vuelve a ejecutar mientras termine con un código distinto de 0."""
    # PYINSTALLER_RESET_ENVIRONMENT: en el .exe de un solo archivo, la app arranca como instancia propia (se
    # descomprime y se limpia sola) en vez de reutilizar la carpeta temporal del lanzador.
    entorno = dict(os.environ, ORDEN_SUPERVISADO="1", PYINSTALLER_RESET_ENVIRONMENT="1", **{VARIABLE_HIJO: "1"})
    while True:
        try:
            codigo = ejecutar(comando, env=entorno)
        except OSError as exc:
            codigo = -1
            anotar_reinicio(f"No se pudo abrir ORDEN BELEN ({exc})", carpeta)
        if codigo == 0:
            return 0
        anotar_reinicio(
            f"ORDEN BELEN se cerro con codigo {codigo}; se reabre en {ESPERA_REINICIO_SEGUNDOS:.0f} s", carpeta
        )
        esperar(ESPERA_REINICIO_SEGUNDOS)


def main() -> None:
    if os.environ.get(VARIABLE_HIJO) == "1":
        from .gui import main as gui_main

        gui_main()
        return
    propia = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and propia:
        from .config_store import DEFAULT_DB_PATH

        origen = sembrar_configuracion(
            DEFAULT_DB_PATH, origenes_de_configuracion(Path(propia) / "config_incluida" / "orden_belen.db")
        )
        if origen is not None:
            anotar_reinicio(f"Primera vez en esta PC: conexiones copiadas de {origen}")
        limpiar_temporales_huerfanos(Path(propia).parent, Path(propia))
    sys.exit(supervisar(comando_hijo()))


if __name__ == "__main__":
    main()
