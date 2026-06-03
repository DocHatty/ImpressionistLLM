import sys
import os

print("Starting Python dependency integration validation...")

# Add the vendor packages path to sys.path so the test can verify them
vendor_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lib", "core", "vendor_site_packages"))
if os.path.exists(vendor_path):
    sys.path.insert(0, vendor_path)
    print(f"Added vendor path: {vendor_path}")

try:
    import openrouter
    print("[OK] openrouter imported successfully")
    import orjson
    print("[OK] orjson imported successfully")
    import pydantic
    print("[OK] pydantic imported successfully")
    import certifi
    print("[OK] certifi imported successfully")
    import anyio
    print("[OK] anyio imported successfully")
    import numpy
    print("[OK] numpy imported successfully")
    from PIL import Image
    print("[OK] pillow (PIL) imported successfully")
    import cv2
    print("[OK] opencv-python (cv2) imported successfully")
    print("ALL DEPENDENCIES IMPORT AND WORK FLAWLESSLY!")
    sys.exit(0)
except Exception as e:
    print(f"FAIL: Dependency import failed: {e}", file=sys.stderr)
    sys.exit(1)
