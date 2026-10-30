import os
import struct
from datetime import date

import pytest

from orden_belen import dbf_io

from conftest import DLHNSE2_CAMPOS, construir_dbf


def _ruta(dbf_dir: str) -> str:
    return os.path.join(dbf_dir, "dlhnse2.dbf")


def _crudo(ruta: str, n: int) -> bytes:
    esq = dbf_io.leer_esquema(ruta)
    with open(ruta, "rb") as f:
        f.seek(esq.longitud_cabecera + n * esq.longitud_registro)
        return f.read(esq.longitud_registro)


def test_esquema_real_de_dlhnse2(dbf_dir) -> None:
    esq = dbf_io.leer_esquema(_ruta(dbf_dir))
    assert esq.longitud_registro == 288
    assert esq.codificacion == "cp437"
    assert len(esq.campos) == 50
    assert esq.campo("dl_tipdoc").longitud == 1


def test_agregar_actualiza_contador_y_marca_de_fin(dbf_dir) -> None:
    ruta = _ruta(dbf_dir)
    dbf_io.agregar_registros(ruta, [{"dl_numero": "1511088"}, {"dl_numero": "1511089"}])
    esq = dbf_io.leer_esquema(ruta)
    assert esq.n_registros == 2
    with open(ruta, "rb") as f:
        contenido = f.read()
    assert len(contenido) == esq.longitud_cabecera + 2 * esq.longitud_registro + 1
    assert contenido[-1:] == b"\x1a"
    hoy = date.today()
    assert tuple(contenido[1:4]) == (hoy.year - 1900, hoy.month, hoy.day)


def test_append_blank_deja_texto_en_espacios_y_numericos_en_cero(dbf_dir) -> None:
    ruta = _ruta(dbf_dir)
    dbf_io.agregar_registros(ruta, [{"dl_numero": "1"}])
    esq = dbf_io.leer_esquema(ruta)
    reg = _crudo(ruta, 0)
    assert reg[0:1] == b" "  # no borrado
    c = esq.campo("DL_EDAD")
    assert reg[c.desplazamiento : c.desplazamiento + c.longitud] == b"  0"
    c = esq.campo("DL_TALLA")
    assert reg[c.desplazamiento : c.desplazamiento + c.longitud] == b"    0"
    c = esq.campo("DL_CAMA")
    assert reg[c.desplazamiento : c.desplazamiento + c.longitud] == b" " * 12
    c = esq.campo("DL_FECNAC")
    assert reg[c.desplazamiento : c.desplazamiento + c.longitud] == b" " * 8


def test_replace_trunca_al_ancho_del_campo(dbf_dir) -> None:
    ruta = _ruta(dbf_dir)
    dbf_io.agregar_registros(ruta, [{"dl_tipdoc": "MDN", "dl_cexame": "ABCDEFGHIJ", "dl_numero": "1"}])
    r = next(dbf_io.leer_registros(ruta))
    assert r["DL_TIPDOC"] == "M"  # C(1): 'MDN' -> 'M', igual que VFP
    assert r["DL_CEXAME"] == "ABCDEFG"  # C(7)


def test_texto_en_pagina_de_codigos_dos_437(dbf_dir) -> None:
    ruta = _ruta(dbf_dir)
    dbf_io.agregar_registros(ruta, [{"dl_nombre": "NUÑEZ ÁLVARO"}])
    esq = dbf_io.leer_esquema(ruta)
    c = esq.campo("DL_NOMBRE")
    crudo = _crudo(ruta, 0)[c.desplazamiento : c.desplazamiento + c.longitud]
    assert crudo.startswith(b"NU\xa5EZ ")  # Ñ = 0xA5 en DOS 437 (por eso otros procesos veían '¥')
    assert b"ALVARO" in crudo  # 'Á' no existe en 437: se aproxima sin tilde
    assert next(dbf_io.leer_registros(ruta))["DL_NOMBRE"].startswith("NUÑEZ ")


def test_fechas_y_campos_desconocidos(dbf_dir) -> None:
    ruta = _ruta(dbf_dir)
    dbf_io.agregar_registros(ruta, [{"dl_fecexa": date(2025, 10, 14), "dl_fecnac": None}])
    r = next(dbf_io.leer_registros(ruta))
    assert r["DL_FECEXA"] == date(2025, 10, 14)
    assert r["DL_FECNAC"] is None
    antes = open(ruta, "rb").read()
    with pytest.raises(dbf_io.DBFError):
        dbf_io.agregar_registros(ruta, [{"dl_numero": "2"}, {"no_existe": "x"}])
    assert open(ruta, "rb").read() == antes  # un error no deja el archivo a medias


def test_no_pisa_registros_existentes_ni_pierde_los_borrados(tmp_path) -> None:
    ruta = str(tmp_path / "t.dbf")
    construir_dbf(ruta, DLHNSE2_CAMPOS, [(True, {"DL_NUMERO": "VIEJO"}), (False, {"DL_NUMERO": "ACTIVO"})], con_fin=False)
    dbf_io.agregar_registros(ruta, [{"dl_numero": "NUEVO"}])
    assert [r["DL_NUMERO"] for r in dbf_io.leer_registros(ruta)] == ["ACTIVO", "NUEVO"]  # SET DELETE ON
    assert [r["DL_NUMERO"] for r in dbf_io.leer_registros(ruta, omitir_borrados=False)] == ["VIEJO", "ACTIVO", "NUEVO"]
    assert dbf_io.leer_esquema(ruta).n_registros == 3


def test_archivo_inexistente_o_truncado(tmp_path) -> None:
    with pytest.raises(dbf_io.DBFError):
        dbf_io.agregar_registros(str(tmp_path / "no_existe.dbf"), [{"dl_numero": "1"}])
    ruta = str(tmp_path / "cortado.dbf")
    construir_dbf(ruta, DLHNSE2_CAMPOS, [(False, {"DL_NUMERO": "1"})])
    with open(ruta, "r+b") as f:
        f.truncate(os.path.getsize(ruta) - 100)
    with pytest.raises(dbf_io.DBFError):
        dbf_io.agregar_registros(ruta, [{"dl_numero": "2"}])


def test_encabezado_inconsistente_se_rechaza(tmp_path) -> None:
    ruta = str(tmp_path / "malo.dbf")
    construir_dbf(ruta, DLHNSE2_CAMPOS)
    with open(ruta, "r+b") as f:
        f.seek(10)
        f.write(struct.pack("<H", 999))  # longitud de registro que no cuadra con los campos
    with pytest.raises(dbf_io.DBFError):
        dbf_io.leer_esquema(ruta)
