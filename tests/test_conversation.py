from unittest.mock import patch

import pytest
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent

from custom_components.homehub_media import assistant
from custom_components.homehub_media.const import DOMAIN


@pytest.fixture
async def agent(hass, setup_entry):
    """The entry reloaded with the assistant configured; returns the conversation entity id."""
    hass.config_entries.async_update_entry(setup_entry, options={
        **setup_entry.options, "assistant": {"provider": "claude", "url": None, "api_key": "k", "model": "m"}})
    with patch.object(assistant, "ensure_sdk"):
        assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    return er.async_get(hass).async_get_entity_id("conversation", DOMAIN, f"{setup_entry.entry_id}_assistant")


async def test_tool_call_then_answer(hass, setup_entry, agent, monkeypatch):
    replies = [assistant.Reply("", [("c1", "AmbienceStart", {"mode": "dinner"})]), assistant.Reply("Dinner is on.")]
    seen = []

    async def complete(hass, cfg, system, messages, tools):
        seen.append(messages)
        assert cfg["provider"] == "claude" and "HomeHub" in system and any(t["name"] == "AmbienceStart" for t in tools)
        return replies.pop(0)

    monkeypatch.setattr(assistant, "complete", complete)
    with patch.object(setup_entry.runtime_data.ambience, "start", return_value=None) as start:
        result = await conversation.async_converse(hass, "dinner mode", None, Context(), agent_id=agent)
    start.assert_awaited_once_with("dinner")
    assert result.response.speech["plain"]["speech"] == "Dinner is on."
    assert seen[0] == [{"role": "user", "content": "dinner mode"}]
    assert seen[1][1:] == [
        {"role": "assistant", "content": None, "tool_calls": [("c1", "AmbienceStart", {"mode": "dinner"})]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"success": true}'}]


async def test_provider_error(hass, setup_entry, agent, monkeypatch):
    async def complete(*args):
        raise HomeAssistantError("invalid_auth")

    monkeypatch.setattr(assistant, "complete", complete)
    result = await conversation.async_converse(hass, "hi", None, Context(), agent_id=agent)
    assert result.response.response_type is intent.IntentResponseType.ERROR
    assert result.response.speech["plain"]["speech"] == "The assistant is unavailable: invalid_auth"


async def test_no_agent_without_assistant(hass, setup_entry):
    assert not hass.states.async_entity_ids("conversation")
