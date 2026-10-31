from psycopg import OperationalError as PsycopgOperationalError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import QueuePool
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

# Database URL from settings (which loads from .env)
database_url = settings.database_url

if database_url.startswith("sqlite"):
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
else:
    # Check if using Neon (SSL required) or local PostgreSQL
    is_neon = "neon.tech" in database_url

    # SSL and connection arguments for PostgreSQL - aggressive keepalive for Neon
    connect_args = {
        "sslmode": "require" if is_neon else "prefer",
        "channel_binding": "require" if is_neon else "prefer",
        "connect_timeout": 30,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
        "application_name": "agenda_atende",
        "tcp_user_timeout": 30000,
    }

    # Engine configuration with robust connection pooling and SSL handling
    engine = create_engine(
        database_url,
        poolclass=QueuePool,
        pool_pre_ping=True,           # CRITICAL: validates connections before use
        pool_recycle=180,             # Recycle connections every 3 minutes (Neon closes idle at 5min)
        pool_size=10,                 # Base pool size
        max_overflow=20,              # Allow up to 20 additional connections
        pool_timeout=30,              # Timeout for getting connection from pool
        connect_args=connect_args,    # SSL and keepalive settings
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


@retry(
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type((PsycopgOperationalError, SAOperationalError)),
    reraise=True,
)
def get_db_session():
    """Get a database session with automatic retry on connection errors."""
    db = SessionLocal()
    try:
        # Test connection before yielding
        db.execute(text("SELECT 1"))
        yield db
    except (PsycopgOperationalError, SAOperationalError):
        db.close()
        raise
    finally:
        db.close()


# Backward compatibility
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
