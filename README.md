# Resalto hidráulico paramétrico — OpenFOAM + Python

Flujo reproducible: **parámetros → geometría y malla → simulación VOF → medición del resalto → validación contra Bélanger**.

## Inicio rápido (con interfaz)

### Requisitos
- **Linux** (Ubuntu, Linux Mint o similar). En Windows se necesita WSL con Ubuntu (no probado).
- **OpenFOAM** de openfoam.org (v11 o superior) o ESI (v2306 o superior). Probado en OpenFOAM 14 y ESI v1912.
- **Python 3.11 o superior** (Ubuntu 24.04 y Linux Mint 22 ya lo traen).
- Opcional, para ver los casos y crear videos: `sudo apt install paraview ffmpeg`

### 1. Descargar
En la página del repositorio: botón verde **Code → Download ZIP**, y descomprímelo.
(Con git: `git clone <enlace del repositorio>`.)

### 2. Abrir una terminal en la carpeta
En el explorador de archivos, entra a la carpeta descomprimida, haz **clic derecho en un espacio vacío → «Abrir en una terminal»**.
O abre una terminal y entra con `cd`, por ejemplo: `cd ~/Descargas/resalto_hidraulico`

### 3. Instalar (una sola vez)
```bash
bash instalar.sh
```
Crea el entorno de Python (`.venv/`), instala las librerías, detecta OpenFOAM y **crea el ícono «Resalto hidráulico»** en el escritorio y en el menú de aplicaciones.
Si dice que falta `python3-venv`, instálalo con `sudo apt install python3-venv` (o el nombre exacto que indique el mensaje, p. ej. `python3.12-venv`) y vuelve a ejecutarlo.

### 4. Abrir la app
La primera vez, desde la misma terminal:
```bash
bash iniciar.sh
```
Desde entonces, basta con **doble clic en el ícono «Resalto hidráulico»**.
Usa siempre `bash iniciar.sh` y no `./iniciar.sh`: al descargar como ZIP se puede perder el permiso de ejecución.

Se abre en el navegador (`http://localhost:8501`):

1. **Datos:** elige cómo ingresar el caudal (Q y ancho B con abertura de compuerta, caudal unitario q, o Froude), completa la tabla y revisa la **vista previa**: Froude, tirante conjugado, umbral, celdas y tiempo estimado por caso. La columna **Longitud poza** es opcional: vacía = modo validación (poza holgada); con valor = revisión de diseño (¿el resalto queda contenido?).
2. **Guardar y ejecutar:** el estudio entra en una **cola** y OpenFOAM corre **en segundo plano**, una corrida a la vez. Cerrar el navegador o la app no la detiene; solo el botón **Detener** o apagar o suspender la PC. Si se detiene, al relanzar continúa desde el último tiempo guardado.
3. **Avance:** una barra por caso con el tiempo simulado.
4. **Resultados:** tabla con semáforo por caso, error frente a Bélanger, gráficas y descarga en zip. Se generan solos al terminar.
   - **🔍 Abrir en ParaView:** abre el caso ya configurado (alpha.water, vista de frente, último tiempo).
   - **🎬 Crear video:** renderiza la formación del resalto con ParaView sin ventana y la convierte en MP4 (formatos horizontal, cuadrado o vertical). Requiere `paraview` y `ffmpeg` (`sudo apt install paraview ffmpeg`). Los videos quedan en `estudios/<nombre>/videos/`.

Cada estudio se guarda en `estudios/<nombre>/` (parámetros, casos y resultados) y **nunca se sobrescribe**.
Para cerrar la app: `pkill -f "streamlit run app.py"`. Para desinstalar: borra la carpeta `.venv/` y el ícono.

## Uso por línea de comandos (sin interfaz)

```
parametros.json      ← edita aquí (y1, lista de Froude, malla, tiempos)
hidraulica.py        ← Bélanger, calado crítico, dimensionamiento del umbral
malla.py             ← genera el blockMeshDict multibloque
generar_casos.py     ← crea una carpeta de caso por cada Fr1   (Windows o Linux)
ejecutar.py          ← blockMesh, checkMesh y solver (foamRun o interFoam), en Linux con OpenFOAM
postproceso.py       ← perfiles h(x), y1, y2, gráfica vs. Bélanger (Windows o Linux)
plantilla_org/       ← diccionarios para openfoam.org v11+ (foamRun -solver incompressibleVoF)
plantilla_esi/       ← diccionarios para openfoam.com / ESI (interFoam + setFields)
app.py, cola.py      ← interfaz web local y cola de ejecución en segundo plano
instalar.sh, iniciar.sh
```

