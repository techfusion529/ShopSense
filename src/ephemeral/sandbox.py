"""Sandbox Executor for ephemeral synthesis.

Runs generated code in an isolated subprocess with strict timeouts
and environment constraints.
"""

import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class SandboxResult:
    """Result of a sandbox execution."""
    success: bool
    stdout: str
    stderr: str
    execution_time_ms: float
    error_message: Optional[str] = None


class SandboxExecutor:
    """Executes Python code in an isolated environment."""
    
    def __init__(self, timeout_sec: int = 30):
        self.timeout_sec = timeout_sec

    def execute(self, code: str) -> SandboxResult:
        """
        Executes the provided code safely in a subprocess.
        
        Args:
            code: The Python code to execute.
            
        Returns:
            SandboxResult object with stdout, stderr, and metadata.
        """
        start_time = time.time()
        
        # Create an isolated temporary directory with automatic cleanup
        with tempfile.TemporaryDirectory() as temp_dir:
            code_path = os.path.join(temp_dir, "ephemeral_script.py")
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(code)

            try:
                # Provide a minimal safe environment to prevent credential leaks.
                # On Windows, python requires at least SystemRoot and potentially PATH to load DLLs.
                safe_env = {}
                if os.name == 'nt':
                    if "SystemRoot" in os.environ:
                        safe_env["SystemRoot"] = os.environ["SystemRoot"]
                    if "PATH" in os.environ:
                        safe_env["PATH"] = os.environ["PATH"]
                else:
                    safe_env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")

                # Note: Memory limiting (256MB) is best done via Docker or `resource` (Unix).
                # Since we are executing on Windows directly, we rely on the process timeout 
                # and clean directory isolation as best-effort defense.
                
                result = subprocess.run(
                    [sys.executable, code_path],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_sec,
                    env=safe_env,
                    cwd=temp_dir
                )
                
                execution_time_ms = (time.time() - start_time) * 1000
                
                return SandboxResult(
                    success=(result.returncode == 0),
                    stdout=result.stdout,
                    stderr=result.stderr,
                    execution_time_ms=execution_time_ms
                )
                
            except subprocess.TimeoutExpired as e:
                execution_time_ms = (time.time() - start_time) * 1000
                stdout = e.stdout.decode('utf-8', errors='replace') if isinstance(e.stdout, bytes) else (e.stdout or "")
                stderr = e.stderr.decode('utf-8', errors='replace') if isinstance(e.stderr, bytes) else (e.stderr or "")
                
                return SandboxResult(
                    success=False,
                    stdout=stdout,
                    stderr=stderr,
                    execution_time_ms=execution_time_ms,
                    error_message=f"Execution timed out after {self.timeout_sec} seconds."
                )
            except Exception as e:
                execution_time_ms = (time.time() - start_time) * 1000
                return SandboxResult(
                    success=False,
                    stdout="",
                    stderr="",
                    execution_time_ms=execution_time_ms,
                    error_message=f"Sandbox execution failed: {str(e)}"
                )
