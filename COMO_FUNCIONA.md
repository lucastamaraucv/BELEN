# ORDEN BELEN (Python) — cómo funciona la versión oficial

Réplica en Python del interfaz Fox "ACTUALIZACION ORDENES GALENO", con la misma lógica de lectura e inserción
y más robustez ante caídas de SQL Server. Este documento describe el comportamiento real del código.

Fuente Fox de referencia: `C:\BACKUP\ORDEN BELEN CODIGO FOX\` (`CODIGO FUENTE\codigo 1.txt`,
`CODIGO FUENTE\temporizado.txt`, `FORM\formprincipal.SCT`, `PRG\principal.prg`).

---

## 1. Qué hace (dos procesos independientes)

| Proceso | Equivale a | Frecuencia |
|---|---|---|
| **ordenes-sync** | `temporizado.txt` (evento Timer1 de `formprincipal.SCT`) | cada 0,1 s sin trabajo; 1 s después de procesar |
| **resultados-sync** | `codigo 1.txt` | cada 15 s |

Cada uno corre en su propio hilo, con su propia sesión SQL por ciclo.

### 1.1 Órdenes: Galeno (SQL Server) → `dlhnse2.dbf` (Datalab)

1. Lee hasta 100 órdenes pendientes:
   `SELECT TOP 100 a.* FROM LabOrdenesInterop2 a WHERE a.Flag = 0` (servidor Galeno, base SIGH).
2. Por cada orden:
   1. **Reclama** la orden en SQL (sin confirmar): `UPDATE LabOrdenesInterop2 SET Flag = 2 WHERE IdMovimiento = ? AND Codigo = ? AND Flag = 0`.
      Si no afecta filas, otro proceso ya la tomó y se omite.
   2. Transforma los campos (sección 3).
   3. Busca el `Codigo` en `perfiles.dbf`. Si existe, **una fila por cada `cod_exam`** del perfil (en el orden del archivo,
      ignorando registros borrados); si no, una sola fila con el código tal cual.
   4. **Escribe las filas en `C:\ORDENES\ORDEN\dlhnse2.dbf`** (todas las de la orden de una sola vez).
   5. **Confirma** (commit) el `Flag = 2`.
3. Si cualquier paso falla antes del commit, el `Flag = 2` se revierte y la orden sigue pendiente (`Flag = 0`).

`vordenobi` y `vperfiles` del Fox **no son tablas SQL**: son los archivos locales `dlhnse2.dbf` y `perfiles.dbf`
(`use &rutan/dlhnse2 alias vordenobi`, `rutan = SYS(5)+"\ORDENES\ORDEN\"`).

### 1.2 Resultados: Jalisco → InterLabDetalle

> **En BELÉN está APAGADO por defecto.** El Fox de BELÉN (`FORM\formprincipal.SCT`) tiene comentado el envío de resultados y la tabla `InterLabDetalle` no existe en SIGH (es de las versiones Nobilis/INSS); en BELÉN los resultados los envía otro programa. Se activa solo con la variable de entorno `ORDEN_PROCESAR_RESULTADOS=1`.

1. Ejecuta `EXEC sp_Minsa_EnviarResultadoMinsa` en Jalisco (resultados validados y aún no transferidos).
2. Por cada fila:
   - `UPDATE InterLabDetalle SET dni, FechaHoraResultado, itemResultado, item, itemReferencia, idEstado = '5' WHERE identificador = ? AND cptcodigo = ?`
   - Luego `EXEC sp_MINSA_UpdateResultadosValidacion identificador, id_determinacion` en Jalisco (marca como transferido).
3. `FechaHoraResultado` se interpreta con el mismo `SUBSTR` posicional del Fox: **los segundos siempre quedan en 0**.

---

## 2. Bases de datos y archivos

| Qué | Dónde |
|---|---|
| **Galeno** (conexión `MINSA`) | SQL Server `192.168.2.12`, base `SIGH`. Tabla `LabOrdenesInterop2` (y `InterLabDetalle`). |
| **Jalisco** (conexión `Resultado`) | SQL Server `WIN-O9B57ROO2HN\SQLEXPRESS`, base `Jalisco`. Los dos stored procedures. |
| **Tablas locales** | `C:\ORDENES\ORDEN\dlhnse2.dbf` (destino), `C:\ORDENES\ORDEN\perfiles.dbf` (perfiles). |

