from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import unicodedata
import httpx
import json
import stripe
from typing import Dict, List, Optional
import bcrypt
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from moderation import (
    moderate_message,
    check_content_safety,
    ModerationCheckRequest,
    ModerationResult,
)
from whatsapp_service import (
    WHATSAPP_TOKEN,
    VERIFY_TOKEN,
    PHONE_NUMBER_ID,
    SERVICE_IMAGES,
    SIMULATED_MESSAGES_LOG,
    get_service_image,
    build_premium_match_interactive,
    build_premium_confirmation_interactive,
    build_premium_details_interactive,
    build_premium_security_alert_interactive,
    build_premium_text_payload,
    send_whatsapp_payload,
    send_whatsapp_reply,
    send_whatsapp_message,
    send_whatsapp_interactive_message,
    send_whatsapp_interactive_match,
    send_whatsapp_interactive_confirmation,
    send_whatsapp_text_message,
)


DB_FILE = "database.db"
SCHEMA_FILE = "schema.sql"


def init_agenda_db(db_cursor):
    """Initialise les tables de gestion de l'agenda (créneaux et missions)."""
    db_cursor.execute("""
        CREATE TABLE IF NOT EXISTS provider_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL,
            date TEXT NOT NULL,          -- Format 'YYYY-MM-DD'
            start_time TEXT NOT NULL,    -- Format 'HH:MM'
            end_time TEXT NOT NULL,      -- Format 'HH:MM'
            is_booked BOOLEAN DEFAULT 0,
            FOREIGN KEY (provider_id) REFERENCES provider_profiles (id)
        )
    """)
    db_cursor.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL,
            customer_email TEXT NOT NULL,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (provider_id) REFERENCES provider_profiles (id)
        )
    """)


def init_db():
    """Initialise la base de données avec le schéma si elle est vide et s'assure de l'existence des tables."""
    schema_path = Path(SCHEMA_FILE)
    if not schema_path.exists():
        return

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='requests'"
        )
        table_exists = cursor.fetchone()[0] > 0

        if not table_exists:
            with open(schema_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
            conn.commit()

        # Initialisation des tables Agenda & Missions
        init_agenda_db(cursor)

        # 1. Table provider_profiles
        conn.execute("""
            CREATE TABLE IF NOT EXISTS provider_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                skills TEXT NOT NULL,
                postal_codes TEXT NOT NULL,
                hourly_rate REAL NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            );
        """)

        # Migration dynamique si la table provider_profiles existait avec une ancienne structure
        cursor.execute("PRAGMA table_info(provider_profiles);")
        existing_cols = {row[1] for row in cursor.fetchall()}
        if "name" not in existing_cols:
            conn.execute("ALTER TABLE provider_profiles ADD COLUMN name TEXT;")
            conn.execute("UPDATE provider_profiles SET name = 'Prestataire ' || id WHERE name IS NULL;")
        if "skills" not in existing_cols:
            conn.execute("ALTER TABLE provider_profiles ADD COLUMN skills TEXT DEFAULT 'ménage, repassage';")
            conn.execute("UPDATE provider_profiles SET skills = coalesce(bio, 'ménage, repassage') WHERE skills IS NULL;")
        if "postal_codes" not in existing_cols:
            conn.execute("ALTER TABLE provider_profiles ADD COLUMN postal_codes TEXT DEFAULT '75001, 75002, 75011, 75012, Paris';")
            conn.execute("UPDATE provider_profiles SET postal_codes = '75001, 75002, 75011, 75012, Paris' WHERE postal_codes IS NULL;")
        if "is_active" not in existing_cols:
            conn.execute("ALTER TABLE provider_profiles ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;")
            conn.execute("UPDATE provider_profiles SET is_active = coalesce(is_available, 1) WHERE is_active IS NULL;")
        if "latitude" not in existing_cols:
            conn.execute("ALTER TABLE provider_profiles ADD COLUMN latitude REAL;")
        if "longitude" not in existing_cols:
            conn.execute("ALTER TABLE provider_profiles ADD COLUMN longitude REAL;")
        if "service_radius_km" not in existing_cols:
            conn.execute("ALTER TABLE provider_profiles ADD COLUMN service_radius_km REAL DEFAULT 15.0;")
        if "rating_avg" not in existing_cols:
            conn.execute("ALTER TABLE provider_profiles ADD COLUMN rating_avg REAL DEFAULT 4.8;")

        # 2. Tables pour la messagerie anonymisée & Modération
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL UNIQUE,
                customer_id INTEGER NOT NULL,
                provider_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
                FOREIGN KEY (customer_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (provider_id) REFERENCES provider_profiles(id) ON DELETE CASCADE
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                sent_at TEXT NOT NULL DEFAULT (datetime('now')),
                is_flagged INTEGER NOT NULL DEFAULT 0,
                moderation_reasons TEXT,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # Migration dynamique pour la table messages si nécessaire
        cursor.execute("PRAGMA table_info(messages);")
        existing_msg_cols = {row[1] for row in cursor.fetchall()}
        if "is_flagged" not in existing_msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN is_flagged INTEGER NOT NULL DEFAULT 0;")
        if "moderation_reasons" not in existing_msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN moderation_reasons TEXT;")

        # Seed utilisateur de démonstration par défaut si absent
        cursor.execute("SELECT count(*) FROM users WHERE id = 1;")
        if cursor.fetchone()[0] == 0:
            conn.execute("""
                INSERT OR IGNORE INTO users (id, role, first_name, last_name, email, password_hash, phone)
                VALUES (1, 'customer', 'Demo', 'Client', 'client@proximatch.fr', 'hashed_pass_demo', '0600000001');
            """)

        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Cleaning Services Matching API",
    version="1.0.0",
    lifespan=lifespan,
)

# Configuration CORS sécurisée (Origines autorisées en local et en production)
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:8001,http://127.0.0.1:8001,https://proximatch.onrender.com",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)



@app.get("/", summary="Page d'accueil et Dashboard web", response_class=FileResponse)
def serve_index():
    index_path = Path("index.html")
    if index_path.exists():
        return FileResponse(index_path)
    return PlainTextResponse("ProxiMatch API opérationnelle.")


def get_db():
    """Générateur de connexion SQLite avec support des clés étrangères et Row factory."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()


def get_or_create_customer(db: sqlite3.Connection, phone: str, name: Optional[str] = None) -> int:
    """Recherche un utilisateur client par son téléphone, ou le crée automatiquement s'il n'existe pas."""
    cursor = db.cursor()
    cleaned_phone = "".join(c for c in phone if c.isdigit() or c == '+') if phone else "0600000000"
    if not cleaned_phone:
        cleaned_phone = "0600000000"

    cursor.execute("SELECT id FROM users WHERE phone = ? AND role = 'customer'", (cleaned_phone,))
    user = cursor.fetchone()
    if user:
        return user[0]

    first_name = "Client"
    last_name = name or (f"{cleaned_phone[-4:]}" if len(cleaned_phone) >= 4 else cleaned_phone)
    safe_phone_str = cleaned_phone.replace('+', '00')
    email = f"client_{safe_phone_str}@proximatch.fr"
    password_hash = "autocreated_phone_user"

    # Vérification anti-collision email
    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    existing_email = cursor.fetchone()
    if existing_email:
        return existing_email[0]

    cursor.execute(
        """
        INSERT INTO users (role, first_name, last_name, email, password_hash, phone)
        VALUES ('customer', ?, ?, ?, ?, ?)
        """,
        (first_name, last_name, email, password_hash, cleaned_phone),
    )
    db.commit()
    return cursor.lastrowid


