#!/bin/sh
set -eu

: "${DB_PATH:=/data/kol.db}"
export DB_PATH
seeded=0

if [ -e "$DB_PATH" ] && { [ ! -f "$DB_PATH" ] || [ ! -s "$DB_PATH" ]; }; then
  echo "[entrypoint] Refusing to overwrite invalid DB path: $DB_PATH" >&2
  exit 1
fi

if [ ! -f "$DB_PATH" ]; then
  echo "[entrypoint] No DB at $DB_PATH yet -- seeding from baked-in snapshot."
  mkdir -p "$(dirname "$DB_PATH")"
  seed_candidate="${DB_PATH}.seed.$$"
  cp data/kol.seed.db "$seed_candidate"
  python3 scripts/check_runtime_db.py --db "$seed_candidate" --seed-contract
  mv "$seed_candidate" "$DB_PATH"
  seeded=1
fi

# Existing volumes are never silently replaced or partially upgraded.  A
# volume from the old Creator-only deployment must go through the documented
# one-time migration runbook and a verified backup before this release boots.
# Preflight deliberately allows columns that the idempotent migrations below
# add. It still rejects corruption, missing base tables/rows, and FK damage.
if ! python3 scripts/check_runtime_db.py --db "$DB_PATH" --preflight-contract; then
  echo "[entrypoint] Existing volume is not release-compatible." >&2
  echo "[entrypoint] Stop and follow docs/railway-internal-release.md; do not delete or reseed the volume." >&2
  exit 1
fi

if [ "$seeded" -eq 0 ]; then
  python3 scripts/backup_db.py pre_boot_migration \
    --db "$DB_PATH" \
    --backup-dir "${BACKUP_DIR:-$(dirname "$DB_PATH")/backups}"
fi

# Idempotent by design (checks column/table existence before altering) --
# safe to run on every boot, so a code deploy that adds a column/table to
# models.py just works against the persistent volume without a separate
# manual migration step.
# All schema migrations (ALTER TABLE / CREATE TABLE) must run before any
# data-apply script queries the affected models -- SQLAlchemy selects every
# mapped column, so a query against IntroBridge fails outright if a column
# added to the model hasn't been ALTERed onto this DB yet, even if that
# script has nothing to do with the new column. Learned the hard way when
# apply_intro_bridges_snapshot.py (unrelated to interaction evidence) broke
# boot because it ran before add_outreach_log_and_interaction_columns.py.
# IntroPath is queried by several later data-apply scripts through the full
# SQLAlchemy model, so its new mapped columns must exist first.
python3 scripts/add_intro_path_direction_columns.py
# Narrow, NULL-only backfill.  Uses the baked seed as the production source
# so an existing persistent volume is repaired without refreshing any other
# source- or human-owned fields.
python3 scripts/backfill_intro_path_directions.py
python3 scripts/add_created_at_columns.py
python3 scripts/add_intro_bridge_schema.py
python3 scripts/add_outreach_log_and_interaction_columns.py
python3 scripts/add_interaction_audit_and_outreach_v2_columns.py
python3 scripts/add_operator_verification_columns.py
python3 scripts/add_shortlist_quote_override_column.py
python3 scripts/add_shortlist_pricing_assumptions_column.py
# Exact-name + exact-business-email entity cleanup. Old ids remain resolvable
# through creator_entity_aliases; fuzzy matches are never merged.
python3 scripts/merge_creator_entities_v8.py --db "$DB_PATH"
python3 scripts/apply_creator_classification_v8.py --db "$DB_PATH"

python3 scripts/fix_action_item_phrasing.py
python3 scripts/fix_stale_connector_brief.py
# Recurring data applies below are monotonic by contract: bootstrap missing
# rows, fill NULLs, repair an exact known-stale signature, or advance only
# when the baked evidence timestamp is strictly newer. They never delete or
# wholesale refresh production/human evidence on redeploy.
python3 scripts/apply_gap_operators.py
# Existing production operators may predate the separately-audited handle
# snapshot. Fill only blank handles before downstream snapshots resolve rows
# by (company_id, x_handle); conflicts remain untouched and are logged.
python3 scripts/apply_operator_x_handles_snapshot.py
python3 scripts/apply_intro_bridges_snapshot.py
python3 scripts/apply_bridge_interaction_snapshot.py
python3 scripts/apply_interaction_evidence_snapshot.py
python3 scripts/fix_fundraise_only_spend_evidence.py
python3 scripts/backfill_sponsorship_publish_dates.py
python3 scripts/apply_release_schedule.py
python3 scripts/audit_and_clean_test_data.py
# Company-first commercial layer. This idempotent apply creates the v5
# longlist/dossier/evidence/route tables when absent, adds the eight sourced
# discovery rows, and upserts only stable v5 keys. Relationship graph state is
# preserved and is never used as an input to commercial scoring.
python3 scripts/apply_commercial_research_v5.py
# Product-facing canonical decisions and the manually reviewed Priority-15
# Asia layer. Stable keys make this safe for both fresh seeds and existing
# volumes; no legacy or human-owned records are deleted.
python3 scripts/apply_decision_and_asia_v6.py
# Send-ready Top-5 sales packets and independently sourced buyer maps. Prices
# are still computed from the live creator rate library at request time; this
# apply stores commercial strategy and contact truth states only.
python3 scripts/apply_top5_sales_packets_v7.py
# Candidate imports remain deliberately conservative: operators stay
# identity-unconfirmed, sponsorship observations stay unreviewed, and rows
# without brand attribution are held back.  The compact input is baked into
# the image and contains no raw Actor payload, run id, cache path, or token.
python3 scripts/apply_apify_review_candidates.py \
  --db "$DB_PATH" \
  --operators data/apify_review_candidates_v1.json \
  --sponsorships data/apify_review_candidates_v1.json \
  --creators data/apify_review_candidates_v1.json \
  --apply
# Recurring research sprint output (new companies, operator candidates,
# sponsorship evidence).  Same conservative contract as the Apify import:
# operators land identity-unconfirmed, sponsorship observations land
# unreviewed, and the apply matches on (company_id, content_url) /
# (company_id, name) so re-running on an existing volume never duplicates.
# Reads DB_PATH from the environment exported above.
python3 scripts/apply_research_batch.py
# Apply the small set of first-party route and Asia-intent corrections after
# the v6/v7 records exist.  Stable keys make this idempotent on every boot.
python3 scripts/apply_bd_refocus_20260901.py --db "$DB_PATH" --apply

# Migrations and narrow data repairs must preserve the complete release
# contract and referential integrity.  Fail before the server accepts traffic
# if any startup step violated it.
python3 scripts/check_runtime_db.py --db "$DB_PATH" --runtime-contract

exec uvicorn backend.app:app --host 0.0.0.0 --port "${PORT:-8000}"
