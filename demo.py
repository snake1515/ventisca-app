"""Datos de ejemplo (solo para pruebas y capturas del manual; la app del cliente no los usa): una empresa de mantenimiento de aires en Cúcuta
con 60 días de movimiento (clientes, equipos, órdenes, cotizaciones, ventas de equipos,
compras, gastos, nómina y agenda de la próxima semana).

Uso por consola en un computador de pruebas: python demo.py
"""
import random
from datetime import datetime, timedelta

from models import (Abono, Activo, Cliente, Configuracion, Cotizacion, CotizacionLinea, DatosNegocio, Empleado,
                    Equipo, Gasto, Insumo, Orden, Usuario, VentaEquipo, ahora, db, sumar_meses)
from services import (calcular_venta_equipo, guardar_orden, recalcular_equipo, registrar_compra)

MATERIALES = [  # nombre, categoría, unidad, costo, precio al cliente, mínimo, compra inicial
    ("Gas refrigerante R-410A", "Gas refrigerante", "kilogramo", 42000, 120000, 3, 11.3),
    ("Gas refrigerante R-22", "Gas refrigerante", "kilogramo", 55000, 140000, 2, 6.8),
    ("Gas refrigerante R-32", "Gas refrigerante", "kilogramo", 46000, 125000, 2, 9),
    ("Tubería de cobre 1/4\"", "Tubería y aislamiento", "metro", 9500, 22000, 10, 30),
    ("Tubería de cobre 3/8\"", "Tubería y aislamiento", "metro", 13500, 28000, 10, 30),
    ("Tubería de cobre 1/2\"", "Tubería y aislamiento", "metro", 18000, 35000, 6, 15),
    ("Aislante térmico (cañuela)", "Tubería y aislamiento", "metro", 2800, 7000, 15, 50),
    ("Capacitor 35+5 µF", "Capacitores y contactores", "unidad", 18000, 45000, 3, 8),
    ("Capacitor 45+5 µF", "Capacitores y contactores", "unidad", 21000, 50000, 3, 6),
    ("Capacitor ventilador 2,5 µF", "Capacitores y contactores", "unidad", 7000, 25000, 3, 8),
    ("Contactor 30 A", "Capacitores y contactores", "unidad", 32000, 75000, 2, 4),
    ("Sensor de temperatura", "Tarjetas y sensores", "unidad", 12000, 40000, 2, 6),
    ("Tarjeta universal mini split", "Tarjetas y sensores", "unidad", 95000, 220000, 1, 3),
    ("Motor ventilador condensadora", "Motores y ventiladores", "unidad", 120000, 260000, 1, 2),
    ("Limpiador de serpentín", "Filtros y limpieza", "litro", 9000, 0, 4, 12),
    ("Filtro de aire universal", "Filtros y limpieza", "unidad", 8000, 22000, 4, 12),
    ("Soporte para condensadora", "Soportes y fijaciones", "juego", 38000, 80000, 2, 6),
    ("Cable encauchetado 3x14", "Cables, cinta y misceláneos", "metro", 3800, 9000, 15, 50),
    ("Cinta de aislar / cinta vinipel", "Cables, cinta y misceláneos", "rollo", 3500, 0, 5, 20),
    ("Manguera de drenaje", "Tubería y aislamiento", "metro", 2200, 6000, 10, 30),
]

