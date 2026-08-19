from datetime import date
import pytest

from finfree import TSETMCClient, TSETMCError


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
