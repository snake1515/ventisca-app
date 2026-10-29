"""Modelos de datos de Ventisca.

Todas las tablas usan el prefijo vt_ para poder convivir con otras apps
en la misma base de datos de Supabase.
"""
import calendar
import contextvars
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, func
from sqlalchemy.orm import Session, declared_attr, with_loader_criteria
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()
TZ = ZoneInfo("America/Bogota")

# ------------------------------------------------------------ multiempresa
# Cada fila de negocio pertenece a una empresa (empresa_id). Todas las consultas se
# filtran solas por la empresa activa y las filas nuevas la reciben al guardarse.
_empresa_actual = contextvars.ContextVar("empresa_actual", default=None)


def set_empresa(empresa_id):
    _empresa_actual.set(empresa_id)


def empresa_id_actual():
    return _empresa_actual.get()


class EmpresaMixin:
    @declared_attr
    def empresa_id(cls):
        return db.Column(db.Integer, db.ForeignKey("vt_empresas.id"), index=True)


@event.listens_for(Session, "do_orm_execute")
def _filtrar_por_empresa(st):
    if (st.is_select and not st.is_column_load and not st.is_relationship_load
            and not st.execution_options.get("todas_empresas")):
        eid = _empresa_actual.get()
        st.statement = st.statement.options(
            with_loader_criteria(EmpresaMixin, lambda cls: cls.empresa_id == eid, include_aliases=True))


@event.listens_for(Session, "before_flush")
def _asignar_empresa(session, ctx, inst):
    eid = _empresa_actual.get()
    siguientes = {}
    with session.no_autoflush:
        for obj in session.new:
            if isinstance(obj, EmpresaMixin) and obj.empresa_id is None:
                if eid is None:
                    raise RuntimeError(f"No hay empresa activa para guardar {type(obj).__name__}")
                obj.empresa_id = eid
            # Consecutivo propio de cada empresa (órdenes, cotizaciones, ventas de equipos)
            if hasattr(type(obj), "consecutivo") and getattr(obj, "consecutivo", 1) is None:
                cls = type(obj)
                if cls not in siguientes:
                    siguientes[cls] = (session.query(func.max(cls.consecutivo))
                                       .filter(cls.empresa_id == obj.empresa_id)
                                       .execution_options(todas_empresas=True).scalar() or 0)
                siguientes[cls] += 1
                obj.consecutivo = siguientes[cls]


def ahora():
    """Fecha y hora actual de Colombia (sin zona, para guardar en BD)."""
    return datetime.now(TZ).replace(tzinfo=None, microsecond=0)


def sumar_meses(d, meses):
    """Suma meses a una fecha conservando el día (o el último día del mes)."""
    m = d.month - 1 + int(meses)
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def wa_link(telefono):
    dig = "".join(ch for ch in (telefono or "") if ch.isdigit())
    if not dig:
        return None
    if len(dig) == 10 and dig.startswith("3"):
        dig = "57" + dig
    return f"https://wa.me/{dig}"


ROLES = {
    "admin": "Administrador",
    "asesor": "Asesor / recepción",
    "tecnico": "Técnico",
}

TIPOS_SERVICIO = {
    "preventivo": "Mantenimiento preventivo",
    "correctivo": "Reparación (correctivo)",
    "instalacion": "Instalación",
    "desinstalacion": "Desinstalación / traslado",
    "diagnostico": "Diagnóstico / visita técnica",
    "gas": "Carga de gas",
    "otro": "Otro servicio",
}
# Servicios que dan garantía por defecto
CON_GARANTIA = {"correctivo", "instalacion", "gas"}

ESTADOS_ORDEN = {
    "programada": ("Programada", "info"),
    "en_proceso": ("En proceso", "warn"),
    "terminada": ("Terminada", "ok"),
    "cancelada": ("Cancelada", "gray"),
}

TIPOS_EQUIPO = ["Mini split (pared)", "Ventana", "Cassette", "Piso-techo", "Multi split", "Central / ductos",
                "Portátil", "Fan coil / chiller", "Cuarto frío / refrigeración", "Nevera / congelador", "Otro"]

REFRIGERANTES = ["R-410A", "R-32", "R-22", "R-134a", "R-404A", "R-290", "R-600a", "Otro"]

CAPACIDADES = [5000, 9000, 12000, 18000, 24000, 30000, 36000, 48000, 60000]

FRECUENCIAS = {1: "Mensual", 2: "Cada 2 meses", 3: "Cada 3 meses", 4: "Cada 4 meses", 6: "Cada 6 meses",
               12: "Una vez al año"}

