from sqlalchemy import select

from app.database import get_session, init_db
from app.importer import import_urls
from app.models import Website

SAMPLE_URLS = "\n".join([
    "https://stripe.com",
    "https://openai.com",
    "https://shopify.com",
    "https://example.com",
])

DUMMY_TEXT = {
    "https://stripe.com": (
        "[PAGE: /home]\nStripe builds payment tools for internet businesses. "
        "Companies of every size use Stripe to accept online payments, send payouts "
        "and manage subscriptions and invoices.\n"
        "[PAGE: /products]\nProducts include Payments, Billing, Invoicing and Fraud Prevention. "
        "Developers connect through a simple API and ready-made checkout pages. "
        "Stripe also offers financial reporting and tax tools for online sellers.\n"
        "[PAGE: /about]\nStripe is used by startups and large enterprises around the world to "
        "grow their revenue online. Our teams work on payment infrastructure and developer tools."
    ),
    "https://openai.com": (
        "[PAGE: /home]\nOpenAI is an AI research and deployment company. "
        "We develop advanced AI models and make them available through ChatGPT and an API.\n"
        "[PAGE: /about]\nOur mission is to make sure artificial intelligence benefits all of humanity. "
        "We publish research and build products used by individuals, developers and enterprises.\n"
        "[PAGE: /products]\nChatGPT for individuals and teams, an API for developers, "
        "and enterprise offerings with admin controls and security features."
    ),
    "https://shopify.com": (
        "[PAGE: /home]\nShopify helps anyone start, run and grow an online store. "
        "Merchants get a website builder, checkout, inventory tools and shipping options in one place.\n"
        "[PAGE: /products]\nOnline store builder, point of sale for physical shops, payments, "
        "shipping labels, marketing tools and an app store with thousands of add-ons.\n"
        "[PAGE: /about]\nMillions of merchants use Shopify to sell to customers online and in person."
    ),
    # Very little text on purpose, to test that confidence comes out low.
    "https://example.com": (
        "[PAGE: /home]\nExample Domain. This domain is for use in documentation examples."
    ),
}


def main() -> None:
    init_db()
    with get_session() as session:
        import_urls(session, SAMPLE_URLS, "dummy")
        for url, text in DUMMY_TEXT.items():
            site = session.scalar(select(Website).where(Website.normalized_website == url))
            if site is not None:
                site.clean_text = text
                site.status = "crawled"
        session.commit()
    print("Dummy clean_text added for", len(DUMMY_TEXT), "websites.")


if __name__ == "__main__":
    main()