"""SQLite Logging and Simulated Position Tracker module for Portfolio Watchdog.

Handles persistent decision logging, portfolio position tracking, cash balance management,
and simulated order executions with position sizing rules.
"""

from datetime import datetime, timezone
import json
import logging
import math
import sqlite3
from typing import Any, Optional

logger = logging.getLogger(__name__)

DB_PATH = "watchdog.db"
DEFAULT_CASH_BALANCE = 10000.0

# Columns added after the initial schema shipped. Existing databases are migrated
# in place by init_db() so previously logged decisions are preserved.
_DECISION_EVIDENCE_COLUMNS = ("patterns_detected", "price_snapshot")


def _to_json(value: Any) -> Optional[str]:
    """Serialize a value for storage in a TEXT column, or None if there is nothing to store.

    Uses ``default=str`` so numpy/pandas scalars and timestamps coming from the
    OHLCV frame serialize instead of raising.
    """
    if value is None:
        return None
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError) as exc:
        logger.warning("Could not serialize evidence field for storage: %s", exc)
        return None


def _from_json(raw: Any) -> Any:
    """Deserialize a stored JSON TEXT column back into Python objects.

    Returns None when the column is empty or holds text that is not valid JSON,
    so a single malformed legacy row cannot break the whole /history response.
    """
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Stored evidence column was not valid JSON; returning None.")
        return None


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Create and return a SQLite database connection with row factory configured.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        sqlite3.Connection object with sqlite3.Row row_factory.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    """Initialize SQLite database tables and default portfolio metadata if not already present.

    Creates:
    1. decisions: Stores historical agent decisions with timestamps.
    2. portfolio: Stores current holdings with shares and average purchase prices.
    3. portfolio_meta: Stores portfolio metadata, such as current cash balance.

    Args:
        db_path: Path to the SQLite database file.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # 1. Decisions Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL NOT NULL,
                reasoning TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                patterns_detected TEXT,
                price_snapshot TEXT
            );
            """
        )

        # Migrate databases created before the evidence columns existed. Both are
        # nullable, so existing rows stay valid and simply report null evidence.
        cursor.execute("PRAGMA table_info(decisions);")
        existing_columns = {row["name"] for row in cursor.fetchall()}
        for column in _DECISION_EVIDENCE_COLUMNS:
            if column not in existing_columns:
                cursor.execute(f"ALTER TABLE decisions ADD COLUMN {column} TEXT;")
                logger.info("Migrated decisions table: added column '%s'.", column)

        # 2. Portfolio Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio (
                ticker TEXT PRIMARY KEY,
                shares REAL NOT NULL,
                avg_price REAL NOT NULL
            );
            """
        )

        # 3. Portfolio Metadata Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_meta (
                key TEXT PRIMARY KEY,
                value REAL NOT NULL
            );
            """
        )

        # Initialize cash balance if missing
        cursor.execute(
            """
            INSERT OR IGNORE INTO portfolio_meta (key, value)
            VALUES ('cash_balance', ?);
            """,
            (DEFAULT_CASH_BALANCE,),
        )

        conn.commit()


def log_decision(
    ticker: str,
    action: str,
    confidence: float,
    reasoning: str,
    patterns_detected: Optional[Any] = None,
    price_snapshot: Optional[Any] = None,
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    """Insert an agent decision into the decisions table with an ISO timestamp.

    Alongside the outcome, persists the evidence the decision was made from so the
    reasoning can be audited later rather than taken on trust.

    Args:
        ticker: Stock ticker symbol (e.g., 'AAPL').
        action: Recommended action ('BUY', 'SELL', 'HOLD').
        confidence: Agent confidence score (0.0 to 1.0).
        reasoning: Rationale supporting the decision.
        patterns_detected: Candlestick patterns fed to the model for this decision.
            Stored as JSON; optional.
        price_snapshot: OHLCV rows used to build the prompt. Stored as JSON; optional.
        db_path: Path to the SQLite database file.

    Returns:
        A dictionary containing the logged decision details including generated ID and
        timestamp, with the evidence fields as structured objects (not JSON strings).
    """
    init_db(db_path)
    timestamp = datetime.now(timezone.utc).isoformat()
    clean_ticker = ticker.strip().upper()
    clean_action = action.strip().upper()

    patterns_json = _to_json(patterns_detected)
    snapshot_json = _to_json(price_snapshot)

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO decisions (
                ticker, action, confidence, reasoning, timestamp,
                patterns_detected, price_snapshot
            )
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                clean_ticker,
                clean_action,
                float(confidence),
                reasoning,
                timestamp,
                patterns_json,
                snapshot_json,
            ),
        )
        conn.commit()
        record_id = cursor.lastrowid

    logger.info(
        "Logged decision #%s: %s for %s with confidence %.2f",
        record_id,
        clean_action,
        clean_ticker,
        confidence,
    )

    return {
        "id": record_id,
        "ticker": clean_ticker,
        "action": clean_action,
        "confidence": float(confidence),
        "reasoning": reasoning,
        "timestamp": timestamp,
        "patterns_detected": patterns_detected,
        "price_snapshot": price_snapshot,
    }


