# LEGO Arbitrage Dashboard — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a React dashboard with 4 tabs (Live Feed, Deal Checker, Inventar, History) connected to the existing FastAPI backend, plus backend extensions for inventory management and sell-signal detection.

**Architecture:** React 19 + Vite SPA served via Nginx. Communicates with FastAPI backend via REST. New `InventoryItem` model + `/api/inventory` router + Celery task for portfolio valuation. BrickMerge price history integration for sell-signal peak detection.

**Tech Stack:** React 19, Vite 6, Tailwind CSS 4, Zustand, TanStack Query, Recharts, React Router 7

---

## Phase 1: Backend Extensions (Tasks 1-5)

### Task 1: InventoryItem Model

**Files:**
- Create: `backend/app/models/inventory.py`
- Modify: `backend/app/models/__init__.py`

**Step 1: Create inventory model**

Create `backend/app/models/inventory.py`:

```python
"""Inventory model — tracks purchased LEGO sets for portfolio management."""

from datetime import date, datetime
from enum import Enum

from sqlalchemy import Date, Float, Index, Integer, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InventoryStatus(str, Enum):
    HOLDING = "HOLDING"
    SOLD = "SOLD"


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        Index("ix_inventory_status", "status"),
        Index("ix_inventory_set_number", "set_number"),
    )

    # Purchase info
    set_number: Mapped[str] = mapped_column(String(20), nullable=False)
    set_name: Mapped[str] = mapped_column(String(300), nullable=False)
    theme: Mapped[str | None] = mapped_column(String(100))
    image_url: Mapped[str | None] = mapped_column(Text)
    buy_price: Mapped[float] = mapped_column(Float, nullable=False)
    buy_shipping: Mapped[float] = mapped_column(Float, default=0.0)
    buy_date: Mapped[date] = mapped_column(Date, nullable=False)
    buy_platform: Mapped[str | None] = mapped_column(String(100))
    buy_url: Mapped[str | None] = mapped_column(Text)
    condition: Mapped[str] = mapped_column(String(20), default="NEW_SEALED")
    notes: Mapped[str | None] = mapped_column(Text)

    # Current valuation (auto-updated by Celery)
    current_market_price: Mapped[float | None] = mapped_column(Float)
    market_price_updated_at: Mapped[datetime | None] = mapped_column()
    unrealized_profit: Mapped[float | None] = mapped_column(Float)
    unrealized_roi_percent: Mapped[float | None] = mapped_column(Float)

    # Sell signal
    sell_signal_active: Mapped[bool] = mapped_column(Boolean, default=False)
    sell_signal_reason: Mapped[str | None] = mapped_column(Text)

    # Status & sale info
    status: Mapped[str] = mapped_column(String(20), default=InventoryStatus.HOLDING.value)
    sell_price: Mapped[float | None] = mapped_column(Float)
    sell_date: Mapped[date | None] = mapped_column(Date)
    sell_platform: Mapped[str | None] = mapped_column(String(100))
    realized_profit: Mapped[float | None] = mapped_column(Float)
    realized_roi_percent: Mapped[float | None] = mapped_column(Float)

    def __repr__(self) -> str:
        return f"<InventoryItem {self.set_number} '{self.set_name}' {self.status}>"
```

**Step 2: Register in models __init__**

Add to `backend/app/models/__init__.py`:

```python
from app.models.inventory import InventoryItem, InventoryStatus
```

And add `"InventoryItem"`, `"InventoryStatus"` to the `__all__` list.

**Step 3: Create Alembic migration**

Run: `cd backend && alembic revision --autogenerate -m "add inventory_items table"`
Then: `alembic upgrade head`

**Step 4: Commit**

```bash
git add backend/app/models/inventory.py backend/app/models/__init__.py backend/alembic/versions/
git commit -m "feat: add InventoryItem model for portfolio tracking"
```

---

### Task 2: Inventory API Router

**Files:**
- Create: `backend/app/api/routes/inventory.py`
- Modify: `backend/app/main.py`

**Step 1: Create inventory router**

Create `backend/app/api/routes/inventory.py`:

```python
"""Inventory API — portfolio tracking and sell-signal management."""

from datetime import date, datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import get_session
from app.models.inventory import InventoryItem, InventoryStatus

logger = structlog.get_logger()
router = APIRouter()


# ── Request/Response Models ────────────────────────────────

class InventoryAdd(BaseModel):
    set_number: str
    set_name: str
    theme: str | None = None
    image_url: str | None = None
    buy_price: float
    buy_shipping: float = 0.0
    buy_date: date
    buy_platform: str | None = None
    buy_url: str | None = None
    condition: str = "NEW_SEALED"
    notes: str | None = None


class InventoryUpdate(BaseModel):
    buy_price: float | None = None
    buy_shipping: float | None = None
    buy_date: date | None = None
    buy_platform: str | None = None
    condition: str | None = None
    notes: str | None = None


class SellRequest(BaseModel):
    sell_price: float
    sell_date: date | None = None
    sell_platform: str | None = None


class InventoryResponse(BaseModel):
    id: int
    set_number: str
    set_name: str
    theme: str | None
    image_url: str | None
    buy_price: float
    buy_shipping: float
    total_invested: float
    buy_date: date
    buy_platform: str | None
    condition: str
    notes: str | None
    current_market_price: float | None
    market_price_updated_at: datetime | None
    unrealized_profit: float | None
    unrealized_roi_percent: float | None
    sell_signal_active: bool
    sell_signal_reason: str | None
    status: str
    sell_price: float | None
    sell_date: date | None
    sell_platform: str | None
    realized_profit: float | None
    realized_roi_percent: float | None
    holding_days: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PortfolioSummary(BaseModel):
    total_items: int
    holding_items: int
    sold_items: int
    total_invested: float
    current_value: float
    unrealized_profit: float
    unrealized_roi_percent: float
    total_realized_profit: float
    sell_signals_active: int


# ── Routes ────────────────────────────────────────────────

@router.get("/", response_model=list[InventoryResponse])
async def list_inventory(
    status: str | None = Query(default=None),
    sort_by: str = Query(default="buy_date"),
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """List inventory items with optional status filter."""
    query = select(InventoryItem)
    if status:
        query = query.where(InventoryItem.status == status)
    query = query.order_by(InventoryItem.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(query)
    items = result.scalars().all()

    return [_to_response(item) for item in items]


@router.post("/", response_model=InventoryResponse)
async def add_inventory_item(data: InventoryAdd, session: AsyncSession = Depends(get_session)):
    """Add a purchased set to inventory."""
    item = InventoryItem(
        set_number=data.set_number,
        set_name=data.set_name,
        theme=data.theme,
        image_url=data.image_url,
        buy_price=data.buy_price,
        buy_shipping=data.buy_shipping,
        buy_date=data.buy_date,
        buy_platform=data.buy_platform,
        buy_url=data.buy_url,
        condition=data.condition,
        notes=data.notes,
        status=InventoryStatus.HOLDING.value,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    logger.info("inventory.added", set_number=data.set_number, buy_price=data.buy_price)
    return _to_response(item)


@router.patch("/{item_id}", response_model=InventoryResponse)
async def update_inventory_item(
    item_id: int,
    data: InventoryUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update an inventory item."""
    item = await _get_item(item_id, session)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    await session.commit()
    await session.refresh(item)
    return _to_response(item)


@router.post("/{item_id}/sell", response_model=InventoryResponse)
async def mark_as_sold(
    item_id: int,
    data: SellRequest,
    session: AsyncSession = Depends(get_session),
):
    """Mark an inventory item as sold."""
    item = await _get_item(item_id, session)
    if item.status == InventoryStatus.SOLD.value:
        raise HTTPException(status_code=400, detail="Item already sold")

    total_invested = item.buy_price + item.buy_shipping
    # Approximate selling costs (eBay fees)
    selling_costs = data.sell_price * 0.129 + 0.35 + data.sell_price * 0.019 + 0.35
    realized_profit = data.sell_price - total_invested - selling_costs

    item.status = InventoryStatus.SOLD.value
    item.sell_price = data.sell_price
    item.sell_date = data.sell_date or date.today()
    item.sell_platform = data.sell_platform
    item.realized_profit = round(realized_profit, 2)
    item.realized_roi_percent = round((realized_profit / total_invested) * 100, 1) if total_invested > 0 else 0
    item.sell_signal_active = False

    await session.commit()
    await session.refresh(item)
    logger.info("inventory.sold", set_number=item.set_number, profit=item.realized_profit)
    return _to_response(item)


@router.delete("/{item_id}")
async def delete_inventory_item(item_id: int, session: AsyncSession = Depends(get_session)):
    """Delete an inventory item permanently."""
    item = await _get_item(item_id, session)
    await session.delete(item)
    await session.commit()
    return {"status": "deleted", "id": item_id}


@router.get("/summary", response_model=PortfolioSummary)
async def portfolio_summary(session: AsyncSession = Depends(get_session)):
    """Get portfolio summary — total invested, current value, P/L."""
    result = await session.execute(select(InventoryItem))
    items = result.scalars().all()

    holding = [i for i in items if i.status == InventoryStatus.HOLDING.value]
    sold = [i for i in items if i.status == InventoryStatus.SOLD.value]

    total_invested = sum(i.buy_price + i.buy_shipping for i in holding)
    current_value = sum(i.current_market_price or (i.buy_price + i.buy_shipping) for i in holding)
    unrealized = current_value - total_invested

    return PortfolioSummary(
        total_items=len(items),
        holding_items=len(holding),
        sold_items=len(sold),
        total_invested=round(total_invested, 2),
        current_value=round(current_value, 2),
        unrealized_profit=round(unrealized, 2),
        unrealized_roi_percent=round((unrealized / total_invested) * 100, 1) if total_invested > 0 else 0,
        total_realized_profit=round(sum(i.realized_profit or 0 for i in sold), 2),
        sell_signals_active=sum(1 for i in holding if i.sell_signal_active),
    )


@router.get("/history", response_model=list[InventoryResponse])
async def inventory_history(session: AsyncSession = Depends(get_session)):
    """Get sold items with performance data."""
    result = await session.execute(
        select(InventoryItem)
        .where(InventoryItem.status == InventoryStatus.SOLD.value)
        .order_by(InventoryItem.sell_date.desc())
    )
    return [_to_response(item) for item in result.scalars().all()]


# ── Helpers ────────────────────────────────────────────────

async def _get_item(item_id: int, session: AsyncSession) -> InventoryItem:
    result = await session.execute(
        select(InventoryItem).where(InventoryItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail=f"Inventory item {item_id} not found")
    return item


def _to_response(item: InventoryItem) -> InventoryResponse:
    total_invested = item.buy_price + (item.buy_shipping or 0)
    holding_days = (date.today() - item.buy_date).days if item.buy_date else 0

    return InventoryResponse(
        id=item.id,
        set_number=item.set_number,
        set_name=item.set_name,
        theme=item.theme,
        image_url=item.image_url,
        buy_price=item.buy_price,
        buy_shipping=item.buy_shipping or 0,
        total_invested=round(total_invested, 2),
        buy_date=item.buy_date,
        buy_platform=item.buy_platform,
        condition=item.condition,
        notes=item.notes,
        current_market_price=item.current_market_price,
        market_price_updated_at=item.market_price_updated_at,
        unrealized_profit=item.unrealized_profit,
        unrealized_roi_percent=item.unrealized_roi_percent,
        sell_signal_active=item.sell_signal_active,
        sell_signal_reason=item.sell_signal_reason,
        status=item.status,
        sell_price=item.sell_price,
        sell_date=item.sell_date,
        sell_platform=item.sell_platform,
        realized_profit=item.realized_profit,
        realized_roi_percent=item.realized_roi_percent,
        holding_days=holding_days,
        created_at=item.created_at,
    )
```

