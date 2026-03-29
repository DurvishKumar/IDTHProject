import json
import os
import random
import re
import string
from collections import OrderedDict
from datetime import date, datetime
from functools import wraps
from hashlib import sha256
from itertools import groupby

import pytz
from flask import Flask, flash, g, redirect, render_template, request, session, url_for

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE_URL = os.environ.get("DATABASE_URL")
DATE_TIME_FORMAT = "%Y-%m-%dT%H:%M"
IST = pytz.timezone("Asia/Kolkata")


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-before-production")


@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def get_db():
    if psycopg2 is None or RealDictCursor is None:
        raise RuntimeError("psycopg2-binary is required. Install dependencies before running the app.")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is required.")

    if "db" not in g:
        g.db = psycopg2.connect(
            DATABASE_URL,
            sslmode="require",
            connect_timeout=5,
            application_name="voting_app",
        )
        g.db.autocommit = False
    return g.db


def ensure_connection():
    conn = get_db()
    try:
        conn.poll()
    except Exception:
        old_conn = g.pop("db", None)
        if old_conn is not None:
            try:
                old_conn.close()
            except Exception:
                pass
        conn = get_db()
    return conn


def get_cursor():
    return ensure_connection().cursor(cursor_factory=RealDictCursor)


def execute_query(query, params=None, fetchone=False, fetchall=False):
    conn = ensure_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(query, params or ())
        result = None
        if fetchone:
            result = cur.fetchone()
        elif fetchall:
            result = cur.fetchall()
        conn.commit()
        return result
    except Exception as error:
        conn.rollback()
        raise error
    finally:
        cur.close()


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


def get_db_connection():
    """Backward-compatible alias for the request-scoped PostgreSQL connection."""
    if psycopg2 is None or RealDictCursor is None:
        raise RuntimeError("psycopg2-binary is required. Install dependencies before running the app.")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is required.")
    return ensure_connection()


def hash_text(value):
    """Create a SHA-256 hash for passwords, Aadhaar numbers, and block data."""
    return sha256(value.encode("utf-8")).hexdigest()


