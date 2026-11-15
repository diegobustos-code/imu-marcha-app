"""Simulador de un equipo completo (como el firmware del Stick): envía una marcha sintética por UDP,
responde al control HTTP en el puerto 8080 y guarda un respaldo .bin mientras graba.

Sirve para probar la app de Windows sin hardware. También se puede ejecutar solo:
    python -m imu_marcha_pc.simulador --frecuencia 1.9 --perdida 0.05
"""
import argparse
import json
import socket
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from . import protocolo as P


class SimuladorEquipo:
    def __init__(self, frecuencia=1.9, perdida=0.0, destino="127.0.0.1", saltos=True,
                 carpeta=None, puerto_control=P.PUERTO_CONTROL):
        self.frecuencia = frecuencia
        self.perdida = perdida
        self.destino = destino
        self.saltos = saltos
        self.carpeta = Path(carpeta or tempfile.mkdtemp(prefix="sim_imu_"))
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.puerto_control = puerto_control
        self.activo = False
        self.grabando = False
        self.sesion = ""
        self.archivo = None
        self.bytes = 0
        self.secuencia = 0
        self.inicio_grab = 0.0
        self.lock = threading.Lock()
        self.rng = np.random.default_rng(42)
        self.servidor = None

    # ---------------------------------------------------------------- señal
    def muestra(self, k):
        t = k / P.FS
        ph = 2 * np.pi * self.frecuencia * t
        ax = 1.0 + 0.25 * np.sin(ph) + 0.08 * np.sin(ph / 2) + 0.03 * self.rng.standard_normal()
        if self.saltos and 3.0 <= t < 6.0:
            # tres saltos pequeños al inicio, como marca de sincronización
            fase = (t - 3.0) % 1.0
            ax = 1.0 + (1.8 if fase < 0.05 else (-0.9 if 0.2 < fase < 0.45 else 0.0))
        ay = 0.10 * np.sin(ph / 2) + 0.02 * self.rng.standard_normal()
        az = 0.15 * np.sin(ph + 1.0) + 0.02 * self.rng.standard_normal()
        g = (20 * np.sin(ph / 2), 10 * np.sin(ph), 5 * np.sin(ph / 2 + 0.5))
        return [int(t * 1e6) & 0xFFFFFFFF,
                *(int(round(v * P.ESC_ACC)) for v in (ax, ay, az)),
                *(int(round(v * P.ESC_GIRO)) for v in g)]

    def bucle(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        k = 0
        t0 = time.perf_counter()
        while self.activo:
            lote = [self.muestra(k + i) for i in range(P.POR_PAQUETE)]
            datos = P.codificar(self.secuencia, lote)
            self.secuencia += 1
            k += P.POR_PAQUETE
            with self.lock:
                if self.grabando:
                    self.archivo.write(datos)
                    self.bytes += len(datos)
            if self.rng.random() >= self.perdida:
                try:
                    s.sendto(datos, (self.destino, P.PUERTO_DATOS))
                except OSError:
                    pass
            espera = t0 + k / P.FS - time.perf_counter()
            if espera > 0:
                time.sleep(espera)
        s.close()

    # ---------------------------------------------------------------- control
    def estado(self):
        return {"equipo": "Simulador", "version": "1.0", "mac": "00:00:00:00:00:00",
                "grabando": self.grabando, "sesion": self.sesion,
                "segundos": round(time.time() - self.inicio_grab, 1) if self.grabando else 0.0,
                "bytes": self.bytes, "secuencia": self.secuencia, "descartados": 0,
                "bateria": 100, "libre_kb": 100000, "fs_hz": 100}

    def iniciar_grabacion(self, sesion):
        with self.lock:
            if self.grabando:
                return
            self.sesion = "".join(c for c in sesion if c.isalnum() or c in "_-")[:40] or "sesion"
            self.archivo = open(self.carpeta / f"{self.sesion}.bin", "wb")
            self.bytes = 0
            self.inicio_grab = time.time()
            self.grabando = True

    def detener_grabacion(self):
        with self.lock:
            if self.grabando:
                self.grabando = False
                self.archivo.close()

    def manejador(self):
        sim = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def enviar(self, codigo, cuerpo, tipo="application/json"):
                b = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode()
                self.send_response(codigo)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                if u.path == "/estado":
                    self.enviar(200, sim.estado())
                elif u.path == "/inicio":
                    sim.iniciar_grabacion(q.get("sesion", ""))
                    self.enviar(200, sim.estado())
                elif u.path == "/fin":
                    sim.detener_grabacion()
                    self.enviar(200, sim.estado())
                elif u.path == "/archivos":
                    self.enviar(200, [{"nombre": f.name, "bytes": f.stat().st_size} for f in sim.carpeta.glob("*.bin")])
                elif u.path == "/archivo":
                    f = sim.carpeta / Path(q.get("n", "")).name
                    if not f.is_file():
                        self.enviar(404, {"error": "no existe"})
                    else:
                        self.enviar(200, f.read_bytes(), "application/octet-stream")
                elif u.path == "/borrar":
                    f = sim.carpeta / Path(q.get("n", "")).name
                    ok = f.is_file()
                    if ok:
                        f.unlink()
                    self.enviar(200 if ok else 404, {"ok": ok})
                else:
                    self.enviar(404, {"error": "ruta desconocida"})
        return H

    def iniciar(self):
        self.activo = True
        self.servidor = ThreadingHTTPServer(("127.0.0.1", self.puerto_control), self.manejador())
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()
        threading.Thread(target=self.bucle, daemon=True).start()
        return self

    def detener(self):
        self.activo = False
        self.detener_grabacion()
        if self.servidor:
            self.servidor.shutdown()
            self.servidor.server_close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frecuencia", type=float, default=1.9)
    ap.add_argument("--perdida", type=float, default=0.0)
    ap.add_argument("--destino", default="127.0.0.1")
    a = ap.parse_args()
    sim = SimuladorEquipo(a.frecuencia, a.perdida, a.destino).iniciar()
    print(f"Simulador enviando a {a.destino}:{P.PUERTO_DATOS}, control en http://127.0.0.1:{P.PUERTO_CONTROL}. Ctrl+C para salir.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sim.detener()


if __name__ == "__main__":
    main()
