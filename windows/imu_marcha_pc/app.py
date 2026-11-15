"""Interfaz de IMU Marcha para Windows (PySide6 + pyqtgraph)."""
import json
import os
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from . import __version__
from . import analisis as A
from . import protocolo as P
from . import reloj as R
from . import registro as RG
from .registro import Receptor, Registro, carpeta_sesion, nombre_sesion, pedir

CARPETA_POR_DEFECTO = r"F:\Practica_I\Etapa2_datos"
CONDICIONES = ["normal", "lenta", "rapida", "caminata_1min", "prueba"]
AZUL = "#1F5FA8"
NARANJA = "#D65C28"
pg.setConfigOptions(antialias=True, background="w", foreground="#333")


def configuracion():
    return QtCore.QSettings("UNAB", "IMU Marcha")


def ruta_modelo():
    candidatos = []
    if getattr(sys, "_MEIPASS", None):
        candidatos.append(Path(sys._MEIPASS) / "pose_landmarker_full.task")
    candidatos += [Path(sys.argv[0]).resolve().parent / "pose_landmarker_full.task",
                   Path(__file__).resolve().parent.parent / "pose_landmarker_full.task"]
    guardado = configuracion().value("modelo", "")
    if guardado:
        candidatos.insert(0, Path(guardado))
    for c in candidatos:
        if c.is_file():
            return c
    return None


class Trabajo(QtCore.QThread):
    """Ejecuta una función larga fuera de la interfaz."""
    progreso = QtCore.Signal(int, int, str)
    listo = QtCore.Signal(object)
    fallo = QtCore.Signal(str)

    def __init__(self, funcion):
        super().__init__()
        self.funcion = funcion
        self.cancelado = False

    def run(self):
        try:
            self.listo.emit(self.funcion(self))
        except Exception:
            self.fallo.emit(traceback.format_exc(limit=3))


