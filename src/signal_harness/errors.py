class HarnessError(Exception):
    """A user-actionable engine error."""


class ConfigurationError(HarnessError):
    """Invalid repository or workspace configuration."""


class ResolutionError(HarnessError):
    """A repository or file could not be resolved safely."""


# Compatibility for integrations written against the pre-extraction prototype.
SddError = HarnessError