UNIDADES = ["unidad", "kilogramo", "libra", "gramo", "metro", "centímetro", "rollo", "litro", "galón",
            "mililitro", "caja", "paquete", "juego", "tarro"]

CATEGORIAS_INSUMO = ["Gas refrigerante", "Tubería y aislamiento", "Capacitores y contactores",
                     "Tarjetas y sensores", "Motores y ventiladores", "Filtros y limpieza",
                     "Soportes y fijaciones", "Cables, cinta y misceláneos", "Otros"]

TIPOS_PAGO = {
    "comision": "Por servicio (comisión)",
    "fijo": "Sueldo fijo (se anota en Gastos)",
    "mixto": "Sueldo fijo + comisión",
}

COMISION_TIPOS = {"pct": "% del valor del servicio", "fijo": "Valor fijo por servicio"}

METODOS_PAGO = ["Efectivo", "Nequi", "Daviplata", "Transferencia", "Tarjeta"]

TIPOS_CLIENTE = ["Hogar", "Comercio / local", "Oficina / empresa", "Conjunto / edificio",
                 "Institución (colegio, clínica…)", "Otro"]

CATEGORIAS_GASTO = ["Sueldos y pagos a técnicos", "Seguridad social y prestaciones", "Combustible / transporte", "Arriendo", "Servicios públicos", "Internet / celular",
                    "Publicidad", "Herramienta menor", "Mantenimiento de vehículo", "Dotación y seguridad",
                    "Honorarios (contador, asesorías)", "Impuestos y trámites", "Papelería y aseo",
                    "Pérdidas de inventario", "Otros"]

ESTADOS_EMPRESA = {"demo": "Demo", "activa": "Activa", "suspendida": "Suspendida"}


class Empresa(db.Model):
    """Cliente de SoftGest que usa la app. Sus datos están aislados de los demás."""
    __tablename__ = "vt_empresas"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(40), unique=True, nullable=False)
    contacto = db.Column(db.String(120))
    telefono = db.Column(db.String(40))
    estado = db.Column(db.String(20), nullable=False, default="demo")
    vence = db.Column(db.Date)  # fin de la demo o de la suscripción; vacío = sin vencimiento
    nota = db.Column(db.String(255))
    demo_cargando = db.Column(db.Boolean, nullable=False, default=False)
    creado = db.Column(db.DateTime, default=ahora)

    @property
    def dias_restantes(self):
        if not self.vence:
            return None
        return (self.vence - ahora().date()).days

    @property
    def bloqueada(self):
        if self.estado == "suspendida":
            return True
        d = self.dias_restantes
        return d is not None and d < 0

    @property
    def estado_txt(self):
        if self.estado == "suspendida":
            return ("Suspendida", "err")
        d = self.dias_restantes
        if d is not None and d < 0:
            return ("Vencida", "err")
        if self.estado == "demo":
            return (f"Demo · {d} día{'s' if d != 1 else ''}" if d is not None else "Demo", "warn" if (d or 0) <= 5 else "")
        return ("Activa" + (f" · vence {self.vence.strftime('%d/%m/%Y')}" if self.vence else ""), "ok")


class Usuario(UserMixin, db.Model):
    __tablename__ = "vt_usuarios"
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("vt_empresas.id"), index=True)  # vacío = SoftGest
    nombre = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(60), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default="asesor")
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado = db.Column(db.DateTime, default=ahora)
    intentos_fallidos = db.Column(db.Integer, nullable=False, default=0)
    bloqueado_hasta = db.Column(db.DateTime)  # tras 5 claves erradas seguidas, 15 minutos
    ultimo_ingreso = db.Column(db.DateTime)
    ultimo_uso = db.Column(db.DateTime)
    ingresos = db.Column(db.Integer, nullable=False, default=0)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.activo

    empresa = db.relationship("Empresa", lazy="joined")

    @property
    def rol_nombre(self):
        return "SoftGest (soporte)" if self.rol == "superadmin" else ROLES.get(self.rol, self.rol)

    @property
    def rol_efectivo(self):
        """El superadmin trabaja como administrador cuando entra a una empresa."""
        return "admin" if self.rol == "superadmin" else self.rol


class Configuracion(EmpresaMixin, db.Model):
    """Una sola fila por empresa: alertas, garantías y distribución del dinero."""
    __tablename__ = "vt_configuracion"
    id = db.Column(db.Integer, primary_key=True)
    # Mantenimientos y garantías
    dias_aviso = db.Column(db.Integer, nullable=False, default=15)  # avisar X días antes del preventivo
    frecuencia_def = db.Column(db.Integer, nullable=False, default=6)  # meses entre preventivos
    garantia_dias = db.Column(db.Integer, nullable=False, default=30)
    msg_recordatorio = db.Column(db.String(500))
    # Distribución del dinero
    pct_reinversion = db.Column(db.Float, nullable=False, default=60)
    pct_dueno = db.Column(db.Float, nullable=False, default=30)
    pct_emergencia = db.Column(db.Float, nullable=False, default=10)
    base = db.Column(db.String(20), nullable=False, default="ganancia")  # ventas | ganancia

    @classmethod
    def actual(cls):
        c = cls.query.first()
        if not c:
            c = cls()
            db.session.add(c)
            db.session.commit()
        return c


