"""Ventisca — órdenes de servicio, plan de mantenimiento con alertas, clientes y equipos,
cotizaciones, compra-venta de aires, materiales, técnicos, cartera y reportes para
empresas de mantenimiento de aires acondicionados."""
import os
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import quote

from dotenv import load_dotenv
from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)
from sqlalchemy import func, or_

import comprobantes
import reportes as rpt
from activos_calc import activos_vigentes, depreciacion_dia, depreciacion_periodo
from models import (CAPACIDADES, CATEGORIAS_GASTO, CATEGORIAS_INSUMO, COMISION_TIPOS, CON_GARANTIA,
                    ESTADOS_EMPRESA, ESTADOS_ORDEN, FRECUENCIAS, METODOS_PAGO, MSG_RECORDATORIO_DEF,
                    REFRIGERANTES, ROLES, TIPOS_CLIENTE, TIPOS_EQUIPO, TIPOS_PAGO, TIPOS_SERVICIO, UNIDADES,
                    Abono, Activo, Cliente, CompraInsumo, Configuracion, Cotizacion, CotizacionLinea,
                    DatosNegocio, Empleado, Empresa, EmpresaMixin, Equipo, Gasto, Insumo, Orden, OrdenLinea,
                    OrdenMaterial, Usuario, VentaEquipo, ahora, db, empresa_id_actual,
                    set_empresa, wa_link)
from services import (ErrorNegocio, ajustar_stock, anular_compra, anular_orden, anular_venta_equipo,
                      calcular_venta_equipo, cambiar_estado, garantia_por_defecto, garantia_vigente,
                      guardar_orden, recalcular_equipo, recalcular_orden, registrar_abono, registrar_compra,
                      terminar_orden, validar_venta_equipo)

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or secrets.token_hex(32)
db_url = (os.getenv("DATABASE_URL") or "sqlite:///ventisca.db").strip().strip('"').strip("'")
if "[YOUR-PASSWORD]" in db_url:
    raise SystemExit("ERROR DATABASE_URL: aún contiene [YOUR-PASSWORD]. Reemplázalo por la contraseña real de Supabase.")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 280}
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=30)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
if os.getenv("RENDER"):  # en Render todo va por HTTPS: las cookies solo viajan cifradas
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["REMEMBER_COOKIE_SECURE"] = True
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024
if not os.getenv("SECRET_KEY"):
    print("AVISO: falta la variable SECRET_KEY; las sesiones se cerrarán en cada reinicio.", flush=True)
NOMBRE_APP = os.getenv("NOMBRE_NEGOCIO", "Ventisca")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60 * 60 * 24 * 30
STATIC_V = (os.getenv("RENDER_GIT_COMMIT") or secrets.token_hex(4))[:8]
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Inicia sesión para continuar."
login_manager.login_message_category = "info"


@login_manager.user_loader
def cargar_usuario(uid):
    return db.session.get(Usuario, int(uid))


# ---------------------------------------------------------------- utilidades
def n(valor, defecto=0.0):
    """Convierte texto a número aceptando 1.500,5 / 1500.5 / 1500,5 / 120.000."""
    if valor is None:
        return defecto
    v = str(valor).strip().replace(" ", "").replace("$", "")
    if not v:
        return defecto
    if "," in v and "." in v:
        v = v.replace(".", "").replace(",", ".")
    elif "," in v:
        v = v.replace(",", ".")
    elif v.count(".") > 1 or (v.count(".") == 1 and len(v.split(".")[1]) == 3 and len(v.split(".")[0]) <= 3
                              and v.split(".")[0] not in ("0", "")):
        v = v.replace(".", "")  # 120.000 → 120000 (miles al estilo colombiano)
    try:
        return float(v)
    except ValueError:
        return defecto


def _fecha(txt, defecto=None):
    try:
        return datetime.strptime(txt or "", "%Y-%m-%d").date()
    except ValueError:
        return defecto


def fecha_form(texto):
    """Fecha del formulario + hora actual (si es hoy) o mediodía."""
    d = _fecha(texto)
    if not d:
        return ahora()
    hoy = ahora()
    if d == hoy.date():
        return hoy
    return datetime.combine(d, datetime.min.time()).replace(hour=12)


def fecha_hora(fecha_txt, hora_txt, defecto=None):
    d = _fecha(fecha_txt)
    if not d:
        return defecto
    try:
        h = datetime.strptime(hora_txt or "", "%H:%M").time()
    except ValueError:
        h = datetime.min.time().replace(hour=8)
    return datetime.combine(d, h)


def periodo():
    """Rango de fechas de los filtros (por defecto, el mes actual)."""
    hoy = ahora().date()
    desde = _fecha(request.args.get("desde"), hoy.replace(day=1))
    hasta = _fecha(request.args.get("hasta"), hoy)
    if hasta < desde:
        desde, hasta = hasta, desde
    ini = datetime.combine(desde, datetime.min.time())
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time())
    return ini, fin, desde.isoformat(), hasta.isoformat()


def gastos_por_dia(desde, hasta):
    """dict date -> gastos, entre fechas date (inclusive)."""
    out = defaultdict(float)
    for f, v in (db.session.query(Gasto.fecha, func.sum(Gasto.valor))
                 .filter(Gasto.fecha >= desde, Gasto.fecha <= hasta).group_by(Gasto.fecha).all()):
        out[f] += v or 0
    return out


def rol_requerido(*roles):
    def deco(f):
        @wraps(f)
        @login_required
        def envoltura(*a, **kw):
            if current_user.rol_efectivo not in roles:
                flash("No tienes permiso para entrar a esa sección.", "error")
                return redirect(url_for("inicio"))
            return f(*a, **kw)
        return envoltura
    return deco


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


@app.before_request
def verificar_csrf():
    if request.method == "POST":
        enviado = request.form.get("csrf_token") or request.headers.get("X-CSRF") or ""
        esperado = session.get("_csrf") or ""
        if not enviado or not esperado or not secrets.compare_digest(enviado, esperado):
            if request.is_json or request.headers.get("X-CSRF") is not None:
                return jsonify(ok=False, error="La sesión expiró. Recarga la página."), 400
            flash("La sesión expiró. Intenta de nuevo.", "error")
            return redirect(request.path)


@app.after_request
def cabeceras_seguridad(resp):
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if app.config.get("SESSION_COOKIE_SECURE"):
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if current_user.is_authenticated and resp.mimetype == "text/html":
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


LIBRES = {"static", "login", "logout", "cuenta", "bloqueado", "logo", "salud", "manifest"}


@app.before_request
def contexto_empresa():
    """Define la empresa activa de esta petición (la app atiende a una sola empresa)."""
    set_empresa(None)
    g.empresa = None
    if not current_user.is_authenticated:
        return None
    emp = current_user.empresa
    if emp is None:
        logout_user()
        return redirect(url_for("login"))
    set_empresa(emp.id)
    g.empresa = emp
    momento = ahora()
    if not current_user.ultimo_uso or momento - current_user.ultimo_uso > timedelta(minutes=5):
        current_user.ultimo_uso = momento
        db.session.commit()
    if emp.estado == "suspendida" and (request.endpoint or "") not in LIBRES:
        return redirect(url_for("bloqueado"))
    return None


_cache_neg = {}


def negocio_cache(refrescar=False):
    """Nombre y prefijo del negocio de la empresa activa, con caché de 10 minutos."""
    import time
    eid = empresa_id_actual()
    if eid is None:
        # Pantallas sin sesión (login): la app atiende a una sola empresa, se usa su nombre
        try:
            emp = Empresa.query.order_by(Empresa.id).first()
        except Exception:
            db.session.rollback()
            emp = None
        if emp is None:
            return dict(nombre=NOMBRE_APP, prefijo="OS", logo_v=0)
        eid = emp.id
    c = _cache_neg.get(eid)
    if refrescar or c is None or time.time() > c[1]:
        val = dict(nombre=NOMBRE_APP, prefijo="OS", logo_v=0)
        try:
            neg = (DatosNegocio.query.filter(DatosNegocio.empresa_id == eid)
                   .execution_options(todas_empresas=True).first())
            if neg:
                val = dict(nombre=neg.nombre or NOMBRE_APP, prefijo=(neg.prefijo or "OS").upper(),
                           logo_v=neg.logo_v if neg.logo else 0)
        except Exception:
            db.session.rollback()
        c = _cache_neg[eid] = (val, time.time() + 600)
    return c[0]


def num_orden(o):
    return f"{negocio_cache()['prefijo']}-{(o.consecutivo or o.id):05d}"


def empleado_actual():
    """El técnico (Empleado) vinculado al usuario que entró, si existe."""
    if not current_user.is_authenticated:
        return None
    return Empleado.query.filter_by(usuario_id=current_user.id).first()


@app.context_processor
def contexto():
    def puede(*roles):
        return current_user.is_authenticated and current_user.rol_efectivo in roles
    neg = negocio_cache()
    emp = getattr(g, "empresa", None)
    alertas_n = 0
    if emp and current_user.is_authenticated and current_user.rol_efectivo in ("admin", "asesor"):
        try:
            alertas_n = contar_alertas()
        except Exception:
            db.session.rollback()
    return dict(csrf_token=csrf_token, puede=puede, ROLES=ROLES, STATIC_V=STATIC_V,
                NOMBRE_NEGOCIO=neg["nombre"], LOGO_V=neg["logo_v"], hoy=ahora().date().isoformat(),
                num_orden=num_orden, EMPRESA=emp, TIPOS_SERVICIO=TIPOS_SERVICIO,
                ALERTAS_N=alertas_n, NOMBRE_APP=NOMBRE_APP)


@app.template_filter("cop")
def filtro_cop(v):
    v = v or 0
    s = f"{abs(v):,.0f}".replace(",", ".")
    return ("-$ " if v < -0.5 else "$ ") + s


@app.template_filter("num")
def filtro_num(v, dec=2):
    v = v or 0
    s = f"{v:,.{dec}f}"
    entero, _, decimal = s.partition(".")
    entero = entero.replace(",", ".")
    decimal = decimal.rstrip("0")
    return f"{entero},{decimal}" if decimal else entero


@app.template_filter("fecha")
def filtro_fecha(v, hora=False):
    if not v:
        return ""
    return v.strftime("%d/%m/%Y %I:%M %p" if hora else "%d/%m/%Y")


DIAS_SEM = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES_TXT = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


@app.template_filter("dia")
def filtro_dia(v):
    """«hoy», «mañana», «ayer» o «mié 5 nov»."""
    if not v:
        return ""
    d = v.date() if isinstance(v, datetime) else v
    dif = (d - ahora().date()).days
    if dif == 0:
        return "Hoy"
    if dif == 1:
        return "Mañana"
    if dif == -1:
        return "Ayer"
    return f"{DIAS_SEM[d.weekday()].capitalize()} {d.day} {MESES_TXT[d.month - 1]}"


DIAS_LARGO = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


@app.template_filter("cuando")
def filtro_cuando(v):
    """«Hoy 29/10/2026», «Mañana 30/10/2026» o «Martes 27/10/2026»."""
    if not v:
        return ""
    d = v.date() if isinstance(v, datetime) else v
    dif = (d - ahora().date()).days
    etq = {0: "Hoy", 1: "Mañana", -1: "Ayer"}.get(dif, DIAS_LARGO[d.weekday()])
    return f"{etq} {d.strftime('%d/%m/%Y')}"


@app.template_filter("hora")
def filtro_hora(v):
    return v.strftime("%I:%M %p").lstrip("0").lower() if v else ""


# ------------------------------------------------------- alertas de preventivo
def equipos_agendados():
    """dict equipo_id -> Orden abierta (programada / en proceso) que lo incluye."""
    out = {}
    for o in (Orden.query.filter(Orden.anulada.is_(False), Orden.estado.in_(["programada", "en_proceso"]))
              .order_by(Orden.fecha).all()):
        for e in o.equipos:
            out.setdefault(e.id, o)
    return out


def contar_alertas():
    cfg = Configuracion.actual()
    hoy = ahora().date()
    ids = [e.id for e in Equipo.query.filter(
        Equipo.activo.is_(True), Equipo.frecuencia_meses > 0, Equipo.proximo_preventivo.isnot(None),
        Equipo.proximo_preventivo <= hoy + timedelta(days=cfg.dias_aviso),
        or_(Equipo.pospuesto_hasta.is_(None), Equipo.pospuesto_hasta <= hoy)).all()]
    if not ids:
        return 0
    abiertas = (db.session.query(OrdenLinea.equipo_id).join(Orden, OrdenLinea.orden_id == Orden.id)
                .filter(Orden.anulada.is_(False), Orden.estado.in_(["programada", "en_proceso"]),
                        OrdenLinea.equipo_id.in_(ids)).distinct().all())
    return len(set(ids) - {x[0] for x in abiertas})


def alertas_preventivo(incluir_agendados=False, dias=None):
    """Clientes con equipos vencidos o por vencer, agrupados. Lo más urgente primero."""
    cfg = Configuracion.actual()
    hoy = ahora().date()
    dias = cfg.dias_aviso if dias is None else dias
    agendados = equipos_agendados()
    q = Equipo.query.filter(Equipo.activo.is_(True), Equipo.frecuencia_meses > 0,
                            Equipo.proximo_preventivo.isnot(None),
                            Equipo.proximo_preventivo <= hoy + timedelta(days=dias))
    grupos = {}
    for e in q.order_by(Equipo.proximo_preventivo).all():
        if not e.cliente or not e.cliente.activo:
            continue
        pospuesto = e.pospuesto_hasta and e.pospuesto_hasta > hoy
        ag = agendados.get(e.id)
        if (ag and not incluir_agendados) or (pospuesto and not incluir_agendados):
            continue
        gr = grupos.setdefault(e.cliente_id, dict(cliente=e.cliente, equipos=[], dias=9999, avisado=None,
                                                  agendado=None, pospuesto=None))
        gr["equipos"].append(dict(e=e, est=e.estado_mant(hoy, cfg.dias_aviso), agendado=ag,
                                  pospuesto=e.pospuesto_hasta if pospuesto else None))
        gr["dias"] = min(gr["dias"], (e.proximo_preventivo - hoy).days)
        if e.aviso_fecha and (not gr["avisado"] or e.aviso_fecha > gr["avisado"]):
            gr["avisado"] = e.aviso_fecha
        if ag:
            gr["agendado"] = ag
        if pospuesto:
            gr["pospuesto"] = e.pospuesto_hasta
    salida = sorted(grupos.values(), key=lambda x: x["dias"])
    for gr in salida:
        gr["wa"] = texto_recordatorio(gr["cliente"], [x["e"] for x in gr["equipos"]], cfg)
    return salida


