"""轻量迁移：为既有 SQLite 数据库补齐新增列（create_all 不会改已有表）。"""
from sqlalchemy import inspect, text

from backend.db.database import engine

# (表, 列, 列 DDL)
_COLUMNS = [
    ("organizations", "uscc", "VARCHAR(30) DEFAULT ''"),
    ("organizations", "registered_region", "VARCHAR(50) DEFAULT ''"),
    ("jobs", "filter_zone", "VARCHAR(20) DEFAULT 'accepted'"),
    ("jobs", "confidence", "FLOAT"),
    ("jobs", "rejection_reason", "VARCHAR(50) DEFAULT ''"),
    ("jobs", "filter_trace", "JSON"),
    ("jobs", "discovered_via", "VARCHAR(30) DEFAULT ''"),
    ("search_tasks", "funnel", "JSON"),
    ("user_preferences", "external_searxng_url", "VARCHAR(300) DEFAULT ''"),
    ("user_preferences", "filter_level", "VARCHAR(10) DEFAULT 'standard'"),
]


def ensure_columns() -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, column, ddl in _COLUMNS:
            if table not in inspector.get_table_names():
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
