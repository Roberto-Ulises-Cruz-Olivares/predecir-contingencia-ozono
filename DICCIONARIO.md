# Diccionario del proyecto

Qué es cada archivo, qué significa cada columna y qué quiere decir cada término. Se organiza en el orden en que se recorre el proyecto: archivos → datos raspados → tablas limpias → resultados → figuras → estaciones → glosario.

---

## 1. Archivos y carpetas

| Ruta | Qué es | Lo genera |
|---|---|---|
| `README.md` | Descripción general, cómo reproducirlo y resultados principales | — |
| `DICCIONARIO.md` | Este documento | — |
| `requirements.txt` | Paquetes de Python necesarios (con las versiones probadas) | — |
| `01_scraping_sinaica_navegador.ipynb` / `.html` | **Paso 1.** Web scraping, evidencia, limpieza, conteo de registros y validación | — |
| `02_pronostico_arima.ipynb` / `.html` | **Paso 2.** Análisis descriptivo, modelo ARIMA, prueba fuera de muestra y proyección | — |
| `03_zonas_clasificacion.ipynb` / `.html` | **Paso 3.** PCA, clusterización de estaciones y clasificación del día siguiente | — |
| `scraper_sinaica_navegador.py` | El robot: controla Chromium y llena el formulario de SINAICA | — |
| `scraper_simat.py` | Lee la respuesta de SINAICA, aplica reglas de calidad y consolida tablas (`consolidar`) | — |
| `datos_scraping/crudo/sinaica_navegador/` | Respuesta del sitio **tal cual**, una por búsqueda (`.html.gz`) | Paso 1 |
| `datos_scraping/largo/` | Lo raspado ya leído, un archivo por estación y mes (`.csv.gz`) | Paso 1 |
| `datos_scraping/salida/` | Tablas limpias que usan los análisis | Paso 1 (`consolidar`) |
| `datos_scraping/catalogos/` | Catálogo de estaciones | Paso 1 |
| `datos_scraping/bitacora_sinaica_navegador.csv` | Registro de cada búsqueda en el formulario | Paso 1 |
| `figuras/` | Gráficas PNG (sección 6) | Pasos 1-3 |
| `resultados/` | Métricas, pronósticos y resúmenes (sección 5) | Pasos 2-3 |

**Nombres de archivo en `crudo/`:** `o3_{ESTACION}_{AAAA}_{MM}_{TIPO}.html.gz`, donde `TIPO` es `V` (validado) o `C` (crudo/preliminar). Ejemplo: `o3_MER_2016_03_V.html.gz` = ozono de Merced, marzo 2016, datos validados.

**Nombres de archivo en `largo/`:** `sinaica_o3_{ESTACION}_{AAAA}_{MM}.csv.gz`.

---

## 2. Datos raspados

### 2.1 Respuesta cruda del sitio (`crudo/*.html.gz`)
Es el fragmento HTML + JavaScript que SINAICA devuelve al pulsar "Actualizar". Los datos vienen en la variable `var dat = [...]`, una entrada por hora:

| Campo | Significado | Ejemplo |
|---|---|---|
| `id` | Identificador interno de SINAICA (estación + parámetro + fecha + hora) | `240O316030400` |
| `fecha` | Día (AAAA-MM-DD) | `2016-03-04` |
| `hora` | Hora 0-23 en la convención de SINAICA (ver glosario: *hora*) | `0` |
| `valor` | Concentración en **ppm** (partes por millón); `null` = sin dato | `0.028` |
| `bandO` | Bandera del sitio (casi siempre vacía) | `null` |
| `val` | `1` = dato válido para el sitio, `0` = inválido (se descarta) | `1` |

### 2.2 Bitácora de búsquedas (`bitacora_sinaica_navegador.csv`)
Una fila por cada búsqueda (o lectura de caché).

