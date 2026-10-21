import { NavLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

const tabs = [
  { to: "/", label: "Feed", icon: "\u{1F4E1}" },
  { to: "/auctions", label: "Auktionen", icon: "\u{1F528}" },
  { to: "/checker", label: "Check", icon: "\u{1F50D}" },
  { to: "/inventar", label: "Inventar", icon: "\u{1F4E6}" },
  { to: "/anpassungen", label: "Anpassungen", short: "Preise", icon: "\u{1F3F7}\u{FE0F}", badge: "dueAdjustments" },
  { to: "/history", label: "Verkäufe", icon: "\u{1F4CA}" },
];

// Zahl offener, nicht zurückgestellter Preisvorschläge plus verkaufter Posten
// mit noch laufender Anzeige (GET /api/listings/due). Der Tages-Check läuft
// einmal am Tag, fünf Minuten Abfrageabstand reichen.
function useDueCount() {
  const { data } = useQuery({
    queryKey: ["dueAdjustments"],
    queryFn: api.dueAdjustments,
    refetchInterval: 5 * 60_000,
    staleTime: 60_000,
  });
  return data?.due_count ?? 0;
}

function Badge({ count }) {
  if (!count) return null;
  return (
    <span
      className="ml-1 inline-flex min-w-[1.25rem] h-5 items-center justify-center rounded-full bg-lego-yellow px-1 text-[11px] font-bold text-black"
      aria-label={`${count} fällig`}
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}

export default function TabNav({ mobile = false }) {
  const dueCount = useDueCount();
  const badgeFor = (tab) => (tab.badge === "dueAdjustments" ? dueCount : 0);

  if (mobile) {
    return (
      <div className="flex justify-around py-2">
        {tabs.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.to === "/"}
            className={({ isActive }) =>
              `relative flex flex-col items-center gap-0.5 px-2 py-1 text-xs transition-colors ${
                isActive
                  ? "text-lego-yellow"
                  : "text-text-muted hover:text-text-secondary"
              }`
            }
          >
            <span className="text-lg">{tab.icon}</span>
            <span>{tab.short || tab.label}</span>
            {badgeFor(tab) > 0 && (
              <span className="absolute -top-0.5 right-0">
                <Badge count={badgeFor(tab)} />
              </span>
            )}
          </NavLink>
        ))}
      </div>
    );
  }

  return (
    <div className="flex gap-1">
      {tabs.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.to === "/"}
          className={({ isActive }) =>
            `px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              isActive
                ? "bg-bg-hover text-lego-yellow"
                : "text-text-secondary hover:text-text-primary hover:bg-bg-hover/50"
            }`
          }
        >
          {tab.icon} {tab.label}
          <Badge count={badgeFor(tab)} />
        </NavLink>
      ))}
    </div>
  );
}
