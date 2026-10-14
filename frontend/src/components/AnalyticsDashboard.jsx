import { useState } from "react";

import { getErrorMessage, getStats } from "../api";

export default function AnalyticsDashboard() {
  const [code, setCode] = useState("");
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    setStats(null);
    setLoading(true);
    try {
      setStats(await getStats(code.trim()));
    } catch (err) {
      setError(getErrorMessage(err, "Could not load stats for that code"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-md mx-auto mt-6 p-6 bg-white rounded shadow">
      <h2 className="text-xl font-bold mb-4">Analytics Dashboard</h2>
      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="Enter a short code..."
          required
          className="flex-1 p-2 border rounded"
        />
        <button
          type="submit"
          className="bg-gray-800 text-white px-4 rounded disabled:opacity-50"
          disabled={loading}
        >
          {loading ? "Loading..." : "Load"}
        </button>
      </form>

      {error && <p className="mt-4 text-red-600">{error}</p>}

      {stats && (
        <div className="mt-4">
          <p className="text-sm text-gray-600">Original URL</p>
          <a href={stats.original_url} className="text-blue-600 break-all">
            {stats.original_url}
          </a>

          <div className="mt-3 grid grid-cols-2 gap-3">
            <div className="p-3 bg-gray-50 rounded">
              <p className="text-xs text-gray-500">Total clicks</p>
              <p className="text-2xl font-bold">{stats.total_clicks}</p>
            </div>
            <div className="p-3 bg-gray-50 rounded">
              <p className="text-xs text-gray-500">Created</p>
              <p className="text-sm">{new Date(stats.created_at).toLocaleString()}</p>
            </div>
          </div>

          {stats.recent_clicks.length > 0 && (
            <table className="w-full mt-4 text-sm">
              <thead>
                <tr className="text-left text-gray-500">
                  <th className="pb-1">When</th>
                  <th className="pb-1">IP</th>
                  <th className="pb-1">User agent</th>
                </tr>
              </thead>
              <tbody>
                {stats.recent_clicks.map((click) => (
                  <tr key={click.id} className="border-t">
                    <td className="py-1 pr-2 whitespace-nowrap">
                      {new Date(click.clicked_at).toLocaleString()}
                    </td>
                    <td className="py-1 pr-2">{click.ip_address || "-"}</td>
                    <td className="py-1 truncate max-w-[10rem]" title={click.user_agent || ""}>
                      {click.user_agent || "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