| Columna | Significado | Ejemplo |
|---|---|---|
| `momento` | Fecha y hora en que se hizo la búsqueda | `2026-11-08 16:55:00` |
| `estacion` | Clave de la estación (sección 7) | `ACO` |
| `mes` | Mes consultado (AAAA-MM) | `2015-01` |
| `tipo` | `V` = página de datos validados, `C` = página de datos crudos | `V` |
| `origen` | `navegador` = búsqueda real en el formulario; `cache` = se leyó la respuesta guardada | `navegador` |
| `http` | Código de respuesta del servidor (`200` = correcto) o el error | `200` |
| `filas_respuesta` | Horas que devolvió el formulario (incluye horas sin dato) = **registros crudos** | `729` |
| `valores_mes` | Horas con valor que se guardaron para ese mes | `729` |
| `segundos` | Lo que tardó la búsqueda | `13.9` |

### 2.3 Datos en formato largo (`largo/*.csv.gz`)
Una fila por estación-hora con valor. Mismas columnas que la tabla limpia (sección 3.1) excepto `fecha_hora` y `flag`.

### 2.4 Catálogos (`catalogos/`)

**`estaciones.csv`** (el que usan los análisis)

| Columna | Significado |
|---|---|
| `cve_estac` | Clave de 3 letras de la estación (la del SIMAT) |
| `nom_estac` | Nombre de la estación, tal como aparece en el formulario de SINAICA |
| `id_sinaica` | Número interno de la estación en SINAICA (el `value` del menú) |
| `lat`, `lon` | Latitud y longitud (grados decimales); solo para mapas |

**`estaciones_sinaica_formulario.csv`**: lo que el robot leyó del menú de estaciones (`station_id`, `station_name`, `station_code`).

---

## 3. Tablas limpias (`datos_scraping/salida/`)

### 3.1 `o3_horario_estaciones.csv.gz`: O₃ por estación y hora (2,103,617 filas)

| Columna | Tipo | Significado | Ejemplo |
|---|---|---|---|
| `fecha_hora` | fecha-hora | **Inicio** del intervalo de una hora, hora local (UTC-6) | `2015-01-01 00:00` |
| `fecha` | fecha | Día al que pertenece la medición | `2015-01-01` |
| `hora` | entero 1-24 | Hora en convención SIMAT: `1` = promedio de 00:00 a 01:00, `24` = de 23:00 a 24:00 del mismo día | `1` |
| `estacion` | texto | Clave de la estación | `ACO` |
| `contaminante` | texto | Siempre `O3` (ozono) | `O3` |
| `valor` | número | Concentración promedio de esa hora, en ppb | `4.0` |
| `unidad` | texto | Siempre `ppb` | `ppb` |
| `fuente` | texto | `sinaica` = dato validado; `sinaica_prelim` = dato crudo/preliminar (puede cambiar) | `sinaica` |
| `flag` | texto | Resultado del control de calidad (ver abajo) | `ok` |

Valores de `flag`:
- `ok`: dato normal (2,103,468).
- `flatline`: el sensor repitió el mismo valor 6 h o más ("pegado"). Se marca, no se borra (143).
- `pico`: pico aislado en dato preliminar, excluido de los análisis (6).
- `rango`: valor > 350 ppb, excluido (0 en esta corrida).

### 3.2 `o3_horario_red.csv`: resumen de toda la red por hora (101,648 filas)

| Columna | Significado |
|---|---|
| `fecha_hora`, `fecha`, `hora` | Igual que en 3.1 |
| `n_estaciones` | Cuántas estaciones tuvieron dato esa hora |
| `maximo` | Valor más alto entre las estaciones (ppb) |
| `media` | Promedio de las estaciones (ppb) |
| `p90` | Percentil 90 de las estaciones (ppb): el valor que solo superan el 10 % de las estaciones |
| `estacion_max` | Estación que registró el máximo |

### 3.3 `o3_diario_red.csv`: resumen de la red por día (4,318 filas; la serie del ARIMA)

