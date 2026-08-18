"""Versioned prompt management.

Prompts are configuration, not code. They live in ``config/prompts/*.yaml``, one
file per ``<name>.<version>``, and are loaded at runtime. That buys three things
that matter in production:

* **Auditability.** Every :class:`~askmydocs.models.Answer` records the prompt name,
  version, and content hash that produced it.
* **Reproducibility.** An evaluation report from three months ago names a prompt
  version that is still in the repository and still runnable.
* **Reviewable change.** A prompt edit is a diff in a pull request that the
  evaluation gate runs against, rather than an untracked string in a source file.

Superseded versions are kept deliberately — see ``answer.v1.yaml``, which exists
so the regression it caused stays reproducible.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

_FILENAME_RE = re.compile(r"^(?P<name>[a-z0-9_\-]+)\.(?P<version>v\d+)$", re.IGNORECASE)
_VERSION_NUM_RE = re.compile(r"(\d+)")
# Matches {placeholder} but not {{escaped}} braces.
_VARIABLE_RE = re.compile(r"(?<!\{)\{([a-z_][a-z0-9_]*)\}(?!\})", re.IGNORECASE)


class MissingPromptVariable(KeyError):
    """Raised when rendering a prompt without a value the template requires."""


class Prompt:
    """A single versioned prompt template."""

    def __init__(
        self,
        name: str,
        version: str,
        system: str,
        user: str,
        metadata: dict[str, Any] | None = None,
        path: Path | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.system = system
        self.user = user
        self.metadata = metadata or {}
        self.path = path

    # -- identity ----------------------------------------------------------

    @property
    def content_hash(self) -> str:
        """Hash of the rendered template text — detects an edited prompt file."""
        payload = f"{self.name}\x00{self.version}\x00{self.system}\x00{self.user}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    @property
    def version_number(self) -> int:
        match = _VERSION_NUM_RE.search(self.version)
        return int(match.group(1)) if match else 0

    @property
    def status(self) -> str:
        return str(self.metadata.get("status", "active"))

    @property
    def declared_variables(self) -> list[str]:
        return list(self.metadata.get("variables") or [])

    @property
    def template_variables(self) -> set[str]:
        """Placeholders actually present in the template text."""
        return set(_VARIABLE_RE.findall(self.system)) | set(_VARIABLE_RE.findall(self.user))

    # -- rendering ---------------------------------------------------------

    def render(self, **variables: Any) -> tuple[str, str]:
        """Render ``(system, user)``.

        Uses ``str.format_map`` with a guard that turns a missing key into a clear,
        named error instead of a bare ``KeyError`` from deep inside formatting.
        """
        required = self.template_variables
        missing = sorted(required - set(variables))
        if missing:
            raise MissingPromptVariable(
                f"Prompt {self.name}.{self.version} requires {missing}, which were not provided"
            )
        safe = {key: variables[key] for key in required}
        try:
            return self.system.format_map(safe).strip(), self.user.format_map(safe).strip()
        except (KeyError, IndexError) as exc:  # pragma: no cover - guarded above
            raise MissingPromptVariable(
                f"Failed to render {self.name}.{self.version}: {exc}"
            ) from exc

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Prompt {self.name}.{self.version} hash={self.content_hash}>"


class PromptLibrary:
    """Loads and resolves the versioned prompts in a directory."""

    def __init__(self, directory: str | Path = "config/prompts") -> None:
        self.directory = Path(directory)
        self._prompts: dict[tuple[str, str], Prompt] = {}
        self.load()

    def load(self) -> PromptLibrary:
        self._prompts = {}
        if not self.directory.exists():
            raise FileNotFoundError(
                f"Prompt directory not found: {self.directory}. "
                "Prompts are versioned configuration and must be present."
            )

        for path in sorted(self.directory.glob("*.y*ml")):
            match = _FILENAME_RE.match(path.stem)
            if not match:
                raise ValueError(
                    f"Prompt file {path.name} does not follow the <name>.<vN>.yaml convention"
                )
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ValueError(f"{path} must contain a YAML mapping")

            name = str(data.get("name") or match.group("name"))
            version = str(data.get("version") or match.group("version"))
            if name != match.group("name") or version != match.group("version"):
                raise ValueError(
                    f"{path.name}: filename says {match.group('name')}.{match.group('version')} "
                    f"but the file declares {name}.{version}. Keep them in sync."
                )
            if "user" not in data:
                raise ValueError(f"{path.name}: missing required 'user' template")

            prompt = Prompt(
                name=name,
                version=version,
                system=str(data.get("system", "")),
                user=str(data["user"]),
                metadata={k: v for k, v in data.items() if k not in {"system", "user"}},
                path=path,
            )

            declared = set(prompt.declared_variables)
            if declared:
                undeclared = prompt.template_variables - declared
                if undeclared:
                    raise ValueError(
                        f"{path.name}: template uses undeclared variables {sorted(undeclared)}. "
                        "Add them to the 'variables' list."
                    )

            self._prompts[(name, version)] = prompt

        if not self._prompts:
            raise FileNotFoundError(f"No prompt files found in {self.directory}")
        return self

    # -- lookup ------------------------------------------------------------

    def versions(self, name: str) -> list[str]:
        found = [v for (n, v) in self._prompts if n == name]
        return sorted(found, key=lambda v: int(_VERSION_NUM_RE.search(v).group(1)))

    def names(self) -> list[str]:
        return sorted({n for (n, _) in self._prompts})

    def get(self, name: str, version: str = "latest") -> Prompt:
        """Resolve a prompt. ``version="latest"`` picks the highest version number."""
        if version in {"latest", "", None}:
            available = self.versions(name)
            if not available:
                raise KeyError(f"No prompt named {name!r}. Known: {self.names()}")
            # Prefer the highest-numbered version that is not marked superseded.
            active = [
                v
                for v in available
                if self._prompts[(name, v)].status not in {"superseded", "draft"}
            ]
            version = (active or available)[-1]

        try:
            return self._prompts[(name, version)]
        except KeyError as exc:
            raise KeyError(
                f"Unknown prompt {name}.{version}. Available versions: {self.versions(name)}"
            ) from exc

    def describe(self) -> list[dict[str, Any]]:
        """Inventory for the ``/prompts`` API endpoint and the CLI."""
        return [
            {
                "name": prompt.name,
                "version": prompt.version,
                "status": prompt.status,
                "content_hash": prompt.content_hash,
                "variables": sorted(prompt.template_variables),
                "description": str(prompt.metadata.get("description", "")).strip(),
                "path": str(prompt.path),
            }
            for prompt in sorted(self._prompts.values(), key=lambda p: (p.name, p.version_number))
        ]

    def __len__(self) -> int:
        return len(self._prompts)
