"""
记忆演化引擎 — 遗忘·合并·重构 三合一。

融优来源：
  OpenMemory 认知分类衰退(5级λ) + SimpleMem 三阶段压缩(43.24% F1)
  + OpenClaw 混合召回(BM25+向量7:3) + Memobase 批处理成本优化

设计原则：
  仿生优于机械 · 韧性优于性能 · 混合优于单一 · 务实优于完美

三引擎联动：事件流入 → 遗忘引擎递减salience → 合并引擎聚类压缩 → 重构引擎反思提炼
"""

import math
import re
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Tuple, Set
from collections import OrderedDict

import structlog

logger = structlog.get_logger("bridge_v7.memory_evolution")


# ===========================================================================
# Cognitive decay rates (OpenMemory 2025)
# ===========================================================================

class MemoryType(str, Enum):
    REFLECTIVE = "reflective"    # 核心洞察 λ=0.001 半衰期693天
    SEMANTIC = "semantic"        # 技术知识 λ=0.005 半衰期138天
    PROCEDURAL = "procedural"    # 操作流程 λ=0.008 半衰期86天
    EPISODIC = "episodic"        # 对话记录 λ=0.015 半衰期46天
    EMOTIONAL = "emotional"      # 用户偏好 λ=0.020 半衰期34天

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


COGNITIVE_DECAY_RATES: Dict[MemoryType, float] = {
    MemoryType.REFLECTIVE: 0.001,
    MemoryType.SEMANTIC: 0.005,
    MemoryType.PROCEDURAL: 0.008,
    MemoryType.EPISODIC: 0.015,
    MemoryType.EMOTIONAL: 0.020,
}

