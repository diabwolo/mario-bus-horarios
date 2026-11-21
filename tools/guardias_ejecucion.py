"""Guardias de ejecución del servidor de horarios (solo biblioteca estándar: se ejecutan ANTES de instalar nada ni tocar la clave).

  python tools/guardias_ejecucion.py vigencia  --repo=. --evento=push --ref=refs/heads/main --intento=1
  python tools/guardias_ejecucion.py registro  --run=12 --evento=schedule --origen=respaldo-externo ... >> estado/ejecuciones.log
  python tools/guardias_ejecucion.py desarmar  --ruta=disparar/accion.txt --accion=restaurar --evento=push --paso=success

P1 · VIGENCIA: una ejecución solo puede publicar, restaurar o modificar el estado si sigue siendo VÁLIDA respecto a `main`.
    · una REEJECUCIÓN (intento > 1) usa el commit, el código y el estado de cuando se creó: se ABORTA siempre;
    · si `main` avanzó solo por commits que no cambian lo que esta ejecución va a hacer (los de estado del propio servidor, que son los
      que provoca otra ejecución anterior en cola), se PONE AL DÍA con un avance rápido y sigue: no se aborta por un commit concurrente;
    · se ABORTA si cambió el workflow, si (en un push) cambió disparar/, si el commit ya no es antepasado de main o si no se puede comprobar.
P2 · REGISTRO: todo campo que va a una línea de estado/ejecuciones.log se limpia (sin saltos de línea, controles ni caracteres de formato,
    sin «|», recortado) para que quien llame a la API no pueda falsificar entradas.
P3 · DESARMADO: tras una acción extraordinaria (forzar, prueba, restaurar) disparar/accion.txt vuelve a `ciclo`."""
import argparse
import datetime
import os
import subprocess
import sys
import tempfile
import unicodedata

RAMA = "main"


