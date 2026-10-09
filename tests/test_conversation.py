import json
from unittest.mock import patch

import pytest
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent

from custom_components.homehub_media import assistant
from custom_components.homehub_media.const import DOMAIN
from custom_components.homehub_media.conversation import MAX_ROUNDS


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

    async def complete(hass, cfg, system, messages, tools, final):
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
        {"role": "tool", "tool_call_id": "c1", "content": '{"success":true}'}]


async def test_tool_errors_reach_the_model(hass, setup_entry, agent, monkeypatch):
    replies = [assistant.Reply("", [("c1", "AmbienceStart", {"mode": "nope"}), ("c2", "Bogus", {})]),
               assistant.Reply("Sorry.")]
    seen = []

    async def complete(hass, cfg, system, messages, tools, final):
        seen.append(messages)
        return replies.pop(0)

    monkeypatch.setattr(assistant, "complete", complete)
    with patch.object(setup_entry.runtime_data.ambience, "start") as start:
        result = await conversation.async_converse(hass, "x", None, Context(), agent_id=agent)
    start.assert_not_awaited()
    assert result.response.speech["plain"]["speech"] == "Sorry."
    results = {m["tool_call_id"]: json.loads(m["content"]) for m in seen[1] if m["role"] == "tool"}
    assert results.keys() == {"c1", "c2"} and all("error" in r for r in results.values())


async def test_rounds_capped(hass, setup_entry, agent, monkeypatch):
    calls = []

    async def complete(hass, cfg, system, messages, tools, final):
        calls.append(final)
        return assistant.Reply("", [(f"c{len(calls)}", "NowPlaying", {})])

    monkeypatch.setattr(assistant, "complete", complete)
    result = await conversation.async_converse(hass, "loop", None, Context(), agent_id=agent)
    assert calls == [False] * (MAX_ROUNDS - 1) + [True]  # the last round must answer in text
    assert result.response.response_type is intent.IntentResponseType.ERROR


@pytest.mark.parametrize(("code", "speech"), [("invalid_auth", "The assistant's API key was rejected."),
                                              ("cannot_connect", "I can't reach the assistant right now.")])
async def test_provider_error(hass, setup_entry, agent, monkeypatch, code, speech):
    async def complete(*args, **kwargs):
        raise HomeAssistantError(code)

    monkeypatch.setattr(assistant, "complete", complete)
    result = await conversation.async_converse(hass, "hi", None, Context(), agent_id=agent)
    assert result.response.response_type is intent.IntentResponseType.ERROR
    assert result.response.speech["plain"]["speech"] == speech


async def test_no_agent_without_assistant(hass, setup_entry):
    assert not hass.states.async_entity_ids("conversation")


async def test_agent_removed_with_assistant(hass, setup_entry, agent):
    assert hass.states.get(agent)
    hass.config_entries.async_update_entry(
        setup_entry, options={k: v for k, v in setup_entry.options.items() if k != "assistant"})
    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert not er.async_get(hass).async_get(agent) and not hass.states.get(agent)
