"""
The module's blueprint: the Costs tab's time entries and calculator, and
Settings → Costs.

The two GET pages (`order_costs()`, `settings_costs()`) stay in app.py, the
same split inventory has: app.py owns the page and gathers what this module
can't know (revenue from billing, materials from inventory), this module
owns the partials and every form in them.

`resolve_order(order_id)` is the host's `get_order_or_404`, handed over at
registration (inventory `A4`'s pattern), so every order-scoped route here is
tenant-checked before it touches anything.

Notices use the host's session keys and `{message, category, section}`
shape (`order_notice`, `settings_notice`) — written here directly rather
than imported, because importing app.py from a blueprint is circular.
"""

import math
import re
from datetime import date

from flask import Blueprint, redirect, request, session, url_for
from flask_login import current_user, login_required

from models import db
from usage import track

from costing import services

bp = Blueprint("costing", __name__, template_folder="templates")

_resolve_order = None


def register(app, *, resolve_order) -> None:
    global _resolve_order
    _resolve_order = resolve_order
    app.register_blueprint(bp)


def _number(raw: str | None) -> float | None:
    """A finite number, or None for blank/junk/inf/nan — the host's OR6a."""
    try:
        value = float((raw or "").strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _section() -> str | None:
    raw = request.form.get("notice_section") or ""
    return raw if re.fullmatch(r"[a-z0-9-]{1,40}", raw) else None


def _flash_order_notice(message: str, category: str = "error") -> None:
    session["order_notice"] = {"message": message, "category": category, "section": _section()}


def _flash_settings_notice(message: str, category: str = "error") -> None:
    session["settings_notice"] = {"message": message, "category": category, "section": _section()}


def _back(order_id: int):
    return redirect(request.form.get("return_to") or url_for("order_costs", order_id=order_id))


# ---------------------------------------------------------------------------
# Time — /orders/<id>/costs/time
# ---------------------------------------------------------------------------

@bp.route("/orders/<int:order_id>/costs/time", methods=["POST"])
@login_required
def add_time(order_id: int):
    order = _resolve_order(order_id)
    try:
        worked_on = date.fromisoformat(request.form.get("worked_on", ""))
    except ValueError:
        _flash_order_notice("Choose the day the work was done.")
        return _back(order.id)
    hours = _number(request.form.get("hours")) or 0.0
    minutes = _number(request.form.get("minutes")) or 0.0
    if hours < 0 or minutes < 0:
        _flash_order_notice("Hours and minutes can't be negative.")
        return _back(order.id)
    try:
        services.add_entry(
            current_user.company_id, order.id, user_id=current_user.id,
            worked_on=worked_on, minutes=round(hours * 60 + minutes),
            note=request.form.get("note"),
        )
    except ValueError as error:
        _flash_order_notice(str(error))
        return _back(order.id)
    db.session.commit()
    track("order.time_logged")
    _flash_order_notice("Time logged.", "success")
    return _back(order.id)


@bp.route("/orders/<int:order_id>/costs/time/<int:entry_id>/delete", methods=["POST"])
@login_required
def delete_time(order_id: int, entry_id: int):
    order = _resolve_order(order_id)
    if services.delete_entry(order.id, entry_id):
        db.session.commit()
    return _back(order.id)


# ---------------------------------------------------------------------------
# The calculator — /orders/<id>/costs/estimate
# ---------------------------------------------------------------------------

@bp.route("/orders/<int:order_id>/costs/estimate", methods=["POST"])
@login_required
def save_estimate(order_id: int):
    order = _resolve_order(order_id)
    existing = services.estimate_for(order.id)
    # A field the form didn't send keeps what was saved (hard rule 9); one
    # sent blank means "use the default" for the two optional inputs.
    hours = (_number(request.form.get("hours")) if "hours" in request.form
             else (existing.hours if existing else 0.0))
    if hours is None:
        _flash_order_notice("Enter the hours you expect the work to take.")
        return _back(order.id)
    material_cost = (_number(request.form.get("material_cost")) if "material_cost" in request.form
                     else (existing.material_cost if existing else None))
    target = (_number(request.form.get("target_margin_percent")) if "target_margin_percent" in request.form
              else (existing.target_margin_percent if existing else None))
    try:
        services.save_estimate(
            current_user.company_id, order.id, hours=hours,
            material_cost=material_cost, target_margin_percent=target,
        )
    except ValueError as error:
        _flash_order_notice(str(error))
        return _back(order.id)
    db.session.commit()
    track("order.price_estimated")
    _flash_order_notice("Estimate saved.", "success")
    return _back(order.id)


# ---------------------------------------------------------------------------
# Settings → Costs
# ---------------------------------------------------------------------------

_SETTINGS_FIELDS = ("hourly_rate", "overhead_percent", "target_margin_percent")


@bp.route("/settings/costs", methods=["POST"])
@login_required
def update_settings():
    values = {}
    for field in _SETTINGS_FIELDS:
        if field not in request.form:
            continue  # not on the form: leave it alone (hard rule 9)
        value = _number(request.form.get(field))
        if value is None:
            _flash_settings_notice("Enter a number in every field — 0 if it doesn't apply.")
            return redirect(url_for("settings_costs"))
        values[field] = value
    try:
        services.update_settings(current_user.company_id, **values)
    except ValueError as error:
        _flash_settings_notice(str(error))
        return redirect(url_for("settings_costs"))
    db.session.commit()
    track("settings.changed", section="costs")
    _flash_settings_notice("Costs settings saved.", "success")
    return redirect(url_for("settings_costs"))
