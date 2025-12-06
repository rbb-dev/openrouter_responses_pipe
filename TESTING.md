# Testing the Modular Split - Step by Step

## Prerequisites

1. **Push your branch to GitHub:**
   ```bash
   cd /mnt/c/Work/Dev/openrouter_responses_pipe
   git push origin feature/modular-split
   ```

2. **Verify the branch exists:**
   - Go to: https://github.com/rbb-dev/openrouter_responses_pipe/tree/feature/modular-split
   - Confirm you see the `src/openrouter_modules/` directory

## Test 1: Manual pip Install (Local Test)

**Test that the package installs correctly from git:**

```bash
# Create a test virtual environment
python3 -m venv /tmp/test-modular-venv
source /tmp/test-modular-venv/bin/activate

# Install from the branch
pip install git+https://github.com/rbb-dev/openrouter_responses_pipe.git@feature/modular-split

# Verify installation
python3 -c "from openrouter_modules.core.encryption import EncryptedStr; print('✅ Core imports work')"
python3 -c "from openrouter_modules.domain.registry import OpenRouterModelRegistry; print('✅ Domain imports work')"
python3 -c "from openrouter_modules.adapters.openwebui.persistence import ArtifactPersistence; print('✅ Adapter imports work')"

# Check installed package
pip list | grep openrouter

# Cleanup
deactivate
rm -rf /tmp/test-modular-venv
```

**Expected Output:**
```
✅ Core imports work
✅ Domain imports work
✅ Adapter imports work
openrouter-modules    2.0.0
```

## Test 2: Open WebUI Auto-Install (Full Integration Test)

**Test that Open WebUI automatically installs the modules:**

### Step 1: Prepare the Monolith File

```bash
# Copy the monolith to a test location
cp /mnt/c/Work/Dev/openrouter_responses_pipe/openrouter_responses_pipe/openrouter_responses_pipe.py \
   /tmp/openrouter_test.py

# Verify the requirements header
head -n 15 /tmp/openrouter_test.py | grep "requirements:"
```

**Should show:**
```
requirements: git+https://github.com/rbb-dev/openrouter_responses_pipe.git@feature/modular-split
```

### Step 2: Deploy to Open WebUI

