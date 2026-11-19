"""Firma digital del manifiesto de actualizaciones (ECDSA P-256 + SHA-256).

Por qué ECDSA P-256 y no Ed25519: la app (Android 8+, minSdk 26) lo verifica con java.security.Signature
"SHA256withECDSA", que existe en TODAS las versiones de Android; Ed25519 solo desde Android 13.

  * La clave PRIVADA se queda en el ordenador de Mario (carpeta `claves/`, que NO va a git, ni a GitHub, ni a los ZIP).
  * La clave PÚBLICA va dentro de la app (X.509 en Base64) y se puede publicar sin problema.
  * `manifest.sig` = firma DER en Base64 de los bytes EXACTOS de `manifest.json`.

Uso:
  python tools/firma_manifiesto.py --generar-claves [--carpeta claves]   # crea el par de claves (no sobrescribe)
  python tools/firma_manifiesto.py --publica [--carpeta claves]          # imprime la pública (Base64) para la app
  python tools/firma_manifiesto.py --firmar manifest.json [--carpeta claves]
  python tools/firma_manifiesto.py --verificar manifest.json manifest.sig PUBLICA_BASE64
"""
import base64
import os
import sys

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

HERE = os.path.dirname(os.path.abspath(__file__))
CARPETA_CLAVES = os.environ.get("MARIOBUS_CLAVES") or os.path.join(HERE, "..", "claves")
NOMBRE_PRIVADA = "horarios-firma-privada.pem"
NOMBRE_PUBLICA = "horarios-firma-publica.b64"


class ErrorFirma(Exception):
    pass


def publica_base64(clave_publica):
    der = clave_publica.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode("ascii")


def generar_claves(carpeta=CARPETA_CLAVES):
    """Crea el par de claves. NUNCA sobrescribe una clave existente (perderla obligaría a sacar una app nueva)."""
    os.makedirs(carpeta, exist_ok=True)
    ruta_priv = os.path.join(carpeta, NOMBRE_PRIVADA)
    ruta_pub = os.path.join(carpeta, NOMBRE_PUBLICA)
    if os.path.exists(ruta_priv):
        raise ErrorFirma(f"Ya existe {ruta_priv}: no se sobrescribe")
    clave = ec.generate_private_key(ec.SECP256R1())
    with open(ruta_priv, "wb") as f:
        f.write(clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    pub = publica_base64(clave.public_key())
    with open(ruta_pub, "w", encoding="ascii") as f:
        f.write(pub + "\n")
    return ruta_priv, pub


def cargar_privada(carpeta=CARPETA_CLAVES):
    ruta = os.path.join(carpeta, NOMBRE_PRIVADA)
    if not os.path.exists(ruta):
        raise ErrorFirma(f"Falta la clave privada {ruta}. Se crea con --generar-claves")
    with open(ruta, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def publica_de(carpeta=CARPETA_CLAVES):
    return publica_base64(cargar_privada(carpeta).public_key())


def firmar_bytes(datos, clave_privada):
    return base64.b64encode(clave_privada.sign(datos, ec.ECDSA(hashes.SHA256()))).decode("ascii")


def firmar_archivo(ruta_manifest, carpeta=CARPETA_CLAVES, ruta_sig=None):
    with open(ruta_manifest, "rb") as f:
        datos = f.read()
    sig = firmar_bytes(datos, cargar_privada(carpeta))
    ruta_sig = ruta_sig or os.path.splitext(ruta_manifest)[0] + ".sig"
    tmp = ruta_sig + ".tmp"
    with open(tmp, "w", encoding="ascii") as f:
        f.write(sig + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ruta_sig)
    return ruta_sig


def verificar_bytes(datos, firma_base64, publica_base64_texto):
    """True si la firma es válida. Cualquier fallo (formato, clave, firma) → False; nunca lanza."""
    try:
        pub = serialization.load_der_public_key(base64.b64decode(publica_base64_texto.strip()))
        pub.verify(base64.b64decode(firma_base64.strip()), datos, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False


def verificar_archivo(ruta_manifest, ruta_sig, publica):
    with open(ruta_manifest, "rb") as f:
        datos = f.read()
    with open(ruta_sig, "r", encoding="ascii") as f:
        sig = f.read()
    return verificar_bytes(datos, sig, publica)


def main(argv):
    carpeta = argv[argv.index("--carpeta") + 1] if "--carpeta" in argv else CARPETA_CLAVES
    if "--generar-claves" in argv:
        ruta, pub = generar_claves(carpeta)
        print("Clave privada creada en:", os.path.abspath(ruta))
        print("GUÁRDALA EN UNA COPIA DE SEGURIDAD y no la subas nunca a ningún sitio.")
        print("Clave pública (para la app):", pub)
        return 0
    if "--publica" in argv:
        print(publica_de(carpeta))
        return 0
    if "--firmar" in argv:
        print("Firma escrita en:", firmar_archivo(argv[argv.index("--firmar") + 1], carpeta))
        return 0
    if "--verificar" in argv:
        i = argv.index("--verificar")
        ok = verificar_archivo(argv[i + 1], argv[i + 2], argv[i + 3])
        print("FIRMA VÁLIDA" if ok else "FIRMA NO VÁLIDA")
        return 0 if ok else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
