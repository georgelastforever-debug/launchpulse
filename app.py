#!/usr/bin/env python3
"""
LaunchPulse — Product Launch Command Center
BUILD: workspace-sheet-fix-2026-08-15

Supabase Auth + Database · Sleek orange UI
"""

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify
)
try:
    from flask_wtf.csrf import CSRFProtect
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "flask-wtf", "-q"])
    from flask_wtf.csrf import CSRFProtect
from datetime import datetime, date, timedelta
from functools import wraps
import os
import sys
import re

sys.path.insert(0, os.path.expanduser("~/.local/lib/python3.12/site-packages"))

try:
    from dateutil.relativedelta import relativedelta
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "python-dateutil", "-q"])
    from dateutil.relativedelta import relativedelta

try:
    from supabase import create_client, Client
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "supabase", "-q"])
    from supabase import create_client, Client

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_dotenv()

app = Flask(__name__)

@app.errorhandler(500)
def internal_error(e):
    import traceback
    traceback.print_exc()
    err = traceback.format_exc()
    print("INTERNAL_500:", err[-2000:])
    # Show short hint in HTML for debugging (no secrets)
    msg = str(getattr(e, "original_exception", e) or e)
    return (
        "<!doctype html><html><body style='font-family:system-ui;padding:2rem'>"
        "<h1>Something went wrong</h1>"
        f"<p style='color:#b91c1c'><code>{msg[:300]}</code></p>"
        "<p><a href='/dashboard'>Back to Home</a> · <a href='/login'>Login</a></p>"
        "<p style='color:#64748b;font-size:12px'>Check Render logs for full traceback.</p>"
        "</body></html>",
        500,
    )

_flask_secret = (os.environ.get("FLASK_SECRET") or os.environ.get("SECRET_KEY") or "").strip()
if not _flask_secret:
    _flask_secret = "dev-only-insecure-change-me"
    print("WARNING: FLASK_SECRET is not set. Set a long random value in production.")
app.secret_key = _flask_secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Secure cookies on HTTPS (Render). Set SESSION_COOKIE_SECURE=false for local http if needed.
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "true").lower()
    in ("1", "true", "yes"),
)

csrf = CSRFProtect(app)

# ---------------------------------------------------------------------------
# Login rate limiting (in-memory; per Render instance)
# ---------------------------------------------------------------------------
from collections import defaultdict
import time as _time

_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SEC = 15 * 60  # 15 minutes
_login_attempts = defaultdict(list)  # key -> list of failure timestamps


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _rate_key(email=""):
    ip = _client_ip()
    em = (email or "").strip().lower()
    return f"{ip}|{em}"


def is_login_rate_limited(email=""):
    key = _rate_key(email)
    now = _time.time()
    # drop old failures
    _login_attempts[key] = [t for t in _login_attempts[key] if now - t < _LOGIN_WINDOW_SEC]
    return len(_login_attempts[key]) >= _LOGIN_MAX_ATTEMPTS


def record_login_failure(email=""):
    key = _rate_key(email)
    now = _time.time()
    _login_attempts[key] = [t for t in _login_attempts[key] if now - t < _LOGIN_WINDOW_SEC]
    _login_attempts[key].append(now)


def clear_login_failures(email=""):
    key = _rate_key(email)
    _login_attempts.pop(key, None)
    # also clear IP-only style keys for this IP
    ip = _client_ip()
    for k in list(_login_attempts.keys()):
        if k.startswith(ip + "|"):
            _login_attempts.pop(k, None)


def login_rate_limit_message():
    return (
        f"Too many failed login attempts. "
        f"Try again in {_LOGIN_WINDOW_SEC // 60} minutes."
    )



SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
# Service role bypasses RLS — ONLY for admin user creation
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
SOLDSCOPE_API_TOKEN = os.environ.get("SOLDSCOPE_API_TOKEN", "").strip()
SOLDSCOPE_API_BASE = os.environ.get("SOLDSCOPE_API_BASE", "https://www.soldscope.com/api").rstrip("/")

# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def get_supabase(use_service=False):
    """
    use_service=True  → service role (bypasses RLS). Admin create-user only.
    use_service=False → anon key + user session. RLS applies.
    """
    if not SUPABASE_URL:
        return None
    key = SUPABASE_SERVICE_KEY if (use_service and SUPABASE_SERVICE_KEY) else SUPABASE_KEY
    if not key:
        return None
    client = create_client(SUPABASE_URL, key)
    if not use_service:
        access = session.get("access_token")
        refresh = session.get("refresh_token")
        if access and refresh:
            try:
                client.auth.set_session(access, refresh)
            except Exception:
                pass
    return client


def get_db():
    """
    Server-side data access for the shared team CRM.
    Prefer service role so products always load (RLS was blocking empty lists
    when the user JWT was missing/expired on the anon client).
    Falls back to anon + session if service key is not configured.
    """
    if SUPABASE_SERVICE_KEY:
        return get_supabase(use_service=True)
    return get_supabase(use_service=False)


def get_admin_client():
    """Service role client — admin operations only (create user)."""
    return get_supabase(use_service=True)


