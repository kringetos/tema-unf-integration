"""Specification PDF generated in Python from the saved order, without recalculating tax."""
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import threading
from xml.sax.saxutils import escape


_font_lock = threading.Lock()
_fonts_ready = False


def number(value):
    return f"{Decimal(str(value)):,.2f}".replace(",", " ")


def specification(order, currency="", organization=""):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    global _fonts_ready
    with _font_lock:
        if not _fonts_ready:
            fonts = Path(__file__).resolve().parents[1] / "assets" / "fonts"
            pdfmetrics.registerFont(TTFont("TemaText", str(fonts / "Ubuntu-R.ttf")))
            pdfmetrics.registerFont(TTFont("TemaBold", str(fonts / "Ubuntu-B.ttf")))
            pdfmetrics.registerFontFamily("TemaText", normal="TemaText", bold="TemaBold")
            _fonts_ready = True
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=(210 * mm, 297 * mm), rightMargin=17 * mm,
                            leftMargin=17 * mm, topMargin=19 * mm, bottomMargin=18 * mm,
                            title=f"Спецификация к заказу {order['number']}", author="ТеМа")
    text = ParagraphStyle("text", fontName="TemaText", fontSize=9, leading=13, textColor=colors.HexColor("#263345"))
    title = ParagraphStyle("title", parent=text, fontName="TemaBold", fontSize=20, leading=25, spaceAfter=10)
    small = ParagraphStyle("small", parent=text, fontSize=8, leading=11)
    right = ParagraphStyle("right", parent=small, alignment=TA_RIGHT)
    story = [Paragraph("Спецификация", title),
             Paragraph(f"К заказу № {escape(str(order['number']))} от {escape(str(order['date']))}", text), Spacer(1, 7 * mm)]
    if organization:
        story.append(Paragraph("Исполнитель: " + escape(organization), text))
    story.append(Paragraph("Заказчик: " + escape(order.get("customer", {}).get("name", "")), text))
    story.append(Spacer(1, 7 * mm))
    rows = [[Paragraph(escape(label), small) for label in ["№", "Наименование", "Ед.", "Кол-во", "Цена", "Сумма"]]]
    for index, item in enumerate(order.get("items", []), 1):
        values = [str(index), item.get("product", {}).get("name", ""), item.get("unit", ""),
                  number(item.get("quantity", 0)), number(item.get("price", 0)), number(item.get("total", 0))]
        rows.append([Paragraph(escape(str(value)), right if col >= 3 else small) for col, value in enumerate(values)])
    table = Table(rows, colWidths=[8 * mm, 76 * mm, 14 * mm, 22 * mm, 26 * mm, 30 * mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf0f7")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.HexColor("#8195ad")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#dce3eb")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([table, Spacer(1, 6 * mm)])
    total = order.get("total_amount", 0)
    item_total = sum((Decimal(str(x.get("total", 0))) for x in order.get("items", [])), Decimal(0))
    if item_total != Decimal(str(total)):
        story.append(Paragraph("Итого по строкам: " + number(item_total) + " " + escape(currency), text))
        story.append(Paragraph("Дополнительные суммы документа: " + number(Decimal(str(total)) - item_total) + " " + escape(currency), text))
    story.append(Paragraph("<b>Итого по документу: " + number(total) + " " + escape(currency) + "</b>", text))
    story.extend([Spacer(1, 9 * mm), Paragraph("Цены, суммы и налоги перенесены из сохраненного заказа в 1С.", small),
                  Spacer(1, 12 * mm), Paragraph("Исполнитель ____________________     Заказчик ____________________", text)])

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("TemaText", 8)
        canvas.setFillColor(colors.HexColor("#68778a"))
        canvas.drawString(17 * mm, 10 * mm, "ТеМа | Спецификация к заказу")
        canvas.drawRightString(193 * mm, 10 * mm, str(document.page))
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
