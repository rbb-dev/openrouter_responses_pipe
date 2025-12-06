# Smart Stub Loader - Design Specification

**Version**: 2.0.0-alpha
**Status**: Implementation Ready
**Target Size**: ~200 lines (ultra-minimal)

---

## Design Philosophy

The stub loader should be **as small as possible** to minimize what users need to manually update. The strategy:

1. **Pinned by Default**: Load modules from a specific Git tag (e.g., `v2.0.0`) for stability
2. **Auto-Update Valve**: Optional flag to pull from `@main` for bleeding-edge updates
3. **Smart Caching**: Cache modules locally (Open WebUI data directory) with TTL
4. **Graceful Degradation**: Log errors clearly if modules fail to load
5. **Zero Configuration**: Works out-of-the-box with sensible defaults

---

## Stub Loader Structure

### Frontmatter (Required Metadata)
```python
"""
title: OpenRouter Responses API Manifold
author: rbb-dev
author_url: https://github.com/rbb-dev
git_url: https://github.com/rbb-dev/openrouter_responses_pipe/
version: 2.0.0
requirements: git+https://github.com/rbb-dev/openrouter_responses_pipe.git@v2.0.0#subdirectory=openrouter_modules&egg=openrouter_modules, aiohttp, cryptography, fastapi, httpx, lz4, pydantic, pydantic_core, sqlalchemy, tenacity
license: MIT
description: OpenRouter Responses API pipe with modular architecture. Modules loaded from GitHub.
"""
```

### Valve Configuration
```python
class Valves(BaseModel):
    # Module loading control
    AUTO_UPDATE_MODULES: bool = Field(
        default=False,
        description="Enable automatic module updates from main branch (default: pinned to stable release)"
    )
    MODULE_VERSION: str = Field(
        default="v2.0.0",
        description="Git tag/branch to load modules from (ignored if AUTO_UPDATE_MODULES=True)"
    )
    MODULE_CACHE_TTL_HOURS: int = Field(
        default=24,
        description="Hours to cache modules before checking for updates"
    )

    # All existing valves from monolithic version
    OPENROUTER_API_KEY: str = Field(...)
    # ... (inherit all other valves)
```

### Core Logic (~200 lines)

```python
import logging
import sys
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

class Pipe:
    """Ultra-minimal stub loader for OpenRouter Responses modular architecture."""

    def __init__(self):
        self.valves = Valves()
        self._modules = {}
        self._delegate_pipe = None
        self._load_modules()

    def _load_modules(self):
        """Load modules from installed package."""
        try:
            # Open WebUI already pip-installed via requirements header
            from openrouter_modules import (
                core_models,
                registry,
                persistence,
                multimodal,
                streaming,
                tools,
                utilities
            )

            self._modules = {
                'core_models': core_models,
                'registry': registry,
                'persistence': persistence,
                'multimodal': multimodal,
                'streaming': streaming,
                'tools': tools,
                'utilities': utilities
            }

            # Instantiate the real Pipe class from persistence module
            # (It has all the valve definitions and core logic)
            self._delegate_pipe = persistence.ComposedPipe(
                modules=self._modules,
                valves=self.valves
            )

            LOGGER.info(f"✓ OpenRouter modules loaded successfully (version: {self._get_module_version()})")

        except ImportError as e:
            LOGGER.error(f"✗ Failed to load OpenRouter modules: {e}")
            LOGGER.error("Ensure Open WebUI successfully ran 'pip install' from requirements header")
            raise RuntimeError(
                "OpenRouter modules unavailable. Check Open WebUI logs for pip install errors."
            )

    def _get_module_version(self) -> str:
        """Get loaded module version from package metadata."""
        try:
            from openrouter_modules import __version__
            return __version__
        except (ImportError, AttributeError):
            return "unknown"

    # ─────────────────────────────────────────────────────────────────
    # Delegate all methods to the real Pipe class
    # ─────────────────────────────────────────────────────────────────

    async def pipes(self, body: dict) -> list[dict]:
        """Delegate to composed pipe for model discovery."""
        return await self._delegate_pipe.pipes(body)

    async def pipe(
        self,
        body: dict,
        __user__: dict = {},
        __event_emitter__=None,
        __event_call__=None,
        __task__=None
    ):
        """Delegate to composed pipe for main request handling."""
        return await self._delegate_pipe.pipe(
            body=body,
            __user__=__user__,
            __event_emitter__=__event_emitter__,
            __event_call__=__event_call__,
            __task__=__task__
        )

    async def on_startup(self):
        """Delegate to composed pipe for startup lifecycle."""
        if hasattr(self._delegate_pipe, 'on_startup'):
            await self._delegate_pipe.on_startup()

    async def on_shutdown(self):
        """Delegate to composed pipe for shutdown lifecycle."""
        if hasattr(self._delegate_pipe, 'on_shutdown'):
            await self._delegate_pipe.on_shutdown()
```

