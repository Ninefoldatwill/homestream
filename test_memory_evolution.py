"""
test_memory_evolution.py — 记忆演化引擎测试

覆盖范围：
- MemoryType 认知分类与 from_category 映射
- MemoryRecord salience 衰减与 should_forget
- ForgettingEngine 增删查 + 衰减扫描 + 冷存储归档
- MergingEngine Jaccard相似度 + 聚类 + 去重 + 批处理缓冲
- ReconstructionEngine 反思提炼 + 实体关系 + BFS关联查找
- HybridRetriever BM25 + 向量相似 + RRF融合 + MMR多样性
- MemoryEvolutionOrchestra 三引擎联动 + ingest + 日维护
- ReMeCompressor 记忆压缩 + recall + auto_compress + TTL

注意：ForgettingEngine 每次 op 开关新连接，:memory: 模式下数据丢失。
      测试用文件路径替代（work_dir / *.db）。
"""

import time

from memory_evolution import (
    COGNITIVE_DECAY_RATES,
    ForgettingEngine,
    HybridRetriever,
    MemoryEvolutionOrchestra,
    MemoryRecord,
    MemoryType,
    MergingEngine,
    ReconstructionEngine,
    ReMeCompressor,
)

# ============================================================
# MemoryType 测试
# ============================================================


class TestMemoryType:
    """认知分类枚举测试。"""

    def test_enum_values(self):
        """5种认知类型。"""
        assert len(MemoryType) == 5
        assert MemoryType.REFLECTIVE.value == "reflective"
        assert MemoryType.SEMANTIC.value == "semantic"
        assert MemoryType.PROCEDURAL.value == "procedural"
        assert MemoryType.EPISODIC.value == "episodic"
        assert MemoryType.EMOTIONAL.value == "emotional"

    def test_from_category_reflective(self):
        assert MemoryType.from_category("insight") == MemoryType.REFLECTIVE
        assert MemoryType.from_category("reflective") == MemoryType.REFLECTIVE

    def test_from_category_semantic(self):
        assert MemoryType.from_category("knowledge") == MemoryType.SEMANTIC
        assert MemoryType.from_category("semantic") == MemoryType.SEMANTIC

    def test_from_category_procedural(self):
        assert MemoryType.from_category("workflow") == MemoryType.PROCEDURAL

    def test_from_category_unknown(self):
        assert MemoryType.from_category("unknown_xyz") == MemoryType.SEMANTIC

    def test_decay_rates_ordered(self):
        """衰减率递增：reflective < semantic < procedural < episodic < emotional。"""
        rates = [
            COGNITIVE_DECAY_RATES[MemoryType.REFLECTIVE],
            COGNITIVE_DECAY_RATES[MemoryType.SEMANTIC],
            COGNITIVE_DECAY_RATES[MemoryType.PROCEDURAL],
            COGNITIVE_DECAY_RATES[MemoryType.EPISODIC],
            COGNITIVE_DECAY_RATES[MemoryType.EMOTIONAL],
        ]
        for i in range(len(rates) - 1):
            assert rates[i] < rates[i + 1]


# ============================================================
# MemoryRecord 测试
# ============================================================


