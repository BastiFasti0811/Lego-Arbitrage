import { create } from "zustand";

export const useAppStore = create((set) => ({
  // Filters for Live Feed
  feedFilters: {
    verdict: null,
    minRoi: 0,
    maxRisk: 10,
    theme: null,
  },
  setFeedFilters: (filters) =>
    set((state) => ({ feedFilters: { ...state.feedFilters, ...filters } })),

  // Last analysis result (for Deal Checker → Inventar flow)
  lastAnalysis: null,
  setLastAnalysis: (analysis) => set({ lastAnalysis: analysis }),
}));
