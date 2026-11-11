"""
hidraulica.py — Relaciones de canal abierto para el diseño de cada caso.

Todas las fórmulas son clásicas de hidráulica de canales (Chow, 1959;
French, 1985). Los coeficientes empíricos están marcados como tales y
conviene revisarlos para cada aplicación.
"""
from dataclasses import dataclass, asdict
import math


def belanger(fr1: float) -> float:
    """Relación de calados conjugados y2/y1 (ecuación de Bélanger)."""
    return 0.5 * (math.sqrt(1.0 + 8.0 * fr1 ** 2) - 1.0)


def calado_critico(q: float, g: float) -> float:
    """Calado crítico en canal rectangular: yc = (q^2/g)^(1/3)."""
    return (q ** 2 / g) ** (1.0 / 3.0)


def snap(valor: float, paso: float) -> float:
    """Redondea un valor al múltiplo más cercano de 'paso' (mínimo un paso)."""
    return max(paso, round(valor / paso) * paso)


@dataclass
class DisenoCaso:
    nombre: str
    g: float
    y1: float          # calado bajo la compuerta (entrada supercrítica) [m]
    fr1: float         # Froude nominal en la entrada [-]
    v1: float          # velocidad de entrada [m/s]
    q: float           # caudal unitario [m2/s]
    y2_belanger: float # calado conjugado teórico [m]
    yc: float          # calado crítico [m]
    y_cola: float      # nivel de aguas abajo que se busca con el umbral [m]
    h_umbral: float    # carga sobre la cresta del umbral [m]
    p_umbral: float    # altura del umbral (vertedero de cresta ancha) [m]
    L_canal: float     # longitud del canal hasta el umbral [m]
    L_cresta: float    # longitud de la cresta [m]
    L_caida: float     # tramo de caída aguas abajo del umbral [m]
    D_caida: float     # profundidad de la caída [m]
    z_techo: float     # cota superior del dominio [m]
    dx: float
    dz: float
    espesor: float     # espesor del dominio 2D (una celda) [m]

    def a_dict(self):
        return asdict(self)


def disenar_caso(fr1: float, p: dict, y1: float = None, nombre: str = None,
                 L_poza: float = None) -> DisenoCaso:
    """
    Dimensiona un caso a partir de Fr1 y de los parámetros globales.

    Control de aguas abajo: un umbral (vertedero de cresta ancha) con caída
    libre. Su altura se elige para que el nivel aguas arriba del umbral sea
    ~factor_cola * y2_Bélanger. Con factor_cola < 1 el resalto se desplaza
    aguas abajo de la compuerta (resalto libre); el modelo lo ubica solo
    donde el calado supercrítico local cumple la condición de conjugados.
    El umbral NO impone el resultado: y1 y y2 se miden en la simulación.
    """
    g = p["g"]
    y1 = p["y1"] if y1 is None else y1
    malla = p["malla"]
    dz = y1 / malla["celdas_en_y1"]
    dx = dz * malla["relacion_dx_dz"]

    v1 = fr1 * math.sqrt(g * y1)
    q = v1 * y1
    y2 = y1 * belanger(fr1)
    yc = calado_critico(q, g)

    # Vertedero de cresta ancha. La carga se mide como ENERGÍA aguas arriba
    # (calado + carga de velocidad de aproximación):
    #   q = Cd * sqrt(g) * (2E/3)^1.5   ->   E_cresta = 1.5*yc / Cd^(2/3)
    # Cd es EMPÍRICO (~0.85-1.0 según forma y longitud de la cresta; verificar).
    # Con calado de cola y_cola: E = y_cola + q^2/(2 g y_cola^2) = P + E_cresta
    e_cresta = 1.5 * yc / p["cd_umbral"] ** (2.0 / 3.0)
    y_cola = p["factor_cola"] * y2
    e_cola = y_cola + q ** 2 / (2 * g * y_cola ** 2)
    p_umbral = e_cola - e_cresta
    h_umbral = y_cola - p_umbral  # calado sobre la cota de cresta, aguas arriba
    if p_umbral < 2 * dz:
        raise ValueError(
            f"Fr1={fr1:.2f}: la altura de umbral calculada ({p_umbral:.4f} m) es "
            "demasiado pequeña. Con Froude bajo (< ~3.5) un umbral de cresta "
            "ancha no da el nivel de cola necesario; use otro control de "
            "aguas abajo o suba factor_cola."
        )

    geo = p["geometria"]
    # Longitud desde la compuerta hasta el umbral: la de la poza real si el
    # usuario la da (modo revisión de diseño); si no, una holgada (validación).
    if L_poza:
        L_canal = L_poza
    else:
        L_canal = max(geo["L_canal_min"], geo["L_canal_en_y2"] * y2)
    L_cresta = max(geo["L_cresta_min"], 3.0 * h_umbral)
    L_caida = max(0.3, 4.0 * yc)
    D_caida = max(0.10, 2.0 * yc)
    z_techo = max(y_cola, y1) * geo["techo_sobre_cola"] + 0.05

    # Todas las cotas se ajustan a la malla: así la malla es uniforme y
    # cada nivel (y1, umbral, techo) coincide con una cara de celda.
    return DisenoCaso(
        nombre=nombre or f"Fr{fr1:.2f}".replace(".", "p"),
        g=g, y1=y1, fr1=fr1, v1=v1, q=q,
        y2_belanger=y2, yc=yc, y_cola=y_cola, h_umbral=h_umbral,
        p_umbral=snap(p_umbral, dz),
        L_canal=snap(L_canal, dx), L_cresta=snap(L_cresta, dx),
        L_caida=snap(L_caida, dx), D_caida=snap(D_caida, dz),
        z_techo=snap(z_techo, dz), dx=dx, dz=dz, espesor=dx,
    )
