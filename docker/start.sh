#!/bin/bash
# Quick start script for Docker deployment

set -e

echo "🐳 Vorlagen Generator - Docker Deployment"
echo "=========================================="
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

echo "✅ Docker and Docker Compose found"
echo ""

# Build and start containers
echo "📦 Building Docker image..."
docker-compose -f docker/docker-compose.yml build

echo ""
echo "🚀 Starting containers..."
docker-compose -f docker/docker-compose.yml up -d

echo ""
echo "⏳ Waiting for service to be ready..."
sleep 5

# Check if service is running
if docker-compose -f docker/docker-compose.yml ps | grep -q "vorlagen-app.*Up"; then
    echo ""
    echo "✅ SUCCESS! Your application is running"
    echo ""
    echo "🌐 Access it at: http://localhost:8000"
    echo ""
    echo "📝 Useful commands:"
    echo "   View logs:      docker-compose -f docker/docker-compose.yml logs -f"
    echo "   Stop service:   docker-compose -f docker/docker-compose.yml down"
    echo "   Restart:        docker-compose -f docker/docker-compose.yml restart"
    echo ""
else
    echo ""
    echo "❌ Service failed to start. Check logs:"
    docker-compose -f docker/docker-compose.yml logs vorlagen-app
    exit 1
fi
