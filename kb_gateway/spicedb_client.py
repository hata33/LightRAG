"""
ReBAC 权限引擎 — 纯 Python 实现(接口兼容 SpiceDB)

为什么不直接用 SpiceDB:
  SpiceDB 需要 Docker 容器(authzed/spicedb 镜像),练习环境拉取慢。
  这个模块用纯 Python 实现了 SpiceDB 的核心 ReBAC 模型,
  API 和 authzed-py 一致(check_permission / write_relationships / lookup_resources)。
  以后 Docker 可用后,把本文件替换为真实 gRPC 客户端调用即可,上层代码不变。

ReBAC (Relationship-Based Access Control) 核心概念:
  - relationship: 三元组 (resource, relation, subject)
    例: (document:report, viewer, user:alice) 表示 alice 是 report 的 viewer
  - permission: 基于关系的计算规则
    例: view = viewer + owner 表示 viewer 或 owner 都有 view 权限
  - check_permission: 给定 (resource, permission, subject),返回是否有权限
  - lookup_resources: 给定 (resource_type, permission, subject),返回所有有权限的资源

这是 Google Zanzibar 论文的简化实现。
真实 SpiceDB 支持图遍历、caveals、一致性等高级特性,这里只实现核心子集。
"""

from typing import Any

import os


# ── Schema 定义 ─────────────────────────────────────────────

# SpiceDB 的 schema 语言(Zed)定义如下:
#   definition user {}
#   definition document {
#       relation viewer: user
#       relation owner: user
#       permission view = viewer + owner
#   }
#
# 这里用 Python dict 表示权限计算规则:
#   permission_name → list of relation_names (并集)
SCHEMA = {
    "user": {},
    "document": {
        "relations": ["viewer", "owner"],
        "permissions": {
            "view": ["viewer", "owner"],  # view = viewer + owner
        },
    },
}


