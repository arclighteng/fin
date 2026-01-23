"""
Secure credential storage using system keyring.

Stores SimpleFIN credentials in:
- Windows: Credential Manager
- macOS: Keychain
- Linux: Secret Service (GNOME Keyring, KWallet, etc.)

Falls back to .env file if keyring is unavailable.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SERVICE_NAME = "fin"
SIMPLEFIN_KEY = "simplefin_access_url"

# Lazy import keyring to allow graceful degradation
_keyring = None
_keyring_available: Optional[bool] = None


def _get_keyring():
    """Lazy load keyring module."""
    global _keyring, _keyring_available

    if _keyring_available is False:
        return None

    if _keyring is None:
        try:
            import keyring
            # Test if keyring backend is actually usable
            keyring.get_keyring()
            _keyring = keyring
            _keyring_available = True
        except Exception as e:
            logger.debug(f"Keyring not available: {e}")
            _keyring_available = False
            return None

    return _keyring


def is_keyring_available() -> bool:
    """Check if system keyring is available."""
    return _get_keyring() is not None


def get_credential(key: str) -> Optional[str]:
    """
    Get a credential from the system keyring.

    Args:
        key: The credential key (e.g., 'simplefin_access_url')

    Returns:
        The credential value, or None if not found or keyring unavailable.
    """
    kr = _get_keyring()
    if kr is None:
        return None

    try:
        value = kr.get_password(SERVICE_NAME, key)
        if value:
            logger.debug(f"Retrieved credential '{key}' from keyring")
        return value
    except Exception as e:
        logger.warning(f"Failed to get credential from keyring: {e}")
        return None


def set_credential(key: str, value: str) -> bool:
    """
    Store a credential in the system keyring.

    Args:
        key: The credential key
        value: The credential value

    Returns:
        True if stored successfully, False otherwise.
    """
    kr = _get_keyring()
    if kr is None:
        logger.warning("Keyring not available, cannot store credential")
        return False

    try:
        kr.set_password(SERVICE_NAME, key, value)
        logger.info(f"Stored credential '{key}' in keyring")
        return True
    except Exception as e:
        logger.error(f"Failed to store credential in keyring: {e}")
        return False


def delete_credential(key: str) -> bool:
    """
    Delete a credential from the system keyring.

    Args:
        key: The credential key

    Returns:
        True if deleted (or didn't exist), False on error.
    """
    kr = _get_keyring()
    if kr is None:
        logger.warning("Keyring not available, cannot delete credential")
        return False

    try:
        kr.delete_password(SERVICE_NAME, key)
        logger.info(f"Deleted credential '{key}' from keyring")
        return True
    except Exception as e:
        # keyring raises an exception if the credential doesn't exist
        # on some backends - treat as success
        if "not found" in str(e).lower() or "no password" in str(e).lower():
            logger.debug(f"Credential '{key}' not found in keyring (already deleted)")
            return True
        logger.error(f"Failed to delete credential from keyring: {e}")
        return False


def get_simplefin_url() -> Optional[str]:
    """Get SimpleFIN access URL from keyring."""
    return get_credential(SIMPLEFIN_KEY)


def set_simplefin_url(url: str) -> bool:
    """Store SimpleFIN access URL in keyring."""
    return set_credential(SIMPLEFIN_KEY, url)


def clear_simplefin_url() -> bool:
    """Remove SimpleFIN access URL from keyring."""
    return delete_credential(SIMPLEFIN_KEY)


def get_credential_source() -> str:
    """
    Determine where credentials are being loaded from.

    Returns:
        'keyring', 'env', or 'none'
    """
    import os

    # Check keyring first
    if get_simplefin_url():
        return "keyring"

    # Check environment/.env
    if os.getenv("SIMPLEFIN_ACCESS_URL", "").strip():
        return "env"

    return "none"
