"""Análisis de la Etapa 2: frecuencia dominante desde la IMU y desde el video, sincronización
por los saltos del inicio, regla de errores gruesos y concordancia (Bland-Altman e ICC).

Las funciones pasabajo() y espectro() son las mismas del notebook 01 (LTMM) y de marcha_video.py,
para que la cámara y la IMU se midan exactamente igual que en el modelo.
"""
import numpy as np
import pandas as pd
from scipy import signal

FS_IMU = 100
CORTE_RUIDO_HZ = 20
CORTE_VIDEO_HZ = 6
VISIBILIDAD_MIN = 0.5
MAX_PERDIDOS = 0.10
MIN_DURACION_S = 6
UMBRAL_RIESGO_HZ = 1.93

# Criterios fijados en el plan de la Etapa 2 (sección 4), antes de medir
CRITERIOS = {"sesgo_max_hz": 0.05, "loa_max_hz": 0.10, "icc_min_global": 0.90, "icc_min_normal": 0.75}

# Regla de errores gruesos: frecuencia dominante frente a la cadencia contando pasos
RAZON_MIN, RAZON_MAX = 0.75, 1.33
MIN_PASOS = 3

HOMBRO_IZQ, HOMBRO_DER, CADERA_IZQ, CADERA_DER = 11, 12, 23, 24


# ------------------------------------------------------------------ funciones del notebook 01
def pasabajo(x, fs, fc, orden=4):
    b, a = signal.butter(orden, fc / (fs / 2), btype="low")
    return signal.filtfilt(b, a, x, axis=0)


def welch(v, fs):
    return signal.welch(v, fs=fs, nperseg=min(len(v), int(10 * fs)), nfft=8192)


def espectro(v, fs):
    f, p = welch(v, fs)
    banda = (f >= 0.5) & (f <= 3.0)
    i = int(np.argmax(np.where(banda, p, 0)))
    mitad = p[i] / 2
    izq = i
    while izq > 0 and p[izq] > mitad:
        izq -= 1
    der = i
    while der < len(p) - 1 and p[der] > mitad:
        der += 1
    return f[i], p[i], f[der] - f[izq]


def contar_pasos(v, fs):
    suave = pasabajo(v, fs, 3)
    picos, _ = signal.find_peaks(suave, distance=int(0.3 * fs), prominence=0.3 * np.std(suave))
    if len(picos) < 3:
        return np.nan, len(picos)
    return 60 / np.median(np.diff(picos) / fs), len(picos)


def frecuencia_con_control(v, fs):
    """Frecuencia dominante + regla de errores gruesos.

    Si la razón entre la frecuencia dominante y la cadencia contando pasos queda fuera de 0,75-1,33,
    se toma el pico del espectro más cercano a la cadencia contada. Con menos de 3 pasos la pasada
    se descarta.
    """
    fd, _, ancho = espectro(v, fs)
    cad, n = contar_pasos(v, fs)
    res = {"frec_dominante_hz": fd, "ancho_pico_hz": ancho, "cadencia_conteo_pasos_min": cad,
           "n_pasos": n, "corregida": False, "descartada": False, "frec_original_hz": fd}
    if n < MIN_PASOS or not np.isfinite(cad):
        res["descartada"] = True
        return res
    razon = fd * 60 / cad
    if not (RAZON_MIN <= razon <= RAZON_MAX):
        f, p = welch(v, fs)
        banda = (f >= 0.5) & (f <= 3.0)
        picos, _ = signal.find_peaks(np.where(banda, p, 0))
        if len(picos):
            objetivo = cad / 60
            k = picos[np.argmin(np.abs(f[picos] - objetivo))]
            res["frec_dominante_hz"] = float(f[k])
            res["corregida"] = True
    return res


# ------------------------------------------------------------------ IMU
def leer_imu(ruta):
    d = pd.read_csv(ruta)
    t = d["t_s"].to_numpy(float)
    acc = d[["ax_g", "ay_g", "az_g"]].to_numpy(float)
    return t, acc


def imu_a_grilla(t, acc, fs=FS_IMU):
    """Rellena los huecos (paquetes perdidos) en una grilla uniforme de 100 Hz."""
    tn = np.arange(t[0], t[-1], 1 / fs)
    return tn, np.column_stack([np.interp(tn, t, acc[:, j]) for j in range(acc.shape[1])])


def eje_vertical(acc):
    return int(np.argmax(np.abs(acc.mean(axis=0))))


def senal_imu(t, acc, ventana=None):
    t, acc = imu_a_grilla(np.asarray(t, float), np.asarray(acc, float))
    eje = eje_vertical(acc)
    if ventana is not None:
        m = (t >= ventana[0]) & (t <= ventana[1])
        t, acc = t[m], acc[m]
    v = pasabajo(acc[:, eje], FS_IMU, CORTE_RUIDO_HZ)
    v = v - v.mean()
    return t, v, "xyz"[eje]


