"""Recepción de los equipos, control remoto y guardado de sesiones.

Cada equipo se identifica por la dirección IP desde la que llegan sus paquetes, así que el PC
puede recibir a la vez del celular y de cualquier IMU (M5StickS3, M5StickC Plus2 o ESP32 + MPU-6050).
"""
import collections
import json
import os
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


def pedir_reintentando(ip, ruta, intentos=3, espera=1.0, **kw):
    """Igual que pedir(), pero reintenta si la red falla (WiFi intermitente)."""
    ultimo = None
    for k in range(intentos):
        try:
            return pedir(ip, ruta, **kw)
        except Exception as e:          # noqa: BLE001
            ultimo = e
            if k < intentos - 1:
                time.sleep(espera)
    raise ultimo


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
        self.diario = None           # archivo vivo_<equipo>.dat de la sesión
        self._ult_flush = 0.0
        self.rec_recibidos = 0
        self.rec_perdidos = 0
        self.rec_reinicios = 0

    def recibir(self, sec, m, t_pc, datos=None):
        with self.lock:
            if self.ultima_sec is not None and sec < self.ultima_sec and self.ultima_sec - sec > 50:
                self.reinicios += 1
                if self.grabando:
                    self.rec_reinicios += 1
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
                self.rec_recibidos += 1
                if self.diario is not None:
                    try:
                        self.diario.write(P.registro_diario(t_pc, datos if datos is not None else P.codificar(sec, m)))
                        if t_pc - self._ult_flush > 1.0:          # al disco al menos una vez por segundo
                            self.diario.flush()
                            os.fsync(self.diario.fileno())
                            self._ult_flush = t_pc
                    except OSError:
                        pass

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
            eq.recibir(d[0], d[1], time.time(), datos)
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


def escribir_json(ruta, datos):
    """Escritura atómica: primero a un temporal y después se reemplaza (un corte no deja el archivo a medias)."""
    ruta = Path(ruta)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(datos, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, ruta)


def leer_json(ruta, defecto=None):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return defecto


# ---------------------------------------------------------------- respaldo del equipo
def archivos_de_sesion(lista, sesion):
    """Del listado /archivos, los de esta sesión en orden: X.bin, X_cont1.bin, X_cont2.bin…"""
    nombres = {f["nombre"].lstrip("/"): f["bytes"] for f in lista}
    orden = []
    if f"{sesion}.bin" in nombres:
        orden.append(f"{sesion}.bin")
    k = 1
    while f"{sesion}_cont{k}.bin" in nombres:
        orden.append(f"{sesion}_cont{k}.bin")
        k += 1
    return [(n, nombres[n]) for n in orden]


def descargar_respaldo(ip, sesion, carpeta, etiqueta, intentos=3):
    """Descarga todos los archivos de respaldo de la sesión (el principal y los de continuación después
    de un reinicio), comprueba su tamaño y devuelve una lista de tramos de paquetes."""
    lista = pedir_reintentando(ip, "/archivos", intentos=intentos, timeout=5)
    archivos = archivos_de_sesion(lista, sesion)
    if not archivos:
        raise FileNotFoundError(f"el equipo no tiene el respaldo {sesion}.bin")
    tramos = []
    for k, (nombre, tam) in enumerate(archivos):
        destino = carpeta / (f"respaldo_{etiqueta}.bin" if k == 0 else f"respaldo_{etiqueta}_cont{k}.bin")
        for intento in range(intentos):
            try:
                datos = pedir(ip, "/archivo?n=" + urllib.parse.quote(nombre), timeout=60, binario=True)
                if len(datos) != tam:
                    raise IOError(f"descarga incompleta de {nombre} ({len(datos)} de {tam} bytes)")
                break
            except Exception:  # noqa: BLE001
                if intento == intentos - 1:
                    raise
                time.sleep(1.5)
        tmp = destino.with_suffix(".tmp")
        tmp.write_bytes(datos)
        os.replace(tmp, destino)
        tramos.append(P.leer_bin(destino))
    return tramos