def get_history(
    ticker: Optional[str] = None, db_path: str = DB_PATH
) -> list[dict[str, Any]]:
    """Query logged agent decisions, optionally filtered by ticker symbol.

    Args:
        ticker: Optional ticker symbol to filter results.
        db_path: Path to the SQLite database file.

    Returns:
        A list of decision dicts sorted descending by record ID. Always returns a list.
    """
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        if ticker:
            cursor.execute(
                """
                SELECT id, ticker, action, confidence, reasoning, timestamp,
                       patterns_detected, price_snapshot
                FROM decisions
                WHERE UPPER(ticker) = ?
                ORDER BY id DESC;
                """,
                (ticker.strip().upper(),),
            )
        else:
            cursor.execute(
                """
                SELECT id, ticker, action, confidence, reasoning, timestamp,
                       patterns_detected, price_snapshot
                FROM decisions
                ORDER BY id DESC;
                """
            )
        rows = cursor.fetchall()

    return [
        {
            "id": int(row["id"]),
            "ticker": str(row["ticker"]),
            "action": str(row["action"]),
            "confidence": float(row["confidence"]),
            "reasoning": str(row["reasoning"]),
            "timestamp": str(row["timestamp"]),
            "patterns_detected": _from_json(row["patterns_detected"]),
            "price_snapshot": _from_json(row["price_snapshot"]),
        }
        for row in rows
    ]


