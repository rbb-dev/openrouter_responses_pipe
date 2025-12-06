# Modular Split Progress Report

**Date**: 2025-12-06
**Branch**: `feature/modular-split`
**Status**: Core layer complete, domain layer in progress

---

## ✅ Completed: Core Layer (1,690 lines)

### Module Structure
```
src/openrouter_modules/core/
├── __init__.py          (60 lines)   - Package exports
├── config.py            (540 lines)  - Valve definitions
├── encryption.py        (200 lines)  - Fernet + LZ4
├── logging.py           (180 lines)  - SessionLogger
├── markers.py           (220 lines)  - ULID generation
└── errors.py            (290 lines)  - Error templates
```

### Key Achievements
- ✅ Zero circular dependencies
- ✅ All modules testable in isolation (no Open WebUI mocks needed)
- ✅ Clear docstrings and type hints throughout
- ✅ File sizes kept manageable (180-540 lines each)
- ✅ Following hexagonal architecture pattern from jrkropp's manifold

### Commit
```
7fa5068 feat: extract core layer with layered architecture
```

---

## 🔄 In Progress: Domain Layer (~3,500 lines estimated)

### Remaining Extractions

#### **domain/types.py** (~400 lines)
**Source**: Lines 553-606, 1435-2896
**Content**:
- TypedDicts: `FunctionCall`, `ToolCall`, `Message`, `FunctionSchema`, `ToolDefinition`, `MCPServerConfig`, `UsageStats`, `ArtifactPayload`
- Pydantic models: `CompletionsBody`, `ResponsesBody`
- Data classes: `_PipeJob`, `_QueuedToolCall`, `_ToolExecutionContext`
- Exception classes: `OpenRouterAPIError`, `_RetryableHTTPStatusError`

**Dependencies**: `core.markers`, `core.errors`

---

#### **domain/registry.py** (~600 lines)
**Source**: Lines 766-880 (ModelFamily), 881-1180 (OpenRouterModelRegistry)
**Content**:
- `ModelFamily` class: Model normalization, capability detection
- `OpenRouterModelRegistry`: Catalog fetching, caching, model registration
- Capability flags: reasoning, vision, audio, tools, web search
- Alias resolution and date-suffix stripping

**Dependencies**: `core.logging`, `core.config`

---

#### **domain/history.py** (~800 lines)
**Source**: Lines 1576-2400 (transform_messages_to_input method)
**Content**:
- Message translation: Open WebUI → Responses API format
- Artifact loading and replay logic
- ULID marker parsing and segment reconstruction
- Tool output pruning for token efficiency
- Reasoning token replay with cleanup

**Dependencies**: `core.markers`, `domain.types`

---

#### **domain/multimodal.py** (~500 lines)
**Source**: Scattered throughout main Pipe class
**Content**:
- Image processing (base64, URLs, Open WebUI uploads)
- File downloads with SSRF protection
- Video/audio validation
- Content size guards
- Storage integration

**Dependencies**: `core.config`, `core.logging`

---

#### **domain/tools.py** (~800 lines)
**Source**: Lines 9105-9308 (build_tools, strictify, dedupe)
**Content**:
- Tool schema strictification
- FIFO execution queues
- Circuit breaker logic
- Parallel execution with semaphores
- Tool output collection and formatting
- MCP server integration

**Dependencies**: `core.logging`, `domain.types`

---

#### **domain/streaming.py** (~600 lines)
**Source**: Streaming logic in main pipe() method
**Content**:
- SSE producer-consumer workers
- Delta batching and idle flush
- Citation/reasoning event formatters
- Usage metric collectors
- Completion finalizers

**Dependencies**: `core.logging`, `core.markers`, `domain.types`

---

#### **domain/engine.py** (~800 lines)
**Source**: Main orchestration logic from pipe() method
**Content**:
- `ResponsesEngine` class: Main orchestrator
- Request/response lifecycle management
- Tool loop coordination
- Streaming coordination
- Error handling and retries
- Artifact persistence coordination

**Dependencies**: All other domain modules + `core`

---

## ⏳ Pending: Adapters Layer (~3,000 lines estimated)

### adapters/openrouter/

#### **client.py** (~500 lines)
- HTTP client for OpenRouter API
- Request/response handling
- Retry logic with exponential backoff
- Timeout handling

#### **models.py** (~400 lines)
- OpenRouter-specific Pydantic models
- API request/response DTOs
- Error response parsing

#### **streaming.py** (~300 lines)
- SSE chunk parsing
- Event stream processing
- Backpressure handling

---

### adapters/openwebui/

#### **persistence.py** (~600 lines)
- SQLAlchemy table management
- Artifact CRUD operations
- Redis write-behind cache
- Cleanup workers

#### **events.py** (~300 lines)
- Event emitter wrappers
- Citation formatting
- Status message helpers

#### **file_handler.py** (~400 lines)
- File upload integration
- Storage management
- MIME type handling

#### **tools.py** (~500 lines)
- Tool registry integration
- Tool executor wrappers
- MCP server coordination

---

## ⏳ Pending: Composition Layer

### **pipe.py** (~300 lines)
- Main `Pipe` class
- Dependency injection setup
- Lifecycle methods (`on_startup`, `on_shutdown`)
- Method delegation to domain engine

