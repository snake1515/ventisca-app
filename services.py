"""Lógica de negocio: compras de materiales, órdenes de servicio, garantías, plan de
mantenimiento de los equipos, venta de equipos y cartera.

Cada función valida, mueve inventario y calcula costos. Si algo no cuadra lanza
ErrorNegocio con la lista de mensajes para mostrar al usuario. El commit lo hace quien llama.
"""
from datetime import timedelta

from models import (CON_GARANTIA, Abono, CompraInsumo, Equipo, Orden, OrdenLinea, OrdenMaterial,
                    VentaEquipo, ahora, db, sumar_meses)

TOL = 1e-9


class ErrorNegocio(Exception):
    def __init__(self, errores):
        self.errores = errores if isinstance(errores, list) else [errores]
        super().__init__("; ".join(self.errores))


def promedio_ponderado(stock, costo_actual, cantidad, costo_unitario):
    """Costo promedio ponderado al recibir una entrada de inventario."""
    stock = stock or 0
    nuevo = stock + cantidad
    if stock <= 0 or nuevo <= 0:
        return costo_unitario
    return (stock * (costo_actual or 0) + cantidad * costo_unitario) / nuevo


def _quitar_de_promedio(stock, costo_prom, cantidad, costo_unit):
    """Inverso del promedio ponderado: saca una entrada del costo promedio."""
    restante = (stock or 0) - cantidad
    if restante <= TOL:
        return costo_prom
    return max(((stock or 0) * (costo_prom or 0) - cantidad * costo_unit) / restante, 0)


def fmt_cop(v):
    return "$ " + f"{v or 0:,.0f}".replace(",", ".")


# ----------------------------------------------------------------- compras
def registrar_compra(insumo, cantidad, costo_total, proveedor="", nota="", fecha=None, usuario_id=None):
    if cantidad <= 0:
        raise ErrorNegocio(f"La cantidad de «{insumo.nombre}» debe ser mayor a 0.")
    if costo_total < 0:
        raise ErrorNegocio(f"El costo de «{insumo.nombre}» no puede ser negativo.")
    costo_unitario = costo_total / cantidad
    insumo.costo_promedio = promedio_ponderado(insumo.stock, insumo.costo_promedio, cantidad, costo_unitario)
    insumo.stock = (insumo.stock or 0) + cantidad
    compra = CompraInsumo(fecha=fecha or ahora(), insumo=insumo, cantidad=cantidad, costo_total=costo_total,
                          costo_unitario=costo_unitario, proveedor=proveedor or None, nota=nota or None,
                          usuario_id=usuario_id)
    db.session.add(compra)
    return compra


def anular_compra(compra):
    if compra.anulada:
        raise ErrorNegocio("Esta compra ya estaba anulada.")
    ins = compra.insumo
    if (ins.stock or 0) + TOL < compra.cantidad:
        raise ErrorNegocio(
            f"No se puede anular: de «{ins.nombre}» quedan {ins.stock:g} {ins.unidad} y la compra fue de "
            f"{compra.cantidad:g}. Parte ya se usó en órdenes de servicio.")
    ins.costo_promedio = _quitar_de_promedio(ins.stock, ins.costo_promedio, compra.cantidad, compra.costo_unitario)
    ins.stock = max((ins.stock or 0) - compra.cantidad, 0)
    compra.anulada = True


def ajustar_stock(insumo, contado):
    """Conteo físico: deja el stock en lo contado. Devuelve el valor de la diferencia (pérdida > 0)."""
    if contado < 0:
        raise ErrorNegocio("El conteo no puede ser negativo.")
    dif = (insumo.stock or 0) - contado
    insumo.stock = contado
    return dif * (insumo.costo_promedio or 0)


# --------------------------------------------------------- plan de mantenimiento
def recalcular_equipo(eq):
    """Último y próximo preventivo según las órdenes terminadas del equipo.

    Cuentan los preventivos y las instalaciones (la instalación arranca el plan).
    """
    if eq is None:
        return
    ult = (db.session.query(db.func.max(Orden.fecha_fin))
           .join(OrdenLinea, OrdenLinea.orden_id == Orden.id)
           .filter(OrdenLinea.equipo_id == eq.id, Orden.estado == "terminada", Orden.anulada.is_(False),
                   Orden.en_garantia.is_(False), Orden.tipo.in_(["preventivo", "instalacion"]))
           .scalar())
    ult = ult.date() if ult else None
    candidatos = [d for d in (ult, eq.ultimo_manual) if d]
    anterior = eq.ultimo_preventivo
    eq.ultimo_preventivo = max(candidatos) if candidatos else None
    if not eq.frecuencia_meses:
        eq.proximo_preventivo = None
    elif eq.ultimo_preventivo:
        eq.proximo_preventivo = sumar_meses(eq.ultimo_preventivo, eq.frecuencia_meses)
    elif eq.fecha_instalacion:
        eq.proximo_preventivo = sumar_meses(eq.fecha_instalacion, eq.frecuencia_meses)
    else:
        eq.proximo_preventivo = None
    if eq.ultimo_preventivo and eq.ultimo_preventivo != anterior:
        eq.pospuesto_hasta = None
        eq.aviso_fecha = None