def execute_trade(
    ticker: str,
    action: str,
    confidence: float,
    current_price: float,
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    """Execute simulated portfolio trade execution based on an agent recommendation.

    Position Sizing and Execution Rules:
    - BUY: Allocates (confidence * 0.20 * cash_balance). Computes shares = floor(allocation / current_price).
           If shares == 0, logs a warning and returns {"status": "skipped", "reason": "insufficient allocation"}.
           Otherwise updates portfolio position and deducts cash balance.
    - SELL: Sells floor(confidence * current_shares). If no existing position or shares == 0, logs a warning
            and returns {"status": "skipped", "reason": "..."}. Otherwise reduces/removes position and adds revenue to cash.
    - HOLD: Returns {"status": "held", "ticker": ticker}.

    Args:
        ticker: Stock ticker symbol (e.g., 'AAPL').
        action: Recommended trade action ('BUY', 'SELL', 'HOLD').
        confidence: Confidence level from 0.0 to 1.0.
        current_price: Current market price per share.
        db_path: Path to the SQLite database file.

    Returns:
        A dictionary describing the executed trade or skip status.
    """
    init_db(db_path)
    clean_ticker = ticker.strip().upper()
    clean_action = action.strip().upper()

    if clean_action == "HOLD":
        logger.info("Trade action for %s is HOLD. No order executed.", clean_ticker)
        return {"status": "held", "ticker": clean_ticker}

    if current_price <= 0:
        logger.warning(
            "Invalid current price ($%.2f) for %s. Trade skipped.",
            current_price,
            clean_ticker,
        )
        return {
            "status": "skipped",
            "reason": f"invalid current price: {current_price}",
            "ticker": clean_ticker,
            "action": clean_action,
        }

    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Fetch current cash balance
        cursor.execute("SELECT value FROM portfolio_meta WHERE key = 'cash_balance'")
        meta_row = cursor.fetchone()
        cash_balance = float(meta_row["value"]) if meta_row else DEFAULT_CASH_BALANCE

        # Fetch existing position if any
        cursor.execute(
            "SELECT shares, avg_price FROM portfolio WHERE ticker = ?",
            (clean_ticker,),
        )
        pos_row = cursor.fetchone()
        current_shares = float(pos_row["shares"]) if pos_row else 0.0
        current_avg_price = float(pos_row["avg_price"]) if pos_row else 0.0

        if clean_action == "BUY":
            allocation = confidence * 0.20 * cash_balance
            shares = math.floor(allocation / current_price)

            if shares == 0:
                logger.warning(
                    "Skipped BUY for %s: insufficient allocation ($%.2f at $%.2f/share)",
                    clean_ticker,
                    allocation,
                    current_price,
                )
                return {"status": "skipped", "reason": "insufficient allocation"}

            total_cost = shares * current_price
            new_cash = cash_balance - total_cost

            if pos_row:
                new_shares = current_shares + shares
                new_avg_price = (
                    (current_shares * current_avg_price) + total_cost
                ) / new_shares
                cursor.execute(
                    "UPDATE portfolio SET shares = ?, avg_price = ? WHERE ticker = ?",
                    (new_shares, new_avg_price, clean_ticker),
                )
            else:
                new_shares = float(shares)
                new_avg_price = current_price
                cursor.execute(
                    "INSERT INTO portfolio (ticker, shares, avg_price) VALUES (?, ?, ?)",
                    (clean_ticker, new_shares, new_avg_price),
                )

            cursor.execute(
                "UPDATE portfolio_meta SET value = ? WHERE key = 'cash_balance'",
                (new_cash,),
            )
            conn.commit()

            logger.info(
                "Executed BUY for %s: %d shares at $%.2f (Cost: $%.2f, Remaining Cash: $%.2f)",
                clean_ticker,
                shares,
                current_price,
                total_cost,
                new_cash,
            )

            return {
                "status": "executed",
                "action": "BUY",
                "ticker": clean_ticker,
                "shares": shares,
                "price": current_price,
                "total_cost": round(total_cost, 2),
                "new_shares": new_shares,
                "avg_price": round(new_avg_price, 2),
                "remaining_cash": round(new_cash, 2),
            }

        elif clean_action == "SELL":
            if not pos_row or current_shares <= 0:
                logger.warning(
                    "Skipped SELL for %s: no position to sell",
                    clean_ticker,
                )
                return {"status": "skipped", "reason": "no position to sell"}

            shares_to_sell = math.floor(confidence * current_shares)
            if shares_to_sell == 0:
                logger.warning(
                    "Skipped SELL for %s: sell quantity rounded to 0 (confidence %.2f * %.2f shares)",
                    clean_ticker,
                    confidence,
                    current_shares,
                )
                return {
                    "status": "skipped",
                    "reason": "sell quantity rounded to 0",
                }

            revenue = shares_to_sell * current_price
            new_cash = cash_balance + revenue
            remaining_shares = current_shares - shares_to_sell

            if remaining_shares <= 0:
                cursor.execute(
                    "DELETE FROM portfolio WHERE ticker = ?", (clean_ticker,)
                )
            else:
                cursor.execute(
                    "UPDATE portfolio SET shares = ? WHERE ticker = ?",
                    (remaining_shares, clean_ticker),
                )

            cursor.execute(
                "UPDATE portfolio_meta SET value = ? WHERE key = 'cash_balance'",
                (new_cash,),
            )
            conn.commit()

            logger.info(
                "Executed SELL for %s: %d shares at $%.2f (Revenue: $%.2f, Remaining Shares: %.2f, Remaining Cash: $%.2f)",
                clean_ticker,
                shares_to_sell,
                current_price,
                revenue,
                remaining_shares,
                new_cash,
            )

            return {
                "status": "executed",
                "action": "SELL",
                "ticker": clean_ticker,
                "shares": shares_to_sell,
                "price": current_price,
                "total_revenue": round(revenue, 2),
                "remaining_shares": remaining_shares,
                "remaining_cash": round(new_cash, 2),
            }

        else:
            logger.warning("Unknown action '%s' for %s", action, clean_ticker)
            return {
                "status": "skipped",
                "reason": f"unknown action: {action}",
                "ticker": clean_ticker,
            }


def get_portfolio(db_path: str = DB_PATH) -> dict[str, Any]:
    """Retrieve current cash balance and all open stock positions.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A dictionary containing cash_balance and a list of open positions.
    """
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM portfolio_meta WHERE key = 'cash_balance'")
        meta_row = cursor.fetchone()
        cash_balance = float(meta_row["value"]) if meta_row else DEFAULT_CASH_BALANCE

        cursor.execute(
            "SELECT ticker, shares, avg_price FROM portfolio ORDER BY ticker ASC"
        )
        rows = cursor.fetchall()
        positions = [
            {
                "ticker": str(row["ticker"]),
                "shares": float(row["shares"]),
                "avg_price": float(row["avg_price"]),
            }
            for row in rows
        ]

    return {
        "cash_balance": cash_balance,
        "positions": positions,
    }


def reset_portfolio(db_path: str = DB_PATH) -> dict[str, Any]:
    """Reset the simulated portfolio to its initial default state.

    Deletes all logged decisions, deletes all stock positions, and resets
    the cash balance to 10,000.0.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A dictionary confirming the reset status and default state.
    """
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM decisions;")
        cursor.execute("DELETE FROM portfolio;")
        cursor.execute(
            """
            INSERT OR REPLACE INTO portfolio_meta (key, value)
            VALUES ('cash_balance', ?);
            """,
            (DEFAULT_CASH_BALANCE,),
        )
        conn.commit()

    logger.info("Portfolio and decision history reset to default state ($10,000.00 cash).")
    return {
        "status": "reset",
        "cash_balance": DEFAULT_CASH_BALANCE,
        "positions": [],
        "message": "Portfolio and decision history reset successfully",
    }