def supabase_configured():
    return bool(SUPABASE_URL and SUPABASE_KEY)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        if session.get("user", {}).get("role") != "admin":
            flash("Admin access required.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()


def ensure_profile(user_id, email):
    """Create/update profile; promote ADMIN_EMAIL or first user to admin."""
    sb = get_db()
    if not sb or not user_id:
        return "user"
    email = (email or "").strip().lower()
    role = "user"
    try:
        existing = sb.table("profiles").select("*").eq("id", user_id).execute()
        if existing.data:
            role = existing.data[0].get("role") or "user"
            # Promote if ADMIN_EMAIL matches
            if ADMIN_EMAIL and email == ADMIN_EMAIL and role != "admin":
                sb.table("profiles").update({"role": "admin", "email": email}).eq("id", user_id).execute()
                role = "admin"
            return role

        # First profile ever, or matching ADMIN_EMAIL → admin
        all_profiles = sb.table("profiles").select("id", count="exact").execute()
        count = all_profiles.count if all_profiles.count is not None else len(all_profiles.data or [])
        if count == 0 or (ADMIN_EMAIL and email == ADMIN_EMAIL):
            role = "admin"
        sb.table("profiles").insert({
            "id": user_id,
            "email": email,
            "role": role,
        }).execute()
        return role
    except Exception as e:
        print("ensure_profile error:", e)
        # Fallback: env admin email
        if ADMIN_EMAIL and email == ADMIN_EMAIL:
            return "admin"
        return "user"



def get_profile(user_id):
    sb = get_db()
    if not sb or not user_id:
        return None
    try:
        return sb.table("profiles").select("*").eq("id", user_id).single().execute().data
    except Exception:
        return None


def set_profile_totp(user_id, secret, enabled=False):
    sb = get_db()
    if not sb:
        raise RuntimeError("Database not configured")
    # Ensure profile row exists
    ensure_profile(user_id, (session.get("user") or {}).get("email") or "")
    res = sb.table("profiles").update({
        "totp_secret": secret or "",
        "totp_enabled": bool(enabled),
    }).eq("id", user_id).execute()
    if not res.data:
        # Insert if update matched nothing
        sb.table("profiles").upsert({
            "id": user_id,
            "email": ((session.get("user") or {}).get("email") or "").lower(),
            "role": (session.get("user") or {}).get("role") or "user",
            "totp_secret": secret or "",
            "totp_enabled": bool(enabled),
        }).execute()
    return True


def verify_totp(secret, code):
    if not secret or not code:
        return False
    code = str(code).strip().replace(" ", "").replace("-", "")
    secret = str(secret).strip().replace(" ", "")
    try:
        import pyotp
        totp = pyotp.TOTP(secret)
        # valid_window=2 tolerates ~60s clock drift
        if totp.verify(code, valid_window=2):
            return True
        # Fallback: compare current code directly
        return totp.now() == code
    except Exception as e:
        print("verify_totp error:", e)
        return False


def list_profiles():
    sb = get_db()
    if not sb:
        return []
    try:
        res = sb.table("profiles").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception as e:
        print("list_profiles error:", e)
        return []


def create_user_account(email, password, role="user"):
    """Admin creates a normal user via Supabase Auth admin API."""
    admin = get_admin_client()
    if not admin:
        raise RuntimeError("SUPABASE_SERVICE_KEY required to create users")
    result = admin.auth.admin.create_user({
        "email": email,
        "password": password,
        "email_confirm": True,
    })
    user = result.user
    if not user:
        raise RuntimeError("User creation failed")
    sb = get_db()
    if sb:
        try:
            sb.table("profiles").upsert({
                "id": user.id,
                "email": email.strip().lower(),
                "role": role if role in ("admin", "user") else "user",
            }).execute()
        except Exception as e:
            print("profile upsert error:", e)
    return user


# ---------------------------------------------------------------------------
# Phase engine
# ---------------------------------------------------------------------------

PHASE_INFO = {
    1: {
        "name": "Buy Process",
        "short": "Buy",
        "color": "#FF6B00",
        "icon": "🛒",
        "desc": "Initial acquisition, inventory setup, supplier onboarding, and foundational campaigns.",
        "tasks": [
            "Secure inventory & logistics",
            "Set up product listings & creatives",
            "Launch initial ad tests",
            "Onboard support team",
        ],
    },
    2: {
        "name": "Review Process",
        "short": "Review",
        "color": "#E85D04",
        "icon": "🔍",
        "desc": "Performance analysis, customer feedback loops, creative optimization, and budget reallocation.",
        "tasks": [
            "Analyze conversion & ROAS data",
            "Collect & action customer reviews",
            "A/B test creatives & landing pages",
            "Refine targeting & offers",
        ],
    },
    3: {
        "name": "Pushing Velocity",
        "short": "Push",
        "color": "#DC2F02",
        "icon": "🚀",
        "desc": "Scale winning channels, maximize sales velocity, expand reach, and lock in momentum.",
        "tasks": [
            "Scale top-performing campaigns",
            "Launch influencer / affiliate pushes",
            "Expand to new traffic sources",
            "Optimize fulfillment for volume",
        ],
    },
    4: {
        "name": "Upkeep",
        "short": "Upkeep",
        "color": "#9A3412",
        "icon": "🔧",
        "desc": "Ongoing maintenance at a lighter intensity — keep listings healthy, refresh creatives, monitor reviews, and sustain baseline velocity.",
        "tasks": [
            "Weekly performance pulse-check",
            "Refresh top creatives monthly",
            "Respond to new reviews within 48h",
            "Maintain baseline ad spend",
            "Restock alerts & supplier check-ins",
        ],
    },
}



# Pre-launch schedule (before launch_date). default windows are days-before-launch.
PRE_LAUNCH_STEPS = [
    {
        "key": "design_assets",
        "name": "Design & Assets",
        "short": "Design",
        "color": "#0284C7",
        "days_before_start": 90,
        "days_before_end": 70,
        "desc": "Packaging, labels, A+ images, brand assets",
    },
    {
        "key": "print_production",
        "name": "Print Production",
        "short": "Print",
        "color": "#7C3AED",
        "days_before_start": 75,
        "days_before_end": 50,
        "desc": "Print runs, packaging manufacturing",
    },
    {
        "key": "parallel_task",
        "name": "Parallel Task",
        "short": "Parallel",
        "color": "#DB2777",
        "days_before_start": 70,
        "days_before_end": 40,
        "desc": "Concurrent workstreams running alongside production",
    },
    {
        "key": "create_listing",
        "name": "Create Listing",
        "short": "Listing",
        "color": "#EA580C",
        "days_before_start": 55,
        "days_before_end": 35,
        "desc": "Title, bullets, backend terms, images uploaded",
    },
    {
        "key": "compliance",
        "name": "Compliance",
        "short": "Compliance",
        "color": "#CA8A04",
        "days_before_start": 45,
        "days_before_end": 25,
        "desc": "Claims review, certificates, marketplace requirements",
    },
    {
        "key": "sample_testing",
        "name": "Sample Testing",
        "short": "Samples",
        "color": "#16A34A",
        "days_before_start": 40,
        "days_before_end": 20,
        "desc": "Physical samples, QC, photo samples",
    },
    {
        "key": "inventory_shipment",
        "name": "Inventory Shipment",
        "short": "Ship",
        "color": "#0F766E",
        "days_before_start": 30,
        "days_before_end": 5,
        "desc": "Freight to FBA / 3PL, check-in ready for launch",
    },
]


def normalize_prelaunch(raw):
    """{step_key: {status, note}} status in todo|doing|done"""
    out = {}
    if not isinstance(raw, dict):
        return out
    for step in PRE_LAUNCH_STEPS:
        k = step["key"]
        item = raw.get(k) or {}
        if not isinstance(item, dict):
            item = {}
        st = (item.get("status") or "todo").lower()
        if st not in ("todo", "doing", "done"):
            st = "todo"
        out[k] = {"status": st, "note": (item.get("note") or "")[:500]}
    return out


def prelaunch_schedule_for_product(p, today=None):
    """Build step rows with date windows relative to launch_date + stored status."""
    if today is None:
        today = date.today()
    try:
        launch = datetime.strptime(str(p.get("launch_date") or "")[:10], "%Y-%m-%d").date()
    except Exception:
        launch = today
    stored = normalize_prelaunch(p.get("prelaunch") or {})
    steps = []
    for step in PRE_LAUNCH_STEPS:
        start = launch - timedelta(days=step["days_before_start"])
        end = launch - timedelta(days=step["days_before_end"])
        if end < start:
            start, end = end, start
        st = stored.get(step["key"], {}).get("status", "todo")
        # auto hint if not manually done
        if st != "done":
            if today < start:
                auto = "upcoming"
            elif start <= today <= end:
                auto = "active"
            else:
                auto = "overdue" if st != "done" else "done"
        else:
            auto = "done"
        steps.append({
            **step,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "start_fmt": start.strftime("%b %d"),
            "end_fmt": end.strftime("%b %d"),
            "status": st,
            "auto": auto,
            "note": stored.get(step["key"], {}).get("note") or "",
        })
    return steps



def get_phase(launch_date_str, today=None):
    """90-day cycle from the exact launch date: three 30-day phases, then Upkeep."""
    if today is None:
        today = date.today()
    try:
        launch_date_str = str(launch_date_str or "")[:10]
        launch = datetime.strptime(launch_date_str, "%Y-%m-%d").date()
    except Exception:
        return {
            "phase": 0, "status": "upcoming", "label": "No launch date", "progress": 0,
            "days_left": None, "start": None, "end": None,
        }

    # True day-based windows from the launch date (not calendar months)
    phase1_end = launch + timedelta(days=30)
    phase2_end = launch + timedelta(days=60)
    phase3_end = launch + timedelta(days=90)

    if today < launch:
        return {
            "phase": 0, "status": "upcoming", "label": "Upcoming", "progress": 0,
            "days_left": (launch - today).days,
            "start": None, "end": None,
        }
    if today < phase1_end:
        days_in = (today - launch).days
        return {
            "phase": 1, "status": "active", "label": PHASE_INFO[1]["name"],
            "progress": min(100, int(days_in / 30 * 100)),
            "days_left": max(0, (phase1_end - today).days),
            "start": launch.isoformat(),
            "end": (phase1_end - timedelta(days=1)).isoformat(),
        }
    if today < phase2_end:
        days_in = (today - phase1_end).days
        return {
            "phase": 2, "status": "active", "label": PHASE_INFO[2]["name"],
            "progress": min(100, int(days_in / 30 * 100)),
            "days_left": max(0, (phase2_end - today).days),
            "start": phase1_end.isoformat(),
            "end": (phase2_end - timedelta(days=1)).isoformat(),
        }
    if today < phase3_end:
        days_in = (today - phase2_end).days
        return {
            "phase": 3, "status": "active", "label": PHASE_INFO[3]["name"],
            "progress": min(100, int(days_in / 30 * 100)),
            "days_left": max(0, (phase3_end - today).days),
            "start": phase2_end.isoformat(),
            "end": (phase3_end - timedelta(days=1)).isoformat(),
        }
    # After day 90 → Upkeep (ongoing)
    return {
        "phase": 4, "status": "active", "label": PHASE_INFO[4]["name"],
        "progress": 100, "days_left": None,
        "start": phase3_end.isoformat(), "end": None,
    }


def enrich_product(p, today=None, keywords_map=None, fetch_ranks=False):
    if not p or not isinstance(p, dict):
        return {"name": "?", "phase": 0, "status": "active", "label": "—", "progress": 0,
                "phase_info": PHASE_INFO.get(1, {}), "daily_buys": {}, "daily_buys_actual": {},
                "keywords_ranked": [], "all_keywords_ranked": {}, "price_fmt": "", "asin": ""}
    if today is None:
        today = date.today()
    phase_data = get_phase(p.get("launch_date"), today)
    p = dict(p)
    p.update(phase_data)
    p["phase_info"] = PHASE_INFO.get(phase_data["phase"], {})
    try:
        p["launch_fmt"] = datetime.strptime(str(p["launch_date"])[:10], "%Y-%m-%d").strftime("%b %d, %Y")
    except Exception:
        p["launch_fmt"] = str(p["launch_date"])
    p["daily_buys"] = normalize_daily_buys(p.get("daily_buys"))
    p["daily_buys_actual"] = normalize_daily_buys(p.get("daily_buys_actual"))
    p["today_buys"] = today_buy_target(p, today)
    p["today_buys_actual"] = p["daily_buys_actual"].get(str(today.day), 0)
    p["month_buys_total"] = month_buy_total(p)
    p["month_buys_actual_total"] = sum(p["daily_buys_actual"].values()) if p["daily_buys_actual"] else 0
    p["asin"] = (p.get("asin") or "").strip()
    p["thumbnail_url"] = (p.get("thumbnail_url") or "").strip()
    try:
        price_val = p.get("price")
        p["price"] = float(price_val) if price_val is not None and str(price_val).strip() != "" else None
    except (TypeError, ValueError):
        p["price"] = None
    p["price_fmt"] = f"${p['price']:,.2f}" if p["price"] is not None else ""
    km = keywords_map if keywords_map is not None else {}
    if not km and p.get("id"):
        try:
            km = fetch_phase_keywords(p["id"])
        except Exception:
            km = {}
    p["all_keywords"] = km
    phase_num = phase_data.get("phase") or 0
    p["keywords"] = (km.get(phase_num) or km.get(str(phase_num)) or "").strip()

    kws = parse_keyword_list(p.get("keywords") or "")
    if fetch_ranks:
        try:
            p["keywords_ranked"] = attach_ranks_to_keywords(
                p.get("keywords") or "", p.get("asin") or ""
            )
            p["all_keywords_ranked"] = {
                ph: attach_ranks_to_keywords(txt, p.get("asin") or "")
                for ph, txt in (km or {}).items()
                if txt
            }
        except Exception as e:
            print("keyword rank enrich error:", e)
            p["keywords_ranked"] = [{"keyword": k, "organic": None} for k in kws]
            p["all_keywords_ranked"] = {}
    else:
        p["keywords_ranked"] = [{"keyword": k, "organic": None} for k in kws]
        p["all_keywords_ranked"] = {}

    planned, actual = p["today_buys"], p["today_buys_actual"]
    p["buys_gap"] = actual - planned if planned else 0
    p["buys_on_track"] = (actual >= planned) if planned else True
    try:
        p["prelaunch"] = normalize_prelaunch(p.get("prelaunch"))
        p["prelaunch_steps"] = prelaunch_schedule_for_product(p, today)
        done_n = sum(1 for s in p["prelaunch_steps"] if s.get("status") == "done")
        p["prelaunch_progress"] = int(100 * done_n / max(1, len(PRE_LAUNCH_STEPS)))
    except Exception as e:
        print("prelaunch enrich error:", e)
        p["prelaunch"] = {}
        p["prelaunch_steps"] = []
        p["prelaunch_progress"] = 0
    return p


def fetch_all_phase_keywords():
    """{product_id: {phase: keywords}}"""
    sb = get_db()
    if not sb:
        return {}
    try:
        res = sb.table("phase_notes").select("product_id, phase, keywords").execute()
        out = {}
        for row in (res.data or []):
            pid = row["product_id"]
            out.setdefault(pid, {})[row["phase"]] = row.get("keywords") or ""
        return out
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

def fetch_products(status_filter=None):
    sb = get_db()
    if not sb:
        print("fetch_products: Supabase client is None — check .env keys")
        return []
    try:
        q = sb.table("products").select("*").order("launch_date")
        if status_filter:
            q = q.eq("status", status_filter)
        res = q.execute()
        data = res.data or []
        print(f"fetch_products: {len(data)} row(s)")
        return data
    except Exception as e:
        print("fetch_products error:", repr(e))
        return []


def fetch_product(pid):
    sb = get_db()
    if not sb:
        return None
    try:
        res = sb.table("products").select("*").eq("id", pid).single().execute()
        return res.data
    except Exception:
        return None


def insert_product(name, launch_date, notes="", thumbnail_url="", asin="", daily_buys=None, price=None):
    sb = get_db()
    if not sb:
        raise RuntimeError("Supabase not configured")
    payload = {
        "price": price,
        "name": name,
        "launch_date": launch_date,
        "notes": notes,
        "status": "active",
    }
    if thumbnail_url:
        payload["thumbnail_url"] = thumbnail_url
    if asin:
        payload["asin"] = asin
    if daily_buys is not None:
        payload["daily_buys"] = daily_buys
    res = sb.table("products").insert(payload).execute()
    return res.data[0] if res.data else None


def update_product(pid, **fields):
    sb = get_db()
    if not sb:
        raise RuntimeError("Supabase not configured")
    # Keep price even when None (clear price); drop other Nones
    cleaned = {}
    for k, v in fields.items():
        if v is not None or k == "price":
            cleaned[k] = v
    res = sb.table("products").update(cleaned).eq("id", pid).execute()
    return res.data[0] if res.data else None


def parse_daily_buys_from_form(form):
    out = {}
    for d in range(1, 32):
        raw = (form.get(f"buys_{d}") or "").strip()
        if raw == "":
            continue
        try:
            n = int(raw)
            if n > 0:
                out[str(d)] = n
        except ValueError:
            continue
    return out


def normalize_daily_buys(raw):
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            import json
            raw = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        try:
            out[str(int(k))] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def today_buy_target(product, today=None):
    if today is None:
        today = date.today()
    buys = normalize_daily_buys(product.get("daily_buys"))
    return buys.get(str(today.day), 0)


def month_buy_total(product):
    buys = normalize_daily_buys(product.get("daily_buys"))
    return sum(buys.values()) if buys else 0


def upload_thumbnail(file_storage, product_name="product"):
    """Upload image to Supabase Storage bucket 'thumbnails'. Returns public URL or None."""
    if not file_storage or not file_storage.filename:
        return None
    sb = get_db()
    if not sb:
        return None
    try:
        import re
        import uuid
        ext = (file_storage.filename.rsplit(".", 1)[-1] or "jpg").lower()
        if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
            ext = "jpg"
        safe = re.sub(r"[^a-zA-Z0-9_-]", "", product_name)[:40] or "product"
        path = f"{safe}-{uuid.uuid4().hex[:8]}.{ext}"
        data = file_storage.read()
        sb.storage.from_("thumbnails").upload(
            path, data,
            file_options={"content-type": file_storage.content_type or f"image/{ext}", "upsert": "true"},
        )
        # Public URL
        pub = sb.storage.from_("thumbnails").get_public_url(path)
        return pub
    except Exception as e:
        print("thumbnail upload error:", e)
        return None


def fetch_phase_keywords(pid):
    """Return {phase: keywords_str} from phase_notes."""
    sb = get_db()
    if not sb:
        return {}
    try:
        res = sb.table("phase_notes").select("phase, keywords").eq("product_id", pid).execute()
        out = {}
        for row in (res.data or []):
            out[row["phase"]] = row.get("keywords") or ""
        return out
    except Exception:
        # Column may not exist yet — fall back
        return {}


def upsert_phase_keywords(pid, phase, keywords):
    sb = get_db()
    if not sb:
        return
    try:
        existing = sb.table("phase_notes").select("id").eq("product_id", pid).eq("phase", phase).execute()
        if existing.data:
            sb.table("phase_notes").update({
                "keywords": keywords,
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("product_id", pid).eq("phase", phase).execute()
        else:
            sb.table("phase_notes").insert({
                "product_id": pid,
                "phase": phase,
                "content": "",
                "keywords": keywords,
            }).execute()
    except Exception as e:
        print("upsert keywords error:", e)


def delete_product(pid):
    sb = get_db()
    if not sb:
        raise RuntimeError("Supabase not configured")
    try:
        sb.table("phase_notes").delete().eq("product_id", pid).execute()
    except Exception:
        pass
    sb.table("products").delete().eq("id", pid).execute()


def fetch_phase_notes(pid):
    sb = get_db()
    if not sb:
        return {}
    try:
        res = sb.table("phase_notes").select("*").eq("product_id", pid).execute()
        return {n["phase"]: n["content"] for n in (res.data or [])}
    except Exception:
        return {}


def upsert_phase_note(pid, phase, content):
    sb = get_db()
    if not sb:
        raise RuntimeError("Supabase not configured")
    existing = sb.table("phase_notes").select("id").eq("product_id", pid).eq("phase", phase).execute()
    if existing.data:
        sb.table("phase_notes").update({
            "content": content,
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("product_id", pid).eq("phase", phase).execute()
    else:
        sb.table("phase_notes").insert({
            "product_id": pid,
            "phase": phase,
            "content": content,
        }).execute()


def seed_if_empty():
    products = fetch_products()
    if products:
        return
    seeds = [
        ("MaxErectPro", "2026-07-01", "Currently in Review Process (Month 2). Strong early traction."),
        ("TrueVitamin", "2026-08-01", "Just launched — Buy Process underway."),
        ("MaxRyno", "2026-09-01", "Next month launch. Prep inventory & creatives."),
        ("ErectForgepro", "2026-10-01", "Following month. Finalizing supplier agreements."),
    ]
    for name, launch, notes in seeds:
        try:
            insert_product(name, launch, notes)
        except Exception as e:
            print("Seed error:", e)


# ---------------------------------------------------------------------------
# Auth routes (front page = login)
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if session.get("user"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user"):
        return redirect(url_for("dashboard"))

    error = None
    configured = supabase_configured()

    if request.method == "POST":
        if not configured:
            flash("Supabase is not configured. Add SUPABASE_URL and SUPABASE_KEY to .env", "error")
            return redirect(url_for("login"))

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not email or not password:
            error = "Email and password are required."
        elif is_login_rate_limited(email):
            error = login_rate_limit_message()
        else:
            try:
                sb = create_client(SUPABASE_URL, SUPABASE_KEY)
                result = sb.auth.sign_in_with_password({"email": email, "password": password})
                user = result.user
                sess = result.session
                if user and sess:
                    clear_login_failures(email)
                    session.clear()
                    role = ensure_profile(user.id, user.email)
                    profile = get_profile(user.id)
                    # Stash tokens pending 2FA if enabled
                    if profile and profile.get("totp_enabled") and profile.get("totp_secret"):
                        session["mfa_pending"] = {
                            "id": user.id,
                            "email": user.email,
                            "role": role,
                            "access_token": sess.access_token,
                            "refresh_token": sess.refresh_token,
                        }
                        return redirect(url_for("mfa_verify"))
                    session["user"] = {"id": user.id, "email": user.email, "role": role}
                    session["access_token"] = sess.access_token
                    session["refresh_token"] = sess.refresh_token
                    seed_if_empty()
                    flash(f"Welcome back, {user.email}!", "success")
                    return redirect(url_for("dashboard"))
                record_login_failure(email)
                error = "Login failed. Check your credentials."
            except Exception as e:
                record_login_failure(email)
                msg = str(e)
                if "Invalid login credentials" in msg or "invalid" in msg.lower():
                    error = "Invalid email or password."
                else:
                    error = f"Login error: {msg}"

    return render_template(
        "login.html",
        error=error,
        configured=configured,
        today_fmt=date.today().strftime("%B %d, %Y"),
    )


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if session.get("user"):
        return redirect(url_for("dashboard"))

    error = None
    configured = supabase_configured()

    if request.method == "POST":
        if not configured:
            flash("Supabase is not configured.", "error")
            return redirect(url_for("signup"))

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not email or not password:
            error = "Email and password are required."
        elif password != confirm:
            error = "Passwords do not match."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        else:
            try:
                sb = create_client(SUPABASE_URL, SUPABASE_KEY)
                result = sb.auth.sign_up({"email": email, "password": password})
                if result.user:
                    if result.session:
                        session.clear()
                        session["user"] = {"id": result.user.id, "email": result.user.email}
                        session["access_token"] = result.session.access_token
                        session["refresh_token"] = result.session.refresh_token
                        seed_if_empty()
                        flash("Account created — you're in!", "success")
                        return redirect(url_for("dashboard"))
                    flash("Check your email to confirm your account, then log in.", "success")
                    return redirect(url_for("login"))
                error = "Sign-up failed."
            except Exception as e:
                msg = str(e)
                if "already registered" in msg.lower() or "already been registered" in msg.lower():
                    error = "That email is already registered. Try logging in."
                else:
                    error = f"Sign-up error: {msg}"

    return render_template(
        "signup.html",
        error=error,
        configured=configured,
        today_fmt=date.today().strftime("%B %d, %Y"),
    )



@app.route("/debug/form-check")
@admin_required
def debug_form_check():
    """Admin-only template check."""
    path = os.path.join(app.root_path, "templates", "product_form.html")
    try:
        text = open(path).read()
    except Exception as e:
        return f"Cannot read template: {e}", 500
    checks = {
        "ASIN": "ASIN" in text,
        "Daily Buys": "Daily Buys" in text,
        "Phase Keywords": "Phase Keywords" in text,
        "thumbnail": "thumbnail" in text,
        "file_exists": os.path.exists(path),
    }
    return "<pre>" + "\n".join(f"{k}: {v}" for k, v in checks.items()) + "</pre>"


@app.route("/logout")
def logout():
    try:
        sb = get_supabase()
        if sb:
            sb.auth.sign_out()
    except Exception:
        pass
    session.clear()
    flash("You've been logged out.", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Protected app routes
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    today = date.today()
    products = []
    activity = []
    try:
        rows = fetch_products() or []
        kw_all = fetch_all_phase_keywords() or {}
        for r in rows:
            if not r:
                continue
            try:
                products.append(enrich_product(r, today, kw_all.get(r.get("id"), {})))
            except Exception as e:
                print("enrich error", r.get("id"), e)
        activity = fetch_activity(12) or []
    except Exception as e:
        print("dashboard load error:", repr(e))
        import traceback
        traceback.print_exc()
        flash("Some data could not be loaded.", "error")

    # Only fully enriched products (have phase key)
    products = [p for p in products if isinstance(p, dict) and "phase" in p]

    active = [p for p in products if p.get("status") == "active" and p.get("phase") in (1, 2, 3, 4)]
    upcoming = [p for p in products if p.get("phase") == 0]
    in_cycle = [p for p in products if p.get("phase") in (1, 2, 3)]
    in_upkeep = [p for p in products if p.get("phase") == 4]
    buys_focus = [p for p in products if p.get("today_buys") or p.get("today_buys_actual")]

    stats = {
        "total": len([p for p in products if p.get("status") == "active"]),
        "in_buy": len([p for p in products if p.get("phase") == 1]),
        "in_review": len([p for p in products if p.get("phase") == 2]),
        "in_push": len([p for p in products if p.get("phase") == 3]),
        "in_upkeep": len(in_upkeep),
        "upcoming": len(upcoming),
        "buys_behind": len([p for p in buys_focus if p.get("today_buys") and not p.get("buys_on_track")]),
    }

    return render_template(
        "dashboard.html",
        products=products,
        active=in_cycle + in_upkeep,
        upcoming=upcoming,
        buys_focus=buys_focus,
        stats=stats,
        activity=activity,
        today=today.isoformat(),
        today_day=today.day,
        today_fmt=today.strftime("%B %d, %Y"),
        phase_info=PHASE_INFO,
        user=session.get("user"),
    )


@app.route("/timeline")
@login_required
def timeline():
    today = date.today()
    rows = fetch_products()
    kw_all = fetch_all_phase_keywords()
    products = [enrich_product(r, today, kw_all.get(r.get("id"), {})) for r in rows]

    launches = []
    for p in products:
        try:
            launches.append(datetime.strptime(str(p.get("launch_date") or "")[:10], "%Y-%m-%d").date())
        except Exception:
            pass
    if launches:
        start = min(launches).replace(day=1) - relativedelta(months=1)
        end = max(launches).replace(day=1) + relativedelta(months=6)
    else:
        start = today.replace(day=1) - relativedelta(months=1)
        end = start + relativedelta(months=8)

    months = []
    cur = start
    while cur <= end:
        months.append({
            "key": cur.strftime("%Y-%m"),
            "label": cur.strftime("%b %Y"),
            "is_current": cur.year == today.year and cur.month == today.month,
        })
        cur += relativedelta(months=1)

    timeline_rows = []
    for p in products:
        try:
            launch = datetime.strptime(str(p.get("launch_date") or "")[:10], "%Y-%m-%d").date()
        except Exception:
            launch = today
        phase_map = {}
        for m in months:
            # Use mid-month to decide which 30-day phase owns this column
            m_start = datetime.strptime(m["key"] + "-01", "%Y-%m-%d").date()
            if m_start.month == 12:
                m_mid = m_start.replace(day=15)
            else:
                m_mid = m_start + timedelta(days=14)
            if m_mid < launch:
                phase_map[m["key"]] = 0
            else:
                day_offset = (m_mid - launch).days
                if day_offset < 30:
                    phase_map[m["key"]] = 1
                elif day_offset < 60:
                    phase_map[m["key"]] = 2
                elif day_offset < 90:
                    phase_map[m["key"]] = 3
                else:
                    phase_map[m["key"]] = 4
        timeline_rows.append({"product": p, "phases": phase_map})

    return render_template(
        "timeline.html",
        products=products,
        months=months,
        timeline_rows=timeline_rows,
        phase_info=PHASE_INFO,
        pre_launch_steps=PRE_LAUNCH_STEPS,
        today_fmt=today.strftime("%B %d, %Y"),
        user=session.get("user"),
    )


@app.route("/products")
@login_required
def products_list():
    today = date.today()
    rows = fetch_products()
    rows = sorted(rows, key=lambda r: str(r.get("launch_date", "")), reverse=True)
    kw_all = fetch_all_phase_keywords()
    products = [enrich_product(r, today, kw_all.get(r.get("id"), {})) for r in rows]
    return render_template(
        "products.html",
        products=products,
        phase_info=PHASE_INFO,
        today_fmt=today.strftime("%B %d, %Y"),
        today_day=today.day,
        user=session.get("user"),
    )


@app.route("/product/<int:pid>")
@login_required
def product_detail(pid):
    today = date.today()
    try:
        row = fetch_product(pid)
        if not row:
            flash("Product not found.", "error")
            return redirect(url_for("dashboard"))
        phase_keywords = fetch_phase_keywords(pid) or {}
        product = enrich_product(row, today, phase_keywords, fetch_ranks=False)
        # Ensure dicts for template safety
        product["daily_buys"] = product.get("daily_buys") or {}
        product["daily_buys_actual"] = product.get("daily_buys_actual") or {}
        product["phase_info"] = product.get("phase_info") or PHASE_INFO.get(product.get("phase") or 0) or {
            "name": "—", "color": "#FF6B00", "icon": "🍊", "desc": ""
        }
        notes_map = fetch_phase_notes(pid) or {}
        # Ranks load async via /api/product/<id>/keyword-ranks (avoids page timeout)
        phase_keywords_ranked = {}
        for ph, txt in phase_keywords.items():
            if not txt:
                continue
            phase_keywords_ranked[ph] = [
                {"keyword": k, "organic": None} for k in parse_keyword_list(txt)
            ]
        return render_template(
            "product_detail.html",
            product=product,
            notes_map=notes_map,
            phase_keywords=phase_keywords,
            phase_keywords_ranked=phase_keywords_ranked,
            phase_info=PHASE_INFO,
            today_fmt=today.strftime("%B %d, %Y"),
            today_day=today.day,
            user=session.get("user"),
        )
    except Exception as e:
        print("product_detail error:", repr(e))
        import traceback
        traceback.print_exc()
        flash(f"Could not open product: {e}", "error")
        return redirect(url_for("dashboard"))


@app.route("/product/new", methods=["GET", "POST"])
@login_required
def product_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        launch_date = request.form.get("launch_date", "").strip()
        notes = request.form.get("notes", "").strip()
        thumbnail_url = request.form.get("thumbnail_url", "").strip()
        asin = request.form.get("asin", "").strip()
        price_raw = request.form.get("price", "").strip()
        try:
            price = float(price_raw) if price_raw else None
        except ValueError:
            price = None
        daily_buys = parse_daily_buys_from_form(request.form)
        if not name or not launch_date:
            flash("Name and launch date are required.", "error")
            return redirect(url_for("product_new"))
        try:
            file = request.files.get("thumbnail_file")
            uploaded = upload_thumbnail(file, name) if file and file.filename else None
            if uploaded:
                thumbnail_url = uploaded
            created = insert_product(
                name, launch_date, notes, thumbnail_url,
                asin=asin, daily_buys=daily_buys, price=price,
            )
            if created and created.get("id"):
                for n in range(1, 5):
                    kw = request.form.get(f"keywords_{n}", "").strip()
                    if kw:
                        upsert_phase_keywords(created["id"], n, kw)
            if created and created.get("id"):
                ensure_product_sheets(created["id"])
                log_activity(created["id"], f'Created product "{name}"', action="create")
            flash(f'"{name}" added successfully.', "success")
            return redirect(url_for("dashboard"))
        except Exception as e:
            flash(f"Could not create product: {e}", "error")
            return redirect(url_for("product_new"))
    return render_template(
        "product_form.html",
        product=None,
        phase_info=PHASE_INFO,
        phase_keywords={},
        daily_buys={},
        today_fmt=date.today().strftime("%B %d, %Y"),
        user=session.get("user"),
    )


@app.route("/product/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def product_edit(pid):
    row = fetch_product(pid)
    if not row:
        flash("Product not found.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        launch_date = request.form.get("launch_date", "").strip()
        notes = request.form.get("notes", "").strip()
        status = request.form.get("status", "active")
        thumbnail_url = request.form.get("thumbnail_url", "").strip()
        asin = request.form.get("asin", "").strip().upper()
        price_raw = request.form.get("price", "").strip()
        try:
            price = float(price_raw) if price_raw else None
        except ValueError:
            price = None
        daily_buys = parse_daily_buys_from_form(request.form)
        try:
            file = request.files.get("thumbnail_file")
            uploaded = upload_thumbnail(file, name) if file and file.filename else None
            fields = {
                "name": name,
                "launch_date": launch_date,
                "notes": notes,
                "status": status,
                "asin": asin,
                "price": price,
                "daily_buys": daily_buys,
            }
            if uploaded:
                fields["thumbnail_url"] = uploaded
            elif thumbnail_url:
                fields["thumbnail_url"] = thumbnail_url
            update_product(pid, **fields)
            for n in range(1, 5):
                kw = request.form.get(f"keywords_{n}", "").strip()
                upsert_phase_keywords(pid, n, kw)
            log_activity(pid, f'Updated product "{name}" (ASIN={asin or "—"})', action="update")
            flash("Product updated.", "success")
            return redirect(url_for("product_detail", pid=pid))
        except Exception as e:
            flash(f"Update failed: {e}", "error")
            return redirect(url_for("product_edit", pid=pid))

    phase_keywords = fetch_phase_keywords(pid)
    daily_buys = normalize_daily_buys(row.get("daily_buys"))
    return render_template(
        "product_form.html",
        product=row,
        phase_info=PHASE_INFO,
        phase_keywords=phase_keywords,
        daily_buys=daily_buys,
        today_fmt=date.today().strftime("%B %d, %Y"),
        user=session.get("user"),
    )


@app.route("/product/<int:pid>/delete", methods=["POST"])
@login_required
def product_delete(pid):
    try:
        row = fetch_product(pid)
        name = (row or {}).get("name", pid)
        delete_product(pid)
        log_activity(None, f'Deleted product "{name}"', action="delete")
        flash("Product removed.", "success")
    except Exception as e:
        flash(f"Delete failed: {e}", "error")
    return redirect(url_for("products_list"))


@app.route("/product/<int:pid>/note", methods=["POST"])
@login_required
def save_phase_note(pid):
    phase = int(request.form.get("phase", 0))
    content = request.form.get("content", "").strip()
    try:
        upsert_phase_note(pid, phase, content)
        log_activity(pid, f"Updated phase {phase} notes", action="note")
        flash("Phase note saved.", "success")
    except Exception as e:
        flash(f"Could not save note: {e}", "error")
    return redirect(url_for("product_detail", pid=pid))


# ---------------------------------------------------------------------------
# API — drag-drop phase moves + activity
# ---------------------------------------------------------------------------

def log_activity(product_id, message, action="update"):
    """Best-effort activity log with actor + timestamp."""
    sb = get_db()
    if not sb:
        return
    user = session.get("user") or {}
    payload = {
        "product_id": product_id,
        "message": message,
        "action": action,
        "user_id": user.get("id"),
        "user_email": user.get("email"),
    }
    try:
        sb.table("activity_log").insert(payload).execute()
    except Exception as e:
        # Retry without new columns if schema not migrated yet
        try:
            sb.table("activity_log").insert({
                "product_id": product_id,
                "message": message,
            }).execute()
        except Exception as e2:
            print("activity_log skip:", e, e2)


def fetch_activity(limit=50):
    sb = get_db()
    if not sb:
        return []
    try:
        res = sb.table("activity_log").select("*, products(name)").order(
            "created_at", desc=True
        ).limit(limit).execute()
        return res.data or []
    except Exception:
        return []


@app.route("/api/product/<int:pid>/set-phase", methods=["POST"])
@login_required
def api_set_phase(pid):
    """
    Drag-drop endpoint.
    Body JSON: { "phase": 1-4, "target_month": "2026-09" }
    Recalculates launch_date so the given phase lands on target_month,
    then the whole app (dashboard, products, detail) reflects the change.
    """
    data = request.get_json(silent=True) or {}
    phase = int(data.get("phase", 0))
    target_month = data.get("target_month", "")  # YYYY-MM

    if phase not in (1, 2, 3, 4) or not target_month:
        return jsonify({"ok": False, "error": "phase (1-4) and target_month required"}), 400

    try:
        target = datetime.strptime(target_month + "-01", "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid target_month"}), 400

    # phase N starts at launch + (N-1)*30 days → set launch so phase N begins on target month 1st
    new_launch = (target - timedelta(days=(phase - 1) * 30)).isoformat()

    row = fetch_product(pid)
    if not row:
        return jsonify({"ok": False, "error": "Product not found"}), 404

    old_name = row.get("name", "Product")
    try:
        update_product(pid, launch_date=new_launch)
        phase_label = PHASE_INFO.get(phase, {}).get("name", f"Phase {phase}")
        log_activity(
            pid,
            f"{old_name} → {phase_label} aligned to {target.strftime('%b %Y')} "
            f"(launch set to {new_launch})",
        )
        return jsonify({
            "ok": True,
            "launch_date": new_launch,
            "phase": phase,
            "target_month": target_month,
            "message": f"Moved {phase_label} to {target.strftime('%b %Y')}",
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/product/<int:pid>/advance", methods=["POST"])
@login_required
def api_advance_phase(pid):
    """Quick-action: shift launch back 1 month so the product advances one phase."""
    row = fetch_product(pid)
    if not row:
        return jsonify({"ok": False, "error": "Not found"}), 404
    launch = datetime.strptime(str(row["launch_date"])[:10], "%Y-%m-%d").date()
    # Shift launch 30 days earlier → advances one phase in the 90-day cycle
    new_launch = (launch - timedelta(days=30)).isoformat()
    try:
        update_product(pid, launch_date=new_launch)
        enriched = enrich_product({**row, "launch_date": new_launch})
        log_activity(pid, f"{row.get('name')} advanced to {enriched.get('label')}")
        return jsonify({"ok": True, "launch_date": new_launch, "phase": enriched["phase"], "label": enriched["label"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/activity")
@login_required
def api_activity():
    return jsonify(fetch_activity())


@app.route("/api/product/<int:pid>/log-buys", methods=["POST"])
@login_required
def api_log_buys(pid):
    """Log actual buys for a given day of month. JSON: {day: 1-31, actual: int} or {actual: int} for today."""
    data = request.get_json(silent=True) or {}
    today = date.today()
    try:
        day = int(data.get("day") or today.day)
        actual = int(data.get("actual", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "day and actual must be integers"}), 400
    if day < 1 or day > 31 or actual < 0:
        return jsonify({"ok": False, "error": "invalid day/actual"}), 400

    row = fetch_product(pid)
    if not row:
        return jsonify({"ok": False, "error": "Not found"}), 404

    actuals = normalize_daily_buys(row.get("daily_buys_actual"))
    actuals[str(day)] = actual
    try:
        update_product(pid, daily_buys_actual=actuals)
        planned = normalize_daily_buys(row.get("daily_buys")).get(str(day), 0)
        log_activity(pid, f"{row.get('name')}: day {day} actual buys = {actual} (plan {planned})")
        return jsonify({
            "ok": True,
            "day": day,
            "actual": actual,
            "planned": planned,
            "gap": actual - planned,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/product/<int:pid>/log-buys", methods=["POST"])
@login_required
def form_log_buys(pid):
    """Form POST fallback for logging actual buys."""
    today = date.today()
    try:
        day = int(request.form.get("day") or today.day)
        actual = int(request.form.get("actual") or 0)
    except (TypeError, ValueError):
        flash("Invalid buys value.", "error")
        return redirect(url_for("product_detail", pid=pid))
    row = fetch_product(pid)
    if not row:
        flash("Product not found.", "error")
        return redirect(url_for("dashboard"))
    actuals = normalize_daily_buys(row.get("daily_buys_actual"))
    actuals[str(day)] = actual
    try:
        update_product(pid, daily_buys_actual=actuals)
        flash(f"Logged {actual} actual buys for day {day}.", "success")
    except Exception as e:
        flash(f"Could not save: {e}", "error")
    return redirect(url_for("product_detail", pid=pid))




# ---------------------------------------------------------------------------
# SoldScope Rank Tracker API
# ---------------------------------------------------------------------------

def soldscope_configured():
    return bool(SOLDSCOPE_API_TOKEN)


def soldscope_request(method, path, params=None, json_body=None):
    """Call SoldScope API. Returns (ok: bool, data_or_error)."""
    if not SOLDSCOPE_API_TOKEN:
        return False, "SOLDSCOPE_API_TOKEN is not set"
    try:
        import urllib.request
        import urllib.error
        import json as _json
        from urllib.parse import urlencode
        url = SOLDSCOPE_API_BASE + path
        if params:
            clean = {}
            for k, v in params.items():
                if v is None:
                    continue
                if isinstance(v, bool):
                    clean[k] = "true" if v else "false"
                else:
                    clean[k] = v
            url += "?" + urlencode(clean, doseq=True)
        data = None
        headers = {
            "Authorization": f"Bearer {SOLDSCOPE_API_TOKEN}",
            "Accept": "application/json",
            "User-Agent": "LaunchPulse/1.0",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            data = _json.dumps(json_body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read().decode("utf-8")
            return True, _json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            err_json = _json.loads(err_body)
        except Exception:
            err_json = err_body
        print(f"SoldScope HTTP {e.code} {path}: {str(err_json)[:300]}")
        return False, f"HTTP {e.code}: {err_json}"
    except Exception as e:
        print(f"SoldScope error {path}: {e}")
        return False, str(e)


def ss_list_groups(page=1, per_page=50, search=""):
    params = {"page": page, "perPage": per_page}
    if search:
        params["search"] = search
    return soldscope_request("GET", "/rank-tracker/groups", params=params)


def ss_get_group(group_id):
    return soldscope_request("GET", f"/rank-tracker/groups/{group_id}")


def ss_create_group(marketplace, asin, phrases, track_all_variations=False):
    body = {
        "marketplace": marketplace,
        "asin": asin,
        "phrases": phrases,
        "trackAllVariations": bool(track_all_variations),
    }
    return soldscope_request("POST", "/rank-tracker/groups", json_body=body)


def ss_list_products(group_id):
    return soldscope_request("GET", f"/rank-tracker/groups/{group_id}/products")


def ss_products_with_stats(group_id, page=1, per_page=50):
    return soldscope_request(
        "GET",
        f"/rank-tracker/groups/{group_id}/products-with-stats",
        params={"page": page, "perPage": per_page},
    )


def ss_list_phrases(group_id, product_id, page=1, per_page=100, results_type="organic"):
    return soldscope_request(
        "GET",
        f"/rank-tracker/groups/{group_id}/products/{product_id}/phrases/v2",
        params={
            "page": page,
            "perPage": per_page,
            "resultsType": results_type,
            "sort": "organic_position",
            "sortDesc": False,
        },
    )


def ss_phrases_list(group_id):
    """Keyword list for a group (no ranks)."""
    return soldscope_request("GET", f"/rank-tracker/groups/{group_id}/phrases-list")




def parse_keyword_list(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [k.strip() for k in re.split(r"[,;\n]+", str(raw)) if k.strip()]


_rank_map_cache = {}
_rank_map_cache_at = {}


def rank_map_for_asin(asin, limit=80):
    """Map lowercased phrase -> organic rank from SoldScope (cached ~5 min)."""
    import time as _t
    if not asin or not soldscope_configured():
        return {}
    key = str(asin).strip().upper()
    now = _t.time()
    if key in _rank_map_cache and _rank_map_cache[key] and now - _rank_map_cache_at.get(key, 0) < 300:
        return _rank_map_cache[key]
    try:
        ranks = cockpit_ranks_for_asin(key, limit=limit)
        m = {}
        for r in ranks:
            phrase = (r.get("phrase") or "").strip().lower()
            if phrase and r.get("organic") is not None:
                m[phrase] = r.get("organic")
        _rank_map_cache[key] = m
        _rank_map_cache_at[key] = now if m else now - 240  # retry sooner if empty
        return m
    except Exception as e:
        print("rank_map_for_asin error:", e)
        return {}


def _norm_kw(s):
    return " ".join(str(s or "").lower().split())


def attach_ranks_to_keywords(keywords_str, asin, rank_map=None):
    """Return list of {keyword, organic} for display."""
    if rank_map is None:
        rank_map = rank_map_for_asin(asin) if asin else {}
    # also index normalized keys
    norm_map = {_norm_kw(k): v for k, v in (rank_map or {}).items()}
    out = []
    for kw in parse_keyword_list(keywords_str):
        low = _norm_kw(kw)
        org = norm_map.get(low)
        if org is None:
            for phrase, rnk in norm_map.items():
                if not phrase:
                    continue
                if low == phrase or low in phrase or phrase in low:
                    org = rnk
                    break
        out.append({"keyword": kw, "organic": org})
    return out


def _ss_unwrap_list(data, *keys):
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for k in keys:
        v = data.get(k)
        if isinstance(v, list):
            return v
    # nested data.data
    inner = data.get("data")
    if isinstance(inner, list):
        return inner
    if isinstance(inner, dict):
        for k in keys:
            v = inner.get(k)
            if isinstance(v, list):
                return v
    return []


def _ss_organic_from_phrase(ph):
    """Pull organic rank from SoldScope RtPhrase fields."""
    if not isinstance(ph, dict):
        return None
    for key in (
        "organicPosition", "organic_position", "organicRank", "organic_rank",
        "orgPosition",
    ):
        if key in ph and ph[key] is not None and ph[key] != "":
            try:
                return int(ph[key])
            except (TypeError, ValueError):
                try:
                    return int(float(ph[key]))
                except Exception:
                    return None
    # nested shapes
    for nest in ("positions", "current", "ranks", "organicData", "organic"):
        sub = ph.get(nest)
        if isinstance(sub, dict):
            for key in ("organic", "organicPosition", "position", "rank"):
                if sub.get(key) is not None and sub.get(key) != "":
                    try:
                        return int(sub[key])
                    except (TypeError, ValueError):
                        return None
        elif nest == "organic" and isinstance(sub, (int, float)):
            return int(sub)
    return None


def _ss_phrase_text(ph):
    if not isinstance(ph, dict):
        return ""
    for key in ("phrase", "keyword", "text", "query", "name"):
        if ph.get(key):
            return str(ph[key]).strip()
    return ""


def cockpit_ranks_for_asin(asin, limit=100):
    """
    Fetch organic ranks for ONE ASIN from SoldScope Rank Tracker.
    Uses official fields: RtPhrase.organicPosition / phrase.
    """
    if not asin or not soldscope_configured():
        return []
    asin_u = str(asin).strip().upper()
    debug = {"asin": asin_u, "groups_checked": 0, "matched_group": None, "product_id": None, "phrase_count": 0}

    def find_product_id_in_group(gid):
        # 1) products-with-stats (has asin)
        ok, data = ss_products_with_stats(gid, per_page=100)
        products = _ss_unwrap_list(data, "data", "products", "items") if ok else []
        for pr in products:
            if str(pr.get("asin") or "").upper() == asin_u:
                return pr.get("id") or pr.get("productId")
        # 2) plain products list
        ok2, data2 = ss_list_products(gid)
        products2 = _ss_unwrap_list(data2, "data", "products", "items") if ok2 else []
        for pr in products2:
            if str(pr.get("asin") or "").upper() == asin_u:
                return pr.get("id") or pr.get("productId")
        return None

    # Collect candidate groups
    groups = []
    # Search by ASIN first
    ok, data = ss_list_groups(per_page=100, search=asin_u)
    if ok:
        groups = _ss_unwrap_list(data, "data", "groups", "items")
    # Always also load full list (search can miss)
    ok2, data2 = ss_list_groups(per_page=100)
    if ok2:
        all_g = _ss_unwrap_list(data2, "data", "groups", "items")
        seen = {g.get("id") for g in groups if isinstance(g, dict)}
        for g in all_g:
            if isinstance(g, dict) and g.get("id") not in seen:
                groups.append(g)

    ranks = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        gid = g.get("id")
        if not gid:
            continue
        debug["groups_checked"] += 1
        g_asin = str(g.get("asin") or "").upper()
        product_id = None

        # Direct group ASIN match → use primaryProductId when possible
        if g_asin == asin_u:
            product_id = g.get("primaryProductId") or g.get("primary_product_id")
            if not product_id:
                product_id = find_product_id_in_group(gid)
        else:
            # Group may track multiple products — find our ASIN inside
            product_id = find_product_id_in_group(gid)

        if not product_id:
            continue

        debug["matched_group"] = gid
        debug["product_id"] = product_id

        # Fetch all phrase pages
        page = 1
        while page <= 5 and len(ranks) < limit:
            ok_ph, phdata = ss_list_phrases(gid, product_id, page=page, per_page=min(100, limit))
            if not ok_ph:
                print("ss_list_phrases fail", gid, product_id, phdata)
                break
            phrases = _ss_unwrap_list(phdata, "data", "phrases", "items", "results")
            if not phrases and isinstance(phdata, dict) and isinstance(phdata.get("data"), list):
                phrases = phdata["data"]
            if not phrases:
                break
            debug["phrase_count"] += len(phrases)
            for ph in phrases:
                if not isinstance(ph, dict):
                    continue
                text = _ss_phrase_text(ph)
                org = _ss_organic_from_phrase(ph)
                # organicPosition can legitimately be null if not ranking
                ranks.append({
                    "phrase": text or "—",
                    "organic": org,
                    "sponsored": ph.get("sponsoredPosition") if ph.get("sponsoredPosition") is not None else ph.get("sponsored_position"),
                    "prev_organic": ph.get("organicPreviousPosition") if ph.get("organicPreviousPosition") is not None else ph.get("organic_previous_position"),
                    "group_id": gid,
                    "asin": asin_u,
                    "organic_asin": ph.get("organicAsin"),
                })
            # pagination
            meta = phdata.get("meta") if isinstance(phdata, dict) else None
            last_page = None
            if isinstance(meta, dict):
                last_page = meta.get("last_page") or meta.get("lastPage")
            if last_page and page >= int(last_page):
                break
            if len(phrases) < 25:
                break
            page += 1

        if ranks:
            break

    print(f"SoldScope ranks for {asin_u}: {len(ranks)} phrases | debug={debug}")
    return ranks[:limit]



def ss_auth_check():
    return soldscope_request("GET", "/auth/check")



def compute_share_of_shelf(ranks, page1_cutoff=16):
    """% of tracked keywords with organic rank on roughly page 1."""
    if not ranks:
        return {"owned": 0, "total": 0, "pct": None}
    total = 0
    owned = 0
    for r in ranks:
        org = r.get("organic")
        if org is None:
            continue
        total += 1
        try:
            if int(org) <= page1_cutoff:
                owned += 1
        except (TypeError, ValueError):
            pass
    pct = round(100.0 * owned / total, 1) if total else None
    return {"owned": owned, "total": total, "pct": pct, "cutoff": page1_cutoff}


def compute_rank_alerts(ranks, drop_threshold=5):
    """Alerts when organic rank worsens vs previous position."""
    alerts = []
    for r in ranks:
        org = r.get("organic")
        prev = r.get("prev_organic")
        if org is None or prev is None:
            continue
        try:
            org_i, prev_i = int(org), int(prev)
        except (TypeError, ValueError):
            continue
        delta = org_i - prev_i  # positive = dropped (worse)
        if delta >= drop_threshold:
            alerts.append({
                "phrase": r.get("phrase") or "keyword",
                "organic": org_i,
                "prev": prev_i,
                "delta": delta,
                "type": "drop",
            })
        elif delta <= -drop_threshold:
            alerts.append({
                "phrase": r.get("phrase") or "keyword",
                "organic": org_i,
                "prev": prev_i,
                "delta": delta,
                "type": "gain",
            })
    alerts.sort(key=lambda a: abs(a["delta"]), reverse=True)
    return alerts


def bsr_sparkline_points(asin, marketplace="US", days=30):
    """Return list of BSR values (newest last) for sparkline rendering."""
    if not asin or not soldscope_configured():
        return []
    ok, data = ss_bsr_history(asin, marketplace, days)
    if not ok:
        print("bsr_history error:", data)
        return []
    # Keepa-style or SoldScope: try common shapes
    series = []
    if isinstance(data, list):
        series = data
    elif isinstance(data, dict):
        for key in ("data", "history", "bsr", "values", "points", "items"):
            if isinstance(data.get(key), list):
                series = data[key]
                break
        if not series and isinstance(data.get("data"), dict):
            inner = data["data"]
            for key in ("history", "bsr", "values", "points"):
                if isinstance(inner.get(key), list):
                    series = inner[key]
                    break
    points = []
    for item in series:
        if isinstance(item, (int, float)):
            points.append(int(item))
        elif isinstance(item, dict):
            for k in ("bsr", "rank", "value", "y", "salesRank"):
                if item.get(k) is not None:
                    try:
                        points.append(int(item[k]))
                    except (TypeError, ValueError):
                        pass
                    break
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                points.append(int(item[-1]))
            except (TypeError, ValueError):
                pass
    return points[-days:]


def sparkline_svg(values, width=120, height=28, better_lower=True):
    """Inline SVG path for BSR (lower is better)."""
    if not values or len(values) < 2:
        return ""
    nums = [v for v in values if isinstance(v, (int, float))]
    if len(nums) < 2:
        return ""
    lo, hi = min(nums), max(nums)
    span = (hi - lo) or 1
    n = len(nums)
    coords = []
    for i, v in enumerate(nums):
        x = i * (width / (n - 1))
        # lower BSR = higher on chart
        y = ((v - lo) / span) * (height - 4) + 2
        if better_lower:
            y = height - y
        coords.append(f"{x:.1f},{y:.1f}")
    path = "M" + " L".join(coords)
    last = nums[-1]
    first = nums[0]
    # improving if BSR went down
    color = "#10b981" if last < first else ("#ef4444" if last > first else "#94a3b8")
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'class="inline-block align-middle" aria-hidden="true">'
        f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
        f"</svg>"
    )


def ss_bsr_history(asin, marketplace="US", days=30):
    return soldscope_request(
        "GET",
        "/common/bsr-history",
        params={"asin": asin, "marketplace": marketplace, "days": days},
    )

# ---------------------------------------------------------------------------
# BA / Emails workspace (per-product databases)
# ---------------------------------------------------------------------------

DEFAULT_SHEET_COLUMNS = {
    "ba": [
        "Program", "Profile", "Password", "2FA", "Review Code",
        "Review Date", "Purchased", "Delivered", "User",
    ],
    "emails": [
        "Program", "Profile", "Password", "2FA", "Review Code",
        "Review Date", "Purchased", "Delivered", "User",
    ],
}


def ensure_product_sheets(product_id):
    """Create BA + Emails sheets with default columns and one empty row."""
    sb = get_db()
    if not sb or not product_id:
        return
    for sheet_type, col_names in DEFAULT_SHEET_COLUMNS.items():
        try:
            existing = sb.table("workspace_sheets").select("id").eq(
                "product_id", product_id
            ).eq("sheet_type", sheet_type).execute()
            if existing.data:
                continue
            res = sb.table("workspace_sheets").insert({
                "product_id": product_id,
                "sheet_type": sheet_type,
            }).execute()
            if not res.data:
                continue
            sheet_id = res.data[0]["id"]
            cols = [
                {"sheet_id": sheet_id, "name": name, "position": i}
                for i, name in enumerate(col_names)
            ]
            sb.table("workspace_columns").insert(cols).execute()
            sb.table("workspace_rows").insert({
                "sheet_id": sheet_id,
                "position": 0,
                "color": "none",
                "data": {},
            }).execute()
        except Exception as e:
            print("ensure_product_sheets error:", e)


def ensure_all_product_sheets():
    for row in fetch_products():
        if row.get("id"):
            ensure_product_sheets(row["id"])


def get_or_create_sheet(product_id, sheet_type):
    sb = get_db()
    if not sb:
        return None
    ensure_product_sheets(product_id)
    try:
        res = sb.table("workspace_sheets").select("*").eq(
            "product_id", product_id
        ).eq("sheet_type", sheet_type).single().execute()
        return res.data
    except Exception as e:
        print("get_or_create_sheet:", e)
        return None


def fetch_sheet_columns(sheet_id):
    sb = get_db()
    if not sb:
        return []
    try:
        res = sb.table("workspace_columns").select("*").eq(
            "sheet_id", sheet_id
        ).order("position").execute()
        return res.data or []
    except Exception:
        return []


def fetch_sheet_rows(sheet_id):
    sb = get_db()
    if not sb:
        return []
    try:
        res = sb.table("workspace_rows").select("*").eq(
            "sheet_id", sheet_id
        ).order("position").execute()
        return res.data or []
    except Exception:
        return []


def add_sheet_row(sheet_id):
    sb = get_db()
    rows = fetch_sheet_rows(sheet_id)
    pos = (max((r.get("position") or 0) for r in rows) + 1) if rows else 0
    user = (session.get("user") or {}).get("email")
    res = sb.table("workspace_rows").insert({
        "sheet_id": sheet_id,
        "position": pos,
        "color": "none",
        "data": {},
        "updated_by": user,
    }).execute()
    return res.data[0] if res.data else None


def add_sheet_column(sheet_id, name):
    sb = get_db()
    cols = fetch_sheet_columns(sheet_id)
    pos = (max((c.get("position") or 0) for c in cols) + 1) if cols else 0
    res = sb.table("workspace_columns").insert({
        "sheet_id": sheet_id,
        "name": name.strip() or "New column",
        "position": pos,
    }).execute()
    return res.data[0] if res.data else None


def update_row_cell(row_id, col_id, value):
    sb = get_db()
    row = sb.table("workspace_rows").select("*").eq("id", row_id).single().execute().data
    data = row.get("data") or {}
    if isinstance(data, str):
        import json
        data = json.loads(data)
    data[str(col_id)] = value
    user = (session.get("user") or {}).get("email")
    sb.table("workspace_rows").update({
        "data": data,
        "updated_at": datetime.utcnow().isoformat(),
        "updated_by": user,
    }).eq("id", row_id).execute()


def update_row_color(row_id, color):
    if color not in ("none", "green", "red", "orange"):
        color = "none"
    sb = get_db()
    user = (session.get("user") or {}).get("email")
    sb.table("workspace_rows").update({
        "color": color,
        "updated_at": datetime.utcnow().isoformat(),
        "updated_by": user,
    }).eq("id", row_id).execute()


def delete_sheet_row(row_id):
    sb = get_db()
    sb.table("workspace_rows").delete().eq("id", row_id).execute()


def duplicate_sheet_row(row_id):
    sb = get_db()
    row = sb.table("workspace_rows").select("*").eq("id", row_id).single().execute().data
    if not row:
        return None
    rows = fetch_sheet_rows(row["sheet_id"])
    pos = (max((r.get("position") or 0) for r in rows) + 1) if rows else 0
    user = (session.get("user") or {}).get("email")
    data = row.get("data") or {}
    res = sb.table("workspace_rows").insert({
        "sheet_id": row["sheet_id"],
        "position": pos,
        "color": row.get("color") or "none",
        "data": data,
        "updated_by": user,
    }).execute()
    return res.data[0] if res.data else None


def rename_sheet_column(col_id, name):
    sb = get_db()
    sb.table("workspace_columns").update({"name": name.strip() or "Column"}).eq("id", col_id).execute()


def delete_sheet_column(col_id):
    sb = get_db()
    sb.table("workspace_columns").delete().eq("id", col_id).execute()



@app.route("/ba")
@login_required
def ba_index():
    ensure_all_product_sheets()
    today = date.today()
    rows = fetch_products()
    products = [enrich_product(r, today) for r in rows]
    return render_template(
        "workspace_index.html",
        sheet_type="ba",
        title="BA",
        products=products,
        today_fmt=today.strftime("%B %d, %Y"),
        user=session.get("user"),
    )


@app.route("/emails")
@login_required
def emails_index():
    ensure_all_product_sheets()
    today = date.today()
    rows = fetch_products()
    products = [enrich_product(r, today) for r in rows]
    return render_template(
        "workspace_index.html",
        sheet_type="emails",
        title="Emails",
        products=products,
        today_fmt=today.strftime("%B %d, %Y"),
        user=session.get("user"),
    )




def _open_workspace_sheet(pid, sheet_type):
    """BA / Emails product sheet page."""
    try:
        if sheet_type not in ("ba", "emails"):
            flash("Unknown sheet type.", "error")
            return redirect(url_for("dashboard"))
        row = fetch_product(pid)
        if not row:
            flash("Product not found.", "error")
            return redirect(url_for("ba_index" if sheet_type == "ba" else "emails_index"))
        product = enrich_product(row, fetch_ranks=False)
        sheet = get_or_create_sheet(pid, sheet_type)
        if not sheet:
            flash("Could not open sheet. Check database tables.", "error")
            return redirect(url_for("ba_index" if sheet_type == "ba" else "emails_index"))
        columns = fetch_sheet_columns(sheet["id"]) or []
        rows = fetch_sheet_rows(sheet["id"]) or []
        color_filter = (request.args.get("color") or "").strip().lower()
        if color_filter in ("green", "red", "orange", "none"):
            rows = [r for r in rows if (r.get("color") or "none") == color_filter]
        title = "Buyer accounts" if sheet_type == "ba" else "Email accounts"
        return render_template(
            "workspace_sheet.html",
            sheet_type=sheet_type,
            title=title,
            product=product,
            sheet=sheet,
            columns=columns,
            rows=rows,
            color_filter=color_filter,
            today_fmt=date.today().strftime("%B %d, %Y"),
            user=session.get("user"),
        )
    except Exception as e:
        print("workspace sheet error:", repr(e))
        import traceback
        traceback.print_exc()
        flash(f"Could not open sheet: {e}", "error")
        return redirect(url_for("ba_index" if sheet_type == "ba" else "emails_index"))


@app.route("/ba/<int:pid>")
@login_required
def ba_sheet(pid):
    return _open_workspace_sheet(pid, "ba")


@app.route("/emails/<int:pid>")
@login_required
def emails_sheet(pid):
    return _open_workspace_sheet(pid, "emails")





@app.route("/workspace/<int:sheet_id>/row", methods=["POST"])
@login_required
def workspace_add_row(sheet_id):
    add_sheet_row(sheet_id)
    log_activity(None, f"Added row to sheet {sheet_id}", action="workspace")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/workspace/<int:sheet_id>/column", methods=["POST"])
@login_required
def workspace_add_column(sheet_id):
    name = request.form.get("name", "").strip() or "New column"
    add_sheet_column(sheet_id, name)
    log_activity(None, f"Added column '{name}' to sheet {sheet_id}", action="workspace")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/workspace/row/<int:row_id>/cell", methods=["POST"])
@login_required
def workspace_save_cell(row_id):
    col_id = request.form.get("col_id")
    value = request.form.get("value", "")
    try:
        update_row_cell(row_id, col_id, value)
    except Exception as e:
        flash(f"Save failed: {e}", "error")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/workspace/row/<int:row_id>/color", methods=["POST"])
@login_required
def workspace_row_color(row_id):
    color = request.form.get("color", "none")
    update_row_color(row_id, color)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/workspace/row/<int:row_id>/delete", methods=["POST"])
@login_required
def workspace_delete_row(row_id):
    delete_sheet_row(row_id)
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/workspace/column/<int:col_id>/rename", methods=["POST"])
@login_required
def workspace_rename_column(col_id):
    name = request.form.get("name", "").strip()
    if name:
        rename_sheet_column(col_id, name)
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/workspace/column/<int:col_id>/delete", methods=["POST"])
@login_required
def workspace_delete_column(col_id):
    delete_sheet_column(col_id)
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/workspace/row/<int:row_id>/duplicate", methods=["POST"])
@login_required
def workspace_duplicate_row(row_id):
    duplicate_sheet_row(row_id)
    log_activity(None, f"Duplicated workspace row {row_id}", action="workspace")
    return redirect(request.referrer or url_for("dashboard"))


def _write_workspace_type_csv(sheet_type, filename):
    """Export all sheets of type ba or emails only."""
    import csv
    import io
    from flask import Response
    if sheet_type not in ("ba", "emails"):
        flash("Only Buyer accounts and Emails can be exported.", "error")
        return redirect(url_for("dashboard"))
    sb = get_db()
    if not sb:
        flash("Database not configured.", "error")
        return redirect(url_for("dashboard"))
    buf = io.StringIO()
    w = csv.writer(buf)
    status_map = {"green": "Done", "orange": "In progress", "red": "Blocked", "none": ""}
    try:
        sheets = (
            sb.table("workspace_sheets")
            .select("*, products(name, asin)")
            .eq("sheet_type", sheet_type)
            .execute()
            .data
            or []
        )
    except Exception:
        sheets = []
    label = "Buyer accounts" if sheet_type == "ba" else "Emails"
    w.writerow([f"LaunchPulse · {label} export"])
    if not sheets:
        w.writerow(["No sheets found"])
    for sheet in sheets:
        cols = fetch_sheet_columns(sheet["id"])
        rows = fetch_sheet_rows(sheet["id"])
        prod = sheet.get("products") or {}
        pname = prod.get("name") or sheet.get("product_id")
        pasin = prod.get("asin") or ""
        w.writerow([])
        w.writerow([f"Product: {pname}", f"ASIN: {pasin}", f"Type: {label}"])
        w.writerow(
            ["Color", "Status"]
            + [c["name"] for c in cols]
            + ["Last edited by", "Updated at"]
        )
        for r in rows:
            data = r.get("data") or {}
            color = r.get("color") or "none"
            cells = [data.get(str(c["id"]), data.get(c["id"], "")) for c in cols]
            w.writerow(
                [color, status_map.get(color, "")]
                + cells
                + [r.get("updated_by") or "", (r.get("updated_at") or "")[:19]]
            )
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/export/buyer-accounts.csv")
@login_required
def export_ba_csv():
    return _write_workspace_type_csv("ba", "buyer_accounts_export.csv")


@app.route("/export/emails.csv")
@login_required
def export_emails_csv():
    return _write_workspace_type_csv("emails", "emails_export.csv")


@app.route("/workspace/<int:sheet_id>/export.csv")
@login_required
def workspace_export_csv(sheet_id):
    """Per-sheet export — only BA or Emails sheets."""
    import csv
    import io
    from flask import Response
    sb = get_db()
    sheet = (
        sb.table("workspace_sheets")
        .select("*, products(name)")
        .eq("id", sheet_id)
        .single()
        .execute()
        .data
    )
    if not sheet or sheet.get("sheet_type") not in ("ba", "emails"):
        flash("Only Buyer accounts and Emails can be exported.", "error")
        return redirect(url_for("dashboard"))
    columns = fetch_sheet_columns(sheet_id)
    rows = fetch_sheet_rows(sheet_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["Color", "Status"] + [c["name"] for c in columns] + ["Last edited by", "Updated at"]
    w.writerow(header)
    status_map = {"green": "Done", "orange": "In progress", "red": "Blocked", "none": ""}
    for r in rows:
        data = r.get("data") or {}
        color = r.get("color") or "none"
        cells = [data.get(str(c["id"]), data.get(c["id"], "")) for c in columns]
        w.writerow(
            [color, status_map.get(color, "")]
            + cells
            + [r.get("updated_by") or "", (r.get("updated_at") or "")[:19]]
        )
    product_name = (sheet.get("products") or {}).get("name") or "sheet"
    st = sheet.get("sheet_type", "export")
    fname = f"{st}_{product_name}.csv".replace(" ", "_")
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@app.route("/workspace/search")
@login_required
def workspace_search():
    q = (request.args.get("q") or "").strip().lower()
    sheet_type = (request.args.get("type") or "all").strip().lower()
    results = []
    if q:
        sb = get_db()
        try:
            query = sb.table("workspace_sheets").select("*, products(name, id)")
            if sheet_type in ("ba", "emails"):
                query = query.eq("sheet_type", sheet_type)
            sheets = query.execute().data or []
        except Exception:
            sheets = []
        for sheet in sheets:
            cols = fetch_sheet_columns(sheet["id"])
            col_names = {str(c["id"]): c["name"] for c in cols}
            for row in fetch_sheet_rows(sheet["id"]):
                data = row.get("data") or {}
                blob = " ".join(str(v) for v in data.values()).lower()
                if q in blob or q in ((sheet.get("products") or {}).get("name") or "").lower():
                    results.append({
                        "sheet": sheet,
                        "row": row,
                        "columns": cols,
                        "col_names": col_names,
                        "product": sheet.get("products") or {},
                    })
    return render_template(
        "workspace_search.html",
        q=q,
        sheet_type=sheet_type,
        results=results,
        today_fmt=date.today().strftime("%B %d, %Y"),
        user=session.get("user"),
    )



@app.route("/mfa", methods=["GET", "POST"])
def mfa_verify():
    pending = session.get("mfa_pending")
    if not pending:
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        email = pending.get("email") or ""
        if is_login_rate_limited(email):
            error = login_rate_limit_message()
        else:
            profile = get_profile(pending["id"])
            secret = (profile or {}).get("totp_secret") or ""
            if verify_totp(secret, code):
                clear_login_failures(email)
                session.pop("mfa_pending", None)
                session["user"] = {
                    "id": pending["id"],
                    "email": pending["email"],
                    "role": pending.get("role") or "user",
                }
                session["access_token"] = pending.get("access_token")
                session["refresh_token"] = pending.get("refresh_token")
                seed_if_empty()
                flash(f"Welcome back, {pending['email']}!", "success")
                return redirect(url_for("dashboard"))
            record_login_failure(email)
            error = "Invalid authentication code."
    return render_template(
        "mfa.html",
        error=error,
        email=pending.get("email"),
        today_fmt=date.today().strftime("%B %d, %Y"),
    )


@app.route("/account/2fa", methods=["GET", "POST"])
@login_required
def account_2fa():
    user = session.get("user") or {}
    profile = get_profile(user.get("id")) or {}
    error = None
    provisioning_uri = None
    secret = profile.get("totp_secret") or ""
    enabled = bool(profile.get("totp_enabled"))

    if request.method == "POST":
        action = request.form.get("action")
        try:
            import pyotp
        except ImportError:
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "pyotp", "-q"])
            import pyotp

        if action == "start":
            secret = pyotp.random_base32()
            session["totp_setup_secret"] = secret
            try:
                set_profile_totp(user["id"], secret, enabled=False)
            except Exception as e:
                print("set_profile_totp start error:", e)
                # Still allow setup via session secret
            flash("Add the secret to your authenticator app, then enter a code below.", "success")
            return redirect(url_for("account_2fa"))
        if action == "enable":
            code = request.form.get("code", "").strip()
            secret = (
                request.form.get("secret")
                or session.get("totp_setup_secret")
                or profile.get("totp_secret")
                or ""
            ).strip()
            if not secret:
                error = "No setup secret found. Click Set up 2FA again."
            elif verify_totp(secret, code):
                try:
                    set_profile_totp(user["id"], secret, enabled=True)
                except Exception as e:
                    error = f"Code OK but could not save (run SQL for totp columns?): {e}"
                else:
                    session.pop("totp_setup_secret", None)
                    flash("2FA enabled.", "success")
                    return redirect(url_for("account_2fa"))
            else:
                error = "Invalid code. Wait for a new code and try again. Check phone time is automatic."
        if action == "disable":
            code = request.form.get("code", "").strip()
            secret = profile.get("totp_secret") or ""
            if verify_totp(secret, code) or not enabled:
                set_profile_totp(user["id"], "", enabled=False)
                session.pop("totp_setup_secret", None)
                flash("2FA disabled.", "success")
                return redirect(url_for("account_2fa"))
            error = "Invalid code."

    profile = get_profile(user.get("id")) or {}
    secret = (profile.get("totp_secret") or session.get("totp_setup_secret") or "").strip()
    enabled = bool(profile.get("totp_enabled"))
    if secret and not enabled:
        try:
            import pyotp
            provisioning_uri = pyotp.TOTP(secret).provisioning_uri(
                name=user.get("email") or "user",
                issuer_name="LaunchPulse",
            )
        except Exception:
            provisioning_uri = None

    return render_template(
        "account_2fa.html",
        enabled=enabled,
        secret=secret if secret and not enabled else None,
        provisioning_uri=provisioning_uri,
        error=error,
        today_fmt=date.today().strftime("%B %d, %Y"),
        user=user,
    )






@app.route("/api/pulse")
@login_required
def api_pulse():
    """Rank drop/gain alerts across products with ASINs (SoldScope)."""
    if not soldscope_configured():
        return jsonify({"ok": True, "alerts": [], "message": "SoldScope not configured"})
    rows = fetch_products()
    alerts = []
    for row in rows[:12]:
        asin = (row.get("asin") or "").strip()
        if not asin:
            continue
        try:
            ranks = cockpit_ranks_for_asin(asin, limit=40)
            for a in compute_rank_alerts(ranks, drop_threshold=5)[:3]:
                a["product"] = row.get("name")
                a["product_id"] = row.get("id")
                a["asin"] = asin
                alerts.append(a)
        except Exception as e:
            print("pulse error", asin, e)
    alerts.sort(key=lambda a: abs(a.get("delta") or 0), reverse=True)
    return jsonify({"ok": True, "alerts": alerts[:12]})


@app.route("/api/product/<int:pid>/insights")
@login_required
def api_product_insights(pid):
    """Share-of-shelf + BSR sparkline + rank alerts for one product."""
    row = fetch_product(pid)
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    asin = (row.get("asin") or "").strip()
    out = {
        "ok": True,
        "asin": asin,
        "share_of_shelf": {"owned": 0, "total": 0, "pct": None},
        "alerts": [],
        "bsr_points": [],
        "bsr_svg": "",
        "ranks": [],
        "empty_hints": [],
    }
    if not asin:
        out["empty_hints"].append({"title": "Add an ASIN", "body": "Edit this product and add its Amazon ASIN to unlock ranks and BSR.", "cta": "Edit product", "url": url_for("product_edit", pid=pid)})
        return jsonify(out)
    if not soldscope_configured():
        out["empty_hints"].append({"title": "Connect SoldScope", "body": "Set SOLDSCOPE_API_TOKEN on the server to load organic ranks and BSR.", "cta": "Home", "url": url_for("dashboard")})
        return jsonify(out)
    try:
        ranks = cockpit_ranks_for_asin(asin, limit=80)
        out["ranks"] = ranks
        out["share_of_shelf"] = compute_share_of_shelf(ranks)
        out["alerts"] = compute_rank_alerts(ranks, drop_threshold=5)[:8]
        pts = bsr_sparkline_points(asin, days=30)
        out["bsr_points"] = pts
        out["bsr_svg"] = sparkline_svg(pts)
        if not ranks:
            out["empty_hints"].append({
                "title": "No rank tracker for this ASIN",
                "body": "Create a SoldScope rank tracker group with keywords for this ASIN.",
                "cta": "Home",
                "url": url_for("dashboard"),
            })
    except Exception as e:
        out["empty_hints"].append({"title": "Could not load SoldScope data", "body": str(e), "cta": "Home", "url": url_for("dashboard")})
    return jsonify(out)


@app.route("/compare")
@login_required
def compare_products():
    today = date.today()
    try:
        rows = fetch_products() or []
        products = [enrich_product(r, today) for r in rows if r]
        a_id = request.args.get("a", type=int)
        b_id = request.args.get("b", type=int)
        left = right = None
        if a_id:
            row_a = fetch_product(a_id)
            left = enrich_product(row_a, today, fetch_ranks=False) if row_a else None
        if b_id:
            row_b = fetch_product(b_id)
            right = enrich_product(row_b, today, fetch_ranks=False) if row_b else None
        return render_template(
            "compare.html",
            products=products,
            left=left,
            right=right,
            a_id=a_id,
            b_id=b_id,
            today_fmt=today.strftime("%B %d, %Y"),
            user=session.get("user"),
        )
    except Exception as e:
        print("compare_products error:", repr(e))
        import traceback
        traceback.print_exc()
        flash(f"Compare failed: {e}", "error")
        return redirect(url_for("dashboard"))



@app.route("/api/product/<int:pid>/prelaunch", methods=["POST"])
@login_required
def api_prelaunch_update(pid):
    """Update one pre-launch step status. JSON: {step, status, note?}"""
    row = fetch_product(pid)
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    step_key = (data.get("step") or request.form.get("step") or "").strip()
    status = (data.get("status") or request.form.get("status") or "todo").strip().lower()
    note = (data.get("note") or request.form.get("note") or "").strip()[:500]
    valid_keys = {s["key"] for s in PRE_LAUNCH_STEPS}
    if step_key not in valid_keys:
        return jsonify({"ok": False, "error": "invalid step"}), 400
    if status not in ("todo", "doing", "done"):
        return jsonify({"ok": False, "error": "invalid status"}), 400
    current = normalize_prelaunch(row.get("prelaunch"))
    current[step_key] = {"status": status, "note": note or current.get(step_key, {}).get("note") or ""}
    try:
        update_product(pid, prelaunch=current)
        log_activity(pid, f'Pre-launch "{step_key}" → {status}', action="prelaunch")
        return jsonify({"ok": True, "prelaunch": current})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/product/<int:pid>/keyword-ranks")
@login_required
def api_keyword_ranks(pid):
    """Return phase keywords with SoldScope organic ranks for one product."""
    row = fetch_product(pid)
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    asin = (row.get("asin") or "").strip()
    km = fetch_phase_keywords(pid)
    phase = get_phase(row.get("launch_date")).get("phase") or 0
    current_kw = (km.get(phase) or km.get(str(phase)) or "").strip()
    # merge all phase keywords for matching
    all_kw_text = current_kw
    for txt in km.values():
        if txt and txt not in all_kw_text:
            all_kw_text = (all_kw_text + ", " + txt) if all_kw_text else txt
    ranked = []
    ranks_raw = []
    error = None
    if not soldscope_configured():
        error = "SOLDSCOPE_API_TOKEN not set"
        ranked = [{"keyword": k, "organic": None} for k in parse_keyword_list(current_kw or all_kw_text)]
    elif not asin:
        error = "Product has no ASIN"
        ranked = [{"keyword": k, "organic": None} for k in parse_keyword_list(current_kw or all_kw_text)]
    else:
        try:
            ranks_raw = cockpit_ranks_for_asin(asin, limit=100)
            ranked = attach_ranks_to_keywords(current_kw or all_kw_text, asin)
            if not ranked and ranks_raw:
                ranked = [
                    {"keyword": r["phrase"], "organic": r.get("organic")}
                    for r in ranks_raw if r.get("phrase")
                ]
            if not ranks_raw:
                error = (
                    error
                    or f"No SoldScope rank tracker found for ASIN {asin}. "
                       "Create a Rank Tracker group for this ASIN in SoldScope."
                )
            elif ranked and all(r.get("organic") is None for r in ranked) and ranks_raw:
                # Product keywords don't match tracker phrases — still expose tracker ranks
                ranked = [
                    {"keyword": r["phrase"], "organic": r.get("organic")}
                    for r in ranks_raw if r.get("phrase")
                ][:30]
        except Exception as e:
            error = str(e)
            ranked = [{"keyword": k, "organic": None} for k in parse_keyword_list(current_kw or all_kw_text)]
    # Rank map only from THIS ASIN's tracker phrases
    if ranks_raw:
        rank_map = {
            _norm_kw(r["phrase"]): r.get("organic")
            for r in ranks_raw if r.get("phrase")
        }
        # Re-attach so product keywords get correct ranks
        src = current_kw or all_kw_text
        if src:
            ranked = attach_ranks_to_keywords(src, asin, rank_map=rank_map)
        else:
            # No phase keywords saved — show this ASIN's tracked SoldScope phrases only
            ranked = [
                {"keyword": r.get("phrase"), "organic": r.get("organic")}
                for r in ranks_raw if r.get("phrase")
            ]
    sos = compute_share_of_shelf(ranks_raw)
    alerts = compute_rank_alerts(ranks_raw, drop_threshold=5)[:6]
    bsr_pts = bsr_sparkline_points(asin) if asin and soldscope_configured() else []
    with_rank = sum(1 for r in ranked if r.get("organic") is not None)
    return jsonify({
        "ok": True,
        "asin": asin,
        "keywords": ranked,
        "soldscope_phrases": ranks_raw[:40],
        "soldscope_configured": soldscope_configured(),
        "soldscope_phrase_count": len(ranks_raw),
        "keywords_with_rank": with_rank,
        "error": error,
        "share_of_shelf": sos,
        "alerts": alerts,
        "bsr_points": bsr_pts,
        "bsr_svg": sparkline_svg(bsr_pts),
    })



@app.route("/api/cockpit/<int:pid>")
@login_required
def api_cockpit(pid):
    row = fetch_product(pid)
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    product = enrich_product(row, fetch_ranks=True)
    ranks = []
    ranks_error = None
    if product.get("asin") and soldscope_configured():
        try:
            ranks = cockpit_ranks_for_asin(product["asin"])
        except Exception as e:
            ranks_error = str(e)
    # heatmap data: days 1-31 planned vs actual
    planned = product.get("daily_buys") or {}
    actual = product.get("daily_buys_actual") or {}
    heat = []
    for d in range(1, 32):
        p = int(planned.get(str(d), 0) or 0)
        a = int(actual.get(str(d), 0) or 0)
        heat.append({"day": d, "planned": p, "actual": a})
    return jsonify({
        "ok": True,
        "product": {
            "id": product.get("id"),
            "name": product.get("name"),
            "asin": product.get("asin"),
            "price_fmt": product.get("price_fmt"),
            "thumbnail_url": product.get("thumbnail_url"),
            "label": product.get("label"),
            "phase": product.get("phase"),
            "progress": product.get("progress"),
            "days_left": product.get("days_left"),
            "launch_fmt": product.get("launch_fmt"),
            "today_buys": product.get("today_buys"),
            "today_buys_actual": product.get("today_buys_actual"),
            "buys_on_track": product.get("buys_on_track"),
            "detail_url": url_for("product_detail", pid=pid),
            "edit_url": url_for("product_edit", pid=pid),
                        "ba_url": url_for("ba_sheet", pid=pid),
        },
        "ranks": ranks,
        "ranks_error": ranks_error,
        "heatmap": heat,
        "soldscope": soldscope_configured(),
    })







@app.route("/admin")
@admin_required
def admin_home():
    profiles = list_profiles()
    activity = fetch_activity(100)
    products = fetch_products()
    return render_template(
        "admin.html",
        profiles=profiles,
        activity=activity,
        product_count=len(products),
        today_fmt=date.today().strftime("%B %d, %Y"),
        user=session.get("user"),
    )


@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "user")
        if not email or not password:
            error = "Email and password are required."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        else:
            try:
                create_user_account(email, password, role=role)
                log_activity(None, f"Admin created account {email} (role={role})", action="user_create")
                flash(f"Account created for {email}.", "success")
                return redirect(url_for("admin_users"))
            except Exception as e:
                error = str(e)
    profiles = list_profiles()
    return render_template(
        "admin_users.html",
        profiles=profiles,
        error=error,
        today_fmt=date.today().strftime("%B %d, %Y"),
        user=session.get("user"),
    )


@app.route("/admin/users/<uid>/role", methods=["POST"])
@admin_required
def admin_set_role(uid):
    role = request.form.get("role", "user")
    if role not in ("admin", "user"):
        flash("Invalid role.", "error")
        return redirect(url_for("admin_users"))
    sb = get_db()
    try:
        sb.table("profiles").update({"role": role}).eq("id", uid).execute()
        log_activity(None, f"Set role={role} for user {uid}", action="role_change")
        flash("Role updated.", "success")
    except Exception as e:
        flash(f"Could not update role: {e}", "error")
    return redirect(url_for("admin_users"))


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n  🍊  LaunchPulse (Supabase)")
    if supabase_configured():
        print(f"  →  Supabase: {SUPABASE_URL[:48]}...")
    else:
        print("  ⚠  SUPABASE_URL / SUPABASE_KEY not set — copy .env.example → .env")
    if ADMIN_EMAIL:
        print(f"  →  Admin email: {ADMIN_EMAIL}")
    print("  →  http://0.0.0.0:5050\n")
    app.run(host="0.0.0.0", port=5050, debug=True)
