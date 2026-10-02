# Διαχείριση context (conversation memory) στο ELIZA AI AGENT

## 1. Conversation Context Logic

**Το σύστημα κρατάει context της συνομιλίας.** Κάθε ερώτηση **δεν** αντιμετωπίζεται ως ανεξάρτητη.

- Ο χρήστης στέλνει μήνυμα με `session_id` (ή κενό για νέο chat).
- Το backend συσχετίζει το μήνυμα με ένα **session** (υπάρχον ή νέο).
- Το **ιστορικό της συνομιλίας** (user + assistant turns) αποθηκεύεται ανά session και **φορτώνεται** πριν την επεξεργασία του νέου μηνύματος.
- Το φιλτραρισμένο ιστορικό (τελευταία N γύροι) **περνάει στο prompt** του AI ώστε το μοντέλο να λαμβάνει υπόψη προηγούμενα μηνύματα (π.χ. budget, κατηγορία, στυλ).

**Συνοπτικά:**
- **Intent extraction:** το `conversation_context` περνάει στο `build_intent_prompt` ώστε το μοντέλο να ταξινομήσει σωστά το intent (π.χ. follow-up).
- **Stylist prompt:** το ίδιο `conversation_context` περνάει στο `build_stylist_prompt` ώστε η απάντηση να συνδυάζει όλους τους περιορισμούς που έχουν ειπωθεί («φόρεμα για βραδινή» + «κάτω από 100€»).

---

## 2. Message Storage

### Χρήση `session_id`

- Κάθε συνομιλία αντιστοιχεί σε ένα **session** που αναγνωρίζεται από `session_id` (UUID-like string, π.χ. 16 hex chars).
- Το frontend:
  - Καλεί `POST /api/v1/chat/session` όταν ο χρήστης ξεκινά νέο chat (ή στέλνει πρώτο μήνυμα χωρίς session).
  - Στέλνει `session_id` στο body κάθε `POST /api/v1/chat/ask`.
- Το backend:
  - `MemoryService.get_or_create(session_id, language)`: αν υπάρχει session με αυτό το id το επιστρέφει, αλλιώς δημιουργεί νέο.
  - Όλες οι επόμενες κλήσεις (add_turn, get_conversation_context, update_session) γίνονται με αυτό το `session_id`.

### Πού αποθηκεύονται τα μηνύματα

- **Θέση:** In-memory στο process του backend, σε λεξικό `MemoryService._store: dict[session_id, _SessionEntry]`.
- **Δεν** χρησιμοποιείται database ή Redis στην τρέχουσα υλοποίηση· το module σχολιάζει ότι είναι "designed to be replaced by Redis / Firestore".
- Σε restart του backend χάνεται όλο το ιστορικό.

### Ποια fields αποθηκεύονται ανά μήνυμα

Κάθε μήνυμα αποθηκεύεται ως **ConversationTurn** (Pydantic model):

| Field      | Type | Περιγραφή |
|-----------|------|-----------|
| `role`    | str  | `"user"` ή `"assistant"` |
| `content` | str  | Το κείμενο του μηνύματος (user query ή assistant answer) |
| `timestamp` | str | ISO 8601 UTC, π.χ. `"2025-03-16T12:00:00Z"` |

Τα turns κρατούνται στη λίστα `ChatSession.conversation_history: list[ConversationTurn]`.

**Επιπλέον ανά session (ChatSession):**

| Field | Χρήση |
|-------|--------|
| `session_id` | Αναγνωριστικό session |
| `created_at`, `updated_at` | Timestamps |
| `language` | Γλώσσα συνομιλίας |
| `user_preferences` | Budget, προτιμήσεις (από session) |
| `conversation_history` | Λίστα ConversationTurn |
| `retrieved_skus` | SKU που έχουν ήδη προταθεί (για αποφυγή επαναλήψεων) |
| `last_query`, `last_answer` | Τελευταία user query και assistant απάντηση |

---

## 3. Δομή αποθήκευσης – παράδειγμα

**Session (αντικείμενο στη μνήμη):**

