import os
import psycopg2

def get_conn():
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