| Columna | Significado |
|---|---|
| `fecha` | Día |
| `maximo_red` | Máximo horario del día en toda la red (ppb). **Es la variable que se pronostica** |
| `estacion_max` | Estación donde ocurrió ese máximo |
| `hora_max` | Hora (1-24) del máximo |
| `media_red` | Promedio de los promedios diarios de cada estación (ppb) |
| `n_estaciones_validas` | Estaciones con 18 h o más de datos ese día (solo esas cuentan para el máximo). Los notebooks 2 y 3 tratan como sin dato los días con menos de 10 |
| `n_estaciones_reportando` | Estaciones con al menos un dato ese día |
| `supera_nom_90ppb` | `True` si `maximo_red` > 90 ppb (límite horario de la NOM-020) |
| `supera_fase1_155ppb` | `True` si `maximo_red` ≥ 155 ppb (nivel de Fase I de contingencia) |

### 3.4 `calidad_o3.txt`
Reporte de la limpieza: cuántos valores se leyeron, cuántos se descartaron por cada regla, completitud por estación y días sobre la norma por año.

---

## 4. Variables construidas en los análisis

### 4.1 Notebook 2 (ARIMA)

| Nombre | Significado |
|---|---|
| `y` | Máximo diario de la red (`maximo_red`), sin dato en los días con menos de 10 estaciones válidas |
| `ULTIMO` | Último día con máximo válido |
| `H_SIGNIF` | Hasta cuántos días adelante la ventaja sobre la climatología es significativa (Diebold-Mariano por horizonte) |
| `sin1`, `cos1` (y `sin2`, `cos2`) | Términos de Fourier: ondas seno y coseno con periodo de 365.25 días que modelan la temporada |
| `dia_semana` | Indicadores de día de la semana |
| `ar.L1` (φ) | Coeficiente AR(1): cuánto del "exceso" de hoy sobre lo normal se mantiene mañana |
| `sigma2` | Varianza del error del modelo |

### 4.2 Notebook 3 (zonas y clasificación)

| Nombre | Significado |
|---|---|
| Perfil de estación | 288 números por estación: su O₃ promedio en cada combinación mes (12) × hora (24) |
| `CP1`, `CP2`, `CP3` | Componentes principales (coordenadas de cada estación en el PCA) |
| `razon_madrugada_tarde` | O₃ promedio de 1 a 6 h dividido entre el de 14 a 16 h. Bajo = mucho NO local que destruye el ozono de noche |
| `zona` | Grupo de la clusterización con todos los datos (1, 2 o 3; numerados de más a menos ozono). Es la zona descriptiva de la sección 3 |
| `zona_clf` | La misma clusterización hecha solo con datos de entrenamiento (hasta 2021). Es la que alimenta al clasificador |
| `max_hoy`, `max_ayer`, `max_antier` | Máximo de la red hoy, ayer y antier (ppb) |
| `media7` | Promedio de `maximo_red` en los últimos 7 días |
| `media_red_hoy` | `media_red` de hoy |
| `hora_max_hoy` | Hora del máximo de hoy |
| `zona_1_hoy`, `zona_2_hoy`, `zona_3_hoy` | Máximo de hoy dentro de cada zona de `zona_clf`. Si una zona no tiene estaciones válidas ese día, se estima con el máximo de la red por la razón típica de esa zona en entrenamiento |
| `sin_doy`, `cos_doy` | Época del año de mañana (día del año en forma de onda) |
| `dia_0` … `dia_6` | Día de la semana de **mañana**: `dia_0` = lunes … `dia_6` = domingo |
| `y` (clasificación) | `1` si el máximo de la red de mañana es > 90 ppb, `0` si no |

---

## 5. Resultados (`resultados/`)

