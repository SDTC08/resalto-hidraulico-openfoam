"""
postproceso.py — Mide el resalto en cada caso y lo valida contra Bélanger.

Para cada caso:
  1. Lee la malla (constant/polyMesh) y alpha.water de los tiempos escritos.
  2. Calcula el calado h(x) = suma de alpha*dz en cada columna de celdas,
     promediado en la última fracción del tiempo simulado.
  3. Ubica el pie (x_pie) y el final (x_fin) del resalto a partir de la
     pendiente de la superficie libre, y mide y1 (pie) e y2 (cola).
  4. Calcula Fr en el pie con el caudal impuesto y compara y2/y1 con Bélanger.

Salidas en ./resultados: resultados.csv, validacion_belanger.png,
perfiles.png y un perfil por caso.

Solo necesita numpy y matplotlib; puede correr en Windows sobre los
resultados copiados desde WSL.
"""
import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from hidraulica import belanger

AQUI = Path(__file__).resolve().parent

# Paleta serena (azules suaves, verde azulado, salvia, lavanda)
C_TEORIA = "#2F6F73"   # verde azulado profundo
C_SIM = "#6FA8DC"      # azul suave
C_BORDE = "#2B4C6F"
C_BANDA = "#CFE3E0"
C_AGUA = "#9CC7D9"
C_PIE = "#8FB996"      # salvia
C_FIN = "#A99CC9"      # lavanda
C_TEXTO = "#33424F"
CICLO = ["#4F86B5", "#5FA8A0", "#8FB996", "#A99CC9", "#7FA7C9", "#6E9C8E", "#B3A4D6"]


# --------------------------------------------------------------- lectura OpenFOAM
def _cuerpo_lista(txt: str):
    """Devuelve el contenido entre el primer 'N\\n(' y su ')' final."""
    txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.S)
    txt = re.sub(r"//.*", "", txt)
    m = re.search(r"FoamFile\s*\{.*?\}", txt, flags=re.S)
    if m:
        txt = txt[m.end():]
    m = re.search(r"(\d+)\s*\(", txt)
    n = int(m.group(1))
    ini = m.end()
    fin = txt.rindex(")")
    return n, txt[ini:fin]


def leer_puntos(f: Path):
    n, cuerpo = _cuerpo_lista(f.read_text())
    v = np.array(re.findall(r"[-+0-9.eE]+", cuerpo), dtype=float)
    return v.reshape(n, 3)


def leer_caras(f: Path):
    n, cuerpo = _cuerpo_lista(f.read_text())
    caras = [list(map(int, g.split())) for g in re.findall(r"\d+\s*\(([^)]*)\)", cuerpo)]
    assert len(caras) == n, "faces: lectura incompleta (¿formato binario?)"
    return caras


def leer_etiquetas(f: Path):
    n, cuerpo = _cuerpo_lista(f.read_text())
    v = np.array(cuerpo.split(), dtype=int)
    assert len(v) == n
    return v


def leer_escalar(f: Path, n_celdas: int):
    txt = f.read_text()
    m = re.search(r"internalField\s+uniform\s+([-+0-9.eE]+)", txt)
    if m:
        return np.full(n_celdas, float(m.group(1)))
    m = re.search(r"internalField\s+nonuniform\s+List<scalar>\s*(\d+)\s*\(", txt)
    if not m:
        raise ValueError(f"No se pudo leer {f} (¿writeFormat binary?)")
    n = int(m.group(1))
    fin = txt.index(")", m.end())
    v = np.array(txt[m.end():fin].split(), dtype=float)
    assert len(v) == n == n_celdas
    return v


def geometria_celdas(caso: Path):
    """Caja envolvente de cada celda (la malla es de hexaedros alineados)."""
    pm = caso / "constant" / "polyMesh"
    P = leer_puntos(pm / "points")
    caras = leer_caras(pm / "faces")
    own = leer_etiquetas(pm / "owner")
    nei = leer_etiquetas(pm / "neighbour")
    n_celdas = own.max() + 1
    fmin = np.array([P[c].min(axis=0) for c in caras])
    fmax = np.array([P[c].max(axis=0) for c in caras])
    cmin = np.full((n_celdas, 3), np.inf)
    cmax = np.full((n_celdas, 3), -np.inf)
    for etiquetas, rango in ((own, slice(None)), (nei, slice(0, len(nei)))):
        np.minimum.at(cmin, etiquetas, fmin[rango])
        np.maximum.at(cmax, etiquetas, fmax[rango])
    return 0.5 * (cmin + cmax), cmax - cmin


