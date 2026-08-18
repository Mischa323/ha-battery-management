"""The Battery Management integration."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import BatteryCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
]

SERVICE_SET_SETPOINT = "set_setpoint"
SERVICE_START_FAST_CHARGE = "start_fast_charge"
SERVICE_STOP_FAST_CHARGE = "stop_fast_charge"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_SETPOINT = "setpoint"

_SERVICES_KEY = f"{DOMAIN}_services_registered"

_TARGET_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})
_SET_SETPOINT_SCHEMA = _TARGET_SCHEMA.extend(
    {vol.Required(ATTR_SETPOINT): vol.Coerce(float)}
)

CARD_FILENAME = "battery-management-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"
_CARD_KEY = f"{DOMAIN}_card_registered"


def _card_view(path: Path):
    """Serve the card with headers we control.

    Home Assistant's static-path helper offers two settings and neither is the
    one this needs: cache headers on means a month of hard caching, and cache
    headers off means *no* Cache-Control at all - at which point the browser
    falls back to heuristic caching and invents a lifetime from the file's
    modification date. That is what was serving stale copies of this card, and
    it is why two releases in a row tried to escape it by moving the URL.

    Moving the URL worked, and cost more than it saved: a new URL is a
    guaranteed cache miss on the first load after every update, and Lovelace
    does not wait for a module before it builds its cards. Losing that race is
    what puts "custom element doesn't exist" on the dashboard.

    `no-cache` was the previous attempt and it was worse than what it replaced.
    It does not mean "do not store", it means "revalidate before reusing" - so
    the browser has to reach the server before it may run the module, on every
    single page load. Lovelace does not wait for a module before it builds its
    cards, so that turns losing the race from a possibility into a certainty.
    Reported back from the primary site as "still broken", deterministically
    rather than now and then, which is what a mandatory round trip looks like.

    What works is what every card on that dashboard that *does* load uses: a
    URL that identifies the contents, cached hard because it then cannot go
    stale. The stamp is not a cache-buster bolted on, it is what makes
    `immutable` a true statement - different bytes are a different URL, so the
    stored copy is never wrong, and after the first load the module is there
    before Lovelace asks for it.

    The cost is one cache miss per release, on the first load after an update.
    That is the same deal HACS cards take, and it is the cheap half of the
    trade: one slow load against every load being slow.

    Built by a function rather than declared at import time:
    `homeassistant.components.http` is not importable in the stubbed test run,
    and the point of that run is that it needs no Home Assistant at all.
    """
    from homeassistant.components.http import HomeAssistantView

    class CardView(HomeAssistantView):
        """The card, served from one stable URL."""

        url = CARD_URL
        name = f"{DOMAIN}:card"
        # a `<script src>` carries no bearer token, and this file is the card's
        # own source, which ships publicly with the integration
        requires_auth = False

        async def get(self, request):
            """Hand it back, and say how long it may be trusted for."""
            from aiohttp import web

            return web.FileResponse(
                path,
                headers={
                    # explicit, because a guess is how a module ends up
                    # fetched and then silently never executed
                    "Content-Type": "text/javascript; charset=utf-8",
                    # safe precisely because the URL carries the file's hash
                    "Cache-Control": "public, max-age=31536000, immutable",
                },
            )

    return CardView()


async def _card_version(hass: HomeAssistant) -> str:
    """This release's version, for busting the browser's cache of the card.

    Read from the manifest Home Assistant has already loaded - opening the file
    again would be blocking I/O in the event loop for a query string.
    """
    try:
        from homeassistant.loader import async_get_integration

        integration = await async_get_integration(hass, DOMAIN)
        return str(integration.version or "0")
    except Exception:  # noqa: BLE001 - never block setup over a query string
        return "0"


async def _async_card_url(hass: HomeAssistant) -> str:
    """Where the card lives: the path, plus a hash of what is at it.

    Both registrations need it and either can go first - `frontend` and
    `lovelace` set up independently - so the recipe lives here and neither
    side can disagree about where the card is.

    The hash is not a cache-buster in the usual sense. It is what lets the
    response say `immutable` honestly: different bytes are a different URL, so
    a stored copy can never be the wrong one, and the browser is free to reuse
    it without asking. That is what gets the module defined before Lovelace
    builds its cards - see `_card_view` for the version of this that got it
    backwards.
    """
    version = await _card_version(hass)
    card_file = Path(__file__).parent / "www" / CARD_FILENAME

    def _digest() -> str:
        try:
            return hashlib.sha256(card_file.read_bytes()).hexdigest()[:12]
        except OSError:
            return "0"

    digest = await hass.async_add_executor_job(_digest)
    return f"{CARD_URL}?v={version}-{digest}"


async def _async_schedule_card(hass: HomeAssistant) -> None:
    """Register the card once the frontend exists - whenever that is.

    The ordering problem is real: on a cold boot this integration can be set
    up before the frontend, and a card offered to a frontend that has not
    initialised is simply lost. But making `frontend` a *hard* dependency,
    which was the first fix, is a worse trade - the batteries then stop being
    coordinated at all if the frontend fails to start, and a battery
    controller has no business depending on a web interface.

    `async_when_setup` is the middle: it fires immediately if the frontend is
    already up, and waits for it if it is not. If the frontend never comes,
    the card never registers and everything else carries on.
    """
    try:
        from homeassistant.setup import async_when_setup
    except ImportError:      # pragma: no cover - very old cores
        await _async_register_card(hass)
        return

    async def _ready(hass: HomeAssistant, _component: str) -> None:
        await _async_register_card(hass)

    async def _lovelace_ready(hass: HomeAssistant, _component: str) -> None:
        await _async_register_resource(hass)

    async_when_setup(hass, "frontend", _ready)
    async_when_setup(hass, "lovelace", _lovelace_ready)


async def _async_register_resource(hass: HomeAssistant) -> None:
    """Also register the card as a Lovelace *resource*, not just an extra module.

    These are not two ways of saying the same thing, and the difference is the
    whole bug. `add_extra_js_url` puts a `<script type="module">` on the page,
    which the browser loads whenever it gets round to it - Lovelace does not
    wait for it. Resources it does wait for, before it builds any card.

    So on a cold load the module wins the race and everything is fine, and on
    an ordinary reload the dashboard comes out of cache, renders immediately,
    finds no `battery-management-card` registered yet and draws an error card
    in its place. Reported from the primary site as "fine after Ctrl+Shift+R,
    broken on F5", on desktop and phone alike, and confirmed by hand: adding
    the resource made it stop. Every other custom card on that dashboard was
    already a resource, which is why ours was the only one failing.

    The extra module stays as well. Loading twice is harmless - `defineCard`
    checks `customElements.get` first, and `double_load.mjs` pins that - and it
    is the only mechanism left if this one cannot run, which is a real case:
    a YAML-mode dashboard has no writable resource collection at all.
    """
    report = hass.data.get(_CARD_KEY) or {}
    url = report.get("offered_to_frontend") or await _async_card_url(hass)
    try:
        data = hass.data.get("lovelace")
        resources = getattr(data, "resources", None)
        if resources is None and isinstance(data, dict):
            resources = data.get("resources")
        if resources is None:
            report["resource"] = "no_lovelace"
            return
        # YAML-mode dashboards have a read-only collection: the user declares
        # resources in their own file, and writing there is not ours to do.
        if not hasattr(resources, "async_create_item"):
            report["resource"] = "yaml_mode"
            _LOGGER.info(
                "Lovelace is in YAML mode, so the card cannot be registered as "
                "a resource automatically. Add %s as a module under "
                "`lovelace: resources:` if cards intermittently fail to load.",
                url,
            )
            return
        # Home Assistant's own accessor, because it sets `loaded` as well as
        # reading the file. Calling `async_load()` directly leaves the flag
        # False, so the collection reloads itself from disk again on the next
        # access - and every reload re-announces every item as newly created.
        if hasattr(resources, "_async_ensure_loaded"):
            await resources._async_ensure_loaded()
        elif hasattr(resources, "loaded") and not resources.loaded:
            await resources.async_load()
            resources.loaded = True

        # Match on the path and ignore the query, so the cache-busting stamp
        # can change without leaving a second stale entry behind - and so a
        # hand-added one gets adopted instead of duplicated.
        mine = [
            item
            for item in resources.async_items()
            if str(item.get("url", "")).split("?")[0] == CARD_URL
        ]
        report["resource_urls"] = [str(item.get("url", "")) for item in mine]
        report["resource_type"] = mine[0].get("type") if mine else None

        if not mine:
            await resources.async_create_item({"res_type": "module", "url": url})
            report["resource"] = "created"
        else:
            # Exactly one entry, and it is ours. A second one pointing at the
            # same file is not harmless bookkeeping: the browser imports the
            # module twice, and the two copies race to define the same custom
            # elements. `defineCard` survives that, but nothing downstream of a
            # half-registered pair is worth relying on - and a hand-added entry
            # from before this existed is exactly how a site ends up with two.
            for stale in mine[1:]:
                await resources.async_delete_item(stale["id"])
            if len(mine) > 1:
                report["resource_removed"] = len(mine) - 1
                _LOGGER.warning(
                    "Removed %s duplicate Lovelace resource(s) for the card. "
                    "Reload the page once; the cards may show an error until "
                    "you do.",
                    len(mine) - 1,
                )
            if mine[0].get("url") != url:
                await resources.async_update_item(mine[0]["id"], {"url": url})
                report["resource"] = "updated"
            else:
                report["resource"] = "present"

        # A resource whose type is not "module" is fetched and then ignored,
        # and the only symptom is "custom element doesn't exist" - the same
        # message as half a dozen unrelated causes. Say so here instead.
        if report.get("resource_type") not in (None, "module"):
            _LOGGER.warning(
                "The Lovelace resource for the card is registered as %r rather "
                "than 'module', so the browser will never execute it. Remove it "
                "under Settings > Dashboards > Resources and restart.",
                report["resource_type"],
            )

        _LOGGER.debug("card resource %s: %s", report["resource"], url)
    except Exception as err:  # noqa: BLE001 - a dashboard nicety, never a blocker
        report["resource"] = f"error: {err}"
        _LOGGER.warning(
            "Could not register the card as a Lovelace resource (%s). Cards may "
            "show an error until the page is hard-refreshed; adding %s manually "
            "under Settings > Dashboards > Resources fixes that.",
            err,
            url,
        )


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the Lovelace card and add it as an extra frontend module (once).

    Every step says what it did. "Custom element doesn't exist" is reported
    from the browser, and until now nothing on the server side said whether
    the file had been served, which version, or whether it had been offered to
    the frontend at all - so the only way to investigate was to guess.

    The outcome is stashed in `hass.data` and travels with the diagnostics, so
    the question "is the card registered" has an answer that does not depend on
    anyone having had debug logging switched on at the right moment.
    """
    if hass.data.get(_CARD_KEY):
        return
    card_file = Path(__file__).parent / "www" / CARD_FILENAME
    card_path = str(card_file)
    version = await _card_version(hass)
    report: dict = {"path": card_path, "url": CARD_URL, "version": version}
    hass.data[_CARD_KEY] = report

    # A missing file registers perfectly happily and then 404s, which reaches
    # the user as "custom element doesn't exist" - the same message as a dozen
    # unrelated causes. Check it here, where the path is known.
    if not await hass.async_add_executor_job(card_file.is_file):
        report["error"] = "file_missing"
        _LOGGER.error(
            "The Battery Management card is missing from %s, so no card can load. "
            "Reinstall the integration through HACS.",
            card_path,
        )
        return

    def _fingerprint() -> tuple[int, str]:
        """Size and a short content hash, in one trip to the disk."""
        data = card_file.read_bytes()
        return len(data), hashlib.sha256(data).hexdigest()[:12]

    report["bytes"], report["fingerprint"] = await hass.async_add_executor_job(
        _fingerprint
    )

    try:
        hass.http.register_view(_card_view(card_file))
        report["served"] = "view"
    except Exception as err:  # noqa: BLE001  -- fall back to a plain static path
        try:
            from homeassistant.components.http import StaticPathConfig

            await hass.http.async_register_static_paths(
                [StaticPathConfig(CARD_URL, card_path, False)]
            )
            report["served"] = "static"
        except Exception as err2:  # noqa: BLE001
            report["error"] = f"serving: {err2}"
            _LOGGER.warning(
                "Could not serve the Battery Management card (%s; static path also "
                "failed: %s). No card will load.",
                err,
                err2,
            )
            return

    try:
        from homeassistant.components.frontend import add_extra_js_url

        # The URL carries a hash of the file, and the response says
        # `immutable`. The two only work together, and this repo has now had
        # each half without the other.
        #
        # Stamped URL, no cache headers: the browser invents its own lifetime
        # (heuristic caching), which served week-old copies of this card.
        # Stable URL, `no-cache`: never stale, but the browser must reach the
        # server before it may run the module - on every load - and Lovelace
        # does not wait for a module before building its cards. That made
        # losing the race certain rather than occasional, which is what
        # "still broken, every reload" from the primary site meant.
        #
        # Together they are what the cards that do load on that same
        # dashboard use: content in the URL, so a cached copy cannot be the
        # wrong one, and caching hard enough that after the first load the
        # module is already there when Lovelace asks.
        url = f"{CARD_URL}?v={version}-{report['fingerprint']}"
        # As a *classic* script, not a module, and this is the point of the
        # whole exercise. A module is deferred by specification: the browser
        # runs it after the document is parsed, which is after Home Assistant
        # has booted and started building cards. Lovelace does not wait, so
        # the elements get defined moments too late and every card on the page
        # is already an error card. Reported from the primary site as
        # "Custom element not found" out of create-element-base.ts, on every
        # load, deterministically - which is what a losing race looks like
        # when the timing is not actually close.
        #
        # A classic script is not deferred. It executes while the page is
        # being parsed, so `customElements.define` has run before anything
        # asks for a card. The card file has no import or export in it, so it
        # is a valid classic script as it stands - and `defineCard` is
        # idempotent, so the Lovelace resource importing it again afterwards
        # costs nothing.
        try:
            add_extra_js_url(hass, url, es5=True)
            report["offered_as"] = "script"
        except TypeError:
            # older cores take two arguments and only offer the module route
            add_extra_js_url(hass, url)
            report["offered_as"] = "module"
        report["offered_to_frontend"] = url
        _LOGGER.info(
            "Battery Management card registered: %s (%s bytes, version %s, "
            "served %s). The URL is stable and the response is sent "
            "no-cache, so the browser revalidates instead of guessing.",
            CARD_URL,
            report.get("bytes"),
            version,
            report.get("served"),
        )
    except Exception as err:  # noqa: BLE001
        report["error"] = f"add_extra_js_url: {err}"
        _LOGGER.warning(
            "Could not auto-add the card resource (%s); add %s manually under "
            "Settings > Dashboards > Resources",
            err,
            CARD_URL,
        )


