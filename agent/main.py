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
    log_cotizacion, log_error,
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

        # ── DEBUG: Loguear respuesta cruda para diagnosticar ─────────
        logger.info(f"[DEBUG] Respuesta cruda de Claude ({len(respuesta)} chars): {respuesta[:300]}...")

        # Detectar si Ana incluye el comando ENVIAR_COTIZACION
        # Formato: ENVIAR_COTIZACION|nombre|producto|link|cantidad|email
        comando = None
        for linea in respuesta.splitlines():
            if linea.strip().startswith("ENVIAR_COTIZACION|"):
                comando = linea.strip()
                logger.info(f"[COMANDO] Detectado: {comando}")
                break

        if comando:
            respuesta = respuesta.replace(comando, "").strip()
            partes = comando.split("|")
            logger.info(f"[COMANDO] Partes ({len(partes)}): {partes}")

            # Aceptar 6 partes (nuevo formato sin teléfono) o 7 partes (formato legacy)
            nombre = producto = link = cantidad = email_cliente = ""
            formato_valido = False

            if len(partes) == 6:
                # Formato nuevo: ENVIAR_COTIZACION|nombre|producto|link|cantidad|email
                _, nombre, producto, link, cantidad, email_cliente = partes
                formato_valido = True
                logger.info(f"[COMANDO] Formato 6 campos OK")
            elif len(partes) == 7:
                # Formato legacy: ENVIAR_COTIZACION|nombre|producto|link|cantidad|email|telefono
                _, nombre, producto, link, cantidad, email_cliente, _ = partes
                formato_valido = True
                logger.info(f"[COMANDO] Formato 7 campos (legacy) OK")
            else:
                logger.error(
                    f"[COMANDO] FORMATO INVALIDO — esperaba 6 o 7 campos, recibí {len(partes)}. "
                    f"Comando completo: {comando}"
                )

            if formato_valido:
                cant_int = int(cantidad.strip()) if cantidad.strip().isdigit() else 1

                # ── EMAIL — fallo aquí NO cancela Notion ─────────────
                exito_email = False
                try:
                    exito_email = await enviar_cotizacion_email(
                        email_cliente=email_cliente.strip(),
                        nombre_cliente=nombre.strip(),
                        producto=producto.strip(),
                        link=link.strip(),
                        cantidad=cant_int,
                        telefono_cliente=telefono,
                    )
                    logger.info(f"[EMAIL] Resultado: {'OK' if exito_email else 'FALLO'}")
                except Exception as e_email:
                    logger.error(f"[EMAIL] Excepcion: {type(e_email).__name__}: {e_email}")

                try:
                    log_cotizacion(
                        telefono=telefono,
                        nombre=nombre.strip(),
                        producto=producto.strip(),
                        link=link.strip(),
                        cantidad=cant_int,
                        email=email_cliente.strip(),
                        exito=exito_email,
                    )
                except Exception as e_log:
                    logger.error(f"Error en log_cotizacion: {e_log}")

                # ── NOTION — 100% independiente del email ────────────
                lineas_chat = []
                for msg in historial:
                    prefijo = "Cliente" if msg["role"] == "user" else "Ana"
                    lineas_chat.append(f"{prefijo}: {msg['content']}")
                lineas_chat.append(f"Cliente: {texto}")
                lineas_chat.append(f"Ana: {respuesta}")
                resumen_completo = (
                    f"Producto: {producto.strip()}\n"
                    f"Link: {link.strip()}\n"
                    f"Cantidad: {cantidad.strip()}\n\n"
                    f"--- Conversacion WhatsApp ---\n"
                    + "\n".join(lineas_chat)
                )

                logger.info(f"[NOTION] Guardando prospecto: {nombre.strip()} — {email_cliente.strip()}")
                try:
                    exito_notion = await crear_prospecto_notion(
                        nombre=nombre.strip(),
                        email=email_cliente.strip(),
                        whatsapp=telefono,
                        producto=producto.strip(),
                        resumen_chat=resumen_completo,
                    )
                    logger.info(f"[NOTION] Resultado: {'OK' if exito_notion else 'FALLO'}")
                except Exception as e_notion:
                    logger.error(f"[NOTION] Excepcion: {type(e_notion).__name__}: {e_notion}")

                if not exito_email:
                    respuesta += f"\n\n⚠️ Tuve un problema enviando el email a {email_cliente.strip()}. ¿Podrías verificar que el correo esté bien escrito?"
        else:
            logger.info(f"[COMANDO] No se detectó ENVIAR_COTIZACION en la respuesta")

        # Guardar mensaje del usuario Y respuesta del agente en memoria
        await guardar_mensaje(telefono, "user", texto)
        await guardar_mensaje(telefono, "assistant", respuesta)

        log_respuesta_ana(telefono, respuesta)

        # Enviar respuesta por WhatsApp via el proveedor
        await proveedor.enviar_mensaje(telefono, respuesta)

        logger.info(f"Respuesta a {telefono}: {respuesta[:200]}...")

    except Exception as e:
        logger.error(f"Error procesando mensaje de {telefono}: {e}")
        log_error(telefono, str(e))


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
