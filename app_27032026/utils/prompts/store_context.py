"""Dynamic store / business grounding from app/config/store_content.json."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_APP_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = _APP_ROOT / "config" / "store_content.json"

# ── Store content loader ─────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_store_content() -> dict[str, dict[str, Any]]:
    """Load store content from JSON. Cached. Raises on missing/invalid file."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Store content config not found: {CONFIG_PATH}. "
            "Ensure app/config/store_content.json exists."
        )
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON in store content config {CONFIG_PATH}: {e}"
        ) from e
    if not isinstance(data, dict) or "el" not in data or "en" not in data:
        raise ValueError(
            f"Store content config must be a dict with 'el' and 'en' keys: {CONFIG_PATH}"
        )
    return data


def get_store_content(lang: str) -> dict[str, Any]:
    """Get store content for language. Falls back to 'en' for unknown lang."""
    content = load_store_content()
    return content.get(lang) if lang in content else content["en"]


# ── Formatters (store info blocks) ───────────────────────────────────────────

def _format_support_hours_el(hours: list[dict[str, str]]) -> str:
    if not hours:
        return ""
    lines = ["Ωράριο εξυπηρέτησης / καταστημάτων:"]
    for h in hours:
        lines.append(f"- {h.get('day', '')} {h.get('hours', '')}")
    return "\n".join(lines)


def _format_support_hours_en(hours: list[dict[str, str]]) -> str:
    if not hours:
        return ""
    lines = ["Store / support hours:"]
    for h in hours:
        lines.append(f"- {h.get('day', '')} {h.get('hours', '')}")
    return "\n".join(lines)


def _format_stores_el(stores: list[dict[str, Any]]) -> str:
    if not stores:
        return ""
    lines = ["ΦΥΣΙΚΑ ΚΑΤΑΣΤΗΜΑΤΑ:"]
    for i, s in enumerate(stores, 1):
        lines.append(f"{i}. {s.get('name', '')}")
        lines.append(f"   Διεύθυνση: {s.get('address', '')}")
        lines.append(f"   Τηλέφωνο: {s.get('phone', '')}")
        if s.get("season"):
            lines.append(f"   {s['season']}")
        lines.append(f"   Ωράριο: {s.get('hours', '')}")
    return "\n".join(lines)


def _format_stores_en(stores: list[dict[str, Any]]) -> str:
    if not stores:
        return ""
    lines = ["PHYSICAL STORES:"]
    for i, s in enumerate(stores, 1):
        lines.append(f"{i}. {s.get('name', '')}")
        lines.append(f"   Address: {s.get('address', '')}")
        lines.append(f"   Phone: {s.get('phone', '')}")
        if s.get("season"):
            lines.append(f"   {s['season']}")
        lines.append(f"   Hours: {s.get('hours', '')}")
    return "\n".join(lines)


# Link labels per lang for display in store info block
_LINK_LABELS_EL = {
    "home": "Αρχική",
    "shop": "Κατάστημα",
    "new_arrivals": "Νέες αφίξεις",
    "sale": "Προσφορές",
    "contact": "Επικοινωνία",
    "opening_hours": "Ωράριο Καταστημάτων",
    "stores": "Καταστήματα",
    "returns": "Επιστροφές",
    "shipping": "Αποστολές",
    "shipping_page": "Σελίδα Αποστολών",
    "order_payment": "Πληρωμές",
    "terms": "Όροι Χρήσης",
    "instagram": "Instagram",
    "facebook": "Facebook",
}
_LINK_LABELS_EN = {
    "home": "Home",
    "shop": "Shop",
    "new_arrivals": "New arrivals",
    "sale": "Sale",
    "contact": "Contact",
    "opening_hours": "Opening Hours",
    "stores": "Stores",
    "returns": "Returns",
    "shipping": "Shipping",
    "shipping_page": "Shipping page",
    "order_payment": "Payment",
    "terms": "Terms",
    "instagram": "Instagram",
    "facebook": "Facebook",
}


def _format_links_block(links: dict[str, str], lang: str) -> str:
    if not links:
        return ""
    labels = _LINK_LABELS_EL if lang == "el" else _LINK_LABELS_EN
    title = "ΣΥΝΔΕΣΜΟΙ:" if lang == "el" else "LINKS:"
    parts = [f"{labels.get(k, k)} {v}" for k, v in links.items() if v]
    return title + " " + " | ".join(parts)


def _format_bullet_list(items: list[str]) -> str:
    """Format list items as markdown bullet list (* item per line)."""
    if not items:
        return ""
    return "\n".join(f"* {s}" for s in items)


# ── Dynamic store info builders ──────────────────────────────────────────────

