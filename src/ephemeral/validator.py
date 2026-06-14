"""AST-Based Code Validator for ephemeral synthesis.

Parses generated code and detects dangerous patterns to ensure safe execution.
"""

import ast

# Restricted names that should not be used or called
BANNED_NAMES = {
    "eval", "exec", "__import__", "open", "compile",
    "globals", "locals", "getattr", "setattr", "delattr",
    "hasattr", "memoryview", "bytearray", "bytes"
}

# Restricted modules that must not be imported or used
BANNED_IMPORTS = {
    "os", "sys", "subprocess", "shutil", "socket", 
    "urllib", "requests", "pathlib", "importlib", 
    "pty", "commands", "popen2", "builtins"
}

# Explicit whitelist of allowed modules
ALLOWED_IMPORTS = {
    "math", "decimal", "statistics", "random",
    "datetime", "typing", "collections", "itertools",
    "re", "string", "json", "typing_extensions", "dataclasses"
}

class EphemeralASTValidator(ast.NodeVisitor):
    def __init__(self):
        self.errors = []

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            if node.func.id in BANNED_NAMES:
                self.errors.append(f"Use of banned function '{node.func.id}' is not allowed.")
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in BANNED_NAMES:
            self.errors.append(f"Use of banned name '{node.id}' is not allowed.")
        if node.id in BANNED_IMPORTS:
            self.errors.append(f"Use of banned module '{node.id}' is not allowed.")
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            base_module = alias.name.split('.')[0]
            if base_module not in ALLOWED_IMPORTS:
                self.errors.append(f"Importing module '{alias.name}' is not allowed. Only whitelisted modules are allowed.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            base_module = node.module.split('.')[0]
            if base_module not in ALLOWED_IMPORTS:
                self.errors.append(f"Importing from module '{node.module}' is not allowed. Only whitelisted modules are allowed.")
        self.generic_visit(node)
        
    def visit_Attribute(self, node):
        # Additional check to prevent things like __builtins__.eval
        if node.attr.startswith("__") and node.attr.endswith("__"):
            # We allow basic dunders like __name__ or __main__, but block accessing dangerous attributes
            if node.attr in {"__builtins__", "__dict__", "__class__", "__bases__", "__subclasses__"}:
                self.errors.append(f"Accessing dunder attribute '{node.attr}' is not allowed.")
        self.generic_visit(node)


def validate_code(code: str) -> list[str]:
    """
    Parses Python code and returns a list of validation errors.
    Returns an empty list if code is safe.
    
    Args:
        code: Python source code string.
        
    Returns:
        List of error messages.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"Syntax Error: {e}"]
    
    validator = EphemeralASTValidator()
    validator.visit(tree)
    return validator.errors
