import os

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import sessionmaker


# 1. Charger les variables d'environnement
load_dotenv()


# 2. Récupérer les paramètres PostgreSQL
DB_USER = os.getenv("POSTGRES_APP_USER")
DB_PASSWORD = os.getenv("POSTGRES_APP_PASSWORD")
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
DB_NAME = os.getenv("POSTGRES_DB")


# 3. Vérifier la configuration
required_variables = {
    "POSTGRES_APP_USER": DB_USER,
    "POSTGRES_APP_PASSWORD": DB_PASSWORD,
    "POSTGRES_DB": DB_NAME,
}

missing_variables = [
    name for name, value in required_variables.items() if not value
]

if missing_variables:
    raise RuntimeError(
        f"Missing database configuration: {', '.join(missing_variables)}"
    )


# 4. Construire l'URL de connexion
DATABASE_URL = URL.create(
    drivername="postgresql+psycopg",
    username=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=DB_PORT,
    database=DB_NAME,
)


# 5. Créer le moteur SQLAlchemy
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)


# 6. Préparer le gestionnaire de sessions
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)