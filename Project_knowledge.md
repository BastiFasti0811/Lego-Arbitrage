# LEGO Arbitrage — Projekt-Wissensdatenbank

## Projekt-Status
- **Stand**: Phase 1 — Grundgerüst implementiert (März 2026)
- **Python-Dateien**: 40 Files
- **Implementiert**: Models, Scrapers (7x), Engine (ROI/Risk/Decision), API, Docker, Telegram, Celery
- **Masterplan**: LEGO-Arbitrage-Masterplan.html
- **Owner**: Sebastian (sebastian.willkommen@conuti.de)

## Architektur-Entscheidungen
- **Autonomie-Level**: Voll-autonomer AI-Agent mit Human-in-the-Loop für Käufe
- **Hosting**: Hetzner Cloud (CX31, 4 vCPU, 8GB RAM, ~15€/Monat)
- **Backend**: Python 3.12, FastAPI, Celery, PostgreSQL + TimescaleDB, Redis
- **AI**: Claude API (Anthropic) + LangGraph + XGBoost für Preis-Prediction
- **Frontend**: Next.js 14, TypeScript, Tailwind CSS
- **Scraping**: Playwright (JS-heavy), httpx + BeautifulSoup (statische Seiten)

## Datenquellen (7 Scraper implementiert)
1. BrickEconomy — Globale Marktpreise, Growth%, EOL-Status
2. BrickMerge — Deutsche Retailpreise, Shop-Angebote
3. eBay.de Sold Items — Tatsächliche Verkaufspreise (Median, Outlier-Filter)
4. Kleinanzeigen.de — Privatangebote, oft günstiger
5. Idealo.de — Preisvergleich, Verfügbarkeit
6. LEGO.com — EOL-Status Check
7. Amazon.de — Marketplace-Preise, Third-Party Seller, Pricing Errors

## API-Endpunkte
- POST /api/analysis/analyze — Einzelanalyse mit vollständiger ROI/Risk-Bewertung
- POST /api/scout/scan — Proaktive Deal-Suche über mehrere Sets
- GET /api/scout/quick/{set_number} — Schnell-Scan eines Sets
- GET/POST /api/sets/ — Set-Datenbank CRUD
- GET/POST/DELETE /api/watchlist/ — Watchlist Management
- POST /api/feedback/ — Deal-Ergebnisse loggen (Self-Improvement Loop)
- GET /api/feedback/performance — System-Performance-Metriken

## Automatisierung (Celery Beat)
- Alle 6h: Watchlist-Sets scrapen (alle Plattformen)
- Alle 30min: Neue Angebote analysieren + Telegram-Alert bei GO-Deals
- Täglich 20:00: Zusammenfassung per Telegram
- Wöchentlich Sonntag 03:00: ML-Model Retraining (Phase 3)

## Geschätzte Kosten
- Infrastruktur: ~21€/Monat (Hetzner + Storage + Domain)
- Proxies: ~15€/Monat (Bright Data Residential)
- AI: ~30-80€/Monat (Claude API, variabel)
- **Gesamt: ~60-110€/Monat**

## Sicherheitshinweise
- Scraping-Legalität: AGB der Plattformen beachten, robots.txt respektieren
- Keine automatischen Käufe ohne User-Bestätigung
- API-Keys nur in Vault/Docker Secrets, nie im Code
- Tägliche DB-Backups, 30-Tage Retention

## Offene Fragen
- [ ] Bright Data vs. alternative Proxy-Anbieter evaluieren
- [ ] Genauere Claude API Kosten-Schätzung nach ersten Tests
- [ ] Kleinanzeigen Captcha-Handling Strategie finalisieren
- [ ] DSGVO-Konformität prüfen (Preisdaten = keine personenbezogenen Daten?)
