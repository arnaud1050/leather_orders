"""
The client page's Orders tab (/clients/<id>/orders) is a sortable, filterable
table styled like the main Orders list (/orders) — same `.invoice-table`,
same column-header sort links, same click-a-legend-item-to-hide status
filter — scoped to one client's own orders, plus columns that list doesn't
have (Start) and one it doesn't need company-wide (Invoice).

Each row's own status renders as the same labeled `.pill.pill--{status}` the
Orders page's Status column uses, not the bare, label-less `.dot--{status}`
marker — that marker still appears, but only in the filter legend above the
table (see test_orders_tab_status_dot_only_appears_in_the_legend), same as
on /orders itself.
"""

from datetime import date

from app import STATUS_LABELS
from models import Order, OrderLine, db


def _table_body(html: str) -> str:
    return html.split("<tbody", 1)[1].split("</tbody>", 1)[0]


def test_orders_tab_shows_the_labeled_status_pill(logged_in, client_record, order):
    html = logged_in.get(f"/clients/{client_record.id}/orders").get_data(as_text=True)

    assert f'pill pill--{order.status}' in html
    assert STATUS_LABELS[order.status] in html


def test_orders_tab_status_dot_only_appears_in_the_legend(logged_in, client_record, order):
    html = logged_in.get(f"/clients/{client_record.id}/orders").get_data(as_text=True)

    assert 'dot dot--' in html  # the filter legend still uses it
    assert 'dot dot--' not in _table_body(html)  # but never on a row


def test_orders_tab_is_a_table_with_the_expected_columns(logged_in, client_record, order):
    html = logged_in.get(f"/clients/{client_record.id}/orders").get_data(as_text=True)

    assert 'id="client-orders-table"' in html
    thead = html.split("<thead", 1)[1].split("</thead>", 1)[0]
    for label in ("Item", "Status", "Start", "Due", "Total", "Paid", "Balance", "Invoice"):
        assert label in thead


def test_orders_tab_shows_dash_for_an_uninvoiced_order(logged_in, client_record, order):
    html = logged_in.get(f"/clients/{client_record.id}/orders").get_data(as_text=True)

    assert order.invoice is None
    assert "&mdash;" in _table_body(html)


def test_orders_tab_sorts_by_the_requested_column(logged_in, client_record, order):
    earlier = Order(
        client_id=client_record.id, item="AAA earlier item",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="in_progress",
    )
    db.session.add(earlier)
    db.session.flush()
    db.session.add(OrderLine(order_id=earlier.id, description="AAA", quantity=1, unit_price=100.0))
    db.session.commit()

    html = logged_in.get(
        f"/clients/{client_record.id}/orders", query_string={"sort": "item", "dir": "asc"}
    ).get_data(as_text=True)

    assert html.index("AAA earlier item") < html.index(order.item)
