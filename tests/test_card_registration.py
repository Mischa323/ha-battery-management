"""Serving the Lovelace card, and saying so.

"Custom element doesn't exist" is a browser message with a dozen possible
causes on the server: the file was never copied, the static path was never
registered, the frontend was not up yet, the browser is holding an old copy.
Nothing on the server side distinguished them, so the only way to investigate
was to guess - twice, wrongly.

These tests pin the reporting, not the plumbing: whichever way it goes, the
server must be able to say which way it went.
"""
from __future__ import annotations

import pytest

from custom_components.battery_management import (
    CARD_FILENAME,
    CARD_URL,
    _CARD_KEY,
    _async_register_card,
)

pytestmark = pytest.mark.asyncio


class FakeHttp:
    def __init__(self, fail: bool = False) -> None:
        self.registered: list = []
        self.views: list = []
        self.fail = fail

    def register_view(self, view):
        if self.fail:
            raise RuntimeError("no views here")
        self.views.append(view)

    async def async_register_static_paths(self, configs):
        if self.fail:
            raise RuntimeError("no static paths here")
        self.registered.extend(configs)

    def register_static_path(self, url, path, cache):
        if self.fail:
            raise RuntimeError("nor here")
        self.registered.append((url, path, cache))


class CardHass:
    """Just enough Home Assistant for the registration path."""

    def __init__(self, http_fails: bool = False) -> None:
        self.data: dict = {}
        self.http = FakeHttp(http_fails)

    async def async_add_executor_job(self, func, *args):
        return func(*args)


@pytest.fixture
def offered(monkeypatch):
    """Capture what gets handed to the frontend.

    The suite runs both against real Home Assistant and against the stub, and
    the stub has no `homeassistant.components` at all - so stand one up rather
    than skipping. What is being tested is our reporting either way.
    """
    import sys
    import types

    seen: list[str] = []
    try:
        import homeassistant.components.frontend as frontend
    except ImportError:
        components = sys.modules.setdefault(
            "homeassistant.components", types.ModuleType("homeassistant.components")
        )
        frontend = types.ModuleType("homeassistant.components.frontend")
        monkeypatch.setitem(sys.modules, "homeassistant.components.frontend", frontend)
        monkeypatch.setattr(components, "frontend", frontend, raising=False)
    monkeypatch.setattr(
        frontend,
        "add_extra_js_url",
        # the real one takes es5=; a fake that does not would silently push
        # this back onto the deferred module route
        lambda hass, url, es5=False: seen.append(url),
        raising=False,
    )
    return seen


async def test_it_reports_exactly_what_it_registered(offered):
    hass = CardHass()

    await _async_register_card(hass)

    report = hass.data[_CARD_KEY]
    assert report["url"] == CARD_URL
    assert report["path"].endswith(CARD_FILENAME)
    assert report["bytes"] > 0, "served an empty file without noticing"
    assert report["served"] in {"view", "static"}
    assert report["offered_to_frontend"].startswith(f"{CARD_URL}?v=")
    assert "error" not in report


async def test_it_reads_back_which_frontend_list_it_landed_in(offered):
    """Asking, rather than assuming it worked.

    `es5=True` is supposed to put the file on the page as a plain script
    instead of a deferred module, and the entire argument for that change
    rests on it having happened. Four fixes were shipped at this problem on
    reasoning alone; this one reports what is actually there, and it shows up
    in the diagnostics without anyone having to open a browser console.
    """
    from custom_components.battery_management import CARD_FILENAME

    hass = CardHass()
    hass.data["frontend_extra_js_url_es5"] = {f"/x/{CARD_FILENAME}?v=1"}
    hass.data["frontend_extra_module_url"] = {"/hacsfiles/other/other.js"}

    await _async_register_card(hass)

    lists = hass.data[_CARD_KEY]["frontend_lists"]
    assert lists["script"] == [f"/x/{CARD_FILENAME}?v=1"]
    # somebody else's card must never be reported as ours
    assert lists["module"] == []


