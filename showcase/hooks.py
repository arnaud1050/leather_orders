"""
What the module knows about orders, all of it handed in by the host.

`showcase_adapter.py` (host side) is the only file that knows a showcase
item can come from an `Order`. `routes.register()` stores its functions
here, and `services` calls them, so neither ever imports a host model.
Every hook takes `company_id` first and answers only for that company.

- `delivered_orders(company_id) -> list[dict]` — every delivered order, as
  the summary below.
- `order_summary(company_id, order_id) -> dict | None` — `{"id", "item",
  "client_name", "status", "delivered" (bool), "delivered_on" (a date: the
  pickup date, or the due date when none was recorded), "order_type_id",
  "order_type_label", "showcase_url" (the order's Showcase tab)}`; None
  when the order isn't this company's.
- `order_images(company_id, order_id) -> list[dict]` — the order's JPEG/PNG
  documents: `{"id", "filename", "preview_url"}`.
- `load_order_image(company_id, order_id, document_id) -> dict | None` —
  `{"filename", "content_type", "data"}`, tenant-checked.
- `order_types(company_id) -> list[dict]` — `{"id", "label", "is_active"}`.
- `company_active(company_id) -> bool` — False once the company is
  deactivated; its kiosk links stop working with it.
- `studio_brand(company_id) -> dict` — `{"name", "logo_path",
  "logo_tone"}`: the studio's name, the path of its logo PNG on disk (or
  None), and whether that logo is "light", "dark" or "opaque", for
  catalog mode's dark page (SC26).

Unwired (a test app, a deployment without orders), each answers as if
there were no orders at all rather than failing.
"""

_hooks: dict = {}


def configure(**hooks) -> None:
    _hooks.clear()
    _hooks.update({name: fn for name, fn in hooks.items() if fn is not None})


def delivered_orders(company_id: int) -> list[dict]:
    fn = _hooks.get("delivered_orders")
    return fn(company_id) if fn else []


def order_summary(company_id: int, order_id: int) -> dict | None:
    fn = _hooks.get("order_summary")
    return fn(company_id, order_id) if fn else None


def order_images(company_id: int, order_id: int) -> list[dict]:
    fn = _hooks.get("order_images")
    return fn(company_id, order_id) if fn else []


def load_order_image(company_id: int, order_id: int, document_id: int) -> dict | None:
    fn = _hooks.get("load_order_image")
    return fn(company_id, order_id, document_id) if fn else None


def order_types(company_id: int) -> list[dict]:
    fn = _hooks.get("order_types")
    return fn(company_id) if fn else []


def company_active(company_id: int) -> bool:
    """False once the company is deactivated: its kiosk links stop too."""
    fn = _hooks.get("company_active")
    return fn(company_id) if fn else True


def studio_brand(company_id: int) -> dict:
    fn = _hooks.get("studio_brand")
    return fn(company_id) if fn else {"name": "", "logo_path": None, "logo_tone": None}
