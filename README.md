# Escalera de niveles del NQ y del ES

Herramienta que, a partir del open interest de las opciones del **E-mini Nasdaq 100 (NQ)** y
del **E-mini S&P 500 (ES)** publicado por CME, genera cada día una "escalera de niveles" para
cada índice: resistencias, frontera (gamma flip), zonas de aceleración, imán y muros de
calls/puts, con su probabilidad de toque y un gráfico de la gamma de la 0DTE.

Los dos índices usan el **mismo código** (mismas reglas, categorías, formato ESC2, página,
histórico y pruebas); lo propio de cada uno (secciones del boletín, etiquetas, parámetros
escalados) está en `config.yaml` → `indices`.

**Nunca envía órdenes ni se conecta a cuentas de trading.** Solo lee archivos.

## Estado del proyecto

| Fase | Estado |
|---|---|
| Lectura de datos (PDF de CME y CSV manual) | ✅ |
| Cálculos (Black-76, GEX, gamma flip, movimiento esperado, niveles) | ✅ |
| Página HTML + histórico diario | ✅ |
| "Estado hoy" automático (máximo/mínimo del boletín + velas opcionales) | ✅ |
| Automático con launchd (vigilante de `datos/entrada/` y 23:00) | ✅ |
| Indicador de TradingView | ✅ |
| Mejoras 26-oct-2026, Fase 1: puts trimestrales identificadas por la secuencia del boletín, GEX por nivel y desglose por vencimiento | ✅ |
| Mejoras 26-oct-2026, Fase 2: jerarquía por GEX bruta estructural, fusión, zonas, 0DTE aparte, formato ESC2 | ✅ |
| Fase 3: vencimiento de la gamma por nivel y estado en vivo en el indicador (3a, 3b); seguimiento con velas de 1 minuto (3c, aparcada) | pendiente (tras probar el indicador en vivo) |
| ES (E-mini S&P 500): fases ES-0 a ES-2 (lector de las secciones 47 y 48, cálculo, página, indicador, vigilante) | ✅ |
| Estrategia ES de 2 min (Pine) y motor de backtest en Python | ✅ (backtest con datos reales pendiente de las velas) |
| Escalera fiable (F-1 a F-4): estudio de fiabilidad, clasificación por régimen, indicador en vivo, tarea mensual | ✅ (12-nov-2026) |
| Oro (GC): ORO-0 y ORO-1 (lector de la sección 64 y motor) | ✅ · ORO-2 (página, vigilante, indicador) pendiente |
| Otras: Telegram, NDX, Databento, Interactive Brokers | pendiente |

## Sobre los datos (léelo)

- La fuente es el **Daily Bulletin de CME** (PDF): **sección 40** para el NQ
  (`Section40_Nasdaq_100_And_E_Mini_Nasdaq_100_Options.pdf`) y **secciones 47 y 48** para el
  ES (`Section47_E_Mini_S_And_P_500_Call_Options.pdf` con las calls y
  `Section48_E_Mini_S_And_P_500_Put_Options.pdf` con las puts). Las condiciones de uso de CME
  **prohíben descargarlos con programas**: tienes que descargarlos tú a mano desde
  <https://www.cmegroup.com/market-data/daily-bulletin.html> y guardarlos en
  `datos/entrada/`. La herramienta usa el PDF más reciente de cada sección; el 47 y el 48
  tienen que ser del mismo boletín (lo comprueba leyendo la fecha dentro del PDF).
- Para que sea 100 % automático hará falta una fuente con licencia (Databento o
  Interactive Brokers con datos de CME). El código está preparado para enchufarla.
- El PDF solo da el mes de cada vencimiento. La fecha exacta sale de la cabecera del
  boletín cuando la trae (p. ej. la trimestral de junio de 2027 vence el jueves 17 por
  festivo) y, si no, se deduce del producto (W1 = primer viernes, QTW = martes, etc.).
- NQ: se descartan las Micro (MN1–MN5) y las opciones del Russell que vienen en la misma
  sección. ES: se descartan las del Russell (RTM EOM) y el producto sin identificar
  «SME OPT» (la página del ES enseña su open interest; no entra en ningún cálculo).
- ES: las diarias de un mes salen en un solo bloque con varias semanas (el mismo strike una
  vez por semana). Los strikes que tienen todas las semanas se fechan por precio (a igual
  strike, más plazo vale más); el resto queda sin fecha segura y la página dice cuántos
  contratos son.

## Instalación (una sola vez)

Necesitas Python 3 (viene con el Mac). Abre la app **Terminal** y escribe:

```
cd ~/escalera_nq
python3 -m venv .venv
.venv/bin/pip install pandas numpy scipy pyyaml pytest pdfplumber
```

## Uso a mano

```
cd ~/escalera_nq
.venv/bin/python -m tareas.escalera              # NQ
.venv/bin/python -m tareas.escalera --indice ES  # ES (secciones 47 y 48)
```

Lee el PDF (o los PDF), calcula, guarda el histórico y abre la página en el navegador.

Opciones útiles:

| Comando | Para qué |
|---|---|
| `--precio 29250` | usar un precio de referencia escrito a mano (por defecto: liquidación del futuro) |
| `--pdf ruta.pdf` | usar un PDF concreto (ES: `--pdf Section47_….pdf Section48_….pdf`) |
| `--indice ES --comprobar` | solo dice si están el 47 y el 48 del mismo boletín (lo usa el vigilante) |
| `--csv archivo.csv --fecha-datos 2026-10-23 --futuro 29117.75` | importación manual de emergencia |
| `--no-abrir` | no abrir el navegador |
| `--permitir-viejos` | generar aunque los datos no sean del día anterior (solo para pruebas; la página lo avisa) |
| `--recentrar --precio 7840` | si el precio actual sale del rango del 68 %: escalera recentrada (ver abajo) |

**Recentrar** (`--recentrar --precio X`, opcionales `--sesion AAAA-MM-DD` y `--forzar`): si X
está fuera del rango del 68 % de la escalera del día (por defecto la última del histórico),
la recalcula con el **mismo open interest** (el boletín archivado de ese día en
`datos/entrada/archivo`) centrada en X: cambian la gamma, la frontera, los rangos y los
niveles que dependen del precio; los muros por OI no. Se guarda aparte como
`datos/historico/ES_<sesión>_recentrada_<X>.json` y `…_recentrada_<X>_esc2.txt`; el histórico
del día no se toca y el backtest no la lee. Dentro del rango no hace nada (salvo con
`--forzar`). La página del día tiene una casilla «Precio actual»: si escribes un precio fuera
del rango, avisa y te da la orden para recentrar; las recentradas ya hechas salen con un aviso
y el botón «Copiar ESC2 recentrado» (la más reciente arriba). La página no sabe el precio en
vivo: lo escribes tú.

