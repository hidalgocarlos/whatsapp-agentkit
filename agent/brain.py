# agent/brain.py — Cerebro del agente: conexión con Claude API + tool use
# Generado por AgentKit

"""
Lógica de IA del agente. Lee el system prompt de prompts.yaml,
genera respuestas usando Claude API y ejecuta herramientas de búsqueda web
cuando el agente lo necesite (Tavily + scraping de páginas).
"""

import os
import yaml
import logging
from pathlib import Path
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

# Cliente de Anthropic
client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Cache del knowledge — se carga al arrancar, no en cada mensaje
_knowledge_cache: str = ""

# ── Definición de herramientas que Claude puede usar ──────────────────────────
TOOLS = [
    {
        "name": "buscar_web",
        "description": (
            "Busca información actualizada en internet. Úsala cuando el cliente pregunte "
            "por un producto específico, modelo, precio, disponibilidad, especificaciones "
            "técnicas, o cualquier dato que no tengas en tu conocimiento base. "
            "Siempre busca antes de decir que no tienes información."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "La búsqueda a realizar. Sé específico: incluye nombre del producto, modelo y qué necesitas saber (precio, specs, disponibilidad en Amazon, etc.)."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "obtener_pagina",
        "description": (
            "Obtiene el contenido completo de una URL. Úsala cuando el cliente comparte "
            "un link de producto para extraer nombre, precio, especificaciones y detalles. "
            "También sirve para leer páginas de tiendas, artículos o fichas técnicas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "La URL completa de la página a consultar."
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "obtener_trm",
        "description": (
            "Obtiene la Tasa Representativa del Mercado (TRM) del día: el tipo de cambio "
            "oficial USD → COP del Banco de la República de Colombia. "
            "SIEMPRE usa esta herramienta antes de mencionar precios en pesos colombianos. "
            "Nunca inventes ni estimes la TRM — siempre consulta el valor real del día."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]


def cargar_knowledge() -> str:
    """Lee todos los archivos de /knowledge al arrancar y los guarda en memoria."""
    global _knowledge_cache
    knowledge_dir = Path("knowledge")
    textos = []

    extensiones_texto = {".txt", ".md", ".csv", ".json", ".yaml", ".yml"}
    extensiones_ignorar = {".gitkeep", ".docx", ".pdf", ".png", ".jpg", ".jpeg"}

    for archivo in sorted(knowledge_dir.iterdir()):
        if not archivo.is_file():
            continue
        if archivo.name.startswith("~") or archivo.name.startswith("."):
            continue
        if archivo.suffix.lower() in extensiones_ignorar:
            continue
        if archivo.suffix.lower() in extensiones_texto:
            try:
                contenido = archivo.read_text(encoding="utf-8", errors="ignore")
                # Limitar a 2000 caracteres por archivo para no inflar el contexto
                if len(contenido) > 2000:
                    contenido = contenido[:2000] + "\n...[contenido truncado]"
                textos.append(f"### {archivo.name}\n{contenido}")
                logger.info(f"Knowledge cargado: {archivo.name}")
            except Exception as e:
                logger.warning(f"No se pudo leer {archivo.name}: {e}")

    _knowledge_cache = "\n\n".join(textos)
    if _knowledge_cache:
        logger.info(f"Knowledge base cargada: {len(textos)} archivos")
    return _knowledge_cache


def cargar_config_prompts() -> dict:
    """Lee toda la configuración desde config/prompts.yaml."""
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def cargar_system_prompt() -> str:
    """Lee el system prompt desde config/prompts.yaml."""
    config = cargar_config_prompts()
    return config.get("system_prompt", "Eres Ana, la asistente virtual de Imporusa. Responde en español.")


def obtener_mensaje_error() -> str:
    """Retorna el mensaje de error configurado en prompts.yaml."""
    config = cargar_config_prompts()
    return config.get("error_message", "Lo siento, estoy teniendo problemas técnicos. Por favor intenta de nuevo en unos minutos.")


def obtener_mensaje_fallback() -> str:
    """Retorna el mensaje de fallback configurado en prompts.yaml."""
    config = cargar_config_prompts()
    return config.get("fallback_message", "Disculpa, no entendí tu mensaje. ¿Podrías reformularlo? 😊")


async def _ejecutar_herramienta(nombre: str, parametros: dict) -> str:
    """Ejecuta la herramienta solicitada por Claude y retorna el resultado."""
    from agent.tools import buscar_web, obtener_pagina, obtener_trm

    logger.info(f"Herramienta ejecutada: {nombre} — params: {parametros}")

    if nombre == "buscar_web":
        return await buscar_web(parametros.get("query", ""))
    elif nombre == "obtener_pagina":
        return await obtener_pagina(parametros.get("url", ""))
    elif nombre == "obtener_trm":
        return await obtener_trm()
    else:
        return f"Herramienta desconocida: {nombre}"


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    """
    Genera una respuesta usando Claude API con soporte de tool use.
    Claude puede buscar en la web o leer páginas cuando lo necesite.

    Args:
        mensaje: El mensaje nuevo del usuario
        historial: Lista de mensajes anteriores [{"role": "user/assistant", "content": "..."}]

    Returns:
        La respuesta generada por Claude (texto limpio para WhatsApp)
    """
    if not mensaje or len(mensaje.strip()) == 0:
        return obtener_mensaje_fallback()

    system_prompt = cargar_system_prompt()

    # Agregar knowledge base si hay contenido cargado
    if _knowledge_cache:
        system_prompt += f"\n\n## Información adicional del negocio\n{_knowledge_cache}"

    # Agregar instrucciones de formato para WhatsApp
    system_prompt += (
        "\n\n## Formato de respuestas"
        "\nUsa formato compatible con WhatsApp:"
        "\n- *texto* para negrita"
        "\n- _texto_ para cursiva"
        "\n- Listas con guiones o números"
        "\n- Emojis para hacer los mensajes más visuales"
        "\nEvita HTML, markdown estándar (##, **) o tablas. Mantén respuestas concisas."
    )

    # Construir mensajes para la API
    mensajes = []
    for msg in historial:
        mensajes.append({"role": msg["role"], "content": msg["content"]})
    mensajes.append({"role": "user", "content": mensaje})

    try:
        # ── Loop de tool use ──────────────────────────────────────────────────
        max_iteraciones = 5  # Evitar loops infinitos
        iteracion = 0

        while iteracion < max_iteraciones:
            iteracion += 1

            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=system_prompt,
                messages=mensajes,
                tools=TOOLS,
            )

            logger.info(
                f"Claude [{iteracion}]: stop_reason={response.stop_reason} "
                f"({response.usage.input_tokens} in / {response.usage.output_tokens} out)"
            )

            # Respuesta final — extraer texto
            if response.stop_reason == "end_turn":
                texto = ""
                for bloque in response.content:
                    if hasattr(bloque, "text"):
                        texto += bloque.text
                return texto.strip()

            # Claude quiere usar una herramienta
            elif response.stop_reason == "tool_use":
                # Agregar la respuesta del asistente al historial de mensajes
                mensajes.append({"role": "assistant", "content": response.content})

                # Ejecutar cada herramienta solicitada
                resultados_tools = []
                for bloque in response.content:
                    if bloque.type == "tool_use":
                        resultado = await _ejecutar_herramienta(bloque.name, bloque.input)
                        resultados_tools.append({
                            "type": "tool_result",
                            "tool_use_id": bloque.id,
                            "content": resultado,
                        })

                # Agregar resultados de herramientas al historial
                mensajes.append({"role": "user", "content": resultados_tools})

            else:
                # stop_reason inesperado — salir del loop
                logger.warning(f"stop_reason inesperado: {response.stop_reason}")
                break

        # Si llegamos aquí sin respuesta de texto, extraer lo que haya
        logger.warning("Se alcanzó el límite de iteraciones de tool use")
        return obtener_mensaje_error()

    except Exception as e:
        logger.error(f"Error Claude API: {e}")
        return obtener_mensaje_error()
