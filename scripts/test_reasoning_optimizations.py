import sys
import os

# Add required paths
base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, base_dir)

# Add vendor packages
vendor_path = os.path.join(base_dir, "lib", "core", "vendor_site_packages")
if os.path.exists(vendor_path):
    sys.path.insert(0, vendor_path)

from lib.core.server.openrouter_modules.openrouter_client_helpers import OpenRouterClientHelpersMixin
from lib.core.server.chat import ChatMixin

class DummyOpenRouterClient(OpenRouterClientHelpersMixin):
    def get_api_key(self):
        return "mock_key"

class DummyChatHandler(ChatMixin):
    pass

def test_normalize_chat_reasoning():
    client = DummyOpenRouterClient()
    
    # 1. Gemini / Anthropic Models (Strictly max_tokens, no effort)
    # Case A: User specifies only effort. It should map effort to max_tokens.
    req = {
        "model": "google/gemini-2.5-pro",
        "reasoning": {"effort": "high"}
    }
    client._normalize_chat_reasoning(req, None)
    assert "max_tokens" in req["reasoning"], "Should map effort to max_tokens for Gemini"
    assert "effort" not in req["reasoning"], "Should strip effort parameter for Gemini"
    assert req["reasoning"]["max_tokens"] == 4096, f"Expected 4096, got {req['reasoning']['max_tokens']}"
    
    # Case B: User specifies only max_tokens for Claude (effort provider). It should map max_tokens to effort.
    req = {
        "model": "anthropic/claude-3.7-sonnet",
        "reasoning": {"max_tokens": 1500}
    }
    client._normalize_chat_reasoning(req, None)
    assert "effort" in req["reasoning"], "Should map max_tokens to effort for Claude"
    assert "max_tokens" not in req["reasoning"], "Should strip max_tokens parameter for Claude"
    assert req["reasoning"]["effort"] == "medium", f"Expected medium, got {req['reasoning']['effort']}"

    # Case C: Conflicting parameters supplied. Should clean up to only max_tokens.
    req = {
        "model": "google/gemini-3.5-pro",
        "reasoning": {"effort": "low", "max_tokens": 1024}
    }
    client._normalize_chat_reasoning(req, None)
    assert req["reasoning"]["max_tokens"] == 1024, "Should keep max_tokens"
    assert "effort" not in req["reasoning"], "Should strip conflicting effort"

    # 2. OpenAI / Grok Models (Strictly effort, no max_tokens)
    # Case A: User specifies only max_tokens. It should map max_tokens to effort.
    req = {
        "model": "openai/o1-preview",
        "reasoning": {"max_tokens": 2500}
    }
    client._normalize_chat_reasoning(req, None)
    assert "effort" in req["reasoning"], "Should map max_tokens to effort for OpenAI"
    assert "max_tokens" not in req["reasoning"], "Should strip max_tokens parameter for OpenAI"
    assert req["reasoning"]["effort"] == "medium", f"Expected medium, got {req['reasoning']['effort']}"

    # Case B: User specifies only effort. It should keep effort intact.
    req = {
        "model": "openai/o3-mini",
        "reasoning": {"effort": "low"}
    }
    client._normalize_chat_reasoning(req, None)
    assert req["reasoning"]["effort"] == "low", "Should keep effort"
    assert "max_tokens" not in req["reasoning"], "Should not inject max_tokens"

    # 3. Test _ensure_reasoning_disabled_by_default
    # Case A: Gemini Flash model (should default to max_tokens=512 for minimal reasoning)
    req = {
        "model": "google/gemini-3.5-flash"
    }
    client._ensure_reasoning_disabled_by_default(req, None)
    assert "reasoning" in req, "Should set reasoning defaults for Gemini Flash"
    assert req["reasoning"].get("max_tokens") == 512, f"Expected max_tokens 512, got {req['reasoning'].get('max_tokens')}"
    assert "effort" not in req["reasoning"], "Should not use effort parameter for Gemini"

    # Case B: OpenAI o1-pro model (should default to effort=medium)
    req = {
        "model": "openai/o1-pro"
    }
    client._ensure_reasoning_disabled_by_default(req, None)
    assert "reasoning" in req, "Should set reasoning defaults for o1-pro"
    assert req["reasoning"].get("effort") == "medium", f"Expected effort medium, got {req['reasoning'].get('effort')}"
    assert "max_tokens" not in req["reasoning"], "Should not use max_tokens parameter for OpenAI"

    # Case C: Standard model without reasoning capabilities
    req = {
        "model": "meta-llama/llama-3-70b-instruct"
    }
    client._ensure_reasoning_disabled_by_default(req, None)
    assert "reasoning" not in req, "Should not set reasoning for standard model"

    print("[PASS] _normalize_chat_reasoning and _ensure_reasoning_disabled_by_default tests passed successfully!")

