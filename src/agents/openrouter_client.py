from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class CreditAnalysisResult:
    model_version: str
    confidence_score: float
    risk_tier: str
    recommended_limit_usd: float
    regulatory_basis: list[str]
    reason: str


class OpenRouterError(Exception):
    pass


class OpenRouterClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY") or os.getenv("openrouterOPENROUTER_API_KEY")
        self.model = model or os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini")
        self.base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")

    def enabled(self) -> bool:
        return bool(self.api_key)

    def analyze_credit_application(
        self,
        application_id: str,
        requested_amount_usd: float,
        applicant_id: str,
    ) -> CreditAnalysisResult:
        if not self.enabled():
            raise OpenRouterError("OpenRouter API key is not configured")

        prompt = (
            "You are a credit analysis model. Return JSON with keys "
            "confidence_score (0-1), risk_tier (LOW|MEDIUM|HIGH), "
            "recommended_limit_usd (number), regulatory_basis (array of strings), reason (string). "
            f"Application: id={application_id}, applicant_id={applicant_id}, requested_amount_usd={requested_amount_usd}."
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Respond with strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc

        try:
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return CreditAnalysisResult(
                model_version=self.model,
                confidence_score=float(parsed["confidence_score"]),
                risk_tier=str(parsed["risk_tier"]),
                recommended_limit_usd=float(parsed["recommended_limit_usd"]),
                regulatory_basis=[str(x) for x in parsed.get("regulatory_basis", [])],
                reason=str(parsed.get("reason", "model-generated")),
            )
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise OpenRouterError(f"Invalid OpenRouter response shape: {exc}") from exc
