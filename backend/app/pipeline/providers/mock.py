"""
backend/app/pipeline/providers/mock.py - High-Fidelity Hermetic Mock AI Provider
"""

import json
import re
from typing import Any, Dict, List, Optional
from backend.app.pipeline.providers.base import BaseAIProvider


class MockAIProvider(BaseAIProvider):
    """
    High-fidelity hermetic offline mock provider for Gemini and Groq.
    Operates in <1ms with 0 external network calls.
    Supports realistic parsing, dynamic prompt sensitivity, error simulation,
    and stage call recording.
    """

    def __init__(
        self,
        provider: str = "gemini",
        api_key: str = "mock-api-key-12345",
        default_model: str = "gemini-2.0-flash",
        simulate_429: bool = False,
        simulate_auth_error: bool = False,
        simulate_timeout: bool = False,
    ):
        super().__init__(api_key=api_key, default_model=default_model)
        self._provider_name = provider.lower()
        self.simulate_429 = simulate_429
        self.simulate_auth_error = simulate_auth_error
        self.simulate_timeout = simulate_timeout

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def _check_simulation_errors(self) -> None:
        if self.simulate_auth_error or not self.api_key:
            raise PermissionError(
                f"{self.provider_name.capitalize()} API key is invalid or unauthorized (HTTP 401)"
            )
        if self.simulate_429:
            if self.provider_name == "gemini":
                raise RuntimeError("Gemini ResourceExhausted: 429 Quota Exceeded")
            else:
                raise RuntimeError("Groq RateLimitError: 429 Rate limit reached")
        if self.simulate_timeout:
            raise RuntimeError(f"[{self.provider_name}] Connection timed out after 25s")

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> str:
        self._check_simulation_errors()
        model_name = model or self.default_model

        self.call_history.append({
            "provider": self.provider_name,
            "model": model_name,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "json_mode": json_mode,
        })

        # Return realistic response based on prompt contents
        p_lower = (prompt + " " + (system_prompt or "")).lower()
        if "stage4" in p_lower or "visual" in p_lower or "trend" in p_lower or "cluster" in p_lower:
            res = self._mock_stage4(prompt)
            return json.dumps(res) if json_mode else str(res)
        elif "stage3" in p_lower or "summar" in p_lower or "headline" in p_lower:
            res = self._mock_stage3(prompt)
            return json.dumps(res) if json_mode else str(res)
        elif "stage2" in p_lower or "scoring" in p_lower or "importance_score" in p_lower or "score" in p_lower:
            combined_stage2 = prompt + "\n" + (system_prompt or "")
            res = self._mock_stage2(combined_stage2, {"scoring_prompt": combined_stage2})
            return json.dumps(res) if json_mode else str(res)
        elif "stage1" in p_lower or "extraction" in p_lower or "translate" in p_lower:
            res = self._mock_stage1(prompt)
            return json.dumps(res) if json_mode else str(res)

        return json.dumps({"status": "ok", "provider": self.provider_name}) if json_mode else "Mock response"

    async def process_stage(
        self,
        stage: str,
        input_text: str,
        context: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        self._check_simulation_errors()
        model_name = model or self.default_model
        ctx = context or {}

        self.call_history.append({
            "provider": self.provider_name,
            "stage": stage,
            "model": model_name,
            "input_text": input_text,
            "context": ctx,
        })

        norm_stage = stage.lower()
        if norm_stage in ("extraction", "extraction_translation", "stage1"):
            return self._mock_stage1(input_text)
        elif norm_stage in ("scoring", "importance_scoring", "stage2"):
            return self._mock_stage2(input_text, ctx)
        elif norm_stage in ("summarization", "stage3"):
            return self._mock_stage3(input_text)
        elif norm_stage in ("visuals", "visual_generation", "stage4"):
            return self._mock_stage4(input_text)
        else:
            return {"result": f"Processed stage {stage}"}

    def _mock_stage1(self, text: str) -> Dict[str, Any]:
        # Detect Arabic / Persian
        is_persian = any("\u0600" <= c <= "\u06FF" for c in text) and (
            "ایران" in text or "سپاه" in text or "خلیج" in text or "تهران" in text or "\u200c" in text
        )
        is_arabic = any("\u0600" <= c <= "\u06FF" for c in text) and not is_persian

        # Entity extraction keywords
        entities: List[str] = []
        for word in ["IRGC", "CENTCOM", "IAEA", "IDF", "Hezbollah", "Houthis", "US Navy", "Iran", "Israel", "Strait of Hormuz"]:
            if word.lower() in text.lower():
                entities.append(word)

        if is_persian or is_arabic:
            detected_lang = "fa" if is_persian else "ar"
            # Retain acronyms
            trans_body = text
            # Clean ZWNJ
            trans_body = trans_body.replace("\u200c", " ")
            # Normalize Persian numbers if present
            persian_nums = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
            trans_body = trans_body.translate(persian_nums)

            # Build mock translation
            translated = (
                f"English translation: Iranian forces reported military activity in the region. "
                f"Entities involved include {', '.join(entities) if entities else 'regional actors'}."
            )
            if not entities:
                entities = ["Iran", "IRGC", "Strait of Hormuz"]
        else:
            detected_lang = "en"
            translated = text
            if not entities:
                entities = ["Regional Command", "Defense Ministry"]

        return {
            "extracted_entities": entities,
            "detected_language": detected_lang,
            "translated_text": translated,
            "key_facts": [
                "Regional military activity detected.",
                "Strategic waterways monitored by defense commands."
            ],
            "entity_breakdown": {
                "organizations": [e for e in entities if e in ("IRGC", "CENTCOM", "IAEA", "IDF")],
                "locations": [e for e in entities if e in ("Strait of Hormuz", "Iran", "Israel")],
                "people": [],
                "military_assets": [],
                "geopolitical_events": []
            }
        }

    def _mock_stage2(self, text: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        scoring_prompt = (ctx.get("scoring_prompt") or "").lower()
        match = re.search(r"scoring instructions.*?\n(.*?)\n\s*general scale definitions", text, re.IGNORECASE | re.DOTALL)
        if match:
            scoring_prompt = match.group(1).lower()
        elif not scoring_prompt:
            scoring_prompt = text.lower()

        threshold = ctx.get("importance_threshold", ctx.get("alert_threshold", 7))
        text_lower = text.lower()

        # Dynamic steering: check user instructions in scoring_prompt
        is_diplomatic = "diplomatic" in text_lower or "talks" in text_lower or "negotiat" in text_lower or "meeting" in text_lower
        if is_diplomatic and ("score diplomatic talks 10" in scoring_prompt or "score 10 for diplomatic" in scoring_prompt or "score diplomatic talks as 10" in scoring_prompt):
            score = 10
            rationale = "User scoring prompt prioritizes diplomatic talks at critical importance (10)."
            should_alert = score >= threshold
            return {
                "importance_score": score,
                "score_rationale": rationale,
                "priority_level": "critical",
                "should_alert": should_alert,
                "matched_keywords": ["diplomatic", "talks"],
                "matched_entities": []
            }

        if is_diplomatic and ("score diplomatic talks 1" in scoring_prompt or "score 1 for diplomatic" in scoring_prompt or "score diplomatic talks as 1" in scoring_prompt):
            score = 1
            rationale = "User scoring prompt minimizes diplomatic talks to lowest importance (1)."
            should_alert = score >= threshold
            return {
                "importance_score": score,
                "score_rationale": rationale,
                "priority_level": "low",
                "should_alert": should_alert,
                "matched_keywords": ["diplomatic"],
                "matched_entities": []
            }

        # Default heuristic score based on severity
        if any(w in text_lower for w in ["missile", "strike", "kinetic", "nuclear", "war", "attack", "casualt"]):
            score = 9
            priority = "critical"
            rationale = "Direct kinetic action or significant military engagement reported."
        elif any(w in text_lower for w in ["blockade", "seiz", "intercept", "drone", "patrol", "sanction", "threat"]):
            score = 7
            priority = "high"
            rationale = "High-priority operational event or escalation in strategic waters."
        elif any(w in text_lower for w in ["diplomatic", "statement", "summit", "envoy", "talks"]):
            score = 4
            priority = "medium"
            rationale = "Routine diplomatic communication and non-kinetic developments."
        else:
            score = 3
            priority = "low"
            rationale = "General regional background commentary and administrative reporting."

        should_alert = score >= threshold
        return {
            "importance_score": score,
            "score_rationale": rationale,
            "priority_level": priority,
            "should_alert": should_alert,
            "matched_keywords": ["military", "defense"],
            "matched_entities": ["IRGC", "CENTCOM"]
        }

    def _mock_stage3(self, text: str) -> Dict[str, Any]:
        clean_text = text.replace("English translation:", "").strip()
        headline = clean_text.split(".")[0][:75]
        if not headline:
            headline = "Regional Intelligence Briefing Update"
        summary = (
            f"BLUF: Intelligence forces report ongoing tactical monitoring in the theater. "
            f"{clean_text[:120]}... Operational readiness remains elevated."
        )
        return {
            "headline": headline,
            "summary": summary
        }

    def _mock_stage4(self, text: str) -> Dict[str, Any]:
        text_lower = text.lower()
        if "naval" in text_lower or "strait" in text_lower or "ship" in text_lower or "vessel" in text_lower:
            category = "Naval Escalation"
            tags = ["Naval", "Maritime Security", "Strait of Hormuz"]
        elif "missile" in text_lower or "drone" in text_lower or "air" in text_lower:
            category = "Air & Missile Defense"
            tags = ["Missile", "Air Defense", "UAV"]
        elif "diplomat" in text_lower or "treaty" in text_lower or "talk" in text_lower:
            category = "Diplomatic Posturing"
            tags = ["Diplomacy", "Negotiations", "Bilateral"]
        else:
            category = "Geopolitical Development"
            tags = ["Intelligence", "Regional Security"]

        return {
            "trend_category": category,
            "cluster_tags": tags,
            "narrative_insight": f"Event aligns with ongoing developments in {category}."
        }
