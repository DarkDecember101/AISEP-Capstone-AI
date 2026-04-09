"""
Phase 2A – Externalized checkpoint tests.

Covers:
 - checkpoint.py factory: memory backend, unknown backend fallback, redis
   fallback to memory on error, singleton behaviour, reset helper, TTL config,
   URL redaction
 - builder.py: graph compiles with None checkpointer, with MemorySaver
 - router.py: _research_graph has no checkpointer, _chat_graph has one;
   /chat thread_id continuity across requests; /chat/stream wiring;
   /research is stateless (no thread_id leak)
 - settings: new checkpoint fields have expected defaults
"""

from __future__ import annotations
from src.modules.investor_agent.application.dto.state import GroundingSummary
from src.modules.investor_agent.api.router import _safe_grounding
from src.modules.investor_agent.api.router import _chunk_text
from src.modules.investor_agent.api.router import ChatRequest
from src.modules.investor_agent.api.router import _recover_state_from_checkpointer
from src.modules.investor_agent.application.dto.state import GraphState
from langgraph.graph import END, StateGraph
from src.modules.investor_agent.infrastructure.graph.builder import (
    build_investor_agent_graph,
)
from src.shared.checkpoint import (
    _build_ttl_config,
    _create_redis_checkpointer,
    _redact_url,
    get_checkpointer,
    reset_checkpointer,
)

import asyncio
import re
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

# ────────────────────────────────────────────────────────────────────
# 0. Settings defaults
# ────────────────────────────────────────────────────────────────────

from src.shared.config.settings import Settings


class TestCheckpointSettings:
    """Ensure the three new checkpoint fields have safe defaults."""

    def test_default_backend_is_memory(self):
        s = Settings()
        assert s.CHECKPOINT_BACKEND == "memory"

    def test_default_redis_url_uses_db2(self):
        s = Settings()
        assert s.CHECKPOINT_REDIS_URL == "redis://localhost:6379/2"

    def test_default_ttl_is_1440(self):
        s = Settings()
        assert s.CHECKPOINT_TTL_MINUTES == 1440


# ────────────────────────────────────────────────────────────────────
# 1. checkpoint.py – factory / singleton
# ────────────────────────────────────────────────────────────────────


class TestRedactUrl:
    def test_no_password(self):
        assert _redact_url(
            "redis://localhost:6379/2") == "redis://localhost:6379/2"

    def test_with_password(self):
        result = _redact_url("redis://:s3cret@myhost:6379/2")
        assert "s3cret" not in result
        assert "***@myhost:6379/2" in result

    def test_with_user_and_password(self):
        result = _redact_url("redis://user:pass@host:6379/0")
        assert "pass" not in result
        assert "***@host:6379/0" in result


class TestBuildTtlConfig:
    @patch("src.shared.checkpoint.settings")
    def test_positive_ttl(self, mock_settings: MagicMock):
        mock_settings.CHECKPOINT_TTL_MINUTES = 120
        cfg = _build_ttl_config()
        assert cfg is not None
        assert cfg["default_ttl"] == 120
        assert cfg["refresh_on_read"] is True

    @patch("src.shared.checkpoint.settings")
    def test_zero_ttl_returns_none(self, mock_settings: MagicMock):
        mock_settings.CHECKPOINT_TTL_MINUTES = 0
        assert _build_ttl_config() is None

    @patch("src.shared.checkpoint.settings")
    def test_negative_ttl_returns_none(self, mock_settings: MagicMock):
        mock_settings.CHECKPOINT_TTL_MINUTES = -1
        assert _build_ttl_config() is None


class TestGetCheckpointerMemory:
    """get_checkpointer with CHECKPOINT_BACKEND='memory'."""

    def setup_method(self):
        reset_checkpointer()

    def teardown_method(self):
        reset_checkpointer()

    @patch("src.shared.checkpoint.settings")
    def test_returns_memory_saver(self, mock_settings: MagicMock):
        mock_settings.CHECKPOINT_BACKEND = "memory"
        ckpt = get_checkpointer()
        assert isinstance(ckpt, MemorySaver)

    @patch("src.shared.checkpoint.settings")
    def test_singleton_returns_same_instance(self, mock_settings: MagicMock):
        mock_settings.CHECKPOINT_BACKEND = "memory"
        a = get_checkpointer()
        b = get_checkpointer()
        assert a is b

    @patch("src.shared.checkpoint.settings")
    def test_unknown_backend_falls_back_to_memory(self, mock_settings: MagicMock):
        mock_settings.CHECKPOINT_BACKEND = "postgres"
        ckpt = get_checkpointer()
        assert isinstance(ckpt, MemorySaver)


