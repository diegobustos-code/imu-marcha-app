package cl.unab.imumarcha

import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.util.Random
import java.util.concurrent.locks.LockSupport
import kotlin.math.PI
import kotlin.math.sin

/**
 * Hace de Stick: cada 10 ms toma la última lectura del acelerómetro y el giroscopio del
 * teléfono (o una marcha simulada) y envía paquetes con el mismo formato que el firmware.
 */
class Emisor(
    private val sm: SensorManager,
    val destino: String,
    private val simulada: Boolean,
    private val frecSim: Double,
    private val carpetaRespaldo: File,
    private val red: android.net.Network? = null
) : Thread("emisor"), SensorEventListener {
    @Volatile var activo = true
    @Volatile var enviados = 0L
    @Volatile var error: String? = null
    @Volatile var secuencia = 0L
    val creado = System.currentTimeMillis()

    // respaldo local (mismo formato que el Stick: paquetes de 164 bytes, uno tras otro)
    @Volatile var grabando = false
    @Volatile var sesion = ""
    @Volatile var bytesGrabados = 0L
    @Volatile var inicioGrabacion = 0L
    private var salida: BufferedOutputStream? = null
    private val lockArchivo = Object()

    fun iniciarGrabacion(nombre: String): Boolean {
        synchronized(lockArchivo) {
        if (grabando) return true
        var n = nombre.filter { it.isLetterOrDigit() || it == '_' || it == '-' }.take(40)
        if (n.isEmpty()) n = "sesion_" + System.currentTimeMillis() / 1000
        carpetaRespaldo.mkdirs()
        carpetaRespaldo.listFiles()?.filter { it.name.startsWith("${n}_cont") }?.forEach { it.delete() }
        salida = BufferedOutputStream(FileOutputStream(File(carpetaRespaldo, "$n.bin")))
        sesion = n; bytesGrabados = 0; inicioGrabacion = System.currentTimeMillis()
        grabando = true
        return true
        }
    }

    fun detenerGrabacion() {
        synchronized(lockArchivo) {
            if (!grabando) return
            grabando = false
            try { salida?.flush(); salida?.close() } catch (_: Exception) {}
            salida = null
        }
    }

    private var ultimoFlush = 0L

    private fun respaldar(bytes: ByteArray) {
        synchronized(lockArchivo) {
            if (!grabando) return
            try {
                salida?.write(bytes); bytesGrabados += bytes.size
                val ahora = System.currentTimeMillis()
                if (ahora - ultimoFlush > 1000) { salida?.flush(); ultimoFlush = ahora }   // al disco cada segundo
            } catch (e: Exception) { error = e.message; grabando = false }
        }
    }

    private val acc = DoubleArray(3)
    private val gir = DoubleArray(3)
    private var hilo: HandlerThread? = null
    private val g = SensorManager.GRAVITY_EARTH.toDouble()

    override fun onSensorChanged(e: SensorEvent) {
        synchronized(acc) {
            when (e.sensor.type) {
                Sensor.TYPE_ACCELEROMETER -> for (k in 0..2) acc[k] = e.values[k] / g
                Sensor.TYPE_GYROSCOPE -> for (k in 0..2) gir[k] = Math.toDegrees(e.values[k].toDouble())
            }
        }
    }

    override fun onAccuracyChanged(s: Sensor?, a: Int) {}

    override fun run() {
        if (!simulada) {
            val h = HandlerThread("sensores-emisor").also { it.start() }
            hilo = h
            val handler = Handler(h.looper)
            sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let { sm.registerListener(this, it, 10_000, handler) }
            sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.let { sm.registerListener(this, it, 10_000, handler) }
        }
        val rnd = Random(42)
        var socket: DatagramSocket? = null
        try {
            socket = DatagramSocket()
            socket.broadcast = true
            red?.bindSocket(socket)          // salir por el WiFi aunque no tenga internet
            val dir = InetAddress.getByName(destino)
            val periodo = 10_000_000L                      // 10 ms en ns
            var proximo = SystemClock.elapsedRealtimeNanos()
            val inicio = proximo
            val lote = ArrayList<Paquete.Muestra>(Paquete.POR_PAQUETE)
            var sec = 0L
            var k = 0L
            while (activo) {
                val espera = proximo - SystemClock.elapsedRealtimeNanos()
                if (espera > 0) { LockSupport.parkNanos(espera); continue }
                proximo += periodo
                val tUs = ((SystemClock.elapsedRealtimeNanos() - inicio) / 1000L) and 0xFFFFFFFFL
                val a = DoubleArray(3); val w = DoubleArray(3)
                if (simulada) {
                    val ph = 2 * PI * frecSim * k / Paquete.FS
                    a[0] = 1.0 + 0.25 * sin(ph) + 0.08 * sin(ph / 2) + 0.03 * rnd.nextGaussian()
                    a[1] = 0.10 * sin(ph / 2) + 0.02 * rnd.nextGaussian()
                    a[2] = 0.15 * sin(ph + 1.0) + 0.02 * rnd.nextGaussian()
                    w[0] = 20 * sin(ph / 2); w[1] = 10 * sin(ph); w[2] = 5 * sin(ph / 2 + 0.5)
                } else synchronized(acc) {
                    for (i in 0..2) { a[i] = acc[i]; w[i] = gir[i] }
                }
                k++
                lote.add(Paquete.Muestra(tUs,
                    Paquete.aCuentas(a[0], Paquete.ESC_ACC), Paquete.aCuentas(a[1], Paquete.ESC_ACC),
                    Paquete.aCuentas(a[2], Paquete.ESC_ACC), Paquete.aCuentas(w[0], Paquete.ESC_GIRO),
                    Paquete.aCuentas(w[1], Paquete.ESC_GIRO), Paquete.aCuentas(w[2], Paquete.ESC_GIRO)))
                if (lote.size == Paquete.POR_PAQUETE) {
                    val bytes = Paquete.codificar(sec++, lote)
                    secuencia = sec
                    respaldar(bytes)
                    try {
                        socket.send(DatagramPacket(bytes, bytes.size, dir, Paquete.PUERTO))
                        enviados++
                    } catch (e: Exception) {
                        error = e.message                 // se sigue respaldando aunque falle la red
                    }
                    lote.clear()
                }
            }
        } catch (e: Exception) {
            if (activo) error = e.message ?: e.toString()
        } finally {
            detenerGrabacion()
            socket?.close()
            sm.unregisterListener(this)
            hilo?.quitSafely()
        }
    }

    fun detener() { activo = false }
}
