"""Code Generator for ephemeral synthesis.

Uses an LLM to generate type-hinted Python code and validates it,
with retry logic for graceful degradation.
"""

import logging
from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.ephemeral.validator import validate_code

logger = logging.getLogger(__name__)


GENERATOR_SYSTEM_PROMPT = """You are an expert Python programmer.
Your job is to generate a standalone Python script to solve a specific problem.
The output MUST be exactly one Python code block enclosed in ```python ... ``` and nothing else.
Do not include any explanations.

The code MUST:
1. Include type hints for all functions.
2. Be completely self-contained (no external dependencies, no file I/O).
3. Print the final result to stdout. Do not use logging, only `print`.
4. Only use whitelisted modules: math, decimal, statistics, random, datetime, typing, collections, itertools, re, string, json, typing_extensions, dataclasses.
5. NEVER use os, sys, subprocess, exec, eval, __import__, open, or any dunder attributes like __builtins__.
"""

class CodeGenerator:
    """Generates safe Python code for ephemeral execution."""
    
    def __init__(self, llm: BaseChatModel, max_retries: int = 2):
        self.llm = llm
        self.max_retries = max_retries

    async def agenerate(self, task_description: str) -> str:
        """
        Generates safe Python code for the given task.
        Validates the code and retries if it violates AST constraints.
        
        Args:
            task_description: Plain-English description of the task or calculation.
            
        Returns:
            The raw, validated Python code string.
            
        Raises:
            ValueError: If valid code cannot be generated after max_retries.
        """
        prompt = f"Write a Python script that solves the following task and prints the result:\n\n{task_description}"
        messages = [
            SystemMessage(content=GENERATOR_SYSTEM_PROMPT),
            HumanMessage(content=prompt)
        ]

        last_errors = []
        for attempt in range(self.max_retries + 1):
            logger.info("Code generation attempt %d/%d", attempt + 1, self.max_retries + 1)
            
            try:
                response = await self.llm.ainvoke(messages)
                raw_content = str(response.content)
            except Exception as e:
                logger.error("LLM invocation failed: %s", e)
                raise ValueError(f"LLM failure: {e}")
            
            code = self._extract_code(raw_content)
            
            errors = validate_code(code)
            if not errors:
                return code
            
            logger.warning("Validation failed on attempt %d: %s", attempt + 1, errors)
            last_errors = errors
            
            if attempt < self.max_retries:
                error_msg = (
                    "The generated code failed AST validation with the following errors:\n"
                    + "\n".join(f"- {err}" for err in errors)
                    + "\n\nPlease rewrite the code to fix these errors. Respond ONLY with the fixed Python code inside ```python ```."
                )
                messages.append(response)
                messages.append(HumanMessage(content=error_msg))
                
        raise ValueError(f"Failed to generate valid code after {self.max_retries + 1} attempts. Last errors: {last_errors}")

    def _extract_code(self, raw: str) -> str:
        """Extracts python code from a markdown-formatted LLM response."""
        if "```python" in raw:
            parts = raw.split("```python")
            if len(parts) > 1:
                return parts[1].split("```")[0].strip()
        if "```" in raw:
            parts = raw.split("```")
            if len(parts) > 1:
                return parts[1].strip()
        return raw.strip()
