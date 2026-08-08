"""
Analytics section/card order — dragged in place on /analytics itself (see
ANALYTICS_SECTIONS / _analytics_layout_for / _save_analytics_layout in app.py).

Persistence is the Orders-columns pattern verbatim (tests/test_order_columns.py):
one JSON blob on Company, merged against a canonical dict on every read, saved
by a JSON POST fired on drop. What's different, and what most of this file
defends, is the nesting — cards live inside sections, and a card must never be
able to leave the section it belongs to. A card under the wrong heading would
be a mislabelled stat, not a preference, so the route re-checks that
server-side rather than trusting a payload the browser could have hand-crafted.
"""

import json

from models import Company, db

REORDER = "/analytics/layout/reorder"

DEFAULT_SECTIONS = [
    {"key": "clients", "cards": ["avg_value", "top_clients", "sources"]},
    {"key": "revenue", "cards": ["total_revenue", "revenue_ytd", "outstanding",
                                 "by_method", "tax_ytd"]},
]


def post_layout(logged_in, sections):
    return logged_in.post(
        REORDER, data=json.dumps({"sections": sections}),
        content_type="application/json",
    )


def saved_layout(company):
    return json.loads(db.session.get(Company, company.id).analytics_layout)


def section_positions(html, *keys):
    return [html.index(f'data-section="{key}"') for key in keys]


def card_positions(html, *keys):
    return [html.index(f'data-card="{key}"') for key in keys]


def test_analytics_renders_clients_before_revenue_by_default(logged_in):
    html = logged_in.get("/analytics").data.decode()

    clients, revenue = section_positions(html, "clients", "revenue")
    assert clients < revenue


def test_default_page_stores_nothing_until_something_is_dragged(logged_in, company):
    logged_in.get("/analytics")

    assert db.session.get(Company, company.id).analytics_layout is None


def test_reordering_sections_persists_and_reflects_on_the_page(logged_in, company):
    post_layout(logged_in, list(reversed(DEFAULT_SECTIONS)))

    assert [s["key"] for s in saved_layout(company)] == ["revenue", "clients"]
    html = logged_in.get("/analytics").data.decode()
    revenue, clients = section_positions(html, "revenue", "clients")
    assert revenue < clients


def test_reordering_cards_within_a_section_persists_and_reflects(logged_in, company):
    post_layout(logged_in, [
        {"key": "clients", "cards": ["sources", "top_clients", "avg_value"]},
        DEFAULT_SECTIONS[1],
    ])

    clients = next(s for s in saved_layout(company) if s["key"] == "clients")
    assert clients["cards"] == ["sources", "top_clients", "avg_value"]
    html = logged_in.get("/analytics").data.decode()
    sources, top, avg = card_positions(html, "sources", "top_clients", "avg_value")
    assert sources < top < avg


def test_a_card_cannot_be_moved_into_another_section(logged_in, company):
    """AN11 — the drag UI refuses it, and so does the route."""
    post_layout(logged_in, [
        {"key": "clients", "cards": ["avg_value", "top_clients", "sources", "tax_ytd"]},
        {"key": "revenue", "cards": ["total_revenue", "revenue_ytd", "outstanding",
                                     "by_method"]},
    ])

    assert "tax_ytd" not in {s["key"]: s["cards"] for s in saved_layout(company)}["clients"]
    # Rejected from Clients, and not lost either — the read-time merge puts it
    # back at the end of the section it actually belongs to.
    html = logged_in.get("/analytics").data.decode()
    revenue, tax = section_positions(html, "revenue"), card_positions(html, "tax_ytd")
    assert revenue[0] < tax[0]
    assert card_positions(html, "by_method")[0] < tax[0]


def test_unknown_section_and_card_keys_are_ignored(logged_in, company):
    post_layout(logged_in, [
        {"key": "bogus", "cards": ["whatever"]},
        {"key": "revenue", "cards": ["tax_ytd", "nonsense", "total_revenue"]},
        DEFAULT_SECTIONS[0],
    ])

    layout = {s["key"]: s["cards"] for s in saved_layout(company)}
    assert set(layout) == {"clients", "revenue"}
    assert "nonsense" not in layout["revenue"]
    assert layout["revenue"][:2] == ["tax_ytd", "total_revenue"]


def test_a_card_the_client_omitted_is_appended_not_dropped(logged_in, company):
    post_layout(logged_in, [{"key": "revenue", "cards": ["tax_ytd"]}])

    html = logged_in.get("/analytics").data.decode()
    tax, total = card_positions(html, "tax_ytd", "total_revenue")
    assert tax < total
    # All five revenue cards still render, in one section.
    for key in ("total_revenue", "revenue_ytd", "outstanding", "by_method", "tax_ytd"):
        assert f'data-card="{key}"' in html


def test_a_section_the_client_omitted_is_appended_not_dropped(logged_in, company):
    post_layout(logged_in, [{"key": "revenue", "cards": ["total_revenue"]}])

    assert [s["key"] for s in saved_layout(company)] == ["revenue"]
    html = logged_in.get("/analytics").data.decode()
    revenue, clients = section_positions(html, "revenue", "clients")
    assert revenue < clients


def test_a_duplicated_card_key_is_only_kept_once(logged_in, company):
    post_layout(logged_in, [
        {"key": "clients", "cards": ["sources", "sources", "avg_value"]},
    ])

    html = logged_in.get("/analytics").data.decode()
    assert html.count('data-card="sources"') == 1


def test_empty_payload_is_a_no_op(logged_in, company):
    post_layout(logged_in, [])

    assert db.session.get(Company, company.id).analytics_layout is None


def test_unparseable_stored_layout_falls_back_to_the_canonical_order(logged_in, company):
    db.session.get(Company, company.id).analytics_layout = "not json"
    db.session.commit()

    html = logged_in.get("/analytics").data.decode()

    clients, revenue = section_positions(html, "clients", "revenue")
    assert clients < revenue


def test_layout_is_scoped_per_company(logged_in, company, other_company):
    post_layout(logged_in, list(reversed(DEFAULT_SECTIONS)))

    assert db.session.get(Company, company.id).analytics_layout is not None
    assert db.session.get(Company, other_company.id).analytics_layout is None


def test_reorder_requires_a_login(app, company):
    response = app.test_client().post(
        REORDER, data=json.dumps({"sections": DEFAULT_SECTIONS}),
        content_type="application/json",
    )

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert db.session.get(Company, company.id).analytics_layout is None
