"""Web scraping de SINAICA (INECC) con un navegador Chromium controlado por Playwright.

Qué hace, igual que lo haría una persona en https://sinaica.inecc.gob.mx/data.php:
  1. Abre la página de datos validados (?tipo=V) y, en otra pestaña, la de datos crudos (?tipo=C).
  2. Lee del propio formulario el catálogo de estaciones de la red "Valle de México".
  3. Por cada estación: la elige en el buscador del formulario, elige "Ozono", rango "1 mes".
  4. Por cada mes: escribe la fecha, pulsa "Actualizar" y captura la respuesta que dibuja la tabla
     y la gráfica (los datos vienen dentro de JavaScript: no hay tabla HTML que leer sin navegador).
  5. Si los validados no cubren el 90 % del mes (meses recientes), repite la búsqueda en la pestaña
     de crudos para completar las horas que faltan.

Todo lo que responde el sitio se guarda tal cual (HTML .gz) para poder reprocesarlo sin volver a
pedirlo; la bitácora registra cada búsqueda. Es reanudable: si se corta, vuelve a correrlo.

Uso:
  python scraper_sinaica_navegador.py                       # 2015-01 -> mes actual, toda la red
  python scraper_sinaica_navegador.py --desde 2016-03 --hasta 2016-04 --estaciones ACO MER
  python scraper_sinaica_navegador.py --ver                 # muestra el navegador trabajando
"""
from __future__ import annotations

import argparse
import asyncio
import calendar
import csv
import gzip
import random
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scraper_simat as ss  # noqa: E402  (parser de la respuesta de SINAICA, reglas de calidad, consolidación)

BASE = "https://sinaica.inecc.gob.mx"
PAGINA = BASE + "/data.php?tipo={tipo}"
RED = "Valle de M"                       # optgroup "Red: Valle de México" (= RAMA de la CDMX)
PARAM, NOMBRE_PARAM = "o3", "Ozono"
FUENTE = {"V": "sinaica", "C": "sinaica_prelim"}
SEL_ABIERTO = ".bootstrap-select.open, .bootstrap-select.show"


class SinOzono(Exception):
    """El formulario no ofrece ozono para esa estación."""


def mes_reciente(y: int, m: int, hoy: date, n: int) -> bool:
    return (hoy.year - y) * 12 + (hoy.month - m) < n


class Bitacora:
    def __init__(self, ruta: Path):
        self.ruta = ruta
        nueva = not ruta.exists()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(ruta, "a", newline="", encoding="utf-8")
        self.w = csv.writer(self.f)
        if nueva:
            self.w.writerow(["momento", "estacion", "mes", "tipo", "origen", "http", "filas_respuesta",
                             "valores_mes", "segundos"])

    def anotar(self, *fila):
        self.w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *fila])
        self.f.flush()


# ------------------------------------------------------------------------------------------
# Acciones sobre el formulario (las mismas que haría una persona)
# ------------------------------------------------------------------------------------------
async def abrir_formulario(contexto, tipo: str):
    pagina = await contexto.new_page()
    await pagina.goto(PAGINA.format(tipo=tipo), wait_until="networkidle", timeout=120_000)
    await pagina.wait_for_selector("button[data-id='estacion']", state="visible", timeout=60_000)
    mt = re.search(r"var\s+tipoDatos\s*=\s*\('(\w*)'\)", await pagina.content())
    tipo_pag = mt.group(1) if mt else None
    if tipo_pag != tipo:
        raise RuntimeError(f"La página ?tipo={tipo} dice tipoDatos={tipo_pag!r}: el sitio cambió")
    return pagina


