import secrets

# Base62 alphabet: 0-9, a-z, A-Z = 62 characters
BASE62_CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def encode_base62(num: int) -> str:
    """
    Convert a positive integer to a Base62 string.

    Why Base62 (not Base64)?
    - Base64 includes '+' and '/' which are URL-unsafe
    - Base62 uses only alphanumeric chars = clean URLs
    - 62^7 = 3.5 trillion combinations = sufficient for most systems

    DSA Connection: This is number base conversion.
    Same pattern as converting decimal to binary, but base=62.
    """
    if num == 0:
        return BASE62_CHARS[0]

    result = []
    while num > 0:
        result.append(BASE62_CHARS[num % 62])
        num //= 62

    return "".join(reversed(result))


def decode_base62(s: str) -> int:
    """Convert a Base62 string back to integer."""
    num = 0
    for char in s:
        num = num * 62 + BASE62_CHARS.index(char)
    return num


async def create_short_url(db, original_url: str, redirect_type: int = 302):
    """
    Strategy: Auto-increment ID -> Base62 encode

    Why NOT hash-based?
    - MD5/SHA256 -> Base62 can cause collisions requiring DB lookups
    - Auto-increment -> Base62 is collision-free by construction
    - Trade-off: sequential IDs are predictable (if that matters, add random offset)

    SDI Ch 8 discusses both approaches. We use counter-based for simplicity.
    """
    from app.models import URL

    # NOTE: the placeholder must be unique (not "") so that two concurrent
    # inserts never collide on the unique short_code index before the
    # Base62 code has been assigned from the auto-increment ID.
    url = URL(
        original_url=original_url,
        redirect_type=redirect_type,
        short_code=secrets.token_hex(4),
    )
    db.add(url)
    await db.flush()  # Get the auto-generated ID

    url.short_code = encode_base62(url.id)
    await db.commit()
    await db.refresh(url)
    return url
