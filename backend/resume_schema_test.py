import os
import psycopg

database_url = os.environ["DATABASE_URL"]

with psycopg.connect(database_url) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS download_parts (
                id BIGSERIAL PRIMARY KEY,
                download_id TEXT NOT NULL,
                part_number INTEGER NOT NULL,
                b2_object TEXT,
                etag TEXT,
                start_byte BIGINT NOT NULL DEFAULT 0,
                end_byte BIGINT,
                size_bytes BIGINT NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(download_id, part_number)
            )
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_download_parts_download_id
            ON download_parts(download_id)
        """)

    conn.commit()

print({
    "success": True,
    "table": "download_parts",
    "database": "postgresql",
})