class TestMemoryRecord:
    """记忆记录数据结构测试。"""

    def test_salience_fresh(self):
        """新记忆 salience ≈ importance。"""
        rec = MemoryRecord(id="t1", content="test", importance=0.8)
        assert abs(rec.salience() - 0.8) < 0.01

    def test_salience_evergreen(self):
        """evergreen 记忆不衰减。"""
        rec = MemoryRecord(id="t1", content="test", importance=0.9, is_evergreen=True, timestamp=0)
        assert rec.salience() == 0.9

    def test_salience_old_memory_decays(self):
        """老记忆显著度下降。"""
        old_time = time.time() - 86400 * 100
        rec = MemoryRecord(
            id="t1", content="test", importance=1.0, mtype=MemoryType.EPISODIC, timestamp=old_time
        )
        assert rec.salience() < 1.0

    def test_should_forget_old(self):
        """足够老且低重要度 → 应遗忘。"""
        old_time = time.time() - 86400 * 500
        rec = MemoryRecord(
            id="t1", content="test", importance=0.3, mtype=MemoryType.EPISODIC, timestamp=old_time
        )
        assert rec.should_forget()

    def test_should_not_forget_evergreen(self):
        """evergreen 永不遗忘。"""
        old_time = time.time() - 86400 * 1000
        rec = MemoryRecord(
            id="t1", content="test", importance=0.3, is_evergreen=True, timestamp=old_time
        )
        assert not rec.should_forget()

    def test_reinforce(self):
        """访问强化 → importance 增加。"""
        rec = MemoryRecord(id="t1", content="test", importance=0.5)
        old_imp = rec.importance
        rec.reinforce()
        assert rec.importance > old_imp
        assert rec.access_count == 1

    def test_reinforce_cap(self):
        """importance 上限1.0。"""
        rec = MemoryRecord(id="t1", content="test", importance=0.95)
        for _ in range(10):
            rec.reinforce()
        assert rec.importance <= 1.0


# ============================================================
# ForgettingEngine 测试（文件路径，非 :memory:）
# ============================================================


class TestForgettingEngine:
    """遗忘引擎测试。"""

    def test_add_and_get(self, work_dir):
        """增 + 查。"""
        engine = ForgettingEngine(str(work_dir / "fe.db"))
        rec = MemoryRecord(id="fe1", content="hello", importance=0.8)
        engine.add(rec)
        got = engine.get("fe1")
        assert got is not None
        assert got.content == "hello"

    def test_get_nonexistent(self, work_dir):
        """查不存在返回 None。"""
        engine = ForgettingEngine(str(work_dir / "fe.db"))
        assert engine.get("nonexistent") is None

    def test_list_active(self, work_dir):
        """列出活跃记忆。"""
        engine = ForgettingEngine(str(work_dir / "fe.db"))
        for i in range(5):
            engine.add(MemoryRecord(id=f"fe{i}", content=f"content{i}", importance=0.5 + i * 0.1))
        active = engine.list_active(limit=10)
        assert len(active) == 5
        assert active[0].importance >= active[-1].importance

    def test_forget_and_archive(self, work_dir):
        """遗忘 → 归档到冷存储。"""
        engine = ForgettingEngine(str(work_dir / "fe.db"))
        engine.add(MemoryRecord(id="fe1", content="test", importance=0.5))
        engine.forget_and_archive("fe1", "test_reason")
        assert engine.get("fe1") is None

    def test_reinforce_access(self, work_dir):
        """访问强化。"""
        engine = ForgettingEngine(str(work_dir / "fe.db"))
        engine.add(MemoryRecord(id="fe1", content="test", importance=0.5))
        engine.reinforce_access("fe1")
        rec = engine.get("fe1")
        assert rec.access_count == 1
        assert rec.importance > 0.5

    def test_is_evergreen_path(self, work_dir):
        """evergreen 路径检测。"""
        engine = ForgettingEngine(str(work_dir / "fe.db"))
        assert engine.is_evergreen_path("project/MEMORY.md")
        assert engine.is_evergreen_path("project/SOUL.md")
        assert engine.is_evergreen_path(".workbuddy/memory/MEMORY.md")
        assert not engine.is_evergreen_path("random/file.txt")

    def test_decay_scan(self, work_dir):
        """衰减扫描找到需遗忘记忆。"""
        engine = ForgettingEngine(str(work_dir / "fe.db"))
        old_time = time.time() - 86400 * 500
        engine.add(
            MemoryRecord(
                id="old1",
                content="old",
                importance=0.1,
                mtype=MemoryType.EPISODIC,
                timestamp=old_time,
            )
        )
        engine.add(MemoryRecord(id="new1", content="new", importance=0.9))
        candidates = engine.decay_scan()
        assert any(c.id == "old1" for c in candidates)
        assert not any(c.id == "new1" for c in candidates)


