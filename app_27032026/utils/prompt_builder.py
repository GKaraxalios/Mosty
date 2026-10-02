"""User-side prompt builders and catalog helpers for ELIZA / MOSTY.

System instruction layers live under `app.utils.prompts` (see `assembly.py`, `system_instruction_content.py`, `store_context.py`).
"""
from __future__ import annotations

from typing import Any

from app.models.schemas import RAGSearchResult
from app.services.weather_service import WeatherInfo, map_openmeteo_weather_code
from app.utils.intent import FashionIntent
from app.utils.prompts.assembly import OutputMode, get_system_instruction
from app.utils.prompts.store_context import (
    CONFIG_PATH,
    build_store_info_el,
    build_store_info_en,
    get_store_content,
    load_store_content,
)
from app.utils.prompts.system_instruction_content import SYSTEM_RAG_RULES_EL, SYSTEM_RAG_RULES_EN


# ── Response Rules ───────────────────────────────────────────────────────────

RULES_EL = (
    "ΚΑΝΟΝΕΣ:\n"
    "1. Πρότεινε ΜΟΝΟ από τα παρακάτω προϊόντα (ΤΡΕΧΟΥΣΑ αναζήτηση).\n"
    "2. ΜΗΝ δείχνεις προϊόντα από προηγούμενα μηνύματα εκτός αν ο χρήστης αναφέρεται ρητά.\n"
    "3. Αν κανένα δεν ταιριάζει, πες ξεκάθαρα ότι δεν βρέθηκε κάτι σχετικό.\n"
    "4. Αν ο χρήστης έχει δηλώσει budget/εύρος τιμής: ΜΗΝ συμπεριλάβεις προϊόντα εκτός εύρους.\n"
    "5. Αναφέρε πάντα όνομα, τιμή και URL.\n"
    "6. ΜΗΝ εφευρίσκεις προϊόντα ή URLs.\n"
    "7. ΜΗΝ προτείνεις προϊόντα χωρίς stock ή χωρίς URL.\n"
    "8. Εξήγησε συγκεκριμένα γιατί ταιριάζει κάθε πρόταση στο ΑΥΤΟ το αίτημα (όχι γενικές φράσεις).\n"
    "9. Πρότεινε εναλλακτικές αν δεν υπάρχει ακριβής ταίριασμα.\n"
    "10. ΜΗΝ δείχνεις το ίδιο προϊόν δύο φορές.\n"
    "11. Αν υπάρχει λίστα προϊόντων από την αναζήτηση: πρότεινε συγκεκριμένα SKU — "
    "όλα όσα κρίνεις σχετικά με την ερώτηση, έως όσα εμφανίζονται στη λίστα. "
    "Μην αφήνεις κενό products όταν υπάρχουν σχετικά· όχι γενική απάντηση χωρίς προϊόντα.\n"
)

def get_no_catalog_rules(lang: str) -> str:
    """Compact rules when RAG returned zero products — avoids contradicting full RULES_* (SKU list, prices, etc.)."""
    if lang == "el":
        return (
            "ΚΑΤΑΛΟΓΟΣ: Η τρέχουσα αναζήτηση επέστρεψε 0 προϊόντα (δεν υπάρχει λίστα SKU).\n\n"
            "ΓΙΑ ΤΟ ΠΕΔΙΟ message:\n"
            "• Πρώτα: συμβουλές styling για το συγκεκριμένο αίτημα (σιλουέτα, περίσταση, χρώματα, τι να ψάξει μετά).\n"
            "• Στο τέλος: μία σύντομη πρόταση ότι δεν υπάρχουν ταιριαστά προϊόντα στον κατάλογο τώρα.\n"
            "• ΜΗΝ ανοίγεις με «Δυστυχώς». ΜΗΝ επαναλαμβάνεις την ίδια γενική άρνηση σε κάθε ερώτηση — προσάρμοσε στο ΤΡΕΧΟΝ ερώτημα.\n"
            "• Κατηγορία/τύπος: μην προσθέτεις κατηγορία που δεν είπε ο χρήστης· αν την είπε ρητά (π.χ. «φορέματα»), μπορείς να την αναφέρεις φυσικά.\n\n"
            "Έξοδος: μόνο JSON με no_results=true και products=[]. ΜΗΝ εφευρίσκεις προϊόντα, SKU ή URLs."
        )
    return (
        "CATALOG: Current search returned 0 products (no SKU list).\n\n"
        "FOR the message field:\n"
        "• First: styling advice for this specific request (silhouette, occasion, colors, what to try next).\n"
        "• Last: one short sentence that no matching products are in the catalog now.\n"
        "• Do not open with \"Unfortunately\". Do not reuse the same canned denial — tailor to the CURRENT question.\n"
        "• Category/type: do not add a category the user did not mention; if they named one (e.g. dresses), you may refer to it.\n\n"
        "Output: JSON only with no_results=true and products=[]. Do NOT invent products, SKUs, or URLs."
    )


