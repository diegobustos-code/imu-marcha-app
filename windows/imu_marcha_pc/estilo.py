"""Aspecto de la app: tema claro u oscuro según Windows, iconos y colores."""
import sys
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

ACENTO = "#1F5FA8"
VERDE = "#1a9f45"
ROJO = "#d64545"
AMBAR = "#d39b16"

# Iconos sencillos (24 x 24) dibujados con trazos; COLOR se reemplaza por el color del tema.
_SVG = {
    "registro": '<circle cx="12" cy="12" r="7" fill="#d64545"/>',
    "detener": '<rect x="6" y="6" width="12" height="12" rx="2" fill="COLOR"/>',
    "procesar": '<rect x="3" y="5" width="18" height="14" rx="2" fill="none" stroke="COLOR" stroke-width="2"/>'
                '<path d="M10 9l5 3-5 3z" fill="COLOR"/>',
    "concordancia": '<path d="M4 20V4M4 20h16" stroke="COLOR" stroke-width="2" fill="none"/>'
                    '<circle cx="9" cy="14" r="1.8" fill="COLOR"/><circle cx="13" cy="11" r="1.8" fill="COLOR"/>'
                    '<circle cx="17" cy="8" r="1.8" fill="COLOR"/>',
    "ayuda": '<circle cx="12" cy="12" r="9" fill="none" stroke="COLOR" stroke-width="2"/>'
             '<path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .8-1 1.5V14" stroke="COLOR" stroke-width="2" fill="none"/>'
             '<circle cx="12" cy="17" r="1.2" fill="COLOR"/>',
    "onda": '<path d="M2 12h4l2-6 4 12 3-8 2 4h5" stroke="COLOR" stroke-width="2" fill="none" stroke-linejoin="round"/>',
    "descargar": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14" stroke="COLOR" stroke-width="2" fill="none"/>',
    "carpeta": '<path d="M3 6h6l2 2h10v11H3z" fill="none" stroke="COLOR" stroke-width="2" stroke-linejoin="round"/>',
    "wifi": '<path d="M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0" stroke="COLOR" '
            'stroke-width="2" fill="none"/><circle cx="12" cy="19" r="1.5" fill="COLOR"/>',
    "diagnostico": '<circle cx="10" cy="10" r="6" fill="none" stroke="COLOR" stroke-width="2"/>'
                   '<path d="M14.5 14.5L20 20" stroke="COLOR" stroke-width="2.5"/>',
    "copiar": '<rect x="8" y="8" width="12" height="12" rx="2" fill="none" stroke="COLOR" stroke-width="2"/>'
              '<path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" fill="none" stroke="COLOR" stroke-width="2"/>',
    "actualizar": '<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5" stroke="COLOR" stroke-width="2" fill="none"/>',
}


def oscuro():
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False
    return app.palette().color(QtGui.QPalette.Window).lightness() < 128


def color_texto():
    return "#e8e8e8" if oscuro() else "#202124"


def color_suave():
    return "#9aa0a6" if oscuro() else "#6b7075"


def icono(nombre, color=None):
    from PySide6 import QtSvg
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           + _SVG[nombre].replace("COLOR", color or color_texto()) + "</svg>")
    render = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode()))
    pix = QtGui.QPixmap(48, 48)
    pix.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pix)
    render.render(p)
    p.end()
    return QtGui.QIcon(pix)


def ruta_recurso(nombre):
    for base in [Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parent.parent / "recursos",
                 Path(sys.argv[0]).resolve().parent / "recursos"]:
        if (base / nombre).is_file():
            return base / nombre
    return None


def aplicar(app):
    app.setStyle("Fusion")
    ico = ruta_recurso("icono.png")
    if ico:
        app.setWindowIcon(QtGui.QIcon(str(ico)))
    borde = "#3c4043" if oscuro() else "#d0d4d9"
    fondo_caja = "#26282b" if oscuro() else "#f6f8fa"
    app.setStyleSheet(f"""
        QGroupBox {{ border: 1px solid {borde}; border-radius: 8px; margin-top: 14px; padding: 8px;
                     background: {fondo_caja}; font-weight: 600; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
        QPushButton {{ border: 1px solid {borde}; border-radius: 6px; padding: 6px 12px; }}
        QPushButton:hover {{ border-color: {ACENTO}; }}
        QPushButton#principal {{ background: {VERDE}; color: white; border: none; font-size: 15px;
                                 font-weight: bold; border-radius: 8px; }}
        QPushButton#principal:hover {{ background: #18893c; }}
        QPushButton#detener {{ background: {ROJO}; color: white; border: none; font-size: 15px;
                               font-weight: bold; border-radius: 8px; }}
        QTabBar::tab {{ padding: 8px 16px; }}
        QTabBar::tab:selected {{ border-bottom: 3px solid {ACENTO}; font-weight: 600; }}
        QLineEdit {{ border: 1px solid {borde}; border-radius: 5px; padding: 4px; }}
        QFrame#pildora {{ border-radius: 11px; padding: 2px 10px; }}
        QFrame#aviso {{ background: #5c1d1d; border: 1px solid {ROJO}; border-radius: 8px; }}
    """)


def configurar_graficos():
    import pyqtgraph as pg
    if oscuro():
        pg.setConfigOptions(antialias=True, background="#1e1f22", foreground="#c8c8c8")
    else:
        pg.setConfigOptions(antialias=True, background="w", foreground="#333")


class Pildora(QtWidgets.QFrame):
    """Etiqueta redondeada de estado (verde, ámbar, rojo o gris)."""

    def __init__(self, texto=""):
        super().__init__()
        self.setObjectName("pildora")
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(10, 3, 10, 3)
        self.lbl = QtWidgets.QLabel(texto)
        h.addWidget(self.lbl)
        self.fijar(texto, "gris")

    def fijar(self, texto, nivel):
        colores = {"verde": VERDE, "ambar": AMBAR, "rojo": ROJO, "gris": "#6b7075", "azul": ACENTO}
        self.lbl.setText(texto)
        self.setStyleSheet(f"QFrame#pildora {{ background: {colores[nivel]}; }} QLabel {{ color: white; font-weight: 600; }}")
