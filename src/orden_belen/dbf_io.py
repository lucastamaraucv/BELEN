"""Lectura y escritura de tablas DBF (dBase/FoxPro) sin dependencias externas.

Réplica del acceso local que hace el Fox original (formprincipal.SCT / temporizado.txt):

    use &rutan/dlhnse2  alias vordenobi    (APPEND BLANK + REPLACE de cada campo)
    use &rutan/perfiles alias vperfiles    (LOCATE / CONTINUE con SET DELETE ON)

Comportamientos de VFP que se replican a propósito:
  * REPLACE de texto trunca al ancho del campo (p. ej. DL_TIPDOC es C(1): 'MDN' se guarda como 'M').
  * Texto en la página de códigos de la tabla (dlhnse2.dbf es DOS 437: la 'Ñ' se guarda como 0xA5).
  * APPEND BLANK: texto en espacios, numéricos en 0, fechas en blanco.
  * SET DELETE ON: los registros marcados como borrados no se leen.
"""

from __future__ import annotations

import os
import struct
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterator

# Byte 29 del encabezado -> codificación (los que usan las tablas de Datalab/Fox).
_CODEPAGES = {0x01: "cp437", 0x02: "cp850", 0x03: "cp1252", 0x57: "cp1252", 0xC8: "cp1250", 0x00: "cp437"}

FIN_ARCHIVO = b"\x1a"
_REINTENTOS_APERTURA = 5
_ESPERA_REINTENTO_SEGUNDOS = 0.2


class DBFError(Exception):
    """El archivo DBF no existe, está bloqueado, está corrupto o no tiene los campos esperados."""


@dataclass(frozen=True)
class Campo:
    nombre: str
    tipo: str  # C, D, N, F, L ...
    longitud: int
    decimales: int
    desplazamiento: int  # dentro del registro (el byte 0 es la marca de borrado)


@dataclass(frozen=True)
class Esquema:
    version: int
    n_registros: int
    longitud_cabecera: int
    longitud_registro: int
    codificacion: str
    campos: "tuple[Campo, ...]"

    def campo(self, nombre: str) -> Campo:
        nombre = nombre.upper()
        for c in self.campos:
            if c.nombre.upper() == nombre:
                return c
        raise DBFError(f"El DBF no tiene el campo {nombre}")


def _leer_encabezado(f) -> Esquema:
    cab = f.read(32)
    if len(cab) < 32:
        raise DBFError("Encabezado DBF incompleto")
    version = cab[0]
    n_registros, longitud_cabecera, longitud_registro = struct.unpack("<IHH", cab[4:12])
    campos = []
    desplazamiento = 1
    while True:
        d = f.read(32)
        if len(d) < 32 or d[0] == 0x0D:
            break
        nombre = d[:11].split(b"\0")[0].decode("ascii", "replace")
        campos.append(Campo(nombre, chr(d[11]), d[16], d[17], desplazamiento))
        desplazamiento += d[16]
    if desplazamiento != longitud_registro:
        raise DBFError(f"Estructura inconsistente (suma de campos {desplazamiento} <> registro {longitud_registro})")
    return Esquema(version, n_registros, longitud_cabecera, longitud_registro, _CODEPAGES.get(cab[29], "cp1252"), tuple(campos))


def _abrir(path: str, modo: str):
    """Abre el archivo con reintentos cortos: otro proceso puede tenerlo tomado un instante."""
    ultimo: "Exception | None" = None
    for _ in range(_REINTENTOS_APERTURA):
        try:
            return open(path, modo)
        except FileNotFoundError as exc:
            raise DBFError(f"No existe el archivo {path}") from exc
        except OSError as exc:  # PermissionError: en uso / bloqueado
            ultimo = exc
            time.sleep(_ESPERA_REINTENTO_SEGUNDOS)
    raise DBFError(f"No se pudo abrir {path}: {ultimo}") from ultimo


def leer_esquema(path: str) -> Esquema:
    with _abrir(path, "rb") as f:
        return _leer_encabezado(f)


def leer_registros(path: str, *, omitir_borrados: bool = True) -> "Iterator[dict[str, Any]]":
    """Registros en orden físico, como dicts {CAMPO: valor}. Texto sin espacios a la derecha.

    `omitir_borrados=True` replica SET DELETE ON (el programa principal lo activa)."""
    with _abrir(path, "rb") as f:
        esq = _leer_encabezado(f)
        f.seek(esq.longitud_cabecera)
        for _ in range(esq.n_registros):
            raw = f.read(esq.longitud_registro)
            if len(raw) < esq.longitud_registro:
                raise DBFError(f"{path}: el archivo termina antes de los {esq.n_registros} registros declarados")
            if omitir_borrados and raw[0:1] == b"*":
                continue
            yield {c.nombre.upper(): _decodificar(raw[c.desplazamiento : c.desplazamiento + c.longitud], c, esq.codificacion) for c in esq.campos}


