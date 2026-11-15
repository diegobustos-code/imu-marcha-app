"""Simula el ESP32 con la IMU: envía una marcha sintética por UDP al propio computador.
Sirve para probar ver_imu.py sin el sensor.

Uso (en otra ventana, mientras corre ver_imu.py):
    python simulador_esp32.py --frecuencia 1.9 --segundos 60
"""
import argparse, socket, struct, time
import numpy as np

PUERTO, FS, POR_PAQUETE = 5005, 100, 10
FORMATO = "<I" + "I6h" * POR_PAQUETE
ESC_ACC, ESC_GIRO = 8192.0, 65.5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frecuencia", type=float, default=1.9, help="frecuencia de los pasos (Hz)")
    ap.add_argument("--segundos", type=float, default=60)
    ap.add_argument("--perdida", type=float, default=0.0, help="fracción de paquetes que se descartan (prueba)")
    ap.add_argument("--destino", default="127.0.0.1")
    a = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rng = np.random.default_rng(42)
    n_total = int(a.segundos * FS)
    inicio = time.perf_counter()
    for sec, k0 in enumerate(range(0, n_total, POR_PAQUETE)):
        campos = [sec]
        for k in range(k0, k0 + POR_PAQUETE):
            t = k / FS
            paso = 2 * np.pi * a.frecuencia * t
            ax = 1.0 + 0.25 * np.sin(paso) + 0.08 * np.sin(paso / 2) + 0.03 * rng.standard_normal()  # vertical
            ay = 0.10 * np.sin(paso / 2) + 0.02 * rng.standard_normal()                          # mediolateral
            az = 0.15 * np.sin(paso + 1.0) + 0.02 * rng.standard_normal()                        # anteroposterior
            gx, gy, gz = 20 * np.sin(paso / 2), 10 * np.sin(paso), 5 * np.sin(paso / 2 + 0.5)
            campos += [int(t * 1e6) & 0xFFFFFFFF,
                       *(int(round(v * ESC_ACC)) for v in (ax, ay, az)),
                       *(int(round(v * ESC_GIRO)) for v in (gx, gy, gz))]
        if rng.random() >= a.perdida:
            s.sendto(struct.pack(FORMATO, *campos), (a.destino, PUERTO))
        # mantener el ritmo real: un paquete cada 0,1 s
        espera = inicio + (k0 + POR_PAQUETE) / FS - time.perf_counter()
        if espera > 0:
            time.sleep(espera)
    print(f"Enviados {n_total // POR_PAQUETE} paquetes a {a.frecuencia} Hz.")


if __name__ == "__main__":
    main()
