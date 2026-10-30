"""Equivalente funcional de CODIGO FUENTE/temporizado.txt (evento Timer1.Timer de formprincipal.SCT).

Original: lee las órdenes pendientes (Flag=0) de LabOrdenesInterop2 en GALENO (SQL Server), las transforma
(tipo de documento, sexo, código de servicio, código de procedimiento), expande "perfiles" (paneles que agrupan
varios exámenes, según la tabla local perfiles.dbf) y las inserta en la tabla local dlhnse2.dbf (alias
`vordenobi`, la que lee el sistema Datalab). Al terminar cada orden marca Flag=2 en LabOrdenesInterop2.

`vordenobi` y `vperfiles` NO son tablas SQL: en el Init del formulario se abren con
`use &rutan/dlhnse2 alias vordenobi` y `use &rutan/perfiles alias vperfiles`, donde
`rutan = SYS(5)+"\\ORDENES\\ORDEN\\"` (normalmente C:\\ORDENES\\ORDEN). Aquí se accede a esos DBF con `dbf_io`.

Mejora frente al original: la orden se reclama en SQL (Flag 0 -> 2, sin confirmar) ANTES de escribir en el DBF
y se confirma después. Si algo falla, la transacción se revierte y la orden sigue pendiente; con dos instancias,
la segunda ve Flag<>0 y no duplica.

Errores: uno de red o de configuración (tabla/permisos) corta el ciclo; uno de datos solo salta esa orden.
El mismo error se escribe completo una vez cada 10 min. Si ninguna orden del ciclo se pudo procesar se lanza
`CicloFallido` para que el worker espere (antes reintentaba cada 0,1 s).
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import dbf_io
from ..errores import CicloFallido, Repeticiones, aborta_el_ciclo, firma_error, rollback_seguro, sufijo_repeticiones
from ..mappers import (
    codigo_procedimiento,
    codigo_servicio,
    documento_paciente,
    fecha_nacimiento,
    fila_minusculas,
    historia_clinica,
    sexo,
    tipo_documento,
    transform_numero,
)

logger = logging.getLogger(__name__)

ARCHIVO_ORDENES = "dlhnse2.dbf"  # alias vordenobi
ARCHIVO_PERFILES = "perfiles.dbf"  # alias vperfiles

# Errores de datos por orden ya escritos en el log (los repetidos solo se cuentan). Lo usa solo ordenes-sync.
_repetidos = Repeticiones()

_SELECT_PENDIENTES = text("SELECT TOP (:batch_size) a.* FROM LabOrdenesInterop2 a WHERE a.Flag = 0")

_UPDATE_FLAG = text(
    "UPDATE LabOrdenesInterop2 SET Flag = 2 "
    "WHERE IdMovimiento = :id_movimiento AND Codigo = :codigo AND Flag = 0"
)


def ruta_dbf(dbf_dir: str, archivo: str) -> str:
    return os.path.join(dbf_dir, archivo)


def cargar_perfiles(dbf_dir: str) -> "list[tuple[str, str]]":
    """perfiles.dbf como [(Codigo, cod_exam)] en orden físico, sin registros borrados (SET DELETE ON)."""
    return [
        ((r["CODIGO"] or "").strip(" "), (r["COD_EXAM"] or "").strip(" "))  # ALLTRIM
        for r in dbf_io.leer_registros(ruta_dbf(dbf_dir, ARCHIVO_PERFILES))
    ]


def _fila_dbf(
    *,
    numero: Any,
    fecha_solicitud: "date | None",
    codigo_examen: str,
    perfil: str,
    historia: "str | None",
    nombre: "str | None",
    sexo_paciente: str,
    fecha_nac: date,
    tipo_doc: str,
    procedimiento: "str | None",
    cod_servicio: str,
    documento: str,
    hora: str,
) -> "dict[str, Any]":
    """Los 15 REPLACE de temporizado.txt (líneas 124-140 y 155-171). dl_cama no se escribe en esta versión."""
    return {
        "dl_numero": transform_numero(numero),
        "dl_fecexa": fecha_solicitud,
        "dl_cexame": codigo_examen,
        "dl_perfil": perfil,
        "dl_codlab": "BIO",
        "dl_histor": historia,
        "dl_nombre": nombre,
        "dl_sexo": sexo_paciente,
        "dl_fecnac": fecha_nac,
        "dl_tipdoc": tipo_doc,  # el campo es C(1): VFP guarda solo la primera letra ('MDN' -> 'M')
        "dl_fecreg": fecha_solicitud,
        "dl_cpro": procedimiento,
        "dl_cser": cod_servicio,
        "dl_ndocum": documento,
        "dl_hora": hora,
    }


def _procesar_orden(
    session: Session, row_raw: "dict[str, Any]", perfiles: "list[tuple[str, str]]", dbf_dir: str
) -> bool:
    """Procesa una orden. Devuelve False si otro proceso ya la había tomado (no escribe nada)."""
    row = fila_minusculas(row_raw)  # campos VFP: sin distinción de mayúsculas
    numero = row["idmovimiento"]
    codigo = (row["codigo"] or "").strip(" ")  # ALLTRIM: solo espacios

    # Fox hacía el UPDATE Flag=2 al final (línea 179). Aquí se reclama PRIMERO, en la transacción que se
    # confirma al final: mismo resultado, pero con dos instancias la segunda ve Flag<>0 y no duplica.
    if session.execute(_UPDATE_FLAG, {"id_movimiento": numero, "codigo": codigo}).rowcount == 0:
        logger.debug("Orden IdMovimiento=%s Codigo=%s ya procesada por otro proceso; se omite", numero, codigo)
        return False


    fecha_raw = row["fecha"]
    fecha_solicitud = fecha_raw.date() if isinstance(fecha_raw, datetime) else fecha_raw
    # mhora=substr(time(vorden.fecha),1,5): en VFP TIME() devuelve la hora del sistema (su argumento solo
    # pide centésimas), o sea la hora en que se procesa la orden, no la de la solicitud.
    hora = datetime.now().strftime("%H:%M")

    paciente = row.get("paciente")
    comunes = dict(
        numero=numero,
        fecha_solicitud=fecha_solicitud,
        historia=historia_clinica(row.get("nrohistoriaclinica")),
        nombre=None if paciente is None else paciente.strip(" ").upper(),  # ALLTRIM/UPPER de NULL = NULL
        sexo_paciente=sexo(row.get("idtiposexo")),
        fecha_nac=fecha_nacimiento(row.get("fechanacimiento")),
        tipo_doc=tipo_documento(row.get("iddocidentidad")),
        procedimiento=codigo_procedimiento(row.get("idtiposervicio"), row.get("idfuentefinanciamiento")),
        cod_servicio=codigo_servicio(row.get("idservicio")),
        documento=documento_paciente(row.get("nrodocumento")),
        hora=hora,
    )

    # LOCATE FOR alltrim(vperfiles.Codigo)==alltrim(mnumcins) + CONTINUE: todos los registros que coinciden
    # (comparación exacta, sensible a mayúsculas), en el orden físico del archivo.
    cod_examenes = [cod_exam for cod, cod_exam in perfiles if cod == codigo]
    if cod_examenes:
        # temporizado.txt líneas 108-151: un examen expande a varios sub-exámenes del perfil.
        filas = [_fila_dbf(codigo_examen=cod_exam, perfil=codigo, **comunes) for cod_exam in cod_examenes]
    else:
        # temporizado.txt líneas 152-174: inserción directa, sin expansión.
        filas = [_fila_dbf(codigo_examen=codigo, perfil=codigo, **comunes)]

    dbf_io.agregar_registros(ruta_dbf(dbf_dir, ARCHIVO_ORDENES), filas)
    return True


def sync_ordenes(
    session: Session, *, batch_size: int, dbf_dir: str, progreso: "Callable[[], None] | None" = None
) -> int:
    """Ejecuta un ciclo de sincronización de órdenes. Devuelve órdenes procesadas.

    `progreso` se llama tras cada orden: el vigilante distingue un ciclo largo de uno colgado.

    Si SQL Server cae justo entre la escritura del DBF y el commit, la orden vuelve a Flag=0 y se escribe otra vez
    (igual que el Fox). Es aceptado: Nobilis/Datalab no inserta los duplicados del DBF."""
    rows = session.execute(_SELECT_PENDIENTES, {"batch_size": batch_size}).mappings().all()
    if not rows:
        return 0

    perfiles = cargar_perfiles(dbf_dir)  # un DBF ilegible/ausente aborta el ciclo (nada se marca como procesado)

    procesadas = 0
    fallidas = 0
    for row in rows:
        try:
            escrita = _procesar_orden(session, dict(row), perfiles, dbf_dir)
            session.commit()
            if escrita:
                procesadas += 1
        except MemoryError:
            rollback_seguro(session)
            raise  # no es un error de la orden: el worker reinicia el proceso
        except (dbf_io.DBFError, OSError):
            # DBF ausente/bloqueado/dañado o error de disco (lleno, permisos): las siguientes fallarían igual. Se corta el ciclo (el worker lo escribe
            # en el log y reintenta con backoff); el Flag=2 sin confirmar se revierte y la orden sigue pendiente.
            rollback_seguro(session)
            raise
        except Exception as exc:
            rollback_seguro(session)
            if aborta_el_ciclo(exc):
                # Caída/timeout de SQL Server o error de configuración: se corta el ciclo (la sesión se cierra y el
                # worker aplica backoff). Las órdenes sin confirmar siguen con Flag=0 y se reintentan.
                if procesadas:
                    logger.warning("sync_ordenes: ciclo interrumpido tras %s/%s órdenes", procesadas, len(rows))
                raise
            fallidas += 1
            omitidos = _repetidos.registrar(firma_error(exc))
            if omitidos is not None:
                logger.exception(
                    "Error procesando orden IdMovimiento=%s Codigo=%s; se continúa con la siguiente%s",
                    fila_minusculas(row).get("idmovimiento"),
                    fila_minusculas(row).get("codigo"),
                    sufijo_repeticiones(omitidos),
                )
        if progreso is not None:
            progreso()

    if fallidas and not procesadas:
        raise CicloFallido(f"sync_ordenes: las {fallidas} órdenes pendientes fallaron")
    if fallidas:
        logger.warning("sync_ordenes: %s/%s órdenes procesadas, %s con error", procesadas, len(rows), fallidas)
    else:
        logger.info("sync_ordenes: %s/%s órdenes procesadas", procesadas, len(rows))
    return procesadas
