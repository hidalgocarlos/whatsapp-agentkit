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
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("agentkit")

# ── Cache de TRM para no consultar la API en cada mensaje ──
_trm_cache: dict = {"valor": None, "fecha": None, "timestamp": None}


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


async def obtener_trm() -> str:
    """
    Obtiene la Tasa Representativa del Mercado (TRM) del día desde datos.gov.co.
    La TRM es el tipo de cambio oficial USD → COP del Banco de la República de Colombia.
    Usa cache de 6 horas para no abusar de la API.

    Returns:
        String con el valor de la TRM, fecha de vigencia e instrucciones de uso.
    """
    global _trm_cache

    # Verificar cache (válido por 6 horas)
    if _trm_cache["valor"] and _trm_cache["timestamp"]:
        edad_horas = (datetime.now() - _trm_cache["timestamp"]).total_seconds() / 3600
        if edad_horas < 6:
            logger.info(f"[TRM] Usando cache: ${_trm_cache['valor']} COP (edad: {edad_horas:.1f}h)")
            return (
                f"TRM del día ({_trm_cache['fecha']}): 1 USD = {_trm_cache['valor']} COP.\n"
                f"Fuente: Banco de la República (datos.gov.co).\n"
                f"Para convertir: multiplica el precio en USD por {_trm_cache['valor']}."
            )

    async def _trm_desde_datos_gov() -> tuple[str, str] | None:
        """Intenta obtener TRM desde datos.gov.co (fuente oficial)."""
        url = "https://www.datos.gov.co/resource/32sa-8pi3.json"
        params = {"$order": "vigenciadesde DESC", "$limit": "1"}
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                logger.warning(f"[TRM] datos.gov.co retornó {r.status_code} — se usará fallback")
                return None
            data = r.json()
            if not data:
                return None
            registro = data[0]
            valor = registro.get("valor", "")
            vigencia = registro.get("vigenciadesde", "")[:10]
            valor_float = float(valor)
            return f"{valor_float:,.2f}", vigencia

    async def _trm_desde_exchangerate() -> tuple[str, str] | None:
        """Fallback: obtiene COP/USD desde open.er-api.com (gratis, sin API key)."""
        url = "https://open.er-api.com/v6/latest/USD"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            if r.status_code != 200:
                logger.warning(f"[TRM] open.er-api.com retornó {r.status_code}")
                return None
            data = r.json()
            cop = data.get("rates", {}).get("COP")
            if not cop:
                return None
            fecha = data.get("time_last_update_utc", "")[:10]
            return f"{float(cop):,.2f}", fecha

    try:
        resultado = await _trm_desde_datos_gov()
        fuente = "Banco de la República (datos.gov.co)"

        if resultado is None:
            resultado = await _trm_desde_exchangerate()
            fuente = "Open Exchange Rates (fallback)"

        if resultado is None:
            return "No pude obtener la TRM en este momento. Usa un valor aproximado de referencia."

        valor_formateado, vigencia = resultado

        # Actualizar cache
        _trm_cache["valor"] = valor_formateado
        _trm_cache["fecha"] = vigencia
        _trm_cache["timestamp"] = datetime.now()

        logger.info(f"[TRM] Obtenida: 1 USD = {valor_formateado} COP (vigencia: {vigencia}) — fuente: {fuente}")
        return (
            f"TRM del día ({vigencia}): 1 USD = {valor_formateado} COP.\n"
            f"Fuente: {fuente}.\n"
            f"Para convertir: multiplica el precio en USD por {valor_formateado}."
        )

    except Exception as e:
        logger.error(f"[TRM] Error consultando TRM: {type(e).__name__}: {e}")
        return "Error al consultar la TRM. Intenta de nuevo en un momento."


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


