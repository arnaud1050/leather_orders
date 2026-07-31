"""
The billing module: numbering, and the snapshot that makes a reprint match
what the client was actually given.

The rule this file exists to defend: **once an invoice leaves draft,
nothing that happens afterwards may change what it says.** Seller settings,
the buyer's province, the subject's line items — all can move, and none of
them may move an issued invoice. The inverse matters too: a draft *should*
track them, because nobody has seen it yet.

Tests go through `billing.services.invoicing` rather than poking the models
directly — that's the module's public surface, and the surface is what a
second project would depend on.
"""

from datetime import date

import pytest
import sqlalchemy as sa

from billing.documents import Billable, LineItem, PartyDetails, PaymentRecord
from billing.models import Invoice, InvoiceTaxLine, next_invoice_number
from billing.services import invoicing
from billing_adapter import billable_for
from models import Client, Order, OrderLine, Payment, db

ALL_REGISTRATIONS = {
    "gst_number": "123456789 RT0001",
    "qst_number": "1234567890 TQ0001",
}


@pytest.fixture
def registered(company):
    invoicing.update_profile(
        company.id, company.name,
        street="4820 rue Sainte-Catherine E", city="Montréal",
        province="QC", postal_code="H1V 1M6",
        payment_instructions="E-transfer to pay@example.com",
        **ALL_REGISTRATIONS,
    )
    db.session.flush()
    return company


