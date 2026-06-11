"""Mileage Tracker — business mileage logging with IRS year-end summaries."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from functools import wraps

from flask import (
    Flask,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

# IRS standard mileage rates (cents per mile)
IRS_MILEAGE_RATES: dict[int, float] = {
    2022: 0.625,
    2023: 0.655,
    2024: 0.670,
    2025: 0.700,
    2026: 0.700,
}

DATABASE = os.environ.get("MILEAGE_DB", "mileage.db")


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-mileage-tracker-key-change-in-prod"),
        DATABASE=DATABASE,
    )
    if test_config:
        app.config.update(test_config)

    @app.before_request
    def load_user():
        user_id = session.get("user_id")
        g.user = get_user_by_id(user_id) if user_id else None

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    register_routes(app)
    return app


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def init_db(database_path: str | None = None):
    path = database_path or DATABASE
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                make TEXT,
                model TEXT,
                year INTEGER,
                license_plate TEXT,
                is_default INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
                trip_date TEXT NOT NULL,
                distance_miles REAL NOT NULL,
                purpose TEXT NOT NULL CHECK (purpose IN ('business', 'personal')),
                start_location TEXT,
                end_location TEXT,
                odometer_start REAL,
                odometer_end REAL,
                notes TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def get_user_by_id(user_id):
    if not user_id:
        return None
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str):
    row = get_db().execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
    return dict(row) if row else None


def clear_default_vehicle(user_id: int):
    get_db().execute(
        "UPDATE vehicles SET is_default = 0 WHERE user_id = ?",
        (user_id,),
    )


def register_routes(app: Flask):
    @app.route("/")
    def index():
        if g.user:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            display_name = (request.form.get("display_name") or "").strip()
            password = request.form.get("password") or ""
            confirm = request.form.get("confirm_password") or ""

            errors = []
            if not email or "@" not in email:
                errors.append("A valid email address is required.")
            if not display_name:
                errors.append("Display name is required.")
            if len(password) < 8:
                errors.append("Password must be at least 8 characters.")
            if password != confirm:
                errors.append("Passwords do not match.")
            if get_user_by_email(email):
                errors.append("An account with this email already exists.")

            if errors:
                for err in errors:
                    flash(err, "error")
                return render_template("register.html")

            db = get_db()
            db.execute(
                "INSERT INTO users (email, password_hash, display_name, created_at) VALUES (?, ?, ?, ?)",
                (email, generate_password_hash(password), display_name, datetime.now(UTC).isoformat()),
            )
            db.commit()
            user = get_user_by_email(email)
            session.clear()
            session["user_id"] = user["id"]
            flash("Welcome! Your account has been created.", "success")
            return redirect(url_for("dashboard"))

        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            user = get_user_by_email(email)
            if user is None or not check_password_hash(user["password_hash"], password):
                flash("Invalid email or password.", "error")
                return render_template("login.html")
            session.clear()
            session["user_id"] = user["id"]
            flash(f"Welcome back, {user['display_name']}!", "success")
            next_url = request.args.get("next") or request.form.get("next")
            if next_url and next_url.startswith("/"):
                return redirect(next_url)
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        session.clear()
        flash("You have been logged out.", "success")
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        uid = g.user["id"]
        db = get_db()
        year = date.today().year
        stats = db.execute(
            """
            SELECT
                COUNT(*) AS trip_count,
                COALESCE(SUM(CASE WHEN purpose = 'business' THEN distance_miles ELSE 0 END), 0) AS business_miles,
                COALESCE(SUM(distance_miles), 0) AS total_miles
            FROM trips WHERE user_id = ? AND strftime('%Y', trip_date) = ?
            """,
            (uid, str(year)),
        ).fetchone()
        recent = db.execute(
            """
            SELECT t.*, v.label AS vehicle_label
            FROM trips t
            JOIN vehicles v ON v.id = t.vehicle_id
            WHERE t.user_id = ?
            ORDER BY t.trip_date DESC, t.id DESC
            LIMIT 5
            """,
            (uid,),
        ).fetchall()
        vehicle_count = db.execute(
            "SELECT COUNT(*) AS c FROM vehicles WHERE user_id = ? AND is_active = 1",
            (uid,),
        ).fetchone()["c"]
        rate = IRS_MILEAGE_RATES.get(year, 0.70)
        estimated_deduction = stats["business_miles"] * rate
        return render_template(
            "dashboard.html",
            stats=stats,
            recent=recent,
            vehicle_count=vehicle_count,
            year=year,
            rate=rate,
            estimated_deduction=estimated_deduction,
        )

    @app.route("/trips")
    @login_required
    def trips_list():
        uid = g.user["id"]
        purpose_filter = request.args.get("purpose", "")
        vehicle_filter = request.args.get("vehicle", "")
        sort = request.args.get("sort", "date_desc")

        query = """
            SELECT t.*, v.label AS vehicle_label
            FROM trips t
            JOIN vehicles v ON v.id = t.vehicle_id
            WHERE t.user_id = ?
        """
        params: list = [uid]
        if purpose_filter in ("business", "personal"):
            query += " AND t.purpose = ?"
            params.append(purpose_filter)
        if vehicle_filter.isdigit():
            query += " AND t.vehicle_id = ?"
            params.append(int(vehicle_filter))

        if sort == "date_asc":
            query += " ORDER BY t.trip_date ASC, t.id ASC"
        elif sort == "miles_desc":
            query += " ORDER BY t.distance_miles DESC"
        else:
            query += " ORDER BY t.trip_date DESC, t.id DESC"

        trips = get_db().execute(query, params).fetchall()
        vehicles = get_db().execute(
            "SELECT id, label FROM vehicles WHERE user_id = ? ORDER BY label",
            (uid,),
        ).fetchall()
        totals = get_db().execute(
            """
            SELECT
                COALESCE(SUM(distance_miles), 0) AS total,
                COALESCE(SUM(CASE WHEN purpose = 'business' THEN distance_miles ELSE 0 END), 0) AS business
            FROM trips WHERE user_id = ?
            """,
            (uid,),
        ).fetchone()
        return render_template(
            "trips.html",
            trips=trips,
            vehicles=vehicles,
            purpose_filter=purpose_filter,
            vehicle_filter=vehicle_filter,
            sort=sort,
            totals=totals,
        )

    @app.route("/trips/new", methods=["GET", "POST"])
    @app.route("/trips/<int:trip_id>/edit", methods=["GET", "POST"])
    @login_required
    def trip_form(trip_id: int | None = None):
        uid = g.user["id"]
        db = get_db()
        trip = None
        if trip_id:
            trip = db.execute(
                "SELECT * FROM trips WHERE id = ? AND user_id = ?",
                (trip_id, uid),
            ).fetchone()
            if not trip:
                flash("Trip not found.", "error")
                return redirect(url_for("trips_list"))

        vehicles = db.execute(
            "SELECT * FROM vehicles WHERE user_id = ? AND is_active = 1 ORDER BY is_default DESC, label",
            (uid,),
        ).fetchall()
        if not vehicles:
            flash("Add a vehicle before logging trips.", "warning")
            return redirect(url_for("vehicle_form"))

        if request.method == "POST":
            trip_date = request.form.get("trip_date") or ""
            vehicle_id = request.form.get("vehicle_id")
            purpose = request.form.get("purpose") or ""
            distance = request.form.get("distance_miles")
            start_loc = (request.form.get("start_location") or "").strip()
            end_loc = (request.form.get("end_location") or "").strip()
            odo_start = request.form.get("odometer_start") or None
            odo_end = request.form.get("odometer_end") or None
            notes = (request.form.get("notes") or "").strip()

            errors = []
            try:
                datetime.strptime(trip_date, "%Y-%m-%d")
            except ValueError:
                errors.append("A valid trip date is required.")

            if purpose not in ("business", "personal"):
                errors.append("Purpose must be business or personal.")

            try:
                vehicle_id_int = int(vehicle_id)
                v = db.execute(
                    "SELECT id FROM vehicles WHERE id = ? AND user_id = ? AND is_active = 1",
                    (vehicle_id_int, uid),
                ).fetchone()
                if not v:
                    errors.append("Select a valid active vehicle.")
            except (TypeError, ValueError):
                errors.append("Select a valid vehicle.")
                vehicle_id_int = 0

            dist_val = None
            if odo_start and odo_end:
                try:
                    dist_val = float(odo_end) - float(odo_start)
                    if dist_val <= 0:
                        errors.append("Odometer end must be greater than start.")
                except ValueError:
                    errors.append("Odometer readings must be numbers.")
            if dist_val is None:
                try:
                    dist_val = float(distance)
                    if dist_val <= 0:
                        errors.append("Distance must be greater than zero.")
                except (TypeError, ValueError):
                    errors.append("Enter a valid distance or odometer readings.")

            if errors:
                for err in errors:
                    flash(err, "error")
                return render_template("trip_form.html", trip=trip, vehicles=vehicles, form=request.form)

            odo_start_val = float(odo_start) if odo_start else None
            odo_end_val = float(odo_end) if odo_end else None

            if trip:
                db.execute(
                    """
                    UPDATE trips SET vehicle_id=?, trip_date=?, distance_miles=?, purpose=?,
                    start_location=?, end_location=?, odometer_start=?, odometer_end=?, notes=?
                    WHERE id=? AND user_id=?
                    """,
                    (
                        vehicle_id_int,
                        trip_date,
                        dist_val,
                        purpose,
                        start_loc,
                        end_loc,
                        odo_start_val,
                        odo_end_val,
                        notes,
                        trip_id,
                        uid,
                    ),
                )
                flash("Trip updated.", "success")
            else:
                db.execute(
                    """
                    INSERT INTO trips (user_id, vehicle_id, trip_date, distance_miles, purpose,
                    start_location, end_location, odometer_start, odometer_end, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uid,
                        vehicle_id_int,
                        trip_date,
                        dist_val,
                        purpose,
                        start_loc,
                        end_loc,
                        odo_start_val,
                        odo_end_val,
                        notes,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                flash("Trip logged successfully.", "success")
            db.commit()
            return redirect(url_for("trips_list"))

        return render_template("trip_form.html", trip=trip, vehicles=vehicles, form=None)

    @app.route("/trips/<int:trip_id>/delete", methods=["POST"])
    @login_required
    def trip_delete(trip_id: int):
        db = get_db()
        result = db.execute(
            "DELETE FROM trips WHERE id = ? AND user_id = ?",
            (trip_id, g.user["id"]),
        )
        db.commit()
        if result.rowcount:
            flash("Trip deleted.", "success")
        else:
            flash("Trip not found.", "error")
        return redirect(url_for("trips_list"))

    @app.route("/vehicles")
    @login_required
    def vehicles_list():
        uid = g.user["id"]
        vehicles = get_db().execute(
            """
            SELECT v.*,
                (SELECT COUNT(*) FROM trips t WHERE t.vehicle_id = v.id) AS trip_count
            FROM vehicles v
            WHERE v.user_id = ?
            ORDER BY v.is_active DESC, v.is_default DESC, v.label
            """,
            (uid,),
        ).fetchall()
        return render_template("vehicles.html", vehicles=vehicles)

    @app.route("/vehicles/new", methods=["GET", "POST"])
    @app.route("/vehicles/<int:vehicle_id>/edit", methods=["GET", "POST"])
    @login_required
    def vehicle_form(vehicle_id: int | None = None):
        uid = g.user["id"]
        db = get_db()
        vehicle = None
        if vehicle_id:
            vehicle = db.execute(
                "SELECT * FROM vehicles WHERE id = ? AND user_id = ?",
                (vehicle_id, uid),
            ).fetchone()
            if not vehicle:
                flash("Vehicle not found.", "error")
                return redirect(url_for("vehicles_list"))

        if request.method == "POST":
            label = (request.form.get("label") or "").strip()
            make = (request.form.get("make") or "").strip()
            model = (request.form.get("model") or "").strip()
            year_raw = (request.form.get("year") or "").strip()
            plate = (request.form.get("license_plate") or "").strip()
            is_default = request.form.get("is_default") == "on"
            is_active = request.form.get("is_active", "on") == "on"

            errors = []
            if not label:
                errors.append("Vehicle name is required.")
            year_val = None
            if year_raw:
                try:
                    year_val = int(year_raw)
                    if year_val < 1900 or year_val > 2100:
                        errors.append("Enter a valid vehicle year.")
                except ValueError:
                    errors.append("Year must be a number.")

            if vehicle and not is_active:
                active_trips = db.execute(
                    "SELECT COUNT(*) AS c FROM trips WHERE vehicle_id = ?",
                    (vehicle_id,),
                ).fetchone()["c"]
                if active_trips:
                    pass  # allow archive; historical trips keep reference

            if errors:
                for err in errors:
                    flash(err, "error")
                return render_template("vehicle_form.html", vehicle=vehicle, form=request.form)

            if is_default:
                clear_default_vehicle(uid)

            if vehicle:
                db.execute(
                    """
                    UPDATE vehicles SET label=?, make=?, model=?, year=?, license_plate=?,
                    is_default=?, is_active=? WHERE id=? AND user_id=?
                    """,
                    (
                        label,
                        make,
                        model,
                        year_val,
                        plate,
                        1 if is_default else 0,
                        1 if is_active else 0,
                        vehicle_id,
                        uid,
                    ),
                )
                flash("Vehicle updated.", "success")
            else:
                db.execute(
                    """
                    INSERT INTO vehicles (user_id, label, make, model, year, license_plate,
                    is_default, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        uid,
                        label,
                        make,
                        model,
                        year_val,
                        plate,
                        1 if is_default else 0,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                flash("Vehicle added.", "success")
            db.commit()
            return redirect(url_for("vehicles_list"))

        return render_template("vehicle_form.html", vehicle=vehicle, form=None)

    @app.route("/reports")
    @login_required
    def reports():
        uid = g.user["id"]
        selected_year = request.args.get("year", str(date.today().year))
        try:
            year = int(selected_year)
        except ValueError:
            year = date.today().year

        db = get_db()
        rate = IRS_MILEAGE_RATES.get(year, 0.70)
        by_vehicle = db.execute(
            """
            SELECT v.id, v.label, v.make, v.model, v.year AS vehicle_year,
                COALESCE(SUM(t.distance_miles), 0) AS business_miles
            FROM vehicles v
            LEFT JOIN trips t ON t.vehicle_id = v.id
                AND t.purpose = 'business'
                AND strftime('%Y', t.trip_date) = ?
            WHERE v.user_id = ?
            GROUP BY v.id
            ORDER BY business_miles DESC
            """,
            (str(year), uid),
        ).fetchall()

        by_month = db.execute(
            """
            SELECT strftime('%m', trip_date) AS month,
                COALESCE(SUM(distance_miles), 0) AS business_miles
            FROM trips
            WHERE user_id = ? AND purpose = 'business' AND strftime('%Y', trip_date) = ?
            GROUP BY month
            ORDER BY month
            """,
            (uid, str(year)),
        ).fetchall()

        total_business = sum(row["business_miles"] for row in by_vehicle)
        estimated_deduction = total_business * rate
        years_with_trips = db.execute(
            """
            SELECT DISTINCT strftime('%Y', trip_date) AS y
            FROM trips WHERE user_id = ? ORDER BY y DESC
            """,
            (uid,),
        ).fetchall()
        available_years = [int(r["y"]) for r in years_with_trips] or [date.today().year]

        month_names = {
            "01": "January", "02": "February", "03": "March", "04": "April",
            "05": "May", "06": "June", "07": "July", "08": "August",
            "09": "September", "10": "October", "11": "November", "12": "December",
        }

        return render_template(
            "reports.html",
            year=year,
            rate=rate,
            by_vehicle=by_vehicle,
            by_month=by_month,
            month_names=month_names,
            total_business=total_business,
            estimated_deduction=estimated_deduction,
            available_years=available_years,
            taxpayer_name=g.user["display_name"],
            print_mode=request.args.get("print") == "1",
        )

    @app.route("/account")
    @login_required
    def account():
        return render_template("account.html")


app = create_app()


@app.cli.command("init-db")
def init_db_command():
    init_db()
    print("Database initialized.")


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5050, debug=False)
