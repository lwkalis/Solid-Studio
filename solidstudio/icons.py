"""Original vector glyphs drawn at the toolbar's native resolution."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF


def icon(name, size=28):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 28, size / 28)
    blue, ink, fill, orange = "#366785", "#354b59", "#c4e1f3", "#e79248"

    def line(points, color=ink, width=1.5):
        painter.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(QPolygonF([QPointF(*p) for p in points]))

    def polygon(points, color=fill):
        painter.setPen(QPen(QColor(ink), 1.2))
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygonF([QPointF(*p) for p in points]))

    def cube():
        polygon([(4, 10), (14, 5), (24, 10), (14, 15)])
        polygon([(4, 10), (14, 15), (14, 25), (4, 20)], "#a0c6dd")
        polygon([(14, 15), (24, 10), (24, 20), (14, 25)], "#79abc8")

    if name in {"Extrude", "Combine", "Fillet", "Chamfer", "Bodies", "View"}:
        cube()
        if name == "Extrude":
            line([(14, 15), (14, 2), (10, 6)], blue, 2)
            line([(14, 2), (18, 6)], blue, 2)
        elif name == "Combine":
            polygon([(16, 4), (21, 2), (26, 5), (21, 8), (21, 14), (16, 11)], "#f2bd7c")
        elif name in {"Fillet", "Chamfer"}:
            line([(4, 10), (14, 15), (24, 10)], orange, 3)
    elif name in {"Sketch", "Rectangle"}:
        polygon([(3, 19), (8, 7), (23, 7), (19, 20)], "#dcecf6")
        line([(5, 15), (21, 15)], blue, 1)
        if name == "Sketch":
            line([(19, 20), (24, 20), (24, 25)], "#5c9661", 2)
            line([(19, 25), (24, 25), (24, 20)], "#5c9661", 2)
    elif name == "Circle":
        painter.setPen(QPen(QColor(blue), 1.8))
        painter.drawEllipse(QRectF(5, 5, 18, 18))
        line([(14, 14), (20, 8)], blue)
    elif name == "Arc":
        painter.setPen(QPen(QColor(blue), 2))
        painter.drawArc(QRectF(4, 6, 20, 18), 0, 180 * 16)
        painter.drawEllipse(QPointF(4, 15), 2, 2)
        painter.drawEllipse(QPointF(24, 15), 2, 2)
    elif name == "Trim":
        line([(4, 4), (24, 24)], blue, 2)
        line([(4, 24), (24, 4)], ink, 2)
        for x in (5, 23):
            painter.drawEllipse(QPointF(x, 23), 3, 3)
    elif name == "Project":
        polygon([(4, 18), (13, 13), (24, 18), (15, 24)], "#dcecf6")
        line([(14, 3), (14, 17), (10, 13)], blue, 2)
        line([(14, 17), (18, 13)], blue, 2)
    elif name == "Line":
        line([(5, 22), (22, 6)], blue, 2)
        for point in [(5, 22), (22, 6)]:
            painter.drawEllipse(QPointF(*point), 2, 2)
    elif name in {"Save", "Open", "New", "Export STEP"}:
        polygon([(5, 3), (17, 3), (23, 9), (23, 25), (5, 25)], "#e3edf3")
        line([(17, 3), (17, 9), (23, 9)], blue)
        if name == "Save":
            line([(9, 20), (19, 20), (19, 15), (9, 15), (9, 20)], blue, 2)
        elif name != "New":
            line([(9, 17), (21, 17), (17, 13)], blue, 2)
            line([(21, 17), (17, 21)], blue, 2)
    elif name in {"Undo", "Redo", "Orbit"}:
        if name == "Redo":
            painter.translate(28, 0)
            painter.scale(-1, 1)
        line([(3, 13), (9, 7), (9, 11), (17, 11), (22, 13), (24, 18), (20, 22), (13, 22)], blue, 2)
        line([(3, 13), (9, 18), (9, 13)], blue, 2)
    elif name in {"Finish Sketch", "Origin"}:
        if name == "Finish Sketch":
            line([(5, 14), (11, 20), (23, 7)], "#40824f", 3)
        else:
            line([(13, 15), (23, 18)], "#c36058", 2)
            line([(13, 15), (13, 4)], "#4f83ad", 2)
            line([(13, 15), (4, 22)], "#61905d", 2)
    elif name == "Pan":
        line([(14, 4), (14, 24)], blue, 2)
        line([(4, 14), (24, 14)], blue, 2)
        for pts in [[(11, 7), (14, 4), (17, 7)], [(11, 21), (14, 24), (17, 21)],
                    [(7, 11), (4, 14), (7, 17)], [(21, 11), (24, 14), (21, 17)]]:
            line(pts, blue, 1.5)
    elif name in {"Zoom", "Fit"}:
        painter.setPen(QPen(QColor(blue), 2))
        painter.drawEllipse(QRectF(4, 4, 14, 14))
        line([(16, 16), (24, 24)], blue, 2)
        if name == "Fit":
            line([(7, 11), (15, 11)], blue)
            line([(11, 7), (11, 15)], blue)
    else:
        polygon([(7, 4), (22, 16), (15, 17), (19, 24), (15, 26), (11, 18), (7, 22)], "#e1edf4")
    painter.end()
    return QIcon(pixmap)