def texto_recordatorio(cliente, equipos, cfg=None):
    """Enlace de WhatsApp con el recordatorio de mantenimiento listo para enviar."""
    wa = cliente.whatsapp
    if not wa:
        return None
    cfg = cfg or Configuracion.actual()
    hoy = ahora().date()
    if len(equipos) == 1:
        e = equipos[0]
        eq_txt, verbo = f"su aire {e.corto}", "tiene pendiente"
    else:
        eq_txt, verbo = f"sus {len(equipos)} aires ({', '.join(e.corto for e in equipos[:4])})", "tienen pendiente"
    prox = min(e.proximo_preventivo for e in equipos if e.proximo_preventivo)
    cuando = ("desde el " + prox.strftime("%d/%m/%Y")) if prox < hoy else ("para el " + prox.strftime("%d/%m/%Y"))
    plantilla = cfg.msg_recordatorio or MSG_RECORDATORIO_DEF
    try:
        txt = plantilla.format(cliente=cliente.nombre, negocio=negocio_cache()["nombre"], equipos=eq_txt,
                               verbo=verbo, cuando=cuando)
    except (KeyError, IndexError, ValueError):
        txt = MSG_RECORDATORIO_DEF.format(cliente=cliente.nombre, negocio=negocio_cache()["nombre"],
                                          equipos=eq_txt, verbo=verbo, cuando=cuando)
    return f"{wa}?text={quote(txt)}"


@app.route("/mantenimientos/avisado", methods=["POST"])
@rol_requerido("admin", "asesor")
def mant_avisado():
    """Marca que al cliente ya se le envió el recordatorio por WhatsApp."""
    cid = int(n(request.form.get("cliente_id")))
    hoy = ahora().date()
    for e in Equipo.query.filter_by(cliente_id=cid, activo=True).all():
        if e.proximo_preventivo and e.proximo_preventivo <= hoy + timedelta(days=Configuracion.actual().dias_aviso):
            e.aviso_fecha = hoy
    db.session.commit()
    return jsonify(ok=True)


@app.route("/mantenimientos/posponer", methods=["POST"])
@rol_requerido("admin", "asesor")
def mant_posponer():
    cid = int(n(request.form.get("cliente_id")))
    dias = max(min(int(n(request.form.get("dias"), 7)), 90), 1)
    hoy = ahora().date()
    lim = hoy + timedelta(days=Configuracion.actual().dias_aviso)
    for e in Equipo.query.filter_by(cliente_id=cid, activo=True).all():
        if e.proximo_preventivo and e.proximo_preventivo <= lim:
            e.pospuesto_hasta = hoy + timedelta(days=dias)
    db.session.commit()
    if request.headers.get("X-CSRF") is not None:
        return jsonify(ok=True)
    flash(f"Recordatorio pospuesto {dias} días.", "ok")
    return redirect(request.form.get("volver") or url_for("mantenimientos"))


@app.route("/mantenimientos/visto", methods=["POST"])
@login_required
def mant_visto():
    session["alertas_vistas"] = ahora().date().isoformat()
    return jsonify(ok=True)


@app.route("/mantenimientos")
@rol_requerido("admin", "asesor")
def mantenimientos():
    cfg = Configuracion.actual()
    hoy = ahora().date()
    ver = request.args.get("ver", "pendientes")
    dias = {"pendientes": cfg.dias_aviso, "30": 30, "60": 60, "90": 90}.get(ver, cfg.dias_aviso)
    grupos = alertas_preventivo(incluir_agendados=True, dias=dias)
    if ver == "pendientes":
        grupos = [gr for gr in grupos if not gr["agendado"] and not gr["pospuesto"]]
    elif ver == "agendados":
        grupos = [gr for gr in alertas_preventivo(incluir_agendados=True, dias=365) if gr["agendado"]]
    sin_fecha = (Equipo.query.filter(Equipo.activo.is_(True), Equipo.frecuencia_meses > 0,
                                     Equipo.proximo_preventivo.is_(None)).all())
    vencidos = sum(1 for gr in grupos for x in gr["equipos"] if x["est"][0] == "vencido")
    return render_template("mantenimientos.html", grupos=grupos, ver=ver, cfg=cfg, sin_fecha=sin_fecha,
                           vencidos=vencidos, n_equipos=sum(len(gr["equipos"]) for gr in grupos), hoy_d=hoy)