## Modelo

```
 compuerta                                          umbral (cresta ancha)
   │▓▓                                              ┌──────┐
   │▓▓  y1, V1 →        ╱‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾│      │‾‾╲
 ──┘▓▓▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁╱  resalto     y2             │  P   │   ╲ caída libre → salida
   entrada (z < y1)                                  └──────┘_______
```

- **Entrada**: lámina supercrítica bajo una compuerta, con calado `y1` y velocidad `V1 = Fr1·√(g·y1)`.
- **Control de aguas abajo**: un umbral de cresta ancha con caída libre. Su altura `P` se dimensiona por energía (calado + carga de velocidad de aproximación) para que el nivel de cola sea ≈ `factor_cola · y2_Bélanger`. El flujo ubica el resalto donde el calado supercrítico local cumple la condición de conjugados.
- **La validación no es circular**: el umbral no impone `y1` ni `y2`. Ambos se **miden** en la simulación, `Fr` se recalcula en el pie del resalto y se compara `y2/y1` con Bélanger.
- **Solver**: VOF incompresible (`foamRun -solver incompressibleVoF` en openfoam.org; `interFoam` en ESI), turbulencia k-ω SST, 2D (una celda de espesor).

## Uso

### a) Generar los casos (Windows o Linux)
```bash
python generar_casos.py                          # crea los casos que aún no existen
python generar_casos.py --casos Fr7p00 --forzar  # rehace solo ese caso
```
Un caso que ya existe no se modifica salvo con `--forzar`. `ejecutar.py` omite los casos ya terminados.
Muestra una tabla con V1, q, y2 teórico, altura del umbral y número de celdas de cada caso.

### b) Ejecutar (en Linux, con el entorno de OpenFOAM cargado)
Requisitos: `sudo apt install python3-numpy python3-matplotlib`. Comprueba tu versión con `echo $WM_PROJECT_VERSION`.
```bash
cd ~/resalto_hidraulico
python3 ejecutar.py --np 4          # todos los casos, 4 núcleos
python3 ejecutar.py --casos Fr4p00  # uno solo
```
Tiempo de referencia (1 núcleo, malla por defecto): Fr = 4, ~15 000 celdas y 30 s simulados, ≈ 10 min. Los casos de Froude alto tienen hasta ~55 000 celdas y mayor velocidad: calcula más de 1 h por caso en serie.

### c) Postprocesar
```bash
python postproceso.py
```
Genera en `resultados/`:
- `validacion_belanger.png`: y2/y1 simulado frente a la curva de Bélanger (banda ±5 %)
- `perfiles.png`: perfiles de superficie adimensionales de todos los casos
- `perfil_<caso>.png`: perfil medio, banda ±1σ temporal, pie y final del resalto
- `resultados.csv`: tabla con y1, y2, Fr en el pie, error %, longitud del resalto y estado

Para ver los campos en 3D, abre `casos/<caso>/caso.foam` en ParaView.

## Parámetros principales (`parametros.json`)

| Clave | Significado | Valor inicial |
|---|---|---|
| `y1` | calado bajo la compuerta [m] | 0.04 |
| `Fr1` | lista de Froude de entrada | 4 a 8 |
| `factor_cola` | nivel de cola / y2 de Bélanger | 1.00 |
| `cd_umbral` | coef. de descarga de la cresta ancha, sobre carga de energía (**empírico**; ver "Calibración") | 0.85 |
| `malla.celdas_en_y1` | celdas verticales dentro de y1 (resolución) | 5 |
| `malla.relacion_dx_dz` | relación de aspecto de celda | 1.25 |
| `tiempo_final` | tiempo simulado [s] | 30 |
| `fraccion_promedio` | fracción final del tiempo que se promedia | 0.3 |

**Estudio de independencia de malla:** copia `parametros.json` y cambia `celdas_en_y1` (4, 5, 8). Genera cada juego en su carpeta:
```bash
python generar_casos.py -p parametros_fina.json -o casos_fina
python3 ejecutar.py -d casos_fina --np 4
python postproceso.py -d casos_fina -o resultados_fina
```

## Si el resalto no queda libre

`resultados.csv` marca el estado de cada caso:
- **ahogado (pie en la compuerta)**: el nivel de cola es demasiado alto. Baja `factor_cola` (p. ej. 0.95).
- **barrido hacia el umbral**: el nivel de cola es demasiado bajo. Sube `factor_cola`.
- **en movimiento**: el pie se desplazó más de 2·y1 durante la ventana promediada (la columna `velocidad_pie_m_s` lo cuantifica). Aumenta `tiempo_final` (la corrida continúa desde el último tiempo) o ajusta `factor_cola`.
- La banda ±1σ del perfil indica cuánto oscila la superficie.

