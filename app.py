from flask import (Flask, render_template, request, redirect,
                   url_for, flash, session, jsonify, Response)
import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool
from datetime import datetime, timedelta, date
import os, secrets, csv, io, re
import atexit
from dotenv import load_dotenv
from flask_mail import Mail, Message
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    SCHEDULER_OK = True
except ImportError:
    SCHEDULER_OK = False

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Table,
                                    TableStyle, Paragraph, Spacer)
    from reportlab.lib.styles import getSampleStyleSheet
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False

# ── Constants ──────────────────────────────────────────────────
BORROW_DAYS   = 14
MAX_RENEWALS  = 1
FINE_RATE     = 2
REMEMBER_DAYS = 30

# ── App ────────────────────────────────────────────────────────
app = Flask(__name__)

# FIX: strong random secret key from env — never hardcoded
_raw_key = os.environ.get('SECRET_KEY', '')
if not _raw_key or len(_raw_key) < 32:
    raise RuntimeError(
        "SECRET_KEY env var is missing or too short. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
app.secret_key = _raw_key
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)
app.config['WTF_CSRF_TIME_LIMIT']        = None

# ── Mail ───────────────────────────────────────────────────────
app.config.update(
    MAIL_SERVER            = 'smtp.gmail.com',
    MAIL_PORT              = 587,
    MAIL_USE_TLS           = True,
    MAIL_USE_SSL           = False,
    MAIL_USERNAME          = os.environ.get('MAIL_USERNAME'),
    MAIL_PASSWORD          = os.environ.get('MAIL_PASSWORD'),
    MAIL_DEFAULT_SENDER    = ('LendWise Library',
                              os.environ.get('MAIL_USERNAME')),
    MAIL_MAX_EMAILS        = None,
    MAIL_ASCII_ATTACHMENTS = False,
)

mail    = Mail(app)
limiter = Limiter(get_remote_address, app=app,
                  default_limits=["200 per day"])
csrf    = CSRFProtect(app)

# ── DB pool ────────────────────────────────────────────────────
pool = MySQLConnectionPool(
    pool_name="lendwise_pool",
    pool_size=5,
    host=os.environ.get('DB_HOST', 'localhost'),
    port=int(os.environ.get('DB_PORT', 3306)),
    user=os.environ.get('DB_USER', 'root'),
    password=os.environ.get('DB_PASSWORD', ''),
    database=os.environ.get('DB_NAME', 'library_db'),
)

def get_db():
    return pool.get_connection()

def query(sql, params=(), fetchone=False, commit=False):
    """
    Run one SQL statement.
    Returns rowcount when commit=True so callers can detect zero-row updates.
    """
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute(sql, params)
        result = None
        if commit:
            conn.commit()
            result = cur.rowcount      # FIX: return rowcount for atomic checks
        elif fetchone:
            result = cur.fetchone()
        else:
            result = cur.fetchall()
        cur.close()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# FIX: renamed to read_many and guarded against write statements
def read_many(queries):
    """
    Run multiple SELECT statements in one connection.
    Raises ValueError if any query is not a SELECT.
    """
    for q in queries:
        sql_upper = q['sql'].lstrip().upper()
        if not sql_upper.startswith('SELECT'):
            raise ValueError(
                f"read_many() only accepts SELECT statements. Got: {q['sql'][:40]}"
            )
    conn = get_db()
    try:
        cur     = conn.cursor(dictionary=True, buffered=True)
        results = []
        for q in queries:
            cur.execute(q['sql'], q.get('params', ()))
            results.append(
                cur.fetchone() if q.get('fetchone') else cur.fetchall()
            )
        cur.close()
        return results
    finally:
        conn.close()

# Keep old name as alias so nothing else breaks
query_many = read_many

# ── Helpers ────────────────────────────────────────────────────
_DUMMY_HASH = generate_password_hash('__dummy_lendwise__')

def log_activity(username, action, detail):
    ip = request.remote_addr or '0.0.0.0'
    query(
        "INSERT INTO activity_log "
        "(username, action, detail, ip_address) VALUES (%s,%s,%s,%s)",
        (username, action, detail, ip), commit=True,
    )

def send_email(to, subject, body_html):
    try:
        mail.send(Message(subject, recipients=[to], html=body_html))
        return True, None
    except Exception as e:
        print(f"[Mail error] {e}")
        return False, str(e)

def validate_isbn(isbn):
    if not isbn:
        return True
    cleaned = isbn.replace('-', '').replace(' ', '')
    return bool(re.match(r'^\d{9}[\dXx]$|^\d{13}$', cleaned))

def validate_year(year):
    if not year:
        return True
    try:
        y = int(year)
        return 1000 <= y <= datetime.now().year + 1
    except ValueError:
        return False

def validate_username(username):
    return bool(re.match(r'^[a-zA-Z0-9_.-]{3,30}$', username))

def validate_email(email):
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email))

def serialise_row(row):
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(v, date):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out

def calc_fine(due_date_val, return_date_val=None):
    """
    FIX: accepts an optional return_date so history page shows the fine
    that was owed at the time of return, not today's ever-growing amount.
    Falls back to date.today() for currently active loans.
    """
    if not due_date_val:
        return 0
    if isinstance(due_date_val, str):
        try:
            due_date_val = datetime.strptime(due_date_val, '%Y-%m-%d').date()
        except ValueError:
            return 0
    if isinstance(due_date_val, datetime):
        due_date_val = due_date_val.date()

    if return_date_val is not None:
        if isinstance(return_date_val, datetime):
            return_date_val = return_date_val.date()
        elif isinstance(return_date_val, str):
            try:
                return_date_val = datetime.strptime(
                    return_date_val, '%Y-%m-%d').date()
            except ValueError:
                return_date_val = None

    effective_date = return_date_val if return_date_val else date.today()
    return max(0, (effective_date - due_date_val).days * FINE_RATE)

def book_cover_url(book):
    if book.get('cover_url'):
        return book['cover_url']
    isbn = (book.get('isbn') or '').replace('-', '').replace(' ', '')
    if isbn:
        return f"https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg"
    return None

def is_ajax():
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('Accept', '')
        or 'application/json' in request.headers.get('Content-Type', '')
    )

def _categories():
    rows = query("SELECT DISTINCT category FROM books ORDER BY category")
    return [r['category'] for r in rows if r['category']]

# FIX: proper email masking — shows only first char + *** before @
def mask_email(email):
    if not email or '@' not in email:
        return email or ''
    local, domain = email.split('@', 1)
    masked_local = local[0] + '***' if len(local) > 1 else '***'
    return f"{masked_local}@{domain}"

# Register as a Jinja filter
app.jinja_env.filters['mask_email'] = mask_email

# ── Session timeout ────────────────────────────────────────────
@app.before_request
def check_session_timeout():
    if 'username' not in session:
        return
    if session.get('remember_me'):
        session['last_active'] = datetime.now().isoformat()
        return
    last = session.get('last_active')
    now  = datetime.now()
    if last:
        if (now - datetime.fromisoformat(last)).total_seconds() > 1800:
            session.clear()
            flash("Session expired. Please sign in again.", "warning")
            return redirect(url_for('login'))
    session['last_active'] = now.isoformat()
    session.permanent = True

