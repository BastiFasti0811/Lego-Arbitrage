# LEGO Arbitrage Dashboard — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a React dashboard ("Brick Terminal") with 4 tabs — Live Feed, Deal Checker, Inventar, History — plus backend extensions for inventory tracking, portfolio valuation, and sell-signal detection.

**Architecture:** React SPA (Vite + Tailwind) served as static files via Nginx. Communicates with existing FastAPI backend via `/api/*`. New `InventoryItem` model + `/api/inventory` router + BrickMerge price history scraping + Celery task for portfolio valuation updates.

**Tech Stack:** React 19, Vite 6, Tailwind CSS 4, Zustand (state), Recharts (charts), Framer Motion (animations), Axios (HTTP). Backend: FastAPI, SQLAlchemy, Celery, PostgreSQL.

---

## Phase A: Backend Extensions

### Task 1: InventoryItem Model

**Files:**
- Create: `backend/app/models/inventory.py`
- Modify: `backend/app/models/__init__.py`

**Step 1: Create the inventory model**

```python
# backend/app/models/inventory.py
"""Inventory — tracks purchased LEGO sets for portfolio management."""

from datetime import date, datetime
from enum import Enum

from sqlalchemy import Date, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class InventoryStatus(str, Enum):
    HOLDING = "HOLDING"
    SOLD = "SOLD"


class InventoryItem(Base):
    """A purchased LEGO set in the user's portfolio."""

    __tablename__ = "inventory_items"
    __table_args__ = (
        Index("ix_inventory_status", "status"),
        Index("ix_inventory_set", "set_number"),
    )

    # Purchase info
    set_number: Mapped[str] = mapped_column(String(20), nullable=False)
    set_name: Mapped[str] = mapped_column(String(300), nullable=False)
    theme: Mapped[str | None] = mapped_column(String(100))
    image_url: Mapped[str | None] = mapped_column(Text)
    buy_price: Mapped[float] = mapped_column(Float, nullable=False)
    buy_shipping: Mapped[float] = mapped_column(Float, default=0.0)
    buy_date: Mapped[date] = mapped_column(Date, nullable=False)
    buy_platform: Mapped[str] = mapped_column(String(50), nullable=False)
    condition: Mapped[str] = mapped_column(String(20), default="NEW_SEALED")
    notes: Mapped[str | None] = mapped_column(Text)

    # Current valuation (auto-updated by Celery)
    current_market_price: Mapped[float | None] = mapped_column(Float)
    market_price_updated_at: Mapped[datetime | None] = mapped_column()

    # Sell signal
    sell_signal: Mapped[bool] = mapped_column(default=False)
    sell_signal_reason: Mapped[str | None] = mapped_column(Text)

    # Status
    status: Mapped[str] = mapped_column(String(10), default=InventoryStatus.HOLDING.value)

    # Sell info (filled when sold)
    sell_price: Mapped[float | None] = mapped_column(Float)
    sell_date: Mapped[date | None] = mapped_column(Date)
    sell_platform: Mapped[str | None] = mapped_column(String(50))

    def __repr__(self) -> str:
        return f"<InventoryItem {self.set_number} buy={self.buy_price}€ status={self.status}>"

    @property
    def total_buy_cost(self) -> float:
        return self.buy_price + (self.buy_shipping or 0)

    @property
    def unrealized_profit(self) -> float | None:
        if self.current_market_price is None:
            return None
        return self.current_market_price - self.total_buy_cost

    @property
    def unrealized_roi_percent(self) -> float | None:
        if self.current_market_price is None or self.total_buy_cost == 0:
            return None
        return round((self.unrealized_profit / self.total_buy_cost) * 100, 1)
```

**Step 2: Register in models __init__.py**

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

**Step 1: Create the inventory router**

```python
# backend/app/api/routes/inventory.py
"""Inventory API — portfolio tracking, valuation, sell signals."""

from datetime import date

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import get_session
from app.models.inventory import InventoryItem, InventoryStatus

logger = structlog.get_logger()
router = APIRouter()


class InventoryCreate(BaseModel):
    set_number: str
    set_name: str
    theme: str | None = None
    image_url: str | None = None
    buy_price: float
    buy_shipping: float = 0.0
    buy_date: date
    buy_platform: str
    condition: str = "NEW_SEALED"
    notes: str | None = None


class InventorySell(BaseModel):
    sell_price: float
    sell_date: date
    sell_platform: str


class InventoryUpdate(BaseModel):
    notes: str | None = None
    buy_price: float | None = None
    buy_shipping: float | None = None
    condition: str | None = None


class InventoryResponse(BaseModel):
    id: int
    set_number: str
    set_name: str
    theme: str | None
    image_url: str | None
    buy_price: float
    buy_shipping: float
    buy_date: date
    buy_platform: str
    condition: str
    notes: str | None
    current_market_price: float | None
    market_price_updated_at: str | None
    sell_signal: bool
    sell_signal_reason: str | None
    status: str
    sell_price: float | None
    sell_date: date | None
    sell_platform: str | None
    total_buy_cost: float
    unrealized_profit: float | None
    unrealized_roi_percent: float | None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class PortfolioSummary(BaseModel):
    total_items: int
    total_invested: float
    total_current_value: float
    total_unrealized_profit: float
    average_roi_percent: float
    items_with_sell_signal: int
    total_realized_profit: float
    total_sold: int


@router.get("/", response_model=list[InventoryResponse])
async def list_inventory(
    status: str | None = Query(default="HOLDING"),
    session: AsyncSession = Depends(get_session),
):
    """List inventory items, default HOLDING only."""
    query = select(InventoryItem)
    if status:
        query = query.where(InventoryItem.status == status)
    query = query.order_by(InventoryItem.buy_date.desc())
    result = await session.execute(query)
    items = result.scalars().all()
    return [_item_to_response(i) for i in items]


@router.get("/summary", response_model=PortfolioSummary)
async def portfolio_summary(session: AsyncSession = Depends(get_session)):
    """Get portfolio summary with totals."""
    # Holding items
    result = await session.execute(
        select(InventoryItem).where(InventoryItem.status == InventoryStatus.HOLDING.value)
    )
    holding = result.scalars().all()

    total_invested = sum(i.total_buy_cost for i in holding)
    total_value = sum(i.current_market_price or i.buy_price for i in holding)
    total_unrealized = total_value - total_invested
    avg_roi = (total_unrealized / total_invested * 100) if total_invested > 0 else 0
    signals = sum(1 for i in holding if i.sell_signal)

    # Sold items
    result_sold = await session.execute(
        select(InventoryItem).where(InventoryItem.status == InventoryStatus.SOLD.value)
    )
    sold = result_sold.scalars().all()
    total_realized = sum(
        (i.sell_price or 0) - i.total_buy_cost for i in sold
    )

    return PortfolioSummary(
        total_items=len(holding),
        total_invested=round(total_invested, 2),
        total_current_value=round(total_value, 2),
        total_unrealized_profit=round(total_unrealized, 2),
        average_roi_percent=round(avg_roi, 1),
        items_with_sell_signal=signals,
        total_realized_profit=round(total_realized, 2),
        total_sold=len(sold),
    )


@router.post("/", response_model=InventoryResponse)
async def add_to_inventory(data: InventoryCreate, session: AsyncSession = Depends(get_session)):
    """Add a purchased set to inventory."""
    item = InventoryItem(**data.model_dump())
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return _item_to_response(item)


@router.patch("/{item_id}", response_model=InventoryResponse)
async def update_inventory_item(
    item_id: int, data: InventoryUpdate, session: AsyncSession = Depends(get_session)
):
    """Update an inventory item (notes, price corrections)."""
    item = await _get_item(item_id, session)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    await session.commit()
    await session.refresh(item)
    return _item_to_response(item)


@router.post("/{item_id}/sell", response_model=InventoryResponse)
async def mark_as_sold(
    item_id: int, data: InventorySell, session: AsyncSession = Depends(get_session)
):
    """Mark an inventory item as sold."""
    item = await _get_item(item_id, session)
    item.status = InventoryStatus.SOLD.value
    item.sell_price = data.sell_price
    item.sell_date = data.sell_date
    item.sell_platform = data.sell_platform
    await session.commit()
    await session.refresh(item)
    return _item_to_response(item)


@router.delete("/{item_id}")
async def delete_inventory_item(item_id: int, session: AsyncSession = Depends(get_session)):
    """Delete an inventory item."""
    item = await _get_item(item_id, session)
    await session.delete(item)
    await session.commit()
    return {"status": "deleted", "id": item_id}


@router.get("/history", response_model=list[InventoryResponse])
async def inventory_history(session: AsyncSession = Depends(get_session)):
    """Get sold items (history)."""
    result = await session.execute(
        select(InventoryItem)
        .where(InventoryItem.status == InventoryStatus.SOLD.value)
        .order_by(InventoryItem.sell_date.desc())
    )
    return [_item_to_response(i) for i in result.scalars().all()]


async def _get_item(item_id: int, session: AsyncSession) -> InventoryItem:
    result = await session.execute(
        select(InventoryItem).where(InventoryItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail=f"Inventory item {item_id} not found")
    return item


def _item_to_response(item: InventoryItem) -> InventoryResponse:
    return InventoryResponse(
        id=item.id,
        set_number=item.set_number,
        set_name=item.set_name,
        theme=item.theme,
        image_url=item.image_url,
        buy_price=item.buy_price,
        buy_shipping=item.buy_shipping or 0,
        buy_date=item.buy_date,
        buy_platform=item.buy_platform,
        condition=item.condition,
        notes=item.notes,
        current_market_price=item.current_market_price,
        market_price_updated_at=item.market_price_updated_at.isoformat() if item.market_price_updated_at else None,
        sell_signal=item.sell_signal,
        sell_signal_reason=item.sell_signal_reason,
        status=item.status,
        sell_price=item.sell_price,
        sell_date=item.sell_date,
        sell_platform=item.sell_platform,
        total_buy_cost=item.total_buy_cost,
        unrealized_profit=item.unrealized_profit,
        unrealized_roi_percent=item.unrealized_roi_percent,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )
```

