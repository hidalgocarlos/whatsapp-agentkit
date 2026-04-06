# agent/tools.py — Herramientas del agente Imporusa
# Generado por AgentKit

"""
Herramientas específicas de Imporusa.
Funciones para cotizaciones, pedidos, leads, soporte post-venta y envío de emails.
"""

import os
import base64
import yaml
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import httpx
import aiosmtplib
from bs4 import BeautifulSoup

logger = logging.getLogger("agentkit")


def cargar_info_negocio() -> dict:
    """Carga la información del negocio desde business.yaml."""
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("config/business.yaml no encontrado")
        return {}


def obtener_horario() -> dict:
    """Retorna el horario de atención y si está abierto ahora."""
    info = cargar_info_negocio()
    ahora = datetime.now()
    dia_semana = ahora.weekday()  # 0=Lunes, 6=Domingo
    hora_actual = ahora.hour + ahora.minute / 60

    # Lunes a Viernes (0-4): 9am - 6pm
    if 0 <= dia_semana <= 4:
        esta_abierto = 9.0 <= hora_actual < 18.0
    # Sábado (5): 10am - 2pm
    elif dia_semana == 5:
        esta_abierto = 10.0 <= hora_actual < 14.0
    else:
        esta_abierto = False

    return {
        "horario": info.get("negocio", {}).get("horario", "Lunes a Viernes 9am-6pm, Sábados 10am-2pm"),
        "esta_abierto": esta_abierto,
    }


def buscar_en_knowledge(consulta: str) -> str:
    """
    Busca información relevante en los archivos de /knowledge.
    Retorna el contenido más relevante encontrado.
    """
    resultados = []
    knowledge_dir = "knowledge"

    if not os.path.exists(knowledge_dir):
        return "No hay archivos de conocimiento disponibles."

    for archivo in os.listdir(knowledge_dir):
        ruta = os.path.join(knowledge_dir, archivo)
        if archivo.startswith(".") or not os.path.isfile(ruta):
            continue
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                contenido = f.read()
                if consulta.lower() in contenido.lower():
                    resultados.append(f"[{archivo}]: {contenido[:500]}")
        except (UnicodeDecodeError, IOError):
            continue

    if resultados:
        return "\n---\n".join(resultados)
    return "No encontré información específica sobre eso en mis archivos."


def obtener_info_cotizacion() -> dict:
    """Retorna la información necesaria para dar una cotización."""
    return {
        "comision": "Entre el 10% y el 20% sobre el valor del producto",
        "pedido_minimo": "$300.000 COP",
        "tiempo_entrega": "8 días hábiles desde ingreso a Miami",
        "metodos_pago": ["Transferencia bancaria", "ePayco"],
        "campos_requeridos": ["Nombre del producto", "Link o descripción", "Cantidad"],
    }


def obtener_info_pedido() -> dict:
    """Retorna los campos requeridos para procesar un pedido."""
    return {
        "campos_requeridos": [
            "Nombre completo del cliente",
            "Producto (nombre + link o descripción detallada)",
            "Cantidad",
            "Dirección de entrega en Cali",
            "Número de contacto",
        ],
        "proceso": [
            "1. Cliente comparte producto",
            "2. Imporusa cotiza (producto + comisión + envío)",
            "3. Cliente aprueba y paga",
            "4. Imporusa compra y envía a Miami",
            "5. En 8 días llega a domicilio en Cali",
        ],
    }


def calificar_lead(mensaje: str) -> str:
    """
    Evalúa el nivel de interés de un lead basado en su mensaje.
    Retorna: 'alto', 'medio' o 'bajo'
    """
    mensaje_lower = mensaje.lower()

    # Indicadores de alto interés
    palabras_alto = ["comprar", "quiero", "necesito", "cuánto cuesta", "precio",
                     "cotización", "pedido", "urgente", "hoy", "ya"]
    # Indicadores de bajo interés
    palabras_bajo = ["solo preguntando", "curiosidad", "algún día", "tal vez"]

    puntaje = sum(1 for p in palabras_alto if p in mensaje_lower)
    puntaje -= sum(1 for p in palabras_bajo if p in mensaje_lower)

    if puntaje >= 2:
        return "alto"
    elif puntaje >= 1:
        return "medio"
    else:
        return "bajo"


def registrar_solicitud_cotizacion(telefono: str, producto: str, link: str = "") -> str:
    """
    Registra una solicitud de cotización en el log del servidor.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    logger.info(f"[COTIZACIÓN] {timestamp} | Tel: {telefono} | Producto: {producto} | Link: {link}")
    return f"Solicitud registrada para: {producto}"


async def obtener_imagen_producto(url: str) -> bytes | None:
    """
    Descarga la imagen principal de una página de producto.
    Funciona con Amazon, BestBuy, Walmart, eBay y cualquier
    tienda que use la meta og:image estándar.

    Returns:
        Bytes de la imagen, o None si no se pudo obtener.
    """
    if not url or not url.startswith("http"):
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
    }

    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=10) as client:
            # 1. Obtener la página del producto
            r = await client.get(url)
            if r.status_code != 200:
                logger.warning(f"No se pudo acceder a {url}: {r.status_code}")
                return None

            soup = BeautifulSoup(r.text, "html.parser")

            # 2. Buscar og:image (funciona en Amazon, BestBuy, Walmart, eBay, etc.)
            imagen_url = None
            og = soup.find("meta", property="og:image")
            if og and og.get("content"):
                imagen_url = og["content"]

            # Fallback: twitter:image
            if not imagen_url:
                tw = soup.find("meta", attrs={"name": "twitter:image"})
                if tw and tw.get("content"):
                    imagen_url = tw["content"]

            if not imagen_url:
                logger.warning(f"No se encontró imagen en {url}")
                return None

            # 3. Descargar la imagen
            r_img = await client.get(imagen_url)
            if r_img.status_code == 200:
                logger.info(f"Imagen del producto descargada: {imagen_url[:80]}")
                return r_img.content

    except Exception as e:
        logger.warning(f"No se pudo obtener imagen del producto: {e}")

    return None


async def enviar_cotizacion_email(
    email_cliente: str,
    nombre_cliente: str,
    producto: str,
    link: str,
    cantidad: int,
    telefono_cliente: str,
) -> bool:
    """
    Envía la cotización al correo del cliente y una copia a Imporusa.

    Args:
        email_cliente: Correo del cliente donde llega la cotización
        nombre_cliente: Nombre del cliente
        producto: Nombre o descripción del producto
        link: Link del producto (opcional)
        cantidad: Cantidad solicitada
        telefono_cliente: Número de WhatsApp del cliente

    Returns:
        True si el envío fue exitoso
    """
    remitente = os.getenv("EMAIL_REMITENTE", "imporusa@yahoo.com")
    password = os.getenv("EMAIL_PASSWORD", "")
    copia_negocio = os.getenv("EMAIL_COPIA_NEGOCIO", "imporusa@yahoo.com")
    smtp_host = os.getenv("SMTP_HOST", "smtp.mail.yahoo.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    fecha_legible = datetime.now().strftime("%d de %B de %Y")

    # ── Cargar logo como base64 inline (compatible con Resend) ──
    logo_path = os.path.join("knowledge", "logo imporusa.png")
    logo_tag = '<h2 style="color:#1a1a2e;">Imporusa</h2>'
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            logo_b64 = base64.b64encode(f.read()).decode()
        logo_tag = f'<img src="data:image/png;base64,{logo_b64}" alt="Imporusa" style="max-width:200px; margin-bottom:20px;">'

    # ── Descargar imagen del producto e incrustar como base64 ──
    producto_img_data = await obtener_imagen_producto(link) if link else None

    # ── HTML del email al cliente ────────────────────────────
    link_html = f'<a href="{link}" style="color:#0066cc;">{link[:60]}...</a>' if link else "No proporcionado"
    if producto_img_data:
        producto_img_b64 = base64.b64encode(producto_img_data).decode()
        producto_img_tag = (
            f'<tr><td align="center" style="padding:20px 40px 0;">'
            f'<img src="data:image/jpeg;base64,{producto_img_b64}" alt="{producto}" '
            f'style="max-width:300px; max-height:300px; border-radius:8px; border:1px solid #eee;">'
            f'</td></tr>'
        )
    else:
        producto_img_tag = ""

    html_cliente = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif; background:#f4f4f4; margin:0; padding:0;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4; padding:30px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:8px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,0.1);">

        <!-- Encabezado con logo -->
        <tr>
          <td align="center" style="background:#1a1a2e; padding:30px 40px;">
            {logo_tag}
          </td>
        </tr>

        <!-- Imagen del producto -->
        {producto_img_tag}

        <!-- Cuerpo -->
        <tr>
          <td style="padding:40px;">
            <p style="font-size:18px; color:#1a1a2e; margin-top:0;">Hola, <strong>{nombre_cliente}</strong> 👋</p>
            <p style="color:#444; line-height:1.6;">
              Gracias por contactar a <strong>Imporusa</strong>. Hemos recibido tu solicitud de
              cotización con los siguientes datos:
            </p>

            <!-- Tabla de detalles -->
            <table width="100%" cellpadding="10" cellspacing="0"
                   style="background:#f8f9fa; border-radius:6px; margin:20px 0; font-size:14px;">
              <tr>
                <td style="color:#666; width:130px;">📦 Producto</td>
                <td style="color:#1a1a2e; font-weight:bold;">{producto}</td>
              </tr>
              <tr style="background:#fff;">
                <td style="color:#666;">🔗 Link</td>
                <td style="color:#1a1a2e;">{link_html}</td>
              </tr>
              <tr>
                <td style="color:#666;">🔢 Cantidad</td>
                <td style="color:#1a1a2e;">{cantidad}</td>
              </tr>
              <tr style="background:#fff;">
                <td style="color:#666;">📅 Fecha</td>
                <td style="color:#1a1a2e;">{fecha_legible}</td>
              </tr>
            </table>

            <p style="color:#444; line-height:1.6;">
              Nuestro equipo revisará tu solicitud y te contactará pronto con la cotización exacta
              incluyendo precio del producto, comisión y envío.
            </p>

            <!-- Info clave -->
            <table width="100%" cellpadding="8" cellspacing="0"
                   style="border-left:4px solid #0066cc; background:#f0f7ff; border-radius:0 6px 6px 0; margin:20px 0; font-size:14px;">
              <tr><td style="color:#444;">⏱️ <strong>Entrega:</strong> 8 días hábiles desde Miami hasta tu puerta en Cali</td></tr>
              <tr><td style="color:#444;">💳 <strong>Pago:</strong> Transferencia bancaria o ePayco</td></tr>
              <tr><td style="color:#444;">💰 <strong>Pedido mínimo:</strong> $300.000 COP</td></tr>
            </table>

            <p style="color:#444; line-height:1.6;">
              Si tienes dudas, responde a este correo o escríbenos por WhatsApp.
            </p>
            <p style="color:#444;">¡Gracias por confiar en <strong>Imporusa</strong>! 🚀</p>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#1a1a2e; padding:20px 40px; text-align:center;">
            <p style="color:#aaa; font-size:12px; margin:0;">
              Imporusa — Personal Shopping &amp; Importaciones<br>
              <a href="https://imporusa.com" style="color:#6699cc;">imporusa.com</a> |
              imporusa@yahoo.com | Cali, Colombia
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>
"""

    # ── Email interno a Imporusa (texto plano) ───────────────
    html_interno = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif; padding:20px;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="background:#1a1a2e; padding:20px; border-radius:8px 8px 0 0;">
      {logo_tag}
    </td></tr>
    <tr><td style="background:#fff; padding:30px; border:1px solid #ddd; border-radius:0 0 8px 8px;">
      <h2 style="color:#1a1a2e; margin-top:0;">🔔 Nueva solicitud de cotización</h2>
      <table cellpadding="8" cellspacing="0" style="font-size:14px; width:100%;">
        <tr style="background:#f8f9fa;"><td style="color:#666; width:120px;">👤 Cliente</td><td><strong>{nombre_cliente}</strong></td></tr>
        <tr><td style="color:#666;">📱 WhatsApp</td><td>{telefono_cliente}</td></tr>
        <tr style="background:#f8f9fa;"><td style="color:#666;">📧 Email</td><td><a href="mailto:{email_cliente}">{email_cliente}</a></td></tr>
        <tr><td style="color:#666;">📦 Producto</td><td>{producto}</td></tr>
        <tr style="background:#f8f9fa;"><td style="color:#666;">🔗 Link</td><td>{link if link else "No proporcionado"}</td></tr>
        <tr><td style="color:#666;">🔢 Cantidad</td><td>{cantidad}</td></tr>
        <tr style="background:#f8f9fa;"><td style="color:#666;">🕐 Fecha/hora</td><td>{timestamp}</td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

    def _construir_mensaje(de: str, para: str, asunto: str, html: str,
                           incluir_img_producto: bool = False) -> MIMEMultipart:
        """Arma el mensaje MIME con HTML, logo e imagen del producto embebidos."""
        msg = MIMEMultipart("related")
        msg["From"] = de
        msg["To"] = para
        msg["Subject"] = asunto
        alternativa = MIMEMultipart("alternative")
        alternativa.attach(MIMEText(html, "html", "utf-8"))
        msg.attach(alternativa)
        if logo_data:
            img_logo = MIMEImage(logo_data, name="logo imporusa.png")
            img_logo.add_header("Content-ID", f"<{logo_cid}>")
            img_logo.add_header("Content-Disposition", "inline", filename="logo imporusa.png")
            msg.attach(img_logo)
        if incluir_img_producto and producto_img_data:
            img_prod = MIMEImage(producto_img_data, name="producto.jpg")
            img_prod.add_header("Content-ID", f"<{producto_img_cid}>")
            img_prod.add_header("Content-Disposition", "inline", filename="producto.jpg")
            msg.attach(img_prod)
        return msg

    try:
        msg_cliente = _construir_mensaje(
            de=f"Imporusa <{remitente}>",
            para=email_cliente,
            asunto=f"Solicitud de cotización recibida — {producto[:50]}",
            html=html_cliente,
            incluir_img_producto=True,
        )
        msg_interno = _construir_mensaje(
            de=f"Imporusa Bot <{remitente}>",
            para=copia_negocio,
            asunto=f"[LEAD] Nueva cotización: {producto[:50]} — {nombre_cliente}",
            html=html_interno,
            incluir_img_producto=True,
        )

        # Resend API — funciona en Railway sin restricciones de puertos
        resend_key = os.getenv("RESEND_API_KEY", "")
        if not resend_key:
            logger.error("RESEND_API_KEY no configurada — email no enviado")
            return False

        resend_from = os.getenv("RESEND_FROM", "Imporusa <onboarding@resend.dev>")
        headers = {
            "Authorization": f"Bearer {resend_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            # Email al cliente
            r1 = await client.post(
                "https://api.resend.com/emails",
                headers=headers,
                json={
                    "from": resend_from,
                    "to": [email_cliente],
                    "subject": f"Solicitud de cotización recibida — {producto[:50]}",
                    "html": html_cliente,
                },
            )
            if r1.status_code == 200:
                logger.info(f"Email enviado al cliente: {email_cliente}")
            else:
                logger.error(f"Error Resend (cliente): {r1.status_code} — {r1.text}")

            # Copia interna a Imporusa
            r2 = await client.post(
                "https://api.resend.com/emails",
                headers=headers,
                json={
                    "from": resend_from,
                    "to": [copia_negocio],
                    "subject": f"[LEAD] Nueva cotización: {producto[:50]} — {nombre_cliente}",
                    "html": html_interno,
                },
            )
            if r2.status_code == 200:
                logger.info(f"Copia interna enviada a: {copia_negocio}")
            else:
                logger.error(f"Error Resend (interno): {r2.status_code} — {r2.text}")

        return r1.status_code == 200

    except Exception as e:
        logger.error(f"Error enviando email de cotización: {type(e).__name__}: {e}")
        return False
