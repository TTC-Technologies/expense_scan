# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Geocoding, road distance and route picture.

The services are OpenStreetMap's: a Nominatim search for the addresses and
an OSRM server for the route, both replaceable in the settings by one's
own. Their data is © OpenStreetMap contributors (ODbL): the picture says it.
"""
import io
import json
import math
import urllib.parse
from dataclasses import dataclass, field
from typing import List, Tuple

#: Road distance believed from a straight line when no route service
#: answers: roads are about a quarter longer than the crow flies.
ROAD_FACTOR = 1.25
EARTH_RADIUS_KM = 6371.0088
#: Longest route believed for one expense.
MAX_ROUTE_KM = 3000.0


@dataclass
class Point:
    lat: float
    lon: float
    label: str = ""


@dataclass
class Route:
    """Distance of one way, in km, and the path followed (latitude, longitude)."""
    km: float
    minutes: float = 0.0
    path: List[Tuple[float, float]] = field(default_factory=list)
    #: True when no service answered: straight line times ROAD_FACTOR.
    estimated: bool = False
    source: str = ""
    #: The roads most of the route follows: (number, (lat, lon) of the middle of their longest stretch).
    roads: List[Tuple[str, Tuple[float, float]]] = field(default_factory=list)


def haversine_km(a, b):
    """Distance as the crow flies between two (lat, lon), in km."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def decode_polyline(text, precision=5):
    """Points of an encoded polyline (Google's format, used by OSRM)."""
    points, index, lat, lon = [], 0, 0, 0
    factor = 10 ** precision
    while index < len(text):
        values = []
        for _coordinate in range(2):
            shift = result = 0
            while True:
                byte = ord(text[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            values.append(~(result >> 1) if result & 1 else result >> 1)
        lat += values[0]
        lon += values[1]
        points.append((lat / factor, lon / factor))
    return points


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

def geocode_url(base, query, language=None, country_codes=None, limit=5):
    params = {"q": query, "format": "jsonv2", "limit": limit, "addressdetails": 0}
    if language:
        params["accept-language"] = language
    if country_codes:
        params["countrycodes"] = ",".join(country_codes)
    return "%s/search?%s" % (base.rstrip("/"), urllib.parse.urlencode(params))


def geocode(fetch, base, query, language=None, country_codes=None, limit=5):
    """Places matching a text: ``[Point]``, the most likely first."""
    if not (query or "").strip():
        return []
    data = json.loads(fetch(geocode_url(base, query.strip(), language, country_codes, limit)))
    points = []
    for item in data if isinstance(data, list) else []:
        try:
            points.append(Point(float(item["lat"]), float(item["lon"]),
                                item.get("display_name") or query))
        except (KeyError, TypeError, ValueError):
            continue
    return points


def route_url(base, start, end):
    return "%s/route/v1/driving/%.6f,%.6f;%.6f,%.6f?overview=simplified&geometries=polyline&steps=true" % (
        base.rstrip("/"), start.lon, start.lat, end.lon, end.lat)


def road_route(fetch, base, start, end):
    """The road route from ``start`` to ``end``, or ``None``."""
    data = json.loads(fetch(route_url(base, start, end)))
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    best = data["routes"][0]
    km = float(best.get("distance") or 0.0) / 1000.0
    if not 0 < km <= MAX_ROUTE_KM:
        return None
    path = decode_polyline(best["geometry"]) if isinstance(best.get("geometry"), str) else []
    return Route(km=km, minutes=float(best.get("duration") or 0.0) / 60.0,
                 path=path or [(start.lat, start.lon), (end.lat, end.lon)], source="osrm",
                 roads=main_roads(best, km))


def main_roads(route, km, count=4):
    """The numbered roads ("A 68", "D 631") most of an OSRM route follows, the longest first.

    Each with the middle of its longest stretch, where its number is drawn.
    """
    totals, longest = {}, {}
    for leg in route.get("legs") or []:
        for step in leg.get("steps") or []:
            ref = str(step.get("ref") or "").split(";")[0].strip()
            if not ref:
                continue
            distance = float(step.get("distance") or 0.0) / 1000.0
            totals[ref] = totals.get(ref, 0.0) + distance
            if distance > longest.get(ref, (0.0, None))[0] and isinstance(step.get("geometry"), str):
                points = decode_polyline(step["geometry"])
                if points:
                    longest[ref] = (distance, points[len(points) // 2])
    # Roads of a few hundred metres say nothing about the way taken.
    kept = [ref for ref in totals if ref in longest and totals[ref] >= max(3.0, km * 0.05)]
    kept.sort(key=lambda ref: -totals[ref])
    return [(ref, longest[ref][1]) for ref in kept[:count]]


def reverse_url(base, lat, lon, language=None):
    params = {"lat": "%.5f" % lat, "lon": "%.5f" % lon, "zoom": 10, "format": "jsonv2"}
    if language:
        params["accept-language"] = language
    return "%s/reverse?%s" % (base.rstrip("/"), urllib.parse.urlencode(params))


def towns_along(fetch, base, path, language=None, count=3, pause=None):
    """Towns the route goes through: ``[Point]``, read at even distances along the path.

    One question to the geocoder per town; ``pause(seconds)`` spaces them,
    as Nominatim asks (one request per second at most). A short route has
    none: its two ends say enough.
    """
    if not base or len(path) < 2:
        return []
    lengths = [0.0]
    for a, b in zip(path, path[1:]):
        lengths.append(lengths[-1] + haversine_km(a, b))
    if lengths[-1] < 20:
        return []
    towns, seen = [], set()
    for number in range(1, count + 1):
        target = lengths[-1] * number / (count + 1)
        lat, lon = path[next(i for i, length in enumerate(lengths) if length >= target)]
        if pause and number > 1:
            pause(1.0)
        try:
            data = json.loads(fetch(reverse_url(base, lat, lon, language)))
        except Exception:  # noqa: BLE001 - a picture without towns is still a picture
            continue
        if not isinstance(data, dict):
            continue
        address = data.get("address") or {}
        name = next((address[key] for key in ("city", "town", "village", "municipality") if address.get(key)),
                    None) or data.get("name")
        if name and name not in seen:
            seen.add(name)
            towns.append(Point(lat, lon, name))
    return towns


def estimated_route(start, end):
    """The straight line, lengthened like a road."""
    return Route(km=haversine_km((start.lat, start.lon), (end.lat, end.lon)) * ROAD_FACTOR,
                 path=[(start.lat, start.lon), (end.lat, end.lon)], estimated=True)


def find_route(fetch, base, start, end):
    """Road route if the service answers, otherwise the estimate."""
    if base:
        try:
            found = road_route(fetch, base, start, end)
        except Exception:  # noqa: BLE001 - a service down falls back on the estimate
            found = None
        if found:
            return found
    return estimated_route(start, end)


# ---------------------------------------------------------------------------
# Picture
# ---------------------------------------------------------------------------

TILE_SIZE = 256
WIDTH, HEIGHT = 1100, 780
MAP_TOP = 150
MARGIN = 60


def _mercator(lat, lon, zoom):
    """Pixel position of a point at a zoom level (Web Mercator)."""
    scale = TILE_SIZE * 2 ** zoom
    x = (lon + 180.0) / 360.0 * scale
    lat = max(min(lat, 85.0511), -85.0511)
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * scale
    return x, y


def _fit_zoom(path, width, height):
    """Highest zoom (whole) at which the path fits in the frame."""
    for zoom in range(17, 1, -1):
        xs, ys = zip(*(_mercator(lat, lon, zoom) for lat, lon in path))
        if max(xs) - min(xs) <= width and max(ys) - min(ys) <= height:
            return zoom
    return 2


def _font(size, bold=False, font_path=None):
    from PIL import ImageFont
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:  # noqa: BLE001 - any font problem falls back on the default one
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def render_route(route, start, end, lines, attribution, tiles_url=None, fetch=None,
                 fonts=None, towns=None):
    """PNG of the route: the path, both ends, and the lines of text above.

    ``tiles_url``: a tile server ("https://.../{z}/{x}/{y}.png") for a map
    background; without one, the route is drawn on a plain background, with
    the towns it goes through (``towns``: ``[Point]``) and the numbers of its
    main roads to show the way. ``fonts``: ``(regular, bold)`` font files.
    """
    from PIL import Image, ImageDraw

    regular, bold = (fonts or (None, None))
    image = Image.new("RGB", (WIDTH, HEIGHT), (250, 250, 247))
    draw = ImageDraw.Draw(image)
    frame_w, frame_h = WIDTH - 2 * MARGIN, HEIGHT - MAP_TOP - MARGIN
    path = route.path or [(start.lat, start.lon), (end.lat, end.lon)]
    zoom = _fit_zoom(path, frame_w - 80, frame_h - 80)
    pixels = [_mercator(lat, lon, zoom) for lat, lon in path]
    xs, ys = zip(*pixels)
    origin_x = (min(xs) + max(xs)) / 2 - frame_w / 2
    origin_y = (min(ys) + max(ys)) / 2 - frame_h / 2

    def place(x, y):
        return MARGIN + x - origin_x, MAP_TOP + y - origin_y

    box = (MARGIN, MAP_TOP, MARGIN + frame_w, MAP_TOP + frame_h)
    background = tiles_url and fetch and _tiles(tiles_url, fetch, zoom, origin_x, origin_y,
                                                frame_w, frame_h)
    if background:
        image.paste(background, (MARGIN, MAP_TOP))
    else:
        draw.rectangle(box, fill=(236, 241, 236))
        # A light grid, every 10 km or so, gives a sense of scale.
        step = 10.0 / _km_per_pixel(path[0][0], zoom)
        if step > 25:
            offset = step - origin_x % step
            x = offset
            while x < frame_w:
                draw.line((MARGIN + x, MAP_TOP, MARGIN + x, MAP_TOP + frame_h), fill=(222, 229, 222))
                x += step
            y = step - origin_y % step
            while y < frame_h:
                draw.line((MARGIN, MAP_TOP + y, MARGIN + frame_w, MAP_TOP + y), fill=(222, 229, 222))
                y += step
    draw.rectangle(box, outline=(160, 170, 160), width=2)

    points = [place(x, y) for x, y in pixels]
    if len(points) > 1:
        draw.line(points, fill=(255, 255, 255), width=11, joint="curve")
        draw.line(points, fill=(32, 96, 200), width=6, joint="curve")
    ends = (points[0], points[-1])

    def free(x, y, room=45):
        """Away from both ends, whose names are written next to them."""
        return all(abs(x - ex) > room or abs(y - ey) > room for ex, ey in ends)

    town_font = _font(15, False, regular)
    for town in towns or []:
        x, y = place(*_mercator(town.lat, town.lon, zoom))
        if not free(x, y):
            continue
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(60, 60, 60), outline=(255, 255, 255), width=2)
        right = x < MARGIN + frame_w * 0.7
        draw.text((x + 10 if right else x - 10, y), town.label[:30], fill=(40, 40, 40), font=town_font,
                  anchor="lm" if right else "rm", stroke_width=3, stroke_fill=(255, 255, 255))
    road_font = _font(14, True, bold)
    for ref, (lat, lon) in route.roads:
        x, y = place(*_mercator(lat, lon, zoom))
        if not free(x, y, 30):
            continue
        # The colours of the road signs: red for motorways and national roads, yellow for the others.
        main = ref[:1].upper() in ("A", "M", "N", "E")
        left, top, right_edge, bottom = draw.textbbox((x, y), ref, font=road_font, anchor="mm")
        draw.rounded_rectangle((left - 6, top - 4, right_edge + 6, bottom + 4), radius=4,
                               fill=(196, 40, 40) if main else (245, 205, 50), outline=(255, 255, 255), width=2)
        draw.text((x, y), ref, fill=(255, 255, 255) if main else (30, 30, 30), font=road_font, anchor="mm")
    marker = _font(18, True, bold)
    name = _font(17, True, bold)
    for (x, y), colour, letter, point in ((points[0], (30, 140, 60), "A", start),
                                          (points[-1], (200, 50, 40), "B", end)):
        draw.ellipse((x - 15, y - 15, x + 15, y + 15), fill=colour, outline=(255, 255, 255), width=3)
        draw.text((x, y), letter, fill=(255, 255, 255), font=marker, anchor="mm")
        # The short name of the place next to its marker, on the side with room.
        short = (point.label or "").split(",")[0][:40]
        right = x < MARGIN + frame_w * 0.7
        draw.text((x + 22 if right else x - 22, y), short, fill=(30, 30, 30), font=name,
                  anchor="lm" if right else "rm", stroke_width=3, stroke_fill=(255, 255, 255))

    title = _font(30, True, bold)
    text = _font(19, False, regular)
    draw.text((MARGIN, 24), lines[0], fill=(20, 20, 20), font=title)
    y = 70
    for line in lines[1:]:
        draw.text((MARGIN, y), line, fill=(60, 60, 60), font=text)
        y += 26
    small = _font(14, False, regular)
    draw.text((WIDTH - MARGIN, HEIGHT - MARGIN + 18), attribution, fill=(110, 110, 110),
              font=small, anchor="ra")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _km_per_pixel(lat, zoom):
    return 40075.016686 * math.cos(math.radians(lat)) / (TILE_SIZE * 2 ** zoom)


def _tiles(url, fetch, zoom, origin_x, origin_y, width, height):
    """The map background of the frame, from a tile server, or ``None``."""
    from PIL import Image

    canvas = Image.new("RGB", (width, height), (236, 241, 236))
    first_x, first_y = int(origin_x // TILE_SIZE), int(origin_y // TILE_SIZE)
    last_x, last_y = int((origin_x + width) // TILE_SIZE), int((origin_y + height) // TILE_SIZE)
    if (last_x - first_x + 1) * (last_y - first_y + 1) > 30:
        return None
    count = 2 ** zoom
    try:
        for tile_x in range(first_x, last_x + 1):
            for tile_y in range(first_y, last_y + 1):
                if not 0 <= tile_y < count:
                    continue
                data = fetch(url.format(z=zoom, x=tile_x % count, y=tile_y))
                tile = Image.open(io.BytesIO(data)).convert("RGB")
                canvas.paste(tile, (int(tile_x * TILE_SIZE - origin_x), int(tile_y * TILE_SIZE - origin_y)))
    except Exception:  # noqa: BLE001 - without its tiles, the plain background will do
        return None
    return canvas


def format_minutes(minutes):
    hours, rest = divmod(int(round(minutes)), 60)
    return "%d h %02d" % (hours, rest) if hours else "%d min" % rest


def osm_link(start, end):
    """Link to the route on openstreetmap.org, to look at it on a real map."""
    return ("https://www.openstreetmap.org/directions?engine=fossgis_osrm_car&route=%.5f%%2C%.5f%%3B%.5f%%2C%.5f"
            % (start.lat, start.lon, end.lat, end.lon))

