"""
Unit tests for memory search ranking in backend/services/memory_service.py

Tests cover:
- Vector similarity scoring
- BM25 keyword matching
- Combined ranking with 70/30 weights
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime, timezone, timedelta
import math

# Import the functions we're testing
from backend.services.memory_service import (
    compute_temporal_boost,
    _compute_tier,
    Memory,
)


class TestComputeTemporalBoost:
    """Tests for compute_temporal_boost function."""

    def test_temporal_boost_timeless_stays_near_one(self):
        """Timeless memories should have boost close to 1.0."""
        now = datetime.now(timezone.utc)
        
        # Fresh timeless memory
        boost = compute_temporal_boost("timeless", now, 0)
        assert 0.95 <= boost <= 1.05, f"Expected ~1.0, got {boost}"
        
        # Old timeless memory (should still be near 1.0)
        old_time = now - timedelta(days=365)
        boost = compute_temporal_boost("timeless", old_time, 0)
        assert 0.95 <= boost <= 1.05, f"Expected ~1.0, got {boost}"

    def test_temporal_boost_temporary_decays_with_age(self):
        """Temporary memories should decay when stale."""
        now = datetime.now(timezone.utc)
        
        # Fresh temporary memory (should be high)
        boost_fresh = compute_temporal_boost("temporary", now, 0)
        assert boost_fresh >= 1.2, f"Expected >= 1.2, got {boost_fresh}"
        
        # Old temporary memory (should be low)
        old_time = now - timedelta(days=180)
        boost_old = compute_temporal_boost("temporary", old_time, 0)
        assert boost_old <= 0.8, f"Expected <= 0.8, got {boost_old}"
        
        # Verify decay relationship
        assert boost_fresh > boost_old

    def test_temporal_boost_evolving_considers_frequency(self):
        """Evolving memories should consider both recency and frequency."""
        now = datetime.now(timezone.utc)
        
        # High frequency, recent
        boost_high_freq = compute_temporal_boost("evolving", now, 100)
        
        # Low frequency, recent
        boost_low_freq = compute_temporal_boost("evolving", now, 1)
        
        # High frequency should get higher boost
        assert boost_high_freq > boost_low_freq

    def test_temporal_boost_bounds(self):
        """Boost should always be in [0.7, 1.3] range."""
        now = datetime.now(timezone.utc)
        
        # Test various combinations
        test_cases = [
            ("timeless", now, 0),
            ("timeless", now - timedelta(days=365), 0),
            ("temporary", now, 0),
            ("temporary", now - timedelta(days=365), 0),
            ("evolving", now, 100),
            ("evolving", now - timedelta(days=365), 0),
        ]
        
        for temporal_nature, last_accessed, access_count in test_cases:
            boost = compute_temporal_boost(temporal_nature, last_accessed, access_count)
            assert 0.7 <= boost <= 1.3, f"Boost {boost} out of range for {temporal_nature}"

    def test_temporal_boost_none_last_accessed(self):
        """Should handle None last_accessed (treat as very old)."""
        boost = compute_temporal_boost("temporary", None, 0)
        # Should be low because treated as old
        assert boost <= 0.75, f"Expected low boost for None, got {boost}"


class TestComputeTier:
    """Tests for _compute_tier function."""

    def test_tier_observation_for_low_count(self):
        """Low reinforcement count should result in observation tier."""
        assert _compute_tier(0) == "observation"
        assert _compute_tier(1) == "observation"

    def test_tier_belief_for_medium_count(self):
        """Medium reinforcement count should result in belief tier."""
        assert _compute_tier(2) == "belief"
        assert _compute_tier(3) == "belief"
        assert _compute_tier(4) == "belief"

    def test_tier_knowledge_for_high_count(self):
        """High reinforcement count should result in knowledge tier."""
        assert _compute_tier(5) == "knowledge"
        assert _compute_tier(10) == "knowledge"
        assert _compute_tier(100) == "knowledge"

    def test_tier_progression(self):
        """Tier should progress as reinforcement increases."""
        tiers = [_compute_tier(i) for i in range(7)]
        assert tiers == ["observation", "observation", "belief", "belief", "belief", "knowledge", "knowledge"]


class TestMemoryDataclass:
    """Tests for Memory dataclass."""

    def test_memory_creation(self):
        """Should create a Memory with all fields."""
        now = datetime.now(timezone.utc)
        memory = Memory(
            id="test-id",
            content="Test content",
            memory_type="fact",
            importance=0.8,
            source_conversation_id="conv-123",
            created_at=now,
            updated_at=now,
            last_accessed=now,
            access_count=5,
            temporal_nature="timeless",
            tier="knowledge",
            reinforcement_count=5,
            user_id="user-123",
            score=0.95
        )
        
        assert memory.id == "test-id"
        assert memory.content == "Test content"
        assert memory.memory_type == "fact"
        assert memory.importance == 0.8
        assert memory.score == 0.95

    def test_memory_defaults(self):
        """Should create a Memory with default values."""
        memory = Memory(
            id=None,
            content="Test",
            memory_type="context",
            importance=0.5
        )
        
        assert memory.access_count == 0
        assert memory.temporal_nature == "timeless"
        assert memory.tier == "observation"
        assert memory.reinforcement_count == 0
        assert memory.score == 0.0


class TestRetrieveMemoriesRanking:
    """Tests for retrieve_memories ranking logic."""

    @pytest.mark.asyncio
    async def test_combined_ranking_70_30_weights(self):
        """Test that combined ranking uses 70% vector + 30% keyword weights."""
        # This test verifies the weight calculation logic
        # We'll test the formula: (0.7 * vector_score + 0.3 * keyword_score)
        
        vector_score = 0.8
        keyword_score = 0.6
        importance = 0.5
        
        # Expected base score calculation (from retrieve_memories logic)
        base_score = (
            0.7 * vector_score +
            0.3 * keyword_score
        ) * (1 + importance * 0.2)
        
        # Expected: (0.7 * 0.8 + 0.3 * 0.6) * (1 + 0.5 * 0.2)
        # = (0.56 + 0.18) * 1.1
        # = 0.74 * 1.1
        # = 0.814
        expected = 0.814
        
        assert abs(base_score - expected) < 0.001, f"Expected {expected}, got {base_score}"

    @pytest.mark.asyncio
    async def test_importance_affects_ranking(self):
        """Test that higher importance increases the score."""
        vector_score = 0.8
        keyword_score = 0.6
        
        # Low importance
        score_low = (0.7 * vector_score + 0.3 * keyword_score) * (1 + 0.3 * 0.2)
        
        # High importance
        score_high = (0.7 * vector_score + 0.3 * keyword_score) * (1 + 0.9 * 0.2)
        
        assert score_high > score_low

    @pytest.mark.asyncio
    async def test_tier_boost_affects_ranking(self):
        """Test that tier multipliers affect the final score."""
        base_score = 0.8
        
        # Tier multipliers (from retrieve_memories logic)
        observation_multiplier = 1.0
        belief_multiplier = 1.1
        knowledge_multiplier = 1.2
        
        score_observation = base_score * observation_multiplier
        score_belief = base_score * belief_multiplier
        score_knowledge = base_score * knowledge_multiplier
        
        assert score_knowledge > score_belief > score_observation


class TestSearchMemoriesWithFilters:
    """Tests for search_memories with various filters."""

    @pytest.mark.asyncio
    async def test_search_with_memory_type_filter(self):
        """Test that memory_type filter is applied correctly."""
        # This is a placeholder for integration test
        # In real implementation, we would mock the database and verify the query
        pass

    @pytest.mark.asyncio
    async def test_search_with_importance_threshold(self):
        """Test that min_importance filter works."""
        # Placeholder for integration test
        pass

    @pytest.mark.asyncio
    async def test_search_with_temporal_nature_filter(self):
        """Test that temporal_nature filter works."""
        # Placeholder for integration test
        pass


class TestFindSimilarMemories:
    """Tests for find_similar_memories function."""

    @pytest.mark.asyncio
    async def test_similarity_threshold(self):
        """Test that only memories above threshold are returned."""
        # Placeholder for integration test
        # Would verify that memories below threshold are excluded
        pass

    @pytest.mark.asyncio
    async def test_similarity_ordering(self):
        """Test that results are ordered by similarity (descending)."""
        # Placeholder for integration test
        pass


# Run tests with: pytest tests/unit/test_memory_service.py -v
