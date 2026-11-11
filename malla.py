"""
malla.py — Genera un blockMeshDict multibloque 2D (plano x-z) para:

    compuerta |  canal (fondo z=0)  | umbral (cresta z=P) | caída (fondo z=-D) | salida
    (entrada  |                     |                     |                    |
     z<y1)    |                     |                     |                    |

La geometría se describe como una rejilla de columnas (x) y bandas (z) con
una máscara de fluido. Cada bloque es una celda de esa rejilla, así que
blockMesh recibe bloques con caras compartidas consistentes.
"""
from hidraulica import DisenoCaso

# Caras hexaédricas en el orden y orientación (normal hacia afuera) de OpenFOAM
CARAS = {
    "xmin": (0, 4, 7, 3), "xmax": (1, 2, 6, 5),
    "ymin": (0, 1, 5, 4), "ymax": (3, 7, 6, 2),
    "zmin": (0, 3, 2, 1), "zmax": (4, 5, 6, 7),
}


def generar_blockmesh(d: DisenoCaso) -> str:
    xs = [0.0, d.L_canal, d.L_canal + d.L_cresta, d.L_canal + d.L_cresta + d.L_caida]
    zs = sorted({-d.D_caida, 0.0, d.y1, d.p_umbral, d.z_techo})
    fondo = [0.0, d.p_umbral, -d.D_caida]  # cota de fondo de cada columna

    def es_fluido(i, j):
        return zs[j] >= fondo[i] - 1e-12

    nc, nb = len(xs) - 1, len(zs) - 1
    vert_id, vertices = {}, []

    def vid(ix, jz, k):
        clave = (ix, jz, k)
        if clave not in vert_id:
            vert_id[clave] = len(vertices)
            vertices.append((xs[ix], d.espesor * k, zs[jz]))
        return vert_id[clave]

    bloques, parches = [], {
        "entrada": [], "compuerta": [], "salida": [], "atmosfera": [],
        "paredes": [], "frenteYFondo": [],
    }
    for i in range(nc):
        for j in range(nb):
            if not es_fluido(i, j):
                continue
            h = [vid(i, j, 0), vid(i + 1, j, 0), vid(i + 1, j, 1), vid(i, j, 1),
                 vid(i, j + 1, 0), vid(i + 1, j + 1, 0), vid(i + 1, j + 1, 1), vid(i, j + 1, 1)]
            nx = max(1, round((xs[i + 1] - xs[i]) / d.dx))
            nz = max(1, round((zs[j + 1] - zs[j]) / d.dz))
            bloques.append(f"    hex ({' '.join(map(str, h))}) ({nx} 1 {nz}) simpleGrading (1 1 1)")

            def cara(nombre):
                return "(" + " ".join(str(h[k]) for k in CARAS[nombre]) + ")"

            parches["frenteYFondo"] += [cara("ymin"), cara("ymax")]
            # Oeste
            if i == 0:
                destino = "entrada" if zs[j + 1] <= d.y1 + 1e-12 else "compuerta"
                parches[destino].append(cara("xmin"))
            elif not es_fluido(i - 1, j):
                parches["paredes"].append(cara("xmin"))
            # Este
            if i == nc - 1:
                parches["salida"].append(cara("xmax"))
            elif not es_fluido(i + 1, j):
                parches["paredes"].append(cara("xmax"))
            # Sur
            if j == 0 or not es_fluido(i, j - 1):
                parches["paredes"].append(cara("zmin"))
            # Norte
            if j == nb - 1:
                parches["atmosfera"].append(cara("zmax"))

    tipos = {"entrada": "patch", "compuerta": "wall", "salida": "patch",
             "atmosfera": "patch", "paredes": "wall", "frenteYFondo": "empty"}
    txt_v = "\n".join(f"    ({x:.6f} {y:.6f} {z:.6f})" for x, y, z in vertices)
    txt_p = ""
    for nombre, caras in parches.items():
        txt_p += (f"    {nombre}\n    {{\n        type {tipos[nombre]};\n        faces\n        (\n"
                  + "\n".join("            " + c for c in caras) + "\n        );\n    }\n")

    return f"""FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      blockMeshDict;
}}
// Generado por malla.py — no editar a mano

scale 1;

vertices
(
{txt_v}
);

blocks
(
{chr(10).join(bloques)}
);

edges ();

boundary
(
{txt_p});

mergePatchPairs ();
"""


def numero_celdas(d: DisenoCaso) -> int:
    """Estimación del número de celdas (para avisar antes de correr)."""
    area = (d.L_canal * d.z_techo + d.L_cresta * (d.z_techo - d.p_umbral)
            + d.L_caida * (d.z_techo + d.D_caida))
    return round(area / (d.dx * d.dz))
