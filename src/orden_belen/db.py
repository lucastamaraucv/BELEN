from __future__ import annotations

import logging

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from .config import Settings
from .errores import es_desconexion_dbapi

logger = logging.getLogger(__name__)


def _build_engine(url: str, settings: Settings, *, label: str) -> Engine:
    """Crea un engine con pool de conexiones (QueuePool).

    Sustituye al SQLCONNECT/handle único y permanente del original: aquí cada hilo
    toma y devuelve conexiones del pool en vez de compartir un solo handle ODBC.
    `pool_pre_ping` evita el problema típico de FoxPro de handles ODBC muertos tras
    una caída de red silenciosa.
    """
    engine = create_engine(
        url,
        poolclass=QueuePool,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout,
        pool_recycle=settings.pool_recycle,
        pool_pre_ping=True,
        future=True,
        # Sin esto, un intento de conexión mientras el servidor/red está caído
        # puede colgarse indefinidamente (el mismo problema que el ODBC original
        # de FoxPro, que nunca tuvo timeout). pyodbc corta el intento a los N
        # segundos y deja que el worker reintente con backoff.
        connect_args={"timeout": settings.connect_timeout_seconds},
    )

    # `timeout` de connect_args es solo el de LOGIN. Sin un timeout de CONSULTA (pyodbc: 0 = sin
    # límite), una caída de red a mitad de un SELECT/INSERT/SP deja el hilo colgado para siempre.
    @event.listens_for(engine, "connect")
    def _timeout_de_consulta(dbapi_connection, _record) -> None:
        dbapi_connection.timeout = settings.query_timeout_seconds

    instalar_deteccion_de_desconexion(engine)

    logger.info(
        "Engine '%s' creado (pool_size=%s, max_overflow=%s)", label, settings.pool_size, settings.max_overflow
    )
    return engine


def instalar_deteccion_de_desconexion(engine: Engine) -> None:
    """Marca como desconexión los errores que SQLAlchemy no reconoce (p. ej. "SHUTDOWN en curso", 6005).

    Con `is_disconnect = True` SQLAlchemy invalida esa conexión y todas las del pool abiertas antes, así que tras
    un reinicio del servidor se reconecta limpio en vez de ir encontrando conexiones muertas una por una.
    También aplica al pre-ping (`pool_pre_ping`)."""

    @event.listens_for(engine, "handle_error")
    def _marcar_desconexion(context) -> None:
        if not context.is_disconnect and es_desconexion_dbapi(context.original_exception):
            context.is_disconnect = True


class Databases:
    """Agrupa los dos engines + session factories, uno por sistema origen.

    Equivale a las variables nCn (Resultado/Jalisco) y nCn1 (MINSA/Nobilis) del
    original. nCn2 (usado en temporizado.txt para el UPDATE de Flag) se asume como
    la misma base MINSA -- ver README si en producción es una tercera base distinta.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Solo se crea el engine de las conexiones configuradas (None si falta, p. ej. MINSA sin instalar).
        self.minsa_engine: "Engine | None" = None
        self.resultado_engine: "Engine | None" = None
        self.minsa_session_factory: "sessionmaker[Session] | None" = None
        self.resultado_session_factory: "sessionmaker[Session] | None" = None

        if settings.minsa_conn_str:
            self.minsa_engine = _build_engine(settings.minsa_url, settings, label="MINSA")
            self.minsa_session_factory = sessionmaker(bind=self.minsa_engine, future=True)
        if settings.resultado_conn_str:
            self.resultado_engine = _build_engine(settings.resultado_url, settings, label="Resultado")
            self.resultado_session_factory = sessionmaker(bind=self.resultado_engine, future=True)

    def dispose(self) -> None:
        for engine in (self.minsa_engine, self.resultado_engine):
            if engine is not None:
                engine.dispose()
        logger.info("Pools de conexiones liberados.")
