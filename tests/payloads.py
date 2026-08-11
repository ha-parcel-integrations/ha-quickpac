"""Sample Quickpac API payloads shared by the test modules.

Shapes follow ``TrackingInfoResponse`` as pinned by Quickpac's own OpenAPI
document (``carrier-research/api/quickpac/swagger-v1.json``) and the status
vocabulary reverse-engineered from the tracking SPA's own icon-selection
logic (``carrier-research/api/quickpac/tracking.md``). **No populated ``200``
has ever been observed on the wire** — every value below is
schema-conformant, not carrier-confirmed. Keep them in one module rather than
inline in each test — when a real payload turns up, there is then exactly one
place to fix.
"""
from __future__ import annotations

ACTIVE_CODE = "990000123456"
DELIVERED_CODE = "990000654321"


def event(
    status: int,
    time: str,
    text: str,
    *,
    group: str = "Info",
    need_token: bool = False,
) -> dict:
    """One entry of Quickpac's own ``Protocol`` timeline."""
    return {
        "Time": time,
        "Status": status,
        "StatusText": text,
        "StatusGroup": group,
        "NeedToken": need_token,
        "TokenExtraInfo": None,
        "TokenExtraInfoData": None,
    }


def _formatted(code: str) -> str:
    """Mimic Quickpac's grouped display form (``IdentCodeFormatted``)."""
    return " ".join(code[i : i + 4] for i in range(0, len(code), 4))


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A representative response for a delivered parcel (status 21xx)."""
    return {
        "IdentCode": code,
        "IdentCodeFormatted": _formatted(code),
        "LastStatus": "Zugestellt",
        "LastStatusCode": 2100,
        "StatusTooltipText": "Ihre Sendung wurde zugestellt.",
        "Product": "Tempo",
        "ProductTooltipText": "Tempo",
        "FAQTag": None,
        "OutlookHeadline": None,
        "OutlookInfo": [],
        "PostTrackingUrl": None,
        "PlanzerTrackingUrl": None,
        "ForceForwardUrl": None,
        "QuickpicOrderUrl": None,
        "QuickpicReverseTrackingUrl": None,
        "QuickpicIsOrigin": False,
        "QuickpicHasOrder": False,
        "Protocol": [
            event(1000, "2026-04-27T23:03:58", "Sendung angekündigt", group="Announcement"),
            event(1401, "2026-04-28T15:52:17", "In Bearbeitung", group="Prepare"),
            event(1801, "2026-04-29T08:46:00", "In Zustellung", group="Delivery"),
            event(2100, "2026-04-29T13:12:42", "Zugestellt", group="Delivered"),
        ],
        "ServiceOptions": [0, 1, 2, 3],
        "TokenInfos": None,
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An out-for-delivery parcel (status 18xx), not yet delivered."""
    sample = delivered_sample(code)
    sample.update(
        {
            "LastStatus": "In Zustellung",
            "LastStatusCode": 1801,
            "StatusTooltipText": "Ihre Sendung ist unterwegs zu Ihnen.",
            "Protocol": sample["Protocol"][:3],
        }
    )
    return sample


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel waiting at a Coop Pick-up point (status 2600)."""
    sample = active_sample(code)
    sample.update(
        {
            "LastStatus": "Bereit zur Abholung",
            "LastStatusCode": 2600,
            "StatusTooltipText": "Ihre Sendung ist abholbereit.",
            "Protocol": sample["Protocol"]
            + [event(2600, "2026-04-29T09:30:00", "Bereit zur Abholung", group="Pickup")],
        }
    )
    return sample