| Archivo | Columnas | Significado |
|---|---|---|
| `resumen.json` | Ver claves abajo | Cifras principales del ARIMA |
| *(nota)* | | En todas las tablas, **error y sesgo = observado − pronóstico** |
| `metricas_prueba.csv` | `MAE`, `RMSE`, `MASE`, `sesgo` por modelo | Error de cada modelo en la prueba (2024 →), pronóstico a 1 día |
| `pronosticos_prueba.csv` | `fecha`, `observado`, una columna por modelo, `error_est` | Pronóstico de cada día de la prueba; `error_est` = desviación estándar del error del ARIMA |
| `mae_por_horizonte.csv` | `días adelante` (1-7), MAE por modelo | Cómo crece el error al pronosticar más días adelante |
| `pronostico_proxima_semana.csv` | `fecha`, `pronóstico (ppb)`, `inf/sup 80 %`, `inf/sup 95 %`, `P(≥155)` | **Proyección** de los 7 días siguientes al último dato, con intervalos y probabilidad de Fase I |
| `zonas_estaciones.csv` | `estacion`, `grupo`, `o3_promedio`, `CP1`, `CP2`, `razon_madrugada_tarde`, `nom_estac`, `lat`, `lon`, `zona`, `zona_nombre` | Zona asignada a cada estación y sus coordenadas en el PCA (`grupo` = etiqueta interna de K-means) |
| `clasificacion_metricas_prueba.csv` | `exactitud`, `exactitud balanceada`, `precisión`, `sensibilidad (recall)`, `F1`, `AUC`, `Brier` | Desempeño de cada clasificador en la prueba |
| `clasificacion_predicciones_prueba.csv` | `fecha_hoy`, `fecha_pronostico`, `real_mayor_90`, `max_hoy`, `prob_logistica`, `prob_bosque`, `pred_elegido` | Predicción día por día; `prob_*` = probabilidad de que mañana sea > 90 ppb; `pred_elegido` = decisión del modelo elegido (1 = sí) |
| `resumen_zonas_clasificacion.json` | Ver claves abajo | Cifras principales del notebook 3 |

**Claves de `resumen.json`:** `periodo_datos`, `dias_con_dato`, `pct_dias_sobre_nom` (% de días > 90 ppb), `modelo`, `phi_ar1`, `prueba` (periodo de prueba), `MAE_modelo`, `RMSE_modelo`, `RMSE_persistencia`, `mejora_RMSE_vs_persistencia_pct`, `DM_p_vs_persistencia` (valor p de Diebold-Mariano), `cobertura_95` (fracción de días dentro del intervalo del 95 %), `ultimo_dia_con_dato`, `horizonte_significativo_vs_climatologia_dias`, `AUC_155_persistencia` (AUC usando solo el máximo de ayer), `mejora_horaria_vs_ingenuo_pct` (SARIMA horario, promedio de los años de prueba), `eventos_155_prueba`, `AUC_155`, `BSS_155`.

**Claves de `resumen_zonas_clasificacion.json`:** `estaciones_pca`, `varianza_cp1`, `varianza_cp2`, `componentes_90pct`, `k_zonas`, `silueta`, `rand_kmeans_vs_ward`, `rand_minimo_al_quitar_un_anio` (estabilidad de las zonas), `particion`, `prueba_dias`, `modelo_clasificacion`, `umbral` (elegido en validación), `prueba_*` (métricas del modelo elegido con su umbral), `solo_max_hoy_*` (logística con solo el máximo de hoy), `ganancia_AUC_vs_solo_max_hoy` y su `ganancia_AUC_IC95` (bootstrap por bloques), `persistencia_*` y `tasa_base_Brier`.

---

## 6. Figuras (`figuras/`)

| Archivo | Qué muestra | Notebook |
|---|---|---|
| `captura_formulario_sinaica.png` | Lo que vio Chromium: formulario lleno y gráfica dibujada por el sitio (evidencia del scraping) | 1 |
| `01_serie_diaria.png` | Máximo diario de la red, 2015 → hoy, con la NOM y el nivel de Fase I | 2 |
| `02_hora_mes.png` | Mapa de calor del O₃ por hora del día y mes | 2 |
| `03_dias_fase1_por_anio.png` | Días por año con máximo ≥ 155 ppb | 2 |
| `04_estaciones_maximo.png` | Qué estaciones registran con más frecuencia el máximo de la red | 2 |
| `05_acf_pacf.png` | Autocorrelación (ACF) y autocorrelación parcial (PACF) para identificar el ARIMA | 2 |
| `06_diagnostico_residuos.png` | Residuos del modelo: serie, histograma, QQ-plot y ACF | 2 |
| `07_prueba_temporada.png` | Una temporada de la prueba: pronóstico a 1 día contra lo observado, con intervalos, y la probabilidad pronosticada de llegar a 155 ppb | 2 |
| `08_mae_horizonte.png` | Error según cuántos días adelante se pronostica | 2 |
| `09_sarima_horario.png` | Pronóstico horario de 24 h con SARIMA | 2 |
| `10_pca_varianza.png` | Varianza explicada por cada componente principal | 3 |
| `11_pca_cargas.png` | Qué meses y horas pesan en CP1 y CP2 (rojo = positivo, azul = negativo) | 3 |
| `12_zonas_pca_mapa.png` | Estaciones en el plano del PCA y en el mapa, coloreadas por zona | 3 |
| `13_perfil_zonas.png` | Ciclo diario y anual promedio de cada zona | 3 |
| `14_clasificacion_roc_confusion.png` | Curva ROC y matriz de confusión del clasificador | 3 |
| `15_clasificacion_importancia.png` | Qué variables usa más el clasificador | 3 |

