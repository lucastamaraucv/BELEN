"""Verificación de conectividad y de objetos requeridos en las dos bases de datos.

Solo lectura: `SELECT 1`, `@@SERVERNAME`/`DB_NAME()`, `OBJECT_ID` y `HAS_PERMS_BY_NAME`. No lee datos de
negocio ni escribe nada. Se ejecuta al arrancar `main` y también por separado:

    python -m orden_belen.diagnostico      (código de salida 0 = todo OK, 1 = hay fallas)
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Permite ejecutar este archivo directamente (botón ▶ de VS Code), además de `python -m orden_belen.diagnostico`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "orden_belen"

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from . import dbf_io
from .config import Settings
from .connection import build_url, describe, describe_error, fijar_timeout_de_consulta

OK, FALLA, SIN_CONFIGURAR = "OK", "FALLA", "N/C"

_INDICADOR = {OK: "[ OK  ]", FALLA: "[FALLA]", SIN_CONFIGURAR: "[ N/C ]"}

# (objeto, permisos que necesita el proceso). Surgen de temporizado.txt y codigo 1.txt.
# vordenobi y vperfiles NO son tablas SQL: son los archivos locales dlhnse2.dbf y perfiles.dbf (ver DBF_REQUERIDOS).
OBJETOS_REQUERIDOS: "dict[str, list[tuple[str, tuple[str, ...]]]]" = {
    "MINSA": [
        ("LabOrdenesInterop2", ("SELECT", "UPDATE")),
        ("InterLabDetalle", ("UPDATE",)),
    ],
    "Resultado": [
        ("sp_Minsa_EnviarResultadoMinsa", ("EXECUTE",)),
        ("sp_MINSA_UpdateResultadosValidacion", ("EXECUTE",)),
    ],
}


# archivo -> campos que el proceso necesita (temporizado.txt: los REPLACE y el LOCATE de perfiles)
DBF_REQUERIDOS: "dict[str, tuple[str, ...]]" = {
    "dlhnse2.dbf": (
        "DL_NUMERO", "DL_FECEXA", "DL_CEXAME", "DL_PERFIL", "DL_CODLAB", "DL_HISTOR", "DL_NOMBRE", "DL_SEXO",
        "DL_FECNAC", "DL_TIPDOC", "DL_FECREG", "DL_CPRO", "DL_CSER", "DL_NDOCUM", "DL_HORA",
    ),
    "perfiles.dbf": ("CODIGO", "COD_EXAM"),
}
BASE_DBF = "Archivos DBF"


@dataclass(frozen=True)
class Chequeo:
    base: str
    nombre: str
    estado: str  # OK | FALLA | N/C
    detalle: str = ""


def _engine(cadena: str, timeout: int) -> Engine:
    engine = create_engine(build_url(cadena), poolclass=NullPool, future=True, connect_args={"timeout": timeout})
    fijar_timeout_de_consulta(engine, 30)  # el diagnóstico de main.py no debe colgar el arranque
    return engine


def objetos_a_verificar(base: str, procesar_resultados: bool) -> "list[tuple[str, tuple[str, ...]]]":
    """Sin envío de resultados (lo normal en BELÉN) no se exige InterLabDetalle ni los SP de Jalisco."""
    if procesar_resultados:
        return OBJETOS_REQUERIDOS.get(base, [])
    return [(o, p) for o, p in OBJETOS_REQUERIDOS.get(base, []) if o == "LabOrdenesInterop2"]


def verificar_base(
    engine: Engine, base: str, objetos: "list[tuple[str, tuple[str, ...]]] | None" = None
) -> "list[Chequeo]":
    """Conectividad + existencia y permisos de los objetos que necesita `base`."""
    resultados: "list[Chequeo]" = []
    try:
        with engine.connect() as conn:
            t0 = time.perf_counter()
            conn.execute(text("SELECT 1"))
            ms = (time.perf_counter() - t0) * 1000
            detalle = f"respondió en {ms:.0f} ms"
            try:
                servidor, nombre_db = conn.execute(text("SELECT @@SERVERNAME, DB_NAME()")).one()
                detalle += f" | servidor={servidor} base={nombre_db}"
            except Exception:
                pass  # solo informativo
            resultados.append(Chequeo(base, "Conectividad", OK, detalle))

            for objeto, permisos in (OBJETOS_REQUERIDOS.get(base, []) if objetos is None else objetos):
                try:
                    existe = conn.execute(text("SELECT OBJECT_ID(:o)"), {"o": objeto}).scalar()
                    if existe is None:
                        resultados.append(
                            Chequeo(base, objeto, FALLA, "no existe en esta base (o el usuario no puede verlo)")
                        )
                        continue
                    faltan = [
                        p
                        for p in permisos
                        if not conn.execute(
                            text("SELECT HAS_PERMS_BY_NAME(:o, 'OBJECT', :p)"), {"o": objeto, "p": p}
                        ).scalar()
                    ]
                    if faltan:
                        resultados.append(Chequeo(base, objeto, FALLA, "existe, pero falta permiso: " + ", ".join(faltan)))
                    else:
                        resultados.append(Chequeo(base, objeto, OK, "existe, permisos: " + ", ".join(permisos)))
                except Exception as exc:
                    resultados.append(Chequeo(base, objeto, FALLA, describe_error(exc)))
    except Exception as exc:
        resultados.append(Chequeo(base, "Conectividad", FALLA, describe_error(exc)))
    return resultados


def verificar_dbf(dbf_dir: str) -> "list[Chequeo]":
    """Los DBF locales: existen, se pueden abrir para escribir (no están bloqueados) y traen los campos requeridos."""
    resultados: "list[Chequeo]" = []
    if not os.path.isdir(dbf_dir):
        return [Chequeo(BASE_DBF, "Carpeta", FALLA, f"no existe {dbf_dir}")]
    resultados.append(Chequeo(BASE_DBF, "Carpeta", OK, dbf_dir))
    for archivo, campos in DBF_REQUERIDOS.items():
        ruta = os.path.join(dbf_dir, archivo)
        try:
            esq = dbf_io.comprobar_escritura(ruta)
            existentes = {c.nombre.upper() for c in esq.campos}
            faltan = [c for c in campos if c not in existentes]
            if faltan:
                resultados.append(Chequeo(BASE_DBF, archivo, FALLA, "faltan campos: " + ", ".join(faltan)))
            else:
                resultados.append(
                    Chequeo(BASE_DBF, archivo, OK, f"{esq.n_registros} registros, escribible, página de códigos {esq.codificacion}")
                )
        except Exception as exc:
            resultados.append(Chequeo(BASE_DBF, archivo, FALLA, describe_error(exc) if not isinstance(exc, dbf_io.DBFError) else str(exc)))
    return resultados


def verificar(settings: Settings) -> "list[Chequeo]":
    """Verifica MINSA y Resultado con las cadenas configuradas, y los DBF locales."""
    resultados: "list[Chequeo]" = []
    for base, cadena in (("MINSA", settings.minsa_conn_str), ("Resultado", settings.resultado_conn_str)):
        if not cadena:
            resultados.append(Chequeo(base, "Conectividad", SIN_CONFIGURAR, "cadena de conexión no configurada"))
            continue
        engine = _engine(cadena, settings.connect_timeout_seconds)
        try:
            chequeos = verificar_base(engine, base, objetos_a_verificar(base, settings.procesar_resultados))
        finally:
            engine.dispose()
        resultados.extend(chequeos)
    resultados.extend(verificar_dbf(settings.dbf_dir))
    return resultados


def todo_ok(resultados: "list[Chequeo]") -> bool:
    return bool(resultados) and all(r.estado == OK for r in resultados)


def conectividad_ok(resultados: "list[Chequeo]") -> bool:
    return all(r.estado == OK for r in resultados if r.nombre == "Conectividad")


def formatear(resultados: "list[Chequeo]", settings: "Settings | None" = None) -> str:
    sin_config = any(r.estado == SIN_CONFIGURAR for r in resultados)
    modo = "SIN CONFIGURAR (no es producción)" if sin_config else "PRODUCCION (bases reales)"
    lineas = ["=" * 78, " DIAGNOSTICO DE BASES DE DATOS", f" MODO: {modo}", "=" * 78]
    base_actual = None
    for r in resultados:
        if r.base != base_actual:
            base_actual = r.base
            cadena = None
            if settings is not None and r.base != BASE_DBF:
                cadena = settings.minsa_conn_str if r.base == "MINSA" else settings.resultado_conn_str
            destino = f"  ({describe(cadena)})" if cadena else ""
            lineas.append(f"\n {r.base}{destino}")
        lineas.append(f"   {_INDICADOR[r.estado]} {r.nombre:<38} {r.detalle}")
    fallas = sum(1 for r in resultados if r.estado == FALLA)
    lineas.append("\n" + "-" * 78)
    lineas.append(" RESULTADO: TODO OK" if todo_ok(resultados) else f" RESULTADO: {fallas} falla(s) / sin configurar")
    lineas.append("=" * 78)
    return "\n".join(lineas)


def main() -> int:
    from .config import get_settings

    settings = get_settings()
    resultados = verificar(settings)
    print(formatear(resultados, settings))
    return 0 if todo_ok(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
