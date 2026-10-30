# orden-belen (réplica en Python)

Réplica funcional en Python de `CODIGO FUENTE/codigo 1.txt` y
`CODIGO FUENTE/temporizado.txt` (VFP). Dos workers en hilos independientes, cada
uno con su propio pool de conexiones SQLAlchemy hacia SQL Server:

- **`resultados-sync`** (`services/resultados_sync.py`): equivalente a
  `codigo 1.txt`. Ejecuta `sp_Minsa_EnviarResultadoMinsa` en la base
  Resultado/Jalisco, actualiza `InterLabDetalle` en MINSA/Nobilis y marca cada
  resultado como transferido vía `sp_MINSA_UpdateResultadosValidacion`.
- **`ordenes-sync`** (`services/ordenes_sync.py`): equivalente a
  `temporizado.txt` (el evento `Timer1.Timer`). Lee órdenes pendientes
  (`Flag=0`) de `LabOrdenesInterop2`, expande perfiles vía `vperfiles`, inserta
  en `vordenobi` para el sistema BIO y marca `Flag=2`.

## Cómo correr

Usa directamente el Python principal del sistema (sin entorno virtual). El
proyecto solo depende de librerías ya instaladas ahí: `SQLAlchemy`, `pyodbc`, `pytest`.

```bash
python -m pytest            # tests unitarios (sin BD real)
python -m orden_belen.main  # modo headless: arranca ambos workers (Ctrl+C para apagado ordenado)
python -m orden_belen.gui   # modo GUI: ventana con botones INICIAR/SALIR (ver abajo)
```

### Conexiones SQL Server (sin `.env`)

Se llenan en la pestaña **Conexiones SQL Server** de la GUI, un formulario por base (MINSA y
Resultado/Jalisco): servidor/instancia, base de datos, usuario y contraseña SQL. Siempre usa el puerto por
defecto de SQL Server (no se pide puerto) y no se usan DSN de ODBC. La app arma la cadena ODBC y la guarda en SQLite (`config/orden_belen.db`, no se versiona).

Se detecta solo, en cada conexión (no depende de la PC donde se configuró):
- **Driver ODBC**: el 17 o el 18 que haya instalado (el 17 primero si están los dos).
- **Autenticación**: con usuario => SQL Server; sin usuario => autenticación de Windows.
- **Certificado**: con ODBC Driver 18 (que cifra y exige certificado válido) se confía en el
  certificado del servidor automáticamente (`TrustServerCertificate=yes`); con el 17 no hace falta.

La contraseña va oculta (•••) con casilla *Mostrar*; *Probar conexión* hace un `SELECT 1`. Se puede
configurar solo una: sin MINSA únicamente se vigila la conexión de Resultado y los workers no
arrancan. Sin ninguna, la GUI entra en MODO DEMO.

### GUI (`gui.py`) — "ORDEN BELEN — Monitor LIS"

Panel oscuro tipo terminal hecho solo con `tkinter` (stdlib, sin instalar nada más):

- **Tarjetas**: CICLOS, PENDIENTES (`LabOrdenesInterop2.Flag=0`), RESULTADOS
  (transferidos por `resultados-sync`), ÓRDENES (procesadas por `ordenes-sync`) y ERRORES.
- **Monitor** tipo consola (`monitor.log`): líneas en verde / ámbar / rojo / celeste,
  solo lectura pero copiable, últimas 5000 líneas, botones *Exportar log* y *Limpiar*
  (limpiar solo borra la pantalla).
- **Barra inferior**: un indicador de color por cada SQL Server (MINSA y Resultado):
  verde = OK, rojo = sin conexión, ámbar = reconectando, azul = demo. Muestra el
  último ciclo, la pausa y el modo, y el botón DETENER / INICIAR (detiene de forma
  ordenada: termina el ciclo activo antes de marcar DETENIDO).
- Arranca solo al abrir (`AUTO_INICIAR` en `gui.py`). Si falta la configuración de
  conexión, entra en **MODO DEMO** con datos simulados (rotulado, sin tocar bases).
- Los errores y advertencias también se guardan en `logs/orden_belen_AAAA-MM-DD.log` (tope automático: 100 MB y 15 días; ver `COMO_FUNCIONA.md`, 5.1 y 5.2 para el modo 24x7).
- Al cerrar con el monitor activo pregunta si desea detenerlo y cerrar.

La lógica de negocio sigue en `services/`/`workers/`; el motor del monitor está en
`monitor.py` (sin tkinter) y `main.py` (headless) comparte los mismos workers.

Si `orden_belen` no se encuentra al ejecutar `python -m orden_belen.main` fuera
de `PYTHON/`, agrega `src/` al `PYTHONPATH` o instala el paquete en modo
editable una sola vez: `pip install -e .` (usa el mismo Python principal, no
crea entorno nuevo).

## Decisiones y supuestos (léelos antes de poner esto en producción)

1. **`nCn2`** (usado en `temporizado.txt` línea 179 para el `UPDATE Flag=2`) no
   se define en ese archivo — se asume la misma base MINSA que `nCn1`. Si en el
   sistema real es una tercera conexión distinta, agregar un tercer engine en
   `db.py` y pasarlo a `sync_ordenes`.
