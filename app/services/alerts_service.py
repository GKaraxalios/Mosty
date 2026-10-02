"""Notification / alerts service.

Manages alert subscriptions for:
- restock: notify when a previously out-of-stock SKU comes back
- price_drop: notify when a SKU's price drops below a threshold
- new_in_category: notify when new items appear in a preferred category

Alerts are persisted in MySQL. A future scheduler job can process active
alerts by calling check_and_trigger().
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update

from app.core.logging import get_logger
from app.db.base import session_scope
from app.db.models import AlertModel

logger = get_logger(__name__)

VALID_ALERT_TYPES = {"restock", "price_drop", "new_in_category"}
VALID_STATUSES = {"active", "triggered", "dismissed"}


class AlertsService:

    def create_alert(
        self,
        session_id: str,
        alert_type: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if alert_type not in VALID_ALERT_TYPES:
            raise ValueError(
                f"Invalid alert_type '{alert_type}'. Must be one of: {', '.join(sorted(VALID_ALERT_TYPES))}"
            )
        with session_scope() as db:
            row = AlertModel(
                session_id=session_id,
                alert_type=alert_type,
                config=config or {},
            )
            db.add(row)
            db.flush()
            return _row_to_dict(row)

    def list_alerts(
        self,
        session_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        with session_scope() as db:
            q = select(AlertModel).where(AlertModel.session_id == session_id)
            if status:
                q = q.where(AlertModel.status == status)
            q = q.order_by(AlertModel.created_at.desc())
            rows = db.execute(q).scalars().all()
            return [_row_to_dict(r) for r in rows]

    def delete_alert(self, alert_id: int, session_id: str) -> bool:
        with session_scope() as db:
            result = db.execute(
                delete(AlertModel).where(
                    AlertModel.id == alert_id,
                    AlertModel.session_id == session_id,
                )
            )
            return (result.rowcount or 0) > 0

    def dismiss_alert(self, alert_id: int, session_id: str) -> bool:
        with session_scope() as db:
            result = db.execute(
                update(AlertModel)
                .where(AlertModel.id == alert_id, AlertModel.session_id == session_id)
                .values(status="dismissed")
            )
            return (result.rowcount or 0) > 0

    def trigger_alert(self, alert_id: int) -> bool:
        with session_scope() as db:
            result = db.execute(
                update(AlertModel)
                .where(AlertModel.id == alert_id)
                .values(status="triggered", triggered_at=datetime.now(timezone.utc))
            )
            return (result.rowcount or 0) > 0

    def check_and_trigger(self) -> list[dict[str, Any]]:
        """Check active alerts against current catalog state.

        This is designed to be called by a scheduler job. For each active alert,
        it checks the condition and triggers if met. Currently uses the chunk
        store for stock/price checks.

        Returns list of triggered alert dicts.
        """
        from app.services.rag_ingestion_service import get_chunk_store
        store = get_chunk_store()

        sku_data: dict[str, dict[str, Any]] = {}
        for chunk in store.values():
            if chunk.sku not in sku_data:
                sku_data[chunk.sku] = {
                    "price": chunk.price,
                    "stock_status": chunk.stock_status,
                    "categories": chunk.categories,
                }

        triggered: list[dict[str, Any]] = []
        with session_scope() as db:
            active = db.execute(
                select(AlertModel).where(AlertModel.status == "active")
            ).scalars().all()

            for alert in active:
                config = alert.config or {}
                should_trigger = False

                if alert.alert_type == "restock":
                    target_sku = config.get("sku")
                    if target_sku and target_sku in sku_data:
                        status = (sku_data[target_sku].get("stock_status") or "").upper()
                        if "IN_STOCK" in status:
                            should_trigger = True

                elif alert.alert_type == "price_drop":
                    target_sku = config.get("sku")
                    target_price = config.get("target_price")
                    if target_sku and target_price and target_sku in sku_data:
                        current = sku_data[target_sku].get("price")
                        if current is not None and current <= target_price:
                            should_trigger = True

                elif alert.alert_type == "new_in_category":
                    pass

                if should_trigger:
                    alert.status = "triggered"
                    alert.triggered_at = datetime.now(timezone.utc)
                    triggered.append(_row_to_dict(alert))

        return triggered


def _row_to_dict(row: AlertModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "alert_type": row.alert_type,
        "status": row.status,
        "config": row.config or {},
        "created_at": row.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.created_at else None,
        "triggered_at": row.triggered_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.triggered_at else None,
    }
