"""
The communications module boundary, enforced rather than intended.

`communications/` is meant to be liftable into another project, the same
claim `billing/` makes and `tests/test_billing_boundary.py` defends. This
is the equivalent for this module — the test its own REQUIREMENTS names as
the most valuable one missing (§12: "No test asserts the module never
imports `Order`"; P-2: the vendor branch belongs in the registry alone).

The allowances are wider than billing's, and deliberately enumerated:
- `db` is the shared SQLAlchemy handle, a connection rather than a domain
  concept.
- `Client`, `SourceOption`, `DEFAULT_TIMEZONE` — converting an enquiry
  fills in *client* details, so field mapping reads exactly those. One
  model wider than billing, no wider. Anything about an **order** must
  arrive through a host-registered hook, never an import (§12) — which is
  exactly the temptation the order-from-enquiry work will bring.
"""

import ast
import pathlib

import pytest

COMMS = pathlib.Path(__file__).resolve().parent.parent / "communications"

# The only names allowed to cross from the host's models.py into this module.
SANCTIONED_MODEL_IMPORTS = {"db", "Client", "SourceOption", "DEFAULT_TIMEZONE"}

# Order/billing domain models. Importing any of these — by whatever path —
# is the specific coupling §12 exists to prevent.
FORBIDDEN_HOST_MODELS = {
    "Order", "OrderLine", "OrderType", "Payment",
    "Invoice", "InvoiceTaxLine", "BillingProfile", "Document",
}

# The module reaches back into app.py for exactly two host helpers, from
# inside a function to dodge a circular import (routes.py). Pinned so the
# back-reference can't quietly widen: pulling, say, an order helper out of
# app.py is how §12 would be circumvented without ever touching
# `from models import`. Unlike billing/, which takes these as registered
# hooks, communications imports them directly — a known, bounded exception.
SANCTIONED_APP_IMPORTS = {"back_label", "get_client_or_404"}


def comms_sources():
    return sorted(COMMS.rglob("*.py"))


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


def _branches_on_provider(path):
    """True if the file compares a `.provider` (or bare `provider`) against
    something — the vendor `if provider == "gmail"` seam."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            left = node.left
            if isinstance(left, ast.Attribute) and left.attr == "provider":
                return True
            if isinstance(left, ast.Name) and left.id == "provider":
                return True
    return False


def test_there_are_comms_sources_to_check():
    """A glob that silently matched nothing would make every test below pass
    for the wrong reason."""
    assert len(comms_sources()) >= 10


@pytest.mark.parametrize("path", comms_sources(), ids=lambda p: p.name)
def test_comms_never_imports_a_forbidden_host_model(path):
    for module, name in imports_in(path):
        if module == "models":
            assert name in SANCTIONED_MODEL_IMPORTS, (
                f"{path.name} imports {name!r} from the host's models. Only "
                f"{sorted(SANCTIONED_MODEL_IMPORTS)} may cross that line — an "
                "order must arrive through a host-registered hook, not an "
                "import (REQUIREMENTS §12)."
            )
        assert name not in FORBIDDEN_HOST_MODELS, (
            f"{path.name} imports host model {name!r} — see REQUIREMENTS §12."
        )


@pytest.mark.parametrize("path", comms_sources(), ids=lambda p: p.name)
def test_comms_only_reaches_back_into_app_for_sanctioned_helpers(path):
    """The module is registered by app.py (routes.register(app)). It does
    reach back for two host helpers — that's allowed, but bounded: anything
    beyond `back_label` / `get_client_or_404` is a new coupling to notice."""
    for module, name in imports_in(path):
        if module == "app" or module.startswith("app."):
            assert name in SANCTIONED_APP_IMPORTS, (
                f"{path.name} imports {name!r} from app.py. The only sanctioned "
                f"back-references are {sorted(SANCTIONED_APP_IMPORTS)} (routes.py, "
                "deferred to dodge a circular import); anything more is the "
                "coupling the module layout exists to avoid."
            )


def test_the_provider_vendor_branch_lives_only_in_the_registry():
    """P-1/P-2: `registry.py` is the single place an `if provider == "gmail"`
    may live, so adding a second provider is one seam to change, not five."""
    branching = [p.name for p in comms_sources() if _branches_on_provider(p)]
    assert branching == ["registry.py"], (
        f"A vendor branch on `.provider` appears in {branching}; it belongs "
        "in registry.py alone. Register the provider there rather than "
        "special-casing it here (P-2)."
    )
