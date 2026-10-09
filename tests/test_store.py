from custom_components.homehub_media.store import HomeHubStore


async def test_store_roundtrip(hass, hass_storage):
    s = HomeHubStore(hass); await s.async_load()
    await s.async_save_composition("dinner", {"visual": "aquarium"})
    await s.async_set_favorite("visual", "aquarium", True)
    s2 = HomeHubStore(hass); await s2.async_load()
    assert s2.composition("dinner")["visual"] == "aquarium"
    assert s2.favorites("visual") == {"aquarium"}


async def test_remove_favorite(hass, hass_storage):
    s = HomeHubStore(hass); await s.async_load()
    await s.async_set_favorite("visual", "aquarium", True)
    await s.async_set_favorite("visual", "rain", True)
    await s.async_set_favorite("visual", "aquarium", False)
    await s.async_set_favorite("visual", "never_added", False)
    s2 = HomeHubStore(hass); await s2.async_load()
    assert s2.favorites("visual") == {"rain"} and s2.favorites("soundtrack") == set()


async def test_compositions_saved_before_visuals_get_the_default(hass, hass_storage):
    hass_storage["homehub_media.data"] = {"version": 1, "key": "homehub_media.data", "data": {"compositions": {
        "dinner": {"visual": "aquarium", "visual_enabled": True},  # the shape before "visuals" existed
        "broken": {"visual": "../etc"}}}}
    s = HomeHubStore(hass); await s.async_load()
    assert s.composition("dinner")["visuals"] == [] and s.composition("dinner")["visual"] == "aquarium"
    assert s.composition("broken") is None and s.composition("missing") is None
