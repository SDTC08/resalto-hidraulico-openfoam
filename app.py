"""
app.py — Interfaz web local para el simulador de resalto hidráulico.

Iniciar:  ./iniciar.sh   (o: .venv/bin/streamlit run app.py)
"""
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

import cola
import video
from hidraulica import belanger, disenar_caso
from malla import numero_celdas

AQUI = Path(__file__).resolve().parent
ESTUDIOS = AQUI / "estudios"
BASE = json.loads((AQUI / "parametros.json").read_text(encoding="utf-8"))
G = BASE.get("g", 9.81)

MALLAS = {"Gruesa (rápida, para probar)": 4, "Media (recomendada)": 5, "Fina (estudio de malla)": 8}
# Referencia de tiempo: Fr=4, 15 166 celdas, V1=2.506 m/s, 30 s simulados, dx=0.01 m,
# 4 núcleos -> 137 s (corrida real en OpenFOAM 14). El costo crece con
# celdas × pasos de tiempo, y los pasos con t_final·V1/dx. Es solo una estimación.
REF_SEG, REF_UNID, REF_NP = 136.8, 15166 * 2.506 * 30.0 / 0.01, 4

st.set_page_config(page_title="Resalto hidráulico · Hidroclic", page_icon="🌊", layout="wide")
st.markdown("""
<style>
h1, h2, h3 { color:#0F4E38; }
div[data-testid="stMetricValue"] { color:#0F4E38; }
.etiqueta { font-family: 'JetBrains Mono', monospace; color:#2F6F73; letter-spacing:2px; font-size:0.85rem; }
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ utilidades
def slug(txt: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", txt.strip()).strip("_")
    return s[:60] or "estudio"


def estudios_existentes():
    if not ESTUDIOS.exists():
        return []
    return sorted([d.name for d in ESTUDIOS.iterdir() if (d / "parametros.json").exists()],
                  key=lambda n: -(ESTUDIOS / n / "parametros.json").stat().st_mtime)


def tiempo_estimado(celdas, v1, t_fin, dx, nucleos):
    seg = REF_SEG * (celdas * v1 * t_fin / dx) / REF_UNID * (REF_NP / max(1, nucleos)) ** 0.7
    return seg


GUION_PV = """# vista_paraview.py — generado por la app: abre el caso listo para ver el resalto.
from paraview.simple import *
foam = {foam!r}
r = OpenFOAMReader(registrationName="resalto", FileName=foam)
r.UpdatePipelineInformation()
tiempos = list(getattr(r, "TimestepValues", []) or [])
vista = GetActiveViewOrCreate("RenderView")
d = Show(r, vista)
# Campo del agua: alpha.water (1 = agua, 0 = aire)
try:
    ColorBy(d, ("POINTS", "alpha.water"))
except Exception:
    ColorBy(d, ("CELLS", "alpha.water"))
lut = GetColorTransferFunction("alpha.water")
lut.RescaleTransferFunction(0.0, 1.0)
d.SetScalarBarVisibility(vista, True)
# Último tiempo simulado (GoToLast mueve también el selector de tiempo de la ventana)
escena = GetAnimationScene()
escena.UpdateAnimationUsingDataTimeSteps()
try:
    escena.GoToLast()
except Exception:
    if tiempos:
        escena.AnimationTime = tiempos[-1]
        vista.ViewTime = tiempos[-1]
# Vista de frente (plano x-z, agua de izquierda a derecha, fondo abajo)
xmin, xmax, ymin, ymax, zmin, zmax = r.GetDataInformation().GetBounds()
cx, cy, cz = (xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2
vista.CameraFocalPoint = [cx, cy, cz]
vista.CameraPosition = [cx, cy - 10 * (xmax - xmin), cz]
vista.CameraViewUp = [0, 0, 1]
vista.CameraParallelProjection = 1
vista.ResetCamera()
Render()
"""


def abrir_paraview(caso: Path):
    """
    Abre el caso en ParaView como programa aparte (no bloquea la app), ya
    configurado: alpha.water, vista de frente y último tiempo. Busca
    'paraview' en el sistema o en el entorno de OpenFOAM. Devuelve (ok, mensaje).
    """
    foam = caso / "caso.foam"
    foam.touch(exist_ok=True)
    guion = caso / "vista_paraview.py"
    guion.write_text(GUION_PV.format(foam=str(foam)))
    kw = dict(cwd=caso, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              stdin=subprocess.DEVNULL, start_new_session=True)
    if shutil.which("paraview"):
        subprocess.Popen(["paraview", f"--script={guion}"], **kw)
        return True, "ParaView se está abriendo en otra ventana (puede tardar unos segundos)."
    bashrc = cola.leer_ajustes().get("bashrc")
    if bashrc:
        r = subprocess.run(["bash", "-c", f'source "{bashrc}" >/dev/null 2>&1; command -v paraview || command -v paraFoam'],
                           capture_output=True, text=True)
        exe = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        if exe:
            cmd = f'source "{bashrc}" >/dev/null 2>&1; ' + (
                f'paraview --script="{guion}"' if exe.endswith("paraview") else "paraFoam")
            subprocess.Popen(["bash", "-c", cmd], **kw)
            return True, "ParaView se está abriendo en otra ventana (puede tardar unos segundos)."
    return False, ("No se encontró ParaView en esta PC. Instálalo con `sudo apt install paraview` "
                   "y vuelve a intentarlo.")


def fmt_min(seg):
    return f"{seg / 60:.0f} min" if seg >= 90 else f"{seg:.0f} s"


@st.cache_data(ttl=600, show_spinner=False)
def detectar_openfoam(_forzar=0):
    return cola.configurar_openfoam()


# ------------------------------------------------------------------ barra lateral
with st.sidebar:
    st.markdown('<p class="etiqueta">HIDROCLIC</p>', unsafe_allow_html=True)
    st.title("Resalto hidráulico")
    st.caption("Simulación CFD paramétrica con validación automática")

    st.subheader("OpenFOAM")
    if "forzar_det" not in st.session_state:
        st.session_state.forzar_det = 0
    of = detectar_openfoam(st.session_state.forzar_det)
    if of:
        st.success(f"Versión {of['version']} ({'openfoam.org' if of['rama'] == 'org' else 'ESI'})")
        st.caption(f"`{of['bashrc']}`")
    else:
        st.error("No se encontró OpenFOAM. Indica la ruta de su archivo `etc/bashrc`.")
    with st.expander("Cambiar instalación"):
        ruta = st.text_input("Ruta del bashrc", value=of.get("bashrc", "") if of else "",
                             placeholder="/opt/openfoam14/etc/bashrc")
        if st.button("Detectar de nuevo"):
            if ruta:
                cola.configurar_openfoam(ruta)
            st.session_state.forzar_det += 1
            st.rerun()

    st.subheader("Cola")
    t_act = cola.en_curso()
    n_pend = len(cola.pendientes())
    if t_act:
        st.info(f"Corriendo: **{Path(t_act['estudio']).name}**" + (f" · {n_pend} en espera" if n_pend else ""))
    else:
        st.caption("Sin corridas en curso." + (f" {n_pend} en espera." if n_pend else ""))

    st.subheader("Estudio")
    existentes = estudios_existentes()
    opciones = ["➕ Nuevo estudio"] + existentes
    elegido = st.selectbox("Abrir", opciones, index=0)

tab_datos, tab_avance, tab_res, tab_ayuda = st.tabs(["1 · Datos", "2 · Avance", "3 · Resultados", "Ayuda"])


# ------------------------------------------------------------------ 1 · datos
def cargar_estudio(nombre):
    p = json.loads((ESTUDIOS / nombre / "parametros.json").read_text(encoding="utf-8"))
    return p


with tab_datos:
    previo = cargar_estudio(elegido) if elegido in existentes else None
    ui = (previo or {}).get("interfaz", {})

    c1, c2 = st.columns([2, 3])
    with c1:
        nombre_est = st.text_input("Nombre del estudio", value=elegido if previo else "mi_estudio",
                                   help="Se crea una carpeta con este nombre dentro de 'estudios/'.")
    with c2:
        modos = ["Caudal Q y ancho B", "Caudal unitario q", "Número de Froude (validación)"]
        modo = st.radio("¿Cómo quieres ingresar los datos?", modos, horizontal=True,
                        index=modos.index(ui.get("modo", modos[0])))

    st.markdown("#### Casos a simular")
    st.caption("Edita la tabla: cada fila es una simulación. Usa **+** al pie para agregar filas.")

    COL_L = "Longitud poza (m)"
    EJEMPLOS = {
        modos[0]: {"Q (m³/s)": [9.0, 11.0, 13.5], "B (m)": [4.0] * 3, "Abertura a (m)": [0.5] * 3,
                   COL_L: [float("nan")] * 3},
        modos[1]: {"q (m²/s)": [2.25, 3.25, 4.25], "y1 (m)": [0.30] * 3, COL_L: [float("nan")] * 3},
        modos[2]: {"y1 (m)": [0.04] * 5, "Fr1 (-)": [4.0, 5.0, 6.0, 7.0, 8.0], COL_L: [float("nan")] * 5},
    }
    if modo == modos[0]:
        cc = st.columns([1, 3])[0].number_input("Coeficiente de contracción de la compuerta, Cc", 0.50, 1.00,
                             float(ui.get("Cc", 0.61)), 0.01,
                             help="y1 = Cc × abertura. 0.61 es un valor típico para compuerta plana "
                                  "vertical; verifícalo para tu compuerta.")
    datos = ui.get("tabla") if (ui.get("modo") == modo and ui.get("tabla")) else EJEMPLOS[modo]
    defecto = pd.DataFrame(datos)
    if COL_L not in defecto.columns:
        defecto[COL_L] = float("nan")
    st.caption(f"**{COL_L}** es opcional: déjala vacía para que el programa use una poza holgada "
               "(modo validación), o escribe la longitud real para revisar si el resalto queda contenido.")
    tabla = st.data_editor(defecto, num_rows="dynamic", width="stretch", key=f"tabla_{modo}",
                           column_config={c: st.column_config.NumberColumn(c, min_value=0.0, format="%.3f")
                                          for c in defecto.columns})

    st.markdown("#### Opciones de simulación")
    o1, o2, o3 = st.columns(3)
    with o1:
        malla_nom = st.selectbox("Malla", list(MALLAS), index=list(MALLAS.values()).index(
            (previo or BASE)["malla"]["celdas_en_y1"]) if (previo or BASE)["malla"]["celdas_en_y1"] in MALLAS.values() else 1)
    with o2:
        renov = st.slider("Duración (renovaciones del agua en la poza)", 3, 12,
                          int((previo or {}).get("interfaz", {}).get("renovaciones", 6)),
                          help="Tiempo simulado = renovaciones × longitud ÷ velocidad aguas abajo. "
                               "Con 6 se estabilizó el caso de prueba. Así el tiempo se adapta a la escala de cada caso.")
    with o3:
        ncpu = os.cpu_count() or 2
        nucleos = st.slider("Núcleos", 1, ncpu, min(int((previo or BASE)["nucleos"]), ncpu))
    with st.expander("Avanzado (calibración del control aguas abajo)"):
        a1, a2, a3 = st.columns(3)
        factor_cola = a1.number_input("factor_cola", 0.80, 1.10, float((previo or BASE)["factor_cola"]), 0.01,
                                      help="Tirante aguas abajo / y2 de Bélanger. Bájalo si el resalto sale ahogado.")
        cd_umbral = a2.number_input("Cd del umbral", 0.70, 1.10, float((previo or BASE)["cd_umbral"]), 0.01,
                                    help="Coeficiente de descarga de la cresta ancha (empírico). 0.85 calibrado con Fr ≈ 4.")
        frac = a3.number_input("Fracción promediada", 0.1, 0.8, float((previo or BASE)["fraccion_promedio"]), 0.05,
                               help="Fracción final del tiempo simulado que se promedia para medir el resalto.")

    # --- convertir la tabla a casos (y1, Fr1)
    casos, filas, errores, avisos = [], [], [], []
    p = json.loads(json.dumps(BASE))
    p.update({"nucleos": int(nucleos), "factor_cola": float(factor_cola),
              "cd_umbral": float(cd_umbral), "fraccion_promedio": float(frac)})
    p["malla"]["celdas_en_y1"] = MALLAS[malla_nom]
    total_seg = 0.0
    for i, r in tabla.reset_index(drop=True).iterrows():
        try:
            vals = [float(r[c]) for c in tabla.columns if c != COL_L]
        except (TypeError, ValueError):
            continue
        if any(math.isnan(v) or v <= 0 for v in vals):
            continue
        try:
            L_poza = float(r[COL_L])
            L_poza = L_poza if L_poza > 0 else None
        except (TypeError, ValueError, KeyError):
            L_poza = None
        if L_poza is not None and math.isnan(L_poza):
            L_poza = None
        extra = {}
        if modo == modos[0]:
            Q, B, a = vals
            q, y1 = Q / B, cc * a
            extra = {"Q": Q, "B": B, "abertura": a, "Cc": cc}
        elif modo == modos[1]:
            q, y1 = vals
        else:
            y1, fr = vals
            q = fr * math.sqrt(G * y1) * y1
        fr1 = q / math.sqrt(G * y1 ** 3)
        nombre = f"C{i + 1:02d}"
        fila = {"Caso": nombre, "q (m²/s)": q, "y1 (m)": y1, "V1 (m/s)": q / y1, "Fr1": fr1}
        if modo == modos[0]:
            fila = {"Caso": nombre, "Q (m³/s)": extra["Q"], **{k: v for k, v in fila.items() if k != "Caso"}}
        estado = "✅ listo"
        if fr1 <= 1.0:
            estado = "⛔ subcrítico: no hay resalto"
            errores.append(nombre)
        else:
            try:
                if L_poza:
                    extra["L_poza"] = L_poza
                d = disenar_caso(fr1, p, y1=y1, nombre=nombre, L_poza=L_poza)
                n = numero_celdas(d)
                t_caso = max(5.0, round(renov * d.L_canal / (d.q / d.y_cola) * 2) / 2)
                # 60 guardados; el tiempo final es múltiplo exacto del intervalo para que
                # OpenFOAM guarde también el último instante.
                intervalo = round(t_caso / 60, 4)
                t_caso = round(intervalo * 60, 4)
                extra.update({"tiempo_final": t_caso, "intervalo_escritura": intervalo})
                seg = tiempo_estimado(n, d.v1, t_caso, d.dx, nucleos)
                total_seg += seg
                fila.update({"y2 teórico (m)": d.y2_belanger, "Umbral P (m)": d.p_umbral,
                             "L poza (m)": f"{L_poza:.2f}" if L_poza else "auto",
                             "L ref. ≈ 6·y2 (m)": 6 * d.y2_belanger,
                             "Celdas": n, "t simulado (s)": t_caso, "Tiempo aprox.": fmt_min(seg)})
                if fr1 > 9:
                    estado = "⚠️ Fr > 9: fuera del rango probado"
                    avisos.append(nombre)
                elif n > 150_000:
                    estado = "⚠️ malla grande: corrida larga"
                    avisos.append(nombre)
                if modo == modos[0] and extra["B"] < d.y2_belanger:
                    estado = "⚠️ canal muy angosto: el modelo 2D ignora las paredes"
                    avisos.append(nombre)
                if L_poza and L_poza < 4.5 * d.y2_belanger:
                    estado = "⚠️ poza corta frente a ~6·y2: el resalto podría salirse"
                    avisos.append(nombre)
                casos.append({"nombre": nombre, "y1": y1, "Fr1": fr1, **extra})
            except ValueError:
                estado = "⛔ Fr bajo (< ~3.5): el umbral no logra el tirante aguas abajo"
                errores.append(nombre)
        fila["Estado"] = estado
        filas.append(fila)

    st.markdown("#### Vista previa")
    if filas:
        st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.3f")
                                    for c in ["Q (m³/s)", "q (m²/s)", "y1 (m)", "V1 (m/s)", "Fr1",
                                              "y2 teórico (m)", "Umbral P (m)", "L ref. ≈ 6·y2 (m)"]})
        m1, m2, m3 = st.columns(3)
        m1.metric("Casos válidos", len(casos))
        m2.metric("Tiempo total estimado", fmt_min(total_seg) if casos else "—",
                  help="Estimación basada en una corrida de referencia; depende de tu PC.")
        m3.metric("Con avisos o errores", len(set(avisos + errores)))
    else:
        st.info("Completa al menos una fila de la tabla.")

    destino = ESTUDIOS / slug(nombre_est)
    ya_existe = destino.exists() and (destino / "parametros.json").exists() and not previo
    if ya_existe:
        st.warning(f"Ya existe un estudio llamado **{slug(nombre_est)}**. Ábrelo desde la barra lateral o usa otro nombre.")

    b1, b2, _ = st.columns([1, 1, 3])
    puede = bool(casos) and not ya_existe
    guardar = b1.button("💾 Guardar", disabled=not puede, width="stretch")
    correr = b2.button("▶ Guardar y ejecutar", type="primary", disabled=not (puede and of),
                       width="stretch")
    if errores:
        st.caption("Las filas con ⛔ no se simularán.")

    if guardar or correr:
        destino.mkdir(parents=True, exist_ok=True)
        p.update({"casos": casos, "rama_openfoam": (of or {}).get("rama", "org"),
                  "interfaz": {"modo": modo, "renovaciones": renov, "tabla": tabla.to_dict(orient="list"),
                               **({"Cc": cc} if modo == modos[0] else {})}})
        (destino / "parametros.json").write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run([sys.executable, str(AQUI / "generar_casos.py"), "-p", str(destino / "parametros.json"),
                            "-o", str(destino / "casos")], capture_output=True, text=True, cwd=AQUI)
        if r.returncode != 0:
            st.error("No se pudieron crear los casos:\n\n" + r.stderr[-2000:])
        else:
            st.success(f"Estudio **{destino.name}** guardado ({len(casos)} casos).")
            with st.expander("Detalle"):
                st.code(r.stdout)
            if correr:
                cola.encolar(destino, nucleos)
                st.success("Simulación en cola. Sigue el avance en la pestaña **2 · Avance**. "
                           "Puedes cerrar el navegador: la corrida continúa.")


# ------------------------------------------------------------------ 2 · avance
@st.fragment(run_every="5s")
def panel_avance():
    t = cola.en_curso()
    if not t:
        st.info("No hay ninguna simulación corriendo.")
    else:
        est = Path(t["estudio"])
        mins = (time.time() - t.get("inicio", time.time())) / 60
        st.markdown(f"### Corriendo: {est.name}")
        st.caption(f"Iniciado hace {mins:.0f} min · {t['nucleos']} núcleos")
        for caso in sorted((est / "casos").glob("*")):
            if not (caso / "system").exists():
                continue
            a = cola.avance_caso(caso)
            etiqueta = f"{caso.name}: t = {a['t']:.1f} de {a['t_fin']:g} s" if a["t_fin"] else caso.name
            st.progress(a["fraccion"], text=etiqueta)
        if st.button("⏹ Detener esta corrida", type="secondary"):
            cola.detener()
            st.warning("Corrida detenida. Si la vuelves a ejecutar, continúa desde el último tiempo guardado.")
        log = est / "corrida.log"
        if log.exists():
            with st.expander("Registro (últimas líneas)"):
                st.code("\n".join(log.read_text(errors="ignore").splitlines()[-25:]))

    pend = cola.pendientes()
    if pend:
        st.markdown("#### En espera")
        for j in pend:
            c1, c2 = st.columns([4, 1])
            c1.write(f"{Path(j['estudio']).name} · {j['nucleos']} núcleos")
            if c2.button("Quitar", key=f"q_{j['id']}"):
                cola.quitar_pendiente(j["id"])
                st.rerun()
    hist = cola.historial(8)
    if hist:
        st.markdown("#### Últimas corridas")
        st.dataframe(pd.DataFrame([{"Estudio": Path(h["estudio"]).name, "Estado": h["estado"],
                                    "Terminó": time.strftime("%d/%m %H:%M", time.localtime(h["terminado"]))}
                                   for h in hist]), hide_index=True, width="stretch")


with tab_avance:
    panel_avance()


# ------------------------------------------------------------------ 3 · resultados
SEMAFORO = {"libre": "🟢 libre", "libre (cuasi-estacionario)": "🟢 libre (deriva lenta)"}
CONSEJO = {
    "ahogado": "El tirante aguas abajo es alto: baja **factor_cola** (p. ej. −0.03) y vuelve a correr ese caso.",
    "barrido": "El resalto **no se formó dentro de la poza**: la lámina rápida llega hasta el umbral. "
               "En revisión de diseño significa que **la poza es corta** para este caudal (alárgala). "
               "En validación (sin longitud), sube **factor_cola** (p. ej. +0.03).",
    "en movimiento": "El resalto aún se desplaza: aumenta el **tiempo simulado** o ajusta **factor_cola**.",
}

with tab_res:
    est_res = st.selectbox("Estudio", estudios_existentes() or ["—"], key="est_res")
    carpeta = ESTUDIOS / est_res
    res = carpeta / "resultados"
    if est_res == "—":
        st.info("Aún no hay estudios.")
    else:
        if st.button("🔄 Recalcular resultados"):
            with st.spinner("Midiendo el resalto en cada caso…"):
                r = subprocess.run([sys.executable, str(AQUI / "postproceso.py"), "-d", str(carpeta / "casos"),
                                    "-o", str(res)], capture_output=True, text=True, cwd=AQUI)
            st.code(r.stdout[-3000:] or r.stderr[-3000:])
        csvf = res / "resultados.csv"
        if not csvf.exists():
            st.info("Este estudio aún no tiene resultados. Se generan solos al terminar la corrida, "
                    "o con **Recalcular resultados**.")
        else:
            df = pd.read_csv(csvf)
            df["Estado"] = [SEMAFORO.get(e, "🔴 " + e if ("ahogado" in e or "barrido" in e) else "🟡 " + e)
                            for e in df["estado"]]
            vista = df[["caso", "fr_pie", "y1_pie_m", "y2_sim_m", "y2_y1_sim", "y2_y1_belanger",
                        "error_pct", "L_resalto_sobre_y2", "Estado"]
                       + (["resalto_contenido"] if "resalto_contenido" in df else [])].rename(columns={
                "caso": "Caso", "fr_pie": "Fr en el pie", "y1_pie_m": "y1 (m)", "y2_sim_m": "y2 (m)",
                "y2_y1_sim": "y2/y1 simulado", "y2_y1_belanger": "y2/y1 Bélanger",
                "error_pct": "Error (%)", "L_resalto_sobre_y2": "Longitud / y2",
                "resalto_contenido": "¿Contenido en la poza?"})
            no_valido = ~df["estado"].str.startswith("libre")
            for col in ("y2_y1_sim", "error_pct", "L_resalto_sobre_y2"):
                df.loc[no_valido, col] = float("nan")   # sin resalto válido, el número no significa nada
            libres = df[~no_valido]
            k1, k2, k3 = st.columns(3)
            k1.metric("Casos válidos", f"{len(libres)} de {len(df)}")
            if len(libres):
                k2.metric("Error medio vs. Bélanger", f"{libres['error_pct'].mean():+.1f} %")
                k3.metric("Error máximo", f"{libres['error_pct'].abs().max():.1f} %")
            st.dataframe(vista, hide_index=True, width="stretch",
                         column_config={c: st.column_config.NumberColumn(format="%.2f")
                                        for c in vista.columns if c not in ("Caso", "Estado")})
            for _, f in df[~df["estado"].str.startswith("libre")].iterrows():
                clave = next((k for k in CONSEJO if k in f["estado"]), None)
                if clave:
                    st.warning(f"**{f['caso']}** ({f['estado']}): {CONSEJO[clave]}")
            g1, g2 = st.columns(2)
            if (res / "validacion_belanger.png").exists():
                g1.image(str(res / "validacion_belanger.png"), caption="Validación contra Bélanger")
            if (res / "perfiles.png").exists():
                g2.image(str(res / "perfiles.png"), caption="Perfiles de superficie libre")
            st.markdown("#### Ver en ParaView")
            casos_dir = sorted(c.name for c in (carpeta / "casos").glob("*") if (c / "system").exists())
            v1, v2 = st.columns([2, 1])
            caso_pv = v1.selectbox("Caso", casos_dir, key="caso_pv", label_visibility="collapsed")
            if v2.button("🔍 Abrir en ParaView", width="stretch"):
                ok, msg = abrir_paraview(carpeta / "casos" / caso_pv)
                (st.success if ok else st.error)(msg)
            st.caption("Se abre mostrando **alpha.water** (rojo = agua, azul = aire), de frente y en el último "
                       "tiempo. Con ▶ ves cómo se forma el resalto; para la velocidad, cambia el campo a **U**.")
            st.markdown("#### 🎬 Video del resalto")
            st.caption("Renderiza la formación del resalto (agua/aire) con ParaView sin abrir ventanas y la "
                       "convierte en MP4. Tarda entre 30 s y unos minutos según el caso.")
            f1, f2, f3 = st.columns([2, 2, 1])
            nombres_formato = {
                "horizontal": "Horizontal 1920×1080 (recomendado: el canal es alargado)",
                "cuadrado": "Cuadrado 1080×1080 (feed de LinkedIn)",
                "vertical": "Vertical 1080×1350 (feed en celular)",
            }
            formato = f1.selectbox("Formato", list(nombres_formato), format_func=nombres_formato.get)
            dur = f2.slider("Duración del video (s)", 4, 20, 8,
                            help="Se reparte entre todos los tiempos guardados. Más segundos = más lento.")
            fondo = f3.radio("Fondo", ["Claro", "Oscuro"], help="Claro se ve más limpio en publicaciones.")
            titulo = st.text_input("Título en el video (opcional)", value="",
                                   placeholder="Ej.: Poza de 10.2 m · Q = 9 m³/s")
            if st.button(f"🎬 Crear video de {caso_pv}"):
                with st.spinner("Renderizando cuadros y armando el MP4…"):
                    ok, msg, mp4 = video.crear_video(carpeta / "casos" / caso_pv, formato, dur,
                                                     fondo == "Claro", titulo)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
            for mp4 in sorted((carpeta / "videos").glob(f"{caso_pv}_*.mp4")) if (carpeta / "videos").exists() else []:
                st.video(str(mp4))
                st.download_button(f"⬇ Descargar {mp4.name}", mp4.read_bytes(), mp4.name, "video/mp4",
                                   key=f"dl_{mp4.name}")
            with st.expander("Perfil de cada caso"):
                for img in sorted(res.glob("perfil_*.png")):
                    st.image(str(img))
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in res.iterdir():
                    z.write(f, f.name)
                z.write(carpeta / "parametros.json", "parametros.json")
            st.download_button("⬇ Descargar resultados (zip)", buf.getvalue(), f"{est_res}_resultados.zip",
                               "application/zip")
            st.caption(f"Carpeta del estudio: `{carpeta}` · para ver los campos en 3D abre "
                       "`casos/<caso>/caso.foam` en ParaView.")


# ------------------------------------------------------------------ ayuda
with tab_ayuda:
    st.markdown("""
### Cómo se usa
1. **Datos:** elige cómo ingresar el caudal, completa la tabla y revisa la **vista previa**.
   Las filas con ⛔ no se pueden simular con este modelo; las ⚠️ sí, con precaución.
2. **Guardar y ejecutar:** la simulación entra en la **cola** y corre en segundo plano.
   Puedes cerrar el navegador; la corrida sigue. Solo se detiene con **Detener** o si la PC se apaga o suspende.
3. **Avance:** barra por caso con el tiempo simulado.
4. **Resultados:** tabla, gráfica contra Bélanger y perfiles. Se generan solos al terminar.
   - **🔄 Recalcular resultados:** vuelve a medir el resalto y a dibujar las gráficas con lo que ya se
     simuló, **sin volver a simular** (toma segundos). Úsalo si actualizaste el programa o si las
     gráficas no aparecen.
   - **🔍 Abrir en ParaView:** abre el caso ya configurado para ver el resalto.
   - **🎬 Crear video:** genera un MP4 de la formación del resalto, listo para compartir.

### Dos formas de usarla
- **Validación** (columna *Longitud poza* vacía): el programa da una poza holgada para que el resalto
  se forme libre y compara con Bélanger.
- **Revisión de diseño** (con *Longitud poza*): usa tu longitud real y te dice si el resalto queda
  **contenido** en la poza. El umbral final se dimensiona solo y el tirante aguas abajo se toma como el
  conjugado ideal (condición de diseño); el tirante real del cauce se calcula con Manning, aún no incluido.

### Qué significa el estado de cada caso
- 🟢 **libre**: el resalto se formó lejos de la compuerta y del umbral; la medición es válida.
- 🟡 **en movimiento**: el resalto aún se desplaza; alarga el tiempo o ajusta *factor_cola*.
- 🔴 **ahogado**: el resalto se pegó a la compuerta (tirante aguas abajo alto) → baja *factor_cola*.
- 🔴 **barrido**: el resalto se fue hacia el umbral (tirante bajo) → sube *factor_cola*.

### Límites del modelo
- 2D (canal ancho, sin efecto de paredes) y turbulencia promediada (k-ω SST).
- Froude de entrada desde ~3.5; probado hasta 8.
- Para cambiar un caso ya corrido, crea un estudio nuevo (los estudios no se sobrescriben).

""")