### **stub_loader.py** (~200 lines)
- Ultra-minimal entry point for Open WebUI
- Dynamic module loading from GitHub
- Valve inheritance
- Error handling for missing modules

---

## 📊 Overall Progress

| Layer | Modules | Lines | Status |
|-------|---------|-------|--------|
| **Core** | 5 | 1,690 | ✅ Complete |
| **Domain** | 7 | ~3,500 | 🔄 In Progress |
| **Adapters** | 7 | ~3,000 | ⏳ Pending |
| **Composition** | 2 | ~500 | ⏳ Pending |
| **Total** | 21 | ~8,690 | **19% Complete** |

Original monolith: 9,308 lines → Target: ~8,690 lines (7% reduction from removing duplication)

---

## Next Steps

### Immediate (Domain Layer)
1. Extract `domain/types.py` (all Pydantic models + TypedDicts)
2. Extract `domain/registry.py` (ModelFamily + OpenRouterModelRegistry)
3. Extract `domain/history.py` (message translation logic)
4. Extract `domain/multimodal.py` (image/file processing)
5. Extract `domain/tools.py` (tool execution orchestration)
6. Extract `domain/streaming.py` (SSE workers + emitters)
7. Extract `domain/engine.py` (main orchestrator)

### Then (Adapters Layer)
8. Extract `adapters/openrouter/` (API client + models)
9. Extract `adapters/openwebui/` (DB, events, files, tools)

### Finally (Composition)
10. Write `pipe.py` (composition root)
11. Write `stub_loader.py` (Open WebUI entry point)
12. Test on production Open WebUI
13. Update all 14 documentation files
14. Tag `v2.0.0` release

---

## Challenges & Considerations

### 1. Large Domain Methods
The `transform_messages_to_input` method is ~800 lines. Options:
- **Option A**: Keep as single function in `domain/history.py`
- **Option B**: Break into sub-functions (preferred for readability)

### 2. Tight Coupling in Main Pipe
The monolithic `pipe()` method intermingles:
- Request parsing
- Multimodal processing
- API calls
- Tool execution
- Streaming
- Persistence

**Solution**: `domain/engine.py` will separate these concerns into:
```python
class ResponsesEngine:
    async def execute_request(self, body, valves, ...):
        # 1. Parse & validate
        # 2. Process multimodal
        # 3. Stream with tools
        # 4. Persist artifacts
```

### 3. Dependency Injection
Currently, the Pipe class has ~15 class variables (queues, semaphores, etc.).

**Solution**: Move to instance variables in `ResponsesEngine`:
```python
class ResponsesEngine:
    def __init__(self, valves, adapters):
        self.valves = valves
        self.http_client = adapters.http_client
        self.persistence = adapters.persistence
        self.tools = adapters.tools
```

---

## Testing Strategy

### Core Layer (Already Testable)
```python
# tests/core/test_markers.py
def test_generate_item_id():
    ulid = generate_item_id()
    assert len(ulid) == 20
    assert all(c in CROCKFORD_ALPHABET for c in ulid)

# tests/core/test_errors.py
def test_render_error_template():
    template = "{{#if name}}Hello {name}!{{/if}}"
    result = render_error_template(template, {"name": "World"})
    assert result == "Hello World!"
```

### Domain Layer (Mock Adapters)
```python
# tests/domain/test_registry.py
@pytest.mark.asyncio
async def test_registry_fetch_models(mock_http_client):
    registry = OpenRouterModelRegistry(http_client=mock_http_client)
    models = await registry.fetch_models()
    assert len(models) > 0
```

### Integration Tests (Full Stack)
```python
# tests/integration/test_full_request.py
@pytest.mark.asyncio
async def test_end_to_end_request(mock_owui_db, mock_openrouter):
    from stub_loader import Pipe
    pipe = Pipe()
    result = await pipe.pipe({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "test"}]
    })
    assert result is not None
```

---

## Estimated Completion Time

Given complexity:
- **Domain layer**: 2-3 days (careful extraction of intertwined logic)
- **Adapters layer**: 1-2 days (cleaner boundaries)
- **Composition + stub**: 1 day
- **Testing + docs**: 1 day

**Total**: 5-7 days for complete modular split

---

## Decision Points for Review

Before continuing extraction, please confirm:

1. **Domain/history.py size**: Keep `transform_messages_to_input` as single 800-line method, or break into smaller functions?

2. **Engine pattern**: Should `domain/engine.py` be a class (`ResponsesEngine`) or a module with functions?

3. **Extraction pace**: Continue rapid extraction now, or review core layer design first?

4. **Testing priority**: Write tests alongside extraction, or extract everything first then test?

5. **Documentation updates**: Update docs incrementally per layer, or all at the end?

---

## Files Ready for Review

1. [src/openrouter_modules/core/](src/openrouter_modules/core/) - Complete core layer
2. [ARCHITECTURE_COMPARISON.md](ARCHITECTURE_COMPARISON.md) - jrkropp analysis
3. [STUB_DESIGN.md](STUB_DESIGN.md) - Stub loader spec
4. [MODULAR_SPLIT_PLAN.md](MODULAR_SPLIT_PLAN.md) - Full implementation plan
