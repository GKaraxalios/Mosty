"""Optional LLM blocks for production conversational shopping (additive)."""
from __future__ import annotations


def build_shopping_assistant_block(
    lang: str,
    *,
    exclude_shown_applied: bool,
    relaxed_duplicate_skus: bool,
    repeated_user_message: bool,
) -> str | None:
    """Extra instructions for stylist JSON prompt; None if nothing to add."""
    parts: list[str] = []
    if lang == "el":
        if repeated_user_message:
            parts.append(
                "ΚΡΙΣΙΜΟ — ΕΠΑΝΑΛΗΨΗ ΜΗΝΥΜΑΤΟΣ: Ο χρήστης έστειλε ουσιαστικά το ίδιο ερώτημα. "
                "Μην αντιγράψεις την προηγούμενη απάντηση αυτούσια· δώσε νέα γωνία, διαφορετική διατύπωση, "
                "ή εξήγησε ειλικρινά αν ο κατάλογος δεν αφήνει νέες επιλογές."
            )
        if exclude_shown_applied:
            parts.append(
                "ΚΡΙΣΙΜΟ — ΣΥΝΕΧΕΙΑ / REFINEMENT: Η λίστα προϊόντων προτιμά κομμάτια που δεν έχουν ήδη προταθεί "
                "σε προηγούμενη στροφή (όπου υπάρχουν). Αναγνώρισε το refinement (ύφος, budget, «πιο X») "
                "στο message· εξήγησε σύντομα γιατί οι νέες επιλογές ταιριάζουν καλύτερα. "
                "Μην παρουσιάζεις ξανά τα ίδια προϊόντα με διαφορετικό κείμενο χωρίς λόγο."
            )
        if relaxed_duplicate_skus:
            parts.append(
                "ΔΙΑΘΕΣΙΜΟΤΗΤΑ: Λόγω περιορισμένων εναλλακτικών στον κατάλογο, κάποια προϊόντα μπορεί να επαναφαίνονται. "
                "Στο message πες το ειλικρινά και φυσικά (χωρίς τεχνικές λεπτομέρειες)."
            )
    else:
        if repeated_user_message:
            parts.append(
                "CRITICAL — REPEATED MESSAGE: The user sent essentially the same question again. "
                "Do not duplicate the previous answer verbatim; add a new angle or explain honestly if "
                "the catalog leaves little room for new picks."
            )
        if exclude_shown_applied:
            parts.append(
                "CRITICAL — CONTINUITY / REFINEMENT: The product list prefers items not already suggested "
                "earlier in this chat when possible. Acknowledge the refinement in message; explain briefly "
                "why these picks better match. Do not re-present the same products with only rephrased text."
            )
        if relaxed_duplicate_skus:
            parts.append(
                "AVAILABILITY NOTE: Because alternatives in-catalog are limited, some items may reappear. "
                "Say so honestly and naturally in message."
            )
    if not parts:
        return None
    return "\n\n".join(parts)
