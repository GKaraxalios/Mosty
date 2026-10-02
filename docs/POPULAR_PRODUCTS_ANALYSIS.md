# Ανάλυση προβλήματος: «Ποια είναι τα πιο δημοφιλή προϊόντα σας;»

## 1. Root cause analysis

- **Λάθος intent:** Το ερώτημα ταξινομείται ως `recommendation` ή `product_search`. Το σύστημα δεν έχει intent `popular_products` / `best_sellers`, οπότε τρέχει η ίδια ροή με styling (RAG + styling_reason).
- **Έλλειψη πηγής δημοφιλίας:** Δεν υπάρχει popularity ranking (sales, views, best sellers). Τα αποτελέσματα προέρχονται από semantic search στο query, όχι από μετρήσεις δημοφιλίας.
- **Λάθος πεδίο εξήγησης:** Το prompt ζητά `styling_reason` (για styling/outfit). Για ερωτήματα δημοφιλίας χρειάζεται `popularity_reason` / badge.
- **Raw JSON στο UI:** Όταν το LLM επιστρέφει JSON και το parse δίνει μήνυμα αλλά το backend σε κάποιες περιπτώσεις στέλνει `answer=raw_answer` (π.χ. όταν styling_map κενό ή fallback), το frontend λαμβάνει ολόκληρο το JSON και το εμφανίζει ως κώδικα.
- **Truncation:** Το JSON κόβεται στη μέση επειδή η απάντηση ξεπερνάει το `max_output_tokens` (2048). Το LLM γράφει πολλά προϊόντα με μεγάλα styling_reason και η απάντηση περικόπτεται → ελλιπές JSON → parse failure ή partial parse → επιστροφή raw string.

## 2. Why the current output is wrong

- **Business logic:** Παρουσιάζονται ως «δημοφιλή» προϊόντα που προέκυψαν από semantic search, χωρίς δεδομένα πωλήσεων/views. Τα styling_reason αφορούν στυλ, όχι δημοφιλία. Διάφορα ασύνδετα categories (δαχτυλίδια, κεριά, γούνα, κτλ.) δείχνουν ότι η «δημοφιλία» δεν βασίζεται σε ranking.
- **Frontend:** Εμφάνιση raw JSON σε `<pre><code>` αντί για φυσιολογικό μήνυμα. Λέιμπλ "Recommendations" αντί για "Best sellers" / "Popular now". Χρήση styling phrasing (product-styling) αντί για popularity_reason/badge.
- **Backend response design:** Επιστροφή `answer=raw_answer` όταν το parsed message θεωρείται κενό ή σε fallback path. Μία μόνο μορφή απάντησης (styling_reason), χωρίς intent ή metadata (π.χ. ranking_source).
- **UX:** Ο χρήστης ρωτά για δημοφιλία και βλέπει styling tips και τυχαία mix κατηγοριών, plus σπασμένο JSON.

## 3. Correct logic for popularity-type queries

- **«Πιο δημοφιλή προϊόντα» / «best sellers» / «τι αγοράζουν περισσότερο» / «trending»:** Να αντιμετωπίζονται ως **popular_products** (ή best_sellers/trending) και να ξεχωρίζονται από:
  - **Styling queries:** τι να φορέσω, outfit, τι ταιριάζει → styling_reason.
  - **Generic product recommendation:** «πρότεινέ μου φορέματα» → styling_reason.
  - **Discovery/inspiration:** «δείξε μου ιδέες» → μπορεί να overlap αλλά με διαφορετικό framing.

Για popular_products:
- Αν υπάρχει πραγματικό popularity source (Magento reports, indexed best_sellers, views) → επιστροφή top products με `popularity_reason` και `badge`.
- Αν **δεν** υπάρχει αξιόπιστο signal → μην ισχυριζόμαστε «τα πιο δημοφιλή». Fallback: ειλικρινές μήνυμα («Δεν έχω live δεδομένα πωλήσεων, αλλά μπορώ να σου δείξω επιλογές που ξεχωρίζουν») και προαιρετικά featured/curated picks με ετικέτα «Featured» αντί «Best seller».

## 4. Separation from other intents

- **Intent detection:** Νέο `intent_type`: `popular_products` (και συναφή: best_sellers, trending).
- **Product retrieval:** Για popular_products, ιδανικά dedicated endpoint ή filter (best_sellers, order_count, κτλ.). Μέχρι να υπάρχει, retrieval μένει semantic αλλά το **prompt** και το **response contract** αλλάζουν (popularity_reason, badge, honest message).
- **Response schema:** Ίδιο endpoint αλλά με optional `intent`, `metadata.ranking_source`, και στα products optional `popularity_reason`, `badge`.

## 5. Exact fixes

### A. Backend intent detection
- Στο intent schema και rules: πρόσθεσε `popular_products` (και best_sellers / trending σαν ίδιο ή υποτύπο).
- Κανόνας: αν το query ρωτά ρητά για δημοφιλία / best sellers / τι αγοράζουν / trending → `intent_type = "popular_products"`.

