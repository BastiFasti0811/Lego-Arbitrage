export default function StatCard({ label, value, sub, color = "text-text-primary" }) {
  return (
    <div className="bg-bg-card border border-border rounded-xl p-4">
      <div className="text-text-muted text-xs uppercase tracking-wider mb-1">{label}</div>
      <div className={`font-[family-name:var(--font-mono)] text-xl font-bold ${color}`}>{value}</div>
      {sub && <div className="text-text-secondary text-xs mt-1">{sub}</div>}
    </div>
  );
}
