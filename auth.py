import os
import time
import jwt
from functools import wraps
from flask import request, jsonify, g
 
# Keep this secret OUT of source control in real deployment — load from
# an environment variable. Never hardcode it, and never ship it to the
# frontend (unlike the Supabase anon key, this one must stay secret).
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError(
        "JWT_SECRET is not set. Add it to your .env file "
        "(generate one with: python -c \"import secrets; print(secrets.token_hex(32))\")"
    )
 
JWT_ALGO = "HS256"
TOKEN_TTL_SECONDS = 60 * 60 * 12  # 12 hour session
 
 
def issue_token(username: str, role: str) -> str:
    """Call this after a successful password check in /api/login."""
    now = int(time.time())
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)
 
 
def decode_token(token: str):
    """Returns the payload dict, or raises jwt exceptions on failure."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
 
 
def require_auth(f):
    """
    Decorator for protected routes. Verifies the Authorization header,
    and makes the verified identity available as g.current_user / g.current_role.
    Any route using this can no longer be impersonated by editing a JSON body.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.method == "OPTIONS":
            # CORS preflight check — must return 200 immediately.
            # It must NOT reach the real route logic below, since a
            # preflight request has no real data/token and would fail
            # whatever the route tries to do with it.
            return jsonify({'status': 'ok'}), 200
 
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"status": "error", "msg": "Missing or malformed Authorization header"}), 401
 
        token = auth_header.split(" ", 1)[1]
        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"status": "error", "msg": "Session expired, please log in again"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"status": "error", "msg": "Invalid session token"}), 401
 
        g.current_user = payload["sub"]
        g.current_role = payload["role"]
        return f(*args, **kwargs)
    return wrapper
 
 
def require_role(*allowed_roles):
    """
    Stack this UNDER @require_auth to restrict a route to specific roles.
    Example: only principals can trigger a school-wide dismissal alert.
        @app.route('/api/principal/school_out', methods=['POST'])
        @require_auth
        @require_role('principal')
        def school_out(): ...
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if request.method == "OPTIONS":
                return f(*args, **kwargs)
            if getattr(g, "current_role", None) not in allowed_roles:
                return jsonify({"status": "error", "msg": "Not authorized for this action"}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator