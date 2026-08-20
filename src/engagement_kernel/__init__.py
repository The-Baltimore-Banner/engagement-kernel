"""Portable engagement scoring and clustering kernel.

Four packages, in dependency order:

``contract``
    The canonical columnar input contract: schemas, validation, and the loader
    that turns conforming files into typed frames. Vendor-specific loaders are
    adapters that produce this contract; nothing downstream knows where the data
    came from.
``intermediate``
    Derived per-subscriber and per-period tables built from the contract. Shared
    by the scoring and content packages so neither recomputes the other's inputs.
``engagement``
    Engagement scoring and subscriber clustering over the intermediate tables.
``content``
    Content-side measures and the joins that relate content to engagement.

The reference engine is DuckDB over columnar files. Cloud SDKs are optional
extras, never core dependencies.
"""

__all__ = ["__version__"]

__version__ = "0.0.0"
