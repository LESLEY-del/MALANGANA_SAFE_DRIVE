import os, re, math, time, base64, secrets, threading, html as htmllib
import bcrypt, resend
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify, g, render_template, abort
from flask_cors import CORS
from supabase import create_client, Client, ClientOptions
from httpx import Timeout
from dotenv import load_dotenv
from auth import issue_token, require_auth, require_role

load_dotenv()
try:
    from waitress import serve
except ImportError:
    print("Install waitress: pip install waitress"); raise SystemExit(1)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
SA_TIMEZONE = ZoneInfo("Africa/Johannesburg")

ALLOWED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"] + \
    [o for o in os.getenv("EXTRA_ORIGINS", "").split(",") if o]
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
resend.api_key = os.environ.get("RESEND_API_KEY")
MAIL_FROM = os.getenv("MAIL_FROM", "Safe Drive <noreply@yourdomain.com>")
BUCKET = "profile pictures"
thread_local = threading.local()

def get_db() -> Client:
    if not hasattr(thread_local, "sb"):
        opts = ClientOptions(postgrest_client_timeout=Timeout(30.0, connect=30.0, read=60.0, write=30.0), schema="public")
        thread_local.sb = create_client(SUPABASE_URL, SUPABASE_KEY, options=opts)
    return thread_local.sb

# ---------------- helpers ----------------
def current_sa_time():
    return datetime.now(SA_TIMEZONE).replace(microsecond=0).isoformat(timespec="seconds")

def _expiry(minutes=15):
    return (datetime.now(SA_TIMEZONE) + timedelta(minutes=minutes)).isoformat()

def _is_expired(s):
    if not s: return False
    try: return datetime.fromisoformat(s) < datetime.now(SA_TIMEZONE)
    except Exception: return False

def _h(s): return htmllib.escape(str(s or ''))

def _clean(q):  # safe for PostgREST .or_() filters
    return re.sub(r'[^\w\s\-\.]', '', q or '').strip()[:60]

def _name_key(s): return re.sub(r'\s+', ' ', (s or '').strip().lower())

_attempts = {}
def _too_many(key, limit=5, window=900):
    now = time.time()
    lst = [t for t in _attempts.get(key, []) if now - t < window]
    if len(lst) >= limit:
        _attempts[key] = lst; return True
    lst.append(now); _attempts[key] = lst; return False

def _haversine(a, b, c, d):
    R = 6371000; p1, p2 = math.radians(a), math.radians(c)
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))

def _upload_bytes(data, name, ctype="image/jpeg"):
    get_db().storage.from_(BUCKET).upload(path=name, file=data, file_options={"content-type": ctype})
    return get_db().storage.from_(BUCKET).get_public_url(name)

def _upload_data_url(data_url, prefix):
    if not data_url: return None
    raw = data_url.split(',', 1)[1] if ',' in data_url else data_url
    return _upload_bytes(base64.b64decode(raw), f"{prefix}_{int(time.time())}_{secrets.token_hex(4)}.jpg")

def _remove_file(url):
    if url and 'supabase.co' in url:
        try: get_db().storage.from_(BUCKET).remove([url.split('/')[-1]])
        except Exception as e: print("cleanup:", e)

# ---------------- email ----------------
def generate_email_wrapper(content, subtitle="Security Notification"):
    return f"""
    <div style="background:#010409;padding:40px 16px;font-family:Segoe UI,Roboto,Arial,sans-serif">
      <div style="max-width:620px;margin:auto;background:#0d1117;border-radius:18px;overflow:hidden;border:1px solid #30363d">
        <div style="background:#020218;padding:34px;text-align:center;border-bottom:3px solid #10b981">
          <h1 style="margin:0;color:#fff;font-size:28px;letter-spacing:5px">SAFE <span style="color:#10b981">DRIVE</span></h1>
          <p style="margin:8px 0 0;color:#10b981;font-size:11px;letter-spacing:3px">{_h(subtitle)}</p></div>
        <div style="padding:36px;color:#c9d1d9;line-height:1.7;font-size:16px">{content}
          <p style="margin-top:36px;padding-top:18px;border-top:1px solid #30363d;font-size:12px;color:#8b949e">
          Automated message from Safe Drive. Questions: safedrive@gmail.com</p></div>
        <div style="background:#161b22;padding:18px;text-align:center;color:#8b949e;font-size:12px">© 2026 SAFE DRIVE</div>
      </div></div>"""