# ============================================================
# MergingEngine 测试
# ============================================================


class TestMergingEngine:
    """合并引擎测试。"""

    def test_jaccard_identical(self):
        assert MergingEngine.jaccard_similarity("hello world", "hello world") == 1.0

    def test_jaccard_different(self):
        assert MergingEngine.jaccard_similarity("abc", "xyz") == 0.0

    def test_jaccard_empty(self):
        assert MergingEngine.jaccard_similarity("", "test") == 0.0

    def test_jaccard_partial(self):
        score = MergingEngine.jaccard_similarity("hello world", "hello there")
        assert 0 < score < 1

    def test_token_count(self):
        assert MergingEngine.token_count("hello") > 0

    def test_add_to_buffer_and_should_merge(self):
        engine = MergingEngine(buffer_token_limit=10)
        engine.add_to_buffer("a" * 20)
        assert engine.should_merge() is True

    def test_flush_buffer(self):
        engine = MergingEngine()
        engine.add_to_buffer("content1")
        engine.add_to_buffer("content2")
        items = engine.flush_buffer()
        assert len(items) == 2
        assert engine.buffer_tokens == 0

    def test_cluster_single(self):
        engine = MergingEngine()
        recs = [MemoryRecord(id="m1", content="unique content")]
        clusters = engine.cluster(recs)
        assert len(clusters) == 1
        assert len(clusters[0]) == 1

    def test_cluster_similar(self):
        engine = MergingEngine(similarity_threshold=0.3)
        recs = [
            MemoryRecord(id="m1", content="hello world test"),
            MemoryRecord(id="m2", content="hello world demo"),
            MemoryRecord(id="m3", content="completely different xyz"),
        ]
        clusters = engine.cluster(recs)
        assert len(clusters) == 2

    def test_compress_cluster(self):
        engine = MergingEngine()
        cluster = [
            MemoryRecord(
                id="m1",
                content="best content",
                importance=0.9,
                mtype=MemoryType.SEMANTIC,
                tags=["a"],
            ),
            MemoryRecord(
                id="m2",
                content="other content",
                importance=0.5,
                mtype=MemoryType.SEMANTIC,
                tags=["b"],
            ),
        ]
        merged = engine.compress_cluster(cluster)
        assert merged.id == "merged_m1"
        assert merged.importance > 0.5
        assert merged.source == "merged"

    def test_deduplicate(self):
        engine = MergingEngine()
        recs = [
            MemoryRecord(id="m1", content="hello world test"),
            MemoryRecord(id="m2", content="hello world test"),
            MemoryRecord(id="m3", content="completely different"),
        ]
        deduped = engine.deduplicate(recs)
        assert len(deduped) == 2


# ============================================================
# ReconstructionEngine 测试
# ============================================================


