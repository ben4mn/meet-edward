"""Adapters that let Graphiti use Edward's own model routing and embeddings
without an OpenAI API key.

Edward routes every runtime LLM call through the ``services.llm_utils``
builders: ``build_background_llm()`` returns ``ChatClaude`` on the
``background`` route under ``EDWARD_LLM_PROVIDER=anthropic`` (priced into
``llm_calls`` and held to the daily spend cap), or the Codex fallback
otherwise — never a raw ``openai`` or ``anthropic`` SDK client of its own.
Graphiti's default ``LLMClient`` and ``EmbedderClient`` instantiate
``openai.AsyncOpenAI()`` directly, which would require a separate key and
route around that routing and the spend cap.

This module provides two thin adapters:

* ``BackgroundLLMGraphitiClient`` — wraps ``build_background_llm()`` and
  maps Graphiti ``Message`` objects to LangChain messages. JSON-mode is
  simulated by prompting the system to return strict JSON and parsing the
  response via ``extract_json_payload``.
* ``LocalSentenceTransformerEmbedder`` — reuses the same
  ``sentence-transformers`` model Edward already loads for ``memories``
  (384-dim MiniLM), so Kuzu doesn't have to resize its vector columns
  mid-flight and we avoid OpenAI embedding costs entirely.

Both adapters are safe to import even when ``GRAPHITI_ENABLED=false``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import LLMConfig, ModelSize
from graphiti_core.prompts.models import Message

from services.json_parsing import extract_json_payload
from services.llm_utils import build_background_llm, make_background_system_message


logger = logging.getLogger(__name__)


class BackgroundLLMGraphitiClient(LLMClient):
    """Route Graphiti's prompts through ``build_background_llm()``.

    Graphiti expects ``generate_response`` to return a ``dict`` — either
    parsed JSON or a validated pydantic-dumped dict. We ask the background
    LLM to return strict JSON and parse it with
    ``services.json_parsing.extract_json_payload``.
    """

    def __init__(self):
        # LLMClient base stores a config and tracer; pass defaults.
        super().__init__(config=LLMConfig(), cache=False)

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: Optional[type[BaseModel]] = None,
        max_tokens: int = 1024,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, Any]:
        """Convert messages → LangChain → background LLM → parsed JSON."""
        lc_messages: list[Any] = []
        sys_text = ""
        for msg in messages:
            role = (msg.role or "user").lower()
            if role == "system":
                sys_text += ("\n\n" + msg.content) if sys_text else msg.content
            else:
                lc_messages.append(HumanMessage(content=msg.content))

        # Inject "respond with strict JSON" so the background LLM (which
        # has no native json-mode flag in this call path) knows to stay
        # parseable. If the caller supplied a response_model, append its
        # schema for guidance.
        json_hint = (
            "\n\nReturn ONLY a valid JSON object, no prose or markdown fences."
        )
        if response_model is not None:
            try:
                schema = response_model.model_json_schema()
                json_hint += (
                    "\n\nThe JSON must match this schema exactly:\n"
                    + json.dumps(schema, indent=2)
                )
            except Exception:
                pass
        sys_text = (sys_text + json_hint).strip()

        lc_messages.insert(0, make_background_system_message(sys_text))

        llm = build_background_llm(max_tokens=max_tokens)
        # Hard timeout so a stalled provider call can't hang the worker
        # event loop indefinitely. Typical extraction call is 10-30s;
        # 120s is generous but still bounded.
        try:
            response = await asyncio.wait_for(
                llm.ainvoke(lc_messages), timeout=120.0
            )
        except asyncio.TimeoutError:
            logger.warning(
                "graphiti adapter LLM call timed out after 120s; returning {}"
            )
            return {}
        content = response.content
        if isinstance(content, list):
            content = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )

        parsed = extract_json_payload(
            content, expected="object", subsystem="graphiti_llm_adapter"
        )
        if not isinstance(parsed, dict):
            # Graphiti callers rely on a dict — wrap stray lists / strings
            # so downstream .get() calls don't crash.
            parsed = {"content": parsed}
        return parsed


class LocalSentenceTransformerEmbedder(EmbedderClient):
    """Delegate embeddings to Edward's local sentence-transformers model.

    Reuses the 384-dim MiniLM embedding already cached in-process for the
    ``memories`` pgvector column. Kuzu stores arbitrary-width vectors so
    the dim mismatch vs. OpenAI's 1536 doesn't matter; we just stay
    consistent with the rest of Edward's memory stack.
    """

    def __init__(self):
        # EmbedderClient base doesn't require init args.
        pass

    async def create(
        self,
        input_data: Any,
    ) -> list[float]:
        from services.memory_service import get_embedding

        if isinstance(input_data, str):
            return get_embedding(input_data)
        if isinstance(input_data, list):
            # Graphiti sometimes passes list[str]; return the first row's
            # embedding because ``create`` is documented to return a single
            # vector. For batch use ``create_batch``.
            if input_data and isinstance(input_data[0], str):
                return get_embedding(input_data[0])
        # Unknown shape — best-effort string coercion.
        return get_embedding(str(input_data))

    async def create_batch(
        self,
        input_data_list: list[str],
    ) -> list[list[float]]:
        from services.memory_service import get_embedding

        return [get_embedding(s) for s in input_data_list]


class LocalCosineCrossEncoder(CrossEncoderClient):
    """Rank passages by cosine similarity against the query using Edward's
    local sentence-transformers model.

    Graphiti's default ``OpenAIRerankerClient`` requires an OpenAI API key;
    this adapter keeps the reranker fully offline + free, and matches the
    embedding model used by ``LocalSentenceTransformerEmbedder`` so the
    scoring stays internally consistent.
    """

    async def rank(
        self,
        query: str,
        passages: list[str],
    ) -> list[tuple[str, float]]:
        import math
        from services.memory_service import get_embedding

        if not passages:
            return []
        q_vec = get_embedding(query)
        q_norm = math.sqrt(sum(x * x for x in q_vec)) or 1.0

        scored: list[tuple[str, float]] = []
        for passage in passages:
            p_vec = get_embedding(passage)
            p_norm = math.sqrt(sum(x * x for x in p_vec)) or 1.0
            dot = sum(a * b for a, b in zip(q_vec, p_vec))
            score = dot / (q_norm * p_norm)
            scored.append((passage, float(score)))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored
