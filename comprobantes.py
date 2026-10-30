"""Documentos en PDF (para PC) y JPG (para WhatsApp): órdenes de servicio, cotizaciones,
y ventas de equipos.

No son facturas electrónicas DIAN: son soportes del servicio para el cliente.
"""
import io
import os
import textwrap

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONT_DIR = os.path.join(os.path.dirname(__file__), "static", "fonts")
F_REG = os.path.join(FONT_DIR, "DejaVuSans.ttf")
F_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
LOGO = os.path.join(os.path.dirname(__file__), "static", "logo.jpg")
HAY_LOGO = os.path.exists(LOGO)
pdfmetrics.registerFont(TTFont("DV", F_REG))
pdfmetrics.registerFont(TTFont("DVB", F_BOLD))

TINTA = "#0d1b3a"
GRIS = "#56647c"
LINEA = "#dde5f0"
ACENTO = "#1450B4"
NARANJA = "#F26A1B"
ACENTO_SOFT = "#E7EFFB"
ROJO = "#c53030"
NOTA_LEGAL = "Documento soporte del servicio. No es factura electrónica de venta."


def cop(v):
    v = v or 0
    return ("-$ " if v < -0.5 else "$ ") + f"{abs(v):,.0f}".replace(",", ".")


def cant(v):
    s = f"{v:,.2f}".rstrip("0").rstrip(".")
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _logo(negocio):
    """Logo de la empresa (subido en Datos del negocio) o el genérico de la app."""
    try:
        if getattr(negocio, "logo", None):
            return Image.open(io.BytesIO(negocio.logo)).convert("RGB")
        if HAY_LOGO:
            return Image.open(LOGO).convert("RGB")
    except Exception:
        pass
    return None


def _lineas_negocio(negocio):
    return [x for x in (
        " · ".join(x for x in (f"NIT {negocio.nit}" if negocio.nit else None, negocio.regimen) if x) or None,
        " · ".join(x for x in (negocio.direccion, negocio.ciudad) if x) or None,
        " · ".join(x for x in (f"Tel. {negocio.telefono}" if negocio.telefono else None, negocio.email) if x) or None,
    ) if x]


def _base(negocio, titulo, numero, fecha):
    return dict(negocio=negocio.nombre or "Mi negocio", lineas_neg=_lineas_negocio(negocio), titulo=titulo,
                numero=numero, fecha=fecha, logo=_logo(negocio), info=[], items=[], col_item="DESCRIPCIÓN",
                totales=[], total=0, total_label="TOTAL", resumen="", abonado=None, saldo=None, bloques=[],
                mensaje=negocio.mensaje or "", legal=NOTA_LEGAL, marca=None, firmas=None)


