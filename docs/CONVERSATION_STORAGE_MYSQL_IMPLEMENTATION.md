# Υλοποίηση: Αποθήκευση συνομιλιών σε MySQL

## A. Αναλυτική διάγνωση (πριν)

Δείτε **[CONVERSATION_STORAGE_ANALYSIS.md](./CONVERSATION_STORAGE_ANALYSIS.md)** για:
- πού αποθηκεύονταν οι συνομιλίες (in-memory dict)
- create / append / read / delete flow
- ποια endpoints και services τα χρησιμοποιούσαν
- προβλήματα (απώλεια σε restart, no scale-out)

---

## B. Αρχεία που άλλαξαν / προστέθηκαν

| Αρχείο | Αλλαγή |
|--------|--------|
| **app/core/config.py** | + MEMORY_BACKEND, DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_CHARSET, DATABASE_URL |
| **app/services/memory_service.py** | Αντικαταστάθηκε: πλέον facade που κάνει delegate στο `ConversationRepository` |
| **app/repositories/conversation_repository.py** | **ΝΕΟ**: abstract interface + factory `get_conversation_repository()` |
| **app/repositories/memory_repository.py** | **ΝΕΟ**: in-memory υλοποίηση (λογική από το παλιό MemoryService) |
| **app/repositories/mysql_repository.py** | **ΝΕΟ**: MySQL υλοποίηση με SQLAlchemy |
| **app/db/base.py** | **ΝΕΟ**: engine, session factory, `session_scope()`, `create_tables()` |
| **app/db/models.py** | **ΝΕΟ**: ORM models `ChatSessionModel`, `ChatMessageModel` |
| **app/db/__init__.py** | **ΝΕΟ**: exports |
| **app/routers/chat.py** | + GET /chat/sessions, DELETE /chat/session/{id}/history |
| **app/models/schemas.py** | + ChatSessionListResponse |
| **requirements.txt** | + sqlalchemy, PyMySQL |
| **.env.example** | + MEMORY_BACKEND, MySQL vars |
| **docs/schema_chat_mysql.sql** | **ΝΕΟ**: DDL για chat_sessions, chat_messages |
| **tests/test_conversation_repository.py** | **ΝΕΟ**: tests για in-memory repository |

---

## C. Database schema

### chat_sessions

| Πεδίο | Τύπος | Σκοπός |
|-------|--------|--------|
| id | BIGINT UNSIGNED AUTO_INCREMENT | PK (εσωτερικό) |
| session_id | VARCHAR(64) UNIQUE NOT NULL | Business id (π.χ. 16 hex) |
| created_at | DATETIME(6) NOT NULL | Δημιουργία |
| updated_at | DATETIME(6) NOT NULL | Τελευταία ενημέρωση |
| language | VARCHAR(16) NOT NULL DEFAULT 'auto' | Γλώσσα |
| user_preferences | JSON NULL | Budget, colors, categories, κ.λπ. |
| retrieved_skus | JSON NULL | Λίστα SKU που έχουν προταθεί |
| last_query | TEXT NULL | Τελευταία ερώτηση χρήστη |
| last_answer | TEXT NULL | Τελευταία απάντηση assistant |
| deleted_at | DATETIME(6) NULL | Soft delete (μελλοντική χρήση) |

**Indexes:** UNIQUE(session_id), INDEX(updated_at), INDEX(deleted_at)

### chat_messages

| Πεδίο | Τύπος | Σκοπός |
|-------|--------|--------|
| id | BIGINT UNSIGNED AUTO_INCREMENT | PK |
| session_id | VARCHAR(64) NOT NULL, FK → chat_sessions.session_id ON DELETE CASCADE | Session |
| role | VARCHAR(32) NOT NULL | user \| assistant \| system \| tool |
| content | TEXT NOT NULL | Κείμενο μηνύματος |
| created_at | DATETIME(6) NOT NULL | Χρόνος εισαγωγής |
| sort_order | INT UNSIGNED NOT NULL DEFAULT 0 | Σειρά εμφάνισης (0, 1, 2, …) |

**Indexes:** (session_id, sort_order), FK ON DELETE CASCADE

---

## D. SQL / migrations

- **Καθαρό SQL:** `docs/schema_chat_mysql.sql` — έτοιμο για εκτέλεση σε MySQL 8.0+.
- **ORM create_tables:** Όταν `MEMORY_BACKEND=mysql`, στο πρώτο request καλείται `create_tables()` (SQLAlchemy `Base.metadata.create_all`), ώστε να δημιουργηθούν οι πίνακες αν δεν υπάρχουν.
- **Συμβατότητα:** Το DDL στο `schema_chat_mysql.sql` ταιριάζει με τα ORM models. Μπορείτε είτε να τρέξετε το SQL με το χέρι είτε να αφήσετε το app να δημιουργήσει τους πίνακες.

**Εκτέλεση SQL (χειροκίνητα):**
```bash
mysql -u mosty -p mosty < docs/schema_chat_mysql.sql
```

---

## E. CRUD που υποστηρίζονται

