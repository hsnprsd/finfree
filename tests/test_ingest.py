from datetime import date

from finfree import Candle, Instrument, TSETMCError
from finfree.ingest import ClickHouseClient, ingest_all


def candle(symbol="A"):
    return Candle(symbol, date(2025, 1, 1), 10, 12, 9, 11, 100, 5)


class FakeClickHouseDriver:
    def __init__(self):
        self.commands = []
        self.inserts = []

    def command(self, query):
        self.commands.append(query)

    def insert(self, table, rows, *, column_names):
        self.inserts.append((table, rows, column_names))


def test_clickhouse_writer_creates_schema_and_uses_typed_batches():
    driver = FakeClickHouseDriver()
    writer = ClickHouseClient(client=driver, batch_size=1)
    instrument = Instrument("42", "A", "Alpha", 1)

    writer.ensure_schema()
    written = writer.write_candles(instrument, [candle(), candle()])

    assert "CREATE DATABASE IF NOT EXISTS finfree" in driver.commands[0]
    assert "ReplacingMergeTree" in driver.commands[1]
    assert "ORDER BY (symbol, date)" in driver.commands[1]
    assert written == 2
    assert len(driver.inserts) == 2
    table, rows, columns = driver.inserts[0]
    assert table == "finfree.market_data"
    assert rows[0] == (42, "A", "Alpha", 1, date(2025, 1, 1), 10, 12, 9, 11, 100, 5)
    assert columns[:5] == ["instrument_id", "symbol", "name", "flow", "date"]


def test_ingestion_retries_and_continues_after_a_symbol_failure():
    instruments = [Instrument("1", "A"), Instrument("2", "B")]

    class FakeTSETMC:
        def __init__(self):
            self.calls = []

        def list_instruments(self):
            return instruments

        def read_market_data_by_id(self, instrument_id, symbol, *, start, end):
            self.calls.append(instrument_id)
            if instrument_id == "2":
                raise TSETMCError("unavailable")
            return [candle(symbol)]

    class FakeSink:
        def __init__(self):
            self.schema_created = False
            self.writes = []

        def ensure_schema(self):
            self.schema_created = True

        def write_candles(self, instrument, candles):
            self.writes.append((instrument, candles))
            return len(candles)

    tsetmc = FakeTSETMC()
    sink = FakeSink()
    messages = []
    sleeps = []

    result = ingest_all(
        tsetmc,
        sink,
        attempts=2,
        retry_delay=1,
        request_delay=0.25,
        log=messages.append,
        sleep=sleeps.append,
    )

    assert sink.schema_created
    assert result.instruments == 2
    assert result.rows == 1
    assert result.failures == {"2": "unavailable"}
    assert tsetmc.calls == ["1", "2", "2"]
    assert sleeps == [0.25, 1]
    assert messages[-1] == "[2/2] B: FAILED: unavailable"
