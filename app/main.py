import base64
import hashlib
import io
import secrets
from datetime import datetime, timedelta
from pathlib import Path

import pyotp
import qrcode

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import FastAPI, Form, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from .database import app_db
from .remote_storage import browse as storage_browse, integrity_test as storage_integrity_test, encrypt_secret
from .storage_registry import list_targets, get_target, health_check
from .metrics import collect_dashboard
from .monitoring import send_channel
from .explorer import search_nat, ExplorerError
from .advanced_search import search_logs, AdvancedSearchError
from .export_search import (
    generate_advanced_export,
    ExportError,
    EXPORT_ROOT,
    EXPORT_MAX_AGE_HOURS,
)
from .security import (
    csrf_token,
    csrf_valid,
    create_mfa_setup_token,
    read_mfa_setup_token,
)


app = FastAPI(
    title="Network Log Manager",
    docs_url=None,
    redoc_url=None,
)

app.mount(
    "/static",
    StaticFiles(directory="/opt/netlog-manager/static"),
    name="static",
)

templates = Jinja2Templates(
    directory="/opt/netlog-manager/templates"
)

ph = PasswordHasher()

COOKIE_NAME = "netlog_session"
MFA_CHALLENGE_COOKIE = "netlog_mfa_challenge"


def token_hash(token: str):
    return hashlib.sha256(token.encode()).digest()


def client_ip(request: Request):
    return request.client.host if request.client else None


def get_setting_int(cur, key: str, default: int):
    cur.execute(
        """
        SELECT setting_value
        FROM settings
        WHERE setting_key=%s
        LIMIT 1
        """,
        (key,),
    )

    row = cur.fetchone()

    if not row:
        return default

    try:
        value = int(row["setting_value"])
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def write_audit(
    cur,
    *,
    user_id=None,
    username=None,
    action,
    category,
    request: Request,
    success=True,
    target_type=None,
    target_id=None,
    details=None,
):
    cur.execute(
        """
        INSERT INTO audit_log
        (
            user_id,
            username,
            action,
            category,
            ip_address,
            user_agent,
            target_type,
            target_id,
            success,
            details
        )
        VALUES
        (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            user_id,
            username,
            action,
            category,
            client_ip(request),
            request.headers.get("user-agent"),
            target_type,
            target_id,
            1 if success else 0,
            details,
        ),
    )


def get_session(request: Request):
    token = request.cookies.get(COOKIE_NAME)

    if not token:
        return None

    digest = token_hash(token)

    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    s.id AS session_id,
                    s.user_id,
                    s.last_seen_at,
                    s.expires_at,
                    u.username,
                    u.display_name,
                    GROUP_CONCAT(
                        DISTINCT r.name
                        ORDER BY r.name
                        SEPARATOR ','
                    ) AS roles
                FROM sessions s
                JOIN users u
                    ON u.id=s.user_id
                LEFT JOIN user_roles ur
                    ON ur.user_id=u.id
                LEFT JOIN roles r
                    ON r.id=ur.role_id
                WHERE s.token_hash=%s
                  AND s.revoked_at IS NULL
                  AND s.expires_at > NOW()
                  AND u.enabled=1
                GROUP BY
                    s.id,
                    s.user_id,
                    s.last_seen_at,
                    s.expires_at,
                    u.username,
                    u.display_name
                LIMIT 1
                """,
                (digest,),
            )

            session = cur.fetchone()

            if not session:
                return None

            # Evitiamo una UPDATE ad ogni singola richiesta.
            # Aggiorniamo last_seen al massimo una volta al minuto.
            last_seen = session.get("last_seen_at")

            if (
                last_seen is None
                or last_seen < datetime.now() - timedelta(minutes=1)
            ):
                cur.execute(
                    """
                    UPDATE sessions
                    SET last_seen_at=NOW()
                    WHERE id=%s
                    """,
                    (session["session_id"],),
                )
                conn.commit()

            roles = session.get("roles") or ""
            session["roles"] = [
                role
                for role in roles.split(",")
                if role
            ]

            return session

    finally:
        conn.close()





def valid_form_csrf(request: Request, csrf: str):
    raw_token = request.cookies.get(COOKIE_NAME)

    return bool(
        raw_token
        and csrf_valid(raw_token, csrf)
    )



def has_role(session, *allowed_roles):
    """
    Restituisce True se la sessione possiede
    almeno uno dei ruoli indicati.
    """
    if not session:
        return False

    roles = set(session.get("roles") or [])

    return bool(
        roles.intersection(allowed_roles)
    )


def is_administrator(session):
    return has_role(
        session,
        "Administrator",
    )


def export_file_state(job):
    """
    Restituisce:
      available
      expired
      missing
      integrity_failed
      unavailable
    """

    if (
        not job
        or job.get("status") != "COMPLETED"
        or not job.get("filename")
    ):
        return "unavailable"

    completed_at = job.get("completed_at")

    if completed_at is not None:
        expires_at = completed_at + timedelta(
            hours=EXPORT_MAX_AGE_HOURS
        )

        if datetime.now() >= expires_at:
            return "expired"

    expected = (
        f"network-log-export-job-"
        f"{job['id']}.csv"
    )

    if job["filename"] != expected:
        return "missing"

    export_path = EXPORT_ROOT / expected

    try:
        if not export_path.is_file():
            return "missing"

        if (
            job.get("file_size") is not None
            and export_path.stat().st_size
            != job["file_size"]
        ):
            return "integrity_failed"

    except OSError:
        return "missing"

    return "available"


def can_export(session):
    return has_role(
        session,
        "Administrator",
        "Operator",
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_session(request):
        return RedirectResponse("/", status_code=303)

    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None},
    )

    response.delete_cookie(
        MFA_CHALLENGE_COOKIE,
        path="/login",
    )

    return response


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    username = username.strip()

    conn = app_db()

    try:
        with conn.cursor() as cur:

            failed_limit = get_setting_int(
                cur,
                "failed_login_limit",
                5,
            )

            lock_minutes = get_setting_int(
                cur,
                "failed_login_lock_minutes",
                15,
            )

            session_minutes = get_setting_int(
                cur,
                "session_timeout_minutes",
                60,
            )

            cur.execute(
                """
                SELECT
                    id,
                    username,
                    password_hash,
                    enabled,
                    failed_login_count,
                    locked_until,
                    mfa_enabled,
                    mfa_secret
                FROM users
                WHERE username=%s
                LIMIT 1
                """,
                (username,),
            )

            user = cur.fetchone()

            valid = False
            locked = False

            if user and user["enabled"]:
                locked = bool(
                    user["locked_until"]
                    and user["locked_until"] > datetime.now()
                )

                if not locked:
                    try:
                        valid = ph.verify(
                            user["password_hash"],
                            password,
                        )
                    except VerifyMismatchError:
                        valid = False
                    except Exception:
                        valid = False

            if not valid:

                if user and user["enabled"] and not locked:
                    new_count = (
                        int(user["failed_login_count"] or 0) + 1
                    )

                    if new_count >= failed_limit:
                        cur.execute(
                            """
                            UPDATE users
                            SET
                                failed_login_count=%s,
                                locked_until=
                                    DATE_ADD(
                                        NOW(),
                                        INTERVAL %s MINUTE
                                    )
                            WHERE id=%s
                            """,
                            (
                                new_count,
                                lock_minutes,
                                user["id"],
                            ),
                        )

                        audit_action = "ACCOUNT_LOCKED"

                    else:
                        cur.execute(
                            """
                            UPDATE users
                            SET failed_login_count=%s
                            WHERE id=%s
                            """,
                            (
                                new_count,
                                user["id"],
                            ),
                        )

                        audit_action = "LOGIN_FAILED"

                elif locked:
                    audit_action = "LOGIN_BLOCKED"

                else:
                    audit_action = "LOGIN_FAILED"

                write_audit(
                    cur,
                    user_id=user["id"] if user else None,
                    username=username,
                    action=audit_action,
                    category="AUTH",
                    request=request,
                    success=False,
                )

                conn.commit()

                return templates.TemplateResponse(
                    request=request,
                    name="login.html",
                    context={
                        "error": "Credenziali non valide."
                    },
                    status_code=401,
                )

            #
            # Password valida, ma con MFA attivo
            # NON creiamo ancora una sessione.
            #
            if user["mfa_enabled"]:

                challenge_token = (
                    secrets.token_urlsafe(48)
                )

                challenge_digest = token_hash(
                    challenge_token
                )

                #
                # Invalidiamo eventuali challenge
                # precedenti ancora aperte.
                #
                cur.execute(
                    """
                    UPDATE mfa_challenges
                    SET used_at=NOW()
                    WHERE user_id=%s
                      AND purpose='LOGIN'
                      AND used_at IS NULL
                    """,
                    (user["id"],),
                )

                cur.execute(
                    """
                    INSERT INTO mfa_challenges
                    (
                        user_id,
                        token_hash,
                        purpose,
                        attempts,
                        max_attempts,
                        ip_address,
                        user_agent,
                        expires_at
                    )
                    VALUES (
                        %s,%s,'LOGIN',0,5,%s,%s,
                        DATE_ADD(NOW(), INTERVAL 5 MINUTE)
                    )
                    """,
                    (
                        user["id"],
                        challenge_digest,
                        client_ip(request),
                        request.headers.get(
                            "user-agent"
                        ),
                    ),
                )

                write_audit(
                    cur,
                    user_id=user["id"],
                    username=user["username"],
                    action="MFA_LOGIN_CHALLENGE",
                    category="AUTH",
                    request=request,
                    success=True,
                    target_type="USER",
                    target_id=str(user["id"]),
                    details=(
                        "Password verified; "
                        "TOTP challenge required"
                    ),
                )

                conn.commit()

                response = RedirectResponse(
                    "/login/2fa",
                    status_code=303,
                )

                response.set_cookie(
                    MFA_CHALLENGE_COOKIE,
                    challenge_token,
                    httponly=True,
                    secure=True,
                    samesite="strict",
                    max_age=300,
                    path="/login",
                )

                return response

            raw_token = secrets.token_urlsafe(48)
            digest = token_hash(raw_token)

            expires_at = (
                datetime.now()
                + timedelta(minutes=session_minutes)
            )

            cur.execute(
                """
                INSERT INTO sessions
                (
                    user_id,
                    token_hash,
                    ip_address,
                    user_agent,
                    expires_at
                )
                VALUES (%s,%s,%s,%s,%s)
                """,
                (
                    user["id"],
                    digest,
                    client_ip(request),
                    request.headers.get("user-agent"),
                    expires_at,
                ),
            )

            cur.execute(
                """
                UPDATE users
                SET
                    failed_login_count=0,
                    locked_until=NULL,
                    last_login_at=NOW()
                WHERE id=%s
                """,
                (user["id"],),
            )

            write_audit(
                cur,
                user_id=user["id"],
                username=user["username"],
                action="LOGIN_SUCCESS",
                category="AUTH",
                request=request,
                success=True,
            )

            conn.commit()

        response = RedirectResponse(
            "/",
            status_code=303,
        )

        response.set_cookie(
            COOKIE_NAME,
            raw_token,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=session_minutes * 60,
            path="/",
        )

        return response

    finally:
        conn.close()



