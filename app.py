from flask import Flask, render_template, request, redirect, session, send_file, jsonify
from datetime import datetime, timedelta
from flask_mail import Mail, Message
import mysql.connector
import qrcode
import os
import pandas as pd

app = Flask(__name__)
app.secret_key = "secret123"


# ---------------- DATABASE FUNCTION ----------------
def get_db():
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password="root",
        database="attendance"
    )
    return conn, conn.cursor(buffered=True)

# ---------------- HELPERS ----------------
def is_logged_in():
    return 'user' in session

def is_admin():
    return session.get('role') == 'admin'

# ---------------- HOME ----------------
@app.route('/')
def home():
    return redirect('/login')

# ---------------- LOGIN ----------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        conn, cursor = get_db()

        username = request.form['username']
        password = request.form['password']

        cursor.execute(
            "SELECT id, username, role FROM users WHERE username=%s AND password=%s",
            (username, password)
        )

        user = cursor.fetchone()

        conn.close()

        if user:
            session['user'] = user[1]
            session['role'] = user[2]
            return redirect('/dashboard')
        else:
            return "Invalid Credentials"

    return render_template('login.html')

# ---------------- DASHBOARD ----------------
@app.route('/dashboard')
def dashboard():

    if not is_logged_in():
        return redirect('/login')

    conn, cursor = get_db()

    # Total students
    cursor.execute("SELECT COUNT(*) FROM students")
    total_students = cursor.fetchone()[0]

    # Present today
    cursor.execute("""
        SELECT COUNT(DISTINCT roll_no)
        FROM attendance_new
        WHERE date = CURDATE()
    """)

    present_today = cursor.fetchone()[0]

    # Graph
    cursor.execute("""
        SELECT DATE(date), COUNT(DISTINCT roll_no)
        FROM attendance_new
        GROUP BY DATE(date)
        ORDER BY DATE(date)
    """)

    data = cursor.fetchall()

    dates = [row[0].strftime("%d-%b") for row in data]
    counts = [row[1] for row in data]

    conn.close()

    return render_template(
        "dashboard.html",
        total_students=total_students,
        present_today=present_today,
        dates=dates,
        counts=counts
    )

# ---------------- SCAN ----------------
@app.route('/scan', methods=['POST'])
def scan():

    conn, cursor = get_db()

    data = request.get_json()

    # ---------------- QR EXPIRY ----------------
    qr_data = data.get('data').strip()

    try:
        roll_no, expiry_time = qr_data.split("|")

        expiry_time = datetime.strptime(
            expiry_time,
            "%Y-%m-%d %H:%M:%S"
        )

        if datetime.now() > expiry_time:

            conn.close()

            return jsonify({
                "status": "expired",
                "message": "QR Code Expired"
            })

    except:
        roll_no = qr_data

    # Get student name
    cursor.execute(
    "SELECT name FROM students WHERE roll_no=%s",
    (roll_no,)
    )

    student = cursor.fetchone()

    if not student:
        conn.close()
        return jsonify({
            "status": "error",
            "message": "Student not found"
        })

    name = student[0]
    # ---------------- TIME LIMIT ----------------
    cursor.execute("""
        SELECT start_time, end_time
        FROM attendance_settings
        WHERE id=1
    """)

    timing = cursor.fetchone()
    now = datetime.now()

    start_time = (datetime.min + timing[0]).time()
    end_time = (datetime.min + timing[1]).time()
    if not (start_time <= now.time() <= end_time):

        conn.close()

        return jsonify({
            "status": "closed",
            "message": "Attendance Closed"
        })

    today = now.date()
    current_time = now.strftime("%H:%M:%S")

    # Duplicate check
    cursor.execute("""
        SELECT * FROM attendance_new
        WHERE roll_no=%s AND date=%s
    """, (roll_no, today))

    exists = cursor.fetchone()

    if exists:

        conn.close()

        return jsonify({
            "status": "success",
            "name": name
        })

    # Insert attendance
    cursor.execute(
        """
        INSERT INTO attendance_new
        (roll_no, date, time)
        VALUES (%s, %s, %s)
        """,
        (roll_no, today, current_time)
    )

    conn.commit()
    conn.close()

    return jsonify({
        "status": "success",
        "name": name
    })

# ---------------- REPORT ----------------
@app.route('/report')
def report():

    if not is_logged_in():
        return redirect('/login')

    conn, cursor = get_db()

    cursor.execute("""
        SELECT s.name, s.roll_no, a.date, a.time
        FROM attendance_new a
        JOIN students s ON a.roll_no = s.roll_no
        ORDER BY a.date DESC
    """)

    data = cursor.fetchall()

    conn.close()

    return render_template('report.html', data=data)

# ---------------- ANALYTICS ----------------
@app.route('/analytics')
def analytics():

    if not is_logged_in():
        return redirect('/login')

    conn, cursor = get_db()

    cursor.execute("SELECT COUNT(*) FROM students")
    total_students = cursor.fetchone()[0]

    cursor.execute("""
        SELECT roll_no, COUNT(*)
        FROM attendance_new
        GROUP BY roll_no
    """)

    data = cursor.fetchall()

    conn.close()

    return render_template(
        'analytics.html',
        data=data,
        total_students=total_students
    )

