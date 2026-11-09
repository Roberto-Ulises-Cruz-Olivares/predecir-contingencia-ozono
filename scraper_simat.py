#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_simat.py - Calidad del aire de la CDMX (SIMAT / RAMA) por web scraping.

Métodos
  consultar   MÉTODO PRINCIPAL (web scraping). Raspa la "Consulta de concentraciones" del SIMAT:
              una petición por contaminante y mes (respuesta.php -> tabla HTML -> formato largo).
  descargar   Respaldo: archivos anuales oficiales de datos abiertos (contaminantes_AAAA.csv.gz)
              y, si no existe, el Excel RAMA del año (AARAMA.zip).
  sinaica     Respaldo: datos horarios de SINAICA (INECC) por estación y mes; validados y, donde faltan,
              preliminares. Sirve cuando la consulta del SIMAT falla y para meses sin archivo anual.
  importar    Procesa archivos que ya tengas en disco (RAMA .zip/.xls u opendata .csv/.csv.gz).
  consolidar  Une las fuentes, aplica reglas de calidad y genera las tablas analíticas.
  diagnostico Una sola petición para confirmar que el sitio responde con el formato esperado.

Uso típico (ozono 2015 a la fecha, ~140 peticiones, ~5 min):
  pip install httpx beautifulsoup4 lxml pandas xlrd
  python scraper_simat.py diagnostico
  python scraper_simat.py consultar --contaminantes o3 --desde 2015-01 --hasta 2026-10
  python scraper_simat.py consolidar --contaminante o3

Convención de hora del SIMAT: HORA 1..24 es el promedio del intervalo (h-1, h]. La hora 24 pertenece
al MISMO día (23:00-24:00). Aquí fecha_hora = inicio del intervalo = fecha + (hora - 1) h, en hora
estándar del centro (UTC-6, sin horario de verano). Así los agregados diarios coinciden con los del SIMAT.

