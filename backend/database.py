import psycopg

def get_db():
    conn = psycopg.connect("dbname=erp_ai")
    try:
        yield conn
    finally:
        conn.close()