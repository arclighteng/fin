# fin (local-first)

## Run (Docker)
```powershell
docker compose build --no-cache
docker compose run --rm fin health
docker compose run --rm fin sync --annual-bootstrap
docker compose run --rm fin report --days 400 --top 25
docker compose run --rm fin export-csv --days 400

## Sheets workflow (recommended)
1) Run: `docker compose run --rm fin export-csv --days 400`
2) Import `./exports/actions.csv` into Google Sheets
3) Use “Convert to table” and sort/filter by:
   - `monthly_est_cents` (desc)
   - `confidence` (desc)
   - `status` (blank first)
4) When you decide to act on an item:
   - `docker compose run --rm fin subs-pick <keyword> --note "<cancel|downgrade|verify annual|price check>"`
   - Re-export to refresh `actions.csv`