RULES_EN = (
    "RULES:\n"
    "1. Recommend ONLY from the products listed below (CURRENT search).\n"
    "2. Do NOT show products from previous messages unless the user explicitly refers to them.\n"
    "3. If none match, clearly state that.\n"
    "4. If the user stated a budget / price range: do NOT include products outside that range.\n"
    "5. Always mention name, price, and URL.\n"
    "6. NEVER fabricate products or URLs.\n"
    "7. Do NOT recommend products without stock or without a valid URL.\n"
    "8. Explain specifically why each suggestion fits THIS request (no empty marketing phrases).\n"
    "9. Suggest alternatives if there is no exact match.\n"
    "10. Do NOT show the same product twice.\n"
    "11. When a product list is provided from search: recommend specific SKUs — "
    "every item you judge relevant to the question, up to the full list shown. "
    "Do not leave products empty when relevant rows exist; no vague reply without products.\n"
)


def get_rules(lang: str) -> str:
    return RULES_EL if lang == "el" else RULES_EN


# ── Dynamic product count (never ask for more than available) ─────────────────


def _get_product_count_instruction(valid_count: int, lang: str) -> str:
    """How many products to put in JSON: all relevant rows from this search list (up to valid_count)."""
    if valid_count <= 0:
        return "Μην προτείνεις προϊόντα." if lang == "el" else "Do not recommend any products."
    if lang == "el":
        return (
            f"Η λίστα παρακάτω έχει {valid_count} προϊόν(τα) από αυτή την αναζήτηση. "
            "Στο JSON βάλε ΟΛΑ όσα κρίνεις σχετικά με την ερώτηση (ελάχιστο 1 αν υπάρχει τουλάχιστον ένα σχετικό). "
            f"Μέγιστο {valid_count} (μόνο από αυτή τη λίστα). Μη μειώνεις τεχνητά τον αριθμό· όχι απάντηση χωρίς συγκεκριμένα προϊόντα όταν η λίστα δεν είναι κενή."
        )
    return (
        f"The list below has {valid_count} product(s) from this search. "
        "In JSON include ALL you judge relevant to the question (at least 1 when at least one row fits). "
        f"At most {valid_count} (only from this list). Do not arbitrarily cap below relevant matches; "
        "do not answer without specific products when the list is non-empty."
    )


# ── Valid results filter (before building catalog context) ─────────────────────


def filter_valid_results(results: list[RAGSearchResult]) -> list[RAGSearchResult]:
    """Keep only products safe to recommend: valid URL and in stock.

    - Ignores products without URL or with non-http URL.
    - Ignores products with explicit stock_status not IN_STOCK (e.g. OUT_OF_STOCK).
    - When stock_status is missing, includes the product (fallback: assume available);
      do not ask the model to guess availability.
    """
    valid: list[RAGSearchResult] = []
    for r in results:
        if not r.url or not str(r.url).strip().startswith("http"):
            continue
        stock = (r.metadata or {}).get("stock_status")
        if stock is not None and str(stock).strip().upper() not in ("IN_STOCK", "1", "TRUE", "YES", ""):
            continue
        valid.append(r)
    return valid


# ── Product context block builder (expects pre-filtered results; includes availability) ─


def _format_availability(metadata: dict[str, Any]) -> str:
    """Return availability label for product context. Only call for products that passed filter_valid_results."""
    stock = metadata.get("stock_status")
    if stock is None or str(stock).strip() == "":
        return "in_stock"
    s = str(stock).strip().upper()
    if s in ("IN_STOCK", "1", "TRUE", "YES"):
        return "in_stock"
    return "out_of_stock"


def _build_product_context(results: list[RAGSearchResult], lang: str) -> str:
    """Build catalog block. Call with filter_valid_results(results) so availability is consistent."""
    header = "ΠΡΟΪΟΝΤΑ ΑΠΟ ΚΑΤΑΛΟΓΟ:" if lang == "el" else "PRODUCTS FROM CATALOG:"
    sep = "Προϊόν" if lang == "el" else "Product"
    label_avail = "Διαθεσιμότητα" if lang == "el" else "Availability"
    lines: list[str] = [header]

    for i, r in enumerate(results, 1):
        meta = r.metadata or {}
        lines.append(f"\n--- {sep} {i} (score: {r.score:.2f}) ---")
        lines.append(f"SKU: {r.sku}")
        label_name = "Όνομα" if lang == "el" else "Name"
        lines.append(f"{label_name}: {r.name}")
        if r.url:
            lines.append(f"URL: {r.url}")
        avail = _format_availability(meta)
        lines.append(f"{label_avail}: {avail}")
        label_text = "Κείμενο" if lang == "el" else "Text"
        lines.append(f"{label_text}: {r.chunk_text}")
        if meta.get("price"):
            label_price = "Τιμή" if lang == "el" else "Price"
            lines.append(f"{label_price}: {meta['price']} EUR")
        if meta.get("categories"):
            label_cats = "Κατηγορίες" if lang == "el" else "Categories"
            lines.append(f"{label_cats}: {', '.join(meta['categories'])}")

    return "\n".join(lines)


