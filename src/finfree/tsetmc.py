"""A small client for daily historical prices published by TSETMC.

TSETMC exposes daily prices, so intraday intervals are deliberately not accepted.
Weekly and monthly candles are built locally from those daily observations.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from typing import Any, Callable, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


BASE_URL = "https://cdn.tsetmc.com/api"
Timeframe = Literal["1d", "1w", "1mo"]


class TSETMCError(RuntimeError):
    """Raised when TSETMC cannot provide usable market data."""


@dataclass(frozen=True, slots=True)
class Candle:
    """One OHLCV candle returned by :func:`read_market_data`."""

    symbol: str
    date: date
    open: int
    high: int
    low: int
    close: int
    volume: int
    trades: int | None = None


Transport = Callable[[str], dict[str, Any]]


class TSETMCClient:
    """Client for resolving TSETMC symbols and reading their price history.

    Supply ``transport`` in tests or when requests must use an application-wide
    HTTP client. It receives the complete URL and must return decoded JSON.
    """

    def __init__(self, *, timeout: float = 20.0, transport: Transport | None = None) -> None:
        self.timeout = timeout
        self._transport = transport or self._get_json

    def read_market_data(
        self,
        symbol: str,
        timeframe: Timeframe = "1d",
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> list[Candle]:
        """Return historical OHLCV candles for ``symbol``.

        ``timeframe`` may be ``"1d"``, ``"1w"``, or ``"1mo"``. ``start`` and
        ``end`` are inclusive Gregorian dates (``YYYY-MM-DD``); when omitted,
        TSETMC's available history is returned. The returned list is ordered from
        oldest to newest.
        """
        clean_symbol = symbol.strip()
        if not clean_symbol:
            raise ValueError("symbol must not be empty")
        if timeframe not in {"1d", "1w", "1mo"}:
            raise ValueError("timeframe must be one of: '1d', '1w', '1mo'")

        start_date = _as_date(start, "start")
        end_date = _as_date(end, "end")
        if start_date and end_date and start_date > end_date:
            raise ValueError("start must be on or before end")

        instrument_id = self._resolve_instrument(clean_symbol)
        raw = self._transport(
            f"{BASE_URL}/ClosingPrice/GetClosingPriceDailyList/{instrument_id}/0"
        )
        candles = [
            _candle_from_record(clean_symbol, record)
            for record in _daily_records(raw)
        ]
        candles = _deduplicate_and_sort(candles)
        candles = [
            candle
            for candle in candles
            if (start_date is None or candle.date >= start_date)
            and (end_date is None or candle.date <= end_date)
        ]
        return _resample(candles, timeframe)

    def _resolve_instrument(self, symbol: str) -> str:
        payload = self._transport(
            f"{BASE_URL}/Instrument/GetInstrumentSearch/{quote(symbol, safe='')}"
        )
        matches = payload.get("instrumentSearch") or payload.get("instrumentSearchResult") or []
        exact_matches = [
            item for item in matches
            if str(item.get("lVal18AFC") or item.get("symbol") or "").strip() == symbol
        ]
        candidate = (exact_matches or matches[:1])
        if not candidate:
            raise TSETMCError(f"No TSETMC instrument found for symbol {symbol!r}")

        instrument_id = candidate[0].get("insCode") or candidate[0].get("instrumentId")
        if instrument_id is None:
            raise TSETMCError(f"TSETMC returned an instrument without an id for {symbol!r}")
        return str(instrument_id)

    def _get_json(self, url: str) -> dict[str, Any]:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "finfree/0.1"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TSETMCError(f"TSETMC request failed: {url}") from exc


def read_market_data(
    symbol: str,
    timeframe: Timeframe = "1d",
    *,
    start: date | datetime | str | None = None,
    end: date | datetime | str | None = None,
) -> list[Candle]:
    """Read historical TSETMC candles for one symbol.

    This is the package-level convenience wrapper around :class:`TSETMCClient`.
    """
    return TSETMCClient().read_market_data(symbol, timeframe, start=start, end=end)


def _as_date(value: date | datetime | str | None, name: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD)") from exc
    raise TypeError(f"{name} must be a date, datetime, ISO date string, or None")


def _daily_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("closingPriceDaily", "closingPriceDailyList", "data"):
        records = payload.get(key)
        if isinstance(records, list):
            return records
    raise TSETMCError("TSETMC response did not contain daily price records")


def _candle_from_record(symbol: str, record: dict[str, Any]) -> Candle:
    try:
        trading_date = _tsetmc_date(record["dEven"])
        return Candle(
            symbol=symbol,
            date=trading_date,
            open=int(record["priceFirst"]),
            high=int(record["priceMax"]),
            low=int(record["priceMin"]),
            close=int(record["pClosing"]),
            volume=int(record.get("qTotTran5J", record.get("qTotCap", 0))),
            trades=_optional_int(record.get("zTotTran")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TSETMCError("TSETMC returned a malformed daily price record") from exc


def _tsetmc_date(value: Any) -> date:
    text = str(value)
    if len(text) != 8 or not text.isdigit():
        raise ValueError("dEven must be an eight digit Gregorian date")
    return date(int(text[:4]), int(text[4:6]), int(text[6:]))


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _deduplicate_and_sort(candles: list[Candle]) -> list[Candle]:
    by_date = {candle.date: candle for candle in candles}
    return [by_date[key] for key in sorted(by_date)]


def _resample(candles: list[Candle], timeframe: Timeframe) -> list[Candle]:
    if timeframe == "1d":
        return candles

    groups: dict[date | tuple[int, int], list[Candle]] = defaultdict(list)
    for candle in candles:
        key = _iran_week_start(candle.date) if timeframe == "1w" else (candle.date.year, candle.date.month)
        groups[key].append(candle)

    result: list[Candle] = []
    for group in groups.values():
        first, last = group[0], group[-1]
        result.append(Candle(
            symbol=first.symbol,
            date=first.date,
            open=first.open,
            high=max(candle.high for candle in group),
            low=min(candle.low for candle in group),
            close=last.close,
            volume=sum(candle.volume for candle in group),
            trades=_sum_optional(candle.trades for candle in group),
        ))
    return result


def _iran_week_start(day: date) -> date:
    """Return the Saturday beginning the Gregorian week containing ``day``."""
    return day - timedelta(days=(day.weekday() + 2) % 7)


def _sum_optional(values: Iterable[int | None]) -> int | None:
    values = list(values)
    return None if all(value is None for value in values) else sum(value or 0 for value in values)
