"""AI-powered resolver for low-confidence utility lookups.

Uses LLMs via OpenRouter or Anthropic API to pick the best candidate
from multiple options when the engine's confidence is low.
"""

import json
import logging
import threading
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class AIResolver:
    """Uses an LLM to resolve low-confidence utility lookups."""

    def __init__(self, api_key: str, provider: str = "openrouter", model: str = None):
        """
        Args:
            api_key: API key for the provider
            provider: "anthropic" or "openrouter"
            model: Override default model
        """
        self.api_key = api_key
        self.provider = provider

        if provider == "anthropic":
            self.base_url = "https://api.anthropic.com/v1/messages"
            self.model = model or "claude-sonnet-4-5-20250514"
            self.headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        elif provider == "openrouter":
            self.base_url = "https://openrouter.ai/api/v1/chat/completions"
            self.model = model or "anthropic/claude-sonnet-4-5"
            self.headers = {
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            }
        else:
            raise ValueError(f"Unknown provider: {provider}")

        self.cache = {}
        self.call_count = 0
        self.error_count = 0
        self._counter_lock = threading.Lock()
        self.rate_limit_delay = 0.0

    def resolve(self, address: str, state: str, utility_type: str,
                candidates: list, zip_code: str = None, city: str = None) -> Optional[dict]:
        """
        Ask the AI to pick the best candidate for this address.

        Returns:
            dict with keys: provider, confidence, source, reasoning
            None if AI says none of the candidates are right or on error
        """
        cache_key = f"{address}|{utility_type}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        if not candidates:
            return None

        prompt = self._build_prompt(address, state, utility_type, candidates, zip_code, city)

        try:
            response = self._call_api(prompt)
            result = self._parse_response(response, candidates)
        except Exception as e:
            logger.warning(f"AI resolver error for {address[:50]}: {e}")
            with self._counter_lock:
                self.error_count += 1
            result = None

        self.cache[cache_key] = result
        return result

    def _build_prompt(self, address, state, utility_type, candidates, zip_code, city):
        candidates_text = ""
        for i, c in enumerate(candidates, 1):
            conf = c.get("confidence", 0)
            src = c.get("source", "unknown")
            candidates_text += f"  {i}. {c['provider']} (source: {src}, confidence: {conf:.2f})\n"

        return f"""You are a utility service territory expert. Given an address, determine which utility provider most likely serves it.

Address: {address}
State: {state}
ZIP: {zip_code or 'unknown'}
City: {city or 'unknown'}
Utility type: {utility_type}

Candidates from our database (ranked by confidence):
{candidates_text}
Instructions:
- Pick the candidate number that most likely serves this specific address
- Consider: Is this a rural or urban area? Which provider typically serves this ZIP/city?
- If you're confident none of the candidates are correct, say "NONE"
- If you think a candidate is correct but with low confidence, still pick it

Respond with ONLY a JSON object, no other text:
{{"pick": 1, "confidence": 0.85, "reasoning": "Brief explanation"}}
or
{{"pick": "NONE", "confidence": 0, "reasoning": "Brief explanation"}}"""

    def _call_api(self, prompt):
        """Call the AI API with rate limiting."""
        time.sleep(self.rate_limit_delay)
        with self._counter_lock:
            self.call_count += 1

        if self.provider == "anthropic":
            payload = {
                "model": self.model,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            }
        else:
            payload = {
                "model": self.model,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            }

        resp = requests.post(
            self.base_url, headers=self.headers, json=payload, timeout=15
        )
        resp.raise_for_status()
        return resp.json()

    def resolve_batch(self, items: list, max_workers: int = 20) -> list:
        """
        Resolve multiple items concurrently.

        Args:
            items: list of dicts with keys: address, state, utility_type, candidates, zip_code, city
            max_workers: concurrent API calls

        Returns:
            list of (item, result_or_None) tuples in same order
        """
        import concurrent.futures

        def _do_one(item):
            return (item, self.resolve(**item))

        results = [None] * len(items)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(_do_one, item): i for i, item in enumerate(items)
            }
            for future in concurrent.futures.as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    results[idx] = (items[idx], None)
                    with self._counter_lock:
                        self.error_count += 1

        return results

    def _parse_response(self, response, candidates):
        """Extract the AI's pick from the response."""
        try:
            if self.provider == "anthropic":
                text = response["content"][0]["text"]
            else:
                text = response["choices"][0]["message"]["content"]

            # Strip markdown code fences if present
            text = text.strip()
            if text.startswith("```"):
                text = text.strip("`").strip()
                if text.startswith("json"):
                    text = text[4:].strip()

            parsed = json.loads(text)

            pick = parsed.get("pick")
            if pick == "NONE" or pick is None:
                return None

            pick_idx = int(pick) - 1
            if 0 <= pick_idx < len(candidates):
                chosen = candidates[pick_idx].copy()
                chosen["confidence"] = min(
                    0.90, max(chosen.get("confidence", 0), parsed.get("confidence", 0.80))
                )
                chosen["source"] = f"ai_resolver (was: {chosen.get('source', 'unknown')})"
                chosen["reasoning"] = parsed.get("reasoning", "")
                return chosen

            return None

        except (json.JSONDecodeError, KeyError, ValueError, IndexError) as e:
            logger.debug(f"AI response parse error: {e}")
            return None