**Step 2: Register router in main.py**

Add to `backend/app/main.py` after existing imports:

```python
from app.api.routes import sets, analysis, scout, watchlist, feedback, inventory
```

Add after existing `app.include_router` lines:

```python
app.include_router(inventory.router, prefix="/api/inventory", tags=["Inventory"])
```

**Step 3: Commit**

```bash
git add backend/app/api/routes/inventory.py backend/app/main.py
git commit -m "feat: add inventory API router with CRUD + portfolio summary"
```

---

### Task 3: BrickMerge Price History Integration

**Files:**
- Modify: `backend/app/scrapers/brickmerge.py`

**Step 1: Add price history method to BrickMergeScraper**

Add this method to the `BrickMergeScraper` class in `backend/app/scrapers/brickmerge.py`:

```python
async def get_price_history(self, set_number: str) -> list[dict] | None:
    """Get historical price data from BrickMerge for trend analysis.

    Returns list of dicts with keys: date, price, source.
    Used for sell-signal peak detection.
    """
    try:
        html = await self._fetch(f"{BASE_URL}/?sn={set_number}")
        soup = BeautifulSoup(html, "lxml")

        # BrickMerge embeds price history data in script tags or chart elements
        history = []

        # Look for chart/graph data in script tags
        scripts = soup.find_all("script")
        for script in scripts:
            text = script.string or ""
            # Look for price arrays or chart data
            date_matches = re.findall(r'"(\d{4}-\d{2}-\d{2})"', text)
            price_matches = re.findall(r'(\d+\.\d{2})', text)

            if date_matches and price_matches and len(date_matches) == len(price_matches):
                for d, p in zip(date_matches, price_matches):
                    history.append({
                        "date": d,
                        "price": float(p),
                        "source": "BRICKMERGE",
                    })

        if not history:
            logger.debug("brickmerge.no_history", set_number=set_number)
            return None

        return sorted(history, key=lambda x: x["date"])
    except Exception as e:
        logger.error("brickmerge.history_failed", set_number=set_number, error=str(e))
        return None
```

**Step 2: Commit**

```bash
git add backend/app/scrapers/brickmerge.py
git commit -m "feat: add BrickMerge price history scraping for trend analysis"
```

---

### Task 4: Inventory Valuation Celery Task

**Files:**
- Create: `backend/app/tasks/update_inventory.py`
- Modify: `backend/app/tasks/celery_app.py`

**Step 1: Create inventory update task**

Create `backend/app/tasks/update_inventory.py`:

