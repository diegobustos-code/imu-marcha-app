"""Diagnóstico del puerto UDP 5005 en Windows (WinError 10013 y similares)."""
import os
import re
import subprocess

COMANDOS_RESERVA = (
    "net stop winnat\n"
    "netsh int ipv4 add excludedportrange protocol=udp startport=5005 numberofports=1\n"
    "net start winnat"
)


def _ejecutar(cmd):
    try:
        flags = 0x08000000 if os.name == "nt" else 0            # CREATE_NO_WINDOW
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, creationflags=flags)
        return r.stdout
    except Exception:  # noqa: BLE001
        return ""


def rangos_reservados(salida):
    rangos = []
    for linea in salida.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s*(\*)?\s*$", linea)
        if m:
            rangos.append((int(m.group(1)), int(m.group(2)), bool(m.group(3))))
    return rangos


def procesos_en_puerto(salida_netstat, puerto):
    pids = set()
    for linea in salida_netstat.splitlines():
        partes = linea.split()
        if len(partes) >= 4 and partes[0].upper() == "UDP" and partes[1].endswith(f":{puerto}"):
            pids.add(partes[-1])
    return sorted(pids)


def diagnosticar(puerto=5005):
    """Devuelve (causa, texto con la explicación y la solución)."""
    if os.name != "nt":
        return "otro", f"El puerto {puerto} está ocupado por otro programa."
    reservados = rangos_reservados(_ejecutar(["netsh", "interface", "ipv4", "show", "excludedportrange", "protocol=udp"]))
    ocupados = procesos_en_puerto(_ejecutar(["netstat", "-ano", "-p", "udp"]), puerto)
    if ocupados:
        nombres = []
        for pid in ocupados:
            fila = _ejecutar(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]).strip().split(",")
            nombres.append(f"{fila[0].strip(chr(34))} (PID {pid})" if fila and fila[0] else f"PID {pid}")
        return "ocupado", (f"El puerto {puerto} lo está usando otro programa: {', '.join(nombres)}.\n\n"
                           "Solución: ciérralo (puede ser otra ventana de IMU Marcha, ver_imu.py o el simulador "
                           "en otra ventana). La app vuelve a intentar sola cada 3 segundos.")
    for a, b, admin in reservados:
        if a <= puerto <= b:
            return "reservado", (
                f"Windows reservó los puertos UDP {a} a {b}, y el {puerto} quedó dentro. Esto lo hacen Hyper-V, "
                "WSL o Docker al encender el PC, y por eso aparece el error 10013.\n\n"
                "Solución rápida: reiniciar el PC suele mover la reserva a otros puertos.\n\n"
                "Solución permanente (una sola vez): abrir PowerShell como administrador y ejecutar estos tres "
                f"comandos. Dejan el puerto {puerto} apartado para esta app:\n\n{COMANDOS_RESERVA}")
    return "otro", (f"Windows no permitió abrir el puerto {puerto} (error 10013) y no está reservado ni ocupado por "
                    "otro programa. Suele ser un antivirus o una regla del firewall. Prueba permitir IMU Marcha en el "
                    "firewall (redes privadas) o reiniciar el PC.")
