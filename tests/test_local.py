# tests/test_local.py — Simulador de chat en terminal
# Generado por AgentKit

"""
Prueba tu agente sin necesitar WhatsApp.
Simula una conversación con Ana de Imporusa en la terminal.
"""

import asyncio
import sys
import os

# Agregar el directorio raíz al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial, limpiar_historial
from agent.tools import enviar_cotizacion_email
from agent.session_logger import (
    log_mensaje_cliente, log_respuesta_ana,
    log_cotizacion, log_inicio_sesion,
)

TELEFONO_TEST = "test-local-001"


async def main():
    """Loop principal del chat de prueba."""
    await inicializar_db()
    log_inicio_sesion(TELEFONO_TEST, origen="test_local")

    print()
    print("=" * 55)
    print("   AgentKit — Test Local | Ana - Imporusa")
    print("=" * 55)
    print()
    print("  Escribe mensajes como si fueras un cliente.")
    print("  Comandos especiales:")
    print("    'limpiar'  — borra el historial")
    print("    'salir'    — termina el test")
    print()
    print("-" * 55)
    print()

    while True:
        try:
            mensaje = input("Cliente: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nTest finalizado.")
            break

        if not mensaje:
            continue

        if mensaje.lower() == "salir":
            print("\nTest finalizado.")
            break

        if mensaje.lower() == "limpiar":
            await limpiar_historial(TELEFONO_TEST)
            print("[Historial borrado]\n")
            continue

        log_mensaje_cliente(TELEFONO_TEST, mensaje)

        # Obtener historial ANTES de guardar (brain.py agrega el mensaje actual)
        historial = await obtener_historial(TELEFONO_TEST)

        # Generar respuesta
        respuesta = await generar_respuesta(mensaje, historial)

        # Detectar si Ana incluye el comando ENVIAR_COTIZACION en cualquier parte de la respuesta
        comando = None
        for linea in respuesta.splitlines():
            if linea.strip().startswith("ENVIAR_COTIZACION|"):
                comando = linea.strip()
                break

        if comando:
            respuesta = respuesta.replace(comando, "").strip()
            partes = comando.split("|")
            if len(partes) == 7:
                _, nombre, producto, link, cantidad, email_cliente, _ = partes
                print(f"\n[Enviando email a {email_cliente.strip()}...]")
                exito = await enviar_cotizacion_email(
                    email_cliente=email_cliente.strip(),
                    nombre_cliente=nombre.strip(),
                    producto=producto.strip(),
                    link=link.strip(),
                    cantidad=int(cantidad.strip()) if cantidad.strip().isdigit() else 1,
                    telefono_cliente=TELEFONO_TEST,
                )
                log_cotizacion(
                    telefono=TELEFONO_TEST,
                    nombre=nombre.strip(),
                    producto=producto.strip(),
                    link=link.strip(),
                    cantidad=int(cantidad.strip()) if cantidad.strip().isdigit() else 1,
                    email=email_cliente.strip(),
                    exito=exito,
                )
                if not exito:
                    respuesta += f"\n\n⚠️ Tuve un problema enviando el email a {email_cliente.strip()}. ¿Podrías verificar que el correo esté bien escrito?"

        print(f"\nAna: {respuesta}")
        print()

        log_respuesta_ana(TELEFONO_TEST, respuesta)

        # Guardar mensaje del usuario y respuesta del agente
        await guardar_mensaje(TELEFONO_TEST, "user", mensaje)
        await guardar_mensaje(TELEFONO_TEST, "assistant", respuesta)


if __name__ == "__main__":
    asyncio.run(main())
