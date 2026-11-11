"""
cola.py — Cola de simulaciones en segundo plano (una corrida a la vez).

La interfaz (app.py) pone trabajos en la cola; un proceso "trabajador"
desacoplado los ejecuta en orden. Cerrar el navegador o la app NO detiene
las simulaciones. Solo el botón Detener (o apagar la PC) las detiene.

Uso interno; también sirve en terminal:
    python3 cola.py --estado
    python3 cola.py --detener
"""
import glob
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
COLA = AQUI / "cola"
PENDIENTES = COLA / "pendientes"
EN_CURSO = COLA / "en_curso.json"
HISTORIAL = COLA / "historial.jsonl"
PID_TRABAJADOR = COLA / "trabajador.pid"
AJUSTES = AQUI / "ajustes.json"


# ------------------------------------------------------------------ OpenFOAM
def candidatos_bashrc():
    patrones = [
        "/opt/openfoam*/etc/bashrc",                       # openfoam.org (deb)
        "/usr/lib/openfoam/openfoam*/etc/bashrc",          # ESI (deb)
        os.path.expanduser("~/OpenFOAM/OpenFOAM-*/etc/bashrc"),
        os.path.expanduser("~/openfoam*/etc/bashrc"),
        "/usr/share/openfoam/etc/bashrc",                  # paquete de Debian/Ubuntu
    ]
    vistos = []
    for pat in patrones:
        for f in sorted(glob.glob(pat)):
            if f not in vistos:
                vistos.append(f)
    return vistos


def version_openfoam(bashrc: str) -> str:
    """Carga el bashrc en un bash aparte y devuelve $WM_PROJECT_VERSION ('' si falla)."""
    try:
        r = subprocess.run(
            ["bash", "-c", f'source "{bashrc}" >/dev/null 2>&1; echo "$WM_PROJECT_VERSION"'],
            capture_output=True, text=True, timeout=60)
        return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    except Exception:  # noqa: BLE001
        return ""


def rama_de(version: str) -> str:
    return "esi" if version.lower().startswith("v") else "org"


def leer_ajustes() -> dict:
    try:
        return json.loads(AJUSTES.read_text())
    except Exception:  # noqa: BLE001
        return {}


def guardar_ajustes(a: dict):
    AJUSTES.write_text(json.dumps(a, indent=2))


def configurar_openfoam(bashrc: str = None) -> dict:
    """Detecta (o fija) el OpenFOAM a usar y lo guarda en ajustes.json."""
    a = leer_ajustes()
    opciones = [bashrc] if bashrc else ([a["bashrc"]] if a.get("bashrc") else []) + candidatos_bashrc()
    for b in opciones:
        if b and Path(b).exists():
            v = version_openfoam(b)
            if v:
                a.update({"bashrc": b, "version": v, "rama": rama_de(v)})
                guardar_ajustes(a)
                return a
    return {}


# ------------------------------------------------------------------ procesos
def _vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:  # que el pid sea realmente el trabajador (no uno reciclado)
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore")
        return "cola.py" in cmd
    except OSError:
        return True


def trabajador_activo() -> bool:
    try:
        return _vivo(int(PID_TRABAJADOR.read_text()))
    except (OSError, ValueError):
        return False