---

## Auto-Update Mechanism

### How It Works

1. **Default Behavior (Pinned)**:
   - `requirements` header specifies `@v2.0.0` tag
   - Modules installed once, cached by pip
   - Users manually update stub when new version released

2. **Auto-Update Enabled**:
   - User sets `AUTO_UPDATE_MODULES=True` valve
   - Stub modifies requirements dynamically to `@main`
   - **Challenge**: Open WebUI only reads requirements on initial load

### Implementation Strategy

**Option A: Valve-Controlled Branch in Requirements** (Simplest)

Users manually edit the stub's requirements line to switch branches:
```python
# For stable (default):
requirements: git+https://github.com/rbb-dev/openrouter_responses_pipe.git@v2.0.0#subdirectory=openrouter_modules&egg=openrouter_modules, ...

# For auto-updates:
requirements: git+https://github.com/rbb-dev/openrouter_responses_pipe.git@main#subdirectory=openrouter_modules&egg=openrouter_modules, ...
```

**Pro**: Simple, no complex logic needed
**Con**: Requires manual stub edit

**Option B: Stub Dynamically Re-installs on Startup** (More Complex)

```python
def _load_modules(self):
    """Load modules, re-installing if AUTO_UPDATE_MODULES enabled."""

    if self.valves.AUTO_UPDATE_MODULES:
        # Force pip to re-install from main branch
        import subprocess
        LOGGER.info("AUTO_UPDATE_MODULES enabled, checking for updates...")
        try:
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall",
                "git+https://github.com/rbb-dev/openrouter_responses_pipe.git@main#subdirectory=openrouter_modules&egg=openrouter_modules"
            ])
            LOGGER.info("✓ Modules updated from main branch")
        except subprocess.CalledProcessError as e:
            LOGGER.warning(f"Module update failed: {e}, using cached version")

    # Now import modules (freshly installed or cached)
    from openrouter_modules import ...
```

**Pro**: True auto-update controlled by valve
**Con**: Requires subprocess, slower startup, potential pip conflicts

### Recommended Approach: **Option A + Documentation**

- Default stub uses `@v2.0.0` (pinned, stable)
- Document in README: "For auto-updates, edit requirements line to `@main`"
- Future enhancement: Implement Option B if users request it

---

## Module Version Tracking

### Package `__init__.py`
```python
# openrouter_modules/__init__.py

__version__ = "2.0.0"
__git_ref__ = "v2.0.0"  # or "main" for auto-update

# Export all modules for easy importing
from . import core_models
from . import registry
from . import persistence
from . import multimodal
from . import streaming
from . import tools
from . import utilities

__all__ = [
    "core_models",
    "registry",
    "persistence",
    "multimodal",
    "streaming",
    "tools",
    "utilities"
]
```

### Display Version in Open WebUI

The stub can log version info on startup:
```python
LOGGER.info(f"OpenRouter Responses Pipe v{__version__} (modules: {self._get_module_version()})")
```

Users see this in Open WebUI logs when pipe loads.

---

## Package Structure

```
openrouter_responses_pipe/
├── openrouter_modules/
│   ├── __init__.py              # Version info + exports
│   ├── core_models.py           # ~1,200 lines
│   ├── registry.py              # ~800 lines
│   ├── persistence.py           # ~1,500 lines (includes ComposedPipe class)
│   ├── multimodal.py            # ~1,000 lines
│   ├── streaming.py             # ~1,200 lines
│   ├── tools.py                 # ~1,500 lines
│   └── utilities.py             # ~800 lines
├── setup.py                     # pip package definition
├── stub_loader.py               # ~200 lines (users copy this into OWUI)
├── README.md
├── docs/
└── tests/
```

### `setup.py` (Minimal)
```python
from setuptools import setup, find_packages

setup(
    name="openrouter_modules",
    version="2.0.0",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "aiohttp",
        "cryptography",
        "fastapi",
        "httpx",
        "lz4",
        "pydantic>=2.0",
        "pydantic_core",
        "sqlalchemy",
        "tenacity"
    ],
    author="rbb-dev",
    description="Modular components for OpenRouter Responses API pipe",
    url="https://github.com/rbb-dev/openrouter_responses_pipe",
)
```

---

## Module Coordination: The `ComposedPipe` Class

Since the stub is ultra-minimal, the actual `Pipe` logic lives in one of the modules. Best location: **`persistence.py`** (already handles coordination of DB, cache, etc.)

### `persistence.py` - New Export
```python
# At the end of persistence.py

class ComposedPipe:
    """
    Fully-composed Pipe class that coordinates all modules.

    This is the real implementation that the stub delegates to.
    It receives references to all loaded modules and orchestrates them.
    """

    def __init__(self, modules: dict, valves: Any):
        self.modules = modules
        self.valves = valves

        # Initialize components from modules
        self.logger = modules['utilities'].SessionLogger(...)
        self.registry = modules['registry'].OpenRouterModelRegistry(...)
        self.persistence_manager = PersistenceManager(...)  # from this module
        self.streaming_manager = modules['streaming'].StreamingManager(...)
        self.tool_executor = modules['tools'].ToolExecutor(...)
        self.multimodal_processor = modules['multimodal'].MultimodalProcessor(...)

    async def pipes(self, body: dict) -> list[dict]:
        """Model discovery via registry."""
        return await self.registry.fetch_and_register_models(self.valves, ...)

    async def pipe(self, body: dict, __user__={}, __event_emitter__=None, **kwargs):
        """Main request handler - orchestrates all modules."""

        # 1. Parse request (core_models)
        parsed = self.modules['core_models'].CompletionsBody.model_validate(body)

        # 2. Process multimodal (multimodal)
        enriched = await self.multimodal_processor.process_images(parsed, self.valves, ...)

        # 3. Translate to Responses API (core_models)
        responses_body = self.modules['core_models'].translate_to_responses(enriched)

        # 4. Execute tools if needed (tools)
        if responses_body.tools:
            responses_body = await self.tool_executor.execute_tools(
                responses_body,
                self.valves,
                ...
            )

        # 5. Stream response (streaming)
        async for event in self.streaming_manager.stream_response(
            responses_body,
            self.valves,
            __event_emitter__,
            ...
        ):
            yield event

        # 6. Persist artifacts (persistence - this module)
        await self.persistence_manager.save_artifacts(...)

    async def on_startup(self):
        """Initialize all subsystems."""
        await self.registry.initialize()
        await self.persistence_manager.initialize()

    async def on_shutdown(self):
        """Cleanup all subsystems."""
        await self.persistence_manager.cleanup()
```

---

## Error Handling in Stub

The stub should fail gracefully with helpful messages:

```python
def _load_modules(self):
    try:
        from openrouter_modules import (...)
    except ImportError as e:
        error_msg = (
            "╔═══════════════════════════════════════════════════════════╗\n"
            "║  OpenRouter Modules Failed to Load                        ║\n"
            "╠═══════════════════════════════════════════════════════════╣\n"
            f"║  Error: {str(e):<51}║\n"
            "║                                                           ║\n"
            "║  Possible causes:                                         ║\n"
            "║  1. Open WebUI failed to pip install requirements         ║\n"
            "║  2. Network error downloading from GitHub                 ║\n"
            "║  3. Git version specified in requirements doesn't exist   ║\n"
            "║                                                           ║\n"
            "║  Troubleshooting:                                         ║\n"
            "║  - Check Open WebUI logs for pip errors                   ║\n"
            "║  - Verify requirements header specifies valid Git tag     ║\n"
            "║  - Test manually: pip install git+https://github.com/...  ║\n"
            "╚═══════════════════════════════════════════════════════════╝"
        )
        LOGGER.error(error_msg)
        raise RuntimeError("OpenRouter modules unavailable") from e
```

---

## Testing Strategy for Stub

### Unit Test: Stub Imports
```python
# tests/test_stub_loader.py

def test_stub_can_import_modules():
    """Stub successfully imports all required modules."""
    # Assuming modules installed in test env
    from openrouter_modules import (
        core_models,
        registry,
        persistence,
        multimodal,
        streaming,
        tools,
        utilities
    )
    assert core_models is not None
    assert hasattr(persistence, 'ComposedPipe')

def test_stub_instantiates_pipe():
    """Stub can instantiate ComposedPipe."""
    # Mock valves
    from stub_loader import Pipe
    pipe = Pipe()
    assert pipe._delegate_pipe is not None
    assert hasattr(pipe._delegate_pipe, 'pipes')
    assert hasattr(pipe._delegate_pipe, 'pipe')
```

### Integration Test: Production Simulation
```python
# tests/test_integration_stub.py

@pytest.mark.asyncio
async def test_full_request_flow_via_stub(mock_owui_db):
    """Complete request flows through stub → modules."""
    from stub_loader import Pipe

    pipe = Pipe()

    # Test model discovery
    models = await pipe.pipes({"dummy": "body"})
    assert isinstance(models, list)

    # Test request handling
    body = {
        "model": "openrouter/gpt-4o-mini",
        "messages": [{"role": "user", "content": "test"}]
    }

    events = []
    async def mock_emitter(event):
        events.append(event)

    async for chunk in pipe.pipe(body, __event_emitter__=mock_emitter):
        pass

    assert len(events) > 0
```

### Manual Test: Production Weekend
1. Copy `stub_loader.py` into Open WebUI Functions
2. Set `OPENROUTER_API_KEY` valve
3. Enable pipe in model selector
4. Verify logs: "OpenRouter modules loaded successfully"
5. Send test message to any OpenRouter model
6. Check response streams correctly
7. Inspect database for artifact persistence

---

## Migration Path for Future Updates

### When v2.1.0 is Released

1. **Tag new version in Git**:
   ```bash
   git tag v2.1.0
   git push origin v2.1.0
   ```

2. **Users update by editing stub's requirements line**:
   ```python
   # Change from:
   requirements: git+...@v2.0.0#...

   # To:
   requirements: git+...@v2.1.0#...
   ```

3. **Open WebUI re-runs pip install on stub save** (automatically downloads new modules)

4. **No code changes needed in stub** (unless stub logic itself changed)

### Eventually: Automatic Update Checker

Future enhancement in stub:
```python
def _check_for_updates(self):
    """Check if newer version available (non-blocking)."""
    current = self._get_module_version()
    try:
        import httpx
        latest = httpx.get(
            "https://api.github.com/repos/rbb-dev/openrouter_responses_pipe/releases/latest"
        ).json()["tag_name"]

        if latest != current:
            LOGGER.info(f"🔔 New version available: {latest} (current: {current})")
            LOGGER.info("Update by editing stub requirements line")
    except Exception:
        pass  # Silently fail, not critical
```

---

## Summary: Stub Responsibilities

| Responsibility | Stub | Modules |
|----------------|------|---------|
| Define requirements header | ✅ | ❌ |
| Import modules | ✅ | ❌ |
| Define valves | ✅ (minimal) | ✅ (full definitions in persistence) |
| Implement pipe() logic | ❌ | ✅ |
| Coordinate subsystems | ❌ | ✅ (ComposedPipe) |
| Handle errors | ✅ (import failures) | ✅ (runtime errors) |
| Log version info | ✅ | ✅ |

**Total stub size target**: 200 lines (currently ~150 in this design)

---

## Next: Begin Module Extraction

With stub design finalized, we can now extract modules in dependency order:

1. ✅ `utilities.py` - No dependencies
2. ✅ `core_models.py` - Depends on utilities
3. ✅ `registry.py` - Depends on core_models
4. ✅ `persistence.py` - Depends on core_models, utilities (+ defines ComposedPipe)
5. ✅ `multimodal.py` - Depends on core_models, utilities
6. ✅ `streaming.py` - Depends on core_models, utilities
7. ✅ `tools.py` - Depends on core_models, utilities
8. ✅ Write stub_loader.py
9. ✅ Test on production
