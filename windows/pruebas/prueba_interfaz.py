"""Prueba de la interfaz sin pantalla: registra 12 s con el simulador, crea puntos de video
sintéticos (misma marcha, saltos 2,5 s antes) y procesa la sesión. Uso:
    QT_QPA_PLATFORM=offscreen python pruebas/prueba_interfaz.py <carpeta_temporal>
"""
import sys, time, json
from pathlib import Path
import numpy as np, pandas as pd
from PySide6 import QtWidgets, QtCore
from imu_marcha_pc.app import Ventana

base = Path(sys.argv[1]); base.mkdir(parents=True, exist_ok=True)
app = QtWidgets.QApplication(sys.argv)
w = Ventana(); w.show()
reg = w.p_reg
reg.ed_base.setText(str(base))

def esperar(seg, cond=lambda: False):
    fin = time.time() + seg
    while time.time() < fin and not cond():
        app.processEvents(); time.sleep(0.02)

reg.alternar_simulador()
esperar(5, lambda: any(e.control for e in w.rx.lista()))
esperar(20, lambda: all(e.listo()[0] for e in w.rx.lista()))
print("tabla:", [reg.tabla.item(0, j).text() for j in range(1, 8)])
print("sincronía:", reg.lbl_sinc.text())
reg.cb_cond.setCurrentText("normal")
reg.alternar()                       # iniciar
esperar(14)
reg.alternar()                       # detener
esperar(20, lambda: reg.registro is None and reg.btn.isEnabled())
print(reg.log.toPlainText())
carpeta = base / "P01" / "P01_normal_1"
print("archivos:", sorted(p.name for p in carpeta.iterdir()))

# puntos de video sintéticos: la cámara empezó 2,5 s después que la IMU
fps = 30.0
tv = np.arange(0, 11, 1 / fps)
ti = tv + 2.5
y = np.full(len(tv), 0.55) + 0.01 * np.sin(2 * np.pi * 1.9 * ti)
for s in (3.0, 4.0, 5.0):            # saltos (en tiempo de IMU) del simulador: aterrizan cerca de s
    m = (ti >= s - 0.2) & (ti < s)
    y[m] -= 0.08 * np.sin(np.pi * (ti[m] - (s - 0.2)) / 0.2)
pts = pd.DataFrame({"cuadro": np.arange(len(tv)), "tiempo_s": tv,
                    "cadera_izq_y": y, "cadera_der_y": y, "hombro_izq_y": y - 0.3, "hombro_der_y": y - 0.3,
                    "cadera_izq_vis": 1.0, "cadera_der_vis": 1.0, "hombro_izq_vis": 1.0, "hombro_der_vis": 1.0})
pts.to_csv(carpeta / "puntos_video.csv", index=False)
(carpeta / "video.json").write_text(json.dumps({"video": "sintetico.mp4", "fps": fps}))
pr = w.findChild(QtWidgets.QTabWidget).widget(1)
pr.abrir_sesion(carpeta)
print("sync:", pr.lbl_sync.text(), "| tramo", pr.sp_t0.value(), pr.sp_t1.value())
pr.calcular()
print(pr.lbl_res.text())
print(pd.read_csv(base / "resultados_etapa2.csv").T)
w.findChild(QtWidgets.QTabWidget).setCurrentIndex(2)
app.processEvents()
print(w.p_conc.txt.toPlainText()[:300])
w.close()
