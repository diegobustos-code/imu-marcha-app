"""Recepción de los equipos, control remoto y guardado de sesiones.

Cada equipo se identifica por la dirección IP desde la que llegan sus paquetes, así que el PC
puede recibir a la vez del celular y de cualquier IMU (M5StickS3, M5StickC Plus2 o ESP32 + MPU-6050).
"""
import collections
import json
import socket
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import protocolo as P
from . import reloj as R

# Condiciones para considerar a un equipo "listo para grabar"
LISTO_HZ = (95, 105)
LISTO_PERDIDA_PCT = 1.0
LISTO_BATERIA_PCT = 20
LISTO_MEMORIA_KB = 1000          # ~10 minutos de respaldo


# ---------------------------------------------------------------- control HTTP
def pedir(ip, ruta, timeout=2.0, binario=False):
    url = f"http://{ip}:{P.PUERTO_CONTROL}{ruta}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        datos = r.read()
    return datos if binario else json.loads(datos.decode("utf-8"))


class Equipo:
    """Estado de un equipo que está enviando datos."""

    VENTANA = 10 * P.FS

    def __init__(self, ip):
        self.ip = ip
        self.nombre = f"Equipo {ip}"
        self.mac = ""
        self.control = None          # None = sin consultar, True/False = responde o no a HTTP
        self.estado = {}
        self.recibidos = 0
        self.perdidos = 0
        self.reinicios = 0
        self.ultima_sec = None
        self.ultimo_paquete = 0.0
        self.reloj = P.Reloj()
        self.buf_t = collections.deque(maxlen=self.VENTANA)
        self.buf_v = collections.deque(maxlen=self.VENTANA)
        self.lock = threading.Lock()
        self.sinc = R.Sincronizador()
        self.recientes = collections.deque()   # (t_pc, secuencia) de los últimos 10 s
        self._sinc_cache = (0.0, None)
        # registro en curso
        self.grabando = False
        self.paquetes = []           # (secuencia, matriz, t_pc)
        self.rec_recibidos = 0
        self.rec_perdidos = 0

    def recibir(self, sec, m, t_pc):
        with self.lock:
            if self.ultima_sec is not None and sec < self.ultima_sec and self.ultima_sec - sec > 50:
                self.reinicios += 1
                self.reloj.reinicio()
                self.sinc.reiniciar()
                self.recientes.clear()
            elif self.ultima_sec is not None and sec > self.ultima_sec + 1:
                self.perdidos += sec - self.ultima_sec - 1
                if self.grabando:
                    self.rec_perdidos += sec - self.ultima_sec - 1
            self.ultima_sec = sec
            self.recibidos += 1
            self.ultimo_paquete = t_pc
            vals = P.a_unidades(m)
            for k in range(P.POR_PAQUETE):
                self.buf_t.append(self.reloj.tiempo(int(m[k, 0])))
                self.buf_v.append(vals[k])
            self.sinc.agregar(self.buf_t[-1], t_pc)       # última muestra del paquete
            self.recientes.append((t_pc, sec))
            while self.recientes and t_pc - self.recientes[0][0] > 10:
                self.recientes.popleft()
            if self.grabando:
                self.paquetes.append((sec, m.copy(), t_pc))
                self.rec_recibidos += 1

    def ventana(self):
        with self.lock:
            t = np.array(self.buf_t)
            v = np.array(self.buf_v) if self.buf_v else np.zeros((0, 6))
        return t, v

    def activo(self):
        return time.time() - self.ultimo_paquete < 2.0

    def sincronia(self):
        """Estimación del reloj (se recalcula como máximo una vez por segundo)."""
        ahora = time.time()
        if ahora - self._sinc_cache[0] > 1.0:
            with self.lock:
                r = self.sinc.estimar()
            self._sinc_cache = (ahora, r)
        return self._sinc_cache[1]

    def ritmo(self):
        """(muestras por segundo, % de paquetes perdidos) en los últimos 10 s."""
        with self.lock:
            rec = list(self.recientes)
        if len(rec) < 2:
            return 0.0, 0.0
        dur = rec[-1][0] - rec[0][0]
        hz = (len(rec) - 1) * P.POR_PAQUETE / dur if dur > 0 else 0.0
        esperados = rec[-1][1] - rec[0][1] + 1
        perd = 100.0 * max(esperados - len(rec), 0) / esperados if esperados > 0 else 0.0
        return hz, perd

    def listo(self):
        """(listo, lista de motivos) para empezar un registro."""
        motivos = []
        if not self.activo():
            return False, ["no llegan datos"]
        if self.control is False:
            motivos.append("sin control remoto: no guardará respaldo")
        elif self.control is None:
            motivos.append("identificando el equipo…")
        hz, perd = self.ritmo()
        if len(self.recientes) >= 20:
            if not (LISTO_HZ[0] <= hz <= LISTO_HZ[1]):
                motivos.append(f"envía {hz:.0f} muestras/s (debe ser 100)")
            if perd > LISTO_PERDIDA_PCT:
                motivos.append(f"pierde {perd:.1f} % de los paquetes")
        else:
            motivos.append("midiendo el ritmo…")
        bat = self.estado.get("bateria", -1) if self.estado else -1
        if bat is not None and 0 <= bat < LISTO_BATERIA_PCT:
            motivos.append(f"batería baja ({bat} %)")
        libre = self.estado.get("libre_kb") if self.estado else None
        if libre is not None and libre < LISTO_MEMORIA_KB:
            motivos.append(f"poca memoria para el respaldo ({libre} kB ≈ {libre / 98:.0f} min)")
        _, nivel = R.estado_texto(self.sincronia())
        if nivel == "pendiente":
            motivos.append("reloj aún sin sincronizar")
        elif nivel == "malo":
            motivos.append("sincronización imprecisa (red con mucho retardo)")
        return len(motivos) == 0, motivos


