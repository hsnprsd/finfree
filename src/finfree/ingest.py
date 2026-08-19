"""Load TSETMC daily history into ClickHouse."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
import os
import re
import sys
import time
from typing import Any, Protocol
from urllib.parse import urlparse

import clickhouse_connect

from .tsetmc import Candle, Instrument, TSETMCClient


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CandleSink(Protocol):
    def ensure_schema(self) -> None: ...

    def write_candles(self, instrument: Instrument, candles: list[Candle]) -> int: ...


@dataclass(frozen=True, slots=True)
class IngestionResult:
    instruments: int
    rows: int
    failures: dict[str, str]


class ClickHouseClient:
    """ClickHouse schema manager and typed bulk writer."""

    def __init__(
        self,
        url: str = "http://localhost:8123",
        *,
        database: str = "finfree",
        table: str = "market_data",
        username: str = "grafana",
        password: str = "grafana",
        timeout: float = 30.0,
        batch_size: int = 10_000,
        client: Any | None = None,
    ) -> None:
        if not _IDENTIFIER.fullmatch(database):
            raise ValueError("database must be a ClickHouse identifier")
        if not _IDENTIFIER.fullmatch(table):
            raise ValueError("table must be a ClickHouse identifier")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise ValueError("url must be an HTTP or HTTPS ClickHouse URL")
        if parsed_url.path not in {"", "/"} or parsed_url.query or parsed_url.fragment:
            raise ValueError("url must not contain a path, query, or fragment")
        self.database = database
        self.table = table
        self.batch_size = batch_size
        self._client = client or clickhouse_connect.get_client(
            host=parsed_url.hostname,
            port=parsed_url.port or (8443 if parsed_url.scheme == "https" else 8123),
            username=username,
            password=password,
            secure=parsed_url.scheme == "https",
            connect_timeout=timeout,
            send_receive_timeout=timeout,
        )

    def ensure_schema(self) -> None:
        self._client.command(f"CREATE DATABASE IF NOT EXISTS {self.database}")
        self._client.command(f"""
            CREATE TABLE IF NOT EXISTS {self.database}.{self.table}
            (
                instrument_id UInt64,
                symbol LowCardinality(String),
                name String,
                flow Nullable(UInt8),
                date Date,
                open Int64,
                high Int64,
                low Int64,
                close Int64,
                volume UInt64,
                trades Nullable(UInt64),
                ingested_at DateTime64(3) DEFAULT now64(3)
            )
            ENGINE = ReplacingMergeTree(ingested_at)
            PARTITION BY toYear(date)
            ORDER BY (symbol, date)
        """)

    def write_candles(self, instrument: Instrument, candles: list[Candle]) -> int:
        written = 0
        for batch in _batched(candles, self.batch_size):
            rows = [
                (
                    int(instrument.instrument_id),
                    candle.symbol,
                    instrument.name,
                    instrument.flow,
                    candle.date,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    candle.trades,
                )
                for candle in batch
            ]
            self._client.insert(
                f"{self.database}.{self.table}",
                rows,
                column_names=[
                    "instrument_id",
                    "symbol",
                    "name",
                    "flow",
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "trades",
                ],
            )
            written += len(batch)
        return written


def ingest_all(
    tsetmc: TSETMCClient,
    sink: CandleSink,
    *,
    start: date | str | None = None,
    end: date | str | None = None,
    attempts: int = 3,
    retry_delay: float = 2.0,
    request_delay: float = 0.25,
    log: Callable[[str], Any] = print,
    sleep: Callable[[float], Any] = time.sleep,
) -> IngestionResult:
    """Fetch every market-watch instrument and store its daily candles."""
    if attempts < 1:
        raise ValueError("attempts must be positive")
    if retry_delay < 0 or request_delay < 0:
        raise ValueError("delays must not be negative")

    sink.ensure_schema()
    instruments = _retry(
        tsetmc.list_instruments,
        attempts=attempts,
        delay=retry_delay,
        sleep=sleep,
    )
    rows = 0
    failures: dict[str, str] = {}
    for index, instrument in enumerate(instruments, start=1):
        try:
            candles = _retry(
                lambda: tsetmc.read_market_data_by_id(
                    instrument.instrument_id,
                    instrument.symbol,
                    start=start,
                    end=end,
                ),
                attempts=attempts,
                delay=retry_delay,
                sleep=sleep,
            )
            written = sink.write_candles(instrument, candles)
            rows += written
            log(f"[{index}/{len(instruments)}] {instrument.symbol}: {written} rows")
        except Exception as exc:
            failures[instrument.instrument_id] = str(exc)
            log(f"[{index}/{len(instruments)}] {instrument.symbol}: FAILED: {exc}")
        if index < len(instruments) and request_delay:
            sleep(request_delay)

    return IngestionResult(instruments=len(instruments), rows=rows, failures=failures)


def _retry(
    operation: Callable[[], Any],
    *,
    attempts: int,
    delay: float,
    sleep: Callable[[float], Any],
) -> Any:
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception:
            if attempt == attempts:
                raise
            sleep(delay * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


def _batched(items: list[Candle], size: int) -> Iterable[list[Candle]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch all active TSETMC symbols and write their daily history to ClickHouse."
    )
    parser.add_argument("--start", help="inclusive Gregorian date (YYYY-MM-DD)")
    parser.add_argument("--end", help="inclusive Gregorian date (YYYY-MM-DD)")
    parser.add_argument("--request-delay", type=float, default=0.25)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument("--batch-size", type=int, default=10_000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sink = ClickHouseClient(
        os.getenv("CLICKHOUSE_URL", "http://localhost:8123"),
        database=os.getenv("CLICKHOUSE_DB", "finfree"),
        table=os.getenv("CLICKHOUSE_TABLE", "market_data"),
        username=os.getenv("CLICKHOUSE_USER", "grafana"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "grafana"),
        batch_size=args.batch_size,
    )
    result = ingest_all(
        TSETMCClient(),
        sink,
        start=args.start,
        end=args.end,
        attempts=args.attempts,
        retry_delay=args.retry_delay,
        request_delay=args.request_delay,
    )
    print(
        f"Finished: {result.instruments} instruments, {result.rows} rows, "
        f"{len(result.failures)} failures"
    )
    return 1 if result.failures else 0


if __name__ == "__main__":
    sys.exit(main())
