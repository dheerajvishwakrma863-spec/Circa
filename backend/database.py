import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()
URL = os.getenv("DATABASE_URL", "sqlite:///./circa.db")
if URL.startswith("postgres://"):               # Heroku/Render legacy scheme
    URL = URL.replace("postgres://", "postgresql://", 1)
kw = {"connect_args": {"check_same_thread": False}} if URL.startswith("sqlite") else {"pool_pre_ping": True}
engine = create_engine(URL, **kw)
SessionLocal = sessionmaker(bind=engine, autoflush=False)

class Base(DeclarativeBase):
    pass

def get_db():
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
