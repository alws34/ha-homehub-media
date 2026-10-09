import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homehub_media import assistant as a
from custom_components.homehub_media.const import DOMAIN
from custom_components.homehub_media.hub import Hub


async def test_claude_tool_call_parsed(hass, monkeypatch):
    msg = SimpleNamespace(stop_reason="tool_use", content=[
        SimpleNamespace(type="text", text="Starting"),
        SimpleNamespace(type="tool_use", id="t1", name="AmbienceStart", input={"mode": "dinner"})])
    class C:  # fake AsyncAnthropic
        class messages:
            @staticmethod
            async def create(**kw):
                assert kw["model"] == "claude-opus-5-5" and kw["tool_choice"] == {"type": "auto"}
                assert kw["output_config"] == {"effort": "low"} and kw["extra_body"] == {"fallbacks": "default"}
                assert kw["extra_headers"] == {"anthropic-beta": "server-side-fallback-2026-07-01"}
                return msg
    monkeypatch.setattr(a, "_sdk_client", lambda cfg: C)
    r = await a.complete(hass, {"provider": "claude", "api_key": "k", "model": "claude-opus-5-5"}, "sys",
                         [{"role": "user", "content": "dinner mode"}], [{"name": "AmbienceStart",
                         "description": "d", "parameters": {"type": "object", "properties": {}}}])
    assert r.text == "Starting" and r.tool_calls == [("t1", "AmbienceStart", {"mode": "dinner"})]


async def test_claude_refusal(hass, monkeypatch):
    class C:
        class messages:
            @staticmethod
            async def create(**kw):
                return SimpleNamespace(stop_reason="refusal", content=[])
    monkeypatch.setattr(a, "_sdk_client", lambda cfg: C)
    r = await a.complete(hass, {"provider": "claude", "api_key": "k", "model": "m"}, "s",
                         [{"role": "user", "content": "x"}], [])
    assert r.tool_calls == [] and "can't" in r.text


async def test_openai_compatible_for_ollama(hass, monkeypatch):
    seen = {}
    fn = SimpleNamespace(name="Remote", arguments='{"command": "home"}')
    choice = SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[SimpleNamespace(id="c1", function=fn)]))
    class O:
        class chat:
            class completions:
                @staticmethod
                async def create(**kw):
                    seen.update(kw); return SimpleNamespace(choices=[choice])
    monkeypatch.setattr(a, "_sdk_client", lambda cfg: (seen.setdefault("cfg", cfg), O)[1])
    r = await a.complete(hass, {"provider": "ollama", "url": "http://o:11434", "model": "llama3.2"}, "s",
                         [{"role": "user", "content": "go home"}], [])
    assert r.tool_calls == [("c1", "Remote", {"command": "home"})] and seen["model"] == "llama3.2"


async def test_tool_validates_and_calls_hub(hass):
    hub = SimpleNamespace(last_command=0, ambience=SimpleNamespace(start=AsyncMock(return_value=None), modes=lambda: [{"id": "dinner", "name": "Dinner"}]))
    hub.command = lambda aw: Hub.command(hub, aw)
    api = a.HomeHubLLMAPI(hass)
    assert (api.id, api.name) == (a.API_ID, "HomeHub Media")
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    call = llm.ToolInput("AmbienceStart", {"mode": "dinner"})
    with pytest.raises(HomeAssistantError):  # integration not loaded
        await (await api.async_get_api_instance(ctx)).async_call_tool(llm.ToolInput("AmbienceStop", {}))

    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = hub
    inst = await api.async_get_api_instance(ctx)
    assert "dinner (Dinner)" in inst.api_prompt
    assert await inst.async_call_tool(call) == {"success": True}
    hub.ambience.start.assert_awaited_once_with("dinner")
    assert hub.last_command > 0  # voice counts as user activity
    for bad in ("../etc", "dinner\n", "reading"):  # model output is untrusted; only configured modes
        with pytest.raises(vol.Invalid):
            await inst.async_call_tool(llm.ToolInput("AmbienceStart", {"mode": bad}))
    sleep = next(s for s in a.tool_specs(inst) if s["name"] == "SleepTimer")
    assert sleep["parameters"] == {"type": "object", "required": ["minutes"], "additionalProperties": False,
                                   "properties": {"minutes": {"type": "integer", "minimum": 0, "maximum": 600}}}