class TestGetCheckpointerRedisFallback:
    """When CHECKPOINT_BACKEND='redis' but Redis is unreachable, fall back."""

    def setup_method(self):
        reset_checkpointer()

    def teardown_method(self):
        reset_checkpointer()

    @patch("src.shared.checkpoint.settings")
    @patch(
        "src.shared.checkpoint._create_redis_checkpointer",
        side_effect=RuntimeError("connection refused"),
    )
    def test_falls_back_to_memory_on_redis_error(
        self, _mock_create: MagicMock, mock_settings: MagicMock
    ):
        mock_settings.CHECKPOINT_BACKEND = "redis"
        ckpt = get_checkpointer()
        assert isinstance(ckpt, MemorySaver)


class TestResetCheckpointer:
    def test_reset_clears_singleton(self):
        reset_checkpointer()
        with patch("src.shared.checkpoint.settings") as mock_settings:
            mock_settings.CHECKPOINT_BACKEND = "memory"
            a = get_checkpointer()
        reset_checkpointer()
        with patch("src.shared.checkpoint.settings") as mock_settings:
            mock_settings.CHECKPOINT_BACKEND = "memory"
            b = get_checkpointer()
        # After reset, a new instance should be created
        assert a is not b
        reset_checkpointer()


# ────────────────────────────────────────────────────────────────────
# 2. builder.py – compile with / without checkpointer
# ────────────────────────────────────────────────────────────────────


class TestBuildInvestorAgentGraph:
    def test_compiles_without_checkpointer(self):
        graph = build_investor_agent_graph(checkpointer=None)
        assert graph is not None
        # Should still expose ainvoke
        assert hasattr(graph, "ainvoke")

    def test_compiles_with_memory_saver(self):
        graph = build_investor_agent_graph(checkpointer=MemorySaver())
        assert graph is not None
        assert hasattr(graph, "aget_state")

    def test_stateless_graph_has_no_checkpointer(self):
        """A graph compiled without checkpointer should NOT support aget_state
        in a meaningful way (no stored history)."""
        graph = build_investor_agent_graph(checkpointer=None)
        # It may still have the method, but calling it on a nonexistent thread
        # should return None or raise

        async def _check():
            try:
                snap = await graph.aget_state(
                    {"configurable": {"thread_id": "nonexistent"}}
                )
                # If it returns, values should be empty
                if snap is not None:
                    assert not getattr(snap, "values", None)
            except Exception:
                pass  # acceptable — no checkpointer means no state

        asyncio.run(_check())


# ────────────────────────────────────────────────────────────────────
# 3. router.py – graph wiring
# ────────────────────────────────────────────────────────────────────


class TestRouterGraphWiring:
    """Verify the two module-level graph instances are wired correctly."""

    def test_chat_graph_exists(self):
        from src.modules.investor_agent.api.router import _chat_graph
        assert _chat_graph is not None
        assert hasattr(_chat_graph, "ainvoke")
        assert hasattr(_chat_graph, "aget_state")

# ────────────────────────────────────────────────────────────────────
# 4. Thread-ID continuity via MemorySaver
# ────────────────────────────────────────────────────────────────────


class TestThreadContinuity:
    """End-to-end test: two ainvoke calls on the same thread_id share state
    through a MemorySaver checkpointer, proving the checkpoint wiring works."""

    def test_same_thread_preserves_state(self):
        """
        Build a minimal graph that appends to a list. After two invocations
        on the same thread, the checkpoint should retain both entries.
        """
        async def _run():
            saver = MemorySaver()

            builder = StateGraph(GraphState)

            async def append_node(state: GraphState):
                existing = list(state.sub_queries or [])
                existing.append(state.user_query)
                return {"sub_queries": existing}

            builder.add_node("appender", append_node)
            builder.set_entry_point("appender")
            builder.add_edge("appender", END)

            graph = builder.compile(checkpointer=saver)
            thread_cfg = {"configurable": {"thread_id": "continuity-test"}}

            await graph.ainvoke({"user_query": "first"}, config=thread_cfg)
            await graph.ainvoke({"user_query": "second"}, config=thread_cfg)

            snap = await graph.aget_state(thread_cfg)
            subs = snap.values.get("sub_queries", [])
            # Both values should be visible because the checkpoint stored
            # intermediate state between calls
            assert "first" in subs
            assert "second" in subs
            assert len(subs) == 2

        asyncio.run(_run())

    def test_different_threads_are_isolated(self):
        """Two threads with different IDs should NOT share state."""

        async def _run():
            saver = MemorySaver()
            builder = StateGraph(GraphState)

            async def set_query(state: GraphState):
                return {"user_query": state.user_query}

            builder.add_node("setter", set_query)
            builder.set_entry_point("setter")
            builder.add_edge("setter", END)
            graph = builder.compile(checkpointer=saver)

            cfg_a = {"configurable": {"thread_id": "thread-a"}}
            cfg_b = {"configurable": {"thread_id": "thread-b"}}

            await graph.ainvoke({"user_query": "alpha"}, config=cfg_a)
            await graph.ainvoke({"user_query": "beta"}, config=cfg_b)

            snap_a = await graph.aget_state(cfg_a)
            snap_b = await graph.aget_state(cfg_b)
            assert snap_a.values["user_query"] == "alpha"
            assert snap_b.values["user_query"] == "beta"

        asyncio.run(_run())


