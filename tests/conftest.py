"""Fixtures compartidas: DBF sintéticos con la estructura real de dlhnse2.dbf y perfiles.dbf.

Nunca se usa el archivo de producción: cada test construye el suyo en una carpeta temporal.
"""

from __future__ import annotations

import struct
from datetime import date

import pytest

# Estructura real de C:\ORDENES\ORDEN\dlhnse2.dbf (dBase III, página de códigos DOS 437, registro de 288 bytes).
DLHNSE2_CAMPOS = [
    ("DL_NUMERO", "C", 10, 0), ("DL_FECEXA", "D", 8, 0), ("DL_CEXAME", "C", 7, 0), ("DL_CTUBO", "C", 3, 0),
    ("DL_CMUES", "C", 2, 0), ("DL_CODLAB", "C", 3, 0), ("DL_CODSEC", "C", 2, 0), ("DL_SEDE", "C", 4, 0),
    ("DL_CMEDICO", "C", 5, 0), ("DL_TASE", "C", 2, 0), ("DL_CAMA", "C", 12, 0), ("DL_NSEG", "C", 15, 0),
    ("DL_HISTOR", "C", 10, 0), ("DL_NOMBRE", "C", 40, 0), ("DL_SEXO", "C", 1, 0), ("DL_EDAD", "N", 3, 0),
    ("DL_FECNAC", "D", 8, 0), ("DL_UBINAC", "C", 4, 0), ("DL_UBIDOM", "C", 4, 0), ("DL_ESTCIV", "C", 1, 0),
    ("DL_GRAINS", "C", 2, 0), ("DL_TIPDOC", "C", 1, 0), ("DL_NDOCUM", "C", 10, 0), ("DL_TELEFO", "C", 10, 0),
    ("DL_TALLA", "N", 5, 0), ("DL_FECREG", "D", 8, 0), ("DL_HORREG", "C", 8, 0), ("DL_TIPORD", "C", 1, 0),
    ("DL_ACTMED", "C", 7, 0), ("DL_NUMERA", "C", 4, 0), ("DL_ACTOMA", "C", 7, 0), ("DL_CPRO", "C", 3, 0),
    ("DL_CSER", "C", 8, 0), ("DL_PLANTI", "C", 1, 0), ("DL_CTR", "C", 1, 0), ("DL_AUTO", "C", 1, 0),
    ("DL_CCAS", "C", 3, 0), ("DL_USUARIO", "C", 8, 0), ("DL_IP", "C", 15, 0), ("DL_TERMINA", "C", 10, 0),
    ("DL_PERFIL", "C", 7, 0), ("DL_POLPRO", "C", 3, 0), ("DL_DIAG", "C", 6, 0), ("DL_HORA", "C", 8, 0),
    ("DL_LAB01", "C", 1, 0), ("DL_LAB02", "C", 1, 0), ("DL_LAB03", "C", 1, 0), ("DL_LAB04", "C", 1, 0),
    ("DL_LAB05", "C", 1, 0), ("DL_LAB06", "C", 1, 0),
]

# Estructura real de perfiles.dbf (VFP, página de códigos 1252).
PERFILES_CAMPOS = [("CODIGO", "C", 10, 0), ("NOMBRE", "C", 25, 0), ("COD_EXAM", "C", 10, 0), ("FLAG", "C", 1, 0)]


def construir_dbf(path, campos, filas=(), *, version=0x03, codepage=0x01, con_fin=True) -> None:
    """Escribe un DBF. `filas` = lista de (borrado: bool, {CAMPO: texto ya con el ancho justo o sin él})."""
    longitud_registro = 1 + sum(c[2] for c in campos)
    longitud_cabecera = 32 + 32 * len(campos) + 1
    cab = bytearray(32)
    cab[0] = version
    cab[1:4] = bytes((24, 1, 1))
    cab[4:8] = struct.pack("<I", len(filas))
    cab[8:10] = struct.pack("<H", longitud_cabecera)
    cab[10:12] = struct.pack("<H", longitud_registro)
    cab[29] = codepage
    descriptores = b""
    for nombre, tipo, ln, dec in campos:
        d = bytearray(32)
        d[: len(nombre)] = nombre.encode("ascii")
        d[11] = ord(tipo)
        d[16] = ln
        d[17] = dec
        descriptores += bytes(d)
    cuerpo = b""
    for borrado, valores in filas:
        reg = b"*" if borrado else b" "
        for nombre, tipo, ln, _ in campos:
            v = str(valores.get(nombre, "")).encode("cp1252")[:ln]
            reg += v.rjust(ln, b" ") if tipo == "N" else v.ljust(ln, b" ")
        cuerpo += reg
    with open(path, "wb") as f:
        f.write(bytes(cab) + descriptores + b"\r" + cuerpo + (b"\x1a" if con_fin else b""))


@pytest.fixture
def dbf_dir(tmp_path):
    """Carpeta con dlhnse2.dbf vacío y perfiles.dbf con 3 exámenes para el perfil '80051' (uno borrado)."""
    construir_dbf(tmp_path / "dlhnse2.dbf", DLHNSE2_CAMPOS, version=0x03, codepage=0x01)
    construir_dbf(
        tmp_path / "perfiles.dbf",
        PERFILES_CAMPOS,
        [
            (False, {"CODIGO": "80051", "COD_EXAM": "EX-1"}),
            (False, {"CODIGO": "OTRO", "COD_EXAM": "ZZ"}),
            (False, {"CODIGO": "80051", "COD_EXAM": "EX-2   "}),
            (True, {"CODIGO": "80051", "COD_EXAM": "BORRADO"}),  # SET DELETE ON: no se lee
            (False, {"CODIGO": "80051", "COD_EXAM": "EX-3"}),
        ],
        version=0x30,
        codepage=0x03,
    )
    return str(tmp_path)


@pytest.fixture
def hoy() -> date:
    return date.today()


@pytest.fixture(autouse=True)
def _repeticiones_limpias(monkeypatch):
    """Los servicios recuerdan los errores ya escritos; cada test empieza sin memoria."""
    from orden_belen.errores import Repeticiones
    from orden_belen.services import ordenes_sync, resultados_sync

    monkeypatch.setattr(ordenes_sync, "_repetidos", Repeticiones())
    monkeypatch.setattr(resultados_sync, "_repetidos", Repeticiones())
