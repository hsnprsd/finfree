"""A small client for daily historical prices published by TSETMC.

TSETMC exposes daily prices, so intraday intervals are deliberately not accepted.
Weekly and monthly candles are built locally from those daily observations.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import gzip
import json
from typing import Any, Callable, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


BASE_URL = "https://cdn.tsetmc.com/api"
MARKET_WATCH_URL = (
    "https://old.tsetmc.com/tsev2/data/MarketWatchInit.aspx?h=0&r=0"
)
_FARSI_NORMALIZATION = str.maketrans("يك", "یک")
_HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "Chrome/126 Safari/537.36"
    ),
    "Referer": "https://www.tsetmc.com/",
    "Origin": "https://www.tsetmc.com",
}
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


@dataclass(frozen=True, slots=True)
class Instrument:
    """A TSETMC instrument returned by the market watch."""

    instrument_id: str
    symbol: str
    name: str = ""
    flow: int | None = None


Transport = Callable[[str], dict[str, Any]]
TextTransport = Callable[[str], str]


class TSETMCClient:
    """Client for resolving TSETMC symbols and reading their price history.

    Supply ``transport`` in tests or when requests must use an application-wide
    HTTP client. It receives the complete URL and must return decoded JSON.
    """

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        transport: Transport | None = None,
        text_transport: TextTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self._transport = transport or self._get_json
        self._text_transport = text_transport or self._get_text

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
        clean_symbol, start_date, end_date = _validate_request(symbol, timeframe, start, end)
        instrument_id = self._resolve_instrument(clean_symbol)
        return self.read_market_data_by_id(
            instrument_id,
            clean_symbol,
            timeframe,
            start=start_date,
            end=end_date,
        )

    def list_instruments(self) -> list[Instrument]:
        """Return the instruments currently exposed by TSETMC market watch."""
        instruments = _instruments_from_market_watch(
            self._text_transport(MARKET_WATCH_URL)
        )
        if not instruments:
            raise TSETMCError("TSETMC market watch did not contain any instruments")
        return sorted(instruments.values(), key=lambda item: (item.symbol, item.instrument_id))

    def read_market_data_by_id(
        self,
        instrument_id: str | int,
        symbol: str,
        timeframe: Timeframe = "1d",
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> list[Candle]:
        """Return candles for a known TSETMC instrument id without searching."""
        clean_symbol, start_date, end_date = _validate_request(symbol, timeframe, start, end)
        clean_id = str(instrument_id).strip()
        if not clean_id.isdigit():
            raise ValueError("instrument_id must contain only digits")
        raw = self._transport(
            f"{BASE_URL}/ClosingPrice/GetClosingPriceDailyList/{clean_id}/0"
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
        request = Request(url, headers=_HEADERS)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TSETMCError(f"TSETMC request failed: {url}") from exc

    def _get_text(self, url: str) -> str:
        request = Request(url, headers=_HEADERS)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                content = response.read()
                if response.headers.get("Content-Encoding") == "gzip" or content.startswith(b"\x1f\x8b"):
                    content = gzip.decompress(content)
                charset = response.headers.get_content_charset() or "utf-8"
                return content.decode(charset).translate(_FARSI_NORMALIZATION)
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, gzip.BadGzipFile) as exc:
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


def _validate_request(
    symbol: str,
    timeframe: Timeframe,
    start: date | datetime | str | None,
    end: date | datetime | str | None,
) -> tuple[str, date | None, date | None]:
    clean_symbol = symbol.strip()
    if not clean_symbol:
        raise ValueError("symbol must not be empty")
    if timeframe not in {"1d", "1w", "1mo"}:
        raise ValueError("timeframe must be one of: '1d', '1w', '1mo'")

    start_date = _as_date(start, "start")
    end_date = _as_date(end, "end")
    if start_date and end_date and start_date > end_date:
        raise ValueError("start must be on or before end")
    return clean_symbol, start_date, end_date


def _daily_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("closingPriceDaily", "closingPriceDailyList", "data"):
        records = payload.get(key)
        if isinstance(records, list):
            return records
    raise TSETMCError("TSETMC response did not contain daily price records")


def _instruments_from_market_watch(payload: str) -> dict[str, Instrument]:
    parts = payload.split("@")
    if len(parts) != 5:
        raise TSETMCError("TSETMC returned a malformed market watch")

    instruments: dict[str, Instrument] = {}
    for raw_record in parts[2].split(";"):
        fields = raw_record.split(",")
        if len(fields) < 18:
            continue
        instrument_id, symbol, name = fields[0].strip(), fields[2].strip(), fields[3].strip()
        if not instrument_id.isdigit() or not symbol or any(char.isdigit() for char in symbol):
            continue
        instruments[instrument_id] = Instrument(
            instrument_id=instrument_id,
            symbol=symbol,
            name=name,
            flow=_safe_optional_int(fields[17]),
        )
    return instruments


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


def _safe_optional_int(value: Any) -> int | None:
    try:
        return _optional_int(value)
    except (TypeError, ValueError):
        return None


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