Formato del CSV manual (cabecera obligatoria):

```
subyacente,vencimiento,tipo,strike,open_interest,volumen,precio
NQ,2026-10-26,call,29500,1520,340,85.25
NQ,2026-10-26,put,29000,2100,512,40.50
```

Para ver solo los números en la Terminal (sin página):

```
.venv/bin/python -m calculos.escalera datos/entrada/Section40_Nasdaq_100_And_E_Mini_Nasdaq_100_Options.pdf
.venv/bin/python -m calculos.escalera --indice ES datos/entrada/Section47_….pdf datos/entrada/Section48_….pdf
```

**Diagnóstico de la frontera con los vencimientos más cercanos** (no cambia nada; sirve para
comparar con otras fuentes que calculan la frontera solo con lo que vence en pocos días):

```
.venv/bin/python -m tareas.diagnostico_frontera --indice ES datos/entrada/archivo/Section47_2026-10-26_preliminar.pdf datos/entrada/archivo/Section48_2026-10-26_preliminar.pdf
.venv/bin/python -m tareas.diagnostico_frontera --indice NQ --dias 7 datos/entrada/Section40_….pdf
```

Da, al lado de los de la escalera, la frontera, la GEX en el precio y los mayores bloques de
calls y puts (OI y GEX, a ±3 % del precio) usando solo los vencimientos de los próximos N días
naturales (7 por defecto, de la sesión a la sesión + 6).

## Qué se genera

| | NQ | ES |
|---|---|---|
| Página del día (la que se abre) | `salida/escalera_hoy.html` | `salida/escalera_es_hoy.html` |
| Copia de cada sesión | `salida/html/escalera_AAAA-MM-DD.html` | `salida/html/escalera_es_AAAA-MM-DD.html` |
| Histórico (todos los datos del día) | `datos/historico/AAAA-MM-DD.json` | `datos/historico/ES_AAAA-MM-DD.json` |
| Niveles para Excel/Numbers | `datos/historico/AAAA-MM-DD_niveles.csv` | `datos/historico/ES_AAAA-MM-DD_niveles.csv` |
| Texto ESC2 del día (registro diario) | `datos/historico/AAAA-MM-DD_esc2.txt` | `datos/historico/ES_AAAA-MM-DD_esc2.txt` |
| PDF archivados | `datos/entrada/archivo/Section40_AAAA-MM-DD.pdf` | `…/Section47_AAAA-MM-DD.pdf` y `…/Section48_AAAA-MM-DD.pdf` |

Tus notas del journal van en el campo `"nota"` de cada nivel del JSON.

- `datos/registro.log` — registro de cada ejecución y de los errores.
- `datos/registro.log` — registro de cada ejecución y de los errores.

Si algo falla (no hay PDF, formato cambiado, datos de otro día), la página de hoy
muestra un **error en rojo** en lugar de datos viejos.

## Automático (vigilante y 23:00)

- **Vigilante** (`tareas/auto/vigilar.sh`, agente `com.marc.escalera`): cada vez que cambia
  `datos/entrada/`:
  - NQ: si hay un `Section40…pdf` nuevo, genera la escalera del NQ, abre la página y avisa.
  - ES: cuando están el `Section47…pdf` y el `Section48…pdf` **del mismo boletín** (fecha
    leída dentro del PDF), genera la escalera del ES, abre su página y avisa. Si falta uno
    (o son de días distintos), espera 90 s por si se está descargando y, si sigue faltando,
    sale una notificación «Escalera ES: falta un PDF» diciendo cuál.
- **23:00** (`com.marc.estado-hoy`): «Estado hoy» de los dos índices.
- **08:00** y al iniciar sesión (`com.marc.escalera-databento`, `tareas/escalera_databento.py`):
  escalera del NQ y del ES **sin PDF**, con Databento (ver abajo). Va envuelta en
  `/usr/bin/caffeinate -i -s` para que el Mac no se duerma mientras espera, y el Mac se
  despierta solo a las 07:55 de lunes a viernes (`pmset repeat wake MTWRF 07:55`).
- El aviso diario con los enlaces del Daily Bulletin no está en el Mac (es una tarea
  programada aparte).

### Escalera diaria con Databento (sin PDF)

Los días laborables a las 08:00 (hora de Madrid) y al iniciar sesión: si la escalera de la
sesión ya existe (del PDF o de Databento) no hace nada; si no, espera a que Databento tenga el
boletín y descarga solo lo de ese boletín (open interest, liquidaciones, definiciones de la
semana y futuro) del NQ y del ES, calcula la
escalera con el mismo código que la reconstrucción validada y la guarda en `datos/historico`
con su página, como el vigilante. Avisa en pantalla al terminar o si falla.

- **Hora** (medido el 3-nov-2026): CME publica el open interest entre las 01:15 y las 05:00
  UTC (casi siempre ≈ 01:30-02:00; el del lunes, el domingo a las 17:30 UTC); el histórico de
  Databento llega con un retraso variable (de 15 minutos a 8 horas). La tarea consulta (gratis)
  hasta dónde tiene datos y espera, cada 5 minutos, a que el tramo esté completo; como mucho
  hasta las 15:15 de Nueva York (`databento_diario.esperar_hasta` y `esperar_zona`); si llega
  sin datos, avisa «Databento no ha llegado: descarga el PDF». Cada intento queda en
  `datos/registro.log` con su hora y si el Mac estuvo dormido. Con 8 horas de retraso, la escalera sale
  hacia las 10:00-14:00; con poco retraso, nada más arrancar a las 08:00.
- **Gasto**: calcula el coste exacto antes de descargar; si pasa de `tope_dia` (0,50 $) o de
  `tope_mes` (12 $ desde el 12-nov-2026, para incluir el oro), no descarga y avisa. Lo gastado queda en `datos/oi/gasto_databento.json`.
  Coste real: ≈ 0,22 $/día los dos índices (≈ 0,35 $ los lunes, por las definiciones de la
  semana): unos 5 $ al mes. Casi todo es el tramo de liquidaciones (llegan de 20:00 a 23:40 UTC).