---

## 7. Estaciones con datos de ozono (26)

| Clave | Nombre | Zona (notebook 3) | O₃ promedio (ppb) |
|---|---|---|---|
| AJM | Ajusco Medio | 1 · montaña sur-poniente | 41.5 |
| CUA | Cuajimalpa | 1 · montaña sur-poniente | 35.1 |
| BJU | Benito Juárez | 2 · centro, sur y oriente | 31.3 |
| CCA | Centro de Ciencias de la Atmósfera (UNAM) | 2 · centro, sur y oriente | 33.3 |
| FAR | FES Aragón | 2 · centro, sur y oriente | 31.0 |
| GAM | Gustavo A. Madero | 2 · centro, sur y oriente | 29.4 |
| HGM | Hospital General de México | 2 · centro, sur y oriente | 30.0 |
| IZT | Iztacalco | 2 · centro, sur y oriente | 28.4 |
| MGH | Miguel Hidalgo | 2 · centro, sur y oriente | 28.9 |
| NEZ | Nezahualcóyotl | 2 · centro, sur y oriente | 30.3 |
| PED | Pedregal | 2 · centro, sur y oriente | 34.7 |
| SAC | Santiago Acahualtepec | 2 · centro, sur y oriente | 32.1 |
| TAH | Tláhuac | 2 · centro, sur y oriente | 33.7 |
| UAX | UAM Xochimilco | 2 · centro, sur y oriente | 32.9 |
| UIZ | UAM Iztapalapa | 2 · centro, sur y oriente | 29.5 |
| ACO | Acolman | 3 · norte | 28.0 |
| ATI | Atizapán | 3 · norte | 27.8 |
| CUT | Cuautitlán | 3 · norte | 27.1 |
| FAC | FES Acatlán | 3 · norte | 29.3 |
| LLA | Los Laureles | 3 · norte | 26.4 |
| LPR | La Presa | 3 · norte | 26.3 |
| MER | Merced | 3 · norte | 26.2 |
| SAG | San Agustín | 3 · norte | 26.4 |
| TLI | Tultitlán | 3 · norte | 27.4 |
| VIF | Villa de las Flores | 3 · norte | 28.4 |
| XAL | Xalostoc | 3 · norte | 24.5 |

**Sin datos de ozono en el formulario de SINAICA:** CAM (Camarones), CHO (Chalco), LOM (Lomas), NTS (Netzahualcóyotl Sur), SFE (Santa Fe), TLA (Tlalnepantla): el sitio no les ofrece ozono. AJU (Ajusco), COY (Coyoacán), INN (ININ), MPA (Milpa Alta), MON (Montecillo), MCM (Museo de la Ciudad de México), PLA (Plateros), SJA (San Juan de Aragón): ya no aparecen en el formulario.

---

## 8. Glosario