# ── Overdue scheduler ──────────────────────────────────────────
def check_overdue():
    try:
        rows = query("""
            SELECT bb.id, bb.borrower_name, bb.due_date,
                   b.book_name, u.email
            FROM borrowed_books bb
            JOIN books b ON bb.book_id = b.id
            JOIN users u ON bb.borrower_name = u.username
            WHERE bb.due_date < CURDATE()
              AND b.status = 'Not Available'
              AND bb.overdue_notified = 0
        """)
        for r in rows:
            if r.get('email'):
                fine = calc_fine(r['due_date'])
                ok, _ = send_email(
                    r['email'],
                    f"Overdue: {r['book_name']}",
                    f"""<p>Hi <b>{r['borrower_name']}</b>,</p>
                        <p>Your book <b>{r['book_name']}</b> was due
                        on <b>{r['due_date']}</b>.</p>
                        <p>Accumulated fine: <b>₹{fine}</b>
                        (₹{FINE_RATE}/day).</p>
                        <p>Please return it as soon as possible.</p>"""
                )
                if ok:
                    query(
                        "UPDATE borrowed_books "
                        "SET overdue_notified=1 WHERE id=%s",
                        (r['id'],), commit=True,
                    )
    except Exception as e:
        print(f"[Scheduler error] {e}")

# ── Auth ───────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if 'username' in session:
        return redirect(url_for('home'))
    if request.method == 'POST':
        username    = request.form['username'].strip()
        password    = request.form['password']
        remember_me = bool(request.form.get('remember_me'))

        user          = query("SELECT * FROM users WHERE username=%s",
                              (username,), fetchone=True)
        hash_to_check = user['password'] if user else _DUMMY_HASH
        password_ok   = check_password_hash(hash_to_check, password)

        if user and password_ok:
            old = dict(session)
            session.clear()
            session.update(old)
            session.update({
                'username':    username,
                'role':        user['role'],
                'last_active': datetime.now().isoformat(),
                'remember_me': remember_me,
            })
            session.permanent = True
            app.config['PERMANENT_SESSION_LIFETIME'] = (
                timedelta(days=REMEMBER_DAYS)
                if remember_me else timedelta(minutes=30)
            )
            query("UPDATE users SET last_login=%s WHERE username=%s",
                  (datetime.now(), username), commit=True)
            log_activity(username, 'LOGIN',
                         f"Logged in from {request.remote_addr}")
            flash(f"Welcome back, {username}!", "success")
            return redirect(url_for('home'))
        else:
            log_activity(username, 'LOGIN_FAIL', "Failed login attempt")
            flash('Invalid username or password.', 'danger')
    return render_template('auth/login.html', remember_days=REMEMBER_DAYS)


@app.route('/signup', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def signup():
    if 'username' in session:
        return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        email    = request.form.get('email', '').strip()

        if not username or not password:
            flash("All fields are required!", "danger")
            return redirect(url_for('signup'))
        if not validate_username(username):
            flash("Username must be 3–30 characters: "
                  "letters, digits, _ . - only.", "danger")
            return redirect(url_for('signup'))
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return redirect(url_for('signup'))
        if email and not validate_email(email):
            flash("Please enter a valid email address.", "danger")
            return redirect(url_for('signup'))
        if query("SELECT id FROM users WHERE username=%s",
                 (username,), fetchone=True):
            flash("Username already taken.", "danger")
            return redirect(url_for('signup'))
        if email and query("SELECT id FROM users WHERE email=%s",
                           (email,), fetchone=True):
            flash("That email is already registered.", "danger")
            return redirect(url_for('signup'))

        verify_token  = secrets.token_urlsafe(32)
        verify_expiry = datetime.now() + timedelta(hours=24)
        query(
            """INSERT INTO users
               (username, password, role, email,
                is_verified, verify_token, verify_token_expiry)
               VALUES (%s,%s,'public',%s,0,%s,%s)""",
            (username, generate_password_hash(password),
             email or None,
             verify_token if email else None,
             verify_expiry if email else None),
            commit=True,
        )
        log_activity(username, 'SIGNUP', "New account created")

        if email:
            verify_url = url_for('verify_email',
                                 token=verify_token, _external=True)
            ok, _ = send_email(
                email, "Verify your LendWise email",
                f"""<p>Hi <b>{username}</b>, welcome to LendWise!</p>
                    <p><a href="{verify_url}">Verify your email</a>
                    — link expires in 24 hours.</p>"""
            )
            if ok:
                flash("Account created! Check your email to verify, "
                      "then login.", "success")
            else:
                query("UPDATE users SET is_verified=1 WHERE username=%s",
                      (username,), commit=True)
                flash("Account created! (Email skipped — "
                      "mail not configured.) Please login.", "warning")
        else:
            query("UPDATE users SET is_verified=1 WHERE username=%s",
                  (username,), commit=True)
            flash("Account created! Please login.", "success")
        return redirect(url_for('login'))
    return render_template('auth/signup.html')


@app.route('/verify-email/<token>')
def verify_email(token):
    user = query(
        "SELECT * FROM users "
        "WHERE verify_token=%s AND verify_token_expiry > NOW()",
        (token,), fetchone=True,
    )
    if not user:
        flash("Invalid or expired verification link.", "danger")
        return redirect(url_for('login'))
    query(
        "UPDATE users SET is_verified=1, "
        "verify_token=NULL, verify_token_expiry=NULL WHERE id=%s",
        (user['id'],), commit=True,
    )
    log_activity(user['username'], 'EMAIL_VERIFIED', "Email verified")
    flash("Email verified! You can now log in.", "success")
    return redirect(url_for('login'))


@app.route('/logout')
def logout():
    username = session.get('username', 'unknown')
    log_activity(username, 'LOGOUT', "Logged out")
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for('login'))


# ── Password reset ─────────────────────────────────────────────
@app.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def forgot_password():
    if request.method == 'POST':
        email = request.form['email'].strip()
        user  = query("SELECT * FROM users WHERE email=%s",
                      (email,), fetchone=True)
        if user:
            token  = secrets.token_urlsafe(32)
            expiry = datetime.now() + timedelta(hours=1)
            query(
                "UPDATE users SET reset_token=%s, "
                "reset_token_expiry=%s WHERE email=%s",
                (token, expiry, email), commit=True,
            )
            reset_url = url_for('reset_password',
                                token=token, _external=True)
            ok, err = send_email(
                email, "Reset your LendWise password",
                f"""<p>Hi <b>{user['username']}</b>,</p>
                    <p>Click to reset your password (expires 1 h):</p>
                    <p><a href="{reset_url}">{reset_url}</a></p>"""
            )
            if not ok:
                if app.debug:
                    flash(f"Mail failed ({err}). "
                          f"DEBUG link: {reset_url}", "warning")
                else:
                    flash("Mail delivery failed. "
                          "Check mail configuration.", "danger")
                return redirect(url_for('forgot_password'))
        flash("If that email is registered, a reset link has been sent.",
              "info")
        return redirect(url_for('login'))
    return render_template('auth/forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = query(
        "SELECT * FROM users "
        "WHERE reset_token=%s AND reset_token_expiry > NOW()",
        (token,), fetchone=True,
    )
    if not user:
        flash("Invalid or expired reset link.", "danger")
        return redirect(url_for('login'))
    if request.method == 'POST':
        password = request.form['password']
        confirm  = request.form['confirm_password']
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return redirect(request.url)
        if password != confirm:
            flash("Passwords do not match.", "danger")
            return redirect(request.url)
        query(
            "UPDATE users SET password=%s, "
            "reset_token=NULL, reset_token_expiry=NULL WHERE id=%s",
            (generate_password_hash(password), user['id']), commit=True,
        )
        log_activity(user['username'], 'PASSWORD_RESET',
                     "Password reset via email")
        flash("Password reset successfully. Please login.", "success")
        return redirect(url_for('login'))
    return render_template('auth/reset_password.html', token=token)


