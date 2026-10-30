"""Utilidades para cadenas de conexión ODBC de SQL Server (sin tkinter).

La GUI pide los datos por campos (servidor, base, usuario, contraseña) y `build_conn_str`
arma la cadena, p. ej.
`DRIVER={ODBC Driver 17 for SQL Server};SERVER=192.168.2.12;DATABASE=SIGH;UID=usuario;PWD=clave`.
Siempre se usa el puerto por defecto de SQL Server (1433, o el que resuelva SQL Browser para una
instancia con nombre): no se pide puerto ni se usan DSN de ODBC.
`parse_fields` hace el camino inverso para volver a llenar el formulario desde SQLite.
Nada de lo que devuelven `describe()`, `describe_error()` y `vista_segura()` incluye la contraseña.
"""

from __future__ import annotations

import re

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.pool import NullPool

try:
    import pyodbc as _pyodbc

    # SQLAlchemy ya tiene su pool. Con el de ODBC activo (pyodbc lo activa por defecto) una conexión "cerrada" tras
    # una caída del servidor sigue viva por debajo y puede reutilizarse. Debe fijarse antes de la primera conexión.
    _pyodbc.pooling = False
except ImportError:  # sin pyodbc la GUI aún abre (modo demo / configuración)
    pass

CAMPOS_VACIOS = {
    "driver": "",
    "servidor": "",
    "base": "",
    "usuario": "",
    "password": "",
}

_DRIVERS_PREFERIDOS = (
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
)


def drivers_sql_server() -> "list[str]":
    """Drivers ODBC de SQL Server instalados en esta PC (el más recomendado primero).

    El 17 va antes que el 18: si están los dos se usa el 17, porque el 18 exige cifrado y
    certificado válido por defecto y falla contra servidores locales/antiguos. Si solo hay 18,
    se usa el 18 (`resolver_cadena` confía en su certificado automáticamente).
    """
    try:
        import pyodbc

        instalados = [d for d in pyodbc.drivers() if "SQL Server" in d]
    except Exception:
        instalados = []
    ordenados = [d for d in _DRIVERS_PREFERIDOS if d in instalados] + [d for d in instalados if d not in _DRIVERS_PREFERIDOS]
    return ordenados


def driver_automatico() -> "str | None":
    """Driver que se usará cuando la cadena no fije uno (None si no hay ninguno instalado)."""
    instalados = drivers_sql_server()
    return instalados[0] if instalados else None


# Servidor dado por IP (con puerto opcional), p. ej. 192.168.2.12 de Galeno.
_RE_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(,\d+)?$")


def _version_driver(driver: str) -> int:
    """Número de versión de 'ODBC Driver NN for SQL Server' (0 si no aplica)."""
    m = re.search(r"ODBC Driver (\d+)", driver)
    return int(m.group(1)) if m else 0


def resolver_cadena(cadena: str) -> str:
    """Completa automáticamente lo que el usuario no tiene que decidir:

    * driver: el ODBC 17/18 que haya instalado (si la cadena no fija uno);
    * autenticación: sin usuario => autenticación de Windows (Trusted_Connection); con usuario, SQL Server;
    * certificado: con ODBC Driver 18 (cifra por defecto y exige certificado válido) se confía
      en el certificado del servidor; con el 17 no hace falta;
    * protocolo: si el servidor es una IP remota se fuerza TCP (`tcp:`, mismo puerto por defecto). Sin esto, cuando
      el servidor está caído el driver prueba además Named Pipes y el intento tarda hasta ~26 s en vez de 5 s
      (medido contra 192.168.2.12), con un error engañoso. Nombres e instancias (`PC\\SQLEXPRESS`) no se tocan:
      una instancia Express local puede tener TCP desactivado.
    """
    pares = parse_conn_str(cadena)
    cadena = cadena.rstrip("; ")
    if "DRIVER" not in pares:
        driver = driver_automatico()
        if driver:
            cadena = "DRIVER={" + driver + "};" + cadena
            pares["DRIVER"] = driver
    if not pares.get("UID") and "TRUSTED_CONNECTION" not in pares and "AUTHENTICATION" not in pares:
        cadena += ";Trusted_Connection=yes"
    servidor = pares.get("SERVER", "")
    if _RE_IPV4.match(servidor) and not servidor.startswith("127."):
        cadena = re.sub(r"(?i)(^|;)(\s*SERVER\s*=\s*\{?)", r"\1\2tcp:", cadena, count=1)
    if (
        _version_driver(pares.get("DRIVER", "")) >= 18
        and "TRUSTSERVERCERTIFICATE" not in pares
        and "ENCRYPT" not in pares
    ):
        cadena += ";TrustServerCertificate=yes"
    return cadena


def autenticacion(campos: "dict") -> str:
    """Texto para la GUI: qué autenticación se usará según lo que se llenó."""
    return "SQL Server" if campos.get("usuario") else "Windows"


# --- cadena <-> campos ---------------------------------------------------------


def _valor(texto: str) -> str:
    """Escapa un valor ODBC: entre llaves si trae ; { } = o espacios en los bordes."""
    if any(c in texto for c in ";{}=") or texto != texto.strip():
        return "{" + texto.replace("}", "}}") + "}"
    return texto


