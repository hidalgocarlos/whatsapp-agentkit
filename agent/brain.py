# agent/brain.py — Cerebro del agente: conexión con Claude API
# Generado por AgentKit

"""
Lógica de IA del agente. Lee el system prompt de prompts.yaml
y genera respuestas usando la API de Anthropic Claude.
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


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    """
    Genera una respuesta usando Claude API.

    Args:
        mensaje: El mensaje nuevo del usuario
        historial: Lista de mensajes anteriores [{"role": "user/assistant", "content": "..."}]

    Returns:
        La respuesta generada por Claude
    """
    # Solo usar fallback si el mensaje está completamente vacío
    if not mensaje or len(mensaje.strip()) == 0:
        return obtener_mensaje_fallback()

    system_prompt = cargar_system_prompt()

    # Agregar knowledge base si hay contenido cargado
    if _knowledge_cache:
        system_prompt += f"\n\n## Información adicional del negocio\n{_knowledge_cache}"

    # Construir mensajes para la API
    mensajes = []
    for msg in historial:
        mensajes.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    # Agregar el mensaje actual
    mensajes.append({
        "role": "user",
        "content": mensaje
    })

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=mensajes
        )

        respuesta = response.content[0].text
        logger.info(f"Respuesta generada ({response.usage.input_tokens} in / {response.usage.output_tokens} out)")
        return respuesta

    except Exception as e:
        logger.error(f"Error Claude API: {e}")
        return obtener_mensaje_error()
