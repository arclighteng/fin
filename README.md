# fin (local-first)

## Run (Docker)
```powershell
docker compose build --no-cache
docker compose run --rm fin health
docker compose run --rm fin sync --annual-bootstrap
docker compose run --rm fin report --days 400 --top 25
docker compose run --rm fin export-csv --days 400
