"""Fixed lists the AI must choose from.

Keeping these lists short and fixed means the Excel filters stay clean
(no "IT Services" vs "Software Services" vs "Tech Services").
Edit the lists here; the prompt and the validation both read from this file.
"""

BUSINESS_TYPES = [
    "Product",
    "Service",
    "Product and Service",
    "Marketplace / Platform",
    "Software / SaaS",
    "Content / Media",
    "Non-profit",
    "Unknown",
]

INDUSTRIES = [
    "Technology / Software",
    "Finance / Fintech",
    "E-commerce / Retail",
    "Healthcare / Life Sciences",
    "Education",
    "Manufacturing / Industrial",
    "Construction / Real Estate",
    "Marketing / Advertising",
    "Consulting / Professional Services",
    "Logistics / Transportation",
    "Energy / Utilities",
    "Hospitality / Travel",
    "Media / Entertainment",
    "Non-profit / Government",
    "Other",
]

CUSTOMER_TYPES = ["B2B", "B2C", "Both", "Unknown"]