MSG_RECORDATORIO_DEF = ("Hola {cliente}, le saluda {negocio}. Le recordamos que {equipos} {verbo} el "
                        "mantenimiento preventivo {cuando}. ¿Le agendamos la visita? Responda este mensaje "
                        "con el día y la hora que le queden mejor.")


class DatosNegocio(EmpresaMixin, db.Model):
    """Datos que salen en el encabezado de órdenes, cotizaciones y recibos."""
    __tablename__ = "vt_negocio"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False, default="Mi empresa de aires")
    nit = db.Column(db.String(40))
    direccion = db.Column(db.String(160))
    ciudad = db.Column(db.String(80))
    telefono = db.Column(db.String(40))
    email = db.Column(db.String(120))
    regimen = db.Column(db.String(120), default="No responsable de IVA")
    titulo_doc = db.Column(db.String(60), default="Orden de servicio")
    prefijo = db.Column(db.String(8), default="OS")
    iva_pct = db.Column(db.Float, nullable=False, default=0)
    mensaje = db.Column(db.String(200), default="¡Gracias por confiar en nosotros!")
    condiciones_cot = db.Column(db.String(500))  # texto al pie de las cotizaciones

    logo = db.Column(db.LargeBinary)  # JPG ya redimensionado
    logo_v = db.Column(db.Integer, nullable=False, default=0)

    @classmethod
    def actual(cls):
        d = cls.query.first()
        if not d:
            d = cls()
            db.session.add(d)
            db.session.commit()
        return d


# ----------------------------------------------------------------- clientes
class Cliente(EmpresaMixin, db.Model):
    __tablename__ = "vt_clientes"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    tipo = db.Column(db.String(40), default="Hogar")
    identificacion = db.Column(db.String(40))  # cédula o NIT
    contacto = db.Column(db.String(120))  # persona que atiende (empresas, conjuntos)
    telefono = db.Column(db.String(40))
    email = db.Column(db.String(120))
    direccion = db.Column(db.String(160))
    barrio = db.Column(db.String(80))
    ciudad = db.Column(db.String(80))
    plazo_dias = db.Column(db.Integer, nullable=False, default=0)  # crédito habitual
    nota = db.Column(db.String(255))
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado = db.Column(db.DateTime, default=ahora)

    equipos = db.relationship("Equipo", back_populates="cliente", lazy="selectin",
                              order_by="Equipo.id")

    @property
    def whatsapp(self):
        return wa_link(self.telefono)

    @property
    def equipos_activos(self):
        return [e for e in self.equipos if e.activo]

    @property
    def direccion_completa(self):
        return " · ".join(x for x in (self.direccion, self.barrio, self.ciudad) if x)


