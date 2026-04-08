# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit

"""
Servidor principal del agente de WhatsApp.
Funciona con cualquier proveedor (Whapi, Meta, Twilio) gracias a la capa de providers.
"""

import os
import re
import uuid
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from agent.brain import generar_respuesta, cargar_knowledge
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial,
    registrar_cotizacion, obtener_cotizaciones_para_seguimiento,
    avanzar_etapa_seguimiento, marcar_cotizacion_confirmada, marcar_email_abierto,
)
from agent.providers import obtener_proveedor
from agent.tools import enviar_cotizacion_email
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

# ── Control de concurrencia ──────────────────────────────────
# Limita cuántos mensajes se procesan simultáneamente para no saturar la API de Claude
MAX_MENSAJES_SIMULTANEOS = int(os.getenv("MAX_CONCURRENT_MESSAGES", 5))
semaforo_mensajes = asyncio.Semaphore(MAX_MENSAJES_SIMULTANEOS)

# Mensaje de error que se envía al cliente cuando el agente falla
MENSAJE_ERROR_CLIENTE = (
    "Lo siento, estoy teniendo un problema técnico en este momento. "
    "Por favor intenta de nuevo en unos minutos o escríbenos directamente. "
    "¡Gracias por tu paciencia! 🙏"
)

# Reintentos para procesar un mensaje antes de enviar error al cliente
MAX_REINTENTOS = 2


# Mensajes de follow-up por etapa (0-3)
_MENSAJES_FOLLOWUP = {
    0: (
        "Hola {nombre}! 👋\n\n"
        "Soy Ana de Imporusa. Hace un momento te enviamos la cotización de "
        "*{producto}* al correo {email}.\n\n"
        "¿Pudiste revisarla? ¿Tienes alguna duda o quieres confirmar el pedido? 😊"
    ),
    1: (
        "Hola {nombre}! ✋\n\n"
        "Los precios en Amazon pueden cambiar cualquier día. Tu cotización de "
        "*{producto}* sigue disponible.\n\n"
        "¿Quieres que lo confirmemos antes de que suba el precio? 🚀"
    ),
    2: (
        "Hola {nombre}! ⏰\n\n"
        "Tu cotización de *{producto}* está por vencer.\n\n"
        "Si quieres proceder, solo dime y lo pedimos hoy mismo. "
        "¡Tenemos casillero en Miami listo para ti! 📦"
    ),
    3: (
        "Hola {nombre}! 🌟\n\n"
        "¿Cómo estás? ¿Sigues interesado en *{producto}* "
        "o hay algo más en lo que Imporusa pueda ayudarte?\n\n"
        "Estamos aquí cuando nos necesites 😊"
    ),
}


