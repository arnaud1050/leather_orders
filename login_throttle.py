"""
Sign-in attempt limits (REQUIREMENTS CO4k–CO4m).

Without this, /login answers as many password guesses as anyone cares to
send. Two limits, both counted over the same sliding window:

* **per email** — `MAX_FAILURES_PER_EMAIL` failed attempts for one address
  within `WINDOW` and that address is refused until the oldest of them ages
  out. Applied to every address typed, whether or not it has an account:
  locking only real ones would turn the lockout message into the very
  "does this account exist?" oracle the single "Incorrect email or
  password" message exists to avoid.
* **per client IP** — a coarser `MAX_FAILURES_PER_IP`, for one source
  trying many addresses. The IP is `request.remote_addr`, which `ProxyFix`
  rewrites from nginx's X-Forwarded-For when `TRUST_PROXY_HEADERS=1` (as
  in both deployments). Without that, every request would seem to come
  from nginx and this limit would lock everyone out at once.

**Stored in the database, not in memory**, because gunicorn runs two
workers: a counter in one process is a limit of twice the number, and
gone on every restart. Only failures are written, so the table stays tiny,
and rows older than the window are pruned whenever one is added. It's a
new table, so `db.create_all()` covers it (hard rule 12).

**A refused attempt doesn't check the password and isn't recorded.** Not
checking it is the point: a correct password during a lockout must be
refused like any other, or the lockout is just a slower oracle. Not
recording it means hammering a locked address doesn't extend the lock, so
somebody who wants to keep a studio's owner out has to keep paying for it
at the same rate as anybody else.

A successful sign-in clears that address's failures (not the IP's: one
valid account mustn't reset a source's budget for guessing others).
"""

from datetime import datetime, timedelta, timezone

from models import db

MAX_FAILURES_PER_EMAIL = 5
MAX_FAILURES_PER_IP = 20
WINDOW = timedelta(minutes=15)

LOCKED_MESSAGE = "Too many sign-in attempts. Wait 15 minutes, then try again."


def _utcnow() -> datetime:
    """Naive UTC, like every other timestamp in SQLite here. A function so
    tests can move the clock."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LoginAttempt(db.Model):
    """One failed sign-in. Nothing but the address typed, where from, and
    when — no password, nothing about whether the account exists."""

    __tablename__ = "login_attempts"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    ip = db.Column(db.String(64), nullable=False, index=True)
    attempted_at = db.Column(db.DateTime, nullable=False, index=True)


def _recent(column, value, since):
    return (LoginAttempt.query
            .filter(column == value, LoginAttempt.attempted_at > since)
            .count())


def _key(email: str) -> str:
    # The column's width: a megabyte typed into the email box is still one
    # address's worth of row.
    return (email or "")[:255]


def is_locked(email: str, ip: str) -> bool:
    """True if either limit is reached: refuse without checking anything."""
    email, ip = _key(email), (ip or "")[:64]
    since = _utcnow() - WINDOW
    if _recent(LoginAttempt.email, email, since) >= MAX_FAILURES_PER_EMAIL:
        return True
    return bool(ip) and _recent(LoginAttempt.ip, ip, since) >= MAX_FAILURES_PER_IP


def record_failure(email: str, ip: str) -> None:
    email = _key(email)
    now = _utcnow()
    LoginAttempt.query.filter(LoginAttempt.attempted_at <= now - WINDOW).delete(
        synchronize_session=False)
    db.session.add(LoginAttempt(email=email, ip=(ip or "")[:64], attempted_at=now))
    db.session.commit()


def clear(email: str) -> None:
    """Forget this address's failures. Writes (and commits) only when there
    are some: an ordinary sign-in shouldn't cost a write, nor commit work
    somebody else left pending in the session."""
    pending = LoginAttempt.query.filter_by(email=_key(email))
    if pending.first() is None:
        return
    pending.delete(synchronize_session=False)
    db.session.commit()