# ── Home / Catalogue ───────────────────────────────────────────
PER_PAGE = 12

@app.route('/')
def home():
    if 'username' not in session:
        return redirect(url_for('login'))

    search        = request.args.get('search', '').strip()
    filter_status = request.args.get('status', '')
    filter_cat    = request.args.get('category', '').strip()
    page          = max(1, int(request.args.get('page', 1)))

    sql       = "SELECT * FROM books WHERE 1=1"
    count_sql = "SELECT COUNT(*) AS c FROM books WHERE 1=1"
    params    = []

    if search:
        cond = (" AND (category LIKE %s OR book_name LIKE %s "
                "OR author_name LIKE %s)")
        sql       += cond
        count_sql += cond
        like = f'%{search}%'
        params.extend([like, like, like])

    if filter_status in ('Available', 'Not Available'):
        sql       += " AND status=%s"
        count_sql += " AND status=%s"
        params.append(filter_status)

    if filter_cat:
        sql       += " AND category=%s"
        count_sql += " AND category=%s"
        params.append(filter_cat)

    total_books = query(count_sql, params, fetchone=True)['c']
    total_pages = max(1, (total_books + PER_PAGE - 1) // PER_PAGE)
    page        = min(page, total_pages)
    offset      = (page - 1) * PER_PAGE

    books = query(sql + " ORDER BY id DESC LIMIT %s OFFSET %s",
                  params + [PER_PAGE, offset])
    for b in books:
        b['_cover'] = book_cover_url(b)

    borrower_map   = {}
    borrow_row_map = {}
    borrowed_ids   = [b['id'] for b in books
                      if b['status'] == 'Not Available']
    if borrowed_ids:
        ph   = ','.join(['%s'] * len(borrowed_ids))
        rows = query(
            f"""SELECT bb.book_id, bb.borrower_name, bb.renewals, bb.due_date
                FROM borrowed_books bb
                INNER JOIN (
                    SELECT book_id, MAX(borrow_date) AS latest
                    FROM borrowed_books GROUP BY book_id
                ) lb ON bb.book_id=lb.book_id
                       AND bb.borrow_date=lb.latest
                WHERE bb.book_id IN ({ph})""",
            borrowed_ids,
        )
        for r in rows:
            borrower_map[r['book_id']]   = r['borrower_name']
            borrow_row_map[r['book_id']] = r

    wishlist_ids = set()
    if session.get('role') == 'public':
        for w in query("SELECT book_id FROM wishlist WHERE username=%s",
                       (session['username'],)):
            wishlist_ids.add(w['book_id'])

    stats = None
    if session.get('role') == 'admin':
        r = read_many([
            {'sql': "SELECT COUNT(*) AS c FROM books",            'fetchone': True},
            {'sql': "SELECT COUNT(*) AS c FROM books WHERE status='Available'",     'fetchone': True},
            {'sql': "SELECT COUNT(*) AS c FROM books WHERE status='Not Available'", 'fetchone': True},
            {'sql': "SELECT COUNT(*) AS c FROM users WHERE role='public'",          'fetchone': True},
        ])
        stats = dict(total=r[0]['c'], available=r[1]['c'],
                     borrowed=r[2]['c'], members=r[3]['c'])

    return render_template('pages/home.html',
        books=books, stats=stats, search=search,
        filter_status=filter_status, filter_cat=filter_cat,
        borrower_map=borrower_map, borrow_row_map=borrow_row_map,
        wishlist_ids=wishlist_ids,
        current_user=session['username'],
        page=page, total_pages=total_pages, total_books=total_books,
        categories=_categories(),
        borrow_days=BORROW_DAYS, max_renewals=MAX_RENEWALS,
    )


# ── Book detail page ───────────────────────────────────────────
@app.route('/book/<int:id>')
def book_detail(id):
    if 'username' not in session:
        return redirect(url_for('login'))
    book = query("SELECT * FROM books WHERE id=%s", (id,), fetchone=True)
    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for('home'))
    book['_cover'] = book_cover_url(book)

    borrow_record = None
    if book['status'] == 'Not Available':
        borrow_record = query(
            """SELECT bb.*, u.email FROM borrowed_books bb
               LEFT JOIN users u ON bb.borrower_name = u.username
               WHERE bb.book_id=%s ORDER BY bb.borrow_date DESC LIMIT 1""",
            (id,), fetchone=True,
        )
        if borrow_record:
            borrow_record['fine'] = calc_fine(borrow_record['due_date'])

    borrow_count = query(
        "SELECT COUNT(*) AS c FROM borrowed_books WHERE book_id=%s",
        (id,), fetchone=True,
    )['c']

    in_wishlist = False
    if session.get('role') == 'public':
        in_wishlist = bool(query(
            "SELECT id FROM wishlist WHERE username=%s AND book_id=%s",
            (session['username'], id), fetchone=True,
        ))

    can_borrow = (
        session.get('role') == 'public'
        and book['status'] == 'Available'
    )
    can_return = (
        session.get('role') == 'public'
        and book['status'] == 'Not Available'
        and borrow_record
        and borrow_record.get('borrower_name') == session['username']
    )

    brow = None
    if can_return and borrow_record:
        brow = borrow_record

    return render_template('pages/book_detail.html',
        book=book,
        borrow_record=borrow_record if session.get('role') == 'admin' else None,
        borrow_count=borrow_count,
        in_wishlist=in_wishlist,
        can_borrow=can_borrow,
        can_return=can_return,
        brow=brow,
        max_renewals=MAX_RENEWALS,
        borrow_days=BORROW_DAYS,
    )


# ── Search suggestions ─────────────────────────────────────────
@app.route('/api/suggest')
def suggest():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    like = f'%{q}%'
    rows = query(
        """SELECT book_name AS label, author_name AS sub, 'book' AS type
           FROM books WHERE book_name LIKE %s
           UNION
           SELECT DISTINCT author_name, '', 'author'
           FROM books WHERE author_name LIKE %s
           UNION
           SELECT DISTINCT category, '', 'category'
           FROM books WHERE category LIKE %s
           LIMIT 8""",
        (like, like, like),
    )
    return jsonify(rows)

