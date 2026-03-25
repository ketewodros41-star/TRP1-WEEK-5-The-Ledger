# Apex Ledger

Apex Ledger is an event-sourced ledger for loan application workflows.  
It includes:

- A PostgreSQL-backed event store
- Domain aggregates and command handlers
- Projection infrastructure
- An MCP server surface for tools/resources
- Async tests for concurrency and lifecycle behavior

## Project Layout

- `src/schema.sql`: database schema for events, streams, outbox, checkpoints, snapshots
- `src/eventstore.py`: async event store implementation
- `src/models/events.py`: event and exception models
- `src/aggregates/`: aggregate roots and replay logic
- `src/handlers/commands.py`: command handlers
- `src/projections/`: read-model and projection daemon code
- `src/mcp/server.py`: MCP server
- `tests/`: async test suite

## Requirements

- Python 3.11+
- PostgreSQL 14+

Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Environment

Set your database connection string:

```bash
# PowerShell
$env:DATABASE_URL = "postgres://localhost/apex_ledger"
```

## Database Setup

Create the database, then apply schema:

```bash
psql -d apex_ledger -f src/schema.sql
```

## Run MCP Server

From project root:

```bash
python -m src.mcp.server
```

The server exposes tools such as `submit_application` and `record_credit_analysis`, plus resources like `ledger://applications/{application_id}`.

## Run Tests

```bash
pytest -q tests
```

If your environment has broad pytest discovery enabled, pointing directly at `tests` helps avoid collecting non-test files.

## Aiven Kafka SSL Producer

Send test messages to Aiven Kafka using SSL certs:

```bash
python scripts/aiven_kafka_ssl_producer.py \
  --bootstrap-server kafka-12a67923-ktewodros41-271c.i.aivencloud.com:26372 \
  --topic trp \
  --ca-file ca.pem \
  --cert-file service.cert \
  --key-file service.key \
  --count 100 \
  --interval-seconds 1
```

## Notes

- Event ordering is stream-local (`stream_position`) and global (`global_position` identity).
- Optimistic concurrency is enforced in `append` with transaction + stream row locking.
- Outbox writes are performed in the same transaction as event appends.