def send_mail(to_email, subject, body_content, subtitle="Security Notification"):
    if not to_email: return False
    try:
        resend.Emails.send({"from": MAIL_FROM, "to": [to_email], "subject": subject,
                            "html": generate_email_wrapper(body_content, subtitle)})
        return True
    except Exception as e:
        print("[EMAIL FAIL]", e); return False

def notify_parent(parent_u, subject, text_html, photo_url=None):
    try:
        r = get_db().table("users").select("email").eq("username", parent_u).execute()
        if not r.data: return
        img = f'<p><img src="{_h(photo_url)}" style="max-width:100%;border-radius:12px"></p>' if photo_url else ''
        threading.Thread(target=send_mail, args=(r.data[0]['email'], subject, f"<p style='font-size:18px'>{text_html}</p>{img}", "Child Alert")).start()
    except Exception as e: print("notify_parent:", e)

def send_whatsapp(group_id, text):
    # TODO: connect WhatsApp Cloud API / Twilio. Needs a business number + approved template.
    print("[WHATSAPP STUB]", group_id, text)

# ---------------- pages ----------------
PAGES = {'index', 'login', 'signup', 'terms', 'privacy', 'reset_password',
         'parent_dashboard', 'station_dashboard', 'admin_dashboard'}

@app.route('/')
def home(): return render_template('index.html')

@app.route('/<page>.html')
@app.route('/<page>')
def pages(page):
    if page not in PAGES: abort(404)
    return render_template(f'{page}.html')

# ---------------- availability ----------------
@app.route('/api/check_availability', methods=['POST', 'OPTIONS'])
def check_availability():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
    try:
        u = (request.json or {}).get('username', '').strip().lower()
        taken = False
        if u:
            taken = bool(get_db().table("users").select("username").eq("username", u).execute().data) or \
                    bool(get_db().table("admins").select("username").eq("username", u).execute().data)
        return jsonify({'status': 'success', 'username_taken': taken})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ---------------- login ----------------
@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
    try:
        d = request.json or {}
        u = d.get('username', '').lower().strip(); p = d.get('password', '')
        if _too_many('l:' + u, limit=10):
            return jsonify({'status': 'error', 'msg': 'Too many attempts. Try again in 15 minutes.'}), 429
        user = None; table = "users"
        r = get_db().table("users").select("*").eq("username", u).execute()
        if r.data: user = r.data[0]
        else:
            a = get_db().table("admins").select("*").eq("username", u).execute()
            if a.data: user, table = a.data[0], "admins"
        if not user:
            return jsonify({'status': 'error', 'msg': 'Wrong username or password.'}), 401
        stored = user.get('password', '') or ''
        ok = False
        try:
            ok = bcrypt.checkpw(p.encode(), stored.encode())
        except ValueError:   # legacy plain-text password: accept once, then hash it
            if stored == p:
                ok = True
                get_db().table(table).update({"password": bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()}).eq("username", u).execute()
        if not ok:
            return jsonify({'status': 'error', 'msg': 'Wrong username or password.'}), 401
        role = user.get('role') or ('admin' if table == "admins" else None)
        return jsonify({'status': 'success', 'token': issue_token(username=u, role=role), 'role': role,
                        'name': user.get('name') or u, 'username': u,
                        'school_code': (user.get('school_code') or '').upper() or None})
    except Exception as e:
        return jsonify({'status': 'error', 'msg': f'Login failed: {e}'}), 500

