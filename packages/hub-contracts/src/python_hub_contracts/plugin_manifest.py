"""Source-level plugin manifest contracts.

V1 limits identifiers to 64 characters, operational text to 256 characters,
descriptions and JSON-default strings to 4096 characters, manifest lists and
default containers to 64 items, and default JSON nesting to eight containers.
"""

from typing import Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from .common import PluginId, RelativeProtocolPath, SemanticVersion, StrictContractModel
from .json_values import validate_json_value

# V1 parsing limits apply equally to YAML loading and direct model validation.
MAX_MANIFEST_IDENTIFIER_LENGTH = 64
MAX_MANIFEST_TEXT_LENGTH = 256
MAX_MANIFEST_DESCRIPTION_LENGTH = 4096
MAX_MANIFEST_LIST_ITEMS = 64
MAX_MANIFEST_DEFAULT_DEPTH = 8
MAX_MANIFEST_DEFAULT_CONTAINER_ITEMS = 64
MAX_MANIFEST_DEFAULT_STRING_LENGTH = 4096

ManifestName = Annotated[
    str,
    StringConstraints(
        max_length=MAX_MANIFEST_IDENTIFIER_LENGTH,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
PythonVersion = Annotated[
    str,
    StringConstraints(
        max_length=MAX_MANIFEST_IDENTIFIER_LENGTH,
        pattern=r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$",
    ),
]
ManifestText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_MANIFEST_TEXT_LENGTH),
]
ManifestDescription = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_MANIFEST_DESCRIPTION_LENGTH),
]
ManifestTextList = Annotated[
    list[ManifestText],
    Field(max_length=MAX_MANIFEST_LIST_ITEMS),
]


class PluginInfo(StrictContractModel):
    """Human and machine identity for a plugin."""

    id: PluginId
    name: ManifestText
    version: SemanticVersion
    description: ManifestDescription | None = None
    author: ManifestText | None = None
    category: ManifestText | None = None
    tags: ManifestTextList = Field(default_factory=list)


class SdkSpec(StrictContractModel):
    """SDK protocol version required by plugin source."""

    version: Literal["1.0"]


class PythonSpec(StrictContractModel):
    """Python interpreter requirement for a plugin."""

    version: PythonVersion


class EnvironmentDeclaration(StrictContractModel):
    """Source environment declaration used by the build process."""

    type: Literal["conda"]
    file: RelativeProtocolPath


class RuntimeSpec(StrictContractModel):
    """V1 process runtime configuration."""

    type: Literal["process"]
    python: PythonSpec
    environment: EnvironmentDeclaration


class EntryPointSpec(StrictContractModel):
    """Importable function that implements the plugin."""

    module: ManifestText
    function: ManifestText


class ParameterSpec(StrictContractModel):
    """One scalar plugin parameter."""

    name: ManifestName
    label: ManifestText
    type: Literal["string", "integer", "number", "boolean", "enum", "datetime"]
    required: bool
    default: Any = None
    options: ManifestTextList | None = None
    min: int | float | None = None
    max: int | float | None = None

    @field_validator("default", mode="before")
    @classmethod
    def validate_json_default(cls, value: object) -> object:
        """Bound arbitrary defaults while preserving strict JSON values unchanged."""
        return validate_json_value(
            value,
            max_depth=MAX_MANIFEST_DEFAULT_DEPTH,
            max_container_items=MAX_MANIFEST_DEFAULT_CONTAINER_ITEMS,
            max_string_length=MAX_MANIFEST_DEFAULT_STRING_LENGTH,
        )

    @model_validator(mode="after")
    def validate_parameter_constraints(self) -> Self:
        """Validate constraints whose meaning depends on the parameter type."""
        if self.required and self.default is not None:
            raise ValueError("required parameters cannot define a default")

        if self.type == "enum":
            if not self.options:
                raise ValueError("enum parameters require non-empty options")
            if len(self.options) != len(set(self.options)):
                raise ValueError("enum parameter options must be unique")
            if self.default is not None and self.default not in self.options:
                raise ValueError("enum parameter default must be one of its options")
        elif self.options is not None:
            raise ValueError("only enum parameters may define options")

        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("parameter minimum cannot exceed maximum")
        return self


class InputSpec(StrictContractModel):
    """One file or file-set input."""

    name: ManifestName
    label: ManifestText
    type: Literal["file", "files"]
    required: bool
    extensions: ManifestTextList = Field(default_factory=list)
    min_count: int | None = Field(default=None, gt=0)
    max_count: int | None = Field(default=None, gt=0)
    max_size: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_file_counts(self) -> Self:
        """Keep a declared count interval internally consistent."""
        if (
            self.min_count is not None
            and self.max_count is not None
            and self.min_count > self.max_count
        ):
            raise ValueError("input minimum count cannot exceed maximum count")
        return self


class OutputSpec(StrictContractModel):
    """One structured or file-based output."""

    name: ManifestName
    label: ManifestText
    type: Literal["object", "file", "files"]
    required: bool


class ExecutionSpec(StrictContractModel):
    """Hub-enforced execution limits."""

    timeout: int = Field(gt=0)
    concurrency: int = Field(gt=0)


class EnvironmentVariablesSpec(StrictContractModel):
    """Names of variables the platform must inject."""

    required: ManifestTextList = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_names(self) -> Self:
        """Reject declarations that repeat an environment variable name."""
        if len(self.required) != len(set(self.required)):
            raise ValueError("required environment variable names must be unique")
        return self


class HealthcheckSpec(StrictContractModel):
    """V1 plugin healthcheck declaration."""

    enabled: bool
    type: Literal["import"]


class PluginManifest(StrictContractModel):
    """Validated source-level plugin contract."""

    spec_version: Literal["1.0"]
    plugin: PluginInfo
    sdk: SdkSpec
    runtime: RuntimeSpec
    entrypoint: EntryPointSpec
    parameters: list[ParameterSpec] = Field(
        default_factory=list, max_length=MAX_MANIFEST_LIST_ITEMS
    )
    inputs: list[InputSpec] = Field(default_factory=list, max_length=MAX_MANIFEST_LIST_ITEMS)
    outputs: list[OutputSpec] = Field(default_factory=list, max_length=MAX_MANIFEST_LIST_ITEMS)
    execution: ExecutionSpec
    environment_variables: EnvironmentVariablesSpec
    healthcheck: HealthcheckSpec

    @model_validator(mode="after")
    def validate_named_sections(self) -> Self:
        """Reject ambiguous name-based lookup tables."""
        for section_name, items in (
            ("parameter", self.parameters),
            ("input", self.inputs),
            ("output", self.outputs),
        ):
            names = [item.name for item in items]
            if len(names) != len(set(names)):
                raise ValueError(f"{section_name} names must be unique")
        return self

    def parameter_by_name(self, name: str) -> ParameterSpec:
        """Return a parameter by its protocol name."""
        for parameter in self.parameters:
            if parameter.name == name:
                return parameter
        raise KeyError(name)

    def input_by_name(self, name: str) -> InputSpec:
        """Return an input by its protocol name."""
        for input_spec in self.inputs:
            if input_spec.name == name:
                return input_spec
        raise KeyError(name)

    def output_by_name(self, name: str) -> OutputSpec:
        """Return an output by its protocol name."""
        for output in self.outputs:
            if output.name == name:
                return output
        raise KeyError(name)