def respaldo_local(carpeta, etiqueta):
    """Tramos del respaldo ya descargado antes (si existe)."""
    tramos = []
    base = carpeta / f"respaldo_{etiqueta}.bin"
    if base.exists():
        tramos.append(P.leer_bin(base))
        k = 1
        while (carpeta / f"respaldo_{etiqueta}_cont{k}.bin").exists():
            tramos.append(P.leer_bin(carpeta / f"respaldo_{etiqueta}_cont{k}.bin"))
            k += 1
    return tramos


# ---------------------------------------------------------------- armado de los archivos de un equipo
def armar_equipo(carpeta, info, vivos, tramos_resp):
    """Une lo recibido en vivo con el respaldo, tramo por tramo (un tramo por cada encendido del equipo),
    y escribe imu_<equipo>.csv y paquetes_<equipo>.csv. Devuelve el resumen para sesion.json."""
    etiqueta = info["etiqueta"]
    tramos_vivo = P.separar_tramos(vivos)
    n = max(len(tramos_vivo), len(tramos_resp))
    tramos_finales, recuperados, faltantes, perdidos_vivo = [], 0, 0, 0
    for k in range(n):
        tv = tramos_vivo[k] if k < len(tramos_vivo) else []
        tr = tramos_resp[k] if k < len(tramos_resp) else []
        sv = {s for s, _, _ in tv}
        if tv:
            perdidos_vivo += max(max(sv) - min(sv) + 1 - len(sv), 0)
        comb = {}
        for s_, m in tr:
            comb.setdefault(s_, m)
        for s_, m, _ in tv:
            comb.setdefault(s_, m)
        if tr:
            lo, hi = min(s_ for s_, _ in tr), max(s_ for s_, _ in tr)
            if tv:      # lo recibido en vivo justo antes de /inicio o después de /fin también sirve
                lo, hi = min(lo, min(sv)), max(hi, max(sv))
            recuperados += len(set(comb) - sv)
        orden = sorted(comb.items())
        if orden:
            faltantes += orden[-1][0] - orden[0][0] + 1 - len(orden)
        tramos_finales.append(orden)
    df, reinicios = P.paquetes_a_tabla(None, tramos=tramos_finales)
    if len(df):
        P.guardar_csv(df, carpeta / f"imu_{etiqueta}.csv")
    reloj, relojes = None, []
    if vivos and len(df):
        for k, tv in enumerate(tramos_vivo):
            sub = df[df["tramo"] == k]
            ult = sub.groupby("secuencia")["t_s"].max()
            pares = [(ult[s_], t) for s_, _, t in tv if s_ in ult.index]
            r = R.ajustar(*np.array(pares).T) if len(pares) >= 10 else None
            if r:
                # hora del PC (epoch) en t_s = 0 de este tramo
                r["inicio_pc"] = r["desfase_s"]
                r["inicio_pc_hora"] = datetime.fromtimestamp(r["desfase_s"]).isoformat(timespec="milliseconds")
                r["formula"] = "t_pc = inicio_pc + t_s * (1 + deriva_ppm / 1e6)"
                r["tramo"] = k
            relojes.append(r)
        reloj = relojes[0] if relojes else None
        pd.DataFrame({"secuencia": [s_ for s_, _, _ in vivos],
                      "t_pc_s": [round(t - vivos[0][2], 4) for _, _, t in vivos],
                      "hora_pc": [datetime.fromtimestamp(t).isoformat(timespec="milliseconds") for _, _, t in vivos]}
                     ).to_csv(carpeta / f"paquetes_{etiqueta}.csv", index=False)
    return {
        "equipo": info["nombre"], "ip": info["ip"], "mac": info.get("mac", ""), "etiqueta": etiqueta,
        "archivo": f"imu_{etiqueta}.csv", "paquetes_en_vivo": len(vivos), "perdidos_en_vivo": perdidos_vivo,
        "recuperados_del_respaldo": recuperados, "faltantes_final": max(faltantes, 0),
        "muestras": int(len(df)), "duracion_s": round(float(df["t_s"].iloc[-1]), 2) if len(df) else 0.0,
        "reinicios": reinicios, "con_respaldo": bool(tramos_resp), "tramos_respaldo": len(tramos_resp),
        "reloj": reloj, "reloj_por_tramo": relojes if len(relojes) > 1 else None,
    }


# ---------------------------------------------------------------- respaldos pendientes
def ruta_pendientes(base):
    return Path(base) / "pendientes.json"


