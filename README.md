# Vorlagen Editor

A FastAPI-based document generation system that renders Word templates (DOCX) with user-provided data and converts them to PDFs. Perfect for automated form filling, invoice generation, contract templating, and bulk document production.

## Features

- 📄 **DOCX Template Management** — Upload and manage Word templates with JSON-based field definitions
- 🔄 **Batch Document Generation** — Render templates with form data, convert to PDF automatically
- 👥 **Customer Management** — Store reusable customer data for quick document generation
- 📦 **Order-Based Workflows** — Create orders with templates, fill fields, and generate documents
- 🔐 **Authentication & 2FA** — Secure login with optional TOTP (Google Authenticator) support
- 🎯 **Admin Panel** — Manage users, settings, standard fields, and file retention policies
- 🐳 **Docker Ready** — Production-ready Docker deployment with volume persistence
- 📊 **Background Processing** — Threaded PDF generation with progress tracking

## System Architecture

```
Browser/Client
    ↓
FastAPI Application (app.py)
    ├── Authentication (tokens + 2FA)
    ├── Template Management
    ├── Customer & Order Management
    └── Background PDF Generation
          ├── DocxTemplate → DOCX Rendering
          └── Word COM / docx2pdf → PDF Conversion
                ↓
          Database (SQLite)
          + Archives (Filesystem)
```

## Prerequisites

### For Docker Deployment (Recommended)
- **Docker** 20.10+
- **Docker Compose** 1.29+
- **Linux system** (Ubuntu 20.04+ recommended)
- **2GB RAM** minimum, 4GB+ recommended
- **10GB disk space** (adjust based on document archive size)

### For Local Development
- **Python** 3.11+
- **pip** package manager
- **Windows** (for Word COM) or **Linux with LibreOffice** (for docx2pdf)

## Quick Start (Docker)

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/vorlagen-editor.git
cd vorlagen-editor
```

### 2. Configure Environment (Optional)

Copy the example environment file:

```bash
cp .env.example .env
```

Edit `.env` if you need custom settings (optional—defaults work for most setups):

```bash
nano .env
```

### 3. Build and Start with Docker Compose

```bash
docker-compose -f docker/docker-compose.yml up --build -d
```

This command:
- Builds the Docker image from the Dockerfile
- Creates and starts the container in background (`-d` flag)
- Exposes the app on `http://localhost:8000`
- Mounts persistent volumes for database, templates, and archives

### 4. Access the Application

Open your browser and navigate to:

```
http://localhost:8000
```

**Default credentials:**
- Username: `admin`
- Password: `admin`

⚠️ **Change the default password immediately in production!**

### 5. Stop the Application

```bash
docker-compose -f docker/docker-compose.yml down
```

To also remove persistent data volumes:

```bash
docker-compose -f docker/docker-compose.yml down -v
```

## Detailed Linux Deployment Guide

### Step 1: Install Docker and Docker Compose

**Ubuntu/Debian:**

```bash
# Update package manager
sudo apt-get update

# Install Docker
sudo apt-get install -y docker.io docker-compose

# Start Docker daemon
sudo systemctl start docker
sudo systemctl enable docker

# Add your user to docker group (avoid sudo)
sudo usermod -aG docker $USER
newgrp docker

# Verify installation
docker --version
docker-compose --version
```

### Step 2: Clone Repository to Server

```bash
# Navigate to your preferred directory (e.g., /opt or /home/username)
cd /opt

# Clone the repository
git clone https://github.com/yourusername/vorlagen-editor.git
cd vorlagen-editor

# Verify directory structure
ls -la
# Expected: app.py, requirements.txt, docker/, static/, templates/, .gitignore, docker-compose.yml
```

### Step 3: Create Data Directory with Proper Permissions

```bash
# Create directory for persistent data (outside container)
mkdir -p /opt/vorlagen-data

# Set permissions (docker user: 1000)
sudo chown 1000:1000 /opt/vorlagen-data
chmod 755 /opt/vorlagen-data
```

### Step 4: Configure Environment Variables

```bash
# Copy environment template
cp .env.example .env

# Edit for your system
nano .env
```

**Example production `.env` configuration:**

```bash
DATABASE_URL=sqlite:////app/data/data.db
TEMPLATE_DIR=/app/templates
TEMP_DIR=/app/temp
ARCHIVE_DIR=/app/archive
SESSION_LIFETIME_DAYS=5
TEMP_RETENTION_HOURS=48
# DISABLE_WORD_CONVERTER=1  # Uncomment on non-Windows systems if needed
```

### Step 5: Update Docker Compose (Optional)

Modify `docker/docker-compose.yml` for production:

```yaml
version: '3.8'

services:
  vorlagen-app:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    container_name: vorlagen-generator
    ports:
      - "8000:8000"  # or "127.0.0.1:8000:8000" for local-only
    environment:
      - DATABASE_URL=sqlite:////app/data/data.db
      - TEMPLATE_DIR=/app/templates
      - TEMP_DIR=/app/temp
      - ARCHIVE_DIR=/app/archive
      - SESSION_LIFETIME_DAYS=5
      - TEMP_RETENTION_HOURS=48
    volumes:
      - /opt/vorlagen-data:/app/data
      - vorlagen-archive:/app/archive
      - vorlagen-temp:/app/temp
      - vorlagen-templates:/app/templates
      - vorlagen-static:/app/static
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    networks:
      - vorlagen-network

volumes:
  vorlagen-archive:
    driver: local
  vorlagen-temp:
    driver: local
  vorlagen-templates:
    driver: local
  vorlagen-static:
    driver: local

networks:
  vorlagen-network:
    driver: bridge
```

### Step 6: Build and Start

```bash
# Build the Docker image
docker-compose -f docker/docker-compose.yml build

# Start the container in background
docker-compose -f docker/docker-compose.yml up -d

# Check container status
docker-compose -f docker/docker-compose.yml ps

# View logs
docker-compose -f docker/docker-compose.yml logs -f vorlagen-app

# Stop logs (Ctrl+C)
```

### Step 7: Verify Deployment

```bash
# Check if container is running
docker ps | grep vorlagen

# Test the API
curl http://localhost:8000/

# View application logs
docker-compose -f docker/docker-compose.yml logs vorlagen-app
```

## CLI Usage Examples

### 1. Login and Get Access Token

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=admin"

# Response:
# {
#   "access_token": "your-token-here",
#   "expires_at": "2026-01-16T10:30:00",
#   "is_admin": true,
#   "twofa_enabled": false
# }
```

### 2. Upload a Template

```bash
TOKEN="your-access-token"

curl -X POST http://localhost:8000/templates/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/template.docx" \
  -F "add_standard_fields=true"
```

### 3. List Templates

```bash
TOKEN="your-access-token"

curl -X GET http://localhost:8000/templates \
  -H "Authorization: Bearer $TOKEN"
```

### 4. Create a Customer

```bash
TOKEN="your-access-token"

curl -X POST http://localhost:8000/customers \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Acme Corp",
    "fields": {
      "FIRMENNAME": "Acme Corporation",
      "ADRESSE": "123 Main St",
      "PLZ": "10115",
      "ORT": "Berlin"
    }
  }'
```

### 5. Generate PDF from Template

```bash
TOKEN="your-access-token"

curl -X POST http://localhost:8000/generate/template.docx \
  -H "Authorization: Bearer $TOKEN" \
  -F "field1=value1" \
  -F "field2=value2" \
  -F "customerId=1"

# Response:
# {
#   "id": "abc123def456.pdf",
#   "file_name": "document.pdf"
# }
```

### 6. Check PDF Generation Status

```bash
TOKEN="your-access-token"
TASK_ID="abc123def456.pdf"

curl -X GET http://localhost:8000/generate/$TASK_ID/status \
  -H "Authorization: Bearer $TOKEN"

# Response:
# {
#   "id": "abc123def456.pdf",
#   "status": "done",
#   "percent": 100,
#   "file_name": "document.pdf"
# }
```

### 7. Download Generated PDF

```bash
curl -X GET "http://localhost:8000/generated/document.pdf?download=true" \
  -H "Authorization: Bearer $TOKEN" \
  -o downloaded_document.pdf
```

## Configuration & Admin Panel

### Change Admin Password

1. Open `http://localhost:8000` in browser
2. Login with `admin` / `admin`
3. Access Admin Panel (if admin user)
4. Go to **Users** → Edit **admin** user → Set new password

### Configure Settings

In Admin Panel:

- **Session Lifetime** — How long auth tokens are valid (default: 5 days)
- **Temp Retention** — How long temporary files are kept (default: 48 hours)
- **Filename Pattern** — Template for generated PDF names (default: `{template}-{customer}-{date}`)

### Add New Users

1. Admin Panel → **Users** → **Create User**
2. Username and auto-generated password
3. Users are auto-provisioned with 2FA on first login

### Manage Standard Fields

Standard fields are default customer data fields (FIRMENNAME, ADRESSE, PLZ, ORT). Customize in:

Admin Panel → **Standard Fields** → Add/Edit/Delete

## Persistence & Data

### Docker Volumes

Data is stored in Docker volumes for automatic persistence:

- **vorlagen-data** — SQLite database
- **vorlagen-archive** — Generated PDFs (permanent)
- **vorlagen-temp** — Temporary files (auto-cleaned)
- **vorlagen-templates** — DOCX template files
- **vorlagen-static** — Static frontend files

### Backup Database

```bash
# Copy database from container
docker cp vorlagen-generator:/app/data/data.db ./data.db

# Or from volume
docker run --rm -v vorlagen-data:/data -v $(pwd):/backup \
  alpine cp /data/data.db /backup/data.db
```

### Restore Database

```bash
docker cp ./data.db vorlagen-generator:/app/data/data.db

# Restart container
docker-compose -f docker/docker-compose.yml restart vorlagen-app
```

## Troubleshooting

### Container Won't Start

```bash
# View logs
docker-compose -f docker/docker-compose.yml logs vorlagen-app

# Check if port 8000 is in use
sudo lsof -i :8000

# Rebuild from scratch
docker-compose -f docker/docker-compose.yml down
docker system prune -a
docker-compose -f docker/docker-compose.yml up --build -d
```

### PDF Generation Fails

**Symptom:** Generation status shows `error`

**Solutions:**

1. Check logs:
   ```bash
   docker-compose -f docker/docker-compose.yml logs vorlagen-app
   ```

2. For LibreOffice conversion issues on Linux:
   ```bash
   # Ensure LibreOffice is installed in container
   docker-compose -f docker/docker-compose.yml exec vorlagen-app \
     bash -c "apt-get update && apt-get install -y libreoffice"
   ```

3. Force docx2pdf fallback:
   ```bash
   # Edit .env
   DISABLE_WORD_CONVERTER=1
   
   # Restart
   docker-compose -f docker/docker-compose.yml restart vorlagen-app
   ```

### Permissions Error on Linux

If you see permission errors for temp/archive folders:

```bash
# Fix ownership
sudo chown 1000:1000 /opt/vorlagen-data
sudo chmod 755 /opt/vorlagen-data
```

### Authentication Token Expired

Tokens expire after the configured `SESSION_LIFETIME_DAYS` (default 5 days). Login again to get a new token.

## Production Deployment

### Use Reverse Proxy (Nginx)

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Use SSL with Let's Encrypt

```bash
# Install Certbot
sudo apt-get install -y certbot python3-certbot-nginx

# Generate certificate
sudo certbot certonly --standalone -d yourdomain.com

# Update Nginx config to use SSL
sudo certbot --nginx -d yourdomain.com
```

### Monitor Container Health

```bash
# Check health status
docker-compose -f docker/docker-compose.yml ps

# View resource usage
docker stats vorlagen-generator

# Set up automatic restarts
# Already configured: restart: unless-stopped
```

### Database Backups

Set up cron job for automated backups:

```bash
# Add to crontab
0 2 * * * docker cp vorlagen-generator:/app/data/data.db /backups/data-$(date +\%Y\%m\%d).db
```

## Development Setup

### Run Locally (Without Docker)

```bash
# Clone repository
git clone https://github.com/yourusername/vorlagen-editor.git
cd vorlagen-editor

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run with Uvicorn
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

Access at `http://localhost:8000`

### Database Schema

Key tables:
- **User** — User accounts with hashed passwords
- **AuthToken** — Login tokens with expiry
- **Customer** — Customer data records
- **Order** — Document generation orders
- **OrderField** — Form fields for orders
- **Document** — Generated PDF records
- **Template** — (Metadata stored as JSON files)
- **StandardField** — Default customer fields
- **Setting** — Admin configuration

### Modifying Templates

1. Upload DOCX via web UI or API
2. Auto-generates `templates/template.json` with field definitions
3. Edit fields via Admin Panel or directly in JSON:

```json
{
  "fields": [
    {"name": "FIRMENNAME", "type": "text"},
    {"name": "DATUM", "type": "date"},
    {"name": "SIGNATUR", "type": "checkbox"}
  ]
}
```

## API Reference

See `.github/copilot-instructions.md` for detailed architecture and API patterns.

### Key Endpoints

- `POST /auth/login` — User login
- `GET /templates` — List templates
- `POST /templates/upload` — Upload DOCX template
- `POST /customers` — Create customer
- `GET /customers` — List customers
- `POST /generate/{template_name}` — Generate PDF
- `GET /generate/{task_id}/status` — Check generation status
- `GET /admin/users` — List users (admin only)
- `GET /admin/settings` — Get admin settings

## License

[Add your license here]

## Support

For issues, questions, or contributions:
- 📧 Create an issue in this repository
- 🐛 Report bugs with steps to reproduce
- 💡 Suggest features with use cases

## Contributing

Pull requests welcome! Please:
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request