def _decodificar(raw: bytes, campo: Campo, codificacion: str) -> Any:
    if campo.tipo == "C":
        return raw.decode(codificacion, "replace").rstrip(" ")
    texto = raw.decode("ascii", "replace").strip()
    if campo.tipo == "D":
        return datetime.strptime(texto, "%Y%m%d").date() if texto else None
    if campo.tipo in ("N", "F"):
        return Decimal(texto) if texto else Decimal(0)
    return texto


# --- escritura ------------------------------------------------------------------------


def _codificar_texto(texto: str, codificacion: str) -> bytes:
    """Texto -> bytes de la página de códigos de la tabla. Lo que no existe en ella (p. ej. 'Á' en DOS 437)
    se aproxima quitándole la tilde, y si aun así no cabe queda '?'."""
    try:
        return texto.encode(codificacion)
    except UnicodeEncodeError:
        pass
    salida = bytearray()
    for ch in texto:
        try:
            salida += ch.encode(codificacion)
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFD", ch)[0]
            try:
                salida += base.encode(codificacion)
            except UnicodeEncodeError:
                salida += b"?"
    return bytes(salida)


def _valor_en_blanco(campo: Campo) -> bytes:
    """APPEND BLANK: texto y fecha en espacios; numérico en 0 alineado a la derecha."""
    if campo.tipo in ("N", "F"):
        return b"0".rjust(campo.longitud)
    return b" " * campo.longitud


def _formatear(valor: Any, campo: Campo, codificacion: str) -> bytes:
    if valor is None:
        return _valor_en_blanco(campo)
    if campo.tipo == "C":
        return _codificar_texto(str(valor), codificacion)[: campo.longitud].ljust(campo.longitud, b" ")  # REPLACE trunca
    if campo.tipo == "D":
        if isinstance(valor, datetime):
            valor = valor.date()
        if isinstance(valor, date):
            return valor.strftime("%Y%m%d").encode("ascii")
        raise DBFError(f"{campo.nombre}: se esperaba una fecha y llegó {type(valor).__name__}")
    if campo.tipo in ("N", "F"):
        texto = f"{Decimal(str(valor)):.{campo.decimales}f}"
        if len(texto) > campo.longitud:
            raise DBFError(f"{campo.nombre}: el número {texto} no cabe en {campo.longitud} posiciones")
        return texto.rjust(campo.longitud).encode("ascii")
    raise DBFError(f"{campo.nombre}: tipo de campo {campo.tipo!r} no soportado")


def _registro(esq: Esquema, valores: "dict[str, Any]") -> bytes:
    conocidos = {c.nombre.upper() for c in esq.campos}
    desconocidos = [k for k in valores if k.upper() not in conocidos]
    if desconocidos:
        raise DBFError(f"El DBF no tiene los campos: {', '.join(sorted(desconocidos))}")
    dados = {k.upper(): v for k, v in valores.items()}
    partes = [b" "]  # registro no borrado
    for c in esq.campos:
        partes.append(_formatear(dados[c.nombre.upper()], c, esq.codificacion) if c.nombre.upper() in dados else _valor_en_blanco(c))
    return b"".join(partes)


def agregar_registros(path: str, filas: "list[dict[str, Any]]") -> int:
    """Equivalente a repetir `APPEND BLANK` + `REPLACE` por cada fila. Devuelve cuántos registros agregó.

    Todas las filas se arman antes de tocar el archivo (un campo inválido no escribe nada). Primero se escriben y
    se graban en disco las filas con la marca de fin, y recién después el contador del encabezado: si el proceso o
    la luz se cortan a mitad, quedan filas sin contar (invisibles para Datalab) que la siguiente escritura
    sobrescribe; el contador no queda por delante de las filas (salvo una falla del propio disco), lo que dejaría
    el DBF "truncado" y frenaría las órdenes.
    No es atómico ni usa el bloqueo de VFP."""
    if not filas:
        return 0
    with _abrir(path, "r+b") as f:
        esq = _leer_encabezado(f)
        datos = b"".join(_registro(esq, fila) for fila in filas)
        f.seek(0, os.SEEK_END)
        fin_logico = esq.longitud_cabecera + esq.n_registros * esq.longitud_registro
        if f.tell() < fin_logico:
            raise DBFError(f"{path}: archivo truncado (tiene menos bytes que sus {esq.n_registros} registros)")
        f.seek(fin_logico)
        f.write(datos + FIN_ARCHIVO)
        f.truncate()
        f.flush()
        os.fsync(f.fileno())  # filas en disco antes de publicar el nuevo contador
        hoy = date.today()
        f.seek(1)
        f.write(bytes((hoy.year - 1900, hoy.month, hoy.day)))
        f.seek(4)
        f.write(struct.pack("<I", esq.n_registros + len(filas)))
        f.flush()
        os.fsync(f.fileno())
    return len(filas)


def comprobar_escritura(path: str) -> Esquema:
    """Abre el archivo para lectura/escritura sin modificarlo (¿existe, no está bloqueado, estructura válida?)."""
    with _abrir(path, "r+b") as f:
        return _leer_encabezado(f)
