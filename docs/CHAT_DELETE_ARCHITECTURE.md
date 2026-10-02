# Chat session delete: architecture and implementation

## 1. Why deletion was not propagated to the database

| Layer | Cause |
|-------|--------|
| **Frontend** | `deleteConversation(id)` only updated local state (array + `localStorage`). It never called the backend `DELETE /api/v1/chat/session/{session_id}`. |
| **API** | The endpoint existed and worked; it was simply never invoked from the UI. |
| **Backend** | No bug: `MemoryService.delete_session()` and MySQL repo correctly delete the session row. |
| **Database** | No bug: `ON DELETE CASCADE` on `chat_messages.session_id` was already in place. |

**Root cause:** Frontend treated “delete conversation” as a local-only action and did not call the delete API.

---

## 2. Intended delete behaviour

- **Hard delete (current):** The session row is removed from `chat_sessions`. MySQL `ON DELETE CASCADE` removes all rows in `chat_messages` with that `session_id`. Data is gone for good.
- **Related messages:** Handled by the database: deleting the session deletes all its messages.
- **After refresh:** Conversations are loaded from `localStorage`; the deleted one is already removed there. If the frontend later syncs from the server (e.g. `GET /sessions`), the deleted session will not appear because it no longer exists in the DB.
- **Multiple devices:** Each device has its own `localStorage`. Deleting on device A only calls the API once; device B’s list is unchanged until it refreshes or re-syncs. After re-sync, device B will no longer see the deleted session (it’s gone from the server).

---

## 3. Backend implementation

### API

- **Endpoint:** `DELETE /api/v1/chat/session/{session_id}`
- **Auth:** Same as rest of chat API (e.g. `x-mosty-api-key`).
- **Response:** `200 OK` with `{"success": true}`. Idempotent: same response if the session was already deleted.
- **Validation:** `400` if `session_id` is missing or longer than 64 characters.

### Controller

```python
# app/routers/chat.py
@router.delete("/session/{session_id}", response_model=APIResponse)
async def delete_session(session_id: str) -> APIResponse:
    if not session_id or len(session_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    memory = get_memory_service()
    memory.delete_session(session_id)
    return APIResponse(success=True)
```

### Service

- `MemoryService.delete_session(session_id)` delegates to the configured repository (e.g. `MySQLConversationRepository.delete_session()`).

### Repository (MySQL)

- Single delete on `chat_sessions`: `delete(ChatSessionModel).where(ChatSessionModel.session_id == session_id)`.
- Messages are not deleted in application code; the database does it via `ON DELETE CASCADE` on `chat_messages.session_id → chat_sessions.session_id`.

---

## 4. Database: FK and CASCADE

- **FK:** `chat_messages.session_id` → `chat_sessions.session_id`
- **Option used:** `ON DELETE CASCADE` (in schema and ORM).

**Why CASCADE:**

- One DELETE on `chat_sessions` removes the session and all its messages in one go.
- No risk of “messages deleted but session not” or orphans.
- Matches the intended behaviour: “delete conversation” = delete session and all its data.

**Alternative (manual delete):** You could delete from `chat_messages` first, then `chat_sessions`. Pros: explicit in code. Cons: two round-trips, and if the second fails you can end up with an empty session; you also need to maintain the same order everywhere. CASCADE is simpler and safer.

---

## 5. Safety and edge cases

| Case | Handling |
|------|-----------|
| **Already deleted session** | Idempotent: still return `200` and `{"success": true}`. No 404. |
| **Invalid `session_id`** | `400` if empty or length > 64. |
| **Concurrent delete** | Both requests can return 200; second delete affects 0 rows. Safe. |
| **Partial failure** | With CASCADE, only one DELETE is issued (on `chat_sessions`); MySQL deletes messages in the same transaction. No “messages gone, session left” in normal operation. |

---

## 6. Optional: soft delete, audit, recovery

- **Soft delete:** Table `chat_sessions` already has `deleted_at`. To switch to soft delete:
  - In `delete_session`, `UPDATE chat_sessions SET deleted_at = NOW() WHERE session_id = :id` instead of `DELETE`.
  - In `get_session` and `list_sessions`, add `AND deleted_at IS NULL`.
  - Messages can stay as-is (no CASCADE on soft-update); optionally hide them in reads when `deleted_at` is set, or run a job to hard-delete old soft-deleted sessions and rely on CASCADE for their messages.
- **Audit:** Add an `audit_log` table (e.g. `session_id`, `action`, `at`, `by`) and write a row on delete (and optionally on create).
- **Recovery:** With soft delete, “restore” = set `deleted_at = NULL`. With hard delete, recovery would require backups/point-in-time restore.

---

## 7. Testing

### SQL checks after delete

```sql
-- Before: note session_id and counts
SELECT session_id, (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.session_id) AS msg_count
FROM chat_sessions s
WHERE session_id = 'YOUR_SESSION_ID';

-- Run the delete via API (or below), then:

-- Session should be gone
SELECT * FROM chat_sessions WHERE session_id = 'YOUR_SESSION_ID';
-- Empty.

-- Messages for that session should be gone
SELECT * FROM chat_messages WHERE session_id = 'YOUR_SESSION_ID';
-- Empty.
```

### Direct DB delete (for verification of CASCADE)

```sql
DELETE FROM chat_sessions WHERE session_id = 'YOUR_SESSION_ID';
-- Then:
SELECT * FROM chat_messages WHERE session_id = 'YOUR_SESSION_ID';
-- Empty (CASCADE worked).
```

### Postman

- **Method:** DELETE  
- **URL:** `{{baseUrl}}/api/v1/chat/session/{{session_id}}`  
  Example: `http://localhost:8800/api/v1/chat/session/a1b2c3d4e5f6g7h8`  
- **Headers:** `x-mosty-api-key: YOUR_API_KEY`, `Content-Type: application/json`  
- **Expected:** `200 OK` body `{"success": true}`  
- **Idempotent:** Call again with same `session_id` → again `200` and `{"success": true}`.  
- **Invalid:** `DELETE .../session/` (empty) or very long path → `400`.

### Expected DB state after delete

- No row in `chat_sessions` for that `session_id`.
- No rows in `chat_messages` for that `session_id`.
- Other sessions and their messages unchanged.
