import math
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.location import SafeZone

EARTH_RADIUS_M = 6_371_000


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """§07 §7.10: plain math, six lines - not worth a geopy dependency."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


async def match_safe_zone(
    session: AsyncSession, *, user_id: uuid.UUID, lat: float, lon: float
) -> SafeZone | None:
    """Smallest-radius active zone containing the point, or None if the
    point falls outside every zone the user has configured."""
    result = await session.execute(
        select(SafeZone).where(SafeZone.user_id == user_id, SafeZone.active.is_(True))
    )
    zones = list(result.scalars().all())
    # §17: a zone set via text-only "set address" (no location pin) has no
    # center at all - skip it here rather than crash on float(None). It can
    # still answer "where's my home" (services/caregiver.py reads .address
    # directly); it just can never geofence-match a pin.
    geofenced = [z for z in zones if z.center_lat is not None and z.radius_m is not None]
    matches = [
        zone
        for zone in geofenced
        if haversine_m(lat, lon, float(zone.center_lat), float(zone.center_lon)) <= zone.radius_m
    ]
    if not matches:
        return None
    return min(matches, key=lambda z: z.radius_m)