def init_db():
    """Create all PostgreSQL tables and seed a default admin account."""
    execute_query(
        """
        CREATE TABLE IF NOT EXISTS admins (
            id SERIAL PRIMARY KEY,
            admin_id TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
        """
    )

    execute_query(
        """
        CREATE TABLE IF NOT EXISTS voters (
            voter_id TEXT PRIMARY KEY,
            full_name TEXT,
            father_name TEXT,
            dob DATE,
            address TEXT,
            constituency TEXT,
            phone TEXT,
            email TEXT,
            hashed_aadhaar TEXT UNIQUE,
            hashed_password TEXT,
            gender TEXT,
            name TEXT,
            contact TEXT,
            password_hash TEXT,
            has_voted BOOLEAN DEFAULT FALSE,
            voted_election_id TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            phone_number TEXT
        )
        """
    )

    execute_query(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'unique_aadhaar'
            ) THEN
                ALTER TABLE voters
                ADD CONSTRAINT unique_aadhaar UNIQUE (hashed_aadhaar);
            END IF;
        END $$;
        """
    )

    execute_query(
        """
        CREATE TABLE IF NOT EXISTS elections (
            id SERIAL PRIMARY KEY,
            election_code TEXT UNIQUE NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    execute_query(
        """
        CREATE TABLE IF NOT EXISTS candidates (
            id SERIAL PRIMARY KEY,
            election_id TEXT DEFAULT 'GENERAL',
            candidate_name TEXT NOT NULL,
            party_name TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    execute_query(
        """
        CREATE TABLE IF NOT EXISTS results (
            id SERIAL PRIMARY KEY,
            election_id TEXT,
            candidate_name TEXT,
            votes INTEGER,
            timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    execute_query(
        """
        CREATE TABLE IF NOT EXISTS blockchain (
            id SERIAL PRIMARY KEY,
            election_id TEXT NOT NULL,
            block_index INT NOT NULL,
            voter_id TEXT,
            candidate_id INT,
            candidate_name TEXT,
            party_name TEXT,
            hash TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            is_valid BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    execute_query(
        """
        CREATE TABLE IF NOT EXISTS admin_settings (
            id SERIAL PRIMARY KEY,
            election_id TEXT UNIQUE,
            tamper_action TEXT DEFAULT 'block'
        )
        """
    )

    admin = execute_query(
        "SELECT id FROM admins WHERE admin_id = %s",
        ("admin",),
        fetchone=True,
    )
    if not admin:
        execute_query(
            "INSERT INTO admins (admin_id, password_hash) VALUES (%s, %s)",
            ("admin", hash_text("Admin@123")),
        )


def build_block_hash(block_payload):
    payload = {
        "election_id": block_payload["election_id"],
        "block_index": block_payload["block_index"],
        "voter_id": block_payload["voter_id"],
        "candidate_id": block_payload["candidate_id"],
        "candidate_name": block_payload["candidate_name"],
        "party_name": block_payload["party_name"],
        "previous_hash": block_payload["previous_hash"],
    }
    return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def get_current_election_id():
    election = get_latest_election()
    return election["election_code"] if election else None


def get_blockchain_rows(election_id):
    return execute_query(
        """
        SELECT id, election_id, block_index, voter_id, candidate_id, candidate_name,
               party_name, hash, previous_hash, is_valid, created_at
        FROM blockchain
        WHERE election_id = %s
        ORDER BY block_index ASC, id ASC
        """,
        (election_id,),
        fetchall=True,
    )


def create_block(vote_data):
    """Append a new vote block to the PostgreSQL blockchain table."""
    election_id = vote_data["election_id"]
    previous_block = execute_query(
        """
        SELECT block_index, hash
        FROM blockchain
        WHERE election_id = %s
        ORDER BY block_index DESC, id DESC
        LIMIT 1
        """,
        (election_id,),
        fetchone=True,
    )
    block_index = (previous_block["block_index"] + 1) if previous_block else 0
    previous_hash = previous_block["hash"] if previous_block else "0"
    block_payload = {
        "election_id": election_id,
        "block_index": block_index,
        "voter_id": vote_data.get("voter_id"),
        "candidate_id": vote_data.get("candidate_id"),
        "candidate_name": vote_data.get("candidate_name"),
        "party_name": vote_data.get("party_name"),
        "previous_hash": previous_hash,
    }
    current_hash = build_block_hash(block_payload)

    execute_query(
        """
        INSERT INTO blockchain (
            election_id, block_index, voter_id, candidate_id, candidate_name, party_name,
            hash, previous_hash
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            election_id,
            block_index,
            block_payload["voter_id"],
            block_payload["candidate_id"],
            block_payload["candidate_name"],
            block_payload["party_name"],
            current_hash,
            previous_hash,
        ),
    )

    block_payload["hash"] = current_hash
    block_payload["is_valid"] = True
    return block_payload


def validate_blockchain(election_id):
    blocks = get_blockchain_rows(election_id)
    mismatches = []
    invalid_found = False

    for position, block in enumerate(blocks):
        expected_hash = build_block_hash(
            {
                "election_id": block["election_id"],
                "block_index": block["block_index"],
                "voter_id": block["voter_id"],
                "candidate_id": block["candidate_id"],
                "candidate_name": block["candidate_name"],
                "party_name": block["party_name"],
                "previous_hash": block["previous_hash"],
            }
        )
        is_valid = True

        if block["hash"] != expected_hash:
            invalid_found = True
            is_valid = False
            mismatches.append(
                {
                    "block_number": block["block_index"],
                    "issue": "Current hash mismatch",
                    "expected": expected_hash,
                    "found": block["hash"],
                }
            )

        if block["block_index"] == 0:
            if block["previous_hash"] != "0":
                invalid_found = True
                is_valid = False
                mismatches.append(
                    {
                        "block_number": block["block_index"],
                        "issue": "Genesis block previous hash mismatch",
                        "expected": "0",
                        "found": block["previous_hash"],
                    }
                )
        else:
            previous_block = blocks[position - 1]
            if previous_block["hash"] != block["previous_hash"]:
                invalid_found = True
                is_valid = False
                mismatches.append(
                    {
                        "block_number": block["block_index"],
                        "issue": "Previous hash link mismatch",
                        "expected": previous_block["hash"],
                        "found": block["previous_hash"],
                    }
                )

        execute_query("UPDATE blockchain SET is_valid = %s WHERE id = %s", (is_valid, block["id"]))

    refreshed_blocks = get_blockchain_rows(election_id)
    return {"valid": not invalid_found, "mismatches": mismatches, "chain": refreshed_blocks}


def get_tamper_action(election_id):
    setting = execute_query(
        "SELECT tamper_action FROM admin_settings WHERE election_id = %s",
        (election_id,),
        fetchone=True,
    )
    return setting["tamper_action"] if setting else "block"


def tamper_block(election_id, block_index):
    """Intentionally modify a stored block to simulate tampering without fixing its hash."""
    if block_index < 0:
        return False, "Choose an existing block number."

    block = execute_query(
        """
        SELECT id
        FROM blockchain
        WHERE election_id = %s AND block_index = %s
        """,
        (election_id, block_index),
        fetchone=True,
    )
    if not block:
        return False, "Choose an existing block number."

    execute_query(
        """
        UPDATE blockchain
        SET candidate_name = %s
        WHERE id = %s
        """,
        ("Tampered Candidate", block["id"]),
    )
    return True, f"Block {block_index} has been tampered with."


def admin_required(view_function):
    @wraps(view_function)
    def wrapper(*args, **kwargs):
        if "admin_logged_in" not in session:
            flash("Please log in as admin first.", "warning")
            return redirect(url_for("admin_login"))
        return view_function(*args, **kwargs)

    return wrapper


def voter_required(view_function):
    @wraps(view_function)
    def wrapper(*args, **kwargs):
        if not session.get("voter_id"):
            flash("Please log in as voter first.", "warning")
            return redirect(url_for("voter_login"))
        return view_function(*args, **kwargs)

    return wrapper


def get_latest_election():
    return execute_query("SELECT * FROM elections ORDER BY id DESC LIMIT 1", fetchone=True)


def parse_datetime(value):
    naive_datetime = datetime.strptime(value, DATE_TIME_FORMAT)
    return IST.localize(naive_datetime)


def get_current_ist_time():
    return datetime.now(IST)


def get_election_status(election):
    if not election:
        return "not_configured"

    now = get_current_ist_time()
    start_time = parse_datetime(election["start_time"])
    end_time = parse_datetime(election["end_time"])

    if now < start_time:
        return "not_started"
    if now > end_time:
        return "ended"
    return "active"


def get_candidates_for_election(election_id):
    return execute_query(
        """
        SELECT id, candidate_name, party_name, election_id
        FROM candidates
        WHERE election_id = %s OR election_id = 'GENERAL'
        ORDER BY id ASC
        """,
        (election_id,),
        fetchall=True,
    )


def get_all_candidates():
    return execute_query(
        """
        SELECT id, candidate_name, party_name, election_id
        FROM candidates
        ORDER BY id ASC
        """,
        fetchall=True,
    )


def calculate_results(election_id):
    rows = execute_query(
        """
        SELECT candidate_name, COUNT(*) AS votes
        FROM blockchain
        WHERE election_id = %s
        GROUP BY candidate_name
        ORDER BY votes DESC, candidate_name ASC
        """,
        (election_id,),
        fetchall=True,
    )
    return {row["candidate_name"]: row["votes"] for row in rows}


def get_stored_results(election_id):
    return execute_query(
        """
        SELECT candidate_name, COUNT(*) AS votes
        FROM blockchain
        WHERE election_id = %s
        GROUP BY candidate_name
        ORDER BY votes DESC, candidate_name ASC
        """,
        (election_id,),
        fetchall=True,
    )


def persist_results_for_election(election):
    return get_stored_results(election["election_code"])


def get_results_history():
    rows = execute_query(
        """
        SELECT election_id, candidate_name, COUNT(*) AS votes, MAX(created_at) AS timestamp
        FROM blockchain
        GROUP BY election_id, candidate_name
        ORDER BY election_id DESC, candidate_name ASC
        """,
        fetchall=True,
    )

    grouped_results = OrderedDict()
    for election_id, election_rows in groupby(rows, key=lambda row: row["election_id"]):
        election_rows = list(election_rows)
        grouped_results.setdefault(
            election_id,
            {"timestamp": election_rows[0]["timestamp"], "rows": election_rows},
        )

    return grouped_results


def get_results_from_blockchain(election_id, valid_only=False):
    query = """
        SELECT candidate_name, COUNT(*) AS votes
        FROM blockchain
        WHERE election_id = %s
    """
    if valid_only:
        query += " AND is_valid = TRUE"
    query += """
        GROUP BY candidate_name
        ORDER BY votes DESC, candidate_name ASC
    """
    return execute_query(query, (election_id,), fetchall=True)


def validate_password(password):
    """Check the password against the requested minimum rule."""
    if len(password) < 6:
        return False, "Password must be at least 6 characters long."
    return True, ""


def validate_email(email):
    if not email:
        return True
    email_pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    return bool(re.match(email_pattern, email))


def validate_phone_number(phone_number):
    return bool(re.fullmatch(r"\d{10}", phone_number))


def calculate_age(dob_text):
    dob = datetime.strptime(dob_text, "%Y-%m-%d").date()
    today = date.today()
    years = today.year - dob.year
    if (today.month, today.day) < (dob.month, dob.day):
        years -= 1
    return years


def generate_voter_id():
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=10))


def generate_unique_voter_id():
    while True:
        voter_id = generate_voter_id()
        existing_voter = execute_query(
            "SELECT 1 FROM voters WHERE voter_id = %s",
            (voter_id,),
            fetchone=True,
        )
        if not existing_voter:
            return voter_id


def build_registration_form_data(source=None):
    data = source or {}
    return {
        "full_name": (data.get("full_name") or "").upper(),
        "dob": data.get("dob", ""),
        "gender": data.get("gender", ""),
        "father_name": (data.get("father_name") or "").upper(),
        "aadhaar": data.get("aadhaar", ""),
        "confirm_aadhaar": data.get("confirm_aadhaar", ""),
        "constituency": (data.get("constituency") or "").upper(),
        "address": (data.get("address") or "").upper(),
        "phone_number": data.get("phone_number", ""),
        "email": (data.get("email") or "").lower(),
        "password": data.get("password", ""),
        "confirm_password": data.get("confirm_password", ""),
    }


def render_register_form(form_data=None, error=None):
    return render_template(
        "register.html",
        form_data=build_registration_form_data(form_data),
        error=error,
    )


@app.context_processor
def inject_common_context():
    election = get_latest_election()
    return {
        "current_election": election,
        "current_election_status": get_election_status(election),
    }


def store_register_feedback(form_data=None, error=None):
    session["registration_form_data"] = build_registration_form_data(form_data)
    session["registration_error"] = error


@app.route("/")
def home():
    election = get_latest_election()
    results_available = bool(election and get_election_status(election) == "ended")
    return render_template(
        "home.html",
        election=election,
        results_available=results_available,
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        form_data = build_registration_form_data(request.form)
        full_name = form_data["full_name"].strip()
        dob = request.form.get("dob", "").strip()
        gender = request.form.get("gender", "").strip()
        father_name = form_data["father_name"].strip()
        aadhaar = request.form.get("aadhaar", "").strip()
        confirm_aadhaar = request.form.get("confirm_aadhaar", "").strip()
        constituency = form_data["constituency"].strip()
        address = form_data["address"].strip()
        phone_number = request.form.get("phone_number", "").strip()
        email = form_data["email"].strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not all(
            [
                full_name,
                dob,
                gender,
                father_name,
                aadhaar,
                confirm_aadhaar,
                constituency,
                address,
                phone_number,
                password,
                confirm_password,
            ]
        ):
            store_register_feedback(form_data, error="Please fill in every required field.")
            return redirect(url_for("register"))

        try:
            if calculate_age(dob) < 18:
                store_register_feedback(form_data, error="Voter must be at least 18 years old.")
                return redirect(url_for("register"))
        except ValueError:
            store_register_feedback(form_data, error="Please enter a valid date of birth.")
            return redirect(url_for("register"))

        if not re.fullmatch(r"\d{12}", aadhaar):
            store_register_feedback(form_data, error="Aadhaar must contain exactly 12 digits.")
            return redirect(url_for("register"))

        if aadhaar != confirm_aadhaar:
            store_register_feedback(form_data, error="Aadhaar and confirm Aadhaar do not match.")
            return redirect(url_for("register"))

        if not validate_phone_number(phone_number):
            store_register_feedback(form_data, error="Phone number must contain exactly 10 digits.")
            return redirect(url_for("register"))

        if not validate_email(email):
            store_register_feedback(form_data, error="Please enter a valid email address.")
            return redirect(url_for("register"))

        password_valid, password_message = validate_password(password)
        if not password_valid:
            store_register_feedback(form_data, error=password_message)
            return redirect(url_for("register"))

        if password != confirm_password:
            store_register_feedback(form_data, error="Password and confirm password do not match.")
            return redirect(url_for("register"))

        hashed_aadhaar = hash_text(aadhaar)
        existing_voter = execute_query(
            "SELECT voter_id FROM voters WHERE hashed_aadhaar = %s",
            (hashed_aadhaar,),
            fetchone=True,
        )
        if existing_voter:
            store_register_feedback(form_data, error="User already registered.")
            return redirect(url_for("register"))

        voter_id = generate_unique_voter_id()
        hashed_password = hash_text(password)
        execute_query(
            """
            INSERT INTO voters (
                voter_id, full_name, father_name, dob, address, constituency,
                phone, email, hashed_aadhaar, hashed_password, gender, name,
                contact, password_hash, created_at, phone_number
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                voter_id,
                full_name,
                father_name,
                dob,
                address,
                constituency,
                phone_number,
                email or None,
                hashed_aadhaar,
                hashed_password,
                gender,
                full_name,
                phone_number,
                hashed_password,
                get_current_ist_time().isoformat(),
                phone_number,
            ),
        )
        session.pop("registration_form_data", None)
        session.pop("registration_error", None)

        flash(f"Registration successful. Your voter ID is {voter_id}", "success")
        return redirect(url_for("voter_login"))

    form_data = session.pop("registration_form_data", None)
    error = session.pop("registration_error", None)
    return render_register_form(form_data, error=error)


@app.route("/voter/login", methods=["GET", "POST"])
def voter_login():
    election = get_latest_election()
    election_status = get_election_status(election)

    if request.method == "POST":
        if election_status == "not_configured":
            flash("No election has been configured by the admin yet.", "warning")
            return redirect(url_for("voter_login"))
        if election_status == "not_started":
            flash("Election has not started yet. Voting login is blocked.", "warning")
            return redirect(url_for("voter_login"))
        if election_status == "ended":
            flash("Election has already ended. Voting login is blocked.", "warning")
            return redirect(url_for("voter_login"))

        voter_id = request.form.get("voter_id", "").strip()
        password = request.form.get("password", "")

        voter = execute_query(
            "SELECT * FROM voters WHERE voter_id = %s AND password_hash = %s",
            (voter_id, hash_text(password)),
            fetchone=True,
        )

        if not voter:
            flash("Invalid voter ID or password.", "danger")
            return redirect(url_for("voter_login"))

        session.clear()
        session["voter_id"] = voter["voter_id"]
        session["voter_name"] = voter["name"]
        flash("Voter login successful.", "success")
        return redirect(url_for("vote"))

    return render_template("voter_login.html", election=election, election_status=election_status)


@app.route("/vote", methods=["GET", "POST"])
@voter_required
def vote():
    election = get_latest_election()
    election_status = get_election_status(election)

    if not election:
        flash("No election is configured right now.", "warning")
        return redirect(url_for("home"))

    voter = execute_query(
        "SELECT * FROM voters WHERE voter_id = %s",
        (session["voter_id"],),
        fetchone=True,
    )

    if not voter:
        session.clear()
        flash("Voter account not found. Please log in again.", "danger")
        return redirect(url_for("voter_login"))

    if election_status != "active":
        flash("Election is not active, so voting is blocked.", "warning")
        return redirect(url_for("home"))

    if voter["has_voted"] and voter["voted_election_id"] == election["election_code"]:
        flash("You have already voted in this election.", "warning")
        return redirect(url_for("home"))

    candidates = get_candidates_for_election(election["election_code"])
    if not candidates:
        flash("No candidates are available for this election yet.", "warning")
        return redirect(url_for("home"))

    if request.method == "POST":
        selected_candidate_id = request.form.get("candidate_id")
        if not selected_candidate_id:
            flash("Please choose one candidate before submitting your vote.", "danger")
            return redirect(url_for("vote"))

        voter = execute_query(
            "SELECT * FROM voters WHERE voter_id = %s",
            (session["voter_id"],),
            fetchone=True,
        )
        candidate = execute_query(
            """
            SELECT id, candidate_name, party_name, election_id
            FROM candidates
            WHERE id = %s AND (election_id = %s OR election_id = 'GENERAL')
            """,
            (selected_candidate_id, election["election_code"]),
            fetchone=True,
        )

        if get_election_status(election) != "active":
            flash("Election is no longer active. Vote was not recorded.", "danger")
            return redirect(url_for("home"))

        if voter["has_voted"] and voter["voted_election_id"] == election["election_code"]:
            flash("You have already voted in this election.", "warning")
            return redirect(url_for("home"))

        if not candidate:
            flash("Selected candidate was not found.", "danger")
            return redirect(url_for("vote"))

        vote_data = {
            "election_id": election["election_code"],
            "election_code": election["election_code"],
            "voter_id": voter["voter_id"],
            "candidate_id": candidate["id"],
            "candidate_name": candidate["candidate_name"],
            "party_name": candidate["party_name"],
        }
        create_block(vote_data)

        execute_query(
            "UPDATE voters SET has_voted = TRUE, voted_election_id = %s WHERE voter_id = %s",
            (election["election_code"], voter["voter_id"]),
        )

        flash("Your vote has been securely added to the blockchain.", "success")
        return redirect(url_for("home"))

    return render_template("vote.html", election=election, candidates=candidates, voter=voter)


@app.route("/results")
def results():
    election = get_latest_election()
    if not election:
        return render_template("results.html", election=None, results=None, message="No election configured")

    if get_election_status(election) != "ended":
        return render_template(
            "results.html",
            election=election,
            results=None,
            message="Results not available yet",
        )

    election_id = election["election_code"]
    verification = validate_blockchain(election_id)
    tamper_action = get_tamper_action(election_id)

    if not verification["valid"] and tamper_action == "block":
        return render_template(
            "results.html",
            election=election,
            results=None,
            message="Blockchain tampered. Results blocked.",
        )

    result_rows = get_results_from_blockchain(
        election_id,
        valid_only=not verification["valid"] and tamper_action == "partial",
    )
    return render_template(
        "results.html",
        election=election,
        results=result_rows,
        message=None,
        tampered=not verification["valid"],
        tamper_action=tamper_action,
    )


@app.route("/admin/results")
@admin_required
def admin_results():
    election = get_latest_election()
    if not election:
        flash("No election is configured yet.", "warning")
        return redirect(url_for("admin_dashboard"))

    if get_election_status(election) != "ended":
        flash("Results not available yet", "warning")
        return redirect(url_for("admin_dashboard"))

    election_id = election["election_code"]
    verification = validate_blockchain(election_id)
    tamper_action = get_tamper_action(election_id)

    if not verification["valid"] and tamper_action == "block":
        return render_template(
            "admin_results.html",
            election=election,
            result_rows=[],
            verification=verification,
            tamper_action=tamper_action,
            message="Blockchain tampered. Results blocked.",
        )

    result_rows = get_results_from_blockchain(
        election_id,
        valid_only=not verification["valid"] and tamper_action == "partial",
    )
    return render_template(
        "admin_results.html",
        election=election,
        result_rows=result_rows,
        verification=verification,
        tamper_action=tamper_action,
        message=None,
    )


@app.route("/admin/results/history")
@admin_required
def admin_results_history():
    return render_template("admin_results_history.html", grouped_results=get_results_history())


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        admin_id = request.form.get("admin_id", "").strip()
        password = request.form.get("password", "")

        admin = execute_query(
            "SELECT * FROM admins WHERE admin_id = %s AND password_hash = %s",
            (admin_id, hash_text(password)),
            fetchone=True,
        )

        if not admin:
            flash("Invalid admin ID or password.", "danger")
            return redirect(url_for("admin_login"))

        session.clear()
        session["admin_logged_in"] = True
        session["admin_id"] = admin["admin_id"]
        flash("Admin login successful.", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_login.html")


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    election = get_latest_election()
    candidates = get_all_candidates()
    tamper_action = get_tamper_action(election["election_code"]) if election else "block"

    return render_template(
        "admin_dashboard.html",
        election=election,
        candidates=candidates,
        tamper_action=tamper_action,
    )


@app.route("/admin/election/create", methods=["POST"])
@admin_required
def create_election():
    election_code = request.form.get("election_code", "").strip()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()

    if not all([election_code, start_time, end_time]):
        flash("Please enter election ID, start time, and end time.", "danger")
        return redirect(url_for("admin_dashboard"))

    try:
        start_dt = parse_datetime(start_time)
        end_dt = parse_datetime(end_time)
    except ValueError:
        flash("Please enter valid start and end time values.", "danger")
        return redirect(url_for("admin_dashboard"))

    if end_dt <= start_dt:
        flash("End time must be after start time.", "danger")
        return redirect(url_for("admin_dashboard"))

    try:
        execute_query(
            """
            INSERT INTO elections (election_code, start_time, end_time, created_at)
            VALUES (%s, %s, %s, %s)
            """,
            (election_code, start_time, end_time, get_current_ist_time().isoformat()),
        )
        flash("Election configured successfully.", "success")
    except Exception:
        flash("Election ID must be unique.", "danger")

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/set_tamper_action", methods=["POST"])
@admin_required
def set_tamper_action():
    action = request.form.get("action", "block").strip().lower()
    election_id = get_current_election_id()

    if action not in {"block", "partial"}:
        flash("Choose a valid tampering action.", "danger")
        return redirect(url_for("admin_dashboard"))

    if not election_id:
        flash("Create an election before saving tamper settings.", "warning")
        return redirect(url_for("admin_dashboard"))

    execute_query(
        """
        INSERT INTO admin_settings (election_id, tamper_action)
        VALUES (%s, %s)
        ON CONFLICT (election_id)
        DO UPDATE SET tamper_action = EXCLUDED.tamper_action
        """,
        (election_id, action),
    )
    flash("Tamper handling updated successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/candidates/add", methods=["POST"])
@admin_required
def add_candidate():
    election = get_latest_election()
    election_id = election["election_code"] if election else "GENERAL"

    candidate_name = request.form.get("candidate_name", "").strip()
    party_name = request.form.get("party_name", "").strip()

    if not candidate_name or not party_name:
        flash("Candidate name and party name are required.", "danger")
        return redirect(url_for("admin_dashboard"))

    execute_query(
        """
        INSERT INTO candidates (election_id, candidate_name, party_name, created_at)
        VALUES (%s, %s, %s, %s)
        """,
        (election_id, candidate_name, party_name, get_current_ist_time().isoformat()),
    )

    flash("Candidate added successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/remove_candidate/<int:candidate_id>", methods=["POST"])
@admin_required
def remove_candidate(candidate_id):
    execute_query("DELETE FROM candidates WHERE id = %s", (candidate_id,))

    flash("Candidate removed successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/voters")
@admin_required
def admin_voters():
    voters = execute_query(
        """
        SELECT
            voter_id,
            COALESCE(full_name, name) AS full_name,
            father_name,
            address,
            constituency,
            phone_number,
            email
        FROM voters
        ORDER BY created_at DESC NULLS LAST, voter_id DESC
        """,
        fetchall=True,
    )
    return render_template("admin_voters.html", voters=voters)


@app.route("/admin/delete_voter/<voter_id>", methods=["POST"])
@admin_required
def delete_voter(voter_id):
    print("Deleting voter:", voter_id)
    print("DATABASE_URL configured:", bool(DATABASE_URL))
    execute_query("DELETE FROM voters WHERE voter_id = %s", (voter_id,))
    delete_check = execute_query(
        "SELECT voter_id FROM voters WHERE voter_id = %s",
        (voter_id,),
        fetchone=True,
    )
    print("CHECK DELETE:", delete_check)

    flash("Voter deleted successfully.", "success")
    return redirect(url_for("admin_voters"))


@app.route("/admin/blockchain")
@admin_required
def admin_blockchain():
    election = get_latest_election()
    if not election:
        flash("No election is configured yet.", "warning")
        return redirect(url_for("admin_dashboard"))

    verification = validate_blockchain(election["election_code"])
    return render_template("admin_blockchain.html", verification=verification, election=election)


@app.route("/admin/blockchain/tamper", methods=["POST"])
@admin_required
def simulate_tampering():
    block_index = request.form.get("block_index", "").strip()
    if not block_index.isdigit():
        flash("Enter a valid block number for tampering simulation.", "danger")
        return redirect(url_for("admin_blockchain"))

    election_id = get_current_election_id()
    if not election_id:
        flash("No election is configured yet.", "warning")
        return redirect(url_for("admin_dashboard"))

    success, message = tamper_block(election_id, int(block_index))
    flash(message, "warning" if success else "danger")
    return redirect(url_for("admin_blockchain"))


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    flash("Admin logged out successfully.", "success")
    return redirect(url_for("home"))


@app.route("/voter/logout")
def voter_logout():
    session.clear()
    flash("Voter logged out successfully.", "success")
    return redirect(url_for("home"))


if psycopg2 is not None and DATABASE_URL:
    with app.app_context():
        init_db()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
