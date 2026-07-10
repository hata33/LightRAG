#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# LightRAG Demo 启动脚本
# 模式: Docker 只启动数据库（PG + Neo4j），前后端本地手动启动
# 用法: bash docker/demo/start-demo.sh
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

COMPOSE_FILE="docker-compose-demo.yml"
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail()  { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

# ── 0. 前置检查 ──
info "Checking prerequisites..."
command -v docker >/dev/null 2>&1 || fail "Docker not installed"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 not available"

# ── 1. 复制配置 ──
if [ ! -f ".env" ]; then
    info "Copying .env.demo -> .env"
    cp .env.demo .env
else
    warn ".env already exists, skipping copy (delete it first to regenerate)"
fi

# ── 2. 创建数据目录（含数据库挂载目录） ──
mkdir -p data/rag_storage data/inputs data/prompts
mkdir -p data/postgres data/neo4j data/neo4j-logs

# ── 3. 启动数据库容器（仅 PG + Neo4j） ──
info "Starting 2 database containers (postgres + neo4j)..."
docker compose -f "$COMPOSE_FILE" up -d

# ── 4. 等待数据库就绪 ──
info "Waiting for PostgreSQL..."
for i in $(seq 1 30); do
    docker exec demo-postgres pg_isready -U rag -d rag >/dev/null 2>&1 && break
    sleep 2
    [ "$i" -eq 30 ] && fail "PostgreSQL did not start in 60s"
done
info "PostgreSQL is ready!"

info "Waiting for Neo4j..."
for i in $(seq 1 30); do
    docker exec demo-neo4j cypher-shell -u neo4j -p neo4j123 "RETURN 1" >/dev/null 2>&1 && break
    sleep 3
    [ "$i" -eq 30 ] && fail "Neo4j did not start in 90s"
done
info "Neo4j is ready!"

# ── 5. 验证 PG 扩展 ──
info "Verifying pgvector extension..."
docker exec demo-postgres psql -U rag -d rag -c "SELECT extname FROM pg_extension WHERE extname='vector';" 2>/dev/null \
    | grep -q vector \
    && info "pgvector extension OK" \
    || warn "pgvector extension not found"

# ── 6. 完成 ──
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ Databases are running!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo "  Databases:"
echo "    PostgreSQL:  localhost:5432  (rag / rag123 / rag)"
echo "    Neo4j:       localhost:7687  (neo4j / neo4j123)"
echo "    Neo4j Browser: http://localhost:7474"
echo ""
echo "  Now start the backend and frontend manually:"
echo ""
echo "  ── Backend (terminal 1) ──"
echo "    PYTHONUTF8=1 uv run lightrag-server"
echo ""
echo "  ── Frontend (terminal 2) ──"
echo "    cd lightrag_webui && pnpm dev"
echo ""
echo "  Then open:"
echo "    WebUI:  http://localhost:5173  (or 5174)"
echo "    API:    http://localhost:9621/docs"
echo ""
echo "  Model APIs (192.168.1.161):"
echo "    LLM:        :10001/v1"
echo "    Embedding:  :10004/v1"
echo "    Rerank:     :10005/v1/rerank"
echo ""
echo "  Stop databases:  docker compose -f $COMPOSE_FILE down"
echo "  Clear all data:  docker compose -f $COMPOSE_FILE down -v"
echo ""
