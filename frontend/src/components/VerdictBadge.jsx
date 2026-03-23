const verdictConfig = {
  GO_STAR: { label: "GO \u2B50", bg: "bg-go-star", text: "text-black", pulse: true },
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