# ---------------------------------------------------------------- P1 · vigencia respecto a main
def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def evaluar_vigencia(repo, evento, ref, intento, rama=RAMA, aplicar=True):
    """('CONTINUAR' | 'CONTINUAR_ACTUALIZADO' | 'ABORTAR', motivo). Con aplicar=True el avance rápido a main se hace aquí."""
    try:
        n = int(intento or 1)
    except ValueError:
        return "ABORTAR", f"intento de ejecución ilegible ({intento!r})"
    if n > 1:
        return "ABORTAR", (f"es una REEJECUCIÓN (intento {n}): usaría el commit, el código y el estado de cuando se creó la ejecución, "
                           "no los actuales. El siguiente disparo normal hará el trabajo")
    if ref != f"refs/heads/{rama}":
        return "ABORTAR", f"la referencia es «{ref}», no refs/heads/{rama}"
    if _git(repo, "rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        _git(repo, "fetch", "-q", "--unshallow", "origin")
    f = _git(repo, "fetch", "-q", "origin", f"+refs/heads/{rama}:refs/remotes/origin/{rama}")
    if f.returncode != 0:
        return "ABORTAR", "no se pudo consultar la rama principal de GitHub (se reintentará en el siguiente disparo)"
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    tip = _git(repo, "rev-parse", f"origin/{rama}").stdout.strip()
    if not head or not tip:
        return "ABORTAR", "no se pudo leer el commit actual o el de main"
    if head == tip:
        return "CONTINUAR", "el commit de esta ejecución es el último de main"
    if _git(repo, "merge-base", "--is-ancestor", head, tip).returncode != 0:
        return "ABORTAR", f"el commit {head[:7]} ya no es antepasado de main ({tip[:7]}): estado obsoleto o historia reescrita"
    cambiados = [x for x in _git(repo, "diff", "--name-only", head, tip).stdout.splitlines() if x]
    if any(x.startswith(".github/") for x in cambiados):
        return "ABORTAR", "el workflow cambió mientras esperaba: se ejecutará con la versión nueva en el siguiente disparo"
    if evento == "push" and any(x.startswith("disparar/") for x in cambiados):
        return "ABORTAR", "una instrucción de disparar/ posterior sustituye a la de esta ejecución (la atenderá la otra ejecución)"
    if aplicar:
        m = _git(repo, "merge", "--ff-only", "-q", f"origin/{rama}")
        if m.returncode != 0:
            return "ABORTAR", "no se pudo avanzar a la última versión de main"
    return "CONTINUAR_ACTUALIZADO", (f"main avanzó {len(cambiados)} archivo(s) por commits compatibles (estado/código): "
                                     f"se pone al día {head[:7]} → {tip[:7]}")


# ---------------------------------------------------------------- P2 · limpieza del registro
def limpiar_texto(texto, maximo=40, defecto="-"):
    """Una sola línea de texto imprimible: quita saltos de línea, controles (Cc), caracteres de formato como los invisibles o los de
    dirección (Cf), separadores de línea/párrafo, sustitutos, privados y no asignados; cambia «|» (separador de campos) por «/»,
    junta los espacios y recorta. Conserva lo legítimo («respaldo-externo», «manual»…)."""
    if texto is None:
        return defecto
    salida = []
    for ch in str(texto):
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
            continue
        salida.append("/" if ch == "|" else ch)
    limpio = " ".join("".join(salida).split())
    return limpio[:maximo].rstrip() or defecto


def linea_registro(run, evento, origen, cron, accion, paso, resultado, ahora=None):
    """Una línea del registro, con el mismo formato de siempre y todos los campos limpios."""
    ahora = ahora or datetime.datetime.now(datetime.timezone.utc)
    return (f"{ahora.strftime('%Y-%m-%dT%H:%M:%SZ')} | ejecución #{limpiar_texto(run, 10)} | evento={limpiar_texto(evento, 30)} | "
            f"origen={limpiar_texto(origen, 40)} | cron='{limpiar_texto(cron, 30, '')}' | acción={limpiar_texto(accion, 30)} | "
            f"paso={limpiar_texto(paso, 20)} | {limpiar_texto(resultado, 220, 'sin resultado')}")


# ---------------------------------------------------------------- P3 · desarmado de la instrucción extraordinaria
def desarmar_accion(ruta, accion, evento, paso):
    """Deja `ciclo` en disparar/accion.txt tras una acción extraordinaria lanzada por un push. Devuelve (cambiado, nota para el registro).
    Si salió bien o MAL se desarma igual: una instrucción fallida que siguiera armada se repetiría sola, quizá semanas después, con
    otro estado; queda anotado para repetirla a mano si procede."""
    if evento != "push" or accion in (None, "", "ciclo"):
        return False, ""
    fd, tmp = tempfile.mkstemp(prefix=".accion_", dir=os.path.dirname(os.path.abspath(ruta)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("ciclo\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ruta)  # atómico: nunca queda un archivo a medias
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if paso == "success":
        return True, f"instrucción extraordinaria «{limpiar_texto(accion, 30)}» terminada CORRECTAMENTE: disparar/accion.txt restablecido a ciclo"
    return True, (f"instrucción extraordinaria «{limpiar_texto(accion, 30)}» FALLIDA (paso={limpiar_texto(paso, 20)}): nada se publicó; "
                  "disparar/accion.txt restablecido a ciclo por seguridad; repetirla a mano si procede")


# ---------------------------------------------------------------- línea de órdenes
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="orden", required=True)
    v = sub.add_parser("vigencia")
    v.add_argument("--repo", default=".")
    v.add_argument("--evento", default="")
    v.add_argument("--ref", default="")
    v.add_argument("--intento", default="1")
    r = sub.add_parser("registro")
    for nombre in ("run", "evento", "origen", "cron", "accion", "paso", "resultado"):
        r.add_argument(f"--{nombre}", default="")
    d = sub.add_parser("desarmar")
    d.add_argument("--ruta", default="disparar/accion.txt")
    d.add_argument("--accion", default="")
    d.add_argument("--evento", default="")
    d.add_argument("--paso", default="")
    a = p.parse_args(argv)
    if a.orden == "vigencia":
        veredicto, motivo = evaluar_vigencia(a.repo, a.evento, a.ref, a.intento)
        print(f"VIGENCIA: {veredicto} · {motivo}")
        if veredicto == "ABORTAR":
            print(f"::error::Ejecución NO vigente, se aborta sin tocar nada: {motivo}")
            return 1
        return 0
    if a.orden == "registro":
        print(linea_registro(a.run, a.evento, a.origen, a.cron, a.accion, a.paso, a.resultado))
        return 0
    if a.orden == "desarmar":
        _, nota = desarmar_accion(a.ruta, a.accion, a.evento, a.paso)
        print(nota)
        return 0
    return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