async def test_the_card_is_offered_as_a_classic_script(offered):
    """A module is deferred by specification, and that is the whole bug.

    Deferred means the browser runs it after the document is parsed, which is
    after Home Assistant has booted and started building cards - and Lovelace
    does not wait. Every card on the page is an error card by then, which is
    exactly what the primary site reported: "Custom element not found", on
    every load, deterministically rather than now and again.

    A classic script runs during parsing, so the elements exist before
    anything asks for them. The card file has no import or export in it, so it
    is already valid as one.
    """
    hass = CardHass()

    await _async_register_card(hass)

    assert hass.data[_CARD_KEY]["offered_as"] == "script"


async def test_the_url_identifies_the_contents(offered):
    """The hash is what makes `immutable` an honest thing to say.

    Not a cache-buster bolted on: different bytes are a different URL, so a
    stored copy can never be the wrong one - which is what lets the response
    tell the browser to reuse it without asking, and that is what gets the
    module defined before Lovelace builds its cards.
    """
    from custom_components.battery_management import CARD_URL

    hass = CardHass()

    await _async_register_card(hass)

    assert offered, "nothing was offered to the frontend at all"
    assert offered[0].startswith(f"{CARD_URL}?v=")
    assert hass.data[_CARD_KEY]["fingerprint"] in offered[0]


async def test_the_card_is_served_with_headers_that_forbid_guessing(offered):
    """Two headers, and this repo has had each of them wrong in turn.

    No Cache-Control at all lets the browser invent a lifetime from the file's
    modification date, which served week-old copies. `no-cache` fixed that and
    broke something worse: it forces a round trip to the server before the
    module may run, on every load, and Lovelace does not wait for a module
    before building its cards - so the race was lost every single time.

    `immutable` is the honest answer *because* the URL carries the file's
    hash: a stored copy cannot be the wrong one. And the content type is
    explicit because a guessed one is how a module gets fetched and then
    silently never executed.
    """
    hass = CardHass()

    await _async_register_card(hass)

    assert hass.data[_CARD_KEY]["served"] == "view"
    assert len(hass.http.views) == 1


async def test_a_missing_file_is_named_rather_than_silently_404ing(
    monkeypatch, offered, caplog
):
    """A missing file registers happily and then 404s, and the user sees the
    same message as for every other cause."""
    import custom_components.battery_management as pkg

    monkeypatch.setattr(pkg.Path, "is_file", lambda self: False)
    hass = CardHass()

    await _async_register_card(hass)

    report = hass.data[_CARD_KEY]
    assert report["error"] == "file_missing"
    assert report["path"] in caplog.text, "the log did not say where it looked"
    assert not offered, "offered a file it knew was not there"


async def test_a_failing_static_path_is_recorded(offered):
    hass = CardHass(http_fails=True)

    await _async_register_card(hass)

    report = hass.data[_CARD_KEY]
    assert report["error"].startswith("serving")
    assert not offered


async def test_it_registers_once(offered):
    hass = CardHass()

    await _async_register_card(hass)
    await _async_register_card(hass)

    assert len(offered) == 1
    assert len(hass.http.views) == 1


async def test_the_frontend_is_not_a_hard_dependency():
    """It was, briefly, and that was the wrong fix.

    A hard dependency does guarantee the ordering, but it also means the
    batteries stop being coordinated at all if the frontend fails to start -
    and a battery controller has no business depending on a web interface.
    The ordering is handled by waiting for the frontend instead.
    """
    import json
    import pathlib

    manifest = json.loads(
        (
            pathlib.Path(__file__).resolve().parents[1]
            / "custom_components/battery_management/manifest.json"
        ).read_text(encoding="utf-8")
    )

    assert "dependencies" not in manifest
    assert manifest.get("after_dependencies") == ["http", "frontend"]


async def test_it_waits_for_the_frontend_before_registering(monkeypatch, offered):
    """A card offered to a frontend that has not initialised is simply lost,
    which is indistinguishable from the card never having been built."""
    import sys
    import types

    waited: list[str] = []
    setup_mod = types.ModuleType("homeassistant.setup")

    def async_when_setup(hass, component, callback):
        waited.append(component)

    setup_mod.async_when_setup = async_when_setup
    monkeypatch.setitem(sys.modules, "homeassistant.setup", setup_mod)

    from custom_components.battery_management import _async_schedule_card

    hass = CardHass()
    await _async_schedule_card(hass)

    # both, and for different reasons: the frontend one serves the file, the
    # lovelace one registers it as a resource so Lovelace waits for it
    assert waited == ["frontend", "lovelace"]
    assert not offered, "registered before the frontend was ready"