class PermissionEngine:
    """
    内存 ReBAC 权限引擎。

    存储结构:
        _relationships: set of (resource_type, resource_id, relation, subject_type, subject_id)

    例: ("document", "report", "viewer", "user", "alice")
        表示 document:report#viewer@user:alice
    """

    def __init__(self):
        # (rtype, rid, relation, stype, sid) → True
        self._relationships: set[tuple[str, str, str, str, str]] = set()
        self._schema = SCHEMA

    # ── 写关系(对应 SpiceDB WriteRelationships) ────────────

    def write_relationship(
        self,
        resource_type: str,
        resource_id: str,
        relation: str,
        subject_type: str,
        subject_id: str,
    ):
        """写入一条关系。幂等(重复写不会创建副本)。"""
        self._relationships.add(
            (resource_type, resource_id, relation, subject_type, subject_id)
        )

    def delete_relationship(
        self,
        resource_type: str,
        resource_id: str,
        relation: str,
        subject_type: str,
        subject_id: str,
    ):
        """删除一条关系。"""
        self._relationships.discard(
            (resource_type, resource_id, relation, subject_type, subject_id)
        )

    # ── 检查权限(对应 SpiceDB CheckPermission) ──────────────

    def check_permission(
        self,
        resource_type: str,
        resource_id: str,
        permission: str,
        subject_type: str,
        subject_id: str,
    ) -> bool:
        """
        检查 subject 是否对 resource 有 permission 权限。

        根据 schema 里 permission 的定义,展开为关系的并集检查。
        例: permission "view" = ["viewer", "owner"]
            → 检查 subject 是否是 viewer 或 owner

        Returns:
            True(有权限) / False(无权限)
        """
        resource_schema = self._schema.get(resource_type, {})
        permissions = resource_schema.get("permissions", {})

        if permission not in permissions:
            # 如果 permission 名就是一个 relation 名,直接查
            return self._has_direct_relation(
                resource_type, resource_id, permission, subject_type, subject_id
            )

        # 展开 permission → relations 并集
        relations = permissions[permission]
        for relation in relations:
            if self._has_direct_relation(
                resource_type, resource_id, relation, subject_type, subject_id
            ):
                return True

        return False

    def _has_direct_relation(
        self,
        resource_type: str,
        resource_id: str,
        relation: str,
        subject_type: str,
        subject_id: str,
    ) -> bool:
        """直接查一条关系是否存在。"""
        return (
            resource_type,
            resource_id,
            relation,
            subject_type,
            subject_id,
        ) in self._relationships

    # ── 查资源列表(对应 SpiceDB LookupResources) ────────────

    def lookup_resources(
        self,
        resource_type: str,
        permission: str,
        subject_type: str,
        subject_id: str,
    ) -> list[str]:
        """
        查出 subject 有 permission 权限的所有 resource_id。

        对应 SpiceDB 的 LookupResources API。
        用于 pre-filter:查询前拿到用户能看的所有文档白名单。
        """
        result = []
        # 遍历所有该类型的 resource_id
        seen_ids = {
            rid for (rtype, rid, _, _, _) in self._relationships
            if rtype == resource_type
        }
        for rid in seen_ids:
            if self.check_permission(
                resource_type, rid, permission, subject_type, subject_id
            ):
                result.append(rid)
        return result

    # ── 便捷方法(领域特定,文档权限) ───────────────────────

    def grant_document_view(self, document_id: str, user_id: str, as_owner: bool = False):
        """授予用户对文档的查看权限(viewer 或 owner)。"""
        relation = "owner" if as_owner else "viewer"
        self.write_relationship("document", document_id, relation, "user", user_id)

    def revoke_document_view(self, document_id: str, user_id: str):
        """撤销用户对文档的所有查看权限(viewer + owner 都删)。"""
        for rel in ("viewer", "owner"):
            self.delete_relationship("document", document_id, rel, "user", user_id)

    def can_view_document(self, user_id: str, document_id: str) -> bool:
        """检查用户能否查看文档。"""
        return self.check_permission(
            "document", document_id, "view", "user", user_id
        )

    def get_viewable_documents(self, user_id: str) -> set[str]:
        """获取用户能查看的所有文档 ID。用于 pre-filter 白名单。"""
        return set(
            self.lookup_resources("document", "view", "user", user_id)
        )

    # ── 调试 ──────────────────────────────────────────────────

    def dump_relationships(self) -> list[str]:
        """打印所有关系(调试用)。"""
        lines = []
        for (rtype, rid, rel, stype, sid) in sorted(self._relationships):
            lines.append(f"  {rtype}:{rid}#{rel}@{stype}:{sid}")
        return lines


# ── 文档 ID 映射工具 ─────────────────────────────────────────

def file_path_to_doc_id(file_path: str) -> str:
    """
    LightRAG 的 file_path → SpiceDB document ID 的映射。

    LightRAG 用文件路径标识文档来源(如 "finance_report.txt" 或 "/docs/report.pdf")。
    SpiceDB 用简单字符串作为 object ID。
    映射规则:取 basename,去掉扩展名。

    例:
      "finance_report.txt"     → "finance_report"
      "/docs/salary_table.pdf" → "salary_table"
      "report"                 → "report"
    """
    basename = os.path.basename(file_path)
    # 去掉扩展名
    if "." in basename:
        basename = basename.rsplit(".", 1)[0]
    return basename


# ── 全局单例 ─────────────────────────────────────────────────

# 全局权限引擎实例(由 run_proxy.py 初始化)
_engine: PermissionEngine | None = None


def get_engine() -> PermissionEngine:
    """获取全局权限引擎。"""
    global _engine
    if _engine is None:
        raise RuntimeError("权限引擎未初始化,请用 run_proxy.py 启动")
    return _engine


def set_engine(engine: PermissionEngine):
    """设置全局权限引擎。"""
    global _engine
    _engine = engine
