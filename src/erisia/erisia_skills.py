import os
import sys
import json
import re
import logging
import datetime
import time
import ast
import shutil
import tempfile
import subprocess
import importlib.util
import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set, Callable
from datetime import UTC
from erisia.erisia_llm import query_llm
from erisia.erisia_memory_manager import tools_collection, memory_lock

# Calculate the actual project root
BASE_DIR = Path(__file__).resolve().parents[2]
BASE_DIR_STR = str(BASE_DIR)

# Paths
SKILLS_DIR = os.path.join(BASE_DIR_STR, "skills")
PENDING_SKILLS_DIR = os.path.join(BASE_DIR_STR, "skills", "pending")
SANDBOX_DIR = os.path.join(BASE_DIR_STR, "skills", "sandbox")
SKILLS_PATH = Path(SKILLS_DIR)
PENDING_SKILLS_PATH = Path(PENDING_SKILLS_DIR)
SANDBOX_PATH = Path(SANDBOX_DIR)

# Constants & Cache
_skills_cache: Any = None
_skills_cache_timestamp: float = 0.0
SKILLS_CACHE_TTL_SECONDS: float = 30.0
TOOL_INDEX_BOOTSTRAPPED = False

KNOWN_LOCAL_MODULES = {
    "erisia_core",
    "erisia_body",
    "erisia_daemon",
    "erisia_cognition",
    "erisia_graph",
    "erisia_episodic_memory",
}

THIRD_PARTY_PACKAGE_MAP = {
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "dotenv": "python-dotenv",
    "lxml": "lxml",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "Crypto": "pycryptodome",
}

def _normalize_skill_name(skill_name: str | None) -> str:
    """Normalize a skill name into a reusable snake_case identifier."""
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(skill_name or "").strip().lower())
    normalized = normalized.strip("_")
    return normalized or "autonomous_skill"

