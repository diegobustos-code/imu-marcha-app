# IMU Marcha

Herramientas para la Etapa 2 del Proyecto 1 (Análisis de marcha y riesgo de caída, UNAB): comparar la
frecuencia dominante de la marcha medida con cámara contra una IMU lumbar.

El proyecto principal (Etapa 1, informes, notebooks y plan de la Etapa 2) está en
[proyecto-marcha-practicaI](https://github.com/diegobustos-code/proyecto-marcha-practicaI). Este repositorio
contiene solo el software de captura y procesamiento; no contiene datos de personas.

**Descargas listas para usar:** [última versión](https://github.com/diegobustos-code/imu-marcha-app/releases/latest)
(app de Windows, APK de Android y firmware).

| Carpeta | Qué es |
|---|---|
| `firmware/imu_marcha` | Firmware único para **M5StickS3**, **M5StickC Plus2** o **ESP32 + MPU-6050**: muestreo a 100 Hz, envío UDP (puerto 5005, paquetes de 164 bytes como `imu_esp32_udp.ino`), control HTTP (puerto 8080) y respaldo en la memoria interna. |
| `android` | App Android: prueba de compatibilidad del teléfono, emisor (el celular hace de IMU, con control remoto y respaldo) y receptor (como `ver_imu.py`). |
| `windows` | App de Windows: registro con varios equipos a la vez, inicio y término a distancia, descarga del respaldo, procesamiento del video con MediaPipe, sincronización por saltos y concordancia (Bland-Altman e ICC con los criterios del plan). |
| `herramientas` | `ver_imu.py` y `simulador_esp32.py` (versión corregida de los programas del informe de la IMU). |

Las tres partes hablan el mismo protocolo, así que cualquier combinación funciona: el PC recibe del celular y de
cualquiera de las IMU al mismo tiempo, y los distingue por su dirección IP.

## Protección de los datos

| Falla | Qué hace el sistema |
|---|---|
| Se pierden paquetes por WiFi | La IMU guarda todo en su memoria; al detener, el PC baja el respaldo y rellena. |
| El PC se cierra o se cuelga | Cada paquete se escribe al disco apenas llega (`vivo_<equipo>.dat`) y la sesión queda marcada con `en_curso.json`. Al abrir la app, ofrece recuperarla: cierra el registro en los equipos y baja sus respaldos. |
| Corte total del WiFi al detener | Las órdenes se reintentan; si no hay caso, el respaldo queda en `pendientes.json` y se baja solo cuando el equipo vuelve a la red. |
| La IMU se reinicia mientras graba | El respaldo se asienta cada segundo; al encender, la IMU sigue grabando en `<sesion>_contN.bin` y el PC une los tramos. |
| La memoria de la IMU se llena | Cierra el archivo sin corromperlo y lo informa (`memoria_llena`). |
| El celular apaga la pantalla | El emisor corre como servicio en primer plano y el respaldo se asienta cada segundo. |
| Durante el registro | Alertas visibles y sonoras si un equipo deja de enviar más de 3 s, baja del 15 % de batería, se queda sin memoria, deja de grabar o se reinicia. |

## Protocolo de control (HTTP, puerto 8080)

| Ruta | Respuesta |
|---|---|
| `/estado` | JSON con equipo, batería, si está grabando, sesión, memoria libre |
| `/inicio?sesion=P01_normal_1` | Empieza a guardar el respaldo `P01_normal_1.bin` |
| `/fin` | Termina el respaldo |
| `/archivos` | Lista de respaldos |
| `/archivo?n=P01_normal_1.bin` | Descarga un respaldo (mismos paquetes de 164 bytes, uno tras otro) |
| `/borrar?n=...` | Borra un respaldo |

## Datos en el PC

`F:\Practica_I\Etapa2_datos\<participante>\<participante>_<condición>_<repetición>\` con `imu_<equipo>.csv`
(columnas `t_s, ax_g, ay_g, az_g, gx_dps, gy_dps, gz_dps`), `respaldo_<equipo>.bin`, `paquetes_<equipo>.csv`
(hora de llegada de cada paquete), `sesion.json`, y después del procesamiento `puntos_video.csv` y `resultado.json`.
Todas las sesiones procesadas se reúnen en `resultados_etapa2.csv`.

## Compilación

Cada push compila la APK, el firmware (tres placas) y el ejecutable de Windows con GitHub Actions, corre las
pruebas y publica todo en *Releases*.