- Las conexiones usan **puerto por defecto** y **no usan DSN de ODBC**: se arman con servidor, base, usuario y contraseña
  (driver ODBC 17 o 18, detectado solo). Se guardan en `config\orden_belen.db` (SQLite local, contraseñas en texto plano:
  **no copiar ese archivo entre PCs**). Se editan en la pestaña *Conexiones* de la GUI.
- La carpeta de los DBF es `C:\ORDENES\ORDEN`; se cambia solo con la variable de entorno `ORDEN_DBF_DIR`.

---

## 3. Qué se escribe en `dlhnse2.dbf` (réplica de los REPLACE de Fox)

Estructura: dBase III, 50 campos, registro de 288 bytes, **página de códigos DOS 437**. Python lee la estructura del propio
encabezado y completa solo estos 15 campos; el resto queda como `APPEND BLANK` (texto en espacios, numéricos en 0, fechas en blanco).

| Campo | Valor |
|---|---|
| `DL_NUMERO` | `IdMovimiento` (entero, sin decimales) |
| `DL_FECEXA`, `DL_FECREG` | fecha de la orden (`fecha`) |
| `DL_CEXAME` | `cod_exam` del perfil, o el `Codigo` si no es perfil |
| `DL_PERFIL` | `Codigo` de la orden |
| `DL_CODLAB` | `BIO` (fijo) |
| `DL_HISTOR` | `NroHistoriaClinica` como `STR(,10)` sin espacios |
| `DL_NOMBRE` | `paciente` sin espacios en los bordes y en mayúsculas |
| `DL_SEXO` | `IdTipoSexo`: 1→`M`, 2→`F`, 3→`I`, otro→`I` |
| `DL_FECNAC` | `fechanacimiento` (si es nula, la fecha de hoy) |
| `DL_TIPDOC` | `IdDocIdentidad`: 1→`L`, 2→`C`, 3→`PA`, 7→`U`, 8→`MDN`, 9→`MAU`, otro→`SN` (**el campo es C(1): se guarda solo la primera letra**) |
| `DL_CPRO` | `IdFuenteFinanciamiento` + `IdtipoServicio` (cada uno con `STR(,1)`); `99` si el tipo de servicio es nulo |
| `DL_CSER` | `Idservicio` con `STR(,3)` **sin ceros** (7 → `7`); `007` si es nulo |
| `DL_NDOCUM` | `NroDocumento` sin alterar; un espacio si es nulo |
| `DL_HORA` | hora del sistema `HH:MM` al procesar (el `TIME()` de VFP) |

Particularidades de VFP que se replican a propósito:
- `REPLACE` **trunca** al ancho del campo.
- Texto en **DOS 437**: la `Ñ` se guarda como `0xA5`; las tildes mayúsculas que 437 no tiene (`Á Í Ó Ú`) se guardan sin tilde.
- `STR(x, n)` redondea y devuelve `*` si no cabe.
- `SET DELETE ON`: los registros borrados de `perfiles.dbf` no se leen.
- Un documento nulo **no** fuerza `SN` (en el Fox ese `IF` era código muerto).
- Los nombres de columna se leen sin distinguir mayúsculas, como un cursor de VFP.

---

## 4. Qué mejora respecto al Fox (robustez)

- **Errores de red vs. configuración vs. datos.** Una caída/timeout de SQL Server (SQLSTATE 08xxx, timeouts, deadlock, pool
  agotado, "SHUTDOWN en curso") o un error de **configuración** (tabla/columna/procedimiento inexistente, permiso, login —
  p. ej. `InterLabDetalle no es válido`) **aborta el ciclo** en la primera fila, hace rollback y activa **backoff exponencial**
  (1 s → 2 → 4 … hasta 60 s; se reinicia al primer ciclo correcto). Un error de datos de una sola fila se registra y se continúa
  con la siguiente; si **todas** las filas del ciclo fallan, también se aplica backoff (antes órdenes reintentaba cada 0,1 s).
- **El mismo error no se repite en el log:** se escribe completo una vez cada 10 min; las repeticiones solo se cuentan
  ("se repitió N veces…"). Al volver a funcionar se escribe "recuperado tras N ciclo(s) con error".
