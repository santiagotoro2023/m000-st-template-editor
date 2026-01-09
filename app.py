from fastapi import FastAPI, Form, UploadFile, File, Body, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from docxtpl import DocxTemplate
from docx2pdf import convert
import os
import uuid
import json
import hashlib
import base64
import io
from datetime import datetime, timedelta
import subprocess
import sys
import time
try:
    import win32com.client  # type: ignore
    import pythoncom  # type: ignore
    HAVE_WIN32 = True
except Exception:
    HAVE_WIN32 = False
from typing import Optional, Dict, List, Any, Tuple
from pathlib import Path
from functools import lru_cache
from contextlib import contextmanager

# DB and auth imports
from sqlmodel import SQLModel, Field, create_engine, Session as DBSession, select
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, Header
from sqlalchemy import text
from sqlalchemy.pool import StaticPool, NullPool
import secrets
import pyotp
import qrcode

# Threading imports
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue

# ==================== Configuration ====================
class Config:
    """Centralized configuration"""
    DATABASE_URL = "sqlite:///data.db"
    TEMPLATE_DIR = Path("templates")
    TEMP_DIR = Path("temp")
    ARCHIVE_DIR = Path("archive")
    DEFAULT_SESSION_LIFETIME_DAYS = 5
    DEFAULT_TEMP_RETENTION_HOURS = 48
    CLEANER_INTERVAL_SECONDS = 3600
    JSON_INDENT = 2
    PDF_WORKER_THREADS = 3  # Concurrent PDF conversions
    DOCX_RENDER_CACHE_SIZE = 10  # Cache rendered templates
    
    @classmethod
    def init_dirs(cls):
        """Initialize required directories"""
        cls.TEMPLATE_DIR.mkdir(exist_ok=True)
        cls.TEMP_DIR.mkdir(exist_ok=True)
        cls.ARCHIVE_DIR.mkdir(exist_ok=True)

Config.init_dirs()

# ==================== Database Models ====================
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True)
    hashed_password: str
    is_active: bool = True
    is_admin: bool = False
    twofa_enabled: bool = False
    totp_secret: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class AuthToken(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    token_hash: str = Field(index=True)
    scope: str = Field(default="access")  # access or setup
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime

class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str

class StandardField(SQLModel, table=True):
    key: str = Field(primary_key=True)
    type: str  # text, date, checkbox

class Customer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    fields_json: str = Field(default='{}')

class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    template_name: str
    status: str = Field(default="draft")  # draft, ready, generated, printed, archived
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class OrderField(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="order.id")
    key: str
    type: str = Field(default="text")
    value: Optional[str] = Field(default=None)
    required: bool = Field(default=False)

class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: Optional[int] = Field(default=None, foreign_key="order.id")
    file_name: str
    path: str
    template_name: Optional[str] = None
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    fields_json: Optional[str] = Field(default=None)
    task_id: Optional[str] = None
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    printed_at: Optional[datetime] = None
    checksum: Optional[str] = None

# ==================== Database Setup ====================
# Connection pooling optimized for SQLite
engine = create_engine(
    Config.DATABASE_URL,
    connect_args={
        "check_same_thread": False,
        "timeout": 30,
    },
    poolclass=NullPool,  # Thread-safe for SQLite: creates new connection per request
    echo=False,
)

@contextmanager
def get_db():
    """Context manager for database sessions with automatic cleanup"""
    session = DBSession(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# ==================== Auth & Password ====================
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

def hash_password(pw: str) -> str:
    return pwd_context.hash(pw)

def verify_password(pw: str, hashed: str) -> bool:
    return pwd_context.verify(pw, hashed)

# ==================== Helper Functions ====================
class JSONHelper:
    """Centralized JSON file operations with error handling"""
    
    @staticmethod
    def load(path: Path, default: Any = None) -> Any:
        """Load JSON file with default fallback"""
        try:
            if path.exists():
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        # Migrate document table to include new optional columns
        try:
            with engine.connect() as conn:
                res = conn.execute(text("PRAGMA table_info('document')")).fetchall()
                if res:
                    cols = {r[1] for r in res}
                    if 'template_name' not in cols:
                        conn.execute(text("ALTER TABLE 'document' ADD COLUMN template_name TEXT")); conn.commit()
                    if 'customer_id' not in cols:
                        conn.execute(text("ALTER TABLE 'document' ADD COLUMN customer_id INTEGER")); conn.commit()
                    if 'user_id' not in cols:
                        conn.execute(text("ALTER TABLE 'document' ADD COLUMN user_id INTEGER")); conn.commit()
                    if 'fields_json' not in cols:
                        conn.execute(text("ALTER TABLE 'document' ADD COLUMN fields_json TEXT")); conn.commit()
                    if 'task_id' not in cols:
                        conn.execute(text("ALTER TABLE 'document' ADD COLUMN task_id TEXT")); conn.commit()
        except Exception:
            pass
        return default if default is not None else {}
    
    @staticmethod
    def save(path: Path, data: Any) -> bool:
        """Save JSON file with error handling"""
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=Config.JSON_INDENT)
            return True
        except Exception:
            return False
    
    @staticmethod
    def parse(json_str: str, default: Any = None) -> Any:
        """Parse JSON string with default fallback"""
        try:
            return json.loads(json_str)
        except Exception:
            return default if default is not None else {}

class TemplateHelper:
    """Template-related operations"""
    
    @staticmethod
    def get_json_path(template_name: str) -> Path:
        """Get JSON path for a template"""
        return Config.TEMPLATE_DIR / template_name.replace(".docx", ".json")
    
    @staticmethod
    def get_docx_path(template_name: str) -> Path:
        """Get DOCX path for a template"""
        return Config.TEMPLATE_DIR / template_name
    
    @staticmethod
    def load_template_meta(template_name: str) -> Dict[str, Any]:
        """Load template metadata"""
        json_path = TemplateHelper.get_json_path(template_name)
        return JSONHelper.load(json_path, {"fields": []})
    
    @staticmethod
    def save_template_meta(template_name: str, data: Dict[str, Any]) -> bool:
        """Save template metadata"""
        json_path = TemplateHelper.get_json_path(template_name)
        return JSONHelper.save(json_path, data)
    
    @staticmethod
    def list_templates() -> List[str]:
        """List all template files"""
        return [f.name for f in Config.TEMPLATE_DIR.glob("*.docx")]

class SettingsCache:
    """Cache for frequently accessed settings"""
    _cache: Dict[str, Tuple[Any, float]] = {}
    _cache_duration = 60  # seconds
    _lock = Lock()
    
    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """Get cached setting or fetch from DB"""
        with cls._lock:
            if key in cls._cache:
                value, timestamp = cls._cache[key]
                if time.time() - timestamp < cls._cache_duration:
                    return value
        
        # Cache miss - fetch from DB
        with get_db() as db:
            setting = db.get(Setting, key)
            value = setting.value if setting else default
        
        with cls._lock:
            cls._cache[key] = (value, time.time())
        
        return value
    
    @classmethod
    def invalidate(cls, key: Optional[str] = None):
        """Invalidate cache for a key or all keys"""
        with cls._lock:
            if key:
                cls._cache.pop(key, None)
            else:
                cls._cache.clear()
    
    @classmethod
    def get_int(cls, key: str, default: int) -> int:
        """Get integer setting"""
        value = cls.get(key, str(default))
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

# ==================== Security Helpers ====================
def hash_token(raw: str) -> str:
    """Hash raw token for storage."""
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def generate_totp_secret() -> str:
    """Generate a new TOTP secret."""
    return pyotp.random_base32()


def build_otpauth_url(secret: str, username: str) -> str:
    """Build otpauth URI for authenticator apps."""
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="Vorlagen Editor")


def qr_data_uri(data: str) -> str:
    """Render a QR code as a data URI."""
    buf = io.BytesIO()
    qrcode.make(data, box_size=6, border=2).save(buf, format="PNG") # type: ignore
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode('utf-8')


