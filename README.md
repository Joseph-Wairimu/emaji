docker exec -it emaji-db-1 psql -U water_user -d meter_billing -c "\l"

docker compose down
docker compose build --no-cache
docker compose up -d