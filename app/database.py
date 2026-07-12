from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import os

# Pastikan folder data ada
if not os.path.exists("data"):
    os.makedirs("data")

# Koneksi database PostgreSQL via DATABASE_URL
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://admin_predia:password_aman_yan@localhost:5432/prediabeat_auth"
)

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ── TABEL USER (Login) ────────────────────────────────────────
class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=True)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="user") # 'admin' atau 'user'
    
    # Relasi ke Profil Kesehatan
    profile = relationship("UserProfileDB", back_populates="owner", uselist=False)

# ── TABEL USER PROFILE (Hasil Kuesioner) ───────────────────────
class UserProfileDB(Base):
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Simpan data JSON agar fleksibel menampung field dari models.py
    full_profile_data = Column(Text, nullable=False) 
    analysis_result = Column(Text, nullable=False) # Hasil analisis (JSON string)
    
    owner = relationship("UserDB", back_populates="profile")

# Buat tabel
Base.metadata.create_all(bind=engine)