# ------------------------------------------------------------------- sesión
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("inicio"))
    if request.method == "POST":
        u = Usuario.query.filter(
            func.lower(Usuario.username) == request.form.get("username", "").strip().lower()).first()
        momento = ahora()
        if u and u.bloqueado_hasta and u.bloqueado_hasta > momento:
            minutos = max(int((u.bloqueado_hasta - momento).total_seconds() // 60) + 1, 1)
            flash(f"Demasiados intentos fallidos. Intenta de nuevo en {minutos} minuto(s).", "error")
            return render_template("login.html")
        if u and u.activo and u.check_password(request.form.get("password", "")):
            u.intentos_fallidos, u.bloqueado_hasta = 0, None
            if u.rol != "superadmin":
                u.ultimo_ingreso = u.ultimo_uso = momento
                u.ingresos = (u.ingresos or 0) + 1
            db.session.commit()
            session.clear()
            login_user(u, remember=bool(request.form.get("recordar")))
            destino = request.args.get("next") or ""
            if not destino.startswith("/") or destino.startswith("//") or "\\" in destino:
                destino = url_for("inicio")
            return redirect(destino)
        if u:
            u.intentos_fallidos = (u.intentos_fallidos or 0) + 1
            if u.intentos_fallidos >= 5:
                u.bloqueado_hasta = momento + timedelta(minutes=15)
                u.intentos_fallidos = 0
            db.session.commit()
        import time
        time.sleep(0.6)
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/cuenta", methods=["GET", "POST"])
@login_required
def cuenta():
    if request.method == "POST":
        actual = request.form.get("actual", "")
        nueva = request.form.get("nueva", "")
        if not current_user.check_password(actual):
            flash("La contraseña actual no es correcta.", "error")
        elif len(nueva) < 6:
            flash("La nueva contraseña debe tener al menos 6 caracteres.", "error")
        elif nueva != request.form.get("confirmar", ""):
            flash("Las contraseñas no coinciden.", "error")
        else:
            current_user.set_password(nueva)
            db.session.commit()
            flash("Contraseña actualizada.", "ok")
            return redirect(url_for("inicio"))
    return render_template("cuenta.html")


# ---------------------------------------------------------------- resumen
def ingresos_periodo(ini, fin):
    ords = (Orden.query.filter(Orden.estado == "terminada", Orden.anulada.is_(False),
                               Orden.fecha_fin >= ini, Orden.fecha_fin < fin).all())
    ves = (VentaEquipo.query.filter(VentaEquipo.anulada.is_(False), VentaEquipo.fecha >= ini,
                                    VentaEquipo.fecha < fin).all())
    return ords, ves


def cartera_pendiente():
    docs = [o for o in Orden.query.filter(Orden.anulada.is_(False), Orden.estado == "terminada",
                                          Orden.condicion == "credito").all() if o.saldo > 0.5]
    docs += [v for v in VentaEquipo.query.filter(VentaEquipo.anulada.is_(False),
                                                 VentaEquipo.condicion == "credito").all() if v.saldo > 0.5]
    return docs


@app.route("/")
@login_required
def inicio():
    if current_user.rol_efectivo != "admin":
        return redirect(url_for("agenda"))
    ini, fin, desde, hasta = periodo()
    hoy_d = ahora().date()
    ords, ves = ingresos_periodo(ini, fin)
    serv = sum(o.total for o in ords)
    ventas_eq = sum(v.precio_total for v in ves)
    ingresos = serv + ventas_eq
    costo_serv = sum(o.costo_materiales + o.pago_tecnico + o.otros_costos for o in ords)
    costo_eq = sum(v.costo_completo for v in ves)
    bruta = ingresos - costo_serv - costo_eq
    hasta_dep = min((fin - timedelta(days=1)).date(), hoy_d)
    depreciacion = depreciacion_periodo(ini.date(), hasta_dep) if hasta_dep >= ini.date() else 0
    gastos_det = (db.session.query(Gasto.categoria, func.sum(Gasto.valor))
                  .filter(Gasto.fecha >= ini.date(), Gasto.fecha < fin.date())
                  .group_by(Gasto.categoria).order_by(func.sum(Gasto.valor).desc()).all())
    gastos_neg = sum(v or 0 for _, v in gastos_det)
    gastos_tot = gastos_neg
    neta = bruta - depreciacion - gastos_tot

    por_tipo = defaultdict(lambda: [0, 0.0, 0.0])
    for o in ords:
        t = por_tipo[o.tipo]
        t[0] += 1
        t[1] += o.total
        t[2] += o.ganancia
    por_tipo = sorted(((TIPOS_SERVICIO.get(k, k), *v) for k, v in por_tipo.items()), key=lambda x: -x[2])
    por_dia = defaultdict(lambda: [0.0, 0.0])
    for o in ords:
        k = o.fecha_fin.date()
        por_dia[k][0] += o.total
        por_dia[k][1] += o.ganancia
    for v in ves:
        k = v.fecha.date()
        por_dia[k][0] += v.precio_total
        por_dia[k][1] += v.ganancia
    dias, d = [], ini.date()
    while d < fin.date():
        dias.append(d)
        d += timedelta(days=1)
    serie = dict(labels=[x.strftime("%d/%m") for x in dias], ingresos=[round(por_dia[x][0]) for x in dias],
                 ganancia=[round(por_dia[x][1]) for x in dias])

    hoy_ini = datetime.combine(hoy_d, datetime.min.time())
    agenda_hoy = (Orden.query.filter(Orden.anulada.is_(False), Orden.estado.in_(["programada", "en_proceso"]),
                                     Orden.fecha < hoy_ini + timedelta(days=2))
                  .order_by(Orden.fecha).all())
    credito = cartera_pendiente()
    insumos = Insumo.query.filter_by(activo=True).all()
    mostrar_modal = session.get("alertas_vistas") != hoy_d.isoformat()
    alertas = alertas_preventivo() if mostrar_modal else []
    garantias_mes = sum(1 for o in ords if o.en_garantia)
    return render_template(
        "dashboard.html", desde=desde, hasta=hasta, ingresos=ingresos, serv=serv, ventas_eq=ventas_eq,
        costo_serv=costo_serv, costo_eq=costo_eq, bruta=bruta, depreciacion=depreciacion, neta=neta,
        gastos_det=gastos_det, gastos_tot=gastos_tot,
        margen=(neta / ingresos * 100) if ingresos else 0, n_ordenes=len(ords), n_ves=len(ves),
        n_equipos_atendidos=sum(len(o.equipos) for o in ords), ganancia_eq=sum(v.ganancia for v in ves),
        por_tipo=por_tipo, serie=serie, agenda_hoy=agenda_hoy, hoy_d=hoy_d,
        cartera=sum(x.saldo for x in credito), cartera_vencida=sum(x.saldo for x in credito if x.vencida),
        n_credito=len(credito), alertas_insumos=[i for i in insumos if i.bajo_minimo],
        valor_insumos=sum(i.valor_stock for i in insumos), alertas=alertas, mostrar_modal=mostrar_modal,
        garantias_mes=garantias_mes,
        cot_pendientes=sum(1 for c in Cotizacion.query.filter(Cotizacion.estado == "pendiente",
                                                              Cotizacion.orden_id.is_(None)).all()
                           if c.estado_info[0] == "Pendiente"))


# ------------------------------------------------------------------ agenda
@app.route("/agenda")
@login_required
def agenda():
    hoy_d = ahora().date()
    tec = empleado_actual() if current_user.rol_efectivo == "tecnico" else None
    if current_user.rol_efectivo == "tecnico" and not tec:
        return render_template("error.html", titulo="Sin técnico asignado",
                               mensaje="Tu usuario no está vinculado a un técnico. Pídele al administrador que lo "
                                       "vincule en Técnicos → Editar.")
    dias = max(min(int(n(request.args.get("dias"), 7)), 60), 1)
    tecnico_id = tec.id if tec else request.args.get("tecnico", type=int)
    q = Orden.query.filter(Orden.anulada.is_(False), Orden.estado.in_(["programada", "en_proceso"]),
                           Orden.fecha < datetime.combine(hoy_d + timedelta(days=dias), datetime.min.time()))
    if tecnico_id:
        q = q.filter(Orden.tecnico_id == tecnico_id)
    ordenes = q.order_by(Orden.fecha).all()
    atrasadas = [o for o in ordenes if o.fecha.date() < hoy_d]
    por_dia = defaultdict(list)
    for o in ordenes:
        if o.fecha.date() >= hoy_d:
            por_dia[o.fecha.date()].append(o)
    ini_hoy = datetime.combine(hoy_d, datetime.min.time())
    q2 = Orden.query.filter(Orden.anulada.is_(False), Orden.estado == "terminada", Orden.fecha_fin >= ini_hoy)
    if tecnico_id:
        q2 = q2.filter(Orden.tecnico_id == tecnico_id)
    hechas_hoy = q2.order_by(Orden.fecha_fin.desc()).all()
    mostrar_modal = (current_user.rol_efectivo == "asesor" and session.get("alertas_vistas") != hoy_d.isoformat())
    return render_template("agenda.html", atrasadas=atrasadas, por_dia=sorted(por_dia.items()), dias=dias,
                           tecnicos=Empleado.query.filter_by(activo=True).order_by(Empleado.nombre).all(),
                           tecnico_id=tecnico_id, tec=tec, hechas_hoy=hechas_hoy, hoy_d=hoy_d,
                           mostrar_modal=mostrar_modal, alertas=alertas_preventivo() if mostrar_modal else [])


# ---------------------------------------------------------------- órdenes
def puede_ver_orden(o):
    if current_user.rol_efectivo != "tecnico":
        return True
    tec = empleado_actual()
    return bool(tec and o.tecnico_id == tec.id)


@app.route("/ordenes")
@rol_requerido("admin", "asesor")
def ordenes():
    ini, fin, desde, hasta = periodo()
    tipo = request.args.get("tipo", "")
    estado = request.args.get("estado", "")
    tecnico_id = request.args.get("tecnico", type=int)
    q_txt = request.args.get("q", "").strip()
    q = Orden.query.filter(or_(Orden.fecha_fin.between(ini, fin), Orden.fecha.between(ini, fin)))
    if tipo in TIPOS_SERVICIO:
        q = q.filter(Orden.tipo == tipo)
    if estado == "anulada":
        q = q.filter(Orden.anulada.is_(True))
    elif estado in ESTADOS_ORDEN:
        q = q.filter(Orden.estado == estado, Orden.anulada.is_(False))
    if tecnico_id:
        q = q.filter(Orden.tecnico_id == tecnico_id)
    if q_txt:
        q = q.filter(Orden.cliente_nombre.ilike(f"%{q_txt}%"))
    items = q.order_by(Orden.fecha.desc()).all()
    hechas = [o for o in items if o.terminada]
    return render_template("ordenes.html", items=items, desde=desde, hasta=hasta, tipo=tipo, estado=estado,
                           tecnico_id=tecnico_id, q=q_txt, estados=ESTADOS_ORDEN,
                           tecnicos=Empleado.query.order_by(Empleado.nombre).all(),
                           total=sum(o.total for o in hechas), ganancia=sum(o.ganancia for o in hechas),
                           n_hechas=len(hechas), saldo=sum(o.saldo for o in hechas))


def precios_por_equipo():
    """{equipo_id: {tipo: último precio cobrado}} para sugerir el valor en cada equipo."""
    out = defaultdict(dict)
    for eid, tipo, precio in (db.session.query(OrdenLinea.equipo_id, Orden.tipo, OrdenLinea.precio)
                              .join(Orden, OrdenLinea.orden_id == Orden.id)
                              .filter(Orden.anulada.is_(False), Orden.en_garantia.is_(False), OrdenLinea.precio > 0,
                                      OrdenLinea.equipo_id.isnot(None))
                              .order_by(Orden.id).all()):
        out[eid][tipo] = precio
    return out


def clientes_json():
    hoy = ahora().date()
    cfg = Configuracion.actual()
    clis = Cliente.query.filter_by(activo=True).order_by(Cliente.nombre).all()
    precios_eq = precios_por_equipo()
    out = []
    for c in clis:
        eqs = []
        for e in c.equipos_activos:
            gar = garantia_vigente(e)
            est = e.estado_mant(hoy, cfg.dias_aviso)
            eqs.append(dict(id=e.id, nombre=e.nombre, corto=e.corto, est=est[1], cls=est[2], precios=precios_eq.get(e.id, {}),
                            gar=(dict(num=num_orden(gar), hasta=gar.garantia_hasta.strftime("%d/%m/%Y"), id=gar.id)
                                 if gar else None)))
        out.append(dict(id=c.id, nombre=c.nombre, tel=c.telefono or "", dir=c.direccion_completa,
                        plazo=c.plazo_dias or 0, equipos=eqs))
    return out


def insumos_json():
    return [dict(id=i.id, nombre=i.nombre, unidad=i.unidad, cat=i.categoria or "Otros", costo=i.costo_promedio or 0,
                 precio=i.precio_venta or 0, stock=i.stock or 0)
            for i in Insumo.query.filter_by(activo=True).order_by(Insumo.categoria, Insumo.nombre)]


def tecnicos_json():
    return [dict(id=e.id, nombre=e.nombre, tipo=e.tipo_pago, ctipo=e.comision_tipo, cval=e.comision_valor or 0)
            for e in Empleado.query.filter_by(activo=True).order_by(Empleado.nombre)]


def precios_sugeridos():
    """Último precio cobrado por equipo en cada tipo de servicio (para sugerirlo en el formulario)."""
    out = {}
    for tipo in TIPOS_SERVICIO:
        l = (OrdenLinea.query.join(Orden, OrdenLinea.orden_id == Orden.id)
             .filter(Orden.tipo == tipo, Orden.anulada.is_(False), Orden.en_garantia.is_(False),
                     OrdenLinea.precio > 0, OrdenLinea.equipo_id.isnot(None))
             .order_by(Orden.id.desc()).first())
        out[tipo] = l.precio if l else 0
    return out


def _leer_orden_form(o, f):
    """Llena la cabecera de la orden con el formulario. Devuelve (lineas, materiales, estado, errores)."""
    errores = []
    cli = db.session.get(Cliente, int(n(f.get("cliente_id")))) if n(f.get("cliente_id")) else None
    if not cli:
        nombre = f.get("cliente_nuevo", "").strip()
        if nombre:
            cli = Cliente.query.filter(func.lower(Cliente.nombre) == nombre.lower(), Cliente.activo.is_(True)).first()
            if not cli:
                cli = Cliente(nombre=nombre[:120], telefono=f.get("cliente_tel", "").strip()[:40] or None,
                              direccion=f.get("direccion", "").strip()[:160] or None)
                db.session.add(cli)
                db.session.flush()
    if not cli:
        errores.append("Elige el cliente o escribe el nombre de uno nuevo.")
    tipo = f.get("tipo") if f.get("tipo") in TIPOS_SERVICIO else "preventivo"
    estado = f.get("estado") if f.get("estado") in ESTADOS_ORDEN else "programada"
    fecha = fecha_hora(f.get("fecha"), f.get("hora"))
    if not fecha:
        errores.append("Indica la fecha de la visita.")
    o.tipo = tipo
    if cli:
        o.cliente_id, o.cliente_nombre = cli.id, cli.nombre
    o.fecha = fecha or o.fecha or ahora()
    o.direccion = f.get("direccion", "").strip()[:200] or (cli.direccion_completa if cli else None)
    tid = int(n(f.get("tecnico_id")))
    o.tecnico_id = tid if tid and db.session.get(Empleado, tid) else None
    for campo in ("descripcion", "trabajo", "recomendaciones"):
        setattr(o, campo, f.get(campo, "").strip()[:4000] or None)
    o.nota = f.get("nota", "").strip()[:255] or None
    o.en_garantia = bool(f.get("en_garantia"))
    o.garantia_de_id = int(n(f.get("garantia_de_id"))) or None if o.en_garantia else None
    o.garantia_dias = max(int(n(f.get("garantia_dias"))), 0)
    o.condicion = "credito" if f.get("condicion") == "credito" else "contado"
    o.metodo_pago = f.get("metodo_pago") if f.get("metodo_pago") in METODOS_PAGO else "Efectivo"
    o.descuento = n(f.get("descuento"))
    o.iva_pct = n(f.get("iva_pct"))
    o.pago_tecnico = n(f.get("pago_tecnico"))
    o.otros_costos = n(f.get("otros_costos"))
    lineas = []
    for eid, desc, c, p in zip(f.getlist("linea_equipo[]"), f.getlist("linea_desc[]"),
                               f.getlist("linea_cant[]"), f.getlist("linea_precio[]")):
        eq = db.session.get(Equipo, int(n(eid))) if n(eid) else None
        if eq or (desc or "").strip():
            lineas.append((eq, desc, n(c, 1) or 1, n(p)))
    materiales = []
    for iid, c, p in zip(f.getlist("mat_insumo[]"), f.getlist("mat_cant[]"), f.getlist("mat_precio[]")):
        ins = db.session.get(Insumo, int(n(iid))) if n(iid) else None
        if ins and n(c) > 0:
            materiales.append((ins, n(c), n(p)))
    if not f.get("pago_tecnico", "").strip() and o.tecnico_id and not o.en_garantia:
        # Sin valor escrito: se usa la comisión configurada del técnico sobre la mano de obra
        o.pago_tecnico = db.session.get(Empleado, o.tecnico_id).comision_para(sum(c * p for _, _, c, p in lineas))
    return lineas, materiales, estado, errores


def _form_orden(o, extra=None):
    neg = DatosNegocio.actual()
    cfg = Configuracion.actual()
    datos = dict(o=o, clientes=clientes_json(), insumos=insumos_json(), tecnicos=tecnicos_json(),
                 metodos=METODOS_PAGO, estados=ESTADOS_ORDEN, iva_def=neg.iva_pct or 0,
                 precios=precios_sugeridos(), garantia_def=cfg.garantia_dias, con_garantia=sorted(CON_GARANTIA))
    datos.update(extra or {})
    return render_template("orden_form.html", **datos)


@app.route("/ordenes/nueva", methods=["GET", "POST"])
@rol_requerido("admin", "asesor")
def orden_nueva():
    if request.method == "POST":
        o = Orden(usuario_id=current_user.id)
        lineas, materiales, estado, errores = _leer_orden_form(o, request.form)
        o.cotizacion_id = int(n(request.form.get("cotizacion_id"))) or None
        try:
            if errores:
                raise ErrorNegocio(errores)
            db.session.add(o)
            guardar_orden(o, lineas, materiales, estado,
                          fecha_fin=fecha_hora(request.form.get("fecha_fin"), request.form.get("hora_fin")),
                          plazo_dias=int(n(request.form.get("plazo_dias"))))
            if o.cotizacion_id:
                cot = db.session.get(Cotizacion, o.cotizacion_id)
                if cot:
                    cot.orden_id, cot.estado = o.id, "aprobada"
            db.session.commit()
            flash(f"Orden {num_orden(o)} guardada ({o.estado_info[0].lower()}).", "ok")
            return redirect(url_for("orden_detalle", id=o.id))
        except ErrorNegocio as e:
            db.session.rollback()
            for m in e.errores:
                flash(m, "error")
            return _form_orden(None, dict(pre=_pre_desde_form(request.form)))
    # Valores iniciales desde la URL (agendar desde una alerta, convertir una cotización, garantía…)
    pre = dict(cliente_id=request.args.get("cliente", type=int), tipo=request.args.get("tipo", "preventivo"),
               equipos=[int(x) for x in request.args.getlist("equipo") if x.isdigit()], lineas=[], materiales=[],
               fecha=request.args.get("fecha") or ahora().date().isoformat(), hora="08:00",
               garantia_de_id=request.args.get("garantia_de", type=int))
    cot = db.session.get(Cotizacion, request.args.get("cotizacion", type=int) or 0)
    if cot:
        pre.update(cliente_id=cot.cliente_id, tipo=cot.tipo or "correctivo", cotizacion_id=cot.id,
                   descripcion=cot.descripcion or "", descuento=cot.descuento, iva_pct=cot.iva_pct)
        for l in cot.lineas:
            if l.insumo_id:
                pre["materiales"].append(dict(insumo=l.insumo_id, cant=l.cantidad, precio=l.precio))
            else:
                pre["lineas"].append(dict(equipo=l.equipo_id or "", desc=l.descripcion, cant=l.cantidad,
                                          precio=l.precio))
    if pre["garantia_de_id"]:
        orig = db.session.get(Orden, pre["garantia_de_id"])
        if orig:
            pre.update(cliente_id=orig.cliente_id, tipo="correctivo", en_garantia=True,
                       equipos=pre["equipos"] or [e.id for e in orig.equipos],
                       descripcion=f"Visita en garantía de la orden {num_orden(orig)}.")
    return _form_orden(None, dict(pre=pre))


def _pre_desde_form(f):
    """Reconstruye el formulario después de un error, para no perder lo escrito."""
    return dict(cliente_id=int(n(f.get("cliente_id"))) or None, tipo=f.get("tipo", "preventivo"),
                estado=f.get("estado"), fecha=f.get("fecha"), hora=f.get("hora"), tecnico_id=int(n(f.get("tecnico_id"))),
                descripcion=f.get("descripcion", ""), trabajo=f.get("trabajo", ""),
                recomendaciones=f.get("recomendaciones", ""), en_garantia=bool(f.get("en_garantia")),
                garantia_de_id=int(n(f.get("garantia_de_id"))) or None, garantia_dias=f.get("garantia_dias"),
                condicion=f.get("condicion"), metodo_pago=f.get("metodo_pago"), descuento=n(f.get("descuento")),
                iva_pct=n(f.get("iva_pct")), pago_tecnico=n(f.get("pago_tecnico")),
                otros_costos=n(f.get("otros_costos")), nota=f.get("nota", ""), direccion=f.get("direccion", ""),
                cotizacion_id=int(n(f.get("cotizacion_id"))) or None, equipos=[],
                lineas=[dict(equipo=e or "", desc=d, cant=n(c, 1), precio=n(p)) for e, d, c, p in
                        zip(f.getlist("linea_equipo[]"), f.getlist("linea_desc[]"), f.getlist("linea_cant[]"),
                            f.getlist("linea_precio[]"))],
                materiales=[dict(insumo=i, cant=n(c), precio=n(p)) for i, c, p in
                            zip(f.getlist("mat_insumo[]"), f.getlist("mat_cant[]"), f.getlist("mat_precio[]")) if i])


@app.route("/ordenes/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin", "asesor")
def orden_editar(id):
    o = db.session.get(Orden, id) or abort(404)
    if o.anulada:
        flash("La orden está anulada; no se puede editar.", "error")
        return redirect(url_for("orden_detalle", id=id))
    if request.method == "POST":
        lineas, materiales, estado, errores = _leer_orden_form(o, request.form)
        try:
            if errores:
                raise ErrorNegocio(errores)
            if estado != "terminada" and o.estado == "terminada" and sum(a.valor for a in o.abonos if not a.anulado):
                raise ErrorNegocio("La orden tiene abonos; anúlalos antes de reabrirla.")
            guardar_orden(o, lineas, materiales, estado,
                          fecha_fin=fecha_hora(request.form.get("fecha_fin"), request.form.get("hora_fin")),
                          plazo_dias=int(n(request.form.get("plazo_dias"))))
            db.session.commit()
            flash(f"Orden {num_orden(o)} actualizada.", "ok")
            return redirect(url_for("orden_detalle", id=o.id))
        except ErrorNegocio as e:
            db.session.rollback()
            for m in e.errores:
                flash(m, "error")
            o = db.session.get(Orden, id)
    pre = dict(cliente_id=o.cliente_id, tipo=o.tipo, estado=o.estado, fecha=o.fecha.date().isoformat(),
               hora=o.fecha.strftime("%H:%M"), tecnico_id=o.tecnico_id, descripcion=o.descripcion or "",
               trabajo=o.trabajo or "", recomendaciones=o.recomendaciones or "", en_garantia=o.en_garantia,
               garantia_de_id=o.garantia_de_id, garantia_dias=o.garantia_dias, condicion=o.condicion,
               metodo_pago=o.metodo_pago, descuento=o.descuento, iva_pct=o.iva_pct, pago_tecnico=o.pago_tecnico,
               otros_costos=o.otros_costos, nota=o.nota or "", direccion=o.direccion or "", equipos=[],
               fecha_fin=o.fecha_fin.date().isoformat() if o.fecha_fin else "",
               hora_fin=o.fecha_fin.strftime("%H:%M") if o.fecha_fin else "",
               plazo_dias=((o.vence - o.fecha_fin.date()).days if o.vence and o.fecha_fin else
                           (o.cliente.plazo_dias if o.cliente else 0)),
               lineas=[dict(equipo=l.equipo_id or "", desc=l.descripcion, cant=l.cantidad, precio=l.precio)
                       for l in o.lineas],
               materiales=[dict(insumo=m.insumo_id, cant=m.cantidad, precio=m.precio_unitario) for m in o.materiales])
    return _form_orden(o, dict(pre=pre))


@app.route("/ordenes/<int:id>")
@login_required
def orden_detalle(id):
    o = db.session.get(Orden, id) or abort(404)
    if not puede_ver_orden(o):
        abort(404)
    garantias = Orden.query.filter(Orden.garantia_de_id == o.id).order_by(Orden.fecha).all()
    avisos_gar = []
    if not o.en_garantia and o.estado != "terminada":
        for e in o.equipos:
            gv = garantia_vigente(e, excluir_orden_id=o.id)
            if gv:
                avisos_gar.append((e, gv))
    wa_cli = None
    if o.cliente and o.cliente.whatsapp:
        if o.terminada:
            txt = (f"Hola {o.cliente.nombre}, le saluda {negocio_cache()['nombre']}. Le compartimos la orden "
                   f"{num_orden(o)} del servicio de {o.tipo_txt.lower()} por {filtro_cop(o.total_factura)}. ¡Gracias!")
        else:
            txt = (f"Hola {o.cliente.nombre}, le saluda {negocio_cache()['nombre']}. Le confirmamos la visita de "
                   f"{o.tipo_txt.lower()} el {filtro_dia(o.fecha).lower()} {o.fecha.strftime('%d/%m')} a las "
                   f"{filtro_hora(o.fecha)}" + (f" con el técnico {o.tecnico.nombre}" if o.tecnico else "") + ".")
        wa_cli = f"{o.cliente.whatsapp}?text={quote(txt)}"
    return render_template("orden_detalle.html", o=o, garantias=garantias, avisos_gar=avisos_gar,
                           metodos=METODOS_PAGO, wa_cli=wa_cli, insumos=insumos_json(),
                           wa_tec=o.tecnico.whatsapp if o.tecnico else None)


@app.route("/ordenes/<int:id>/estado", methods=["POST"])
@login_required
def orden_estado(id):
    o = db.session.get(Orden, id) or abort(404)
    if not puede_ver_orden(o):
        abort(404)
    estado = request.form.get("estado")
    if estado not in ESTADOS_ORDEN or (current_user.rol_efectivo == "tecnico" and estado not in ("en_proceso",)):
        abort(400)
    try:
        cambiar_estado(o, estado)
        db.session.commit()
        flash(f"Orden {num_orden(o)}: {o.estado_info[0].lower()}.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        for m in e.errores:
            flash(m, "error")
    return redirect(url_for("orden_detalle", id=id))


@app.route("/ordenes/<int:id>/terminar", methods=["POST"])
@login_required
def orden_terminar(id):
    """Cierre de la orden desde el detalle (también lo usa el técnico desde su celular)."""
    o = db.session.get(Orden, id) or abort(404)
    if not puede_ver_orden(o):
        abort(404)
    f = request.form
    try:
        if f.get("recomendaciones", "").strip():
            o.recomendaciones = f.get("recomendaciones").strip()[:4000]
        if not o.aplicada:
            for iid, c in zip(f.getlist("mat_insumo[]"), f.getlist("mat_cant[]")):
                ins = db.session.get(Insumo, int(n(iid))) if n(iid) else None
                if ins and n(c) > 0:
                    o.materiales.append(OrdenMaterial(insumo=ins, cantidad=n(c),
                                                      precio_unitario=0 if o.en_garantia else (ins.precio_venta or 0)))
        terminar_orden(o, trabajo=f.get("trabajo", ""))
        db.session.commit()
        flash(f"Orden {num_orden(o)} terminada. El plan de mantenimiento de los equipos se actualizó.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        for m in e.errores:
            flash(m, "error")
    return redirect(url_for("orden_detalle", id=id))


@app.route("/ordenes/<int:id>/anular", methods=["POST"])
@rol_requerido("admin")
def orden_anular(id):
    o = db.session.get(Orden, id) or abort(404)
    try:
        anular_orden(o)
        db.session.commit()
        flash(f"Orden {num_orden(o)} anulada. Los materiales volvieron al inventario.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        flash(e.errores[0], "error")
    return redirect(url_for("orden_detalle", id=id))


@app.route("/ordenes/<int:id>/documento.<fmt>")
@login_required
def orden_documento(id, fmt):
    o = db.session.get(Orden, id) or abort(404)
    if not puede_ver_orden(o):
        abort(404)
    neg = DatosNegocio.actual()
    if o.garantia_de:
        o.garantia_de_num = num_orden(o.garantia_de)
    d = comprobantes.datos_orden(o, neg, num_orden(o))
    return _enviar_doc(d, fmt, num_orden(o))


def _enviar_doc(d, fmt, nombre):
    if fmt == "pdf":
        data, mime = comprobantes.pdf(d), "application/pdf"
    elif fmt == "jpg":
        data, mime = comprobantes.jpg(d), "image/jpeg"
    else:
        abort(404)
    modo = "attachment" if request.args.get("descargar") else "inline"
    return Response(data, mimetype=mime, headers={"Content-Disposition": f'{modo}; filename="{nombre}.{fmt}"'})


# ----------------------------------------------------------------- clientes
@app.route("/clientes")
@rol_requerido("admin", "asesor")
def clientes():
    hoy = ahora().date()
    cfg = Configuracion.actual()
    q = request.args.get("q", "").strip()
    filtro = request.args.get("f", "")
    clis = Cliente.query.filter_by(activo=True).order_by(Cliente.nombre).all()
    ult = dict(db.session.query(Orden.cliente_id, func.max(Orden.fecha_fin))
               .filter(Orden.estado == "terminada", Orden.anulada.is_(False)).group_by(Orden.cliente_id).all())
    nserv = dict(db.session.query(Orden.cliente_id, func.count(Orden.id))
                 .filter(Orden.estado == "terminada", Orden.anulada.is_(False)).group_by(Orden.cliente_id).all())
    saldo = defaultdict(float)
    for x in cartera_pendiente():
        if x.cliente_id:
            saldo[x.cliente_id] += x.saldo
    filas = []
    for c in clis:
        if q and not (q.lower() in c.nombre.lower() or q in (c.telefono or "") or
                      q.lower() in (c.direccion_completa or "").lower()):
            continue
        estados = [e.estado_mant(hoy, cfg.dias_aviso) for e in c.equipos_activos]
        peor = ("vencido" if any(s[0] == "vencido" for s in estados) else
                "proximo" if any(s[0] == "proximo" for s in estados) else
                "al_dia" if any(s[0] == "al_dia" for s in estados) else "sin")
        prox = min([e.proximo_preventivo for e in c.equipos_activos if e.proximo_preventivo and e.con_plan] or [None],
                   key=lambda d: d or date.max)
        fila = dict(c=c, n_eq=len(c.equipos_activos), peor=peor, prox=prox, ultimo=ult.get(c.id),
                    servicios=nserv.get(c.id, 0), saldo=saldo.get(c.id, 0))
        if filtro == "vencidos" and peor != "vencido":
            continue
        if filtro == "proximos" and peor not in ("vencido", "proximo"):
            continue
        if filtro == "debe" and fila["saldo"] <= 0.5:
            continue
        filas.append(fila)
    orden_peor = {"vencido": 0, "proximo": 1, "al_dia": 2, "sin": 3}
    filas.sort(key=lambda f: (orden_peor[f["peor"]], f["prox"] or date.max, f["c"].nombre.lower()))
    return render_template("clientes.html", filas=filas, q=q, f=filtro, total=len(clis),
                           n_equipos=Equipo.query.filter_by(activo=True).count())


@app.route("/clientes/nuevo", methods=["GET", "POST"])
@app.route("/clientes/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin", "asesor")
def cliente_form(id=None):
    c = db.session.get(Cliente, id) if id else None
    if id and not c:
        abort(404)
    if request.method == "POST":
        f = request.form
        nombre = f.get("nombre", "").strip()
        dup = Cliente.query.filter(func.lower(Cliente.nombre) == nombre.lower(), Cliente.activo.is_(True),
                                   Cliente.id != (id or 0)).first()
        if not nombre:
            flash("Escribe el nombre del cliente.", "error")
        elif dup:
            flash("Ya existe un cliente con ese nombre.", "error")
        else:
            nuevo = c is None
            if nuevo:
                c = Cliente()
                db.session.add(c)
            c.nombre = nombre[:120]
            for campo, largo in (("telefono", 40), ("email", 120), ("direccion", 160), ("barrio", 80),
                                 ("ciudad", 80), ("identificacion", 40), ("contacto", 120), ("nota", 255)):
                setattr(c, campo, f.get(campo, "").strip()[:largo] or None)
            c.tipo = f.get("tipo") if f.get("tipo") in TIPOS_CLIENTE else "Otro"
            c.plazo_dias = max(int(n(f.get("plazo_dias"))), 0)
            db.session.commit()
            flash(f"Cliente «{c.nombre}» guardado." + (" Ahora agrégale sus equipos." if nuevo else ""), "ok")
            if nuevo:
                return redirect(url_for("equipo_form", cliente_id=c.id))
            return redirect(url_for("cliente_detalle", id=c.id))
    return render_template("cliente_form.html", c=c, tipos=TIPOS_CLIENTE)


@app.route("/clientes/<int:id>")
@rol_requerido("admin", "asesor")
def cliente_detalle(id):
    c = db.session.get(Cliente, id) or abort(404)
    hoy = ahora().date()
    cfg = Configuracion.actual()
    agendados = equipos_agendados()
    equipos = [dict(e=e, est=e.estado_mant(hoy, cfg.dias_aviso), gar=garantia_vigente(e),
                    agendado=agendados.get(e.id)) for e in c.equipos_activos]
    ords = Orden.query.filter(Orden.cliente_id == id).order_by(Orden.fecha.desc()).limit(40).all()
    cots = Cotizacion.query.filter(Cotizacion.cliente_id == id).order_by(Cotizacion.fecha.desc()).limit(10).all()
    ves = VentaEquipo.query.filter(VentaEquipo.cliente_id == id).order_by(VentaEquipo.fecha.desc()).all()
    pendientes = [x for x in cartera_pendiente() if x.cliente_id == id]
    por_vencer = [x["e"] for x in equipos if x["est"][0] in ("vencido", "proximo")]
    hechas = [o for o in ords if o.terminada]
    return render_template("cliente_detalle.html", c=c, equipos=equipos, ordenes=ords, cots=cots, ves=ves,
                           pendientes=pendientes, saldo=sum(x.saldo for x in pendientes),
                           wa_mant=texto_recordatorio(c, por_vencer, cfg) if por_vencer else None,
                           por_vencer=por_vencer, retirados=[e for e in c.equipos if not e.activo],
                           total_hist=sum(o.total for o in hechas) + sum(v.precio_total for v in ves if not v.anulada),
                           n_hechas=len(hechas), wa_cobro=_texto_cobro(c, pendientes), metodos=METODOS_PAGO)


def _texto_cobro(c, pendientes):
    if not pendientes or not c.whatsapp:
        return None
    total = sum(x.saldo for x in pendientes)
    det = "; ".join(f"{num_orden(x) if isinstance(x, Orden) else x.numero} por {filtro_cop(x.saldo)}"
                    + (f" (vence {x.vence.strftime('%d/%m')})" if x.vence else "") for x in pendientes[:6])
    txt = (f"Hola {c.nombre}, le saluda {negocio_cache()['nombre']}. Le recordamos su saldo pendiente de "
           f"{filtro_cop(total)}: {det}. ¡Gracias!")
    return f"{c.whatsapp}?text={quote(txt)}"


@app.route("/clientes/<int:id>/eliminar", methods=["POST"])
@rol_requerido("admin")
def cliente_eliminar(id):
    c = db.session.get(Cliente, id) or abort(404)
    c.activo = False
    db.session.commit()
    flash(f"Cliente «{c.nombre}» retirado (sus órdenes y equipos se conservan).", "ok")
    return redirect(url_for("clientes"))


# ------------------------------------------------------------------ equipos
@app.route("/clientes/<int:cliente_id>/equipos/nuevo", methods=["GET", "POST"])
@app.route("/equipos/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin", "asesor")
def equipo_form(cliente_id=None, id=None):
    e = db.session.get(Equipo, id) if id else None
    if id and not e:
        abort(404)
    cli = e.cliente if e else (db.session.get(Cliente, cliente_id) or abort(404))
    cfg = Configuracion.actual()
    if request.method == "POST":
        f = request.form
        cant = max(min(int(n(f.get("cantidad"), 1)), 30), 1) if not e else 1
        frec = int(n(f.get("frecuencia_meses")))
        if frec not in FRECUENCIAS:
            frec = 0
        btu = int(n(f.get("capacidad_btu")))
        ult = _fecha(f.get("ultimo_manual"))
        inst = _fecha(f.get("fecha_instalacion"))
        hoy = ahora().date()
        if (ult and ult > hoy) or (inst and inst > hoy):
            flash("Las fechas del último mantenimiento y de instalación no pueden ser futuras.", "error")
        else:
            creados = []
            for i in range(cant):
                obj = e or Equipo(cliente_id=cli.id)
                if not e:
                    db.session.add(obj)
                obj.tipo = f.get("tipo") if f.get("tipo") in TIPOS_EQUIPO else "Otro"
                obj.marca = f.get("marca", "").strip()[:60] or None
                obj.modelo = f.get("modelo", "").strip()[:80] or None
                obj.capacidad_btu = btu or None
                obj.refrigerante = f.get("refrigerante") if f.get("refrigerante") in REFRIGERANTES else None
                obj.serial = f.get("serial", "").strip()[:80] or None if cant == 1 else None
                ubic = f.get("ubicacion", "").strip()[:80] or None
                obj.ubicacion = (f"{ubic or 'Equipo'} {i + 1}" if cant > 1 else ubic)
                obj.inverter = bool(f.get("inverter"))
                obj.fecha_instalacion = inst
                obj.frecuencia_meses = frec
                obj.ultimo_manual = ult
                obj.nota = f.get("nota", "").strip()[:255] or None
                db.session.flush()
                recalcular_equipo(obj)
                creados.append(obj)
            db.session.commit()
            if e:
                flash(f"Equipo «{e.corto}» actualizado.", "ok")
            else:
                flash(f"{cant} equipo{'s' if cant > 1 else ''} agregado{'s' if cant > 1 else ''} a {cli.nombre}."
                      + ("" if frec else " Sin plan de preventivo."), "ok")
                if f.get("otro"):
                    return redirect(url_for("equipo_form", cliente_id=cli.id))
            return redirect(url_for("cliente_detalle", id=cli.id))
    return render_template("equipo_form.html", e=e, cli=cli, tipos=TIPOS_EQUIPO, refrigerantes=REFRIGERANTES,
                           capacidades=CAPACIDADES, frecuencias=FRECUENCIAS, frec_def=cfg.frecuencia_def)


@app.route("/equipos/<int:id>")
@rol_requerido("admin", "asesor")
def equipo_detalle(id):
    e = db.session.get(Equipo, id) or abort(404)
    hoy = ahora().date()
    cfg = Configuracion.actual()
    hist = (Orden.query.join(OrdenLinea, OrdenLinea.orden_id == Orden.id)
            .filter(OrdenLinea.equipo_id == id).order_by(Orden.fecha.desc()).distinct().all())
    return render_template("equipo_detalle.html", e=e, hist=hist, est=e.estado_mant(hoy, cfg.dias_aviso),
                           gar=garantia_vigente(e), agendado=equipos_agendados().get(e.id),
                           wa=texto_recordatorio(e.cliente, [e], cfg) if e.proximo_preventivo else None)


@app.route("/equipos/<int:id>/retirar", methods=["POST"])
@rol_requerido("admin", "asesor")
def equipo_retirar(id):
    e = db.session.get(Equipo, id) or abort(404)
    e.activo = not e.activo
    db.session.commit()
    flash(f"Equipo «{e.corto}» " + ("reactivado." if e.activo else "retirado: ya no genera alertas."), "ok")
    return redirect(url_for("cliente_detalle", id=e.cliente_id))


# ------------------------------------------------------------- cotizaciones
@app.route("/cotizaciones")
@rol_requerido("admin", "asesor")
def cotizaciones():
    ini, fin, desde, hasta = periodo()
    estado = request.args.get("estado", "")
    items = (Cotizacion.query.filter(Cotizacion.fecha >= ini, Cotizacion.fecha < fin)
             .order_by(Cotizacion.fecha.desc()).all())
    if estado:
        items = [c for c in items if c.estado_info[0].lower().startswith(estado)]
    todas = items
    aprobadas = [c for c in todas if c.orden_id or c.estado == "aprobada"]
    return render_template("cotizaciones.html", items=items, desde=desde, hasta=hasta, estado=estado,
                           total=sum(c.total_factura for c in todas),
                           total_aprob=sum(c.total_factura for c in aprobadas),
                           tasa=(len(aprobadas) / len(todas) * 100) if todas else 0)


@app.route("/cotizaciones/nueva", methods=["GET", "POST"])
@app.route("/cotizaciones/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin", "asesor")
def cotizacion_form(id=None):
    c = db.session.get(Cotizacion, id) if id else None
    if id and not c:
        abort(404)
    if c and c.orden_id:
        flash("Esta cotización ya se convirtió en orden; no se puede editar.", "error")
        return redirect(url_for("cotizacion_detalle", id=id))
    neg = DatosNegocio.actual()
    if request.method == "POST":
        f = request.form
        cli = db.session.get(Cliente, int(n(f.get("cliente_id")))) if n(f.get("cliente_id")) else None
        nombre = f.get("cliente_nuevo", "").strip()
        if not cli and nombre:
            cli = Cliente.query.filter(func.lower(Cliente.nombre) == nombre.lower(), Cliente.activo.is_(True)).first()
            if not cli:
                cli = Cliente(nombre=nombre[:120], telefono=f.get("cliente_tel", "").strip()[:40] or None)
                db.session.add(cli)
                db.session.flush()
        lineas = []
        for tipo_l, ref, desc, cant_, precio in zip(f.getlist("l_tipo[]"), f.getlist("l_ref[]"), f.getlist("l_desc[]"),
                                                   f.getlist("l_cant[]"), f.getlist("l_precio[]")):
            if not (desc or "").strip() and not n(ref):
                continue
            ins = db.session.get(Insumo, int(n(ref))) if tipo_l == "mat" and n(ref) else None
            eq = db.session.get(Equipo, int(n(ref))) if tipo_l == "serv" and n(ref) else None
            d = (desc or "").strip() or (ins.nombre if ins else (eq.corto if eq else ""))
            lineas.append(CotizacionLinea(insumo=ins, equipo=eq if eq and cli and eq.cliente_id == cli.id else None,
                                          descripcion=d[:200], cantidad=n(cant_, 1) or 1, precio=n(precio)))
        errores = []
        if not cli:
            errores.append("Elige el cliente o escribe el nombre de uno nuevo.")
        if not lineas:
            errores.append("Agrega al menos un ítem a la cotización.")
        if any(l.precio < 0 or l.cantidad <= 0 for l in lineas):
            errores.append("Revisa cantidades y precios.")
        if errores:
            db.session.rollback()
            for m in errores:
                flash(m, "error")
        else:
            if not c:
                c = Cotizacion(usuario_id=current_user.id)
                db.session.add(c)
            c.cliente_id, c.cliente_nombre = cli.id, cli.nombre
            c.tipo = f.get("tipo") if f.get("tipo") in TIPOS_SERVICIO else "correctivo"
            c.descripcion = f.get("descripcion", "").strip()[:4000] or None
            c.nota = f.get("nota", "").strip()[:500] or None
            c.validez_dias = max(int(n(f.get("validez_dias"), 15)), 1)
            c.fecha = fecha_form(f.get("fecha")) if f.get("fecha") else (c.fecha or ahora())
            c.lineas.clear()
            db.session.flush()
            for l in lineas:
                l.subtotal = l.cantidad * l.precio
                c.lineas.append(l)
            sub = sum(l.subtotal for l in lineas)
            c.descuento = min(max(n(f.get("descuento")), 0), sub)
            c.iva_pct = min(max(n(f.get("iva_pct")), 0), 100)
            c.total = sub - c.descuento
            c.iva = round(c.total * c.iva_pct / 100)
            db.session.commit()
            flash(f"Cotización {c.numero} guardada por {filtro_cop(c.total_factura)}.", "ok")
            return redirect(url_for("cotizacion_detalle", id=c.id))
    pre = None
    if c:
        pre = dict(cliente_id=c.cliente_id, tipo=c.tipo, descripcion=c.descripcion or "", nota=c.nota or "",
                   validez_dias=c.validez_dias, descuento=c.descuento, iva_pct=c.iva_pct,
                   fecha=c.fecha.date().isoformat(),
                   lineas=[dict(tipo="mat" if l.insumo_id else "serv", ref=l.insumo_id or l.equipo_id or "",
                                desc=l.descripcion, cant=l.cantidad, precio=l.precio) for l in c.lineas])
    else:
        pre = dict(cliente_id=request.args.get("cliente", type=int), tipo=request.args.get("tipo", "correctivo"),
                   validez_dias=15, iva_pct=neg.iva_pct or 0, lineas=[], fecha=ahora().date().isoformat())
    return render_template("cotizacion_form.html", c=c, pre=pre, clientes=clientes_json(), insumos=insumos_json(),
                           precios=precios_sugeridos())


@app.route("/cotizaciones/<int:id>")
@rol_requerido("admin", "asesor")
def cotizacion_detalle(id):
    c = db.session.get(Cotizacion, id) or abort(404)
    wa = None
    if c.cliente and c.cliente.whatsapp:
        txt = (f"Hola {c.cliente.nombre}, le saluda {negocio_cache()['nombre']}. Le enviamos la cotización "
               f"{c.numero} por {filtro_cop(c.total_factura)}, válida hasta el {c.valida_hasta.strftime('%d/%m/%Y')}. "
               "Quedamos atentos a su aprobación.")
        wa = f"{c.cliente.whatsapp}?text={quote(txt)}"
    return render_template("cotizacion_detalle.html", c=c, wa=wa)


@app.route("/cotizaciones/<int:id>/estado", methods=["POST"])
@rol_requerido("admin", "asesor")
def cotizacion_estado(id):
    c = db.session.get(Cotizacion, id) or abort(404)
    est = request.form.get("estado")
    if est in ("pendiente", "aprobada", "rechazada") and not c.orden_id:
        c.estado = est
        db.session.commit()
        flash(f"Cotización {c.numero}: {est}.", "ok")
    return redirect(url_for("cotizacion_detalle", id=id))


@app.route("/cotizaciones/<int:id>/documento.<fmt>")
@rol_requerido("admin", "asesor")
def cotizacion_documento(id, fmt):
    c = db.session.get(Cotizacion, id) or abort(404)
    return _enviar_doc(comprobantes.datos_cotizacion(c, DatosNegocio.actual()), fmt, c.numero)


@app.route("/cotizaciones/<int:id>/eliminar", methods=["POST"])
@rol_requerido("admin")
def cotizacion_eliminar(id):
    c = db.session.get(Cotizacion, id) or abort(404)
    if c.orden_id:
        flash("Ya se convirtió en orden; no se puede eliminar.", "error")
        return redirect(url_for("cotizacion_detalle", id=id))
    db.session.delete(c)
    db.session.commit()
    flash("Cotización eliminada.", "ok")
    return redirect(url_for("cotizaciones"))


# ------------------------------------------------------ compra-venta de equipos
@app.route("/equipos-venta")
@rol_requerido("admin", "asesor")
def ventas_equipos():
    ini, fin, desde, hasta = periodo()
    items = (VentaEquipo.query.filter(VentaEquipo.fecha >= ini, VentaEquipo.fecha < fin)
             .order_by(VentaEquipo.fecha.desc()).all())
    validas = [v for v in items if not v.anulada]
    return render_template("ventas_equipos.html", items=items, desde=desde, hasta=hasta,
                           total=sum(v.precio_total for v in validas), costo=sum(v.costo_completo for v in validas),
                           ganancia=sum(v.ganancia for v in validas), unidades=sum(v.cantidad for v in validas),
                           saldo=sum(v.saldo for v in validas))


@app.route("/equipos-venta/nueva", methods=["GET", "POST"])
@app.route("/equipos-venta/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin", "asesor")
def venta_equipo_form(id=None):
    v = db.session.get(VentaEquipo, id) if id else None
    if id and (not v or v.anulada):
        abort(404)
    neg = DatosNegocio.actual()
    cfg = Configuracion.actual()
    if request.method == "POST":
        f = request.form
        cli = db.session.get(Cliente, int(n(f.get("cliente_id")))) if n(f.get("cliente_id")) else None
        nombre = f.get("cliente_nuevo", "").strip()
        if not cli and nombre:
            cli = Cliente.query.filter(func.lower(Cliente.nombre) == nombre.lower(), Cliente.activo.is_(True)).first()
            if not cli:
                cli = Cliente(nombre=nombre[:120], telefono=f.get("cliente_tel", "").strip()[:40] or None)
                db.session.add(cli)
                db.session.flush()
        nuevo = v is None
        if nuevo:
            v = VentaEquipo(usuario_id=current_user.id)
        v.cliente_id = cli.id if cli else None
        v.cliente_nombre = cli.nombre if cli else None
        v.fecha = fecha_form(f.get("fecha"))
        v.tipo = f.get("tipo") if f.get("tipo") in TIPOS_EQUIPO else "Mini split (pared)"
        v.marca = f.get("marca", "").strip()[:60] or None
        v.modelo = f.get("modelo", "").strip()[:80] or None
        v.capacidad_btu = int(n(f.get("capacidad_btu"))) or None
        v.inverter = bool(f.get("inverter"))
        v.cantidad = int(n(f.get("cantidad"), 1))
        v.proveedor = f.get("proveedor", "").strip()[:120] or None
        v.costo_total = n(f.get("costo_total"))
        v.otros_costos = n(f.get("otros_costos"))
        v.precio_total = n(f.get("precio_total"))
        v.iva_pct = n(f.get("iva_pct"))
        v.garantia_meses = max(int(n(f.get("garantia_meses"))), 0)
        v.condicion = "credito" if f.get("condicion") == "credito" else "contado"
        v.metodo_pago = f.get("metodo_pago") if f.get("metodo_pago") in METODOS_PAGO else "Efectivo"
        v.nota = f.get("nota", "").strip()[:255] or None
        v.vence = (v.fecha.date() + timedelta(days=max(int(n(f.get("plazo_dias"))), 0))) if v.condicion == "credito" else None
        try:
            validar_venta_equipo(v)
            calcular_venta_equipo(v)
            if nuevo:
                db.session.add(v)
            db.session.flush()
            msg = f"Venta {v.numero} registrada: ganancia {filtro_cop(v.ganancia)}."
            if nuevo and cli and f.get("registrar_equipo"):
                frec = int(n(f.get("frecuencia_meses"), cfg.frecuencia_def))
                eqs = []
                for i in range(min(v.cantidad, 30)):
                    eq = Equipo(cliente_id=cli.id, tipo=v.tipo, marca=v.marca, modelo=v.modelo,
                                capacidad_btu=v.capacidad_btu, inverter=v.inverter,
                                frecuencia_meses=frec if frec in FRECUENCIAS else 0,
                                ubicacion=(f.get("ubicacion", "").strip()[:60] or None) if v.cantidad == 1 else f"Equipo {i + 1}",
                                nota=f"Vendido en {v.numero}")
                    db.session.add(eq)
                    eqs.append(eq)
                db.session.flush()
                msg += f" {len(eqs)} equipo{'s' if len(eqs) != 1 else ''} quedó en la ficha del cliente."
                if f.get("programar_instalacion"):
                    o = Orden(tipo="instalacion", estado="programada", cliente_id=cli.id, cliente_nombre=cli.nombre,
                              fecha=fecha_hora(f.get("fecha_inst"), f.get("hora_inst"), ahora() + timedelta(days=1)),
                              direccion=cli.direccion_completa or None, usuario_id=current_user.id,
                              garantia_dias=cfg.garantia_dias,
                              tecnico_id=int(n(f.get("tecnico_id"))) or None,
                              descripcion=f"Instalación de {v.descripcion} (venta {v.numero}).")
                    db.session.add(o)
                    guardar_orden(o, [(eq, f"Instalación · {eq.corto}", 1, n(f.get("precio_instalacion"))) for eq in eqs],
                                  [], "programada")
                    v.orden_instalacion_id = o.id
                    msg += f" Instalación agendada: {num_orden(o)}."
                    for eq in eqs:
                        eq.fecha_instalacion = None
            db.session.commit()
            flash(msg if nuevo else f"Venta {v.numero} actualizada.", "ok")
            return redirect(url_for("venta_equipo_detalle", id=v.id))
        except ErrorNegocio as e:
            db.session.rollback()
            for m in e.errores:
                flash(m, "error")
            if not nuevo:
                v = db.session.get(VentaEquipo, id)
    return render_template("venta_equipo_form.html", v=v, clientes=clientes_json(), tipos=TIPOS_EQUIPO,
                           capacidades=CAPACIDADES, metodos=METODOS_PAGO, iva_def=neg.iva_pct or 0,
                           frecuencias=FRECUENCIAS, frec_def=cfg.frecuencia_def, tecnicos=tecnicos_json(),
                           precio_inst=precios_sugeridos().get("instalacion", 0),
                           pre_cliente=request.args.get("cliente", type=int))


@app.route("/equipos-venta/<int:id>")
@rol_requerido("admin", "asesor")
def venta_equipo_detalle(id):
    v = db.session.get(VentaEquipo, id) or abort(404)
    wa = None
    if v.cliente and v.cliente.whatsapp:
        txt = (f"Hola {v.cliente.nombre}, le saluda {negocio_cache()['nombre']}. Le compartimos el recibo {v.numero} "
               f"de su {v.descripcion} por {filtro_cop(v.total_factura)}. ¡Gracias por su compra!")
        wa = f"{v.cliente.whatsapp}?text={quote(txt)}"
    return render_template("venta_equipo_detalle.html", v=v, metodos=METODOS_PAGO, wa=wa)


@app.route("/equipos-venta/<int:id>/documento.<fmt>")
@rol_requerido("admin", "asesor")
def venta_equipo_documento(id, fmt):
    v = db.session.get(VentaEquipo, id) or abort(404)
    return _enviar_doc(comprobantes.datos_venta_equipo(v, DatosNegocio.actual()), fmt, v.numero)


@app.route("/equipos-venta/<int:id>/anular", methods=["POST"])
@rol_requerido("admin")
def venta_equipo_anular(id):
    v = db.session.get(VentaEquipo, id) or abort(404)
    try:
        anular_venta_equipo(v)
        db.session.commit()
        flash(f"Venta {v.numero} anulada.", "ok")
    except ErrorNegocio as e:
        flash(e.errores[0], "error")
    return redirect(url_for("venta_equipo_detalle", id=id))


# ------------------------------------------------------------------ cartera
@app.route("/cartera")
@rol_requerido("admin", "asesor")
def cartera():
    hoy_d = ahora().date()
    filtro = request.args.get("f", "")
    docs = cartera_pendiente()
    docs.sort(key=lambda x: (x.vence or date.max))
    total = sum(x.saldo for x in docs)
    vencidas = [x for x in docs if x.vencida]
    semana = [x for x in docs if not x.vencida and x.vence and x.vence <= hoy_d + timedelta(days=7)]
    rangos = {"Al día": 0.0, "1–30 días": 0.0, "31–60 días": 0.0, "61–90 días": 0.0, "Más de 90": 0.0}
    for x in docs:
        dv = (hoy_d - x.vence).days if x.vence else 0
        k = ("Al día" if dv <= 0 else "1–30 días" if dv <= 30 else "31–60 días" if dv <= 60
             else "61–90 días" if dv <= 90 else "Más de 90")
        rangos[k] += x.saldo
    if filtro == "vencidas":
        docs = vencidas
    elif filtro == "semana":
        docs = semana
    ini_mes = datetime.combine(hoy_d.replace(day=1), datetime.min.time())
    recaudo_mes = db.session.query(func.coalesce(func.sum(Abono.valor), 0)).filter(
        Abono.anulado.is_(False), Abono.fecha >= ini_mes).scalar() or 0
    filas = [dict(x=x, es_orden=isinstance(x, Orden),
                  numero=num_orden(x) if isinstance(x, Orden) else x.numero,
                  fecha=x.fecha_fin if isinstance(x, Orden) else x.fecha,
                  url=url_for("orden_detalle", id=x.id) if isinstance(x, Orden) else url_for("venta_equipo_detalle", id=x.id),
                  wa=_texto_cobro(x.cliente, [x]) if x.cliente else None) for x in docs]
    return render_template("cartera.html", filas=filas, total=total, filtro=filtro,
                           total_vencido=sum(x.saldo for x in vencidas), n_vencidas=len(vencidas),
                           total_semana=sum(x.saldo for x in semana), n_semana=len(semana),
                           rangos=rangos, recaudo_mes=recaudo_mes, metodos=METODOS_PAGO)


@app.route("/abono/<tipo>/<int:id>", methods=["POST"])
@rol_requerido("admin", "asesor")
def abono_nuevo(tipo, id):
    doc = db.session.get(Orden if tipo == "orden" else VentaEquipo, id) or abort(404)
    try:
        a = registrar_abono(doc, n(request.form.get("valor")), request.form.get("metodo", "Efectivo"),
                            request.form.get("nota", ""), fecha_form(request.form.get("fecha")), current_user.id)
        db.session.commit()
        flash(f"Abono de {filtro_cop(a.valor)} registrado. "
              + ("Quedó pagada. ✔" if doc.saldo <= 0.5 else f"Saldo: {filtro_cop(doc.saldo)}."), "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        for m in e.errores:
            flash(m, "error")
    destino = url_for("orden_detalle", id=id) if tipo == "orden" else url_for("venta_equipo_detalle", id=id)
    volver = request.form.get("volver") or ""
    return redirect(volver if volver.startswith("/") and not volver.startswith("//") else destino)


@app.route("/abonos/<int:id>/anular", methods=["POST"])
@rol_requerido("admin")
def abono_anular(id):
    a = db.session.get(Abono, id) or abort(404)
    if a.anulado:
        flash("Ese abono ya estaba anulado.", "error")
    else:
        a.anulado = True
        db.session.commit()
        flash(f"Abono de {filtro_cop(a.valor)} anulado. El saldo volvió a subir.", "ok")
    if a.orden_id:
        return redirect(url_for("orden_detalle", id=a.orden_id))
    return redirect(url_for("venta_equipo_detalle", id=a.venta_equipo_id))


# ------------------------------------------------------------------- gastos
@app.route("/gastos")
@rol_requerido("admin")
def gastos():
    ini, fin, desde, hasta = periodo()
    cat = request.args.get("categoria", "")
    q = Gasto.query.filter(Gasto.fecha >= ini.date(), Gasto.fecha < fin.date())
    if cat:
        q = q.filter(Gasto.categoria == cat)
    items = q.order_by(Gasto.fecha.desc(), Gasto.id.desc()).all()
    por_cat = defaultdict(float)
    for x in items:
        por_cat[x.categoria] += x.valor
    return render_template("gastos.html", items=items, desde=desde, hasta=hasta, cat=cat,
                           categorias=CATEGORIAS_GASTO, por_cat=sorted(por_cat.items(), key=lambda x: -x[1]),
                           total=sum(x.valor for x in items))


@app.route("/gastos/nuevo", methods=["GET", "POST"])
@app.route("/gastos/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin")
def gasto_form(id=None):
    gs = db.session.get(Gasto, id) if id else None
    if id and not gs:
        abort(404)
    if request.method == "POST":
        f = request.form
        valor = n(f.get("valor"))
        fecha = _fecha(f.get("fecha"))
        cat = f.get("categoria", "Otros")
        desc = f.get("descripcion", "").strip()
        if valor <= 0:
            flash("El valor debe ser mayor a 0.", "error")
        elif not fecha:
            flash("Indica la fecha del gasto.", "error")
        elif cat not in CATEGORIAS_GASTO:
            flash("Categoría no válida.", "error")
        elif cat == "Otros" and not desc:
            flash("Describe en qué consiste el gasto «Otros».", "error")
        else:
            if not gs:
                gs = Gasto(usuario_id=current_user.id)
                db.session.add(gs)
            gs.fecha, gs.categoria, gs.valor, gs.descripcion = fecha, cat, valor, desc[:255] or None
            db.session.commit()
            flash(f"Gasto de {filtro_cop(valor)} en {cat} guardado.", "ok")
            return redirect(url_for("gastos"))
    return render_template("gasto_form.html", g=gs, categorias=CATEGORIAS_GASTO,
                           pre_cat=request.args.get("categoria", ""))


@app.route("/gastos/<int:id>/eliminar", methods=["POST"])
@rol_requerido("admin")
def gasto_eliminar(id):
    gs = db.session.get(Gasto, id) or abort(404)
    db.session.delete(gs)
    db.session.commit()
    flash("Gasto eliminado.", "ok")
    return redirect(url_for("gastos"))


# ---------------------------------------------------------------- materiales
@app.route("/insumos")
@rol_requerido("admin", "asesor")
def insumos():
    q = request.args.get("q", "").strip()
    cat = request.args.get("cat", "")
    query = Insumo.query.filter_by(activo=True)
    if q:
        query = query.filter(Insumo.nombre.ilike(f"%{q}%"))
    if cat:
        query = query.filter(Insumo.categoria == cat)
    items = query.order_by(Insumo.categoria, Insumo.nombre).all()
    ini_mes = datetime.combine(ahora().date().replace(day=1), datetime.min.time())
    uso_mes = dict(db.session.query(OrdenMaterial.insumo_id, func.sum(OrdenMaterial.cantidad))
                   .join(Orden, OrdenMaterial.orden_id == Orden.id)
                   .filter(Orden.anulada.is_(False), Orden.aplicada.is_(True), Orden.fecha_fin >= ini_mes)
                   .group_by(OrdenMaterial.insumo_id).all())
    return render_template("insumos.html", items=items, q=q, cat=cat, categorias=CATEGORIAS_INSUMO,
                           total=sum(i.valor_stock for i in items), uso_mes=uso_mes)


@app.route("/insumos/nuevo", methods=["GET", "POST"])
@app.route("/insumos/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin", "asesor")
def insumo_form(id=None):
    ins = db.session.get(Insumo, id) if id else None
    if id and not ins:
        abort(404)
    if request.method == "POST":
        f = request.form
        nombre = f.get("nombre", "").strip()
        unidad = f.get("unidad") if f.get("unidad") in UNIDADES else "unidad"
        categoria = f.get("categoria") if f.get("categoria") in CATEGORIAS_INSUMO else "Otros"
        dup = Insumo.query.filter(func.lower(Insumo.nombre) == nombre.lower(), Insumo.activo.is_(True),
                                  Insumo.id != (id or 0)).first()
        if not nombre:
            flash("Escribe el nombre del material.", "error")
        elif dup:
            flash("Ya existe un material activo con ese nombre.", "error")
        else:
            try:
                nuevo = ins is None
                if nuevo:
                    ins = Insumo(nombre=nombre, unidad=unidad)
                    db.session.add(ins)
                ins.nombre, ins.unidad, ins.categoria = nombre[:120], unidad, categoria
                ins.stock_minimo = n(f.get("stock_minimo"))
                ins.precio_venta = n(f.get("precio_venta"))
                db.session.flush()
                msg = f"Material «{nombre}» guardado."
                if nuevo:
                    cant = n(f.get("cantidad_inicial"))
                    if cant > 0:
                        registrar_compra(ins, cant, n(f.get("costo_inicial")), nota="Inventario inicial",
                                         fecha=fecha_form(f.get("fecha_inicial")), usuario_id=current_user.id)
                elif f.get("conteo", "").strip() != "" and current_user.rol_efectivo == "admin":
                    contado = n(f.get("conteo"))
                    if abs(contado - (ins.stock or 0)) > 1e-9:
                        perdida = ajustar_stock(ins, contado)
                        if perdida > 0.5:
                            db.session.add(Gasto(fecha=ahora().date(), categoria="Pérdidas de inventario",
                                                 valor=round(perdida), usuario_id=current_user.id,
                                                 descripcion=f"Ajuste por conteo de {ins.nombre}"[:255]))
                        msg += f" Inventario ajustado a {filtro_num(contado)} {ins.unidad}."
                db.session.commit()
                flash(msg, "ok")
                return redirect(url_for("insumos"))
            except ErrorNegocio as e:
                db.session.rollback()
                for m in e.errores:
                    flash(m, "error")
    return render_template("insumo_form.html", ins=ins, unidades=UNIDADES, categorias=CATEGORIAS_INSUMO)


@app.route("/insumos/<int:id>/eliminar", methods=["POST"])
@rol_requerido("admin")
def insumo_eliminar(id):
    ins = db.session.get(Insumo, id) or abort(404)
    ins.activo = False
    db.session.commit()
    flash(f"Material «{ins.nombre}» eliminado (su historial se conserva).", "ok")
    return redirect(url_for("insumos"))


@app.route("/compras")
@rol_requerido("admin", "asesor")
def compras():
    ini, fin, desde, hasta = periodo()
    tipo = request.args.get("tipo", "")
    filas = []
    if tipo in ("", "insumos"):
        for c in (CompraInsumo.query.filter(CompraInsumo.fecha >= ini, CompraInsumo.fecha < fin)
                  .order_by(CompraInsumo.fecha.desc()).all()):
            filas.append(dict(tipo="insumo", fecha=c.fecha, orden=c.fecha, nombre=c.insumo.nombre,
                              cantidad=f"{filtro_num(c.cantidad, 3)} {c.insumo.unidad}", unitario=c.costo_unitario,
                              total=c.costo_total, proveedor=c.proveedor, nota=c.nota, anulada=c.anulada, obj=c))
    if tipo in ("", "equipos") and current_user.rol_efectivo == "admin":
        for a in (Activo.query.filter(Activo.eliminado.is_(False), Activo.fecha_compra >= ini.date(),
                                      Activo.fecha_compra < fin.date()).all()):
            filas.append(dict(tipo="equipo", fecha=a.fecha_compra,
                              orden=datetime.combine(a.fecha_compra, datetime.min.time()),
                              nombre=a.nombre, cantidad="1 herramienta", unitario=a.valor, total=a.valor,
                              proveedor=a.proveedor, nota=a.descripcion, anulada=False, obj=a))
    filas.sort(key=lambda x: x["orden"], reverse=True)
    t_ins = sum(x["total"] for x in filas if x["tipo"] == "insumo" and not x["anulada"])
    t_eq = sum(x["total"] for x in filas if x["tipo"] == "equipo")
    return render_template("compras.html", filas=filas, desde=desde, hasta=hasta, tipo=tipo,
                           total=t_ins + t_eq, total_insumos=t_ins, total_equipos=t_eq)


@app.route("/compras/nueva", methods=["GET", "POST"])
@rol_requerido("admin", "asesor")
def compra_nueva():
    if request.method == "POST":
        fecha = fecha_form(request.form.get("fecha"))
        proveedor = request.form.get("proveedor", "").strip()
        nota = request.form.get("nota", "").strip()
        registradas = 0
        try:
            for iid, c, ct in zip(request.form.getlist("insumo_id[]"), request.form.getlist("cantidad[]"),
                                  request.form.getlist("costo[]")):
                ins = db.session.get(Insumo, int(n(iid))) if n(iid) else None
                if not ins:
                    continue
                registrar_compra(ins, n(c), n(ct), proveedor, nota, fecha, current_user.id)
                registradas += 1
            if not registradas:
                raise ErrorNegocio("Agrega al menos un material a la compra.")
            db.session.commit()
            flash(f"Compra registrada ({registradas} material(es)). El inventario y el costo promedio se actualizaron.", "ok")
            return redirect(url_for("compras"))
        except ErrorNegocio as e:
            db.session.rollback()
            for m in e.errores:
                flash(m, "error")
    return render_template("compra_form.html", insumos=insumos_json())


@app.route("/compras/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin", "asesor")
def compra_editar(id):
    c = db.session.get(CompraInsumo, id) or abort(404)
    if c.anulada:
        flash("Esta compra está anulada; no se puede editar.", "error")
        return redirect(url_for("compras"))
    if request.method == "POST":
        nueva = _fecha(request.form.get("fecha"))
        if not nueva:
            flash("Indica una fecha válida.", "error")
        elif nueva > ahora().date():
            flash("La fecha no puede ser futura.", "error")
        else:
            if nueva != c.fecha.date():
                c.fecha = fecha_form(nueva.isoformat())
            c.proveedor = request.form.get("proveedor", "").strip()[:120] or None
            c.nota = request.form.get("nota", "").strip()[:255] or None
            db.session.commit()
            flash(f"Compra de «{c.insumo.nombre}» actualizada.", "ok")
            return redirect(url_for("compras", desde=min(nueva, ahora().date().replace(day=1)).isoformat()))
    return render_template("compra_editar.html", c=c)


@app.route("/compras/<int:id>/anular", methods=["POST"])
@rol_requerido("admin")
def compra_anular(id):
    c = db.session.get(CompraInsumo, id) or abort(404)
    try:
        anular_compra(c)
        db.session.commit()
        flash(f"Compra de {c.cantidad:g} {c.insumo.unidad} de «{c.insumo.nombre}» anulada.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        flash(e.errores[0], "error")
    return redirect(request.referrer or url_for("compras"))


# ------------------------------------------------------------------ activos
@app.route("/activos")
@rol_requerido("admin")
def activos():
    hoy_d = ahora().date()
    items = Activo.query.filter_by(eliminado=False).order_by(Activo.fecha_baja.isnot(None),
                                                               Activo.fecha_compra.desc()).all()
    filas = []
    for a in items:
        acum = a.acumulada(hoy_d)
        meses_rest = 0
        if a.estado(hoy_d) == "vigente":
            meses_rest = max((a.fin_vida - hoy_d).days, 0) / (365 / 12)
        filas.append(dict(a=a, acum=acum, pendiente=a.depreciable - acum,
                          pct=(acum / a.depreciable * 100) if a.depreciable else 100,
                          estado=a.estado(hoy_d), meses_rest=meses_rest))
    vig = [x for x in filas if x["estado"] == "vigente"]
    return render_template(
        "activos.html", filas=filas, hoy=hoy_d.isoformat(),
        total_valor=sum(x["a"].valor for x in filas if x["estado"] != "baja"),
        total_pendiente=sum(x["pendiente"] for x in vig), cuota_mes=sum(x["a"].cuota_mensual for x in vig),
        dep_mes=depreciacion_periodo(hoy_d.replace(day=1), hoy_d, [x["a"] for x in filas]))


@app.route("/activos/nuevo", methods=["GET", "POST"])
@app.route("/activos/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin")
def activo_form(id=None):
    a = db.session.get(Activo, id) if id else None
    if id and (not a or a.eliminado):
        abort(404)
    if request.method == "POST":
        f = request.form
        nombre = f.get("nombre", "").strip()
        valor, residual = n(f.get("valor")), n(f.get("valor_residual"))
        vida = int(n(f.get("vida_util_meses")))
        fecha = _fecha(f.get("fecha_compra"))
        errores = []
        if not nombre:
            errores.append("Escribe el nombre de la herramienta.")
        if not fecha:
            errores.append("Indica la fecha de compra.")
        elif fecha > ahora().date():
            errores.append("La fecha de compra no puede ser futura.")
        if valor <= 0:
            errores.append("El valor de compra debe ser mayor a 0.")
        if residual < 0 or residual >= valor > 0:
            errores.append("El valor residual debe ser menor que el valor de compra.")
        if vida < 1:
            errores.append("La vida útil debe ser de al menos 1 mes.")
        if errores:
            for m in errores:
                flash(m, "error")
        else:
            if not a:
                a = Activo()
                db.session.add(a)
            a.nombre, a.fecha_compra, a.valor = nombre[:120], fecha, valor
            a.valor_residual, a.vida_util_meses = residual, vida
            a.descripcion = f.get("descripcion", "").strip()[:255] or None
            a.proveedor = f.get("proveedor", "").strip()[:120] or None
            db.session.commit()
            flash(f"«{nombre}» guardado. Ya aparece en Compras y en Herramientas.", "ok")
            return redirect(url_for("compras") if f.get("origen") == "compras" else url_for("activos"))
    return render_template("activo_form.html", a=a, origen=request.values.get("origen", ""))


@app.route("/activos/<int:id>/baja", methods=["POST"])
@rol_requerido("admin")
def activo_baja(id):
    a = db.session.get(Activo, id) or abort(404)
    if request.form.get("deshacer"):
        a.fecha_baja, a.motivo_baja = None, None
        flash(f"«{a.nombre}» vuelve a estar en uso.", "ok")
    else:
        a.fecha_baja = _fecha(request.form.get("fecha_baja"), ahora().date())
        a.motivo_baja = request.form.get("motivo", "").strip()[:255] or None
        flash(f"«{a.nombre}» dado de baja. Deja de depreciarse desde esa fecha.", "ok")
    db.session.commit()
    return redirect(url_for("activos"))


@app.route("/activos/<int:id>/eliminar", methods=["POST"])
@rol_requerido("admin")
def activo_eliminar(id):
    a = db.session.get(Activo, id) or abort(404)
    a.eliminado = True
    db.session.commit()
    flash(f"«{a.nombre}» eliminado (registrado por error).", "ok")
    return redirect(url_for("activos"))


# ----------------------------------------------------------------- reportes
def _params_reporte():
    agrupar = request.args.get("agrupar", "semanal")
    if agrupar not in rpt.AGRUPACIONES:
        agrupar = "semanal"
    hoy_d = ahora().date()
    d0, h0 = rpt.rango_por_defecto(agrupar, hoy_d)
    desde = _fecha(request.args.get("desde"), d0)
    hasta = _fecha(request.args.get("hasta"), h0)
    if hasta < desde:
        desde, hasta = hasta, desde
    if (hasta - desde).days > 3 * 366:
        desde = hasta - timedelta(days=3 * 366)
    return agrupar, desde, hasta


@app.route("/reportes")
@rol_requerido("admin")
def reportes():
    agrupar, desde, hasta = _params_reporte()
    rep = rpt.generar(desde, hasta, agrupar)
    serie = dict(labels=[x["periodo"] for x in rep["filas"]],
                 ingresos=[round(x["servicios"] + x["ventas_eq"]) for x in rep["filas"]],
                 costos=[round(x["costo_serv"] + x["costo_eq"]) for x in rep["filas"]],
                 neta=[round(x["neta"]) for x in rep["filas"]])
    return render_template("reportes.html", rep=rep, serie=serie, agrupar=agrupar,
                           agrupaciones=rpt.AGRUPACIONES, columnas=rpt.COLUMNAS,
                           desde=desde.isoformat(), hasta=hasta.isoformat())


@app.route("/reportes/excel")
@rol_requerido("admin")
def reportes_excel():
    agrupar, desde, hasta = _params_reporte()
    rep = rpt.generar(desde, hasta, agrupar)
    neg = DatosNegocio.actual()
    data = rpt.excel(rep, desde, hasta, agrupar, neg.nombre, num_orden)
    nombre = f"reporte_{agrupar}_{desde.isoformat()}_a_{hasta.isoformat()}.xlsx"
    return Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


# ------------------------------------------------------------ distribución
@app.route("/distribucion")
@rol_requerido("admin")
def distribucion():
    ini, fin, desde, hasta = periodo()
    cfg = Configuracion.actual()
    ords, ves = ingresos_periodo(ini, fin)
    por_dia = defaultdict(lambda: {"ventas": 0.0, "costo": 0.0, "num": 0})
    for o in ords:
        x = por_dia[o.fecha_fin.date()]
        x["ventas"] += o.total
        x["costo"] += o.costo_materiales + o.pago_tecnico + o.otros_costos
        x["num"] += 1
    for v in ves:
        x = por_dia[v.fecha.date()]
        x["ventas"] += v.precio_total
        x["costo"] += v.costo_completo
        x["num"] += 1
    pct = dict(reinversion=cfg.pct_reinversion, dueno=cfg.pct_dueno, emergencia=cfg.pct_emergencia)
    act = activos_vigentes()
    gastos_dia = gastos_por_dia(ini.date(), (fin - timedelta(days=1)).date())

    def repartir(ventas_, costo_, equipos_, gastos_=0.0):
        ganancia = ventas_ - costo_
        fijos = equipos_ + gastos_
        base = ventas_ if cfg.base == "ventas" else max(ganancia - fijos, 0)
        r = {k: base * p / 100 for k, p in pct.items()}
        r.update(ventas=ventas_, costo=costo_, ganancia=ganancia, base=base, equipos=equipos_, gastos=gastos_,
                 neta=ganancia - fijos)
        r["reponer"] = costo_ if cfg.base == "ganancia" else 0
        r["faltante"] = (max(costo_ + fijos - r["reinversion"], 0) if cfg.base == "ventas"
                         else max(fijos - max(ganancia, 0), 0))
        return r

    hoy_d = ahora().date()
    filas = []
    dia = min((fin - timedelta(days=1)).date(), hoy_d)
    while dia >= ini.date():
        x = por_dia.get(dia, {"ventas": 0.0, "costo": 0.0, "num": 0})
        eq = depreciacion_dia(dia, act)
        gd = gastos_dia.get(dia, 0)
        if x["num"] or eq > 0.005 or gd:
            r = repartir(x["ventas"], x["costo"], eq, gd)
            r.update(fecha=dia, num=x["num"])
            filas.append(r)
        dia -= timedelta(days=1)
    tot = repartir(sum(x["ventas"] for x in filas), sum(x["costo"] for x in filas),
                   sum(x["equipos"] for x in filas), sum(x["gastos"] for x in filas))
    return render_template("distribucion.html", cfg=cfg, filas=filas, tot=tot, desde=desde, hasta=hasta)


@app.route("/configuracion", methods=["GET", "POST"])
@rol_requerido("admin")
def configuracion():
    cfg = Configuracion.actual()
    if request.method == "POST":
        f = request.form
        sec = f.get("seccion")
        if sec == "mantenimiento":
            dias, frec, gar = int(n(f.get("dias_aviso"))), int(n(f.get("frecuencia_def"))), int(n(f.get("garantia_dias")))
            msg = f.get("msg_recordatorio", "").strip()
            if not 1 <= dias <= 90 or frec not in FRECUENCIAS or not 0 <= gar <= 365:
                flash("Revisa los valores: aviso entre 1 y 90 días y garantía entre 0 y 365.", "error")
            elif msg and "{cliente}" not in msg:
                flash("El mensaje debe incluir {cliente} para saludar al cliente por su nombre.", "error")
            else:
                cfg.dias_aviso, cfg.frecuencia_def, cfg.garantia_dias = dias, frec, gar
                cfg.msg_recordatorio = msg[:500] or None
                db.session.commit()
                flash("Configuración de mantenimientos guardada.", "ok")
                return redirect(url_for("configuracion"))
        else:
            r, d, e = (n(f.get(k)) for k in ("pct_reinversion", "pct_dueno", "pct_emergencia"))
            base = f.get("base", "ventas")
            if min(r, d, e) < 0:
                flash("Los porcentajes no pueden ser negativos.", "error")
            elif abs(r + d + e - 100) > 0.01:
                flash(f"Los porcentajes deben sumar 100%. Ahora suman {r + d + e:g}%.", "error")
            elif base not in ("ventas", "ganancia"):
                flash("Base de cálculo no válida.", "error")
            else:
                cfg.pct_reinversion, cfg.pct_dueno, cfg.pct_emergencia, cfg.base = r, d, e, base
                db.session.commit()
                flash("Regla de distribución guardada.", "ok")
                return redirect(url_for("distribucion"))
    return render_template("configuracion.html", cfg=cfg, frecuencias=FRECUENCIAS, msg_def=MSG_RECORDATORIO_DEF)


# ---------------------------------------------------------------- negocio
@app.route("/negocio", methods=["GET", "POST"])
@rol_requerido("admin")
def negocio():
    neg = DatosNegocio.actual()
    if request.method == "POST":
        f = request.form
        nombre = f.get("nombre", "").strip()
        iva = n(f.get("iva_pct"))
        archivo = request.files.get("logo")
        if not nombre:
            flash("El nombre del negocio es obligatorio.", "error")
        elif not 0 <= iva <= 100:
            flash("El IVA debe estar entre 0 y 100.", "error")
        else:
            neg.nombre = nombre[:120]
            for campo, largo in (("nit", 40), ("direccion", 160), ("ciudad", 80), ("telefono", 40),
                                 ("email", 120), ("regimen", 120), ("titulo_doc", 60), ("mensaje", 200),
                                 ("condiciones_cot", 500)):
                setattr(neg, campo, (f.get(campo, "").strip()[:largo]) or None)
            neg.prefijo = "".join(ch for ch in f.get("prefijo", "OS").upper() if ch.isalnum())[:8] or "OS"
            neg.iva_pct = iva
            if f.get("quitar_logo"):
                neg.logo, neg.logo_v = None, (neg.logo_v or 0) + 1
            elif archivo and archivo.filename:
                try:
                    neg.logo = _procesar_logo(archivo.read())
                    neg.logo_v = (neg.logo_v or 0) + 1
                except Exception:
                    flash("No se pudo leer el logo. Sube una imagen JPG o PNG.", "error")
                    db.session.rollback()
                    return redirect(url_for("negocio"))
            db.session.commit()
            negocio_cache(refrescar=True)
            flash("Datos del negocio guardados. Así saldrán en órdenes, cotizaciones y recibos.", "ok")
            return redirect(url_for("negocio"))
    ultima = Orden.query.filter(Orden.estado == "terminada").order_by(Orden.id.desc()).first()
    return render_template("negocio.html", neg=neg, ultima=ultima)


def _procesar_logo(data):
    """Convierte la imagen subida en un JPG cuadrado de hasta 600 px con fondo blanco."""
    from PIL import Image
    import io
    Image.MAX_IMAGE_PIXELS = 40_000_000
    im = Image.open(io.BytesIO(data))
    if im.format not in ("JPEG", "PNG", "WEBP"):
        raise ValueError("formato no permitido")
    im.load()
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        fondo = Image.new("RGB", im.size, "white")
        fondo.paste(im, mask=im.split()[-1])
        im = fondo
    else:
        im = im.convert("RGB")
    im.thumbnail((600, 600))
    lado = max(im.size)
    lienzo = Image.new("RGB", (lado, lado), "white")
    lienzo.paste(im, ((lado - im.width) // 2, (lado - im.height) // 2))
    buf = io.BytesIO()
    lienzo.save(buf, "JPEG", quality=90)
    return buf.getvalue()


@app.route("/salud")
def salud():
    """Respuesta liviana para despertar la app (la usa la página de entrada de softgestapps.com)."""
    r = jsonify(ok=True)
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Cache-Control"] = "no-store"
    return r


@app.route("/manifest.webmanifest")
def manifest():
    """Permite instalar la app en el celular (ícono en la pantalla de inicio)."""
    nombre = negocio_cache()["nombre"]
    return jsonify(name=nombre, short_name=nombre[:12], start_url="/", display="standalone",
                   background_color="#f3f6fb", theme_color="#0A1A3F",
                   icons=[dict(src=url_for("static", filename="brand/icon-192.png"), sizes="192x192", type="image/png"),
                          dict(src=url_for("static", filename="brand/icon-512.png"), sizes="512x512", type="image/png")])


@app.route("/logo.jpg")
def logo():
    if empresa_id_actual():
        neg = DatosNegocio.query.first()
        if neg and neg.logo:
            r = Response(neg.logo, mimetype="image/jpeg")
            r.headers["Cache-Control"] = "private, max-age=2592000"
            return r
    return send_file(os.path.join(app.root_path, "static", "logo_sm.jpg"), max_age=2592000)


@app.route("/bloqueado")
@login_required
def bloqueado():
    emp = getattr(g, "empresa", None)
    if not emp or emp.estado != "suspendida":
        return redirect(url_for("inicio"))
    return render_template("bloqueado.html", emp=emp)


# ---------------------------------------------------------------- técnicos
@app.route("/empleados")
@rol_requerido("admin")
def empleados():
    ver = request.args.get("ver", "")
    q = Empleado.query
    if ver != "todos":
        q = q.filter_by(activo=True)
    items = q.order_by(Empleado.activo.desc(), Empleado.nombre).all()
    ini_mes = datetime.combine(ahora().date().replace(day=1), datetime.min.time())
    com_mes = dict(db.session.query(Orden.tecnico_id, func.sum(Orden.pago_tecnico))
                   .filter(Orden.estado == "terminada", Orden.anulada.is_(False), Orden.fecha_fin >= ini_mes)
                   .group_by(Orden.tecnico_id).all())
    mes = dict(db.session.query(Orden.tecnico_id, func.count(Orden.id))
               .filter(Orden.estado == "terminada", Orden.anulada.is_(False), Orden.fecha_fin >= ini_mes)
               .group_by(Orden.tecnico_id).all())
    return render_template("empleados.html", items=items, com_mes=com_mes, mes=mes, ver=ver,
                           total_com=sum(v or 0 for v in com_mes.values()))


@app.route("/empleados/nuevo", methods=["GET", "POST"])
@app.route("/empleados/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin")
def empleado_form(id=None):
    e = db.session.get(Empleado, id) if id else None
    if id and not e:
        abort(404)
    if request.method == "POST":
        f = request.form
        nombre = f.get("nombre", "").strip()
        tipo = f.get("tipo_pago") if f.get("tipo_pago") in TIPOS_PAGO else "comision"
        salario = n(f.get("salario"))
        username = f.get("username", "").strip().lower()
        clave = f.get("password", "")
        errores = []
        if not nombre:
            errores.append("Escribe el nombre del técnico.")
        if tipo in ("fijo", "mixto") and salario <= 0:
            errores.append("Indica el salario mensual.")
        if f.get("crear_usuario") and not (e and e.usuario):
            if not username or " " in username:
                errores.append("Escribe el usuario de acceso (sin espacios).")
            elif Usuario.query.filter(func.lower(Usuario.username) == username).first():
                errores.append("Ese usuario ya existe. Prueba agregando el nombre del negocio.")
            if len(clave) < 6:
                errores.append("La contraseña de acceso debe tener al menos 6 caracteres.")
        if errores:
            for m in errores:
                flash(m, "error")
        else:
            if not e:
                e = Empleado()
                db.session.add(e)
            e.nombre = nombre[:120]
            for campo, largo in (("documento", 40), ("telefono", 40), ("cargo", 80), ("nota", 255)):
                setattr(e, campo, f.get(campo, "").strip()[:largo] or None)
            e.tipo_pago, e.salario = tipo, salario if tipo != "comision" else 0
            e.comision_tipo = f.get("comision_tipo") if f.get("comision_tipo") in COMISION_TIPOS else "pct"
            e.comision_valor = n(f.get("comision_valor")) if tipo != "fijo" else 0
            e.fecha_ingreso = _fecha(f.get("fecha_ingreso"))
            e.activo = bool(f.get("activo")) if id else True
            if f.get("crear_usuario") and not e.usuario:
                u = Usuario(nombre=e.nombre, username=username, rol="tecnico", empresa_id=empresa_id_actual())
                u.set_password(clave)
                db.session.add(u)
                db.session.flush()
                e.usuario_id = u.id
            if e.usuario and not e.activo:
                e.usuario.activo = False
            db.session.commit()
            flash(f"Técnico «{e.nombre}» guardado.", "ok")
            return redirect(url_for("empleado_detalle", id=e.id))
    return render_template("empleado_form.html", e=e, tipos=TIPOS_PAGO, comision_tipos=COMISION_TIPOS)


@app.route("/empleados/<int:id>")
@rol_requerido("admin")
def empleado_detalle(id):
    e = db.session.get(Empleado, id) or abort(404)
    ords = (Orden.query.filter(Orden.tecnico_id == id, Orden.anulada.is_(False))
            .order_by(Orden.fecha.desc()).limit(40).all())
    ini_mes = datetime.combine(ahora().date().replace(day=1), datetime.min.time())
    mes = [o for o in ords if o.terminada and o.fecha_fin >= ini_mes]
    return render_template("empleado_detalle.html", e=e, ordenes=ords, n_mes=len(mes),
                           com_mes=sum(o.pago_tecnico for o in mes), cobrado_mes=sum(o.total for o in mes),
                           garantias_mes=sum(1 for o in mes if o.en_garantia))


# ---------------------------------------------------------------- usuarios
@app.route("/usuarios")
@rol_requerido("admin")
def usuarios():
    return render_template("usuarios.html",
                           items=Usuario.query.filter_by(empresa_id=empresa_id_actual())
                           .order_by(Usuario.activo.desc(), Usuario.nombre).all())


@app.route("/usuarios/nuevo", methods=["GET", "POST"])
@app.route("/usuarios/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin")
def usuario_form(id=None):
    u = db.session.get(Usuario, id) if id else None
    if id and (not u or u.empresa_id != empresa_id_actual()):
        abort(404)
    if request.method == "POST":
        f = request.form
        nombre, username = f.get("nombre", "").strip(), f.get("username", "").strip().lower()
        rol, clave = f.get("rol", "asesor"), f.get("password", "")
        activo = bool(f.get("activo")) if u else True
        dup = Usuario.query.filter(func.lower(Usuario.username) == username, Usuario.id != (id or 0)).first()
        if not nombre or not username:
            flash("Nombre y usuario son obligatorios.", "error")
        elif dup:
            flash("Ese nombre de usuario ya está en uso (puede ser de otra empresa). Prueba con otro, "
                  "por ejemplo agregando el nombre de tu negocio.", "error")
        elif rol not in ROLES:
            flash("Rol no válido.", "error")
        elif not u and len(clave) < 6:
            flash("La contraseña debe tener al menos 6 caracteres.", "error")
        elif u and u.id == current_user.id and (rol != "admin" or not activo):
            flash("No puedes quitarte el rol de administrador ni desactivarte a ti mismo.", "error")
        else:
            if not u:
                u = Usuario(empresa_id=empresa_id_actual())
                db.session.add(u)
            u.nombre, u.username, u.rol, u.activo = nombre, username, rol, activo
            if clave:
                if len(clave) < 6:
                    flash("La contraseña debe tener al menos 6 caracteres.", "error")
                    return render_template("usuario_form.html", u=u)
                u.set_password(clave)
            db.session.commit()
            if rol == "tecnico" and not Empleado.query.filter_by(usuario_id=u.id).first():
                flash(f"Usuario «{username}» guardado. Vincúlalo a un técnico en Técnicos → Editar para que vea "
                      "sus órdenes.", "info")
            else:
                flash(f"Usuario «{username}» guardado.", "ok")
            return redirect(url_for("usuarios"))
    return render_template("usuario_form.html", u=u)


def crear_empresa(nombre, admin_user, admin_password, admin_nombre="Administrador"):
    """Crea la empresa con sus datos de negocio, configuración y su usuario administrador."""
    emp = Empresa(nombre=nombre[:120], slug="empresa", estado="activa", vence=None)
    db.session.add(emp)
    db.session.flush()
    db.session.add(DatosNegocio(empresa_id=emp.id, nombre=emp.nombre))
    db.session.add(Configuracion(empresa_id=emp.id))
    adm = Usuario(nombre=admin_nombre, username=admin_user.lower(), rol="admin", empresa_id=emp.id)
    adm.set_password(admin_password)
    db.session.add(adm)
    db.session.commit()
    return emp, adm


@app.errorhandler(404)
def no_encontrado(e):
    return render_template("error.html", titulo="No encontrado",
                           mensaje="Lo que buscas no existe o fue eliminado."), 404


# ------------------------------------------------------------- arranque BD
def migrar_columnas():
    """Agrega columnas nuevas a tablas que ya existían (para futuras versiones)."""
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    tablas = set(insp.get_table_names())
    nuevas = []  # (tabla, columna, tipo SQL)
    mapeadas = [m.class_ for m in db.Model.registry.mappers if issubclass(m.class_, EmpresaMixin)]
    nuevas += [(m.__tablename__, "empresa_id", "INTEGER") for m in mapeadas]
    for tabla, col, tipo in nuevas:
        if tabla in tablas and col not in {c["name"] for c in insp.get_columns(tabla)}:
            with db.engine.begin() as cn:
                cn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}"))


def inicializar_bd():
    with app.app_context():
        try:
            db.create_all()
            migrar_columnas()
        except Exception as e:
            print("=" * 70, flush=True)
            print("ERROR: no se pudo conectar a la base de datos (DATABASE_URL).", flush=True)
            print("Revisa: usar 'Session pooler' (puerto 5432), contraseña correcta sin", flush=True)
            print("corchetes, y sin espacios. Detalle técnico:", flush=True)
            print(str(e).split("\n")[0][:300], flush=True)
            print("=" * 70, flush=True)
            raise
        if not Empresa.query.first():
            # Primera vez: se crea la empresa y su administrador con las variables de entorno
            user = os.getenv("ADMIN_USER", "admin").strip().lower() or "admin"
            clave = os.getenv("ADMIN_PASSWORD")
            if not clave:
                clave = secrets.token_urlsafe(9)
                print(f"AVISO: no se definió ADMIN_PASSWORD. Clave temporal de «{user}»: {clave}", flush=True)
            crear_empresa(os.getenv("NOMBRE_EMPRESA", "Mi empresa de aires"), user, clave,
                          os.getenv("ADMIN_NOMBRE", "Administrador"))
            print(f"Empresa creada. Entra con el usuario «{user}».", flush=True)


inicializar_bd()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