- **PDF**: si ya hay escalera del PDF para esa sesión, no la pisa (la de Databento queda en
  `<sesión>_databento_esc2.txt`). Si hay PDF de ese boletín, compara las dos y apunta las
  diferencias en `datos/registro.log`; el vigilante, al generar la del PDF, compara también con
  la de Databento. El camino del PDF sigue igual, de respaldo.
- A mano: `.venv/bin/python -m tareas.escalera_databento` (`--coste` solo dice lo que costaría).

## "Estado hoy"

```
.venv/bin/python -m tareas.estado_hoy                    # NQ y ES, 5 últimas sesiones de cada uno
.venv/bin/python -m tareas.estado_hoy --indice ES
.venv/bin/python -m tareas.estado_hoy --sesion 2026-10-26
```

- Usa el **máximo y mínimo del futuro del Daily Bulletin de ese mismo día** (NQ: tabla de la
  página 1 de la sección 40; ES: tabla de la página 3 de la sección 47). Ese boletín
  es el PDF que descargas al día siguiente para la nueva escalera, así que la tarea de
  las 14:00 rellena sola el estado de la sesión anterior.
- El máximo y el mínimo del boletín son de la **sesión electrónica completa de Globex**
  (17:00 a 16:00 de Chicago, incluida la noche), no solo del horario regular: las
  columnas se llaman «GLOBEX HIGH / GLOBEX LOW» y el volumen RTH va aparte.
- Sin velas: cada nivel queda «Tocado» o «No alcanzado».
- En el ES, la tabla de futuros no se llama «GLOBEX HIGH/LOW»; según la nota del boletín las
  cifras reflejan las sesiones de CME Globex y del horario regular, así que se toman como
  sesión completa (confirmado el 29-oct-2026 con velas de 1 minuto de Databento: coinciden al tick con el máximo y el mínimo Globex del 26, 27 y 28 de octubre).
- Con un CSV de velas de 1 minuto de ese día en `datos/entrada/` (el índice se reconoce por el
  nombre: NQ/MNQ o ES/MES; un CSV sin índice en el nombre vale para el NQ;
  columnas time/open/high/low/close, en segundos Unix o fecha y hora): añade la hora del
  toque (hora de Madrid) y «Tocado y respetado» / «Tocado y roto». Roto = después del toque
  algún cierre de 1 minuto queda al menos 10 puntos más allá del nivel (configurable).
  Si no hay boletín pero sí velas, usa el máximo y mínimo de las velas hasta que llegue.
- Cada PDF leído se guarda en `datos/entrada/archivo/SectionNN_AAAA-MM-DD.pdf` (NN = 40, 47
  o 48), para que el
  boletín de cada día no se pierda aunque el navegador guarde el siguiente con el mismo nombre.
- Tus notas del journal (campo `"nota"` del JSON) se conservan aunque se repita la escalera.

## TradingView

Indicador: `tradingview/escalera_nq.pine` (Pine Script v6), **el mismo para el NQ y el ES**.
En la página de cada índice, el botón **«Copiar para TradingView»** copia su línea del día
(`ESC2|sym=NQ|…` o `ESC2|sym=ES|…`, el ES con decimales); se pega en los ajustes del
indicador añadido al gráfico de ese índice.

**Instalación (solo la primera vez):**

1. Abre TradingView y un gráfico de **NQZ2026** (o MNQZ2026). Usa el contrato que diga la
   página: los niveles están en precios de ese contrato.
2. Abajo, pestaña **Pine Editor** (en la app nueva: icono `{}` del panel derecho).
3. Menú del editor → **Crear nuevo → Indicador**. Borra todo el código de ejemplo.
4. Abre `tradingview/escalera_nq.pine` con TextEdit, copia todo (⌘A, ⌘C) y pégalo en el editor.
5. **Guardar** (⌘S) con el nombre «Escalera» y pulsa **Añadir al gráfico**.
6. En el gráfico, rueda dentada del indicador → **Entradas** → pega el texto en
   **«Niveles del día»** → Aceptar.
7. **ES:** abre un gráfico de **ESZ2026** (o MESZ2026), añade el mismo indicador
   («Indicadores» → «Mis scripts» → «Escalera») y pega el texto de la página del ES.

**Actualizar el indicador** (cuando cambia el archivo `.pine`): Pine Editor → abre el
script («Escalera NQ» si lo guardaste con ese nombre) → borra todo → pega el contenido
nuevo → Guardar. Desde la Fase ES-2 el indicador se titula «Escalera». Los ajustes se conservan,
salvo «Grosor de los muros», que desde la Fase 2 son «Grosor de los principales» y
«Grosor de los secundarios».

**Cada día:** botón «Copiar para TradingView» en la página → ajustes del indicador → pegar
en «Niveles del día». La esquina del gráfico muestra la fecha de los niveles y avisa en
rojo si son de otro día, de otro contrato o de otro índice.

### Qué dibuja

- **Niveles como zonas**: banda de ± el ancho del día (`w`) alrededor del strike, que sale
  de la última vela que la tocó y va hacia la derecha, con el precio en la escala. Si el
  nivel ha absorbido a otros, la zona se amplía hasta cubrirlos (la etiqueta sigue en el
  nivel que absorbe).
- **Color = tipo** (muro de calls, muro de puts, resistencia, imán, aceleración, OI
  relevante; frontera discontinua). **Grosor = categoría**: principal con borde grueso y
  relleno; secundario con borde normal; menor fino y semitransparente (interruptor
  «Mostrar los niveles menores»).
- **Rangos del 68 % y del 95 %**: bandas sombreadas desde el inicio de la sesión.
- **Gamma 0DTE**: zonas grises semitransparentes aparte, sin categoría ni etiqueta en la
  escala (interruptor «Zonas de gamma 0DTE»).
- Máximo y mínimo de la sesión Globex y la línea **NY Open** (00:00 de Nueva York, magenta).
- **Aviso de símbolo**: si el gráfico no es del índice del texto (NQ vale para NQ y MNQ;
  ES para ES y MES), sale «NO corresponden» en rojo.

### Límite de plots (64)

TradingView admite 64 *plots* por indicador. Las etiquetas de la escala son plots, y las
que tienen color variable cuentan doble. El indicador usa **57**: 27 etiquetas de nivel × 2
+ máximo + mínimo + NY Open. Zonas, bandas y zonas grises son cajas (`box`), que no cuentan
(hasta 100). Si un día hubiera más de 27 niveles, se quedan sin etiqueta primero los de
categoría menor más alejados del precio (su zona se dibuja igual).

