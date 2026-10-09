"""Fixed lists the AI must choose from.

Keeping these lists short and fixed means the Excel filters stay clean
(no "IT Services" vs "Software Services" vs "Tech Services").
Edit the lists here; the prompt and the validation both read from this file.

Categories have stable ids. Classification stores the id, then the label.
A model may suggest a new label for review, but nothing here adds it.
"""

TAXONOMY_VERSION = "1"

BUSINESS_TYPE_CATEGORIES = [
    ("bt-product", "Product"),
    ("bt-service", "Service"),
    ("bt-product-service", "Product and Service"),
    ("bt-marketplace", "Marketplace / Platform"),
    ("bt-saas", "Software / SaaS"),
    ("bt-media", "Content / Media"),
    ("bt-nonprofit", "Non-profit"),
    ("bt-unknown", "Unknown"),
]

BUSINESS_TYPES = [label for _category_id, label in BUSINESS_TYPE_CATEGORIES]

INDUSTRY_CATEGORIES = [
    ("ind-technology", "Technology / Software"),
    ("ind-finance", "Finance / Fintech"),
    ("ind-retail", "E-commerce / Retail"),
    ("ind-healthcare", "Healthcare / Life Sciences"),
    ("ind-education", "Education"),
    ("ind-manufacturing", "Manufacturing / Industrial"),
    ("ind-construction", "Construction / Real Estate"),
    ("ind-marketing", "Marketing / Advertising"),
    ("ind-consulting", "Consulting / Professional Services"),
    ("ind-logistics", "Logistics / Transportation"),
    ("ind-energy", "Energy / Utilities"),
    ("ind-hospitality", "Hospitality / Travel"),
    ("ind-media", "Media / Entertainment"),
    ("ind-government", "Non-profit / Government"),
    ("ind-other", "Other"),
]

INDUSTRIES = [label for _category_id, label in INDUSTRY_CATEGORIES]

CUSTOMER_TYPE_CATEGORIES = [
    ("ct-b2b", "B2B"),
    ("ct-b2c", "B2C"),
    ("ct-both", "Both"),
    ("ct-unknown", "Unknown"),
]

CUSTOMER_TYPES = [label for _category_id, label in CUSTOMER_TYPE_CATEGORIES]

FALLBACK_BUSINESS_TYPE = "bt-unknown"
FALLBACK_INDUSTRY = "ind-other"
FALLBACK_CUSTOMER = "ct-unknown"


def category_label(categories: list[tuple[str, str]], category_id: str, fallback_id: str) -> tuple[str, str]:
    """Return (id, label). An unknown id falls back and does not create a category."""
    lookup = dict(categories)
    if category_id in lookup:
        return category_id, lookup[category_id]
    return fallback_id, lookup[fallback_id]