async def catalogo_desde_formulario(pagina) -> pd.DataFrame:
    """Las estaciones de la red salen del propio <select> del formulario (valor = id, data-tokens trae la clave)."""
    filas = await pagina.evaluate("""(red) => [...document.querySelectorAll('#estacion optgroup')]
        .filter(g => g.label.includes(red))
        .flatMap(g => [...g.querySelectorAll('option')].map(o => ({
            station_id: o.value, station_name: o.text.trim(), tokens: o.getAttribute('data-tokens') || ''})))""", RED)
    cat = pd.DataFrame(filas)
    if cat.empty:
        raise RuntimeError("No encontré la red 'Valle de México' en el formulario: el sitio cambió")

    def clave(tokens: str) -> str | None:
        cands = [t for t in tokens.split() if re.fullmatch(r"[A-Z]{3}", t) and t not in {"CMX", "MEX", "CDMX"}]
        return cands[-1] if cands else None

    cat["station_code"] = cat["tokens"].map(clave)
    return cat.drop(columns="tokens")


async def elegir_estacion(pagina, est_id: str) -> bool:
    """Busca la estación en el buscador del formulario y la elige. Devuelve False si no mide ozono."""
    nombre = await pagina.evaluate(
        "(v) => { const o = document.querySelector(`#estacion option[value='${v}']`); return o ? o.text.trim() : null; }",
        est_id)
    if nombre is None:
        raise RuntimeError(f"La estación {est_id} ya no está en el formulario")
    actual = await pagina.evaluate("() => document.getElementById('estacion').value")
    if actual != est_id:
        await pagina.click("button[data-id='estacion']")
        buscador = pagina.locator(f"{', '.join(s + ' .bs-searchbox input' for s in SEL_ABIERTO.split(', '))}").first
        await buscador.fill(nombre)
        await pagina.wait_for_timeout(300)
        candidatos = pagina.locator(f"{', '.join(s + ' li:not(.hidden) a' for s in SEL_ABIERTO.split(', '))}")
        n = await candidatos.count()
        elegida = False
        for i in range(n):
            c = candidatos.nth(i)
            if (await c.inner_text()).strip().startswith(nombre):
                await c.click()
                if await pagina.evaluate("() => document.getElementById('estacion').value") == est_id:
                    elegida = True
                    break
                await pagina.click("button[data-id='estacion']")       # nombre repetido en otra red: reintenta
                await buscador.fill(nombre)
                await pagina.wait_for_timeout(300)
        if not elegida:
            await pagina.keyboard.press("Escape")
            raise RuntimeError(f"No pude elegir la estación {nombre} ({est_id}) en el buscador")
    # la lista de parámetros se recarga por AJAX según la estación
    try:
        await pagina.wait_for_function(
            "(p) => [...document.querySelectorAll('#parametro option')].some(o => o.value === p)", arg="O3",
            timeout=30_000)
    except Exception:
        return False
    if await pagina.evaluate("() => document.getElementById('parametro').value") != "O3":
        await pagina.click("button[data-id='parametro']")
        await pagina.locator(f"{', '.join(s + ' li a' for s in SEL_ABIERTO.split(', '))}").filter(
            has_text=NOMBRE_PARAM).first.click()
    if await pagina.locator("#rango").is_visible():                    # crudos: rango "1 mes"
        await pagina.select_option("#rango", "4")
    base = pagina.locator("#slcDatoBase")
    if await base.is_visible():                                        # validados: "Concentraciones horarias"
        await pagina.wait_for_function(
            "() => [...document.querySelectorAll('#slcDatoBase option')].some(o => o.value === '1')", timeout=30_000)
        await base.select_option("1")
    return await pagina.evaluate("() => document.getElementById('parametro').value") == "O3"


async def escribir_fecha(pagina, selector: str, valor: str) -> None:
    """Teclea la fecha en el campo (el calendario emergente a veces rellena el otro campo solo)."""
    campo = pagina.locator(selector)
    for _ in range(3):
        await campo.click()
        await pagina.keyboard.press("Control+A")
        await pagina.keyboard.type(valor)
        await pagina.keyboard.press("Escape")                        # cierra el calendario emergente
        if await campo.input_value() == valor:
            return
    await campo.evaluate("(e, v) => { e.value = v; }", valor)       # último recurso: el calendario no deja teclear