class TestReconstructionEngine:
    """重构引擎测试。"""

    def test_reflect_empty(self, work_dir):
        """无记忆 → 无洞察。"""
        fe = ForgettingEngine(str(work_dir / "re.db"))
        re = ReconstructionEngine(fe)
        assert re.reflect_on_recent() == []

    def test_reflect_with_data(self, work_dir):
        """有记忆 → 生成洞察。"""
        fe = ForgettingEngine(str(work_dir / "re.db"))
        for i in range(5):
            fe.add(
                MemoryRecord(
                    id=f"r{i}",
                    content=f"semantic memory {i}",
                    mtype=MemoryType.SEMANTIC,
                    importance=0.8,
                    tags=["python", "coding"],
                )
            )
        re = ReconstructionEngine(fe)
        insights = re.reflect_on_recent()
        assert len(insights) > 0

    def test_extract_relations(self, work_dir):
        """实体关系提取。"""
        fe = ForgettingEngine(str(work_dir / "re.db"))
        re = ReconstructionEngine(fe)
        recs = [
            MemoryRecord(id="a", content="a", tags=["shared"]),
            MemoryRecord(id="b", content="b", tags=["shared"]),
        ]
        relations = re.extract_relations(recs)
        assert len(relations) > 0
        assert relations[0][0] == "a"
        assert relations[0][2] == "b"

    def test_find_related(self, work_dir):
        """BFS 关联查找。"""
        fe = ForgettingEngine(str(work_dir / "re.db"))
        re = ReconstructionEngine(fe)
        recs = [
            MemoryRecord(id="a", content="a", tags=["t1"]),
            MemoryRecord(id="b", content="b", tags=["t1"]),
            MemoryRecord(id="c", content="c", tags=["t1"]),
        ]
        re.extract_relations(recs)
        related = re.find_related("a", max_depth=2)
        assert "b" in related or "c" in related

    def test_find_related_nonexistent(self, work_dir):
        fe = ForgettingEngine(str(work_dir / "re.db"))
        re = ReconstructionEngine(fe)
        assert re.find_related("nonexistent") == []


# ============================================================
# HybridRetriever 测试
# ============================================================


class TestHybridRetriever:
    """混合召回测试。"""

    def test_bm25_score(self):
        assert HybridRetriever.bm25_score("hello", "hello world") > 0

    def test_bm25_no_match(self):
        assert HybridRetriever.bm25_score("xyz", "hello world") == 0.0

    def test_vector_similarity(self):
        rec = MemoryRecord(id="v1", content="python programming", tags=["python", "coding"])
        assert HybridRetriever.vector_similarity("python", rec) > 0

    def test_rrf_basic(self):
        list1 = ["a", "b", "c"]
        list2 = ["b", "c", "d"]
        scores = HybridRetriever.reciprocal_rank_fusion([list1, list2])
        assert scores["b"] > scores["a"]
        assert scores["c"] > scores["a"]

    def test_search_empty(self, work_dir):
        """空库搜索 → 空结果。"""
        fe = ForgettingEngine(str(work_dir / "hr.db"))
        retriever = HybridRetriever(fe)
        assert retriever.search("test") == []

    def test_search_with_data(self, work_dir):
        """有数据搜索 → 返回相关记忆。"""
        fe = ForgettingEngine(str(work_dir / "hr.db"))
        fe.add(
            MemoryRecord(
                id="s1", content="python programming tutorial", importance=0.9, tags=["python"]
            )
        )
        fe.add(
            MemoryRecord(id="s2", content="cooking recipe guide", importance=0.7, tags=["cooking"])
        )
        retriever = HybridRetriever(fe)
        results = retriever.search("python", top_k=2)
        assert len(results) > 0
        assert results[0].id == "s1"

    def test_search_mmr_diversity(self, work_dir):
        """MMR 多样性。"""
        fe = ForgettingEngine(str(work_dir / "hr.db"))
        fe.add(MemoryRecord(id="s1", content="python basics", importance=0.9, tags=["python"]))
        fe.add(MemoryRecord(id="s2", content="python basics", importance=0.9, tags=["python"]))
        fe.add(
            MemoryRecord(id="s3", content="javascript guide", importance=0.8, tags=["javascript"])
        )
        retriever = HybridRetriever(fe)
        results = retriever.search("python", top_k=2, use_mmr=True)
        assert len(results) <= 2


# ============================================================
# MemoryEvolutionOrchestra 测试
# ============================================================


