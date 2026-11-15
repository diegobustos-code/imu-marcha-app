package cl.unab.imumarcha

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.SensorManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import java.io.File

/** Lo que tiene que seguir funcionando aunque la pantalla se apague o se cierre la ventana. */
object Global {
    @Volatile var emisor: Emisor? = null
    @Volatile var control: Control? = null
    @Volatile var faro: Faro? = null
    @Volatile var remoto = false              // el PC puede encender y apagar el emisor
    @Volatile var servicioActivo = false

    fun carpeta(ctx: Context) = File(ctx.getExternalFilesDir(null), "respaldo")

    fun asegurarControl(ctx: Context) {
        if (control == null) control = Control(ctx.applicationContext, carpeta(ctx)).also { it.start() }
    }

    /** Enciende el emisor (o lo reapunta al PC si estaba enviando solo a este teléfono). */
    @Synchronized
    fun iniciarEmisor(ctx: Context, destino: String, simulada: Boolean, frec: Double): Emisor {
        emisor?.let { actual ->
            if (!actual.destino.startsWith("127.") || destino.startsWith("127.")) return actual
            actual.detener(); emisor = null
        }
        val sm = ctx.getSystemService(Context.SENSOR_SERVICE) as SensorManager
        val red = if (destino.startsWith("127.")) null else Red.redWifi(ctx)
        val e = Emisor(sm, destino, simulada, frec, carpeta(ctx), red)
        emisor = e
        e.start()
        if (!servicioActivo) ctx.startForegroundService(Intent(ctx, ServicioEmisor::class.java))
        return e
    }

    @Synchronized
    fun detenerEmisor(ctx: Context) {
        emisor?.detener()
        emisor = null
        if (!remoto) ctx.stopService(Intent(ctx, ServicioEmisor::class.java))
    }

    fun activarRemoto(ctx: Context, si: Boolean) {
        remoto = si
        if (si) {
            asegurarControl(ctx)
            if (!servicioActivo) ctx.startForegroundService(Intent(ctx, ServicioEmisor::class.java))
            if (faro == null) faro = Faro(ctx.applicationContext).also { it.start() }
        } else {
            faro?.detener(); faro = null
            if (emisor == null) ctx.stopService(Intent(ctx, ServicioEmisor::class.java))
        }
    }
}

/**
 * Servicio en primer plano mientras el emisor o el modo remoto están activos: Android no cierra la
 * app ni corta los sensores aunque se apague la pantalla, y un bloqueo parcial mantiene la CPU despierta.
 */
class ServicioEmisor : Service() {
    private var bloqueo: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(NotificationChannel(CANAL, "IMU Marcha", NotificationManager.IMPORTANCE_LOW))
        val abrir = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = Notification.Builder(this, CANAL)
            .setContentTitle(if (Global.remoto) "IMU Marcha: modo remoto" else "IMU Marcha: emisor activo")
            .setContentText(if (Global.remoto) "El PC enciende y apaga el envío de datos. Tócalo para abrir la app."
                            else "Enviando la aceleración a 100 Hz. Tócalo para abrir la app.")
            .setSmallIcon(R.drawable.ic_imu)
            .setOngoing(true)
            .setContentIntent(abrir)
            .build()
        if (Build.VERSION.SDK_INT >= 34) startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH)
        else startForeground(ID, n)
        Global.servicioActivo = true
        Global.asegurarControl(this)
        if (bloqueo == null) {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            bloqueo = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "imumarcha:emisor").apply {
                setReferenceCounted(false); acquire(12 * 60 * 60 * 1000L)
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        Global.servicioActivo = false
        bloqueo?.let { if (it.isHeld) it.release() }
        bloqueo = null
        super.onDestroy()
    }

    companion object {
        const val CANAL = "emisor"
        const val ID = 1
    }
}