class Equipo(EmpresaMixin, db.Model):
    """Un aire acondicionado (u otro equipo) de un cliente, con su plan de mantenimiento."""
    __tablename__ = "vt_equipos"
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("vt_clientes.id"), nullable=False, index=True)
    tipo = db.Column(db.String(40), default="Mini split (pared)")
    marca = db.Column(db.String(60))
    modelo = db.Column(db.String(80))
    capacidad_btu = db.Column(db.Integer)
    refrigerante = db.Column(db.String(20))
    serial = db.Column(db.String(80))
    ubicacion = db.Column(db.String(80))  # sala, alcoba principal, oficina 2…
    inverter = db.Column(db.Boolean, nullable=False, default=False)
    fecha_instalacion = db.Column(db.Date)
    frecuencia_meses = db.Column(db.Integer, nullable=False, default=6)  # 0 = sin plan de preventivo
    ultimo_manual = db.Column(db.Date)  # último preventivo hecho antes de usar la app
    ultimo_preventivo = db.Column(db.Date)
    proximo_preventivo = db.Column(db.Date, index=True)
    pospuesto_hasta = db.Column(db.Date)
    aviso_fecha = db.Column(db.Date)  # último recordatorio enviado por WhatsApp
    activo = db.Column(db.Boolean, nullable=False, default=True)
    nota = db.Column(db.String(255))
    creado = db.Column(db.DateTime, default=ahora)

    cliente = db.relationship("Cliente", back_populates="equipos", lazy="joined")

    @property
    def btu_txt(self):
        return f"{self.capacidad_btu:,}".replace(",", ".") + " BTU" if self.capacidad_btu else ""

    @property
    def nombre(self):
        partes = [self.tipo or "Equipo"]
        if self.capacidad_btu:
            partes.append(self.btu_txt)
        if self.marca:
            partes.append(self.marca)
        txt = " · ".join(partes)
        return f"{txt} ({self.ubicacion})" if self.ubicacion else txt

    @property
    def corto(self):
        base = " ".join(x for x in (self.marca, self.btu_txt) if x) or (self.tipo or "Equipo")
        return f"{base} – {self.ubicacion}" if self.ubicacion else base

    @property
    def con_plan(self):
        return bool(self.frecuencia_meses)

    def estado_mant(self, hoy, dias_aviso=15):
        """(clave, texto, clase) del estado del preventivo."""
        if not self.activo:
            return ("retirado", "Retirado", "gray")
        if not self.con_plan:
            return ("sin_plan", "Sin plan", "gray")
        if not self.proximo_preventivo:
            return ("sin_fecha", "Sin fecha", "warn")
        d = (self.proximo_preventivo - hoy).days
        if d < 0:
            return ("vencido", f"Vencido hace {-d} d", "err")
        if d == 0:
            return ("vencido", "Vence hoy", "err")
        if d <= dias_aviso:
            return ("proximo", f"En {d} día{'s' if d != 1 else ''}", "warn")
        return ("al_dia", "Al día", "ok")


# ------------------------------------------------------------------ insumos
class Insumo(EmpresaMixin, db.Model):
    """Repuestos y materiales: gas, tubería, capacitores, filtros…"""
    __tablename__ = "vt_insumos"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    categoria = db.Column(db.String(60), default="Otros")
    unidad = db.Column(db.String(30), nullable=False, default="unidad")
    stock = db.Column(db.Float, nullable=False, default=0)
    costo_promedio = db.Column(db.Float, nullable=False, default=0)
    precio_venta = db.Column(db.Float, nullable=False, default=0)  # lo que se le cobra al cliente
    stock_minimo = db.Column(db.Float, nullable=False, default=0)
    activo = db.Column(db.Boolean, nullable=False, default=True)

    @property
    def valor_stock(self):
        return max(self.stock or 0, 0) * (self.costo_promedio or 0)

    @property
    def bajo_minimo(self):
        return (self.stock_minimo or 0) > 0 and (self.stock or 0) <= self.stock_minimo


class CompraInsumo(EmpresaMixin, db.Model):
    __tablename__ = "vt_compras"
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    insumo_id = db.Column(db.Integer, db.ForeignKey("vt_insumos.id"), nullable=False)
    cantidad = db.Column(db.Float, nullable=False)
    costo_total = db.Column(db.Float, nullable=False)
    costo_unitario = db.Column(db.Float, nullable=False)
    proveedor = db.Column(db.String(120))
    nota = db.Column(db.String(255))
    usuario_id = db.Column(db.Integer, db.ForeignKey("vt_usuarios.id"))
    anulada = db.Column(db.Boolean, nullable=False, default=False)

    insumo = db.relationship("Insumo", lazy="joined")
    usuario = db.relationship("Usuario", lazy="joined")


# --------------------------------------------------------------- empleados
class Empleado(EmpresaMixin, db.Model):
    """Técnicos y ayudantes. Se les asignan las órdenes de servicio."""
    __tablename__ = "vt_empleados"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    documento = db.Column(db.String(40))
    telefono = db.Column(db.String(40))
    cargo = db.Column(db.String(80), default="Técnico")
    tipo_pago = db.Column(db.String(20), nullable=False, default="comision")
    salario = db.Column(db.Float, nullable=False, default=0)  # mensual (fijo o mixto)
    comision_tipo = db.Column(db.String(10), nullable=False, default="pct")
    comision_valor = db.Column(db.Float, nullable=False, default=0)
    fecha_ingreso = db.Column(db.Date)
    usuario_id = db.Column(db.Integer, db.ForeignKey("vt_usuarios.id"))  # su acceso a la app (rol técnico)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    nota = db.Column(db.String(255))

    usuario = db.relationship("Usuario", lazy="joined")

    @property
    def tipo_nombre(self):
        return TIPOS_PAGO.get(self.tipo_pago, self.tipo_pago)

    @property
    def comision_txt(self):
        if self.tipo_pago == "fijo" or not self.comision_valor:
            return ""
        if self.comision_tipo == "pct":
            return f"{self.comision_valor:g}% por servicio"
        return ("$ " + f"{self.comision_valor:,.0f}".replace(",", ".")) + " por servicio"

    def comision_para(self, valor_servicio):
        """Comisión sugerida para una orden (sobre la mano de obra, sin materiales)."""
        if self.tipo_pago == "fijo" or not self.comision_valor:
            return 0.0
        if self.comision_tipo == "pct":
            return round(max(valor_servicio, 0) * self.comision_valor / 100)
        return self.comision_valor

    @property
    def whatsapp(self):
        return wa_link(self.telefono)