# ====================================================================== 1. Registro
class PestanaRegistro(QtWidgets.QWidget):
    def __init__(self, receptor: Receptor):
        super().__init__()
        self.rx = receptor
        self.registro = None
        self.inicio = 0.0
        self.simulador = None
        self.trabajo = None
        cfg = configuracion()

        izq = QtWidgets.QVBoxLayout()
        caja = QtWidgets.QGroupBox("Equipos que están enviando datos (puerto 5005)")
        v = QtWidgets.QVBoxLayout(caja)
        self.tabla = QtWidgets.QTableWidget(0, 8)
        self.tabla.setHorizontalHeaderLabels(["Usar", "Equipo", "IP", "Paquetes / perdidos", "Batería",
                                              "Sincronía", "Listo", "Estado"])
        self.tabla.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.tabla.horizontalHeader().setStretchLastSection(True)
        self.tabla.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tabla.verticalHeader().setVisible(False)
        v.addWidget(self.tabla)
        fila = QtWidgets.QHBoxLayout()
        self.btn_sim = QtWidgets.QPushButton("Iniciar simulador (prueba sin equipo)")
        self.btn_sim.clicked.connect(self.alternar_simulador)
        fila.addWidget(self.btn_sim)
        fila.addStretch()
        v.addLayout(fila)
        self.lbl_sinc = QtWidgets.QLabel("Esperando equipos…")
        self.lbl_sinc.setWordWrap(True)
        v.addWidget(self.lbl_sinc)
        izq.addWidget(caja)

        datos = QtWidgets.QGroupBox("Sesión")
        f = QtWidgets.QFormLayout(datos)
        self.ed_base = QtWidgets.QLineEdit(cfg.value("carpeta", CARPETA_POR_DEFECTO))
        b = QtWidgets.QPushButton("Cambiar…")
        b.clicked.connect(self.elegir_carpeta)
        h = QtWidgets.QHBoxLayout(); h.addWidget(self.ed_base); h.addWidget(b)
        f.addRow("Carpeta de datos:", h)
        self.ed_part = QtWidgets.QLineEdit(cfg.value("participante", "P01"))
        self.ed_part.setToolTip("Código del participante; nunca el nombre de la persona")
        f.addRow("Participante:", self.ed_part)
        self.cb_cond = QtWidgets.QComboBox(); self.cb_cond.addItems(CONDICIONES)
        f.addRow("Condición:", self.cb_cond)
        self.sp_rep = QtWidgets.QSpinBox(); self.sp_rep.setRange(1, 20)
        f.addRow("Repetición:", self.sp_rep)
        self.ed_notas = QtWidgets.QLineEdit()
        f.addRow("Notas:", self.ed_notas)
        self.lbl_sesion = QtWidgets.QLabel()
        f.addRow("Se guardará en:", self.lbl_sesion)
        for w in (self.ed_base, self.ed_part):
            w.textChanged.connect(self.actualizar_nombre)
        self.cb_cond.currentIndexChanged.connect(self.actualizar_nombre)
        self.sp_rep.valueChanged.connect(self.actualizar_nombre)
        izq.addWidget(datos)

        self.btn = QtWidgets.QPushButton("▶  Iniciar registro")
        self.btn.setMinimumHeight(54)
        fuente = self.btn.font(); fuente.setPointSize(13); fuente.setBold(True); self.btn.setFont(fuente)
        self.btn.clicked.connect(self.alternar)
        izq.addWidget(self.btn)
        self.lbl_tiempo = QtWidgets.QLabel("Listo.")
        izq.addWidget(self.lbl_tiempo)
        self.lbl_alerta = QtWidgets.QLabel()
        self.lbl_alerta.setWordWrap(True)
        self.lbl_alerta.setStyleSheet("background:#B00;color:white;padding:6px;font-weight:bold;")
        self.lbl_alerta.hide()
        izq.addWidget(self.lbl_alerta)
        fila_p = QtWidgets.QHBoxLayout()
        self.lbl_pend = QtWidgets.QLabel()
        self.btn_recuperar = QtWidgets.QPushButton("Recuperar respaldos…")
        self.btn_recuperar.clicked.connect(self.dialogo_respaldos)
        fila_p.addWidget(self.lbl_pend, 1); fila_p.addWidget(self.btn_recuperar)
        izq.addLayout(fila_p)
        self.log = QtWidgets.QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(500)
        izq.addWidget(self.log, 1)

        der = QtWidgets.QVBoxLayout()
        self.lbl_graf = QtWidgets.QLabel("Selecciona un equipo para ver su señal")
        der.addWidget(self.lbl_graf)
        self.g_senal = pg.PlotWidget(); self.g_senal.setLabel("bottom", "tiempo (s)"); self.g_senal.setLabel("left", "aceleración vertical (g)")
        self.c_senal = self.g_senal.plot(pen=pg.mkPen(AZUL, width=1.5))
        self.g_esp = pg.PlotWidget(); self.g_esp.setLabel("bottom", "frecuencia (Hz)"); self.g_esp.setLabel("left", "potencia")
        self.g_esp.setXRange(0, 4)
        self.c_esp = self.g_esp.plot(pen=pg.mkPen(AZUL, width=1.5))
        self.l_fd = pg.InfiniteLine(angle=90, pen=pg.mkPen(NARANJA, width=1.5))
        self.g_esp.addItem(self.l_fd)
        self.lbl_fd = QtWidgets.QLabel(); f2 = self.lbl_fd.font(); f2.setPointSize(12); self.lbl_fd.setFont(f2)
        der.addWidget(self.g_senal, 1); der.addWidget(self.g_esp, 1); der.addWidget(self.lbl_fd)

        raiz = QtWidgets.QHBoxLayout(self)
        wi = QtWidgets.QWidget(); wi.setLayout(izq); wi.setMaximumWidth(560)
        raiz.addWidget(wi); raiz.addLayout(der, 1)

        self.usar = {}
        self.alertas_vistas = set()
        self.trabajo_pend = None
        self.actualizar_nombre()
        self.timer = QtCore.QTimer(self); self.timer.timeout.connect(self.refrescar); self.timer.start(500)
        self.timer_pend = QtCore.QTimer(self); self.timer_pend.timeout.connect(self.revisar_pendientes)
        self.timer_pend.start(5000)
        QtCore.QTimer.singleShot(800, self.revisar_sin_terminar)
        self.actualizar_pendientes()

    def escribir(self, txt):
        self.log.appendPlainText(time.strftime("%H:%M:%S  ") + txt)

    def elegir_carpeta(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Carpeta de datos", self.ed_base.text())
        if d:
            self.ed_base.setText(d)

    def actualizar_nombre(self):
        s = nombre_sesion(self.ed_part.text(), self.cb_cond.currentText(), self.sp_rep.value())
        ruta = carpeta_sesion(self.ed_base.text(), self.ed_part.text(), s)
        self.lbl_sesion.setText(str(ruta))
        self.lbl_sesion.setStyleSheet("color:#B00;" if ruta.exists() else "")
        self.lbl_sesion.setToolTip("Ya existe: sube la repetición para no sobrescribir" if ruta.exists() else "")

    def alternar_simulador(self):
        from .simulador import SimuladorEquipo
        if self.simulador:
            self.simulador.detener(); self.simulador = None
            self.btn_sim.setText("Iniciar simulador (prueba sin equipo)")
            self.escribir("Simulador detenido.")
        else:
            try:
                self.simulador = SimuladorEquipo(frecuencia=1.9, perdida=0.005).iniciar()
                self.btn_sim.setText("Detener simulador")
                self.escribir("Simulador iniciado: marcha de 1,90 Hz con tres saltos al inicio y 0,5 % de pérdidas.")
            except OSError as e:
                self.escribir(f"No se pudo iniciar el simulador: {e}")

    def equipos_elegidos(self):
        return [eq for eq in self.rx.lista() if self.usar.get(eq.ip, True) and eq.activo()]

    def refrescar(self):
        if self.rx.error:
            self.lbl_fd.setText(self.rx.error)
        lista = self.rx.lista()
        if self.tabla.rowCount() != len(lista):
            self.tabla.setRowCount(len(lista))
        for i, eq in enumerate(lista):
            chk = self.tabla.cellWidget(i, 0)
            if chk is None or chk.property("ip") != eq.ip:
                chk = QtWidgets.QCheckBox(); chk.setChecked(self.usar.get(eq.ip, True)); chk.setProperty("ip", eq.ip)
                chk.toggled.connect(lambda v, ip=eq.ip: self.usar.__setitem__(ip, v))
                self.tabla.setCellWidget(i, 0, chk)
            total = eq.recibidos + eq.perdidos
            pct = 100 * eq.perdidos / total if total else 0
            bat = eq.estado.get("bateria", -1) if eq.estado else -1
            if eq.grabando:
                est = f"● GRABANDO ({eq.rec_recibidos} paq.)"
            elif not eq.activo():
                est = "sin datos"
            else:
                est = "control remoto OK" if eq.control else ("sin control (solo recepción)" if eq.control is False else "…")
            sinc = eq.sincronia() if eq.activo() else None
            txt_sinc, nivel = R.estado_texto(sinc) if eq.activo() else ("—", "pendiente")
            listo, motivos = eq.listo()
            hz, perd = eq.ritmo()
            vals = [eq.nombre, eq.ip, f"{eq.recibidos} / {eq.perdidos} ({pct:.1f} %)",
                    f"{bat} %" if bat is not None and bat >= 0 else "—", txt_sinc,
                    "✔ listo" if listo else "✘ " + motivos[0], est]
            colores = {5: {"bueno": "#1a7f37", "aceptable": "#B8860B", "malo": "#B00", "pendiente": "#777"}[nivel],
                       6: "#1a7f37" if listo else "#B00", 7: "#B00" if eq.grabando else "#222"}
            for j, txt in enumerate(vals, start=1):
                it = self.tabla.item(i, j)
                if it is None:
                    it = QtWidgets.QTableWidgetItem(); self.tabla.setItem(i, j, it)
                it.setText(str(txt))
                it.setForeground(QtGui.QColor(colores.get(j, "#222")))
            self.tabla.item(i, 6).setToolTip("\n".join(motivos) if motivos else
                                             f"{hz:.0f} muestras/s, {perd:.1f} % perdidos, control remoto OK")
            if sinc:
                self.tabla.item(i, 5).setToolTip(
                    f"Desfase con el reloj del PC estimado en {sinc['ventanas']} ventanas de 2 s\n"
                    f"Deriva del reloj del equipo: {sinc['deriva_ppm']:+.0f} ppm")
        # sincronía entre equipos
        sincronizados = [(eq, eq.sincronia()) for eq in lista if eq.activo() and self.usar.get(eq.ip, True)]
        ok = [(eq, r) for eq, r in sincronizados if r is not None]
        if not sincronizados:
            self.lbl_sinc.setText("Esperando equipos…")
        elif len(ok) < len(sincronizados):
            self.lbl_sinc.setText(f"Sincronizando relojes: {len(ok)} de {len(sincronizados)} equipos listos "
                                  "(se necesitan unos 10 s de datos).")
            self.lbl_sinc.setStyleSheet("color:#777;")
        elif len(ok) == 1:
            self.lbl_sinc.setText(f"Reloj sincronizado con el PC (±{ok[0][1]['incertidumbre_ms']:.0f} ms).")
            self.lbl_sinc.setStyleSheet("color:#1a7f37;")
        else:
            entre = sorted(r["incertidumbre_ms"] for _, r in ok)[-2:]
            total = sum(entre)
            color = "#1a7f37" if total <= 2 * R.UMBRAL_BUENO_MS else ("#B8860B" if total <= 2 * R.UMBRAL_ACEPTABLE_MS else "#B00")
            self.lbl_sinc.setText(f"✔ {len(ok)} equipos sincronizados entre sí con el reloj del PC "
                                  f"(diferencia máxima estimada ±{total:.0f} ms).")
            self.lbl_sinc.setStyleSheet(f"color:{color};")
        # estado de batería cada ~5 s
        for eq in lista:
            periodo = 2 if eq.grabando else 5
            if eq.control and time.time() - getattr(eq, "_ult_estado", 0) > periodo:
                eq._ult_estado = time.time()
                QtCore.QThreadPool.globalInstance().start(lambda e=eq: self._estado(e))
        # gráfico del equipo seleccionado
        fila = self.tabla.currentRow()
        if lista:
            eq = lista[fila if 0 <= fila < len(lista) else 0]
            t, vals = eq.ventana()
            if len(t) > 10:
                eje = int(np.argmax(np.abs(vals[:, :3].mean(axis=0))))
                v = vals[:, eje]
                self.lbl_graf.setText(f"{eq.nombre} · eje vertical {'xyz'[eje]} (últimos 10 s)")
                self.c_senal.setData(t, v)
                if len(t) >= 4 * P.FS:
                    tg = np.arange(t[0], t[-1], 1 / P.FS)
                    vg = np.interp(tg, t, v)
                    vg = vg - vg.mean()
                    f, p = A.welch(vg, P.FS)
                    m = f <= 4
                    self.c_esp.setData(f[m], p[m])
                    fd = A.espectro(vg, P.FS)[0]
                    self.l_fd.setValue(fd)
                    self.lbl_fd.setText(f"Frecuencia dominante: {fd:.2f} Hz ({fd * 60:.0f} pasos/min)")
        if self.registro:
            seg = time.time() - self.inicio
            self.lbl_tiempo.setText(f"● GRABANDO {self.registro.sesion} · {seg:5.1f} s")
            self.revisar_alertas()
        else:
            self.lbl_alerta.hide()

    # ------------------------------------------------------------------ alertas y recuperación
    def revisar_alertas(self):
        avisos = []
        for eq in self.registro.equipos:
            sin_datos = time.time() - eq.ultimo_paquete
            if sin_datos > 3:
                avisos.append((f"{eq.ip}-datos", f"{eq.nombre}: no llegan datos hace {sin_datos:.0f} s "
                               "(revisa que esté encendido y cerca; el respaldo interno sigue grabando)."))
            est = eq.estado or {}
            bat = est.get("bateria", -1)
            if bat is not None and 0 <= bat < 15:
                avisos.append((f"{eq.ip}-bat", f"{eq.nombre}: batería baja ({bat} %)."))
            libre = est.get("libre_kb")
            if libre is not None and libre < 200:
                avisos.append((f"{eq.ip}-mem", f"{eq.nombre}: queda poca memoria para el respaldo ({libre} kB)."))
            if eq.control and est and sin_datos < 3 and est.get("grabando") is False and \
                    time.time() - self.inicio > 5:
                avisos.append((f"{eq.ip}-grab", f"{eq.nombre}: dejó de guardar su respaldo "
                               "(¿memoria llena o reinicio?). Lo recibido en vivo se sigue guardando."))
            if eq.rec_reinicios:
                avisos.append((f"{eq.ip}-rein{eq.rec_reinicios}", f"{eq.nombre}: se reinició durante el registro "
                               f"({eq.rec_reinicios} vez/veces); el respaldo continúa en un archivo aparte."))
        if avisos:
            self.lbl_alerta.setText("⚠ " + "\n⚠ ".join(t for _, t in avisos))
            self.lbl_alerta.show()
            for clave, texto in avisos:
                if clave not in self.alertas_vistas:
                    self.alertas_vistas.add(clave)
                    self.escribir("ALERTA: " + texto)
                    QtWidgets.QApplication.beep()
        else:
            self.lbl_alerta.hide()

    def actualizar_pendientes(self):
        n = len(RG.leer_pendientes(self.ed_base.text()))
        self.lbl_pend.setText(f"Respaldos pendientes: {n} (se bajan solos cuando el equipo vuelve)" if n else
                              "Sin respaldos pendientes.")
        self.lbl_pend.setStyleSheet("color:#B8860B;" if n else "color:#777;")

    def revisar_pendientes(self):
        self.actualizar_pendientes()
        if self.registro is not None or (self.trabajo_pend and self.trabajo_pend.isRunning()):
            return
        base = self.ed_base.text()
        if not RG.leer_pendientes(base):
            return
        equipos = self.rx.lista()

        def tarea(_tr):
            return RG.procesar_pendientes(base, equipos)

        self.trabajo_pend = Trabajo(tarea)
        self.trabajo_pend.listo.connect(lambda msgs: ([self.escribir(m) for m in msgs if "recuperado" in m],
                                                      self.actualizar_pendientes()))
        self.trabajo_pend.start()

    def revisar_sin_terminar(self):
        for f in RG.sesiones_sin_terminar(self.ed_base.text()):
            carpeta = f.parent
            if self.registro is not None and carpeta == self.registro.carpeta:
                continue
            r = QtWidgets.QMessageBox.question(
                self, "Sesión sin terminar",
                f"La sesión {carpeta.name} quedó abierta (el programa se cerró durante el registro).\n\n"
                "¿Recuperarla ahora? Se cierra el registro en los equipos que sigan grabando, se descargan sus "
                "respaldos y se unen con lo que alcanzó a llegar al PC.")
            if r != QtWidgets.QMessageBox.Yes:
                continue
            self.escribir(f"Recuperando {carpeta.name}…")
            tr = Trabajo(lambda _t, c=carpeta: RG.recuperar_sesion(c))
            tr.listo.connect(self.registro_terminado)
            tr.fallo.connect(lambda e: self.escribir("No se pudo recuperar:\n" + e))
            self._recuperacion = tr
            tr.start()
            break

    def dialogo_respaldos(self):
        DialogoRespaldos(self, self.rx, self.ed_base.text()).exec()
        self.actualizar_pendientes()

    def _estado(self, eq):
        try:
            eq.estado = pedir(eq.ip, "/estado", timeout=1.5)
        except Exception:
            pass

    def alternar(self):
        if self.registro is None:
            eqs = self.equipos_elegidos()
            if not eqs:
                QtWidgets.QMessageBox.warning(self, "Sin equipos", "No hay equipos enviando datos. Revisa que el PC "
                                              "esté conectado a la red IMU-Marcha (o inicia el simulador).")
                return
            problemas = []
            for eq in eqs:
                listo, motivos = eq.listo()
                if not listo:
                    problemas.append(f"• {eq.nombre}: " + "; ".join(motivos))
            if problemas:
                r = QtWidgets.QMessageBox.question(
                    self, "Hay equipos que no están listos",
                    "\n".join(problemas) + "\n\n¿Iniciar el registro de todos modos?")
                if r != QtWidgets.QMessageBox.Yes:
                    return
            s = nombre_sesion(self.ed_part.text(), self.cb_cond.currentText(), self.sp_rep.value())
            destino = carpeta_sesion(self.ed_base.text(), self.ed_part.text(), s)
            if destino.exists() and any(destino.iterdir()):
                r = QtWidgets.QMessageBox.question(self, "La sesión ya existe",
                                                   f"{destino}\nya tiene archivos. ¿Sobrescribir?")
                if r != QtWidgets.QMessageBox.Yes:
                    return
            cfg = configuracion()
            cfg.setValue("carpeta", self.ed_base.text()); cfg.setValue("participante", self.ed_part.text())
            try:
                self.registro = Registro(eqs, self.ed_base.text(), self.ed_part.text(), self.cb_cond.currentText(),
                                         self.sp_rep.value(), self.ed_notas.text())
                self.registro.iniciar()
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "No se pudo iniciar", str(e))
                self.registro = None
                return
            self.inicio = time.time()
            for m in self.registro.mensajes:
                self.escribir(m)
            self.escribir(f"Registro iniciado: {self.registro.sesion} con {', '.join(e.nombre for e in eqs)}. "
                          "Recuerda la marca de sincronización (un salto, o 5 s de pie quieto en adultos mayores).")
            self.btn.setText("■  Detener registro")
            self.btn.setStyleSheet("background:#C0392B;color:white;")
        else:
            reg = self.registro
            self.btn.setEnabled(False)
            self.lbl_tiempo.setText("Deteniendo y descargando respaldos…")

            def tarea(trabajo):
                return reg.detener(lambda txt: trabajo.progreso.emit(0, 0, txt))

            self.trabajo = Trabajo(tarea)
            self.trabajo.progreso.connect(lambda a, b, txt: self.escribir(txt))
            self.trabajo.listo.connect(self.registro_terminado)
            self.trabajo.fallo.connect(lambda e: (self.escribir("Error al guardar:\n" + e), self.registro_terminado(None)))
            self.trabajo.start()

    def registro_terminado(self, meta):
        self.registro = None
        self.alertas_vistas.clear()
        self.lbl_alerta.hide()
        self.btn.setEnabled(True)
        self.btn.setText("▶  Iniciar registro")
        self.btn.setStyleSheet("")
        if meta:
            for m in meta["mensajes"]:
                self.escribir(m)
            for e in meta["equipos"]:
                self.escribir(f"{e['equipo']}: {e['muestras']} muestras ({e['duracion_s']} s); perdidos en vivo "
                              f"{e['perdidos_en_vivo']}, recuperados del respaldo {e['recuperados_del_respaldo']}, "
                              f"faltantes {e['faltantes_final']} → {e['archivo']}")
                if e.get("reloj"):
                    rl = e["reloj"]
                    self.escribir(f"   reloj: inicio {rl['inicio_pc_hora']} (±{rl['incertidumbre_ms']:.0f} ms), "
                                  f"deriva {rl['deriva_ppm']:+.0f} ppm")
            self.escribir(f"Sesión guardada en {carpeta_sesion(self.ed_base.text(), meta['participante'], meta['sesion'])}")
            if meta.get("pendientes"):
                self.escribir("⚠ Quedaron respaldos pendientes; se descargarán solos cuando el equipo vuelva a la red.")
            self.actualizar_pendientes()
            dur = f"{meta['duracion_s']} s" if meta.get("duracion_s") is not None else "recuperada"
            self.lbl_tiempo.setText(f"Guardado: {meta['sesion']} ({dur})")
            if not meta.get("recuperada"):
                self.sp_rep.setValue(self.sp_rep.value() + 1)
        self.actualizar_nombre()