**Step 2: Register router in main.py**

Add to `backend/app/main.py`:
```python
from app.api.routes import sets, analysis, scout, watchlist, feedback, inventory
```
And:
```python
app.include_router(inventory.router, prefix="/api/inventory", tags=["Inventory"])
```

**Step 3: Commit**
```bash
git add backend/app/api/routes/inventory.py backend/app/main.py
git commit -m "feat: add inventory API router with CRUD, sell, and portfolio summary"
```

---

### Task 3: BrickMerge Price History Scraper Extension

**Files:**
- Modify: `backend/app/scrapers/brickmerge.py`

**Step 1: Add price history method to BrickMergeScraper**

Add this method to the `BrickMergeScraper` class in `backend/app/scrapers/brickmerge.py`:

```python
async def get_price_history(self, set_number: str) -> list[dict] | None:
    """Get price history data from BrickMerge for trend analysis.

    Returns list of {date, price} dicts for charting and sell-signal detection.
    """
    try:
        html = await self._fetch(f"{BASE_URL}/?sn={set_number}")
        soup = BeautifulSoup(html, "lxml")

        # BrickMerge embeds price history data in JavaScript/chart elements
        # Look for chart data in script tags
        history = []
        scripts = soup.find_all("script")
        for script in scripts:
            text = script.string or ""
            # Look for price data arrays (common chart.js patterns)
            date_matches = re.findall(r'"(\d{4}-\d{2}-\d{2})"', text)
            price_matches = re.findall(r'(\d+\.\d{2})', text)
            if date_matches and price_matches and len(date_matches) == len(price_matches):
                for d, p in zip(date_matches, price_matches):
                    price = float(p)
                    if 5.0 < price < 5000.0:
                        history.append({"date": d, "price": price})
                break

        # Fallback: look for table-based price history
        if not history:
            history_rows = soup.select(".price-history tr, [class*=history] tr")
            for row in history_rows:
                cells = row.find_all("td")
                if len(cells) >= 2:
                    date_text = cells[0].get_text(strip=True)
                    price_match = re.search(r"(\d+[.,]\d{2})", cells[1].get_text())
                    if price_match:
                        history.append({
                            "date": date_text,
                            "price": float(price_match.group(1).replace(",", ".")),
                        })

        return history if history else None
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

**Step 1: Create the valuation task**

```python
# backend/app/tasks/update_inventory.py
"""Celery task to update inventory valuations and detect sell signals."""

import asyncio
from datetime import datetime, timezone

import structlog
from sqlalchemy import select

from app.config import settings
from app.engine.decision_engine import _categorize_set, _get_min_roi, _get_holding_months
from app.models.base import async_session
from app.models.inventory import InventoryItem, InventoryStatus
from app.scrapers.brickmerge import BrickMergeScraper
from app.scrapers import PRICE_SCRAPERS
from app.engine.market_consensus import calculate_consensus
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


def _check_sell_signal(
    item: InventoryItem,
    market_price: float,
    price_history: list[dict] | None,
) -> tuple[bool, str | None]:
    """Determine if it's time to sell based on multiple signals."""
    reasons = []

    buy_cost = item.total_buy_cost
    roi = ((market_price - buy_cost) / buy_cost * 100) if buy_cost > 0 else 0

    # Signal 1: ROI target reached for category
    holding_days = (datetime.now(timezone.utc).date() - item.buy_date).days
    # Estimate release year from set_number pattern or default
    category = _categorize_set(2022)  # Conservative estimate
    min_roi = _get_min_roi(category)
    optimal_holding = _get_holding_months(category)

    if roi >= min_roi * 1.5:
        reasons.append(f"ROI {roi:.0f}% deutlich über Zielwert {min_roi:.0f}%")

    # Signal 2: Optimal holding duration reached
    holding_months = holding_days / 30.44
    if holding_months >= optimal_holding and roi > 0:
        reasons.append(f"Optimale Haltedauer ({optimal_holding:.0f}M) erreicht")

    # Signal 3: Price trend flattening (from BrickMerge history)
    if price_history and len(price_history) >= 5:
        recent = [p["price"] for p in price_history[-5:]]
        if len(recent) >= 3:
            # Check if price is plateauing or declining
            avg_recent = sum(recent[-3:]) / 3
            avg_earlier = sum(recent[:2]) / 2
            if avg_recent <= avg_earlier * 1.02:  # Less than 2% growth
                reasons.append("Preistrend flacht ab — Plateau erkannt")

    if reasons:
        return True, "VERKAUFEN: " + " | ".join(reasons)
    return False, None


@celery_app.task(name="app.tasks.update_inventory.update_inventory_valuations")
def update_inventory_valuations():
    """Update market prices and sell signals for all HOLDING inventory items."""
    asyncio.run(_update_inventory_async())


