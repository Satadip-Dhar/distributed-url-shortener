import axios from "axios";

const api = axios.create({ baseURL: "/api/v1" });

export async function createShortUrl(url, redirectType = 302) {
  const res = await api.post("/shorten", { url, redirect_type: redirectType });
  return res.data;
}

export async function getStats(shortCode) {
  const res = await api.get(`/stats/${encodeURIComponent(shortCode)}`);
  return res.data;
}

/**
 * Turn an axios error into a readable message.
 * FastAPI validation errors (422) carry `detail` as an array of objects,
 * which React cannot render directly — flatten it to a single string.
 */
export function getErrorMessage(err, fallback = "Something went wrong") {
  const detail = err.response?.data?.detail;
  if (Array.isArray(detail)) {
    return (
      detail
        .map((item) => (typeof item === "string" ? item : item?.msg))
        .filter(Boolean)
        .join(", ") || fallback
    );
  }
  return detail || fallback;
}

export default api;
