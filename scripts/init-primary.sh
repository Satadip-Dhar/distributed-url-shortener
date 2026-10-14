#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE USER replicator WITH REPLICATION ENCRYPTED PASSWORD 'repl_password';
    SELECT pg_create_physical_replication_slot('replica_slot_1');
EOSQL

echo "host replication replicator 0.0.0.0/0 md5" >> "$PGDATA/pg_hba.conf"
