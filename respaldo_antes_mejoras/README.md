# Ozono en la CDMX: web scraping de SINAICA + pronóstico ARIMA

**Pregunta:** ¿se puede anticipar con un día de ventaja el máximo de ozono de la red y el riesgo de llegar al nivel de contingencia (155 ppb)?

**Recolección:** todos los datos que se analizan se obtienen por **web scraping**. Un navegador Chromium, controlado con Playwright, llena el formulario de consulta de SINAICA (INECC) igual que una persona, estación por estación y mes por mes. No se usan descargas de archivos ni APIs.

## Rubros que cubre

| Rubro | Pts | Dónde |
|---|---|---|
| Resultados de búsqueda | 3 | Notebook 1: cada dato sale de llenar y enviar el formulario de búsqueda de SINAICA |
| Página dinámica (Selenium/Playwright) | 2 | Notebook 1: Chromium con Playwright; buscador, menús AJAX, calendario y datos dentro de JavaScript |
| ARIMA | 1.5 | Notebook 2: regresión armónica + AR(1), SARIMA semanal y SARIMA horario |
| Proyección | 1 | Notebook 2: pronóstico de los 7 días siguientes con intervalos y probabilidad de contingencia |
| Reducción dimensional | 1 | Notebook 3: PCA de los perfiles mes × hora de cada estación |
| Clusterización | 1.5 | Notebook 3: K-means (verificado con Ward) → 3 zonas de ozono |
| Clasificación | 1 | Notebook 3: regresión logística y bosque aleatorio para "¿mañana > 90 ppb?" |
| Visualización | 1 | 16 figuras en `figuras/` |
| **Total** | **12** | |

**Tamaño de los datos:** 2,502,020 registros crudos (estación-hora devueltas por el formulario) y 2,103,617 después de la limpieza (requisito: ≥ 5,500 y ≥ 5,000).

## Qué incluye

| Archivo | Qué es |
|---|---|
| `DICCIONARIO.md` | **Qué significa cada archivo, columna, variable y término** del proyecto |
| `01_scraping_sinaica_navegador.ipynb` | **Paso 1.** Web scraping con Chromium (Playwright), evidencia (captura, respuesta cruda, bitácora), limpieza, conteo de registros y validación |
| `02_pronostico_arima.ipynb` | **Paso 2.** Descriptivo → Box-Jenkins → prueba fuera de muestra → proyección de la próxima semana |
| `03_zonas_clasificacion.ipynb` | **Paso 3.** PCA → clusterización de estaciones en zonas → clasificación del día siguiente |
| `*.html` | Los tres notebooks ya ejecutados, para leer sin Jupyter |
| `scraper_sinaica_navegador.py` | El robot que llena el formulario de SINAICA (lo usa el notebook 1; también corre por línea de comandos) |
| `scraper_simat.py` | Lectura de la respuesta de SINAICA, reglas de calidad y consolidación |
| `requirements.txt` | Paquetes de Python con las versiones probadas |
| `datos_scraping/crudo/sinaica_navegador/` | Respuesta del sitio tal cual, una por búsqueda (4,262 `.html.gz`): permite reprocesar sin volver a raspar |
| `datos_scraping/largo/` | Lo raspado ya leído, un archivo por estación y mes |
| `datos_scraping/bitacora_sinaica_navegador.csv` | Una fila por búsqueda: estación, mes, tipo, HTTP, filas devueltas, segundos |
| `datos_scraping/catalogos/estaciones.csv` | Estaciones: clave, nombre, id de SINAICA y coordenadas |
| `datos_scraping/salida/o3_horario_estaciones.csv.gz` | **2,103,617 filas**: O₃ horario por estación, 2015-01-01 → 2026-11-09, 26 estaciones |
| `datos_scraping/salida/o3_horario_red.csv` | **101,648 filas**: una por hora (máximo, media, p90 y número de estaciones). Cabe en Excel y Looker Studio |
| `datos_scraping/salida/o3_diario_red.csv` | 4,318 filas: máximo diario de la red (la serie que se modela) |
| `datos_scraping/salida/calidad_o3.txt` | Reporte de calidad de la limpieza |
| `figuras/` | 15 gráficas del análisis y la captura del formulario de SINAICA |
| `resultados/` | Métricas, pronósticos, proyección, zonas y clasificación |

La validación contra los archivos oficiales del SIMAT (sección "Datos de esta entrega") se hizo con una carpeta `datos_aire/` que **no se incluye**: no forma parte de los datos del proyecto. Su resultado queda en la salida ya ejecutada del notebook 1; si se corre sin esa carpeta, ese paso se omite solo.