# FIX: wishlist count API for dashboard live update
@app.route('/api/wishlist-count')
def api_wishlist_count():
    if 'username' not in session or session.get('role') != 'public':
        return jsonify({'error': 'Unauthorized'}), 401
    row = query(
        "SELECT COUNT(*) AS c FROM wishlist WHERE username=%s",
        (session['username'],), fetchone=True,
    )
    return jsonify({'count': row['c']})


# ── REST API ───────────────────────────────────────────────────
@app.route('/api/books')
def api_books():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    page  = max(1, int(request.args.get('page', 1)))
    limit = min(50, int(request.args.get('limit', 20)))
    books = query("SELECT * FROM books ORDER BY id DESC LIMIT %s OFFSET %s",
                  (limit, (page - 1) * limit))
    total = query("SELECT COUNT(*) AS c FROM books", fetchone=True)['c']
    return jsonify({
        'books': [serialise_row(b) for b in books],
        'total': total, 'page': page, 'limit': limit,
    })

@app.route('/api/books/<int:id>')
def api_book(id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    book = query("SELECT * FROM books WHERE id=%s", (id,), fetchone=True)
    if not book:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(serialise_row(book))

@app.route('/api/stats')
def api_stats():
    if 'username' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401
    r = read_many([
        {'sql': "SELECT COUNT(*) AS c FROM books",            'fetchone': True},
        {'sql': "SELECT COUNT(*) AS c FROM books WHERE status='Available'",     'fetchone': True},
        {'sql': "SELECT COUNT(*) AS c FROM books WHERE status='Not Available'", 'fetchone': True},
        {'sql': "SELECT COUNT(*) AS c FROM users WHERE role='public'",          'fetchone': True},
        {'sql': "SELECT COUNT(*) AS c FROM borrowed_books",   'fetchone': True},
        {'sql': "SELECT COUNT(*) AS c FROM returned_books",   'fetchone': True},
    ])
    return jsonify({
        'total_books':   r[0]['c'], 'available':     r[1]['c'],
        'on_loan':       r[2]['c'], 'total_members': r[3]['c'],
        'total_borrows': r[4]['c'], 'total_returns': r[5]['c'],
    })


# ── Add / Update / Delete ──────────────────────────────────────
@app.route('/add', methods=['GET', 'POST'])
def add():
    if 'username' not in session or session.get('role') != 'admin':
        flash("Access denied.", "danger")
        return redirect(url_for('home'))
    if request.method == 'POST':
        author    = request.form['author'].strip()
        name      = request.form['name'].strip()
        category  = request.form['category'].strip()
        isbn      = request.form.get('isbn', '').strip()
        year      = request.form.get('year', '').strip()
        cover_url = request.form.get('cover_url', '').strip()

        if not author or not name or not category:
            flash("Author, title and category are required.", "danger")
            return redirect(url_for('add'))
        if isbn and not validate_isbn(isbn):
            flash("Invalid ISBN format.", "danger")
            return redirect(url_for('add'))
        if year and not validate_year(year):
            flash("Invalid year.", "danger")
            return redirect(url_for('add'))

        query(
            "INSERT INTO books "
            "(author_name, book_name, category, isbn, year, cover_url) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (author, name, category,
             isbn or None, year or None, cover_url or None),
            commit=True,
        )
        log_activity(session['username'], 'ADD_BOOK',
                     f"Added '{name}' by {author}")
        flash(f'"{name}" added successfully!', "success")
        return redirect(url_for('home'))
    return render_template('books/add.html', categories=_categories())


@app.route('/update/<int:id>', methods=['GET', 'POST'])
def update(id):
    if 'username' not in session or session.get('role') != 'admin':
        flash("Access denied.", "danger")
        return redirect(url_for('home'))
    book = query("SELECT * FROM books WHERE id=%s", (id,), fetchone=True)
    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for('home'))
    if request.method == 'POST':
        author    = request.form['author'].strip()
        name      = request.form['name'].strip()
        category  = request.form['category'].strip()
        isbn      = request.form.get('isbn', '').strip()
        year      = request.form.get('year', '').strip()
        cover_url = request.form.get('cover_url', '').strip()

        if not author or not name or not category:
            flash("Author, title and category are required.", "danger")
            return redirect(url_for('update', id=id))
        if isbn and not validate_isbn(isbn):
            flash("Invalid ISBN format.", "danger")
            return redirect(url_for('update', id=id))
        if year and not validate_year(year):
            flash("Invalid year.", "danger")
            return redirect(url_for('update', id=id))

        query(
            "UPDATE books SET author_name=%s, book_name=%s, "
            "category=%s, isbn=%s, year=%s, cover_url=%s WHERE id=%s",
            (author, name, category,
             isbn or None, year or None, cover_url or None, id),
            commit=True,
        )
        log_activity(session['username'], 'UPDATE_BOOK',
                     f"Updated book ID {id}: '{name}'")
        flash("Book updated successfully!", "success")
        return redirect(url_for('home'))
    return render_template('books/update.html',
                           book=book, categories=_categories())


@app.route('/delete/<int:id>', methods=['POST'])
def delete(id):
    if 'username' not in session or session.get('role') != 'admin':
        flash("Access denied.", "danger")
        return redirect(url_for('home'))
    book = query("SELECT book_name FROM books WHERE id=%s",
                 (id,), fetchone=True)
    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for('home'))
    if query(
        """SELECT bb.id FROM borrowed_books bb
           JOIN books b ON bb.book_id=b.id
           WHERE bb.book_id=%s AND b.status='Not Available' LIMIT 1""",
        (id,), fetchone=True,
    ):
        flash(f'"{book["book_name"]}" is currently borrowed '
              f'and cannot be deleted.', "danger")
        return redirect(url_for('home'))

    name = book['book_name']
    for tbl in ('wishlist', 'borrowed_books', 'returned_books'):
        query(f"DELETE FROM {tbl} WHERE book_id=%s", (id,), commit=True)
    query("DELETE FROM books WHERE id=%s", (id,), commit=True)
    log_activity(session['username'], 'DELETE_BOOK', f"Deleted '{name}'")
    flash("Book deleted successfully.", "success")
    return redirect(url_for('home'))


