"""Sample Correos API payloads shared by the test modules.

These reproduce the ``localizador`` shipment envelope: a shipment object with
an ``error`` block (``codError == "0"`` on success) and an ``eventos`` list that
runs **oldest → newest**. Each event splits its timestamp across ``fecEvento``
(``DD/MM/YYYY``) and ``horEvento`` (``HH:MM:SS``) in Europe/Madrid time.

NOTE: the endpoint and the ``codError`` envelope were verified live, but this
*success* shape (the ``eventos`` fields and the event codes) is **reconstructed**
from the 2021 community integration ``rikman122/homeassistant-correos_spain``
and is unverified against a real parcel. When a real ES parcel arrives, this is
the one module to correct — see TODO.md.
"""
from __future__ import annotations

from datetime import datetime

# Realistic-shaped Correos codes (UPU S10 / Paq), distinct per sample.
ACTIVE_CODE = "PQ0011223344ES"
DELIVERED_CODE = "PQ9988776655ES"


def event(cod_evento: str, timestamp: str, resumen: str) -> dict:
    """One Correos ``eventos`` entry, built from an ISO timestamp.

    Correos splits date and time into ``fecEvento`` / ``horEvento``; deriving
    them from an ISO string lets the tests keep passing familiar timestamps. An
    unparseable ISO string yields a deliberately unparseable ``fecEvento`` so
    the "malformed event is dropped" path stays exercised.
    """
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        fecha = parsed.strftime("%d/%m/%Y")
        hora = parsed.strftime("%H:%M:%S")
    except ValueError:
        fecha, hora = "not-a-date", None
    return {
        "codEvento": cod_evento,
        "fecEvento": fecha,
        "horEvento": hora,
        "unidad": "CTA MADRID",
        "desTextoResumen": resumen,
        "desTextoAmpliado": resumen,
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A representative ``localizador`` envelope for a delivered parcel."""
    return {
        "codEnvio": code,
        "nombre_cliente": "Jane Doe",
        "peso": "1500",
        "largo": "30",
        "alto": "10",
        "ancho": "20",
        "fec_admision": "27/04/2026 23:03:58",
        "fec_entregasum": "29/04/2026",
        "error": {"codError": "0", "desError": "OK"},
        "eventos": [
            event("A090000V", "2026-04-27T23:03:58Z", "Admitido"),
            event("P040000V", "2026-04-28T15:52:17Z", "Clasificado"),
            event("H020000V", "2026-04-29T08:46:00Z", "En reparto"),
            event("I010000V", "2026-04-29T13:12:42Z", "Entregado"),
        ],
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An out-for-delivery parcel (latest event ``En reparto``)."""
    sample = delivered_sample(code)
    # Drop the final "Entregado" event so the latest state is out-for-delivery.
    sample["fec_entregasum"] = None
    sample["eventos"] = sample["eventos"][:-1]
    return sample


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel waiting for collection at a Correos office."""
    sample = active_sample(code)
    pickup = event("L010000V", "2026-04-29T09:30:00Z", "En oficina")
    pickup["unidad"] = "OFICINA MADRID CENTRAL"
    sample["eventos"] = sample["eventos"][:-1] + [pickup]
    return sample
