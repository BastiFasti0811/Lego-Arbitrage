import { NavLink } from "react-router-dom";

const tabs = [
  { to: "/", label: "Feed", icon: "\u{1F4E1}" },
  { to: "/auctions", label: "Auktionen", icon: "\u{1F528}" },
  { to: "/checker", label: "Check", icon: "\u{1F50D}" },
  { to: "/inventar", label: "Inventar", icon: "\u{1F4E6}" },
  { to: "/history", label: "Verkäufe", icon: "\u{1F4CA}" },
];

export default function TabNav({ mobile = false }) {
  if (mobile) {
    return (
      <div className="flex justify-around py-2">
        {tabs.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.to === "/"}
            className={({ isActive }) =>
              `flex flex-col items-center gap-0.5 px-3 py-1 text-xs transition-colors ${
                isActive
                  ? "text-lego-yellow"
                  : "text-text-muted hover:text-text-secondary"
              }`
            }
          >
            <span className="text-lg">{tab.icon}</span>
            <span>{tab.label}</span>
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
        </NavLink>
      ))}
    </div>
  );
}
