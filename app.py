import json
import os
import re
import string
from collections import OrderedDict
from datetime import date, datetime
from functools import wraps
from hashlib import sha256

import pytz
from flask import Flask, flash, redirect, render_template, request, session, url_for

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
BLOCKCHAIN_PATH = os.environ.get("BLOCKCHAIN_PATH", os.path.join(BASE_DIR, "blockchain.json"))
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


class PostgresConnection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, query, params=None):
        cursor = self._connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(query, params or ())
        return cursor

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


def get_db_connection():
    """Create a PostgreSQL connection using DATABASE_URL."""
    if psycopg2 is None or RealDictCursor is None:
        raise RuntimeError("psycopg2-binary is required. Install dependencies before running the app.")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is required.")

    connection = psycopg2.connect(DATABASE_URL)
    return PostgresConnection(connection)


def hash_text(value):
    """Create a SHA-256 hash for passwords, Aadhaar numbers, and block data."""
    return sha256(value.encode("utf-8")).hexdigest()


def hash_aadhaar(aadhaar):
    return hash_text(aadhaar)


def init_db():
    """Create all PostgreSQL tables and seed a default admin account."""
    connection = get_db_connection()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS admins (
            id SERIAL PRIMARY KEY,
            admin_id TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
        """
    )

    connection.execute(
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

    connection.execute(
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

    connection.execute(
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

    connection.execute(
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

    connection.execute(
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

    admin = connection.execute("SELECT id FROM admins WHERE admin_id = %s", ("admin",)).fetchone()
    if not admin:
        connection.execute(
            "INSERT INTO admins (admin_id, password_hash) VALUES (%s, %s)",
            ("admin", hash_text("Admin@123")),
        )

    connection.commit()
    connection.close()


def hash_block(block):
    """Hash the core contents of a block, excluding the block's own current hash."""
    payload = {
        "index": block["index"],
        "timestamp": block["timestamp"],
        "vote_data": block["vote_data"],
        "previous_hash": block["previous_hash"],
    }
    return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def ensure_blockchain():
    """Create the blockchain file with one genesis block on first run."""
    if os.path.exists(BLOCKCHAIN_PATH):
        return

    genesis_block = {
        "index": 0,
        "timestamp": get_current_ist_time().isoformat(),
        "vote_data": {"message": "Genesis Block"},
        "previous_hash": "0",
    }
    genesis_block["current_hash"] = hash_block(genesis_block)

    with open(BLOCKCHAIN_PATH, "w", encoding="utf-8") as blockchain_file:
        json.dump([genesis_block], blockchain_file, indent=4)


def load_chain():
    ensure_blockchain()
    with open(BLOCKCHAIN_PATH, "r", encoding="utf-8") as blockchain_file:
        return json.load(blockchain_file)


def save_chain(chain):
    with open(BLOCKCHAIN_PATH, "w", encoding="utf-8") as blockchain_file:
        json.dump(chain, blockchain_file, indent=4)


def create_block(vote_data):
    """Append a new vote block to the JSON chain."""
    chain = load_chain()
    previous_block = chain[-1]
    new_block = {
        "index": len(chain),
        "timestamp": get_current_ist_time().isoformat(),
        "vote_data": vote_data,
        "previous_hash": previous_block["current_hash"],
    }
    new_block["current_hash"] = hash_block(new_block)
    chain.append(new_block)
    save_chain(chain)
    return new_block


def verify_chain():
    """
    Check every block for two things:
    1. The stored current hash still matches the block contents.
    2. Each block still correctly links to the previous block.
    """
    chain = load_chain()
    mismatches = []

    for index, block in enumerate(chain):
        expected_hash = hash_block(block)
        if block.get("current_hash") != expected_hash:
            mismatches.append(
                {
                    "block_number": index,
                    "issue": "Current hash mismatch",
                    "expected": expected_hash,
                    "found": block.get("current_hash"),
                }
            )

        if index == 0:
            if block.get("previous_hash") != "0":
                mismatches.append(
                    {
                        "block_number": index,
                        "issue": "Genesis block previous hash mismatch",
                        "expected": "0",
                        "found": block.get("previous_hash"),
                    }
                )
            continue

        previous_block = chain[index - 1]
        if block.get("previous_hash") != previous_block.get("current_hash"):
            mismatches.append(
                {
                    "block_number": index,
                    "issue": "Previous hash link mismatch",
                    "expected": previous_block.get("current_hash"),
                    "found": block.get("previous_hash"),
                }
            )

    return {"valid": len(mismatches) == 0, "mismatches": mismatches, "chain": chain}


def tamper_block(block_index):
    """Intentionally modify a non-genesis block to simulate tampering."""
    chain = load_chain()
    if block_index <= 0 or block_index >= len(chain):
        return False, "Choose an existing non-genesis block number."

    chain[block_index]["vote_data"]["candidate_name"] = "Tampered Candidate"
    chain[block_index]["vote_data"]["tampered"] = True
    save_chain(chain)
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
    connection = get_db_connection()
    election = connection.execute("SELECT * FROM elections ORDER BY id DESC LIMIT 1").fetchone()
    connection.close()
    return election


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
    connection = get_db_connection()
    candidates = connection.execute(
        """
        SELECT id, candidate_name, party_name, election_id
        FROM candidates
        WHERE election_id = %s OR election_id = 'GENERAL'
        ORDER BY id ASC
        """,
        (election_id,),
    ).fetchall()
    connection.close()
    return candidates


def get_all_candidates():
    connection = get_db_connection()
    candidates = connection.execute(
        """
        SELECT id, candidate_name, party_name, election_id
        FROM candidates
        ORDER BY id ASC
        """
    ).fetchall()
    connection.close()
    return candidates


def calculate_results(election_id):
    """Count votes on the blockchain for one specific election."""
    results = {}
    for block in load_chain()[1:]:
        vote_data = block.get("vote_data", {})
        if vote_data.get("election_id") != election_id:
            continue
        candidate_name = vote_data.get("candidate_name", "Unknown Candidate")
        results[candidate_name] = results.get(candidate_name, 0) + 1
    return results


def get_stored_results(election_id):
    connection = get_db_connection()
    rows = connection.execute(
        """
        SELECT candidate_name, votes, timestamp
        FROM results
        WHERE election_id = %s
        ORDER BY id ASC
        """,
        (election_id,),
    ).fetchall()
    connection.close()
    return rows


def persist_results_for_election(election):
    """
    Persist results once per election after it ends.
    Results are stored in SQLite and read from there afterwards.
    """
    election_id = election["election_code"]
    existing_results = get_stored_results(election_id)
    if existing_results:
        return existing_results

    candidate_rows = get_candidates_for_election(election_id)
    blockchain_counts = calculate_results(election_id)

    connection = get_db_connection()
    for candidate in candidate_rows:
        candidate_name = candidate["candidate_name"]
        connection.execute(
            """
            INSERT INTO results (election_id, candidate_name, votes)
            VALUES (%s, %s, %s)
            """,
            (election_id, candidate_name, blockchain_counts.get(candidate_name, 0)),
        )
    connection.commit()
    connection.close()
    return get_stored_results(election_id)


def get_results_history():
    connection = get_db_connection()
    rows = connection.execute(
        """
        SELECT election_id, candidate_name, votes, timestamp
        FROM results
        ORDER BY timestamp DESC, id DESC
        """
    ).fetchall()
    connection.close()

    grouped_results = OrderedDict()
    for row in rows:
        election_id = row["election_id"]
        grouped_results.setdefault(
            election_id,
            {"timestamp": row["timestamp"], "rows": []},
        )
        grouped_results[election_id]["rows"].append(row)

    return grouped_results


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


def generate_voter_id_from_aadhaar(aadhaar):
    hash_value = hash_aadhaar(aadhaar)
    allowed_chars = string.ascii_uppercase + string.digits

    voter_id = ""
    for ch in hash_value:
        index = int(ch, 16) % len(allowed_chars)
        voter_id += allowed_chars[index]

        if len(voter_id) == 10:
            break

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
    return render_template("home.html")


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

        hashed_aadhaar = hash_aadhaar(aadhaar)
        connection = get_db_connection()
        existing_voter = connection.execute(
            "SELECT voter_id FROM voters WHERE hashed_aadhaar = %s",
            (hashed_aadhaar,),
        ).fetchone()
        connection.close()
        if existing_voter:
            store_register_feedback(form_data, error="User already registered.")
            return redirect(url_for("register"))

        voter_id = generate_voter_id_from_aadhaar(aadhaar)
        hashed_password = hash_text(password)
        connection = get_db_connection()
        connection.execute(
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
        connection.commit()
        connection.close()
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

        connection = get_db_connection()
        voter = connection.execute(
            "SELECT * FROM voters WHERE voter_id = %s AND password_hash = %s",
            (voter_id, hash_text(password)),
        ).fetchone()
        connection.close()

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

    connection = get_db_connection()
    voter = connection.execute(
        "SELECT * FROM voters WHERE voter_id = %s",
        (session["voter_id"],),
    ).fetchone()
    connection.close()

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

        connection = get_db_connection()
        voter = connection.execute(
            "SELECT * FROM voters WHERE voter_id = %s",
            (session["voter_id"],),
        ).fetchone()
        candidate = connection.execute(
            """
            SELECT id, candidate_name, party_name, election_id
            FROM candidates
            WHERE id = %s AND (election_id = %s OR election_id = 'GENERAL')
            """,
            (selected_candidate_id, election["election_code"]),
        ).fetchone()
        connection.close()

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

        connection = get_db_connection()
        connection.execute(
            "UPDATE voters SET has_voted = TRUE, voted_election_id = %s WHERE voter_id = %s",
            (election["election_code"], voter["voter_id"]),
        )
        connection.commit()
        connection.close()

        flash("Your vote has been securely added to the blockchain.", "success")
        return redirect(url_for("home"))

    return render_template("vote.html", election=election, candidates=candidates, voter=voter)


@app.route("/results")
def results():
    flash("Results are available only from the admin results page.", "warning")
    return redirect(url_for("home"))


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

    result_rows = persist_results_for_election(election)
    return render_template("admin_results.html", election=election, result_rows=result_rows)


@app.route("/admin/results/history")
@admin_required
def admin_results_history():
    return render_template("admin_results_history.html", grouped_results=get_results_history())


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        admin_id = request.form.get("admin_id", "").strip()
        password = request.form.get("password", "")

        connection = get_db_connection()
        admin = connection.execute(
            "SELECT * FROM admins WHERE admin_id = %s AND password_hash = %s",
            (admin_id, hash_text(password)),
        ).fetchone()
        connection.close()

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

    return render_template(
        "admin_dashboard.html",
        election=election,
        candidates=candidates,
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

    connection = get_db_connection()
    try:
        connection.execute(
            """
            INSERT INTO elections (election_code, start_time, end_time, created_at)
            VALUES (%s, %s, %s, %s)
            """,
            (election_code, start_time, end_time, get_current_ist_time().isoformat()),
        )
        connection.commit()
        flash("Election configured successfully.", "success")
    except Exception:
        connection.rollback()
        flash("Election ID must be unique.", "danger")
    finally:
        connection.close()

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

    connection = get_db_connection()
    connection.execute(
        """
        INSERT INTO candidates (election_id, candidate_name, party_name, created_at)
        VALUES (%s, %s, %s, %s)
        """,
        (election_id, candidate_name, party_name, get_current_ist_time().isoformat()),
    )
    connection.commit()
    connection.close()

    flash("Candidate added successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/remove_candidate/<int:candidate_id>", methods=["POST"])
@admin_required
def remove_candidate(candidate_id):
    connection = get_db_connection()
    connection.execute("DELETE FROM candidates WHERE id = %s", (candidate_id,))
    connection.commit()
    connection.close()

    flash("Candidate removed successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/voters")
@admin_required
def admin_voters():
    connection = get_db_connection()
    voters = connection.execute(
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
        """
    ).fetchall()
    connection.close()
    return render_template("admin_voters.html", voters=voters)


@app.route("/admin/delete_voter/<voter_id>", methods=["POST"])
@admin_required
def delete_voter(voter_id):
    print("Deleting voter:", voter_id)
    print("DATABASE_URL configured:", bool(DATABASE_URL))
    connection = get_db_connection()
    cursor = connection.execute("DELETE FROM voters WHERE voter_id = %s", (voter_id,))
    connection.commit()
    check_cursor = connection.execute("SELECT voter_id FROM voters WHERE voter_id = %s", (voter_id,))
    print("CHECK DELETE:", check_cursor.fetchone())
    cursor.close()
    check_cursor.close()
    connection.close()

    flash("Voter deleted successfully.", "success")
    return redirect(url_for("admin_voters"))


@app.route("/admin/blockchain")
@admin_required
def admin_blockchain():
    verification = verify_chain()
    return render_template("admin_blockchain.html", verification=verification)


@app.route("/admin/blockchain/tamper", methods=["POST"])
@admin_required
def simulate_tampering():
    block_index = request.form.get("block_index", "").strip()
    if not block_index.isdigit():
        flash("Enter a valid block number for tampering simulation.", "danger")
        return redirect(url_for("admin_blockchain"))

    success, message = tamper_block(int(block_index))
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
    init_db()
ensure_blockchain()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