async def _update_inventory_async():
    logger.info("inventory.valuation_update.start")

    async with async_session() as session:
        result = await session.execute(
            select(InventoryItem).where(
                InventoryItem.status == InventoryStatus.HOLDING.value
            )
        )
        items = result.scalars().all()

        if not items:
            logger.info("inventory.valuation_update.empty")
            return

        for item in items:
            try:
                # Gather prices from all scrapers
                prices = []
                for scraper_cls in PRICE_SCRAPERS:
                    try:
                        async with scraper_cls() as scraper:
                            price = await scraper.get_price(item.set_number)
                            if price:
                                prices.append(price)
                    except Exception as e:
                        logger.warning("inventory.scraper_failed",
                                       scraper=scraper_cls.__name__,
                                       set_number=item.set_number,
                                       error=str(e))

                # Get BrickMerge price history
                price_history = None
                try:
                    async with BrickMergeScraper() as bm:
                        price_history = await bm.get_price_history(item.set_number)
                except Exception as e:
                    logger.warning("inventory.brickmerge_history_failed",
                                   set_number=item.set_number, error=str(e))

                # Calculate consensus
                if prices:
                    consensus = calculate_consensus(prices)
                    market_price = consensus.consensus_price
                else:
                    market_price = item.current_market_price or item.buy_price

                # Update item
                item.current_market_price = round(market_price, 2)
                item.market_price_updated_at = datetime.now(timezone.utc)

                # Check sell signal
                sell_signal, reason = _check_sell_signal(item, market_price, price_history)
                item.sell_signal = sell_signal
                item.sell_signal_reason = reason

                logger.info("inventory.item_updated",
                            set_number=item.set_number,
                            market_price=market_price,
                            sell_signal=sell_signal)

            except Exception as e:
                logger.error("inventory.item_update_failed",
                             set_number=item.set_number, error=str(e))

        await session.commit()
    logger.info("inventory.valuation_update.complete", items_updated=len(items))
```

**Step 2: Register in celery_app.py**

Add `"app.tasks.update_inventory"` to the `include` list in `backend/app/tasks/celery_app.py`.

Add to `beat_schedule`:
```python
# Update inventory valuations every 6 hours (alongside scraping)
"update-inventory-valuations": {
    "task": "app.tasks.update_inventory.update_inventory_valuations",
    "schedule": crontab(minute=30, hour="*/6"),  # 30 min after scrape
    "options": {"queue": "analysis"},
},
```

**Step 3: Commit**
```bash
git add backend/app/tasks/update_inventory.py backend/app/tasks/celery_app.py
git commit -m "feat: add Celery task for inventory valuation updates and sell signals"
```

---

## Phase B: Frontend Scaffolding

### Task 5: Vite + React + Tailwind Project Setup

**Files:**
- Create: `frontend/` directory with full Vite scaffold

**Step 1: Initialize the project**

```bash
cd frontend
npm create vite@latest . -- --template react
npm install
npm install tailwindcss @tailwindcss/vite
npm install zustand axios recharts framer-motion
npm install -D @types/react @types/react-dom
```

**Step 2: Configure Tailwind**

Update `frontend/vite.config.js`:
```javascript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
```

Replace `frontend/src/index.css` with:
```css
@import "tailwindcss";

@theme {
  --color-brick-bg: #0a0a0f;
  --color-brick-surface: #141420;
  --color-brick-border: #1e1e30;
  --color-brick-text: #e0e0e8;
  --color-brick-muted: #6b6b80;
  --color-brick-yellow: #FFD700;
  --color-brick-go-star: #06b6d4;
  --color-brick-go: #22c55e;
  --color-brick-check: #f59e0b;
  --color-brick-nogo: #ef4444;

  --font-mono: 'JetBrains Mono', monospace;
  --font-sans: 'DM Sans', sans-serif;
}
```

Add Google Fonts to `frontend/index.html` `<head>`:
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
```

**Step 3: Create base layout**

```
frontend/src/
├── App.jsx
├── main.jsx
├── index.css
├── api/
│   └── client.js          # Axios instance
├── store/
│   └── useStore.js         # Zustand store
├── components/
│   ├── Layout.jsx          # Shell with tab navigation
│   ├── TabBar.jsx          # Tab navigation
│   ├── DealCard.jsx        # Reusable deal card
│   ├── VerdictBadge.jsx    # GO/NO-GO badge
│   ├── RiskBadge.jsx       # Risk score badge
│   ├── PortfolioBar.jsx    # Portfolio summary bar
│   └── Sparkline.jsx       # Mini price chart
├── pages/
│   ├── LiveFeed.jsx
│   ├── DealChecker.jsx
│   ├── Inventar.jsx
│   └── History.jsx
└── utils/
    └── format.js           # Number/date formatting helpers
```

**Step 4: Commit**
```bash
git add frontend/
git commit -m "feat: scaffold React + Vite + Tailwind frontend with Brick Terminal theme"
```

---

### Task 6: API Client & Zustand Store

**Files:**
- Create: `frontend/src/api/client.js`
- Create: `frontend/src/store/useStore.js`
- Create: `frontend/src/utils/format.js`

**Step 1: Create API client**

```javascript
// frontend/src/api/client.js
import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// Analysis
export const analyzeOffer = (data) => api.post('/analysis/analyze', data)

// Scout
export const scoutDeals = (data) => api.post('/scout/scan', data)
export const quickScout = (setNumber) => api.get(`/scout/quick/${setNumber}`)

// Sets
export const listSets = (params) => api.get('/sets/', { params })
export const getSet = (setNumber) => api.get(`/sets/${setNumber}`)

// Inventory
export const listInventory = (status = 'HOLDING') => api.get('/inventory/', { params: { status } })
export const getPortfolioSummary = () => api.get('/inventory/summary')
export const addToInventory = (data) => api.post('/inventory/', data)
export const updateInventoryItem = (id, data) => api.patch(`/inventory/${id}`, data)
export const markAsSold = (id, data) => api.post(`/inventory/${id}/sell`, data)
export const deleteInventoryItem = (id) => api.delete(`/inventory/${id}`)
export const getInventoryHistory = () => api.get('/inventory/history')

// Watchlist
export const listWatchlist = () => api.get('/watchlist/')
export const addToWatchlist = (data) => api.post('/watchlist/', data)

// Health
export const healthCheck = () => api.get('/health')

export default api
```

**Step 2: Create Zustand store**

```javascript
// frontend/src/store/useStore.js
import { create } from 'zustand'

const useStore = create((set, get) => ({
  // Active tab
  activeTab: 'feed',
  setActiveTab: (tab) => set({ activeTab: tab }),

  // Live Feed
  deals: [],
  dealsLoading: false,
  setDeals: (deals) => set({ deals }),
  setDealsLoading: (loading) => set({ dealsLoading: loading }),

  // Filters
  filters: {
    verdict: null,
    theme: null,
    minRoi: null,
    maxRisk: null,
  },
  setFilter: (key, value) => set((state) => ({
    filters: { ...state.filters, [key]: value },
  })),

  // Deal Checker
  analysisResult: null,
  analysisLoading: false,
  setAnalysisResult: (result) => set({ analysisResult: result }),
  setAnalysisLoading: (loading) => set({ analysisLoading: loading }),

  // Inventory
  inventory: [],
  inventoryLoading: false,
  portfolioSummary: null,
  setInventory: (items) => set({ inventory: items }),
  setInventoryLoading: (loading) => set({ inventoryLoading: loading }),
  setPortfolioSummary: (summary) => set({ portfolioSummary: summary }),

  // History
  history: [],
  historyLoading: false,
  setHistory: (items) => set({ history: items }),
  setHistoryLoading: (loading) => set({ historyLoading: loading }),

  // System status
  systemHealth: null,
  setSystemHealth: (health) => set({ systemHealth: health }),
}))

export default useStore
```

**Step 3: Create format utilities**

```javascript
// frontend/src/utils/format.js
export const formatEuro = (value) => {
  if (value == null) return '—'
  return new Intl.NumberFormat('de-DE', {
    style: 'currency',
    currency: 'EUR',
  }).format(value)
}

export const formatPercent = (value) => {
  if (value == null) return '—'
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}%`
}