### Formato del texto ESC2

Una sola línea, campos separados por `|`. Ejemplo (26-oct-2026, recortado):

```
ESC2|sym=NQ|fecha=2026-10-26|contrato=NQZ2026|roll=2026-12-10|ref=29118|flip=29575
|68=28616-29619|95=28115-30121|w=25
|L=IA:29000-29000:P:701/2705:261030_38,261026_20,261027_12:-:28975-29025
|L=O:28750-28750:S:132/3273:261026_29,261030_21,261106_13:-:28700-28800
|Z=29085-29135
```

| Campo | Qué es |
|---|---|
| `ESC2` | identificador y versión del formato |
| `sym` | índice (NQ; ES más adelante). El indicador avisa si el gráfico es de otro |
| `fecha` | sesión para la que valen los niveles |
| `contrato`, `roll` | contrato de futuros de los niveles y fecha de cambio de contrato |
| `ref`, `flip` | precio de referencia y frontera (gamma flip) |
| `68`, `95` | rangos de movimiento esperado del día |
| `w` | media anchura de las zonas, en puntos |
| `L=` | un nivel, con 7 campos separados por `:` (abajo). Van ordenados por importancia |
| `Z=` | zona gris de gamma 0DTE (desde-hasta ya dibujada). No es nivel |

Campos de cada `L=`:

1. **Tipos**: C muro de calls · P muro de puts · R resistencia · I imán · A aceleración ·
   O OI relevante (el primero visible por prioridad da el color).
2. **desde-hasta** del nivel (la etiqueta de la escala va en su centro).
3. **Categoría**: P principal · S secundario · M menor.
4. **calls/puts** abiertas (todos los vencimientos de la ventana).
5. **Desglose por vencimiento**: hasta 3 `AAMMDD_%` (peso de cada vencimiento en la gamma
   del nivel, de mayor a menor), o `-`.
6. **Estado del día**: R respetado · X roto · T tocado · N no alcanzado, o `-`.
7. **Zona dibujada** desde-hasta (ya con el ancho y, si es un nivel fundido, cubriendo los
   strikes absorbidos). Si falta, el indicador usa desde−w .. hasta+w.

El indicador sigue aceptando el formato antiguo **ESCNQ1** (`ESCNQ1|fecha=…|C=30000@2201/2763|…|G=29090-29130`):
en la página está en el desplegable «¿Tu indicador es la versión anterior?». Con ESCNQ1 no
hay categorías ni zonas (se dibujan líneas) y las zonas 0DTE van como `G=`.

### Qué símbolo usar

El contrato concreto que indica la página (ahora **NQZ2026**; también MNQZ2026, que cotiza
igual). NQ1! sirve mientras TradingView tenga detrás ese mismo contrato; el indicador lo
comprueba y avisa si no. Desde el cambio de contrato (roll, jueves 8 días antes del
vencimiento) la mayoría opera el siguiente, pero los niveles siguen en precios del viejo
hasta su vencimiento: la página avisa con 5 días hábiles de antelación y da la base.

### Recalcular en vivo (bloque `gv=`)

Opcional, al final del ESC2: calls, puts y σ√T por strike y grupo de vencimientos (0DTE, ≤ 2,
≤ 7, ≤ 21, ≤ 60 días y el resto), cada strike en ±3σ y, fuera, tramos de 0,25σ (sin ellos la
frontera se desvía decenas de puntos). Calls/puts son «efectivas»: ajustadas por mínimos
cuadrados para reproducir la gamma real del strike en ±3σ. Formato en `salida/gamma_vivo.py`.
Ocupa 12.000-19.000 caracteres (el campo de TradingView admite 40.960; si no cupiera, entran
primero los strikes con más gamma: `tradingview.gamma_vivo_max_caracteres`).

Con él, el indicador recalcula con Black-76 y el precio actual, **solo en la última vela y
cada N velas** (input), la **frontera en vivo** (línea punteada), el **régimen en vivo** (los dos
en la leyenda, junto a los de la apertura) y la **fuerza de cada nivel** («×1.3» en la zona: su
gamma ahora / en la apertura). El σ√T de cada grupo baja con el tiempo que queda hasta su
vencimiento: la gamma 0DTE se concentra en el precio y se apaga lejos de él a medida que se
acercan las 16:00. Sin el bloque, el indicador funciona igual que antes.

Comprobado (pruebas/test_gamma_vivo.py, copia en Python de las cuentas del Pine): con los
boletines del 23 al 30-oct la frontera queda a menos de 2 puntos de la de Python en el NQ
(< 0,5 en el ES).

## Confluencia con QQQ y NDX (★) — en pruebas

NO cambia la escalera del NQ. Las opciones de QQQ y NDX (OPRA, `tareas/databento_opra.py`,
`fuentes/opra.py`) se trasladan a precio NQ con un ratio por día (NQ frontal / QQQ o NDX: 4-nov-2026,
41,28 y 1,004) y se calcula su escalera aparte (`calculos/confluencia.py`). Cada nivel del NQ con un
nivel de QQQ/NDX a menos de `confluencia.distancia_puntos` (por defecto: mitad del ancho de zona +
`error_ratio_pct` del precio, ≈ 23 puntos) lleva ★ en la página y en el ESC2 (10.º campo: Q, N, QN;
`cq=` en la cabecera; `LQ=` los niveles solo QQQ/NDX). En el indicador: «Solo confluencias (★)» y
«Mostrar niveles solo QQQ» (las dos apagadas). Una opción de QQQ equivale a 0,12 opciones NQ, una de
NDX a 5 (misma GEX en dólares). En OPRA el número de cada opción cambia cada día: se une por símbolo.
Validación (`tareas/validar_confluencia.py`): ★ frente a sin ★, respetados o rotos, pendiente de datos.

## Escalera extendida (de 2σ a 4σ) y vigilancia del rango

Con movimientos grandes (más de 2σ) la escalera de siempre se queda sin niveles. Sin tocarla:

- **Escalera extendida** (`calculos/extendidos.py`): por cada lado, de `desviaciones_rango` (2σ, el
  95 %) a `desviaciones_extendido` (4σ) en `config.yaml`. Usa los MISMOS umbrales del día que la
  escalera de dentro (umbral de OI relevante y percentiles de categoría, sin recalcularlos con la
  tabla ampliada), la misma fusión y el mismo ancho de zona. Tipos: muro de puts (abajo), muro de
  calls (arriba), imán (misma regla de siempre: en la práctica no llega), aceleración y OI relevante;
  solo principales y secundarios. En el JSON van en `niveles_extendidos` y en el ESC2 como `LX=`
  (los 7 primeros campos de `L=`; los indicadores y lectores antiguos lo ignoran). En el
  indicador: «Niveles extendidos (más allá del 95 %)» (encendida), borde fino y discontinuo. El
  bloque `gv=` va strike a strike hasta ±4σ (si cabe en `gamma_vivo_max_caracteres`).
- **Recentrar** (`tareas.escalera --recentrar --precio X`): misma escalera con el mismo OI centrada
  en X (también con las de Databento). **Vigilancia** (`tareas/vigilar_rango.py`, cada 5 min con
  `com.marc.vigilar-rango`; se activa con doble clic en `tareas/auto/activar_vigilar_rango.command`):
  si el precio (Yahoo, ≈ 10 min de retraso) sale del 68 % de todas las escaleras del día, genera la
  recentrada y avisa.

## Fiabilidad de los niveles: lo que dicen los datos (Fases F-1 a F-4)

La escalera dice lo que los datos demuestran, no lo que la teoría supone. **No cambia cómo se calculan
los niveles** (GEX, OI, fusión, zonas, categoría): solo cómo se clasifican, se muestran y se etiquetan.

**Estudio** (`tareas/estudio_fiabilidad.py` → `salida/fiabilidad_NQ.json`/`.html` y `fiabilidad_ES…`).
Método de `calculos/respeto.py`: primer toque de cada zona en la sesión regular; «rechazo» si el precio
se aleja 0,2σ por el lado por el que llegó antes de pasar 0,2σ por el otro. Cada casilla (tipo × régimen
local × papel) se compara con **su propio control**: 20 zonas del mismo ancho a la misma distancia de la
apertura y en lado al azar. Régimen local = nivel por encima (positiva) o por debajo (negativa) de la
frontera; papel = techo (llega desde abajo) o suelo. También: llegada al OI máximo por tramo de distancia.
Escaleras: las reconstruidas (JSON o, en el ES, su ESC2) y después las diarias. Resultados (12-nov-2026):

| | NQ (200 sesiones) | ES (194 sesiones) |
|---|---|---|
| Muros por encima de la frontera | 76 % (n = 49; control 71 %) | 63 % (n = 46; control 62 %) |
| Muros por debajo de la frontera | 54 % (n = 50; control 56 %) | 63 % (n = 41; control 61 %) |
| OI máximo: llega en la sesión | 56 % (control 56 %) | 61 % (control 60 %) |
| Aceleración, OI relevante, resistencia | sin ventaja (de −4 a +1) | sin ventaja clara (de −4 a +5) |

Lectura: en el NQ, por debajo de la frontera todo rompe más (muro o no): el muro no protege más que un
precio cualquiera; por encima, ventaja pequeña (~+4). En el ES el régimen no cambia nada. El OI máximo
no atrae ni repele. (El documento del 12-nov comparaba con el control de todo el régimen y generaba el del
imán solo los días con toque; ya está corregido en `documentacion/estudio_fiabilidad_niveles_2026-11-12.md`.)

**Página y JSON** (`calculos/fiabilidad.py`, sección `fiabilidad` de `config.yaml`):
- Columna «Fiabilidad histórica»: % de rechazo de su tipo, régimen local y papel, su n y la ventaja frente
  al control; con n < 20, «pocos datos». Debajo de la escalera, qué dicen los datos de ese índice.
- Muros (solo si el índice muestra efecto de régimen, ≥ 10 puntos: hoy el NQ): «Muro fiable» (por encima
  de la frontera y día positivo), «Muro» (encima, día negativo), «Muro débil · ruptura» (debajo).
- Imán → **«OI máximo»**: «objetivo» solo a ≤ 0,25σ del precio; siempre con el % de llegada de su tramo.
- Aceleración, OI relevante y resistencia: «contexto», un escalón menos en la jerarquía visual
  (`categoria_visual`; la `categoria` calculada no cambia, el backtest tampoco).
- Oro: «aún no tiene estudio» hasta que haya escaleras pasadas y velas.

**ESC2**: `fb=R` (el régimen cambia los muros) o `fb=N` en la cabecera, y un 11.º campo opcional en cada
`L=`: F muro fiable · M muro · B muro débil (ruptura) · O OI máximo objetivo · L OI máximo lejos ·
c contexto · `-`. Los indicadores, la estrategia y los lectores antiguos lo ignoran.

**Indicador** (grupo «Fiabilidad histórica»): con `fb=R`, cada muro se compara EN VIVO con la frontera en
vivo: encima, sólido (más grueso si el día es positivo); debajo, discontinuo con «ruptura». El OI máximo
solo dice «objetivo» a ≤ 0,25σ del precio actual (si no, fino). Contexto: fino y gris. Esquina: «Muros
fiables» (verde: día positivo y precio encima de la frontera), «Todo rompe más» (rojo: precio debajo).
Sin el 11.º campo, como siempre. Simulación con las velas del 12-nov: `tareas/simular_indicador.py` →
`documentacion/capturas/sim_fiabilidad_NQ_2026-11-12.png` (no es una captura de TradingView).

**Mantenimiento** (`tareas/fiabilidad_mensual.py`, agente `com.marc.fiabilidad-mensual`, día 1 a las 22:00;
se activa con doble clic en `tareas/auto/activar_fiabilidad_mensual.command`): NQ y ES. Actualiza las velas
(MNQ.v.0, ES.v.0) hasta ayer con coste previo y tope (`fiabilidad.mensual.tope_velas`, 0,50 $, y el tope
mensual de Databento), guarda el estudio anterior en `salida/fiabilidad_historico/`, lo repite y avisa si
una casilla (n ≥ 20) cambia más de 10 puntos o si la ventaja de los muros por encima de la frontera deja
de ser positiva. A mano: `.venv/bin/python -m tareas.fiabilidad_mensual [--sin-velas]`.

## Jerarquía y limpieza visual

Se aplica igual en la página (filas gruesas / normales / tenues) y en el indicador.

1. **GEX bruta estructural** de cada nivel = |GEX de calls| + |GEX de puts| **sin la 0DTE**
   (sin las opciones que vencen en la propia sesión). Mide la gamma "de fondo" sin que
   calls y puts se anulen. La GEX neta sigue usándose para el régimen y la frontera.