### B. Product retrieval
- **Τώρα:** Δεν υπάρχει popularity source. Κράτα semantic search αλλά με prompt που δεν ισχυρίζεται δημοφιλία.
- **Μέλλον:** Να προστεθεί πηγή (Magento reports, custom attribute best_seller, view count, κτλ.) και να τρέχει ξεχωριστό ranking ή filter όταν intent = popular_products.

### C. Response schema
- **ProductCard:** `styling_reason` (υπάρχει), πρόσθεσε `popularity_reason: str = ""`, `badge: str = ""`.
- **ChatResponse:** optional `intent: str | None`, `metadata: dict | None` (π.χ. `ranking_source`).
- Στο LLM output format: για popular_products να υποστηρίζεται `popularity_reason` και `badge` αντί (ή παράλληλα) με styling_reason.

### D. Frontend rendering
- Να μην εμφανίζεται ποτέ raw JSON ως κείμενο απάντησης: πάντα να χρησιμοποιείται το parsed `message` όταν υπάρχει (backend να επιστρέφει πάντα `answer=message` όταν το parse δίνει μήνυμα).
- Για `intent === "popular_products"`: τίτλος carousel "Best sellers" / "Popular now" / "Top picks", χρήση `popularity_reason` για το subtitle του card, εμφάνιση `badge` (π.χ. "Best seller", "Trending") αν υπάρχει.
- Για styling intents: όπως τώρα (styling_reason, "Recommendations").

## 6. Problems A–E (summary)

- **A. Wrong intent:** Διόρθωση με νέο intent_type `popular_products` και ενημέρωση intent prompt/rules.
- **B. No popularity source:** Τώρα: honest fallback message + optional featured results. Μέλλον: σύνδεση με Magento/popularity data.
- **C. Wrong field:** Νέο πεδίο `popularity_reason` (και optional `badge`). Για popular_products το LLM να γεμίζει αυτά αντί για styling_reason.
- **D. Raw JSON:** Backend πάντα να επιστρέφει `answer=message` όταν το parsed JSON έχει μήνυμα· frontend να μην εμφανίζει ποτέ το raw body ως κώδικα.
- **E. Truncation:** Αύξηση `max_output_tokens` για stylist response και/ή πιο συμπαγής οδηγίες (λιγότερα προϊόντα, μικρότερα reasons). Επίσης πιο defensive parse ώστε ακόμα και κομμένο JSON να δίνει message.

## 7. Ideal response behavior

Για «Ποια είναι τα πιο δημοφιλή προϊόντα σας;»:

- **Αν υπάρχει πραγματικό popularity dataset:** Επιστροφή top N με `popularity_reason` και `badge`, και `metadata.ranking_source`.
- **Αν δεν υπάρχει αξιόπιστο signal:** Μήνυμα τύπου: «Δεν έχω αυτή τη στιγμή live δεδομένα πωλήσεων, αλλά μπορώ να σου δείξω επιλογές που ξεχωρίζουν στο MOSTY.» και προαιρετικά featured/curated products με badge "Featured" και χωρίς ψευδή ισχυρισμό δημοφιλίας.

## 8. New response contract (example)

```json
{
  "message": "Αυτά είναι μερικά από τα πιο δημοφιλή προϊόντα μας αυτή την περίοδο.",
  "intent": "popular_products",
  "products": [
    {
      "sku": "...",
      "name": "...",
      "popularity_reason": "Ανήκει στα best sellers της κατηγορίας του",
      "badge": "Best seller",
      "styling_reason": ""
    }
  ],
  "metadata": {
    "ranking_source": "best_sellers_last_30_days"
  }
}
```

Όταν δεν υπάρχει πραγματικό ranking:
- `message`: honest disclaimer.
- `metadata.ranking_source`: "curated" ή "featured" ή απουσία.
- `badge`: "Featured" αντί "Best seller".

## 9. Conditional UI behavior

- **intent = popular_products:** Τίτλος "Popular now" / "Best sellers" / "Top picks", χρήση `popularity_reason` για λεζάντα, εμφάνιση badge. Μην δείχνουμε styling phrasing ως κύρια εξήγηση.
- **Δεν υπάρχει αληθινό popularity source:** Μήνυμα fallback στο chat· μην ισχυριζόμαστε ότι τα προϊόντα είναι «τα πιο δημοφιλή».

## 10. Optional Magento data sources for popularity

- Sales: order items count, quantity ordered per product/SKU.
- Reports: Magento Reports → Bestsellers, Most Viewed.
- Custom attributes: `is_bestseller`, `view_count`, `sales_count` (αν indexάρονται).
- Categories: "Best sellers" category ή "Featured" με hand-picked products.
- Revenue ή units sold για ranking score.

Μπορεί να γίνει sync αυτών των δεδομένων στο vector index ή σε ξεχωριστό popularity store και το retrieval για popular_products να βασίζεται σε αυτό.