def leer_pendientes(base):
    return leer_json(ruta_pendientes(base), []) or []


def guardar_pendientes(base, lista):
    Path(base).mkdir(parents=True, exist_ok=True)
    escribir_json(ruta_pendientes(base), lista)


def agregar_pendiente(base, item):
    lista = [p for p in leer_pendientes(base) if not (p["carpeta"] == item["carpeta"] and p["etiqueta"] == item["etiqueta"])]
    lista.append(item)
    guardar_pendientes(base, lista)


def cerrar_y_bajar(info, sesion, carpeta, mensajes, intentos=3):
    """Envía /fin si el equipo sigue grabando esta sesión y descarga el respaldo.
    Devuelve (tramos_respaldo, fin_ok, descarga_ok)."""
    ip = info["ip"]
    fin_ok = False
    try:
        est = pedir_reintentando(ip, "/estado", intentos=intentos, timeout=2)
        if est.get("grabando") and est.get("sesion") == sesion:
            pedir_reintentando(ip, "/fin", intentos=intentos, timeout=3)
        fin_ok = True
    except Exception as e:  # noqa: BLE001
        mensajes.append(f"{info['nombre']}: no respondió a /fin ({e}).")
        return [], False, False
    try:
        return descargar_respaldo(ip, sesion, carpeta, info["etiqueta"], intentos=intentos), fin_ok, True
    except Exception as e:  # noqa: BLE001
        mensajes.append(f"{info['nombre']}: no se pudo descargar el respaldo ({e}).")
        return [], fin_ok, False


def actualizar_equipo_en_sesion(carpeta, resumen):
    meta = leer_json(carpeta / "sesion.json", {}) or {}
    eqs = [e for e in meta.get("equipos", []) if e.get("etiqueta") != resumen["etiqueta"]]
    eqs.append(resumen)
    meta["equipos"] = eqs
    meta["pendientes"] = [p for p in meta.get("pendientes", []) if p != resumen["etiqueta"]]
    escribir_json(carpeta / "sesion.json", meta)


def procesar_pendientes(base, equipos_activos, mensajes=None):
    """Intenta cerrar y descargar los respaldos que quedaron pendientes. equipos_activos: lista de Equipo
    que hoy están enviando datos (se reconocen por su MAC o, si no la tienen, por su IP)."""
    mensajes = mensajes if mensajes is not None else []
    lista = leer_pendientes(base)
    if not lista:
        return mensajes
    quedan = []
    for p in lista:
        eq = next((e for e in equipos_activos if e.control and e.activo() and
                   ((p.get("mac") and e.mac == p.get("mac")) or (not p.get("mac") and e.ip == p["ip"]))), None)
        if eq is None:
            quedan.append(p)
            continue
        info = dict(p, ip=eq.ip)
        carpeta = Path(p["carpeta"])
        tramos, _, ok = cerrar_y_bajar(info, p["sesion"], carpeta, mensajes, intentos=2)
        if not ok:
            quedan.append(p)
            continue
        vivos = P.leer_diario(carpeta / f"vivo_{p['etiqueta']}.dat")
        resumen = armar_equipo(carpeta, info, vivos, tramos)
        actualizar_equipo_en_sesion(carpeta, resumen)
        mensajes.append(f"{p['nombre']}: respaldo pendiente de {p['sesion']} recuperado; "
                        f"faltantes {resumen['faltantes_final']}.")
    guardar_pendientes(base, quedan)
    return mensajes


# ---------------------------------------------------------------- sesión
def finalizar(carpeta, equipos_info, meta, mensajes, progreso=lambda t: None, recuperada=False):
    """Cierra una sesión (normal o recuperada): /fin, descarga, unión y sesion.json."""
    base = Path(meta["base"])
    resumen, pendientes = [], []
    for info in equipos_info:
        tramos, fin_ok, ok = [], False, False
        if info.get("control"):
            progreso(f"Cerrando y descargando el respaldo de {info['nombre']}…")
            tramos, fin_ok, ok = cerrar_y_bajar(info, meta["sesion"], carpeta, mensajes)
        if not ok:
            tramos = respaldo_local(carpeta, info["etiqueta"])      # por si ya se había bajado antes
        vivos = P.leer_diario(carpeta / f"vivo_{info['etiqueta']}.dat")
        r = armar_equipo(carpeta, info, vivos, tramos)
        if info.get("control") and not ok:
            pendientes.append(info["etiqueta"])
            agregar_pendiente(base, {**info, "carpeta": str(carpeta), "sesion": meta["sesion"]})
            mensajes.append(f"{info['nombre']}: el respaldo quedó pendiente; se descargará solo cuando el equipo "
                            "vuelva a la red (o con «Recuperar respaldos»).")
        resumen.append(r)
    meta = dict(meta)
    meta.update({"equipos": resumen, "mensajes": mensajes, "pendientes": pendientes, "recuperada": recuperada})
    escribir_json(carpeta / "sesion.json", meta)
    (carpeta / "en_curso.json").unlink(missing_ok=True)
    return meta