2. **`vperfiles` y `vordenobi` son DBF locales, no tablas SQL.** El Init de `formprincipal.SCT` hace
   `use &rutan/perfiles alias vperfiles` y `use &rutan/dlhnse2 alias vordenobi`, con
   `rutan = SYS(5)+"\ORDENES\ORDEN\\"`. Python escribe en **`C:\ORDENES\ORDEN\dlhnse2.dbf`** y lee
   `perfiles.dbf` de esa misma carpeta (`ORDEN_DBF_DIR` la cambia; módulo `dbf_io.py`, sin dependencias).
   Se replican: truncado de `REPLACE` al ancho del campo (`DL_TIPDOC` es C(1): `MDN` → `M`), página de códigos
   DOS 437 de la tabla (`Ñ` = 0xA5), `APPEND BLANK` (texto en blanco, numéricos en 0) y `SET DELETE ON`.
   `dl_hora` es la hora del sistema al procesar (`TIME()` de VFP). Requisito: Fox no debe estar escribiendo
   el mismo archivo a la vez.
3. **Réplica exacta de lectura/inserción de Fox**: Python hereda las reglas reales de
   VFP, incluidos sus "defectos": `FechaHoraResultado` con `SUBSTR` posicional (segundos
   siempre 0); `dl_cser = ALLTRIM(STR(Idservicio,3))` (sin ceros: 7 → `'7'`); documento
   nulo → `' '` y el `SN` por documento nulo NO se aplica (era código muerto);
   `STR(x,w)` redondea y da `'*'` si no cabe; `cod_exam` con ALLTRIM; búsqueda en
   `vperfiles` con ALLTRIM y comparación exacta (`==`); columnas sin distinguir mayúsculas.
4. **Lista fija de "códigos de perfil"**: la versión de `temporizado.txt` (a
   diferencia de otras variantes vistas en `FORM/`) **no** usa una lista
   hardcodeada de códigos — verifica pertenencia contra la tabla `vperfiles`
   directamente. Esta réplica sigue exactamente esa lógica.
5. **Credenciales**: el original las tiene en texto plano en el `.PRG`
   (`SQLConnect('MINSA','nobilis','********')`). Aquí se
   escriben en la GUI y se guardan en SQLite (`config/orden_belen.db`, fuera de control
   de versiones) en texto plano: protege ese archivo.
6. **Manejo de errores**: el original no tiene manejo de errores por fila (un
   fallo interrumpe el ciclo) y su `ON ERROR` global (`bus_error.PRG`) simplemente
   hace `QUIT` del proceso completo. Aquí cada fila se procesa en su propia
   transacción con `try/except`; un fallo se loguea y se continúa con la
   siguiente, sin matar el proceso.
7. **Concurrencia**: `resultados-sync` y `ordenes-sync` corren en hilos
   `daemon` separados (`workers/base_worker.py`), cada uno abre su propia
   `Session` de SQLAlchemy por ciclo desde un pool de conexiones (`QueuePool`,
   `pool_pre_ping=True`) — nunca se comparte una `Session` ni una conexión ODBC
   entre hilos, que es el modo correcto de usar SQLAlchemy con threads.
8. **Intervalo adaptativo**: igual que `thisform.timer1.interval=100` cuando no
   hay registros pendientes, el worker de órdenes usa un intervalo corto en modo
   "idle" y otro en modo "busy" (configurable con variables `ORDEN_*` opcionales).
9. **Red intermitente (SQL Server "se va y viene")**: el ODBC original de FoxPro
   no tenía timeout de conexión ni reintentos, así que una caída de red podía
   dejar el proceso colgado indefinidamente. Aquí:
   - `db.py` pasa `connect_args={"timeout": ORDEN_CONNECT_TIMEOUT_SECONDS}` a
     pyodbc, así un intento de conexión contra un servidor inalcanzable falla
     rápido en vez de colgarse.
   - `pool_pre_ping=True` descarta conexiones muertas del pool antes de usarlas.
   - `workers/base_worker.py` distingue errores de conexión/driver
     (`sqlalchemy.exc.DBAPIError`, que envuelve fallos de pyodbc como timeout,
     host inalcanzable o conexión reiniciada) de errores de datos, y aplica
     backoff exponencial (`ORDEN_BACKOFF_INITIAL_SECONDS` →
     `ORDEN_BACKOFF_MAX_SECONDS`, factor `ORDEN_BACKOFF_FACTOR`) en vez de
     reintentar cada 0.1s sin pausa. El backoff se resetea apenas un ciclo
     vuelve a tener éxito.
   - Nota: esto no cubre una caída de red a mitad de una transacción larga (un
     `INSERT`/`UPDATE` ya en curso). Si eso ocurre, la fila falla, se hace
     rollback y queda con `Flag=0` para reintentarse en el siguiente ciclo — no
     se pierde ni se duplica, porque `Flag` solo se marca en 2 tras el commit.

## Estructura

```
src/orden_belen/
├── config.py              # Settings (dataclass); cadenas de conexión desde SQLite
├── config_store.py         # SQLite con las cadenas de conexión
├── connection.py           # parseo/prueba de cadenas ODBC
├── db.py                  # Engines SQLAlchemy con QueuePool (uno por sistema origen)
├── mappers.py              # DO CASE de FoxPro → funciones puras testeables
├── logging_config.py
├── monitor.py              # motor del monitor (contadores, chequeo SQL Server, modo demo)
├── gui.py                  # interfaz gráfica (tkinter)
├── main.py                 # entrypoint: arranca workers, apagado ordenado (SIGINT/SIGTERM)
├── services/
│   ├── resultados_sync.py  # codigo 1.txt
│   └── ordenes_sync.py     # temporizado.txt
└── workers/
    ├── base_worker.py      # hilo con polling adaptativo (equivalente a Timer1)
    ├── resultados_worker.py
    └── ordenes_worker.py
tests/
├── test_mappers.py
├── test_resultados_sync.py
└── test_ordenes_sync.py
```
