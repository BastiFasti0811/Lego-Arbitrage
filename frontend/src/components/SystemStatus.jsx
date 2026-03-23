import { useQuery } from "@tanstack/react-query";

export default function SystemStatus() {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: () => fetch("/health").then((r) => r.json()),
    refetchInterval: 60_000,
  });

  return (
    <div className="flex items-center gap-2 text-xs">
      <div
        className={`w-2 h-2 rounded-full ${
          data?.status === "healthy" ? "bg-go-star animate-pulse" : isError ? "bg-no-go" : "bg-check"
        }`}
      />
      <span className="text-text-muted font-[family-name:var(--font-mono)]">
        {data?.version || "..."}
      </span>
    </div>
  );
}
