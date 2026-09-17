"""A small LOCAL catalogue of standard mechanical components.

**This is not a supplier database and must never be presented as one.** It is
a hand-written table of common parts, checked into the repository, with no
network access and no claim of availability, price or stock. Every record
below is a nominal standard dimension, not a vendor's product listing.

It exists so that Part Finder can return *real* records to a real query while
the question of where a production catalogue comes from stays open.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: Said on every response, so a record can never be mistaken for a supplier's.
SOURCE_LABEL = "LOCAL_REFERENCE_CATALOGUE"
SOURCE_NOTE = (
    "Nominal standard dimensions from a small local table checked into this "
    "repository. Not a supplier catalogue: no availability, no price, no "
    "stock, and no network request was made."
)


@dataclass(frozen=True)
class CatalogPart:
    """One component. Dimensions in millimetres unless stated."""

    part_number: str
    category: str
    description: str
    dimensions: Mapping[str, Any]
    material: Optional[str] = None
    standard: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "part_number": self.part_number,
            "category": self.category,
            "description": self.description,
            "dimensions": dict(self.dimensions),
            "material": self.material,
            "standard": self.standard,
        }

    def haystack(self) -> str:
        parts = [self.part_number, self.category, self.description,
                 self.material or "", self.standard or ""]
        parts.extend(f"{k} {v}" for k, v in self.dimensions.items())
        return " ".join(parts).lower()


def _screw(size: str, dia: float, pitch: float, length: float,
           head_d: float, head_h: float, drive: str, std: str) -> CatalogPart:
    return CatalogPart(
        part_number=f"SHCS-{size}x{length:g}",
        category="screw",
        description=f"{size} x {length:g} mm socket head cap screw, {drive} drive",
        dimensions={"thread": size, "nominal_diameter_mm": dia,
                    "pitch_mm": pitch, "length_mm": length,
                    "head_diameter_mm": head_d, "head_height_mm": head_h,
                    "clearance_hole_mm": round(dia * 1.1, 1)},
        material="stainless A2", standard=std)


#: The catalogue. Small on purpose -- enough to answer a real query, little
#: enough that every row can be checked by eye.
CATALOG: Tuple[CatalogPart, ...] = (
    # --- socket head cap screws (ISO 4762) ---
    _screw("M3", 3.0, 0.5, 10, 5.5, 3.0, "hex socket", "ISO 4762"),
    _screw("M4", 4.0, 0.7, 12, 7.0, 4.0, "hex socket", "ISO 4762"),
    _screw("M5", 5.0, 0.8, 16, 8.5, 5.0, "hex socket", "ISO 4762"),
    _screw("M6", 6.0, 1.0, 20, 10.0, 6.0, "hex socket", "ISO 4762"),
    _screw("M8", 8.0, 1.25, 25, 13.0, 8.0, "hex socket", "ISO 4762"),
    _screw("M8", 8.0, 1.25, 40, 13.0, 8.0, "hex socket", "ISO 4762"),
    _screw("M10", 10.0, 1.5, 30, 16.0, 10.0, "hex socket", "ISO 4762"),
    _screw("M12", 12.0, 1.75, 40, 18.0, 12.0, "hex socket", "ISO 4762"),

    # --- hex nuts (ISO 4032) ---
    CatalogPart("NUT-M5", "nut", "M5 hex nut",
                {"thread": "M5", "nominal_diameter_mm": 5.0,
                 "across_flats_mm": 8.0, "thickness_mm": 4.7},
                "stainless A2", "ISO 4032"),
    CatalogPart("NUT-M6", "nut", "M6 hex nut",
                {"thread": "M6", "nominal_diameter_mm": 6.0,
                 "across_flats_mm": 10.0, "thickness_mm": 5.2},
                "stainless A2", "ISO 4032"),
    CatalogPart("NUT-M8", "nut", "M8 hex nut",
                {"thread": "M8", "nominal_diameter_mm": 8.0,
                 "across_flats_mm": 13.0, "thickness_mm": 6.8},
                "stainless A2", "ISO 4032"),
    CatalogPart("NUT-M10", "nut", "M10 hex nut",
                {"thread": "M10", "nominal_diameter_mm": 10.0,
                 "across_flats_mm": 17.0, "thickness_mm": 8.4},
                "stainless A2", "ISO 4032"),

    # --- plain washers (ISO 7089) ---
    CatalogPart("WSH-M5", "washer", "M5 plain washer",
                {"thread": "M5", "nominal_diameter_mm": 5.0, "inner_diameter_mm": 5.3,
                 "outer_diameter_mm": 10.0, "thickness_mm": 1.0},
                "stainless A2", "ISO 7089"),
    CatalogPart("WSH-M6", "washer", "M6 plain washer",
                {"thread": "M6", "nominal_diameter_mm": 6.0, "inner_diameter_mm": 6.4,
                 "outer_diameter_mm": 12.0, "thickness_mm": 1.6},
                "stainless A2", "ISO 7089"),
    CatalogPart("WSH-M8", "washer", "M8 plain washer",
                {"thread": "M8", "nominal_diameter_mm": 8.0, "inner_diameter_mm": 8.4,
                 "outer_diameter_mm": 16.0, "thickness_mm": 1.6},
                "stainless A2", "ISO 7089"),
    CatalogPart("WSH-M10", "washer", "M10 plain washer",
                {"thread": "M10", "nominal_diameter_mm": 10.0, "inner_diameter_mm": 10.5,
                 "outer_diameter_mm": 20.0, "thickness_mm": 2.0},
                "stainless A2", "ISO 7089"),

    # --- deep groove ball bearings (ISO 15) ---
    CatalogPart("BRG-608", "bearing", "608 deep groove ball bearing",
                {"bore_mm": 8.0, "outer_diameter_mm": 22.0, "width_mm": 7.0},
                "chrome steel", "ISO 15"),
    CatalogPart("BRG-6000", "bearing", "6000 deep groove ball bearing",
                {"bore_mm": 10.0, "outer_diameter_mm": 26.0, "width_mm": 8.0},
                "chrome steel", "ISO 15"),
    CatalogPart("BRG-6004", "bearing", "6004 deep groove ball bearing",
                {"bore_mm": 20.0, "outer_diameter_mm": 42.0, "width_mm": 12.0},
                "chrome steel", "ISO 15"),
    CatalogPart("BRG-6204", "bearing", "6204 deep groove ball bearing",
                {"bore_mm": 20.0, "outer_diameter_mm": 47.0, "width_mm": 14.0},
                "chrome steel", "ISO 15"),
    CatalogPart("BRG-6205", "bearing", "6205 deep groove ball bearing",
                {"bore_mm": 25.0, "outer_diameter_mm": 52.0, "width_mm": 15.0},
                "chrome steel", "ISO 15"),

    # --- dowel pins (ISO 2338) ---
    CatalogPart("PIN-4x20", "pin", "4 x 20 mm parallel dowel pin",
                {"nominal_diameter_mm": 4.0, "length_mm": 20.0},
                "hardened steel", "ISO 2338"),
    CatalogPart("PIN-6x30", "pin", "6 x 30 mm parallel dowel pin",
                {"nominal_diameter_mm": 6.0, "length_mm": 30.0},
                "hardened steel", "ISO 2338"),
)

CATEGORIES: Tuple[str, ...] = tuple(sorted({p.category for p in CATALOG}))

#: Words that name a category, so "show me bearings" narrows before matching.
_CATEGORY_WORDS = {
    "screw": "screw", "screws": "screw", "bolt": "screw", "bolts": "screw",
    "shcs": "screw", "cap": "screw", "fastener": "screw", "fasteners": "screw",
    "nut": "nut", "nuts": "nut",
    "washer": "washer", "washers": "washer",
    "bearing": "bearing", "bearings": "bearing",
    "pin": "pin", "pins": "pin", "dowel": "pin",
}

_THREAD = re.compile(r"\bm(\d{1,2})\b", re.I)
_MM = re.compile(r"(\d+(?:\.\d+)?)\s*mm\b", re.I)


@dataclass(frozen=True)
class CatalogQuery:
    """A parsed query, so a caller can see WHY a record matched."""

    text: str
    category: Optional[str] = None
    thread: Optional[str] = None
    bore_mm: Optional[float] = None
    words: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "category": self.category,
                "thread": self.thread, "bore_mm": self.bore_mm,
                "words": list(self.words)}


def parse_query(text: str) -> CatalogQuery:
    """Pull the structured parts out of a plain-language request.

    Deliberately simple and local: no model call. "Find an M8 socket head cap
    screw" is a category plus a thread plus a few words, and a table this
    size is better served by matching than by interpretation.
    """
    lowered = (text or "").lower()
    # The category is the component noun that appears EARLIEST, which is the
    # subject of the request. "Find washers for M8 bolts" asks for washers;
    # taking whichever noun this table happened to list first answered it
    # with screws, because the bolts are only the context.
    category = None
    earliest = len(lowered) + 1
    for word, name in _CATEGORY_WORDS.items():
        match = re.search(rf"\b{word}\b", lowered)
        if match is not None and match.start() < earliest:
            earliest, category = match.start(), name
    thread_match = _THREAD.search(lowered)
    thread = f"M{thread_match.group(1)}" if thread_match else None

    bore = None
    if category == "bearing" or "bore" in lowered:
        mm = _MM.search(lowered)
        if mm:
            bore = float(mm.group(1))

    words = tuple(w for w in re.findall(r"[a-z0-9.]+", lowered) if len(w) > 2)
    return CatalogQuery(text=text, category=category, thread=thread,
                        bore_mm=bore, words=words)


def search(text: str, *, limit: int = 8) -> Dict[str, Any]:
    """Find catalogue records matching a plain-language request.

    Returns the parsed query alongside the results, so a caller can show why
    something matched -- and can see when a query was understood narrowly.
    """
    query = parse_query(text)
    scored: List[Tuple[float, CatalogPart]] = []

    for part in CATALOG:
        if query.category and part.category != query.category:
            continue
        if query.thread and part.dimensions.get("thread") != query.thread:
            continue
        if query.bore_mm is not None:
            bore = part.dimensions.get("bore_mm")
            if not isinstance(bore, (int, float)) or abs(bore - query.bore_mm) > 1e-6:
                continue

        hay = part.haystack()
        score = sum(1.0 for word in query.words if word in hay)
        if query.category:
            score += 2.0
        if query.thread:
            score += 3.0
        if query.bore_mm is not None:
            score += 3.0
        if score > 0:
            scored.append((score, part))

    scored.sort(key=lambda pair: (-pair[0], pair[1].part_number))
    results = [part.to_dict() for _, part in scored[:limit]]
    return {
        "query": query.to_dict(),
        "results": results,
        "total": len(scored),
        "source": SOURCE_LABEL,
        "note": SOURCE_NOTE,
    }


def suggest_for_hole(diameter_mm: float) -> Dict[str, Any]:
    """Which screws pass through a hole of this diameter.

    Uses the clearance the catalogue records for each screw, so the answer is
    a table lookup rather than a rule of thumb invented here.
    """
    fits = [
        part.to_dict() for part in CATALOG
        if part.category == "screw"
        and isinstance(part.dimensions.get("clearance_hole_mm"), (int, float))
        and part.dimensions["clearance_hole_mm"] <= diameter_mm + 1e-9
    ]
    fits.sort(key=lambda p: -p["dimensions"]["nominal_diameter_mm"])
    return {"hole_diameter_mm": diameter_mm, "results": fits[:6],
            "source": SOURCE_LABEL, "note": SOURCE_NOTE}


__all__ = ["CATALOG", "CATEGORIES", "SOURCE_LABEL", "SOURCE_NOTE",
           "CatalogPart", "CatalogQuery", "parse_query", "search",
           "suggest_for_hole"]