CLIENTES = [  # nombre, tipo, barrio, plazo crédito, equipos [(tipo, btu, marca, ubicación)], frecuencia
    ("María Fernanda Ortiz", "Hogar", "Caobos", 0, [("Mini split (pared)", 12000, "LG", "Alcoba principal"),
                                                    ("Mini split (pared)", 9000, "LG", "Alcoba niños")], 6),
    ("Jorge Iván Peñaranda", "Hogar", "La Riviera", 0, [("Mini split (pared)", 18000, "Samsung", "Sala")], 6),
    ("Panadería La Espiga", "Comercio / local", "Centro", 15, [("Piso-techo", 36000, "Carrier", "Local"),
                                                               ("Mini split (pared)", 12000, "Midea", "Oficina")], 3),
    ("Clínica Odontológica Sonríe", "Institución (colegio, clínica…)", "Quinta Oriental", 30,
     [("Cassette", 24000, "Daikin", "Recepción"), ("Mini split (pared)", 12000, "Daikin", "Consultorio 1"),
      ("Mini split (pared)", 12000, "Daikin", "Consultorio 2")], 3),
    ("Carolina Rangel", "Hogar", "Prados del Este", 0, [("Mini split (pared)", 12000, "Hisense", "Alcoba")], 6),
    ("Ferretería El Tornillo", "Comercio / local", "La Libertad", 15, [("Mini split (pared)", 24000, "Gree", "Almacén")], 4),
    ("Colegio Nuevo Horizonte", "Institución (colegio, clínica…)", "Atalaya", 30,
     [("Mini split (pared)", 24000, "LG", "Sala de profesores"), ("Mini split (pared)", 18000, "LG", "Rectoría"),
      ("Mini split (pared)", 24000, "LG", "Sala de sistemas"), ("Piso-techo", 36000, "Carrier", "Auditorio")], 4),
    ("Luis Alberto Contreras", "Hogar", "Los Patios", 0, [("Ventana", 12000, "Whirlpool", "Alcoba")], 12),
    ("Conjunto Torres del Parque", "Conjunto / edificio", "El Rosal", 30,
     [("Mini split (pared)", 18000, "Samsung", "Portería"), ("Mini split (pared)", 24000, "Samsung", "Salón social")], 3),
    ("Restaurante Sabor Santandereano", "Comercio / local", "Ceiba II", 15,
     [("Piso-techo", 48000, "Carrier", "Comedor"), ("Cuarto frío / refrigeración", None, "Danfoss", "Cocina")], 3),
    ("Droguería Vida Sana", "Comercio / local", "San Luis", 0, [("Mini split (pared)", 18000, "Midea", "Mostrador")], 4),
    ("Andrea Patricia Villamizar", "Hogar", "Guaimaral", 0, [("Mini split (pared)", 12000, "Samsung", "Alcoba"),
                                                           ("Mini split (pared)", 12000, "Samsung", "Estudio")], 6),
    ("Oficinas Grupo Andino", "Oficina / empresa", "Av. 0", 30,
     [("Cassette", 36000, "LG", "Sala de juntas"), ("Mini split (pared)", 24000, "LG", "Oficina abierta"),
      ("Mini split (pared)", 12000, "LG", "Gerencia")], 3),
    ("Héctor Julio Suárez", "Hogar", "Villa del Rosario", 0, [("Mini split (pared)", 24000, "Electrolux", "Sala")], 6),
    ("Gimnasio PowerFit", "Comercio / local", "Lleras", 15, [("Piso-techo", 60000, "Carrier", "Salón de máquinas"),
                                                           ("Mini split (pared)", 18000, "Gree", "Recepción")], 3),
    ("Diana Marcela Jaimes", "Hogar", "Prados Norte", 0, [("Mini split (pared)", 9000, "Midea", "Alcoba")], 6),
    ("Hotel Casa Real", "Comercio / local", "Centro", 30,
     [("Mini split (pared)", 12000, "Hisense", f"Habitación {n}") for n in (101, 102, 103, 201, 202, 203)], 3),
    ("Pedro Antonio Gélvez", "Hogar", "Barrio Blanco", 0, [("Mini split (pared)", 12000, "Panasonic", "Alcoba")], 6),
    ("Laboratorio Clínico Bioanálisis", "Institución (colegio, clínica…)", "Caobos", 30,
     [("Mini split (pared)", 18000, "Daikin", "Área de muestras"), ("Mini split (pared)", 12000, "Daikin", "Recepción")], 3),
    ("Sandra Milena Pabón", "Hogar", "El Bosque", 0, [("Mini split (pared)", 18000, "LG", "Sala")], 6),
    ("Tienda de Ropa Moda Única", "Comercio / local", "Centro Comercial Ventura", 15,
     [("Mini split (pared)", 24000, "Samsung", "Local")], 4),
    ("Ricardo Andrés Mora", "Hogar", "Los Acacios", 0, [], 6),
]

