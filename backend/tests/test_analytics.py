import pytest


@pytest.mark.asyncio
async def test_stats_reports_clicks(client):
    res = await client.post(
        "/api/v1/shorten",
        json={"url": "https://example.com", "redirect_type": 302},
    )
    code = res.json()["short_code"]

    await client.get(f"/{code}", follow_redirects=False)
    await client.get(f"/{code}", follow_redirects=False)

    stats = await client.get(f"/api/v1/stats/{code}")
    assert stats.status_code == 200
    body = stats.json()
    assert body["short_code"] == code
    assert body["original_url"] == "https://example.com"
    assert body["total_clicks"] == 2
    assert len(body["recent_clicks"]) == 2


@pytest.mark.asyncio
async def test_click_event_captures_metadata(client):
    res = await client.post("/api/v1/shorten", json={"url": "https://example.com"})
    code = res.json()["short_code"]

    await client.get(
        f"/{code}",
        follow_redirects=False,
        headers={
            "user-agent": "pytest-agent/1.0",
            "referer": "https://ref.example.com",
            "x-forwarded-for": "203.0.113.7, 10.0.0.1",
        },
    )

    stats = await client.get(f"/api/v1/stats/{code}")
    click = stats.json()["recent_clicks"][0]
    assert click["user_agent"] == "pytest-agent/1.0"
    assert click["referrer"] == "https://ref.example.com"
    assert click["ip_address"] == "203.0.113.7"


@pytest.mark.asyncio
async def test_stats_returns_404_for_unknown_code(client):
    res = await client.get("/api/v1/stats/unknown")
    assert res.status_code == 404
