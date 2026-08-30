"""Gemini AI Agent module for Portfolio Watchdog.

Integrates with Google GenAI SDK to analyze candlestick patterns and price action
for stocks and generate structured trading decisions (BUY/SELL/HOLD).
"""

import json
import logging
import os
import re
from typing import Literal

from google import genai
from google.genai import types
import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Model identifier specified for the agent
MODEL_NAME = "gemini-3.5-flash"

# Index of the API key currently in use. Module-level so it persists across calls:
# once a key is known good, every subsequent ticker in the cycle reuses it rather
# than restarting the rotation from the beginning.
_current_key_index = 0


class AllKeysExhaustedError(RuntimeError):
    """Raised when every configured Gemini API key has returned 429/RESOURCE_EXHAUSTED.

    Subclasses RuntimeError so the per-ticker ``except Exception`` in main.py catches
    it, letting one ticker's exhaustion fail in isolation without aborting the cycle.
    """


def _load_api_keys() -> list[str]:
    """Return the configured Gemini API keys, in rotation order.

    Reads the comma-separated GEMINI_API_KEYS first, falling back to the single
    GEMINI_API_KEY for backwards compatibility.

    Raises:
        KeyError: If neither environment variable provides at least one key.
    """
    raw_multi = os.environ.get("GEMINI_API_KEYS", "").strip()
    if raw_multi:
        keys = [key.strip() for key in raw_multi.split(",") if key.strip()]
        if keys:
            return keys

    single_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if single_key:
        return [single_key]

    logger.error("Neither GEMINI_API_KEYS nor GEMINI_API_KEY is set in the environment.")
    raise KeyError(
        "No Gemini API key configured. Set GEMINI_API_KEYS (comma-separated) "
        "or GEMINI_API_KEY in the environment."
    )


def _is_quota_error(exc: Exception) -> bool:
    """Return True only for 429 / RESOURCE_EXHAUSTED quota failures.

    Rotation is reserved for quota exhaustion. Auth failures, network timeouts and
    malformed responses are unrelated to which key is in use, so they must not
    trigger a rotation.
    """
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if status == 429:
        return True

    message = str(exc)
    return "RESOURCE_EXHAUSTED" in message or re.search(r"\b429\b", message) is not None


def _generate_with_rotation(prompt: str, config: "types.GenerateContentConfig") -> str:
    """Call Gemini, advancing to the next API key on quota exhaustion.

    Stays on the current key until it returns 429, then rotates and retries the same
    request with the next key. Tries each configured key at most once per call.

    Raises:
        AllKeysExhaustedError: If every configured key returned 429.
        Exception: Any non-quota error is re-raised immediately without rotating.
    """
    global _current_key_index

    keys = _load_api_keys()
    total_keys = len(keys)
    last_quota_error: Exception | None = None

    for attempt in range(total_keys):
        index = _current_key_index % total_keys
        logger.info("Calling Gemini with API key %d of %d.", index + 1, total_keys)

        try:
            client = genai.Client(api_key=keys[index])
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=config,
            )
            return response.text

        except Exception as exc:
            if not _is_quota_error(exc):
                # Not a quota problem - rotating keys cannot fix it.
                logger.error(
                    "Gemini call failed on key %d of %d with a non-quota error: %s",
                    index + 1,
                    total_keys,
                    exc,
                )
                raise

            last_quota_error = exc
            _current_key_index = (index + 1) % total_keys

            if attempt < total_keys - 1:
                logger.warning(
                    "API key %d of %d is exhausted (429). Rotating to key %d.",
                    index + 1,
                    total_keys,
                    _current_key_index + 1,
                )
            else:
                logger.warning(
                    "API key %d of %d is exhausted (429). No keys remain to try.",
                    index + 1,
                    total_keys,
                )

    logger.error("All %d Gemini API key(s) are exhausted (429).", total_keys)
    raise AllKeysExhaustedError(
        "All Gemini API keys exhausted (429). Try again after quota reset."
    ) from last_quota_error


class AgentDecision(BaseModel):
    """Pydantic model representing a structured trading decision made by the AI agent."""

    ticker: str = Field(..., description="Stock ticker symbol, e.g., AAPL")
    action: Literal["BUY", "SELL", "HOLD"] = Field(
        ..., description="Recommended trade action: BUY, SELL, or HOLD"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score in the recommendation from 0.0 to 1.0",
    )
    reasoning: str = Field(
        ...,
        description="Detailed technical reasoning and rationale supporting the decision",
    )