# ── Borrow ─────────────────────────────────────────────────────
@app.route('/borrow/<int:id>', methods=['GET', 'POST'])
@limiter.limit("10 per minute")   # FIX: rate limit borrow
def borrow(id):
    if 'username' not in session:
        return redirect(url_for('login'))
    # FIX: only public users can borrow
    if session.get('role') != 'public':
        flash("Admins do not borrow books.", "warning")
        return redirect(url_for('home'))

    book = query("SELECT * FROM books WHERE id=%s", (id,), fetchone=True)
    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for('home'))
    if book['status'] == 'Not Available':
        flash("This book is currently borrowed.", "warning")
        return redirect(url_for('home'))

    if request.method == 'POST':
        due_date = (datetime.now() +
                    timedelta(days=BORROW_DAYS)).strftime('%Y-%m-%d')

        # FIX: atomic check — only update if still Available
        rows_updated = query(
            "UPDATE books SET status='Not Available' "
            "WHERE id=%s AND status='Available'",
            (id,), commit=True,
        )
        if not rows_updated:
            flash("Sorry, this book was just borrowed by someone else.", "warning")
            return redirect(url_for('home'))

        query(
            "INSERT INTO borrowed_books "
            "(book_id, borrower_name, due_date) VALUES (%s,%s,%s)",
            (id, session['username'], due_date), commit=True,
        )
        query("DELETE FROM wishlist WHERE username=%s AND book_id=%s",
              (session['username'], id), commit=True)
        log_activity(session['username'], 'BORROW',
                     f"Borrowed '{book['book_name']}' — due {due_date}")
        user = query("SELECT email FROM users WHERE username=%s",
                     (session['username'],), fetchone=True)
        if user and user.get('email'):
            send_email(
                user['email'],
                f"You borrowed: {book['book_name']}",
                f"""<p>Hi <b>{session['username']}</b>,</p>
                    <p>You borrowed <b>{book['book_name']}</b>
                    by {book['author_name']}.</p>
                    <p>Please return it by <b>{due_date}</b>.</p>"""
            )
        flash(f'"{book["book_name"]}" borrowed! '
              f'Return by {due_date}.', "success")
        return redirect(url_for('home'))
    return render_template('books/borrow.html',
                           book=book, borrow_days=BORROW_DAYS)


# ── Return ─────────────────────────────────────────────────────
@app.route('/return/<int:id>', methods=['GET', 'POST'])
@limiter.limit("10 per minute")   # FIX: rate limit return
def return_book(id):
    if 'username' not in session:
        return redirect(url_for('login'))
    # FIX: only public users can return
    if session.get('role') != 'public':
        flash("Admins do not return books.", "warning")
        return redirect(url_for('home'))

    book = query("SELECT * FROM books WHERE id=%s", (id,), fetchone=True)
    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for('home'))
    if book['status'] != 'Not Available':
        flash("This book is not currently on loan.", "warning")
        return redirect(url_for('home'))

    # FIX: authorisation — must be the actual borrower of the active loan
    active = query(
        """SELECT * FROM borrowed_books
           WHERE book_id=%s AND borrower_name=%s
           ORDER BY borrow_date DESC LIMIT 1""",
        (id, session['username']), fetchone=True,
    )
    if not active:
        flash("You did not borrow this book.", "danger")
        return redirect(url_for('home'))

    if request.method == 'POST':
        fine = calc_fine(active['due_date']) if active else 0
        query("INSERT INTO returned_books (book_id, returner_name) "
              "VALUES (%s,%s)",
              (id, session['username']), commit=True)
        query("UPDATE books SET status='Available' WHERE id=%s",
              (id,), commit=True)
        log_activity(session['username'], 'RETURN',
                     f"Returned '{book['book_name']}'")
        if fine > 0:
            flash(f'"{book["book_name"]}" returned. '
                  f'Outstanding fine: ₹{fine}. '
                  f'Please pay at the library desk.', "warning")
        else:
            flash(f'"{book["book_name"]}" returned successfully!',
                  "success")
        return redirect(url_for('home'))
    fine = calc_fine(active['due_date']) if active else 0
    return render_template('books/return.html', book=book, fine=fine)


# ── Renew ──────────────────────────────────────────────────────
@app.route('/renew/<int:id>', methods=['POST'])
@limiter.limit("10 per minute")   # FIX: rate limit renew
def renew(id):
    if 'username' not in session:
        return redirect(url_for('login'))
    # FIX: only public users can renew
    if session.get('role') != 'public':
        flash("Admins do not renew books.", "warning")
        return redirect(url_for('home'))

    book = query("SELECT * FROM books WHERE id=%s", (id,), fetchone=True)
    if not book or book['status'] != 'Not Available':
        flash("Book not available for renewal.", "warning")
        return redirect(url_for('home'))

    # FIX: authorisation — must be the borrower
    rec = query(
        "SELECT * FROM borrowed_books "
        "WHERE book_id=%s AND borrower_name=%s "
        "ORDER BY borrow_date DESC LIMIT 1",
        (id, session['username']), fetchone=True,
    )
    if not rec:
        flash("You have not borrowed this book.", "danger")
        return redirect(url_for('home'))
    if rec['renewals'] >= MAX_RENEWALS:
        flash(f"Maximum renewals ({MAX_RENEWALS}) reached.", "warning")
        return redirect(url_for('home'))

    due = rec['due_date']
    if isinstance(due, str):
        due = datetime.strptime(due, '%Y-%m-%d').date()
    if isinstance(due, datetime):
        due = due.date()
    new_due = (max(due, date.today()) +
               timedelta(days=BORROW_DAYS)).strftime('%Y-%m-%d')
    query(
        "UPDATE borrowed_books "
        "SET due_date=%s, renewals=renewals+1, overdue_notified=0 "
        "WHERE id=%s",
        (new_due, rec['id']), commit=True,
    )
    log_activity(session['username'], 'RENEW',
                 f"Renewed '{book['book_name']}' — new due {new_due}")
    flash(f'"{book["book_name"]}" renewed! '
          f'New due date: {new_due}.', "success")
    return redirect(url_for('home'))


# ── Wishlist ───────────────────────────────────────────────────
@app.route('/wishlist')
def wishlist():
    if 'username' not in session:
        return redirect(url_for('login'))
    if session.get('role') == 'admin':
        return redirect(url_for('home'))
    books = query(
        """SELECT b.*, w.added_at AS wished_at
           FROM wishlist w
           JOIN books b ON w.book_id=b.id
           WHERE w.username=%s
           ORDER BY w.added_at DESC""",
        (session['username'],),
    )
    for b in books:
        b['_cover'] = book_cover_url(b)
    return render_template('pages/wishlist.html', books=books)

@app.route('/wishlist/add/<int:id>', methods=['POST'])
def wishlist_add(id):
    if 'username' not in session:
        return jsonify({'ok': False, 'msg': 'Login required'}), 401
    if session.get('role') == 'admin':
        return jsonify({'ok': False, 'msg': 'Admins have no wishlist'}), 403
    if not query("SELECT id FROM books WHERE id=%s", (id,), fetchone=True):
        return jsonify({'ok': False, 'msg': 'Book not found'}), 404
    try:
        query("INSERT IGNORE INTO wishlist (username, book_id) VALUES (%s,%s)",
              (session['username'], id), commit=True)
    except Exception:
        pass
    return jsonify({'ok': True, 'action': 'added'})

@app.route('/wishlist/remove/<int:id>', methods=['POST'])
def wishlist_remove(id):
    if 'username' not in session:
        if is_ajax():
            return jsonify({'ok': False, 'msg': 'Login required'}), 401
        return redirect(url_for('login'))
    if session.get('role') == 'admin':
        if is_ajax():
            return jsonify({'ok': False, 'msg': 'Admins have no wishlist'}), 403
        return redirect(url_for('home'))
    query("DELETE FROM wishlist WHERE username=%s AND book_id=%s",
          (session['username'], id), commit=True)
    if is_ajax():
        return jsonify({'ok': True, 'action': 'removed'})
    flash("Removed from wishlist.", "info")
    return redirect(url_for('wishlist'))