2. **Fusión**: los niveles a **≤ 2 × w** (centro a centro, distancia incluida; hoy
   w = 25 → 50 puntos) se funden. De mayor a menor GEX bruta estructural, cada nivel
   absorbe a sus vecinos (suma su OI, su GEX y su desglose); un nivel absorbido no absorbe
   a nadie (sin cadenas). **Muros e imán nunca son absorbidos** (si coinciden dos, se quedan
   los dos); la frontera no entra en la fusión.
3. **Zonas**: ± w alrededor del nivel, con w = 5 % del movimiento esperado de 1σ, entre 10 y
   30 puntos. La zona de un nivel fundido va de min(centro − w, strike absorbido más bajo)
   a max(centro + w, strike absorbido más alto).
4. **Categorías**, por percentil de la GEX bruta estructural entre los niveles del día
   (después de fundir):
   - **principal**: percentil 80 o más, o muro de calls / muro de puts / imán / frontera;
   - **secundario**: entre el percentil 40 y el 80;
   - **menor**: por debajo del percentil 40.
5. **Gamma 0DTE aparte**: no crea niveles. Un strike es zona gris si la 0DTE es más de la
   mitad de su gamma bruta y su |GEX 0DTE| está en el percentil 95 o más del rango del 95 %.
   Los strikes cercanos (< 30 puntos) se unen; la zona se dibuja ± 5 puntos.

Ejemplo del 26-oct-2026: 16 niveles → 14 tras fundir (29.250 absorbe 29.300; 28.750 absorbe
28.800 y 28.700): 5 principales, 4 secundarios y 5 menores, más una zona gris 0DTE en
29.090–29.130. Capturas en `documentacion/capturas/` (la simulación del indicador usa velas
inventadas; no es una captura de TradingView).

## Vista corta (próximos N días)

Aparte de la escalera (que no cambia), la página y el indicador dan una **vista corta** con
solo los vencimientos de los próximos N días naturales (`vista_corta.dias` en `config.yaml`,
7 por defecto: de la sesión a la sesión + 6). Se calcula en `calculos/vista_corta.py` (lo
mismo que `tareas/diagnostico_frontera.py`):

- **Frontera corta**: gamma flip con esos vencimientos.
- **Muro de calls corto**: strike con más GEX de calls **estrictamente por encima** del precio
  de referencia; **muro de puts corto**: strike con más GEX de puts (más negativa)
  **estrictamente por debajo**. Los dos dentro del rango del 95 %, con su OI, su GEX y su
  distancia al precio (puntos y σ). Si en un lado no hay ninguno, la página lo dice (no se
  elige otro strike). Si un muro cae en la zona de un nivel de la
  escalera, el nivel lleva la etiqueta «muro corto» (en el indicador, «C·corto» / «P·corto»
  escrito en la zona; si no coincide con ninguno, línea fina punteada).
- **Régimen combinado** (arriba en la página y en la esquina del indicador): «Positivo
  (corto y completo)», «Negativo (corto y completo)» o «Transición (entre X e Y)» si la vista
  corta y la escalera completa no coinciden; X–Y es la franja entre las dos fronteras.
- En el texto ESC2 son campos opcionales de la cabecera (`fc`, `mc`, `mp`, `rc`, `rf`) y un
  8.º campo en cada nivel (`c`, `p`, `cp` o `-`). El indicador sigue aceptando los textos de
  antes. Plots: 58 de 64 (la frontera corta tiene su etiqueta en la escala).

## Vanna y charm

Solo informan: no cambian niveles, categorías, zonas ni la GEX. Se calculan con Black-76
(`calculos/black76.py`: `vanna`, `charm`) y se suman en `calculos/gex.py`
(`anadir_vex_cex`, `vex_cex_por_strike`, `resumen_vex_cex`, `efecto_charm`), con el **mismo
supuesto de posición que la GEX** (`signo_gex`: creadores largos de calls, cortos de puts).

- **CEX (charm)**: cambio de la delta de los creadores, en contratos del futuro, por cada día
  hábil que pasa (precio e IV quietos), calculado en la apertura. Para seguir cubiertos hacen
  lo contrario: CEX > 0 → **venden** hacia el cierre; CEX < 0 → **compran**. Se da el total,
  la parte **0DTE** y el resto. «Fuerte» si |CEX| ≥ `vanna_charm.charm_fuerte_contratos`.
- **VEX (vanna)**: cambio de esa delta si la IV sube 1 punto. Si **baja** la IV: VEX > 0 →
  flujo comprador; VEX < 0 → vendedor.
- **Muros e imán**: el charm **refuerza** un muro si su flujo aleja el precio de él (venta con
  el muro encima, compra con el muro debajo) y lo **debilita** si empuja hacia él. El imán, al
  revés. Si el precio está dentro de la zona, no se dice nada.
- **Página**: dos líneas bajo el régimen y la etiqueta «el charm lo refuerza / debilita» en
  cada muro o imán. **JSON**: `vanna_charm` (totales y por strike) y `cex`, `vex`,
  `efecto_charm` en cada nivel (los JSON antiguos no los traen y se leen igual).
- **ESC2** (opcional, los indicadores antiguos lo ignoran): `ch=V:F:1520:V1300:V220`
  (dirección C/V/N, fuerza F/D, contratos del total, de la 0DTE y del resto) y `va=C:850`
  (dirección si baja la IV, contratos por punto); 9.º campo en cada `L=`: F refuerza ·
  D debilita (minúscula si el charm es débil) · `-` (si no hay vista corta, el 8.º va como `-`).
- **Indicador**: las dos líneas en la leyenda («Charm y vanna en la esquina», grupo Vista corta).
- **Validación**: `tareas/validar_charm.py` (informe en `documentacion/validacion_charm.md`).
  Con este supuesto de posición el charm (y la vanna) sale **comprador los 240 días** del ES
  reconstruido: lo informativo es la fuerza, no la dirección. Umbral «fuerte» del ES 17.000
  contratos/día (percentil 67 de nov-jul); NQ 1.700 (× 0,10, provisional). Los días fuertes
  suben algo más en la última hora en los dos periodos, pero sin significación estadística:
  por ahora es información, no una señal.

**Journal del día**: cada JSON trae `"journal_dia": {"regimen_observado": "", "freno_en": ""}`
para que lo rellenes tú (régimen observado: `rango` o `tendencia`; freno en: el nivel donde se
paró el precio). Se conserva aunque se repita la escalera.

## Estrategia y backtest (ES, 2 minutos) — solo pruebas