### Calidad del aire
| Término | Significado |
|---|---|
| **O₃ (ozono)** | Contaminante secundario: no se emite directo, se forma con la luz del sol a partir de NOx y compuestos orgánicos volátiles (COV). Por eso su pico es a media tarde y en la temporada seca-caliente |
| **NO / NOx** | Óxidos de nitrógeno (principalmente del tráfico). De noche el NO reacciona con el ozono y lo destruye ("titulación") |
| **ppb / ppm** | Partes por mil millones / partes por millón. 1 ppm = 1,000 ppb. SINAICA reporta ppm; el proyecto usa ppb |
| **NOM-020-SSA1-2021** | Norma mexicana de salud para ozono. Límite horario: 0.090 ppm = **90 ppb** |
| **Fase I** | Nivel de activación de contingencia ambiental por ozono: **155 ppb** en una hora (CAMe) |
| **RAMA** | Red Automática de Monitoreo Atmosférico de la Ciudad de México (red "Valle de México" en SINAICA) |
| **SIMAT** | Sistema de Monitoreo Atmosférico de la CDMX (opera la RAMA) |
| **SINAICA / INECC** | Sistema Nacional de Información de la Calidad del Aire, del Instituto Nacional de Ecología y Cambio Climático. **De aquí se raspan los datos** |
| **Dato validado / crudo** | Validado: revisado por la red, definitivo. Crudo (preliminar): tal como lo midió el equipo, puede corregirse después |
| **Hora (convención)** | En el SIMAT la hora `h` (1-24) es el promedio del intervalo (h−1, h]. En SINAICA la hora validada `h` (0-23) equivale a la hora `h+1` del SIMAT; la cruda va una hora adelante |
| **Máximo de la red** | El valor horario más alto entre todas las estaciones en un día |
| **Estación-hora / estación-día** | Unidad de registro: una medición de una estación en una hora / un día |

### Web scraping
| Término | Significado |
|---|---|
| **Web scraping** | Extraer datos de un sitio web de forma automática |
| **Playwright / Chromium** | Biblioteca para controlar un navegador real desde Python / el navegador que controla |
| **Headless** | Navegador sin ventana visible |
| **Página dinámica** | Página cuyo contenido arma el navegador con JavaScript; no basta con descargar el HTML |
| **AJAX** | Petición que la página hace en segundo plano para cargar datos sin recargar (aquí, la lista de contaminantes) |
| **bootstrap-select** | Menú desplegable con buscador hecho en JavaScript (el de estaciones) |
| **robots.txt** | Archivo donde el sitio indica qué se puede raspar; el robot lo respeta |
| **Caché** | Copia guardada de cada respuesta para no volver a pedirla |
| **HTTP 200 / 500** | Código del servidor: 200 = correcto; 500 = error interno del servidor |
| **Registro crudo / limpio** | Crudo: fila estación-hora tal como la devolvió el formulario (incluye horas sin dato). Limpio: estación-hora con valor válido después del control de calidad |

### Series de tiempo (notebook 2)
| Término | Significado |
|---|---|
| **ARIMA(p, d, q)** | Modelo con p rezagos autorregresivos, d diferencias y q términos de media móvil |
| **SARIMA(p,d,q)(P,D,Q)ₛ** | ARIMA con una parte estacional de periodo s (7 = semanal, 24 = diario) |
| **Regresión armónica / Fourier** | Modelar la temporada con ondas seno y coseno en lugar de un SARIMA de periodo 365 |
| **AR(1), φ** | El valor de hoy depende del de ayer; φ mide cuánto (0 = nada, 1 = todo) |
| **Estacionaria** | Serie con media y varianza estables en el tiempo |
| **ADF / KPSS** | Pruebas de estacionariedad con hipótesis opuestas (ADF: H₀ = no estacionaria; KPSS: H₀ = estacionaria) |
| **ACF / PACF** | Autocorrelación simple y parcial; sirven para elegir el orden del ARIMA |
| **AIC** | Criterio de información de Akaike: balance entre ajuste y número de parámetros (menor es mejor) |
| **Ljung-Box** | Prueba de si los residuos son ruido blanco (sin estructura pendiente) |
| **Jarque-Bera** | Prueba de normalidad de los residuos |
| **Persistencia** | Pronóstico ingenuo "mañana igual que hoy" |
| **Climatología** | Pronóstico ingenuo "lo normal para esa época del año" |
| **Ingenuo semanal** | Pronóstico ingenuo "igual que hace 7 días" |
| **MAE / RMSE** | Error absoluto medio / raíz del error cuadrático medio (ppb; menor es mejor; RMSE castiga más los errores grandes) |
| **MASE** | MAE dividido entre el MAE de la persistencia (< 1 = mejor que la persistencia) |
| **Sesgo** | Promedio de (observado − pronóstico): positivo = el modelo se queda corto (subestima); negativo = se pasa |
| **Diebold-Mariano (DM)** | Prueba de si dos pronósticos tienen errores significativamente distintos |
| **Intervalo de predicción / cobertura** | Rango donde debería caer el valor real con cierta probabilidad; cobertura = qué fracción realmente cayó dentro |
| **Horizonte** | Cuántos días adelante se pronostica |
| **Proyección** | Pronóstico hacia fechas futuras sin datos (aquí, los 7 días siguientes) |

