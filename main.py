import random
import sqlite3
import string
from typing import List, Optional
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Courier Tracking System")


# ---------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------
def get_db():
    conn = sqlite3.connect("courier.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS parcels (
            tracking_id TEXT PRIMARY KEY,
            sender_name TEXT NOT NULL,
            recipient_name TEXT NOT NULL,
            destination TEXT NOT NULL,
            weight_kg REAL NOT NULL,
            delivery_speed TEXT NOT NULL,
            status TEXT DEFAULT 'Registered'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS parcel_status_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_id TEXT NOT NULL,
            status TEXT NOT NULL,
            location TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (tracking_id) REFERENCES parcels (tracking_id) ON DELETE CASCADE
        )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_logs_tracking ON parcel_status_logs (tracking_id);"
    )

    conn.commit()
    conn.close()


init_db()


# ---------------------------------------------------------
# PYDANTIC SCHEMAS
# ---------------------------------------------------------
class ParcelCreate(BaseModel):
    sender_name: str
    recipient_name: str
    destination: str
    weight_kg: float
    delivery_speed: str


class StatusUpdate(BaseModel):
    status: str
    location: str
    notes: Optional[str] = None


class HubMetrics(BaseModel):
    location: str
    total_events: int


class AnalyticsMetrics(BaseModel):
    average_transit_duration_hours: float
    busiest_hub: HubMetrics


class StuckParcelAlert(BaseModel):
    tracking_id: str
    location: str
    hours_in_current_state: float
    timestamp: str


class EfficiencyAlerts(BaseModel):
    threshold_hours: float
    total_alerts: int
    stuck_parcels: List[StuckParcelAlert]


class AnalyticsResponse(BaseModel):
    metrics: AnalyticsMetrics
    low_efficiency_alerts: EfficiencyAlerts


# ---------------------------------------------------------
# UTILS & STATE MACHINE RULES
# ---------------------------------------------------------
def generate_tracking_number():
    char_pool = string.ascii_uppercase + string.digits
    return "TRK-" + "".join(random.choices(char_pool, k=8))


ALLOWED_TRANSITIONS = {
    "Registered": ["Sorted"],
    "Sorted": ["In Transit"],
    "In Transit": ["Out for Delivery", "Failed Attempt"],
    "Out for Delivery": ["Delivered", "Failed Attempt"],
    "Failed Attempt": ["Out for Delivery"],
    "Delivered": [],
}