```python
"""Inventory valuation and sell-signal detection — runs via Celery Beat."""

import asyncio
from datetime import datetime, timezone

import structlog
from sqlalchemy import select

from app.models.base import async_session
from app.models.inventory import InventoryItem, InventoryStatus
from app.models.set import SetCategory
from app.scrapers import PRICE_SCRAPERS
from app.scrapers.brickmerge import BrickMergeScraper
from app.engine.market_consensus import calculate_consensus
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()

# Optimal holding months per category (same as decision_engine)
OPTIMAL_HOLDING = {
    SetCategory.FRESH.value: 4.5,
    SetCategory.SWEET_SPOT.value: 12.0,
    SetCategory.ESTABLISHED.value: 24.0,
    SetCategory.VINTAGE.value: 42.0,
    SetCategory.LEGACY.value: 36.0,
}

# ROI targets per category
ROI_TARGETS = {
    SetCategory.FRESH.value: 50.0,
    SetCategory.SWEET_SPOT.value: 25.0,
    SetCategory.ESTABLISHED.value: 20.0,
    SetCategory.VINTAGE.value: 30.0,
    SetCategory.LEGACY.value: 40.0,
}


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _categorize_set(set_number: str, release_year: int | None = None) -> str:
    """Categorize set by age. Fallback to SWEET_SPOT if no year."""
    if not release_year:
        return SetCategory.SWEET_SPOT.value
    age = 2026 - release_year
    if age <= 1:
        return SetCategory.FRESH.value
    elif age <= 4:
        return SetCategory.SWEET_SPOT.value
    elif age <= 7:
        return SetCategory.ESTABLISHED.value
    elif age <= 11:
        return SetCategory.VINTAGE.value
    return SetCategory.LEGACY.value


def _detect_price_peak(history: list[dict] | None) -> bool:
    """Detect if current price is at or past peak using BrickMerge history.

    Returns True if price has been declining for the last 2+ data points
    after a peak — sell signal.
    """
    if not history or len(history) < 5:
        return False

    prices = [h["price"] for h in history]
    recent = prices[-5:]

    # Find if we're past a local maximum
    peak = max(recent)
    peak_idx = recent.index(peak)

    # If peak was in the middle and prices are declining after it
    if peak_idx < len(recent) - 1 and recent[-1] < peak * 0.97:
        return True

    return False


@celery_app.task(name="app.tasks.update_inventory.update_inventory_valuations")
def update_inventory_valuations() -> dict:
    """Update market prices and sell signals for all HOLDING inventory items."""
    return _run_async(_update_valuations_async())


async def _update_valuations_async() -> dict:
    summary = {"updated": 0, "sell_signals": 0, "errors": 0}
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        result = await session.execute(
            select(InventoryItem).where(
                InventoryItem.status == InventoryStatus.HOLDING.value
            )
        )
        items = result.scalars().all()

        for item in items:
            try:
                # Fetch current prices from all sources
                prices = []
                for scraper_cls in PRICE_SCRAPERS:
                    try:
                        async with scraper_cls() as scraper:
                            price = await scraper.get_price(item.set_number)
                            if price:
                                prices.append(price)
                    except Exception:
                        continue

                if not prices:
                    continue

                # Calculate consensus price
                consensus = calculate_consensus(prices)
                if consensus.consensus_price <= 0:
                    continue

                total_invested = item.buy_price + (item.buy_shipping or 0)

                # Update valuation
                item.current_market_price = round(consensus.consensus_price, 2)
                item.market_price_updated_at = now
                item.unrealized_profit = round(consensus.consensus_price - total_invested, 2)
                item.unrealized_roi_percent = round(
                    ((consensus.consensus_price - total_invested) / total_invested) * 100, 1
                ) if total_invested > 0 else 0

                # Sell-signal detection
                category = _categorize_set(item.set_number)
                roi_target = ROI_TARGETS.get(category, 25.0)
                optimal_months = OPTIMAL_HOLDING.get(category, 12.0)
                holding_days = (now.date() - item.buy_date).days
                holding_months = holding_days / 30.44

                signals = []

                # Signal 1: ROI target reached
                if item.unrealized_roi_percent and item.unrealized_roi_percent >= roi_target:
                    signals.append(f"ROI {item.unrealized_roi_percent:.0f}% hat Zielwert {roi_target:.0f}% erreicht")

                # Signal 2: Optimal holding period reached
                if holding_months >= optimal_months:
                    signals.append(f"Optimale Haltedauer ({optimal_months:.0f} Monate) erreicht")

                # Signal 3: Price peak detection via BrickMerge
                try:
                    async with BrickMergeScraper() as bm:
                        history = await bm.get_price_history(item.set_number)
                        if _detect_price_peak(history):
                            signals.append("Marktpreis am Hochpunkt — Trend dreht")
                except Exception:
                    pass

                if signals:
                    item.sell_signal_active = True
                    item.sell_signal_reason = " | ".join(signals)
                    summary["sell_signals"] += 1
                else:
                    item.sell_signal_active = False
                    item.sell_signal_reason = None

                summary["updated"] += 1

            except Exception as e:
                summary["errors"] += 1
                logger.error("inventory.update_failed", set_number=item.set_number, error=str(e))

        await session.commit()

    logger.info("inventory.valuations_updated", **summary)
    return summary
```

