"""
JavaScript/Node.js code execution service.

Provides sandboxed Node.js code execution with:
- Subprocess isolation
- Per-conversation working directories
- Blocked dangerous modules (child_process, net, etc.)
- Timeout enforcement
"""

import os
import shutil
from typing import Optional

from services.execution.base import (
    EXECUTION_LIMITS,
    ExecutionResult,
    _get_sandbox_dir,
    run_subprocess,
)


# Blocked Node.js modules
BLOCKED_MODULES = [
    "child_process", "cluster", "dgram", "dns", "net", "tls",
    "http", "https", "http2", "worker_threads", "vm",
    "fs/promises",
]


def _generate_wrapper(code: str, working_dir) -> str:
    """Generate a wrapper script that executes user code with security restrictions."""
    blocked_json = ", ".join(f'"{m}"' for m in BLOCKED_MODULES)
    return f'''
"use strict";

// Override require to block dangerous modules
const originalRequire = require;
const blockedModules = new Set([{blocked_json}]);

function safeRequire(name) {{
    if (blockedModules.has(name)) {{
        throw new Error(`Module '${{name}}' is not available for security reasons`);
    }}
    return originalRequire(name);
}}

// Replace global require
global.require = safeRequire;
// Also override Module._load for deeper blocking
const Module = originalRequire('module');
const originalLoad = Module._load;
Module._load = function(request, parent, isMain) {{
    if (blockedModules.has(request)) {{
        throw new Error(`Module '${{request}}' is not available for security reasons`);
    }}
    return originalLoad.call(this, request, parent, isMain);
}};

// Block process.exit
process.exit = function() {{
    throw new Error("process.exit() is not allowed");
}};

// Filter process.env to remove sensitive keys
const safeEnv = {{}};
for (const [key, value] of Object.entries(process.env)) {{
    if (!key.includes("KEY") && !key.includes("SECRET") && !key.includes("TOKEN") &&
        !key.includes("PASSWORD") && !key.includes("AUTH")) {{
        safeEnv[key] = value;
    }}
}}
process.env = safeEnv;

// Set working directory
process.chdir({repr(str(working_dir))});

// Provide a safe file writing function
const fs = originalRequire('fs');
const path = originalRequire('path');
let writeCount = 0;
const maxWrites = 10;

global.saveFile = function(filename, content) {{
    if (writeCount >= maxWrites) {{
        throw new Error("Maximum file write limit reached");
    }}
    const safeName = path.basename(filename);
    if (!safeName) throw new Error("Invalid filename");
    const filePath = path.join({repr(str(working_dir))}, safeName);
    fs.writeFileSync(filePath, typeof content === 'string' ? content : String(content));
    writeCount++;
    console.log(`[File written: ${{safeName}}]`);
    return safeName;
}};

// Execute user code
try {{
{_indent_js(code)}
}} catch (e) {{
    console.error(`Error: ${{e.name}}: ${{e.message}}`);
    process.exitCode = 1;
}}
'''


def _indent_js(code: str, spaces: int = 4) -> str:
    """Indent code by the specified number of spaces."""
    indent = " " * spaces
    lines = code.split("\n")
    return "\n".join(indent + line for line in lines)


async def execute_javascript(
    code: str,
    conversation_id: str,
    timeout: Optional[int] = None,
) -> ExecutionResult:
    """
    Execute JavaScript code in a sandboxed Node.js subprocess.

    Args:
        code: JavaScript code to execute
        conversation_id: ID for the conversation (determines working directory)
        timeout: Optional timeout in seconds

    Returns:
        ExecutionResult with output, errors, and execution info
    """
    timeout = timeout or EXECUTION_LIMITS["timeout_seconds"]

    working_dir = _get_sandbox_dir(conversation_id)
    wrapper_code = _generate_wrapper(code, working_dir)

    script_path = working_dir / "_execute.js"
    script_path.write_text(wrapper_code)

    try:
        result = await run_subprocess(
            args=["node", str(script_path)],
            working_dir=working_dir,
            timeout=timeout,
            env={
                **{k: v for k, v in os.environ.items()
                   if not any(s in k for s in ("KEY", "SECRET", "TOKEN", "PASSWORD", "AUTH"))},
                "NODE_ENV": "sandbox",
            },
        )
        return result
    finally:
        if script_path.exists():
            script_path.unlink()


def is_available() -> bool:
    """Check if JavaScript execution is available (Node.js installed)."""
    return shutil.which("node") is not None


def get_status() -> dict:
    """Get the status of the JavaScript execution service."""
    available = is_available()
    return {
        "status": "connected" if available else "error",
        "status_message": "Node.js interpreter available" if available else "Node.js not found",
        "metadata": {
            "timeout_seconds": EXECUTION_LIMITS["timeout_seconds"],
            "max_output_bytes": EXECUTION_LIMITS["max_output_bytes"],
        },
    }
