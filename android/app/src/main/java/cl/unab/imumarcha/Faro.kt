package cl.unab.imumarcha

import android.content.Context
import android.os.Build
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/**
 * En modo remoto, avisa cada segundo a la red que el celular está disponible ("IMUM" + JSON al puerto
 * 5005). La app de Windows lo ve aunque todavía no esté enviando datos y puede encenderlo a distancia.
 */
class Faro(private val ctx: Context) : Thread("faro") {
    @Volatile var activo = true

    override fun run() {
        while (activo) {
            try {
                val datos = Red.datosWifi(ctx)
                val red = Red.redWifi(ctx)
                if (datos != null && red != null) {
                    DatagramSocket().use { s ->
                        s.broadcast = true
                        red.bindSocket(s)
                        val e = Global.emisor
                        val json = "{\"equipo\":\"Celular ${Build.MANUFACTURER} ${Build.MODEL}\"," +
                            "\"modo\":\"${if (e != null) "emitiendo" else "espera"}\",\"control\":${Control.PUERTO}," +
                            "\"grabando\":${e?.grabando ?: false},\"version\":\"1.1\"}"
                        val b = ("IMUM" + json).toByteArray()
                        s.send(DatagramPacket(b, b.size, InetAddress.getByName(datos.first), Paquete.PUERTO))
                    }
                }
            } catch (_: Exception) {
            }
            try { sleep(1000) } catch (_: InterruptedException) { break }
        }
    }

    fun detener() { activo = false; interrupt() }
}