def verify_totp(secret: Optional[str], otp: Optional[str]) -> bool:
    """Verify a provided OTP code against a secret."""
    if not secret or not otp:
        return False
    try:
        return bool(pyotp.TOTP(secret).verify(str(otp), valid_window=1))
    except Exception:
        return False


def cleanup_expired_tokens(db: DBSession) -> None:
    """Remove expired auth tokens from storage."""
    now = datetime.utcnow()
    expired = db.exec(select(AuthToken).where(AuthToken.expires_at < now)).all()
    for token in expired:
        db.delete(token)


def create_auth_token(
    db: DBSession,
    user_id: int,
    scope: str = "access",
    lifetime_seconds: int = 0,
) -> Tuple[str, datetime]:
    """Create and persist a hashed auth token, returning the raw token and expiry."""
    if lifetime_seconds <= 0:
        lifetime_seconds = Config.DEFAULT_SESSION_LIFETIME_DAYS * 86400
    raw = secrets.token_urlsafe(48)
    token_hash = hash_token(raw)
    expires_at = datetime.utcnow() + timedelta(seconds=lifetime_seconds)
    db.add(AuthToken(user_id=user_id, token_hash=token_hash, scope=scope, expires_at=expires_at))
    return raw, expires_at


def revoke_tokens_for_user(db: DBSession, user_id: int, scope: Optional[str] = None) -> int:
    """Delete tokens for a user, optionally filtered by scope."""
    tokens = db.exec(select(AuthToken).where(AuthToken.user_id == user_id)).all()
    count = 0
    for t in tokens:
        if scope and t.scope != scope:
            continue
        db.delete(t)
        count += 1
    return count


def extract_bearer_token(auth_header: Optional[str]) -> str:
    """Parse Authorization header and extract the bearer token."""
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authorization header missing")
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token missing")
    return token


def get_user_from_token(auth_header: Optional[str], required_scope: str = "access") -> Dict[str, Any]:
    """Validate a bearer token and return the user dict."""
    token = extract_bearer_token(auth_header)
    token_hash = hash_token(token)

    with get_db() as db:
        cleanup_expired_tokens(db)
        auth_token = db.exec(
            select(AuthToken).where(
                AuthToken.token_hash == token_hash,
                AuthToken.scope == required_scope
            )
        ).first()
        if not auth_token or auth_token.expires_at < datetime.utcnow():
            raise HTTPException(status_code=401, detail="Token invalid or expired")

        # Use query instead of .get() to avoid caching issues
        user = db.exec(select(User).where(User.id == auth_token.user_id)).first()
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="User inactive")

        return {
            "id": user.id,
            "username": user.username,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "twofa_enabled": bool(user.twofa_enabled),
            "totp_secret": user.totp_secret if required_scope == "setup" else None,
        }

# ==================== Background Task Management ====================
class TaskManager:
    """Centralized task tracking"""
    _tasks: Dict[str, Dict[str, Any]] = {}
    _lock = Lock()
    
    @classmethod
    def create_task(cls, task_id: str, initial_data: Dict[str, Any]) -> None:
        with cls._lock:
            cls._tasks[task_id] = initial_data
    
    @classmethod
    def update_task(cls, task_id: str, **kwargs) -> None:
        with cls._lock:
            if task_id in cls._tasks:
                cls._tasks[task_id].update(kwargs)
    
    @classmethod
    def get_task(cls, task_id: str) -> Optional[Dict[str, Any]]:
        with cls._lock:
            return cls._tasks.get(task_id)
    
    @classmethod
    def cleanup_old_tasks(cls, max_age_seconds: int = 3600) -> int:
        """Remove tasks older than max_age_seconds"""
        cutoff = time.time() - max_age_seconds
        count = 0
        with cls._lock:
            to_remove = []
            for task_id, task_data in cls._tasks.items():
                if task_data.get('timestamp', 0) < cutoff:
                    to_remove.append(task_id)
            for task_id in to_remove:
                del cls._tasks[task_id]
                count += 1
        return count

# ==================== File Cleanup ====================
class FileCleanup:
    """Centralized file cleanup logic"""
    _cleaner_started = False
    _lock = Lock()
    
    @classmethod
    def cleanup_once(cls, retention_hours: int) -> int:
        """Clean up old files in temp directory"""
        cutoff = time.time() - (retention_hours * 3600)
        count = 0
        for file_path in Config.TEMP_DIR.iterdir():
            try:
                if file_path.is_file() and file_path.stat().st_mtime < cutoff:
                    file_path.unlink()
                    count += 1
            except Exception:
                pass
        return count
    
    @classmethod
    def cleanup_loop(cls):
        """Background cleanup loop"""
        while True:
            try:
                hours = SettingsCache.get_int("temp_retention_hours", Config.DEFAULT_TEMP_RETENTION_HOURS)
                cls.cleanup_once(hours)
                TaskManager.cleanup_old_tasks(3600)  # Also cleanup old tasks
            except Exception:
                pass
            time.sleep(Config.CLEANER_INTERVAL_SECONDS)
    
    @classmethod
    def start_cleaner(cls):
        """Start cleanup thread if not already running"""
        with cls._lock:
            if not cls._cleaner_started:
                Thread(target=cls.cleanup_loop, daemon=True).start()
                cls._cleaner_started = True

# ==================== FastAPI App ====================
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Add basic security headers to all responses."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    return response

# ==================== Startup & Initialization ====================
@app.on_event("startup")
def on_startup():
    """Initialize database and start background tasks"""
    # Create tables
    SQLModel.metadata.create_all(engine)
    
    # Migrate username column if needed
    with engine.connect() as conn:
        res = conn.execute(text("PRAGMA table_info('user')")).fetchall()
        cols = [r[1] for r in res]
        if 'username' not in cols:
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN username TEXT"))
            conn.execute(text("UPDATE \"user\" SET username = email WHERE username IS NULL"))
            conn.commit()
        if 'twofa_enabled' not in cols:
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN twofa_enabled INTEGER DEFAULT 0"))
            conn.commit()
        if 'totp_secret' not in cols:
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN totp_secret TEXT"))
            conn.commit()
    
    # Initialize default settings and data
    with get_db() as db:
        # Migrate auth_token table columns if missing
        try:
            with engine.connect() as conn:
                for tname in ("auth_token", "authtoken"):
                    res = conn.execute(text(f"PRAGMA table_info('{tname}')")).fetchall()
                    if not res:
                        continue
                    cols = {r[1] for r in res}
                    if 'scope' not in cols:
                        conn.execute(text(f"ALTER TABLE '{tname}' ADD COLUMN scope TEXT DEFAULT 'access'"))
                        conn.commit()
                    if 'created_at' not in cols:
                        conn.execute(text(f"ALTER TABLE '{tname}' ADD COLUMN created_at TEXT DEFAULT (datetime('now'))"))
                        conn.commit()
                    # ensure indexes as needed are not critical here
                    break
        except Exception:
            pass
        # Default settings
        if not db.get(Setting, "session_lifetime_days"):
            db.add(Setting(key="session_lifetime_days", value=str(Config.DEFAULT_SESSION_LIFETIME_DAYS)))
        
        if not db.get(Setting, "temp_retention_hours"):
            db.add(Setting(key="temp_retention_hours", value=str(Config.DEFAULT_TEMP_RETENTION_HOURS)))
        
        # Default standard fields
        default_fields = [
            ("FIRMENNAME", "text"),
            ("ADRESSE", "text"),
            ("PLZ", "text"),
            ("ORT", "text"),
        ]
        for key, field_type in default_fields:
            if not db.get(StandardField, key):
                db.add(StandardField(key=key, type=field_type))
        
        # Default admin user
        if not db.exec(select(User)).first():
            admin = User(
                username="admin",
                hashed_password=hash_password("admin"),
                is_admin=True
            )
            db.add(admin)
            print("Created initial admin user: admin with password: admin")
    
    # Run initial cleanup
    try:
        hours = SettingsCache.get_int("temp_retention_hours", Config.DEFAULT_TEMP_RETENTION_HOURS)
        FileCleanup.cleanup_once(hours)
    except Exception:
        pass
    
    # Start background tasks
    FileCleanup.start_cleaner()
    # Warm up Word converter (non-blocking)
    try:
        WordConverter.start()
    except Exception:
        pass

# ==================== Dependencies ====================
def get_current_user(request: Request, auth_header: Optional[str] = Header(None, alias="Authorization")) -> Dict[str, Any]:
    """Get current authenticated user via bearer token or session cookie."""
    try:
        return get_user_from_token(auth_header, required_scope="access")
    except HTTPException:
        # Fallback to cookie-based session
        cookie_token = request.cookies.get("ve_access")
        if not cookie_token:
            raise
        return get_user_from_token(f"Bearer {cookie_token}", required_scope="access")


def get_setup_context(request: Request, auth_header: Optional[str] = Header(None, alias="Authorization")) -> Dict[str, Any]:
    """Get user context for setup-scoped tokens (2FA provisioning)."""
    try:
        return get_user_from_token(auth_header, required_scope="setup")
    except HTTPException:
        cookie_token = request.cookies.get("ve_setup")
        if not cookie_token:
            raise
        return get_user_from_token(f"Bearer {cookie_token}", required_scope="setup")

def admin_required(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Require admin privileges"""
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Administrator required")
    return user

# ==================== Routes: Frontend ====================
@app.get("/", response_class=HTMLResponse)
async def read_index():
    """Serve main HTML page"""
    try:
        with open("static/index.html", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load page")

# ==================== Routes: Templates ====================
@app.get("/templates")
async def list_templates_endpoint(user: Dict[str, Any] = Depends(get_current_user)):
    """List all templates with their fields"""
    templates = []
    for template_file in TemplateHelper.list_templates():
        meta = TemplateHelper.load_template_meta(template_file)
        templates.append({
            "name": template_file,
            "fields": meta.get("fields", [])
        })
    return templates

@app.post("/templates/upload")
async def upload_template(
    file: UploadFile = File(...),
    add_standard_fields: bool = Form(True),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Upload a new template"""
    if not file.filename:
        return JSONResponse({"error": "Dateiname erforderlich"}, status_code=400)
    
    save_path = Config.TEMPLATE_DIR / file.filename
    contents = await file.read()
    
    with open(save_path, "wb") as f:
        f.write(contents)
    
    json_path = TemplateHelper.get_json_path(file.filename)
    if not json_path.exists():
        fields = []
        if add_standard_fields:
            with get_db() as db:
                std_fields = db.exec(select(StandardField)).all()
                fields = [{"name": sf.key, "type": sf.type} for sf in std_fields]
        
        JSONHelper.save(json_path, {"fields": fields})
    
    return {"message": f"Template {file.filename} hochgeladen"}

@app.delete("/templates/{template_name}")
async def delete_template(template_name: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Delete a template and its metadata"""
    docx_path = TemplateHelper.get_docx_path(template_name)
    json_path = TemplateHelper.get_json_path(template_name)
    
    if docx_path.exists():
        docx_path.unlink()
    if json_path.exists():
        json_path.unlink()
    
    return {"message": "Template gelöscht"}

@app.get("/templates/{template_name}")
async def get_template_fields(template_name: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Get template field definitions"""
    return TemplateHelper.load_template_meta(template_name)

# ==================== Routes: Template Fields (CRUD) ====================
@app.post("/templates/{template_name}/fields/add")
async def add_field(
    template_name: str,
    field: dict = Body(...),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Add a new field to a template"""
    meta = TemplateHelper.load_template_meta(template_name)
    fields = list(meta.get("fields", []))
    fields.append(field)
    meta["fields"] = fields
    TemplateHelper.save_template_meta(template_name, meta)
    return {"message": "Feld hinzugefügt", "fields": meta["fields"]}

@app.post("/templates/{template_name}/fields/update/{index}")
async def update_field(
    template_name: str,
    index: int,
    field: dict = Body(...),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Update template field"""
    meta = TemplateHelper.load_template_meta(template_name)
    fields = list(meta.get("fields", []))
    if 0 <= index < len(fields):
        fields[index] = field
        meta["fields"] = fields
        TemplateHelper.save_template_meta(template_name, meta)
        return {"message": "Feld aktualisiert", "fields": meta["fields"]}
    return JSONResponse({"error": "Index ungültig"}, status_code=400)

@app.delete("/templates/{template_name}/fields/{index}")
async def delete_field(
    template_name: str,
    index: int,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Delete template field"""
    meta = TemplateHelper.load_template_meta(template_name)
    fields = list(meta.get("fields", []))
    if 0 <= index < len(fields):
        removed = fields.pop(index)
        meta["fields"] = fields
        TemplateHelper.save_template_meta(template_name, meta)
        return {"message": "Feld gelöscht", "removed": removed, "fields": meta["fields"]}
    return JSONResponse({"error": "Index ungültig"}, status_code=400)

# ==================== Routes: Auth ====================
@app.post("/auth/login")
async def auth_login(
    username: str = Form(...),
    password: str = Form(...),
    otp: Optional[str] = Form(None)
):
    """User login using bearer tokens with optional TOTP verification."""
    with get_db() as db:
        user = db.exec(select(User).where(User.username == username)).first()

        if not user or not verify_password(password, user.hashed_password):
            return JSONResponse({"error": "Ungültige Zugangsdaten"}, status_code=401)

        if not user.is_active:
            return JSONResponse({"error": "Konto ist deaktiviert"}, status_code=403)

        if user.id is None:
            return JSONResponse({"error": "Ungültiger Benutzer"}, status_code=500)

        # Auto-provision 2FA for non-admin users without any secret
        if not user.is_admin and not user.totp_secret:
            secret = generate_totp_secret()
            user.totp_secret = secret
            user.twofa_enabled = False
            db.add(user)
            revoke_tokens_for_user(db, int(user.id))
            setup_token, setup_exp = create_auth_token(
                db,
                int(user.id),
                scope="setup",
                lifetime_seconds=600,
            )
            otpauth_url = build_otpauth_url(secret, user.username)
            return {
                "status": "2fa_setup_required",
                "message": "Bitte 2FA einrichten, um fortzufahren",
                "secret": secret,
                "otpauth_url": otpauth_url,
                "qr": qr_data_uri(otpauth_url),
                "setup_token": setup_token,
                "expires_at": setup_exp.isoformat(),
            }

        # Require OTP when 2FA is enabled
        if user.twofa_enabled:
            if not otp:
                return JSONResponse({"error": "OTP erforderlich", "otp_required": True}, status_code=401)
            if not verify_totp(user.totp_secret, otp):
                return JSONResponse({"error": "OTP ungültig"}, status_code=401)

        # Issue access token (session cookie: no Expires/Max-Age)
        revoke_tokens_for_user(db, int(user.id), scope="access")
        days = SettingsCache.get_int("session_lifetime_days", Config.DEFAULT_SESSION_LIFETIME_DAYS)
        token, expires_at = create_auth_token(
            db,
            int(user.id),
            scope="access",
            lifetime_seconds=days * 86400,
        )
        resp = JSONResponse({
            "access_token": token,
            "expires_at": expires_at.isoformat(),
            "is_admin": user.is_admin,
            "twofa_enabled": bool(user.twofa_enabled),
        })
        # Set session cookie (no max-age/expires => session-only)
        resp.set_cookie(
            key="ve_access",
            value=token,
            httponly=True,
            samesite="lax",
            secure=False,
            path="/",
        )
        return resp


@app.post("/auth/confirm-2fa")
def auth_confirm_2fa(
    payload: dict = Body(...),
    ctx: Dict[str, Any] = Depends(get_setup_context)
):
    """Confirm a TOTP setup token and issue an access token."""
    otp = payload.get("otp") if payload else None
    if not verify_totp(ctx.get("totp_secret"), otp):
        return JSONResponse({"error": "OTP ungültig"}, status_code=401)

    with get_db() as db:
        user = db.get(User, ctx["id"])
        if not user:
            return JSONResponse({"error": "User not found"}, status_code=404)
        user.twofa_enabled = True
        db.add(user)
        revoke_tokens_for_user(db, int(user.id), scope="setup") # type: ignore
        revoke_tokens_for_user(db, int(user.id), scope="access") # type: ignore

        days = SettingsCache.get_int("session_lifetime_days", Config.DEFAULT_SESSION_LIFETIME_DAYS)
        token, expires_at = create_auth_token(
            db,
            int(user.id), # type: ignore
            scope="access",
            lifetime_seconds=days * 86400,
        )

    return {
        "access_token": token,
        "expires_at": expires_at.isoformat(),
        "twofa_enabled": True,
    }


@app.post("/auth/logout")
def auth_logout(request: Request, auth_header: Optional[str] = Header(None, alias="Authorization")):
    """User logout by revoking token (header or cookie) and clearing session cookie."""
    raw_token: Optional[str] = None
    try:
        raw_token = extract_bearer_token(auth_header)
    except HTTPException:
        raw_token = request.cookies.get("ve_access")

    if raw_token:
        token_hash = hash_token(raw_token)
        with get_db() as db:
            record = db.exec(select(AuthToken).where(AuthToken.token_hash == token_hash)).first()
            if record:
                db.delete(record)
    resp = JSONResponse({"message": "logged out"})
    resp.delete_cookie("ve_access", path="/")
    return resp


@app.get("/me")
def me(user: Dict[str, Any] = Depends(get_current_user)):
    """Get current user info"""
    return {
        "username": user["username"],
        "is_admin": user["is_admin"],
        "is_active": user["is_active"],
        "twofa_enabled": bool(user.get("twofa_enabled")),
    }

# ==================== Routes: Standard Fields ====================
@app.get("/standard-fields")
def get_standard_fields(user: Dict[str, Any] = Depends(get_current_user)):
    """Get all standard fields"""
    with get_db() as db:
        fields = db.exec(select(StandardField)).all()
        return [{"key": sf.key, "type": sf.type} for sf in fields]

@app.get("/admin/standard-fields")
def admin_list_standard_fields(admin: Dict[str, Any] = Depends(admin_required)):
    """Admin: List all standard fields"""
    with get_db() as db:
        fields = db.exec(select(StandardField)).all()
        return [{"key": sf.key, "type": sf.type} for sf in fields]

@app.post("/admin/standard-fields")
def admin_add_standard_field(
    payload: dict = Body(...),
    admin: Dict[str, Any] = Depends(admin_required)
):
    """Admin: Add or update standard field"""
    key = (payload.get("key") or "").strip().upper()
    field_type = (payload.get("type") or "text").strip()
    
    if not key or field_type not in ["text", "date", "checkbox"]:
        return JSONResponse(
            {"error": "key erforderlich und type muss text, date oder checkbox sein"},
            status_code=400
        )
    
    with get_db() as db:
        existing = db.get(StandardField, key)
        if existing:
            existing.type = field_type
            db.add(existing)
        else:
            db.add(StandardField(key=key, type=field_type))
    
    return {"key": key, "type": field_type}

@app.delete("/admin/standard-fields/{key}")
def admin_delete_standard_field(key: str, admin: Dict[str, Any] = Depends(admin_required)):
    """Admin: Delete standard field"""
    with get_db() as db:
        sf = db.get(StandardField, key)
        if not sf:
            return JSONResponse({"error": "not found"}, status_code=404)
        db.delete(sf)
    
    return {"message": "deleted"}

# ==================== Routes: Customers ====================
@app.get("/customers")
def list_customers(q: Optional[str] = None, user: Dict[str, Any] = Depends(get_current_user)):
    """List all customers with optional search query"""
    with get_db() as db:
        customers = db.exec(select(Customer)).all()
        results = []
        ql = (q or "").strip().lower()
        for c in customers:
            fields = JSONHelper.parse(c.fields_json, {})
            if ql:
                hay = (c.name or "").lower() + " " + json.dumps(fields, ensure_ascii=False).lower()
                if ql not in hay:
                    continue
            results.append({
                "id": c.id,
                "name": c.name,
                "fields": fields,
            })
        return results

    # ==================== Routes: Orders ====================
    @app.get("/orders")
    def list_orders(
        status: Optional[str] = None,
        q: Optional[str] = None,
        customer_id: Optional[int] = None,
        user: Dict[str, Any] = Depends(get_current_user)
    ):
        with get_db() as db:
            orders = db.exec(select(Order)).all()
            ql = (q or "").strip().lower()
            out = []
            for o in orders:
                if status and o.status != status:
                    continue
                if customer_id and o.customer_id != customer_id:
                    continue
                if ql:
                    hay = (o.title or "").lower() + " " + (o.template_name or "").lower()
                    if ql not in hay:
                        continue
                out.append({
                    "id": o.id,
                    "title": o.title,
                    "status": o.status,
                    "template_name": o.template_name,
                    "customer_id": o.customer_id,
                    "created_at": o.created_at.isoformat(),
                    "updated_at": o.updated_at.isoformat(),
                })
            return out

    @app.post("/orders")
    def create_order(
        payload: dict = Body(...),
        user: Dict[str, Any] = Depends(get_current_user)
    ):
        title = (payload.get("title") or "").strip() or f"Auftrag {int(time.time())}"
        template_name = (payload.get("template_name") or "").strip()
        customer_id = payload.get("customer_id")
        if not template_name:
            return JSONResponse({"error": "template_name erforderlich"}, status_code=400)
        # Create order
        with get_db() as db:
            order = Order(title=title, template_name=template_name, customer_id=customer_id)
            db.add(order); db.commit(); db.refresh(order)
            assert order.id is not None
            oid = order.id
            # Pre-populate fields from template schema
            meta = TemplateHelper.load_template_meta(template_name)
            for f in meta.get("fields", []):
                db.add(OrderField(order_id=oid, key=f.get("name"), type=f.get("type", "text"), value=None, required=False))
            db.commit()
            return {"id": order.id}

    @app.get("/orders/{order_id}")
    def get_order(order_id: int, user: Dict[str, Any] = Depends(get_current_user)):
        with get_db() as db:
            order = db.get(Order, order_id)
            if not order:
                return JSONResponse({"error": "Nicht gefunden"}, status_code=404)
            fields = db.exec(select(OrderField).where(OrderField.order_id == order_id)).all()
            return {
                "id": order.id,
                "title": order.title,
                "status": order.status,
                "template_name": order.template_name,
                "customer_id": order.customer_id,
                "fields": [{"key": f.key, "type": f.type, "value": f.value, "required": f.required} for f in fields]
            }

    @app.patch("/orders/{order_id}")
    def update_order(order_id: int, payload: dict = Body(...), user: Dict[str, Any] = Depends(get_current_user)):
        with get_db() as db:
            order = db.get(Order, order_id)
            if not order:
                return JSONResponse({"error": "Nicht gefunden"}, status_code=404)
            status = payload.get("status")
            title = payload.get("title")
            if status:
                order.status = str(status)
            if title:
                order.title = str(title)
            order.updated_at = datetime.utcnow()
            db.add(order)
            return {"message": "Aktualisiert"}

    @app.patch("/orders/{order_id}/fields")
    def update_order_fields(order_id: int, payload: dict = Body(...), user: Dict[str, Any] = Depends(get_current_user)):
        """Update multiple fields: payload = {fields: [{key,type?,value,required?}]}"""
        items = payload.get("fields") or []
        with get_db() as db:
            for item in items:
                key = item.get("key")
                if not key:
                    continue
                of = db.exec(select(OrderField).where(OrderField.order_id == order_id, OrderField.key == key)).first()
                if of:
                    of.value = item.get("value")
                    if item.get("type"):
                        of.type = item.get("type")
                    if item.get("required") is not None:
                        of.required = bool(item.get("required"))
                    db.add(of)
                else:
                    db.add(OrderField(order_id=order_id, key=key, type=item.get("type", "text"), value=item.get("value"), required=bool(item.get("required"))))
            return {"message": "Felder aktualisiert"}

    @app.post("/orders/{order_id}/generate")
    async def generate_order_pdf(order_id: int, user: Dict[str, Any] = Depends(get_current_user)):
        with get_db() as db:
            order = db.get(Order, order_id)
            if not order:
                return JSONResponse({"error": "Nicht gefunden"}, status_code=404)
        template_docx = TemplateHelper.get_docx_path(order.template_name)
        if not template_docx.exists():
            return JSONResponse({"error": "Template nicht gefunden"}, status_code=404)
        template_meta = TemplateHelper.load_template_meta(order.template_name)
        # Build form_dict from order fields
        with get_db() as db:
            fields = db.exec(select(OrderField).where(OrderField.order_id == order_id)).all()
        form_dict = {f.key: f.value for f in fields}
        # Merge customer defaults
        customer_name: Optional[str] = None
        if order.customer_id:
            with get_db() as db:
                cust = db.get(Customer, order.customer_id)
                if cust:
                    customer_name = cust.name
                    cfields = JSONHelper.parse(cust.fields_json, {})
                    for k, v in cfields.items():
                        form_dict.setdefault(k, v)
        # Filename via pattern
        file_name = None
        pattern = SettingsCache.get('filename_pattern', '{template}-{customer}-{date}')
        date_str = datetime.utcnow().strftime('%Y-%m-%d')
        tpl = order.template_name.replace('.docx','')
        cust = (customer_name or '').strip()
        try:
            file_name = pattern.format(template=tpl, customer=cust, date=date_str)
        except Exception:
            file_name = f"{tpl}-{date_str}"
        file_name = ''.join(ch for ch in file_name if ch not in '\\/:*?"<>|').strip() or tpl
        # Create task
        task_id = f"{uuid.uuid4().hex}.pdf"
        TaskManager.create_task(task_id, {
            'status': 'queued',
            'percent': 0,
            'file_name': f"{file_name}.pdf",
            'timestamp': time.time(),
            'order_id': order_id,
            'template_name': order.template_name,
            'customer_id': (int(order.customer_id) if order.customer_id is not None else None),
            'user_id': user.get('id'),
            'fields': form_dict,
            'task_id': task_id,
        })
        # Submit to executor
        executor = PDFGenerator.get_executor()
        executor.submit(
            PDFGenerator.worker,
            task_id,
            template_docx,
            template_meta,
            form_dict,
            f"{file_name}.pdf"
        )
        return {"id": task_id, "file_name": f"{file_name}.pdf"}

    @app.post("/orders/{order_id}/finalize")
    def finalize_order_document(order_id: int, payload: dict = Body(...), user: Dict[str, Any] = Depends(get_current_user)):
        task_id = payload.get('task_id')
        if not task_id:
            return JSONResponse({"error": "task_id erforderlich"}, status_code=400)
        task = TaskManager.get_task(task_id)
        if not task or task.get('status') != 'done':
            return JSONResponse({"error": "Task nicht abgeschlossen"}, status_code=400)
        # Persist document record with metadata (move to archive)
        file_name = task.get('file_name') or 'document.pdf'
        temp_path = Config.TEMP_DIR / task_id
        base_name = ''.join(ch for ch in os.path.splitext(file_name)[0] if ch not in '\\/:*?"<>|').strip() or f'document-{task_id[:8]}'
        target_name = base_name + '.pdf'
        dest = Config.ARCHIVE_DIR / target_name
        if dest.exists():
            target_name = base_name + '-' + datetime.utcnow().strftime('%Y%m%d-%H%M%S') + '.pdf'
            dest = Config.ARCHIVE_DIR / target_name
        try:
            import shutil
            shutil.move(str(temp_path), str(dest))
        except Exception:
            try:
                import shutil
                shutil.copyfile(str(temp_path), str(dest))
            except Exception:
                return JSONResponse({"error": "Archivierung fehlgeschlagen"}, status_code=500)
        with get_db() as db:
            doc = Document(
                order_id=order_id,
                file_name=file_name,
                path=str(dest),
                template_name=task.get('template_name'),
                customer_id=task.get('customer_id'),
                user_id=(user.get('id') if user.get('id') is not None else None),
                fields_json=json.dumps(task.get('fields') or {}, ensure_ascii=False),
                task_id=task_id,
            )
            db.add(doc)
            db.flush(); db.refresh(doc)
        return {"id": doc.id, "message": "Dokument gespeichert"}

@app.post("/customers")
def create_customer(
    payload: dict = Body(...),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Create a new customer"""
    name = (payload.get("name") or "").strip()
    fields = payload.get("fields") or {}
    
    if not name:
        return JSONResponse({"error": "Name erforderlich"}, status_code=400)
    
    with get_db() as db:
        customer = Customer(
            name=name,
            fields_json=json.dumps(fields, ensure_ascii=False)
        )
        db.add(customer)
        db.flush()
        db.refresh(customer)
        
        return {
            "id": customer.id,
            "name": customer.name,
            "fields": fields
        }

@app.put("/customers/{customer_id}")
def update_customer(
    customer_id: int,
    payload: dict = Body(...),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Update customer"""
    with get_db() as db:
        customer = db.get(Customer, customer_id)
        if not customer:
            return JSONResponse({"error": "not found"}, status_code=404)
        
        if "name" in payload:
            customer.name = payload["name"] or customer.name
        
        if "fields" in payload:
            customer.fields_json = json.dumps(
                payload["fields"] or {},
                ensure_ascii=False
            )
        
        db.add(customer)
    
    return {"message": "ok"}

@app.delete("/customers/{customer_id}")
def delete_customer(customer_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    """Delete customer"""
    with get_db() as db:
        customer = db.get(Customer, customer_id)
        if not customer:
            return JSONResponse({"error": "not found"}, status_code=404)
        db.delete(customer)
    
    return {"message": "deleted"}

# ==================== Routes: Admin Users ====================
@app.get("/admin/users")
def admin_list_users(admin: Dict[str, Any] = Depends(admin_required)):
    """Admin: List all users"""
    with get_db() as db:
        users = db.exec(select(User)).all()
        return [
            {
                "id": u.id,
                "username": u.username,
                "is_admin": u.is_admin,
                "is_active": u.is_active,
                "twofa_enabled": bool(u.twofa_enabled),
            }
            for u in users
        ]

@app.post("/admin/users")
def admin_create_user(
    payload: dict = Body(...),
    admin: Dict[str, Any] = Depends(admin_required)
):
    """Admin: Create new user"""
    username = (payload.get('username') or payload.get('email') or '').strip()
    if not username:
        return JSONResponse({"error": "Benutzername erforderlich"}, status_code=400)
    
    password = payload.get('password') or secrets.token_urlsafe(8)
    is_admin = bool(payload.get('is_admin'))
    
    with get_db() as db:
        user = User(
            username=username,
            hashed_password=hash_password(password),
            is_admin=is_admin
        )
        db.add(user)
        db.flush()
        db.refresh(user)
        
        return {
            "id": user.id,
            "username": user.username,
            "temp_password": password
        }

@app.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: int, admin: Dict[str, Any] = Depends(admin_required)):
    """Admin: Delete user and their sessions"""
    with get_db() as db:
        user = db.get(User, user_id)
        if not user:
            return JSONResponse({"error": "User not found"}, status_code=404)

        # Delete all user tokens
        tokens = db.exec(select(AuthToken).where(AuthToken.user_id == user_id)).all()
        for tok in tokens:
            db.delete(tok)

        db.delete(user)
    
    return {"message": "deleted"}

@app.post("/admin/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: int,
    payload: dict = Body(...),
    admin: Dict[str, Any] = Depends(admin_required)
):
    """Admin: Reset user password"""
    password = payload.get('password') if payload else None
    if not password:
        return JSONResponse({"error": "Password required"}, status_code=400)
    
    with get_db() as db:
        user = db.get(User, user_id)
        if not user:
            return JSONResponse({"error": "User not found"}, status_code=404)
        
        user.hashed_password = hash_password(str(password))
        db.add(user)
        revoke_tokens_for_user(db, user_id)
    
    return {"message": "Password set"}


@app.post("/admin/users/{user_id}/reset-2fa")
def admin_reset_2fa(user_id: int, admin: Dict[str, Any] = Depends(admin_required)):
    """Admin: Remove 2FA binding for a user and revoke tokens."""
    with get_db() as db:
        user = db.get(User, user_id)
        if not user:
            return JSONResponse({"error": "User not found"}, status_code=404)
        user.totp_secret = None
        user.twofa_enabled = False
        db.add(user)
        revoke_tokens_for_user(db, user_id)
    return {"message": "2FA reset"}


@app.post("/admin/users/{user_id}/provision-2fa")
def admin_provision_2fa(user_id: int, admin: Dict[str, Any] = Depends(admin_required)):
    """Admin: Generate a new TOTP secret and enable 2FA. Admin shows QR to user."""
    with get_db() as db:
        user = db.get(User, user_id)
        if not user:
            return JSONResponse({"error": "User not found"}, status_code=404)
        secret = generate_totp_secret()
        user.totp_secret = secret
        user.twofa_enabled = True  # Enable immediately - admin is responsible for showing QR to user
        db.add(user)
        revoke_tokens_for_user(db, user_id)  # Force re-login with OTP
        otpauth_url = build_otpauth_url(secret, user.username)
    return {
        "message": "2FA provisioned",
        "secret": secret,
        "otpauth_url": otpauth_url,
        "qr": qr_data_uri(otpauth_url),
    }

# ==================== Routes: Admin Settings ====================
@app.get("/admin/settings")
def admin_get_settings(admin: Dict[str, Any] = Depends(admin_required)):
    """Admin: Get all settings"""
    return {
        "session_lifetime_days": SettingsCache.get_int(
            "session_lifetime_days",
            Config.DEFAULT_SESSION_LIFETIME_DAYS
        ),
        "temp_retention_hours": SettingsCache.get_int(
            "temp_retention_hours",
            Config.DEFAULT_TEMP_RETENTION_HOURS
        ),
        "filename_pattern": SettingsCache.get("filename_pattern", "{template}-{customer}-{date}"),
    }

@app.post("/admin/settings")
def admin_set_settings(
    payload: dict = Body(...),
    admin: Dict[str, Any] = Depends(admin_required)
):
    """Admin: Update settings"""
    days = int(payload.get('session_lifetime_days', Config.DEFAULT_SESSION_LIFETIME_DAYS))
    retention = payload.get('temp_retention_hours')
    
    try:
        retention_i = int(retention) if retention is not None else None
    except (ValueError, TypeError):
        return JSONResponse(
            {"error": "temp_retention_hours muss eine Zahl sein"},
            status_code=400
        )
    
    with get_db() as db:
        # Session lifetime
        setting = db.get(Setting, 'session_lifetime_days')
        if setting:
            setting.value = str(days)
        else:
            db.add(Setting(key='session_lifetime_days', value=str(days)))
        
        # Temp retention
        if retention_i is not None:
            setting = db.get(Setting, 'temp_retention_hours')
            if setting:
                setting.value = str(retention_i)
            else:
                db.add(Setting(key='temp_retention_hours', value=str(retention_i)))
        # Filename pattern
        pattern = str(payload.get('filename_pattern') or "{template}-{customer}-{date}")
        setting = db.get(Setting, 'filename_pattern')
        if setting:
            setting.value = pattern
        else:
            db.add(Setting(key='filename_pattern', value=pattern))
    
    # Invalidate cache
    SettingsCache.invalidate()
    
    return {
        "session_lifetime_days": days,
        "temp_retention_hours": retention_i,
        "filename_pattern": pattern,
    }

@app.post("/admin/clear-temp")
def admin_clear_temp(admin: Dict[str, Any] = Depends(admin_required)):
    """Admin: Clear all temp files"""
    count = 0
    for file_path in Config.TEMP_DIR.iterdir():
        try:
            if file_path.is_file():
                file_path.unlink()
                count += 1
        except Exception:
            pass
    
    return {"deleted": count}

# ==================== PDF Generation ====================
class WordConverter:
    """Persistent Word-based DOCX->PDF converter for Windows (fastest path)."""
    _started = False
    _ready = False
    _lock = Lock()
    _queue: "queue.Queue[tuple[Path, Path, Dict[str, Any]]]" = queue.Queue()
    _worker: Optional[Thread] = None

    @classmethod
    def is_available(cls) -> bool:
        # Allow disabling via env var for troubleshooting
        if os.environ.get("DISABLE_WORD_CONVERTER"):
            return False
        return HAVE_WIN32

    @classmethod
    def start(cls):
        if not HAVE_WIN32:
            return
        with cls._lock:
            if cls._started:
                return
            cls._worker = Thread(target=cls._loop, name="word_converter", daemon=True)
            cls._worker.start()
            cls._started = True

    @classmethod
    def convert(cls, docx_path: Path, pdf_path: Path, timeout: int = 20) -> None:
        if not HAVE_WIN32:
            raise RuntimeError("WordConverter not available")
        cls.start()
        done_flag = {"err": None}
        # Queue request with feedback dict
        cls._queue.put((docx_path, pdf_path, done_flag))
        # Busy-wait loop with small sleeps and timeout
        start_ts = time.time()
        while True:
            if done_flag["err"] is not None:
                # Error string or True for success
                if done_flag["err"] is True:
                    return
                raise Exception(str(done_flag["err"]))
            if time.time() - start_ts > timeout:
                raise TimeoutError("Word conversion timeout")
            time.sleep(0.02)

    @classmethod
    def _loop(cls):
        app = None
        try:
            # Initialize COM for this thread
            pythoncom.CoInitialize()  # type: ignore
            # Keep a single Word instance for all conversions
            app = win32com.client.DispatchEx("Word.Application")  # type: ignore
            app.Visible = False
            app.DisplayAlerts = 0
            cls._ready = True
            while True:
                docx_path, pdf_path, done_flag = cls._queue.get()
                try:
                    # Open document
                    doc = app.Documents.Open(str(docx_path))
                    # Export as PDF (17 = wdExportFormatPDF)
                    # Use ExportAsFixedFormat for better reliability
                    doc.ExportAsFixedFormat(
                        OutputFileName=str(pdf_path),
                        ExportFormat=17,
                        OpenAfterExport=False,
                        OptimizeFor=0,
                        Range=0,
                        Item=0,
                        IncludeDocProps=True,
                        KeepIRM=True,
                        CreateBookmarks=0,
                        DocStructureTags=True,
                        BitmapMissingFonts=True,
                        UseISO19005_1=False,
                    )
                    doc.Close(False)
                    done_flag["err"] = True
                except Exception as e:
                    try:
                        # Ensure doc is closed if opened
                        doc.Close(False)  # type: ignore
                    except Exception:
                        pass
                    done_flag["err"] = str(e)
        except Exception as e:
            # If Word cannot start, mark converter unavailable for this session
            cls._ready = False
            try:
                print(f"[WordConverter] Failed to start Word: {e}")
            except Exception:
                pass
        finally:
            try:
                if app is not None:
                    app.Quit()
            except Exception:
                pass
            try:
                pythoncom.CoUninitialize()  # type: ignore
            except Exception:
                pass

class PDFGenerator:
    """Optimized PDF generation with thread pool and caching"""
    _executor: Optional[ThreadPoolExecutor] = None
    _template_cache: Dict[str, DocxTemplate] = {}
    _cache_lock = Lock()
    
    @classmethod
    def get_executor(cls) -> ThreadPoolExecutor:
        """Get or create thread pool executor"""
        if cls._executor is None:
            cls._executor = ThreadPoolExecutor(
                max_workers=Config.PDF_WORKER_THREADS,
                thread_name_prefix="pdf_worker"
            )
        return cls._executor
    
    @classmethod
    def get_cached_template(cls, template_path: Path) -> DocxTemplate:
        """Get cached DocxTemplate or load and cache it"""
        path_str = str(template_path)
        
        with cls._cache_lock:
            if path_str in cls._template_cache:
                # Return copy to avoid threading issues
                return DocxTemplate(template_path)
            
            # Load and cache (keep limited cache size)
            if len(cls._template_cache) >= Config.DOCX_RENDER_CACHE_SIZE:
                # Remove oldest entry
                cls._template_cache.pop(next(iter(cls._template_cache)))
            
            template = DocxTemplate(template_path)
            cls._template_cache[path_str] = template
            return DocxTemplate(template_path)
    
    @staticmethod
    def format_field_value(field: Dict[str, Any], value: Any) -> str:
        """Format field value based on type (optimized)"""
        field_type = field.get('type', 'text')
        
        if field_type == 'checkbox':
            return '☑' if value else '☐'
        
        if field_type == 'date' and value:
            try:
                # Fast date parsing
                if isinstance(value, str) and len(value) == 10:
                    parts = value.split('-')
                    if len(parts) == 3:
                        return f"{parts[2]}.{parts[1]}.{parts[0]}"
                dt = datetime.strptime(str(value), '%Y-%m-%d')
                return dt.strftime('%d.%m.%Y')
            except Exception:
                return str(value)
        
        return str(value) if value else ''
    
    @staticmethod
    def build_context(template_meta: Dict[str, Any], form_dict: Dict[str, Any]) -> Dict[str, str]:
        """Build rendering context from form data (optimized)"""
        # Pre-allocate dict with known size
        fields = template_meta.get('fields', [])
        context = {}
        
        # Batch process field values
        for field in fields:
            name = field['name']
            value = form_dict.get(name)
            context[name] = PDFGenerator.format_field_value(field, value)
        
        return context
    
    @classmethod
    def generate_docx(cls, template_path: Path, context: Dict[str, str], output_path: Path) -> None:
        """Generate DOCX from template (optimized with caching)"""
        # Use cached template loading
        doc = cls.get_cached_template(template_path)
        doc.render(context)
        doc.save(str(output_path))
    
    @staticmethod
    def convert_to_pdf_fast(docx_path: Path, pdf_path: Path) -> None:
        """Optimized DOCX to PDF conversion"""
        # Prefer persistent Word converter if available; fallback quickly on issues
        if WordConverter.is_available():
            try:
                WordConverter.convert(docx_path, pdf_path, timeout=8)
                return
            except Exception:
                # Fallback below
                pass

        # Fallback 1: Try LibreOffice (works on Linux/Windows without Word)
        try:
            result = subprocess.run([
                'libreoffice',
                '--headless',
                '--convert-to', 'pdf',
                '--outdir', str(pdf_path.parent),
                str(docx_path)
            ], capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and pdf_path.exists():
                return
        except Exception:
            pass

        # Fallback 2: docx2pdf (requires Microsoft Word)
        docx_str = str(docx_path)
        pdf_str = str(pdf_path)
        try:
            convert(docx_str, pdf_str)
        except Exception as e:
            err_str = str(e)
            # Last resort: try subprocess docx2pdf
            if 'not implemented' not in err_str.lower():
                cmd = [
                    sys.executable,
                    '-c',
                    f"from docx2pdf import convert; convert(r'{docx_str}', r'{pdf_str}')"
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    return
            raise Exception(f'PDF conversion failed (LibreOffice not available or docx2pdf not supported): {err_str}')
    
    @classmethod
    def worker(cls, task_id: str, template_docx: Path, template_meta: Dict[str, Any],
               form_dict: Dict[str, Any], display_name: str) -> None:
        """Optimized background worker for PDF generation"""
        temp_docx_path = None
        temp_pdf_path = Config.TEMP_DIR / task_id
        success = False
        
        try:
            TaskManager.update_task(task_id, status='processing', percent=10)
            
            # Fast context building
            context = cls.build_context(template_meta, form_dict)
            TaskManager.update_task(task_id, percent=20)
            
            # Generate DOCX with cached template
            temp_docx_path = Config.TEMP_DIR / f"{uuid.uuid4().hex}.docx"
            cls.generate_docx(template_docx, context, temp_docx_path)
            TaskManager.update_task(task_id, percent=50)
            
            # Fast PDF conversion
            cls.convert_to_pdf_fast(temp_docx_path, temp_pdf_path)
            TaskManager.update_task(task_id, percent=95)
            
            success = True
            TaskManager.update_task(task_id, percent=100, status='done', path=str(temp_pdf_path))
            
        except Exception as e:
            TaskManager.update_task(task_id, status='error', error=str(e))
        
        finally:
            # Fast cleanup
            if temp_docx_path:
                try:
                    temp_docx_path.unlink(missing_ok=True)
                except Exception:
                    pass
            
            if not success and temp_pdf_path:
                try:
                    temp_pdf_path.unlink(missing_ok=True)
                except Exception:
                    pass

# ==================== Routes: PDF Generation ====================
@app.post("/generate/{template_name}")
async def generate_pdf(
    template_name: str,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Generate PDF from template"""
    template_docx = TemplateHelper.get_docx_path(template_name)
    
    if not template_docx.exists():
        return JSONResponse({"error": "Template nicht gefunden"}, status_code=404)
    
    template_meta = TemplateHelper.load_template_meta(template_name)
    
    # Get form data
    form = await request.form()
    form_dict = {k: form.get(k) for k in form.keys()}
    
    # Merge customer fields if provided
    customer_id = form.get('customerId')
    customer_name: Optional[str] = None
    if customer_id:
        try:
            cid = int(str(customer_id))
            with get_db() as db:
                customer = db.get(Customer, cid)
                if customer:
                    customer_name = customer.name
                    customer_fields = JSONHelper.parse(customer.fields_json, {})
                    for key, value in customer_fields.items():
                        form_dict.setdefault(key, value)
        except (ValueError, TypeError):
            pass
    
    # Get file name
    file_name = form.get('pdfFileName')
    if not file_name:
        # Build from pattern
        pattern = SettingsCache.get('filename_pattern', '{template}-{customer}-{date}')
        # Safe placeholders
        date_str = datetime.utcnow().strftime('%Y-%m-%d')
        tpl = template_name.replace('.docx','')
        cust = (customer_name or '').strip()
        try:
            file_name = pattern.format(template=tpl, customer=cust, date=date_str)
        except Exception:
            file_name = f"{tpl}-{date_str}"
        # sanitize filename
        file_name = ''.join(ch for ch in file_name if ch not in '\\/:*?"<>|').strip()
        if not file_name:
            file_name = tpl
    
    # Create task
    task_id = f"{uuid.uuid4().hex}.pdf"
    TaskManager.create_task(task_id, {
        'status': 'queued',
        'percent': 0,
        'file_name': f"{file_name}.pdf",
        'timestamp': time.time(),
        'template_name': template_name,
        'customer_id': (int(str(customer_id)) if customer_id else None),
        'user_id': user.get('id'),
        'fields': form_dict,
        'order_id': None,
        'task_id': task_id,
    })
    
    # Submit to optimized thread pool
    executor = PDFGenerator.get_executor()
    executor.submit(
        PDFGenerator.worker,
        task_id,
        template_docx,
        template_meta,
        form_dict,
        f"{file_name}.pdf"
    )
    
    return {"id": task_id, "file_name": f"{file_name}.pdf"}

@app.get('/generate/{task_id}/status')
def get_generate_status(task_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Get PDF generation status"""
    task = TaskManager.get_task(task_id)
    if not task:
        return JSONResponse({"error": "Nicht gefunden"}, status_code=404)
    
    return {
        "id": task_id,
        "status": task.get('status', 'queued'),
        "percent": task.get('percent', 0),
        "file_name": task.get('file_name'),
        "error": task.get('error')
    }

@app.get("/generated/{file_name}")
def get_generated(
    file_name: str,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Download or preview generated PDF"""
    safe_name = os.path.basename(file_name)
    file_path = Config.TEMP_DIR / safe_name
    
    if not file_path.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)
    
    # Download vs inline preview
    if request.query_params.get('download'):
        f = open(file_path, 'rb')
        return StreamingResponse(f, media_type="application/pdf", headers={
            "Content-Disposition": f"attachment; filename=\"{safe_name}\""
        })
    # Default: inline
    f = open(file_path, 'rb')
    return StreamingResponse(f, media_type="application/pdf", headers={
        "Content-Disposition": f"inline; filename=\"{safe_name}\""
    })

@app.delete("/generated/{file_name}")
def delete_generated(file_name: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Delete generated PDF"""
    safe_name = os.path.basename(file_name)
    file_path = Config.TEMP_DIR / safe_name
    
    if not file_path.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)
    
    file_path.unlink()
    return {"message": "deleted"}

# ==================== Routes: Documents ====================
@app.post("/documents/finalize")
def documents_finalize(payload: dict = Body(...), user: Dict[str, Any] = Depends(get_current_user)):
    """Finalize a general generation task into a Document record."""
    task_id = (payload or {}).get('task_id')
    if not task_id:
        return JSONResponse({"error": "task_id erforderlich"}, status_code=400)
    task = TaskManager.get_task(task_id)
    if not task or task.get('status') != 'done':
        return JSONResponse({"error": "Task nicht abgeschlossen"}, status_code=400)
    file_name = task.get('file_name') or 'document.pdf'
    temp_path = Config.TEMP_DIR / task_id
    # Build archive destination with unique name
    base_name = ''.join(ch for ch in os.path.splitext(file_name)[0] if ch not in '\\/:*?"<>|').strip() or f'document-{task_id[:8]}'
    target_name = base_name + '.pdf'
    dest = Config.ARCHIVE_DIR / target_name
    if dest.exists():
        target_name = base_name + '-' + datetime.utcnow().strftime('%Y%m%d-%H%M%S') + '.pdf'
        dest = Config.ARCHIVE_DIR / target_name
    try:
        import shutil
        shutil.move(str(temp_path), str(dest))
    except Exception:
        # Fallback: if move fails but file exists at temp, try copy
        try:
            import shutil
            shutil.copyfile(str(temp_path), str(dest))
        except Exception:
            return JSONResponse({"error": "Archivierung fehlgeschlagen"}, status_code=500)
    template_name = task.get('template_name')
    customer_id = task.get('customer_id')
    user_id = task.get('user_id') or user.get('id')
    fields = task.get('fields') or {}
    with get_db() as db:
        doc = Document(
            order_id=None,
            file_name=file_name,
            path=str(dest),
            template_name=str(template_name) if template_name else None,
            customer_id=customer_id,
            user_id=(int(user_id) if user_id is not None else None),
            fields_json=json.dumps(fields, ensure_ascii=False),
            task_id=task_id,
        )
        db.add(doc)
        db.flush(); db.refresh(doc)
        return {"id": doc.id}

@app.get("/documents")
def documents_list(
    q: Optional[str] = None,
    customer_id: Optional[int] = None,
    user_id: Optional[int] = None,
    template_name: Optional[str] = None,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """List documents with optional filters."""
    with get_db() as db:
        docs = db.exec(select(Document).order_by(text("generated_at DESC"))).all()
        ql = (q or '').strip().lower()
        out = []
        for d in docs:
            if customer_id and d.customer_id != customer_id:
                continue
            if user_id and d.user_id != user_id:
                continue
            if template_name and d.template_name != template_name:
                continue
            if ql:
                hay = (
                    (d.file_name or '').lower() + ' ' +
                    (d.template_name or '').lower()
                )
                if ql not in hay:
                    continue
            cust_name = None
            usr_name = None
            if d.customer_id:
                c = db.get(Customer, d.customer_id)
                cust_name = c.name if c else None
            if d.user_id:
                u = db.get(User, d.user_id)
                usr_name = u.username if u else None
            out.append({
                'id': d.id,
                'code': f"PDF-{d.id}" if d.id is not None else None,
                'file_name': d.file_name,
                'template_name': d.template_name,
                'customer_id': d.customer_id,
                'customer_name': cust_name,
                'user_id': d.user_id,
                'username': usr_name,
                'generated_at': d.generated_at.isoformat(),
                'printed_at': d.printed_at.isoformat() if d.printed_at else None,
                'order_id': d.order_id,
                'task_id': d.task_id,
            })
        return out

@app.get("/documents/{doc_id}")
def documents_get(doc_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    with get_db() as db:
        d = db.get(Document, doc_id)
        if not d:
            return JSONResponse({"error": "Nicht gefunden"}, status_code=404)
        cust = db.get(Customer, d.customer_id) if d.customer_id else None
        usr = db.get(User, d.user_id) if d.user_id else None
        fields = JSONHelper.parse(d.fields_json or '{}', {})
        return {
            'id': d.id,
            'code': f"PDF-{d.id}" if d.id is not None else None,
            'file_name': d.file_name,
            'path': d.path,
            'template_name': d.template_name,
            'customer': {'id': cust.id, 'name': cust.name} if cust else None,
            'user': {'id': usr.id, 'username': usr.username} if usr else None,
            'fields': fields,
            'generated_at': d.generated_at.isoformat(),
            'printed_at': d.printed_at.isoformat() if d.printed_at else None,
            'order_id': d.order_id,
            'task_id': d.task_id,
            'preview_url': f"/static/pdfviewer.html?file=/documents/{d.id}/file" if d.id is not None else None,
        }

@app.get("/documents/{doc_id}/file")
def documents_file(doc_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    with get_db() as db:
        d = db.get(Document, doc_id)
        if not d or not d.path:
            return JSONResponse({"error": "Nicht gefunden"}, status_code=404)
        file_path = Path(d.path)
        if not file_path.exists():
            return JSONResponse({"error": "Datei nicht gefunden"}, status_code=404)
        display_name = d.file_name or file_path.name
        f = open(file_path, 'rb')
        return StreamingResponse(f, media_type="application/pdf", headers={
            "Content-Disposition": f"inline; filename=\"{display_name}\""
        })

@app.delete("/documents/{doc_id}")
def documents_delete(doc_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    with get_db() as db:
        d = db.get(Document, doc_id)
        if not d:
            return JSONResponse({"error": "Nicht gefunden"}, status_code=404)
        # Try to remove file
        try:
            if d.path and Path(d.path).exists():
                Path(d.path).unlink()
        except Exception:
            pass
        db.delete(d)
    return {"message": "Dokument gelöscht"}

@app.get("/users")
def list_users_simple(user: Dict[str, Any] = Depends(get_current_user)):
    """Non-admin: list users for filters."""
    with get_db() as db:
        users = db.exec(select(User)).all()
        return [{'id': u.id, 'username': u.username} for u in users]
