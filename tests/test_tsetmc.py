from datetime import date
from email.message import Message
import gzip
import pytest

from finfree import Instrument, TSETMCClient, TSETMCError
import finfree.tsetmc as tsetmc_module


def record(day, opening, high, low, close, volume, trades):
    return {
        "dEven": day,
        "priceFirst": opening,
        "priceMax": high,
        "priceMin": low,
        "pClosing": close,
        "qTotTran5J": volume,
        "zTotTran": trades,
    }


@pytest.fixture
def client():
    urls = []

    def transport(url):
        urls.append(url)
        if "GetInstrumentSearch" in url:
            return {
                "instrumentSearch": [
                    {"lVal18AFC": "OTHER", "insCode": "1"},
                    {"lVal18AFC": "TEST", "insCode": "42"},
                ]
            }
        return {
            "closingPriceDaily": [
                record(20250103, 103, 107, 101, 105, 30, 3),
                record(20250101, 100, 104, 99, 102, 10, 1),
                record(20250102, 102, 106, 100, 103, 20, 2),
            ]
        }

    return TSETMCClient(transport=transport), urls


def test_reads_and_sorts_daily_candles(client):
    market_client, urls = client
    candles = market_client.read_market_data("TEST")

    assert [candle.date for candle in candles] == [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]
    assert candles[0].close == 102
    assert urls[0].endswith("/TEST")
    assert urls[1].endswith("/42/0")


def test_filters_dates_and_resamples_monthly(client):
    market_client, _ = client
    candles = market_client.read_market_data("TEST", "1mo", start="2025-01-02")

    assert len(candles) == 1
    candle = candles[0]
    assert (candle.date, candle.open, candle.high, candle.low, candle.close, candle.volume, candle.trades) == (
        date(2025, 1, 2), 102, 107, 100, 105, 50, 5
    )


def test_weekly_candles_begin_on_saturday():
    def transport(url):
        if "GetInstrumentSearch" in url:
            return {"instrumentSearch": [{"lVal18AFC": "TEST", "insCode": "42"}]}
        return {"closingPriceDaily": [
            record(20250103, 100, 105, 99, 104, 10, 1),  # Friday
            record(20250104, 104, 108, 103, 107, 20, 2),  # Saturday
        ]}

    candles = TSETMCClient(transport=transport).read_market_data("TEST", "1w")

    assert [candle.date for candle in candles] == [date(2025, 1, 3), date(2025, 1, 4)]


def test_rejects_invalid_timeframe(client):
    market_client, _ = client

    with pytest.raises(ValueError, match="timeframe"):
        market_client.read_market_data("TEST", "1h")


def test_raises_for_missing_symbol():
    client = TSETMCClient(transport=lambda _: {"instrumentSearch": []})

    with pytest.raises(TSETMCError, match="No TSETMC instrument"):
        client.read_market_data("UNKNOWN")


def test_reads_history_by_instrument_id_without_search():
    urls = []

    def transport(url):
        urls.append(url)
        return {"closingPriceDaily": [
            record(20250101, 100, 104, 99, 102, 10, 1),
        ]}

    candles = TSETMCClient(transport=transport).read_market_data_by_id("42", "TEST")

    assert len(candles) == 1
    assert urls == [
        "https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/42/0"
    ]


def test_lists_unique_market_watch_instruments():
    first = [
        "42", "IRO1TEST0001", "TEST", "Test Company", "123000", "10", "11",
        "12", "1", "100", "1100", "9", "13", "8", "0", "0", "1", "2",
        "00", "14", "7", "1000", "300",
    ]
    second = first.copy()
    second[0] = "43"
    second[2] = "ALPHA"
    second[3] = "Alpha Company"
    numbered = first.copy()
    numbered[0] = "44"
    numbered[2] = "TEST۲"
    payload = (
        "header@market-state@"
        + ";".join([
            ",".join(first),
            ",".join(second),
            ",".join(numbered),
            ",".join(first),
        ])
        + "@limits@123"
    )
    urls = []

    def text_transport(url):
        urls.append(url)
        return payload

    client = TSETMCClient(
        transport=lambda _: pytest.fail("JSON transport must not be used for market watch"),
        text_transport=text_transport,
    )

    assert client.list_instruments() == [
        Instrument("43", "ALPHA", "Alpha Company", 2),
        Instrument("42", "TEST", "Test Company", 2),
    ]
    assert urls == [
        "https://old.tsetmc.com/tsev2/data/MarketWatchInit.aspx?h=0&r=0"
    ]


def test_decodes_gzip_market_watch_and_normalizes_persian(monkeypatch):
    class Response:
        headers = Message()
        headers["Content-Encoding"] = "gzip"
        headers["Content-Type"] = "text/plain; charset=utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return gzip.compress("يك".encode())

    monkeypatch.setattr(tsetmc_module, "urlopen", lambda *_args, **_kwargs: Response())

    assert TSETMCClient()._get_text("https://example.test") == "یک"