class SkillRegistry:
    """
    Single source of truth for all Erisia skills.
    Tracks active skills, pending skills, skill versions,
    and improvement history.
    """

    __slots__ = ("_skills_dir", "_pending_dir", "_registry_path", "_registry", "_logger")

    def __init__(
        self,
        skills_dir: Path,
        pending_dir: Path,
        logger: logging.Logger,
    ) -> None:
        self._skills_dir = skills_dir
        self._pending_dir = pending_dir
        self._registry_path = skills_dir / "_registry.json"
        self._logger = logger
        self._registry: dict[str, dict[str, Any]] = {}
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._pending_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        """Load registry from disk."""
        try:
            if self._registry_path.exists():
                self._registry = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._logger.error("Registry load failed: %s", exc)
            self._registry = {}

    def _save(self) -> None:
        """Persist registry to disk."""
        try:
            self._registry_path.write_text(
                json.dumps(self._registry, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            self._logger.error("Registry save failed: %s", exc)

    def register(
        self,
        skill_name: str,
        description: str,
        location: str,
        version: int = 1,
    ) -> None:
        """Register or update a skill in the registry."""
        normalized_name = _normalize_skill_name(skill_name)
        existing = self._registry.get(normalized_name, {})
        now_text = datetime.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._registry[normalized_name] = {
            "name": normalized_name,
            "description": description,
            "location": location,
            "version": max(version, int(existing.get("version", 0))),
            "forge_count": int(existing.get("forge_count", 0)) + 1,
            "last_updated": now_text,
            "first_forged": existing.get("first_forged", now_text),
        }
        self._save()

    def exists(self, skill_name: str) -> bool:
        """Return True if skill exists in active or pending."""
        normalized_name = _normalize_skill_name(skill_name)
        if normalized_name in self._registry:
            return True
        active = (self._skills_dir / f"{normalized_name}.py").exists()
        pending = (self._pending_dir / f"{normalized_name}.py").exists()
        return active or pending

    def get_location(self, skill_name: str) -> str | None:
        """Return 'active', 'pending', or None."""
        normalized_name = _normalize_skill_name(skill_name)
        entry = self._registry.get(normalized_name)
        if entry:
            return str(entry.get("location", "unknown"))
        if (self._skills_dir / f"{normalized_name}.py").exists():
            return "active"
        if (self._pending_dir / f"{normalized_name}.py").exists():
            return "pending"
        return None

    def get_version(self, skill_name: str) -> int:
        """Return current version number of a skill."""
        normalized_name = _normalize_skill_name(skill_name)
        return int(self._registry.get(normalized_name, {}).get("version", 1))

    def find_similar(self, skill_name: str) -> str | None:
        """
        Find an existing skill with a similar name.
        Returns the matching skill name or None.
        Uses normalized string overlap.
        """
        normalized_new = re.sub(r"[^a-z0-9]", "", _normalize_skill_name(skill_name))
        if not normalized_new:
            return None
        for existing_name in self._registry:
            normalized_ex = re.sub(r"[^a-z0-9]", "", existing_name.lower())
            if not normalized_ex:
                continue
            if normalized_new in normalized_ex or normalized_ex in normalized_new:
                return existing_name
            overlap = len(set(normalized_new) & set(normalized_ex)) / max(len(set(normalized_new)), 1)
            if overlap > 0.80:
                return existing_name
        return None

    def mark_active(self, skill_name: str) -> None:
        """Update skill location to active after approval."""
        normalized_name = _normalize_skill_name(skill_name)
        if normalized_name in self._registry:
            self._registry[normalized_name]["location"] = "active"
            self._save()

    def remove(self, skill_name: str) -> None:
        """Remove a skill from the registry."""
        normalized_name = _normalize_skill_name(skill_name)
        if normalized_name in self._registry:
            del self._registry[normalized_name]
            self._save()

    def all_skills(self) -> list[dict[str, Any]]:
        """Return all registered skills."""
        return list(self._registry.values())

def _bootstrap_skill_registry(
    registry: SkillRegistry,
    skills_dir: Path,
    pending_dir: Path,
) -> None:
    """Scan existing skill files and register them."""
    skills_dir.mkdir(parents=True, exist_ok=True)
    pending_dir.mkdir(parents=True, exist_ok=True)

    for skill_file in sorted(skills_dir.glob("*.py")):
        if skill_file.name.startswith("_"):
            continue
        skill_name = skill_file.stem
        if registry.get_location(skill_name) != "active":
            registry.register(
                skill_name=skill_name,
                description=f"Existing skill: {skill_name}",
                location="active",
                version=registry.get_version(skill_name),
            )

    for skill_file in sorted(pending_dir.glob("*.py")):
        skill_name = skill_file.stem
        if registry.get_location(skill_name) != "pending":
            registry.register(
                skill_name=skill_name,
                description=f"Pending skill: {skill_name}",
                location="pending",
                version=registry.get_version(skill_name),
            )

_logger = logging.getLogger("erisia.skills")
_skill_registry = SkillRegistry(
    skills_dir=SKILLS_PATH,
    pending_dir=PENDING_SKILLS_PATH,
    logger=_logger,
)
_bootstrap_skill_registry(_skill_registry, SKILLS_PATH, PENDING_SKILLS_PATH)

def _extract_skill_contract_from_ast(file_path):
    """Read TOOL_SCHEMA + execute_skill signature without importing module code."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=file_path)
    except Exception as e:
        return None, f"Failed to parse file: {e}"

    schema_node = None
    execute_skill_node = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "TOOL_SCHEMA":
                    schema_node = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "TOOL_SCHEMA":
                schema_node = node.value
        elif isinstance(node, ast.FunctionDef) and node.name == "execute_skill":
            execute_skill_node = node

    if schema_node is None:
        return None, "Missing top-level TOOL_SCHEMA assignment."
    if execute_skill_node is None:
        return None, "Missing execute_skill(**kwargs) function."
    if execute_skill_node.args.kwarg is None:
        return None, "execute_skill must accept **kwargs."

    try:
        schema = ast.literal_eval(schema_node)
    except Exception as e:
        return None, f"TOOL_SCHEMA must be a literal dictionary. Error: {e}"
    return schema, None

def _validate_dynamic_tool_schema(schema):
    """Strictly validate dynamic TOOL_SCHEMA to OpenAI function-calling shape."""
    if not isinstance(schema, dict):
        return None, "TOOL_SCHEMA must be a dictionary."
    if schema.get("type") != "function":
        return None, "TOOL_SCHEMA.type must equal 'function'."

    function_block = schema.get("function")
    if not isinstance(function_block, dict):
        return None, "TOOL_SCHEMA.function must be a dictionary."

    tool_name = str(function_block.get("name") or "").strip()
    description = str(function_block.get("description") or "").strip()
    parameters = function_block.get("parameters")

    if not tool_name:
        return None, "TOOL_SCHEMA.function.name is required."
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tool_name):
        return None, "TOOL_SCHEMA.function.name must be a valid snake_case identifier."
    if not description:
        return None, "TOOL_SCHEMA.function.description is required."
    if not isinstance(parameters, dict):
        return None, "TOOL_SCHEMA.function.parameters must be a dictionary."
    if parameters.get("type") != "object":
        return None, "TOOL_SCHEMA.function.parameters.type must be 'object'."

    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return None, "TOOL_SCHEMA.function.parameters.properties must be a dictionary."

    normalized_properties = {}
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_name, str) or not prop_name.strip():
            return None, "All parameter property names must be non-empty strings."
        if not isinstance(prop_schema, dict):
            return None, f"Property '{prop_name}' schema must be a dictionary."
        if "type" not in prop_schema or not isinstance(prop_schema.get("type"), str):
            return None, f"Property '{prop_name}' must define a string 'type'."
        normalized_properties[prop_name] = copy.deepcopy(prop_schema) if 'copy' in globals() else prop_schema

    required = parameters.get("required", [])
    if required is None:
        required = []
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        return None, "TOOL_SCHEMA.function.parameters.required must be a list of strings."

    unknown_required = [item for item in required if item not in normalized_properties]
    if unknown_required:
        return None, f"required contains unknown properties: {unknown_required}"

    normalized_parameters = {
        "type": "object",
        "properties": normalized_properties,
    }
    if required:
        normalized_parameters["required"] = required

    if "additionalProperties" in parameters:
        additional_properties = parameters.get("additionalProperties")
        if not isinstance(additional_properties, bool):
            return None, "TOOL_SCHEMA.function.parameters.additionalProperties must be boolean."
        normalized_parameters["additionalProperties"] = additional_properties

    normalized_schema = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": normalized_parameters,
        },
    }
    return normalized_schema, None

def _skill_file_to_tool_metadata(file_path):
    """Extract validated tool metadata from a skill file."""
    schema, extraction_error = _extract_skill_contract_from_ast(file_path)
    if extraction_error:
        return None, extraction_error
    validated_schema, validation_error = _validate_dynamic_tool_schema(schema)
    if validation_error:
        return None, validation_error
    if not isinstance(validated_schema, dict):
        return None, "Invalid TOOL_SCHEMA (expected a dictionary after validation)."
    function_block = validated_schema.get("function", {})
    tool_name = str(function_block.get("name") or "").strip()
    description = str(function_block.get("description") or "").strip()
    parameters = function_block.get("parameters", {})
    properties = parameters.get("properties", {}) if isinstance(parameters, dict) else {}
    param_names = sorted([str(k) for k in properties.keys()])
    return {
        "schema": validated_schema,
        "tool_name": tool_name,
        "description": description,
        "param_names": param_names,
    }, None

def _upsert_tool_registry(tool_name, description, file_path, param_names=None):
    """Persist tool semantics for retrieval in erisia_tools collection."""
    safe_tool_name = str(tool_name or "").strip()
    if not safe_tool_name:
        return
    safe_description = str(description or "").strip() or "No description provided."
    params = param_names if isinstance(param_names, list) else []
    params_text = ", ".join([str(p).strip() for p in params if str(p).strip()]) or "none"
    document = f"Tool {safe_tool_name}. {safe_description}. Parameters: {params_text}."
    metadata = {
        "tool_name": safe_tool_name,
        "file_name": os.path.basename(file_path),
        "file_path": os.path.abspath(file_path),
        "updated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    try:
        with memory_lock:
            tools_collection.upsert(
                ids=[safe_tool_name],
                documents=[document],
                metadatas=[metadata],
            )
    except Exception as e:
        print(f"[TOOLS INDEX WARNING]: Failed to upsert '{safe_tool_name}'. Error: {e}")

def _index_skill_file(file_path, fallback_tool_name=None, fallback_description=None):
    """Index a skill file into erisia_tools with validated metadata when possible."""
    metadata, error = _skill_file_to_tool_metadata(file_path)
    if metadata:
        _upsert_tool_registry(
            metadata["tool_name"],
            metadata["description"],
            file_path,
            metadata.get("param_names", []),
        )
        return metadata["tool_name"], None

    if fallback_tool_name:
        _upsert_tool_registry(
            fallback_tool_name,
            fallback_description or f"Skill from {os.path.basename(file_path)}.",
            file_path,
            [],
        )
        return fallback_tool_name, error
    return None, error

def _bootstrap_tool_registry_from_skills_dir(force=False):
    """One-time local index bootstrap for semantic tool retrieval."""
    global TOOL_INDEX_BOOTSTRAPPED
    skills_dir = SKILLS_DIR
    if TOOL_INDEX_BOOTSTRAPPED and not force:
        return
    if not os.path.exists(skills_dir):
        TOOL_INDEX_BOOTSTRAPPED = True
        return

    for filename in sorted(os.listdir(skills_dir)):
        if not filename.endswith(".py"):
            continue
        file_path = os.path.join(skills_dir, filename)
        _index_skill_file(file_path)
    TOOL_INDEX_BOOTSTRAPPED = True

def _query_relevant_dynamic_tools(user_query, max_tools=3):
    """Return semantically relevant dynamic tool hints from erisia_tools."""
    query_text = str(user_query or "").strip()
    if not query_text:
        return []
    n_results = max(1, int(max_tools or 1))
    try:
        with memory_lock:
            result = tools_collection.query(query_texts=[query_text], n_results=n_results)
    except Exception as e:
        print(f"[TOOLS INDEX WARNING]: Semantic query failed. Error: {e}")
        return []

    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    if not ids or not isinstance(ids, list):
        return []

    id_row = ids[0] if ids and isinstance(ids[0], list) else []
    metadata_row = metadatas[0] if metadatas and isinstance(metadatas[0], list) else []
    entries = []
    for idx, tool_id in enumerate(id_row):
        tool_name = str(tool_id or "").strip()
        if not tool_name:
            continue
        meta = metadata_row[idx] if idx < len(metadata_row) and isinstance(metadata_row[idx], dict) else {}
        entries.append(
            {
                "tool_name": tool_name,
                "file_name": str(meta.get("file_name") or "").strip(),
                "file_path": str(meta.get("file_path") or "").strip(),
            }
        )
    return entries

def _module_roots_from_ast(file_path):
    """Collect imported top-level module roots from skill source."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=file_path)
    except Exception:
        return set()

    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    roots.add(alias.name.split(".")[0].strip())
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            if node.module:
                roots.add(node.module.split(".")[0].strip())
    return {r for r in roots if r}

def _detect_external_packages(file_path):
    """Infer third-party package candidates from imports."""
    module_roots = _module_roots_from_ast(file_path)
    stdlib_names = set(getattr(sys, "stdlib_module_names", set()))
    packages = []
    for root in sorted(module_roots):
        if not root or root in stdlib_names or root in KNOWN_LOCAL_MODULES:
            continue
        if os.path.exists(os.path.join(SKILLS_DIR, f"{root}.py")):
            continue
        package_name = THIRD_PARTY_PACKAGE_MAP.get(root, root)
        if package_name not in packages:
            packages.append(package_name)
    return packages

def _build_dynamic_skill_runner(file_path, tool_name, external_packages=None):
    """Build a callable that executes the skill on demand in a subprocess."""
    absolute_path = os.path.abspath(file_path)
    required_packages = [str(p).strip() for p in (external_packages or []) if str(p).strip()]
    required_packages = sorted(set(required_packages))
    requires_isolation = bool(required_packages)
    try:
        with open(absolute_path, "r", encoding="utf-8") as skill_file:
            source_lower = skill_file.read().lower()
        if "pip install" in source_lower or ('"-m", "pip"' in source_lower) or ("'-m', 'pip'" in source_lower):
            requires_isolation = True
    except Exception as e:
        _logger.error(f"Caught unhandled exception in {__name__}: {e}", exc_info=True)

    runner_code = (
        "import contextlib\n"
        "import io\n"
        "import json\n"
        "import runpy\n"
        "import sys\n"
        "import traceback\n"
        "skill_path = sys.argv[1]\n"
        "raw_args = sys.argv[2] if len(sys.argv) > 2 else '{}'\n"
        "try:\n"
        "    kwargs = json.loads(raw_args)\n"
        "    if not isinstance(kwargs, dict):\n"
        "        kwargs = {}\n"
        "except Exception:\n"
        "    kwargs = {}\n"
        "captured = io.StringIO()\n"
        "try:\n"
        "    with contextlib.redirect_stdout(captured):\n"
        "        namespace = runpy.run_path(skill_path, run_name='__erisia_skill_runtime__')\n"
        "        execute_fn = namespace.get('execute_skill')\n"
        "        if not callable(execute_fn):\n"
        "            raise RuntimeError('execute_skill(**kwargs) not found.')\n"
        "        result = execute_fn(**kwargs)\n"
        "    print(json.dumps({'ok': True, 'result': result, 'stdout': captured.getvalue()}, ensure_ascii=False, default=str))\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'ok': False, 'error': str(exc), 'traceback': traceback.format_exc(), 'stdout': captured.getvalue()}, ensure_ascii=False, default=str))\n"
        "    sys.exit(1)\n"
    )

    def _runner(**kwargs):
        try:
            encoded_args = json.dumps(kwargs or {}, ensure_ascii=False)
        except Exception:
            encoded_args = "{}"

        ephemeral_root = None
        run_python = sys.executable
        using_ephemeral_env = False
        setup_stage = "execution"
        try:
            if requires_isolation:
                using_ephemeral_env = True
                setup_stage = "venv_creation"
                os.makedirs(SANDBOX_DIR, exist_ok=True)
                ephemeral_root = tempfile.mkdtemp(prefix=f"{tool_name}_", dir=SANDBOX_DIR)
                venv_path = os.path.join(ephemeral_root, "venv")
                subprocess.run(
                    [sys.executable, "-m", "venv", venv_path],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                )
                run_python = os.path.join(venv_path, "Scripts", "python.exe") if os.name == "nt" else os.path.join(venv_path, "bin", "python")

                if required_packages:
                    setup_stage = "dependency_install"
                    subprocess.run(
                        [run_python, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", *required_packages],
                        capture_output=True,
                        text=True,
                        timeout=180,
                        check=True,
                    )

            setup_stage = "skill_execution"
            result = subprocess.run(
                [run_python, "-c", runner_code, absolute_path, encoded_args],
                capture_output=True,
                text=True,
                timeout=45,
            )
        except subprocess.TimeoutExpired:
            return f"[DYNAMIC SKILL ERROR]: Skill '{tool_name}' timed out after 45 seconds."
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            stdout = (e.stdout or "").strip()
            diagnostics = stderr or stdout or str(e)
            return f"[DYNAMIC SKILL ERROR]: Skill '{tool_name}' failed during {setup_stage}. {diagnostics}"
        except Exception as e:
            return f"[DYNAMIC SKILL ERROR]: Failed to execute skill '{tool_name}'. Error: {e}"
        finally:
            if using_ephemeral_env and ephemeral_root and os.path.exists(ephemeral_root):
                try:
                    shutil.rmtree(ephemeral_root, ignore_errors=True)
                except Exception as e:
                    _logger.error(f"Caught unhandled exception in {__name__}: {e}", exc_info=True)

        raw_stdout = (result.stdout or "").strip()
        payload = None
        if raw_stdout:
            candidate = raw_stdout.splitlines()[-1]
            try:
                payload = json.loads(candidate)
            except Exception:
                payload = None

        if not isinstance(payload, dict):
            stderr = (result.stderr or "").strip()
            if result.returncode != 0:
                return f"[DYNAMIC SKILL ERROR]: Skill '{tool_name}' failed. {stderr or 'No stderr output.'}"
            return raw_stdout or f"[DYNAMIC SKILL ERROR]: Skill '{tool_name}' returned invalid output."

        if payload.get("ok"):
            skill_result = payload.get("result")
            captured_stdout = str(payload.get("stdout") or "").strip()
            if isinstance(skill_result, str):
                rendered = skill_result.strip()
            elif skill_result is None:
                rendered = ""
            else:
                rendered = json.dumps(skill_result, ensure_ascii=False)

            if rendered and captured_stdout:
                return f"{rendered}\n{captured_stdout}"
            if rendered:
                return rendered
            if captured_stdout:
                return captured_stdout
            return f"[{tool_name} SUCCESS]: Completed with no output."

        error_text = str(payload.get("error") or "Unknown dynamic skill failure.").strip()
        traceback_text = str(payload.get("traceback") or "").strip()
        if traceback_text:
            return f"[DYNAMIC SKILL ERROR]: {error_text}\n{traceback_text}"
        return f"[DYNAMIC SKILL ERROR]: {error_text}"

    return _runner

def load_dynamic_skills(base_tools_array, user_query=None, max_tools=3):
    """Load base tools plus semantically selected dynamic skills."""
    global _skills_cache, _skills_cache_timestamp
    
    now = time.time()
    if _skills_cache and (now - _skills_cache_timestamp) < SKILLS_CACHE_TTL_SECONDS:
        return _skills_cache

    expanded_tools = list(base_tools_array or [])
    dynamic_skill_map = {}
    skills_dir = SKILLS_DIR
    if not os.path.exists(skills_dir):
        os.makedirs(skills_dir)
        return expanded_tools, dynamic_skill_map

    query_text = str(user_query or "").strip()
    top_k = max(1, int(max_tools or 1))
    selected_tool_names = None
    preferred_file_paths = []

    if query_text:
        _bootstrap_tool_registry_from_skills_dir()
        semantic_hits = _query_relevant_dynamic_tools(query_text, max_tools=top_k)
        if not semantic_hits:
            _bootstrap_tool_registry_from_skills_dir(force=True)
            semantic_hits = _query_relevant_dynamic_tools(query_text, max_tools=top_k)
        selected_tool_names = {str(hit.get("tool_name") or "").strip() for hit in semantic_hits if str(hit.get("tool_name") or "").strip()}
        if not selected_tool_names:
            return expanded_tools, dynamic_skill_map
        for hit in semantic_hits:
            file_path = str(hit.get("file_path") or "").strip()
            if file_path and file_path.endswith(".py"):
                preferred_file_paths.append(file_path)
            else:
                file_name = str(hit.get("file_name") or "").strip()
                if file_name:
                    preferred_file_paths.append(os.path.join(skills_dir, file_name))

    registered_tool_names = set()
    for tool in expanded_tools:
        if not isinstance(tool, dict):
            continue
        func = tool.get("function", {})
        if isinstance(func, dict):
            name = func.get("name")
            if name:
                registered_tool_names.add(str(name))

    if query_text:
        files_to_scan = []
        seen_files = set()
        for path in preferred_file_paths:
            normalized_path = os.path.abspath(path)
            if normalized_path in seen_files:
                continue
            seen_files.add(normalized_path)
            files_to_scan.append(normalized_path)
        if not files_to_scan:
            files_to_scan = [os.path.abspath(os.path.join(skills_dir, f)) for f in sorted(os.listdir(skills_dir)) if f.endswith(".py")]
    else:
        files_to_scan = [os.path.abspath(os.path.join(skills_dir, f)) for f in sorted(os.listdir(skills_dir)) if f.endswith(".py")]

    for file_path in files_to_scan:
        if not file_path.endswith(".py") or not os.path.exists(file_path):
            continue
        module_name = os.path.basename(file_path)[:-3]
        try:
            metadata, error = _skill_file_to_tool_metadata(file_path)
            if error:
                print(f"[Shield Active: Rejected {module_name}. {error}]")
                continue
            if not isinstance(metadata, dict):
                continue

            validated_schema = metadata.get("schema")
            tool_name = str(metadata.get("tool_name") or "").strip()
            description = str(metadata.get("description") or "").strip()
            param_names = metadata.get("param_names", [])
            if selected_tool_names is not None and tool_name not in selected_tool_names:
                continue
            if tool_name in registered_tool_names:
                continue

            _upsert_tool_registry(tool_name, description, file_path, param_names)
            external_packages = _detect_external_packages(file_path)
            expanded_tools.append(validated_schema)
            dynamic_skill_map[tool_name] = _build_dynamic_skill_runner(file_path, tool_name, external_packages=external_packages)
            registered_tool_names.add(tool_name)
            print(f"[System: Successfully threaded autonomous skill -> {module_name}]")
        except Exception as e:
            print(f"[System Error: Failed to thread {module_name}. Error: {e}]")

    _skills_cache = (expanded_tools, dynamic_skill_map)
    _skills_cache_timestamp = time.time()
    
    return expanded_tools, dynamic_skill_map

def _route_duplicate_skill_to_improver(skill_name, python_code):
    """Route duplicate or similar skill forges into the improver path."""
    normalized_name = _normalize_skill_name(skill_name)
    if _skill_registry.exists(normalized_name):
        return improve_existing_skill(
            skill_name=normalized_name,
            improved_code=python_code,
            reason="autonomous re-forge routed to improvement",
        )

    similar = _skill_registry.find_similar(normalized_name)
    if similar:
        return improve_existing_skill(
            skill_name=similar,
            improved_code=python_code,
            reason=f"merged with similar skill '{normalized_name}'",
        )
    return None

def _stage_skill_in_pending(skill_name, python_code):
    """Write a new skill into Pending and register it for approval."""
    normalized_name = _normalize_skill_name(skill_name)
    PENDING_SKILLS_PATH.mkdir(parents=True, exist_ok=True)
    safe_name = f"{normalized_name}.py"
    file_path = PENDING_SKILLS_PATH / safe_name
    try:
        file_path.write_text(python_code, encoding="utf-8")
    except OSError as exc:
        return f"[SYSTEM FORGE ERROR]: {exc}"

    _skill_registry.register(
        skill_name=normalized_name,
        description=str(python_code or "")[:120],
        location="pending",
    )
    return f"[SYSTEM FORGE]: Skill {safe_name} saved to Pending folder. Waiting for Master Sameer's approval."

def improve_existing_skill(
    skill_name: str,
    improved_code: str,
    reason: str = "autonomous improvement",
) -> str:
    """
    Improve an existing skill rather than forging a duplicate.
    """
    normalized_name = _normalize_skill_name(skill_name)
    existing_location = _skill_registry.get_location(normalized_name)
    current_version = _skill_registry.get_version(normalized_name)
    new_version = current_version + 1

    if "TOOL_SCHEMA" not in improved_code:
        return f"[SKILL IMPROVE ERROR]: Missing TOOL_SCHEMA in {normalized_name}"
    if "execute_skill" not in improved_code:
        return f"[SKILL IMPROVE ERROR]: Missing execute_skill in {normalized_name}"

    versioned_code = (
        f"# Erisia Skill - {normalized_name} v{new_version}\n"
        f"# Improved from v{current_version}: {reason}\n"
        f"# Location before improvement: {existing_location}\n\n"
        + improved_code
    )

    pending_path = PENDING_SKILLS_PATH / f"{normalized_name}.py"
    try:
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(versioned_code, encoding="utf-8")
    except OSError as exc:
        return f"[SKILL IMPROVE ERROR]: {exc}"

    _skill_registry.register(
        skill_name=normalized_name,
        description=f"Improved v{new_version}: {reason}",
        location="pending",
        version=new_version,
    )

    return (
        f"[SKILL IMPROVED]: {normalized_name} upgraded to "
        f"v{new_version} and sent to Pending for approval."
    )

FORGE_SYSTEM_PROMPT = """
You are Erisia's Skill Forge. Your job is to write 
production-quality Python tools that Erisia can use 
to serve Master Sameer.

Every skill you write must follow these MANDATORY rules.
Violation of any rule means the skill will be rejected.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 1 — PARAMETERIZED, NEVER HARDCODED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Every variable piece of data must be a kwargs parameter.
WRONG: query = "dhurandhar song"
RIGHT: query = str(kwargs.get("query", "")).strip()

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 2 — GRACEFUL DEFAULTS AND VALIDATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Every parameter must have a default. Required parameters
must return a clear error message if missing.
WRONG: if not query: return "Error"
RIGHT: if not query: 
           return "[ERROR]: 'query' parameter is required."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 3 — PROPER ERROR HANDLING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Every external call (web, file, OS, subprocess) must be
wrapped in try/except with specific exception types.
WRONG: except:
RIGHT: except (OSError, ValueError, subprocess.SubprocessError) 
           as exc:
               return f"[ERROR]: {exc}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 4 — NO TEMP FILE SUBPROCESS PATTERN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Never write code to a temp file and run it via subprocess.
Import and call directly instead.
WRONG: write script to temp file → subprocess.run(python file)
RIGHT: import webbrowser; webbrowser.open(url)
       import psutil; psutil.sensors_battery()

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 5 — SINGLE execute_skill FUNCTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Define execute_skill(**kwargs) exactly ONCE per file.
Never define it twice. It must accept **kwargs.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 6 — MEANINGFUL STRING RETURN VALUES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Always return a descriptive string. Never return True/False/None.
WRONG: return True
RIGHT: return f"[SUCCESS]: Opened '{query}' successfully."
WRONG: return None  
RIGHT: return "[SUCCESS]: Task completed with no output."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 7 — TOOL_SCHEMA MUST MATCH FUNCTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Every parameter in execute_skill(**kwargs) must appear
in TOOL_SCHEMA["function"]["parameters"]["properties"]
with a "type" and "description". Required parameters
must be listed in "required".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 8 — UNIVERSAL COMPATIBILITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Skills must work across different scenarios:
- File paths: use pathlib.Path, not hardcoded strings
- Browsers: try specific browser, fall back to default
- OS commands: check if command exists before running
- APIs: handle missing keys gracefully
- Encoding: always specify encoding="utf-8" for file ops

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REQUIRED FILE STRUCTURE (exact order):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Erisia Skill — {skill_name} v1
# {one line description}

{imports}

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "{skill_name}",
        "description": "{clear description of what it does}",
        "parameters": {
            "type": "object",
            "properties": {
                "{param_name}": {
                    "type": "{string|boolean|integer|number}",
                    "description": "{what this parameter does}",
                }
            },
            "required": ["{required_param_names}"],
        },
    },
}


def execute_skill(**kwargs: object) -> str:
    \"""One line docstring describing the skill.\"""
    # Extract and validate parameters first
    {param} = {type}(kwargs.get("{param}", {default}))
    
    # Validate required parameters
    if not {required_param}:
        return "[ERROR]: '{required_param}' is required."
    
    # Main logic with error handling
    try:
        {implementation}
        return f"[SUCCESS]: {description of what happened}"
    except {SpecificException} as exc:
        return f"[ERROR]: {exc}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXAMPLES OF GOOD SKILLS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GOOD — Weather checker (parameterized, fallback, error handling):

    import requests
    
    TOOL_SCHEMA = {
        "type": "function",
        "function": {
            "name": "check_weather",
            "description": "Get current weather for any city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "City name to check weather for",
                    },
                    "units": {
                        "type": "string", 
                        "description": "metric or imperial. Default: metric",
                    },
                },
                "required": ["city"],
            },
        },
    }
    
    def execute_skill(**kwargs: object) -> str:
        city = str(kwargs.get("city", "")).strip()
        units = str(kwargs.get("units", "metric")).strip()
        if not city:
            return "[ERROR]: 'city' parameter is required."
        try:
            url = (f"https://wttr.in/{city}?format=3"
                   f"&m={'m' if units == 'metric' else 'u'}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return f"[SUCCESS]: {response.text.strip()}"
        except requests.RequestException as exc:
            return f"[ERROR]: Weather fetch failed — {exc}"

GOOD — File reader (path validation, encoding):

    from pathlib import Path
    
    def execute_skill(**kwargs: object) -> str:
        file_path = str(kwargs.get("file_path", "")).strip()
        if not file_path:
            return "[ERROR]: 'file_path' is required."
        path = Path(file_path)
        if not path.exists():
            return f"[ERROR]: File not found: {file_path}"
        try:
            content = path.read_text(encoding="utf-8",
                                     errors="replace")
            return f"[SUCCESS]: {content[:2000]}"
        except OSError as exc:
            return f"[ERROR]: Cannot read file — {exc}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SKILL CATEGORIES AND THEIR PATTERNS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WEB/BROWSER skills:
- Use webbrowser.open() for simple URL opening
- Use subprocess.Popen([chrome_path, url]) for Chrome-specific
- Always URL-encode query parameters with urllib.parse.quote_plus
- Always fall back to webbrowser if specific browser not found

FILE/DOCUMENT skills:  
- Use pathlib.Path for all file operations
- Always specify encoding="utf-8", errors="replace"
- Validate file exists before reading
- Return truncated content if file is large (>2000 chars)

SYSTEM/OS skills:
- Use psutil for system metrics (CPU, RAM, battery, disk)
- Use subprocess.run() with timeout parameter
- Never use os.system() — always subprocess
- Check platform with sys.platform if OS-specific

SEARCH/WEB skills:
- Use requests with timeout=10 always
- Handle HTTP errors with response.raise_for_status()
- Parse responses appropriately (json(), text, etc.)
- Return structured, readable summaries not raw dumps

MEDIA skills:
- Use webbrowser for video/audio URLs
- Use subprocess.Popen for desktop apps (non-blocking)
- Always include the URL in the success message
"""

def _validate_skill_quality(
    skill_name: str,
    code: str,
    logger: logging.Logger,
) -> tuple[bool, list[str]]:
    """
    Validate generated skill code against quality rules.
    Returns (is_valid, list_of_violations).
    """
    violations: list[str] = []

    # Rule 4: No temp file subprocess pattern
    if ("tempfile" in code and 
            "subprocess" in code and
            "run_path" not in code):
        violations.append(
            "RULE 4: Uses temp file + subprocess pattern. "
            "Import and call directly instead."
        )

    # Rule 5: Single execute_skill
    count = code.count("def execute_skill")
    if count == 0:
        violations.append(
            "RULE 5: Missing execute_skill function."
        )
    elif count > 1:
        violations.append(
            f"RULE 5: execute_skill defined {count} times. "
            "Must be defined exactly once."
        )

    # Rule 7: TOOL_SCHEMA exists
    if "TOOL_SCHEMA" not in code:
        violations.append(
            "RULE 7: Missing TOOL_SCHEMA definition."
        )

    # Rule 6: No bare True/False returns
    import re
    if re.search(r'\\breturn\\s+True\\b', code):
        violations.append(
            "RULE 6: Returns bare True. "
            "Return a descriptive string instead."
        )
    if re.search(r'\\breturn\\s+False\\b', code):
        violations.append(
            "RULE 6: Returns bare False. "
            "Return a descriptive string instead."
        )
    if re.search(r'\\breturn\\s+None\\b', code):
        violations.append(
            "RULE 6: Returns bare None. "
            "Return a descriptive string instead."
        )

    # Rule 3: Bare except check
    if re.search(r'except\\s*:', code):
        violations.append(
            "RULE 3: Contains bare except clause. "
            "Always catch specific exception types."
        )

    # Rule 1: Hardcoded strings warning
    hardcoded_patterns = [
        r'query\\s*=\\s*["\'][^"\']+["\']',
        r'url\\s*=\\s*["\']https?://[^"\']+["\']',
        r'city\\s*=\\s*["\'][^"\']+["\']',
    ]
    for pattern in hardcoded_patterns:
        if re.search(pattern, code):
            violations.append(
                "RULE 1: Possible hardcoded value detected. "
                "All values must come from kwargs.get()."
            )
            break

    is_valid = len(violations) == 0
    if violations:
        logger.warning(
            "Skill '%s' has %d quality violations: %s",
            skill_name, len(violations),
            "; ".join(violations)
        )
    return is_valid, violations

def _forge_and_refine_skill(skill_name: str, python_code_or_req: str, logger: logging.Logger) -> str:
    """
    LLM-powered code generation or refinement using FORGE_SYSTEM_PROMPT.
    Includes quality validation and one automatic retry.
    """
    normalized_name = _normalize_skill_name(skill_name)
    prompt = python_code_or_req
    
    # If the input looks like code already, ask to refine it.
    # Otherwise, treat as a requirement.
    is_code = "def execute_skill" in python_code_or_req or "TOOL_SCHEMA" in python_code_or_req
    user_content = f"Generate code for skill '{normalized_name}':\n\n{prompt}"
    if is_code:
        user_content = f"Refine and ensure production quality for this skill '{normalized_name}':\n\n{prompt}"

    messages = [
        {"role": "system", "content": FORGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]

    final_code = ""
    for attempt in range(2):
        try:
            response = query_llm(messages=messages, model="llama-3.3-70b-versatile")
            raw_code = response.choices[0].message.content or ""
            
            # Extract code block if Markdown is used
            import re
            code_match = re.search(r"```python\\s*(.*?)\\s*```", raw_code, re.DOTALL)
            if not code_match:
                code_match = re.search(r"```\\s*(.*?)\\s*```", raw_code, re.DOTALL)
            
            final_code = code_match.group(1).strip() if code_match else raw_code.strip()
            
            # STEP 3: Validate Quality
            is_valid, violations = _validate_skill_quality(normalized_name, final_code, logger)
            
            if is_valid:
                return final_code
            
            # Attempt to regenerate with violations noted
            violation_note = (
                "The previous attempt had these issues: " +
                " | ".join(violations) +
                " Fix ALL violations in the new version. Output ONLY the code block."
            )
            messages.append({"role": "assistant", "content": raw_code})
            messages.append({"role": "user", "content": violation_note})
            
            if attempt == 1: # Last attempt
                 logger.warning(
                    "Saving skill '%s' despite violations — manual review recommended.", 
                    normalized_name
                )
                 return final_code
                 
        except Exception as exc:
            logger.error("Skill forge LLM call failed: %s", exc)
            if is_code: return python_code_or_req # Fallback to input if refinement fails
            raise exc

    return final_code # Should not be reached

def forge_new_skill(skill_name, python_code, update_consciousness_fn: Callable[[str], None] | None = None):
    """Stage a newly forged skill in Pending so it follows approval flow."""
    # LLM Refinement/Generation Step
    try:
        python_code = _forge_and_refine_skill(skill_name, python_code, _logger)
    except Exception as e:
        return f"[SYSTEM FORGE ERROR]: LLM generation failed: {str(e)}"

    rerouted = _route_duplicate_skill_to_improver(skill_name, python_code)
    if rerouted is not None:
        return rerouted

    normalized_name = _normalize_skill_name(skill_name)
    result = _stage_skill_in_pending(normalized_name, python_code)
    if result.startswith("[SYSTEM FORGE]:") and update_consciousness_fn:
        update_consciousness_fn(
            f"I autonomously forged a new skill: {normalized_name}.py. It is awaiting approval in my Pending folder."
        )
    return result

def forge_pending_skill(skill_name, python_code):
    """Subconscious tool: Saves a tested skill to the Pending folder for review."""
    # LLM Refinement/Generation Step
    try:
        python_code = _forge_and_refine_skill(skill_name, python_code, _logger)
    except Exception as e:
        return f"[SYSTEM FORGE ERROR]: LLM generation failed: {str(e)}"

    rerouted = _route_duplicate_skill_to_improver(skill_name, python_code)
    if rerouted is not None:
        return rerouted
    return _stage_skill_in_pending(skill_name, python_code)

def approve_skill(skill_name):
    """Conscious tool: Moves a skill from Pending to the main Skills folder."""
    raw_name = str(skill_name or "").strip()
    if not raw_name:
        return "[SYSTEM ERROR]: No skill name was provided for approval."

    target_skill = _normalize_skill_name(raw_name)
    skill_file_name = f"{target_skill}.py"
    src = os.path.join(PENDING_SKILLS_DIR, skill_file_name)
    dst = os.path.join(SKILLS_DIR, skill_file_name)

    if os.path.exists(src):
        try:
            os.makedirs(SKILLS_DIR, exist_ok=True)
            if os.path.exists(dst):
                os.remove(dst)
            shutil.move(src, dst)
        except OSError as exc:
            return f"[SYSTEM ERROR]: Failed to activate {skill_file_name}. {exc}"

        _skill_registry.mark_active(target_skill)
        _index_skill_file(dst, fallback_tool_name=target_skill, fallback_description=f"Approved skill stored in {skill_file_name}.")
        return f"[SYSTEM UPDATE]: Success. {skill_file_name} has been approved and will be threaded into Erisia's core on the next loop."
    return f"[SYSTEM ERROR]: Could not find {skill_file_name} in the Pending folder."

def reject_skill(skill_name):
    """Conscious tool: Deletes an unwanted skill from the Pending folder."""
    raw_name = str(skill_name or "").strip()
    if not raw_name:
        return "[SYSTEM ERROR]: No skill name was provided for rejection."

    target_skill = _normalize_skill_name(raw_name)
    skill_file_name = f"{target_skill}.py"
    src = os.path.join(PENDING_SKILLS_DIR, skill_file_name)

    if os.path.exists(src):
        os.remove(src)
        if (SKILLS_PATH / skill_file_name).exists():
            _skill_registry.mark_active(target_skill)
        else:
            _skill_registry.remove(target_skill)
        return f"[SYSTEM UPDATE]: {skill_file_name} has been rejected and permanently deleted from the hard drive."
    return f"[SYSTEM ERROR]: Could not find {skill_file_name} in the Pending folder."