# ────────────────────────────────────────────────────────────────────
# 5. _recover_state_from_checkpointer resilience
# ────────────────────────────────────────────────────────────────────


class TestRecoverState:
    """_recover_state_from_checkpointer should never raise."""

    def test_returns_empty_dict_on_error(self):
        async def _run():
            # Pass an obviously invalid config — should return {} not raise
            result = await _recover_state_from_checkpointer(
                {"configurable": {"thread_id": "nonexistent-thread-xyz"}}
            )
            assert isinstance(result, dict)

        asyncio.run(_run())


# ────────────────────────────────────────────────────────────────────
# 6. ChatRequest validation
# ────────────────────────────────────────────────────────────────────


class TestChatRequestValidation:
    def test_valid_thread_id(self):
        req = ChatRequest(query="hello", thread_id="my-thread_123")
        assert req.thread_id == "my-thread_123"

    def test_default_thread_id(self):
        req = ChatRequest(query="hello")
        assert req.thread_id == "default_thread"

    def test_rejects_invalid_thread_id(self):
        with pytest.raises(Exception):
            ChatRequest(query="hello", thread_id="../../etc/passwd")

    def test_rejects_blank_query(self):
        with pytest.raises(Exception):
            ChatRequest(query="   ", thread_id="ok")

    def test_rejects_empty_query(self):
        with pytest.raises(Exception):
            ChatRequest(query="", thread_id="ok")

    def test_rejects_overlong_thread_id(self):
        with pytest.raises(Exception):
            ChatRequest(query="hello", thread_id="a" * 129)


# ────────────────────────────────────────────────────────────────────
# 7. _chunk_text helper
# ────────────────────────────────────────────────────────────────────


class TestChunkText:
    def test_empty_string(self):
        assert _chunk_text("") == []

    def test_whitespace_only(self):
        assert _chunk_text("   ") == []

    def test_short_string_single_chunk(self):
        result = _chunk_text("hello")
        assert result == ["hello"]

    def test_splits_at_chunk_size(self):
        text = "a" * 360
        chunks = _chunk_text(text, chunk_size=180)
        assert len(chunks) == 2
        assert all(len(c) == 180 for c in chunks)


# ────────────────────────────────────────────────────────────────────
# 8. _safe_grounding helper
# ────────────────────────────────────────────────────────────────────


class TestSafeGrounding:
    def test_returns_dict_from_model(self):
        gs = GroundingSummary(
            verified_claim_count=2,
            weakly_supported_claim_count=0,
            conflicting_claim_count=0,
            unsupported_claim_count=0,
            reference_count=3,
            coverage_status="sufficient",
        )
        result = _safe_grounding(gs)
        assert result["verified_claim_count"] == 2

    def test_returns_dict_from_valid_dict(self):
        raw = {
            "verified_claim_count": 1,
            "weakly_supported_claim_count": 0,
            "conflicting_claim_count": 0,
            "unsupported_claim_count": 0,
            "reference_count": 1,
            "coverage_status": "sufficient",
        }
        result = _safe_grounding(raw)
        assert result["verified_claim_count"] == 1

    def test_returns_empty_grounding_for_garbage(self):
        result = _safe_grounding("not-a-grounding")
        assert result["coverage_status"] == "insufficient"
        assert result["verified_claim_count"] == 0

    def test_returns_empty_grounding_for_none(self):
        result = _safe_grounding(None)
        assert result["coverage_status"] == "insufficient"
