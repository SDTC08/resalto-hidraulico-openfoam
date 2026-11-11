#!/usr/bin/env bash
# iniciar.sh — Abre la interfaz en el navegador (la inicia si no está corriendo).
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
URL="http://localhost:8501"
if [ ! -x .venv/bin/streamlit ]; then
    echo "Primero ejecuta: bash instalar.sh"; exit 1
fi
if ! .venv/bin/python -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',8501))==0 else 1)"; then
    nohup .venv/bin/streamlit run app.py --server.headless true > app.log 2>&1 &
    for i in $(seq 1 30); do
        sleep 1
        .venv/bin/python -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',8501))==0 else 1)" && break
    done
fi
xdg-open "$URL" >/dev/null 2>&1 || echo "Abre en tu navegador: $URL"
