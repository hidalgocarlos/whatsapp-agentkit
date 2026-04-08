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
import asyncio
import time
from pathlib import Path
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

# Cliente de Anthropic — timeout de 90s para que no se quede colgado
CLAUDE_TIMEOUT_SEGUNDOS = 90
client = AsyncAnthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    timeout=CLAUDE_TIMEOUT_SEGUNDOS,  # Timeout HTTP (conexión + lectura)
)

# Cache del knowledge — se carga al arrancar, no en cada mensaje
_knowledge_cache: str = ""

# Cache de la configuración de prompts
_config_cache: dict = {}

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
    },
    {
        "name": "calcular_precio_imporusa",
        "description": (
            "Calcula el precio final de un producto para el cliente aplicando TODOS los costos "
            "obligatorios: tax de Florida 7% + comisión Imporusa 10-20% + envío Miami → Cali. "
            "SIEMPRE usa esta herramienta cuando tengas el precio en USD de un producto. "
            "Nunca hagas el cálculo de cabeza — usa esta herramienta para dar el desglose completo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "precio_usd": {
                    "type": "number",
                    "description": "Precio del producto en USD tal como aparece en la tienda (sin tax)"
                },
                "cantidad": {
                    "type": "integer",
                    "description": "Cantidad de unidades que pide el cliente",
                    "default": 1
                }
            },
            "required": ["precio_usd"]
        }
    },
    {
        "name": "comparar_precios",
        "description": (
            "Busca y compara precios del mismo producto en Amazon, BestBuy y Walmart, "
            "y calcula cuánto costaría con Imporusa en cada caso (tax FL + comisión + envío). "
            "Úsala cuando el cliente quiera saber dónde está más barato o compara opciones. "
            "También úsala cuando el cliente solo diga el nombre del producto sin dar un link, "
            "para encontrar el precio actual en las tiendas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "producto": {
                    "type": "string",
                    "description": "Nombre del producto a comparar (ej: 'iPhone 16 Pro 256GB', 'Samsung 4K TV 65 pulgadas')"
                }
            },
            "required": ["producto"]
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
    """Lee toda la configuración desde config/prompts.yaml (se cachea en memoria)."""
    global _config_cache
    if _config_cache:
        return _config_cache
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            _config_cache = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        _config_cache = {}
    return _config_cache


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
    elif nombre == "calcular_precio_imporusa":
        from agent.tools import calcular_precio_imporusa
        return calcular_precio_imporusa(
            precio_usd=float(parametros.get("precio_usd", 0)),
            cantidad=int(parametros.get("cantidad", 1)),
        )
    elif nombre == "comparar_precios":
        from agent.tools import comparar_precios
        return await comparar_precios(parametros.get("producto", ""))
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
        # ── Loop de tool use con timeout global ──────────────────────────────
        # Timeout total para toda la generación (incluyendo herramientas)
        TIMEOUT_TOTAL_SEGUNDOS = 120  # 2 minutos máximo por mensaje
        inicio_total = time.time()

        async def _generar_con_tools():
            """Loop interno de tool use — se ejecuta dentro de asyncio.wait_for."""
            max_iteraciones = 5  # Evitar loops infinitos
            iteracion = 0

            while iteracion < max_iteraciones:
                iteracion += 1

                inicio_llamada = time.time()
                response = await client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2048,
                    system=system_prompt,
                    messages=mensajes,
                    tools=TOOLS,
                )
                duracion_llamada = time.time() - inicio_llamada

                logger.info(
                    f"Claude [{iteracion}]: stop_reason={response.stop_reason} "
                    f"({response.usage.input_tokens} in / {response.usage.output_tokens} out) "
                    f"— {duracion_llamada:.1f}s"
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
                            inicio_tool = time.time()
                            resultado = await _ejecutar_herramienta(bloque.name, bloque.input)
                            duracion_tool = time.time() - inicio_tool
                            logger.info(f"Herramienta {bloque.name} completada en {duracion_tool:.1f}s")
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

        # ── Ejecutar con timeout global de seguridad ─────────────────────────
        resultado = await asyncio.wait_for(
            _generar_con_tools(),
            timeout=TIMEOUT_TOTAL_SEGUNDOS
        )
        duracion_total = time.time() - inicio_total
        logger.info(f"Respuesta generada en {duracion_total:.1f}s total")
        return resultado

    except asyncio.TimeoutError:
        duracion_total = time.time() - inicio_total
        logger.error(f"TIMEOUT: La generación tardó más de {TIMEOUT_TOTAL_SEGUNDOS}s (real: {duracion_total:.1f}s)")
        return obtener_mensaje_error()

    except Exception as e:
        logger.error(f"Error Claude API: {type(e).__name__}: {e}")
        return obtener_mensaje_error()