async def buscar_mes(pagina, est_id: str, y: int, m: int, tipo: str) -> tuple[int, str]:
    """Escribe la fecha, pulsa 'Actualizar' y devuelve (HTTP, HTML) de la respuesta que pinta tabla y gráfica."""
    fecha = f"{y}-{m:02d}-01"
    await escribir_fecha(pagina, "#fechaIni", fecha)
    if tipo == "V":                                                  # validados: fecha inicial y final
        rango = f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"
        await escribir_fecha(pagina, "#fechaFin", rango)
    else:                                                            # crudos: "1 mes" desde la fecha
        rango = "4"
    async with pagina.expect_response(lambda r: "datGrafs.php" in r.url and r.request.method == "POST",
                                      timeout=120_000) as info:
        await pagina.click("#actualiza")
    resp = await info.value
    enviado = resp.request.post_data or ""
    for esperado in (f"estacionId={est_id}", f"fechaIni={fecha}", "param=O3", f"rango={rango}",
                     f"tipoDatos={tipo}"):
        if esperado not in enviado:
            raise RuntimeError(f"El formulario mandó '{enviado}' (faltó {esperado})")
    return resp.status, await resp.text()


# ------------------------------------------------------------------------------------------
class Trabajador:
    """Un navegador (contexto) con dos pestañas: validados (V) y crudos (C)."""

    def __init__(self, nombre, navegador, meses_lista, a, raiz: Path, bitacora: Bitacora, estado: dict):
        self.nombre, self.navegador, self.meses_lista, self.a = nombre, navegador, meses_lista, a
        self.crudo_dir = raiz / "crudo" / "sinaica_navegador"
        self.largo_dir = raiz / "largo"
        self.bitacora, self.estado = bitacora, estado
        self.pestanas: dict = {}
        self.contexto = None

    async def pestana(self, tipo: str, est_id: str, reabrir: bool = False):
        if reabrir and tipo in self.pestanas:
            await self.pestanas.pop(tipo).close()
        if tipo not in self.pestanas:
            self.pestanas[tipo] = await abrir_formulario(self.contexto, tipo)
        if not await elegir_estacion(self.pestanas[tipo], est_id):
            raise SinOzono(est_id)
        return self.pestanas[tipo]

    async def pedir(self, tipo, est_id, codigo, y, m, preparada: dict):
        """Una búsqueda en el formulario, con reintentos. Devuelve (HTTP, HTML)."""
        st, html = None, None
        for intento in range(1, 5):
            try:
                if not preparada[tipo] or intento > 1:
                    await self.pestana(tipo, est_id, reabrir=intento > 2)
                    preparada[tipo] = True
                st, html = await buscar_mes(self.pestanas[tipo], est_id, y, m, tipo)
                if st == 200:
                    return st, html
            except SinOzono:
                raise
            except Exception as e:                       # timeout, error de red o del sitio
                st = type(e).__name__
                print(f"[{self.nombre}] {codigo} {y}-{m:02d} {tipo}: intento {intento} falló ({e!s:.120})",
                      flush=True)
            await asyncio.sleep(min(60, 3 * 2 ** intento))
        return st, None

    async def estacion(self, est: dict):
        a, estado, hoy = self.a, self.estado, date.today()
        est_id, codigo = str(est["station_id"]), ss.norm_estacion(est["station_code"])
        preparada = {"V": False, "C": False}
        t_est, n_est = time.monotonic(), 0
        for (y, m) in self.meses_lista:
            destino = self.largo_dir / f"sinaica_{PARAM}_{codigo}_{y}_{m:02d}.csv.gz"
            reciente = mes_reciente(y, m, hoy, a.refrescar_meses)
            if destino.exists() and not reciente:
                estado["meses_hechos"] += 1
                continue
            horas_mes = calendar.monthrange(y, m)[1] * 24
            partes, ya = [], set()
            for tipo in ("V", "C"):
                if tipo == "C" and (len(ya) >= 0.9 * horas_mes or (y, m) < a.crudos_desde):
                    break
                cache = self.crudo_dir / f"{PARAM}_{codigo}_{y}_{m:02d}_{tipo}.html.gz"
                t0 = time.monotonic()
                if cache.exists() and not reciente:
                    with gzip.open(cache, "rt", encoding="utf-8") as f:
                        html = f.read()
                    st, origen = 200, "cache"
                else:
                    st, html = await self.pedir(tipo, est_id, codigo, y, m, preparada)
                    estado["peticiones"] += 1
                    if html is None:
                        estado["fallos_seguidos"] += 1
                        self.bitacora.anotar(codigo, f"{y}-{m:02d}", tipo, "navegador", st, 0, 0,
                                             round(time.monotonic() - t0, 1))
                        if estado["fallos_seguidos"] >= 8:
                            raise RuntimeError("8 búsquedas seguidas sin respuesta: SINAICA no responde. "
                                               "Espera y vuelve a correr (retoma donde se quedó).")
                        continue
                    estado["fallos_seguidos"] = 0
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    with gzip.open(cache, "wt", encoding="utf-8") as f:
                        f.write(html)
                    origen = "navegador"
                    if not estado["captura"] and '"valor":0.' in html:
                        estado["captura"] = True
                        await self.pestanas[tipo].wait_for_timeout(3000)    # deja que dibuje tabla y gráfica
                        ruta = Path(a.figuras) / "captura_formulario_sinaica.png"
                        ruta.parent.mkdir(parents=True, exist_ok=True)
                        await self.pestanas[tipo].screenshot(path=str(ruta), full_page=True)
                    await asyncio.sleep(a.pausa + random.uniform(0, 0.3 * a.pausa))
                df = ss.parsear_sinaica(html, codigo, PARAM, y, m, FUENTE[tipo])
                mt = ss.RX_DAT_SINAICA.search(html or "")
                filas_resp = mt.group(1).count('"fecha"') if mt else 0
                if tipo == "C" and not df.empty:                  # solo las horas que faltan en los validados
                    df = df[[(f, h) not in ya for f, h in zip(df["fecha"], df["hora"])]]
                if not df.empty:
                    partes.append(df)
                    ya |= set(zip(df["fecha"], df["hora"]))
                self.bitacora.anotar(codigo, f"{y}-{m:02d}", tipo, origen, st, filas_resp, len(df),
                                     round(time.monotonic() - t0, 1))
            if partes:
                out = pd.concat(partes, ignore_index=True)
                ss.guardar_csv_gz(out, destino)
                n_est += len(out)
                estado["valores"] += len(out)
            estado["meses_hechos"] += 1
        print(f"[{self.nombre}] {codigo}: {n_est:,} valores nuevos ({time.monotonic() - t_est:.0f} s) | "
              f"total {estado['valores']:,} valores, {estado['peticiones']:,} búsquedas, "
              f"{estado['meses_hechos']:,}/{estado['meses_total']:,} estación-mes", flush=True)

    async def correr(self, estaciones: list[dict]):
        self.contexto = await self.navegador.new_context(user_agent=ss.USER_AGENT, locale="es-MX",
                                                         viewport={"width": 1366, "height": 900})
        try:
            for est in estaciones:
                try:
                    await self.estacion(est)
                except SinOzono:
                    print(f"[{self.nombre}] {est['station_code']}: el formulario no ofrece ozono; se omite",
                          flush=True)
                    self.estado["sin_o3"].append(est["station_code"])
        finally:
            await self.contexto.close()


