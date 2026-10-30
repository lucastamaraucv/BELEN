from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy.engine import URL

from .config_store import ConfigStore
from .connection import build_url

_ENV_PREFIX = "ORDEN_"


def _get(name: str, *, default: str) -> str:
    # Parámetros de ajuste (pool, intervalos...): valor por defecto, sobreescribible con
    # una variable de entorno ORDEN_<NOMBRE> opcional. Las cadenas de conexión NO salen de aquí.
    return os.environ.get(_ENV_PREFIX + name, default)


@dataclass(frozen=True)
class Settings:
    """Configuración de conexión y de los workers.

    Las cadenas de conexión (equivalentes a nCn1 = MINSA/Nobilis y nCn = Resultado/Jalisco del
    original) se guardan en SQLite y se editan desde la pestaña "Configuración" de la GUI.
    """

    # None = esa conexión todavía no está configurada (p. ej. MINSA aún no instalado en la PC).
    minsa_conn_str: "str | None"
    resultado_conn_str: "str | None"

    # --- Pool de conexiones (SQLAlchemy QueuePool) ---
    pool_size: int = 5
    max_overflow: int = 5
    pool_timeout: int = 30
    pool_recycle: int = 1800
    # Segundos antes de abortar un intento de conexión ODBC (evita cuelgues
    # indefinidos cuando la red/el servidor está caído).
    connect_timeout_seconds: int = 5
    # Segundos máximos por consulta/SP ya conectado (0 = sin límite). Evita hilos colgados si la
    # red cae a mitad de una operación.
    query_timeout_seconds: int = 60

    # Carpeta de las tablas locales de Datalab: dlhnse2.dbf (donde se insertan las órdenes) y perfiles.dbf.
    # Equivale a `rutan` del Fox original (SYS(5)+"\ORDENES\ORDEN\\"). Sobreescribible con ORDEN_DBF_DIR.
    dbf_dir: str = r"C:\ORDENES\ORDEN"

    # --- Workers ---
    resultados_poll_interval_seconds: float = 15.0
    ordenes_poll_interval_idle_seconds: float = 0.1
    ordenes_poll_interval_busy_seconds: float = 1.0
    ordenes_batch_size: int = 100

    # --- Backoff ante caídas de conexión (ver workers/base_worker.py) ---
    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    backoff_factor: float = 2.0

    log_level: str = "INFO"

    # Envío de resultados (codigo 1.txt: Jalisco -> InterLabDetalle). APAGADO por defecto: en el Fox de BELÉN
    # (FORM/formprincipal.SCT) esa parte está comentada y la tabla InterLabDetalle no existe en SIGH (es de las
    # versiones Nobilis/INSS). En BELÉN los resultados los envía otro programa. ORDEN_PROCESAR_RESULTADOS=1 lo activa.
    procesar_resultados: bool = False

    @property
    def completa(self) -> bool:
        return bool(self.minsa_conn_str and self.resultado_conn_str)

    @property
    def minsa_url(self) -> URL:
        assert self.minsa_conn_str, "MINSA no está configurada"
        return build_url(self.minsa_conn_str)

    @property
    def resultado_url(self) -> URL:
        assert self.resultado_conn_str, "Resultado no está configurada"
        return build_url(self.resultado_conn_str)


def get_settings(store: "ConfigStore | None" = None) -> Settings:
    """Lee las cadenas guardadas en SQLite. Las que falten quedan en None (no lanza error)."""
    store = store or ConfigStore()
    minsa = store.get("MINSA")
    resultado = store.get("Resultado")

    return Settings(
        minsa_conn_str=minsa,
        resultado_conn_str=resultado,
        pool_size=int(_get("POOL_SIZE", default="5")),
        max_overflow=int(_get("MAX_OVERFLOW", default="5")),
        pool_timeout=int(_get("POOL_TIMEOUT", default="30")),
        pool_recycle=int(_get("POOL_RECYCLE", default="1800")),
        connect_timeout_seconds=int(_get("CONNECT_TIMEOUT_SECONDS", default="5")),
        query_timeout_seconds=int(_get("QUERY_TIMEOUT_SECONDS", default="60")),
        dbf_dir=_get("DBF_DIR", default=r"C:\ORDENES\ORDEN"),
        resultados_poll_interval_seconds=float(_get("RESULTADOS_POLL_INTERVAL_SECONDS", default="15")),
        ordenes_poll_interval_idle_seconds=float(_get("ORDENES_POLL_INTERVAL_IDLE_SECONDS", default="0.1")),
        ordenes_poll_interval_busy_seconds=float(_get("ORDENES_POLL_INTERVAL_BUSY_SECONDS", default="1")),
        ordenes_batch_size=int(_get("ORDENES_BATCH_SIZE", default="100")),
        backoff_initial_seconds=float(_get("BACKOFF_INITIAL_SECONDS", default="1")),
        backoff_max_seconds=float(_get("BACKOFF_MAX_SECONDS", default="60")),
        backoff_factor=float(_get("BACKOFF_FACTOR", default="2")),
        log_level=_get("LOG_LEVEL", default="INFO"),
        procesar_resultados=_get("PROCESAR_RESULTADOS", default="0") == "1",
    )