def encolar(estudio: Path, nucleos: int) -> str:
    """Agrega un estudio a la cola y arranca el trabajador si hace falta."""
    PENDIENTES.mkdir(parents=True, exist_ok=True)
    tid = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid() % 1000:03d}"
    trabajo = {"id": tid, "estudio": str(Path(estudio).resolve()), "nucleos": int(nucleos),
               "creado": time.time()}
    (PENDIENTES / f"{tid}.json").write_text(json.dumps(trabajo))
    if not trabajador_activo():
        with open(COLA / "trabajador.log", "a") as log:
            subprocess.Popen([sys.executable, str(AQUI / "cola.py"), "--trabajar"],
                             cwd=AQUI, stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    return tid


def pendientes():
    out = []
    for f in sorted(PENDIENTES.glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except Exception:  # noqa: BLE001
            pass
    return out


def en_curso():
    try:
        t = json.loads(EN_CURSO.read_text())
    except Exception:  # noqa: BLE001
        return None
    return t if trabajador_activo() else None


def historial(n=20):
    try:
        lineas = HISTORIAL.read_text().strip().splitlines()
    except OSError:
        return []
    return [json.loads(x) for x in lineas[-n:]][::-1]


def detener():
    """Detiene la corrida en curso (la cola sigue con el siguiente trabajo)."""
    t = en_curso()
    if not t or not t.get("pgid"):
        return False
    try:
        os.killpg(t["pgid"], signal.SIGTERM)
        time.sleep(3)
        os.killpg(t["pgid"], signal.SIGKILL)
    except ProcessLookupError:
        pass
    return True


def quitar_pendiente(tid: str):
    f = PENDIENTES / f"{tid}.json"
    if f.exists():
        f.unlink()


def _registrar(trabajo, estado):
    trabajo = dict(trabajo, estado=estado, terminado=time.time())
    trabajo.pop("pgid", None)
    with open(HISTORIAL, "a") as f:
        f.write(json.dumps(trabajo) + "\n")


def trabajar():
    """Bucle del trabajador: procesa la cola en orden y termina cuando queda vacía."""
    COLA.mkdir(exist_ok=True)
    if trabajador_activo():
        return
    PID_TRABAJADOR.write_text(str(os.getpid()))
    ajustes = leer_ajustes()
    try:
        while True:
            lista = sorted(PENDIENTES.glob("*.json"))
            if not lista:
                break
            f = lista[0]
            trabajo = json.loads(f.read_text())
            f.unlink()
            estudio = Path(trabajo["estudio"])
            log = estudio / "corrida.log"
            bashrc = ajustes.get("bashrc", "")
            cmd = (f'source "{bashrc}" >/dev/null 2>&1; '
                   f'cd "{AQUI}" && python3 ejecutar.py -d "{estudio / "casos"}" --np {trabajo["nucleos"]}'
                   f' && "{sys.executable}" postproceso.py -d "{estudio / "casos"}" -o "{estudio / "resultados"}"')
            with open(log, "a") as fl:
                fl.write(f"\n=== Inicio {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                fl.flush()
                proc = subprocess.Popen(["bash", "-c", cmd], stdout=fl, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
                trabajo.update({"pgid": proc.pid, "inicio": time.time()})
                EN_CURSO.write_text(json.dumps(trabajo))
                rc = proc.wait()
                fl.write(f"=== Fin {time.strftime('%Y-%m-%d %H:%M:%S')} (código {rc}) ===\n")
            estado = "terminado" if rc == 0 else ("detenido" if rc < 0 else "con errores")
            _registrar(trabajo, estado)
            EN_CURSO.unlink(missing_ok=True)
    finally:
        EN_CURSO.unlink(missing_ok=True)
        PID_TRABAJADOR.unlink(missing_ok=True)


# ------------------------------------------------------------------ avance
_RE_TIEMPO = re.compile(rb"^Time = ([0-9.eE+-]+)s?\s*$", re.M)


def avance_caso(caso: Path) -> dict:
    """Tiempo simulado actual y final de un caso, leyendo su log y su controlDict."""
    try:
        txt = (caso / "system" / "controlDict").read_text()
        t_fin = float(re.search(r"^\s*endTime\s+([0-9.eE+-]+);", txt, re.M).group(1))
    except Exception:  # noqa: BLE001
        t_fin = None
    t = 0.0
    for nombre in ("log.foamRun", "log.interFoam"):
        f = caso / nombre
        if f.exists():
            with open(f, "rb") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 20000))
                m = _RE_TIEMPO.findall(fh.read())
            if m:
                t = max(t, float(m[-1]))
    for d in caso.iterdir() if caso.exists() else []:
        try:
            if (d / "alpha.water").exists():
                t = max(t, float(d.name))
        except ValueError:
            pass
    return {"t": t, "t_fin": t_fin, "fraccion": min(1.0, t / t_fin) if t_fin else 0.0}


if __name__ == "__main__":
    if "--trabajar" in sys.argv:
        trabajar()
    elif "--detectar" in sys.argv:
        a = configurar_openfoam()
        print(f"OpenFOAM {a['version']} ({a['bashrc']})" if a else
              "No se encontró OpenFOAM: indíquelo luego en la app (barra lateral).")
    elif "--detener" in sys.argv:
        print("detenido" if detener() else "no hay corrida en curso")
    else:
        print(json.dumps({"en_curso": en_curso(), "pendientes": pendientes(),
                          "openfoam": leer_ajustes()}, indent=2, ensure_ascii=False))