async def test_once_the_frontend_is_up_it_registers(monkeypatch, offered):
    import sys
    import types

    setup_mod = types.ModuleType("homeassistant.setup")

    # keyed, because two components are waited on now and a single slot
    # would silently hand back whichever registered last
    setup_mod.hooks = {}

    def async_when_setup(hass, component, callback):
        setup_mod.hooks[component] = callback

    async def pending(hass, component):
        await setup_mod.hooks[component](hass, component)

    setup_mod.pending = pending

    setup_mod.async_when_setup = async_when_setup
    monkeypatch.setitem(sys.modules, "homeassistant.setup", setup_mod)

    from custom_components.battery_management import _async_schedule_card

    hass = CardHass()
    await _async_schedule_card(hass)
    await setup_mod.pending(hass, "frontend")

    assert offered, "the frontend came up and the card still was not offered"


class FakeResources:
    """A storage-mode resource collection, as Lovelace exposes one."""

    def __init__(self, items=None, loaded=True) -> None:
        self._items = list(items or [])
        self.loaded = loaded
        self.created: list = []
        self.updated: list = []
        self.deleted: list = []

    async def async_load(self):
        self.loaded = True

    def async_items(self):
        return list(self._items)

    async def async_create_item(self, data):
        self.created.append(data)
        self._items.append({"id": "new", **data})

    async def async_update_item(self, item_id, data):
        self.updated.append((item_id, data))
        for item in self._items:
            if item.get("id") == item_id:
                item.update(data)

    async def async_delete_item(self, item_id):
        self.deleted.append(item_id)
        self._items = [i for i in self._items if i.get("id") != item_id]


class YamlResources:
    """YAML mode: readable, not writable. No create/update at all."""

    def __init__(self, items=None) -> None:
        self._items = list(items or [])

    def async_items(self):
        return list(self._items)


def lovelace_hass(resources):
    hass = CardHass()
    hass.data["lovelace"] = {"resources": resources}
    return hass


async def test_the_card_is_registered_as_a_resource(offered):
    """The extra module is not awaited by Lovelace; a resource is.

    That difference is the whole bug: on an ordinary reload the dashboard came
    out of cache and rendered before the module had defined the element, so
    Lovelace drew an error card instead. Confirmed by hand at the primary site.
    """
    from custom_components.battery_management import _async_register_resource

    resources = FakeResources()
    hass = lovelace_hass(resources)
    await _async_register_card(hass)

    await _async_register_resource(hass)

    assert len(resources.created) == 1
    assert resources.created[0]["res_type"] == "module"
    assert resources.created[0]["url"].startswith(f"{CARD_URL}?v=")
    assert hass.data["battery_management_card_registered"]["resource"] == "created"


async def test_an_existing_entry_is_updated_not_duplicated(offered):
    """Two entries for one card would load it twice and never expire."""
    from custom_components.battery_management import _async_register_resource

    stale = {"id": "abc", "type": "module", "url": f"{CARD_URL}?v=0.12.3"}
    # snapshot: the fake merges updates into the stored item, as Home Assistant
    # does, so the dict itself no longer holds the old value afterwards
    was = stale["url"]
    resources = FakeResources([stale])
    hass = lovelace_hass(resources)
    await _async_register_card(hass)

    await _async_register_resource(hass)

    assert resources.created == [], "made a second entry for the same card"
    assert len(resources.updated) == 1
    item_id, data = resources.updated[0]
    assert item_id == "abc"
    assert data["url"] != was, "left the stale cache stamp in place"


