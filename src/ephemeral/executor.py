"""Ephemeral Executor.

Orchestrates the generate -> validate -> sandbox -> log pipeline.
Implements audit trails and capability gap tracking.
"""

import logging

from langchain_core.language_models import BaseChatModel

from src.ephemeral.generator import CodeGenerator
from src.ephemeral.sandbox import SandboxExecutor

logger = logging.getLogger(__name__)

class EphemeralOrchestrator:
    """End-to-end orchestrator for ephemeral synthesis."""
    
    def __init__(self, llm: BaseChatModel, sandbox_timeout: int = 30, max_retries: int = 2):
        self.generator = CodeGenerator(llm=llm, max_retries=max_retries)
        self.sandbox = SandboxExecutor(timeout_sec=sandbox_timeout)
        self.capability_gaps: list[str] = []

    async def execute(self, query: str) -> str:
        """
        End-to-end execution of ephemeral synthesis.
        
        Args:
            query: The calculation or data processing task to execute.
            
        Returns:
            The execution result or error string.
        """
        # Compliance logging (Audit Trail)
        logger.info("[AUDIT] Starting ephemeral synthesis for query: %r", query)
        
        try:
            # 1. Generate & Validate (Validation happens inside CodeGenerator)
            code = await self.generator.agenerate(query)
            logger.info("[AUDIT] Successfully generated and validated ephemeral code.")
            logger.debug("[AUDIT] Generated Code:\n%s", code)
            
            # 2. Sandbox Execution
            result = self.sandbox.execute(code)
            
            # 3. Log results and return
            if result.success:
                logger.info(
                    "[AUDIT] Sandbox execution successful (time: %.2fms). Output: %s", 
                    result.execution_time_ms, 
                    result.stdout.strip()
                )
                self._track_capability_gap(query)
                return f"Result: {result.stdout.strip()}"
            else:
                logger.warning(
                    "[AUDIT] Sandbox execution failed (time: %.2fms). Error: %s | Stderr: %s",
                    result.execution_time_ms,
                    result.error_message,
                    result.stderr.strip()
                )
                return f"Execution Error: {result.error_message or ''}\n{result.stderr.strip()}"
                
        except Exception as e:
            logger.error("[AUDIT] Ephemeral synthesis pipeline failed: %s", e)
            return f"System Error: {str(e)}"

    def _track_capability_gap(self, query: str):
        """
        Tracks capability gaps for potential Tier 1 promotion.
        """
        self.capability_gaps.append(query)
        logger.info("[CAPABILITY_GAP] Tracked missing capability: %r", query)