Endpoints tomados del paquete R aire.zmvm (v1.0.2) y del sitio hoyodesmog.diegovalle.net, que los usan
en producción. Si el SIMAT cambia su sitio, corre `diagnostico` y revisa el HTML guardado.
"""
from __future__ import annotations

import argparse
import calendar
import gzip
import io
import random
import re
import sys
import time
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlencode, urljoin, urlparse

try:
    import numpy as np
    import pandas as pd
except ImportError:  # pragma: no cover
    sys.exit("Faltan dependencias. Corre:  pip install httpx beautifulsoup4 lxml pandas xlrd")

BASE_WEB = "http://www.aire.cdmx.gob.mx"
RUTA_CONSULTA = "/estadisticas-consultas/concentraciones/respuesta.php"
BASE_DATOS = "http://datosabiertos.aire.cdmx.gob.mx:8080"
USER_AGENT = "Mozilla/5.0 (proyecto-academico-calidad-aire; python-httpx)"

# parámetro web -> (clave SIMAT, unidad, máximo plausible para marcar errores)
PARAMS = {
    "o3": ("O3", "ppb", 350), "no2": ("NO2", "ppb", 500), "no": ("NO", "ppb", 1500),
    "nox": ("NOX", "ppb", 2000), "so2": ("SO2", "ppb", 500), "co": ("CO", "ppm", 40),
    "pm10": ("PM10", "µg/m³", 1500), "pm2": ("PM25", "µg/m³", 1000), "pmco": ("PMCO", "µg/m³", 1000),
}
CLAVE_A_WEB = {v[0]: k for k, v in PARAMS.items()}
ALIAS_PARAM = {"pm25": "pm2", "pm2.5": "pm2", "pm2_5": "pm2", "ozono": "o3"}
# validadas primero; la web y los crudos de SINAICA pueden ser preliminares
PRIORIDAD_FUENTE = {"rama": 0, "opendata": 1, "sinaica": 2, "web": 3, "sinaica_prelim": 4}

# SINAICA (INECC): sistema nacional que recibe los datos de la RAMA. Respaldo cuando la consulta del SIMAT falla.
URL_SINAICA = "https://sinaica.inecc.gob.mx/pags/datGrafs.php"
URL_ESTACIONES_SINAICA = "https://raw.githubusercontent.com/diegovalle/rsinaica/master/data-raw/stations_sinaica.csv"
RED_SINAICA = "CMX"                                         # red "Valle de México" = RAMA
# parámetro web -> (código SINAICA, factor para llevar a la unidad del SIMAT)
SINAICA_PARAM = {"o3": ("O3", 1000), "no2": ("NO2", 1000), "no": ("NO", 1000), "nox": ("NOx", 1000),
                 "so2": ("SO2", 1000), "co": ("CO", 1), "pm10": ("PM10", 1), "pm2": ("PM2.5", 1)}
RENOMBRA_ESTACION = {"CHA": "MON"}                          # Chapingo -> Montecillo (rediseño SIMAT 2011)
COLUMNAS = ["fecha", "hora", "estacion", "contaminante", "valor", "unidad", "fuente"]
HORAS_DIA_VALIDO = 18                                       # 75 % de 24 h para un dato diario válido

RX_EST = re.compile(r"^[A-Z]{3}$")
RX_FECHA_DMY = re.compile(r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")
RX_FECHA_YMD = re.compile(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
RX_HORA = re.compile(r"^\s*(\d{1,2})(?::00)?\s*$")
RX_ARCHIVO_RAMA = re.compile(r"(\d{4})([A-Z0-9]+)\.XLSX?$")


# ----------------------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------------------
def norm_param(p: str) -> str:
    p = p.strip().lower()
    p = ALIAS_PARAM.get(p, p)
    if p not in PARAMS:
        clave = p.upper().replace(".", "")
        if clave in CLAVE_A_WEB:
            return CLAVE_A_WEB[clave]
        raise SystemExit(f"Contaminante desconocido: {p}. Opciones: {', '.join(PARAMS)}")
    return p


def norm_estacion(s: str) -> str:
    s = str(s).strip().upper()
    return RENOMBRA_ESTACION.get(s, s)


def fecha_iso(txt: str) -> Optional[str]:
    m = RX_FECHA_DMY.match(txt or "")
    if m:
        d, mth, y = (int(x) for x in m.groups())
    else:
        m = RX_FECHA_YMD.match(txt or "")
        if not m:
            return None
        y, mth, d = (int(x) for x in m.groups())
    try:
        return date(y, mth, d).isoformat()
    except ValueError:
        return None


def valor_num(txt) -> Optional[float]:
    if txt is None:
        return None
    t = str(txt).strip().lower()
    if t in ("", "nr", "na", "nan", "n/d", "nd", "--", "-"):
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return None if v == -99 else v


def meses(desde: str, hasta: str) -> list[tuple[int, int]]:
    y0, m0 = (int(x) for x in desde.split("-"))
    y1, m1 = (int(x) for x in hasta.split("-"))
    out = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def guardar_csv_gz(df: pd.DataFrame, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    df.to_csv(tmp, index=False, compression="gzip", encoding="utf-8")
    tmp.replace(ruta)                       # escritura atómica: un corte no deja archivos a medias


# ----------------------------------------------------------------------------------------
# Parsers (sin red; se prueban por separado)
# ----------------------------------------------------------------------------------------
def parsear_html_consulta(html: str, parametro: str) -> tuple[pd.DataFrame, dict]:
    """Tabla HTML de respuesta.php -> formato largo. Regresa (datos, info de diagnóstico)."""
    from bs4 import BeautifulSoup

    clave, unidad, _ = PARAMS[parametro]
    soup = BeautifulSoup(html, "lxml")
    mejor: tuple[list[str], list[list[str]]] = ([], [])
    for tabla in soup.find_all("table"):
        encabezado: Optional[list[str]] = None
        datos: list[list[str]] = []
        for tr in tabla.find_all("tr"):
            celdas = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if not celdas:
                continue
            if encabezado is None:
                codigos = [c for c in celdas if RX_EST.match(c.strip().upper())]
                if len(codigos) >= 3:
                    encabezado = [c.strip().upper() for c in celdas]
                continue
            if fecha_iso(celdas[0]):
                datos.append(celdas)
        if encabezado and len(datos) > len(mejor[1]):
            mejor = (encabezado, datos)
    encabezado, datos = mejor
    info = {"filas_tabla": len(datos), "estaciones": [], "filas_malformadas": 0}
    if not datos:
        return pd.DataFrame(columns=COLUMNAS), info
    pos_est = {j: norm_estacion(c) for j, c in enumerate(encabezado) if RX_EST.match(c)}
    info["estaciones"] = sorted(pos_est.values())
    registros = []
    for celdas in datos:
        if len(celdas) != len(encabezado):
            # alinear por la derecha si la fila trae celdas de más o de menos al inicio
            desfase = len(celdas) - len(encabezado)
            if desfase < 0 or len(celdas) < 2:
                info["filas_malformadas"] += 1
                continue
            celdas = [celdas[0]] + celdas[1 + desfase:] if desfase else celdas
        f = fecha_iso(celdas[0])
        mh = RX_HORA.match(celdas[1]) if len(celdas) > 1 else None
        if not f or not mh or not 1 <= int(mh.group(1)) <= 24:
            info["filas_malformadas"] += 1
            continue
        h = int(mh.group(1))
        for j, est in pos_est.items():
            v = valor_num(celdas[j]) if j < len(celdas) else None
            if v is not None:
                registros.append((f, h, est, clave, v, unidad, "web"))
    return pd.DataFrame(registros, columns=COLUMNAS), info


def parsear_rama_excel(contenido: bytes, nombre: str, claves: Optional[set] = None) -> Optional[pd.DataFrame]:
    """Excel RAMA (FECHA, HORA, estaciones...; -99 = faltante) -> formato largo."""
    m = RX_ARCHIVO_RAMA.search(Path(nombre).name.upper())
    if not m:
        return None
    clave = m.group(2)
    if clave not in CLAVE_A_WEB or (claves and clave not in claves):
        return None
    _, unidad, _ = PARAMS[CLAVE_A_WEB[clave]]
    df = pd.read_excel(io.BytesIO(contenido))
    df.columns = [str(c).strip().upper() for c in df.columns]
    col_f, col_h = df.columns[0], df.columns[1]
    estaciones = [c for c in df.columns[2:] if RX_EST.match(c)]
    larga = df.melt(id_vars=[col_f, col_h], value_vars=estaciones, var_name="estacion", value_name="valor")
    larga["valor"] = pd.to_numeric(larga["valor"], errors="coerce")
    larga = larga[larga["valor"].notna() & (larga["valor"] != -99)]
    if pd.api.types.is_datetime64_any_dtype(larga[col_f]):
        f = larga[col_f].dt.strftime("%Y-%m-%d")
    else:
        f = larga[col_f].astype(str).map(fecha_iso)
    out = pd.DataFrame({
        "fecha": f.to_numpy(), "hora": pd.to_numeric(larga[col_h], errors="coerce").to_numpy(),
        "estacion": larga["estacion"].map(norm_estacion).to_numpy(), "contaminante": clave,
        "valor": larga["valor"].to_numpy(), "unidad": unidad, "fuente": "rama"})
    return out.dropna(subset=["fecha", "hora"]).astype({"hora": int})


def parsear_opendata_csv(ruta: Path, claves: Optional[set] = None) -> pd.DataFrame:
    """CSV anual de datos abiertos (10 líneas de metadatos + date,id_station,id_parameter,value,unit)."""
    abrir = gzip.open if ruta.suffix == ".gz" else open
    salto = None
    with abrir(ruta, "rt", encoding="latin-1") as f:
        for i, linea in enumerate(f):
            if linea.lower().replace('"', "").startswith("date,"):
                salto = i
                break
            if i > 50:
                break
    if salto is None:
        raise ValueError(f"{ruta.name}: no encontré el encabezado 'date,...'")
    partes = []
    ultima_fecha = None                              # para arrastrar la fecha entre bloques
    for ch in pd.read_csv(ruta, skiprows=salto, encoding="latin-1", dtype=str, chunksize=500_000):
        ch.columns = [c.strip().lower() for c in ch.columns]
        # Formato 2022+ ("AAAA-MM-DD HH:MM:SS"). La hora 24 llega de tres formas según el año:
        #   2022: fecha VACÍA justo después del bloque de las 23:00 -> se arrastra la fecha anterior
        #   2023: "AAAA-MM-DD 00:00:00" del día siguiente          -> día anterior, hora 24
        #   2024: solo "AAAA-MM-DD" (medianoche del día siguiente)  -> día anterior, hora 24
        #         (validado contra SINAICA: 100 % de horas 24 idénticas con esta regla, 2 % sin ella)
        d = ch["date"].fillna("").str.strip()
        if d.str.match(r"^\d{4}-").any() or (d == "").any():
            vacia = d == ""
            ymd = d.where(~vacia).ffill()
            if ultima_fecha is not None:
                ymd = ymd.fillna(ultima_fecha)
            if ymd.notna().any():
                ultima_fecha = ymd.dropna().iloc[-1]
            dia = pd.to_datetime(ymd.str.slice(0, 10), format="%Y-%m-%d", errors="coerce")
            hh = pd.to_numeric(ymd.str.slice(11, 13), errors="coerce")
            es24 = vacia                                   # 2022: la fecha ya es la del día correcto
            cero = (hh.eq(0) | hh.isna()) & ~vacia         # 2023 y 2024: medianoche -> día anterior
            dia = dia.where(~cero, dia - pd.Timedelta(days=1))
            hh = hh.where(~(es24 | cero), 24)
            # se reescribe en el formato DD/MM/AAAA HH que espera el resto del parser
            ch["date"] = dia.dt.strftime("%d/%m/%Y") + " " + hh.map(lambda h: "" if pd.isna(h) else f"{int(h):02d}")
        est = "id_station" if "id_station" in ch else "cve_station"
        par = "id_parameter" if "id_parameter" in ch else "cve_parameter"
        ch[par] = ch[par].str.strip().str.upper().str.replace(".", "", regex=False)
        if claves:
            ch = ch[ch[par].isin(claves)]
        ch = ch[ch[par].isin(CLAVE_A_WEB)]
        v = pd.to_numeric(ch["value"], errors="coerce")
        ch = ch[v.notna() & (v != -99)].assign(valor=v)
        if ch.empty:
            continue
        s = ch["date"].str.strip()
        fecha = s.str.slice(6, 10) + "-" + s.str.slice(3, 5) + "-" + s.str.slice(0, 2)
        hora = pd.to_numeric(s.str.slice(11, 13), errors="coerce")
        partes.append(pd.DataFrame({
            "fecha": fecha, "hora": hora, "estacion": ch[est].map(norm_estacion), "contaminante": ch[par],
            "valor": ch["valor"], "unidad": ch[par].map(lambda c: PARAMS[CLAVE_A_WEB[c]][1]), "fuente": "opendata"}))
    if not partes:
        return pd.DataFrame(columns=COLUMNAS)
    out = pd.concat(partes, ignore_index=True).dropna(subset=["hora"])
    return out.astype({"hora": int})


# ----------------------------------------------------------------------------------------
# robots.txt (comodines * y $, precedencia por regla más larga)
# ----------------------------------------------------------------------------------------
class Robots:
    def __init__(self, texto: str):
        grupos, agentes, reglas, en_reglas = [], [], [], False
        for linea in (texto or "").splitlines():
            linea = linea.split("#", 1)[0].strip()
            if ":" not in linea:
                continue
            k, v = (x.strip() for x in linea.split(":", 1))
            k = k.lower()
            if k == "user-agent":
                if en_reglas:
                    grupos.append((agentes, reglas))
                    agentes, reglas, en_reglas = [], [], False
                agentes.append(v.lower())
            elif k in ("allow", "disallow"):
                en_reglas = True
                if v:
                    reglas.append((k == "allow", v))
        if agentes:
            grupos.append((agentes, reglas))
        self.reglas = [(a, p, self._rx(p)) for ags, rs in grupos if "*" in ags for a, p in rs]

    @staticmethod
    def _rx(p: str) -> re.Pattern:
        fin = p.endswith("$")
        p = p[:-1] if fin else p
        return re.compile("^" + re.escape(p).replace(r"\*", ".*") + ("$" if fin else ""))

    def permitido(self, url: str) -> bool:
        u = urlparse(url)
        ruta = (u.path or "/") + (f"?{u.query}" if u.query else "")
        mejor = None
        for allow, pat, rx in self.reglas:
            if rx.match(ruta) and (mejor is None or len(pat) > mejor[0] or (len(pat) == mejor[0] and allow)):
                mejor = (len(pat), allow)
        return True if mejor is None else mejor[1]


# ----------------------------------------------------------------------------------------
# Cliente HTTP educado
# ----------------------------------------------------------------------------------------
class Cliente:
    def __init__(self, pausa: float, timeout: float = 120.0):
        import httpx

        self.httpx = httpx
        self.pausa = pausa
        self.http = httpx.Client(headers={"User-Agent": USER_AGENT, "Accept-Language": "es-MX,es;q=0.9"},
                                 timeout=timeout, follow_redirects=True)
        self._ultimo = 0.0

    def close(self) -> None:
        self.http.close()

    def get(self, url: str, params: Optional[dict] = None, reintentos: int = 5):
        return self._pedir("GET", url, params=params, reintentos=reintentos)

    def post(self, url: str, data: dict, reintentos: int = 3):
        return self._pedir("POST", url, data=data, reintentos=reintentos)

    def _pedir(self, metodo: str, url: str, reintentos: int, **kw):
        """Regresa (status, response|None). Respeta la pausa entre peticiones y reintenta con backoff."""
        for intento in range(1, reintentos + 1):
            espera = self._ultimo + self.pausa - time.monotonic()
            if espera > 0:
                time.sleep(espera + random.uniform(0, 0.3 * self.pausa))
            self._ultimo = time.monotonic()
            try:
                r = self.http.request(metodo, url, **kw)
            except self.httpx.HTTPError as e:
                if intento == reintentos:
                    print(f"  [red] {type(e).__name__}: {e}")
                    return -1, None
                time.sleep(min(120, 3 * 2 ** intento))
                continue
            if r.status_code in (429, 500, 502, 503, 504) and intento < reintentos:
                ra = r.headers.get("Retry-After", "")
                time.sleep(float(ra) if ra.isdigit() else min(120, 3 * 2 ** intento))
                continue
            return r.status_code, r
        return -1, None


def decodificar(r) -> str:
    enc = r.charset_encoding or "latin-1"          # el SIMAT sirve ISO-8859-1
    try:
        return r.content.decode(enc, errors="replace")
    except LookupError:
        return r.content.decode("latin-1", errors="replace")


def leer_robots(cli: Cliente, base: str) -> Robots:
    st, r = cli.get(base.rstrip("/") + "/robots.txt", reintentos=2)
    if st == 200 and r is not None and "<html" not in r.text[:500].lower():
        return Robots(r.text)
    return Robots("")                                # sin robots.txt => todo permitido (estándar)


# ----------------------------------------------------------------------------------------
# Comandos
# ----------------------------------------------------------------------------------------
def cmd_consultar(a) -> None:
    raiz = Path(a.dir)
    hoy = date.today()
    params = [norm_param(p) for p in a.contaminantes]
    lista = [(p, y, m) for p in params for (y, m) in meses(a.desde, a.hasta) if date(y, m, 1) <= hoy]
    cli = Cliente(a.pausa)
    try:
        rob = leer_robots(cli, a.base)
        url = a.base.rstrip("/") + RUTA_CONSULTA
        if not rob.permitido(url + "?qtipo=HORARIOS"):
            raise SystemExit("robots.txt no permite la consulta de concentraciones. Usa: descargar")
        pendientes = []
        for p, y, m in lista:
            destino = raiz / "largo" / f"web_{p}_{y}_{m:02d}.csv.gz"
            reciente = (hoy.year - y) * 12 + (hoy.month - m) < a.refrescar_meses
            if destino.exists() and not reciente:
                continue
            pendientes.append((p, y, m, destino))
        print(f"[consultar] {len(lista)} meses solicitados | ya descargados: {len(lista) - len(pendientes)} | "
              f"pendientes: {len(pendientes)} (~{len(pendientes) * a.pausa / 60:.1f} min)")
        fallos_seguidos, total_filas = 0, 0
        for k, (p, y, m, destino) in enumerate(pendientes, 1):
            q = {"qtipo": "HORARIOS", "parametro": p, "anio": y, "qmes": f"{m:02d}"}
            st, r = cli.get(url, params=q)
            if st != 200 or r is None:
                fallos_seguidos += 1
                print(f"  [{k}/{len(pendientes)}] {p} {y}-{m:02d}: HTTP {st}")
                if fallos_seguidos >= 5:
                    raise SystemExit("5 fallos seguidos. El sitio no responde o te está bloqueando: espera, sube "
                                     "--pausa o usa el respaldo: python scraper_simat.py descargar")
                continue
            fallos_seguidos = 0
            html = decodificar(r)
            crudo = raiz / "crudo" / "web" / f"{p}_{y}_{m:02d}.html.gz"
            crudo.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(crudo, "wt", encoding="utf-8") as f:
                f.write(html)
            df, info = parsear_html_consulta(html, p)
            dias = calendar.monthrange(y, m)[1]
            esperado = dias * 24 if (y, m) != (hoy.year, hoy.month) else hoy.day * 24
            cobertura = info["filas_tabla"] / esperado if esperado else 0
            aviso = "" if cobertura >= 0.9 else "  <-- revisa (tabla incompleta o formato distinto)"
            print(f"  [{k}/{len(pendientes)}] {p} {y}-{m:02d}: {info['filas_tabla']} horas x "
                  f"{len(info['estaciones'])} estaciones -> {len(df):,} valores{aviso}")
            if df.empty:
                print(f"     Sin datos. HTML guardado en {crudo} para revisarlo.")
                continue
            guardar_csv_gz(df, destino)
            total_filas += len(df)
        print(f"[consultar] listo: {total_filas:,} valores nuevos en {raiz / 'largo'}")
    finally:
        cli.close()


def _importar_archivo(ruta: Path, raiz: Path, claves: Optional[set]) -> int:
    n = 0
    nombre = ruta.name.lower()
    if nombre.endswith(".zip"):
        with zipfile.ZipFile(ruta) as z:          # se lee en memoria: no se extrae nada a disco
            for miembro in z.namelist():
                df = parsear_rama_excel(z.read(miembro), miembro, claves) if RX_ARCHIVO_RAMA.search(
                    Path(miembro).name.upper()) else None
                if df is not None and not df.empty:
                    m = RX_ARCHIVO_RAMA.search(Path(miembro).name.upper())
                    guardar_csv_gz(df, raiz / "largo" / f"rama_{CLAVE_A_WEB[m.group(2)]}_{m.group(1)}.csv.gz")
                    n += len(df)
                    print(f"  {ruta.name}:{miembro} -> {len(df):,} valores")
    elif nombre.endswith((".xls", ".xlsx")):
        df = parsear_rama_excel(ruta.read_bytes(), ruta.name, claves)
        if df is not None and not df.empty:
            m = RX_ARCHIVO_RAMA.search(ruta.name.upper())
            guardar_csv_gz(df, raiz / "largo" / f"rama_{CLAVE_A_WEB[m.group(2)]}_{m.group(1)}.csv.gz")
            n += len(df)
            print(f"  {ruta.name} -> {len(df):,} valores")
    elif nombre.endswith((".csv", ".csv.gz")):
        df = parsear_opendata_csv(ruta, claves)
        for (clave, anio), g in df.groupby([df["contaminante"], df["fecha"].str.slice(0, 4)]):
            guardar_csv_gz(g, raiz / "largo" / f"opendata_{CLAVE_A_WEB[clave]}_{anio}.csv.gz")
            n += len(g)
            print(f"  {ruta.name} [{clave} {anio}] -> {len(g):,} valores")
    else:
        print(f"  (omitido, formato no reconocido) {ruta}")
    return n


def cmd_importar(a) -> None:
    raiz = Path(a.dir)
    claves = {PARAMS[norm_param(p)][0] for p in a.contaminantes} if a.contaminantes else None
    total = 0
    for p in a.archivos:
        ruta = Path(p)
        if not ruta.exists():
            print(f"  no existe: {ruta}")
            continue
        total += _importar_archivo(ruta, raiz, claves)
    print(f"[importar] {total:,} valores en {raiz / 'largo'}")


RX_ZIP_JS = re.compile(r"location\.href\s*=\s*['\"]([^'\"]+\.zip)['\"]", re.I)


def _url_zip_rama(cli: Cliente, base: str, anio: int) -> Optional[str]:
    """Formulario oficial de descarga RAMA: POST seluniano=AA -> el HTML trae location.href='...AARAMA.zip'."""
    url_form = base.rstrip("/") + "/default.php?opc=%27aKBh%27"
    st, r = cli.post(url_form, data={"seluniano": f"{anio % 100:02d}"})
    if st != 200 or r is None:
        return None
    m = RX_ZIP_JS.search(decodificar(r))
    return urljoin(url_form, m.group(1)) if m else None


FIRMAS = {".zip": b"PK\x03\x04", ".gz": b"\x1f\x8b", ".xls": b"\xd0\xcf\x11\xe0", ".xlsx": b"PK\x03\x04"}


def _contenido_valido(contenido: bytes, archivo: Path) -> bool:
    """Valida por firma binaria (no por tamaño): un 200 con página de error HTML no cuenta como descarga."""
    firma = FIRMAS.get(archivo.suffix.lower())
    if firma is not None:
        return contenido.startswith(firma)
    inicio = contenido[:512].lower()
    return bool(contenido) and b"<html" not in inicio and b"<!doctype" not in inicio


def _bajar(cli: Cliente, url: str, archivo: Path) -> bool:
    candidatos = [url]
    if url.startswith("https://"):                  # el https del SIMAT usa TLS antiguo; http suele funcionar
        candidatos.append("http://" + url[len("https://"):])
    for u in candidatos:
        st, r = cli.get(u, reintentos=3)
        if st != 200 or r is None:
            continue
        contenido = r.content
        # Desde 2022 el portal de datos abiertos publica ZIP con nombre .csv.gz: se re-empaqueta como gzip real.
        if archivo.suffix.lower() == ".gz" and contenido.startswith(FIRMAS[".zip"]):
            with zipfile.ZipFile(io.BytesIO(contenido)) as z:
                csvs = [m for m in z.namelist() if m.lower().endswith(".csv")]
                if not csvs:
                    continue
                contenido = gzip.compress(z.read(csvs[0]))
        if _contenido_valido(contenido, archivo):
            archivo.write_bytes(contenido)
            return True
    return False


def cmd_descargar(a) -> None:
    raiz = Path(a.dir)
    destino = raiz / "crudo" / "archivos"
    destino.mkdir(parents=True, exist_ok=True)
    claves = {PARAMS[norm_param(p)][0] for p in a.contaminantes}
    a0, _, a1 = a.anios.partition("-")
    anios = range(int(a0), int(a1 or a0) + 1)
    cli = Cliente(a.pausa, timeout=600)
    try:
        for anio in anios:
            archivo = destino / f"{anio % 100:02d}RAMA.zip"
            if not archivo.exists():                 # 1) Excel RAMA del año (incluye el año en curso)
                url = _url_zip_rama(cli, a.base, anio)
                if url and not _bajar(cli, url, archivo):
                    print(f"  {anio}: el formulario dio {url} pero la descarga falló")
            if not archivo.exists():                 # 2) CSV anual de datos abiertos
                archivo = destino / f"contaminantes_{anio}.csv.gz"
                if not archivo.exists():
                    _bajar(cli, f"{BASE_DATOS}/opendata/anuales_horarios_gz/contaminantes_{anio}.csv.gz", archivo)
            if archivo.exists():
                print(f"  {anio}: {archivo.name} ({archivo.stat().st_size / 1e6:.1f} MB)")
                _importar_archivo(archivo, raiz, claves)
            else:
                print(f"  {anio}: sin archivo de respaldo; usa 'consultar' para ese año")
    finally:
        cli.close()


RX_DAT_SINAICA = re.compile(r"var\s+dat\s*=\s*(\[[\s\S]*?\]);")


def parsear_sinaica(html: str, estacion: str, p: str, y: int, m: int, fuente: str) -> pd.DataFrame:
    """Respuesta de datGrafs.php -> formato largo del SIMAT.

    SINAICA reporta hora 0..23 y la convención cambia según el tipo de dato:
      validados (V): hora_SIMAT = hora + 1, misma fecha
                     (contra CSV oficiales 2021-2024 y RAMA 2025: 100 % de horas idénticas)
      crudos:        van una hora adelante de los validados -> hora_SIMAT = hora, y la hora 0 es la
                     hora 24 del día anterior (crudo vs validado 2025: 99-100 % idénticas con este ajuste)
    Los gases vienen en ppm; se pasan a ppb como en el SIMAT.
    """
    import json

    mt = RX_DAT_SINAICA.search(html or "")
    filas = json.loads(mt.group(1)) if mt else []
    if not filas:
        return pd.DataFrame(columns=COLUMNAS)
    d = pd.DataFrame(filas)
    d = d[d["fecha"].str.startswith(f"{y}-{m:02d}")]          # rango=4 trae ~32 días; se queda el mes pedido
    if "val" in d:
        d = d[pd.to_numeric(d["val"], errors="coerce").fillna(1) != 0]
    v = pd.to_numeric(d["valor"], errors="coerce") * SINAICA_PARAM[p][1]
    d = d.assign(valor=v.round(4))
    d = d[d["valor"].notna() & (d["valor"] >= 0)]
    clave, unidad, _ = PARAMS[p]
    hora = d["hora"].astype(int)
    fecha = d["fecha"]
    if fuente == "sinaica":
        hora = hora + 1
    else:
        cero = hora == 0
        fecha = fecha.where(~cero, (pd.to_datetime(fecha) - pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d"))
        hora = hora.where(~cero, 24)
    return pd.DataFrame({"fecha": fecha, "hora": hora, "estacion": estacion,
                         "contaminante": clave, "valor": d["valor"], "unidad": unidad, "fuente": fuente})


def estaciones_sinaica(cli: Cliente, raiz: Path) -> pd.DataFrame:
    ruta = raiz / "catalogos" / "estaciones_sinaica.csv"
    if not ruta.exists():
        st, r = cli.get(URL_ESTACIONES_SINAICA, reintentos=3)
        if st != 200 or r is None:
            raise SystemExit(f"No pude bajar el catálogo de estaciones SINAICA ({URL_ESTACIONES_SINAICA}).")
        cat = pd.read_csv(io.StringIO(r.text))
        cat = cat[cat["network_code"] == RED_SINAICA][["station_id", "station_code", "station_name", "lat", "lon"]]
        ruta.parent.mkdir(parents=True, exist_ok=True)
        cat.to_csv(ruta, index=False)
    return pd.read_csv(ruta)


def cmd_sinaica(a) -> None:
    """Datos horarios de SINAICA por estación y mes: validados (V) y, si faltan, crudos (preliminares)."""
    raiz = Path(a.dir)
    hoy = date.today()
    params = [norm_param(p) for p in a.contaminantes]
    for p in params:
        if p not in SINAICA_PARAM:
            raise SystemExit(f"SINAICA no tiene {p}. Opciones: {', '.join(SINAICA_PARAM)}")
    lista = [(y, m) for (y, m) in meses(a.desde, a.hasta) if date(y, m, 1) <= hoy]
    cli = Cliente(a.pausa)
    try:
        cat = estaciones_sinaica(cli, raiz)
        if a.estaciones:
            cat = cat[cat["station_code"].isin([e.upper() for e in a.estaciones])]
        crudo_dir = raiz / "crudo" / "sinaica"
        crudo_dir.mkdir(parents=True, exist_ok=True)
        print(f"[sinaica] {len(params)} contaminante(s) x {len(cat)} estaciones x {len(lista)} meses")
        total, fallos_seguidos = 0, 0
        for p in params:
            for (y, m) in lista:
                destino = raiz / "largo" / f"sinaica_{p}_{y}_{m:02d}.csv.gz"
                reciente = (hoy.year - y) * 12 + (hoy.month - m) < a.refrescar_meses
                if destino.exists() and not reciente:
                    continue
                horas_mes = calendar.monthrange(y, m)[1] * 24
                partes, resumen = [], {"sinaica": 0, "sinaica_prelim": 0}
                for _, est in cat.iterrows():
                    codigo = norm_estacion(est["station_code"])
                    validado = pd.DataFrame(columns=COLUMNAS)
                    for tipo, fuente in (("V", "sinaica"), ("", "sinaica_prelim")):
                        # crudos solo si los validados no cubren el mes
                        if fuente == "sinaica_prelim" and len(validado) >= 0.9 * horas_mes:
                            break
                        cache = crudo_dir / f"{p}_{codigo}_{y}_{m:02d}_{tipo or 'C'}.html.gz"
                        if cache.exists() and not reciente:
                            with gzip.open(cache, "rt", encoding="utf-8") as f:
                                html = f.read()
                        else:
                            st, r = cli.post(URL_SINAICA, data={
                                "estacionId": int(est["station_id"]), "param": SINAICA_PARAM[p][0],
                                "fechaIni": f"{y}-{m:02d}-01", "rango": 4, "tipoDatos": tipo, "datoBase": 1})
                            if st != 200 or r is None:
                                fallos_seguidos += 1
                                print(f"  {p} {y}-{m:02d} {codigo} {fuente}: HTTP {st}")
                                if fallos_seguidos >= 8:
                                    raise SystemExit("8 fallos seguidos: SINAICA no responde. Espera o sube --pausa.")
                                continue
                            fallos_seguidos = 0
                            html = r.text
                            with gzip.open(cache, "wt", encoding="utf-8") as f:
                                f.write(html)
                        df = parsear_sinaica(html, codigo, p, y, m, fuente)
                        if fuente == "sinaica":
                            validado = df
                        else:                      # solo las horas que no trae la versión validada
                            ya = set(zip(validado["fecha"], validado["hora"]))
                            df = df[[(f, h) not in ya for f, h in zip(df["fecha"], df["hora"])]]
                        if not df.empty:
                            partes.append(df)
                            resumen[fuente] += len(df)
                if partes:
                    guardar_csv_gz(pd.concat(partes, ignore_index=True), destino)
                n = sum(resumen.values())
                total += n
                print(f"  {p} {y}-{m:02d}: {n:,} valores (validados {resumen['sinaica']:,} | "
                      f"preliminares {resumen['sinaica_prelim']:,})")
        print(f"[sinaica] listo: {total:,} valores en {raiz / 'largo'}")
    finally:
        cli.close()


def cmd_diagnostico(a) -> None:
    raiz = Path(a.dir)
    hoy = date.today()
    y, m = (hoy.year, hoy.month - 1) if hoy.month > 1 else (hoy.year - 1, 12)
    p = norm_param(a.contaminante)
    cli = Cliente(1.0)
    try:
        rob = leer_robots(cli, a.base)
        url = a.base.rstrip("/") + RUTA_CONSULTA
        print(f"robots.txt permite la consulta: {rob.permitido(url + '?qtipo=HORARIOS')}")
        q = {"qtipo": "HORARIOS", "parametro": p, "anio": y, "qmes": f"{m:02d}"}
        st, r = cli.get(url, params=q, reintentos=2)
        print(f"GET {url}?{urlencode(q)} -> HTTP {st}")
        if r is None:
            raise SystemExit("Sin respuesta. Revisa tu conexión (o prueba otra red).")
        html = decodificar(r)
        muestra = raiz / "crudo" / f"diagnostico_{p}_{y}_{m:02d}.html"
        muestra.parent.mkdir(parents=True, exist_ok=True)
        muestra.write_text(html, encoding="utf-8")
        df, info = parsear_html_consulta(html, p)
        print(f"Tabla: {info['filas_tabla']} filas (esperado ~{calendar.monthrange(y, m)[1] * 24}) | "
              f"estaciones: {len(info['estaciones'])} {info['estaciones'][:8]}... | valores: {len(df):,}")
        if df.empty:
            print(f"No pude leer la tabla. El HTML quedó en {muestra}; ese archivo dice qué cambió en el sitio.")
        else:
            print(df.head(5).to_string(index=False))
            print(f"Rango: {df['valor'].min()} - {df['valor'].max()} {df['unidad'].iloc[0]}  -> todo listo.")
    finally:
        cli.close()


# ----------------------------------------------------------------------------------------
# Consolidación + reglas de calidad + tablas analíticas
# ----------------------------------------------------------------------------------------
def marcar_flatlines(df: pd.DataFrame, min_horas: int = 6, umbral: float = 10.0) -> pd.Series:
    """True en valores que forman parte de >= min_horas horas seguidas idénticas (sensor 'pegado')."""
    d = df.sort_values(["estacion", "fecha_hora"])
    mismo = (d["estacion"].eq(d["estacion"].shift()) & d["valor"].eq(d["valor"].shift())
             & (d["fecha_hora"] - d["fecha_hora"].shift()).eq(pd.Timedelta(hours=1)))
    corrida = (~mismo).cumsum()
    largo = d.groupby(corrida)["valor"].transform("size")
    return ((largo >= min_horas) & (d["valor"] > umbral)).reindex(df.index)


FUENTES_PRELIMINARES = {"web", "sinaica_prelim"}


def marcar_picos(df: pd.DataFrame, maximo: float) -> pd.Series:
    """True en picos aislados de datos PRELIMINARES: un valor > 2.5 veces y > 15 % del máximo plausible
    por encima de la hora anterior y de la siguiente de la misma estación (p. ej. 200 ppb entre dos de 48).
    Los datos validados no se tocan: ahí el SIMAT ya revisó los picos."""
    d = df.sort_values(["estacion", "fecha_hora"])
    misma = d["estacion"].eq(d["estacion"].shift())
    sig_misma = d["estacion"].eq(d["estacion"].shift(-1))
    ant = d["valor"].shift().where(misma & (d["fecha_hora"] - d["fecha_hora"].shift()).eq(pd.Timedelta(hours=1)))
    sig = d["valor"].shift(-1).where(sig_misma & (d["fecha_hora"].shift(-1) - d["fecha_hora"]).eq(pd.Timedelta(hours=1)))
    vecino = pd.concat([ant, sig], axis=1).max(axis=1)
    pico = (d["valor"] > 2.5 * vecino) & (d["valor"] - vecino > 0.15 * maximo) & d["fuente"].isin(FUENTES_PRELIMINARES)
    return pico.reindex(df.index).fillna(False)


def tablas_red(h: pd.DataFrame, clave: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    ok = h[~h["flag"].isin(["rango", "pico"])]
    g = ok.groupby("fecha_hora")["valor"]
    idx_max = ok.loc[g.idxmax(), ["fecha_hora", "estacion"]].set_index("fecha_hora")["estacion"]
    horario = pd.DataFrame({"n_estaciones": g.size(), "maximo": g.max(), "media": g.mean().round(2),
                            "p90": g.quantile(0.9).round(2), "estacion_max": idx_max}).reset_index()
    horario.insert(1, "fecha", horario["fecha_hora"].dt.strftime("%Y-%m-%d"))
    horario.insert(2, "hora", horario["fecha_hora"].dt.hour + 1)
    # diario por estación (solo días con >= 75 % de horas) -> máximo de la red
    est_dia = ok.groupby(["fecha", "estacion"])["valor"].agg(["size", "max", "mean"]).reset_index()
    validos = est_dia[est_dia["size"] >= HORAS_DIA_VALIDO]
    gd = validos.groupby("fecha")
    i_max = validos.loc[gd["max"].idxmax(), ["fecha", "estacion"]].set_index("fecha")["estacion"]
    diario = pd.DataFrame({"maximo_red": gd["max"].max(), "estacion_max": i_max,
                           "media_red": gd["mean"].mean().round(2),
                           "n_estaciones_validas": gd.size()}).reset_index()
    reportando = est_dia.groupby("fecha").size().rename("n_estaciones_reportando").reset_index()
    diario = reportando.merge(diario, on="fecha", how="left")
    hora_max = (ok.merge(diario[["fecha", "estacion_max", "maximo_red"]],
                         left_on=["fecha", "estacion", "valor"], right_on=["fecha", "estacion_max", "maximo_red"])
                .groupby("fecha")["hora"].min().rename("hora_max"))
    diario = diario.merge(hora_max, on="fecha", how="left")
    if clave == "O3":
        diario["supera_nom_90ppb"] = diario["maximo_red"] > 90
        diario["supera_fase1_155ppb"] = diario["maximo_red"] >= 155
    cols = ["fecha", "maximo_red", "estacion_max", "hora_max", "media_red", "n_estaciones_validas",
            "n_estaciones_reportando"] + [c for c in diario.columns if c.startswith("supera_")]
    return horario, diario[cols]


def cmd_consolidar(a) -> None:
    raiz = Path(a.dir)
    p = norm_param(a.contaminante)
    clave, unidad, maximo = PARAMS[p]
    archivos = sorted((raiz / "largo").glob(f"*_{p}_*.csv.gz"))
    if not archivos:
        raise SystemExit(f"No hay datos de {clave} en {raiz / 'largo'}. Corre 'consultar' o 'importar' primero.")
    df = pd.concat((pd.read_csv(f, dtype={"fecha": str, "estacion": str}) for f in archivos), ignore_index=True)
    n0 = len(df)
    rep = [f"REPORTE DE CALIDAD - {clave} ({unidad})", "=" * 64,
           f"Archivos: {len(archivos)} | valores leídos: {n0:,} | fuentes: {df['fuente'].value_counts().to_dict()}"]
    df = df[df["hora"].between(1, 24)]
    rep.append(f"Horas fuera de 1..24 descartadas: {n0 - len(df):,}")
    # duplicados entre fuentes: misma estación-hora; se queda la validada
    df["_prio"] = df["fuente"].map(PRIORIDAD_FUENTE).fillna(9)
    llave = ["estacion", "fecha", "hora"]
    multi = df[df.duplicated(llave, keep=False)]
    if not multi.empty and multi["fuente"].nunique() > 1:
        piv = multi.pivot_table(index=llave, columns="fuente", values="valor", aggfunc="first")
        fuentes = list(piv.columns)
        for i in range(len(fuentes)):
            for j in range(i + 1, len(fuentes)):
                par = piv[[fuentes[i], fuentes[j]]].dropna()
                if len(par):
                    dif = (par[fuentes[i]] - par[fuentes[j]]).abs()
                    rep.append(f"Cruce {fuentes[i]} vs {fuentes[j]}: {len(par):,} horas en común | "
                               f"idénticas {(dif < 0.5).mean():.1%} | dif. media {dif.mean():.2f} {unidad}")
    df = df.sort_values("_prio").drop_duplicates(llave, keep="first").drop(columns="_prio")
    rep.append(f"Duplicados resueltos (prioridad rama > opendata > sinaica > web > sinaica_prelim): "
               f"{n0 - len(df):,}")
    df["fecha_hora"] = pd.to_datetime(df["fecha"]) + pd.to_timedelta(df["hora"] - 1, unit="h")
    df["flag"] = "ok"
    neg = df["valor"] < 0
    df.loc[neg, "valor"] = np.nan
    df = df[df["valor"].notna()].copy()
    df.loc[df["valor"] > maximo, "flag"] = "rango"
    df.loc[marcar_flatlines(df) & df["flag"].eq("ok"), "flag"] = "flatline"
    df.loc[marcar_picos(df, maximo) & df["flag"].eq("ok"), "flag"] = "pico"
    rep += [f"Negativos eliminados: {int(neg.sum()):,}",
            f"Fuera de rango plausible (> {maximo} {unidad}, excluidos de las tablas): {int((df['flag'] == 'rango').sum()):,}",
            f"Sensor 'pegado' (>= 6 h idénticas, solo marcado): {int((df['flag'] == 'flatline').sum()):,}",
            f"Picos aislados en datos preliminares (excluidos de las tablas): {int((df['flag'] == 'pico').sum()):,}",
            f"Valores preliminares (sin validar) en la tabla final: "
            f"{int(df['fuente'].isin(FUENTES_PRELIMINARES).sum()):,} | desde "
            f"{df.loc[df['fuente'].isin(FUENTES_PRELIMINARES), 'fecha'].min()}"]
    df = df.sort_values(["fecha_hora", "estacion"])
    cols = ["fecha_hora", "fecha", "hora", "estacion", "contaminante", "valor", "unidad", "fuente", "flag"]
    out = raiz / "salida"
    out.mkdir(parents=True, exist_ok=True)
    df[cols].to_csv(out / f"{p}_horario_estaciones.csv.gz", index=False, compression="gzip",
                    date_format="%Y-%m-%d %H:%M")
    horario, diario = tablas_red(df, clave)
    horario.to_csv(out / f"{p}_horario_red.csv", index=False, date_format="%Y-%m-%d %H:%M")
    diario.to_csv(out / f"{p}_diario_red.csv", index=False)
    # completitud por estación dentro de su periodo activo
    comp = df.groupby("estacion")["fecha_hora"].agg(["min", "max", "size"])
    comp["horas_periodo"] = ((comp["max"] - comp["min"]) / pd.Timedelta(hours=1) + 1).astype(int)
    comp["completitud"] = (comp["size"] / comp["horas_periodo"]).round(3)
    rep += ["", f"Periodo: {df['fecha'].min()} a {df['fecha'].max()} | estaciones: {df['estacion'].nunique()}",
            f"Valores finales: {len(df):,} (tabla horaria por estación)",
            f"Horas de red: {len(horario):,} | días: {len(diario):,} | "
            f"días con < 10 estaciones válidas: {int((diario['n_estaciones_validas'].fillna(0) < 10).sum()):,}",
            "", "Estaciones con menor completitud (dentro de su periodo activo):",
            comp.sort_values("completitud").head(8)[["min", "max", "completitud"]].to_string()]
    if clave == "O3":
        por_anio = diario.assign(anio=diario["fecha"].str.slice(0, 4)).groupby("anio").agg(
            dias=("fecha", "size"), max_anual=("maximo_red", "max"),
            dias_sobre_nom=("supera_nom_90ppb", "sum"), dias_fase1=("supera_fase1_155ppb", "sum"))
        rep += ["", "Días por año sobre la NOM (90 ppb, 1 h) y con máximo >= 155 ppb (umbral Fase I):",
                por_anio.to_string()]
    texto = "\n".join(rep) + "\n"
    (out / f"calidad_{p}.txt").write_text(texto, encoding="utf-8")
    print(texto)
    print(f"[consolidar] tablas en {out}:\n  {p}_horario_estaciones.csv.gz ({len(df):,} filas)\n"
          f"  {p}_horario_red.csv ({len(horario):,} filas)\n  {p}_diario_red.csv ({len(diario):,} filas)")


# ----------------------------------------------------------------------------------------
def main(argv: Optional[Iterable[str]] = None) -> None:
    comunes = argparse.ArgumentParser(add_help=False)
    comunes.add_argument("--dir", default="datos_aire", help="carpeta de trabajo (default: datos_aire)")
    ap = argparse.ArgumentParser(description="Calidad del aire CDMX (SIMAT) - opciones después del subcomando")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("consultar", parents=[comunes], help="web scraping mensual (método principal)")
    c.add_argument("--contaminantes", nargs="+", default=["o3"])
    c.add_argument("--desde", default="2015-01", help="AAAA-MM")
    c.add_argument("--hasta", default=date.today().strftime("%Y-%m"), help="AAAA-MM")
    c.add_argument("--pausa", type=float, default=2.0, help="segundos entre peticiones (default 2)")
    c.add_argument("--refrescar-meses", type=int, default=2,
                   help="vuelve a bajar los últimos N meses (pueden traer datos preliminares)")
    c.add_argument("--base", default=BASE_WEB, help=argparse.SUPPRESS)
    d = sub.add_parser("descargar", parents=[comunes], help="respaldo: archivos anuales oficiales")
    d.add_argument("--anios", default=f"2015-{date.today().year}", help="AAAA o AAAA-AAAA")
    d.add_argument("--contaminantes", nargs="+", default=["o3"])
    d.add_argument("--pausa", type=float, default=2.0)
    d.add_argument("--base", default=BASE_WEB, help=argparse.SUPPRESS)
    s = sub.add_parser("sinaica", parents=[comunes],
                       help="respaldo: datos horarios de SINAICA/INECC (validados y, si faltan, preliminares)")
    s.add_argument("--contaminantes", nargs="+", default=["o3"])
    s.add_argument("--desde", default="2025-01", help="AAAA-MM")
    s.add_argument("--hasta", default=date.today().strftime("%Y-%m"), help="AAAA-MM")
    s.add_argument("--estaciones", nargs="+", default=None, help="claves SIMAT (default: toda la red)")
    s.add_argument("--pausa", type=float, default=1.0, help="segundos entre peticiones (default 1)")
    s.add_argument("--refrescar-meses", type=int, default=2,
                   help="vuelve a bajar los últimos N meses (los preliminares cambian)")
    i = sub.add_parser("importar", parents=[comunes], help="procesar archivos locales (.zip/.xls/.csv/.csv.gz)")
    i.add_argument("archivos", nargs="+")
    i.add_argument("--contaminantes", nargs="+", default=None)
    k = sub.add_parser("consolidar", parents=[comunes], help="unir, validar y generar tablas")
    k.add_argument("--contaminante", default="o3")
    g = sub.add_parser("diagnostico", parents=[comunes], help="1 petición de prueba")
    g.add_argument("--contaminante", default="o3")
    g.add_argument("--base", default=BASE_WEB, help=argparse.SUPPRESS)
    a = ap.parse_args(list(argv) if argv is not None else None)
    try:
        {"consultar": cmd_consultar, "descargar": cmd_descargar, "importar": cmd_importar, "sinaica": cmd_sinaica,
         "consolidar": cmd_consolidar, "diagnostico": cmd_diagnostico}[a.cmd](a)
    except KeyboardInterrupt:
        sys.exit("\nInterrumpido. Vuelve a correr el mismo comando: continúa donde se quedó.")


if __name__ == "__main__":
    main()
