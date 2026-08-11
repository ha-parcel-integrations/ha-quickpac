"""Config flow for the Quickpac parcel tracker integration."""
from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_REFRESH_INTERVAL,
    CONF_TRACKING_CODE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    REFRESH_INTERVAL_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)

# Digits only — confirmed via the OpenAPI document: ``sendungsNo`` is typed
# ``string`` on ``GetPublicTracking``/``CheckZip`` but ``integer($int64)`` on
# ``GetLiveTracking``/``GetToken``. The same identifier cannot be int64 on one
# route and alphanumeric on another, so Quickpac identcodes are digits, and
# ``IdentCodeFormatted`` exists purely because they are long enough to need
# grouping. No length bound is documented, so none is enforced here.
#
# Not ``QP...``: the ``"QP12345678"`` seen in a third-party CLI's README is a
# synthetic placeholder that contradicts the int64 typing and never resolved.
_TRACKING_CODE_RE = re.compile(r"^\d+$")


def normalize_tracking_code(value: str) -> str:
    """Return the tracking code with spaces and dots stripped.

    Quickpac identcodes are digits only — there is no case to fold, only the
    grouping separators (``IdentCodeFormatted``-style spaces/dots) a user
    might paste along with the code.
    """
    return re.sub(r"[ .]+", "", value or "")


def valid_tracking_code(value: str) -> bool:
    """Whether ``value`` looks like a Quickpac tracking code."""
    return bool(_TRACKING_CODE_RE.match(value))


def _current_parcels(entry: ConfigEntry) -> list[dict[str, str]]:
    """Return a mutable copy of the tracked parcels list."""
    return [dict(item) for item in entry.options.get(CONF_PARCELS, [])]


def _interval_selector() -> selector.SelectSelector:
    """Return the refresh-interval dropdown selector (options translated via strings)."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[str(m) for m in REFRESH_INTERVAL_OPTIONS],
            translation_key=CONF_REFRESH_INTERVAL,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


class QuickpacConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI-driven configuration flow for the Quickpac integration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> QuickpacOptionsFlowHandler:
        """Return the options flow handler."""
        return QuickpacOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the Quickpac hub — single instance, no input needed.

        Tracking is keyed on the tracking code alone (no account, no postal
        code), so there is nothing to ask at setup: the entry is created
        straight away and parcels are added afterwards via the options flow,
        the ``quickpac.track_parcel`` service or a dashboard button.
        ``single_config_entry`` in the manifest enforces one hub.

        A surname+postcode second factor exists (``POST GetToken``) but is
        deliberately not used — see CLAUDE.md's "Deliberate `None`s" section.
        """
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Quickpac",
            data={},
            options={
                CONF_PARCELS: [],
                CONF_DELIVERED_FILTER_TYPE: DEFAULT_DELIVERED_FILTER_TYPE,
                CONF_DELIVERED_FILTER_AMOUNT: DEFAULT_DELIVERED_FILTER_AMOUNT,
                CONF_REFRESH_INTERVAL: DEFAULT_REFRESH_INTERVAL,
                CONF_INCLUDE_HISTORY: DEFAULT_INCLUDE_HISTORY,
            },
        )


class QuickpacOptionsFlowHandler(OptionsFlow):
    """Manage tracked parcels, history and polling in one sectioned form.

    Mirrors the other suite carriers' section layout (here: ``parcels`` /
    ``delivered`` / ``history`` / ``polling``). Changes apply live via HA's
    options-update listener (which refreshes the coordinator), so new/removed
    per-parcel sensors appear and disappear immediately.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the single sectioned options form."""
        errors: dict[str, str] = {}
        parcels = _current_parcels(self.config_entry)

        if user_input is not None:
            parcels_section = user_input.get("parcels", {})
            delivered_section = user_input.get("delivered", {})
            history_section = user_input.get("history", {})
            polling_section = user_input.get("polling", {})

            # Remove first, then add — so re-adding a just-removed code works.
            to_remove = set(parcels_section.get("remove", []))
            parcels = [p for p in parcels if p[CONF_TRACKING_CODE] not in to_remove]

            add_code = normalize_tracking_code(parcels_section.get("add") or "")
            if add_code:
                if not valid_tracking_code(add_code):
                    errors["base"] = "invalid_tracking_code"
                elif any(p[CONF_TRACKING_CODE] == add_code for p in parcels):
                    errors["base"] = "already_tracked"
                else:
                    parcels.append({CONF_TRACKING_CODE: add_code})

            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_PARCELS: parcels,
                        CONF_DELIVERED_FILTER_TYPE: delivered_section[
                            CONF_DELIVERED_FILTER_TYPE
                        ],
                        CONF_DELIVERED_FILTER_AMOUNT: int(
                            delivered_section[CONF_DELIVERED_FILTER_AMOUNT]
                        ),
                        CONF_INCLUDE_HISTORY: bool(
                            history_section[CONF_INCLUDE_HISTORY]
                        ),
                        CONF_REFRESH_INTERVAL: int(
                            polling_section[CONF_REFRESH_INTERVAL]
                        ),
                    },
                )

        current = self.config_entry.options

        parcels_fields: dict[Any, Any] = {vol.Optional("add", default=""): str}
        if parcels:
            parcels_fields[vol.Optional("remove", default=[])] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=p[CONF_TRACKING_CODE],
                            label=p[CONF_TRACKING_CODE],
                        )
                        for p in parcels
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )

        schema = vol.Schema(
            {
                vol.Required("parcels"): section(
                    vol.Schema(parcels_fields), {"collapsed": False}
                ),
                vol.Required("delivered"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_DELIVERED_FILTER_TYPE,
                                default=current.get(
                                    CONF_DELIVERED_FILTER_TYPE,
                                    DEFAULT_DELIVERED_FILTER_TYPE,
                                ),
                            ): selector.SelectSelector(
                                selector.SelectSelectorConfig(
                                    options=["days", "parcels"],
                                    translation_key=CONF_DELIVERED_FILTER_TYPE,
                                    mode=selector.SelectSelectorMode.LIST,
                                )
                            ),
                            vol.Required(
                                CONF_DELIVERED_FILTER_AMOUNT,
                                default=current.get(
                                    CONF_DELIVERED_FILTER_AMOUNT,
                                    DEFAULT_DELIVERED_FILTER_AMOUNT,
                                ),
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(
                                    min=1, max=365, step=1, mode=selector.NumberSelectorMode.BOX
                                )
                            ),
                        }
                    ),
                    {"collapsed": True},
                ),
                vol.Required("history"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_INCLUDE_HISTORY,
                                default=current.get(
                                    CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
                                ),
                            ): selector.BooleanSelector(),
                        }
                    ),
                    {"collapsed": True},
                ),
                vol.Required("polling"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_REFRESH_INTERVAL,
                                # str(): selector option values are strings, so a
                                # stored int default trips "expected str" on submit.
                                default=str(
                                    current.get(
                                        CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL
                                    )
                                ),
                            ): _interval_selector(),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )

        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
