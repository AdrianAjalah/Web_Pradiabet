from passlib.context import CryptContext
from app.database import SessionLocal, UserDB
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from datetime import datetime, timedelta
import json

# SECRET KEY (Biasanya ambil dari environment variables)
SECRET_KEY = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

from fastapi import Request # Pastikan kamu sudah menambahkan import ini di bagian atas file auth.py

def get_current_user(request: Request, db = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # 1. Ambil token dari cookie browser
    token_str = request.cookies.get("access_token")
    if not token_str:
        raise credentials_exception
        
    # 2. Hilangkan kata "Bearer " agar tersisa murni token JWT-nya saja
    token = token_str.replace("Bearer ", "")
    
    # 3. Proses decode JWT asli dari kodemu
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = db.query(UserDB).filter(UserDB.username == username).first()
    if user is None:
        raise credentials_exception
    
    return user

# ── INIT ADMIN (Jalankan sekali saat start) ─────────────────────
def init_admin():
    db = SessionLocal()
    admin = db.query(UserDB).filter(UserDB.username == "admin").first()
    if not admin:
        admin = UserDB(
            username="admin", 
            hashed_password=get_password_hash("admin123"), # Password Default
            role="admin"
        )
        db.add(admin)
        db.commit()
        print("✅ Admin default created (user: admin, pass: admin123)")
    db.close()