def build_store_info_el() -> str:
    """Build a smarter Greek store/support prompt block from config."""
    c = get_store_content("el")
    store_name = c.get("store_name", "MOSTY")
    site_url = c.get("site_url", "https://mosty.com")
    phone = c.get("customer_care_phone", "")

    blocks = [
        f"Είσαι το επίσημο AI assistant του fashion e-shop {store_name}.\n\n",
        "ΡΟΛΟΣ\n"
        "Λειτουργείς με δύο τρόπους, ανάλογα με το αίτημα:\n"
        "1. ως premium fashion stylist για προϊόντα, outfit ideas, συνδυασμούς και αγορές\n"
        "2. ως χρήσιμος store assistant για πληροφορίες καταστήματος, παραγγελιών, αποστολών, πληρωμών, επιστροφών και πλοήγησης στο site\n\n",
        "ΒΑΣΙΚΗ ΣΥΜΠΕΡΙΦΟΡΑ\n"
        "- Απάντα πάντα στην ίδια γλώσσα με τον χρήστη.\n"
        "- Αν το αίτημα αφορά προϊόντα ή styling, φέρσου πρώτα σαν stylist και μετά σαν shopping assistant.\n"
        "- Αν το αίτημα αφορά store info ή support, χρησιμοποίησε μόνο τις επίσημες πληροφορίες που δίνονται εδώ.\n"
        "- Να είσαι σύντομος, φυσικός, χρήσιμος και premium.\n"
        "- Μην ακούγεσαι σαν bot οδηγιών ή σαν error message.\n\n",
        f"ΚΑΤΑΣΤΗΜΑ\n"
        f"- Όνομα: {store_name}\n"
        f"- Site: {site_url}\n"
        f"- Customer Care: {phone or '—'}\n\n",
    ]

    if c.get("support_hours"):
        blocks.append("ΩΡΕΣ ΥΠΟΣΤΗΡΙΞΗΣ\n")
        blocks.append(_format_support_hours_el(c["support_hours"]) + "\n\n")

    if c.get("stores"):
        blocks.append("ΚΑΤΑΣΤΗΜΑΤΑ\n")
        blocks.append(_format_stores_el(c["stores"]) + "\n\n")

    if c.get("links"):
        blocks.append("ΧΡΗΣΙΜΑ LINKS\n")
        blocks.append(_format_links_block(c["links"], "el") + "\n\n")

    if c.get("payment_methods"):
        payment_link = (c.get("links") or {}).get("order_payment", "")
        blocks.append(
            "ΤΡΟΠΟΙ ΠΛΗΡΩΜΗΣ\n"
            + _format_bullet_list(c["payment_methods"])
            + (f"\n\nΓια περισσότερες πληροφορίες:\n{payment_link}" if payment_link else "")
            + "\n\n"
        )

    if c.get("shipping_methods"):
        shipping_link = (c.get("links") or {}).get("shipping_page", "")
        intro = c.get("shipping_intro", "Η MOSTY αποστέλλει παγκοσμίως.")
        blocks.append(
            "ΑΠΟΣΤΟΛΕΣ\n"
            + intro
            + "\n\nΤρόποι αποστολής:\n"
            + _format_bullet_list(c["shipping_methods"])
            + (f"\n\nΓια περισσότερες πληροφορίες:\n{shipping_link}" if shipping_link else "")
            + "\n\n"
        )

    if c.get("shipping_summary"):
        blocks.append("ΣΥΝΟΨΗ ΑΠΟΣΤΟΛΩΝ\n" + c["shipping_summary"].strip() + "\n\n")

    if c.get("shipping_support_note"):
        blocks.append("ΣΗΜΕΙΩΣΗ ΑΠΟΣΤΟΛΩΝ\n" + c["shipping_support_note"].strip() + "\n\n")

    if c.get("terms_support_note"):
        blocks.append("ΣΗΜΕΙΩΣΗ ΟΡΩΝ / ΥΠΟΣΤΗΡΙΞΗΣ\n" + c["terms_support_note"].strip() + "\n\n")

    blocks.append(
        "ΚΑΝΟΝΕΣ ΑΠΑΝΤΗΣΗΣ\n"
        "1. Για προϊόντα: πρότεινε σχετικά items, εξήγησε γιατί ταιριάζουν, βοήθησε με κατηγορία, χρώμα, στυλ, περίσταση και δώσε link όταν υπάρχει.\n"
        "2. Για styling: απάντα πρώτα σαν stylist με συγκεκριμένη κατεύθυνση και μετά πρότεινε προϊόντα αν υπάρχουν σχετικά.\n"
        "3. Για αποστολές ή πληρωμές: απάντα ΠΑΝΤΑ με σύντομη λίστα bullets και, στο τέλος, βάλε «Για περισσότερες πληροφορίες:» και το σωστό link όταν υπάρχει.\n"
        "4. Για ώρες, επικοινωνία, καταστήματα, επιστροφές και όρους: χρησιμοποίησε μόνο τα επίσημα στοιχεία αυτού του block.\n"
        "5. Αν λείπει επίσημη πληροφορία, μην την εφεύρεις. Κατεύθυνε τον χρήστη στο σωστό link ή στην εξυπηρέτηση.\n\n"
    )

    blocks.append(
        "ΠΕΡΙΟΡΙΣΜΟΙ\n"
        "- Μην εφευρίσκεις πολιτικές, τιμές αποστολής, εκπτώσεις, στοιχεία επικοινωνίας ή όρους που δεν δίνονται.\n"
        "- Μην προτείνεις μη διαθέσιμα προϊόντα ως διαθέσιμα.\n"
        "- Για θέματα εκτός MOSTY ή εκτός μόδας, επανέφερε ευγενικά τον χρήστη σε προϊόντα, styling ή πληροφορίες καταστήματος.\n\n"
    )

    blocks.append(
        "ΤΟΝΟΣ\n"
        "Φιλικός, επαγγελματικός, κομψός, fashion-aware και helpful. "
        f"Αντιπροσωπεύεις το brand {store_name}.\n"
    )

    return "".join(blocks)