def calcular_precio_imporusa(precio_usd: float, cantidad: int = 1) -> str:
    """
    Calcula el precio final de un producto importado desde Miami a Cali.

    Fórmula obligatoria:
      1. precio_con_tax  = precio_usd * cantidad * 1.07   (tax FL 7%)
      2. precio_imporusa = precio_con_tax * (1.10 ... 1.20)  (comisión 10-20%)
      3. precio_final    = precio_imporusa + envío Miami → Cali

    Retorna un desglose completo en texto listo para WhatsApp.
    """
    info = cargar_info_negocio()
    cfg = info.get("precios", {})

    TAX_FL       = cfg.get("tax_florida_pct",          0.07)
    COM_MIN      = cfg.get("comision_min_pct",          0.10)
    COM_MAX      = cfg.get("comision_max_pct",          0.20)
    ENVIO_MIN    = cfg.get("envio_miami_cali_min_usd",  20)
    ENVIO_MAX    = cfg.get("envio_miami_cali_max_usd",  50)

    precio_base   = round(precio_usd * cantidad, 2)
    tax           = round(precio_base * TAX_FL, 2)
    precio_miami  = round(precio_base + tax, 2)

    comision_min  = round(precio_miami * COM_MIN, 2)
    comision_max  = round(precio_miami * COM_MAX, 2)

    total_min     = round(precio_miami + comision_min + ENVIO_MIN, 2)
    total_max     = round(precio_miami + comision_max + ENVIO_MAX, 2)

    lineas = [
        f"💰 *Desglose de precio Imporusa* ({cantidad} unidad{'es' if cantidad != 1 else ''})\n",
        f"Precio en tienda (USD):          ${precio_base:>10,.2f}",
        f"+ Tax Florida ({TAX_FL*100:.0f}%):            +${tax:>9,.2f}",
        f"= Precio en Miami:               ${precio_miami:>10,.2f}\n",
        f"+ Comisión Imporusa ({COM_MIN*100:.0f}%-{COM_MAX*100:.0f}%):  +${comision_min:,.2f} — +${comision_max:,.2f}",
        f"+ Envío Miami → Cali:            +${ENVIO_MIN} — +${ENVIO_MAX} USD\n",
        f"*Precio final estimado: ${total_min:,.2f} — ${total_max:,.2f} USD*",
    ]

    # Convertir a COP si hay TRM en cache
    trm_val = None
    if _trm_cache.get("valor"):
        try:
            trm_val = float(str(_trm_cache["valor"]).replace(",", ""))
        except (ValueError, TypeError):
            pass
    if trm_val:
        cop_min = int(total_min * trm_val)
        cop_max = int(total_max * trm_val)
        trm_str = _trm_cache["valor"]
        lineas.append(
            f"\nEn pesos (TRM {trm_str} COP/USD):\n"
            f"*${cop_min:,} — ${cop_max:,} COP*"
        )

    return "\n".join(lineas)


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
    _ahora = datetime.now()
    timestamp = _ahora.strftime("%Y-%m-%d %H:%M")
    _meses = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
              "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    fecha_legible = f"{_ahora.day:02d} de {_meses[_ahora.month - 1]} de {_ahora.year}"
    copia_negocio = os.getenv("EMAIL_COPIA_NEGOCIO", "imporusa@yahoo.com")

    # ── Obtener TRM del día para incluir en el email ──
    trm_valor = ""
    trm_fecha = ""
    try:
        if _trm_cache["valor"] and _trm_cache["timestamp"]:
            edad_horas = (datetime.now() - _trm_cache["timestamp"]).total_seconds() / 3600
            if edad_horas < 6:
                trm_valor = _trm_cache["valor"]
                trm_fecha = _trm_cache["fecha"]
        if not trm_valor:
            # Consultar la API directamente
            async with httpx.AsyncClient(timeout=10) as trm_client:
                r_trm = await trm_client.get(
                    "https://www.datos.gov.co/resource/32sa-8pi3.json",
                    params={"$order": "vigenciadesde DESC", "$limit": "1"},
                )
                if r_trm.status_code == 200:
                    data_trm = r_trm.json()
                    if data_trm:
                        val = data_trm[0].get("valor", "")
                        try:
                            trm_valor = f"{float(val):,.2f}"
                        except (ValueError, TypeError):
                            trm_valor = val
                        trm_fecha = data_trm[0].get("vigenciadesde", "")[:10]
                        # Actualizar cache
                        _trm_cache["valor"] = trm_valor
                        _trm_cache["fecha"] = trm_fecha
                        _trm_cache["timestamp"] = datetime.now()
    except Exception as e_trm:
        logger.warning(f"[EMAIL] No se pudo obtener TRM para el email: {e_trm}")

    trm_html_cliente = ""
    trm_html_interno = ""
    if trm_valor:
        trm_html_cliente = (
            f'<tr style="background-color:#f0fdf4;">'
            f'<td style="padding:12px 16px;font-size:14px;color:#6b7280;border-bottom:1px solid #f3f4f6;">💱 TRM del día</td>'
            f'<td style="padding:12px 16px;font-size:14px;color:#16a34a;font-weight:700;border-bottom:1px solid #f3f4f6;">'
            f'1 USD = {trm_valor} COP <span style="font-size:11px;color:#6b7280;font-weight:400;">({trm_fecha})</span></td>'
            f'</tr>'
        )
        trm_html_interno = (
            f'<tr><td style="color:#666;">💱 TRM</td>'
            f'<td><strong style="color:#16a34a;">1 USD = {trm_valor} COP</strong> ({trm_fecha})</td></tr>'
        )

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

    html_cliente = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Imporusa | Tu Cotización</title>