def build_conn_str(campos: "dict") -> str:
    """Arma la cadena ODBC a partir de los campos del formulario."""
    c = {**CAMPOS_VACIOS, **campos}
    partes = []
    if c["driver"]:
        partes.append("DRIVER={" + c["driver"] + "}")
    if c["servidor"]:
        partes.append(f"SERVER={_valor(c['servidor'])}")  # sin puerto: se usa el por defecto
    if c["base"]:
        partes.append(f"DATABASE={_valor(c['base'])}")
    if c["usuario"]:
        partes.append(f"UID={_valor(c['usuario'])}")
    if c["password"]:
        partes.append(f"PWD={_valor(c['password'])}")
    return ";".join(partes)


def parse_conn_str(cadena: str) -> "dict[str, str]":
    """Separa `CLAVE=valor;...` respetando valores entre llaves ({a;b}, con }} para una llave)."""
    pares: "dict[str, str]" = {}
    i, n = 0, len(cadena)
    while i < n:
        igual, punto = cadena.find("=", i), cadena.find(";", i)
        if igual == -1 or (punto != -1 and punto < igual):  # trozo sin '=': se ignora
            i = punto + 1 if punto != -1 else n
            continue
        clave = cadena[i:igual].strip().upper()
        i = igual + 1
        while i < n and cadena[i] == " ":
            i += 1
        if i < n and cadena[i] == "{":
            i += 1
            valor = ""
            while i < n:
                if cadena[i] == "}":
                    if i + 1 < n and cadena[i + 1] == "}":
                        valor += "}"
                        i += 2
                        continue
                    i += 1
                    break
                valor += cadena[i]
                i += 1
            punto = cadena.find(";", i)
            i = punto + 1 if punto != -1 else n
        else:
            punto = cadena.find(";", i)
            fin = punto if punto != -1 else n
            valor = cadena[i:fin].strip()
            i = fin + 1
        pares[clave] = valor
    return pares


def parse_fields(cadena: str) -> "dict":
    """Camino inverso de `build_conn_str`: rellena el formulario desde una cadena guardada."""
    p = parse_conn_str(cadena)
    servidor = p.get("SERVER", "").partition(",")[0]  # si una cadena antigua traía puerto, se descarta
    return {
        "driver": p.get("DRIVER", ""),
        "servidor": servidor,
        "base": p.get("DATABASE", ""),
        "usuario": p.get("UID", ""),
        "password": p.get("PWD", ""),
    }


def campos_vacios(campos: "dict") -> bool:
    """True si el usuario no llenó nada (esa conexión queda 'sin configurar')."""
    return not any(campos.get(k) for k in ("servidor", "base", "usuario", "password"))


def validar_campos(campos: "dict") -> "str | None":
    """Mensaje de error si faltan datos imprescindibles, o None si están completos."""
    if not campos.get("servidor"):
        return "Indique el servidor."
    return None


def vista_segura(campos: "dict") -> str:
    """La cadena que se usará al conectar (con el driver detectado), con la contraseña oculta."""
    return resolver_cadena(build_conn_str({**campos, "password": "********" if campos.get("password") else ""}))


# --- validación, SQLAlchemy y pruebas --------------------------------------------


def validar(cadena: str) -> "str | None":
    """Devuelve un mensaje de error si la cadena no sirve, o None si es válida."""
    if not cadena.strip():
        return "Escriba la cadena de conexión."
    pares = parse_conn_str(cadena)
    if "SERVER" not in pares:
        return "La cadena debe incluir SERVER=..."
    return None


def build_url(cadena: str) -> URL:
    """URL de SQLAlchemy a partir de una cadena ODBC (usuario/clave incluidos; driver, autenticación y certificado automáticos)."""
    return URL.create("mssql+pyodbc", query={"odbc_connect": resolver_cadena(cadena)})


def describe(cadena: str) -> str:
    """Nombre corto y seguro para mostrar: `servidor/base`, Nunca usuario ni clave."""
    pares = parse_conn_str(cadena)
    servidor, base = pares.get("SERVER"), pares.get("DATABASE")
    if servidor and base:
        return f"{servidor}/{base}"
    return servidor or "?"


def describe_error(exc: BaseException) -> str:
    """Resumen de una línea de una excepción (pyodbc no incluye credenciales en sus mensajes)."""
    causa = getattr(exc, "orig", None) or exc
    mensaje = " ".join(str(causa).replace("\\r\\n", " ").split())  # pyodbc trae \r\n escapados en el texto
    return f"{type(causa).__name__}: {mensaje[:160]}"


def fijar_timeout_de_consulta(engine: Engine, segundos: int) -> None:
    """Timeout por consulta (pyodbc: 0 = sin límite). El `timeout` de connect_args es solo el del login."""

    @event.listens_for(engine, "connect")
    def _timeout(dbapi_connection, _record) -> None:
        dbapi_connection.timeout = segundos


def probar_conexion(cadena: str, timeout: int = 5) -> "tuple[bool, str]":
    """Abre una conexión de prueba (SELECT 1). Devuelve (ok, mensaje) sin exponer la cadena."""
    error = validar(cadena)
    if error:
        return False, error
    pares = parse_conn_str(cadena)
    if "DRIVER" not in pares and driver_automatico() is None:
        return False, "No hay ningún driver ODBC de SQL Server instalado (instale ODBC Driver 17 o 18)."
    engine = create_engine(build_url(cadena), poolclass=NullPool, future=True, connect_args={"timeout": timeout})
    fijar_timeout_de_consulta(engine, 15)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, f"Conexión OK [{describe(cadena)}]"
    except Exception as exc:
        return False, describe_error(exc)
    finally:
        engine.dispose()
