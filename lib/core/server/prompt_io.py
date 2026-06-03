"""
Prompt file I/O mixin for PromptHandler.
Handles prompt file reading, parsing, saving, and deletion with caching.
"""
from pathlib import Path
import os
import tempfile
from urllib.parse import unquote
import re

from ._shared import (
    json,
    logger,
    _DELIM_EXAMPLES,
    _DELIM_EXAMPLE,
    _DELIM_OUTPUT,
)


class PromptIOMixin:
    """Mixin providing prompt file CRUD operations with caching."""

    _PROMPT_META_DIRNAME = "_meta"
    _MAX_PROMPT_FILE_BYTES = 512 * 1024

    def _get_prompt_meta_dir(self):
        meta_dir = self.script_dir / "prompts" / self._PROMPT_META_DIRNAME
        meta_dir.mkdir(parents=True, exist_ok=True)
        return meta_dir

    def _get_prompt_sidecar_paths(self, name):
        meta_dir = self._get_prompt_meta_dir()
        return {
            "params": meta_dir / f"{name}.params.json",
            "output": meta_dir / f"{name}.output.json",
        }

    def _safe_unlink(self, path: Path):
        try:
            if path.exists():
                path.unlink()
        except FileNotFoundError:
            pass

    def _write_text_atomic(self, path: Path, content: str, encoding: str = "utf-8"):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _sanitize_prompt_text(self, content: str) -> str:
        content = (content or "").replace("\r\n", "\n")
        return content.strip()

    def _parse_prompt_text(self, content: str):
        """Parse prompt text while tolerating leading comments/blank lines."""
        normalized = (content or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        model = ""
        body_start = 0

        for index, raw_line in enumerate(lines):
            line = raw_line.replace("\ufeff", "")
            stripped = line.strip()
            if not stripped or stripped.startswith(";"):
                continue
            model = stripped
            body_start = index + 1
            break

        body_text = "\n".join(lines[body_start:]).strip() if model else ""
        examples = []
        base_content = body_text

        # Tolerant search for ---EXAMPLES--- section (case-insensitive, allows space around/inside delimiters)
        match_examples = re.search(r'(?i)---\s*EXAMPLES\s*---', body_text)
        if match_examples:
            delim_start, delim_end = match_examples.span()
            base_content = body_text[:delim_start].strip()
            examples_section = body_text[delim_end:].strip()

            # Tolerant split for ---EXAMPLE--- (case-insensitive, allows space around/inside delimiters)
            ex_parts = re.split(r'(?i)---\s*EXAMPLE\s*---', examples_section)
            for ex in ex_parts:
                ex = ex.strip()
                if not ex:
                    continue
                
                # Tolerant search for ---OUTPUT--- within the example
                match_output = re.search(r'(?i)---\s*OUTPUT\s*---', ex)
                if match_output:
                    out_start, out_end = match_output.span()
                    input_text = ex[:out_start].strip()
                    output_text = ex[out_end:].strip()
                    examples.append(
                        {
                            "input": input_text,
                            "output": output_text,
                        }
                    )

        return {
            "model": model,
            "prompt": base_content,
            "examples": examples,
            "raw": content,
        }

    def _normalize_user_parameters(self, user_parameters):
        if not isinstance(user_parameters, dict):
            return {}
        try:
            encoded = json.dumps(user_parameters)
            decoded = json.loads(encoded)
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {}

    def _read_json_file(self, path: Path):
        try:
            if not path.exists():
                return None
            raw = path.read_text(encoding="utf-8")
            if not raw.strip():
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            logger.warning("Failed to read JSON sidecar: %s", path)
            return None


    def _get_prompt_list_cached(self):
        """Get list of prompt files with caching based on directory mtime."""
        prompts_dir = self.script_dir / "prompts"
        latest_mtime = 0
        names = []
        for f in prompts_dir.glob("*.txt"):
            if "processors" in str(f):
                continue
            try:
                stat_mtime = f.stat().st_mtime
            except OSError:
                continue
            latest_mtime = max(latest_mtime, stat_mtime)
            names.append(f.stem)

        sorted_names = tuple(sorted(names, key=str.lower))
        cache_key = (sorted_names, latest_mtime)
        if (
            self._prompt_list_cache is not None
            and self._prompt_list_cache_key == cache_key
        ):
            return self._prompt_list_cache

        data = [{"name": name} for name in sorted_names]
        self._prompt_list_cache = data
        self._prompt_list_cache_key = cache_key
        return data

    def _parse_prompt_file_cached(self, path: Path):
        """Parse prompt file with caching based on mtime."""
        key = str(path)
        with self._cache_lock:
            cache_entry = self._prompt_parsed_cache.get(key)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None

        if cache_entry and cache_entry.get("mtime") == mtime:
            with self._cache_lock:
                self._prompt_parsed_cache[key] = (
                    self._prompt_parsed_cache.pop(key, cache_entry)
                )
            return cache_entry["data"]

        if path.stat().st_size > self._MAX_PROMPT_FILE_BYTES:
            raise ValueError(f"Prompt file too large: {path.name}")

        content = path.read_text(encoding="utf-8-sig")
        parsed = self._parse_prompt_text(content)
        with self._cache_lock:
            self._prompt_parsed_cache[key] = {"mtime": mtime, "data": parsed}
            self._prune_lru_cache(
                self._prompt_parsed_cache,
                self._prompt_parsed_cache_max_entries,
            )
        return parsed

    def list_prompts(self):
        """List all available prompts."""
        prompts = self._get_prompt_list_cached()
        self.send_json({"success": True, "data": prompts})

    def get_prompt(self, name):
        """Get a specific prompt by name."""
        name = unquote(name)
        valid_name, validation_error = self._validate_prompt_name(name)
        if not valid_name:
            self.send_json({"error": validation_error}, 400)
            return
        path = self.script_dir / "prompts" / f"{name}.txt"
        if not path.exists():
            self.send_json({"error": "Not found"}, 404)
            return

        parsed = self._parse_prompt_file_cached(path)
        if not parsed:
            self.send_json({"error": "Failed to load prompt"}, 500)
            return

        model = parsed.get("model", "")
        base_content = parsed.get("prompt", "")
        examples = parsed.get("examples", [])
        content = parsed.get("raw", "")

        parameter_mode = "default"
        user_parameters = {}
        output_settings = self._get_default_output_settings(name)
        sidecars = self._get_prompt_sidecar_paths(name)

        params_sidecar = self._read_json_file(sidecars["params"])
        if params_sidecar:
            parameter_mode = params_sidecar.get("mode", "default")
            user_parameters = self._normalize_user_parameters(params_sidecar.get("user_parameters", {}))

        output_sidecar = self._read_json_file(sidecars["output"])
        if output_sidecar:
            output_settings.update(output_sidecar)
        output_settings = self._normalize_output_settings(output_settings)

        base_content = self._sanitize_prompt_text(base_content)

        self.send_json(
            {
                "success": True,
                "data": {
                    "name": name,
                    "model": model,
                    "content": base_content,
                    "examples": examples,
                    "parameter_mode": parameter_mode,  # "default" or "user"
                    "user_parameters": user_parameters,  # Custom user parameters
                    "output_settings": output_settings,  # Output behavior settings
                },
            }
        )

    def save_prompt(self, body):
        """Save a prompt file."""
        try:
            try:
                data = json.loads(body or "{}")
            except json.JSONDecodeError:
                self.send_json({"error": "Invalid JSON"}, 400)
                return

            if not isinstance(data, dict):
                self.send_json({"error": "Invalid request"}, 400)
                return

            name = (data.get("name") or "").strip()
            model = (data.get("model") or "").strip()
            content = (data.get("content") or "").strip()
            previous_name = (data.get("previous_name") or "").strip()
            examples = data.get("examples", [])
            parameter_mode = data.get(
                "parameter_mode", "default"
            )  # "default" or "user"
            user_parameters = data.get("user_parameters", {})  # Custom user parameters
            output_settings = data.get(
                "output_settings", {}
            )  # Output behavior settings
            default_output_settings = self._get_default_output_settings(name)
            if not isinstance(output_settings, dict):
                output_settings = {}
            output_settings = {**default_output_settings, **output_settings}
            output_settings = self._normalize_output_settings(output_settings)

            content = self._sanitize_prompt_text(content)
            user_parameters = self._normalize_user_parameters(user_parameters)

            if not name or not model or not content:
                self.send_json({"error": "Missing fields"}, 400)
                return

            if len(model) > 200:
                self.send_json({"error": "Model ID too long"}, 400)
                return
            if any(ord(ch) < 32 or ch.isspace() for ch in model):
                self.send_json({"error": "Invalid model ID"}, 400)
                return

            try:
                canonical_model, model_error, suggestions = self._canonicalize_openrouter_model_id(model)
            except Exception:
                canonical_model, model_error, suggestions = model, None, []
            if model_error:
                self.send_json({"error": model_error, "suggestions": suggestions}, 400)
                return
            model = canonical_model

            if not isinstance(examples, list):
                self.send_json({"error": "Examples must be a list"}, 400)
                return
            if len(examples) > 20:
                self.send_json({"error": "Too many examples"}, 400)
                return

            valid_name, validation_error = self._validate_prompt_name(name)
            if not valid_name:
                self.send_json({"error": validation_error}, 400)
                return

            if previous_name:
                valid_previous_name, validation_previous_error = self._validate_prompt_name(
                    previous_name
                )
                if not valid_previous_name:
                    self.send_json({"error": validation_previous_error}, 400)
                    return

            # Build file content
            parts = [f"{model}\n\n{content}"]

            # Add examples
            if examples:
                parts.append("\n\n" + _DELIM_EXAMPLES)
                for ex in examples:
                    if not isinstance(ex, dict):
                        continue
                    parts.append(
                        f"\n\n{_DELIM_EXAMPLE}\n{ex.get('input', '')}\n{_DELIM_OUTPUT}\n{ex.get('output', '')}"
                    )

            file_content = "".join(parts)
            if len(file_content.encode("utf-8")) > self._MAX_PROMPT_FILE_BYTES:
                self.send_json({"error": "Prompt file too large"}, 413)
                return

            path = self.script_dir / "prompts" / f"{name}.txt"
            sidecars = self._get_prompt_sidecar_paths(name)
            previous_path = (
                self.script_dir / "prompts" / f"{previous_name}.txt"
                if previous_name and previous_name != name
                else None
            )

            with self._prompt_write_lock:
                if previous_path:
                    if not previous_path.exists():
                        self.send_json({"error": "Previous prompt not found"}, 404)
                        return
                    if path.exists():
                        self.send_json({"error": "Prompt already exists"}, 409)
                        return
                elif not previous_name and path.exists():
                    self.send_json({"error": "Prompt already exists"}, 409)
                    return

                try:
                    self._write_text_atomic(path, file_content)

                    # Persist parameter settings separately from the prompt text.
                    if parameter_mode == "user" and user_parameters:
                        self._write_text_atomic(
                            sidecars["params"],
                            json.dumps(
                                {
                                    "mode": parameter_mode,
                                    "user_parameters": user_parameters,
                                },
                                indent=2,
                            ),
                        )
                    else:
                        self._safe_unlink(sidecars["params"])

                    # Persist non-default output settings separately from the prompt text.
                    if output_settings and output_settings != default_output_settings:
                        self._write_text_atomic(
                            sidecars["output"],
                            json.dumps(output_settings, indent=2),
                        )
                    else:
                        self._safe_unlink(sidecars["output"])
                except Exception:
                    if previous_path:
                        self._safe_unlink(path)
                        for sidecar_path in sidecars.values():
                            self._safe_unlink(sidecar_path)
                    raise

                if previous_path:
                    self._safe_unlink(previous_path)
                    previous_sidecars = self._get_prompt_sidecar_paths(previous_name)
                    for previous_sidecar in previous_sidecars.values():
                        self._safe_unlink(previous_sidecar)

            # Touch the prompt .txt file after writing sidecars.  When only
            # sidecar files change, the modification time of the .txt file
            # remains stale, causing CachedPromptLoader to think nothing
            # changed and to skip invalidation.  Bumping the mtime here
            # ensures the next fingerprint includes this update.
            try:
                # Use os.utime via pathlib.Path.touch to update the mtime
                path.touch()
            except Exception:
                # If touch fails, ignore; the fingerprint function will
                # still pick up sidecar mtimes directly.
                pass

            # Invalidate prompt caches so next read reflects file mutations immediately.
            with self._cache_lock:
                self._prompt_list_cache = None
                self._prompt_list_cache_key = None
                self._prompt_parsed_cache.pop(str(path), None)
                if previous_path:
                    self._prompt_parsed_cache.pop(str(previous_path), None)

            self.send_json({"success": True, "model": model})
        except Exception as e:
            logger.exception("save_prompt failed")
            self.send_json({"error": str(e)}, 500)

    def delete_prompt(self, body):
        """Delete a prompt file."""
        try:
            try:
                data = json.loads(body or "{}")
            except json.JSONDecodeError:
                self.send_json({"error": "Invalid JSON"}, 400)
                return
            if not isinstance(data, dict):
                self.send_json({"error": "Invalid request"}, 400)
                return
            name = (data.get("name") or "").strip()
            if not name:
                self.send_json({"error": "Missing name"}, 400)
                return
            valid_name, validation_error = self._validate_prompt_name(name)
            if not valid_name:
                self.send_json({"error": validation_error}, 400)
                return
            path = self.script_dir / "prompts" / f"{name}.txt"
            sidecars = self._get_prompt_sidecar_paths(name)
            with self._prompt_write_lock:
                self._safe_unlink(path)
                for sidecar_path in sidecars.values():
                    self._safe_unlink(sidecar_path)

            with self._cache_lock:
                self._prompt_list_cache = None
                self._prompt_list_cache_key = None
                self._prompt_parsed_cache.pop(str(path), None)

            self.send_json({"success": True})
        except Exception as e:
            logger.exception("delete_prompt failed")
            self.send_json({"error": str(e)}, 500)
