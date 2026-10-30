"""Almacén SQLite de las cadenas de conexión de los dos SQL Server.

Reemplaza al antiguo `.env`: las cadenas se escriben en la pestaña "Configuración" de la GUI
y quedan guardadas en `config/orden_belen.db` (SQLite, un archivo local que no se versiona).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .rutas import CARPETA_APP

CONNECTION_NAMES = ("MINSA", "Resultado")

DEFAULT_DB_PATH = CARPETA_APP / "config" / "orden_belen.db"  # PYTHON\config o, en el .exe, junto a OrdenBelen.exe

_CREATE_TABLE = (
    "CREATE TABLE IF NOT EXISTS conexiones ("
    "nombre TEXT PRIMARY KEY, cadena TEXT NOT NULL, actualizado TEXT NOT NULL)"
)


class ConfigStore:
    def __init__(self, path: "Path | str | None" = None) -> None:
        self.path = Path(path) if path else DEFAULT_DB_PATH

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path)
        try:
            con.execute(_CREATE_TABLE)
            yield con
            con.commit()
        finally:
            con.close()

    def get(self, nombre: str) -> "str | None":
        with self._connect() as con:
            fila = con.execute("SELECT cadena FROM conexiones WHERE nombre = ?", (nombre,)).fetchone()
        return fila[0] if fila else None

    def delete(self, nombre: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM conexiones WHERE nombre = ?", (nombre,))

    def set(self, nombre: str, cadena: str) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO conexiones (nombre, cadena, actualizado) VALUES (?, ?, ?) "
                "ON CONFLICT(nombre) DO UPDATE SET cadena = excluded.cadena, actualizado = excluded.actualizado",
                (nombre, cadena, datetime.now().isoformat(timespec="seconds")),
            )
