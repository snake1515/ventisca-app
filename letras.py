"""Números y fechas en letras (español de Colombia) para las cuentas de cobro."""

UNIDADES = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce",
            "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte",
            "veintiuno", "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete",
            "veintiocho", "veintinueve"]
DECENAS = ["", "", "", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
CENTENAS = ["", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos", "setecientos",
            "ochocientos", "novecientos"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]


def _menor_mil(n):
    if n == 0:
        return ""
    if n == 100:
        return "cien"
    c, r = divmod(n, 100)
    partes = [CENTENAS[c]] if c else []
    if r < 30:
        if r:
            partes.append(UNIDADES[r])
    else:
        d, u = divmod(r, 10)
        partes.append(DECENAS[d] + (f" y {UNIDADES[u]}" if u else ""))
    return " ".join(partes)


def _apocopar(txt):
    """«uno» → «un» al final (veintiún mil, un millón)."""
    if txt.endswith("veintiuno"):
        return txt[:-4] + "ún"
    if txt.endswith("uno"):
        return txt[:-1]
    return txt


def numero_a_letras(n):
    n = int(round(n))
    if n == 0:
        return "cero"
    millones, resto = divmod(n, 1_000_000)
    miles, unidades = divmod(resto, 1000)
    partes = []
    if millones:
        if millones == 1:
            partes.append("un millón")
        else:
            partes.append(_apocopar(numero_a_letras(millones)) + " millones")
    if miles:
        partes.append("mil" if miles == 1 else _apocopar(_menor_mil(miles)) + " mil")
    if unidades:
        partes.append(_menor_mil(unidades))
    return " ".join(partes)


def pesos_en_letras(valor):
    """410000 → «Cuatrocientos diez mil pesos M/L ($410.000,oo)»."""
    v = int(round(valor or 0))
    txt = numero_a_letras(v)
    if v % 1_000_000 == 0 and v:
        txt += " de"  # «un millón de pesos»
    txt = _apocopar(txt) if txt.endswith("uno") else txt
    cifra = f"{v:,}".replace(",", ".")
    moneda = "peso" if v == 1 else "pesos"
    return f"{txt[0].upper()}{txt[1:]} {moneda} M/L (${cifra},oo)"


def fecha_larga(d):
    """2026-03-12 → «Marzo 12 de 2026»."""
    return f"{MESES[d.month - 1].capitalize()} {d.day} de {d.year}"