**Step 2: Register task in celery_app.py**

Add `"app.tasks.update_inventory"` to the `include` list in `backend/app/tasks/celery_app.py`.

Add to `beat_schedule`:

```python
# Update inventory valuations every 6 hours (with scrape cycle)
"update-inventory": {
    "task": "app.tasks.update_inventory.update_inventory_valuations",
    "schedule": crontab(minute=30, hour="*/6"),  # 30min after scrape
    "options": {"queue": "analysis"},
},
```

**Step 3: Commit**

```bash
git add backend/app/tasks/update_inventory.py backend/app/tasks/celery_app.py
git commit -m "feat: add Celery task for inventory valuation + sell-signal detection"
```

---

### Task 5: Backend Commit & Cleanup

**Step 1: Verify all backend changes**

Run: `cd backend && python -c "from app.models import InventoryItem; print('OK')"`

**Step 2: Commit all remaining backend changes**

```bash
git add -A backend/
git commit -m "feat: complete backend extensions for inventory management"
```

---

## Phase 2: Frontend Scaffold (Tasks 6-8)

### Task 6: Vite + React Project Setup

**Files:**
- Create: `frontend/` directory with Vite scaffold

**Step 1: Scaffold Vite project**

```bash
cd frontend-parent-dir
npm create vite@latest frontend -- --template react
cd frontend
npm install
```

**Step 2: Install dependencies**

```bash
npm install react-router-dom@7 @tanstack/react-query zustand recharts tailwindcss @tailwindcss/vite
```

**Step 3: Configure Tailwind**

Update `frontend/vite.config.js`:

```js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
```

Replace `frontend/src/index.css` with:

```css
@import "tailwindcss";

/* ── Custom Fonts ──────────────────────────────────── */
@import url("https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=DM+Sans:wght@400;500;600;700&display=swap");

/* ── Design Tokens ─────────────────────────────────── */
@theme {
  --color-bg-primary: #0f172a;
  --color-bg-card: #1e293b;
  --color-bg-hover: #334155;
  --color-border: #334155;
  --color-text-primary: #f1f5f9;
  --color-text-secondary: #94a3b8;
  --color-text-muted: #64748b;

  --color-lego-red: #e3000b;
  --color-lego-yellow: #f5c518;
  --color-lego-blue: #006db7;

  --color-go-star: #22c55e;
  --color-go: #4ade80;
  --color-check: #f59e0b;
  --color-no-go: #ef4444;
  --color-sell-signal: #06b6d4;

  --font-display: "DM Sans", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", "Fira Code", monospace;
}

body {
  font-family: var(--font-display);
  background: var(--color-bg-primary);
  color: var(--color-text-primary);
}
```

**Step 4: Commit**

```bash
git add frontend/
git commit -m "feat: scaffold React + Vite frontend with Tailwind config"
```

---

### Task 7: App Shell — Router + Layout + Tabs

**Files:**
- Create: `frontend/src/App.jsx`
- Create: `frontend/src/layouts/AppLayout.jsx`
- Create: `frontend/src/components/TabNav.jsx`

**Step 1: Create App with router**

`frontend/src/App.jsx`:

```jsx
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AppLayout from "./layouts/AppLayout";
import LiveFeed from "./pages/LiveFeed";
import DealChecker from "./pages/DealChecker";
import Inventar from "./pages/Inventar";
import History from "./pages/History";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchInterval: 30_000,
      staleTime: 10_000,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<LiveFeed />} />
            <Route path="checker" element={<DealChecker />} />
            <Route path="inventar" element={<Inventar />} />
            <Route path="history" element={<History />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
```

**Step 2: Create AppLayout**

`frontend/src/layouts/AppLayout.jsx`:

```jsx
import { Outlet } from "react-router-dom";
import TabNav from "../components/TabNav";
import SystemStatus from "../components/SystemStatus";

export default function AppLayout() {
  return (
    <div className="min-h-screen bg-bg-primary">
      {/* Desktop: top nav */}
      <header className="hidden md:block border-b border-border sticky top-0 z-50 bg-bg-primary/95 backdrop-blur">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-lego-yellow font-bold text-xl font-[family-name:var(--font-mono)]">
              LEGO
            </span>
            <span className="text-text-secondary text-sm">Arbitrage System</span>
          </div>
          <TabNav />
          <SystemStatus />
        </div>
      </header>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 py-6 pb-24 md:pb-6">
        <Outlet />
      </main>

      {/* Mobile: bottom tab bar */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 bg-bg-card border-t border-border z-50">
        <TabNav mobile />
      </nav>
    </div>
  );
}
```