FORGET_THRESHOLD = 0.1            # salience < 0.1 → 标记遗忘
DEFAULT_IMPORTANCE = 0.7          # 新记忆默认重要性
BOOST_ON_ACCESS = 0.1             # 访问时salience增量
EVERGREEN_PATTERNS = [
    r"MEMORY\.md", r"SOUL\.md", r"IDENTITY\.md",
    r"patterns\.md", r"USER\.md", r"\.workbuddy/memory/MEMORY\.md",
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
    tags: List[str] = field(default_factory=list)
    parent_id: Optional[str] = None   # 合并来源
    is_evergreen: bool = False
    archived: bool = False

    def salience(self, now: Optional[float] = None) -> float:
        """计算当前衰减后显著度。salience = importance × e^(-λ × days)。"""
        if self.is_evergreen:
            return self.importance
        now = now or time.time()
        days = (now - self.timestamp) / 86400.0
        lam = COGNITIVE_DECAY_RATES.get(self.mtype, 0.005)
        return self.importance * math.exp(-lam * days)

    def should_forget(self, now: Optional[float] = None) -> bool:
        return self.salience(now) < FORGET_THRESHOLD and not self.is_evergreen

    def reinforce(self):
        """访问强化。"""
        self.access_count += 1
        self.last_access = time.time()
        self.importance = min(1.0, self.importance + BOOST_ON_ACCESS)
        # 重置时间戳：让衰减重新计 — 仿生设计
        self.timestamp = time.time()


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
                archived INTEGER DEFAULT 0
            )
        """)
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
                source,tags,parent_id,is_evergreen,archived)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (record.id, record.content, record.mtype.value, record.importance,
             record.timestamp, record.access_count, record.last_access,
             record.source, json.dumps(record.tags), record.parent_id,
             int(record.is_evergreen), int(record.archived)),
        )
        conn.commit()
        conn.close()

    def get(self, record_id: str) -> Optional[MemoryRecord]:
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
            id=row[0], content=row[1], mtype=MemoryType(row[2]),
            importance=row[3], timestamp=row[4], access_count=row[5],
            last_access=row[6], source=row[7],
            tags=json.loads(row[8]) if row[8] else [],
            parent_id=row[9], is_evergreen=bool(row[10]),
            archived=bool(row[11]),
        )

    def decay_scan(self) -> List[MemoryRecord]:
        """扫描所有需遗忘的记忆。"""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT * FROM memories WHERE archived=0 AND is_evergreen=0"
        ).fetchall()
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
        row = conn.execute(
            "SELECT * FROM memories WHERE id=?", (record_id,)
        ).fetchone()
        if not row:
            conn.close()
            return
        rec = self._row_to_record(row)
        conn.execute(
            """INSERT OR REPLACE INTO cold_store
               (id,content,mtype,importance,timestamp,archived_at,reason)
               VALUES (?,?,?,?,?,?,?)""",
            (rec.id, rec.content, rec.mtype.value, rec.importance,
             rec.timestamp, time.time(), reason),
        )
        conn.execute(
            "UPDATE memories SET archived=1 WHERE id=?", (record_id,)
        )
        conn.commit()
        conn.close()
        logger.info("memory_evolution.forgot",
                    record_id=record_id, reason=reason,
                    salience=round(rec.salience(), 4))

    def reinforce_access(self, record_id: str):
        rec = self.get(record_id)
        if rec:
            rec.reinforce()
            self.add(rec)

    def list_active(self, limit: int = 50) -> List[MemoryRecord]:
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
        self.buffer: List[str] = []
        self.buffer_tokens: int = 0
        self.buffer_token_limit = buffer_token_limit

    @staticmethod
    def jaccard_similarity(a: str, b: str) -> float:
        """Jaccard相似度（bigram级，务实优于完美）。"""
        def bigrams(s):
            s = s.lower()
            return {s[i:i+2] for i in range(len(s)-1)}

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

    def cluster(self, records: List[MemoryRecord]) -> List[List[MemoryRecord]]:
        """简单贪心聚类（替代DBSCAN·无外部依赖）。"""
        if len(records) <= 1:
            return [[r] for r in records]

        clusters: List[List[MemoryRecord]] = []
        assigned: Set[int] = set()

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

    def compress_cluster(self, cluster: List[MemoryRecord]) -> MemoryRecord:
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
        logger.info("memory_evolution.merged",
                    cluster_size=len(cluster),
                    merged_id=merged_id,
                    boost_imp=round(merged.importance, 3))
        return merged

    def deduplicate(self, records: List[MemoryRecord]) -> List[MemoryRecord]:
        """去重：Jaccard >= 0.85 的视为重复。"""
        seen: List[MemoryRecord] = []
        for r in records:
            is_dup = any(
                self.jaccard_similarity(r.content, s.content) >= 0.85
                for s in seen
            )
            if not is_dup:
                seen.append(r)
        return seen

    def flush_buffer(self) -> List[str]:
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
        self.entities: Dict[str, Set[str]] = {}   # 实体 → 关联实体集

    def reflect_on_recent(self, days: int = 3, top_k: int = 20) -> List[str]:
        """反思最近N天的记忆，生成高层洞察（无需LLM·启发式）。

        Returns:
            insight strings
        """
        recs = self.forgetting.list_active(limit=top_k)
        if not recs:
            return []

        insights = []
        # 按认知类型聚合
        by_type: Dict[MemoryType, List[MemoryRecord]] = {}
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

    def extract_relations(self, records: List[MemoryRecord]) -> List[Tuple[str, str, str]]:
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
                    rel = ("related_to" if len(common_tags) == 1
                           else "strongly_related_to")
                    # 用第一条tag作为关系锚
                    anchor = list(common_tags)[0]
                    relations.append((r1.id, rel, r2.id))

                    # 更新实体图
                    self.entities.setdefault(r1.id, set()).add(r2.id)
                    self.entities.setdefault(r2.id, set()).add(r1.id)
        return relations[:100]

    def reinforce_on_access(self, record_id: str):
        self.forgetting.reinforce_access(record_id)

    def find_related(self, record_id: str, max_depth: int = 2) -> List[str]:
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
    """混合召回：BM25(稀疏) + 向量(语义) → RRF 融合 → Cross-Encoder 重排 → MMR 多样性。"""

    # RRF 常数：Elasticsearch 生产默认值 k=60
    RRF_K = 60
    # 多样性权衡：λ 越高越看重相关性
    MMR_LAMBDA = 0.7

    def __init__(self, forgetting: ForgettingEngine, vector_weight: float = 0.3,
                 cross_encoder_enabled: bool = False):
        self.forgetting = forgetting
        # 兼容旧接口：保留 vector_weight，但内部默认走 RRF
        self.vw = vector_weight
        self.bw = 1.0 - vector_weight
        self.cross_encoder_enabled = cross_encoder_enabled

    @staticmethod
    def bm25_score(query: str, doc: str, avg_len: float = 100.0,
                   k1: float = 1.2, b: float = 0.75) -> float:
        """简化BM25评分（bigram级）。"""
        q_terms = {query[i:i+2].lower() for i in range(len(query)-1)}
        d_lower = doc.lower()
        d_terms = {d_lower[i:i+2] for i in range(len(d_lower)-1)}

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
    def reciprocal_rank_fusion(ranked_lists: List[List[str]], k: int = 60) -> Dict[str, float]:
        """
        RRF：将多个排序列表融合为统一分数。
        score(d) = Σ 1 / (k + rank(d, list))
        """
        from collections import defaultdict
        scores: Dict[str, float] = defaultdict(float)
        for lst in ranked_lists:
            for rank, doc_id in enumerate(lst, start=1):
                scores[doc_id] += 1.0 / (k + rank)
        return dict(scores)

    def search(self, query: str, top_k: int = 10,
               time_decay: bool = True,
               use_rrf: bool = True,
               use_mmr: bool = True) -> List[MemoryRecord]:
        """混合检索入口：RRF 融合 + Cross-Encoder 重排 + MMR 多样性。"""
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

    def _cross_encoder_rerank(self, query: str,
                              scored: List[Tuple[MemoryRecord, float]],
                              ) -> List[Tuple[MemoryRecord, float]]:
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
            logger.debug("memory_evolution.cross_encoder_unavailable",
                         fallback="heuristic")
            # fallback：用 query 在 content 中的词覆盖度做简单重排
            def _coverage(pair):
                r, _ = pair
                q_terms = set(query.lower().split())
                if not q_terms:
                    return 0.0
                hits = sum(1 for t in q_terms if t in r.content.lower())
                return hits / len(q_terms)

            return sorted(scored, key=_coverage, reverse=True)

    def _mmr_rerank(self, scored: List[Tuple[MemoryRecord, float]],
                    top_k: int, lam: float = MMR_LAMBDA) -> List[Tuple[MemoryRecord, float]]:
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

    def ingest(self, content: str, mtype: MemoryType = MemoryType.EPISODIC,
               importance: float = DEFAULT_IMPORTANCE,
               source: str = "", tags: Optional[List[str]] = None) -> MemoryRecord:
        """摄入一条新记忆。"""
        rec = MemoryRecord(
            id=f"mem_{int(time.time()*1000)}_{hash(content)%10000:04d}",
            content=content, mtype=mtype, importance=importance,
            source=source, tags=tags or [],
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
        logger.info("memory_evolution.merge_cycle",
                    clusters=len(clusters), merged=merged_count)

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

        logger.info("memory_evolution.daily_maintenance",
                    forgotten=len(forgotten),
                    dedup_removed=removed,
                    insights=len(insights),
                    relations=len(relations))

        return {
            "forgotten": len(forgotten),
            "dedup_removed": removed,
            "insights": insights,
            "relations_count": len(relations),
        }
