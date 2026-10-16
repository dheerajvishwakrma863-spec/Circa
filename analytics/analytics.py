"""Live impact analytics from the database. Run from repo root: python -m analytics.analytics
Scope-3 scope: packaging MATERIAL production only (excludes transport, filling, consumer use)."""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from sqlalchemy import text
from backend.database import engine

Q = text("""SELECT t.timestamp, t.plastic_saved_g, t.co2_saved_g, p.sku, p.weight_g, p.co2_per_kg
            FROM transactions t JOIN packaging_serials s ON s.id = t.serial_id
            JOIN products p ON p.id = s.product_id""")
df = pd.read_sql(Q, engine)
if df.empty:
    raise SystemExit("No transactions yet. Verify some packs through the API first.")
df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
df["month"] = df["timestamp"].dt.strftime("%Y-%m")
df["baseline_co2_g"] = df["weight_g"] * df["co2_per_kg"]      # if each pack had been a new container
monthly = df.groupby("month").agg(plastic_g=("plastic_saved_g", "sum"), co2_g=("co2_saved_g", "sum"),
                                  baseline_g=("baseline_co2_g", "sum"), packs=("sku", "count")).reset_index()
monthly["reduction_pct"] = 100 * monthly.co2_g / monthly.baseline_g
by_sku = df.groupby("sku").agg(plastic_g=("plastic_saved_g", "sum"), co2_g=("co2_saved_g", "sum")).reset_index()

fig = make_subplots(rows=1, cols=3, subplot_titles=("CO2e avoided per month (kg)", "Cumulative plastic diverted (kg)", "Plastic saved by SKU (kg)"))
fig.add_trace(go.Bar(x=monthly.month, y=monthly.co2_g / 1000, marker_color="#16a34a", name="CO2e"), 1, 1)
fig.add_trace(go.Scatter(x=monthly.month, y=(monthly.plastic_g / 1000).cumsum(), line=dict(color="#0ea5e9"), name="Plastic"), 1, 2)
fig.add_trace(go.Bar(x=by_sku.sku, y=by_sku.plastic_g / 1000, marker_color="#f59e0b", name="By SKU"), 1, 3)
fig.update_layout(title="CIRCA live impact (from database)", template="plotly_white", height=420, showlegend=False)
fig.write_html("impact_live.html")
print(f"Packs: {len(df)} | plastic saved {df.plastic_saved_g.sum()/1000:.2f} kg | CO2e avoided {df.co2_saved_g.sum()/1000:.2f} kg"
      f" | packaging-material reduction {100*df.co2_saved_g.sum()/df.baseline_co2_g.sum():.1f}%")
print("Wrote impact_live.html")