# ── Prompt Builders ──────────────────────────────────────────────────────────

def build_rag_prompt(
    query: str,
    results: list[RAGSearchResult],
    lang: str = "el",
    conversation_context: str = "",
) -> str:
    """Standard retrieval-answer prompt.

    Order: RULES → CONVERSATION HISTORY → PRODUCTS → QUESTION → ANSWER
    History goes before products so the LLM understands the conversational
    flow before interpreting what was retrieved.
    """
    rules = get_rules(lang)
    context_block = _build_product_context(results, lang)

    parts = [rules, ""]

    if conversation_context:
        header = "ΙΣΤΟΡΙΚΟ ΣΥΝΟΜΙΛΙΑΣ:" if lang == "el" else "CONVERSATION HISTORY:"
        note = (
            "(Λάβε υπόψη ΟΛΟΥΣ τους περιορισμούς που ανέφερε ο χρήστης παραπάνω)"
            if lang == "el"
            else "(Consider ALL constraints the user mentioned above)"
        )
        parts.extend([header, conversation_context, note, ""])

    parts.extend([context_block, ""])

    q_label = "ΕΡΩΤΗΣΗ ΧΡΗΣΤΗ" if lang == "el" else "USER QUESTION"
    a_label = "ΑΠΑΝΤΗΣΗ" if lang == "el" else "ANSWER"
    parts.extend([f"{q_label}: {query}", "", f"{a_label}:"])

    return "\n".join(parts)


def build_recommendation_prompt(
    query: str,
    results: list[RAGSearchResult],
    lang: str = "el",
    conversation_context: str = "",
) -> str:
    """Recommendation-focused prompt."""
    context_block = _build_product_context(results, lang)

    if lang == "el":
        intro = (
            "Ο χρήστης ψάχνει προτάσεις. Δώσε εξατομικευμένες συστάσεις "
            "βάσει των παρακάτω προϊόντων. Λάβε υπόψη budget, στυλ, περίσταση. "
            "Μη δημιουργείς προϊόντα. Αναφέρε ονόματα, τιμές, URLs."
        )
        q_label = "ΖΗΤΗΣΗ ΧΡΗΣΤΗ"
        a_label = "ΠΡΟΤΑΣΕΙΣ"
    else:
        intro = (
            "The user is looking for recommendations. Provide personalized suggestions "
            "based on the products below. Consider budget, style, and occasion. "
            "Never fabricate products. Always mention names, prices, and URLs."
        )
        q_label = "USER REQUEST"
        a_label = "RECOMMENDATIONS"

    parts = [intro, ""]
    if conversation_context:
        header = "ΙΣΤΟΡΙΚΟ ΣΥΝΟΜΙΛΙΑΣ:" if lang == "el" else "CONVERSATION HISTORY:"
        parts.extend([header, conversation_context, ""])
    parts.extend([context_block, ""])
    parts.extend([f"{q_label}: {query}", "", f"{a_label}:"])
    return "\n".join(parts)


def build_comparison_prompt(
    product_texts: list[str],
    query: str | None,
    lang: str = "el",
) -> str:
    """Product comparison prompt."""
    if lang == "el":
        intro = (
            "Σύγκρινε τα παρακάτω προϊόντα. Ανάφερε διαφορές σε τιμή, στυλ, "
            "κατηγορία. Μη εφευρίσκεις χαρακτηριστικά."
        )
        label = "ΠΡΟΪΟΝΤΑ ΠΡΟΣ ΣΥΓΚΡΙΣΗ"
        a_label = "ΣΥΓΚΡΙΣΗ"
    else:
        intro = (
            "Compare the following products. Note differences in price, style, "
            "category. Do not invent characteristics."
        )
        label = "PRODUCTS TO COMPARE"
        a_label = "COMPARISON"

    parts = [intro, "", f"{label}:"]
    for i, text in enumerate(product_texts, 1):
        parts.append(f"\n--- #{i} ---")
        parts.append(text)

    if query:
        q_label = "ΕΡΩΤΗΣΗ" if lang == "el" else "QUESTION"
        parts.extend(["", f"{q_label}: {query}"])

    parts.extend(["", f"{a_label}:"])
    return "\n".join(parts)