# ---------------- EXPORT ----------------
@app.route('/export')
def export():

    if not is_logged_in():
        return redirect('/login')

    conn, cursor = get_db()

    query = """
    SELECT s.name, s.roll_no, a.date, a.time
    FROM attendance_new a
    JOIN students s ON a.roll_no = s.roll_no
    """

    df = pd.read_sql(query, conn)

    file_path = "attendance_report.csv"

    df.to_csv(file_path, index=False)

    conn.close()

    return send_file(file_path, as_attachment=True)

# ---------------- SCAN PAGE ----------------
@app.route('/scan_page')
def scan_page():

    if not is_logged_in():
        return redirect('/login')

    return render_template("scan.html")

# ---------------- GENERATE ----------------
@app.route('/generate')
def generate():

    if not is_logged_in():
        return redirect('/login')

    conn, cursor = get_db()

    cursor.execute("SELECT * FROM students")

    students = cursor.fetchall()

    conn.close()

    return render_template(
        'generate_qr.html',
        students=students
    )

# ---------------- RANKING ----------------
@app.route('/ranking')
def ranking():

    if not is_logged_in():
        return redirect('/login')

    conn, cursor = get_db()

    cursor.execute("""
        SELECT s.name, s.roll_no, COUNT(a.id)
        FROM students s
        LEFT JOIN attendance_new a
        ON s.roll_no = a.roll_no
        GROUP BY s.roll_no, s.name
    """)

    data = cursor.fetchall()

    conn.close()

    students = []

    for name, roll, present in data:

        present = present if present else 0

        percent = round((present / 30) * 100, 2)

        students.append({
            "name": name,
            "roll": roll,
            "percent": percent
        })

    students.sort(
        key=lambda x: x["percent"],
        reverse=True
    )

    return render_template(
        "ranking.html",
        students=students
    )

# ---------------- ADD STUDENT ----------------
@app.route('/add_student', methods=['GET', 'POST'])
def add_student():

    if not is_logged_in():
        return redirect('/login')

    if request.method == 'POST':

        name = request.form['name']
        roll_no = request.form['roll_no']

        conn, cursor = get_db()

        cursor.execute(
            """
            INSERT INTO students
            (name, roll_no)
            VALUES (%s, %s)
            """,
            (name, roll_no)
        )

        conn.commit()

        conn.close()

        return redirect('/dashboard')

    return render_template('add_student.html')

# ---------------- QR GENERATE ----------------
@app.route('/generate_qr/<roll_no>')
def generate_qr(roll_no):

    file_path = f"static/qr_codes/qr_{roll_no}.png"

    expiry_time = (
        datetime.now() + timedelta(seconds=30)
    ).strftime("%Y-%m-%d %H:%M:%S")

    qr_data = f"{roll_no}|{expiry_time}"

    qr = qrcode.make(qr_data)

    qr.save(file_path)

    return send_file(
        file_path,
        mimetype='image/png'
    )

# ---------------- LOGOUT ----------------
@app.route('/logout')
def logout():

    session.clear()

    return redirect('/login')

# ---------------- LEADERBOARD ----------------
@app.route('/leaderboard')
def leaderboard():

    if not is_logged_in():
        return redirect('/login')

    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password="root",
        database="attendance"
    )

    cursor = conn.cursor(dictionary=True)

    query = """
    SELECT s.name, s.roll_no,
    COUNT(a.roll_no) AS present_days,

    (SELECT COUNT(DISTINCT date)
    FROM attendance_new) AS total_days,

    ROUND(
        (COUNT(a.roll_no) /
        (SELECT COUNT(DISTINCT date)
        FROM attendance_new)) * 100, 2
    ) AS percentage

    FROM students s

    LEFT JOIN attendance_new a
    ON s.roll_no = a.roll_no

    GROUP BY s.roll_no, s.name

    ORDER BY percentage DESC
    """

    cursor.execute(query)

    data = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('leaderboard.html', data=data)

# ---------------- ATTENDANCE CONTROL ----------------
@app.route('/attendance_control', methods=['GET', 'POST'])
def attendance_control():

    if not is_logged_in():
        return redirect('/login')

    conn, cursor = get_db()

    if request.method == 'POST':

        start_time = request.form['start_time']
        end_time = request.form['end_time']

        cursor.execute("""
            UPDATE attendance_settings
            SET start_time=%s,
                end_time=%s
            WHERE id=1
        """, (start_time, end_time))

        conn.commit()

    cursor.execute("""
        SELECT start_time, end_time
        FROM attendance_settings
        WHERE id=1
    """)

    timing = cursor.fetchone()

    conn.close()

    return render_template(
        'attendance_control.html',
        timing=timing
    )
# ---------------- RUN ----------------
if __name__ == '__main__':

    app.run(debug=True)