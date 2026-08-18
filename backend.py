import os
import cv2
import numpy as np
import base64
import random
import datetime
import string
import time
import bcrypt
import threading
from flask import Flask, request, jsonify, send_from_directory, g
from flask_cors import CORS
from supabase import create_client, Client, ClientOptions
from httpx import Timeout
import smtplib
from dotenv import load_dotenv
from email.message import EmailMessage
from zoneinfo import ZoneInfo
from datetime import datetime
from flask import render_template
from auth import issue_token, require_auth, require_role  # NEW: JWT auth module

print("BACKEND STARTED")
load_dotenv()
try:
    from waitress import serve
except ImportError:
    print("CRITICAL ENGINE ERROR: Waitress is not installed. Please run 'pip install waitress' in your terminal setup.")
    exit(1)

app = Flask(__name__)

SA_TIMEZONE = ZoneInfo("Africa/Johannesburg")

def current_sa_time():
    return (
        datetime.now(SA_TIMEZONE)
        .replace(microsecond=0)
        .isoformat(timespec="seconds")
    )


@app.route('/')
def home():
    return render_template('index.html')

@app.before_request
def log_requests():
    print("REQUEST:", request.method, request.path)

ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})

UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

face_cascade = cv2.CascadeClassifier('haarcascade_frontalface_default.xml')
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

thread_local = threading.local()

def get_db() -> Client:
    if not hasattr(thread_local, "supabase_client"):
        opts = ClientOptions(
            postgrest_client_timeout=Timeout(30.0, connect=30.0, read=60.0, write=30.0),
            schema="public"
        )
        thread_local.supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY, options=opts)
    return thread_local.supabase_client

def check_face_proximity(image_path):
    img = cv2.imread(image_path)
    if img is None:
        return False, "Biometric Stream Read Error: Camera data structure unreadable."

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.1, 4)

    if len(faces) == 0:
        return False, "Biometric Match Failure: No human face detected in frame. Adjust alignment and lighting."

    (x, y, w, h) = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)[0]
    face_area = w * h
    total_area = img.shape[0] * img.shape[1]

    if (face_area / total_area) < 0.20:
        return False, "Security Boundary Warning: Face detected too far from lens. Move closer to device."

    return True, "Biometric Pass Secured"

SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")

pending_users = {}
pending_parent_links = {}
password_reset_codes = {}

def generate_email_wrapper(content, subtitle="Security Notification"):
    return f"""
    <div style="background-color: #010409; padding: 50px 20px; font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
        <div style="max-width: 650px; margin: auto; background: #0d1117; border-radius: 20px; overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,0.5); border: 1px solid #30363d;">
            <div style="background: linear-gradient(135deg, #020218 0%, #0a0a2e 100%); padding: 45px; text-align: center; border-bottom: 3px solid #10b981;">
                <h1 style="margin: 0; color: #ffffff; font-size: 32px; letter-spacing: 6px; font-weight: 900; text-shadow: 0 2px 4px rgba(16,185,129,0.3);">SAFE <span style="color: #10b981;">DRIVE</span></h1>
                <p style="margin: 10px 0 0; color: #10b981; font-size: 11px; text-transform: uppercase; letter-spacing: 3px; font-weight: 600;">{subtitle}</p>
            </div>
            <div style="padding: 50px 45px; color: #c9d1d9; line-height: 1.8; font-size: 16px;">
                <h2 style="color: #ffffff; margin-top: 0; margin-bottom: 25px; font-size: 22px; border-bottom: 1px solid #30363d; padding-bottom: 10px;">Official Authorization Required</h2>
                {content}
                <div style="margin-top: 50px; padding-top: 25px; border-top: 1px solid #30363d; font-size: 13px; color: #8b949e; line-height: 1.6;">
                    <p style="color: #e6edf3; font-weight: bold; margin-bottom: 5px;">Why did I receive this email?</p>
                    <p style="margin-top: 0;">This is an automated, highly secure communication from the Safe Drive AI Network.</p>
                    <p style="color: #e6edf3; font-weight: bold; margin-bottom: 5px; margin-top: 20px;">Need further assistance?</p>
                    <p style="margin-top: 0;">Contact our administrative security team at:<br>
                    <a href="mailto:safedrive@gmail.com" style="color: #10b981; text-decoration: none; font-weight: bold; font-size: 15px;">safedrive@gmail.com</a></p>
                </div>
            </div>
            <div style="background: #161b22; padding: 30px; text-align: center; color: #8b949e; font-size: 12px; border-top: 1px solid #30363d;">
                <p style="margin: 0; font-weight: 700; letter-spacing: 1px;">© 2026 SAFE DRIVE LOGISTICS & SECURITY</p>
            </div>
        </div>
    </div>
    """

def send_mail(to_email, subject, body):
    SENDER_EMAIL = os.getenv("SENDER_EMAIL")
    APP_PASSWORD = os.getenv("SENDER_PASSWORD")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email
    msg.set_content(body, subtype="html")

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, APP_PASSWORD)
            server.send_message(msg)
            print(f"✅ Email successfully sent to {to_email}")
    except Exception as e:
        print(f"❌ [CRITICAL SMTP FAILURE] Error: {e}")

# =====================================================================
# OPEN ROUTES — no identity exists yet, so these stay unauthenticated.
# (login, signup, otp verification, password reset)
# =====================================================================



@app.route('/moderator_signup')
@app.route('/moderatore_signup.html')
def moderator_signup_page():
    return render_template('moderatore_signup.html')

@app.route('/general_signup')
@app.route('/general_signup.html')
def general_signup_page():
    return render_template('general_signup.html')


@app.route('/signup')
@app.route('/signup.html')
def signup_page():
    return render_template('signup.html')

@app.route('/student_signup')
@app.route('/student_signup.html')
def student_signup_page():
    return render_template('student_signup.html')

@app.route('/driver_signup')
@app.route('/driver_signup.html')
def driver_signup_page():
    return render_template('driver_signup.html')



@app.route('/principal_signup')
@app.route('/principal_signup.html')
def principal_signup_page():
    return render_template('principal_signup.html')




@app.route('/student_dashboard.html')
def student_dashboard():
    return render_template('student_dashboard.html')

@app.route('/admin_dashboard.html')
def admin_dashboard():
    return render_template('admin_dashboard.html')

@app.route('/driver_dashboard.html')
def driver_dashboard():
    return render_template('driver_dashboard.html')

@app.route('/principal_dashboard.html')
def principal_dashboard():
    return render_template('principal_dashboard.html')

@app.route('/moderator_dashboard.html')
def moderator_dashboard():
    return render_template('moderator_dashboard.html')

@app.route('/general_dashboard.html')
def general_dashboard():
    return render_template('general_dashboard.html')


@app.route('/login')
@app.route('/login.html')
def login_page():
    return render_template('login.html')

@app.route('/choice')
@app.route('/choice.html')
def choice_page():
    return render_template('choice.html')



