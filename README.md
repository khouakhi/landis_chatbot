# LandIS chatbot

Cited question answering for the National Soil Data Portal for England and Wales.

## Run locally

```bash
cd code
cp .env.example .env        # set MISTRAL_API_KEY and DATABASE_URL
uv sync --all-packages
uv run --directory apps/api uvicorn main:app --port 8080
```

- Chat: http://127.0.0.1:8080/web/
- Health: http://127.0.0.1:8080/health

## Deploy on Render

`render.yaml` defines the web service and Postgres. Set `MISTRAL_API_KEY` in the Render dashboard. Never commit `.env`.