async def cron_seguimientos():
    """Cron job: cada hora revisa cotizaciones con seguimiento pendiente (multi-etapa)."""
    while True:
        await asyncio.sleep(3600)  # Revisar cada hora
        try:
            pendientes = await obtener_cotizaciones_para_seguimiento()
            if not pendientes:
                logger.info("[FOLLOWUP] Sin cotizaciones pendientes")
                continue
            logger.info(f"[FOLLOWUP] {len(pendientes)} cotizaciones para follow-up")
            for cot in pendientes:
                try:
                    etapa = cot.etapa_seguimiento
                    plantilla = _MENSAJES_FOLLOWUP.get(etapa, _MENSAJES_FOLLOWUP[3])
                    msg = plantilla.format(
                        nombre=cot.nombre.split()[0],
                        producto=cot.producto,
                        email=cot.email,
                    )
                    exito = await proveedor.enviar_mensaje(cot.telefono, msg)
                    if exito:
                        await avanzar_etapa_seguimiento(cot.id)
                        logger.info(
                            f"[FOLLOWUP] ✅ Etapa {etapa} enviada a {cot.nombre} "
                            f"({cot.telefono})"
                        )
                    else:
                        logger.warning(f"[FOLLOWUP] ❌ No se pudo enviar a {cot.telefono}")
                except Exception as e_cot:
                    logger.error(f"[FOLLOWUP] Error enviando a {cot.telefono}: {e_cot}")
        except Exception as e:
            logger.error(f"[FOLLOWUP] Error en cron: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa la base de datos al arrancar el servidor."""
    await inicializar_db()
    cargar_knowledge()
    # Lanzar cron de follow-up en background
    asyncio.create_task(cron_seguimientos())
    logger.info("Base de datos inicializada")
    logger.info("[FOLLOWUP] Cron de seguimiento activo (secuencia: 24h → 3d → 7d → 14d)")
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


@app.get("/track/open/{tracking_id}")
async def tracking_apertura(tracking_id: str):
    """Pixel de tracking: registra cuando el cliente abre el email de cotización."""
    logger.info(f"[TRACKING] Email abierto — tracking_id={tracking_id}")
    try:
        await marcar_email_abierto(tracking_id)
    except Exception as e:
        logger.warning(f"[TRACKING] Error marcando apertura: {e}")
    # Retornar pixel GIF 1×1 transparente
    from fastapi.responses import Response
    gif_1x1 = (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!"
        b"\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
        b"\x00\x00\x02\x02D\x01\x00;"
    )
    return Response(content=gif_1x1, media_type="image/gif", headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    })


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (requerido por Meta Cloud API, no-op para otros)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


async def procesar_mensaje(telefono: str, texto: str):
    """
    Procesa el mensaje en background con:
    - Semáforo de concurrencia (máx mensajes simultáneos)
    - Reintentos automáticos
    - Mensaje de error al cliente si todo falla
    - Logging detallado con tiempos
    """
    inicio_total = time.time()

    # ── Semáforo: esperar turno si hay muchos mensajes en cola ────────
    logger.info(f"[COLA] Mensaje de {telefono} esperando semáforo")
    async with semaforo_mensajes:
        logger.info(f"[COLA] Mensaje de {telefono} adquirió slot — procesando")

        for intento in range(1, MAX_REINTENTOS + 1):
            try:
                await _procesar_mensaje_interno(telefono, texto, intento, inicio_total)
                return  # Éxito — salir
            except Exception as e:
                duracion = time.time() - inicio_total
                logger.error(
                    f"[RETRY] Intento {intento}/{MAX_REINTENTOS} falló para {telefono} "
                    f"({duracion:.1f}s): {type(e).__name__}: {e}"
                )
                log_error(telefono, f"Intento {intento}: {type(e).__name__}: {e}")

                if intento < MAX_REINTENTOS:
                    # Esperar un poco antes de reintentar
                    await asyncio.sleep(2)

        # ── Todos los reintentos fallaron — enviar mensaje de error al cliente ──
        duracion_total = time.time() - inicio_total
        logger.error(
            f"[FALLO TOTAL] No se pudo procesar mensaje de {telefono} después de "
            f"{MAX_REINTENTOS} intentos ({duracion_total:.1f}s). Enviando mensaje de error."
        )
        try:
            await proveedor.enviar_mensaje(telefono, MENSAJE_ERROR_CLIENTE)
            logger.info(f"[RECOVERY] Mensaje de error enviado a {telefono}")
        except Exception as e_envio:
            logger.error(f"[RECOVERY] No se pudo enviar mensaje de error a {telefono}: {e_envio}")


