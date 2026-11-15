package cl.unab.imumarcha

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Formato de los paquetes UDP, idéntico al de imu_esp32_udp.ino, imu_m5sticks3_udp.ino,
 * imu_m5stickcplus2_udp.ino y ver_imu.py:
 *   uint32 secuencia + 10 muestras de (uint32 t_us, int16 ax, ay, az, gx, gy, gz), little-endian.
 */
object Paquete {
    const val PUERTO = 5005
    const val FS = 100                 // Hz
    const val POR_PAQUETE = 10
    const val BYTES_MUESTRA = 16
    const val BYTES = 4 + POR_PAQUETE * BYTES_MUESTRA   // 164
    const val ESC_ACC = 8192.0         // cuentas por g (±4 g)
    const val ESC_GIRO = 65.5          // cuentas por °/s (±500 °/s)

    class Muestra(
        val tUs: Long,                 // 0 .. 2^32-1
        val ax: Short, val ay: Short, val az: Short,
        val gx: Short, val gy: Short, val gz: Short
    )

    class Datos(val secuencia: Long, val muestras: List<Muestra>)

    fun aCuentas(valor: Double, escala: Double): Short {
        val c = Math.round(valor * escala)
        return c.coerceIn(-32768L, 32767L).toInt().toShort()
    }

    fun codificar(secuencia: Long, muestras: List<Muestra>): ByteArray {
        val b = ByteBuffer.allocate(BYTES).order(ByteOrder.LITTLE_ENDIAN)
        b.putInt((secuencia and 0xFFFFFFFFL).toInt())
        for (m in muestras) {
            b.putInt((m.tUs and 0xFFFFFFFFL).toInt())
            b.putShort(m.ax); b.putShort(m.ay); b.putShort(m.az)
            b.putShort(m.gx); b.putShort(m.gy); b.putShort(m.gz)
        }
        return b.array()
    }

    fun decodificar(datos: ByteArray, largo: Int): Datos? {
        if (largo != BYTES) return null
        val b = ByteBuffer.wrap(datos, 0, largo).order(ByteOrder.LITTLE_ENDIAN)
        val sec = b.int.toLong() and 0xFFFFFFFFL
        val lista = ArrayList<Muestra>(POR_PAQUETE)
        repeat(POR_PAQUETE) {
            val t = b.int.toLong() and 0xFFFFFFFFL
            lista.add(Muestra(t, b.short, b.short, b.short, b.short, b.short, b.short))
        }
        return Datos(sec, lista)
    }
}
