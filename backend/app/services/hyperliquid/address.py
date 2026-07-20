import re

_HL_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def normalize_hl_address(address: str) -> str:
    normalized = address.strip().lower()
    if not _HL_ADDRESS_RE.fullmatch(normalized):
        raise ValueError("HL address must be a 42-character 0x-prefixed hex address.")
    return normalized
