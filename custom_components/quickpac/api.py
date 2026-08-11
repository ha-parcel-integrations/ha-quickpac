"""Quickpac public tracking API client.

Two calls: ``GetPublicTracking`` (the parcel lookup) and
``GetTrackingStartup`` (a parameter-free liveness canary). Full mechanics:
``carrier-research/api/quickpac/``.

Keyless: no key, no cookie, no CSRF token, no ``Origin``/``Referer``
requirement — confirmed at the source (the tracking SPA's own axios client
never attaches an ``Authorization`` header on the public path; that header
only appears when a staff login is present).

The API answers a bare ``400 problem+json`` for *every* unknown or
not-yet-registered tracking code, with no ``Detail`` field — so a wrong number
and a wrong URL are indistinguishable from the 400 alone. ``GetTrackingStartup``
never needs a tracking code and answers ``200`` as long as the host and base
path are correct, so every 400 on the parcel route is paired with one canary
call: canary 200 → the tracking code really is unknown/not yet scanned, report
it as pending; canary non-200 → the *route* itself is broken, raise rather
than silently reporting every tracked parcel as pending.

The contract the coordinator relies on:

* ``async_get_parcel`` returns the raw ``TrackingInfoResponse`` dict on
  success,
* returns ``None`` when the code is unknown or not yet registered (a normal,
  expected state — a freshly announced parcel is indistinguishable from a
  typo without the canary),
* raises :class:`QuickpacApiError` for anything else — including a 400 whose
  canary also fails,
* lets ``aiohttp.ClientError`` propagate untouched — ``DataUpdateCoordinator``
  already wraps those into ``UpdateFailed``.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import TRACKING_API_URL, TRACKING_STARTUP_URL

_LOGGER = logging.getLogger(__name__)


class QuickpacApiError(Exception):
    """Raised when a Quickpac API call returns an unexpected response."""

    def __init__(self, detail: str) -> None:
        """Store the detail that triggered the error."""
        super().__init__(f"Quickpac API request failed: {detail}")
        self.detail = detail


class QuickpacApiClient:
    """Client for the public Quickpac tracking endpoint."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session
        # One-shot: the canary runs on every 400 (it's cheap and this is the
        # only way to tell "unknown code" from "broken route"), but a
        # sustained outage should not re-log the same WARNING every poll.
        self._canary_failure_logged = False

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one shipment's tracking details.

        Returns the ``TrackingInfoResponse`` dict for a known number. A 400
        is never trusted at face value — the liveness canary decides what it
        means: canary success → ``None`` (unknown or not-yet-registered, the
        coordinator's normal pending state); canary failure → raise, because
        the real cause is then the route or host, not the tracking code. Any
        other non-200 status, or a body that is not a JSON object, also
        raises.
        """
        url = TRACKING_API_URL.format(tracking_code=tracking_code)
        async with self._session.get(url) as response:
            if response.status == 400:
                if await self._async_check_liveness():
                    return None
                raise QuickpacApiError(
                    "HTTP 400 and the liveness canary also failed — the host "
                    "or route likely moved, not just an unknown tracking code"
                )
            if response.status != 200:
                raise QuickpacApiError(f"HTTP {response.status}")
            try:
                # content_type=None: consumer endpoints routinely serve JSON as
                # text/plain, and aiohttp would otherwise refuse to parse it.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise QuickpacApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise QuickpacApiError("unexpected body (not a JSON object)")
        return payload

    async def _async_check_liveness(self) -> bool:
        """Call the parameter-free canary; ``True`` when the host/route are fine.

        Logged once per failure streak rather than on every poll — a real
        outage is already visible via ``UpdateFailed`` on the coordinator, so
        repeating the same WARNING every cycle would just be noise.
        """
        try:
            async with self._session.get(TRACKING_STARTUP_URL) as response:
                alive = response.status == 200
        except aiohttp.ClientError:
            alive = False

        if alive:
            self._canary_failure_logged = False
            return True
        if not self._canary_failure_logged:
            self._canary_failure_logged = True
            _LOGGER.warning(
                "Quickpac liveness canary (GetTrackingStartup) failed right "
                "after a 400 on the tracking route — the host or base path "
                "may have moved, not just an unknown tracking code"
            )
        return False
