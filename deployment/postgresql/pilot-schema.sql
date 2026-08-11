BEGIN;

CREATE TABLE IF NOT EXISTS pilot_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL CHECK (length(checksum) = 64),
    applied_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','suspended','archived'))
);

CREATE TABLE IF NOT EXISTS businesses (
    business_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    legal_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    base_currency TEXT NOT NULL CHECK (base_currency ~ '^[A-Z]{3}$'),
    timezone_name TEXT NOT NULL,
    UNIQUE (business_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS pilot_runtime_bindings (
    role_name TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    FOREIGN KEY (business_id, tenant_id)
      REFERENCES businesses(business_id, tenant_id)
);

REVOKE ALL ON pilot_runtime_bindings FROM PUBLIC;

CREATE OR REPLACE FUNCTION aos_scope_tenant() RETURNS TEXT
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT tenant_id FROM pilot_runtime_bindings
  WHERE role_name = session_user::text
$$;

CREATE OR REPLACE FUNCTION aos_scope_business() RETURNS TEXT
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT business_id FROM pilot_runtime_bindings
  WHERE role_name = session_user::text
$$;

REVOKE ALL ON FUNCTION aos_scope_tenant() FROM PUBLIC;
REVOKE ALL ON FUNCTION aos_scope_business() FROM PUBLIC;

CREATE TABLE IF NOT EXISTS actors (
    actor_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human','agent','service')),
    roles_json TEXT NOT NULL CHECK (jsonb_typeof(roles_json::jsonb) = 'array'),
    business_ids_json TEXT NOT NULL CHECK (jsonb_typeof(business_ids_json::jsonb) = 'array'),
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    UNIQUE (actor_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS evidence_records (
    evidence_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    statement TEXT NOT NULL,
    facts_json TEXT NOT NULL CHECK (jsonb_typeof(facts_json::jsonb) = 'object'),
    confidence NUMERIC(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (business_id, tenant_id)
      REFERENCES businesses(business_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS capability_pack_acceptances (
    acceptance_id TEXT PRIMARY KEY,
    pack_id TEXT NOT NULL,
    pack_version TEXT NOT NULL,
    pack_hash TEXT NOT NULL CHECK (pack_hash ~ '^[0-9a-f]{64}$'),
    evaluator_version TEXT NOT NULL,
    case_count INTEGER NOT NULL CHECK (case_count > 0),
    passed_count INTEGER NOT NULL CHECK (passed_count BETWEEN 0 AND case_count),
    passed INTEGER NOT NULL CHECK (passed IN (0,1)),
    accepted_at TIMESTAMPTZ NOT NULL,
    UNIQUE (pack_id, pack_version, pack_hash, evaluator_version),
    CHECK ((passed = 1) = (passed_count = case_count))
);

CREATE TABLE IF NOT EXISTS aggregate_performance_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    producer_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    offer_key TEXT NOT NULL,
    source_system TEXT NOT NULL CHECK (source_system LIKE '%-readonly'),
    source_ref TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    impressions BIGINT NOT NULL CHECK (impressions >= 0),
    engagements BIGINT NOT NULL CHECK (engagements BETWEEN 0 AND impressions),
    content_clicks BIGINT NOT NULL CHECK (content_clicks BETWEEN 0 AND engagements),
    outbound_clicks BIGINT NOT NULL CHECK (outbound_clicks BETWEEN 0 AND content_clicks),
    conversions BIGINT NOT NULL CHECK (conversions BETWEEN 0 AND outbound_clicks),
    gross_revenue_minor BIGINT NOT NULL CHECK (gross_revenue_minor >= 0),
    commission_minor BIGINT NOT NULL CHECK (commission_minor BETWEEN 0 AND gross_revenue_minor),
    minimum_outbound_clicks BIGINT NOT NULL CHECK (minimum_outbound_clicks > 0),
    evidence_refs_json TEXT NOT NULL CHECK (jsonb_typeof(evidence_refs_json::jsonb) = 'array'),
    evidence_class TEXT NOT NULL CHECK (evidence_class = 'directional_aggregate'),
    snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
    imported_at TIMESTAMPTZ NOT NULL,
    limitation TEXT NOT NULL CHECK (
      limitation = 'Aggregate evidence does not identify people or prove incrementality.'
    ),
    FOREIGN KEY (business_id, tenant_id)
      REFERENCES businesses(business_id, tenant_id),
    FOREIGN KEY (producer_id, tenant_id)
      REFERENCES actors(actor_id, tenant_id),
    UNIQUE (tenant_id, business_id, source_system, source_ref, window_start, window_end),
    CHECK (window_start < window_end AND window_end <= imported_at)
);

CREATE TABLE IF NOT EXISTS aggregate_performance_verifications (
    verification_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL UNIQUE REFERENCES aggregate_performance_snapshots(snapshot_id),
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    verifier_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('verified','inconclusive','rejected')),
    recomputed_hash TEXT NOT NULL CHECK (recomputed_hash ~ '^[0-9a-f]{64}$'),
    rationale TEXT NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (business_id, tenant_id)
      REFERENCES businesses(business_id, tenant_id),
    FOREIGN KEY (verifier_id, tenant_id)
      REFERENCES actors(actor_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS production_qualifications (
    rowid BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    qualification_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
      'packaging','onboarding','persistence','security','observability',
      'recovery','cost','upgrade'
    )),
    release_version TEXT NOT NULL CHECK (release_version !~* 'latest'),
    artifact_hash TEXT NOT NULL CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
    checks_json TEXT NOT NULL CHECK (jsonb_typeof(checks_json::jsonb) = 'object'),
    checks_hash TEXT NOT NULL CHECK (checks_hash ~ '^[0-9a-f]{64}$'),
    producer_id TEXT NOT NULL,
    verifier_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('passed','held')),
    external_side_effects_enabled INTEGER NOT NULL CHECK (external_side_effects_enabled = 0),
    qualified_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (business_id, tenant_id)
      REFERENCES businesses(business_id, tenant_id),
    FOREIGN KEY (producer_id, tenant_id)
      REFERENCES actors(actor_id, tenant_id),
    FOREIGN KEY (verifier_id, tenant_id)
      REFERENCES actors(actor_id, tenant_id),
    CHECK (producer_id <> verifier_id),
    UNIQUE (tenant_id, business_id, kind, release_version, artifact_hash)
);

CREATE TABLE IF NOT EXISTS legacy_cutover_plans (
    plan_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    source_system TEXT NOT NULL CHECK (source_system IN ('agent-os-v1','openclaw-legacy')),
    capability_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('read_only','proposal','shadow')),
    owner_id TEXT NOT NULL,
    rollback_hash TEXT NOT NULL CHECK (rollback_hash ~ '^[0-9a-f]{64}$'),
    legacy_disable_allowed INTEGER NOT NULL CHECK (legacy_disable_allowed = 0),
    external_side_effects_enabled INTEGER NOT NULL CHECK (external_side_effects_enabled = 0),
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (business_id, tenant_id)
      REFERENCES businesses(business_id, tenant_id),
    FOREIGN KEY (owner_id, tenant_id)
      REFERENCES actors(actor_id, tenant_id),
    UNIQUE (tenant_id, business_id, source_system, capability_id)
);

CREATE TABLE IF NOT EXISTS legacy_cutover_events (
    rowid BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    event_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES legacy_cutover_plans(plan_id),
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN (
      'inventoried','shadow_compared','recovery_verified','approved',
      'canary_observed','rolled_back'
    )),
    actor_id TEXT NOT NULL,
    evidence_hash TEXT NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (business_id, tenant_id)
      REFERENCES businesses(business_id, tenant_id),
    FOREIGN KEY (actor_id, tenant_id)
      REFERENCES actors(actor_id, tenant_id)
);

CREATE OR REPLACE FUNCTION aos_validate_actor_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements_text(NEW.business_ids_json::jsonb) claimed
    LEFT JOIN businesses business
      ON business.business_id = claimed.value AND business.tenant_id = NEW.tenant_id
    WHERE business.business_id IS NULL
  ) THEN
    RAISE EXCEPTION 'actor business membership crosses tenant boundary';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION aos_validate_aggregate_snapshot() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM actors actor
    WHERE actor.actor_id = NEW.producer_id AND actor.tenant_id = NEW.tenant_id
      AND actor.enabled = 1 AND actor.business_ids_json::jsonb ? NEW.business_id
      AND actor.roles_json::jsonb ?| ARRAY['commerce','marketing','research','operations']
  ) THEN
    RAISE EXCEPTION 'aggregate producer is outside business scope';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION aos_validate_aggregate_verification() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE expected_hash TEXT;
