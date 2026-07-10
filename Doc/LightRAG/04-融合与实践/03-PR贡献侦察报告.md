# LightRAG PR 贡献侦察报告（pr-recon）

**目标仓库**：HKUDS/LightRAG（fork：hata33/LightRAG）
**侦察日期**：2026-07-05 · **作者**：15531
**方法**：逆向最近 100 个 merged PR（其中 30 个外部贡献者）+ 197 个 open issue + 29 个 open PR 的竞争排查

> 结论先行：这是 37k-star 大仓库，**几乎所有"看起来很干净"的 bug 都已有人在改**。首推策略不是抢热门 issue，而是 (a) 补**文档缺口**（你刚做完文档、最快合并区）、或 (b) 盯**新 issue<48h 抢跑**、或 (c) 主动提**资源泄漏/安全加固**类小修复。

---

## 一、维护者品位档案（一页纸）

```
仓库:        HKUDS/LightRAG  (★37,345, 大仓库)
真人决策者:  danielaskdd  —— 30/30 外部 PR 全部由他亲手 merge（既 review 又 merge）
AI 评审:     Codex（@codex review 触发，轻量门槛建模）
             信号: "Didn't find any major issues" + 👍  / 否则提建议
硬门槛:      ① Codex 无 major issue  ② CI 绿  ③ 标题 conventional + scope
             （注意: 没有 APPROVED 状态，danielaskdd 直接 merge；trivial PR 无需测试）
快合并公式:  conventional 标题(scope) + Fixes #issue + +2~200行 / 1~2文件 / 1~3 commit
             中位 22 行、2 文件、9 小时合并、70% 一次过
红旗区:      ① 体量过大（+1000行/多文件 → 拖到 80~93h 且维护者要自己补 follow-up）
             ② 改默认值无开关  ③ 解析/安全路径用 ad-hoc 正则
             ④ scope 蔓延进核心运行时（pipeline/operate/kg 共享存储）
维护者性格:  强"补丁型" —— 在好 PR 上频繁亲手补 follow-up commit/测试/默认值
             （#3304 #3228 都被他亲手补过）。策略：方向对+测试齐 > 一次完美
热点区域:    安全加固(path traversal/URL注入)、资源泄漏(关闭 client)、
             存储后端正确性(Milvus/Mongo/Postgres/Bedrock-Cohere)、
             WebUI(perf/NDJSON 解析)、文档(README 翻译/typo/链接)
```

### 快合并公式证据（非 dependabot 的人类 PR）
| PR | 时长 | 规模 | 类型 |
|----|------|------|------|
| #3291 | 0h | +2/-2 | typo: Dokcer→Docker |
| #3268 | 2h | +54/-2 | fix(bedrock): Cohere modelId |
| #3316 | 3h | +158/-0 | **fix(sidecar): path traversal** |
| #3324 | 5h | +197 | **fix(external): task ID URL injection** |
| #3251 | — | +106 | 🐛fix(mongo): close MongoClient on release |
| #3261 | 37h | +428 | fix(llm): close Anthropic AsyncClient |
| #3275 | 2h | +518 | docs: Japanese README |

### 红旗证据（大体量被拖慢 + 维护者自补）
- #3304 perf(webui) +1423/20f → **88h**，danielaskdd 亲手补 follow-up 修交互回归
- #3228 Milvus +1222/4f → **93h**，danielaskdd 要求合并 #2979、亲手补 `55894a3`

---

## 二、分级候选清单（已做竞争排查）

> ⚠️ 关键教训：以下 6 个"看起来很干净"的候选，逐条查后**全部已在途或已解决**。这正是 pr-recon 第 4 步的价值——**不查竞争就动手 = 必撞车**。

| 级别 | #编号 | 标题 | 区域 | 竞争排查结论 |
|------|------|------|------|--------------|
| 🔴 | #3355 | OpenSearch DocStatus 静默失败 | backend | **open PR #3354**(VectorPeak) 直接修 |
| 🔴 | #3352 | ainsert_custom_chunks 不合并入 KG | core | **open PR #3353**(ysys143) `Fixes #3352` |
| 🔴 | #2996 | Docling 存整个 JSON envelope | parser | **open PR #3344** `Fixes #2996`(含回归测试) |
| 🔴 | #2975 | OpenSearch `_shard_doc` 不支持 | backend | **已修复**: merged PR #2991（issue 未关） |
| 🔴 | #2138/#2555 | QueryParam `ids` 不生效 | API | **维护者 deferred**: 等 multi-workspace；closed PR #2878 已删文档 |
| 🔴 | #2904 | LIGHTRAG-WORKSPACE header 被忽略 | API | **2 个 open PR** #3011/#2932（workspace 是热门战场） |
| 🔴 | #3302 | Security | — | **空 body**，不可操作 |
| 🟡 | #2632 | auth "Invalid token"（带 Authorization 仍报错） | API/docs | 根因=用户同时发 `Authorization`+`X-API-Key`；**已是用户错误**，代码无 bug → 可做**文档澄清** |
| 🟡 | #1323 | 同名实体自动合并（👍12/c33） | backend | 大型 enhancement + tracked/discuss → 体积红旗区，首 PR 别碰 |
| 🟡 | #1985 | 文档自定义 metadata 列（👍15） | backend | 大型 enhancement，高 thumbs 但体积大 |

### 真 🟢 方向（首推，按 ROI 排序）

