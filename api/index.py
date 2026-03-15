import os
import re
import csv
import uuid
import smtplib
import qrcode
from io import BytesIO, StringIO
from datetime import datetime
from email.message import EmailMessage

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session
)
from dotenv import load_dotenv
from supabase import create_client, Client
from cryptography.fernet import Fernet, InvalidToken

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24))

SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@sasepass.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")
SUPABASE_BUCKET_URL = os.getenv("SUPABASE_BUCKET_URL", f"{SUPABASE_URL}/storage/v1/object/public/images")
DELETE_EVENT_PASSWORD = os.getenv("DELETE_EVENT_PASSWORD", "")
FERNET_KEY = os.getenv("FERNET_KEY", "")
cipher = Fernet(FERNET_KEY.encode()) if FERNET_KEY else None


def encrypt_email(email: str) -> str:
    """Encrypt an email address using Fernet symmetric encryption."""
    if not cipher:
        raise ValueError("FERNET_KEY not configured")
    return cipher.encrypt(email.encode()).decode()


def decrypt_email(token: str) -> str:
    """Decrypt a Fernet token back to the original email address."""
    if not cipher:
        raise ValueError("FERNET_KEY not configured")
    return cipher.decrypt(token.encode()).decode()


def is_valid_email(email: str) -> bool:
    """Basic email validation."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

@app.context_processor
def inject_image_url():
    def get_public_image_url(filename):
        if not SUPABASE_BUCKET_URL:
            return ""
        return f"{SUPABASE_BUCKET_URL}/{filename}"
    return dict(get_public_image_url=get_public_image_url)


# ---------------------------------------------------------------------------
# Auth Middleware
# ---------------------------------------------------------------------------
@app.before_request
def require_login():
    allowed = ("login", "static")
    if "user" not in session and request.endpoint not in allowed:
        return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Auth Routes
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email_input = request.form.get("email", "").strip().lower()
        pass_input = request.form.get("password", "").strip()

        # Check against the organizers table (if it exists)
        user_row = None
        try:
            result = (
                supabase.table("organizers")
                .select("*")
                .eq("email", email_input)
                .execute()
            )
            user_row = result.data[0] if result.data else None
        except Exception:
            pass  # Table may not exist, continue to admin check

        if user_row and user_row["password"] == pass_input:
            session["user"] = email_input
            session["user_name"] = user_row["name"]
            session["is_admin"] = user_row.get("is_admin", False)
            return redirect(url_for("home"))
        elif email_input == ADMIN_EMAIL.lower() and pass_input == ADMIN_PASSWORD:
            session["user"] = email_input
            session["user_name"] = "Admin"
            session["is_admin"] = True
            return redirect(url_for("home"))
        else:
            error = "Invalid email or password"

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard / Home
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    search_query = request.args.get("q", "").strip().lower()

    result = supabase.table("events").select("event_name").execute()
    events = [row["event_name"] for row in result.data]

    if search_query:
        events = [e for e in events if search_query in e.lower()]

    return render_template("home.html", events=events, search_query=search_query)


# ---------------------------------------------------------------------------
# Event Detail
# ---------------------------------------------------------------------------
@app.route("/event/<path:event_name>")
def event_detail(event_name):
    # Total accepted hackers
    hackers_res = (
        supabase.table("hackers")
        .select("id", count="exact")
        .eq("status", "Accepted")
        .execute()
    )
    total_accepted = hackers_res.count or 0

    # Total checked-in (unique hackers at the Check-in event)
    checkin_res = (
        supabase.table("attendance")
        .select("hacker_id")
        .eq("event", "Check-in")
        .execute()
    )
    checked_in_ids = list({row["hacker_id"] for row in checkin_res.data})
    total_here = len(checked_in_ids)

    # Hacker attendance for this specific event (UUID-based QR)
    event_att_res = (
        supabase.table("attendance")
        .select("*")
        .eq("event", event_name)
        .order("created_at", desc=True)
        .execute()
    )
    checked_in_list = event_att_res.data
    event_count = len({row["hacker_id"] for row in checked_in_list})

    # Workshop attendance for this event (Fernet-encrypted QR)
    workshop_att_res = (
        supabase.table("workshop_attendees")
        .select("*")
        .eq("event", event_name)
        .order("created_at", desc=True)
        .execute()
    )
    workshop_attendees_list = workshop_att_res.data
    workshop_count = len({row["email"] for row in workshop_attendees_list})

    return render_template(
        "event_detail.html",
        event_name=event_name,
        checked_in=checked_in_list,
        workshop_attendees=workshop_attendees_list,
        here=total_here,
        total=total_accepted,
        event_count=event_count,
        workshop_count=workshop_count,
    )


# ---------------------------------------------------------------------------
# API: Live Stats (polled from event detail page)
# ---------------------------------------------------------------------------
@app.route("/api/stats/<path:event_name>")
def get_stats(event_name):
    hackers_res = (
        supabase.table("hackers")
        .select("id", count="exact")
        .eq("status", "Accepted")
        .execute()
    )
    total_accepted = hackers_res.count or 0

    checkin_res = (
        supabase.table("attendance")
        .select("hacker_id")
        .eq("event", "Check-in")
        .execute()
    )
    total_here = len({row["hacker_id"] for row in checkin_res.data})

    # Hacker attendance (UUID-based QR)
    event_att_res = (
        supabase.table("attendance")
        .select("*")
        .eq("event", event_name)
        .order("created_at", desc=True)
        .limit(200)
        .execute()
    )
    event_count = len({row["hacker_id"] for row in event_att_res.data})

    # Workshop attendance (Fernet-encrypted QR)
    workshop_att_res = (
        supabase.table("workshop_attendees")
        .select("*")
        .eq("event", event_name)
        .order("created_at", desc=True)
        .limit(200)
        .execute()
    )
    workshop_count = len({row["email"] for row in workshop_att_res.data})

    return jsonify({
        "here": total_here,
        "total": total_accepted,
        "event_count": event_count,
        "workshop_count": workshop_count,
        "recent_activity": event_att_res.data,
        "workshop_activity": workshop_att_res.data,
    })


# ---------------------------------------------------------------------------
# API: Eligible Users for a given event
# ---------------------------------------------------------------------------
@app.route("/get_eligible_users/<path:event_name>")
def get_eligible_users(event_name):
    accepted_res = (
        supabase.table("hackers")
        .select("id, full_name")
        .eq("status", "Accepted")
        .execute()
    )
    accepted = accepted_res.data

    # For non-check-in events, only show hackers already checked in
    if event_name != "Check-in":
        checkin_res = (
            supabase.table("attendance")
            .select("hacker_id")
            .eq("event", "Check-in")
            .execute()
        )
        checked_in_ids = {row["hacker_id"] for row in checkin_res.data}
        accepted = [h for h in accepted if h["id"] in checked_in_ids]

    def format_last_first(name):
        parts = name.split()
        if len(parts) <= 1:
            return name
        return f"{' '.join(parts[1:])}, {parts[0]}"

    for h in accepted:
        h["display_name"] = format_last_first(h["full_name"])

    accepted.sort(key=lambda h: h["display_name"].lower())

    return jsonify([
        {"guest_id": h["id"], "display_name": h["display_name"]}
        for h in accepted
    ])


# ---------------------------------------------------------------------------
# API: Log Attendance (QR scan or manual selection)
# ---------------------------------------------------------------------------
@app.route("/log_attendance", methods=["POST"])
def log_attendance():
    data = request.json
    guest_id = data.get("guest_id")
    event = data.get("event")

    # Validate hacker exists
    hacker_res = (
        supabase.table("hackers")
        .select("*")
        .eq("id", guest_id)
        .execute()
    )
    if not hacker_res.data:
        return jsonify({"status": "error", "message": "Invalid QR Code"}), 404

    hacker = hacker_res.data[0]
    name = hacker["full_name"]

    # Gatekeeper: non-check-in events require check-in first
    if event != "Check-in":
        checkin_check = (
            supabase.table("attendance")
            .select("id")
            .eq("hacker_id", guest_id)
            .eq("event", "Check-in")
            .execute()
        )
        if not checkin_check.data:
            return jsonify({
                "status": "error",
                "message": f"ACCESS DENIED: {name} must go to the main Check-in desk first.",
            }), 403

    # Prevent duplicate
    dup_check = (
        supabase.table("attendance")
        .select("id")
        .eq("hacker_id", guest_id)
        .eq("event", event)
        .execute()
    )
    if dup_check.data:
        return jsonify({
            "status": "warning",
            "message": f"{name} is already logged for {event}.",
        })

    # Record attendance
    supabase.table("attendance").insert({
        "hacker_id": guest_id,
        "name": name,
        "event": event,
    }).execute()

    # If this is a main check-in, also update the hacker row
    if event == "Check-in":
        supabase.table("hackers").update({
            "checked_in": True
        }).eq("id", guest_id).execute()

    return jsonify({"status": "success", "message": f"Verified: {name}"})


# ---------------------------------------------------------------------------
# API: Remove Attendance (Admin only)
# ---------------------------------------------------------------------------
@app.route("/remove_attendance", methods=["POST"])
def remove_attendance():
    if not session.get("is_admin"):
        return jsonify({"status": "error", "message": "Unauthorized: Admin access required"}), 403

    data = request.json
    guest_id = data.get("guest_id")
    event = data.get("event")

    supabase.table("attendance").delete().eq(
        "hacker_id", guest_id
    ).eq("event", event).execute()

    return jsonify({"status": "success", "message": "Record removed by Admin"})


# ---------------------------------------------------------------------------
# API: Remove Workshop Attendance (Admin only)
# ---------------------------------------------------------------------------
@app.route("/remove_workshop_attendance", methods=["POST"])
def remove_workshop_attendance():
    if not session.get("is_admin"):
        return jsonify({"status": "error", "message": "Unauthorized: Admin access required"}), 403

    data = request.json
    email = data.get("email")
    event = data.get("event")

    supabase.table("workshop_attendees").delete().eq(
        "email", email
    ).eq("event", event).execute()

    return jsonify({"status": "success", "message": "Workshop attendance removed by Admin"})


# ---------------------------------------------------------------------------
# API: Add Hacker + Email QR Code (Admin only)
# ---------------------------------------------------------------------------
@app.route("/add_hacker", methods=["POST"])
def add_hacker():
    if not session.get("is_admin"):
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    data = request.json
    name = data.get("name", "").strip()
    email = data.get("email", "").strip()

    if not name or not email:
        return jsonify({"status": "error", "message": "Missing name or email"}), 400

    guest_id = str(uuid.uuid4())

    try:
        supabase.table("hackers").insert({
            "id": guest_id,
            "full_name": name,
            "email": email,
            "status": "Accepted",
        }).execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Database error: {e}"}), 500

    # Generate QR code in memory
    qr = qrcode.make(guest_id)
    img_io = BytesIO()
    qr.save(img_io, "PNG")
    img_io.seek(0)

    # Email QR code
    if EMAIL_ADDRESS and EMAIL_APP_PASSWORD:
        try:
            msg = EmailMessage()
            msg["Subject"] = "SASEHacks - Your Check-In QR Code"
            msg["From"] = f"Vincent Lin <{EMAIL_ADDRESS}>"
            msg["To"] = email
            msg.set_content(
                "Hello!\n\n"
                "Attached is your QR code for SASEHacks.\n\n"
                "How to use your QR code:\n"
                "- Show it at check-in to register for the hackathon\n"
                "- Keep it handy - it will also be scanned at workshops and events throughout the weekend\n\n"
                "Before the event, please:\n"
                "- Read the Hacker Guide: https://www.notion.so/SASEHacks-2026-Hacker-Guide-3199fa3e113580e993caf4e3832d7aa8\n"
                "- Join Devpost (required for submission): https://sasehacks.devpost.com/\n\n"
                "See you soon!\n"
                "Vincent Lin\n"
                "SASEHacks Website Lead"
            )
            msg.add_attachment(
                img_io.read(),
                maintype="image",
                subtype="png",
                filename=f"SASEPass_{name}.png",
            )
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
                smtp.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
                smtp.send_message(msg)

            return jsonify({"status": "success", "message": f"{name} added and email sent!"})
        except Exception as e:
            return jsonify({
                "status": "warning",
                "message": f"{name} added to database, but email failed: {str(e)}",
            })
    else:
        return jsonify({
            "status": "success",
            "message": f"{name} added (email not configured).",
        })


# ---------------------------------------------------------------------------
# API: Create Event (Admin only)
# ---------------------------------------------------------------------------
@app.route("/api/create_event", methods=["POST"])
def create_event():
    if not session.get("is_admin"):
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    data = request.json
    event_name = data.get("event_name", "").strip()

    if not event_name:
        return jsonify({"status": "error", "message": "Event name is required"}), 400

    try:
        # Check if event exists
        existing = supabase.table("events").select("event_name").eq("event_name", event_name).execute()
        if existing.data:
            return jsonify({"status": "error", "message": "Event already exists"}), 400

        # Insert new event
        supabase.table("events").insert({"event_name": event_name}).execute()
        return jsonify({"status": "success", "message": f"Event '{event_name}' created successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Database error: {e}"}), 500


# ---------------------------------------------------------------------------
# API: Delete Event (Admin only, requires password)
# ---------------------------------------------------------------------------
@app.route("/api/delete_event", methods=["POST"])
def delete_event():
    if not session.get("is_admin"):
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    data = request.json
    event_name = data.get("event_name", "").strip()
    password = data.get("password", "")

    if not event_name or not password:
        return jsonify({"status": "error", "message": "Event name and password are required"}), 400

    if password != DELETE_EVENT_PASSWORD:
        return jsonify({"status": "error", "message": "Invalid delete password"}), 401
    
    # Do not allow deleting the Check-in event
    if event_name == "Check-in":
        return jsonify({"status": "error", "message": "Cannot delete the main Check-in event"}), 403

    try:
        # Delete attendance for this event first
        supabase.table("attendance").delete().eq("event", event_name).execute()
        # Delete the event itself
        supabase.table("events").delete().eq("event_name", event_name).execute()
        return jsonify({"status": "success", "message": f"Event '{event_name}' deleted successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Database error: {e}"}), 500


# ---------------------------------------------------------------------------
# API: Search Hackers (debounced on the frontend)
# ---------------------------------------------------------------------------
@app.route("/api/hackers")
def search_hackers():
    q = request.args.get("q", "").strip()
    if not q:
        result = (
            supabase.table("hackers")
            .select("*")
            .eq("status", "Accepted")
            .order("full_name")
            .limit(50)
            .execute()
        )
    else:
        result = (
            supabase.table("hackers")
            .select("*")
            .eq("status", "Accepted")
            .ilike("full_name", f"%{q}%")
            .order("full_name")
            .limit(50)
            .execute()
        )
    return jsonify(result.data)


# ---------------------------------------------------------------------------
# Bulk Import Page (Admin only)
# ---------------------------------------------------------------------------
@app.route("/bulk_import")
def bulk_import():
    if not session.get("is_admin"):
        return redirect(url_for("home"))

    # Get events for dropdown
    result = supabase.table("events").select("event_name").execute()
    events = [row["event_name"] for row in result.data]

    return render_template("bulk_import.html", events=events)


# ---------------------------------------------------------------------------
# API: Validate CSV for Bulk Import (Admin only)
# ---------------------------------------------------------------------------
@app.route("/api/bulk_import/validate", methods=["POST"])
def bulk_import_validate():
    if not session.get("is_admin"):
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename or not file.filename.endswith(".csv"):
        return jsonify({"status": "error", "message": "Please upload a CSV file"}), 400

    try:
        content = file.read().decode("utf-8")
        reader = csv.reader(StringIO(content))

        valid_emails = []
        invalid_entries = []
        seen = set()

        for row_num, row in enumerate(reader, start=1):
            if not row:
                continue

            email = row[0].strip().lower()

            # Skip header row if it looks like a header
            if row_num == 1 and email in ("email", "emails", "e-mail"):
                continue

            if not email:
                continue

            if email in seen:
                invalid_entries.append({"row": row_num, "value": email, "reason": "Duplicate"})
                continue

            seen.add(email)

            if is_valid_email(email):
                valid_emails.append(email)
            else:
                invalid_entries.append({"row": row_num, "value": email, "reason": "Invalid email format"})

        return jsonify({
            "status": "success",
            "valid": valid_emails,
            "invalid": invalid_entries,
            "total_valid": len(valid_emails),
            "total_invalid": len(invalid_entries),
        })

    except Exception as e:
        return jsonify({"status": "error", "message": f"Error parsing CSV: {str(e)}"}), 400


# ---------------------------------------------------------------------------
# API: Send Batch of Encrypted QR Codes (Admin only)
# ---------------------------------------------------------------------------
@app.route("/api/bulk_import/send_batch", methods=["POST"])
def bulk_import_send_batch():
    if not session.get("is_admin"):
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    if not cipher:
        return jsonify({"status": "error", "message": "FERNET_KEY not configured"}), 500

    if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD:
        return jsonify({"status": "error", "message": "Email credentials not configured"}), 500

    data = request.json
    emails = data.get("emails", [])
    event_name = data.get("event_name", "Workshop")

    if not emails:
        return jsonify({"status": "error", "message": "No emails provided"}), 400

    if len(emails) > 15:
        return jsonify({"status": "error", "message": "Batch size exceeds limit of 15"}), 400

    results = {"success": [], "failed": []}

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)

            for email in emails:
                try:
                    # Encrypt email into QR data
                    encrypted_data = encrypt_email(email)

                    # Generate QR code
                    qr = qrcode.make(encrypted_data)
                    img_io = BytesIO()
                    qr.save(img_io, "PNG")
                    img_io.seek(0)

                    # Send email
                    msg = EmailMessage()
                    msg["Subject"] = "SASEHacks - Your Check-In QR Code"
                    msg["From"] = f"Vincent Lin <{EMAIL_ADDRESS}>"
                    msg["To"] = email
                    msg.set_content(
                        "Hello!\n\n"
                        "Attached is your QR code for SASEHacks.\n\n"
                        "How to use your QR code:\n"
                        "- Show it at check-in to register for the hackathon\n"
                        "- Keep it handy - it will also be scanned at workshops and events throughout the weekend\n\n"
                        "Before the event, please:\n"
                        "- Read the Hacker Guide: https://www.notion.so/SASEHacks-2026-Hacker-Guide-3199fa3e113580e993caf4e3832d7aa8\n"
                        "- Join Devpost (required for submission): https://sasehacks.devpost.com/\n\n"
                        "See you soon!\n"
                        "Vincent Lin\n"
                        "SASEHacks Website Lead"
                    )
                    msg.add_attachment(
                        img_io.read(),
                        maintype="image",
                        subtype="png",
                        filename=f"SASEPass_QR.png",
                    )
                    smtp.send_message(msg)
                    results["success"].append(email)

                except Exception as e:
                    results["failed"].append({"email": email, "error": str(e)})

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"SMTP connection error: {str(e)}",
            "results": results,
        }), 500

    return jsonify({
        "status": "success",
        "results": results,
        "success_count": len(results["success"]),
        "failed_count": len(results["failed"]),
    })


# ---------------------------------------------------------------------------
# API: Log Manual Attendance (Email entry)
# ---------------------------------------------------------------------------
@app.route("/log_manual_attendance", methods=["POST"])
def log_manual_attendance():
    data = request.json
    email = data.get("email", "").strip().lower()
    event = data.get("event", "")

    if not email or not event:
        return jsonify({"status": "error", "message": "Missing email or event"}), 400

    if not is_valid_email(email):
        return jsonify({"status": "error", "message": "Invalid email format"}), 400

    # Check for duplicate attendance
    dup_check = (
        supabase.table("workshop_attendees")
        .select("id")
        .eq("email", email)
        .eq("event", event)
        .execute()
    )

    if dup_check.data:
        return jsonify({
            "status": "warning",
            "message": f"{email} is already registered for {event}.",
        })

    # Insert attendance record
    try:
        supabase.table("workshop_attendees").insert({
            "email": email,
            "event": event,
        }).execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Database error: {str(e)}"}), 500

    return jsonify({"status": "success", "message": f"Registered: {email}"})


# ---------------------------------------------------------------------------
# API: Log Workshop Attendance (Encrypted QR)
# ---------------------------------------------------------------------------
@app.route("/log_workshop_attendance", methods=["POST"])
def log_workshop_attendance():
    data = request.json
    qr_data = data.get("qr_data", "")
    event = data.get("event", "")

    if not qr_data or not event:
        return jsonify({"status": "error", "message": "Missing QR data or event"}), 400

    if not cipher:
        return jsonify({"status": "error", "message": "Encryption not configured"}), 500

    try:
        email = decrypt_email(qr_data)
    except InvalidToken:
        return jsonify({"status": "error", "message": "Invalid QR Code"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Decryption error: {str(e)}"}), 400

    # Check for duplicate attendance
    dup_check = (
        supabase.table("workshop_attendees")
        .select("id")
        .eq("email", email)
        .eq("event", event)
        .execute()
    )

    if dup_check.data:
        return jsonify({
            "status": "warning",
            "message": f"{email} is already registered for {event}.",
        })

    # Insert attendance record
    try:
        supabase.table("workshop_attendees").insert({
            "email": email,
            "event": event,
        }).execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Database error: {str(e)}"}), 500

    return jsonify({"status": "success", "message": f"Registered: {email}"})


# ---------------------------------------------------------------------------
# Vercel entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
