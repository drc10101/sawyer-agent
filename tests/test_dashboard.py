"""Tests for Sawyer Dashboard — FastAPI web interface."""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sawyer.auth.api import SawyerAuth
from sawyer.dashboard.server import create_app
from sawyer.storage.database import SawyerStorage
from sawyer.token.accounting import TokenAccountant
from sawyer.token.budget import SubscriptionTier


@pytest.fixture
def test_env():
    """Set up test environment with storage, auth, and API key."""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test_dashboard.db"
    storage = SawyerStorage(str(db_path))
    auth = SawyerAuth(storage)
    full_key, api_key_obj = auth.create_key(user_id="test-admin", tier="operator")

    # Create a test account
    accountant = TokenAccountant()
    account = accountant.create_account("user-1", SubscriptionTier.EXPLORER)
    storage.save_account(account)

    # Record some inference
    record = accountant.record_inference(
        user_id="user-1",
        model_name="mixtral-8x7b",
        expert_ids=[0, 2],
        input_tokens=800,
        output_tokens=200,
        latency_ms=45.0,
        node_id="node-a",
    )
    storage.save_inference_record(record)
    storage.save_account(accountant.get_account("user-1"))

    yield {
        "storage": storage,
        "auth": auth,
        "api_key": full_key,
        "key_id": api_key_obj.key_id,
    }

    storage.close()


@pytest.fixture
def client(test_env):
    """Create a test client with injected storage."""
    app = create_app(storage=test_env["storage"])
    return TestClient(app)


class TestDashboardHealthAndRoot:
    """Test health check and root endpoint."""

    def test_health_check(self, client):
        """Health check returns healthy status."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_cluster_overview(self, client, test_env):
        """Root endpoint returns cluster overview."""
        api_key = test_env["api_key"]
        response = client.get("/", headers={"X-API-Key": api_key})
        assert response.status_code == 200
        data = response.json()
        assert "cluster" in data
        assert "nodes" in data
        assert "accounts" in data
        assert "timestamp" in data


class TestDashboardNodes:
    """Test node endpoints."""

    def test_list_nodes(self, client, test_env):
        """List nodes returns a list."""
        api_key = test_env["api_key"]
        response = client.get("/nodes", headers={"X-API-Key": api_key})
        assert response.status_code == 200

    def test_get_node_not_found(self, client, test_env):
        """Get non-existent node returns 404."""
        api_key = test_env["api_key"]
        response = client.get("/nodes/nonexistent", headers={"X-API-Key": api_key})
        assert response.status_code == 404


class TestDashboardAccounts:
    """Test account endpoints."""

    def test_list_accounts(self, client, test_env):
        """List accounts returns user data."""
        api_key = test_env["api_key"]
        response = client.get("/accounts", headers={"X-API-Key": api_key})
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert data[0]["user_id"] == "user-1"

    def test_get_account(self, client, test_env):
        """Get account details."""
        api_key = test_env["api_key"]
        response = client.get("/accounts/user-1", headers={"X-API-Key": api_key})
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "user-1"
        assert data["tier"] == "explorer"
        assert "balance" in data

    def test_get_account_not_found(self, client, test_env):
        """Get non-existent account returns 404."""
        api_key = test_env["api_key"]
        response = client.get("/accounts/nonexistent", headers={"X-API-Key": api_key})
        assert response.status_code == 404


class TestDashboardInference:
    """Test inference history endpoint."""

    def test_inference_history(self, client, test_env):
        """Get inference history for a user."""
        api_key = test_env["api_key"]
        response = client.get(
            "/inference/history/user-1",
            headers={"X-API-Key": api_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert data[0]["model"] == "mixtral-8x7b"
        assert data[0]["total_tokens"] == 1000


class TestDashboardStats:
    """Test aggregate stats endpoint."""

    def test_stats(self, client, test_env):
        """Stats endpoint returns aggregate data."""
        api_key = test_env["api_key"]
        response = client.get("/stats", headers={"X-API-Key": api_key})
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "accounts" in data
        assert "tokens" in data


class TestDashboardAuth:
    """Test API key authentication."""

    def test_no_api_key_returns_401(self, client):
        """Endpoints requiring auth return 401 without key."""
        response = client.get("/nodes")
        assert response.status_code == 401

    def test_invalid_api_key_returns_401(self, client):
        """Invalid API key returns 401."""
        response = client.get("/nodes", headers={"X-API-Key": "sak_invalid"})
        assert response.status_code == 401

    def test_api_key_in_query_param(self, client, test_env):
        """API key can be passed as query parameter."""
        api_key = test_env["api_key"]
        response = client.get(f"/nodes?api_key={api_key}")
        assert response.status_code == 200

    def test_health_check_no_auth_required(self, client):
        """Health check doesn't require authentication."""
        response = client.get("/health")
        assert response.status_code == 200