def _targets(hass: HomeAssistant, call: ServiceCall) -> list[BatteryCoordinator]:
    """Coordinators a service call applies to; all of them unless narrowed."""
    stored: dict = hass.data.get(DOMAIN, {})
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if entry_id is None:
        return list(stored.values())
    coordinator = stored.get(entry_id)
    return [coordinator] if coordinator else []


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register the domain services once, not per config entry."""
    if hass.data.get(_SERVICES_KEY):
        return

    async def _set_setpoint(call: ServiceCall) -> None:
        for coordinator in _targets(hass, call):
            await coordinator.async_set_setpoint(call.data[ATTR_SETPOINT])

    async def _start_fast_charge(call: ServiceCall) -> None:
        for coordinator in _targets(hass, call):
            await coordinator.async_set_fast_charge(True)

    async def _stop_fast_charge(call: ServiceCall) -> None:
        for coordinator in _targets(hass, call):
            await coordinator.async_set_fast_charge(False)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_SETPOINT, _set_setpoint, schema=_SET_SETPOINT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_START_FAST_CHARGE, _start_fast_charge, schema=_TARGET_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_FAST_CHARGE, _stop_fast_charge, schema=_TARGET_SCHEMA
    )
    hass.data[_SERVICES_KEY] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Management from a config entry."""
    await _async_schedule_card(hass)
    _async_register_services(hass)

    coordinator = BatteryCoordinator(hass, entry)
    await coordinator.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and revert the batteries to a safe mode."""
    coordinator: BatteryCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        await coordinator.async_stop(revert=True)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
