"""
video.py — Crea un video MP4 de un caso: ParaView sin ventana (pvbatch o
pvpython) renderiza un cuadro por cada tiempo guardado y ffmpeg los une.

Uso desde la app (botón «Crear video») o en terminal:
    python3 video.py estudios/<estudio>/casos/C02 --formato horizontal --duracion 8
"""
import argparse
import shutil
import subprocess
from pathlib import Path

import cola

FORMATOS = {
    "horizontal": (1920, 1080),   # 16:9 — presentaciones, YouTube, LinkedIn
    "cuadrado": (1080, 1080),     # 1:1  — LinkedIn / Instagram en el feed
    "vertical": (1080, 1350),     # 4:5  — feed en celular
}

GUION = """# Generado por video.py: renderiza un cuadro por cada tiempo guardado.
from paraview.simple import *
foam, patron = {foam!r}, {patron!r}
ancho, alto, fondo_blanco, titulo = {ancho}, {alto}, {fondo_blanco}, {titulo!r}

r = OpenFOAMReader(registrationName="resalto", FileName=foam)
r.UpdatePipelineInformation()
tiempos = [t for t in list(r.TimestepValues or []) if t > 0] or [0.0]

v = CreateView("RenderView")
v.ViewSize = [ancho, alto]
v.OrientationAxesVisibility = 0
claro, oscuro = [0.97, 0.98, 0.97], [0.14, 0.14, 0.14]
try:
    v.UseColorPaletteForBackground = 0
except Exception:
    pass
v.Background = claro if fondo_blanco else [0.32, 0.34, 0.43]
texto = oscuro if fondo_blanco else [1, 1, 1]

d = Show(r, v)
try:
    ColorBy(d, ("POINTS", "alpha.water"))
except Exception:
    ColorBy(d, ("CELLS", "alpha.water"))
lut = GetColorTransferFunction("alpha.water")
lut.RescaleTransferFunction(0.0, 1.0)
d.SetScalarBarVisibility(v, True)
barra = GetScalarBar(lut, v)
barra.Title = "Agua (1) / aire (0)"
barra.ComponentTitle = ""
barra.TitleColor = texto
barra.LabelColor = texto

if titulo:
    t = Text(Text=titulo)
    td = Show(t, v)
    td.FontSize = 28
    td.Color = texto
    td.WindowLocation = "Upper Center"

xmin, xmax, ymin, ymax, zmin, zmax = r.GetDataInformation().GetBounds()
cx, cy, cz = (xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2
v.CameraFocalPoint = [cx, cy, cz]
v.CameraPosition = [cx, cy - 10 * (xmax - xmin), cz]
v.CameraViewUp = [0, 0, 1]
v.CameraParallelProjection = 1
v.ResetCamera()

for i, tt in enumerate(tiempos):
    v.ViewTime = tt
    Render(v)
    SaveScreenshot(patron % i, v, ImageResolution=[ancho, alto])
print("CUADROS", len(tiempos))
"""


def comando_paraview():
    """Devuelve la orden para ejecutar un guion de ParaView sin ventana, o None."""
    if shutil.which("pvbatch"):
        return lambda g: ["pvbatch", str(g)]
    if shutil.which("pvpython"):
        return lambda g: ["pvpython", "--force-offscreen-rendering", str(g)]
    bashrc = cola.leer_ajustes().get("bashrc")
    if bashrc:
        r = subprocess.run(["bash", "-c", f'source "{bashrc}" >/dev/null 2>&1; '
                                          'command -v pvbatch || command -v pvpython'],
                           capture_output=True, text=True)
        exe = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        if exe:
            extra = "" if exe.endswith("pvbatch") else " --force-offscreen-rendering"
            return lambda g: ["bash", "-c", f'source "{bashrc}" >/dev/null 2>&1; "{exe}"{extra} "{g}"']
    return None


def crear_video(caso: Path, formato="horizontal", duracion=8.0, fondo_blanco=True, titulo=""):
    """Devuelve (ok, mensaje, ruta_mp4)."""
    caso = Path(caso).resolve()
    if not shutil.which("ffmpeg"):
        return False, "Falta ffmpeg. Instálalo con `sudo apt install ffmpeg`.", None
    pv = comando_paraview()
    if pv is None:
        return False, "No se encontró ParaView (pvbatch/pvpython). Instálalo con `sudo apt install paraview`.", None
    ancho, alto = FORMATOS[formato]
    tmp = caso / "video_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    (caso / "caso.foam").touch(exist_ok=True)
    guion = tmp / "cuadros.py"
    guion.write_text(GUION.format(foam=str(caso / "caso.foam"), patron=str(tmp / "c.%04d.png"),
                                  ancho=ancho, alto=alto, fondo_blanco=fondo_blanco, titulo=titulo))
    r = subprocess.run(pv(guion), capture_output=True, text=True, cwd=tmp)
    cuadros = sorted(tmp.glob("c.*.png"))
    if not cuadros:
        return False, "ParaView no generó imágenes:\n\n" + (r.stdout + r.stderr)[-1500:], None
    destino = caso.parent.parent / "videos"
    destino.mkdir(exist_ok=True)
    mp4 = destino / f"{caso.name}_{formato}.mp4"
    fps = max(1.0, len(cuadros) / max(1.0, duracion))
    f = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", f"{fps:.3f}",
                        "-i", str(tmp / "c.%04d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", str(mp4)],
                       capture_output=True, text=True)
    if f.returncode != 0 or not mp4.exists():
        return False, "ffmpeg no pudo crear el video:\n\n" + f.stderr[-1500:], None
    shutil.rmtree(tmp, ignore_errors=True)
    return True, f"Video creado: {len(cuadros)} cuadros, {len(cuadros) / fps:.0f} s, {ancho}×{alto}.", mp4


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("caso")
    ap.add_argument("--formato", choices=list(FORMATOS), default="horizontal")
    ap.add_argument("--duracion", type=float, default=8.0)
    ap.add_argument("--fondo-oscuro", action="store_true")
    ap.add_argument("--titulo", default="")
    a = ap.parse_args()
    ok, msg, ruta = crear_video(Path(a.caso), a.formato, a.duracion, not a.fondo_oscuro, a.titulo)
    print(msg, ruta or "")
