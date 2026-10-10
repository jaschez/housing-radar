"""E-mail strings and number formatting. `language` in the config picks the table (en, es).

To add a language, copy the "en" table, translate the values and keep the keys.
"""
from __future__ import annotations

STRINGS = {
    "en": {
        "type.flat": "Flat", "type.penthouse": "Penthouse", "type.duplex": "Duplex", "type.studio": "Studio",
        "type.loft": "Loft", "type.house": "House", "type.detached": "Detached house", "type.other": "Home",
        "op.sale": "for sale", "op.rent": "for rent",
        "confirm.area": "floor area", "confirm.rooms": "rooms", "confirm.elevator": "elevator",
        "elevator.1": "elevator", "elevator.0": "no elevator", "elevator.none": "elevator ?",
        "rooms": "{n} bed", "baths": "{n} bath", "floor": "floor {f}",
        "vs_zone": "({pct:+.0f} % vs {kind} in {zone})", "kind.flat": "flats", "kind.house": "houses",
        "anomaly": "ANOMALOUS PRICE",
        "zone": "Area", "value": "Value", "flags": "Watch out", "to_confirm": "To confirm",
        "daily.subject": "Housing radar {day}: {n} matching", "daily.subject_high": " ({n} strong)",
        "daily.subject_drops": ", {n} price drop(s)", "daily.subject_gone": ", {n} withdrawn",
        "daily.intro": ("Today's sweep: {seen}. New listings passing the filter: {new}. "
                        "Evaluated today: {evaluated} ({queue} left in the queue)."),
        "daily.no_data": "no data",
        "daily.matching": "Matching", "daily.drops": "Price drops", "daily.gone": "Withdrawn (were matching)",
        "daily.errors": "Sources with problems today", "per_month": "/month",
        "watch.subject": "Housing: {n} new — {where}",
        "watch.intro": ("New listings that pass the hard filter (budget, floor area, rooms, type and "
                        "exclusions). Not evaluated yet."),
        "watch.more": "…and {n} more with lower scores; they reach the daily e-mail if the evaluation likes them.",
        "footer": ("Housing radar. Full sweep with evaluation once a day; quick alerts during the day. "
                   "Criteria in the workspace config.yaml."),
    },
    "es": {
        "type.flat": "Piso", "type.penthouse": "Ático", "type.duplex": "Dúplex", "type.studio": "Estudio",
        "type.loft": "Loft", "type.house": "Casa", "type.detached": "Chalet", "type.other": "Inmueble",
        "op.sale": "en venta", "op.rent": "en alquiler",
        "confirm.area": "superficie", "confirm.rooms": "habitaciones", "confirm.elevator": "ascensor",
        "elevator.1": "con ascensor", "elevator.0": "sin ascensor", "elevator.none": "ascensor ?",
        "rooms": "{n} hab.", "baths": "{n} baños", "floor": "planta {f}",
        "vs_zone": "({pct:+.0f} % vs {kind} de {zone})", "kind.flat": "pisos", "kind.house": "casas",
        "anomaly": "PRECIO ANÓMALO",
        "zone": "Zona", "value": "Calidad-precio", "flags": "Ojo", "to_confirm": "Por confirmar",
        "daily.subject": "Radar de vivienda {day}: {n} con encaje", "daily.subject_high": " ({n} alto)",
        "daily.subject_drops": ", {n} bajada(s)", "daily.subject_gone": ", {n} retirado(s)",
        "daily.intro": ("Barrido de hoy: {seen}. Nuevos que pasan el filtro: {new}. "
                        "Evaluados hoy: {evaluated} (quedan {queue} en cola)."),
        "daily.no_data": "sin datos",
        "daily.matching": "Con encaje", "daily.drops": "Bajadas de precio", "daily.gone": "Retirados (tenían encaje)",
        "daily.errors": "Fuentes con problemas hoy", "per_month": "/mes",
        "watch.subject": "Vivienda: {n} nuevo(s) — {where}",
        "watch.intro": ("Nuevos anuncios que cumplen el filtro duro (presupuesto, m², habitaciones, tipo y "
                        "exclusiones). Sin evaluar todavía."),
        "watch.more": "…y {n} más con menos puntuación; saldrán evaluados en el diario si tienen encaje.",
        "footer": ("Radar de vivienda. Barrido completo con evaluación una vez al día; avisos rápidos durante "
                   "el día. Criterios en el config.yaml del workspace."),
    },
}

LANGUAGE_NAMES = {"en": "English", "es": "Spanish (Spain)"}
CURRENCY_SYMBOLS = {"EUR": "€", "GBP": "£", "USD": "$"}


class T:
    def __init__(self, cfg: dict):
        self.lang = cfg.get("language", "en") if cfg.get("language", "en") in STRINGS else "en"
        self.currency = cfg.get("currency", "EUR")

    def __call__(self, key: str, **kw) -> str:
        s = STRINGS[self.lang].get(key) or STRINGS["en"].get(key, key)
        return s.format(**kw) if kw else s

    def number(self, v: float, decimals: int = 0) -> str:
        s = f"{v:,.{decimals}f}"
        if self.lang == "es":        # 1.234,5
            s = s.replace(",", "\0").replace(".", ",").replace("\0", ".")
        return s

    def money(self, v, operation: str | None = None) -> str:
        if v is None:
            return "?"
        sym = CURRENCY_SYMBOLS.get(self.currency, self.currency)
        s = f"{self.number(v)} {sym}" if self.lang == "es" else f"{sym}{self.number(v)}"
        return s + (self("per_month") if operation == "rent" else "")
