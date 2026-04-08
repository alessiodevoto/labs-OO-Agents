export function formatRelativeTime(timestamp: string | number): string {
  const date =
    typeof timestamp === "number" || !isNaN(Number(timestamp))
      ? Number(timestamp) > 1e9
        ? new Date(Number(timestamp) * 1000)
        : new Date(Number(timestamp))
      : new Date(timestamp);

  if (isNaN(date.getTime())) return String(timestamp);

  const diffMs = Date.now() - date.getTime();
  const diffSecs = Math.floor(diffMs / 1000);
  const diffMins = Math.floor(diffSecs / 60);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffSecs < 60) return "just now";
  if (diffMins < 60) return `${diffMins} ${diffMins === 1 ? "minute" : "minutes"} ago`;
  if (diffHours < 24) return `${diffHours} ${diffHours === 1 ? "hour" : "hours"} ago`;
  if (diffDays < 30) return `${diffDays} ${diffDays === 1 ? "day" : "days"} ago`;

  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