def build_followup_prompt(
    query: str,
    results: list[RAGSearchResult],
    conversation_context: str,
    lang: str = "el",
) -> str:
    """Follow-up prompt with conversation memory.

    Adds an explicit instruction block telling the LLM to treat the
    current query as a continuation that inherits previous constraints.
    """
    if lang == "el":
        followup_note = (
            "ΣΗΜΑΝΤΙΚΟ: Αυτό είναι ΣΥΝΕΧΕΙΑ συνομιλίας. Ο χρήστης δεν επαναλαμβάνει "
            "τους προηγούμενους περιορισμούς του. ΠΡΕΠΕΙ να τους συνδυάσεις ΟΛΟΥΣ "
            "(κατηγορία, χρώμα, στυλ, budget, περίσταση, κ.λπ.) από το ιστορικό παραπάνω "
            "με το τρέχον μήνυμα. Αν το νέο μήνυμα αντικαθιστά κάποιον περιορισμό, "
            "χρησιμοποίησε τον νέο."
        )
    else:
        followup_note = (
            "IMPORTANT: This is a FOLLOW-UP message. The user is NOT repeating their "
            "earlier constraints. You MUST combine ALL constraints (category, color, "
            "style, budget, occasion, etc.) from the conversation history above with "
            "the current message. If the new message replaces a constraint, use the "
            "new one."
        )

    prompt = build_rag_prompt(
        query=query,
        results=results,
        lang=lang,
        conversation_context=conversation_context,
    )
    q_label = "ΕΡΩΤΗΣΗ ΧΡΗΣΤΗ" if lang == "el" else "USER QUESTION"
    return prompt.replace(
        f"{q_label}:",
        f"{followup_note}\n\n{q_label}:",
    )


def build_no_results_response(lang: str = "el") -> str:
    if lang == "el":
        return (
            "Αυτή τη στιγμή δεν υπάρχουν διαθέσιμα προϊόντα σε αυτή την κατηγορία. "
            "Μπορείς να δοκιμάσεις με διαφορετικούς όρους αναζήτησης ή "
            "να μου πεις περισσότερα για αυτό που ψάχνεις, "
            "και θα προσπαθήσω να σου προτείνω παρόμοια."
        )
    return (
        "There are currently no available products in this category. "
        "You can try different search terms or tell me more about what you're looking for, "
        "and I'll try to suggest similar alternatives."
    )


def build_info_only_prompt(query: str, lang: str = "el") -> str:
    """Prompt for store/support questions: answer from system instructions only, no products."""
    if lang == "el":
        return (
            f"Ο χρήστης ρώτησε: «{query}»\n\n"
            "Απάντησε ΜΟΝΟ με βάση τις πληροφορίες καταστήματος και υποστήριξης που έχεις στα instructions. "
            "ΜΗΝ προτείνεις ή αναφέρεις προϊόντα. ΜΗΝ εμφανίζεις product cards ή recommendations. "
            "Απάντησε στην ίδια γλώσσα με το ερώτημα."
        )
    return (
        f"The user asked: «{query}»\n\n"
        "Answer ONLY using the store and support information in your instructions. "
        "Do NOT recommend or mention any products. Do NOT show product cards or recommendations. "
        "Reply in the same language as the question."
    )


def build_query_rewrite_prompt(
    current_query: str,
    conversation_context: str,
    lang: str = "el",
) -> str:
    """Ask the LLM to rewrite a follow-up query into a standalone search query."""
    if lang == "el":
        instruction = (
            "Το παρακάτω μήνυμα είναι follow-up σε συνομιλία.\n"
            "Ξαναγράψε το ως ένα ΑΥΤΟΝΟΜΟ search query για αναζήτηση προϊόντων.\n\n"
            "ΚΑΝΟΝΕΣ:\n"
            "- Συνδύασε ΟΛΟΥΣ τους περιορισμούς του χρήστη (κατηγορία, χρώμα, τιμή, στυλ, κ.λπ.)\n"
            "- Γράψε ΜΟΝΟ λέξεις-κλειδιά αναζήτησης, χωρίς σχόλια ή παρατηρήσεις\n"
            "- ΜΗΝ αναφέρεις τι δεν βρέθηκε ή τι απάντησε ο assistant\n"
            "- ΜΗΝ γράψεις πρόταση — μόνο όρους αναζήτησης\n"
            "- Απάντησε ΜΟΝΟ με το νέο query, τίποτα άλλο"
        )
    else:
        instruction = (
            "The message below is a follow-up in a conversation.\n"
            "Rewrite it as a STANDALONE product search query.\n\n"
            "RULES:\n"
            "- Combine ALL user constraints (category, color, price, style, etc.)\n"
            "- Write ONLY search keywords, no commentary or explanations\n"
            "- Do NOT mention what was not found or what the assistant said\n"
            "- Do NOT write a sentence — only search terms\n"
            "- Reply ONLY with the new query, nothing else"
        )

    return (
        f"{instruction}\n\n"
        f"CONVERSATION:\n{conversation_context}\n\n"
        f"FOLLOW-UP: {current_query}\n\n"
        f"REWRITTEN QUERY:"
    )


# ── Weather context formatter ─────────────────────────────────────────────────