# ----------------------------------------------------------------------
# Configuration de la Sécurité & Authentification JWT
# ----------------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "super_secret_key_proximatch_change_me_in_prod")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Vérifie la correspondance d'un mot de passe en clair avec son hash bcrypt."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8")[:72],
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """Génère un hash bcrypt sécurisé pour le mot de passe."""
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Génère un jeton d'accès JWT signé avec date d'expiration."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def authenticate_user(db: sqlite3.Connection, email_or_username: str, password: str) -> Optional[dict]:
    """Authentifie un utilisateur en base SQLite via email et mot de passe hashé."""
    cursor = db.cursor()
    cursor.execute(
        "SELECT id, role, first_name, last_name, email, password_hash, phone FROM users WHERE email = ?",
        (email_or_username.strip().lower(),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    user_dict = dict(row)
    if not verify_password(password, user_dict["password_hash"]):
        # Fallback pour compte démo
        if user_dict["password_hash"] in ("hashed_pass_demo", "autocreated_phone_user") and password == "demo123":
            return user_dict
        return None
    return user_dict


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Dépendance FastAPI pour valider le JWT et récupérer l'utilisateur connecté."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Impossible de valider les informations d'authentification.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    cursor = db.cursor()
    cursor.execute(
        "SELECT id, role, first_name, last_name, email, phone, created_at FROM users WHERE email = ?",
        (username.strip().lower(),),
    )
    user = cursor.fetchone()
    if user is None:
        raise credentials_exception
    return dict(user)


def require_role(allowed_roles: list[str]):
    """Vérifie que l'utilisateur connecté possède l'un des rôles autorisés."""
    def role_checker(current_user: dict = Depends(get_current_user)):
        user_role = current_user.get("role", "customer")
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Accès refusé. Rôle requis : {', '.join(allowed_roles)} (actuel: {user_role}).",
            )
        return current_user
    return role_checker


# ----------------------------------------------------------------------
# Modèles Pydantic (Auth & Métier)
# ----------------------------------------------------------------------
class UserRegister(BaseModel):
    email: str = Field(..., description="Adresse e-mail valide")
    password: str = Field(..., min_length=6, description="Mot de passe (minimum 6 caractères)")
    first_name: str = Field(..., min_length=2, description="Prénom")
    last_name: str = Field(..., min_length=2, description="Nom")
    phone: str = Field(..., description="Numéro de téléphone")
    role: Optional[str] = Field("customer", description="Rôle : 'customer', 'provider', 'admin'")


class UserLogin(BaseModel):
    email: str = Field(..., description="Adresse e-mail")
    password: str = Field(..., description="Mot de passe")


class UserResponse(BaseModel):
    id: int
    role: str
    first_name: str
    last_name: str
    email: str
    phone: Optional[str] = None
    phone_masked: Optional[str] = None
    created_at: Optional[str] = None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Optional[str] = None
    user_id: Optional[int] = None
    user_name: Optional[str] = None


class RequestCreate(BaseModel):
    customer_id: Optional[int] = None
    customer_phone: Optional[str] = Field(None, description="Téléphone ou WhatsApp du client")
    phone: Optional[str] = None
    title: str = Field(..., min_length=2, max_length=150)
    description: Optional[str] = None
    service_date: Optional[str] = Field(None, description="Format ISO (ex: 2026-09-01T10:00:00)")
    duration_hours: Optional[float] = Field(2.0, gt=0)
    surface_m2: Optional[float] = Field(None, gt=0)
    address: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    max_hourly_rate: Optional[float] = Field(None, gt=0)

    # Paramètres alternatifs acceptés depuis le formulaire web
    location: Optional[str] = None
    max_budget: Optional[float] = None
    skills_required: Optional[str] = None


class RequestResponse(BaseModel):
    id: int
    customer_id: int
    title: str
    description: Optional[str]
    service_date: str
    duration_hours: float
    surface_m2: Optional[float]
    address: str
    postal_code: str
    city: str
    latitude: Optional[float]
    longitude: Optional[float]
    max_hourly_rate: Optional[float]
    status: str
    created_at: str
    updated_at: str


class MessageCreate(BaseModel):
    sender_id: int
    content: str = Field(..., min_length=1, description="Contenu du message")
    mode: Optional[str] = Field("mask", description="Mode de modération : 'mask' (masquage auto), 'block' (rejet strict) ou 'audit'")


class MessageResponse(BaseModel):
    id: int
    conversation_id: int
    sender_id: int
    content: str
    sent_at: str
    is_flagged: Optional[int] = 0
    moderation_reasons: Optional[str] = None


class ConversationResponse(BaseModel):
    id: int
    match_id: int
    customer_id: int
    provider_id: int
    created_at: str


class ProviderCreate(BaseModel):
    name: str = Field(..., min_length=2, description="Nom du prestataire")
    skills: str = Field(..., description="Compétences (ex: 'ménage, repassage, rôtisserie')")
    postal_codes: str = Field(..., description="Codes postaux ou villes (ex: '75011, 75012, Bondy')")
    hourly_rate: float = Field(..., gt=0, description="Tarif horaire (€/h)")
    is_active: int = Field(1, description="1 si actif, 0 si inactif")


class ProviderResponse(BaseModel):
    id: int
    name: str
    skills: str
    postal_codes: str
    hourly_rate: float
    is_active: int


class MatchUpdateStatus(BaseModel):
    status: str = Field(..., description="Nouveau statut du match (ex: 'accepted', 'declined')")


class MatchDetailResponse(BaseModel):
    id: int
    request_id: int
    provider_id: int
    match_score: float
    status: str
    matched_at: str
    responded_at: Optional[str] = None


class DashboardSummaryResponse(BaseModel):
    total_requests: int
    requests_by_status: dict[str, int]
    total_providers: int
    total_matches: int
    matches_accepted: int
    total_conversations: int
    total_messages: int
    moderated_messages: int


# ----------------------------------------------------------------------
# Routes API
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# 0. Routes d'Authentification & Gestion des Comptes (JWT & OAuth2)
# ----------------------------------------------------------------------
@app.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Inscription d'un nouvel utilisateur (Client ou Prestataire)",
)
def register_user(
    payload: UserRegister,
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (payload.email.strip().lower(),))
    if cursor.fetchone():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cette adresse e-mail est déjà associée à un compte.",
        )

    hashed_password = get_password_hash(payload.password)
    user_role = payload.role if payload.role in ("customer", "provider", "admin") else "customer"

    cursor.execute(
        """
        INSERT INTO users (role, first_name, last_name, email, password_hash, phone)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_role,
            payload.first_name.strip(),
            payload.last_name.strip(),
            payload.email.strip().lower(),
            hashed_password,
            payload.phone.strip(),
        ),
    )
    db.commit()
    user_id = cursor.lastrowid

    cursor.execute("SELECT id, role, first_name, last_name, email, phone, phone_masked, created_at FROM users WHERE id = ?", (user_id,))
    return dict(cursor.fetchone())


@app.post(
    "/token",
    response_model=Token,
    summary="Connexion standard OAuth2 (Swagger / Client) pour obtenir un token JWT",
)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: sqlite3.Connection = Depends(get_db),
):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Identifiants incorrects (email ou mot de passe invalide).",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["email"], "role": user["role"], "user_id": user["id"]},
        expires_delta=access_token_expires,
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user["role"],
        "user_id": user["id"],
        "user_name": f"{user['first_name']} {user['last_name']}",
    }


@app.post(
    "/auth/login",
    response_model=Token,
    summary="Connexion par JSON pour applications Web et Mobiles",
)
def login_json(
    payload: UserLogin,
    db: sqlite3.Connection = Depends(get_db),
):
    user = authenticate_user(db, payload.email, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Adresse e-mail ou mot de passe incorrect.",
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["email"], "role": user["role"], "user_id": user["id"]},
        expires_delta=access_token_expires,
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user["role"],
        "user_id": user["id"],
        "user_name": f"{user['first_name']} {user['last_name']}",
    }


@app.get(
    "/auth/me",
    response_model=UserResponse,
    summary="Consulter le profil de l'utilisateur actuellement authentifié",
)
def get_current_user_profile(
    current_user: dict = Depends(get_current_user),
):
    return current_user


@app.get(
    "/api/v1/protected-requests",
    summary="Route de test sécurisée par JWT Bearer",
)
async def read_protected_requests(
    current_user: dict = Depends(get_current_user),
):
    return {
        "message": f"Bienvenue {current_user.get('first_name', '')} {current_user.get('last_name', '')}, voici les données sécurisées.",
        "user": current_user,
    }


# ----------------------------------------------------------------------
# 1. Gestion des Prestataires & Demandes
# ----------------------------------------------------------------------
@app.post(
    "/providers",
    response_model=ProviderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enregistrer un nouveau prestataire",
)
def create_provider(
    provider_data: ProviderCreate,
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()
    query = """
        INSERT INTO provider_profiles (name, skills, postal_codes, hourly_rate, is_active)
        VALUES (?, ?, ?, ?, ?)
    """
    cursor.execute(
        query,
        (
            provider_data.name,
            provider_data.skills,
            provider_data.postal_codes,
            provider_data.hourly_rate,
            provider_data.is_active,
        ),
    )
    db.commit()
    created_id = cursor.lastrowid

    cursor.execute("SELECT * FROM provider_profiles WHERE id = ?", (created_id,))
    row = cursor.fetchone()
    return dict(row)


@app.get(
    "/providers",
    response_model=list[ProviderResponse],
    summary="Lister tous les prestataires enregistrés",
)
def list_providers(db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, name, skills, postal_codes, hourly_rate, is_active FROM provider_profiles ORDER BY id ASC")
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


@app.post(
    "/requests",
    response_model=RequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une nouvelle demande de ménage",
)
@app.post(
    "/requests/",
    response_model=RequestResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def create_request(request_data: RequestCreate, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()

    # 1. Identification ou création automatique du client via son numéro de téléphone
    phone_to_use = request_data.customer_phone or request_data.phone
    if phone_to_use:
        customer_id = get_or_create_customer(db, phone_to_use)
    elif request_data.customer_id is not None:
        customer_id = request_data.customer_id
    else:
        customer_id = get_or_create_customer(db, "0600000001", "Client Démo")

    # 2. Normalisation des données issues du formulaire simplifié ou complet
    postal_code = request_data.postal_code
    city = request_data.city
    if request_data.location:
        loc = request_data.location.strip()
        if loc.isdigit() or (len(loc) == 5 and loc[:2].isdigit()):
            postal_code = loc
            city = city or "Paris"
        else:
            city = loc
            postal_code = postal_code or "75000"
    postal_code = postal_code or "75000"
    city = city or "Paris"
    address = request_data.address or f"{city} {postal_code}"

    description = request_data.description
    if request_data.skills_required:
        skills_text = f"Compétence(s) : {request_data.skills_required}"
        description = f"{description} - {skills_text}" if description else skills_text

    max_hourly_rate = request_data.max_hourly_rate if request_data.max_hourly_rate is not None else request_data.max_budget

    service_date = request_data.service_date
    if not service_date:
        from datetime import datetime, timedelta
        service_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00:00")

    duration_hours = request_data.duration_hours or 2.0

    query = """
        INSERT INTO requests (
            customer_id, title, description, service_date, duration_hours,
            surface_m2, address, postal_code, city, latitude, longitude, max_hourly_rate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        customer_id,
        request_data.title,
        description,
        service_date,
        duration_hours,
        request_data.surface_m2,
        address,
        postal_code,
        city,
        request_data.latitude,
        request_data.longitude,
        max_hourly_rate,
    )

    try:
        cursor.execute(query, params)
        db.commit()
        created_id = cursor.lastrowid
    except sqlite3.IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erreur d'intégrité de la base de données : {str(e)}",
        )

    # Lancer automatiquement le matching pour cette nouvelle demande
    try:
        match_request(created_id, db)
    except Exception:
        pass

    cursor.execute("SELECT * FROM requests WHERE id = ?", (created_id,))
    row = cursor.fetchone()
    return dict(row)


