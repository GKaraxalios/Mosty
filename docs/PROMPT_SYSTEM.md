# MOSTY / ELIZA prompt system — architecture

## 1. Problems in the legacy monolith (`prompt_builder.py` ~1500 lines)

| Issue | Why it hurts |
|--------|----------------|
| **Triple identity** | “You are ELIZA” repeated in `_CORE_DOMAIN`, `_ELIZA_ROLE`, `_ELIZA_STYLIST` — token waste + drift risk. |
| **RAG rules repeated** | “Catalog only / no hallucination” appears in role card, stylist block, RAG block, commerce blocks. |
| **Overlapping commerce** | High-end shopper, HARD/SOFT, conversation memory said the same budget/gift/multi-turn ideas three times. |
| **Mixed concerns** | Store JSON formatting, weather persona, JSON schema, and domain classification in one file. |
| **Logic in prompts** | Price filtering, dedupe, stock, color post-filter — duplicated as instructions; must stay in **backend** as source of truth. |

## 2. Target layers (implemented)

| Layer | Module | Responsibility |
|--------|--------|----------------|
| **Store knowledge** | `app/utils/prompts/store_context.py` | `build_store_info_el/en()`, `load_store_content`, `CONFIG_PATH` — JSON-driven hours, links, shipping, payment. |
| **Core identity + domain** | `system_instruction_content._CORE_DOMAIN_AND_BEHAVIOR` | MOSTY-only scope, A–G classification, per-class responses, global style rules. |
| **Role + styling-first** | `_ELIZA_ROLE_AND_STYLING_FIRST` | Stylist-before-products, RAG one-liner (detail in RAG pack), intent resolution, output control. |
| **Stylist intelligence** | `_ELIZA_STYLIST_AND_FORMAT` | Occasion, beach/office/wedding, progression mode, versatile/reuse, natural-language intent. |
| **Output mode** | `_OUTPUT_FORMAT_TEXT` / `_OUTPUT_FORMAT_JSON` | Prevents “plain text only” vs JSON conflict. |
| **RAG + persona + commerce** | `SYSTEM_RAG_RULES_EL/EN` | Constraint validation, weather persona, RAG product rules, shopper + HARD/SOFT + multi-turn (concatenated). |
| **Assembly** | `app/utils/prompts/assembly.py` | `get_system_instruction(lang, output_mode)` — fixed concat order. |
| **User / RAG prompt** | `prompt_builder.py` | `build_stylist_prompt`, `RULES_EL/EN`, JSON schema injection, product context. |

**Physical layout:** strings remain in `system_instruction_content.py` (single source) for now; **next optimization** is splitting that file into `layers/core.py`, `layers/rag_pack.py`, etc., without changing text.

## 3. Logic vs prompt

| Rule | Backend (`chat_service`, `commerce_retrieval`, `hybrid_search`) | Prompt |
|------|------------------------------------------------------------------|--------|
| Out-of-stock / invalid URL | `filter_valid_results`, search filters | Remind: only use catalog; never invent URLs |
| Budget max/min | `merge_intent_into_filters`, `post_filter_by_price`, hybrid metadata | Never show over-budget in text/JSON |
| Color strict | `post_filter_by_color` | Skip wrong color in list; explain if needed |
| Dedupe SKU | `_dedupe_by_sku` | Do not repeat SKU |
| Intent JSON | `FashionIntent` + LLM parser | Interpret user language; follow `strict_constraints` |
| Tone, comparative answers, gift wording | — | Prompt-only |
| “Cheaper than these” | Retrieval uses rewritten query + context; **not** structured state machine | Model uses `conversation_context` + last catalog block |

## 4. Optimized system prompt example (structure)

```
[CORE_DOMAIN] classification A–G + style
[ELIZA_ROLE] styling first + intent + output control  
[ELIZA_STYLIST] occasion, progression, reusability, …
[STORE_INFO] from store_content.json
[OUTPUT_FORMAT_JSON | OUTPUT_FORMAT_TEXT]
[SYSTEM_RAG_RULES] = constraint + persona + RAG + commerce + HARD/SOFT + memory
```

## 5. Stylist user prompt (RAG) — shape

```
RULES_EL/EN
[optional WEATHER]
[optional CONVERSATION_HISTORY]
[optional REFERENCE_PRODUCT]
PRODUCTS FROM CATALOG: (SKU, name, url, price, …)
[intent lines: category, color, occasion, gift, popular]
JSON schema + USER QUESTION + "JSON:"
```

## 6. JSON mode

- **System:** `get_system_instruction(lang, output_mode="json")` → `_OUTPUT_FORMAT_JSON` (allows raw JSON).
- **User:** `_build_json_output_instruction` in `prompt_builder.py` — schema `message`, `no_results`, `products[]`.

## 7. Improvements delivered

1. **`prompt_builder.py` ~625 lines** — builders + catalog helpers only; system strings moved out.
2. **`app/utils/prompts/`** — `store_context`, `assembly`, `system_instruction_content` (all ELIZA system text).
3. **Single import path** for `get_system_instruction`: `assembly.py`.
4. **Re-exports** — `SYSTEM_RAG_RULES_*`, `CONFIG_PATH`, `load_store_content` available from `prompt_builder` and `app.utils.prompts`.
5. **Next step (token reduction)** — merge `_HIGH_END` + `_CONSTRAINT_LAYERS` + `_CONVERSATION_REFINEMENT` into one **commerce policy** block per language (~30–40% fewer tokens in that section) without removing rules.
