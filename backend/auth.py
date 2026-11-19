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


def change_user_password(conn, user_id: str, current_password: str, new_password: str):
    """Verify current password and update hash. Returns (ok, error_message)."""
    row = conn.execute(
        """
        SELECT password_hash
        FROM users
        WHERE id = %s AND is_active = TRUE
        """,
        (user_id,),
    ).fetchone()

    if not row:
        return False, "User not found"

    if not bcrypt.checkpw(
        current_password.encode("utf-8"),
        row[0].encode("utf-8"),
    ):
        return False, "Current password is incorrect"

    new_hash = bcrypt.hashpw(
        new_password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    conn.execute(
        """
        UPDATE users
        SET password_hash = %s
        WHERE id = %s
        """,
        (new_hash, user_id),
    )
    conn.commit()
    return True, None