# --------------------------------------------------------- órdenes de servicio
class Orden(EmpresaMixin, db.Model):
    """Orden de servicio: preventivo, correctivo, instalación… con sus cobros y materiales."""
    __tablename__ = "vt_ordenes"
    id = db.Column(db.Integer, primary_key=True)
    consecutivo = db.Column(db.Integer)
    tipo = db.Column(db.String(20), nullable=False, default="preventivo")
    estado = db.Column(db.String(20), nullable=False, default="programada", index=True)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)  # fecha y hora programada
    fecha_fin = db.Column(db.DateTime, index=True)  # cuándo se terminó (cuenta como ingreso)
    cliente_id = db.Column(db.Integer, db.ForeignKey("vt_clientes.id"), nullable=False, index=True)
    cliente_nombre = db.Column(db.String(120))
    direccion = db.Column(db.String(200))
    tecnico_id = db.Column(db.Integer, db.ForeignKey("vt_empleados.id"), index=True)
    descripcion = db.Column(db.Text)  # lo que pidió el cliente / falla reportada
    trabajo = db.Column(db.Text)  # diagnóstico y trabajo realizado
    recomendaciones = db.Column(db.Text)
    # Garantía
    en_garantia = db.Column(db.Boolean, nullable=False, default=False)  # visita cubierta por garantía
    garantia_de_id = db.Column(db.Integer, db.ForeignKey("vt_ordenes.id"))
    garantia_dias = db.Column(db.Integer, nullable=False, default=0)  # garantía que da esta orden
    garantia_hasta = db.Column(db.Date)
    cotizacion_id = db.Column(db.Integer, db.ForeignKey("vt_cotizaciones.id"))
    # Cobro
    condicion = db.Column(db.String(10), nullable=False, default="contado")  # contado | credito
    vence = db.Column(db.Date)
    metodo_pago = db.Column(db.String(30), default="Efectivo")
    descuento = db.Column(db.Float, nullable=False, default=0)
    iva_pct = db.Column(db.Float, nullable=False, default=0)
    iva = db.Column(db.Float, nullable=False, default=0)
    total = db.Column(db.Float, nullable=False, default=0)  # neto sin IVA
    costo_materiales = db.Column(db.Float, nullable=False, default=0)
    pago_tecnico = db.Column(db.Float, nullable=False, default=0)
    otros_costos = db.Column(db.Float, nullable=False, default=0)  # transporte, ayudante, alquiler de escalera…
    ganancia = db.Column(db.Float, nullable=False, default=0)
    aplicada = db.Column(db.Boolean, nullable=False, default=False)  # ya descontó materiales del inventario
    anulada = db.Column(db.Boolean, nullable=False, default=False)
    nota = db.Column(db.String(255))
    usuario_id = db.Column(db.Integer, db.ForeignKey("vt_usuarios.id"))
    creado = db.Column(db.DateTime, default=ahora)

    cliente = db.relationship("Cliente", lazy="joined")
    tecnico = db.relationship("Empleado", lazy="joined")
    usuario = db.relationship("Usuario", lazy="joined")
    garantia_de = db.relationship("Orden", remote_side=[id], lazy="joined")
    lineas = db.relationship("OrdenLinea", back_populates="orden", lazy="selectin",
                             cascade="all, delete-orphan", order_by="OrdenLinea.id")
    materiales = db.relationship("OrdenMaterial", back_populates="orden", lazy="selectin",
                                 cascade="all, delete-orphan", order_by="OrdenMaterial.id")
    abonos = db.relationship("Abono", back_populates="orden", lazy="selectin",
                             cascade="all, delete-orphan", order_by="Abono.fecha")

    @property
    def tipo_txt(self):
        return TIPOS_SERVICIO.get(self.tipo, self.tipo)

    @property
    def estado_info(self):
        if self.anulada:
            return ("Anulada", "err")
        return ESTADOS_ORDEN.get(self.estado, (self.estado, "gray"))

    @property
    def terminada(self):
        return self.estado == "terminada" and not self.anulada

    @property
    def equipos(self):
        vistos, out = set(), []
        for l in self.lineas:
            if l.equipo and l.equipo.id not in vistos:
                vistos.add(l.equipo.id)
                out.append(l.equipo)
        return out

    @property
    def subtotal_servicios(self):
        return sum(l.subtotal for l in self.lineas)

    @property
    def subtotal_materiales(self):
        return sum(m.subtotal for m in self.materiales)

    @property
    def subtotal(self):
        return self.subtotal_servicios + self.subtotal_materiales

    @property
    def total_factura(self):
        return (self.total or 0) + (self.iva or 0)

    @property
    def pagado(self):
        if self.anulada or self.estado == "cancelada":
            return 0
        abonado = sum(a.valor for a in self.abonos if not a.anulado)
        if self.condicion != "credito" and self.estado == "terminada":
            return self.total_factura
        return abonado

    @property
    def saldo(self):
        if self.anulada or self.estado != "terminada" or self.condicion != "credito":
            return 0
        return max(self.total_factura - self.pagado, 0)

    @property
    def vencida(self):
        return bool(self.saldo > 0.5 and self.vence and self.vence < ahora().date())

    @property
    def estado_pago(self):
        if self.anulada:
            return ("Anulada", "err")
        if self.estado != "terminada":
            return ("—", "gray")
        if self.total_factura <= 0.5:
            return ("Sin cobro", "gray")
        if self.condicion != "credito" or self.saldo <= 0.5:
            return ("Pagada", "ok")
        if self.vencida:
            return (f"Vencida hace {(ahora().date() - self.vence).days} d", "err")
        return ("Pendiente", "warn")

    @property
    def garantia_vigente(self):
        return bool(self.terminada and self.garantia_hasta and self.garantia_hasta >= ahora().date())


