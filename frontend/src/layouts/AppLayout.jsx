import { Outlet } from "react-router-dom";
import TabNav from "../components/TabNav";
import SystemStatus from "../components/SystemStatus";

export default function AppLayout() {
  return (
    <div className="min-h-screen bg-bg-primary">
      {/* Desktop: top nav */}
      <header className="hidden md:block border-b border-border sticky top-0 z-50 bg-bg-primary/95 backdrop-blur">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-lego-yellow font-bold text-xl font-[family-name:var(--font-mono)]">
              LEGO
            </span>
            <span className="text-text-secondary text-sm">Arbitrage System</span>
          </div>
          <TabNav />
          <SystemStatus />
        </div>
      </header>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 py-6 pb-24 md:pb-6">
        <Outlet />
      </main>

      {/* Mobile: bottom tab bar */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 bg-bg-card border-t border-border z-50">
        <TabNav mobile />
      </nav>
    </div>
  );
}