## Calibración (pruebas realizadas con Fr = 4)

La posición del resalto en un canal horizontal es **muy sensible al nivel de cola**: unos pocos % de diferencia lo desplazan hasta la compuerta o hacia el umbral. En laboratorio esto se ajusta con una compuerta de cola; aquí, con `cd_umbral` y `factor_cola`.

| Prueba | `cd_umbral` | Altura del umbral | Nivel de cola obtenido | Resultado |
|---|---|---|---|---|
| 1 | 0.85 | 4.8 cm | 19.2 cm | **Libre, cuasi-estacionario** (el pie avanza ~2.6 cm/s, ≈1 % de V1). Fr en el pie = 3.47; y2/y1 = 4.35 frente a 4.43 de Bélanger: **error −1.6 %** |
| 2 | 0.93 | 6.4 cm | 21.1 cm | Ahogado: el resalto sube hasta la compuerta |
| 3 | 0.89 | 5.6 cm | 20.1 cm | Ahogado |

Lectura física: la lámina supercrítica se engrosa por fricción antes del pie (4.0 → 4.4 cm). Su calado conjugado real (~19.5 cm) es menor que el de Bélanger calculado con el Fr nominal de la compuerta (20.7 cm). Un nivel de cola apenas superior a ese valor ya ahoga el resalto. Por eso se dejó `cd_umbral = 0.85`. Es mejor una deriva lenta aguas abajo, que sigue dando una medición válida, que un resalto ahogado, que no sirve.

**Para los demás Froude** (5 a 8) la calibración no se ha probado. Corre primero un caso, revisa el `estado` en `resultados.csv` y ajusta `factor_cola` antes de lanzar el barrido completo. Las gráficas de esas tres pruebas están en `pruebas_calibracion/`.

## Versión de OpenFOAM

`parametros.json` tiene la clave `"rama_openfoam"`:
- `"auto"` (por defecto): la detecta con `$WM_PROJECT_VERSION`. Si empieza con `v` (v2312…) usa ESI; si es un número (14) usa openfoam.org. Requiere tener OpenFOAM cargado al correr `generar_casos.py`.
- `"org"` o `"esi"`: la fija a mano (útil si generas los casos en Windows).

`ejecutar.py` detecta el solver leyendo el `controlDict` de cada caso.

| Plantilla | Basada en | Estado de pruebas |
|---|---|---|
| `plantilla_esi` | sintaxis ESI | **Probada** en v1912: malla, tres corridas completas y postproceso |
| `plantilla_org` | tutorial `incompressibleVoF/weirOverflow` de **OpenFOAM 14** | **Probada** en OpenFOAM 14: caso Fr = 4, error de −2.8 % frente a Bélanger |

Diferencias de `plantilla_org`: `solver incompressibleVoF;` en `controlDict`; `phaseProperties` y `physicalProperties.<fase>`; turbulencia en `momentumTransport` (`model kOmegaSST`); condición inicial con `internalField { type zonal; ... }` en `alpha.water` (sin `setFields`); `interfaceCompression` en `div(phi,alpha)`; `prghTotalPressure` en la atmósfera.

Si `foamRun` falla, el error está en `casos/<caso>/log.foamRun`: las últimas ~30 líneas bastan para corregirlo.

## Limitaciones conocidas

- **2D y RANS**: k-ω SST tiende a sobreestimar la turbulencia en la interfaz y no representa bien el aire atrapado del rodillo. Para `y2/y1` suele bastar; para longitudes de rodillo, presiones fluctuantes o aireación conviene LES o 3D.
- **Criterio de pie y final** (convención): el pie es donde la superficie media sube un 5 % del salto total; `y1` se toma de la recta ajustada al tramo supercrítico evaluada en el pie. El final es donde el calado queda dentro de ±5 % del nivel de cola. Con otros umbrales cambian `L_resalto` e `y1` en unos pocos %, y Fr en el pie es sensible a `y1` (Fr ∝ y1^-1.5).
- **Froude bajo (< ~3.5)**: el umbral de cresta ancha no da el nivel de cola necesario y el caso se omite. Haría falta otro control (compuerta aguas abajo o nivel impuesto).
- **Ecuación de Bélanger**: supone canal rectangular horizontal, presión hidrostática y fricción despreciable en el tramo del resalto. Desviaciones de unos pocos % son esperables.