export const formatDate = (dateStr) => {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleDateString('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  })
}

export const formatRelativeTime = (dateStr) => {
  if (!dateStr) return '—'
  const diff = Date.now() - new Date(dateStr).getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 60) return `vor ${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `vor ${hours}h`
  const days = Math.floor(hours / 24)
  return `vor ${days}d`
}

export const verdictColor = (verdict) => ({
  GO_STAR: 'text-brick-go-star',
  GO: 'text-brick-go',
  CHECK: 'text-brick-check',
  NO_GO: 'text-brick-nogo',
}[verdict] || 'text-brick-muted')

export const verdictBgColor = (verdict) => ({
  GO_STAR: 'bg-brick-go-star/15 border-brick-go-star/30',
  GO: 'bg-brick-go/15 border-brick-go/30',
  CHECK: 'bg-brick-check/15 border-brick-check/30',
  NO_GO: 'bg-brick-nogo/15 border-brick-nogo/30',
}[verdict] || 'bg-brick-surface border-brick-border')
```

**Step 4: Commit**
```bash
git add frontend/src/api/ frontend/src/store/ frontend/src/utils/
git commit -m "feat: add API client, Zustand store, and formatting utilities"
```

---

### Task 7: Layout Shell & Tab Navigation

**Files:**
- Create: `frontend/src/components/Layout.jsx`
- Create: `frontend/src/components/TabBar.jsx`
- Modify: `frontend/src/App.jsx`

**Step 1: Create TabBar**

```jsx
// frontend/src/components/TabBar.jsx
import { motion } from 'framer-motion'
import useStore from '../store/useStore'

const tabs = [
  { id: 'feed', label: 'Live Feed', icon: '◉' },
  { id: 'checker', label: 'Deal Check', icon: '⌕' },
  { id: 'inventar', label: 'Inventar', icon: '▤' },
  { id: 'history', label: 'History', icon: '↗' },
]

export default function TabBar() {
  const { activeTab, setActiveTab } = useStore()

  return (
    <nav className="flex border-b border-brick-border bg-brick-surface/50 backdrop-blur-sm sticky top-0 z-50">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => setActiveTab(tab.id)}
          className={`relative flex-1 py-3 px-4 text-sm font-medium font-sans transition-colors
            ${activeTab === tab.id ? 'text-brick-yellow' : 'text-brick-muted hover:text-brick-text'}`}
        >
          <span className="font-mono mr-1.5">{tab.icon}</span>
          <span className="hidden sm:inline">{tab.label}</span>
          {activeTab === tab.id && (
            <motion.div
              layoutId="activeTab"
              className="absolute bottom-0 left-0 right-0 h-0.5 bg-brick-yellow"
              transition={{ type: 'spring', stiffness: 500, damping: 30 }}
            />
          )}
        </button>
      ))}
    </nav>
  )
}
```

**Step 2: Create Layout**

```jsx
// frontend/src/components/Layout.jsx
import TabBar from './TabBar'

export default function Layout({ children }) {
  return (
    <div className="min-h-screen bg-brick-bg text-brick-text font-sans">
      <header className="px-4 py-3 flex items-center justify-between border-b border-brick-border">
        <div className="flex items-center gap-2">
          <span className="text-brick-yellow font-mono font-bold text-lg">◧</span>
          <h1 className="font-bold text-sm tracking-wide uppercase">Brick Terminal</h1>
        </div>
        <div className="flex items-center gap-2 text-xs text-brick-muted font-mono">
          <span className="w-2 h-2 rounded-full bg-brick-go animate-pulse" />
          <span>System OK</span>
        </div>
      </header>
      <TabBar />
      <main className="p-4 max-w-7xl mx-auto">
        {children}
      </main>
    </div>
  )
}
```

**Step 3: Wire up App.jsx**

```jsx
// frontend/src/App.jsx
import Layout from './components/Layout'
import useStore from './store/useStore'
import LiveFeed from './pages/LiveFeed'
import DealChecker from './pages/DealChecker'
import Inventar from './pages/Inventar'
import History from './pages/History'

const pages = {
  feed: LiveFeed,
  checker: DealChecker,
  inventar: Inventar,
  history: History,
}

export default function App() {
  const activeTab = useStore((s) => s.activeTab)
  const Page = pages[activeTab]

  return (
    <Layout>
      <Page />
    </Layout>
  )
}
```

**Step 4: Commit**
```bash
git add frontend/src/components/Layout.jsx frontend/src/components/TabBar.jsx frontend/src/App.jsx
git commit -m "feat: add Layout shell with Brick Terminal header and tab navigation"
```

---

### Task 8: Shared Components — VerdictBadge, RiskBadge, DealCard

**Files:**
- Create: `frontend/src/components/VerdictBadge.jsx`
- Create: `frontend/src/components/RiskBadge.jsx`
- Create: `frontend/src/components/DealCard.jsx`

**Step 1: VerdictBadge**

```jsx
// frontend/src/components/VerdictBadge.jsx
import { motion } from 'framer-motion'
import { verdictColor, verdictBgColor } from '../utils/format'

const labels = {
  GO_STAR: 'GO ★',
  GO: 'GO',
  CHECK: 'CHECK',
  NO_GO: 'NO-GO',
}

export default function VerdictBadge({ verdict, size = 'md' }) {
  const sizeClasses = {
    sm: 'text-xs px-2 py-0.5',
    md: 'text-sm px-3 py-1',
    lg: 'text-lg px-4 py-2 font-bold',
  }

  return (
    <motion.span
      initial={{ scale: 0.8, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      className={`inline-block font-mono font-semibold rounded border
        ${verdictColor(verdict)} ${verdictBgColor(verdict)} ${sizeClasses[size]}`}
    >
      {labels[verdict] || verdict}
    </motion.span>
  )
}
```

**Step 2: RiskBadge**

```jsx
// frontend/src/components/RiskBadge.jsx
const riskColor = (score) => {
  if (score <= 2) return 'text-brick-go bg-brick-go/10'
  if (score <= 5) return 'text-brick-check bg-brick-check/10'
  if (score <= 7) return 'text-orange-400 bg-orange-400/10'
  return 'text-brick-nogo bg-brick-nogo/10'
}

export default function RiskBadge({ score }) {
  return (
    <span className={`font-mono text-xs px-2 py-0.5 rounded ${riskColor(score)}`}>
      R{score}/10
    </span>
  )
}
```

**Step 3: DealCard**

```jsx
// frontend/src/components/DealCard.jsx
import { motion } from 'framer-motion'
import VerdictBadge from './VerdictBadge'
import RiskBadge from './RiskBadge'
import { formatEuro, formatPercent } from '../utils/format'

export default function DealCard({ deal, index = 0 }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05 }}
      className="bg-brick-surface border border-brick-border rounded-lg p-4 hover:border-brick-yellow/30 transition-colors"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="font-mono text-brick-yellow text-sm font-semibold">
              {deal.set_number}
            </span>
            <VerdictBadge verdict={deal.recommendation} size="sm" />
            <RiskBadge score={deal.risk_score} />
          </div>
          <p className="text-sm text-brick-text truncate">{deal.set_name || deal.offer_title}</p>
          <p className="text-xs text-brick-muted mt-1">{deal.platform}</p>
        </div>
        <div className="text-right shrink-0">
          <p className="font-mono text-sm">
            {formatEuro(deal.price || deal.offer_price)}
            <span className="text-brick-muted mx-1">→</span>
            {formatEuro(deal.market_price)}
          </p>
          <p className={`font-mono text-sm font-bold ${deal.estimated_roi >= 0 ? 'text-brick-go' : 'text-brick-nogo'}`}>
            {formatPercent(deal.estimated_roi || deal.roi_percent)}
          </p>
        </div>
      </div>
      {deal.offer_url && (
        <a
          href={deal.offer_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-brick-yellow/60 hover:text-brick-yellow mt-2 inline-block font-mono"
        >
          → Angebot öffnen
        </a>
      )}
    </motion.div>
  )
}
```

**Step 4: Commit**
```bash
git add frontend/src/components/VerdictBadge.jsx frontend/src/components/RiskBadge.jsx frontend/src/components/DealCard.jsx
git commit -m "feat: add VerdictBadge, RiskBadge, and DealCard shared components"
```

---

### Task 9: Live Feed Page

**Files:**
- Create: `frontend/src/pages/LiveFeed.jsx`

**Step 1: Implement LiveFeed**

```jsx
// frontend/src/pages/LiveFeed.jsx
import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import useStore from '../store/useStore'
import DealCard from '../components/DealCard'
import { scoutDeals, healthCheck } from '../api/client'

const VERDICT_OPTIONS = ['ALL', 'GO_STAR', 'GO', 'CHECK', 'NO_GO']

export default function LiveFeed() {
  const { deals, setDeals, dealsLoading, setDealsLoading, systemHealth, setSystemHealth } = useStore()
  const [verdictFilter, setVerdictFilter] = useState('ALL')
  const [minRoi, setMinRoi] = useState('')

  useEffect(() => {
    loadDeals()
    checkHealth()
    const interval = setInterval(checkHealth, 60000)
    return () => clearInterval(interval)
  }, [])

  const checkHealth = async () => {
    try {
      const { data } = await healthCheck()
      setSystemHealth(data)
    } catch {
      setSystemHealth(null)
    }
  }

  const loadDeals = async () => {
    setDealsLoading(true)
    try {
      const { data } = await scoutDeals({
        set_numbers: [], // Will be populated from watchlist
        min_roi: 10,
      })
      setDeals(data.deals || [])
    } catch (err) {
      console.error('Failed to load deals:', err)
    } finally {
      setDealsLoading(false)
    }
  }

  const filtered = deals.filter((d) => {
    if (verdictFilter !== 'ALL' && d.recommendation !== verdictFilter) return false
    if (minRoi && d.estimated_roi < parseFloat(minRoi)) return false
    return true
  })

  return (
    <div>
      {/* Status Bar */}
      <div className="flex items-center gap-4 mb-4 text-xs font-mono text-brick-muted">
        <span className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full ${systemHealth ? 'bg-brick-go' : 'bg-brick-nogo'}`} />
          {systemHealth ? `v${systemHealth.version}` : 'Offline'}
        </span>
        <span>{deals.length} Deals</span>
        <button
          onClick={loadDeals}
          className="text-brick-yellow hover:text-brick-yellow/80 transition-colors"
        >
          ↻ Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 mb-4">
        {VERDICT_OPTIONS.map((v) => (
          <button
            key={v}
            onClick={() => setVerdictFilter(v)}
            className={`px-3 py-1 text-xs font-mono rounded border transition-colors
              ${verdictFilter === v
                ? 'border-brick-yellow text-brick-yellow bg-brick-yellow/10'
                : 'border-brick-border text-brick-muted hover:border-brick-muted'}`}
          >
            {v === 'ALL' ? 'Alle' : v.replace('_', ' ')}
          </button>
        ))}
        <input
          type="number"
          placeholder="Min ROI %"
          value={minRoi}
          onChange={(e) => setMinRoi(e.target.value)}
          className="w-24 px-2 py-1 text-xs font-mono bg-brick-surface border border-brick-border rounded text-brick-text placeholder:text-brick-muted focus:border-brick-yellow outline-none"
        />
      </div>

      {/* Deal Cards */}
      {dealsLoading ? (
        <div className="text-center py-12 text-brick-muted font-mono text-sm">
          <motion.span animate={{ opacity: [0.3, 1, 0.3] }} transition={{ repeat: Infinity, duration: 1.5 }}>
            Scanning...
          </motion.span>
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-12 text-brick-muted text-sm">
          Keine Deals gefunden. Watchlist füllen oder Filter anpassen.
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((deal, i) => (
            <DealCard key={`${deal.set_number}-${deal.offer_url}`} deal={deal} index={i} />
          ))}
        </div>
      )}
    </div>
  )
}
```

**Step 2: Commit**
```bash
git add frontend/src/pages/LiveFeed.jsx
git commit -m "feat: add Live Feed page with deal cards, filters, and auto-refresh"
```

---

### Task 10: Deal Checker Page

**Files:**
- Create: `frontend/src/pages/DealChecker.jsx`

**Step 1: Implement DealChecker with analysis form and result display**

```jsx
// frontend/src/pages/DealChecker.jsx
import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import useStore from '../store/useStore'
import VerdictBadge from '../components/VerdictBadge'
import RiskBadge from '../components/RiskBadge'
import { analyzeOffer, addToInventory } from '../api/client'
import { formatEuro, formatPercent, verdictBgColor } from '../utils/format'

