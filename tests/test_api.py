"""Tests for the REST API endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport
from nassimdl.api.server import app, lifespan


@pytest.mark.asyncio
async def test_api_config_and_downloads():
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Test config GET
            res = await client.get("/api/config")
            assert res.status_code == 200
            data = res.json()
            assert "max_concurrent" in data
            assert "default_segments" in data

            # Test config POST
            res = await client.post("/api/config", json={"max_concurrent": 5})
            assert res.status_code == 200
            assert res.json() == {"success": True}

            # Verify updated config
            res = await client.get("/api/config")
            assert res.json()["max_concurrent"] == 5

            # Test downloads GET
            res = await client.get("/api/downloads")
            assert res.status_code == 200
            assert isinstance(res.json(), list)
