# Vorlagen Editor

FastAPI-based document generation system. Renders Word templates (DOCX) with form data and converts to PDFs. Supports template management, customer data, orders, authentication with 2FA, and background PDF generation.

## Quick Deploy on Debian/Ubuntu with Docker

### Prerequisites
- Docker & Docker Compose installed
- 1GB+ RAM
- 2GB+ disk space

### 1. Install Docker (Debian/Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER
newgrp docker
```

### 2. Clone & Deploy

```bash
git clone https://github.com/santiagotoro2023/m000-st-template-editor.git
cd m000-st-template-editor

# Build and start
docker compose -f docker/docker-compose.yml up --build -d

# Check status
docker compose -f docker/docker-compose.yml ps
```

### 3. Access the Application

```
http://localhost:8000
```

**Default login:**
- Username: `admin`
- Password: `admin`

⚠️ Change the default password immediately!

### 4. Stop & Cleanup

```bash
# Stop and remove container (data is ephemeral)
docker compose -f docker/docker-compose.yml down
```

**Note:** Data is stored inside the container and will be lost when the container stops. This is fine for testing and development.

## Usage Examples

### Login and Get Token

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=admin"
```

### Upload Template

```bash
TOKEN="your-token"
curl -X POST http://localhost:8000/templates/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@template.docx"
```

### Create Customer

```bash
TOKEN="your-token"
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

### Generate PDF

```bash
TOKEN="your-token"
curl -X POST http://localhost:8000/generate/template.docx \
  -H "Authorization: Bearer $TOKEN" \
  -F "field1=value1" \
  -F "field2=value2"
```

## Key Features

- ✅ DOCX template management with JSON field definitions
- ✅ Automatic PDF generation from templates
- ✅ Customer data management
- ✅ Order-based workflows
- ✅ Authentication & 2FA (TOTP/Google Authenticator)
- ✅ Admin panel for users, settings, standard fields
- ✅ SQLite database with persistent Docker volumes
- ✅ Threaded background PDF processing

## Configuration

Edit `.env` to customize:

```bash
DATABASE_URL=sqlite:////app/data/data.db
TEMPLATE_DIR=/app/templates
TEMP_DIR=/app/temp
ARCHIVE_DIR=/app/archive
SESSION_LIFETIME_DAYS=5
TEMP_RETENTION_HOURS=48
```

## Admin Panel

Access at `http://localhost:8000` after login:
- **Users** — Create/manage user accounts
- **Settings** — Configure session lifetime, temp file retention, filename patterns
- **Standard Fields** — Define default customer data fields
- **Clear Temp** — Remove temporary generated files

## Troubleshooting

### Build Fails

```bash
# Clean and rebuild
docker system prune -a
docker compose -f docker/docker-compose.yml up --build -d
```

### View Logs

```bash
docker compose -f docker/docker-compose.yml logs -f vorlagen-app
```

### Port Already in Use

```bash
# Find process using port 8000
sudo lsof -i :8000

# Or use different port in docker-compose.yml
# Change "8000:8000" to "8001:8000"
```

### PDF Generation Fails

Check logs for LibreOffice/conversion errors:

```bash
docker compose -f docker/docker-compose.yml logs vorlagen-app | grep -i error
```

## Production Deployment

### With Nginx Reverse Proxy

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx

# Create nginx config
sudo nano /etc/nginx/sites-available/vorlagen

# Content:
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

# Enable site
sudo ln -s /etc/nginx/sites-available/vorlagen /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# Add SSL
sudo certbot --nginx -d yourdomain.com
```

## Data Storage

Data is stored **inside the container only**:
- Database resets when container restarts
- Ideal for testing, demos, and ephemeral deployments
- No manual backups needed
- Clean restart: `docker compose -f docker/docker-compose.yml down && docker compose -f docker/docker-compose.yml up --build -d`

## System Architecture

```
Browser
  ↓
FastAPI (app.py)
  ├── Auth (tokens + 2FA)
  ├── Template Management
  ├── Customer & Order Management
  └── Background PDF Worker
      ├── DocxTemplate rendering
      ├── LibreOffice/docx2pdf conversion
      └── Archive storage
        ↓
Database (SQLite) + Filesystem
```

## Support

See `.github/copilot-instructions.md` for detailed architecture and developer patterns.

For issues:
- Check logs: `docker compose -f docker/docker-compose.yml logs vorlagen-app`
- Create issue in GitHub repository
- Verify Docker is running: `docker ps`

## License

MIT
