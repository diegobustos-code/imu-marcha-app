# IMU Marcha

App Android para probar la IMU del Proyecto 1 (Análisis de marcha y riesgo de caída, UNAB) antes de tener el M5StickS3 / M5StickC Plus2.

- **1. Compatibilidad:** mide si el teléfono entrega aceleración a 100 Hz estables, rango ±4 g, reposo ≈ 1 g y red UDP.
- **2. Emisor:** hace de Stick. Envía los sensores del teléfono (o una marcha simulada) con el mismo formato de paquete que `imu_esp32_udp.ino`, `imu_m5sticks3_udp.ino` e `imu_m5stickcplus2_udp.ino` (164 bytes, puerto 5005).
- **3. Receptor:** hace lo mismo que `ver_imu.py`: recibe, cuenta pérdidas, muestra señal y espectro, calcula la frecuencia dominante (0,5–3 Hz, Welch, nfft 8192, huecos interpolados) y guarda el CSV en `Descargas/IMU_Marcha` con las columnas `t_s, ax_g, ay_g, az_g, gx_dps, gy_dps, gz_dps`.

La APK se compila sola con GitHub Actions en cada push (pestaña *Actions* → último run → artefacto `IMU_Marcha`).
