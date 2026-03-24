import { Outlet, NavLink } from "react-router-dom";
import TabNav from "../components/TabNav";
import SystemStatus from "../components/SystemStatus";

function SettingsGearIcon() {
  return (
    <NavLink
      to="/settings"
      className={({ isActive }) =>
        `p-2 rounded-lg transition-colors ${
          isActive
            ? "text-lego-yellow bg-bg-hover"
            : "text-text-muted hover:text-text-primary hover:bg-bg-hover/50"
        }`
      }
      title="Einstellungen"
    >
      <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>
        <circle cx="12" cy="12" r="3"/>
      </svg>
    </NavLink>
  );
}

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
          <div className="flex items-center gap-2">
            <SystemStatus />
            <SettingsGearIcon />
          </div>
        </div>
      </header>

      {/* Mobile: top header with settings gear */}
      <div className="md:hidden sticky top-0 z-50 bg-bg-primary/95 backdrop-blur border-b border-border">
        <div className="flex items-center justify-between px-4 py-2">
          <div className="flex items-center gap-2">
            <span className="text-lego-yellow font-bold text-lg">LEGO</span>
            <span className="text-text-secondary text-xs">Arbitrage</span>
          </div>
          <SettingsGearIcon />
        </div>
      </div>

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
