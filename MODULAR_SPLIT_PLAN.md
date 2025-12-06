# OpenRouter Responses Pipe - Modular Split Plan

**Status**: Draft Plan for Review
**Date**: 2025-12-06
**Current Size**: 9,308 lines in single file
**Target Architecture**: Stub loader + GitHub-hosted modules

---

## Executive Summary

This plan outlines splitting the monolithic `openrouter_responses_pipe.py` (9,308 lines) into a modular architecture with:
- **Lightweight stub** (< 500 lines) that loads into Open WebUI
- **Functional modules** hosted on GitHub and loaded dynamically via `requirements` header
- **Zero breaking changes** to existing deployments
- **Improved maintainability** through logical separation

---

## 1. Current Architecture Analysis

### File Structure
```
openrouter_responses_pipe/
├── openrouter_responses_pipe.py  (9,308 lines - MONOLITHIC)
├── pytest_bootstrap.py            (test helper)
├── docs/                          (14 markdown files)
└── tests/                         (test suite)
```

### Code Organization (Current)
Based on the internal section markers in the file:

1. **Imports** (lines 1-115)
2. **Constants & Global Config** (lines 780-861)
3. **Data Models** (lines 1450-2896)
4. **Main Controller: Pipe class** (lines 2921-8631)
5. **Utility & Helper Layer** (lines 8650-8780)
6. **Framework Integration** (lines 8782-8786)
7. **General-Purpose Utilities** (lines 8787-8971)
8. **Persistent Item Markers** (lines 8973-9104)
9. **Database & Persistence** (lines 9106-9118)
10. **Tool & Schema Utilities** (lines 9120-9308)

### Key Dependencies
- Open WebUI internals: `Chats`, `Models`, `Files`, `Users`, `upload_file_handler`
- External packages: `aiohttp`, `cryptography`, `fastapi`, `httpx`, `lz4`, `pydantic`, `sqlalchemy`, `tenacity`

---

## 2. Proposed Modular Architecture

### 2.1 Module Split Strategy

Split into **7 functional modules** + **1 stub loader**:

```
openrouter_responses_pipe/
├── stub_loader.py                    # 300-500 lines - OWUI entry point
├── modules/
│   ├── __init__.py
│   ├── core_models.py               # Data models & schemas
│   ├── registry.py                  # OpenRouter model catalog
│   ├── persistence.py               # Database, encryption, Redis
│   ├── multimodal.py                # Image/file/audio/video handling
│   ├── streaming.py                 # SSE workers & emitters
│   ├── tools.py                     # Tool execution & orchestration
│   └── utilities.py                 # Logging, helpers, markers
├── docs/
└── tests/
```

### 2.2 Module Breakdown

#### **Module 1: `core_models.py`** (~1,200 lines)
**Purpose**: Core data structures, Pydantic models, TypedDicts
**Exports**:
- `CompletionsBody`, `ResponsesBody`
- `Message`, `ToolCall`, `FunctionCall`
- `UsageStats`, `ArtifactPayload`
- `EncryptedStr`, error classes (`OpenRouterAPIError`)
- Template rendering functions

**Dependencies**: `pydantic`, `cryptography`

---

#### **Module 2: `registry.py`** (~800 lines)
**Purpose**: OpenRouter model catalog management
**Exports**:
- `OpenRouterModelRegistry` class
- Model family detection (`ModelFamily`)
- Capability flags (vision, audio, tools, reasoning)
- Model sanitization utilities

**Dependencies**: `aiohttp`, `tenacity`

---

#### **Module 3: `persistence.py`** (~1,500 lines)
**Purpose**: Database operations, encryption, Redis caching
**Exports**:
- SQLAlchemy table management (per-pipe isolation)
- Artifact CRUD operations (reasoning, tool outputs)
- Encryption/decryption (`Fernet` + LZ4 compression)
- Redis write-behind cache (pub/sub, TTL)
- Cleanup workers

**Dependencies**: `sqlalchemy`, `cryptography`, `lz4`, `redis` (optional)

---

#### **Module 4: `multimodal.py`** (~1,000 lines)
**Purpose**: Image, file, audio, video ingestion
**Exports**:
- Remote file download (SSRF protection, retries)
- Base64 decode & validation
- Open WebUI storage integration
- MIME type handling
- Size guards (MB caps)

**Dependencies**: `httpx`, `aiohttp`, `fastapi`

---