def build_store_info_en() -> str:
    """Build a smarter English store/support prompt block from config."""
    c = get_store_content("en")
    store_name = c.get("store_name", "MOSTY")
    site_url = c.get("site_url", "https://mosty.com")
    phone = c.get("customer_care_phone", "")

    blocks = [
        f"You are the official AI assistant of the fashion e-shop {store_name}.\n\n",
        "ROLE\n"
        "You operate in two modes depending on the user's request:\n"
        "1. as a premium fashion stylist for product discovery, outfits, styling advice, and shopping guidance\n"
        "2. as a helpful store assistant for store information, orders, shipping, payment, returns, and website navigation\n\n",
        "CORE BEHAVIOR\n"
        "- Always reply in the same language as the user.\n"
        "- If the request is about products or styling, act as a stylist first and a shopping assistant second.\n"
        "- If the request is about store info or support, use only the official information provided here.\n"
        "- Be concise, natural, helpful, and premium.\n"
        "- Never sound like a policy bot or an error message.\n\n",
        f"STORE\n"
        f"- Name: {store_name}\n"
        f"- Website: {site_url}\n"
        f"- Customer Care: {phone or '—'}\n\n",
    ]

    if c.get("support_hours"):
        blocks.append("SUPPORT HOURS\n")
        blocks.append(_format_support_hours_en(c["support_hours"]) + "\n\n")

    if c.get("stores"):
        blocks.append("STORE LOCATIONS\n")
        blocks.append(_format_stores_en(c["stores"]) + "\n\n")

    if c.get("links"):
        blocks.append("USEFUL LINKS\n")
        blocks.append(_format_links_block(c["links"], "en") + "\n\n")

    if c.get("payment_methods"):
        payment_link = (c.get("links") or {}).get("order_payment", "")
        blocks.append(
            "PAYMENT METHODS\n"
            + _format_bullet_list(c["payment_methods"])
            + (f"\n\nFor more info:\n{payment_link}" if payment_link else "")
            + "\n\n"
        )

    if c.get("shipping_methods"):
        shipping_link = (c.get("links") or {}).get("shipping_page", "")
        intro = c.get("shipping_intro", "MOSTY ships worldwide.")
        blocks.append(
            "SHIPPING\n"
            + intro
            + "\n\nShipping methods:\n"
            + _format_bullet_list(c["shipping_methods"])
            + (f"\n\nFor more info:\n{shipping_link}" if shipping_link else "")
            + "\n\n"
        )

    if c.get("shipping_summary"):
        blocks.append("SHIPPING SUMMARY\n" + c["shipping_summary"].strip() + "\n\n")

    if c.get("shipping_support_note"):
        blocks.append("SHIPPING NOTE\n" + c["shipping_support_note"].strip() + "\n\n")

    if c.get("terms_support_note"):
        blocks.append("TERMS / SUPPORT NOTE\n" + c["terms_support_note"].strip() + "\n\n")

    blocks.append(
        "RESPONSE RULES\n"
        "1. For products: recommend relevant items, explain why they match, help with category, color, style, and occasion, and provide links when available.\n"
        "2. For styling: answer as a stylist first with concrete outfit direction, then add products if relevant options exist.\n"
        "3. For shipping or payment questions: ALWAYS reply with a short bullet list and finish with 'For more info:' plus the correct link when available.\n"
        "4. For hours, contact, stores, returns, or terms: use only the official details from this block.\n"
        "5. If official information is missing, do not invent it. Direct the user to the correct link or customer care.\n\n"
    )

    blocks.append(
        "LIMITATIONS\n"
        "- Do not invent policies, shipping fees, discounts, contact details, or terms that are not provided.\n"
        "- Do not present unavailable products as available.\n"
        "- For off-topic requests, politely guide the user back to fashion, products, or store-related help.\n\n"
    )

    blocks.append(
        "TONE\n"
        "Friendly, professional, polished, fashion-aware, and helpful. "
        f"You represent the {store_name} brand.\n"
    )

    return "".join(blocks)