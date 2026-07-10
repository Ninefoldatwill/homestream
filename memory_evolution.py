"""
记忆演化引擎 — 遗忘·合并·重构 三合一。

融优来源：
  OpenMemory 认知分类衰退(5级λ) + SimpleMem 三阶段压缩(43.24% F1)
  + OpenClaw 混合召回(BM25+向量7:3) + Memobase 批处理成本优化

设计原则：
  仿生优于机械 · 韧性优于性能 · 混合优于单一 · 务实优于完美

三引擎联动：事件流入 → 遗忘引擎递减salience → 合并引擎聚类压缩 → 重构引擎反思提炼
"""

import json
import math
import re
import sqlite3
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

import structlog

logger = structlog.get_logger("bridge_v7.memory_evolution")


# ===========================================================================
# Cognitive decay rates (OpenMemory 2025)
# ===========================================================================


class MemoryType(str, Enum):
    REFLECTIVE = "reflective"  # 核心洞察 λ=0.001 半衰期693天
    SEMANTIC = "semantic"  # 技术知识 λ=0.005 半衰期138天
    PROCEDURAL = "procedural"  # 操作流程 λ=0.008 半衰期86天
    EPISODIC = "episodic"  # 对话记录 λ=0.015 半衰期46天
    EMOTIONAL = "emotional"  # 用户偏好 λ=0.020 半衰期34天

    @classmethod
    def from_category(cls, category: str) -> "MemoryType":
        mapping = {
            "reflective": cls.REFLECTIVE,
            "insight": cls.REFLECTIVE,
            "semantic": cls.SEMANTIC,
            "knowledge": cls.SEMANTIC,
            "procedural": cls.PROCEDURAL,
            "workflow": cls.PROCEDURAL,
            "episodic": cls.EPISODIC,
            "log": cls.EPISODIC,
            "emotional": cls.EMOTIONAL,
            "preference": cls.EMOTIONAL,
        }
        return mapping.get(category.lower(), cls.SEMANTIC)


COGNITIVE_DECAY_RATES: dict[MemoryType, float] = {
    MemoryType.REFLECTIVE: 0.001,
    MemoryType.SEMANTIC: 0.005,
    MemoryType.PROCEDURAL: 0.008,
    MemoryType.EPISODIC: 0.015,
    MemoryType.EMOTIONAL: 0.020,
}

FORGET_THRESHOLD = 0.1  # salience < 0.1 → 标记遗忘
DEFAULT_IMPORTANCE = 0.7  # 新记忆默认重要性
BOOST_ON_ACCESS = 0.1  # 访问时salience增量
EVERGREEN_PATTERNS = [
    r"MEMORY\.md",
    r"SOUL\.md",
    r"IDENTITY\.md",
    r"patterns\.md",
    r"USER\.md",
    r"\.workbuddy/memory/MEMORY\.md",
]


# ===========================================================================
# Data structures
# ===========================================================================


@dataclass
class MemoryRecord:
    """单条记忆记录。"""

    id: str
    content: str
    mtype: MemoryType = MemoryType.SEMANTIC
    importance: float = DEFAULT_IMPORTANCE
    timestamp: float = field(default_factory=time.time)
    access_count: int = 0
    last_access: float = 0.0
    source: str = ""
    tags: list[str] = field(default_factory=list)
    parent_id: str | None = None  # 合并来源
    is_evergreen: bool = False
    archived: bool = False
    cause_event_id: str | None = None  # 因果链：触发此记忆写入的Event ID

    def salience(self, now: float | None = None) -> float:
        """计算当前衰减后显著度。salience = importance × e^(-λ × days)。"""
        if self.is_evergreen:
            return self.importance
        now = now or time.time()
        days = (now - self.timestamp) / 86400.0
        lam = COGNITIVE_DECAY_RATES.get(self.mtype, 0.005)
        return self.importance * math.exp(-lam * days)

    def should_forget(self, now: float | None = None) -> bool:
        return self.salience(now) < FORGET_THRESHOLD and not self.is_evergreen

    def reinforce(self):
        """访问强化。"""
        self.access_count += 1
        self.last_access = time.time()
        self.importance = min(1.0, self.importance + BOOST_ON_ACCESS)
        # 重置时间戳：让衰减重新计 — 仿生设计
        self.timestamp = time.time()

    @property
    def has_cause(self) -> bool:
        """是否携带因果链。"""
        return self.cause_event_id is not None


# ===========================================================================
# Forgetting Engine
# ===========================================================================