@app.get("/login/2fa", response_class=HTMLResponse)
def login_2fa_page(request: Request):
    if get_session(request):
        return RedirectResponse(
            "/",
            status_code=303,
        )

    challenge_token = request.cookies.get(
        MFA_CHALLENGE_COOKIE
    )

    if not challenge_token:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    digest = token_hash(challenge_token)

    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.id,
                    c.user_id,
                    c.attempts,
                    c.max_attempts,
                    c.expires_at,
                    c.used_at,
                    c.ip_address,
                    c.user_agent,
                    u.username,
                    u.enabled,
                    u.mfa_enabled,
                    u.locked_until
                FROM mfa_challenges c
                JOIN users u
                    ON u.id=c.user_id
                WHERE c.token_hash=%s
                  AND c.purpose='LOGIN'
                LIMIT 1
                """,
                (digest,),
            )

            challenge = cur.fetchone()

    finally:
        conn.close()

    valid = bool(
        challenge
        and challenge["enabled"]
        and challenge["mfa_enabled"]
        and challenge["used_at"] is None
        and challenge["expires_at"] > datetime.now()
        and int(challenge["attempts"])
            < int(challenge["max_attempts"])
        and challenge["ip_address"]
            == client_ip(request)
        and challenge["user_agent"]
            == request.headers.get("user-agent")
        and not (
            challenge["locked_until"]
            and challenge["locked_until"]
                > datetime.now()
        )
    )

    if not valid:
        response = RedirectResponse(
            "/login",
            status_code=303,
        )

        response.delete_cookie(
            MFA_CHALLENGE_COOKIE,
            path="/login",
        )

        return response

    return templates.TemplateResponse(
        request=request,
        name="login_2fa.html",
        context={
            "error": None,
        },
    )


@app.post("/login/2fa", response_class=HTMLResponse)
def login_2fa_verify(
    request: Request,
    code: str = Form(...),
):
    if get_session(request):
        return RedirectResponse(
            "/",
            status_code=303,
        )

    challenge_token = request.cookies.get(
        MFA_CHALLENGE_COOKIE
    )

    if not challenge_token:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    digest = token_hash(challenge_token)
    code = code.strip().replace(" ", "")

    conn = app_db()

    try:
        with conn.cursor() as cur:

            #
            # Lockiamo la challenge: in questo modo
            # non può essere consumata due volte
            # contemporaneamente.
            #
            cur.execute(
                """
                SELECT
                    c.id,
                    c.user_id,
                    c.attempts,
                    c.max_attempts,
                    c.expires_at,
                    c.used_at,
                    c.ip_address,
                    c.user_agent,
                    u.username,
                    u.enabled,
                    u.mfa_enabled,
                    u.mfa_secret,
                    u.failed_login_count,
                    u.locked_until
                FROM mfa_challenges c
                JOIN users u
                    ON u.id=c.user_id
                WHERE c.token_hash=%s
                  AND c.purpose='LOGIN'
                LIMIT 1
                FOR UPDATE
                """,
                (digest,),
            )

            challenge = cur.fetchone()

            valid_challenge = bool(
                challenge
                and challenge["enabled"]
                and challenge["mfa_enabled"]
                and challenge["used_at"] is None
                and challenge["expires_at"] > datetime.now()
                and int(challenge["attempts"])
                    < int(challenge["max_attempts"])
                and challenge["ip_address"]
                    == client_ip(request)
                and challenge["user_agent"]
                    == request.headers.get("user-agent")
                and not (
                    challenge["locked_until"]
                    and challenge["locked_until"]
                        > datetime.now()
                )
            )

            if not valid_challenge:
                conn.rollback()

                response = RedirectResponse(
                    "/login",
                    status_code=303,
                )

                response.delete_cookie(
                    MFA_CHALLENGE_COOKIE,
                    path="/login",
                )

                return response

            secret = challenge["mfa_secret"]

            if isinstance(secret, bytes):
                secret = secret.decode("ascii")

            valid_totp = bool(
                secret
                and code.isdigit()
                and len(code) == 6
                and pyotp.TOTP(secret).verify(
                    code,
                    valid_window=1,
                )
            )

            if not valid_totp:
                new_attempts = (
                    int(challenge["attempts"]) + 1
                )

                failed_limit = get_setting_int(
                    cur,
                    "failed_login_limit",
                    5,
                )

                lock_minutes = get_setting_int(
                    cur,
                    "failed_login_lock_minutes",
                    15,
                )

                new_failed_count = (
                    int(
                        challenge[
                            "failed_login_count"
                        ] or 0
                    ) + 1
                )

                exhausted = (
                    new_attempts
                    >= int(challenge["max_attempts"])
                )

                account_locked = (
                    new_failed_count >= failed_limit
                )

                cur.execute(
                    """
                    UPDATE mfa_challenges
                    SET
                        attempts=%s,
                        used_at=CASE
                            WHEN %s=1 THEN NOW()
                            ELSE used_at
                        END
                    WHERE id=%s
                    """,
                    (
                        new_attempts,
                        1 if exhausted else 0,
                        challenge["id"],
                    ),
                )

                if account_locked:
                    cur.execute(
                        """
                        UPDATE users
                        SET
                            failed_login_count=%s,
                            locked_until=DATE_ADD(
                                NOW(),
                                INTERVAL %s MINUTE
                            )
                        WHERE id=%s
                        """,
                        (
                            new_failed_count,
                            lock_minutes,
                            challenge["user_id"],
                        ),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE users
                        SET failed_login_count=%s
                        WHERE id=%s
                        """,
                        (
                            new_failed_count,
                            challenge["user_id"],
                        ),
                    )

                write_audit(
                    cur,
                    user_id=challenge["user_id"],
                    username=challenge["username"],
                    action="MFA_LOGIN_FAILED",
                    category="AUTH",
                    request=request,
                    success=False,
                    target_type="USER",
                    target_id=str(
                        challenge["user_id"]
                    ),
                    details=(
                        "MFA login challenge failed"
                    ),
                )

                conn.commit()

                if exhausted:
                    response = RedirectResponse(
                        "/login",
                        status_code=303,
                    )

                    response.delete_cookie(
                        MFA_CHALLENGE_COOKIE,
                        path="/login",
                    )

                    return response

                return templates.TemplateResponse(
                    request=request,
                    name="login_2fa.html",
                    context={
                        "error":
                            "Codice non valido."
                    },
                    status_code=401,
                )

            session_minutes = get_setting_int(
                cur,
                "session_timeout_minutes",
                60,
            )

            raw_token = secrets.token_urlsafe(48)
            session_digest = token_hash(raw_token)

            expires_at = (
                datetime.now()
                + timedelta(
                    minutes=session_minutes
                )
            )

            cur.execute(
                """
                INSERT INTO sessions
                (
                    user_id,
                    token_hash,
                    ip_address,
                    user_agent,
                    expires_at
                )
                VALUES (%s,%s,%s,%s,%s)
                """,
                (
                    challenge["user_id"],
                    session_digest,
                    client_ip(request),
                    request.headers.get(
                        "user-agent"
                    ),
                    expires_at,
                ),
            )

            cur.execute(
                """
                UPDATE mfa_challenges
                SET used_at=NOW()
                WHERE id=%s
                """,
                (challenge["id"],),
            )

            cur.execute(
                """
                UPDATE users
                SET
                    failed_login_count=0,
                    locked_until=NULL,
                    last_login_at=NOW()
                WHERE id=%s
                """,
                (challenge["user_id"],),
            )

            write_audit(
                cur,
                user_id=challenge["user_id"],
                username=challenge["username"],
                action="LOGIN_SUCCESS",
                category="AUTH",
                request=request,
                success=True,
                target_type="USER",
                target_id=str(
                    challenge["user_id"]
                ),
                details="Login completed with TOTP",
            )

            conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    response = RedirectResponse(
        "/",
        status_code=303,
    )

    response.set_cookie(
        COOKIE_NAME,
        raw_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=session_minutes * 60,
        path="/",
    )

    response.delete_cookie(
        MFA_CHALLENGE_COOKIE,
        path="/login",
    )

    return response


