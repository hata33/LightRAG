# 练习 3:向量层 pre-filter

**目标**:在检索阶段就排除越权文档,让 LLM 根本看不到越权内容,彻底解决 post-fetch 的局限。

---

## 一、对比:为什么 post-fetch 不够

```
练习 2 (post-fetch):
  query → 向量搜索(看到所有文档) → LLM 生成(含越权内容) → 代理过滤 references
  结果:LLM 文本泄露 "850万",只过滤了引用

练习 3 (pre-filter):
  query → 向量搜索(只搜有权限的文档) → LLM 生成(不含越权内容) → 返回
  结果:LLM 文本天然干净,根本不含薪资数字
```

---

## 二、代码改动(4 个文件,7 处改动)

### 2.1 QueryParam 加 ACL 字段(`base.py`)

```python
@dataclass
class QueryParam:
    ...
    acl_allowed_doc_ids: set[str] | None = None
    """If set, only retrieve content from these doc_ids. None = no filtering."""
```

### 2.2 三个检索注入点(`operate.py`)

**chunk 过滤**(`_get_vector_context`):
```python
# chunks 结果直接带 full_doc_id,过滤最简单
if acl_doc_ids is not None:
    search_top_k = search_top_k * 3  # 放大召回,防止过滤后不足
    results = await chunks_vdb.query(query, top_k=search_top_k, ...)
    results = [r for r in results if r.get("full_doc_id") in acl_doc_ids]
```

**entity/relation 过滤**(`_get_node_data` / `_get_edge_data`):
```python
# entity/relation 的 file_path 是 <SEP> 拼接的多个文件名
# 用 OR 语义:只要有一个文件在白名单就保留
if acl_doc_ids is not None:
    results = await entities_vdb.query(query, top_k=top_k * 3, ...)
    results = [r for r in results if _entity_has_allowed_doc(r, acl_doc_ids)]
```

**OR 语义 helper**:
```python
def _entity_has_allowed_doc(result: dict, allowed_doc_ids: set[str]) -> bool:
    """file_path 可能是 'report.txt<SEP>salary.txt' 多值格式"""
    paths = result.get("file_path", "").split(GRAPH_FIELD_SEP)
    for p in paths:
        basename = os.path.basename(p.strip()).rsplit(".", 1)[0]
        if basename in allowed_doc_ids:
            return True
    return False
```

### 2.3 修复 aquery_data clone(`lightrag.py`)

```python
# 坑位4:手工 clone 必须同步新字段
data_param = QueryParam(
    ...,
    acl_allowed_doc_ids=param.acl_allowed_doc_ids,  # ← 必须加
)
```

### 2.4 修复 cache key(`operate.py`)

```python
# 坑位5:ACL 必须进入 cache key,否则跨 ACL 缓存污染
acl_hash = sorted(query_param.acl_allowed_doc_ids) if ... else None
args_hash = compute_args_hash(..., str(acl_hash))
```

---

## 三、踩的坑

### 坑位 4:aquery_data clone 漏字段

`lightrag.py:2249` 手工 clone QueryParam,只复制了 15 个字段,漏了新增的 `acl_allowed_doc_ids`。

**教训**:在手工 clone 的 dataclass 上加字段,必须 grep 所有构造点。

### 坑位 5:LLM 缓存跨 ACL 污染

场景1(无 ACL)的答案被场景2(有 ACL)复用 —— cache key 没包含 ACL 白名单。

**修复**:kg_query 和 naive_query 两处 cache key 都加 `str(sorted(acl_allowed_doc_ids))`。

**教训**:任何影响 LLM 输出的参数,都必须进入 cache key。修复后要 grep 找同类调用点(naive_query 也有同样的 compute_args_hash)。

### 坑位 8:file_path 多值格式

entity/relation 的 `file_path` 是 `<SEP>` 拼接的多个文件名。直接用 `==` 比较永远匹配不上。

**修复**:split(GRAPH_FIELD_SEP) 后逐个检查,OR 语义。

---

## 四、验证结果(SDK 直接测试)

```
场景 1: 无 ACL → "CEO张三的年薪为850万"            ✅ 正常
场景 2: ACL 只允许 budget → "没有找到CEO年薪信息"    ✅ 薪资被过滤!
场景 3: ACL 允许 salary → "CEO张三的年薪为850万"    ✅ 有权限能查到
```

对比练习 2:LLM 文本不再泄露薪资内容。**pre-filter 彻底解决了 post-fetch 的局限。**

---

## 五、学到的核心概念

1. **pre-filter vs post-fetch**:过滤输入 vs 过滤输出,安全性天壤之别
2. **放大召回**:过滤发生在搜索后,必须 top_k × 3 防止不足
3. **缓存与权限的交互**:影响输出的参数必须进入 cache key
4. **多值字段处理**:graph RAG 的实体是跨文档聚合的,file_path 是多值
