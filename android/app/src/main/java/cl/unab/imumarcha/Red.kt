package cl.unab.imumarcha

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities

/** Datos de la red WiFi del celular: la red, su dirección de difusión y la IP del celular. */
object Red {
    fun redWifi(ctx: Context): Network? {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        @Suppress("DEPRECATION")
        return cm.allNetworks.firstOrNull { cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true }
    }

    fun datosWifi(ctx: Context): Pair<String, String>? {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val red = redWifi(ctx) ?: return null
        val la = cm.getLinkProperties(red)?.linkAddresses?.firstOrNull { it.address is java.net.Inet4Address } ?: return null
        val ip = la.address.address
        val pref = la.prefixLength
        val mascara = if (pref == 0) 0 else (-1 shl (32 - pref))
        val ipInt = ((ip[0].toInt() and 255) shl 24) or ((ip[1].toInt() and 255) shl 16) or
            ((ip[2].toInt() and 255) shl 8) or (ip[3].toInt() and 255)
        val b = ipInt or mascara.inv()
        val difusion = "${(b ushr 24) and 255}.${(b ushr 16) and 255}.${(b ushr 8) and 255}.${b and 255}"
        return Pair(difusion, la.address.hostAddress ?: "")
    }
}
