# System Design Document: ShopSense Agent Architecture

---

## 1. Executive Summary

Enterprise deployments of AI agents struggle with "tool explosion" when bound directly to large Model Context Protocol (MCP) registries or massive API sets. This results in context window bloat, degraded tool selection accuracy, high latency, and elevated token costs. 

This design document outlines the **ShopSense Agent Architecture** that utilizes a simplified 2-tier waterfall approach to partition capabilities dynamically into permanent tools (MCP tools provided directly in prompt) and ephemeral tools (synthesized, sandboxed, and auto-destroyed at task completion). 

By giving users full authority to select which bound tools are permanent before each agent execution, the platform maximizes flexibility and token efficiency while maintaining robust security and fallback pathways for complex E-Commerce and Price Intelligence queries.

---

## 2. Core Architectural Principles

### 2.1 Decoupled Runtime & Dynamic Bindings
The agent is designed as a generic, stateful runtime. It does not ship with a hardcoded, static set of core tools. Instead, all capabilities are dynamically derived from user-bound MCP servers.

### 2.2 User-Controlled Context Window
Unlike automated classifiers that can misidentify critical tools or cause unpredictable context shifts, the user is the sole authority for defining the permanent tool registry. Before each execution run, the user selects a subset of tools to be pre-loaded into the LLM context. 

### 2.3 Strict Resolution Waterfall
To control execution costs and guarantee performance, the agent resolves any requested capability using a hierarchical lookup. It only synthesizes new code (ephemeral tools) as a last resort when existing assets and combinations are exhausted.

### 2.4 Ephemeral & Isolated Execution
When a capability gap is detected, code is generated on-the-fly. This code must remain task-scoped, run in a zero-privilege sandbox, and be completely purged from the system upon task completion.

---

## 3. Component Breakdown

### 3.1 MCP Server Registry
Manages user configurations for connected MCP servers (e.g., GitHub, Jira, SAP, Salesforce, or custom endpoints). It handles connection pooling, transport selection (SSE, HTTP, or Stdio), credentials resolution via an encrypted vault, and schema extraction. 

### 3.2 Pre-Run Configuration Interface
Allows users to configure their agent run profile programmatically or via a user interface. Users select which tools from the bound MCP servers should be "permanent" for the upcoming execution. Unselected tools are cataloged into the "on-demand" pool. Profiles can be saved, modified, and loaded dynamically.

### 3.3 Execution Context Manager
An execution-scoped state container created at the beginning of each run. It loads the permanent tools, maintains the schema catalog for the on-demand pool, tracks dynamically injected tools, and holds references to active ephemeral tools. It executes a mandatory teardown sequence to clean up resources when the run finishes.

### 3.4 Capability Routing (Graph logic)
LangGraph handles dynamic routing. If the agent core determines that its permanent tool set cannot satisfy the user prompt (e.g. requires a complex calculation), it delegates the capability description to the ephemeral synthesis tool.

### 3.5 Ephemeral Tool Synthesizer
A code-generation and validation pipeline. It translates a raw capability gap description into a clean Python script, schema-conforming interface definitions, and automatically generated unit tests.

### 3.6 Sandbox Runtime
A highly restricted execution wrapper (utilizing isolated Python subprocesses). The sandbox isolates ephemeral code from host credentials, system environment variables, network access (unless explicitly whitelisted), and limits execution time and memory.

### 3.7 Audit Logger & Promotion Tracker
An asynchronous logging system that records input, output, and execution time for compliance. It logs capability gaps and alerts the user when an ephemeral tool is repeatedly generated, recommending a permanent tool binding or custom MCP integration.

---

## 4. Capability Resolution Waterfall

When a task requires a capability, the runtime routes the request through a strict priority structure:

1. **Permanent MCP Tools (Tier 1):** The agent core first checks if the capability can be met by tools loaded directly in its active prompt context (e.g., `fetch`, `memory`).
2. **Ephemeral Tool Synthesis (Tier 2):** If the capability cannot be met by Tier 1 tools, a capability gap is declared. The Ephemeral Tool Synthesizer (`generate_ephemeral`) generates, validates, runs, and destroys a bespoke script designed specifically for the request.

---

## 5. Implementation Design Decisions

### 5.1 LangChain v1 Integration
- **Context Customization:** The agent executor is bootstrapped with a list of tools composed of the user's permanent selections and the Capability Router wrapper itself.
- **Dynamic Updates:** The LangChain AgentExecutor's toolset is modified at runtime. When the Capability Router resolves an on-demand tool or generates an ephemeral tool, it appends the wrapped tool directly to the executor's active tool list.
- **System Prompts:** The agent's system prompt dictates strict delegation: the agent must call the `capability_router` tool with a clear description whenever it encounters a capability deficiency rather than attempting code generation itself.