class TestMemoryEvolutionOrchestra:
    """记忆演化总指挥测试。"""

    def test_init(self, work_dir):
        orch = MemoryEvolutionOrchestra(str(work_dir / "orch.db"))
        assert orch.forgetting is not None
        assert orch.merging is not None
        assert orch.reconstruction is not None
        assert orch.retriever is not None

    def test_ingest(self, work_dir):
        """摄入新记忆。"""
        orch = MemoryEvolutionOrchestra(str(work_dir / "orch.db"))
        rec = orch.ingest("test content", importance=0.8)
        assert rec.id.startswith("mem_")
        assert rec.content == "test content"
        got = orch.forgetting.get(rec.id)
        assert got is not None

    def test_run_daily_maintenance(self, work_dir):
        """日维护执行。"""
        orch = MemoryEvolutionOrchestra(str(work_dir / "orch.db"))
        for i in range(5):
            orch.ingest(f"memory item {i}", importance=0.6, tags=[f"tag{i}"])
        result = orch.run_daily_maintenance()
        assert "forgotten" in result
        assert "dedup_removed" in result
        assert "insights" in result
        assert "relations_count" in result


# ============================================================
# ReMeCompressor 测试（持久连接，:memory: 可用）
# ============================================================


class TestReMeCompressor:
    """ReMe 记忆压缩引擎测试。"""

    def test_remember_and_recall(self):
        """存储 + 召回。"""
        reme = ReMeCompressor(db_path=":memory:")
        reme.remember(
            "user_prefers_python", "用户偏好Python编程", category="preference", importance=0.9
        )
        results = reme.recall("python")
        assert len(results) > 0
        assert results[0]["key"] == "user_prefers_python"

    def test_remember_empty_key(self):
        reme = ReMeCompressor(db_path=":memory:")
        assert reme.remember("", "value") is False
        assert reme.remember("key", "") is False

    def test_forget(self):
        reme = ReMeCompressor(db_path=":memory:")
        reme.remember("temp_key", "temp value")
        assert reme.forget("temp_key") is True
        assert reme.forget("temp_key") is False

    def test_list_all(self):
        reme = ReMeCompressor(db_path=":memory:")
        reme.remember("k1", "v1", category="decision")
        reme.remember("k2", "v2", category="preference")
        all_items = reme.list_all()
        assert len(all_items) == 2

    def test_list_by_category(self):
        reme = ReMeCompressor(db_path=":memory:")
        reme.remember("k1", "v1", category="decision")
        reme.remember("k2", "v2", category="preference")
        decisions = reme.list_all(category="decision")
        assert len(decisions) == 1
        assert decisions[0]["key"] == "k1"

    def test_auto_compress_extracts_decisions(self):
        """自动压缩提取决策信息。"""
        reme = ReMeCompressor(db_path=":memory:")
        reme.add_message("user", "我们决定：采用Python作为主语言")
        reme.add_message("assistant", "好的，记录下来")
        compressed = reme.auto_compress()
        assert any(c["category"] == "decision" for c in compressed)

    def test_auto_compress_empty_buffer(self):
        reme = ReMeCompressor(db_path=":memory:")
        assert reme.auto_compress() == []

    def test_ttl_expiry(self):
        """TTL 过期清理。"""
        reme = ReMeCompressor(db_path=":memory:")
        reme.remember("temp", "value", ttl_seconds=0.01)
        time.sleep(0.02)
        results = reme.recall("temp")
        assert len(results) == 0
        deleted = reme.cleanup_expired()
        assert deleted == 1

    def test_stats(self):
        reme = ReMeCompressor(db_path=":memory:")
        reme.remember("k1", "v1", category="decision", importance=0.9)
        reme.remember("k2", "v2", category="preference", importance=0.5)
        stats = reme.stats()
        assert stats["total_memories"] == 2
        assert "decision" in stats["by_category"]
        assert stats["avg_importance"] > 0

    def test_orchestra_sync(self, work_dir):
        """与 Orchestra 联动（需文件路径DB）。"""
        orch = MemoryEvolutionOrchestra(str(work_dir / "sync.db"))
        reme = ReMeCompressor(orchestra=orch, db_path=":memory:")
        reme.remember("sync_key", "sync_value", importance=0.8)
        active = orch.forgetting.list_active(limit=10)
        assert any("sync_key" in r.content for r in active)