**Step 3: Create TabNav**

`frontend/src/components/TabNav.jsx`:

```jsx
import { NavLink } from "react-router-dom";

const tabs = [
  { to: "/", label: "Feed", icon: "📡" },
  { to: "/checker", label: "Check", icon: "🔍" },
  { to: "/inventar", label: "Inventar", icon: "📦" },
  { to: "/history", label: "History", icon: "📊" },
];

export default function TabNav({ mobile = false }) {
  if (mobile) {
    return (
      <div className="flex justify-around py-2">
        {tabs.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.to === "/"}
            className={({ isActive }) =>
              `flex flex-col items-center gap-0.5 px-3 py-1 text-xs transition-colors ${
                isActive
                  ? "text-lego-yellow"
                  : "text-text-muted hover:text-text-secondary"
              }`
            }
          >
            <span className="text-lg">{tab.icon}</span>
            <span>{tab.label}</span>
          </NavLink>
        ))}
      </div>
    );
  }

  return (
    <div className="flex gap-1">
      {tabs.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.to === "/"}
          className={({ isActive }) =>
            `px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              isActive
                ? "bg-bg-hover text-lego-yellow"
                : "text-text-secondary hover:text-text-primary hover:bg-bg-hover/50"
            }`
          }
        >
          {tab.icon} {tab.label}
        </NavLink>
      ))}
    </div>
  );
}
```

**Step 4: Create placeholder pages**

Create `frontend/src/pages/LiveFeed.jsx`, `DealChecker.jsx`, `Inventar.jsx`, `History.jsx` — each as:

```jsx
export default function PageName() {
  return <div className="text-text-secondary">PageName — coming soon</div>;
}
```

**Step 5: Create SystemStatus component**

`frontend/src/components/SystemStatus.jsx`:

```jsx
import { useQuery } from "@tanstack/react-query";

export default function SystemStatus() {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: () => fetch("/api/../health").then((r) => r.json()),
    refetchInterval: 60_000,
  });

  return (
    <div className="flex items-center gap-2 text-xs">
      <div
        className={`w-2 h-2 rounded-full ${
          data?.status === "healthy" ? "bg-go-star animate-pulse" : isError ? "bg-no-go" : "bg-check"
        }`}
      />
      <span className="text-text-muted font-[family-name:var(--font-mono)]">
        {data?.version || "..."}
      </span>
    </div>
  );
}
```

**Step 6: Update main.jsx entry point**

`frontend/src/main.jsx`:

```jsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

**Step 7: Commit**

```bash
git add frontend/src/
git commit -m "feat: add app shell with router, layout, tab navigation"
```

---

### Task 8: API Client + Zustand Store

**Files:**
- Create: `frontend/src/api/client.js`
- Create: `frontend/src/stores/appStore.js`

**Step 1: Create API client**

`frontend/src/api/client.js`:

```js
const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `API Error ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Health
  health: () => fetch("/health").then((r) => r.json()),

  // Analysis
  analyze: (data) => request("/analysis/analyze", { method: "POST", body: JSON.stringify(data) }),

  // Scout
  scoutQuick: (setNumber) => request(`/scout/quick/${setNumber}`),
  scoutScan: (data) => request("/scout/scan", { method: "POST", body: JSON.stringify(data) }),

  // Sets
  listSets: (params) => request(`/sets/?${new URLSearchParams(params)}`),
  getSet: (setNumber) => request(`/sets/${setNumber}`),

  // Inventory
  listInventory: (params = {}) => request(`/inventory/?${new URLSearchParams(params)}`),
  addInventory: (data) => request("/inventory/", { method: "POST", body: JSON.stringify(data) }),
  updateInventory: (id, data) => request(`/inventory/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  sellInventory: (id, data) => request(`/inventory/${id}/sell`, { method: "POST", body: JSON.stringify(data) }),
  deleteInventory: (id) => request(`/inventory/${id}`, { method: "DELETE" }),
  portfolioSummary: () => request("/inventory/summary"),
  inventoryHistory: () => request("/inventory/history"),

  // Watchlist
  listWatchlist: () => request("/watchlist/"),
  addWatchlist: (data) => request("/watchlist/", { method: "POST", body: JSON.stringify(data) }),
  removeWatchlist: (id) => request(`/watchlist/${id}`, { method: "DELETE" }),
};
```

**Step 2: Create Zustand store**

`frontend/src/stores/appStore.js`:

```js
import { create } from "zustand";

