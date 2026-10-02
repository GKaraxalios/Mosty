#!/usr/bin/env python3
"""
QA runner: read JSONL scenarios, call POST /api/v1/chat/ask, score heuristically, write JSONL results.

Usage:
  export MOSTY_API_KEY=your_key
  python scripts/qa_chat_scenarios.py scripts/qa_scenarios.sample.jsonl -o /tmp/qa_out.jsonl

  python scripts/qa_chat_scenarios.py scenarios.jsonl --base-url https://api.example.com/api/v1

Environment:
  MOSTY_API_KEY   API key header x-mosty-api-key (or --api-key)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{line_no}: invalid JSON: {e}") from e
    return rows


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    timeout: float = 120.0,
) -> tuple[int, dict[str, Any] | str]:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        if "Content-Type" not in headers:
            headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            code = resp.getcode()
            try:
                return code, json.loads(raw)
            except json.JSONDecodeError:
                return code, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def _urls_in_text(text: str) -> set[str]:
    if not text:
        return set()
    found = re.findall(r"https?://[^\s\)\]\"\'<>]+", text)
    return {u.rstrip(".,;") for u in found}


def _normalize_url(u: str) -> str:
    return u.rstrip("/").lower()


def _hallucination_pass(answer: str, products: list[dict[str, Any]], sources: list[dict[str, Any]]) -> bool:
    """URLs in the answer must appear in catalog-backed products/sources (when present)."""
    allowed: set[str] = set()
    for p in products:
        u = p.get("url")
        if u:
            allowed.add(_normalize_url(str(u)))
    for s in sources:
        u = s.get("url")
        if u:
            allowed.add(_normalize_url(str(u)))
    mentioned = {_normalize_url(u) for u in _urls_in_text(answer)}
    if not mentioned:
        return True
    if not allowed:
        # Model cited URLs but API returned no URLs — suspicious
        return False
    for u in mentioned:
        if u and not any(u == a or u.startswith(a + "/") for a in allowed):
            return False
    return True


def _greek_ratio(text: str) -> float:
    if not text:
        return 0.0
    greek = sum(1 for c in text if "\u0370" <= c <= "\u03FF" or "\u1F00" <= c <= "\u1FFF")
    return greek / max(len(text), 1)


def score_heuristic(
    scenario: dict[str, Any],
    resp: dict[str, Any] | None,
    http_ok: bool,
) -> dict[str, Any]:
    """Return scores 0–10 per dimension + hallucination PASS/FAIL (heuristic)."""
    answer = ""
    products: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    intent = ""
    if isinstance(resp, dict):
        answer = str(resp.get("answer") or "")
        products = resp.get("products") or []
        sources = resp.get("sources") or []
        intent = str(resp.get("intent") or "")

    expect_products = scenario.get("expect_products")
    expect_no_products = scenario.get("expect_no_products")
    expected_intent = scenario.get("expected_intent")

    # Intent
    intent_score = 6.0
    if not http_ok:
        intent_score = 0.0
    elif expected_intent and intent:
        intent_score = 10.0 if expected_intent.lower() in intent.lower() else 5.0
    elif intent:
        intent_score = 8.0

    # Relevance
    rel = 6.0
    if not http_ok:
        rel = 0.0
    elif expect_products is True:
        rel = min(10.0, 4.0 + min(len(products), 4) * 1.5)
    elif expect_no_products is True:
        rel = 8.0 if not products else 5.0
    else:
        rel = 7.0 if len(answer) > 40 else 4.0

    # Accuracy (structure of response vs API)
    acc = 7.0
    if not http_ok:
        acc = 0.0
    elif products:
        with_url = sum(1 for p in products if p.get("url"))
        acc = min(10.0, 6.0 + (with_url / max(len(products), 1)) * 4.0)
    elif expect_products is True and "δεν βρήκα" in answer.lower() or "didn't find" in answer.lower():
        acc = 8.0

    # Helpfulness
    alen = len(answer.strip())
    help_s = 5.0
    if 80 <= alen <= 4000:
        help_s = 8.0
    elif alen < 80:
        help_s = 4.0
    else:
        help_s = 7.0
    if not http_ok:
        help_s = 0.0

    # Language (Greek for default scenarios)
    lang = scenario.get("language", "el")
    lang_score = 7.0
    if lang == "el" and answer:
        lang_score = min(10.0, 6.0 + _greek_ratio(answer) * 8.0)

    hall = "PASS"
    if http_ok and answer and not _hallucination_pass(answer, products, sources):
        hall = "FAIL"

    return {
        "intent": round(intent_score, 1),
        "relevance": round(rel, 1),
        "accuracy": round(acc, 1),
        "helpfulness": round(help_s, 1),
        "language": round(lang_score, 1),
        "hallucination": hall,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Run chat QA scenarios against /chat/ask")
    ap.add_argument("scenarios", help="Path to JSONL file (one scenario per line)")
    ap.add_argument("-o", "--output", help="Write results JSONL here (default: stdout)")
    ap.add_argument("--base-url", default=os.environ.get("MOSTY_API_BASE", "http://127.0.0.1:8000/api/v1"))
    ap.add_argument("--api-key", default=os.environ.get("MOSTY_API_KEY", ""))
    ap.add_argument("--session-mode", choices=("single", "fresh"), default="single")
    ap.add_argument("--delay-ms", type=int, default=300, help="Pause between requests")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--top-k", type=int, default=10)
    args = ap.parse_args()

    api_key = (args.api_key or "").strip()
    if not api_key:
        print("Error: set MOSTY_API_KEY or pass --api-key", file=sys.stderr)
        sys.exit(1)

    base = args.base_url.rstrip("/")
    scenarios = _read_jsonl(args.scenarios)

    headers = {"x-mosty-api-key": api_key, "Accept": "application/json"}

    # Create initial session
    code, sess = _http_json("POST", f"{base}/chat/session?language=auto", headers=headers)
    if code != 200 or not isinstance(sess, dict):
        print(f"Failed to create session: HTTP {code} {sess}", file=sys.stderr)
        sys.exit(1)
    session_obj = sess.get("session") if isinstance(sess, dict) else None
    session_id = (
        (session_obj or {}).get("session_id")
        if isinstance(session_obj, dict)
        else None
    ) or (sess.get("session_id") if isinstance(sess, dict) else None)
    if not session_id:
        print(f"Unexpected session response: {sess}", file=sys.stderr)
        sys.exit(1)

    out_rows: list[dict[str, Any]] = []
    current_session = session_id

    for sc in scenarios:
        sid = current_session
        if args.session_mode == "fresh":
            c2, sess2 = _http_json("POST", f"{base}/chat/session?language=auto", headers=headers)
            if c2 == 200 and isinstance(sess2, dict):
                s2 = sess2.get("session")
                if isinstance(s2, dict) and s2.get("session_id"):
                    sid = s2["session_id"]

        query = sc.get("query") or sc.get("question")
        if not query:
            continue

        body = {
            "session_id": sid,
            "query": query,
            "temperature": sc.get("temperature", args.temperature),
            "top_k": sc.get("top_k", args.top_k),
        }
        if sc.get("filters"):
            body["filters"] = sc["filters"]

        code, resp = _http_json("POST", f"{base}/chat/ask", headers=headers, body=body, timeout=180.0)
        http_ok = code == 200 and isinstance(resp, dict)

        answer_text = ""
        if isinstance(resp, dict):
            answer_text = str(resp.get("answer") or "")
        elif isinstance(resp, str):
            answer_text = resp[:2000]

        scores = score_heuristic(sc, resp if isinstance(resp, dict) else None, http_ok)

        row = {
            "id": sc.get("id"),
            "category": sc.get("category"),
            "question": query,
            "expected_behavior": sc.get("expected_behavior"),
            "http_status": code,
            "ai_response": answer_text,
            "response_intent": (resp.get("intent") if isinstance(resp, dict) else None),
            "products_count": len((resp.get("products") or [])) if isinstance(resp, dict) else 0,
            "scores": scores,
            "notes": sc.get("notes"),
            "raw_error": None
            if http_ok
            else (resp if not isinstance(resp, dict) else resp.get("detail") or resp.get("error")),
        }
        out_rows.append(row)

        if args.delay_ms > 0:
            time.sleep(args.delay_ms / 1000.0)

    # Per-scenario: mean of 5 dims (0–10) → ×10 = 0–100; overall = mean across scenarios
    per_100: list[float] = []
    for row in out_rows:
        s = row.get("scores") or {}
        if isinstance(s, dict) and "hallucination" in s:
            avg = (
                float(s.get("intent", 0))
                + float(s.get("relevance", 0))
                + float(s.get("accuracy", 0))
                + float(s.get("helpfulness", 0))
                + float(s.get("language", 0))
            ) / 5.0
            per_100.append(avg * 10.0)
    overall = sum(per_100) / len(per_100) if per_100 else 0.0

    low = [r.get("id") for r in out_rows if isinstance(r.get("scores"), dict) and sum(
        float(r["scores"].get(k, 0)) for k in ("intent", "relevance", "accuracy", "helpfulness", "language")
    ) / 5.0 < 5.0]
    http_errs = [r.get("id") for r in out_rows if r.get("http_status", 200) != 200]

    summary = {
        "overall_score_approx": round(overall, 1),
        "scenarios_run": len(out_rows),
        "session_mode": args.session_mode,
        "failures_hallucination": sum(
            1 for r in out_rows if r.get("scores", {}).get("hallucination") == "FAIL"
        ),
        "http_errors": http_errs,
        "low_score_scenario_ids": low,
    }

    output_obj = {"summary": summary, "results": out_rows}

    out_txt = json.dumps(output_obj, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out_txt)
        print(f"Wrote {args.output} ({len(out_rows)} scenarios)", file=sys.stderr)
    else:
        print(out_txt)


if __name__ == "__main__":
    main()
