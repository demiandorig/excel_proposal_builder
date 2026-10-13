"""
Standard intake for avails / forecast data.

Every source the team sends (a Madhive export zip, the Live Sports workbook, the Netflix baseline CSV, the original CTV
model workbook) is read by ONE reader into the same in-memory shape, canonicalised (one spelling per DMA / state /
district), validated against written rules, staged as an immutable version in Postgres, and only then published. See
docs/forecast_data_platform.md for the rules and the commands.

    python -m app.services.forecast_import plan      # read + validate every source, change nothing
    python -m app.services.forecast_import import    # stage, validate and publish (idempotent)
    python -m app.services.forecast_import status
"""
TOOL_VERSION = "forecast-import/1"
