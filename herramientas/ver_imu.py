"""Recibe los datos de la IMU (ESP32 + MPU-6050) por WiFi, los grafica en vivo
y los guarda en CSV. Calcula la frecuencia dominante de la marcha cada segundo.
 
Uso:
    python ver_imu.py --archivo caminata_01.csv --eje ax
    python ver_imu.py --sin-grafico --segundos 60     (solo texto)
 
Requiere: pip install numpy scipy matplotlib
"""
import argparse, collections, csv, socket, struct, threading, time
import numpy as np
from scipy import signal
 
PUERTO = 5005
FS = 100                      # Hz
POR_PAQUETE = 10
FORMATO = "<I" + "I6h" * POR_PAQUETE          # 4 + 10 × 16 = 164 bytes
ESC_ACC, ESC_GIRO = 8192.0, 65.5               # ±4 g y ±500 °/s
EJES = ["ax", "ay", "az", "gx", "gy", "gz"]
VENTANA_S = 10                                  # segundos que se grafican y analizan
 
def frecuencia_dominante(v, fs=FS):
    """Pico del espectro entre 0,5 y 3 Hz (mismo criterio que el proyecto)."""
    v = signal.detrend(v)
    f, p = signal.welch(v, fs=fs, nperseg=min(len(v), int(10 * fs)), nfft=8192)
    banda = (f >= 0.5) & (f <= 3.0)
    return f[banda][np.argmax(p[banda])]
 
class Receptor(threading.Thread):
    def __init__(self, archivo, eje):
        super().__init__(daemon=True)
        self.idx = EJES.index(eje)
        self.buf_t = collections.deque(maxlen=VENTANA_S * FS)
        self.buf_v = collections.deque(maxlen=VENTANA_S * FS)
        self.recibidos = self.perdidos = 0
        self.ultima_sec = None
        self.t0 = None; self.t_prev = None; self.vueltas = 0
        self.reinicios = 0; self.desfase = 0.0; self.t_ultimo = 0.0
        self.lock = threading.Lock()
        self.f = open(archivo, "w", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(["t_s", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps"])
 
    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", PUERTO))
        while True:
            datos, _ = s.recvfrom(1024)
            if len(datos) != struct.calcsize(FORMATO):
                continue
            campos = struct.unpack(FORMATO, datos)
            sec = campos[0]
            if self.ultima_sec is not None and sec < self.ultima_sec:
                # el ESP32 se reinició: el tiempo sigue desde el último valor
                self.reinicios += 1
                self.t0 = None; self.t_prev = None; self.vueltas = 0
                self.desfase = self.t_ultimo + 1.0 / FS
            elif self.ultima_sec is not None and sec > self.ultima_sec + 1:
                self.perdidos += sec - self.ultima_sec - 1
            self.ultima_sec = sec
            self.recibidos += 1
            with self.lock:
                for k in range(POR_PAQUETE):
                    t_us, *crudo = campos[1 + 7 * k: 8 + 7 * k]
                    if self.t_prev is not None and t_us < self.t_prev:
                        self.vueltas += 1          # el contador de µs se reinicia cada ~71 min
                    self.t_prev = t_us
                    t = (t_us + self.vueltas * 2**32) / 1e6
                    if self.t0 is None:
                        self.t0 = t
                    t = t - self.t0 + self.desfase
                    self.t_ultimo = t
                    fila = [c / ESC_ACC for c in crudo[:3]] + [c / ESC_GIRO for c in crudo[3:]]
                    self.w.writerow([f"{t:.4f}"] + [f"{x:.4f}" for x in fila])
                    self.buf_t.append(t)
                    self.buf_v.append(fila[self.idx])
 
    def ventana(self):
        with self.lock:
            return np.array(self.buf_t), np.array(self.buf_v)
 
    def resumen(self):
        t, v = self.ventana()
        if len(v) < 4 * FS:
            return None, t, v
        # Si se perdieron paquetes, la señal tiene huecos; se rellena en una grilla
        # uniforme de 100 Hz para que el espectro no se deforme.
        grilla = np.arange(t[0], t[-1], 1.0 / FS)
        return frecuencia_dominante(np.interp(grilla, t, v)), t, v
 
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archivo", default=time.strftime("imu_%Y%m%d_%H%M%S.csv"))
    ap.add_argument("--eje", default="ax", choices=EJES, help="eje vertical según cómo se fijó el sensor")
    ap.add_argument("--sin-grafico", action="store_true")
    ap.add_argument("--segundos", type=float, default=0, help="detenerse después de N s (0 = hasta Ctrl+C)")
    a = ap.parse_args()
 
    r = Receptor(a.archivo, a.eje); r.start()
    print(f"Escuchando en el puerto {PUERTO}. Guardando en {a.archivo}. Ctrl+C para terminar.")
    inicio = time.time()
    try:
        if a.sin_grafico:
            while not a.segundos or time.time() - inicio < a.segundos:
                time.sleep(1)
                fd, t, _ = r.resumen()
                txt = "esperando datos..." if fd is None else f"frecuencia dominante = {fd:.2f} Hz ({fd*60:.0f} pasos/min)"
                aviso = f", reinicios {r.reinicios}" if r.reinicios else ""
                print(f"paquetes {r.recibidos}, perdidos {r.perdidos}{aviso} | {txt}")
        else:
            import matplotlib.pyplot as plt
            from matplotlib.animation import FuncAnimation
            fig, (g1, g2) = plt.subplots(2, 1, figsize=(9, 6), constrained_layout=True)
            linea, = g1.plot([], [], lw=1)
            g1.set_xlabel("tiempo (s)"); g1.set_ylabel(f"{a.eje} (g)")
            espectro, = g2.plot([], [], lw=1)
            g2.set_xlim(0, 4); g2.set_xlabel("frecuencia (Hz)"); g2.set_ylabel("potencia")
            def actualizar(_):
                fd, t, v = r.resumen()
                if len(t):
                    linea.set_data(t, v); g1.set_xlim(t[0], max(t[-1], t[0] + 1))
                    g1.set_ylim(v.min() - 0.1, v.max() + 0.1)
                if fd is not None:
                    v = np.interp(np.arange(t[0], t[-1], 1.0 / FS), t, v)   # rellena huecos
                    f, p = signal.welch(signal.detrend(v), fs=FS, nperseg=min(len(v), 10 * FS), nfft=2048)
                    espectro.set_data(f, p); g2.set_ylim(0, p[(f > 0.5) & (f < 4)].max() * 1.1)
                    g2.set_title(f"Frecuencia dominante: {fd:.2f} Hz ({fd*60:.0f} pasos/min)  |  "
                                 f"paquetes perdidos: {r.perdidos} de {r.recibidos + r.perdidos}")
                return linea, espectro
            anim = FuncAnimation(fig, actualizar, interval=500, cache_frame_data=False)
            plt.show()
    except KeyboardInterrupt:
        pass
    finally:
        r.f.flush(); r.f.close()
        print(f"Archivo guardado: {a.archivo} ({r.recibidos} paquetes, {r.perdidos} perdidos)")
 
if __name__ == "__main__":
    main()