def frecuencia_imu(t, acc, ventana=None):
    tt, v, eje = senal_imu(t, acc, ventana)
    if len(v) < 4 * FS_IMU:
        raise ValueError("Tramo de IMU demasiado corto (< 4 s)")
    r = frecuencia_con_control(v, FS_IMU)
    r.update({"duracion_s": len(v) / FS_IMU, "eje_vertical": eje})
    return r


# ------------------------------------------------------------------ video
def extraer_puntos(video, modelo, progreso=None, cancelar=None):
    """MediaPipe Pose sobre el video: un cuadro por fila con caderas y hombros (como marcha_video.py)."""
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    opciones = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(modelo)),
        running_mode=vision.RunningMode.VIDEO, num_poses=1)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    filas, i = [], 0
    with vision.PoseLandmarker.create_from_options(opciones) as detector:
        while True:
            if cancelar is not None and cancelar():
                break
            ok, cuadro = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(cuadro, cv2.COLOR_BGR2RGB)
            imagen = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
            res = detector.detect_for_video(imagen, int(round(i * 1000 / fps)))
            fila = {"cuadro": i, "tiempo_s": i / fps}
            if res.pose_landmarks:
                p = res.pose_landmarks[0]
                for nombre, k in [("cadera_izq", CADERA_IZQ), ("cadera_der", CADERA_DER),
                                  ("hombro_izq", HOMBRO_IZQ), ("hombro_der", HOMBRO_DER)]:
                    fila[f"{nombre}_x"], fila[f"{nombre}_y"] = p[k].x, p[k].y
                    fila[f"{nombre}_vis"] = p[k].visibility
            filas.append(fila)
            i += 1
            if progreso is not None and i % 15 == 0:
                progreso(i, total)
    cap.release()
    df = pd.DataFrame(filas)
    df.attrs["fps"] = fps
    return df


COLS_Y = ["cadera_izq_y", "cadera_der_y", "hombro_izq_y", "hombro_der_y"]


def cadera_cruda(df):
    """Altura de la pelvis normalizada por el tronco (positiva hacia arriba), con huecos en NaN."""
    for c in COLS_Y:
        if c not in df:
            df[c] = np.nan
    vis_cols = [c.replace("_y", "_vis") for c in COLS_Y if c.replace("_y", "_vis") in df]
    vis = df[vis_cols].min(axis=1) if vis_cols else pd.Series(1.0, index=df.index)
    malo = (df[COLS_Y].isna().any(axis=1) | (vis < VISIBILIDAD_MIN)).to_numpy()
    cadera = (df["cadera_izq_y"] + df["cadera_der_y"]) / 2
    hombro = (df["hombro_izq_y"] + df["hombro_der_y"]) / 2
    tronco = (cadera - hombro).abs()
    esc = tronco[~malo].median() if (~malo).any() else np.nan
    y = -(cadera / esc)
    y[malo] = np.nan
    return df["tiempo_s"].to_numpy(float), y.to_numpy(float), malo


def senal_cadera(df, fps, ventana=None):
    """Oscilación vertical de la pelvis lista para el espectro (igual que marcha_video.py)."""
    t, y, malo = cadera_cruda(df)
    if ventana is not None:
        m = (t >= ventana[0]) & (t <= ventana[1])
        t, y, malo = t[m], y[m], malo[m]
    ok = np.flatnonzero(~malo)
    if len(ok) < 2 * fps:
        raise ValueError("La persona aparece menos de 2 s en el tramo")
    t, y, malo = t[ok[0]:ok[-1] + 1], y[ok[0]:ok[-1] + 1], malo[ok[0]:ok[-1] + 1]
    perdido = float(malo.mean())
    y = pd.Series(y).interpolate(limit_direction="both").to_numpy()
    y = signal.detrend(y)
    b, a = signal.butter(2, 0.5 / (fps / 2), btype="high")
    y = signal.filtfilt(b, a, y)
    if fps > 2 * CORTE_VIDEO_HZ:
        y = pasabajo(y, fps, CORTE_VIDEO_HZ)
    return t, y, perdido


def frecuencia_video(df, fps, ventana=None):
    t, y, perdido = senal_cadera(df, fps, ventana)
    r = frecuencia_con_control(y, fps)
    dur = len(y) / fps
    r.update({"duracion_s": dur, "pct_cuadros_perdidos": 100 * perdido,
              "valida": bool(perdido <= MAX_PERDIDOS and dur >= MIN_DURACION_S and not r["descartada"]),
              "t_inicio_s": float(t[0]), "t_fin_s": float(t[-1])})
    return r


