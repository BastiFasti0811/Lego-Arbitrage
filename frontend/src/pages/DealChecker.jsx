import { useEffect, useRef, useState } from "react";
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

const EURO = "\u20ac";
const ICON_UP = "\u25b4";
const ICON_DOWN = "\u25be";
const ICON_RIGHT = "\u25b8";
const ICON_CLOSE = "\u2715";
const ICON_EXTERNAL = "\u2197";
const formatMoney = (value, digits = 2) => `${Number(value).toFixed(digits)}${EURO}`;
const formatAnalyzedAt = (value) =>
  new Date(value).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
const referenceLabelMap = {
  LEGO_UVP: "LEGO UVP",
  MARKT_KONSENS: "Markt-Konsens",
  ANGEBOT_PREIS: "Angebotspreis",
};

function describeLearning(stats) {
  if (!stats?.completed_deals) {
    return "Sobald echte Verkäufe vorliegen, kalibriert sich die ROI-Logik mit deinen Ergebnissen.";
  }

  if (stats.avg_roi_deviation == null) {
    return `${stats.completed_deals} Verkäufe erfasst. Für die Kalibrierung fehlen noch genug Vergleichswerte.`;
  }

  if (stats.avg_roi_deviation < -2) {
    return `Bisher war das System im Schnitt ${Math.abs(stats.avg_roi_deviation).toFixed(1)} ROI-Punkte zu optimistisch.`;
  }

  if (stats.avg_roi_deviation > 2) {
    return `Bisher war das System im Schnitt ${stats.avg_roi_deviation.toFixed(1)} ROI-Punkte zu konservativ.`;
  }

  return `Bisher liegen Prognose und Realität im Schnitt nur ${Math.abs(stats.avg_roi_deviation).toFixed(1)} ROI-Punkte auseinander.`;
}

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
  const barcodeInputRef = useRef(null);
  const liveVideoRef = useRef(null);
  const liveScannerStreamRef = useRef(null);
  const liveScannerTimeoutRef = useRef(null);

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
  const [selectedHistoryItem, setSelectedHistoryItem] = useState(null);
  const [isScanningBarcode, setIsScanningBarcode] = useState(false);
  const [barcodeRawValue, setBarcodeRawValue] = useState("");
  const [showLiveScanner, setShowLiveScanner] = useState(false);
  const [cameraError, setCameraError] = useState("");

  // Seller check
  const [sellerUrl, setSellerUrl] = useState("");
  const [showSellerCheck, setShowSellerCheck] = useState(false);

  // Gekauft modal
  const [showBuyModal, setShowBuyModal] = useState(false);
  const [buyPlatform, setBuyPlatform] = useState("");
  const [buyDate, setBuyDate] = useState(new Date().toISOString().split("T")[0]);

  // URL parse status message
  const [parseMessage, setParseMessage] = useState("");

  // Auction bid guard
  const [auctionCurrentBid, setAuctionCurrentBid] = useState("");
  const [auctionShipping, setAuctionShipping] = useState("");
  const [auctionTargetRoi, setAuctionTargetRoi] = useState("");
  const [auctionPlatform, setAuctionPlatform] = useState("CATAWIKI");

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
      if (data.platform === "CATAWIKI") {
        setAuctionPlatform("CATAWIKI");
        if (data.shipping != null) {
          setAuctionShipping(String(data.shipping));
        }
        if (data.price != null) {
          setAuctionCurrentBid(String(data.price));
        }
      }
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
      setSelectedHistoryItem(null);
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

  const auctionMaxBid = useMutation({
    mutationFn: (data) => api.auctionMaxBid(data),
  });

  const addAuctionWatch = useMutation({
    mutationFn: (data) => api.addAuctionWatch(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auction-watch"] }),
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

  const { data: feedbackPerformance } = useQuery({
    queryKey: ["feedback-performance"],
    queryFn: () => api.feedbackPerformance(),
    staleTime: 30000,
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

  function stopLiveScanner({ closeModal = true } = {}) {
    if (liveScannerTimeoutRef.current) {
      window.clearTimeout(liveScannerTimeoutRef.current);
      liveScannerTimeoutRef.current = null;
    }

    if (liveScannerStreamRef.current) {
      liveScannerStreamRef.current.getTracks().forEach((track) => track.stop());
      liveScannerStreamRef.current = null;
    }

    if (liveVideoRef.current) {
      liveVideoRef.current.srcObject = null;
    }

    if (closeModal) {
      setShowLiveScanner(false);
    }

    setIsScanningBarcode(false);
  }

  useEffect(() => () => {
    if (liveScannerTimeoutRef.current) {
      window.clearTimeout(liveScannerTimeoutRef.current);
      liveScannerTimeoutRef.current = null;
    }
    if (liveScannerStreamRef.current) {
      liveScannerStreamRef.current.getTracks().forEach((track) => track.stop());
      liveScannerStreamRef.current = null;
    }
  }, []);

  const handleDetectedCode = async (rawValue) => {
    const normalizedValue = rawValue.trim();
    setBarcodeRawValue(normalizedValue);
    const lookup = await api.lookupCode(normalizedValue);

    if (lookup.matched_set_number) {
      setIsKonvolut(false);
      setKonvolutSets([]);
      setKonvolutPrice("");
      setSetNumber(lookup.matched_set_number);
    }

    if (lookup.found && lookup.set_number) {
      setParseMessage(lookup.message || `Code ${normalizedValue} erkannt, Set ${lookup.set_number} geladen`);
    } else if (lookup.matched_set_number) {
      setParseMessage(lookup.message || `Code ${normalizedValue} erkannt, bitte Preis ergänzen`);
    } else {
      const fallbackMatch = normalizedValue.match(/\b(\d{4,6})\b/);
      if (fallbackMatch) {
        setSetNumber(fallbackMatch[1]);
      }
      setParseMessage(lookup.message || "Code erkannt, aber kein Set automatisch zugeordnet");
    }
  };

  const scheduleLiveScan = (detector) => {
    liveScannerTimeoutRef.current = window.setTimeout(async () => {
      const video = liveVideoRef.current;
      if (!video || !liveScannerStreamRef.current) {
        return;
      }

      try {
        if (video.readyState >= 2) {
          const results = await detector.detect(video);
          const rawValue = results.find((result) => result?.rawValue)?.rawValue?.trim();
          if (rawValue) {
            stopLiveScanner();
            await handleDetectedCode(rawValue);
            return;
          }
        }
      } catch {
        // Ignore transient detection errors and keep scanning.
      }

      scheduleLiveScan(detector);
    }, 350);
  };

  const startLiveScanner = async () => {
    stopLiveScanner({ closeModal: false });
    setSelectedHistoryItem(null);
    setBarcodeRawValue("");
    setParseMessage("");
    setCameraError("");
    setShowLiveScanner(true);

    try {
      if (!window.BarcodeDetector) {
        throw new Error("Live-Scan wird in diesem Browser leider nicht unterstützt.");
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("Die Kamera ist in diesem Browser nicht verfügbar.");
      }
      if (!window.isSecureContext && !["localhost", "127.0.0.1"].includes(window.location.hostname)) {
        throw new Error("Live-Kamera-Scan braucht HTTPS oder localhost.");
      }

      setIsScanningBarcode(true);
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" } },
        audio: false,
      });
      liveScannerStreamRef.current = stream;

      const video = liveVideoRef.current;
      if (!video) {
        throw new Error("Die Kamera-Vorschau konnte nicht initialisiert werden.");
      }

      video.srcObject = stream;
      await video.play();

      const detector = new window.BarcodeDetector({
        formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39"],
      });
      scheduleLiveScan(detector);
    } catch (error) {
      stopLiveScanner({ closeModal: false });
      setCameraError(error.message || "Kamera konnte nicht gestartet werden.");
    }
  };

  const handleSmartInput = (value) => {
    setSelectedHistoryItem(null);
    setBarcodeRawValue("");
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

  const handleBarcodeScan = async (file) => {
    if (!file) return;
    setSelectedHistoryItem(null);
    setIsScanningBarcode(true);
    setBarcodeRawValue("");
    setParseMessage("");

    try {
      if (!window.BarcodeDetector) {
        throw new Error("Barcode-Scan wird in diesem Browser leider nicht unterstützt.");
      }

      const detector = new window.BarcodeDetector({
        formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39"],
      });
      const bitmap = await createImageBitmap(file);
      const results = await detector.detect(bitmap);
      if (typeof bitmap.close === "function") {
        bitmap.close();
      }

      if (!results.length || !results[0]?.rawValue) {
        throw new Error("Kein Barcode oder EAN auf dem Bild gefunden.");
      }

      await handleDetectedCode(results[0].rawValue);
    } catch (error) {
      setParseMessage(error.message || "Barcode-Scan fehlgeschlagen");
    } finally {
      setIsScanningBarcode(false);
      if (barcodeInputRef.current) {
        barcodeInputRef.current.value = "";
      }
    }
  };

  const handleAnalyze = (e) => {
    e.preventDefault();
    if (!setNumber || !offerPrice) return;
    setSelectedHistoryItem(null);
    analyze.mutate({
      set_number: setNumber.trim(),
      offer_price: parseFloat(offerPrice),
      condition,
      box_damage: boxDamage,
      purchase_shipping: shipping ? parseFloat(shipping) : null,
      source_url: sourceUrl || null,
      source_platform: sourcePlatform || null,
    });
  };

  const handleAnalyzeKonvolut = () => {
    if (konvolutSets.length === 0 || !konvolutPrice) return;
    analyzeMulti.mutate({
      set_numbers: konvolutSets,
      total_price: parseFloat(konvolutPrice),
      condition,
      source_url: sourceUrl || null,
      source_platform: sourcePlatform || null,
    });
  };

  const handleAuctionMaxBid = () => {
    if (!setNumber || !auctionCurrentBid) return;
    auctionMaxBid.mutate({
      set_number: setNumber.trim(),
      current_bid: parseFloat(auctionCurrentBid),
      purchase_shipping: auctionShipping ? parseFloat(auctionShipping) : null,
      desired_roi_percent: auctionTargetRoi ? parseFloat(auctionTargetRoi) : null,
      source_platform: auctionPlatform || sourcePlatform || "CATAWIKI",
      source_url: sourceUrl || null,
    });
  };

  const handleAddAuctionWatch = () => {
    if (!auctionResult || !setNumber || !sourceUrl) return;
    addAuctionWatch.mutate({
      set_number: setNumber.trim(),
      source_url: sourceUrl,
      source_platform: auctionPlatform || sourcePlatform || "CATAWIKI",
      lot_title: result?.set_name ? `LEGO ${setNumber} - ${result.set_name}` : null,
      current_bid: parseFloat(auctionCurrentBid),
      purchase_shipping: auctionShipping ? parseFloat(auctionShipping) : 0,
      desired_roi_percent: auctionTargetRoi ? parseFloat(auctionTargetRoi) : auctionResult.target_roi_percent,
    });
  };

  const removeKonvolutSet = (setNum) => {
    setKonvolutSets((prev) => prev.filter((s) => s !== setNum));
  };

  const handleBuy = () => {
    const result = selectedHistoryItem || analyze.data;
    if (!result) return;
    addToInventory.mutate({
      set_number: result.set_number,
      set_name: result.set_name,
      theme: result.theme,
      buy_price: result.offer_price,
      buy_shipping: shipping ? parseFloat(shipping) : 0,
      buy_date: buyDate,
      buy_platform: buyPlatform || sourcePlatform || null,
      buy_url: sourceUrl || result.source_url || null,
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
          buy_url: sourceUrl || item.source_url || null,
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
    setSelectedHistoryItem(item);
    setLastAnalysis(item);
    setBarcodeRawValue("");
    setIsKonvolut(false);
    setKonvolutSets([]);
    setKonvolutPrice("");
    setSetNumber(item.set_number);
    setOfferPrice(String(item.offer_price));
    setSourceUrl(item.source_url || "");
    setSourcePlatform(item.source_platform || "");
    if (item.source_platform === "CATAWIKI") {
      setAuctionPlatform("CATAWIKI");
      setAuctionCurrentBid(String(item.offer_price));
    }
    setSmartInput(item.source_url || "");
    setParseMessage("Check aus der Historie geladen");
    setShowHistory(false);
  };

  const result = selectedHistoryItem || analyze.data;
  const multiResult = analyzeMulti.data;
  const auctionResult = auctionMaxBid.data;
  const bg = result ? verdictBg[result.recommendation] || verdictBg.NO_GO : "";
  const referenceLabel = result?.reference_label
    ? (referenceLabelMap[result.reference_label] || result.reference_label)
    : "Markt-Konsens";

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Deal-Check</h1>
        {history.length > 0 && (
          <button
            onClick={() => setShowHistory(!showHistory)}
            className="text-text-muted text-sm hover:text-lego-yellow transition-colors flex items-center gap-1"
          >
            <span className="font-[family-name:var(--font-mono)]">{history.length}</span> Check-Historie
            <span>{showHistory ? ICON_UP : ICON_DOWN}</span>
          </button>
        )}
      </div>

      {/* History Panel */}
      {showHistory && history.length > 0 && (
        <div className="bg-bg-card border border-border rounded-xl mb-6 max-h-64 overflow-y-auto">
          {history.map((item) => (
            <div
              key={item.history_id ?? `${item.set_number}-${item.analyzed_at}`}
              className="flex items-center gap-2 px-4 py-3 hover:bg-bg-hover transition-colors border-b border-border/50 last:border-0"
            >
              <button
                type="button"
                onClick={() => loadFromHistory(item)}
                className="flex-1 flex items-center justify-between text-left"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <VerdictBadge verdict={item.recommendation} size="sm" />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm">
                        {item.set_number}
                      </span>
                      <span className="text-text-muted text-sm truncate">{item.set_name}</span>
                    </div>
                    <div className="flex flex-wrap gap-x-3 gap-y-1 text-text-muted text-xs mt-1">
                      <span>{item.source_platform || "MANUELL"}</span>
                      <span>{formatAnalyzedAt(item.analyzed_at)}</span>
                    </div>
                  </div>
                </div>
                <div className="text-right shrink-0 ml-3">
                  <div
                    className={`font-[family-name:var(--font-mono)] text-sm font-bold ${
                      item.roi_percent >= 20 ? "text-go-star" : item.roi_percent >= 0 ? "text-check" : "text-no-go"
                    }`}
                  >
                    {item.roi_percent.toFixed(1)}%
                  </div>
                  <div className="text-text-muted text-xs">{formatMoney(item.offer_price)}</div>
                </div>
              </button>
              {item.source_url && (
                <a
                  href={item.source_url}
                  target="_blank"
                  rel="noreferrer"
                  title="Originalangebot öffnen"
                  className="shrink-0 text-text-muted hover:text-lego-yellow transition-colors px-2 py-1"
                >
                  {ICON_EXTERNAL}
                </a>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="bg-bg-card border border-border rounded-xl p-4 mb-6">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-text-primary font-semibold">Lern-Feedback</h2>
            <p className="text-text-muted text-sm mt-1">{describeLearning(feedbackPerformance)}</p>
          </div>
          <div className="text-right shrink-0">
            <div className="text-text-primary font-[family-name:var(--font-mono)] text-lg font-bold">
              {feedbackPerformance?.completed_deals || 0}
            </div>
            <div className="text-text-muted text-xs">verkaufte Sets</div>
          </div>
        </div>
        {feedbackPerformance?.completed_deals > 0 && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4 text-sm">
            <div className="bg-bg-hover rounded-lg px-3 py-2">
              <div className="text-text-muted text-xs uppercase">Trefferquote</div>
              <div className="text-text-primary font-[family-name:var(--font-mono)] font-semibold">
                {feedbackPerformance.success_rate != null ? `${feedbackPerformance.success_rate.toFixed(0)}%` : "--"}
              </div>
            </div>
            <div className="bg-bg-hover rounded-lg px-3 py-2">
              <div className="text-text-muted text-xs uppercase">Ist ROI</div>
              <div className="text-text-primary font-[family-name:var(--font-mono)] font-semibold">
                {feedbackPerformance.avg_actual_roi != null ? `${feedbackPerformance.avg_actual_roi.toFixed(1)}%` : "--"}
              </div>
            </div>
            <div className="bg-bg-hover rounded-lg px-3 py-2">
              <div className="text-text-muted text-xs uppercase">Prognose ROI</div>
              <div className="text-text-primary font-[family-name:var(--font-mono)] font-semibold">
                {feedbackPerformance.avg_predicted_roi != null ? `${feedbackPerformance.avg_predicted_roi.toFixed(1)}%` : "--"}
              </div>
            </div>
            <div className="bg-bg-hover rounded-lg px-3 py-2">
              <div className="text-text-muted text-xs uppercase">Abweichung</div>
              <div className="text-text-primary font-[family-name:var(--font-mono)] font-semibold">
                {feedbackPerformance.avg_roi_deviation != null ? `${feedbackPerformance.avg_roi_deviation.toFixed(1)}pp` : "--"}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Smart Input Form */}
      <form onSubmit={handleAnalyze} className="bg-bg-card border border-border rounded-xl p-6 mb-6">
        {/* URL/Link Input */}
        <div className="mb-4">
          <label className="block text-text-muted text-xs mb-1">
            {"Link einf\u00fcgen (Kleinanzeigen, eBay, Amazon, Catawiki) oder direkt Set-Nummer eingeben"}
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
              placeholder="https://www.catawiki.com/... oder 75192"
              autoFocus
              className="w-full bg-bg-primary border border-border rounded-lg px-4 py-3 text-text-primary text-sm placeholder:text-text-muted focus:border-lego-yellow focus:outline-none transition-colors"
            />
            {parseUrl.isPending && (
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-lego-yellow text-xs animate-pulse">
                Lade...
              </span>
            )}
            {sourceUrl && !parseUrl.isPending && (
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-go text-xs">
                {sourcePlatform}
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3 mt-3">
            <button
              type="button"
              onClick={startLiveScanner}
              disabled={isScanningBarcode}
              className="bg-lego-yellow/15 text-lego-yellow text-xs font-medium px-3 py-2 rounded-lg hover:bg-lego-yellow/25 transition-colors disabled:opacity-50"
            >
              {showLiveScanner && isScanningBarcode ? "Kamera läuft..." : "Live-Kamera-Scan"}
            </button>
            <button
              type="button"
              onClick={() => barcodeInputRef.current?.click()}
              disabled={isScanningBarcode}
              className="bg-lego-blue/10 text-lego-blue text-xs font-medium px-3 py-2 rounded-lg hover:bg-lego-blue/20 transition-colors disabled:opacity-50"
            >
              {showLiveScanner && isScanningBarcode ? "Kamera aktiv..." : isScanningBarcode ? "Scanne Bild..." : "Barcode/EAN aus Bild lesen"}
            </button>
            <input
              ref={barcodeInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              className="hidden"
              onChange={(e) => handleBarcodeScan(e.target.files?.[0])}
            />
            <span className="text-text-muted text-xs">
              Alternativ zum Link-Einfügen. Funktioniert am besten mit gut sichtbarem EAN/Barcode.
            </span>
          </div>
          {barcodeRawValue && (
            <p className="text-text-muted text-xs mt-2">
              Gelesener Code: <span className="font-[family-name:var(--font-mono)] text-text-secondary">{barcodeRawValue}</span>
            </p>
          )}
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
                    {ICON_CLOSE}
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
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted">{EURO}</span>
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
                  onChange={(e) => {
                    setSelectedHistoryItem(null);
                    setSetNumber(e.target.value);
                  }}
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
                    onChange={(e) => {
                      setSelectedHistoryItem(null);
                      setOfferPrice(e.target.value);
                    }}
                    placeholder="0.00"
                    className="w-full bg-bg-primary border border-border rounded-lg px-4 py-3 text-text-primary font-[family-name:var(--font-mono)] text-lg placeholder:text-text-muted focus:border-lego-yellow focus:outline-none transition-colors pr-8"
                  />
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted">{EURO}</span>
                </div>
              </div>
            </div>

            {/* Expandable Options */}
            <button
              type="button"
              onClick={() => setShowOptions(!showOptions)}
              className="text-text-muted text-xs hover:text-text-secondary transition-colors mb-3"
            >
              {showOptions ? ICON_DOWN : ICON_RIGHT} Optionen
            </button>

            {showOptions && (
              <div className="space-y-3 mb-4 p-3 bg-bg-primary rounded-lg">
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="block text-text-muted text-xs mb-1">Zustand</label>
                    <select
                      value={condition}
                      onChange={(e) => {
                        setSelectedHistoryItem(null);
                        setCondition(e.target.value);
                      }}
                      className="w-full bg-bg-card border border-border rounded-lg px-2 py-1.5 text-text-primary text-sm"
                    >
                      <option value="NEW_SEALED">Neu &amp; Versiegelt</option>
                      <option value="NEW_OPEN">{"Neu & Ge\u00f6ffnet"}</option>
                      <option value="USED_COMPLETE">Gebraucht (komplett)</option>
                      <option value="USED_INCOMPLETE">{"Gebraucht (unvollst\u00e4ndig)"}</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-text-muted text-xs mb-1">Versand</label>
                    <select
                      value={shipping}
                      onChange={(e) => {
                        setSelectedHistoryItem(null);
                        setShipping(e.target.value);
                      }}
                      className="w-full bg-bg-card border border-border rounded-lg px-2 py-1.5 text-text-primary text-sm"
                    >
                      <option value="">{"Ausw\u00e4hlen..."}</option>
                      {SHIPPING_PRESETS.map((p) => (
                        <option key={p.label} value={p.value}>
                          {p.label} ({formatMoney(p.value)})
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
                        onChange={(e) => {
                          setSelectedHistoryItem(null);
                          setBoxDamage(e.target.checked);
                        }}
                        className="accent-lego-yellow"
                      />
                      <span className="text-text-secondary text-sm">{"Box besch\u00e4digt"}</span>
                    </label>
                  </div>
                </div>
                {shipping === "custom" && (
                  <div className="w-1/3">
                    <label className="block text-text-muted text-xs mb-1">Versandkosten ({EURO})</label>
                    <input
                      type="number"
                      step="0.01"
                      onChange={(e) => {
                        setSelectedHistoryItem(null);
                        setShipping(e.target.value);
                      }}
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

      <div className="bg-bg-card border border-border rounded-xl p-6 mb-6">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h2 className="text-text-primary font-semibold">Auktions-Maximalgebot</h2>
            <p className="text-text-muted text-sm mt-1">
              Rechnet Hammerpreis, Catawiki-Gebühr und Versand gegen deinen Ziel-ROI.
            </p>
          </div>
          <div className="text-right shrink-0">
            <div className="text-text-muted text-xs uppercase">Quelle</div>
            <select
              value={auctionPlatform}
              onChange={(e) => setAuctionPlatform(e.target.value)}
              className="mt-1 bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
            >
              <option value="CATAWIKI">Catawiki</option>
              <option value="AUCTION">Andere Auktion</option>
            </select>
          </div>
        </div>

        <div className="grid md:grid-cols-3 gap-3">
          <div>
            <label className="block text-text-muted text-xs mb-1">Aktuelles Gebot ({EURO})</label>
            <input
              type="number"
              step="0.01"
              value={auctionCurrentBid}
              onChange={(e) => setAuctionCurrentBid(e.target.value)}
              placeholder="123.00"
              className="w-full bg-bg-primary border border-border rounded-lg px-3 py-3 text-text-primary font-[family-name:var(--font-mono)]"
            />
          </div>
          <div>
            <label className="block text-text-muted text-xs mb-1">Versand zu dir ({EURO})</label>
            <input
              type="number"
              step="0.01"
              value={auctionShipping}
              onChange={(e) => setAuctionShipping(e.target.value)}
              placeholder="13.00"
              className="w-full bg-bg-primary border border-border rounded-lg px-3 py-3 text-text-primary font-[family-name:var(--font-mono)]"
            />
          </div>
          <div>
            <label className="block text-text-muted text-xs mb-1">Ziel-ROI % (optional)</label>
            <input
              type="number"
              step="0.1"
              value={auctionTargetRoi}
              onChange={(e) => setAuctionTargetRoi(e.target.value)}
              placeholder="auto"
              className="w-full bg-bg-primary border border-border rounded-lg px-3 py-3 text-text-primary font-[family-name:var(--font-mono)]"
            />
          </div>
        </div>

        <button
          type="button"
          onClick={handleAuctionMaxBid}
          disabled={auctionMaxBid.isPending || !setNumber || !auctionCurrentBid}
          className="w-full mt-4 bg-lego-blue text-white font-bold py-3 rounded-lg hover:bg-lego-blue/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {auctionMaxBid.isPending ? "Berechne Maximalgebot..." : "Maximalgebot berechnen"}
        </button>

        {!setNumber && (
          <p className="text-text-muted text-xs mt-3">
            Erst ein Set oder einen Link laden, dann kann das Maximalgebot berechnet werden.
          </p>
        )}
        {auctionMaxBid.isError && (
          <p className="text-no-go text-sm mt-3">Fehler: {auctionMaxBid.error.message}</p>
        )}

        {auctionResult && (
          <div className="mt-4 border border-border rounded-xl overflow-hidden">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-px bg-border">
              <div className="bg-bg-primary p-4">
                <div className="text-text-muted text-xs uppercase">Maximalgebot</div>
                <div className="text-go-star text-2xl font-bold font-[family-name:var(--font-mono)]">
                  {formatMoney(auctionResult.recommended_max_bid, 0)}
                </div>
              </div>
              <div className="bg-bg-primary p-4">
                <div className="text-text-muted text-xs uppercase">Break-even</div>
                <div className="text-text-primary text-xl font-bold font-[family-name:var(--font-mono)]">
                  {formatMoney(auctionResult.break_even_bid, 0)}
                </div>
              </div>
              <div className="bg-bg-primary p-4">
                <div className="text-text-muted text-xs uppercase">Gebots-Luft</div>
                <div
                  className={`text-xl font-bold font-[family-name:var(--font-mono)] ${
                    auctionResult.current_bid_gap >= 0 ? "text-go" : "text-no-go"
                  }`}
                >
                  {formatMoney(auctionResult.current_bid_gap, 0)}
                </div>
              </div>
              <div className="bg-bg-primary p-4">
                <div className="text-text-muted text-xs uppercase">Ziel-ROI</div>
                <div className="text-text-primary text-xl font-bold font-[family-name:var(--font-mono)]">
                  {auctionResult.target_roi_percent.toFixed(1)}%
                </div>
              </div>
            </div>

            <div className="bg-bg-card p-4">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
                <span className="text-text-primary font-medium">{auctionResult.current_bid_recommendation}</span>
                <span className="text-text-muted">
                  Aktuell: <span className="font-[family-name:var(--font-mono)] text-text-primary">{formatMoney(auctionResult.current_bid, 0)}</span>
                </span>
                <span className="text-text-muted">
                  Markt: <span className="font-[family-name:var(--font-mono)] text-text-primary">{formatMoney(auctionResult.reference_price, 0)}</span>
                </span>
                <span className="text-text-muted">
                  All-in bei Max: <span className="font-[family-name:var(--font-mono)] text-text-primary">{formatMoney(auctionResult.total_purchase_cost_at_recommended_bid, 0)}</span>
                </span>
              </div>

              <div className="grid md:grid-cols-2 gap-3 mt-4">
                <div className="bg-bg-hover rounded-lg p-3">
                  <div className="text-text-muted text-xs uppercase">Bei Maximalgebot</div>
                  <div className="flex items-center justify-between mt-2 text-sm">
                    <span className="text-text-muted">Käuferschutz</span>
                    <span className="font-[family-name:var(--font-mono)] text-text-primary">
                      {formatMoney(auctionResult.buyer_fee_at_recommended_bid)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between mt-2 text-sm">
                    <span className="text-text-muted">Erwarteter Gewinn</span>
                    <span className="font-[family-name:var(--font-mono)] text-go">
                      {formatMoney(auctionResult.expected_profit_at_recommended_bid)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between mt-2 text-sm">
                    <span className="text-text-muted">Erwarteter ROI</span>
                    <span className="font-[family-name:var(--font-mono)] text-go">
                      {auctionResult.expected_roi_at_recommended_bid.toFixed(1)}%
                    </span>
                  </div>
                </div>
                <div className="bg-bg-hover rounded-lg p-3">
                  <div className="text-text-muted text-xs uppercase">Beim aktuellen Gebot</div>
                  <div className="flex items-center justify-between mt-2 text-sm">
                    <span className="text-text-muted">Käuferschutz</span>
                    <span className="font-[family-name:var(--font-mono)] text-text-primary">
                      {formatMoney(auctionResult.buyer_fee_at_current_bid)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between mt-2 text-sm">
                    <span className="text-text-muted">Erwarteter Gewinn</span>
                    <span
                      className={`font-[family-name:var(--font-mono)] ${
                        auctionResult.expected_profit_at_current_bid >= 0 ? "text-go" : "text-no-go"
                      }`}
                    >
                      {formatMoney(auctionResult.expected_profit_at_current_bid)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between mt-2 text-sm">
                    <span className="text-text-muted">Erwarteter ROI</span>
                    <span
                      className={`font-[family-name:var(--font-mono)] ${
                        auctionResult.expected_roi_at_current_bid >= 0 ? "text-go" : "text-no-go"
                      }`}
                    >
                      {auctionResult.expected_roi_at_current_bid.toFixed(1)}%
                    </span>
                  </div>
                </div>
              </div>

              {auctionResult.warnings?.length > 0 && (
                <div className="mt-4 space-y-2">
                  {auctionResult.warnings.slice(0, 3).map((warning) => (
                    <p key={warning} className="text-check text-sm">
                      {warning}
                    </p>
                  ))}
                </div>
              )}

              <div className="flex flex-wrap items-center gap-3 mt-4">
                <button
                  type="button"
                  onClick={handleAddAuctionWatch}
                  disabled={addAuctionWatch.isPending || !sourceUrl}
                  className="bg-lego-yellow text-black text-sm font-bold px-4 py-2 rounded-lg hover:bg-lego-yellow/90 transition-colors disabled:opacity-50"
                >
                  {addAuctionWatch.isPending ? "Speichere..." : "Zur Auktions-Watchlist"}
                </button>
                {!sourceUrl && (
                  <span className="text-text-muted text-xs">
                    Für die Watchlist brauchen wir die Lot-URL.
                  </span>
                )}
                {addAuctionWatch.isSuccess && (
                  <span className="text-go text-sm">Lot zur Watchlist hinzugefügt.</span>
                )}
                {addAuctionWatch.isError && (
                  <span className="text-no-go text-sm">Fehler: {addAuctionWatch.error.message}</span>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

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
                  {multiResult.summary?.total_market_value != null ? formatMoney(multiResult.summary.total_market_value, 0) : "--"}
                </div>
              </div>
              <div className="bg-bg-card p-4 text-center">
                <div className="text-text-muted text-xs uppercase">Gesamt-ROI</div>
                <div className={`font-[family-name:var(--font-mono)] text-xl font-bold ${(multiResult.summary?.combined_roi ?? 0) >= 20 ? "text-go-star" : (multiResult.summary?.combined_roi ?? 0) >= 0 ? "text-check" : "text-no-go"}`}>
                  {multiResult.summary?.combined_roi?.toFixed(1) ?? "--"}%
                </div>
              </div>
              <div className="bg-bg-card p-4 text-center">
                <div className="text-text-muted text-xs uppercase">Kaufpreis</div>
                <div className="font-[family-name:var(--font-mono)] text-xl font-bold text-text-primary">
                  {multiResult.summary?.total_investment != null ? formatMoney(multiResult.summary.total_investment, 0) : `${konvolutPrice}${EURO}`}
                </div>
              </div>
              <div className="bg-bg-card p-4 text-center">
                <div className="text-text-muted text-xs uppercase">Empfehlung</div>
                <div className="mt-1">
                  {multiResult.summary?.recommendation ? (
                    <VerdictBadge verdict={multiResult.summary.recommendation} size="md" />
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
                      <span className="text-text-primary font-[family-name:var(--font-mono)]">{formatMoney(price)}</span>
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
                          <span className="text-text-primary font-[family-name:var(--font-mono)]">{formatMoney(allocatedPrice)}</span>
                        </div>
                      )}
                      <div className="flex justify-between">
                        <span className="text-text-muted">Marktwert</span>
                        <span className="text-lego-yellow font-[family-name:var(--font-mono)]">{item.market_price != null ? formatMoney(item.market_price) : "--"}</span>
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
                          {item.net_profit != null ? `${item.net_profit > 0 ? "+" : ""}${formatMoney(item.net_profit)}` : "--"}
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
              { label: "Gewinn", value: `${result.net_profit > 0 ? "+" : ""}${formatMoney(result.net_profit, 0)}`, color: result.net_profit > 0 ? "text-go" : "text-no-go" },
              { label: "Risiko", value: `${result.risk_score}/10`, color: result.risk_score <= 5 ? "text-go" : result.risk_score <= 7 ? "text-check" : "text-no-go" },
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
                  <span className="text-text-primary font-[family-name:var(--font-mono)]">{formatMoney(price)}</span>
                </div>
              ))}
              <div className="flex justify-between text-sm pt-2 border-t border-border/50">
                <span className="text-text-secondary font-medium">Markt-Konsens ({result.num_sources} Quellen)</span>
                <span className="text-lego-yellow font-[family-name:var(--font-mono)] font-bold">{formatMoney(result.market_price)}</span>
              </div>
            </div>
          </div>

          <div className="p-4 bg-bg-card border-t border-border">
            <h3 className="text-text-secondary text-xs uppercase tracking-wider mb-3">Bewertungsbasis</h3>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-text-muted">ROI-Referenz</span>
                <span className="text-text-primary font-medium">{referenceLabel}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Referenzpreis</span>
                <span className="text-lego-yellow font-[family-name:var(--font-mono)] font-bold">
                  {formatMoney(result.reference_price ?? result.market_price)}
                </span>
              </div>
              {result.still_in_retail && (
                <div className="flex justify-between">
                  <span className="text-text-muted">Retail-Status</span>
                  <span className="text-check font-medium">Noch regulär im Handel</span>
                </div>
              )}
              {result.eol_status && (
                <div className="flex justify-between">
                  <span className="text-text-muted">EOL-Status</span>
                  <span className="text-text-primary font-[family-name:var(--font-mono)]">{result.eol_status}</span>
                </div>
              )}
            </div>
          </div>

          {/* ROI Breakdown */}
          <div className="p-4 bg-bg-card border-t border-border">
            <h3 className="text-text-secondary text-xs uppercase tracking-wider mb-3">ROI Berechnung</h3>
            <div className="space-y-1.5 text-sm">
              <div className="flex justify-between">
                <span className="text-text-muted">Kaufpreis</span>
                <span className="text-text-primary font-[family-name:var(--font-mono)]">{formatMoney(result.offer_price)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Gesamtkosten (Kauf)</span>
                <span className="text-text-primary font-[family-name:var(--font-mono)]">{formatMoney(result.total_purchase_cost)}</span>
              </div>
              {result.calibration_roi_delta != null && (
                <div className="flex justify-between">
                  <span className="text-text-muted">Lern-Korrektur</span>
                  <span className={`font-[family-name:var(--font-mono)] ${result.calibration_roi_delta >= 0 ? "text-go" : "text-no-go"}`}>
                    {result.calibration_roi_delta > 0 ? "+" : ""}
                    {result.calibration_roi_delta.toFixed(1)}pp
                  </span>
                </div>
              )}
              {result.calibrated_roi_percent != null && result.calibration_roi_delta != null && (
                <div className="flex justify-between">
                  <span className="text-text-muted">Kalibrierter ROI</span>
                  <span className="text-lego-yellow font-[family-name:var(--font-mono)] font-bold">
                    {result.calibrated_roi_percent.toFixed(1)}%
                  </span>
                </div>
              )}
              <div className="flex justify-between">
                <span className="text-text-muted">{"Verkaufskosten (eBay Geb\u00fchren)"}</span>
                <span className="text-no-go font-[family-name:var(--font-mono)]">-{formatMoney(result.total_selling_costs)}</span>
              </div>
              <div className="flex justify-between pt-2 border-t border-border/50 font-medium">
                <span className="text-text-secondary">Netto-Gewinn</span>
                <span className={`font-[family-name:var(--font-mono)] font-bold ${result.net_profit > 0 ? "text-go-star" : "text-no-go"}`}>
                  {result.net_profit > 0 ? "+" : ""}{formatMoney(result.net_profit)}
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
              Gekauft - ins Inventar aufnehmen
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
              {showSellerCheck
                ? `${ICON_DOWN} Schließen`
                : `${ICON_RIGHT} Weitere Angebote des Verkäufers prüfen`}
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
                            setSelectedHistoryItem(null);
                            setSetNumber(listing.set_number);
                            if (listing.price) setOfferPrice(String(listing.price));
                            setSourceUrl(listing.url);
                            setSourcePlatform("KLEINANZEIGEN");
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
                            {listing.price ? `${listing.price}${EURO}` : "\u2014"}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>

                  {sellerCheck.data.total_value > 0 && (
                    <div className="flex justify-between mt-3 pt-3 border-t border-border/50">
                      <span className="text-text-muted text-sm">Gesamtwert LEGO</span>
                      <span className="text-lego-yellow font-[family-name:var(--font-mono)] font-bold">
                        {formatMoney(sellerCheck.data.total_value, 0)}
                      </span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {showLiveScanner && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="bg-bg-card border border-border rounded-xl p-6 w-full max-w-lg mx-4">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-text-primary text-lg font-bold">Live-Kamera-Scan</h2>
                <p className="text-text-muted text-xs mt-1">
                  Halte den Barcode ruhig ins Bild. Der Check startet automatisch.
                </p>
              </div>
              <button
                type="button"
                onClick={() => stopLiveScanner()}
                className="text-text-muted hover:text-text-primary transition-colors px-2 py-1"
              >
                {ICON_CLOSE}
              </button>
            </div>

            <div className="rounded-xl overflow-hidden border border-border bg-black aspect-video flex items-center justify-center">
              {cameraError ? (
                <div className="px-6 text-center">
                  <p className="text-no-go text-sm">{cameraError}</p>
                  <button
                    type="button"
                    onClick={startLiveScanner}
                    className="mt-4 bg-lego-yellow text-black font-bold px-4 py-2 rounded-lg hover:bg-lego-yellow/90 transition-colors"
                  >
                    Erneut versuchen
                  </button>
                </div>
              ) : (
                <video
                  ref={liveVideoRef}
                  autoPlay
                  playsInline
                  muted
                  className="w-full h-full object-cover"
                />
              )}
            </div>

            {!cameraError && (
              <div className="flex items-center justify-between mt-4 text-xs text-text-muted">
                <span>Bevorzugt Rückkamera und sichtbarer EAN/Barcode.</span>
                <span>{isScanningBarcode ? "Suche..." : "Bereit"}</span>
              </div>
            )}
          </div>
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
                Kaufpreis: {formatMoney(result.offer_price)} + {shipping ? formatMoney(shipping) : formatMoney(0)} Versand
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
                      {formatMoney(multiResult.price_allocation?.[r.set_number] ?? r.offer_price)}
                    </span>
                  </div>
                ))}
              </div>
              <div className="flex justify-between pt-2 border-t border-border/50 font-medium text-sm">
                <span className="text-text-secondary">Gesamt</span>
                <span className="text-lego-yellow font-[family-name:var(--font-mono)] font-bold">
                  {konvolutPrice ? formatMoney(konvolutPrice) : formatMoney(0)}
                </span>
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