async def test_list_models_ollama(hass, aioclient_mock):
    aioclient_mock.get("http://o:11434/api/tags", json={"models": [{"name": "llama3.2:latest"}, {"name": "qwen3:8b"}]})
    assert await a.list_models(hass, "ollama", "http://o:11434/", None) == ["llama3.2:latest", "qwen3:8b"]
    aioclient_mock.clear_requests()
    aioclient_mock.get("http://o:11434/api/tags", status=500)
    with pytest.raises(HomeAssistantError, match="cannot_connect"):
        await a.list_models(hass, "ollama", "http://o:11434", None)


def test_tool_result_turn_conversion():
    msgs = [{"role": "user", "content": "go home"},
            {"role": "assistant", "content": "", "tool_calls": [("c1", "Remote", {"command": "home"}),
                                                                ("c2", "NowPlaying", {})]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"success": true}'},
            {"role": "tool", "tool_call_id": "c2", "content": '{"now_playing": []}'}]
    assert a._openai_messages("s", msgs) == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "go home"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "Remote", "arguments": '{"command": "home"}'}},
            {"id": "c2", "type": "function", "function": {"name": "NowPlaying", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"success": true}'},
        {"role": "tool", "tool_call_id": "c2", "content": '{"now_playing": []}'}]
    # Claude: parallel tool results go back in ONE user turn
    assert a._claude_messages(msgs) == [
        {"role": "user", "content": [{"type": "text", "text": "go home"}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "c1", "name": "Remote", "input": {"command": "home"}},
            {"type": "tool_use", "id": "c2", "name": "NowPlaying", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": '{"success": true}'},
            {"type": "tool_result", "tool_use_id": "c2", "content": '{"now_playing": []}'}]}]
    # an empty assistant turn is dropped instead of 400-ing every later request
    assert a._claude_messages([{"role": "user", "content": "hi"}, {"role": "assistant", "content": ""},
                               {"role": "user", "content": "again"}]) == [
        {"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "text", "text": "again"}]}]


class APIError(Exception):  # stands in for anthropic.APIError
    def __init__(self, status_code):
        self.status_code = status_code


async def test_claude_400_retry_and_auth_error(hass, monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(APIError=APIError))
    calls = []
    class C:
        class messages:
            @staticmethod
            async def create(**kw):
                calls.append(kw)
                if "output_config" in kw:
                    raise APIError(400)  # older models reject effort/fallbacks
                return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="ok")])
    monkeypatch.setattr(a, "_sdk_client", lambda cfg: C)
    cfg = {"provider": "claude", "api_key": "k", "model": "claude-haiku-4-5"}
    r = await a.complete(hass, cfg, "s", [{"role": "user", "content": "x"}], [])
    assert r.text == "ok" and len(calls) == 2
    assert calls[1]["model"] == "claude-haiku-4-5" and not {"output_config", "extra_body", "extra_headers"} & set(calls[1])

    class D:
        class messages:
            @staticmethod
            async def create(**kw):
                calls.append(kw)
                raise APIError(401)
    monkeypatch.setattr(a, "_sdk_client", lambda cfg: D)
    with pytest.raises(HomeAssistantError, match="invalid_auth"):
        await a.complete(hass, cfg, "s", [{"role": "user", "content": "x"}], [])
    assert len(calls) == 3  # no retry on auth errors


async def test_final_round_forbids_tools(hass, monkeypatch):
    seen = []

    async def create(**kw):
        seen.append(kw)
        return SimpleNamespace(stop_reason="end_turn", content=[],
                               choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])
    fake = SimpleNamespace(messages=SimpleNamespace(create=create),
                           chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(a, "_sdk_client", lambda cfg: fake)
    tools = [{"name": "Remote", "description": "d", "parameters": {"type": "object"}}]
    for provider in ("claude", "openai"):
        await a.complete(hass, {"provider": provider, "api_key": "k", "model": "m"}, "s",
                         [{"role": "user", "content": "x"}], tools, final=True)
    assert seen[0]["tool_choice"] == {"type": "none"} and seen[0]["tools"]  # Claude needs them for the history
    assert "tools" not in seen[1]


async def test_media_titles_are_cleaned_for_the_model():
    hub = SimpleNamespace(library=SimpleNamespace(now_playing=[
        {"title": "A\u202eB\u200b\x00" + "x" * 200, "player": "TV\n", "position": 1.5, "image": "/art"}]))
    assert await a._now_playing(hub) == {"now_playing": [{"title": "AB" + "x" * 98, "player": "TV", "position": 1.5}]}