**No conecta ningún bróker ni envía órdenes.** Las alertas con JSON para webhook están
apagadas por defecto y, aunque se enciendan, no hacen nada sin una alerta de TradingView
con URL de webhook creada a mano.

- `tradingview/estrategia_escalera.pine`: estrategia (Pine v6, `strategy()`) para ESZ2026 o
  MESZ2026 en velas de 2 minutos. Lee el mismo texto ESC2 que el indicador (5 casillas: un
  día en cada una, cada texto vale solo para su fecha). Entrada seleccionable: **rechazo en
  nivel** (por defecto: el precio toca la zona de un nivel protector y una vela de 2 min cierra
  de vuelta fuera; entrada al cierre a favor del VWAP; stop detrás de esa zona), con mejoras
  opcionales (a)–(g) apagadas (1.er toque, calidad de la mecha, sin los primeros 5 min, stop
  por tiempo, régimen, stop mínimo, días de noticias de `datos/noticias.txt`); **rebote en el VWAP** ( toque del VWAP con la mecha a ≤ 1 punto y cierre de
  vuelta del lado de la tendencia, a mercado al cierre; solo el 1.º y 2.º toque del día;
  opcionales: solo en gamma negativa, solo con el VWAP cerca de un nivel) o **iFVG de 2
  minutos** ligado al nivel que reaccionó (entrada límite en el retesteo). El VWAP filtra
  la dirección (anclaje 9:30 o 18:00 NY). Filtros opcionales:
  frontera de gamma y NY Open. Con escalera **no hay TP ni ratio fijo**: stop detrás del nivel
  protector más cercano por detrás de la entrada (muro, resistencia/soporte o imán; también
  detrás de la vela, con margen, nunca en zona gris) y TP antes del siguiente nivel donde el
  precio debería frenarse. El ratio sale de esas dos zonas; si es menor de 1:1 (ajustable) o
  no hay protector u objetivo, no opera. Sin límite de distancia al stop por defecto: si queda
  lejos se reducen los contratos (riesgo máximo 1.000 $ por defecto). Comisión y deslizamiento: pestaña «Propiedades» (2,50 $ y
  1 tick por defecto; para el MES pon ~0,75 $ en Propiedades y en «Comisión por contrato»).
- `estrategia/`: la misma lógica en Python, vela a vela, con el modelo de relleno del
  probador de TradingView (para correr el histórico largo fuera de TradingView).
- `tareas/databento_velas.py`: coste exacto de las velas de Databento (consulta gratuita,
  no descarga) y descarga con un tope de gasto obligatorio (`--tope`).
- `tareas/backtest_escalera.py`: fase A (histórico largo sin escalera: iFVG + VWAP) y fase B
  (días con ESC2 del ES), una opción cada vez frente a la base → `salida/backtest_escalera.html`
  (rechazo en nivel, con la prueba del stop mínimo en dos periodos separados); con
  `--entrada vwap --salida salida/backtest_escalera_vwap.html` y `--entrada ifvg --salida
  salida/backtest_escalera_ifvg.html`, las otras entradas. Resumen: `salida/resumen_noche.html`.
  Resultado (29-oct-2026, 5 años de ES): las dos entradas pierden por sí solas (rebote en el VWAP
  −479 R, iFVG −96 R); con la escalera solo hay 3 días de datos. Detalle en los informes.

```
.venv/bin/python -m tareas.databento_velas coste --desde 2021-11-01 --hasta 2026-10-29
.venv/bin/python -m tareas.backtest_escalera --velas-a datos/velas/ES_v_0_….csv --velas-b datos/velas/ESZ6_….csv
```

Las velas también pueden ser una exportación de TradingView («Exportar datos del gráfico»,
1 o 2 minutos, con volumen).

### Escaleras pasadas reconstruidas con Databento (ES)

Para tener histórico de la fase B: el open interest, la liquidación y la definición (strike,
vencimiento exacto, call/put y su futuro) de cada opción del ES salen de Databento
(GLBX.MDP3, esquemas `statistics` y `definition`) y la escalera se calcula **con el mismo
código** que la diaria. Comprobado con el boletín del 23-oct-2026 (sesión del 26): los 10
niveles, sus categorías y zonas y la zona gris son idénticos; la frontera se mueve 0,2 puntos;
el 99,4 % de las filas de opciones tiene el mismo open interest que el PDF.

- `tareas/databento_oi.py`: coste exacto (gratis) y descarga con tope obligatorio. Solo compra
  los tramos de horas que hacen falta (liquidación, primera publicación del open interest, la
  que ya existe antes de la sesión, y una foto semanal de definiciones). Lo ya bajado no se
  vuelve a pagar. Archivos en `datos/oi/`.
- `fuentes/databento_oi.py`: los convierte en la tabla común (como el lector del PDF).
- `tareas/reconstruir_escalera.py`: `reconstruir` guarda `datos/historico_reconstruido/
  ES_<sesión>_esc2.txt` (aparte del histórico real) y `comparar --dia` la compara con la real.
- Backtest: `--escaleras reconstruida` (o `ambas`) usa esas escaleras en la fase B.

```
.venv/bin/python -m tareas.databento_oi coste --desde 2025-10-31 --hasta 2026-10-28
.venv/bin/python -m tareas.databento_oi descargar --desde 2025-10-31 --hasta 2026-10-28 --tope <dólares>
.venv/bin/python -m tareas.reconstruir_escalera reconstruir --desde 2025-10-31 --hasta 2026-10-28
.venv/bin/python -m tareas.backtest_escalera --velas-b datos/velas/ES_v_0_….csv --escaleras reconstruida
```

## Configuración

Todos los umbrales están en `config.yaml`, comentados. Los de hoy:

| Sección | Parámetro | Valor | Qué hace |
|---|---|---|---|
| `jerarquia` | `percentil_principal` / `percentil_secundario` | 80 / 40 | cortes de las categorías |
| `jerarquia` | `tipos_siempre_principales` | muros, imán | siempre principales (la frontera también) |
| `jerarquia` | `fusion_factor_ancho` | 2 | distancia de fusión = factor × w |
| `jerarquia` | `tipos_nunca_absorbidos` | muros, imán | no los absorbe la fusión |
| `jerarquia` | `ancho_proporcion_sigma`, `ancho_minimo`, `ancho_maximo` | 0,05 · 10 · 30 | w = 5 % de 1σ entre 10 y 30 puntos |
| `gamma_0dte` | `dominio` / `percentil` / `ancho_puntos` | 0,5 / 95 / 5 | qué strikes son zona gris y cuánto se dibujan |
| `signo_gex` | `call` / `put` | +1 / −1 | **suposición**: dealers largos de calls y cortos de puts |
| `indices` | `NQ`, `ES` → `cme_pdf` | — | cómo leer el boletín de cada índice: secciones, etiquetas de producto, rangos, fechas, tabla de futuros |
| `seguimiento` | `ventanas_minutos` | 15, 30, 60 | hueco para la Fase 3c (vacío) |