async def test_a_matching_entry_is_left_alone(offered):
    """No storage write four times a day for a URL that has not moved."""
    from custom_components.battery_management import (
        _async_card_url,
        _async_register_resource,
    )

    hass = CardHass()
    url = await _async_card_url(hass)
    resources = FakeResources([{"id": "abc", "type": "module", "url": url}])
    hass.data["lovelace"] = {"resources": resources}
    await _async_register_card(hass)

    await _async_register_resource(hass)

    assert resources.created == [] and resources.updated == []
    assert hass.data["battery_management_card_registered"]["resource"] == "present"


async def test_duplicate_entries_are_collapsed_to_one(offered):
    """Two entries for the same file is how a site ends up importing it twice.

    The realistic route to it: somebody added the resource by hand while a
    release was failing to register it, and a later release added its own. Both
    then point at the same module, the browser imports it twice, and the two
    copies race to define the same custom elements.
    """
    from custom_components.battery_management import (
        _async_card_url,
        _async_register_resource,
    )

    hass = CardHass()
    resources = FakeResources(
        [
            {"id": "hand", "type": "module", "url": CARD_URL},
            # a leftover from when the URL still carried a cache stamp
            {"id": "ours", "type": "module", "url": f"{CARD_URL}?v=0.15.1-abc"},
        ]
    )
    hass.data["lovelace"] = {"resources": resources}
    await _async_register_card(hass)

    await _async_register_resource(hass)

    assert resources.deleted == ["ours"], resources.deleted
    remaining = [i["url"] for i in resources.async_items()]
    assert len(remaining) == 1
    assert remaining[0] == await _async_card_url(hass)
    report = hass.data["battery_management_card_registered"]
    assert report["resource_removed"] == 1


async def test_one_entry_is_never_deleted(offered):
    """The ordinary case must not touch the collection at all."""
    from custom_components.battery_management import (
        _async_card_url,
        _async_register_resource,
    )

    hass = CardHass()
    url = await _async_card_url(hass)
    resources = FakeResources([{"id": "abc", "type": "module", "url": url}])
    hass.data["lovelace"] = {"resources": resources}
    await _async_register_card(hass)

    await _async_register_resource(hass)

    assert resources.deleted == []
    assert "resource_removed" not in hass.data["battery_management_card_registered"]


async def test_the_collection_is_marked_loaded(offered):
    """Loading it without setting the flag makes it reload on every access.

    Home Assistant's own `_async_ensure_loaded` does both. Calling
    `async_load()` directly - which is what this used to do - leaves `loaded`
    False, so the collection re-reads from disk on the next access and
    re-announces every item as newly created.
    """
    from custom_components.battery_management import _async_register_resource

    resources = FakeResources([], loaded=False)
    hass = lovelace_hass(resources)
    await _async_register_card(hass)

    await _async_register_resource(hass)

    assert resources.loaded is True
    assert len(resources.created) == 1


async def test_a_resource_of_the_wrong_type_is_reported(offered, caplog):
    """Fetched, then ignored - and the browser only says "doesn't exist".

    A resource registered as "js" rather than "module" is downloaded and never
    executed, which reaches the user as exactly the same message as a missing
    file, a failed import, or a load race. The server side knows better, so it
    says so.
    """
    from custom_components.battery_management import (
        _async_card_url,
        _async_register_resource,
    )

    hass = CardHass()
    resources = FakeResources([{"id": "abc", "type": "js", "url": CARD_URL}])
    hass.data["lovelace"] = {"resources": resources}
    await _async_register_card(hass)

    await _async_register_resource(hass)

    assert hass.data["battery_management_card_registered"]["resource_type"] == "js"
    assert "never execute it" in caplog.text


async def test_yaml_mode_is_left_to_its_owner(offered):
    """A YAML dashboard's resources are the user's file, not ours to write."""
    from custom_components.battery_management import _async_register_resource

    hass = lovelace_hass(YamlResources())
    await _async_register_card(hass)

    await _async_register_resource(hass)

    assert hass.data["battery_management_card_registered"]["resource"] == "yaml_mode"


async def test_no_lovelace_is_not_an_error(offered):
    """The batteries have no business depending on a dashboard."""
    from custom_components.battery_management import _async_register_resource

    hass = CardHass()
    await _async_register_card(hass)

    await _async_register_resource(hass)

    assert hass.data["battery_management_card_registered"]["resource"] == "no_lovelace"
