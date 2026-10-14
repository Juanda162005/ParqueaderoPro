#!/bin/bash
# ================================================================
#  DESPLIEGUE COMPLETO — ParqueaderoPRO en Hostinger VPS
#  Dominio: parqueaderopro.online
#  Ejecutar como root o usuario con sudo
# ================================================================
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${GREEN} ╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN} ║   ParqueaderoPRO - Deploy Hostinger VPS      ║${NC}"
echo -e "${GREEN} ╚══════════════════════════════════════════════╝${NC}"
echo ""

# ============================================================
# PASO 1: Instalar Docker si no está
# ============================================================
echo -e "${YELLOW}[1/6] Instalando Docker...${NC}"
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}   Docker instalado.${NC}"
else
    echo -e "${GREEN}   Docker ya está instalado.${NC}"
fi

# ============================================================
# PASO 2: Crear carpetas y archivos de entorno
# ============================================================
echo -e "${YELLOW}[2/6] Preparando entorno...${NC}"

mkdir -p certbot/www
mkdir -p certbot/conf

if [ ! -f .env ]; then
    JWT_KEY=$(openssl rand -base64 32)
    echo "JWT_SECRET_KEY=$JWT_KEY" > .env
    echo -e "${GREEN}   .env creado con clave JWT segura.${NC}"
else
    echo -e "${GREEN}   .env ya existe.${NC}"
fi

source .env

# ============================================================
# PASO 3: Primera fase — levantar SIN SSL
# ============================================================
echo -e "${YELLOW}[3/6] Levantando servicios en modo inicial (HTTP)...${NC}"

cp nginx-init.conf nginx.conf.current

cat > docker-compose.init.yml << 'EOFINIT'
services:
  api:
    build:
      context: ./backend
      dockerfile: Dockerfile
    environment:
      - JWT_SECRET_KEY=${JWT_SECRET_KEY}
      - CORS_ORIGINS=https://parqueaderopro.online,https://www.parqueaderopro.online,http://localhost:8080
      - ENABLE_DOCS=false
    restart: unless-stopped
    volumes:
      - parqueadero_data:/app

  web:
    image: nginx:alpine
    volumes:
      - ./frontend:/usr/share/nginx/html:ro
    restart: unless-stopped

  proxy:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf.current:/etc/nginx/conf.d/default.conf:ro
      - ./certbot/www:/var/www/certbot:ro
    depends_on:
      - api
      - web
    restart: unless-stopped

volumes:
  parqueadero_data:
EOFINIT

docker compose -f docker-compose.init.yml up -d --build
echo -e "${GREEN}   Servicios levantados en HTTP.${NC}"

# ============================================================
# PASO 4: Verificar que el dominio responde
# ============================================================
echo -e "${YELLOW}[4/6] Verificando dominio...${NC}"
echo "   Asegúrate de que en Hostinger el dominio parqueaderopro.online"
echo "   tenga un registro A apuntando a la IP de este VPS."
echo ""
SERVER_IP=$(curl -s ifconfig.me)
echo -e "   IP del servidor: ${GREEN}$SERVER_IP${NC}"
echo ""

read -p "   ¿El dominio ya apunta a este servidor? (s/n): " DOMINIO_OK
if [ "$DOMINIO_OK" != "s" ]; then
    echo ""
    echo -e "   ${YELLOW}Configura el registro A en Hostinger primero:${NC}"
    echo "   - Tipo: A  |  Nombre: @  |  Valor: $SERVER_IP  |  TTL: 3600"
    echo "   - Tipo: A  |  Nombre: www  |  Valor: $SERVER_IP  |  TTL: 3600"
    echo ""
    echo "   Vuelve a ejecutar este script cuando el DNS se propague."
    exit 0
fi

# ============================================================
# PASO 5: Obtener certificado SSL con Certbot
# ============================================================
echo -e "${YELLOW}[5/6] Obteniendo certificado SSL...${NC}"

docker run --rm \
  -v "$(pwd)/certbot/conf:/etc/letsencrypt" \
  -v "$(pwd)/certbot/www:/var/www/certbot" \
  certbot/certbot certonly --webroot \
  -w /var/www/certbot \
  -d parqueaderopro.online \
  -d www.parqueaderopro.online \
  --email admin@parqueaderopro.online \
  --agree-tos \
  --non-interactive

if [ $? -ne 0 ]; then
    echo -e "${RED}   ERROR: No se pudo obtener el certificado.${NC}"
    exit 1
fi

echo -e "${GREEN}   Certificado SSL obtenido.${NC}"

# ============================================================
# PASO 6: Activar HTTPS
# ============================================================
echo -e "${YELLOW}[6/6] Activando HTTPS...${NC}"

docker compose -f docker-compose.init.yml down
cp nginx.conf nginx.conf.current
docker compose -f docker-compose.prod.yml up -d --build
rm -f docker-compose.init.yml

echo ""
echo -e "${GREEN} ╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN} ║   ¡DESPLIEGUE COMPLETO!                      ║${NC}"
echo -e "${GREEN} ║   https://parqueaderopro.online               ║${NC}"
echo -e "${GREEN} ╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "  🔑 Login: admin / admin123"
echo "  ⚠️  CAMBIA LA CONTRASEÑA en el primer inicio"
echo ""
echo "  Renovación automática del SSL (agrega al crontab):"
echo "  0 3 * * * docker run --rm -v $(pwd)/certbot/conf:/etc/letsencrypt -v $(pwd)/certbot/www:/var/www/certbot certbot/certbot renew --quiet && docker compose -f $(pwd)/docker-compose.prod.yml restart proxy"
echo ""