class DialogoRespaldos(QtWidgets.QDialog):
    """Lista los respaldos guardados en cada equipo conectado y permite bajarlos a su sesión."""

    def __init__(self, padre, rx, base):
        super().__init__(padre)
        self.setWindowTitle("Respaldos en los equipos")
        self.resize(760, 420)
        self.rx, self.base = rx, Path(base)
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(QtWidgets.QLabel("Respaldos guardados en la memoria de los equipos conectados. Al bajarlos se unen "
                                     "con lo recibido en vivo y se rehace el CSV de la sesión."))
        self.tabla = QtWidgets.QTableWidget(0, 5)
        self.tabla.setHorizontalHeaderLabels(["", "Equipo", "Archivo", "Tamaño", "Sesión en el PC"])
        self.tabla.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.tabla, 1)
        self.lbl = QtWidgets.QLabel(); v.addWidget(self.lbl)
        h = QtWidgets.QHBoxLayout()
        for txt, fn in [("Actualizar", self.cargar), ("Bajar y unir los marcados", self.bajar),
                        ("Borrar del equipo los marcados", self.borrar), ("Cerrar", self.accept)]:
            b = QtWidgets.QPushButton(txt); b.clicked.connect(fn); h.addWidget(b)
        v.addLayout(h)
        self.filas = []
        self.cargar()

    def cargar(self):
        self.filas = []
        for eq in self.rx.lista():
            if not (eq.control and eq.activo()):
                continue
            try:
                lista = pedir(eq.ip, "/archivos", timeout=3)
            except Exception as e:  # noqa: BLE001
                self.lbl.setText(f"{eq.nombre}: no respondió ({e})")
                continue
            for f in lista:
                nombre = f["nombre"].lstrip("/")
                if not nombre.endswith(".bin"):
                    continue
                sesion = nombre[:-4].split("_cont")[0]
                carpetas = sorted(self.base.glob(f"*/{sesion}"))
                self.filas.append((eq, nombre, f["bytes"], sesion, carpetas[0] if carpetas else None))
        self.tabla.setRowCount(len(self.filas))
        for i, (eq, nombre, tam, sesion, carpeta) in enumerate(self.filas):
            chk = QtWidgets.QTableWidgetItem(); chk.setCheckState(QtCore.Qt.Unchecked)
            self.tabla.setItem(i, 0, chk)
            for j, txt in enumerate([eq.nombre, nombre, f"{tam / 1024:.0f} kB", str(carpeta) if carpeta else "— (no existe)"], 1):
                self.tabla.setItem(i, j, QtWidgets.QTableWidgetItem(txt))
        self.lbl.setText(f"{len(self.filas)} respaldo(s) en {len({f[0].ip for f in self.filas})} equipo(s).")

    def marcados(self):
        return [self.filas[i] for i in range(self.tabla.rowCount())
                if self.tabla.item(i, 0).checkState() == QtCore.Qt.Checked]

    def bajar(self):
        hechos = set()
        msgs = []
        for eq, nombre, tam, sesion, carpeta in self.marcados():
            if carpeta is None or (eq.ip, sesion) in hechos:
                continue
            hechos.add((eq.ip, sesion))
            meta = RG.leer_json(carpeta / "sesion.json", {}) or {}
            info = next((e for e in meta.get("equipos", []) if e.get("mac") == eq.mac or e.get("ip") == eq.ip), None)
            etiqueta = info["etiqueta"] if info else RG.etiqueta_archivo(eq)
            datos = {"ip": eq.ip, "mac": eq.mac, "nombre": eq.nombre, "etiqueta": etiqueta, "control": True}
            try:
                tramos = RG.descargar_respaldo(eq.ip, sesion, carpeta, etiqueta)
                vivos = P.leer_diario(carpeta / f"vivo_{etiqueta}.dat")
                r = RG.armar_equipo(carpeta, datos, vivos, tramos)
                RG.actualizar_equipo_en_sesion(carpeta, r)
                RG.guardar_pendientes(self.base, [p for p in RG.leer_pendientes(self.base)
                                                  if not (p["carpeta"] == str(carpeta) and p["etiqueta"] == etiqueta)])
                msgs.append(f"{sesion}: unido, faltantes {r['faltantes_final']}")
            except Exception as e:  # noqa: BLE001
                msgs.append(f"{sesion}: error ({e})")
        self.lbl.setText(" · ".join(msgs) or "Marca al menos un respaldo con sesión en el PC.")

    def borrar(self):
        sel = self.marcados()
        if not sel:
            return
        r = QtWidgets.QMessageBox.question(self, "Borrar respaldos",
                                           f"¿Borrar {len(sel)} archivo(s) de la memoria del equipo? "
                                           "Hazlo solo si ya los bajaste.")
        if r != QtWidgets.QMessageBox.Yes:
            return
        for eq, nombre, *_ in sel:
            try:
                pedir(eq.ip, "/borrar?n=" + nombre, timeout=3)
            except Exception:  # noqa: BLE001
                pass
        self.cargar()