def sesiones_sin_terminar(base):
    return sorted(Path(base).glob("*/*/en_curso.json")) if Path(base).exists() else []


def recuperar_sesion(carpeta, progreso=lambda t: None):
    """Cierra una sesión que quedó abierta porque el PC se cerró durante el registro."""
    carpeta = Path(carpeta)
    meta = leer_json(carpeta / "en_curso.json")
    if meta is None:
        raise FileNotFoundError("no hay en_curso.json")
    mensajes = [f"Sesión recuperada después de un cierre inesperado del PC (iniciada {meta['inicio']})."]
    fin = datetime.now()
    meta["fin"] = fin.isoformat(timespec="seconds")
    meta["duracion_s"] = None       # se desconoce el momento real del término
    return finalizar(carpeta, meta["equipos_info"], meta, mensajes, progreso, recuperada=True)


class Registro:
    """Inicia, detiene y guarda una sesión con todos los equipos elegidos.

    Protecciones: cada paquete se escribe al disco apenas llega (vivo_<equipo>.dat), la sesión queda
    marcada con en_curso.json hasta que termina bien, las órdenes se reintentan, y si un respaldo no se
    puede bajar queda en pendientes.json para descargarlo cuando el equipo vuelva."""

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
        self.infos = []

    def meta_base(self):
        return {"sesion": self.sesion, "participante": self.participante, "condicion": self.condicion,
                "repeticion": self.repeticion, "notas": self.notas, "base": str(self.base),
                "inicio": self.inicio.isoformat(timespec="seconds"), "fs_hz": P.FS}

    def iniciar(self):
        self.carpeta.mkdir(parents=True, exist_ok=True)
        # una sesión anterior con el mismo nombre no debe mezclarse con esta
        for viejo in list(self.carpeta.glob("vivo_*.dat")) + list(self.carpeta.glob("respaldo_*.bin")):
            viejo.unlink()
        self.inicio = datetime.now()
        self.infos = [{"ip": eq.ip, "mac": eq.mac, "nombre": eq.nombre, "etiqueta": etiqueta_archivo(eq),
                       "control": bool(eq.control)} for eq in self.equipos]
        escribir_json(self.carpeta / "en_curso.json", {**self.meta_base(), "equipos_info": self.infos})
        for eq, info in zip(self.equipos, self.infos):
            with eq.lock:
                eq.diario = open(self.carpeta / f"vivo_{info['etiqueta']}.dat", "ab")
                eq.rec_recibidos = eq.rec_perdidos = eq.rec_reinicios = 0
                eq.grabando = True
            if eq.control:
                try:
                    pedir_reintentando(eq.ip, "/inicio?sesion=" + urllib.parse.quote(self.sesion), intentos=3, timeout=2)
                except Exception as e:  # noqa: BLE001
                    self.mensajes.append(f"{eq.nombre}: no respondió a /inicio ({e}); se guarda solo lo recibido.")

    def detener(self, progreso=lambda txt: None):
        fin = datetime.now()
        for eq in self.equipos:
            with eq.lock:
                eq.grabando = False
                if eq.diario is not None:
                    eq.diario.flush()
                    os.fsync(eq.diario.fileno())
                    eq.diario.close()
                    eq.diario = None
        meta = {**self.meta_base(), "fin": fin.isoformat(timespec="seconds"),
                "duracion_s": round((fin - self.inicio).total_seconds(), 1)}
        return finalizar(self.carpeta, self.infos, meta, self.mensajes, progreso)