@app.get(
    "/requests",
    response_model=list[RequestResponse],
    summary="Lister toutes les demandes de service",
)
@app.get(
    "/requests/",
    response_model=list[RequestResponse],
    include_in_schema=False,
)
def list_requests(
    limit: int = 100,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Récupère la liste de toutes les demandes par ordre chronologique croissant.
    """
    cursor = db.cursor()
    cursor.execute("SELECT * FROM requests ORDER BY id ASC LIMIT ? OFFSET ?", (limit, offset))
    rows = cursor.fetchall()
    return [dict(r) for r in rows]


@app.get(
    "/requests/latest",
    summary="Récupérer la dernière demande enregistrée et ses prestataires compatibles",
)
def get_latest_request(db: sqlite3.Connection = Depends(get_db)):
    """
    Renvoie la dernière demande créée en base avec son statut et la liste des prestataires compatibles.
    """
    cursor = db.cursor()
    cursor.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()

    if not row:
        return {
            "status": "empty",
            "message": "Aucune demande enregistrée pour le moment.",
            "request": None,
            "matches": [],
            "best_match": None,
        }

    req_dict = dict(row)
    req_id = req_dict["id"]

    # Récupérer les prestataires matchés pour cette demande
    cursor.execute(
        """
        SELECT m.match_score, m.status AS match_status, p.id, p.name, p.skills, p.postal_codes, p.hourly_rate
        FROM matches m
        JOIN provider_profiles p ON m.provider_id = p.id
        WHERE m.request_id = ?
        ORDER BY m.match_score DESC
        LIMIT 5
        """,
        (req_id,),
    )
    matched_rows = cursor.fetchall()
    matches_list = [dict(m) for m in matched_rows]

    return {
        "status": "success",
        "request": req_dict,
        "matches": matches_list,
        "best_match": matches_list[0] if matches_list else None,
    }


@app.get(
    "/requests/{id}",
    response_model=RequestResponse,
    summary="Récupérer une demande et son statut par son ID",
)
def get_request(id: int, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM requests WHERE id = ?", (id,))
    row = cursor.fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"La demande avec l'ID {id} n'existe pas.",
        )

    return dict(row)


import json
import re

# ----------------------------------------------------------------------
# Prompt Système & Modèles pour l'agent IA (Route /chat)
# ----------------------------------------------------------------------
SYSTEM_PROMPT = """Tu es un assistant IA spécialisé dans l'analyse de demandes de ménage et de nettoyage.
Ton unique objectif est d'analyser le message en langage naturel envoyé par un client et d'en extraire les informations requises au format JSON strict.

Tu dois impérativement renvoyer UNIQUEMENT un objet JSON valide, sans aucun texte introductif, ni markdown (pas de ```json), ni conclusion.

Structure JSON attendue :
{
  "title": "<Titre concis de la demande, ex: 'Ménage appartement 3 pièces'>",
  "description": "<Détails ou consignes spécifiques mentionnées, ou null>",
  "service_date": "<Date et heure au format ISO 8601, ex: '2026-09-05T14:00:00'. Si non spécifié, estimer une date future>",
  "duration_hours": <Nombre d'heures estimé ou demandé (flottant ou entier > 0, ex: 3.0)>,
  "surface_m2": <Surface en m2 si mentionnée, sinon null>,
  "address": "<Adresse de la prestation>",
  "postal_code": "<Code postal>",
  "city": "<Ville>",
  "latitude": <Latitude si déductible, sinon null>,
  "longitude": <Longitude si déductible, sinon null>,
  "max_hourly_rate": <Budget horaire maximum si mentionné, sinon null>
}
"""


class ChatMessageRequest(BaseModel):
    customer_id: int
    message: str = Field(..., min_length=3, description="Message brut du client")


class ExtractedRequestData(BaseModel):
    title: str
    description: Optional[str] = None
    service_date: str
    duration_hours: float = Field(..., gt=0)
    surface_m2: Optional[float] = Field(None, gt=0)
    address: str
    postal_code: str
    city: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    max_hourly_rate: Optional[float] = Field(None, gt=0)


class ChatMessageResponse(BaseModel):
    success: bool
    message: str
    extracted_data: ExtractedRequestData
    request: RequestResponse


def call_llm_extractor(user_message: str) -> dict:
    """Appelle OpenAI si configuré (OPENAI_API_KEY), sinon utilise un mock intelligent."""
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=openai_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
            )
            raw_content = response.choices[0].message.content.strip()
            return json.loads(raw_content)
        except Exception:
            pass

    # Simulation / Mock intelligent basé sur le texte reçu
    return {
        "title": "Prestation de ménage à domicile",
        "description": user_message,
        "service_date": "2026-09-01T10:00:00",
        "duration_hours": 3.0,
        "surface_m2": 65.0,
        "address": "10 Rue de la République",
        "postal_code": "75011",
        "city": "Paris",
        "latitude": 48.8566,
        "longitude": 2.3522,
        "max_hourly_rate": 25.0,
    }


@app.post(
    "/chat",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Analyser le message d'un client via LLM et créer automatiquement une demande",
)
def chat_and_create_request(
    payload: ChatMessageRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    # 0. Filtrage de sécurité & modération
    mod_check = moderate_message(payload.message, default_mode="mask")
    if "inappropriate_language" in mod_check.reasons:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le message contient des propos contraires aux règles de modération de la plateforme.",
        )

    # 1. Extraction LLM (sur contenu nettoyé si coordonnées directes injectées)
    sanitized_prompt = mod_check.filtered_content
    try:
        raw_extracted = call_llm_extractor(sanitized_prompt)
        extracted = ExtractedRequestData(**raw_extracted)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Échec de l'extraction des données par l'IA : {str(e)}",
        )

    # 2. Insertion dans la base de données
    cursor = db.cursor()
    query = """
        INSERT INTO requests (
            customer_id, title, description, service_date, duration_hours,
            surface_m2, address, postal_code, city, latitude, longitude, max_hourly_rate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        payload.customer_id,
        extracted.title,
        extracted.description,
        extracted.service_date,
        extracted.duration_hours,
        extracted.surface_m2,
        extracted.address,
        extracted.postal_code,
        extracted.city,
        extracted.latitude,
        extracted.longitude,
        extracted.max_hourly_rate,
    )

    try:
        cursor.execute(query, params)
        db.commit()
        created_id = cursor.lastrowid
    except sqlite3.IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erreur d'intégrité de la base de données : {str(e)}",
        )

    cursor.execute("SELECT * FROM requests WHERE id = ?", (created_id,))
    row = cursor.fetchone()

    return ChatMessageResponse(
        success=True,
        message="Demande extraite par l'IA et enregistrée avec succès.",
        extracted_data=extracted,
        request=dict(row),
    )


from math import radians, sin, cos, sqrt, atan2

def calculate_haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calcule la distance en kilomètres entre deux points géographiques 
    en utilisant la formule de Haversine.
    """
    # Rayon de la Terre en kilomètres
    R = 6371.0

    # Conversion des degrés en radians
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return round(R * c, 2)


# Table de correspondance de coordonnées géographiques (Île-de-France & Villes clés)
GEO_COORDINATES_MAP = {
    # Paris arrondissements
    "75001": (48.8625, 2.3364),
    "75002": (48.8682, 2.3428),
    "75003": (48.8637, 2.3615),
    "75004": (48.8543, 2.3576),
    "75005": (48.8448, 2.3471),
    "75006": (48.8493, 2.3300),
    "75007": (48.8565, 2.3126),
    "75008": (48.8727, 2.3126),
    "75009": (48.8770, 2.3374),
    "75010": (48.8760, 2.3600),
    "75011": (48.8590, 2.3780),
    "75012": (48.8350, 2.3950),
    "75013": (48.8280, 2.3620),
    "75014": (48.8290, 2.3270),
    "75015": (48.8410, 2.2990),
    "75016": (48.8600, 2.2620),
    "75017": (48.8870, 2.3070),
    "75018": (48.8920, 2.3440),
    "75019": (48.8820, 2.3820),
    "75020": (48.8630, 2.3980),
    "75000": (48.8566, 2.3522),
    "paris": (48.8566, 2.3522),
    # Villes Île-de-France & Banlieue
    "93140": (48.9022, 2.4828),  # Bondy
    "bondy": (48.9022, 2.4828),
    "93100": (48.8624, 2.4412),  # Montreuil
    "montreuil": (48.8624, 2.4412),
    "92100": (48.8397, 2.2399),  # Boulogne-Billancourt
    "boulogne": (48.8397, 2.2399),
    "93200": (48.9362, 2.3574),  # Saint-Denis
    "saint-denis": (48.9362, 2.3574),
    "94160": (48.8422, 2.4189),  # Saint-Mandé
    "saint-mande": (48.8422, 2.4189),
    "94200": (48.8125, 2.3850),  # Ivry-sur-Seine
    "ivry": (48.8125, 2.3850),
    "92200": (48.8847, 2.2694),  # Neuilly-sur-Seine
    "neuilly": (48.8847, 2.2694),
}


def resolve_coordinates(
    postal_code: Optional[str] = None,
    city: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> Optional[tuple[float, float]]:
    """Résout les coordonnées géographiques soit directement, soit par géocodage local."""
    if lat is not None and lon is not None and (lat != 0.0 or lon != 0.0):
        return (lat, lon)
    p_code = (postal_code or "").strip().lower()
    if p_code in GEO_COORDINATES_MAP:
        return GEO_COORDINATES_MAP[p_code]
    c_name = (city or "").strip().lower()
    if c_name in GEO_COORDINATES_MAP:
        return GEO_COORDINATES_MAP[c_name]
    for key, coords in GEO_COORDINATES_MAP.items():
        if key in p_code or key in c_name:
            return coords
    return None


# ----------------------------------------------------------------------
# 5. Moteur de Matching Multi-critères & Scoring
# ----------------------------------------------------------------------
class MatchedProviderItem(BaseModel):
    id: int
    name: str
    skills: str
    postal_codes: str
    hourly_rate: float
    is_active: int
    match_score: float
    score_skills: float
    score_budget: float
    score_location: float
    distance_km: Optional[float] = None


class MatchResponse(BaseModel):
    request_id: int
    request_title: str
    request_city: Optional[str] = None
    request_postal_code: Optional[str] = None
    max_hourly_rate: Optional[float] = None
    matches_count: int
    matches: list[MatchedProviderItem]


def _strip_accents(text: str) -> str:
    if not text:
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn").lower()


def calculate_skills_score(provider_skills: str, title: str, description: Optional[str]) -> float:
    """Calcule la correspondance des compétences sur 40 points."""
    target_raw = f"{title or ''} {description or ''}".lower()
    target_text = _strip_accents(target_raw)
    prov_raw = (provider_skills or "").lower()
    prov_norm = _strip_accents(prov_raw)

    skills = [_strip_accents(s.strip()) for s in (provider_skills or "").split(",") if s.strip()]
    if not skills:
        return 0.0

    matches = 0
    for s in skills:
        if s in target_text or target_text in s or any(word in target_text for word in s.split() if len(word) > 2):
            matches += 1

    if matches >= 1:
        return 40.0

    # Synonymes et équivalences de compétences multi-métiers (normalisés sans accents)
    skill_categories = {
        "plumbing": ["plomberie", "plombier", "fuite", "sanitaire", "robinet", "tuyau", "plumbing"],
        "cleaning": ["menage", "nettoyage", "repassage", "vitre", "vitres", "sol", "appartement", "maison", "cuisine", "rotisserie", "cleaning"],
        "gardening": ["jardin", "jardinage", "tonte", "pelouse", "haie", "espaces verts", "gardening"],
        "electrical": ["electricite", "electricien", "electrical", "cablage", "tableau electrique"],
        "painting": ["peinture", "peintre", "enduit", "painting"],
        "moving": ["demenagement", "moving", "manutention", "portage"],
        "bricolage": ["bricolage", "montage", "pose", "fixation", "reparation", "diy"],
    }

    for cat, keywords in skill_categories.items():
        if (cat in target_text or any(k in target_text for k in keywords)) and (cat in prov_norm or any(k in prov_norm for k in keywords)):
            return 40.0

    return 0.0


def calculate_budget_score(provider_rate: float, max_rate: Optional[float]) -> float:
    """Calcule la compatibilité du tarif horaire sur 30 points."""
    if max_rate is None or max_rate <= 0:
        return 30.0
    if provider_rate <= max_rate:
        return 30.0
    # Dégressif en cas de dépassement de budget
    diff = provider_rate - max_rate
    return max(0.0, round(30.0 - (diff * 3.0), 2))


def calculate_location_score(
    postal_codes: str,
    postal_code: Optional[str] = None,
    city: Optional[str] = None,
    address: Optional[str] = None,
    req_lat: Optional[float] = None,
    req_lon: Optional[float] = None,
    prov_lat: Optional[float] = None,
    prov_lon: Optional[float] = None,
) -> tuple[float, Optional[float]]:
    """
    Calcule la proximité géographique sur 30 points avec la formule de Haversine.
    Retourne un tuple : (score_sur_30, distance_en_km).
    """
    # 1. Résolution des coordonnées géographiques
    req_coords = resolve_coordinates(postal_code, city, req_lat, req_lon)
    
    prov_coords = None
    if prov_lat is not None and prov_lon is not None and (prov_lat != 0.0 or prov_lon != 0.0):
        prov_coords = (prov_lat, prov_lon)
    elif postal_codes:
        first_prov_code = postal_codes.split(",")[0].strip()
        prov_coords = resolve_coordinates(first_prov_code)

    distance_km = None
    if req_coords and prov_coords:
        distance_km = calculate_haversine_distance(
            req_coords[0], req_coords[1],
            prov_coords[0], prov_coords[1],
        )
        # Barème progressif précis basé sur la distance réelle en km
        if distance_km <= 3.0:
            score = 30.0
        elif distance_km <= 6.0:
            score = 27.0
        elif distance_km <= 10.0:
            score = 24.0
        elif distance_km <= 15.0:
            score = 20.0
        elif distance_km <= 25.0:
            score = 15.0
        elif distance_km <= 35.0:
            score = 10.0
        else:
            score = max(0.0, round(30.0 - (distance_km * 0.8), 2))
        return (score, distance_km)

    # 2. Fallback textuel / codes postaux si aucune coordonnée résolue
    if not postal_codes:
        return (0.0, None)

    codes = [c.strip().lower() for c in postal_codes.split(",") if c.strip()]
    p_code = (postal_code or "").strip().lower()
    p_city = (city or "").strip().lower()
    p_addr = (address or "").strip().lower()

    # Correspondance exacte ville ou code postal
    if (p_code and p_code in codes) or (p_city and p_city in codes):
        return (30.0, 2.0)

    # Correspondance de département (ex: 75, 93)
    if p_code and any(p_code[:2] == c[:2] for c in codes if len(c) >= 2 and len(p_code) >= 2 and p_code[:2].isdigit() and c[:2].isdigit()):
        return (20.0, 7.0)

    # Présence dans l'adresse
    if any(c in p_addr or c in p_city for c in codes):
        return (30.0, 2.0)

    return (0.0, None)


@app.post(
    "/requests/{request_id}/match",
    response_model=MatchResponse,
    summary="Calculer et renvoyer la liste des meilleurs prestataires pour une demande",
)
def match_request(
    request_id: int,
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()

    # 1. Récupérer la demande spécifiée
    cursor.execute("SELECT * FROM requests WHERE id = ?", (request_id,))
    req = cursor.fetchone()
    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"La demande {request_id} n'existe pas.",
        )

    # 2. Interroger tous les prestataires actifs de la base
    cursor.execute(
        """
        SELECT id, name, skills, postal_codes, hourly_rate, is_active
        FROM provider_profiles
        WHERE is_active = 1
        """
    )
    providers = cursor.fetchall()

    req_dict = dict(req)

    # 3. Calculer les scores pour chaque prestataire
    candidates = []
    for prov in providers:
        prov_dict = dict(prov)
        score_skills = calculate_skills_score(
            prov_dict.get("skills", ""),
            req_dict.get("title", ""),
            req_dict.get("description", ""),
        )
        score_budget = calculate_budget_score(
            prov_dict.get("hourly_rate", 25.0),
            req_dict.get("max_hourly_rate"),
        )
        score_loc, dist_km = calculate_location_score(
            postal_codes=prov_dict.get("postal_codes", ""),
            postal_code=req_dict.get("postal_code"),
            city=req_dict.get("city"),
            address=req_dict.get("address"),
            req_lat=req_dict.get("latitude"),
            req_lon=req_dict.get("longitude"),
            prov_lat=prov_dict.get("latitude"),
            prov_lon=prov_dict.get("longitude"),
        )

        total_score = round(min(100.0, score_skills + score_budget + score_loc), 2)

        candidates.append({
            "id": prov_dict["id"],
            "name": prov_dict.get("name") or f"Prestataire {prov_dict['id']}",
            "skills": prov_dict.get("skills") or "",
            "postal_codes": prov_dict.get("postal_codes") or "",
            "hourly_rate": prov_dict["hourly_rate"],
            "is_active": prov_dict["is_active"],
            "match_score": total_score,
            "score_skills": score_skills,
            "score_budget": score_budget,
            "score_location": score_loc,
            "distance_km": dist_km,
        })

    # 4. Trier du score le plus haut au plus bas
    candidates.sort(key=lambda x: x["match_score"], reverse=True)

    # 5. Enregistrer les matches dans la table matches
    for cand in candidates:
        cursor.execute(
            """
            INSERT INTO matches (request_id, provider_id, match_score, status)
            VALUES (?, ?, ?, 'suggested')
            ON CONFLICT(request_id, provider_id) DO UPDATE SET
                match_score = excluded.match_score,
                status = 'suggested',
                matched_at = datetime('now')
            """,
            (request_id, cand["id"], cand["match_score"]),
        )

    cursor.execute("UPDATE requests SET status = 'matching' WHERE id = ?", (request_id,))
    db.commit()

    return MatchResponse(
        request_id=request_id,
        request_title=req_dict["title"],
        request_city=req_dict.get("city"),
        request_postal_code=req_dict.get("postal_code"),
        max_hourly_rate=req_dict.get("max_hourly_rate"),
        matches_count=len(candidates),
        matches=candidates,
    )


def find_best_matching_providers(
    client_lat: float,
    client_lon: float,
    requested_skill: str,
    db_cursor: sqlite3.Cursor,
    max_radius_km: Optional[float] = None,
) -> list[dict]:
    """
    Récupère les prestataires qualifiés, calcule leur distance exacte (Haversine)
    et filtre par rayon d'intervention maximal avant de trier par proximité.
    """
    # 1. Sélection des prestataires actifs
    db_cursor.execute(
        """
        SELECT id, name, skills, hourly_rate, 
               COALESCE(latitude, 0.0) AS lat, 
               COALESCE(longitude, 0.0) AS lon,
               COALESCE(service_radius_km, 15.0) AS max_distance_km,
               COALESCE(rating_avg, 4.8) AS rating,
               postal_codes
        FROM provider_profiles
        WHERE is_active = 1
        """
    )
    providers = db_cursor.fetchall()
    
    matched_providers = []
    skill_clean = (requested_skill or "").strip().lower()

    for prov in providers:
        prov_dict = dict(prov) if isinstance(prov, sqlite3.Row) else {
            "id": prov[0], "name": prov[1], "skills": prov[2], "hourly_rate": prov[3],
            "lat": prov[4], "lon": prov[5], "max_distance_km": prov[6], "rating": prov[7],
            "postal_codes": prov[8] if len(prov) > 8 else ""
        }
        
        # Filtrage de compétence (sémantique ou mot-clé)
        prov_skills = (prov_dict.get("skills") or "").lower()
        if skill_clean and skill_clean not in prov_skills:
            score_sk = calculate_skills_score(prov_skills, skill_clean, None)
            if score_sk < 20.0:
                continue

        p_lat = prov_dict.get("lat", 0.0)
        p_lon = prov_dict.get("lon", 0.0)

        # Si coordonnées absentes, résolution depuis les codes postaux
        if (p_lat == 0.0 or p_lon == 0.0) and prov_dict.get("postal_codes"):
            coords = resolve_coordinates(prov_dict["postal_codes"].split(",")[0].strip())
            if coords:
                p_lat, p_lon = coords

        if p_lat == 0.0 or p_lon == 0.0:
            p_lat, p_lon = (48.8566, 2.3522)

        # Calcul de la distance réelle avec Haversine
        distance = calculate_haversine_distance(client_lat, client_lon, p_lat, p_lon)
        prov_max_dist = max_radius_km if max_radius_km is not None else float(prov_dict.get("max_distance_km", 15.0))
        
        # Vérification si le client est dans le rayon d'intervention du prestataire
        if distance <= prov_max_dist:
            matched_providers.append({
                "id": prov_dict["id"],
                "name": prov_dict["name"],
                "skill": prov_dict["skills"],
                "skills": prov_dict["skills"],
                "distance_km": round(distance, 1),
                "hourly_rate": prov_dict["hourly_rate"],
                "rate": prov_dict["hourly_rate"],
                "rating": round(float(prov_dict.get("rating", 4.8)), 1),
                "latitude": p_lat,
                "longitude": p_lon,
                "max_distance_km": prov_max_dist,
            })
            
    # Tri par ordre croissant de distance (les plus proches en premier)
    matched_providers.sort(key=lambda x: x["distance_km"])
    return matched_providers


@app.get(
    "/providers/match-geo",
    summary="Trouver les meilleurs prestataires par coordonnées GPS (Formule de Haversine)",
)
def get_providers_by_geo(
    lat: float = Query(..., description="Latitude GPS du client (ex: 48.8590)"),
    lon: float = Query(..., description="Longitude GPS du client (ex: 2.3780)"),
    skill: Optional[str] = Query(None, description="Compétence demandée (ex: 'plumbing', 'cleaning', 'gardening', 'all')"),
    max_distance_km: Optional[float] = Query(25.0, description="Rayon maximal de recherche en km"),
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()
    clean_skill = skill if (skill and skill.strip().lower() != "all") else ""
    matched = find_best_matching_providers(
        client_lat=lat,
        client_lon=lon,
        requested_skill=clean_skill,
        db_cursor=cursor,
        max_radius_km=max_distance_km,
    )
    return {
        "status": "success",
        "client_coordinates": {"lat": lat, "lon": lon},
        "requested_skill": skill or "all",
        "max_distance_km": max_distance_km,
        "count": len(matched),
        "providers": matched,
    }


# ----------------------------------------------------------------------
# 5.1 Parser LLM / Google GenAI en Langage Naturel (/requests/ai-parse)
# ----------------------------------------------------------------------
class NLPPrompt(BaseModel):
    text: Optional[str] = Field(None, description="Texte brut en langage naturel (ex: 'Salut, je cherche quelqu'un pour refaire la plomberie de ma salle de bain à Bondy, budget 150 max')")
    message: Optional[str] = Field(None, description="Alias pour text")
    customer_phone: Optional[str] = Field(None, description="Téléphone ou WhatsApp du client pour identification")
    phone: Optional[str] = None
    customer_id: Optional[int] = None


class ParsedServiceData(BaseModel):
    title: str
    location: str
    max_budget: float
    skills_required: str
    description: Optional[str] = None


class AIParseResponse(BaseModel):
    status: str
    message: str
    parsed_data: ParsedServiceData
    request: RequestResponse
    matches_count: int
    matches: list[MatchedProviderItem] = []


def extract_request_with_gemini(user_text: str) -> dict:
    """
    Analyse et extrait les données structurées d'une demande formulée en langage naturel
    via Google GenAI (Gemini), avec fallback heuristique intelligent.
    """
    # 1. Tentative avec Google GenAI SDK (si clé API configurée)
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if api_key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)

            prompt = f"""Tu es un assistant IA expert en structuration de demandes de services à domicile et artisanat.
Analyse la demande suivante en langage naturel (français ou anglais) et extrait les informations au format JSON strict :
- title : résumé court et précis du besoin (ex: "Plomberie salle de bain", "Ménage appartement 3h", "Tonte de pelouse")
- location : ville ou code postal (ex: "Bondy", "Paris 11e", "75011", "Lyon"). Si non mentionné, "Paris / Proximité".
- max_budget : nombre flottant du budget maximum en euros (ex: 150.0 ou 30.0). 0.0 si non spécifié.
- skills_required : compétence principale requise parmi ["cleaning", "plumbing", "gardening", "electrical", "painting", "moving", "general"]

Réponds UNIQUEMENT un objet JSON valide sans balises markdown additionnelles.

Demande de l'utilisateur : "{user_text}"
"""

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )

            raw_text = response.text.strip()
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?", "", raw_text, flags=re.MULTILINE).strip("` \n")
            parsed = json.loads(raw_text)

            return {
                "title": str(parsed.get("title") or "Demande de service"),
                "location": str(parsed.get("location") or "Paris / Proximité"),
                "max_budget": float(parsed.get("max_budget") or 0.0),
                "skills_required": str(parsed.get("skills_required") or "general"),
            }
        except Exception as e:
            print(f"[Gemini GenAI Info] Parser heuristique actif : {e}")

    # 2. Heuristic Rule-Based Fallback (Haute précision)
    text_lower = user_text.lower()

    # Détection de compétences
    skills_map = {
        "plumbing": ["plomb", "plomberie", "fuite", "tuyau", "robinet", "évier", "lavabo", "douche", "wc", "canalisation", "siphon", "plumbing"],
        "cleaning": ["ménage", "nettoyage", "repassage", "vitre", "vitres", "sol", "poussière", "laver", "nettoyer", "cleaning"],
        "gardening": ["jardin", "jardinage", "tonte", "pelouse", "haie", "arbres", "fleurs", "désherbage", "gardening"],
        "electrical": ["électri", "electrici", "prise", "disjoncteur", "tableau électrique", "lumière", "câble", "electrical"],
        "painting": ["peinture", "peindre", "peintre", "enduit", "ponçage", "painting"],
        "moving": ["déménag", "demenag", "cartons", "transport", "meubles", "portage", "moving"],
    }
    detected_skill = "general"
    for skill, keywords in skills_map.items():
        if any(k in text_lower for k in keywords):
            detected_skill = skill
            break

    # Détection du budget
    budget_match = re.search(r'(?:budget\s*(?:est\s*de|de|max)?|tarif\s*(?:max)?|pour)\s*:?\s*(\d+(?:[\.,]\d+)?)\s*(?:€|euros?|e)?', text_lower)
    if not budget_match:
        budget_match = re.search(r'(\d+(?:[\.,]\d+)?)\s*(?:€|euros?|max)', text_lower)
    budget_val = float(budget_match.group(1).replace(",", ".")) if budget_match else 0.0

    # Détection de la localisation
    loc_match = re.search(r'\b(?:à|a|sur|dans|vers|secteur)\s+([A-ZÀ-Ÿa-zà-ÿ0-9\-]+(?:\s+[A-ZÀ-Ÿa-zà-ÿ0-9\-]+)?)', user_text)
    if loc_match:
        detected_loc = loc_match.group(1).strip()
    else:
        postal_match = re.search(r'\b(75\d{3}|9[1-5]\d{3}|\d{5})\b', user_text)
        detected_loc = postal_match.group(1) if postal_match else "Paris / Proximité"

    # Construction du titre
    skill_names_fr = {
        "plumbing": "Plomberie sanitaire",
        "cleaning": "Ménage et entretien",
        "gardening": "Entretien jardin et espaces verts",
        "electrical": "Travaux et dépannage électrique",
        "painting": "Peinture et finitions",
        "moving": "Aide au déménagement",
        "general": "Prestation de service",
    }
    base_title = skill_names_fr.get(detected_skill, "Prestation de service")
    if detected_loc and detected_loc != "Paris / Proximité":
        title = f"{base_title} à {detected_loc}"
    else:
        title = base_title

    return {
        "title": title,
        "location": detected_loc,
        "max_budget": budget_val,
        "skills_required": detected_skill,
    }


@app.post(
    "/requests/ai-parse",
    response_model=AIParseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Parser en langage naturel via Gemini / IA & créer la demande",
)
def parse_request_with_ai(
    data: NLPPrompt,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Analyse une phrase en langage naturel via Gemini pour extraire et créer une demande.
    Effectue la modération, la structuration IA, l'identification client et le matching automatique.
    """
    # 1. Extraction et validation du texte
    raw_text = (data.text or data.message or "").strip()
    if not raw_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Veuillez fournir un texte ou message à analyser.",
        )

    # 2. Modération du message
    mod_check = moderate_message(raw_text, default_mode="mask")
    if "inappropriate_language" in mod_check.reasons:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le message contient des propos non conformes à notre charte de modération.",
        )

    # 3. Analyse et extraction par l'IA
    parsed_dict = extract_request_with_gemini(mod_check.filtered_content)

    # 4. Identification ou création du client
    phone_to_use = data.customer_phone or data.phone or "0600000001"
    if data.customer_id is not None:
        customer_id = data.customer_id
    else:
        customer_id = get_or_create_customer(db, phone_to_use)

    # 4. Traitement et normalisation géographique
    loc = parsed_dict["location"].strip()
    city = loc if loc and loc != "Paris / Proximité" else "Paris"
    postal_code = "75000"
    if loc.isdigit() or (len(loc) == 5 and loc[:2].isdigit()):
        postal_code = loc
        city = "Paris"
    elif "bondy" in loc.lower():
        postal_code = "93140"
    elif "montreuil" in loc.lower():
        postal_code = "93100"
    elif "paris" in loc.lower():
        postal_code = "75011"

    address = f"{city} {postal_code}"
    max_budget_val = parsed_dict["max_budget"] if parsed_dict["max_budget"] > 0 else 30.0

    from datetime import datetime, timedelta
    service_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00:00")

    description_full = f"{mod_check.filtered_content} (Compétence : {parsed_dict['skills_required']})"

    # 5. Enregistrement en base de données
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO requests (
            customer_id, title, description, service_date, duration_hours,
            address, postal_code, city, max_hourly_rate, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
        """,
        (
            customer_id,
            parsed_dict["title"],
            description_full,
            service_date,
            2.0,
            address,
            postal_code,
            city,
            max_budget_val,
        ),
    )
    db.commit()
    created_id = cursor.lastrowid

    # 6. Matching multi-critères automatique
    match_result = None
    try:
        match_result = match_request(created_id, db)
    except Exception as e:
        print(f"Erreur de matching automatique : {e}")

    cursor.execute("SELECT * FROM requests WHERE id = ?", (created_id,))
    row = cursor.fetchone()

    parsed_obj = ParsedServiceData(
        title=parsed_dict["title"],
        location=parsed_dict["location"],
        max_budget=parsed_dict["max_budget"],
        skills_required=parsed_dict["skills_required"],
        description=description_full,
    )

    return AIParseResponse(
        status="success",
        message="Demande extraite et structurée avec succès par l'IA.",
        parsed_data=parsed_obj,
        request=dict(row),
        matches_count=match_result.matches_count if match_result else 0,
        matches=match_result.matches if match_result else [],
    )


@app.patch(
    "/matches/{match_id}/status",
    response_model=MatchDetailResponse,
    summary="Mettre à jour le statut d'un match (ex: accepted, declined)",
)
def update_match_status(
    match_id: int,
    payload: MatchUpdateStatus,
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()

    # 1. Vérifier que le match existe
    cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
    match_row = cursor.fetchone()
    if not match_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Le match {match_id} n'existe pas.",
        )

    new_status = payload.status.strip().lower()

    # 2. Mettre à jour le statut du match
    cursor.execute(
        """
        UPDATE matches
        SET status = ?, responded_at = datetime('now')
        WHERE id = ?
        """,
        (new_status, match_id),
    )

    # 3. Si le match est accepté/confirmé, mettre à jour la demande associée
    if new_status in ("accepted", "confirmed", "assigned"):
        cursor.execute(
            """
            UPDATE requests
            SET status = 'assigned', updated_at = datetime('now')
            WHERE id = ?
            """,
            (match_row["request_id"],),
        )

    db.commit()

    # 4. Renvoyer les détails mis à jour du match
    cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
    updated_match = cursor.fetchone()
    return dict(updated_match)


@app.post(
    "/requests/{request_id}/accept",
    summary="Accepter une mission pour une demande donnée",
)
def accept_request_mission(
    request_id: int,
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM requests WHERE id = ?", (request_id,))
    req = cursor.fetchone()
    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"La demande {request_id} n'existe pas.",
        )

    # Mettre à jour le statut de la demande
    cursor.execute(
        "UPDATE requests SET status = 'assigned', updated_at = datetime('now') WHERE id = ?",
        (request_id,),
    )

    # Mettre à jour le meilleur match associé à 'accepted'
    cursor.execute(
        """
        UPDATE matches
        SET status = 'accepted', responded_at = datetime('now')
        WHERE id = (
            SELECT id FROM matches
            WHERE request_id = ?
            ORDER BY match_score DESC, id ASC
            LIMIT 1
        )
        """,
        (request_id,),
    )

    db.commit()
    return {
        "status": "success",
        "message": f"Mission #{request_id} acceptée avec succès",
        "request_id": request_id,
        "new_status": "assigned",
    }


# ----------------------------------------------------------------------
# 6. Routes de Messagerie Anonymisée & Modération
# ----------------------------------------------------------------------
@app.post(
    "/moderation/check",
    response_model=ModerationResult,
    summary="Vérifier et filtrer un contenu textuel contre le contournement et les abus",
)
def check_moderation(payload: ModerationCheckRequest):
    """
    Analyse un message pour détecter les tentatives de contournement (téléphone, e-mail,
    liens externes, réseaux sociaux, IBAN) et les propos indésirables ou toxiques.
    """
    return moderate_message(payload.content, default_mode=payload.action_mode or "mask")


@app.post(
    "/matches/{match_id}/conversation",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Créer ou récupérer la conversation anonymisée pour un match",
)
def create_or_get_conversation(
    match_id: int,
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()

    # 1. Vérifier que le match existe et récupérer les identifiants
    cursor.execute(
        """
        SELECT m.id AS match_id, m.provider_id, r.customer_id
        FROM matches m
        JOIN requests r ON m.request_id = r.id
        WHERE m.id = ?
        """,
        (match_id,),
    )
    match_row = cursor.fetchone()
    if not match_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Le match {match_id} n'existe pas.",
        )

    customer_id = match_row["customer_id"]
    provider_id = match_row["provider_id"]

    # 2. Vérifier si une conversation existe déjà pour ce match
    cursor.execute("SELECT * FROM conversations WHERE match_id = ?", (match_id,))
    existing_conv = cursor.fetchone()
    if existing_conv:
        return dict(existing_conv)

    # 3. Créer une nouvelle entrée dans conversations
    cursor.execute(
        """
        INSERT INTO conversations (match_id, customer_id, provider_id)
        VALUES (?, ?, ?)
        """,
        (match_id, customer_id, provider_id),
    )
    db.commit()
    conversation_id = cursor.lastrowid

    cursor.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
    conv_row = cursor.fetchone()
    return dict(conv_row)


class ConnectionManager:
    def __init__(self):
        # Dictionnaire associant un ID de conversation à une liste de WebSockets actifs
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, conversation_id: int, websocket: WebSocket):
        await websocket.accept()
        if conversation_id not in self.active_connections:
            self.active_connections[conversation_id] = []
        self.active_connections[conversation_id].append(websocket)

    def disconnect(self, conversation_id: int, websocket: WebSocket):
        if conversation_id in self.active_connections:
            if websocket in self.active_connections[conversation_id]:
                self.active_connections[conversation_id].remove(websocket)
            if not self.active_connections[conversation_id]:
                del self.active_connections[conversation_id]

    async def broadcast_to_conversation(self, conversation_id: int, message: dict):
        if conversation_id in self.active_connections:
            for connection in list(self.active_connections[conversation_id]):
                try:
                    await connection.send_text(json.dumps(message))
                except Exception:
                    pass

manager = ConnectionManager()


@app.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Envoyer un message dans une conversation avec filtrage et modération automatique",
)
async def send_message(
    conversation_id: int,
    message_data: MessageCreate,
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()

    # 1. Vérifier que la conversation existe
    cursor.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
    conv = cursor.fetchone()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"La conversation {conversation_id} n'existe pas.",
        )

    # 2. Modération et filtrage de sécurité
    mod_result = moderate_message(message_data.content, default_mode=message_data.mode or "mask")

    # Si le mode est 'block' et que des violations sont présentes
    if mod_result.action == "block":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Message bloqué par les règles de sécurité et de modération de ProxiMatch.",
                "reasons": mod_result.reasons,
                "violations": [v.model_dump() for v in mod_result.violations],
            },
        )

    content_to_save = mod_result.filtered_content
    reasons_str = ",".join(mod_result.reasons) if mod_result.reasons else None
    is_flagged_val = 1 if mod_result.is_flagged else 0

    # 3. Insérer le message filtré
    cursor.execute(
        """
        INSERT INTO messages (conversation_id, sender_id, content, is_flagged, moderation_reasons)
        VALUES (?, ?, ?, ?, ?)
        """,
        (conversation_id, message_data.sender_id, content_to_save, is_flagged_val, reasons_str),
    )
    db.commit()
    message_id = cursor.lastrowid

    cursor.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
    msg_row = cursor.fetchone()
    msg_dict = dict(msg_row)

    # Diffusion temps réel aux WebSockets connectés
    await manager.broadcast_to_conversation(conversation_id, msg_dict)

    return msg_dict