class Receptor(threading.Thread):
    """Escucha el puerto 5005 y reparte los paquetes por equipo."""

    def __init__(self):
        super().__init__(daemon=True)
        self.equipos = {}
        self.lock = threading.Lock()
        self.error = None
        self.activo = True

    def run(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", P.PUERTO_DATOS))
            s.settimeout(0.5)
        except OSError as e:
            self.error = f"No se pudo abrir el puerto {P.PUERTO_DATOS}: {e}"
            return
        while self.activo:
            try:
                datos, (ip, _) = s.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                continue
            d = P.decodificar(datos)
            if d is None:
                continue
            with self.lock:
                eq = self.equipos.get(ip)
                if eq is None:
                    eq = self.equipos[ip] = Equipo(ip)
                    threading.Thread(target=self.identificar, args=(eq,), daemon=True).start()
            eq.recibir(d[0], d[1], time.time())
        s.close()

    def identificar(self, eq):
        try:
            est = pedir(eq.ip, "/estado")
            eq.estado = est
            eq.nombre = est.get("equipo", eq.nombre)
            eq.mac = est.get("mac", "")
            eq.control = True
        except Exception:
            eq.control = False
            if eq.ip.startswith("127."):
                eq.nombre = "Simulador (este PC)"
            else:
                eq.nombre = f"IMU sin control ({eq.ip})"

    def lista(self):
        with self.lock:
            return list(self.equipos.values())


# ---------------------------------------------------------------- sesiones
def limpiar(txt):
    return "".join(c for c in txt if c.isalnum() or c in "_-")[:40]


def nombre_sesion(participante, condicion, repeticion):
    return limpiar(f"{participante}_{condicion}_{repeticion}")


def carpeta_sesion(base, participante, sesion):
    return Path(base) / limpiar(participante) / sesion


def etiqueta_archivo(eq):
    return limpiar(eq.nombre.replace(" ", "_").replace("+", "")) or eq.ip.replace(".", "_")


class Registro:
    """Inicia, detiene y guarda una sesión con todos los equipos elegidos."""

    def __init__(self, equipos, base, participante, condicion, repeticion, notas=""):
        self.equipos = equipos
        self.base = Path(base)
        self.participante = limpiar(participante)
        self.condicion = condicion
        self.repeticion = repeticion
        self.notas = notas
        self.sesion = nombre_sesion(participante, condicion, repeticion)
        self.carpeta = carpeta_sesion(base, participante, self.sesion)
        self.inicio = None
        self.mensajes = []

    def iniciar(self):
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.inicio = datetime.now()
        for eq in self.equipos:
            with eq.lock:
                eq.paquetes = []
                eq.rec_recibidos = eq.rec_perdidos = 0
                eq.grabando = True
            if eq.control:
                try:
                    pedir(eq.ip, "/inicio?sesion=" + urllib.parse.quote(self.sesion))
                except Exception as e:
                    self.mensajes.append(f"{eq.nombre}: no respondió a /inicio ({e}); se guarda solo lo recibido.")

    def detener(self, progreso=lambda txt: None):
        fin = datetime.now()
        resumen = []
        for eq in self.equipos:
            with eq.lock:
                eq.grabando = False
                vivos = list(eq.paquetes)
            if eq.control:
                try:
                    pedir(eq.ip, "/fin")
                except Exception as e:
                    self.mensajes.append(f"{eq.nombre}: no respondió a /fin ({e}).")
            etiqueta = etiqueta_archivo(eq)
            respaldo = []
            if eq.control:
                progreso(f"Descargando respaldo de {eq.nombre}…")
                try:
                    datos = pedir(eq.ip, "/archivo?n=" + urllib.parse.quote(self.sesion + ".bin"),
                                  timeout=60, binario=True)
                    (self.carpeta / f"respaldo_{etiqueta}.bin").write_bytes(datos)
                    respaldo = P.leer_bin(self.carpeta / f"respaldo_{etiqueta}.bin")
                except Exception as e:
                    self.mensajes.append(f"{eq.nombre}: no se pudo descargar el respaldo ({e}).")
            info = self.guardar_equipo(eq, etiqueta, vivos, respaldo)
            resumen.append(info)
        meta = {
            "sesion": self.sesion, "participante": self.participante, "condicion": self.condicion,
            "repeticion": self.repeticion, "notas": self.notas,
            "inicio": self.inicio.isoformat(timespec="seconds"), "fin": fin.isoformat(timespec="seconds"),
            "duracion_s": round((fin - self.inicio).total_seconds(), 1),
            "equipos": resumen, "mensajes": self.mensajes, "fs_hz": P.FS,
        }
        (self.carpeta / "sesion.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return meta

    def guardar_equipo(self, eq, etiqueta, vivos, respaldo):
        seq_vivos = {s for s, _, _ in vivos}
        combinados = {}
        for s, m in respaldo:
            combinados[s] = m
        for s, m, _ in vivos:
            combinados.setdefault(s, m)
        recuperados = len(set(combinados) - seq_vivos) if respaldo else 0
        orden = sorted(combinados.items())
        if respaldo:
            # se usa el tramo del respaldo (es el que el equipo grabó entre /inicio y /fin)
            lo, hi = min(s for s, _ in respaldo), max(s for s, _ in respaldo)
            orden = [(s, m) for s, m in orden if lo <= s <= hi] or orden
        df, reinicios = P.paquetes_a_tabla(orden)
        if len(df):
            P.guardar_csv(df, self.carpeta / f"imu_{etiqueta}.csv")
        reloj = None
        if vivos and len(df):
            # tiempo del equipo de la última muestra de cada paquete recibido en vivo
            ult = df.groupby("secuencia")["t_s"].max()
            pares = [(ult[s], t) for s, _, t in vivos if s in ult.index]
            if pares:
                a = np.array(pares)
                reloj = R.ajustar(a[:, 0], a[:, 1])
            if reloj:
                reloj["inicio_pc"] = reloj["desfase_s"]          # hora del PC (epoch) en t_s = 0
                reloj["inicio_pc_hora"] = datetime.fromtimestamp(reloj["desfase_s"]).isoformat(timespec="milliseconds")
                reloj["formula"] = "t_pc = inicio_pc + t_s * (1 + deriva_ppm / 1e6)"
        if vivos:
            pd.DataFrame({"secuencia": [s for s, _, _ in vivos],
                          "t_pc_s": [round(t - vivos[0][2], 4) for _, _, t in vivos],
                          "hora_pc": [datetime.fromtimestamp(t).isoformat(timespec="milliseconds") for _, _, t in vivos]}
                         ).to_csv(self.carpeta / f"paquetes_{etiqueta}.csv", index=False)
        if orden:
            esperados = orden[-1][0] - orden[0][0] + 1 if not reinicios else len(orden)
            faltantes = max(esperados - len(orden), 0)
        else:
            esperados = faltantes = 0
        return {
            "equipo": eq.nombre, "ip": eq.ip, "mac": eq.mac, "archivo": f"imu_{etiqueta}.csv",
            "paquetes_en_vivo": len(vivos), "perdidos_en_vivo": eq.rec_perdidos,
            "recuperados_del_respaldo": recuperados, "faltantes_final": faltantes,
            "muestras": int(len(df)), "duracion_s": round(float(df["t_s"].iloc[-1]), 2) if len(df) else 0.0,
            "reinicios": reinicios, "con_respaldo": bool(respaldo), "reloj": reloj,
        }
