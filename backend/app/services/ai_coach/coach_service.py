"""AI Race Engineer service — orchestrates telemetry analysis via Claude API."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Pricing per 1M tokens (USD). Cache writes cost 1.25x input for the 5-minute TTL
# and 2x for the 1-hour TTL; cache reads are listed per model.
_PRICING = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20},
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.50},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.10},
}
_DEFAULT_PRICING = _PRICING["claude-opus-5-5"]
_CACHE_WRITE_MULT = {"5m": 1.25, "1h": 2.0}
_USD_TO_EUR = 0.92


@dataclass
class AnalysisResult:
    summary: str = ""
    sections: list[dict] = field(default_factory=list)
    recommendations: list[dict] = field(default_factory=list)
    memory_updates: list[dict] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_eur: float = 0.0
    model: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "sections": self.sections,
            "recommendations": self.recommendations,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_eur": round(self.cost_eur, 4),
            "model": self.model,
            "error": self.error,
        }


def _load_knowledge_base() -> str:
    """Load the setup/telemetry knowledge base markdown."""
    kb_path = Path(__file__).parent / "knowledge_base.md"
    if not kb_path.exists():
        logger.warning(f"Knowledge base not found at {kb_path}")
        return ""
    return kb_path.read_text(encoding="utf-8")


class CoachService:
    def __init__(self, settings):
        self._api_key = settings.ANTHROPIC_API_KEY
        self._model = settings.ANTHROPIC_MODEL
        self._max_tokens = settings.ANTHROPIC_MAX_TOKENS
        self._effort = settings.ANTHROPIC_EFFORT
        self._cache_ttl = settings.ANTHROPIC_CACHE_TTL if settings.ANTHROPIC_CACHE_TTL in _CACHE_WRITE_MULT else "1h"
        self._client = None
        self._kb_text = _load_knowledge_base()

    def _get_client(self):
        if not self._api_key:
            raise ValueError("ANTHROPIC_API_KEY is not configured. Set it in your .env file.")
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def _get_memory(self, data_dir: str):
        from .engineer_memory import EngineerMemory
        return EngineerMemory(data_dir)

    def _compute_cost(self, usage) -> float:
        """Real cost of a call, including knowledge-base cache writes and reads."""
        prices = _PRICING.get(self._model, _DEFAULT_PRICING)
        cache_write = usage.cache_creation_input_tokens or 0
        cache_read = usage.cache_read_input_tokens or 0
        cost_usd = (
            usage.input_tokens * prices["input"]
            + cache_write * prices["input"] * _CACHE_WRITE_MULT[self._cache_ttl]
            + cache_read * prices["cache_read"]
            + usage.output_tokens * prices["output"]
        ) / 1_000_000
        return cost_usd * _USD_TO_EUR

    def _call_claude(self, system_prompt: str, user_message: str) -> AnalysisResult:
        """Make a single Claude API call with prompt caching on the knowledge base."""
        from .prompts import RESPONSE_JSON_SCHEMA

        client = self._get_client()

        system_blocks = []
        if self._kb_text:
            system_blocks.append({
                "type": "text",
                "text": self._kb_text,
                "cache_control": {"type": "ephemeral", "ttl": self._cache_ttl},
            })
        system_blocks.append({"type": "text", "text": system_prompt})

        # The schema guarantees parseable JSON; adaptive thinking + effort set how hard it reasons
        output_config: dict = {"format": {"type": "json_schema", "schema": RESPONSE_JSON_SCHEMA}}
        extra: dict = {}
        if not self._model.startswith("claude-haiku"):  # Haiku has no adaptive thinking / effort
            output_config["effort"] = self._effort
            extra["thinking"] = {"type": "adaptive"}

        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_blocks,
                messages=[{"role": "user", "content": user_message}],
                output_config=output_config,
                **extra,
            )
        except Exception as e:
            logger.error(f"Claude API call failed: {e}")
            return AnalysisResult(error=str(e))

        usage = response.usage
        tokens_in = (usage.input_tokens + (usage.cache_creation_input_tokens or 0)
                     + (usage.cache_read_input_tokens or 0))
        tokens_out = usage.output_tokens
        cost = self._compute_cost(usage)
        logger.info(
            f"Coach call {self._model}: in={usage.input_tokens} cache_write={usage.cache_creation_input_tokens} "
            f"cache_read={usage.cache_read_input_tokens} out={tokens_out} cost={cost:.4f} EUR "
            f"stop={response.stop_reason}"
        )
        if response.stop_reason == "refusal":
            return AnalysisResult(error="Le modèle a refusé cette analyse.", tokens_in=tokens_in,
                                  tokens_out=tokens_out, cost_eur=cost, model=self._model)
        if response.stop_reason == "max_tokens":
            return AnalysisResult(error="Réponse tronquée : augmente ANTHROPIC_MAX_TOKENS dans le .env.",
                                  tokens_in=tokens_in, tokens_out=tokens_out, cost_eur=cost, model=self._model)

        raw_text = "".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        )

        # Parse JSON response
        try:
            # Strip potential markdown code fences
            cleaned = raw_text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()

            parsed = json.loads(cleaned)
            return AnalysisResult(
                summary=parsed.get("summary", ""),
                sections=parsed.get("sections", []),
                recommendations=parsed.get("recommendations", []),
                memory_updates=parsed.get("memory_updates", []),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_eur=cost, model=self._model,
            )
        except json.JSONDecodeError:
            logger.warning("Failed to parse Claude response as JSON, returning raw text")
            return AnalysisResult(
                summary=raw_text[:500],
                sections=[{"title": "Analyse", "content": raw_text, "severity": "info"}],
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_eur=cost, model=self._model,
            )

    def analyze_lap(
        self,
        db_path: str,
        lap_idx: int,
        reference_lap_idx: Optional[int] = None,
        data_dir: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AnalysisResult:
        from .data_preparator import prepare_lap_analysis
        from .prompts import SYSTEM_LAP_ANALYSIS, RESPONSE_SCHEMA_INSTRUCTION

        telemetry_summary = prepare_lap_analysis(db_path, lap_idx, reference_lap_idx)
        if telemetry_summary.startswith("Error:"):
            return AnalysisResult(error=telemetry_summary)

        # Load past observations
        memory_context = ""
        meta = self._get_meta(db_path)
        if data_dir:
            memory = self._get_memory(data_dir)
            obs = memory.get_relevant(
                meta.get("TrackName", ""), meta.get("CarName", "")
            )
            if obs:
                memory_context = (
                    "[Observations passées de l'ingénieur sur ce circuit/voiture]\n"
                    + "\n".join(f"- {o}" for o in obs)
                    + "\n\n"
                )

        user_msg = (
            f"{memory_context}"
            f"[Données télémétrie du tour]\n{telemetry_summary}\n\n"
            f"[Demande]\nAnalyse en détail ce tour et identifie où le temps est perdu. "
            f"Compare avec le tour de référence si disponible."
        )

        result = self._call_claude(
            SYSTEM_LAP_ANALYSIS + RESPONSE_SCHEMA_INSTRUCTION, user_msg
        )

        # Save memory updates
        if data_dir and not result.error:
            self._save_memory_updates(result, meta, data_dir, session_id)

        return result

    def analyze_session(
        self,
        db_path: str,
        data_dir: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AnalysisResult:
        from .data_preparator import prepare_session_analysis
        from .prompts import SYSTEM_SESSION_ANALYSIS, RESPONSE_SCHEMA_INSTRUCTION

        telemetry_summary = prepare_session_analysis(db_path)

        meta = self._get_meta(db_path)
        memory_context = ""
        if data_dir:
            memory = self._get_memory(data_dir)
            obs = memory.get_relevant(
                meta.get("TrackName", ""), meta.get("CarName", "")
            )
            if obs:
                memory_context = (
                    "[Observations passées de l'ingénieur]\n"
                    + "\n".join(f"- {o}" for o in obs)
                    + "\n\n"
                )

        user_msg = (
            f"{memory_context}"
            f"[Données de session]\n{telemetry_summary}\n\n"
            f"[Demande]\nAnalyse la session complète : consistance, dégradation, gestion du carburant. "
            f"Identifie les forces et faiblesses."
        )

        result = self._call_claude(
            SYSTEM_SESSION_ANALYSIS + RESPONSE_SCHEMA_INSTRUCTION, user_msg
        )

        if data_dir and not result.error:
            self._save_memory_updates(result, meta, data_dir, session_id)

        return result

    def advise_setup(
        self,
        db_path: str,
        setup_data: Optional[dict] = None,
        lap_idx: Optional[int] = None,
        data_dir: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AnalysisResult:
        from .data_preparator import prepare_setup_analysis
        from .prompts import SYSTEM_SETUP_ADVICE, RESPONSE_SCHEMA_INSTRUCTION

        telemetry_summary = prepare_setup_analysis(db_path, setup_data, lap_idx)
        if telemetry_summary.startswith("Error:"):
            return AnalysisResult(error=telemetry_summary)

        meta = self._get_meta(db_path)
        memory_context = ""
        if data_dir:
            memory = self._get_memory(data_dir)
            obs = memory.get_relevant(
                meta.get("TrackName", ""), meta.get("CarName", ""), category="setup"
            )
            if obs:
                memory_context = (
                    "[Observations passées sur le setup de cette voiture/circuit]\n"
                    + "\n".join(f"- {o}" for o in obs)
                    + "\n\n"
                )

        user_msg = (
            f"{memory_context}"
            f"[Données télémétrie et setup]\n{telemetry_summary}\n\n"
            f"[Demande]\nAnalyse les données et propose des modifications de setup concrètes "
            f"pour améliorer le comportement de la voiture."
        )

        result = self._call_claude(
            SYSTEM_SETUP_ADVICE + RESPONSE_SCHEMA_INSTRUCTION, user_msg
        )

        if data_dir and not result.error:
            self._save_memory_updates(result, meta, data_dir, session_id)

        return result

    def _get_meta(self, db_path: str) -> dict:
        from .data_preparator import _get_metadata
        return _get_metadata(db_path)

    def _save_memory_updates(
        self, result: AnalysisResult, meta: dict, data_dir: str, session_id: Optional[str]
    ):
        if not result.memory_updates:
            return
        try:
            memory = self._get_memory(data_dir)
            memory.save_observations(
                circuit=meta.get("TrackName", "Unknown"),
                car=meta.get("CarName", "Unknown"),
                car_class=meta.get("CarClass", ""),
                observations=result.memory_updates,
                source_session=session_id,
            )
        except Exception as e:
            logger.error(f"Failed to save memory updates: {e}")

    def check_api_key(self) -> bool:
        return bool(self._api_key)
