# LEGO Arbitrage Dashboard — Design Document

**Date**: 2026-03-23
**Author**: Sebastian Willkommen + Claude
**Status**: Approved

## Purpose

Personal power-tool dashboard for autonomous LEGO investment management. Single user (Sebastian), used on desktop for deep analysis and mobile for quick deal checks on the go (e.g. standing in a store).

## Tech Stack

- **Frontend**: React 19 + Vite
- **Styling**: Tailwind CSS 4
- **Charts**: Recharts (lightweight, React-native)
- **State**: Zustand (minimal boilerplate)
- **HTTP**: TanStack Query (caching, auto-refresh)
- **Routing**: React Router 7
- **Container**: Nginx static serving via existing Docker Compose

## Aesthetic Direction

**"Trading Terminal meets LEGO"** — dark theme, data-dense, bold verdict badges (GO/NO-GO), accent colors from LEGO palette (red, yellow, blue). Monospace numbers. No fluff, no onboarding. Every pixel serves a purpose.

- **Typography**: JetBrains Mono for numbers/data, DM Sans for UI text
- **Colors**: Dark slate background (#0f172a), LEGO Red (#e3000b) for alerts/NO-GO, Green (#22c55e) for GO, Amber (#f59e0b) for CHECK, Electric Blue (#3b82f6) for accents
- **Motion**: Subtle — pulse on sell-signals, fade-in on new deals, smooth tab transitions
- **Mobile**: Bottom tab bar, swipeable cards, pull-to-refresh

## Page Structure — 4 Tabs

### Tab 1: Live Feed (Home)

- **Top bar**: System status — scraper health indicators (green/red dots), last scan timestamp, next scan countdown
- **Deal cards**: Sorted by opportunity score (descending)
  - Each card: Set image, number, name, offer price → market price, ROI%, risk badge (1-10), verdict badge (GO_STAR/GO/CHECK/NO_GO)
  - Tap/click → expands to full analysis detail
- **Filter bar**: By verdict, theme, min ROI slider, max risk slider
- **Auto-refresh**: Every 30s via TanStack Query, pull-to-refresh on mobile

### Tab 2: Deal Checker

- **Input form**: Set number (large input, auto-focus) + offer price
  - Optional expandable: condition toggle (NEW_SEALED/USED), shipping cost, box damage switch
- **Result card** (after submit):
  - Verdict banner (full-width, color-coded)
  - Price sources breakdown — table showing each scraper's price + BrickMerge historical data
  - ROI calculation: purchase cost → selling costs → net profit, broken down
  - Risk score: radar/bar chart with individual factors (age, EOL, liquidity, condition, data quality)
  - Suggestions: actionable tips as pill badges
  - **"Gekauft" button** → adds to inventory with pre-filled data (price, date, set info)

### Tab 3: Inventar (Portfolio)

- **Portfolio summary bar** (sticky top):
  ```
  📦 12 Sets  |  Investiert: 1.847€  |  Wert: 2.934€  |  +1.087€ (+58,8%)
  ```
  - Invested: sum of all purchase prices (incl. shipping)
  - Current value: sum of market consensus prices (updated every 6h via Celery)
  - Unrealized P/L: absolute + percentage

- **Inventory list**: Cards or compact table (toggle)
  - Per item: Set image, number, name, buy price, buy date, current market price, delta (€ + %), holding period
  - **Sell-Signal badge**: Pulsing green when conditions met:
    - ROI target for category reached or exceeded
    - Market price at local peak (trend flattening/declining)
    - Optimal holding period for category reached
  - Quick actions: "Mark as sold" (enter sell price + date → moves to history), "Re-analyze", "Remove"

- **Data sources for valuation**:
  - Primary: 7-scraper market consensus (existing engine)
  - Secondary: BrickMerge price history integration for trend analysis and peak detection

### Tab 4: History

- **Completed deals**: Buy → Sell with realized profit per deal
- **Performance charts**:
  - Profit per month (bar chart)
  - ROI distribution (histogram)
  - Best themes/categories (horizontal bar)
  - Cumulative profit over time (line chart)
- **Stats summary**: Total realized profit, average ROI, best deal, worst deal, win rate

## Backend Extensions Required

### New Model: `InventoryItem`

```python
class InventoryItem(Base):
    __tablename__ = "inventory_items"

    set_number: str           # FK to LegoSet
    set_name: str
    buy_price: float          # Purchase price EUR
    buy_shipping: float       # Shipping cost EUR
    buy_date: date
    buy_platform: str         # Where purchased
    buy_url: str | None       # Link to listing
    condition: str            # NEW_SEALED, USED, etc.
    notes: str | None

    # Current valuation (auto-updated)
    current_market_price: float | None
    market_price_updated_at: datetime | None
    unrealized_profit: float | None
    unrealized_roi_percent: float | None

    # Sell signal
    sell_signal_active: bool = False
    sell_signal_reason: str | None

    # Status
    status: str = "HOLDING"   # HOLDING, SOLD
    sell_price: float | None
    sell_date: date | None
    sell_platform: str | None
    realized_profit: float | None
    realized_roi_percent: float | None
```

### New API Router: `/api/inventory`

- `GET /api/inventory` — list all items (with filters: status, sort)
- `POST /api/inventory` — add item (from Deal Checker "Gekauft" or manual)
- `PATCH /api/inventory/{id}` — update item
- `POST /api/inventory/{id}/sell` — mark as sold (enter sell price/date)
- `DELETE /api/inventory/{id}` — remove
- `GET /api/inventory/summary` — portfolio totals (invested, value, P/L)
- `GET /api/inventory/history` — sold items with performance stats

### New Celery Task: `update_inventory_valuations`

- Runs every 6h (alongside existing scraper cycle)
- For each HOLDING item: fetch fresh market consensus, update current_market_price
- Compute sell signals based on:
  - ROI target reached for set category
  - Price trend analysis (using BrickMerge history)
  - Holding period vs. optimal for category
- Fire Telegram notification when sell signal activates

### BrickMerge Integration Enhancement

- Use BrickMerge's price history data for:
  - Trend analysis (is price rising, peaking, or falling?)
  - Peak detection for sell-signal timing
  - Historical context in Deal Checker results

## Docker Compose Addition

New service `frontend`:
```yaml
frontend:
  build:
    context: ./frontend
    dockerfile: ../infra/Dockerfile.frontend
  container_name: lego-frontend
  ports:
    - "3000:80"
  depends_on:
    - api
```

Update Nginx to proxy `/` → frontend, `/api` → backend.

## Mobile Responsiveness

- Breakpoints: mobile-first (`< 768px` compact cards + bottom tabs), desktop (`>= 768px` full data density + top tabs)
- Touch targets: minimum 44px
- Cards: stack vertically on mobile, grid on desktop
- Portfolio summary: wraps to 2 rows on narrow screens
