-- Apply to a staging database first. This migration is additive and idempotent.
-- The request workflow remains disabled until REQUEST_WORKFLOW_ENABLED=1.
CREATE SEQUENCE IF NOT EXISTS planning_request_number_seq START WITH 1;

CREATE TABLE IF NOT EXISTS planning_intake_events (
    id UUID PRIMARY KEY,
    form_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    payload JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    state TEXT NOT NULL CHECK (state IN ('accepted', 'quarantined')),
    issue TEXT,
    request_id UUID,
    UNIQUE (form_id, submission_id, payload_sha256)
);

CREATE TABLE IF NOT EXISTS planning_requests (
    id UUID PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    intake_event_id UUID NOT NULL UNIQUE REFERENCES planning_intake_events(id) ON DELETE RESTRICT,
    request_type TEXT NOT NULL,
    seller_name TEXT NOT NULL,
    seller_email TEXT NOT NULL,
    market TEXT NOT NULL,
    client_name TEXT NOT NULL DEFAULT '',
    due_date DATE,
    monthly_budget NUMERIC(14,2),
    priority TEXT NOT NULL DEFAULT 'Untriaged'
        CHECK (priority IN ('Untriaged', 'Low', 'Medium', 'High', 'Critical')),
    status TEXT NOT NULL DEFAULT 'New'
        CHECK (status IN ('New', 'Progress', 'Paused', 'Reviewing', 'Done', 'Canceled')),
    owner_id TEXT REFERENCES users(id) ON DELETE RESTRICT,
    details JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewing_at TIMESTAMPTZ,
    review_link TEXT,
    done_at TIMESTAMPTZ,
    canceled_at TIMESTAMPTZ
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'planning_intake_request_fk') THEN
        ALTER TABLE planning_intake_events
            ADD CONSTRAINT planning_intake_request_fk
            FOREIGN KEY (request_id) REFERENCES planning_requests(id) ON DELETE RESTRICT;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS planning_request_activity (
    id UUID PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES planning_requests(id) ON DELETE RESTRICT,
    actor_email TEXT NOT NULL,
    action TEXT NOT NULL,
    before_state JSONB,
    after_state JSONB NOT NULL,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_planning_requests_queue
    ON planning_requests(status, due_date, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_planning_requests_owner
    ON planning_requests(owner_id, status);
CREATE INDEX IF NOT EXISTS idx_planning_intake_submission
    ON planning_intake_events(form_id, submission_id);
CREATE INDEX IF NOT EXISTS idx_planning_activity_request
    ON planning_request_activity(request_id, created_at DESC);

CREATE TABLE IF NOT EXISTS planning_request_proposals (
    request_id UUID NOT NULL REFERENCES planning_requests(id) ON DELETE RESTRICT,
    proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals(proposal_id) ON DELETE RESTRICT,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (request_id, proposal_id)
);

-- Intake identity and original form details are immutable. Corrections need a
-- reviewed revision flow; operational fields are the only mutable columns.
CREATE OR REPLACE FUNCTION planning_request_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Planning requests cannot be deleted';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.code IS DISTINCT FROM OLD.code
       OR NEW.intake_event_id IS DISTINCT FROM OLD.intake_event_id
       OR NEW.request_type IS DISTINCT FROM OLD.request_type
       OR NEW.seller_name IS DISTINCT FROM OLD.seller_name
       OR NEW.seller_email IS DISTINCT FROM OLD.seller_email
       OR NEW.market IS DISTINCT FROM OLD.market
       OR NEW.client_name IS DISTINCT FROM OLD.client_name
       OR NEW.monthly_budget IS DISTINCT FROM OLD.monthly_budget
       OR NEW.details IS DISTINCT FROM OLD.details
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'Original request data is immutable';
    END IF;
    IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'Request version must increase by one';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS planning_request_guard_trigger ON planning_requests;
CREATE TRIGGER planning_request_guard_trigger
    BEFORE UPDATE OR DELETE ON planning_requests
    FOR EACH ROW EXECUTE FUNCTION planning_request_guard();

CREATE OR REPLACE FUNCTION planning_append_only_guard() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Planning audit records cannot be changed or deleted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS planning_activity_guard_trigger ON planning_request_activity;
CREATE TRIGGER planning_activity_guard_trigger
    BEFORE UPDATE OR DELETE ON planning_request_activity
    FOR EACH ROW EXECUTE FUNCTION planning_append_only_guard();

DROP TRIGGER IF EXISTS planning_intake_delete_guard_trigger ON planning_intake_events;
CREATE TRIGGER planning_intake_delete_guard_trigger
    BEFORE DELETE ON planning_intake_events
    FOR EACH ROW EXECUTE FUNCTION planning_append_only_guard();

CREATE OR REPLACE FUNCTION planning_intake_update_guard() RETURNS trigger AS $$
BEGIN
    IF OLD.request_id IS NOT NULL
       OR OLD.state <> 'accepted'
       OR NEW.request_id IS NULL
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.form_id IS DISTINCT FROM OLD.form_id
       OR NEW.submission_id IS DISTINCT FROM OLD.submission_id
       OR NEW.payload_sha256 IS DISTINCT FROM OLD.payload_sha256
       OR NEW.payload IS DISTINCT FROM OLD.payload
       OR NEW.received_at IS DISTINCT FROM OLD.received_at
       OR NEW.state IS DISTINCT FROM OLD.state
       OR NEW.issue IS DISTINCT FROM OLD.issue THEN
        RAISE EXCEPTION 'Original intake event is immutable';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM planning_requests
        WHERE id = NEW.request_id AND intake_event_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'Intake event must link to its own request';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS planning_intake_update_guard_trigger ON planning_intake_events;
CREATE TRIGGER planning_intake_update_guard_trigger
    BEFORE UPDATE ON planning_intake_events
    FOR EACH ROW EXECUTE FUNCTION planning_intake_update_guard();

DROP TRIGGER IF EXISTS planning_proposal_link_guard_trigger ON planning_request_proposals;
CREATE TRIGGER planning_proposal_link_guard_trigger
    BEFORE UPDATE OR DELETE ON planning_request_proposals
    FOR EACH ROW EXECUTE FUNCTION planning_append_only_guard();