def test_extract_reasoning_text():
    handler = DummyChatHandler()
    
    # Test case A: Simple string reasoning field
    data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "The final answer is 42.",
                    "reasoning": "Let us think about the ultimate question."
                }
            }
        ]
    }
    content = handler._extract_chat_response_text(data)
    assert content == "The final answer is 42."
    assert data.get("reasoning") == "Let us think about the ultimate question.", "Should extract and inject reasoning text"

    # Test case B: Structured object steps reasoning field
    data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Done.",
                    "reasoning": {
                        "steps": [
                            {"text": "Step 1: Check hypothesis."},
                            {"text": "Step 2: Conclude."}
                        ]
                    }
                }
            }
        ]
    }
    content = handler._extract_chat_response_text(data)
    assert content == "Done."
    assert "Step 1: Check hypothesis." in data.get("reasoning"), "Should extract steps text"
    
    print("[PASS] _extract_chat_response_text tests passed successfully!")

def test_json_adapter_optimizations():
    import numpy as np
    from lib.core.server._shared import json
    
    # 1. Test normal serialization
    obj = {"name": "Test", "val": 123}
    encoded = json.dumps(obj)
    assert "name" in encoded and "val" in encoded
    assert json.loads(encoded) == obj
    
    # 2. Test Numpy array serialization (requires OPT_SERIALIZE_NUMPY)
    arr = np.array([[1, 2], [3, 4]], dtype=np.int32)
    encoded_arr = json.dumps(arr)
    assert encoded_arr == "[[1,2],[3,4]]", f"Expected [[1,2],[3,4]], got {encoded_arr}"
    assert json.loads(encoded_arr) == [[1, 2], [3, 4]]
    
    # 3. Test robust fallback for unsupported custom types (like sets) using default parameter
    def fallback_handler(o):
        if isinstance(o, set):
            return list(o)
        raise TypeError
        
    custom_obj = {"my_set": {5, 6}}
    encoded_fallback = json.dumps(custom_obj, default=fallback_handler)
    # The order of a set when converted to list can be anything, but let's check values are present
    decoded = json.loads(encoded_fallback)
    assert set(decoded["my_set"]) == {5, 6}
    
    # 4. Test strict standard library fallback for unhandled exceptions
    # orjson doesn't serialize complexes natively; complex should trigger standard json serializer fallback
    complex_obj = {"complex": 1 + 2j}
    def complex_default(o):
        if isinstance(o, complex):
            return [o.real, o.imag]
        raise TypeError
    encoded_complex = json.dumps(complex_obj, default=complex_default)
    assert json.loads(encoded_complex) == {"complex": [1.0, 2.0]}
    
    print("[PASS] json_adapter_optimizations tests passed successfully!")

if __name__ == "__main__":
    try:
        test_normalize_chat_reasoning()
        test_extract_reasoning_text()
        test_json_adapter_optimizations()
        print("\nALL REASONING & JSON SYSTEM TESTS PASSED SUCCESSFULLY!")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n[FAIL] Test assertion failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        import traceback
        print("\n[ERROR] Test run encountered error:")
        traceback.print_exc()
        sys.exit(1)