def tiempos(caso: Path):
    t = []
    for d in caso.iterdir():
        if d.is_dir() and (d / "alpha.water").exists():
            try:
                t.append((float(d.name), d))
            except ValueError:
                pass
    return sorted(t)


# --------------------------------------------------------------- análisis
def perfil_calado(caso: Path, d: dict, fraccion: float):
    centros, tam = geometria_celdas(caso)
    n = len(centros)
    ts = [(t, p) for t, p in tiempos(caso) if t > 0]
    if not ts:
        raise RuntimeError("no hay tiempos escritos > 0 (¿no se corrió interFoam?)")
    t_fin = ts[-1][0]
    usados = [(t, p) for t, p in ts if t >= (1 - fraccion) * t_fin] or ts[-1:]

    # Solo el canal (antes del umbral), donde el fondo está en z = 0
    sel = centros[:, 0] < d["L_canal"]
    col = np.floor(centros[sel, 0] / d["dx"]).astype(int)
    columnas, inv = np.unique(col, return_inverse=True)
    x = np.array([centros[sel, 0][inv == k].mean() for k in range(len(columnas))])

    perfiles = []
    for _, p in usados:
        a = leer_escalar(p / "alpha.water", n)[sel]
        h = np.bincount(inv, weights=a * tam[sel, 2], minlength=len(columnas))
        perfiles.append(h)
    perfiles = np.array(perfiles)
    return x, perfiles, [t for t, _ in usados]