#### **Module 5: `streaming.py`** (~1,200 lines)
**Purpose**: SSE streaming, worker pools, emitters
**Exports**:
- Producer-consumer SSE queues
- Delta batching logic
- Citation/reasoning event formatters
- Usage metric collectors
- Completion finalizers

**Dependencies**: `asyncio`

---

#### **Module 6: `tools.py`** (~1,500 lines)
**Purpose**: Tool execution orchestration
**Exports**:
- Tool schema strictification
- FIFO execution queues
- Parallel workers with semaphores
- Circuit breaker logic (per-user/per-tool)
- MCP server integration
- Web search plugin wiring

**Dependencies**: `asyncio`, `tenacity`

---

#### **Module 7: `utilities.py`** (~800 lines)
**Purpose**: Cross-cutting utilities
**Exports**:
- `SessionLogger` (contextvars-based logging)
- ULID marker generation (`generate_item_id`)
- Marker parsing (`split_text_by_markers`)
- Usage stat merging
- JSON helpers, sanitization

**Dependencies**: `logging`, `contextvars`

---

#### **Stub Loader: `stub_loader.py`** (~400 lines)
**Purpose**: Minimal entry point for Open WebUI
**Responsibilities**:
1. Declare frontmatter with GitHub-based requirements
2. Dynamically import modules from GitHub
3. Instantiate `Pipe` class from composed modules
4. Forward lifecycle methods (`pipes`, `pipe`, `on_startup`, `on_shutdown`)

**Requirements Header**:
```python
"""
title: OpenRouter Responses API Manifold
author: rbb-dev
git_url: https://github.com/rbb-dev/openrouter_responses_pipe/
version: 2.0.0
requirements: https://raw.githubusercontent.com/rbb-dev/openrouter_responses_pipe/main/modules/core_models.py, https://raw.githubusercontent.com/rbb-dev/openrouter_responses_pipe/main/modules/registry.py, https://raw.githubusercontent.com/rbb-dev/openrouter_responses_pipe/main/modules/persistence.py, https://raw.githubusercontent.com/rbb-dev/openrouter_responses_pipe/main/modules/multimodal.py, https://raw.githubusercontent.com/rbb-dev/openrouter_responses_pipe/main/modules/streaming.py, https://raw.githubusercontent.com/rbb-dev/openrouter_responses_pipe/main/modules/tools.py, https://raw.githubusercontent.com/rbb-dev/openrouter_responses_pipe/main/modules/utilities.py
"""
```

**Fallback Strategy**:
- If GitHub modules unavailable, degrade gracefully (log warning, disable features)
- Optionally bundle minimal fallback for core functionality

---

## 3. Implementation Strategy

### Phase 1: Preparation (Week 1)
1. **Create branch**: `feature/modular-split`
2. **Set up module directory**: `modules/` with `__init__.py`
3. **Add CI tests**: Ensure module imports work in isolation
4. **Document interfaces**: Each module exports explicit public API

### Phase 2: Module Extraction (Week 2-3)
Extract modules in dependency order:

**Day 1-2**: `utilities.py` (no internal dependencies)
**Day 3-4**: `core_models.py` (depends on utilities)
**Day 5-6**: `registry.py` (depends on core_models)
**Day 7-8**: `persistence.py` (depends on core_models, utilities)
**Day 9-10**: `multimodal.py` (depends on core_models, utilities)
**Day 11-12**: `streaming.py` (depends on core_models, utilities)
**Day 13-14**: `tools.py` (depends on core_models, utilities)

### Phase 3: Stub Development (Week 4)
1. Create `stub_loader.py` with GitHub requirements
2. Implement dynamic import logic
3. Add error handling for missing modules
4. Test in isolated Open WebUI instance

### Phase 4: Testing & Validation (Week 5)
1. **Unit tests**: Each module independently
2. **Integration tests**: Stub + modules
3. **Load tests**: Compare performance vs monolithic
4. **Compatibility tests**: Existing valve configs

### Phase 5: Migration Path (Week 6)
1. **Publish GitHub release**: Tag v2.0.0 with modular structure
2. **Update documentation**: Migration guide for existing users
3. **Backward compatibility**: Keep monolithic version as v1.x branch
4. **Announce**: Community release notes

---

## 4. GitHub Requirements Integration

### How Open WebUI Loads Requirements

From the exploration results, Open WebUI:
1. **Parses frontmatter** using regex: `^\\s*([a-z_]+):\\s*(.*)\\s*$`
2. **Splits requirements** by comma: `[req.strip() for req in requirements.split(",")]`
3. **Installs via pip**: `subprocess.check_call([sys.executable, "-m", "pip", "install"] + req_list)`

