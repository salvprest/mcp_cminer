import base64
import os
import string
import zlib

_PUBLIC_PLANTUML = "https://www.plantuml.com/plantuml"
# In Docker Compose the env var is set to http://plantuml:8080.
# In local dev (no env var) we fall back to the public server.
PLANTUML_SERVER: str = os.environ.get("PLANTUML_SERVER_URL", _PUBLIC_PLANTUML)

_STANDARD_ALPHABET = string.ascii_uppercase + string.ascii_lowercase + string.digits + "+/"
_PLANTUML_ALPHABET  = string.digits + string.ascii_uppercase + string.ascii_lowercase + "-_"
_B64_TABLE = str.maketrans(_STANDARD_ALPHABET, _PLANTUML_ALPHABET)


def plantuml_encode(text: str) -> str:
    """Encodes text to PlantUML format, see PlantUML docs."""
    utf8_data  = text.encode("utf-8")
    compressor = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=-15)
    compressed = compressor.compress(utf8_data) + compressor.flush()
    return base64.b64encode(compressed).decode("utf-8").translate(_B64_TABLE)
