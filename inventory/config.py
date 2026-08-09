"""
The unit vocabulary for an InventoryItem — a fixed catalog, not free text, so
the quantity input on the Materials tab knows what step/precision to offer
(whole zippers vs. fractional square feet of leather) without guessing from a
string someone typed.

UNIT_CATALOG is deliberately broad: this app started as a leatherworker's
tool, but the unit a maker measures stock in is one of the least
leather-specific things about the whole model — a ceramist counts glaze by
the pound and clay by the bag, a woodworker buys lumber by the board foot,
a jeweler weighs metal in grams, a fiber artist counts yarn in skeins. The
catalog covers count, length, area, weight and volume so any small maker's
business has something that fits, without inventing a "just type it" escape
hatch that would break the Materials tab's whole/divisible step logic (see
`whole` below).

**What's fixed vs. what's configurable:** this catalog — the full set of
keys, labels and whether each is whole or divisible — never changes per
company. What *is* company-configurable is which of these keys are offered
in the Add/Edit item dropdown, and in what order — see InventoryUnit in
inventory/models.py and the "Units" section on /settings/inventory. A unit
already in use by an `InventoryItem` stays valid indefinitely regardless of
a company's current selection (see `add_item`/`edit_item` in
inventory/services.py) — the per-company list controls what's *offered*,
never what's *accepted*, the same way a hidden InventoryType still labels
the items already tagged with it correctly.
"""

# key -> {label, group, whole}. `group` only affects how the "Add a unit"
# dropdown on /settings/inventory is organized (<optgroup>s); it has no
# other meaning. `whole` decides the Materials tab's quantity step (1 for a
# whole count like "Each" or "Skein", 0.01 for anything divisible like
# "Sq ft" or "Yard") — see inventory/templates/inventory/_order_materials.html.
UNIT_CATALOG = {
    # Count — one of the actual pieces, package, or bundle.
    "each":      {"label": "Each",        "group": "Count",  "whole": True},
    "pair":      {"label": "Pair",        "group": "Count",  "whole": True},
    "set":       {"label": "Set",         "group": "Count",  "whole": True},
    "dozen":     {"label": "Dozen",       "group": "Count",  "whole": True},
    "sheet":     {"label": "Sheet",       "group": "Count",  "whole": True},
    "panel":     {"label": "Panel",       "group": "Count",  "whole": True},
    "skein":     {"label": "Skein",       "group": "Count",  "whole": True},
    "spool":     {"label": "Spool",       "group": "Count",  "whole": True},
    "roll":      {"label": "Roll",        "group": "Count",  "whole": True},
    "bolt":      {"label": "Bolt",        "group": "Count",  "whole": True},
    "box":       {"label": "Box",         "group": "Count",  "whole": True},
    "bag":       {"label": "Bag",         "group": "Count",  "whole": True},
    "tube":      {"label": "Tube",        "group": "Count",  "whole": True},
    "bottle":    {"label": "Bottle",      "group": "Count",  "whole": True},
    "jar":       {"label": "Jar",         "group": "Count",  "whole": True},
    "hide":      {"label": "Hide",        "group": "Count",  "whole": True},
    "bar":       {"label": "Bar",         "group": "Count",  "whole": True},
    "ingot":     {"label": "Ingot",       "group": "Count",  "whole": True},

    # Length — trim, lumber, wire, cord.
    "inch":      {"label": "Inch",        "group": "Length", "whole": False},
    "foot":      {"label": "Foot",        "group": "Length", "whole": False},
    "linear_ft": {"label": "Linear ft",   "group": "Length", "whole": False},
    "board_ft":  {"label": "Board ft",    "group": "Length", "whole": False},
    "yard":      {"label": "Yard",        "group": "Length", "whole": False},
    "meter":     {"label": "Meter",       "group": "Length", "whole": False},
    "cm":        {"label": "Centimeter",  "group": "Length", "whole": False},
    "mm":        {"label": "Millimeter",  "group": "Length", "whole": False},

    # Area — leather, fabric, sheet stock.
    "sqft":      {"label": "Sqft",        "group": "Area",   "whole": False},
    "sqin":      {"label": "Sq in",       "group": "Area",   "whole": False},
    "sqm":       {"label": "Sq m",        "group": "Area",   "whole": False},
    "sqyd":      {"label": "Sq yd",       "group": "Area",   "whole": False},

    # Weight — clay, metal, wax, fiber.
    "gram":      {"label": "Gram",        "group": "Weight", "whole": False},
    "kg":        {"label": "Kilogram",    "group": "Weight", "whole": False},
    "oz":        {"label": "Ounce",       "group": "Weight", "whole": False},
    "lb":        {"label": "Pound",       "group": "Weight", "whole": False},

    # Volume — glaze, resin, dye, wax.
    "ml":        {"label": "Milliliter",  "group": "Volume", "whole": False},
    "liter":     {"label": "Liter",       "group": "Volume", "whole": False},
    "fl_oz":     {"label": "Fluid ounce", "group": "Volume", "whole": False},
    "gallon":    {"label": "Gallon",      "group": "Volume", "whole": False},
    "cup":       {"label": "Cup",         "group": "Volume", "whole": False},
}