def format_weather_context(weather: WeatherInfo, lang: str) -> str:
    """Format current weather for injection into the stylist prompt.

    Uses Open-Meteo WeatherInfo (temp_c, feels_like_c, wind_speed_kmh, etc.).
    Description is localized via map_openmeteo_weather_code when weather_code is present.
    """
    temp = weather.temp_c
    feels = weather.feels_like_c
    desc = (
        map_openmeteo_weather_code(weather.weather_code or 0, weather.is_day, lang)
        if weather.weather_code is not None
        else weather.description
    )
    wind = weather.wind_speed_kmh
    city = weather.city or ""
    if lang == "el":
        lines = [
            "Τρέχων καιρός:",
            f"- Θερμοκρασία: {temp:.0f}°C",
            f"- Αίσθηση: {feels:.0f}°C",
            f"- Κατάσταση: {desc}",
            f"- Άνεμος: {wind:.1f} km/h",
        ]
        if city:
            lines.insert(1, f"- Τοποθεσία: {city}")
    else:
        lines = [
            "Current weather:",
            f"- Temperature: {temp:.0f}°C",
            f"- Feels like: {feels:.0f}°C",
            f"- Condition: {desc}",
            f"- Wind: {wind:.1f} km/h",
        ]
        if city:
            lines.insert(1, f"- Location: {city}")
    return "\n".join(lines)


# ── JSON output instructions (schema aligned with rules; dynamic count; no_results) ─


def _build_json_output_instruction(
    lang: str,
    valid_count: int,
    is_popular: bool,
    no_results_flow: bool = False,
) -> str:
    """Build the user-prompt output block: JSON schema + rules. No conflict with system (system uses output_mode=json).

    Schema includes: message, no_results, products[]. Each product: sku, name, price, url, styling_reason
    (and for popular: popularity_reason, badge). Product count is dynamic from valid_count.
    When no_results_flow=True, instructs to return no_results: true, products: [], and a short message.
    """
    count_instr = _get_product_count_instruction(valid_count, lang)
    if no_results_flow:
        if lang == "el":
            return (
                "ΜΟΡΦΗ ΕΞΟΔΟΥ — απάντησε ΜΟΝΟ με έγκυρο JSON (χωρίς markdown fences):\n"
                '{"message": "...", "no_results": true, "products": []}\n'
            )
        return (
            "OUTPUT — reply with valid JSON only (no markdown fences):\n"
            '{"message": "...", "no_results": true, "products": []}\n'
        )
    if is_popular:
        if lang == "el":
            return (
                "ΜΟΡΦΗ ΕΞΟΔΟΥ:\n"
                "Απάντησε ΜΟΝΟ σε JSON με αυτή τη δομή:\n"
                '{"message": "...", "no_results": false, "products": [{"sku": "...", "name": "...", "price": "...", "url": "...", "popularity_reason": "...", "badge": "Featured"}, ...]}\n\n'
                f"ΚΑΝΟΝΕΣ: Χρησιμοποίησε ΜΟΝΟ προϊόντα από τα παραπάνω. {count_instr} Για κάθε προϊόν: sku, name, price, url (από το catalog), popularity_reason (1 πρόταση), badge. Απάντησε ΜΟΝΟ με JSON.\n"
            )
        return (
            "OUTPUT FORMAT:\n"
            "Reply ONLY in JSON with this structure:\n"
            '{"message": "...", "no_results": false, "products": [{"sku": "...", "name": "...", "price": "...", "url": "...", "popularity_reason": "...", "badge": "Featured"}, ...]}\n\n'
            f"RULES: Use ONLY products from above. {count_instr} For each product: sku, name, price, url (from catalog), popularity_reason (one sentence), badge. Reply ONLY with JSON.\n"
        )
    if lang == "el":
        return (
            "ΜΟΡΦΗ ΕΞΟΔΟΥ:\n"
            "Απάντησε ΜΟΝΟ σε JSON με αυτή τη δομή:\n"
            '{\n'
            '  "message": "Σύντομη εισαγωγή στη γλώσσα του χρήστη",\n'
            '  "no_results": false,\n'
            '  "products": [\n'
            '    {"sku": "...", "name": "...", "price": "...", "url": "...", "styling_reason": "Γιατί ταιριάζει (1 πρόταση)"},\n'
            '    ...\n'
            '  ]\n'
            '}\n\n'
            f"ΚΑΝΟΝΕΣ ΕΞΟΔΟΥ:\n"
            f"- Χρησιμοποίησε ΜΟΝΟ προϊόντα από τα παραπάνω. {count_instr}\n"
            "- ΓΙΑ ΚΑΘΕ προϊόν στο products[]: sku, name, price, url (από το catalog), styling_reason (1 πρόταση, μη κενό).\n"
            "- Μην εφευρίσκεις SKU ή URLs. Απάντησε ΜΟΝΟ με JSON.\n"
        )
    return (
        "OUTPUT FORMAT:\n"
        "Reply ONLY in JSON with this structure:\n"
        '{\n'
        '  "message": "Short introduction in the user\'s language",\n'
        '  "no_results": false,\n'
        '  "products": [\n'
        '    {"sku": "...", "name": "...", "price": "...", "url": "...", "styling_reason": "Why it fits (1 sentence)"},\n'
        '    ...\n'
        '  ]\n'
        '}\n\n'
        f"OUTPUT RULES:\n"
        f"- Use ONLY products from above. {count_instr}\n"
        "- For EVERY product in products[]: sku, name, price, url (from catalog), styling_reason (one sentence, non-empty).\n"
        "- Do NOT invent SKUs or URLs. Reply ONLY with JSON.\n"
    )


