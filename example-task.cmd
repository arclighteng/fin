@echo off
setlocal
pushd "%~dp0"

docker compose run --rm fin health
docker compose run --rm fin sync --lookback-days 7
docker compose run --rm fin report --days 60 --top 25
docker compose run --rm fin export-csv --days 400

popd
endlocal