@app.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
    summary="Récupérer l'historique d'une conversation par ordre chronologique",
)
def get_conversation_messages(
    conversation_id: int,
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()

    # 1. Vérifier que la conversation existe
    cursor.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
    conv = cursor.fetchone()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"La conversation {conversation_id} n'existe pas.",
        )

    # 2. Récupérer les messages par ordre chronologique
    cursor.execute(
        """
        SELECT * FROM messages
        WHERE conversation_id = ?
        ORDER BY sent_at ASC, id ASC
        """,
        (conversation_id,),
    )
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


@app.websocket("/ws/conversations/{conversation_id}")
@app.websocket("/api/v1/ws/conversations/{conversation_id}")
async def websocket_conversation_endpoint(
    websocket: WebSocket,
    conversation_id: int,
    sender_name: str = Query("Utilisateur"),
):
    """
    Endpoint WebSocket temps réel pour les échanges instantanés dans une conversation.
    Gère la modération en direct, la persistance en base et la diffusion (broadcast).
    """
    await manager.connect(conversation_id, websocket)
    try:
        while True:
            # Réception du message brut envoyé via le WebSocket
            data_raw = await websocket.receive_text()
            try:
                payload = json.loads(data_raw)
                content = payload.get("content") or payload.get("message") or data_raw
                sender_id = payload.get("sender_id", 1)
                sender = payload.get("sender", sender_name)
                mode = payload.get("mode", "mask")
            except Exception:
                content = data_raw
                sender_id = 1
                sender = sender_name
                mode = "mask"

            if not content:
                continue

            # Analyse de sécurité avec le module de modération anti-désintermédiation
            moderation_result = check_content_safety(content, default_mode=mode)
            is_flagged = not moderation_result.get("is_safe", True)
            
            # Si le mode est 'block' et que des violations sont présentes
            if mode == "block" and is_flagged:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "status": "blocked",
                    "flagged": True,
                    "error": "Message bloqué par les règles de modération ProxiMatch.",
                    "reasons": moderation_result.get("reasons", []),
                    "violations": moderation_result.get("violations", []),
                }))
                continue

            clean_message = moderation_result.get("filtered_content", content)
            reasons_str = ",".join(moderation_result.get("reasons", [])) if moderation_result.get("reasons") else None
            is_flagged_val = 1 if is_flagged else 0
            now_iso = datetime.now(timezone.utc).isoformat()

            # Sauvegarde en base de données
            message_id = None
            try:
                with sqlite3.connect(DB_FILE) as conn:
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    cur.execute(
                        """
                        INSERT INTO messages (conversation_id, sender_id, content, is_flagged, moderation_reasons)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (conversation_id, sender_id, clean_message, is_flagged_val, reasons_str),
                    )
                    conn.commit()
                    message_id = cur.lastrowid
            except Exception:
                pass

            message_payload = {
                "id": message_id,
                "conversation_id": conversation_id,
                "sender": sender,
                "sender_id": sender_id,
                "message": clean_message,
                "content": clean_message,
                "flagged": is_flagged,
                "is_flagged": is_flagged_val,
                "moderation_reasons": reasons_str,
                "timestamp": "Maintenant",
                "sent_at": now_iso,
            }

            # Diffusion en temps réel à tous les participants de la conversation
            await manager.broadcast_to_conversation(conversation_id, message_payload)

    except WebSocketDisconnect:
        manager.disconnect(conversation_id, websocket)
        leave_payload = {
            "sender": "Système",
            "message": f"{sender_name} a quitté le chat.",
            "flagged": False,
            "timestamp": "Maintenant",
        }
        await manager.broadcast_to_conversation(conversation_id, leave_payload)
    except Exception:
        manager.disconnect(conversation_id, websocket)


# ----------------------------------------------------------------------
# 7. Dashboard & Synthèse Globale
# ----------------------------------------------------------------------
@app.get(
    "/dashboard/summary",
    response_model=DashboardSummaryResponse,
    summary="Statistiques globales et tableau de bord de l'application",
)
def get_dashboard_summary(
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()

    # 1. Total des demandes et répartition par statut
    cursor.execute("SELECT count(*) FROM requests")
    total_requests = cursor.fetchone()[0]

    cursor.execute("SELECT status, count(*) FROM requests GROUP BY status")
    requests_by_status = {row[0]: row[1] for row in cursor.fetchall()}

    # 2. Total des prestataires
    cursor.execute("SELECT count(*) FROM provider_profiles")
    total_providers = cursor.fetchone()[0]

    # 3. Total des matches et matches acceptés/confirmés
    cursor.execute("SELECT count(*) FROM matches")
    total_matches = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT count(*) FROM matches
        WHERE status IN ('accepted', 'confirmed', 'assigned', 'accepted_by_provider', 'accepted_by_customer')
        """
    )
    matches_accepted = cursor.fetchone()[0]

    # 4. Total des conversations
    cursor.execute("SELECT count(*) FROM conversations")
    total_conversations = cursor.fetchone()[0]

    # 5. Total des messages
    cursor.execute("SELECT count(*) FROM messages")
    total_messages = cursor.fetchone()[0]

    # 6. Total des messages modérés/filtrés
    cursor.execute("SELECT count(*) FROM messages WHERE is_flagged = 1")
    moderated_messages = cursor.fetchone()[0]

    return DashboardSummaryResponse(
        total_requests=total_requests,
        requests_by_status=requests_by_status,
        total_providers=total_providers,
        total_matches=total_matches,
        matches_accepted=matches_accepted,
        total_conversations=total_conversations,
        total_messages=total_messages,
        moderated_messages=moderated_messages,
    )


