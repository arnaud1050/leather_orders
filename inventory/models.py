"""
Tables: `inventory_types`, `inventory_items`, `order_materials`,
`order_material_others`.

`OrderMaterial`/`OrderMaterialOther` carry a plain `order_id` foreign key into
this app's `orders` table, same as `documents.Document.order_id` — there's no
circular import forcing the adapter-indirection `billing/` needs, so a real
FK is fine here. `company_id` is denormalized onto both (and onto
`InventoryItem`), same reasoning as `Document.company_id`: every query
filters by company first, and a tenant filter that depends on remembering to
join through order -> client -> company is one that eventually gets skipped.
"""

from models import db

from inventory.config import DEFAULT_UNIT, UNIT_CATALOG


class InventoryUnit(db.Model):
    """A company-configurable measurement unit for InventoryItem.unit,
    picked from the fixed catalog in inventory/config.py's UNIT_CATALOG —
    not free text, unlike InventoryType's label, so the Materials tab's
    quantity-step logic (whole vs. divisible, see UNIT_CATALOG's `whole`
    flag) always has a definition to read for whatever a company enables.

    This table controls what's *offered* in the Add/Edit item dropdown and
    in what order — it does not gate what `InventoryItem.unit` accepts (see
    the module docstring in config.py and add_item/edit_item in
    services.py): a unit already in use keeps labelling correctly even
    after a company hides or removes it here, the same hide-don't-delete
    reasoning as every other company-configurable list in this app.

    `key` (config.DEFAULT_UNIT, "each") is special: every company always
    has it, seeded lazily on first use (see services._ensure_default_unit,
    the same "create it on first read" idiom billing.profile_for() uses),
    and it can be neither hidden nor deleted — see `is_default`/`can_delete`
    below. Position is not part of what makes it special, though: it's
    reorderable like any other unit, and typically lands first only because
    it's the first row a company ever has.
    """
    __tablename__ = "inventory_units"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False)
    key = db.Column(db.String(20), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    @property
    def label(self) -> str:
        return UNIT_CATALOG.get(self.key, {}).get("label", self.key)

    @property
    def whole(self) -> bool:
        return UNIT_CATALOG.get(self.key, {}).get("whole", True)

    @property
    def is_default(self) -> bool:
        return self.key == DEFAULT_UNIT

    @property
    def can_delete(self):
        if self.is_default:
            return False
        return InventoryItem.query.filter_by(
            company_id=self.company_id, unit=self.key,
        ).first() is None


class InventoryType(db.Model):
    """A company-configurable material category (Leather, Lining,
    Hardware...). Same hide-don't-delete shape as OrderType/DocumentType:
    once an item references it, deleting it would orphan that item's label,
    so hiding (is_active=False) is the only way to retire one from new
    items while what's already tagged with it stays labelled.
    """
    __tablename__ = "inventory_types"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False)
    label = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    @property
    def can_delete(self):
        return InventoryItem.query.filter_by(inventory_type_id=self.id).first() is None


class InventoryItem(db.Model):
    """One stocked material — leather, lining, a box of zippers.

    `quantity_on_hand` and `unit_price` are edited in place from the master
    /inventory page (unlike OrderLine, which is remove-and-re-add only) —
    stock gets replenished and prices change, and there's no billing/invoice
    freeze concern here to protect against re-editing.

    Same hide-don't-delete shape as OrderType/DocumentType/InventoryType:
    once an order has drawn on this item (see OrderMaterial), it can't be
    hard-deleted — hide it instead so it drops out of new selections while
    that order's material history keeps its name and unit intact.

    `inventory_type_id` is nullable for the same reason
    `Document.document_type_id` is: a company with zero InventoryTypes
    defined yet should still be able to add items, just without a category
    picker (see inventory/routes.py).
    """
    __tablename__ = "inventory_items"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False)
    inventory_type_id = db.Column(db.Integer, db.ForeignKey("inventory_types.id"))
    name = db.Column(db.String(200), nullable=False)
    # "each" or "sqft" — see config.UNIT_LABELS. A fixed choice rather than
    # free text, so the Materials-tab quantity input knows whether to step
    # by whole units or by hundredths of a square foot.
    unit = db.Column(db.String(10), nullable=False, default="each")
    quantity_on_hand = db.Column(db.Float, nullable=False, default=0.0)
    unit_price = db.Column(db.Float, nullable=False, default=0.0)
    # The low-stock warning point: once quantity_on_hand drops to this or
    # below (while still above zero), the item is "low" — an amber signal,
    # milder than the red at-or-below-zero "out of stock" one. `0` means the
    # band is off: only the hard zero/negative signal applies, which is
    # exactly how every item behaved before this column existed. The Add/Edit
    # modal preloads it at ~10% of the quantity entered (see
    # inventory_list.html), but it's freely editable from there.
    low_stock_threshold = db.Column(db.Float, nullable=False, default=0.0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    inventory_type = db.relationship("InventoryType")

    @property
    def can_delete(self):
        return OrderMaterial.query.filter_by(inventory_item_id=self.id).first() is None

    @property
    def is_low_stock(self) -> bool:
        """Above zero but at or below the configured warning point — the
        amber "restock soon" band. Deliberately excludes zero/negative (the
        red `out_of_stock` band owns those), so the two signals never
        double-count the same item. A `0` threshold makes this always False,
        so an item with no warning point set never registers as "low"."""
        return 0 < self.quantity_on_hand <= self.low_stock_threshold


class OrderMaterial(db.Model):
    """One material drawn from inventory onto an order.

    `item_name`/`unit`/`unit_price` are a **snapshot** taken when the row is
    created, not a live join to InventoryItem — the same shape OrderLine
    already has (a price typed in, not read live off some product record),
    and for the same reason the app freezes invoice amounts/issuer details:
    a studio's cost report for an order placed in March shouldn't change
    because leather got pricier in June. The live item is still linked via
    `inventory_item_id` (so it can be found/restocked on delete), but nothing
    here reads its *current* price or name.

    No in-place quantity edit — remove and re-add, same limitation OrderLine
    has today. Removing a row restores `quantity_used` back onto the item's
    `quantity_on_hand` (see inventory/services.py).
    """
    __tablename__ = "order_materials"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    inventory_item_id = db.Column(db.Integer, db.ForeignKey("inventory_items.id"), nullable=False)
    quantity_used = db.Column(db.Float, nullable=False)
    item_name = db.Column(db.String(200), nullable=False)
    unit = db.Column(db.String(10), nullable=False)
    unit_price = db.Column(db.Float, nullable=False)

    order = db.relationship("Order")
    item = db.relationship("InventoryItem")

    @property
    def total(self):
        return self.quantity_used * self.unit_price


class OrderMaterialOther(db.Model):
    """A one-off material cost on an order — not tied to any InventoryItem,
    so it never touches stock. For the thing that doesn't warrant its own
    tracked inventory row: a one-time hardware purchase, a rush courier fee
    on materials, etc.
    """
    __tablename__ = "order_material_others"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    cost = db.Column(db.Float, nullable=False)

    order = db.relationship("Order")
