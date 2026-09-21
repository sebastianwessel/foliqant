"""The fixed, explicitly applied initial durable-store migration."""

from hashlib import sha256

SCHEMA_VERSION = 1
MIGRATION_LOCK = 727747640167923795
MIGRATION_SQL = """
CREATE SCHEMA foliqant;
CREATE TABLE foliqant.schema_version (
    version integer PRIMARY KEY CHECK (version > 0),
    sql_hash text NOT NULL CHECK (length(sql_hash) = 64)
);
CREATE TABLE foliqant.executions (
    execution_id uuid PRIMARY KEY,
    scope text NOT NULL CHECK (scope ~ '^[0-9a-f]{64}$'),
    tenant_id text,
    principal_id text,
    workflow text NOT NULL CHECK (length(workflow) <= 256
        AND workflow ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'),
    revision text NOT NULL CHECK (length(revision) BETWEEN 1 AND 512),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
    input_digest text NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    submission json NOT NULL CHECK (json_typeof(submission) = 'object'),
    accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    deadline timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'accepted'
        CHECK (status IN ('accepted','running','completed','needs_review','failed','cancelled')),
    current_step text CHECK (length(current_step) <= 256
        AND current_step ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'),
    cancel_requested boolean NOT NULL DEFAULT false,
    owner text CHECK (length(owner) BETWEEN 1 AND 256),
    fence bigint NOT NULL DEFAULT 0 CHECK (fence >= 0),
    lease_until timestamptz,
    result json,
    UNIQUE (scope, workflow, idempotency_key),
    CHECK ((status IN ('completed','needs_review','failed','cancelled')) = (result IS NOT NULL)),
    CHECK ((status = 'running') = (owner IS NOT NULL AND lease_until IS NOT NULL)),
    CHECK ((owner IS NULL) = (lease_until IS NULL)),
    CHECK (status <> 'running' OR fence > 0),
    CHECK (result IS NULL OR json_typeof(result) = 'object')
);
CREATE INDEX execution_claim ON foliqant.executions (accepted_at, execution_id)
    WHERE status IN ('accepted','running');
CREATE TABLE foliqant.checkpoints (
    execution_id uuid NOT NULL REFERENCES foliqant.executions(execution_id),
    step_id text NOT NULL CHECK (length(step_id) <= 256
        AND step_id ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'),
    position integer NOT NULL CHECK (position > 0),
    record json NOT NULL CHECK (json_typeof(record) = 'object'),
    next_step text CHECK (length(next_step) <= 256
        AND next_step ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'),
    CHECK (next_step IS NULL OR next_step <> step_id),
    PRIMARY KEY (execution_id, step_id),
    UNIQUE (execution_id, position)
);
CREATE TABLE foliqant.attempts (
    execution_id uuid NOT NULL REFERENCES foliqant.executions(execution_id),
    step_id text NOT NULL CHECK (length(step_id) <= 256
        AND step_id ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'),
    kind text NOT NULL CHECK (kind IN ('model','tool')),
    ticket integer NOT NULL CHECK (ticket > 0),
    usage json,
    PRIMARY KEY (execution_id, step_id, kind, ticket),
    CHECK (kind = 'model' OR usage IS NULL)
);
CREATE TABLE foliqant.outbox (
    event_id uuid PRIMARY KEY,
    execution_id uuid NOT NULL UNIQUE REFERENCES foliqant.executions(execution_id),
    destination text NOT NULL CHECK (length(destination) BETWEEN 1 AND 256),
    result json NOT NULL CHECK (json_typeof(result) = 'object'),
    max_attempts integer NOT NULL CHECK (max_attempts BETWEEN 1 AND 32),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0 AND attempts <= max_attempts),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','delivered','exhausted')),
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    owner text CHECK (length(owner) BETWEEN 1 AND 256),
    fence bigint NOT NULL DEFAULT 0 CHECK (fence >= 0),
    lease_until timestamptz,
    last_error text CHECK (last_error IN (
        'invalid_configuration','invalid_input','invalid_output','unauthenticated','forbidden',
        'not_found','missing_binding','timeout','budget_exhausted','dependency_failure','conflict',
        'uncertain_effect','cancelled','capacity_exceeded')),
    CHECK ((owner IS NULL) = (lease_until IS NULL)),
    CHECK (status = 'pending' OR owner IS NULL),
    CHECK (owner IS NULL OR (attempts > 0 AND fence > 0)),
    CHECK (status <> 'exhausted' OR attempts = max_attempts),
    CHECK (status <> 'delivered' OR attempts > 0)
);
CREATE INDEX outbox_claim ON foliqant.outbox (available_at, event_id) WHERE status = 'pending';
"""
MIGRATION_HASH = sha256(MIGRATION_SQL.encode()).hexdigest()
