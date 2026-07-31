"""
The module boundary, enforced rather than intended.

`billing/` is meant to be liftable into another project. That claim decays
the moment someone adds `from models import Order` to make one thing
easier, and nothing else in the suite would notice — so these tests read
the source and fail on it.

The allowances are deliberate and small:
- `from models import db` is the shared SQLAlchemy handle, a connection
  rather than a domain concept. `communications/` takes the same one.
- `billing/tax.py` is allowed nothing at all, because a pure rate table is
  the piece most likely to be reused verbatim.
"""

import ast
import pathlib

import pytest

BILLING = pathlib.Path(__file__).resolve().parent.parent / "billing"

# Domain models that live in the host application. Importing any of these
# is what would tie the module to this particular project.
HOST_MODELS = {
    "Client", "Company", "Document", "Order", "OrderLine", "OrderType",
    "Payment", "SourceOption", "User", "seed_if_empty", "run_migrations",
}


def billing_sources():
    return sorted(BILLING.rglob("*.py"))


def imports_in(path):
    """(module, name) for every import in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                yield node.module or "", alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, ""


def test_there_are_billing_sources_to_check():
    """A glob that silently matches nothing would make every test below
    pass for the wrong reason."""
    assert len(billing_sources()) >= 6


@pytest.mark.parametrize("path", billing_sources(), ids=lambda p: p.name)
def test_billing_never_imports_a_host_model(path):
    for module, name in imports_in(path):
        if module == "models":
            assert name == "db", (
                f"{path.name} imports {name!r} from the host's models. Only the "
                "shared `db` handle may cross that line — everything else "
                "arrives as a Billable (see billing/documents.py)."
            )
        assert name not in HOST_MODELS, f"{path.name} imports host model {name!r}"


@pytest.mark.parametrize("path", billing_sources(), ids=lambda p: p.name)
def test_billing_never_imports_the_host_adapter(path):
    """billing_adapter.py knows about both sides; it must depend on the
    module, never the other way round."""
    for module, _ in imports_in(path):
        assert not module.startswith("billing_adapter"), path.name
        assert module != "app", path.name


def test_the_tax_engine_imports_nothing_but_the_standard_library():
    """The most reusable piece of all — plain data and one pure function.
    A Flask or SQLAlchemy import here would make it unliftable."""
    for module, _ in imports_in(BILLING / "tax.py"):
        assert module.split(".")[0] in {"collections", "dataclasses", ""}, module


def test_the_document_dataclasses_touch_no_database():
    for module, _ in imports_in(BILLING / "documents.py"):
        root = module.split(".")[0]
        assert root in {"dataclasses", "datetime", "billing", ""}, module


def test_the_subject_foreign_key_is_configurable():
    """The one place billing names a host table. Porting to a project that
    invoices something else is meant to be this line, not a rewrite."""
    from billing import config

    assert config.SUBJECT_FK == "orders.id"
    source = (BILLING / "models.py").read_text(encoding="utf-8")
    assert "config.SUBJECT_FK" in source
    assert '"orders.id"' not in source, "models.py hardcodes the host's table"


def test_services_are_the_public_surface():
    """Every service takes company_id first, so a tenant filter can't be
    forgotten by a caller."""
    import inspect

    from billing.services import invoicing

    scoped = [
        invoicing.profile_for, invoicing.update_profile, invoicing.next_number,
        invoicing.get_invoice, invoicing.list_invoices, invoicing.create_invoice,
        invoicing.invoice_for_subject, invoicing.invoiced_subject_ids,
        invoicing.tax_collected, invoicing.documents_for,
    ]
    for function in scoped:
        first = list(inspect.signature(function).parameters)[0]
        assert first == "company_id", f"{function.__name__} takes {first!r} first"
