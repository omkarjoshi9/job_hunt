"""SQLite/Postgres persistence layer.

The former single-module ``jobdigest.db`` is split by concern; every public
(and test-visible private) name is re-exported here so
``from jobdigest.db import X`` keeps working.  ``utc_now`` is looked up on this
package at call time by the submodules, so ``monkeypatch.setattr(
"jobdigest.db.utc_now", ...)`` still affects them.
"""

from __future__ import annotations

from jobdigest.db._common import Conn, RowLike, utc_now
from jobdigest.db.applications import (
    apply_bot_status,
    ensure_application,
    job_score_row,
    mark_sheet_synced,
    pending_sheet_rows,
    upsert_application,
)
from jobdigest.db.connection import (
    MappingRow,
    _PgConnection,
    _PgCursor,
    _adapt_row,
    _pg_sql,
    connect,
    connect_postgres,
    default_db_path,
    postgres_url,
    safe_ident,
    table_columns,
    transaction,
    uses_postgres,
)
from jobdigest.db.contacts import best_contact_for_job, contacts_for_job, upsert_contact
from jobdigest.db.digest_queries import (
    MIN_OUTCOMES,
    OUTCOME,
    guest_rows,
    load_company_priors,
    relevant_unapplied_jobs,
    urgent_sent_count,
)
from jobdigest.db.jobs import (
    bump_unseen_for_source,
    job_hard_status,
    job_seen_key,
    link_duplicate_jobs,
    lookup_job,
    mark_scores_stale,
    note_seen_job,
    stamp_notified,
    update_company_fetch,
    upsert_company,
    upsert_job,
)
from jobdigest.db.runs import (
    collect_is_ok,
    finish_run,
    insert_portal_error,
    insert_run,
    last_collect_run,
    last_digest_job_ids,
    last_ok_run,
    unscored_pass_jobs,
    upsert_score,
)
from jobdigest.db.schema import SCHEMA, SCHEMA_PG, init_schema, migrate_schema
from jobdigest.paths import repo_root

# Backwards-compatible alias for the former private helper name.
_table_columns = table_columns

__all__ = [
    "Conn",
    "MIN_OUTCOMES",
    "MappingRow",
    "OUTCOME",
    "RowLike",
    "SCHEMA",
    "SCHEMA_PG",
    "_pg_sql",
    "apply_bot_status",
    "best_contact_for_job",
    "bump_unseen_for_source",
    "collect_is_ok",
    "connect",
    "connect_postgres",
    "contacts_for_job",
    "default_db_path",
    "ensure_application",
    "finish_run",
    "guest_rows",
    "init_schema",
    "insert_portal_error",
    "insert_run",
    "job_hard_status",
    "job_score_row",
    "job_seen_key",
    "last_collect_run",
    "last_digest_job_ids",
    "last_ok_run",
    "link_duplicate_jobs",
    "load_company_priors",
    "lookup_job",
    "mark_scores_stale",
    "mark_sheet_synced",
    "migrate_schema",
    "note_seen_job",
    "pending_sheet_rows",
    "postgres_url",
    "relevant_unapplied_jobs",
    "repo_root",
    "safe_ident",
    "stamp_notified",
    "table_columns",
    "transaction",
    "unscored_pass_jobs",
    "update_company_fetch",
    "upsert_application",
    "upsert_company",
    "upsert_contact",
    "upsert_job",
    "upsert_score",
    "urgent_sent_count",
    "uses_postgres",
    "utc_now",
]
