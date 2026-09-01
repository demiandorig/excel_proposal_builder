# Replit run instructions

The imported FastAPI app runs with the `Start application` workflow:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

The workflow expands `$PORT` through a shell wrapper and serves the Replit web
preview on port 5000. Optional OpenAI and Google Drive secrets can be added in
the Replit Secrets panel when those features are needed.