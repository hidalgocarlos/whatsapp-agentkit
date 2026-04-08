# agent/memory.py — Memoria de conversaciones con SQLite
# Generado por AgentKit

"""
Sistema de memoria del agente. Guarda el historial de conversaciones
por número de teléfono usando SQLite (local) o PostgreSQL (producción).
"""

import os
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, select, Integer, Boolean, update
from dotenv import load_dotenv

load_dotenv()

# Configuración de base de datos
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentkit.db")

# Si es PostgreSQL en producción, ajustar el esquema de URL
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Mensaje(Base):
    """Modelo de mensaje en la base de datos."""
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" o "assistant"
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Cotizacion(Base):
    """Registro de cotizaciones para el sistema de follow-up automático."""
    __tablename__ = "cotizaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    producto: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(String(200))
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    seguimiento_enviado: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmado: Mapped[bool] = mapped_column(Boolean, default=False)


async def inicializar_db():
    """Crea las tablas si no existen."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def guardar_mensaje(telefono: str, role: str, content: str):
    """Guarda un mensaje en el historial de conversación."""
    async with async_session() as session:
        mensaje = Mensaje(
            telefono=telefono,
            role=role,
            content=content,
            timestamp=datetime.now(timezone.utc)
        )
        session.add(mensaje)
        await session.commit()


async def obtener_historial(telefono: str, limite: int = 20) -> list[dict]:
    """
    Recupera los últimos N mensajes de una conversación.

    Args:
        telefono: Número de teléfono del cliente
        limite: Máximo de mensajes a recuperar (default: 20)

    Returns:
        Lista de diccionarios con role y content
    """
    async with async_session() as session:
        query = (
            select(Mensaje)
            .where(Mensaje.telefono == telefono)
            .order_by(Mensaje.timestamp.desc())
            .limit(limite)
        )
        result = await session.execute(query)
        mensajes = result.scalars().all()

        # Invertir para orden cronológico (los más recientes están primero)
        mensajes.reverse()

        return [
            {"role": msg.role, "content": msg.content}
            for msg in mensajes
        ]


async def limpiar_historial(telefono: str):
    """Borra todo el historial de una conversación."""
    async with async_session() as session:
        query = select(Mensaje).where(Mensaje.telefono == telefono)
        result = await session.execute(query)
        mensajes = result.scalars().all()
        for msg in mensajes:
            session.delete(msg)
        await session.commit()


# ── Funciones de follow-up ────────────────────────────────────────────────────

async def registrar_cotizacion(telefono: str, nombre: str, producto: str, email: str):
    """Registra una cotización enviada para hacer seguimiento posterior."""
    async with async_session() as session:
        cot = Cotizacion(
            telefono=telefono,
            nombre=nombre,
            producto=producto,
            email=email,
            timestamp=datetime.now(timezone.utc),
            seguimiento_enviado=False,
            confirmado=False,
        )
        session.add(cot)
        await session.commit()


async def obtener_cotizaciones_para_seguimiento(horas_espera: int = 24) -> list[Cotizacion]:
    """
    Retorna cotizaciones que llevan más de `horas_espera` sin confirmar
    y sin seguimiento enviado aún.
    """
    limite = datetime.now(timezone.utc) - timedelta(hours=horas_espera)
    async with async_session() as session:
        query = (
            select(Cotizacion)
            .where(Cotizacion.seguimiento_enviado == False)
            .where(Cotizacion.confirmado == False)
            .where(Cotizacion.timestamp <= limite)
        )
        result = await session.execute(query)
        return list(result.scalars().all())


async def marcar_seguimiento_enviado(cotizacion_id: int):
    """Marca una cotización como 'seguimiento enviado'."""
    async with async_session() as session:
        await session.execute(
            update(Cotizacion)
            .where(Cotizacion.id == cotizacion_id)
            .values(seguimiento_enviado=True)
        )
        await session.commit()


async def marcar_cotizacion_confirmada(telefono: str):
    """Marca todas las cotizaciones pendientes de un cliente como confirmadas."""
    async with async_session() as session:
        await session.execute(
            update(Cotizacion)
            .where(Cotizacion.telefono == telefono)
            .where(Cotizacion.confirmado == False)
            .values(confirmado=True)
        )
        await session.commit()
