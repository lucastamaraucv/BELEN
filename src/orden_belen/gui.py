"""Monitor gráfico "ORDEN BELEN — Monitor LIS" (tkinter, sin dependencias extra).

Panel oscuro tipo terminal de monitoreo: encabezado con reloj, cinco tarjetas de
indicadores, consola de actividad y barra de estado con un indicador de color por
cada conexión SQL Server (MINSA y Resultado). Toda la lógica de sincronización vive
en `workers/` y `services/`; el motor de monitoreo está en `monitor.py` y la GUI solo
lo dibuja, así que nada bloquea la interfaz (los workers corren en hilos).
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# Permite ejecutar este archivo directamente (botón ▶ de VS Code / `python gui.py`),
# además de `python -m orden_belen.gui`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "orden_belen"

from .config_store import ConfigStore
from .connection import (
    autenticacion,
    build_conn_str,
    campos_vacios,
    driver_automatico,
    parse_fields,
    probar_conexion,
    validar_campos,
    vista_segura,
)
from .instancia import tomar_instancia_unica
from .logging_config import DIRECTORIO_LOGS, add_file_logging, configure_logging, instalar_capturas
from .monitor import (
    CONN_DEMO,
    CONN_FAIL,
    CONN_OK,
    CONN_RETRY,
    CONN_UNSET,
    CONNECTIONS,
    LEVEL_INFO,
    Monitor,
    Stats,
)

APP_TITLE = "ORDEN BELEN — Monitor LIS"
AUTO_INICIAR = True  # arranca el monitoreo al abrir la ventana
MAX_LINEAS = 5000  # líneas que conserva la consola (evita consumir memoria)
LOG_DIR = DIRECTORIO_LOGS

# --- paleta ------------------------------------------------------------------
# Tema oscuro por defecto; "claro" es la variante minimalista clara. Cambiar TEMA para alternar.
# ACCION = botón principal (relleno), PELIGRO = detener, SECUNDARIO = fondo de botones con contorno.
TEMA = "oscuro"
PALETAS = {
    "claro": dict(
        BG="#F7F8FA", PANEL="#FFFFFF", BORDER="#E3E6EB", NEON="#15935A", BLUE="#2F6FEB", AMBER="#B7791F",
        AQUA="#0F8F86", RED="#D64545", GRAY="#7A8494", INFO="#2B6CB0", TEXT="#1F2937", DIM="#D9DEE5",
        CONSOLA_BG="#FFFFFF", CONSOLA_FG="#1F2937", SELECCION="#DCEBFF", ACCION="#1F2937",
        PELIGRO="#D64545", SECUNDARIO="#FFFFFF",
    ),
    "oscuro": dict(
        BG="#0A0E14", PANEL="#111722", BORDER="#222C3B", NEON="#34D399", BLUE="#60A5FA", AMBER="#FBBF24",
        AQUA="#2DD4BF", RED="#F87171", GRAY="#8190A5", INFO="#7DD3FC", TEXT="#E6EDF6", DIM="#2A3547",
        CONSOLA_BG="#070A10", CONSOLA_FG="#34D399", SELECCION="#17483A", ACCION="#16A34A",
        PELIGRO="#DC2626", SECUNDARIO="#111722",
    ),
}
_P = PALETAS[TEMA]
BG, PANEL, BORDER, NEON, BLUE, AMBER = (_P[k] for k in ("BG", "PANEL", "BORDER", "NEON", "BLUE", "AMBER"))
AQUA, RED, GRAY, INFO, TEXT, DIM = (_P[k] for k in ("AQUA", "RED", "GRAY", "INFO", "TEXT", "DIM"))
CONSOLA_BG, CONSOLA_FG, SELECCION, ACCION = (_P[k] for k in ("CONSOLA_BG", "CONSOLA_FG", "SELECCION", "ACCION"))
PELIGRO, SECUNDARIO = _P["PELIGRO"], _P["SECUNDARIO"]


def _aclarar(color: str, factor: float = 0.14) -> str:
    """Color un poco más claro (efecto al pasar el mouse sobre un botón relleno)."""
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(min(255, int(c + (255 - c) * factor)) for c in (r, g, b))


def _hover(boton: tk.Button, normal: str, encima: str) -> None:
    """Resalta el botón al pasar el mouse (solo si está habilitado)."""
    boton.bind("<Enter>", lambda _e: boton.cget("state") == "normal" and boton.config(bg=encima))
    boton.bind("<Leave>", lambda _e: boton.cget("state") == "normal" and boton.config(bg=normal))


MONO = "Consolas"
UI = "Segoe UI"

# (clave en Stats, etiqueta, color de la línea superior, color del valor)
TARJETAS = [
    ("ciclos", "Ciclos", BORDER, TEXT),
    ("pendientes", "Pendientes", BORDER, TEXT),
    ("resultados", "Resultados transferidos", BORDER, TEXT),
    ("ordenes", "Órdenes procesadas", BORDER, TEXT),
    ("errores", "Errores", BORDER, TEXT),
]

# estado de conexión -> (color del indicador, texto)
ESTADOS_CONEXION = {
    CONN_OK: (NEON, "OK"),
    CONN_FAIL: (RED, "SIN CONEXIÓN"),
    CONN_RETRY: (AMBER, "RECONECTANDO..."),
    CONN_DEMO: (BLUE, "DEMO"),
    CONN_UNSET: (GRAY, "SIN CONFIGURAR"),
}


class MonitorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(APP_TITLE)
        # Tamaño adaptado a la pantalla: en equipos pequeños la barra inferior (indicadores de
        # conexión y botón DETENER) no debe quedar fuera de la ventana visible.
        ancho = min(1200, root.winfo_screenwidth() - 40)
        alto = min(740, root.winfo_screenheight() - 110)
        root.geometry(f"{ancho}x{alto}+20+10")
        root.minsize(min(950, ancho), min(560, alto))
        root.configure(bg=BG)
        self._set_icon()

        self.stats = Stats()
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.store = ConfigStore()
        self.monitor = Monitor(self.stats, self.events, self.store)
        self._estado = "detenido"  # detenido | ejecutando | deteniendo
        self._cerrando = False
        self._reiniciar = False  # reiniciar el monitor al terminar de detenerse (tras guardar la configuración)

        self._estilo_scrollbar()
        self._build_header()
        self._build_barra_estado()  # antes que las pestañas: así siempre queda visible abajo

        self.pestanas = ttk.Notebook(self.root)
        self.pestanas.pack(side="top", fill="both", expand=True)
        self.tab_monitor = tk.Frame(self.pestanas, bg=BG)
        self.tab_config = tk.Frame(self.pestanas, bg=BG)
        self.pestanas.add(self.tab_monitor, text="Monitor      ")
        self.pestanas.add(self.tab_config, text="Conexiones")
        self._build_tarjetas(self.tab_monitor)
        self._build_cabecera_monitor(self.tab_monitor)
        self._build_consola(self.tab_monitor)
        self._build_config(self.tab_config)
        self._aplicar_estado("detenido")  # necesita el semáforo (pestaña Monitor) y la barra ya creados
        if not (self.store.get("MINSA") or self.store.get("Resultado")):
            self.pestanas.select(self.tab_config)  # primera vez: abre directo donde se pegan las cadenas

        root.protocol("WM_DELETE_WINDOW", self._on_cerrar)
        self._tick_reloj()
        self._drenar_eventos()
        if AUTO_INICIAR:
            root.after(300, self._iniciar)

    # --- construcción ------------------------------------------------------------

    def _set_icon(self) -> None:
        tam = 32
        img = tk.PhotoImage(width=tam, height=tam)
        c, r = (tam - 1) / 2, tam / 2 - 2
        for y in range(tam):
            for x in range(tam):
                if (x - c) ** 2 + (y - c) ** 2 <= r * r:
                    img.put(NEON, to=(x, y))
        self.root.iconphoto(True, img)
        self._icono = img  # evita que el recolector de basura lo libere

    def _estilo_scrollbar(self) -> None:
        estilo = ttk.Style(self.root)
        estilo.theme_use("clam")
        estilo.configure(
            "Monitor.Vertical.TScrollbar",
            background=BORDER,
            troughcolor=CONSOLA_BG,
            bordercolor=CONSOLA_BG,
            arrowcolor=GRAY,
            lightcolor=BORDER,
            darkcolor=BORDER,
        )
        estilo.map("Monitor.Vertical.TScrollbar", background=[("active", "#3A4356")])
        plano = dict(bordercolor=BG, lightcolor=BG, darkcolor=BG)  # sin marcos: solo texto
        estilo.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(20, 0, 0, 0), **plano)
        estilo.configure(
            "TNotebook.Tab", background=BG, foreground=GRAY, padding=(4, 8), borderwidth=0, font=(UI, 11), **plano
        )
        estilo.map(
            "TNotebook.Tab",
            background=[("selected", BG), ("active", BG)],
            foreground=[("selected", BLUE), ("active", TEXT)],
            font=[("selected", (UI, 11, "bold"))],
            bordercolor=[("selected", BG), ("!selected", BG)],
            lightcolor=[("selected", BG), ("!selected", BG)],
            darkcolor=[("selected", BG), ("!selected", BG)],
        )

    def _build_header(self) -> None:
        barra = tk.Frame(self.root, bg=BG, height=64)
        barra.pack(side="top", fill="x")
        barra.pack_propagate(False)

        tk.Label(barra, text="Orden Belén", font=(UI, 17), fg=TEXT, bg=BG).pack(side="left", padx=(20, 0))
        tk.Label(barra, text="Monitor de interoperabilidad LIS", font=(UI, 10), fg=GRAY, bg=BG).pack(
            side="left", padx=(14, 0), pady=(5, 0)
        )

        self.lbl_reloj = tk.Label(barra, text="--:--:--", font=(UI, 14), fg=GRAY, bg=BG)
        self.lbl_reloj.pack(side="right", padx=20)
        # Estado del entorno: PRODUCCIÓN = bases reales; DEMO = datos simulados.
        self.lbl_entorno = tk.Label(barra, text="●  Sin iniciar", font=(UI, 11, "bold"), fg=GRAY, bg=BG)
        self.lbl_entorno.pack(side="right", padx=(0, 18))

    def _build_tarjetas(self, padre: tk.Widget) -> None:
        fila = tk.Frame(padre, bg=BG)
        fila.pack(side="top", fill="x", padx=20, pady=(10, 14))
        self._valores: "dict[str, tk.Label]" = {}
        for i, (clave, etiqueta, color_linea, color_valor) in enumerate(TARJETAS):
            fila.columnconfigure(i, weight=1, uniform="tarjeta")  # cinco tarjetas del mismo ancho
            tarjeta = tk.Frame(fila, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
            tarjeta.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            cuerpo = tk.Frame(tarjeta, bg=PANEL)
            cuerpo.pack(fill="both", expand=True, padx=18, pady=(14, 16))
            tk.Label(cuerpo, text=etiqueta, font=(UI, 9), fg=GRAY, bg=PANEL, anchor="w").pack(fill="x")
            valor = tk.Label(cuerpo, text="0", font=(UI, 28), fg=color_valor, bg=PANEL, anchor="w")
            valor.pack(fill="x")
            self._valores[clave] = valor

    def _build_cabecera_monitor(self, padre: tk.Widget) -> None:
        barra = tk.Frame(padre, bg=BG, height=40)
        barra.pack(side="top", fill="x", padx=20)
        barra.pack_propagate(False)

        self._semaforo: "dict[str, tuple[tk.Canvas, int, str]]" = {}
        for clave, color in (("detenido", RED), ("deteniendo", AMBER), ("ejecutando", NEON)):
            lienzo = tk.Canvas(barra, width=14, height=14, bg=BG, highlightthickness=0)
            lienzo.pack(side="left", padx=(0 if clave == "detenido" else 2, 0))
            ovalo = lienzo.create_oval(2, 2, 12, 12, fill=DIM, outline="")
            self._semaforo[clave] = (lienzo, ovalo, color)
        tk.Label(barra, text="Registro de actividad", font=(UI, 10), fg=GRAY, bg=BG).pack(side="left", padx=10)

        self.btn_limpiar = self._boton_barra(barra, "Limpiar", TEXT, self._on_limpiar)
        self.btn_limpiar.pack(side="right", padx=(0, 8))
        self.btn_exportar = self._boton_barra(barra, "Exportar", BLUE, self._on_exportar)
        self.btn_exportar.pack(side="right", padx=4)

    def _boton_barra(self, padre: tk.Widget, texto: str, color: str, comando) -> tk.Button:
        """Botón secundario: fondo del panel, contorno fino y texto de color; se ilumina al pasar el mouse."""
        boton = tk.Button(
            padre,
            text=texto,
            command=comando,
            font=(UI, 9, "bold"),
            fg=color,
            bg=SECUNDARIO,
            activeforeground=color,
            activebackground=BORDER,
            relief="flat",
            bd=0,
            padx=12,
            pady=3,
            cursor="hand2",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=color,
        )
        _hover(boton, SECUNDARIO, BORDER)
        return boton

    def _build_consola(self, padre: tk.Widget) -> None:
        marco = tk.Frame(padre, bg=CONSOLA_BG, highlightbackground=BORDER, highlightthickness=1)
        marco.pack(side="top", fill="both", expand=True, padx=20, pady=(0, 10))
        self.consola = tk.Text(
            marco,
            bg=CONSOLA_BG,
            fg=CONSOLA_FG,
            insertbackground=CONSOLA_FG,
            selectbackground=SELECCION,
            selectforeground=TEXT,
            font=(MONO, 11),
            spacing1=1,
            spacing3=2,
            relief="flat",
            highlightthickness=0,
            wrap="word",
            padx=14,
            pady=10,
            undo=False,
        )
        scroll = ttk.Scrollbar(marco, orient="vertical", command=self.consola.yview, style="Monitor.Vertical.TScrollbar")
        self.consola.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.consola.pack(side="left", fill="both", expand=True)

        for nivel, color in (("ok", NEON), ("info", INFO), ("warn", AMBER), ("error", RED)):
            self.consola.tag_configure(nivel, foreground=color)

        # Solo lectura, pero seleccionable y copiable (Ctrl+C / Ctrl+A).
        self.consola.bind("<Key>", self._bloquear_edicion)
        self.consola.bind("<<Paste>>", lambda e: "break")
        self.consola.bind("<<Cut>>", lambda e: "break")
        self.consola.bind("<Button-2>", lambda e: "break")
        self.consola.bind("<Control-a>", self._seleccionar_todo)

    def _build_barra_estado(self) -> None:
        barra = tk.Frame(self.root, bg=BG)
        barra.pack(side="bottom", fill="x")
        tk.Frame(self.root, bg=BORDER, height=1).pack(side="bottom", fill="x")
        # Dos filas: así nada se superpone aunque la ventana sea angosta o el nombre del servidor largo.
        fila1 = tk.Frame(barra, bg=BG)
        fila1.pack(fill="x", pady=(6, 2))
        fila2 = tk.Frame(barra, bg=BG)
        fila2.pack(fill="x", pady=(0, 6))

        # Fila 1: un indicador de color por cada conexión SQL Server + botón DETENER / INICIAR.
        self._conexiones: "dict[str, tuple[tk.Canvas, int, tk.Label]]" = {}
        for nombre in CONNECTIONS:
            lienzo = tk.Canvas(fila1, width=14, height=14, bg=BG, highlightthickness=0)
            lienzo.pack(side="left", padx=(20, 6))
            ovalo = lienzo.create_oval(2, 2, 12, 12, fill=GRAY, outline="")
            etiqueta = tk.Label(fila1, text="", font=(UI, 10), fg=GRAY, bg=BG)
            etiqueta.pack(side="left")
            self._conexiones[nombre] = (lienzo, ovalo, etiqueta)

        self.btn_accion = tk.Button(
            fila1, font=(UI, 10, "bold"), fg="white", relief="flat", bd=0, padx=18, pady=5,
            cursor="hand2", command=self._on_accion,
        )
        self.btn_accion.pack(side="right", padx=20)
        # El color de fondo cambia con el estado (Iniciar/Detener): el hover aclara el color actual.
        self.btn_accion.bind("<Enter>", lambda _e: self.btn_accion.cget("state") == "normal"
                             and self.btn_accion.config(bg=self.btn_accion.cget("activebackground")))
        self.btn_accion.bind("<Leave>", lambda _e: self.btn_accion.cget("state") == "normal"
                             and self.btn_accion.config(bg=PELIGRO if self._estado == "ejecutando" else ACCION))

        # Fila 2: último ciclo | pausa | modo (izquierda) y estado del servicio (derecha).
        info = tk.Frame(fila2, bg=BG)
        info.pack(side="left", padx=(20, 0))
        self.lbl_ultimo = self._etiqueta_estado(info, "Último ciclo: --:--:--")
        self._separador(info)
        self.lbl_pausa = self._etiqueta_estado(info, "Pausa: 3s")
        self._separador(info)
        self.lbl_modo = self._etiqueta_estado(info, "Modo: —")
        self.lbl_estado = tk.Label(fila2, text="", font=(UI, 10, "bold"), bg=BG)
        self.lbl_estado.pack(side="right", padx=20)

        self._pintar_conexiones({name: "unknown" for name in CONNECTIONS})

    def _etiqueta_estado(self, padre: tk.Widget, texto: str) -> tk.Label:
        etiqueta = tk.Label(padre, text=texto, font=(UI, 10), fg=GRAY, bg=BG)
        etiqueta.pack(side="left")
        return etiqueta

    def _separador(self, padre: tk.Widget) -> None:
        tk.Frame(padre, bg=BORDER, width=1, height=14).pack(side="left", padx=12)

    # --- consola -----------------------------------------------------------------

    def _bloquear_edicion(self, event: tk.Event) -> "str | None":
        if event.state & 0x4 and event.keysym.lower() in ("c", "a"):  # Ctrl+C / Ctrl+A
            return None
        if event.keysym in ("Left", "Right", "Up", "Down", "Prior", "Next", "Home", "End"):
            return None
        return "break"

    def _seleccionar_todo(self, _event: tk.Event) -> str:
        self.consola.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _agregar_linea(self, nivel: str, texto: str) -> None:
        self.consola.insert("end", texto + "\n", nivel)
        total = int(self.consola.index("end-1c").split(".")[0])
        if total > MAX_LINEAS:
            self.consola.delete("1.0", f"{total - MAX_LINEAS + 1}.0")

    def _log_local(self, texto: str, nivel: str = LEVEL_INFO) -> None:
        self.events.put(("log", nivel, f"[{time.strftime('%H:%M:%S')}] {texto}"))

    # --- eventos de la UI ---------------------------------------------------------

    def _on_accion(self) -> None:
        if self._estado == "ejecutando":
            self._detener()
        elif self._estado == "detenido":
            self._iniciar()

    def _iniciar(self) -> None:
        if self._estado != "detenido":
            return
        if self.monitor.start():
            self._aplicar_estado("ejecutando")
            self.lbl_pausa.config(text=f"Pausa: {self.monitor.interval:g}s")
            demo = self.monitor.mode == "demo"
            self.lbl_modo.config(text="Modo: DEMO" if demo else "Modo: PRODUCCION", fg=BLUE if demo else NEON)
            if demo:
                self.lbl_entorno.config(text="●  Demo (datos simulados)", fg=BLUE)
            else:
                self.lbl_entorno.config(text="●  PRODUCCIÓN", fg=NEON)

    def _detener(self) -> None:
        self._aplicar_estado("deteniendo")
        self._log_local("INFO | Deteniendo: se termina el ciclo activo...")
        self.monitor.stop()

    def _on_cerrar(self) -> None:
        if self._estado == "detenido":
            self.root.destroy()
        elif self._estado == "ejecutando":
            if messagebox.askyesno(
                APP_TITLE, "El monitor se encuentra en ejecución. ¿Desea detenerlo y cerrar la aplicación?"
            ):
                self._cerrando = True
                self._detener()
        else:  # ya se está deteniendo: cerrar cuando termine
            self._cerrando = True

    def _on_exportar(self) -> None:
        ruta = filedialog.asksaveasfilename(
            title="Exportar log",
            defaultextension=".log",
            initialfile=f"orden_belen_{date.today().isoformat()}.log",
            filetypes=[("Archivo de log", "*.log"), ("Texto", "*.txt")],
        )
        if not ruta:
            return
        try:
            Path(ruta).write_text(self.consola.get("1.0", "end-1c") + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"No se pudo guardar el archivo:\n{exc}")
            return
        self._log_local(f"INFO | Log exportado a {ruta}")

    def _on_limpiar(self) -> None:
        if messagebox.askyesno(
            APP_TITLE,
            "¿Limpiar el monitor?\n\nSolo se borra lo que se ve en pantalla; "
            "no se toca ningún archivo ni ningún registro de las bases de datos.",
        ):
            self.consola.delete("1.0", "end")

    # --- pestaña Conexiones SQL Server -------------------------------------------

    def _build_config(self, padre: tk.Widget) -> None:
        cont = tk.Frame(padre, bg=BG)
        cont.pack(fill="both", expand=True, padx=20, pady=10)
        tk.Label(cont, text="Conexiones SQL Server", font=(UI, 14), fg=TEXT, bg=BG).pack(anchor="w")
        tk.Label(
            cont,
            text=(
                "Complete los datos de cada base; la cadena de conexión se arma sola. Puede dejar una vacía "
                f"hasta instalarla.  Se guardan en SQLite: {self.store.path}"
            ),
            font=(UI, 9),
            fg=GRAY,
            bg=BG,
            justify="left",
            wraplength=880,
        ).pack(anchor="w", pady=(2, 8))

        paneles = tk.Frame(cont, bg=BG)
        paneles.pack(fill="x")
        self._cfg: "dict[str, dict]" = {}
        driver = driver_automatico()  # ODBC 17 o 18, según lo instalado: no se elige a mano
        for i, (nombre, titulo, color) in enumerate(
            (
                ("MINSA", "MINSA / Galeno (hospital)", BLUE),
                ("Resultado", "Resultado / Jalisco (laboratorio)", AQUA),
            )
        ):
            paneles.columnconfigure(i, weight=1, uniform="panel")
            self._panel_conexion(paneles, i, nombre, titulo, color, driver)

        pie = tk.Frame(cont, bg=BG)
        pie.pack(fill="x", pady=(10, 0))
        btn_guardar = tk.Button(
            pie, text="Guardar configuración", command=self._guardar_config, font=(UI, 10, "bold"),
            fg="white", bg=ACCION, activebackground=_aclarar(ACCION), activeforeground="white",
            relief="flat", bd=0, padx=18, pady=7, cursor="hand2",
        )
        btn_guardar.pack(side="left")
        _hover(btn_guardar, ACCION, _aclarar(ACCION))
        self.lbl_guardado = tk.Label(pie, text="", font=(MONO, 10), fg=GRAY, bg=BG)
        self.lbl_guardado.pack(side="left", padx=12)

    def _panel_conexion(
        self, padre: tk.Widget, columna: int, nombre: str, titulo: str, color: str, driver: "str | None"
    ) -> None:
        panel = tk.Frame(padre, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        panel.grid(row=0, column=columna, sticky="new", padx=(0 if columna == 0 else 10, 0))
        cuerpo = tk.Frame(panel, bg=PANEL)
        cuerpo.pack(fill="x", padx=16, pady=12)
        cuerpo.columnconfigure(0, weight=1, uniform="campo")
        cuerpo.columnconfigure(1, weight=1, uniform="campo")

        guardados = parse_fields(self.store.get(nombre) or "")
        vars_: "dict[str, tk.Variable]" = {}
        for clave in ("servidor", "base", "usuario", "password"):
            vars_[clave] = tk.StringVar(value=guardados[clave])

        tk.Label(cuerpo, text=titulo, font=(UI, 11, "bold"), fg=TEXT, bg=PANEL).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )

        def campo(fila: int, col: int, etiqueta: str, clave: str, *, show: str = ""):
            tk.Label(cuerpo, text=etiqueta, font=(UI, 9), fg=GRAY, bg=PANEL).grid(
                row=fila, column=col, sticky="w", padx=(0 if col == 0 else 8, 0)
            )
            widget = tk.Entry(
                cuerpo, textvariable=vars_[clave], show=show, font=(MONO, 10), bg=BG, fg=TEXT,
                insertbackground=TEXT, relief="flat", highlightthickness=1,
                highlightbackground=BORDER, highlightcolor=BLUE, disabledbackground=PANEL,
                disabledforeground=GRAY,
            )
            widget.grid(row=fila + 1, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0), pady=(0, 6), ipady=3)
            return widget

        campo(1, 0, "Servidor / instancia (puerto por defecto)", "servidor")
        campo(1, 1, "Base de datos", "base")
        tk.Label(cuerpo, text="Driver ODBC (automático)", font=(UI, 9), fg=GRAY, bg=PANEL).grid(
            row=3, column=0, sticky="w"
        )
        tk.Label(
            cuerpo, text=driver or "¡Ninguno instalado! (ODBC Driver 17 o 18)", font=(MONO, 9),
            fg=INFO if driver else RED, bg=PANEL, anchor="w", wraplength=260, justify="left",
        ).grid(row=4, column=0, sticky="w", pady=(0, 6))
        usuario = campo(5, 0, "Usuario SQL (vacío = Windows)", "usuario")
        clave = campo(5, 1, "Contraseña SQL", "password", show="•")

        mostrar = tk.BooleanVar(value=False)
        tk.Checkbutton(
            cuerpo, text="Mostrar contraseña", variable=mostrar, font=(UI, 9), fg=TEXT, bg=PANEL,
            selectcolor=BG, activebackground=PANEL, activeforeground=TEXT, bd=0, highlightthickness=0,
            command=lambda: clave.config(show="" if mostrar.get() else "•"),
        ).grid(row=7, column=0, sticky="w")
        modo_auth = tk.Label(cuerpo, text="", font=(UI, 9), fg=INFO, bg=PANEL, anchor="w")
        modo_auth.grid(row=7, column=1, sticky="w", padx=(8, 0))

        boton = self._boton_barra(cuerpo, "Probar conexión", BLUE, lambda n=nombre: self._probar(n))
        boton.grid(row=11, column=0, sticky="w", pady=(4, 0))
        estado = tk.Label(
            cuerpo, text="Guardada" if not campos_vacios(guardados) else "Sin configurar", font=(MONO, 9),
            fg=GRAY, bg=PANEL, anchor="w", justify="left", wraplength=260,
        )
        estado.grid(row=11, column=1, sticky="w", padx=(8, 0))
        vista = tk.Label(cuerpo, text="", font=(MONO, 8), fg="#5E6B80", bg=PANEL, anchor="w", justify="left", wraplength=410)
        vista.grid(row=12, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self._cfg[nombre] = {"vars": vars_, "boton": boton, "estado": estado, "vista": vista}

        def refrescar_vista(*_args) -> None:
            campos = self._campos(nombre)
            modo_auth.config(text="Autenticación: " + autenticacion(campos) + " (auto)")
            vista.config(text="Cadena: " + vista_segura(campos) if not campos_vacios(campos) else "")

        for var in vars_.values():
            var.trace_add("write", refrescar_vista)
        refrescar_vista()

    def _campos(self, nombre: str) -> "dict":
        """Valores actuales del formulario de una base (texto recortado)."""
        vars_ = self._cfg[nombre]["vars"]
        campos = {k: v.get() for k, v in vars_.items()}
        for clave in ("servidor", "base", "usuario"):
            campos[clave] = campos[clave].strip()
        return campos  # la contraseña se conserva tal cual (puede llevar espacios)

    def _probar(self, nombre: str) -> None:
        campos = self._campos(nombre)
        error = validar_campos(campos)
        if error:
            self._cfg[nombre]["estado"].config(text=error, fg=AMBER)
            return
        cadena = build_conn_str(campos)
        self._cfg[nombre]["estado"].config(text="Probando...", fg=AMBER)
        self._cfg[nombre]["boton"].config(state="disabled")

        def trabajo() -> None:  # en un hilo: no congela la interfaz
            ok, mensaje = probar_conexion(cadena)
            self.events.put(("probe", nombre, ok, mensaje))

        threading.Thread(target=trabajo, name=f"probar-{nombre}", daemon=True).start()

    def _mostrar_prueba(self, nombre: str, ok: bool, mensaje: str) -> None:
        self._cfg[nombre]["estado"].config(text=("✓ " if ok else "✗ ") + mensaje, fg=NEON if ok else RED)
        self._cfg[nombre]["boton"].config(state="normal")

    def _guardar_config(self) -> None:
        todos = {nombre: self._campos(nombre) for nombre in self._cfg}
        for nombre, campos in todos.items():
            error = None if campos_vacios(campos) else validar_campos(campos)  # vacía = sin configurar
            if error:
                messagebox.showwarning(APP_TITLE, f"{nombre}: {error}")
                return
        try:
            for nombre, campos in todos.items():
                if campos_vacios(campos):
                    self.store.delete(nombre)
                else:
                    self.store.set(nombre, build_conn_str(campos))
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"No se pudo guardar la configuración:\n{exc}")
            return
        for nombre, campos in todos.items():
            self._cfg[nombre]["estado"].config(
                text="Sin configurar" if campos_vacios(campos) else "Guardada", fg=GRAY
            )
        self.lbl_guardado.config(text=f"Guardado {time.strftime('%H:%M:%S')}", fg=NEON)
        self._log_local("INFO | Conexiones SQL Server guardadas en SQLite")

        if self._estado == "ejecutando" and messagebox.askyesno(
            APP_TITLE, "La configuración se guardó.\n\n¿Reiniciar el monitor ahora para aplicarla?"
        ):
            self._reiniciar = True
            self._detener()

    # --- refresco periódico -------------------------------------------------------

    def _tick_reloj(self) -> None:
        self.lbl_reloj.config(text=time.strftime("%H:%M:%S"))
        self.root.after(1000, self._tick_reloj)

    def _drenar_eventos(self) -> None:
        hubo_lineas = False
        al_final = self.consola.yview()[1] >= 0.999  # solo sigue el final si el usuario no subió a leer
        try:
            while True:
                evento = self.events.get_nowait()
                if evento[0] == "log":
                    self._agregar_linea(evento[1], evento[2])
                    hubo_lineas = True
                elif evento[0] == "probe":
                    self._mostrar_prueba(evento[1], evento[2], evento[3])
                elif evento[0] == "stopped":
                    self._aplicar_estado("detenido")
                    if self._cerrando:
                        self.root.destroy()
                        return
                    if self._reiniciar:
                        self._reiniciar = False
                        self._iniciar()
        except queue.Empty:
            pass
        if hubo_lineas and al_final:
            self.consola.see("end")
        self._refrescar_indicadores()
        self.root.after(150, self._drenar_eventos)

    def _refrescar_indicadores(self) -> None:
        datos, conexiones = self.stats.snapshot()
        for clave, etiqueta in self._valores.items():
            texto = str(datos[clave])
            if etiqueta.cget("text") != texto:
                etiqueta.config(text=texto)
            if clave == "errores":
                etiqueta.config(fg=RED if datos[clave] else TEXT)
        self.lbl_ultimo.config(text=f"Último ciclo: {datos['ultimo_ciclo']}")
        self._pintar_conexiones(conexiones)

    def _pintar_conexiones(self, conexiones: "dict[str, str]") -> None:
        for nombre, (lienzo, ovalo, etiqueta) in self._conexiones.items():
            color, estado = ESTADOS_CONEXION.get(conexiones.get(nombre, ""), (GRAY, "—"))
            lienzo.itemconfig(ovalo, fill=color)
            etiqueta.config(text=f"{self.monitor.labels.get(nombre, nombre)}  {estado.capitalize()}", fg=color)

    def _aplicar_estado(self, estado: str) -> None:
        self._estado = estado
        for clave, (lienzo, ovalo, color) in self._semaforo.items():
            lienzo.itemconfig(ovalo, fill=color if clave == estado else DIM)
        if estado == "ejecutando":
            self.btn_accion.config(text="Detener", bg=PELIGRO, activebackground=_aclarar(PELIGRO), state="normal")
            self.lbl_estado.config(text="En ejecución", fg=NEON)
        elif estado == "deteniendo":
            self.btn_accion.config(text="Deteniendo...", bg=DIM, activebackground=DIM, state="disabled")
            self.lbl_estado.config(text="Deteniendo", fg=AMBER)
        else:
            self.btn_accion.config(text="Iniciar", bg=ACCION, activebackground=_aclarar(ACCION), state="normal")
            self.lbl_estado.config(text="Detenido", fg=GRAY)


def main() -> None:
    configure_logging("INFO")
    add_file_logging(LOG_DIR)
    instalar_capturas(LOG_DIR)
    root = tk.Tk()
    if not tomar_instancia_unica():
        # Dos copias escribirían a la vez en dlhnse2.dbf. Salida normal (código 0): el lanzador no la reabre.
        logging.getLogger(__name__).warning("Ya hay otra ventana de ORDEN BELEN abierta en esta PC; esta se cierra")
        root.withdraw()
        messagebox.showwarning(APP_TITLE, "ORDEN BELEN ya está abierto en esta PC.\n\nNo se abre una segunda copia.")
        root.destroy()
        return

    def _error_en_ventana(exc_type, exc_value, exc_tb) -> None:
        # Por defecto tkinter lo imprime en la consola (que no existe con pythonw): se deja en el log.
        logging.getLogger(__name__).error("Error en la ventana", exc_info=(exc_type, exc_value, exc_tb))

    root.report_callback_exception = _error_en_ventana
    MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