```
session_id: "a1b2c3d4e5f67890"
created_at: "2025-03-16T10:00:00Z"
updated_at: "2025-03-16T10:05:00Z"
language: "el"
conversation_history: [
  { "role": "user", "content": "Θέλω ένα φόρεμα για βραδινή έξοδο", "timestamp": "2025-03-16T10:00:00Z" },
  { "role": "assistant", "content": "Σου προτείνω αυτές τις επιλογές...", "timestamp": "2025-03-16T10:00:05Z" },
  { "role": "user", "content": "Να είναι κάτω από 100€", "timestamp": "2025-03-16T10:01:00Z" }
]
retrieved_skus: ["SKU1", "SKU2", ...]
last_query: "Να είναι κάτω από 100€"
last_answer: "..."
```

**Διαδοχική εγγραφή:** Κάθε φορά που ο χρήστης στέλνει μήνυμα ή το assistant απαντά, καλείται `memory.add_turn(session_id, role, content)`, που:
1. Φτιάχνει ένα `ConversationTurn(role=role, content=content, timestamp=now)`.
2. Το append-άρει στο `entry.session.conversation_history`.
3. Αν `len(conversation_history) > 40`, κρατά μόνο τα **τελευταία 40** turns (trim από την αρχή).

---

## 4. Χρήση του stored history όταν ο χρήστης στέλνει νέο μήνυμα

### Πώς φορτώνεται το conversation history

1. Στο `ChatService.ask()`:
   - `session = self._memory.get_or_create(request.session_id, language=lang)`
   - `context = self._memory.get_conversation_context(session.session_id)`
2. Το `get_conversation_context(session_id, max_turns=10)`:
   - Παίρνει το session από το `_store`.
   - Παίρνει τα **τελευταία 10 turns** (5 ζεύγη user–assistant):  
     `history = entry.session.conversation_history[-max_turns:]`
   - Τα μορφοποιεί σε ένα string: γραμμές `"User: ..."` και `"Assistant: ..."` (content περικοπή στα 600 chars ανά turn).
3. Αυτό το string επιστρέφεται ως `context` (κενό αν δεν υπάρχει ιστορικό).

### Πόσα προηγούμενα μηνύματα χρησιμοποιούνται

- **Στο prompt:** Μόνο τα **τελευταία 10 turns** (default `max_turns=10` στο `get_conversation_context`).
- Στη **αποθήκευση** κρατούνται μέχρι **40 turns**· πάνω από αυτά γίνεται trim και μένουν μόνο τα νεότερα.

### Πώς περνάνε στο prompt του AI

- Το `context` (string) περνάει ως `conversation_context` στα:
  - `build_intent_prompt(query, conversation_context, lang)` → για intent extraction.
  - `build_stylist_prompt(..., conversation_context=context, ...)` → για την κύρια απάντηση stylist.
- Στο `prompt_builder`, το `conversation_context` εμφανίζεται ως block πριν τα προϊόντα, π.χ.:

```
ΙΣΤΟΡΙΚΟ ΣΥΝΟΜΙΛΙΑΣ:
User: Θέλω ένα φόρεμα για βραδινή έξοδο
Assistant: Σου προτείνω αυτές τις επιλογές...
User: Να είναι κάτω από 100€

(Λάβε υπόψη ΟΛΟΥΣ τους περιορισμούς που ανέφερε ο χρήστης)
```

Έτσι το μοντέλο βλέπει ρητά τα προηγούμενα μηνύματα και τις οδηγίες να συνδυάζει περιορισμούς.

---

## 5. Πώς αποφεύγεται να μεγαλώνει υπερβολικά το prompt

- **Τελευταία N γύροι:** Μόνο τα τελευταία **10 turns** περνούν στο context (`max_turns=10`). Οι παλαιότερες συνεχίζουν να υπάρχουν στο session μέχρι 40, αλλά δεν μπαίνουν στο prompt.
- **Περικοπή ανά turn:** Κάθε turn περικόπτεται σε **600 χαρακτήρες** στο `get_conversation_context`:  
  `content = turn.content[:600]`
