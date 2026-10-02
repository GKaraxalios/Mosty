# Αναλυτική διάγνωση: Αποθήκευση συνομιλιών (πριν την αλλαγή σε MySQL)

## 1. Πού αποθηκεύονται τώρα οι συνομιλίες

| Στοιχείο | Θέση | Τύπος |
|----------|------|--------|
| Sessions | `MemoryService._store: dict[str, _SessionEntry]` | In-memory dict στο process |
| Κάθε entry | `_SessionEntry(session: ChatSession, last_access: float)` | In-memory object |
| Messages | `ChatSession.conversation_history: list[ConversationTurn]` | List μέσα στο session object |

**Δεν υπάρχει:** αρχείο, JSON, Redis, database. Μόνο RAM. Σε restart ή TTL expiry τα δεδομένα χάνονται.

---

## 2. Create session / Append message / Read history / Delete

| Λειτουργία | Πώς γίνεται |
|------------|-------------|
| **Create session** | `MemoryService.create_session(language)` → νέο UUID(16), ChatSession, εγγραφή στο `_store`. |
| **Get session** | `get_session(session_id)` → `_store.get(session_id)` με prune expired. |
| **Get or create** | `get_or_create(session_id, language)` → αν session_id και υπάρχει return it, αλλιώς `create_session(language)`. |
| **Append message** | `add_turn(session_id, role, content)` → νέο ConversationTurn, append στο `conversation_history`, trim αν > 40. |
| **Read history** | `get_conversation_context(session_id, max_turns=10)` → τελευταία 10 turns, format "User: ...\nAssistant: ...", content περικοπή 600 chars. |
| **Delete session** | `delete_session(session_id)` → `_store.pop(session_id)`. |
| **Update session** | `update_session(session_id, **kwargs)` → setattr στο session, updated_at = now. |
| **Add retrieved SKUs** | `add_retrieved_skus(session_id, skus)` → extend `retrieved_skus` χωρίς duplicates. |
| **Update preferences** | `update_preferences(session_id, **kwargs)` → setattr στο `user_preferences`. |

---

## 3. Endpoints / functions που χρησιμοποιούν τον μηχανισμό

| Αρχείο | Σημεία |
|--------|--------|
| **app/routers/chat.py** | `create_session` → memory.create_session; `get_session` → memory.get_session; `delete_session` → memory.delete_session; `chat_ask`, `chat_recommend`, `chat_compare` → ChatService (που χρησιμοποιεί memory). |
| **app/services/chat_service.py** | Όλα τα `self._memory.*`: get_or_create, get_conversation_context, add_turn, add_retrieved_skus, update_session. |
| **app/services/memory_service.py** | Μοναδική υλοποίηση storage (in-memory). |
| **app/core/config.py** | MEMORY_ENABLED, MEMORY_TTL_MINUTES, DEFAULT_ASSISTANT_LANGUAGE. |

Δεν υπάρχουν άλλα routers ή services που να γράφουν/διαβάζουν συνομιλίες.

---

## 4. Προβλήματα τωρινού τρόπου

- **Δεν επιζεί σε restart:** Όλο το ιστορικό χάνεται.
- **Δεν scale σε πολλά instances:** Κάθε process έχει δικό του _store.
- **TTL 60 min:** Μετά αδράνειας το session διαγράφεται (prune), χωρίς επιλογή να κρατήσουμε ιστορικό.
- **Όριο 40 turns:** Trim in-memory· δεν υπάρχει long-term retention ή analytics.
- **Όχι list sessions / clear history:** Τα endpoints δεν προσφέρονται.

---

## 5. Architecture map (current flow)

```
Client
  │
  ├─ POST /chat/session ──────────────► get_memory_service().create_session()
  ├─ GET  /chat/session/{id} ─────────► get_memory_service().get_session(id)
  ├─ DELETE /chat/session/{id} ───────► get_memory_service().delete_session(id)
  └─ POST /chat/ask (body: session_id, query)
        │
        ▼
     ChatService.ask(request)
        │
        ├─ get_or_create(request.session_id)  ──► _store.get / create_session
        ├─ get_conversation_context(session_id) ► _store[sid].session.conversation_history[-10:]
        ├─ ... LLM / RAG ...
        ├─ add_turn(session_id, "user", query)
        ├─ add_turn(session_id, "assistant", answer)
        ├─ add_retrieved_skus(session_id, skus)
        └─ update_session(session_id, last_query=..., last_answer=..., language=...)
```

**Σύνοψη:** Ένα singleton `MemoryService` κρατάει dict in-memory. Δεν υπάρχει abstraction layer· το ChatService καλεί απευθείας `get_memory_service()`.
