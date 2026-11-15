package cl.unab.imumarcha

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager

/** Lo que tiene que seguir funcionando aunque la pantalla se apague o se cierre la ventana. */
object Global {
    @Volatile var emisor: Emisor? = null
    @Volatile var control: Control? = null
}

/**
 * Servicio en primer plano mientras el emisor está activo: Android no cierra la app ni corta los
 * sensores aunque se apague la pantalla, y un bloqueo parcial mantiene la CPU despierta.
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
            .setContentTitle("IMU Marcha: emisor activo")
            .setContentText("Enviando la aceleración a 100 Hz. Tócalo para abrir la app.")
            .setSmallIcon(R.drawable.ic_imu)
            .setOngoing(true)
            .setContentIntent(abrir)
            .build()
        if (Build.VERSION.SDK_INT >= 34) startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH)
        else startForeground(ID, n)
        if (bloqueo == null) {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            bloqueo = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "imumarcha:emisor").apply {
                setReferenceCounted(false); acquire(12 * 60 * 60 * 1000L)
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        bloqueo?.let { if (it.isHeld) it.release() }
        bloqueo = null
        super.onDestroy()
    }

    companion object {
        const val CANAL = "emisor"
        const val ID = 1
    }
}