Además: tipo de interés, ventana de vencimientos, distancia para agrupar strikes, percentil
de open interest relevante, gamma flip, avisos de roll, etc.

### Parámetros propios del ES

Los valores globales son los del NQ. El ES tiene en `indices → ES → parametros` los que
cambian (el NQ no tiene esa sección: usa los globales tal cual). Escalados por
r = ES/NQ = 0,2631 (liquidaciones DEC26 del 23-oct-2026) y ajustados a su rejilla de strikes:

| Parámetro | NQ | ES | Por qué |
|---|---|---|---|
| Ancho de zona mín.–máx. (`jerarquia.ancho_minimo/maximo`) | 10–30 | 2,5–7,5 | r × NQ, en múltiplos de medio paso de strike |
| Agrupar strikes (`distancia_agrupar_puntos`) | 30 | 7,5 | une strikes contiguos de 5 |
| Rejilla del umbral de OI (`niveles.rejilla_umbral_oi`) | 25 | 5 | 25 × r ≈ 6,6 → 5 (paso de strike del ES) |
| Números redondos (`niveles.numero_redondo_cada`) | 500 | 100 | el redondo natural del ES |
| Distancia del imán (`niveles.iman_distancia_puntos`) | 150 | 40 | r × NQ en múltiplos de 5 |
| Tramo del mapa 0DTE (`tramo_puntos`) | 25 | 5 | paso de strike de la 0DTE |
| Ancho de la zona gris (`gamma_0dte.ancho_puntos`) | 5 | 2,5 | medio paso de strike |
| Umbral de rotura con velas (`estado.umbral_rotura_puntos`) | 10 | 2,5 | 10 × r ≈ 2,6 → 2,5 |
| Multiplicador (`multiplicadores`) | 20 $ | 50 $ | contrato ES (MES 5 $, mismo precio) |

Iguales que el NQ: percentiles 80/40 y 75, fusión a 2 × w, 0DTE (dominio 0,5, percentil 95),
ventana de vencimientos, signos y gamma flip a ±5 %. Otras claves del ES en `indices → ES`:
`tradingview_decimales: 2` (ESC2 con decimales), `velas_patron` (CSV de velas del ES/MES),
`cme_pdf → diarias_codigos` y `diarias_por_precio: true` (diarias fechadas por precio).
Umbral de rotura con velas (`estado.umbral_rotura_puntos`): NQ 10 puntos, ES 2,5 (10 × r).

## Qué guarda el JSON (versión 3)

Además de lo anterior, cada nivel lleva: `gex_neto`, `gex_abs`, `gex_calls`, `gex_puts`,
`gex_bruta_estructural`, `categoria`, `zona_desde`/`zona_hasta`, `fusionados`,
`por_vencimiento` (GEX y calls/puts de cada vencimiento) y `seguimiento` (vacío hasta la
Fase 3c). Arriba: `boletin` (PRELIMINAR o FINAL y las comprobaciones del PDF), `jerarquia`
(umbrales del día, ancho de zona, distancia de fusión), `vencimientos_incluidos`,
`zonas_0dte`, `tradingview` (ESCNQ1) y `tradingview_esc2`.

## Pruebas

```
.venv/bin/pytest
```

Comprueban Black-76, GEX, gamma flip, movimiento esperado, probabilidad de toque,
calendario de festivos, la lectura del PDF (contra los totales que imprime el propio
boletín, los rangos de la página 1 y la sección EOO), la jerarquía, la fusión, las zonas,
el formato ESC2, el recuento de plots del indicador y la página. Las pruebas con el PDF
real se saltan si no está.

Además hay pruebas del lector del ES (`pruebas/test_cme_pdf_es.py`: totales del boletín, calls
sin etiqueta, comprobaciones, SME, fechas, diarias por precio) y del flujo del ES
(`pruebas/test_es_flujo.py`: página, par 47/48, «Estado hoy», indicador, vigilante).

**Regresión**: `pruebas/test_regresion.py` recalcula la escalera con los PDF archivados (NQ:
sección 40; ES: 47 y 48) y la compara con la base más reciente de cada índice y sesión en
`pruebas/regresion/`. Si un cambio mueve niveles a propósito, se añade una base nueva y se
explica en `pruebas/regresion/LEEME.md`. Las pruebas con PDF usan los archivados en
`datos/entrada/archivo/` (no el último descargado).

## Supuestos y aproximaciones

- GEX = gamma × OI × 20 $ × precio² × 1 %, con creadores de mercado largos de calls y
  cortos de puts (suposición estándar, en `signo_gex`; no es su posición real).
- Vencimientos: desde la 0DTE hasta el próximo trimestral inclusive (si faltan menos de
  5 días hábiles, también el siguiente). Configurable en `ventana_vencimientos`. Las
  calls/puts y la GEX de cada nivel suman todos esos vencimientos (la página lo indica).
- Muros, imán y OI relevante por open interest; la GEX se usa para régimen, frontera,
  resistencias, aceleración y la jerarquía visual.
- Tiempo en días hábiles / 252. La IV se calcula con la liquidación del día anterior;
  la gamma, en la apertura de la sesión (9:30 Nueva York).
- La IV de cada strike sale de la opción fuera del dinero (para las dos patas); si no se
  puede calcular se interpola (la página lo avisa).
- El umbral de "open interest relevante" se calcula con los strikes múltiplos de 25.
- Puts sin etiqueta en el PDF (trimestrales y Russell): se identifican por la secuencia
  de productos de las calls (cuando el mes vuelve atrás empieza el siguiente producto) y
  se comprueban con los rangos de strikes de la página 1 y la sección EOO/BLOCKS. Si
  algo no cuadra, la página lo avisa con el strike y el bloque.
- Probabilidad de toque ≈ 2 × probabilidad de cerrar más allá (lognormal, sin deriva).
- Calendario de festivos del mercado de EE. UU. (no el federal); no tiene en cuenta
  los cierres anticipados.
