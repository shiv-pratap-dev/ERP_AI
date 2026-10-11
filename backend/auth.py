import bcrypt
from database import get_db

def authenticate_user(conn, email: str, password: str):
    user = conn.execute(
        """
        SELECT id, email, password_hash, role
        FROM users
        WHERE email = %s AND is_active = TRUE
        """,
        (email,)
    ).fetchone()

    if not user:
        return None

    if not bcrypt.checkpw(
        password.encode("utf-8"),
        user[2].encode("utf-8")
    ):
        return None

    return {
        "id": str(user[0]),
        "email": user[1],
        "role": user[3]
    }