class ForgettingEngine:
    """遗忘引擎：基于认知分类的指数衰减。"""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                mtype TEXT DEFAULT 'semantic',
                importance REAL DEFAULT 0.7,
                timestamp REAL,
                access_count INTEGER DEFAULT 0,
                last_access REAL DEFAULT 0,
                source TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                parent_id TEXT,
                is_evergreen INTEGER DEFAULT 0,
                archived INTEGER DEFAULT 0,
                cause_event_id TEXT
            )
        """)
        # Migration: 为旧库添加 cause_event_id 列（CREATE IF NOT EXISTS 不更新已有表）
        try:
            conn.execute("SELECT cause_event_id FROM memories LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE memories ADD COLUMN cause_event_id TEXT")
            logger.info("memory_evolution.schema_migrated", added_column="cause_event_id")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cold_store (
                id TEXT PRIMARY KEY,
                content TEXT,
                mtype TEXT,
                importance REAL,
                timestamp REAL,
                archived_at REAL,
                reason TEXT
            )
        """)
        # 因果索引：加速 cause_event_id 查询
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_cause
            ON memories(cause_event_id)
        """)
        conn.commit()
        conn.close()

    def is_evergreen_path(self, path: str) -> bool:
        for pattern in EVERGREEN_PATTERNS:
            if re.search(pattern, path, re.IGNORECASE):
                return True
        return False

    def add(self, record: MemoryRecord):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT OR REPLACE INTO memories
               (id,content,mtype,importance,timestamp,access_count,last_access,
                source,tags,parent_id,is_evergreen,archived,cause_event_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record.id,
                record.content,
                record.mtype.value,
                record.importance,
                record.timestamp,
                record.access_count,
                record.last_access,
                record.source,
                json.dumps(record.tags),
                record.parent_id,
                int(record.is_evergreen),
                int(record.archived),
                record.cause_event_id,
            ),
        )
        conn.commit()
        conn.close()

    def get(self, record_id: str) -> MemoryRecord | None:
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT * FROM memories WHERE id=? AND archived=0",
            (record_id,),
        ).fetchone()
        conn.close()
        if row:
            return self._row_to_record(row)
        return None

    def _row_to_record(self, row) -> MemoryRecord:
        return MemoryRecord(
            id=row[0],
            content=row[1],
            mtype=MemoryType(row[2]),
            importance=row[3],
            timestamp=row[4],
            access_count=row[5],
            last_access=row[6],
            source=row[7],
            tags=json.loads(row[8]) if row[8] else [],
            parent_id=row[9],
            is_evergreen=bool(row[10]),
            archived=bool(row[11]),
            cause_event_id=row[12] if len(row) > 12 else None,
        )

    def get_by_cause(self, event_id: str) -> list[MemoryRecord]:
        """根据因果事件ID查找所有关联记忆。"""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT * FROM memories WHERE cause_event_id=? AND archived=0",
            (event_id,),
        ).fetchall()
        conn.close()
        return [self._row_to_record(r) for r in rows]

    def decay_scan(self) -> list[MemoryRecord]:
        """扫描所有需遗忘的记忆。"""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT * FROM memories WHERE archived=0 AND is_evergreen=0").fetchall()
        conn.close()

        now = time.time()
        forget_candidates = []
        for row in rows:
            rec = self._row_to_record(row)
            if rec.should_forget(now):
                forget_candidates.append(rec)
        return forget_candidates

    def forget_and_archive(self, record_id: str, reason: str = "decay"):
        """将记忆迁移到冷存储。"""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT * FROM memories WHERE id=?", (record_id,)).fetchone()
        if not row:
            conn.close()
            return
        rec = self._row_to_record(row)
        conn.execute(
            """INSERT OR REPLACE INTO cold_store
               (id,content,mtype,importance,timestamp,archived_at,reason)
               VALUES (?,?,?,?,?,?,?)""",
            (
                rec.id,
                rec.content,
                rec.mtype.value,
                rec.importance,
                rec.timestamp,
                time.time(),
                reason,
            ),
        )
        conn.execute("UPDATE memories SET archived=1 WHERE id=?", (record_id,))
        conn.commit()
        conn.close()
        logger.info(
            "memory_evolution.forgot",
            record_id=record_id,
            reason=reason,
            salience=round(rec.salience(), 4),
        )

    def reinforce_access(self, record_id: str):
        rec = self.get(record_id)
        if rec:
            rec.reinforce()
            self.add(rec)

    def list_active(self, limit: int = 50) -> list[MemoryRecord]:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT * FROM memories WHERE archived=0 ORDER BY importance DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [self._row_to_record(r) for r in rows]


# ===========================================================================
# Merging Engine
# ===========================================================================


class MergingEngine:
    """合并引擎：语义聚类 + 相似去重 + 批处理缓冲。"""

    def __init__(self, similarity_threshold: float = 0.7, buffer_token_limit: int = 2048):
        self.threshold = similarity_threshold
        self.buffer: list[str] = []
        self.buffer_tokens: int = 0
        self.buffer_token_limit = buffer_token_limit

    @staticmethod
    def jaccard_similarity(a: str, b: str) -> float:
        """Jaccard相似度（bigram级，务实优于完美）。"""

        def bigrams(s):
            s = s.lower()
            return {s[i : i + 2] for i in range(len(s) - 1)}

        set_a = bigrams(a)
        set_b = bigrams(b)
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)

    @staticmethod
    def token_count(text: str) -> int:
        """估算token数（中英混合·1字符≈0.5-2 token·保守取1.5）。"""
        return len(text) * 3 // 2

    def add_to_buffer(self, content: str):
        self.buffer.append(content)
        self.buffer_tokens += self.token_count(content)

    def should_merge(self) -> bool:
        return self.buffer_tokens >= self.buffer_token_limit or len(self.buffer) >= 20

    def cluster(self, records: list[MemoryRecord]) -> list[list[MemoryRecord]]:
        """简单贪心聚类（替代DBSCAN·无外部依赖）。"""
        if len(records) <= 1:
            return [[r] for r in records]

        clusters: list[list[MemoryRecord]] = []
        assigned: set[int] = set()

        for i, r in enumerate(records):
            if i in assigned:
                continue
            cluster = [r]
            assigned.add(i)
            for j, r2 in enumerate(records):
                if j in assigned:
                    continue
                if self.jaccard_similarity(r.content, r2.content) >= self.threshold:
                    cluster.append(r2)
                    assigned.add(j)
            clusters.append(cluster)
        return clusters

    def compress_cluster(self, cluster: list[MemoryRecord]) -> MemoryRecord:
        """合并聚类为一条浓缩记忆。无需LLM·纯启发式。

        规则：取importance最高的content + avg_importance×1.1
        """
        best = max(cluster, key=lambda r: r.importance)
        avg_imp = sum(r.importance for r in cluster) / len(cluster)
        merged_id = f"merged_{best.id}"
        # 摘要：用最长内容或importance最高者
        merged = MemoryRecord(
            id=merged_id,
            content=best.content,
            mtype=best.mtype,
            importance=min(1.0, avg_imp * 1.1),
            timestamp=max(r.timestamp for r in cluster),
            source="merged",
            tags=list({t for r in cluster for t in r.tags}),
            parent_id=best.id,
        )
        logger.info(
            "memory_evolution.merged",
            cluster_size=len(cluster),
            merged_id=merged_id,
            boost_imp=round(merged.importance, 3),
        )
        return merged

    def deduplicate(self, records: list[MemoryRecord]) -> list[MemoryRecord]:
        """去重：Jaccard >= 0.85 的视为重复。"""
        seen: list[MemoryRecord] = []
        for r in records:
            is_dup = any(self.jaccard_similarity(r.content, s.content) >= 0.85 for s in seen)
            if not is_dup:
                seen.append(r)
        return seen

    def flush_buffer(self) -> list[str]:
        """清空缓冲区，返回内容列表。"""
        items = list(self.buffer)
        self.buffer.clear()
        self.buffer_tokens = 0
        return items


# ===========================================================================
# Reconstruction Engine
# ===========================================================================


class ReconstructionEngine:
    """重构引擎：反思提炼高层洞察 + 实体关系提取。"""

    def __init__(self, forgetting: ForgettingEngine):
        self.forgetting = forgetting
        self.entities: dict[str, set[str]] = {}  # 实体 → 关联实体集

    def reflect_on_recent(self, days: int = 3, top_k: int = 20) -> list[str]:
        """反思最近N天的记忆，生成高层洞察（无需LLM·启发式）。

        Returns:
            insight strings
        """
        recs = self.forgetting.list_active(limit=top_k)
        if not recs:
            return []

        insights = []
        # 按认知类型聚合
        by_type: dict[MemoryType, list[MemoryRecord]] = {}
        for r in recs:
            by_type.setdefault(r.mtype, []).append(r)

        # 每种类型提取一条insight
        for mtype, items in by_type.items():
            if len(items) >= 2:
                avg_imp = sum(r.importance for r in items) / len(items)
                topics = [t for r in items for t in r.tags][:5]
                insights.append(
                    f"[{mtype.value}] {len(items)}条记忆·均重要度{avg_imp:.2f}·"
                    f"主题:{','.join(topics[:3])}"
                )

        return insights[:5]

    def extract_relations(self, records: list[MemoryRecord]) -> list[tuple[str, str, str]]:
        """实体关系提取（主体-关系-客体 三元组·启发式）。

        基于标签共现和importance关联。
        """
        relations = []
        for i, r1 in enumerate(records):
            for j, r2 in enumerate(records):
                if i >= j:
                    continue
                common_tags = set(r1.tags) & set(r2.tags)
                if common_tags:
                    rel = "related_to" if len(common_tags) == 1 else "strongly_related_to"
                    # 用第一条tag作为关系锚
                    anchor = list(common_tags)[0]
                    relations.append((r1.id, rel, r2.id))

                    # 更新实体图
                    self.entities.setdefault(r1.id, set()).add(r2.id)
                    self.entities.setdefault(r2.id, set()).add(r1.id)
        return relations[:100]

    def reinforce_on_access(self, record_id: str):
        self.forgetting.reinforce_access(record_id)

    def find_related(self, record_id: str, max_depth: int = 2) -> list[str]:
        """查找关联记忆（BFS，最多2跳）。"""
        if record_id not in self.entities:
            return []
        visited = {record_id}
        frontier = set(self.entities[record_id])
        result = list(frontier)
        for _ in range(max_depth - 1):
            next_frontier = set()
            for nid in frontier:
                if nid in self.entities:
                    for nn in self.entities[nid]:
                        if nn not in visited:
                            visited.add(nn)
                            next_frontier.add(nn)
                            result.append(nn)
            frontier = next_frontier
        return result


# ===========================================================================
# Hybrid Retriever
# ===========================================================================


class HybridRetriever:
    """混合召回：BM25(稀疏) + 向量(语义) → RRF 融合 → Cross-Encoder 重排 → MMR 多样性。

    因果召回通道（V5.0新增）：
      当提供 causal_context（当前因果链上的事件ID集合）时，
      携带 cause_event_id 且在因果链上的记忆获得分数加成，
      实现"果形成时，相关的因自然涌现"。
    """

    # RRF 常数：Elasticsearch 生产默认值 k=60
    RRF_K = 60
    # 多样性权衡：λ 越高越看重相关性
    MMR_LAMBDA = 0.7
    # 因果加成权重：因果链上的记忆获得此比例的额外分数
    CAUSAL_BOOST_RATIO = 0.3

    def __init__(
        self,
        forgetting: ForgettingEngine,
        vector_weight: float = 0.3,
        cross_encoder_enabled: bool = False,
    ):
        self.forgetting = forgetting
        # 兼容旧接口：保留 vector_weight，但内部默认走 RRF
        self.vw = vector_weight
        self.bw = 1.0 - vector_weight
        self.cross_encoder_enabled = cross_encoder_enabled

    @staticmethod
    def bm25_score(
        query: str, doc: str, avg_len: float = 100.0, k1: float = 1.2, b: float = 0.75
    ) -> float:
        """简化BM25评分（bigram级）。"""
        q_terms = {query[i : i + 2].lower() for i in range(len(query) - 1)}
        d_lower = doc.lower()
        d_terms = {d_lower[i : i + 2] for i in range(len(d_lower) - 1)}

        if not q_terms or not d_terms:
            return 0.0

        doc_len = len(d_terms)
        score = 0.0
        for t in q_terms:
            tf = d_lower.count(t)
            if tf == 0:
                continue
            idf = 1.0  # 简化：不计算全库IDF
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * doc_len / avg_len)
            score += idf * numerator / denominator
        return score

    @staticmethod
    def vector_similarity(query: str, record: MemoryRecord) -> float:
        """简化语义相似度：tag 匹配 + content 包含度 + 语义启发。"""
        q_terms = set(query.lower().split())
        tag_match = len(q_terms & {t.lower() for t in record.tags})
        base = min(1.0, tag_match / max(len(record.tags), 1) + 0.3)
        # 内容包含度
        content_lower = record.content.lower()
        content_hits = sum(1 for t in q_terms if t in content_lower)
        content_boost = min(0.3, content_hits / max(len(q_terms), 1) * 0.3)
        return min(1.0, base + content_boost)

    @staticmethod
    def reciprocal_rank_fusion(ranked_lists: list[list[str]], k: int = 60) -> dict[str, float]:
        """
        RRF：将多个排序列表融合为统一分数。
        score(d) = Σ 1 / (k + rank(d, list))
        """
        from collections import defaultdict

        scores: dict[str, float] = defaultdict(float)
        for lst in ranked_lists:
            for rank, doc_id in enumerate(lst, start=1):
                scores[doc_id] += 1.0 / (k + rank)
        return dict(scores)

    def search(
        self,
        query: str,
        top_k: int = 10,
        time_decay: bool = True,
        use_rrf: bool = True,
        use_mmr: bool = True,
        causal_context: set[str] | None = None,
    ) -> list[MemoryRecord]:
        """混合检索入口：RRF 融合 + Cross-Encoder 重排 + MMR 多样性 + 因果加成。

        Args:
            causal_context: 当前因果链上的事件ID集合。
                           提供时，携带 cause_event_id 且在此集合中的记忆获得分数加成，
                           实现"因果涌现"——果形成时相关的因自然浮现。
        """
        candidates = self.forgetting.list_active(limit=200)
        if not candidates:
            return []

        now = time.time()

        if use_rrf:
            # 分别生成 BM25 排序 和 向量排序，然后用 RRF 融合
            bm25_ranked = sorted(
                candidates,
                key=lambda r: self.bm25_score(query, r.content),
                reverse=True,
            )[:200]
            vector_ranked = sorted(
                candidates,
                key=lambda r: self.vector_similarity(query, r),
                reverse=True,
            )[:200]

            bm25_list = [r.id for r in bm25_ranked]
            vector_list = [r.id for r in vector_ranked]
            rrf_scores = self.reciprocal_rank_fusion([bm25_list, vector_list], k=self.RRF_K)

            # 应用时间衰减
            if time_decay:
                for r in candidates:
                    days = (now - r.timestamp) / 86400.0
                    decay = math.exp(-0.005 * days)
                    rrf_scores[r.id] = rrf_scores.get(r.id, 0.0) * decay

            # 因果加成：因果链上的记忆获得分数提升
            if causal_context:
                for r in candidates:
                    if r.cause_event_id and r.cause_event_id in causal_context:
                        rrf_scores[r.id] = rrf_scores.get(r.id, 0.0) * (
                            1.0 + self.CAUSAL_BOOST_RATIO
                        )

            scored = [(r, rrf_scores.get(r.id, 0.0)) for r in candidates]
            scored.sort(key=lambda x: x[1], reverse=True)
        else:
            # 兼容旧 7:3 融合
            scored = []
            for r in candidates:
                bm25 = self.bm25_score(query, r.content)
                vector_sim = self.vector_similarity(query, r)
                raw_score = self.bw * bm25 + self.vw * vector_sim
                if time_decay:
                    days = (now - r.timestamp) / 86400.0
                    decay = math.exp(-0.005 * days)
                    raw_score *= decay
                # 因果加成（兼容旧路径）
                if causal_context and r.cause_event_id and r.cause_event_id in causal_context:
                    raw_score *= 1.0 + self.CAUSAL_BOOST_RATIO
                scored.append((r, raw_score))
            scored.sort(key=lambda x: x[1], reverse=True)

        # 取前 top_k * 2 进入 Cross-Encoder / MMR
        pre_top = scored[: max(top_k * 2, 20)]

        if self.cross_encoder_enabled:
            pre_top = self._cross_encoder_rerank(query, pre_top)

        if use_mmr:
            ranked = self._mmr_rerank(pre_top, top_k, lam=self.MMR_LAMBDA)
        else:
            ranked = pre_top[:top_k]

        return [r for r, _ in ranked]

    def _cross_encoder_rerank(
        self,
        query: str,
        scored: list[tuple[MemoryRecord, float]],
    ) -> list[tuple[MemoryRecord, float]]:
        """
        Cross-Encoder 重排序接口。
        未安装 sentence-transformers 时使用启发式 fallback。
        """
        try:
            from sentence_transformers import CrossEncoder

            model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
            pairs = [(query, r.content) for r, _ in scored]
            ce_scores = model.predict(pairs)
            reranked = sorted(
                zip(scored, ce_scores),
                key=lambda x: x[1],
                reverse=True,
            )
            return [item[0] for item in reranked]
        except Exception:
            logger.debug("memory_evolution.cross_encoder_unavailable", fallback="heuristic")

            # fallback：用 query 在 content 中的词覆盖度做简单重排
            def _coverage(pair):
                r, _ = pair
                q_terms = set(query.lower().split())
                if not q_terms:
                    return 0.0
                hits = sum(1 for t in q_terms if t in r.content.lower())
                return hits / len(q_terms)

            return sorted(scored, key=_coverage, reverse=True)

    def _mmr_rerank(
        self, scored: list[tuple[MemoryRecord, float]], top_k: int, lam: float = MMR_LAMBDA
    ) -> list[tuple[MemoryRecord, float]]:
        """MMR贪心重排：λ·相关性 - (1-λ)·最大相似度。"""
        if len(scored) <= 1:
            return scored[:top_k]

        selected = [scored[0]]
        remaining = scored[1:]

        while len(selected) < min(top_k, len(scored)):
            best_idx = 0
            best_score = -float("inf")
            for i, (rec, rel) in enumerate(remaining):
                max_sim = max(
                    MergingEngine.jaccard_similarity(rec.content, sel_rec.content)
                    for sel_rec, _ in selected
                )
                mmr = lam * rel - (1 - lam) * max_sim
                if mmr > best_score:
                    best_score = mmr
                    best_idx = i
            selected.append(remaining.pop(best_idx))

        return selected


# ===========================================================================
# Evolution Orchestrator
# ===========================================================================


class MemoryEvolutionOrchestra:
    """记忆演化总指挥：三引擎联动 + 周期触发。"""

    def __init__(self, db_path: str = ":memory:"):
        self.forgetting = ForgettingEngine(db_path)
        self.merging = MergingEngine()
        self.reconstruction = ReconstructionEngine(self.forgetting)
        self.retriever = HybridRetriever(self.forgetting)

    def ingest(
        self,
        content: str,
        mtype: MemoryType = MemoryType.EPISODIC,
        importance: float = DEFAULT_IMPORTANCE,
        source: str = "",
        tags: list[str] | None = None,
    ) -> MemoryRecord:
        """摄入一条新记忆。"""
        rec = MemoryRecord(
            id=f"mem_{int(time.time() * 1000)}_{hash(content) % 10000:04d}",
            content=content,
            mtype=mtype,
            importance=importance,
            source=source,
            tags=tags or [],
        )
        self.forgetting.add(rec)
        self.merging.add_to_buffer(content)

        # 触发条件检查
        if self.merging.should_merge():
            self._run_merge_cycle()

        return rec

    def _run_merge_cycle(self):
        """执行合并周期。"""
        candidates = self.forgetting.list_active(limit=100)
        if len(candidates) < 3:
            self.merging.flush_buffer()
            return

        clusters = self.merging.cluster(candidates)
        merged_count = 0
        for cluster in clusters:
            if len(cluster) >= 2:
                merged = self.merging.compress_cluster(cluster)
                self.forgetting.add(merged)
                # 标记原条目为遗忘
                for old in cluster:
                    self.forgetting.forget_and_archive(old.id, "merged")
                merged_count += 1

        self.merging.flush_buffer()
        logger.info("memory_evolution.merge_cycle", clusters=len(clusters), merged=merged_count)

    def run_daily_maintenance(self):
        """每日维护：遗忘扫描 + 反思提炼。"""
        # 遗忘扫描
        forgotten = self.forgetting.decay_scan()
        for rec in forgotten:
            self.forgetting.forget_and_archive(rec.id, "decay")

        # 去重检查
        active = self.forgetting.list_active(limit=100)
        deduped = self.merging.deduplicate(active)
        removed = len(active) - len(deduped)
        if removed > 0:
            for r in active:
                if r not in deduped:
                    self.forgetting.forget_and_archive(r.id, "duplicate")

        # 反思提炼
        insights = self.reconstruction.reflect_on_recent()
        relations = self.reconstruction.extract_relations(deduped)

        logger.info(
            "memory_evolution.daily_maintenance",
            forgotten=len(forgotten),
            dedup_removed=removed,
            insights=len(insights),
            relations=len(relations),
        )

        return {
            "forgotten": len(forgotten),
            "dedup_removed": removed,
            "insights": insights,
            "relations_count": len(relations),
        }


# ===========================================================================
# ReMe Compression Engine (融优自 CoPaw/阿里)
# ===========================================================================


class ReMeCompressor:
    """ReMe 记忆压缩引擎 — 对话自动压缩 + 结构化摘要 + 持久化召回。

    融优来源：阿里 CoPaw ReMe (Recursive Memory compression)
    核心思想：长对话自动压缩为 key:value 摘要，下次自动想起，省 token。

    工作流：
      1. remember(key, value)  → 存储一条结构化记忆
      2. recall(query)         → 召回相关记忆（混合检索）
      3. auto_compress(msgs)   → 自动压缩超阈值对话
      4. forget(key)           → 主动遗忘

    特点：
      - 无需 LLM，纯启发式压缩（关键词提取 + 主题聚类）
      - 与 MemoryEvolutionOrchestra 联动，压缩后自动入库
      - 支持 TTL（生存时间），过期自动遗忘
    """

    # 压缩触发阈值
    COMPRESS_TOKEN_THRESHOLD = 3000  # token 估算超过此值触发压缩
    COMPRESS_MSG_THRESHOLD = 15  # 消息条数超过此值触发压缩
    MAX_SUMMARY_ITEMS = 20  # 单次压缩最多保留20条 key:value

    # 关键信息提取模式
    DECISION_PATTERN = re.compile(
        r"(决定|decision|chose|选择|确定|adopted|采用|方案[ABCA])[:：]\s*(.+)",
        re.IGNORECASE,
    )
    PREFERENCE_PATTERN = re.compile(
        r"(偏好|prefer|喜欢|like|习惯|habit)[:：]\s*(.+)",
        re.IGNORECASE,
    )
    COMMIT_PATTERN = re.compile(
        r"(承诺|commit|必须|must|需要|need|todo|待办)[:：]\s*(.+)",
        re.IGNORECASE,
    )
    FACT_PATTERN = re.compile(
        r"(事实|fact|注意|note|注意点|关键点)[:：]\s*(.+)",
        re.IGNORECASE,
    )

    def __init__(
        self, orchestra: Optional["MemoryEvolutionOrchestra"] = None, db_path: str = ":memory:"
    ):
        self.orchestra = orchestra
        self._db_path = db_path
        # 持久连接：:memory: 模式下必须复用同一连接
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._init_reme_schema()
        self._buffer: list[dict[str, str]] = []  # 未压缩消息缓冲
        self._buffer_tokens: int = 0

    def _init_reme_schema(self):
        """初始化 ReMe 专用表。"""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS reme_memories (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                importance REAL DEFAULT 0.7,
                created_at REAL,
                last_recalled REAL DEFAULT 0,
                recall_count INTEGER DEFAULT 0,
                ttl_seconds REAL DEFAULT 0,
                source TEXT DEFAULT 'auto'
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reme_category
            ON reme_memories(category)
        """)
        self._conn.commit()

    # ── 核心 API ───────────────────────────────────────────────

    def remember(
        self,
        key: str,
        value: str,
        category: str = "general",
        importance: float = 0.7,
        ttl_seconds: float = 0,
        source: str = "manual",
    ) -> bool:
        """存储一条结构化记忆。

        Args:
            key: 记忆键（如 "user_prefers_dark_mode"）
            value: 记忆值（如 "用户偏好暗色主题"）
            category: 分类（decision/preference/commit/fact/general）
            importance: 重要性 0.0-1.0
            ttl_seconds: 生存时间（秒），0=永不过期
            source: 来源（manual/auto_compress/conversation）

        Returns:
            True=存储成功
        """
        if not key or not value:
            return False

        self._conn.execute(
            """INSERT OR REPLACE INTO reme_memories
               (key, value, category, importance, created_at, ttl_seconds, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key, value, category, importance, time.time(), ttl_seconds, source),
        )
        self._conn.commit()

        # 同步到 MemoryEvolutionOrchestra
        if self.orchestra:
            self.orchestra.ingest(
                content=f"[{category}] {key}: {value}",
                mtype=MemoryType.SEMANTIC,
                importance=importance,
                source=f"reme:{source}",
                tags=[category, key],
            )

        logger.info("reme.remembered", key=key, category=category, importance=importance)
        return True

    def recall(
        self, query: str, top_k: int = 5, include_expired: bool = False
    ) -> list[dict[str, Any]]:
        """召回相关记忆（混合检索）。

        Args:
            query: 查询文本
            top_k: 返回前K条
            include_expired: 是否包含过期记忆

        Returns:
            记忆列表，每条含 key/value/category/importance/score
        """
        # 获取所有有效记忆
        now = time.time()
        if include_expired:
            rows = self._conn.execute(
                "SELECT * FROM reme_memories ORDER BY importance DESC LIMIT 200"
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM reme_memories
                   WHERE ttl_seconds = 0 OR created_at + ttl_seconds > ?
                   ORDER BY importance DESC LIMIT 200""",
                (now,),
            ).fetchall()

        if not rows:
            return []

        # 混合评分：BM25 + tag匹配 + 时间衰减
        scored = []
        for row in rows:
            key, value, category, importance, created, last_recalled, recall_count, ttl, source = (
                row
            )

            content = f"{key} {value}"
            bm25 = HybridRetriever.bm25_score(query, content)
            tag_match = 1.0 if query.lower() in content.lower() else 0.0

            # 时间衰减
            days = (now - created) / 86400.0
            decay = math.exp(-0.003 * days)

            # 综合分
            score = (0.5 * bm25 + 0.3 * tag_match + 0.2 * importance) * decay
            scored.append((row, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        results = []
        for row, score in scored[:top_k]:
            key, value, category, importance, created, last_recalled, recall_count, ttl, source = (
                row
            )

            # 更新召回计数
            self._conn.execute(
                """UPDATE reme_memories
                   SET recall_count = recall_count + 1, last_recalled = ?
                   WHERE key = ?""",
                (now, key),
            )
            self._conn.commit()

            results.append(
                {
                    "key": key,
                    "value": value,
                    "category": category,
                    "importance": importance,
                    "score": round(score, 4),
                    "recall_count": recall_count + 1,
                    "source": source,
                }
            )

        return results

    def forget(self, key: str) -> bool:
        """主动遗忘一条记忆。"""
        cursor = self._conn.execute("DELETE FROM reme_memories WHERE key = ?", (key,))
        deleted = cursor.rowcount
        self._conn.commit()

        if deleted > 0:
            logger.info("reme.forgotten", key=key)
            return True
        return False

    def list_all(self, category: str = "") -> list[dict[str, Any]]:
        """列出所有记忆（可选按分类过滤）。"""
        if category:
            rows = self._conn.execute(
                "SELECT key, value, category, importance, created_at, recall_count FROM reme_memories WHERE category = ? ORDER BY importance DESC",
                (category,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT key, value, category, importance, created_at, recall_count FROM reme_memories ORDER BY importance DESC"
            ).fetchall()

        return [
            {
                "key": r[0],
                "value": r[1],
                "category": r[2],
                "importance": r[3],
                "created_at": r[4],
                "recall_count": r[5],
            }
            for r in rows
        ]

    # ── 自动压缩 ───────────────────────────────────────────────

    def add_message(self, role: str, content: str):
        """添加一条对话消息到缓冲区。"""
        self._buffer.append({"role": role, "content": content})
        self._buffer_tokens += len(content) * 3 // 2  # 估算 token

        # 检查是否触发压缩
        if (
            self._buffer_tokens >= self.COMPRESS_TOKEN_THRESHOLD
            or len(self._buffer) >= self.COMPRESS_MSG_THRESHOLD
        ):
            self.auto_compress()

    def auto_compress(self) -> list[dict[str, str]]:
        """自动压缩缓冲区中的对话，提取关键信息为 key:value。

        无需 LLM，纯启发式：
          1. 正则匹配决策/偏好/承诺/事实
          2. 提取高频关键词作为 key
          3. 上下文片段作为 value

        Returns:
            压缩后的 key:value 列表
        """
        if not self._buffer:
            return []

        compressed = []
        all_text = "\n".join(m["content"] for m in self._buffer)

        # 1. 正则提取结构化信息
        patterns = [
            (self.DECISION_PATTERN, "decision", 0.9),
            (self.PREFERENCE_PATTERN, "preference", 0.8),
            (self.COMMIT_PATTERN, "commit", 0.85),
            (self.FACT_PATTERN, "fact", 0.7),
        ]

        for pattern, category, importance in patterns:
            for match in pattern.finditer(all_text):
                key_raw = match.group(1).strip().lower().replace(" ", "_")
                value = match.group(2).strip()[:200]  # 截断长值
                key = f"{category}_{key_raw}_{hash(value) % 10000:04d}"

                self.remember(key, value, category, importance, source="auto_compress")
                compressed.append({"key": key, "value": value, "category": category})

        # 2. 高频关键词提取（补充正则未覆盖的内容）
        words = re.findall(r"[\u4e00-\u9fff]{2,6}|[a-zA-Z_]{3,20}", all_text)
        word_freq: dict[str, int] = {}
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1

        # 取 Top-5 高频词作为 general 记忆
        top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:5]
        for word, freq in top_words:
            if freq >= 2:  # 至少出现2次
                key = f"topic_{word}"
                # 找到包含该词的上下文
                context = ""
                for msg in self._buffer:
                    if word in msg["content"]:
                        idx = msg["content"].find(word)
                        start = max(0, idx - 30)
                        end = min(len(msg["content"]), idx + len(word) + 50)
                        context = msg["content"][start:end]
                        break

                if context:
                    self.remember(key, context, "topic", 0.5, source="auto_compress")
                    compressed.append({"key": key, "value": context, "category": "topic"})

        # 3. 清空缓冲区
        msg_count = len(self._buffer)
        self._buffer.clear()
        self._buffer_tokens = 0

        logger.info("reme.auto_compressed", messages=msg_count, extracted=len(compressed))

        return compressed[: self.MAX_SUMMARY_ITEMS]

    # ── 维护 ───────────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """清理过期记忆，返回清理数量。"""
        now = time.time()
        cursor = self._conn.execute(
            """DELETE FROM reme_memories
               WHERE ttl_seconds > 0 AND created_at + ttl_seconds <= ?""",
            (now,),
        )
        deleted = cursor.rowcount
        self._conn.commit()

        if deleted > 0:
            logger.info("reme.cleanup_expired", deleted=deleted)
        return deleted

    def stats(self) -> dict[str, Any]:
        """返回 ReMe 统计信息。"""
        total = self._conn.execute("SELECT COUNT(*) FROM reme_memories").fetchone()[0]
        by_category = self._conn.execute(
            "SELECT category, COUNT(*) FROM reme_memories GROUP BY category"
        ).fetchall()
        avg_importance = (
            self._conn.execute("SELECT AVG(importance) FROM reme_memories").fetchone()[0] or 0.0
        )

        return {
            "total_memories": total,
            "by_category": dict(by_category),
            "avg_importance": round(avg_importance, 3),
            "buffer_messages": len(self._buffer),
            "buffer_tokens": self._buffer_tokens,
        }
