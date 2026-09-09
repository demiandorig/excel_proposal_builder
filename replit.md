# Replit run instructions

The imported FastAPI app runs with the `Start application` workflow:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

The workflow expands `$PORT` through a shell wrapper and serves the Replit web
preview on port 5000. Optional OpenAI and Google Drive secrets can be added in
the Replit Secrets panel when those features are needed.

## Database environments

Development and production intentionally use separate Replit PostgreSQL
databases. The storage layer must be tested in development and then verified
against production after publishing; do not assume that development rows are
automatically copied to production.

The development database has the baseline `market_config` rows:
`__default__`, `__base_ccs__`, and `__t1_ccs__`. If the production database is
newly provisioned, save those same defaults through the published app's Admin
market configuration screen before treating production persistence as seeded.