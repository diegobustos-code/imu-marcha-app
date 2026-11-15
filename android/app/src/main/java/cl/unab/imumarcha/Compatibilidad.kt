package cl.unab.imumarcha

import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Handler
import android.os.HandlerThread
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import kotlin.math.sqrt

/**
 * Mide durante unos segundos lo que el teléfono entrega de verdad cuando se piden 100 Hz:
 * frecuencia real, intervalo máximo entre muestras, valores repetidos, rango y resolución,
 * y si el reposo marca ~1 g. Además prueba que la red UDP funcione dentro del teléfono.
 */
class Compatibilidad(private val sm: SensorManager) {

    class Medicion(
        val nombre: String, val fabricante: String,
        val rangoG: Double, val resolucion: Double, val minDelayUs: Int,
        val hz: Double, val dtMedianaMs: Double, val dtMaxMs: Double,
        val repetidas: Double, val normaMedia: Double, val n: Int
    )

    class Resultado(val acc: Medicion?, val gir: Medicion?, val udpOk: Boolean, val udpError: String?)

    private class Colector : SensorEventListener {
        val ts = ArrayList<Long>()
        val vals = ArrayList<FloatArray>()
        override fun onSensorChanged(e: SensorEvent) {
            synchronized(this) { ts.add(e.timestamp); vals.add(e.values.copyOf(3)) }
        }
        override fun onAccuracyChanged(s: Sensor?, a: Int) {}
    }

    /** Bloquea el hilo que la llama durante [segundos]. No llamar desde el hilo de la interfaz. */
    fun medir(segundos: Double): Resultado {
        val hilo = HandlerThread("compat").also { it.start() }
        val handler = Handler(hilo.looper)
        val sAcc = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        val sGir = sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
        val cAcc = Colector(); val cGir = Colector()
        sAcc?.let { sm.registerListener(cAcc, it, 10_000, handler) }
        sGir?.let { sm.registerListener(cGir, it, 10_000, handler) }
        Thread.sleep((segundos * 1000).toLong())
        sm.unregisterListener(cAcc); sm.unregisterListener(cGir)
        hilo.quitSafely()
        val g = SensorManager.GRAVITY_EARTH.toDouble()
        val acc = sAcc?.let { resumir(it, cAcc, g, esAcc = true) }
        val gir = sGir?.let { resumir(it, cGir, g, esAcc = false) }
        val (ok, err) = probarUdp()
        return Resultado(acc, gir, ok, err)
    }

    private fun resumir(s: Sensor, c: Colector, g: Double, esAcc: Boolean): Medicion {
        val ts: List<Long>; val vs: List<FloatArray>
        synchronized(c) { ts = ArrayList(c.ts); vs = ArrayList(c.vals) }
        // se descarta el primer medio segundo (el sensor se está iniciando)
        val desde = ts.indexOfFirst { it - (ts.firstOrNull() ?: 0L) >= 500_000_000L }.coerceAtLeast(0)
        val t = ts.subList(desde, ts.size); val v = vs.subList(desde, vs.size)
        val dts = DoubleArray(maxOf(t.size - 1, 0)) { (t[it + 1] - t[it]) / 1e6 }
        val dur = if (t.size > 1) (t.last() - t.first()) / 1e9 else 0.0
        val hz = if (dur > 0) (t.size - 1) / dur else 0.0
        val ord = dts.sorted()
        val med = if (ord.isNotEmpty()) ord[ord.size / 2] else 0.0
        val mx = ord.lastOrNull() ?: 0.0
        var rep = 0
        for (i in 1 until v.size) if (v[i].contentEquals(v[i - 1])) rep++
        var norma = 0.0
        if (esAcc && v.isNotEmpty()) {
            for (x in v) norma += sqrt((x[0] * x[0] + x[1] * x[1] + x[2] * x[2]).toDouble())
            norma /= v.size * g
        }
        val escala = if (esAcc) 1.0 / g else 180.0 / Math.PI
        return Medicion(
            s.name, s.vendor, s.maximumRange * escala, s.resolution * escala, s.minDelay,
            hz, med, mx, if (v.size > 1) rep.toDouble() / (v.size - 1) else 0.0, norma, t.size
        )
    }

    private fun probarUdp(): Pair<Boolean, String?> = try {
        DatagramSocket(0, InetAddress.getByName("127.0.0.1")).use { rx ->
            rx.soTimeout = 1000
            DatagramSocket().use { tx ->
                val datos = Paquete.codificar(7, List(Paquete.POR_PAQUETE) {
                    Paquete.Muestra(it * 10_000L, 8192, 0, 0, 0, 0, 0)
                })
                tx.send(DatagramPacket(datos, datos.size, InetAddress.getByName("127.0.0.1"), rx.localPort))
            }
            val buf = ByteArray(1024); val dp = DatagramPacket(buf, buf.size)
            rx.receive(dp)
            val d = Paquete.decodificar(dp.data, dp.length)
            if (d != null && d.secuencia == 7L && d.muestras[0].ax.toInt() == 8192) Pair(true, null)
            else Pair(false, "el paquete llegó alterado")
        }
    } catch (e: Exception) {
        Pair(false, e.message ?: e.toString())
    }
}