## Cómo funciona el scraping

Sitio: https://sinaica.inecc.gob.mx/data.php?tipo=V (datos validados) y `?tipo=C` (datos crudos).

Por cada estación y cada mes, el robot:
1. Escribe el nombre de la estación en el **buscador** del menú desplegable y la elige.
2. Espera a que el sitio cargue por AJAX los contaminantes de esa estación y elige **Ozono**.
3. Elige *Concentraciones horarias* y teclea la **fecha de inicio y fin** del mes.
4. Pulsa **Actualizar** y captura la respuesta que el sitio usa para dibujar la tabla y la gráfica.
5. Si los validados no cubren el 90 % del mes (meses recientes), repite la búsqueda en la página de **crudos**.

**Por qué hace falta un navegador:** la respuesta no trae una tabla HTML. Los datos vienen dentro de JavaScript (`var dat = [...]`), y la tabla y la gráfica las construye el navegador. Los menús son componentes de JavaScript, la lista de contaminantes llega por AJAX y las fechas usan un calendario emergente.

**Buenas prácticas:** respeta `robots.txt`, usa 1 s de pausa por navegador y como máximo 3 navegadores en paralelo, reintenta con espera exponencial y se detiene si el sitio deja de responder. Guarda caché de cada respuesta y se reanuda si se corta. Además verifica que lo que manda el formulario sea exactamente la estación, fecha y contaminante pedidos.

## Cómo reproducirlo

Python 3.10+, con toda la carpeta del proyecto.

```bash
pip install -r requirements.txt
```

```bash
python -m playwright install chromium
```

1. Abre `01_scraping_sinaica_navegador.ipynb` y ejecuta todo. Como `datos_scraping/` ya trae lo raspado, solo vuelve a buscar los 2 meses más recientes (unos minutos). Si borras `datos_scraping/`, raspa todo de nuevo (2015 → hoy, ~4,300 búsquedas, ~1.5 h); si se corta, vuelve a ejecutarlo y sigue donde se quedó. Con `VER_NAVEGADOR = True` se ve al robot llenar el formulario.
2. Abre `02_pronostico_arima.ipynb` y ejecuta todo.
3. Abre `03_zonas_clasificacion.ipynb` y ejecuta todo.

Los notebooks 2 y 3 leen `datos_scraping/salida/` y se ejecutan desde la carpeta del proyecto.

Por línea de comandos:

```bash
python scraper_sinaica_navegador.py --estaciones MER BJU --desde 2024-01   # prueba rápida
```

```bash
python scraper_sinaica_navegador.py                                         # todo: 2015-01 -> hoy
```

```bash
python scraper_simat.py consolidar --contaminante o3 --dir datos_scraping
```

## Datos de esta entrega (noviembre de 2026)

| | Registros |
|---|---|
| Búsquedas hechas en el formulario | 4,623 (todas HTTP 200) |
| **Crudos:** filas estación-hora devueltas por el formulario | **2,502,020** |
| **Limpios:** estación-hora válidas después de la limpieza | **2,103,617** |
| Serie diaria de la red | 4,318 días |

- **Validación:** lo raspado coincide en el **99.1 %** de 1.84 millones de horas con los archivos oficiales del SIMAT (diferencia media de 0.1 ppb).
- **Limpieza:** se descartan horas sin dato y negativos; los validados ganan sobre los crudos; se excluyen valores > 350 ppb y picos aislados en datos crudos (6); se marcan sensores "pegados" (143 valores); un estación-día cuenta con 18 h o más y un día de red con 10 estaciones o más.
- **Datos preliminares:** desde octubre de 2024 parte de los datos vienen de la versión cruda de SINAICA (148 mil valores, `fuente = sinaica_prelim`). Pueden cambiar cuando se validen.
- **Convención de hora:** en SINAICA, la hora h validada es la hora h+1 del SIMAT, y los crudos van una hora adelante de los validados. Se verificó cruzando fuentes.
- **Estaciones:** el formulario publica 32 estaciones de la red *Valle de México*. Para 6 de ellas (CAM, CHO, LOM, NTS, SFE, TLA) el sitio no ofrece ozono, y 8 estaciones históricas (AJU, COY, INN, MPA, MON, MCM, PLA, SJA) ya no aparecen. Quedan **26 estaciones con ozono** (lista completa en `DICCIONARIO.md`).

## Diccionario de datos