NOMBRES_CALLE = ["Calle 10 # 4-25", "Av. 4 # 12-40", "Calle 15 # 1E-32", "Av. 1E # 9-80", "Calle 7N # 3-15",
                 "Av. 11E # 7-20", "Calle 2 # 8-66", "Av. 6 # 14-09", "Calle 18 # 2-41", "Av. 0 # 13-55"]

PRECIO_PREV = {9000: 90000, 12000: 100000, 18000: 120000, 24000: 140000, 36000: 200000, 48000: 240000, 60000: 280000}


def _precio_prev(eq):
    if eq.tipo == "Cuarto frío / refrigeración":
        return 260000
    base = PRECIO_PREV.get(eq.capacidad_btu or 12000, 120000)
    if eq.tipo == "Cassette":
        base += 40000
    if eq.tipo == "Ventana":
        base -= 15000
    return base


def cargar(empresa, admin):
    rnd = random.Random(empresa.id * 7919 + 13)
    hoy = ahora()
    dia0 = hoy - timedelta(days=60)

    neg = DatosNegocio.query.first() or DatosNegocio()
    neg.nombre = empresa.nombre
    neg.nit = neg.nit or "901.456.789-3"
    neg.direccion = neg.direccion or "Av. 5 # 10-32, local 2"
    neg.ciudad = neg.ciudad or "Cúcuta"
    neg.telefono = neg.telefono or empresa.telefono or "310 555 0199"
    neg.condiciones_cot = neg.condiciones_cot or ("Forma de pago: 50% para iniciar y 50% al terminar. "
                                                  "Garantía de 30 días sobre la mano de obra. "
                                                  "No incluye obras civiles ni resanes.")
    db.session.add(neg)
    cfg = Configuracion.actual()
    cfg.dias_aviso, cfg.garantia_dias, cfg.frecuencia_def = 15, 30, 6

    # Técnicos y usuarios de ejemplo
    slug = empresa.slug
    u_tec = Usuario(nombre="Carlos Méndez", username=f"{slug}.tecnico", rol="tecnico", empresa_id=empresa.id)
    u_tec.set_password("demo1234")
    u_ase = Usuario(nombre="Paola Cárdenas", username=f"{slug}.asesor", rol="asesor", empresa_id=empresa.id)
    u_ase.set_password("demo1234")
    for u in (u_tec, u_ase):
        if not Usuario.query.filter_by(username=u.username).first():
            db.session.add(u)
    db.session.flush()
    carlos = Empleado(nombre="Carlos Méndez", cargo="Técnico líder", telefono="315 420 1188", tipo_pago="comision",
                      comision_tipo="pct", comision_valor=40, fecha_ingreso=(hoy - timedelta(days=700)).date(),
                      usuario_id=u_tec.id, documento="1.090.456.221")
    andres = Empleado(nombre="Andrés Rojas", cargo="Técnico", telefono="320 667 4410", tipo_pago="mixto",
                      salario=1750905, comision_tipo="pct", comision_valor=15, documento="1.093.778.104",
                      fecha_ingreso=(hoy - timedelta(days=320)).date())
    luis = Empleado(nombre="Luis Pabón", cargo="Ayudante", telefono="311 209 5532", tipo_pago="comision",
                    comision_tipo="fijo", comision_valor=25000, documento="1.094.110.876",
                    fecha_ingreso=(hoy - timedelta(days=90)).date())
    tecnicos = [carlos, andres, luis]
    db.session.add_all(tecnicos)

    # Materiales con su inventario inicial
    mats = {}
    for nombre, cat, uni, costo, precio, minimo, cant in MATERIALES:
        ins = Insumo(nombre=nombre, categoria=cat, unidad=uni, precio_venta=precio, stock_minimo=minimo)
        db.session.add(ins)
        db.session.flush()
        registrar_compra(ins, cant, round(costo * cant), "Distribuidora Refrigeración del Oriente",
                         "Inventario inicial", dia0 - timedelta(days=2), admin.id)
        mats[nombre] = ins

    # Herramientas
    for nombre, valor, meses, dias_atras in (("Bomba de vacío 6 CFM", 980000, 48, 400),
                                             ("Manifold digital", 1450000, 48, 250),
                                             ("Recuperadora de refrigerante", 2300000, 60, 520),
                                             ("Hidrolavadora para serpentines", 690000, 36, 180),
                                             ("Escalera de tijera 8 pasos", 520000, 60, 610),
                                             ("Moto Honda CB 125 (domicilios técnicos)", 7800000, 60, 800)):
        db.session.add(Activo(nombre=nombre, valor=valor, vida_util_meses=meses, valor_residual=round(valor * .1),
                              fecha_compra=(hoy - timedelta(days=dias_atras)).date(), proveedor="Varios"))

    # Clientes y equipos
    clientes = []
    for i, (nombre, tipo, barrio, plazo, eqs, frec) in enumerate(CLIENTES):
        c = Cliente(nombre=nombre, tipo=tipo, barrio=barrio, ciudad="Cúcuta", plazo_dias=plazo,
                    direccion=NOMBRES_CALLE[i % len(NOMBRES_CALLE)],
                    telefono=f"3{rnd.choice(['00', '01', '10', '13', '15', '16', '17', '20', '22'])} "
                             f"{rnd.randint(200, 899)} {rnd.randint(1000, 9999)}",
                    contacto=("Administración" if tipo != "Hogar" else None),
                    identificacion=(f"{rnd.randint(800, 901)}.{rnd.randint(100, 999)}.{rnd.randint(100, 999)}-{rnd.randint(0, 9)}"
                                    if tipo != "Hogar" else None),
                    creado=dia0 - timedelta(days=rnd.randint(30, 400)))
        db.session.add(c)
        db.session.flush()
        for tipo_eq, btu, marca, ubic in eqs:
            # Último preventivo antes de usar la app: repartido para que haya vencidos, próximos y al día
            atras = rnd.randint(20, frec * 30 + 40)
            e = Equipo(cliente_id=c.id, tipo=tipo_eq, capacidad_btu=btu, marca=marca, ubicacion=ubic,
                       refrigerante=rnd.choice(["R-410A", "R-410A", "R-32", "R-22"]) if btu else "R-404A",
                       inverter=rnd.random() < .4, frecuencia_meses=frec,
                       ultimo_manual=(dia0 - timedelta(days=max(atras - 60, 5))).date(),
                       fecha_instalacion=(dia0 - timedelta(days=rnd.randint(200, 1500))).date())
            db.session.add(e)
        clientes.append(c)
    db.session.flush()
    for e in Equipo.query.all():
        recalcular_equipo(e)

    def costo_tecnico(tec, mano_obra):
        return tec.comision_para(mano_obra)

    # Órdenes de los últimos 60 días: se recorren los días y se atienden equipos que tocaban
    n_ordenes = 0
    terminadas = []
    # Clientes que no se atienden en estos 60 días: quedan con el preventivo vencido o por vencer
    pendientes = {clientes[i].id: dias for i, dias in ((0, -18), (4, -6), (7, 3), (10, 9), (15, -32), (17, 12), (19, 0))
                  if i < len(clientes)}
    atendibles = [c for c in clientes if c.id not in pendientes]
    for d in range(60):
        dia = dia0 + timedelta(days=d)
        if dia.weekday() == 6:
            continue
        for _ in range(rnd.choice([1, 1, 2, 2, 2, 3])):
            cli = rnd.choice(atendibles)
            eqs = [e for e in cli.equipos if e.activo]
            if not eqs:
                continue
            r = rnd.random()
            tec = rnd.choice([carlos, carlos, andres])
            hora = dia.replace(hour=rnd.choice([8, 9, 10, 11, 14, 15, 16]), minute=rnd.choice([0, 30]), second=0)
            fin = hora + timedelta(hours=rnd.choice([1, 2, 2, 3]))
            if fin > hoy:
                continue
            o = Orden(cliente_id=cli.id, cliente_nombre=cli.nombre, direccion=cli.direccion_completa, fecha=hora,
                      tecnico_id=tec.id, usuario_id=admin.id, metodo_pago=rnd.choice(["Efectivo", "Nequi", "Transferencia", "Efectivo"]))
            lineas, materiales = [], []
            if r < .62:  # preventivo de varios o todos los equipos
                o.tipo = "preventivo"
                sel = eqs if len(eqs) <= 3 or rnd.random() < .6 else rnd.sample(eqs, rnd.randint(2, len(eqs)))
                for e in sel:
                    lineas.append((e, f"Mantenimiento preventivo · {e.corto}", 1, _precio_prev(e)))
                o.descripcion = "Mantenimiento preventivo programado."
                o.trabajo = ("Lavado de evaporadora y condensadora con hidrolavadora, limpieza de filtros y bandeja, "
                             "revisión de presiones, amperaje y drenaje. Equipos funcionando correctamente.")
                o.recomendaciones = rnd.choice(["Limpiar los filtros cada 15 días.", "Mantener la temperatura entre 22 y 24 °C.",
                                                "Revisar el drenaje; hay acumulación de sarro.", None])
                if rnd.random() < .2:
                    materiales.append((mats["Filtro de aire universal"], 1, 22000))
            elif r < .82:  # correctivo
                o.tipo = "correctivo"
                e = rnd.choice(eqs)
                falla = rnd.choice([
                    ("No enfría", "Capacitor del compresor dañado. Se reemplaza y se completa carga de gas.",
                     [("Capacitor 35+5 µF", 1), ("Gas refrigerante R-410A", .5)], 120000),
                    ("Gotea agua por la evaporadora", "Drenaje obstruido. Se destapa y se reemplaza manguera.",
                     [("Manguera de drenaje", 3)], 80000),
                    ("No enciende", "Sensor de temperatura defectuoso. Se reemplaza y se prueba.",
                     [("Sensor de temperatura", 1)], 90000),
                    ("Hace ruido la condensadora", "Capacitor del ventilador débil. Se reemplaza.",
                     [("Capacitor ventilador 2,5 µF", 1)], 80000),
                    ("Enfría poco", "Fuga en abocinado. Se rehace abocinado, vacío y carga de gas.",
                     [("Gas refrigerante R-410A", 1)], 150000),
                ])
                o.descripcion, o.trabajo = falla[0], falla[1]
                lineas.append((e, f"Reparación · {e.corto}", 1, falla[3]))
                for nombre_m, cant in falla[2]:
                    m = mats[nombre_m]
                    materiales.append((m, cant, m.precio_venta))
                o.garantia_dias = cfg.garantia_dias
            elif r < .9:  # diagnóstico
                o.tipo = "diagnostico"
                e = rnd.choice(eqs)
                o.descripcion = "El cliente reporta que el equipo se apaga solo."
                o.trabajo = "Se revisa tarjeta y sensores. Se recomienda cambio de tarjeta; se envía cotización."
                lineas.append((e, f"Visita técnica y diagnóstico · {e.corto}", 1, 60000))
            else:  # carga de gas
                o.tipo = "gas"
                e = rnd.choice(eqs)
                o.descripcion = "Equipo enfría poco."
                o.trabajo = "Prueba de fugas con nitrógeno, vacío y carga completa de refrigerante."
                gas = mats["Gas refrigerante R-22"] if e.refrigerante == "R-22" else mats["Gas refrigerante R-410A"]
                lineas.append((e, f"Carga de gas · {e.corto}", 1, 90000))
                materiales.append((gas, 1, gas.precio_venta))
                o.garantia_dias = cfg.garantia_dias
            mano = sum(p * c for _, _, c, p in lineas)
            o.pago_tecnico = costo_tecnico(tec, mano)
            o.otros_costos = rnd.choice([0, 0, 8000, 12000])
            if cli.plazo_dias and rnd.random() < .7:
                o.condicion = "credito"
            # Que no falte material
            for m, cant, _ in materiales:
                if (m.stock or 0) < cant + 1:
                    registrar_compra(m, 5, round(5 * (m.costo_promedio or 10000)), "Distribuidora Refrigeración del Oriente",
                                     "", hora - timedelta(hours=3), admin.id)
            db.session.add(o)
            guardar_orden(o, lineas, materiales, "terminada", fecha_fin=fin, plazo_dias=cli.plazo_dias)
            if o.condicion == "credito" and (hoy - fin).days > 10 and rnd.random() < .6:
                pago = o.total_factura if rnd.random() < .7 else round(o.total_factura * .5, -3)
                o.abonos.append(Abono(fecha=fin + timedelta(days=rnd.randint(5, 25)), valor=pago,
                                      metodo="Transferencia", usuario_id=admin.id))
            terminadas.append(o)
            n_ordenes += 1
        db.session.flush()

    # Visitas en garantía de algunos correctivos
    for o in [x for x in terminadas if x.tipo == "correctivo"][:3]:
        if (hoy - o.fecha_fin).days < 8:
            continue
        g_fecha = o.fecha_fin + timedelta(days=rnd.randint(4, 7))
        if g_fecha > hoy - timedelta(hours=3):
            continue
        g = Orden(tipo="correctivo", cliente_id=o.cliente_id, cliente_nombre=o.cliente_nombre, direccion=o.direccion,
                  fecha=g_fecha, tecnico_id=o.tecnico_id, usuario_id=admin.id, en_garantia=True,
                  garantia_de_id=o.id, descripcion="El equipo volvió a presentar la falla.",
                  trabajo="Se ajusta conexión del capacitor y se verifica funcionamiento. Sin costo por garantía.")
        db.session.add(g)
        guardar_orden(g, [(o.equipos[0], f"Visita en garantía · {o.equipos[0].corto}", 1, 0)], [], "terminada",
                      fecha_fin=g_fecha + timedelta(hours=1))

    for cid, dias in pendientes.items():
        for e in db.session.get(Cliente, cid).equipos:
            # último preventivo para que el próximo caiga en «hoy + dias»
            e.ultimo_manual = sumar_meses((hoy + timedelta(days=dias)).date(), -e.frecuencia_meses)
            recalcular_equipo(e)
    db.session.flush()

    # Agenda: órdenes programadas para los próximos días (equipos que se vencen)
    por_vencer = (Equipo.query.filter(Equipo.activo.is_(True), Equipo.proximo_preventivo.isnot(None),
                                      Equipo.proximo_preventivo <= (hoy + timedelta(days=20)).date())
                  .order_by(Equipo.proximo_preventivo).all())
    por_cliente = {}
    for e in por_vencer:
        por_cliente.setdefault(e.cliente_id, []).append(e)
    agendar = [x for x in por_cliente.items() if x[0] not in pendientes][:5]
    for k, (cid, eqs) in enumerate(agendar[:7]):
        cli = db.session.get(Cliente, cid)
        dia = (hoy + timedelta(days=k // 2)).replace(hour=[8, 10, 14, 16][k % 4], minute=0, second=0)
        if dia.weekday() == 6:
            dia += timedelta(days=1)
        o = Orden(tipo="preventivo", cliente_id=cid, cliente_nombre=cli.nombre, direccion=cli.direccion_completa,
                  fecha=dia, tecnico_id=rnd.choice([carlos, andres]).id, usuario_id=admin.id,
                  descripcion="Mantenimiento preventivo programado.")
        db.session.add(o)
        guardar_orden(o, [(e, f"Mantenimiento preventivo · {e.corto}", 1, _precio_prev(e)) for e in eqs], [], "programada")
    # Un correctivo programado para hoy
    cli = clientes[3]
    o = Orden(tipo="correctivo", cliente_id=cli.id, cliente_nombre=cli.nombre, direccion=cli.direccion_completa,
              fecha=hoy.replace(hour=15, minute=30, second=0), tecnico_id=carlos.id, usuario_id=admin.id,
              descripcion="El aire del consultorio 2 no enfría.", garantia_dias=cfg.garantia_dias)
    db.session.add(o)
    guardar_orden(o, [(cli.equipos[2], f"Reparación · {cli.equipos[2].corto}", 1, 120000)], [], "programada")

    # Cotizaciones
    cot_items = [
        (clientes[6], "instalacion", "Instalación de 2 aires nuevos en salones de clase (suministra el colegio).",
         [("serv", "Instalación de mini split 24.000 BTU (hasta 3 m de tubería)", 2, 320000),
          ("mat", "Tubería de cobre 3/8\"", 6, None), ("mat", "Tubería de cobre 1/4\"", 6, None),
          ("mat", "Soporte para condensadora", 2, None), ("mat", "Cable encauchetado 3x14", 10, None)], "aprobada"),
        (clientes[12], "correctivo", "Cambio de tarjeta del aire de gerencia.",
         [("mat", "Tarjeta universal mini split", 1, None), ("serv", "Mano de obra: cambio de tarjeta y pruebas", 1, 120000)],
         "pendiente"),
        (clientes[9], "preventivo", "Plan anual de mantenimiento trimestral para los equipos del restaurante.",
         [("serv", "Mantenimiento preventivo piso-techo 48.000 BTU (4 visitas)", 4, 230000),
          ("serv", "Mantenimiento cuarto frío (4 visitas)", 4, 250000)], "pendiente"),
        (clientes[1], "correctivo", "Cambio de motor ventilador de la condensadora.",
         [("mat", "Motor ventilador condensadora", 1, None), ("serv", "Mano de obra", 1, 100000)], "rechazada"),
        (clientes[14], "correctivo", "Reparación de fuga y carga de gas del equipo de la recepción.",
         [("serv", "Detección y reparación de fuga", 1, 150000), ("mat", "Gas refrigerante R-410A", 1.5, None)], "pendiente"),
    ]
    for k, (cli, tipo, desc, lineas, estado) in enumerate(cot_items):
        c = Cotizacion(cliente_id=cli.id, cliente_nombre=cli.nombre, tipo=tipo, descripcion=desc, estado=estado,
                       fecha=hoy - timedelta(days=[12, 6, 3, 20, 1][k]), validez_dias=15, usuario_id=admin.id)
        for t, d, cant, precio in lineas:
            if t == "mat":
                m = mats[d]
                c.lineas.append(CotizacionLinea(insumo=m, descripcion=m.nombre, cantidad=cant, precio=m.precio_venta,
                                                subtotal=cant * m.precio_venta))
            else:
                c.lineas.append(CotizacionLinea(descripcion=d, cantidad=cant, precio=precio, subtotal=cant * precio))
        c.total = sum(l.subtotal for l in c.lineas)
        db.session.add(c)

    # Compra-venta de equipos
    ventas = [(clientes[21], "Mini split (pared)", 12000, "Samsung", "Wind-Free", True, 1, 1650000, 2150000, 14),
              (clientes[4], "Mini split (pared)", 12000, "LG", "DualCool", True, 1, 1720000, 2290000, 40),
              (clientes[13], "Mini split (pared)", 18000, "Midea", "Xtreme Save", False, 1, 1890000, 2450000, 25),
              (clientes[16], "Mini split (pared)", 12000, "Hisense", "Inverter Silent", True, 2, 3100000, 4100000, 50),
              (clientes[19], "Mini split (pared)", 24000, "Gree", "Pular", False, 1, 2350000, 3050000, 8)]
    for cli, tipo, btu, marca, modelo, inv, cant, costo, precio, dias_atras in ventas:
        v = VentaEquipo(cliente_id=cli.id, cliente_nombre=cli.nombre, tipo=tipo, capacidad_btu=btu, marca=marca,
                        modelo=modelo, inverter=inv, cantidad=cant, proveedor="Climatización Total S.A.S.",
                        costo_total=costo, otros_costos=40000, precio_total=precio, garantia_meses=12,
                        fecha=hoy - timedelta(days=dias_atras), usuario_id=admin.id,
                        metodo_pago="Transferencia", condicion="contado")
        calcular_venta_equipo(v)
        db.session.add(v)
        db.session.flush()
        eqs = []
        for i in range(cant):
            e = Equipo(cliente_id=cli.id, tipo=tipo, capacidad_btu=btu, marca=marca, modelo=modelo, inverter=inv,
                       frecuencia_meses=6, refrigerante="R-410A",
                       ubicacion="Alcoba" if cant == 1 else f"Habitación {i + 1}", nota=f"Vendido en {v.numero}")
            db.session.add(e)
            eqs.append(e)
        db.session.flush()
        inst = Orden(tipo="instalacion", cliente_id=cli.id, cliente_nombre=cli.nombre, direccion=cli.direccion_completa,
                     fecha=v.fecha + timedelta(days=1), tecnico_id=carlos.id, usuario_id=admin.id,
                     garantia_dias=cfg.garantia_dias, descripcion=f"Instalación de {v.descripcion} (venta {v.numero}).",
                     trabajo="Instalación con 3 m de tubería, vacío de 30 minutos y prueba de funcionamiento.")
        inst.pago_tecnico = carlos.comision_para(280000 * cant)
        db.session.add(inst)
        fin = v.fecha + timedelta(days=1, hours=3)
        if fin < hoy:
            guardar_orden(inst, [(e, f"Instalación · {e.corto}", 1, 280000) for e in eqs],
                          [(mats["Cable encauchetado 3x14"], 4 * cant, 9000)], "terminada", fecha_fin=fin)
        else:
            guardar_orden(inst, [(e, f"Instalación · {e.corto}", 1, 280000) for e in eqs], [], "programada")
        v.orden_instalacion_id = inst.id

    # Compras de reposición y gastos del periodo
    for d in range(0, 60, 9):
        f = dia0 + timedelta(days=d, hours=10)
        for nombre in rnd.sample(["Gas refrigerante R-410A", "Capacitor 35+5 µF", "Limpiador de serpentín",
                                  "Filtro de aire universal", "Manguera de drenaje"], 2):
            m = mats[nombre]
            cant = 4 if m.unidad != "metro" else 20
            registrar_compra(m, cant, round(cant * (m.costo_promedio or 10000) * rnd.uniform(.97, 1.06)),
                             "Distribuidora Refrigeración del Oriente", "Reposición", f, admin.id)
    for d in range(0, 60, 7):
        db.session.add(Gasto(fecha=(dia0 + timedelta(days=d)).date(), categoria="Combustible / transporte",
                             valor=rnd.choice([85000, 95000, 110000]), descripcion="Gasolina moto y Uber de técnicos"))
    for mes in (dia0, dia0 + timedelta(days=30)):
        f = mes.date().replace(day=5)
        db.session.add_all([
            Gasto(fecha=f, categoria="Arriendo", valor=1200000, descripcion="Local y bodega"),
            Gasto(fecha=f, categoria="Internet / celular", valor=180000, descripcion="Planes de los técnicos"),
            Gasto(fecha=f.replace(day=12), categoria="Publicidad", valor=150000, descripcion="Campaña Facebook e Instagram"),
            Gasto(fecha=f.replace(day=15), categoria="Servicios públicos", valor=210000, descripcion="Energía y agua"),
        ])
    db.session.add(Gasto(fecha=(hoy - timedelta(days=20)).date(), categoria="Dotación y seguridad", valor=340000,
                         descripcion="Arnés, guantes y gafas"))
    db.session.flush()

    # Nómina pagada de la quincena anterior (Carlos por comisión y Andrés mixto)
    import nomina as nom
    q_ini = (hoy - timedelta(days=30)).date().replace(day=1)
    q_fin = q_ini.replace(day=15)
    for emp in (carlos, andres):
        try:
            nom.registrar(emp, cfg, q_ini, q_fin, q_fin + timedelta(days=1), 15, usuario_id=admin.id,
                          metodo="Transferencia")
        except Exception:
            db.session.rollback()
    db.session.commit()
    return n_ordenes


if __name__ == "__main__":
    # Solo para pruebas en un computador (capturas del manual): llena la empresa con datos de ejemplo.
    from app import app
    from models import Empresa, set_empresa
    with app.app_context():
        emp = Empresa.query.first()
        adm = Usuario.query.filter_by(empresa_id=emp.id, rol="admin").first()
        set_empresa(emp.id)
        n = cargar(emp, adm)
        print(f"Datos de ejemplo cargados en «{emp.nombre}»: {n} órdenes.")
