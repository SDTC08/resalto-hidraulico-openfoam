"""
generar_casos.py — Crea una carpeta de caso OpenFOAM por cada Fr1 del barrido.

Uso:
    python generar_casos.py                 # crea los casos que aún no existen
    python generar_casos.py --casos Fr7p00 --forzar   # rehace solo ese caso
    python generar_casos.py -p otro.json -o casos_malla_fina

Por seguridad, un caso que ya existe NO se toca salvo con --forzar.

Solo necesita Python 3 (sin librerías externas); puede correr en Windows.
"""
import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

from hidraulica import disenar_caso
from malla import generar_blockmesh, numero_celdas

AQUI = Path(__file__).resolve().parent


def elegir_plantilla(p: dict) -> Path:
    """
    'org' -> OpenFOAM de openfoam.org (v11+: foamRun -solver incompressibleVoF)
    'esi' -> OpenFOAM de openfoam.com (interFoam)
    'auto' -> según $WM_PROJECT_VERSION (si empieza con 'v' es ESI).
    """
    rama = p.get("rama_openfoam", "auto").lower()
    if rama == "auto":
        version = os.environ.get("WM_PROJECT_VERSION", "")
        if not version:
            sys.exit("rama_openfoam='auto' pero OpenFOAM no está cargado: "
                     "cárguelo o ponga 'org' o 'esi' en parametros.json")
        rama = "esi" if version.lower().startswith("v") else "org"
    if rama not in ("org", "esi"):
        sys.exit(f"rama_openfoam desconocida: {rama}")
    return AQUI / f"plantilla_{rama}"


def turbulencia_entrada(v1, y1, p):
    """k y omega de entrada a partir de intensidad y escala de longitud (estimación usual)."""
    t = p["turbulencia_entrada"]
    k = 1.5 * (t["intensidad"] * v1) ** 2
    ell = t["escala_en_y1"] * y1
    omega = math.sqrt(k) / (0.09 ** 0.25 * ell)
    return k, omega


def lista_casos(p: dict):
    """
    Casos a generar. Dos formatos en parametros.json:
      - "casos": [{"nombre": "C01", "y1": 0.04, "Fr1": 4.0, ...}, ...]
        (lo escribe la interfaz; cada caso con su propio y1 y datos extra)
      - "Fr1": [4, 5, ...] con un "y1" común (formato original)
    """
    if p.get("casos"):
        return [dict(c) for c in p["casos"]]
    return [{"nombre": None, "y1": p["y1"], "Fr1": f} for f in p["Fr1"]]


def crear_caso(c: dict, p, destino: Path, plantilla: Path, forzar=False):
    d = disenar_caso(c["Fr1"], p, y1=c["y1"], nombre=c.get("nombre"), L_poza=c.get("L_poza"))
    caso = destino / d.nombre
    if caso.exists():
        if not forzar:
            return d, None
        shutil.rmtree(caso)
    shutil.copytree(plantilla, caso)

    k_in, w_in = turbulencia_entrada(d.v1, d.y1, p)
    reemplazos = {
        "@@V1@@": f"{d.v1:.6f}",
        "@@G@@": f"{d.g:.4f}",
        "@@K_IN@@": f"{k_in:.6e}",
        "@@OMEGA_IN@@": f"{w_in:.6e}",
        "@@T_FINAL@@": f"{c.get('tiempo_final', p['tiempo_final'])}",
        "@@T_ESCRITURA@@": f"{c.get('intervalo_escritura', p['intervalo_escritura'])}",
        "@@NP@@": str(p["nucleos"]),
        "@@X_INI@@": f"{p['x_inicial_resalto_en_L'] * d.L_canal:.6f}",
        "@@X_FIN_COLA@@": f"{d.L_canal + d.L_cresta:.6f}",
        "@@Y1@@": f"{d.y1:.6f}",
        "@@Y_COLA@@": f"{d.p_umbral + d.h_umbral:.6f}",
        "@@V_COLA@@": f"{d.q / max(d.y_cola, d.y1):.6f}",
    }
    for f in caso.rglob("*"):
        if f.is_file():
            txt = f.read_text()
            for clave, valor in reemplazos.items():
                txt = txt.replace(clave, valor)
            f.write_text(txt, newline="\n")

    (caso / "system" / "blockMeshDict").write_text(generar_blockmesh(d), newline="\n")
    meta = d.a_dict()
    meta.update({k: v for k, v in c.items() if k not in ("nombre", "y1", "Fr1")})
    meta.update({"k_entrada": k_in, "omega_entrada": w_in,
                 "tiempo_final": c.get("tiempo_final", p["tiempo_final"]), "nucleos": p["nucleos"],
                 "celdas_aprox": numero_celdas(d),
                 "fraccion_promedio": p["fraccion_promedio"]})
    (caso / "diseno.json").write_text(json.dumps(meta, indent=2), newline="\n")
    (caso / "caso.foam").write_text("")  # para abrir en ParaView
    return d, meta["celdas_aprox"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--parametros", default=str(AQUI / "parametros.json"))
    ap.add_argument("-o", "--salida", default=str(AQUI / "casos"))
    ap.add_argument("--casos", nargs="*", help="solo estos casos (p. ej. Fr7p00)")
    ap.add_argument("--forzar", action="store_true", help="borra y rehace los casos indicados")
    a = ap.parse_args()

    p = json.loads(Path(a.parametros).read_text(encoding="utf-8"))
    destino = Path(a.salida)
    destino.mkdir(parents=True, exist_ok=True)
    plantilla = elegir_plantilla(p)
    print(f"Plantilla: {plantilla.name}\n")

    print(f"{'caso':<10}{'V1 m/s':>8}{'q m2/s':>9}{'y2_B m':>8}{'P umbral':>10}{'L canal':>9}{'celdas':>9}")
    for c in lista_casos(p):
        try:
            nombre = disenar_caso(c["Fr1"], p, y1=c["y1"], nombre=c.get("nombre"), L_poza=c.get("L_poza")).nombre
            if a.casos and nombre not in a.casos:
                continue
            d, n = crear_caso(c, p, destino, plantilla, a.forzar)
        except ValueError as e:
            print(f"OMITIDO  {e}")
            continue
        if n is None:
            print(f"{d.nombre:<10}  ya existe: no se modifica (use --forzar para rehacerlo)")
            continue
        print(f"{d.nombre:<10}{d.v1:>8.3f}{d.q:>9.4f}{d.y2_belanger:>8.3f}"
              f"{d.p_umbral:>10.3f}{d.L_canal:>9.2f}{n:>9,}")
    print(f"\nCasos creados en: {destino}")


if __name__ == "__main__":
    main()