### Challenge: GitHub URLs as Requirements

**Problem**: `pip install` expects package names or valid URLs in specific formats (e.g., `git+https://...`).

**Solutions**:

#### Option A: Use `git+` URLs (Recommended)
```python
requirements: git+https://github.com/rbb-dev/openrouter_responses_pipe.git@main#subdirectory=modules&egg=openrouter_modules
```

**Pros**: Native pip support, versioned via tags/branches
**Cons**: Requires package structure (`setup.py` or `pyproject.toml`)

#### Option B: Raw GitHub URLs with Custom Loader
```python
requirements: https://raw.githubusercontent.com/rbb-dev/openrouter_responses_pipe/main/modules/core_models.py
```

**Implementation**: Stub downloads files via `httpx`, saves to temp directory, adds to `sys.path`

**Pros**: No package structure needed
**Cons**: Manual version management, potential security concerns

#### Option C: Publish to PyPI (Future)
```python
requirements: openrouter-responses-modules>=2.0.0
```

**Pros**: Standard ecosystem, versioning, caching
**Cons**: Overhead of PyPI releases, namespace availability

---

## 5. Recommended Approach

### Step 1: Package Structure
Create a proper Python package:

```
openrouter_responses_pipe/
├── setup.py (or pyproject.toml)
├── openrouter_modules/
│   ├── __init__.py
│   ├── core_models.py
│   ├── registry.py
│   ├── persistence.py
│   ├── multimodal.py
│   ├── streaming.py
│   ├── tools.py
│   └── utilities.py
└── stub_loader.py
```

### Step 2: Stub Requirements Header
```python
"""
title: OpenRouter Responses API Manifold
version: 2.0.0
requirements: git+https://github.com/rbb-dev/openrouter_responses_pipe.git@v2.0.0#subdirectory=openrouter_modules&egg=openrouter_modules, aiohttp, cryptography, fastapi, httpx, lz4, pydantic, sqlalchemy, tenacity
"""
```

### Step 3: Stub Loader Code
```python
class Pipe:
    def __init__(self):
        try:
            from openrouter_modules import (
                core_models,
                registry,
                persistence,
                multimodal,
                streaming,
                tools,
                utilities
            )
            self.modules = {
                'core_models': core_models,
                'registry': registry,
                # ...
            }
        except ImportError as e:
            raise RuntimeError(f"Failed to load OpenRouter modules: {e}")

    async def pipes(self, body):
        # Delegate to registry module
        return await self.modules['registry'].fetch_models(...)

    async def pipe(self, body, __event_emitter__=None):
        # Compose full pipeline from modules
        ...
```

---

## 6. Benefits of Modular Split

### Developer Experience
- **Easier navigation**: 800-1,500 line modules vs 9,308 line monolith
- **Parallel development**: Multiple contributors can work on separate modules
- **Focused testing**: Test modules in isolation
- **Code review**: Smaller, logical changesets

### Operational Benefits
- **Selective updates**: Update `streaming.py` without touching `persistence.py`
- **Debugging**: Clear module boundaries simplify log analysis
- **Documentation**: Each module maps to existing docs (14 guides already split by topic)

### User Experience
- **Faster updates**: Users only download changed modules
- **Transparency**: GitHub history shows exactly what changed per module
- **Confidence**: Smaller changes = lower risk of regressions

---

## 7. Risks & Mitigations

### Risk 1: Open WebUI pip Install Limitations
**Description**: Open WebUI's `pip install` may not support `git+` URLs or custom indexes.

**Mitigation**:
1. Test in live Open WebUI instance before full migration
2. Fallback: Bundle all modules in stub if pip fails
3. Document environment variable workarounds (`PIP_OPTIONS`, `PIP_PACKAGE_INDEX_OPTIONS`)

### Risk 2: Breaking Existing Deployments
**Description**: Users with v1.x may not migrate smoothly.

**Mitigation**:
1. Maintain v1.x branch indefinitely for critical fixes
2. Provide migration script: `migrate_v1_to_v2.py`
3. Clear upgrade guide in README
4. Version bump to 2.0.0 signals breaking change

### Risk 3: Import Circular Dependencies
**Description**: Modules may inadvertently create circular imports.

**Mitigation**:
1. Strict dependency ordering (utilities → core_models → domain modules)
2. Use `TYPE_CHECKING` guards for type hints
3. CI linting with `pylint` circular dependency checks

