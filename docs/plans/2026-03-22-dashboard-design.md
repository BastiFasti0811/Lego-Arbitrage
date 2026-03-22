# LEGO Arbitrage Dashboard — Design Document

**Date:** 2026-03-22
**Author:** Sebastian Willkommen + Claude
**Status:** Approved

---

## 1. Purpose

Personal power-tool dashboard for the LEGO Arbitrage system. Combines deal detection, investment analysis, portfolio tracking, and sell-timing into a single interface. Used exclusively by Sebastian on desktop and mobile.

## 2. Aesthetic Direction: "Brick Terminal"

A dark, data-driven trading terminal with LEGO DNA.

- **Theme:** Dark default (`#0a0a0f` background)
- **Accent colors:** LEGO-Yellow `#FFD700` (primary accent), Green `#22c55e` (GO), Red `#ef4444` (NO-GO), Orange `#f59e0b` (CHECK), Cyan `#06b6d4` (GO_STAR)
- **Typography:** `JetBrains Mono` for numbers/data, `DM Sans` for UI text
- **Cards:** Compact deal cards with color-coded verdict badges, Bloomberg-terminal density with LEGO-brick aesthetic
- **Mobile:** Cards stack vertically, Deal Checker becomes quick-input with large verdict output

## 3. Tech Stack

| Layer | Choice | Reason |
|-------|--------|--------|
| Framework | React 19 + Vite | Fast DX, static build for Nginx |
| Styling | Tailwind CSS 4 | Utility-first, responsive out-of-box |
| Charts | Recharts | Lightweight, React-native |
| HTTP | Axios / fetch | API calls to FastAPI backend |
| State | Zustand | Minimal, no boilerplate |
| Animations | Framer Motion | Micro-interactions, verdict reveals |
| Build output | Static files | Served by existing Nginx container |

## 4. Page Structure — 4 Tabs

### Tab 1: Live Feed (Startseite)

**Purpose:** Automatic deal stream from the Scout system.

- **System Status Bar:** Scraper health, last scan timestamp, next scan countdown
- **Deal Cards** sorted by opportunity score (highest first):
  - Set number + name
  - Price arrow Market price
  - ROI %, Risk badge (1-10)
  - Verdict badge: GO_STAR / GO / CHECK / NO_GO
  - Platform + link to offer
- **Filter bar:** Verdict, Theme, min ROI, max Risk
- **Pull-to-refresh** on mobile
- **Auto-refresh:** Polls `/api/scout/scan` on configurable interval

### Tab 2: Deal Checker

**Purpose:** Manual deal analysis — see a set somewhere, check it instantly.

- **Input form:**
  - Set number (primary, large input)
  - Offer price
  - Optional: Condition toggle (NEW_SEALED / USED), shipping cost, box damage toggle
- **Result:** Full analysis card with:
  - Large verdict badge at top
  - Source prices breakdown (which scraper returned what)
  - ROI calculation breakdown (purchase cost, selling costs, net profit)
  - Risk score with individual factors
  - Suggestions as actionable tips
- **"Gekauft" Button:** After analysis, one click adds set to inventory with buy price + date

### Tab 3: Inventar (Portfolio)

**Purpose:** Track owned sets, see real-time portfolio value, get sell signals.

#### Portfolio Summary Bar (top)
```
12 Sets | Investiert: 1.847 EUR | Wert: 2.934 EUR | +1.087 EUR (+58,8%)
```

- **Investiert:** Sum of all purchase prices (incl. shipping)
- **Aktueller Wert:** Sum of current market prices (consensus from scrapers + BrickMerge history)
- **Unrealisierter Gewinn:** Delta in EUR and %

#### Inventory Table/Cards
Per item:
- Set number, name, image
- Buy price, buy date, platform
- Current market price (from consensus + BrickMerge price history)
- Unrealized profit/loss (EUR + %)
- Holding duration
- **Sell-Signal Badge** (pulsing green): triggers when:
  - ROI target for category reached or exceeded
  - Market price at or near peak (trend flattening/declining)
  - Optimal holding duration for category reached
  - BrickMerge price history confirms uptrend plateau

#### Actions
- Mark as "Verkauft" with sell price + date -> moves to History
- Re-analyze (refresh market data)
- Add notes

### Tab 4: History

**Purpose:** Completed deals, performance analytics, learning.

- **Completed deals table:** Buy -> Sell with real profit
- **Performance charts:**
  - Profit per month (bar chart)
  - Best themes (pie/bar)
  - ROI distribution (histogram)
  - Portfolio value over time (line chart)
- **Insights:** Which category/theme performs best for you

## 5. Data Sources & BrickMerge Integration

BrickMerge provides excellent price history data. The system will:

1. **Scrape BrickMerge price history** for each tracked/inventory set
2. **Use BrickMerge trends** as additional signal for:
   - Market consensus calculation (historical context)
   - Sell-signal timing (peak detection from price history curve)
   - Portfolio valuation (cross-reference with other scrapers)
3. **Store price history** in PostgreSQL for offline analysis and charts

## 6. New Backend Requirements

### New Model: `InventoryItem`
```
- id: int (PK)
- set_number: str (FK -> lego_sets)
- buy_price: float
- buy_shipping: float
- buy_date: date
- buy_platform: str
- condition: str
- notes: text (nullable)
- status: HOLDING | SOLD
- sell_price: float (nullable)
- sell_date: date (nullable)
- sell_platform: str (nullable)
- current_market_price: float (nullable, auto-updated)
- sell_signal: bool (default false)
- sell_signal_reason: str (nullable)
- created_at, updated_at: timestamps
```

### New API Router: `/api/inventory`
- `GET /` — list all inventory items with current valuations
- `POST /` — add item (from Deal Checker or manual)
- `PATCH /{id}` — update (notes, mark as sold)
- `DELETE /{id}` — remove
- `GET /summary` — portfolio totals (invested, current value, profit)
- `GET /history` — sold items with realized profit

### New Celery Task: `update_inventory_valuations`
- Runs every 6h (alongside existing scrape cycle)
- For each HOLDING item: refresh market price from consensus + BrickMerge
- Calculate sell signals based on ROI targets, trend analysis, holding duration
- Store price snapshots for history charts

## 7. Docker Integration

Add `frontend` service to `docker-compose.yml`:
```yaml
frontend:
  build:
    context: ./frontend
    dockerfile: ../infra/Dockerfile.frontend
  container_name: lego-frontend
```

Nginx updated to:
- Serve frontend static files on `/`
- Proxy `/api/*` to FastAPI backend (already configured)

## 8. Mobile Responsiveness

- **Breakpoint:** 768px (md)
- **Desktop:** Multi-column card grid, full tables, side-by-side charts
- **Mobile:** Single-column card stack, swipeable tabs, compact summary bar, large touch targets for "Gekauft"/"Verkauft" buttons
- **PWA-ready:** Add manifest.json for home screen install on phone

## 9. Non-Goals (YAGNI)

- No user auth (single user, accessed via VPN/direct IP)
- No settings page (config via .env)
- No multi-language (German only)
- No email notifications (Telegram already exists)
- No public-facing landing page