### 5.2 LangGraph Implementation
- **Explicit State Routing:** A graph structure maps the execution flow cleanly. Nodes represent discrete actions (e.g., `agent_step`, `route_capability`, `synthesize`, `validate`, `cleanup`).
- **Conditional Decisions:** Transition logic evaluates the router's decision and routes to the appropriate node. A validation loop allows the graph to transition back to the agent step with detailed error feedback if code synthesis or unit testing fails.
- **Guaranteed Cleanup:** The `cleanup` node lies directly before the exit edge, ensuring that the execution context teardown runs regardless of whether the agent succeeds, errors, or hits an iteration limit.

---

## 6. Security and Sandboxing

### 6.1 Credential Isolation
Neither the LLM context, the Capability Router, nor the ephemeral tools ever have direct access to user secrets or API keys. MCP credentials remain in an encrypted vault. The agent only executes tools by posting JSON-RPC requests to the MCP gateway, which injects tokens server-side.

### 6.2 Sandbox Environment Restrictions
Synthesized Python tools are executed in a blank subprocess environment:
- **Environment Variables:** Cleared entirely to prevent leaks of system-level tokens.
- **Network Access:** Disabled by default. If a tool requires network access, it must request it against an explicit domain-level whitelist configured by the organization.
- **Resource Limits:** Hard limits are applied on OS levels (defaulting to 30 seconds of CPU execution and 256 MB of RAM memory footprint).
- **File System:** Writes are restricted to a temporary path (`/tmp/ephemeral/<run_id>/`) which is recursively deleted during teardown.

### 6.3 Code Validation Checks
Before execution, generated code undergoes:
- **Abstract Syntax Tree (AST) Parsing:** To ensure syntactic correctness.
- **AST Security Scan:** Blocking calls to unsafe built-ins (e.g., `eval`, `exec`, `compile`, `__import__`) and dangerous library functions (e.g., `subprocess.run`, `os.system`).
- **Pydantic Schema Validation:** Enforcing type conformance between the LLM's generated parameters and the script's actual entry point.
- **Unit Testing:** Executing an LLM-generated happy-path unit test in the sandbox to verify runtime stability.

---

## 7. Performance and Cost Analysis

### 7.1 Token Cost Optimization
By allowing users to pin only a handful of relevant tools for a specific task rather than importing entire MCP server schemas (500+ tools), the prompt size is minimized.
- **Flat Registry:** 500 tools $\approx$ 50,000+ tokens per agent turn.
- **Hybrid Approach:** 15 permanent tools $\approx$ 4,500 tokens per agent turn.
- **Synthesis Overhead:** Generating code adds a single synthesis LLM call ($\approx$ 2,000 tokens) but only occurs when a true capability gap exists.

### 7.2 Latency Trade-offs
- **Permanent Tier:** 0 ms routing overhead.
- **Synthesis Tier:** 1,500-4,000 ms due to the round-trip code generation, and sandbox validation.

---

## 8. LLM Selection Strategy

The system is optimized by separating the LLM used for planning and user interaction from the LLM used for code generation.

### 8.1 Agent Core LLM
Requires high reasoning capabilities, complex planning, and long-term memory management.
- **Recommended Cloud Models:** Claude 3.7 Sonnet, GPT-4o, or Gemini 1.5 Pro.
- **Recommended Open-Source Models:** Llama-3-70b-Instruct or Qwen2.5-72B-Instruct.

### 8.2 Ephemeral Synthesizer LLM
Requires high-speed code generation, precise JSON schema formatting, and low latency.
- **Recommended Cloud Models:** GPT-4o-mini, Claude 3.5 Haiku, or Gemini 1.5 Flash.
- **Recommended Open-Source Models:** Codestral, DeepSeek-Coder-V2, or Qwen2.5-Coder-32B.

---

## 9. Error Handling and Reliability

### 9.1 Circuit Breakers
Each bound MCP server configuration has a dedicated circuit breaker. If an MCP server becomes unresponsive (e.g., 3 consecutive timeouts), the circuit breaker opens, prompting the Capability Router to immediately skip the on-demand tier and fallback to composition or ephemeral synthesis to solve the problem.

### 9.2 Synthesis Fallbacks
If the synthesized tool fails validation or crashes during execution:
1. The stack trace is captured and formatted.
2. A structured error is returned to the agent core.
3. The agent is permitted one retry iteration to regenerate the tool spec with updated instructions containing the failure history.
4. If it fails a second time, the agent degrades gracefully and prompts the user for clarification.

---

## 10. Conclusions and Roadmap

### 10.1 Key Conclusions
- Decentralizing "permanent" tools to user configuration prevents context explosion.
- Restricting code generation to true gaps controls token expenses and mitigates security risks.
- Combining AST analysis, schema testing, and sandbox isolation provides a viable enterprise security posture.

### 10.2 MVP Phase
Focuses on establishing the MCP Server Registry, building the Pre-Run Configuration UI, integrating the basic Capability Router (Tiers 1 & 2), and deploying a local subprocess sandbox with AST security filtering.

### 10.3 Long-Term Evolution
Includes learned profile suggestions (analyzing previous user runs to auto-suggest permanent tools), shared session-level ephemeral tools across multi-agent clusters, and advanced container-level sandboxing (e.g., gVisor or Firecracker microVMs).

---
*Document Version: 1.0 | Prepared: 2026-06-11*
