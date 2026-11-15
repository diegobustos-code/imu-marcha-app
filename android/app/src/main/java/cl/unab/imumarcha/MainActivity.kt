package cl.unab.imumarcha

import android.app.Activity
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.hardware.SensorManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.view.WindowManager
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import java.io.BufferedWriter
import java.io.OutputStreamWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : Activity() {

    private lateinit var sm: SensorManager
    private val ui = Handler(Looper.getMainLooper())
    private val dp get() = resources.displayMetrics.density

    // pestañas
    private lateinit var vistas: List<View>
    private lateinit var botonesPestana: List<Button>

    // compatibilidad
    private lateinit var txtCompat: TextView
    private lateinit var btnCompat: Button
    private var midiendo = false

    // emisor
    private var emisor: Emisor? = null
    private lateinit var txtEmisor: TextView
    private lateinit var btnEmisor: Button
    private lateinit var btnRespaldo: Button
    private lateinit var rbSimulada: RadioButton
    private lateinit var edFrec: EditText
    private lateinit var edDestino: EditText
    private var inicioEmisor = 0L
    private var control: Control? = null
    private val carpetaRespaldo by lazy { java.io.File(getExternalFilesDir(null), "respaldo") }

    // receptor
    private var receptor: Receptor? = null
    private lateinit var txtReceptor: TextView
    private lateinit var btnReceptor: Button
    private lateinit var btnCompartir: Button
    private lateinit var edArchivo: EditText
    private lateinit var edSegundos: EditText
    private lateinit var chkGuardar: CheckBox
    private lateinit var spEje: Spinner
    private lateinit var grafico: Grafico
    private var csvUri: Uri? = null
    private var csvWriter: BufferedWriter? = null
    private var ultimoArchivo: Uri? = null
    private var segundosLimite = 0.0
    private var multicast: WifiManager.MulticastLock? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        sm = getSystemService(Context.SENSOR_SERVICE) as SensorManager

        val raiz = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setBackgroundColor(Color.WHITE) }
        raiz.addView(TextView(this).apply {
            text = "IMU Marcha · Proyecto 1"
            setTextColor(Color.WHITE); textSize = 18f; setTypeface(typeface, Typeface.BOLD)
            setBackgroundColor(Color.rgb(31, 95, 168)); setPadding(px(16), px(14), px(16), px(14))
        })
        val barra = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        val nombres = listOf("1. Compatibilidad", "2. Emisor", "3. Receptor")
        botonesPestana = nombres.mapIndexed { i, n ->
            Button(this).apply {
                text = n; isAllCaps = false; textSize = 13f
                setOnClickListener { mostrar(i) }
                barra.addView(this, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            }
        }
        raiz.addView(barra)
        vistas = listOf(crearCompatibilidad(), crearEmisor(), crearReceptor())
        val cont = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        vistas.forEach { cont.addView(it, LinearLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT)) }
        raiz.addView(cont, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
        // Android 15+ dibuja bajo la barra de estado: se deja espacio para las barras del sistema
        raiz.setOnApplyWindowInsetsListener { v, ins ->
            if (Build.VERSION.SDK_INT >= 30) {
                val b = ins.getInsets(android.view.WindowInsets.Type.systemBars())
                v.setPadding(b.left, b.top, b.right, b.bottom)
            } else {
                @Suppress("DEPRECATION")
                v.setPadding(ins.systemWindowInsetLeft, ins.systemWindowInsetTop, ins.systemWindowInsetRight, ins.systemWindowInsetBottom)
            }
            ins
        }
        raiz.setBackgroundColor(Color.rgb(31, 95, 168))
        cont.setBackgroundColor(Color.WHITE)
        barra.setBackgroundColor(Color.WHITE)
        setContentView(raiz)
        mostrar(0)
        ui.postDelayed({ medirCompatibilidad() }, 600)
        ui.post(refresco)
        control = Control({ emisor }, carpetaRespaldo).also { it.start() }
    }

    override fun onDestroy() {
        ui.removeCallbacksAndMessages(null)
        emisor?.detener()
        control?.detener()
        detenerReceptor()
        super.onDestroy()
    }

    private fun px(v: Int) = (v * dp).toInt()

    private fun mostrar(i: Int) {
        vistas.forEachIndexed { k, v -> v.visibility = if (k == i) View.VISIBLE else View.GONE }
        botonesPestana.forEachIndexed { k, b -> b.setTypeface(null, if (k == i) Typeface.BOLD else Typeface.NORMAL) }
    }

    private fun pagina(): Pair<ScrollView, LinearLayout> {
        val sc = ScrollView(this)
        val ll = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(px(16), px(12), px(16), px(24)) }
        sc.addView(ll)
        return Pair(sc, ll)
    }

    private fun parrafo(t: String, tam: Float = 14f) = TextView(this).apply {
        text = t; textSize = tam; setTextColor(Color.rgb(40, 40, 40)); setPadding(0, px(4), 0, px(4))
    }

    private fun mantenerPantalla() {
        if (emisor != null || receptor != null) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    // ------------------------------------------------------------------ 1. Compatibilidad

    private fun crearCompatibilidad(): View {
        val (sc, ll) = pagina()
        ll.addView(parrafo("Revisa si este teléfono entrega los datos que necesita el proyecto: " +
            "aceleración a 100 Hz estables (como en LTMM), rango de al menos ±4 g y red UDP funcionando.\n\n" +
            "Deja el teléfono quieto sobre una mesa durante la prueba (6 s)."))
        btnCompat = Button(this).apply { text = "Repetir prueba"; isAllCaps = false; setOnClickListener { medirCompatibilidad() } }
        ll.addView(btnCompat)
        txtCompat = parrafo("", 14f).apply { typeface = Typeface.MONOSPACE; textSize = 13f }
        ll.addView(txtCompat)
        return sc
    }

    private fun medirCompatibilidad() {
        if (midiendo) return
        if (emisor != null) { txtCompat.text = "Detén el emisor antes de repetir la prueba."; return }
        midiendo = true
        btnCompat.isEnabled = false
        txtCompat.text = "Midiendo… deja el teléfono quieto."
        Thread {
            val r = Compatibilidad(sm).medir(6.0)
            ui.post {
                txtCompat.text = informeCompatibilidad(r)
                btnCompat.isEnabled = true
                midiendo = false
            }
        }.start()
    }

    private fun wifiConectado(): Boolean {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
    }

    private fun redWifi(): android.net.Network? {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        @Suppress("DEPRECATION")
        return cm.allNetworks.firstOrNull { cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true }
    }

    /** Dirección de difusión de la red WiFi (por ejemplo 192.168.4.255) y la IP del celular en ella. */
    private fun datosWifi(): Pair<String, String>? {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val red = redWifi() ?: return null
        val la = cm.getLinkProperties(red)?.linkAddresses?.firstOrNull { it.address is java.net.Inet4Address } ?: return null
        val ip = la.address.address
        val pref = la.prefixLength
        val mascara = if (pref == 0) 0 else (-1 shl (32 - pref))
        val ipInt = ((ip[0].toInt() and 255) shl 24) or ((ip[1].toInt() and 255) shl 16) or ((ip[2].toInt() and 255) shl 8) or (ip[3].toInt() and 255)
        val b = ipInt or mascara.inv()
        val difusion = "${(b ushr 24) and 255}.${(b ushr 16) and 255}.${(b ushr 8) and 255}.${b and 255}"
        return Pair(difusion, la.address.hostAddress ?: "")
    }

    private fun difusionWifi(): String? = datosWifi()?.first

    private fun informeCompatibilidad(r: Compatibilidad.Resultado): String {
        val sb = StringBuilder()
        var fallas = 0; var avisos = 0
        fun ok(t: String) = sb.append("✔ ").append(t).append('\n')
        fun av(t: String) { avisos++; sb.append("⚠ ").append(t).append('\n') }
        fun mal(t: String) { fallas++; sb.append("✘ ").append(t).append('\n') }

        sb.append("Teléfono: ${Build.MANUFACTURER} ${Build.MODEL}\nAndroid ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})\n\n")
        val a = r.acc
        sb.append("ACELERÓMETRO\n")
        if (a == null) {
            mal("No tiene acelerómetro.")
        } else {
            sb.append("  ${a.nombre} (${a.fabricante})\n")
            sb.append(String.format(Locale.US, "  Pedido: 100 Hz · medido: %.1f Hz (%d muestras)\n", a.hz, a.n))
            sb.append(String.format(Locale.US, "  Intervalo típico %.1f ms · máximo %.1f ms\n", a.dtMedianaMs, a.dtMaxMs))
            sb.append(String.format(Locale.US, "  Rango ±%.1f g · resolución %.2f mg\n", a.rangoG, a.resolucion * 1000))
            when {
                a.hz >= 95 -> ok("Frecuencia suficiente para 100 Hz.")
                a.hz >= 50 -> av("Entrega menos de 100 Hz; el emisor repetirá valores.")
                else -> mal("Frecuencia muy baja para el proyecto.")
            }
            when {
                a.dtMaxMs <= 30 -> ok("Sin pausas largas entre muestras.")
                a.dtMaxMs <= 100 -> av(String.format(Locale.US, "Hubo una pausa de %.0f ms.", a.dtMaxMs))
                else -> mal(String.format(Locale.US, "Pausas largas (%.0f ms): señal irregular.", a.dtMaxMs))
            }
            if (a.repetidas <= 0.01) ok("Cada muestra es una lectura nueva.")
            else av(String.format(Locale.US, "%.1f %% de muestras repetidas.", a.repetidas * 100))
            if (a.rangoG >= 3.9) ok("Rango suficiente (≥ ±4 g).") else av("Rango menor a ±4 g: puede saturar al pisar fuerte.")
            if (a.normaMedia in 0.95..1.05) ok(String.format(Locale.US, "Reposo: %.3f g (cerca de 1 g).", a.normaMedia))
            else av(String.format(Locale.US, "Reposo: %.3f g. ¿Estaba quieto?", a.normaMedia))
        }
        sb.append("\nGIROSCOPIO (no lo usa el modelo)\n")
        val g = r.gir
        if (g == null) av("No tiene giroscopio; el CSV llevará el giro en 0.")
        else {
            sb.append(String.format(Locale.US, "  %s · medido %.1f Hz\n", g.nombre, g.hz))
            if (g.hz >= 95) ok("Giroscopio a 100 Hz.") else av(String.format(Locale.US, "Giroscopio a %.0f Hz.", g.hz))
        }
        sb.append("\nRED\n")
        if (r.udpOk) ok("Envío y recepción UDP dentro del teléfono.") else mal("UDP no funciona: ${r.udpError}")
        if (wifiConectado()) ok("Conectado a una red WiFi.")
        else av("Sin WiFi. Para probar en este mismo teléfono no hace falta; para el Stick, conéctate a la red IMU-Marcha.")

        sb.append("\nRESULTADO\n")
        val veredicto = when {
            fallas > 0 -> "NO APTO como IMU de prueba. Sirve solo como receptor del Stick si la red UDP funciona."
            avisos > 0 -> "APTO CON OBSERVACIONES. Revisa los avisos (⚠) antes de grabar."
            else -> "APTO. Sirve como IMU de prueba (pestaña 2) y como receptor del Stick (pestaña 3)."
        }
        sb.append(veredicto).append('\n')
        return sb.toString()
    }

    // ------------------------------------------------------------------ 2. Emisor

    private fun crearEmisor(): View {
        val (sc, ll) = pagina()
        ll.addView(parrafo("Hace de Stick: envía la aceleración y el giro del teléfono (o una marcha simulada) " +
            "a 100 Hz, con el mismo formato de paquete que el firmware. Así se prueban el receptor y el registro " +
            "antes de que llegue el equipo."))
        val grupo = RadioGroup(this)
        val rbSensores = RadioButton(this).apply { text = "Sensores del teléfono"; id = View.generateViewId() }
        rbSimulada = RadioButton(this).apply { text = "Marcha simulada"; id = View.generateViewId() }
        grupo.addView(rbSensores); grupo.addView(rbSimulada); grupo.check(rbSensores.id)
        ll.addView(grupo)
        ll.addView(parrafo("Frecuencia de la marcha simulada (Hz):", 13f))
        edFrec = EditText(this).apply { setText("1.90"); inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL }
        ll.addView(edFrec)
        ll.addView(parrafo("Destino de los paquetes:", 13f))
        edDestino = EditText(this).apply { setText("127.0.0.1"); inputType = InputType.TYPE_CLASS_TEXT }
        ll.addView(edDestino)
        val fila = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        fila.addView(Button(this).apply { text = "Este teléfono"; isAllCaps = false; setOnClickListener { edDestino.setText("127.0.0.1") } },
            LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        fila.addView(Button(this).apply { text = "Toda la red WiFi"; isAllCaps = false; setOnClickListener { edDestino.setText(difusionWifi() ?: "255.255.255.255") } },
            LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        ll.addView(fila)
        ll.addView(parrafo("«Este teléfono» sirve para probar todo con un solo celular (emisor y receptor a la vez). " +
            "«Toda la red WiFi» envía a otro equipo de la misma red, por ejemplo el PC con la app de Windows o ver_imu.py.\n\n" +
            "Mientras el emisor está activo, la app de Windows puede iniciar y detener el registro a distancia; " +
            "el celular guarda además un respaldo propio que el PC descarga al terminar.", 12f))
        btnEmisor = Button(this).apply { text = "Iniciar emisor"; isAllCaps = false; setOnClickListener { alternarEmisor() } }
        ll.addView(btnEmisor)
        btnRespaldo = Button(this).apply {
            text = "Grabar respaldo aquí"; isAllCaps = false; isEnabled = false
            setOnClickListener {
                val e = emisor ?: return@setOnClickListener
                if (e.grabando) e.detenerGrabacion() else e.iniciarGrabacion("local_" + nombrePorDefecto())
            }
        }
        ll.addView(btnRespaldo)
        txtEmisor = parrafo("Detenido.").apply { typeface = Typeface.MONOSPACE; textSize = 13f }
        ll.addView(txtEmisor)
        return sc
    }

    private fun alternarEmisor() {
        val e = emisor
        if (e != null) {
            e.detener(); emisor = null
            btnEmisor.text = "Iniciar emisor"
            btnRespaldo.isEnabled = false
            txtEmisor.text = "Detenido. Paquetes enviados: ${e.enviados}"
        } else {
            if (midiendo) { txtEmisor.text = "Espera que termine la prueba de compatibilidad."; return }
            val f = edFrec.text.toString().replace(',', '.').toDoubleOrNull() ?: 1.9
            val dest = edDestino.text.toString().trim()
            val red = if (dest.startsWith("127.")) null else redWifi()
            val nuevo = Emisor(sm, dest, rbSimulada.isChecked, f, carpetaRespaldo, red)
            emisor = nuevo; inicioEmisor = System.currentTimeMillis()
            nuevo.start()
            btnEmisor.text = "Detener emisor"
            btnRespaldo.isEnabled = true
        }
        mantenerPantalla()
    }

    // ------------------------------------------------------------------ 3. Receptor

    private fun crearReceptor(): View {
        val (sc, ll) = pagina()
        ll.addView(parrafo("Hace lo mismo que ver_imu.py: recibe los paquetes por el puerto 5005, muestra la señal " +
            "y su espectro, calcula la frecuencia dominante (0,5 a 3 Hz) y guarda un CSV en Descargas/IMU_Marcha."))
        ll.addView(parrafo("Nombre del archivo (código, nunca el nombre de la persona):", 13f))
        edArchivo = EditText(this).apply { setText(nombrePorDefecto()); inputType = InputType.TYPE_CLASS_TEXT }
        ll.addView(edArchivo)
        val fila = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        fila.addView(parrafo("Eje vertical: ", 13f))
        spEje = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, Receptor.EJES)
        }
        fila.addView(spEje)
        fila.addView(parrafo("   Segundos (0 = manual): ", 13f))
        edSegundos = EditText(this).apply { setText("0"); inputType = InputType.TYPE_CLASS_NUMBER; minEms = 3 }
        fila.addView(edSegundos)
        ll.addView(fila)
        chkGuardar = CheckBox(this).apply { text = "Guardar CSV"; isChecked = true }
        ll.addView(chkGuardar)
        btnReceptor = Button(this).apply { text = "Iniciar receptor"; isAllCaps = false; setOnClickListener { alternarReceptor() } }
        ll.addView(btnReceptor)
        grafico = Grafico(this)
        ll.addView(grafico, LinearLayout.LayoutParams(MATCH_PARENT, px(380)))
        txtReceptor = parrafo("Detenido.").apply { typeface = Typeface.MONOSPACE; textSize = 12.5f }
        ll.addView(txtReceptor)
        btnCompartir = Button(this).apply {
            text = "Compartir último CSV"; isAllCaps = false; isEnabled = false
            setOnClickListener { compartir() }
        }
        ll.addView(btnCompartir)
        return sc
    }

    private fun nombrePorDefecto() = "imu_" + SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())

    private fun alternarReceptor() {
        if (receptor != null) { detenerReceptor(); return }
        var nombre = edArchivo.text.toString().trim().ifEmpty { nombrePorDefecto() }
        if (!nombre.endsWith(".csv")) nombre += ".csv"
        segundosLimite = edSegundos.text.toString().toDoubleOrNull() ?: 0.0
        csvUri = null; csvWriter = null
        if (chkGuardar.isChecked) {
            try {
                val valores = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, nombre)
                    put(MediaStore.Downloads.MIME_TYPE, "text/csv")
                    put(MediaStore.Downloads.RELATIVE_PATH, "Download/IMU_Marcha")
                    put(MediaStore.Downloads.IS_PENDING, 1)
                }
                val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, valores)
                    ?: throw IllegalStateException("no se pudo crear el archivo")
                val w = BufferedWriter(OutputStreamWriter(contentResolver.openOutputStream(uri)!!, Charsets.UTF_8))
                w.write(Receptor.ENCABEZADO)
                csvUri = uri; csvWriter = w
            } catch (e: Exception) {
                txtReceptor.text = "No se pudo crear el CSV: ${e.message}"; return
            }
        }
        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        multicast = wifi.createMulticastLock("imu-marcha").apply { setReferenceCounted(false); acquire() }
        val r = Receptor(csvWriter, spEje.selectedItemPosition)
        receptor = r
        r.start()
        btnReceptor.text = "Detener receptor"
        txtReceptor.text = "Escuchando en el puerto ${Paquete.PUERTO}…"
        mantenerPantalla()
    }

    private fun detenerReceptor() {
        val r = receptor ?: return
        r.detener()
        try { r.join(1500) } catch (_: InterruptedException) {}
        receptor = null
        multicast?.let { if (it.isHeld) it.release() }
        multicast = null
        val uri = csvUri
        var aviso = ""
        try {
            csvWriter?.flush(); csvWriter?.close()
            if (uri != null) {
                contentResolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
                ultimoArchivo = uri
                btnCompartir.isEnabled = true
                aviso = "\nArchivo guardado en Descargas/IMU_Marcha."
            }
        } catch (e: Exception) {
            aviso = "\nError al cerrar el CSV: ${e.message}"
        }
        csvWriter = null; csvUri = null
        btnReceptor.text = "Iniciar receptor"
        txtReceptor.text = estadoReceptor(r) + "\nDetenido." + aviso
        edArchivo.setText(nombrePorDefecto())
        mantenerPantalla()
    }

    private fun estadoReceptor(r: Receptor): String {
        val total = r.recibidos + r.perdidos
        val pct = if (total > 0) 100.0 * r.perdidos / total else 0.0
        val m = r.mediasAcc()
        val vertical = (0..2).maxByOrNull { kotlin.math.abs(m[it]) } ?: 0
        val sb = StringBuilder()
        sb.append(String.format(Locale.US, "Paquetes: %d recibidos, %d perdidos (%.2f %%)%s\n",
            r.recibidos, r.perdidos, pct, if (r.reinicios > 0) ", ${r.reinicios} reinicios" else ""))
        sb.append(String.format(Locale.US, "Muestras: %d · tiempo %.1f s\n", r.muestras, r.ultimoT))
        if (r.muestras > 1) {
            sb.append(String.format(Locale.US, "Intervalo entre muestras: %.1f a %.1f ms (debe ser 10)\n",
                r.dtMin * 1000, r.dtMax * 1000))
            sb.append(String.format(Locale.US, "Muestras repetidas: %.1f %%\n", 100.0 * r.repetidas / r.muestras))
            sb.append(String.format(Locale.US, "Promedio ax %.2f · ay %.2f · az %.2f g → eje vertical probable: %s\n",
                m[0], m[1], m[2], Receptor.EJES[vertical]))
        }
        if (pct > 1.0) sb.append("⚠ Más de 1 % de pérdidas: acerca el receptor al emisor.\n")
        r.error?.let { sb.append("Error: $it\n") }
        return sb.toString()
    }

    private fun compartir() {
        val uri = ultimoArchivo ?: return
        val i = Intent(Intent.ACTION_SEND).apply {
            type = "text/csv"
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        startActivity(Intent.createChooser(i, "Compartir CSV"))
    }

    // ------------------------------------------------------------------ actualización periódica

    private val refresco = object : Runnable {
        override fun run() {
            emisor?.let { e ->
                val s = (System.currentTimeMillis() - inicioEmisor) / 1000.0
                val tasa = if (s > 0) e.enviados * Paquete.POR_PAQUETE / s else 0.0
                val ip = datosWifi()?.second ?: "sin WiFi"
                val resp = if (e.grabando) String.format(Locale.US, "GRABANDO respaldo «%s» · %.0f s · %d kB",
                    e.sesion, (System.currentTimeMillis() - e.inicioGrabacion) / 1000.0, e.bytesGrabados / 1024)
                    else "Respaldo detenido (se controla desde el PC)"
                btnRespaldo.text = if (e.grabando) "Detener respaldo" else "Grabar respaldo aquí"
                txtEmisor.text = String.format(Locale.US,
                    "Enviando a %s:%d\nPaquetes: %d · %.1f muestras/s · %.0f s\n%s\nControl: http://%s:%d%s%s",
                    edDestino.text, Paquete.PUERTO, e.enviados, tasa, s, resp, ip, Control.PUERTO,
                    e.error?.let { "\nError: $it" } ?: "", control?.error?.let { "\nControl: $it" } ?: "")
            }
            receptor?.let { r ->
                r.eje = spEje.selectedItemPosition
                val (t, v) = r.ventana()
                val res = if (v.size >= 4 * Paquete.FS) Espectro.calcular(t, v, Paquete.FS) else null
                val unidad = if (r.eje < 3) "g" else "°/s"
                grafico.actualizar(t, v, res, "${Receptor.EJES[r.eje]} ($unidad)")
                txtReceptor.text = estadoReceptor(r)
                if (segundosLimite > 0 && r.ultimoT >= segundosLimite) detenerReceptor()
            }
            ui.postDelayed(this, 500)
        }
    }
}
