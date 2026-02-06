# Windsurf Prompt 13: AI Resolver for Low-Confidence Results
## February 6, 2026

After the batch run, take every result flagged `needs_review: true` (confidence < 0.80) and send it to an AI model to pick the best candidate or reject all of them.

```
## Overview

The engine returns multiple candidates for low-confidence lookups. Instead of making a human review all of them, send them to Claude Sonnet (or GPT via OpenRouter) as a tiebreaker. The AI sees the full address, all candidates with sources, and picks the most likely correct provider.

This runs as a POST-PROCESSING step on batch_results.csv — not inline during the lookup. That keeps the main engine fast and makes the AI step optional/swappable.

## Module: `lookup_engine/ai_resolver.py`

```python
import json
import time
import httpx
from typing import Optional

class AIResolver:
    """Uses an LLM to resolve low-confidence utility lookups."""
    
    def __init__(self, api_key: str, provider: str = "anthropic", model: str = None):
        """
        provider: "anthropic" or "openrouter"
        model: defaults based on provider
        """
        self.api_key = api_key
        self.provider = provider
        
        if provider == "anthropic":
            self.base_url = "https://api.anthropic.com/v1/messages"
            self.model = model or "claude-sonnet-4-5-20250514"
            self.headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
        elif provider == "openrouter":
            self.base_url = "https://openrouter.ai/api/v1/chat/completions"
            self.model = model or "anthropic/claude-sonnet-4-5"
            self.headers = {
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json"
            }
        
        self.cache = {}  # address+utility_type -> result
        self.call_count = 0
        self.rate_limit_delay = 0.5  # seconds between calls
    
    def resolve(self, address: str, state: str, utility_type: str, 
                candidates: list, zip_code: str = None, city: str = None) -> Optional[dict]:
        """
        Ask the AI to pick the best candidate for this address.
        
        Returns: {"provider": "...", "confidence": 0.85, "source": "ai_resolver", "reasoning": "..."}
                 or None if AI says none of the candidates are right
        """
        cache_key = f"{address}|{utility_type}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        prompt = self._build_prompt(address, state, utility_type, candidates, zip_code, city)
        response = self._call_api(prompt)
        result = self._parse_response(response, candidates)
        
        self.cache[cache_key] = result
        return result
    
    def _build_prompt(self, address, state, utility_type, candidates, zip_code, city):
        candidates_text = ""
        for i, c in enumerate(candidates, 1):
            candidates_text += f"  {i}. {c['provider']} (source: {c['source']}, confidence: {c['confidence']:.2f})\n"
        
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
        self.call_count += 1
        
        if self.provider == "anthropic":
            payload = {
                "model": self.model,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}]
            }
        else:  # openrouter
            payload = {
                "model": self.model,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}]
            }
        
        with httpx.Client(timeout=15) as client:
            resp = client.post(self.base_url, headers=self.headers, json=payload)
            resp.raise_for_status()
            return resp.json()
    
    def _parse_response(self, response, candidates):
        """Extract the AI's pick from the response."""
        try:
            if self.provider == "anthropic":
                text = response["content"][0]["text"]
            else:
                text = response["choices"][0]["message"]["content"]
            
            # Strip markdown code fences if present
            text = text.strip().strip("`").strip()
            if text.startswith("json"):
                text = text[4:].strip()
            
            parsed = json.loads(text)
            
            pick = parsed.get("pick")
            if pick == "NONE" or pick is None:
                return None
            
            pick_idx = int(pick) - 1  # 1-indexed to 0-indexed
            if 0 <= pick_idx < len(candidates):
                chosen = candidates[pick_idx].copy()
                chosen["confidence"] = min(0.90, max(chosen["confidence"], parsed.get("confidence", 0.80)))
                chosen["source"] = f"ai_resolver (was: {chosen['source']})"
                chosen["reasoning"] = parsed.get("reasoning", "")
                return chosen
            
            return None
            
        except (json.JSONDecodeError, KeyError, ValueError, IndexError):
            return None
```

## Script: `ai_resolve_batch.py`

Standalone script that reads batch_results.csv, finds all `needs_review` rows, sends them to the AI resolver, and writes an updated CSV.

```python
"""
Post-process batch results with AI resolver for low-confidence lookups.

Usage:
  python ai_resolve_batch.py --input batch_results.csv --output batch_results_ai.csv --provider openrouter --api-key YOUR_KEY
  python ai_resolve_batch.py --input batch_results.csv --output batch_results_ai.csv --provider anthropic --api-key YOUR_KEY

Options:
  --max-calls 5000    Limit total API calls (cost control)
  --min-confidence 0.80   Only resolve results below this confidence
  --dry-run          Show what would be resolved without calling API
  --model            Override default model
"""