<style>
  @media only screen and (max-width:600px) {{
    .wrapper {{ padding: 8px !important; }}
    .main-card {{ padding: 24px 16px !important; }}
    .hero-title {{ font-size: 22px !important; }}
    .card-row {{ display: block !important; width: 100% !important; }}
    .card-cell {{ display: block !important; width: 100% !important; margin-bottom: 10px !important; }}
    .footer-cell {{ display: block !important; text-align: center !important; padding-bottom: 8px !important; }}
    .cta-btn {{ width: 100% !important; text-align: center !important; box-sizing: border-box !important; }}
    .banner-badge {{ display: block !important; margin-top: 8px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background-color:#f3f4f6;font-family:Arial,Helvetica,sans-serif;-webkit-text-size-adjust:100%;">

<table width="100%" cellpadding="0" cellspacing="0" class="wrapper" style="background-color:#f3f4f6;padding:24px 12px;">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;">

  <!-- Banner -->
  <tr>
    <td style="background-color:#e0e7ff;border-radius:10px 10px 0 0;padding:12px 20px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="font-size:12px;font-weight:700;color:#3730a3;letter-spacing:0.05em;">
            ✅ Cotización recibida — {fecha_legible}
          </td>
          <td align="right">
            <span class="banner-badge" style="font-size:10px;font-weight:800;color:#fff;background-color:#dc2626;padding:4px 10px;border-radius:20px;white-space:nowrap;">
              Válida 24h
            </span>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Card principal -->
  <tr>
    <td class="main-card" style="background-color:#ffffff;padding:32px 28px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">

      <!-- Logo -->
      <div style="margin-bottom:24px;">
        {logo_tag}
        <div style="font-size:11px;color:#9ca3af;margin-top:4px;letter-spacing:0.05em;text-transform:uppercase;">Personal Shopping &amp; Importaciones</div>
      </div>

      <!-- Saludo -->
      <h1 class="hero-title" style="font-size:26px;font-weight:900;color:#111827;margin:0 0 10px;line-height:1.2;">
        Hola, {nombre_cliente} 👋
      </h1>
      <p style="font-size:15px;color:#4b5563;line-height:1.65;margin:0 0 24px;">
        Recibimos tu solicitud. Te contactaremos pronto con el precio exacto:
        producto + comisión + envío a tu puerta en Cali.
      </p>

      <!-- Imagen del producto -->
      {producto_img_tag}

      <!-- Detalles -->
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;border-radius:10px;overflow:hidden;border:1px solid #e5e7eb;">
        <tr style="background-color:#f9fafb;">
          <td colspan="2" style="padding:10px 16px;font-size:11px;font-weight:700;color:#6b7280;letter-spacing:0.08em;text-transform:uppercase;border-bottom:1px solid #e5e7eb;">
            Detalle del pedido
          </td>
        </tr>
        <tr>
          <td style="padding:12px 16px;font-size:14px;color:#6b7280;width:38%;border-bottom:1px solid #f3f4f6;">📦 Producto</td>
          <td style="padding:12px 16px;font-size:14px;color:#111827;font-weight:700;border-bottom:1px solid #f3f4f6;word-break:break-word;">{producto}</td>
        </tr>
        <tr style="background-color:#f9fafb;">
          <td style="padding:12px 16px;font-size:14px;color:#6b7280;border-bottom:1px solid #e5e7eb;">🔗 Enlace</td>
          <td style="padding:12px 16px;font-size:13px;border-bottom:1px solid #e5e7eb;word-break:break-all;">{link_html}</td>
        </tr>
        <tr>
          <td style="padding:12px 16px;font-size:14px;color:#6b7280;border-bottom:1px solid #f3f4f6;">🔢 Cantidad</td>
          <td style="padding:12px 16px;font-size:14px;color:#111827;font-weight:700;border-bottom:1px solid #f3f4f6;">{cantidad} unidad(es)</td>
        </tr>
        <tr style="background-color:#f9fafb;">
          <td style="padding:12px 16px;font-size:14px;color:#6b7280;">📅 Fecha</td>
          <td style="padding:12px 16px;font-size:14px;color:#111827;">{fecha_legible}</td>
        </tr>
        {trm_html_cliente}
      </table>

      <!-- CTA Button -->
      {'<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;"><tr><td align="center"><a href="' + link + '" class="cta-btn" style="display:inline-block;background-color:#1d4ed8;color:#ffffff;font-size:15px;font-weight:700;padding:16px 28px;border-radius:10px;text-decoration:none;min-width:200px;text-align:center;">Ver Producto →</a></td></tr></table>' if link else ''}

      <!-- Cards de valor — apiladas en móvil -->
      <table width="100%" cellpadding="0" cellspacing="0" class="card-row" style="margin-bottom:24px;">
        <tr class="card-row">
          <td class="card-cell" width="31%" style="background-color:#f0f4ff;border-radius:8px;padding:14px;vertical-align:top;border-bottom:3px solid #4f46e5;">
            <div style="font-size:22px;margin-bottom:6px;">⚡</div>
            <div style="font-size:13px;font-weight:700;color:#1e1b4b;margin-bottom:4px;">Entrega en 8 días</div>
            <div style="font-size:12px;color:#4b5563;">Desde Miami hasta tu puerta en Cali.</div>
          </td>
          <td width="3%"></td>
          <td class="card-cell" width="31%" style="background-color:#f0fdf4;border-radius:8px;padding:14px;vertical-align:top;border-bottom:3px solid #16a34a;">
            <div style="font-size:22px;margin-bottom:6px;">🛡️</div>
            <div style="font-size:13px;font-weight:700;color:#14532d;margin-bottom:4px;">Compra Segura</div>
            <div style="font-size:12px;color:#4b5563;">Cuenta bancaria en EE.UU. Cualquier tienda.</div>
          </td>
          <td width="3%"></td>
          <td class="card-cell" width="31%" style="background-color:#fff7ed;border-radius:8px;padding:14px;vertical-align:top;border-bottom:3px solid #ea580c;">
            <div style="font-size:22px;margin-bottom:6px;">🏆</div>
            <div style="font-size:13px;font-weight:700;color:#7c2d12;margin-bottom:4px;">20+ Años</div>
            <div style="font-size:12px;color:#4b5563;">Bodega propia en Miami. Experiencia real.</div>
          </td>
        </tr>
      </table>

      <!-- Condiciones -->
      <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#eff6ff;border-left:4px solid #1d4ed8;border-radius:0 8px 8px 0;margin-bottom:24px;">
        <tr>
          <td style="padding:14px 18px;font-size:14px;color:#1e40af;line-height:1.9;">
            <strong>💳 Pago:</strong> Transferencia bancaria o ePayco<br>
            <strong>💰 Comisión:</strong> 10% – 20% sobre el valor del producto<br>
            <strong>📦 Mínimo:</strong> $300.000 COP
          </td>
        </tr>
      </table>

      <p style="font-size:14px;color:#6b7280;line-height:1.6;margin:0;">
        ¿Dudas? Responde este correo o escríbenos por WhatsApp.<br>
        <strong style="color:#111827;">¡Gracias por confiar en Imporusa!</strong> 🚀
      </p>

    </td>
  </tr>

  <!-- Footer -->
  <tr>
    <td style="background-color:#111827;padding:20px 28px;border-radius:0 0 10px 10px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td class="footer-cell" style="font-size:12px;color:#9ca3af;padding-bottom:4px;">
            <strong style="color:#ffffff;">Imporusa</strong> — Cali, Colombia<br>
            <a href="https://imporusa.com" style="color:#60a5fa;text-decoration:none;">imporusa.com</a>
          </td>
          <td align="right" class="footer-cell" style="font-size:11px;color:#6b7280;">
            © {datetime.now().year} Imporusa
          </td>
        </tr>
      </table>
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
        {trm_html_interno}
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

    try:
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


async def buscar_web(query: str) -> str:
    """
    Busca información actualizada en internet usando Tavily.
    Retorna un resumen con los resultados más relevantes.
    """
    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        return "Búsqueda web no disponible (TAVILY_API_KEY no configurada)."

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 5,
                    "include_answer": True,
                }
            )
            if r.status_code != 200:
                logger.error(f"Error Tavily: {r.status_code} — {r.text[:200]}")
                return "No se pudo realizar la búsqueda en este momento."

            data = r.json()
            answer = data.get("answer", "")
            results = data.get("results", [])

            output = ""
            if answer:
                output += f"Respuesta directa: {answer}\n\n"

            for res in results[:4]:
                title = res.get("title", "")
                content = res.get("content", "")[:400]
                url = res.get("url", "")
                output += f"📌 {title}\n{content}\nFuente: {url}\n\n"

            logger.info(f"Búsqueda Tavily: '{query}' — {len(results)} resultados")
            return output.strip() or "No se encontraron resultados."

    except Exception as e:
        logger.error(f"Error Tavily: {e}")
        return f"Error en la búsqueda: {e}"


async def obtener_pagina(url: str) -> str:
    """
    Extrae el contenido de texto de una URL. Útil para leer páginas de producto,
    artículos, fichas técnicas, precios en tiendas, etc.
    """
    try:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"}
        ) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return f"No se pudo acceder a la página (código {r.status_code})."

            soup = BeautifulSoup(r.text, "html.parser")

            # Eliminar elementos que no aportan contenido
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
                tag.decompose()

            title = soup.find("title")
            title_text = title.get_text().strip() if title else ""

            # Extraer texto limpio
            lines = [l.strip() for l in soup.get_text(separator="\n").splitlines() if l.strip()]
            content = "\n".join(lines[:120])

            logger.info(f"Página obtenida: {url[:80]}")
            return f"Título: {title_text}\n\nContenido:\n{content[:3500]}"

    except Exception as e:
        logger.error(f"Error obteniendo página {url}: {e}")
        return f"No se pudo acceder a la página: {e}"


async def crear_prospecto_notion(
    nombre: str,
    email: str,
    whatsapp: str,
    producto: str,
    resumen_chat: str = "",
) -> bool:
    """
    Crea un nuevo prospecto en la base de datos de Notion.
    Se llama automáticamente cuando Ana recopila todos los datos de cotización.
    """
    notion_token = os.getenv("NOTION_TOKEN", "")
    notion_db = os.getenv("NOTION_DB_ID", "")

    if not notion_token or not notion_db:
        logger.warning("NOTION_TOKEN o NOTION_DB_ID no configurados — prospecto no guardado")
        return False

    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    # Truncar resumen para la propiedad "Texto" (rich_text máximo 2000 chars)
    texto_propiedad = resumen_chat[:2000] if resumen_chat else ""

    payload = {
        "parent": {"database_id": notion_db},
        "properties": {
            "Nombre": {
                "title": [{"text": {"content": nombre}}]
            },
            "Email": {
                "email": email
            },
            "WhatsApp": {
                "phone_number": whatsapp.replace("@s.whatsapp.net", "").replace("+", "")
            },
            "Producto": {
                "rich_text": [{"text": {"content": producto[:200]}}]
            },
            "Estado": {
                "select": {"name": "Nuevo"}
            },
            "Fecha": {
                "date": {"start": datetime.now().strftime("%Y-%m-%d")}
            },
            "Texto": {
                "rich_text": [{"text": {"content": texto_propiedad}}] if texto_propiedad else []
            },
        },
    }

    # Agregar resumen del chat como contenido de la página
    # Notion limita cada bloque a 2000 caracteres, así que dividimos en chunks
    if resumen_chat:
        bloques = []
        # Dividir en trozos de máximo 2000 caracteres sin cortar palabras
        texto_restante = resumen_chat
        while texto_restante:
            if len(texto_restante) <= 2000:
                trozo = texto_restante
                texto_restante = ""
            else:
                # Buscar el último salto de línea antes del límite
                corte = texto_restante[:2000].rfind("\n")
                if corte < 0:
                    # No hay salto de línea — cortar en el último espacio
                    corte = texto_restante[:2000].rfind(" ")
                if corte < 0:
                    # Sin espacios ni saltos — cortar en el límite duro
                    corte = 2000
                trozo = texto_restante[:corte]
                texto_restante = texto_restante[corte:].lstrip()

            bloques.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"text": {"content": trozo}}]
                }
            })
        payload["children"] = bloques

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            logger.info(f"[NOTION] Enviando prospecto: {nombre} | Email: {email} | Producto: {producto[:50]} | Texto: {len(texto_propiedad)} chars | Children: {len(payload.get('children', []))} bloques")
            r = await client.post(
                "https://api.notion.com/v1/pages",
                headers=headers,
                json=payload,
            )
            if r.status_code == 200:
                page_id = r.json().get("id", "?")
                logger.info(f"[NOTION] Prospecto creado OK: {nombre} — page_id={page_id}")
                return True
            else:
                logger.error(f"[NOTION] Error HTTP {r.status_code}: {r.text[:500]}")
                return False
    except Exception as e:
        logger.error(f"[NOTION] Excepcion: {type(e).__name__}: {e}")
        return False
