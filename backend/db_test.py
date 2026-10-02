import os
import psycopg

database_url = os.environ["DATABASE_URL"]

with psycopg.connect(database_url) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT version()")
        version = cur.fetchone()[0]

print({
    "success": True,
    "database": "postgresql",
    "connected": True,
    "version": version,
})
