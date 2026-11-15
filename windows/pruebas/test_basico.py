"""Pruebas sin hardware: registro con control remoto y respaldo, frecuencia dominante,
sincronización por saltos y concordancia. Ejecutar desde la carpeta windows:
    python -m pytest pruebas -q
"""
import json
import time

import numpy as np
import pandas as pd

from imu_marcha_pc import analisis as A
from imu_marcha_pc import protocolo as P
from imu_marcha_pc.registro import Receptor, Registro
from imu_marcha_pc.simulador import SimuladorEquipo


def test_registro_con_respaldo(tmp_path):
    sim = SimuladorEquipo(frecuencia=1.9, perdida=0.05, carpeta=tmp_path / "equipo").iniciar()
    rx = Receptor()
    rx.start()
    try:
        for _ in range(40):
            time.sleep(0.1)
            if rx.lista() and rx.lista()[0].control is not None:
                break
        eqs = rx.lista()
        assert len(eqs) == 1 and eqs[0].control is True and eqs[0].nombre == "Simulador"
        reg = Registro(eqs, tmp_path / "datos", "P01", "normal", 1)
        reg.iniciar()
        time.sleep(12)
        meta = reg.detener()
    finally:
        rx.activo = False
        sim.detener()
    info = meta["equipos"][0]
    assert info["con_respaldo"] and info["perdidos_en_vivo"] > 0
    assert info["faltantes_final"] == 0                      # el respaldo rellenó todo
    assert info["recuperados_del_respaldo"] >= info["perdidos_en_vivo"] - 2
    carpeta = tmp_path / "datos" / "P01" / "P01_normal_1"
    df = pd.read_csv(carpeta / "imu_Simulador.csv")
    assert list(df.columns) == P.COLUMNAS
    dt = np.diff(df["t_s"])
    assert abs(np.median(dt) - 0.01) < 1e-6 and dt.max() < 0.0105   # sin huecos
    t, acc = A.leer_imu(carpeta / "imu_Simulador.csv")
    # frecuencia después de los saltos (t > 7 s)
    r = A.frecuencia_imu(t, acc, ventana=(7, t[-1]))
    assert abs(r["frec_dominante_hz"] - 1.9) < 0.02, r
    assert json.loads((carpeta / "sesion.json").read_text(encoding="utf-8"))["participante"] == "P01"


def test_regla_errores_gruesos():
    fs = 100
    t = np.arange(0, 30, 1 / fs)
    # el pico de zancada (0,95 Hz) domina, pero los pasos van a 1,9 Hz
    v = 1.0 * np.sin(2 * np.pi * 0.95 * t) + 0.6 * np.sin(2 * np.pi * 1.9 * t)
    r = A.frecuencia_con_control(v, fs)
    assert r["frec_original_hz"] < 1.2
    # sin un conteo de pasos coherente la regla no puede corregir; con señal de pasos clara sí
    v2 = 0.4 * np.sin(2 * np.pi * 0.95 * t) + 1.0 * np.sin(2 * np.pi * 1.9 * t)
    r2 = A.frecuencia_con_control(v2, fs)
    assert abs(r2["frec_dominante_hz"] - 1.9) < 0.02 and not r2["descartada"]


def test_sincronizacion_saltos():
    fs = 100
    t = np.arange(0, 40, 1 / fs)
    acc = np.zeros((len(t), 3))
    acc[:, 0] = 1.0
    for s in (5.0, 6.0, 7.0):                         # aterrizajes en la IMU
        acc[(t >= s) & (t < s + 0.04), 0] = 2.6
    tiempos_imu = A.saltos_imu(t, acc)
    assert len(tiempos_imu) == 3
    fps = 30
    tv = np.arange(0, 35, 1 / fps)
    y_cadera = np.full(len(tv), 0.55)
    for s in (2.4, 3.4, 4.4):                         # en el video, los saltos 2,6 s antes
        m = (tv >= s - 0.2) & (tv < s)
        y_cadera[m] -= 0.08 * np.sin(np.pi * (tv[m] - (s - 0.2)) / 0.2)
    df = pd.DataFrame({"tiempo_s": tv, "cadera_izq_y": y_cadera, "cadera_der_y": y_cadera,
                       "hombro_izq_y": y_cadera - 0.3, "hombro_der_y": y_cadera - 0.3,
                       "cadera_izq_vis": 1.0, "cadera_der_vis": 1.0, "hombro_izq_vis": 1.0, "hombro_der_vis": 1.0})
    tiempos_vid = A.saltos_video(df, fps)
    assert len(tiempos_vid) == 3
    d, disp = A.desfase_por_saltos(tiempos_imu, tiempos_vid)
    assert abs(d - 2.6) < 0.1, (d, tiempos_imu, tiempos_vid)


def test_concordancia():
    rng = np.random.default_rng(1)
    imu = rng.uniform(1.6, 2.2, 30)
    cam = imu + rng.normal(0.01, 0.02, 30)
    tabla = pd.DataFrame({"frec_camara_hz": cam, "frec_imu_hz": imu,
                          "condicion": ["normal"] * 10 + ["lenta"] * 10 + ["rapida"] * 10})
    r = A.evaluar_criterios(tabla)
    assert r["cumple_sesgo"] and r["cumple_loa"] and r["cumple_icc_global"] and r["icc_global"] > 0.95
    assert abs(A.icc_a1(imu, imu) - 1.0) < 1e-9
