"""Pruebas de fallas: cierre del PC a mitad del registro, corte de WiFi al detener y reinicio del
equipo mientras graba. En los tres casos la sesión debe terminar sin muestras faltantes."""
import json
import time

import numpy as np
import pandas as pd

from imu_marcha_pc import registro as RG
from imu_marcha_pc.registro import Receptor, Registro
from imu_marcha_pc.simulador import SimuladorEquipo


def preparar(tmp_path, perdida=0.05):
    sim = SimuladorEquipo(frecuencia=1.9, perdida=perdida, carpeta=tmp_path / "equipo").iniciar()
    rx = Receptor(); rx.start()
    for _ in range(50):
        time.sleep(0.1)
        if rx.lista() and rx.lista()[0].control is not None:
            break
    return sim, rx


def revisar_csv(carpeta, etiqueta="Simulador", reinicios=0):
    meta = json.loads((carpeta / "sesion.json").read_text(encoding="utf-8"))
    e = next(x for x in meta["equipos"] if x["etiqueta"] == etiqueta)
    assert e["faltantes_final"] == 0, e
    assert e["reinicios"] == reinicios, e
    df = pd.read_csv(carpeta / f"imu_{etiqueta}.csv")
    dt = np.diff(df["t_s"])
    assert dt.max() < 0.0105 and dt.min() > 0.0095, (dt.min(), dt.max())
    return meta, e, df


def test_cierre_del_pc(tmp_path):
    sim, rx = preparar(tmp_path)
    try:
        reg = Registro(rx.lista(), tmp_path / "d", "P01", "normal", 1)
        reg.iniciar()
        time.sleep(6)
        # el PC "se cae": se pierde el objeto Registro sin detener; el equipo sigue grabando
        rx.activo = False
        for eq in reg.equipos:
            eq.grabando = False
            eq.diario.close()
        time.sleep(3)
        pendientes = RG.sesiones_sin_terminar(tmp_path / "d")
        assert len(pendientes) == 1
        meta = RG.recuperar_sesion(pendientes[0].parent)
    finally:
        sim.detener()
    assert meta["recuperada"] and not meta["pendientes"]
    _, e, df = revisar_csv(reg.carpeta)
    assert e["duracion_s"] > 8                     # incluye lo grabado después de la caída del PC
    assert not (reg.carpeta / "en_curso.json").exists()


def test_corte_de_wifi_al_detener(tmp_path):
    sim, rx = preparar(tmp_path)
    try:
        reg = Registro(rx.lista(), tmp_path / "d", "P01", "rapida", 1)
        reg.iniciar()
        time.sleep(5)
        sim.cortar_red(6)                          # se corta el WiFi justo antes de detener
        time.sleep(1)
        meta = reg.detener()
        assert meta["pendientes"] == ["Simulador"]
        assert RG.leer_pendientes(tmp_path / "d")
        # vuelve la red: el procesador de pendientes cierra el registro y baja el respaldo
        for _ in range(40):
            time.sleep(0.5)
            msgs = RG.procesar_pendientes(tmp_path / "d", rx.lista())
            if not RG.leer_pendientes(tmp_path / "d"):
                break
    finally:
        rx.activo = False
        sim.detener()
    assert not RG.leer_pendientes(tmp_path / "d"), msgs
    meta, e, _ = revisar_csv(reg.carpeta)
    assert e["con_respaldo"] and not meta["pendientes"]


def test_reinicio_del_equipo(tmp_path):
    sim, rx = preparar(tmp_path)
    try:
        reg = Registro(rx.lista(), tmp_path / "d", "P01", "lenta", 1)
        reg.iniciar()
        time.sleep(7)
        sim.reiniciar()                             # el equipo se reinicia mientras graba
        time.sleep(6)
        meta = reg.detener()
    finally:
        rx.activo = False
        sim.detener()
    _, e, df = revisar_csv(reg.carpeta, reinicios=1)
    assert e["tramos_respaldo"] == 2
    assert (reg.carpeta / "respaldo_Simulador_cont1.bin").exists()
    assert set(df.columns) == {"t_s", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps"}
