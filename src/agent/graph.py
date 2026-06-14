"""LangGraph agent graph for the ShopSense agent."""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import textwrap
from typing import Annotated, Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_MESSAGE = """You are ShopSense, an E-Commerce Price Intelligence Agent.

You have access to predefined tools:
- fetch: retrieve information from the internet.
- memory: store and recall user preferences.
- generate_ephemeral: perform mathematical calculations on prices or budgets.

Guidelines:
1. Please use generate_ephemeral for all math.
2. Always fetch live prices rather than guessing.
3. Be helpful and proactive.
4. You must ONLY answer questions related to shopping, e-commerce, price intelligence, product comparisons, or ShopSense functionalities
"""


# ── Graph state ───────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    ephemeral_code: str | None
    run_id: str


# ── Ephemeral tool ────────────────────────────────────────────────────────────

@tool
def generate_ephemeral(query: str) -> str:
    """Calculate shopping related math.

    Args:
        query: The calculation to perform.

    Returns:
        The result.
    """
    # This stub is replaced at runtime by the graph executor node.
    return f"[ephemeral stub] query={query!r}"



# ── Graph nodes ───────────────────────────────────────────────────────────────

def build_graph(llm, mcp_tools: list):
    """Compile and return the LangGraph StateGraph for the ShopSense agent."""

    from src.ephemeral.executor import EphemeralOrchestrator
    orchestrator = EphemeralOrchestrator(llm=llm)

    # Scrub fetch tool description to avoid content-filter triggers
    clean_mcp_tools = []
    for t in mcp_tools:
        if t.name == "fetch":
            t.description = (
                "Retrieve the content of a URL. "
                "Use this to look up product prices, specifications, and reviews "
                "from e-commerce and manufacturer websites."
            )
        clean_mcp_tools.append(t)

    all_tools = clean_mcp_tools + [generate_ephemeral]
    tool_map = {t.name: t for t in all_tools}

    llm_with_tools = llm.bind_tools(all_tools)

    # ── chatbot node ──────────────────────────────────────────────────────────
    async def chatbot(state: AgentState):
        messages = state["messages"]

        # Prepend system message if not already present
        from langchain_core.messages import SystemMessage
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_MESSAGE)] + list(messages)

        logger.debug("chatbot: invoking LLM with %d messages", len(messages))
        response = await llm_with_tools.ainvoke(messages)

        finish_reason = response.response_metadata.get("finish_reason", "")
        logger.info(
            "  RAW RESPONSE DICT: %s",
            {"content": response.content,
             "additional_kwargs": response.additional_kwargs,
             "response_metadata": response.response_metadata,
             "type": response.type,
             "name": response.name,
             "id": response.id,
             "tool_calls": response.tool_calls,
             "invalid_tool_calls": response.invalid_tool_calls,
             "usage_metadata": response.usage_metadata},
        )

        if finish_reason == "content_filter":
            logger.warning("LLM response blocked by content filter.")

        return {"messages": [response]}

    # ── tool execution node ───────────────────────────────────────────────────
    async def execute_tools(state: AgentState):
        last_msg = state["messages"][-1]
        tool_results = []

        for tool_call in last_msg.tool_calls:
            name = tool_call["name"]
            args = tool_call["args"]
            call_id = tool_call["id"]

            logger.info("  TOOL CALL: %s(%s)", name, args)

            if name == "generate_ephemeral":
                query = args.get("query", "")
                result = await orchestrator.execute(query)
            elif name in tool_map:
                try:
                    result = await tool_map[name].ainvoke(args)
                except Exception as exc:
                    result = f"[Tool error] {type(exc).__name__}: {exc}"
            else:
                result = f"[Unknown tool: {name}]"

            logger.info("  TOOL RESULT (%s): %s", name, str(result)[:200])
            tool_results.append(
                ToolMessage(content=str(result), tool_call_id=call_id)
            )

        return {"messages": tool_results}

    # ── routing ───────────────────────────────────────────────────────────────
    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    # ── build graph ───────────────────────────────────────────────────────────
    graph = StateGraph(AgentState)
    graph.add_node("chatbot", chatbot)
    graph.add_node("tools", execute_tools)

    graph.set_entry_point("chatbot")
    graph.add_conditional_edges("chatbot", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "chatbot")

    return graph.compile()
