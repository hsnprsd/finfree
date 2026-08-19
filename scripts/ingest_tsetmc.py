#!/usr/bin/env python3
"""CLI wrapper for the TSETMC-to-ClickHouse ingestion job."""

from finfree.ingest import main


if __name__ == "__main__":
    raise SystemExit(main())