DECLARE expected_decision TEXT;
BEGIN
  SELECT snapshot.snapshot_hash,
         CASE WHEN snapshot.outbound_clicks >= snapshot.minimum_outbound_clicks
              THEN 'verified' ELSE 'inconclusive' END
    INTO expected_hash, expected_decision
    FROM aggregate_performance_snapshots snapshot
    JOIN actors verifier ON verifier.actor_id = NEW.verifier_id
   WHERE snapshot.snapshot_id = NEW.snapshot_id
     AND snapshot.tenant_id = NEW.tenant_id
     AND snapshot.business_id = NEW.business_id
     AND verifier.tenant_id = NEW.tenant_id AND verifier.enabled = 1
     AND verifier.actor_id <> snapshot.producer_id
     AND verifier.business_ids_json::jsonb ? NEW.business_id
     AND verifier.roles_json::jsonb ?| ARRAY['qa','verifier','platform-reliability'];
  IF expected_hash IS NULL THEN
    RAISE EXCEPTION 'aggregate verification requires independent scoped QA';
  END IF;
  IF NEW.recomputed_hash <> expected_hash OR NEW.decision <> expected_decision THEN
    RAISE EXCEPTION 'aggregate verification contradicts immutable snapshot';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION aos_validate_production_qualification() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE all_passed BOOLEAN;
