# LendWise — Library Management System

A full-stack library management system built with Flask and MySQL.
Supports book cataloguing, borrowing, returning, renewals, fines,
wishlists, member dashboards, admin analytics, and email notifications.

---

## Features

**For Members**
- Browse and search the book catalogue with live autocomplete
- Borrow and return books with 14-day due dates
- Renew a borrow once before the due date
- Wishlist — save books to borrow later
- Personal dashboard with active loans, fines, and borrow trend chart
- Export personal borrow history as PDF
- Email notifications for borrows and overdue reminders

**For Admins**
- Full catalogue management (add, edit, delete with cover images)
- Admin dashboard with charts, top books, top members, recent activity
- Borrow/return history with fine tracking and mark-as-paid
- Member list with per-member PDF history export
- Activity log with action and date-range filters
- CSV and PDF catalogue exports

**General**
- Role-based access (admin / public)
- Secure auth: bcrypt passwords, CSRF protection, rate limiting
- Remember me (30-day session option)
- Password reset via email token
- Email verification on signup
- Session timeout after 30 minutes of inactivity
- Book cover images via Open Library Covers API (ISBN-based, no API key needed)

---

## Tech Stack

| Layer     | Technology                                |
|-----------|-------------------------------------------|
| Backend   | Python 3.10+, Flask 3.x                   |
| Database  | MySQL 8.x                                 |
| Frontend  | Jinja2, vanilla JS, CSS custom properties |
| Auth      | Flask-WTF (CSRF), Werkzeug (bcrypt)       |
| Mail      | Flask-Mail + Gmail SMTP                   |
| PDF       | ReportLab                                 |
| Charts    | Chart.js 4.4                              |
| Icons     | Tabler Icons                              |
| Scheduler | APScheduler (overdue email reminders)     |

---

## Project Structure

```
lendwise/
├── app.py                   # All routes and business logic
├── .env                     # Secret config (not committed)
├── .env.example             # Template for .env
├── requirements.txt
├── README.md
├── database/
│   └── schema.sql           # Database schema
├── static/
│   ├── css/
│   │   ├── style.css        # Main design system
│   │   └── auth.css         # Auth pages styling
│   └── js/
│       ├── main.js          # UI helpers (toast, drawer, CSRF)
│       ├── auth-particles.js
│       └── page-particles.js
└── templates/
    ├── base.html            # Main layout
    ├── auth_base.html       # Auth layout
    ├── macros/
    │   ├── flash.html
    │   ├── nav_links.html
    │   └── pagination.html
    ├── auth/
    │   ├── login.html
    │   ├── signup.html
    │   ├── forgot_password.html
    │   └── reset_password.html
    ├── books/
    │   ├── add.html
    │   ├── update.html
    │   ├── borrow.html
    │   └── return.html
    └── pages/
        ├── home.html
        ├── dashboard.html
        ├── my_dashboard.html
        ├── history.html
        ├── wishlist.html
        ├── members.html
        └── activity.html
```

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/your-username/lendwise.git
cd lendwise
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set up the database

```bash
mysql -u root -p < database/schema.sql
```

### 5. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in your values — database credentials, a generated secret key, and a Gmail App Password.

---

## Gmail Setup (for email features)

1. Enable **2-Step Verification** on your Google account
2. Go to **Google Account → Security → App Passwords**
3. Generate a password for "Mail / Windows Computer"
4. Paste the 16-character password (no spaces) into `MAIL_PASSWORD` in `.env`

Password reset, email verification, and overdue reminders all require this to be set up.
If mail is not configured, the app auto-verifies new accounts and shows the reset link directly in debug mode.

---

## Deployment Checklist

Before deploying to production:

- [ ] Set `DEBUG = False` (remove `debug=True` from `app.run()` or use a production WSGI server)
- [ ] Generate a strong `SECRET_KEY` (32+ random hex characters)
- [ ] Use a production WSGI server: **Gunicorn** (Linux) or **Waitress** (Windows)
- [ ] Point to a production MySQL instance (not localhost)
- [ ] Set up HTTPS (use a reverse proxy like Nginx or deploy to a platform that handles TLS)
- [ ] Verify Gmail App Password works by triggering a password reset
- [ ] Run `UPDATE users SET is_verified=1;` if existing users are unverified

### Quick deploy with Gunicorn

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

### Quick deploy with Waitress (Windows)

```bash
pip install waitress
waitress-serve --port=8000 app:app
```

---