async def _procesar_mensaje_interno(telefono: str, texto: str, intento: int, inicio_total: float):
    """Lógica interna de procesamiento — puede lanzar excepciones para retry."""
    log_mensaje_cliente(telefono, texto)

    # Si el cliente escribe después de cotizarle, marcar como confirmado (lead caliente)
    if any(p in texto.lower() for p in ["confirmo", "quiero pedirlo", "dale", "vamos", "si lo quiero", "me lo traes", "hágale", "hagale", "listo", "procede"]):
        try:
            await marcar_cotizacion_confirmada(telefono)
            logger.info(f"[FOLLOWUP] Cotización marcada como confirmada para {telefono}")
        except Exception:
            pass

    # Obtener historial ANTES de guardar el mensaje actual
    historial = await obtener_historial(telefono)

    # Generar respuesta con Claude
    inicio_claude = time.time()
    respuesta = await generar_respuesta(texto, historial)
    duracion_claude = time.time() - inicio_claude

    # ── DEBUG: Loguear respuesta cruda para diagnosticar ─────────
    logger.info(
        f"[DEBUG] Respuesta de Claude ({len(respuesta)} chars, {duracion_claude:.1f}s): "
        f"{respuesta[:300]}..."
    )

    # Detectar si Ana incluye el comando ENVIAR_COTIZACION
    # Formato: ENVIAR_COTIZACION|nombre|producto|link|cantidad|email
    # La detección limpia caracteres de formato que Claude puede agregar
    comando = None

    def limpiar_linea(linea: str) -> str:
        """Elimina caracteres de formato que Claude puede agregar alrededor del comando."""
        limpia = linea.strip()
        # Quitar backticks (inline code), asteriscos (negrita), guiones bajos (cursiva), comillas
        for char in ["`", "*", "_", '"', "'", "«", "»"]:
            limpia = limpia.strip(char)
        return limpia.strip()

    for linea in respuesta.splitlines():
        limpia = limpiar_linea(linea)
        if limpia.startswith("ENVIAR_COTIZACION|"):
            comando = limpia
            if limpia != linea.strip():
                logger.info(f"[COMANDO] Detectado (limpiado de formato): '{linea.strip()}' → '{limpia}'")
            else:
                logger.info(f"[COMANDO] Detectado: {comando}")
            break

    # Diagnóstico: si la respuesta contiene el texto pero no se detectó como comando
    if not comando and "ENVIAR_COTIZACION" in respuesta:
        logger.warning(
            f"[COMANDO] ⚠️ La respuesta CONTIENE 'ENVIAR_COTIZACION' pero NO se detectó como comando. "
            f"Revisión necesaria. Primeras 500 chars de respuesta: {respuesta[:500]}"
        )
        # Intento de rescate: buscar con regex el patrón completo en toda la respuesta
        patron = re.search(r'ENVIAR_COTIZACION\|[^|\n]+\|[^|\n]+\|[^|\n]+\|[^|\n]+\|[^|\n]+', respuesta)
        if patron:
            comando = patron.group(0).strip()
            logger.info(f"[COMANDO] ✅ Rescatado via regex: {comando}")

    if comando:
        # Limpiar el comando de la respuesta (buscar tanto el comando limpio como con formato)
        # Eliminar cualquier línea que contenga el comando (con o sin formato)
        lineas_respuesta = respuesta.splitlines()
        lineas_limpias = []
        for linea in lineas_respuesta:
            if "ENVIAR_COTIZACION|" not in linea:
                lineas_limpias.append(linea)
        respuesta = "\n".join(lineas_limpias).strip()

        partes = comando.split("|")
        logger.info(f"[COMANDO] Partes ({len(partes)}): {partes}")

        # Aceptar 6 partes (nuevo formato sin teléfono) o 7 partes (formato legacy)
        nombre = producto = link = cantidad = email_cliente = ""
        formato_valido = False

        if len(partes) == 6:
            # Formato nuevo: ENVIAR_COTIZACION|nombre|producto|link|cantidad|email
            _, nombre, producto, link, cantidad, email_cliente = partes
            formato_valido = True
            logger.info("[COMANDO] Formato 6 campos OK")
        elif len(partes) == 7:
            # Formato legacy: ENVIAR_COTIZACION|nombre|producto|link|cantidad|email|telefono
            _, nombre, producto, link, cantidad, email_cliente, _ = partes
            formato_valido = True
            logger.info("[COMANDO] Formato 7 campos (legacy) OK")
        else:
            logger.error(
                f"[COMANDO] FORMATO INVALIDO — esperaba 6 o 7 campos, recibí {len(partes)}. "
                f"Comando completo: {comando}"
            )

        if formato_valido:
            cant_int = int(cantidad.strip()) if cantidad.strip().isdigit() else 1

            # ── EMAIL ──────────────────────────────────────────────────────────
            tracking_id = str(uuid.uuid4())
            exito_email = False
            try:
                exito_email = await enviar_cotizacion_email(
                    email_cliente=email_cliente.strip(),
                    nombre_cliente=nombre.strip(),
                    producto=producto.strip(),
                    link=link.strip(),
                    cantidad=cant_int,
                    telefono_cliente=telefono,
                    tracking_id=tracking_id,
                )
                logger.info(f"[EMAIL] Resultado: {'OK' if exito_email else 'FALLO'}")
            except Exception as e_email:
                logger.error(f"[EMAIL] Excepcion: {type(e_email).__name__}: {e_email}")

            # ── FOLLOW-UP — registrar SOLO si el email se envió exitosamente ──
            if exito_email:
                try:
                    await registrar_cotizacion(
                        telefono=telefono,
                        nombre=nombre.strip(),
                        producto=producto.strip(),
                        email=email_cliente.strip(),
                        tracking_id=tracking_id,
                    )
                    logger.info(f"[FOLLOWUP] Cotización registrada: {nombre.strip()} — tracking={tracking_id}")
                except Exception as e_fu:
                    logger.error(f"[FOLLOWUP] Error registrando: {e_fu}")

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

            if not exito_email:
                respuesta += f"\n\n⚠️ Tuve un problema enviando el email a {email_cliente.strip()}. ¿Podrías verificar que el correo esté bien escrito?"
    else:
        # Log de diagnóstico mejorado
        tiene_email = "@" in respuesta and "." in respuesta.split("@")[-1] if "@" in respuesta else False
        tiene_cotizacion_palabra = any(p in respuesta.lower() for p in ["cotización", "cotizacion", "listo", "enviamos"])
        if tiene_email and tiene_cotizacion_palabra:
            logger.warning(
                f"[COMANDO] ⚠️ Respuesta parece contener datos de cotización pero NO tiene ENVIAR_COTIZACION. "
                f"Claude probablemente olvidó incluir el comando. Respuesta: {respuesta[:400]}"
            )
        else:
            logger.info("[COMANDO] No se detectó ENVIAR_COTIZACION en la respuesta (normal — no era cotización)")

    # Guardar mensaje del usuario Y respuesta del agente en memoria
    await guardar_mensaje(telefono, "user", texto)
    await guardar_mensaje(telefono, "assistant", respuesta)

    log_respuesta_ana(telefono, respuesta)

    # ── Imagen del producto — si el usuario envió un link, mostrar la imagen primero ──
    urls_en_mensaje = re.findall(r'https?://[^\s]+', texto)
    if urls_en_mensaje:
        url_producto = urls_en_mensaje[0]
        try:
            from agent.tools import obtener_imagen_producto
            imagen_bytes = await obtener_imagen_producto(url_producto)
            if imagen_bytes:
                logger.info(f"[IMAGEN] Enviando imagen del producto a {telefono}")
                await proveedor.enviar_imagen(telefono, imagen_bytes)
            else:
                logger.info(f"[IMAGEN] No se encontró imagen en {url_producto}")
        except Exception as e_img:
            logger.warning(f"[IMAGEN] Error enviando imagen: {e_img}")

    # Enviar respuesta por WhatsApp via el proveedor
    await proveedor.enviar_mensaje(telefono, respuesta)

    duracion_total = time.time() - inicio_total
    logger.info(f"[OK] Respuesta a {telefono} enviada en {duracion_total:.1f}s: {respuesta[:200]}...")


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

            logger.info(
                f"[WEBHOOK] Mensaje de {msg.telefono}: {msg.texto[:100]}"
            )

            # Procesar en background — responde 200 OK antes de llamar a Claude
            background_tasks.add_task(procesar_mensaje, msg.telefono, msg.texto)

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        log_error("webhook", str(e))
        raise HTTPException(status_code=500, detail=str(e))
