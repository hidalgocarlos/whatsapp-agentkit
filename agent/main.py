# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit

"""
Servidor principal del agente de WhatsApp.
Funciona con cualquier proveedor (Whapi, Meta, Twilio) gracias a la capa de providers.
"""

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from agent.brain import generar_respuesta, cargar_knowledge
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial
from agent.providers import obtener_proveedor
from agent.tools import enviar_cotizacion_email, crear_prospecto_notion
from agent.session_logger import (
    log_mensaje_cliente, log_respuesta_ana,
    log_cotizacion, log_error, log_inicio_sesion,
)

load_dotenv()

# Configuración de logging según entorno
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

# Proveedor de WhatsApp (se configura en .env con WHATSAPP_PROVIDER)
proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa la base de datos al arrancar el servidor."""
    await inicializar_db()
    cargar_knowledge()
    logger.info("Base de datos inicializada")
    logger.info(f"Servidor AgentKit corriendo en puerto {PORT}")
    logger.info(f"Proveedor de WhatsApp: {proveedor.__class__.__name__}")
    yield


app = FastAPI(
    title="AgentKit — Ana de Imporusa",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/")
async def health_check():
    """Endpoint de salud para Railway/monitoreo."""
    return {"status": "ok", "agente": "Ana - Imporusa", "service": "agentkit"}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (requerido por Meta Cloud API, no-op para otros)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


async def procesar_mensaje(telefono: str, texto: str):
    """Procesa el mensaje en background para no bloquear el webhook."""
    try:
        log_mensaje_cliente(telefono, texto)

        # Obtener historial ANTES de guardar el mensaje actual
        historial = await obtener_historial(telefono)

        # Generar respuesta con Claude
        respuesta = await generar_respuesta(texto, historial)

        # Detectar si Ana incluye el comando ENVIAR_COTIZACION
        # Formato: ENVIAR_COTIZACION|nombre|producto|link|cantidad|email|telefono
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
                exito = await enviar_cotizacion_email(
                    email_cliente=email_cliente.strip(),
                    nombre_cliente=nombre.strip(),
                    producto=producto.strip(),
                    link=link.strip(),
                    cantidad=int(cantidad.strip()) if cantidad.strip().isdigit() else 1,
                    telefono_cliente=telefono,
                )
                log_cotizacion(
                    telefono=telefono,
                    nombre=nombre.strip(),
                    producto=producto.strip(),
                    link=link.strip(),
                    cantidad=int(cantidad.strip()) if cantidad.strip().isdigit() else 1,
                    email=email_cliente.strip(),
                    exito=exito,
                )
                await crear_prospecto_notion(
                    nombre=nombre.strip(),
                    email=email_cliente.strip(),
                    whatsapp=telefono,
                    producto=producto.strip(),
                    resumen_chat=f"Producto: {producto.strip()}\nLink: {link.strip()}\nCantidad: {cantidad.strip()}",
                )
                if not exito:
                    respuesta += f"\n\n⚠️ Tuve un problema enviando el email a {email_cliente.strip()}. ¿Podrías verificar que el correo esté bien escrito?"

        # Guardar mensaje del usuario Y respuesta del agente en memoria
        await guardar_mensaje(telefono, "user", texto)
        await guardar_mensaje(telefono, "assistant", respuesta)

        log_respuesta_ana(telefono, respuesta)

        # Enviar respuesta por WhatsApp via el proveedor
        await proveedor.enviar_mensaje(telefono, respuesta)

        logger.info(f"Respuesta a {telefono}: {respuesta}")

    except Exception as e:
        logger.error(f"Error procesando mensaje de {telefono}: {e}")
        log_error("procesar_mensaje", str(e))


@app.post("/webhook")
async def webhook_handler(request: Request, background_tasks: BackgroundTasks):
    """
    Recibe mensajes de WhatsApp via el proveedor configurado.
    Responde 200 OK inmediatamente y procesa en background para evitar timeouts.
    """
    try:
        # Parsear webhook — el proveedor normaliza el formato
        mensajes = await proveedor.parsear_webhook(request)

        for msg in mensajes:
            # Ignorar mensajes propios o vacíos
            if msg.es_propio or not msg.texto:
                continue

            logger.info(f"Mensaje de {msg.telefono}: {msg.texto}")

            # Procesar en background — responde 200 OK antes de llamar a Claude
            background_tasks.add_task(procesar_mensaje, msg.telefono, msg.texto)

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        log_error("webhook", str(e))
        raise HTTPException(status_code=500, detail=str(e))
