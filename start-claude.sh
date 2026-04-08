#!/usr/bin/env bash
# start-claude.sh — Lanzador de Claude Code con selector de proveedor

COPILOT_PORT=4141
COPILOT_PID_FILE="/tmp/copilot-api-plus.pid"

echo ""
echo "======================================="
echo "   Claude Code — Selector de Proveedor"
echo "======================================="
echo ""
echo "  1) Claude (Anthropic) — modelos nativos"
echo "  2) GitHub Copilot     — modelos de Copilot"
echo ""
read -p "Elige una opción [1/2]: " opcion

case "$opcion" in
  1)
    echo ""
    echo "Usando Claude (Anthropic)..."
    # Matar proxy si estaba corriendo
    if [ -f "$COPILOT_PID_FILE" ]; then
      kill "$(cat $COPILOT_PID_FILE)" 2>/dev/null || true
      rm -f "$COPILOT_PID_FILE"
      echo "Proxy de Copilot detenido."
    fi
    echo ""
    # Lanzar Claude con API de Anthropic (sin proxy)
    unset ANTHROPIC_BASE_URL
    cmd //c claude
    ;;

  2)
    echo ""
    echo "Iniciando GitHub Copilot proxy..."

    # Verificar si ya está corriendo
    if curl -s "http://localhost:$COPILOT_PORT" > /dev/null 2>&1; then
      echo "Proxy ya está corriendo en puerto $COPILOT_PORT"
    else
      echo "Instalando/arrancando copilot-api-plus (puede tardar en la primera vez)..."
      # Usar cmd //c para evitar problemas de cygpath en Git Bash
      cmd //c "npx copilot-api-plus@latest start --port $COPILOT_PORT" &
      echo $! > "$COPILOT_PID_FILE"

      echo "Esperando que el proxy arranque..."
      for i in {1..20}; do
        sleep 2
        if curl -s "http://localhost:$COPILOT_PORT" > /dev/null 2>&1; then
          echo "Proxy listo."
          break
        fi
        echo "  Intento $i/20..."
      done
    fi

    echo ""
    echo "Conectado al proxy de Copilot en puerto $COPILOT_PORT"
    echo ""

    # Usar variable de entorno para apuntar al proxy
    cmd //c "set ANTHROPIC_BASE_URL=http://localhost:$COPILOT_PORT && set ANTHROPIC_API_KEY=copilot && claude"
    ;;

  *)
    echo "Opción inválida. Saliendo."
    exit 1
    ;;
esac
