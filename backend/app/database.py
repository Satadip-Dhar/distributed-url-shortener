from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config import settings

Base = declarative_base()

# Primary (writes)
write_engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    echo=False,
)

# Replica (reads)
read_engine = create_async_engine(
    settings.DATABASE_READ_URL,
    pool_size=20,  # More read connections (read-heavy workload)
    max_overflow=30,
    echo=False,
)

WriteSession = async_sessionmaker(write_engine, class_=AsyncSession, expire_on_commit=False)
ReadSession = async_sessionmaker(read_engine, class_=AsyncSession, expire_on_commit=False)


async def get_write_db():
    async with WriteSession() as session:
        yield session


async def get_read_db():
    async with ReadSession() as session:
        yield session