def garantia_vigente(equipo, excluir_orden_id=None):
    """Última orden terminada con garantía vigente que incluye este equipo (o None)."""
    hoy = ahora().date()
    return (Orden.query.join(OrdenLinea, OrdenLinea.orden_id == Orden.id)
            .filter(OrdenLinea.equipo_id == equipo.id, Orden.estado == "terminada", Orden.anulada.is_(False),
                    Orden.garantia_hasta.isnot(None), Orden.garantia_hasta >= hoy,
                    Orden.id != (excluir_orden_id or 0))
            .order_by(Orden.fecha_fin.desc()).first())


# ----------------------------------------------------------- órdenes de servicio
def _revertir_inventario(o):
    """Devuelve a la bodega los materiales de una orden que ya los había descontado."""
    if not o.aplicada:
        return
    for m in o.materiales:
        ins = m.insumo
        ins.costo_promedio = promedio_ponderado(ins.stock, ins.costo_promedio, m.cantidad, m.costo_unitario)
        ins.stock = (ins.stock or 0) + m.cantidad
    o.aplicada = False


def _aplicar_inventario(o):
    """Descuenta los materiales de la bodega con su costo promedio actual."""
    if o.aplicada:
        return
    faltan = []
    por_insumo = {}
    for m in o.materiales:
        por_insumo[m.insumo] = por_insumo.get(m.insumo, 0) + m.cantidad
    for ins, cant in por_insumo.items():
        if cant > (ins.stock or 0) + TOL:
            faltan.append(f"De «{ins.nombre}» hay {ins.stock or 0:g} {ins.unidad} y la orden usa {cant:g}. "
                          "Registra la compra primero o corrige la cantidad.")
    if faltan:
        raise ErrorNegocio(faltan)
    for m in o.materiales:
        m.costo_unitario = m.insumo.costo_promedio or 0
        m.costo_total = m.cantidad * m.costo_unitario
        m.insumo.stock = (m.insumo.stock or 0) - m.cantidad
    o.aplicada = True


def recalcular_orden(o):
    for l in o.lineas:
        l.subtotal = (l.cantidad or 0) * (l.precio or 0)
    for m in o.materiales:
        m.subtotal = (m.cantidad or 0) * (m.precio_unitario or 0)
        if not o.aplicada:
            m.costo_unitario = m.insumo.costo_promedio or 0
        m.costo_total = (m.cantidad or 0) * (m.costo_unitario or 0)
    sub = o.subtotal
    o.descuento = min(max(o.descuento or 0, 0), sub)
    o.total = sub - o.descuento
    o.iva = round(o.total * (o.iva_pct or 0) / 100, 0)
    o.costo_materiales = sum(m.costo_total for m in o.materiales)
    o.ganancia = o.total - o.costo_materiales - (o.pago_tecnico or 0) - (o.otros_costos or 0)


def guardar_orden(o, lineas, materiales, estado, fecha_fin=None, plazo_dias=0):
    """Reemplaza servicios y materiales de la orden y aplica su estado.

    lineas: [(Equipo|None, descripcion, cantidad, precio)]
    materiales: [(Insumo, cantidad, precio_unitario)]
    """
    errores = []
    lineas = [x for x in lineas if (x[1] or "").strip() or x[0]]
    materiales = [x for x in materiales if x[0] and x[1] > 0]
    if not lineas:
        errores.append("Agrega al menos un servicio (por ejemplo, el mantenimiento de cada equipo).")
    for eq, desc, cant, precio in lineas:
        if cant <= 0:
            errores.append(f"La cantidad de «{desc or eq.corto}» debe ser mayor a 0.")
        if precio < 0:
            errores.append(f"El precio de «{desc or eq.corto}» no puede ser negativo.")
        if eq is not None and eq.cliente_id != o.cliente_id:
            errores.append(f"El equipo «{eq.corto}» no es de este cliente.")
    for ins, cant, precio in materiales:
        if precio < 0:
            errores.append(f"El precio de «{ins.nombre}» no puede ser negativo.")
    if min(o.pago_tecnico or 0, o.otros_costos or 0, o.descuento or 0) < 0:
        errores.append("Los valores no pueden ser negativos.")
    if not 0 <= (o.iva_pct or 0) <= 100:
        errores.append("El porcentaje de IVA no es válido.")
    if estado == "terminada" and o.condicion == "credito" and not o.cliente_id:
        errores.append("Para dejar a crédito, la orden debe tener cliente.")
    if errores:
        raise ErrorNegocio(errores)

    equipos_antes = {l.equipo for l in o.lineas if l.equipo}
    _revertir_inventario(o)
    o.lineas.clear()
    o.materiales.clear()
    db.session.flush()
    for eq, desc, cant, precio in lineas:
        o.lineas.append(OrdenLinea(equipo=eq, descripcion=(desc or "").strip()[:200] or
                                   f"{o.tipo_txt} · {eq.corto}", cantidad=cant,
                                   precio=0 if o.en_garantia else precio))
    for ins, cant, precio in materiales:
        o.materiales.append(OrdenMaterial(insumo=ins, cantidad=cant,
                                          precio_unitario=0 if o.en_garantia else precio))
    o.estado = estado
    if estado == "terminada":
        o.fecha_fin = fecha_fin or o.fecha_fin or ahora()
        _aplicar_inventario(o)
        o.garantia_hasta = (o.fecha_fin.date() + timedelta(days=o.garantia_dias)) if o.garantia_dias else None
        if o.condicion == "credito":
            o.vence = o.fecha_fin.date() + timedelta(days=max(int(plazo_dias or 0), 0))
        else:
            o.vence = None
    else:
        o.fecha_fin = None
        o.garantia_hasta = None
    recalcular_orden(o)
    db.session.flush()
    for eq in equipos_antes | {l.equipo for l in o.lineas if l.equipo}:
        recalcular_equipo(eq)
    return o