@app.post("/logout")
def logout(
    request: Request,
    csrf: str = Form(...),
):
    token = request.cookies.get(COOKIE_NAME)

    if not token or not csrf_valid(token, csrf):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    if token:
        conn = app_db()

        try:
            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT
                        s.id AS session_id,
                        s.user_id,
                        u.username
                    FROM sessions s
                    JOIN users u
                        ON u.id=s.user_id
                    WHERE s.token_hash=%s
                    LIMIT 1
                    """,
                    (token_hash(token),),
                )

                session = cur.fetchone()

                cur.execute(
                    """
                    UPDATE sessions
                    SET revoked_at=NOW()
                    WHERE token_hash=%s
                      AND revoked_at IS NULL
                    """,
                    (token_hash(token),),
                )

                if session:
                    write_audit(
                        cur,
                        user_id=session["user_id"],
                        username=session["username"],
                        action="LOGOUT",
                        category="AUTH",
                        request=request,
                        success=True,
                    )

            conn.commit()

        finally:
            conn.close()

    response = RedirectResponse(
        "/login",
        status_code=303,
    )

    response.delete_cookie(
        COOKIE_NAME,
        path="/",
    )

    return response


@app.get("/api/dashboard")
def dashboard_api(request: Request):
    session = get_session(request)

    if not session:
        return JSONResponse(
            {"detail": "Unauthorized"},
            status_code=401,
        )

    return JSONResponse(
        content=jsonable_encoder(
            collect_dashboard()
        )
    )



@app.get("/explorer", response_class=HTMLResponse)
def explorer_page(request: Request):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    raw_token = request.cookies.get(COOKIE_NAME)

    now = datetime.now()
    default_start = now - timedelta(minutes=15)

    start_value = default_start.strftime(
        "%Y-%m-%dT%H:%M"
    )
    end_value = now.strftime(
        "%Y-%m-%dT%H:%M"
    )

    return templates.TemplateResponse(
        request=request,
        name="explorer.html",
        context={
            "session": session,
            "can_export": can_export(session),
            "csrf_token": csrf_token(raw_token),

            "results": None,
            "error": None,
            "form": {
                "public_ip": "",
                "public_port": "",
                "start": start_value,
                "end": end_value,
                "protocol": "",
            },

            "advanced_results": None,
            "advanced_error": None,
            "advanced_form": {
                "start": start_value,
                "end": end_value,
                "source_ip": "",
                "source_port": "",
                "nat_source_ip": "",
                "nat_source_port": "",
                "dest_ip": "",
                "dest_port": "",
                "protocol": "",
            },

            "active_mode": "nat",
        },
    )


@app.post("/explorer", response_class=HTMLResponse)
def explorer_search(
    request: Request,
    csrf: str = Form(...),
    public_ip: str = Form(...),
    public_port: str = Form(...),
    start: str = Form(...),
    end: str = Form(...),
    protocol: str = Form(""),
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    raw_token = request.cookies.get(COOKIE_NAME)

    if not raw_token or not csrf_valid(
        raw_token,
        csrf,
    ):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    form_data = {
        "public_ip": public_ip,
        "public_port": public_port,
        "start": start,
        "end": end,
        "protocol": protocol,
    }

    result = None
    error = None

    try:
        start_dt = datetime.strptime(
            start,
            "%Y-%m-%dT%H:%M",
        )

        end_dt = datetime.strptime(
            end,
            "%Y-%m-%dT%H:%M",
        ).replace(
            second=59,
            microsecond=999999,
        )

        result = search_nat(
            public_ip=public_ip,
            public_port=public_port,
            start=start_dt,
            end=end_dt,
            protocol=protocol or None,
        )

        audit_success = True

        audit_details = (
            f"public_ip={result['public_ip']}; "
            f"public_port={result['public_port']}; "
            f"start={start_dt.isoformat()}; "
            f"end={end_dt.isoformat()}; "
            f"protocol={result['protocol'] or 'ALL'}; "
            f"results={result['count']}; "
            f"truncated={result['truncated']}"
        )

    except ValueError:
        error = "Formato data/ora non valido."
        audit_success = False
        audit_details = "Invalid date/time"

    except ExplorerError as exc:
        error = str(exc)
        audit_success = False
        audit_details = (
            f"Search rejected: {error}"
        )

    except Exception:
        error = (
            "Errore durante la ricerca. "
            "Consultare i log del servizio."
        )
        audit_success = False
        audit_details = "Internal search error"

    # Registriamo ogni consultazione.
    conn = app_db()

    try:
        with conn.cursor() as cur:
            write_audit(
                cur,
                user_id=session["user_id"],
                username=session["username"],
                action="LOG_SEARCH",
                category="LOG_ACCESS",
                request=request,
                success=audit_success,
                target_type="NAT_CORRELATION",
                target_id=public_ip.strip(),
                details=audit_details,
            )

        conn.commit()

    finally:
        conn.close()

    return templates.TemplateResponse(
        request=request,
        name="explorer.html",
        context={
            "session": session,
            "can_export": can_export(session),
            "csrf_token": csrf_token(raw_token),

            "results": result,
            "error": error,
            "form": form_data,

            "advanced_results": None,
            "advanced_error": None,
            "advanced_form": {
                "start": start,
                "end": end,
                "source_ip": "",
                "source_port": "",
                "nat_source_ip": "",
                "nat_source_port": "",
                "dest_ip": "",
                "dest_port": "",
                "protocol": "",
            },

            "active_mode": "nat",
        },
    )



@app.post(
    "/explorer/advanced",
    response_class=HTMLResponse,
)
def explorer_advanced_search(
    request: Request,
    csrf: str = Form(...),
    start: str = Form(...),
    end: str = Form(...),
    source_ip: str = Form(""),
    source_port: str = Form(""),
    nat_source_ip: str = Form(""),
    nat_source_port: str = Form(""),
    dest_ip: str = Form(""),
    dest_port: str = Form(""),
    protocol: str = Form(""),
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    raw_token = request.cookies.get(COOKIE_NAME)

    if not raw_token or not csrf_valid(
        raw_token,
        csrf,
    ):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    advanced_form = {
        "start": start,
        "end": end,
        "source_ip": source_ip,
        "source_port": source_port,
        "nat_source_ip": nat_source_ip,
        "nat_source_port": nat_source_port,
        "dest_ip": dest_ip,
        "dest_port": dest_port,
        "protocol": protocol,
    }

    result = None
    error = None

    try:
        start_dt = datetime.strptime(
            start,
            "%Y-%m-%dT%H:%M",
        )

        end_dt = datetime.strptime(
            end,
            "%Y-%m-%dT%H:%M",
        ).replace(
            second=59,
            microsecond=999999,
        )

        result = search_logs(
            start=start_dt,
            end=end_dt,
            source_ip=source_ip,
            source_port=source_port,
            nat_source_ip=nat_source_ip,
            nat_source_port=nat_source_port,
            dest_ip=dest_ip,
            dest_port=dest_port,
            protocol=protocol,
        )

        audit_success = True

        filter_text = "; ".join(
            f"{key}={value}"
            for key, value
            in result["filters"].items()
        )

        audit_details = (
            f"start={start_dt.isoformat()}; "
            f"end={end_dt.isoformat()}; "
            f"{filter_text}; "
            f"results={result['count']}; "
            f"truncated={result['truncated']}"
        )

    except ValueError:
        error = "Formato data/ora non valido."
        audit_success = False
        audit_details = "Invalid date/time"

    except AdvancedSearchError as exc:
        error = str(exc)
        audit_success = False
        audit_details = (
            f"Search rejected: {error}"
        )

    except Exception:
        error = (
            "Errore durante la ricerca. "
            "Consultare i log del servizio."
        )
        audit_success = False
        audit_details = "Internal advanced search error"

    conn = app_db()

    try:
        with conn.cursor() as cur:
            write_audit(
                cur,
                user_id=session["user_id"],
                username=session["username"],
                action="ADVANCED_LOG_SEARCH",
                category="LOG_ACCESS",
                request=request,
                success=audit_success,
                target_type="ADVANCED_SEARCH",
                target_id=None,
                details=audit_details,
            )

        conn.commit()

    finally:
        conn.close()

    now = datetime.now()

    nat_form = {
        "public_ip": "",
        "public_port": "",
        "start": (
            now - timedelta(minutes=15)
        ).strftime("%Y-%m-%dT%H:%M"),
        "end": now.strftime("%Y-%m-%dT%H:%M"),
        "protocol": "",
    }

    return templates.TemplateResponse(
        request=request,
        name="explorer.html",
        context={
            "session": session,
            "can_export": can_export(session),
            "csrf_token": csrf_token(raw_token),

            "results": None,
            "error": None,
            "form": nat_form,

            "advanced_results": result,
            "advanced_error": error,
            "advanced_form": advanced_form,

            "active_mode": "advanced",
        },
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    raw_token = request.cookies.get(COOKIE_NAME)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "session": session,
            "can_export": can_export(session),
            "csrf_token": csrf_token(raw_token),
        },
    )


@app.post("/explorer/advanced/export")
def explorer_advanced_export(
    request: Request,
    csrf: str = Form(...),
    start: str = Form(...),
    end: str = Form(...),
    source_ip: str = Form(""),
    source_port: str = Form(""),
    nat_source_ip: str = Form(""),
    nat_source_port: str = Form(""),
    dest_ip: str = Form(""),
    dest_port: str = Form(""),
    protocol: str = Form(""),
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not can_export(session):
        return HTMLResponse(
            "Non autorizzato alla creazione di export.",
            status_code=403,
        )

    raw_token = request.cookies.get(
        COOKIE_NAME
    )

    if (
        not raw_token
        or not csrf_valid(raw_token, csrf)
    ):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    try:
        start_dt = datetime.strptime(
            start,
            "%Y-%m-%dT%H:%M",
        )

        end_dt = datetime.strptime(
            end,
            "%Y-%m-%dT%H:%M",
        ).replace(
            second=59,
            microsecond=999999,
        )

        if end_dt < start_dt:
            raise ValueError(
                "Intervallo temporale non valido."
            )

        if (
            end_dt - start_dt
            > timedelta(days=31)
        ):
            raise ValueError(
                "Intervallo massimo: 31 giorni."
            )

        filters = {
            "source_ip": source_ip.strip(),
            "source_port": source_port.strip(),
            "nat_source_ip": nat_source_ip.strip(),
            "nat_source_port": nat_source_port.strip(),
            "dest_ip": dest_ip.strip(),
            "dest_port": dest_port.strip(),
            "protocol": protocol.strip().upper(),
        }

        if not any(filters.values()):
            raise ValueError(
                "Specificare almeno un filtro "
                "oltre al periodo."
            )

        #
        # Le porte vengono normalizzate qui.
        # La validazione completa viene comunque
        # eseguita dal motore export.
        #
        for key in (
            "source_port",
            "nat_source_port",
            "dest_port",
        ):
            value = filters[key]

            if not value:
                filters[key] = None
                continue

            try:
                port = int(value)
            except ValueError:
                raise ValueError(
                    f"Porta non valida: {key}"
                )

            if port < 1 or port > 65535:
                raise ValueError(
                    f"Porta fuori intervallo: {key}"
                )

            filters[key] = port

        for key in (
            "source_ip",
            "nat_source_ip",
            "dest_ip",
            "protocol",
        ):
            if not filters[key]:
                filters[key] = None

        conn = app_db()

        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO export_jobs
                    (
                        user_id,
                        username,
                        start_time,
                        end_time,
                        source_ip,
                        source_port,
                        nat_source_ip,
                        nat_source_port,
                        dest_ip,
                        dest_port,
                        protocol,
                        status
                    )
                    VALUES
                    (
                        %s,%s,%s,%s,
                        %s,%s,%s,%s,
                        %s,%s,%s,
                        'PENDING'
                    )
                    """,
                    (
                        session["user_id"],
                        session["username"],
                        start_dt,
                        end_dt,
                        filters["source_ip"],
                        filters["source_port"],
                        filters["nat_source_ip"],
                        filters["nat_source_port"],
                        filters["dest_ip"],
                        filters["dest_port"],
                        filters["protocol"],
                    ),
                )

                job_id = cur.lastrowid

                filter_text = "; ".join(
                    f"{key}={value}"
                    for key, value
                    in filters.items()
                    if value is not None
                )

                write_audit(
                    cur,
                    user_id=session["user_id"],
                    username=session["username"],
                    action="ADVANCED_EXPORT_QUEUED",
                    category="LOG_ACCESS",
                    request=request,
                    success=True,
                    target_type="EXPORT_JOB",
                    target_id=str(job_id),
                    details=(
                        f"start={start_dt.isoformat()}; "
                        f"end={end_dt.isoformat()}; "
                        f"{filter_text}"
                    ),
                )

            conn.commit()

        finally:
            conn.close()

        #
        # Il job è già persistito nel DB.
        # La scrittura del trigger risveglia
        # netlog-export-worker.path.
        #
        trigger = Path(
            "/var/lib/netlog-manager/"
            "export-queue.trigger"
        )

        trigger.write_text(
            f"{job_id}\n"
        )

        return RedirectResponse(
            f"/exports/{job_id}",
            status_code=303,
        )

    except ValueError as exc:
        return HTMLResponse(
            f"Export non accodato: {exc}",
            status_code=400,
        )

    except Exception as exc:
        return HTMLResponse(
            "Impossibile accodare l'export. "
            f"Errore: {type(exc).__name__}",
            status_code=500,
        )


