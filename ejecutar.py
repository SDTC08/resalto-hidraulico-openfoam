"""
ejecutar.py — Corre el barrido completo en OpenFOAM.

Detecta el ramal por el controlDict de cada caso:
  - openfoam.org (v11+): 'solver incompressibleVoF;' -> foamRun
  - openfoam.com (ESI):  'application interFoam;'    -> interFoam (+ setFields)

Debe ejecutarse en una terminal donde OpenFOAM esté cargado, por ejemplo:

    python3 ejecutar.py                    # todos los casos de ./casos
    python3 ejecutar.py --np 4             # en paralelo con 4 núcleos
    python3 ejecutar.py --casos Fr4p00 Fr6p00
    python3 ejecutar.py --solo-malla       # solo blockMesh + checkMesh

Cada paso escribe su log.<programa> dentro de la carpeta del caso.
Si una corrida se interrumpe, al volver a lanzarla continúa desde el
último tiempo escrito (startFrom latestTime).
"""
import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent


def correr(cmd, caso: Path, log: str):
    t0 = time.time()
    with open(caso / f"log.{log}", "w") as f:
        r = subprocess.run(cmd, cwd=caso, stdout=f, stderr=subprocess.STDOUT)
    estado = "ok" if r.returncode == 0 else f"ERROR ({r.returncode}) -> revisar log.{log}"
    print(f"    {log:<22}{time.time() - t0:8.1f} s  {estado}")
    if r.returncode != 0:
        raise RuntimeError(f"{caso.name}: falló {' '.join(cmd)}")


def tiempos_escritos(caso: Path):
    out = []
    for d in caso.iterdir():
        try:
            out.append(float(d.name))
        except ValueError:
            pass
    return sorted(out)


def solver_de(caso: Path) -> str:
    txt = (caso / "system" / "controlDict").read_text()
    return "foamRun" if re.search(r"^\s*solver\s+\w+;", txt, re.M) else "interFoam"


def preparar(caso: Path):
    if not (caso / "0").exists():
        shutil.copytree(caso / "0.orig", caso / "0")
    correr(["blockMesh"], caso, "blockMesh")
    correr(["checkMesh"], caso, "checkMesh")
    if (caso / "system" / "setFieldsDict").exists():  # solo plantilla ESI
        correr(["setFields"], caso, "setFields")


def tiempo_final(caso: Path) -> float:
    txt = (caso / "system" / "controlDict").read_text()
    m = re.search(r"^\s*endTime\s+([0-9.eE+-]+);", txt, re.M)
    return float(m.group(1)) if m else float("inf")


def terminado(caso: Path) -> bool:
    t = tiempos_escritos(caso)
    return bool(t) and t[-1] >= tiempo_final(caso) - 1e-9


def resolver(caso: Path, np_: int):
    if terminado(caso):
        print(f"    ya terminado (t = {tiempos_escritos(caso)[-1]:g} s): se omite")
        return
    app = solver_de(caso)
    if shutil.which(app) is None:
        raise RuntimeError(f"no se encuentra {app}: ¿la plantilla corresponde a su versión de OpenFOAM?")
    if np_ <= 1:
        correr([app], caso, app)
        return
    if not (caso / "processor0").exists():
        try:
            correr(["decomposePar", "-force"], caso, "decomposePar")
        except RuntimeError:
            # Algunas instalaciones no traen scotch: se reintenta con 'simple',
            # que corta el canal en tramos a lo largo de x (adecuado para 2D).
            f = caso / "system" / "decomposeParDict"
            f.write_text(re.sub(r"method\s+\w+;.*", f"method          simple;\n\nsimpleCoeffs\n{{\n    n               ({np_} 1 1);\n}}\n",
                                f.read_text(), flags=re.S))
            print("    reintento con el método 'simple'")
            correr(["decomposePar", "-force"], caso, "decomposePar")
    correr(["mpirun", "-np", str(np_), app, "-parallel"], caso, app)
    correr(["reconstructPar", "-newTimes"], caso, "reconstructPar")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-d", "--directorio", default=str(AQUI / "casos"))
    ap.add_argument("--casos", nargs="*", help="nombres de casos (por defecto, todos)")
    ap.add_argument("--np", type=int, default=1, help="núcleos MPI (1 = serie)")
    ap.add_argument("--solo-malla", action="store_true")
    a = ap.parse_args()

    if shutil.which("blockMesh") is None:
        sys.exit("No se encuentra blockMesh: cargue el entorno de OpenFOAM antes de ejecutar.")

    raiz = Path(a.directorio)
    casos = sorted(c for c in raiz.iterdir() if (c / "system" / "blockMeshDict").exists())
    if a.casos:
        casos = [c for c in casos if c.name in a.casos]

    if a.np > 1:  # sincroniza el número de subdominios con --np
        for c in casos:
            f = c / "system" / "decomposeParDict"
            txt = f.read_text()
            f.write_text(re.sub(r"numberOfSubdomains\s+\d+;", f"numberOfSubdomains {a.np};", txt))

    fallidos = []
    for caso in casos:
        print(f"\n== {caso.name} ==")
        try:
            if not (caso / "constant" / "polyMesh" / "faces").exists() or a.solo_malla:
                preparar(caso)
            if not a.solo_malla:
                resolver(caso, a.np)
        except RuntimeError as e:
            print(f"    {e}")
            fallidos.append(caso.name)
    print("\nTerminado. Siguiente paso: python3 postproceso.py")
    if fallidos:
        print(f"Casos con errores: {', '.join(fallidos)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
