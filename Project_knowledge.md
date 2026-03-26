# LEGO Arbitrage - Projekt-Wissensdatenbank

## Projekt-Status
- Stand: Arbeitsstand Maerz 2026
- Implementiert: Backend API, Scraper, ROI-/Risk-Engine, Telegram, Celery, Dashboard
- Masterplan: LEGO-Arbitrage-Masterplan.html
- Owner: Sebastian (sebastian.willkommen@conuti.de)

## Architektur
- Hosting: Hetzner Cloud auf `spm-prod-01` in `NBG1`
- Deployment: GitHub als Source of Truth plus separater Produktionsserver
- Backend: Python 3.12, FastAPI, SQLAlchemy, Celery, PostgreSQL, Redis
- Frontend: Vite, React 19, React Router, TanStack Query
- Reverse Proxy: Caddy in Produktion, lokales `nginx` nur fuer den Dev-Stack
- Scraping: Playwright, httpx und BeautifulSoup

## Produktlogik
- Einzelanalysen laufen ueber `/api/analysis/analyze`.
- Scout-Scans und der Live-Feed nutzen `/api/scout/*`.
- Watchlist, Inventory, History und Settings sind ueber das React-Dashboard erreichbar.
- Telegram-Settings werden zur Laufzeit aus der Datenbank gelesen.
- Der Live-Feed verwendet gecachte Angebotsdaten statt dauernd neue Live-Scrapes.

## Datenquellen
1. BrickEconomy
2. BrickMerge
3. eBay.de Sold Items
4. Kleinanzeigen.de
5. Idealo.de
6. LEGO.com
7. Amazon.de

## Automatisierung
- Wiederkehrende Jobs laufen ueber Celery Worker und Celery Beat.
- Watchlist-Sets werden regelmaessig gescraped und neu analysiert.
- Telegram-Alerts und taegliche Zusammenfassungen sind vorbereitet.

## Infrastruktur-Hinweise
- Produktionsdaten liegen auf `/mnt/HC_Volume_105179687`.
- Der produktionsnahe Stack ist in `docker-compose.prod.yml` beschrieben.
- Die Caddy-Konfiguration liegt in `infra/Caddyfile`.
- Das Deploy-Runbook liegt in `docs/deploy.md`.

## Sicherheitshinweise
- Keine bekannten Default-Credentials fuer Dashboard oder Session-Secret verwenden.
- Serverzugriff nur per SSH-Key und bevorzugt ueber den `deploy`-User.
- Secrets bleiben in `.env`-Dateien ausserhalb von Git.

## Offene Themen
- Testsuite ausbauen, aktuell gibt es kaum automatisierte Backend-Tests.
- Proxy-/Captcha-Strategie fuer schwierigere Quellen schaerfen.
- Backup-Strategie fuer das Hetzner-Volume dokumentieren und testen.
- Naechstes Produkt-Feature: Foto-Unterstuetzung fuer das Inventar, damit Eintraege mit mehreren Bildern dokumentiert werden koennen.
- Naechstes Produkt-Feature: Deal-Checker um EAN-/Barcode-Scan erweitern, weil das voraussichtlich der einfachste erste Bild-/Scan-Pfad fuer eine schnelle Set-Erkennung ist.
- Spaeter moeglich: echte Bildanalyse/OCR fuer Kartonfotos, falls EAN nicht sichtbar oder nicht ausreichend ist.