# ---------------- signup (parent / school station) ----------------
@app.route('/api/<role>/signup', methods=['POST', 'OPTIONS'])
def signup(role):
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
    if role not in ('general', 'principal'): abort(404)   # never allow admin signup
    try:
        d = request.json or {}
        u = d.get('username', '').lower().strip()
        email = d.get('email', '').strip()
        pw = d.get('password', '')
        if not d.get('terms_accepted'):
            return jsonify({'status': 'error', 'message': 'You must accept the Terms and Privacy Policy.'}), 400
        if not re.fullmatch(r'[a-z0-9_.\-]{3,30}', u):
            return jsonify({'status': 'error', 'message': 'Username: 3-30 letters, numbers, dot, dash or underscore.'}), 400
        if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
            return jsonify({'status': 'error', 'message': 'Enter a valid email address.'}), 400
        if len(pw) < 8:
            return jsonify({'status': 'error', 'message': 'Password needs at least 8 characters.'}), 400
        if get_db().table("users").select("username").eq("username", u).execute().data or \
           get_db().table("admins").select("username").eq("username", u).execute().data:
            return jsonify({'status': 'error', 'message': 'Username already registered.'}), 400

        payload = {"name": d.get('name', '').strip(), "email": email, "role": role,
                   "password": bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode(),
                   "terms_accepted_at": current_sa_time(), "terms_version": "2026-11"}
        if role == 'principal':
            sc = re.sub(r'\s+', '', str(d.get('school_code', ''))).upper()
            sn = d.get('school_name', '').strip()
            if not re.fullmatch(r'[A-Z0-9\-]{3,20}', sc) or not sn:
                return jsonify({'status': 'error', 'message': 'Add the school name and a code (3-20 letters or numbers).'}), 400
            if get_db().table("schools").select("school_code").eq("school_code", sc).execute().data:
                return jsonify({'status': 'error', 'message': 'That school code is already taken.'}), 400
            payload.update({"school_code": sc, "school_name": sn})

        otp = str(secrets.randbelow(9000) + 1000)
        get_db().table("pending_signups").upsert({"username": u, "payload": payload, "v_code": otp,
            "created_at": current_sa_time(), "expires_at": _expiry()}).execute()
        body = f"""<p>Your verification code:</p><div style="background:#161b22;padding:22px;border-radius:10px;border-left:4px solid #10b981;text-align:center">
                   <h2 style="margin:0;font-size:32px;color:#10b981;letter-spacing:8px">{otp}</h2></div><p>It expires in 15 minutes.</p>"""
        threading.Thread(target=send_mail, args=(email, "Safe Drive verification code", body, "Account Verification")).start()
        return jsonify({'status': 'pending', 'username': u})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/verify-and-save', methods=['POST', 'OPTIONS'])
def verify_and_save():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
    try:
        d = request.get_json(silent=True) or {}
        u = str(d.get('username', '')).lower().strip(); code = str(d.get('code', '')).strip()
        if not u or not code: return jsonify({'status': 'error', 'message': 'Missing data'}), 400
        if _too_many('v:' + u):
            return jsonify({'status': 'error', 'message': 'Too many tries. Sign up again later.'}), 429
        r = get_db().table("pending_signups").select("*").eq("username", u).execute()
        if not r.data: return jsonify({'status': 'error', 'message': 'Session expired'}), 400
        row = r.data[0]
        if _is_expired(row.get('expires_at')):
            get_db().table("pending_signups").delete().eq("username", u).execute()
            return jsonify({'status': 'error', 'message': 'Session expired'}), 400
        if not secrets.compare_digest(str(row.get('v_code', '')), code):
            return jsonify({'status': 'error', 'message': 'Invalid code'}), 400
        p = row.get('payload') or {}
        get_db().table("users").insert({"username": u, "password": p.get('password'), "name": p.get('name'),
            "email": p.get('email'), "role": p.get('role'), "school_code": p.get('school_code'),
            "terms_accepted_at": p.get('terms_accepted_at'), "terms_version": p.get('terms_version')}).execute()
        if p.get('role') == 'principal':
            try:
                get_db().table("schools").insert({"school_code": p['school_code'], "school_name": p['school_name'],
                                                  "principal_username": u}).execute()
            except Exception as e:
                get_db().table("users").delete().eq("username", u).execute()
                return jsonify({'status': 'error', 'message': 'That school code was just taken. Sign up again.'}), 400
        get_db().table("pending_signups").delete().eq("username", u).execute()
        return jsonify({'status': 'success'})
    except Exception as e:
        print("verify error:", e)
        return jsonify({'status': 'error', 'message': 'Server error'}), 500

# ---------------- password reset ----------------
@app.route('/api/forgot-password', methods=['POST', 'OPTIONS'])
def forgot_password():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
    try:
        u = (request.json or {}).get('username', '').lower().strip()
        r = get_db().table("users").select("email").eq("username", u).execute()
        if not r.data: return jsonify({'status': 'error', 'message': 'Username not found.'}), 404
        otp = str(secrets.randbelow(900000) + 100000)
        get_db().table("password_reset_codes").upsert({"username": u, "code": otp,
            "created_at": current_sa_time(), "expires_at": _expiry()}).execute()
        body = f"""<p>Password reset for <b>{_h(u)}</b>.</p><div style="background:#161b22;padding:22px;border-radius:10px;border-left:4px solid #f59e0b;text-align:center">
                   <h2 style="margin:0;font-size:32px;color:#f59e0b;letter-spacing:8px">{otp}</h2></div>"""
        threading.Thread(target=send_mail, args=(r.data[0]['email'], "Password reset code", body, "Account Recovery")).start()
        return jsonify({'status': 'success', 'message': 'Code sent to registered email.'})
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500

