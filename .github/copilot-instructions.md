# Copilot Instructions for Vorlagen Editor

## Architecture Overview

**Vorlagen Editor** is a FastAPI-based document generation system that renders Word templates (DOCX) with user-provided data and converts them to PDFs. The system is built around three core workflows:

1. **Template Management**: Upload/manage DOCX templates with metadata (field definitions) stored as JSON
2. **Document Generation**: Render templates with form data via background workers, supporting both standalone and order-based generation
3. **Authentication & Admin**: Token-based auth with optional 2FA (TOTP), user/settings management

### Key Components

- **app.py** (1958 lines): Single-file monolith containing models, routes, and business logic
  - **Database**: SQLModel/SQLAlchemy with SQLite; context manager pattern via `get_db()`
  - **Background Processing**: ThreadPoolExecutor for async PDF generation, in-memory TaskManager
  - **PDF Generation**: DocxTemplate → DOCX rendering, then Word COM (Windows) or fallback docx2pdf conversion
  - **File Cleanup**: Background daemon thread for temp file retention policies

- **static/**: Frontend HTML/CSS (index.html is main UI)
- **templates/**: DOCX template files + JSON metadata
- **temp/**: Transient generated files, cleared periodically
- **archive/**: Final PDF documents

---

## Critical Developer Patterns

### Database & Session Management
```python
# Always use context manager - auto commits/rollbacks
with get_db() as db:
    user = db.exec(select(User).where(...)).first()
    db.add(new_object)  # Auto-commits on exit
```

**Key Tables**: User, AuthToken, Customer, Order, OrderField, Document, StandardField, Setting
- Never use `.get()` on relationships; use `select()` + `where()` to avoid cached stale data
- All models use `SQLModel` (inherits from Pydantic + SQLAlchemy)

### Authentication
- **Tokens**: SHA256-hashed (not reversible), stored in AuthToken table with expiry
- **Setup Tokens** (scope="setup"): Short-lived (10 min), used for 2FA provisioning
- **Access Tokens** (scope="access"): Session tokens, configurable lifetime (default 5 days)
- **Bearer vs Cookie**: Routes accept both `Authorization: Bearer` header and `ve_access`/`ve_setup` cookies
- **2FA**: TOTP (Time-based OTP) via PyOTP; non-admin users are auto-provisioned on first login

**Dependency Functions**:
- `get_current_user()` — validates access token, returns user dict
- `get_setup_context()` — validates setup token (for 2FA)
- `admin_required()` — wraps `get_current_user`, raises 403 if not admin

### PDF Generation Workflow
1. **Request → Task**: Form data → TaskManager creates task with unique UUID-based ID
2. **Background Processing**: Submits to ThreadPoolExecutor with `PDFGenerator.worker()`
3. **Rendering**: DocxTemplate caches templates, renders context dict → temporary DOCX
4. **Conversion**: Prefers Word COM (Windows) via WordConverter singleton, falls back to docx2pdf
5. **Storage**: Temp PDF moved to archive on finalization; Document record persists metadata

**Task States**: `queued` → `processing` → `done` or `error`

### File Naming & Cleanup
- **Patterns**: Configurable filename template `{template}-{customer}-{date}` (admin setting)
- **Temp Retention**: Files deleted after X hours (default 48); configurable cleanup loop every 1 hour
- **Archive**: Final PDFs stored with checksums, timestamped if duplicate names

### Settings Cache
- `SettingsCache`: In-memory cache (60s TTL) for frequent DB lookups (session lifetime, temp retention, filename patterns)
- Call `SettingsCache.invalidate(key)` after admin updates

### Standard Fields
- Pre-defined customer data fields (FIRMENNAME, ADRESSE, PLZ, ORT by default)
- Stored in StandardField table, used to populate new template field lists

---

## Common Implementation Tasks

### Adding a New Route
1. Add model to database if needed (inherit from SQLModel)
2. Create route function with `@app.post()`/`@app.get()`, depend on `get_current_user` or `admin_required`
3. Use `with get_db() as db` for queries
4. Return JSONResponse or dict (FastAPI auto-serializes)

### Adding Admin Settings
1. Add Setting record in startup or via admin endpoint
2. Cache with `SettingsCache.get()` or `SettingsCache.get_int()`
3. Call `SettingsCache.invalidate()` after POST to force reload

### Modifying Template Metadata
- JSON stored in `templates/{template_name}.json` (parallel to DOCX file)
- Use `TemplateHelper.load_template_meta()` / `save_template_meta()` for safety

### Background Tasks
- Use TaskManager for tracking; check `status` + `percent` for polling UI
- Workers submit to `PDFGenerator.get_executor()` (ThreadPoolExecutor, max 3 workers by default)
- Critical: Update task status in worker before/after long operations

---

## Testing & Debugging

### Local Development
- Run with `python app.py` (no explicit server command; needs Uvicorn from requirements.txt)
- Or use Docker: `docker-compose -f docker/docker-compose.yml up --build`
- Default user: `admin` / `admin` (created on first startup)

### Common Issues
- **Word COM Errors**: Set `DISABLE_WORD_CONVERTER=1` to force docx2pdf fallback
- **PDF Generation Hangs**: Check PDFGenerator thread pool; max 3 concurrent conversions
- **Token Expired**: Session tokens default to 5 days; adjust `session_lifetime_days` setting
- **Template Not Found**: Verify DOCX + JSON exist in `templates/` directory

### Database Schema
- Auto-migrations on startup (ALTER TABLE for missing columns like `username`, `twofa_secret`, etc.)
- SQLite with NullPool (one connection per request, thread-safe)

---

## Architecture Decisions

**Why monolithic?** Single app.py reduces deployment complexity for a template editor; models, routes, and helpers coexist.

**Why ThreadPoolExecutor for PDF?** Async I/O has overhead for CPU-bound DOCX rendering; thread pool avoids context switching.

**Why Word COM (not pure Python)?** MS Word DOCX→PDF conversion is fastest + most compatible on Windows; fallback to docx2pdf for robustness.

**Why in-memory TaskManager?** PDFs generate quickly (seconds); in-process tracking avoids Redis overhead for this scale.

**Why Bearer tokens + cookies?** Headers for APIs, cookies for browser-based frontend; both decode the same way.

---

## File References for Key Concepts
- Database setup: [app.py#L55-L80](app.py#L55-L80)
- Auth token creation: [app.py#L330-L345](app.py#L330-L345)
- PDF generation worker: [app.py#L1560-L1600](app.py#L1560-L1600)
- Template rendering: [app.py#L1460-L1510](app.py#L1460-L1510)
- Admin routes: [app.py#L1100-L1200](app.py#L1100-L1200)
