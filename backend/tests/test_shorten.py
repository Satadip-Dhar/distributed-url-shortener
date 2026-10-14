import pytest
from app.services.shortener import decode_base62, encode_base62


class TestBase62:
    def test_encode_zero(self):
        assert encode_base62(0) == "0"

    def test_encode_basic(self):
        assert encode_base62(1) == "1"
        assert encode_base62(61) == "Z"
        assert encode_base62(62) == "10"

    def test_roundtrip(self):
        for num in [0, 1, 42, 100, 999999, 3521614606208]:
            assert decode_base62(encode_base62(num)) == num

    def test_short_code_length(self):
        """62^7 = 3.5 trillion — verify codes stay <= 7 chars."""
        assert len(encode_base62(1_000_000_000)) <= 7


class TestShortenEndpoint:
    @pytest.mark.asyncio
    async def test_shorten_returns_201(self, client):
        response = await client.post("/api/v1/shorten", json={"url": "https://example.com"})
        assert response.status_code == 201
        assert "short_url" in response.json()

    @pytest.mark.asyncio
    async def test_redirect_returns_302(self, client):
        res = await client.post("/api/v1/shorten", json={"url": "https://example.com"})
        code = res.json()["short_code"]
        res = await client.get(f"/{code}", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_invalid_code_returns_404(self, client):
        res = await client.get("/nonexistent", follow_redirects=False)
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_url_rejected(self, client):
        res = await client.post("/api/v1/shorten", json={"url": "not-a-url"})
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_redirect_type_rejected(self, client):
        res = await client.post(
            "/api/v1/shorten",
            json={"url": "https://example.com", "redirect_type": 307},
        )
        assert res.status_code == 422