# ------------------------------------------------------------------ sincronización por saltos
def saltos_imu(t, acc, umbral_g=1.6, buscar_s=30):
    """Instantes de los aterrizajes de los saltos del inicio (picos de |a| sobre el umbral)."""
    tt, a = imu_a_grilla(np.asarray(t, float), np.asarray(acc, float))
    norma = np.linalg.norm(a, axis=1)
    m = tt <= tt[0] + buscar_s
    picos, _ = signal.find_peaks(norma[m], height=umbral_g, distance=int(0.35 * FS_IMU))
    return tt[m][picos]


def saltos_video(df, fps, buscar_s=30):
    """Instantes de los saltos en el video: máximos de la altura de la cadera claramente
    sobre su nivel de reposo, en los primeros segundos."""
    t, y, malo = cadera_cruda(df)
    m = (t <= t[0] + buscar_s) & ~malo
    if m.sum() < fps:
        return np.array([])
    tv, yv = t[m], y[m]
    base = np.median(yv)
    dev = np.median(np.abs(yv - base)) + 1e-6
    picos, _ = signal.find_peaks(yv, height=base + 6 * dev, distance=int(0.35 * fps))
    # La IMU marca el aterrizaje (golpe), no el punto más alto: en el video se busca el momento en
    # que la cadera vuelve casi a su nivel de reposo después de cada salto.
    aterrizajes = []
    for k in picos:
        nivel = base + 0.2 * (yv[k] - base)
        j = k
        while j < len(yv) - 1 and yv[j] > nivel:
            j += 1
        aterrizajes.append(tv[j])
    return np.array(aterrizajes)


def desfase_por_saltos(t_imu_saltos, t_vid_saltos):
    """Desfase tal que t_imu = t_video + desfase, usando los primeros saltos de cada uno.
    Se emparejan en orden; con al menos 2 saltos en ambos se informa la dispersión."""
    n = min(len(t_imu_saltos), len(t_vid_saltos), 3)
    if n == 0:
        return None, None
    d = np.asarray(t_imu_saltos[:n]) - np.asarray(t_vid_saltos[:n])
    return float(np.median(d)), (float(d.max() - d.min()) if n > 1 else None)


# ------------------------------------------------------------------ concordancia
def bland_altman(cam, imu):
    cam, imu = np.asarray(cam, float), np.asarray(imu, float)
    d = cam - imu
    sesgo = float(d.mean())
    sd = float(d.std(ddof=1)) if len(d) > 1 else float("nan")
    return {"n": int(len(d)), "sesgo_hz": sesgo, "sd_hz": sd,
            "loa_inf_hz": sesgo - 1.96 * sd, "loa_sup_hz": sesgo + 1.96 * sd}


def icc_a1(cam, imu):
    """ICC(A,1): dos vías, acuerdo absoluto, medida única (McGraw y Wong)."""
    x = np.column_stack([cam, imu]).astype(float)
    n, k = x.shape
    if n < 2:
        return float("nan")
    gm = x.mean()
    msr = k * ((x.mean(axis=1) - gm) ** 2).sum() / (n - 1)
    msc = n * ((x.mean(axis=0) - gm) ** 2).sum() / (k - 1)
    sse = ((x - x.mean(axis=1, keepdims=True) - x.mean(axis=0, keepdims=True) + gm) ** 2).sum()
    mse = sse / ((n - 1) * (k - 1))
    return float((msr - mse) / (msr + (k - 1) * mse + k * (msc - mse) / n))


def evaluar_criterios(tabla):
    """tabla: DataFrame con columnas frec_camara_hz, frec_imu_hz, condicion."""
    ba = bland_altman(tabla["frec_camara_hz"], tabla["frec_imu_hz"])
    icc = icc_a1(tabla["frec_camara_hz"], tabla["frec_imu_hz"])
    normal = tabla[tabla["condicion"].str.lower().str.startswith("normal")]
    icc_n = icc_a1(normal["frec_camara_hz"], normal["frec_imu_hz"]) if len(normal) > 1 else float("nan")
    loa = max(abs(ba["loa_inf_hz"]), abs(ba["loa_sup_hz"])) if np.isfinite(ba["sd_hz"]) else float("nan")
    c = CRITERIOS
    return {
        **ba, "icc_global": icc, "icc_normal": icc_n, "n_normal": int(len(normal)),
        "cumple_sesgo": bool(abs(ba["sesgo_hz"]) <= c["sesgo_max_hz"]),
        "cumple_loa": bool(np.isfinite(loa) and loa <= c["loa_max_hz"]),
        "cumple_icc_global": bool(np.isfinite(icc) and icc >= c["icc_min_global"]),
        "cumple_icc_normal": bool(np.isfinite(icc_n) and icc_n >= c["icc_min_normal"]),
    }