def make_order(client_row, unit_price=1000.0):
    row = Order(
        client_id=client_row.id, item="Briefcase",
        start=date(2026, 7, 1), due=date(2026, 7, 15), status="in_progress",
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(OrderLine(
        order_id=row.id, description="Briefcase", quantity=1, unit_price=unit_price,
    ))
    db.session.flush()
    return row


def issue(company_row, order_row, status="sent"):
    """Raise an invoice and take it out of draft, the way the app does."""
    billable = billable_for(order_row)
    invoice = invoicing.create_invoice(company_row.id, billable,
                                       display_name=company_row.name)
    invoicing.set_status(company_row.id, invoice, status, billable,
                         display_name=company_row.name)
    db.session.flush()
    return invoice


def draft(company_row, order_row):
    invoice = invoicing.create_invoice(
        company_row.id, billable_for(order_row), display_name=company_row.name)
    db.session.flush()
    return invoice


def doc_for(company_row, invoice):
    order = db.session.get(Order, invoice.subject_id)
    return invoicing.document_for(company_row.id, invoice, billable_for(order),
                                  company_row.name)


# --- Numbering ------------------------------------------------------------

def test_first_number_of_the_year(registered):
    assert invoicing.next_number(registered.id, today=date(2026, 3, 1)) == "BM-2026-0001"


def test_numbers_increment(registered, client_record):
    draft(registered, make_order(client_record))
    assert invoicing.next_number(registered.id, today=date(2026, 3, 1)) == "BM-2026-0002"


def test_the_sequence_restarts_each_year(registered, client_record):
    draft(registered, make_order(client_record)).number = "BM-2026-0009"
    db.session.flush()
    assert invoicing.next_number(registered.id, today=date(2027, 1, 2)) == "BM-2027-0001"


def test_a_voided_invoice_does_not_free_up_its_number(registered, client_record):
    """Voiding keeps the row, so the number stays spent. This is the
    supported way to cancel an invoice."""
    issue(registered, make_order(client_record), status="void")
    assert invoicing.next_number(registered.id, today=date(2026, 3, 1)) == "BM-2026-0002"


def test_deleting_an_older_invoice_leaves_a_gap_rather_than_reusing_it(
    registered, client_record
):
    first = draft(registered, make_order(client_record))
    draft(registered, make_order(client_record))
    db.session.delete(first)
    db.session.flush()
    assert invoicing.next_number(registered.id, today=date(2026, 3, 1)) == "BM-2026-0003"


def test_deleting_the_latest_invoice_DOES_free_its_number(registered, client_record):
    """KNOWN GAP, pinned deliberately so a change here is a decision.

    The next number is derived from the highest *existing* one, so deleting
    the most recent invoice hands its number to the next one issued. Fine
    for a draft nobody has seen, wrong the moment a document bearing that
    number has left the building. Fix (a per-tenant high-water mark) before
    exposing invoice deletion — see billing/models.py.
    """
    draft(registered, make_order(client_record))
    latest = draft(registered, make_order(client_record))
    assert latest.number == "BM-2026-0002"
    db.session.delete(latest)
    db.session.flush()
    assert invoicing.next_number(registered.id, today=date(2026, 3, 1)) == "BM-2026-0002"


def test_sequences_are_per_company(registered, other_company, client_record):
    draft(registered, make_order(client_record))
    assert invoicing.next_number(other_company.id, today=date(2026, 3, 1)) == "OS-2026-0001"


def test_numbers_stay_sortable_past_nine(registered, client_record):
    """Zero-padding is what makes `ORDER BY number DESC` equal numeric
    order — "10" would otherwise sort below "9"."""
    draft(registered, make_order(client_record)).number = "BM-2026-0009"
    db.session.flush()
    draft(registered, make_order(client_record)).number = "BM-2026-0010"
    db.session.flush()
    assert invoicing.next_number(registered.id, today=date(2026, 3, 1)) == "BM-2026-0011"


def test_the_same_number_twice_for_one_company_is_rejected(registered, client_record):
    """The unique constraint is the real guard against two concurrent
    requests issuing the same number."""
    draft(registered, make_order(client_record)).number = "BM-2026-0001"
    db.session.flush()
    second = draft(registered, make_order(client_record))
    second.number = "BM-2026-0001"
    with pytest.raises(sa.exc.IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_two_companies_may_hold_the_same_number(registered, other_company, client_record):
    outsider = Client(company_id=other_company.id, first_name="X", last_name="Y")
    db.session.add(outsider)
    db.session.flush()
    draft(registered, make_order(client_record)).number = "SAME-0001"
    draft(other_company, make_order(outsider)).number = "SAME-0001"
    db.session.commit()  # must not raise


def test_creating_twice_for_one_subject_returns_the_same_invoice(
    registered, client_record
):
    """A double-submitted button must not burn a second number."""
    order = make_order(client_record)
    first = draft(registered, order)
    again = invoicing.create_invoice(registered.id, billable_for(order),
                                     display_name=registered.name)
    assert again.id == first.id
    assert Invoice.query.count() == 1


def test_next_invoice_number_takes_the_prefix_it_is_given(app):
    """The low-level helper knows nothing about profiles — a host with its
    own numbering scheme calls this directly."""
    assert next_invoice_number(1, "ACME", date(2030, 6, 1)) == "ACME-2030-0001"


# --- Drafts track live settings ------------------------------------------

def test_a_draft_reads_seller_details_live(registered, client_record):
    invoice = draft(registered, make_order(client_record))
    invoicing.update_profile(registered.id, "Renamed Studio",
                             gst_number="999999999 RT0001")
    db.session.flush()
    issuer = doc_for(registered, invoice).issuer
    assert issuer.gst_number == "999999999 RT0001"


def test_a_draft_is_not_frozen(registered, client_record):
    assert not draft(registered, make_order(client_record)).is_frozen


def test_a_draft_subtotal_follows_its_line_items(registered, client_record):
    order = make_order(client_record, 100.0)
    invoice = draft(registered, order)
    db.session.add(OrderLine(order_id=order.id, description="Extra",
                             quantity=1, unit_price=50.0))
    db.session.flush()
    db.session.expire(order)  # the lines collection is already loaded
    assert doc_for(registered, invoice).subtotal == 150.0


# --- Freezing at issue ----------------------------------------------------

def test_issuing_stores_the_seller_details_and_the_money(registered, client_record):
    invoice = issue(registered, make_order(client_record, 1000.0))
    assert invoice.is_frozen
    assert invoice.issued_subtotal == 1000.0
    assert invoice.issuer_name == "By Monsieur"
    assert invoice.issuer_gst_number == "123456789 RT0001"
    assert invoice.issuer_address == "4820 rue Sainte-Catherine E\nMontréal, QC  H1V 1M6"
    assert [(r.label, r.amount) for r in invoice.tax_rows] == [("GST", 50.0), ("QST", 99.75)]


def test_an_issued_invoice_ignores_later_seller_changes(registered, client_record):
    invoice = issue(registered, make_order(client_record))
    invoicing.update_profile(registered.id, "Renamed Studio",
                             gst_number="999999999 RT0001", street="Somewhere else")
    db.session.flush()
    issuer = doc_for(registered, invoice).issuer
    assert issuer.name == "By Monsieur"
    assert issuer.gst_number == "123456789 RT0001"
    assert "Sainte-Catherine" in issuer.address


def test_an_issued_invoice_ignores_later_line_item_changes(registered, client_record):
    """The client has been given a number; adding work must not re-bill."""
    order = make_order(client_record, 1000.0)
    invoice = issue(registered, order)
    billed = doc_for(registered, invoice).total

    db.session.add(OrderLine(order_id=order.id, description="Added later",
                             quantity=1, unit_price=500.0))
    db.session.flush()
    db.session.expire(order)

    assert order.subtotal == 1500.0                       # the order changed
    assert doc_for(registered, invoice).total == pytest.approx(billed)
    assert order.total == pytest.approx(billed)           # and so does the host


def test_an_issued_invoice_ignores_the_buyer_moving_province(registered, client_record):
    order = make_order(client_record, 1000.0)              # client_record is QC
    invoice = issue(registered, order)
    client_record.province = "ON"
    db.session.flush()
    db.session.expire(order)
    assert [t.label for t in doc_for(registered, invoice).tax_lines] == ["GST", "QST"]


def test_an_uninvoiced_order_does_follow_a_province_change(registered, client_record):
    """The mirror: nothing is frozen until it's issued."""
    order = make_order(client_record, 1000.0)
    client_record.province = "ON"
    db.session.flush()
    assert [t.label for t in order.tax_lines] == ["HST"]


def test_issuing_with_no_taxable_province_stores_no_tax_rows(registered, company):
    row = Client(company_id=company.id, first_name="No", last_name="Province")
    db.session.add(row)
    db.session.flush()
    invoice = issue(registered, make_order(row, 100.0))
    assert invoice.tax_rows == []
    assert invoice.is_frozen          # frozen, just with nothing to charge
    assert doc_for(registered, invoice).total == 100.0


def test_resaving_an_issued_invoice_does_not_rewrite_history(registered, client_record):
    order = make_order(client_record)
    invoice = issue(registered, order)
    invoicing.update_profile(registered.id, "Renamed Studio",
                             gst_number="999999999 RT0001")
    db.session.flush()

    invoicing.set_status(registered.id, invoice, "sent", billable_for(order),
                         notes="edited later", display_name="Renamed Studio")
    db.session.flush()

    assert invoice.issuer_name == "By Monsieur"
    assert invoice.issuer_gst_number == "123456789 RT0001"
    assert invoice.notes == "edited later"


def test_an_unknown_status_is_ignored(registered, client_record):
    order = make_order(client_record)
    invoice = draft(registered, order)
    invoicing.set_status(registered.id, invoice, "nonsense", billable_for(order))
    assert invoice.status == "draft"
    assert not invoice.is_frozen


# --- Derived status -------------------------------------------------------

def pay(order, amount):
    db.session.add(Payment(order_id=order.id, amount=amount,
                           paid_date=date(2026, 7, 2), method="cash"))
    db.session.flush()


def test_display_status_is_paid_once_payments_cover_the_total(registered, client_record):
    order = make_order(client_record, 1000.0)
    invoice = issue(registered, order)
    assert doc_for(registered, invoice).display_status == "sent"
    pay(order, doc_for(registered, invoice).total)
    db.session.expire(order)
    assert doc_for(registered, invoice).display_status == "paid"


def test_a_deposit_does_not_make_it_paid(registered, client_record):
    order = make_order(client_record, 1000.0)
    invoice = issue(registered, order)
    pay(order, 500.0)
    db.session.expire(order)
    document = doc_for(registered, invoice)
    assert document.display_status == "sent"
    assert not document.is_settled


def test_paying_the_pre_tax_amount_does_not_make_it_paid(registered, client_record):
    """The tax-inclusive total is what has to be covered."""
    order = make_order(client_record, 1000.0)
    invoice = issue(registered, order)
    pay(order, 1000.0)
    db.session.expire(order)
    assert doc_for(registered, invoice).display_status == "sent"


def test_void_wins_over_paid(registered, client_record):
    order = make_order(client_record, 1000.0)
    invoice = issue(registered, order, status="void")
    pay(order, doc_for(registered, invoice).total)
    db.session.expire(order)
    assert doc_for(registered, invoice).display_status == "void"


def test_a_cent_of_rounding_does_not_leave_it_unpaid(registered, client_record):
    order = make_order(client_record, 1000.0)
    invoice = issue(registered, order)
    pay(order, doc_for(registered, invoice).total - 0.004)
    db.session.expire(order)
    assert doc_for(registered, invoice).display_status == "paid"


def test_a_zero_value_order_is_not_reported_as_paid(registered, client_record):
    """Nothing has been collected, so "Paid" would be a lie."""
    order = Order(client_id=client_record.id, item="Empty",
                  start=date(2026, 7, 1), due=date(2026, 7, 2), status="in_progress")
    db.session.add(order)
    db.session.flush()
    invoice = issue(registered, order)
    assert doc_for(registered, invoice).display_status == "sent"


# --- Tenant isolation -----------------------------------------------------

def test_get_invoice_is_scoped_to_the_tenant(registered, other_company, client_record):
    invoice = draft(registered, make_order(client_record))
    assert invoicing.get_invoice(registered.id, invoice.id) is not None
    assert invoicing.get_invoice(other_company.id, invoice.id) is None


def test_listing_is_scoped_to_the_tenant(registered, other_company, client_record):
    draft(registered, make_order(client_record))
    assert len(invoicing.list_invoices(registered.id)) == 1
    assert invoicing.list_invoices(other_company.id) == []


def test_profiles_are_per_tenant(registered, other_company):
    assert invoicing.profile_for(registered.id).invoice_prefix == "BM"
    assert invoicing.profile_for(other_company.id).invoice_prefix == "OS"


# --- Reporting ------------------------------------------------------------

def test_tax_collected_sums_the_frozen_rows(registered, client_record):
    """The reason InvoiceTaxLine is a table and not a JSON blob — a GST
    remittance is a SUM over a period."""
    for _ in range(3):
        issue(registered, make_order(client_record, 1000.0))
    collected = dict(invoicing.tax_collected(registered.id))
    assert collected["GST"] == pytest.approx(150.0)
    assert collected["QST"] == pytest.approx(299.25)


def test_tax_collected_excludes_voided_invoices(registered, client_record):
    issue(registered, make_order(client_record, 1000.0))
    issue(registered, make_order(client_record, 1000.0), status="void")
    assert dict(invoicing.tax_collected(registered.id))["GST"] == pytest.approx(50.0)


def test_tax_collected_is_scoped_to_the_tenant(registered, other_company, client_record):
    issue(registered, make_order(client_record, 1000.0))
    assert invoicing.tax_collected(other_company.id) == []


def test_invoiced_subject_ids(registered, client_record):
    order = make_order(client_record)
    make_order(client_record)  # left uninvoiced
    draft(registered, order)
    assert invoicing.invoiced_subject_ids(registered.id) == {order.id}


# --- The adapter boundary -------------------------------------------------

def test_the_module_never_needs_a_host_model(registered):
    """A Billable built by hand — no Order, no Client — must work, because
    that's what porting the module to another project looks like."""
    billable = Billable(
        subject_id=999,
        description="Consulting, June",
        payer=PartyDetails(name="Someone Else", address="1 Rue X\nMontréal, QC"),
        tax_province="QC",
        lines=[LineItem("Consulting", 10, 100.0)],
        payments=[PaymentRecord(500.0, date(2026, 7, 1), "etransfer", "ref-1")],
    )
    issuer = invoicing.profile_for(registered.id, registered.name).issuer
    amounts = invoicing.amounts_for(billable, issuer)
    assert amounts.subtotal == 1000.0
    assert [t.label for t in amounts.tax_lines] == ["GST", "QST"]
    assert amounts.total == pytest.approx(1149.75)
    assert amounts.balance_due == pytest.approx(649.75)
