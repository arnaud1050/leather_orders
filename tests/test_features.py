"""
Per-company features and the /admin toggle that switches them.

Defends features/REQUIREMENTS.md (FE1–FE9) and admin/REQUIREMENTS.md PA8a.
"""

import ast
import pathlib

import pytest
from flask import render_template_string
from werkzeug.exceptions import NotFound

import features
from features.models import CompanyFeature
from models import db

ROOT = pathlib.Path(__file__).resolve().parent.parent


def keys_of(company_id):
    db.session.expire_all()
    return features.enabled_keys(company_id)


# --- FE1/FE2: the catalog, and unknown keys --------------------------------

def test_the_catalog_has_a_label_and_description_for_every_key():
    assert "showcase" in features.FEATURES
    for key, (label, description) in features.FEATURES.items():
        assert key == key.strip().lower() and len(key) <= 40
        assert label.strip() and description.strip()


def test_an_unknown_key_raises_rather_than_reading_as_off(company):
    """A typo like "showcse" must fail the first test that reaches it, not
    quietly hide a paid feature forever."""
    with pytest.raises(KeyError):
        features.is_enabled(company.id, "showcse")
    with pytest.raises(KeyError):
        features.set_enabled(company.id, "showcse", True)


# --- FE3/FE4: off by default, per company ----------------------------------

def test_a_company_starts_with_no_features(company):
    assert features.enabled_keys(company.id) == set()
    assert features.is_enabled(company.id, "showcase") is False


def test_no_company_means_no_features(app):
    """Staff have a null company_id. Null is "not a tenant", never "no
    filter" — hard rule 1 applied to features."""
    assert features.enabled_keys(None) == set()
    assert features.is_enabled(None, "showcase") is False


def test_switching_on_and_off(company):
    features.set_enabled(company.id, "showcase", True)
    db.session.commit()
    assert features.is_enabled(company.id, "showcase") is True
    row = CompanyFeature.query.filter_by(company_id=company.id).one()
    assert row.enabled_at is not None

    features.set_enabled(company.id, "showcase", False)
    db.session.commit()
    assert features.is_enabled(company.id, "showcase") is False
    assert CompanyFeature.query.count() == 0


def test_switching_is_idempotent(company):
    for _ in range(2):
        features.set_enabled(company.id, "showcase", True)
        db.session.commit()
    assert CompanyFeature.query.count() == 1
    for _ in range(2):
        features.set_enabled(company.id, "showcase", False)
        db.session.commit()
    assert CompanyFeature.query.count() == 0


def test_one_companys_feature_is_not_anothers(company, other_company):
    features.set_enabled(company.id, "showcase", True)
    db.session.commit()
    assert features.is_enabled(other_company.id, "showcase") is False
    assert features.enabled_by_company() == {company.id: {"showcase"}}


# --- FE6: a key that left the catalog reads as off ------------------------

def test_a_retired_key_is_kept_but_reads_as_off(company):
    db.session.add(CompanyFeature(
        company_id=company.id, feature_key="retired", enabled_at=features._utcnow(),
    ))
    db.session.commit()
    assert features.enabled_keys(company.id) == set()
    assert features.enabled_by_company() == {}
    assert CompanyFeature.query.count() == 1


# --- FE7: require() 404s without the feature ------------------------------

def test_require_404s_for_a_company_without_the_feature(app, company, user):
    from flask_login import login_user
    with app.test_request_context():
        login_user(user)
        with pytest.raises(NotFound):
            features.require("showcase")
        features.set_enabled(company.id, "showcase", True)
        db.session.commit()
        features.require("showcase")  # no exception


def test_require_404s_for_a_signed_out_visitor(app):
    with app.test_request_context():
        with pytest.raises(NotFound):
            features.require("showcase")


# --- FE8: has_feature() in templates --------------------------------------

def test_templates_can_ask_has_feature(app, company, user):
    from flask_login import login_user
    template = "{{ 'yes' if has_feature('showcase') else 'no' }}"
    with app.test_request_context():
        login_user(user)
        assert render_template_string(template) == "no"
        features.set_enabled(company.id, "showcase", True)
        db.session.commit()
        assert render_template_string(template) == "yes"


def test_has_feature_is_false_when_signed_out(app):
    with app.test_request_context():
        assert render_template_string(
            "{{ 'yes' if has_feature('showcase') else 'no' }}") == "no"


# --- FE9: the package's boundary ------------------------------------------

SIBLINGS = {"admin", "ai", "billing", "communications", "documents", "inventory"}


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                yield node.module or "", alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, ""