BEGIN
  SELECT bool_and(value::boolean) INTO all_passed
  FROM jsonb_each_text(NEW.checks_json::jsonb);
  IF all_passed IS NULL OR ((NEW.decision = 'passed') <> all_passed) THEN
    RAISE EXCEPTION 'production qualification decision contradicts checks';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM actors producer JOIN actors verifier ON TRUE
    WHERE producer.actor_id = NEW.producer_id AND verifier.actor_id = NEW.verifier_id
      AND producer.actor_id <> verifier.actor_id
      AND producer.tenant_id = NEW.tenant_id AND verifier.tenant_id = NEW.tenant_id
      AND producer.enabled = 1 AND verifier.enabled = 1
      AND producer.business_ids_json::jsonb ? NEW.business_id
      AND verifier.business_ids_json::jsonb ? NEW.business_id
      AND producer.roles_json::jsonb ?| ARRAY['platform-reliability','operations']
      AND verifier.roles_json::jsonb ?| ARRAY['qa','verifier']
  ) THEN
    RAISE EXCEPTION 'production qualification requires independent scoped operations and QA';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION aos_validate_cutover_plan() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM actors owner
    WHERE owner.actor_id = NEW.owner_id AND owner.tenant_id = NEW.tenant_id
      AND owner.enabled = 1 AND owner.business_ids_json::jsonb ? NEW.business_id
      AND owner.roles_json::jsonb ?| ARRAY['operations','platform-reliability']
  ) THEN
    RAISE EXCEPTION 'legacy cutover owner is outside scoped operations';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION aos_validate_cutover_event() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE latest_stage TEXT;
DECLARE actor_roles JSONB;
DECLARE actor_kind TEXT;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM legacy_cutover_plans plan JOIN actors actor ON actor.actor_id = NEW.actor_id
    WHERE plan.plan_id = NEW.plan_id AND plan.tenant_id = NEW.tenant_id
      AND plan.business_id = NEW.business_id AND actor.tenant_id = NEW.tenant_id
      AND actor.enabled = 1 AND actor.business_ids_json::jsonb ? NEW.business_id
  ) THEN
    RAISE EXCEPTION 'legacy cutover event crosses scope';
  END IF;
  SELECT stage INTO latest_stage FROM legacy_cutover_events
    WHERE plan_id = NEW.plan_id ORDER BY rowid DESC LIMIT 1;
  IF latest_stage IS NULL AND NEW.stage <> 'inventoried' THEN
    RAISE EXCEPTION 'legacy cutover initial stage is invalid';
  ELSIF latest_stage IS NOT NULL AND NOT (
    (latest_stage = 'inventoried' AND NEW.stage = 'shadow_compared') OR
    (latest_stage = 'shadow_compared' AND NEW.stage = 'recovery_verified') OR
    (latest_stage = 'recovery_verified' AND NEW.stage = 'approved') OR
    (latest_stage = 'approved' AND NEW.stage IN ('canary_observed','rolled_back')) OR
    (latest_stage = 'canary_observed' AND NEW.stage = 'rolled_back')
  ) THEN
    RAISE EXCEPTION 'legacy cutover stage transition is invalid';
  END IF;
  SELECT roles_json::jsonb, actor_type INTO actor_roles, actor_kind
    FROM actors WHERE actor_id = NEW.actor_id;
  IF NEW.stage IN ('shadow_compared','recovery_verified')
     AND NOT actor_roles ?| ARRAY['qa','verifier'] THEN
    RAISE EXCEPTION 'legacy comparison and recovery require QA';
  END IF;
  IF NEW.stage = 'approved'
     AND (actor_kind <> 'human' OR NOT actor_roles ?| ARRAY['business-owner','operations']) THEN
    RAISE EXCEPTION 'legacy cutover approval requires a scoped human owner';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION aos_block_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