# ── Stylist Prompt Builder ───────────────────────────────────────────────────


def build_stylist_prompt(
    query: str,
    intent: FashionIntent,
    results: list[RAGSearchResult],
    lang: str = "el",
    conversation_context: str = "",
    ref_product: RAGSearchResult | None = None,
    weather_context: str = "",
    retrieval_meta: dict | None = None,
) -> str:
    """Build the main stylist prompt with intent-aware structure.

    Filters to valid results (URL + in stock) before building catalog context.
    Instructs the model to include all catalog rows relevant to the query (up to the list size). JSON schema includes
    message, no_results, products[] with sku, name, price, url, styling_reason.
    When no valid products, prompts for no_results: true, products: [].

    retrieval_meta: optional keys ``match_tier`` (``exact`` | ``alternatives`` | ``none``),
    ``color_canonical`` — when ``alternatives``, instructs the model not to claim exact color matches.
    """
    valid_results = filter_valid_results(results)
    # If every hit fails URL/stock checks, we still feed raw retrieval into the prompt so the model
    # does not think the catalog is empty (avoids repetitive "nothing found" + wrong category).
    catalog_for_prompt = valid_results if valid_results else results
    relaxed_catalog = bool(results and not valid_results)

    rules = (
        get_no_catalog_rules(lang)
        if len(catalog_for_prompt) == 0
        else get_rules(lang)
    )
    parts = [rules, ""]

    if weather_context:
        parts.append(weather_context)
        if lang == "el":
            parts.append("Χρησιμοποίησε τα παραπάνω δεδομένα καιρού για την απάντησή σου· μην πεις ότι δεν έχεις πρόσβαση στον καιρό.")
        else:
            parts.append("Use the weather data above in your reply; do not say you do not have access to weather.")
        parts.append("")

    if conversation_context:
        h = "ΙΣΤΟΡΙΚΟ ΣΥΝΟΜΙΛΙΑΣ:" if lang == "el" else "CONVERSATION HISTORY:"
        note = (
            "(Λάβε υπόψη ΟΛΟΥΣ τους περιορισμούς που ανέφερε ο χρήστης)"
            if lang == "el"
            else "(Consider ALL constraints the user mentioned)"
        )
        parts.extend([h, conversation_context, note, ""])

    if ref_product:
        rh = "ΠΡΟΪΟΝ ΑΝΑΦΟΡΑΣ:" if lang == "el" else "REFERENCE PRODUCT:"
        rn = (
            "(Ο χρήστης θέλει κάτι που ταιριάζει με αυτό το προϊόν)"
            if lang == "el"
            else "(The user wants something that matches this product)"
        )
        ref_lines = [rh, rn, f"  Name: {ref_product.name}"]
        if ref_product.url:
            ref_lines.append(f"  URL: {ref_product.url}")
        ref_lines.append(f"  Details: {ref_product.chunk_text[:400]}")
        ref_meta = ref_product.metadata or {}
        if ref_meta.get("price"):
            ref_lines.append(f"  Price: {ref_meta['price']} EUR")
        if ref_meta.get("categories"):
            ref_lines.append(f"  Categories: {', '.join(ref_meta['categories'])}")
        parts.extend(ref_lines)
        parts.append("")

    if len(catalog_for_prompt) == 0:
        parts.append(_build_json_output_instruction(lang, 0, False, no_results_flow=True))
        q_label = "ΕΡΩΤΗΣΗ ΧΡΗΣΤΗ" if lang == "el" else "USER QUESTION"
        parts.extend([f"{q_label}: {query}", "", "JSON:"])
        return "\n".join(parts)

    if relaxed_catalog:
        if lang == "el":
            parts.append(
                "ΣΗΜΕΙΩΣΗ: Η αναζήτηση βρήκε προϊόντα αλλά κάποια δεν πέρασαν αυτόματο έλεγχο URL/stock στη βάση. "
                "Χρησιμοποίησε την παρακάτω λίστα για προτάσεις· πρότεινε σχετικά SKU στο JSON. "
                "ΜΗΝ πεις ότι ο κατάλογος είναι άδειος.\n"
            )
        else:
            parts.append(
                "NOTE: Search returned products but some rows failed automatic URL/stock validation in our DB. "
                "Use the list below; include matching SKUs in JSON. Do NOT say the catalog is empty.\n"
            )
        parts.append("")

    target_block = _build_product_context(catalog_for_prompt, lang)
    parts.extend([target_block, ""])

    if intent.intent_type == "complementary" and intent.target_category:
        if lang == "el":
            parts.append(
                f"ΟΔΗΓΙΑ STYLING: Πρότεινε ΜΟΝΟ {intent.target_category} που ταιριάζουν "
                f"στυλιστικά με το προϊόν αναφοράς.\n"
            )
        else:
            parts.append(
                f"STYLING INSTRUCTION: Recommend ONLY {intent.target_category} that "
                f"stylistically match the reference product.\n"
            )
    elif intent.intent_type == "outfit":
        if lang == "el":
            parts.append(
                "ΟΔΗΓΙΑ STYLING: Πρότεινε ένα ολοκληρωμένο look σε λογική σειρά.\n"
            )
        else:
            parts.append(
                "STYLING INSTRUCTION: Suggest a complete look in logical fashion order.\n"
            )
    elif intent.target_category:
        if lang == "el":
            parts.append(
                f"ΟΔΗΓΙΑ: Πρότεινε ΜΟΝΟ {intent.target_category}.\n"
            )
        else:
            parts.append(
                f"INSTRUCTION: Recommend ONLY {intent.target_category}.\n"
            )

    if intent.style_keywords:
        kw = ", ".join(intent.style_keywords)
        parts.append(f"STYLE (user-explicit or inferred narrow terms only): {kw}\n")

    rm = retrieval_meta or {}
    tier = rm.get("match_tier") or "none"
    cc_eff = rm.get("color_canonical")

    cid = getattr(intent, "color_canonical", None) or getattr(intent, "color", None)
    if cc_eff:
        cid = cc_eff

    if tier == "alternatives" and catalog_for_prompt:
        label = cc_eff or cid or "the requested color"
        if lang == "el":
            parts.append(
                f"ΚΡΙΣΙΜΟ — ΠΑΡΟΜΟΙΕΣ ΕΠΙΛΟΓΕΣ (χρώμα): Στη βάση δεν εντοπίστηκαν αρκετά προϊόντα στο ακριβές χρώμα "
                f"(οικογένεια: {label}). Η λίστα καταλόγου παρακάτω περιέχει **σχετικές εναλλακτικές** "
                "(συχνά άλλο χρώμα). Στο message **πρέπει** να το πεις ρητά — π.χ. ότι δεν βρέθηκαν αρκετά "
                "στο συγκεκριμένο χρώμα και προτείνεις παρόμοια κομμάτια. "
                "ΜΗΝ πεις «δεν βρήκα τίποτα» ή «δεν υπάρχουν μαύρα/κόκκινα» αν η λίστα έχει προϊόντα.\n"
                "Στο JSON βάζεις μόνο SKU από τη λίστα· στο styling_reason εξήγησε αν το χρώμα διαφέρει από το ζητούμενο.\n"
            )
        else:
            parts.append(
                f"CRITICAL — COLOR ALTERNATIVES: Few items matched the exact color family «{label}». "
                "The catalog list below is **similar alternatives** (often another color). "
                "State this clearly in message. Do NOT say nothing was found if the list is non-empty.\n"
            )
    elif cid:
        parts.append(
            f"COLOR CONSTRAINT (hard): user asked for color family {cid}. "
            "Recommend ONLY products from the list below whose title/description matches this color. "
            "Do not present blue as red, pink as white, etc. If a product in the list does not match, skip it.\n"
        )
    if intent.target_category and cid and tier != "alternatives":
        cat = intent.target_category
        if lang == "el":
            parts.append(
                f"ΚΡΙΣΙΜΟ — ΚΑΤΗΓΟΡΙΑ + ΧΡΩΜΑ ΜΑΖΙ: Ο χρήστης ζήτησε «{cat}» και χρώμα (οικογένεια: {cid}). "
                "Στο JSON, products[] περιέχει ΜΟΝΟ SKU από τη λίστα που είναι σαφώς αυτή η κατηγορία "
                f"(π.χ. φορέματα όταν ζητά φορέματα) ΚΑΙ ταιριάζουν στο χρώμα. "
                "ΑΠΟΚΛΕΙΣΕ εντελώς από το JSON: σκουλαρίκια, δαχτυλίδια, κολιέ, βραχιόλια, τσάντες, παπούτσια, "
                "καπέλα, ζώνες κ.λπ. αν η αίτηση είναι για ρούχο/φόρεμα — αυτά ΔΕΝ μετράνε ως φόρεμα. "
                "Αν στη λίστα υπάρχουν μόνο άσχετα προϊόντα, άφησε products=[] και no_results=true με σύντομο μήνυμα.\n"
            )
        else:
            parts.append(
                f"CRITICAL — CATEGORY + COLOR: The user asked for «{cat}» and color family {cid}. "
                "products[] must ONLY include SKUs that are clearly that garment category AND match the color. "
                "EXCLUDE entirely: earrings, rings, necklaces, bracelets, bags, shoes, belts, hats, etc. "
                "when the request is for a dress/garment — jewelry is NOT a dress. "
                "If the list only has irrelevant SKUs, use products=[] and no_results=true with a short message.\n"
            )
    elif intent.target_category and tier == "alternatives":
        if lang == "el":
            parts.append(
                f"ΚΡΙΣΙΜΟ — ΚΑΤΗΓΟΡΙΑ: Ο χρήστης ζήτησε «{intent.target_category}». "
                "Από τη λίστα, προτίμησε SKU που ανήκουν σε αυτή την κατηγορία· απόκλεισε σκουλαρίκια/κοσμήματα/τσάντες "
                "αν ζητά φόρεμα.\n"
            )
        else:
            parts.append(
                f"CRITICAL — CATEGORY: Prefer SKUs in «{intent.target_category}»; exclude jewelry/bags if the user asked for garments.\n"
            )
    elif intent.target_category:
        if lang == "el":
            parts.append(
                f"ΚΡΙΣΙΜΟ — ΚΑΤΗΓΟΡΙΑ: Ο χρήστης ζήτησε «{intent.target_category}». "
                "Μην βάζεις στο JSON προϊόντα άλλης κατηγορίας (π.χ. σκουλαρίκια όταν ζητά φορέματα). "
                "Αγνόησε άσχετα SKU από τη λίστα.\n"
            )
        else:
            parts.append(
                f"CRITICAL — CATEGORY: The user asked for «{intent.target_category}». "
                "Do not put other categories in JSON (e.g. earrings when they asked for dresses). "
                "Ignore irrelevant SKUs from the list.\n"
            )
    occ = getattr(intent, "occasion_key", None)
    if occ:
        if lang == "el":
            parts.append(
                f"ΠΕΡΙΣΤΑΣΗ (μαλακό σήμα, όχι κρυφό κριτήριο υφάσματος/σιλουέτας): κωδικός={occ}. "
                "Χρησιμοποίησέ το για τόνο και styling_reason στο catalog, όχι σαν να ζήτησε ρητά glam/satin/bodycon αν δεν εμφανίζονται στο μήνυμα χρήστη. "
                "Μην πεις «δεν βρήκα ακριβώς glam/satin» αν ο χρήστης ζήτησε απλώς βραδινή έξοδο.\n"
            )
        else:
            parts.append(
                f"OCCASION (soft context — not a hidden fabric/silhouette requirement): key={occ}. "
                "Use for tone and styling_reason; do NOT claim the user asked for glam/satin/bodycon unless they did. "
                "Do not say no exact match for glam if they only asked for an evening dress.\n"
            )
    if occ == "gift":
        if lang == "el":
            parts.append(
                "ΔΩΡΟ: Προτίμησε σετ, αξεσουάρ, καλλυντικά/lifestyle ή κομμάτια με αισθητική· "
                "απόφυγε generic basics εκτός αν ζητηθούν ρητά. Φίλτραρε αδύναμα matches από το JSON.\n"
            )
        else:
            parts.append(
                "GIFT: Prefer sets, accessories, beauty/lifestyle, or distinctive aesthetic pieces; "
                "avoid generic basics unless explicitly requested. Omit weak matches from JSON.\n"
            )

    # Popular products intent: honest disclaimer + popularity_reason/badge (no fake "most popular" claim)
    is_popular = (intent.intent_type or "").lower() == "popular_products"
    if is_popular:
        if lang == "el":
            parts.append(
                "ΕΡΩΤΗΣΗ ΔΗΜΟΦΙΛΙΑΣ: Ο χρήστης ρωτά για πιο δημοφιλή / best sellers. "
                "Δεν έχουμε live δεδομένα πωλήσεων. Στο message να είσαι ειλικρινής: π.χ. "
                "\"Δεν έχω αυτή τη στιγμή live δεδομένα πωλήσεων, αλλά μπορώ να σου δείξω επιλογές που ξεχωρίζουν στο MOSTY.\" "
                "Για κάθε προϊόν χρησιμοποίησε popularity_reason (γιατί προτείνεται) και badge (π.χ. \"Featured\", \"Τα πιο αγαπημένα\").\n\n"
            )
        else:
            parts.append(
                "POPULARITY QUERY: The user asks for most popular / best sellers. "
                "We do not have live sales data. In message be honest: e.g. "
                "\"I don't have live sales data right now, but I can show you picks that stand out at MOSTY.\" "
                "For each product use popularity_reason and badge (e.g. \"Featured\", \"Top picks\").\n\n"
            )

    parts.append(
        _build_json_output_instruction(
            lang, len(catalog_for_prompt), is_popular, no_results_flow=False
        )
    )

    q_label = "ΕΡΩΤΗΣΗ ΧΡΗΣΤΗ" if lang == "el" else "USER QUESTION"
    parts.extend([f"{q_label}: {query}", "", "JSON:"])

    return "\n".join(parts)
