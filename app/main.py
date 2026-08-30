"""
Portfolio Watchdog — FastAPI Main Application

This module provides the REST API endpoints and orchestration logic for the
Portfolio Watchdog autonomous stock monitoring agent. It connects:
1. Market data fetching (app.data)
2. Candlestick pattern detection (app.patterns)
3. Gemini AI agent reasoning (app.agent)
4. Trade execution and portfolio tracking (app.actions)
"""

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import actions, agent, data, patterns

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("portfolio_watchdog")

# Initialize FastAPI application
app = FastAPI(
    title="Portfolio Watchdog",
    description="Autonomous AI-powered stock monitoring agent backend",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Configure CORS Middleware
# Reads ALLOWED_ORIGINS from environment; defaults to ["*"] if not set
allowed_origins_raw = os.getenv("ALLOWED_ORIGINS", "").strip()
if allowed_origins_raw and allowed_origins_raw != "*":
    allowed_origins = [origin.strip() for origin in allowed_origins_raw.split(",") if origin.strip()]
else:
    allowed_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Automatic cycle scheduler (US equity market hours)
# ---------------------------------------------------------------------------
# Cycles are spread evenly across the regular US trading session, weekdays only.
# Running overnight or at weekends would spend Gemini quota re-reading the same
# unchanged daily bars.
#
# Quota note: each cycle costs one Gemini request per watchlist ticker. With
# 5 tickers, 8 cycles/day = 40 requests/day. Each free-tier PROJECT allows
# 20/day, so three keys from three projects supply 60 - leaving 20 spare for
# manual runs during a demo.

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN_MINUTE = 9 * 60 + 30   # 09:30 ET
MARKET_CLOSE_MINUTE = 16 * 60      # 16:00 ET

AUTO_RUN_ENABLED = os.getenv("AUTO_RUN_ENABLED", "false").strip().lower() in ("1", "true", "yes")
AUTO_RUN_ON_STARTUP = os.getenv("AUTO_RUN_ON_STARTUP", "false").strip().lower() in ("1", "true", "yes")

try:
    AUTO_RUN_PER_DAY = max(1, int(os.getenv("AUTO_RUN_PER_DAY", "8")))
except ValueError:
    logger.warning("AUTO_RUN_PER_DAY was not an integer; defaulting to 8.")
    AUTO_RUN_PER_DAY = 8

# Serializes cycles so a scheduled run and a manual one can never overlap and
# double-spend quota or interleave writes to the same portfolio rows.
_cycle_lock = threading.Lock()


def _market_slot_minutes() -> List[int]:
    """Minutes-past-midnight ET for each scheduled run, evenly spread over the session.

    Starts at the opening bell and stops short of the close, so the final run of
    the day still reflects a full session rather than firing exactly at 16:00.
    """
    session_length = MARKET_CLOSE_MINUTE - MARKET_OPEN_MINUTE  # 390 minutes
    step = session_length / AUTO_RUN_PER_DAY
    return [int(MARKET_OPEN_MINUTE + round(i * step)) for i in range(AUTO_RUN_PER_DAY)]


def _next_market_run(after: datetime) -> datetime:
    """Return the next scheduled run strictly after ``after`` (both in market time).

    Skips Saturdays and Sundays. Market holidays are NOT handled - a cycle on a
    holiday simply re-reads the previous session's bars.
    """
    slots = _market_slot_minutes()
    day = after.astimezone(MARKET_TZ)

    for offset in range(8):  # today plus a week covers any weekend gap
        candidate_day = (day + timedelta(days=offset)).date()
        if candidate_day.weekday() >= 5:  # 5=Sat, 6=Sun
            continue
        for minute in slots:
            candidate = datetime(
                candidate_day.year, candidate_day.month, candidate_day.day,
                minute // 60, minute % 60, tzinfo=MARKET_TZ,
            )
            if candidate > after:
                return candidate

    # Unreachable in practice; keeps the scheduler alive rather than crashing.
    return after + timedelta(hours=1)


_scheduler_state: Dict[str, Any] = {
    "enabled": AUTO_RUN_ENABLED,
    "runs_per_day": AUTO_RUN_PER_DAY,
    "market_timezone": "America/New_York",
    "session": "09:30-16:00 ET, Mon-Fri",
    "run_times_et": [f"{m // 60:02d}:{m % 60:02d}" for m in _market_slot_minutes()],
    "last_run_at": None,
    "next_run_at": None,
    "last_result": None,
    "runs_completed": 0,
    "runs_skipped_busy": 0,
}


def _try_execute_cycle_locked() -> Optional[Dict[str, Any]]:
    """Run one cycle if the lock is free, else return None.

    Acquires without blocking so a scheduled tick that collides with a manual run
    is skipped outright rather than queueing up and firing late.
    """
    if not _cycle_lock.acquire(blocking=False):
        return None
    try:
        return _execute_cycle()
    finally:
        _cycle_lock.release()


async def _auto_run_loop() -> None:
    """Run the monitoring cycle on a fixed interval until the app shuts down.

    The cycle itself is blocking (network I/O plus Gemini calls), so it is
    dispatched to a worker thread to keep the event loop responsive.
    """
    if AUTO_RUN_ON_STARTUP:
        logger.info("Auto-run: firing one cycle in 5s (AUTO_RUN_ON_STARTUP enabled).")
        _scheduler_state["next_run_at"] = (
            datetime.now(MARKET_TZ) + timedelta(seconds=5)
        ).isoformat()
        await asyncio.sleep(5)
        await asyncio.to_thread(_try_execute_cycle_locked)

    while True:
        try:
            now = datetime.now(MARKET_TZ)
            next_run = _next_market_run(now)
            _scheduler_state["next_run_at"] = next_run.isoformat()

            wait_seconds = max(1.0, (next_run - now).total_seconds())
            logger.info(
                "Auto-run: next cycle at %s (in %.1f h).",
                next_run.strftime("%a %Y-%m-%d %H:%M %Z"),
                wait_seconds / 3600,
            )
            await asyncio.sleep(wait_seconds)

            logger.info(
                "Auto-run: starting scheduled cycle %d of %d for today.",
                _scheduler_state["runs_completed"] + 1,
                AUTO_RUN_PER_DAY,
            )
            result = await asyncio.to_thread(_try_execute_cycle_locked)

            if result is None:
                _scheduler_state["runs_skipped_busy"] += 1
                logger.warning("Auto-run: a cycle was already in progress; skipped this tick.")
            else:
                _scheduler_state["runs_completed"] += 1
                _scheduler_state["last_run_at"] = datetime.now(MARKET_TZ).isoformat()
                _scheduler_state["last_result"] = {
                    "processed": len(result.get("results", [])),
                    "errors": len(result.get("errors", [])),
                }
                logger.info(
                    "Auto-run: cycle finished - %d processed, %d errors.",
                    _scheduler_state["last_result"]["processed"],
                    _scheduler_state["last_result"]["errors"],
                )

        except asyncio.CancelledError:
            logger.info("Auto-run: scheduler stopped.")
            raise
        except Exception as exc:
            # Never let one bad cycle kill the scheduler.
            logger.error("Auto-run: cycle raised an unexpected error: %s", exc, exc_info=True)



@app.on_event("startup")
async def startup_event() -> None:
    """
    Application startup lifecycle hook.
    Initializes the SQLite database and, if enabled, starts the auto-run scheduler.
    """
    logger.info("Initializing Portfolio Watchdog SQLite database...")
    actions.init_db()
    logger.info("Database initialized successfully.")

    if AUTO_RUN_ENABLED:
        estimated = AUTO_RUN_PER_DAY * len(data.get_watchlist())
        logger.info(
            "Auto-run ENABLED: %d cycles/day at %s ET (Mon-Fri), ~%d Gemini requests/day.",
            AUTO_RUN_PER_DAY,
            ", ".join(_scheduler_state["run_times_et"]),
            estimated,
        )
        app.state.auto_run_task = asyncio.create_task(_auto_run_loop())
    else:
        logger.info("Auto-run disabled. Set AUTO_RUN_ENABLED=true in .env to schedule cycles.")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Cancel the scheduler task cleanly on shutdown."""
    task = getattr(app.state, "auto_run_task", None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@app.get("/scheduler", summary="Auto-Run Scheduler Status")
def get_scheduler_status() -> Dict[str, Any]:
    """
    Reports whether automatic cycles are enabled, and when the next one is due.

    Returns:
        Dict[str, Any]: Scheduler configuration, counters and timestamps.
    """
    status = dict(_scheduler_state)
    status["cycle_in_progress"] = _cycle_lock.locked()
    status["estimated_requests_per_day"] = AUTO_RUN_PER_DAY * len(data.get_watchlist())
    return {"scheduler": status}


@app.get("/api", summary="Service Metadata")
def get_root() -> Dict[str, str]:
    """
    Root health and metadata endpoint.

    Returns:
        Dict[str, str]: Basic service info and docs link.
    """
    return {
        "name": "Portfolio Watchdog",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/watchlist", summary="Get Tracked Watchlist")
def get_watchlist() -> Dict[str, Any]:
    """
    Returns the tracked ticker symbols plus a live quote for each.

    'tickers' keeps its original list-of-strings shape for backwards compatibility;
    the per-ticker quotes are added under 'quotes'. A ticker whose price lookup fails
    is still listed, with null price fields, so one bad symbol cannot fail the request.

    Returns:
        Dict[str, Any]: {'tickers': [...], 'quotes': [{ticker, current_price,
            previous_close, percent_change_today}, ...]}.
    """
    watchlist = data.get_watchlist()

    quotes: List[Dict[str, Any]] = []
    for ticker in watchlist:
        try:
            quotes.append(data.get_quote(ticker))
        except Exception as exc:
            logger.warning("Could not fetch quote for %s: %s", ticker, exc)
            quotes.append({
                "ticker": ticker,
                "current_price": None,
                "previous_close": None,
                "percent_change_today": None,
            })

    return {"tickers": watchlist, "quotes": quotes}


def _execute_cycle() -> Dict[str, Any]:
    """
    Executes one full autonomous monitoring cycle across all watchlist tickers.

    For each ticker independently (isolated with try/except):
    1. Fetches recent daily OHLCV market data via data.fetch_ohlcv().
    2. Detects candlestick patterns via patterns.detect_patterns().
    3. Builds textual summaries for AI consumption.
    4. Calls Gemini AI agent via agent.analyze_ticker() to generate a trading decision.
    5. Logs the decision into SQLite via actions.log_decision().
    6. Executes simulated trade via actions.execute_trade() using data.get_latest_price().

    Returns:
        Dict[str, Any]: Results per ticker, errors encountered, and updated portfolio state.
    """
    watchlist = data.get_watchlist()
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    logger.info("Starting monitoring cycle for %d tickers: %s", len(watchlist), watchlist)

    for ticker in watchlist:
        try:
            logger.info("--- Processing ticker: %s ---", ticker)

            # Step 1: Fetch OHLCV data
            df = data.fetch_ohlcv(ticker=ticker, period="1mo")

            # Step 2: Detect candlestick patterns
            detected_patterns = patterns.detect_patterns(df, ticker=ticker)

            # Step 3: Build summaries
            ohlcv_summary = agent.build_ohlcv_summary(df, ticker=ticker)
            patterns_summary = patterns.summarize_patterns(detected_patterns)

            # Step 4: Get AI decision from Gemini
            ai_decision = agent.analyze_ticker(
                ticker=ticker,
                ohlcv_summary=ohlcv_summary,
                patterns_summary=patterns_summary,
            )

            # Extract fields whether decision is a Pydantic model or dict
            if hasattr(ai_decision, "action"):
                action = ai_decision.action
                confidence = float(ai_decision.confidence)
                reasoning = ai_decision.reasoning
                decision_payload = (
                    ai_decision.model_dump()
                    if hasattr(ai_decision, "model_dump")
                    else ai_decision.dict()
                )
            else:
                action = str(ai_decision["action"])
                confidence = float(ai_decision["confidence"])
                reasoning = str(ai_decision["reasoning"])
                decision_payload = ai_decision

            # Step 5: Log decision to database, together with the evidence it was
            # made from, so /history can show what the model actually saw.
            actions.log_decision(
                ticker=ticker,
                action=action,
                confidence=confidence,
                reasoning=reasoning,
                patterns_detected=detected_patterns,
                price_snapshot=df.to_dict(orient="records"),
            )

            # Step 6: Get current price & execute trade
            latest_price = data.get_latest_price(ticker)
            trade_result = actions.execute_trade(
                ticker=ticker,
                action=action,
                confidence=confidence,
                current_price=latest_price,
            )

            results.append({
                "ticker": ticker,
                "latest_price": latest_price,
                "patterns": detected_patterns,
                "decision": decision_payload,
                "trade": trade_result,
            })
            logger.info("Successfully processed %s: action=%s, confidence=%.2f", ticker, action, confidence)

        except Exception as exc:
            error_message = str(exc)
            logger.error("Error processing ticker %s: %s", ticker, error_message, exc_info=True)
            errors.append({
                "ticker": ticker,
                "error": error_message,
            })

    current_portfolio = actions.get_portfolio()
    logger.info("Cycle completed. Processed: %d, Errors: %d", len(results), len(errors))

    return {
        "results": results,
        "errors": errors,
        "portfolio": current_portfolio,
    }


@app.post("/run-cycle", summary="Run Monitoring Cycle")
def run_monitoring_cycle() -> Dict[str, Any]:
    """
    Runs one monitoring cycle on demand.

    Shares a lock with the auto-run scheduler so a manual run and a scheduled one
    can never execute concurrently, which would double-spend Gemini quota and
    interleave writes to the same portfolio rows.

    Returns:
        Dict[str, Any]: Per-ticker results, errors, and the updated portfolio.
    """
    if _cycle_lock.locked():
        logger.warning("Manual run requested while a cycle is already in progress; waiting.")

    with _cycle_lock:
        return _execute_cycle()


@app.get("/history", summary="Get Decision History")
def get_history(ticker: Optional[str] = Query(default=None, description="Optional ticker filter (case-insensitive)")) -> Dict[str, List[Dict[str, Any]]]:
    """
    Retrieves logged AI trading decisions from SQLite.

    Args:
        ticker (Optional[str]): Optional stock symbol filter (e.g. 'AAPL').
            If provided, must match a symbol in the watchlist (case-insensitive).
            If not found in watchlist, returns an empty list without error.

    Returns:
        Dict[str, List[Dict[str, Any]]]: List of logged decision records.
    """
    if ticker is not None and ticker.strip():
        search_ticker = ticker.strip().upper()
        watchlist = [sym.upper() for sym in data.get_watchlist()]

        # Return empty list if ticker is not recognized in watchlist
        if search_ticker not in watchlist:
            logger.info("Queried ticker '%s' not in watchlist; returning empty decisions list.", ticker)
            return {"decisions": []}

        decisions = actions.get_history(ticker=search_ticker)
    else:
        decisions = actions.get_history()

    return {"decisions": decisions}


@app.get("/portfolio", summary="Get Current Portfolio State")
def get_portfolio() -> Dict[str, Any]:
    """
    Returns the current simulated portfolio state, including cash balance,
    open positions, and total calculated value.

    Each position is enriched with its live 'current_price' and 'market_value'
    (shares x current price), and the response gains 'total_position_value' — the sum
    across all holdings. 'avg_price' is left untouched: it is the cost basis, which is
    a different quantity from current market value.

    If a live price cannot be fetched, that position falls back to its cost basis and
    is marked with 'price_source': 'avg_price_fallback' so the frontend can tell the
    difference between a live valuation and a stale one.

    Returns:
        Dict[str, Any]: Current portfolio dictionary.
    """
    portfolio_state = actions.get_portfolio()

    total_position_value = 0.0
    for position in portfolio_state.get("positions", []):
        ticker = position["ticker"]
        current_price: Optional[float] = None
        try:
            current_price = data.get_quote(ticker)["current_price"]
        except Exception as exc:
            logger.warning("Could not fetch live price for %s: %s", ticker, exc)

        if current_price is None:
            current_price = float(position["avg_price"])
            position["price_source"] = "avg_price_fallback"
        else:
            position["price_source"] = "live"

        position["current_price"] = round(current_price, 2)
        position["market_value"] = round(position["shares"] * current_price, 2)
        total_position_value += position["market_value"]

    portfolio_state["total_position_value"] = round(total_position_value, 2)
    return {"portfolio": portfolio_state}


@app.post("/reset", summary="Reset Portfolio and History")
def reset_portfolio() -> Dict[str, Any]:
    """
    Resets the simulated portfolio to its initial state ($10,000 cash, 0 positions)
    and clears all past decision history from SQLite.

    Returns:
        Dict[str, Any]: Reset confirmation status and fresh portfolio state.
    """
    logger.info("Resetting simulated portfolio and clearing decision history...")
    actions.reset_portfolio()
    fresh_portfolio = actions.get_portfolio()
    logger.info("Portfolio successfully reset.")

    return {
        "status": "reset",
        "portfolio": fresh_portfolio,
    }


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------
# Mounted LAST, on purpose. Starlette matches routes in registration order, so
# every API route defined above (plus /docs and /openapi.json) is resolved
# before this catch-all mount is consulted. Declaring it earlier would shadow
# them and the API would start returning HTML.
#
# html=True makes "/" serve index.html, which is why the service metadata
# endpoint moved to /api - the dashboard owns the root path now.

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    logger.info("Serving dashboard from %s at /", FRONTEND_DIR)
else:
    # API-only deployments stay valid; the service must not fail to boot just
    # because the dashboard is absent from the image.
    logger.warning(
        "Frontend directory not found at %s - running API-only. "
        "The dashboard will 404 until the directory is present.",
        FRONTEND_DIR,
    )