@app.get(
    "/exports/{job_id}",
    response_class=HTMLResponse,
)
def export_job_status(
    request: Request,
    job_id: int,
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    conn = app_db()

    try:
        with conn.cursor() as cur:
            if is_administrator(session):
                cur.execute(
                    """
                    SELECT *
                    FROM export_jobs
                    WHERE id=%s
                    LIMIT 1
                    """,
                    (job_id,),
                )
            else:
                cur.execute(
                    """
                    SELECT *
                    FROM export_jobs
                    WHERE id=%s
                      AND user_id=%s
                    LIMIT 1
                    """,
                    (
                        job_id,
                        session["user_id"],
                    ),
                )

            job = cur.fetchone()

    finally:
        conn.close()

    if not job:
        return HTMLResponse(
            "Export non trovato.",
            status_code=404,
        )

    job["file_state"] = export_file_state(job)
    job["file_available"] = (
        job["file_state"] == "available"
    )

    raw_token = request.cookies.get(
        COOKIE_NAME
    )

    return templates.TemplateResponse(
        request=request,
        name="export_job.html",
        context={
            "session": session,
            "job": job,
            "can_export": can_export(session),
            "csrf_token": csrf_token(raw_token),
        },
    )


@app.get("/exports/{job_id}/download")
def export_job_download(
    request: Request,
    job_id: int,
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not can_export(session):
        return HTMLResponse(
            "Non autorizzato al download degli export.",
            status_code=403,
        )

    conn = app_db()

    try:
        with conn.cursor() as cur:
            if is_administrator(session):
                cur.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        status,
                        filename,
                        file_size
                    FROM export_jobs
                    WHERE id=%s
                    LIMIT 1
                    """,
                    (job_id,),
                )
            else:
                cur.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        status,
                        filename,
                        file_size
                    FROM export_jobs
                    WHERE id=%s
                      AND user_id=%s
                    LIMIT 1
                    """,
                    (
                        job_id,
                        session["user_id"],
                    ),
                )

            job = cur.fetchone()

    finally:
        conn.close()

    if not job:
        return HTMLResponse(
            "Export non trovato.",
            status_code=404,
        )

    if job["status"] != "COMPLETED":
        return HTMLResponse(
            "Export non ancora disponibile.",
            status_code=409,
        )

    expected_filename = (
        f"network-log-export-job-{job_id}.csv"
    )

    if job["filename"] != expected_filename:
        return HTMLResponse(
            "Riferimento export non valido.",
            status_code=500,
        )

    export_root = Path(
        "/var/cache/netlog-manager/exports"
    )

    export_path = (
        export_root / expected_filename
    )

    if not export_path.is_file():
        return HTMLResponse(
            "Il file export non è più disponibile. "
            "Potrebbe essere scaduto.",
            status_code=410,
        )

    try:
        actual_size = export_path.stat().st_size
    except OSError:
        return HTMLResponse(
            "Impossibile accedere al file export.",
            status_code=500,
        )

    if (
        job["file_size"] is not None
        and actual_size != job["file_size"]
    ):
        return HTMLResponse(
            "Verifica integrità export fallita.",
            status_code=500,
        )

    #
    # Registriamo il download separatamente
    # dalla generazione dell'export.
    #
    conn = app_db()

    try:
        with conn.cursor() as cur:
            write_audit(
                cur,
                user_id=session["user_id"],
                username=session["username"],
                action="EXPORT_DOWNLOAD",
                category="LOG_ACCESS",
                request=request,
                success=True,
                target_type="EXPORT_JOB",
                target_id=str(job_id),
                details=(
                    f"file={expected_filename}; "
                    f"size={actual_size}"
                ),
            )

        conn.commit()

    finally:
        conn.close()

    return FileResponse(
        path=str(export_path),
        media_type="text/csv; charset=utf-8",
        filename=expected_filename,
    )


@app.get(
    "/exports",
    response_class=HTMLResponse,
)
def export_jobs_list(request: Request):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    conn = app_db()

    try:
        with conn.cursor() as cur:
            if is_administrator(session):
                cur.execute(
                    """
                    SELECT
                        id,
                        username,
                        status,
                        start_time,
                        end_time,
                        source_ip,
                        source_port,
                        nat_source_ip,
                        nat_source_port,
                        dest_ip,
                        dest_port,
                        protocol,
                        progress_rows,
                        progress_day,
                        result_rows,
                        online_days,
                        archive_days,
                        filename,
                        file_size,
                        file_sha256,
                        error_message,
                        requested_at,
                        started_at,
                        completed_at
                    FROM export_jobs
                    ORDER BY id DESC
                    LIMIT 100
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT
                        id,
                        username,
                        status,
                        start_time,
                        end_time,
                        source_ip,
                        source_port,
                        nat_source_ip,
                        nat_source_port,
                        dest_ip,
                        dest_port,
                        protocol,
                        progress_rows,
                        progress_day,
                        result_rows,
                        online_days,
                        archive_days,
                        filename,
                        file_size,
                        file_sha256,
                        error_message,
                        requested_at,
                        started_at,
                        completed_at
                    FROM export_jobs
                    WHERE user_id=%s
                    ORDER BY id DESC
                    LIMIT 100
                    """,
                    (session["user_id"],),
                )

            jobs = cur.fetchall()

    finally:
        conn.close()

    export_root = Path(
        "/var/cache/netlog-manager/exports"
    )

    active_jobs = False

    for job in jobs:
        status = job["status"]

        if status in ("PENDING", "RUNNING"):
            active_jobs = True

        job["file_state"] = export_file_state(job)
        job["file_available"] = (
            job["file_state"] == "available"
        )

    raw_token = request.cookies.get(
        COOKIE_NAME
    )

    return templates.TemplateResponse(
        request=request,
        name="exports.html",
        context={
            "session": session,
            "jobs": jobs,
            "active_jobs": active_jobs,
            "is_admin": is_administrator(session),
            "can_export": can_export(session),
            "csrf_token": csrf_token(raw_token),
        },
    )


@app.get(
    "/admin/users",
    response_class=HTMLResponse,
)
def admin_users(
    request: Request,
    message: str = "",
    error: str = "",
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not is_administrator(session):
        return HTMLResponse(
            "Accesso non autorizzato.",
            status_code=403,
        )

    conn = app_db()

    try:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.display_name,
                    u.email,
                    u.enabled,
                    u.mfa_enabled,
                    u.failed_login_count,
                    u.locked_until,
                    u.last_login_at,
                    u.created_at,
                    GROUP_CONCAT(
                        DISTINCT r.name
                        ORDER BY r.name
                        SEPARATOR ', '
                    ) AS roles
                FROM users u
                LEFT JOIN user_roles ur
                    ON ur.user_id=u.id
                LEFT JOIN roles r
                    ON r.id=ur.role_id
                GROUP BY
                    u.id,
                    u.username,
                    u.display_name,
                    u.email,
                    u.enabled,
                    u.mfa_enabled,
                    u.failed_login_count,
                    u.locked_until,
                    u.last_login_at,
                    u.created_at
                ORDER BY u.username
                """
            )

            users = cur.fetchall()

            cur.execute(
                """
                SELECT id,name,description
                FROM roles
                ORDER BY id
                """
            )

            roles = cur.fetchall()

    finally:
        conn.close()

    raw_token = request.cookies.get(
        COOKIE_NAME
    )

    return templates.TemplateResponse(
        request=request,
        name="users.html",
        context={
            "session": session,
            "users": users,
            "roles": roles,
            "message": message,
            "error": error,
            "csrf_token": csrf_token(raw_token),
        },
    )


@app.post("/admin/users/create")
def admin_user_create(
    request: Request,
    csrf: str = Form(...),
    username: str = Form(...),
    display_name: str = Form(""),
    email: str = Form(""),
    role: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not is_administrator(session):
        return HTMLResponse(
            "Accesso non autorizzato.",
            status_code=403,
        )

    raw_token = request.cookies.get(
        COOKIE_NAME
    )

    if (
        not raw_token
        or not csrf_valid(raw_token, csrf)
    ):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    username = username.strip()
    display_name = display_name.strip()
    email = email.strip() or None
    role = role.strip()

    if (
        not username
        or len(username) > 64
        or not all(
            c.isalnum() or c in "._-"
            for c in username
        )
    ):
        return RedirectResponse(
            "/admin/users?error=Username+non+valido",
            status_code=303,
        )

    if password != password_confirm:
        return RedirectResponse(
            "/admin/users?error=Le+password+non+coincidono",
            status_code=303,
        )

    if len(password) < 12:
        return RedirectResponse(
            "/admin/users?error=Password+troppo+corta",
            status_code=303,
        )

    conn = app_db()

    try:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM roles
                WHERE name=%s
                LIMIT 1
                """,
                (role,),
            )

            role_row = cur.fetchone()

            if not role_row:
                return RedirectResponse(
                    "/admin/users?error=Ruolo+non+valido",
                    status_code=303,
                )

            cur.execute(
                """
                SELECT id
                FROM users
                WHERE username=%s
                   OR (
                        %s IS NOT NULL
                        AND email=%s
                   )
                LIMIT 1
                """,
                (
                    username,
                    email,
                    email,
                ),
            )

            if cur.fetchone():
                return RedirectResponse(
                    "/admin/users?error=Utente+o+email+gia+esistente",
                    status_code=303,
                )

            password_hash = ph.hash(password)

            cur.execute(
                """
                INSERT INTO users
                (
                    username,
                    password_hash,
                    display_name,
                    email,
                    enabled,
                    password_changed_at
                )
                VALUES
                (%s,%s,%s,%s,1,NOW())
                """,
                (
                    username,
                    password_hash,
                    display_name or None,
                    email,
                ),
            )

            new_user_id = cur.lastrowid

            cur.execute(
                """
                INSERT INTO user_roles
                (
                    user_id,
                    role_id
                )
                VALUES (%s,%s)
                """,
                (
                    new_user_id,
                    role_row["id"],
                ),
            )

            write_audit(
                cur,
                user_id=session["user_id"],
                username=session["username"],
                action="USER_CREATED",
                category="ADMIN",
                request=request,
                success=True,
                target_type="USER",
                target_id=str(new_user_id),
                details=(
                    f"username={username}; "
                    f"role={role}"
                ),
            )

        conn.commit()

    except Exception:
        conn.rollback()

        return RedirectResponse(
            "/admin/users?error=Impossibile+creare+utente",
            status_code=303,
        )

    finally:
        conn.close()

    return RedirectResponse(
        "/admin/users?message=Utente+creato+correttamente",
        status_code=303,
    )


def get_admin_user_data(user_id: int):
    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.display_name,
                    u.email,
                    u.enabled,
                    u.mfa_enabled,
                    u.failed_login_count,
                    u.locked_until,
                    u.last_login_at,
                    u.created_at,
                    r.name AS role
                FROM users u
                LEFT JOIN user_roles ur
                    ON ur.user_id=u.id
                LEFT JOIN roles r
                    ON r.id=ur.role_id
                WHERE u.id=%s
                LIMIT 1
                """,
                (user_id,),
            )

            user = cur.fetchone()

            cur.execute(
                """
                SELECT id,name,description
                FROM roles
                ORDER BY id
                """
            )

            roles = cur.fetchall()

            return user, roles

    finally:
        conn.close()


def active_administrator_count(cur):
    cur.execute(
        """
        SELECT COUNT(DISTINCT u.id) AS total
        FROM users u
        JOIN user_roles ur
            ON ur.user_id=u.id
        JOIN roles r
            ON r.id=ur.role_id
        WHERE u.enabled=1
          AND r.name='Administrator'
        """
    )

    row = cur.fetchone()

    return int(row["total"] or 0)


@app.get(
    "/admin/users/{user_id}",
    response_class=HTMLResponse,
)
def admin_user_edit(
    request: Request,
    user_id: int,
    message: str = "",
    error: str = "",
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not is_administrator(session):
        return HTMLResponse(
            "Accesso non autorizzato.",
            status_code=403,
        )

    user, roles = get_admin_user_data(user_id)

    if not user:
        return HTMLResponse(
            "Utente non trovato.",
            status_code=404,
        )

    raw_token = request.cookies.get(
        COOKIE_NAME
    )

    return templates.TemplateResponse(
        request=request,
        name="user_edit.html",
        context={
            "session": session,
            "user": user,
            "roles": roles,
            "message": message,
            "error": error,
            "csrf_token": csrf_token(raw_token),
        },
    )


@app.post("/admin/users/{user_id}/update")
def admin_user_update(
    request: Request,
    user_id: int,
    csrf: str = Form(...),
    display_name: str = Form(""),
    email: str = Form(""),
    role: str = Form(...),
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not is_administrator(session):
        return HTMLResponse(
            "Accesso non autorizzato.",
            status_code=403,
        )

    if not valid_form_csrf(request, csrf):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    display_name = display_name.strip()
    email = email.strip() or None
    role = role.strip()

    conn = app_db()

    try:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.enabled,
                    r.name AS current_role
                FROM users u
                LEFT JOIN user_roles ur
                    ON ur.user_id=u.id
                LEFT JOIN roles r
                    ON r.id=ur.role_id
                WHERE u.id=%s
                LIMIT 1
                FOR UPDATE
                """,
                (user_id,),
            )

            target = cur.fetchone()

            if not target:
                return HTMLResponse(
                    "Utente non trovato.",
                    status_code=404,
                )

            cur.execute(
                """
                SELECT id,name
                FROM roles
                WHERE name=%s
                LIMIT 1
                """,
                (role,),
            )

            role_row = cur.fetchone()

            if not role_row:
                return HTMLResponse(
                    "Ruolo non valido.",
                    status_code=400,
                )

            if (
                target["enabled"]
                and target["current_role"] == "Administrator"
                and role != "Administrator"
                and active_administrator_count(cur) <= 1
            ):
                return RedirectResponse(
                    f"/admin/users/{user_id}"
                    "?error=Impossibile+rimuovere+"
                    "l%27ultimo+Administrator+attivo",
                    status_code=303,
                )

            if email:
                cur.execute(
                    """
                    SELECT id
                    FROM users
                    WHERE email=%s
                      AND id<>%s
                    LIMIT 1
                    """,
                    (email, user_id),
                )

                if cur.fetchone():
                    return RedirectResponse(
                        f"/admin/users/{user_id}"
                        "?error=Email+gia+utilizzata",
                        status_code=303,
                    )

            cur.execute(
                """
                UPDATE users
                SET
                    display_name=%s,
                    email=%s
                WHERE id=%s
                """,
                (
                    display_name or None,
                    email,
                    user_id,
                ),
            )

            if target["current_role"] != role:
                cur.execute(
                    """
                    DELETE FROM user_roles
                    WHERE user_id=%s
                    """,
                    (user_id,),
                )

                cur.execute(
                    """
                    INSERT INTO user_roles
                    (
                        user_id,
                        role_id
                    )
                    VALUES (%s,%s)
                    """,
                    (
                        user_id,
                        role_row["id"],
                    ),
                )

            write_audit(
                cur,
                user_id=session["user_id"],
                username=session["username"],
                action="USER_UPDATED",
                category="ADMIN",
                request=request,
                success=True,
                target_type="USER",
                target_id=str(user_id),
                details=(
                    f"target={target['username']}; "
                    f"role={role}"
                ),
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    return RedirectResponse(
        f"/admin/users/{user_id}"
        "?message=Modifiche+salvate",
        status_code=303,
    )


@app.post("/admin/users/{user_id}/password")
def admin_user_password(
    request: Request,
    user_id: int,
    csrf: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not is_administrator(session):
        return HTMLResponse(
            "Accesso non autorizzato.",
            status_code=403,
        )

    if not valid_form_csrf(request, csrf):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    if password != password_confirm:
        return RedirectResponse(
            f"/admin/users/{user_id}"
            "?error=Le+password+non+coincidono",
            status_code=303,
        )

    if len(password) < 12:
        return RedirectResponse(
            f"/admin/users/{user_id}"
            "?error=Password+troppo+corta",
            status_code=303,
        )

    password_hash = ph.hash(password)

    conn = app_db()

    try:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT username
                FROM users
                WHERE id=%s
                LIMIT 1
                """,
                (user_id,),
            )

            target = cur.fetchone()

            if not target:
                return HTMLResponse(
                    "Utente non trovato.",
                    status_code=404,
                )

            cur.execute(
                """
                UPDATE users
                SET
                    password_hash=%s,
                    password_changed_at=NOW(),
                    failed_login_count=0,
                    locked_until=NULL
                WHERE id=%s
                """,
                (
                    password_hash,
                    user_id,
                ),
            )

            cur.execute(
                """
                UPDATE sessions
                SET revoked_at=NOW()
                WHERE user_id=%s
                  AND revoked_at IS NULL
                """,
                (user_id,),
            )

            cur.execute(
                """
                UPDATE mfa_challenges
                SET used_at=NOW()
                WHERE user_id=%s
                  AND purpose='LOGIN'
                  AND used_at IS NULL
                """,
                (user_id,),
            )

            write_audit(
                cur,
                user_id=session["user_id"],
                username=session["username"],
                action="USER_PASSWORD_RESET",
                category="ADMIN",
                request=request,
                success=True,
                target_type="USER",
                target_id=str(user_id),
                details=(
                    f"target={target['username']}"
                ),
            )

        conn.commit()

    finally:
        conn.close()

    #
    # Se l'Administrator cambia la propria
    # password, la sessione corrente è stata
    # revocata intenzionalmente.
    #
    if user_id == session["user_id"]:
        response = RedirectResponse(
            "/login",
            status_code=303,
        )

        response.delete_cookie(
            COOKIE_NAME,
            path="/",
        )

        return response

    return RedirectResponse(
        f"/admin/users/{user_id}"
        "?message=Password+aggiornata+"
        "e+sessioni+revocate",
        status_code=303,
    )


@app.post("/admin/users/{user_id}/unlock")
def admin_user_unlock(
    request: Request,
    user_id: int,
    csrf: str = Form(...),
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not is_administrator(session):
        return HTMLResponse(
            "Accesso non autorizzato.",
            status_code=403,
        )

    if not valid_form_csrf(request, csrf):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    conn = app_db()

    try:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT username
                FROM users
                WHERE id=%s
                LIMIT 1
                """,
                (user_id,),
            )

            target = cur.fetchone()

            if not target:
                return HTMLResponse(
                    "Utente non trovato.",
                    status_code=404,
                )

            cur.execute(
                """
                UPDATE users
                SET
                    failed_login_count=0,
                    locked_until=NULL
                WHERE id=%s
                """,
                (user_id,),
            )

            write_audit(
                cur,
                user_id=session["user_id"],
                username=session["username"],
                action="USER_UNLOCKED",
                category="ADMIN",
                request=request,
                success=True,
                target_type="USER",
                target_id=str(user_id),
                details=(
                    f"target={target['username']}"
                ),
            )

        conn.commit()

    finally:
        conn.close()

    return RedirectResponse(
        f"/admin/users/{user_id}"
        "?message=Account+sbloccato",
        status_code=303,
    )


@app.post("/admin/users/{user_id}/toggle")
def admin_user_toggle(
    request: Request,
    user_id: int,
    csrf: str = Form(...),
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not is_administrator(session):
        return HTMLResponse(
            "Accesso non autorizzato.",
            status_code=403,
        )

    if not valid_form_csrf(request, csrf):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    if user_id == session["user_id"]:
        return RedirectResponse(
            f"/admin/users/{user_id}"
            "?error=Non+puoi+disabilitare+"
            "il+tuo+account",
            status_code=303,
        )

    conn = app_db()

    try:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    u.username,
                    u.enabled,
                    r.name AS role
                FROM users u
                LEFT JOIN user_roles ur
                    ON ur.user_id=u.id
                LEFT JOIN roles r
                    ON r.id=ur.role_id
                WHERE u.id=%s
                LIMIT 1
                FOR UPDATE
                """,
                (user_id,),
            )

            target = cur.fetchone()

            if not target:
                return HTMLResponse(
                    "Utente non trovato.",
                    status_code=404,
                )

            new_enabled = (
                0 if target["enabled"] else 1
            )

            if (
                target["enabled"]
                and target["role"] == "Administrator"
                and active_administrator_count(cur) <= 1
            ):
                return RedirectResponse(
                    f"/admin/users/{user_id}"
                    "?error=Impossibile+disabilitare+"
                    "l%27ultimo+Administrator+attivo",
                    status_code=303,
                )

            cur.execute(
                """
                UPDATE users
                SET enabled=%s
                WHERE id=%s
                """,
                (
                    new_enabled,
                    user_id,
                ),
            )

            if not new_enabled:
                cur.execute(
                    """
                    UPDATE sessions
                    SET revoked_at=NOW()
                    WHERE user_id=%s
                      AND revoked_at IS NULL
                    """,
                    (user_id,),
                )

                cur.execute(
                    """
                    UPDATE mfa_challenges
                    SET used_at=NOW()
                    WHERE user_id=%s
                      AND purpose='LOGIN'
                      AND used_at IS NULL
                    """,
                    (user_id,),
                )

            action = (
                "USER_ENABLED"
                if new_enabled
                else "USER_DISABLED"
            )

            write_audit(
                cur,
                user_id=session["user_id"],
                username=session["username"],
                action=action,
                category="ADMIN",
                request=request,
                success=True,
                target_type="USER",
                target_id=str(user_id),
                details=(
                    f"target={target['username']}"
                ),
            )

        conn.commit()

    finally:
        conn.close()

    return RedirectResponse(
        f"/admin/users/{user_id}"
        "?message=Stato+account+aggiornato",
        status_code=303,
    )


@app.post("/admin/users/{user_id}/mfa-reset")
def admin_user_mfa_reset(
    request: Request,
    user_id: int,
    csrf: str = Form(...),
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not is_administrator(session):
        return HTMLResponse(
            "Accesso non autorizzato.",
            status_code=403,
        )

    if not valid_form_csrf(request, csrf):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    conn = app_db()

    try:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT username
                FROM users
                WHERE id=%s
                LIMIT 1
                """,
                (user_id,),
            )

            target = cur.fetchone()

            if not target:
                return HTMLResponse(
                    "Utente non trovato.",
                    status_code=404,
                )

            cur.execute(
                """
                UPDATE users
                SET
                    mfa_enabled=0,
                    mfa_secret=NULL
                WHERE id=%s
                """,
                (user_id,),
            )

            #
            # Un reset MFA è un evento di sicurezza:
            # invalidiamo sessioni e challenge LOGIN.
            #
            cur.execute(
                """
                UPDATE sessions
                SET revoked_at=NOW()
                WHERE user_id=%s
                  AND revoked_at IS NULL
                """,
                (user_id,),
            )

            cur.execute(
                """
                UPDATE mfa_challenges
                SET used_at=NOW()
                WHERE user_id=%s
                  AND purpose='LOGIN'
                  AND used_at IS NULL
                """,
                (user_id,),
            )

            write_audit(
                cur,
                user_id=session["user_id"],
                username=session["username"],
                action="MFA_RESET",
                category="ADMIN",
                request=request,
                success=True,
                target_type="USER",
                target_id=str(user_id),
                details=(
                    f"target={target['username']}"
                ),
            )

        conn.commit()

    finally:
        conn.close()

    if user_id == session["user_id"]:
        response = RedirectResponse(
            "/login",
            status_code=303,
        )

        response.delete_cookie(
            COOKIE_NAME,
            path="/",
        )

        return response

    return RedirectResponse(
        f"/admin/users/{user_id}"
        "?message=2FA+reimpostato",
        status_code=303,
    )


@app.get(
    "/account",
    response_class=HTMLResponse,
)
def account_page(request: Request):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.display_name,
                    u.email,
                    u.enabled,
                    u.mfa_enabled,
                    u.last_login_at,
                    u.password_changed_at,
                    u.created_at,
                    GROUP_CONCAT(
                        DISTINCT r.name
                        ORDER BY r.name
                        SEPARATOR ', '
                    ) AS roles
                FROM users u
                LEFT JOIN user_roles ur
                    ON ur.user_id=u.id
                LEFT JOIN roles r
                    ON r.id=ur.role_id
                WHERE u.id=%s
                GROUP BY
                    u.id,
                    u.username,
                    u.display_name,
                    u.email,
                    u.enabled,
                    u.mfa_enabled,
                    u.last_login_at,
                    u.password_changed_at,
                    u.created_at
                LIMIT 1
                """,
                (session["user_id"],),
            )

            account = cur.fetchone()

    finally:
        conn.close()

    if not account:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    raw_token = request.cookies.get(COOKIE_NAME)

    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context={
            "session": session,
            "account": account,
            "csrf_token": csrf_token(raw_token),
        },
    )


@app.get(
    "/account/password",
    response_class=HTMLResponse,
)
def account_password_page(
    request: Request,
    error: str = "",
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    raw_token = request.cookies.get(COOKIE_NAME)

    return templates.TemplateResponse(
        request=request,
        name="account_password.html",
        context={
            "session": session,
            "error": error,
            "csrf_token": csrf_token(raw_token),
        },
    )


@app.post("/account/password")
def account_password_change(
    request: Request,
    csrf: str = Form(...),
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not valid_form_csrf(request, csrf):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    if new_password != new_password_confirm:
        return RedirectResponse(
            "/account/password"
            "?error=Le+nuove+password+non+coincidono",
            status_code=303,
        )

    if len(new_password) < 12:
        return RedirectResponse(
            "/account/password"
            "?error=La+nuova+password+deve+avere+"
            "almeno+12+caratteri",
            status_code=303,
        )

    conn = app_db()

    try:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    username,
                    password_hash
                FROM users
                WHERE id=%s
                  AND enabled=1
                LIMIT 1
                """,
                (session["user_id"],),
            )

            user = cur.fetchone()

            if not user:
                return RedirectResponse(
                    "/login",
                    status_code=303,
                )

            try:
                current_valid = ph.verify(
                    user["password_hash"],
                    current_password,
                )
            except Exception:
                current_valid = False

            if not current_valid:
                write_audit(
                    cur,
                    user_id=session["user_id"],
                    username=session["username"],
                    action="PASSWORD_CHANGE_FAILED",
                    category="AUTH",
                    request=request,
                    success=False,
                    target_type="USER",
                    target_id=str(session["user_id"]),
                    details="Current password verification failed",
                )

                conn.commit()

                return RedirectResponse(
                    "/account/password"
                    "?error=Password+attuale+non+corretta",
                    status_code=303,
                )

            #
            # Evitiamo anche il riutilizzo immediato
            # della stessa password.
            #
            try:
                same_password = ph.verify(
                    user["password_hash"],
                    new_password,
                )
            except Exception:
                same_password = False

            if same_password:
                return RedirectResponse(
                    "/account/password"
                    "?error=La+nuova+password+deve+essere+"
                    "diversa+dalla+password+attuale",
                    status_code=303,
                )

            new_hash = ph.hash(new_password)

            cur.execute(
                """
                UPDATE users
                SET
                    password_hash=%s,
                    password_changed_at=NOW(),
                    failed_login_count=0,
                    locked_until=NULL
                WHERE id=%s
                """,
                (
                    new_hash,
                    session["user_id"],
                ),
            )

            #
            # Manteniamo la sessione corrente ma
            # revochiamo tutte le altre.
            #
            cur.execute(
                """
                UPDATE sessions
                SET revoked_at=NOW()
                WHERE user_id=%s
                  AND id<>%s
                  AND revoked_at IS NULL
                """,
                (
                    session["user_id"],
                    session["session_id"],
                ),
            )

            cur.execute(
                """
                UPDATE mfa_challenges
                SET used_at=NOW()
                WHERE user_id=%s
                  AND purpose='LOGIN'
                  AND used_at IS NULL
                """,
                (session["user_id"],),
            )

            write_audit(
                cur,
                user_id=session["user_id"],
                username=session["username"],
                action="PASSWORD_CHANGED",
                category="AUTH",
                request=request,
                success=True,
                target_type="USER",
                target_id=str(session["user_id"]),
                details="Password changed by account owner",
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    return RedirectResponse(
        "/account",
        status_code=303,
    )


@app.get(
    "/account/security",
    response_class=HTMLResponse,
)
def account_security_page(
    request: Request,
    error: str = "",
    message: str = "",
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mfa_enabled
                FROM users
                WHERE id=%s
                  AND enabled=1
                LIMIT 1
                """,
                (session["user_id"],),
            )

            row = cur.fetchone()

    finally:
        conn.close()

    if not row:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    setup_active = False
    qr_base64 = None
    manual_secret = None

    setup_token = request.cookies.get(
        "netlog_mfa_setup"
    )

    if not row["mfa_enabled"] and setup_token:
        setup = read_mfa_setup_token(
            setup_token,
            max_age=600,
        )

        if (
            setup
            and setup["user_id"] == session["user_id"]
        ):
            setup_active = True
            manual_secret = setup["secret"]

            uri = pyotp.TOTP(
                manual_secret
            ).provisioning_uri(
                name=session["username"],
                issuer_name="Network Log Manager",
            )

            qr = qrcode.QRCode(
                version=None,
                box_size=8,
                border=4,
            )
            qr.add_data(uri)
            qr.make(fit=True)

            image = qr.make_image(
                fill_color="black",
                back_color="white",
            )

            buffer = io.BytesIO()
            image.save(buffer, format="PNG")

            qr_base64 = base64.b64encode(
                buffer.getvalue()
            ).decode("ascii")

    raw_token = request.cookies.get(COOKIE_NAME)

    return templates.TemplateResponse(
        request=request,
        name="account_security.html",
        context={
            "session": session,
            "mfa_enabled": bool(row["mfa_enabled"]),
            "setup_active": setup_active,
            "qr_base64": qr_base64,
            "manual_secret": manual_secret,
            "error": error,
            "message": message,
            "csrf_token": csrf_token(raw_token),
        },
    )


@app.post("/account/security/setup")
def account_security_setup(
    request: Request,
    csrf: str = Form(...),
    password: str = Form(...),
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not valid_form_csrf(request, csrf):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    password_hash,
                    mfa_enabled
                FROM users
                WHERE id=%s
                  AND enabled=1
                LIMIT 1
                """,
                (session["user_id"],),
            )

            user = cur.fetchone()

            valid = False

            if user:
                try:
                    valid = ph.verify(
                        user["password_hash"],
                        password,
                    )
                except Exception:
                    valid = False

            if not user or not valid:
                write_audit(
                    cur,
                    user_id=session["user_id"],
                    username=session["username"],
                    action="MFA_SETUP_FAILED",
                    category="AUTH",
                    request=request,
                    success=False,
                    target_type="USER",
                    target_id=str(session["user_id"]),
                    details="Password verification failed",
                )

                conn.commit()

                return RedirectResponse(
                    "/account/security"
                    "?error=Password+attuale+non+corretta",
                    status_code=303,
                )

            if user["mfa_enabled"]:
                return RedirectResponse(
                    "/account/security",
                    status_code=303,
                )

            secret = pyotp.random_base32()

            setup_token = create_mfa_setup_token(
                user_id=session["user_id"],
                secret=secret,
            )

            write_audit(
                cur,
                user_id=session["user_id"],
                username=session["username"],
                action="MFA_SETUP_STARTED",
                category="AUTH",
                request=request,
                success=True,
                target_type="USER",
                target_id=str(session["user_id"]),
                details="TOTP enrollment started",
            )

            conn.commit()

    finally:
        conn.close()

    response = RedirectResponse(
        "/account/security",
        status_code=303,
    )

    response.set_cookie(
        "netlog_mfa_setup",
        setup_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=600,
        path="/account/security",
    )

    return response


@app.post("/account/security/enable")
def account_security_enable(
    request: Request,
    csrf: str = Form(...),
    code: str = Form(...),
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not valid_form_csrf(request, csrf):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    setup_token = request.cookies.get(
        "netlog_mfa_setup"
    )

    setup = (
        read_mfa_setup_token(
            setup_token,
            max_age=600,
        )
        if setup_token
        else None
    )

    if (
        not setup
        or setup["user_id"] != session["user_id"]
    ):
        response = RedirectResponse(
            "/account/security"
            "?error=Configurazione+2FA+scaduta.+"
            "Avvia+nuovamente+la+procedura",
            status_code=303,
        )

        response.delete_cookie(
            "netlog_mfa_setup",
            path="/account/security",
        )

        return response

    code = code.strip().replace(" ", "")

    valid_totp = (
        code.isdigit()
        and len(code) == 6
        and pyotp.TOTP(
            setup["secret"]
        ).verify(
            code,
            valid_window=1,
        )
    )

    conn = app_db()

    try:
        with conn.cursor() as cur:

            if not valid_totp:
                write_audit(
                    cur,
                    user_id=session["user_id"],
                    username=session["username"],
                    action="MFA_SETUP_CODE_FAILED",
                    category="AUTH",
                    request=request,
                    success=False,
                    target_type="USER",
                    target_id=str(session["user_id"]),
                    details="Invalid TOTP enrollment code",
                )

                conn.commit()

                return RedirectResponse(
                    "/account/security"
                    "?error=Codice+2FA+non+valido",
                    status_code=303,
                )

            cur.execute(
                """
                UPDATE users
                SET
                    mfa_secret=%s,
                    mfa_enabled=1
                WHERE id=%s
                  AND enabled=1
                  AND mfa_enabled=0
                """,
                (
                    setup["secret"],
                    session["user_id"],
                ),
            )

            if cur.rowcount != 1:
                conn.rollback()

                return RedirectResponse(
                    "/account/security",
                    status_code=303,
                )

            #
            # La sessione corrente resta valida.
            # Tutte le altre vengono revocate.
            #
            cur.execute(
                """
                UPDATE sessions
                SET revoked_at=NOW()
                WHERE user_id=%s
                  AND id<>%s
                  AND revoked_at IS NULL
                """,
                (
                    session["user_id"],
                    session["session_id"],
                ),
            )

            write_audit(
                cur,
                user_id=session["user_id"],
                username=session["username"],
                action="MFA_ENABLED",
                category="AUTH",
                request=request,
                success=True,
                target_type="USER",
                target_id=str(session["user_id"]),
                details="TOTP authentication enabled",
            )

            conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    response = RedirectResponse(
        "/account/security"
        "?message=Autenticazione+2FA+attivata",
        status_code=303,
    )

    response.delete_cookie(
        "netlog_mfa_setup",
        path="/account/security",
    )

    return response


@app.post("/account/security/disable")
def account_security_disable(
    request: Request,
    csrf: str = Form(...),
    password: str = Form(...),
    code: str = Form(...),
):
    session = get_session(request)

    if not session:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not valid_form_csrf(request, csrf):
        return HTMLResponse(
            "Richiesta non valida.",
            status_code=403,
        )

    code = code.strip().replace(" ", "")

    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    password_hash,
                    mfa_enabled,
                    mfa_secret
                FROM users
                WHERE id=%s
                  AND enabled=1
                LIMIT 1
                FOR UPDATE
                """,
                (session["user_id"],),
            )

            user = cur.fetchone()

            password_valid = False

            if user:
                try:
                    password_valid = ph.verify(
                        user["password_hash"],
                        password,
                    )
                except Exception:
                    password_valid = False

            secret = None

            if user and user["mfa_secret"]:
                secret = user["mfa_secret"]

                if isinstance(secret, bytes):
                    secret = secret.decode("ascii")

            totp_valid = bool(
                secret
                and code.isdigit()
                and len(code) == 6
                and pyotp.TOTP(secret).verify(
                    code,
                    valid_window=1,
                )
            )

            if (
                not user
                or not user["mfa_enabled"]
                or not password_valid
                or not totp_valid
            ):
                write_audit(
                    cur,
                    user_id=session["user_id"],
                    username=session["username"],
                    action="MFA_DISABLE_FAILED",
                    category="AUTH",
                    request=request,
                    success=False,
                    target_type="USER",
                    target_id=str(session["user_id"]),
                    details="MFA disable verification failed",
                )

                conn.commit()

                return RedirectResponse(
                    "/account/security"
                    "?error=Password+o+codice+2FA+non+valido",
                    status_code=303,
                )

            cur.execute(
                """
                UPDATE users
                SET
                    mfa_enabled=0,
                    mfa_secret=NULL
                WHERE id=%s
                """,
                (session["user_id"],),
            )

            cur.execute(
                """
                UPDATE sessions
                SET revoked_at=NOW()
                WHERE user_id=%s
                  AND id<>%s
                  AND revoked_at IS NULL
                """,
                (
                    session["user_id"],
                    session["session_id"],
                ),
            )

            cur.execute(
                """
                UPDATE mfa_challenges
                SET used_at=NOW()
                WHERE user_id=%s
                  AND purpose='LOGIN'
                  AND used_at IS NULL
                """,
                (session["user_id"],),
            )

            write_audit(
                cur,
                user_id=session["user_id"],
                username=session["username"],
                action="MFA_DISABLED",
                category="AUTH",
                request=request,
                success=True,
                target_type="USER",
                target_id=str(session["user_id"]),
                details="TOTP authentication disabled",
            )

            conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    return RedirectResponse(
        "/account/security"
        "?message=Autenticazione+2FA+disattivata",
        status_code=303,
    )



@app.get("/admin/settings", response_class=HTMLResponse)
def admin_settings_page(request: Request):
    session=get_session(request)
    if not session: return RedirectResponse("/login",status_code=303)
    if not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT setting_key,setting_value FROM settings")
            settings={r["setting_key"]:r["setting_value"] or "" for r in cur.fetchall()}
            cur.execute("SELECT id,name,channel_type,enabled,event_types_json FROM notification_channels ORDER BY name")
            channels=cur.fetchall()
            cur.execute("SELECT * FROM system_alerts WHERE resolved_at IS NULL ORDER BY severity DESC,last_seen_at DESC LIMIT 50")
            alerts=cur.fetchall()
    finally: conn.close()
    raw=request.cookies.get(COOKIE_NAME)
    return templates.TemplateResponse(request=request,name="settings.html",context={"session":session,"csrf_token":csrf_token(raw),"settings":settings,"channels":channels,"alerts":alerts,"storages":list_targets(),"message":request.query_params.get("message"),"error":None})

@app.post("/admin/settings")
def admin_settings_save(request: Request, csrf: str=Form(...), retention_days: int=Form(...), archive_after_days: int=Form(...), storage_warning_percent: int=Form(...), storage_critical_percent: int=Form(...), ingestion_stale_minutes: int=Form(...), alert_repeat_minutes: int=Form(...)):
    session=get_session(request)
    if not session: return RedirectResponse("/login",status_code=303)
    if not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    if not valid_form_csrf(request,csrf): return HTMLResponse("Invalid CSRF",status_code=403)
    if retention_days<1 or archive_after_days<1 or archive_after_days>=retention_days or not (1<=storage_warning_percent<storage_critical_percent<=99) or ingestion_stale_minutes<1 or alert_repeat_minutes<1:
        return RedirectResponse("/admin/settings?message=Invalid+settings",status_code=303)
    values={"retention_days":retention_days,"archive_after_days":archive_after_days,"storage_warning_percent":storage_warning_percent,"storage_critical_percent":storage_critical_percent,"ingestion_stale_minutes":ingestion_stale_minutes,"alert_repeat_minutes":alert_repeat_minutes}
    conn=app_db()
    try:
        with conn.cursor() as cur:
            for key,value in values.items():
                cur.execute("INSERT INTO settings(setting_key,setting_value) VALUES(%s,%s) ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)",(key,str(value)))
            write_audit(cur,user_id=session["user_id"],username=session["username"],action="SETTINGS_UPDATED",category="ADMIN",request=request,details="Operational settings updated")
            conn.commit()
    finally: conn.close()
    return RedirectResponse("/admin/settings?message=Settings+saved",status_code=303)

@app.post("/admin/settings/channels")
def admin_channel_add(request: Request, csrf: str=Form(...), name: str=Form(...), channel_type: str=Form(...), configuration_json: str=Form(...), secret_json: str=Form('{}')):
    session=get_session(request)
    if not session: return RedirectResponse("/login",status_code=303)
    if not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    if not valid_form_csrf(request,csrf): return HTMLResponse("Invalid CSRF",status_code=403)
    import json
    if channel_type not in {"WEBHOOK","EMAIL","TELEGRAM","SLACK","DISCORD"}: return HTMLResponse("Invalid channel",status_code=400)
    try: cfg=json.loads(configuration_json); supplied_secrets=json.loads(secret_json or '{}')
    except Exception: return HTMLResponse("Invalid JSON configuration",status_code=400)
    if not isinstance(cfg,dict) or not isinstance(supplied_secrets,dict): return HTMLResponse("JSON objects required",status_code=400)
    secret_keys={"password","bot_token","token","secret","api_key"}
    secret_cfg={k:cfg.pop(k) for k in list(cfg) if k.lower() in secret_keys}
    secret_cfg.update(supplied_secrets)
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO notification_channels(name,channel_type,configuration_json,secret_json) VALUES(%s,%s,%s,%s)",(name.strip(),channel_type,json.dumps(cfg),json.dumps(secret_cfg) if secret_cfg else None))
            write_audit(cur,user_id=session["user_id"],username=session["username"],action="NOTIFICATION_CHANNEL_CREATED",category="ADMIN",request=request,target_type="NOTIFICATION_CHANNEL",target_id=str(cur.lastrowid))
            conn.commit()
    finally: conn.close()
    return RedirectResponse("/admin/settings?message=Notification+channel+created",status_code=303)

@app.post("/admin/settings/channels/{channel_id}/delete")
def admin_channel_delete(channel_id: int, request: Request, csrf: str=Form(...)):
    session=get_session(request)
    if not session: return RedirectResponse("/login",status_code=303)
    if not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    if not valid_form_csrf(request,csrf): return HTMLResponse("Invalid CSRF",status_code=403)
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM notification_channels WHERE id=%s",(channel_id,))
            write_audit(cur,user_id=session["user_id"],username=session["username"],action="NOTIFICATION_CHANNEL_DELETED",category="ADMIN",request=request,target_type="NOTIFICATION_CHANNEL",target_id=str(channel_id))
            conn.commit()
    finally: conn.close()
    return RedirectResponse("/admin/settings?message=Notification+channel+deleted",status_code=303)


@app.post("/admin/settings/storage/browse")
def admin_storage_browse(request: Request, csrf: str=Form(...), storage_type: str=Form(...), host: str=Form(""), port: str=Form(""), share: str=Form(""), path: str=Form(""), folder: str=Form(""), domain: str=Form(""), username: str=Form(""), password: str=Form(""), private_key: str=Form(""), key_passphrase: str=Form(""), endpoint: str=Form(""), bucket: str=Form(""), region: str=Form(""), access_key: str=Form(""), secret_key: str=Form("")):
    session=get_session(request)
    if not session or not is_administrator(session): return JSONResponse({"error":"Forbidden"},status_code=403)
    if not valid_form_csrf(request,csrf): return JSONResponse({"error":"Invalid CSRF"},status_code=403)
    cfg={"type":storage_type,"host":host.strip(),"port":port.strip(),"share":share.strip(),"path":path.strip(),"domain":domain.strip(),"endpoint":endpoint.strip(),"bucket":bucket.strip(),"region":region.strip(),"folder":folder.strip()}
    secret={"username":username,"password":password,"private_key":private_key,"key_passphrase":key_passphrase,"access_key":access_key,"secret_key":secret_key}
    try:
        folders=storage_browse(cfg,secret,folder)
        return {"ok":True,"folder":folder,"folders":folders}
    except Exception as exc: return JSONResponse({"ok":False,"error":str(exc)},status_code=400)

@app.post("/admin/settings/storage/add")
def admin_storage_add(request: Request, csrf: str=Form(...), name: str=Form(...), storage_type: str=Form(...), role: str=Form("REPLICA"), host: str=Form(""), port: str=Form(""), share: str=Form(""), path: str=Form(""), folder: str=Form(""), domain: str=Form(""), username: str=Form(""), password: str=Form(""), private_key: str=Form(""), key_passphrase: str=Form(""), endpoint: str=Form(""), bucket: str=Form(""), region: str=Form(""), access_key: str=Form(""), secret_key: str=Form(""), read_fallback: str=Form("")):
    session=get_session(request)
    if not session or not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    if not valid_form_csrf(request,csrf): return HTMLResponse("Invalid CSRF",status_code=403)
    kind=storage_type.upper(); role=role.upper()
    if kind not in {"LOCAL","SMB","SFTP","S3"} or role not in {"PRIMARY","REPLICA"}: return HTMLResponse("Invalid storage",status_code=400)
    cfg={"type":kind,"host":host.strip(),"port":port.strip(),"share":share.strip(),"path":path.strip(),"domain":domain.strip(),"endpoint":endpoint.strip(),"bucket":bucket.strip(),"region":region.strip(),"folder":folder.strip()}
    secret={"username":username,"password":password,"private_key":private_key,"key_passphrase":key_passphrase,"access_key":access_key,"secret_key":secret_key}
    try:
        result=storage_integrity_test(cfg,secret,folder)
    except Exception as exc:
        return RedirectResponse("/admin/settings?message=Storage+test+failed%3A+"+str(exc).replace(" ","+"),status_code=303)
    conn=app_db()
    try:
        with conn.cursor() as cur:
            if role=="PRIMARY": cur.execute("UPDATE storage_targets SET role='REPLICA' WHERE role='PRIMARY'")
            cur.execute("""INSERT INTO storage_targets(name,storage_type,role,enabled,read_fallback,host,port,share_name,endpoint_url,bucket_name,region_name,base_path,folder,domain_name,secret_encrypted,health_status,test_status,tested_at,test_sha256)
              VALUES(%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'HEALTHY','OK',NOW(3),%s)""",
              (name.strip(),kind,role,1 if read_fallback else 0,host.strip() or None,int(port) if port.strip() else None,share.strip() or None,endpoint.strip() or None,bucket.strip() or None,region.strip() or None,path.strip(),folder.strip(),domain.strip() or None,encrypt_secret(secret) if kind!="LOCAL" else None,result.get("sha256")))
            conn.commit()
    finally: conn.close()
    return RedirectResponse("/admin/settings?message=Storage+verified+and+added",status_code=303)

@app.post("/admin/settings/storage/{storage_id}/primary")
def admin_storage_primary(storage_id:int,request:Request,csrf:str=Form(...)):
    session=get_session(request)
    if not session or not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    if not valid_form_csrf(request,csrf): return HTMLResponse("Invalid CSRF",status_code=403)
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE storage_targets SET role='REPLICA' WHERE role='PRIMARY'")
            cur.execute("UPDATE storage_targets SET role='PRIMARY',enabled=1,read_fallback=1 WHERE id=%s",(storage_id,)); conn.commit()
    finally: conn.close()
    return RedirectResponse("/admin/settings?message=Primary+storage+updated",status_code=303)

@app.post("/admin/settings/storage/{storage_id}/health")
def admin_storage_health(storage_id:int,request:Request,csrf:str=Form(...)):
    session=get_session(request)
    if not session or not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    if not valid_form_csrf(request,csrf): return HTMLResponse("Invalid CSRF",status_code=403)
    row=get_target(storage_id)
    if row: health_check(row,deep=row["storage_type"]!="LOCAL")
    return RedirectResponse("/admin/settings?message=Storage+health+check+completed",status_code=303)

@app.post("/admin/settings/storage/{storage_id}/toggle")
def admin_storage_toggle(storage_id:int,request:Request,csrf:str=Form(...)):
    session=get_session(request)
    if not session or not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    if not valid_form_csrf(request,csrf): return HTMLResponse("Invalid CSRF",status_code=403)
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE storage_targets SET enabled=
              IF(role='PRIMARY' OR (storage_type='LOCAL' AND base_path='/archive/mikrotik'),1,IF(enabled=1,0,1))
              WHERE id=%s""",(storage_id,)); conn.commit()
    finally: conn.close()
    return RedirectResponse("/admin/settings?message=Storage+updated",status_code=303)

@app.post("/admin/settings/storage/{storage_id}/delete")
def admin_storage_delete(storage_id:int,request:Request,csrf:str=Form(...)):
    session=get_session(request)
    if not session or not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    if not valid_form_csrf(request,csrf): return HTMLResponse("Invalid CSRF",status_code=403)
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT storage_type,base_path,role FROM storage_targets WHERE id=%s",(storage_id,)); target=cur.fetchone()
            if target and target["storage_type"]=="LOCAL" and target["base_path"]=="/archive/mikrotik":
                return RedirectResponse("/admin/settings?message=Default+local+storage+cannot+be+removed",status_code=303)
            cur.execute("DELETE FROM storage_targets WHERE id=%s AND role<>'PRIMARY'",(storage_id,)); conn.commit()
    finally: conn.close()
    return RedirectResponse("/admin/settings?message=Storage+removed",status_code=303)

@app.post("/admin/settings/storage/restore-local")
def admin_storage_restore_local(request:Request,csrf:str=Form(...)):
    session=get_session(request)
    if not session or not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    if not valid_form_csrf(request,csrf): return HTMLResponse("Invalid CSRF",status_code=403)
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE storage_targets SET role='REPLICA' WHERE role='PRIMARY'")
            cur.execute("""SELECT id FROM storage_targets WHERE storage_type='LOCAL' AND base_path='/archive/mikrotik' ORDER BY id LIMIT 1"""); row=cur.fetchone()
            if row: cur.execute("UPDATE storage_targets SET role='PRIMARY',enabled=1,read_fallback=1,test_status='OK' WHERE id=%s",(row["id"],))
            else: cur.execute("""INSERT INTO storage_targets(name,storage_type,role,enabled,read_fallback,base_path,test_status) VALUES('Storage locale','LOCAL','PRIMARY',1,1,'/archive/mikrotik','OK')""")
            conn.commit()
    finally: conn.close()
    return RedirectResponse("/admin/settings?message=Local+storage+restored",status_code=303)

@app.post("/admin/settings/channels/{channel_id}/toggle")
def admin_channel_toggle(channel_id: int, request: Request, csrf: str=Form(...)):
    session=get_session(request)
    if not session or not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    if not valid_form_csrf(request,csrf): return HTMLResponse("Invalid CSRF",status_code=403)
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE notification_channels SET enabled=IF(enabled=1,0,1) WHERE id=%s",(channel_id,))
            conn.commit()
    finally: conn.close()
    return RedirectResponse("/admin/settings?message=Notification+updated",status_code=303)


@app.post("/admin/settings/channels/simple")
def admin_channel_simple(request: Request, csrf: str=Form(...), name: str=Form(...), channel_type: str=Form(...), destination: str=Form(""), host: str=Form(""), port: int=Form(587), username: str=Form(""), password: str=Form(""), bot_token: str=Form(""), chat_id: str=Form(""), webhook_url: str=Form(""), event_types: list[str]=Form([])):
    session=get_session(request)
    if not session or not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    if not valid_form_csrf(request,csrf): return HTMLResponse("Invalid CSRF",status_code=403)
    kind=channel_type.upper(); allowed={"EMAIL","TELEGRAM","SLACK","DISCORD","WEBHOOK"}
    if kind not in allowed: return HTMLResponse("Invalid channel",status_code=400)
    cfg={}; secret={}
    if kind=="EMAIL":
        cfg={"host":host,"port":port,"from":username,"to":destination,"username":username,"starttls":True}; secret={"password":password}
    elif kind=="TELEGRAM":
        cfg={"chat_id":chat_id}; secret={"bot_token":bot_token}
    else:
        cfg={"url":webhook_url}
    import json
    events=[x for x in event_types if x in {"storage_capacity","storage_unavailable","storage_health","storage_replication","ingestion_stale","database_unavailable","syslog_listener_down"}]
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO notification_channels(name,channel_type,enabled,configuration_json,secret_json,event_types_json) VALUES(%s,%s,1,%s,%s,%s)",(name.strip(),kind,json.dumps(cfg),json.dumps(secret) if secret else None,json.dumps(events)))
            conn.commit()
    finally: conn.close()
    return RedirectResponse("/admin/settings?message=Notification+channel+created",status_code=303)


@app.post("/admin/settings/channels/{channel_id}/test")
def admin_channel_test(channel_id: int, request: Request, csrf: str=Form(...)):
    session=get_session(request)
    if not session or not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    if not valid_form_csrf(request,csrf): return HTMLResponse("Invalid CSRF",status_code=403)
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM notification_channels WHERE id=%s",(channel_id,)); channel=cur.fetchone()
        if not channel: return RedirectResponse("/admin/settings?message=Channel+not+found",status_code=303)
        send_channel(channel,"Network Log Manager - Test","Canale configurato correttamente. Questo è un messaggio di test.")
    except Exception:
        return RedirectResponse("/admin/settings?message=Notification+test+failed",status_code=303)
    finally: conn.close()
    return RedirectResponse("/admin/settings?message=Notification+test+sent",status_code=303)


@app.post("/admin/settings/channels/{channel_id}/events")
def admin_channel_events(channel_id:int,request:Request,csrf:str=Form(...),event_types:list[str]=Form([])):
    session=get_session(request)
    if not session or not is_administrator(session): return HTMLResponse("Forbidden",status_code=403)
    if not valid_form_csrf(request,csrf): return HTMLResponse("Invalid CSRF",status_code=403)
    import json
    allowed={"storage_capacity","storage_unavailable","storage_health","storage_replication","ingestion_stale","database_unavailable","syslog_listener_down"}
    selected=[x for x in event_types if x in allowed]
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE notification_channels SET event_types_json=%s WHERE id=%s",(json.dumps(selected),channel_id)); conn.commit()
    finally: conn.close()
    return RedirectResponse("/admin/settings?message=Notification+events+updated",status_code=303)