- **Trim ιστορικού:** Αν το `conversation_history` ξεπεράσει **40** turns, κρατούνται μόνο τα τελευταία 40.
- **Δεν** υπάρχει ακόμα: summary της συζήτησης, token counting, ή dynamic N με βάση token limit. Το N είναι σταθερό (10) και το μέγεθος ανά turn σταθερό (600 chars).

---

## 6. Παράδειγμα πραγματικού prompt με history

**Συνομιλία:**
- User: «Θέλω ένα φόρεμα για βραδινή έξοδο»
- Assistant: «Σου προτείνω αυτές τις επιλογές…»
- User: «Να είναι κάτω από 100€»

**Βήματα backend:**
1. Για το τρίτο μήνυμα, φορτώνεται `context = get_conversation_context(session_id)` που περιέχει τα δύο πρώτα turns (+ τυχόν άλλα μέχρι 10).
2. Το `context` περνά στο intent prompt και στο stylist prompt.
3. Στο **stylist prompt** το block είναι κάπως έτσι (με τα προϊόντα από RAG να ακολουθούν):

```
ΚΑΝΟΝΕΣ:
...

ΙΣΤΟΡΙΚΟ ΣΥΝΟΜΙΛΙΑΣ:
User: Θέλω ένα φόρεμα για βραδινή έξοδο
Assistant: Σου προτείνω αυτές τις επιλογές...
User: Να είναι κάτω από 100€

(Λάβε υπόψη ΟΛΟΥΣ τους περιορισμούς που ανέφερε ο χρήστης)

ΠΡΟΪΟΝΤΑ ΑΠΟ ΚΑΤΑΛΟΓΟ:
...
ΕΡΩΤΗΣΗ ΧΡΗΣΤΗ: Να είναι κάτω από 100€

JSON:
```

**Γιατί «καταλαβαίνει» το budget:** Το μοντέλο δεν έχει ξεχωριστό slot «budget»· βλέπει το ιστορικό και την τρέχουσα ερώτηση. Οι οδηγίες λένε ρητά «Λάβε υπόψη ΟΛΟΥΣ τους περιορισμούς». Έτσι ερμηνεύει το «Να είναι κάτω από 100€» ως περιορισμό για το ίδιο αίτημα (φόρεμα για βραδινή). Το retrieval (filters) μπορεί επιπλέον να χρησιμοποιεί `last_query` / filters από session (π.χ. budget από user_preferences) αν το pipeline το περνάει στα filters.

---

## 7. Νέο chat, reset, λήξη session

### Όταν ξεκινά νέο chat (frontend)

- Ο χρήστης πατά «New chat» (ή αντίστοιχο).
- Frontend: `activeConversationId = null`, `sessionId = null`, νέο conversation object με `session_id: null`.
- Στην **επόμενη** αποστολή μηνύματος: `session_id` είναι null → καλείται `createSession()` (POST /chat/session) → νέο `session_id` → το request πηγαίνει με αυτό το id. Backend δημιουργεί **νέο** session και **κενό** ιστορικό.

### Reset conversation

- **Frontend:** «Clear chat» συνήθως σημαίνει νέο conversation (ίδια ροή με νέο chat) ή διαγραφή μηνύματα μόνο στο UI. Το backend **δεν** καλείται απαραίτητα για delete session.
- **Backend:** `DELETE /api/v1/chat/session/{session_id}` διαγράφει το session από το `_store` (`memory.delete_session(session_id)`). Μετά, οποιοδήποτε request με αυτό το id θα λήξει σε `get_or_create` → νέο session (ίδιο id μπορεί να ξαναχρησιμοποιηθεί μετά το delete, αφού το entry έχει φύγει).

### Λήξη session (TTL)

- Κάθε φορά που γίνεται `get_session(session_id)` ή `add_turn`, ενημερώνεται `entry.last_access = time.monotonic()`.
- Πριν από create/get, τρέχει `_prune_expired()`: αφαιρεί sessions όπου `last_access < cutoff` με `cutoff = now - MEMORY_TTL_MINUTES*60`.
- Default `MEMORY_TTL_MINUTES = 60`. Μετά από 60 λεπτά αδράνειας το session θεωρείται expired και **διαγράφεται** στο επόμενο prune· το ιστορικό χάνεται. Request με το παλιό session_id θα πάρει νέο session (get_session → None → get_or_create → create_session).

