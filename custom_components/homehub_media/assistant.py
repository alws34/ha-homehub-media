"""Voice assistant: the HomeHub Media LLM tools API and Ollama/OpenAI/Claude adapters.

Neutral message format used by complete():
  {"role": "user"|"assistant"|"tool", "content": str|None,
   "tool_calls"?: [(id, name, args)],  # assistant
   "tool_call_id"?: str}               # tool (content = JSON result)
Neutral tool format: {"name", "description", "parameters": JSON schema} (see tool_specs()).
"""
from __future__ import annotations

import json
import sys
import unicodedata
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.requirements import async_process_requirements
from homeassistant.util.json import JsonObjectType
from probatio import to_openapi

from .const import ACTIVITIES, APP_CATALOG, COMMANDS, DOMAIN, TEXT_MAX
from .hub import loaded_hub

API_ID = f"{DOMAIN}_tools"
CLAUDE_MODEL = "claude-opus-5-5"
REQ = {"openai": "openai>=2.45.0", "ollama": "openai>=2.45.0", "claude": "anthropic>=0.108.0"}
REFUSAL = "Sorry, I can't help with that."
TITLE_MAX = 100
PROMPT = ("You control the living-room TV, its streaming apps and the room ambience (lights, visuals, sound) "
          "with the HomeHub Media tools. Replies are spoken aloud, so keep them to one short sentence.")


class HubTool(llm.Tool):
    """Validates the model's arguments, then calls the Hub."""

    def __init__(self, name: str, description: str, schema: dict, run: Callable[..., Awaitable[Any]]) -> None:
        self.name, self.description, self.parameters, self._run = name, description, vol.Schema(schema), run

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput,
                         llm_context: llm.LLMContext) -> JsonObjectType:
        args = self.parameters(tool_input.tool_args)  # HA 2026.9 does not validate; model output is untrusted
        if not (hub := loaded_hub(hass)):
            raise HomeAssistantError("HomeHub Media is not loaded")
        return await hub.command(self._run(hub, **args)) or {"success": True}  # voice is user activity


def _clean(text: str) -> str:
    """Media-server text for the model: no format/control characters (hidden instructions), short."""
    return "".join(c for c in text if unicodedata.category(c)[0] != "C")[:TITLE_MAX]


async def _play(hub: Any, title: str) -> dict:
    q = title.casefold()
    lists = hub.library.items.values() if hub.library else ()
    items = [i for server in lists for kind in server.values() for i in kind]
    item = (next((i for i in items if i["title"].casefold() == q), None)
            or next((i for i in items if q in i["title"].casefold()), None))
    if item is None:
        raise HomeAssistantError(f"Nothing called {title!r} in continue watching or recently added")
    await hub.play_library_item(item["server"], item["id"])
    return {"playing": _clean(item["title"])}


async def _now_playing(hub: Any) -> dict:
    playing = hub.library.now_playing if hub.library else []
    return {"now_playing": [{k: _clean(v) if isinstance(v, str) else v for k, v in p.items() if k != "image"}
                            for p in playing]}


TOOLS = [
    HubTool("LaunchApp", "Open a streaming app on the TV.", {vol.Required("app"): vol.In(list(APP_CATALOG))},
            lambda hub, app: hub.launch_app(app)),
    HubTool("Remote", "Press a remote-control button on whatever the TV is showing (TV, streaming box or PC).",
            {vol.Required("command"): vol.In(COMMANDS)}, lambda hub, command: hub.remote(command)),
    HubTool("SetActivity", "Switch what the TV shows: off (all off), tv, box (streaming box) or pc (media PC).",
            {vol.Required("activity"): vol.In(ACTIVITIES)}, lambda hub, activity: hub.activity.set(activity)),
    HubTool("AmbienceStop", "Stop the running ambience.", {}, lambda hub: hub.ambience.stop()),
    HubTool("AmbienceAuto", "Start the ambience mode set for the current time of day.", {},
            lambda hub: hub.ambience.auto_start()),
    HubTool("SleepTimer", "Stop the ambience after this many minutes; 0 cancels the timer.",
            {vol.Required("minutes"): vol.All(vol.Coerce(int), vol.Range(0, 600))},
            lambda hub, minutes: hub.ambience.set_sleep_timer(minutes)),
    HubTool("SetQuietMode", "Turn quiet mode (lower TV volume) on or off.", {vol.Required("on"): bool},
            lambda hub, on: hub.activity.set_quiet(on)),
    HubTool("PlayLibraryItem", "Play a movie or show by title from Plex/Jellyfin continue watching or recently added.",
            {vol.Required("title"): vol.All(str, vol.Length(1, TEXT_MAX))}, _play),
    HubTool("NowPlaying", "What is playing on Plex/Jellyfin right now.", {}, _now_playing),
]


