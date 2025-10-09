from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# SQLite file in project root; change path if needed
DATABASE_URL = "sqlite:///./app.db"

engine = create_engine(
    DATABASE_URL,
    future=True,
    echo=False,          # flip to True to debug SQL
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# FastAPI dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
