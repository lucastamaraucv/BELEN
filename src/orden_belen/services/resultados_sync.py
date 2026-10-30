"""Equivalente funcional de CODIGO FUENTE/codigo 1.txt.

Original: conecta a Resultado (Jalisco) y MINSA (Nobilis), ejecuta el stored
procedure sp_Minsa_EnviarResultadoMinsa (resultados validados y no transferidos),
y por cada fila actualiza InterLabDetalle en MINSA y marca la fila como transferida
en Resultado vía sp_MINSA_UpdateResultadosValidacion.

Réplica exacta del original, incluido el parseo de fecha con SUBSTR posicional (los
segundos siempre quedan en 0, ver _parse_fecha_hora_resultado).

Diferencias deliberadas (documentadas, no silenciosas):
- Un error de datos en una fila no aborta el proceso completo (el original no tenía manejo
  de errores por fila); se registra y se continúa con la siguiente.
- Un error que haría fallar a todas las filas (red, o configuración: p. ej. 'InterLabDetalle no es
  válido') corta el ciclo en la primera fila; el worker reintenta con espera creciente.
- El mismo error se escribe completo una vez cada 10 min (las repeticiones solo se cuentan).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..errores import CicloFallido, Repeticiones, aborta_el_ciclo, firma_error, rollback_seguro, sufijo_repeticiones
from ..mappers import fila_minusculas

logger = logging.getLogger(__name__)

# SET NOCOUNT ON: pyodbc falla con 'Previous SQL was not a query' si el SP emite conteos de filas
# (DML) antes de su SELECT; el SQLEXEC de Fox no tenía ese problema.
_EXEC_PENDIENTES = text("SET NOCOUNT ON; EXEC sp_Minsa_EnviarResultadoMinsa")

_UPDATE_INTERLAB_DETALLE = text(
    "UPDATE InterLabDetalle "
    "SET dni = :dni, "
    "    FechaHoraResultado = :fecha_hora_resultado, "
    "    itemResultado = :item_resultado, "
    "    item = :item, "
    "    itemReferencia = :item_referencia, "
    "    idEstado = :id_estado "
    "WHERE identificador = :identificador AND cptcodigo = :cptcodigo"
)

_EXEC_UPDATE_VALIDACION = text(
    "SET NOCOUNT ON; EXEC sp_MINSA_UpdateResultadosValidacion :identificador, :id_determinacion"
)

# codigo 1.txt líneas 88-89: lcParm9='Automatizado' (itemMetodo, no se persiste en el
# UPDATE real del original) / lcParm10=transform(5) (idEstado)
ID_ESTADO_AUTOMATIZADO = "5"

# Errores de datos por fila ya escritos en el log (los repetidos solo se cuentan). Lo usa solo resultados-sync.
_repetidos = Repeticiones()


def _val(texto: str) -> int:
    """VAL() de VFP: número al inicio del texto, 0 si no hay (VAL(':0') = 0)."""
    m = re.match(r"\s*(\d+)", texto)
    return int(m.group(1)) if m else 0


def _parse_fecha_hora_resultado(raw: str) -> datetime:
    """Igual que codigo 1.txt línea 79: DATETIME(...) con SUBSTR posicional sobre 'YYYY-MM-DD HH:MM:SS.fff'.

    Réplica exacta, incluido el desfase del original: los segundos se leen con SUBSTR(x,17,2) = ':0',
    así que VAL(...) = 0 y los segundos SIEMPRE quedan en 0.
    """
    return datetime(
        _val(raw[0:4]),
        _val(raw[5:7]),
        _val(raw[8:10]),
        _val(raw[11:13]),
        _val(raw[14:16]),
        _val(raw[16:18]),
    )


def _procesar_fila(minsa_session: Session, resultado_session: Session, row_raw: dict) -> None:
    row = fila_minusculas(row_raw)  # campos VFP: sin distinción de mayúsculas
    fecha_hora_resultado = _parse_fecha_hora_resultado(row["fechahoraresultado"])

    actualizacion = minsa_session.execute(
        _UPDATE_INTERLAB_DETALLE,
        {
            "dni": row["dni"],
            "fecha_hora_resultado": fecha_hora_resultado,
            "item_resultado": row["itemresultado"],
            "item": row["item"],
            "item_referencia": row["itemreferencia"],
            "id_estado": ID_ESTADO_AUTOMATIZADO,
            "identificador": row["identificador"],
            "cptcodigo": row["cptcodigo"],
        },
    )
    if actualizacion.rowcount == 0:
        # Igual que Fox, se marca como transferido de todos modos (codigo 1.txt línea 99); solo se avisa.
        logger.warning(
            "InterLabDetalle sin coincidencia: identificador=%s cptcodigo=%s (el UPDATE no afectó filas)",
            row["identificador"],
            row["cptcodigo"],
        )
    minsa_session.commit()

    resultado_session.execute(
        _EXEC_UPDATE_VALIDACION,
        {"identificador": row["identificador"], "id_determinacion": row["id_determinacion"]},
    )
    resultado_session.commit()


def sync_resultados(
    minsa_session: Session, resultado_session: Session, *, progreso: "Callable[[], None] | None" = None
) -> int:
    """Ejecuta un ciclo de sincronización de resultados. Devuelve filas procesadas.

    `progreso` se llama tras cada fila: el vigilante distingue un ciclo largo de uno colgado."""
    rows = resultado_session.execute(_EXEC_PENDIENTES).mappings().all()
    if not rows:
        # Fox (autocommit) conservaba cualquier efecto del SP aunque no devolviera filas; sin este
        # commit, cerrar la sesión lo revertiría.
        resultado_session.commit()
        return 0

    procesadas = 0
    fallidas = 0
    for row in rows:
        try:
            _procesar_fila(minsa_session, resultado_session, dict(row))
            procesadas += 1
            logger.debug(
                "Resultado transferido: identificador=%s cpt=%s",
                fila_minusculas(row).get("identificador"),
                fila_minusculas(row).get("cptcodigo"),
            )
        except MemoryError:
            raise  # no es un error de la fila: el worker reinicia el proceso
        except Exception as exc:
            rollback_seguro(minsa_session)
            rollback_seguro(resultado_session)
            if aborta_el_ciclo(exc):
                # Caída de SQL Server o error de configuración: las filas siguientes fallarían igual. Se corta el
                # ciclo y el worker reintenta con backoff (y escribe el error). Lo no confirmado sigue pendiente
                # en Resultado y se reenvía (el UPDATE es idempotente).
                if procesadas:
                    logger.warning("sync_resultados: ciclo interrumpido tras transferir %s/%s filas", procesadas, len(rows))
                raise
            fallidas += 1
            omitidos = _repetidos.registrar(firma_error(exc))
            if omitidos is not None:
                logger.exception(
                    "Error procesando resultado identificador=%s cpt=%s; se continúa con el siguiente%s",
                    fila_minusculas(row).get("identificador"),
                    fila_minusculas(row).get("cptcodigo"),
                    sufijo_repeticiones(omitidos),
                )
        if progreso is not None:
            progreso()

    if fallidas and not procesadas:
        raise CicloFallido(f"sync_resultados: las {fallidas} filas pendientes fallaron")
    if fallidas:
        logger.warning("sync_resultados: %s/%s filas procesadas, %s con error", procesadas, len(rows), fallidas)
    else:
        logger.info("sync_resultados: %s/%s filas procesadas", procesadas, len(rows))
    return procesadas
