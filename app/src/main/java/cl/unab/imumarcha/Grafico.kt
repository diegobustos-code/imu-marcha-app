package cl.unab.imumarcha

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.view.View
import java.util.Locale

/** Dos gráficos simples: la señal del eje elegido (arriba) y su espectro (abajo). */
class Grafico(ctx: Context) : View(ctx) {
    private var t = DoubleArray(0)
    private var v = DoubleArray(0)
    private var f: DoubleArray? = null
    private var p: DoubleArray? = null
    private var fd: Double? = null
    private var etiqueta = "ax (g)"

    private val dens = resources.displayMetrics.density
    private val linea = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.rgb(31, 95, 168); style = Paint.Style.STROKE; strokeWidth = 1.6f * dens
    }
    private val marca = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.rgb(214, 92, 40); style = Paint.Style.STROKE; strokeWidth = 1.4f * dens
    }
    private val ejeP = Paint().apply { color = Color.rgb(180, 180, 180); strokeWidth = 1f * dens }
    private val texto = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.DKGRAY; textSize = 11f * dens }

    fun actualizar(t: DoubleArray, v: DoubleArray, r: Espectro.Resultado?, etiqueta: String) {
        this.t = t; this.v = v; this.etiqueta = etiqueta
        f = r?.f; p = r?.p; fd = r?.fd
        invalidate()
    }

    override fun onDraw(c: Canvas) {
        val w = width.toFloat(); val h = height.toFloat()
        val m = 30f * dens
        val alto = (h - 3 * m) / 2
        dibujarSenal(c, m, m * 0.6f, w - m * 1.3f, alto)
        dibujarEspectro(c, m, m * 1.9f + alto, w - m * 1.3f, alto)
    }

    private fun dibujarSenal(c: Canvas, x0: Float, y0: Float, ancho: Float, alto: Float) {
        c.drawRect(x0, y0, x0 + ancho, y0 + alto, ejeP.apply { style = Paint.Style.STROKE })
        c.drawText(etiqueta + "  (últimos 10 s)", x0, y0 - 4 * dens, texto)
        if (t.size < 2) { c.drawText("esperando datos…", x0 + 8 * dens, y0 + alto / 2, texto); return }
        var lo = v.min(); var hi = v.max()
        if (hi - lo < 1e-6) { lo -= 0.1; hi += 0.1 }
        lo -= 0.1; hi += 0.1
        val ta = t.first(); val tb = maxOf(t.last(), ta + 1)
        val path = Path()
        for (i in t.indices) {
            val x = x0 + ((t[i] - ta) / (tb - ta) * ancho).toFloat()
            val y = y0 + alto - ((v[i] - lo) / (hi - lo) * alto).toFloat()
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        c.drawPath(path, linea)
        c.drawText(String.format(Locale.US, "%.2f", hi), x0 + ancho + 2 * dens, y0 + 10 * dens, texto)
        c.drawText(String.format(Locale.US, "%.2f", lo), x0 + ancho + 2 * dens, y0 + alto, texto)
    }

    private fun dibujarEspectro(c: Canvas, x0: Float, y0: Float, ancho: Float, alto: Float) {
        c.drawRect(x0, y0, x0 + ancho, y0 + alto, ejeP.apply { style = Paint.Style.STROKE })
        val ff = f; val pp = p; val d = fd
        c.drawText(if (d != null) String.format(Locale.US, "Espectro · frecuencia dominante %.2f Hz (%.0f pasos/min)", d, d * 60)
                   else "Espectro (se calcula con al menos 4 s de datos)", x0, y0 - 4 * dens, texto)
        for (hz in 0..4) {
            val x = x0 + hz / 4f * ancho
            c.drawText("$hz", x - 3 * dens, y0 + alto + 12 * dens, texto)
        }
        c.drawText("Hz", x0 + ancho + 2 * dens, y0 + alto + 12 * dens, texto)
        if (ff == null || pp == null) return
        var pmax = 0.0
        for (k in ff.indices) if (ff[k] > 0.5 && ff[k] < 4.0 && pp[k] > pmax) pmax = pp[k]
        if (pmax <= 0) return
        val path = Path(); var primero = true
        for (k in ff.indices) {
            if (ff[k] > 4.0) break
            val x = x0 + (ff[k] / 4.0 * ancho).toFloat()
            val y = y0 + alto - (minOf(pp[k] / (pmax * 1.1), 1.0) * alto).toFloat()
            if (primero) { path.moveTo(x, y); primero = false } else path.lineTo(x, y)
        }
        c.drawPath(path, linea)
        if (d != null) {
            val x = x0 + (d / 4.0 * ancho).toFloat()
            c.drawLine(x, y0, x, y0 + alto, marca)
        }
    }
}
