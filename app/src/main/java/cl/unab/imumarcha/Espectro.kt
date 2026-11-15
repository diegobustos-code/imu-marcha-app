package cl.unab.imumarcha

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * Frecuencia dominante con el mismo criterio de ver_imu.py:
 * Welch con un segmento (hasta 10 s), ventana de Hann, nfft = 8192 y pico entre 0,5 y 3 Hz.
 * Antes se rellenan los huecos (paquetes perdidos) en una grilla uniforme de 100 Hz.
 */
object Espectro {
    const val NFFT = 8192
    const val F_MIN = 0.5
    const val F_MAX = 3.0

    class Resultado(val fd: Double, val f: DoubleArray, val p: DoubleArray)

    /** Interpola (t, v) en una grilla uniforme de 1/fs s. */
    fun aGrilla(t: DoubleArray, v: DoubleArray, fs: Int): DoubleArray {
        if (t.size < 2) return v.copyOf()
        val n = ((t.last() - t.first()) * fs).toInt()
        val out = DoubleArray(maxOf(n, 0))
        var j = 0
        for (i in out.indices) {
            val ti = t.first() + i.toDouble() / fs
            while (j < t.size - 2 && t[j + 1] < ti) j++
            val t0 = t[j]; val t1 = t[j + 1]
            out[i] = if (t1 > t0) v[j] + (v[j + 1] - v[j]) * (ti - t0) / (t1 - t0) else v[j]
        }
        return out
    }

    fun calcular(t: DoubleArray, v: DoubleArray, fs: Int): Resultado? {
        val g = aGrilla(t, v, fs)
        if (g.size < 4 * fs) return null
        val n = minOf(g.size, 10 * fs)
        val x = g.copyOfRange(g.size - n, g.size)
        // quitar la tendencia lineal (signal.detrend)
        val xm = (n - 1) / 2.0
        var sxy = 0.0; var sxx = 0.0; var ym = 0.0
        for (i in 0 until n) ym += x[i]
        ym /= n
        for (i in 0 until n) { sxy += (i - xm) * (x[i] - ym); sxx += (i - xm) * (i - xm) }
        val pend = if (sxx > 0) sxy / sxx else 0.0
        for (i in 0 until n) x[i] = x[i] - (ym + pend * (i - xm))
        // ventana de Hann (simétrica en scipy "hann" con sym=False para welch)
        val re = DoubleArray(NFFT); val im = DoubleArray(NFFT)
        var media = 0.0
        val w = DoubleArray(n) { 0.5 - 0.5 * cos(2 * PI * it / n) }
        for (i in 0 until n) media += x[i]
        media /= n
        for (i in 0 until n) re[i] = (x[i] - media) * w[i]
        fft(re, im)
        val nf = NFFT / 2 + 1
        val f = DoubleArray(nf) { it.toDouble() * fs / NFFT }
        val p = DoubleArray(nf) { re[it] * re[it] + im[it] * im[it] }
        var mejor = -1; var pmax = -1.0
        for (k in 0 until nf) if (f[k] >= F_MIN && f[k] <= F_MAX && p[k] > pmax) { pmax = p[k]; mejor = k }
        if (mejor < 0) return null
        return Resultado(f[mejor], f, p)
    }

    /** FFT radix-2 iterativa, en el lugar. */
    private fun fft(re: DoubleArray, im: DoubleArray) {
        val n = re.size
        var j = 0
        for (i in 1 until n) {
            var bit = n shr 1
            while (j and bit != 0) { j = j xor bit; bit = bit shr 1 }
            j = j xor bit
            if (i < j) {
                var tmp = re[i]; re[i] = re[j]; re[j] = tmp
                tmp = im[i]; im[i] = im[j]; im[j] = tmp
            }
        }
        var largo = 2
        while (largo <= n) {
            val ang = -2 * PI / largo
            val wr = cos(ang); val wi = sin(ang)
            var i = 0
            while (i < n) {
                var cr = 1.0; var ci = 0.0
                for (k in 0 until largo / 2) {
                    val a = i + k; val b = a + largo / 2
                    val tr = re[b] * cr - im[b] * ci
                    val ti = re[b] * ci + im[b] * cr
                    re[b] = re[a] - tr; im[b] = im[a] - ti
                    re[a] += tr; im[a] += ti
                    val ncr = cr * wr - ci * wi
                    ci = cr * wi + ci * wr; cr = ncr
                }
                i += largo
            }
            largo = largo shl 1
        }
    }
}
