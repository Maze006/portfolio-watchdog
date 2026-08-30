"""
Portfolio Watchdog — Data Ingestion Module

Pulls recent daily OHLCV market data via yfinance for a fixed watchlist.
Features exponential backoff retry logic to handle rate limiting and intermittent API failures.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import yfinance as yf

# Configure logger
logger = logging.getLogger(__name__)

# Fixed watchlist of tracked stock symbols
WATCHLIST: List[str] = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"]

# Short-lived quote cache. /watchlist and /portfolio are both called on every page
# load, and each would otherwise trigger one throttled yfinance download per ticker.
# A few seconds of staleness is invisible on daily bars but removes most of the wait.
_QUOTE_CACHE_TTL_SECONDS = 60.0
_quote_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def get_watchlist() -> List[str]:
    """
    Returns a copy of the fixed stock watchlist.

    Returns:
        List[str]: List of stock ticker symbols.
    """
    return list(WATCHLIST)


def fetch_ohlcv(ticker: str, period: str = "1mo") -> pd.DataFrame:
    """
    Fetches daily OHLCV data for a given ticker with exponential backoff retry logic.

    Retries up to 3 times with exponential backoff delays (1s, 2s, 4s) when
    handling potential yfinance rate limits or network issues.

    Args:
        ticker (str): Stock ticker symbol (e.g. 'AAPL').
        period (str): Valid yfinance period string (default: '1mo').

    Returns:
        pd.DataFrame: Cleaned DataFrame with columns [Date, Open, High, Low, Close, Volume].

    Raises:
        ValueError: If no data could be returned for the ticker after all retries.
    """
    max_retries = 3
    backoff_delays = [1.0, 2.0, 4.0]
    last_exception = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "Fetching OHLCV data for %s (period='%s', attempt %d/%d)...",
                ticker,
                period,
                attempt,
                max_retries,
            )
            df = yf.download(
                ticker,
                period=period,
                interval="1d",
                progress=False,
                auto_adjust=False,
            )

            if df is not None and not df.empty:
                # Handle MultiIndex columns that yfinance (>=0.2.x) may return
                if isinstance(df.columns, pd.MultiIndex):
                    if ticker in df.columns.get_level_values(1):
                        df = df.xs(ticker, axis=1, level=1)
                    elif ticker in df.columns.get_level_values(0):
                        df = df.xs(ticker, axis=1, level=0)
                    else:
                        df.columns = df.columns.get_level_values(0)

                # Reset index so Date becomes a standard column
                df = df.reset_index()

                # Normalize column naming for Date
                if "Date" not in df.columns:
                    if "Datetime" in df.columns:
                        df = df.rename(columns={"Datetime": "Date"})
                    elif "index" in df.columns:
                        df = df.rename(columns={"index": "Date"})

                # Convert Date column to standard YYYY-MM-DD string representation
                if "Date" in df.columns:
                    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")

                # Verify all required OHLCV columns exist
                required_cols = ["Date", "Open", "High", "Low", "Close", "Volume"]
                missing_cols = [col for col in required_cols if col not in df.columns]
                if missing_cols:
                    raise ValueError(
                        f"Missing required columns {missing_cols} in downloaded data for {ticker}"
                    )

                # Filter and order columns
                df = df[required_cols].copy()

                # Drop any rows with NaN in critical price/volume columns
                df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).reset_index(drop=True)

                if not df.empty:
                    logger.info(
                        "Successfully fetched %d rows of OHLCV data for %s.",
                        len(df),
                        ticker,
                    )
                    return df
                else:
                    logger.warning("Data for %s was empty after dropping NaN values.", ticker)

            logger.warning(
                "Attempt %d/%d for %s returned an empty DataFrame.",
                attempt,
                max_retries,
                ticker,
            )

        except Exception as exc:
            last_exception = exc
            logger.warning(
                "Attempt %d/%d for %s encountered error: %s",
                attempt,
                max_retries,
                ticker,
                str(exc),
            )

        # Exponential backoff sleep before next attempt
        if attempt < max_retries:
            delay = backoff_delays[attempt - 1]
            logger.info("Retrying in %.1f seconds...", delay)
            time.sleep(delay)

    error_msg = f"No data returned for {ticker}"
    if last_exception:
        error_msg += f" (Last error: {last_exception})"
    logger.error(error_msg)
    raise ValueError(f"No data returned for {ticker}")


def get_quote(ticker: str, use_cache: bool = True) -> Dict[str, Any]:
    """
    Returns the latest close, the previous close, and the percent change between them.

    Reuses the OHLCV data already available from fetch_ohlcv() rather than introducing
    a second data source.

    Args:
        ticker (str): Stock ticker symbol.
        use_cache (bool): Serve from the short-lived quote cache when fresh.

    Returns:
        Dict[str, Any]: Keys 'ticker', 'current_price', 'previous_close', and
            'percent_change_today'. 'previous_close' and 'percent_change_today' are
            None when fewer than two sessions of data are available.

    Raises:
        ValueError: If no price data could be retrieved for the ticker.
    """
    now = time.time()
    if use_cache:
        cached = _quote_cache.get(ticker)
        if cached and (now - cached[0]) < _QUOTE_CACHE_TTL_SECONDS:
            logger.debug("Serving cached quote for %s.", ticker)
            return dict(cached[1])

    df = fetch_ohlcv(ticker, period="5d")
    closes = df["Close"].dropna() if "Close" in df.columns else pd.Series(dtype=float)
    if closes.empty:
        raise ValueError(f"No price data available for {ticker}")

    current_price = float(closes.iloc[-1])
    previous_close: Optional[float] = (
        float(closes.iloc[-2]) if len(closes) >= 2 else None
    )

    percent_change: Optional[float] = None
    if previous_close:
        percent_change = round(
            ((current_price - previous_close) / previous_close) * 100.0, 2
        )
    elif previous_close == 0:
        logger.warning("Previous close for %s was 0; percent change undefined.", ticker)

    quote: Dict[str, Any] = {
        "ticker": ticker,
        "current_price": round(current_price, 2),
        "previous_close": round(previous_close, 2) if previous_close else None,
        "percent_change_today": percent_change,
    }

    _quote_cache[ticker] = (now, quote)
    return dict(quote)


def get_latest_price(ticker: str) -> float:
    """
    Fetches the most recent closing price for a given ticker.

    Args:
        ticker (str): Stock ticker symbol.

    Returns:
        float: Most recent Close price.

    Raises:
        ValueError: If no price data could be retrieved.
    """
    df = fetch_ohlcv(ticker, period="5d")
    if df.empty or "Close" not in df.columns:
        raise ValueError(f"No price data available for {ticker}")

    latest_close = float(df["Close"].iloc[-1])
    return round(latest_close, 2)
