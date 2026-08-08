"""
The AI module's boundary, enforced rather than intended.

`ai/` claims the strictest boundary in this codebase — stricter than
`documents/` or `inventory/`, which both hold a real `order_id` foreign key.
The claim is that it never sees a host model at all: an order, a document
and a mail thread each reach it as a plain dict through a hook the host
registers. That claim decays the moment someone adds `from models import
Order` to make one thing easier, and nothing else in the suite would notice
— so these tests read the source and fail on it.

Same shape as `test_billing_boundary.py`, with one extra allowance: the
host's `crypto.py`. It's a dependency-free file (stdlib plus `cryptography`)
that knows nothing about models, tenants or Flask, and both this module and
`communications/` name their own env var and salt over it. Importing it
doesn't drag the app in behind it — which is exactly the test below.
"""

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
AI = ROOT / "ai"

# Domain models that live in the host application. Importing any of these is
# what would tie the module to this particular project.
HOST_MODELS = {
    "Client", "Company", "Document", "Order", "OrderLine", "OrderType",
    "Payment", "SourceOption", "User", "seed_if_empty", "run_migrations",
}

# The other self-contained modules. `ai/` is wired to three host concepts;
# reaching into any one of their modules directly would tie it to all of
# them at once, which is what the hook indirection exists to prevent.
SIBLING_MODULES = {"billing", "communications", "documents", "inventory"}


def ai_sources():
    return sorted(AI.rglob("*.py"))


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


def test_there_are_ai_sources_to_check():
    """A glob that silently matches nothing would make every test below
    pass for the wrong reason."""
    assert len(ai_sources()) >= 6


@pytest.mark.parametrize("path", ai_sources(), ids=lambda p: p.name)
def test_ai_never_imports_a_host_model(path):
    for module, name in imports_in(path):
        if module == "models":
            assert name == "db", (
                f"{path.name} imports {name!r} from the host's models. Only the "
                "shared `db` handle may cross that line — everything else "
                "arrives through a host-registered hook."
            )
        assert name not in HOST_MODELS, f"{path.name} imports host model {name!r}"


@pytest.mark.parametrize("path", ai_sources(), ids=lambda p: p.name)
def test_ai_never_imports_the_host_or_a_sibling_module(path):
    for module, _ in imports_in(path):
        root = module.split(".")[0]
        assert root != "app", f"{path.name} imports app.py"
        assert root not in SIBLING_MODULES, (
            f"{path.name} imports {root!r}. What it needs from another module "
            "arrives as a dict through a hook registered in app.py."
        )


def test_the_only_host_helper_is_crypto():
    """One host import that isn't `db`, and it's the shared SecretBox."""
    allowed = {"", "ai", "models", "crypto", "flask", "flask_login"}
    for path in ai_sources():
        for module, _ in imports_in(path):
            root = module.split(".")[0]
            if root in {"os", "base64", "hashlib", "logging", "datetime", "dataclasses"}:
                continue
            if root == "sqlalchemy":
                continue  # migrations.py, same as every other module's
            assert root in allowed, f"{path.name} imports {module!r}"


def test_the_shared_crypto_helper_drags_nothing_in_behind_it():
    """The reason `ai/` and `communications/` are allowed to import it: it
    depends on the standard library and `cryptography`, nothing else. A
    Flask or models import here would make it a host dependency in
    disguise."""
    for module, _ in imports_in(ROOT / "crypto.py"):
        root = module.split(".")[0]
        assert root in {"base64", "hashlib", "os", "cryptography", ""}, module


def test_services_are_the_public_surface():
    """Every service takes company_id first, so a tenant filter can't be
    forgotten by a caller."""
    import inspect

    from ai import services

    scoped = [
        services.settings_for, services.reply_available, services.render_available,
        services.can_render_document, services.save_reply_settings,
        services.save_render_settings, services.clear_text_key,
        services.clear_image_key,
    ]
    for function in scoped:
        first = list(inspect.signature(function).parameters)[0]
        assert first == "company_id", f"{function.__name__} takes {first!r} first"