class OrdenLinea(EmpresaMixin, db.Model):
    """Un servicio cobrado dentro de la orden (normalmente uno por equipo)."""
    __tablename__ = "vt_orden_lineas"
    id = db.Column(db.Integer, primary_key=True)
    orden_id = db.Column(db.Integer, db.ForeignKey("vt_ordenes.id"), nullable=False, index=True)
    equipo_id = db.Column(db.Integer, db.ForeignKey("vt_equipos.id"), index=True)
    descripcion = db.Column(db.String(200), nullable=False)
    cantidad = db.Column(db.Float, nullable=False, default=1)
    precio = db.Column(db.Float, nullable=False, default=0)
    subtotal = db.Column(db.Float, nullable=False, default=0)

    orden = db.relationship("Orden", back_populates="lineas")
    equipo = db.relationship("Equipo", lazy="joined")


class OrdenMaterial(EmpresaMixin, db.Model):
    """Material o repuesto usado en la orden: sale del inventario y se le puede cobrar al cliente."""
    __tablename__ = "vt_orden_materiales"
    id = db.Column(db.Integer, primary_key=True)
    orden_id = db.Column(db.Integer, db.ForeignKey("vt_ordenes.id"), nullable=False, index=True)
    insumo_id = db.Column(db.Integer, db.ForeignKey("vt_insumos.id"), nullable=False)
    cantidad = db.Column(db.Float, nullable=False)
    costo_unitario = db.Column(db.Float, nullable=False, default=0)
    costo_total = db.Column(db.Float, nullable=False, default=0)
    precio_unitario = db.Column(db.Float, nullable=False, default=0)  # cobrado al cliente
    subtotal = db.Column(db.Float, nullable=False, default=0)

    orden = db.relationship("Orden", back_populates="materiales")
    insumo = db.relationship("Insumo", lazy="joined")


