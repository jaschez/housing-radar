"""Hard filter and geographic guard: what decides what reaches the evaluation."""
import pytest

import filters


def flat(**kw):
    base = {"operation": "sale", "price": 150000, "area_m2": 90, "rooms": 3, "property_type": "flat",
            "elevator": 1, "title": "Piso en Granada", "description": "Luminoso, exterior."}
    return {**base, **kw}


# --- geographic guard ------------------------------------------------------------------

def test_same_name_in_another_region_does_not_get_in(cfg):
    # One portal resolved "granada" to La Granada (Barcelona) on 2026-10-10.
    area, why = filters.zone_of("Barcelona", "La Granada", cfg)
    assert area is None and "region" in why


def test_accents_and_case(cfg):
    assert filters.zone_of("Sevilla", "Alcalá de Guadaíra", cfg) == ("sevilla", "Alcala de Guadaira")
    assert filters.zone_of("Granada", "Churriana de La Vega", cfg) == ("granada", "Churriana de la Vega")


def test_alias_for_a_neighbourhood_used_as_locality(cfg):
    assert filters.zone_of("Sevilla", "Montequinto", cfg) == ("sevilla", "Dos Hermanas")


def test_locality_of_the_region_not_listed(cfg):
    area, why = filters.zone_of("Granada", "Almuñécar", cfg)
    assert area is None and "not listed" in why


def test_locality_listed_under_another_region_does_not_slip_in(cfg):
    # "Camas" is listed under Sevilla; under Granada it is not valid.
    assert filters.zone_of("Granada", "Camas", cfg)[0] is None


# --- hard filter -------------------------------------------------------------------------

def test_a_matching_flat_passes(cfg):
    r = filters.hard_filter(flat(), cfg)
    assert r["passes"] == 1 and r["filter_reason"] is None and r["to_confirm"] is None


@pytest.mark.parametrize("kw,reason", [
    ({"price": 205000}, "price"),
    ({"operation": "rent", "price": 700}, "price"),
    ({"operation": "rent", "price": 250}, "rent below floor"),
    ({"area_m2": 60}, "area"),
    ({"rooms": 1}, "rooms"),
    ({"property_type": "studio"}, "excluded type"),
    ({"property_type": "loft"}, "excluded type"),
    ({"price": None}, "no price"),
    ({"occupied": True}, "occupied"),
    ({"bare_ownership": True}, "bare ownership"),
    ({"auction": True}, "auction"),
    ({"temporary": True}, "temporary"),
    ({"elevator": 0}, "no elevator"),
])
def test_each_criterion_discards(cfg, kw, reason):
    r = filters.hard_filter(flat(**kw), cfg)
    assert r["passes"] == 0
    assert reason in r["filter_reason"]


def test_missing_data_does_not_discard_it_goes_to_confirm(cfg):
    r = filters.hard_filter(flat(area_m2=None, rooms=None, elevator=None), cfg)
    assert r["passes"] == 1
    assert r["to_confirm"] == "area, rooms, elevator"


def test_a_house_without_elevator_is_normal(cfg):
    r = filters.hard_filter(flat(property_type="house", elevator=None, description="Casa de dos plantas sin ascensor"),
                            cfg)
    assert r["passes"] == 1
    assert r["to_confirm"] is None


def test_elevator_requirement_can_be_switched_off(cfg):
    r = filters.hard_filter(flat(elevator=0), {**cfg, "require_elevator": False})
    assert r["passes"] == 1


@pytest.mark.parametrize("text,reason", [      # real listing texts
    ("Se busca, compañero/a en Sevilla - Bellavista", "temporary or room rental"),
    ("HABITACIONES PARA ESTUDIANTES EN BARRIADA FELIPE II", "temporary or room rental"),
    ("Se alquila habitación en piso compartido a estudiantes", "temporary or room rental"),
    ("Se buscan 2 compañeras de piso en este fantástico inmueble", "temporary or room rental"),
    ("Hola es una casa con cuatro habitaciones tres baños se alquila por habitac", "temporary or room rental"),
    ("1er. Piso sin ascencor, orientación sureste", "no elevator"),       # a real typo
    ("Vivienda okupada, venta como inversión", "occupied"),
    ("Venta sin posesión - alquilado. No se puede visitar", "occupied"),
    ("Chalet en cesión de remate, sin visita ni hipoteca", "auction"),
    ("¡¡YA ESTA ALQUILADO!!", "already rented"),
    ("Necesita reforma integral", "needs renovation"),
])
def test_text_exclusions(cfg, text, reason):
    assert filters.hard_filter(flat(description=text), cfg)["filter_reason"] == reason


@pytest.mark.parametrize("text", [
    "Piso que no necesita reforma, entrar a vivir",
    "No es alquiler de temporada: contrato de larga duración",
    "Mobiliario contemporáneo de diseño",           # "temporal" does not match inside a word
])
def test_negations_and_substrings_do_not_discard(cfg, text):
    assert filters.hard_filter(flat(description=text), cfg)["passes"] == 1


def test_norm_and_words():
    assert filters.norm("  Huétor   VEGA ") == "huetor vega"
    assert filters.words("Se busca, compañero/a") == " se busca companero a "