# ----------------------------------------------------------- datos por documento
def datos_orden(o, negocio, num):
    d = _base(negocio, (negocio.titulo_doc or "Orden de servicio").upper(), num,
              (o.fecha_fin or o.fecha).strftime("%d/%m/%Y %I:%M %p"))
    cli = o.cliente
    d["info"] = [("Cliente", o.cliente_nombre or (cli.nombre if cli else "")),
                 ("Servicio", o.tipo_txt),
                 ("Técnico", o.tecnico.nombre if o.tecnico else "—")]
    fila2 = []
    if cli and cli.identificacion:
        fila2.append(("C.C./NIT", cli.identificacion))
    if cli and cli.telefono:
        fila2.append(("Teléfono", cli.telefono))
    if o.direccion:
        fila2.append(("Dirección", o.direccion))
    d["info"] += fila2[:3]
    if o.estado == "terminada":
        pago = ("Crédito · vence " + o.vence.strftime("%d/%m/%Y")) if o.condicion == "credito" and o.vence else (o.metodo_pago or "")
        d["info"].append(("Estado", "Terminada"))
        d["info"].append(("Forma de pago", pago))
    else:
        d["info"].append(("Estado", o.estado_info[0]))
        d["info"].append(("Programada", o.fecha.strftime("%d/%m/%Y %I:%M %p")))
    if o.en_garantia:
        d["info"].append(("Garantía", f"Cubierta por la orden {o.garantia_de_num}" if getattr(o, "garantia_de_num", None) else "Servicio en garantía"))
    d["items"] = [dict(nombre=l.descripcion, detalle=(l.equipo.nombre if l.equipo and l.equipo.nombre not in l.descripcion else ""),
                       cant=l.cantidad, precio=l.precio, subtotal=l.subtotal) for l in o.lineas]
    d["items"] += [dict(nombre=m.insumo.nombre, detalle="Material / repuesto", cant=m.cantidad, unidad=m.insumo.unidad,
                        precio=m.precio_unitario, subtotal=m.subtotal) for m in o.materiales if m.subtotal or not o.en_garantia]
    if o.descuento or o.iva:
        d["totales"].append(("Subtotal", o.subtotal))
    if o.descuento:
        d["totales"].append(("Descuento", -o.descuento))
    if o.iva:
        d["totales"].append((f"IVA {o.iva_pct:g}%", o.iva))
    d["total"] = o.total_factura
    neq = len(o.equipos)
    d["resumen"] = f"{neq} equipo{'s' if neq != 1 else ''}" if neq else ""
    if o.condicion == "credito" and o.estado == "terminada":
        d["abonado"], d["saldo"] = o.pagado, o.saldo
    if o.descripcion:
        d["bloques"].append(("Solicitud del cliente", o.descripcion))
    if o.trabajo:
        d["bloques"].append(("Trabajo realizado", o.trabajo))
    if o.recomendaciones:
        d["bloques"].append(("Recomendaciones", o.recomendaciones))
    if o.garantia_dias and o.estado == "terminada" and o.garantia_hasta:
        d["bloques"].append(("Garantía", f"{o.garantia_dias} días sobre el trabajo realizado, hasta el "
                                         f"{o.garantia_hasta.strftime('%d/%m/%Y')}. No cubre daños por mal uso, "
                                         "variaciones de voltaje ni manipulación de terceros."))
    proximos = [e for e in o.equipos if e.proximo_preventivo and o.estado == "terminada"]
    if proximos:
        txt = "; ".join(f"{e.corto}: {e.proximo_preventivo.strftime('%d/%m/%Y')}" for e in proximos[:6])
        d["bloques"].append(("Próximo mantenimiento preventivo", txt))
    d["firmas"] = ("Firma del técnico", "Recibí a satisfacción (cliente)")
    if o.anulada:
        d["marca"] = "ANULADA"
    elif o.estado == "cancelada":
        d["marca"] = "CANCELADA"
    return d


def datos_cotizacion(c, negocio):
    d = _base(negocio, "COTIZACIÓN", c.numero, c.fecha.strftime("%d/%m/%Y"))
    cli = c.cliente
    d["info"] = [("Cliente", c.cliente_nombre or (cli.nombre if cli else "")), ("Servicio", c.tipo_txt),
                 ("Válida hasta", c.valida_hasta.strftime("%d/%m/%Y"))]
    if cli and (cli.telefono or cli.direccion_completa):
        d["info"] += [x for x in (("Teléfono", cli.telefono), ("Dirección", cli.direccion_completa)) if x[1]]
    if c.usuario:
        d["info"].append(("Elaboró", c.usuario.nombre))
    d["items"] = [dict(nombre=l.descripcion, detalle=(l.equipo.nombre if l.equipo else ""), cant=l.cantidad,
                       unidad=l.insumo.unidad if l.insumo else None, precio=l.precio, subtotal=l.subtotal)
                  for l in c.lineas]
    if c.descuento or c.iva:
        d["totales"].append(("Subtotal", c.subtotal))
    if c.descuento:
        d["totales"].append(("Descuento", -c.descuento))
    if c.iva:
        d["totales"].append((f"IVA {c.iva_pct:g}%", c.iva))
    d["total"] = c.total_factura
    d["total_label"] = "TOTAL COTIZADO"
    if c.descripcion:
        d["bloques"].append(("Descripción del trabajo", c.descripcion))
    if c.nota:
        d["bloques"].append(("Observaciones", c.nota))
    if negocio.condiciones_cot:
        d["bloques"].append(("Condiciones", negocio.condiciones_cot))
    d["mensaje"] = "Quedamos atentos a su aprobación."
    d["legal"] = "Cotización informativa. Los precios pueden cambiar después de la fecha de validez."
    return d


