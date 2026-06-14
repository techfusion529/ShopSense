import asyncio
import logging
import os
import time
import uuid
from dotenv import load_dotenv

load_dotenv()

from azure.core.credentials import AccessToken, TokenCredential
from azure.identity import DefaultAzureCredential
from langchain_azure_ai.chat_models import AzureAIOpenAIApiChatModel
from langchain_core.messages import AIMessage, HumanMessage
from typing_extensions import TypedDict

from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponsesAgentServerHost,
    ResponsesServerOptions,
    TextResponse,
)
from azure.ai.agentserver.responses.models import (
    MessageContentInputTextContent,
    MessageContentOutputTextContent,
)

from src.mcp.client import MCPClientManager
from src.agent.graph import build_graph

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_SEP = "═" * 60  # heavy separator for request boundaries

# Suppress noisy telemetry output that appears locally when there is no
# Application Insights / Azure IMDS endpoint available.
# These are all cosmetic — they do NOT affect agent functionality.
for _noisy_logger in (
    "azure.identity",           # DefaultAzureCredential IMDS probe attempts
    "opentelemetry.sdk.trace",  # OTLP export failures
    "opentelemetry",            # General OTel noise
    "urllib3.connectionpool",   # Low-level HTTP timeout details
):
    logging.getLogger(_noisy_logger).setLevel(logging.ERROR)

# ── Environment validation ────────────────────────────────────────────
if not os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    logger.warning(
        "APPLICATIONINSIGHTS_CONNECTION_STRING not set — traces will not be sent to "
        "Application Insights. (Auto-injected in hosted Foundry containers.)"
    )

FOUNDRY_PROJECT_ENDPOINT = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
if not FOUNDRY_PROJECT_ENDPOINT:
    raise EnvironmentError(
        "FOUNDRY_PROJECT_ENDPOINT environment variable is not set. "
        "Set it to your Foundry project endpoint, or use 'azd ai agent run'."
    )

AZURE_AI_MODEL_DEPLOYMENT_NAME = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
if not AZURE_AI_MODEL_DEPLOYMENT_NAME:
    raise EnvironmentError(
        "AZURE_AI_MODEL_DEPLOYMENT_NAME environment variable is not set. "
        "Set it to your model deployment name as declared in agent.manifest.yaml."
    )


# ── LLM ──────────────────────────────────────────────────────────────
class _StaticBearerCredential:
    """
    Minimal TokenCredential shim that wraps a static API key.

    AzureAIOpenAIApiChatModel with `project_endpoint` only accepts
    TokenCredential (not AzureKeyCredential). This adapter satisfies that
    interface by returning the raw key as a Bearer token — matching how
    Azure AI Foundry expects API-key auth on the wire.
    """

    def __init__(self, key: str) -> None:
        self._key = key

    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        # Expires far in the future; the key is static.
        return AccessToken(self._key, int(time.time()) + 3600)


def _build_credential():
    """
    Resolve Azure credentials.

    - Local development: set AZURE_AI_API_KEY in .env → uses _StaticBearerCredential.
    - Foundry hosted:    Managed Identity is auto-injected  → uses DefaultAzureCredential.
    """
    api_key = os.environ.get("AZURE_AI_API_KEY")
    if api_key:
        logger.info("Using static API key for authentication (local dev mode).")
        return _StaticBearerCredential(api_key)
    logger.info("Using DefaultAzureCredential for authentication (Foundry/MI mode).")
    return DefaultAzureCredential()


def _build_llm() -> AzureAIOpenAIApiChatModel:
    return AzureAIOpenAIApiChatModel(
        project_endpoint=FOUNDRY_PROJECT_ENDPOINT,
        credential=_build_credential(),
        model=AZURE_AI_MODEL_DEPLOYMENT_NAME,
        streaming=True,
        use_responses_api=False,
    )


# ── Startup: connect MCP servers & compile graph ──────────────────────
# These are module-level so they are initialized once when the server starts.
_mcp_manager = MCPClientManager()
_llm = _build_llm()
_graph = None  # will be set after async MCP connect in lifespan