1. **Open your Open WebUI instance** (http://localhost:3000 or your deployment URL)

2. **Navigate to Admin Panel:**
   - Click your profile → Admin Panel
   - Go to Functions (or Pipelines, depending on your version)

3. **Upload the Pipeline:**
   - Click "+ Add Function" or "Import Function"
   - Upload `/tmp/openrouter_test.py`
   - Or paste the entire file contents

4. **Watch the Installation:**
   - Open WebUI should show: "Installing requirements..."
   - Monitor the Open WebUI logs:
     ```bash
     # In your Open WebUI directory
     docker logs -f open-webui  # if running in Docker
     # OR
     tail -f backend/logs/*.log  # if running locally
     ```

5. **Look for these log messages:**
   ```
   Installing requirements: git+https://github.com/rbb-dev/openrouter_responses_pipe.git@feature/modular-split
   Successfully installed openrouter-modules-2.0.0
   Loaded module: function_<id>
   ```

### Step 3: Verify Installation

**Check if modules are available:**

```bash
# SSH into your Open WebUI container (if Docker)
docker exec -it open-webui bash

# OR if running locally, activate Open WebUI's venv
source /path/to/open-webui/.venv/bin/activate

# Test imports
python3 -c "import openrouter_modules; print(openrouter_modules.__file__)"
python3 -c "from openrouter_modules.core import encryption; print('✅ Modules installed')"

# List installed packages
pip list | grep openrouter
```

**Expected:**
```
/path/to/site-packages/openrouter_modules/__init__.py
✅ Modules installed
openrouter-modules    2.0.0
```

### Step 4: Test the Pipeline

1. **In Open WebUI UI:**
   - Go to a chat
   - Select an OpenRouter model from the dropdown
   - Send a test message: "Hello, test message"

2. **Check for errors:**
   - Pipeline should load without import errors
   - Response should work normally
   - Check logs for any missing module errors

## Test 3: Verify Module Structure

**Confirm all extracted modules are accessible:**

```python
# Create test script: /tmp/test_modules.py
"""Test all extracted modules can be imported."""

def test_core_layer():
    from openrouter_modules.core.encryption import EncryptedStr
    from openrouter_modules.core.logging import SessionLogger
    from openrouter_modules.core.markers import generate_item_id
    from openrouter_modules.core.errors import _render_error_template
    from openrouter_modules.core.config import _OPENROUTER_TITLE
    print("✅ Core layer: All 5 modules import successfully")

def test_domain_layer():
    from openrouter_modules.domain.types import CompletionsBody
    from openrouter_modules.domain.registry import OpenRouterModelRegistry
    from openrouter_modules.domain.history import transform_messages_to_input
    from openrouter_modules.domain.multimodal import download_remote_file
    from openrouter_modules.domain.tools import build_tools
    print("✅ Domain layer: All 5 modules import successfully")

def test_adapter_layer():
    from openrouter_modules.adapters.openrouter.models import _format_openrouter_error_markdown
    from openrouter_modules.adapters.openwebui.persistence import ArtifactPersistence
    from openrouter_modules.adapters.openwebui.events import wrap_event_emitter
    from openrouter_modules.adapters.openwebui.file_handler import upload_to_owui_storage
    from openrouter_modules.adapters.openwebui.tools import ToolExecutionAdapter
    print("✅ Adapter layer: All modules import successfully")

if __name__ == "__main__":
    test_core_layer()
    test_domain_layer()
    test_adapter_layer()
    print("\n🎉 All 18 modules verified!")
```

**Run the test:**
```bash
# In Open WebUI environment
python3 /tmp/test_modules.py
```

## Test 4: Check Package Metadata

```bash
# Verify package details
pip show openrouter-modules
```

**Expected output:**
```
Name: openrouter-modules
Version: 2.0.0
Summary: Modular components for OpenRouter Responses API pipe (Open WebUI)
Home-page: https://github.com/rbb-dev/openrouter_responses_pipe
Author: rbb-dev
License: MIT
Location: /path/to/site-packages
Requires: aiohttp, cryptography, fastapi, httpx, lz4, pydantic, pydantic_core, sqlalchemy, tenacity
```

## Troubleshooting

### Issue: "No module named 'openrouter_modules'"

**Cause:** Package didn't install correctly

**Fix:**
```bash
# Reinstall manually
pip uninstall openrouter-modules -y
pip install git+https://github.com/rbb-dev/openrouter_responses_pipe.git@feature/modular-split --force-reinstall

# Verify
python3 -c "import openrouter_modules; print('OK')"
```

### Issue: "Failed to install requirements"

**Cause:** Git URL incorrect or branch not pushed

**Fix:**
```bash
# Verify branch exists
git ls-remote https://github.com/rbb-dev/openrouter_responses_pipe.git feature/modular-split

# Should show:
# <commit-hash>  refs/heads/feature/modular-split

# If empty, push the branch:
git push origin feature/modular-split
```

### Issue: Import errors in monolith

**Cause:** Monolith still has inline code, not using extracted modules yet

**Status:** This is EXPECTED. The current state is:
- ✅ Extracted modules installed and importable
- ⏳ Monolith still contains inline implementations
- 📋 Next step: Update monolith to import from extracted modules

## Success Criteria

✅ **Test 1 Passed:** Package installs from git
✅ **Test 2 Passed:** Open WebUI auto-installs on upload
✅ **Test 3 Passed:** All 18 modules importable
✅ **Test 4 Passed:** Package metadata correct

## Next Steps

Once all tests pass, the modular architecture is ready to use! The final step would be to update the monolith to actually import and use the extracted modules (Hybrid Pattern).

**Current State:**
```
Monolith (9,291 lines) - Still has inline code
     ↓ (requirements header)
Auto-installs → openrouter_modules (8,674 lines) - Ready but unused
```

**Target State (Hybrid Pattern):**
```
Monolith (smaller) - Imports from openrouter_modules
     ↓ (uses extracted modules)
openrouter_modules (8,674 lines) - Actually used
```

---

**Report any issues to:** https://github.com/rbb-dev/openrouter_responses_pipe/issues