- **create session:** `POST /api/v1/chat/session`
- **get session:** `GET /api/v1/chat/session/{session_id}`
- **list sessions:** `GET /api/v1/chat/sessions?limit=50&offset=0`
- **append message:** εσωτερικά μέσω `add_turn(session_id, role, content)` (από /ask, /recommend, κ.λπ.)
- **get messages / context:** εσωτερικά μέσω `get_conversation_context(session_id, max_turns=10)`
- **delete session:** `DELETE /api/v1/chat/session/{session_id}`
- **clear session history:** `DELETE /api/v1/chat/session/{session_id}/history`
- **update session metadata:** εσωτερικά (`update_session`, `add_retrieved_skus`, `update_preferences`)

---

## F. Σημεία αλλαγής ροής

1. **get_memory_service()** — επιστρέφει `MemoryService` που κάνει delegate στο repository. Το repository επιλέγεται από `MEMORY_BACKEND` (memory | mysql).
2. **ChatService** — δεν αλλάζει· συνεχίζει να καλεί `self._memory.*` όπως πριν.
3. **Routers** — ίδια request/response contracts· προστέθηκαν μόνο GET /sessions και DELETE /session/{id}/history.

---

## G. Οδηγίες βήμα-βήμα

1. **Εγκατάσταση dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **MySQL**
   - Δημιουργήστε DB και user, π.χ.:
     ```sql
     CREATE DATABASE mosty CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
     CREATE USER 'mosty'@'%' IDENTIFIED BY 'your_password';
     GRANT ALL ON mosty.* TO 'mosty'@'%';
     FLUSH PRIVILEGES;
     ```
   - Τρέξτε το schema (προαιρετικά):
     ```bash
     mysql -u mosty -p mosty < docs/schema_chat_mysql.sql
     ```

3. **Environment**
   - Αντιγράψτε από `.env.example` και ορίστε:
     ```env
     MEMORY_BACKEND=mysql
     DB_HOST=localhost
     DB_PORT=3306
     DB_NAME=mosty
     DB_USER=mosty
     DB_PASSWORD=your_password
     DB_CHARSET=utf8mb4
     ```
   - Ή ένα URL:
     ```env
     MEMORY_BACKEND=mysql
     DATABASE_URL=mysql+pymysql://mosty:your_password@localhost:3306/mosty?charset=utf8mb4
     ```

4. **Εκκίνηση εφαρμογής**
   - Ξεκινήστε το API ως συνήθως. Στο πρώτο request με backend=mysql θα γίνει (αν χρειάζεται) create_tables.

5. **Έλεγχος**
   - Δημιουργία session: `POST /api/v1/chat/session`
   - Λίστα sessions: `GET /api/v1/chat/sessions`
   - Chat: `POST /api/v1/chat/ask` με `session_id` από το πρώτο βήμα.

---

## H. Commands

```bash
# Εγκατάσταση
pip install -r requirements.txt

# Schema (αν τρέχετε SQL με το χέρι)
mysql -u mosty -p mosty < docs/schema_chat_mysql.sql

# Tests (in-memory, χωρίς MySQL)
pytest tests/test_conversation_repository.py -v

# Εκκίνηση API
uvicorn app.main:app --reload --host 0.0.0.0 --port 8800
```

---

## I. Refactor για καθαρή αρχιτεκτονική

- **Repository pattern:** Όλη η persistence περνάει από `ConversationRepository`. Το `MemoryService` είναι thin facade.
- **Separation of concerns:** DB layer (`app/db/`), repositories (`app/repositories/`), service (`MemoryService`), routers (unchanged contracts).
- **Config-driven backend:** `MEMORY_BACKEND=memory` | `mysql` χωρίς αλλαγή κώδικα.

---

## J. Error handling / edge cases

- **Invalid or missing session_id:** `get_session` → None → router επιστρέφει 404.
- **DB connection failure:** Exceptions από SQLAlchemy ανεβαίνουν· το router που καλεί το ChatService έχει `except Exception` → 503.
- **Duplicate session_id:** Στο create_session παράγεται πάντα νέο UUID· δεν υπάρχει insert με ίδιο session_id από client.
- **Ordering:** Τα messages έχουν `sort_order` (0, 1, 2, …)· η ανάγνωση γίνεται με ORDER BY sort_order.
- **Trim:** Πάνω από 40 turns ανά session, τα παλαιότερα διαγράφονται (ίδιο όριο με το in-memory).

---

## K. Αξιολόγηση (production-ready)

- **Schema:** Καθαρό, με indexes και FK, utf8mb4.
- **Persistence:** MySQL με connection pooling (pool_pre_ping, pool_recycle).
- **API:** Τα υπάρχοντα endpoints δεν άλλαξαν contract· προστέθηκαν μόνο list_sessions και clear_history.
- **Backward compatibility:** Με `MEMORY_BACKEND=memory` (default) η συμπεριφορά παραμένει in-memory όπως πριν.
- **Tests:** Βασικά tests για in-memory repository· για MySQL μπορεί να προστεθεί integration test με test DB.

Η λύση είναι production-ready με την προϋπόθεση ότι το MySQL είναι διαθέσιμο και ρυθμισμένο (backups, users, network).