def datos_venta_equipo(v, negocio):
    d = _base(negocio, "RECIBO DE VENTA", v.numero, v.fecha.strftime("%d/%m/%Y %I:%M %p"))
    cli = v.cliente
    pago = ("Crédito · vence " + v.vence.strftime("%d/%m/%Y")) if v.condicion == "credito" and v.vence else (v.metodo_pago or "")
    d["info"] = [("Cliente", v.cliente_nombre or ""), ("Forma de pago", pago),
                 ("Atendió", v.usuario.nombre if v.usuario else "")]
    if cli and cli.identificacion:
        d["info"].append(("C.C./NIT", cli.identificacion))
    unit = (v.precio_total or 0) / (v.cantidad or 1)
    d["items"] = [dict(nombre=v.descripcion, detalle="Equipo nuevo", cant=v.cantidad, precio=unit,
                       subtotal=v.precio_total)]
    if v.iva:
        d["totales"] = [("Subtotal", v.precio_total), (f"IVA {v.iva_pct:g}%", v.iva)]
    d["total"] = v.total_factura
    d["resumen"] = f"{v.cantidad} equipo{'s' if v.cantidad != 1 else ''}"
    if v.condicion == "credito":
        d["abonado"], d["saldo"] = v.pagado, v.saldo
    if v.garantia_meses:
        d["bloques"].append(("Garantía", f"{v.garantia_meses} meses de garantía de fábrica según las condiciones "
                                         "del fabricante. Requiere instalación profesional y mantenimientos al día."))
    if v.nota:
        d["bloques"].append(("Nota", v.nota))
    if v.anulada:
        d["marca"] = "ANULADA"
    return d


# --------------------------------------------------------------------- PDF
def _encabezado_pdf(c, d, titulo, x0, x1, y):
    """Logo, datos del negocio, título, número y fecha. Devuelve la y más baja usada."""
    W, H = letter
    c.setFillColor(HexColor(ACENTO))
    c.rect(0, H - 8 * mm, W * .62, 8 * mm, stroke=0, fill=1)
    c.setFillColor(HexColor(NARANJA))
    c.rect(W * .62, H - 8 * mm, W * .38, 8 * mm, stroke=0, fill=1)
    xt = x0
    lg = d.get("logo")
    if lg:
        lw = 40 * mm
        c.drawImage(ImageReader(lg), x0, y - 22 * mm, width=lw, height=lw * lg.height / lg.width, mask=None)
        xt = x0 + lw + 5 * mm
    c.setFillColor(HexColor(TINTA))
    c.setFont("DVB", 17)
    c.drawString(xt, y - 2 * mm, d["negocio"][:38])
    c.setFont("DV", 9.5)
    c.setFillColor(HexColor(GRIS))
    yy = y - 8 * mm
    for ln in d["lineas_neg"]:
        c.drawString(xt, yy, ln[:70])
        yy -= 4.6 * mm
    if lg:
        yy = min(yy, y - 24 * mm)
    c.setFillColor(HexColor(TINTA))
    c.setFont("DVB", 12)
    c.drawRightString(x1, y, titulo)
    c.setFont("DVB", 16)
    c.setFillColor(HexColor(ACENTO))
    c.drawRightString(x1, y - 8 * mm, d["numero"])
    c.setFont("DV", 9.5)
    c.setFillColor(HexColor(GRIS))
    c.drawRightString(x1, y - 14 * mm, d["fecha"])
    return yy


def _wrap(texto, ancho_chars):
    out = []
    for parrafo in str(texto).splitlines() or [""]:
        out += textwrap.wrap(parrafo, ancho_chars) or [""]
    return out