export const useAppStore = create((set) => ({
  // Filters for Live Feed
  feedFilters: {
    verdict: null, // GO_STAR, GO, CHECK, NO_GO
    minRoi: 0,
    maxRisk: 10,
    theme: null,
  },
  setFeedFilters: (filters) =>
    set((state) => ({ feedFilters: { ...state.feedFilters, ...filters } })),

  // Last analysis result (for Deal Checker → Inventar flow)
  lastAnalysis: null,
  setLastAnalysis: (analysis) => set({ lastAnalysis: analysis }),
}));
```

**Step 3: Commit**

```bash
git add frontend/src/api/ frontend/src/stores/
git commit -m "feat: add API client and Zustand store"
```

---

## Phase 3: Dashboard Pages (Tasks 9-14)

### Task 9: Shared UI Components

**Files:**
- Create: `frontend/src/components/VerdictBadge.jsx`
- Create: `frontend/src/components/DealCard.jsx`
- Create: `frontend/src/components/StatCard.jsx`

**Step 1: VerdictBadge**

`frontend/src/components/VerdictBadge.jsx`:

```jsx
const verdictConfig = {
  GO_STAR: { label: "GO ⭐", bg: "bg-go-star", text: "text-black", pulse: true },
  GO: { label: "GO", bg: "bg-go", text: "text-black", pulse: false },
  CHECK: { label: "CHECK", bg: "bg-check", text: "text-black", pulse: false },
  NO_GO: { label: "NO-GO", bg: "bg-no-go", text: "text-white", pulse: false },
};

export default function VerdictBadge({ verdict, size = "md" }) {
  const config = verdictConfig[verdict] || verdictConfig.NO_GO;
  const sizeClass = size === "lg" ? "px-4 py-2 text-lg" : size === "sm" ? "px-2 py-0.5 text-xs" : "px-3 py-1 text-sm";

  return (
    <span
      className={`inline-flex items-center font-bold rounded-md font-[family-name:var(--font-mono)] ${config.bg} ${config.text} ${sizeClass} ${config.pulse ? "animate-pulse" : ""}`}
    >
      {config.label}
    </span>
  );
}
```

**Step 2: DealCard**

`frontend/src/components/DealCard.jsx`:

```jsx
import VerdictBadge from "./VerdictBadge";

