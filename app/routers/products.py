from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.schemas import (
    ProductDetailResponse,
    ProductListResponse,
    ProductLiveResponse,
)
from app.services.auth_service import verify_api_key
from app.services.product_service import ProductService

router = APIRouter(prefix="/products", tags=["products"], dependencies=[Depends(verify_api_key)])


@router.get("", response_model=ProductListResponse)
async def list_products(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=200, description="Items per page"),
    search: str | None = Query(None, description="Search query"),
    category: str | None = Query(None, description="Category URL path filter"),
    brand: str | None = Query(None, description="Brand name filter (fallback)"),
    min_price: float | None = Query(None, ge=0, description="Minimum price"),
    max_price: float | None = Query(None, ge=0, description="Maximum price"),
    updated_after: str | None = Query(None, description="ISO8601 date filter"),
    lang: str | None = Query(None, description="Language / store code"),
) -> ProductListResponse:
    svc = ProductService()
    try:
        return await svc.list_products(
            page=page,
            per_page=per_page,
            search=search,
            category=category,
            brand=brand,
            min_price=min_price,
            max_price=max_price,
            updated_after=updated_after,
            lang=lang,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Upstream failure: {exc}")


@router.get("/{sku}", response_model=ProductDetailResponse)
async def get_product(sku: str) -> ProductDetailResponse:
    svc = ProductService()
    try:
        result = await svc.get_product(sku)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Upstream failure: {exc}")

    if not result:
        raise HTTPException(status_code=404, detail=f"Product {sku} not found")
    return result


@router.get("/{sku}/live", response_model=ProductLiveResponse)
async def get_product_live(sku: str) -> ProductLiveResponse:
    svc = ProductService()
    try:
        result = await svc.get_product_live(sku)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Upstream failure: {exc}")

    if not result:
        raise HTTPException(status_code=404, detail=f"Product {sku} not found")
    return result