def pdf(d):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(f"{d['titulo'].capitalize()} {d['numero']}")
    W, H = letter
    x0, x1 = 20 * mm, W - 20 * mm
    y = H - 22 * mm
    yy = _encabezado_pdf(c, d, d["titulo"], x0, x1, y)
    y = min(yy, y - 20 * mm) - 6 * mm

    def nueva_pagina():
        c.showPage()
        return H - 22 * mm

    # Cuadro de datos (3 columnas)
    info = d["info"]
    filas = (len(info) + 2) // 3
    alto = 6 * mm + filas * 11 * mm
    c.setStrokeColor(HexColor(LINEA))
    c.roundRect(x0, y - alto, x1 - x0, alto, 3 * mm, stroke=1, fill=0)
    cw = (x1 - x0) / 3
    for i, (k, v) in enumerate(info):
        cx = x0 + 5 * mm + (i % 3) * cw
        cy = y - 6 * mm - (i // 3) * 11 * mm
        c.setFont("DV", 7.5)
        c.setFillColor(HexColor(GRIS))
        c.drawString(cx, cy, k.upper())
        c.setFillColor(HexColor(TINTA))
        txt, tam, maxw = str(v or ""), 10, cw - 8 * mm
        while tam > 7.5 and pdfmetrics.stringWidth(txt, "DVB", tam) > maxw:
            tam -= 0.5
        while len(txt) > 3 and pdfmetrics.stringWidth(txt, "DVB", tam) > maxw:
            txt = txt[:-2].rstrip() + "…"
        c.setFont("DVB", tam)
        c.drawString(cx, cy - 5 * mm, txt)
    y -= alto + 9 * mm

    # Tabla
    cx_cant, cx_precio, cx_sub = x1 - 82 * mm, x1 - 38 * mm, x1
    c.setFont("DVB", 8.5)
    c.setFillColor(HexColor(GRIS))
    c.drawString(x0, y, d["col_item"])
    c.drawRightString(cx_cant, y, "CANT.")
    c.drawRightString(cx_precio, y, "VALOR UNIT.")
    c.drawRightString(cx_sub, y, "SUBTOTAL")
    y -= 3 * mm
    c.setStrokeColor(HexColor(TINTA))
    c.line(x0, y, x1, y)
    y -= 6.5 * mm
    c.setStrokeColor(HexColor(LINEA))
    for it in d["items"]:
        if y < 45 * mm:
            y = nueva_pagina()
        c.setFont("DV", 10)
        c.setFillColor(HexColor(TINTA))
        c.drawString(x0, y, it["nombre"][:56])
        q = cant(it["cant"]) + (f" {it['unidad']}" if it.get("unidad") and it["unidad"] != "unidad" else "")
        c.drawRightString(cx_cant, y, q)
        c.drawRightString(cx_precio, y, cop(it["precio"]))
        c.setFont("DVB", 10)
        c.drawRightString(cx_sub, y, cop(it["subtotal"]))
        if it.get("detalle"):
            y -= 4.3 * mm
            c.setFont("DV", 8.3)
            c.setFillColor(HexColor(GRIS))
            c.drawString(x0, y, it["detalle"][:80])
        y -= 3.5 * mm
        c.line(x0, y, x1, y)
        y -= 6 * mm

    if y < 70 * mm:
        y = nueva_pagina()
    for k, v in d["totales"]:
        c.setFont("DV", 10)
        c.setFillColor(HexColor(GRIS))
        c.drawString(x1 - 80 * mm, y, k)
        c.setFillColor(HexColor(TINTA))
        c.drawRightString(x1 - 5 * mm, y, cop(v))
        y -= 6 * mm
    y -= 2 * mm
    c.setFillColor(HexColor(ACENTO_SOFT))
    c.roundRect(x1 - 85 * mm, y - 14 * mm, 85 * mm, 16 * mm, 3 * mm, stroke=0, fill=1)
    if d["resumen"]:
        c.setFillColor(HexColor(GRIS))
        c.setFont("DV", 9)
        c.drawString(x1 - 80 * mm, y - 3.5 * mm, d["resumen"])
    c.setFillColor(HexColor(TINTA))
    c.setFont("DVB", 10.5)
    c.drawString(x1 - 80 * mm, y - 10 * mm, d["total_label"])
    c.setFont("DVB", 16)
    c.drawRightString(x1 - 5 * mm, y - 10 * mm, cop(d["total"]))
    ybloques = y
    y -= 22 * mm
    if d["saldo"] is not None:
        c.setFont("DV", 10)
        c.setFillColor(HexColor(GRIS))
        c.drawString(x1 - 80 * mm, y, "Abonado")
        c.drawRightString(x1 - 5 * mm, y, cop(d["abonado"]))
        y -= 6 * mm
        c.setFont("DVB", 10.5)
        c.setFillColor(HexColor(ROJO if d["saldo"] > 0.5 else TINTA))
        c.drawString(x1 - 80 * mm, y, "SALDO PENDIENTE")
        c.drawRightString(x1 - 5 * mm, y, cop(d["saldo"]))
        y -= 8 * mm

    # Bloques de texto (a la izquierda del total si caben, si no debajo)
    yb = ybloques
    ancho_txt = 58  # caracteres en la columna izquierda
    for titulo, texto in d["bloques"]:
        lineas = _wrap(texto, ancho_txt)
        if yb - (6 + len(lineas) * 4.3) * mm < 40 * mm:
            yb = min(yb, y)
            if yb - (6 + len(lineas) * 4.3) * mm < 40 * mm:
                yb = nueva_pagina()
                y = yb
        c.setFont("DVB", 8.5)
        c.setFillColor(HexColor(ACENTO))
        c.drawString(x0, yb, titulo.upper())
        yb -= 4.8 * mm
        c.setFont("DV", 9.3)
        c.setFillColor(HexColor(TINTA))
        for ln in lineas[:18]:
            c.drawString(x0, yb, ln)
            yb -= 4.3 * mm
        yb -= 3 * mm
    y = min(y, yb) - 2 * mm

    if d["firmas"]:
        y = max(min(y - 14 * mm, 62 * mm), 30 * mm)
        if y < 30 * mm:
            y = nueva_pagina() - 30 * mm
        for i, rot in enumerate(d["firmas"]):
            fx = x0 + i * ((x1 - x0) / 2 + 5 * mm)
            c.setStrokeColor(HexColor(TINTA))
            c.line(fx, y, fx + 70 * mm, y)
            c.setFont("DV", 9)
            c.setFillColor(HexColor(GRIS))
            c.drawString(fx, y - 5 * mm, rot)
        y -= 14 * mm
    if d["mensaje"]:
        c.setFont("DVB", 10.5)
        c.setFillColor(HexColor(TINTA))
        c.drawCentredString(W / 2, max(y, 22 * mm), d["mensaje"])
    c.setFont("DV", 8)
    c.setFillColor(HexColor(GRIS))
    c.drawCentredString(W / 2, 12 * mm, d["legal"])

    if d["marca"]:
        c.saveState()
        c.translate(W / 2, H / 2)
        c.rotate(30)
        c.setFillColor(HexColor(ROJO))
        c.setFillAlpha(0.18)
        c.setFont("DVB", 84)
        c.drawCentredString(0, 0, d["marca"])
        c.restoreState()
    c.showPage()
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------- JPG
def jpg(d):
    W, M = 1080, 64
    f = lambda size, bold=False: ImageFont.truetype(F_BOLD if bold else F_REG, size)
    F = dict(neg=f(52, True), small=f(29), lbl=f(26), val=f(32, True), item=f(34), itemb=f(34, True),
             sub=f(29), tot=f(62, True), totl=f(34, True), num=f(46, True), msg=f(32, True), legal=f(23),
             blk=f(27, True), txt=f(29))

    def ancho(txt, font):
        return ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(txt, font=font)

    def cortar(txt, font, maxw):
        out = []
        for parrafo in str(txt).splitlines() or [""]:
            palabras, actual = parrafo.split(), ""
            for p in palabras:
                prueba = (actual + " " + p).strip()
                if ancho(prueba, font) <= maxw:
                    actual = prueba
                else:
                    out.append(actual)
                    actual = p
            out.append(actual)
        return out

    LOGO_W = 520
    lg = d.get("logo")
    info = [(k, v) for k, v in d["info"] if v]
    alto_items = sum(44 * len(cortar(it["nombre"], F["itemb"], W - 2 * M - 280)) + 40 + (38 if it.get("detalle") else 0) + 34
                     for it in d["items"])
    alto_bloques = sum(44 + 40 * len(cortar(t, F["txt"], W - 2 * M)) + 24 for _, t in d["bloques"])
    H = (560 + (int(LOGO_W * lg.height / lg.width) + 30 if lg else 0) + 44 * len(d["lineas_neg"]) + 50 * len(info)
         + alto_items + 50 * len(d["totales"]) + 200 + (110 if d["saldo"] is not None else 0) + alto_bloques + 180)
    img = Image.new("RGB", (W, H), "white")
    g = ImageDraw.Draw(img)
    g.rectangle([0, 0, int(W * .62), 18], fill=ACENTO)
    g.rectangle([int(W * .62), 0, W, 18], fill=NARANJA)
    y = 64
    if lg:
        lg = lg.resize((LOGO_W, LOGO_W * lg.height // lg.width), Image.LANCZOS)
        img.paste(lg, ((W - LOGO_W) // 2, 46))
        y = 46 + lg.height + 26
    for ln in cortar(d["negocio"], F["neg"], W - 2 * M):
        g.text((W / 2, y), ln, font=F["neg"], fill=TINTA, anchor="mt")
        y += 62
    for ln in d["lineas_neg"]:
        g.text((W / 2, y), ln, font=F["small"], fill=GRIS, anchor="mt")
        y += 44
    y += 20
    g.line([M, y, W - M, y], fill=LINEA, width=3)
    y += 30
    g.text((W / 2, y), d["titulo"], font=F["lbl"], fill=GRIS, anchor="mt")
    y += 40
    g.text((W / 2, y), d["numero"], font=F["num"], fill=ACENTO, anchor="mt")
    y += 62
    g.text((W / 2, y), d["fecha"], font=F["small"], fill=GRIS, anchor="mt")
    y += 60
    for k, v in info:
        g.text((M, y), k, font=F["sub"], fill=GRIS)
        g.text((W - M, y), str(v)[:34], font=F["val"], fill=TINTA, anchor="ra")
        y += 50
    y += 14
    g.line([M, y, W - M, y], fill=TINTA, width=3)
    y += 28
    for it in d["items"]:
        for ln in cortar(it["nombre"], F["itemb"], W - 2 * M - 280):
            g.text((M, y), ln, font=F["itemb"], fill=TINTA)
            y += 44
        if it.get("detalle"):
            g.text((M, y), it["detalle"][:52], font=F["sub"], fill=GRIS)
            y += 38
        q = cant(it["cant"]) + (f" {it['unidad']}" if it.get("unidad") and it["unidad"] != "unidad" else "")
        g.text((M, y), f"{q} × {cop(it['precio'])}", font=F["sub"], fill=GRIS)
        g.text((W - M, y), cop(it["subtotal"]), font=F["itemb"], fill=TINTA, anchor="ra")
        y += 40
        g.line([M, y, W - M, y], fill=LINEA, width=2)
        y += 34
    y += 8
    for k, v in d["totales"]:
        g.text((M + 36, y), k, font=F["sub"], fill=GRIS)
        g.text((W - M - 36, y), cop(v), font=F["val"], fill=TINTA, anchor="ra")
        y += 50
    g.rounded_rectangle([M, y, W - M, y + 150], radius=24, fill=ACENTO_SOFT)
    g.text((M + 36, y + 34), d["total_label"], font=F["totl"], fill=TINTA)
    if d["resumen"]:
        g.text((M + 36, y + 84), d["resumen"], font=F["sub"], fill=GRIS)
    g.text((W - M - 36, y + 75), cop(d["total"]), font=F["tot"], fill=TINTA, anchor="rm")
    y += 176
    if d["saldo"] is not None:
        g.text((M + 36, y), "Abonado", font=F["sub"], fill=GRIS)
        g.text((W - M - 36, y), cop(d["abonado"]), font=F["val"], fill=TINTA, anchor="ra")
        y += 50
        col = ROJO if d["saldo"] > 0.5 else TINTA
        g.text((M + 36, y), "SALDO PENDIENTE", font=F["val"], fill=col)
        g.text((W - M - 36, y), cop(d["saldo"]), font=F["val"], fill=col, anchor="ra")
        y += 60
    y += 10
    for titulo, texto in d["bloques"]:
        g.text((M, y), titulo.upper(), font=F["blk"], fill=ACENTO)
        y += 44
        for ln in cortar(texto, F["txt"], W - 2 * M):
            g.text((M, y), ln, font=F["txt"], fill=TINTA)
            y += 40
        y += 24
    if d["mensaje"]:
        g.text((W / 2, y + 10), d["mensaje"], font=F["msg"], fill=TINTA, anchor="mt")
    g.text((W / 2, H - 50), d["legal"], font=F["legal"], fill=GRIS, anchor="mt")

    if d["marca"]:
        capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        t = Image.new("RGBA", (1000, 220), (0, 0, 0, 0))
        ImageDraw.Draw(t).text((500, 110), d["marca"], font=f(150, True), fill=(197, 48, 48, 95), anchor="mm")
        t = t.rotate(30, expand=True)
        capa.paste(t, ((W - t.width) // 2, (H - t.height) // 2), t)
        img = Image.alpha_composite(img.convert("RGBA"), capa).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()