# ── Member dashboard ───────────────────────────────────────────
@app.route('/my-dashboard')
def my_dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))
    if session.get('role') == 'admin':
        return redirect(url_for('dashboard'))

    username = session['username']
    active_loans = query(
        """SELECT bb.*, b.book_name, b.author_name, b.isbn,
                  b.cover_url, b.category,
                  CASE WHEN bb.due_date < CURDATE() THEN 1 ELSE 0 END AS is_overdue
           FROM borrowed_books bb
           JOIN books b ON bb.book_id=b.id
           WHERE bb.borrower_name=%s AND b.status='Not Available'
           ORDER BY bb.due_date ASC""",
        (username,),
    )
    for loan in active_loans:
        loan['fine']   = calc_fine(loan['due_date'])
        loan['_cover'] = book_cover_url(loan)
        due = loan.get('due_date')
        loan['due_date_fmt'] = (
            due.strftime('%d %b %Y') if hasattr(due, 'strftime') else str(due)
        )

    r = read_many([
        {'sql': "SELECT COUNT(*) AS c FROM wishlist WHERE username=%s",
         'params': (username,), 'fetchone': True},
        {'sql': "SELECT COUNT(*) AS c FROM borrowed_books WHERE borrower_name=%s",
         'params': (username,), 'fetchone': True},
        {'sql': "SELECT COUNT(*) AS c FROM returned_books WHERE returner_name=%s",
         'params': (username,), 'fetchone': True},
    ])
    monthly = query(
        """SELECT DATE_FORMAT(borrow_date,'%b %Y') AS month,
                  DATE_FORMAT(borrow_date,'%Y-%m') AS sort_key,
                  COUNT(*) AS count
           FROM borrowed_books
           WHERE borrower_name=%s
             AND borrow_date >= DATE_SUB(NOW(), INTERVAL 6 MONTH)
           GROUP BY month, sort_key ORDER BY sort_key""",
        (username,),
    )
    return render_template('pages/my_dashboard.html',
        active_loans=active_loans,
        total_fine=sum(l['fine'] for l in active_loans),
        wcount=r[0]['c'],
        total_borrows=r[1]['c'],
        total_returns=r[2]['c'],
        monthly=monthly,
        max_renewals=MAX_RENEWALS,
        fine_rate=FINE_RATE,
    )


# ── Fine paid (admin) ──────────────────────────────────────────
@app.route('/admin/fine-paid/<int:borrow_id>', methods=['POST'])
def fine_paid(borrow_id):
    if 'username' not in session or session.get('role') != 'admin':
        flash("Access denied.", "danger")
        return redirect(url_for('home'))
    query("UPDATE borrowed_books SET fine_paid=1 WHERE id=%s",
          (borrow_id,), commit=True)
    flash("Fine marked as paid.", "success")
    return redirect(request.referrer or url_for('history'))