export default function DealCard({ deal, onClick }) {
  const roi = deal.estimated_roi ?? deal.roi_percent;
  const roiColor = roi >= 30 ? "text-go-star" : roi >= 15 ? "text-go" : roi >= 0 ? "text-check" : "text-no-go";

  return (
    <div
      onClick={onClick}
      className="bg-bg-card border border-border rounded-xl p-4 hover:border-lego-blue/50 transition-all cursor-pointer group"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm font-semibold">
              {deal.set_number}
            </span>
            <VerdictBadge verdict={deal.recommendation} size="sm" />
          </div>
          <h3 className="text-text-primary text-sm font-medium truncate group-hover:text-lego-blue transition-colors">
            {deal.set_name || deal.offer_title}
          </h3>
          <p className="text-text-muted text-xs mt-1">{deal.platform || deal.theme}</p>
        </div>
        <div className="text-right shrink-0">
          <div className="text-text-primary font-[family-name:var(--font-mono)] font-semibold">
            {deal.price ?? deal.offer_price}€
          </div>
          <div className="text-text-muted text-xs">→ {deal.market_price}€</div>
          <div className={`font-[family-name:var(--font-mono)] text-sm font-bold ${roiColor}`}>
            {roi > 0 ? "+" : ""}{roi?.toFixed(1)}%
          </div>
        </div>
      </div>
      <div className="flex items-center justify-between mt-3 pt-3 border-t border-border/50">
        <span className="text-text-muted text-xs">Risk {deal.risk_score}/10</span>
        <span className="text-text-muted text-xs">Score {deal.opportunity_score}</span>
      </div>
    </div>
  );
}
```

**Step 3: StatCard**

`frontend/src/components/StatCard.jsx`:

```jsx
export default function StatCard({ label, value, sub, color = "text-text-primary" }) {
  return (
    <div className="bg-bg-card border border-border rounded-xl p-4">
      <div className="text-text-muted text-xs uppercase tracking-wider mb-1">{label}</div>
      <div className={`font-[family-name:var(--font-mono)] text-xl font-bold ${color}`}>{value}</div>
      {sub && <div className="text-text-secondary text-xs mt-1">{sub}</div>}
    </div>
  );
}
```

**Step 4: Commit**

```bash
git add frontend/src/components/
git commit -m "feat: add shared UI components — VerdictBadge, DealCard, StatCard"
```

---

### Task 10: Live Feed Page

**Files:**
- Modify: `frontend/src/pages/LiveFeed.jsx`

**Step 1: Implement Live Feed**

Replace `frontend/src/pages/LiveFeed.jsx` with full implementation using `useQuery` to fetch from `/api/scout/scan`, display DealCards in a responsive grid, and filter bar with verdict/ROI/risk filters using Zustand store.

Key features:
- `useQuery({ queryKey: ["feed"], queryFn: () => api.scoutScan(...) })` with 30s refetch
- Filter bar: verdict toggle buttons, min ROI slider, max risk slider
- Responsive grid: 1 col mobile, 2 col tablet, 3 col desktop
- Empty state when no deals match filters
- Loading skeleton cards

**Step 2: Commit**

```bash
git add frontend/src/pages/LiveFeed.jsx
git commit -m "feat: implement Live Feed page with auto-refresh and filters"
```

---

### Task 11: Deal Checker Page

**Files:**
- Modify: `frontend/src/pages/DealChecker.jsx`

**Step 1: Implement Deal Checker**

Key features:
- Large set number input + price input (auto-focus)
- Expandable "Optionen" section: condition select, shipping input, box damage toggle
- Submit calls `api.analyze({ set_number, offer_price, ... })`
- Result displays:
  - Full-width verdict banner (color-coded)
  - Source prices table (each scraper → price)
  - ROI breakdown (purchase → fees → profit)
  - Risk radar as horizontal bar segments
  - Suggestions as pill badges
- **"Gekauft" button** → opens modal → pre-fills `addInventory` form → saves to inventory

**Step 2: Commit**

```bash
git add frontend/src/pages/DealChecker.jsx
git commit -m "feat: implement Deal Checker with analysis + Gekauft flow"
```

---

### Task 12: Inventar (Portfolio) Page

**Files:**
- Modify: `frontend/src/pages/Inventar.jsx`

**Step 1: Implement Inventar**

Key features:
- **Portfolio summary bar** (sticky): total items, invested, current value, unrealized P/L, sell signals count
  - Uses `useQuery({ queryKey: ["portfolio"], queryFn: api.portfolioSummary })`
- **Inventory list**: Uses `useQuery({ queryKey: ["inventory"], queryFn: () => api.listInventory({ status: "HOLDING" }) })`
  - Card per item: set info, buy price, current market price, delta (€ + %), holding days
  - **Sell-signal badge**: pulsing cyan badge with reason text when `sell_signal_active`
  - Quick actions: "Verkauft" button → modal with sell price/date → calls `api.sellInventory()`
  - "Analysieren" button → navigates to Deal Checker pre-filled
  - "Entfernen" button with confirm → calls `api.deleteInventory()`
- **Add manually** button → modal form for sets not from Deal Checker

**Step 2: Commit**

```bash
git add frontend/src/pages/Inventar.jsx
git commit -m "feat: implement Inventar page with portfolio summary + sell signals"
```

---

### Task 13: History Page

**Files:**
- Modify: `frontend/src/pages/History.jsx`

**Step 1: Implement History**

Key features:
- **Stats summary row**: Total realized profit, avg ROI, best deal, worst deal, win rate
- **Sold items list**: `useQuery({ queryKey: ["history"], queryFn: api.inventoryHistory })`
  - Per item: set, buy → sell price, profit (€ + %), holding days, platform
- **Charts** (Recharts):
  - Profit per month (BarChart)
  - Cumulative profit (LineChart/AreaChart)
  - ROI distribution (histogram via BarChart with buckets)
- Graceful empty state when no history yet

**Step 2: Commit**

```bash
git add frontend/src/pages/History.jsx
git commit -m "feat: implement History page with performance charts"
```

---

### Task 14: Docker + Nginx Integration

**Files:**
- Create: `infra/Dockerfile.frontend`
- Modify: `docker-compose.yml`
- Modify: `infra/nginx.conf`

**Step 1: Create frontend Dockerfile**

`infra/Dockerfile.frontend`:

```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY infra/nginx-frontend.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

**Step 2: Add frontend service to docker-compose.yml**

```yaml
frontend:
  build:
    context: .
    dockerfile: infra/Dockerfile.frontend
  container_name: lego-frontend
  restart: unless-stopped
```

**Step 3: Update nginx.conf**

Update `infra/nginx.conf` to:
- Proxy `/api/` → `http://api:8000/api/`
- Proxy `/health` → `http://api:8000/health`
- Serve `/` → `http://frontend:80/` (React SPA)
- Add `try_files $uri /index.html` for SPA routing

**Step 4: Commit**

```bash
git add infra/ docker-compose.yml
git commit -m "feat: add frontend Docker build + Nginx reverse proxy"
```

---

## Summary

| Phase | Tasks | Description |
|-------|-------|-------------|
| 1 | 1-5 | Backend: InventoryItem model, API, BrickMerge history, Celery task |
| 2 | 6-8 | Frontend scaffold: Vite, Router, Layout, API client, Store |
| 3 | 9-14 | Pages: Components, Live Feed, Deal Checker, Inventar, History, Docker |

**Total: 14 tasks, 3 phases**