class HomeHubLLMAPI(llm.API):
    """HomeHub Media tools for any HA conversation agent (ours and core Ollama/OpenAI/Anthropic)."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass=hass, id=API_ID, name="HomeHub Media")

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        prompt, modes = PROMPT, []
        if hub := loaded_hub(self.hass):
            modes = hub.ambience.modes()
            prompt += " Ambience modes (id (name)): " + ", ".join(f"{m['id']} ({m['name']})" for m in modes) + "."
        start = HubTool("AmbienceStart", "Start an ambience mode (lights, visuals and sound) by its id.",
                        {vol.Required("mode"): vol.In([m["id"] for m in modes])},
                        lambda hub, mode: hub.ambience.start(mode))
        return llm.APIInstance(api=self, api_prompt=prompt, llm_context=llm_context, tools=[*TOOLS, start] if modes else TOOLS)


def tool_specs(api: llm.APIInstance) -> list[dict]:
    """Neutral tool list for complete(); schemas converted the way core agents do."""
    return [{"name": t.name, "description": t.description or "",
             "parameters": to_openapi(t.parameters, custom_serializer=api.custom_serializer)} for t in api.tools]


def _sdk_errors() -> tuple[type[Exception], ...]:
    """APIError of each SDK already imported (they load lazily; an absent SDK cannot have raised)."""
    return tuple(e for n in ("openai", "anthropic") if (e := getattr(sys.modules.get(n), "APIError", None)))


@contextmanager
def _api_errors() -> Iterator[None]:
    """Network/SDK failures -> HomeAssistantError("invalid_auth"|"cannot_connect"); raw errors never leak."""
    try:
        yield
    except (aiohttp.ClientError, TimeoutError, *_sdk_errors()) as err:
        status = getattr(err, "status_code", None) or getattr(err, "status", None)
        raise HomeAssistantError("invalid_auth" if status in (401, 403) else "cannot_connect") from err


def _sdk_client(cfg: dict) -> Any:
    """AsyncAnthropic, or AsyncOpenAI for OpenAI / Ollama's OpenAI-compatible /v1. Runs in the executor."""
    opts = {"timeout": 60, "max_retries": 1, "http_client": cfg["http_client"]}
    if cfg["provider"] == "claude":
        import anthropic  # installed on demand by ensure_sdk

        client = anthropic.AsyncAnthropic(api_key=cfg["api_key"], **opts)
    else:
        import openai

        url = f"{cfg['url'].rstrip('/')}/v1" if cfg["provider"] == "ollama" else None
        client = openai.AsyncOpenAI(api_key=cfg.get("api_key") or "ollama", base_url=url, **opts)
    client.platform_headers()  # the SDK probes the OS (file reads) once and caches it; keep that off the loop
    return client


async def _client(hass: HomeAssistant, cfg: dict) -> Any:
    # The executor hop keeps the first SDK import and OS probe off the loop; HA's shared httpx client
    # (created in the loop) carries the connection pool, so no per-call TLS setup and nothing to close.
    # ponytail: SDK wrapper object rebuilt per call; cache it per (provider, url, key) if that ever shows
    return await hass.async_add_executor_job(_sdk_client, {**cfg, "http_client": get_async_client(hass)})


async def ensure_sdk(hass: HomeAssistant, provider: str) -> None:
    """Install only the chosen provider's SDK (versions HA core pins)."""
    await async_process_requirements(hass, DOMAIN, [REQ[provider]], is_built_in=False)


