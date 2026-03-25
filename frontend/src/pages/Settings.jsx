import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

const FIELD_CONFIG = [
  {
    category: "telegram",
    title: "Telegram Notifications",
    description: "Diese Werte werden direkt fuer Testnachrichten und Alerts verwendet.",
    enabled: true,
    fields: [
      { key: "telegram_bot_token", label: "Bot Token", type: "password" },
      { key: "telegram_chat_id", label: "Chat ID", type: "text" },
      { key: "telegram_alert_on_go_only", label: "Nur GO Alerts", type: "checkbox" },
    ],
  },
  {
    category: "ebay",
    title: "eBay API",
    description: "Fuer automatisches Einstellen - kommt spaeter.",
    enabled: false,
    fields: [
      { key: "ebay_api_key", label: "API Key", type: "password" },
      { key: "ebay_api_secret", label: "API Secret", type: "password" },
    ],
  },
  {
    category: "kleinanzeigen",
    title: "Kleinanzeigen",
    description: "Fuer automatisches Einstellen - kommt spaeter.",
    enabled: false,
    fields: [
      { key: "kleinanzeigen_email", label: "E-Mail", type: "text" },
      { key: "kleinanzeigen_password", label: "Passwort", type: "password" },
    ],
  },
];

function isTruthy(value) {
  if (typeof value === "boolean") return value;
  if (value == null) return false;
  return String(value).toLowerCase() === "true";
}

function SettingsField({ field, sectionEnabled, values, dirtyValues, onChange }) {
  const currentValue =
    dirtyValues[field.key] !== undefined ? dirtyValues[field.key] : values[field.key] ?? "";

  if (field.type === "checkbox") {
    return (
      <label className="flex items-center gap-3">
        <input
          type="checkbox"
          disabled={!sectionEnabled}
          checked={isTruthy(currentValue)}
          onChange={(event) => onChange(field.key, String(event.target.checked))}
          className="h-4 w-4 accent-lego-yellow"
        />
        <span className={`text-sm ${sectionEnabled ? "text-text-secondary" : "text-text-muted"}`}>
          {field.label}
        </span>
      </label>
    );
  }

  return (
    <div>
      <label className="block text-sm font-medium text-text-secondary mb-1.5">{field.label}</label>
      <input
        type={field.type}
        disabled={!sectionEnabled}
        value={currentValue}
        onChange={(event) => onChange(field.key, event.target.value)}
        placeholder={!sectionEnabled ? "Deaktiviert" : ""}
        className={`w-full px-3 py-2 rounded-lg border text-sm transition-colors
          bg-bg-primary border-border text-text-primary placeholder-text-muted
          focus:outline-none focus:ring-2 focus:ring-lego-yellow/50 focus:border-lego-yellow
          ${!sectionEnabled ? "opacity-50 cursor-not-allowed" : ""}`}
      />
    </div>
  );
}

function SettingsCard({ section, values, dirtyValues, onChange }) {
  return (
    <div className="bg-bg-card rounded-xl border border-border p-6">
      <div className="flex items-center gap-3 mb-4">
        <h2 className="text-lg font-semibold text-text-primary">{section.title}</h2>
        {!section.enabled && (
          <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-lego-yellow/20 text-lego-yellow border border-lego-yellow/30">
            Bald verfuegbar
          </span>
        )}
      </div>
      {section.description && <p className="text-text-muted text-sm mb-4">{section.description}</p>}
      <div className="space-y-4">
        {section.fields.map((field) => (
          <SettingsField
            key={field.key}
            field={field}
            sectionEnabled={section.enabled}
            values={values}
            dirtyValues={dirtyValues}
            onChange={onChange}
          />
        ))}
      </div>
    </div>
  );
}

export default function Settings() {
  const queryClient = useQueryClient();
  const [dirtyValues, setDirtyValues] = useState({});
  const [telegramStatus, setTelegramStatus] = useState(null);

  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.listSettings(),
    staleTime: 60_000,
  });

  const values = {};
  if (settings) {
    for (const setting of settings) {
      values[setting.key] = setting.value;
    }
  }

  const saveMutation = useMutation({
    mutationFn: (updates) => api.updateSettings(updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      setDirtyValues({});
    },
  });

  const telegramMutation = useMutation({
    mutationFn: () => api.testTelegram(),
    onSuccess: () => {
      setTelegramStatus({ type: "success", message: "Testnachricht gesendet." });
      setTimeout(() => setTelegramStatus(null), 4000);
    },
    onError: (error) => {
      setTelegramStatus({ type: "error", message: error.message || "Fehler beim Senden" });
      setTimeout(() => setTelegramStatus(null), 4000);
    },
  });

  const hasDirty = Object.keys(dirtyValues).length > 0;

  const handleChange = (key, value) => {
    setDirtyValues((prev) => ({ ...prev, [key]: value }));
  };

  const handleSave = () => {
    const updates = Object.entries(dirtyValues)
      .filter(([key, value]) => value !== values[key])
      .map(([key, value]) => ({ key, value }));

    if (updates.length > 0) {
      saveMutation.mutate(updates);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="h-8 w-8 border-2 border-lego-yellow border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Einstellungen</h1>
        <p className="text-text-muted text-sm mt-1">
          Laufende Alerts sind DB-basiert. Scheduler und Infrastruktur bleiben deploy-/serverseitig konfiguriert.
        </p>
      </div>

      <div className="bg-bg-card border border-border rounded-xl p-4 mb-6 text-sm text-text-muted">
        Fuer Produktions-Takte, Domains und Reverse Proxy gilt die Server-Konfiguration. Diese Seite steuert bewusst nur
        Laufzeitwerte, die die App direkt verwenden kann.
      </div>

      <div className="space-y-6">
        {FIELD_CONFIG.map((section) => (
          <div key={section.category}>
            <SettingsCard
              section={section}
              values={values}
              dirtyValues={dirtyValues}
              onChange={handleChange}
            />

            {section.category === "telegram" && (
              <div className="mt-3 flex items-center gap-3">
                <button
                  onClick={() => telegramMutation.mutate()}
                  disabled={telegramMutation.isPending}
                  className="px-4 py-2 text-sm font-medium rounded-lg bg-bg-card border border-border
                    text-text-secondary hover:text-lego-yellow hover:border-lego-yellow/50
                    transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {telegramMutation.isPending ? "Sende..." : "Test senden"}
                </button>
                {telegramStatus && (
                  <span className={`text-sm font-medium ${telegramStatus.type === "success" ? "text-green-400" : "text-red-400"}`}>
                    {telegramStatus.message}
                  </span>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="mt-8 flex items-center gap-4">
        <button
          onClick={handleSave}
          disabled={!hasDirty || saveMutation.isPending}
          className={`px-6 py-2.5 rounded-lg text-sm font-semibold transition-all
            ${hasDirty
              ? "bg-lego-yellow text-gray-900 hover:bg-lego-yellow/90 shadow-lg shadow-lego-yellow/20"
              : "bg-bg-hover text-text-muted cursor-not-allowed"}
            disabled:opacity-50 disabled:cursor-not-allowed`}
        >
          {saveMutation.isPending ? "Speichere..." : "Speichern"}
        </button>
        {saveMutation.isSuccess && <span className="text-sm text-green-400 font-medium">Gespeichert.</span>}
        {saveMutation.isError && <span className="text-sm text-red-400 font-medium">Fehler: {saveMutation.error?.message}</span>}
      </div>
    </div>
  );
}