def build_ohlcv_summary(df: pd.DataFrame, ticker: str) -> str:
    """Summarize the last 10 rows of OHLCV price action data into a readable text block.

    Args:
        df: pandas DataFrame containing OHLCV data. Expected columns include
            'Open', 'High', 'Low', 'Close', 'Volume' (and optionally 'Date').
        ticker: Stock ticker symbol being analyzed.

    Returns:
        A formatted string summarizing recent price action, key stats, and daily rows.
    """
    if df is None or df.empty:
        return f"No OHLCV price action data available for {ticker}."

    # Extract the last 10 rows
    recent_df = df.tail(10).copy()

    lines: list[str] = [
        f"=== Recent 10-Period OHLCV Summary for {ticker} ===",
        f"{'Date':<10} | {'Open':>8} | {'High':>8} | {'Low':>8} | {'Close':>8} | {'Volume':>12}",
        "-" * 68,
    ]

    for idx, row in recent_df.iterrows():
        # Handle date whether it's in a column or in the index
        if "Date" in row and pd.notna(row["Date"]):
            date_str = str(row["Date"])[:10]
        elif hasattr(idx, "strftime"):
            date_str = idx.strftime("%Y-%m-%d")
        else:
            date_str = str(idx)[:10]

        open_val = float(row.get("Open", 0.0))
        high_val = float(row.get("High", 0.0))
        low_val = float(row.get("Low", 0.0))
        close_val = float(row.get("Close", 0.0))
        volume_val = int(row.get("Volume", 0))

        lines.append(
            f"{date_str:<10} | {open_val:>8.2f} | {high_val:>8.2f} | {low_val:>8.2f} | {close_val:>8.2f} | {volume_val:>12,}"
        )

    # Compute key descriptive summary metrics
    latest_close = float(recent_df["Close"].iloc[-1])
    first_close = float(recent_df["Close"].iloc[0])
    period_high = float(recent_df["High"].max())
    period_low = float(recent_df["Low"].min())
    price_change = latest_close - first_close
    pct_change = (price_change / first_close) * 100.0 if first_close != 0 else 0.0

    lines.append("-" * 68)
    lines.append(f"Latest Close: ${latest_close:.2f}")
    lines.append(f"10-Period Range: Low ${period_low:.2f} - High ${period_high:.2f}")
    lines.append(f"10-Period Net Change: ${price_change:+.2f} ({pct_change:+.2f}%)")

    return "\n".join(lines)


def analyze_ticker(
    ticker: str,
    ohlcv_summary: str,
    patterns_summary: str,
) -> AgentDecision:
    """Analyze stock price action and detected candlestick patterns using Gemini AI.

    Evaluates recent OHLCV price action and technical candlestick patterns to produce
    a structured trading decision (BUY, SELL, HOLD) with a confidence level and
    concise rationale.

    Args:
        ticker: The stock ticker symbol (e.g., 'AAPL').
        ohlcv_summary: Formatted string containing recent OHLCV price action data.
        patterns_summary: Formatted string containing detected candlestick patterns.

    Returns:
        An AgentDecision instance containing ticker, action, confidence, and reasoning.

    Raises:
        KeyError: If neither GEMINI_API_KEYS nor GEMINI_API_KEY is set.
        AllKeysExhaustedError: If every configured API key has hit its 429 quota.
        Exception: If the Gemini API request or JSON parsing fails.
    """
    logger.info("Starting Gemini technical analysis for %s...", ticker)

    # Construct the technical analysis prompt
    prompt = f"""You are an expert technical analyst and algorithmic trading strategist.
Analyze the following price action and candlestick pattern signals for {ticker} to provide a disciplined trading recommendation.

=== TICKER ===
{ticker}

=== RECENT PRICE ACTION (OHLCV) ===
{ohlcv_summary}

=== DETECTED CANDLESTICK PATTERNS ===
{patterns_summary}

=== INSTRUCTIONS ===
1. Evaluate recent price trends, momentum, volatility, and key levels.
2. Analyze the significance of any detected candlestick patterns within the current market context.
3. Recommend one discrete action:
   - "BUY": High-conviction bullish setup or reversal.
   - "SELL": High-conviction bearish setup or breakdown.
   - "HOLD": Indecision, consolidation, weak conviction, or conflicting signals.
4. Assign a confidence score strictly between 0.0 (no conviction) and 1.0 (highest conviction).
5. Provide a concise technical reasoning (2-4 sentences) explaining the rationale behind your decision.

Respond with the exact JSON object adhering to the schema.
"""

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema={
            "type": "OBJECT",
            "properties": {
                "ticker": {"type": "STRING"},
                "action": {"type": "STRING", "enum": ["BUY", "SELL", "HOLD"]},
                "confidence": {"type": "NUMBER"},
                "reasoning": {"type": "STRING"},
            },
            "required": ["ticker", "action", "confidence", "reasoning"],
        },
        temperature=0.2,
    )

    try:
        response_text = _generate_with_rotation(prompt, config).strip()
        logger.debug("Received raw Gemini response for %s: %s", ticker, response_text)

        # Handle any possible markdown code block wrapper
        if response_text.startswith("```"):
            lines = response_text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            response_text = "\n".join(lines).strip()

        data = json.loads(response_text)
        decision = AgentDecision(**data)
        logger.info(
            "Successfully analyzed %s: Action=%s, Confidence=%.2f",
            ticker,
            decision.action,
            decision.confidence,
        )
        return decision

    except Exception as e:
        logger.error(
            "Failed to analyze %s with Gemini model '%s': %s",
            ticker,
            MODEL_NAME,
            e,
            exc_info=True,
        )
        raise
