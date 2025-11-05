set -e

export PGPASSWORD=$POSTGRES_PASSWORD

BACKUP_DIR="/backups"
DB_NAME=$POSTGRES_DB
USER=$POSTGRES_USER
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_$TIMESTAMP.sql"

echo "[$(date)] Starting backup for database '$DB_NAME'..."
pg_dump -U "$USER" -h db -F c -b -v -f "$BACKUP_FILE" "$DB_NAME"
echo "[$(date)] Backup completed: $BACKUP_FILE"

find "$BACKUP_DIR" -type f -name "*.sql" -mtime +7 -delete
echo "Old backups deleted (older than 7 days)"