export default function DealChecker() {
  const { analysisResult, setAnalysisResult, analysisLoading, setAnalysisLoading } = useStore()
  const [form, setForm] = useState({
    set_number: '',
    offer_price: '',
    condition: 'NEW_SEALED',
    box_damage: false,
    purchase_shipping: '',
  })
  const [buySuccess, setBuySuccess] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setAnalysisLoading(true)
    setAnalysisResult(null)
    setBuySuccess(false)
    try {
      const { data } = await analyzeOffer({
        set_number: form.set_number,
        offer_price: parseFloat(form.offer_price),
        condition: form.condition,
        box_damage: form.box_damage,
        purchase_shipping: form.purchase_shipping ? parseFloat(form.purchase_shipping) : null,
      })
      setAnalysisResult(data)
    } catch (err) {
      console.error('Analysis failed:', err)
    } finally {
      setAnalysisLoading(false)
    }
  }

  const handleBuy = async () => {
    if (!analysisResult) return
    try {
      await addToInventory({
        set_number: analysisResult.set_number,
        set_name: analysisResult.set_name,
        theme: analysisResult.theme,
        buy_price: analysisResult.offer_price,
        buy_shipping: analysisResult.total_purchase_cost - analysisResult.offer_price,
        buy_date: new Date().toISOString().split('T')[0],
        buy_platform: 'Manual',
        condition: form.condition,
      })
      setBuySuccess(true)
    } catch (err) {
      console.error('Failed to add to inventory:', err)
    }
  }

  const r = analysisResult

  return (
    <div className="max-w-2xl mx-auto">
      {/* Input Form */}
      <form onSubmit={handleSubmit} className="bg-brick-surface border border-brick-border rounded-lg p-4 mb-6">
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2 sm:col-span-1">
            <label className="block text-xs text-brick-muted mb-1 font-mono">Set-Nummer</label>
            <input
              type="text"
              value={form.set_number}
              onChange={(e) => setForm({ ...form, set_number: e.target.value })}
              placeholder="z.B. 75192"
              required
              className="w-full px-3 py-2 bg-brick-bg border border-brick-border rounded text-sm font-mono text-brick-text placeholder:text-brick-muted focus:border-brick-yellow outline-none"
            />
          </div>
          <div className="col-span-2 sm:col-span-1">
            <label className="block text-xs text-brick-muted mb-1 font-mono">Angebotspreis (€)</label>
            <input
              type="number"
              step="0.01"
              value={form.offer_price}
              onChange={(e) => setForm({ ...form, offer_price: e.target.value })}
              placeholder="z.B. 149.99"
              required
              className="w-full px-3 py-2 bg-brick-bg border border-brick-border rounded text-sm font-mono text-brick-text placeholder:text-brick-muted focus:border-brick-yellow outline-none"
            />
          </div>
          <div>
            <label className="block text-xs text-brick-muted mb-1 font-mono">Zustand</label>
            <select
              value={form.condition}
              onChange={(e) => setForm({ ...form, condition: e.target.value })}
              className="w-full px-3 py-2 bg-brick-bg border border-brick-border rounded text-sm font-mono text-brick-text focus:border-brick-yellow outline-none"
            >
              <option value="NEW_SEALED">Neu & Versiegelt</option>
              <option value="NEW_OPEN_BOX">Neu, Karton offen</option>
              <option value="USED_COMPLETE">Gebraucht, vollständig</option>
            </select>
          </div>
          <div className="flex items-end gap-4">
            <label className="flex items-center gap-2 text-xs text-brick-muted cursor-pointer">
              <input
                type="checkbox"
                checked={form.box_damage}
                onChange={(e) => setForm({ ...form, box_damage: e.target.checked })}
                className="accent-brick-yellow"
              />
              <span className="font-mono">Box-Schaden</span>
            </label>
          </div>
          <div className="col-span-2">
            <label className="block text-xs text-brick-muted mb-1 font-mono">Versandkosten (€, optional)</label>
            <input
              type="number"
              step="0.01"
              value={form.purchase_shipping}
              onChange={(e) => setForm({ ...form, purchase_shipping: e.target.value })}
              placeholder="wird geschätzt wenn leer"
              className="w-full px-3 py-2 bg-brick-bg border border-brick-border rounded text-sm font-mono text-brick-text placeholder:text-brick-muted focus:border-brick-yellow outline-none"
            />
          </div>
        </div>
        <button
          type="submit"
          disabled={analysisLoading}
          className="mt-4 w-full py-3 bg-brick-yellow text-brick-bg font-bold font-mono rounded hover:bg-brick-yellow/90 transition-colors disabled:opacity-50"
        >
          {analysisLoading ? 'Analysiere...' : 'ANALYSIEREN'}
        </button>
      </form>

      {/* Result */}
      <AnimatePresence>
        {r && (
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            className={`border rounded-lg p-5 ${verdictBgColor(r.recommendation)}`}
          >
            {/* Verdict Header */}
            <div className="flex items-center justify-between mb-4">
              <div>
                <p className="font-mono text-brick-yellow text-lg font-bold">{r.set_number}</p>
                <p className="text-sm text-brick-text">{r.set_name}</p>
                <p className="text-xs text-brick-muted">{r.theme} · {r.release_year} · {r.category}</p>
              </div>
              <VerdictBadge verdict={r.recommendation} size="lg" />
            </div>

            {/* Reason */}
            <p className="text-sm mb-4 text-brick-text">{r.reason}</p>

            {/* Key Metrics */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
              {[
                { label: 'ROI', value: formatPercent(r.roi_percent) },
                { label: 'Gewinn', value: formatEuro(r.net_profit) },
                { label: 'Risk', value: <RiskBadge score={r.risk_score} /> },
                { label: 'Konfidenz', value: `${(r.confidence * 100).toFixed(0)}%` },
              ].map(({ label, value }) => (
                <div key={label} className="bg-brick-bg/50 rounded p-2">
                  <p className="text-xs text-brick-muted font-mono">{label}</p>
                  <p className="font-mono text-sm font-semibold">{value}</p>
                </div>
              ))}
            </div>

            {/* Price Sources */}
            <details className="mb-3">
              <summary className="text-xs text-brick-muted cursor-pointer font-mono hover:text-brick-text">
                Preisquellen ({r.num_sources})
              </summary>
              <div className="mt-2 grid grid-cols-2 gap-1">
                {Object.entries(r.source_prices).map(([source, price]) => (
                  <div key={source} className="flex justify-between text-xs font-mono px-2 py-1 bg-brick-bg/30 rounded">
                    <span className="text-brick-muted">{source}</span>
                    <span>{formatEuro(price)}</span>
                  </div>
                ))}
              </div>
            </details>

            {/* ROI Breakdown */}
            <details className="mb-3">
              <summary className="text-xs text-brick-muted cursor-pointer font-mono hover:text-brick-text">
                ROI-Berechnung
              </summary>
              <div className="mt-2 text-xs font-mono space-y-1">
                <div className="flex justify-between"><span>Kaufpreis</span><span>{formatEuro(r.offer_price)}</span></div>
                <div className="flex justify-between"><span>+ Kaufnebenkosten</span><span>{formatEuro(r.total_purchase_cost - r.offer_price)}</span></div>
                <div className="flex justify-between border-t border-brick-border pt-1"><span>= Gesamtkosten</span><span>{formatEuro(r.total_purchase_cost)}</span></div>
                <div className="flex justify-between"><span>Marktpreis</span><span>{formatEuro(r.market_price)}</span></div>
                <div className="flex justify-between"><span>- Verkaufskosten</span><span>{formatEuro(r.total_selling_costs)}</span></div>
                <div className="flex justify-between border-t border-brick-border pt-1 font-bold">
                  <span>= Netto-Gewinn</span><span className={r.net_profit >= 0 ? 'text-brick-go' : 'text-brick-nogo'}>{formatEuro(r.net_profit)}</span>
                </div>
              </div>
            </details>

            {/* Suggestions */}
            {r.suggestions?.length > 0 && (
              <div className="mt-3 space-y-1">
                {r.suggestions.map((s, i) => (
                  <p key={i} className="text-xs text-brick-muted">
                    <span className="text-brick-yellow mr-1">→</span>{s}
                  </p>
                ))}
              </div>
            )}

            {/* Buy Button */}
            {(r.recommendation === 'GO' || r.recommendation === 'GO_STAR') && (
              <button
                onClick={handleBuy}
                disabled={buySuccess}
                className="mt-4 w-full py-2 bg-brick-go/20 border border-brick-go/40 text-brick-go font-mono font-semibold rounded hover:bg-brick-go/30 transition-colors disabled:opacity-50"
              >
                {buySuccess ? '✓ Im Inventar gespeichert' : 'GEKAUFT — Ins Inventar'}
              </button>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
```

**Step 2: Commit**
```bash
git add frontend/src/pages/DealChecker.jsx
git commit -m "feat: add Deal Checker page with analysis form, result display, and buy action"
```

---

### Task 11: Inventar Page (Portfolio)

**Files:**
- Create: `frontend/src/components/PortfolioBar.jsx`
- Create: `frontend/src/pages/Inventar.jsx`

**Step 1: Create PortfolioBar**

```jsx
// frontend/src/components/PortfolioBar.jsx
import { formatEuro, formatPercent } from '../utils/format'

export default function PortfolioBar({ summary }) {
  if (!summary) return null

  const profitColor = summary.total_unrealized_profit >= 0 ? 'text-brick-go' : 'text-brick-nogo'

  return (
    <div className="bg-brick-surface border border-brick-border rounded-lg p-4 mb-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-center">
        <div>
          <p className="text-xs text-brick-muted font-mono">Sets</p>
          <p className="font-mono text-lg font-bold text-brick-yellow">{summary.total_items}</p>
        </div>
        <div>
          <p className="text-xs text-brick-muted font-mono">Investiert</p>
          <p className="font-mono text-lg font-bold">{formatEuro(summary.total_invested)}</p>
        </div>
        <div>
          <p className="text-xs text-brick-muted font-mono">Aktueller Wert</p>
          <p className="font-mono text-lg font-bold">{formatEuro(summary.total_current_value)}</p>
        </div>
        <div>
          <p className="text-xs text-brick-muted font-mono">Gewinn</p>
          <p className={`font-mono text-lg font-bold ${profitColor}`}>
            {formatEuro(summary.total_unrealized_profit)} ({formatPercent(summary.average_roi_percent)})
          </p>
        </div>
      </div>
      {summary.items_with_sell_signal > 0 && (
        <div className="mt-3 text-center">
          <span className="inline-flex items-center gap-1.5 text-xs font-mono text-brick-go bg-brick-go/10 px-3 py-1 rounded-full animate-pulse">
            <span className="w-2 h-2 rounded-full bg-brick-go" />
            {summary.items_with_sell_signal} Set(s) mit Sell-Signal
          </span>
        </div>
      )}
    </div>
  )
}
```

**Step 2: Create Inventar page**

```jsx
// frontend/src/pages/Inventar.jsx
import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import useStore from '../store/useStore'
import PortfolioBar from '../components/PortfolioBar'
import { listInventory, getPortfolioSummary, markAsSold, deleteInventoryItem } from '../api/client'
import { formatEuro, formatPercent, formatDate } from '../utils/format'

export default function Inventar() {
  const {
    inventory, setInventory, inventoryLoading, setInventoryLoading,
    portfolioSummary, setPortfolioSummary,
  } = useStore()
  const [sellModal, setSellModal] = useState(null)
  const [sellForm, setSellForm] = useState({ sell_price: '', sell_date: '', sell_platform: 'eBay' })

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setInventoryLoading(true)
    try {
      const [inv, summary] = await Promise.all([
        listInventory('HOLDING'),
        getPortfolioSummary(),
      ])
      setInventory(inv.data)
      setPortfolioSummary(summary.data)
    } catch (err) {
      console.error('Failed to load inventory:', err)
    } finally {
      setInventoryLoading(false)
    }
  }

  const handleSell = async () => {
    if (!sellModal) return
    try {
      await markAsSold(sellModal, {
        sell_price: parseFloat(sellForm.sell_price),
        sell_date: sellForm.sell_date,
        sell_platform: sellForm.sell_platform,
      })
      setSellModal(null)
      loadData()
    } catch (err) {
      console.error('Failed to mark as sold:', err)
    }
  }

  const handleDelete = async (id) => {
    if (!confirm('Wirklich löschen?')) return
    try {
      await deleteInventoryItem(id)
      loadData()
    } catch (err) {
      console.error('Failed to delete:', err)
    }
  }

  return (
    <div>
      <PortfolioBar summary={portfolioSummary} />

      {inventoryLoading ? (
        <div className="text-center py-12 text-brick-muted font-mono text-sm">Lade Inventar...</div>
      ) : inventory.length === 0 ? (
        <div className="text-center py-12 text-brick-muted text-sm">
          Noch keine Sets im Inventar. Nutze den Deal Checker um Sets hinzuzufügen.
        </div>
      ) : (
        <div className="space-y-3">
          {inventory.map((item, i) => (
            <motion.div
              key={item.id}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.03 }}
              className={`bg-brick-surface border rounded-lg p-4 transition-colors
                ${item.sell_signal ? 'border-brick-go/50 shadow-[0_0_15px_rgba(34,197,94,0.1)]' : 'border-brick-border'}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-mono text-brick-yellow text-sm font-semibold">{item.set_number}</span>
                    {item.sell_signal && (
                      <span className="text-xs font-mono text-brick-go bg-brick-go/15 px-2 py-0.5 rounded animate-pulse">
                        SELL
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-brick-text truncate">{item.set_name}</p>
                  <p className="text-xs text-brick-muted mt-1">
                    Gekauft {formatDate(item.buy_date)} · {item.buy_platform} · {item.condition}
                  </p>
                  {item.sell_signal_reason && (
                    <p className="text-xs text-brick-go mt-1">{item.sell_signal_reason}</p>
                  )}
                </div>
                <div className="text-right shrink-0">
                  <p className="font-mono text-xs text-brick-muted">
                    {formatEuro(item.total_buy_cost)} → {formatEuro(item.current_market_price)}
                  </p>
                  <p className={`font-mono text-sm font-bold ${(item.unrealized_roi_percent || 0) >= 0 ? 'text-brick-go' : 'text-brick-nogo'}`}>
                    {formatEuro(item.unrealized_profit)} ({formatPercent(item.unrealized_roi_percent)})
                  </p>
                </div>
              </div>
              <div className="flex gap-2 mt-3">
                <button
                  onClick={() => {
                    setSellModal(item.id)
                    setSellForm({ sell_price: '', sell_date: new Date().toISOString().split('T')[0], sell_platform: 'eBay' })
                  }}
                  className="text-xs font-mono px-3 py-1 border border-brick-go/40 text-brick-go rounded hover:bg-brick-go/10 transition-colors"
                >
                  Verkauft
                </button>
                <button
                  onClick={() => handleDelete(item.id)}
                  className="text-xs font-mono px-3 py-1 border border-brick-border text-brick-muted rounded hover:border-brick-nogo hover:text-brick-nogo transition-colors"
                >
                  Löschen
                </button>
              </div>
            </motion.div>
          ))}
        </div>
      )}

      {/* Sell Modal */}
      <AnimatePresence>
        {sellModal && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
            onClick={() => setSellModal(null)}
          >
            <motion.div
              initial={{ scale: 0.9 }}
              animate={{ scale: 1 }}
              exit={{ scale: 0.9 }}
              onClick={(e) => e.stopPropagation()}
              className="bg-brick-surface border border-brick-border rounded-lg p-6 w-full max-w-sm"
            >
              <h3 className="font-mono font-bold text-brick-yellow mb-4">Als verkauft markieren</h3>
              <div className="space-y-3">
                <div>
                  <label className="block text-xs text-brick-muted mb-1 font-mono">Verkaufspreis (€)</label>
                  <input
                    type="number"
                    step="0.01"
                    value={sellForm.sell_price}
                    onChange={(e) => setSellForm({ ...sellForm, sell_price: e.target.value })}
                    className="w-full px-3 py-2 bg-brick-bg border border-brick-border rounded text-sm font-mono text-brick-text focus:border-brick-yellow outline-none"
                    autoFocus
                  />
                </div>
                <div>
                  <label className="block text-xs text-brick-muted mb-1 font-mono">Verkaufsdatum</label>
                  <input
                    type="date"
                    value={sellForm.sell_date}
                    onChange={(e) => setSellForm({ ...sellForm, sell_date: e.target.value })}
                    className="w-full px-3 py-2 bg-brick-bg border border-brick-border rounded text-sm font-mono text-brick-text focus:border-brick-yellow outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs text-brick-muted mb-1 font-mono">Plattform</label>
                  <select
                    value={sellForm.sell_platform}
                    onChange={(e) => setSellForm({ ...sellForm, sell_platform: e.target.value })}
                    className="w-full px-3 py-2 bg-brick-bg border border-brick-border rounded text-sm font-mono text-brick-text focus:border-brick-yellow outline-none"
                  >
                    <option>eBay</option>
                    <option>Kleinanzeigen</option>
                    <option>BrickLink</option>
                    <option>Direkt</option>
                  </select>
                </div>
              </div>
              <div className="flex gap-2 mt-4">
                <button
                  onClick={handleSell}
                  className="flex-1 py-2 bg-brick-go/20 border border-brick-go/40 text-brick-go font-mono font-semibold rounded hover:bg-brick-go/30"
                >
                  Bestätigen
                </button>
                <button
                  onClick={() => setSellModal(null)}
                  className="flex-1 py-2 border border-brick-border text-brick-muted font-mono rounded hover:border-brick-muted"
                >
                  Abbrechen
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
```

**Step 3: Commit**
```bash
git add frontend/src/components/PortfolioBar.jsx frontend/src/pages/Inventar.jsx
git commit -m "feat: add Inventar page with portfolio summary, sell signals, and sell modal"
```

---

### Task 12: History Page

**Files:**
- Create: `frontend/src/pages/History.jsx`

**Step 1: Implement History with charts**

```jsx
// frontend/src/pages/History.jsx
import { useEffect } from 'react'
import { motion } from 'framer-motion'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import useStore from '../store/useStore'
import { getInventoryHistory } from '../api/client'
import { formatEuro, formatPercent, formatDate } from '../utils/format'

export default function History() {
  const { history, setHistory, historyLoading, setHistoryLoading } = useStore()

  useEffect(() => {
    loadHistory()
  }, [])

  const loadHistory = async () => {
    setHistoryLoading(true)
    try {
      const { data } = await getInventoryHistory()
      setHistory(data)
    } catch (err) {
      console.error('Failed to load history:', err)
    } finally {
      setHistoryLoading(false)
    }
  }

  // Calculate monthly profit data for chart
  const monthlyProfit = history.reduce((acc, item) => {
    if (!item.sell_date) return acc
    const month = item.sell_date.slice(0, 7) // YYYY-MM
    const profit = (item.sell_price || 0) - item.total_buy_cost
    acc[month] = (acc[month] || 0) + profit
    return acc
  }, {})

  const chartData = Object.entries(monthlyProfit)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, profit]) => ({ month, profit: Math.round(profit) }))

  const totalRealized = history.reduce((sum, i) => sum + ((i.sell_price || 0) - i.total_buy_cost), 0)

  return (
    <div>
      {/* Summary */}
      <div className="bg-brick-surface border border-brick-border rounded-lg p-4 mb-4 flex items-center justify-between">
        <div>
          <p className="text-xs text-brick-muted font-mono">Abgeschlossene Deals</p>
          <p className="font-mono text-lg font-bold text-brick-yellow">{history.length}</p>
        </div>
        <div className="text-right">
          <p className="text-xs text-brick-muted font-mono">Realisierter Gewinn</p>
          <p className={`font-mono text-lg font-bold ${totalRealized >= 0 ? 'text-brick-go' : 'text-brick-nogo'}`}>
            {formatEuro(totalRealized)}
          </p>
        </div>
      </div>

      {/* Monthly Profit Chart */}
      {chartData.length > 0 && (
        <div className="bg-brick-surface border border-brick-border rounded-lg p-4 mb-4">
          <p className="text-xs text-brick-muted font-mono mb-3">Gewinn pro Monat</p>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData}>
              <XAxis dataKey="month" tick={{ fill: '#6b6b80', fontSize: 10, fontFamily: 'JetBrains Mono' }} />
              <YAxis tick={{ fill: '#6b6b80', fontSize: 10, fontFamily: 'JetBrains Mono' }} />
              <Tooltip
                contentStyle={{ background: '#141420', border: '1px solid #1e1e30', borderRadius: 8, fontFamily: 'JetBrains Mono', fontSize: 12 }}
                labelStyle={{ color: '#FFD700' }}
              />
              <Bar dataKey="profit" fill="#22c55e" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* History Table */}
      {historyLoading ? (
        <div className="text-center py-12 text-brick-muted font-mono text-sm">Lade History...</div>
      ) : history.length === 0 ? (
        <div className="text-center py-12 text-brick-muted text-sm">
          Noch keine abgeschlossenen Deals. Verkaufe Sets im Inventar.
        </div>
      ) : (
        <div className="space-y-2">
          {history.map((item, i) => {
            const profit = (item.sell_price || 0) - item.total_buy_cost
            const roi = item.total_buy_cost > 0 ? (profit / item.total_buy_cost) * 100 : 0
            return (
              <motion.div
                key={item.id}
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.03 }}
                className="bg-brick-surface border border-brick-border rounded-lg p-3 flex items-center justify-between"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-brick-yellow text-sm">{item.set_number}</span>
                    <span className="text-xs text-brick-muted">{item.set_name}</span>
                  </div>
                  <p className="text-xs text-brick-muted mt-1">
                    {formatDate(item.buy_date)} → {formatDate(item.sell_date)} · {item.sell_platform}
                  </p>
                </div>
                <div className="text-right shrink-0">
                  <p className="font-mono text-xs text-brick-muted">
                    {formatEuro(item.total_buy_cost)} → {formatEuro(item.sell_price)}
                  </p>
                  <p className={`font-mono text-sm font-bold ${profit >= 0 ? 'text-brick-go' : 'text-brick-nogo'}`}>
                    {formatEuro(profit)} ({formatPercent(roi)})
                  </p>
                </div>
              </motion.div>
            )
          })}
        </div>
      )}
    </div>
  )
}
```

**Step 2: Commit**
```bash
git add frontend/src/pages/History.jsx
git commit -m "feat: add History page with realized profits and monthly chart"
```

---

## Phase C: Infrastructure

### Task 13: Docker & Nginx Integration

**Files:**
- Create: `infra/Dockerfile.frontend`
- Modify: `docker-compose.yml`
- Modify: `infra/nginx.conf`

**Step 1: Create frontend Dockerfile**

```dockerfile
# infra/Dockerfile.frontend
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
```

**Step 2: Add frontend service to docker-compose.yml**

Add after the `nginx` service:
```yaml
  # ── Frontend Dashboard ─────────────────────────────────
  frontend:
    build:
      context: .
      dockerfile: infra/Dockerfile.frontend
    container_name: lego-frontend
    restart: unless-stopped
```

Update the nginx service to depend on frontend:
```yaml
  nginx:
    ...
    depends_on:
      - api
      - frontend
    volumes:
      - ./infra/nginx.conf:/etc/nginx/conf.d/default.conf:ro
```

**Step 3: Update nginx.conf**

Replace `infra/nginx.conf` to serve frontend and proxy API:

```nginx
server {
    listen 80;
    server_name localhost lego-arbitrage.de;

    # Frontend static files
    location / {
        proxy_pass http://frontend:80;
        proxy_set_header Host $host;
    }

    # API Backend
    location /api/ {
        proxy_pass http://api:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    # Health check
    location /health {
        proxy_pass http://api:8000/health;
    }

    # API Docs (Swagger)
    location /docs {
        proxy_pass http://api:8000/docs;
        proxy_set_header Host $host;
    }

    location /openapi.json {
        proxy_pass http://api:8000/openapi.json;
    }

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=30r/m;
    location /api/analysis/ {
        limit_req zone=api burst=5 nodelay;
        proxy_pass http://api:8000/api/analysis/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

**Step 4: Commit**
```bash
git add infra/Dockerfile.frontend docker-compose.yml infra/nginx.conf
git commit -m "feat: add frontend Docker build and update Nginx to serve dashboard"
```

---

### Task 14: Final Integration & .gitignore Update

**Files:**
- Modify: `.gitignore`

**Step 1: Add frontend ignores to .gitignore**

Append to `.gitignore`:
```
# Frontend
frontend/node_modules/
frontend/dist/
frontend/.vite/
```

**Step 2: Final commit**
```bash
git add .gitignore
git commit -m "chore: add frontend build artifacts to gitignore"
```

---

## Execution Order Summary

| # | Task | Phase | Est. Size |
|---|------|-------|-----------|
| 1 | InventoryItem Model | Backend | Small |
| 2 | Inventory API Router | Backend | Medium |
| 3 | BrickMerge History Scraper | Backend | Small |
| 4 | Inventory Valuation Celery Task | Backend | Medium |
| 5 | Vite + React + Tailwind Setup | Frontend | Small |
| 6 | API Client & Zustand Store | Frontend | Small |
| 7 | Layout Shell & Tab Navigation | Frontend | Small |
| 8 | Shared Components | Frontend | Small |
| 9 | Live Feed Page | Frontend | Medium |
| 10 | Deal Checker Page | Frontend | Medium |
| 11 | Inventar Page | Frontend | Medium |
| 12 | History Page | Frontend | Medium |
| 13 | Docker & Nginx Integration | Infra | Small |
| 14 | .gitignore Update | Infra | Tiny |

**Dependencies:** Tasks 1-4 (backend) and 5-8 (frontend scaffold) can run in parallel. Tasks 9-12 depend on 5-8. Task 13 depends on 5. Task 14 is last.