- **Timeouts:** conexión 5 s y consulta 60 s (evita hilos colgados con la red caída). `pool_pre_ping` descarta conexiones muertas.
- **Sin duplicados entre instancias:** el `UPDATE ... AND Flag = 0` hace que la segunda instancia no procese la misma orden.
- **Atómico en SQL:** si falla la escritura del DBF, la orden vuelve a `Flag = 0`.
- **Escritura DBF segura:** las filas se validan antes de tocar el archivo; se escriben juntas con la marca de fin (`0x1A`) y el
  contador del encabezado. Un campo desconocido o un archivo truncado/bloqueado aborta sin dejar el archivo a medias.
- **SPs:** `SET NOCOUNT ON` (evita el error `Previous SQL was not a query` de pyodbc) y commit aunque no devuelvan filas.
- **Apagado ordenado:** Ctrl+C detiene los workers y libera los pools (si un worker sigue vivo, no se liberan).
- **Caídas del SQL Server de Galeno (varias al día):** "SHUTDOWN en curso" (6005) se trata como desconexión: se descartan
  todas las conexiones viejas del pool y se reconecta limpio (sin el pool interno de pyodbc debajo). Con el servidor por IP se
  fuerza TCP (`tcp:192.168.2.12`, mismo puerto): cada intento fallido tarda 5 s en vez de hasta 26 s por Named Pipes.
  Si el servidor cae entre la escritura del DBF y el commit, la orden se reprocesa y puede quedar repetida en el DBF
  (igual que el Fox; Nobilis/Datalab no inserta los duplicados).
- **24x7:** una sola instancia por PC (dos escribirían a la vez en `dlhnse2.dbf`). El vigilante (hilo propio) reinicia un
  worker que muera; si uno **no avanza** (ni una fila) en 5 min lo avisa y en **10 min reinicia el proceso completo**
  (un commit/rollback colgado en el driver no se puede cortar de otra forma); lo mismo si la memoria pasa de 1400 MB en Python
  de 32 bits o si hay `MemoryError`. Al detener, se espera a que terminen **todos** los hilos antes de liberar las conexiones.
  El reinicio automático solo ocurre si se abrió con `iniciar_orden_belen.bat` (ver 5.1); deja en `logs\fallos_python.log`
  dónde estaba cada hilo. Un fallo del log (disco lleno, permisos) nunca detiene el programa.

---

## 5. Cómo ejecutarlo

Requisitos: Python 3.9+, `SQLAlchemy`, `pyodbc` y **ODBC Driver 17 o 18 for SQL Server** (del mismo bits que Python).

```cmd
python -m pip install --no-deps SQLAlchemy
python -m pip install typing_extensions pyodbc
```
(`greenlet` no se necesita; en Python 3.9 de 32 bits no se puede compilar.)

| Modo | Comando / acción |
|---|---|
| Sin ventana | `python -m orden_belen.main` o ▶ sobre `src\orden_belen\main.py` (Ctrl+C para detener) |
| Con ventana (GUI) | `python -m orden_belen.gui` o ▶ sobre `src\orden_belen\gui.py` |
| Solo diagnóstico | `python -m orden_belen.diagnostico` (código de salida 0 = todo OK) |
| Tests | `python -m pytest -q` (necesita `pytest`; no toca bases ni el DBF real) |

Variables de entorno opcionales (prefijo `ORDEN_`): `DBF_DIR`, `QUERY_TIMEOUT_SECONDS`, `CONNECT_TIMEOUT_SECONDS`,
`ORDENES_BATCH_SIZE`, `RESULTADOS_POLL_INTERVAL_SECONDS`, `ORDENES_POLL_INTERVAL_IDLE_SECONDS`,
`ORDENES_POLL_INTERVAL_BUSY_SECONDS`, `BACKOFF_INITIAL_SECONDS`, `BACKOFF_MAX_SECONDS`, `BACKOFF_FACTOR`, `POOL_SIZE`, `LOG_LEVEL`.

### 5.1 Funcionamiento 24x7 (recomendado en la PC servidor)