# ── History ────────────────────────────────────────────────────
@app.route('/history')
def history():
    if 'username' not in session:
        return redirect(url_for('login'))
    role     = session.get('role', 'public')
    username = session['username']
    page_b   = max(1, int(request.args.get('page_b', 1)))
    page_r   = max(1, int(request.args.get('page_r', 1)))
    PER      = 15

    if role == 'admin':
        total_b  = query("SELECT COUNT(*) AS c FROM borrowed_books",
                         fetchone=True)['c']
        total_r  = query("SELECT COUNT(*) AS c FROM returned_books",
                         fetchone=True)['c']
        borrowed = query(
            """SELECT bb.id, b.book_name, b.author_name, bb.borrower_name,
                      bb.borrow_date, bb.due_date, bb.renewals, bb.fine_paid,
                      CASE WHEN bb.due_date < CURDATE() THEN 1 ELSE 0 END AS is_overdue
               FROM borrowed_books bb JOIN books b ON bb.book_id=b.id
               ORDER BY bb.borrow_date DESC LIMIT %s OFFSET %s""",
            (PER, (page_b - 1) * PER),
        )
        returned = query(
            """SELECT rb.id, b.book_name, b.author_name,
                      rb.returner_name, rb.return_date
               FROM returned_books rb JOIN books b ON rb.book_id=b.id
               ORDER BY rb.return_date DESC LIMIT %s OFFSET %s""",
            (PER, (page_r - 1) * PER),
        )
    else:
        total_b  = query("SELECT COUNT(*) AS c FROM borrowed_books "
                         "WHERE borrower_name=%s",
                         (username,), fetchone=True)['c']
        total_r  = query("SELECT COUNT(*) AS c FROM returned_books "
                         "WHERE returner_name=%s",
                         (username,), fetchone=True)['c']
        borrowed = query(
            """SELECT bb.id, b.book_name, b.author_name, bb.borrower_name,
                      bb.borrow_date, bb.due_date, bb.renewals, bb.fine_paid,
                      CASE WHEN bb.due_date < CURDATE() THEN 1 ELSE 0 END AS is_overdue
               FROM borrowed_books bb JOIN books b ON bb.book_id=b.id
               WHERE bb.borrower_name=%s
               ORDER BY bb.borrow_date DESC LIMIT %s OFFSET %s""",
            (username, PER, (page_b - 1) * PER),
        )
        returned = query(
            """SELECT rb.id, b.book_name, b.author_name,
                      rb.returner_name, rb.return_date
               FROM returned_books rb JOIN books b ON rb.book_id=b.id
               WHERE rb.returner_name=%s
               ORDER BY rb.return_date DESC LIMIT %s OFFSET %s""",
            (username, PER, (page_r - 1) * PER),
        )

    for row in borrowed:
        row['fine'] = calc_fine(row['due_date'])
        if row.get('borrow_date'):
            row['borrow_date'] = row['borrow_date'].strftime("%d %b %Y, %I:%M %p")
        if row.get('due_date') and hasattr(row['due_date'], 'strftime'):
            row['due_date'] = row['due_date'].strftime("%d %b %Y")
    for row in returned:
        if row.get('return_date'):
            row['return_date'] = row['return_date'].strftime("%d %b %Y, %I:%M %p")

    return render_template('pages/history.html',
        borrowed=borrowed, returned=returned,
        page_b=page_b, page_r=page_r,
        total_b=total_b, total_r=total_r,
        total_pages_b=max(1, (total_b + PER - 1) // PER),
        total_pages_r=max(1, (total_r + PER - 1) // PER),
        fine_rate=FINE_RATE,
    )


# ── Admin dashboard ────────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    if 'username' not in session or session.get('role') != 'admin':
        flash("Access denied.", "danger")
        return redirect(url_for('home'))

    borrows_monthly = query(
        """SELECT DATE_FORMAT(borrow_date,'%b %Y') AS month,
                  DATE_FORMAT(borrow_date,'%Y-%m') AS sort_key,
                  COUNT(*) AS count
           FROM borrowed_books
           WHERE borrow_date >= DATE_SUB(NOW(), INTERVAL 6 MONTH)
           GROUP BY month, sort_key ORDER BY sort_key"""
    )
    returns_monthly = query(
        """SELECT DATE_FORMAT(return_date,'%b %Y') AS month,
                  DATE_FORMAT(return_date,'%Y-%m') AS sort_key,
                  COUNT(*) AS count
           FROM returned_books
           WHERE return_date >= DATE_SUB(NOW(), INTERVAL 6 MONTH)
           GROUP BY month, sort_key ORDER BY sort_key"""
    )
    top_categories = query(
        """SELECT b.category, COUNT(*) AS count
           FROM borrowed_books bb JOIN books b ON bb.book_id=b.id
           GROUP BY b.category ORDER BY count DESC LIMIT 6"""
    )
    top_borrowers = query(
        """SELECT borrower_name, COUNT(*) AS count
           FROM borrowed_books
           GROUP BY borrower_name ORDER BY count DESC LIMIT 5"""
    )
    top_books = query(
        """SELECT b.book_name, b.author_name, COUNT(*) AS count
           FROM borrowed_books bb JOIN books b ON bb.book_id=b.id
           GROUP BY b.id, b.book_name, b.author_name
           ORDER BY count DESC LIMIT 5"""
    )
    recent_activity = query(
        "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT 10"
    )
    for r in recent_activity:
        if r.get('created_at'):
            r['created_at'] = r['created_at'].strftime("%d %b %Y, %I:%M %p")

    overdue_rows = query(
        """SELECT bb.due_date FROM borrowed_books bb
           JOIN books b ON bb.book_id=b.id
           WHERE bb.due_date < CURDATE()
             AND b.status='Not Available' AND bb.fine_paid=0"""
    )
    total_fines = sum(calc_fine(r['due_date']) for r in overdue_rows)

    r = read_many([
        {'sql': "SELECT COUNT(*) AS c FROM books",            'fetchone': True},
        {'sql': "SELECT COUNT(*) AS c FROM books WHERE status='Available'",     'fetchone': True},
        {'sql': "SELECT COUNT(*) AS c FROM books WHERE status='Not Available'", 'fetchone': True},
        {'sql': "SELECT COUNT(*) AS c FROM users WHERE role='public'",          'fetchone': True},
        {'sql': "SELECT COUNT(*) AS c FROM borrowed_books",   'fetchone': True},
        {'sql': "SELECT COUNT(*) AS c FROM returned_books",   'fetchone': True},
        {'sql': """SELECT COUNT(*) AS c FROM borrowed_books bb
                   JOIN books b ON bb.book_id=b.id
                   WHERE bb.due_date < CURDATE() AND b.status='Not Available'""",
         'fetchone': True},
    ])
    stats = dict(
        total=r[0]['c'],         available=r[1]['c'],
        borrowed=r[2]['c'],      members=r[3]['c'],
        total_borrows=r[4]['c'], total_returns=r[5]['c'],
        overdue=r[6]['c'],       total_fines=total_fines,
    )
    return render_template('pages/dashboard.html',
        stats=stats,
        borrows_monthly=borrows_monthly,
        returns_monthly=returns_monthly,
        top_categories=top_categories,
        top_borrowers=top_borrowers,
        top_books=top_books,
        recent_activity=recent_activity,
    )


# ── Members ────────────────────────────────────────────────────
@app.route('/members')
def members():
    if 'username' not in session or session.get('role') != 'admin':
        flash("Access denied.", "danger")
        return redirect(url_for('home'))
    page   = max(1, int(request.args.get('page', 1)))
    search = request.args.get('search', '').strip()   # FIX: member search
    PER    = 15

    sql       = "SELECT id, username, role, last_login, email FROM users WHERE 1=1"
    count_sql = "SELECT COUNT(*) AS c FROM users WHERE 1=1"
    params    = []

    if search:
        sql       += " AND username LIKE %s"
        count_sql += " AND username LIKE %s"
        params.append(f'%{search}%')

    total = query(count_sql, params, fetchone=True)['c']
    users = query(
        sql + " ORDER BY id DESC LIMIT %s OFFSET %s",
        params + [PER, (page - 1) * PER],
    )
    for u in users:
        if u.get('last_login'):
            u['last_login'] = u['last_login'].strftime("%d %b %Y")
    return render_template('pages/members.html',
        users=users, page=page, search=search,
        total_pages=max(1, (total + PER - 1) // PER),
    )


# ── Activity log ───────────────────────────────────────────────
@app.route('/activity')
def activity():
    if 'username' not in session or session.get('role') != 'admin':
        flash("Access denied.", "danger")
        return redirect(url_for('home'))
    page      = max(1, int(request.args.get('page', 1)))
    action    = request.args.get('action', '')
    date_from = request.args.get('date_from', '').strip()
    date_to   = request.args.get('date_to', '').strip()
    PER       = 20

    sql    = "SELECT * FROM activity_log WHERE 1=1"
    count  = "SELECT COUNT(*) AS c FROM activity_log WHERE 1=1"
    params = []
    if action:
        sql   += " AND action=%s";  count += " AND action=%s"
        params.append(action)
    if date_from:
        sql   += " AND DATE(created_at) >= %s"
        count += " AND DATE(created_at) >= %s"
        params.append(date_from)
    if date_to:
        sql   += " AND DATE(created_at) <= %s"
        count += " AND DATE(created_at) <= %s"
        params.append(date_to)

    total = query(count, params, fetchone=True)['c']
    logs  = query(sql + " ORDER BY created_at DESC LIMIT %s OFFSET %s",
                  params + [PER, (page - 1) * PER])
    for r in logs:
        if r.get('created_at'):
            r['created_at'] = r['created_at'].strftime("%d %b %Y, %I:%M %p")

    return render_template('pages/activity.html',
        logs=logs,
        page=page, total_pages=max(1, (total + PER - 1) // PER),
        action=action,
        actions=query("SELECT DISTINCT action FROM activity_log ORDER BY action"),
        date_from=date_from, date_to=date_to,
    )


# ── Exports ────────────────────────────────────────────────────
@app.route('/export/catalogue/<fmt>')
def export_catalogue(fmt):
    if 'username' not in session or session.get('role') != 'admin':
        flash("Access denied.", "danger")
        return redirect(url_for('home'))

    books = query("SELECT id, book_name, author_name, category, "
                  "isbn, year, status FROM books ORDER BY id")
    log_activity(session['username'], 'EXPORT',
                 f"Exported catalogue as {fmt.upper()}")

    if fmt == 'csv':
        out = io.StringIO()
        w   = csv.writer(out)
        w.writerow(['ID', 'Title', 'Author', 'Category',
                    'ISBN', 'Year', 'Status'])
        for b in books:
            w.writerow([b['id'], b['book_name'], b['author_name'],
                        b['category'], b.get('isbn', ''),
                        b.get('year', ''), b['status']])
        return Response(out.getvalue(), mimetype='text/csv',
            headers={'Content-Disposition':
                     'attachment;filename=lendwise_catalogue.csv'})

    if fmt == 'pdf' and REPORTLAB_OK:
        buf  = io.BytesIO()
        doc  = SimpleDocTemplate(buf, pagesize=A4,
                   topMargin=40, bottomMargin=40,
                   leftMargin=40, rightMargin=40)
        styl = getSampleStyleSheet()
        data = [['ID', 'Title', 'Author', 'Category', 'Year', 'Status']]
        for b in books:
            data.append([str(b['id']), b['book_name'][:35],
                         b['author_name'][:25], b['category'][:18],
                         b.get('year', '') or '', b['status']])
        t = Table(data, colWidths=[30, 160, 120, 90, 40, 75])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0e0e18')),
            ('TEXTCOLOR',  (0,0), (-1,0), colors.HexColor('#c8bfa8')),
            ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0,0), (-1,0), 9),
            ('ROWBACKGROUNDS', (0,1), (-1,-1),
             [colors.HexColor('#f9f9f9'), colors.white]),
            ('FONTSIZE',   (0,1), (-1,-1), 8),
            ('GRID',       (0,0), (-1,-1), 0.4, colors.HexColor('#dddddd')),
            ('ROWPADDING', (0,0), (-1,-1), 6),
        ]))
        elems = [
            Paragraph("LendWise — Book Catalogue", styl['Title']),
            Paragraph(f"Generated: "
                      f"{datetime.now().strftime('%d %b %Y %H:%M')}",
                      styl['Normal']),
            Spacer(1, 20), t,
        ]
        doc.build(elems)
        buf.seek(0)
        return Response(buf, mimetype='application/pdf',
            headers={'Content-Disposition':
                     'attachment;filename=lendwise_catalogue.pdf'})

    flash("Export format not available.", "warning")
    return redirect(url_for('home'))


