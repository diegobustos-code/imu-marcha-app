"""Formato de los paquetes, idéntico al firmware, a la app Android y a ver_imu.py.

uint32 secuencia + 10 muestras de (uint32 t_us, int16 ax, ay, az, gx, gy, gz), little-endian.
Los respaldos (.bin) de los equipos son los mismos paquetes de 164 bytes, uno tras otro.
"""
import struct
from pathlib import Path

import numpy as np
import pandas as pd

PUERTO_DATOS = 5005
PUERTO_CONTROL = 8080
FS = 100
POR_PAQUETE = 10
FORMATO = "<I" + "I6h" * POR_PAQUETE
BYTES = struct.calcsize(FORMATO)          # 164
ESC_ACC = 8192.0                          # cuentas por g
ESC_GIRO = 65.5                           # cuentas por °/s
COLUMNAS = ["t_s", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps"]


def decodificar(datos: bytes):
    """Devuelve (secuencia, matriz 10x7 con t_us y los 6 valores crudos) o None."""
    if len(datos) != BYTES:
        return None
    c = struct.unpack(FORMATO, datos)
    m = np.array(c[1:], dtype=np.int64).reshape(POR_PAQUETE, 7)
    return c[0], m


def codificar(secuencia: int, muestras) -> bytes:
    plano = []
    for fila in muestras:
        plano.extend(int(x) for x in fila)
    return struct.pack(FORMATO, secuencia & 0xFFFFFFFF, *plano)


class Reloj:
    """Convierte t_us (contador de 32 bits del equipo) en tiempo continuo en segundos,
    con el mismo manejo de vueltas y reinicios que ver_imu.py."""

    def __init__(self):
        self.t0 = None
        self.t_prev = None
        self.vueltas = 0
        self.desfase = 0.0
        self.ultimo = 0.0

    def reinicio(self):
        self.t0 = None
        self.t_prev = None
        self.vueltas = 0
        self.desfase = self.ultimo + 1.0 / FS

    def tiempo(self, t_us: int) -> float:
        if self.t_prev is not None and t_us < self.t_prev:
            self.vueltas += 1
        self.t_prev = t_us
        t = (t_us + self.vueltas * 2 ** 32) / 1e6
        if self.t0 is None:
            self.t0 = t
        t = t - self.t0 + self.desfase
        self.ultimo = t
        return t


def a_unidades(crudo: np.ndarray) -> np.ndarray:
    """Columnas 1..6 en cuentas -> g y °/s."""
    out = np.empty((crudo.shape[0], 6))
    out[:, :3] = crudo[:, 1:4] / ESC_ACC
    out[:, 3:] = crudo[:, 4:7] / ESC_GIRO
    return out


def paquetes_a_tabla(paquetes):
    """paquetes: lista de (secuencia, matriz). Ordena por secuencia, quita duplicados y arma la tabla
    con el tiempo continuo. Devuelve (DataFrame con COLUMNAS + 'secuencia', n_reinicios)."""
    # Un reinicio del equipo hace que la secuencia vuelva a 0: se separan los tramos.
    tramos, actual, ult = [], [], None
    for sec, m in paquetes:
        if ult is not None and sec < ult and ult - sec > 50:
            tramos.append(actual)
            actual = []
        actual.append((sec, m))
        ult = sec
    tramos.append(actual)
    reloj = Reloj()
    filas, secs = [], []
    for k, tramo in enumerate(tramos):
        if k > 0:
            reloj.reinicio()
        vistos = {}
        for sec, m in tramo:
            vistos.setdefault(sec, m)
        for sec in sorted(vistos):
            m = vistos[sec]
            for fila in m:
                t = reloj.tiempo(int(fila[0]))
                filas.append(np.concatenate([[t], a_unidades(fila[None, :])[0]]))
                secs.append(sec)
    if not filas:
        return pd.DataFrame(columns=COLUMNAS + ["secuencia"]), 0
    df = pd.DataFrame(np.array(filas), columns=COLUMNAS)
    df["secuencia"] = secs
    return df, len(tramos) - 1


def leer_bin(ruta):
    """Lee un respaldo .bin del equipo y devuelve la lista de paquetes."""
    datos = Path(ruta).read_bytes()
    n = len(datos) // BYTES
    paquetes = []
    for i in range(n):
        d = decodificar(datos[i * BYTES:(i + 1) * BYTES])
        if d is not None:
            paquetes.append(d)
    return paquetes


def guardar_csv(df: pd.DataFrame, ruta):
    """CSV con las mismas columnas que ver_imu.py."""
    df[COLUMNAS].to_csv(ruta, index=False, float_format="%.4f")
