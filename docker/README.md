# Vorlagen Generator - Docker Deployment Guide

## Quick Start (Local Development)

### Prerequisites
- Docker & Docker Compose installed on your machine
- [Download Docker Desktop](https://www.docker.com/products/docker-desktop)

### Run the Application

From the project root directory:

```bash
docker-compose -f docker/docker-compose.yml up --build
```

The application will be available at: **http://localhost:8000**

To run in background:
```bash
docker-compose -f docker/docker-compose.yml up -d --build
```

To stop:
```bash
docker-compose -f docker/docker-compose.yml down
```

## Data Persistence

All data is automatically persisted in Docker volumes:
- **vorlagen-data** - Database file (data.db)
- **vorlagen-archive** - Generated PDF archive
- **vorlagen-temp** - Temporary files
- **vorlagen-templates** - Template DOCX files
- **vorlagen-static** - Static frontend files

Volumes survive container restarts and are only removed with:
```bash
docker-compose -f docker/docker-compose.yml down -v
```

## Production Deployment

### Option 1: Deploy to Your Own Server

```bash
# On your Linux server
git clone <your-repo>
cd Vorlagen-Generator
docker-compose -f docker/docker-compose.yml up -d

# Check logs
docker-compose -f docker/docker-compose.yml logs -f vorlagen-app
```

### Option 2: Nginx Reverse Proxy (Production)

Create `/etc/nginx/sites-available/vorlagen`:

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }

    location /static/ {
        alias /path/to/Vorlagen-Generator/static/;
        expires 7d;
    }
}
```

Enable it:
```bash
sudo ln -s /etc/nginx/sites-available/vorlagen /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### Option 3: SSL with Let's Encrypt

```bash
# Install certbot
sudo apt-get install certbot python3-certbot-nginx

# Get certificate
sudo certbot --nginx -d yourdomain.com

# Auto-renewal is automatic
```

### Option 4: Deploy to Cloud Platforms

#### Railway.app (Recommended - 5 minutes)
1. Push to GitHub
2. Connect repo to Railway.app
3. Set environment variables
4. Deploy!

#### Render.com
1. Connect GitHub repo
2. Select Docker deployment
3. Configure environment
4. Deploy!

#### Fly.io
```bash
flyctl launch  # Follow prompts
flyctl deploy
```

## Useful Commands

```bash
# View logs
docker-compose -f docker/docker-compose.yml logs -f vorlagen-app

# Execute command in container
docker-compose -f docker/docker-compose.yml exec vorlagen-app bash

# Restart service
docker-compose -f docker/docker-compose.yml restart vorlagen-app

# Remove all volumes (WARNING: deletes all data)
docker-compose -f docker/docker-compose.yml down -v

# Check container status
docker-compose -f docker/docker-compose.yml ps

# View resource usage
docker stats
```

## Backup & Restore

### Backup Database and Archive

```bash
# Backup all volumes
docker run --rm \
  -v vorlagen-data:/data \
  -v vorlagen-archive:/archive \
  -v $(pwd)/backup:/backup \
  alpine tar czf /backup/vorlagen-backup.tar.gz -C / data archive

# Backup just the database
docker cp vorlagen-generator:/app/data/data.db ./data.db
```

### Restore from Backup

```bash
docker run --rm \
  -v vorlagen-data:/data \
  -v vorlagen-archive:/archive \
  -v $(pwd)/backup:/backup \
  alpine tar xzf /backup/vorlagen-backup.tar.gz -C /
```

## Troubleshooting

### Container won't start
```bash
docker-compose -f docker/docker-compose.yml logs vorlagen-app
```

### Database locked
```bash
# Restart the container
docker-compose -f docker/docker-compose.yml restart vorlagen-app
```

### Port already in use
Change in `docker-compose.yml`:
```yaml
ports:
  - "9000:8000"  # Access on http://localhost:9000
```

### PDF conversion failing
Ensure LibreOffice is properly installed in the container:
```bash
docker-compose -f docker/docker-compose.yml exec vorlagen-app \
  libreoffice --version
```

## Scaling (Production)

For multiple instances with load balancing, use:

```bash
docker-compose -f docker/docker-compose.yml up -d --scale vorlagen-app=3
```

Then use Nginx or HAProxy for load balancing across instances.

## Security Best Practices

1. **Use HTTPS only** in production (Let's Encrypt)
2. **Set strong admin password** on first login
3. **Enable 2FA** for all admin accounts
4. **Regular backups** - automate with cron jobs
5. **Keep Docker updated** - `docker pull` regularly
6. **Use environment variables** for secrets (not in compose file)
7. **Run behind firewall** - restrict access to trusted IPs

## Questions?

For issues, check:
- Container logs: `docker-compose logs vorlagen-app`
- Database integrity: Check `/app/data/data.db`
- File permissions: All should be owned by `appuser` (UID 1000)
