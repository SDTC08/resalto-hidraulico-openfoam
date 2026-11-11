#!/usr/bin/env bash
# instalar.sh — Instala la interfaz del simulador de resalto hidráulico (una sola vez).
# Uso:  bash instalar.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
echo "== Simulador de resalto hidráulico · instalación =="

# 1. Python 3.11 o superior
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "ERROR: se necesita Python 3.11 o superior (tienes $(python3 --version 2>&1))."
    exit 1
fi

# 2. Entorno virtual dentro de esta carpeta (no toca el sistema)
if [ ! -x .venv/bin/python ]; then
    echo "-> Creando entorno de Python en $DIR/.venv"
    if ! python3 -m venv .venv 2>/dev/null; then
        rm -rf .venv
        echo "ERROR: falta el paquete python3-venv. Instálalo con:"
        echo "    sudo apt install python3-venv"
        echo "y vuelve a ejecutar: bash instalar.sh"
        exit 1
    fi
fi

# 3. Librerías
echo "-> Instalando librerías (puede tardar unos minutos)"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# 4. Detectar OpenFOAM
echo "-> Buscando OpenFOAM"
.venv/bin/python cola.py --detectar

# 5. Ícono en el menú de aplicaciones y en el escritorio
chmod +x iniciar.sh
LANZADOR="[Desktop Entry]
Type=Application
Name=Resalto hidráulico
Comment=Simulador CFD de resalto hidráulico (Hidroclic)
Exec=bash \"$DIR/iniciar.sh\"
Icon=applications-science
Terminal=false
Categories=Science;Education;"
mkdir -p "$HOME/.local/share/applications"
echo "$LANZADOR" > "$HOME/.local/share/applications/resalto-hidraulico.desktop"
ESCRITORIO="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$ESCRITORIO" ]; then
    echo "$LANZADOR" > "$ESCRITORIO/resalto-hidraulico.desktop"
    chmod +x "$ESCRITORIO/resalto-hidraulico.desktop"
    gio set "$ESCRITORIO/resalto-hidraulico.desktop" metadata::trusted true 2>/dev/null || true
fi

echo
echo "Listo. Abre la app con el ícono «Resalto hidráulico» o con:  bash iniciar.sh"