def terminar_orden(o, fecha_fin=None, trabajo=None):
    """Cierre rápido desde el detalle o desde el celular del técnico."""
    if o.anulada or o.estado in ("terminada", "cancelada"):
        raise ErrorNegocio("Esta orden ya está cerrada.")
    if trabajo is not None and trabajo.strip():
        o.trabajo = trabajo.strip()
    o.estado = "terminada"
    o.fecha_fin = fecha_fin or ahora()
    _aplicar_inventario(o)
    o.garantia_hasta = (o.fecha_fin.date() + timedelta(days=o.garantia_dias)) if o.garantia_dias else None
    if o.condicion == "credito":
        plazo = (o.cliente.plazo_dias if o.cliente else 0) or 0
        o.vence = o.fecha_fin.date() + timedelta(days=plazo)
    recalcular_orden(o)
    db.session.flush()
    for eq in o.equipos:
        recalcular_equipo(eq)


def cambiar_estado(o, estado):
    if o.anulada:
        raise ErrorNegocio("La orden está anulada.")
    if estado == "terminada":
        return terminar_orden(o)
    if o.estado == "terminada":
        if sum(a.valor for a in o.abonos if not a.anulado) > 0.5:
            raise ErrorNegocio("La orden tiene abonos; anúlalos primero para reabrirla.")
        _revertir_inventario(o)
        o.fecha_fin = None
        o.garantia_hasta = None
    o.estado = estado
    recalcular_orden(o)
    db.session.flush()
    for eq in o.equipos:
        recalcular_equipo(eq)


def anular_orden(o):
    if o.anulada:
        raise ErrorNegocio("Esta orden ya estaba anulada.")
    _revertir_inventario(o)
    for a in o.abonos:
        a.anulado = True
    o.anulada = True
    db.session.flush()
    for eq in o.equipos:
        recalcular_equipo(eq)


def garantia_por_defecto(tipo, cfg):
    return cfg.garantia_dias if tipo in CON_GARANTIA else 0


# ------------------------------------------------------------ venta de equipos
def calcular_venta_equipo(v):
    v.iva = round((v.precio_total or 0) * (v.iva_pct or 0) / 100, 0)
    v.ganancia = (v.precio_total or 0) - (v.costo_total or 0) - (v.otros_costos or 0)


def validar_venta_equipo(v):
    errores = []
    if not (v.cliente_nombre or "").strip():
        errores.append("Escribe o elige el cliente.")
    if (v.cantidad or 0) < 1:
        errores.append("La cantidad debe ser al menos 1.")
    if (v.precio_total or 0) <= 0:
        errores.append("Escribe el precio de venta al cliente.")
    if (v.costo_total or 0) < 0 or (v.otros_costos or 0) < 0:
        errores.append("Los costos no pueden ser negativos.")
    if not 0 <= (v.iva_pct or 0) <= 100:
        errores.append("El porcentaje de IVA no es válido.")
    if errores:
        raise ErrorNegocio(errores)


def anular_venta_equipo(v):
    if v.anulada:
        raise ErrorNegocio("Esta venta ya estaba anulada.")
    for a in v.abonos:
        a.anulado = True
    v.anulada = True


# ------------------------------------------------------------------ cartera
def registrar_abono(doc, valor, metodo="Efectivo", nota="", fecha=None, usuario_id=None):
    """doc: Orden o VentaEquipo a crédito."""
    if doc.anulada:
        raise ErrorNegocio("El documento está anulado; no recibe abonos.")
    if isinstance(doc, Orden) and doc.estado != "terminada":
        raise ErrorNegocio("Solo se reciben abonos de órdenes terminadas.")
    if doc.condicion != "credito":
        raise ErrorNegocio("Fue de contado: ya está pagada.")
    if valor <= 0:
        raise ErrorNegocio("El valor del abono debe ser mayor a 0.")
    if valor > doc.saldo + 0.5:
        raise ErrorNegocio(f"El abono supera el saldo pendiente ({fmt_cop(doc.saldo)}).")
    a = Abono(fecha=fecha or ahora(), valor=valor, metodo=metodo, nota=(nota or "").strip()[:255] or None,
              usuario_id=usuario_id)
    doc.abonos.append(a)
    return a
