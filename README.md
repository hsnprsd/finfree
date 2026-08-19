# finfree

Dependency-free access to historical market data published by TSETMC.

```python
from finfree import read_market_data

candles = read_market_data("عیار", "1w", start="2025-01-01")
```

`read_market_data(symbol, timeframe, start=None, end=None)` returns a list of
`Candle` objects in chronological order. Supported timeframes are `1d`, `1w`,
and `1mo`. TSETMC provides daily data; weekly and monthly candles are aggregated
locally.

## Local ClickHouse and Grafana

Start both services with:

```sh
docker compose up -d
```

Grafana is available at <http://localhost:3000> (`admin` / `admin`). Its
`ClickHouse` data source is installed and provisioned automatically. ClickHouse
is also exposed over HTTP at <http://localhost:8123> and through its native
protocol on port `9000`.

The provisioned `Finfree` folder contains two dashboards:

- **Market Overview** shows the latest market snapshot, breadth, traded value,
  and top movers.
- **Symbol Detail** provides a symbol selector, price and return statistics,
  and an OHLCV chart.

The local defaults can be overridden with `GRAFANA_ADMIN_USER`,
`GRAFANA_ADMIN_PASSWORD`, `CLICKHOUSE_DB`, `CLICKHOUSE_USER`, and
`CLICKHOUSE_PASSWORD` environment variables.

## Importing TSETMC history

The ingestion script discovers the instruments in TSETMC's current market
watch, downloads their complete daily history, and writes it to
`finfree.market_data`. Symbols containing numbers are excluded:

```sh
uv run --extra ingest finfree-ingest
```

Use `--start YYYY-MM-DD` or `--end YYYY-MM-DD` to restrict the rows written.
The script processes requests sequentially, retries transient failures, and
reports any symbols it could not ingest. It exits non-zero if any symbol fails.

ClickHouse connection settings use the same `CLICKHOUSE_DB`, `CLICKHOUSE_USER`,
and `CLICKHOUSE_PASSWORD` variables as Compose. `CLICKHOUSE_URL` defaults to
`http://localhost:8123`, and `CLICKHOUSE_TABLE` defaults to `market_data`.
