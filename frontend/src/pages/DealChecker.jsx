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

  // Konvolut state
  const [isKonvolut, setIsKonvolut] = useState(false);
  const [konvolutSets, setKonvolutSets] = useState([]);
  const [konvolutPrice, setKonvolutPrice] = useState("");
  const [showKonvolutBuyModal, setShowKonvolutBuyModal] = useState(false);
  const [konvolutBuyPlatform, setKonvolutBuyPlatform] = useState("");
  const [konvolutBuyDate, setKonvolutBuyDate] = useState(new Date().toISOString().split("T")[0]);
  const [konvolutAddingAll, setKonvolutAddingAll] = useState(false);

  // URL parsing mutation
  const parseUrl = useMutation({
    mutationFn: (url) => api.parseUrl(url),
    onSuccess: (data) => {
      if (data.is_konvolut && data.set_numbers && data.set_numbers.length > 0) {
        // Konvolut mode
        setIsKonvolut(true);
        setKonvolutSets(data.set_numbers);
        if (data.price) {
          setKonvolutPrice(String(data.price));
          setParseMessage(`Konvolut mit ${data.set_numbers.length} Sets erkannt, Preis ${data.price}`);
        } else {
          setParseMessage(`Konvolut mit ${data.set_numbers.length} Sets erkannt`);
        }
        // Clear single-set fields
        setSetNumber("");
        setOfferPrice("");
      } else {
        // Single set mode
        setIsKonvolut(false);
        setKonvolutSets([]);
        setKonvolutPrice("");
        if (data.set_number) setSetNumber(data.set_number);
        if (data.price) {
          setOfferPrice(String(data.price));
          setParseMessage(`Set ${data.set_number} erkannt, Preis ${data.price}`);
        } else if (data.set_number) {
          setParseMessage(`Set ${data.set_number} erkannt -- bitte Preis manuell eingeben`);
        } else {
          setParseMessage("Set-Nummer nicht erkannt -- bitte manuell eingeben");
        }
      }
      if (data.condition) setCondition(data.condition);
      if (data.url) setSourceUrl(data.url);
      if (data.platform) setSourcePlatform(data.platform);
      setSmartInput("");
    },
    onError: () => {
      setParseMessage("URL konnte nicht geladen werden -- bitte manuell eingeben");
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

  // Multi-set analysis mutation
  const analyzeMulti = useMutation({
    mutationFn: (data) => api.analyzeMulti(data),
    onSuccess: () => {
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
      // Treat as set number — reset konvolut mode
      setIsKonvolut(false);
      setKonvolutSets([]);
      setKonvolutPrice("");
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

  const handleAnalyzeKonvolut = () => {
    if (konvolutSets.length === 0 || !konvolutPrice) return;
    analyzeMulti.mutate({
      set_numbers: konvolutSets,
      total_price: parseFloat(konvolutPrice),
      condition,
    });
  };

  const removeKonvolutSet = (setNum) => {
    setKonvolutSets((prev) => prev.filter((s) => s !== setNum));
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

  const handleBuyAllKonvolut = async () => {
    const multiResult = analyzeMulti.data;
    if (!multiResult || !multiResult.results) return;
    setKonvolutAddingAll(true);
    try {
      for (const item of multiResult.results) {
        const allocatedPrice = multiResult.price_allocation?.[item.set_number] ?? item.offer_price;
        await api.addInventory({
          set_number: item.set_number,
          set_name: item.set_name,
          theme: item.theme,
          buy_price: allocatedPrice,
          buy_shipping: 0,
          buy_date: konvolutBuyDate,
          buy_platform: konvolutBuyPlatform || sourcePlatform || null,
          condition,
        });
      }
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
      setShowKonvolutBuyModal(false);
    } catch {
      // error handled by UI
    } finally {
      setKonvolutAddingAll(false);
    }
  };

  const loadFromHistory = (item) => {
    setIsKonvolut(false);
    setKonvolutSets([]);
    setKonvolutPrice("");
    setSetNumber(item.set_number);
    setOfferPrice(String(item.offer_price));
    setShowHistory(false);
  };

  const result = analyze.data;
  const multiResult = analyzeMulti.data;
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
            <span>{showHistory ? "\u25B4" : "\u25BE"}</span>
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
                <div className="text-text-muted text-xs">{item.offer_price}\u20AC</div>
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
            Link einf\u00FCgen (Kleinanzeigen, eBay, Amazon) oder direkt Set-Nummer eingeben
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
            {sourceUrl && !parseUrl.isPending && (
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-go text-xs">
                {sourcePlatform}
              </span>
            )}
          </div>
        </div>

        {/* Konvolut Banner & Controls */}
        {isKonvolut && konvolutSets.length > 0 && (
          <div className="mb-4">
            {/* Info Banner */}
            <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-3 mb-3">
              <p className="text-blue-400 text-sm font-medium">
                Konvolut erkannt! {konvolutSets.length} Sets gefunden
              </p>
            </div>

            {/* Set Chips */}
            <div className="flex flex-wrap gap-2 mb-3">
              {konvolutSets.map((s) => (
                <span
                  key={s}
                  className="inline-flex items-center gap-1.5 bg-bg-primary border border-lego-yellow/40 text-text-primary text-sm font-[family-name:var(--font-mono)] px-3 py-1.5 rounded-full"
                >
                  {s}
                  <button
                    type="button"
                    onClick={() => removeKonvolutSet(s)}
                    className="text-text-muted hover:text-no-go transition-colors text-xs leading-none"
                    aria-label={`Set ${s} entfernen`}
                  >
                    \u2715
                  </button>
                </span>
              ))}
            </div>

            {/* Total Price Input */}
            <div className="mb-3">
              <label className="block text-text-muted text-xs mb-1">Gesamtpreis (Konvolut)</label>
              <div className="relative w-48">
                <input
                  type="number"
                  step="0.01"
                  value={konvolutPrice}
                  onChange={(e) => setKonvolutPrice(e.target.value)}
                  placeholder="0.00"
                  className="w-full bg-bg-primary border border-border rounded-lg px-4 py-3 text-text-primary font-[family-name:var(--font-mono)] text-lg placeholder:text-text-muted focus:border-lego-yellow focus:outline-none transition-colors pr-8"
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted">\u20AC</span>
              </div>
            </div>

            {/* Analyze Konvolut Button */}
            <button
              type="button"
              onClick={handleAnalyzeKonvolut}
              disabled={analyzeMulti.isPending || konvolutSets.length === 0 || !konvolutPrice}
              className="w-full bg-lego-yellow text-black font-bold py-3 rounded-lg hover:bg-lego-yellow/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {analyzeMulti.isPending ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="inline-block w-4 h-4 border-2 border-black/30 border-t-black rounded-full animate-spin" />
                  Analysiere {konvolutSets.length} Sets...
                </span>
              ) : `Alle ${konvolutSets.length} Sets analysieren`}
            </button>

            {analyzeMulti.isError && (
              <p className="text-no-go text-sm mt-3">Fehler: {analyzeMulti.error.message}</p>
            )}
          </div>
        )}

        {/* Single-set fields (hidden in Konvolut mode) */}
        {!isKonvolut && (
          <>
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
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted">\u20AC</span>
                </div>
              </div>
            </div>

            {/* Expandable Options */}
            <button
              type="button"
              onClick={() => setShowOptions(!showOptions)}
              className="text-text-muted text-xs hover:text-text-secondary transition-colors mb-3"
            >
              {showOptions ? "\u25BE" : "\u25B8"} Optionen
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
                      <option value="NEW_SEALED">Neu &amp; Versiegelt</option>
                      <option value="NEW_OPEN">Neu &amp; Ge\u00F6ffnet</option>
                      <option value="USED_COMPLETE">Gebraucht (komplett)</option>
                      <option value="USED_INCOMPLETE">Gebraucht (unvollst\u00E4ndig)</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-text-muted text-xs mb-1">Versand</label>
                    <select
                      value={shipping}
                      onChange={(e) => setShipping(e.target.value)}
                      className="w-full bg-bg-card border border-border rounded-lg px-2 py-1.5 text-text-primary text-sm"
                    >
                      <option value="">Ausw\u00E4hlen...</option>
                      {SHIPPING_PRESETS.map((p) => (
                        <option key={p.label} value={p.value}>
                          {p.label} ({p.value.toFixed(2)}\u20AC)
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
                      <span className="text-text-secondary text-sm">Box besch\u00E4digt</span>
                    </label>
                  </div>
                </div>
                {shipping === "custom" && (
                  <div className="w-1/3">
                    <label className="block text-text-muted text-xs mb-1">Versandkosten (\u20AC)</label>
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
          </>
        )}

        {parseMessage && (
          <p className={`text-sm mt-3 ${parseMessage.startsWith("Set") || parseMessage.startsWith("Konvolut") ? "text-go" : "text-check"}`}>{parseMessage}</p>
        )}
        {analyze.isError && (
          <p className="text-no-go text-sm mt-3">Fehler: {analyze.error.message}</p>
        )}
      </form>

      {/* Konvolut Multi-Set Results */}
      {isKonvolut && multiResult && multiResult.results && (
        <div className="space-y-4 mb-6">
          {/* Summary Bar */}
          <div className="border border-border rounded-xl overflow-hidden">
            <div className="bg-bg-card p-4 border-b border-border">
              <h2 className="text-text-primary font-bold text-lg mb-1">Konvolut-Zusammenfassung</h2>
              <p className="text-text-muted text-xs">{multiResult.results.length} Sets analysiert</p>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-border">
              <div className="bg-bg-card p-4 text-center">
                <div className="text-text-muted text-xs uppercase">Gesamtwert</div>
                <div className="font-[family-name:var(--font-mono)] text-xl font-bold text-lego-yellow">
                  {multiResult.summary?.total_market_value?.toFixed(0) ?? "--"}\u20AC
                </div>
              </div>
              <div className="bg-bg-card p-4 text-center">
                <div className="text-text-muted text-xs uppercase">Gesamt-ROI</div>
                <div className={`font-[family-name:var(--font-mono)] text-xl font-bold ${(multiResult.summary?.total_roi ?? 0) >= 20 ? "text-go-star" : (multiResult.summary?.total_roi ?? 0) >= 0 ? "text-check" : "text-no-go"}`}>
                  {multiResult.summary?.total_roi?.toFixed(1) ?? "--"}%
                </div>
              </div>
              <div className="bg-bg-card p-4 text-center">
                <div className="text-text-muted text-xs uppercase">Kaufpreis</div>
                <div className="font-[family-name:var(--font-mono)] text-xl font-bold text-text-primary">
                  {multiResult.summary?.total_price?.toFixed(0) ?? konvolutPrice}\u20AC
                </div>
              </div>
              <div className="bg-bg-card p-4 text-center">
                <div className="text-text-muted text-xs uppercase">Empfehlung</div>
                <div className="mt-1">
                  {multiResult.summary?.overall_recommendation ? (
                    <VerdictBadge verdict={multiResult.summary.overall_recommendation} size="md" />
                  ) : (
                    <span className="text-text-muted">--</span>
                  )}
                </div>
              </div>
            </div>

            {/* Price Allocation */}
            {multiResult.price_allocation && (
              <div className="p-4 bg-bg-card border-t border-border">
                <h3 className="text-text-secondary text-xs uppercase tracking-wider mb-3">Preis-Aufteilung</h3>
                <div className="space-y-1.5 text-sm">
                  {Object.entries(multiResult.price_allocation).map(([setNum, price]) => (
                    <div key={setNum} className="flex justify-between">
                      <span className="text-lego-yellow font-[family-name:var(--font-mono)]">{setNum}</span>
                      <span className="text-text-primary font-[family-name:var(--font-mono)]">{price.toFixed(2)}\u20AC</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Individual Set Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {multiResult.results.map((item) => {
              const itemBg = verdictBg[item.recommendation] || verdictBg.NO_GO;
              const allocatedPrice = multiResult.price_allocation?.[item.set_number];
              return (
                <div
                  key={item.set_number}
                  className={`border rounded-xl overflow-hidden bg-gradient-to-b ${itemBg}`}
                >
                  <div className="p-4">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-lego-yellow font-[family-name:var(--font-mono)] font-bold">{item.set_number}</span>
                      <VerdictBadge verdict={item.recommendation} size="sm" />
                    </div>
                    <p className="text-text-muted text-xs mb-3 truncate">{item.set_name}</p>
                    <div className="space-y-1.5 text-sm">
                      {allocatedPrice != null && (
                        <div className="flex justify-between">
                          <span className="text-text-muted">Anteil</span>
                          <span className="text-text-primary font-[family-name:var(--font-mono)]">{allocatedPrice.toFixed(2)}\u20AC</span>
                        </div>
                      )}
                      <div className="flex justify-between">
                        <span className="text-text-muted">Marktwert</span>
                        <span className="text-lego-yellow font-[family-name:var(--font-mono)]">{item.market_price?.toFixed(2) ?? "--"}\u20AC</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-text-muted">ROI</span>
                        <span className={`font-[family-name:var(--font-mono)] font-bold ${item.roi_percent >= 20 ? "text-go-star" : item.roi_percent >= 0 ? "text-check" : "text-no-go"}`}>
                          {item.roi_percent?.toFixed(1) ?? "--"}%
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-text-muted">Gewinn</span>
                        <span className={`font-[family-name:var(--font-mono)] font-bold ${item.net_profit > 0 ? "text-go" : "text-no-go"}`}>
                          {item.net_profit > 0 ? "+" : ""}{item.net_profit?.toFixed(2) ?? "--"}\u20AC
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Add All to Inventory */}
          <button
            onClick={() => setShowKonvolutBuyModal(true)}
            className="w-full bg-go-star text-black font-bold py-3 rounded-lg hover:bg-go-star/90 transition-colors"
          >
            Alle {multiResult.results.length} Sets ins Inventar aufnehmen
          </button>
        </div>
      )}

      {/* Analysis Result (Single Set) */}
      {!isKonvolut && result && (
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
              { label: "Net Profit", value: `${result.net_profit.toFixed(0)}\u20AC`, color: result.net_profit > 0 ? "text-go" : "text-no-go" },
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
                  <span className="text-text-primary font-[family-name:var(--font-mono)]">{price.toFixed(2)}\u20AC</span>
                </div>
              ))}
              <div className="flex justify-between text-sm pt-2 border-t border-border/50">
                <span className="text-text-secondary font-medium">Markt-Konsens ({result.num_sources} Quellen)</span>
                <span className="text-lego-yellow font-[family-name:var(--font-mono)] font-bold">{result.market_price.toFixed(2)}\u20AC</span>
              </div>
            </div>
          </div>

          {/* ROI Breakdown */}
          <div className="p-4 bg-bg-card border-t border-border">
            <h3 className="text-text-secondary text-xs uppercase tracking-wider mb-3">ROI Berechnung</h3>
            <div className="space-y-1.5 text-sm">
              <div className="flex justify-between">
                <span className="text-text-muted">Kaufpreis</span>
                <span className="text-text-primary font-[family-name:var(--font-mono)]">{result.offer_price.toFixed(2)}\u20AC</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Gesamtkosten (Kauf)</span>
                <span className="text-text-primary font-[family-name:var(--font-mono)]">{result.total_purchase_cost.toFixed(2)}\u20AC</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Verkaufskosten (eBay Geb\u00FChren)</span>
                <span className="text-no-go font-[family-name:var(--font-mono)]">-{result.total_selling_costs.toFixed(2)}\u20AC</span>
              </div>
              <div className="flex justify-between pt-2 border-t border-border/50 font-medium">
                <span className="text-text-secondary">Netto-Gewinn</span>
                <span className={`font-[family-name:var(--font-mono)] font-bold ${result.net_profit > 0 ? "text-go-star" : "text-no-go"}`}>
                  {result.net_profit > 0 ? "+" : ""}{result.net_profit.toFixed(2)}\u20AC
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
                    {w}
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
              Gekauft -- ins Inventar aufnehmen
            </button>
          </div>
        </div>
      )}

      {/* Seller Check */}
      {!isKonvolut && result && (
        <div className="bg-bg-card border border-border rounded-xl p-6 mt-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-text-primary font-bold">Seller-Check</h2>
            <button
              onClick={() => setShowSellerCheck(!showSellerCheck)}
              className="text-text-muted text-xs hover:text-lego-yellow transition-colors"
            >
              {showSellerCheck ? "\u25BE Schlie\u00DFen" : "\u25B8 Weitere Angebote des Verk\u00E4ufers pr\u00FCfen"}
            </button>
          </div>

          {showSellerCheck && (
            <div>
              <div className="flex gap-3 mb-4">
                <input
                  type="text"
                  value={sellerUrl}
                  onChange={(e) => setSellerUrl(e.target.value)}
                  placeholder='Seller-Profil-URL einf\u00FCgen (z.B. "Alle Anzeigen" Link)'
                  className="flex-1 bg-bg-primary border border-border rounded-lg px-4 py-2 text-text-primary text-sm placeholder:text-text-muted focus:border-lego-yellow focus:outline-none"
                />
                <button
                  onClick={() => sellerCheck.mutate(sellerUrl)}
                  disabled={!sellerUrl || sellerCheck.isPending}
                  className="bg-lego-blue text-white font-bold px-6 py-2 rounded-lg hover:bg-lego-blue/90 transition-colors disabled:opacity-50 text-sm whitespace-nowrap"
                >
                  {sellerCheck.isPending ? "Pr\u00FCfe..." : "Pr\u00FCfen"}
                </button>
              </div>

              {sellerCheck.isError && (
                <p className="text-no-go text-sm mb-3">{sellerCheck.error.message}</p>
              )}

              {sellerCheck.data && (
                <div>
                  {sellerCheck.data.bundle_suggestion && (
                    <div className="bg-go-star/10 border border-go-star/30 rounded-lg p-3 mb-4">
                      <p className="text-go-star text-sm font-medium">{sellerCheck.data.bundle_suggestion}</p>
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
                            {listing.price ? `${listing.price}\u20AC` : "\u2014"}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>

                  {sellerCheck.data.total_value > 0 && (
                    <div className="flex justify-between mt-3 pt-3 border-t border-border/50">
                      <span className="text-text-muted text-sm">Gesamtwert LEGO</span>
                      <span className="text-lego-yellow font-[family-name:var(--font-mono)] font-bold">
                        {sellerCheck.data.total_value.toFixed(0)}\u20AC
                      </span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Buy Modal (Single Set) */}
      {showBuyModal && result && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-bg-card border border-border rounded-xl p-6 w-full max-w-md mx-4">
            <h2 className="text-text-primary text-lg font-bold mb-4">Ins Inventar aufnehmen</h2>
            <div className="space-y-3">
              <div className="text-text-muted text-sm">
                <span className="text-lego-yellow font-[family-name:var(--font-mono)]">{result.set_number}</span> -- {result.set_name}
              </div>
              <div className="text-text-primary font-[family-name:var(--font-mono)]">
                Kaufpreis: {result.offer_price}\u20AC + {shipping || "0"}\u20AC Versand
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

      {/* Konvolut Buy Modal */}
      {showKonvolutBuyModal && multiResult && multiResult.results && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-bg-card border border-border rounded-xl p-6 w-full max-w-md mx-4">
            <h2 className="text-text-primary text-lg font-bold mb-4">Alle {multiResult.results.length} Sets ins Inventar aufnehmen</h2>
            <div className="space-y-3">
              <div className="text-text-muted text-sm">
                {multiResult.results.map((r) => (
                  <div key={r.set_number} className="flex justify-between py-1">
                    <span className="text-lego-yellow font-[family-name:var(--font-mono)]">{r.set_number}</span>
                    <span className="text-text-primary font-[family-name:var(--font-mono)]">
                      {(multiResult.price_allocation?.[r.set_number] ?? r.offer_price).toFixed(2)}\u20AC
                    </span>
                  </div>
                ))}
              </div>
              <div className="flex justify-between pt-2 border-t border-border/50 font-medium text-sm">
                <span className="text-text-secondary">Gesamt</span>
                <span className="text-lego-yellow font-[family-name:var(--font-mono)] font-bold">{konvolutPrice}\u20AC</span>
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Kaufdatum</label>
                <input
                  type="date"
                  value={konvolutBuyDate}
                  onChange={(e) => setKonvolutBuyDate(e.target.value)}
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
                />
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Plattform</label>
                <input
                  type="text"
                  value={konvolutBuyPlatform || sourcePlatform}
                  onChange={(e) => setKonvolutBuyPlatform(e.target.value)}
                  placeholder="z.B. Kleinanzeigen, eBay, Amazon"
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
                />
              </div>
            </div>
            <div className="flex gap-3 mt-6">
              <button
                onClick={() => setShowKonvolutBuyModal(false)}
                className="flex-1 bg-bg-hover text-text-secondary py-2 rounded-lg hover:text-text-primary transition-colors"
              >
                Abbrechen
              </button>
              <button
                onClick={handleBuyAllKonvolut}
                disabled={konvolutAddingAll}
                className="flex-1 bg-go-star text-black font-bold py-2 rounded-lg hover:bg-go-star/90 transition-colors disabled:opacity-50"
              >
                {konvolutAddingAll ? "Speichere..." : "Alle speichern"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