---

## 8. Best practices για conversation context σε AI shopping assistants

### Session management

- **Ένα session_id ανά συνομιλία (tab/sidebar):** Όπως κάνει ήδη το frontend: ένα session ανά conversation, αποθηκευμένο στο conversation object.
- **Νέο chat = νέο session:** Νέο `session_id` ώστε να μην μπερδεύονται context από διαφορετικές συνομιλίες.
- **Επιλογή να καλείται DELETE /session** όταν ο χρήστης κλείνει/διαγράφει μια συνομιλία, ώστε να μην μένουν «orphan» sessions στη μνήμη.

### Context window size

- **Συμβιβασμός βάθους vs tokens:** Τα τελευταία **10 turns** (5 ζεύγη) είναι λογικό για shopping· συνήθως αρκούν για «φόρεμα → κάτω από 100€ → με μανίκι» χωρίς να φουσκώνει το prompt.
- **Περικοπή ανά turn (600 chars):** Μειώνει το μέγεθος χωρίς να κόβει συνήθως το νόημα. Μπορεί να γίνει ρυθμίσιμο (config) ή να βασίζεται σε token count αν χρησιμοποιείται tokenizer.

### Caching

- Τα αποτελέσματα RAG (products) **δεν** cache-άρονται ανά turn στο memory· κάθε νέο μήνυμα = νέα αναζήτηση. Το cache (CacheService) μπορεί να χρησιμοποιείται αλλού (π.χ. search results). Για conversation context, το «cache» είναι απλά το in-memory ιστορικό· αν μεταφερθεί σε Redis, το session store θα λειτουργεί σαν distributed cache.

### Persistence strategy

- **Τώρα:** In-memory, TTL 60 λεπτά. Απλό, χωρίς εξαρτήσεις· χάνεται σε restart και μετά από TTL.
- **Συνιστάται για production:**
  - **Redis:** Session data (session_id → ChatSession ή serialized history) με TTL. Ίδιο API (get/set/delete), επιπλέον persistence και διασπορά ανά instance.
  - **Database (π.χ. PostgreSQL):** Πίνακας `sessions`, πίνακας `turns` (session_id, role, content, timestamp). Μόνιμη αποθήκευση, δυνατότητα analytics. Περικοπή (π.χ. τελευταία 40 turns ανά session) μπορεί να γίνεται periodic ή on-read.
- **Υβριδικό:** Redis για ζεστό context (τελευταία N turns) + DB για long-term ιστορικό αν χρειάζεται.

### Επιπλέον συμβουλές

- **Συνοπτικό context (summary):** Για πολύ long conversations μπορεί να προστεθεί optional βήμα: ένα summary των παλιών turns (π.χ. μέχρι 20) και μόνο τα τελευταία 5–10 full turns στο prompt.
- **Token limit:** Αν το model έχει context limit, μπορεί να υπολογίζεται το μέγεθος του `conversation_context` (και του product block) και να μειώνεται δυναμικά το `max_turns` ή το `max_chars_per_turn` ώστε το συνολικό prompt να μην ξεπερνά το όριο.
- **User preferences:** Τα `user_preferences` (π.χ. budget_min/max) που ενημερώνονται από τη συνομιλία είναι καλή πρακτική για να εφαρμόζονται και στα filters του retrieval, όχι μόνο στο prompt.

---

## Σύνοψη

| Θέμα | Υλοποίηση ELIZA AI AGENT |
|------|---------------------|
| Context | Ναι, κρατάει ιστορικό ανά session |
| Αποθήκευση | In-memory dict, key = session_id |
| Ανά μήνυμα | role, content, timestamp (ConversationTurn) |
| Πόσα στο prompt | Τελευταία 10 turns, έως 600 chars/turn |
| Μέγιστο ιστορικό | 40 turns, μετά trim |
| Όριο μεγέθους | 600 chars/turn, σταθερό N=10 |
| Νέο chat | νέο session_id (createSession) |
| Reset | DELETE /session ή νέο chat |
| Λήξη | TTL 60 min (MEMORY_TTL_MINUTES) |