### Risk 4: Network Failures Loading Modules
**Description**: GitHub outage or rate limiting prevents module loading.

**Mitigation**:
1. Local cache: Stub stores downloaded modules in Open WebUI's data directory
2. TTL: 24-hour cache before re-fetch
3. Fallback mode: Embed critical paths in stub (basic Completions → Responses translation)

---

## 8. Testing Plan

### Unit Tests (Per Module)
- `test_core_models.py`: Pydantic validation, encryption
- `test_registry.py`: Model catalog parsing, capability detection
- `test_persistence.py`: SQLAlchemy ops, Redis pub/sub
- `test_multimodal.py`: File downloads, MIME validation
- `test_streaming.py`: SSE queues, delta batching
- `test_tools.py`: Schema strictification, circuit breakers
- `test_utilities.py`: ULID generation, marker parsing

### Integration Tests (Stub + Modules)
- `test_stub_loader.py`: GitHub module fetching, fallback logic
- `test_end_to_end.py`: Full request lifecycle (existing test suite)

### Performance Tests
- Compare request latency: monolithic vs modular
- Measure import overhead: cold start time
- Stress test: 100 concurrent users (existing benchmark)

---

## 9. Migration Guide (For Users)

### For Existing v1.x Users

#### Option 1: Stay on v1.x (No Action Required)
- Monolithic version remains supported for critical fixes
- Branch: `v1.x-maintenance`
- No new features, security patches only

#### Option 2: Upgrade to v2.0 (Modular)
1. **Backup**: Export Open WebUI data (Settings → Backup)
2. **Update pipe**:
   - Admin → Functions → Select OpenRouter pipe
   - Replace content with `stub_loader.py` from GitHub release
   - Save (Open WebUI will `pip install` modules automatically)
3. **Verify**:
   - Check logs for "OpenRouter modules loaded successfully"
   - Test model catalog refresh
   - Send test message
4. **Reconfigure valves** (if needed):
   - No breaking changes to valve names/types
   - New valves: `MODULE_CACHE_TTL_HOURS` (default: 24)

### For New Users
1. Copy `stub_loader.py` into Open WebUI Functions
2. Set `OPENROUTER_API_KEY` valve
3. Enable pipe in model selector
4. Done! Modules load automatically from GitHub

---

## 10. Timeline

### Week 1: Preparation
- [x] Analyze current structure
- [ ] Create `feature/modular-split` branch
- [ ] Set up `openrouter_modules/` package
- [ ] Define module interfaces (docstrings, type hints)

### Week 2-3: Extraction
- [ ] Extract `utilities.py` (Day 1-2)
- [ ] Extract `core_models.py` (Day 3-4)
- [ ] Extract `registry.py` (Day 5-6)
- [ ] Extract `persistence.py` (Day 7-8)
- [ ] Extract `multimodal.py` (Day 9-10)
- [ ] Extract `streaming.py` (Day 11-12)
- [ ] Extract `tools.py` (Day 13-14)

### Week 4: Stub Development
- [ ] Write `stub_loader.py`
- [ ] Implement GitHub module fetching
- [ ] Add fallback logic
- [ ] Create `setup.py` or `pyproject.toml`

### Week 5: Testing
- [ ] Run full test suite on modular version
- [ ] Performance benchmarks
- [ ] Compatibility tests (valves, Open WebUI versions)

### Week 6: Release
- [ ] Tag `v2.0.0` on GitHub
- [ ] Update README with migration guide
- [ ] Publish release notes
- [ ] Announce in Open WebUI community

---

## 11. Architecture Decisions (CONFIRMED)

1. **GitHub Hosting**: ✅ **DECISION**: Modules in `feature/modular-split` branch of current repo. When stable, this becomes `main`, old `main` becomes `legacy/v1`.

2. **Version Strategy**: ✅ **DECISION**: Pinned by default for stability, with optional `AUTO_UPDATE_MODULES` valve to pull from `@main` for early adopters.

3. **Backward Compatibility**: ✅ **DECISION**: No backward compatibility needed (greenfield deployment). Monolithic stays in `legacy/v1` branch for reference only.

4. **Testing Requirements**: ✅ **DECISION**: Test on production Open WebUI during weekends (low usage).

5. **PyPI Publishing**: ✅ **DECISION**: Deferred. GitHub-based loading sufficient for now. Revisit after modular split proven stable.

6. **Stub Design**: ✅ **DECISION**: Ultra-minimal stub (~200 lines) with smart auto-update logic controlled by valve.

---

## 12. Next Steps

Once you answer the questions above, I'll:

1. Create detailed interface specifications for each module
2. Generate migration scripts for existing deployments
3. Set up CI/CD pipelines for modular testing
4. Draft PR template for module extraction reviews
5. Write comprehensive upgrade documentation

---

## Appendix A: Module Dependency Graph

```
utilities.py (no internal dependencies)
    ↓
core_models.py (depends: utilities)
    ↓
    ├── registry.py (depends: core_models)
    ├── persistence.py (depends: core_models, utilities)
    ├── multimodal.py (depends: core_models, utilities)
    ├── streaming.py (depends: core_models, utilities)
    └── tools.py (depends: core_models, utilities)
        ↓
    stub_loader.py (depends: all modules)
```

---

## Appendix B: Estimated Line Counts

| File | Current Lines | Proposed Lines | Reduction |
|------|--------------|----------------|-----------|
| openrouter_responses_pipe.py | 9,308 | 0 (retired) | -100% |
| stub_loader.py | - | 400 | New |
| utilities.py | - | 800 | New |
| core_models.py | - | 1,200 | New |
| registry.py | - | 800 | New |
| persistence.py | - | 1,500 | New |
| multimodal.py | - | 1,000 | New |
| streaming.py | - | 1,200 | New |
| tools.py | - | 1,500 | New |
| **Total** | 9,308 | 8,400 | -10% |

**Note**: Reduction from shared imports, reduced duplication, and removal of commented-out code.

---

## Appendix C: Example Stub Loader (Pseudocode)

```python
"""
title: OpenRouter Responses API Manifold
version: 2.0.0
requirements: git+https://github.com/rbb-dev/openrouter_responses_pipe.git@v2.0.0#subdirectory=openrouter_modules&egg=openrouter_modules, aiohttp, cryptography, fastapi, httpx, lz4, pydantic, sqlalchemy, tenacity
"""

class Pipe:
    def __init__(self):
        self._load_modules()
        self._initialize_pipe()

    def _load_modules(self):
        """Dynamically import modules from GitHub package."""
        try:
            from openrouter_modules import (
                core_models,
                registry,
                persistence,
                multimodal,
                streaming,
                tools,
                utilities
            )
            self.modules = {
                'core_models': core_models,
                'registry': registry,
                'persistence': persistence,
                'multimodal': multimodal,
                'streaming': streaming,
                'tools': tools,
                'utilities': utilities
            }
            LOGGER.info("OpenRouter modules loaded successfully")
        except ImportError as e:
            LOGGER.error(f"Failed to load modules: {e}")
            raise RuntimeError("OpenRouter modules unavailable")

    def _initialize_pipe(self):
        """Initialize the composed pipeline from modules."""
        # Create registry instance
        self.registry = self.modules['registry'].OpenRouterModelRegistry()

        # Create persistence manager
        self.persistence = self.modules['persistence'].PersistenceManager(...)

        # Create streaming manager
        self.streaming = self.modules['streaming'].StreamingManager(...)

        # Create tool executor
        self.tools = self.modules['tools'].ToolExecutor(...)

        LOGGER.info("Pipe initialized")

    async def pipes(self, body: dict) -> list[dict]:
        """Delegate to registry for model discovery."""
        return await self.registry.fetch_and_register_models(...)

    async def pipe(self, body: dict, __event_emitter__=None):
        """Main request handler - compose modules."""
        # 1. Parse request
        parsed = self.modules['core_models'].CompletionsBody.model_validate(body)

        # 2. Load multimodal content
        enriched = await self.modules['multimodal'].process_images(parsed)

        # 3. Translate to Responses API
        responses_body = self.modules['core_models'].translate_to_responses(enriched)

        # 4. Execute tools
        if responses_body.tools:
            responses_body = await self.tools.execute_tools(responses_body)

        # 5. Stream response
        async for event in self.streaming.stream_response(responses_body):
            if __event_emitter__:
                await __event_emitter__(event)
            yield event

        # 6. Persist artifacts
        await self.persistence.save_artifacts(...)

    async def on_startup(self):
        """Lifecycle: startup hook."""
        await self.registry.initialize()
        await self.persistence.initialize()

    async def on_shutdown(self):
        """Lifecycle: shutdown hook."""
        await self.persistence.cleanup()
```