@app.route('/api/admin/users', methods=['GET', 'OPTIONS'])
@require_auth
@require_role('admin')
def admin_get_users():
    try:
        all_users = get_db().table("users").select(
            "username, name, email, phone, role, school_code, driver_mode, transport_type, "
            "vehicle_type, number_plate, verification_image, license_image, grade, "
            "moderator_type, moderated_grade, created_at"
        ).execute().data or []

        drivers = [u for u in all_users if u.get('role') == 'driver' and u.get('transport_type') != 'school_events']
        event_drivers = [u for u in all_users if u.get('role') == 'driver' and u.get('transport_type') == 'school_events']
        principals_raw = [u for u in all_users if u.get('role') == 'principal']
        students = [u for u in all_users if u.get('role') == 'student']
        moderators = [u for u in all_users if u.get('role') in ('moderator', 'teacher')]
        general_users = [u for u in all_users if u.get('role') == 'general']

        schools_res = get_db().table("schools").select("school_code, school_name, principal_username").execute()
        schools_by_principal = {s['principal_username']: s.get('school_name') for s in (schools_res.data or [])}

        principals = []
        for p in principals_raw:
            p = dict(p)
            p['school_name'] = schools_by_principal.get(p.get('username'), 'N/A')
            principals.append(p)

        students_with_school = []
        school_names_by_code = {s['school_code']: s.get('school_name') for s in (schools_res.data or [])}
        for s in students:
            s = dict(s)
            s['school_name'] = school_names_by_code.get(s.get('school_code'), 'N/A')
            students_with_school.append(s)

        return jsonify({
            'status': 'success',
            'drivers': drivers,
            'event_drivers': event_drivers,
            'principals': principals,
            'students': students_with_school,
            'moderators': moderators,
            'general_users': general_users
        }), 200
    except Exception as e:
        print(f"Admin users fetch error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/reject_license', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('admin')
def admin_reject_license():
    """Clears a driver's license image so they're forced to re-upload,
    without deleting their whole account."""
    try:
        username = request.json.get('username')
        if not username:
            return jsonify({'status': 'error', 'message': 'Missing username'}), 400

        get_db().table("users").update({"license_image": None}).eq("username", username).execute()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/delete_user', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('admin')
def admin_delete_user():
    try:
        username = request.json.get('username')
        if not username:
            return jsonify({'status': 'error', 'message': 'Missing username'}), 400

        user_check = get_db().table("users").select("role").eq("username", username).execute()
        if not user_check.data:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        user_role = user_check.data[0].get('role')

        if user_role == 'driver':
            get_db().table("parent_driver_rooms").delete().eq("driver_username", username).execute()
            get_db().table("posts").delete().eq("username", username).execute()
        elif user_role == 'general':
            get_db().table("parent_driver_rooms").delete().eq("parent_username", username).execute()
            get_db().table("parent_principal_rooms").delete().eq("parent_username", username).execute()
        elif user_role == 'student':
            get_db().table("bookings").delete().eq("student_username", username).execute()
            get_db().table("attendance_logs").delete().eq("student_username", username).execute()
            get_db().table("parent_driver_rooms").delete().eq("child_username", username).execute()
            get_db().table("parent_principal_rooms").delete().eq("child_username", username).execute()

        get_db().table("users").delete().eq("username", username).execute()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200

    try:
        data = request.json
        u = data.get('username', '').lower().strip()
        p = data.get('password', '')

        res = get_db().table("users").select("*").eq("username", u).execute()
        user = res.data[0] if res.data else None
        source_table = "users"

        if not user:
            admin_res = get_db().table("admins").select("*").eq("username", u).execute()
            if admin_res.data:
                user = admin_res.data[0]
                source_table = "admins"

        if not user:
            return jsonify({'status': 'error', 'msg': 'Identity Verification Failed: Invalid User Credentials'}), 401

        stored_password = user.get('password', '')
        password_valid = False

        try:
            password_valid = bcrypt.checkpw(p.encode('utf-8'), stored_password.encode('utf-8'))
        except:
            if str(stored_password) == str(p):
                password_valid = True
                hashed_pw = bcrypt.hashpw(p.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                get_db().table(source_table).update({"password": hashed_pw}).eq("username", u).execute()

        if password_valid:
            role = user.get('role') or ('admin' if source_table == 'admins' else None)
            token = issue_token(username=u, role=role)
            return jsonify({
                'status': 'success',
                'token': token,
                'role': role,
                'name': user.get('name') or user.get('username'),
                'username': u,
                'school_code': str(user['school_code']).upper() if user.get('school_code') else None,
                'driver_mode': user.get('driver_mode'),
                'moderator_type': user.get('moderator_type'),
                'moderated_grade': user.get('moderated_grade')
            }), 200

        return jsonify({'status': 'error', 'msg': 'Identity Verification Failed: Invalid User Credentials'}), 401

    except Exception as e:
        return jsonify({'status': 'error', 'msg': f'Database processing failure: {str(e)}'}), 500

@app.route('/api/<role>/signup', methods=['POST', 'OPTIONS'])
def signup(role):
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
    try:
        data = request.json or {}
        u = data.get('username', '').lower().strip()
        sc = str(data.get('school_code', '')).strip().upper()
        school_name = str(data.get("school_name", "")).strip()

        if not u:
            return jsonify({'status': 'error', 'message': 'Missing username'}), 400

        existing = get_db().table("users").select("username").eq("username", u).execute()
        if existing.data:
            return jsonify({'status': 'error', 'message': 'Username already registered to an active profile.'}), 400

        if sc and role == "principal":
            school_check = get_db().table("schools").select("school_code").eq("school_code", sc).execute()
            if not school_check.data:
                get_db().table("schools").insert({
                    "school_code": sc, "school_name": school_name, "principal_username": u
                }).execute()
                print("AUTO-CREATED SCHOOL:", sc)

        face_b64 = data.get('face_image')
        if face_b64:
            try:
                if "," in face_b64:
                    face_b64 = face_b64.split(",", 1)[1]
                img_data = base64.b64decode(face_b64)
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{u}.jpg")
                with open(img_path, "wb") as f:
                    f.write(img_data)
                is_valid, msg = check_face_proximity(img_path)
                if os.path.exists(img_path):
                    os.remove(img_path)
                if not is_valid:
                    return jsonify({'status': 'error', 'message': msg}), 400
            except Exception:
                return jsonify({'status': 'error', 'message': 'Facial biometric data could not be verified. Check lighting parameters.'}), 400

        otp = str(random.randint(1111, 9999))
        pending_users[u] = {
            **data, "role": role, "v_code": otp, "school_code": sc,
            "verification_image": face_b64,
            "parent_number": data.get("parent_number") or data.get("parent_phone") or ""
        }
        print("PENDING STORED:", u)

        email_content = f"""
        <p style="font-size: 18px;">Your account registration requires final verification.</p>
        <div style="background: #161b22; padding: 25px; border-radius: 10px; margin: 25px 0; border-left: 4px solid #10b981; text-align: center;">
            <p style="margin: 0; color: #8b949e; font-size: 12px; text-transform: uppercase;">Unique Security PIN</p>
            <h2 style="margin: 10px 0 0; font-size: 32px; color: #10b981; letter-spacing: 8px;">{otp}</h2>
        </div>
        """
        threading.Thread(target=send_mail, args=(data.get('email'), "Safe Drive Registration Code", generate_email_wrapper(email_content, "Account Verification"))).start()

        return jsonify({'status': 'pending', 'username': u}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/verify-and-save', methods=['POST', 'OPTIONS'])
def verify_and_save():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
    try:
        import uuid
        data = request.get_json(silent=True) or {}
        u = str(data.get('username', '')).lower().strip()
        code = str(data.get('code', '')).strip()

        if not u or not code:
            return jsonify({'status': 'error', 'message': 'Missing data'}), 400
        if u not in pending_users:
            return jsonify({'status': 'error', 'message': 'Session expired'}), 400

        p_u = pending_users[u]
        stored = str(p_u.get('v_code', '')).strip()
        if stored != code:
            return jsonify({'status': 'error', 'message': 'Invalid code'}), 400

        face_image = p_u.get('verification_image', None)
        verification_image_url = None
        if face_image:
            try:
                if isinstance(face_image, str) and "," in face_image:
                    face_image = face_image.split(",", 1)[1]
                image_bytes = base64.b64decode(face_image)
                filename = f"{uuid.uuid4()}.jpg"
                get_db().storage.from_("verification_images").upload(path=filename, file=image_bytes, file_options={"content-type": "image/jpeg"})
                verification_image_url = get_db().storage.from_("verification_images").get_public_url(filename)
            except Exception as e:
                print("IMAGE UPLOAD FAILED:", str(e))

        raw_password = p_u.get('password', '')
        hashed_password = bcrypt.hashpw(raw_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        get_db().table("users").insert({
            "username": u, "password": hashed_password, "name": p_u.get('name'),
            "email": p_u.get('email'), "role": p_u.get('role'), "school_code": p_u.get('school_code'),
            "verification_image": verification_image_url, "parent_email": p_u.get('parent_email'),
            "parent_number": p_u.get('parent_number'), "grade": p_u.get('grade'),
            "driver_mode": p_u.get('driver_mode'), "moderator_type": p_u.get('moderator_type'),
            "moderated_grade": p_u.get('moderated_grade'), "vehicle_type": p_u.get('vehicle_type')
        }).execute()

        del pending_users[u]
        print("USER CREATED:", u)
        return jsonify({'status': 'success', 'message': 'Account verified successfully'}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': 'Server error'}), 500


@app.route('/api/moderator/signup', methods=['POST', 'OPTIONS'])
def moderator_signup():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
    try:
        data = request.json
        u = data.get('username', '').lower().strip()
        face_b64 = data.get('face_image')

        existing = get_db().table("users").select("username").eq("username", u).execute()
        if existing.data:
            return jsonify({'status': 'error', 'message': 'Username already registered to an active profile.'}), 400

        if face_b64:
            if "," in face_b64:
                face_b64 = face_b64.split(",", 1)[1]
            img_data = base64.b64decode(face_b64)
            img_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_mod_{u}.jpg")
            with open(img_path, "wb") as f:
                f.write(img_data)
            is_valid, msg = check_face_proximity(img_path)
            if os.path.exists(img_path):
                os.remove(img_path)
            if not is_valid:
                return jsonify({'status': 'error', 'message': msg}), 400

        otp = str(random.randint(1111, 9999))
        data.update({'role': 'moderator', 'v_code': otp})
        pending_users[u] = data

        email_content = f"""
        <p style="font-size: 18px;">Your Moderator account registration requires final PIN confirmation.</p>
        <div style="background: #161b22; padding: 25px; border-radius: 10px; margin: 25px 0; border-left: 4px solid #10b981; text-align: center;">
            <p style="margin: 0; color: #8b949e; font-size: 12px; text-transform: uppercase;">Moderator Security PIN</p>
            <h2 style="margin: 10px 0 0; font-size: 32px; color: #10b981; letter-spacing: 8px;">{otp}</h2>
        </div>
        """
        threading.Thread(target=send_mail, args=(data.get('email'), "Moderator Registration Code", generate_email_wrapper(email_content, "Moderator Overwatch Sec"))).start()
        return jsonify({'status': 'pending', 'username': u}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/moderator/verify_boundaries', methods=['POST', 'OPTIONS'])
def verify_moderator_boundaries():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
    try:
        data = request.json
        sc = data.get('school_code', '').upper().strip()
        grade = str(data.get('grade', '')).strip()
        mod_type = data.get('moderator_type', '')
        passcode = data.get('assistant_passcode', '').strip()

        school_check = get_db().table("schools").select("school_name").eq("school_code", sc).execute()
        if not school_check.data:
            return jsonify({'status': 'error', 'message': 'REGISTRATION BLOCKED: The specified School Code does not exist in the platform system.'}), 404

        if mod_type == 'teacher':
            check = get_db().table("users").select("username").eq("school_code", sc).eq("moderated_grade", grade).eq("moderator_type", "teacher").execute()
            if check.data:
                return jsonify({'status': 'error', 'message': f'CRITICAL PROTECTION WARNING: A verified Lead Teacher already exists for Grade {grade} at this institution.'}), 400
        else:
            match = get_db().table("users").select("username").eq("school_code", sc).eq("moderated_grade", grade).eq("assistant_passcode", passcode).eq("moderator_type", "teacher").execute()
            if not match.data:
                return jsonify({'status': 'error', 'message': 'SECURITY GAP FAILURE: Invalid Assistant Passcode provided.'}), 400

        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Boundary validation pipeline exception: {str(e)}'}), 500


@app.route('/api/forgot-password', methods=['POST', 'OPTIONS'])
def forgot_password():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
    try:
        data = request.json
        u = data.get('username', '').lower().strip()
        res = get_db().table("users").select("email").eq("username", u).execute()
        if not res.data:
            return jsonify({'status': 'error', 'message': 'Username not found in the system.'}), 404

        user_email = res.data[0]['email']
        otp = str(random.randint(100000, 999999))
        password_reset_codes[u] = otp

        email_body = f"""
        <p style="font-size: 18px;">A password reset was requested for your account: <b>{u}</b>.</p>
        <div style="background: #161b22; padding: 25px; border-radius: 10px; margin: 25px 0; border-left: 4px solid #f59e0b; text-align: center;">
            <p style="margin: 0; color: #8b949e; font-size: 12px; text-transform: uppercase;">Secure Reset Code</p>
            <h2 style="margin: 10px 0 0; font-size: 32px; color: #f59e0b; letter-spacing: 8px;">{otp}</h2>
        </div>
        """
        threading.Thread(target=send_mail, args=(user_email, "Password Reset Code", generate_email_wrapper(email_body, "Account Recovery"))).start()
        return jsonify({'status': 'success', 'message': 'Code sent to registered email.'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/api/reset-password', methods=['POST', 'OPTIONS'])
def reset_password():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
    try:
        data = request.json
        u = data.get('username', '').lower().strip()
        code = data.get('code', '').strip()
        new_password = data.get('newPassword', '')

        if u in password_reset_codes and str(password_reset_codes[u]) == code:
            hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            get_db().table("users").update({"password": hashed_password}).eq("username", u).execute()
            del password_reset_codes[u]
            return jsonify({'status': 'success'}), 200

        return jsonify({'status': 'error', 'message': 'Invalid or expired recovery code reference.'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/api/verify-parent-otp', methods=['POST', 'OPTIONS'])
def verify_parent_otp():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
    try:
        data = request.json
        cu = data.get('child_username', '').lower()
        code = str(data.get('code'))
        if cu in pending_parent_links and str(pending_parent_links[cu]['v_code']) == code:
            child_info = pending_parent_links[cu]['child_data']
            del pending_parent_links[cu]
            return jsonify({'status': 'success', 'child_data': child_info}), 200
        return jsonify({'status': 'error', 'msg': 'Invalid security authorization credentials.'}), 401
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


# =====================================================================
# PROTECTED ROUTES — every one of these now requires a valid
# "Authorization: Bearer <token>" header (issued at /api/login).
# Identity is read from g.current_user / g.current_role — NEVER from
# the request body anymore, so it can't be spoofed by editing JSON.
# =====================================================================

@app.route('/api/get_my_bookings', methods=['POST', 'OPTIONS'])
@require_auth
def get_my_bookings():
    try:
        u = g.current_user
        role = g.current_role
        col = "driver_username" if role == "driver" else "student_username"
        res = get_db().table("bookings").select("*").eq(col, u).execute()
        return jsonify({'status': 'success', 'bookings': res.data if res.data else []}), 200
    except Exception as e:
        print(f"[RECOVERABLE] /api/get_my_bookings stalled: {e}")
        return jsonify({'status': 'success', 'bookings': []}), 200


@app.route('/api/student/book', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('student', 'general')
def student_book():
    try:
        data = request.json
        booking_id = f"BK-{random.randint(1000,9999)}"
        student_u = g.current_user

        requested_code = data.get('school_code')
        if requested_code == "D2D_GLOBAL":
            db_school_code = "D2D_GLOBAL"
        else:
            user_res = get_db().table("users").select("school_code").eq("username", student_u).execute()
            db_school_code = user_res.data[0]['school_code'] if user_res.data and user_res.data[0].get('school_code') else requested_code

        booking_data = {
            "booking_id": booking_id, "student_username": student_u,
            "student_name": data.get('student_name'), "driver_username": data.get('driver_username'),
            "driver_name": data.get('driver_name'), "school_code": db_school_code, "status": "Pending",
            "created_at": current_sa_time(), "last_updated": current_sa_time(), "last_status_time": current_sa_time(),
            "initial_msg": data.get('message', 'New booking request tracking line initiated.'), "chat": []
        }
        get_db().table("bookings").insert(booking_data).execute()

        if db_school_code == "D2D_GLOBAL":
            room_data = {
                "booking_id": booking_id, "parent_username": student_u, "parent_name": data.get("student_name"),
                "driver_username": data.get("driver_username"), "driver_name": data.get("driver_name"),
                "transport_type": data.get("transport_type", "Door to Door"), "status": "Pending"
            }
            get_db().table("general_driver_rooms").insert(room_data).execute()

        return jsonify({"status": "success", "booking_id": booking_id}), 200
    except Exception as e:
        return jsonify({"status": "error", "msg": f"Booking injection halted: {str(e)}"}), 500


@app.route('/api/driver/respond_booking', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('driver')
def respond_booking():
    try:
        data = request.json
        bid = data.get('booking_id')
        status = data.get('status')
        now = current_sa_time()

        booking_check = get_db().table("bookings").select("*").eq("booking_id", bid).execute()
        if not booking_check.data or booking_check.data[0].get('driver_username') != g.current_user:
            return jsonify({'status': 'error', 'msg': 'Not authorized to modify this booking'}), 403

        booking = booking_check.data[0]

        if status in ["Rejected", "Declined"]:
            get_db().table("bookings").delete().eq("booking_id", bid).execute()
            return jsonify({"status": "success", "message": "Booking rejected and removed."}), 200

        if status == "Finished":
            student_u = booking.get('student_username')
            get_db().table("parent_driver_rooms") \
                .delete() \
                .eq("child_username", student_u) \
                .eq("driver_username", g.current_user) \
                .execute()

            get_db().table("bookings").update({"status": "Finished", "tracking_status": "Trip Completed", "last_status_time": now, "last_updated": now}).eq("booking_id", bid).execute()
            get_db().table("bookings").delete().eq("booking_id", bid).execute()
            return jsonify({"status": "success", "message": "Trip completed and booking removed."}), 200

        update_payload = {"status": status, "last_status_time": now, "last_updated": now}

        if status == "Accepted":
            update_payload["tracking_status"] = "APPROVED"
            get_db().table("bookings").update(update_payload).eq("booking_id", bid).execute()

            school_code = booking.get('school_code') or ""
            student_u = booking.get('student_username')
            student_name = booking.get('student_name')

            # Only school-linked bookings get a parent-driver room —
            # D2D_GLOBAL bookings already have their own general_driver_rooms row.
            if school_code != "D2D_GLOBAL" and student_u:
                monitor_check = get_db().table("parent_principal_rooms") \
                    .select("parent_username") \
                    .eq("child_username", student_u) \
                    .limit(1) \
                    .execute()

                verified_parent = monitor_check.data[0]['parent_username'] if monitor_check.data else None

                existing_room = get_db().table("parent_driver_rooms") \
                    .select("id") \
                    .eq("child_username", student_u) \
                    .eq("driver_username", g.current_user) \
                    .execute()

                if not existing_room.data:
                    get_db().table("parent_driver_rooms").insert({
                        "child_name": student_name,
                        "child_username": student_u,
                        "parent_username": verified_parent,
                        "driver_username": g.current_user,
                        "school_code": school_code
                    }).execute()

            return jsonify({"status": "success", "new_status": status}), 200

        get_db().table("bookings").update(update_payload).eq("booking_id", bid).execute()
        return jsonify({"status": "success", "new_status": status}), 200
    except Exception as e:
        print(e)
        return jsonify({"status": "error", "msg": str(e)}), 500


@app.route('/api/driver/get_parent_rooms', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('driver')
def get_driver_parent_rooms():
    try:
        res = get_db().table("parent_driver_rooms") \
            .select("child_username, parent_username") \
            .eq("driver_username", g.current_user) \
            .execute()
        return jsonify({'status': 'success', 'rooms': res.data or []}), 200
    except Exception as e:
        return jsonify({'status': 'success', 'rooms': []}), 200

@app.route('/api/parent/get_logs', methods=['POST', 'OPTIONS'])
@require_auth
def get_parent_logs():
    try:
        data = request.json
        s_u = data.get('student_name', '').strip()

        if g.current_role == 'student':
            if s_u.lower() != g.current_user.lower():
                return jsonify({'status': 'error', 'logs': []}), 403
        elif g.current_role == 'general':
            link_check = get_db().table("parent_principal_rooms") \
                .select("child_username") \
                .eq("parent_username", g.current_user) \
                .execute()
            linked_children = [r['child_username'] for r in (link_check.data or [])]
            if s_u not in linked_children:
                return jsonify({'status': 'error', 'logs': []}), 403
        else:
            return jsonify({'status': 'error', 'logs': []}), 403

        res = get_db().table("safety_logs").select("*").ilike("student_name", s_u).order("timestamp", desc=True).limit(50).execute()
        return jsonify({'status': 'success', 'logs': res.data or []}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/api/booking/chat', methods=['POST', 'OPTIONS'])
@require_auth
def booking_chat():
    try:
        data = request.json
        bid = data.get('booking_id')
        msg_text = data.get('message_obj', {}).get('text', '')
        msg_obj = {"sender": g.current_user, "text": msg_text, "time": current_sa_time()}

        res = get_db().table("bookings").select("chat, student_username, driver_username").eq("booking_id", bid).execute()
        if not res.data:
            return jsonify({'status': 'error', 'msg': 'Booking not found'}), 404

        booking = res.data[0]
        if g.current_user not in (booking.get('student_username'), booking.get('driver_username')):
            return jsonify({'status': 'error', 'msg': 'Not part of this conversation'}), 403

        current_chat = booking.get('chat') or []
        current_chat.append(msg_obj)
        get_db().table("bookings").update({"chat": current_chat}).eq("booking_id", bid).execute()
        return jsonify({'status': 'success', 'chat': current_chat}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': f'Matrix messaging write fault: {str(e)}'}), 500


@app.route('/api/transport/track', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('driver')
def track_transport():
    try:
        data = request.json
        bid = data.get('booking_id')
        action = data.get('action')
        forced_time = current_sa_time()
        status_msg = "PICKED UP" if action == 'pickup' else "DROPPED OFF"

        owner_check = get_db().table("bookings").select("driver_username").eq("booking_id", bid).execute()
        if not owner_check.data or owner_check.data[0].get('driver_username') != g.current_user:
            return jsonify({'status': 'error', 'msg': 'Not authorized to update this booking'}), 403

        get_db().table("bookings").update({"tracking_status": status_msg, "last_updated": forced_time}).eq("booking_id", bid).execute()
        b_res = get_db().table("bookings").select("*").eq("booking_id", bid).execute()

        if b_res.data:
            b = b_res.data[0]
            get_db().table("safety_logs").insert({
                "student_name": b['student_name'], "driver_name": b['driver_name'],
                "school_code": b['school_code'], "status": status_msg, "timestamp": forced_time, "log_type": "transport"
            }).execute()
            get_db().table("principal_transit_logs").insert({
                "student_username": b['student_username'], "student_name": b['student_name'],
                "driver_name": b['driver_name'], "status": status_msg, "school_code": b['school_code'], "timestamp": forced_time
            }).execute()

        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': f'Tracking coordinate drop: {str(e)}'}), 500


@app.route('/api/driver/update_role', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('driver')
def driver_update_role():
    try:
        role = request.json.get('role')
        get_db().table("users").update({"driver_mode": role}).eq("username", g.current_user).execute()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/api/posts/create', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('driver')
def create_post():
    try:
        data = request.form
        post_id = f"POST-{random.randint(10000, 99999)}"
        post_data = {
            "post_id": post_id, "username": g.current_user, "name": data.get('name'),
            "content": data.get('content'), "timestamp": current_sa_time(), "expiry": data.get('expiry', None)
        }
        get_db().table("posts").insert(post_data).execute()
        return jsonify({'status': 'success', 'post_id': post_id}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/api/posts/delete', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('driver')
def delete_post():
    try:
        pid = request.json.get('post_id')
        owner_check = get_db().table("posts").select("username").eq("post_id", pid).execute()
        if not owner_check.data or owner_check.data[0].get('username') != g.current_user:
            return jsonify({'status': 'error', 'msg': 'Not authorized to delete this post'}), 403
        get_db().table("posts").delete().eq("post_id", pid).execute()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/api/posts/get_all', methods=['GET', 'POST', 'OPTIONS'])
def get_all_posts():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
    try:
        res = get_db().table("posts").select("*").order("timestamp", desc=True).execute()
        return jsonify({'status': 'success', 'posts': res.data if res.data else []}), 200
    except Exception as e:
        return jsonify({'status': 'success', 'posts': []}), 200


@app.route('/api/support/get_all_counts', methods=['POST', 'OPTIONS'])
@require_auth
def get_support_counts():
    try:
        receiver = g.current_user
        res = get_db().table("direct_support_messages").select("child_name").or_(f"receiver_username.eq.{receiver},sender_username.eq.{receiver}").execute()
        counts = {}
        for m in (res.data if res.data else []):
            if m.get('child_name'):
                room = m['child_name']
                counts[room] = counts.get(room, 0) + 1
        return jsonify({'status': 'success', 'counts': counts}), 200
    except Exception as e:
        return jsonify({'status': 'success', 'counts': {}}), 200


@app.route('/api/support/get_history', methods=['POST', 'OPTIONS'])
@require_auth
def get_support_history():
    try:
        data = request.json or {}
        child = str(data.get('child_username', '')).strip()
        room_type = data.get('room_type', 'triple_party_matrix')

        if not child:
            return jsonify({'status': 'error', 'messages': []}), 400

        if room_type == "general_driver_room":
            room_check = get_db().table("general_driver_rooms").select("driver_username,parent_username").eq("booking_id", child).limit(1).execute()
            if room_check.data and len(room_check.data) > 0:
                room = room_check.data[0]
                driver_u = room["driver_username"]
                parent_u = room["parent_username"]
                if g.current_user not in (driver_u, parent_u):
                    return jsonify({'status': 'error', 'messages': []}), 403
                res = get_db().table("direct_support_messages").select("*").eq("room_id", child).or_(
                    f"and(sender_username.eq.{parent_u},receiver_username.eq.{driver_u}),"
                    f"and(sender_username.eq.{driver_u},receiver_username.eq.{parent_u})"
                ).order("timestamp", desc=False).execute()
                return jsonify({"status": "success", "messages": res.data or []}), 200

        if room_type == "parent_driver_matrix":
            room_check = get_db().table("parent_driver_rooms").select("driver_username, parent_username").eq("child_username", child).limit(1).execute()
            if room_check.data and len(room_check.data) > 0:
                room_info = room_check.data[0]
                driver_u = room_info.get('driver_username')
                parent_u = room_info.get('parent_username')
                if g.current_user not in (driver_u, parent_u):
                    return jsonify({'status': 'error', 'messages': []}), 403
                res = get_db().table("direct_support_messages").select("*").eq("child_name", child).or_(
                    f"and(sender_username.eq.{parent_u},receiver_username.eq.{driver_u}),and(sender_username.eq.{driver_u},receiver_username.eq.{parent_u})"
                ).order("timestamp", desc=False).execute()
                return jsonify({'status': 'success', 'messages': res.data if res.data else []}), 200

        room_check = get_db().table("parent_principal_rooms").select("principal_username, parent_username").eq("child_username", child).limit(1).execute()
        if room_check.data and len(room_check.data) > 0:
            room_info = room_check.data[0]
            principal_u = room_info.get('principal_username')
            parent_u = room_info.get('parent_username')
            if g.current_user not in (principal_u, parent_u):
                return jsonify({'status': 'error', 'messages': []}), 403
            res = get_db().table("direct_support_messages").select("*").eq("child_name", child).or_(
                f"and(sender_username.eq.{parent_u},receiver_username.eq.{principal_u}),and(sender_username.eq.{principal_u},receiver_username.eq.{parent_u})"
            ).order("timestamp", desc=False).execute()
            return jsonify({'status': 'success', 'messages': res.data if res.data else []}), 200

        return jsonify({'status': 'success', 'messages': []}), 200
    except Exception as e:
        print(f"❌ [CHAT LOG FETCH EXCEPTION]: {str(e)}")
        return jsonify({'status': 'success', 'messages': []}), 200


@app.route('/api/principal/get_data', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('principal')
def principal_get_data():
    try:
        data = request.get_json(silent=True) or {}
        sc = str(data.get('school_code', '')).strip().upper()
        if not sc:
            return jsonify({'status': 'error', 'message': 'Missing school code'}), 400

        my_school = get_db().table("schools").select("school_code").eq("principal_username", g.current_user).execute()
        allowed_codes = [s['school_code'] for s in (my_school.data or [])]
        if sc not in allowed_codes:
            return jsonify({'status': 'error', 'message': 'Not authorized for this school code'}), 403

        school_check = get_db().table("schools").select("school_name").eq("school_code", sc).execute()
        if not school_check.data:
            return jsonify({'status': 'error', 'message': 'This School Code does not exist.'}), 404
        s_name = school_check.data[0].get('school_name') or "Unknown School"

        res = get_db().table("users").select("username, name, role, school_code, photo, moderated_grade").eq("school_code", sc).execute()
        users = res.data or []
        drivers = [u for u in users if u.get('role') == 'driver']
        students = [u for u in users if u.get('role') == 'student']

        log_res = get_db().table("principal_transit_logs").select("student_name, status, school_code, timestamp, driver_name").eq("school_code", sc).order("timestamp", desc=True).limit(50).execute()
        logs = log_res.data or []
        combined_logs = [log for log in logs if isinstance(log, dict)]

        parent_rooms_res = get_db().table("parent_principal_rooms").select("parent_username, child_username").eq("school_code", sc).execute()
        parent_rooms = parent_rooms_res.data or []

        return jsonify({'status': 'success', 'school_name': s_name, 'drivers': drivers, 'students': students, 'logs': combined_logs, 'parent_rooms': parent_rooms}), 200
    except Exception as e:
        print("DASHBOARD ERROR:", str(e))
        return jsonify({'status': 'error', 'message': f'Internal server error: {str(e)}'}), 500


@app.route('/api/get_all_school_bookings', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('principal')
def get_all_school_bookings():
    try:
        sc = request.json.get('school_code', '').strip().upper()
        my_school = get_db().table("schools").select("school_code").eq("principal_username", g.current_user).execute()
        allowed_codes = [s['school_code'] for s in (my_school.data or [])]
        if sc not in allowed_codes:
            return jsonify({'status': 'success', 'bookings': []}), 200

        res = get_db().table("bookings").select("*").eq("school_code", sc).execute()
        return jsonify({'status': 'success', 'bookings': res.data if res.data else []}), 200
    except Exception as e:
        return jsonify({'status': 'success', 'bookings': []}), 200


@app.route('/api/principal/school_out', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('principal')
def school_out():
    try:
        data = request.json
        sc = data.get('school_code')

        my_school = get_db().table("schools").select("school_code, school_name").eq("principal_username", g.current_user).execute()
        allowed = {s['school_code']: s.get('school_name') for s in (my_school.data or [])}
        if sc not in allowed:
            return jsonify({'status': 'error', 'msg': 'Not authorized for this school'}), 403

        school_name = allowed.get(sc) or sc
        broadcast_msg = f"⚠️ URGENT ALERT: Students at {school_name} have been officially released. Please proceed to the pickup point immediately!"
        timestamp = current_sa_time()

        rooms = get_db().table("parent_principal_rooms").select("parent_username, child_username").eq("school_code", sc).execute()
        for room in (rooms.data or []):
            get_db().table("direct_support_messages").insert({
                "sender_username": "PRINCIPAL", "receiver_username": room["parent_username"],
                "child_name": room["child_username"], "school_name": school_name, "school_code": sc,
                "message": broadcast_msg, "is_alarm": True, "timestamp": timestamp
            }).execute()

            student = get_db().table("users").select("name").eq("username", room["child_username"]).limit(1).execute()
            student_name = student.data[0]["name"] if student.data else room["child_username"]

            existing = get_db().table("safety_logs").select("id").eq("student_name", student_name).eq("school_code", sc).eq("status", "SCHOOL DISMISSED - URGENT").limit(1).execute()
            if not existing.data:
                get_db().table("safety_logs").insert({
                    "student_name": student_name, "driver_name": "PRINCIPAL (SYSTEM)", "school_code": sc,
                    "status": "SCHOOL DISMISSED - URGENT", "timestamp": timestamp, "log_type": "transport",
                    "additional_info": "Mass dismissal broadcast initiated by Principal."
                }).execute()

        driver_res = get_db().table("users").select("username, school_codes").eq("role", "driver").execute()
        for d in (driver_res.data or []):
            codes = d.get("school_codes") or []
            if sc in codes:
                get_db().table("direct_support_messages").insert({
                    "sender_username": "PRINCIPAL", "receiver_username": d["username"],
                    "child_name": sc, "school_name": school_name, "school_code": sc,
                    "message": broadcast_msg, "is_alarm": True, "timestamp": timestamp
                }).execute()

        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500

@app.route('/api/driver/get_alarms', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('driver')
def get_driver_alarms():
    try:
        driver_u = g.current_user
        user_res = get_db().table("users").select("school_codes").eq("username", driver_u).execute()
        if not user_res.data:
            return jsonify({'status': 'error', 'message': 'Driver not found'}), 404
        school_codes = user_res.data[0].get('school_codes', [])
        alarms = get_db().table("direct_support_messages").select("*").eq("is_alarm", True).in_("child_name", school_codes).order("timestamp", desc=True).limit(10).execute()
        return jsonify({'status': 'success', 'alarms': alarms.data}), 200
    except Exception as e:
        print(f"❌ Driver Alarm fetch error: {e}")
        return jsonify({'status': 'error', 'alarms': []}), 500


@app.route('/api/support/get_all_alarms', methods=['POST', 'OPTIONS'])
@require_auth
def get_all_alarms():
    try:
        receiver_u = g.current_user
        alarms = get_db().table("direct_support_messages").select("*").eq("receiver_username", receiver_u).eq("is_alarm", True).order("timestamp", desc=True).execute()
        return jsonify({'status': 'success', 'alarms': alarms.data if alarms.data else []}), 200
    except Exception as e:
        print(f"❌ Alarm fetch error: {e}")
        return jsonify({'status': 'error', 'alarms': []}), 500


@app.route('/api/support/send_msg', methods=['POST', 'OPTIONS'])
@require_auth
def send_support_msg():
    try:
        data = request.json
        sender = g.current_user
        receiver = str(data.get('receiver', ''))
        child = str(data.get('child_name', ''))
        room_id = str(data.get("room_id", ""))
        room_type = data.get('room_type', '')
        school_name = str(data.get('school_name', ''))
        school_code = str(data.get('school_code', '')).strip().upper()
        msg_payload = data.get('message')

        lookup_key = room_id or child

        if not receiver and room_type == "general_driver_room" and lookup_key:
            room = get_db().table("general_driver_rooms") \
                .select("driver_username, parent_username") \
                .eq("booking_id", lookup_key).limit(1).execute()
            if room.data:
                driver_u = room.data[0].get("driver_username")
                parent_u = room.data[0].get("parent_username")
                if sender not in (driver_u, parent_u):
                    return jsonify({'status': 'error', 'msg': 'Not part of this conversation'}), 403
                receiver = parent_u if sender == driver_u else driver_u
                child = lookup_key

        elif not receiver and room_type == "parent_driver_matrix" and lookup_key:
            room = get_db().table("parent_driver_rooms") \
                .select("driver_username, parent_username") \
                .eq("child_username", lookup_key).limit(1).execute()
            if room.data:
                driver_u = room.data[0].get("driver_username")
                parent_u = room.data[0].get("parent_username")
                if sender not in (driver_u, parent_u):
                    return jsonify({'status': 'error', 'msg': 'Not part of this conversation'}), 403
                receiver = parent_u if sender == driver_u else driver_u
                child = lookup_key

        elif not receiver and room_type == "triple_party_matrix" and lookup_key:
            room = get_db().table("parent_principal_rooms") \
                .select("principal_username, parent_username") \
                .eq("child_username", lookup_key).limit(1).execute()
            if room.data:
                principal_u = room.data[0].get("principal_username")
                parent_u = room.data[0].get("parent_username")
                if sender not in (principal_u, parent_u):
                    return jsonify({'status': 'error', 'msg': 'Not part of this conversation'}), 403
                receiver = parent_u if sender == principal_u else principal_u
                child = lookup_key

        if not receiver:
            return jsonify({'status': 'error', 'msg': 'Could not resolve message recipient'}), 400

        is_alarm = data.get('is_alarm', False)
        if is_alarm and g.current_role != 'principal':
            is_alarm = False

        chat_entry = {
            "room_id": room_id, "sender_username": sender, "receiver_username": receiver,
            "child_name": child, "school_name": school_name, "school_code": school_code,
            "message": msg_payload, "is_alarm": is_alarm, "timestamp": current_sa_time()
        }
        get_db().table("direct_support_messages").insert(chat_entry).execute()

        if is_alarm:
            get_db().table("safety_logs").insert({
                "student_name": child, "driver_name": sender, "school_code": school_code,
                "status": "SCHOOL DISMISSED", "timestamp": current_sa_time(),
                "log_type": "SCHOOL OUT ALERT", "additional_info": msg_payload
            }).execute()

        return jsonify({'status': 'success'}), 200
    except Exception as e:
        print(f"[CRITICAL CHAT DROP RECOVERED] Insertion blocked: {e}")
        return jsonify({'status': 'error', 'msg': 'Communication channel delay.'}), 200
    
@app.route('/api/parent/request_link', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('general')
def request_parent_link():
    try:
        data = request.json
        sc = data.get('school_code', '').upper()
        cu = data.get('child_username', '').lower()
        res = get_db().table("users").select("*").eq("username", cu).eq("school_code", sc).execute()
        if not res.data:
            return jsonify({'status': 'error', 'message': 'Verification Check: Student data not found in records.'}), 404

        student = res.data[0]
        otp = str(random.randint(1111, 9999))
        pending_parent_links[cu] = {"v_code": otp, "child_data": student, "requested_by": g.current_user}

        email_body = f"""
        <p style="font-size: 18px;">A Guardian Tracking request has been initiated.</p>
        <div style="background: #161b22; padding: 20px; border-radius: 10px; margin: 20px 0; border-left: 4px solid #10b981;">
            <p style="margin: 5px 0;">Student Linked: <b style="color: #ffffff;">{student['name']}</b></p>
            <p style="margin: 5px 0;">Security Pin: <b style="font-size: 24px; color: #10b981; letter-spacing: 4px;">{otp}</b></p>
        </div>
        <p>Enter this pin in your Safe Drive app to confirm parental access.</p>
        """
        send_mail(student['parent_email'], "Safe Drive Link", generate_email_wrapper(email_body, "Parent Verification"))
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        print(f"[NETWORK LINK CRASH RECOVERED] Parent request line failure: {e}")
        return jsonify({'status': 'error', 'msg': 'Database connection drop. Please retry execution.'}), 200


@app.route('/api/parent/verify_link', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('general')
def verify_parent_link():
    try:
        data = request.json
        cu = data.get('child_username', '').lower()
        code = str(data.get('code', '')).strip()

        pending = pending_parent_links.get(cu)
        if not (pending and str(pending['v_code']) == code and pending.get('requested_by') == g.current_user):
            return jsonify({'status': 'error', 'message': 'Invalid security authorization credentials.'}), 401

        child_info = pending['child_data']
        del pending_parent_links[cu]

        parent_u = g.current_user
        school_code = child_info.get('school_code')

        # Create parent <-> principal room (if not already linked)
        school_res = get_db().table("schools").select("principal_username").eq("school_code", school_code).limit(1).execute()
        if school_res.data:
            principal_u = school_res.data[0].get('principal_username')
            if principal_u:
                existing = get_db().table("parent_principal_rooms") \
                    .select("id") \
                    .eq("child_username", cu).eq("parent_username", parent_u).eq("principal_username", principal_u) \
                    .execute()
                if not existing.data:
                    get_db().table("parent_principal_rooms").insert({
                        "child_name": child_info.get('name'),
                        "child_username": cu,
                        "parent_username": parent_u,
                        "principal_username": principal_u,
                        "school_code": school_code
                    }).execute()

        # Late-link parent <-> driver rooms for existing accepted school bookings
        bookings_res = get_db().table("bookings").select("*").eq("student_username", cu).execute()
        for booking in (bookings_res.data or []):
            if booking.get('school_code') == "D2D_GLOBAL":
                continue
            if booking.get('status') != "Accepted":
                continue
            driver_u = booking.get('driver_username')
            existing_room = get_db().table("parent_driver_rooms") \
                .select("id") \
                .eq("child_username", cu).eq("parent_username", parent_u).eq("driver_username", driver_u) \
                .execute()
            if not existing_room.data:
                get_db().table("parent_driver_rooms").insert({
                    "child_name": child_info.get('name'),
                    "child_username": cu,
                    "parent_username": parent_u,
                    "driver_username": driver_u,
                    "school_code": booking.get('school_code')
                }).execute()

        return jsonify({'status': 'success', 'child_data': child_info}), 200
    except Exception as e:
        print(f"❌ [VERIFY LINK EXCEPTION]: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/parent/remove_monitor', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('general')
def remove_monitor():
    try:
        child = request.json.get('child_username', '')
        get_db().table('parent_principal_rooms').delete().eq('child_username', child).eq('parent_username', g.current_user).execute()
        get_db().table('parent_driver_rooms').delete().eq('child_username', child).eq('parent_username', g.current_user).execute()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/api/parent/get_child_room', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('general')
def get_child_room():
    try:
        data = request.json or {}
        child = data.get('child_username', '')
        room_type = data.get('room_type', '')  # 'principal' or 'driver'

        if room_type == 'principal':
            table, field = 'parent_principal_rooms', 'principal_username'
        else:
            table, field = 'parent_driver_rooms', 'driver_username'

        res = get_db().table(table).select(field) \
            .eq('child_username', child).eq('parent_username', g.current_user) \
            .limit(1).execute()

        if not res.data:
            return jsonify({'status': 'success', 'room': None}), 200
        return jsonify({'status': 'success', 'room': {'partner_username': res.data[0].get(field)}}), 200
    except Exception as e:
        return jsonify({'status': 'success', 'room': None}), 200


@app.route('/api/attendance/get_for_student', methods=['POST', 'OPTIONS'])
@require_auth
def get_attendance_for_student():
    try:
        data = request.json or {}
        student_u = data.get('student_username', '').strip()

        if g.current_role == 'student':
            if student_u.lower() != g.current_user.lower():
                return jsonify({'status': 'error', 'logs': []}), 403
        elif g.current_role == 'general':
            link_check = get_db().table("parent_principal_rooms") \
                .select("child_username") \
                .eq("parent_username", g.current_user).eq("child_username", student_u) \
                .execute()
            if not link_check.data:
                return jsonify({'status': 'error', 'logs': []}), 403
        elif g.current_role not in ('principal', 'moderator'):
            return jsonify({'status': 'error', 'logs': []}), 403

        res = get_db().table("attendance_logs").select("*").eq("student_username", student_u).order("timestamp", desc=True).execute()
        return jsonify({'status': 'success', 'logs': res.data or []}), 200
    except Exception as e:
        return jsonify({'status': 'success', 'logs': []}), 200



@app.route('/api/verify_license', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('driver')
def verify_license():
    data = request.json
    username = g.current_user
    img_b64 = data.get('license_image')

    try:
        license_url = None
        if img_b64:
            if "," in img_b64:
                img_b64 = img_b64.split(",", 1)[1]
            img_data = base64.b64decode(img_b64)
            file_name = f"license_{username}_{int(time.time())}.jpg"
            get_db().storage.from_("profile pictures").upload(file=img_data, path=file_name, file_options={"content-type": "image/jpeg"})
            license_url = get_db().storage.from_("profile pictures").get_public_url(file_name)

        get_db().table("users").update({"license_image": license_url}).eq("username", username).execute()
        return jsonify({'status': 'success', 'msg': 'License verified and securely vaulted.'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/api/search_drivers', methods=['POST', 'OPTIONS'])
@require_auth
def search_drivers():
    data = request.json
    sc = data.get('school_code', '').strip().upper()
    try:
        if sc == "ALL_SYSTEM_DRIVERS":
            res = get_db().table("users").select("name, username, school_code, driver_mode, vehicle_type, email").eq("role", "driver").execute()
        else:
            res = get_db().table("users").select("name, username, school_code, driver_mode, vehicle_type, email").eq("role", "driver").eq("school_code", sc).execute()
        return jsonify({'status': 'success', 'drivers': res.data if res.data else []}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/api/moderator/log_attendance', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('moderator')
def log_attendance():
    try:
        data = request.json
        s_u = data.get('student_username')
        s_n = data.get('student_name')
        mod_u = g.current_user
        grade = str(data.get('grade')).strip()
        status = data.get('status', '').upper().strip()
        sc = data.get('school_code', '').upper().strip()

        log_payload = {"student_username": s_u, "student_name": s_n, "moderator_username": mod_u, "grade": grade, "status": status, "timestamp": current_sa_time()}
        get_db().table("attendance_logs").insert(log_payload).execute()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': f'Attendance ledger push aborted: {str(e)}'}), 500


@app.route('/api/delete_account', methods=['POST', 'OPTIONS'])
@require_auth
def delete_account():
    try:
        username = g.current_user
        print(f"⚠️ PURGE INITIALIZED: Attempting database wipe for user -> [{username}]")

        user_check = get_db().table("users").select("role").eq("username", username).execute()
        if not user_check.data:
            return jsonify({'status': 'error', 'message': 'Account purge failed: profile does not exist.'}), 404
        user_role = user_check.data[0].get('role')

        if user_role == 'driver':
            get_db().table("parent_driver_rooms").delete().eq("driver_username", username).execute()
            get_db().table("posts").delete().eq("username", username).execute()
        elif user_role == 'general':
            get_db().table("parent_driver_rooms").delete().eq("parent_username", username).execute()
            get_db().table("parent_principal_rooms").delete().eq("parent_username", username).execute()
        elif user_role == 'student':
            get_db().table("bookings").delete().eq("student_username", username).execute()
            get_db().table("attendance_logs").delete().eq("student_username", username).execute()
            get_db().table("parent_driver_rooms").delete().eq("child_username", username).execute()
            get_db().table("parent_principal_rooms").delete().eq("child_username", username).execute()

        get_db().table("users").delete().eq("username", username).execute()
        print(f"✅ PURGE COMPLETED: [{username}] removed.")
        return jsonify({'status': 'success', 'message': 'Profile data clusters successfully wiped.'}), 200
    except Exception as e:
        print(f"❌ [CRITICAL PURGE EXCEPTION]: {str(e)}")
        return jsonify({'status': 'error', 'message': f'Cloud database engine transaction failed: {str(e)}'}), 500


# =====================================================================
# NEW: routes replacing direct frontend _supabase.from('users') calls.
# These let the frontend stop touching the `users` table directly with
# the public key, so `users` can eventually be locked down in RLS too.
# =====================================================================

@app.route('/api/profile/get_self', methods=['POST', 'OPTIONS'])
@require_auth
def get_self_profile():
    """Returns the logged-in user's own full row. Replaces every
    `_supabase.from('users').select('*').eq('username', myU)` pattern
    used across dashboards for init checks (photo, grade, license, etc.)."""
    try:
        res = get_db().table("users").select("*").eq("username", g.current_user).execute()
        if not res.data:
            return jsonify({'status': 'error', 'message': 'Profile not found'}), 404
        return jsonify({'status': 'success', 'profile': res.data[0]}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


# Only these fields can ever be changed via update_self — nothing else
# (role, school_code, username, password, etc. are NOT in this list,
# so this endpoint can never be used to escalate privileges or hijack
# another account's identity, even by a malicious request).
ALLOWED_SELF_UPDATE_FIELDS = {
    'photo', 'town', 'location', 'current_town', 'grade',
    'transport_type', 'driver_mode', 'moderated_grade', 'school_codes'
}

@app.route('/api/profile/update_self', methods=['POST', 'OPTIONS'])
@require_auth
def update_self_profile():
    """Updates only the caller's own row, and only whitelisted fields.
    Replaces every `_supabase.from('users').update({...}).eq('username', myU)`
    pattern (profile photo, grade, driver location, school codes, etc.)."""
    try:
        data = request.json or {}
        updates = {k: v for k, v in data.items() if k in ALLOWED_SELF_UPDATE_FIELDS}
        if not updates:
            return jsonify({'status': 'error', 'message': 'No valid fields to update'}), 400
        get_db().table("users").update(updates).eq("username", g.current_user).execute()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/api/profile/upload_photo', methods=['POST', 'OPTIONS'])
@require_auth
def upload_profile_photo():
    try:
        if 'photo' not in request.files:
            return jsonify({'status': 'error', 'message': 'No file provided'}), 400

        file = request.files['photo']
        username = g.current_user

        old_res = get_db().table("users").select("photo").eq("username", username).execute()
        old_photo = old_res.data[0].get('photo') if old_res.data else None
        if old_photo and 'supabase.co' in old_photo:
            try:
                old_filename = old_photo.split('/')[-1]
                get_db().storage.from_("profile pictures").remove([old_filename])
            except Exception as cleanup_err:
                print(f"Old photo cleanup warning: {cleanup_err}")

        file_ext = file.filename.rsplit('.', 1)[-1] if '.' in file.filename else 'jpg'
        file_bytes = file.read()
        new_filename = f"{username}_{int(time.time())}.{file_ext}"

        get_db().storage.from_("profile pictures").upload(
            path=new_filename,
            file=file_bytes,
            file_options={"content-type": file.content_type or "image/jpeg"}
        )
        public_url = get_db().storage.from_("profile pictures").get_public_url(new_filename)

        get_db().table("users").update({"photo": public_url}).eq("username", username).execute()

        return jsonify({'status': 'success', 'photo_url': public_url}), 200
    except Exception as e:
        print(f"Photo upload error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/profile/delete_photo', methods=['POST', 'OPTIONS'])
@require_auth
def delete_profile_photo():
    try:
        username = g.current_user
        old_res = get_db().table("users").select("photo").eq("username", username).execute()
        old_photo = old_res.data[0].get('photo') if old_res.data else None

        if old_photo and 'supabase.co' in old_photo:
            try:
                old_filename = old_photo.split('/')[-1]
                get_db().storage.from_("profile pictures").remove([old_filename])
            except Exception as cleanup_err:
                print(f"Photo cleanup warning: {cleanup_err}")

        get_db().table("users").update({"photo": None}).eq("username", username).execute()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/history/clear', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('principal')
def clear_history():
    try:
        sc = request.json.get('school_code', '').strip().upper()
        my_school = get_db().table("schools").select("school_code").eq("principal_username", g.current_user).execute()
        allowed_codes = [s['school_code'] for s in (my_school.data or [])]
        if sc not in allowed_codes:
            return jsonify({'status': 'error', 'msg': 'Not authorized for this school'}), 403
        get_db().table("safety_logs").delete().eq("school_code", sc).execute()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/api/history/delete_single', methods=['POST', 'OPTIONS'])
@require_auth
@require_role('principal')
def delete_single_log():
    try:
        log_id = request.json.get('log_id')
        log_check = get_db().table("safety_logs").select("school_code").eq("id", log_id).execute()
        if not log_check.data:
            return jsonify({'status': 'error', 'msg': 'Log not found'}), 404
        my_school = get_db().table("schools").select("school_code").eq("principal_username", g.current_user).execute()
        allowed_codes = [s['school_code'] for s in (my_school.data or [])]
        if log_check.data[0].get('school_code') not in allowed_codes:
            return jsonify({'status': 'error', 'msg': 'Not authorized for this log'}), 403
        get_db().table("safety_logs").delete().eq("id", log_id).execute()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500

    
@app.route('/api/users/lookup', methods=['POST', 'OPTIONS'])
@require_auth
def users_lookup():
    """Public-safe user lookups (name/photo/role only — never email,
    phone, license images, etc. unless the caller is a moderator or
    principal, who legitimately need student emails for their roster).
    Replaces direct `_supabase.from('users').select('*')...` reads used
    for driver lists, student rosters, and chat-partner profile photos."""
    try:
        data = request.json or {}
        usernames = data.get('usernames')
        role = data.get('role')
        school_code = data.get('school_code')
        grade = data.get('grade')
        moderated_grade = data.get('moderated_grade')

        if role in ('student', 'moderator') and school_code:
            if g.current_role not in ('principal', 'moderator'):
                my_code = get_db().table("users").select("school_code").eq("username", g.current_user).execute()
                caller_code = (my_code.data[0].get('school_code') if my_code.data else None)
                if not caller_code or caller_code.upper() != str(school_code).upper():
                    return jsonify({'status': 'error', 'msg': 'Not authorized for this school roster'}), 403

        select_fields = "username, name, photo, role, school_code, school_codes, moderated_grade, grade, driver_mode, transport_type, vehicle_type, town, location, current_town"
        if g.current_role in ('moderator', 'principal'):
            select_fields += ", email"

        query = get_db().table("users").select(select_fields)
        if usernames:
            query = query.in_("username", usernames)
        if role:
            query = query.eq("role", role)
        if school_code:
            query = query.eq("school_code", str(school_code).upper())
        if grade:
            query = query.eq("grade", grade)
        if moderated_grade:
            query = query.eq("moderated_grade", moderated_grade)

        res = query.execute()
        return jsonify({'status': 'success', 'users': res.data or []}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting Safe Drive backend on port {port}...")
    serve(app, host='0.0.0.0', port=port, threads=8)