# ---------------------------------------------------------
# API ROUTES
# ---------------------------------------------------------
@app.post("/parcels")
def create_parcel(parcel: ParcelCreate):
    tracking_id = generate_tracking_number()
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO parcels (tracking_id, sender_name, recipient_name, destination, weight_kg, delivery_speed, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                tracking_id,
                parcel.sender_name,
                parcel.recipient_name,
                parcel.destination,
                parcel.weight_kg,
                parcel.delivery_speed,
                "Registered",
            ),
        )

        cursor.execute(
            """
            INSERT INTO parcel_status_logs (tracking_id, status, location, notes)
            VALUES (?, ?, ?, ?)
        """,
            (tracking_id, "Registered", "Sender Location", "Parcel created"),
        )

        conn.commit()
        return {
            "message": "Parcel registered successfully",
            "tracking_number": tracking_id,
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/parcels")
def get_all_parcels():
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM parcels")
        parcels = cursor.fetchall()
        return [dict(row) for row in parcels]
    finally:
        conn.close()


@app.get("/parcels/{tracking_id}")
def get_parcel(tracking_id: str):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM parcels WHERE tracking_id = ?", (tracking_id,))
        parcel = cursor.fetchone()

        if parcel is None:
            raise HTTPException(status_code=404, detail="Parcel not found")
        return dict(parcel)
    finally:
        conn.close()


@app.patch("/parcels/{tracking_id}/status")
def update_parcel_status(tracking_id: str, update: StatusUpdate):
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT status FROM parcels WHERE tracking_id = ?", (tracking_id,))
        parcel = cursor.fetchone()

        if parcel is None:
            raise HTTPException(status_code=404, detail="Parcel not found")

        current_status = parcel["status"]
        allowed_next_states = ALLOWED_TRANSITIONS.get(current_status, [])

        if update.status not in allowed_next_states:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid transition from '{current_status}' to '{update.status}'",
            )

        cursor.execute(
            """
            INSERT INTO parcel_status_logs (tracking_id, status, location, notes)
            VALUES (?, ?, ?, ?)
        """,
            (tracking_id, update.status, update.location, update.notes),
        )

        cursor.execute(
            "UPDATE parcels SET status = ? WHERE tracking_id = ?",
            (update.status, tracking_id),
        )

        conn.commit()
        return {
            "message": "Status updated successfully",
            "tracking_number": tracking_id,
            "new_status": update.status,
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/parcels/{tracking_id}/history")
def get_parcel_history(tracking_id: str):
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT status FROM parcels WHERE tracking_id = ?", (tracking_id,))
        parcel = cursor.fetchone()

        if parcel is None:
            raise HTTPException(status_code=404, detail="Parcel not found")

        cursor.execute(
            """
            SELECT status, location, timestamp, notes
            FROM parcel_status_logs
            WHERE tracking_id = ?
            ORDER BY id ASC
        """,
            (tracking_id,),
        )
        logs = cursor.fetchall()

        return {
            "tracking_id": tracking_id,
            "current_status": parcel["status"],
            "history": [dict(log) for log in logs],
        }
    finally:
        conn.close()


@app.get("/analytics", response_model=AnalyticsResponse)
def get_analytics(stuck_threshold_hours: float = 24.0):
    conn = get_db()
    try:
        query = "SELECT tracking_id, status, location, timestamp FROM parcel_status_logs"
        df = pd.read_sql_query(query, conn, parse_dates=["timestamp"])
    finally:
        conn.close()

    if df.empty:
        return {
            "metrics": {
                "average_transit_duration_hours": 0.0,
                "busiest_hub": {"location": "N/A", "total_events": 0},
            },
            "low_efficiency_alerts": {
                "threshold_hours": stuck_threshold_hours,
                "total_alerts": 0,
                "stuck_parcels": [],
            },
        }

    # 1. Average Transit Duration
    df_delivered = df[df["status"].isin(["Registered", "Delivered"])]
    transit = df_delivered.groupby("tracking_id")["timestamp"].agg(["min", "max", "count"])
    completed = transit[transit["count"] == 2].copy()

    if not completed.empty:
        completed["duration"] = (completed["max"] - completed["min"]).dt.total_seconds() / 3600.0
        avg_duration = round(float(completed["duration"].mean()), 2)
    else:
        avg_duration = 0.0

    # 2. Busiest Hub
    hub_counts = df[df["location"] != "Sender Location"]["location"].value_counts()
    if not hub_counts.empty:
        busiest_hub = {"location": str(hub_counts.index[0]), "total_events": int(hub_counts.iloc[0])}
    else:
        busiest_hub = {"location": "N/A", "total_events": 0}

    # 3. Stuck Parcels Alert
    latest_logs = df.sort_values("timestamp").groupby("tracking_id").last().reset_index()
    now = pd.Timestamp.now()
    latest_logs["hours_in_current_state"] = (
        (now - latest_logs["timestamp"]).dt.total_seconds() / 3600.0
    ).round(2)

    stuck = latest_logs[
        (latest_logs["status"] == "In Transit")
        & (latest_logs["hours_in_current_state"] > stuck_threshold_hours)
    ]

    alerts = (
        stuck[["tracking_id", "location", "hours_in_current_state", "timestamp"]]
        .astype({"timestamp": str})
        .to_dict(orient="records")
    )

    return {
        "metrics": {
            "average_transit_duration_hours": avg_duration,
            "busiest_hub": busiest_hub,
        },
        "low_efficiency_alerts": {
            "threshold_hours": stuck_threshold_hours,
            "total_alerts": len(alerts),
            "stuck_parcels": alerts,
        },
    }