import os
import psycopg2

def get_conn():
    # Consolidated onto the shared dashboard DB: when DB_SCHEMA is set we connect to
    # SHARED_DB_URL and scope every session to that schema via search_path. Unset both
    # to instantly roll back to this app's original DATABASE_URL (still attached).
    schema = os.environ.get("DB_SCHEMA")
    if schema:
        return psycopg2.connect(
            os.environ["SHARED_DB_URL"],
            options=f"-c search_path={schema},public",
        )
    return psycopg2.connect(os.environ["DATABASE_URL"])

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scores (
            id SERIAL PRIMARY KEY,
            score NUMERIC(4,2) NOT NULL,
            scoring_method TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("Database initialized.")

def save_score(score: float, scoring_method: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO scores (score, scoring_method) VALUES (%s, %s) RETURNING id",
        (score, scoring_method),
    )
    row_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return row_id

def get_all_scores():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT score FROM scores ORDER BY created_at")
    rows = [float(r[0]) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows

def seed_test_scores():
    """Delete all existing scores and insert 10 test samples."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM scores")
    test_scores = [2.5, 3.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 9.0]
    for s in test_scores:
        cur.execute(
            "INSERT INTO scores (score, scoring_method) VALUES (%s, %s)",
            (s, "test sample"),
        )
    conn.commit()
    cur.close()
    conn.close()
    print(f"Seeded {len(test_scores)} test scores.")
