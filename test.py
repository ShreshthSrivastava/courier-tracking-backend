from fastapi.testclient import TestClient
from main import app

# Create a test client that pretends to be a user sending requests to our API
client = TestClient(app)


# ---------------------------------------------------------
# TEST 1: Creating a Parcel (Week 1)
# ---------------------------------------------------------
def test_create_parcel():
    # 1. Define the parcel data we want to send
    parcel_data = {
        "sender_name": "Alice",
        "recipient_name": "Bob",
        "destination": "New York",
        "weight_kg": 2.5,
        "delivery_speed": "Express",
    }

    # 2. Send a POST request to create the parcel
    response = client.post("/parcels", json=parcel_data)

    # 3. Check if the server responded with HTTP 200 (OK)
    assert response.status_code == 200

    # 4. Check if the server returned a tracking number
    result = response.json()
    assert "tracking_number" in result


# ---------------------------------------------------------
# TEST 2: Status Updates & Allowed Steps (Week 2)
# ---------------------------------------------------------
def test_parcel_status_rules():
    # Step A: Create a new parcel first
    create_response = client.post(
        "/parcels",
        json={
            "sender_name": "John",
            "recipient_name": "Mary",
            "destination": "London",
            "weight_kg": 1.0,
            "delivery_speed": "Standard",
        },
    )
    tracking_id = create_response.json()["tracking_number"]

    # Step B: Try a VALID update (Registered -> Sorted)
    valid_update = client.patch(
        f"/parcels/{tracking_id}/status",
        json={"status": "Sorted", "location": "Hub A"},
    )
    assert valid_update.status_code == 200

    # Step C: Try an INVALID update (Sorted -> Delivered is not allowed directly)
    invalid_update = client.patch(
        f"/parcels/{tracking_id}/status",
        json={"status": "Delivered", "location": "Home Address"},
    )
    # The server should block this and give HTTP 400 (Bad Request)
    assert invalid_update.status_code == 400


# ---------------------------------------------------------
# TEST 3: Getting Analytics (Week 3)
# ---------------------------------------------------------
def test_get_analytics():
    # Send a GET request to the analytics endpoint
    response = client.get("/analytics")

    # Check that the request succeeded
    assert response.status_code == 200

    # Check that the expected keys exist in the response
    data = response.json()
    assert "metrics" in data
    assert "low_efficiency_alerts" in data


# ---------------------------------------------------------
# TEST 4: Requesting a Parcel That Doesn't Exist
# ---------------------------------------------------------
def test_parcel_not_found():
    # Send a request with a fake ID
    response = client.get("/parcels/TRK-FAKE1234")

    # The server should give HTTP 404 (Not Found)
    assert response.status_code == 404