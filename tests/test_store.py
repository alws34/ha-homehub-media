from custom_components.homehub_media.store import HomeHubStore


async def test_store_roundtrip(hass, hass_storage):
    s = HomeHubStore(hass); await s.async_load()
    await s.async_save_composition("dinner", {"visual": "aquarium"})
    await s.async_set_favorite("visual", "aquarium", True)
    s2 = HomeHubStore(hass); await s2.async_load()
    assert s2.composition("dinner")["visual"] == "aquarium"
    assert s2.favorites("visual") == {"aquarium"}
