import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAppStore } from "../stores/appStore";
import VerdictBadge from "../components/VerdictBadge";

const verdictBg = {
  GO_STAR: "from-go-star/20 to-go-star/5 border-go-star/30",
  GO: "from-go/20 to-go/5 border-go/30",
  CHECK: "from-check/20 to-check/5 border-check/30",
  NO_GO: "from-no-go/20 to-no-go/5 border-no-go/30",
};

const SHIPPING_PRESETS = [
  { label: "Kein Versand", value: 0 },
  { label: "DHL Päckchen S", value: 3.99 },
  { label: "DHL Paket", value: 5.49 },
  { label: "Hermes S", value: 4.50 },
  { label: "Hermes M", value: 5.50 },
  { label: "DHL Paket L", value: 7.49 },
  { label: "Abholung", value: 0 },
];

const isUrl = (str) => /^https?:\/\//.test(str.trim());

export default function DealChecker() {
  const queryClient = useQueryClient();
  const { setLastAnalysis } = useAppStore();

  // Smart input — accepts URL or set number
  const [smartInput, setSmartInput] = useState("");
  const [setNumber, setSetNumber] = useState("");
  const [offerPrice, setOfferPrice] = useState("");
  const [showOptions, setShowOptions] = useState(false);
  const [condition, setCondition] = useState("NEW_SEALED");
  const [shipping, setShipping] = useState("");
  const [boxDamage, setBoxDamage] = useState(false);
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourcePlatform, setSourcePlatform] = useState("");
  const [showHistory, setShowHistory] = useState(false);

  // Seller check
  const [sellerUrl, setSellerUrl] = useState("");
  const [showSellerCheck, setShowSellerCheck] = useState(false);

  // Gekauft modal
  const [showBuyModal, setShowBuyModal] = useState(false);
  const [buyPlatform, setBuyPlatform] = useState("");
  const [buyDate, setBuyDate] = useState(new Date().toISOString().split("T")[0]);

  // URL parse status message
  const [parseMessage, setParseMessage] = useState("");

  // URL parsing mutation
  const parseUrl = useMutation({
    mutationFn: (url) => api.parseUrl(url),
    onSuccess: (data) => {
      if (data.set_number) setSetNumber(data.set_number);
      if (data.price) {
        setOfferPrice(String(data.price));
        setParseMessage(`✓ Set ${data.set_number} erkannt, Preis ${data.price}€`);
      } else if (data.set_number) {
        setParseMessage(`✓ Set ${data.set_number} erkannt — bitte Preis manuell eingeben`);
      } else {
        setParseMessage("Set-Nummer nicht erkannt — bitte manuell eingeben");
      }
      if (data.condition) setCondition(data.condition);
      if (data.url) setSourceUrl(data.url);
      if (data.platform) setSourcePlatform(data.platform);
      setSmartInput("");
    },
    onError: () => {
      setParseMessage("URL konnte nicht geladen werden — bitte manuell eingeben");
    },
  });

  // Analysis mutation
  const analyze = useMutation({
    mutationFn: (data) => api.analyze(data),
    onSuccess: (data) => {
      setLastAnalysis(data);
      queryClient.invalidateQueries({ queryKey: ["analysis-history"] });
    },
  });

  // Seller check mutation
  const sellerCheck = useMutation({
    mutationFn: (url) => api.sellerCheck(url),
  });

  // Analysis history
  const { data: history = [] } = useQuery({
    queryKey: ["analysis-history"],
    queryFn: () => api.analysisHistory(),
    staleTime: 10000,
  });

  // Add to inventory mutation
  const addToInventory = useMutation({
    mutationFn: (data) => api.addInventory(data),
    onSuccess: () => {
      setShowBuyModal(false);
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
    },
  });

  const handleSmartInput = (value) => {
    setSmartInput(value);
    // Auto-detect URL on paste
    if (isUrl(value)) {
      parseUrl.mutate(value);
    } else {
      // Treat as set number
      setSetNumber(value.replace(/\D/g, ""));
    }
  };

  const handleAnalyze = (e) => {
    e.preventDefault();
    if (!setNumber || !offerPrice) return;
    analyze.mutate({
      set_number: setNumber.trim(),
      offer_price: parseFloat(offerPrice),
      condition,
      box_damage: boxDamage,
      purchase_shipping: shipping ? parseFloat(shipping) : null,
      source_url: sourceUrl || null,
    });
  };

  const handleBuy = () => {
    const result = analyze.data;
    if (!result) return;
    addToInventory.mutate({
      set_number: result.set_number,
      set_name: result.set_name,
      theme: result.theme,
      buy_price: result.offer_price,
      buy_shipping: shipping ? parseFloat(shipping) : 0,
      buy_date: buyDate,
      buy_platform: buyPlatform || sourcePlatform || null,
      condition,
    });
  };

  const loadFromHistory = (item) => {
    setSetNumber(item.set_number);
    setOfferPrice(String(item.offer_price));
    setShowHistory(false);
  };

  const result = analyze.data;
  const bg = result ? verdictBg[result.recommendation] || verdictBg.NO_GO : "";

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Deal Checker</h1>
        {history.length > 0 && (
          <button
            onClick={() => setShowHistory(!showHistory)}
            className="text-text-muted text-sm hover:text-lego-yellow transition-colors flex items-center gap-1"
          >
            <span className="font-[family-name:var(--font-mono)]">{history.length}</span> History
            <span>{showHistory ? "▴" : "▾"}</span>
          </button>
        )}
      </div>

      {/* History Panel */}
      {showHistory && history.length > 0 && (
        <div className="bg-bg-card border border-border rounded-xl mb-6 max-h-64 overflow-y-auto">
          {history.map((item, i) => (
            <button
              key={i}
              onClick={() => loadFromHistory(item)}
              className="w-full flex items-center justify-between px-4 py-3 hover:bg-bg-hover transition-colors border-b border-border/50 last:border-0 text-left"
            >
              <div className="flex items-center gap-3">
                <VerdictBadge verdict={item.recommendation} size="sm" />
                <div>
                  <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm">{item.set_number}</span>
                  <span className="text-text-muted text-sm ml-2">{item.set_name}</span>
                </div>
              </div>
              <div className="text-right">
                <div className={`font-[family-name:var(--font-mono)] text-sm font-bold ${item.roi_percent >= 20 ? "text-go-star" : item.roi_percent >= 0 ? "text-check" : "text-no-go"}`}>
                  {item.roi_percent.toFixed(1)}%
                </div>
                <div className="text-text-muted text-xs">{item.offer_price}€</div>
              </div>
            </button>
          ))}
        </div>
      )}

      {/* Smart Input Form */}
      <form onSubmit={handleAnalyze} className="bg-bg-card border border-border rounded-xl p-6 mb-6">
        {/* URL/Link Input */}
        <div className="mb-4">
          <label className="block text-text-muted text-xs mb-1">
            Link einfügen (Kleinanzeigen, eBay, Amazon) oder direkt Set-Nummer eingeben
          </label>
          <div className="relative">
            <input
              type="text"
              value={smartInput}
              onChange={(e) => handleSmartInput(e.target.value)}
              onPaste={(e) => {
                const pasted = e.clipboardData.getData("text");
                if (isUrl(pasted)) {
                  e.preventDefault();
                  handleSmartInput(pasted);
                }
              }}
              placeholder="https://www.kleinanzeigen.de/s-anzeige/... oder 75192"
              autoFocus
              className="w-full bg-bg-primary border border-border rounded-lg px-4 py-3 text-text-primary text-sm placeholder:text-text-muted focus:border-lego-yellow focus:outline-none transition-colors"
            />
            {parseUrl.isPending && (
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-lego-yellow text-xs animate-pulse">
                Parsing...
              </span>
            )}
            {sourceUrl && (
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-go text-xs">
                ✓ {sourcePlatform}
              </span>
            )}
          </div>
        </div>

        <div className="flex gap-4 mb-4">
          <div className="flex-1">
            <label className="block text-text-muted text-xs mb-1">Set-Nummer</label>
            <input
              type="text"
              value={setNumber}
              onChange={(e) => setSetNumber(e.target.value)}
              placeholder="z.B. 75192"
              className="w-full bg-bg-primary border border-border rounded-lg px-4 py-3 text-text-primary font-[family-name:var(--font-mono)] text-lg placeholder:text-text-muted focus:border-lego-yellow focus:outline-none transition-colors"
            />
          </div>
          <div className="w-36">
            <label className="block text-text-muted text-xs mb-1">Angebotspreis</label>
            <div className="relative">
              <input
                type="number"
                step="0.01"
                value={offerPrice}
                onChange={(e) => setOfferPrice(e.target.value)}
                placeholder="0.00"
                className="w-full bg-bg-primary border border-border rounded-lg px-4 py-3 text-text-primary font-[family-name:var(--font-mono)] text-lg placeholder:text-text-muted focus:border-lego-yellow focus:outline-none transition-colors pr-8"
              />
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted">€</span>
            </div>
          </div>
        </div>

        {/* Expandable Options */}
        <button
          type="button"
          onClick={() => setShowOptions(!showOptions)}
          className="text-text-muted text-xs hover:text-text-secondary transition-colors mb-3"
        >
          {showOptions ? "▾" : "▸"} Optionen
        </button>

        {showOptions && (
          <div className="space-y-3 mb-4 p-3 bg-bg-primary rounded-lg">
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-text-muted text-xs mb-1">Zustand</label>
                <select
                  value={condition}
                  onChange={(e) => setCondition(e.target.value)}
                  className="w-full bg-bg-card border border-border rounded-lg px-2 py-1.5 text-text-primary text-sm"
                >
                  <option value="NEW_SEALED">Neu & Versiegelt</option>
                  <option value="NEW_OPEN">Neu & Geöffnet</option>
                  <option value="USED_COMPLETE">Gebraucht (komplett)</option>
                  <option value="USED_INCOMPLETE">Gebraucht (unvollständig)</option>
                </select>
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Versand</label>
                <select
                  value={shipping}
                  onChange={(e) => setShipping(e.target.value)}
                  className="w-full bg-bg-card border border-border rounded-lg px-2 py-1.5 text-text-primary text-sm"
                >
                  <option value="">Auswählen...</option>
                  {SHIPPING_PRESETS.map((p) => (
                    <option key={p.label} value={p.value}>
                      {p.label} ({p.value.toFixed(2)}€)
                    </option>
                  ))}
                  <option value="custom">Eigener Betrag</option>
                </select>
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={boxDamage}
                    onChange={(e) => setBoxDamage(e.target.checked)}
                    className="accent-lego-yellow"
                  />
                  <span className="text-text-secondary text-sm">Box beschädigt</span>
                </label>
              </div>
            </div>
            {shipping === "custom" && (
              <div className="w-1/3">
                <label className="block text-text-muted text-xs mb-1">Versandkosten (€)</label>
                <input
                  type="number"
                  step="0.01"
                  onChange={(e) => setShipping(e.target.value)}
                  placeholder="0.00"
                  className="w-full bg-bg-card border border-border rounded-lg px-2 py-1.5 text-text-primary text-sm font-[family-name:var(--font-mono)]"
                />
              </div>
            )}
          </div>
        )}

        <button
          type="submit"
          disabled={analyze.isPending || !setNumber || !offerPrice}
          className="w-full bg-lego-yellow text-black font-bold py-3 rounded-lg hover:bg-lego-yellow/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {analyze.isPending ? (
            <span className="flex items-center justify-center gap-2">
              <span className="inline-block w-4 h-4 border-2 border-black/30 border-t-black rounded-full animate-spin" />
              Analysiere... (kann bis zu 30s dauern)
            </span>
          ) : "Analysieren"}
        </button>

        {parseMessage && (
          <p className={`text-sm mt-3 ${parseMessage.startsWith("✓") ? "text-go" : "text-check"}`}>{parseMessage}</p>
        )}
        {analyze.isError && (
          <p className="text-no-go text-sm mt-3">Fehler: {analyze.error.message}</p>
        )}
      </form>

      {/* Analysis Result */}
      {result && (
        <div className={`border rounded-xl overflow-hidden bg-gradient-to-b ${bg}`}>
          {/* Verdict Banner */}
          <div className="p-6 text-center">
            <VerdictBadge verdict={result.recommendation} size="lg" />
            <p className="text-text-primary mt-3 font-medium">{result.reason}</p>
            <p className="text-text-muted text-sm mt-1">
              {result.set_name} ({result.theme}, {result.release_year})
            </p>
          </div>

          {/* Key Metrics */}
          <div className="grid grid-cols-3 gap-px bg-border">
            {[
              { label: "ROI", value: `${result.roi_percent.toFixed(1)}%`, color: result.roi_percent >= 20 ? "text-go-star" : result.roi_percent >= 0 ? "text-check" : "text-no-go" },
              { label: "Net Profit", value: `${result.net_profit.toFixed(0)}€`, color: result.net_profit > 0 ? "text-go" : "text-no-go" },
              { label: "Risk", value: `${result.risk_score}/10`, color: result.risk_score <= 5 ? "text-go" : result.risk_score <= 7 ? "text-check" : "text-no-go" },
            ].map((m) => (
              <div key={m.label} className="bg-bg-card p-4 text-center">
                <div className="text-text-muted text-xs uppercase">{m.label}</div>
                <div className={`font-[family-name:var(--font-mono)] text-xl font-bold ${m.color}`}>{m.value}</div>
              </div>
            ))}
          </div>

          {/* Source Prices */}
          <div className="p-4 bg-bg-card border-t border-border">
            <h3 className="text-text-secondary text-xs uppercase tracking-wider mb-3">Preisquellen</h3>
            <div className="space-y-2">
              {Object.entries(result.source_prices).map(([source, price]) => (
                <div key={source} className="flex justify-between text-sm">
                  <span className="text-text-muted">{source}</span>
                  <span className="text-text-primary font-[family-name:var(--font-mono)]">{price.toFixed(2)}€</span>
                </div>
              ))}
              <div className="flex justify-between text-sm pt-2 border-t border-border/50">
                <span className="text-text-secondary font-medium">Markt-Konsens ({result.num_sources} Quellen)</span>
                <span className="text-lego-yellow font-[family-name:var(--font-mono)] font-bold">{result.market_price.toFixed(2)}€</span>
              </div>
            </div>
          </div>

          {/* ROI Breakdown */}
          <div className="p-4 bg-bg-card border-t border-border">
            <h3 className="text-text-secondary text-xs uppercase tracking-wider mb-3">ROI Berechnung</h3>
            <div className="space-y-1.5 text-sm">
              <div className="flex justify-between">
                <span className="text-text-muted">Kaufpreis</span>
                <span className="text-text-primary font-[family-name:var(--font-mono)]">{result.offer_price.toFixed(2)}€</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Gesamtkosten (Kauf)</span>
                <span className="text-text-primary font-[family-name:var(--font-mono)]">{result.total_purchase_cost.toFixed(2)}€</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Verkaufskosten (eBay Gebühren)</span>
                <span className="text-no-go font-[family-name:var(--font-mono)]">-{result.total_selling_costs.toFixed(2)}€</span>
              </div>
              <div className="flex justify-between pt-2 border-t border-border/50 font-medium">
                <span className="text-text-secondary">Netto-Gewinn</span>
                <span className={`font-[family-name:var(--font-mono)] font-bold ${result.net_profit > 0 ? "text-go-star" : "text-no-go"}`}>
                  {result.net_profit > 0 ? "+" : ""}{result.net_profit.toFixed(2)}€
                </span>
              </div>
            </div>
          </div>

          {/* Suggestions */}
          {result.suggestions.length > 0 && (
            <div className="p-4 bg-bg-card border-t border-border">
              <h3 className="text-text-secondary text-xs uppercase tracking-wider mb-3">Empfehlungen</h3>
              <div className="flex flex-wrap gap-2">
                {result.suggestions.map((s, i) => (
                  <span key={i} className="bg-bg-hover text-text-secondary text-xs px-3 py-1.5 rounded-full">
                    {s}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Warnings */}
          {result.warnings.length > 0 && (
            <div className="p-4 bg-bg-card border-t border-border">
              <div className="flex flex-wrap gap-2">
                {result.warnings.map((w, i) => (
                  <span key={i} className="bg-no-go/10 text-no-go text-xs px-3 py-1.5 rounded-full">
                    ⚠ {w}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Gekauft Button */}
          <div className="p-4 bg-bg-card border-t border-border">
            <button
              onClick={() => setShowBuyModal(true)}
              className="w-full bg-go-star text-black font-bold py-3 rounded-lg hover:bg-go-star/90 transition-colors"
            >
              📦 Gekauft — ins Inventar aufnehmen
            </button>
          </div>
        </div>
      )}

      {/* Seller Check */}
      {result && (
        <div className="bg-bg-card border border-border rounded-xl p-6 mt-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-text-primary font-bold">🔍 Seller-Check</h2>
            <button
              onClick={() => setShowSellerCheck(!showSellerCheck)}
              className="text-text-muted text-xs hover:text-lego-yellow transition-colors"
            >
              {showSellerCheck ? "▾ Schließen" : "▸ Weitere Angebote des Verkäufers prüfen"}
            </button>
          </div>

          {showSellerCheck && (
            <div>
              <div className="flex gap-3 mb-4">
                <input
                  type="text"
                  value={sellerUrl}
                  onChange={(e) => setSellerUrl(e.target.value)}
                  placeholder='Seller-Profil-URL einfügen (z.B. "Alle Anzeigen" Link)'
                  className="flex-1 bg-bg-primary border border-border rounded-lg px-4 py-2 text-text-primary text-sm placeholder:text-text-muted focus:border-lego-yellow focus:outline-none"
                />
                <button
                  onClick={() => sellerCheck.mutate(sellerUrl)}
                  disabled={!sellerUrl || sellerCheck.isPending}
                  className="bg-lego-blue text-white font-bold px-6 py-2 rounded-lg hover:bg-lego-blue/90 transition-colors disabled:opacity-50 text-sm whitespace-nowrap"
                >
                  {sellerCheck.isPending ? "Prüfe..." : "Prüfen"}
                </button>
              </div>

              {sellerCheck.isError && (
                <p className="text-no-go text-sm mb-3">{sellerCheck.error.message}</p>
              )}

              {sellerCheck.data && (
                <div>
                  {sellerCheck.data.bundle_suggestion && (
                    <div className="bg-go-star/10 border border-go-star/30 rounded-lg p-3 mb-4">
                      <p className="text-go-star text-sm font-medium">💡 {sellerCheck.data.bundle_suggestion}</p>
                    </div>
                  )}

                  <p className="text-text-muted text-xs mb-3">
                    {sellerCheck.data.lego_listings.length} LEGO-Angebote von {sellerCheck.data.total_listings} Gesamtanzeigen
                  </p>

                  <div className="space-y-2 max-h-64 overflow-y-auto">
                    {sellerCheck.data.lego_listings.map((listing, i) => (
                      <div
                        key={i}
                        className="flex items-center justify-between p-3 bg-bg-primary rounded-lg hover:bg-bg-hover transition-colors cursor-pointer"
                        onClick={() => {
                          if (listing.set_number) {
                            setSetNumber(listing.set_number);
                            if (listing.price) setOfferPrice(String(listing.price));
                            setSourceUrl(listing.url);
                            window.scrollTo({ top: 0, behavior: "smooth" });
                          }
                        }}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            {listing.set_number && (
                              <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-xs">{listing.set_number}</span>
                            )}
                            <span className="text-text-secondary text-sm truncate">{listing.title}</span>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 ml-3">
                          {listing.is_negotiable && (
                            <span className="text-check text-xs">VB</span>
                          )}
                          <span className="text-text-primary font-[family-name:var(--font-mono)] text-sm font-bold">
                            {listing.price ? `${listing.price}€` : "—"}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>

                  {sellerCheck.data.total_value > 0 && (
                    <div className="flex justify-between mt-3 pt-3 border-t border-border/50">
                      <span className="text-text-muted text-sm">Gesamtwert LEGO</span>
                      <span className="text-lego-yellow font-[family-name:var(--font-mono)] font-bold">
                        {sellerCheck.data.total_value.toFixed(0)}€
                      </span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Buy Modal */}
      {showBuyModal && result && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-bg-card border border-border rounded-xl p-6 w-full max-w-md mx-4">
            <h2 className="text-text-primary text-lg font-bold mb-4">Ins Inventar aufnehmen</h2>
            <div className="space-y-3">
              <div className="text-text-muted text-sm">
                <span className="text-lego-yellow font-[family-name:var(--font-mono)]">{result.set_number}</span> — {result.set_name}
              </div>
              <div className="text-text-primary font-[family-name:var(--font-mono)]">
                Kaufpreis: {result.offer_price}€ + {shipping || "0"}€ Versand
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Kaufdatum</label>
                <input
                  type="date"
                  value={buyDate}
                  onChange={(e) => setBuyDate(e.target.value)}
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
                />
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Plattform</label>
                <input
                  type="text"
                  value={buyPlatform || sourcePlatform}
                  onChange={(e) => setBuyPlatform(e.target.value)}
                  placeholder="z.B. Kleinanzeigen, eBay, Amazon"
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
                />
              </div>
            </div>
            <div className="flex gap-3 mt-6">
              <button
                onClick={() => setShowBuyModal(false)}
                className="flex-1 bg-bg-hover text-text-secondary py-2 rounded-lg hover:text-text-primary transition-colors"
              >
                Abbrechen
              </button>
              <button
                onClick={handleBuy}
                disabled={addToInventory.isPending}
                className="flex-1 bg-go-star text-black font-bold py-2 rounded-lg hover:bg-go-star/90 transition-colors disabled:opacity-50"
              >
                {addToInventory.isPending ? "Speichern..." : "Speichern"}
              </button>
            </div>
            {addToInventory.isError && (
              <p className="text-no-go text-sm mt-3">Fehler: {addToInventory.error.message}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
