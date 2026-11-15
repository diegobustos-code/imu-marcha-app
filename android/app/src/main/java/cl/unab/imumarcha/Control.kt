package cl.unab.imumarcha

import android.os.Build
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder

/**
 * Servidor HTTP mínimo (puerto 8080) con las mismas rutas que el firmware del Stick, para que la
 * app de Windows controle el celular igual que a cualquier IMU:
 *   /estado  /inicio?sesion=X  /fin  /archivos  /archivo?n=X.bin  /borrar?n=X.bin
 * y dos propias del celular, para encenderlo y apagarlo desde el PC sin tocar el teléfono:
 *   /emisor/iniciar[?modo=sensores|simulada&frec=1.9&destino=IP]   (por defecto envía al PC que lo pide)
 *   /emisor/detener
 */
class Control(private val ctx: android.content.Context, private val carpeta: File) : Thread("control") {
    @Volatile var activo = true
    @Volatile var error: String? = null
    private var servidor: ServerSocket? = null

    override fun run() {
        try {
            val s = ServerSocket()
            s.reuseAddress = true
            s.bind(java.net.InetSocketAddress(PUERTO))
            servidor = s
            while (activo) {
                val c = try { s.accept() } catch (e: Exception) { break }
                Thread { atender(c) }.start()
            }
        } catch (e: Exception) {
            if (activo) error = e.message ?: e.toString()
        }
    }

    fun detener() {
        activo = false
        try { servidor?.close() } catch (_: Exception) {}
    }

    private fun atender(c: Socket) {
        c.use { sock ->
            sock.soTimeout = 5000
            val entrada = BufferedReader(InputStreamReader(sock.getInputStream()))
            val linea = entrada.readLine() ?: return
            while (true) { val l = entrada.readLine() ?: break; if (l.isEmpty()) break }
            val partes = linea.split(" ")
            val ruta = partes.getOrElse(1) { "/" }
            val (camino, consulta) = ruta.split("?", limit = 2).let { it[0] to it.getOrElse(1) { "" } }
            val args = consulta.split("&").filter { it.contains("=") }.associate {
                val (k, v) = it.split("=", limit = 2)
                k to URLDecoder.decode(v, "UTF-8")
            }
            val out = sock.getOutputStream()
            var e = Global.emisor
            when (camino) {
                "/estado" -> json(out, 200, estado(e))
                "/emisor/iniciar" -> {
                    val destino = args["destino"] ?: sock.inetAddress.hostAddress ?: "255.255.255.255"
                    e = Global.iniciarEmisor(ctx, destino, args["modo"] == "simulada",
                        args["frec"]?.toDoubleOrNull() ?: 1.9)
                    json(out, 200, estado(e))
                }
                "/emisor/detener" -> {
                    if (e?.grabando == true) json(out, 409, "{\"error\":\"está grabando; primero /fin\"}")
                    else { Global.detenerEmisor(ctx); json(out, 200, estado(null)) }
                }
                "/inicio" -> {
                    if (e == null) json(out, 409, "{\"error\":\"el emisor no está activo\"}")
                    else { e.iniciarGrabacion(args["sesion"] ?: ""); json(out, 200, estado(e)) }
                }
                "/fin" -> { e?.detenerGrabacion(); json(out, 200, estado(e)) }
                "/archivos" -> {
                    val lista = (carpeta.listFiles() ?: emptyArray()).filter { it.isFile }.sortedBy { it.name }
                    json(out, 200, lista.joinToString(",", "[", "]") {
                        "{\"nombre\":\"${it.name}\",\"bytes\":${it.length()}}"
                    })
                }
                "/archivo" -> {
                    val f = archivoSeguro(args["n"])
                    if (f == null || !f.isFile) json(out, 404, "{\"error\":\"no existe\"}")
                    else if (e != null && e.grabando && f.name == e.sesion + ".bin") json(out, 409, "{\"error\":\"grabando\"}")
                    else {
                        out.write(("HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n" +
                            "Content-Length: ${f.length()}\r\nConnection: close\r\n\r\n").toByteArray())
                        f.inputStream().use { it.copyTo(out) }
                        out.flush()
                    }
                }
                "/borrar" -> {
                    val f = archivoSeguro(args["n"])
                    val ok = f != null && f.isFile && !(e != null && e.grabando && f.name == e.sesion + ".bin") && f.delete()
                    json(out, if (ok) 200 else 404, if (ok) "{\"ok\":true}" else "{\"error\":\"no se pudo borrar\"}")
                }
                else -> json(out, 404, "{\"error\":\"ruta desconocida\"}")
            }
        }
    }

    private fun archivoSeguro(n: String?): File? {
        if (n.isNullOrEmpty() || n.contains("/") || n.contains("\\") || n.contains("..")) return null
        return File(carpeta, n)
    }

    private fun estado(e: Emisor?): String {
        val libre = carpeta.apply { mkdirs() }.usableSpace / 1024
        val seg = if (e != null && e.grabando) (System.currentTimeMillis() - e.inicioGrabacion) / 1000.0 else 0.0
        return "{\"equipo\":\"Celular ${Build.MANUFACTURER} ${Build.MODEL}\",\"version\":\"1.0\"," +
            "\"emitiendo\":${e != null},\"remoto\":${Global.remoto},\"grabando\":${e?.grabando ?: false},\"sesion\":\"${e?.sesion ?: ""}\"," +
            "\"segundos\":${"%.1f".format(java.util.Locale.US, seg)},\"bytes\":${e?.bytesGrabados ?: 0}," +
            "\"secuencia\":${e?.secuencia ?: 0},\"descartados\":0,\"bateria\":-1,\"libre_kb\":$libre,\"fs_hz\":100}"
    }

    private fun json(out: OutputStream, codigo: Int, cuerpo: String) {
        val b = cuerpo.toByteArray()
        val txt = when (codigo) { 200 -> "OK"; 404 -> "Not Found"; 409 -> "Conflict"; else -> "Error" }
        out.write(("HTTP/1.1 $codigo $txt\r\nContent-Type: application/json\r\nContent-Length: ${b.size}\r\n" +
            "Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n").toByteArray())
        out.write(b)
        out.flush()
    }

    companion object { const val PUERTO = 8080 }
}
