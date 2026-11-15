package cl.unab.imumarcha

import java.io.Writer
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.SocketTimeoutException
import java.util.Locale

/**
 * Recibe los paquetes UDP (del Stick, del ESP32 o del modo emisor de esta app),
 * igual que ver_imu.py: cuenta pérdidas y reinicios, arma el tiempo continuo
 * y guarda cada muestra en el CSV.
 */
class Receptor(private val csv: Writer?, var eje: Int) : Thread("receptor") {
    @Volatile var activo = true
    @Volatile var recibidos = 0L
    @Volatile var perdidos = 0L
    @Volatile var reinicios = 0
    @Volatile var muestras = 0L
    @Volatile var repetidas = 0L       // muestras idénticas a la anterior (sensor más lento que 100 Hz)
    @Volatile var dtMin = Double.MAX_VALUE
    @Volatile var dtMax = 0.0
    @Volatile var ultimoT = 0.0
    @Volatile var error: String? = null

    private val maxBuf = 10 * Paquete.FS
    private val bufT = ArrayDeque<Double>()
    private val bufV = ArrayDeque<DoubleArray>()
    private val lock = Object()

    private var ultimaSec: Long? = null
    private var t0: Double? = null
    private var tPrev: Long? = null
    private var vueltas = 0L
    private var desfase = 0.0
    private var anterior: DoubleArray? = null
    private var socket: DatagramSocket? = null
    private var ultimoFlush = 0L

    override fun run() {
        try {
            val s = DatagramSocket(null)
            s.reuseAddress = true
            s.broadcast = true
            s.soTimeout = 500
            s.bind(InetSocketAddress(Paquete.PUERTO))
            socket = s
            val buf = ByteArray(1024)
            val dp = DatagramPacket(buf, buf.size)
            while (activo) {
                try {
                    s.receive(dp)
                } catch (e: SocketTimeoutException) {
                    continue
                }
                val d = Paquete.decodificar(dp.data, dp.length) ?: continue
                procesar(d)
            }
            s.close()
        } catch (e: Exception) {
            if (activo) error = e.message ?: e.toString()
        } finally {
            try { csv?.flush() } catch (_: Exception) {}
        }
    }

    fun detener() {
        activo = false
        try { socket?.close() } catch (_: Exception) {}
    }

    private fun procesar(d: Paquete.Datos) {
        val sec = d.secuencia
        val ult = ultimaSec
        if (ult != null && sec < ult) {
            reinicios++                       // el emisor se reinició
            t0 = null; tPrev = null; vueltas = 0
            desfase = ultimoT + 1.0 / Paquete.FS
        } else if (ult != null && sec > ult + 1) {
            perdidos += sec - ult - 1
        }
        ultimaSec = sec
        recibidos++
        val sb = StringBuilder()
        synchronized(lock) {
            for (m in d.muestras) {
                val tp = tPrev
                if (tp != null && m.tUs < tp) vueltas++      // el contador de µs da la vuelta
                tPrev = m.tUs
                var t = (m.tUs + vueltas * 4294967296L) / 1e6
                if (t0 == null) t0 = t
                t = t - t0!! + desfase
                if (muestras > 0) {
                    val dt = t - ultimoT
                    if (dt < dtMin) dtMin = dt
                    if (dt > dtMax) dtMax = dt
                }
                ultimoT = t
                val fila = doubleArrayOf(
                    m.ax / Paquete.ESC_ACC, m.ay / Paquete.ESC_ACC, m.az / Paquete.ESC_ACC,
                    m.gx / Paquete.ESC_GIRO, m.gy / Paquete.ESC_GIRO, m.gz / Paquete.ESC_GIRO
                )
                val prev = anterior
                if (prev != null && prev.contentEquals(fila)) repetidas++
                anterior = fila
                muestras++
                bufT.addLast(t); bufV.addLast(fila)
                while (bufT.size > maxBuf) { bufT.removeFirst(); bufV.removeFirst() }
                if (csv != null) {
                    sb.append(String.format(Locale.US, "%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f\n",
                        t, fila[0], fila[1], fila[2], fila[3], fila[4], fila[5]))
                }
            }
        }
        if (csv != null) try {
            csv.write(sb.toString())
            val ahora = System.currentTimeMillis()
            if (ahora - ultimoFlush > 1000) { csv.flush(); ultimoFlush = ahora }     // al disco cada segundo
        } catch (e: Exception) { error = e.message }
    }

    /** Copia de la ventana de los últimos 10 s: tiempos y el eje elegido. */
    fun ventana(): Pair<DoubleArray, DoubleArray> = synchronized(lock) {
        val t = bufT.toDoubleArray()
        val v = DoubleArray(bufV.size)
        var i = 0
        for (f in bufV) v[i++] = f[eje]
        Pair(t, v)
    }

    /** Promedio de las 3 aceleraciones en la ventana (para identificar el eje vertical). */
    fun mediasAcc(): DoubleArray = synchronized(lock) {
        val m = DoubleArray(3)
        if (bufV.isEmpty()) return m
        for (f in bufV) for (k in 0..2) m[k] += f[k]
        for (k in 0..2) m[k] /= bufV.size
        m
    }

    companion object {
        const val ENCABEZADO = "t_s,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps\n"
        val EJES = arrayOf("ax", "ay", "az", "gx", "gy", "gz")
    }
}