async def _init_graph():
    """Connect to MCP servers and compile the LangGraph agent graph."""
    global _graph
    logger.info("Connecting to MCP servers...")
    mcp_tools = await _mcp_manager.connect_all()
    if mcp_tools:
        logger.info("Bound %d MCP tools: %s", len(mcp_tools), [t.name for t in mcp_tools])
    else:
        logger.warning(
            "No MCP tools were loaded. The agent will use ephemeral synthesis for all tasks. "
            "Set MCP_SERVERS_CONFIG in .env to connect MCP servers."
        )
    _graph = build_graph(_llm, mcp_tools)
    logger.info("ShopSense agent graph compiled and ready.")


# ── Helpers ───────────────────────────────────────────────────────────
def _history_to_langchain_messages(history: list) -> list:
    """Convert responses-protocol history items to LangChain messages."""
    messages = []
    for item in history:
        if hasattr(item, "content") and item.content:
            for content in item.content:
                if isinstance(content, MessageContentOutputTextContent) and content.text:
                    messages.append(AIMessage(content=content.text))
                elif isinstance(content, MessageContentInputTextContent) and content.text:
                    messages.append(HumanMessage(content=content.text))
    return messages


def _extract_final_answer(result: dict) -> str:
    """Pull the last text answer out of the graph result's message list."""
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage):
            # Check for Azure OpenAI content filter block
            finish_reason = msg.response_metadata.get("finish_reason")
            if finish_reason == "content_filter":
                return "[Error] The model's response was blocked by Azure AI content filters (finish_reason: 'content_filter')."

            raw = msg.content
            answer_text = ""
            if isinstance(raw, list):
                answer_text = "".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in raw
                ).strip()
            elif raw:
                answer_text = raw.strip()

            reasoning = msg.additional_kwargs.get("reasoning_content", "").strip()
            if reasoning and answer_text:
                return f"<think>\n{reasoning}\n</think>\n\n{answer_text}"
            elif reasoning:
                return f"<think>\n{reasoning}\n</think>"
            elif answer_text:
                return answer_text
    return "(no response)"


# ── Foundry responses protocol wiring ─────────────────────────────────
app = ResponsesAgentServerHost(
    options=ResponsesServerOptions(default_fetch_history_count=20)
)


@app.response_handler
async def handle_create(
    request: CreateResponse,
    context: ResponseContext,
    cancellation_signal: asyncio.Event,
):
    """Run the ShopSense LangGraph agent and stream the response."""

    async def run_graph():
        """Initialise graph if needed, fetch history, invoke, yield result."""
        if _graph is None:
            await _init_graph()

        try:
            try:
                history = await context.get_history()
            except Exception:
                history = []

            current_input = await context.get_input_text() or "Hello!"
            lc_messages = _history_to_langchain_messages(history)
            lc_messages.append(HumanMessage(content=current_input))

            run_id = str(uuid.uuid4())
            input_preview = current_input[:120] + ("…" if len(current_input) > 120 else "")
            logger.info(
                "%s\n  REQUEST START  run=%s\n  Input: \"%s\"",
                _SEP, run_id, input_preview,
            )

            t0 = time.perf_counter()
            result = await _graph.ainvoke(
                {"messages": lc_messages, "ephemeral_code": None, "run_id": run_id}
            )
            answer = _extract_final_answer(result)
            elapsed = time.perf_counter() - t0

            answer_preview = answer[:120] + ("…" if len(answer) > 120 else "")
            logger.info(
                "  REQUEST DONE   run=%s  total=%.2fs\n  Answer: \"%s\"\n%s",
                run_id, elapsed, answer_preview, _SEP,
            )
            yield answer

        except Exception as exc:
            logger.exception("run_graph failed")
            yield f"[ERROR] {type(exc).__name__}: {exc}"

    return TextResponse(context, request, text=run_graph())



# ── Entry point ───────────────────────────────────────────────────────
if __name__ == "__main__":
    # Phase 1: run async startup (MCP connect + graph compile) in its own loop.
    asyncio.run(_init_graph())
    # Phase 2: hand off to the Foundry server host (starts its own uvicorn loop).
    app.run()
