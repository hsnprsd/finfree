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

The local defaults can be overridden with `GRAFANA_ADMIN_USER`,
`GRAFANA_ADMIN_PASSWORD`, `CLICKHOUSE_DB`, `CLICKHOUSE_USER`, and
`CLICKHOUSE_PASSWORD` environment variables.
