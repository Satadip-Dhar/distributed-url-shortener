import { useState } from "react";

import { createShortUrl, getErrorMessage } from "../api";

export default function ShortenForm() {
  const [url, setUrl] = useState("");
  const [shortUrl, setShortUrl] = useState(null);
  const [error, setError] = useState(null);
  const [redirectType, setRedirectType] = useState(302);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    try {
      const data = await createShortUrl(url, redirectType);
      setShortUrl(data.short_url);
    } catch (err) {
      setError(getErrorMessage(err));
    }
  };

  return (
    <div className="max-w-md mx-auto mt-10 p-6 bg-white rounded shadow">
      <h1 className="text-2xl font-bold mb-4">URL Shortener</h1>
      <form onSubmit={handleSubmit}>
        <input
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Paste your long URL here..."
          required
          className="w-full p-2 border rounded mb-3"
        />
        <div className="mb-3">
          <label className="mr-4">
            <input type="radio" value={302}
              checked={redirectType === 302}
              onChange={() => setRedirectType(302)}
            /> 302 (Trackable)
          </label>
          <label>
            <input type="radio" value={301}
              checked={redirectType === 301}
              onChange={() => setRedirectType(301)}
            /> 301 (Cached by browser)
          </label>
        </div>
        <button type="submit" className="w-full bg-blue-600 text-white p-2 rounded">
          Shorten
        </button>
      </form>
      {shortUrl && (
        <div className="mt-4 p-3 bg-green-50 rounded">
          <p className="text-sm text-gray-600">Your short URL:</p>
          <a href={shortUrl} className="text-blue-600 font-mono break-all">{shortUrl}</a>
        </div>
      )}
      {error && <p className="mt-4 text-red-600">{error}</p>}
    </div>
  );
}