### Reducción dimensional, clusterización y clasificación (notebook 3)
| Término | Significado |
|---|---|
| **PCA (análisis de componentes principales)** | Resume muchas variables correlacionadas en pocas combinaciones (componentes) que conservan la mayor varianza |
| **Componente principal (CP)** | Una de esas combinaciones; CP1 es la que más varianza explica |
| **Carga (loading)** | Peso de cada variable original en un componente |
| **Varianza explicada** | Qué porcentaje de las diferencias entre estaciones captura cada componente |
| **K-means** | Algoritmo que agrupa observaciones en k grupos minimizando la distancia a su centro |
| **Ward (jerárquica)** | Otro método de agrupación que une grupos paso a paso; se usa para verificar estabilidad |
| **Silueta** | Qué tan bien está cada elemento en su grupo frente al grupo vecino (−1 a 1; más alto es mejor) |
| **Inercia** | Suma de distancias al cuadrado de cada estación a su centro (baja siempre al aumentar k) |
| **Índice de Rand ajustado** | Coincidencia entre dos agrupaciones (1 = idénticas, 0 = como al azar) |
| **Regresión logística** | Modelo que da la probabilidad de un evento sí/no a partir de una combinación lineal de variables |
| **C (regularización)** | En la logística, controla cuánto se castigan los coeficientes grandes (C pequeño = más castigo) |
| **Bosque aleatorio** | Conjunto de muchos árboles de decisión; `hoja≥n` = mínimo de días en cada hoja |
| **Partición temporal** | Entrenar con el pasado y evaluar con el futuro. En los notebooks 2 y 3: entrenamiento 2015-2021, validación 2022-2023, prueba 2024 → |
| **Umbral** | Probabilidad a partir de la cual se dice "sí". Aquí 0.5 y 0.56 (el que maximiza la exactitud balanceada en validación) |
| **Tasa base** | Referencia que siempre da la misma probabilidad: la proporción histórica de días altos |
| **Bootstrap por bloques** | Remuestrear la prueba en bloques de días seguidos para obtener un intervalo de confianza sin romper la dependencia entre días vecinos |
| **Matriz de confusión** | Tabla de aciertos y errores: verdaderos/falsos positivos y negativos |
| **Exactitud (accuracy)** | Fracción de días bien clasificados |
| **Exactitud balanceada** | Promedio de los aciertos en días altos y en días bajos (no se infla cuando una clase domina) |
| **Precisión** | De los días que el modelo dijo "sí", cuántos lo fueron |
| **Sensibilidad (recall)** | De los días que fueron "sí", cuántos detectó el modelo |
| **F1** | Media armónica de precisión y sensibilidad |
| **AUC (curva ROC)** | Probabilidad de que el modelo asigne más riesgo a un día alto que a uno bajo (0.5 = azar, 1 = perfecto) |
| **Brier** | Error cuadrático de las probabilidades (0 = perfecto; menor es mejor) |
| **BSS (Brier skill score)** | Mejora del Brier frente a una referencia (0 = igual, 1 = perfecto) |
| **Importancia por permutación** | Cuánto empeora el AUC si se desordena una variable: mide cuánto la usa el modelo |