# ------------------------------------------------------------- cotizaciones
class Cotizacion(EmpresaMixin, db.Model):
    __tablename__ = "vt_cotizaciones"
    id = db.Column(db.Integer, primary_key=True)
    consecutivo = db.Column(db.Integer)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("vt_clientes.id"), index=True)
    cliente_nombre = db.Column(db.String(120))
    tipo = db.Column(db.String(20), default="correctivo")
    descripcion = db.Column(db.Text)
    validez_dias = db.Column(db.Integer, nullable=False, default=15)
    estado = db.Column(db.String(20), nullable=False, default="pendiente")  # pendiente|aprobada|rechazada
    descuento = db.Column(db.Float, nullable=False, default=0)
    iva_pct = db.Column(db.Float, nullable=False, default=0)
    iva = db.Column(db.Float, nullable=False, default=0)
    total = db.Column(db.Float, nullable=False, default=0)
    nota = db.Column(db.String(500))
    orden_id = db.Column(db.Integer, db.ForeignKey("vt_ordenes.id"))
    usuario_id = db.Column(db.Integer, db.ForeignKey("vt_usuarios.id"))

    cliente = db.relationship("Cliente", lazy="joined")
    usuario = db.relationship("Usuario", lazy="joined")
    orden = db.relationship("Orden", foreign_keys=[orden_id], lazy="joined")
    lineas = db.relationship("CotizacionLinea", back_populates="cotizacion", lazy="selectin",
                             cascade="all, delete-orphan", order_by="CotizacionLinea.id")

    @property
    def numero(self):
        return f"COT-{(self.consecutivo or self.id):05d}"

    @property
    def tipo_txt(self):
        return TIPOS_SERVICIO.get(self.tipo, self.tipo)

    @property
    def valida_hasta(self):
        return self.fecha.date() + timedelta(days=self.validez_dias or 0)

    @property
    def subtotal(self):
        return sum(l.subtotal for l in self.lineas)

    @property
    def total_factura(self):
        return (self.total or 0) + (self.iva or 0)

    @property
    def estado_info(self):
        if self.orden_id:
            return ("Convertida en orden", "ok")
        if self.estado == "aprobada":
            return ("Aprobada", "ok")
        if self.estado == "rechazada":
            return ("Rechazada", "gray")
        if self.valida_hasta < ahora().date():
            return ("Vencida", "err")
        return ("Pendiente", "warn")


class CotizacionLinea(EmpresaMixin, db.Model):
    __tablename__ = "vt_cotizacion_lineas"
    id = db.Column(db.Integer, primary_key=True)
    cotizacion_id = db.Column(db.Integer, db.ForeignKey("vt_cotizaciones.id"), nullable=False, index=True)
    insumo_id = db.Column(db.Integer, db.ForeignKey("vt_insumos.id"))  # si es un material del inventario
    equipo_id = db.Column(db.Integer, db.ForeignKey("vt_equipos.id"))
    descripcion = db.Column(db.String(200), nullable=False)
    cantidad = db.Column(db.Float, nullable=False, default=1)
    precio = db.Column(db.Float, nullable=False, default=0)
    subtotal = db.Column(db.Float, nullable=False, default=0)

    cotizacion = db.relationship("Cotizacion", back_populates="lineas")
    insumo = db.relationship("Insumo", lazy="joined")
    equipo = db.relationship("Equipo", lazy="joined")


# -------------------------------------------------- compra-venta de equipos
class VentaEquipo(EmpresaMixin, db.Model):
    """Aire comprado a un proveedor y vendido a un cliente (no pasa por inventario)."""
    __tablename__ = "vt_ventas_equipos"
    id = db.Column(db.Integer, primary_key=True)
    consecutivo = db.Column(db.Integer)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("vt_clientes.id"), index=True)
    cliente_nombre = db.Column(db.String(120))
    tipo = db.Column(db.String(40))
    marca = db.Column(db.String(60))
    modelo = db.Column(db.String(80))
    capacidad_btu = db.Column(db.Integer)
    inverter = db.Column(db.Boolean, nullable=False, default=False)
    cantidad = db.Column(db.Integer, nullable=False, default=1)
    proveedor = db.Column(db.String(120))
    costo_total = db.Column(db.Float, nullable=False, default=0)  # lo que pagaste al proveedor
    otros_costos = db.Column(db.Float, nullable=False, default=0)  # flete, transporte
    precio_total = db.Column(db.Float, nullable=False, default=0)  # lo que pagó el cliente (sin IVA)
    iva_pct = db.Column(db.Float, nullable=False, default=0)
    iva = db.Column(db.Float, nullable=False, default=0)
    ganancia = db.Column(db.Float, nullable=False, default=0)
    garantia_meses = db.Column(db.Integer, nullable=False, default=12)  # garantía de fábrica
    condicion = db.Column(db.String(10), nullable=False, default="contado")
    vence = db.Column(db.Date)
    metodo_pago = db.Column(db.String(30), default="Efectivo")
    orden_instalacion_id = db.Column(db.Integer, db.ForeignKey("vt_ordenes.id"))
    nota = db.Column(db.String(255))
    anulada = db.Column(db.Boolean, nullable=False, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("vt_usuarios.id"))

    cliente = db.relationship("Cliente", lazy="joined")
    usuario = db.relationship("Usuario", lazy="joined")
    orden_instalacion = db.relationship("Orden", foreign_keys=[orden_instalacion_id], lazy="joined")
    abonos = db.relationship("Abono", back_populates="venta_equipo", lazy="selectin",
                             cascade="all, delete-orphan", order_by="Abono.fecha")

    @property
    def numero(self):
        return f"VE-{(self.consecutivo or self.id):05d}"

    @property
    def descripcion(self):
        partes = [self.tipo or "Aire acondicionado"]
        if self.capacidad_btu:
            partes.append(f"{self.capacidad_btu:,}".replace(",", ".") + " BTU")
        if self.inverter:
            partes.append("Inverter")
        partes += [x for x in (self.marca, self.modelo) if x]
        return " · ".join(partes)

    @property
    def costo_completo(self):
        return (self.costo_total or 0) + (self.otros_costos or 0)

    @property
    def total_factura(self):
        return (self.precio_total or 0) + (self.iva or 0)

    @property
    def pagado(self):
        if self.anulada:
            return 0
        if self.condicion != "credito":
            return self.total_factura
        return sum(a.valor for a in self.abonos if not a.anulado)

    @property
    def saldo(self):
        if self.anulada or self.condicion != "credito":
            return 0
        return max(self.total_factura - self.pagado, 0)

    @property
    def vencida(self):
        return bool(self.saldo > 0.5 and self.vence and self.vence < ahora().date())

    @property
    def estado_pago(self):
        if self.anulada:
            return ("Anulada", "err")
        if self.condicion != "credito" or self.saldo <= 0.5:
            return ("Pagada", "ok")
        if self.vencida:
            return (f"Vencida hace {(ahora().date() - self.vence).days} d", "err")
        return ("Pendiente", "warn")

    @property
    def margen(self):
        return (self.ganancia / self.precio_total * 100) if self.precio_total else 0


