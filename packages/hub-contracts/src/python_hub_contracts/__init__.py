"""Public contracts for Python Service Hub."""

from .build_manifest import PackagedRuntime, PluginBuildManifest, TargetPlatform
from .common import (
    PluginId,
    RelativeProtocolPath,
    SemanticVersion,
    StrictContractModel,
    normalize_arch,
    normalize_os,
)
from .plugin_manifest import (
    EntryPointSpec,
    EnvironmentDeclaration,
    EnvironmentVariablesSpec,
    ExecutionSpec,
    HealthcheckSpec,
    InputSpec,
    OutputSpec,
    ParameterSpec,
    PluginInfo,
    PluginManifest,
    PythonSpec,
    RuntimeSpec,
    SdkSpec,
)
from .yaml_io import load_plugin_manifest

__version__ = "0.1.0"

__all__ = [
    "EntryPointSpec",
    "EnvironmentDeclaration",
    "EnvironmentVariablesSpec",
    "ExecutionSpec",
    "HealthcheckSpec",
    "InputSpec",
    "OutputSpec",
    "PackagedRuntime",
    "ParameterSpec",
    "PluginBuildManifest",
    "PluginId",
    "PluginInfo",
    "PluginManifest",
    "PythonSpec",
    "RelativeProtocolPath",
    "RuntimeSpec",
    "SdkSpec",
    "SemanticVersion",
    "StrictContractModel",
    "TargetPlatform",
    "load_plugin_manifest",
    "normalize_arch",
    "normalize_os",
]