**① 文档缺口 PR（最稳，匹配你的能力与最快合并区）**
- 来源：#2632 的根因是"同时传 Authorization 和 X-API-Key 会冲突"，但当前文档未警告。
- 动作：在 API 鉴权文档（`docs/` 或 README 鉴权段）补一段"不要同时发送两种凭据头"。
- 优势：docs 是最快合并区（#3275/#3291/#3333 均≤2h）、无代码风险、无竞争、你刚做完文档手感正。
- 风险：需先确认上游文档确实没写（动手前 grep 一遍）。

**② 盯新 issue 抢跑（VectorPeak 模式）**
- VectorPeak 用 #3316（path traversal，3h 合并）+ #3354（#3355）展示了"新 bug 一出现就提 scoped fix"。
- 动作：watch 仓库，新 bug issue 出现 24h 内读 body→复现→提 `fix(scope):` 小 PR（+回归测试）。
- 优势：抢在他人前面；安全/泄漏/后端正确性最易过。

**③ 主动资源泄漏/安全加固（无 issue 也可提）**
- 模式参照 #3251/#3261（关闭 client）/ #3316/#3324（路径安全）。
- 动作：审计 `lightrag/llm/*`、`lightrag/kg/*`、`lightrag/sidecar/*` 里未在 release/error 路径关闭的 client / 未校验的路径拼接。
- 优势：维护者见一个合一个；风险：需构造对抗用例自证没开新 bypass。

---

## 三、PR 模板（按 LightRAG 逆向品位固化）

```markdown
Fixes #<number>

## Problem
<现象 + 最小复现。证明问题真实，附 issue 链接。>

## Changes
- <改动点 1>
- <改动点 2>
- 明示：No core runtime behavior touched（pipeline.py / operate.py / shared_storage 未改语义）

## Side effect analysis
- 默认值：未改 / 改了（已加配置开关 `<flag>` 保留旧行为）
- 向后兼容：<旧配置/调用是否照常工作>
- 安全边界：<有没有开新的路径/解析/网络口子；若有正则，附对抗用例自证无 bypass>

## Tests
- `python -m pytest tests/<对应目录>/test_xxx.py -q`  ✓
- `ruff check <改动文件>`  ✓
- CI green  ✓
```

---

## 四、提交前自查清单（按 danielaskdd / Codex 常戳点）

- [ ] **体量**：在快合并区内（+2~200 行 / 1–2 文件）；超了就拆
- [ ] **标题**：conventional + scope：`fix(sidecar):` / `fix(bedrock):` / `fix(llm):` / `fix(mongo):` / `fix(opensearch):` / `docs:` / `chore:` / `perf(webui):`
- [ ] **绑 issue**：`Fixes #xxx` 在 body 第一行
- [ ] **回归测试**：bug 修复必带边界测试（空值/超大/并发/编码）；trivial typo/dep 可免
- [ ] **默认值**：改了→加 opt-in 开关保留旧行为
- [ ] **解析/安全路径**：用了正则？→ 构造对抗用例自证（#3316/#3324 是正面范例）
- [ ] **None 安全**：metadata/dict 可能为 None；dict→str 用 `json.dumps` 不用 `str()`
- [ ] **新增配置字段**：代码里真有人读它
- [ ] **lint + format**：`ruff check .` + `ruff format .` 干净（target py310）
- [ ] **没碰核心语义区**（pipeline / operate / shared_storage 的语义）
- [ ] **不误改** README/CHANGELOG（除非 issue 明确要求）
- [ ] **方向对 + 测试齐即可**：danielaskdd 是补丁型，细节他会补，别在格式上过度纠结

---

## 五、给你的执行建议（首 PR）

1. **先做 ①（文档澄清 #2632）**：风险最低、最快合、建立"外部贡献者"信任记录（CONTRIBUTOR 关联）。
2. 提交后获得首次 merge，下次再做 ②/③ 这类代码修复时，维护者对你已有信任基线。
3. 全程用 **`pr-run`** skill 跑「建分支→实现→自审→推送→建 PR→盯 Codex/CI」，本报告作为其输入。
4. 每次新 PR 前回 pr-recon 重查竞争（37k 仓库竞争每周都在变）。

---

## 六、已合并 PR 成绩单（实时回填）

| PR | 分支 | 类型 | 合并者 | 合并时间(UTC) | trailer | 备注 |
|----|------|------|--------|--------------|---------|------|
| #3358 | docs/auth-header-conflict | docs | danielaskdd | 2026-07-06 08:57 | ✅带 | Fixes #2632，文档澄清 |
| #3359 | perf/summary-encode-once | perf(operate) | danielaskdd | 2026-07-06 10:37 | ✅带 | map-reduce 每条 desc 只编码一次 |
| #3360 | perf/vector-finalize-close-client | perf(kg) | danielaskdd | 2026-07-06 10:44 | ✅带 | Milvus/Qdrant finalize 关连接 |

**复盘（3/3）**：
- 全部由 danielaskdd **亲手 merge**，全部带 `Co-Authored-By: Claude` trailer —— 实证"AI 署名不影响合入"，先前担忧作废。
- #3359/#3360 相隔仅 **7 分钟** → 维护者**攒批 review** perf PR。
- #3360 命中其"资源泄漏"热点区（同 #3251 Mongo close / #3261 Anthropic close 一条线），零追问即合。
- 三件套齐全（root cause 钉 file:line + side-effect 分析 + 真跑过的测试）是快通道的共性。

---

*侦察产出文件位置：`Doc/LightRAG/PR贡献侦察报告.md`（本文件）*
*数据快照：`C:/Users/15531/AppData/Local/Temp/prrecon/{issues,prs,open_prs}.json`*