async def list_models(hass: HomeAssistant, provider: str, url: str | None, api_key: str | None) -> list[str]:
    with _api_errors():
        if provider == "ollama":
            async with async_get_clientsession(hass).get(
                    f"{url.rstrip('/')}/api/tags", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                return [m["name"] for m in (await resp.json()).get("models", [])]
        client = await _client(hass, {"provider": provider, "url": url, "api_key": api_key})
        return sorted([m.id async for m in client.with_options(timeout=10, max_retries=0).models.list()])


@dataclass
class Reply:
    text: str
    tool_calls: list[tuple[str, str, dict]] = field(default_factory=list)


def _openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        msg = {k: m[k] for k in ("role", "content", "tool_call_id") if k in m}
        if m.get("tool_calls"):
            msg["tool_calls"] = [{"id": i, "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                                 for i, n, a in m["tool_calls"]]
        out.append(msg)
    return out


def _claude_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m["role"] == "tool":
            role, blocks = "user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}]
        else:
            role, blocks = m["role"], [{"type": "text", "text": m["content"]}] if m.get("content") else []
            blocks += [{"type": "tool_use", "id": i, "name": n, "input": a} for i, n, a in m.get("tool_calls") or []]
        if not blocks:  # an empty turn would 400 every later request
            continue
        if out and out[-1]["role"] == role:  # parallel tool results go back in one user turn
            out[-1]["content"] += blocks
        else:
            out.append({"role": role, "content": blocks})
    return out


def _loads(args: str | None) -> Any:
    try:
        return json.loads(args or "{}")
    except ValueError:
        return {}  # malformed model JSON: the tool schema rejects it and the model can retry


async def complete(hass: HomeAssistant, cfg: dict, system: str, messages: list[dict], tools: list[dict],
                   final: bool = False) -> Reply:
    """One model call; cfg is the assistant options section (provider, url, api_key, model).

    final: the last round, so the model must answer in text. Claude still needs the tool definitions
    (the history holds tool_use blocks), so it gets tool_choice none; OpenAI/Ollama just get no tools.
    """
    with _api_errors():
        client = await _client(hass, cfg)
        if cfg["provider"] != "claude":
            msg = (await client.chat.completions.create(
                model=cfg["model"], messages=_openai_messages(system, messages),
                **({"tools": [{"type": "function", "function": t} for t in tools]} if tools and not final else {}),
            )).choices[0].message
            return Reply(msg.content or getattr(msg, "refusal", None) or "",
                         [(c.id, c.function.name, _loads(c.function.arguments)) for c in msg.tool_calls or []])
        # ponytail: thinking blocks are not replayed (neutral format), so later tool rounds lose earlier
        # reasoning; carry resp.content via AssistantContent.native if multi-step quality suffers
        req = {"model": cfg.get("model") or CLAUDE_MODEL, "max_tokens": 4096, "system": system,
               "messages": _claude_messages(messages),
               **({"tools": [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                             for t in tools],
                   # forced tool choice 400s on Opus 5.5; auto and none are fine
                   "tool_choice": {"type": "none" if final else "auto"}} if tools else {})}
        try:
            resp = await client.messages.create(
                **req,
                output_config={"effort": "low"},  # snappy voice; thinking is always on, effort is the control
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
                extra_body={"fallbacks": "default"},  # SDK 0.108.0 only types the list form
            )
        except _sdk_errors() as err:
            if getattr(err, "status_code", None) != 400:
                raise
            # ponytail: older models (e.g. claude-haiku-4-5) reject effort/fallbacks; one blind retry without
            # them; check models.retrieve() capabilities instead if unrelated 400s get retried too often
            resp = await client.messages.create(**req)
    if resp.stop_reason == "refusal":
        return Reply(REFUSAL)
    return Reply("".join(b.text for b in resp.content if b.type == "text"),
                 [(b.id, b.name, b.input) for b in resp.content if b.type == "tool_use"]
                 if resp.stop_reason == "tool_use" else [])