@app.route('/api/reset-password', methods=['POST', 'OPTIONS'])
def reset_password():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
    try:
        d = request.json or {}
        u = d.get('username', '').lower().strip(); code = str(d.get('code', '')).strip(); new = d.get('newPassword', '')
        if _too_many('r:' + u): return jsonify({'status': 'error', 'message': 'Too many tries. Try later.'}), 429
        if len(new) < 8: return jsonify({'status': 'error', 'message': 'Password needs at least 8 characters.'}), 400
        r = get_db().table("password_reset_codes").select("*").eq("username", u).execute()
        row = r.data[0] if r.data else None
        if row and secrets.compare_digest(str(row.get('code', '')), code) and not _is_expired(row.get('expires_at')):
            get_db().table("users").update({"password": bcrypt.hashpw(new.encode(), bcrypt.gensalt()).decode()}).eq("username", u).execute()
            get_db().table("password_reset_codes").delete().eq("username", u).execute()
            return jsonify({'status': 'success'})
        return jsonify({'status': 'error', 'message': 'Invalid or expired code.'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500

# ---------------- profile ----------------
@app.route('/api/profile/get_self', methods=['POST', 'OPTIONS'])
@require_auth
def get_self_profile():
    r = get_db().table("users").select("username, name, email, role, school_code, photo, theme, created_at").eq("username", g.current_user).execute()
    if not r.data: return jsonify({'status': 'error', 'message': 'Not found'}), 404
    return jsonify({'status': 'success', 'profile': r.data[0]})

@app.route('/api/profile/update_self', methods=['POST', 'OPTIONS'])
@require_auth
def update_self_profile():
    upd = {k: v for k, v in (request.json or {}).items() if k in {'theme'}}
    if not upd: return jsonify({'status': 'error', 'message': 'No valid fields'}), 400
    get_db().table("users").update(upd).eq("username", g.current_user).execute()
    return jsonify({'status': 'success'})

@app.route('/api/profile/upload_photo', methods=['POST', 'OPTIONS'])
@require_auth
def upload_profile_photo():
    try:
        f = request.files.get('photo')
        if not f: return jsonify({'status': 'error', 'message': 'No file'}), 400
        old = get_db().table("users").select("photo").eq("username", g.current_user).execute()
        if old.data: _remove_file(old.data[0].get('photo'))
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'jpg'
        if ext not in ('jpg', 'jpeg', 'png', 'webp'): return jsonify({'status': 'error', 'message': 'Use a JPG, PNG or WEBP image.'}), 400
        url = _upload_bytes(f.read(), f"{g.current_user}_{int(time.time())}.{ext}", f.content_type or "image/jpeg")
        get_db().table("users").update({"photo": url}).eq("username", g.current_user).execute()
        return jsonify({'status': 'success', 'photo_url': url})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/profile/delete_photo', methods=['POST', 'OPTIONS'])
@require_auth
def delete_profile_photo():
    old = get_db().table("users").select("photo").eq("username", g.current_user).execute()
    if old.data: _remove_file(old.data[0].get('photo'))
    get_db().table("users").update({"photo": None}).eq("username", g.current_user).execute()
    return jsonify({'status': 'success'})

@app.route('/api/delete_account', methods=['POST', 'OPTIONS'])
@require_auth
def delete_account():
    try:
        u = g.current_user
        r = get_db().table("users").select("role").eq("username", u).execute()
        if not r.data: return jsonify({'status': 'error', 'message': 'Profile not found.'}), 404
        if r.data[0]['role'] == 'principal':
            get_db().table("schools").delete().eq("principal_username", u).execute()
        get_db().table("users").delete().eq("username", u).execute()   # children, memberships cascade
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ---------------- schools ----------------
@app.route('/api/schools/search', methods=['POST', 'OPTIONS'])
@require_auth
def schools_search():
    q = _clean((request.json or {}).get('q'))
    if len(q) < 2: return jsonify({'status': 'success', 'schools': []})
    r = get_db().table("schools").select("school_code, school_name").or_(f"school_name.ilike.%{q}%,school_code.ilike.{q}%").limit(10).execute()
    return jsonify({'status': 'success', 'schools': r.data or []})

# ---------------- children (parent) ----------------
CHILD_EDIT = {'name', 'surname', 'grade', 'tracker_device_id', 'notify_school', 'notify_home', 'notify_zone',
              'call_confirm', 'home_lat', 'home_lng', 'school_lat', 'school_lng', 'zone_radius'}

@app.route('/api/children/add', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('general')
def children_add():
    d = request.json or {}
    name, surname = d.get('name', '').strip(), d.get('surname', '').strip()
    idn = re.sub(r'\D', '', str(d.get('id_number', '')))
    if not name or not surname or len(idn) < 6:
        return jsonify({'status': 'error', 'message': 'Name, surname and ID number are required.'}), 400
    sc = str(d.get('school_code', '')).strip().upper()
    s = get_db().table("schools").select("school_code, school_name").eq("school_code", sc).execute()
    if not s.data:
        return jsonify({'status': 'error', 'message': 'School not found. Pick it from the search list.'}), 404
    res = get_db().table("children").insert({"parent_username": g.current_user, "school_code": sc,
        "school_name": s.data[0]['school_name'], "name": name, "surname": surname, "id_last4": idn[-4:],
        "grade": d.get('grade', '').strip(), "qr_token": secrets.token_urlsafe(24)}).execute()
    return jsonify({'status': 'success', 'child': res.data[0]})

@app.route('/api/children/list', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('general')
def children_list():
    r = get_db().table("children").select("*").eq("parent_username", g.current_user).order("created_at").execute()
    return jsonify({'status': 'success', 'children': r.data or []})

@app.route('/api/children/update', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('general')
def children_update():
    d = request.json or {}
    upd = {k: v for k, v in d.items() if k in CHILD_EDIT}
    if 'tracker_device_id' in upd: upd['tracker_device_id'] = (upd['tracker_device_id'] or '').strip() or None
    if 'zone_radius' in upd: upd['zone_radius'] = max(50, min(int(upd['zone_radius'] or 150), 2000))
    if not upd: return jsonify({'status': 'error', 'message': 'Nothing to update'}), 400
    try:
        r = get_db().table("children").update(upd).eq("id", d.get('id')).eq("parent_username", g.current_user).execute()
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'That tracker ID may already be in use.'}), 400
    return (jsonify({'status': 'success'}), 200) if r.data else (jsonify({'status': 'error'}), 404)

@app.route('/api/children/photo', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('general')
def children_photo():
    f = request.files.get('photo'); cid = request.form.get('id')
    if not f or not cid: return jsonify({'status': 'error', 'message': 'Missing file'}), 400
    own = get_db().table("children").select("photo").eq("id", cid).eq("parent_username", g.current_user).execute()
    if not own.data: return jsonify({'status': 'error'}), 404
    _remove_file(own.data[0].get('photo'))
    url = _upload_bytes(f.read(), f"child_{cid}_{int(time.time())}.jpg", f.content_type or "image/jpeg")
    get_db().table("children").update({"photo": url}).eq("id", cid).execute()
    return jsonify({'status': 'success', 'photo_url': url})

@app.route('/api/children/delete', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('general')
def children_delete():
    cid = (request.json or {}).get('id')
    own = get_db().table("children").select("photo").eq("id", cid).eq("parent_username", g.current_user).execute()
    if own.data: _remove_file(own.data[0].get('photo'))
    get_db().table("children").delete().eq("id", cid).eq("parent_username", g.current_user).execute()
    return jsonify({'status': 'success'})

@app.route('/api/parent/arrivals', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('general')
def parent_arrivals():
    r = get_db().table("arrivals").select("*").eq("parent_username", g.current_user).order("timestamp", desc=True).limit(100).execute()
    return jsonify({'status': 'success', 'logs': r.data or []})

@app.route('/api/parent/get_child_location', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('general')
def get_child_location():
    cid = (request.json or {}).get('child_id')
    own = get_db().table("children").select("id").eq("id", cid).eq("parent_username", g.current_user).execute()
    if not own.data: return jsonify({'status': 'error', 'message': 'Not authorized'}), 403
    r = get_db().table("child_locations").select("latitude, longitude, updated_at").eq("child_id", cid).execute()
    return jsonify({'status': 'success', 'location': r.data[0] if r.data else None})

# ---------------- school station ----------------
def _my_codes():
    r = get_db().table("schools").select("school_code").eq("principal_username", g.current_user).execute()
    return [s['school_code'] for s in (r.data or [])]

@app.route('/api/station/roster', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('principal')
def station_roster():
    codes = _my_codes()
    if not codes: return jsonify({'status': 'success', 'children': []})
    r = get_db().table("children").select("id, name, surname, grade, photo, school_code").in_("school_code", codes).order("name").execute()
    return jsonify({'status': 'success', 'children': r.data or []})

@app.route('/api/station/logs', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('principal')
def station_logs():
    codes = _my_codes()
    if not codes: return jsonify({'status': 'success', 'logs': []})
    r = get_db().table("arrivals").select("id, child_name, status, photo_url, timestamp").in_("school_code", codes).order("timestamp", desc=True).limit(200).execute()
    return jsonify({'status': 'success', 'logs': r.data or []})

@app.route('/api/principal/scan_attendance', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('principal')
def station_scan():
    d = request.json or {}
    status = 'DISMISSED' if str(d.get('status', '')).upper().startswith(('DISMISS', 'SCHOOL OUT')) else 'ARRIVED'
    c = get_db().table("children").select("*").eq("qr_token", str(d.get('qr_token', ''))).execute()
    if not c.data: return jsonify({'status': 'error', 'message': 'Unknown card.'}), 404
    ch = c.data[0]
    if ch['school_code'] not in _my_codes():
        return jsonify({'status': 'error', 'message': 'This child is not registered at your school.'}), 403
    photo = None
    try: photo = _upload_data_url(d.get('photo'), "scan")
    except Exception as e: print("scan photo:", e)
    full = f"{ch['name']} {ch['surname']}"
    get_db().table("arrivals").insert({"child_id": ch['id'], "parent_username": ch['parent_username'], "school_code": ch['school_code'],
        "child_name": full, "status": status, "photo_url": photo, "station_username": g.current_user}).execute()
    word = "has arrived at school" if status == 'ARRIVED' else "has been dismissed from school"
    if ch.get('notify_school', True):
        notify_parent(ch['parent_username'], f"{ch['name']} {word}", f"<b>{_h(full)}</b> {word}.", photo)
    return jsonify({'status': 'success', 'student_name': full, 'attendance_status': status})

@app.route('/api/principal/school_out', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('principal')
def school_out_all():
    codes = _my_codes()
    if not codes: return jsonify({'status': 'success', 'notified': 0})
    kids = get_db().table("children").select("*").in_("school_code", codes).execute().data or []
    rows = [{"child_id": c['id'], "parent_username": c['parent_username'], "school_code": c['school_code'],
             "child_name": f"{c['name']} {c['surname']}", "status": "DISMISSED", "station_username": g.current_user} for c in kids]
    if rows: get_db().table("arrivals").insert(rows).execute()
    for c in kids:
        if c.get('notify_school', True):
            notify_parent(c['parent_username'], "School is out", f"School is out. <b>{_h(c['name'])}</b> has been dismissed.")
    return jsonify({'status': 'success', 'notified': len(kids)})

# ---------------- tracker ingest + zones ----------------
@app.route('/api/gps/ingest', methods=['POST', 'OPTIONS'])
def gps_ingest():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
    try:
        secret = os.getenv("TRACCAR_INGEST_SECRET")
        if not secret or request.args.get('secret') != secret:
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
        d = request.json or {}; dev = d.get('device') or {}
        lid = str(dev.get('uniqueId') or d.get('uniqueId') or dev.get('id') or d.get('deviceId') or '').strip()
        lat = d.get('latitude', d.get('lat')); lng = d.get('longitude', d.get('lon', d.get('lng')))
        if not lid or lat is None or lng is None: return jsonify({'status': 'error', 'message': 'Missing data'}), 400
        lat, lng = float(lat), float(lng)
        c = get_db().table("children").select("*").eq("tracker_device_id", lid).execute()
        if not c.data: return jsonify({'status': 'ignored'}), 200
        ch = c.data[0]
        get_db().table("child_locations").upsert({"child_id": ch['id'], "tracker_device_id": lid, "latitude": lat,
            "longitude": lng, "speed": d.get('speed'), "updated_at": current_sa_time()}).execute()
        state = dict(ch.get('zone_state') or {}); rad = ch.get('zone_radius') or 150; changed = False
        for zone, flag in (('school', 'notify_school'), ('home', 'notify_home')):
            zl, zg = ch.get(f'{zone}_lat'), ch.get(f'{zone}_lng')
            if zl is None or zg is None: continue
            inside = _haversine(lat, lng, zl, zg) <= rad; was = state.get(zone)
            if was is not None and inside != was:
                if ch.get(flag, True):
                    verb = 'arrived at' if inside else 'left'
                    notify_parent(ch['parent_username'], f"{ch['name']} {verb} {zone}", f"<b>{_h(ch['name'])}</b> {verb} the {zone} zone.")
                if not inside and ch.get('call_confirm'):
                    print(f"[STAFF CALL TASK] confirm child {ch['id']} left {zone}")  # TODO: staff queue
            if was != inside: state[zone] = inside; changed = True
        if changed:
            if state and not any(state.values()) and ch.get('notify_zone', True):
                notify_parent(ch['parent_username'], f"{ch['name']} is outside the safe zones",
                              f"<b>{_h(ch['name'])}</b> is outside every zone you set.")
            get_db().table("children").update({"zone_state": state}).eq("id", ch['id']).execute()
        return jsonify({'status': 'success'})
    except Exception as e:
        print("GPS:", e); return jsonify({'status': 'error', 'message': str(e)}), 500

# ---------------- community groups ----------------
def _is_member(cid, u):
    return bool(get_db().table("community_members").select("username").eq("community_id", cid).eq("username", u).execute().data)

@app.route('/api/groups/create', methods=['POST', 'OPTIONS'])
@require_auth
def groups_create():
    d = request.json or {}
    name = re.sub(r'\s+', ' ', d.get('name', '').strip())
    if len(name) < 3: return jsonify({'status': 'error', 'message': 'Group name is too short.'}), 400
    if get_db().table("communities").select("id").eq("name_key", _name_key(name)).execute().data:
        return jsonify({'status': 'error', 'message': 'A group with this exact name exists. Add a section or suburb to the name.'}), 409
    try:
        r = get_db().table("communities").insert({"name": name, "name_key": _name_key(name), "suburb": d.get('suburb', '').strip(),
            "town": d.get('town', '').strip(), "province": d.get('province', '').strip(), "rules": d.get('rules', '').strip()[:1000],
            "created_by": g.current_user}).execute()
    except Exception:
        return jsonify({'status': 'error', 'message': 'A group with this name already exists.'}), 409
    return jsonify({'status': 'success', 'group': r.data[0]})

@app.route('/api/groups/search', methods=['POST', 'OPTIONS'])
@require_auth
def groups_search():
    q = _clean((request.json or {}).get('q'))
    qry = get_db().table("communities").select("*")
    if q: qry = qry.or_(f"name.ilike.%{q}%,suburb.ilike.%{q}%,town.ilike.%{q}%,province.ilike.%{q}%")
    return jsonify({'status': 'success', 'groups': qry.order("name").limit(30).execute().data or []})

@app.route('/api/groups/join', methods=['POST', 'OPTIONS'])
@require_auth
def groups_join():
    d = request.json or {}
    if not d.get('agreed_terms'): return jsonify({'status': 'error', 'message': 'You must agree to the group terms.'}), 400
    get_db().table("community_members").upsert({"community_id": d.get('group_id'), "username": g.current_user, "agreed_terms": True,
        "share_location": bool(d.get('share_location')), "share_child_photo": bool(d.get('share_child_photo'))}).execute()
    return jsonify({'status': 'success'})

@app.route('/api/groups/leave', methods=['POST', 'OPTIONS'])
@require_auth
def groups_leave():
    get_db().table("community_members").delete().eq("community_id", (request.json or {}).get('group_id')).eq("username", g.current_user).execute()
    return jsonify({'status': 'success'})

@app.route('/api/groups/mine', methods=['POST', 'OPTIONS'])
@require_auth
def groups_mine():
    m = get_db().table("community_members").select("community_id, share_location, share_child_photo").eq("username", g.current_user).execute().data or []
    if not m: return jsonify({'status': 'success', 'groups': []})
    gs = get_db().table("communities").select("*").in_("id", [x['community_id'] for x in m]).execute().data or []
    opt = {x['community_id']: x for x in m}
    for x in gs: x['my_opts'] = opt.get(x['id'])
    return jsonify({'status': 'success', 'groups': gs})

@app.route('/api/groups/messages', methods=['POST', 'OPTIONS'])
@require_auth
def groups_messages():
    cid = (request.json or {}).get('group_id')
    if not _is_member(cid, g.current_user): return jsonify({'status': 'error', 'messages': []}), 403
    r = get_db().table("community_messages").select("*").eq("community_id", cid).order("created_at").limit(200).execute()
    return jsonify({'status': 'success', 'messages': r.data or []})

@app.route('/api/groups/send', methods=['POST', 'OPTIONS'])
@require_auth
def groups_send():
    d = request.json or {}; cid = d.get('group_id'); body = (d.get('body') or '').strip()
    if not body or not _is_member(cid, g.current_user): return jsonify({'status': 'error'}), 403
    nm = get_db().table("users").select("name").eq("username", g.current_user).execute()
    get_db().table("community_messages").insert({"community_id": cid, "sender_username": g.current_user,
        "sender_name": (nm.data[0]['name'] if nm.data else g.current_user), "body": body[:1000]}).execute()
    return jsonify({'status': 'success'})

@app.route('/api/groups/emergency', methods=['POST', 'OPTIONS'])
@require_auth
def groups_emergency():
    d = request.json or {}; cid = d.get('group_id')
    mem = get_db().table("community_members").select("*").eq("community_id", cid).eq("username", g.current_user).execute()
    if not mem.data: return jsonify({'status': 'error'}), 403
    opts = mem.data[0]; lat = lng = photo = None
    if opts.get('share_location'): lat, lng = d.get('lat'), d.get('lng')
    if opts.get('share_child_photo') and d.get('child_id'):
        ch = get_db().table("children").select("photo").eq("id", d['child_id']).eq("parent_username", g.current_user).execute()
        photo = ch.data[0].get('photo') if ch.data else None
    note = (d.get('note') or 'Emergency, please help.')[:300]
    nm = get_db().table("users").select("name").eq("username", g.current_user).execute()
    get_db().table("community_messages").insert({"community_id": cid, "sender_username": g.current_user,
        "sender_name": (nm.data[0]['name'] if nm.data else g.current_user), "body": "🚨 EMERGENCY: " + note,
        "kind": "emergency", "lat": lat, "lng": lng, "photo_url": photo}).execute()
    send_whatsapp(cid, f"EMERGENCY: {note} " + (f"https://maps.google.com/?q={lat},{lng}" if lat else ""))
    # TODO: police / roadblock integration once you have a contact
    return jsonify({'status': 'success'})

# ---------------- admin ----------------
@app.route('/api/admin/users', methods=['GET', 'POST', 'OPTIONS'])
@require_auth
@require_role('admin')
def admin_get_users():
    try:
        users = get_db().table("users").select("username, name, email, role, school_code, photo, created_at").execute().data or []
        schools = get_db().table("schools").select("school_code, school_name, principal_username").execute().data or []
        by_p = {s['principal_username']: s for s in schools}
        stations = []
        for p in [u for u in users if u['role'] == 'principal']:
            p = dict(p); p['school_name'] = by_p.get(p['username'], {}).get('school_name', 'N/A'); stations.append(p)
        kids = get_db().table("children").select("id, name, surname, grade, school_name, parent_username, tracker_device_id, created_at").execute().data or []
        return jsonify({'status': 'success', 'principals': stations,
                        'general_users': [u for u in users if u['role'] == 'general'], 'children': kids,
                        'communities': get_db().table("communities").select("id, name, town, province, created_by, created_at").execute().data or []})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/admin/delete_user', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('admin')
def admin_delete_user():
    u = (request.json or {}).get('username')
    if not u: return jsonify({'status': 'error', 'message': 'Missing username'}), 400
    r = get_db().table("users").select("role").eq("username", u).execute()
    if not r.data: return jsonify({'status': 'error', 'message': 'User not found'}), 404
    if r.data[0]['role'] == 'principal':
        get_db().table("schools").delete().eq("principal_username", u).execute()
    get_db().table("users").delete().eq("username", u).execute()
    return jsonify({'status': 'success'})

@app.route('/api/admin/delete_group', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('admin')
def admin_delete_group():
    get_db().table("communities").delete().eq("id", (request.json or {}).get('id')).execute()
    return jsonify({'status': 'success'})

@app.route('/api/admin/cleanup_expired', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('admin')
def cleanup_expired():
    now = datetime.now(SA_TIMEZONE).isoformat()
    for t in ("pending_signups", "password_reset_codes"):
        get_db().table(t).delete().lt("expires_at", now).execute()
    return jsonify({'status': 'success'})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"Safe Drive backend on port {port}")
    serve(app, host='0.0.0.0', port=port, threads=8)
