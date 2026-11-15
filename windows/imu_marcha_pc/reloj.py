"""Sincronización de relojes entre cada equipo y el PC.

Cada paquete trae el tiempo del equipo (t_dev) y el PC anota cuándo llegó (t_pc). La diferencia
t_pc − t_dev es el desfase entre relojes más el retardo de la red, que siempre es positivo y
variable. El retardo mínimo es casi constante, así que se toma el mínimo de la diferencia en
ventanas de 2 s (la "envolvente inferior") y se ajusta una recta: el corte es el desfase y la
pendiente es la deriva del cristal del equipo (unas decenas de ppm).

Con eso, cualquier muestra del equipo se lleva al reloj del PC: t_pc = desfase + (1 + deriva)·t_dev,
y dos equipos distintos quedan alineados entre sí sin depender de los saltos.
"""
import collections

import numpy as np

VENTANA_BIN_S = 2.0
MIN_SPAN_S = 8.0
MIN_BINS = 4
GUARDA_S = 120.0
UMBRAL_BUENO_MS = 10.0
UMBRAL_ACEPTABLE_MS = 25.0


def ajustar(t_dev, t_pc):
    """Ajuste de la envolvente inferior. Devuelve dict o None si faltan datos."""
    t_dev = np.asarray(t_dev, float)
    t_pc = np.asarray(t_pc, float)
    if len(t_dev) < 10 or t_dev[-1] - t_dev[0] < MIN_SPAN_S:
        return None
    ref = t_pc[0] - t_dev[0]                   # para trabajar con números pequeños
    d = t_pc - t_dev - ref
    bins = np.floor((t_dev - t_dev[0]) / VENTANA_BIN_S).astype(int)
    xs, ys = [], []
    for b in np.unique(bins):
        m = bins == b
        k = np.argmin(d[m])
        xs.append(t_dev[m][k]); ys.append(d[m][k])
    if len(xs) < MIN_BINS:
        return None
    xs, ys = np.array(xs), np.array(ys)
    pend, corte = np.polyfit(xs, ys, 1)
    resid = ys - (corte + pend * xs)
    # se descartan ventanas con un retardo mínimo anormal (congestión) y se reajusta
    bueno = resid <= np.median(resid) + 3 * (np.median(np.abs(resid - np.median(resid))) + 1e-4)
    if bueno.sum() >= MIN_BINS:
        pend, corte = np.polyfit(xs[bueno], ys[bueno], 1)
        resid = ys[bueno] - (corte + pend * xs[bueno])
    incert_ms = float(max(np.abs(resid).max(), 0.5e-3) * 1000)
    return {"desfase_s": float(ref + corte), "deriva_ppm": float(pend * 1e6),
            "incertidumbre_ms": incert_ms, "ventanas": int(len(xs)), "duracion_s": float(t_dev[-1] - t_dev[0])}


def estado_texto(r):
    if r is None:
        return "sincronizando…", "pendiente"
    nivel = "bueno" if r["incertidumbre_ms"] <= UMBRAL_BUENO_MS else (
        "aceptable" if r["incertidumbre_ms"] <= UMBRAL_ACEPTABLE_MS else "malo")
    return f"sincronizado ±{max(1, round(r['incertidumbre_ms']))} ms", nivel


class Sincronizador:
    """Va juntando pares (t_dev, t_pc) de los últimos 2 minutos para el estado en vivo."""

    def __init__(self):
        self.pares = collections.deque()

    def reiniciar(self):
        self.pares.clear()

    def agregar(self, t_dev, t_pc):
        self.pares.append((t_dev, t_pc))
        while self.pares and t_dev - self.pares[0][0] > GUARDA_S:
            self.pares.popleft()

    def estimar(self):
        if len(self.pares) < 10:
            return None
        a = np.array(self.pares)
        return ajustar(a[:, 0], a[:, 1])
