# agent/session_logger.py — Logger de sesiones y conversaciones
# Generado por AgentKit

"""
Guarda todas las conversaciones en archivos de log diarios.
Formato legible para revisión y análisis.

Archivos generados en logs/:
  conversaciones_2026-04-05.log  — mensajes de todas las sesiones del día
  cotizaciones_2026-04-05.log    — solo las cotizaciones solicitadas
  errores_2026-04-05.log         — errores del sistema
"""

import os
import logging
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

# Crear carpeta logs/ si no existe
LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)


def _crear_logger(nombre: str, archivo_base: str) -> logging.Logger:
    """Crea un logger que rota automáticamente cada día a medianoche."""
    log = logging.getLogger(nombre)
    if log.handlers:
        return log  # Ya inicializado, evitar duplicados

    log.setLevel(logging.INFO)
    ruta = os.path.join(LOGS_DIR, archivo_base)
    handler = TimedRotatingFileHandler(
        ruta,
        when="midnight",
        interval=1,
        backupCount=90,        # Conserva 90 días de historial
        encoding="utf-8",
        utc=False,
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    log.propagate = False
    return log


# Loggers especializados
_log_conversaciones = _crear_logger("sesiones.conversaciones", "conversaciones.log")
_log_cotizaciones   = _crear_logger("sesiones.cotizaciones",   "cotizaciones.log")
_log_errores        = _crear_logger("sesiones.errores",        "errores.log")


def _ts() -> str:
    """Timestamp legible para los logs."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_mensaje_cliente(telefono: str, texto: str):
    """Registra un mensaje enviado por el cliente."""
    _log_conversaciones.info(
        f"[{_ts()}] [{telefono}] CLIENTE: {texto}"
    )


def log_respuesta_ana(telefono: str, texto: str):
    """Registra una respuesta de Ana al cliente."""
    _log_conversaciones.info(
        f"[{_ts()}] [{telefono}] ANA:     {texto}"
    )
    _log_conversaciones.info(
        f"[{_ts()}] [{telefono}] {'─' * 60}"
    )


def log_cotizacion(telefono: str, nombre: str, producto: str,
                   link: str, cantidad: int, email: str, exito: bool):
    """Registra una cotización enviada por email."""
    estado = "✅ ENVIADA" if exito else "❌ ERROR"
    _log_cotizaciones.info(
        f"[{_ts()}] {estado}\n"
        f"  Teléfono : {telefono}\n"
        f"  Cliente  : {nombre}\n"
        f"  Producto : {producto}\n"
        f"  Link     : {link or 'No proporcionado'}\n"
        f"  Cantidad : {cantidad}\n"
        f"  Email    : {email}\n"
        f"  {'─' * 50}"
    )


def log_error(telefono: str, error: str):
    """Registra un error del sistema."""
    _log_errores.info(
        f"[{_ts()}] [{telefono}] ERROR: {error}"
    )


def log_inicio_sesion(telefono: str, origen: str = "whatsapp"):
    """Registra el inicio de una nueva sesión."""
    _log_conversaciones.info(
        f"\n{'═' * 70}\n"
        f"[{_ts()}] NUEVA SESIÓN — {telefono} (via {origen})\n"
        f"{'═' * 70}"
    )