async def principal(a):
    from playwright.async_api import async_playwright

    raiz = Path(a.dir)
    hoy = date.today()
    meses_lista = [(y, m) for (y, m) in ss.meses(a.desde, a.hasta) if date(y, m, 1) <= hoy]
    bitacora = Bitacora(raiz / "bitacora_sinaica_navegador.csv")
    async with async_playwright() as pw:
        navegador = await pw.chromium.launch(headless=not a.ver, slow_mo=250 if a.ver else 0)
        # robots.txt y catálogo, con el mismo navegador
        ctx = await navegador.new_context(user_agent=ss.USER_AGENT)
        pag = await ctx.new_page()
        r = await pag.goto(BASE + "/robots.txt", timeout=60_000)
        texto = await r.text() if (r is not None and r.ok) else ""
        robots = ss.Robots("" if "<html" in texto[:500].lower() else texto)
        for ruta in ("/data.php?tipo=V", "/pags/datGrafs.php"):
            if not robots.permitido(BASE + ruta):
                raise SystemExit(f"robots.txt no permite {ruta}")
        pag = await abrir_formulario(ctx, "V")
        cat = await catalogo_desde_formulario(pag)
        await ctx.close()
        respaldo = Path("datos_aire/catalogos/estaciones_sinaica.csv")
        if cat["station_code"].isna().any() and respaldo.exists():
            claves = pd.read_csv(respaldo).astype({"station_id": str}).set_index("station_id")["station_code"]
            cat["station_code"] = cat["station_code"].fillna(cat["station_id"].map(claves))
        cat = cat.dropna(subset=["station_code"])
        (raiz / "catalogos").mkdir(parents=True, exist_ok=True)
        cat.to_csv(raiz / "catalogos" / "estaciones_sinaica_formulario.csv", index=False)
        if a.estaciones:
            cat = cat[cat["station_code"].isin([e.upper() for e in a.estaciones])]
        estaciones = cat.to_dict("records")
        print(f"[sinaica-navegador] {len(estaciones)} estaciones x {len(meses_lista)} meses "
              f"({meses_lista[0][0]}-{meses_lista[0][1]:02d} -> {meses_lista[-1][0]}-{meses_lista[-1][1]:02d}), "
              f"{a.trabajadores} navegador(es) en paralelo, pausa {a.pausa} s", flush=True)
        estado = {"valores": 0, "peticiones": 0, "fallos_seguidos": 0, "captura": False, "sin_o3": [],
                  "meses_hechos": 0, "meses_total": len(estaciones) * len(meses_lista)}
        grupos = [estaciones[i::a.trabajadores] for i in range(a.trabajadores)]
        t0 = time.monotonic()
        await asyncio.gather(*(Trabajador(f"N{i + 1}", navegador, meses_lista, a, raiz, bitacora, estado).correr(g)
                               for i, g in enumerate(grupos) if g))
        await navegador.close()
    print(f"[sinaica-navegador] listo en {(time.monotonic() - t0) / 60:.1f} min: {estado['peticiones']:,} búsquedas "
          f"en el formulario, {estado['valores']:,} valores nuevos en {raiz / 'largo'}"
          + (f" | sin ozono: {', '.join(estado['sin_o3'])}" if estado["sin_o3"] else ""), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Web scraping de SINAICA con Chromium (Playwright)")
    ap.add_argument("--dir", default="datos_scraping", help="carpeta de trabajo (default: datos_scraping)")
    ap.add_argument("--desde", default="2015-01", help="AAAA-MM")
    ap.add_argument("--hasta", default=date.today().strftime("%Y-%m"), help="AAAA-MM")
    ap.add_argument("--estaciones", nargs="+", default=None, help="claves (ACO, MER, ...); default: toda la red")
    ap.add_argument("--crudos-desde", default="2024-01",
                    help="AAAA-MM: antes de esta fecha solo hay validados, no se buscan crudos")
    ap.add_argument("--pausa", type=float, default=1.0, help="segundos entre búsquedas por navegador")
    ap.add_argument("--trabajadores", type=int, default=2, help="navegadores en paralelo (no más de 3)")
    ap.add_argument("--refrescar-meses", type=int, default=2, help="vuelve a buscar los últimos N meses")
    ap.add_argument("--figuras", default="figuras")
    ap.add_argument("--ver", action="store_true", help="muestra el navegador (más lento)")
    a = ap.parse_args(argv)
    y, m = a.crudos_desde.split("-")
    a.crudos_desde = (int(y), int(m))
    a.trabajadores = max(1, min(3, a.trabajadores))
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(principal(a))


if __name__ == "__main__":
    main()