# The one unit every company always has, seeded lazily (see
# inventory/services.py's _ensure_default_unit) and exempt from hide/delete —
# items already on file may carry unit="each", and it's the guaranteed
# fallback the rest of the app assumes exists.
DEFAULT_UNIT = "each"

# key -> label for every catalog entry, regardless of a company's current
# selection — used anywhere a unit needs to render as text (an item's row on
# /inventory, a material's line on the Materials tab), since an item created
# while a unit was offered keeps that unit even after a company later hides
# or removes it from their list.
UNIT_LABELS = {key: info["label"] for key, info in UNIT_CATALOG.items()}

# key -> whole, for the Materials tab's quantity-step logic (see
# inventory/templates/inventory/_order_materials.html) — generalizes what
# used to be a literal `unit === 'sqft'` check in that template's JS to any
# unit a company enables, whole or divisible.
UNIT_WHOLE = {key: info["whole"] for key, info in UNIT_CATALOG.items()}


# ---------------------------------------------------------------------------
# The /inventory master-list columns.
#
# Same role as app.py's ORDER_COLUMNS, and deliberately the same shape: this
# dict is both the fallback order for a company that's never reordered
# anything and the whitelist a saved layout is filtered against, so a column
# added here later appears (visible, appended) for a company with an older
# saved layout, and one removed here silently drops out of theirs instead of
# erroring. Declaration order *is* the default column order.
#
# `sort` names the INVENTORY_SORT_KEYS entry a column's header links to (see
# inventory/routes.py), or None for a column that isn't sortable — the same
# split the page already had by hand, where Type/Name/Unit price carried a
# sort link and Unit/Quantity didn't.
# ---------------------------------------------------------------------------
INVENTORY_COLUMNS = {
    "type":       {"label": "Type",             "numeric": False, "sort": "type"},
    "name":       {"label": "Name",             "numeric": False, "sort": "name"},
    "reference":  {"label": "Ref",              "numeric": False, "sort": None},
    "unit":       {"label": "Unit",             "numeric": False, "sort": None},
    "quantity":   {"label": "Quantity on hand", "numeric": True,  "sort": None},
    "unit_price": {"label": "Unit price",       "numeric": True,  "sort": "price"},
    "notes":      {"label": "Notes",            "numeric": False, "sort": None},
    "url":        {"label": "Link",             "numeric": False, "sort": None},
}

# Columns a company can reorder but not hide. The item's name is the row's
# only handle on its edit modal (clicking it is what opens the thing), so
# hiding it would leave a table nothing could be edited from — unlike
# /orders, where every row still links out from its own Item cell *and* the
# order page is reachable from the timeline. Reordering it is fine.
INVENTORY_REQUIRED_COLUMNS = frozenset({"name"})