**Opción A — un solo archivo `OrdenBelen.exe` (no necesita Python ni nada más instalado):** copiar
`dist\OrdenBelen.exe` a la PC (p. ej. `C:\OrdenBelen\`) y abrirlo. Lleva dentro Python 3.9 de 32 bits, SQLAlchemy, pyodbc y
tkinter (funciona en Windows de 32 y 64 bits). En la PC solo hace falta el **ODBC Driver 17 for SQL Server** y la carpeta de
Datalab `C:\ORDENES\ORDEN\`.
- **Conexiones, la primera vez:** si junto al `.exe` no hay `config\orden_belen.db`, copia las de la instalación anterior de esa
  PC (`Escritorio\PYTHON\config\orden_belen.db`, p. ej. `C:\Users\Administrador\Desktop\PYTHON`) y, si no hay, las que vienen
  **dentro del `.exe`** (las de `PYTHON\config` al compilar). Arranca directo en **PRODUCCIÓN**. Queda anotado en
  `logs\reinicios.log`. Después manda lo guardado junto al `.exe` (pestaña *Conexiones*). **El `.exe` lleva las contraseñas:
  no compartirlo fuera del hospital.**
- `config\` y `logs\` se crean junto al `.exe`. Al arrancar se descomprime en `%LOCALAPPDATA%\OrdenBelen\_MEIxxxx` (no en
  %TEMP%, que los limpiadores vacían) y lo borra al cerrar; las carpetas que deja un cierre forzado o un corte de luz se borran
  solas en el siguiente arranque.
- Volver a compilarlo: `compilar_exe.bat` (ver `OrdenBelen.spec`).

**Opción B — código fuente:** doble clic en **`iniciar_orden_belen.bat`** (no `python` desde CMD).

En los dos casos arranca `orden_belen.lanzador`, sin consola negra (un clic en una consola de CMD activa "QuickEdit" y
**congela** el programa sin error). El lanzador abre la ventana como proceso hijo y:
- si la app se cierra por un fallo, la matan o ella misma se reinicia (bloqueo de 10 min o memoria alta, código 3), la vuelve
  a abrir a los 15 s y lo anota en `logs\reinicios.log` (máx. 1 MB);
- si se cierra normal (Detener + X) o ya había otra copia abierta, termina también.
El `.bat` usa primero `%LOCALAPPDATA%\Programs\Python\Python39-32\pythonw.exe` (donde se instalaron SQLAlchemy y pyodbc).

Para que arranque sola al iniciar sesión en Windows (CMD como administrador, ajustando la ruta):
```cmd
schtasks /create /tn "ORDEN BELEN" /tr "\"C:\OrdenBelen\OrdenBelen.exe\"" /sc onlogon /rl highest
```
(o con `...\PYTHON\iniciar_orden_belen.bat` en la opción B). Es una ventana: necesita una sesión de Windows iniciada. Tras un
reinicio de Windows Update solo vuelve a abrirse al iniciar sesión (o configurar el inicio de sesión automático de Windows).

### 5.2 Logs (`PYTHON\logs\` o `logs\` junto a `OrdenBelen.exe`)

| Archivo | Qué tiene |
|---|---|
| `orden_belen_AAAA-MM-DD.log` (y `_2`, `_3`…) | actividad y errores; una parte nueva cada 10 MB |
| `fallos_python.log` | volcado de Python si se cae por un error interno (p. ej. dentro del driver ODBC) |
| `reinicios.log` | lo que hizo el lanzador (`OrdenBelen.exe` o el `.bat`): cada reapertura y de dónde copió las conexiones la primera vez |

- **Tope automático:** se borran solos los logs de más de **15 días** y, empezando por los más viejos, los que no quepan en
  **100 MB** en total (un archivo gigante se borra sin arrastrar a los pequeños). Ajustable con `ORDEN_LOG_MAX_MB_ARCHIVO`,
  `ORDEN_LOG_MAX_MB_TOTAL`, `ORDEN_LOG_DIAS_RETENCION`; `ORDEN_LOG_DIR` cambia la carpeta.
- **LATIDO** cada 10 min: conexiones, pendientes, contadores, estado de cada hilo y memoria. Si el log se corta sin
  "Proceso terminado …", el proceso fue cerrado desde fuera (apagado, Administrador de tareas, cierre de sesión) o se cayó.
- Las líneas de cada ciclo (cada 3 s) solo se ven en pantalla; el botón *Exportar log* guarda lo que se ve.

### Diagnóstico al arrancar
`main` imprime este cuadro y lo registra en el log antes de iniciar los workers (solo lectura, no impide arrancar):

```
 MODO: PRODUCCION (bases reales)
 MINSA (192.168.2.12/SIGH)   [ OK ] Conectividad · [ OK ] LabOrdenesInterop2 · [ OK ] InterLabDetalle
 Resultado (...\SQLEXPRESS/Jalisco)   [ OK ] Conectividad · [ OK ] los dos stored procedures
 Archivos DBF   [ OK ] Carpeta · [ OK ] dlhnse2.dbf (escribible) · [ OK ] perfiles.dbf
```
`[FALLA]` indica qué falta: red, usuario/permiso, objeto inexistente, DBF bloqueado, ausente o sin los campos.

### GUI
Tema oscuro por defecto (`TEMA = "oscuro"` en `gui.py`; `"claro"` para la variante minimalista). Muestra: insignia
**● PRODUCCIÓN** (bases reales) o **Demo** (sin conexiones configuradas; datos simulados, no toca nada), tarjetas
(ciclos, pendientes, resultados transferidos, órdenes procesadas, errores), registro de actividad con *Exportar* y *Limpiar*,
y una barra inferior con el estado de cada conexión y el botón **Iniciar / Detener**. Lo importante también va al log de
archivo (ver 5.2).

---

## 6. Puesta en producción (checklist)

1. **Detener Fox** (Python y Fox no deben escribir a la vez en `dlhnse2.dbf` ni procesar las mismas órdenes).
2. **Copia de seguridad de `C:\ORDENES\ORDEN\dlhnse2.dbf`** (~194 MB; también lo lee Datalab).
3. Configurar las dos conexiones en la GUI y pulsar *Probar conexión*.
4. Arrancar y revisar el cuadro de diagnóstico: todo `[ OK ]`.
5. Probar con una orden: dejarla en `Flag = 0` y verificar `Flag = 2` y las filas nuevas en `dlhnse2.dbf`.

## 7. Si una orden no se consume
1. ¿`Flag = 0` y `Codigo` con valor? (`SELECT * FROM LabOrdenesInterop2 WHERE IdMovimiento = ...`)
2. ¿El cuadro de diagnóstico tiene `[FALLA]`? (conexión, permisos, DBF bloqueado o ausente)
3. Buscar la orden o `Error` en `logs\orden_belen_AAAA-MM-DD.log`.
4. Reprocesar una orden (volver a `Flag = 0`) **duplica** sus filas en el DBF, igual que el Fox.

## 8. Límites conocidos
- El usuario SQL de Galeno debe poder hacer `UPDATE` sobre `LabOrdenesInterop2` (y sobre `InterLabDetalle` para resultados).
- La escritura del DBF no usa el protocolo de bloqueo de VFP; solo reintenta al abrir. Con Datalab leyendo a la vez, un lector
  podría ver un instante intermedio.
- `dl_hora` usa la hora del sistema al procesar; confirmar contra una fila insertada por el Fox.
- Los segundos de `FechaHoraResultado` quedan en 0 por fidelidad al original.
- Si el `UPDATE` a `InterLabDetalle` falla, Python **no** marca el resultado como transferido (el Fox sí lo marcaba).

## 9. Mapa del código (`src\orden_belen\`)

| Archivo | Función |
|---|---|
| `main.py` | arranque sin ventana, diagnóstico, apagado ordenado |
| `gui.py`, `monitor.py` | ventana y motor del monitor (modo producción/demo) |
| `services/ordenes_sync.py` | lógica de `temporizado.txt` (SQL + DBF) |
| `services/resultados_sync.py` | lógica de `codigo 1.txt` |
| `dbf_io.py` | lectura/escritura de DBF (formato VFP, DOS 437, `APPEND BLANK`, `SET DELETE ON`) |
| `mappers.py` | reglas de transformación de VFP (`STR`, `DO CASE`, nulos) |
| `workers/` | hilos con intervalo adaptativo y backoff |
| `errores.py` | clasifica errores (red / configuración / datos) y evita repetirlos en el log |
| `logging_config.py` | log de archivo con tope y borrado automático, captura de caídas, memoria |
| `instancia.py` | una sola instancia por PC |
| `reinicio.py` | reinicia el proceso (lo reabre el lanzador) si algo queda colgado o sin memoria |
| `lanzador.py` | punto de entrada del .exe y del .bat: abre la app y la reabre si se cae |
| `rutas.py` | carpeta de `config\` y `logs\` (PYTHON o junto al .exe) |
| `diagnostico.py` | test de conectividad, objetos, permisos y DBF |
| `connection.py`, `config.py`, `config_store.py`, `db.py` | cadenas ODBC, ajustes y pools |