# ====================================================================== 2. Procesar sesión
class PestanaProcesar(QtWidgets.QWidget):
    def __init__(self, pestana_registro):
        super().__init__()
        self.reg = pestana_registro
        self.carpeta = None
        self.puntos = None
        self.fps = None
        self.trabajo = None

        izq = QtWidgets.QVBoxLayout()
        g1 = QtWidgets.QGroupBox("1. Sesión")
        f1 = QtWidgets.QFormLayout(g1)
        self.ed_carpeta = QtWidgets.QLineEdit(); b = QtWidgets.QPushButton("Elegir…"); b.clicked.connect(self.elegir_sesion)
        h = QtWidgets.QHBoxLayout(); h.addWidget(self.ed_carpeta); h.addWidget(b)
        f1.addRow("Carpeta de la sesión:", h)
        self.cb_imu = QtWidgets.QComboBox(); f1.addRow("Archivo de la IMU:", self.cb_imu)
        self.lbl_meta = QtWidgets.QLabel(); self.lbl_meta.setWordWrap(True); f1.addRow(self.lbl_meta)
        izq.addWidget(g1)

        g2 = QtWidgets.QGroupBox("2. Video de la cámara")
        f2 = QtWidgets.QFormLayout(g2)
        self.ed_video = QtWidgets.QLineEdit(); b2 = QtWidgets.QPushButton("Elegir…"); b2.clicked.connect(self.elegir_video)
        h2 = QtWidgets.QHBoxLayout(); h2.addWidget(self.ed_video); h2.addWidget(b2)
        f2.addRow("Video:", h2)
        self.sp_fps = QtWidgets.QDoubleSpinBox(); self.sp_fps.setRange(1, 240); self.sp_fps.setDecimals(3)
        self.sp_fps.setToolTip("Cuadros por segundo reales. El valor del archivo puede no ser exacto.")
        f2.addRow("Cuadros por segundo:", self.sp_fps)
        self.btn_mp = QtWidgets.QPushButton("Extraer puntos con MediaPipe"); self.btn_mp.clicked.connect(self.extraer)
        self.barra = QtWidgets.QProgressBar()
        f2.addRow(self.btn_mp); f2.addRow(self.barra)
        izq.addWidget(g2)

        g3 = QtWidgets.QGroupBox("3. Sincronización por los saltos")
        f3 = QtWidgets.QFormLayout(g3)
        self.btn_sync = QtWidgets.QPushButton("Detectar saltos"); self.btn_sync.clicked.connect(self.sincronizar)
        f3.addRow(self.btn_sync)
        self.sp_desfase = QtWidgets.QDoubleSpinBox(); self.sp_desfase.setRange(-600, 600); self.sp_desfase.setDecimals(3)
        self.sp_desfase.setSingleStep(0.01); self.sp_desfase.setSuffix(" s")
        self.sp_desfase.setToolTip("t_IMU = t_video + desfase. Se puede ajustar a mano mirando el gráfico.")
        self.sp_desfase.valueChanged.connect(self.dibujar_sync)
        f3.addRow("Desfase (t_IMU − t_video):", self.sp_desfase)
        self.lbl_sync = QtWidgets.QLabel(); self.lbl_sync.setWordWrap(True); f3.addRow(self.lbl_sync)
        izq.addWidget(g3)

        g4 = QtWidgets.QGroupBox("4. Frecuencia dominante")
        f4 = QtWidgets.QFormLayout(g4)
        self.sp_t0 = QtWidgets.QDoubleSpinBox(); self.sp_t1 = QtWidgets.QDoubleSpinBox()
        for s in (self.sp_t0, self.sp_t1):
            s.setRange(0, 100000); s.setDecimals(2); s.setSuffix(" s (tiempo del video)")
        f4.addRow("Desde:", self.sp_t0); f4.addRow("Hasta:", self.sp_t1)
        self.btn_calc = QtWidgets.QPushButton("Calcular y guardar"); self.btn_calc.clicked.connect(self.calcular)
        f4.addRow(self.btn_calc)
        self.lbl_res = QtWidgets.QLabel(); self.lbl_res.setWordWrap(True)
        self.lbl_res.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        f4.addRow(self.lbl_res)
        izq.addWidget(g4)
        izq.addStretch()

        der = QtWidgets.QVBoxLayout()
        self.g_sync = pg.PlotWidget(title="IMU (azul) y cadera en el video (naranja), alineadas")
        self.g_sync.setLabel("bottom", "tiempo del video (s)")
        self.c_imu = self.g_sync.plot(pen=pg.mkPen(AZUL, width=1))
        self.c_vid = self.g_sync.plot(pen=pg.mkPen(NARANJA, width=1.5))
        self.region = pg.LinearRegionItem(brush=pg.mkBrush(31, 95, 168, 30))
        self.region.sigRegionChangeFinished.connect(self.region_cambio)
        self.g_sync.addItem(self.region)
        self.g_esp = pg.PlotWidget(title="Espectros del tramo")
        self.g_esp.setLabel("bottom", "frecuencia (Hz)"); self.g_esp.setXRange(0, 4); self.g_esp.addLegend()
        self.e_imu = self.g_esp.plot(pen=pg.mkPen(AZUL, width=1.5), name="IMU")
        self.e_vid = self.g_esp.plot(pen=pg.mkPen(NARANJA, width=1.5), name="Cámara")
        der.addWidget(self.g_sync, 3); der.addWidget(self.g_esp, 2)
        for s in (self.sp_t0, self.sp_t1):
            s.valueChanged.connect(self.tramo_cambio)

        raiz = QtWidgets.QHBoxLayout(self)
        wi = QtWidgets.QWidget(); wi.setLayout(izq); wi.setMaximumWidth(520)
        sc = QtWidgets.QScrollArea(); sc.setWidget(wi); sc.setWidgetResizable(True); sc.setMaximumWidth(540)
        raiz.addWidget(sc); raiz.addLayout(der, 1)

    # ------------------------------------------------------------------ sesión y video
    def elegir_sesion(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Carpeta de la sesión", self.reg.ed_base.text())
        if d:
            self.abrir_sesion(Path(d))

    def abrir_sesion(self, carpeta):
        self.carpeta = carpeta
        self.ed_carpeta.setText(str(carpeta))
        self.cb_imu.clear()
        self.cb_imu.addItems(sorted(p.name for p in carpeta.glob("imu_*.csv")))
        meta = carpeta / "sesion.json"
        if meta.exists():
            m = json.loads(meta.read_text(encoding="utf-8"))
            self.lbl_meta.setText(f"{m['participante']} · {m['condicion']} · repetición {m['repeticion']} · "
                                  f"{m['duracion_s']} s · {m['inicio']}")
        else:
            self.lbl_meta.setText("Sin sesion.json (carpeta creada a mano).")
        self.puntos = None
        pts = carpeta / "puntos_video.csv"
        if pts.exists():
            self.puntos = pd.read_csv(pts)
            info = carpeta / "video.json"
            if info.exists():
                vi = json.loads(info.read_text(encoding="utf-8"))
                self.ed_video.setText(vi.get("video", ""))
                self.sp_fps.setValue(vi.get("fps", 30))
            self.barra.setValue(100)
            self.sincronizar()
        res = carpeta / "resultado.json"
        if res.exists():
            r = json.loads(res.read_text(encoding="utf-8"))
            self.sp_desfase.setValue(r.get("desfase_s", 0))
            self.fijar_tramo(r.get("t0_video_s", 0), r.get("t1_video_s", 0))

    def elegir_video(self):
        a, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Video de la cámara", str(self.carpeta or ""),
                                                     "Videos (*.mp4 *.mov *.avi *.mkv);;Todos (*.*)")
        if a:
            self.ed_video.setText(a)
            try:
                import cv2
                cap = cv2.VideoCapture(a); fps = cap.get(cv2.CAP_PROP_FPS); cap.release()
                if fps:
                    self.sp_fps.setValue(fps)
            except Exception:
                pass

    def extraer(self):
        if not self.carpeta or not self.ed_video.text():
            QtWidgets.QMessageBox.information(self, "Falta información", "Elige la sesión y el video.")
            return
        modelo = ruta_modelo()
        if modelo is None:
            a, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Modelo de MediaPipe (pose_landmarker_full.task)", "", "*.task")
            if not a:
                return
            configuracion().setValue("modelo", a)
            modelo = Path(a)
        video = self.ed_video.text()
        self.btn_mp.setEnabled(False)
        self.barra.setValue(0)

        def tarea(tr):
            return A.extraer_puntos(video, modelo, progreso=lambda i, n: tr.progreso.emit(i, n, ""),
                                    cancelar=lambda: tr.cancelado)

        self.trabajo = Trabajo(tarea)
        self.trabajo.progreso.connect(lambda i, n, _: self.barra.setValue(int(100 * i / n) if n else 0))
        self.trabajo.listo.connect(self.puntos_listos)
        self.trabajo.fallo.connect(lambda e: (QtWidgets.QMessageBox.critical(self, "Error en MediaPipe", e),
                                              self.btn_mp.setEnabled(True)))
        self.trabajo.start()

    def puntos_listos(self, df):
        self.btn_mp.setEnabled(True)
        self.barra.setValue(100)
        self.puntos = df
        df.to_csv(self.carpeta / "puntos_video.csv", index=False, float_format="%.5f")
        (self.carpeta / "video.json").write_text(json.dumps(
            {"video": self.ed_video.text(), "fps": self.sp_fps.value(), "fps_archivo": df.attrs.get("fps")},
            indent=2, ensure_ascii=False), encoding="utf-8")
        detectados = df["cadera_izq_y"].notna().mean() * 100 if "cadera_izq_y" in df else 0
        self.lbl_sync.setText(f"MediaPipe detectó a la persona en el {detectados:.0f} % de los cuadros.")
        self.sincronizar()

    # ------------------------------------------------------------------ sincronización
    def datos_imu(self):
        if not self.carpeta or not self.cb_imu.currentText():
            return None
        return A.leer_imu(self.carpeta / self.cb_imu.currentText())

    def sincronizar(self):
        imu = self.datos_imu()
        if imu is None or self.puntos is None:
            return
        t, acc = imu
        fps = self.sp_fps.value()
        pts = self.puntos.copy(); pts["tiempo_s"] = self.tiempos_video()
        si = A.saltos_imu(t, acc)
        sv = A.saltos_video(pts, fps)
        d, disp = A.desfase_por_saltos(si, sv)
        txt = f"Saltos en la IMU: {len(si)} · en el video: {len(sv)}. "
        if d is None:
            txt += "No se pudieron emparejar; ajusta el desfase a mano."
        else:
            self.sp_desfase.setValue(d)
            txt += f"Desfase {d:.3f} s" + (f" (dispersión entre saltos {disp * 1000:.0f} ms)" if disp is not None else "")
            if disp is not None and disp > 0.1:
                txt += " ⚠ revisa a mano."
        self.lbl_sync.setText(txt)
        # tramo por defecto: desde 1 s después del último salto hasta el último cuadro válido
        tv, y, malo = A.cadera_cruda(pts)
        ok = np.flatnonzero(~malo)
        if len(ok):
            t0 = (sv[-1] + 1.0) if len(sv) else tv[ok[0]]
            self.fijar_tramo(max(t0, tv[ok[0]]), tv[ok[-1]])
        self.dibujar_sync()

    def tiempos_video(self):
        """Tiempos del video con el fps elegido (puede diferir del archivo)."""
        n = self.puntos["cuadro"].to_numpy()
        return n / self.sp_fps.value()

    def dibujar_sync(self):
        imu = self.datos_imu()
        if imu is None or self.puntos is None:
            return
        t, acc = imu
        tt, v, _ = A.senal_imu(t, acc)
        d = self.sp_desfase.value()
        tv_imu = tt - d
        s = np.std(v) or 1
        self.c_imu.setData(tv_imu, v / s)
        pts = self.puntos.copy(); pts["tiempo_s"] = self.tiempos_video()
        tv, y, malo = A.cadera_cruda(pts)
        yv = y - np.nanmedian(y)
        sy = np.nanstd(yv) or 1
        self.c_vid.setData(tv, np.nan_to_num(yv / sy) + 4, connect="finite")
        self.tramo_cambio()

    def fijar_tramo(self, t0, t1):
        for sp, v in ((self.sp_t0, t0), (self.sp_t1, t1)):
            sp.blockSignals(True); sp.setValue(v); sp.blockSignals(False)
        self.region.blockSignals(True); self.region.setRegion((t0, t1)); self.region.blockSignals(False)

    def tramo_cambio(self, _=None):
        if self.sp_t1.value() > self.sp_t0.value():
            self.region.blockSignals(True)
            self.region.setRegion((self.sp_t0.value(), self.sp_t1.value()))
            self.region.blockSignals(False)

    def region_cambio(self):
        a, b = self.region.getRegion()
        self.sp_t0.blockSignals(True); self.sp_t1.blockSignals(True)
        self.sp_t0.setValue(a); self.sp_t1.setValue(b)
        self.sp_t0.blockSignals(False); self.sp_t1.blockSignals(False)

    # ------------------------------------------------------------------ cálculo
    def calcular(self):
        imu = self.datos_imu()
        if imu is None or self.puntos is None:
            QtWidgets.QMessageBox.information(self, "Falta información", "Necesitas la IMU y los puntos del video.")
            return
        t, acc = imu
        fps = self.sp_fps.value()
        d = self.sp_desfase.value()
        t0, t1 = self.sp_t0.value(), self.sp_t1.value()
        pts = self.puntos.copy(); pts["tiempo_s"] = self.tiempos_video()
        try:
            rv = A.frecuencia_video(pts, fps, ventana=(t0, t1))
            # el mismo tramo, en el tiempo de la IMU
            ri = A.frecuencia_imu(t, acc, ventana=(rv["t_inicio_s"] + d, rv["t_fin_s"] + d))
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "No se pudo calcular", str(e))
            return
        meta = {}
        if (self.carpeta / "sesion.json").exists():
            meta = json.loads((self.carpeta / "sesion.json").read_text(encoding="utf-8"))
        fila = {
            "sesion": meta.get("sesion", self.carpeta.name), "participante": meta.get("participante", ""),
            "condicion": meta.get("condicion", ""), "repeticion": meta.get("repeticion", ""),
            "equipo_imu": self.cb_imu.currentText(),
            "frec_camara_hz": rv["frec_dominante_hz"], "frec_imu_hz": ri["frec_dominante_hz"],
            "diferencia_hz": rv["frec_dominante_hz"] - ri["frec_dominante_hz"],
            "cadencia_camara_pasos_min": rv["cadencia_conteo_pasos_min"], "cadencia_imu_pasos_min": ri["cadencia_conteo_pasos_min"],
            "corregida_camara": rv["corregida"], "corregida_imu": ri["corregida"],
            "valida_camara": rv["valida"], "descartada_imu": ri["descartada"],
            "pct_cuadros_perdidos": rv["pct_cuadros_perdidos"], "duracion_s": rv["duracion_s"],
            "eje_vertical_imu": ri["eje_vertical"], "desfase_s": d, "t0_video_s": t0, "t1_video_s": t1, "fps": fps,
            "valida": bool(rv["valida"] and not ri["descartada"]),
            "riesgo_camara": rv["frec_dominante_hz"] < A.UMBRAL_RIESGO_HZ, "riesgo_imu": ri["frec_dominante_hz"] < A.UMBRAL_RIESGO_HZ,
            "procesado": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (self.carpeta / "resultado.json").write_text(json.dumps(fila, indent=2, ensure_ascii=False, default=float), encoding="utf-8")
        tabla = Path(self.reg.ed_base.text()) / "resultados_etapa2.csv"
        try:
            tabla.parent.mkdir(parents=True, exist_ok=True)
            previo = pd.read_csv(tabla) if tabla.exists() else pd.DataFrame()
            if len(previo):
                previo = previo[~((previo["sesion"] == fila["sesion"]) & (previo["equipo_imu"] == fila["equipo_imu"]))]
            pd.concat([previo, pd.DataFrame([fila])], ignore_index=True).to_csv(tabla, index=False)
            guardado = f"Guardado en {tabla.name} y resultado.json."
        except PermissionError:
            guardado = f"⚠ No se pudo escribir {tabla} (¿está abierto en Excel?). Quedó en resultado.json."
        aviso = ""
        if fila["condicion"] != "caminata_1min":
            aviso = "\n(El umbral de 1,93 Hz se aplica solo a la caminata de 1 minuto.)"
        self.lbl_res.setText(
            f"<b>Cámara:</b> {rv['frec_dominante_hz']:.3f} Hz ({rv['frec_dominante_hz'] * 60:.0f} pasos/min)"
            f"{' · corregida por la regla de errores gruesos' if rv['corregida'] else ''}<br>"
            f"<b>IMU:</b> {ri['frec_dominante_hz']:.3f} Hz (eje {ri['eje_vertical']})"
            f"{' · corregida' if ri['corregida'] else ''}<br>"
            f"<b>Diferencia:</b> {fila['diferencia_hz']:+.3f} Hz · tramo {rv['duracion_s']:.1f} s · "
            f"cuadros perdidos {rv['pct_cuadros_perdidos']:.1f} %<br>"
            f"<b>{'Válida' if fila['valida'] else 'NO válida'}</b> · {guardado}{aviso}")
        # espectros
        try:
            _, vimu, _ = A.senal_imu(t, acc, (rv["t_inicio_s"] + d, rv["t_fin_s"] + d))
            f, p = A.welch(vimu, A.FS_IMU); m = f <= 4
            self.e_imu.setData(f[m], p[m] / p[m].max())
            _, yv, _ = A.senal_cadera(pts, fps, (t0, t1))
            f2, p2 = A.welch(yv, fps); m2 = f2 <= 4
            self.e_vid.setData(f2[m2], p2[m2] / p2[m2].max())
        except Exception:
            pass


# ====================================================================== 3. Concordancia
class PestanaConcordancia(QtWidgets.QWidget):
    def __init__(self, pestana_registro):
        super().__init__()
        self.reg = pestana_registro
        izq = QtWidgets.QVBoxLayout()
        b = QtWidgets.QPushButton("Actualizar con resultados_etapa2.csv"); b.clicked.connect(self.actualizar)
        izq.addWidget(b)
        self.chk_validas = QtWidgets.QCheckBox("Usar solo pasadas válidas"); self.chk_validas.setChecked(True)
        self.chk_validas.toggled.connect(self.actualizar)
        izq.addWidget(self.chk_validas)
        self.txt = QtWidgets.QTextBrowser(); izq.addWidget(self.txt, 1)
        b2 = QtWidgets.QPushButton("Exportar gráfico y resumen"); b2.clicked.connect(self.exportar)
        izq.addWidget(b2)
        self.g = pg.PlotWidget(title="Bland-Altman: cámara − IMU")
        self.g.setLabel("bottom", "promedio de cámara e IMU (Hz)"); self.g.setLabel("left", "diferencia (Hz)")
        raiz = QtWidgets.QHBoxLayout(self)
        wi = QtWidgets.QWidget(); wi.setLayout(izq); wi.setMaximumWidth(480)
        raiz.addWidget(wi); raiz.addWidget(self.g, 1)
        self.resumen = ""

    def actualizar(self):
        ruta = Path(self.reg.ed_base.text()) / "resultados_etapa2.csv"
        self.g.clear()
        if not ruta.exists():
            self.txt.setHtml("Todavía no hay resultados. Procesa al menos dos sesiones en la pestaña anterior.")
            return
        t = pd.read_csv(ruta)
        if self.chk_validas.isChecked() and "valida" in t:
            t = t[t["valida"].astype(str).str.lower() == "true"]
        if len(t) < 2:
            self.txt.setHtml(f"Hay {len(t)} resultado(s) válido(s); se necesitan al menos 2.")
            return
        t["condicion"] = t["condicion"].astype(str)
        r = A.evaluar_criterios(t)
        c = A.CRITERIOS
        ok = lambda x: "<span style='color:#1a7f37'>cumple</span>" if x else "<span style='color:#B00'>no cumple</span>"
        html = (f"<h3>Concordancia cámara–IMU (n = {r['n']})</h3>"
                f"<p>Sesgo: <b>{r['sesgo_hz']:+.3f} Hz</b> (criterio ±{c['sesgo_max_hz']}) → {ok(r['cumple_sesgo'])}<br>"
                f"Límites de acuerdo: <b>{r['loa_inf_hz']:+.3f} a {r['loa_sup_hz']:+.3f} Hz</b> (criterio ±{c['loa_max_hz']}) → {ok(r['cumple_loa'])}<br>"
                f"ICC(A,1) global: <b>{r['icc_global']:.3f}</b> (criterio ≥ {c['icc_min_global']}) → {ok(r['cumple_icc_global'])}<br>"
                f"ICC(A,1) velocidad normal (n = {r['n_normal']}): <b>{r['icc_normal']:.3f}</b> (criterio ≥ {c['icc_min_normal']}) → {ok(r['cumple_icc_normal'])}</p>")
        un_min = t[t["condicion"] == "caminata_1min"]
        if len(un_min):
            acuerdo = (un_min["riesgo_camara"].astype(str) == un_min["riesgo_imu"].astype(str)).mean()
            html += (f"<p><b>Umbral de 1,93 Hz (solo caminata de 1 minuto, análisis secundario):</b> "
                     f"cámara e IMU clasifican igual en el {100 * acuerdo:.0f} % de {len(un_min)} caminatas.</p>")
        html += "<p>Por condición:</p><table border=1 cellpadding=3 cellspacing=0><tr><th>Condición</th><th>n</th><th>Sesgo (Hz)</th></tr>"
        for cond, g in t.groupby("condicion"):
            html += f"<tr><td>{cond}</td><td>{len(g)}</td><td>{(g['frec_camara_hz'] - g['frec_imu_hz']).mean():+.3f}</td></tr>"
        html += "</table>"
        self.txt.setHtml(html)
        self.resumen = html
        media = (t["frec_camara_hz"] + t["frec_imu_hz"]) / 2
        dif = t["frec_camara_hz"] - t["frec_imu_hz"]
        colores = {"normal": AZUL, "lenta": "#2E8B57", "rapida": NARANJA, "caminata_1min": "#7B3FA0"}
        self.g.addLegend()
        for cond, g in t.groupby("condicion"):
            m = (g["frec_camara_hz"] + g["frec_imu_hz"]) / 2
            dd = g["frec_camara_hz"] - g["frec_imu_hz"]
            self.g.plot(m.to_numpy(), dd.to_numpy(), pen=None, symbol="o", symbolSize=8,
                        symbolBrush=colores.get(cond, "#555"), name=cond)
        for y, estilo in [(r["sesgo_hz"], QtCore.Qt.SolidLine), (r["loa_inf_hz"], QtCore.Qt.DashLine), (r["loa_sup_hz"], QtCore.Qt.DashLine)]:
            self.g.addItem(pg.InfiniteLine(pos=y, angle=0, pen=pg.mkPen("#333", style=estilo)))
        for y in (-c["loa_max_hz"], c["loa_max_hz"]):
            self.g.addItem(pg.InfiniteLine(pos=y, angle=0, pen=pg.mkPen("#B00", style=QtCore.Qt.DotLine)))

    def exportar(self):
        base = Path(self.reg.ed_base.text())
        try:
            from pyqtgraph.exporters import ImageExporter
            ImageExporter(self.g.plotItem).export(str(base / "bland_altman.png"))
            (base / "concordancia_resumen.html").write_text(self.resumen, encoding="utf-8")
            QtWidgets.QMessageBox.information(self, "Exportado", f"Guardado en {base}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "No se pudo exportar", str(e))


# ====================================================================== 4. Ayuda
AYUDA = """
<h3>Cómo usar IMU Marcha</h3>
<ol>
<li><b>Red.</b> Enciende el Stick: crea la red WiFi <b>IMU-Marcha</b> (clave marcha2026). Conecta el PC a esa red.
Si usas el celular como IMU, conéctalo a la misma red, abre la app Android, pestaña <i>Emisor</i>, botón
<i>Toda la red WiFi</i> e <i>Iniciar emisor</i>.</li>
<li><b>Registro.</b> Los equipos aparecen solos en la tabla. Marca los que quieras usar, completa participante,
condición y repetición, y presiona <i>Iniciar registro</i>. El equipo en la zona lumbar empieza a guardar su
respaldo (en el Stick la pantalla se pone roja y dice GRABANDO).</li>
<li><b>Marca de sincronización.</b> Con la cámara grabando: en voluntarios jóvenes, <b>un salto con ambos pies</b> antes de
cada pasada (el software lo detecta solo). En adultos mayores <b>no se salta</b>: 5 s de pie quietos y luego caminar; el desfase se
ajusta a mano en <i>Procesar</i> alineando el inicio de la marcha (en la caminata de 1 minuto basta con esa marca).</li>
<li><b>Detener.</b> Al detener, el PC descarga el respaldo del equipo y rellena lo que se perdió por WiFi.
Se guardan <i>imu_&lt;equipo&gt;.csv</i> (mismas columnas que ver_imu.py), el respaldo .bin, la hora de llegada de cada
paquete y <i>sesion.json</i>.</li>
<li><b>Procesar.</b> Copia el video del celular-cámara, elige la sesión y el video, extrae los puntos con MediaPipe,
revisa la sincronización y el tramo, y calcula. El resultado se agrega a <i>resultados_etapa2.csv</i>.</li>
<li><b>Concordancia.</b> Bland-Altman e ICC con los criterios fijados en el plan (sesgo ±0,05 Hz, límites ±0,10 Hz,
ICC ≥ 0,90 global y ≥ 0,75 a velocidad normal).</li>
</ol>
<p>Puertos: datos UDP 5005 y control HTTP 8080. Si Windows pregunta por el firewall, permite el acceso en redes privadas.</p>
<p>Sin equipo: en <i>Registro</i>, botón <i>Iniciar simulador</i>.</p>
"""


class Ventana(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"IMU Marcha · Proyecto 1 · v{__version__}")
        self.resize(1300, 820)
        self.rx = Receptor(); self.rx.start()
        tabs = QtWidgets.QTabWidget()
        self.p_reg = PestanaRegistro(self.rx)
        tabs.addTab(self.p_reg, "1. Registro")
        tabs.addTab(PestanaProcesar(self.p_reg), "2. Procesar sesión")
        self.p_conc = PestanaConcordancia(self.p_reg)
        tabs.addTab(self.p_conc, "3. Concordancia")
        ayuda = QtWidgets.QTextBrowser(); ayuda.setHtml(AYUDA)
        tabs.addTab(ayuda, "Ayuda")
        tabs.currentChanged.connect(lambda i: self.p_conc.actualizar() if i == 2 else None)
        self.setCentralWidget(tabs)

    def closeEvent(self, e):
        if self.p_reg.registro is not None:
            r = QtWidgets.QMessageBox.question(self, "Registro en curso", "Hay un registro en curso. ¿Detenerlo y salir?")
            if r != QtWidgets.QMessageBox.Yes:
                e.ignore(); return
            self.p_reg.registro.detener()
        if self.p_reg.simulador:
            self.p_reg.simulador.detener()
        self.rx.activo = False
        e.accept()


def autoprueba():
    """Comprobación rápida del ejecutable (la usa la compilación en GitHub)."""
    salida = Path(sys.argv[0]).resolve().parent / "autoprueba.txt"
    try:
        import cv2
        import mediapipe
        import scipy.signal  # noqa: F401
        modelo = ruta_modelo()
        assert modelo is not None, "falta pose_landmarker_full.task"
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        opciones = vision.PoseLandmarkerOptions(base_options=mp_python.BaseOptions(model_asset_path=str(modelo)),
                                                running_mode=vision.RunningMode.IMAGE)
        with vision.PoseLandmarker.create_from_options(opciones):
            pass
        salida.write_text(f"ok cv2 {cv2.__version__} mediapipe {mediapipe.__version__} modelo {modelo.name}", encoding="utf-8")
        return 0
    except Exception:
        salida.write_text("error\n" + traceback.format_exc(), encoding="utf-8")
        return 1


def main():
    if "--autoprueba" in sys.argv:
        sys.exit(autoprueba())
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("IMU Marcha")
    w = Ventana(); w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