DROP TRIGGER IF EXISTS aos_actor_scope ON actors;
CREATE TRIGGER aos_actor_scope BEFORE INSERT OR UPDATE ON actors
  FOR EACH ROW EXECUTE FUNCTION aos_validate_actor_scope();
DROP TRIGGER IF EXISTS aos_aggregate_snapshot ON aggregate_performance_snapshots;
CREATE TRIGGER aos_aggregate_snapshot BEFORE INSERT ON aggregate_performance_snapshots
  FOR EACH ROW EXECUTE FUNCTION aos_validate_aggregate_snapshot();
DROP TRIGGER IF EXISTS aos_aggregate_verification ON aggregate_performance_verifications;
CREATE TRIGGER aos_aggregate_verification BEFORE INSERT ON aggregate_performance_verifications
  FOR EACH ROW EXECUTE FUNCTION aos_validate_aggregate_verification();
DROP TRIGGER IF EXISTS aos_production_qualification ON production_qualifications;
CREATE TRIGGER aos_production_qualification BEFORE INSERT ON production_qualifications
  FOR EACH ROW EXECUTE FUNCTION aos_validate_production_qualification();
DROP TRIGGER IF EXISTS aos_cutover_plan ON legacy_cutover_plans;
CREATE TRIGGER aos_cutover_plan BEFORE INSERT ON legacy_cutover_plans
  FOR EACH ROW EXECUTE FUNCTION aos_validate_cutover_plan();
DROP TRIGGER IF EXISTS aos_cutover_event ON legacy_cutover_events;
CREATE TRIGGER aos_cutover_event BEFORE INSERT ON legacy_cutover_events
  FOR EACH ROW EXECUTE FUNCTION aos_validate_cutover_event();

DO $$
DECLARE table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'evidence_records','capability_pack_acceptances',
    'aggregate_performance_snapshots','aggregate_performance_verifications',
    'production_qualifications','legacy_cutover_plans','legacy_cutover_events'
  ] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS aos_append_only ON %I', table_name);
    EXECUTE format(
      'CREATE TRIGGER aos_append_only BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION aos_block_mutation()',
      table_name
    );
  END LOOP;
END;
$$;

DO $$
DECLARE table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'tenants','businesses','actors','evidence_records',
    'aggregate_performance_snapshots','aggregate_performance_verifications',
    'production_qualifications','legacy_cutover_plans','legacy_cutover_events'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
  END LOOP;
END;
$$;

DROP POLICY IF EXISTS aos_tenant_scope ON tenants;
CREATE POLICY aos_tenant_scope ON tenants USING (
  tenant_id = aos_scope_tenant()
);
DROP POLICY IF EXISTS aos_business_scope ON businesses;
CREATE POLICY aos_business_scope ON businesses USING (
  tenant_id = aos_scope_tenant()
  AND business_id = aos_scope_business()
);
DROP POLICY IF EXISTS aos_actor_scope ON actors;
CREATE POLICY aos_actor_scope ON actors USING (
  tenant_id = aos_scope_tenant()
  AND business_ids_json::jsonb ? aos_scope_business()
);

DO $$
DECLARE table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'evidence_records','aggregate_performance_snapshots',
    'aggregate_performance_verifications','production_qualifications',
    'legacy_cutover_plans','legacy_cutover_events'
  ] LOOP
    EXECUTE format('DROP POLICY IF EXISTS aos_business_scope ON %I', table_name);
    EXECUTE format(
      'CREATE POLICY aos_business_scope ON %I USING (tenant_id = aos_scope_tenant() AND business_id = aos_scope_business()) WITH CHECK (tenant_id = aos_scope_tenant() AND business_id = aos_scope_business())',
      table_name
    );
  END LOOP;
END;
$$;

COMMIT;