def suavizar(h, n):
    if n < 2:
        return h
    k = np.ones(n) / n
    hp = np.pad(h, (n // 2, n - 1 - n // 2), mode="edge")
    return np.convolve(hp, k, mode="valid")


def medir_resalto(x, h, d):
    """
    Criterio (convención documentada en el README):
      - y_cola: mediana de h en el último 25 % del canal útil.
      - pie: primer punto donde la superficie media supera al calado de
        entrada en un 5 % del salto total (y_cola - y_entrada).
      - y1: valor en el pie de la recta ajustada al tramo supercrítico
        (descuenta la subida propia del resalto).
      - fin: primer punto aguas abajo del frente (90 % del salto) desde el
        cual h se mantiene dentro de ±5 % de y_cola en una distancia y_cola.
      - y2: calado medio entre el fin del resalto y 2·H_umbral antes del umbral.
    """
    dx = d["dx"]
    hs = suavizar(h, max(1, int(round(2 * d["y1"] / dx))))
    zona = (x > 3 * d["y1"]) & (x < d["L_canal"] - 2 * d["h_umbral"])
    if zona.sum() < 10:
        raise RuntimeError("canal demasiado corto para medir")
    idx = np.where(zona)[0]
    i0, i_ult = idx[0], idx[-1]

    y_cola = float(np.median(h[idx[int(0.75 * len(idx)):]]))
    y_ent = float(hs[i0])
    salto = y_cola - y_ent
    # Ahogado: junto a la compuerta ya no hay lámina supercrítica (h >> y1).
    # Sin resalto: hay lámina supercrítica pero el agua no sube dentro de la
    # zona medible -> el resalto fue empujado contra el umbral (no contenido).
    ahogado = y_ent > 1.3 * d["y1"]
    sin_resalto = (not ahogado) and salto < 0.5 * (d["y2_belanger"] - d["y1"])

    rel = idx[hs[idx] >= y_ent + 0.05 * salto]
    i_pie = int(rel[0]) if len(rel) else i_ult
    rel = idx[hs[idx] >= y_ent + 0.90 * salto]
    i_90 = int(rel[0]) if len(rel) else i_ult

    margen = max(2, int(round(2 * d["y1"] / dx)))
    tramo = np.arange(i0, max(i0, i_pie - margen))
    if len(tramo) >= 3:
        a1, a0 = np.polyfit(x[tramo], hs[tramo], 1)
        y1 = float(a0 + a1 * x[i_pie])
    else:
        y1 = y_ent

    paso = max(2, int(round(y_cola / dx)))
    i_fin = i_90
    while i_fin < i_ult - paso and not np.all(np.abs(hs[i_fin:i_fin + paso] / y_cola - 1) < 0.05):
        i_fin += 1

    cola = (x >= x[i_fin]) & (x <= d["L_canal"] - 2 * d["h_umbral"])
    y2 = float(h[cola].mean()) if cola.sum() >= 3 else float(hs[i_fin])

    fr_pie = d["q"] / np.sqrt(d["g"] * y1 ** 3)
    estado = "libre"
    if ahogado:
        estado = "ahogado (pie en la compuerta)"
    elif (sin_resalto or i_pie >= i_ult - paso or not cola.any()
          or x[i_fin] > d["L_canal"] - 2 * d["h_umbral"]):
        estado = "barrido hacia el umbral (no contenido)"
    return {
        "x_pie": float(x[i_pie]), "x_fin": float(x[i_fin]),
        "y1_pie": y1, "y2_sim": y2, "fr_pie": float(fr_pie),
        "ratio_sim": y2 / y1, "ratio_belanger": belanger(fr_pie),
        "L_resalto": float(x[i_fin] - x[i_pie]), "estado": estado,
        "h_suave": hs,
    }


def deriva_del_pie(x, perfiles, ts, d):
    """Velocidad media del pie [m/s] en la ventana promediada (0 = estacionario)."""
    xs = []
    for h in perfiles:
        try:
            xs.append(medir_resalto(x, h, d)["x_pie"])
        except Exception:  # noqa: BLE001
            xs.append(np.nan)
    xs = np.array(xs)
    ok = ~np.isnan(xs)
    if ok.sum() < 3:
        return float("nan"), xs
    return float(np.polyfit(np.array(ts)[ok], xs[ok], 1)[0]), xs


# --------------------------------------------------------------- gráficas
def estilo():
    plt.rcParams.update({
        "figure.facecolor": "white", "axes.facecolor": "#FAFCFC",
        "axes.edgecolor": "#B8C7CC", "axes.labelcolor": C_TEXTO,
        "xtick.color": C_TEXTO, "ytick.color": C_TEXTO, "text.color": C_TEXTO,
        "axes.grid": True, "grid.color": "#E3ECEE", "grid.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False,
        "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold",
    })


def graficar_validacion(filas, salida: Path):
    fr = np.linspace(1.0, max(9.0, max(f["fr_pie"] for f in filas) + 0.5), 300)
    teo = np.array([belanger(f) for f in fr])
    fig, ax = plt.subplots(figsize=(7.2, 4.8))
    ax.fill_between(fr, 0.95 * teo, 1.05 * teo, color=C_BANDA, lw=0, label="Bélanger ±5 %")
    ax.plot(fr, teo, color=C_TEORIA, lw=2, label="Bélanger: $y_2/y_1=\\frac{1}{2}(\\sqrt{1+8Fr_1^2}-1)$")
    for f in filas:
        libre = f["estado"].startswith("libre")
        ax.scatter(f["fr_pie"], f["ratio_sim"], s=70, zorder=3,
                   color=C_SIM if libre else "white", edgecolor=C_BORDE, lw=1.4)
        ax.annotate(f"{f['error_pct']:+.1f} %", (f["fr_pie"], f["ratio_sim"]),
                    textcoords="offset points", xytext=(8, -12), fontsize=8.5, color=C_BORDE)
    ax.scatter([], [], s=70, color=C_SIM, edgecolor=C_BORDE, label="OpenFOAM (VOF, k-ω SST)")
    if any(not f["estado"].startswith("libre") for f in filas):
        ax.scatter([], [], s=70, color="white", edgecolor=C_BORDE, label="Resalto no válido (revisar)")
    ax.set_xlabel("Número de Froude en el pie del resalto, $Fr_1$ [-]")
    ax.set_ylabel("Relación de tirantes conjugados, $y_2/y_1$ [-]")
    ax.set_title("Resalto hidráulico: simulación vs. Bélanger")
    ax.set_xlim(fr[0], fr[-1])
    ax.set_ylim(0, teo.max() * 1.08)
    ax.legend(frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(salida, dpi=160)
    plt.close(fig)


def graficar_perfil(nombre, x, h, s, m, d, salida: Path):
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.fill_between(x, 0, h, color=C_AGUA, alpha=0.55, lw=0)
    ax.fill_between(x, h - s, h + s, color=C_BORDE, alpha=0.12, lw=0, label="Oscilación (±1σ)")
    ax.plot(x, h, color=C_BORDE, lw=1.2, label="Superficie media")
    ax.axvline(m["x_pie"], color=C_PIE, lw=2, ls="--", label=f"Pie del resalto: y₁ = {m['y1_pie']:.2f} m")
    ax.axvline(m["x_fin"], color=C_FIN, lw=2, ls="--", label=f"Fin del resalto: y₂ = {m['y2_sim']:.2f} m")
    ax.axhline(d["y2_belanger"], color=C_TEORIA, lw=1, ls=":", label="y₂ teórico (Bélanger)")
    ax.set_xlim(0, d["L_canal"])
    ax.set_ylim(0, max(h.max(), d["y2_belanger"]) * 1.6)
    ax.set_xlabel("Distancia desde la compuerta [m]")
    ax.set_ylabel("Tirante h [m]")
    ax.set_title(f"{nombre} · Fr₁ en el pie = {m['fr_pie']:.2f} · Estado: {m['estado']}")
    ax.legend(frameon=True, framealpha=0.92, facecolor="#FAFCFC", edgecolor="none", fontsize=8, loc="upper left", ncol=2)
    fig.tight_layout()
    fig.savefig(salida, dpi=160)
    plt.close(fig)


def graficar_perfiles(datos, salida: Path):
    fig, ax = plt.subplots(figsize=(9, 4))
    for k, (nombre, x, h, d) in enumerate(datos):
        ax.plot(x / d["y1"], h / d["y1"], color=CICLO[k % len(CICLO)], lw=1.6,
                label=f"{nombre}  (Fr nominal {d['fr1']:.1f})")
    ax.set_xlabel("Distancia desde la compuerta / y₁ [-]")
    ax.set_ylabel("Tirante / y₁ [-]")
    ax.set_title("Perfiles de superficie libre adimensionales")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(salida, dpi=160)
    plt.close(fig)


# --------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-d", "--directorio", default=str(AQUI / "casos"))
    ap.add_argument("-o", "--salida", default=str(AQUI / "resultados"))
    a = ap.parse_args()

    estilo()
    salida = Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)
    filas, datos = [], []
    for caso in sorted(Path(a.directorio).iterdir()):
        if not (caso / "diseno.json").exists():
            continue
        d = json.loads((caso / "diseno.json").read_text())
        try:
            x, perf, ts = perfil_calado(caso, d, d.get("fraccion_promedio", 0.3))
            h, s = perf.mean(axis=0), perf.std(axis=0)
            m = medir_resalto(x, h, d)
            v_pie, _ = deriva_del_pie(x, perf, ts, d)
            # Un pie que se desplaza lento frente a V1 (< 2 %) da un resalto
            # cuasi-estacionario: y2/y1 sigue siendo válido (error ~ v_pie/V1).
            if np.isfinite(v_pie) and m["estado"] == "libre":
                if abs(v_pie) > 0.02 * d["v1"]:
                    m["estado"] = "en movimiento (alargar tiempo o ajustar cola)"
                elif abs(v_pie) * (ts[-1] - ts[0]) > 2 * d["y1"]:
                    m["estado"] = "libre (cuasi-estacionario)"
        except Exception as e:  # noqa: BLE001
            print(f"{caso.name}: omitido — {e}")
            continue
        err = 100 * (m["ratio_sim"] / m["ratio_belanger"] - 1)
        fila = {
            "caso": caso.name, "Fr_nominal": d["fr1"], "q_m2s": d["q"],
            "t_final_s": ts[-1], "n_tiempos_promediados": len(ts),
            "x_pie_m": m["x_pie"], "x_fin_m": m["x_fin"],
            "y1_pie_m": m["y1_pie"], "y2_sim_m": m["y2_sim"], "fr_pie": m["fr_pie"],
            "y2_y1_sim": m["ratio_sim"], "y2_y1_belanger": m["ratio_belanger"],
            "error_pct": err, "L_resalto_m": m["L_resalto"],
            "L_resalto_sobre_y2": m["L_resalto"] / m["y2_sim"],
            "velocidad_pie_m_s": v_pie, "estado": m["estado"],
            "L_poza_m": d.get("L_poza", float("nan")),
            "resalto_contenido": ("—" if not d.get("L_poza") else
                                  "sí" if (m["estado"].startswith("libre") and m["x_fin"] <= d["L_poza"]) else "no"),
        }
        filas.append({**fila, "fr_pie": m["fr_pie"], "ratio_sim": m["ratio_sim"]})
        datos.append((caso.name, x, h, d))
        graficar_perfil(caso.name, x, h, s, m, d, salida / f"perfil_{caso.name}.png")
        print(f"{caso.name}: Fr_pie={m['fr_pie']:.2f}  y2/y1 sim={m['ratio_sim']:.2f}  "
              f"Bélanger={m['ratio_belanger']:.2f}  error={err:+.1f} %  ({m['estado']})")

    if not filas:
        print("No hay casos con resultados.")
        return
    columnas = [k for k in filas[0] if k not in ("ratio_sim",)]
    with open(salida / "resultados.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columnas, extrasaction="ignore")
        w.writeheader()
        for fila in filas:
            w.writerow({k: (round(v, 5) if isinstance(v, float) else v) for k, v in fila.items()})
    graficar_validacion(filas, salida / "validacion_belanger.png")
    graficar_perfiles(datos, salida / "perfiles.png")
    print(f"\nResultados en: {salida}")


if __name__ == "__main__":
    main()
