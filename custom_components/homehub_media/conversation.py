"""Voice assistant conversation agent: the configured model driving the HomeHub Media tools."""
from __future__ import annotations

import json
from typing import Literal

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent, llm

from . import assistant
from .const import DOMAIN, OPT_ASSISTANT
from .entity import HomeHubEntity

MAX_ROUNDS = 5
PROMPT = ("You are the HomeHub voice assistant: you control the user's TV and room ambience with your tools. "
          "Answer briefly in plain text; replies are spoken aloud.")


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([HomeHubConversationEntity(entry.runtime_data, "assistant")])


def _messages(content: list[conversation.Content]) -> list[dict]:
    """Chat log (minus the system prompt) -> assistant.complete()'s neutral messages."""
    out: list[dict] = []
    for c in content:
        if isinstance(c, conversation.UserContent):
            out.append({"role": "user", "content": c.content})
        elif isinstance(c, conversation.AssistantContent):
            out.append({"role": "assistant", "content": c.content,
                        "tool_calls": [(t.id, t.tool_name, t.tool_args) for t in c.tool_calls or []]})
        elif isinstance(c, conversation.ToolResultContent):
            out.append({"role": "tool", "tool_call_id": c.tool_call_id, "content": json.dumps(c.tool_result)})
    return out


class HomeHubConversationEntity(HomeHubEntity, conversation.ConversationEntity):
    _attr_supports_streaming = False

    @property
    def supported_languages(self) -> Literal["*"]:
        return MATCH_ALL

    async def _async_handle_message(self, user_input: conversation.ConversationInput,
                                    chat_log: conversation.ChatLog) -> conversation.ConversationResult:
        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN), [assistant.API_ID], PROMPT, user_input.extra_system_prompt)
        except conversation.ConverseError as err:
            return err.as_conversation_result()
        tools = assistant.tool_specs(chat_log.llm_api)
        for _ in range(MAX_ROUNDS):
            try:
                reply = await assistant.complete(self.hass, self.hub.options[OPT_ASSISTANT],
                                                 chat_log.content[0].content, _messages(chat_log.content[1:]), tools)
            except HomeAssistantError as err:  # "cannot_connect" / "invalid_auth", never the raw error
                response = intent.IntentResponse(language=user_input.language)
                response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN,
                                         f"The assistant is unavailable: {err}")
                return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id)
            calls = [llm.ToolInput(id=i, tool_name=n, tool_args=a) for i, n, a in reply.tool_calls]
            async for _result in chat_log.async_add_assistant_content(conversation.AssistantContent(
                    agent_id=user_input.agent_id, content=reply.text or None, tool_calls=calls or None)):
                pass  # tool results land in chat_log.content
            if not calls:
                break
        return conversation.async_get_result_from_chat_log(user_input, chat_log)