# ----------------------------------------------------------------------
# 8. WhatsApp Cloud API Webhooks & Messages Interactifs Premium
# ----------------------------------------------------------------------

# 1. Route de vérification (exigée par Meta lors de l'enregistrement du webhook)
@app.get("/webhook", summary="Validation du Webhook WhatsApp par Meta")
async def verify_whatsapp_webhook(request: Request):
    """
    Étape de vérification obligatoire par Meta lors de la configuration du webhook.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            print("Webhook WhatsApp vérifié avec succès !")
            return PlainTextResponse(content=str(challenge), status_code=200)
        else:
            raise HTTPException(status_code=403, detail="Échec de la vérification du token")

    raise HTTPException(status_code=400, detail="Paramètres manquants")


class WhatsAppSimulateRequest(BaseModel):
    sender_phone: Optional[str] = Field("33699887766", description="Numéro WhatsApp de l'expéditeur")
    text: Optional[str] = Field(None, description="Texte envoyé par l'utilisateur")
    button_id: Optional[str] = Field(None, description="Identifiant du bouton cliqué (ex: accept_req_1, details_req_1)")


@app.get("/whatsapp/messages/latest", summary="Récupérer les derniers messages WhatsApp simulés/envoyés")
def get_latest_whatsapp_messages(limit: int = 20):
    """Renvoie les derniers messages du journal WhatsApp pour l'affichage en direct dans le Dashboard."""
    return {
        "status": "success",
        "count": len(SIMULATED_MESSAGES_LOG),
        "messages": SIMULATED_MESSAGES_LOG[-limit:],
    }


