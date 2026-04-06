# agent/providers/whapi.py — Adaptador para Whapi.cloud
# Generado por AgentKit

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorWhapi(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando Whapi.cloud (REST API simple)."""

    def __init__(self):
        self.token = os.getenv("WHAPI_TOKEN")
        self.url_envio = "https://gate.whapi.cloud/messages/text"

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload de Whapi.cloud — soporta texto, links y documentos."""
        body = await request.json()
        mensajes = []
        for msg in body.get("messages", []):
            tipo = msg.get("type", "")
            logger.info(f"Mensaje tipo={tipo} keys={list(msg.keys())}")

            # Extraer texto según el tipo de mensaje
            texto = ""

            if tipo == "text":
                texto = msg.get("text", {}).get("body", "")

            elif tipo == "extended_text":
                texto = msg.get("extended_text", {}).get("text", "")

            elif tipo == "link_preview":
                lp = msg.get("link_preview", {})
                texto = lp.get("body", "") or lp.get("url", "") or lp.get("title", "")

            elif tipo == "image":
                texto = msg.get("image", {}).get("caption", "[imagen sin texto]")

            elif tipo == "document":
                nombre = msg.get("document", {}).get("file_name", "documento")
                texto = f"[documento: {nombre}]"

            else:
                # Tipo no soportado — loguear para diagnóstico
                logger.warning(f"Tipo no soportado: {tipo} — payload: {msg}")
                continue

            # Si no hay texto, intentar extraer de cualquier campo conocido
            if not texto:
                for campo in ["text", "extended_text", "link_preview"]:
                    data = msg.get(campo, {})
                    texto = data.get("body", "") or data.get("text", "") or data.get("url", "")
                    if texto:
                        break

            if not texto:
                logger.warning(f"Mensaje tipo={tipo} sin texto extraíble — payload: {msg}")
                continue

            mensajes.append(MensajeEntrante(
                telefono=msg.get("chat_id", ""),
                texto=texto,
                mensaje_id=msg.get("id", ""),
                es_propio=msg.get("from_me", False),
            ))
        return mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje via Whapi.cloud."""
        if not self.token:
            logger.warning("WHAPI_TOKEN no configurado — mensaje no enviado")
            return False
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(
                self.url_envio,
                json={"to": telefono, "body": mensaje},
                headers=headers,
            )
            if r.status_code != 200:
                logger.error(f"Error Whapi: {r.status_code} — {r.text}")
            return r.status_code == 200
