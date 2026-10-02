"""Merchandising control service.

Provides CRUD for merchandising rules (featured SKUs, boosted brands,
boosted categories, excluded SKUs, campaigns) and a scoring function
used by the reranker to apply merchandising boosts at search time.

Rule types:
- featured_sku: boost a specific SKU
- boost_brand: boost all products from a brand
- boost_category: boost all products in a category
- exclude_sku: suppress a specific SKU from results
- campaign: boost products matching a campaign tag/label
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update

from app.core.logging import get_logger
from app.db.base import session_scope
from app.db.models import MerchandisingRuleModel

logger = get_logger(__name__)

VALID_RULE_TYPES = {"featured_sku", "boost_brand", "boost_category", "exclude_sku", "campaign"}


class MerchandisingService:

    def create_rule(self, data: dict[str, Any]) -> dict[str, Any]:
        rule_type = data.get("rule_type", "")
        if rule_type not in VALID_RULE_TYPES:
            raise ValueError(
                f"Invalid rule_type '{rule_type}'. Must be one of: {', '.join(sorted(VALID_RULE_TYPES))}"
            )
        with session_scope() as db:
            row = MerchandisingRuleModel(
                rule_type=rule_type,
                value=data["value"],
                boost_weight=data.get("boost_weight", 0.1),
                label=data.get("label"),
                active=data.get("active", True),
                starts_at=_parse_dt(data.get("starts_at")),
                ends_at=_parse_dt(data.get("ends_at")),
            )
            db.add(row)
            db.flush()
            return _row_to_dict(row)

    def list_rules(self, active_only: bool = False) -> list[dict[str, Any]]:
        with session_scope() as db:
            q = select(MerchandisingRuleModel).order_by(MerchandisingRuleModel.created_at.desc())
            if active_only:
                q = q.where(MerchandisingRuleModel.active.is_(True))
            rows = db.execute(q).scalars().all()
            return [_row_to_dict(r) for r in rows]

    def update_rule(self, rule_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with session_scope() as db:
            row = db.execute(
                select(MerchandisingRuleModel).where(MerchandisingRuleModel.id == rule_id)
            ).scalar_one_or_none()
            if not row:
                return None
            for field in ("rule_type", "value", "boost_weight", "label", "active"):
                if field in data:
                    setattr(row, field, data[field])
            if "starts_at" in data:
                row.starts_at = _parse_dt(data["starts_at"])
            if "ends_at" in data:
                row.ends_at = _parse_dt(data["ends_at"])
            db.flush()
            return _row_to_dict(row)

    def delete_rule(self, rule_id: int) -> bool:
        with session_scope() as db:
            result = db.execute(
                delete(MerchandisingRuleModel).where(MerchandisingRuleModel.id == rule_id)
            )
            return (result.rowcount or 0) > 0

    def get_active_rules(self) -> list[dict[str, Any]]:
        """Return currently active rules (respecting date windows)."""
        now = datetime.now(timezone.utc)
        with session_scope() as db:
            q = (
                select(MerchandisingRuleModel)
                .where(MerchandisingRuleModel.active.is_(True))
            )
            rows = db.execute(q).scalars().all()
            result: list[dict[str, Any]] = []
            for r in rows:
                if r.starts_at and r.starts_at > now:
                    continue
                if r.ends_at and r.ends_at < now:
                    continue
                result.append(_row_to_dict(r))
            return result

    def compute_boost(
        self,
        sku: str,
        categories: list[str],
        name: str,
    ) -> float:
        """Compute total merchandising boost for a search result.

        Returns a float to be added to the rerank score. Returns negative
        infinity for excluded SKUs (caller should remove from results).
        """
        rules = self.get_active_rules()
        if not rules:
            return 0.0

        total_boost = 0.0
        name_lower = name.lower()
        cats_lower = [c.lower() for c in categories]

        for rule in rules:
            rt = rule["rule_type"]
            val = rule["value"].lower()
            weight = rule["boost_weight"]

            if rt == "exclude_sku" and sku.lower() == val:
                return float("-inf")

            if rt == "featured_sku" and sku.lower() == val:
                total_boost += weight

            elif rt == "boost_brand" and val in name_lower:
                total_boost += weight

            elif rt == "boost_category":
                if any(val in c for c in cats_lower):
                    total_boost += weight

            elif rt == "campaign":
                if val in name_lower or any(val in c for c in cats_lower):
                    total_boost += weight

        return total_boost


def _row_to_dict(row: MerchandisingRuleModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "rule_type": row.rule_type,
        "value": row.value,
        "boost_weight": row.boost_weight,
        "label": row.label,
        "active": row.active,
        "starts_at": row.starts_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.starts_at else None,
        "ends_at": row.ends_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.ends_at else None,
        "created_at": row.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.created_at else None,
    }


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