FEATURE_SOURCES = sorted((ROOT / "features").rglob("*.py"))


def test_there_are_feature_sources_to_check():
    assert len(FEATURE_SOURCES) >= 2


@pytest.mark.parametrize("path", FEATURE_SOURCES, ids=lambda p: p.name)
def test_features_imports_only_db_from_the_host(path):
    """Modules will import this package, so it must not drag a host model,
    the app or another module in behind it."""
    for module, name in _imports(path):
        root = module.split(".")[0]
        if module == "models":
            assert name == "db", f"{path.name} imports {name!r} from models"
        assert root != "app", f"{path.name} imports app.py"
        assert root not in SIBLINGS, f"{path.name} imports {root!r}"
        assert module != "usage.store", f"{path.name} imports usage.store"


# --- PA8a: the admin toggle ------------------------------------------------

def test_the_company_page_lists_every_catalog_feature(admin_client, company):
    db.session.commit()
    page = admin_client.get(f"/admin/companies/{company.id}").get_data(as_text=True)
    assert "<h2>Features</h2>" in page
    for key, (label, _) in features.FEATURES.items():
        assert f'name="feature" value="{key}"' in page
        assert label in page
    assert f'name="feature" value="showcase" checked' not in page


def test_ticking_a_feature_switches_it_on(admin_client, company):
    db.session.commit()
    url = f"/admin/companies/{company.id}"
    response = admin_client.post(f"{url}/features",
                                 data={"features_shown": "1", "feature": "showcase"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith(url)
    assert keys_of(company.id) == {"showcase"}
    page = admin_client.get(url).get_data(as_text=True)
    assert 'name="feature" value="showcase" checked' in page
    assert "Features saved." in page


def test_unticking_switches_it_off(admin_client, company):
    features.set_enabled(company.id, "showcase", True)
    db.session.commit()
    admin_client.post(f"/admin/companies/{company.id}/features",
                      data={"features_shown": "1"})
    assert keys_of(company.id) == set()


def test_a_post_without_the_marker_changes_nothing(admin_client, company):
    """Hard rule 9: an empty post is "leave alone", not "switch all off"."""
    features.set_enabled(company.id, "showcase", True)
    db.session.commit()
    admin_client.post(f"/admin/companies/{company.id}/features", data={})
    assert keys_of(company.id) == {"showcase"}


def test_an_unknown_feature_in_the_post_is_ignored(admin_client, company):
    db.session.commit()
    response = admin_client.post(
        f"/admin/companies/{company.id}/features",
        data={"features_shown": "1", "feature": ["showcase", "free-lunch"]},
    )
    assert response.status_code == 302
    assert keys_of(company.id) == {"showcase"}
    assert CompanyFeature.query.count() == 1


def test_the_toggle_touches_only_that_company(admin_client, company, other_company):
    features.set_enabled(other_company.id, "showcase", True)
    db.session.commit()
    admin_client.post(f"/admin/companies/{company.id}/features",
                      data={"features_shown": "1", "feature": "showcase"})
    admin_client.post(f"/admin/companies/{company.id}/features",
                      data={"features_shown": "1"})
    assert keys_of(company.id) == set()
    assert keys_of(other_company.id) == {"showcase"}


def test_an_unknown_company_404s(admin_client):
    response = admin_client.post("/admin/companies/9999/features",
                                 data={"features_shown": "1"})
    assert response.status_code == 404


def test_a_tenant_user_cannot_switch_features(logged_in, company):
    db.session.commit()
    response = logged_in.post(f"/admin/companies/{company.id}/features",
                              data={"features_shown": "1", "feature": "showcase"})
    assert response.status_code == 403
    assert keys_of(company.id) == set()


def test_the_roster_shows_each_companys_features(admin_client, company, other_company):
    features.set_enabled(company.id, "showcase", True)
    db.session.commit()
    page = admin_client.get("/admin/companies").get_data(as_text=True)
    mine = page.split(f'/admin/companies/{company.id}"', 1)[1].split("</li>", 1)[0]
    theirs = page.split(f'/admin/companies/{other_company.id}"', 1)[1].split("</li>", 1)[0]
    assert "· Showcase" in mine
    assert "Showcase" not in theirs


def test_deactivating_a_company_keeps_its_features(admin_client, company):
    """PA14: deactivation blocks sign-in and changes nothing else."""
    features.set_enabled(company.id, "showcase", True)
    db.session.commit()
    admin_client.post(f"/admin/companies/{company.id}/active", data={"active": "0"})
    assert keys_of(company.id) == {"showcase"}