class Abono(EmpresaMixin, db.Model):
    """Pago parcial o total de una orden o venta de equipo a crédito."""
    __tablename__ = "vt_abonos"
    id = db.Column(db.Integer, primary_key=True)
    orden_id = db.Column(db.Integer, db.ForeignKey("vt_ordenes.id"), index=True)
    venta_equipo_id = db.Column(db.Integer, db.ForeignKey("vt_ventas_equipos.id"), index=True)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    valor = db.Column(db.Float, nullable=False)
    metodo = db.Column(db.String(30), default="Efectivo")
    nota = db.Column(db.String(255))
    anulado = db.Column(db.Boolean, nullable=False, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("vt_usuarios.id"))

    orden = db.relationship("Orden", back_populates="abonos")
    venta_equipo = db.relationship("VentaEquipo", back_populates="abonos")
    usuario = db.relationship("Usuario", lazy="joined")

    @property
    def documento(self):
        return self.orden or self.venta_equipo


# ------------------------------------------------------------------ activos
class Activo(EmpresaMixin, db.Model):
    """Herramientas y equipos propios (bomba de vacío, manifold, recuperadora, moto…)."""
    __tablename__ = "vt_activos"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    descripcion = db.Column(db.String(255))
    fecha_compra = db.Column(db.Date, nullable=False)
    valor = db.Column(db.Float, nullable=False)
    valor_residual = db.Column(db.Float, nullable=False, default=0)
    vida_util_meses = db.Column(db.Integer, nullable=False)
    fecha_baja = db.Column(db.Date)
    motivo_baja = db.Column(db.String(255))
    eliminado = db.Column(db.Boolean, nullable=False, default=False)
    proveedor = db.Column(db.String(120))

    @property
    def depreciable(self):
        return max((self.valor or 0) - (self.valor_residual or 0), 0)

    @property
    def cuota_mensual(self):
        return self.depreciable / self.vida_util_meses if self.vida_util_meses else 0

    @property
    def cuota_diaria(self):
        return self.cuota_mensual * 12 / 365

    @property
    def fin_vida(self):
        return self.fecha_compra + timedelta(days=round(self.vida_util_meses * 365 / 12))

    def depreciacion_entre(self, desde, hasta):
        """Depreciación de los días [desde, hasta) — fechas date."""
        ini = max(desde, self.fecha_compra)
        fin = min(hasta, self.fin_vida, self.fecha_baja or hasta)
        dias = (fin - ini).days
        return max(dias, 0) * self.cuota_diaria

    def acumulada(self, hoy):
        return min(self.depreciacion_entre(self.fecha_compra, hoy + timedelta(days=1)), self.depreciable)

    def estado(self, hoy):
        if self.fecha_baja:
            return "baja"
        if hoy >= self.fin_vida:
            return "recuperado"
        return "vigente"


class Gasto(EmpresaMixin, db.Model):
    """Gastos del negocio que no son materiales ni herramientas (incluye sueldos fijos)."""
    __tablename__ = "vt_gastos"
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, nullable=False, index=True)
    categoria = db.Column(db.String(60), nullable=False, default="Otros")
    descripcion = db.Column(db.String(255))
    valor = db.Column(db.Float, nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("vt_usuarios.id"))
    usuario = db.relationship("Usuario")