@app.route('/export/history/<fmt>')
def export_history(fmt):
    if 'username' not in session or session.get('role') != 'admin':
        flash("Access denied.", "danger")
        return redirect(url_for('home'))
    borrowed = query(
        """SELECT bb.id, b.book_name, b.author_name, bb.borrower_name,
                  bb.borrow_date, bb.due_date, bb.renewals
           FROM borrowed_books bb JOIN books b ON bb.book_id=b.id
           ORDER BY bb.borrow_date DESC"""
    )
    log_activity(session['username'], 'EXPORT',
                 f"Exported history as {fmt.upper()}")
    if fmt == 'csv':
        out = io.StringIO()
        w   = csv.writer(out)
        w.writerow(['ID', 'Book', 'Author', 'Borrower',
                    'Borrow Date', 'Due Date', 'Renewals'])
        for r in borrowed:
            w.writerow([
                r['id'], r['book_name'], r['author_name'],
                r['borrower_name'],
                r['borrow_date'].strftime('%d %b %Y') if r['borrow_date'] else '',
                r['due_date'].strftime('%d %b %Y')    if r['due_date']    else '',
                r['renewals'],
            ])
        return Response(out.getvalue(), mimetype='text/csv',
            headers={'Content-Disposition':
                     'attachment;filename=lendwise_history.csv'})
    flash("PDF history export coming soon.", "info")
    return redirect(url_for('history'))


def _build_history_pdf(username, rows):
    buf  = io.BytesIO()
    doc  = SimpleDocTemplate(buf, pagesize=A4,
               topMargin=40, bottomMargin=40,
               leftMargin=40, rightMargin=40)
    styl = getSampleStyleSheet()
    data = [['#', 'Book', 'Author', 'Borrowed', 'Due', 'Fine', 'R']]
    for r in rows:
        fine = calc_fine(r['due_date']) if not r.get('fine_paid') else 0
        data.append([
            str(r['id']),
            r['book_name'][:30],
            r['author_name'][:20],
            r['borrow_date'].strftime('%d %b %Y') if r.get('borrow_date') else '',
            r['due_date'].strftime('%d %b %Y')    if r.get('due_date')    else '',
            f"Rs.{fine}" if fine > 0 else '-',
            str(r.get('renewals', 0)),
        ])
    t = Table(data, colWidths=[25, 140, 100, 75, 75, 45, 20])
    t.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,0), colors.HexColor('#0e0e18')),
        ('TEXTCOLOR',     (0,0), (-1,0), colors.HexColor('#c8bfa8')),
        ('FONTNAME',      (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',      (0,0), (-1,0), 8),
        ('ROWBACKGROUNDS',(0,1), (-1,-1),
         [colors.HexColor('#f9f9f9'), colors.white]),
        ('FONTSIZE',      (0,1), (-1,-1), 7),
        ('GRID',          (0,0), (-1,-1), 0.3, colors.HexColor('#dddddd')),
        ('ROWPADDING',    (0,0), (-1,-1), 5),
    ]))
    elems = [
        Paragraph(f"LendWise — Borrow History: {username}", styl['Title']),
        Paragraph(f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')}",
                  styl['Normal']),
        Spacer(1, 20), t,
    ]
    doc.build(elems)
    buf.seek(0)
    return buf

@app.route('/export/my-history/pdf')
def export_my_history_pdf():
    if 'username' not in session:
        return redirect(url_for('login'))
    if not REPORTLAB_OK:
        flash("PDF export is not available on this server.", "warning")
        return redirect(url_for('history'))
    username = session['username']
    rows = query(
        """SELECT bb.id, b.book_name, b.author_name,
                  bb.borrow_date, bb.due_date, bb.renewals, bb.fine_paid
           FROM borrowed_books bb JOIN books b ON bb.book_id=b.id
           WHERE bb.borrower_name=%s ORDER BY bb.borrow_date DESC""",
        (username,),
    )
    log_activity(username, 'EXPORT', "Exported personal history as PDF")
    return Response(
        _build_history_pdf(username, rows),
        mimetype='application/pdf',
        headers={'Content-Disposition':
                 f'attachment;filename=lendwise_history_{username}.pdf'},
    )

@app.route('/export/member-history/<username>/pdf')
def export_member_history_pdf(username):
    if 'username' not in session or session.get('role') != 'admin':
        flash("Access denied.", "danger")
        return redirect(url_for('home'))
    if not REPORTLAB_OK:
        flash("PDF export not available.", "warning")
        return redirect(url_for('members'))
    rows = query(
        """SELECT bb.id, b.book_name, b.author_name,
                  bb.borrow_date, bb.due_date, bb.renewals, bb.fine_paid
           FROM borrowed_books bb JOIN books b ON bb.book_id=b.id
           WHERE bb.borrower_name=%s ORDER BY bb.borrow_date DESC""",
        (username,),
    )
    log_activity(session['username'], 'EXPORT',
                 f"Exported history for {username} as PDF")
    return Response(
        _build_history_pdf(username, rows),
        mimetype='application/pdf',
        headers={'Content-Disposition':
                 f'attachment;filename=lendwise_history_{username}.pdf'},
    )


# ── Scheduler ──────────────────────────────────────────────────
# FIX: only start scheduler once — avoids duplicate jobs under Flask reloader
if SCHEDULER_OK:
    _run_main = os.environ.get('WERKZEUG_RUN_MAIN')
    if not app.debug or _run_main == 'true':
        scheduler = BackgroundScheduler()
        scheduler.add_job(check_overdue, 'interval', hours=24,
                          id='overdue_check', replace_existing=True)
        scheduler.start()
        atexit.register(lambda: scheduler.shutdown(wait=False))

if __name__ == '__main__':
    app.run(debug=True)