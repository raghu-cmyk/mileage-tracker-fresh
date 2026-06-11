"""In-process smoke tests for the mileage tracker app."""

import os
import tempfile

import pytest

from app import create_app, init_db


@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    app = create_app({"TESTING": True, "DATABASE": db_path, "SECRET_KEY": "test-secret"})
    init_db(db_path)
    with app.test_client() as c:
        yield c
    os.unlink(db_path)


def test_login_page_loads(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Log in" in resp.data


def test_register_and_login_flow(client):
    resp = client.post(
        "/register",
        data={
            "display_name": "Test User",
            "email": "test@example.com",
            "password": "securepass123",
            "confirm_password": "securepass123",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data

    client.get("/logout", follow_redirects=True)
    resp = client.post(
        "/login",
        data={"email": "test@example.com", "password": "securepass123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Welcome back" in resp.data


def test_full_trip_vehicle_report_flow(client):
    client.post(
        "/register",
        data={
            "display_name": "Driver",
            "email": "driver@example.com",
            "password": "password1234",
            "confirm_password": "password1234",
        },
        follow_redirects=True,
    )

    resp = client.post(
        "/vehicles/new",
        data={"label": "Work Car", "make": "Toyota", "model": "Camry", "year": "2020", "is_default": "on"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Work Car" in resp.data

    resp = client.post(
        "/trips/new",
        data={
            "trip_date": "2025-06-15",
            "vehicle_id": "1",
            "purpose": "business",
            "distance_miles": "42.5",
            "start_location": "Office",
            "end_location": "Client Site",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"42.5" in resp.data

    resp = client.get("/reports?year=2025")
    assert resp.status_code == 200
    assert b"42.5" in resp.data
    assert b"IRS" in resp.data


def test_protected_routes_redirect(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
