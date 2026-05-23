"""One-off patch: add is_active to dispatch_zones seed INSERT in c3d4e5f6a7b8 migration."""
from pathlib import Path

path = Path(__file__).parent / "mediroute-backend/alembic/versions/c3d4e5f6a7b8_add_dispatch_tables.py"
text = path.read_text(encoding="utf-8")
old = (
    "        INSERT INTO dispatch_zones (city_id, zone_code, zone_name, center_latitude, center_longitude, radius_km)\n"
    "        VALUES ('HYD', 'HYD-BH', 'Banjara Hills / Jubilee Hills', 17.4126, 78.4471, 8.0)"
)
new = (
    "        INSERT INTO dispatch_zones (\n"
    "            city_id, zone_code, zone_name,\n"
    "            center_latitude, center_longitude, radius_km,\n"
    "            is_active\n"
    "        )\n"
    "        VALUES (\n"
    "            'HYD', 'HYD-BH', 'Banjara Hills / Jubilee Hills',\n"
    "            17.4126, 78.4471, 8.0,\n"
    "            TRUE\n"
    "        )"
)
if old not in text:
    raise SystemExit("pattern not found (already patched?)")
path.write_text(text.replace(old, new), encoding="utf-8")
print("patched", path)
