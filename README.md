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