**`o3_horario_estaciones.csv.gz`**: `fecha_hora` (inicio del intervalo, UTC-6) · `fecha` · `hora` (1-24, convención SIMAT) · `estacion` · `contaminante` · `valor` (ppb) · `unidad` · `fuente` (`sinaica` = validado | `sinaica_prelim` = crudo) · `flag` (`ok` | `flatline` | `rango` | `pico`)

**`o3_horario_red.csv`**: `fecha_hora` · `fecha` · `hora` · `n_estaciones` · `maximo` · `media` · `p90` · `estacion_max`

**`o3_diario_red.csv`**: `fecha` · `maximo_red` · `estacion_max` · `hora_max` · `media_red` · `n_estaciones_validas` (≥ 18 h) · `n_estaciones_reportando` · `supera_nom_90ppb` · `supera_fase1_155ppb`

## Resultados (datos 2015 → 9 de noviembre de 2026)

**Descriptivo**
- El máximo de la red rebasa el límite horario de la NOM (90 ppb) en **65 % de los días**.
- Hubo días con máximo ≥ 155 ppb (nivel de Fase I) en todos los años: desde 1 (2020) hasta 16 (2016 y 2024). En 2026 van 9.
- El pico típico es en **mayo a las 14 h** (118 ppb en promedio).
- Cinco estaciones del sur (Ajusco Medio, CCA-UNAM, UAM Xochimilco, Pedregal y Tláhuac) concentran el 51 % de los máximos diarios. Ajusco Medio encabeza con 15 % de los días.

**Modelo**
- **Especificación:** regresión armónica (K=2) + día de la semana con errores **AR(1)**, $\phi = 0.51$. Se eligió por menor RMSE a 1 día en dos años de validación (2022-2023), sin regla de empate.
- **Pruebas de estacionariedad:** ADF/KPSS indican $d = 0$.
- **Residuos:** Ljung-Box no rechaza ruido blanco (rezagos 7, 14 y 28).

**Prueba fuera de muestra (2024-01-01 → 2026-10-21, 1,005 días, pronóstico a 1 día)**

| Modelo | MAE | RMSE | vs modelo (Diebold-Mariano) |
|---|---|---|---|
| **Armónica K=2 + día sem. + AR(1)** | **17.5** | **22.0** | — |
| SARIMA(1,1,1)(1,0,1)₇ "de libro" | 17.6 | 22.3 | p = 0.04 |
| Persistencia (ayer) | 19.3 | 24.9 | p < 0.001 |
| Climatología (media por día del año) | 21.7 | 27.0 | p < 0.001 |
| Ingenuo semanal | 27.7 | 34.8 | p < 0.001 |

- **Precisión:** RMSE 12 % menor que la persistencia. El intervalo del 95 % cubre 94.1 % de los días y el del 80 %, 80.1 %.
- **Horizonte útil de 1-2 días; ventaja significativa hasta 4.** Frente a la climatología por día del año, la ventaja pasa de 4.2 ppb a 1 día a 0.8 a 3 días y 0.5 a 4 (Diebold-Mariano p = 0.03); del día 5 en adelante no es distinguible de cero.
- **Alerta ≥ 155 ppb:** AUC 0.91 y BSS 0.12. Con un corte de 10 % detecta 14 de 30 días, pero 4 de cada 5 alertas son falsas, y subestima un poco la frecuencia (28 eventos esperados contra 30 observados). La persistencia sola ya alcanza AUC 0.90: la discriminación viene sobre todo de cuánto ozono hubo hoy. Sirve como **semáforo de riesgo**, no como alarma.
- **Horario:** SARIMA(1,0,1)(0,1,1)₂₄ pronostica las siguientes 24 h con 15 % menos error que "igual que ayer".

**Zonas (PCA + K-means, notebook 3)**
- Tres componentes resumen el 91 % de las diferencias entre estaciones. CP1 (66 %) es cuánto ozono tiene la estación; CP2 (18 %) qué tan marcado es el pico de la tarde frente a la noche.
- k = 3 es la única agrupación estable: K-means y Ward forman exactamente los mismos grupos (Rand ajustado = 1.00; silueta 0.40).
  - **Zona 1, montaña sur-poniente** (Ajusco Medio, Cuajimalpa): la que más ozono tiene (38 ppb de promedio) y la que más conserva de noche.
  - **Zona 2, centro, sur y oriente** (13 estaciones): pico de tarde marcado (74 ppb a las 15 h) y la mayor frecuencia de días con alguna estación sobre la NOM (61 %).
  - **Zona 3, norte** (11 estaciones, más Merced): la de menos ozono (27 ppb) y la menor frecuencia de días sobre la NOM (43 %).
- Las zonas salen solo del ozono, sin coordenadas, y aun así quedan geográficamente compactas.

