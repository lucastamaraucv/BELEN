# -*- mode: python ; coding: utf-8 -*-
# OrdenBelen.exe — PyInstaller, UN SOLO ARCHIVO (--onefile) y sin consola. Compilar con compilar_exe.bat.
#
# Todo va dentro del .exe (Python, SQLAlchemy, pyodbc, tkinter): en la PC destino basta copiar OrdenBelen.exe.
# config\ y logs\ se crean junto al .exe (ver src/orden_belen/rutas.py).
#
# Al arrancar, un .exe --onefile se descomprime en una carpeta _MEIxxxx. Por defecto es %TEMP%, que los limpiadores
# de Windows vacían: con la app abierta 24x7 podrían borrar archivos que usa (p. ej. los del diálogo "Exportar log").
# Por eso se descomprime en %LOCALAPPDATA%\OrdenBelen (el bootloader de Windows expande la variable). Se borra solo
# al cerrar cada instancia.
import os

from PyInstaller.utils.hooks import collect_submodules

# create_engine("mssql+pyodbc://...") carga el dialecto por nombre: PyInstaller no lo ve solo.
hiddenimports = ["sqlalchemy.dialects.mssql.pyodbc"] + collect_submodules("sqlalchemy.dialects.mssql")

# Conexiones de producción incluidas: si existe config\orden_belen.db al compilar, va dentro del .exe y la primera vez
# que se abre en una PC se copia junto al .exe (lanzador.sembrar_configuracion) -> arranca en PRODUCCIÓN, no en demo.
# OJO: el .exe lleva las contraseñas de las bases; no compartirlo fuera del hospital.
_config = os.path.join(SPECPATH, "config", "orden_belen.db")
datas = [(_config, "config_incluida")] if os.path.isfile(_config) else []

a = Analysis(
    [os.path.join(SPECPATH, "OrdenBelen.py")],
    pathex=[os.path.join(SPECPATH, "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "_pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OrdenBelen",
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir="%LOCALAPPDATA%\\OrdenBelen",
    console=False,
    icon=os.path.join(SPECPATH, "icono.ico"),
)