import argparse
import csv
import json
import sys
from lookup_engine.ai_resolver import AIResolver

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--provider", choices=["anthropic", "openrouter"], required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-calls", type=int, default=5000)
    parser.add_argument("--min-confidence", type=float, default=0.80)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    
    resolver = AIResolver(args.api_key, args.provider, args.model)
    
    # Read batch results
    with open(args.input) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    # Find rows needing review
    needs_review = []
    for i, row in enumerate(rows):
        conf = float(row.get("engine_confidence", 0) or 0)
        has_provider = bool(row.get("engine_provider"))
        has_tenant = bool(row.get("tenant_raw"))
        
        if has_provider and has_tenant and conf < args.min_confidence:
            needs_review.append((i, row))
    
    print(f"Found {len(needs_review)} rows needing review (confidence < {args.min_confidence})")
    print(f"Max API calls: {args.max_calls}")
    
    if args.dry_run:
        # Show breakdown by utility type
        by_type = {}
        for _, row in needs_review:
            ut = row.get("utility_type", "unknown")
            by_type[ut] = by_type.get(ut, 0) + 1
        for ut, count in sorted(by_type.items()):
            print(f"  {ut}: {count}")
        sys.exit(0)
    
    # Resolve
    resolved = 0
    changed = 0
    errors = 0
    
    for idx, (row_idx, row) in enumerate(needs_review):
        if resolver.call_count >= args.max_calls:
            print(f"Reached max calls ({args.max_calls}), stopping")
            break
        
        # Parse alternatives from pipe-separated column
        alts_str = row.get("engine_alternatives", "")
        candidates = []
        
        # Primary is always first candidate
        if row.get("engine_provider"):
            candidates.append({
                "provider": row["engine_provider"],
                "confidence": float(row.get("engine_confidence", 0.5)),
                "source": row.get("engine_source", "unknown")
            })
        
        # Parse alternatives: "Provider1 (0.72, hifld)|Provider2 (0.65, eia_zip)"
        if alts_str:
            for alt in alts_str.split("|"):
                alt = alt.strip()
                if alt:
                    # Try to parse "Name (conf, source)" format
                    # If parsing fails, just use the name
                    candidates.append({
                        "provider": alt.split(" (")[0] if " (" in alt else alt,
                        "confidence": 0.50,
                        "source": "alternative"
                    })
        
        if not candidates:
            continue
        
        result = resolver.resolve(
            address=row.get("address", ""),
            state=row.get("state", ""),
            utility_type=row.get("utility_type", ""),
            candidates=candidates,
            zip_code=row.get("zip_code"),
            city=row.get("city")
        )
        
        resolved += 1
        
        if result:
            old_provider = rows[row_idx]["engine_provider"]
            rows[row_idx]["engine_provider"] = result["provider"]
            rows[row_idx]["engine_confidence"] = str(result["confidence"])
            rows[row_idx]["engine_source"] = result["source"]
            rows[row_idx]["ai_reasoning"] = result.get("reasoning", "")
            rows[row_idx]["ai_resolved"] = "true"
            
            if result["provider"] != old_provider:
                changed += 1
        
        if idx % 100 == 0 and idx > 0:
            print(f"  Processed {idx}/{len(needs_review)}, {changed} changed, {errors} errors")
    
    # Write output
    fieldnames = list(rows[0].keys())
    if "ai_reasoning" not in fieldnames:
        fieldnames.extend(["ai_reasoning", "ai_resolved"])
    
    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"\nDone. Resolved {resolved}, changed {changed}, errors {errors}")
    print(f"API calls made: {resolver.call_count}")
    print(f"Output: {args.output}")

if __name__ == "__main__":
    main()
```

## Cost Estimate

At ~$0.003 per Sonnet call (short prompt + response):
- 5,000 calls = ~$15
- 15,000 calls = ~$45
- Full batch worst case (~18K low-confidence) = ~$54

With OpenRouter, add ~20% markup.

## Usage

```bash
# Dry run first — see how many rows need review
python ai_resolve_batch.py --input batch_results.csv --output batch_results_ai.csv --provider openrouter --api-key sk-or-xxx --dry-run

# Small test — 100 calls
python ai_resolve_batch.py --input batch_results.csv --output batch_results_ai.csv --provider openrouter --api-key sk-or-xxx --max-calls 100

# Full run
python ai_resolve_batch.py --input batch_results.csv --output batch_results_ai.csv --provider anthropic --api-key sk-ant-xxx --max-calls 20000

# Then re-run comparison scoring on the AI-resolved results
python batch_validate.py --recompare batch_results_ai.csv
```

## Expected Outcome

- AI resolves ~70-80% of low-confidence cases correctly
- Remaining ~20-30% still flagged for human review
- Net effect: the "manual review" queue shrinks from ~15K to ~4K
- Human reviewers only see the truly ambiguous cases

## Notes

- The AI resolver is a POST-PROCESSING step, not part of the real-time engine
- For production single-address lookups, we can optionally call the AI inline (adds 1-2s latency)
- Cache AI decisions so the same address never gets resolved twice
- Consider building a corrections database from AI + human decisions that feeds back into Priority 0
```