**Clasificación "¿mañana > 90 ppb?" (prueba 2024 →, 1,007 días)**

| Modelo | Exactitud | Exactitud balanceada | Precisión | Sensibilidad | AUC | Brier |
|---|---|---|---|---|---|---|
| **Logística, umbral 0.64** | 0.75 | **0.73** | **0.86** | 0.77 | **0.81** | **0.159** |
| Logística, umbral 0.5 | 0.76 | 0.68 | 0.79 | 0.89 | 0.81 | 0.159 |
| Bosque aleatorio | 0.76 | 0.67 | 0.79 | 0.89 | 0.80 | 0.161 |
| Persistencia (mañana = hoy) | 0.75 | 0.70 | 0.82 | 0.82 | — | 0.253 |

- Las probabilidades de la logística son 37 % más precisas que la persistencia (Brier). Con umbral igual a la proporción de días altos (0.64) también le gana en exactitud balanceada y precisión.
- El bosque aleatorio no mejora a la logística: la relación es casi lineal.
- El techo (AUC ≈ 0.81) coincide con el del ARIMA para el mismo evento: sin meteorología no se puede anticipar mucho mejor.

## Errores comunes que el pipeline evita

1. **Hora 24.** En el SIMAT, la hora 24 es el intervalo 23:00-24:00 del mismo día. Un `pd.to_datetime` ingenuo falla o corre el dato al día siguiente.
2. **Horas de SINAICA desfasadas.** La hora validada y la cruda no usan la misma convención; sin corregirlo, los crudos quedan corridos una hora.
3. **Mezclar validados y crudos.** Al consolidar gana la versión validada y se reporta el cruce.
4. **Composición de la red.** El máximo depende de cuántas estaciones operan. Se exige 18 h por estación-día y 10 estaciones o más por día.
5. **SARIMA con s = 365.** Es inviable. La estacionalidad anual va con términos de Fourier como regresores.
6. **Sobrediferenciar.** Se diferencia solo si ADF/KPSS lo piden. Un MA(1) cercano a −1 es la señal de que sobró la diferencia.
7. **Evaluar dentro de muestra o con partición aleatoria.** La partición es temporal (entrenamiento → validación → prueba), los parámetros quedan fijos en la prueba y siempre se compara contra referencias ingenuas.
8. **Interpolar huecos antes de ajustar.** SARIMAX los maneja con el filtro de Kalman; interpolar subestima la incertidumbre.
9. **Elegir con un solo año de validación o con reglas de empate.** Con solo 2023, el orden de los candidatos salía al revés que en la prueba, y la regla "empate dentro de 0.5 % → menos parámetros" elegía un modelo peor. Se usan dos años (2022-2023) y el menor RMSE, sin más.
10. **Compararse contra una climatología débil.** La parte armónica del propio modelo es una referencia floja; la media histórica por día del año es más exigente y no infla la ventaja.

## Limitaciones y siguientes pasos

- **Sin meteorología.** El modelo no ve temperatura, radiación ni viento, que es donde está la varianza que falta. Siguiente paso: SARIMAX con temperatura máxima y radiación pronosticadas como regresores.
- **Sesgo estacional y creciente.** Subestima mayo (≈ +9 ppb) y sobreestima junio (≈ −8 ppb). Además el error medio en la prueba crece: +1.3 ppb en 2024, +2.0 en 2025 y +3.1 en 2026, coincidiendo con la entrada de datos preliminares. Conviene reestimar cuando se publiquen los validados.
- **El perfil horario del día anterior ayuda.** En una regresión con rezagos, el ozono de 20 a 24 h y el percentil 90 entre estaciones mejoran el pronóstico a 1 día en ~4 %; es el camino más barato para ganar precisión.
- **Cobertura de estaciones.** El formulario de SINAICA no ofrece ozono para 14 estaciones históricas de la red (entre ellas Milpa Alta y Ajusco).
- **Calibración de extremos.** Para alertas, recalibrar la probabilidad, por ejemplo con regresión logística.
- **Granularidad y frescura.** Hacer modelos por zona y reestimarlos cada mes.

## Fuentes

- SINAICA, Sistema Nacional de Información de la Calidad del Aire (INECC): consulta de datos validados y crudos (sinaica.inecc.gob.mx).
- SIMAT, Ciudad de México: archivos anuales de datos abiertos y bases RAMA (aire.cdmx.gob.mx), usados solo para validar.
- Umbral de Fase I, 155 ppb en 1 h (CAMe, 2026).
- Límite horario de O₃ de 0.090 ppm (NOM-020-SSA1-2021).
