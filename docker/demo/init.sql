-- LightRAG Demo: PostgreSQL 初始化脚本
-- ──────────────────────────────────────────
-- 此脚本由 pgvector 镜像在首次启动时自动执行
-- (挂载到 /docker-entrypoint-initdb.d/init.sql)
--
-- 作用: 确保 rag 库安装 pgvector 扩展。
-- LightRAG 的 PGVectorStorage 在首次写入时会自动建表,
-- 但 vector 扩展必须提前安装。

-- 安装 pgvector 扩展（向量类型 + 索引支持）
CREATE EXTENSION IF NOT EXISTS vector;

-- 验证安装成功
DO $$
BEGIN
    RAISE NOTICE 'pgvector extension installed successfully on database: %', current_database();
END $$;
