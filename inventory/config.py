"""
The unit vocabulary for an InventoryItem — a fixed choice, not free text, so
the quantity input on the Materials tab knows what step/precision to offer
(whole zippers vs. fractional square feet of leather) without guessing from a
string someone typed.
"""

UNIT_LABELS = {
    "each": "Each",
    "sqft": "Sqft",
}
