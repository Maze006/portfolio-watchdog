"""
Portfolio Watchdog — Candlestick Pattern Recognition Module

Detects 5 classic technical candlestick patterns from daily OHLCV market data:
1. Bullish Engulfing: Two-candle bullish reversal pattern.
2. Bearish Engulfing: Two-candle bearish reversal pattern.
3. Hammer: Single-candle bullish reversal with long lower shadow.
4. Doji: Single-candle indecision pattern with very small body.
5. Shooting Star: Single-candle bearish reversal with long upper shadow.
"""

from typing import Any, Dict, List
import pandas as pd


def detect_patterns(df: pd.DataFrame, ticker: str = "") -> List[Dict[str, Any]]:
    """
    Scans an OHLCV DataFrame and detects 5 candlestick patterns across consecutive candles.

    Pattern Rules:
    1. Bullish Engulfing:
       - Previous candle is red (prev_close < prev_open).
       - Current candle is green (curr_close > curr_open).
       - Current candle body fully engulfs previous candle body:
         (curr_open <= prev_close and curr_close >= prev_open).

    2. Bearish Engulfing:
       - Previous candle is green (prev_close > prev_open).
       - Current candle is red (curr_close < curr_open).
       - Current candle body fully engulfs previous candle body:
         (curr_open >= prev_close and curr_close <= prev_open).

    3. Hammer:
       - Small body situated at the upper end of the trading range.
       - Lower shadow >= 2 * body.
       - Upper shadow <= 0.3 * body (30% of body size).
       - Formulas:
         body = abs(curr_close - curr_open)
         lower_shadow = min(curr_open, curr_close) - curr_low
         upper_shadow = curr_high - max(curr_open, curr_close)

    4. Doji:
       - Candle body size <= 10% of total range (curr_high - curr_low).
       - Guarded against zero total range.

    5. Shooting Star:
       - Small body situated at the lower end of the trading range.
       - Upper shadow >= 2 * body.
       - Lower shadow <= 0.3 * body (30% of body size).
       - Formulas:
         body = abs(curr_close - curr_open)
         upper_shadow = curr_high - max(curr_open, curr_close)
         lower_shadow = min(curr_open, curr_close) - curr_low

    Args:
        df (pd.DataFrame): DataFrame containing columns [Date, Open, High, Low, Close, Volume].
        ticker (str, optional): Stock symbol for labeling results.

    Returns:
        List[Dict[str, Any]]: List of pattern dictionaries, e.g.:
            [{"date": "2024-01-15", "pattern": "Bullish Engulfing"}]
    """
    if df is None or df.empty:
        return []

    detected_patterns: List[Dict[str, Any]] = []

    for i in range(len(df)):
        curr_row = df.iloc[i]
        curr_date = str(curr_row["Date"])
        curr_open = float(curr_row["Open"])
        curr_high = float(curr_row["High"])
        curr_low = float(curr_row["Low"])
        curr_close = float(curr_row["Close"])

        curr_body = abs(curr_close - curr_open)
        curr_range = curr_high - curr_low
        curr_upper_shadow = curr_high - max(curr_open, curr_close)
        curr_lower_shadow = min(curr_open, curr_close) - curr_low

        # ------------------------------------------------------------------
        # 1 & 2: Consecutive Candle Patterns (Bullish / Bearish Engulfing)
        # ------------------------------------------------------------------
        if i > 0:
            prev_row = df.iloc[i - 1]
            prev_open = float(prev_row["Open"])
            prev_close = float(prev_row["Close"])

            # 1. Bullish Engulfing
            # Previous candle is red, current candle is green, current body engulfs previous body
            if (
                prev_close < prev_open
                and curr_close > curr_open
                and curr_open <= prev_close
                and curr_close >= prev_open
            ):
                item = {"date": curr_date, "pattern": "Bullish Engulfing"}
                if ticker:
                    item["ticker"] = ticker
                detected_patterns.append(item)

            # 2. Bearish Engulfing
            # Previous candle is green, current candle is red, current body engulfs previous body
            if (
                prev_close > prev_open
                and curr_close < curr_open
                and curr_open >= prev_close
                and curr_close <= prev_open
            ):
                item = {"date": curr_date, "pattern": "Bearish Engulfing"}
                if ticker:
                    item["ticker"] = ticker
                detected_patterns.append(item)

        # ------------------------------------------------------------------
        # 3. Hammer (Bullish Reversal Single Candle)
        # Small body at top of range: lower shadow >= 2*body, upper shadow <= 0.3*body
        # ------------------------------------------------------------------
        if curr_range > 0 and curr_body > 0:
            if curr_lower_shadow >= (2.0 * curr_body) and curr_upper_shadow <= (0.3 * curr_body):
                item = {"date": curr_date, "pattern": "Hammer"}
                if ticker:
                    item["ticker"] = ticker
                detected_patterns.append(item)

        # ------------------------------------------------------------------
        # 4. Doji (Indecision Single Candle)
        # Body size <= 10% of total candle range (high - low)
        # ------------------------------------------------------------------
        if curr_range > 0 and curr_body <= (0.10 * curr_range):
            item = {"date": curr_date, "pattern": "Doji"}
            if ticker:
                item["ticker"] = ticker
            detected_patterns.append(item)

        # ------------------------------------------------------------------
        # 5. Shooting Star (Bearish Reversal Single Candle)
        # Small body at bottom of range: upper shadow >= 2*body, lower shadow <= 0.3*body
        # ------------------------------------------------------------------
        if curr_range > 0 and curr_body > 0:
            if curr_upper_shadow >= (2.0 * curr_body) and curr_lower_shadow <= (0.3 * curr_body):
                item = {"date": curr_date, "pattern": "Shooting Star"}
                if ticker:
                    item["ticker"] = ticker
                detected_patterns.append(item)

    return detected_patterns


def summarize_patterns(patterns: List[Dict[str, Any]]) -> str:
    """
    Constructs a human-readable text summary of detected candlestick patterns
    for consumption in AI agent prompts.

    Args:
        patterns (List[Dict[str, Any]]): List of pattern dictionaries.

    Returns:
        str: Descriptive string summary of candlestick patterns.
    """
    if not patterns:
        return "No significant candlestick patterns detected in the analyzed timeframe."

    lines = ["Detected Candlestick Patterns:"]
    for p in patterns:
        date_str = p.get("date", "Unknown Date")
        pattern_name = p.get("pattern", "Unknown Pattern")
        ticker_str = f" ({p['ticker']})" if p.get("ticker") else ""
        lines.append(f"- {date_str}{ticker_str}: {pattern_name}")

    return "\n".join(lines)