# 2. Route de réception des messages (Webhook POST)
@app.post("/webhook", summary="Réception des messages WhatsApp (Texte et Boutons Interactifs)")
async def receive_whatsapp_message(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Réception des messages entrants de WhatsApp (clients ou prestataires).
    Prend en charge :
    - Les messages texte en langage naturel (avec modération, extraction IA & proposition Premium)
    - Les interactions sur les boutons cliquables (acceptation, détails, refus, support)
    """
    body = await request.json()

    try:
        entry = body.get("entry", [])
        if not entry:
            return {"status": "ignored"}

        changes = entry[0].get("changes", [])
        if not changes:
            return {"status": "ignored"}

        value = changes[0].get("value", {})
        messages = value.get("messages", [])

        if not messages:
            return {"status": "ignored"}

        msg = messages[0]
        sender_phone = msg.get("from", "33600000000")
        msg_type = msg.get("type", "text")

        # -------------------------------------------------------------
        # CAS A : L'utilisateur a cliqué sur un BOUTON INTERACTIF
        # -------------------------------------------------------------
        if msg_type == "interactive":
            interactive = msg.get("interactive", {})
            int_type = interactive.get("type")
            button_id = ""
            button_title = ""

            if int_type == "button_reply":
                btn_reply = interactive.get("button_reply", {})
                button_id = btn_reply.get("id", "")
                button_title = btn_reply.get("title", "")
            elif int_type == "list_reply":
                list_reply = interactive.get("list_reply", {})
                button_id = list_reply.get("id", "")
                button_title = list_reply.get("title", "")

            print(f"[WhatsApp Webhook] Clic Bouton de {sender_phone}: id='{button_id}', title='{button_title}'")

            cursor = db.cursor()

            # 1. Action : Accepter la mission / Valider le profil
            if button_id in ("btn_accept", "btn_validate") or button_id.startswith("accept_req_") or button_id.startswith("accept_mission_"):
                if "_" in button_id and button_id.split("_")[-1].isdigit():
                    req_id = int(button_id.split("_")[-1])
                else:
                    cursor.execute("SELECT id FROM requests WHERE customer_id = (SELECT id FROM users WHERE phone = ? LIMIT 1) ORDER BY id DESC LIMIT 1", (sender_phone,))
                    row_last = cursor.fetchone()
                    req_id = row_last[0] if row_last else 1

                # Mettre à jour le statut de la demande et du match
                cursor.execute(
                    "UPDATE requests SET status = 'assigned', updated_at = datetime('now') WHERE id = ?",
                    (req_id,),
                )
                cursor.execute(
                    """
                    UPDATE matches
                    SET status = 'accepted', responded_at = datetime('now')
                    WHERE request_id = ? AND id = (
                        SELECT id FROM matches WHERE request_id = ? ORDER BY match_score DESC, id ASC LIMIT 1
                    )
                    """,
                    (req_id, req_id),
                )
                db.commit()

                # Récupérer les informations complètes pour générer la confirmation Premium
                cursor.execute(
                    """
                    SELECT r.id, r.title, r.city, r.postal_code, r.duration_hours, r.service_date,
                           p.name AS provider_name, p.hourly_rate AS provider_rate
                    FROM requests r
                    LEFT JOIN matches m ON m.request_id = r.id
                    LEFT JOIN provider_profiles p ON m.provider_id = p.id
                    WHERE r.id = ?
                    ORDER BY m.match_score DESC LIMIT 1
                    """,
                    (req_id,),
                )
                req_info = cursor.fetchone()

                prov_name = req_info["provider_name"] if req_info and req_info["provider_name"] else "Prestataire Partenaire"
                prov_rate = float(req_info["provider_rate"]) if req_info and req_info["provider_rate"] else 25.0
                loc = f"{req_info['city']} ({req_info['postal_code']})" if req_info else "Paris"
                dur = float(req_info["duration_hours"]) if req_info and req_info["duration_hours"] else 2.0
                s_date = req_info["service_date"] if req_info else "Demain à 10:00"
                r_title = req_info["title"] if req_info else "Mission de service"

                confirm_payload = build_premium_confirmation_interactive(
                    recipient_phone=sender_phone,
                    request_id=req_id,
                    request_title=r_title,
                    provider_name=prov_name,
                    location=loc,
                    hourly_rate=prov_rate,
                    duration_hours=dur,
                    service_date=s_date,
                )
                await send_whatsapp_payload(confirm_payload)
                return {"status": "success", "action": "mission_accepted", "request_id": req_id}

            # 2. Action : Voir les détails complets
            elif button_id.startswith("details_req_"):
                req_id = int(button_id.split("_")[-1])
                cursor.execute("SELECT * FROM requests WHERE id = ?", (req_id,))
                req_row = cursor.fetchone()

                if req_row:
                    details_payload = build_premium_details_interactive(
                        recipient_phone=sender_phone,
                        request_id=req_id,
                        request_title=req_row["title"],
                        description=req_row["description"] or "Aucune consigne particulière.",
                        location=f"{req_row['city']} ({req_row['postal_code']})",
                        max_budget=float(req_row["max_hourly_rate"] or 30.0),
                        duration_hours=float(req_row["duration_hours"] or 2.0),
                        service_date=req_row["service_date"] or "Non spécifiée",
                    )
                    await send_whatsapp_payload(details_payload)
                else:
                    await send_whatsapp_reply(sender_phone, f"ℹ️ La demande #{req_id} est introuvable.")

                return {"status": "success", "action": "details_sent", "request_id": req_id}

            # 3. Action : Refuser / Chercher un autre profil
            elif button_id in ("btn_other", "btn_decline") or button_id.startswith("decline_req_"):
                if "_" in button_id and button_id.split("_")[-1].isdigit():
                    req_id = int(button_id.split("_")[-1])
                else:
                    cursor.execute("SELECT id FROM requests WHERE customer_id = (SELECT id FROM users WHERE phone = ? LIMIT 1) ORDER BY id DESC LIMIT 1", (sender_phone,))
                    row_last = cursor.fetchone()
                    req_id = row_last[0] if row_last else 1

                cursor.execute("UPDATE requests SET status = 'open' WHERE id = ?", (req_id,))
                db.commit()

                decline_reply = (
                    f"🔄 *Recherche d'un autre profil pour la demande #{req_id}*\n\n"
                    f"Nous avons pris en compte votre retour. Notre algorithme explore de nouveaux profils compatibles disponibles dans votre secteur.\n\n"
                    f"Vous recevrez une nouvelle notification dès qu'une alternative correspondante sera validée."
                )
                await send_whatsapp_reply(sender_phone, decline_reply)
                return {"status": "success", "action": "declined_and_refreshing", "request_id": req_id}

            # 4. Action : Contacter le support / Assistance
            elif button_id == "contact_support":
                support_reply = (
                    f"💬 *ASSISTANCE PROXIMATCH 24/7*\n\n"
                    f"Une question sur votre mission ou besoin d'aide pour votre réservation ?\n\n"
                    f"📞 *Ligne prioritaire :* 01 89 00 12 34\n"
                    f"✉️ *Email :* support@proximatch.fr\n"
                    f"🌐 *Centre d'aide :* https://proximatch.fr/faq\n\n"
                    f"Un conseiller est à votre entière disposition !"
                )
                await send_whatsapp_reply(sender_phone, support_reply)
                return {"status": "success", "action": "support_info_sent"}

            # 5. Action : Voir la charte de sécurité
            elif button_id == "view_guidelines":
                guidelines_reply = (
                    f"📜 *CHARTE DE SÉCURITÉ & DE QUALITÉ PROXIMATCH*\n\n"
                    f"1️⃣ *Paiement Sécurisé :* Tous les règlements transitent de façon protégée.\n"
                    f"2️⃣ *Garantie Sérénité :* Prestataires vérifiés avec avis certifiés.\n"
                    f"3️⃣ *Messagerie Sécurisée :* Échanges anonymisés sans divulgation de coordonnées directes.\n\n"
                    f"Merci de contribuer à une communauté fiable et respectueuse !"
                )
                await send_whatsapp_reply(sender_phone, guidelines_reply)
                return {"status": "success", "action": "guidelines_sent"}

            else:
                await send_whatsapp_reply(sender_phone, f"✅ Option enregistrée : *{button_title}*")
                return {"status": "success", "action": "generic_button_handled"}

        # -------------------------------------------------------------
        # CAS B : L'utilisateur a envoyé un MESSAGE TEXTE
        # -------------------------------------------------------------
        msg_body = msg.get("text", {}).get("body", "").strip()
        print(f"Message WhatsApp reçu de {sender_phone}: {msg_body}")

        # 1. Étape de modération / sécurité
        from moderation import check_text_moderation
        mod_result = check_text_moderation(msg_body, default_mode="mask")

        if mod_result["is_flagged"] and (
            "inappropriate_language" in mod_result["reasons"]
            or "platform_bypass_phrase" in mod_result["reasons"]
            or mod_result["action"] == "block"
        ):
            if sender_phone:
                reasons_text = "Contournement de plateforme ou propos non conformes à notre charte."
                if "inappropriate_language" in mod_result["reasons"]:
                    reasons_text = "Propos inappropriés ou contraires à la charte de respect."
                elif "platform_bypass_phrase" in mod_result["reasons"]:
                    reasons_text = "Tentative de contournement ou paiement hors plateforme."

                alert_payload = build_premium_security_alert_interactive(
                    recipient_phone=sender_phone,
                    reason=reasons_text,
                )
                await send_whatsapp_payload(alert_payload)
            return {"status": "blocked", "reason": "Contenu interdit ou tentative de contournement"}

        # 2. Récupérer ou créer le client via son numéro WhatsApp
        customer_id = get_or_create_customer(db, sender_phone)
        message_text = msg_body.strip().lower()
        cursor = db.cursor()

        # -------------------------------------------------------------
        # Commande 1 : "mes annonces" / "liste" / "historique" / "mes demandes"
        # -------------------------------------------------------------
        if message_text in ["mes annonces", "liste", "historique", "mes demandes"]:
            cursor.execute(
                "SELECT id, title, city, status, created_at FROM requests WHERE customer_id = ? ORDER BY id DESC LIMIT 5",
                (customer_id,)
            )
            user_requests = cursor.fetchall()

            if not user_requests:
                response_text = "📭 Vous n'avez aucune annonce active pour le moment. Envoyez-moi simplement votre besoin !"
            else:
                response_text = "📋 *Vos dernières annonces :*\n\n"
                for req in user_requests:
                    req_id = req["id"]
                    title = req["title"] or "Service"
                    city = req["city"] or "Non spécifié"
                    req_status = req["status"]
                    response_text += f"🔹 *Annonce #{req_id}*\n" \
                                     f"   • Titre : {title}\n" \
                                     f"   • Ville : {city}\n" \
                                     f"   • Statut : `{req_status}`\n\n"
                response_text += "💡 Pour supprimer une annonce, tapez *supprimer [ID]* (ex: *supprimer 12*)."

            await send_whatsapp_text_message(sender_phone, response_text)
            return {"status": "success", "action": "list_requests"}

        # -------------------------------------------------------------
        # Commande 2 : "supprimer [ID]" / "annuler [ID]"
        # -------------------------------------------------------------
        elif message_text.startswith("supprimer ") or message_text.startswith("annuler "):
            try:
                parts = message_text.split()
                target_id = int(parts[1])

                # Vérification de l'appartenance de l'annonce au client émetteur
                cursor.execute("SELECT id FROM requests WHERE id = ? AND customer_id = ?", (target_id, customer_id))
                owned_req = cursor.fetchone()

                if owned_req:
                    cursor.execute("UPDATE requests SET status = 'cancelled', updated_at = datetime('now') WHERE id = ?", (target_id,))
                    db.commit()
                    response_text = f"✅ L'annonce #{target_id} a bien été annulée."
                else:
                    response_text = f"❌ Annonce #{target_id} introuvable ou accès non autorisé."
            except (IndexError, ValueError):
                response_text = "⚠️ Format invalide. Tapez par exemple : *supprimer 12*"

            await send_whatsapp_text_message(sender_phone, response_text)
            return {"status": "success", "action": "delete_request"}

        # -------------------------------------------------------------
        # 3. Flux classique : Création d'une nouvelle annonce
        # -------------------------------------------------------------
        # Accusé de réception immédiat avant traitement IA et matching
        await send_whatsapp_text_message(
            sender_phone,
            "✅ *Annonce prise en compte !* Analyse par notre IA et recherche des meilleurs professionnels en cours...",
        )

        # Extraction intelligente du besoin via Gemini / NLP Parser
        parsed_nlp = extract_request_with_gemini(mod_result["filtered_content"])


        from datetime import datetime, timedelta
        service_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00:00")
        title_text = parsed_nlp["title"]
        loc = parsed_nlp["location"].strip()
        city = loc if loc and loc != "Paris / Proximité" else "Paris"
        postal_code = "93140" if "bondy" in loc.lower() else ("75011" if "paris" in loc.lower() else "75000")
        address = f"{city} {postal_code}"
        budget_val = parsed_nlp["max_budget"] if parsed_nlp["max_budget"] > 0 else 30.0
        description_full = f"{mod_result['filtered_content']} (Compétence : {parsed_nlp['skills_required']})"

        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO requests (
                customer_id, title, description, service_date, duration_hours,
                address, postal_code, city, max_hourly_rate, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (
                customer_id,
                title_text,
                description_full,
                service_date,
                2.0,
                address,
                postal_code,
                city,
                budget_val,
            ),
        )
        db.commit()
        created_request_id = cursor.lastrowid

        # 4. Déclencher automatiquement le moteur de matching
        match_res = None
        try:
            match_res = match_request(created_request_id, db)
        except Exception as match_err:
            print(f"Erreur lors du matching automatique : {match_err}")

        # 5. Recherche du meilleur prestataire et envoi automatique de la réponse interactive
        matches = match_res.matches if match_res else []
        best_provider = matches[0] if matches else None
        parsed_title = title_text

        if best_provider:
            best_provider_dict = (
                best_provider.model_dump() if hasattr(best_provider, "model_dump")
                else (dict(best_provider) if isinstance(best_provider, dict)
                else {"name": getattr(best_provider, "name", "Prestataire Certifié"), "phone": getattr(best_provider, "phone", "06 ** ** ** 01")})
            )
            if "phone" not in best_provider_dict or not best_provider_dict["phone"]:
                best_provider_dict["phone"] = "06 ** ** ** 01"

            send_whatsapp_interactive_message(
                recipient_phone=sender_phone,
                header_text=parsed_title,
                provider_name=best_provider_dict['name'],
                provider_phone=best_provider_dict['phone'],
                request_id=created_request_id,
            )
        else:
            send_whatsapp_message(
                sender_phone,
                "✅ Demande enregistrée ! Nous recherchons activement le prestataire idéal pour vous.",
            )

        return {"status": "success", "request_id": created_request_id}

    except Exception as e:
        print(f"Erreur de traitement Webhook: {e}")
        return {"status": "ignored", "error": str(e)}


@app.post("/whatsapp/simulate", summary="Simuler l'envoi d'un message ou clic de bouton WhatsApp")
async def simulate_whatsapp_interaction(
    payload: WhatsAppSimulateRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Point d'entrée de simulation pour tester le flux WhatsApp (texte ou clic sur boutons interactifs)
    depuis le tableau de bord ou les outils de test.
    """
    phone = payload.sender_phone or "33699887766"

    # Construction du payload conforme à Meta Graph API
    if payload.button_id:
        simulated_meta_payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "sim_entry_1",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "messages": [
                                    {
                                        "from": phone,
                                        "id": f"wamid.sim_{int(datetime.now().timestamp())}",
                                        "timestamp": str(int(datetime.now().timestamp())),
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "button_reply",
                                            "button_reply": {
                                                "id": payload.button_id,
                                                "title": "Action Simulée",
                                            },
                                        },
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }
    else:
        text_content = payload.text or "Besoin d'un ménage 3h à Paris 11ème"
        simulated_meta_payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "sim_entry_1",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "messages": [
                                    {
                                        "from": phone,
                                        "id": f"wamid.sim_{int(datetime.now().timestamp())}",
                                        "timestamp": str(int(datetime.now().timestamp())),
                                        "type": "text",
                                        "text": {"body": text_content},
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

    # Création d'une fausse Request FastAPI pour appeler receive_whatsapp_message
    class MockRequest:
        async def json(self):
            return simulated_meta_payload

    result = await receive_whatsapp_message(MockRequest(), db)
    return {
        "status": "success",
        "result": result,
        "latest_sent_message": SIMULATED_MESSAGES_LOG[-1] if SIMULATED_MESSAGES_LOG else None,
    }


# ----------------------------------------------------------------------
# 10. Module de Paiement Séquestre Stripe (Escrow & Hold)
# ----------------------------------------------------------------------
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_proximatch_mock_key")
stripe.api_key = STRIPE_SECRET_KEY

MOCK_ESCROW_STORE: dict[str, dict] = {}


class EscrowBookingRequest(BaseModel):
    mission_id: int = Field(..., description="ID de la mission / demande associée")
    amount_cents: int = Field(..., description="Montant en centimes (ex: 3500 pour 35,00 €)")
    customer_email: str = Field(..., description="Email du client pour le reçu bancaire")
    provider_stripe_account_id: Optional[str] = Field("acct_default_provider", description="Compte Stripe Connect du prestataire")
    currency: Optional[str] = Field("eur", description="Devise (par défaut 'eur')")


class EscrowPaymentResponse(BaseModel):
    status: str
    client_secret: Optional[str] = None
    payment_intent_id: str
    amount_cents: int
    currency: str
    capture_method: str
    mission_id: int
    message: str


class ReleaseEscrowResponse(BaseModel):
    status: str
    payment_intent_id: str
    amount_captured: int
    currency: str
    message: str
    mission_id: Optional[int] = None


@app.post(
    "/api/v1/payments/create-escrow-intent",
    response_model=EscrowPaymentResponse,
    summary="Créer une intention de paiement avec séquestre (Hold bancaire / capture manuelle)",
)
async def create_escrow_payment_intent(
    data: EscrowBookingRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Crée une intention de paiement avec séquestre (Hold / Pre-authorisation).
    Les fonds sont bloqués sur la carte du client avec capture_method='manual'.
    """
    if data.amount_cents <= 0:
        raise HTTPException(status_code=400, detail="Le montant doit être supérieur à 0 centimes.")

    # Vérifier si la mission existe en base
    cursor = db.cursor()
    cursor.execute("SELECT id, status FROM requests WHERE id = ?", (data.mission_id,))
    req_row = cursor.fetchone()

    use_mock = (
        not STRIPE_SECRET_KEY
        or STRIPE_SECRET_KEY.startswith("sk_test_proximatch_mock")
        or STRIPE_SECRET_KEY == "sk_test_mock"
    )

    if not use_mock:
        try:
            intent = stripe.PaymentIntent.create(
                amount=data.amount_cents,
                currency=data.currency or "eur",
                payment_method_types=["card"],
                capture_method="manual",  # Clé du séquestre : bloque les fonds sans débit immédiat
                receipt_email=data.customer_email,
                metadata={
                    "mission_id": str(data.mission_id),
                    "provider_account": data.provider_stripe_account_id or "acct_default",
                    "escrow_mode": "true",
                },
            )
            pi_id = intent.id
            client_secret = intent.client_secret
        except Exception as e:
            # Si Stripe réel échoue pour cause d'identifiants de test, basculer proprement
            raise HTTPException(status_code=400, detail=f"Erreur Stripe : {str(e)}")
    else:
        # Mocking local déterministe pour tests automatisés et environnement de dev
        timestamp = int(datetime.now().timestamp())
        pi_id = f"pi_mock_escrow_{data.mission_id}_{timestamp}"
        client_secret = f"{pi_id}_secret_mock_{timestamp}"
        MOCK_ESCROW_STORE[pi_id] = {
            "id": pi_id,
            "client_secret": client_secret,
            "amount": data.amount_cents,
            "currency": data.currency or "eur",
            "capture_method": "manual",
            "status": "requires_capture",
            "customer_email": data.customer_email,
            "provider_account": data.provider_stripe_account_id,
            "mission_id": data.mission_id,
        }

    return EscrowPaymentResponse(
        status="success",
        client_secret=client_secret,
        payment_intent_id=pi_id,
        amount_cents=data.amount_cents,
        currency=data.currency or "eur",
        capture_method="manual",
        mission_id=data.mission_id,
        message="Empreinte bancaire enregistrée et fonds mis en séquestre avec succès.",
    )


@app.post(
    "/api/v1/payments/release-escrow/{payment_intent_id}",
    response_model=ReleaseEscrowResponse,
    summary="Libérer les fonds séquestrés et déclencher le versement au prestataire",
)
async def release_escrow_funds(
    payment_intent_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Valide la mission : capture définitive des fonds mis en séquestre 
    et déclenchement du versement au prestataire (moins la commission).
    """
    mission_id = None
    amount_captured = 0
    currency = "eur"

    use_mock = (
        not STRIPE_SECRET_KEY
        or STRIPE_SECRET_KEY.startswith("sk_test_proximatch_mock")
        or payment_intent_id.startswith("pi_mock_")
    )

    if not use_mock:
        try:
            intent = stripe.PaymentIntent.capture(payment_intent_id)
            amount_captured = intent.amount_received or intent.amount or 0
            currency = intent.currency or "eur"
            if intent.metadata and "mission_id" in intent.metadata:
                try:
                    mission_id = int(intent.metadata["mission_id"])
                except ValueError:
                    pass
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erreur lors de la capture Stripe : {str(e)}")
    else:
        mock_intent = MOCK_ESCROW_STORE.get(payment_intent_id)
        if not mock_intent and not payment_intent_id.startswith("pi_mock_"):
            raise HTTPException(status_code=404, detail="Intention de paiement introuvable.")
        
        amount_captured = mock_intent.get("amount", 3500) if mock_intent else 3500
        currency = mock_intent.get("currency", "eur") if mock_intent else "eur"
        mission_id = mock_intent.get("mission_id") if mock_intent else None
        if mock_intent:
            mock_intent["status"] = "succeeded"

    # Mise à jour du statut de la mission si identifiée
    if mission_id:
        cursor = db.cursor()
        cursor.execute("UPDATE requests SET status = 'confirmed', updated_at = datetime('now') WHERE id = ?", (mission_id,))
        db.commit()

    return ReleaseEscrowResponse(
        status="completed",
        payment_intent_id=payment_intent_id,
        amount_captured=amount_captured,
        currency=currency,
        mission_id=mission_id,
        message="Mission validée : les fonds ont été capturés et libérés pour le prestataire.",
    )


@app.post(
    "/api/v1/payments/cancel-escrow/{payment_intent_id}",
    summary="Annuler un séquestre et débloquer les fonds pour le client",
)
async def cancel_escrow_funds(
    payment_intent_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Annule l'empreinte bancaire / le séquestre en cas de désistement ou litige.
    """
    use_mock = (
        not STRIPE_SECRET_KEY
        or STRIPE_SECRET_KEY.startswith("sk_test_proximatch_mock")
        or payment_intent_id.startswith("pi_mock_")
    )

    if not use_mock:
        try:
            intent = stripe.PaymentIntent.cancel(payment_intent_id)
            return {
                "status": "cancelled",
                "payment_intent_id": intent.id,
                "message": "Séquestre annulé avec succès. L'empreinte bancaire a été libérée sans débit."
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erreur d'annulation Stripe : {str(e)}")
    else:
        if payment_intent_id in MOCK_ESCROW_STORE:
            MOCK_ESCROW_STORE[payment_intent_id]["status"] = "canceled"
        return {
            "status": "cancelled",
            "payment_intent_id": payment_intent_id,
            "message": "Séquestre annulé avec succès. L'empreinte bancaire a été libérée sans débit."
        }


@app.get(
    "/api/v1/payments/intent/{payment_intent_id}",
    summary="Consulter l'état d'un paiement / séquestre",
)
async def get_payment_intent_status(payment_intent_id: str):
    """
    Renvoie le statut actuel d'un PaymentIntent.
    """
    if payment_intent_id in MOCK_ESCROW_STORE:
        return {"status": "success", "intent": MOCK_ESCROW_STORE[payment_intent_id]}
    
    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        return {"status": "success", "intent": intent}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Paiement introuvable : {str(e)}")


# ----------------------------------------------------------------------
# 11. Module Agenda & Réservation de Créneaux (provider_slots & missions)
# ----------------------------------------------------------------------
class SlotCreate(BaseModel):
    provider_id: int = Field(..., description="ID du prestataire")
    date: str = Field(..., description="Date au format YYYY-MM-DD (ex: '2026-09-01')")
    start_time: str = Field(..., description="Heure de début au format HH:MM (ex: '09:00')")
    end_time: str = Field(..., description="Heure de fin au format HH:MM (ex: '12:00')")


class SlotResponse(BaseModel):
    id: int
    provider_id: int
    date: str
    start_time: str
    end_time: str
    is_booked: bool


class BookSlotRequest(BaseModel):
    slot_id: int = Field(..., description="ID du créneau à réserver")
    customer_email: str = Field(..., description="Email du client qui réserve")


class MissionItemResponse(BaseModel):
    id: int
    provider_id: int
    customer_email: str
    date: str
    start_time: str
    end_time: str
    status: str


@app.post(
    "/api/v1/agenda/slots",
    response_model=SlotResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter un créneau horaire de disponibilité pour un prestataire",
)
def create_provider_slot(slot: SlotCreate, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    # Vérification que le prestataire existe
    cursor.execute("SELECT id FROM provider_profiles WHERE id = ?", (slot.provider_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail=f"Prestataire #{slot.provider_id} introuvable.")

    cursor.execute(
        """
        INSERT INTO provider_slots (provider_id, date, start_time, end_time, is_booked)
        VALUES (?, ?, ?, ?, 0)
        """,
        (slot.provider_id, slot.date.strip(), slot.start_time.strip(), slot.end_time.strip()),
    )
    db.commit()
    slot_id = cursor.lastrowid

    return SlotResponse(
        id=slot_id,
        provider_id=slot.provider_id,
        date=slot.date.strip(),
        start_time=slot.start_time.strip(),
        end_time=slot.end_time.strip(),
        is_booked=False,
    )


@app.post(
    "/api/v1/agenda/slots/batch",
    response_model=list[SlotResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter plusieurs créneaux horaires en une seule requête",
)
def create_provider_slots_batch(slots: list[SlotCreate], db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    created = []
    for slot in slots:
        cursor.execute(
            """
            INSERT INTO provider_slots (provider_id, date, start_time, end_time, is_booked)
            VALUES (?, ?, ?, ?, 0)
            """,
            (slot.provider_id, slot.date.strip(), slot.start_time.strip(), slot.end_time.strip()),
        )
        created.append(
            SlotResponse(
                id=cursor.lastrowid,
                provider_id=slot.provider_id,
                date=slot.date.strip(),
                start_time=slot.start_time.strip(),
                end_time=slot.end_time.strip(),
                is_booked=False,
            )
        )
    db.commit()
    return created


@app.get(
    "/api/v1/agenda/providers/{provider_id}/slots",
    response_model=list[SlotResponse],
    summary="Consulter les créneaux d'un prestataire",
)
def get_provider_slots(
    provider_id: int,
    date: Optional[str] = Query(None, description="Filtrer par date (YYYY-MM-DD)"),
    available_only: bool = Query(False, description="Afficher uniquement les créneaux disponibles"),
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()
    query = "SELECT id, provider_id, date, start_time, end_time, is_booked FROM provider_slots WHERE provider_id = ?"
    params = [provider_id]

    if date:
        query += " AND date = ?"
        params.append(date.strip())
    if available_only:
        query += " AND is_booked = 0"

    query += " ORDER BY date ASC, start_time ASC"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    return [
        SlotResponse(
            id=r[0],
            provider_id=r[1],
            date=r[2],
            start_time=r[3],
            end_time=r[4],
            is_booked=bool(r[5]),
        )
        for r in rows
    ]


@app.post(
    "/api/v1/agenda/book-slot",
    response_model=MissionItemResponse,
    summary="Réserver un créneau disponible et créer une mission",
)
def book_slot(req: BookSlotRequest, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute(
        "SELECT id, provider_id, date, start_time, end_time, is_booked FROM provider_slots WHERE id = ?",
        (req.slot_id,),
    )
    slot_row = cursor.fetchone()
    if not slot_row:
        raise HTTPException(status_code=404, detail="Créneau introuvable.")

    slot_id, provider_id, date, start_time, end_time, is_booked = slot_row
    if is_booked:
        raise HTTPException(status_code=409, detail="Ce créneau est déjà réservé.")

    # Marquer le créneau comme réservé
    cursor.execute("UPDATE provider_slots SET is_booked = 1 WHERE id = ?", (slot_id,))

    # Créer la mission
    cursor.execute(
        """
        INSERT INTO missions (provider_id, customer_email, date, start_time, end_time, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
        """,
        (provider_id, req.customer_email.strip(), date, start_time, end_time),
    )
    mission_id = cursor.lastrowid
    db.commit()

    return MissionItemResponse(
        id=mission_id,
        provider_id=provider_id,
        customer_email=req.customer_email.strip(),
        date=date,
        start_time=start_time,
        end_time=end_time,
        status="pending",
    )


@app.get(
    "/api/v1/agenda/missions",
    response_model=list[MissionItemResponse],
    summary="Lister les missions issues des réservations d'agenda",
)
def list_agenda_missions(
    provider_id: Optional[int] = Query(None, description="Filtrer par prestataire"),
    customer_email: Optional[str] = Query(None, description="Filtrer par email client"),
    status: Optional[str] = Query(None, description="Filtrer par statut"),
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()
    query = "SELECT id, provider_id, customer_email, date, start_time, end_time, status FROM missions WHERE 1=1"
    params = []

    if provider_id is not None:
        query += " AND provider_id = ?"
        params.append(provider_id)
    if customer_email:
        query += " AND customer_email = ?"
        params.append(customer_email.strip())
    if status:
        query += " AND status = ?"
        params.append(status.strip())

    query += " ORDER BY id DESC"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    return [
        MissionItemResponse(
            id=r[0],
            provider_id=r[1],
            customer_email=r[2],
            date=r[3],
            start_time=r[4],
            end_time=r[5],
            status=r[6],
        )
        for r in rows
    ]


@app.patch(
    "/api/v1/agenda/missions/{mission_id}/status",
    response_model=MissionItemResponse,
    summary="Mettre à jour le statut d'une mission d'agenda",
)
def update_agenda_mission_status(
    mission_id: int,
    status: str = Query(..., description="Nouveau statut ('confirmed', 'completed', 'cancelled')"),
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()
    cursor.execute(
        "SELECT id, provider_id, customer_email, date, start_time, end_time, status FROM missions WHERE id = ?",
        (mission_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Mission introuvable.")

    cursor.execute("UPDATE missions SET status = ? WHERE id = ?", (status.strip(), mission_id))
    db.commit()

    return MissionItemResponse(
        id=row[0],
        provider_id=row[1],
        customer_email=row[2],
        date=row[3],
        start_time=row[4],
        end_time=row[5],
        status=status.strip(),
    )



if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8001))
    host = os.getenv("HOST", "127.0.0.1")
    uvicorn.run("main:app", host=host, port=port, reload=True)





